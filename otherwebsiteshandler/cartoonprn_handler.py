"""
cartoonprn_handler.py
─────────────────────
هندلر برای CartoonPRN (cartoonprn.com) — ه انیمه.

روش کار:
  سایت وردپرسیه و از پلیر «BB Pimp Player» (خانواده‌ی تم‌های WP-Script)
  استفاده می‌کنه. ساختار صفحه‌ی ویدیو:

    <video class="bb-pimp-player__video"
           data-bb-main-src="https://cpNN.cartoonprn.com/videos/....mp4"
           data-bb-preroll-src=".../vip-preroll.mp4" ...>
      <source src="https://cpNN.cartoonprn.com/videos/....mp4" type="video/mp4">
    </video>

  یعنی ویدیوی اصلی یه MP4 مستقیم روی CDN خودشه (ساب‌دامنه‌ی cpNN).

مراحل:
  ۱) استخراج مستقیم از HTML با curl_cffi (browser impersonation برای Cloudflare)
  ۲) fallback به yt-dlp اگه استخراج مستقیم شکست خورد

دانلود با download_direct_multi (۱۶ کانکشن موازی = حداکثر سرعت) انجام میشه
و اگه CDN اجازه‌ی Range نداد، خودکار به دانلود تک‌کانکشنه برمی‌گرده.
"""

import asyncio
import json
import logging
import os
import re
import shutil
from typing import List, Optional, Tuple
from urllib.parse import urlparse

from ._common import (
    ProgressCallback,
    check_impersonation_support,
    cleanup_file,
    default_user_agent,
    download_direct as _download_direct_impl,
    download_direct_multi as _download_direct_multi_impl,
    download_with_ytdlp as _download_ytdlp_impl,
    extract_title_from_html,
    fetch_html,
    fetch_html_via_wayback,
    unwrap_wayback_urls,
    quality_sort_key,
)

logger = logging.getLogger("CartoonPrnHandler")

_USER_AGENT = default_user_agent()

_SITE_URL = "https://cartoonprn.com"
_SITE_REFERER = f"{_SITE_URL}/"

# دامنه‌های مجاز — هم سایت، هم CDN ساب‌دامنه‌دار (cpNN.cartoonprn.com)
_ALLOWED_HOSTS = frozenset({
    "cartoonprn.com",
    "www.cartoonprn.com",
    "m.cartoonprn.com",
})
_ALLOWED_HOST_SUFFIXES = (
    ".cartoonprn.com",
)
# الگوی ساب‌دامنه‌ی CDN (cp1، cp13، ... )
_CDN_HOST_RE = re.compile(r"^cp\d+\.cartoonprn\.com$", re.I)

# مسیرهایی که ویدیوی اصلی نیستن (پیش‌رونده تبلیغاتی / تیزر / اسکرین‌شات)
_EXCLUDE_PATTERNS = (
    "vip-preroll",
    "preroll",
    "preview",
    "screenshots",
    "trailer",
    "tsyndicate",
    "s.magsrv",
    "s.magsrv.com",
    "/ads/",
    "creative.",
)

# لینک مستقیم ویدیو روی CDN
_MAIN_URL_RE = re.compile(
    r"https?://cp\d+\.cartoonprn\.com/videos/[^\"'\s\\<>]+?\.mp4(?:\?[^\"'\s\\<>]*)?",
    re.I,
)

cartoonprn_sessions: dict = {}


# ─── URL detection ─────────────────────────────────────────


def is_cartoonprn_url(url: str) -> bool:
    """بررسی اینکه URL مربوط به cartoonprn.com هست (سایت یا ساب‌دامنه‌ها)."""
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    if host in _ALLOWED_HOSTS:
        return True
    if any(host.endswith(s) for s in _ALLOWED_HOST_SUFFIXES):
        return True
    return False


def _is_allowed_media_host(url: str) -> bool:
    """هاست فایل مدیا باید از دامنه‌ی خود سایت باشه (سایت یا CDN)."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    if host in _ALLOWED_HOSTS or _CDN_HOST_RE.match(host):
        return True
    return any(host.endswith(s) for s in _ALLOWED_HOST_SUFFIXES)


def _is_excluded(url: str) -> bool:
    u = (url or "").lower()
    return any(p in u for p in _EXCLUDE_PATTERNS)


# ─── Extraction: HTML direct ───────────────────────────────


def _extract_from_html(html: str, page_url: str) -> Tuple[List[dict], str]:
    """استخراج لینک MP4 مستقیم و عنوان از HTML صفحه‌ی BB Pimp Player."""
    # 🆕 z33: اگه HTML از Wayback آمده، پیشوند web.archive.org رو بردار تا
    # الگوهای عادی (و هاست‌های مجاز) روی URLهای اصلی کار کنن
    html = unwrap_wayback_urls(html)
    title = extract_title_from_html(html, "CartoonPRN")

    qualities: List[dict] = []
    seen: set = set()

    def _add(url: str, note: str = "") -> None:
        url = (url or "").strip().replace("\\/", "/")
        if not url or url in seen:
            return
        if not url.lower().startswith("http"):
            return
        if not _is_allowed_media_host(url):
            return
        if _is_excluded(url):
            return
        seen.add(url)
        m_h = re.search(r"[_\-.](\d{3,4})p[_\-.]", url)
        height = int(m_h.group(1)) if m_h else 0
        label = f"📺 {height}p (MP4)" if height else "📡 MP4 اصلی (Auto)"
        q = {"label": label, "url": url, "method": "direct", "height": height}
        if note:
            q["note"] = note
        qualities.append(q)

    # ۱) اتریبیوت اختصاصی پلیر — data-bb-main-src (منبع اصلی پخش)
    for m in re.finditer(
        r'data-bb-main-src\s*=\s*["\']([^"\']+)["\']', html, re.I
    ):
        _add(m.group(1), "main")

    # ۲) تگ‌های <source> داخل <video>
    for vm in re.finditer(r"<video[^>]*>(.*?)</video>", html, re.I | re.S):
        for sm in re.finditer(
            r'<source[^>]+src\s*=\s*["\']([^"\']+)["\']', vm.group(1), re.I
        ):
            _add(sm.group(1), "source")

    # ۳) اتریبیوت src خود <video> (بعضی تم‌ها مستقیم روی ویدیو می‌ذارن)
    for m in re.finditer(r"<video[^>]+src\s*=\s*[\"']([^\"']+\.mp4[^\"']*)[\"']", html, re.I):
        _add(m.group(1), "video-src")

    # ۴) اسکن عمومی لینک‌های ویدیوی CDN (اگه هیچ‌کدوم از موارد بالا نبود)
    if not qualities:
        for m in _MAIN_URL_RE.finditer(html):
            _add(m.group(0), "scan")

    # حذف تکراری بر اساس URL و مرتب‌سازی کیفیت (بالاترین اول)
    unique = {}
    for q in qualities:
        key = q["url"]
        unique.setdefault(key, q)
    result = sorted(unique.values(), key=quality_sort_key, reverse=True)

    # اگه چند لینک بود، اونی که note=main داره (data-bb-main-src) اولویت داره
    if len(result) > 1:
        result.sort(key=lambda q: 0 if q.get("note") == "main" else 1)

    return result, title


# ─── Extraction: yt-dlp fallback (ز32 — با سقف زمانی + اعتبارسنجی) ───────────


_YTDLP_EXTRACT_TIMEOUT = int(os.environ.get("YTDLP_EXTRACT_TIMEOUT", "70"))


def _is_real_media_url(u: str) -> bool:
    """فقط URLهای مدیای واقعی (mp4/m3u8) — نه URL خود صفحه (تله‌ی «Auto via yt-dlp»)."""
    u = (u or "").split("?")[0].lower()
    return u.endswith((".mp4", ".m3u8", ".mkv", ".webm", ".ts"))


async def _extract_with_ytdlp(url: str) -> Tuple[List[dict], str]:
    """Fallback: استخراج با yt-dlp — با سقف زمانی سخت و فیلتر URL واقعی.

    🆕 ز32: قبلاً extract_qualities_with_ytdlp بدون timeout اجرا می‌شد (چند دقیقه
    معطلی روی Cloudflare) و اگه فرمتی پیدا نمی‌کرد کیفیت ساختگی
    «Auto (via yt-dlp)» با URL خودِ صفحه برمی‌گردوند که مسیر دانلود کند
    yt-dlp رو فعال می‌کرد. الان: سقف ۷۰ ثانیه + فقط مدیای واقعی."""
    if not shutil.which("yt-dlp"):
        return [], "yt-dlp not installed"

    cmd = [
        "yt-dlp", "--no-warnings", "--no-check-certificates",
        "--skip-download", "-J", url,
    ]
    if check_impersonation_support():
        cmd.extend(["--impersonate", "chrome"])

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=_YTDLP_EXTRACT_TIMEOUT
            )
        except asyncio.TimeoutError:
            try:
                process.kill()
            except Exception:
                pass
            await process.wait()
            logger.warning("[CartoonPrn] yt-dlp extract timeout (%ds)", _YTDLP_EXTRACT_TIMEOUT)
            return [], f"استخراج خیلی طول کشید (سقف {_YTDLP_EXTRACT_TIMEOUT} ثانیه)"

        if process.returncode != 0:
            err = stderr.decode(errors="replace").strip()[:160]
            return [], err or "yt-dlp failed"

        try:
            data = json.loads(stdout.decode(errors="replace"))
        except Exception:
            return [], "yt-dlp output parse failed"

        title = data.get("title", "") or "CartoonPRN"
        qualities: List[dict] = []
        seen: set = set()
        for fmt in data.get("formats", []):
            if (fmt.get("vcodec") or "") == "none":
                continue
            furl = fmt.get("url", "")
            if not furl or furl in seen or not _is_real_media_url(furl):
                continue
            seen.add(furl)
            height = fmt.get("height") or 0
            ext = fmt.get("ext", "mp4")
            method = "m3u8" if (ext == "m3u8" or ".m3u8" in furl) else "direct"
            label = f"📺 {height}p ({ext.upper()})" if height else f"📡 {ext.upper()}"
            qualities.append({"label": label, "url": furl, "method": method, "height": height or 0})

        if not qualities:
            # هیچ مدیای واقعی — یعنی Cloudflare جلوی استخراج رو گرفته
            return [], "کیفیت واقعی پیدا نشد (احتمالاً Cloudflare صفحه رو بلاک کرده)"

        qualities.sort(key=quality_sort_key, reverse=True)
        return qualities, title
    except Exception as e:
        return [], str(e)[:160]


# ─── Main extraction ───────────────────────────────────────


async def extract_cartoonprn_qualities(
    url: str,
    progress_cb: Optional[ProgressCallback] = None,
) -> Tuple[List[dict], str]:
    """استخراج کیفیت‌های ویدیو از cartoonprn.com.

    ز33 — زنجیره:
      ۱) HTML مستقیم (۲ impersonation) — سریع‌ترین مسیر
      ۲) 🆕 Wayback Machine (اسنپ‌شات + Save-Page-Now) — وقتی CF آی‌پی سرور رو بلاک کرده
      ۳) yt-dlp با سقف زمانی (آخرین راه)

    progress_cb: اختیاری — برای پیام‌های مرحله‌ای («از آرشیو امتحان می‌کنم…»)."""
    if not is_cartoonprn_url(url):
        return [], "Invalid URL"

    async def _stage(text: str):
        if progress_cb:
            try:
                await progress_cb(text)
            except Exception:
                pass

    if not check_impersonation_support():
        logger.warning("[CartoonPrn] curl_cffi unavailable → yt-dlp")
        return await _extract_with_ytdlp(url)

    logger.info("[CartoonPrn] Fetching: %s", url[:100])

    html = None
    status = 0
    for imp in ("chrome", "chrome131"):
        html, status = await fetch_html(url=url, referer=_SITE_REFERER, impersonate=imp)
        if html and status == 200:
            break
        logger.info("[CartoonPrn] fetch failed (imp=%s status=%s) — trying next", imp, status)

    if html and status == 200:
        qualities, title = _extract_from_html(html, url)
        if qualities:
            logger.info(
                "[CartoonPrn] Extracted %d quality(ies) from HTML for: %s",
                len(qualities), title[:60],
            )
            return qualities, title
        logger.info("[CartoonPrn] HTML got but no direct mp4 → yt-dlp")

    # 🆕 z33: مستقیم نشد (احتمالاً CF بلاک کرده) → Wayback Machine
    await _stage(
        "☁️ <b>سایت مستقیم جواب نداد (Cloudflare)…</b>\n"
        "📚 از آرشیو Wayback Machine امتحان می‌کنم — ممکنه ۱ تا ۲ دقیقه طول بکشه، صبر کن…"
    )
    wb_html, wb_status = await fetch_html_via_wayback(
        url,
        validator=lambda h: bool(_extract_from_html(h, url)[0]),
    )
    if wb_html:
        qualities, title = _extract_from_html(wb_html, url)
        if qualities:
            logger.info(
                "[CartoonPrn] Extracted %d quality(ies) from Wayback for: %s",
                len(qualities), title[:60],
            )
            return qualities, title

    # آخرین راه: yt-dlp (سقف‌دار — z32)
    await _stage("🛠 هنوز پیدا نشد — استخراج با yt-dlp (سقف ۷۰ ثانیه)…")
    return await _extract_with_ytdlp(url)


# ─── Download ──────────────────────────────────────────────


async def download_cartoonprn_video(
    video_url: str,
    filepath: str,
    progress_cb: Optional[ProgressCallback] = None,
) -> Tuple[bool, str, int]:
    """دانلود MP4 مستقیم با حداکثر سرعت (۱۶ کانکشن موازی).

    زنجیره:
      ۱) download_direct_multi (۱۶ worker، chunk ۵MB — سریع‌ترین)
      ۲) download_direct (تک‌کانکشنه استریمی)
      ۳) yt-dlp (با aria2c اگه موجوده)
    """
    if not _is_allowed_media_host(video_url):
        return False, "URL host not allowed", 0

    progress_cb = progress_cb or (lambda text: _noop())

    # ۱) multi-segment 16x
    success, error, size = await _download_direct_multi_impl(
        video_url, filepath, progress_cb, referer=_SITE_REFERER,
    )
    if success:
        return True, "", size
    cleanup_file(filepath)
    logger.info("[CartoonPrn] multi-segment failed (%s) → direct", error[:80])

    # ۲) تک‌کانکشنه استریمی
    success, error, size = await _download_direct_impl(
        video_url, filepath, progress_cb, referer=_SITE_REFERER,
    )
    if success:
        return True, "", size
    cleanup_file(filepath)
    logger.info("[CartoonPrn] direct failed (%s) → yt-dlp", error[:80])

    # ۳) yt-dlp
    success, error, size = await _download_ytdlp_impl(
        video_url, filepath, progress_cb, referer=_SITE_REFERER,
    )
    if not success:
        cleanup_file(filepath)
    return success, error, size


async def _noop() -> None:
    return None
