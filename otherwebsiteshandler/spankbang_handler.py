"""
spankbang_handler.py
────────────────────
استخراج و دانلود ویدیو از spankbang.com

ویژگی‌ها:
  - دریافت اطلاعات و لینک‌های ویدیو با پشتیبانی از Cloudflare Bypass اتوماتیک via Dirpy Studio & Playwright
  - دانلود فوق سریع ۳۲ اتصاله موازی (Multi-Segment با 32 Workers)
  - بدون تایم‌اوت بدنه دریافت (timeout=86400) برای جلوگیری از قطعی در فایل‌های سنگین
  - پشتیبانی از HLS (m3u8) با ffmpeg و MP4 مستقیم
"""

import asyncio
import logging
import os
import re
import shutil
import time
from typing import Awaitable, Callable, List, Optional, Tuple
from urllib.parse import urlparse, quote

import sys
sys.stdout.reconfigure(encoding='utf-8')
import aiofiles
from curl_cffi.requests import AsyncSession

logger = logging.getLogger("SpankBangHandler")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

MAX_DOWNLOAD_SIZE = 50 * 1024 * 1024 * 1024
MIN_VALID_VIDEO_SIZE = 100 * 1024
CHUNK_SIZE = 1024 * 1024
PROGRESS_INTERVAL = 1.0

MULTI_SEGMENT_WORKERS = 32
MULTI_SEGMENT_CHUNK_SIZE = 4 * 1024 * 1024
MULTI_SEGMENT_MIN_SIZE = 2 * 1024 * 1024
MAX_RETRIES = 3
RETRY_DELAY = 1.0

_ALLOWED_HOSTS = frozenset({"spankbang.com", "www.spankbang.com", "m.spankbang.com"})

active_downloads: dict = {}

ProgressCallback = Callable[[str], Awaitable[None]]


def is_spankbang_url(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
        return host in _ALLOWED_HOSTS or host.endswith(".spankbang.com")
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


def _has_playwright() -> bool:
    try:
        from playwright.async_api import async_playwright  # noqa
        return True
    except ImportError:
        return False


# ─── Extraction via Dirpy (Primary CF Bypass) ────────────────────────────


async def _extract_via_dirpy(url: str, progress_cb=None) -> Tuple[List[dict], str, dict]:
    """استخراج لینک‌های مستقیم ویدیو SpankBang از طریق Dirpy Studio."""
    if not _has_playwright():
        return [], "Playwright not installed", {}

    if progress_cb:
        await progress_cb("🔄 **دریافت اطلاعات ویدیو (Dirpy Bypass)...**")

    dirpy_url = f"https://dirpy.com/studio?url={quote(url)}"
    sources = []
    title = "SpankBang Video"

    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            context = await browser.new_context(user_agent=_USER_AGENT)
            page = await context.new_page()
            await page.goto(dirpy_url, wait_until="domcontentloaded", timeout=45000)

            for _ in range(12):
                await page.wait_for_timeout(1000)
                t = await page.title()
                if "Dirpy" in t or "Studio" in t:
                    break

            html = await page.content()
            await browser.close()

            title_m = re.search(r'<title[^>]*>(.*?)</title>', html, re.I)
            if title_m:
                t = title_m.group(1).replace("Dirpy Studio", "").strip(" -|\t\n")
                if t: title = t

            links = re.findall(r'href=["\'](https://vdownload-[^"\']+)["\']', html)
            if not links:
                links = re.findall(r'(https://vdownload-[^\s"\'<>]+)', html)

            seen = set()
            for l in links:
                clean_l = l.replace("&amp;", "&")
                if clean_l in seen: continue
                seen.add(clean_l)

                q_match = re.search(r'-(\d+p)\.mp4', clean_l, re.I)
                qk = q_match.group(1).lower() if q_match else "720p"
                h = int(qk.replace("p", "")) if qk.endswith("p") and qk[:-1].isdigit() else 720

                sources.append({
                    "label": f"📺 MP4 {qk.upper()}",
                    "url": clean_l,
                    "height": h,
                    "quality_key": qk,
                    "method": "dirpy_cdn",
                    "is_hd": h >= 720,
                })

            sources.sort(key=lambda x: x["height"], reverse=True)
            info = {"title": title, "page_url": url, "fetch_method": "dirpy"}

            if progress_cb and sources:
                labels = ", ".join(s["label"] for s in sources)
                await progress_cb(f"✅ **پیدا شد:** {title[:50]}\n🎞 کیفیت‌ها: {labels}")

            return sources, title, info
    except Exception as e:
        logger.error(f"Dirpy extraction error: {e}")
        return [], f"Dirpy error: {e}", {}


# ─── Extraction via Playwright (Direct Page) ──────────────────────────────


async def _extract_via_playwright(url: str, progress_cb=None) -> Tuple[List[dict], str, dict]:
    """استخراج ویدیو URL با Playwright (مستقیم یا Dirpy)."""
    if not _has_playwright():
        return [], "Playwright not installed", {}

    from playwright.async_api import async_playwright

    if progress_cb:
        await progress_cb("🔄 **دریافت اطلاعات صفحه...**")

    sources = []
    title = ""
    info = {}

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context(
                user_agent=_USER_AGENT,
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
            )
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                window.chrome = {runtime: {}, app: {}, csi: () => {}, loadTimes: () => {}};
            """)
            page = await context.new_page()

            m3u8_urls = []
            mp4_urls = []

            async def on_response(resp):
                u = str(resp.url)
                if '.m3u8' in u:
                    m3u8_urls.append(u)
                elif '.mp4' in u and 'preview' not in u.lower():
                    mp4_urls.append(u)

            page.on("response", on_response)

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                logger.warning(f"goto: {e}")

            for i in range(15):
                await page.wait_for_timeout(2000)
                title_check = await page.title()
                if "Just a moment" not in title_check:
                    break

            title = await page.title()
            html = await page.content()
            await browser.close()

            # اگر Cloudflare چالش داد، سراغ Dirpy می‌رویم
            if "Just a moment" in title or "Just a moment" in html:
                logger.info("Direct Playwright CF blocked — switching to Dirpy extraction...")
                return await _extract_via_dirpy(url, progress_cb)

            seen = set()
            for u in m3u8_urls:
                if u not in seen:
                    seen.add(u)
                    sources.append({
                        "label": "📺 MP4 (HLS)",
                        "url": u,
                        "height": 720,
                        "quality_key": "720p",
                        "method": "playwright_m3u8",
                        "is_hd": True,
                    })

            for u in mp4_urls:
                if u not in seen:
                    seen.add(u)
                    url_lower = u.lower()
                    if "_1080" in url_lower or "1080p" in url_lower:
                        h, qk = 1080, "1080p"
                    elif "_720" in url_lower or "720p" in url_lower:
                        h, qk = 720, "720p"
                    elif "_480" in url_lower or "480p" in url_lower:
                        h, qk = 480, "480p"
                    else:
                        h, qk = 720, "720p"
                    sources.append({
                        "label": f"📺 MP4 {qk}",
                        "url": u,
                        "height": h,
                        "quality_key": qk,
                        "method": "playwright_mp4",
                        "is_hd": h >= 720,
                    })

            if not sources:
                logger.info("No sources from direct page — trying Dirpy...")
                return await _extract_via_dirpy(url, progress_cb)

            info = {"page_url": url, "fetch_method": "playwright"}
            if progress_cb:
                labels = ", ".join(s["label"] for s in sources)
                await progress_cb(f"✅ **پیدا شد:** {title[:50]}\n🎞 کیفیت‌ها: {labels}")

            return sources, title, info

    except Exception as e:
        logger.error(f"Playwright error: {e}", exc_info=True)
        return await _extract_via_dirpy(url, progress_cb)


# ─── High-Speed Multi-Segment Download (32x Workers) ─────────────────────


async def _download_multi_segment(s, page_url, target_url, filepath, progress_cb, dl_id="", num_workers=MULTI_SEGMENT_WORKERS):
    """
    دانلود موازی ۳۲ اتصاله برای فایل‌های MP4.
    """
    cdn_headers = {
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": page_url or "https://spankbang.com/",
        "User-Agent": _USER_AGENT,
    }

    content_length = 0
    accept_ranges = False

    try:
        probe_headers = dict(cdn_headers)
        probe_headers["Range"] = "bytes=0-1023"
        probe_resp = await s.get(target_url, impersonate="chrome", headers=probe_headers, allow_redirects=True, timeout=(30, 86400))
        if probe_resp.status_code in (200, 206):
            ct = probe_resp.headers.get("content-type", "").lower()
            if "image" in ct or "text/html" in ct:
                return False, f"Got invalid content-type: {ct}", 0
            if probe_resp.status_code == 206:
                accept_ranges = True
                cr = probe_resp.headers.get("Content-Range", "")
                m = re.search(r"/(\d+)", cr)
                if m:
                    content_length = int(m.group(1))
            if content_length == 0:
                content_length = int(probe_resp.headers.get("Content-Length", 0))
    except Exception as e:
        logger.warning("Probe request failed: %s", e)

    if content_length == 0 or not accept_ranges or content_length < MULTI_SEGMENT_MIN_SIZE:
        return False, "Range not supported or file too small for multi-segment", 0

    if content_length > MAX_DOWNLOAD_SIZE:
        return False, f"File too large: {_format_size(content_length)}", 0

    total_mb = content_length / 1024 / 1024
    if progress_cb:
        await progress_cb(f"📥 **Downloading (32x Parallel)...**\n💾 Size: {total_mb:.1f} MB")

    chunks = []
    offset = 0
    chunk_idx = 0
    while offset < content_length:
        end = min(offset + MULTI_SEGMENT_CHUNK_SIZE - 1, content_length - 1)
        chunks.append((chunk_idx, offset, end))
        offset = end + 1
        chunk_idx += 1

    total_chunks = len(chunks)
    logger.info("Multi-segment work-queue: %d chunks, %d workers, size=%d", total_chunks, num_workers, content_length)

    try:
        async with aiofiles.open(filepath, "wb") as f:
            await f.truncate(content_length)
    except Exception as e:
        logger.warning("File pre-allocate error: %s", e)

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
        if progress_cb:
            try:
                await progress_cb(
                    f"📥 **Downloading...**\n(هندلر)\n`[{bar}]`\n"
                    f"💾 {dl_mb:.1f}/{total_mb_local:.1f} MB  •  ⚡ {speed_mb:.1f} MB/s\n"
                    f"📊 {pct:.1f}%  •  ⏱ ETA: {eta_m}:{eta_s:02d}\n"
                    f"📦 {completed_chunks[0]}/{total_chunks} chunks • 🔥 {num_workers}x speed"
                )
            except Exception:
                pass

    shared_file = await aiofiles.open(filepath, "r+b")

    async def _worker(worker_id):
        while True:
            if active_downloads.get(dl_id, {}).get("cancelled"):
                return False
            try:
                chunk_info = chunk_queue.get_nowait()
            except asyncio.QueueEmpty:
                return True

            c_idx, byte_start, byte_end = chunk_info
            chunk_size = byte_end - byte_start + 1

            for attempt in range(MAX_RETRIES):
                if active_downloads.get(dl_id, {}).get("cancelled"):
                    return False
                try:
                    resp = await s.get(
                        target_url,
                        impersonate="chrome",
                        headers={**cdn_headers, "Range": f"bytes={byte_start}-{byte_end}"},
                        allow_redirects=True,
                        timeout=(30, 86400),
                    )
                    if resp.status_code not in (200, 206):
                        raise Exception(f"HTTP {resp.status_code}")
                    chunk_data = resp.content if hasattr(resp, "content") else resp.body
                    if len(chunk_data) != chunk_size:
                        raise Exception(f"Size mismatch: expected {chunk_size}, got {len(chunk_data)}")

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
                    logger.warning("Worker %d chunk %d attempt %d failed: %s", worker_id, c_idx, attempt + 1, e)
                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(RETRY_DELAY * (attempt + 1))
                    else:
                        failed_chunks.append((c_idx, str(e)[:100]))
                        return False
            chunk_queue.task_done()

    try:
        results = await asyncio.gather(*[_worker(i) for i in range(num_workers)], return_exceptions=True)
        try: await shared_file.close()
        except: pass

        if active_downloads.get(dl_id, {}).get("cancelled"):
            _cleanup_file(filepath)
            return False, "Cancelled by user", 0

        real_failures = [r for r in results if r is not True]
        if real_failures or failed_chunks:
            logger.warning("Multi-segment failed: %d worker failures, %d chunk failures", len(real_failures), len(failed_chunks))
            _cleanup_file(filepath)
            return False, f"Multi-segment failed ({len(failed_chunks)} chunks)", 0

        file_size = os.path.getsize(filepath)
        if file_size < MIN_VALID_VIDEO_SIZE:
            _cleanup_file(filepath)
            return False, f"File too small ({file_size} bytes)", 0

        logger.info("Multi-segment DONE (32x) | size=%s", _format_size(file_size))
        return True, "", file_size

    except Exception as e:
        logger.error("Multi-segment execution error: %s", e, exc_info=True)
        try: await shared_file.close()
        except: pass
        _cleanup_file(filepath)
        return False, str(e)[:200], 0


async def _stream_download_response(s, page_url, target_url, filepath, progress_cb, dl_id=""):
    """دانلود تکه‌ای (Stream) ویدیو بدون تایم‌اوت بدنه."""
    video_resp = await s.get(target_url, impersonate="chrome", headers={
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": page_url or "https://spankbang.com/",
        "User-Agent": _USER_AGENT,
    }, allow_redirects=True, timeout=(30, 86400), stream=True)

    if video_resp.status_code != 200:
        return False, f"HTTP {video_resp.status_code}", 0

    ct = video_resp.headers.get("content-type", "").lower()
    if "image" in ct or "text/html" in ct:
        return False, f"Got invalid content-type: {ct}", 0

    content_length = int(video_resp.headers.get("content-length", 0))
    if content_length > MAX_DOWNLOAD_SIZE:
        return False, f"File too large: {_format_size(content_length)}", 0

    start_time = time.time()
    last_update = 0.0
    downloaded = 0

    async with aiofiles.open(filepath, "wb") as f:
        async for chunk in video_resp.aiter_content(chunk_size=CHUNK_SIZE):
            if active_downloads.get(dl_id, {}).get("cancelled"):
                _cleanup_file(filepath)
                return False, "Cancelled by user", 0
            if chunk:
                await f.write(chunk)
                downloaded += len(chunk)
                now = time.time()
                if progress_cb and now - last_update >= PROGRESS_INTERVAL:
                    last_update = now
                    msg = (
                        f"📥 **Downloading...**\n"
                        f"💾 {downloaded/1024/1024:.1f}/{content_length/1024/1024:.1f} MB  •  ⚡ {downloaded/(now-start_time)/1024/1024:.1f} MB/s"
                    ) if content_length > 0 else f"📥 **Downloading...**\n(هندلر)\n💾 {downloaded/1024/1024:.1f} MB"
                    try:
                        await progress_cb(msg)
                    except Exception:
                        pass

    size = os.path.getsize(filepath)
    if size < MIN_VALID_VIDEO_SIZE:
        _cleanup_file(filepath)
        return False, f"File too small ({size} bytes)", 0

    logger.info("Download DONE | size=%s", _format_size(size))
    return True, "", size


# ─── Download via ffmpeg (for HLS/m3u8) ──────────────────────────────────


async def _download_with_ffmpeg(video_url, filepath, progress_cb, dl_id="", referer="https://spankbang.com/"):
    if not shutil.which("ffmpeg"):
        return False, "ffmpeg not installed", 0

    if progress_cb:
        await progress_cb("📥 **Downloading via ffmpeg...**")

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-user_agent", _USER_AGENT,
        "-referer", referer,
        "-i", video_url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        "-movflags", "+faststart",
        filepath,
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )

        last_update = 0.0
        start_time = time.time()
        stderr_buffer = []

        async def _read_stderr():
            nonlocal last_update
            try:
                while True:
                    if dl_id and dl_id in active_downloads and active_downloads[dl_id].get("cancelled"):
                        process.kill()
                        return
                    line = await process.stderr.readline()
                    if not line:
                        break
                    text = line.decode(errors="replace").strip()
                    if text:
                        stderr_buffer.append(text)
                    now = time.time()
                    if now - last_update >= PROGRESS_INTERVAL:
                        last_update = now
                        try:
                            current_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
                        except OSError:
                            current_size = 0
                        elapsed = now - start_time
                        speed = current_size / elapsed if elapsed > 0 else 0
                        dl_mb = current_size / 1024 / 1024
                        speed_mb = min(speed / 1024 / 1024, 999)
                        if progress_cb:
                            try:
                                await progress_cb(f"📥 **Downloading...**\n(هندلر)\n💾 {dl_mb:.1f} MB  •  ⚡ {speed_mb:.1f} MB/s")
                            except:
                                pass
            except asyncio.CancelledError:
                process.kill()
                raise
            except Exception as e:
                logger.warning(f"stderr read error: {e}")

        stderr_task = asyncio.create_task(_read_stderr())
        await process.wait()
        try:
            stderr_task.cancel()
        except:
            pass

        if process.returncode != 0:
            err_tail = "\n".join(stderr_buffer[-5:])[:300]
            _cleanup_file(filepath)
            return False, f"ffmpeg failed: {err_tail[:200]}", 0

        file_size = os.path.getsize(filepath)
        if file_size < MIN_VALID_VIDEO_SIZE:
            _cleanup_file(filepath)
            return False, f"File too small ({file_size} bytes)", 0

        logger.info(f"ffmpeg DONE | size={_format_size(file_size)}")
        return True, "", file_size

    except asyncio.CancelledError:
        _cleanup_file(filepath)
        raise
    except Exception as e:
        logger.error(f"ffmpeg error: {e}")
        _cleanup_file(filepath)
        return False, str(e)[:200], 0


# ─── Public API ────────────────────────────────────────────────────────────


async def extract_spankbang_qualities(url, progress_cb=None):
    if not is_spankbang_url(url):
        return [], "Invalid URL", {}
    return await _extract_via_playwright(url, progress_cb)


async def download_spankbang_video(page_url, video_url, filepath, progress_cb=None, cookies=None, dl_id="", quality_key=""):
    if not is_spankbang_url(page_url):
        return False, "URL host not allowed", 0
    if not video_url:
        return False, "Empty video URL", 0
    if progress_cb is None:
        async def _noop(msg): pass
        progress_cb = _noop
    if dl_id and dl_id not in active_downloads:
        active_downloads[dl_id] = {"paused": False, "cancelled": False}

    # اگر m3u8 باشد از ffmpeg استفاده می‌شود
    if ".m3u8" in video_url.lower():
        success, error, size = await _download_with_ffmpeg(video_url, filepath, progress_cb, dl_id=dl_id, referer=page_url)
        if success:
            return True, "", size
        _cleanup_file(filepath)
        return False, error or "Download failed", 0

    # اگر MP4 باشد، دانلود ۳۲ اتصاله موازی یا استریم پیوسته
    async with AsyncSession(max_clients=64) as s:
        # اولویت ۱: دانلود موازی ۳۲ تایی
        success, err, size = await _download_multi_segment(s, page_url, video_url, filepath, progress_cb, dl_id=dl_id)
        if success:
            return True, "", size

        # اولویت ۲: استریم تک‌اتصاله پیوسته
        success, err, size = await _stream_download_response(s, page_url, target_url=video_url, filepath=filepath, progress_cb=progress_cb, dl_id=dl_id)
        if success:
            return True, "", size

    _cleanup_file(filepath)
    return False, err or "Download failed", 0


async def download_spankbang_direct(url, filepath, progress_cb=None, video_url="", quality="high", dl_id=""):
    if not video_url:
        qualities, title, info = await extract_spankbang_qualities(url, progress_cb)
        if not qualities:
            return False, title or "Extraction failed", 0
        selected = qualities[0]
        video_url = selected["url"]
        quality_key = selected.get("quality_key", "")
    else:
        quality_key = quality

    return await download_spankbang_video(url, video_url, filepath, progress_cb, dl_id=dl_id, quality_key=quality_key)


async def _self_test():
    url = "https://spankbang.com/a4y8j/video/teen+petra+kneading+laced+sandals+with+her+bare+toes"
    print(f"\n{'═' * 80}\nSelf-test: {url}\n{'═' * 80}\n")

    async def progress(msg):
        print(f"  → {msg}")

    sources, title, info = await extract_spankbang_qualities(url, progress_cb=progress)
    print(f"\n  Title: {title}")
    print(f"\n  Sources ({len(sources)}):")
    for s in sources:
        print(f"    [{s['quality_key']:6s}] {s['url'][:120]} ({s['method']})")
        
    if sources:
        test_file = r"C:\Users\Administrator\.gemini\antigravity-ide\brain\51dc314e-07ca-412b-8e56-475aac029ca5\scratch\test_spankbang_dl.mp4"
        print(f"\n  Testing download to {test_file}...")
        ok, err, sz = await download_spankbang_direct(url, test_file, progress_cb=progress, video_url=sources[0]["url"])
        print(f"\n  Download result: ok={ok}, err={err}, size={sz/1024/1024:.2f} MB")
        if os.path.exists(test_file):
            os.remove(test_file)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    asyncio.run(_self_test())
