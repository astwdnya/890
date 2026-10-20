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

روش کار (🆕 نسخه‌ی امضا-دار — بعد از دیباگ 403):
  1. fetch صفحه‌ی ویدیو با curl_cffi (impersonate chrome — دور زدن CF)
  2. پیدا کردن iframe پلاگین و base64-decode پارامتر q
     ⚠️ لینکی که از q درمیاد «بدون امضاست» و CDN روش 403 میده!
  3. 🆕 فچ خود player-x.php با curl_cffi (بدون impersonation خودش 403 میده —
     TLS فینگرپرینت چک می‌کنه) → HTML پلیر لینک «امضاشده» میده:
        <video src="https://cdnN.9vids.com/media/....mp4?md5=HASH&expires=TS">
     (nginx secure_link — md5 + expires ~۸ ساعت)
  4. دانلود لینک امضاشده با Referer: آدرسِ کاملِ آیفریم پلیر (نه هوم‌پیج!)

نکته‌ی RE (تست‌شده با پراب واقعی):
  • لینک بدون امضا → 403 همیشه (مهم نیست چه هدری بدی)
  • لینک امضاشده بدون Referer → 403
  • لینک امضاشده + Referer آیفریم → 200 ✅ (CDN خودش TLS ایمیپرشن نمی‌خواد)
  • player-x.php بدون TLS ایمیپریشن → 403 (حتماً curl_cffi لازمه)
  برای همین بعد از direct، مسیر yt-dlp (با referer و impersonation خودش) هم امتحان میشه.

ساختار URL ویدیوها:
  https://9vids.com/<term>/<slug>/
  مثل: /mother-videos/...  /daughter-videos/...  /ai-taboo/...  /just-for-fun/...
  (صفحات سیستمی: /category/x/ ، /feed/ ، /dmca/ ، /2257-2/ — ویدیو نیستن)
"""

import asyncio
import base64
import html as _htmlmod
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


# 🆕 کشِ رفرر/امضا — key: URL پایه‌ی mp4 (بدون query)، value: آیفریم + صفحه‌ی مبدأ
# دانلود فقط URL رو می‌گیره؛ پس رفررِ درست (آیفریم پلیر) رو اینجا نگه می‌داریم
_SIGNED_REF: dict = {}
_SIGNED_REF_MAX = 200


def _signed_ref_put(base_url: str, iframe_url: str, page_url: str) -> None:
    """ثبت رفرر آیفریم برای یه mp4 (با جلوگیری از رشد بی‌نهایت)."""
    if not base_url:
        return
    if len(_SIGNED_REF) >= _SIGNED_REF_MAX:
        try:
            _SIGNED_REF.pop(next(iter(_SIGNED_REF)))
        except Exception:
            pass
    _SIGNED_REF[base_url] = {"iframe": iframe_url or "", "page": page_url or ""}


def _signed_ref_get(url: str) -> dict:
    """رفرر آیفریم مربوط به یه mp4 (با یا بدون query)."""
    base = (url or "").split("?", 1)[0]
    return _SIGNED_REF.get(base) or {}


async def _fresh_signed_url(iframe_url: str, page_url: str) -> Optional[str]:
    """🆕 گرفتن لینک امضاشده‌ی تازه از player-x.php (nginx secure_link).

    player-x.php بدون TLS ایمیپریشن 403 میده — fetch_html با curl_cffi این
    رو حل می‌کنه. خروجی: لینک mp4 با ‎?md5=...&expires=...‎ یا None."""
    if not iframe_url:
        return None
    try:
        phtml, pstatus = await fetch_html(iframe_url, referer=page_url or HOMEPAGE)
    except Exception as e:
        logger.warning("[9Vids] player fetch error: %s", e)
        return None
    if not phtml or pstatus != 200:
        logger.warning("[9Vids] player fetch failed (HTTP %s) — لینک امضاشده نشد", pstatus)
        return None
    mv = _VIDEO_TAG_SRC_RE.search(phtml)
    if not mv:
        return None
    signed = _htmlmod.unescape(mv.group(1).strip())
    if "md5=" not in signed:
        return None
    return signed


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
    iframe_url: Optional[str] = None
    for m in _PLAYER_IFRAME_RE.finditer(html):
        decoded = _decode_player_q(m.group(2))
        if not decoded:
            continue
        mv = _VIDEO_TAG_SRC_RE.search(decoded)
        if mv:
            mp4_url = mv.group(1).strip()
            mp = re.search(r'\bposter=["\']([^"\']+)["\']', decoded, re.I)
            poster_url = mp.group(1).strip() if mp else None
            iframe_url = m.group(1).strip()
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

    # 🆕 ثبت رفرر آیفریم برای دانلود (حتی اگه امضا شکست بخوره)
    _signed_ref_put(mp4_url.split("?", 1)[0], iframe_url, url)

    # ── 🆕 مسیر امضا: فچ player-x.php با ایمیپریشن → لینک امضاشده ──
    # لینکِ داخل q «بدون امضا»ست و CDN روش 403 میده؛ امضای واقعی (md5+expires)
    # رو خود player-x.php موقع لود تولید می‌کنه (nginx secure_link، ~۸ ساعت).
    if iframe_url:
        signed = await _fresh_signed_url(iframe_url, url)
        if signed:
            try:
                host2 = (urlparse(signed).hostname or "").lower()
            except Exception:
                host2 = ""
            if host2 and (host2 in ALLOWED_HOSTS or host2.endswith(ALLOWED_SUFFIXES)):
                _signed_ref_put(signed.split("?", 1)[0], iframe_url, url)
                mp4_url = signed
                logger.info("[9Vids] signed URL obtained (md5+expires)")
            else:
                logger.warning("[9Vids] signed URL host unexpected: %s", host2)
        else:
            logger.warning(
                "[9Vids] signing failed — لینک بدون امضا مونده؛ دانلود احتمالاً 403 میشه"
            )

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
    """دانلود MP4 مستقیم از CDN — multi-segment → direct → re-sign → yt-dlp.

    🆕 نکته‌ی مهم (بعد از دیباگ 403): CDN فقط «لینک امضاشده» (‎?md5=&expires=)
    رو با «Referer: آدرس کامل آیفریم پلیر» قبول می‌کنه؛ لینک خام از q همیشه 403 میده."""
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

    # 🆕 رفررِ درست = آدرس کامل آیفریم پلیر (از کشِ extract) — نه هوم‌پیج
    ref_info = _signed_ref_get(url)
    referer = ref_info.get("iframe") or HOMEPAGE
    iframe_url = ref_info.get("iframe") or ""
    page_url = ref_info.get("page") or HOMEPAGE

    # ۱) دانلود چندسگمنتی موازی (۱۶ وورکر — سریع‌ترین)
    success, error, size = await _download_direct_multi_impl(
        url, filepath, progress_cb, referer=referer
    )
    if success:
        return True, "", size
    cleanup_file(filepath)

    # ۲) دانلود مستقیم ساده
    success, error, size = await _download_direct_impl(
        url, filepath, progress_cb, referer=referer
    )
    if success:
        return True, "", size
    cleanup_file(filepath)

    # ۳) 🆕 re-sign: لینک ممکنه منقضی/بدون امضا باشه — امضای تازه از پلیر
    fresh = await _fresh_signed_url(iframe_url, page_url)
    if fresh and fresh != url:
        logger.info("[9Vids] re-signed URL — retry download")
        success, error, size = await _download_direct_multi_impl(
            fresh, filepath, progress_cb, referer=referer
        )
        if success:
            return True, "", size
        cleanup_file(filepath)
        success, error, size = await _download_direct_impl(
            fresh, filepath, progress_cb, referer=referer
        )
        if success:
            return True, "", size
        cleanup_file(filepath)

    # ۴) fallback آخر: yt-dlp (impersonation داخلی خودش + referer آیفریم)
    #    نکته: CDN خودش TLS ایمیپریشن نمی‌خواد — فقط لینک امضاشده + referer
    success, error, size = await download_with_ytdlp(
        url,
        filepath,
        progress_cb,
        referer=referer,
        extra_headers={"Referer": referer},
    )
    if success:
        return True, "", size
    cleanup_file(filepath)
    if not fresh:
        error = (
            (error or "")[:150]
            + " — CDN لینک بدون امضا رو 403 می‌کنه؛ curl_cffi نصبه؟ "
            "(pip install curl_cffi)"
        )
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
