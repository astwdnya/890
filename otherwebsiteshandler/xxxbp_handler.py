"""
xxxbp_handler.py
────────────────
استخراج و دانلود ویدیو از xxxbp.tv

روش کار (بر اساس تحلیل واقعی سایت — تأیید شده با تست):
  - سایت از پلیر اختصاصی XPlayer استفاده می‌کنه (نه Video.js، نه KT Player)
  - ویدیو به‌صورت lazy لود می‌شه: صفحه شامل data-video-id و data-video-sizes است
  - بعد از لود صفحه، یه درخواست POST به API ارسال می‌شه که URL های واقعی ویدیو رو برمی‌گردونه

  API endpoint:
      POST https://api.xxxbp.tv/hls
      Content-Type: application/x-www-form-urlencoded
      Body: id=<video_id>
      Response (JSON):
          {
            "master": "<HLS master.m3u8 URL>",
            "mp4":    [{"src": "<url>", "title": "720p"}, ...],
            "links":  [{"src": "<per-quality m3u8>", "title": "720p"}, ...]
          }

  - video_id از مسیر URL استخراج می‌شه: /video/<id>/<slug> → id
  - URL های MP4 شامل توکن امضا شده در مسیر هستن (مثل /<token>,<expire>/mp4/...)
    - نیازی به کوکی یا هدر اضافه نیست
    - توکن تا تاریخ expire معتبره (معمولاً چند روز)
  - سرور CDN (mjedge.net) از Range request پشتیبانی می‌کنه (HTTP 206, Accept-Ranges: bytes)
  - IP های datacenter بلاک نمی‌شن (برخلاف hdtube) ولی curl_cffi برای اطمینان از TLS fingerprint استفاده می‌شه

استراتژی دانلود:
  1. POST به API برای گرفتن URL های MP4
  2. multi-segment download با N workers (پیش‌فرض 16)
  3. fallback به single-connection
  4. fallback به yt-dlp روی master.m3u8 (HLS)

وابستگی‌ها:
    pip install aiohttp aiofiles curl_cffi yt-dlp
"""

import asyncio
import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, List, Optional, Tuple
from urllib.parse import urlparse, urljoin, unquote

import aiofiles
import aiohttp
from aiohttp import ClientTimeout, CookieJar, TCPConnector

# در صورت نصب بودن curl_cffi، ازش استفاده می‌کنیم (TLS fingerprint واقعی مرورگر)
try:
    from curl_cffi.requests import AsyncSession as _CurlAsyncSession
    _HAS_CURL_CFFI = True
except ImportError:
    _CurlAsyncSession = None
    _HAS_CURL_CFFI = False

logger = logging.getLogger("XXXBPHandler")

# ─── Constants ────────────────────────────────────────────────────────────

MAX_DOWNLOAD_SIZE = 50 * 1024 * 1024 * 1024  # 2 GB
MIN_VALID_VIDEO_SIZE = 100 * 1024  # 100 KB
PROGRESS_INTERVAL = 1.0
CHUNK_SIZE = 1024 * 1024  # 1 MB
MAX_RETRIES = 3
RETRY_DELAY = 2.0
MULTI_SEGMENT_MIN_SIZE = 5 * 1024 * 1024  # 5 MB

# تنظیمات سرعت بالا
DEFAULT_SEGMENT_WORKERS = 16
SEGMENT_CHUNK_SIZE = 10 * 1024 * 1024  # 10 MB
CONNECTOR_LIMIT = 32
CONNECTOR_LIMIT_PER_HOST = 32

# دامنه‌های مجاز
_ALLOWED_HOSTS = frozenset({
    "xxxbp.tv",
    "www.xxxbp.tv",
    "m.xxxbp.tv",
})

# هدرهای پیش‌فرض
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_DEFAULT_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# هدرهای مخصوص درخواست به API
_API_HEADERS = {
    **_DEFAULT_HEADERS,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": "https://xxxbp.tv",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "X-Requested-With": "XMLHttpRequest",
}

# آدرس API
_API_BASE = "https://api.xxxbp.tv"
_API_HLS_PATH = "/hls"

ProgressCallback = Callable[[str], Awaitable[None]]


# ─── State Manager (جایگزین global dict — thread-safe) ────────────────────


@dataclass
class DownloadState:
    """state برای یه download فعال — جایگزین active_downloads global."""
    paused: bool = False
    cancelled: bool = False


# registry از download_id به DownloadState
# (همین thread-safe در داخل یه event loop، چون فقط از طریق await ها دسترسی می‌شه)
_active_downloads: dict[str, DownloadState] = {}


def _get_state(dl_id: str) -> DownloadState:
    if not dl_id:
        return DownloadState()  # dummy
    if dl_id not in _active_downloads:
        _active_downloads[dl_id] = DownloadState()
    return _active_downloads[dl_id]


def _is_cancelled(dl_id: str) -> bool:
    return _get_state(dl_id).cancelled


# ─── Utility ──────────────────────────────────────────────────────────────


def is_xxxbp_url(url: str) -> bool:
    """بررسی اینکه URL مربوط به xxxbp.tv هست."""
    try:
        host = urlparse(url).hostname or ""
        return host in _ALLOWED_HOSTS or host.endswith(".xxxbp.tv")
    except Exception:
        return False


def extract_video_id(url: str) -> Optional[int]:
    """
    استخراج video_id از URL.

    الگوهای پشتیبانی شده:
        /video/11978/slug
        /video/11978
        /embed/11978
        ?id=11978
    """
    try:
        parsed = urlparse(url)
        path = parsed.path or ""
        # الگوی /video/<id>/<slug> یا /video/<id>
        m = re.search(r"/(?:video|embed)/(\d+)", path)
        if m:
            return int(m.group(1))
        # الگوی ?id=<id>
        query = parsed.query or ""
        m = re.search(r"(?:^|&)id=(\d+)", query)
        if m:
            return int(m.group(1))
        return None
    except (ValueError, TypeError):
        return None


def _cleanup_file(filepath: str) -> None:
    try:
        if filepath and os.path.exists(filepath):
            os.remove(filepath)
    except OSError as e:
        logger.warning("Failed to cleanup file %s: %s", filepath, e)


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / 1024 / 1024:.1f} MB"
    return f"{size_bytes / 1024 / 1024 / 1024:.2f} GB"


def _clean_url(url: str) -> str:
    """نرمال‌سازی URL (unquote، حذف &amp;، حذف quote‌های اضافه)."""
    if not url:
        return ""
    url = unquote(url).replace("&amp;", "&")
    url = re.sub(r"[\\/]+$", "", url)
    url = url.rstrip("',\"")
    return url.strip()


def _detect_quality(url: str, title: str = "") -> Tuple[str, int, bool]:
    """
    تشخیص کیفیت از URL یا title.

    Returns:
        (quality_text, height, is_hd)
    """
    combined = f"{url} {title}".lower()
    # اولویت: title دقیق
    m = re.search(r"(\d{3,4})p", title.lower()) if title else None
    if m:
        h = int(m.group(1))
        return f"{h}p", h, h >= 720
    # fallback: بررسی URL
    for h in (2160, 1440, 1080, 720, 480, 360, 240, 144):
        if f"_{h}p" in combined or f"{h}p" in combined:
            return f"{h}p", h, h >= 720
    return "default", 480, False


def _format_progress(downloaded: int, content_length: int, start_time: float, now: float) -> str:
    elapsed = now - start_time
    speed = downloaded / elapsed if elapsed > 0 else 0
    dl_mb = downloaded / 1024 / 1024
    if content_length > 0:
        total_mb = content_length / 1024 / 1024
        pct = downloaded / content_length * 100
        filled = int(pct / 5)
        bar = "█" * filled + "░" * (20 - filled)
        speed_mb = min(speed / 1024 / 1024, 999)
        eta_secs = int((content_length - downloaded) / speed) if speed > 0 else 0
        eta_m, eta_s = divmod(eta_secs, 60)
        return (
            f"📥 **Downloading...**\n(هندلر)\n`[{bar}]`\n"
            f"💾 {dl_mb:.1f}/{total_mb:.1f} MB  •  ⚡ {speed_mb:.1f} MB/s\n"
            f"📊 {pct:.1f}%  •  ⏱ ETA: {eta_m}:{eta_s:02d}"
        )
    return f"📥 **Downloading...**\n(هندلر)\n💾 {dl_mb:.1f} MB  •  ⚡ {speed / 1024 / 1024:.1f} MB/s"


def _parse_iso8601_duration(s: str) -> Optional[int]:
    """تبدیل PT6M6S → 366 (ثانیه)."""
    if not s:
        return None
    total = 0
    for unit, mult in [("H", 3600), ("M", 60), ("S", 1)]:
        m = re.search(rf"(\d+){unit}", s)
        if m:
            total += int(m.group(1)) * mult
    return total if total > 0 else None


def _check_curl_cffi() -> bool:
    return _HAS_CURL_CFFI


# ─── Page Metadata ────────────────────────────────────────────────────────


def _extract_title(html: str) -> str:
    """استخراج عنوان ویدیو از HTML."""
    # روش 1: og:title
    m = re.search(
        r'(?:property|name)=["\']og:title["\']\s+content=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    )
    if not m:
        m = re.search(
            r'content=["\']([^"\']+)["\']\s+(?:property|name)=["\']og:title["\']',
            html, re.IGNORECASE,
        )
    if m:
        title = m.group(1).strip()
        return _strip_site_suffix(title)

    # روش 2: <title>
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    if m:
        return _strip_site_suffix(m.group(1).strip()) or "Untitled"

    return "Untitled"


def _strip_site_suffix(title: str) -> str:
    """حذف پسوندهای سایت مثل ' - XXXBP'."""
    title = re.sub(r"\s*[-|@]\s*(?:xxxbp(?:\.tv)?|XXX BP)\s*$", "", title, flags=re.IGNORECASE)
    return title.strip()


def _extract_thumbnail(html: str) -> str:
    m = re.search(
        r'(?:property|name)=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    m = re.search(r'<video[^>]+poster=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # روش 3: data-video-poster
    m = re.search(r'data-video-poster=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


def _extract_duration(html: str) -> Optional[int]:
    """استخراج مدت زمان ویدیو (به ثانیه)."""
    # روش 1: ISO 8601 در JSON-LD
    m = re.search(r'"duration"\s*:\s*"(PT[^"]+)"', html)
    if m:
        d = _parse_iso8601_duration(m.group(1))
        if d:
            return d
    # روش 2: og:video:duration
    m = re.search(
        r'(?:property|name)=["\'](?:og:video:duration|video:duration)["\']\s+content=["\'](\d+)["\']',
        html, re.IGNORECASE,
    )
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    # روش 3: data-duration
    m = re.search(r'data-duration=["\'](\d+)["\']', html, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return None


# ─── Fetch Page / API ─────────────────────────────────────────────────────


async def _fetch_page(url: str) -> Tuple[Optional[str], str]:
    """
    GET صفحه ویدیو.

    Returns:
        (html, error) — در صورت موفقیت error خالی است.
    """
    if _check_curl_cffi():
        try:
            async with _CurlAsyncSession() as session:
                resp = await session.get(
                    url, impersonate="chrome",
                    headers=_DEFAULT_HEADERS, allow_redirects=True, timeout=30,
                )
                if resp.status_code == 200 and resp.text:
                    text = resp.text
                    # صحت‌سنجی ساده: باید یا playerNext یا data-video-id یا <title> داشته باشه
                    if ("playerNext" in text or "data-video-id" in text
                            or "xxxbp" in text.lower()):
                        logger.info("Page fetched via curl_cffi, size=%d", len(text))
                        return text, ""
                    logger.warning("curl_cffi: 200 ولی محتوای مورد انتظار پیدا نشد")
                else:
                    logger.warning("curl_cffi fetch: HTTP %s", resp.status_code)
        except Exception as e:
            logger.warning(f"curl_cffi fetch error: {e}")

    # fallback به aiohttp
    try:
        timeout = ClientTimeout(total=30, connect=10)
        async with aiohttp.ClientSession(timeout=timeout, headers=_DEFAULT_HEADERS) as session:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status == 200:
                    html = await resp.text(errors="replace")
                    logger.info("Page fetched via aiohttp, size=%d", len(html))
                    return html, ""
                logger.warning("aiohttp fetch: HTTP %s", resp.status)
    except Exception as e:
        logger.warning(f"aiohttp fetch error: {e}")

    return None, "Failed to fetch page"


async def _call_hls_api(video_id: int, page_url: str) -> Tuple[Optional[dict], str]:
    """
    POST به https://api.xxxbp.tv/hls برای گرفتن URL های ویدیو.

    Returns:
        (json_data, error) — json_data شامل کلیدهای master/mp4/links است.
    """
    api_url = f"{_API_BASE}{_API_HLS_PATH}"
    body = f"id={video_id}"
    headers = {**_API_HEADERS, "Referer": page_url}

    # روش 1: curl_cffi (اول — برای TLS fingerprint واقعی)
    if _check_curl_cffi():
        try:
            async with _CurlAsyncSession() as session:
                resp = await session.post(
                    api_url, impersonate="chrome",
                    headers=headers, data=body,
                    allow_redirects=True, timeout=20,
                )
                if resp.status_code == 200 and resp.text:
                    try:
                        data = resp.json()
                        if _validate_api_response(data):
                            logger.info("API call via curl_cffi OK (mp4 count=%d)",
                                        len(data.get("mp4") or []))
                            return data, ""
                        logger.warning("API response invalid shape: keys=%s",
                                       list(data.keys()) if isinstance(data, dict) else "not-dict")
                    except (json.JSONDecodeError, ValueError) as e:
                        logger.warning("API JSON parse error: %s", e)
                else:
                    logger.warning("API curl_cffi: HTTP %s", resp.status_code)
        except Exception as e:
            logger.warning(f"API curl_cffi error: {e}")

    # روش 2: aiohttp (fallback)
    try:
        timeout = ClientTimeout(total=20, connect=10)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.post(api_url, data=body, allow_redirects=True) as resp:
                if resp.status == 200:
                    text = await resp.text(errors="replace")
                    try:
                        data = json.loads(text)
                        if _validate_api_response(data):
                            logger.info("API call via aiohttp OK")
                            return data, ""
                    except (json.JSONDecodeError, ValueError) as e:
                        logger.warning("API aiohttp JSON parse error: %s", e)
                else:
                    logger.warning("API aiohttp: HTTP %s", resp.status)
    except Exception as e:
        logger.warning(f"API aiohttp error: {e}")

    return None, "Failed to call /hls API"


def _validate_api_response(data) -> bool:
    """اعتبارسنجی پاسخ API."""
    if not isinstance(data, dict):
        return False
    mp4 = data.get("mp4")
    master = data.get("master")
    # حداقل یکی از mp4 یا master باید موجود باشه
    if mp4 and not isinstance(mp4, list):
        return False
    if master and not isinstance(master, str):
        return False
    return bool(mp4 or master)


# ─── Extract Qualities (Public API) ───────────────────────────────────────


async def extract_xxxbp_qualities(url: str, progress_cb: Optional[ProgressCallback] = None
                                  ) -> Tuple[List[dict], str, dict]:
    """
    استخراج کیفیت‌های ویدیو از xxxbp.tv.

    Args:
        url: URL صفحه ویدیو (مثل https://xxxbp.tv/video/11978/slug)
        progress_cb: callback async برای گزارش پیشرفت

    Returns:
        (sources, title, info)
        sources: list of dicts با کلیدهای label/url/height/quality_key/method/is_hd
        title: عنوان ویدیو
        info: dict با کلیدهای thumbnail/page_url/duration/master/mp4_raw
    """
    if not is_xxxbp_url(url):
        return [], "Invalid URL (host not allowed)", {}

    video_id = extract_video_id(url)
    if not video_id:
        return [], "Invalid URL (could not extract video_id)", {}

    if progress_cb:
        await progress_cb("🔄 **دریافت اطلاعات ویدیو...**")

    # مرحله 1: GET صفحه برای metadata (title, thumbnail, duration)
    html, page_error = await _fetch_page(url)
    if html:
        title = _extract_title(html)
        thumbnail = _extract_thumbnail(html)
        duration = _extract_duration(html)
    else:
        logger.warning("Page fetch failed (%s); continuing with API only", page_error)
        title = f"xxxbp video {video_id}"
        thumbnail = ""
        duration = None

    # مرحله 2: POST به API برای URL های ویدیو
    if progress_cb:
        await progress_cb("📡 **درخواست لینک‌های دانلود از API...**")

    api_data, api_error = await _call_hls_api(video_id, url)
    if not api_data:
        return [], f"خطا در دریافت لینک‌ها: {api_error}", {}

    # مرحله 3: ساخت لیست کیفیت‌ها از mp4 array
    sources = _build_sources_from_api(api_data)

    if not sources:
        # اگه mp4 خالی بود ولی master بود، fallback به yt-dlp با master.m3u8
        master = api_data.get("master", "")
        if master:
            return [], "Only HLS available (no direct MP4)", {
                "thumbnail": thumbnail,
                "page_url": url,
                "video_id": video_id,
                "duration": duration,
                "master": master,
                "fetch_method": "curl_cffi" if _check_curl_cffi() else "aiohttp",
            }
        return [], "URL ویدیو در پاسخ API پیدا نشد", {}

    if progress_cb:
        labels = ", ".join(s["label"] for s in sources)
        dur_str = ""
        if duration:
            mins, secs = divmod(duration, 60)
            dur_str = f" ({mins}:{secs:02d})"
        await progress_cb(f"✅ **پیدا شد:** {title[:50]}{dur_str}\n🎞 کیفیت‌ها: {labels}")

    return sources, title, {
        "thumbnail": thumbnail,
        "page_url": url,
        "video_id": video_id,
        "duration": duration,
        "master": api_data.get("master", ""),
        "fetch_method": "curl_cffi" if _check_curl_cffi() else "aiohttp",
    }


def _build_sources_from_api(api_data: dict) -> List[dict]:
    """
    ساخت لیست کیفیت‌ها از پاسخ API.

    اولویت: mp4 array (دانلود مستقیم) > links array (HLS per-quality)
    """
    sources: List[dict] = []
    seen_urls: set = set()

    # ─── روش 1: mp4 array (اولویت اصلی — دانلود مستقیم) ───
    for item in api_data.get("mp4") or []:
        if not isinstance(item, dict):
            continue
        raw_url = item.get("src", "")
        if not raw_url:
            continue
        url = _clean_url(raw_url)
        if url in seen_urls:
            continue
        seen_urls.add(url)

        title = str(item.get("title") or "")
        quality_text, height, is_hd = _detect_quality(url, title)
        label = f"📺 MP4 {quality_text}" if quality_text != "default" else "📺 MP4 (default)"

        sources.append({
            "label": label,
            "url": url,
            "height": height,
            "quality_key": quality_text,
            "method": "api_mp4",
            "is_hd": is_hd,
        })
        logger.info("Found MP4 source (%s): %s", quality_text, url[:100])

    # ─── روش 2: links array (HLS per-quality m3u8 — فقط اگه mp4 نبود) ───
    if not sources:
        for item in api_data.get("links") or []:
            if not isinstance(item, dict):
                continue
            raw_url = item.get("src", "")
            if not raw_url:
                continue
            url = _clean_url(raw_url)
            if url in seen_urls:
                continue
            seen_urls.add(url)

            title = str(item.get("title") or "")
            quality_text, height, is_hd = _detect_quality(url, title)
            label = f"📡 HLS {quality_text}" if quality_text != "default" else "📡 HLS (default)"

            sources.append({
                "label": label,
                "url": url,
                "height": height,
                "quality_key": quality_text,
                "method": "api_hls",
                "is_hd": is_hd,
            })
            logger.info("Found HLS source (%s): %s", quality_text, url[:100])

    # مرتب‌سازی: بالاترین کیفیت اول
    sources.sort(key=lambda q: q.get("height", 0), reverse=True)
    return sources


# ─── Download: Probe (HEAD/Range) ─────────────────────────────────────────


async def _probe_size(url: str, referer: str, cookies: dict) -> Tuple[int, str, str]:
    """
    Probe کردن سایز واقعی فایل و پشتیبانی از Range.

    Returns:
        (content_length, accept_ranges, error)
        accept_ranges == "bytes" اگه Range پشتیبانی بشه.
    """
    headers = {**_DEFAULT_HEADERS, "Referer": referer, "Accept": "*/*"}

    # روش 1: curl_cffi HEAD
    if _check_curl_cffi():
        try:
            async with _CurlAsyncSession() as session:
                for name, val in cookies.items():
                    try:
                        session.cookies.set(name, val)
                    except Exception:
                        pass
                resp = await session.head(
                    url, impersonate="chrome",
                    headers=headers, allow_redirects=True, timeout=15,
                )
                if resp.status_code in (200, 206):
                    cl = int(resp.headers.get("Content-Length", 0))
                    ar = resp.headers.get("Accept-Ranges", "").lower()
                    ct = resp.headers.get("Content-Type", "")
                    if ct and not ct.startswith("video/") and not ct.startswith("application/"):
                        logger.warning("HEAD returned non-video content-type: %s", ct)
                    if cl > 0:
                        return cl, ar, ""
                    # اگه HEAD بدون Content-Length بود، probe با Range امتحان کن
                elif resp.status_code == 403:
                    return 0, "", "HTTP_403"
        except Exception as e:
            logger.warning(f"curl_cffi HEAD failed: {e}")

    # روش 2: aiohttp HEAD
    try:
        timeout = ClientTimeout(total=15, connect=8)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers, cookies=cookies) as s:
            async with s.head(url, allow_redirects=True) as r:
                if r.status in (200, 206):
                    cl = int(r.headers.get("Content-Length", 0))
                    ar = r.headers.get("Accept-Ranges", "").lower()
                    if cl > 0:
                        return cl, ar, ""
                elif r.status == 403:
                    return 0, "", "HTTP_403"
    except Exception as e:
        logger.warning(f"aiohttp HEAD failed: {e}")

    # روش 3: probe با Range bytes=0-0 (curl_cffi)
    if _check_curl_cffi():
        try:
            async with _CurlAsyncSession() as session:
                for name, val in cookies.items():
                    try:
                        session.cookies.set(name, val)
                    except Exception:
                        pass
                resp = await session.get(
                    url, impersonate="chrome",
                    headers={**headers, "Range": "bytes=0-0"},
                    allow_redirects=True, timeout=15,
                )
                if resp.status_code in (200, 206):
                    cr = resp.headers.get("Content-Range", "")
                    m = re.search(r"/(\d+)", cr)
                    if m:
                        return int(m.group(1)), "bytes", ""
                    cl = int(resp.headers.get("Content-Length", 0))
                    if cl > 0:
                        return cl, "bytes" if resp.status_code == 206 else "", ""
        except Exception as e:
            logger.warning(f"curl_cffi probe failed: {e}")

    return 0, "", "Cannot determine file size"


# ─── Download: Multi-segment ──────────────────────────────────────────────


async def _download_multi_segment(
    direct_url: str,
    filepath: str,
    referer: str,
    cookies: dict,
    progress_cb: ProgressCallback,
    dl_id: str = "",
    num_workers: int = DEFAULT_SEGMENT_WORKERS,
) -> Tuple[bool, str, int]:
    """
    دانلود چند تیکه‌ای با work-queue pattern.

    URLs روی CDN xxxbp از Range پشتیبانی می‌کنن، پس می‌تونیم N تیکه رو
    موازی دانلود کنیم.
    """
    try:
        # Probe size
        content_length, accept_ranges, probe_err = await _probe_size(direct_url, referer, cookies)
        if probe_err == "HTTP_403":
            return False, "HTTP_403", 0
        if content_length == 0:
            return False, "Cannot determine file size", 0
        if content_length > MAX_DOWNLOAD_SIZE:
            return False, f"File too large: {_format_size(content_length)}", 0
        if accept_ranges != "bytes" or content_length < MULTI_SEGMENT_MIN_SIZE:
            return False, "Range not supported or file too small", 0

        total_mb = content_length / 1024 / 1024
        await progress_cb(
            f"📥 **Downloading...**\n(هندلر)\n💾 Size: {total_mb:.1f} MB\n🔥 {num_workers} parallel workers"
        )

        # ساخت chunks
        chunks: List[Tuple[int, int, int]] = []  # (idx, start, end)
        offset = 0
        idx = 0
        while offset < content_length:
            end = min(offset + SEGMENT_CHUNK_SIZE - 1, content_length - 1)
            chunks.append((idx, offset, end))
            offset = end + 1
            idx += 1

        total_chunks = len(chunks)
        logger.info(
            "[DL-XXXBP] Work-queue: %d chunks, %d workers, total=%d",
            total_chunks, num_workers, content_length,
        )

        # پیش‌تخصیص فایل
        try:
            with open(filepath, "wb") as f:
                f.truncate(content_length)
        except OSError as e:
            logger.warning("Could not pre-allocate file: %s", e)

        # State مشترک بین workers
        chunk_queue: asyncio.Queue = asyncio.Queue()
        for c in chunks:
            await chunk_queue.put(c)

        downloaded_bytes = [0] * total_chunks
        completed_chunks = [0]
        failed_chunks: List[Tuple[int, str]] = []
        start_time = time.time()
        last_update = [0.0]
        progress_lock = asyncio.Lock()
        file_write_lock = asyncio.Lock()
        first_chunk_started = [False]

        async def _update_progress(force: bool = False) -> None:
            now = time.time()
            if not force and now - last_update[0] < PROGRESS_INTERVAL:
                return
            last_update[0] = now
            total_dl = sum(downloaded_bytes)
            elapsed = now - start_time
            speed = total_dl / elapsed if elapsed > 0 else 0
            dl_mb = total_dl / 1024 / 1024
            total_mb_local = content_length / 1024 / 1024
            pct = (total_dl / content_length * 100) if content_length > 0 else 0
            filled = int(pct / 5)
            bar = "█" * filled + "░" * (20 - filled)
            speed_mb = min(speed / 1024 / 1024, 999)
            eta_secs = int((content_length - total_dl) / speed) if speed > 0 else 0
            eta_m, eta_s = divmod(eta_secs, 60)
            try:
                await progress_cb(
                    f"📥 **Downloading...**\n(هندلر)\n`[{bar}]`\n"
                    f"💾 {dl_mb:.1f}/{total_mb_local:.1f} MB  •  ⚡ {speed_mb:.1f} MB/s\n"
                    f"📊 {pct:.1f}%  •  ⏱ ETA: {eta_m}:{eta_s:02d}\n"
                    f"📦 {completed_chunks[0]}/{total_chunks} chunks • 🔥 {num_workers}x"
                )
            except Exception:
                pass

        # ساخت worker function
        if _check_curl_cffi():
            worker = _make_curl_worker(
                direct_url, filepath, referer, cookies, headers={**_DEFAULT_HEADERS, "Referer": referer, "Accept": "*/*"},
                chunk_queue=chunk_queue, downloaded_bytes=downloaded_bytes,
                completed_chunks=completed_chunks, failed_chunks=failed_chunks,
                progress_lock=progress_lock, file_write_lock=file_write_lock,
                first_chunk_started=first_chunk_started, update_progress=_update_progress,
                dl_id=dl_id,
            )
        else:
            worker = _make_aiohttp_worker(
                direct_url, filepath, cookies, headers={**_DEFAULT_HEADERS, "Referer": referer, "Accept": "*/*"},
                chunk_queue=chunk_queue, downloaded_bytes=downloaded_bytes,
                completed_chunks=completed_chunks, failed_chunks=failed_chunks,
                progress_lock=progress_lock, file_write_lock=file_write_lock,
                first_chunk_started=first_chunk_started, update_progress=_update_progress,
                dl_id=dl_id,
            )

        # اجرای workers
        try:
            results = await asyncio.gather(
                *[_make_worker_task(worker, i) for i in range(num_workers)],
                return_exceptions=True,
            )
        except Exception as e:
            logger.error("[DL-XXXBP] Work-queue error: %s", e, exc_info=True)
            _cleanup_file(filepath)
            return False, str(e)[:200], 0

        if _is_cancelled(dl_id):
            _cleanup_file(filepath)
            return False, "Cancelled by user", 0

        worker_failures = [r for r in results if r is not True]
        if worker_failures or failed_chunks:
            logger.warning(
                "[DL-XXXBP] %d workers failed, %d chunks failed",
                len(worker_failures), len(failed_chunks),
            )
            _cleanup_file(filepath)
            return False, f"Multi-segment failed: {len(failed_chunks)} chunks", 0

        # تأیید فایل نهایی
        file_size = os.path.getsize(filepath)
        if file_size < MIN_VALID_VIDEO_SIZE:
            _cleanup_file(filepath)
            return False, f"File too small ({file_size} bytes)", 0

        elapsed = time.time() - start_time
        avg_speed = file_size / elapsed / 1024 / 1024 if elapsed > 0 else 0
        logger.info(
            "[DL-XXXBP] Multi-segment DONE | size=%s | time=%.1fs | avg=%.1f MB/s",
            _format_size(file_size), elapsed, avg_speed,
        )
        return True, "", file_size

    except Exception as e:
        logger.error("[DL-XXXBP] Multi-segment error: %s", e, exc_info=True)
        _cleanup_file(filepath)
        return False, str(e)[:200], 0


async def _make_worker_task(worker_func, worker_id: int):
    """Wrapper برای اجرای worker با exception handling."""
    try:
        return await worker_func(worker_id)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("[DL-XXXBP] Worker %d crashed: %s", worker_id, e)
        return False


def _make_curl_worker(direct_url, filepath, referer, cookies, headers,
                       chunk_queue, downloaded_bytes, completed_chunks,
                       failed_chunks, progress_lock, file_write_lock,
                       first_chunk_started, update_progress, dl_id):
    """
    ساخت worker function برای دانلود با curl_cffi.
    هر worker یه session جداگانه می‌سازه.
    """

    async def _worker(worker_id: int) -> bool:
        max_retries = 3
        while True:
            if _is_cancelled(dl_id):
                return False
            try:
                c_idx, byte_start, byte_end = chunk_queue.get_nowait()
            except asyncio.QueueEmpty:
                return True

            chunk_size = byte_end - byte_start + 1

            for attempt in range(max_retries):
                if _is_cancelled(dl_id):
                    return False
                try:
                    async with _CurlAsyncSession() as session:
                        # set cookies
                        for name, val in cookies.items():
                            try:
                                session.cookies.set(name, val)
                            except Exception:
                                pass

                        resp = await session.get(
                            direct_url, impersonate="chrome",
                            headers={**headers, "Range": f"bytes={byte_start}-{byte_end}"},
                            allow_redirects=True, timeout=300,
                        )
                        if resp.status_code not in (200, 206):
                            raise RuntimeError(f"HTTP {resp.status_code}")

                        if not first_chunk_started[0]:
                            first_chunk_started[0] = True
                            await update_progress(force=True)

                        chunk_data = resp.content if hasattr(resp, "content") else resp.body
                        if isinstance(chunk_data, str):
                            chunk_data = chunk_data.encode("utf-8", errors="replace")

                        if len(chunk_data) != chunk_size:
                            raise RuntimeError(
                                f"Size mismatch: expected {chunk_size}, got {len(chunk_data)}"
                            )

                        async with file_write_lock:
                            async with aiofiles.open(filepath, "r+b") as f:
                                await f.seek(byte_start)
                                await f.write(chunk_data)

                        downloaded_bytes[c_idx] = chunk_size
                        async with progress_lock:
                            completed_chunks[0] += 1
                            await update_progress()
                        break  # موفقیت، break retry loop

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(
                        "[DL-XXXBP] W%d c%d attempt %d failed: %s",
                        worker_id, c_idx, attempt + 1, e,
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 * (attempt + 1))
                    else:
                        failed_chunks.append((c_idx, str(e)[:100]))
                        try:
                            chunk_queue.task_done()
                        except Exception:
                            pass
                        return False

            try:
                chunk_queue.task_done()
            except Exception:
                pass
        return True

    return _worker


def _make_aiohttp_worker(direct_url, filepath, cookies, headers,
                          chunk_queue, downloaded_bytes, completed_chunks,
                          failed_chunks, progress_lock, file_write_lock,
                          first_chunk_started, update_progress, dl_id):
    """
    ساخت worker function برای دانلود با aiohttp (fallback).
    از shared session استفاده می‌کنه برای کارایی.
    """
    shared_timeout = ClientTimeout(total=600, connect=30, sock_read=120)
    connector = TCPConnector(
        limit=CONNECTOR_LIMIT,
        limit_per_host=CONNECTOR_LIMIT_PER_HOST,
        keepalive_timeout=60,
        enable_cleanup_closed=True,
    )

    # shared session (در async context ساخته می‌شه)
    session_holder = {"session": None}

    async def _ensure_session():
        if session_holder["session"] is None:
            session_holder["session"] = aiohttp.ClientSession(
                timeout=shared_timeout, headers=headers, cookies=cookies, connector=connector,
            )
        return session_holder["session"]

    async def _worker(worker_id: int) -> bool:
        max_retries = 3
        session = await _ensure_session()

        while True:
            if _is_cancelled(dl_id):
                return False
            try:
                c_idx, byte_start, byte_end = chunk_queue.get_nowait()
            except asyncio.QueueEmpty:
                return True

            chunk_size = byte_end - byte_start + 1

            for attempt in range(max_retries):
                if _is_cancelled(dl_id):
                    return False
                try:
                    async with session.get(
                        direct_url,
                        headers={"Range": f"bytes={byte_start}-{byte_end}"},
                        allow_redirects=True,
                    ) as resp:
                        if resp.status not in (200, 206):
                            raise RuntimeError(f"HTTP {resp.status}")
                        if not first_chunk_started[0]:
                            first_chunk_started[0] = True
                            await update_progress(force=True)
                        chunk_data = bytearray()
                        async for piece in resp.content.iter_chunked(CHUNK_SIZE):
                            if not piece:
                                continue
                            if _is_cancelled(dl_id):
                                return False
                            chunk_data.extend(piece)
                        if len(chunk_data) != chunk_size:
                            raise RuntimeError(
                                f"Size mismatch: expected {chunk_size}, got {len(chunk_data)}"
                            )
                        async with file_write_lock:
                            async with aiofiles.open(filepath, "r+b") as f:
                                await f.seek(byte_start)
                                await f.write(bytes(chunk_data))
                        downloaded_bytes[c_idx] = chunk_size
                        async with progress_lock:
                            completed_chunks[0] += 1
                            await update_progress()
                        break

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(
                        "[DL-XXXBP] W%d c%d attempt %d failed: %s",
                        worker_id, c_idx, attempt + 1, e,
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 * (attempt + 1))
                    else:
                        failed_chunks.append((c_idx, str(e)[:100]))
                        try:
                            chunk_queue.task_done()
                        except Exception:
                            pass
                        return False

            try:
                chunk_queue.task_done()
            except Exception:
                pass
        return True

    async def _cleanup_session():
        if session_holder["session"]:
            await session_holder["session"].close()

    # attach cleanup for caller
    _worker._cleanup = _cleanup_session  # type: ignore
    return _worker


# ─── Download: Single connection (fallback) ───────────────────────────────


async def _download_single(url: str, filepath: str, referer: str, cookies: dict,
                            progress_cb: ProgressCallback, dl_id: str = "") -> Tuple[bool, str, int]:
    """
    دانلود با single connection (fallback اگه Range پشتیبانی نشه).
    از curl_cffi استفاده می‌کنه با streaming (نه بارگذاری کل فایل در RAM).
    """
    headers = {**_DEFAULT_HEADERS, "Referer": referer, "Accept": "*/*"}

    # روش 1: curl_cffi با stream
    if _check_curl_cffi():
        try:
            t0 = time.time()
            async with _CurlAsyncSession() as session:
                for name, val in cookies.items():
                    try:
                        session.cookies.set(name, val)
                    except Exception:
                        pass

                resp = await session.get(
                    url, impersonate="chrome", headers=headers,
                    allow_redirects=True, timeout=3600,
                    stream=True,  # streaming — مهم!
                )
                if resp.status_code not in (200, 206):
                    _cleanup_file(filepath)
                    return False, f"HTTP {resp.status_code}", 0

                content_length = int(resp.headers.get("Content-Length", 0))
                if content_length > MAX_DOWNLOAD_SIZE:
                    _cleanup_file(filepath)
                    return False, f"File too large: {_format_size(content_length)}", 0

                downloaded = 0
                start_time = time.time()
                last_update = 0.0

                async with aiofiles.open(filepath, "wb") as f:
                    async for chunk in resp.aiter_content(chunk_size=CHUNK_SIZE):
                        if _is_cancelled(dl_id):
                            _cleanup_file(filepath)
                            return False, "Cancelled by user", 0
                        if not chunk:
                            continue
                        await f.write(chunk)
                        downloaded += len(chunk)
                        now = time.time()
                        if now - last_update >= PROGRESS_INTERVAL:
                            last_update = now
                            await progress_cb(
                                _format_progress(downloaded, content_length, start_time, now)
                            )

                size = os.path.getsize(filepath)
                if size < MIN_VALID_VIDEO_SIZE:
                    _cleanup_file(filepath)
                    return False, f"File too small ({size} bytes)", 0
                elapsed = time.time() - t0
                logger.info(
                    "[DL-XXXBP] Single (curl_cffi) DONE | size=%s | time=%.1fs",
                    _format_size(size), elapsed,
                )
                return True, "", size

        except Exception as e:
            logger.warning(f"[DL-XXXBP] Single (curl_cffi) error: {e}")
            _cleanup_file(filepath)

    # روش 2: aiohttp با streaming
    error = ""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            timeout = ClientTimeout(total=3600, connect=30, sock_read=120)
            async with aiohttp.ClientSession(timeout=timeout, headers=headers, cookies=cookies) as s:
                async with s.get(url, allow_redirects=True) as resp:
                    if resp.status != 200:
                        error = f"HTTP {resp.status}"
                        if 400 <= resp.status < 500:
                            _cleanup_file(filepath)
                            return False, error, 0
                        # retry on 5xx
                    else:
                        content_length = int(resp.headers.get("Content-Length", 0))
                        if content_length > MAX_DOWNLOAD_SIZE:
                            return False, f"File too large: {_format_size(content_length)}", 0
                        downloaded = 0
                        start_time = time.time()
                        last_update = 0.0
                        async with aiofiles.open(filepath, "wb") as f:
                            async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                                if _is_cancelled(dl_id):
                                    _cleanup_file(filepath)
                                    return False, "Cancelled by user", 0
                                await f.write(chunk)
                                downloaded += len(chunk)
                                now = time.time()
                                if now - last_update >= PROGRESS_INTERVAL:
                                    last_update = now
                                    await progress_cb(
                                        _format_progress(downloaded, content_length, start_time, now)
                                    )
                        size = os.path.getsize(filepath)
                        if size < MIN_VALID_VIDEO_SIZE:
                            _cleanup_file(filepath)
                            return False, f"File too small ({size} bytes)", 0
                        return True, "", size
        except asyncio.CancelledError:
            _cleanup_file(filepath)
            raise
        except Exception as e:
            error = str(e)[:200]
        if attempt < MAX_RETRIES:
            _cleanup_file(filepath)
            await asyncio.sleep(RETRY_DELAY * attempt)
    _cleanup_file(filepath)
    return False, f"Failed after {MAX_RETRIES} attempts: {error}", 0


# ─── Download: yt-dlp (fallback نهایی روی HLS master) ─────────────────────


async def _download_with_ytdlp(url: str, filepath: str, progress_cb: ProgressCallback,
                                quality_key: str = "") -> Tuple[bool, str, int]:
    """
    fallback نهایی با yt-dlp روی master.m3u8 یا page URL.
    """
    if not shutil.which("yt-dlp"):
        return False, "yt-dlp not installed", 0
    has_curl_cffi = _check_curl_cffi()
    await progress_cb("📥 **Fallback: yt-dlp...**")
    format_selector = "best"
    if quality_key in ("720p", "480p", "1080p", "360p", "240p", "144p"):
        format_selector = f"{quality_key}/best"

    try:
        cmd = [
            "yt-dlp", "--no-warnings", "--progress", "--newline",
            "--no-check-certificates", "-f", format_selector,
            "-N", "32", "--concurrent-fragments", "32",
            "--retries", "10", "--fragment-retries", "10",
            "--buffer-size", "16K",
            "--max-filesize", str(MAX_DOWNLOAD_SIZE),
            "--add-header", f"User-Agent:{_USER_AGENT}",
            "--add-header", f"Referer:https://xxxbp.tv/",
            "-o", filepath,
        ]
        if has_curl_cffi:
            cmd.extend(["--impersonate", "chrome"])
        cmd.append(url)

        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        last_update = 0.0
        while True:
            try:
                line = await asyncio.wait_for(process.stdout.readline(), timeout=300)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                _cleanup_file(filepath)
                return False, "Download timed out", 0
            if not line:
                break
            text = line.decode(errors="replace").strip()
            now = time.time()
            if now - last_update >= PROGRESS_INTERVAL and text:
                last_update = now
                if "[download]" in text:
                    msg = _parse_ytdlp_progress(text)
                    if msg:
                        await progress_cb(msg)

        await process.wait()
        if process.returncode != 0:
            stderr = (await process.stderr.read()).decode(errors="replace")
            err_msg = _extract_ytdlp_error(stderr)
            return False, err_msg[:200], 0

        actual_path = _find_output_file(filepath)
        if not actual_path:
            return False, "Output file not found", 0
        size = os.path.getsize(actual_path)
        if size > MAX_DOWNLOAD_SIZE:
            _cleanup_file(actual_path)
            return False, "File exceeds size limit", 0
        if size < MIN_VALID_VIDEO_SIZE:
            _cleanup_file(actual_path)
            return False, f"File too small ({size} bytes)", 0
        if actual_path != filepath:
            try:
                os.rename(actual_path, filepath)
            except OSError:
                pass
        logger.info("[DL-XXXBP] yt-dlp DONE | size=%s", _format_size(size))
        return True, "", size

    except asyncio.CancelledError:
        _cleanup_file(filepath)
        raise
    except Exception as e:
        logger.error("[DL-XXXBP] yt-dlp error: %s", e, exc_info=True)
        _cleanup_file(filepath)
        return False, str(e)[:200], 0


def _parse_ytdlp_progress(text: str) -> Optional[str]:
    pct_match = re.search(r"(\d+\.?\d*)%", text)
    if not pct_match:
        return None
    pct = pct_match.group(1)
    size_match = re.search(r"of\s+~?\s*([\d.]+\s*\w+)", text)
    speed_match = re.search(r"at\s+([\d.]+\s*\w+/s)", text)
    eta_match = re.search(r"ETA\s+(\S+)", text)
    total = size_match.group(1) if size_match else "?"
    speed = speed_match.group(1) if speed_match else "?"
    eta = eta_match.group(1) if eta_match else "?"
    try:
        pct_num = float(pct)
        filled = int(pct_num / 5)
        bar = "█" * filled + "░" * (20 - filled)
    except (ValueError, TypeError):
        bar = "░" * 20
    return (
        f"📥 **Downloading...**\n(هندلر)\n`[{bar}]`\n"
        f"💾 {total}  •  ⚡ {speed}\n📊 {pct}%  •  ⏱ ETA: {eta}"
    )


def _extract_ytdlp_error(stderr: str) -> str:
    if not stderr:
        return "Unknown error"
    for line in stderr.splitlines():
        line = line.strip()
        if line.startswith("ERROR:"):
            return line[6:].strip()[:200]
    lines = [l.strip() for l in stderr.splitlines() if l.strip()]
    return lines[-1][:200] if lines else "Unknown error"


def _find_output_file(filepath: str) -> Optional[str]:
    if os.path.exists(filepath):
        return filepath
    base, _ = os.path.splitext(filepath)
    for ext in (".mp4", ".mkv", ".webm", ".ts"):
        candidate = base + ext
        if os.path.exists(candidate):
            return candidate
    return None


# ─── Public API: download ─────────────────────────────────────────────────


async def download_xxxbp_video(
    page_url: str,
    video_url: str,
    filepath: str,
    progress_cb: Optional[ProgressCallback] = None,
    cookies: Optional[dict] = None,
    dl_id: str = "",
) -> Tuple[bool, str, int]:
    """
    دانلود ویدیو از xxxbp.tv.

    Args:
        page_url: URL صفحه ویدیو (برای Referer)
        video_url: URL مستقیم MP4 (از extract_xxxbp_qualities)
        filepath: مسیر فایل خروجی
        progress_cb: callback async برای گزارش پیشرفت
        cookies: کوکی‌ها (معمولاً خالی — API نیازی نداره)
        dl_id: شناسه download برای cancellation support

    Returns:
        (success, error, size)
    """
    if not is_xxxbp_url(page_url):
        return False, "URL host not allowed", 0
    if not video_url:
        return False, "Empty video URL", 0

    if progress_cb is None:
        async def _noop(msg: str) -> None: pass
        progress_cb = _noop

    if dl_id:
        _get_state(dl_id)  # init state

    referer = page_url
    cookies = cookies or {}

    # ── روش 1: multi-segment ──
    logger.info("[DL-XXXBP] Attempt 1: multi-segment (%d workers)", DEFAULT_SEGMENT_WORKERS)
    success, error, size = await _download_multi_segment(
        video_url, filepath, referer, cookies, progress_cb, dl_id=dl_id,
    )
    if success:
        return True, "", size

    if error == "Cancelled by user":
        return False, error, 0

    if error == "HTTP_403":
        logger.info("[DL-XXXBP] 403 — refreshing API session...")
        if progress_cb:
            await progress_cb("🔄 **Refreshing session...**")
        vid = extract_video_id(page_url)
        if vid:
            api_data, _ = await _call_hls_api(vid, page_url)
            if api_data:
                sources = _build_sources_from_api(api_data)
                if sources:
                    video_url = sources[0]["url"]
                    logger.info("[DL-XXXBP] Got fresh URL from API")
                    success, error, size = await _download_multi_segment(
                        video_url, filepath, referer, cookies, progress_cb, dl_id=dl_id,
                    )
                    if success:
                        return True, "", size

    logger.info(f"[DL-XXXBP] Multi-segment failed: {error}")
    _cleanup_file(filepath)

    # ── روش 2: single-connection ──
    logger.info("[DL-XXXBP] Attempt 2: single-connection")
    success, error, size = await _download_single(
        video_url, filepath, referer, cookies, progress_cb, dl_id=dl_id,
    )
    if success:
        return True, "", size
    logger.info(f"[DL-XXXBP] Single failed: {error}")
    _cleanup_file(filepath)

    # ── روش 3: yt-dlp روی page URL (استخراج خودش از صفحه/API) ──
    logger.info("[DL-XXXBP] Attempt 3: yt-dlp on page URL")
    success, error, size = await _download_with_ytdlp(page_url, filepath, progress_cb)
    if success:
        return True, "", size
    _cleanup_file(filepath)
    return False, error or "All download methods failed", 0


# ─── Wrapper (سازگار با bot architecture) ─────────────────────────────────


async def download_xxxbp_direct(
    url: str,
    filepath: str,
    progress_cb: Optional[ProgressCallback] = None,
    video_url: str = "",
    quality: str = "high",
    dl_id: str = "",
) -> Tuple[bool, str, int]:
    """
    Wrapper برای سازگاری با bot architecture.

    Args:
        url: URL صفحه ویدیو
        filepath: مسیر فایل خروجی
        progress_cb: callback async
        video_url: اگه از قبل استخراج شده، مستقیم استفاده می‌شه
        quality: 'high' | 'low' | '<quality_key>' (مثل '720p')
        dl_id: شناسه برای cancellation
    """
    if not video_url:
        qualities, title, info = await extract_xxxbp_qualities(url, progress_cb)
        if not qualities:
            return False, title or "Extraction failed", 0
        selected = _select_quality(qualities, quality)
        video_url = selected["url"]
        cookies = info.get("cookies", {})  # معمولاً خالی
    else:
        # اگه video_url از قبل داریم، validation کنه و از همون استفاده کنه
        qualities, title, info = await extract_xxxbp_qualities(url, progress_cb)
        cookies = info.get("cookies", {}) if info else {}
        if qualities:
            # اگه URL موجود در لیست بود، از نسخه نرمال‌شده استفاده کن
            for q in qualities:
                if q.get("url") and video_url in q["url"]:
                    video_url = q["url"]
                    break
            else:
                video_url = qualities[0]["url"]
        # اگه qualities خالی بود ولی master بود، از master استفاده کنه
        if not video_url and info and info.get("master"):
            video_url = info["master"]

    return await download_xxxbp_video(
        url, video_url, filepath, progress_cb, cookies=cookies, dl_id=dl_id,
    )


def _select_quality(qualities: List[dict], quality: str) -> dict:
    """انتخاب کیفیت بر اساس پارامتر quality."""
    if not qualities:
        raise ValueError("Empty qualities list")

    # اگه quality_key خاص خواسته شده
    for q in qualities:
        if q.get("quality_key") == quality:
            return q

    # اگه 'high' یا 'best' — بالاترین کیفیت
    if quality in ("high", "best"):
        # اولویت به mp4 (method=api_mp4)، بعد hls
        mp4_sources = [q for q in qualities if q.get("method") == "api_mp4"]
        if mp4_sources:
            return mp4_sources[0]  # مرتب‌سازی نزولی هست
        return qualities[0]

    # اگه 'low' یا 'worst' — پایین‌ترین کیفیت
    if quality in ("low", "worst"):
        return qualities[-1]

    # default — بالاترین کیفیت
    return qualities[0]


# ─── Cancellation API ────────────────────────────────────────────────────


def cancel_download(dl_id: str) -> bool:
    """لغو یه download فعال."""
    state = _active_downloads.get(dl_id)
    if state:
        state.cancelled = True
        return True
    return False


def clear_download_state(dl_id: str) -> None:
    """پاکسازی state یه download بعد از اتمام."""
    _active_downloads.pop(dl_id, None)


# ─── CLI (برای تست مستقل) ─────────────────────────────────────────────────


async def _cli_main():
    import sys
    if len(sys.argv) < 2:
        print("Usage: python xxxbp_handler.py <url> [output.mp4] [quality]")
        print("  quality: high (default) | low | 720p | 480p | 360p | 240p | 144p")
        sys.exit(1)

    url = sys.argv[1]
    output = sys.argv[2] if len(sys.argv) > 2 else "/home/z/my-project/download/xxxbp_test.mp4"
    quality = sys.argv[3] if len(sys.argv) > 3 else "high"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    os.makedirs(os.path.dirname(output), exist_ok=True)

    async def progress_cb(msg: str) -> None:
        print(f"\r{msg}", end="", flush=True)

    print(f"URL: {url}")
    print(f"Output: {output}")
    print(f"Quality: {quality}")
    print()

    success, error, size = await download_xxxbp_direct(
        url, output, progress_cb=progress_cb, quality=quality,
    )

    print()  # newline after progress
    if success:
        print(f"✅ SUCCESS — saved {size} bytes ({_format_size(size)}) to {output}")
    else:
        print(f"❌ FAILED — {error}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(_cli_main())
