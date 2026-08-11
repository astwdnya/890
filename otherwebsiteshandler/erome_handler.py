"""
erome_handler.py
────────────────
استخراج و دانلود ویدیو و عکس از erome.com

ساختار:
  - erome.com آلبوم‌ها می‌تونن شامل ویدیو و عکس باشن
  - ویدیو: https://v{N}.erome.com/{ID}/{album_id}/{hash}_720p.mp4
  - عکس: https://s{N}.erome.com/{ID}/{album_id}/{hash}.jpg
  - بدون Cloudflare
  - Accept-Ranges: bytes ✓ (برای ویدیو)
  - Player: Video.js

سه دکمه شیشه‌ای:
  1. عکس و ویدیو (همه)
  2. ویدیو ها
  3. عکس ها

API:
  extract_erome_media(url) → {videos: [...], photos: [...], title, album_id}
  download_erome_video(url, filepath, progress_cb)
  download_erome_photo(url, filepath)
"""

import asyncio
import logging
import os
import re
import time
from typing import Awaitable, Callable, List, Optional, Tuple
from urllib.parse import urlparse, urljoin, unquote

import aiofiles
import aiohttp
from aiohttp import ClientTimeout, CookieJar, TCPConnector

logger = logging.getLogger("EromeHandler")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_DEFAULT_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}

MAX_DOWNLOAD_SIZE = 50 * 1024 * 1024 * 1024
MIN_VALID_VIDEO_SIZE = 100 * 1024
PROGRESS_INTERVAL = 1.0
CHUNK_SIZE = 1024 * 1024

MULTI_SEGMENT_WORKERS = 32
MULTI_SEGMENT_CHUNK_SIZE = 10 * 1024 * 1024
MULTI_SEGMENT_MIN_SIZE = 5 * 1024 * 1024
MAX_RETRIES = 3
RETRY_DELAY = 2.0

_ALLOWED_HOSTS = frozenset({
    "erome.com",
    "www.erome.com",
})

ProgressCallback = Callable[[str], Awaitable[None]]


# ─── Utility ────────────────────────────────────────────────────────────────


def is_erome_url(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
        return host in _ALLOWED_HOSTS or host.endswith(".erome.com")
    except Exception:
        return False


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


def _format_progress(downloaded, content_length, start_time, now):
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


# ─── Fetch Page ───────────────────────────────────────────────────────────


async def _fetch_page(url) -> str:
    """fetch صفحه با aiohttp."""
    try:
        timeout = ClientTimeout(total=30, connect=10)
        jar = CookieJar(unsafe=True)
        async with aiohttp.ClientSession(timeout=timeout, headers=_DEFAULT_HEADERS, cookie_jar=jar) as session:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status == 200:
                    html = await resp.text(errors="replace")
                    logger.info("Page fetched: %s (size=%d)", url[:80], len(html))
                    return html
                logger.warning("fetch: HTTP %d", resp.status)
    except Exception as e:
        logger.warning(f"fetch error: {e}")
    return ""


# ─── Extraction ────────────────────────────────────────────────────────────


def _extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    if m:
        title = m.group(1).strip()
        title = re.sub(r"\s*[-|@]\s*(?:EroMe|erome\.com)\s*$", "", title, flags=re.IGNORECASE)
        return title or "Untitled"
    # og:title
    m = re.search(r'(?:property|name)=["\']og:title["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return "Untitled"


def _extract_album_id(url: str) -> str:
    """استخراج album ID از URL.
    مثال: https://www.erome.com/a/vpIzeHdM → vpIzeHdM
    """
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    parts = path.split("/")
    # /a/vpIzeHdM or /vpIzeHdM
    for part in parts:
        if part and part != "a" and len(part) >= 6:
            return part
    return ""


def _extract_videos(html: str) -> List[dict]:
    """استخراج ویدیوها از HTML.

    ساختار:
      <source src="https://v89.erome.com/8781/vpIzeHdM/HASH_720p.mp4" type='video/mp4' label='HD' res='720'>

    Returns:
        list of {url, label, height, quality_key, poster}
    """
    videos = []
    seen_urls = set()

    # Method 1: <source> tags
    for m in re.finditer(
        r'<source\s+src=["\']([^"\']+\.mp4[^"\']*)["\'][^>]*?(?:label=["\']([^"\']*)["\'])?[^>]*?(?:res=["\']?(\d+))?[^>]*>',
        html, re.IGNORECASE,
    ):
        url = m.group(1).strip()
        label = m.group(2) or "HD"
        res = m.group(3) or "720"

        if url in seen_urls:
            continue
        seen_urls.add(url)

        try:
            height = int(res)
        except ValueError:
            height = 720

        # Extract poster from parent video tag
        # Find the video tag that contains this source
        source_pos = m.start()
        # Search backwards for poster
        before = html[max(0, source_pos - 500):source_pos]
        poster_match = re.search(r'poster=["\']([^"\']+)["\']', before, re.IGNORECASE)
        if not poster_match:
            poster_match = re.search(r'data-poster=["\']([^"\']+)["\']', before, re.IGNORECASE)
        poster = poster_match.group(1) if poster_match else ""

        videos.append({
            "url": url,
            "label": f"📺 MP4 {height}p ({label})",
            "height": height,
            "quality_key": f"{height}p",
            "poster": poster,
        })
        logger.info("Found video: %s (%s)", height, url[:100])

    return videos


def _extract_photos(html: str, album_id: str = "") -> List[dict]:
    """استخراج عکس‌ها از HTML.

    ساختار:
      <img data-src="https://s89.erome.com/8781/vpIzeHdM/HASH.jpg?v=..." />

    Returns:
        list of {url, filename}
    """
    photos = []
    seen_urls = set()

    # Method 1: data-src attributes (lazy loaded images)
    for m in re.finditer(r'data-src=["\']([^"\']*erome\.com[^"\']+\.jpg[^"\']*)["\']', html, re.IGNORECASE):
        url = m.group(1).strip()
        # Skip thumbnails
        if "/thumbs/" in url:
            continue
        # Skip video posters (they're video thumbnails)
        # We'll check if the hash matches a video hash

        # Clean URL
        base_url = url.split("?")[0]
        if base_url in seen_urls:
            continue
        seen_urls.add(base_url)

        # Extract filename
        filename = base_url.rsplit("/", 1)[-1] if "/" in base_url else base_url

        photos.append({
            "url": url,
            "filename": filename,
            "base_url": base_url,
        })

    # Method 2: src attributes (non-lazy loaded)
    for m in re.finditer(r'src=["\']([^"\']*erome\.com[^"\']+\.jpg[^"\']*)["\']', html, re.IGNORECASE):
        url = m.group(1).strip()
        if "/thumbs/" in url:
            continue
        base_url = url.split("?")[0]
        if base_url in seen_urls:
            continue
        seen_urls.add(base_url)
        filename = base_url.rsplit("/", 1)[-1] if "/" in base_url else base_url
        photos.append({
            "url": url,
            "filename": filename,
            "base_url": base_url,
        })

    # Deduplicate by base_url
    unique_photos = []
    seen_base = set()
    for p in photos:
        if p["base_url"] not in seen_base:
            seen_base.add(p["base_url"])
            unique_photos.append(p)

    logger.info("Found %d photos", len(unique_photos))
    return unique_photos


def _filter_video_posters(photos: List[dict], videos: List[dict]) -> List[dict]:
    """حذف عکس‌هایی که poster ویدیو هستن (نه عکس مستقل)."""
    video_hashes = set()
    for v in videos:
        # Extract hash from video URL: .../HASH_720p.mp4
        m = re.search(r'/([a-zA-Z0-9]+)_\d+p\.mp4', v["url"])
        if m:
            video_hashes.add(m.group(1))

    filtered = []
    for p in photos:
        # Extract hash from photo URL: .../HASH.jpg
        m = re.search(r'/([a-zA-Z0-9]+)\.jpg', p["base_url"])
        if m:
            photo_hash = m.group(1)
            if photo_hash in video_hashes:
                logger.debug("Skipping video poster: %s", photo_hash)
                continue
        filtered.append(p)

    return filtered


# ─── Main API: extract media ──────────────────────────────────────────────


async def extract_erome_media(url, progress_cb=None):
    """استخراج ویدیو و عکس از آلبوم erome.

    Returns:
        {videos, photos, title, album_id, has_videos, has_photos}
    """
    if not is_erome_url(url):
        return {"error": "Invalid URL — host not allowed"}

    if progress_cb:
        await progress_cb("🔄 **دریافت اطلاعات صفحه...**")

    html = await _fetch_page(url)
    if not html:
        return {"error": "خطا در دریافت صفحه"}

    title = _extract_title(html)
    album_id = _extract_album_id(url)

    videos = _extract_videos(html)
    photos = _extract_photos(html, album_id)

    # Remove video posters from photos
    photos = _filter_video_posters(photos, videos)

    has_videos = len(videos) > 0
    has_photos = len(photos) > 0

    if progress_cb:
        msg_parts = []
        if has_videos:
            msg_parts.append(f"🎬 {len(videos)} ویدیو")
        if has_photos:
            msg_parts.append(f"🖼 {len(photos)} عکس")
        await progress_cb(f"✅ **پیدا شد:** {title[:50]}\n🎞 {', '.join(msg_parts)}")

    return {
        "videos": videos,
        "photos": photos,
        "title": title,
        "album_id": album_id,
        "page_url": url,
        "has_videos": has_videos,
        "has_photos": has_photos,
    }


# ─── Download: Video (multi-segment) ─────────────────────────────────────


active_downloads: dict = {}


async def download_erome_video(video_url, filepath, progress_cb=None, dl_id="", referer="https://www.erome.com/"):
    """دانلود یه ویدیو از erome با multi-segment.

    نکته: erome CDN نیاز به Referer header داره (بدون Referer = 403).
    """
    if progress_cb is None:
        async def _noop(msg): pass
        progress_cb = _noop
    if dl_id and dl_id not in active_downloads:
        active_downloads[dl_id] = {"paused": False, "cancelled": False}

    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Referer": referer,
        "Origin": "https://www.erome.com",
        "Sec-Fetch-Dest": "video",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Site": "cross-site",
    }

    # Get content length
    content_length = 0
    accept_ranges = ""
    try:
        timeout = ClientTimeout(total=10, connect=5)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as s:
            async with s.head(video_url, allow_redirects=True) as r:
                if r.status in (200, 206):
                    content_length = int(r.headers.get("Content-Length", 0))
                    accept_ranges = r.headers.get("Accept-Ranges", "").lower()
    except Exception as e:
        logger.warning(f"HEAD failed: {e}")

    # Probe with Range
    if content_length == 0:
        try:
            probe_headers = dict(headers)
            probe_headers["Range"] = "bytes=0-0"
            timeout = ClientTimeout(total=10, connect=5)
            async with aiohttp.ClientSession(timeout=timeout, headers=probe_headers) as s:
                async with s.get(video_url, allow_redirects=True) as r:
                    if r.status in (200, 206):
                        if r.status == 206:
                            accept_ranges = "bytes"
                            cr = r.headers.get("Content-Range", "")
                            m = re.search(r"/(\d+)", cr)
                            if m:
                                content_length = int(m.group(1))
                        else:
                            content_length = int(r.headers.get("Content-Length", 0))
        except Exception as e:
            logger.warning(f"Probe failed: {e}")

    if content_length == 0:
        return False, "Cannot determine file size", 0
    if content_length > MAX_DOWNLOAD_SIZE:
        return False, f"File too large: {_format_size(content_length)}", 0
    if accept_ranges != "bytes" or content_length < MULTI_SEGMENT_MIN_SIZE:
        # Use single connection for small files
        return await _download_single(video_url, filepath, headers, progress_cb, dl_id)

    total_mb = content_length / 1024 / 1024
    await progress_cb(f"📥 **Downloading...**
(هندلر)
💾 Size: {total_mb:.1f} MB\n🔥 {MULTI_SEGMENT_WORKERS} workers")

    CHUNK_SIZE_BYTES = MULTI_SEGMENT_CHUNK_SIZE
    chunks = []
    offset = 0
    while offset < content_length:
        end = min(offset + CHUNK_SIZE_BYTES - 1, content_length - 1)
        chunks.append((offset, end))
        offset = end + 1

    total_chunks = len(chunks)
    logger.info(f"[DL-ER] Work-queue: {total_chunks} chunks, total={content_length}")

    try:
        async with aiofiles.open(filepath, "wb") as f:
            await f.truncate(content_length)
    except Exception as e:
        logger.warning(f"Could not pre-allocate: {e}")

    chunk_queue = asyncio.Queue()
    for c in chunks:
        await chunk_queue.put(c)

    downloaded_bytes = [0] * total_chunks
    completed_chunks = [0]
    failed_chunks = []
    start_time = time.time()
    last_update = [0.0]
    progress_lock = asyncio.Lock()
    file_write_lock = asyncio.Lock()

    async def _update_progress(force=False):
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
                f"📦 {completed_chunks[0]}/{total_chunks} chunks • 🔥 {MULTI_SEGMENT_WORKERS}x"
            )
        except Exception:
            pass

    shared_timeout = ClientTimeout(total=600, connect=30, sock_read=120)
    connector = TCPConnector(limit=50, limit_per_host=50, keepalive_timeout=60, enable_cleanup_closed=True)
    shared_session = aiohttp.ClientSession(timeout=shared_timeout, headers=headers, connector=connector)
    shared_file = await aiofiles.open(filepath, "r+b")

    async def _download_worker(worker_id):
        while True:
            if active_downloads.get(dl_id, {}).get("cancelled"):
                return False
            try:
                byte_start, byte_end = chunk_queue.get_nowait()
            except asyncio.QueueEmpty:
                return True
            chunk_size = byte_end - byte_start + 1
            c_idx = (byte_start // CHUNK_SIZE_BYTES)
            for attempt in range(MAX_RETRIES):
                if active_downloads.get(dl_id, {}).get("cancelled"):
                    return False
                try:
                    async with shared_session.get(video_url, headers={"Range": f"bytes={byte_start}-{byte_end}"}, allow_redirects=True) as resp:
                        if resp.status not in (200, 206):
                            raise Exception(f"HTTP {resp.status}")
                        chunk_data = bytearray()
                        async for piece in resp.content.iter_chunked(CHUNK_SIZE):
                            if not piece:
                                continue
                            if active_downloads.get(dl_id, {}).get("cancelled"):
                                return False
                            chunk_data.extend(piece)
                        if len(chunk_data) != chunk_size:
                            raise Exception(f"Size mismatch: {chunk_size} vs {len(chunk_data)}")
                        async with file_write_lock:
                            await shared_file.seek(byte_start)
                            await shared_file.write(bytes(chunk_data))
                        downloaded_bytes[c_idx] = chunk_size
                        async with progress_lock:
                            completed_chunks[0] += 1
                            await _update_progress()
                        break
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(f"[DL-ER] W{worker_id} c{c_idx} attempt {attempt+1} failed: {e}")
                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(RETRY_DELAY * (attempt + 1))
                    else:
                        failed_chunks.append((c_idx, str(e)[:100]))
                        return False
            chunk_queue.task_done()
        return True

    try:
        results = await asyncio.gather(*[_download_worker(i) for i in range(MULTI_SEGMENT_WORKERS)], return_exceptions=True)
        try: await shared_file.close()
        except: pass
        try: await shared_session.close()
        except: pass

        if active_downloads.get(dl_id, {}).get("cancelled"):
            _cleanup_file(filepath)
            return False, "Cancelled by user", 0

        real_failures = []
        for i, r in enumerate(results):
            if r is True: continue
            if isinstance(r, Exception): real_failures.append(f"worker{i}: {r}")
            elif r is False: real_failures.append(f"worker{i}: False")
        if real_failures or failed_chunks:
            _cleanup_file(filepath)
            return False, f"Multi-segment failed: {len(real_failures)+len(failed_chunks)} chunks", 0

    except Exception as e:
        logger.error(f"[DL-ER] error: {e}", exc_info=True)
        try: await shared_file.close()
        except: pass
        try: await shared_session.close()
        except: pass
        _cleanup_file(filepath)
        return False, str(e)[:200], 0

    file_size = os.path.getsize(filepath)
    if file_size < MIN_VALID_VIDEO_SIZE:
        _cleanup_file(filepath)
        return False, f"File too small ({file_size} bytes)", 0

    elapsed = time.time() - start_time
    avg_speed = file_size / elapsed / 1024 / 1024 if elapsed > 0 else 0
    logger.info(f"[DL-ER] DONE | size={_format_size(file_size)} | time={elapsed:.1f}s | speed={avg_speed:.1f} MB/s")
    return True, "", file_size


async def _download_single(url, filepath, headers, progress_cb, dl_id=""):
    """Single connection download."""
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
                                if active_downloads.get(dl_id, {}).get("cancelled"):
                                    _cleanup_file(filepath)
                                    return False, "Cancelled by user", 0
                                await f.write(chunk)
                                downloaded += len(chunk)
                                now = time.time()
                                if now - last_update >= PROGRESS_INTERVAL:
                                    last_update = now
                                    await progress_cb(_format_progress(downloaded, content_length, start_time, now))
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


# ─── Download: Photo ─────────────────────────────────────────────────────


async def download_erome_photo(photo_url, filepath, progress_cb=None):
    """دانلود یه عکس از erome."""
    if progress_cb is None:
        async def _noop(msg): pass
        progress_cb = _noop

    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        timeout = ClientTimeout(total=60, connect=15)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as s:
            async with s.get(photo_url, allow_redirects=True) as resp:
                if resp.status != 200:
                    return False, f"HTTP {resp.status}", 0
                data = await resp.read()
                if not data:
                    return False, "Empty response", 0
                async with aiofiles.open(filepath, "wb") as f:
                    await f.write(data)
                size = len(data)
                logger.info(f"[DL-ER] Photo DONE | size={_format_size(size)} | {os.path.basename(filepath)}")
                return True, "", size
    except Exception as e:
        logger.error(f"[DL-ER] Photo error: {e}")
        return False, str(e)[:200], 0


# ─── Download: All videos ────────────────────────────────────────────────


async def download_all_videos(media_info, output_dir, progress_cb=None, dl_id=""):
    """دانلود همه ویدیوهای آلبوم.

    Returns:
        list of {success, filepath, url, error}
    """
    videos = media_info.get("videos", [])
    if not videos:
        return []

    results = []
    os.makedirs(output_dir, exist_ok=True)
    title = media_info.get("title", "erome")

    for i, video in enumerate(videos, 1):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            break

        safe_title = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '_')[:50]
        filename = f"{safe_title}_video_{i:02d}_{video.get('quality_key', '720p')}.mp4"
        filepath = os.path.join(output_dir, filename)

        if progress_cb:
            await progress_cb(f"📥 **Downloading video {i}/{len(videos)}...**")

        success, error, size = await download_erome_video(
            video["url"], filepath, progress_cb=progress_cb, dl_id=dl_id,
            referer=media_info.get("page_url", "https://www.erome.com/"),
        )

        results.append({
            "success": success,
            "filepath": filepath if success else None,
            "url": video["url"],
            "error": error,
            "size": size,
            "type": "video",
        })

    return results


# ─── Download: All photos ────────────────────────────────────────────────


async def download_all_photos(media_info, output_dir, progress_cb=None, dl_id=""):
    """دانلود همه عکس‌های آلبوم.

    Returns:
        list of {success, filepath, url, error}
    """
    photos = media_info.get("photos", [])
    if not photos:
        return []

    results = []
    os.makedirs(output_dir, exist_ok=True)

    for i, photo in enumerate(photos, 1):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            break

        filename = photo.get("filename", f"photo_{i:02d}.jpg")
        filepath = os.path.join(output_dir, filename)

        if progress_cb:
            await progress_cb(f"📸 **Downloading photo {i}/{len(photos)}...**")

        success, error, size = await download_erome_photo(
            photo["url"], filepath, progress_cb=progress_cb,
        )

        results.append({
            "success": success,
            "filepath": filepath if success else None,
            "url": photo["url"],
            "error": error,
            "size": size,
            "type": "photo",
        })

    return results


# ─── Download: All media (videos + photos) ────────────────────────────────


async def download_all_media(media_info, output_dir, progress_cb=None, dl_id=""):
    """دانلود همه ویدیو و عکس‌های آلبوم.

    Returns:
        {videos: [...], photos: [...]}
    """
    video_results = await download_all_videos(media_info, output_dir, progress_cb, dl_id)
    photo_results = await download_all_photos(media_info, output_dir, progress_cb, dl_id)

    return {
        "videos": video_results,
        "photos": photo_results,
    }


# ─── Self-test ────────────────────────────────────────────────────────────


async def _self_test():
    test_url = "https://www.erome.com/a/vpIzeHdM"
    print(f"\n{'═' * 80}")
    print(f"Self-test: {test_url}")
    print(f"{'═' * 80}\n")

    async def progress(msg):
        print(f"  → {msg}")

    media = await extract_erome_media(test_url, progress_cb=progress)

    print(f"\n  Title: {media.get('title')}")
    print(f"  Album ID: {media.get('album_id')}")
    print(f"  Has videos: {media.get('has_videos')}")
    print(f"  Has photos: {media.get('has_photos')}")
    print(f"\n  Videos ({len(media.get('videos', []))}):")
    for v in media.get("videos", []):
        print(f"    [{v['quality_key']}] {v['url'][:120]}")
        print(f"      poster: {v.get('poster', '')[:100]}")
    print(f"\n  Photos ({len(media.get('photos', []))}):")
    for p in media.get("photos", []):
        print(f"    {p['filename']}: {p['url'][:120]}")

    return media


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    asyncio.run(_self_test())
