"""
spankbang_handler.py
────────────────────
استخراج و دانلود ویدیو از spankbang.com

ساختار:
  - SpankBang پشت Cloudflare محکم هست
  - aiohttp, curl_cffi, yt-dlp همگی 403 می‌گیرن
  - فقط Playwright با anti-detection می‌تونه صفحه رو لود کنه
  - ویدیو URL ها در JS embedded هستن (m3u8 یا mp4)
  - بعد از لود صفحه، URL ویدیو از network capture یا HTML استخراج می‌شه

استراتژی:
  1. Playwright برای fetch صفحه (CF bypass)
  2. Capture network requests برای m3u8/mp4 URLs
  3. ffmpeg برای دانلود HLS یا multi-segment برای MP4
"""

import asyncio
import json
import logging
import os
import re
import shutil
import time
from typing import Awaitable, Callable, List, Optional, Tuple
from urllib.parse import urlparse, urljoin

import aiofiles
import aiohttp
from aiohttp import ClientTimeout

logger = logging.getLogger("SpankBangHandler")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

MAX_DOWNLOAD_SIZE = 2 * 1024 * 1024 * 1024
MIN_VALID_VIDEO_SIZE = 100 * 1024
PROGRESS_INTERVAL = 1.0

_ALLOWED_HOSTS = frozenset({"spankbang.com", "www.spankbang.com", "m.spankbang.com"})

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


# ─── Extraction via Playwright ────────────────────────────────────────────


async def _extract_via_playwright(url: str, progress_cb=None) -> Tuple[List[dict], str, dict]:
    """استخراج ویدیو URL با Playwright (CF bypass)."""
    if not _has_playwright():
        return [], "Playwright not installed", {}

    from playwright.async_api import async_playwright

    if progress_cb:
        await progress_cb("🔄 **Loading page (CF bypass)...**")

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
            # Anti-detection
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

            # Wait for CF to resolve
            for i in range(30):
                await page.wait_for_timeout(2000)
                title_check = await page.title()
                if "Just a moment" not in title_check:
                    break

            title = await page.title()
            logger.info(f"Page title: {title}")

            html = await page.content()
            logger.info(f"Page size: {len(html)}")

            # Check if CF still blocking
            if "Just a moment" in title or "Just a moment" in html:
                await browser.close()
                return [], "Cloudflare challenge — cannot bypass", {}

            if progress_cb:
                await progress_cb("🔎 **Extracting video URLs...**")

            # From network capture
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
                    # Try to determine quality from URL
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

            # Also search in HTML for stream URLs
            html_m3u8 = re.findall(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', html)
            for u in html_m3u8:
                if u not in seen:
                    seen.add(u)
                    sources.append({
                        "label": "📺 MP4 (HLS from HTML)",
                        "url": u,
                        "height": 720,
                        "quality_key": "720p",
                        "method": "html_m3u8",
                        "is_hd": True,
                    })

            html_mp4 = re.findall(r'https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*', html)
            for u in html_mp4:
                if 'preview' not in u.lower() and u not in seen:
                    seen.add(u)
                    sources.append({
                        "label": "📺 MP4 (from HTML)",
                        "url": u,
                        "height": 720,
                        "quality_key": "720p",
                        "method": "html_mp4",
                        "is_hd": True,
                    })

            # Thumbnail
            m = re.search(r'(?:property|name)=["\']og:image["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
            thumbnail = m.group(1) if m else ""

            await browser.close()

        info = {"thumbnail": thumbnail, "page_url": url, "fetch_method": "playwright"}

        if progress_cb:
            labels = ", ".join(s["label"] for s in sources)
            await progress_cb(f"✅ **پیدا شد:** {title[:50]}\n🎞 کیفیت‌ها: {labels}")

        return sources, title, info

    except Exception as e:
        logger.error(f"Playwright error: {e}", exc_info=True)
        return [], f"Playwright error: {e}", {}


# ─── Download via ffmpeg ─────────────────────────────────────────────────


async def _download_with_ffmpeg(video_url, filepath, progress_cb, dl_id="", referer="https://spankbang.com/"):
    if not shutil.which("ffmpeg"):
        return False, "ffmpeg not installed", 0

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
                        try:
                            await progress_cb(f"📥 **Downloading...**\n💾 {dl_mb:.1f} MB  •  ⚡ {speed_mb:.1f} MB/s")
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


active_downloads: dict = {}


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

    # ffmpeg برای HLS یا MP4
    success, error, size = await _download_with_ffmpeg(video_url, filepath, progress_cb, dl_id=dl_id, referer=page_url)
    if success:
        return True, "", size
    _cleanup_file(filepath)
    return False, error or "Download failed", 0


async def download_spankbang_direct(url, filepath, progress_cb=None, video_url="", quality="high", dl_id=""):
    if not video_url:
        qualities, title, info = await extract_spankbang_qualities(url, progress_cb)
        if not qualities:
            return False, title or "Extraction failed", 0
        selected = qualities[0]
        video_url = selected["url"]
        quality_key = selected.get("quality_key", "")
    else:
        qualities, title, info = await extract_spankbang_qualities(url, progress_cb)
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
    return sources, title, info


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    asyncio.run(_self_test())
