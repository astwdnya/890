"""
xhaccess_handler.py
───────────────────
هندلر برای xHAccess (xhaccess.com).

xhaccess.com یک mirror از پلتفرم xHamster هست (همون window.initials،
پخش‌کننده xplayer و CDN xhcdn.com/xhpingcdn.com).

روش استخراج:
  1. window.initials رو با balanced-brace از HTML بیرون می‌کشیم.
  2. از xplayerSettings["sources"]["hls"] فقط استریم h264 HLS رو می‌گیریم
     (ساختار: {"av1":{"url":hex},"h264":{"url":hex}}). hex با
     _ByteGenerator (7 روش) decipher می‌شه → master m3u8.
  3. رزولوشن‌های موجود رو از پارامتر multi= URL master می‌خونه و برای هر
     رزولوشن یک quality جدا می‌سازه (144p..2160p).
  4. دانلود: ارتفاع در fragment URL (#xh_h=H) ذخیره می‌شه و yt-dlp با
     -f best[height<=H] دقیقاً همون رزولوشن رو می‌گیره.

چرا فقط HLS؟
  - مستقیم mp4 (videoModel.sources / standard) روی CDN همیشه «Wrong key»
    (403) می‌ده — مشکل سمت سرور xHamster که yt-dlp هم تاییدش کرده.
  - AV1 روی تلگرام صفحه‌ی سیاه می‌شه، پس فقط h264 پیش می‌شه.
  - master m3u8 از نوع H.264 + AAC و بدون encryption است.

نکته مهم درباره محدودیت جغرافیایی:
  از IP آمریکا، xHamster صفحه‌ی gated (age-verification) برمی‌گردونه که
  videoModel توش نیست. سرور ربات (غیر از آمریکا) صفحه‌ی کامل رو می‌گیره.
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
    download_with_ytdlp as _download_with_ytdlp_impl,
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
    ".xhpingcdn.com",
    "xhpingcdn.com",
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


def _parse_resolutions(m3u8_url: str) -> List[Tuple[int, str]]:
    """رزولوشن‌های موجود در master m3u8 رو از پارامتر multi= URL می‌خونه.

    URL xhcdn شامل multi=WxH:LABEL,WxH:LABEL,... است که رزولوشن‌های
    موجود در master playlist رو نشون می‌ده. برمی‌گرده:
        [(144, "144p"), (240, "240p"), (480, "480p"), ...]
    به‌ترتیب نزولی (بزرگ‌ترین اول). اگه multi= پیدا نشد، [] برمی‌گرده.
    """
    m = re.search(r"multi=([^/]+)", m3u8_url)
    if not m:
        return []
    res: List[Tuple[int, str]] = []
    for part in m.group(1).split(","):
        part = part.strip().rstrip(":")
        mm = re.match(r"(\d+)x(\d+):(\d+p)", part)
        if mm:
            height = int(mm.group(2))
            label = mm.group(3)
            res.append((height, label))
    res.sort(key=lambda r: r[0], reverse=True)
    return res


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

    # ── HLS از xplayerSettings["sources"]["hls"] ──
    # فقط استریم h264 رو می‌گیریم. AV1 روی تلگرام صفحه‌ی سیاه می‌شه (تلگرام
    # AV1 رو دیکد نمی‌کنه) و مستقیم mp4 روی CDN همیشه «Wrong key» (403)
    # می‌ده (مشکل سمت سرور xHamster که yt-dlp هم تاییدش کرده).
    master_m3u8 = None
    xplayer_settings = initials.get("xplayerSettings") or {}
    if isinstance(xplayer_settings, dict):
        xp_sources = xplayer_settings.get("sources")
        if isinstance(xp_sources, dict):
            hls = xp_sources.get("hls")
            if isinstance(hls, dict):
                # codec-keyed: {"av1": {"url": hex}, "h264": {"url": hex}}
                h264_entry = hls.get("h264")
                if isinstance(h264_entry, dict):
                    for key in ("url", "fallback"):
                        raw = h264_entry.get(key)
                        if not raw:
                            continue
                        mu = _decipher_url(raw)
                        if mu and _is_allowed_host(mu):
                            master_m3u8 = mu
                            break
                # fallback: ساختار قدیمی xhamster {"url": hex, "fallback": hex}
                if not master_m3u8:
                    for key in ("url", "fallback"):
                        raw = hls.get(key)
                        if not raw:
                            continue
                        mu = _decipher_url(raw)
                        if mu and _is_allowed_host(mu):
                            master_m3u8 = mu
                            break

    if not master_m3u8:
        return [], (
            "❌ این ویدیو استریم H.264 قابل پخش در تلگرام نداره "
            "(احتمالاً فقط کدک AV1 موجود است که تلگرام پخشش نمی‌کنه)."
        )

    res_list = _parse_resolutions(master_m3u8)
    if res_list:
        for height, rlabel in res_list:
            # ارتفاع رو در fragment URL می‌ذاریم تا دانلودر با -f درست بزنه
            qurl = f"{master_m3u8}#xh_h={height}"
            _REFERERS[qurl] = url
            qualities.append({
                "label": f"📡 {rlabel} (H.264)",
                "url": qurl,
                "method": "m3u8",
            })
    else:
        # master بدون لیست رزولوشن → یک quality Adaptive
        _REFERERS[master_m3u8] = url
        qualities.append({
            "label": "📡 HLS (H.264)",
            "url": master_m3u8,
            "method": "m3u8",
        })

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
    """دانلود HLS/m3u8 از xhaccess/xhcdn با yt-dlp.

    اگر URL fragment ``#xh_h=<height>`` داشته باشه، فقط همون رزولوشن
    (یا نزدیک‌ترین پایین‌تر) با ``-f best[height<=H]`` دانلود می‌شه.
    اگه fragment نبود، بهترین کیفیت پیش‌فرض دانلود می‌شه.
    """
    referer = _REFERERS.get(url, _SITE_REFERER)

    # ارتفاع رو از fragment بکش بیرون و fragment رو از URL حذف کن
    clean_url = url
    format_spec: Optional[str] = None
    if "#" in url:
        base, frag = url.split("#", 1)
        clean_url = base
        m = re.search(r"xh_h=(\d+)", frag)
        if m:
            height = int(m.group(1))
            format_spec = f"best[height<={height}]"

    success, error, size = await _download_with_ytdlp_impl(
        clean_url, filepath, progress_cb,
        referer=referer, format_spec=format_spec,
    )
    if success:
        return True, "", size
    cleanup_file(filepath)
    return False, error, 0
