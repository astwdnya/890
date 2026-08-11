"""
ixxx_handler.py
───────────────
هندلر برای ixxx.com — این سایت یه aggregator هست که /out/ لینک‌ها رو به سایت‌های دیگه (مثل xhamster) redirect می‌کنه.

روش کار:
  1. تشخیص ixxx.com URL
  2. Follow redirect برای گرفتن URL نهایی (معمولاً xhamster.com)
  3. استخراج URL ویدیو از صفحه هدف
  4. دانلود با multi-segment یا ffmpeg

نکته: ixxx.com لینک‌های /out/ داره که با base64 encode شده و به xh.partners redirect می‌شن
  که خودش به xhamster.com redirect می‌شه.

ساختار URL:
  https://www.ixxx.com/out/?l={base64_encoded}&c={checksum}&v=3
  → redirects to https://xh.partners/x/{id}?pw=
  → redirects to https://xhamster.com/videos/{slug}-{id}
"""

import asyncio
import logging
import os
import re
import shutil
import time
from typing import Awaitable, Callable, List, Optional, Tuple
from urllib.parse import urlparse, urljoin, unquote

import aiofiles
import aiohttp
from aiohttp import ClientTimeout, CookieJar, TCPConnector

logger = logging.getLogger("IxxxHandler")

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

MAX_DOWNLOAD_SIZE = 2 * 1024 * 1024 * 1024
MIN_VALID_VIDEO_SIZE = 100 * 1024
PROGRESS_INTERVAL = 1.0
CHUNK_SIZE = 1024 * 1024
MAX_RETRIES = 3
RETRY_DELAY = 2.0
MULTI_SEGMENT_MIN_SIZE = 5 * 1024 * 1024

MULTI_SEGMENT_WORKERS = 32
MULTI_SEGMENT_CHUNK_SIZE = 10 * 1024 * 1024
CONNECTOR_LIMIT = 50
CONNECTOR_LIMIT_PER_HOST = 50

_ALLOWED_HOSTS = frozenset({
    "ixxx.com",
    "www.ixxx.com",
    "m.ixxx.com",
    "inxxx.com",
    "www.inxxx.com",
})

ProgressCallback = Callable[[str], Awaitable[None]]


def is_ixxx_url(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
        return host in _ALLOWED_HOSTS or host.endswith(".ixxx.com") or host.endswith(".inxxx.com")
    except Exception:
        return False


def _cleanup_file(filepath: str) -> None:
    try:
        if filepath and os.path.exists(filepath):
            os.remove(filepath)
    except OSError as e:
        logger.warning("cleanup %s: %s", filepath, e)


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024: return f"{size_bytes} B"
    if size_bytes < 1024*1024: return f"{size_bytes/1024:.1f} KB"
    if size_bytes < 1024*1024*1024: return f"{size_bytes/1024/1024:.1f} MB"
    return f"{size_bytes/1024/1024/1024:.2f} GB"


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
            f"📥 **Downloading...**\n`[{bar}]`\n"
            f"💾 {dl_mb:.1f}/{total_mb:.1f} MB  •  ⚡ {speed_mb:.1f} MB/s\n"
            f"📊 {pct:.1f}%  •  ⏱ ETA: {eta_m}:{eta_s:02d}"
        )
    return f"📥 **Downloading...**\n💾 {dl_mb:.1f} MB  •  ⚡ {speed / 1024 / 1024:.1f} MB/s"


# ─── Redirect follower ────────────────────────────────────────────────────


async def _resolve_redirect(url: str) -> Tuple[str, str]:
    """
    Follow redirect chain from ixxx.com to get the final video page URL.
    
    Returns:
        (final_url, html_content)
    """
    try:
        timeout = ClientTimeout(total=30, connect=10)
        jar = CookieJar(unsafe=True)
        async with aiohttp.ClientSession(timeout=timeout, headers=_DEFAULT_HEADERS, cookie_jar=jar) as session:
            async with session.get(url, allow_redirects=True) as resp:
                final_url = str(resp.url)
                html = await resp.text(errors="replace")
                logger.info("Redirect resolved: %s → %s (size=%d)", url[:60], final_url[:80], len(html))
                return final_url, html
    except Exception as e:
        logger.error(f"Redirect resolution failed: {e}")
        return "", ""


# ─── Extraction ────────────────────────────────────────────────────────────


def _extract_title(html: str) -> str:
    m = re.search(r'(?:property|name)=["\']og:title["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if not m:
        m = re.search(r'content=["\']([^"\']+)["\']\s+(?:property|name)=["\']og:title["\']', html, re.IGNORECASE)
    if m:
        title = m.group(1).strip()
        title = re.sub(r"\s*[-|@]\s*(?:xhamster|XHamster|ixxx)\.com\s*$", "", title, flags=re.IGNORECASE)
        return title or "Untitled"
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    if m:
        title = m.group(1).strip()
        title = re.sub(r"\s*[-|@]\s*(?:xhamster|XHamster|ixxx)\.com\s*$", "", title, flags=re.IGNORECASE)
        return title or "Untitled"
    return "Untitled"


def _extract_thumbnail(html: str) -> str:
    m = re.search(r'(?:property|name)=["\']og:image["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


def _extract_duration(html: str) -> Optional[int]:
    m = re.search(r'"duration"\s*:\s*"?(\d+)"?', html)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    m = re.search(r'"duration"\s*:\s*"(PT[^"]+)"', html)
    if m:
        ds = m.group(1)
        h = re.search(r'(\d+)H', ds)
        mn = re.search(r'(\d+)M', ds)
        s = re.search(r'(\d+)S', ds)
        total = 0
        if h: total += int(h.group(1)) * 3600
        if mn: total += int(mn.group(1)) * 60
        if s: total += int(s.group(1))
        return total if total > 0 else None
    return None


def _extract_video_sources(html: str) -> List[dict]:
    """
    استخراج URL های ویدیو از HTML صفحه هدف (معمولاً xhamster).
    
    xhamster از HLS و MP4 مستقیم استفاده می‌کنه.
    URL pattern: https://video{N}.xhcdn.com/key=.../{quality}p.h264.mp4
    یا: https://video-nss.xhcdn.com/.../master.m3u8
    """
    sources = []
    seen_urls = set()

    # Method 1: Direct MP4 URLs from xhcdn (not thumbnails/previews)
    mp4_pattern = re.compile(
        r'(https?://video\d*\.xhcdn\.com/[^\s"\'<>\)\]]+?\.mp4[^\s"\'<>\)\]]*)',
        re.IGNORECASE,
    )
    for m in mp4_pattern.finditer(html):
        url = m.group(1).replace("\\/", "/").strip()
        # Skip thumbnails and previews
        if "thumb" in url.lower() or "preview" in url.lower() or ".t.mp4" in url.lower():
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)

        # Extract quality from URL
        url_lower = url.lower()
        if "1080p" in url_lower or "1080" in url_lower:
            height, qk, is_hd = 1080, "1080p", True
        elif "720p" in url_lower or "720" in url_lower:
            height, qk, is_hd = 720, "720p", True
        elif "480p" in url_lower or "480" in url_lower:
            height, qk, is_hd = 480, "480p", False
        elif "360p" in url_lower or "360" in url_lower:
            height, qk, is_hd = 360, "360p", False
        elif "240p" in url_lower or "240" in url_lower:
            height, qk, is_hd = 240, "240p", False
        elif "144p" in url_lower or "144" in url_lower:
            height, qk, is_hd = 144, "144p", False
        else:
            # Try to extract from URL path
            qm = re.search(r'/(\d{3,4})p\.', url, re.IGNORECASE)
            if qm:
                height = int(qm.group(1))
                qk = f"{height}p"
                is_hd = height >= 720
            else:
                continue  # Skip if we can't determine quality

        sources.append({
            "label": f"📺 MP4 {qk}",
            "url": url,
            "height": height,
            "quality_key": qk,
            "method": "xhcdn_mp4",
            "is_hd": is_hd,
        })
        logger.info("Found: %s (%s)", qk, url[:100])

    # Method 2: HLS M3U8 URLs
    m3u8_pattern = re.compile(
        r'(https?://[^\s"\'<>\)\]]+?xhcdn\.com[^\s"\'<>\)\]]+?\.m3u8[^\s"\'<>\)\]]*)',
        re.IGNORECASE,
    )
    for m in m3u8_pattern.finditer(html):
        url = m.group(1).replace("\\/", "/").strip()
        if url in seen_urls:
            continue
        seen_urls.add(url)

        # HLS master playlist — use ffmpeg to download
        sources.append({
            "label": "📺 MP4 (HLS)",
            "url": url,
            "height": 720,
            "quality_key": "720p",
            "method": "hls_m3u8",
            "is_hd": True,
        })
        logger.info("Found HLS: %s", url[:100])

    # Sort by height descending
    sources.sort(key=lambda q: q.get("height", 0), reverse=True)
    return sources


# ─── Main API: extract qualities ──────────────────────────────────────────


async def extract_ixxx_qualities(url, progress_cb=None):
    """استخراج کیفیت‌های ویدیو از ixxx.com (با redirect resolution)."""
    if not is_ixxx_url(url):
        return [], "Invalid URL", {}

    if progress_cb:
        await progress_cb("🔄 **دریافت اطلاعات صفحه...**")

    # Follow redirect to get the actual video page
    final_url, html = await _resolve_redirect(url)

    if not html:
        return [], "خطا در دریافت صفحه (redirect failed)", {}

    if not final_url:
        return [], "خطا در resolve redirect", {}

    logger.info("Final URL: %s", final_url[:100])

    title = _extract_title(html)
    thumbnail = _extract_thumbnail(html)
    duration = _extract_duration(html)
    sources = _extract_video_sources(html)

    if not sources:
        # Fallback: try yt-dlp on the final URL
        logger.warning("No direct video URLs found, will need yt-dlp fallback")
        return [], f"No video sources found at {final_url[:80]}", {}

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
        "final_url": final_url,
        "duration": duration,
        "fetch_method": "redirect",
    }


# ─── Download: Multi-segment ──────────────────────────────────────────────


active_downloads: dict = {}


async def _download_multi_segment(direct_url, filepath, referer, cookies, progress_cb, dl_id="", num_workers=MULTI_SEGMENT_WORKERS):
    try:
        headers = {**_DEFAULT_HEADERS, "Referer": referer, "Accept": "*/*"}
        timeout = ClientTimeout(total=10, connect=5)
        content_length = 0
        accept_ranges = ""

        try:
            async with aiohttp.ClientSession(timeout=timeout, headers={**headers, "Range": "bytes=0-0"}, cookies=cookies) as s:
                async with s.get(direct_url, allow_redirects=True) as r:
                    if r.status in (200, 206):
                        accept_ranges = "bytes"
                        cr = r.headers.get("Content-Range", "")
                        m = re.search(r"/(\d+)", cr)
                        if m:
                            content_length = int(m.group(1))
                        else:
                            content_length = int(r.headers.get("Content-Length", 0))
                    elif r.status == 403:
                        return False, "HTTP_403", 0
        except Exception as e:
            logger.warning(f"Probe failed: {e}")

        if content_length == 0:
            return False, "Cannot determine file size", 0
        if content_length > MAX_DOWNLOAD_SIZE:
            return False, f"File too large: {_format_size(content_length)}", 0

        total_mb = content_length / 1024 / 1024
        await progress_cb(f"📥 **Downloading...**\n💾 Size: {total_mb:.1f} MB\n🔥 {num_workers} workers")

        CHUNK_SIZE_BYTES = MULTI_SEGMENT_CHUNK_SIZE
        chunks = []
        offset = 0
        while offset < content_length:
            end = min(offset + CHUNK_SIZE_BYTES - 1, content_length - 1)
            chunks.append((offset, end))
            offset = end + 1

        total_chunks = len(chunks)
        logger.info(f"[DL-IX] Work-queue: {total_chunks} chunks, total={content_length}")

        try:
            async with aiofiles.open(filepath, "wb") as f:
                await f.truncate(content_length)
        except: pass

        chunk_queue = asyncio.Queue()
        for c in chunks:
            await chunk_queue.put(c)

        downloaded_bytes = [0] * total_chunks
        completed_chunks = [0]
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
                    f"📥 **Downloading...**\n`[{bar}]`\n"
                    f"💾 {dl_mb:.1f}/{total_mb_local:.1f} MB  •  ⚡ {speed_mb:.1f} MB/s\n"
                    f"📊 {pct:.1f}%  •  ⏱ ETA: {eta_m}:{eta_s:02d}\n"
                    f"📦 {completed_chunks[0]}/{total_chunks} chunks • 🔥 {num_workers}x"
                )
            except: pass

        shared_timeout = ClientTimeout(total=600, connect=30, sock_read=120)
        connector = TCPConnector(limit=CONNECTOR_LIMIT, limit_per_host=CONNECTOR_LIMIT_PER_HOST, keepalive_timeout=60, enable_cleanup_closed=True)
        shared_session = aiohttp.ClientSession(timeout=shared_timeout, headers=headers, cookies=cookies, connector=connector)
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
                for attempt in range(MAX_RETRIES):
                    if active_downloads.get(dl_id, {}).get("cancelled"):
                        return False
                    try:
                        async with shared_session.get(direct_url, headers={"Range": f"bytes={byte_start}-{byte_end}"}, allow_redirects=True) as resp:
                            if resp.status not in (200, 206):
                                raise Exception(f"HTTP {resp.status}")
                            chunk_data = bytearray()
                            async for piece in resp.content.iter_chunked(CHUNK_SIZE):
                                if not piece: continue
                                if active_downloads.get(dl_id, {}).get("cancelled"):
                                    return False
                                chunk_data.extend(piece)
                            if len(chunk_data) != chunk_size:
                                raise Exception(f"Size mismatch: {chunk_size} vs {len(chunk_data)}")
                            async with file_write_lock:
                                await shared_file.seek(byte_start)
                                await shared_file.write(bytes(chunk_data))
                            downloaded_bytes[byte_start // CHUNK_SIZE_BYTES] = chunk_size
                            async with progress_lock:
                                completed_chunks[0] += 1
                                await _update_progress()
                            break
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.warning(f"[DL-IX] W{worker_id} c{byte_start//CHUNK_SIZE_BYTES} attempt {attempt+1}: {e}")
                        if attempt < MAX_RETRIES - 1:
                            await asyncio.sleep(RETRY_DELAY * (attempt + 1))
                        else:
                            return False
                chunk_queue.task_done()
            return True

        try:
            results = await asyncio.gather(*[_download_worker(i) for i in range(num_workers)], return_exceptions=True)
            try: await shared_file.close()
            except: pass
            try: await shared_session.close()
            except: pass

            if active_downloads.get(dl_id, {}).get("cancelled"):
                _cleanup_file(filepath)
                return False, "Cancelled by user", 0

            real_failures = [r for r in results if r is not True]
            if real_failures:
                _cleanup_file(filepath)
                return False, f"Multi-segment failed: {len(real_failures)} chunks", 0

        except Exception as e:
            logger.error(f"[DL-IX] error: {e}", exc_info=True)
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
        logger.info(f"[DL-IX] DONE | size={_format_size(file_size)} | time={elapsed:.1f}s | speed={avg_speed:.1f} MB/s")
        return True, "", file_size
    except Exception as e:
        logger.error(f"[DL-IX] error: {e}", exc_info=True)
        _cleanup_file(filepath)
        return False, str(e)[:200], 0


# ─── Download: ffmpeg (for HLS) ──────────────────────────────────────────


async def _download_with_ffmpeg(video_url, filepath, progress_cb, dl_id="", referer=""):
    if not shutil.which("ffmpeg"):
        return False, "ffmpeg not installed", 0
    await progress_cb("📥 **Downloading via ffmpeg...**")
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-user_agent", _USER_AGENT,
        "-referer", referer or "https://xhamster.com/",
        "-i", video_url,
        "-c", "copy", "-bsf:a", "aac_adtstoasc",
        "-movflags", "+faststart", filepath,
    ]
    try:
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        last_update = 0.0
        start_time = time.time()
        stderr_buffer = []

        async def _read_stderr():
            nonlocal last_update
            try:
                while True:
                    if active_downloads.get(dl_id, {}).get("cancelled"):
                        process.kill()
                        return
                    line = await process.stderr.readline()
                    if not line: break
                    text = line.decode(errors="replace").strip()
                    if text: stderr_buffer.append(text)
                    now = time.time()
                    if now - last_update >= PROGRESS_INTERVAL:
                        last_update = now
                        try:
                            sz = os.path.getsize(filepath) if os.path.exists(filepath) else 0
                            el = now - start_time
                            sp = sz / el if el > 0 else 0
                            await progress_cb(f"📥 **Downloading...**\n💾 {sz/1024/1024:.1f} MB  •  ⚡ {sp/1024/1024:.1f} MB/s")
                        except: pass
            except: pass

        stderr_task = asyncio.create_task(_read_stderr())
        await process.wait()
        try: stderr_task.cancel()
        except: pass

        if process.returncode != 0:
            _cleanup_file(filepath)
            return False, f"ffmpeg: {';'.join(stderr_buffer[-3:])[:200]}", 0

        file_size = os.path.getsize(filepath)
        if file_size < MIN_VALID_VIDEO_SIZE:
            _cleanup_file(filepath)
            return False, f"File too small ({file_size} bytes)", 0
        return True, "", file_size
    except asyncio.CancelledError:
        _cleanup_file(filepath)
        raise
    except Exception as e:
        _cleanup_file(filepath)
        return False, str(e)[:200], 0


# ─── Download: yt-dlp fallback ───────────────────────────────────────────


async def _download_with_ytdlp(url, filepath, progress_cb, quality_key=""):
    if not shutil.which("yt-dlp"):
        return False, "yt-dlp not installed", 0
    await progress_cb("📥 **Fallback: yt-dlp...**")
    try:
        cmd = [
            "yt-dlp", "--no-warnings", "--progress", "--newline",
            "--no-check-certificates", "-f", "best",
            "--concurrent-fragments", "16", "--retries", "10",
            "--buffer-size", "16K", "--max-filesize", str(MAX_DOWNLOAD_SIZE),
            "--add-header", f"User-Agent:{_USER_AGENT}",
            "-o", filepath, url,
        ]
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        while True:
            try:
                line = await asyncio.wait_for(process.stdout.readline(), timeout=300)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                _cleanup_file(filepath)
                return False, "Download timed out", 0
            if not line: break
            text = line.decode(errors="replace").strip()
            if "[download]" in text:
                pct_m = re.search(r"(\d+\.?\d*)%", text)
                if pct_m:
                    try:
                        pct = float(pct_m.group(1))
                        filled = int(pct / 5)
                        bar = "█" * filled + "░" * (20 - filled)
                        await progress_cb(f"📥 **Downloading (yt-dlp)...**\n`[{bar}]`\n📊 {pct_m.group(1)}%")
                    except: pass
        await process.wait()
        if process.returncode != 0:
            stderr = (await process.stderr.read()).decode(errors="replace")
            return False, stderr[-200:], 0
        actual_path = filepath
        if not os.path.exists(actual_path):
            base, _ = os.path.splitext(filepath)
            for ext in (".mp4", ".mkv", ".webm", ".ts"):
                c = base + ext
                if os.path.exists(c):
                    actual_path = c
                    break
        if not os.path.exists(actual_path):
            return False, "Output file not found", 0
        size = os.path.getsize(actual_path)
        if size < MIN_VALID_VIDEO_SIZE:
            _cleanup_file(actual_path)
            return False, f"File too small ({size} bytes)", 0
        if actual_path != filepath:
            try: os.rename(actual_path, filepath)
            except: pass
        return True, "", size
    except asyncio.CancelledError:
        _cleanup_file(filepath)
        raise
    except Exception as e:
        _cleanup_file(filepath)
        return False, str(e)[:200], 0


# ─── Public API ────────────────────────────────────────────────────────────


async def download_ixxx_video(page_url, video_url, filepath, progress_cb=None, cookies=None, dl_id="", quality_key="", final_url=""):
    if not is_ixxx_url(page_url):
        return False, "URL host not allowed", 0
    if not video_url:
        return False, "Empty video URL", 0
    if progress_cb is None:
        async def _noop(msg): pass
        progress_cb = _noop
    if dl_id and dl_id not in active_downloads:
        active_downloads[dl_id] = {"paused": False, "cancelled": False}
    referer = final_url or page_url
    if not cookies:
        cookies = {}

    # If HLS, use ffmpeg
    if ".m3u8" in video_url:
        logger.info("[DL-IX] Using ffmpeg for HLS")
        success, error, size = await _download_with_ffmpeg(video_url, filepath, progress_cb, dl_id=dl_id, referer=referer)
        if success:
            return True, "", size
        _cleanup_file(filepath)

    # Try multi-segment
    logger.info("[DL-IX] Trying multi-segment")
    success, error, size = await _download_multi_segment(video_url, filepath, referer, cookies, progress_cb, dl_id=dl_id)
    if success:
        return True, "", size
    _cleanup_file(filepath)

    # Fallback: yt-dlp on final URL
    logger.info("[DL-IX] Trying yt-dlp")
    success, error, size = await _download_with_ytdlp(final_url or page_url, filepath, progress_cb, quality_key=quality_key)
    if success:
        return True, "", size
    _cleanup_file(filepath)
    return False, error or "All download methods failed", 0


async def download_ixxx_direct(url, filepath, progress_cb=None, video_url="", quality="high", dl_id=""):
    if not video_url:
        qualities, title, info = await extract_ixxx_qualities(url, progress_cb)
        if not qualities:
            return False, title or "Extraction failed", 0
        selected = None
        for q in qualities:
            if q.get("quality_key") == quality:
                selected = q
                break
        if not selected:
            if quality in ("high", "best"):
                hd = [q for q in qualities if q.get("is_hd")]
                selected = hd[0] if hd else qualities[0]
            elif quality in ("low", "worst"):
                selected = qualities[-1]
            else:
                selected = qualities[0]
        video_url = selected["url"]
        quality_key = selected.get("quality_key", "")
        final_url = info.get("final_url", "")
    else:
        qualities, title, info = await extract_ixxx_qualities(url, progress_cb)
        final_url = info.get("final_url", "") if info else ""
        quality_key = quality

    return await download_ixxx_video(url, video_url, filepath, progress_cb, dl_id=dl_id, quality_key=quality_key, final_url=final_url)


# ─── Self-test ────────────────────────────────────────────────────────────


async def _self_test():
    url = "https://www.ixxx.com/out/?l=3AASPc4ghdsrq2ZRcUxncE5pclMwAtkhaHR0cHM6Ly94aC5wYXJ0bmVycy94L3hod0MyM2o/cHc9zQGGonRjAQGncG9wdWxhcgHZMHsiYWxsIjoiIiwib3JpZW50YXRpb24iOiJzdHJhaWdodCIsInByaWNpbmciOiIifc0FcM5qe5RDqGNhdGVnb3J5zgDhrG7A2T1beyIxIjoiSjUwS1BBSTJFbWgifSx7IjIiOiJVUEJUMHFDR0hTUiJ9LHsiMyI6InMyTHNWd3hLNVVxIn1d&c=81604693f18df86c&v=3&"
    print(f"\n{'═' * 80}\nSelf-test: ixxx.com\n{'═' * 80}\n")

    async def progress(msg):
        print(f"  → {msg}")

    sources, title, info = await extract_ixxx_qualities(url, progress_cb=progress)
    print(f"\n  Title: {title}")
    print(f"  Final URL: {info.get('final_url', '?')[:100]}")
    print(f"\n  Sources ({len(sources)}):")
    for s in sources:
        print(f"    [{s['quality_key']:6s}] {s['url'][:120]} ({s['method']})")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    asyncio.run(_self_test())
