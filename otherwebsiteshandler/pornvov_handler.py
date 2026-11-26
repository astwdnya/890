"""
pornvov_handler.py
──────────────────
هندلر اختصاصی pornvov.net — ریورس‌انجینیر شده (🆕)

معماری سایت (RE — تست‌شده با پراب واقعی):
  - پلیر kt_player با محافظت «generate_mp4» (کد لایسنس KVS)
  - صفحه‌ی embed این شکلیه:
      generate_mp4('BASE64_PAYLOAD', 'KEY16', 'VIDS_NUMBER', 'VIDEO_ID');
    که BASE64_PAYLOAD یک JSON با ساختار زیره:
      {"ciphertext": "<base64>", "iv": "<hex16>",
       "salt": "<hex64>", "iterations": 999}
  - کلاس Encryption داخل kt_player_new.js decrypt می‌کنه با:
      AES-256-CBC + PBKDF2-SHA512 (keySize=32B، iterations از JSON)
  - 🔥 خروجی decrypt یک «session_key» برای API عمومی OK.ru هست!
      (یعنی pornvov در واقع ری‌امبدِ ویدیوهای OK.ru است)
  - بعدش پلیر این درخواست رو می‌زنه:
      https://api.ok.ru/fb.do?application_key=CBAFJIICABABABABA
        &fields=video.url_tiny,...&method=video.get
        &session_key=<DECRYPTED>&vids=<VIDS_NUMBER>
    و جواب JSON ویدیوهای مستقیم okcdn.ru میده:
      url_tiny / url_low / url_mobile / url_medium / url_high /
      (url_fullhd / url_ultrahd / url_quadhd اگه موجود باشه)

نکته‌های تست‌شده:
  • API ok.ru با impersonate=chrome «XML» برمی‌گردونه! بدون impersonation
    (فقط UA) جواب JSON میده → برای API حتماً impersonate=None
  • لینک‌های okcdn امضای srcIp (IP مبدا) + expires دارن؛ برای همین دانلود
    اول لینک extract رو امتحان می‌کنه، اگه نشد «زنجیره رو از نو» اجرا
    می‌کنه (لینک تازه برای IP فعلی) — آدرس صفحه توی #fragment لینک ذخیره میشه
  • okcdn Range/پارالول کاملاً پشتیبانی می‌کنه (تست ۱۲ کانکشن همزمان ✓)
  • لینک get_stream/88-720.mp4?md5=... خود صفحه‌ی ویدیو همیشه «error» میده
    (حتی با cookie/referer) — فقط مسیر OK.ru جواب میده
  • رفرر/کوکی لازم نیست؛ دانلود با و بدون impersonation هر دو OK

کیفیت‌ها (مپ OK.ru):
  tiny=144p  low=240p  mobile=360p  medium=480p  high=720p
  fullhd=1080p  ultrahd=1440p  quadhd=2160p
"""

import asyncio
import base64
import json
import logging
import re
from typing import List, Optional, Tuple
from urllib.parse import unquote

from ._common import (
    ProgressCallback,
    cleanup_file,
    download_direct as _download_direct_impl,
    download_direct_multi as _download_direct_multi_impl,
    download_m3u8 as _download_m3u8_impl,
    extract_title_from_html,
)

logger = logging.getLogger("PornVovHandler")


# ─── Site configuration ────────────────────────────────────

DISPLAY_NAME = "PornVov"
DOMAIN = "pornvov.net"
HOMEPAGE = "https://www.pornvov.net/"

_URL_RE = re.compile(
    r"^https?://(?:www\.)?pornvov\.net/(?:video/[\w\-]+/?|embed/\d+/??)$",
    re.I,
)

# generate_mp4 در صفحه‌ی embed
_GENERATE_MP4_RE = re.compile(
    r"generate_mp4\(\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*'([^']+)'"
)
# id ویدیو از لینک embed یا صفحه‌ی ویدیو
_EMBED_ID_RE = re.compile(r"/embed/(\d+)")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# مپ کلیدهای API OK.ru → لیبل کیفیت (به‌ترتیب از بهترین)
_OKRU_QUALITY_MAP = [
    ("url_quadhd", "2160p"),
    ("url_ultrahd", "1440p"),
    ("url_fullhd", "1080p"),
    ("url_high", "720p"),
    ("url_medium", "480p"),
    ("url_mobile", "360p"),
    ("url_low", "240p"),
    ("url_tiny", "144p"),
]

_OKRU_APP_KEY = "CBAFJIICABABABABA"
_OKRU_FIELDS = ",".join(f"video.{k}" for k, _ in _OKRU_QUALITY_MAP) + ","

# مپ پارامتر type داخل لینک okcdn → کلید API
# (از پراب واقعی: type=0 tiny، 1 low، 2 medium، 3 high، 4 mobile)
_OKRU_TYPE_TO_KEY = {
    "0": "url_tiny",
    "1": "url_low",
    "2": "url_medium",
    "3": "url_high",
    "4": "url_mobile",
}

# پسوند fragment برای حمل آدرس صفحه در لینک کیفیت
_FRAGMENT_TAG = "pvo"


def is_pornvov_url(url: str) -> bool:
    """بررسی اینکه URL مربوط به pornvov.net هست (صفحه‌ی ویدیو یا embed)."""
    if not url:
        return False
    return bool(_URL_RE.match(url.strip()))


def _split_fragment(url: str) -> Tuple[str, Optional[str]]:
    """جدا کردن fragment حمل‌شده (#pvo=<page>) از لینک کیفیت."""
    m = re.search(r"#%s=([^#]+)$" % _FRAGMENT_TAG, url)
    if not m:
        return url, None
    return url[: m.start()], unquote(m.group(1))


# ─── RE: decrypt payload (معادل کلاس Encryption در kt_player_new.js) ──


def _decrypt_payload(b64_payload: str, key: str) -> str:
    """AES-256-CBC + PBKDF2-SHA512 — دقیقاً مثل Encryption.decrypt در JS.

    ساختار payload: {"ciphertext": base64, "iv": hex, "salt": hex,
    "iterations": int} → خروجی: session_key اوکی.رو.
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes

    data = json.loads(base64.b64decode(b64_payload))
    salt = bytes.fromhex(data["salt"])
    iv = bytes.fromhex(data["iv"])
    iterations = int(data.get("iterations") or 999)
    ct = base64.b64decode(data["ciphertext"].replace("\\/", "/"))

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA512(), length=32, salt=salt, iterations=iterations
    )
    dk = kdf.derive(key.encode())
    dec = Cipher(algorithms.AES(dk), modes.CBC(iv)).decryptor()
    pt = dec.update(ct) + dec.finalize()
    # PKCS7 unpad
    pad = pt[-1]
    if 0 < pad <= 16:
        pt = pt[:-pad]
    return pt.decode("utf-8", "replace")


# ─── OK.ru API chain (هسته‌ی ریورس) ─────────────────────────


def _fetch_okru_videos_sync(session_key: str, vids: str) -> dict:
    """صدا زدن API عمومی OK.ru (بدون impersonation — chrome جواب XML میده!)."""
    from curl_cffi import requests as _req

    api_url = (
        f"https://api.ok.ru/fb.do?application_key={_OKRU_APP_KEY}"
        f"&fields={_OKRU_FIELDS}&method=video.get"
        f"&session_key={session_key}&vids={vids}"
    )
    r = _req.get(api_url, timeout=25, headers={"User-Agent": _UA})
    r.raise_for_status()
    data = r.json()
    videos = (data.get("videos") or [{}])[0]
    if not videos:
        raise ValueError("OK.ru API: empty videos")
    return videos


async def _fetch_okru_videos(session_key: str, vids: str) -> dict:
    return await asyncio.to_thread(_fetch_okru_videos_sync, session_key, vids)


def _fetch_chain_sync(page_url: str) -> Tuple[str, dict]:
    """کل زنجیره‌ی RE به‌صورت sync: صفحه/embed → decrypt → OK.ru API.

    Returns: (title, videos_dict از API)
    """
    from curl_cffi import requests as _req

    s = _req.Session()
    if "/embed/" in page_url:
        emb_html = s.get(
            page_url, impersonate="chrome", timeout=25,
            headers={"User-Agent": _UA},
        ).text
        title = extract_title_from_html(emb_html, DISPLAY_NAME)
    else:
        page = s.get(
            page_url, impersonate="chrome", timeout=25,
            headers={"User-Agent": _UA},
        ).text
        title = extract_title_from_html(page, DISPLAY_NAME)
        m = _EMBED_ID_RE.search(page)
        if not m:
            raise ValueError("embed id not found on page")
        emb_html = s.get(
            f"{HOMEPAGE}embed/{m.group(1)}",
            impersonate="chrome", timeout=25,
            headers={"User-Agent": _UA, "Referer": page_url},
        ).text

    g = _GENERATE_MP4_RE.search(emb_html)
    if not g:
        raise ValueError("generate_mp4 payload not found on embed page")
    payload, key, vids, _vid_id = g.groups()

    session_key = _decrypt_payload(payload, key)
    logger.info("[PornVov] decrypted session_key for vids=%s", vids[:24])

    return title, _fetch_okru_videos_sync(session_key, vids)


async def _resolve_chain(page_url: str) -> Tuple[str, dict]:
    return await asyncio.to_thread(_fetch_chain_sync, page_url)


def _qualities_from_videos(videos: dict, page_url: Optional[str] = None) -> List[dict]:
    """ساخت لیست کیفیت از خروجی API؛ صفحه‌ی ویدیو توی fragment حمل میشه."""
    frag = ""
    if page_url:
        frag = f"#{_FRAGMENT_TAG}={page_url}"
    qualities = []
    for key_name, label in _OKRU_QUALITY_MAP:
        u = videos.get(key_name)
        if not u:
            continue
        qualities.append({
            "label": f"📡 {label}",
            "url": f"{u}{frag}",
            "method": "direct",
        })
    return qualities


async def extract_pornvov_qualities(url: str) -> Tuple[List[dict], str]:
    """استخراج کیفیت‌های ویدیو از pornvov.net (از طریق زنجیره‌ی OK.ru)."""
    if not is_pornvov_url(url):
        return [], "Invalid PornVov URL"

    logger.info("[PornVov] Extracting: %s", url[:80])
    try:
        title, videos = await _resolve_chain(url)
    except Exception as e:
        logger.warning("[PornVov] extract failed: %s", e)
        return [], f"{type(e).__name__}: {str(e)[:120]}"

    qualities = _qualities_from_videos(videos, page_url=url)
    if not qualities:
        return [], "OK.ru API returned no playable urls"

    logger.info("[PornVov] Extracted %d qualities | %s", len(qualities), title[:60])
    return qualities, title


# ─── Download ──────────────────────────────────────────────


async def _download_url(url: str, filepath: str, progress_cb: ProgressCallback):
    """دانلود یک لینک okcdn: multi-segment → direct ساده."""
    success, error, size = await _download_direct_multi_impl(
        url, filepath, progress_cb, referer=None, user_agent=_UA
    )
    if success:
        return True, "", size
    cleanup_file(filepath)
    success, error, size = await _download_direct_impl(
        url, filepath, progress_cb, referer=None, user_agent=_UA
    )
    if success:
        return True, "", size
    cleanup_file(filepath)
    return False, error or "download failed", 0


async def download_pornvov_direct(
    url: str, filepath: str, progress_cb: ProgressCallback
) -> Tuple[bool, str, int]:
    """دانلود MP4 از okcdn.ru.

    🔑 لینک‌های okcdn امضای srcIp + expires دارن؛ لینکی که موقع extract
    گرفته شده ممکنه منقضی/غیرمطابق IP فعلی باشه:
      ۱) اول لینک موجود → multi-segment
      ۲) اگه نشد، از fragment (#pvo=page) زنجیره از نو اجرا میشه و
         لینک تازه‌ی «همون کیفیت» (با type param) گرفته میشه
    """
    if not url:
        return False, "Empty URL", 0

    real_url, page_url = _split_fragment(url)

    # ۱) لینک فعلی
    if real_url.startswith("http"):
        success, error, size = await _download_url(real_url, filepath, progress_cb)
        if success:
            return True, "", size

    # ۲) زنجیره‌ی تازه (لینک منقضی/غیرمطابق IP)
    if not page_url:
        return False, (error or "no page url for re-resolve")[:150], 0

    try:
        _, videos = await _resolve_chain(page_url)
    except Exception as e:
        return False, f"re-resolve failed: {type(e).__name__}: {str(e)[:100]}", 0

    # انتخاب همون کیفیت: از پارامتر type لینک قبلی؛ fallback بهترین کیفیت
    type_m = re.search(r"[?&]type=(\d+)", real_url or "")
    fresh_url = None
    if type_m:
        key_name = _OKRU_TYPE_TO_KEY.get(type_m.group(1))
        if key_name and videos.get(key_name):
            fresh_url = videos[key_name]
    if not fresh_url:
        for key_name, _label in _OKRU_QUALITY_MAP:
            if videos.get(key_name):
                fresh_url = videos[key_name]
                break
    if not fresh_url:
        return False, "fresh resolve: no playable urls", 0

    logger.info("[PornVov] fresh re-resolved link (type=%s)", type_m.group(1) if type_m else "?")
    success, error, size = await _download_url(fresh_url, filepath, progress_cb)
    if success:
        return True, "", size
    return False, error or "download failed", 0


async def download_pornvov_m3u8(
    url: str, filepath: str, progress_cb: ProgressCallback
) -> Tuple[bool, str, int]:
    """pornvov فقط MP4 مستقیم میده؛ برای سازگاری اینترفیس."""
    success, error, size = await _download_m3u8_impl(
        url, filepath, progress_cb, referer=HOMEPAGE
    )
    if success:
        return True, "", size
    cleanup_file(filepath)
    return False, error, 0


# ─── Session store (برای _make_site_handler در bot.py) ─────

pornvov_sessions: dict = {}
