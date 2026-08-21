"""
kvs_handler.py
──────────────
هندلر عمومی برای سایت‌های مبتنی بر KVS (Kernel Video Sharing).

این پلتفرم توسط سایت‌های زیادی استفاده می‌شه و الگوی ثابتی داره:
  - صفحه ویدیو شامل <video id="bravoplayer"> یا <video class="video-js">
  - داخلش <source src="https://site.com/get_file/..." title="360p"> هست
  - کیفیت‌ها: 360p, 720p, 1080p, 4k
  - URL‌های ویدیو شامل hash توکن‌دار هستن (مثلاً /get_file/1/HASH/...)

سایت‌های پشتیبانی‌شده:
  - hellporno.com
  - alphaporno.com
  - bravoteens.com
  - bravotube.com
  - crocotube.com
  - porngo.com

روش کار:
  1. fetch صفحه ویدیو با curl_cffi
  2. پیدا کردن <video> tag اصلی و <source> tag‌های داخلش
  3. استخراج URL و کیفیت از هر source
  4. دانلود مستقیم mp4 (نه m3u8 - چون URL مستقیم داره)
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
    download_direct_multi as _download_direct_multi_impl,
    download_m3u8 as _download_m3u8_impl,
    extract_title_from_html,
    fetch_html,
    is_url_in_domains,
    quality_sort_key,
)

logger = logging.getLogger("KvsHandler")


# ─── Site configurations ──────────────────────────────────

# هر سایت یه config جداگانه داره
SITES = {
    "hellporno": {
        "display_name": "HellPorno",
        "domain": "hellporno.com",
        "url_patterns": (r"^https?://(?:www\.|m\.)?hellporno\.com/videos/[\w\-]+/?$",),
        "allowed_hosts": frozenset({"hellporno.com", "www.hellporno.com", "m.hellporno.com"}),
        "allowed_suffixes": (".hellporno.com", ".hellcdn.net"),
        "homepage": "https://hellporno.com/",
    },
    "alphaporno": {
        "display_name": "AlphaPorno",
        "domain": "alphaporno.com",
        "url_patterns": (r"^https?://(?:www\.|m\.)?alphaporno\.com/videos/[\w\-]+/?$",),
        "allowed_hosts": frozenset({"alphaporno.com", "www.alphaporno.com", "m.alphaporno.com"}),
        "allowed_suffixes": (".alphaporno.com",),
        "homepage": "https://www.alphaporno.com/",
    },
    "bravoteens": {
        "display_name": "BravoTeens",
        "domain": "bravoteens.com",
        "url_patterns": (r"^https?://(?:www\.|m\.)?bravoteens\.com/videos/[\w\-]+/?$",),
        "allowed_hosts": frozenset({"bravoteens.com", "www.bravoteens.com", "m.bravoteens.com"}),
        "allowed_suffixes": (".bravoteens.com",),
        "homepage": "https://www.bravoteens.com/",
    },
    "bravotube": {
        "display_name": "BravoTube",
        "domain": "bravotube.com",
        "url_patterns": (r"^https?://(?:www\.|m\.)?bravotube\.com/videos/[\w\-]+/?$",),
        "allowed_hosts": frozenset({"bravotube.com", "www.bravotube.com", "m.bravotube.com"}),
        "allowed_suffixes": (".bravotube.com",),
        "homepage": "https://www.bravotube.com/",
    },
    "crocotube": {
        "display_name": "CrocoTube",
        "domain": "crocotube.com",
        "url_patterns": (r"^https?://(?:www\.|m\.)?crocotube\.com/videos/[\w\-]+/?$",),
        "allowed_hosts": frozenset({"crocotube.com", "www.crocotube.com", "m.crocotube.com"}),
        "allowed_suffixes": (".crocotube.com",),
        "homepage": "https://crocotube.com/",
    },
    "porngo": {
        "display_name": "PornGo",
        "domain": "porngo.com",
        "url_patterns": (
            r"^https?://(?:www\.|m\.)?porngo\.com/videos/\d+/[\w\-]+/?$",
            r"^https?://(?:www\.|m\.)?porngo\.com/embed/\d+/?$",
        ),
        "allowed_hosts": frozenset({"porngo.com", "www.porngo.com", "m.porngo.com"}),
        "allowed_suffixes": (".porngo.com", ".porngo.xxx"),
        "homepage": "https://www.porngo.com/",
    },
}


# ─── URL detection ─────────────────────────────────────────


def _make_is_url_fn(site_key: str):
    """ساخت تابع is_<site>_url برای یه سایت خاص."""
    cfg = SITES[site_key]
    patterns = tuple(re.compile(p, re.I) for p in cfg["url_patterns"])

    def is_url(url: str) -> bool:
        if not url:
            return False
        # الگو اول
        for p in patterns:
            if p.match(url):
                return True
        # fallback: hostname check
        try:
            host = (urlparse(url).hostname or "").lower()
            if host in cfg["allowed_hosts"]:
                return True
        except Exception:
            pass
        return False

    is_url.__name__ = f"is_{site_key}_url"
    return is_url


# تعریف توابع is_<site>_url برای هر سایت
for _site_key in SITES:
    globals()[f"is_{_site_key}_url"] = _make_is_url_fn(_site_key)


def _get_site_key_from_url(url: str) -> Optional[str]:
    """پیدا کردن کلید سایت از روی URL."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return None
    for key, cfg in SITES.items():
        if host in cfg["allowed_hosts"] or any(
            host.endswith(s) for s in cfg["allowed_suffixes"]
        ):
            return key
    return None


# ─── Quality extraction ────────────────────────────────────


async def extract_kvs_qualities(url: str, site_key: Optional[str] = None) -> Tuple[List[dict], str]:
    """
    استخراج کیفیت‌های ویدیو از یه سایت KVS-based.

    Args:
        url: URL صفحه ویدیو
        site_key: کلید سایت (اختیاری - اگه None باشه، خودکار تشخیص داده می‌شه)

    Returns:
        Tuple (qualities_list, title). اگه خطا باشه ([], error_msg).
    """
    if not site_key:
        site_key = _get_site_key_from_url(url)
    if not site_key:
        return [], "Unknown site"

    cfg = SITES[site_key]
    if not check_impersonation_support():
        return [], "curl_cffi لازمه: pip install curl_cffi"

    logger.info("[%s] Fetching: %s", cfg["display_name"], url[:80])

    # fetch صفحه ویدیو با referer به homepage
    html, status = await fetch_html(
        url=url,
        referer=cfg["homepage"],
        visit_homepage_first=cfg["homepage"],
    )
    if not html:
        return [], f"Could not fetch page (HTTP {status})"

    title = extract_title_from_html(html, cfg["display_name"])

    # استخراج source tag‌ها از داخل video tag اصلی
    # الگو: <video id="bravoplayer" ...>  <source src="..." title="360p" />  </video>
    # یا    <video class="video-js" ...>  <source src="..." /> </video>
    qualities: List[dict] = []
    seen_urls = set()

    # Pattern 1: bravoplayer با source داخلش
    main_video_match = re.search(
        r'<video[^>]*\bid=["\']?bravoplayer[^>]*>(.*?)</video>',
        html, re.DOTALL | re.IGNORECASE,
    )
    video_block = None
    if main_video_match:
        video_block = main_video_match.group(1)
    else:
        # Pattern 2: video با controls (main player)
        main_video_match = re.search(
            r'<video[^>]*\bcontrols\b[^>]*>(.*?)</video>',
            html, re.DOTALL | re.IGNORECASE,
        )
        if main_video_match:
            video_block = main_video_match.group(1)

    if not video_block:
        logger.warning("[%s] No main <video> block found", cfg["display_name"])
        return [], "Video player not found on page"

    # Pattern 3: تمام source tag‌ها داخل video block
    # این pattern هر source tag رو پیدا می‌کنه، سپس src و title رو جداگانه استخراج می‌کنه
    # (چون ترتیب attribute‌ها ممکنه متفاوت باشه)
    for m in re.finditer(r'<source\b([^>]*)/?>', video_block, re.IGNORECASE):
        attrs = m.group(1)
        # پیدا کردن src
        m_src = re.search(r'\bsrc=["\']([^"\']+)["\']', attrs)
        if not m_src:
            continue
        src = m_src.group(1)
        # پیدا کردن title (اگه هست)
        m_title = re.search(r'\btitle=["\']([^"\']*)["\']', attrs)
        title_attr = m_title.group(1) if m_title else ""
        # پیدا کردن data-fluid-hd (KVS HD marker)
        has_hd_marker = "data-fluid-hd" in attrs

        if not src or src in seen_urls:
            continue
        # Skip poster/preview (که معمولاً trailer_360p یا preview_ هست)
        if "trailer_" in src.lower() or "_preview" in src.lower():
            continue
        # host check
        try:
            host = urlparse(src).hostname or ""
            if host and host not in cfg["allowed_hosts"] and not any(
                host.endswith(s) for s in cfg["allowed_suffixes"]
            ):
                logger.debug("[%s] Skipping non-allowed host: %s", cfg["display_name"], src[:60])
                continue
        except Exception:
            pass

        seen_urls.add(src)

        # تشخیص label کیفیت از روی title attribute یا URL
        quality_label = title_attr or ""
        # اگه title خالی بود، از URL حدس بزن
        if not quality_label:
            m_q = re.search(r"_(\d{3,4})p?\.", src)
            if m_q:
                quality_label = f"{m_q.group(1)}p"
            elif "_480m" in src or "480m" in src:
                quality_label = "480p"
            elif "_hq" in src:
                quality_label = "720p"
            elif "_lq" in src:
                quality_label = "360p"
            elif "_4k" in src:
                quality_label = "2160p"
            elif has_hd_marker:
                # منبع data-fluid-hd بدون title (معمولاً بالاترین کیفیت)
                quality_label = "HD"
            else:
                # main MP4 بدون کیفیت مشخص
                quality_label = "Auto"

        qualities.append({
            "label": f"📡 {quality_label}",
            "url": src,
            "method": "direct",  # همه KVS sites mp4 مستقیم می‌دن
        })

    # اگه qualities پیدا نشد، fallback به همه‌ی source‌های صفحه (شاید site variations داره)
    if not qualities:
        # Pattern 4: فقط source‌های داخل video tag با get_file pattern
        for m in re.finditer(
            r'<source[^>]+src=["\']([^"\']*get_file/[^"\']+)["\'][^>]*>',
            html,
        ):
            src = m.group(1)
            if src in seen_urls:
                continue
            if "trailer_" in src.lower():
                continue
            seen_urls.add(src)
            m_q = re.search(r"_(\d{3,4})p?\.", src)
            quality_label = f"{m_q.group(1)}p" if m_q else "Auto"
            qualities.append({
                "label": f"📡 {quality_label}",
                "url": src,
                "method": "direct",
            })

    if not qualities:
        logger.warning("[%s] No video sources found", cfg["display_name"])
        return [], "No playable video sources found"

    qualities.sort(key=quality_sort_key, reverse=True)
    logger.info("[%s] Extracted %d qualities for: %s",
                cfg["display_name"], len(qualities), title[:60])
    return qualities, title


# ─── Generic factory for site-specific entry points ────────


def _make_site_entrypoints(site_key: str):
    """
    ساخت توابع is_<site>_url, extract_<site>_qualities, download_<site>_direct,
    download_<site>_m3u8, و <site>_sessions برای یه سایت خاص.
    """
    cfg = SITES[site_key]
    sessions_dict: dict = {}

    is_url_fn = globals().get(f"is_{site_key}_url")
    if not is_url_fn:
        # اگه هنوز ساخته نشده، بساز
        is_url_fn = _make_is_url_fn(site_key)
        globals()[f"is_{site_key}_url"] = is_url_fn

    async def extract_qualities(url: str) -> Tuple[List[dict], str]:
        return await extract_kvs_qualities(url, site_key)

    async def download_direct(url: str, filepath: str, progress_cb: ProgressCallback) -> Tuple[bool, str, int]:
        if not is_url_in_domains(url, cfg["allowed_hosts"], cfg["allowed_suffixes"]):
            return False, "URL host not allowed", 0
        # اول سعی کن با multi-segment (16 worker موازی - سریع‌تر)
        success, error, size = await _download_direct_multi_impl(
            url, filepath, progress_cb,
            referer=cfg["homepage"],
        )
        if success:
            return True, "", size
        # fallback به direct ساده اگه multi-segment شکست خورد
        cleanup_file(filepath)
        success, error, size = await _download_direct_impl(
            url, filepath, progress_cb,
            referer=cfg["homepage"],
        )
        if success:
            return True, "", size
        cleanup_file(filepath)
        return False, error, 0

    async def download_m3u8_fn(url: str, filepath: str, progress_cb: ProgressCallback) -> Tuple[bool, str, int]:
        # KVS sites معمولاً mp4 مستقیم دارن، ولی اگه m3u8 پیدا شد از yt-dlp استفاده می‌کنیم
        success, error, size = await _download_m3u8_impl(
            url, filepath, progress_cb, referer=cfg["homepage"]
        )
        if success:
            return True, "", size
        cleanup_file(filepath)
        return False, error, 0

    # Set proper names
    extract_qualities.__name__ = f"extract_{site_key}_qualities"
    download_direct.__name__ = f"download_{site_key}_direct"
    download_m3u8_fn.__name__ = f"download_{site_key}_m3u8"

    return is_url_fn, extract_qualities, download_direct, download_m3u8_fn, sessions_dict


# ─── Expose entry points for each site ─────────────────────

# این متغیرها برای import در bot.py هستن
for _site_key in SITES:
    (
        _is_fn,
        _extract_fn,
        _dl_direct_fn,
        _dl_m3u8_fn,
        _sessions,
    ) = _make_site_entrypoints(_site_key)
    globals()[f"is_{_site_key}_url"] = _is_fn
    globals()[f"extract_{_site_key}_qualities"] = _extract_fn
    globals()[f"download_{_site_key}_direct"] = _dl_direct_fn
    globals()[f"download_{_site_key}_m3u8"] = _dl_m3u8_fn
    globals()[f"{_site_key}_sessions"] = _sessions
