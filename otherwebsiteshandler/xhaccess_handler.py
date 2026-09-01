"""
xhaccess_handler.py
───────────────────
هندلر برای xHAccess (xhaccess.com).

xhaccess.com یک mirror از پلتفرم xHamster هست (همون window.initials،
پخش‌کننده xplayer و CDN است xhcdn.com). روش استخراج دقیقاً بر اساس
ساختار JSON رسمی xHamster پیاده‌سازی شده (مطابق منطق extractor مرجع yt-dlp):

  1. window.initials رو با balanced-brace از HTML بیرون می‌کشیم.
  2. qualities مستقیم mp4 از initials["videoModel"]["sources"] می‌آد
     (دیکشنری: format_id -> {quality: url}). کلید "download" رد می‌شه.
     این URLها معمولاً plain هستن و بدون decipher عبور می‌کنن.
  3. کیفیت HLS از initials["xplayerSettings"]["sources"]["hls"]["url"]
     (و "fallback") می‌آد. این URLها ممکنه hex-ciphertext باشن و با
     الگوریتم _ByteGenerator (7 روش) decipher بشن.

نکته مهم درباره محدودیت جغرافیایی:
  از IP آمریکا، xHamster/yhaccess صفحه‌ی ویدیو رو با یک صفحه‌ی gated
  (age-verification قانون ویرجینیا) برمی‌گردونه که videoModel توش نیست.
  سرور ربات (غیر از آمریکا) صفحه‌ی کامل با videoModel رو می‌گیره.
  اگه videoModel پیدا نشد، هندلر پیام واضح برمی‌گردونه (نه crash).
"""

import json
import logging
import re
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

logger = logging.getLogger("XHAccessHandler")

_USER_AGENT = default_user_agent()

_SITE_URL = "https://xhaccess.com"
_SITE_REFERER = f"{_SITE_URL}/"

# هاست‌های مجاز: خود سایت + CDN ویدیوی xHamster (xhcdn.com و ساب‌دامین‌هاش).
_ALLOWED_HOSTS = frozenset({
    "xhaccess.com",
    "www.xhaccess.com",
    "m.xhaccess.com",
})

_ALLOWED_HOST_SUFFIXES = (
    ".xhaccess.com",
    "xhaccess.com",
    ".xhcdn.com",
    "xhcdn.com",
)

# ذخیره‌ی referer دقیق صفحه‌ی ویدیو برای هر media-url (برای دانلود).
# yt-dlp هم از URL صفحه‌ی ویدیو به‌عنوان Referer استفاده می‌کنه.
_REFERERS: dict = {}

xhaccess_sessions: dict = {}


# ─── URL detection ─────────────────────────────────────────


def is_xhaccess_url(url: str) -> bool:
    """تشخیص URL ویدیوی xhaccess.com (صفحه‌ی /videos/...)."""
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
        return host in _ALLOWED_HOSTS or any(
            host.endswith(s) for s in (".xhaccess.com", "xhaccess.com")
        )
    except Exception:
        return False


def _is_allowed_host(url: str) -> bool:
    """بررسی اینکه media-url روی هاست مجاز (سایت یا CDN) هست یا نه."""
    return is_url_in_domains(url, _ALLOWED_HOSTS, _ALLOWED_HOST_SUFFIXES)


# ─── Hex decipher (xHamster ciphertext) ───────────────────


_HEX_RE = r'[0-9a-fA-F]{12,}'


def _int32(x: int) -> int:
    """تبدیل به signed int32 (معادل yt-dlp.utils.int_to_int32)."""
    x &= 0xFFFFFFFF
    return x - 0x100000000 if x & 0x80000000 else x


class _ByteGenerator:
    """مولد بایت برای decipher کردن URLهای hex-ciphertext xHamster.

    port شده از yt-dlp.extractor.xhamster._ByteGenerator.
    algo_id (بایت اول) یکی از الگوریتم‌های _algo1.._algo7 رو انتخاب می‌کنه.
    """

    def __init__(self, algo_id: int, seed: int):
        self._algorithm = getattr(self, f"_algo{algo_id}", None)
        if self._algorithm is None:
            raise ValueError(f"Unknown algorithm ID {algo_id!r}")
        self._s = _int32(seed)

    def _algo1(self, s):
        # LCG (a=1664525, c=1013904223, m=2^32)
        self._s = _int32(s * 1664525 + 1013904223)
        return self._s

    def _algo2(self, s):
        # xorshift32
        s = _int32(s ^ (s << 13))
        s = _int32(s ^ ((s & 0xFFFFFFFF) >> 17))
        self._s = _int32(s ^ (s << 5))
        return self._s

    def _algo3(self, s):
        # Weyl Sequence + MurmurHash3 (fmix32)
        self._s = _int32(s + 0x9E3779B9)
        s = self._s
        s = _int32(s ^ ((s & 0xFFFFFFFF) >> 16))
        s = _int32(s * _int32(0x85EBCA77))
        s = _int32(s ^ ((s & 0xFFFFFFFF) >> 13))
        s = _int32(s * _int32(0xC2B2AE3D))
        return _int32(s ^ ((s & 0xFFFFFFFF) >> 16))

    def _algo4(self, s):
        # Custom scrambling با left rotation (ROL 7)
        self._s = _int32(s + 0x6D2B79F5)
        s = self._s
        s = _int32((s << 7) | ((s & 0xFFFFFFFF) >> 25))
        s = _int32(s + 0x9E3779B9)
        s = _int32(s ^ ((s & 0xFFFFFFFF) >> 11))
        return _int32(s * 0x27D4EB2D)

    def _algo5(self, s):
        # xorshift variant با final addition
        s = _int32(s ^ (s << 7))
        s = _int32(s ^ ((s & 0xFFFFFFFF) >> 9))
        s = _int32(s ^ (s << 8))
        self._s = _int32(s + 0xA5A5A5A5)
        return self._s

    def _algo6(self, s):
        # LCG با variable right shift scrambler
        self._s = _int32(s * _int32(0x2C9277B5) + _int32(0xAC564B05))
        s = self._s
        s2 = _int32(s ^ ((s & 0xFFFFFFFF) >> 18))
        shift = (s & 0xFFFFFFFF) >> 27 & 31
        return _int32((s2 & 0xFFFFFFFF) >> shift)

    def _algo7(self, s):
        # Weyl Sequence + custom multiply-xor-shift mixing
        self._s = _int32(s + _int32(0x9E3779B9))
        s = self._s
        e = _int32(s ^ (s << 5))
        e = _int32(e * _int32(0x7FEB352D))
        e = _int32(e ^ ((e & 0xFFFFFFFF) >> 15))
        return _int32(e * _int32(0x846CA68B))

    def __next__(self):
        return self._algorithm(self._s) & 0xFF

    def __iter__(self):
        return self


def _decipher_hex(hex_string: str) -> Optional[str]:
    """Decipher یک رشته‌ی hex-ciphertext کامل به URL/mainfest."""
    try:
        byte_data = bytes.fromhex(hex_string)
    except Exception:
        return None
    if len(byte_data) < 6:
        return None
    seed = int.from_bytes(byte_data[1:5], byteorder="little", signed=True)
    try:
        gen = _ByteGenerator(byte_data[0], seed)
    except Exception:
        return None
    try:
        return bytearray(b ^ next(gen) for b in byte_data[5:]).decode("latin-1")
    except Exception:
        return None


def _decipher_url(format_url) -> Optional[str]:
    """Decipher غیرمخرب یک candidate URL.

    - plain URL (http...) → تغییر نکرده برمی‌گرده.
    - hex-ciphertext کامل → decipher می‌شه.
    - URL که مسیرش با /<hex>[/,]... شروع می‌شه → فقط همون segment decipher می‌شه.

    این تابع روی همه‌ی candidate‌ها (mp4 و hls) امن اعمال می‌شه:
    URLهای ساده دست‌نخورده عبور می‌کنن.
    """
    if not isinstance(format_url, str) or not format_url:
        return None
    s = format_url.strip()
    if re.fullmatch(_HEX_RE, s):
        return _decipher_hex(s)
    if not (s.startswith("http://") or s.startswith("https://")):
        return None  # نه URL نه hex معتبر
    parsed = urlparse(s)
    m = re.match(rf"^/({_HEX_RE})([/,].+)$", parsed.path)
    if not m:
        return s  # plain URL — دست‌نخورده
    hex_part, remainder = m.group(1), m.group(2)
    deciphered = _decipher_hex(hex_part)
    if not deciphered:
        return None
    return parsed._replace(path=f"/{deciphered}{remainder}").geturl()


# ─── initials JSON extraction ──────────────────────────────


def _extract_initials(html: str):
    """استخراج و parse کردن window.initials = {...}; با balanced-brace.

    Returns dict یا None.
    """
    marker = "window.initials"
    idx = 0
    while True:
        pos = html.find(marker, idx)
        if pos == -1:
            return None
        # بعد از marker، whitespace‌ها رو رد کن تا به '=' برسی
        j = pos + len(marker)
        while j < len(html) and html[j] in " \t\r\n":
            j += 1
        if j >= len(html) or html[j] != "=":
            idx = pos + 1
            continue
        j += 1
        while j < len(html) and html[j] in " \t\r\n":
            j += 1
        if j >= len(html) or html[j] != "{":
            idx = pos + 1
            continue
        # balanced-brace extraction
        start = j
        depth = 0
        in_str = False
        esc = False
        k = j
        while k < len(html):
            c = html[k]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        obj_text = html[start:k + 1]
                        try:
                            return json.loads(obj_text)
                        except Exception:
                            idx = pos + 1
                            break
            k += 1
        else:
            idx = pos + 1


# ─── Quality extraction ────────────────────────────────────


async def extract_xhaccess_qualities(url: str) -> Tuple[List[dict], str]:
    """استخراج کیفیت‌های ویدیو از xhaccess.com.

    Returns:
      (qualities, title) — qualities لیست dict با کلیدهای label/url/method.
      اگه صفحه gated بود یا ویدیو پیدا نشد، ([], error_msg) برمی‌گرده.
    """
    if not is_xhaccess_url(url):
        return [], "Invalid URL"

    if not check_impersonation_support():
        return await extract_qualities_with_ytdlp(url, "xHAccess")

    logger.info("[xHAccess] Fetching: %s", url[:80])

    html, status = await fetch_html(
        url=url,
        referer=_SITE_REFERER,
        visit_homepage_first=_SITE_URL,
    )
    if not html:
        return [], (
            "❌ دریافت صفحه ناموفق بود. ممکنه محدودیت جغرافیایی/سنی فعال باشه "
            "یا سایت موقتاً در دسترس نباشه."
        )

    initials = _extract_initials(html)

    if not initials or "videoModel" not in initials:
        # صفحه gated هست (از US IP، videoModel وجود نداره)
        title = extract_title_from_html(html, "xHAccess")
        logger.warning(
            "[xHAccess] videoModel not found (geo/age-gate likely). title=%r",
            title[:60],
        )
        return [], (
            "❌ ویدیو پیدا نشد — صفحه از این IP محدود شده (geo/age-gate). "
            "سرور باید غیر از آمریکا باشه تا صفحه‌ی کامل با منابع ویدیو بیاد."
        )

    video = initials.get("videoModel") or {}
    if not isinstance(video, dict):
        return [], "❌ ساختار videoModel نامعتبر بود."

    title = (
        video.get("title")
        or extract_title_from_html(html, "xHAccess")
        or "xHAccess Video"
    )

    qualities: List[dict] = []
    seen_urls: set = set()

    # ── 1) qualities مستقیم mp4 از videoModel["sources"] ──
    sources = video.get("sources")
    if isinstance(sources, dict):
        for fmt_id, fmt_dict in sources.items():
            if fmt_id == "download":
                # لینک download هنوز تولید نشده، رد می‌شه
                continue
            if not isinstance(fmt_dict, dict):
                continue
            for quality, fmt_item in fmt_dict.items():
                raw = fmt_item if isinstance(fmt_item, str) else (
                    fmt_item.get("url") if isinstance(fmt_item, dict) else None
                )
                u = _decipher_url(raw)
                if not u or u in seen_urls:
                    continue
                if not _is_allowed_host(u):
                    logger.debug("[xHAccess] skipping non-allowed host: %s", u[:80])
                    continue
                seen_urls.add(u)
                _REFERERS[u] = url
                qlabel = str(quality) if quality else "Auto"
                qualities.append({
                    "label": f"📡 {qlabel}",
                    "url": u,
                    "method": "direct",
                })

    # ── 2) HLS از xplayerSettings["sources"]["hls"] ──
    xplayer_settings = initials.get("xplayerSettings") or {}
    if isinstance(xplayer_settings, dict):
        xp_sources = xplayer_settings.get("sources")
        if isinstance(xp_sources, dict):
            hls = xp_sources.get("hls")
            if isinstance(hls, dict):
                for key in ("url", "fallback"):
                    raw = hls.get(key)
                    if not raw:
                        continue
                    hu = _decipher_url(raw)
                    if not hu or hu in seen_urls:
                        continue
                    if not _is_allowed_host(hu):
                        logger.debug("[xHAccess] skipping non-allowed hls host: %s", hu[:80])
                        continue
                    seen_urls.add(hu)
                    _REFERERS[hu] = url
                    hls_label = "📡 HLS (Adaptive)" if key == "url" else "📡 HLS (Fallback)"
                    qualities.append({
                        "label": hls_label,
                        "url": hu,
                        "method": "m3u8",
                    })
            # ── 3) standard sources (mp4/m3u8 آبی) ──
            standard = xp_sources.get("standard")
            if isinstance(standard, dict):
                for identifier, fmt_list in standard.items():
                    if not isinstance(fmt_list, list):
                        continue
                    for fmt in fmt_list:
                        if not isinstance(fmt, dict):
                            continue
                        for key in ("url", "fallback"):
                            raw = fmt.get(key)
                            if not raw:
                                continue
                            su = _decipher_url(raw)
                            if not su or su in seen_urls:
                                continue
                            if not _is_allowed_host(su):
                                continue
                            seen_urls.add(su)
                            _REFERERS[su] = url
                            qlabel = (
                                str(fmt.get("quality") or fmt.get("label") or identifier)
                            )
                            if su.split("?")[0].lower().endswith(".m3u8"):
                                method = "m3u8"
                                label = "📡 HLS (Adaptive)"
                            else:
                                method = "direct"
                                label = f"📡 {qlabel}"
                            qualities.append({
                                "label": label,
                                "url": su,
                                "method": method,
                            })

    if not qualities:
        return [], (
            "❌ هیچ کیفیت‌ای پیدا نشد. ممکنه ویدیو حذف شده باشه یا "
            "محدودیت جغرافیایی/سنی فعال باشه."
        )

    qualities.sort(key=quality_sort_key, reverse=True)
    logger.info(
        "[xHAccess] Extracted %d qualities for: %s",
        len(qualities), title[:60],
    )
    return qualities, title


# ─── Download ─────────────────────────────────────────────


async def download_xhaccess_direct(
    url: str,
    filepath: str,
    progress_cb: ProgressCallback,
) -> Tuple[bool, str, int]:
    """دانلود مستقیم mp4 از xhaccess/xhcdn با multi-segment (16x سریع‌تر)."""
    if not _is_allowed_host(url):
        return False, "URL host not allowed", 0
    referer = _REFERERS.get(url, _SITE_REFERER)
    # اول multi-segment (16 worker موازی)
    success, error, size = await _download_direct_multi_impl(
        url, filepath, progress_cb, referer=referer,
    )
    if success:
        return True, "", size
    # fallback به direct ساده
    cleanup_file(filepath)
    success, error, size = await _download_direct_impl(
        url, filepath, progress_cb, referer=referer,
    )
    if success:
        return True, "", size
    cleanup_file(filepath)
    return False, error, 0


async def download_xhaccess_m3u8(
    url: str,
    filepath: str,
    progress_cb: ProgressCallback,
) -> Tuple[bool, str, int]:
    """دانلود HLS/m3u8 از xhaccess/xhcdn با yt-dlp."""
    referer = _REFERERS.get(url, _SITE_REFERER)
    success, error, size = await _download_m3u8_impl(
        url, filepath, progress_cb, referer=referer,
    )
    if success:
        return True, "", size
    cleanup_file(filepath)
    return False, error, 0
