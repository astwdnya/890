"""
pornhex_handler.py
──────────────────
هندلر برای PornHex (pornhex.com).

ساختار صفحه از بیرون قابل پروب نبود (Cloudflare چالش همه‌جایی)، برای همین
استخراج چند-الگویی و مقاوم نوشته شده — پوشش‌دهنده‌ی خانواده‌های پلیر رایج
سایت‌های وردپرسی/ویدیویی:

  ۱) پلیر BB Pimp (WP-Script):  <video data-bb-main-src="....mp4">
  ۲) KVS flashvars:             video_url: 'https://....mp4'
  ۳) تگ <video><source src="...">
  ۴) اتریبیوت src خود <video>
  ۵) اسکن عمومی لینک‌های mp4/m3u8 در HTML (با فیلتر تبلیغات)

اولویت: فایل‌های روی دامنه‌ی خود سایت (یا ساب‌دامنه‌هاش) بالاتر از CDNهای
خارجی هستن و لینک‌های تبلیغاتی (tsyndicate/exoclick/preroll/preview) حذف
میشن. دانلود با download_direct_multi (۱۶ کانکشن = حداکثر سرعت) و fallback
های بعدی. fallback استخراج هم yt-dlp هست ولی با سقف زمانی سخت (۷۰ ثانیه) و
فیلتر «URL مدیای واقعی» — دیگه مسیر کند «Auto (via yt-dlp)» وجود نداره.
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
    download_m3u8 as _download_m3u8_impl,
    download_with_ytdlp as _download_ytdlp_impl,
    extract_title_from_html,
    fetch_html,
    quality_sort_key,
)

logger = logging.getLogger("PornHexHandler")

_USER_AGENT = default_user_agent()

_SITE_URL = "https://pornhex.com"
_SITE_REFERER = f"{_SITE_URL}/"

_ALLOWED_HOSTS = frozenset({
    "pornhex.com",
    "www.pornhex.com",
    "m.pornhex.com",
})
_ALLOWED_HOST_SUFFIXES = (".pornhex.com",)

# تبلیغ/بازدارنده‌ها — این URLها هرگز مدیای اصلی نیستن
_EXCLUDE_PATTERNS = (
    "tsyndicate",
    "magsrv",
    "exoclick",
    "exosrv",
    "realsrv",
    "doubleclick",
    "googlesyndication",
    "preroll",
    "/ads/",
    "adserver",
    "creative.",
    "preview",
    "trailer",
    "screenshots",
    ".jpg", ".jpeg", ".png", ".webp", ".gif",
)

_YTDLP_EXTRACT_TIMEOUT = int(os.environ.get("YTDLP_EXTRACT_TIMEOUT", "70"))

pornhex_sessions: dict = {}


# ─── URL detection ─────────────────────────────────────────


def is_pornhex_url(url: str) -> bool:
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
    return any(host.endswith(s) for s in _ALLOWED_HOST_SUFFIXES)


def _is_excluded(url: str) -> bool:
    u = (url or "").lower()
    return any(p in u for p in _EXCLUDE_PATTERNS)


def _is_site_media(url: str) -> bool:
    """URL روی دامنه‌ی خود سایت هست؟ (برای اولویت‌بندی)"""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host in _ALLOWED_HOSTS or any(host.endswith(s) for s in _ALLOWED_HOST_SUFFIXES)


def _is_real_media_url(u: str) -> bool:
    u = (u or "").split("?")[0].lower()
    return u.endswith((".mp4", ".m3u8", ".mkv", ".webm", ".ts"))


# ─── Extraction: HTML direct ───────────────────────────────


def _extract_from_html(html: str, page_url: str) -> Tuple[List[dict], str]:
    """استخراج چند-الگویی لینک مدیا + عنوان از HTML."""
    title = extract_title_from_html(html, "PornHex")

    qualities: List[dict] = []
    seen: set = set()

    def _add(url: str, note: str = "") -> None:
        url = (url or "").strip().replace("\\/", "/")
        if not url or url in seen or not url.lower().startswith("http"):
            return
        if not _is_real_media_url(url):
            return
        if _is_excluded(url):
            return
        seen.add(url)
        m_h = re.search(r"[_\-.](\d{3,4})p[_\-.]", url)
        height = int(m_h.group(1)) if m_h else 0
        ext = ".m3u8" if ".m3u8" in url.lower() else ".mp4"
        method = "m3u8" if ext == ".m3u8" else "direct"
        if height:
            label = f"📺 {height}p ({ext[1:].upper()})"
        elif note == "main":
            label = "📡 اصلی (Auto)"
        else:
            label = "📡 MP4" if method == "direct" else "📡 HLS"
        q = {"label": label, "url": url, "method": method, "height": height}
        if note:
            q["note"] = note
        # فایل‌های دامنه‌ی خود سایت اولویت دارن
        q["_site"] = 1 if _is_site_media(url) else 0
        qualities.append(q)

    # ۱) BB Pimp Player — data-bb-main-src
    for m in re.finditer(r'data-bb-main-src\s*=\s*["\']([^"\']+)["\']', html, re.I):
        _add(m.group(1), "main")

    # ۲) KVS flashvars — video_url / video_alt_url / video_url_text
    for m in re.finditer(
        r"(?:video_url|video_alt_url|video_alt_url\d*)\s*['\"]?\s*:\s*['\"]([^'\"]+)['\"]",
        html, re.I,
    ):
        _add(m.group(1), "kvs")

    # ۳) تگ‌های <source> داخل <video>
    for vm in re.finditer(r"<video[^>]*>(.*?)</video>", html, re.I | re.S):
        for sm in re.finditer(
            r'<source[^>]+src\s*=\s*["\']([^"\']+)["\']', vm.group(1), re.I
        ):
            _add(sm.group(1), "source")

    # ۴) src خود <video>
    for m in re.finditer(
        r"<video[^>]+src\s*=\s*[\"']([^\"']+\.mp4[^\"']*)[\"']", html, re.I
    ):
        _add(m.group(1), "video-src")

    # ۵) اسکن عمومی mp4/m3u8 (فقط اگه هیچ‌کدوم از بالا نبود)
    if not qualities:
        for m in re.finditer(
            r"https?://[^\s\"'\\<>]+?\.(?:mp4|m3u8)(?:\?[^\s\"'\\<>]*)?", html, re.I
        ):
            _add(m.group(0), "scan")

    # dedupe + sort: سایت‌دامنه اول، بعد کیفیت (فیلد height — دقیق‌تر از لیبل)، بعد note=main
    unique = {}
    for q in qualities:
        unique.setdefault(q["url"], q)
    result = sorted(
        unique.values(),
        key=lambda q: (q.get("_site", 0), q.get("height", 0) or 0, 1 if q.get("note") == "main" else 0),
        reverse=True,
    )
    for q in result:
        q.pop("_site", None)
    return result, title


# ─── Extraction: yt-dlp fallback (سقف زمانی + اعتبارسنجی) ──


async def _extract_with_ytdlp(url: str) -> Tuple[List[dict], str]:
    """Fallback yt-dlp با سقف زمانی سخت و فقط URLهای مدیای واقعی."""
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
            logger.warning("[PornHex] yt-dlp extract timeout (%ds)", _YTDLP_EXTRACT_TIMEOUT)
            return [], f"استخراج خیلی طول کشید (سقف {_YTDLP_EXTRACT_TIMEOUT} ثانیه)"

        if process.returncode != 0:
            err = stderr.decode(errors="replace").strip()[:160]
            return [], err or "yt-dlp failed"

        try:
            data = json.loads(stdout.decode(errors="replace"))
        except Exception:
            return [], "yt-dlp output parse failed"

        title = data.get("title", "") or "PornHex"
        qualities: List[dict] = []
        seen: set = set()
        for fmt in data.get("formats", []):
            if (fmt.get("vcodec") or "") == "none":
                continue
            furl = fmt.get("url", "")
            if not furl or furl in seen or not _is_real_media_url(furl):
                continue
            if _is_excluded(furl):
                continue
            seen.add(furl)
            height = fmt.get("height") or 0
            ext = fmt.get("ext", "mp4")
            method = "m3u8" if (ext == "m3u8" or ".m3u8" in furl) else "direct"
            label = f"📺 {height}p ({ext.upper()})" if height else f"📡 {ext.upper()}"
            qualities.append({"label": label, "url": furl, "method": method, "height": height or 0})

        if not qualities:
            return [], "کیفیت واقعی پیدا نشد (احتمالاً Cloudflare صفحه رو بلاک کرده)"

        qualities.sort(key=quality_sort_key, reverse=True)
        return qualities, title
    except Exception as e:
        return [], str(e)[:160]


# ─── Main extraction ───────────────────────────────────────


async def extract_pornhex_qualities(url: str) -> Tuple[List[dict], str]:
    """استخراج کیفیت‌ها — HTML مستقیم (۲ impersonation) → yt-dlp سقف‌دار."""
    if not is_pornhex_url(url):
        return [], "Invalid URL"

    if not check_impersonation_support():
        logger.warning("[PornHex] curl_cffi unavailable → yt-dlp")
        return await _extract_with_ytdlp(url)

    logger.info("[PornHex] Fetching: %s", url[:100])

    html = None
    status = 0
    for imp in ("chrome", "chrome131"):
        html, status = await fetch_html(url=url, referer=_SITE_REFERER, impersonate=imp)
        if html and status == 200:
            break
        logger.info("[PornHex] fetch failed (imp=%s status=%s) — trying next", imp, status)

    if html and status == 200:
        qualities, title = _extract_from_html(html, url)
        if qualities:
            logger.info(
                "[PornHex] Extracted %d quality(ies) from HTML for: %s",
                len(qualities), title[:60],
            )
            return qualities, title
        logger.info("[PornHex] HTML got but no media URL → yt-dlp")
    else:
        logger.warning("[PornHex] HTML fetch failed (status=%s) → yt-dlp", status)

    return await _extract_with_ytdlp(url)


# ─── Download ──────────────────────────────────────────────


def _is_downloadable(url: str) -> bool:
    """هر مدیایی که استخراج‌کننده قبول کرده (تبلیغ‌ها قبلاً فیلتر شدن)."""
    return bool(url) and url.lower().startswith(("http://", "https://")) and not _is_excluded(url)


async def download_pornhex_video(
    video_url: str,
    filepath: str,
    progress_cb: Optional[ProgressCallback] = None,
) -> Tuple[bool, str, int]:
    """دانلود با حداکثر سرعت: multi-segment ۱۶ کانکشن → direct → (yt-dlp|m3u8)."""
    if not _is_downloadable(video_url):
        return False, "URL not allowed", 0

    progress_cb = progress_cb or (lambda text: _noop())
    is_hls = ".m3u8" in video_url.lower()

    # m3u8 → yt-dlp (HLS با fragmented downloader سریع)
    if is_hls:
        return await _download_m3u8_impl(
            video_url, filepath, progress_cb, referer=_SITE_REFERER,
        )

    # ۱) multi-segment 16x
    success, error, size = await _download_direct_multi_impl(
        video_url, filepath, progress_cb, referer=_SITE_REFERER,
    )
    if success:
        return True, "", size
    cleanup_file(filepath)
    logger.info("[PornHex] multi-segment failed (%s) → direct", error[:80])

    # ۲) تک‌کانکشنه استریمی
    success, error, size = await _download_direct_impl(
        video_url, filepath, progress_cb, referer=_SITE_REFERER,
    )
    if success:
        return True, "", size
    cleanup_file(filepath)
    logger.info("[PornHex] direct failed (%s) → yt-dlp", error[:80])

    # ۳) yt-dlp (با aria2c اگه موجوده)
    success, error, size = await _download_ytdlp_impl(
        video_url, filepath, progress_cb, referer=_SITE_REFERER,
    )
    if not success:
        cleanup_file(filepath)
    return success, error, size


async def _noop() -> None:
    return None
