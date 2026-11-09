"""
porntop_handler.py
──────────────────
هندلر برای PornTop (porntop.com).

روش کار:
  سایت از پلتفرم KVS استفاده می‌کنه ولی با تغییرات اختصاصی.
  yt-dlp extractor داره ولی فقط کیفیت پایین (lq) رو پیدا می‌کنه.
  این هندلر سعی می‌کنه با fetch مستقیم صفحه و پیدا کردن <source> tag‌ها
  کیفیت‌های بیشتری پیدا کنه.

اگه استخراج اختصاصی ناموفق بود، به yt-dlp fallback می‌کنه.
"""

import asyncio
import html as html_lib
import logging
import re
import shutil
from typing import List, Optional, Tuple
from urllib.parse import urlparse

from ._common import (
    ProgressCallback,
    check_impersonation_support,
    cleanup_file,
    default_user_agent,
    download_direct as _download_direct_impl,
    download_direct_multi as _download_direct_multi_impl,
    download_m3u8 as _download_m3u8_impl,
    extract_qualities_with_ytdlp,
    extract_title_from_html,
    fetch_html,
    is_url_in_domains,
    quality_sort_key,
)

logger = logging.getLogger("PornTopHandler")

_USER_AGENT = default_user_agent()

_SITE_URL = "https://www.porntop.com"
_SITE_REFERER = f"{_SITE_URL}/"

_ALLOWED_HOSTS = frozenset({
    "porntop.com",
    "www.porntop.com",
    "m.porntop.com",
})

_ALLOWED_HOST_SUFFIXES = (
    ".porntop.com",
    "porntop.com",
)

porntop_sessions: dict = {}


# ─── URL detection ─────────────────────────────────────────


def is_porntop_url(url: str) -> bool:
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
        return host in _ALLOWED_HOSTS or any(
            host.endswith(s) for s in _ALLOWED_HOST_SUFFIXES
        )
    except Exception:
        return False


def _is_allowed_host(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
        return host in _ALLOWED_HOSTS or any(
            host.endswith(s) for s in _ALLOWED_HOST_SUFFIXES
        )
    except Exception:
        return False


# ─── Quality extraction ────────────────────────────────────


async def extract_porntop_qualities(url: str) -> Tuple[List[dict], str]:
    """
    استخراج کیفیت‌های ویدیو از PornTop.

    روش‌های مختلف:
      1. پیدا کردن source tag‌ها داخل video tag اصلی
      2. fallback به yt-dlp
    """
    if not is_porntop_url(url):
        return [], "Invalid URL"

    if not check_impersonation_support():
        # fallback به yt-dlp
        return await extract_qualities_with_ytdlp(url, "PornTop")

    logger.info("[PornTop] Fetching: %s", url[:80])

    html, status = await fetch_html(
        url=url,
        referer=_SITE_REFERER,
        visit_homepage_first=_SITE_URL,
    )
    if not html:
        # fallback به yt-dlp
        return await extract_qualities_with_ytdlp(url, "PornTop")

    title = extract_title_from_html(html, "PornTop")

    # استخراج source tag‌ها از video tag
    qualities: List[dict] = []
    seen_urls = set()

    # Pattern: <video ...> <source src="..." title="..." /> </video>
    # PornTop ممکنه از KVS-style <video id="bravoplayer"> یا video tag ساده استفاده کنه
    main_video_match = re.search(
        r'<video[^>]*\bid=["\']?(?:bravoplayer|video-js|main-video)["\']?[^>]*>(.*?)</video>',
        html, re.DOTALL | re.IGNORECASE,
    )
    video_block = None
    if main_video_match:
        video_block = main_video_match.group(1)
    else:
        # fallback به اولین video tag با controls
        m = re.search(
            r'<video[^>]*\bcontrols\b[^>]*>(.*?)</video>',
            html, re.DOTALL | re.IGNORECASE,
        )
        if m:
            video_block = m.group(1)

    if video_block:
        for m in re.finditer(
            r'<source[^>]+src=["\']([^"\']+)["\'][^>]*(?:title=["\']([^"\']*)["\'])?[^>]*>',
            video_block,
        ):
            src = m.group(1)
            title_attr = m.group(2) or ""
            if not src or src in seen_urls:
                continue
            if "trailer_" in src.lower() or "_preview" in src.lower():
                continue
            # host check
            if not _is_allowed_host(src):
                continue
            seen_urls.add(src)

            # تشخیص label کیفیت
            if title_attr:
                quality_label = title_attr
            else:
                m_q = re.search(r"_(\d{3,4})p?\.", src)
                if m_q:
                    quality_label = f"{m_q.group(1)}p"
                elif "_hq" in src:
                    quality_label = "720p"
                elif "_lq" in src:
                    quality_label = "360p"
                elif "_4k" in src:
                    quality_label = "2160p"
                else:
                    quality_label = "Auto"

            qualities.append({
                "label": f"📡 {quality_label}",
                "url": src,
                "method": "direct",
            })

    # اگه چیزی پیدا نشد، fallback به yt-dlp
    if not qualities:
        logger.info("[PornTop] No sources in HTML, falling back to yt-dlp")
        return await extract_qualities_with_ytdlp(url, "PornTop")

    qualities.sort(key=quality_sort_key, reverse=True)
    logger.info("[PornTop] Extracted %d qualities for: %s",
                len(qualities), title[:60])
    return qualities, title


# ─── Download ─────────────────────────────────────────────


async def download_porntop_direct(
    url: str,
    filepath: str,
    progress_cb: ProgressCallback,
) -> Tuple[bool, str, int]:
    """دانلود مستقیم mp4 از PornTop با multi-segment (16x سریع‌تر)."""
    if not _is_allowed_host(url):
        return False, "URL host not allowed", 0
    # اول سعی کن با multi-segment (16 worker موازی - سریع‌تر)
    success, error, size = await _download_direct_multi_impl(
        url, filepath, progress_cb,
        referer=_SITE_REFERER,
    )
    if success:
        return True, "", size
    # fallback به direct ساده اگه multi-segment شکست خورد
    cleanup_file(filepath)
    success, error, size = await _download_direct_impl(
        url, filepath, progress_cb,
        referer=_SITE_REFERER,
    )
    if success:
        return True, "", size
    cleanup_file(filepath)
    return False, error, 0


async def download_porntop_m3u8(
    url: str,
    filepath: str,
    progress_cb: ProgressCallback,
) -> Tuple[bool, str, int]:
    success, error, size = await _download_m3u8_impl(
        url, filepath, progress_cb, referer=_SITE_REFERER
    )
    if success:
        return True, "", size
    cleanup_file(filepath)
    return False, error, 0
