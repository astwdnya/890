"""
rapelust_handler.py
───────────────────
استخراج و دانلود ویدیو از rapelust.com

روش کار (بر اساس تحلیل واقعی سایت — تأیید شده با تست):

  سایت از وردپرس + پلاگین FV Flowplayer استفاده می‌کنه.

  ساختار:
    - صفحه‌ی ویدیو: /video/<slug>/
    - ویدیو داخل <div class="flowplayer" data-item="<JSON>"> embed شده
    - data-item یک JSON HTML-encoded هست با ساختار:
        {
          "sources": [{"src": "<mp4_url>", "type": "video/mp4"}],
          "id": "<post_id>",
          "splash": "<thumbnail_url>"
        }
    - URL ویدیو از CDN rl.eros.icu (و subpaths) serve می‌شه
    - thumbnail از wp-content/uploads/ سایت اصلی

  CDN (rl.eros.icu):
    - Accept-Ranges: bytes
    - HTTP 206 برای Range requests
    - بدون توکن، بدون expiry، بدون IP-lock
    - Referer مهمه: درخواست با fake Origin 403 می‌گیره
      → باید Referer رو به https://rapelust.com/ set کنیم
    - بدون Referer هم کار می‌کنه ولی برای اطمینان set می‌کنیم

  الگوی URL های ویدیو:
    https://rl.eros.icu/<filename>.mp4
    https://rl.eros.icu/<subdir>/<filename>.mp4
    (subdir ممکنه rapesection، gen، و ... باشه)

  نکات:
    - سایت تک‌کیفیتیه (یه <source> per video)
    - کوکی‌های __ddg1_ و غیره set می‌شن ولی برای دانلود لازم نیستن
    - صفحه‌ها با Cloudflare محافظت می‌شن ولی curl_cffi با impersonate=chrome کار می‌کنه

استراتژی دانلود:
  1. GET /video/<slug>/ برای parent page
  2. استخراج data-item از <div class="flowplayer">
  3. parse JSON برای گرفتن sources[].src و splash
  4. multi-segment download با N workers (پیش‌فرض 16)
  5. fallback به single-connection
  6. fallback به yt-dlp روی page URL

وابستگی‌ها:
    pip install aiohttp aiofiles curl_cffi yt-dlp
"""

import asyncio
import json
import logging
import os
import re
import shutil
import html as html_mod
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional, Tuple
from urllib.parse import urlparse, urljoin, unquote

import aiofiles
import aiohttp
from aiohttp import ClientTimeout, CookieJar, TCPConnector

# در صورت نصب بودن curl_cffi
try:
    from curl_cffi.requests import AsyncSession as _CurlAsyncSession
    _HAS_CURL_CFFI = True
except ImportError:
    _CurlAsyncSession = None
    _HAS_CURL_CFFI = False

logger = logging.getLogger("RapeLustHandler")

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
    "rapelust.com",
    "www.rapelust.com",
    "m.rapelust.com",
})

# CDN host pattern
_CDN_HOSTS = frozenset({
    "rl.eros.icu",
    "rl2.eros.icu",
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

ProgressCallback = Callable[[str], Awaitable[None]]


# ─── State Manager ────────────────────────────────────────────────────────


@dataclass
class DownloadState:
    paused: bool = False
    cancelled: bool = False


_active_downloads: dict[str, DownloadState] = {}


def _get_state(dl_id: str) -> DownloadState:
    if not dl_id:
        return DownloadState()
    if dl_id not in _active_downloads:
        _active_downloads[dl_id] = DownloadState()
    return _active_downloads[dl_id]


def _is_cancelled(dl_id: str) -> bool:
    return _get_state(dl_id).cancelled


# ─── Utility ──────────────────────────────────────────────────────────────


def is_rapelust_url(url: str) -> bool:
    """بررسی اینکه URL مربوط به rapelust.com هست."""
    try:
        host = urlparse(url).hostname or ""
        return host in _ALLOWED_HOSTS or host.endswith(".rapelust.com")
    except Exception:
        return False


def extract_video_slug(url: str) -> Optional[str]:
    """
    استخراج slug از URL.

    الگو: /video/<slug>/
    """
    try:
        parsed = urlparse(url)
        path = parsed.path or ""
        m = re.search(r"/video/([^/]+)/?", path)
        if m:
            return m.group(1)
        return None
    except Exception:
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
    """نرمال‌سازی URL."""
    if not url:
        return ""
    # WordPress escapes slashes in JSON
    url = url.replace("\\/", "/")  # noqa: W605 (intentional escape)
    url = unquote(url).replace("&amp;", "&")
    url = re.sub(r"[\\/]+$", "", url)
    url = url.rstrip("',\"")
    return url.strip()


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
            f"📥 **Downloading...**
(هندلر)
`[{bar}]`\n"
            f"💾 {dl_mb:.1f}/{total_mb:.1f} MB  •  ⚡ {speed_mb:.1f} MB/s\n"
            f"📊 {pct:.1f}%  •  ⏱ ETA: {eta_m}:{eta_s:02d}"
        )
    return f"📥 **Downloading...**
(هندلر)
💾 {dl_mb:.1f} MB  •  ⚡ {speed / 1024 / 1024:.1f} MB/s"


def _check_curl_cffi() -> bool:
    return _HAS_CURL_CFFI


# ─── Page Metadata Extraction ─────────────────────────────────────────────


def _strip_site_suffix(title: str) -> str:
    """حذف پسوندهای سایت مثل ' - RapeLust'."""
    title = re.sub(r"\s*[-|@]\s*rapelust\s*$", "", title, flags=re.IGNORECASE)
    return title.strip()


def _extract_title(html: str) -> str:
    """استخراج عنوان ویدیو."""
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
        return _strip_site_suffix(m.group(1).strip())

    # روش 2: JSON-LD headline
    m = re.search(r'"headline"\s*:\s*"([^"]+)"', html)
    if m:
        return _strip_site_suffix(m.group(1).strip())

    # روش 3: <h1> یا <h2>
    for tag in ("h1", "h2"):
        m = re.search(rf'<{tag}[^>]*>([^<]+)</{tag}>', html, re.IGNORECASE)
        if m:
            t = m.group(1).strip()
            if t and "rape" in t.lower() or len(t) > 5:
                return _strip_site_suffix(t)

    # روش 4: <title>
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    if m:
        return _strip_site_suffix(m.group(1).strip()) or "Untitled"

    return "Untitled"


def _extract_description(html: str) -> str:
    """استخراج توضیحات ویدیو."""
    m = re.search(
        r'(?:property|name)=["\']og:description["\']\s+content=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    )
    if not m:
        m = re.search(
            r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']+)["\']',
            html, re.IGNORECASE,
        )
    if not m:
        # JSON-LD description
        m = re.search(r'"description"\s*:\s*"([^"]+)"', html)
    if m:
        return m.group(1).strip()
    return ""


def _extract_thumbnail(html: str) -> str:
    """استخراج URL تصویر بندانگشتی."""
    # روش 1: og:image
    m = re.search(
        r'(?:property|name)=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    # روش 2: splash از data-item (در extract_video_source هم استفاده می‌شه)
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
    # روش 3: thumbDuration div (الگوی رایج در سایت)
    # <div class="thumbDuration">5 min</div>
    m = re.search(r'class="thumbDuration"[^>]*>\s*(\d+)\s*min\s*</', html, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1)) * 60
        except ValueError:
            pass
    return None


def _parse_iso8601_duration(s: str) -> Optional[int]:
    """تبدیل PT1H30M45S → 5445 (ثانیه)."""
    if not s:
        return None
    total = 0
    for unit, mult in [("H", 3600), ("M", 60), ("S", 1)]:
        m = re.search(rf"(\d+){unit}", s)
        if m:
            total += int(m.group(1)) * mult
    return total if total > 0 else None


def _extract_video_source(html: str) -> Optional[dict]:
    """
    استخراج URL ویدیو از data-item attribute روی <div class="flowplayer">.

    الگوی HTML:
        <div id="..." data-item="{&quot;sources&quot;:[{&quot;src&quot;:&quot;https:\/\/rl.eros.icu\/...mp4&quot;,&quot;type&quot;:&quot;video\/mp4&quot;}],&quot;splash&quot;:&quot;...&quot;}" class="flowplayer ...">

    Returns:
        dict با کلیدهای url, splash, post_id, source_type, method یا None
    """
    # روش 1: پیدا کردن <div class="flowplayer" ... data-item="...">
    # الگوی دقیق: data-item رو بعد از class="flowplayer..." یا قبل از اون می‌گردیم
    flowplayer_pattern = re.compile(
        r'<div\b[^>]*\bclass="[^"]*flowplayer[^"]*"[^>]*\bdata-item="([^"]+)"[^>]*>',
        re.IGNORECASE,
    )
    m = flowplayer_pattern.search(html)
    if not m:
        # الگوی برعکس: data-item قبل از class
        flowplayer_pattern2 = re.compile(
            r'<div\b[^>]*\bdata-item="([^"]+)"[^>]*\bclass="[^"]*flowplayer[^"]*"[^>]*>',
            re.IGNORECASE,
        )
        m = flowplayer_pattern2.search(html)

    if not m:
        # fallback: هر div با data-item که شامل "sources" باشه
        any_data_item_pattern = re.compile(
            r'<div\b[^>]*\bdata-item="([^"]*sources[^"]+)"[^>]*>',
            re.IGNORECASE,
        )
        m = any_data_item_pattern.search(html)

    if not m:
        logger.error("No flowplayer div with data-item found")
        return None

    raw_data_item = m.group(1)
    # HTML-unescape: &quot; → ", &amp; → &, \/ → /
    decoded = html_mod.unescape(raw_data_item).replace("\\/", "/")

    # parse JSON
    try:
        data = json.loads(decoded)
    except json.JSONDecodeError as e:
        logger.warning(f"data-item JSON parse failed: {e}")
        # fallback: استخراج src با regex
        src_match = re.search(r'"src"\s*:\s*"([^"]+)"', decoded)
        if not src_match:
            return None
        url = _clean_url(src_match.group(1))
        splash_match = re.search(r'"splash"\s*:\s*"([^"]+)"', decoded)
        return {
            "url": url,
            "splash": _clean_url(splash_match.group(1)) if splash_match else "",
            "post_id": "",
            "source_type": "video/mp4",
            "method": "data_item_regex",
        }

    # استخراج از JSON
    sources = data.get("sources") or []
    if not sources or not isinstance(sources, list):
        logger.warning("data-item has no 'sources' array")
        return None

    # اولین source با type=video/mp4
    for source in sources:
        if not isinstance(source, dict):
            continue
        src = source.get("src", "")
        if not src:
            continue
        url = _clean_url(src)
        source_type = source.get("type", "video/mp4")
        splash = _clean_url(data.get("splash", "") or "")
        post_id = str(data.get("id", "") or "")

        logger.info("Found video URL from data-item: %s", url[:100])
        return {
            "url": url,
            "splash": splash,
            "post_id": post_id,
            "source_type": source_type,
            "method": "data_item_json",
        }

    return None


# ─── Fetch Page ───────────────────────────────────────────────────────────


async def _fetch_page(url: str, referer: Optional[str] = None) -> Tuple[Optional[str], str]:
    """
    GET یک صفحه‌ی HTML.

    Returns:
        (html, error)
    """
    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer
        headers["Sec-Fetch-Dest"] = "iframe"
        headers["Sec-Fetch-Mode"] = "navigate"
        headers["Sec-Fetch-Site"] = "same-origin"

    if _check_curl_cffi():
        try:
            async with _CurlAsyncSession() as session:
                resp = await session.get(
                    url, impersonate="chrome",
                    headers=headers, allow_redirects=True, timeout=30,
                )
                if resp.status_code == 200 and resp.text:
                    text = resp.text
                    # صحت‌سنجی: باید flowplayer یا data-item یا <title> داشته باشه
                    if ("flowplayer" in text.lower()
                            or "data-item" in text.lower()
                            or "<title" in text.lower()):
                        logger.info("Page fetched via curl_cffi: %s (size=%d)", url, len(text))
                        return text, ""
                    logger.warning("curl_cffi: 200 ولی محتوای مورد انتظار پیدا نشد")
                else:
                    logger.warning("curl_cffi fetch %s: HTTP %s", url, resp.status_code)
        except Exception as e:
            logger.warning(f"curl_cffi fetch error for {url}: {e}")

    # fallback به aiohttp
    try:
        timeout = ClientTimeout(total=30, connect=10)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status == 200:
                    html = await resp.text(errors="replace")
                    logger.info("Page fetched via aiohttp: %s (size=%d)", url, len(html))
                    return html, ""
                logger.warning("aiohttp fetch %s: HTTP %s", url, resp.status)
    except Exception as e:
        logger.warning(f"aiohttp fetch error for {url}: {e}")

    return None, "Failed to fetch page"


# ─── Extract Qualities (Public API) ───────────────────────────────────────


async def extract_rapelust_qualities(
    url: str,
    progress_cb: Optional[ProgressCallback] = None,
) -> Tuple[List[dict], str, dict]:
    """
    استخراج کیفیت‌های ویدیو از rapelust.com.

    Args:
        url: URL صفحه ویدیو (مثل https://rapelust.com/video/<slug>/)
        progress_cb: callback async برای گزارش پیشرفت

    Returns:
        (sources, title, info)
        sources: list of dicts (معمولاً فقط 1 آیتم)
        title: عنوان ویدیو
        info: dict با کلیدهای thumbnail/page_url/slug/description
    """
    if not is_rapelust_url(url):
        return [], "Invalid URL (host not allowed)", {}

    slug = extract_video_slug(url)
    if not slug:
        return [], "Invalid URL (could not extract video slug)", {}

    if progress_cb:
        await progress_cb("🔄 **دریافت اطلاعات صفحه...**")

    # مرحله 1: GET parent page
    html, page_error = await _fetch_page(url)
    if not html:
        return [], f"خطا در دریافت صفحه: {page_error}", {}

    # مرحله 2: استخراج metadata + video source
    title = _extract_title(html)
    description = _extract_description(html)
    duration = _extract_duration(html)
    thumbnail = _extract_thumbnail(html)

    source_info = _extract_video_source(html)
    if not source_info:
        logger.error("No video source found in page")
        return [], "URL ویدیو در صفحه پیدا نشد", {}

    # اگه thumbnail از og:image پیدا نشد، از splash استفاده کن
    if not thumbnail and source_info.get("splash"):
        thumbnail = source_info["splash"]

    # ساخت sources list (فقط 1 آیتم — سایت تک‌کیفیتیه)
    sources = [{
        "label": "📺 MP4 (default quality)",
        "url": source_info["url"],
        "height": 0,
        "quality_key": "default",
        "method": source_info["method"],
        "is_hd": False,
    }]

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
        "video_slug": slug,
        "post_id": source_info.get("post_id", ""),
        "description": description,
        "duration": duration,
        "fetch_method": "curl_cffi" if _check_curl_cffi() else "aiohttp",
        "source_type": source_info.get("source_type", "video/mp4"),
    }


# ─── Download: Probe (HEAD/Range) ─────────────────────────────────────────


async def _probe_size(url: str, referer: str) -> Tuple[int, str, str]:
    """
    Probe کردن سایز واقعی فایل و پشتیبانی از Range.

    Returns:
        (content_length, accept_ranges, error)
    """
    headers = {
        **_DEFAULT_HEADERS,
        "Referer": referer,
        "Origin": "https://rapelust.com",
        "Accept": "*/*",
        "Sec-Fetch-Dest": "video",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Site": "cross-site",
    }

    # روش 1: curl_cffi HEAD
    if _check_curl_cffi():
        try:
            async with _CurlAsyncSession() as session:
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
                elif resp.status_code == 403:
                    return 0, "", "HTTP_403"
        except Exception as e:
            logger.warning(f"curl_cffi HEAD failed: {e}")

    # روش 2: aiohttp HEAD
    try:
        timeout = ClientTimeout(total=15, connect=8)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as s:
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

    # روش 3: probe با Range bytes=0-0
    if _check_curl_cffi():
        try:
            async with _CurlAsyncSession() as session:
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
    progress_cb: ProgressCallback,
    dl_id: str = "",
    num_workers: int = DEFAULT_SEGMENT_WORKERS,
) -> Tuple[bool, str, int]:
    """دانلود چند تیکه‌ای با work-queue pattern."""
    try:
        content_length, accept_ranges, probe_err = await _probe_size(direct_url, referer)
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
            f"📥 **Downloading...**
(هندلر)
💾 Size: {total_mb:.1f} MB\n🔥 {num_workers} parallel workers"
        )

        # ساخت chunks
        chunks: List[Tuple[int, int, int]] = []
        offset = 0
        idx = 0
        while offset < content_length:
            end = min(offset + SEGMENT_CHUNK_SIZE - 1, content_length - 1)
            chunks.append((idx, offset, end))
            offset = end + 1
            idx += 1

        total_chunks = len(chunks)
        logger.info(
            "[DL-RAPELUST] Work-queue: %d chunks, %d workers, total=%d",
            total_chunks, num_workers, content_length,
        )

        # پیش‌تخصیص فایل
        try:
            with open(filepath, "wb") as f:
                f.truncate(content_length)
        except OSError as e:
            logger.warning("Could not pre-allocate file: %s", e)

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
                    f"📥 **Downloading...**
(هندلر)
`[{bar}]`\n"
                    f"💾 {dl_mb:.1f}/{total_mb_local:.1f} MB  •  ⚡ {speed_mb:.1f} MB/s\n"
                    f"📊 {pct:.1f}%  •  ⏱ ETA: {eta_m}:{eta_s:02d}\n"
                    f"📦 {completed_chunks[0]}/{total_chunks} chunks • 🔥 {num_workers}x"
                )
            except Exception:
                pass

        # هدرهای مخصوص دانلود — Referer و Origin مهمه
        download_headers = {
            **_DEFAULT_HEADERS,
            "Referer": referer,
            "Origin": "https://rapelust.com",
            "Accept": "*/*",
            "Sec-Fetch-Dest": "video",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "cross-site",
        }

        # انتخاب روش دانلود
        if _check_curl_cffi():
            worker = _make_curl_worker(
                direct_url, filepath,
                headers=download_headers,
                chunk_queue=chunk_queue, downloaded_bytes=downloaded_bytes,
                completed_chunks=completed_chunks, failed_chunks=failed_chunks,
                progress_lock=progress_lock, file_write_lock=file_write_lock,
                first_chunk_started=first_chunk_started, update_progress=_update_progress,
                dl_id=dl_id,
            )
        else:
            worker = _make_aiohttp_worker(
                direct_url, filepath,
                headers=download_headers,
                chunk_queue=chunk_queue, downloaded_bytes=downloaded_bytes,
                completed_chunks=completed_chunks, failed_chunks=failed_chunks,
                progress_lock=progress_lock, file_write_lock=file_write_lock,
                first_chunk_started=first_chunk_started, update_progress=_update_progress,
                dl_id=dl_id,
            )

        try:
            results = await asyncio.gather(
                *[_make_worker_task(worker, i) for i in range(num_workers)],
                return_exceptions=True,
            )
        except Exception as e:
            logger.error("[DL-RAPELUST] Work-queue error: %s", e, exc_info=True)
            _cleanup_file(filepath)
            return False, str(e)[:200], 0

        if _is_cancelled(dl_id):
            _cleanup_file(filepath)
            return False, "Cancelled by user", 0

        worker_failures = [r for r in results if r is not True]
        if worker_failures or failed_chunks:
            logger.warning(
                "[DL-RAPELUST] %d workers failed, %d chunks failed",
                len(worker_failures), len(failed_chunks),
            )
            _cleanup_file(filepath)
            return False, f"Multi-segment failed: {len(failed_chunks)} chunks", 0

        file_size = os.path.getsize(filepath)
        if file_size < MIN_VALID_VIDEO_SIZE:
            _cleanup_file(filepath)
            return False, f"File too small ({file_size} bytes)", 0

        elapsed = time.time() - start_time
        avg_speed = file_size / elapsed / 1024 / 1024 if elapsed > 0 else 0
        logger.info(
            "[DL-RAPELUST] Multi-segment DONE | size=%s | time=%.1fs | avg=%.1f MB/s",
            _format_size(file_size), elapsed, avg_speed,
        )
        return True, "", file_size

    except Exception as e:
        logger.error("[DL-RAPELUST] Multi-segment error: %s", e, exc_info=True)
        _cleanup_file(filepath)
        return False, str(e)[:200], 0


async def _make_worker_task(worker_func, worker_id: int):
    """Wrapper برای اجرای worker با exception handling."""
    try:
        return await worker_func(worker_id)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("[DL-RAPELUST] Worker %d crashed: %s", worker_id, e)
        return False


def _make_curl_worker(direct_url, filepath, headers,
                       chunk_queue, downloaded_bytes, completed_chunks,
                       failed_chunks, progress_lock, file_write_lock,
                       first_chunk_started, update_progress, dl_id):
    """ساخت worker function برای دانلود با curl_cffi."""

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
                        break

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(
                        "[DL-RAPELUST] W%d c%d attempt %d failed: %s",
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


def _make_aiohttp_worker(direct_url, filepath, headers,
                          chunk_queue, downloaded_bytes, completed_chunks,
                          failed_chunks, progress_lock, file_write_lock,
                          first_chunk_started, update_progress, dl_id):
    """ساخت worker function برای دانلود با aiohttp (fallback)."""
    shared_timeout = ClientTimeout(total=600, connect=30, sock_read=120)
    connector = TCPConnector(
        limit=CONNECTOR_LIMIT,
        limit_per_host=CONNECTOR_LIMIT_PER_HOST,
        keepalive_timeout=60,
        enable_cleanup_closed=True,
    )

    session_holder = {"session": None}

    async def _ensure_session():
        if session_holder["session"] is None:
            session_holder["session"] = aiohttp.ClientSession(
                timeout=shared_timeout, headers=headers, connector=connector,
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
                        "[DL-RAPELUST] W%d c%d attempt %d failed: %s",
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


# ─── Download: Single connection (fallback) ───────────────────────────────


async def _download_single(url: str, filepath: str, referer: str,
                            progress_cb: ProgressCallback, dl_id: str = "") -> Tuple[bool, str, int]:
    """دانلود با single connection (fallback)."""
    headers = {
        **_DEFAULT_HEADERS,
        "Referer": referer,
        "Origin": "https://rapelust.com",
        "Accept": "*/*",
        "Sec-Fetch-Dest": "video",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Site": "cross-site",
    }

    # روش 1: curl_cffi با streaming
    if _check_curl_cffi():
        try:
            t0 = time.time()
            async with _CurlAsyncSession() as session:
                resp = await session.get(
                    url, impersonate="chrome", headers=headers,
                    allow_redirects=True, timeout=3600,
                    stream=True,
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
                    "[DL-RAPELUST] Single (curl_cffi) DONE | size=%s | time=%.1fs",
                    _format_size(size), elapsed,
                )
                return True, "", size

        except Exception as e:
            logger.warning(f"[DL-RAPELUST] Single (curl_cffi) error: {e}")
            _cleanup_file(filepath)

    # روش 2: aiohttp با streaming
    error = ""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            timeout = ClientTimeout(total=3600, connect=30, sock_read=120)
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as s:
                async with s.get(url, allow_redirects=True) as resp:
                    if resp.status != 200:
                        error = f"HTTP {resp.status}"
                        if 400 <= resp.status < 500:
                            _cleanup_file(filepath)
                            return False, error, 0
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


# ─── Download: yt-dlp (fallback نهایی) ────────────────────────────────────


async def _download_with_ytdlp(url: str, filepath: str, progress_cb: ProgressCallback) -> Tuple[bool, str, int]:
    """fallback نهایی با yt-dlp روی page URL."""
    if not shutil.which("yt-dlp"):
        return False, "yt-dlp not installed", 0
    has_curl_cffi = _check_curl_cffi()
    await progress_cb("📥 **Fallback: yt-dlp...**")

    try:
        cmd = [
            "yt-dlp", "--no-warnings", "--progress", "--newline",
            "--no-check-certificates", "-f", "best",
            "-N", "32", "--concurrent-fragments", "32",
            "--retries", "10", "--fragment-retries", "10",
            "--buffer-size", "16K",
            "--max-filesize", str(MAX_DOWNLOAD_SIZE),
            "--add-header", f"User-Agent:{_USER_AGENT}",
            "--add-header", f"Referer:https://rapelust.com/",
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
        logger.info("[DL-RAPELUST] yt-dlp DONE | size=%s", _format_size(size))
        return True, "", size

    except asyncio.CancelledError:
        _cleanup_file(filepath)
        raise
    except Exception as e:
        logger.error("[DL-RAPELUST] yt-dlp error: %s", e, exc_info=True)
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
        f"📥 **Downloading...**
(هندلر)
`[{bar}]`\n"
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


async def download_rapelust_video(
    page_url: str,
    video_url: str,
    filepath: str,
    progress_cb: Optional[ProgressCallback] = None,
    dl_id: str = "",
) -> Tuple[bool, str, int]:
    """
    دانلود ویدیو از rapelust.com.

    Args:
        page_url: URL صفحه ویدیو (برای Referer)
        video_url: URL مستقیم MP4 (از extract_rapelust_qualities)
        filepath: مسیر فایل خروجی
        progress_cb: callback async برای گزارش پیشرفت
        dl_id: شناسه download برای cancellation support

    Returns:
        (success, error, size)

    نکته مهم:
        CDN rl.eros.icu به Origin حساسه — handler خودکار Referer و Origin رو set می‌کنه.
    """
    if not is_rapelust_url(page_url):
        return False, "URL host not allowed", 0
    if not video_url:
        return False, "Empty video URL", 0

    if progress_cb is None:
        async def _noop(msg: str) -> None: pass
        progress_cb = _noop

    if dl_id:
        _get_state(dl_id)

    referer = page_url

    # ── روش 1: multi-segment ──
    logger.info("[DL-RAPELUST] Attempt 1: multi-segment (%d workers)", DEFAULT_SEGMENT_WORKERS)
    success, error, size = await _download_multi_segment(
        video_url, filepath, referer, progress_cb, dl_id=dl_id,
    )
    if success:
        return True, "", size

    if error == "Cancelled by user":
        return False, error, 0

    # اگه 403، صفحه رو دوباره fetch کن (شاید URL عوض شده)
    if error == "HTTP_403":
        logger.info("[DL-RAPELUST] 403 — refreshing page...")
        if progress_cb:
            await progress_cb("🔄 **Refreshing session...**")
        fresh_html, _ = await _fetch_page(page_url)
        if fresh_html:
            new_source = _extract_video_source(fresh_html)
            if new_source:
                video_url = new_source["url"]
                logger.info("[DL-RAPELUST] Got fresh URL")
                success, error, size = await _download_multi_segment(
                    video_url, filepath, referer, progress_cb, dl_id=dl_id,
                )
                if success:
                    return True, "", size

    logger.info(f"[DL-RAPELUST] Multi-segment failed: {error}")
    _cleanup_file(filepath)

    # ── روش 2: single-connection ──
    logger.info("[DL-RAPELUST] Attempt 2: single-connection")
    success, error, size = await _download_single(
        video_url, filepath, referer, progress_cb, dl_id=dl_id,
    )
    if success:
        return True, "", size
    logger.info(f"[DL-RAPELUST] Single failed: {error}")
    _cleanup_file(filepath)

    # ── روش 3: yt-dlp روی page URL ──
    logger.info("[DL-RAPELUST] Attempt 3: yt-dlp on page URL")
    success, error, size = await _download_with_ytdlp(page_url, filepath, progress_cb)
    if success:
        return True, "", size
    _cleanup_file(filepath)
    return False, error or "All download methods failed", 0


# ─── Wrapper (سازگار با bot architecture) ─────────────────────────────────


async def download_rapelust_direct(
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
        video_url: اگه از قبل استخراج شده
        quality: 'high' | 'low' (چون سایت تک‌کیفیتیه، تأثیری نداره)
        dl_id: شناسه برای cancellation
    """
    if not video_url:
        qualities, title, info = await extract_rapelust_qualities(url, progress_cb)
        if not qualities:
            return False, title or "Extraction failed", 0
        video_url = qualities[0]["url"]
    else:
        qualities, title, info = await extract_rapelust_qualities(url, progress_cb)
        if qualities:
            for q in qualities:
                if q.get("url") and video_url in q["url"]:
                    video_url = q["url"]
                    break
            else:
                video_url = qualities[0]["url"]

    return await download_rapelust_video(
        url, video_url, filepath, progress_cb, dl_id=dl_id,
    )


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
        print("Usage: python rapelust_handler.py <url> [output.mp4] [quality]")
        sys.exit(1)

    url = sys.argv[1]
    output = sys.argv[2] if len(sys.argv) > 2 else "/home/z/my-project/download/rapelust_test.mp4"
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
    print()

    success, error, size = await download_rapelust_direct(
        url, output, progress_cb=progress_cb, quality=quality,
    )

    print()
    if success:
        print(f"✅ SUCCESS — saved {size} bytes ({_format_size(size)}) to {output}")
    else:
        print(f"❌ FAILED — {error}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(_cli_main())
