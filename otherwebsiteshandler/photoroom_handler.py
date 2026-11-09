"""
photoroom_handler.py
────────────────────
هندلر حذف پس‌زمینه عکس — برعکس نام فایل، این حالا از API پابلیک
سایت slazzer.com استفاده می‌کنه (نه از API خود PhotoRoom، که اون یکی
پشت Cloudflare Turnstile قفل شده و بدون حل کپچای پولی کار نمی‌کنه).

جریان کاری (کاملاً HTTP، بدون Playwright، بدون مرورگر):
    1. GET https://www.slazzer.com/         → کوکی session_slazzer + CSRF token
    2. POST /generate_trust_token            → JWT با اعتبار ۵ دقیقه
    3. POST /upload_image (multipart)        → JSON شامل آدرس نتیجه PNG
    4. GET  /downloads/.../image_prev_ui.png  → PNG با شفافیت (۵۰۰×۵۰۰ RGBA)

خروجی:
    remove_background(image_path, output_path=None) → (bool, path_or_err)
        فایل PNG با شفافیت به همان ابعاد از slazzer (۵۰۰×۵۰۰)
    remove_background_to_sticker(image_path, output_path=None) → (bool, path_or_err)
        فایل **WebP** ۵۱۲×۵۱۲ با شفافیت — فرمت رسمی استیکر استاتیک
        تلگرام (PNG به‌عنوان استیکر پذیرفته نمی‌شه و به‌عنوان عکس معمولی
        نمایش داده می‌شه)

نکته:
    - اندازه‌ی رایگان slazzer ۵۰۰×۵۰۰ پیکسله. برای استیکر تلگرام به ۵۱۲×۵۱۲
      ریسایز می‌کنیم (Pillow از قبل توی requirements هست).
    - کوکی و CSRF رو یه‌بار می‌گیریم و توی ماژول کش می‌کنیم؛ اگه توکن
      منقضی بشه یا کوکی منقضی بشه، خودکار renewal می‌شه.
    - محدودیت slazzer: ۱۰ عکس در دقیقه. اگه بیشتر بفرستیم، ۴۲۹ می‌گیریم.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import time
import uuid
from typing import Optional, Tuple

logger = logging.getLogger("Photoroom")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
HOME = "https://www.slazzer.com/"
ORIGIN = "https://www.slazzer.com"
UA_HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "*/*",
    "Origin": ORIGIN,
    "Referer": HOME,
}

# ─── module-level cached session ──────────────────────────────────────────
# We do the network IO in run_in_executor so this stays sync I/O under the
# asyncio event loop.
_cached_csrf: Optional[str] = None
_cached_session_cookie: Optional[str] = None
_cached_trust_token: Optional[str] = None
_cached_token_exp: float = 0.0  # unix seconds when trust_token expires


def _reset_cache() -> None:
    """Drop the entire cached session/csrf/token state."""
    global _cached_csrf, _cached_session_cookie, _cached_trust_token, _cached_token_exp
    _cached_csrf = None
    _cached_session_cookie = None
    _cached_trust_token = None
    _cached_token_exp = 0.0


def _reset_token_cache() -> None:
    """Drop only the trust_token (keep csrf + cookie)."""
    global _cached_trust_token, _cached_token_exp
    _cached_trust_token = None
    _cached_token_exp = 0.0


def _new_session():
    """Create a fresh requests.Session and fetch the homepage to seed cookies
    and the CSRF token. Returns (session, csrf_token).
    """
    import requests

    s = requests.Session()
    s.headers.update(UA_HEADERS)
    r = s.get(HOME, timeout=30)
    r.raise_for_status()
    m = re.search(
        r'<meta\s+name="csrf-token"\s+content="([^"]+)"', r.text
    )
    if not m:
        raise RuntimeError("slazzer: csrf-token meta not found on homepage")
    return s, m.group(1)


def _get_or_refresh_token() -> Tuple["requests.Session", str, str]:
    """Return (session, csrf_token, trust_token).

    Reuses the cached session/csrf as long as the trust_token is still valid
    (we refresh ~30s before it expires). On any error we throw the cache away
    and start fresh.
    """
    global _cached_csrf, _cached_trust_token, _cached_token_exp, _cached_session_cookie
    import requests

    now = time.time()
    # Still fresh? reuse.
    if (
        _cached_csrf
        and _cached_trust_token
        and now < _cached_token_exp - 30
    ):
        # Build a fresh session (don't keep a global Session object across
        # invocations — it can leak file handles and accumulate cookies)
        s = requests.Session()
        s.headers.update(UA_HEADERS)
        if _cached_session_cookie:
            s.headers["Cookie"] = _cached_session_cookie
        return s, _cached_csrf, _cached_trust_token

    # Need to refresh. If we have a valid csrf cookie already, try a quick
    # token refresh first (avoids the homepage fetch).
    if _cached_csrf and _cached_session_cookie:
        try:
            s = requests.Session()
            s.headers.update(UA_HEADERS)
            s.headers["Cookie"] = _cached_session_cookie
            r = s.post(
                f"{ORIGIN}/generate_trust_token",
                headers={"X-CSRFToken": _cached_csrf},
                timeout=30,
            )
            if r.status_code == 200:
                j = r.json()
                tok = j.get("trust_token")
                if tok:
                    _cached_trust_token = tok
                    _cached_token_exp = now + 280  # JWT is 300s, refresh at 280
                    return s, _cached_csrf, tok
            # 429 means rate-limited; fall through to full refresh
        except Exception:
            # fall through to full refresh
            pass

    # Full refresh — get a fresh csrf + cookie from homepage
    s, csrf = _new_session()
    # Capture cookies as a Cookie header so future calls reuse them
    cookie_str = "; ".join(f"{k}={v}" for k, v in s.cookies.items())
    _cached_session_cookie = cookie_str
    _cached_csrf = csrf

    r = s.post(
        f"{ORIGIN}/generate_trust_token",
        headers={"X-CSRFToken": csrf},
        timeout=30,
    )
    r.raise_for_status()
    j = r.json()
    tok = j.get("trust_token")
    if not tok:
        raise RuntimeError("slazzer: trust_token not in response")
    _cached_trust_token = tok
    _cached_token_exp = now + 280
    return s, csrf, tok


def _do_remove_background_sync(image_path: str) -> Tuple[bool, bytes, str]:
    """Run the full slazzer flow synchronously. Returns (success, png_bytes, msg)."""
    import requests

    if not os.path.exists(image_path):
        return False, b"", f"input file not found: {image_path}"

    try:
        s, csrf, token = _get_or_refresh_token()
    except Exception as e:
        # On any token-flow failure, force a full refresh next time
        _reset_cache()
        return False, b"", f"slazzer token error: {e}"

    ag_id = "tg_" + uuid.uuid4().hex[:14]
    file_size = os.path.getsize(image_path)
    logger.info(
        "[Photoroom] slazzer upload_image  img=%s  size=%d  ag_id=%s",
        image_path, file_size, ag_id,
    )

    # ── Step 3: upload the image ──
    with open(image_path, "rb") as fh:
        files = {"source_image_file": ("image.jpg", fh, "image/jpeg")}
        data = {"autogenerate_id": ag_id}
        headers = {
            "X-CSRFToken": csrf,
            "X-Trust-Token": token,
        }
        try:
            r = s.post(
                f"{ORIGIN}/upload_image",
                files=files,
                data=data,
                headers=headers,
                timeout=180,
            )
        except requests.RequestException as e:
            return False, b"", f"slazzer upload_image network error: {e}"

    if r.status_code == 429:
        return False, b"", "slazzer rate-limited (10 images/minute). please retry in 60 seconds."
    if r.status_code != 200:
        # Discard cached token; likely expired
        _reset_token_cache()
        return False, b"", f"slazzer upload_image HTTP {r.status_code}: {r.text[:200]}"

    try:
        j = r.json()
    except Exception:
        return False, b"", f"slazzer: non-JSON response: {r.text[:200]}"

    if not j.get("status"):
        return False, b"", f"slazzer: status=false — {j.get('message', j)}"

    preview_url = j.get("preview_size_output_image") or j.get("original_image")
    if not preview_url:
        return False, b"", f"slazzer: no preview URL in response: {j}"
    if not preview_url.startswith("http"):
        preview_url = ORIGIN + preview_url

    # ── Step 4: download the result PNG ──
    try:
        r2 = s.get(preview_url, timeout=120)
    except requests.RequestException as e:
        return False, b"", f"slazzer download PNG network error: {e}"
    if r2.status_code != 200:
        return False, b"", f"slazzer download PNG HTTP {r2.status_code}"

    if r2.content[:8] != b"\x89PNG\r\n\x1a\n":
        return False, b"", f"slazzer: response is not a PNG (magic={r2.content[:8]!r})"

    logger.info(
        "[Photoroom] slazzer OK  png_bytes=%d  preview=%s",
        len(r2.content), preview_url,
    )
    return True, r2.content, "ok"


def _resize_to_512_sticker_webp(png_bytes: bytes) -> bytes:
    """Convert the slazzer PNG to a Telegram static sticker:

    - WebP format (استاندارد استیکر استاتیک تلگرام؛ PNG به‌عنوان استیکر
      قبول نیست و کلاینت‌ها اون رو مثل عکس/سند معمولی نشون می‌دن)
    - longest side = 512 (سایز اجباری استیکر)
    - شفافیت (RGBA) حفظ می‌شه
    - اگر lossless از ۴۰۰KB بزرگ‌تر شد، به lossy با کیفیت ۸۵ برمی‌گرده
      (سقف تلگرام برای استیکر استاتیک ۵۱۲KB است)
    """
    from PIL import Image

    src = Image.open(io.BytesIO(png_bytes))
    if src.mode != "RGBA":
        src = src.convert("RGBA")
    # Telegram stickers: longest side must be 512, the other ≤ 512.
    w, h = src.size
    if max(w, h) != 512:
        if w >= h:
            new_w, new_h = 512, max(1, round(512 * h / w))
        else:
            new_h, new_w = 512, max(1, round(512 * w / h))
        src = src.resize((new_w, new_h), Image.LANCZOS)

    # WebP canvas must be exactly 512×512 for perfect sticker rendering;
    # paste the (possibly non-square) image centered on a transparent canvas.
    if src.size != (512, 512):
        canvas = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
        canvas.paste(src, ((512 - src.size[0]) // 2, (512 - src.size[1]) // 2))
        src = canvas

    buf = io.BytesIO()
    src.save(buf, format="WEBP", lossless=True)
    data = buf.getvalue()
    if len(data) > 400 * 1024:  # keep well under Telegram's 512 KB sticker cap
        buf = io.BytesIO()
        src.save(buf, format="WEBP", quality=85, method=6)
        data = buf.getvalue()
    return data


async def remove_background(image_path: str, output_path: Optional[str] = None) -> Tuple[bool, str]:
    """حذف پس‌زمینه و خروجی PNG با شفافیت (ابعاد از slazzer، ۵۰۰×۵۰۰)."""
    if output_path is None:
        base, _ = os.path.splitext(image_path)
        output_path = base + "_nobg.png"

    loop = asyncio.get_event_loop()
    try:
        ok, png_bytes, msg = await loop.run_in_executor(
            None, _do_remove_background_sync, image_path
        )
    except Exception as e:
        logger.exception("[Photoroom] remove_background failure")
        return False, f"unexpected error: {e}"

    if not ok:
        return False, msg

    with open(output_path, "wb") as f:
        f.write(png_bytes)
    return True, output_path


async def remove_background_to_sticker(image_path: str, output_path: Optional[str] = None) -> Tuple[bool, str]:
    """حذف پس‌زمینه و ساخت WebP ۵۱۲×۵۱۲ برای استیکر تلگرام."""
    if output_path is None:
        base, _ = os.path.splitext(image_path)
        output_path = base + "_sticker.webp"

    loop = asyncio.get_event_loop()
    try:
        ok, png_bytes, msg = await loop.run_in_executor(
            None, _do_remove_background_sync, image_path
        )
    except Exception as e:
        logger.exception("[Photoroom] remove_background_to_sticker failure")
        return False, f"unexpected error: {e}"

    if not ok:
        return False, msg

    try:
        sticker_bytes = await loop.run_in_executor(None, _resize_to_512_sticker_webp, png_bytes)
    except Exception as e:
        # Fallback: keep the original PNG
        logger.warning("[Photoroom] webp sticker conversion failed (%s); using raw slazzer PNG", e)
        sticker_bytes = png_bytes

    with open(output_path, "wb") as f:
        f.write(sticker_bytes)
    return True, output_path
