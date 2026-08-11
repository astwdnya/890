"""
shahvani_handler.py
───────────────────
استخراج و دانلود ویدیو از shahvani.com (forum)

روش کار (بر اساس تحلیل واقعی سایت — تأیید شده با تست):

  سایت یه فروم فارسیه. ویدیوها داخل پست‌ها embed می‌شن.

  ساختار:
    - صفحه‌ی تاپیک: /forum/topic/<slug>
    - ویدیو با HTML5 native <video> embed می‌شه
    - الگو:
        <video controls="">
            <source src="/media/video/<id>/<filename>.mp4" type="video/mp4">
        </video>
    - URL ویدیو نسبی هست (با /media/ شروع می‌شه) → باید absolute بشه

  CDN (shahvani.com خودش):
    - URL: https://shahvani.com/media/video/<id>/<filename>.mp4
    - بدون توکن، بدون expiry، بدون IP-lock
    - HEAD 403 می‌گیره (Cloudflare) ولی Range GET کار می‌کنه
    - بدون نیاز به Referer یا کوکی
    - Accept-Ranges: bytes (HTTP 206)
    - Content-Type: video/mp4
    - server: cloudflare

  نکات:
    - سایت تک‌کیفیتیه
    - کوکی shsess set می‌شه ولی برای دانلود لازم نیست
    - سایت با curl_cffi/impersonate=chrome کار می‌کنه
    - URL slug ممکنه فارسی (URL-encoded) باشه

استراتژی دانلود:
  1. GET parent page
  2. استخراج <source src="..."> از <video>
  3. اگه URL نسبی بود، absolute کن
  4. multi-segment download با N workers (پیش‌فرض 16) — به‌جای HEAD از probe با Range استفاده می‌کنیم
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
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional, Tuple
from urllib.parse import urlparse, urljoin, unquote

import aiofiles
import aiohttp
from aiohttp import ClientTimeout, CookieJar, TCPConnector

try:
    from curl_cffi.requests import AsyncSession as _CurlAsyncSession
    _HAS_CURL_CFFI = True
except ImportError:
    _CurlAsyncSession = None
    _HAS_CURL_CFFI = False

logger = logging.getLogger("ShahvaniHandler")

# ─── Constants ────────────────────────────────────────────────────────────

MAX_DOWNLOAD_SIZE = 50 * 1024 * 1024 * 1024  # 2 GB
MIN_VALID_VIDEO_SIZE = 100 * 1024  # 100 KB
PROGRESS_INTERVAL = 1.0
CHUNK_SIZE = 1024 * 1024  # 1 MB
MAX_RETRIES = 3
RETRY_DELAY = 2.0
MULTI_SEGMENT_MIN_SIZE = 5 * 1024 * 1024  # 5 MB

DEFAULT_SEGMENT_WORKERS = 16
SEGMENT_CHUNK_SIZE = 10 * 1024 * 1024  # 10 MB
CONNECTOR_LIMIT = 32
CONNECTOR_LIMIT_PER_HOST = 32

# دامنه‌های مجاز
_ALLOWED_HOSTS = frozenset({
    "shahvani.com",
    "www.shahvani.com",
    "m.shahvani.com",
})

# هدرهای پیش‌فرض
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_DEFAULT_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
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

# هدرهای مخصوص دانلود
_CDN_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "*/*",
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
    "Accept-Encoding": "identity",
    "Sec-Fetch-Dest": "video",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "same-origin",
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


def is_shahvani_url(url: str) -> bool:
    """بررسی اینکه URL مربوط به shahvani.com هست."""
    try:
        host = urlparse(url).hostname or ""
        return host in _ALLOWED_HOSTS or host.endswith(".shahvani.com")
    except Exception:
        return False


def extract_topic_slug(url: str) -> Optional[str]:
    """
    استخراج slug از URL.

    الگو: /forum/topic/<slug>
    """
    try:
        parsed = urlparse(url)
        path = parsed.path or ""
        m = re.search(r"/forum/topic/([^/]+)/?", path)
        if m:
            return unquote(m.group(1))
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
    url = unquote(url).replace("&amp;", "&")
    url = re.sub(r"[\\/]+$", "", url)
    url = url.rstrip("',\"")
    return url.strip()


def _absolute_url(url: str, base: str) -> str:
    """تبدیل URL نسبی به absolute با استفاده از base."""
    if not url:
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("//"):
        return "https:" + url
    # نسبی
    return urljoin(base, url)


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
    """حذف پسوندهای سایت."""
    title = re.sub(r"\s*[-|@]\s*shahvani(?:\.com)?\s*$", "", title, flags=re.IGNORECASE)
    return title.strip()


def _extract_title(html: str) -> str:
    """استخراج عنوان ویدیو."""
    # روش 1: <h1>
    m = re.search(r'<h1[^>]*>([^<]+)</h1>', html, re.IGNORECASE)
    if m:
        return _strip_site_suffix(m.group(1).strip())

    # روش 2: og:title
    m = re.search(
        r'(?:property|name)=["\']og:title["\']\s+content=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    )
    if m:
        return _strip_site_suffix(m.group(1).strip())

    # روش 3: <title>
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    if m:
        return _strip_site_suffix(m.group(1).strip()) or "Untitled"

    return "Untitled"


def _extract_thumbnail(html: str) -> str:
    """استخراج URL تصویر بندانگشتی."""
    m = re.search(
        r'(?:property|name)=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    # fallback: <video poster="...">
    m = re.search(r'<video[^>]+poster=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


def _extract_video_source(html: str, page_url: str) -> Optional[dict]:
    """
    استخراج URL ویدیو از <source> tag داخل <video>.

    الگوی HTML:
        <video controls="">
            <source src="/media/video/17268653/a51Xjv.mp4" type="video/mp4">
        </video>

    Returns:
        dict با کلیدهای url, method یا None
    """
    # روش 1: پیدا کردن <video> و سپس <source> داخلش
    video_match = re.search(
        r'<video\b[^>]*>(.*?)</video>',
        html, re.IGNORECASE | re.DOTALL,
    )
    video_block = video_match.group(1) if video_match else html

    # پیدا کردن <source> با src
    for sm in re.finditer(r'<source\b([^>]*)>', video_block, re.IGNORECASE):
        attrs_str = sm.group(1)
        # استخراج src
        src = None
        for pattern in [
            r'\bsrc\s*=\s*"([^"]*)"',
            r"\bsrc\s*=\s*'([^']*)'",
        ]:
            m = re.search(pattern, attrs_str, re.IGNORECASE)
            if m:
                src = m.group(1)
                break

        if not src:
            continue

        # نرمال‌سازی URL (نسبی → absolute)
        url = _clean_url(src)
        url = _absolute_url(url, page_url)

        # فقط MP4 یا URL های /media/video/ رو قبول کن
        if ".mp4" not in url.lower() and "/media/video/" not in url.lower():
            continue

        logger.info("Found video URL from <source>: %s", url[:100])
        return {
            "url": url,
            "method": "source_tag",
        }

    # روش 2: جستجوی مستقیم URL های /media/video/
    media_pattern = re.compile(
        r"""['"]([^'"]*?(?:/media/video/|shahvani\.com/media/)[^'"]*?\.mp4[^'"]*?)['"]""",
        re.IGNORECASE,
    )
    for m in media_pattern.finditer(html):
        url = _clean_url(m.group(1))
        url = _absolute_url(url, page_url)
        logger.info("Found video URL from regex: %s", url[:100])
        return {
            "url": url,
            "method": "media_regex",
        }

    # روش 3: هر .mp4 URL در صفحه
    mp4_pattern = re.compile(
        r"""['"]([^'"]+?\.mp4(?:\?[^'"]*)?)['"]""",
        re.IGNORECASE,
    )
    for m in mp4_pattern.finditer(html):
        url = _clean_url(m.group(1))
        url = _absolute_url(url, page_url)
        # فقط از host مجاز
        try:
            host = urlparse(url).hostname or ""
            if host in _ALLOWED_HOSTS or host.endswith(".shahvani.com"):
                logger.info("Found video URL from generic MP4 regex: %s", url[:100])
                return {
                    "url": url,
                    "method": "mp4_generic",
                }
        except Exception:
            continue

    return None


# ─── Fetch Page ───────────────────────────────────────────────────────────


async def _fetch_page(url: str, referer: Optional[str] = None) -> Tuple[Optional[str], str]:
    """GET یک صفحه‌ی HTML."""
    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer

    if _check_curl_cffi():
        try:
            async with _CurlAsyncSession() as session:
                resp = await session.get(
                    url, impersonate="chrome",
                    headers=headers, allow_redirects=True, timeout=30,
                )
                if resp.status_code == 200 and resp.text:
                    text = resp.text
                    if "<html" in text.lower() or "<video" in text.lower():
                        logger.info("Page fetched via curl_cffi: %s (size=%d)", url, len(text))
                        return text, ""
                else:
                    logger.warning("curl_cffi fetch %s: HTTP %s", url, resp.status_code)
        except Exception as e:
            logger.warning(f"curl_cffi fetch error: {e}")

    try:
        timeout = ClientTimeout(total=30, connect=10)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status == 200:
                    html = await resp.text(errors="replace")
                    return html, ""
                logger.warning("aiohttp fetch %s: HTTP %s", url, resp.status)
    except Exception as e:
        logger.warning(f"aiohttp fetch error: {e}")

    return None, "Failed to fetch page"


# ─── Extract Qualities (Public API) ───────────────────────────────────────


async def extract_shahvani_qualities(
    url: str,
    progress_cb: Optional[ProgressCallback] = None,
) -> Tuple[List[dict], str, dict]:
    """
    استخراج کیفیت‌های ویدیو از shahvani.com.

    Args:
        url: URL صفحه‌ی تاپیک
        progress_cb: callback async

    Returns:
        (sources, title, info)
    """
    if not is_shahvani_url(url):
        return [], "Invalid URL (host not allowed)", {}

    slug = extract_topic_slug(url)
    if not slug:
        return [], "Invalid URL (could not extract topic slug)", {}

    if progress_cb:
        await progress_cb("🔄 **دریافت اطلاعات صفحه...**")

    html, page_error = await _fetch_page(url)
    if not html:
        return [], f"خطا در دریافت صفحه: {page_error}", {}

    title = _extract_title(html)
    thumbnail = _extract_thumbnail(html)

    source_info = _extract_video_source(html, url)
    if not source_info:
        return [], "URL ویدیو در صفحه پیدا نشد", {}

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
        await progress_cb(f"✅ **پیدا شد:** {title[:50]}\n🎞 کیفیت‌ها: {labels}")

    return sources, title, {
        "thumbnail": thumbnail,
        "page_url": url,
        "topic_slug": slug,
        "fetch_method": "curl_cffi" if _check_curl_cffi() else "aiohttp",
        "source_type": "video/mp4",
    }


# ─── Download: Probe (Range GET) ──────────────────────────────────────────


async def _probe_size(url: str, referer: str) -> Tuple[int, str, str]:
    """
    Probe کردن سایز فایل با Range GET (نه HEAD — چون HEAD 403 می‌گیره).

    Returns:
        (content_length, accept_ranges, error)
    """
    headers = {
        **_CDN_HEADERS,
        "Referer": referer,
    }

    # روش 1: curl_cffi با Range bytes=0-0
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
                elif resp.status_code == 403:
                    return 0, "", "HTTP_403"
        except Exception as e:
            logger.warning(f"curl_cffi probe failed: {e}")

    # روش 2: aiohttp
    try:
        timeout = ClientTimeout(total=15, connect=8)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as s:
            async with s.get(url, headers={"Range": "bytes=0-0"}, allow_redirects=True) as r:
                if r.status in (200, 206):
                    cr = r.headers.get("Content-Range", "")
                    m = re.search(r"/(\d+)", cr)
                    if m:
                        return int(m.group(1)), "bytes", ""
                    cl = int(r.headers.get("Content-Length", 0))
                    if cl > 0:
                        return cl, "bytes" if r.status == 206 else "", ""
                elif r.status == 403:
                    return 0, "", "HTTP_403"
    except Exception as e:
        logger.warning(f"aiohttp probe failed: {e}")

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
    """دانلود چند تیکه‌ای."""
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
            "[DL-SHAHVANI] Work-queue: %d chunks, %d workers, total=%d",
            total_chunks, num_workers, content_length,
        )

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

        download_headers = {
            **_CDN_HEADERS,
            "Referer": referer,
        }

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
            logger.error("[DL-SHAHVANI] Work-queue error: %s", e, exc_info=True)
            _cleanup_file(filepath)
            return False, str(e)[:200], 0

        if _is_cancelled(dl_id):
            _cleanup_file(filepath)
            return False, "Cancelled by user", 0

        worker_failures = [r for r in results if r is not True]
        if worker_failures or failed_chunks:
            _cleanup_file(filepath)
            return False, f"Multi-segment failed: {len(failed_chunks)} chunks", 0

        file_size = os.path.getsize(filepath)
        if file_size < MIN_VALID_VIDEO_SIZE:
            _cleanup_file(filepath)
            return False, f"File too small ({file_size} bytes)", 0

        elapsed = time.time() - start_time
        avg_speed = file_size / elapsed / 1024 / 1024 if elapsed > 0 else 0
        logger.info(
            "[DL-SHAHVANI] Multi-segment DONE | size=%s | time=%.1fs | avg=%.1f MB/s",
            _format_size(file_size), elapsed, avg_speed,
        )
        return True, "", file_size

    except Exception as e:
        logger.error("[DL-SHAHVANI] Multi-segment error: %s", e, exc_info=True)
        _cleanup_file(filepath)
        return False, str(e)[:200], 0


async def _make_worker_task(worker_func, worker_id: int):
    try:
        return await worker_func(worker_id)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("[DL-SHAHVANI] Worker %d crashed: %s", worker_id, e)
        return False


def _make_curl_worker(direct_url, filepath, headers,
                       chunk_queue, downloaded_bytes, completed_chunks,
                       failed_chunks, progress_lock, file_write_lock,
                       first_chunk_started, update_progress, dl_id):
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
                        "[DL-SHAHVANI] W%d c%d attempt %d failed: %s",
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
                        "[DL-SHAHVANI] W%d c%d attempt %d failed: %s",
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


# ─── Download: Single connection ──────────────────────────────────────────


async def _download_single(url: str, filepath: str, referer: str,
                            progress_cb: ProgressCallback, dl_id: str = "") -> Tuple[bool, str, int]:
    """دانلود single connection."""
    headers = {
        **_CDN_HEADERS,
        "Referer": referer,
    }

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
                return True, "", size
        except Exception as e:
            logger.warning(f"[DL-SHAHVANI] Single error: {e}")
            _cleanup_file(filepath)

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


# ─── Download: yt-dlp ─────────────────────────────────────────────────────


async def _download_with_ytdlp(url: str, filepath: str, progress_cb: ProgressCallback) -> Tuple[bool, str, int]:
    if not shutil.which("yt-dlp"):
        return False, "yt-dlp not installed", 0
    await progress_cb("📥 **Fallback: yt-dlp...**")

    try:
        cmd = [
            "yt-dlp", "--no-warnings", "--progress", "--newline",
            "--no-check-certificates", "-f", "best",
            "-N", "32", "--concurrent-fragments", "32",
            "--retries", "10", "--fragment-retries", "10",
            "--max-filesize", str(MAX_DOWNLOAD_SIZE),
            "--add-header", f"User-Agent:{_USER_AGENT}",
            "--add-header", f"Referer:https://shahvani.com/",
            "-o", filepath,
        ]
        if _check_curl_cffi():
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
        return True, "", size
    except asyncio.CancelledError:
        _cleanup_file(filepath)
        raise
    except Exception as e:
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


async def download_shahvani_video(
    page_url: str,
    video_url: str,
    filepath: str,
    progress_cb: Optional[ProgressCallback] = None,
    dl_id: str = "",
) -> Tuple[bool, str, int]:
    """دانلود ویدیو از shahvani.com."""
    if not is_shahvani_url(page_url):
        return False, "URL host not allowed", 0
    if not video_url:
        return False, "Empty video URL", 0

    if progress_cb is None:
        async def _noop(msg: str) -> None: pass
        progress_cb = _noop

    if dl_id:
        _get_state(dl_id)

    referer = page_url

    # روش 1: multi-segment
    logger.info("[DL-SHAHVANI] Attempt 1: multi-segment (%d workers)", DEFAULT_SEGMENT_WORKERS)
    success, error, size = await _download_multi_segment(
        video_url, filepath, referer, progress_cb, dl_id=dl_id,
    )
    if success:
        return True, "", size

    if error == "Cancelled by user":
        return False, error, 0

    # اگه 403، re-fetch کن
    if error in ("HTTP_403", "Cannot determine file size"):
        logger.info("[DL-SHAHVANI] Refreshing page...")
        if progress_cb:
            await progress_cb("🔄 **Refreshing session...**")
        fresh_html, _ = await _fetch_page(page_url)
        if fresh_html:
            new_source = _extract_video_source(fresh_html, page_url)
            if new_source:
                video_url = new_source["url"]
                success, error, size = await _download_multi_segment(
                    video_url, filepath, referer, progress_cb, dl_id=dl_id,
                )
                if success:
                    return True, "", size

    logger.info(f"[DL-SHAHVANI] Multi-segment failed: {error}")
    _cleanup_file(filepath)

    # روش 2: single-connection
    logger.info("[DL-SHAHVANI] Attempt 2: single-connection")
    success, error, size = await _download_single(
        video_url, filepath, referer, progress_cb, dl_id=dl_id,
    )
    if success:
        return True, "", size
    _cleanup_file(filepath)

    # روش 3: yt-dlp
    logger.info("[DL-SHAHVANI] Attempt 3: yt-dlp on page URL")
    success, error, size = await _download_with_ytdlp(page_url, filepath, progress_cb)
    if success:
        return True, "", size
    _cleanup_file(filepath)
    return False, error or "All download methods failed", 0


async def download_shahvani_direct(
    url: str,
    filepath: str,
    progress_cb: Optional[ProgressCallback] = None,
    video_url: str = "",
    quality: str = "high",
    dl_id: str = "",
) -> Tuple[bool, str, int]:
    """Wrapper برای سازگاری با bot architecture."""
    if not video_url:
        qualities, title, info = await extract_shahvani_qualities(url, progress_cb)
        if not qualities:
            return False, title or "Extraction failed", 0
        video_url = qualities[0]["url"]
    else:
        qualities, title, info = await extract_shahvani_qualities(url, progress_cb)
        if qualities:
            for q in qualities:
                if q.get("url") and video_url in q["url"]:
                    video_url = q["url"]
                    break
            else:
                video_url = qualities[0]["url"]

    return await download_shahvani_video(
        url, video_url, filepath, progress_cb, dl_id=dl_id,
    )


# ─── Cancellation API ────────────────────────────────────────────────────


def cancel_download(dl_id: str) -> bool:
    state = _active_downloads.get(dl_id)
    if state:
        state.cancelled = True
        return True
    return False


def clear_download_state(dl_id: str) -> None:
    _active_downloads.pop(dl_id, None)


# ─── CLI ──────────────────────────────────────────────────────────────────


async def _cli_main():
    import sys
    if len(sys.argv) < 2:
        print("Usage: python shahvani_handler.py <url> [output.mp4] [quality]")
        sys.exit(1)

    url = sys.argv[1]
    output = sys.argv[2] if len(sys.argv) > 2 else "/tmp/shahvani_test.mp4"
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

    success, error, size = await download_shahvani_direct(
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
