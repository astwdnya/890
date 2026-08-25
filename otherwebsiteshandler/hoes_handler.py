"""
hoes_handler.py
───────────────
هندلر دانلود ویدیو از hoes.tube (سایت KVS-based با flashvars player).

ساختار سایت hoes.tube:
  - صفحه‌ی ویدیو: https://hoes.tube/videos/<id>/<slug>/
  - صفحه‌ی embed:  https://hoes.tube/embed/<id>
  - پلیر: KVS kt_player با flashvars JS object

الگوی استخراج (از روی مهندسی معکوس فعلیم):
  داخل HTML صفحه، یه بلوک JS هست به این شکل:
    var flashvars = {
        video_id: '187866',
        video_title: '...',
        license_code: '$385447312716581',
        video_url: 'https://hoes.tube/get_file/1/<hash>/<xx000>/<id>/<id>.mp4/?v-acctoken=<base64>',
        video_url_text: '480p',
        video_alt_url: 'https://hoes.tube/get_file/1/<hash>/<xx000>/<id>/<id>_720p.mp4/?v-acctoken=<base64>',
        video_alt_url_text: '720p',
        video_alt_url_hd: '1',
        ...
    };

  پس برای استخراج کیفیت‌ها:
    1. fetch HTML صفحه
    2. پیدا کردن var flashvars = { ... };
    3. استخراج جفت‌های (video_url, video_url_text) و (video_alt_url, video_alt_url_text)
    4. اگه ویدیو فقط یه کیفیت داشت (مثلاً فقط 480p)، فقط همون رو می‌دیم

  URL ویدیو خودش شامل token هست (v-acctoken)، نیازی به کار اضافه نیست.
  سرور Range رو پشتیبانی می‌کنه (HTTP 206) پس دانلود multi-segment کار می‌کنه.

روش کار:
  - is_hoes_url(url): تشخیص URL صفحه ویدیو
  - extract_hoes_qualities(url): استخراج لیست کیفیت‌ها
  - download_hoes_direct(url, ...): دانلود مستقیم mp4 (multi-segment)
  - download_hoes_m3u8(url, ...): fallback اگه m3u8 پیدا شد (با yt-dlp)
"""

import asyncio
import html as html_lib
import logging
import re
from typing import List, Optional, Tuple
from urllib.parse import urlparse

from ._common import (
    ProgressCallback,
    check_impersonation_support,
    cleanup_file,
    download_direct as _download_direct_impl,
    download_direct_multi as _download_direct_multi_impl,
    download_m3u8 as _download_m3u8_impl,
    extract_title_from_html,
    fetch_html,
    is_url_in_domains,
    quality_sort_key,
)

logger = logging.getLogger("HoesHandler")

# ─── Site config ────────────────────────────────────────────

DISPLAY_NAME = "Hoes.tube"
DOMAIN = "hoes.tube"
HOMEPAGE = "https://hoes.tube/"

# الگوی URL صفحه ویدیو: /videos/<id>/<slug>/
# الگوی URL embed:      /embed/<id>
URL_PATTERNS = (
    re.compile(r"^https?://(?:www\.|m\.)?hoes\.tube/videos/\d+/[\w\-]+/?$", re.I),
    re.compile(r"^https?://(?:www\.|m\.)?hoes\.tube/embed/\d+/?$", re.I),
)

ALLOWED_HOSTS = frozenset({
    "hoes.tube",
    "www.hoes.tube",
    "m.hoes.tube",
    "st.hoes.tube",
})

# هاست‌های مجاز برای URL ویدیو (mp4):
# - خود دامنه hoes.tube (دارای v-acctoken)
# - st.hoes.tube برای screenshot/trailer (نباید دانلود بشه به عنوان ویدیو)
ALLOWED_VIDEO_HOSTS = frozenset({
    "hoes.tube",
    "www.hoes.tube",
    "m.hoes.tube",
})

# session store (در صورت نیاز به حفظ کوکی/سشن - فعلاً خالی)
hoes_sessions: dict = {}


# ─── URL detection ──────────────────────────────────────────


def is_hoes_url(url: str) -> bool:
    """
    تشخیص این که URL متعلق به hoes.tube هست یا نه.

    URL‌های پشتیبانی‌شده:
      - https://hoes.tube/videos/<id>/<slug>/
      - https://hoes.tube/embed/<id>
      - https://www.hoes.tube/...
      - https://m.hoes.tube/...
    """
    if not url:
        return False
    for p in URL_PATTERNS:
        if p.match(url):
            return True
    # fallback: hostname check
    try:
        host = (urlparse(url).hostname or "").lower()
        if host in ALLOWED_HOSTS:
            # اگه روی همون دامنه هست ولی الگوی exact match نشد،
            # بازم بذار قبول کنه (مثلاً با کوئری ?source=... یا همراه با hash)
            return True
    except Exception:
        pass
    return False


# ─── Quality extraction ─────────────────────────────────────


# Pattern‌های استخراج از flashvars JS object
# هر جفت (url, text) رو جدا استخراج می‌کنیم چون ممکنه ترتیب attribute‌ها
# تو source متفاوت باشه و همیشه video_url قبل از video_url_text نمیاد.
_RE_FLASHVARS_BLOCK = re.compile(
    r"var\s+flashvars\s*=\s*\{(.*?)\}\s*;",
    re.DOTALL | re.IGNORECASE,
)

# استخراج مقدار یه key از داخل flashvars block
def _extract_flashvar_value(flashvars_block: str, key: str) -> Optional[str]:
    """
    استخراج مقدار key از داخل فلش‌وارز.
    الگوی کلی: key: 'value'
    """
    pat = re.compile(
        rf"\b{re.escape(key)}\s*:\s*[\"']([^\"']*)[\"']",
        re.IGNORECASE,
    )
    m = pat.search(flashvars_block)
    return m.group(1) if m else None


# تمام key‌های احتمالی کیفیت‌ها که KVS player استفاده می‌کنه
# (به ترتیب اولویت: هر چه بهتر)
# KVS player از این keyها استفاده می‌کنه:
#   video_url         + video_url_text         → کیفیت اصلی (معمولاً 480p)
#   video_alt_url     + video_alt_url_text     → کیفیت جایگزین (معمولاً 720p)
#   video_hd_url      + video_hd_url_text      → کیفیت HD (گاهی 1080p)
#   video_alt_url2    + video_alt_url2_text     → کیفیت سوم
#   video_alt_url3    + video_alt_url3_text     → کیفیت چهارم
_QUALITY_KEY_PAIRS = [
    ("video_hd_url", "video_hd_url_text", "1080p"),
    ("video_alt_url3", "video_alt_url3_text", None),
    ("video_alt_url2", "video_alt_url2_text", None),
    ("video_alt_url", "video_alt_url_text", None),
    ("video_url", "video_url_text", None),
]


async def extract_hoes_qualities(url: str) -> Tuple[List[dict], str]:
    """
    استخراج کیفیت‌های ویدیو از hoes.tube.

    Args:
        url: URL صفحه ویدیو (https://hoes.tube/videos/<id>/<slug>/)

    Returns:
        Tuple (qualities_list, title).
        qualities_list: [{label, url, method}, ...]
        اگه خطا باشه: ([], error_msg).
    """
    if not check_impersonation_support():
        return [], "curl_cffi لازمه: pip install curl_cffi"

    logger.info("[Hoes] Fetching: %s", url[:80])

    # fetch صفحه ویدیو با referer به homepage
    html, status = await fetch_html(
        url=url,
        referer=HOMEPAGE,
        visit_homepage_first=HOMEPAGE,
    )
    if not html:
        return [], f"Could not fetch page (HTTP {status})"

    title = extract_title_from_html(html, DISPLAY_NAME)

    # پیدا کردن بلوک flashvars
    m_block = _RE_FLASHVARS_BLOCK.search(html)
    if not m_block:
        logger.warning("[Hoes] No flashvars block found on page")
        return [], "Video player (flashvars) not found on page"

    flashvars = m_block.group(1)

    qualities: List[dict] = []
    seen_urls: set = set()

    for url_key, text_key, fallback_label in _QUALITY_KEY_PAIRS:
        u = _extract_flashvar_value(flashvars, url_key)
        if not u:
            continue
        # skip preview/trailer URLs
        u_lower = u.lower()
        if "_preview" in u_lower or "trailer_" in u_lower or "preview.mp4" in u_lower:
            continue
        # skip if URL is not on allowed video host
        try:
            host = (urlparse(u).hostname or "").lower()
            if host and host not in ALLOWED_VIDEO_HOSTS:
                logger.debug("[Hoes] Skipping non-allowed host for %s: %s", url_key, host)
                continue
        except Exception:
            pass
        # skip duplicates
        if u in seen_urls:
            continue

        # تشخیص label کیفیت
        label = _extract_flashvar_value(flashvars, text_key) or ""
        if not label:
            # fallback: از URL حدس بزن
            m_q = re.search(r"_(\d{3,4})p?\.mp4", u)
            if m_q:
                label = f"{m_q.group(1)}p"
            elif fallback_label:
                label = fallback_label
            else:
                label = "Auto"

        # اگه label خالی بود (مثلاً فقط یه کیفیت اصلی داشت)، "Default" بذار
        if not label:
            label = "Default"

        seen_urls.add(u)
        qualities.append({
            "label": f"📡 {label}",
            "url": u,
            "method": "direct",  # hoes.tube همیشه mp4 مستقیم می‌ده (با v-acctoken)
        })

    # اگه هیچ video_url/alt_url پیدا نشد، fallback به استخراج همه‌ی get_file URL‌ها
    if not qualities:
        # الگوی fallback: هر get_file URL با یه حجم > 1MB
        for m in re.finditer(
            r"https?://[^\s\"'<>]+get_file/\d+/[a-f0-9]+/\d+/\d+/[^\s\"'?]+\.mp4/[^\s\"'<>]*",
            html,
            re.IGNORECASE,
        ):
            u = m.group(0)
            u_lower = u.lower()
            # skip preview/trailer
            if "_preview" in u_lower or "trailer_" in u_lower or "preview.mp4" in u_lower:
                continue
            if u in seen_urls:
                continue
            try:
                host = (urlparse(u).hostname or "").lower()
                if host and host not in ALLOWED_VIDEO_HOSTS:
                    continue
            except Exception:
                continue
            # تشخیص label
            m_q = re.search(r"_(\d{3,4})p?\.mp4", u)
            label = f"{m_q.group(1)}p" if m_q else "Auto"
            seen_urls.add(u)
            qualities.append({
                "label": f"📡 {label}",
                "url": u,
                "method": "direct",
            })

    if not qualities:
        logger.warning("[Hoes] No video sources found on page")
        return [], "No playable video sources found on page"

    # مرتب‌سازی از کیفیت بالا به پایین
    qualities.sort(key=quality_sort_key, reverse=True)
    logger.info("[Hoes] Extracted %d qualities for: %s",
               len(qualities), title[:60])
    return qualities, title


# ─── Download ───────────────────────────────────────────────


async def download_hoes_direct(
    url: str, filepath: str, progress_cb: ProgressCallback
) -> Tuple[bool, str, int]:
    """
    دانلود مستقیم mp4 از hoes.tube.

    Args:
        url: URL مستقیم mp4 (با v-acctoken)
        filepath: مسیر فایل خروجی
        progress_cb: callback پیشرفت

    Returns:
        Tuple (success, error, size_bytes).
    """
    # اعتبارسنجی host
    if not is_url_in_domains(url, ALLOWED_VIDEO_HOSTS):
        return False, "URL host not allowed for hoes.tube", 0

    # اول سعی کن با multi-segment (16 worker موازی - سریع‌تر)
    success, error, size = await _download_direct_multi_impl(
        url, filepath, progress_cb,
        referer=HOMEPAGE,
    )
    if success:
        return True, "", size

    # fallback به direct ساده اگه multi-segment شکست خورد
    cleanup_file(filepath)
    success, error, size = await _download_direct_impl(
        url, filepath, progress_cb,
        referer=HOMEPAGE,
    )
    if success:
        return True, "", size

    cleanup_file(filepath)
    return False, error, 0


async def download_hoes_m3u8(
    url: str, filepath: str, progress_cb: ProgressCallback
) -> Tuple[bool, str, int]:
    """
    دانلود m3u8 (HLS) از hoes.tube.
    hoes.tube معمولاً mp4 مستقیم می‌ده، ولی اگه m3u8 پیدا شد از yt-dlp استفاده می‌کنیم.
    """
    success, error, size = await _download_m3u8_impl(
        url, filepath, progress_cb, referer=HOMEPAGE
    )
    if success:
        return True, "", size
    cleanup_file(filepath)
    return False, error, 0


# ─── Self-test ──────────────────────────────────────────────


async def _self_test():
    """تست سریع - اجرا با: python -m otherwebsiteshandler.hoes_handler"""
    test_url = "https://hoes.tube/videos/187866/practicing-sex-film-with-step-sister/"
    print(f"is_hoes_url({test_url}) = {is_hoes_url(test_url)}")
    qs, title = await extract_hoes_qualities(test_url)
    print(f"title: {title}")
    print(f"qualities ({len(qs)}):")
    for q in qs:
        print(f"  {q['label']}  →  {q['url'][:90]}...")
        print(f"     method={q['method']}")


if __name__ == "__main__":
    asyncio.run(_self_test())
