"""
txxx_network_handler.py
───────────────────────
هندلر برای سایت‌های شبکه Txxx.

این سایت‌ها همگی از یه پلتفرم مشابه (JWPlayer + ad system) استفاده می‌کنن:
  - txxx.com
  - hclips.com
  - upornia.com
  - vjav.com
  - hdzog.com

روش کار:
  سایت‌های این شبکه‌ی ویدیو رو به‌صورت dynamic با JS load می‌کنن،
  پس از yt-dlp استفاده می‌کنیم که پشتیبانی کامل داره (extractor: Txxx).
  این extractor خودش با تمام سایت‌های شبکه کار می‌کنه.

اگه yt-dlp خراب بود، fallback به fetching مستقیم صفحه.
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
    download_with_ytdlp,
    extract_qualities_with_ytdlp,
    extract_title_from_html,
    fetch_html,
    is_url_in_domains,
    quality_sort_key,
)

logger = logging.getLogger("TxxxHandler")


# ─── Site configurations ──────────────────────────────────


SITES = {
    "txxx": {
        "display_name": "Txxx",
        "domain": "txxx.com",
        "allowed_hosts": frozenset({"txxx.com", "www.txxx.com", "m.txxx.com"}),
        "allowed_suffixes": (".txxx.com",),
        "homepage": "https://txxx.com/",
    },
    "hclips": {
        "display_name": "HClips",
        "domain": "hclips.com",
        "allowed_hosts": frozenset({"hclips.com", "www.hclips.com", "m.hclips.com"}),
        "allowed_suffixes": (".hclips.com",),
        "homepage": "https://www.hclips.com/",
    },
    "upornia": {
        "display_name": "Upornia",
        "domain": "upornia.com",
        "allowed_hosts": frozenset({"upornia.com", "www.upornia.com", "m.upornia.com"}),
        "allowed_suffixes": (".upornia.com",),
        "homepage": "https://upornia.com/",
    },
    "vjav": {
        "display_name": "VJAV",
        "domain": "vjav.com",
        "allowed_hosts": frozenset({"vjav.com", "www.vjav.com", "m.vjav.com"}),
        "allowed_suffixes": (".vjav.com",),
        "homepage": "https://vjav.com/",
    },
    "hdzog": {
        "display_name": "HDzog",
        "domain": "hdzog.com",
        "allowed_hosts": frozenset({"hdzog.com", "www.hdzog.com", "m.hdzog.com"}),
        "allowed_suffixes": (".hdzog.com",),
        "homepage": "https://www.hdzog.com/",
    },
}


# ─── URL detection ─────────────────────────────────────────


def _make_is_url_fn(site_key: str):
    cfg = SITES[site_key]

    def is_url(url: str) -> bool:
        if not url:
            return False
        try:
            host = (urlparse(url).hostname or "").lower()
            if host in cfg["allowed_hosts"]:
                return True
            for suffix in cfg["allowed_suffixes"]:
                if host.endswith(suffix):
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


async def extract_txxx_network_qualities(
    url: str, site_key: Optional[str] = None
) -> Tuple[List[dict], str]:
    """
    استخراج کیفیت‌های ویدیو از سایت‌های شبکه Txxx.

    از yt-dlp (که extractor Txxx داره) استفاده می‌کنیم.
    """
    if not site_key:
        site_key = _get_site_key_from_url(url)
    if not site_key:
        return [], "Unknown site"

    cfg = SITES[site_key]

    if not shutil.which("yt-dlp"):
        return [], "yt-dlp not installed"

    # yt-dlp با extractor Txxx کار می‌کنه
    logger.info("[%s] Extracting via yt-dlp: %s", cfg["display_name"], url[:80])
    qualities, title = await extract_qualities_with_ytdlp(url, cfg["display_name"])
    return qualities, title


# ─── Generic factory for site-specific entry points ────────


def _make_site_entrypoints(site_key: str):
    cfg = SITES[site_key]
    sessions_dict: dict = {}

    is_url_fn = globals().get(f"is_{site_key}_url")
    if not is_url_fn:
        is_url_fn = _make_is_url_fn(site_key)
        globals()[f"is_{site_key}_url"] = is_url_fn

    async def extract_qualities(url: str) -> Tuple[List[dict], str]:
        return await extract_txxx_network_qualities(url, site_key)

    async def download_direct(url: str, filepath: str, progress_cb: ProgressCallback) -> Tuple[bool, str, int]:
        """دانلود با multi-segment (16x سریع‌تر)."""
        # اول سعی کن با multi-segment (16 worker موازی)
        success, error, size = await _download_direct_multi_impl(
            url, filepath, progress_cb,
            referer=cfg["homepage"],
        )
        if success:
            return True, "", size
        # fallback به direct ساده
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
        # برای سایت‌های Txxx معمولاً m3u8 هست
        success, error, size = await _download_m3u8_impl(
            url, filepath, progress_cb, referer=cfg["homepage"]
        )
        if success:
            return True, "", size
        cleanup_file(filepath)
        return False, error, 0

    extract_qualities.__name__ = f"extract_{site_key}_qualities"
    download_direct.__name__ = f"download_{site_key}_direct"
    download_m3u8_fn.__name__ = f"download_{site_key}_m3u8"

    return is_url_fn, extract_qualities, download_direct, download_m3u8_fn, sessions_dict


# ─── Expose entry points for each site ─────────────────────

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
