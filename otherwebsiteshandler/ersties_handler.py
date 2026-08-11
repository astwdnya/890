"""
ersties_handler.py
───────────────────
استخراج و دانلود ویدیو از ersties.com

روش کار (بر اساس تحلیل واقعی):

  ─── ساختار صفحه ───
  ersties.com یه Vue.js SPA هست. صفحه HTML فقط یه shell خالی با <div id="app"></div> هست.
  محتوا از طریق API load می‌شه.

  ─── API ───
  GET https://api.ersties.com/track/visit?referer=&lang=en&visit_uid=&w={campaign}
  → JSON response با ساختار:
    {
      "page": {
        "id": 1185,
        "title": "POP THE BALLOON",
        "language": "en",
        "videos": [...],
        "hero_videos": [...],
        "currently_on_site": {
          "hls_url": "https://cdn.ersties.com/.../master.m3u8",
          ...
        }
      }
    }

  نکته: API از بعضی IP ها timeout می‌خوره (geo-restricted).
  باید از Playwright برای fetch صفحه استفاده کنیم.

  ─── ساختار URL HLS ───
  https://cdn.ersties.com/upload/CMS/freearea_video/recoded/,360/{name}_360p.mp4,480/{name}_480p.mp4,720/{name}_720p.mp4,1080/{name}_1080p.mp4,.urlset/master.m3u8

  ─── سرور ───
  - ersties.com: پشت Cloudflare
  - cdn.ersties.com: CDN بدون Cloudflare (بدون نیاز به referer!)
  - Accept-Ranges: bytes ✓
  - HLS: EVENT type با VOD segments

کیفیت‌ها:
  - 360p, 480p, 720p, 1080p

استراتژی:
  1. fetch صفحه با Playwright (برای SPA rendering و API capture)
  2. استخراج HLS URL از API response
  3. fetch master.m3u8 با aiohttp (CDN بدون CF)
  4. ffmpeg برای دانلود HLS و تبدیل به MP4
  5. fallback به yt-dlp

وابستگی‌ها:
    pip install aiohttp aiofiles curl_cffi playwright yt-dlp
    playwright install chromium
    apt install ffmpeg
"""

import asyncio
import json
import logging
import os
import re
import shutil
import time
from typing import Awaitable, Callable, List, Optional, Tuple
from urllib.parse import urlparse, urljoin, unquote

import aiofiles
import aiohttp
from aiohttp import ClientTimeout

logger = logging.getLogger("ErstiesHandler")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# ─── Constants ─────────────────────────────────────────────────────────────

MAX_DOWNLOAD_SIZE = 50 * 1024 * 1024 * 1024  # 2 GB
MIN_VALID_VIDEO_SIZE = 100 * 1024  # 100 KB
PROGRESS_INTERVAL = 1.0

_ALLOWED_HOSTS = frozenset({
    "ersties.com",
    "www.ersties.com",
    "en.ersties.com",
})

_ALLOWED_CDN_HOSTS = frozenset({
    "cdn.ersties.com",
    "cdn-cf.ersties.com",
    "thumb.ersties.com",
})

ProgressCallback = Callable[[str], Awaitable[None]]


# ─── Utility ────────────────────────────────────────────────────────────────


def is_ersties_url(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
        return host in _ALLOWED_HOSTS or host.endswith(".ersties.com")
    except Exception:
        return False


def _extract_page_id(url: str) -> str:
    """استخراج page ID از URL.
    
    مثال: https://ersties.com/welcome/1185?w=5552.hot → 1185
    """
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    # /welcome/1185
    parts = path.split("/")
    for part in parts:
        if part.isdigit():
            return part
    # Try query params
    for part in parts:
        if part and not part.startswith("?"):
            # Maybe it's in the path
            m = re.search(r'(\d+)', part)
            if m:
                return m.group(1)
    return ""


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


def _check_curl_cffi() -> bool:
    try:
        import curl_cffi  # noqa: F401
        return True
    except ImportError:
        return False


def _has_playwright() -> bool:
    try:
        from playwright.async_api import async_playwright  # noqa: F401
        return True
    except ImportError:
        return False


# ─── Extraction via Playwright ────────────────────────────────────────────


async def _extract_via_playwright(url: str, progress_cb: Optional[ProgressCallback] = None) -> Tuple[List[dict], str, dict]:
    """
    استخراج HLS URLs از ersties.com با Playwright.

    صفحه یه Vue.js SPA هست و نیاز به JavaScript execution داره.
    API response حاوی hls_url برای هر ویدیو هست.

    Returns:
        (sources, title, info)
    """
    if not _has_playwright():
        return [], "Playwright not installed", {}

    from playwright.async_api import async_playwright

    if progress_cb:
        await progress_cb("🔄 **Loading page with Playwright...**")

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

            # Capture API responses
            api_data = [None]

            async def on_response(resp):
                if "api.ersties.com/track/visit" in resp.url:
                    try:
                        text = await resp.text()
                        api_data[0] = text
                    except Exception:
                        pass

            page.on("response", on_response)

            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            # Wait longer for API response (SPA needs time to load)
            for i in range(30):
                if api_data[0]:
                    break
                await page.wait_for_timeout(2000)

            await browser.close()

        if not api_data[0]:
            return [], "API response not captured", {}

        data = json.loads(api_data[0])
        page_data = data.get("page", {})
        title = page_data.get("title", "Untitled")

        if progress_cb:
            await progress_cb(f"✅ Page loaded: {title}")

        # Extract video URLs
        seen_urls = set()

        # Method 1: from page.videos
        videos = page_data.get("videos", [])
        if isinstance(videos, list):
            for vid in videos:
                if isinstance(vid, dict):
                    hls_url = vid.get("hls_url", "")
                    if hls_url and hls_url not in seen_urls:
                        seen_urls.add(hls_url)
                        vid_title = vid.get("title", title)
                        sources.append({
                            "label": f"📺 MP4 {vid_title[:30]}",
                            "url": hls_url,
                            "height": 720,
                            "quality_key": "720p",
                            "method": "playwright_api",
                            "is_hd": True,
                            "video_title": vid_title,
                        })

        # Method 2: from page.currently_on_site
        cos = page_data.get("currently_on_site", {})
        if isinstance(cos, dict):
            hls_url = cos.get("hls_url", "")
            if hls_url and hls_url not in seen_urls:
                seen_urls.add(hls_url)
                sources.append({
                    "label": f"📺 MP4 (currently on site)",
                    "url": hls_url,
                    "height": 720,
                    "quality_key": "720p",
                    "method": "playwright_api",
                    "is_hd": True,
                    "video_title": title,
                })

        # Method 3: from page.hero_videos
        hero_videos = page_data.get("hero_videos", [])
        if isinstance(hero_videos, list):
            for vid in hero_videos:
                if isinstance(vid, dict):
                    hls_url = vid.get("hls_url", "")
                    if hls_url and hls_url not in seen_urls:
                        seen_urls.add(hls_url)
                        vid_title = vid.get("title", "Hero video")
                        sources.append({
                            "label": f"📺 MP4 {vid_title[:30]}",
                            "url": hls_url,
                            "height": 720,
                            "quality_key": "720p",
                            "method": "playwright_api",
                            "is_hd": True,
                            "video_title": vid_title,
                        })

        # For each HLS URL, try to parse master.m3u8 for actual qualities
        if sources:
            # Fetch master.m3u8 for first source to get qualities
            try:
                master_content = await _fetch_m3u8(sources[0]["url"])
                variants = _parse_master_playlist(master_content, sources[0]["url"])
                if variants:
                    # Replace sources with properly parsed qualities
                    new_sources = []
                    for v in variants:
                        new_sources.append({
                            "label": f"📺 MP4 {v['height']}p",
                            "url": v["url"],
                            "height": v["height"],
                            "quality_key": f"{v['height']}p",
                            "method": "playwright_api",
                            "is_hd": v["height"] >= 720,
                            "video_title": title,
                        })
                    new_sources.sort(key=lambda q: q.get("height", 0), reverse=True)
                    sources = new_sources
            except Exception as e:
                logger.warning(f"Failed to parse master.m3u8: {e}")

        info = {
            "thumbnail": "",
            "page_url": url,
            "page_id": page_data.get("id"),
            "title": title,
            "fetch_method": "playwright",
        }

        logger.info("Found %d video sources via Playwright", len(sources))
        return sources, title, info

    except Exception as e:
        logger.error(f"Playwright extraction error: {e}", exc_info=True)
        return [], f"Playwright error: {e}", {}


# ─── HLS parsing ──────────────────────────────────────────────────────────


async def _fetch_m3u8(url: str) -> str:
    """Fetch M3U8 playlist content."""
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "*/*",
    }
    try:
        from curl_cffi.requests import AsyncSession
        async with AsyncSession() as s:
            resp = await s.get(url, impersonate="chrome", headers=headers, allow_redirects=True, timeout=15)
            if resp.status_code == 200:
                return resp.text
            raise RuntimeError(f"M3U8 fetch failed: HTTP {resp.status_code}")
    except ImportError:
        timeout = ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as s:
            async with s.get(url, allow_redirects=True) as resp:
                if resp.status == 200:
                    return await resp.text(errors="replace")
                raise RuntimeError(f"M3U8 fetch failed: HTTP {resp.status}")


def _parse_master_playlist(content: str, base_url: str) -> List[dict]:
    """Parse master.m3u8 و استخراج variant playlists."""
    variants = []
    lines = content.strip().split("\n")
    base_dir = base_url.rsplit("/", 1)[0] + "/"

    for i, line in enumerate(lines):
        line = line.strip()
        if line.startswith("#EXT-X-STREAM-INF:"):
            if i + 1 < len(lines):
                variant_url = lines[i + 1].strip()
                attrs = {}
                attr_str = line[len("#EXT-X-STREAM-INF:"):]
                for m in re.finditer(r'(\w+)=(?:"([^"]+)"|([^,]+))', attr_str):
                    attrs[m.group(1)] = m.group(2) or m.group(3)

                if variant_url.startswith("/"):
                    parsed = urlparse(base_url)
                    variant_url = f"{parsed.scheme}://{parsed.netloc}{variant_url}"
                elif not variant_url.startswith("http"):
                    variant_url = urljoin(base_dir, variant_url)

                resolution = attrs.get("RESOLUTION", "")
                try:
                    height = int(resolution.split("x")[1]) if "x" in resolution else 0
                except (ValueError, IndexError):
                    height = 0

                variants.append({
                    "url": variant_url,
                    "resolution": resolution,
                    "height": height,
                    "bandwidth": int(attrs.get("BANDWIDTH", 0)),
                    "codecs": attrs.get("CODECS", ""),
                })

    variants.sort(key=lambda v: v["height"], reverse=True)
    return variants


def _select_variant(variants: List[dict], quality: str = "720p") -> dict:
    if not variants:
        raise RuntimeError("No variants available")
    if quality == "best":
        return variants[0]
    if quality == "worst":
        return variants[-1]
    try:
        target = int(quality.replace("p", ""))
    except ValueError:
        target = 720
    for v in variants:
        if v["height"] == target:
            return v
    best = variants[-1]
    for v in variants:
        if v["height"] <= target and v["height"] >= best["height"]:
            best = v
    return best


# ─── Main API: extract qualities ──────────────────────────────────────────


async def extract_ersties_qualities(url, progress_cb=None):
    """استخراج کیفیت‌های ویدیو."""
    if not is_ersties_url(url):
        return [], "Invalid URL — host not allowed", {}

    # Use Playwright for extraction
    sources, title, info = await _extract_via_playwright(url, progress_cb)

    if not sources:
        logger.error("No video sources found")
        return [], title or "URL ویدیو در صفحه پیدا نشد", {}

    if progress_cb:
        labels = ", ".join(s["label"] for s in sources)
        await progress_cb(f"✅ **پیدا شد:** {title[:50]}\n🎞 کیفیت‌ها: {labels}")

    return sources, title, info


# ─── Download via ffmpeg ─────────────────────────────────────────────────


active_downloads: dict = {}


async def _download_with_ffmpeg(hls_url, filepath, progress_cb, dl_id="", duration_hint=0):
    """دانلود HLS با ffmpeg."""
    if not shutil.which("ffmpeg"):
        return False, "ffmpeg not installed", 0

    await progress_cb("📥 **Downloading via ffmpeg (HLS)...**")

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel", "warning",
        "-user_agent", _USER_AGENT,
        "-i", hls_url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        "-movflags", "+faststart",
        filepath,
    ]

    logger.info("[DL-ER] ffmpeg cmd: %s", " ".join(cmd[:8]) + " ...")

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

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
                        # Try to parse time from ffmpeg
                        time_match = re.search(r'time=(\d+):(\d+):(\d+\.\d+)', text)
                        if time_match and duration_hint > 0:
                            h, m, s = int(time_match.group(1)), int(time_match.group(2)), float(time_match.group(3))
                            current_time = h * 3600 + m * 60 + s
                            pct = (current_time / duration_hint) * 100
                            filled = int(pct / 5)
                            bar = "█" * filled + "░" * (20 - filled)
                            try:
                                await progress_cb(
                                    f"📥 **Downloading (HLS)...**\n`[{bar}]`\n"
                                    f"💾 {dl_mb:.1f} MB  •  ⚡ {speed_mb:.1f} MB/s\n"
                                    f"📊 {pct:.1f}%"
                                )
                            except Exception:
                                pass
                        else:
                            try:
                                await progress_cb(
                                    f"📥 **Downloading (HLS)...**\n"
                                    f"💾 {dl_mb:.1f} MB  •  ⚡ {speed_mb:.1f} MB/s"
                                )
                            except Exception:
                                pass
            except asyncio.CancelledError:
                process.kill()
                raise
            except Exception as e:
                logger.warning(f"[DL-ER] stderr read error: {e}")

        stderr_task = asyncio.create_task(_read_stderr())
        await process.wait()
        try:
            stderr_task.cancel()
        except Exception:
            pass

        if active_downloads.get(dl_id, {}).get("cancelled"):
            _cleanup_file(filepath)
            return False, "Cancelled by user", 0

        if process.returncode != 0:
            err_tail = "\n".join(stderr_buffer[-5:])[:300]
            logger.error(f"[DL-ER] ffmpeg failed (rc={process.returncode}): {err_tail}")
            _cleanup_file(filepath)
            return False, f"ffmpeg failed: {err_tail[:200]}", 0

        file_size = os.path.getsize(filepath)
        if file_size < MIN_VALID_VIDEO_SIZE:
            _cleanup_file(filepath)
            return False, f"File too small ({file_size} bytes)", 0

        elapsed = time.time() - start_time
        avg_speed = file_size / elapsed / 1024 / 1024 if elapsed > 0 else 0
        logger.info(
            "[DL-ER] ffmpeg DONE | size=%s | time=%.1fs | avg_speed=%.1f MB/s",
            _format_size(file_size), elapsed, avg_speed,
        )
        return True, "", file_size

    except asyncio.CancelledError:
        _cleanup_file(filepath)
        raise
    except Exception as e:
        logger.error(f"[DL-ER] ffmpeg error: {e}", exc_info=True)
        _cleanup_file(filepath)
        return False, str(e)[:200], 0


# ─── Download via yt-dlp (fallback) ──────────────────────────────────────


async def _download_with_ytdlp(url, filepath, progress_cb, quality_key=""):
    if not shutil.which("yt-dlp"):
        return False, "yt-dlp not installed", 0
    await progress_cb("📥 **Fallback: yt-dlp...**")
    format_selector = "best"
    if quality_key in ("720p", "480p", "1080p", "360p"):
        height = quality_key.replace("p", "")
        format_selector = f"[height<={height}]/best"
    try:
        cmd = [
            "yt-dlp", "--no-warnings", "--progress", "--newline",
            "--no-check-certificates", "-f", format_selector,
            "-N", "32", "--concurrent-fragments", "32",
            "--retries", "10", "--fragment-retries", "10",
            "--max-filesize", str(MAX_DOWNLOAD_SIZE),
            "--add-header", f"User-Agent:{_USER_AGENT}",
            "-o", filepath,
            url,
        ]
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
                    pct_match = re.search(r"(\d+\.?\d*)%", text)
                    if pct_match:
                        pct = pct_match.group(1)
                        try:
                            pct_num = float(pct)
                            filled = int(pct_num / 5)
                            bar = "█" * filled + "░" * (20 - filled)
                        except (ValueError, TypeError):
                            bar = "░" * 20
                        await progress_cb(f"📥 **Downloading (yt-dlp)...**\n`[{bar}]`\n📊 {pct}%")
        await process.wait()
        if process.returncode != 0:
            stderr = (await process.stderr.read()).decode(errors="replace")
            return False, stderr[-200:], 0
        actual_path = filepath
        if not os.path.exists(actual_path):
            base, _ = os.path.splitext(filepath)
            for ext in (".mp4", ".mkv", ".webm", ".ts"):
                candidate = base + ext
                if os.path.exists(candidate):
                    actual_path = candidate
                    break
        if not os.path.exists(actual_path):
            return False, "Output file not found", 0
        size = os.path.getsize(actual_path)
        if size < MIN_VALID_VIDEO_SIZE:
            _cleanup_file(actual_path)
            return False, f"File too small ({size} bytes)", 0
        if actual_path != filepath:
            try:
                os.rename(actual_path, filepath)
            except OSError:
                pass
        logger.info(f"[DL-ER] yt-dlp DONE | size={_format_size(size)}")
        return True, "", size
    except asyncio.CancelledError:
        _cleanup_file(filepath)
        raise
    except Exception as e:
        logger.error(f"[DL-ER] yt-dlp error: {e}", exc_info=True)
        _cleanup_file(filepath)
        return False, str(e)[:200], 0


# ─── Public API ────────────────────────────────────────────────────────────


async def download_ersties_video(
    page_url, hls_url, filepath, progress_cb=None, cookies=None, dl_id="",
    quality_key="", duration_hint=0,
):
    """دانلود ویدیو از ersties با HLS URL."""
    if not is_ersties_url(page_url):
        return False, "URL host not allowed", 0
    if not hls_url:
        return False, "Empty HLS URL", 0
    if progress_cb is None:
        async def _noop(msg): pass
        progress_cb = _noop
    if dl_id and dl_id not in active_downloads:
        active_downloads[dl_id] = {"paused": False, "cancelled": False}

    # ── روش 1: ffmpeg (بهترین برای HLS) ──
    logger.info("[DL-ER] Attempt 1: ffmpeg (HLS)")
    success, error, size = await _download_with_ffmpeg(
        hls_url, filepath, progress_cb, dl_id=dl_id,
        duration_hint=duration_hint,
    )
    if success:
        return True, "", size
    if error == "Cancelled by user":
        return False, error, 0
    logger.info(f"[DL-ER] ffmpeg failed: {error}")
    _cleanup_file(filepath)

    # ─ـ روش 2: yt-dlp ──
    logger.info("[DL-ER] Attempt 2: yt-dlp on HLS URL")
    success, error, size = await _download_with_ytdlp(
        hls_url, filepath, progress_cb, quality_key=quality_key,
    )
    if success:
        return True, "", size
    _cleanup_file(filepath)
    return False, error or "All download methods failed", 0


async def download_ersties_direct(
    url, filepath, progress_cb=None, video_url="", quality="720p", dl_id="",
):
    """Wrapper برای سازگاری با bot architecture."""
    if not video_url:
        qualities, title, info = await extract_ersties_qualities(url, progress_cb)
        if not qualities:
            return False, title or "Extraction failed", 0
        selected = None
        for q in qualities:
            if q.get("quality_key") == quality:
                selected = q
                break
        if not selected:
            if quality in ("high", "best", "1080p"):
                hd = [q for q in qualities if q.get("is_hd")]
                selected = hd[0] if hd else qualities[0]
            elif quality in ("low", "worst"):
                selected = qualities[-1]
            else:
                selected = qualities[0]
        video_url = selected["url"]
        quality_key = selected.get("quality_key", "")
    else:
        qualities, title, info = await extract_ersties_qualities(url, progress_cb)
        quality_key = quality

    return await download_ersties_video(
        url, video_url, filepath, progress_cb,
        dl_id=dl_id, quality_key=quality_key,
    )


# ─── Self-test ────────────────────────────────────────────────────────────


async def _self_test():
    """تست خودي هندلر."""
    test_url = "https://ersties.com/welcome/1185?w=5552.hot"
    print(f"\n{'═' * 80}")
    print(f"Self-test: {test_url}")
    print(f"{'═' * 80}\n")

    async def progress(msg):
        print(f"  → {msg}")

    sources, title, info = await extract_ersties_qualities(test_url, progress_cb=progress)

    print(f"\n  Title: {title}")
    print(f"  Page ID: {info.get('page_id')}")
    print(f"\n  Sources ({len(sources)}):")
    for s in sources:
        print(f"    [{s['quality_key']:6s}] {s['url'][:120]}")

    return sources, title, info


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(_self_test())
