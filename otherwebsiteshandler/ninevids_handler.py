"""
ninevids_handler.py
──────────────────
هندلر اختصاصی 9vids.com — ریورس‌انجینیر شده (🆕)

معماری سایت (RE):
  - وردپرس + پلاگین اختصاصی «clean-tube-player» (نه KVS، نه kt_player!)
  - صفحه‌ی ویدیو، پلیر رو داخل iframe می‌ذاره:
      <iframe src="https://9vids.com/wp-content/plugins/clean-tube-player/
              public/player-x.php?q=BASE64" frameborder="0" ...>
  - پارامتر q بعد از base64-decode + percent-decode این شکلیه:
      post_id=17489&type=video&tag=<video controls
        src="https://cdn3.9vids.com/media/2026/10/XXXX.mp4"
        poster="https://9vids.com/wp-content/uploads/2026/10/XXXX.jpg"></video>
  - یعنی لینک MP4 مستقیم (بدون توکن، بدون چند کیفیت) همون‌جای پارامتره!

روش کار:
  1. fetch صفحه‌ی ویدیو با curl_cffi (impersonate chrome — دور زدن CF)
  2. پیدا کردن iframe پلاگین و base64-decode پارامتر q
  3. استخراج src (MP4 مستقیم) از داخل تگ video دیکدشده
  4. دانلود مستقیم با Referer سایت + fallback به yt-dlp

نکته‌ی RE: CDN (cdnN.9vids.com — nginx جدا از CF) ممکنه بعضی IPها
(به‌خصوص دیتاسنتر) رو با 403 بلاک کنه؛ برای همین بعد از direct، مسیر
yt-dlp (با referer و impersonation خودش) هم امتحان میشه.

ساختار URL ویدیوها:
  https://9vids.com/<term>/<slug>/
  مثل: /mother-videos/...  /daughter-videos/...  /ai-taboo/...  /just-for-fun/...
  (صفحات سیستمی: /category/x/ ، /feed/ ، /dmca/ ، /2257-2/ — ویدیو نیستن)
"""

import asyncio
import base64
import logging
import re
from typing import List, Optional, Tuple
from urllib.parse import unquote, urlparse

from ._common import (
    ProgressCallback,
    check_impersonation_support,
    cleanup_file,
    download_direct as _download_direct_impl,
    download_direct_multi as _download_direct_multi_impl,
    download_m3u8 as _download_m3u8_impl,
    download_with_ytdlp,
    extract_title_from_html,
    fetch_html,
)

logger = logging.getLogger("NineVidsHandler")


# ─── Site configuration ────────────────────────────────────

DISPLAY_NAME = "9Vids"
DOMAIN = "9vids.com"
HOMEPAGE = "https://9vids.com/"

# صفحه‌ی ویدیو: ۹vids.com/<term>/<slug>/ — دوتا سگمنت که اولش «category»
# یا صفحات سیستمی نباشه. www هم قبوله.
_URL_RE = re.compile(
    r"^https?://(?:www\.)?9vids\.com/"
    r"(?!category(?:/|$)|feed(?:/|$)|dmca(?:/|$)|2257(?:/|$)|wp-)"
    r"[\w\-]+/[\w\-]+/?$",
    re.I,
)

# هاست‌های مجاز دانلود — cdn1/cdn2/cdn3/... با suffix پوشش داده میشن
ALLOWED_HOSTS = frozenset({"9vids.com", "www.9vids.com"})
ALLOWED_SUFFIXES = (".9vids.com",)

# iframe پلاگین پلیر — گروه ۲ = مقدار base64 پارامتر q
_PLAYER_IFRAME_RE = re.compile(
    r'<iframe[^>]+src=["\']([^"\']*clean-tube-player[^"\']*?'
    r"q=([A-Za-z0-9+/\-_=]+)[^\"']*)['\"]",
    re.I,
)
# تگ video داخل payload دیکدشده
_VIDEO_TAG_SRC_RE = re.compile(r'<video[^>]*\bsrc=["\']([^"\']+)["\']', re.I)
# fallback: هر MP4 مستقیم از CDN داخل خود صفحه
_RAW_MP4_RE = re.compile(
    r"https?://cdn\d*\.9vids\.com/[^\s\"'<>]+\.mp4", re.I
)


def is_ninevids_url(url: str) -> bool:
    """تشخیص URL صفحه‌ی ویدیوی 9vids."""
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if host not in ALLOWED_HOSTS:
        return False
    return bool(_URL_RE.match(url))


def _decode_player_q(b64: str) -> Optional[str]:
    """base64-decode + percent-decode پارامتر q پلیر.

    tolerant نسبت به padding ناقص و base64 از نوع URL-safe (‎-_‎)."""
    if not b64:
        return None
    candidates = [b64]
    stripped = b64.rstrip("=")
    pad = (-len(stripped)) % 4
    if pad:
        candidates.append(stripped + "=" * pad)
    for s in candidates:
        try:
            raw = base64.b64decode(s, altchars=b"-_")
            return unquote(raw.decode("utf-8", "ignore"))
        except Exception:
            continue
    return None


# ─── Quality extraction ────────────────────────────────────


async def extract_ninevids_qualities(url: str) -> Tuple[List[dict], str]:
    """استخراج لینک MP4 مستقیم از صفحه‌ی ویدیوی 9vids.

    Returns:
        Tuple (qualities_list, title). اگه خطا باشه ([], error_msg).
    """
    if not is_ninevids_url(url):
        return [], "Invalid 9vids URL"
    if not check_impersonation_support():
        return [], "curl_cffi لازمه: pip install curl_cffi"

    logger.info("[9Vids] Fetching: %s", (url or "")[:80])
    html, status = await fetch_html(url, referer=HOMEPAGE)
    if not html:
        return [], f"Could not fetch page (HTTP {status})"

    title = extract_title_from_html(html, DISPLAY_NAME)

    mp4_url: Optional[str] = None
    poster_url: Optional[str] = None

    # ── مسیر ۱ (اصلی): iframe پلاگین → base64 → تگ video ──
    for m in _PLAYER_IFRAME_RE.finditer(html):
        decoded = _decode_player_q(m.group(2))
        if not decoded:
            continue
        mv = _VIDEO_TAG_SRC_RE.search(decoded)
        if mv:
            mp4_url = mv.group(1).strip()
            mp = re.search(r'\bposter=["\']([^"\']+)["\']', decoded, re.I)
            poster_url = mp.group(1).strip() if mp else None
            break

    # ── مسیر ۲ (fallback): تگ video مستقیم داخل صفحه ──
    if not mp4_url:
        mv = re.search(
            r'<video[^>]*\bsrc=["\']([^"\']+\.mp4[^"\']*)["\']', html, re.I
        )
        if mv:
            mp4_url = mv.group(1).strip()

    # ── مسیر ۳ (fallback): هر لینک MP4 از CDN داخل صفحه ──
    if not mp4_url:
        mv = _RAW_MP4_RE.search(html)
        if mv:
            mp4_url = mv.group(0)

    if not mp4_url:
        logger.warning("[9Vids] No mp4 source found | title=%s", title[:60])
        return [], "No playable video source found"

    # گارد هاست — لینک باید مال دامنه‌ی خود سایت باشه (نه تبلیغ/ردایرکت)
    try:
        host = (urlparse(mp4_url).hostname or "").lower()
    except Exception:
        host = ""
    if not host or (
        host not in ALLOWED_HOSTS and not host.endswith(ALLOWED_SUFFIXES)
    ):
        logger.warning("[9Vids] Unexpected video host: %s", host)
        return [], f"Unexpected video host: {host}"

    qualities: List[dict] = [
        {
            "label": "📡 Original MP4",
            "url": mp4_url,
            "method": "direct",
        }
    ]
    # پوستر رو برای دیباگ تو لاگ نگه می‌داریم (ممکنه بعداً برای تامبنیل
    # استفاده بشه — الان send_file_with_progress خودش تامبنیل می‌سازه)
    if poster_url:
        logger.debug("[9Vids] poster: %s", poster_url[:100])

    logger.info("[9Vids] Extracted: %s | %s", title[:60], mp4_url[:80])
    return qualities, title


# ─── Download ──────────────────────────────────────────────


async def download_ninevids_direct(
    url: str, filepath: str, progress_cb: ProgressCallback
) -> Tuple[bool, str, int]:
    """دانلود MP4 مستقیم از CDN — سه مسیر: multi-segment → direct → yt-dlp."""
    if not url:
        return False, "Empty URL", 0
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        host = ""
    if not host or (
        host not in ALLOWED_HOSTS and not host.endswith(ALLOWED_SUFFIXES)
    ):
        return False, "URL host not allowed", 0

    # ۱) دانلود چندسگمنتی موازی (۱۶ وورکر — سریع‌ترین)
    success, error, size = await _download_direct_multi_impl(
        url, filepath, progress_cb, referer=HOMEPAGE
    )
    if success:
        return True, "", size
    cleanup_file(filepath)

    # ۲) دانلود مستقیم ساده
    success, error, size = await _download_direct_impl(
        url, filepath, progress_cb, referer=HOMEPAGE
    )
    if success:
        return True, "", size
    cleanup_file(filepath)

    # ۳) fallback: yt-dlp (impersonation داخلی خودش + referer)
    success, error, size = await download_with_ytdlp(
        url,
        filepath,
        progress_cb,
        referer=HOMEPAGE,
        extra_headers={"Referer": HOMEPAGE},
    )
    if success:
        return True, "", size
    cleanup_file(filepath)
    return False, error, 0


async def download_ninevids_m3u8(
    url: str, filepath: str, progress_cb: ProgressCallback
) -> Tuple[bool, str, int]:
    """9vids معمولاً MP4 مستقیم میده؛ این مسیر فقط برای سازگاری اینترفیس
    و حالت‌های خاص (اگه روزی HLS گذاشت) با yt-dlp کار می‌کنه."""
    success, error, size = await _download_m3u8_impl(
        url, filepath, progress_cb, referer=HOMEPAGE
    )
    if success:
        return True, "", size
    cleanup_file(filepath)
    return False, error, 0


# ─── Session store (برای _make_site_handler در bot.py) ─────
ninevids_sessions: dict = {}
