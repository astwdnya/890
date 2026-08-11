"""
jebacina_handler.py
───────────────────
استخراج و دانلود ویدیو از fa.jebacina.top

روش کار (بر اساس تحلیل واقعی سایت — تأیید شده با تست):

  ساختار دو-مرحله‌ای:
    1. صفحه‌ی اصلی ویدیو: /video/<id>/<slug>
       - شامل <iframe src="/embed/<id>"> برای پلیر
       - شامل <h1> برای عنوان، og:description، og:title
       - شامل لیست related videos
       - کوکی `av` set می‌شه ولی برای embed لازم نیست

    2. صفحه‌ی embed: /embed/<id>
       - بسیار ساده (~700 bytes)
       - فقط یه <video> با یه <source> tag
       - الگو:
           <video preload="none" controls poster="https://fa.jebacina.top/media/thumbs/<n>/v<id>.webp?<ts>">
             <source src="https://vs<N>.videosrc.net/s/<a>/<ab>/<hash>.mp4?md5=<token>&expires=<ts>" type="video/mp4"/>
           </video>

  CDN (videosrc.net):
    - چند سرور: vs4, vs8, vs10, ... (همگی *.videosrc.net)
    - URL شامل md5 token و expires timestamp
    - توکن 1 ساعته (60 دقیقه از زمان embed fetch)
    - IP-locked نیست — هر کسی با توکن می‌تونه دانلود کنه
    - Referer لازم نیست (تست شده)
    - Range requests پشتیبانی می‌شه (HTTP 206, Accept-Ranges: bytes)

  نکات:
    - سایت تک‌کیفیتیه (یه <source> per video)
    - سایت fa.jebacina.top نسخه فارسیه؛ نسخه‌های دیگه هم هستن (en, ru, ...)
    - کوکی set می‌شه ولی برای embed یا دانلود لازم نیست

استراتژی دانلود:
  1. GET /video/<id>/<slug> برای metadata (title, description)
  2. GET /embed/<id> برای video URL و poster
  3. multi-segment download با N workers (پیش‌فرض 16)
  4. fallback به single-connection
  5. fallback به yt-dlp روی page URL

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
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional, Tuple
from urllib.parse import urlparse, urljoin, unquote

import aiofiles
import aiohttp
from aiohttp import ClientTimeout, CookieJar, TCPConnector

# در صورت نصب بودن curl_cffi، ازش استفاده می‌کنیم
try:
    from curl_cffi.requests import AsyncSession as _CurlAsyncSession
    _HAS_CURL_CFFI = True
except ImportError:
    _CurlAsyncSession = None
    _HAS_CURL_CFFI = False

logger = logging.getLogger("JebacinaHandler")

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

# دامنه‌های مجاز برای صفحه‌ی ویدیو
_ALLOWED_HOSTS = frozenset({
    "jebacina.top",
    "fa.jebacina.top",
    "en.jebacina.top",
    "ru.jebacina.top",
    "www.jebacina.top",
    "www.fa.jebacina.top",
})

# CDN host pattern (vs4.videosrc.net, vs8.videosrc.net, ...)
_CDN_HOST_SUFFIX = ".videosrc.net"

# توکن expires تا 60 دقیقه؛ ما 50 دقیقه در نظر می‌گیریم برای margin
_TOKEN_REFRESH_THRESHOLD = 50 * 60  # seconds

# هدرهای پیش‌فرض
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_DEFAULT_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "fa-IR,fa;q=0.9,en-US;q=0.8,en;q=0.7",
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


def is_jebacina_url(url: str) -> bool:
    """بررسی اینکه URL مربوط به jebacina.top هست."""
    try:
        host = urlparse(url).hostname or ""
        return host in _ALLOWED_HOSTS or host.endswith(".jebacina.top")
    except Exception:
        return False


def extract_video_id(url: str) -> Optional[int]:
    """
    استخراج video_id از URL.

    الگوهای پشتیبانی شده:
        /video/<id>/<slug>
        /video/<id>
        /embed/<id>
    """
    try:
        parsed = urlparse(url)
        path = parsed.path or ""
        m = re.search(r"/(?:video|embed)/(\d+)", path)
        if m:
            return int(m.group(1))
        return None
    except (ValueError, TypeError):
        return None


def _build_embed_url(page_url: str, video_id: int) -> str:
    """ساخت URL صفحه embed از page_url و video_id."""
    parsed = urlparse(page_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    return f"{base}/embed/{video_id}"


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


def _parse_time_string(s: str) -> Optional[int]:
    """
    تبدیل "12:34" یا "1:23:45" به ثانیه.
    """
    if not s:
        return None
    parts = s.split(":")
    try:
        parts_int = [int(p) for p in parts]
    except ValueError:
        return None
    if len(parts_int) == 2:
        return parts_int[0] * 60 + parts_int[1]
    if len(parts_int) == 3:
        return parts_int[0] * 3600 + parts_int[1] * 60 + parts_int[2]
    return None


def _token_seconds_remaining(video_url: str) -> Optional[int]:
    """استخراج زمان باقی‌مانده تا انقضای توکن از URL."""
    m = re.search(r"[?&]expires=(\d+)", video_url)
    if not m:
        return None
    try:
        exp_ts = int(m.group(1))
        return exp_ts - int(time.time())
    except (ValueError, OSError):
        return None


# ─── Page Metadata Extraction ─────────────────────────────────────────────


def _strip_site_suffix(title: str) -> str:
    """حذف پسوندهای سایت."""
    # الگوهای رایج: " | Jebacina", " - Jebacina Top", " - سایت Jebacina"
    title = re.sub(r"\s*[-|@]\s*(?:jebacina(?:\.top)?)\s*$",
                   "", title, flags=re.IGNORECASE)
    return title.strip()


def _extract_title(html: str) -> str:
    """استخراج عنوان ویدیو از parent page."""
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

    # روش 2: <h1> (معمولاً عنوان ویدیو)
    m = re.search(r'<h1[^>]*>([^<]+)</h1>', html, re.IGNORECASE)
    if m:
        return _strip_site_suffix(m.group(1).strip())

    # روش 3: <title>
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
    if m:
        return m.group(1).strip()
    return ""


def _extract_video_source_from_embed(embed_html: str) -> Optional[dict]:
    """
    استخراج URL ویدیو از صفحه‌ی embed.

    الگوی مورد انتظار:
        <video ... poster="...">
          <source src="https://vs<N>.videosrc.net/.../HASH.mp4?md5=...&expires=..." type="video/mp4"/>
        </video>

    Returns:
        dict با کلیدهای url, poster, source_type یا None
    """
    # روش 1: پیدا کردن <source> با src
    # الگوی دقیق برای videosrc.net
    source_match = re.search(
        r'<source\b[^>]*\bsrc=["\']([^"\']+videosrc\.net[^"\']+\.mp4[^"\']*)["\'][^>]*>',
        embed_html, re.IGNORECASE,
    )
    if not source_match:
        # fallback: هر <source> با .mp4
        source_match = re.search(
            r'<source\b[^>]*\bsrc=["\']([^"\']+\.mp4[^"\']*)["\'][^>]*>',
            embed_html, re.IGNORECASE,
        )

    if not source_match:
        # روش 2: جستجوی مستقیم URL در HTML
        url_match = re.search(
            r'(https?://[^\s"\'<>)\]]*videosrc\.net[^\s"\'<>)\]]+\.mp4[^\s"\'<>)\]]*)',
            embed_html, re.IGNORECASE,
        )
        if not url_match:
            # fallback: هر URL با .mp4 و md5/expires
            url_match = re.search(
                r'(https?://[^\s"\'<>)\]]+?\.mp4\?[^\s"\'<>)\]]*(?:md5|expires)=[^\s"\'<>)\]]+)',
                embed_html, re.IGNORECASE,
            )
        if url_match:
            url = _clean_url(url_match.group(1))
            poster = _extract_poster_from_embed(embed_html)
            return {
                "url": url,
                "poster": poster,
                "method": "regex_url",
                "source_type": "video/mp4",
            }
        return None

    src = source_match.group(1)
    url = _clean_url(src)

    # استخراج poster از همون video tag
    poster = _extract_poster_from_embed(embed_html)

    # استخراج type attribute
    type_match = re.search(
        r'<source\b[^>]*\btype=["\']([^"\']+)["\']',
        source_match.group(0),
        re.IGNORECASE,
    )
    source_type = type_match.group(1) if type_match else "video/mp4"

    logger.info("Found video URL from embed <source>: %s", url[:100])
    return {
        "url": url,
        "poster": poster,
        "method": "embed_source_tag",
        "source_type": source_type,
    }


def _extract_poster_from_embed(embed_html: str) -> str:
    """استخراج URL poster از <video> tag در embed page."""
    m = re.search(r'<video[^>]+poster=["\']([^"\']+)["\']', embed_html, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


# ─── Fetch Pages ──────────────────────────────────────────────────────────


async def _fetch_page(url: str, referer: Optional[str] = None) -> Tuple[Optional[str], str]:
    """
    GET یک صفحه‌ی HTML.

    Args:
        url: URL برای fetch
        referer: اگه لازمه (مثلاً برای embed page)

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
                    verify=False,
                )
                if resp.status_code == 200 and resp.text:
                    text = resp.text
                    # صحت‌سنجی ساده
                    if "<html" in text.lower() or "<video" in text.lower() or "<iframe" in text.lower():
                        logger.info("Page fetched via curl_cffi: %s (size=%d)", url, len(text))
                        return text, ""
                    logger.warning("curl_cffi: 200 ولی محتوای HTML پیدا نشد")
                else:
                    logger.warning("curl_cffi fetch %s: HTTP %s", url, resp.status_code)
        except Exception as e:
            logger.warning(f"curl_cffi fetch error for {url}: {e}")

    # fallback به aiohttp
    try:
        timeout = ClientTimeout(total=30, connect=10)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url, allow_redirects=True, ssl=False) as resp:
                if resp.status == 200:
                    html = await resp.text(errors="replace")
                    logger.info("Page fetched via aiohttp: %s (size=%d)", url, len(html))
                    return html, ""
                logger.warning("aiohttp fetch %s: HTTP %s", url, resp.status)
    except Exception as e:
        logger.warning(f"aiohttp fetch error for {url}: {e}")

    return None, "Failed to fetch page"


# ─── Extract Qualities (Public API) ───────────────────────────────────────


async def extract_jebacina_qualities(
    url: str,
    progress_cb: Optional[ProgressCallback] = None,
) -> Tuple[List[dict], str, dict]:
    """
    استخراج کیفیت‌های ویدیو از fa.jebacina.top.

    Args:
        url: URL صفحه ویدیو (مثل https://fa.jebacina.top/video/<id>/<slug>)
        progress_cb: callback async برای گزارش پیشرفت

    Returns:
        (sources, title, info)
        sources: list of dicts (معمولاً فقط 1 آیتم)
        title: عنوان ویدیو
        info: dict با کلیدهای thumbnail/page_url/video_id/description/embed_url
    """
    if not is_jebacina_url(url):
        return [], "Invalid URL (host not allowed)", {}

    video_id = extract_video_id(url)
    if not video_id:
        return [], "Invalid URL (could not extract video_id)", {}

    embed_url = _build_embed_url(url, video_id)

    if progress_cb:
        await progress_cb("🔄 **دریافت اطلاعات صفحه...**")

    # مرحله 1: GET parent page برای metadata
    parent_html, parent_error = await _fetch_page(url)
    if parent_html:
        title = _extract_title(parent_html)
        description = _extract_description(parent_html)
    else:
        logger.warning("Parent page fetch failed (%s); continuing with embed", parent_error)
        title = f"jebacina video {video_id}"
        description = ""

    # مرحله 2: GET embed page برای video URL
    if progress_cb:
        await progress_cb("📡 **دریافت لینک دانلود...**")

    embed_html, embed_error = await _fetch_page(embed_url, referer=url)
    if not embed_html:
        return [], f"خطا در دریافت embed page: {embed_error}", {}

    # مرحله 3: استخراج video source از embed
    source_info = _extract_video_source_from_embed(embed_html)
    if not source_info:
        logger.error("No video source found in embed page")
        return [], "URL ویدیو در embed page پیدا نشد", {}

    video_url = source_info["url"]
    poster = source_info.get("poster", "")

    # ساخت sources list (فقط 1 آیتم — سایت تک‌کیفیتیه)
    sources = [{
        "label": "📺 MP4 (default quality)",
        "url": video_url,
        "height": 0,  # سایز واقعی بعد از probe مشخص می‌شه
        "quality_key": "default",
        "method": source_info["method"],
        "is_hd": False,  # نمی‌دونیم تا زمانی که probe کنیم
    }]

    # محاسبه زمان باقی‌مانده توکن
    token_remaining = _token_seconds_remaining(video_url)
    if token_remaining is not None:
        logger.info("Token expires in %d seconds", token_remaining)
        if token_remaining < 300:
            logger.warning("Token expires soon (%ds) — download may fail", token_remaining)

    if progress_cb:
        labels = ", ".join(s["label"] for s in sources)
        msg = f"✅ **پیدا شد:** {title[:50]}\n🎞 کیفیت‌ها: {labels}"
        if token_remaining is not None:
            mins, secs = divmod(token_remaining, 60)
            msg += f"\n⏳ توکن تا {mins}:{secs:02d} معتبره"
        await progress_cb(msg)

    return sources, title, {
        "thumbnail": poster,
        "page_url": url,
        "embed_url": embed_url,
        "video_id": video_id,
        "description": description,
        "duration": None,  # در parent page مشخص نیست
        "fetch_method": "curl_cffi" if _check_curl_cffi() else "aiohttp",
        "source_type": source_info.get("source_type", "video/mp4"),
        "token_remaining": token_remaining,
    }


# ─── Download: Probe (HEAD/Range) ─────────────────────────────────────────


async def _probe_size(url: str, referer: str) -> Tuple[int, str, str]:
    """
    Probe کردن سایز واقعی فایل و پشتیبانی از Range.

    Returns:
        (content_length, accept_ranges, error)
    """
    headers = {**_DEFAULT_HEADERS, "Referer": referer, "Accept": "*/*"}

    # روش 1: curl_cffi HEAD
    if _check_curl_cffi():
        try:
            async with _CurlAsyncSession() as session:
                resp = await session.head(
                    url, impersonate="chrome",
                    headers=headers, allow_redirects=True, timeout=15,
                    verify=False,
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
            async with s.head(url, allow_redirects=True, ssl=False) as r:
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
                    verify=False,
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
            "[DL-JEBACINA] Work-queue: %d chunks, %d workers, total=%d",
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

        # انتخاب روش دانلود
        if _check_curl_cffi():
            worker = _make_curl_worker(
                direct_url, filepath,
                headers={**_DEFAULT_HEADERS, "Referer": referer, "Accept": "*/*"},
                chunk_queue=chunk_queue, downloaded_bytes=downloaded_bytes,
                completed_chunks=completed_chunks, failed_chunks=failed_chunks,
                progress_lock=progress_lock, file_write_lock=file_write_lock,
                first_chunk_started=first_chunk_started, update_progress=_update_progress,
                dl_id=dl_id,
            )
        else:
            worker = _make_aiohttp_worker(
                direct_url, filepath,
                headers={**_DEFAULT_HEADERS, "Referer": referer, "Accept": "*/*"},
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
            logger.error("[DL-JEBACINA] Work-queue error: %s", e, exc_info=True)
            _cleanup_file(filepath)
            return False, str(e)[:200], 0

        if _is_cancelled(dl_id):
            _cleanup_file(filepath)
            return False, "Cancelled by user", 0

        worker_failures = [r for r in results if r is not True]
        if worker_failures or failed_chunks:
            logger.warning(
                "[DL-JEBACINA] %d workers failed, %d chunks failed",
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
            "[DL-JEBACINA] Multi-segment DONE | size=%s | time=%.1fs | avg=%.1f MB/s",
            _format_size(file_size), elapsed, avg_speed,
        )
        return True, "", file_size

    except Exception as e:
        logger.error("[DL-JEBACINA] Multi-segment error: %s", e, exc_info=True)
        _cleanup_file(filepath)
        return False, str(e)[:200], 0


async def _make_worker_task(worker_func, worker_id: int):
    """Wrapper برای اجرای worker با exception handling."""
    try:
        return await worker_func(worker_id)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("[DL-JEBACINA] Worker %d crashed: %s", worker_id, e)
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
                            verify=False,
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
                        "[DL-JEBACINA] W%d c%d attempt %d failed: %s",
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
                        ssl=False,
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
                        "[DL-JEBACINA] W%d c%d attempt %d failed: %s",
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
    headers = {**_DEFAULT_HEADERS, "Referer": referer, "Accept": "*/*"}

    # روش 1: curl_cffi با streaming
    if _check_curl_cffi():
        try:
            t0 = time.time()
            async with _CurlAsyncSession() as session:
                resp = await session.get(
                    url, impersonate="chrome", headers=headers,
                    allow_redirects=True, timeout=3600,
                    verify=False,
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
                    "[DL-JEBACINA] Single (curl_cffi) DONE | size=%s | time=%.1fs",
                    _format_size(size), elapsed,
                )
                return True, "", size

        except Exception as e:
            logger.warning(f"[DL-JEBACINA] Single (curl_cffi) error: {e}")
            _cleanup_file(filepath)

    # روش 2: aiohttp با streaming
    error = ""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            timeout = ClientTimeout(total=3600, connect=30, sock_read=120)
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as s:
                async with s.get(url, allow_redirects=True, ssl=False) as resp:
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


async def _download_with_ytdlp(url: str, filepath: str, progress_cb: ProgressCallback,
                                quality_key: str = "") -> Tuple[bool, str, int]:
    """fallback نهایی با yt-dlp روی page URL."""
    if not shutil.which("yt-dlp"):
        return False, "yt-dlp not installed", 0
    has_curl_cffi = _check_curl_cffi()
    await progress_cb("📥 **Fallback: yt-dlp...**")
    format_selector = "best"

    try:
        cmd = [
            "yt-dlp", "--no-warnings", "--progress", "--newline",
            "--no-check-certificates", "-f", format_selector,
            "-N", "32", "--concurrent-fragments", "32",
            "--retries", "10", "--fragment-retries", "10",
            "--buffer-size", "16K",
            "--max-filesize", str(MAX_DOWNLOAD_SIZE),
            "--add-header", f"User-Agent:{_USER_AGENT}",
            "--add-header", f"Referer:https://fa.jebacina.top/",
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
                verify=False,
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
        logger.info("[DL-JEBACINA] yt-dlp DONE | size=%s", _format_size(size))
        return True, "", size

    except asyncio.CancelledError:
        _cleanup_file(filepath)
        raise
    except Exception as e:
        logger.error("[DL-JEBACINA] yt-dlp error: %s", e, exc_info=True)
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


async def download_jebacina_video(
    page_url: str,
    video_url: str,
    filepath: str,
    progress_cb: Optional[ProgressCallback] = None,
    dl_id: str = "",
) -> Tuple[bool, str, int]:
    """
    دانلود ویدیو از fa.jebacina.top.

    Args:
        page_url: URL صفحه ویدیو (برای Referer)
        video_url: URL مستقیم MP4 (از extract_jebacina_qualities)
        filepath: مسیر فایل خروجی
        progress_cb: callback async برای گزارش پیشرفت
        dl_id: شناسه download برای cancellation support

    Returns:
        (success, error, size)

    نکته مهم:
        توکن URL فقط 1 ساعت معتبره. اگه بین extract و download زیاد فاصله بیفته،
        handler خودش embed رو دوباره fetch می‌کنه تا توکن تازه بگیره.
    """
    if not is_jebacina_url(page_url):
        return False, "URL host not allowed", 0
    if not video_url:
        return False, "Empty video URL", 0

    if progress_cb is None:
        async def _noop(msg: str) -> None: pass
        progress_cb = _noop

    if dl_id:
        _get_state(dl_id)

    referer = page_url

    # بررسی زمان باقی‌مانده توکن
    token_remaining = _token_seconds_remaining(video_url)
    if token_remaining is not None and token_remaining < 60:
        logger.warning("Token about to expire (%ds) — refreshing", token_remaining)
        if progress_cb:
            await progress_cb("🔄 **Refreshing token...**")
        video_id = extract_video_id(page_url)
        if video_id:
            embed_url = _build_embed_url(page_url, video_id)
            embed_html, _ = await _fetch_page(embed_url, referer=page_url)
            if embed_html:
                new_source = _extract_video_source_from_embed(embed_html)
                if new_source:
                    video_url = new_source["url"]
                    logger.info("[DL-JEBACINA] Got fresh URL with new token")

    # ── روش 1: multi-segment ──
    logger.info("[DL-JEBACINA] Attempt 1: multi-segment (%d workers)", DEFAULT_SEGMENT_WORKERS)
    success, error, size = await _download_multi_segment(
        video_url, filepath, referer, progress_cb, dl_id=dl_id,
    )
    if success:
        return True, "", size

    if error == "Cancelled by user":
        return False, error, 0

    # اگه 403 یا خطای توکن، embed رو دوباره fetch کن
    if error in ("HTTP_403", "Cannot determine file size"):
        logger.info("[DL-JEBACINA] Token may have expired — refreshing embed...")
        if progress_cb:
            await progress_cb("🔄 **Refreshing session...**")
        video_id = extract_video_id(page_url)
        if video_id:
            embed_url = _build_embed_url(page_url, video_id)
            embed_html, _ = await _fetch_page(embed_url, referer=page_url)
            if embed_html:
                new_source = _extract_video_source_from_embed(embed_html)
                if new_source:
                    video_url = new_source["url"]
                    logger.info("[DL-JEBACINA] Got fresh URL")
                    success, error, size = await _download_multi_segment(
                        video_url, filepath, referer, progress_cb, dl_id=dl_id,
                    )
                    if success:
                        return True, "", size

    logger.info(f"[DL-JEBACINA] Multi-segment failed: {error}")
    _cleanup_file(filepath)

    # ── روش 2: single-connection ──
    logger.info("[DL-JEBACINA] Attempt 2: single-connection")
    success, error, size = await _download_single(
        video_url, filepath, referer, progress_cb, dl_id=dl_id,
    )
    if success:
        return True, "", size
    logger.info(f"[DL-JEBACINA] Single failed: {error}")
    _cleanup_file(filepath)

    # ── روش 3: yt-dlp روی page URL ──
    logger.info("[DL-JEBACINA] Attempt 3: yt-dlp on page URL")
    success, error, size = await _download_with_ytdlp(page_url, filepath, progress_cb)
    if success:
        return True, "", size
    _cleanup_file(filepath)
    return False, error or "All download methods failed", 0


# ─── Wrapper (سازگار با bot architecture) ─────────────────────────────────


async def download_jebacina_direct(
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
        qualities, title, info = await extract_jebacina_qualities(url, progress_cb)
        if not qualities:
            return False, title or "Extraction failed", 0
        video_url = qualities[0]["url"]
    else:
        # اگه video_url داریم، validation کنه
        qualities, title, info = await extract_jebacina_qualities(url, progress_cb)
        if qualities:
            for q in qualities:
                if q.get("url") and video_url in q["url"]:
                    video_url = q["url"]
                    break
            else:
                video_url = qualities[0]["url"]

    return await download_jebacina_video(
        url, video_url, filepath, progress_cb, dl_id=dl_id,
    )


def _select_quality(qualities: List[dict], quality: str) -> dict:
    """انتخاب کیفیت (معمولاً فقط 1 آیتم داریم)."""
    if not qualities:
        raise ValueError("Empty qualities list")
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
        print("Usage: python jebacina_handler.py <url> [output.mp4] [quality]")
        sys.exit(1)

    url = sys.argv[1]
    output = sys.argv[2] if len(sys.argv) > 2 else "/home/z/my-project/download/jebacina_test.mp4"
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

    success, error, size = await download_jebacina_direct(
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
