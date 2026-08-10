"""
xfetish_handler.py
──────────────────
استخراج و دانلود ویدیو از x-fetish.tube

ساختار: KT Player (مثل severeporn، whoreshub)
- URL ویدیو در flashvars block با v-acctoken
- معمولاً یه کیفیت (HD vertical یا default)
- پشت Cloudflare — نیاز به curl_cffi با impersonate=chrome
- نیاز به cookies از همون session (PHPSESSID, kt_acctoken)
- Accept-Ranges: bytes ✓

نکته مهم: CDN به TLS fingerprinting حساس هست.
aiohttp به تنهایی 403 می‌گیره. باید از curl_cffi استفاده کنیم
و video URL رو با همون session ای که صفحه رو fetch کرده fetch کنیم.
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
from aiohttp import ClientTimeout, CookieJar, TCPConnector

logger = logging.getLogger("XFetishHandler")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_DEFAULT_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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
    "x-fetish.tube",
    "www.x-fetish.tube",
})

ProgressCallback = Callable[[str], Awaitable[None]]


def is_xfetish_url(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
        return host in _ALLOWED_HOSTS or host.endswith(".x-fetish.tube")
    except Exception:
        return False


def _is_main_video_url(url: str) -> bool:
    url_lower = url.lower()
    if "preview" in url_lower:
        return False
    if "_preview" in url_lower:
        return False
    if "screenshot" in url_lower:
        return False
    if "/contents/videos_screenshots/" in url_lower:
        return False
    if "/get_file/" not in url_lower and ".mp4" not in url_lower:
        return False
    if "_preview.mp4" in url_lower:
        return False
    return True


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
    url = unquote(url).replace("&amp;", "&")
    url = re.sub(r'[\\/]+$', '', url)
    url = url.rstrip("',\"")
    return url.strip()


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


def _check_curl_cffi() -> bool:
    try:
        import curl_cffi  # noqa: F401
        return True
    except ImportError:
        return False


# ─── Extraction ────────────────────────────────────────────────────────────


def _extract_title(html: str) -> str:
    flashvars = _extract_flashvars(html)
    if flashvars.get("video_title"):
        title = flashvars["video_title"].strip()
        title = re.sub(r"\s*[-|@]\s*(?:x-fetish\.tubefetish)\s*$", "", title, flags=re.IGNORECASE)
        if title:
            return title
    m = re.search(r'(?:property|name)=["\']og:title["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if not m:
        m = re.search(r'content=["\']([^"\']+)["\']\s+(?:property|name)=["\']og:title["\']', html, re.IGNORECASE)
    if m:
        title = m.group(1).strip()
        title = re.sub(r"\s*[-|@]\s*(?:x-fetish\.tubefetish)\s*$", "", title, flags=re.IGNORECASE)
        return title
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    if m:
        title = m.group(1).strip()
        title = re.sub(r"\s*[-|@]\s*(?:x-fetish\.tubefetish)\s*$", "", title, flags=re.IGNORECASE)
        return title or "Untitled"
    return "Untitled"


def _extract_thumbnail(html: str) -> str:
    m = re.search(r'(?:property|name)=["\']og:image["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


def _extract_duration(html: str) -> Optional[int]:
    m = re.search(r'"duration"\s*:\s*"(PT[^"]+)"', html)
    if m:
        duration_str = m.group(1)
        h = re.search(r'(\d+)H', duration_str)
        m_min = re.search(r'(\d+)M', duration_str)
        s = re.search(r'(\d+)S', duration_str)
        total = 0
        if h: total += int(h.group(1)) * 3600
        if m_min: total += int(m_min.group(1)) * 60
        if s: total += int(s.group(1))
        return total if total > 0 else None
    return None


def _extract_flashvars(html: str) -> dict:
    """استخراج flashvars block از HTML.

    نکته: بعضی صفحات از var flashvars = {...} استفاده می‌کنن،
    بعضی دیگه فقط key:value pairs دارن بدون flashvars wrapper.
    """
    flashvars = {}

    # روش 1: var flashvars = {...} (standard KT Player)
    fv_match = re.search(r'var\s+flashvars\s*=\s*\{([^}]+(?:\{[^}]*\}[^{}]*)*)\}', html, re.DOTALL)
    if fv_match:
        block = fv_match.group(0)
        title_match = re.search(r"video_title\s*:\s*'((?:[^'\\]|\\.)*)'", block)
        if title_match:
            title_val = title_match.group(1).replace("\\'", "'").replace("\\/", "/").replace("&amp;", "&")
            flashvars["video_title"] = title_val
            block_for_pairs = block[:title_match.start()] + block[title_match.end():]
        else:
            block_for_pairs = block
        pairs = re.findall(r"(\w+)\s*:\s*'([^']*)'", block_for_pairs)
        pairs += re.findall(r'(\w+)\s*:\s*"([^"]*)"', block_for_pairs)
        pairs += re.findall(r"(\w+)\s*:\s*([0-9]+)", block_for_pairs)
        for k, v in pairs:
            if k.startswith("//") or k.startswith("/*"):
                continue
            v_decoded = v.replace("\\/", "/").replace("&amp;", "&")
            flashvars[k] = v_decoded
        return flashvars

    # روش 2: fallback — استخراج مستقیم از HTML بدون flashvars wrapper
    # بعضی صفحات فقط key:value pairs دارن (مثل: video_url: '...', video_url_fhd: '1')
    for key in ["video_url", "video_url_text", "video_alt_url", "video_alt_url_text",
                "video_alt_url2", "video_alt_url2_text", "video_url_fhd", "video_url_hd",
                "postfix", "video_title", "preview_url"]:
        # pattern: key: 'value' یا key: "value" یا key: value
        m = re.search(rf"{key}\s*:\s*['\"]([^'\"]*)['\"]", html, re.IGNORECASE)
        if not m:
            m = re.search(rf"{key}\s*:\s*([0-9]+)", html, re.IGNORECASE)
        if m:
            val = m.group(1).replace("\\/", "/").replace("&amp;", "&")
            flashvars[key] = val

    # اگر چیزی پیدا نشد، از v-acctoken URLs استفاده کن
    if not flashvars:
        vacctoken_pattern = re.compile(
            r'(https?://[^\s"\'<>\)\]]+?/get_file/[^\s"\'<>\)\]]+?\.mp4[^\s"\'<>\)\]]*?\?v-acctoken=[a-zA-Z0-9+/=_-]+)',
            re.IGNORECASE,
        )
        for m in vacctoken_pattern.finditer(html):
            url = m.group(1)
            if "preview" not in url.lower():
                flashvars.setdefault("video_urls", []).append(url)

    return flashvars


def _extract_video_sources(html: str) -> List[dict]:
    sources = []
    seen_urls = set()

    # Method 1: v-acctoken URLs (preferred)
    vacctoken_pattern = re.compile(
        r'(https?://[^\s"\'<>\)\]]+?/get_file/[^\s"\'<>\)\]]+?\.mp4[^\s"\'<>\)\]]*?\?v-acctoken=[a-zA-Z0-9+/=_-]+)',
        re.IGNORECASE,
    )
    for m in vacctoken_pattern.finditer(html):
        url = _clean_url(m.group(1))
        if not _is_main_video_url(url) or url in seen_urls:
            continue
        seen_urls.add(url)
        url_lower = url.lower()
        if "_1080p" in url_lower:
            label, height, quality_key, is_hd = "📺 MP4 1080p", 1080, "1080p", True
        elif "_720p" in url_lower:
            label, height, quality_key, is_hd = "📺 MP4 720p", 720, "720p", True
        elif "_480p" in url_lower:
            label, height, quality_key, is_hd = "📺 MP4 480p", 480, "480p", False
        elif "_360p" in url_lower:
            label, height, quality_key, is_hd = "📺 MP4 360p", 360, "360p", False
        elif "_hd" in url_lower or "_fhd" in url_lower:
            label, height, quality_key, is_hd = "📺 MP4 HD", 720, "720p", True
        else:
            label, height, quality_key, is_hd = "📺 MP4 (default)", 480, "480p", False
        sources.append({
            "label": label, "url": url, "height": height,
            "quality_key": quality_key, "method": "v-acctoken", "is_hd": is_hd,
        })
        logger.info("Found: %s (%s)", quality_key, url[:100])

    # Method 2: from flashvars (video_url)
    flashvars = _extract_flashvars(html)

    # If flashvars has video_urls list (from fallback), use them
    if flashvars.get("video_urls"):
        for url in flashvars["video_urls"]:
            url = _clean_url(url)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            url_lower = url.lower()
            if "_1080p" in url_lower or "_fhd" in url_lower:
                label, height, quality_key, is_hd = "📺 MP4 1080p", 1080, "1080p", True
            elif "_720p" in url_lower or "_hd" in url_lower:
                label, height, quality_key, is_hd = "📺 MP4 720p", 720, "720p", True
            elif "_480p" in url_lower:
                label, height, quality_key, is_hd = "📺 MP4 480p", 480, "480p", False
            else:
                label, height, quality_key, is_hd = "📺 MP4 (default)", 480, "480p", False
            sources.append({
                "label": label, "url": url, "height": height,
                "quality_key": quality_key, "method": "flashvars_fallback", "is_hd": is_hd,
            })
            logger.info("Found via fallback: %s (%s)", quality_key, url[:100])

    # Standard flashvars video_url
    if flashvars.get("video_url"):
        url = _clean_url(flashvars["video_url"])
        if _is_main_video_url(url) and url not in seen_urls:
            seen_urls.add(url)
            is_fhd = flashvars.get("video_url_fhd") == "1"
            quality_text = "1080p" if is_fhd else "720p"
            height = 1080 if is_fhd else 720
            sources.append({
                "label": f"📺 MP4 {quality_text}",
                "url": url, "height": height,
                "quality_key": quality_text.lower(),
                "method": "flashvars", "is_hd": True,
            })
            logger.info("Found from flashvars: %s (%s)", quality_text, url[:100])

    # Deduplicate — sometimes v-acctoken and flashvars give same URL
    unique_sources = []
    seen_final = set()
    for s in sources:
        if s["url"] not in seen_final:
            seen_final.add(s["url"])
            unique_sources.append(s)

    unique_sources.sort(key=lambda q: q.get("height", 0), reverse=True)
    return unique_sources


# ─── Fetch Page ───────────────────────────────────────────────────────────


async def _fetch_page_with_curl_cffi(url):
    """fetch صفحه با curl_cffi — برای CF bypass و cookie persistence."""
    if not _check_curl_cffi():
        return None, None, "curl_cffi not installed"

    from curl_cffi.requests import AsyncSession

    try:
        async with AsyncSession() as session:
            resp = await session.get(
                url, impersonate="chrome",
                headers=_DEFAULT_HEADERS, allow_redirects=True, timeout=30,
            )
            if resp.status_code == 200 and resp.text:
                text = resp.text
                if "video_url" in text or "flashvars" in text or "v-acctoken" in text:
                    logger.info("Page fetched via curl_cffi, size=%d", len(text))
                    # Extract cookies
                    cookies = {}
                    try:
                        for cookie in session.cookies.jar:
                            cookies[cookie.name] = cookie.value
                    except Exception:
                        pass
                    return text, cookies, ""
                logger.warning("curl_cffi: 200 ولی video_url پیدا نشد")
            logger.warning("curl_cffi fetch: HTTP %d", resp.status_code)
    except Exception as e:
        logger.warning(f"curl_cffi fetch error: {e}")
    return None, None, "Failed to fetch page"


async def _fetch_page_aiohttp(url):
    """fetch صفحه با aiohttp (fallback)."""
    try:
        jar = CookieJar(unsafe=True)
        timeout = ClientTimeout(total=30, connect=10)
        async with aiohttp.ClientSession(timeout=timeout, headers=_DEFAULT_HEADERS, cookie_jar=jar) as session:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status == 200:
                    html = await resp.text(errors="replace")
                    if "video_url" in html or "flashvars" in html or "v-acctoken" in html:
                        logger.info("Page fetched via aiohttp, size=%d", len(html))
                        cookies = {}
                        for cookie in jar:
                            cookies[cookie.key] = cookie.value
                        return html, cookies, ""
                    logger.warning("aiohttp: 200 ولی video_url پیدا نشد")
                logger.warning("aiohttp fetch: HTTP %s", resp.status)
    except Exception as e:
        logger.warning(f"aiohttp fetch error: {e}")
    return None, None, "Failed to fetch page"


# ─── Main API: extract qualities ──────────────────────────────────────────


async def extract_xfetish_qualities(url, progress_cb=None):
    if not is_xfetish_url(url):
        return [], "Invalid URL", {}

    if progress_cb:
        await progress_cb("🔄 **دریافت اطلاعات صفحه...**")

    # Try curl_cffi first (for CF bypass)
    html, cookies, error = await _fetch_page_with_curl_cffi(url)

    # Fallback to aiohttp
    if not html:
        html, cookies, error = await _fetch_page_aiohttp(url)

    if not html:
        return [], f"خطا در دریافت صفحه: {error}", {}

    title = _extract_title(html)
    thumbnail = _extract_thumbnail(html)
    duration = _extract_duration(html)
    sources = _extract_video_sources(html)

    if not sources:
        return [], "URL ویدیو در صفحه پیدا نشد", {}

    logger.info("Found %d video sources (cookies: %s)", len(sources), list(cookies.keys()) if cookies else "none")

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
        "cookies": cookies or {},
        "duration": duration,
        "fetch_method": "curl_cffi" if _check_curl_cffi() else "aiohttp",
        "flashvars": _extract_flashvars(html),
    }


# ─── Download: Single connection with curl_cffi ─────────────────────────────


async def _download_single_curl_cffi(url, filepath, referer, cookies, progress_cb, dl_id=""):
    """دانلود با connection واحد با curl_cffi — با همون session که صفحه fetch شده."""
    if not _check_curl_cffi():
        return False, "curl_cffi not installed", 0

    from curl_cffi.requests import AsyncSession

    headers = {
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer,
    }

    await progress_cb("📥 **Downloading...**")

    try:
        # استفاده از session جدید با impersonate و cookies
        async with AsyncSession() as s:
            # Set cookies — باید قبل از request set بشن
            if cookies:
                for name, val in cookies.items():
                    try: s.cookies.set(name, val)
                    except: pass

            # استفاده از impersonate=chrome (مهم برای TLS fingerprinting)
            resp = await s.get(url, impersonate="chrome", headers=headers, allow_redirects=True, timeout=300)

            if resp.status_code != 200:
                # اگر 403 شد، بدون Referer امتحان کن
                if resp.status_code == 403:
                    logger.warning("[DL-XF] 403 with referer, trying without...")
                    no_ref_headers = {k: v for k, v in headers.items() if k != "Referer"}
                    # باز refresh URL
                    return False, "HTTP_403", 0
                return False, f"HTTP {resp.status_code}", 0

            content_length = int(resp.headers.get("Content-Length", 0))
            if content_length > MAX_DOWNLOAD_SIZE:
                return False, f"File too large: {_format_size(content_length)}", 0

            data = resp.content if hasattr(resp, "content") else resp.body
            if not data or len(data) < MIN_VALID_VIDEO_SIZE:
                return False, f"File too small ({len(data) if data else 0} bytes)", 0

            async with aiofiles.open(filepath, "wb") as f:
                await f.write(data)

            size = len(data)
            logger.info(f"[DL-XF] Single curl_cffi DONE | size={_format_size(size)}")
            return True, "", size

    except asyncio.CancelledError:
        _cleanup_file(filepath)
        raise
    except Exception as e:
        logger.error(f"[DL-XF] Single curl_cffi error: {e}", exc_info=True)
        _cleanup_file(filepath)
        return False, str(e)[:200], 0


async def _download_with_page_session(page_url, video_url, filepath, quality_key, progress_cb, dl_id=""):
    """Fetch صفحه و دانلود ویدیو با همون session.

    نکته مهم: x-fetish.tube نیاز داره که صفحه و ویدیو با همون session fetch بشن.
    اگه session جدید ساخته بشه، cookies درست کار نمی‌کنن و 403 می‌گیریم.
    """
    if not _check_curl_cffi():
        return False, "curl_cffi not installed", 0

    from curl_cffi.requests import AsyncSession

    try:
        async with AsyncSession() as s:
            # 1. Fetch page (for cookies)
            if progress_cb:
                await progress_cb("🔄 **Fetching page for session...**")

            resp = await s.get(page_url, impersonate="chrome", headers={
                "User-Agent": _USER_AGENT,
                "Accept": "text/html,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }, allow_redirects=True, timeout=30)

            if resp.status_code != 200:
                return False, f"Page fetch failed: HTTP {resp.status_code}", 0

            html = resp.text
            logger.info(f"[DL-XF] Page fetched: {len(html)} bytes")

            # 2. Extract video URL from HTML
            sources = _extract_video_sources(html)
            if not sources:
                return False, "No video sources found", 0

            # Select quality
            target_url = None
            for q in sources:
                if q.get("quality_key") == quality_key:
                    target_url = q["url"]
                    break
            if not target_url:
                target_url = sources[0]["url"]

            logger.info(f"[DL-XF] Using URL: {target_url[:100]}")

            # 3. Download video with same session (cookies persist!)
            if progress_cb:
                await progress_cb("📥 **Downloading...**")

            video_resp = await s.get(target_url, impersonate="chrome", headers={
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": page_url,
            }, allow_redirects=True, timeout=300)

            if video_resp.status_code != 200:
                return False, f"Video download: HTTP {video_resp.status_code}", 0

            data = video_resp.content if hasattr(video_resp, "content") else video_resp.body
            if not data or len(data) < MIN_VALID_VIDEO_SIZE:
                return False, f"File too small ({len(data) if data else 0} bytes)", 0

            async with aiofiles.open(filepath, "wb") as f:
                await f.write(data)

            size = len(data)
            logger.info(f"[DL-XF] Download DONE | size={_format_size(size)}")
            return True, "", size

    except asyncio.CancelledError:
        _cleanup_file(filepath)
        raise
    except Exception as e:
        logger.error(f"[DL-XF] Download error: {e}", exc_info=True)
        _cleanup_file(filepath)
        return False, str(e)[:200], 0


# ─── Download: Multi-segment ──────────────────────────────────────────────


active_downloads: dict = {}


async def _download_multi_segment(direct_url, filepath, referer, cookies, progress_cb, dl_id="", num_workers=MULTI_SEGMENT_WORKERS):
    """Multi-segment download — uses curl_cffi for TLS fingerprinting."""
    if not _check_curl_cffi():
        return False, "curl_cffi not installed (required for x-fetish.tube)", 0

    from curl_cffi.requests import AsyncSession

    try:
        cdn_headers = {
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": referer,
        }

        content_length = 0
        accept_ranges = ""

        # Probe with Range
        try:
            probe_headers = dict(cdn_headers)
            probe_headers["Range"] = "bytes=0-1023"
            async with AsyncSession() as s:
                # Set cookies
                if cookies:
                    for name, val in cookies.items():
                        try: s.cookies.set(name, val)
                        except: pass
                resp = await s.get(direct_url, impersonate="chrome", headers=probe_headers, allow_redirects=True, timeout=15)
                if resp.status_code in (200, 206):
                    if resp.status_code == 206:
                        accept_ranges = "bytes"
                        cr = resp.headers.get("Content-Range", "")
                        m = re.search(r"/(\d+)", cr)
                        if m: content_length = int(m.group(1))
                    else:
                        content_length = int(resp.headers.get("Content-Length", 0))
                elif resp.status_code == 403:
                    return False, "HTTP_403", 0
        except Exception as e:
            logger.warning(f"Probe request failed: {e}")

        if content_length == 0:
            return False, "Cannot determine file size", 0
        if content_length > MAX_DOWNLOAD_SIZE:
            return False, f"File too large: {_format_size(content_length)}", 0
        if accept_ranges != "bytes" or content_length < MULTI_SEGMENT_MIN_SIZE:
            return False, "Range not supported or file too small", 0

        total_mb = content_length / 1024 / 1024
        await progress_cb(f"📥 **Downloading...**\n💾 Size: {total_mb:.1f} MB\n🔥 {num_workers} parallel workers")

        CHUNK_SIZE_BYTES = MULTI_SEGMENT_CHUNK_SIZE
        chunks = []
        offset = 0
        chunk_idx = 0
        while offset < content_length:
            end = min(offset + CHUNK_SIZE_BYTES - 1, content_length - 1)
            chunks.append((chunk_idx, offset, end))
            offset = end + 1
            chunk_idx += 1

        total_chunks = len(chunks)
        logger.info(f"[DL-XF] Work-queue: {total_chunks} chunks, {num_workers} workers, total={content_length}")

        try:
            async with aiofiles.open(filepath, "wb") as f:
                await f.truncate(content_length)
        except Exception as e:
            logger.warning(f"Could not pre-allocate file: {e}")

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
                    f"📥 **Downloading...**\n`[{bar}]`\n"
                    f"💾 {dl_mb:.1f}/{total_mb_local:.1f} MB  •  ⚡ {speed_mb:.1f} MB/s\n"
                    f"📊 {pct:.1f}%  •  ⏱ ETA: {eta_m}:{eta_s:02d}\n"
                    f"📦 {completed_chunks[0]}/{total_chunks} chunks • 🔥 {num_workers}x"
                )
            except Exception:
                pass

        # Use curl_cffi shared session for all workers
        shared_session = AsyncSession()
        # Set cookies on shared session
        if cookies:
            for name, val in cookies.items():
                try: shared_session.cookies.set(name, val)
                except: pass

        shared_file = await aiofiles.open(filepath, "r+b")

        async def _download_worker(worker_id):
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
                        resp = await shared_session.get(
                            direct_url,
                            impersonate="chrome",
                            headers={**cdn_headers, "Range": f"bytes={byte_start}-{byte_end}"},
                            allow_redirects=True,
                            timeout=120,
                        )
                        try:
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
                        finally:
                            try: resp.close()
                            except: pass
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.warning(f"[DL-XF] W{worker_id} c{c_idx} attempt {attempt+1} failed: {e}")
                        if attempt < MAX_RETRIES - 1:
                            await asyncio.sleep(RETRY_DELAY * (attempt + 1))
                        else:
                            failed_chunks.append((c_idx, str(e)[:100]))
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

            real_failures = []
            for i, r in enumerate(results):
                if r is True: continue
                if isinstance(r, Exception): real_failures.append(f"worker{i}: {r}")
                elif r is False: real_failures.append(f"worker{i}: returned False")
            if real_failures or failed_chunks:
                logger.warning(f"[DL-XF] {len(real_failures)} worker failures, {len(failed_chunks)} chunks failed")
                _cleanup_file(filepath)
                return False, f"Multi-segment failed: {len(real_failures)+len(failed_chunks)} chunks", 0

        except Exception as e:
            logger.error(f"[DL-XF] Work-queue error: {e}", exc_info=True)
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
        logger.info(f"[DL-XF] Multi-segment DONE | size={_format_size(file_size)} | time={elapsed:.1f}s | avg_speed={avg_speed:.1f} MB/s")
        return True, "", file_size
    except Exception as e:
        logger.error(f"[DL-XF] Multi-segment error: {e}", exc_info=True)
        _cleanup_file(filepath)
        return False, str(e)[:200], 0


# ─── Download: yt-dlp fallback ─────────────────────────────────────────────


async def _download_with_ytdlp(url, filepath, progress_cb, quality_key=""):
    if not shutil.which("yt-dlp"):
        return False, "yt-dlp not installed", 0
    await progress_cb("📥 **Fallback: yt-dlp...**")
    try:
        cmd = [
            "yt-dlp", "--no-warnings", "--progress", "--newline",
            "--no-check-certificates", "-f", "best",
            "--concurrent-fragments", "16", "--retries", "10", "--fragment-retries", "10",
            "--buffer-size", "16K", "--max-filesize", str(MAX_DOWNLOAD_SIZE),
            "--add-header", f"User-Agent:{_USER_AGENT}",
            "--add-header", f"Referer:https://x-fetish.tube/",
            "--extractor-args", "generic:impersonate",
            "-o", filepath, url,
        ]
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
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
            try: os.rename(actual_path, filepath)
            except OSError: pass
        logger.info(f"[DL-XF] yt-dlp DONE | size={_format_size(size)}")
        return True, "", size
    except asyncio.CancelledError:
        _cleanup_file(filepath)
        raise
    except Exception as e:
        logger.error(f"[DL-XF] yt-dlp error: {e}", exc_info=True)
        _cleanup_file(filepath)
        return False, str(e)[:200], 0


# ─── Public API ────────────────────────────────────────────────────────────


async def download_xfetish_video(page_url, video_url, filepath, progress_cb=None, cookies=None, dl_id="", quality_key=""):
    if not is_xfetish_url(page_url):
        return False, "URL host not allowed", 0
    if not video_url:
        return False, "Empty video URL", 0
    if progress_cb is None:
        async def _noop(msg): pass
        progress_cb = _noop
    if dl_id and dl_id not in active_downloads:
        active_downloads[dl_id] = {"paused": False, "cancelled": False}
    referer = page_url
    if not cookies:
        cookies = {}

    # ── روش 1: fetch صفحه + download با همون session ──
    # نکته مهم: x-fetish.tube نیاز داره که صفحه و ویدیو با همون session fetch بشن.
    logger.info("[DL-XF] Attempt 1: single-session download (page + video)")
    success, error, size = await _download_with_page_session(page_url, video_url, filepath, quality_key, progress_cb, dl_id=dl_id)
    if success:
        return True, "", size
    if error == "Cancelled by user":
        return False, error, 0
    logger.info(f"[DL-XF] Single-session failed: {error}")
    _cleanup_file(filepath)

    # ── روش 2: yt-dlp ──
    logger.info("[DL-XF] Attempt 2: yt-dlp on page URL")
    success, error, size = await _download_with_ytdlp(page_url, filepath, progress_cb, quality_key=quality_key)
    if success:
        return True, "", size
    if error == "Cancelled by user":
        return False, error, 0
    if error == "HTTP_403":
        logger.info("[DL-XF] 403, refreshing session...")
        if progress_cb:
            await progress_cb("🔄 **Refreshing session...**")
        try:
            new_sources, _, new_info = await extract_xfetish_qualities(page_url, progress_cb=None)
            if new_sources:
                new_video_url = None
                for q in new_sources:
                    if q.get("quality_key") == quality_key:
                        new_video_url = q["url"]
                        break
                if not new_video_url:
                    new_video_url = new_sources[0]["url"]
                video_url = new_video_url
                new_cookies = new_info.get("cookies", {})
                cookies.update(new_cookies)
                logger.info("[DL-XF] Got fresh URL")
        except Exception as e:
            logger.warning(f"[DL-XF] refresh failed: {e}")
        success, error, size = await _download_multi_segment(video_url, filepath, referer, cookies, progress_cb, dl_id=dl_id)
        if success:
            return True, "", size
    logger.info(f"[DL-XF] Multi-segment failed: {error}")
    _cleanup_file(filepath)

    # ─ـ روش 2: yt-dlp ──
    logger.info("[DL-XF] Attempt 2: yt-dlp on page URL")
    success, error, size = await _download_with_ytdlp(page_url, filepath, progress_cb, quality_key=quality_key)
    if success:
        return True, "", size
    _cleanup_file(filepath)
    return False, error or "All download methods failed", 0


async def download_xfetish_direct(url, filepath, progress_cb=None, video_url="", quality="high", dl_id=""):
    if not video_url:
        qualities, title, info = await extract_xfetish_qualities(url, progress_cb)
        if not qualities:
            return False, title or "Extraction failed", 0
        selected = qualities[0]  # x-fetish معمولاً یه کیفیت داره
        video_url = selected["url"]
        quality_key = selected.get("quality_key", "")
        cookies = info.get("cookies", {})
    else:
        qualities, title, info = await extract_xfetish_qualities(url, progress_cb)
        cookies = info.get("cookies", {}) if info else {}
        quality_key = quality
    return await download_xfetish_video(url, video_url, filepath, progress_cb, cookies=cookies, dl_id=dl_id, quality_key=quality_key)


# ─── Self-test ────────────────────────────────────────────────────────────


async def _self_test():
    test_url = "https://x-fetish.tube/video/668241/footking17s-aka-footking17s-05-04-2024-onlyfans-video-leaked-footking17s-50362/"
    print(f"\n{'═' * 80}")
    print(f"Self-test: {test_url}")
    print(f"{'═' * 80}\n")

    async def progress(msg):
        print(f"  → {msg}")

    sources, title, info = await extract_xfetish_qualities(test_url, progress_cb=progress)
    print(f"\n  Title: {title}")
    print(f"  Duration: {info.get('duration', '?')}s")
    print(f"  Cookies: {list(info.get('cookies', {}).keys())}")
    print(f"\n  Sources ({len(sources)}):")
    for s in sources:
        print(f"    [{s['quality_key']:6s}] {s['url'][:120]} ({s['method']})")
    return sources, title, info


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    asyncio.run(_self_test())
