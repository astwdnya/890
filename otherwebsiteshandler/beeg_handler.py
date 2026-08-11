"""
beeg_handler.py
───────────────
استخراج و دانلود ویدیو از beeg.com

ساختار:
  - Beeg یه Vue.js SPA هست
  - HLS playlist از video.beeg.com سرو می‌شه
  - Master M3U8: https://video.beeg.com/key=.../media=hls4A/multi=.../{id}.mp4.m3u8
  - Variant: https://video.beeg.com/key=.../media=hls4A/{quality}p/{id}.mp4.m3u8
  - Segments: https://ip{N}.video.beeg.com/key=.../media=hls4A/.../seg-N-v1-a1.ts
  - URL ها با key و end timestamp امضا شدن
  - بدون Cloudflare (CDN مستقیم)
  - صفحه beeg.com پشت Cloudflare هست ولی CDN نیست

استراتژی:
  1. Playwright برای fetch صفحه و capture M3U8 URL
  2. ffmpeg برای دانلود HLS
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

logger = logging.getLogger("BeegHandler")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

MAX_DOWNLOAD_SIZE = 2 * 1024 * 1024 * 1024
MIN_VALID_VIDEO_SIZE = 100 * 1024
PROGRESS_INTERVAL = 1.0

_ALLOWED_HOSTS = frozenset({"beeg.com", "www.beeg.com", "video.beeg.com"})

ProgressCallback = Callable[[str], Awaitable[None]]


def is_beeg_url(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
        return host in _ALLOWED_HOSTS or host.endswith(".beeg.com")
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
    """استخراج M3U8 URL با Playwright."""
    if not _has_playwright():
        return [], "Playwright not installed", {}

    from playwright.async_api import async_playwright

    if progress_cb:
        await progress_cb("🔄 **Loading page...**")

    sources = []
    title = ""
    info = {}

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                user_agent=_USER_AGENT,
                viewport={"width": 1280, "height": 720},
                locale="en-US",
            )
            page = await context.new_page()

            m3u8_urls = []
            mp4_urls = []

            async def on_response(resp):
                u = str(resp.url)
                if '.m3u8' in u and 'video.beeg.com' in u:
                    m3u8_urls.append(u)
                elif '.mp4' in u and 'preview' not in u.lower() and 'video.beeg.com' in u:
                    mp4_urls.append(u)

            page.on("response", on_response)

            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(15000)

            title = await page.title()
            html = await page.content()

            await browser.close()

        # Parse M3U8 URLs
        seen = set()
        for u in m3u8_urls:
            if u not in seen:
                seen.add(u)
                # Check if it's a master playlist (multi=)
                if 'multi=' in u:
                    sources.append({
                        "label": "📺 MP4 (HLS Master)",
                        "url": u,
                        "height": 720,
                        "quality_key": "720p",
                        "method": "playwright_m3u8_master",
                        "is_hd": True,
                    })
                else:
                    # Variant playlist — extract quality from URL
                    m = re.search(r'/(\d+)p/', u)
                    height = int(m.group(1)) if m else 720
                    sources.append({
                        "label": f"📺 MP4 {height}p (HLS)",
                        "url": u,
                        "height": height,
                        "quality_key": f"{height}p",
                        "method": "playwright_m3u8_variant",
                        "is_hd": height >= 720,
                    })

        # Thumbnail
        m = re.search(r'(?:property|name)=["\']og:image["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
        thumbnail = m.group(1) if m else ""

        info = {"thumbnail": thumbnail, "page_url": url, "fetch_method": "playwright"}

        # Sort by height
        sources.sort(key=lambda q: q.get("height", 0), reverse=True)

        if progress_cb:
            labels = ", ".join(s["label"] for s in sources)
            await progress_cb(f"✅ **پیدا شد:** {title[:50]}\n🎞 کیفیت‌ها: {labels}")

        return sources, title, info

    except Exception as e:
        logger.error(f"Playwright error: {e}", exc_info=True)
        return [], f"Playwright error: {e}", {}


# ─── Download via ffmpeg ─────────────────────────────────────────────────


async def _download_with_ffmpeg(video_url, filepath, progress_cb, dl_id="", referer="https://beeg.com/"):
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


async def extract_beeg_qualities(url, progress_cb=None):
    if not is_beeg_url(url):
        return [], "Invalid URL", {}
    return await _extract_via_playwright(url, progress_cb)


async def download_beeg_video(page_url, video_url, filepath, progress_cb=None, cookies=None, dl_id="", quality_key=""):
    if not is_beeg_url(page_url):
        return False, "URL host not allowed", 0
    if not video_url:
        return False, "Empty video URL", 0
    if progress_cb is None:
        async def _noop(msg): pass
        progress_cb = _noop
    if dl_id and dl_id not in active_downloads:
        active_downloads[dl_id] = {"paused": False, "cancelled": False}

    success, error, size = await _download_with_ffmpeg(video_url, filepath, progress_cb, dl_id=dl_id, referer=page_url)
    if success:
        return True, "", size
    _cleanup_file(filepath)
    return False, error or "Download failed", 0


async def download_beeg_direct(url, filepath, progress_cb=None, video_url="", quality="high", dl_id=""):
    if not video_url:
        qualities, title, info = await extract_beeg_qualities(url, progress_cb)
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
    else:
        qualities, title, info = await extract_beeg_qualities(url, progress_cb)
        quality_key = quality

    return await download_beeg_video(url, video_url, filepath, progress_cb, dl_id=dl_id, quality_key=quality_key)


async def _self_test():
    url = "https://beeg.com/-0763395922267095"
    print(f"\n{'═' * 80}\nSelf-test: {url}\n{'═' * 80}\n")

    async def progress(msg):
        print(f"  → {msg}")

    sources, title, info = await extract_beeg_qualities(url, progress_cb=progress)
    print(f"\n  Title: {title}")
    print(f"\n  Sources ({len(sources)}):")
    for s in sources:
        print(f"    [{s['quality_key']:6s}] {s['url'][:120]} ({s['method']})")
    return sources, title, info


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    asyncio.run(_self_test())
