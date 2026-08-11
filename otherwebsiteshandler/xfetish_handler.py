"""
xfetish_handler.py
──────────────────
استخراج و دانلود ویدیو از x-fetish.tube — مستقل، بدون yt-dlp

ساختار: KT Player
- URL ویدیو در flashvars یا key:value pairs با v-acctoken
- پشت Cloudflare — نیاز به curl_cffi با impersonate=chrome
- نکته کلیدی: صفحه و ویدیو باید با همون session fetch بشن
- URL فقط برای یه request کار می‌کنه (بعدش expire می‌شه)
- پس: fetch صفحه → استخراج URL → download با همون session
"""

import asyncio
import json
import logging
import os
import re
import time
from typing import Awaitable, Callable, List, Optional, Tuple
from urllib.parse import urlparse, urljoin, unquote

import aiofiles
from curl_cffi.requests import AsyncSession

logger = logging.getLogger("XFetishHandler")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

MAX_DOWNLOAD_SIZE = 2 * 1024 * 1024 * 1024
MIN_VALID_VIDEO_SIZE = 100 * 1024
CHUNK_SIZE = 1024 * 1024
PROGRESS_INTERVAL = 1.0

_ALLOWED_HOSTS = frozenset({"x-fetish.tube", "www.x-fetish.tube"})

active_downloads: dict = {}

ProgressCallback = Callable[[str], Awaitable[None]]


def is_xfetish_url(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
        return host in _ALLOWED_HOSTS or host.endswith(".x-fetish.tube")
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
            f"📥 **Downloading...**\n`[{bar}]`\n"
            f"💾 {dl_mb:.1f}/{total_mb:.1f} MB  •  ⚡ {speed_mb:.1f} MB/s\n"
            f"📊 {pct:.1f}%  •  ⏱ ETA: {eta_m}:{eta_s:02d}"
        )
    return f"📥 **Downloading...**\n💾 {dl_mb:.1f} MB  •  ⚡ {speed / 1024 / 1024:.1f} MB/s"


def _is_main_video_url(url: str) -> bool:
    url_lower = url.lower()
    if "preview" in url_lower: return False
    if "_preview" in url_lower: return False
    if "screenshot" in url_lower: return False
    if "/contents/videos_screenshots/" in url_lower: return False
    if "/get_file/" not in url_lower and ".mp4" not in url_lower: return False
    if "_preview.mp4" in url_lower: return False
    # نکته مهم: x-fetish.tube یه GIF placeholder به‌جای ویدیوی اصلی می‌فرسته
    # که URL اون بدون suffix هست (مثلاً 668241.mp4 نه 668241_720p.mp4)
    # ویدیوی واقعی همیشه یه suffix داره: _720p, _1080p, _hd, _fhd, _single_hd
    # URL بدون suffix (668241.mp4) یه GIF 37 بایتی هست!
    filename = url_lower.split("/")[-1].split("?")[0]
    # اگه فقط {id}.mp4 باشه (بدون suffix)، اون رو skip کن
    if re.match(r'^\d+\.mp4$', filename):
        return False
    return True


def _clean_url(url: str) -> str:
    url = unquote(url).replace("&amp;", "&")
    url = re.sub(r'[\\/]+$', '', url)
    return url.rstrip("',\"").strip()


# ─── Extraction ────────────────────────────────────────────────────────────


def _extract_title(html: str) -> str:
    m = re.search(r'(?:property|name)=["\']og:title["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if not m:
        m = re.search(r'content=["\']([^"\']+)["\']\s+(?:property|name)=["\']og:title["\']', html, re.IGNORECASE)
    if m:
        title = m.group(1).strip()
        title = re.sub(r"\s*[-|@]\s*(?:x-fetish\.tube.*)\s*$", "", title, flags=re.IGNORECASE)
        return title or "Untitled"
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    if m:
        title = m.group(1).strip()
        title = re.sub(r"\s*[-|@]\s*(?:x-fetish\.tube.*)\s*$", "", title, flags=re.IGNORECASE)
        return title or "Untitled"
    return "Untitled"


def _extract_thumbnail(html: str) -> str:
    m = re.search(r'(?:property|name)=["\']og:image["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # fallback: preview_url from flashvars/key-values
    m = re.search(r"preview_url\s*:\s*['\"]([^'\"]+)['\"]", html, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


def _extract_duration(html: str) -> Optional[int]:
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


def _extract_flashvars(html: str) -> dict:
    """استخراج flashvars — هم از var flashvars={...} و هم از key:value مستقیم."""
    flashvars = {}

    # روش 1: var flashvars = {...}
    fv_match = re.search(r'var\s+flashvars\s*=\s*\{([^}]+(?:\{[^}]*\}[^{}]*)*)\}', html, re.DOTALL)
    if fv_match:
        block = fv_match.group(0)
        title_match = re.search(r"video_title\s*:\s*'((?:[^'\\]|\\.)*)'", block)
        if title_match:
            flashvars["video_title"] = title_match.group(1).replace("\\'", "'").replace("\\/", "/")
            block = block[:title_match.start()] + block[title_match.end():]
        pairs = re.findall(r"(\w+)\s*:\s*'([^']*)'", block)
        pairs += re.findall(r'(\w+)\s*:\s*"([^"]*)"', block)
        pairs += re.findall(r"(\w+)\s*:\s*([0-9]+)", block)
        for k, v in pairs:
            if k.startswith("//") or k.startswith("/*"): continue
            flashvars[k] = v.replace("\\/", "/").replace("&amp;", "&")
        if flashvars:
            return flashvars

    # روش 2: key:value pairs مستقیم از HTML (بدون flashvars wrapper)
    for key in ["video_url", "video_url_text", "video_alt_url", "video_alt_url_text",
                "video_alt_url2", "video_alt_url2_text", "video_url_fhd", "video_url_hd",
                "postfix", "video_title", "preview_url"]:
        m = re.search(rf"{key}\s*:\s*['\"]([^'\"]*)['\"]", html, re.IGNORECASE)
        if not m:
            m = re.search(rf"{key}\s*:\s*([0-9]+)", html, re.IGNORECASE)
        if m:
            flashvars[key] = m.group(1).replace("\\/", "/").replace("&amp;", "&")

    return flashvars


def _extract_video_sources(html: str) -> List[dict]:
    """استخراج URL های ویدیو از HTML."""
    sources = []
    seen_urls = set()

    # روش 1: v-acctoken URLs
    for m in re.finditer(
        r'(https?://[^\s"\'<>\)\]]+?/get_file/[^\s"\'<>\)\]]+?\.mp4[^\s"\'<>\)\]]*?\?v-acctoken=[a-zA-Z0-9+/=_-]+)',
        html, re.IGNORECASE,
    ):
        url = _clean_url(m.group(1))
        if not _is_main_video_url(url) or url in seen_urls:
            continue
        seen_urls.add(url)
        url_lower = url.lower()
        if "_1080p" in url_lower:
            label, height, qk, is_hd = "📺 MP4 1080p", 1080, "1080p", True
        elif "_720p" in url_lower:
            label, height, qk, is_hd = "📺 MP4 720p", 720, "720p", True
        elif "_hd" in url_lower or "_fhd" in url_lower:
            label, height, qk, is_hd = "📺 MP4 HD", 720, "720p", True
        elif "_480p" in url_lower:
            label, height, qk, is_hd = "📺 MP4 480p", 480, "480p", False
        else:
            label, height, qk, is_hd = "📺 MP4 (default)", 480, "480p", False
        sources.append({"label": label, "url": url, "height": height, "quality_key": qk, "method": "v-acctoken", "is_hd": is_hd})
        logger.info("Found: %s (%s)", qk, url[:100])

    # روش 2: از flashvars (video_url)
    fv = _extract_flashvars(html)
    if fv.get("video_url"):
        url = _clean_url(fv["video_url"])
        if _is_main_video_url(url) and url not in seen_urls:
            seen_urls.add(url)
            is_fhd = fv.get("video_url_fhd") == "1"
            qt = "1080p" if is_fhd else "720p"
            h = 1080 if is_fhd else 720
            sources.append({"label": f"📺 MP4 {qt}", "url": url, "height": h, "quality_key": qt.lower(), "method": "flashvars", "is_hd": True})
            logger.info("Found from flashvars: %s (%s)", qt, url[:100])

    # Deduplicate
    unique = []
    seen_final = set()
    for s in sources:
        if s["url"] not in seen_final:
            seen_final.add(s["url"])
            unique.append(s)

    unique.sort(key=lambda q: q.get("height", 0), reverse=True)
    return unique


# ─── Combined: Fetch page + Extract + Download (same session) ────────────


async def _stream_download_response(s, page_url, target_url, filepath, progress_cb, dl_id=""):
    """دانلود تکه‌ای (Stream) ویدیو با aiter_content تا از تایم‌اوت و مصرف زیاد RAM جلوگیری شود."""
    video_resp = await s.get(target_url, impersonate="chrome", headers={
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": page_url,
    }, allow_redirects=True, timeout=30, stream=True)

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
                    msg = _format_progress(downloaded, content_length, start_time, now)
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


async def _fetch_and_download(page_url, filepath, quality_key, progress_cb, dl_id=""):
    """
    Fetch صفحه، استخراج URL ویدیو، و دانلود استریم با همان session.
    """
    if progress_cb:
        await progress_cb("🔄 **دریافت اطلاعات صفحه...**")

    try:
        async with AsyncSession() as s:
            # 1. Fetch page
            resp = await s.get(page_url, impersonate="chrome", headers={
                "User-Agent": _USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }, allow_redirects=True, timeout=30)

            if resp.status_code != 200:
                return False, f"Page fetch failed: HTTP {resp.status_code}", 0

            html = resp.text
            logger.info("Page fetched: %d bytes", len(html))

            if "Just a moment" in html or "challenge-platform" in html:
                return False, "Cloudflare challenge — cannot bypass", 0

            # 2. Extract video sources
            sources = _extract_video_sources(html)
            if not sources:
                return False, "URL ویدیو در صفحه پیدا نشد", 0

            # ترتیب‌بندی: انتخاب کیفیت درخواستی یا جایگزین‌ها
            ordered_urls = []
            for q in sources:
                if q.get("quality_key") == quality_key:
                    ordered_urls.insert(0, q["url"])
                else:
                    ordered_urls.append(q["url"])

            # 3. تست و دانلود تک‌تک URLها به روش Streaming
            last_err = ""
            for target_url in ordered_urls:
                logger.info("Trying stream URL: %s", target_url[:100])
                if progress_cb:
                    await progress_cb("📥 **Downloading...**")

                success, err, size = await _stream_download_response(s, page_url, target_url, filepath, progress_cb, dl_id=dl_id)
                if success:
                    return True, "", size
                logger.warning("Stream failed for URL %s: %s", target_url[:100], err)
                last_err = err

            return False, last_err or "All stream URLs failed", 0

    except asyncio.CancelledError:
        _cleanup_file(filepath)
        raise
    except Exception as e:
        logger.error(f"Download error: {e}", exc_info=True)
        _cleanup_file(filepath)
        return False, str(e)[:200], 0


# ─── Combined: Fetch page + Extract only (for quality selection) ─────────


async def _fetch_and_extract(page_url, progress_cb=None):
    """Fetch صفحه و استخراج info — با curl_cffi."""
    if progress_cb:
        await progress_cb("🔄 **دریافت اطلاعات صفحه...**")

    try:
        async with AsyncSession() as s:
            resp = await s.get(page_url, impersonate="chrome", headers={
                "User-Agent": _USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }, allow_redirects=True, timeout=30)

            if resp.status_code != 200:
                return [], f"HTTP {resp.status_code}", {}

            html = resp.text
            if "Just a moment" in html or "challenge-platform" in html:
                return [], "Cloudflare challenge", {}

            title = _extract_title(html)
            thumbnail = _extract_thumbnail(html)
            duration = _extract_duration(html)
            sources = _extract_video_sources(html)

            if not sources:
                return [], "URL ویدیو در صفحه پیدا نشد", {}

            # Extract cookies (for info only — actual download uses same session)
            cookies = {}
            try:
                for c in s.cookies.jar:
                    cookies[c.name] = c.value
            except: pass

            if progress_cb:
                labels = ", ".join(s["label"] for s in sources)
                dur_str = ""
                if duration:
                    mins, secs = divmod(duration, 60)
                    dur_str = f" ({mins}:{secs:02d})"
                await progress_cb(f"✅ **پیدا شد:** {title[:50]}{dur_str}\n🎞 کیفیت‌ها: {labels}")

            return sources, title, {
                "thumbnail": thumbnail,
                "page_url": page_url,
                "cookies": cookies,
                "duration": duration,
                "fetch_method": "curl_cffi",
            }
    except Exception as e:
        logger.error(f"Extract error: {e}", exc_info=True)
        return [], str(e), {}


# ─── Public API ────────────────────────────────────────────────────────────


async def extract_xfetish_qualities(url, progress_cb=None):
    """استخراج کیفیت‌های ویدیو."""
    if not is_xfetish_url(url):
        return [], "Invalid URL", {}
    return await _fetch_and_extract(url, progress_cb)


async def download_xfetish_video(page_url, video_url, filepath, progress_cb=None, cookies=None, dl_id="", quality_key=""):
    """دانلود ویدیو — مستقل، بدون yt-dlp.

    نکته: این تابع صفحه رو دوباره fetch می‌کنه تا یه session تازه با URL تازه بگیره.
    video_url پارامتر فقط برای API compatibility هست — در عمل از URL تازه استفاده می‌شه.
    """
    if not is_xfetish_url(page_url):
        return False, "URL host not allowed", 0
    if progress_cb is None:
        async def _noop(msg): pass
        progress_cb = _noop

    # استفاده از _fetch_and_download که صفحه + دانلود رو با همون session انجام می‌ده
    success, error, size = await _fetch_and_download(page_url, filepath, quality_key, progress_cb, dl_id=dl_id)

    if success:
        return True, "", size
    if error == "Cancelled by user":
        return False, error, 0

    # Retry once more with fresh session
    if progress_cb:
        await progress_cb("🔄 **Retry with fresh session...**")
    logger.info("Retrying with fresh session...")
    success, error, size = await _fetch_and_download(page_url, filepath, quality_key, progress_cb, dl_id=dl_id)

    if success:
        return True, "", size
    _cleanup_file(filepath)
    return False, error or "Download failed", 0


async def download_xfetish_direct(url, filepath, progress_cb=None, video_url="", quality="high", dl_id=""):
    """Wrapper برای سازگاری با bot architecture."""
    if not video_url:
        qualities, title, info = await extract_xfetish_qualities(url, progress_cb)
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
        qualities, title, info = await extract_xfetish_qualities(url, progress_cb)
        quality_key = quality

    return await download_xfetish_video(url, video_url, filepath, progress_cb, dl_id=dl_id, quality_key=quality_key)


# ─── Self-test ────────────────────────────────────────────────────────────


async def _self_test():
    test_url = "https://x-fetish.tube/video/667282/lapfetish-productions-heterosexual-vs-lesbian-full-video-kissing-4k/"
    print(f"\n{'═' * 80}")
    print(f"Self-test: {test_url}")
    print(f"{'═' * 80}\n")

    async def progress(msg):
        print(f"  → {msg}")

    sources, title, info = await extract_xfetish_qualities(test_url, progress_cb=progress)
    print(f"\n  Title: {title}")
    print(f"  Duration: {info.get('duration', '?')}s")
    print(f"\n  Sources ({len(sources)}):")
    for s in sources:
        print(f"    [{s['quality_key']:6s}] {s['url'][:120]}")

    # Test download
    if sources:
        output_path = "/home/z/my-project/logs/test_xfetish.mp4"
        import os
        if os.path.exists(output_path): os.remove(output_path)
        print(f"\n  Downloading...")
        success, error, size = await download_xfetish_direct(test_url, output_path, progress_cb=progress, quality="720p")
        print(f"\n  success: {success}")
        print(f"  error: {error}")
        if size: print(f"  size: {_format_size(size)}")
        if success:
            with open(output_path, "rb") as f:
                data = f.read(32)
            if data[4:8] == b'ftyp': print("  ✓ MP4 ftyp!")

    return sources, title, info


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    asyncio.run(_self_test())
