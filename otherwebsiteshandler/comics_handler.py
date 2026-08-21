"""
comics_handler.py
─────────────────
هندلر عمومی برای سایت‌های کمیک.

این هندلر از 17 سایت پشتیبانی می‌کنه:
  - hdporncomics.com
  - sexkomix2.com
  - hentai.name
  - porncomics.cloud
  - novelcrow.com
  - 3hentai.net
  - erofus.com
  - nhentai.net
  - ilikecomix.com
  - hentai18.net
  - sexcomix.me
  - xlecx.one (تصاویر + ویدیو)
  - comics-moon.com
  - comicsporn.net
  - zzcartoon.com (ویدیو)
  - comicsflix.com
  - eggporncomics.com

نکته مهم: این هندلرها از نوع "کمیک" هستن - نه ویدیو.
ر-bot یه لینک کمیک می‌بینه و:
  1. تصاویر صفحه رو استخراج می‌کنه
  2. PDF می‌سازه
  3. یا تک‌تک تصاویر رو می‌فرسته

برای صفحات سرچ، ربات لیست همه کمیک‌ها رو نشون می‌ده و کاربر می‌تونه انتخاب کنه.
"""

import asyncio
import html as html_lib
import json
import logging
import os
import re
import shutil
import tempfile
import time
from typing import List, Optional, Tuple, Callable, Awaitable
from urllib.parse import urljoin, urlparse

from ._common import (
    ProgressCallback,
    check_impersonation_support,
    cleanup_file,
    default_user_agent,
    download_direct as _download_direct_impl,
    extract_title_from_html,
    fetch_html,
    is_url_in_domains,
)

logger = logging.getLogger("ComicsHandler")

_USER_AGENT = default_user_agent()


# ─── Site configurations ──────────────────────────────────
# هر سایت یه config جداگانه داره:
#   - domain: دامنه اصلی
#   - allowed_hosts: هاست‌های مجاز (شامل CDN)
#   - homepage: URL صفحه اصلی
#   - comic_url_patterns: regex برای تشخیص لینک کمیک
#   - search_url_patterns: regex برای تشخیص لینک سرچ
#   - image_patterns: regex برای استخراج URL تصاویر از صفحه کمیک
#   - comic_link_patterns: regex برای استخراج لینک کمیک از صفحه سرچ

SITES = {
    "hdporncomics": {
        "display_name": "HDPornComics",
        "domain": "hdporncomics.com",
        "allowed_hosts": frozenset({"hdporncomics.com", "www.hdporncomics.com", "e.hdporncomics.com"}),
        "allowed_suffixes": (".hdporncomics.com",),
        "homepage": "https://hdporncomics.com/",
        "comic_url_patterns": [
            r"^https?://(?:www\.)?hdporncomics\.com/[\w\-]+/?$",
        ],
        "search_url_patterns": [
            r"^https?://(?:www\.)?hdporncomics\.com/(?:pcharacter|tag|category)/[\w\-]+/?$",
            r"^https?://(?:www\.)?hdporncomics\.com/(?:pcharacter|tag|category)/[\w\-]+/page/\d+/?$",
        ],
        # تصاویر از e.hdporncomics.com/thumbs/.../NNN.jpg
        "image_patterns": [
            r'https://e\.hdporncomics\.com/[^"\']+\.jpg',
            r'https://e\.hdporncomics\.com/[^"\']+\.webp',
        ],
        "comic_link_patterns": [
            r'href="(https?://(?:www\.)?hdporncomics\.com/[\w\-]+)/?"',
        ],
    },
    "sexkomix2": {
        "display_name": "SexKomix2",
        "domain": "sexkomix2.com",
        "allowed_hosts": frozenset({"sexkomix2.com", "www.sexkomix2.com", "imgen.sexkomix2.com"}),
        "allowed_suffixes": (".sexkomix2.com",),
        "homepage": "https://sexkomix2.com/",
        "comic_url_patterns": [
            r"^https?://(?:www\.)?sexkomix2\.com/comicsx_en/[\w\-]+/?$",
        ],
        "search_url_patterns": [
            r"^https?://(?:www\.)?sexkomix2\.com/tag_pagex/.*$",
            r"^https?://(?:www\.)?sexkomix2\.com/search.*$",
            r"^https?://(?:www\.)?sexkomix2\.com/tag_pagex.*$",
        ],
        "image_patterns": [
            r'https://imgen\.sexkomix2\.com/uploads_images/[^"\']+\.jpg',
            r'https://imgen\.sexkomix2\.com/uploads_images/[^"\']+\.webp',
        ],
        "comic_link_patterns": [
            r'href="(https?://(?:www\.)?sexkomix2\.com/comicsx_en/[\w\-]+)/?"',
        ],
    },
    "hentai_name": {
        "display_name": "Hentai.name",
        "domain": "hentai.name",
        "allowed_hosts": frozenset({"hentai.name", "www.hentai.name"}),
        "allowed_suffixes": (".hentai.name",),
        "homepage": "https://www.hentai.name/",
        # NOTE: This site has Cloudflare 403 protection.
        # May work from some servers but not others.
        "needs_ytdlp_fallback": True,
        "comic_url_patterns": [
            r"^https?://(?:www\.)?hentai\.name/g/\d+/?$",
        ],
        "search_url_patterns": [
            r"^https?://(?:www\.)?hentai\.name/(?:search|tag)/.*$",
        ],
        "image_patterns": [
            r'https://[^"\']*\.hentai\.name/[^"\']+\.(?:jpg|webp|png)',
        ],
        "comic_link_patterns": [
            r'href="(https?://(?:www\.)?hentai\.name/g/\d+)/?"',
        ],
    },
    "porncomics_cloud": {
        "display_name": "PornComics.cloud",
        "domain": "porncomics.cloud",
        "allowed_hosts": frozenset({"porncomics.cloud", "www.porncomics.cloud", "cdn.porncomics.cloud"}),
        "allowed_suffixes": (".porncomics.cloud",),
        "homepage": "https://porncomics.cloud/",
        "comic_url_patterns": [
            r"^https?://(?:www\.)?porncomics\.cloud/books/\d+\.html$",
        ],
        "search_url_patterns": [
            r"^https?://(?:www\.)?porncomics\.cloud/genre-[\w\-]+(?:-page-\d+)?\.html$",
        ],
        # porncomics.cloud: images at cdn.porncomics.cloud/galleries/.../*.{webp,png,jpg}
        "image_patterns": [
            r'https://cdn\.porncomics\.cloud/galleries/[^"\']+\.(?:webp|png|jpg)',
            r'https://cdn\.porncomics\.cloud/\d+/\d+_[^"\']+\.(?:webp|png|jpg)',
        ],
        "comic_link_patterns": [
            r'href="(https?://(?:www\.)?porncomics\.cloud/books/\d+\.html)"',
        ],
    },
    "novelcrow": {
        "display_name": "NovelCrow",
        "domain": "novelcrow.com",
        "allowed_hosts": frozenset({"novelcrow.com", "www.novelcrow.com"}),
        "allowed_suffixes": (".novelcrow.com",),
        "homepage": "https://novelcrow.com/",
        # NOTE: Cloudflare 403 protection.
        "needs_ytdlp_fallback": True,
        "comic_url_patterns": [
            r"^https?://(?:www\.)?novelcrow\.com/comic/[\w\-]+/[\w\-]+/?$",
        ],
        "search_url_patterns": [
            r"^https?://(?:www\.)?novelcrow\.com/comic-genre/[\w\-]+/?$",
        ],
        "image_patterns": [
            r'https://[^"\']*novelcrow\.com/[^"\']+\.(?:jpg|webp|png)',
        ],
        "comic_link_patterns": [
            r'href="(https?://(?:www\.)?novelcrow\.com/comic/[\w\-]+/[\w\-]+)/?"',
        ],
    },
    "3hentai": {
        "display_name": "3Hentai",
        "domain": "3hentai.net",
        "allowed_hosts": frozenset({"3hentai.net", "www.3hentai.net"}),
        "allowed_suffixes": (".3hentai.net",),
        "homepage": "https://3hentai.net/",
        # NOTE: Site appears to be down (404). May come back.
        "is_dead": True,
        "comic_url_patterns": [
            r"^https?://(?:www\.)?3hentai\.net/d/\d+/?$",
        ],
        "search_url_patterns": [
            r"^https?://(?:www\.)?3hentai\.net/tags/[\w\-]+/?\d*$",
        ],
        "image_patterns": [
            r'https://[^"\']*3hentai\.net/[^"\']+\.(?:jpg|webp|png)',
        ],
        "comic_link_patterns": [
            r'href="(https?://(?:www\.)?3hentai\.net/d/\d+)/?"',
        ],
    },
    "erofus": {
        "display_name": "Erofus",
        "domain": "erofus.com",
        "allowed_hosts": frozenset({"erofus.com", "www.erofus.com"}),
        "allowed_suffixes": (".erofus.com",),
        "homepage": "https://www.erofus.com/",
        "comic_url_patterns": [
            r"^https?://(?:www\.)?erofus\.com/comics/[\w\-/]+/?$",
        ],
        "search_url_patterns": [
            r"^https?://(?:www\.)?erofus\.com/\?search=.*$",
        ],
        # erofus: images are relative URLs like /thumb/36193/HASH.jpeg
        "image_patterns": [
            r'/thumb/\d+/[a-f0-9]+\.(?:jpg|jpeg|png)',
        ],
        "comic_link_patterns": [
            r'href="((?:https?://(?:www\.)?erofus\.com)?/comics/[\w\-/]+)"',
        ],
    },
    "nhentai": {
        "display_name": "nhentai",
        "domain": "nhentai.net",
        "allowed_hosts": frozenset({"nhentai.net", "www.nhentai.net", "t1.nhentai.net", "t2.nhentai.net", "t3.nhentai.net", "t4.nhentai.net", "t5.nhentai.net", "t7.nhentai.net", "i.nhentai.net", "i2.nhentai.net", "i3.nhentai.net", "i5.nhentai.net", "i7.nhentai.net"}),
        "allowed_suffixes": (".nhentai.net",),
        "homepage": "https://nhentai.net/",
        "comic_url_patterns": [
            r"^https?://(?:www\.)?nhentai\.net/g/\d+/?$",
        ],
        "search_url_patterns": [
            r"^https?://(?:www\.)?nhentai\.net/search/?\?.*$",
        ],
        # nhentai: thumbnails are https://tN.nhentai.net/galleries/GID/PAGEt.webp
        # Full images: https://i.nhentai.net/galleries/GID/PAGE.jpg
        "image_patterns": [
            r'https://t\d+\.nhentai\.net/galleries/\d+/\d+t\.(?:webp|jpg|png)',
        ],
        "post_process": "nhentai",
        "comic_link_patterns": [
            r'href="((?:https?://(?:www\.)?nhentai\.net)?/g/\d+)/?"',
        ],
    },
    "ilikecomix": {
        "display_name": "ILikeComix",
        "domain": "ilikecomix.com",
        "allowed_hosts": frozenset({"ilikecomix.com", "www.ilikecomix.com"}),
        "allowed_suffixes": (".ilikecomix.com",),
        "homepage": "https://ilikecomix.com/",
        "comic_url_patterns": [
            r"^https?://(?:www\.)?ilikecomix\.com/[\w\-]+/[\w\-]+/?$",
        ],
        "search_url_patterns": [
            r"^https?://(?:www\.)?ilikecomix\.com/(?:comics-tag|category|search)/.*$",
        ],
        "image_patterns": [
            r'https://ilikecomix\.com/comic/[^"\']+\.(?:jpg|webp|png)',
        ],
        "comic_link_patterns": [
            r'href="(https?://(?:www\.)?ilikecomix\.com/[\w\-]+/[\w\-]+)/?"',
        ],
    },
    "hentai18": {
        "display_name": "Hentai18",
        "domain": "hentai18.net",
        "allowed_hosts": frozenset({"hentai18.net", "www.hentai18.net", "cdn.hentai18.net"}),
        "allowed_suffixes": (".hentai18.net",),
        "homepage": "https://hentai18.net/",
        "comic_url_patterns": [
            r"^https?://(?:www\.)?hentai18\.net/read-hentai/[\w\-]+/?$",
        ],
        "search_url_patterns": [
            r"^https?://(?:www\.)?hentai18\.net/search\?.*$",
            r"^https?://(?:www\.)?hentai18\.net/[\w\-]+-sex-comics/?$",
            r"^https?://(?:www\.)?hentai18\.net/[\w\-]+-sex-comics",
        ],
        "image_patterns": [
            r'https://cdn\.hentai18\.net/images/manga/[^"\']+\.jpg',
            r'https://cdn\.hentai18\.net/images/manga/[^"\']+\.webp',
        ],
        "comic_link_patterns": [
            r'href="(https?://(?:www\.)?hentai18\.net/read-hentai/[\w\-]+)/?"',
        ],
    },
    "sexcomix_me": {
        "display_name": "SexComix.me",
        "domain": "sexcomix.me",
        "allowed_hosts": frozenset({"sexcomix.me", "www.sexcomix.me"}),
        "allowed_suffixes": (".sexcomix.me",),
        "homepage": "https://www.sexcomix.me/",
        "comic_url_patterns": [
            r"^https?://(?:www\.)?sexcomix\.me/galleries/[\w\-]+/?$",
        ],
        "search_url_patterns": [
            r"^https?://(?:www\.)?sexcomix\.me/[\w\-]+/\d+/?$",
        ],
        # sexcomix.me: full images are in data-img attribute (single quotes)
        # Pattern: data-img='/photos/galleries/43/201/0_780' data-ext='.jpg'
        # We extract both and combine to get full URL
        "image_patterns": [
            r"""data-img=['"](/photos/galleries/\d+/\d+/\d+_\d+)['"][^>]*data-ext=['"](\.(?:jpg|webp|png))['"]""",
        ],
        "post_process": "sexcomix_me",
        "comic_link_patterns": [
            r'href="((?:https?://(?:www\.)?sexcomix\.me)?/galleries/[\w\-]+)/?"',
        ],
    },
    "xlecx": {
        "display_name": "xlecx",
        "domain": "xlecx.one",
        "allowed_hosts": frozenset({"xlecx.one", "www.xlecx.one"}),
        "allowed_suffixes": (".xlecx.one",),
        "homepage": "https://xlecx.one/",
        "comic_url_patterns": [
            r"^https?://(?:www\.)?xlecx\.one/\d+-[\w\-]+\.html$",
        ],
        "search_url_patterns": [
            r"^https?://(?:www\.)?xlecx\.one/(?:tags|category)/.*$",
        ],
        "image_patterns": [
            r'(?:https?:)?//[^"\']*xlecx\.one/uploads/posts/[^"\']+\.webp',
            r'(?:https?:)?//[^"\']*xlecx\.one/uploads/posts/[^"\']+\.jpg',
        ],
        "video_patterns": [
            r'(?:https?:)?//[^"\']*xlecx\.one/uploads/files/[^"\']+\.mp4',
        ],
        "comic_link_patterns": [
            r'href="(https?://(?:www\.)?xlecx\.one/\d+-[\w\-]+\.html)"',
        ],
    },
    "comics_moon": {
        "display_name": "ComicsMoon",
        "domain": "comics-moon.com",
        "allowed_hosts": frozenset({"comics-moon.com", "www.comics-moon.com"}),
        "allowed_suffixes": (".comics-moon.com",),
        "homepage": "https://comics-moon.com/",
        "comic_url_patterns": [
            r"^https?://(?:www\.)?comics-moon\.com/\d+-[\w\-]+\.html$",
        ],
        "search_url_patterns": [
            r"^https?://(?:www\.)?comics-moon\.com/tags/[\w\-]+/?$",
        ],
        "image_patterns": [
            r'(?:https?:)?//[^"\']*comics-moon\.com/uploads/posts/[^"\']+\.webp',
            r'(?:https?:)?//[^"\']*comics-moon\.com/uploads/posts/[^"\']+\.jpg',
        ],
        "comic_link_patterns": [
            r'href="(https?://(?:www\.)?comics-moon\.com/\d+-[\w\-]+\.html)"',
        ],
    },
    "comicsporn": {
        "display_name": "ComicsPorn",
        "domain": "comicsporn.net",
        "allowed_hosts": frozenset({"comicsporn.net", "www.comicsporn.net", "i2.sigmapic.com", "i3.sigmapic.com", "i4.sigmapic.com", "i5.sigmapic.com"}),
        "allowed_suffixes": (".comicsporn.net", ".sigmapic.com"),
        "homepage": "https://www.comicsporn.net/",
        "comic_url_patterns": [
            r"^https?://(?:www\.)?comicsporn\.net/(?:fa|en|ja)/galleries/[\w\-]+",
        ],
        "search_url_patterns": [
            r"^https?://(?:www\.)?comicsporn\.net/(?:fa|en|ja)/find/[\w\-]+/\d+",
        ],
        # comicsporn: images at iN.sigmapic.com/images/comicsporn.net/galleries/.../*.jpg
        "image_patterns": [
            r'https?://i\d+\.sigmapic\.com/images/comicsporn\.net/galleries/[^"\']+\.jpg',
        ],
        "comic_link_patterns": [
            r'href="((?:https?://(?:www\.)?comicsporn\.net)?/(?:fa|en|ja)/galleries/[\w\-]+)"',
        ],
    },
    "zzcartoon": {
        "display_name": "ZZCartoon",
        "domain": "zzcartoon.com",
        "allowed_hosts": frozenset({"zzcartoon.com", "www.zzcartoon.com", "cdn3.zzcartoon.com", "cdnv1.cumcoming.com"}),
        "allowed_suffixes": (".zzcartoon.com",),
        "homepage": "https://www.zzcartoon.com/",
        "comic_url_patterns": [
            r"^https?://(?:www\.)?zzcartoon\.com/video/[\w\-]+\.html$",
        ],
        "search_url_patterns": [
            r"^https?://(?:www\.)?zzcartoon\.com/search/.*$",
        ],
        # zzcartoon: video URLs use /get_file/ pattern
        "image_patterns": [],
        "video_patterns": [
            r'https?://[^"\']*zzcartoon\.com/get_file/[^"\']+\.mp4',
            r'(?:https?:)?//[^"\']*zzcartoon\.com/contents/videos_screenshots/[^"\']+preview\.mp4',
        ],
        "comic_link_patterns": [
            r'href="(https?://(?:www\.)?zzcartoon\.com/video/[\w\-]+\.html)"',
        ],
    },
    "comicsflix": {
        "display_name": "ComicsFlix",
        "domain": "comicsflix.com",
        "allowed_hosts": frozenset({"comicsflix.com", "www.comicsflix.com"}),
        "allowed_suffixes": (".comicsflix.com",),
        "homepage": "https://comicsflix.com/",
        "comic_url_patterns": [
            r"^https?://(?:www\.)?comicsflix\.com/comic-porno/[\w\-]+/?$",
        ],
        "search_url_patterns": [
            r"^https?://(?:www\.)?comicsflix\.com/(?:categorias|buscar)/.*$",
        ],
        "image_patterns": [
            r'https://comicsflix\.com/uploads/[^"\']+\.(?:jpg|webp|png)',
        ],
        "comic_link_patterns": [
            r'href="(https?://(?:www\.)?comicsflix\.com/comic-porno/[\w\-]+)/?"',
        ],
    },
    "eggporncomics": {
        "display_name": "EggPornComics",
        "domain": "eggporncomics.com",
        "allowed_hosts": frozenset({"eggporncomics.com", "www.eggporncomics.com"}),
        "allowed_suffixes": (".eggporncomics.com",),
        "homepage": "https://eggporncomics.com/",
        "comic_url_patterns": [
            r"^https?://(?:www\.)?eggporncomics\.com/comics/\d+/[\w\-]+/?$",
        ],
        "search_url_patterns": [
            r"^https?://(?:www\.)?eggporncomics\.com/(?:search|category-tag)/.*$",
        ],
        # eggporncomics: images at /images/postImg/COMIC_ID/thumb300_IMG_ID.webp
        "image_patterns": [
            r'/images/postImg/\d+/thumb300_\d+\.(?:webp|jpg|png)',
        ],
        "comic_link_patterns": [
            r'href="((?:https?://(?:www\.)?eggporncomics\.com)?/comics/\d+/[\w\-]+)/?"',
        ],
    },
}


# ─── Helper functions ──────────────────────────────────────


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


def _is_comic_url(url: str, site_key: str) -> bool:
    """بررسی می‌کنه آیا URL یه صفحه کمیک هست (نه سرچ)."""
    cfg = SITES[site_key]
    for pattern in cfg.get("comic_url_patterns", []):
        if re.match(pattern, url, re.I):
            return True
    return False


def _is_search_url(url: str, site_key: str) -> bool:
    """بررسی می‌کنه آیا URL یه صفحه سرچ هست."""
    cfg = SITES[site_key]
    for pattern in cfg.get("search_url_patterns", []):
        if re.match(pattern, url, re.I):
            return True
    return False


def _is_allowed_host(url: str, site_key: str) -> bool:
    cfg = SITES[site_key]
    try:
        host = (urlparse(url).hostname or "").lower()
        return host in cfg["allowed_hosts"] or any(
            host.endswith(s) for s in cfg["allowed_suffixes"]
        )
    except Exception:
        return False


def _make_absolute_url(url: str, base_url: str) -> str:
    """تبدیل URL نسبی به مطلق."""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http"):
        return url
    if url.startswith("/"):
        parsed = urlparse(base_url)
        return f"{parsed.scheme}://{parsed.netloc}{url}"
    return urljoin(base_url, url)


# ─── URL detection ─────────────────────────────────────────


def _make_is_url_fn(site_key: str):
    """ساخت تابع is_<site>_url برای یه سایت."""
    cfg = SITES[site_key]

    def is_url(url: str) -> bool:
        if not url:
            return False
        try:
            host = (urlparse(url).hostname or "").lower()
            return host in cfg["allowed_hosts"] or any(
                host.endswith(s) for s in cfg["allowed_suffixes"]
            )
        except Exception:
            return False

    is_url.__name__ = f"is_{site_key}_url"
    return is_url


# تعریف توابع is_<site>_url
for _site_key in SITES:
    globals()[f"is_{_site_key}_url"] = _make_is_url_fn(_site_key)


# ─── Extraction ────────────────────────────────────────────


async def _fetch_page(url: str, site_key: str) -> Tuple[Optional[str], int]:
    """fetch HTML صفحه با curl_cffi."""
    cfg = SITES[site_key]
    return await fetch_html(
        url=url,
        referer=cfg["homepage"],
        visit_homepage_first=cfg["homepage"],
    )


def _extract_images_from_html(html: str, site_key: str, base_url: str) -> List[str]:
    """استخراج URL تصاویر کمیک از HTML."""
    cfg = SITES[site_key]
    images = []
    seen = set()
    
    for pattern in cfg.get("image_patterns", []):
        for m in re.finditer(pattern, html, re.I):
            # اگه pattern گروه‌های capture داره (مثل sexcomix.me)، اون‌ها رو ترکیب کن
            if m.groups():
                # ترکیب همه گروه‌ها (group1 + group2 + ...)
                url = "".join(m.groups())
            else:
                url = m.group(0).replace('"', '').replace("'", "")
                url = url.split('"')[0].split("'")[0]
            url = _make_absolute_url(url, base_url)
            
            # Skip فقط آیکون‌ها و لوگوها
            if any(s in url.lower() for s in ['avatar', 'logo', 'favicon', 'icon-', 'banner']):
                continue
            # Skip اگر خیلی کوتاهه
            if len(url) < 20:
                continue
            
            if url not in seen:
                seen.add(url)
                images.append(url)
    
    return images


def _extract_videos_from_html(html: str, site_key: str, base_url: str) -> List[str]:
    """استخراج URL ویدیو از HTML."""
    cfg = SITES[site_key]
    videos = []
    seen = set()
    
    for pattern in cfg.get("video_patterns", []):
        for m in re.finditer(pattern, html, re.I):
            url = m.group(0).replace('"', '').replace("'", "")
            url = url.split('"')[0].split("'")[0]
            url = _make_absolute_url(url, base_url)
            
            if url not in seen:
                seen.add(url)
                videos.append(url)
    
    return videos


def _post_process_images(images: List[str], site_key: str) -> List[str]:
    """پردازش بعد از استخراج تصاویر - مثلا تبدیل thumbnail به full image."""
    cfg = SITES[site_key]
    post_process = cfg.get("post_process", "")
    
    if post_process == "nhentai":
        # nhentai: تبدیل thumbnail به full image
        # Thumbnail: https://t4.nhentai.net/galleries/4008082/76t.webp
        # Full:      https://i7.nhentai.net/galleries/4008082/76.jpg (try multiple CDN hosts)
        # CDN hosts: i2, i5, i7 - all work
        processed = []
        for url in images:
            # استخراج gallery_id و page از URL
            m = re.match(
                r'https://t\d+\.nhentai\.net/galleries/(\d+)/(\d+)t\.(?:webp|jpg|png)',
                url
            )
            if m:
                gallery_id = m.group(1)
                page = m.group(2)
                # Use i7 first (most reliable), fallback to i5, i2
                full_url = f"https://i7.nhentai.net/galleries/{gallery_id}/{page}.jpg"
                processed.append(full_url)
            else:
                processed.append(url)
        return processed
    
    if post_process == "sexcomix_me":
        # sexcomix.me: data-img="/photos/galleries/43/201/0_780" + data-ext=".jpg"
        # → full URL: https://www.sexcomix.me/photos/galleries/43/201/0_780.jpg
        processed = []
        for url in images:
            # URL is already the full path from data-img + data-ext
            if url.startswith('/'):
                full_url = "https://www.sexcomix.me" + url
            else:
                full_url = url
            processed.append(full_url)
        return processed
    
    return images


def _extract_comic_links_from_search_html(html: str, site_key: str, base_url: str) -> List[Tuple[str, str]]:
    """استخراج لینک‌های کمیک از صفحه سرچ."""
    cfg = SITES[site_key]
    links = []
    seen = set()
    
    for pattern in cfg.get("comic_link_patterns", []):
        for m in re.finditer(pattern, html, re.I):
            url = m.group(1) if m.groups() else m.group(0)
            url = _make_absolute_url(url, base_url)
            
            if url not in seen and _is_comic_url(url, site_key):
                seen.add(url)
                # پیدا کردن title از HTML context
                title = _extract_title_for_link(html, url)
                links.append((url, title or url.split("/")[-1]))
    
    return links


def _extract_title_for_link(html: str, link_url: str) -> str:
    """سعی می‌کنه title از HTML پیدا کنه."""
    # این یه پیاده‌سازی ساده‌ست - می‌تونه بهتر بشه
    try:
        # پیدا کردن context حول link
        idx = html.find(link_url)
        if idx >= 0:
            # Look for title attribute or nearby text
            context = html[max(0, idx - 500):idx + 500]
            # title attribute
            m = re.search(r'title="([^"]+)"', context)
            if m:
                return html_lib.unescape(m.group(1))
            # alt attribute
            m = re.search(r'alt="([^"]+)"', context)
            if m:
                return html_lib.unescape(m.group(1))
    except Exception:
        pass
    return ""


async def extract_comic_info(url: str, site_key: Optional[str] = None) -> Optional[dict]:
    """
    استخراج اطلاعات کمیک از صفحه.
    
    Returns:
        dict با فیلدهای:
        - title: عنوان کمیک
        - images: لیست URL تصاویر
        - videos: لیست URL ویدیوها (اگه سایت ویدیو داره)
        - site_key: کلید سایت
        - display_name: نام نمایشی سایت
    """
    if not site_key:
        site_key = _get_site_key_from_url(url)
    if not site_key:
        logger.warning("Unknown comic site: %s", url)
        return None
    
    cfg = SITES[site_key]
    
    logger.info("[%s] Fetching comic page: %s", cfg["display_name"], url[:80])
    
    html, status = await _fetch_page(url, site_key)
    if not html:
        logger.warning("[%s] Failed to fetch page (HTTP %s)", cfg["display_name"], status)
        return None
    
    title = extract_title_from_html(html, cfg["display_name"])
    images = _extract_images_from_html(html, site_key, url)
    videos = _extract_videos_from_html(html, site_key, url)
    
    # Post-process images (e.g. nhentai: thumbnail → full image)
    images = _post_process_images(images, site_key)
    
    if not images and not videos:
        logger.warning("[%s] No images or videos found on page", cfg["display_name"])
        return None
    
    logger.info("[%s] Found %d images, %d videos for: %s",
                cfg["display_name"], len(images), len(videos), title[:60])
    
    return {
        "title": title,
        "images": images,
        "videos": videos,
        "site_key": site_key,
        "display_name": cfg["display_name"],
        "url": url,
    }


async def extract_comic_search_results(url: str, site_key: Optional[str] = None) -> Optional[dict]:
    """
    استخراج لیست کمیک‌ها از صفحه سرچ.
    
    Returns:
        dict با فیلدهای:
        - comics: لیست (url, title) کمیک‌ها
        - site_key: کلید سایت
        - display_name: نام نمایشی سایت
        - search_url: URL سرچ
    """
    if not site_key:
        site_key = _get_site_key_from_url(url)
    if not site_key:
        logger.warning("Unknown comic site: %s", url)
        return None
    
    cfg = SITES[site_key]
    
    logger.info("[%s] Fetching search page: %s", cfg["display_name"], url[:80])
    
    html, status = await _fetch_page(url, site_key)
    if not html:
        logger.warning("[%s] Failed to fetch search page (HTTP %s)", cfg["display_name"], status)
        return None
    
    comics = _extract_comic_links_from_search_html(html, site_key, url)
    
    if not comics:
        logger.warning("[%s] No comic links found on search page", cfg["display_name"])
        return None
    
    logger.info("[%s] Found %d comics on search page", cfg["display_name"], len(comics))
    
    return {
        "comics": comics,
        "site_key": site_key,
        "display_name": cfg["display_name"],
        "search_url": url,
    }


# ─── Download images and build PDF ────────────────────────


async def download_comic_images(
    images: List[str],
    out_dir: str,
    site_key: str,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    max_concurrent: int = 5,
) -> Optional[List[str]]:
    """دانلود همه تصاویر کمیک به‌صورت موازی."""
    if not images:
        return None
    
    os.makedirs(out_dir, exist_ok=True)
    cfg = SITES[site_key]
    total = len(images)
    
    logger.info("[%s] Downloading %d images", cfg["display_name"], total)
    
    paths: List[Optional[str]] = [None] * total
    sem = asyncio.Semaphore(max_concurrent)
    done_counter = [0]
    counter_lock = asyncio.Lock()
    
    if not check_impersonation_support():
        logger.error("curl_cffi not available")
        return None
    
    from curl_cffi.requests import AsyncSession
    
    async with AsyncSession() as s:
        async def download_one(idx: int, img_url: str):
            async with sem:
                # اسم فایل: 001.jpg, 002.jpg, etc.
                ext = ".jpg"
                for e in [".webp", ".png", ".gif", ".jpeg"]:
                    if e in img_url.lower():
                        ext = e
                        break
                local_name = f"{idx + 1:03d}{ext}"
                out_path = os.path.join(out_dir, local_name)
                
                for attempt in range(3):
                    try:
                        r = await s.get(
                            img_url,
                            impersonate="chrome",
                            timeout=30,
                            headers={
                                "User-Agent": _USER_AGENT,
                                "Referer": cfg["homepage"],
                            },
                        )
                        if r.status_code == 200 and r.content:
                            with open(out_path, "wb") as f:
                                f.write(r.content)
                            paths[idx] = out_path
                            async with counter_lock:
                                done_counter[0] += 1
                                if progress_cb:
                                    try:
                                        await progress_cb(done_counter[0], total, img_url) \
                                            if asyncio.iscoroutinefunction(progress_cb) \
                                            else progress_cb(done_counter[0], total, img_url)
                                    except Exception:
                                        pass
                            return
                        elif r.status_code in (429, 503):
                            await asyncio.sleep(1.5 * (attempt + 1))
                        else:
                            logger.warning("[%s] image %d HTTP %d", cfg["display_name"], idx, r.status_code)
                            await asyncio.sleep(0.5)
                    except Exception as e:
                        logger.warning("[%s] image %d attempt %d: %s", cfg["display_name"], idx, attempt, e)
                        await asyncio.sleep(1 * (attempt + 1))
        
        await asyncio.gather(*[download_one(i, url) for i, url in enumerate(images)])
    
    valid_paths = [p for p in paths if p]
    logger.info("[%s] Downloaded %d/%d images", cfg["display_name"], len(valid_paths), total)
    return valid_paths if valid_paths else None


async def download_comic_video(
    video_url: str,
    out_path: str,
    site_key: str,
    progress_cb: ProgressCallback,
) -> Tuple[bool, str, int]:
    """دانلود ویدیوی کمیک."""
    from ._common import download_direct_multi, download_direct
    
    cfg = SITES[site_key]
    
    # اول multi-segment
    success, error, size = await download_direct_multi(
        video_url, out_path, progress_cb,
        referer=cfg["homepage"],
    )
    if success:
        return True, "", size
    cleanup_file(out_path)
    # fallback
    success, error, size = await download_direct(
        video_url, out_path, progress_cb,
        referer=cfg["homepage"],
    )
    if success:
        return True, "", size
    cleanup_file(out_path)
    return False, error, 0


async def build_comic_pdf(
    images: List[str],
    out_path: str,
    site_key: str,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> Optional[str]:
    """دانلود تصاویر و ساخت PDF."""
    cfg = SITES[site_key]
    
    out_dir = tempfile.mkdtemp(prefix=f"comic_{site_key}_")
    try:
        # Step 1: Download images
        if progress_cb:
            try:
                await progress_cb(0, len(images), "Downloading images...") \
                    if asyncio.iscoroutinefunction(progress_cb) \
                    else progress_cb(0, len(images), "Downloading images...")
            except Exception:
                pass
        
        img_paths = await download_comic_images(images, out_dir, site_key, progress_cb)
        if not img_paths:
            logger.error("[%s] No images downloaded", cfg["display_name"])
            return None
        
        # Step 2: Build PDF (sequential append)
        if progress_cb:
            try:
                await progress_cb(len(img_paths), len(img_paths), "Building PDF...") \
                    if asyncio.iscoroutinefunction(progress_cb) \
                    else progress_cb(len(img_paths), len(img_paths), "Building PDF...")
            except Exception:
                pass
        
        img_paths.sort()
        
        # اطمینان از وجود پوشه‌ی parent
        parent_dir = os.path.dirname(out_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)
        
        loop = asyncio.get_event_loop()
        result_path = await loop.run_in_executor(
            None, _build_pdf_sequential, img_paths, out_path
        )
        return result_path
    finally:
        try:
            shutil.rmtree(out_dir, ignore_errors=True)
        except Exception:
            pass


def _build_pdf_sequential(img_paths: List[str], out_path: str) -> Optional[str]:
    """ساخت PDF با PIL sequential append."""
    if not img_paths:
        return None
    
    from PIL import Image
    
    saved_count = 0
    
    for i, p in enumerate(img_paths):
        try:
            img = Image.open(p)
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            elif img.mode != "RGB":
                img = img.convert("RGB")
            
            if i == 0:
                img.save(out_path, "PDF")
            else:
                with open(out_path, "r+b") as f:
                    img.save(f, "PDF", append=True)
            img.close()
            saved_count += 1
        except Exception as e:
            logger.warning("Failed to add %s to PDF: %s", p, e)
            continue
    
    if saved_count == 0:
        return None
    
    logger.info("PDF created: %s (%d/%d images)", out_path, saved_count, len(img_paths))
    return out_path


# ─── Sessions for state management ────────────────────────

# session dict برای هر سایت (برای نگه‌داری state بین callback ها)
for _site_key in SITES:
    globals()[f"{_site_key}_sessions"] = {}


# ─── Expose entry points ───────────────────────────────────

# توابع اصلی که bot.py استفاده می‌کنه:
# - is_<site>_url(url): تشخیص URL
# - extract_<site>_comic_info(url): استخراج اطلاعات کمیک
# - extract_<site>_search_results(url): استخراج نتایج سرچ
# - download_<site>_comic_pdf(images, out_path): ساخت PDF
# - download_<site>_comic_video(url, out_path): دانلود ویدیو
