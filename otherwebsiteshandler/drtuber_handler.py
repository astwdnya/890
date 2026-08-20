"""
drtuber_handler.py
──────────────────
هندلر اختصاصی برای DrTuber.

روش کار:
  1. fetch صفحه‌ی ویدیو با curl_cffi (با cookies از homepage)
  2. استخراج video_id از صفحه
  3. fetch از /player_config_json/?vid=ID&... (با session cookies)
  4. دریافت JSON با URLs کیفیت‌های مختلف (lq, hq, 4k)
  5. دانلود مستقیم mp4 (URL‌های توکن‌دار هستن - مدت کوتاه valid هستن)

نکته: yt-dlp extractor خراب شده (داده‌ها به‌جای dict لیست برمی‌گرده)،
      پس این هندلر اختصاصی نوشته شده.
"""

import asyncio
import html as html_lib
import logging
import re
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from ._common import (
    ProgressCallback,
    check_impersonation_support,
    cleanup_file,
    default_user_agent,
    download_direct as _download_direct_impl,
    download_m3u8 as _download_m3u8_impl,
    extract_title_from_html,
    fetch_html,
    fetch_json,
    is_url_in_domains,
    quality_sort_key,
)

logger = logging.getLogger("DrTuberHandler")

_USER_AGENT = default_user_agent()

_SITE_DOMAIN = "drtuber.com"
_SITE_URL = "https://www.drtuber.com"
_SITE_REFERER = f"{_SITE_URL}/"

_ALLOWED_HOSTS = frozenset({
    "drtuber.com",
    "www.drtuber.com",
    "m.drtuber.com",
})

_ALLOWED_HOST_SUFFIXES = (
    ".drtuber.com",
    ".drtst.com",       # g3.drtst.com, g5.drtst.com etc.
    ".drtuber.com",
    "gcdn.drtuber.com",  # CDN for video files
)

drtuber_sessions: dict = {}


# ─── URL detection ─────────────────────────────────────────


def is_drtuber_url(url: str) -> bool:
    """تشخیص آیا URL متعلق به drtuber.com هست."""
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


def _parse_video_id(url: str) -> Optional[str]:
    """استخراج video_id از URL DrTuber."""
    # URL: https://www.drtuber.com/video/9587518/some-slug
    # URL: https://www.drtuber.com/embed/9587518
    m = re.search(r"/(?:video|embed)/(\d+)", url)
    if m:
        return m.group(1)
    return None


# ─── Quality extraction ────────────────────────────────────


async def extract_drtuber_qualities(url: str) -> Tuple[List[dict], str]:
    """
    استخراج کیفیت‌های ویدیو از DrTuber.

    استراتژی:
      1. fetch صفحه‌ی ویدیو برای cookies و video_id
      2. fetch از /player_config_json/ با session cookies
      3. parse JSON که شامل files = {lq: url, hq: url, 4k: url} هست
    """
    if not is_drtuber_url(url):
        return [], "Invalid URL"

    if not check_impersonation_support():
        return [], "curl_cffi لازمه: pip install curl_cffi"

    video_id = _parse_video_id(url)
    if not video_id:
        return [], "Could not extract video ID from URL"

    logger.info("[DrTuber] Extracting for video_id=%s", video_id)

    # fetch صفحه‌ی ویدیو (برای cookies)
    html, status = await fetch_html(
        url=url,
        referer=_SITE_REFERER,
        visit_homepage_first=_SITE_URL,
    )
    if not html:
        return [], f"Could not fetch page (HTTP {status})"

    title = extract_title_from_html(html, "DrTuber")

    # fetch از /player_config_json/ با session cookies
    # query params: vid, aid, domain_id, embed, ref, check_speed
    config_url = (
        f"{_SITE_URL}/player_config_json/"
        f"?vid={video_id}&aid=0&domain_id=0&embed=0&ref=&check_speed=0"
    )
    config_data, cstatus = await fetch_json(
        url=config_url,
        referer=url,
        visit_homepage_first=_SITE_URL,
        visit_video_page=url,
    )

    if not config_data or cstatus != 200:
        logger.warning("[DrTuber] player_config_json failed: HTTP %s", cstatus)
        return [], f"Could not fetch video config (HTTP {cstatus})"

    # Parse JSON
    files = config_data.get("files") or {}
    if not isinstance(files, dict):
        # yt-dlp bug: files به‌جای dict یه list برمی‌گرده
        logger.warning("[DrTuber] Unexpected files format: %s", type(files).__name__)
        return [], "Video config returned unexpected format"

    # هر کیفیت رو تبدیل به یه dict استاندارد می‌کنیم
    qualities: List[dict] = []
    quality_map = {
        "4k": ("2160p", "📡 2160p (4K)"),
        "hq": ("720p",  "📡 720p HD"),
        "lq": ("360p",  "📡 360p"),
    }

    # ترتیب نمایش: از بهترین به بدترین
    order = ["4k", "hq", "lq"]
    for fmt in order:
        video_url = files.get(fmt)
        if not video_url:
            continue
        # host check
        if not _is_allowed_host(video_url):
            logger.debug("[DrTuber] Skipping non-allowed host: %s", video_url[:60])
            continue

        label_simple, label_full = quality_map.get(fmt, (fmt.upper(), f"📡 {fmt}"))
        qualities.append({
            "label": label_full,
            "url": video_url,
            "method": "direct",  # mp4 مستقیم
        })

    # اگه هیچ کدوم از files پیدا نشد، fallback به download_url (اگه هست)
    if not qualities:
        download_url = config_data.get("download_url")
        if download_url and _is_allowed_host(download_url):
            qualities.append({
                "label": "📡 Auto",
                "url": download_url,
                "method": "direct",
            })

    if not qualities:
        # اگه هردو files خالی بودن، fallback به yt-dlp
        logger.warning("[DrTuber] No files found in config, falling back to yt-dlp")
        try:
            from ._common import extract_qualities_with_ytdlp
            return await extract_qualities_with_ytdlp(url, "DrTuber")
        except Exception as e:
            return [], f"yt-dlp fallback failed: {e}"

    logger.info("[DrTuber] Extracted %d qualities for: %s",
                len(qualities), title[:60])
    return qualities, title


# ─── Download ─────────────────────────────────────────────


async def download_drtuber_direct(
    url: str,
    filepath: str,
    progress_cb: ProgressCallback,
) -> Tuple[bool, str, int]:
    """دانلود مستقیم mp4 از DrTuber CDN."""
    if not _is_allowed_host(url):
        return False, "URL host not allowed", 0
    success, error, size = await _download_direct_impl(
        url, filepath, progress_cb,
        referer=_SITE_REFERER,
    )
    if success:
        return True, "", size
    cleanup_file(filepath)
    return False, error, 0


async def download_drtuber_m3u8(
    url: str,
    filepath: str,
    progress_cb: ProgressCallback,
) -> Tuple[bool, str, int]:
    """DrTuber م3u8 نمی‌ده، ولی برای سازگاری با API."""
    success, error, size = await _download_m3u8_impl(
        url, filepath, progress_cb, referer=_SITE_REFERER
    )
    if success:
        return True, "", size
    cleanup_file(filepath)
    return False, error, 0
