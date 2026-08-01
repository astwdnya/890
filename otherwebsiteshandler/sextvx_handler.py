"""
sextvx_handler.py
─────────────────
استخراج و دانلود ویدیو از sextvx.com

روش کار (بر اساس تحلیل واقعی سایت — تأیید شده با تست):

  سایت تحت محافظت Cloudflare Challenge است.
  نکته مهم: curl_cffi با impersonate=chrome به 403 می‌خوره، ولی safari17_0 کار می‌کنه.
  CDN (str7.sextvx.com) هم به TLS fingerprint حساسه — باید همه‌جا از safari17_0 استفاده کنیم.

  ساختار:
    - صفحه‌ی ویدیو: /video/<id>/<slug>
    - سایت از FluidPlayer (HTML5) استفاده می‌کنه
    - ویدیو داخل <video id="video-player"><source src="..."></video> embed شده
    - URL الگو: https://www.sextvx.com/flux?&d=<id>_<quality>p.mp4&s=<server>&p=<params>
    - این URL به یه CDN امضا شده redirect (302) می‌شه:
        https://str7.sextvx.com/k/<token>/<timestamp>/p/<path>/<id>_<quality>.mp4
    - توکن ~6 ساعت معتبره

  Subdomain normalization (مهم):
    - sextvx.com → www.sextvx.com (redirect خودکار)
    - m.sextvx.com → www.sextvx.com (نسخه موبایل، صفحه ناقص)
    - www.sextvx.com → www.sextvx.com (target)
    handler همیشه URL رو به www.sextvx.com تبدیل می‌کنه تا صفحه‌ی کامل desktop رو بگیره.

  CDN (str7.sextvx.com):
    - Accept-Ranges: bytes (HTTP 206)
    - Content-Type: video/mp4
    - Server: nginx
    - Referer الزامی (بدون Referer کار می‌کنه ولی fake Referer → 403)
    - TLS fingerprint حساس — فقط safari17_0 کار می‌کنه
    - توکن 6 ساعت معتبر

  نکات:
    - سایت چندین کیفیت داره (240p, 360p, 480p, 720p, 1080p)
    - handler همه کیفیت‌ها رو استخراج می‌کنه و کاربر می‌تونه انتخاب کنه
    - کوکی set نمی‌شه (یا مهم نیست)

استراتژی دانلود:
  1. normalize URL به www.sextvx.com
  2. GET parent page با safari17_0 برای دور زدن Cloudflare
  3. استخراج <source> tags از <video>
  4. multi-segment download با N workers (پیش‌فرض 16) با safari17_0
  5. fallback به single-connection
  6. fallback به yt-dlp روی page URL
  7. در 403: re-fetch parent page (شاید URL عوض شده)

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

logger = logging.getLogger("SEXTVXHandler")

# ─── Constants ────────────────────────────────────────────────────────────

MAX_DOWNLOAD_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB
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

# دامنه‌های مجاز (شامل subdomain ها)
_ALLOWED_HOSTS = frozenset({
    "sextvx.com",
    "www.sextvx.com",
    "m.sextvx.com",
})

# host نرمال‌شده (هدف)
_NORMAL_HOST = "www.sextvx.com"

# CDN host pattern
_CDN_HOST_SUFFIX = ".sextvx.com"

# توکن 6 ساعت معتبره — ما 5 ساعت در نظر می‌گیریم برای margin
_TOKEN_REFRESH_THRESHOLD = 5 * 3600  # seconds

# impersonation profile (safari17_0 برای دور زدن CF و CDN)
_IMPERSONATE = "safari17_0"

# User-Agent متناسب با safari17_0
_SAFARI_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)

# هدرهای پیش‌فرض
_DEFAULT_HEADERS = {
    "User-Agent": _SAFARI_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# هدرهای مخصوص دانلود از CDN
_CDN_HEADERS = {
    "User-Agent": _SAFARI_UA,
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "identity",
    "Sec-Fetch-Dest": "video",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "same-site",
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


def is_sextvx_url(url: str) -> bool:
    """بررسی اینکه URL مربوط به sextvx.com (با هر subdomain) هست."""
    try:
        host = urlparse(url).hostname or ""
        return host in _ALLOWED_HOSTS or host.endswith(".sextvx.com")
    except Exception:
        return False


def normalize_sextvx_url(url: str) -> str:
    """
    نرمال‌سازی URL به www.sextvx.com.

    تبدیل‌ها:
        sextvx.com → www.sextvx.com
        m.sextvx.com → www.sextvx.com
        www.sextvx.com → www.sextvx.com (بدون تغییر)

    این کار برای گرفتن صفحه‌ی کامل desktop انجام می‌شه.
    نسخه‌ی m. صفحه‌ی ناقصی داره و quality switching در اون پشتیبانی نمی‌شه.
    """
    if not url:
        return url
    try:
        parsed = urlparse(url)
        # ساخت URL جدید با host نرمال‌شده
        new_netloc = _NORMAL_HOST
        # preserve port اگه بود (معمولاً نیست)
        if ":" in parsed.netloc:
            port = parsed.netloc.rsplit(":", 1)[1]
            new_netloc = f"{_NORMAL_HOST}:{port}"
        # ساخت URL جدید
        new_parsed = parsed._replace(netloc=new_netloc, scheme="https")
        return new_parsed.geturl()
    except Exception:
        return url


def extract_video_id(url: str) -> Optional[int]:
    """
    استخراج video_id از URL.

    الگو: /video/<id>/<slug>
    """
    try:
        parsed = urlparse(url)
        path = parsed.path or ""
        m = re.search(r"/video/(\d+)", path)
        if m:
            return int(m.group(1))
        return None
    except (ValueError, TypeError):
        return None


def extract_video_slug(url: str) -> Optional[str]:
    """استخراج slug از URL."""
    try:
        parsed = urlparse(url)
        path = parsed.path or ""
        m = re.search(r"/video/\d+/([^/]+)/?", path)
        if m:
            return m.group(1)
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
    # HTML entities (مثل &amp;)
    url = html_unescape(url)
    url = unquote(url)
    url = re.sub(r"[\\/]+$", "", url)
    url = url.rstrip("',\"")
    return url.strip()


def html_unescape(s: str) -> str:
    """ساده‌سازی HTML entities."""
    s = s.replace("&amp;", "&")
    s = s.replace("&lt;", "<")
    s = s.replace("&gt;", ">")
    s = s.replace("&quot;", '"')
    s = s.replace("&#39;", "'")
    s = s.replace("&apos;", "'")
    return s


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


def _check_curl_cffi() -> bool:
    return _HAS_CURL_CFFI


def _parse_time_string(s: str) -> Optional[int]:
    """تبدیل "12:34" یا "1:23:45" به ثانیه."""
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


# ─── Page Metadata Extraction ─────────────────────────────────────────────


def _strip_site_suffix(title: str) -> str:
    """حذف پسوندهای سایت مثل ' - SEXTVX.COM'."""
    title = re.sub(r"\s*[-|@]\s*sextvx(?:\.com)?\s*$", "", title, flags=re.IGNORECASE)
    return title.strip()


def _extract_title(html: str) -> str:
    """استخراج عنوان ویدیو."""
    # روش 1: og:title
    m = re.search(
        r'(?:property|name)=["\']og:title["\']\s+content=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    )
    if m:
        return _strip_site_suffix(m.group(1).strip())

    # روش 2: <h1>
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


def _extract_thumbnail(html: str) -> str:
    """استخراج URL تصویر بندانگشتی."""
    m = re.search(
        r'(?:property|name)=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    return ""


def _extract_duration(html: str) -> Optional[int]:
    """استخراج مدت زمان ویدیو (به ثانیه)."""
    # روش 1: ISO 8601 در JSON-LD
    m = re.search(r'"duration"\s*:\s*"(PT[^"]+)"', html)
    if m:
        d = _parse_iso8601_duration(m.group(1))
        if d:
            return d

    # روش 2: og:video:duration
    m = re.search(
        r'(?:property|name)=["\'](?:og:video:duration|video:duration)["\']\s+content=["\'](\d+)["\']',
        html, re.IGNORECASE,
    )
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return None


def _parse_iso8601_duration(s: str) -> Optional[int]:
    """تبدیل PT1H30M45S → 5445 (ثانیه)."""
    if not s:
        return None
    total = 0
    for unit, mult in [("H", 3600), ("M", 60), ("S", 1)]:
        m = re.search(rf"(\d+){unit}", s)
        if m:
            total += int(m.group(1)) * mult
    return total if total > 0 else None


def _extract_video_sources(html: str) -> List[dict]:
    """
    استخراج URL های ویدیو از <source> tags داخل <video>.

    الگوی HTML:
        <video id="video-player" class="vplayer" controls>
            <source src='https://www.sextvx.com/flux?&amp;d=510380_360p.mp4&amp;s=7&amp;p=5,1,0,3,8,510380' title='360p' type='video/mp4' />
            <source src='...' title='240p' type='video/mp4' />
        </video>

    Returns:
        list of dicts: [{label, url, height, quality_key, method, is_hd}, ...]
    """
    sources: List[dict] = []
    seen_urls: set = set()

    # روش 1: پیدا کردن <video id="video-player"> و سپس <source> داخلش
    video_match = re.search(
        r'<video\b[^>]*\bid=["\']video-player["\'][^>]*>(.*?)</video>',
        html, re.IGNORECASE | re.DOTALL,
    )
    video_block = ""
    if video_match:
        video_block = video_match.group(1)
    else:
        # fallback: هر <video> با class vplayer
        video_match = re.search(
            r'<video\b[^>]*\bclass=["\'][^"\']*vplayer[^"\']*["\'][^>]*>(.*?)</video>',
            html, re.IGNORECASE | re.DOTALL,
        )
        if video_match:
            video_block = video_match.group(1)
        else:
            # fallback آخر: کل HTML
            video_block = html

    # پیدا کردن همه <source> tags
    for sm in re.finditer(
        r'<source\b([^>]*)>',
        video_block, re.IGNORECASE,
    ):
        attrs_str = sm.group(1)
        # استخراج src (می‌تونه با ' یا " باشه)
        src = _extract_attr(attrs_str, "src")
        if not src:
            continue
        url = _clean_url(src)
        if not url or url in seen_urls:
            continue
        # فقط URL های flux یا mp4
        if "flux" not in url.lower() and ".mp4" not in url.lower():
            continue
        seen_urls.add(url)

        title_attr = _extract_attr(attrs_str, "title") or ""
        # تشخیص کیفیت از title یا URL
        quality_text, height, is_hd = _detect_quality(url, title_attr)

        label = f"📺 MP4 {quality_text}" if quality_text != "default" else "📺 MP4 (default)"

        sources.append({
            "label": label,
            "url": url,
            "height": height,
            "quality_key": quality_text,
            "method": "source_tag",
            "is_hd": is_hd,
        })
        logger.info("Found source (%s): %s", quality_text, url[:100])

    # اگه هیچ source ای پیدا نشد، fallback به جستجوی مستقیم /flux URLs
    if not sources:
        flux_pattern = re.compile(
            r'(https?://[^\s"\'<>)\]]*sextvx\.com/flux\?[^\s"\'<>)\]]+)',
            re.IGNORECASE,
        )
        for m in flux_pattern.finditer(html):
            url = _clean_url(m.group(1))
            if url in seen_urls:
                continue
            seen_urls.add(url)
            # استخراج کیفیت از URL
            quality_text, height, is_hd = _detect_quality(url, "")
            label = f"📺 MP4 {quality_text}" if quality_text != "default" else "📺 MP4 (default)"
            sources.append({
                "label": label,
                "url": url,
                "height": height,
                "quality_key": quality_text,
                "method": "flux_regex",
                "is_hd": is_hd,
            })
            logger.info("Found source via regex (%s): %s", quality_text, url[:100])

    # مرتب‌سازی: بالاترین کیفیت اول
    sources.sort(key=lambda q: q.get("height", 0), reverse=True)
    return sources


def _extract_attr(attrs_str: str, attr_name: str) -> Optional[str]:
    """استخراج مقدار یک attribute از رشته attributes."""
    for pattern in [
        rf'\b{attr_name}\s*=\s*"([^"]*)"',
        rf"\b{attr_name}\s*=\s*'([^']*)'",
    ]:
        m = re.search(pattern, attrs_str, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def _detect_quality(url: str, title: str = "") -> Tuple[str, int, bool]:
    """
    تشخیص کیفیت از URL یا title.

    الگوی URL: /flux?&d=<id>_<quality>p.mp4
    مثال: 510380_360p.mp4 → 360p

    Returns:
        (quality_text, height, is_hd)
    """
    combined = f"{url} {title}".lower()

    # اولویت 1: title دقیق
    if title:
        m = re.search(r"(\d{3,4})p", title.lower())
        if m:
            h = int(m.group(1))
            return f"{h}p", h, h >= 720

    # اولویت 2: بررسی الگوی <id>_<quality>p در URL
    # مثال: 510380_360p.mp4 یا 510380_360pp.mp4
    m = re.search(r"_(\d{3,4})p+\.mp4", combined)
    if m:
        h = int(m.group(1))
        return f"{h}p", h, h >= 720

    # اولویت 3: بررسی کیفیت‌های استاندارد
    for h in (2160, 1440, 1080, 720, 480, 360, 240, 144):
        if f"{h}p" in combined:
            return f"{h}p", h, h >= 720

    return "default", 0, False


# ─── Fetch Page ───────────────────────────────────────────────────────────


async def _fetch_page(url: str, referer: Optional[str] = None) -> Tuple[Optional[str], str]:
    """
    GET یک صفحه‌ی HTML از sextvx.com.

    نکته مهم: سایت تحت Cloudflare Challenge است.
    curl_cffi با impersonate=chrome به 403 می‌خوره، ولی safari17_0 کار می‌کنه.

    Returns:
        (html, error)
    """
    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer

    if _check_curl_cffi():
        try:
            async with _CurlAsyncSession() as session:
                resp = await session.get(
                    url, impersonate=_IMPERSONATE,
                    headers=headers, allow_redirects=True, timeout=30,
                )
                if resp.status_code == 200 and resp.text:
                    text = resp.text
                    # صحت‌سنجی: نباید "Just a moment" داشته باشه (CF challenge)
                    if "Just a moment" in text or "_cf_chl_opt" in text:
                        logger.warning("curl_cffi safari: still got CF challenge")
                    elif "<html" in text.lower() or "<video" in text.lower() or "flux" in text.lower():
                        logger.info("Page fetched via curl_cffi safari: %s (size=%d)", url, len(text))
                        return text, ""
                    else:
                        logger.warning("curl_cffi safari: 200 ولی محتوای مورد انتظار پیدا نشد")
                else:
                    logger.warning("curl_cffi safari fetch %s: HTTP %s", url, resp.status_code)
        except Exception as e:
            logger.warning(f"curl_cffi safari fetch error for {url}: {e}")

    # fallback به aiohttp (معمولاً fail می‌شه به دلیل CF)
    try:
        timeout = ClientTimeout(total=30, connect=10)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status == 200:
                    html = await resp.text(errors="replace")
                    if "Just a moment" not in html and "_cf_chl_opt" not in html:
                        logger.info("Page fetched via aiohttp: %s (size=%d)", url, len(html))
                        return html, ""
                    logger.warning("aiohttp: got CF challenge")
                else:
                    logger.warning("aiohttp fetch %s: HTTP %s", url, resp.status)
    except Exception as e:
        logger.warning(f"aiohttp fetch error for {url}: {e}")

    return None, "Failed to fetch page (Cloudflare challenge)"


# ─── Extract Qualities (Public API) ───────────────────────────────────────


async def extract_sextvx_qualities(
    url: str,
    progress_cb: Optional[ProgressCallback] = None,
) -> Tuple[List[dict], str, dict]:
    """
    استخراج کیفیت‌های ویدیو از sextvx.com.

    Args:
        url: URL صفحه ویدیو (مثل https://sextvx.com/video/<id>/<slug>)
             subdomain های m., www., یا بدون subdomain هم قبول می‌شه.
        progress_cb: callback async برای گزارش پیشرفت

    Returns:
        (sources, title, info)
        sources: list of dicts با کلیدهای label/url/height/quality_key/method/is_hd
        title: عنوان ویدیو
        info: dict با کلیدهای thumbnail/page_url/video_id/slug
    """
    if not is_sextvx_url(url):
        return [], "Invalid URL (host not allowed)", {}

    # normalize URL به www.sextvx.com
    normalized_url = normalize_sextvx_url(url)
    if normalized_url != url:
        logger.info("Normalized URL: %s → %s", url, normalized_url)

    video_id = extract_video_id(url)
    if not video_id:
        return [], "Invalid URL (could not extract video_id)", {}

    slug = extract_video_slug(url)

    if progress_cb:
        await progress_cb("🔄 **دریافت اطلاعات صفحه...**")

    # مرحله 1: GET parent page (با safari17_0 برای دور زدن CF)
    html, page_error = await _fetch_page(normalized_url)
    if not html:
        return [], f"خطا در دریافت صفحه: {page_error}", {}

    # مرحله 2: استخراج metadata + video sources
    title = _extract_title(html)
    description = _extract_description(html)
    duration = _extract_duration(html)
    thumbnail = _extract_thumbnail(html)

    sources = _extract_video_sources(html)
    if not sources:
        logger.error("No video source found in page")
        return [], "URL ویدیو در صفحه پیدا نشد", {}

    if progress_cb:
        labels = ", ".join(s["label"] for s in sources)
        dur_str = ""
        if duration:
            mins, secs = divmod(duration, 60)
            dur_str = f" ({mins}:{secs:02d})"
        await progress_cb(f"✅ **پیدا شد:** {title[:50]}{dur_str}\n🎞 کیفیت‌ها: {labels}")

    return sources, title, {
        "thumbnail": thumbnail,
        "page_url": normalized_url,
        "original_url": url,
        "video_id": video_id,
        "video_slug": slug,
        "description": description,
        "duration": duration,
        "fetch_method": "curl_cffi_safari" if _check_curl_cffi() else "aiohttp",
        "source_type": "video/mp4",
    }


# ─── Download: Probe (HEAD/Range) ─────────────────────────────────────────


async def _probe_size(url: str, referer: str) -> Tuple[int, str, str]:
    """
    Probe کردن سایز واقعی فایل و پشتیبانی از Range.

    نکته: URL /flux به CDN redirect می‌شه، پس allow_redirects=True لازمه.

    Returns:
        (content_length, accept_ranges, error)
    """
    headers = {
        **_CDN_HEADERS,
        "Referer": referer,
    }

    # روش 1: curl_cffi HEAD با safari17_0
    if _check_curl_cffi():
        try:
            async with _CurlAsyncSession() as session:
                resp = await session.head(
                    url, impersonate=_IMPERSONATE,
                    headers=headers, allow_redirects=True, timeout=15,
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
            async with s.head(url, allow_redirects=True) as r:
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
                    url, impersonate=_IMPERSONATE,
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
            f"📥 **Downloading...**\n💾 Size: {total_mb:.1f} MB\n🔥 {num_workers} parallel workers"
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
            "[DL-SEXTVX] Work-queue: %d chunks, %d workers, total=%d",
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
                    f"📥 **Downloading...**\n`[{bar}]`\n"
                    f"💾 {dl_mb:.1f}/{total_mb_local:.1f} MB  •  ⚡ {speed_mb:.1f} MB/s\n"
                    f"📊 {pct:.1f}%  •  ⏱ ETA: {eta_m}:{eta_s:02d}\n"
                    f"📦 {completed_chunks[0]}/{total_chunks} chunks • 🔥 {num_workers}x"
                )
            except Exception:
                pass

        # هدرهای مخصوص دانلود
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
            logger.error("[DL-SEXTVX] Work-queue error: %s", e, exc_info=True)
            _cleanup_file(filepath)
            return False, str(e)[:200], 0

        if _is_cancelled(dl_id):
            _cleanup_file(filepath)
            return False, "Cancelled by user", 0

        worker_failures = [r for r in results if r is not True]
        if worker_failures or failed_chunks:
            logger.warning(
                "[DL-SEXTVX] %d workers failed, %d chunks failed",
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
            "[DL-SEXTVX] Multi-segment DONE | size=%s | time=%.1fs | avg=%.1f MB/s",
            _format_size(file_size), elapsed, avg_speed,
        )
        return True, "", file_size

    except Exception as e:
        logger.error("[DL-SEXTVX] Multi-segment error: %s", e, exc_info=True)
        _cleanup_file(filepath)
        return False, str(e)[:200], 0


async def _make_worker_task(worker_func, worker_id: int):
    """Wrapper برای اجرای worker با exception handling."""
    try:
        return await worker_func(worker_id)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("[DL-SEXTVX] Worker %d crashed: %s", worker_id, e)
        return False


def _make_curl_worker(direct_url, filepath, headers,
                       chunk_queue, downloaded_bytes, completed_chunks,
                       failed_chunks, progress_lock, file_write_lock,
                       first_chunk_started, update_progress, dl_id):
    """ساخت worker function برای دانلود با curl_cffi (با safari17_0)."""

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
                            direct_url, impersonate=_IMPERSONATE,
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
                        "[DL-SEXTVX] W%d c%d attempt %d failed: %s",
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
                        "[DL-SEXTVX] W%d c%d attempt %d failed: %s",
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
    headers = {
        **_CDN_HEADERS,
        "Referer": referer,
    }

    if _check_curl_cffi():
        try:
            t0 = time.time()
            async with _CurlAsyncSession() as session:
                resp = await session.get(
                    url, impersonate=_IMPERSONATE, headers=headers,
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
                elapsed = time.time() - t0
                logger.info(
                    "[DL-SEXTVX] Single (curl_cffi) DONE | size=%s | time=%.1fs",
                    _format_size(size), elapsed,
                )
                return True, "", size

        except Exception as e:
            logger.warning(f"[DL-SEXTVX] Single (curl_cffi) error: {e}")
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


async def _download_with_ytdlp(url: str, filepath: str, progress_cb: ProgressCallback) -> Tuple[bool, str, int]:
    """fallback نهایی با yt-dlp روی page URL."""
    if not shutil.which("yt-dlp"):
        return False, "yt-dlp not installed", 0
    has_curl_cffi = _check_curl_cffi()
    await progress_cb("📥 **Fallback: yt-dlp...**")

    try:
        cmd = [
            "yt-dlp", "--no-warnings", "--progress", "--newline",
            "--no-check-certificates", "-f", "best",
            "--concurrent-fragments", "16",
            "--retries", "10", "--fragment-retries", "10",
            "--buffer-size", "16K",
            "--max-filesize", str(MAX_DOWNLOAD_SIZE),
            "--add-header", f"User-Agent:{_SAFARI_UA}",
            "--add-header", f"Referer:https://www.sextvx.com/",
            "-o", filepath,
        ]
        if has_curl_cffi:
            cmd.extend(["--impersonate", "safari"])
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
        logger.info("[DL-SEXTVX] yt-dlp DONE | size=%s", _format_size(size))
        return True, "", size

    except asyncio.CancelledError:
        _cleanup_file(filepath)
        raise
    except Exception as e:
        logger.error("[DL-SEXTVX] yt-dlp error: %s", e, exc_info=True)
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
        f"📥 **Downloading...**\n`[{bar}]`\n"
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


async def download_sextvx_video(
    page_url: str,
    video_url: str,
    filepath: str,
    progress_cb: Optional[ProgressCallback] = None,
    dl_id: str = "",
) -> Tuple[bool, str, int]:
    """
    دانلود ویدیو از sextvx.com.

    Args:
        page_url: URL صفحه ویدیو (برای Referer)
        video_url: URL مستقیم MP4 (از extract_sextvx_qualities)
        filepath: مسیر فایل خروجی
        progress_cb: callback async برای گزارش پیشرفت
        dl_id: شناسه download برای cancellation support

    Returns:
        (success, error, size)

    نکته مهم:
        CDN به Referer حساسه — handler خودکار Referer رو set می‌کنه.
        URL /flux به CDN redirect می‌شه، پس allow_redirects=True لازمه.
    """
    if not is_sextvx_url(page_url):
        return False, "URL host not allowed", 0
    if not video_url:
        return False, "Empty video URL", 0

    if progress_cb is None:
        async def _noop(msg: str) -> None: pass
        progress_cb = _noop

    if dl_id:
        _get_state(dl_id)

    # normalize URL برای Referer (همیشه به www.sextvx.com)
    referer = normalize_sextvx_url(page_url)

    # ─ـ مرحله 1: multi-segment ──
    logger.info("[DL-SEXTVX] Attempt 1: multi-segment (%d workers)", DEFAULT_SEGMENT_WORKERS)
    success, error, size = await _download_multi_segment(
        video_url, filepath, referer, progress_cb, dl_id=dl_id,
    )
    if success:
        return True, "", size

    if error == "Cancelled by user":
        return False, error, 0

    # اگه 403، صفحه رو دوباره fetch کن
    if error in ("HTTP_403", "Cannot determine file size"):
        logger.info("[DL-SEXTVX] Refreshing page...")
        if progress_cb:
            await progress_cb("🔄 **Refreshing session...**")
        fresh_html, _ = await _fetch_page(referer)
        if fresh_html:
            fresh_sources = _extract_video_sources(fresh_html)
            if fresh_sources:
                video_url = fresh_sources[0]["url"]
                logger.info("[DL-SEXTVX] Got fresh URL")
                success, error, size = await _download_multi_segment(
                    video_url, filepath, referer, progress_cb, dl_id=dl_id,
                )
                if success:
                    return True, "", size

    logger.info(f"[DL-SEXTVX] Multi-segment failed: {error}")
    _cleanup_file(filepath)

    # ─ـ مرحله 2: single-connection ──
    logger.info("[DL-SEXTVX] Attempt 2: single-connection")
    success, error, size = await _download_single(
        video_url, filepath, referer, progress_cb, dl_id=dl_id,
    )
    if success:
        return True, "", size
    logger.info(f"[DL-SEXTVX] Single failed: {error}")
    _cleanup_file(filepath)

    # ─ـ مرحله 3: yt-dlp روی page URL ──
    logger.info("[DL-SEXTVX] Attempt 3: yt-dlp on page URL")
    success, error, size = await _download_with_ytdlp(referer, filepath, progress_cb)
    if success:
        return True, "", size
    _cleanup_file(filepath)
    return False, error or "All download methods failed", 0


# ─── Wrapper (سازگار با bot architecture) ─────────────────────────────────


async def download_sextvx_direct(
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
        url: URL صفحه ویدیو (با هر subdomain)
        filepath: مسیر فایل خروجی
        progress_cb: callback async
        video_url: اگه از قبل استخراج شده
        quality: 'high' | 'low' | '<quality_key>' (مثل '720p', '360p')
        dl_id: شناسه برای cancellation
    """
    if not video_url:
        qualities, title, info = await extract_sextvx_qualities(url, progress_cb)
        if not qualities:
            return False, title or "Extraction failed", 0
        selected = _select_quality(qualities, quality)
        video_url = selected["url"]
    else:
        qualities, title, info = await extract_sextvx_qualities(url, progress_cb)
        if qualities:
            for q in qualities:
                if q.get("url") and video_url in q["url"]:
                    video_url = q["url"]
                    break
            else:
                video_url = qualities[0]["url"]

    return await download_sextvx_video(
        url, video_url, filepath, progress_cb, dl_id=dl_id,
    )


def _select_quality(qualities: List[dict], quality: str) -> dict:
    """انتخاب کیفیت بر اساس پارامتر quality."""
    if not qualities:
        raise ValueError("Empty qualities list")

    # اگه quality_key خاص خواسته شده
    for q in qualities:
        if q.get("quality_key") == quality:
            return q

    # اگه 'high' یا 'best' — بالاترین کیفیت
    if quality in ("high", "best"):
        return qualities[0]  # مرتب‌سازی نزولی هست

    # اگه 'low' یا 'worst' — پایین‌ترین کیفیت
    if quality in ("low", "worst"):
        return qualities[-1]

    # default — اولین آیتم
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
        print("Usage: python sextvx_handler.py <url> [output.mp4] [quality]")
        print("  Note: m.sextvx.com URLs are auto-normalized to www.sextvx.com")
        sys.exit(1)

    url = sys.argv[1]
    output = sys.argv[2] if len(sys.argv) > 2 else "/tmp/sextvx_test.mp4"
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

    success, error, size = await download_sextvx_direct(
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
