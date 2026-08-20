"""
generic_handler.py
───────────────────
هندلر عمومی yt-dlp برای سایت‌هایی که yt-dlp از اون‌ها پشتیبانی می‌کنه
ولی هندلر اختصاصی براشون ننوشتیم.

برای سایت‌های زیر استفاده می‌شه:
  - pornone.com       (custom SPA - yt-dlp fallback)
  - pornhd.com        (custom)
  - xtube.com         (MindGeek-like)
  - mofosex.net       (Cloudflare protected)
  - fapvid.com        (Cloudflare protected)
  - monsterporn.com   (WordPress + custom CDN)
  - fetishkitsch.com  (membership required - fallback)
  - javhihi.com       (custom)
  - tokyoporn.com     (custom)
  - javwhores.com     (custom)
  - goodporn.to       (custom)
  - porn365.to        (custom)
  - fapcake.com       (custom)
  - fux.com           (custom - yt-dlp broken)

روش کار:
  1. yt-dlp به‌عنوان extractor استفاده می‌شه
  2. yt-dlp خودش URL ویدیو و quality‌ها رو پیدا می‌کنه
  3. download هم با yt-dlp انجام می‌شه
"""

import asyncio
import logging
import shutil
from typing import List, Optional, Tuple
from urllib.parse import urlparse

from ._common import (
    ProgressCallback,
    check_impersonation_support,
    cleanup_file,
    default_user_agent,
    download_direct as _download_direct_impl,
    download_m3u8 as _download_m3u8_impl,
    extract_qualities_with_ytdlp,
    fetch_html,
)

logger = logging.getLogger("GenericHandler")

_USER_AGENT = default_user_agent()


# ─── Site configurations ──────────────────────────────────
# هر سایت: domain, allowed_hosts, allowed_suffixes, homepage, display_name


SITES = {
    "pornone": {
        "display_name": "PornOne",
        "allowed_hosts": frozenset({"pornone.com", "www.pornone.com", "m.pornone.com"}),
        "allowed_suffixes": (".pornone.com",),
        "homepage": "https://pornone.com/",
    },
    "pornhd": {
        "display_name": "PornHD",
        "allowed_hosts": frozenset({"pornhd.com", "www.pornhd.com", "m.pornhd.com"}),
        "allowed_suffixes": (".pornhd.com",),
        "homepage": "https://www.pornhd.com/",
    },
    "xtube": {
        "display_name": "xTube",
        "allowed_hosts": frozenset({"xtube.com", "www.xtube.com", "m.xtube.com"}),
        "allowed_suffixes": (".xtube.com",),
        "homepage": "https://www.xtube.com/",
    },
    "mofosex": {
        "display_name": "MofoSex",
        "allowed_hosts": frozenset({"mofosex.net", "www.mofosex.net", "m.mofosex.net"}),
        "allowed_suffixes": (".mofosex.net",),
        "homepage": "https://www.mofosex.net/",
    },
    "fapvid": {
        "display_name": "FapVid",
        "allowed_hosts": frozenset({"fapvid.com", "www.fapvid.com", "m.fapvid.com"}),
        "allowed_suffixes": (".fapvid.com",),
        "homepage": "https://www.fapvid.com/",
    },
    "monsterporn": {
        "display_name": "MonsterPorn",
        "allowed_hosts": frozenset({"monsterporn.com", "www.monsterporn.com"}),
        "allowed_suffixes": (".monsterporn.com",),
        "homepage": "https://monsterporn.com/",
    },
    "fetishkitsch": {
        "display_name": "FetishKitsch",
        "allowed_hosts": frozenset({"fetishkitsch.com", "www.fetishkitsch.com"}),
        "allowed_suffixes": (".fetishkitsch.com",),
        "homepage": "https://fetishkitsch.com/",
    },
    "javhihi": {
        "display_name": "JAVHiHi",
        "allowed_hosts": frozenset({"javhihi.com", "www.javhihi.com", "m.javhihi.com"}),
        "allowed_suffixes": (".javhihi.com",),
        "homepage": "https://www.javhihi.com/",
    },
    "tokyoporn": {
        "display_name": "TokyoPorn",
        "allowed_hosts": frozenset({"tokyoporn.com", "www.tokyoporn.com", "m.tokyoporn.com"}),
        "allowed_suffixes": (".tokyoporn.com",),
        "homepage": "https://www.tokyoporn.com/",
    },
    "javwhores": {
        "display_name": "JAVWhores",
        "allowed_hosts": frozenset({"javwhores.com", "www.javwhores.com", "m.javwhores.com"}),
        "allowed_suffixes": (".javwhores.com",),
        "homepage": "https://javwhores.com/",
    },
    "goodporn": {
        "display_name": "GoodPorn",
        "allowed_hosts": frozenset({"goodporn.to", "www.goodporn.to", "m.goodporn.to"}),
        "allowed_suffixes": (".goodporn.to",),
        "homepage": "https://goodporn.to/",
    },
    "porn365": {
        "display_name": "Porn365",
        "allowed_hosts": frozenset({"porn365.to", "www.porn365.to", "m.porn365.to"}),
        "allowed_suffixes": (".porn365.to",),
        "homepage": "https://www.porn365.to/",
    },
    "fapcake": {
        "display_name": "FapCup",
        "allowed_hosts": frozenset({"fapcake.com", "www.fapcake.com", "m.fapcake.com"}),
        "allowed_suffixes": (".fapcake.com",),
        "homepage": "https://fapcake.com/",
    },
    "fux": {
        "display_name": "Fux",
        "allowed_hosts": frozenset({"fux.com", "www.fux.com", "m.fux.com"}),
        "allowed_suffixes": (".fux.com",),
        "homepage": "https://www.fux.com/",
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


# تعریف توابع is_<site>_url
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


# ─── Generic yt-dlp extraction ─────────────────────────────


async def _extract_via_ytdlp(url: str, site_key: str) -> Tuple[List[dict], str]:
    """استخراج کیفیت‌ها با yt-dlp."""
    cfg = SITES[site_key]
    if not shutil.which("yt-dlp"):
        return [], "yt-dlp not installed"
    logger.info("[%s] Extracting via yt-dlp: %s", cfg["display_name"], url[:80])
    return await extract_qualities_with_ytdlp(url, cfg["display_name"])


# ─── Generic factory for site-specific entry points ────────


def _make_site_entrypoints(site_key: str):
    cfg = SITES[site_key]
    sessions_dict: dict = {}

    is_url_fn = globals().get(f"is_{site_key}_url")
    if not is_url_fn:
        is_url_fn = _make_is_url_fn(site_key)
        globals()[f"is_{site_key}_url"] = is_url_fn

    async def extract_qualities(url: str) -> Tuple[List[dict], str]:
        return await _extract_via_ytdlp(url, site_key)

    async def download_direct(url: str, filepath: str, progress_cb: ProgressCallback) -> Tuple[bool, str, int]:
        success, error, size = await _download_direct_impl(
            url, filepath, progress_cb,
            referer=cfg["homepage"],
        )
        if success:
            return True, "", size
        cleanup_file(filepath)
        return False, error, 0

    async def download_m3u8_fn(url: str, filepath: str, progress_cb: ProgressCallback) -> Tuple[bool, str, int]:
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
