"""
drtuber_handler.py
──────────────────
هندلر اختصاصی برای DrTuber — کاملاً بدون وابستگی به yt-dlp.

روش کار:
  1. ساخت یه AsyncSession پایدار (برای نگه‌داری cookies)
  2. Visit homepage (برای دریافت session cookies)
  3. Visit صفحه‌ی ویدیو (برای ثبت session فعال)
  4. fetch از /player_config_json/?vid=ID&... با headers درست
     (با retry چون این endpoint گاهی 502 یا پاسخ خالی می‌ده)
  5. parse JSON که شامل files = {lq, hq, 4k} هست
  6. دانلود مستقیم mp4 با stream=True (URL‌ها توکن‌دار هستن، مدت کوتاه valid)

اگه بعد از چند retry هم API پاسخ نداد، fallback به:
  - استخراج از خود HTML صفحه (ویدیو داخل video tag)
  - یا yt-dlp (به‌عنوان آخرین راه)
"""

import asyncio
import html as html_lib
import json
import logging
import os
import re
import time
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from ._common import (
    ProgressCallback,
    check_impersonation_support,
    cleanup_file,
    default_user_agent,
    download_direct as _download_direct_impl,
    download_direct_multi as _download_direct_multi_impl,
    download_m3u8 as _download_m3u8_impl,
    extract_title_from_html,
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
    ".drtst.com",       # g2.drtst.com, g3.drtst.com, g5.drtst.com etc. (thumbnails/poster)
    "gcdn.drtuber.com",  # CDN for video files: gcdn.drtuber.com
)

# تعداد retry برای API call - چون گاهی 502 یا پاسخ خالی می‌ده
_API_MAX_RETRIES = 4
_API_RETRY_DELAY = 2.0  # ثانیه

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
    # URL: https://m.drtuber.com/video/7701687/some-slug
    # URL: https://www.drtuber.com/embed/9587518
    m = re.search(r"/(?:video|embed)/(\d+)", url)
    if m:
        return m.group(1)
    return None


def _normalize_url(url: str) -> str:
    """تبدیل m.drtuber.com به www.drtuber.com (API فقط روی www کار می‌کنه)."""
    # m.drtuber.com → www.drtuber.com
    return re.sub(r"^https?://m\.", "https://www.", url, count=1)


# ─── HTTP fetch helper (with retry) ─────────────────────────


async def _fetch_with_retry(
    session,
    url: str,
    headers: dict,
    max_retries: int = _API_MAX_RETRIES,
    retry_delay: float = _API_RETRY_DELAY,
) -> Tuple[Optional[object], int]:
    """
    fetch URL با retry.
    DrTuber API گاهی 502 یا پاسخ خالی می‌ده - retry لازمه.
    """
    last_status = 0
    for attempt in range(max_retries):
        try:
            r = await session.get(
                url, impersonate="chrome", headers=headers,
                timeout=30, verify=False,
            )
            last_status = r.status_code
            # اگه 200 بود و پاسخ خالی نبود، برگردون
            if r.status_code == 200 and r.text and len(r.text) > 10:
                logger.debug("[DrTuber] fetch success on attempt %d: %d bytes",
                             attempt + 1, len(r.text))
                return r, 200
            # اگه 502 یا 503 بود، retry
            if r.status_code in (502, 503, 504, 429):
                logger.warning(
                    "[DrTuber] API HTTP %d on attempt %d/%d - retrying in %ss",
                    r.status_code, attempt + 1, max_retries, retry_delay
                )
                await asyncio.sleep(retry_delay * (attempt + 1))  # exponential backoff
                continue
            # اگه 200 بود ولی پاسخ خالی ([]) یا خیلی کوتاه بود، retry
            if r.status_code == 200 and (not r.text or r.text.strip() in ("[]", "{}", "")):
                logger.warning(
                    "[DrTuber] API empty response on attempt %d/%d - retrying in %ss",
                    attempt + 1, max_retries, retry_delay
                )
                await asyncio.sleep(retry_delay * (attempt + 1))
                continue
            # اگه کد دیگه‌ای بود، خطا برگردون
            return r, r.status_code
        except Exception as e:
            logger.warning(
                "[DrTuber] fetch exception on attempt %d/%d: %s",
                attempt + 1, max_retries, e
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay * (attempt + 1))
            last_status = 0
    return None, last_status


# ─── Quality extraction (main function) ────────────────────


async def extract_drtuber_qualities(url: str) -> Tuple[List[dict], str]:
    """
    استخراج کیفیت‌های ویدیو از DrTuber — کاملاً بدون yt-dlp.

    استراتژی:
      1. یه AsyncSession پایدار بساز (برای cookies)
      2. Homepage رو visit کن (برای session cookies)
      3. صفحه‌ی ویدیو رو visit کن (برای ثبت session فعال)
      4. API /player_config_json/ رو با retry فراخوانی کن
      5. از JSON، files = {lq, hq, 4k} رو استخراج کن
      6. fallback به استخراج از HTML اگه API کار نکرد
    """
    if not is_drtuber_url(url):
        return [], "Invalid URL"

    if not check_impersonation_support():
        return [], "curl_cffi لازمه: pip install curl_cffi"

    video_id = _parse_video_id(url)
    if not video_id:
        return [], "Could not extract video ID from URL"

    # تبدیل m.drtuber.com به www.drtuber.com
    www_url = _normalize_url(url)

    logger.info("[DrTuber] Extracting for video_id=%s", video_id)

    if not check_impersonation_support():
        return [], "curl_cffi not available"

    from curl_cffi.requests import AsyncSession

    title = f"DrTuber video {video_id}"
    config_data = None

    async with AsyncSession() as session:
        # Step 1: Visit homepage (برای دریافت session cookies)
        try:
            await session.get(
                _SITE_URL + "/",
                impersonate="chrome",
                headers={"User-Agent": _USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
                timeout=20, verify=False,
            )
            logger.debug("[DrTuber] Step 1: Homepage visited")
        except Exception as e:
            logger.warning("[DrTuber] Step 1 (homepage) failed: %s", e)

        # Step 2: Visit video page (برای ثبت session فعال)
        try:
            r_video = await session.get(
                www_url,
                impersonate="chrome",
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
                timeout=20, verify=False,
            )
            if r_video.status_code == 200 and r_video.text:
                title = extract_title_from_html(r_video.text, "DrTuber")
                logger.debug("[DrTuber] Step 2: Video page visited, title=%s", title[:50])
            else:
                logger.warning("[DrTuber] Step 2 (video page) HTTP %d", r_video.status_code)
        except Exception as e:
            logger.warning("[DrTuber] Step 2 (video page) failed: %s", e)

        # Step 3: Call /player_config_json/ با retry
        config_url = (
            f"{_SITE_URL}/player_config_json/"
            f"?vid={video_id}&aid=0&domain_id=0&embed=0&ref=&check_speed=0"
        )
        config_headers = {
            "User-Agent": _USER_AGENT,
            "Referer": www_url,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en-US,en;q=0.9",
        }

        r_config, cstatus = await _fetch_with_retry(
            session, config_url, config_headers
        )

        if r_config is None or cstatus != 200 or not r_config.text:
            logger.error("[DrTuber] API failed after retries: HTTP %s", cstatus)
            # Fallback: استخراج از HTML صفحه‌ی ویدیو
            if r_video and r_video.text:
                logger.info("[DrTuber] Trying fallback: extract from HTML")
                qualities = _extract_from_html(r_video.text)
                if qualities:
                    return qualities, title
            return [], f"Could not fetch video config (HTTP {cstatus})"

        # Parse JSON
        try:
            config_data = r_config.json()
        except Exception as e:
            logger.error("[DrTuber] JSON parse failed: %s", e)
            return [], "Video config returned invalid JSON"

    # اگه config_data یه list خالی بود (گاهی پیش میاد)
    if isinstance(config_data, list):
        if not config_data:
            logger.error("[DrTuber] API returned empty list []")
            return [], "Video config is empty"
        config_data = config_data[0] if config_data else {}

    if not isinstance(config_data, dict):
        return [], "Video config returned unexpected format"

    # استخراج title از JSON اگه موجود بود (دقیق‌تر از HTML)
    json_title = config_data.get("title") or ""
    if json_title:
        title = json_title

    # استخراج کیفیت‌ها از files
    files = config_data.get("files") or {}
    if not isinstance(files, dict):
        logger.warning("[DrTuber] Unexpected files format: %s", type(files).__name__)
        return [], "Video config returned unexpected files format"

    # ترتیب نمایش: از بهترین به بدترین
    quality_map = {
        "4k": ("2160p", "📡 2160p (4K)"),
        "hq": ("720p",  "📡 720p HD"),
        "lq": ("360p",  "📡 360p"),
    }
    order = ["4k", "hq", "lq"]

    qualities: List[dict] = []
    for fmt in order:
        video_url = files.get(fmt)
        if not video_url:
            continue
        # host check
        if not _is_allowed_host(video_url):
            logger.debug("[DrTuber] Skipping non-allowed host: %s", video_url[:60])
            continue

        _, label_full = quality_map.get(fmt, (fmt.upper(), f"📡 {fmt}"))
        qualities.append({
            "label": label_full,
            "url": video_url,
            "method": "direct",
        })

    # fallback به download_url اگه هیچ file پیدا نشد
    if not qualities:
        download_url = config_data.get("download_url")
        if download_url and _is_allowed_host(download_url):
            qualities.append({
                "label": "📡 Auto",
                "url": download_url,
                "method": "direct",
            })

    if not qualities:
        logger.error("[DrTuber] No files found in config")
        return [], "No playable video sources found in config"

    logger.info("[DrTuber] Extracted %d qualities for: %s",
                len(qualities), title[:60])
    return qualities, title


# ─── HTML fallback extraction ─────────────────────────────


def _extract_from_html(html: str) -> List[dict]:
    """
    fallback: استخراج URL ویدیو از HTML صفحه.
    DrTuber گاهی URL ویدیو رو داخل <video> tag یا data-* attribute میذاره.
    """
    qualities: List[dict] = []
    seen_urls = set()

    # Pattern 1: <source src="...mp4">
    for m in re.finditer(
        r'<source[^>]+src=["\']([^"\']+\.mp4[^"\']*)["\']',
        html, re.IGNORECASE,
    ):
        src = m.group(1).replace("\\/", "/")
        if src in seen_urls:
            continue
        if "/tmb/" in src:  # Skip thumbnails
            continue
        if not _is_allowed_host(src):
            continue
        seen_urls.add(src)
        # تشخیص label از URL
        m_q = re.search(r"_(\d{3,4})p?\.", src)
        label = f"📡 {m_q.group(1)}p" if m_q else "📡 Auto"
        qualities.append({
            "label": label,
            "url": src,
            "method": "direct",
        })

    # Pattern 2: flashvars file: "...mp4"
    for m in re.finditer(
        r'(?:file|video_url|source)\s*:\s*["\']([^"\']+\.mp4[^"\']*)["\']',
        html,
    ):
        src = m.group(1).replace("\\/", "/")
        if src in seen_urls:
            continue
        if "/tmb/" in src:
            continue
        if not _is_allowed_host(src):
            continue
        seen_urls.add(src)
        m_q = re.search(r"_(\d{3,4})p?\.", src)
        label = f"📡 {m_q.group(1)}p" if m_q else "📡 Auto"
        qualities.append({
            "label": label,
            "url": src,
            "method": "direct",
        })

    return qualities


# ─── Download ─────────────────────────────────────────────


async def download_drtuber_direct(
    url: str,
    filepath: str,
    progress_cb: ProgressCallback,
) -> Tuple[bool, str, int]:
    """دانلود مستقیم mp4 از DrTuber CDN با multi-segment (16x سریع‌تر)."""
    if not _is_allowed_host(url):
        return False, "URL host not allowed", 0

    # اول سعی کن با multi-segment (16 worker موازی - سریع‌تر)
    success, error, size = await _download_direct_multi_impl(
        url, filepath, progress_cb,
        referer=_SITE_REFERER,
        user_agent=_USER_AGENT,
    )
    if success:
        return True, "", size
    # fallback به direct ساده اگه multi-segment شکست خورد
    cleanup_file(filepath)
    success, error, size = await _download_direct_impl(
        url, filepath, progress_cb,
        referer=_SITE_REFERER,
        user_agent=_USER_AGENT,
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
    """DrTuber معمولاً m3u8 نمی‌ده، ولی برای سازگاری با API."""
    success, error, size = await _download_m3u8_impl(
        url, filepath, progress_cb,
        referer=_SITE_REFERER,
        user_agent=_USER_AGENT,
    )
    if success:
        return True, "", size
    cleanup_file(filepath)
    return False, error, 0
