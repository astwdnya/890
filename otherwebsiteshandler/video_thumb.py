"""
video_thumb.py
──────────────
تولید و مدیریت تامبنیل ویدیو برای همه‌ی فلوها (مشترک بین هندلرها).

هر فلو سه تا کار باهاش می‌کنه:
  1. `pick_best_thumb_url(data)`      → از خروجی yt-dlp --dump-json
  2. `fetch_page_thumb(url)`          → از HTML خود صفحه (og:image و مشابهش)
  3. `fetch_video_thumbs(url, out)`   → دانلود + تبدیل:
        - photo (JPEG تا 1280px) برای نمایش تو چت
        - tg (JPEG تا 320px) برای تامبنیل فایل آپلودی تلگرام
و آخر کار `cleanup_thumb_files(assets)`.

هیچ‌کدوم از توابع exception نمی‌دن؛ خرابی همیشه یعنی None —
فلو اصلی هرگز به‌خاطر تامبنیل نمی‌شکنه.
"""

import asyncio
import hashlib
import io
import logging
import os
import re
from typing import Optional, Tuple

import aiohttp
from aiohttp import ClientTimeout

logger = logging.getLogger("VideoThumb")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_THUMB_PATTERNS = (
    # og:image و نسخه‌ی secure (ترتیب attribute ها هر دو جهت)
    r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image(?::secure_url)?["\']',
    r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image(?::src)?["\']',
    r'itemprop=["\']thumbnailUrl["\'][^>]*content=["\']([^"\']+)["\']',
    r'"thumbnailUrl"\s*:\s*"(.*?)"',
    r'"thumb_url"\s*:\s*"(.*?)"',
)


def _normalize_url(u: str) -> Optional[str]:
    if not u:
        return None
    u = u.replace("\\/", "/").replace("&amp;", "&").strip()
    if u.startswith("//"):
        u = "https:" + u
    return u if u.startswith("http") else None


def pick_best_thumb_url(data: dict) -> Optional[str]:
    """بزرگ‌ترین تامبنیل موجود رو از خروجی yt-dlp انتخاب می‌کنه."""
    candidates = []
    for t in data.get("thumbnails") or []:
        u = _normalize_url(t.get("url"))
        if u:
            candidates.append(((t.get("width") or 0) * (t.get("height") or 0), u))
    main = _normalize_url(data.get("thumbnail"))
    if main:
        candidates.append((0, main))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0], reverse=True)
    return candidates[0][1]


def extract_thumb_from_html(html: str) -> Optional[str]:
    """استخراج تامبنیل از HTML صفحه‌ی ویدیو (og:image و مشابه‌ها)."""
    if not html:
        return None
    for pat in _THUMB_PATTERNS:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            u = _normalize_url(m.group(1))
            if u:
                return u
    return None


async def fetch_page_thumb(url: str, timeout_s: int = 15) -> Optional[str]:
    """
    دریافت صفحه‌ی ویدیو و خوندن تامبنیل از HTML.
    اول aiohttp ساده، اگه جواب نداد curl_cffi با impersonate.
    """
    html = None
    try:
        timeout = ClientTimeout(total=timeout_s)
        headers = {"User-Agent": _USER_AGENT, "Referer": url}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    html = await resp.text()
    except Exception:
        html = None

    if not html:
        try:
            from curl_cffi.requests import AsyncSession

            async with AsyncSession() as session:
                resp = await session.get(url, impersonate="chrome", timeout=timeout_s)
                if resp.status_code == 200:
                    html = resp.text
        except Exception:
            html = None

    return extract_thumb_from_html(html)


async def fetch_video_thumbs(
    thumb_url: str, out_dir: str
) -> Optional[Tuple[str, Optional[str]]]:
    """
    دانلود و آماده‌سازی تامبنیل ویدیو.

    Returns:
        (photo_path, tg_thumb_path)
          - photo_path: تصویر باکیفیت برای نمایش تو چت (JPEG، لبه بزرگ ≤ 1280)
          - tg_thumb_path: نسخه کوچیک JPEG برای تامبنیل فایل آپلودی (≤ 320)
    """
    try:
        from PIL import Image
    except Exception:
        Image = None

    try:
        os.makedirs(out_dir, exist_ok=True)
        digest = hashlib.md5(thumb_url.encode()).hexdigest()[:12]
        base = os.path.join(out_dir, f"thumb_{digest}")

        timeout = ClientTimeout(total=20)
        headers = {"User-Agent": _USER_AGENT, "Referer": "https://www.google.com/"}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(thumb_url) as resp:
                if resp.status != 200:
                    logger.warning("Thumb download failed: HTTP %s", resp.status)
                    return None
                raw = await resp.read()

        if not raw or len(raw) < 1024:
            return None

        photo_path = base + "_hd.jpg"
        tg_path = base + "_tg.jpg"

        if Image is not None:
            try:
                im = Image.open(io.BytesIO(raw)).convert("RGB")
                im.save(photo_path, "JPEG", quality=88)
                tg = im.copy()
                tg.thumbnail((320, 320), Image.LANCZOS)
                tg.save(tg_path, "JPEG", quality=85)
                return photo_path, tg_path
            except Exception as e:
                logger.warning("Thumb convert error: %s", e)
                return None

        # بدون Pillow: فقط اگه خود فایل JPEG مناسبه
        if raw[:3] == b"\xff\xd8\xff":
            with open(photo_path, "wb") as f:
                f.write(raw)
            if len(raw) <= 200 * 1024:
                with open(tg_path, "wb") as f:
                    f.write(raw)
                return photo_path, tg_path
            return photo_path, None
        return None
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("Thumb fetch error: %s", e)
        return None


def cleanup_thumb_files(assets) -> None:
    """حذف فایل‌های موقت تامبنیل."""
    for p in assets or ():
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
