"""
itchio_handler.py
──────────────────
هندلر دانلود بازی‌ها و فایل‌ها از itch.io

ساپورت می‌شه:
  - بازی‌های رایگان (دانلود ناشناس)
  - بازی‌های name-your-price که فعلاً رایگان شدن (actual_price = 0)
  - بازی‌های چند-پلتفرمی (انتخاب فایل: Windows / Linux / macOS / Android)
  - کامیک‌ها و asset pack ها و هر فایل قابل دانلود دیگه
  - بازی‌های پولی/claim-required با کوکی اکانت itch.io (ITCHIO_COOKIE)
  - جریان claim خودکار برای بازی‌های رایگان نیازمند اکانت

روش کار (جریان ۴ مرحله‌ای با cookie مشترک itchio_token):
  1. GET صفحه‌ی بازی
       → استخراج csrf_token + اطلاعات بازی (عنوان/نویسنده/کاور/قیمت)
  2. POST <game_url>/download_url  (با csrf)
       → JSON {url: <download_page_url>}
       → خطای «you must buy this game to download» برای بازی‌های پولی
  3. GET صفحه‌ی دانلود
       → استخراج لیست upload ها (upload_id, نام فایل, حجم, پلتفرم)
       → تشخیص «Claim this game» (نیاز به اکانت)
  4. POST <game_url>/file/<upload_id>?source=game_download  (با csrf جدید)
       → JSON {url: <CDN امضاشده>} (فقط ۶۰ ثانیه اعتبار داره!)
  5. دانلود فایل از CDN (Cloudflare R2)
       → چون URL فقط ۶۰ ثانیه‌ایه، در صورت شکست URL تازه می‌گیریم و retry می‌کنیم

محدودیت‌ها:
  - بازی‌های پولی که اکانت مالکشون نیست: فقط پیام خطا با قیمت
  - بازی‌های browser-only: فایل قابل دانلود ندارن
  - لینک‌های CDN امضاشده ۶۰ ثانیه بعد از تولید منقضی میشن

احراز هویت (اختیاری):
  - متغیر محیطی ITCHIO_COOKIE یا تنظیم runtime با set_session_cookie()
  - فرمت: کل رشته‌ی کوکی مرورگر (مثل «itchio=abc; itchio_token=xyz; ...»)
    یا فقط مقدار کوکی itchio
  - با سشن معتبر: بازی‌های خریداری‌شده/claim‌شده قابل دانلود میشن و
    بازی‌های رایگان claim-required به‌صورت خودکار claim و دانلود میشن
"""

import asyncio
import logging
import os
import re
from typing import Awaitable, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from ._common import (
    ProgressCallback,
    cleanup_file,
    download_direct as _download_direct_impl,
    download_direct_multi as _download_direct_multi_impl,
    fetch_html,
)

logger = logging.getLogger("ItchioHandler")

# ─── Constants ─────────────────────────────────────────────

ITCHIO_SUFFIX = ".itch.io"

# نام کوکی سشن itch.io
SESSION_COOKIE_NAME = "itchio"

# کشِ upload_idهای کشف‌شده برای بازی‌های پولی.
# وقتی برای یه بازی پولی، upload_id رو با اسکنر brute-force پیدا کردیم،
# اینجا ذخیره می‌شه تا دفعه‌ی بعد بدون اسکن مجدد، مستقیم به /file/<id> بزنیم.
# ولی یادت باشه: /file/<id> هم هنوز چک مالکیت می‌کنه → برای بازی بدون سشن جواب نمی‌ده.
KNOWN_PAID_UPLOAD_IDS: Dict[str, str] = {
    # کلید: game_url نرمال‌شده، مقدار: upload_id
    "https://deaddove-studio.itch.io/the-backrooms-incident-1997": "18640198",
}

# حداکثر حجم دانلود (هماهنگ با بقیه‌ی هندلرها - 50 GB)
MAX_DOWNLOAD_SIZE = 50 * 1024 * 1024 * 1024

# حداکثر حجم قابل ارسال به تلگرام (سند - 2GB)
TELEGRAM_DOC_LIMIT = 1900 * 1024 * 1024

# تعداد تلاش برای گرفتن CDN URL تازه (هر بار URL جدید ۶۰ ثانیه اعتبار داره)
MAX_URL_ATTEMPTS = 3

# session state برای سازگاری با API بقیه‌ی هندلرها
itchio_sessions: dict = {}

# ─── Session cookie management (اختیاری) ──────────────────

# کوکی اکانت itch.io — با ITCHIO_COOKIE یا set_session_cookie() تنظیم میشه
_session_cookie_str: Optional[str] = None
# کش وضعیت لاگین (None = چک نشده)
_session_cache: Optional[dict] = None


def set_session_cookie(cookie_str: str) -> None:
    """
    تنظیم کوکی اکانت itch.io در زمان اجرا.

    فرمت قابل قبول:
      - کل رشته‌ی کوکی مرورگر: «itchio=abc; itchio_token=xyz; ...»
      - یا فقط مقدار کوکی itchio: «abc»
    """
    global _session_cookie_str, _session_cache
    _session_cookie_str = (cookie_str or "").strip() or None
    _session_cache = None  # باگ‌گیری مجدد در دفعه‌ی بعد


def clear_session_cookie() -> None:
    """حذف کوکی اکانت (بازگشت به حالت ناشناس)."""
    global _session_cookie_str, _session_cache
    _session_cookie_str = None
    _session_cache = None


def has_session_cookie() -> bool:
    """آیا کوکی اکانت تنظیم شده؟"""
    return bool(_session_cookie_str)


def _parse_cookie_string(cookie_str: str) -> Dict[str, str]:
    """
    پارس رشته‌ی کوکی به dict.

    ورودی می‌تونه:
      - «itchio=abc; itchio_token=xyz»  (فرمت هدر Cookie)
      - «abc»  (فقط مقدار — به‌عنوان کوکی itchio تفسیر میشه)
    """
    if not cookie_str:
        return {}
    cookie_str = cookie_str.strip()
    if "=" not in cookie_str:
        # فقط مقدار خام → کوکی سشن itch.io
        return {SESSION_COOKIE_NAME: cookie_str}
    cookies: Dict[str, str] = {}
    for part in cookie_str.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        name = name.strip()
        value = value.strip()
        if name and value:
            cookies[name] = value
    return cookies


def _get_request_cookies() -> Dict[str, str]:
    """کوکی‌های درخواست: کوکی سشن (اگه تنظیم شده) — بقیه خود itch.io می‌سازه."""
    if not _session_cookie_str:
        return {}
    return _parse_cookie_string(_session_cookie_str)


def _load_env_cookie() -> None:
    """خواندن ITCHIO_COOKIE از متغیرهای محیطی (یک بار در ابتدای import)."""
    global _session_cookie_str
    env_val = os.environ.get("ITCHIO_COOKIE", "").strip()
    if env_val and not _session_cookie_str:
        _session_cookie_str = env_val


_load_env_cookie()


async def verify_session(force: bool = False) -> Optional[dict]:
    """
    بررسی وضعیت کوکی اکانت.

    Returns:
        dict با کلیدهای {username, display_name, url} در صورت لاگین معتبر،
        None اگه لاگین نیست/کوکی نامعتبره،
        dict با کلید error در صورت خطای شبکه.
    """
    global _session_cache
    if not _session_cookie_str:
        return None
    if _session_cache is not None and not force:
        return _session_cache

    if not check_impersonation_support():
        return {"error": "curl_cffi not available"}

    from curl_cffi.requests import AsyncSession

    try:
        async with AsyncSession() as session:
            resp = await session.get(
                "https://itch.io/",
                impersonate="chrome",
                headers={"User-Agent": _UA(), "Accept-Language": "en-US,en;q=0.9"},
                cookies=_get_request_cookies(),
                timeout=30,
            )
            if resp.status_code != 200:
                return {"error": f"itch.io HTTP {resp.status_code}"}
            # I.current_user = null برای ناشناس؛ برای لاگین‌شده یه آبجکت JSON هست
            m = re.search(r"I\.current_user\s*=\s*(\{.*?\})\s*;", resp.text)
            if not m:
                _session_cache = None
                return None
            import json
            try:
                user = json.loads(m.group(1))
            except Exception:
                _session_cache = None
                return None
            if not user or not user.get("username"):
                _session_cache = None
                return None
            result = {
                "username": user.get("username", ""),
                "display_name": user.get("display_name") or user.get("username", ""),
                "url": user.get("url", ""),
            }
            _session_cache = result
            return result
    except Exception as e:
        logger.warning("[itch.io] verify_session error: %s", e)
        return {"error": str(e)[:150]}

# خطاهای شناخته‌شده
ERR_PAID = "paid"
ERR_CLAIM = "claim_required"
ERR_NO_DOWNLOADS = "no_downloads"
ERR_NOT_FOUND = "not_found"
ERR_NETWORK = "network"

# آیکون پلتفرم‌ها برای دکمه‌ها
PLATFORM_ICONS = {
    "windows": "🖥",
    "linux": "🐧",
    "macos": "🍎",
    "osx": "🍎",
    "mac": "🍎",
    "android": "📱",
    "ios": "📱",
}


# ─── URL detection ─────────────────────────────────────────


def is_itchio_url(url: str) -> bool:
    """
    بررسی می‌کنه که URL یه صفحه‌ی بازی itch.io باشه.
    فرمت‌های قابل قبول:
      - https://<creator>.itch.io/<game-slug>
      - https://<creator>.itch.io/<game-slug>/download/<key>  (لینک دانلود-key)
    (صفحه‌ی پروفایل سازنده و صفحات داخلی بازی مثل devlog ساپورت نمی‌شن)
    """
    if not url:
        return False
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if not host.endswith(ITCHIO_SUFFIX):
            return False
        # www.itch.io صفحه‌ی اصلی خود سايته، نه بازی
        if host == "www" + ITCHIO_SUFFIX or host == ITCHIO_SUFFIX:
            return False
        path = parsed.path.strip("/")
        if not path:
            return False  # صفحه‌ی پروفایل سازنده
        parts = [p for p in path.split("/") if p]
        # صفحه‌ی بازی: /<slug>
        if len(parts) == 1:
            return True
        # لینک دانلود-key: /<slug>/download/<key>
        if len(parts) == 3 and parts[1] == "download" and parts[2]:
            return True
        return False
    except Exception:
        return False


def extract_download_key(url: str) -> Optional[str]:
    """استخراج کلید دانلود از URL با فرمت /<slug>/download/<key>."""
    try:
        path = urlparse(url).path.strip("/")
        parts = [p for p in path.split("/") if p]
        if len(parts) == 3 and parts[1] == "download":
            return parts[2]
    except Exception:
        pass
    return None


def _game_slug_from_url(url: str) -> Optional[str]:
    """استخراج slug بازی از URL."""
    try:
        path = urlparse(url).path.strip("/")
        return path.split("/")[0] if path else None
    except Exception:
        return None


def _game_url_from_key(url: str) -> str:
    """تبدیل URL کلید (/slug/download/KEY) به URL صفحه‌ی بازی (/slug)."""
    try:
        key = extract_download_key(url)
        if key:
            parsed = urlparse(url)
            path = parsed.path.strip("/")
            parts = [p for p in path.split("/") if p]
            return f"https://{parsed.hostname}/{parts[0]}"
    except Exception:
        pass
    return url


def normalize_game_url(url: str) -> str:
    """
    نرمال‌سازی URL بازی:
      - حذف query string و fragment (مثل ?ref=test)
      - حذف / انتهایی
      - اطمینان از https
    (برای ساخت URL های API مثل /download_url و /file/<id> ضروریه)
    """
    try:
        parsed = urlparse(url)
        scheme = "https"
        host = parsed.hostname or ""
        path = parsed.path.strip("/")
        if not host or not path:
            return url
        return f"{scheme}://{host}/{path}"
    except Exception:
        return url


# ─── Helpers ───────────────────────────────────────────────


def _parse_size_to_bytes(size_str: str) -> int:
    """تبدیل «423 MB» / «1.2 GB» به بایت."""
    try:
        m = re.search(r"([\d.]+)\s*(KB|MB|GB|TB|B)?", size_str or "", re.I)
        if not m:
            return 0
        num = float(m.group(1))
        unit = (m.group(2) or "B").upper()
        mult = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
        return int(num * mult.get(unit, 1))
    except Exception:
        return 0


def _format_price(cents: int) -> str:
    """تبدیل سنت به دلار."""
    if cents is None:
        return ""
    return f"${cents / 100:.2f}"


def _platform_icon(platforms: List[str]) -> str:
    """آیکون مناسب برای پلتفرم فایل."""
    for p in platforms:
        icon = PLATFORM_ICONS.get(p.lower())
        if icon:
            return icon
    return "📦"


# ─── Game info extraction ──────────────────────────────────


async def extract_game_info(url: str) -> Optional[dict]:
    """
    استخراج اطلاعات کامل بازی + لیست فایل‌های قابل دانلود.

    Returns:
        dict با کلیدهای:
          title, author, cover, description,
          min_price, actual_price (سنت),
          uploads: [{id, name, size_str, size_bytes, platforms}],
          url
        یا dict با کلید error در صورت خطا:
          error: یکی از ERR_PAID / ERR_CLAIM / ERR_NO_DOWNLOADS / ERR_NOT_FOUND / ERR_NETWORK
          error_detail: متن دقیق خطا
    """
    if not check_impersonation_support():
        return {"error": ERR_NETWORK, "error_detail": "curl_cffi not available"}

    from curl_cffi.requests import AsyncSession

    url = normalize_game_url(url)
    try:
        async with AsyncSession() as session:
            return await _extract_game_info_with_session(session, url)
    except Exception as e:
        logger.error("[itch.io] extract_game_info error: %s", e, exc_info=True)
        return {"error": ERR_NETWORK, "error_detail": str(e)[:200]}


def check_impersonation_support() -> bool:
    """بررسی دسترسی بودن curl_cffi."""
    try:
        import curl_cffi  # noqa: F401
        return True
    except ImportError:
        return False


async def _extract_game_info_with_session(session, url: str, allow_claim: bool = True) -> dict:
    """مرحله‌های ۱ تا ۳ جریان دانلود با session مشترک (برای cookies)."""

    cookies = _get_request_cookies()

    # ─── Step 1: صفحه‌ی بازی ───
    resp = await session.get(
        url, impersonate="chrome",
        headers={"User-Agent": _UA(), "Accept-Language": "en-US,en;q=0.9"},
        cookies=cookies,
        timeout=30,
    )
    if resp.status_code == 404:
        return {"error": ERR_NOT_FOUND, "error_detail": "Game page not found (404)"}
    if resp.status_code != 200:
        return {"error": ERR_NETWORK, "error_detail": f"HTTP {resp.status_code}"}

    html = resp.text
    csrf_m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    if not csrf_m:
        # صفحه‌ی بازی نیست (مثلاً پروفایل سازنده)
        return {"error": ERR_NOT_FOUND, "error_detail": "Not a game page (no CSRF token)"}
    csrf = csrf_m.group(1)

    # اطلاعات بازی از متا تگ‌ها
    title = "Untitled"
    m = re.search(r"<title>([^<]+?)\s+by\s+[^<]+</title>", html)
    if m:
        title = _html_unescape(m.group(1).strip())
    author = ""
    m = re.search(r"<title>[^<]+?\s+by\s+([^<]+)</title>", html)
    if m:
        author = _html_unescape(m.group(1).strip())

    cover = ""
    m = re.search(r'<meta property="og:image" content="([^"]+)"', html)
    if m:
        cover = m.group(1)

    description = ""
    m = re.search(r'<meta property="og:description" content="([^"]*)"', html)
    if m:
        description = _html_unescape(m.group(1))[:300]

    # قیمت از init_ViewGame JSON
    min_price = 0
    actual_price = 0
    m = re.search(r"init_ViewGame\([^,]+,\s*(\{.*?\})\);", html, re.S)
    if m:
        try:
            import json
            data = json.loads(m.group(1))
            game = data.get("game", {})
            min_price = int(game.get("min_price") or 0)
            actual_price = int(game.get("actual_price") or 0)
        except Exception:
            pass

    # ─── Step 2: لینک دانلود-key؟ مستقیم صفحه‌ی دانلودش رو باز کن ───
    key = extract_download_key(url)
    if key:
        # صفحه‌ی کلید خودش صفحه‌ی دانلود هست
        # (کلید رو هم برای استخراج‌های بعدی نگه می‌داریم)
        resp3 = await session.get(
            url, impersonate="chrome",
            headers={"User-Agent": _UA(), "Referer": _game_url_from_key(url)},
            cookies=cookies,
            timeout=30,
            allow_redirects=False,
        )
        # کلید نامعتبر → 302 برمی‌گرده به صفحه‌ی بازی
        if resp3.status_code in (301, 302, 303, 307):
            return {
                "error": ERR_NOT_FOUND,
                "error_detail": "Invalid or expired download key",
                "title": title,
                "url": url,
            }
        if resp3.status_code != 200:
            return {"error": ERR_NETWORK, "error_detail": f"Key page: HTTP {resp3.status_code}", "title": title, "url": url}

        dhtml = resp3.text
        # کلید معتبر → صفحه‌ی دانلود با uploads
        uploads = _parse_uploads(dhtml)
        if not uploads:
            # شاید صفحه‌ی purchase باشه (بازی برای این کلید خریده نشده)
            if "purchase" in dhtml.lower()[:3000]:
                return {
                    "error": ERR_PAID,
                    "error_detail": "Key does not grant access to this game",
                    "title": title,
                    "author": author,
                    "url": url,
                }
            return {
                "error": ERR_NO_DOWNLOADS,
                "error_detail": "No downloadable files on key page",
                "title": title,
                "author": author,
                "cover": cover,
                "url": url,
            }

        # کلید رو به uploads اضافه کن تا موقع دانلود استفاده بشه
        for u in uploads:
            u["key"] = key

        return {
            "title": title,
            "author": author,
            "cover": cover,
            "description": description,
            "min_price": min_price,
            "actual_price": actual_price,
            "uploads": uploads,
            "url": _game_url_from_key(url),
            "key": key,
        }

    # ─── Step 2 (عادی): POST /download_url ───
    resp2 = await session.post(
        url + "/download_url",
        impersonate="chrome",
        headers={
            "User-Agent": _UA(),
            "Referer": url,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        },
        data={"csrf_token": csrf},
        cookies=cookies,
        timeout=30,
    )
    try:
        dl_data = resp2.json()
    except Exception:
        return {"error": ERR_NETWORK, "error_detail": f"/download_url: HTTP {resp2.status_code} (non-JSON)"}

    if dl_data.get("errors"):
        err_msg = "; ".join(dl_data["errors"])[:200]
        if "buy this game" in err_msg:
            # اگه برای این بازی upload_id از قبل کشف شده، در error_detail بیار
            known_uid = KNOWN_PAID_UPLOAD_IDS.get(normalize_game_url(url))
            extra_hint = ""
            if known_uid:
                extra_hint = (
                    f" (upload_id={known_uid} already discovered, but itch.io "
                    f"server-side still requires a valid download key — provide "
                    f"your itch.io session via /itchio set if you own this game)"
                )
            else:
                # اگه سشن لاگین داریم، راهنمایی بکن
                if has_session_cookie():
                    extra_hint = " (you have a session set but it does not own this game)"
                else:
                    extra_hint = (
                        " (this is a paid game — provide your itch.io session "
                        "cookie via /itchio set if you own this game)"
                    )
            return {
                "error": ERR_PAID,
                "error_detail": err_msg + extra_hint,
                "title": title,
                "author": author,
                "cover": cover,
                "description": description,
                "min_price": min_price,
                "actual_price": actual_price,
                "url": url,
            }
        return {
            "error": ERR_NETWORK,
            "error_detail": err_msg,
            "title": title,
            "author": author,
            "cover": cover,
            "url": url,
        }

    dl_page_url = dl_data.get("url")
    if not dl_page_url:
        return {"error": ERR_NO_DOWNLOADS, "error_detail": "No download URL returned", "title": title, "author": author, "url": url}

    # ─── Step 3: صفحه‌ی دانلود ───
    resp3 = await session.get(
        dl_page_url,
        impersonate="chrome",
        headers={"User-Agent": _UA(), "Referer": url},
        cookies=cookies,
        timeout=30,
    )
    if resp3.status_code != 200:
        return {"error": ERR_NETWORK, "error_detail": f"Download page: HTTP {resp3.status_code}", "title": title, "url": url}

    dhtml = resp3.text

    # بازی claim-required؟
    is_claim_page = (
        "Claim this game" in dhtml
        or "Log in or create" in dhtml
        or ("/claim-key" in dhtml)
    )
    if is_claim_page:
        # اگه سشن لاگین داریم → خودکار claim کن و دوباره امتحان کن
        if has_session_cookie() and allow_claim:
            claimed = await _try_claim(session, dhtml, dl_page_url, cookies)
            if claimed:
                # claim موفق بود → دوباره کل جریان رو اجرا کن
                return await _extract_game_info_with_session(
                    session, url, allow_claim=False
                )
        return {
            "error": ERR_CLAIM,
            "error_detail": "This game must be claimed with an itch.io account",
            "title": title,
            "author": author,
            "cover": cover,
            "description": description,
            "url": url,
        }

    # استخراج upload ها
    uploads = _parse_uploads(dhtml)
    if not uploads:
        return {
            "error": ERR_NO_DOWNLOADS,
            "error_detail": "No downloadable files (browser-only game?)",
            "title": title,
            "author": author,
            "cover": cover,
            "url": url,
        }

    return {
        "title": title,
        "author": author,
        "cover": cover,
        "description": description,
        "min_price": min_price,
        "actual_price": actual_price,
        "uploads": uploads,
        "url": url,
    }


async def _try_claim(session, dhtml: str, dl_page_url: str, cookies: dict) -> bool:
    """
    تلاش برای claim کردن بازی رایگان با سشن لاگین‌شده.

    فرم claim در صفحه‌ی دانلود:
      <form action="<game>/claim-key?sig=..." method="post">
        <input type="hidden" name="csrf_token" value="..."/>
      </form>

    Returns:
        True اگه claim موفق بود (یا قبلاً claim شده بود).
    """
    try:
        m = re.search(
            r'<form[^>]*action="([^"]*claim-key[^"]*)"[^>]*>.*?'
            r'name="csrf_token" value="([^"]+)"',
            dhtml, re.S,
        )
        if not m:
            logger.warning("[itch.io] claim form not found on claim page")
            return False
        action, claim_csrf = m.group(1), m.group(2)

        resp = await session.post(
            action,
            impersonate="chrome",
            headers={
                "User-Agent": _UA(),
                "Referer": dl_page_url,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            data={"csrf_token": claim_csrf},
            cookies=cookies,
            timeout=30,
            allow_redirects=True,
        )
        # claim موفق معمولاً به صفحه‌ی دانلود redirect میشه (200 بعد از follow)
        # claim ناموفق به login/register میره (و respawn میشه)
        final_url = str(resp.url) if hasattr(resp, "url") else ""
        if resp.status_code == 200 and "register" not in final_url and "login" not in final_url:
            logger.info("[itch.io] game claimed successfully")
            return True
        logger.warning(
            "[itch.io] claim failed: status=%s final_url=%s",
            resp.status_code, final_url[:100],
        )
        return False
    except Exception as e:
        logger.warning("[itch.io] claim error: %s", e)
        return False


def _parse_uploads(dhtml: str) -> List[dict]:
    """
    پارس لیست فایل‌ها از HTML صفحه‌ی دانلود.

    ساختار هر upload:
      <a class="button download_btn" ... data-upload_id="16571935">Download</a>
      ... <strong class="name" title="game.zip">game.zip</strong>
      ... <span class="file_size"><span>423 MB</span></span>
      ... <span class="download_platforms">... title="Download for Windows" ...</span>
    """
    uploads = []
    # برای هر دکمه‌ی دانلود، بخش بعدی رو پارس می‌کنیم —
    # ولی فقط تا شروع upload بعدی (جلوگیری از چسبیدن پلتفرم‌های فایل بعدی)
    for m in re.finditer(r'data-upload_id="(\d+)"', dhtml):
        upload_id = m.group(1)
        tail = dhtml[m.end(): m.end() + 3000]
        # برش در مرز upload بعدی
        boundary = re.search(r'data-upload_id="', tail)
        if boundary:
            tail = tail[: boundary.start()]

        name = ""
        # strong با class="name" — ترتیب attribute ها رندومه!
        # (گاهی: <strong class="name" title="..."> گاهی: <strong title="..." class="name">)
        for sm in re.finditer(r"<strong([^>]*)>([^<]*)</strong>", tail):
            attrs, inner = sm.group(1), sm.group(2)
            if 'class="name"' not in attrs and "class='name'" not in attrs:
                continue
            tm = re.search(r'title="([^"]*)"', attrs)
            if tm:
                name = _html_unescape(tm.group(1))
            else:
                name = _html_unescape(inner.strip())
            break

        size_str = ""
        sm = re.search(
            r'<span[^>]*class="file_size"[^>]*>\s*<span[^>]*>([^<]+)</span>', tail
        )
        if sm:
            size_str = sm.group(1).strip()

        platforms = []
        for pm in re.finditer(r'title="Download for ([^"]+)"', tail):
            p = pm.group(1).strip()
            if p not in platforms:
                platforms.append(p)

        uploads.append({
            "id": upload_id,
            "name": name or f"file_{upload_id}",
            "size_str": size_str,
            "size_bytes": _parse_size_to_bytes(size_str),
            "platforms": platforms,
        })

    # dedupe بر اساس id (حفظ ترتیب)
    seen = set()
    unique = []
    for u in uploads:
        if u["id"] not in seen:
            seen.add(u["id"])
            unique.append(u)
    return unique


def _html_unescape(s: str) -> str:
    import html as html_lib
    return html_lib.unescape(s)


def _UA() -> str:
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )


# ─── CDN URL acquisition ───────────────────────────────────


async def _get_fresh_cdn_url(session, game_url: str, upload_id: str, key: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """
    گرفتن URL تازه‌ی CDN برای یه upload مشخص.
    (هر بار که صدا زده بشه URL جدید با ۶۰ ثانیه اعتبار می‌ده)

    Returns:
        (cdn_url, error) — یکی از دو تا None هست.
    """
    game_url = normalize_game_url(game_url)
    cookies = _get_request_cookies()

    if key:
        # ─── جریان کلید: صفحه‌ی /download/<key> خودش صفحه‌ی دانلوده ───
        key_url = f"{game_url}/download/{key}"
        resp = await session.get(
            key_url, impersonate="chrome",
            headers={"User-Agent": _UA(), "Referer": game_url},
            cookies=cookies,
            timeout=30,
            allow_redirects=False,
        )
        if resp.status_code in (301, 302, 303, 307):
            return None, "Invalid or expired download key"
        if resp.status_code != 200:
            return None, f"Key page HTTP {resp.status_code}"
        csrf3_m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', resp.text)
        if not csrf3_m:
            return None, "No CSRF token on key page"
        csrf3 = csrf3_m.group(1)
        dl_page_url = key_url
    else:
        # ─── جریان عادی: game page → download_url → download page ───
        # مرحله ۱: csrf از صفحه‌ی بازی
        resp = await session.get(
            game_url, impersonate="chrome",
            headers={"User-Agent": _UA(), "Accept-Language": "en-US,en;q=0.9"},
            cookies=cookies,
            timeout=30,
        )
        if resp.status_code != 200:
            return None, f"Game page HTTP {resp.status_code}"
        csrf_m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', resp.text)
        if not csrf_m:
            return None, "No CSRF token on game page"
        csrf = csrf_m.group(1)

        # مرحله ۲: download_url
        resp2 = await session.post(
            game_url + "/download_url",
            impersonate="chrome",
            headers={
                "User-Agent": _UA(),
                "Referer": game_url,
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/javascript, */*; q=0.01",
            },
            data={"csrf_token": csrf},
            cookies=cookies,
            timeout=30,
        )
        try:
            dl_data = resp2.json()
        except Exception:
            return None, f"/download_url HTTP {resp2.status_code} (non-JSON)"
        if dl_data.get("errors"):
            return None, "; ".join(dl_data["errors"])[:150]
        dl_page_url = dl_data.get("url")
        if not dl_page_url:
            return None, "No download page URL"

        # مرحله ۳: صفحه‌ی دانلود → csrf تازه
        resp3 = await session.get(
            dl_page_url,
            impersonate="chrome",
            headers={"User-Agent": _UA(), "Referer": game_url},
            cookies=cookies,
            timeout=30,
        )
        if resp3.status_code != 200:
            return None, f"Download page HTTP {resp3.status_code}"
        csrf3_m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', resp3.text)
        if not csrf3_m:
            return None, "No CSRF token on download page"
        csrf3 = csrf3_m.group(1)

    # مرحله ۴: POST /file/<upload_id> → CDN URL
    # (اگه کلید دانلود هست، به‌عنوان پارامتر هم فرستاده میشه)
    file_qs = "source=game_download"
    if key:
        file_qs += f"&key={key}"
    resp4 = await session.post(
        f"{game_url}/file/{upload_id}?{file_qs}",
        impersonate="chrome",
        headers={
            "User-Agent": _UA(),
            "Referer": dl_page_url,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        },
        data={"csrf_token": csrf3},
        cookies=cookies,
        timeout=30,
    )
    try:
        file_data = resp4.json()
    except Exception:
        return None, f"/file/ HTTP {resp4.status_code} (non-JSON)"
    if file_data.get("errors"):
        return None, "; ".join(file_data["errors"])[:150]

    cdn_url = file_data.get("url")
    if not cdn_url:
        return None, "No CDN URL in response"
    return cdn_url, None


# ─── Download ──────────────────────────────────────────────


async def download_itchio_file(
    game_url: str,
    upload_id: str,
    filepath: str,
    progress_cb: ProgressCallback,
    key: Optional[str] = None,
) -> Tuple[bool, str, int]:
    """
    دانلود یه فایل از itch.io.

    چون URL های CDN فقط ۶۰ ثانیه اعتبار دارن، استراتژی retry:
      تلاش ۱: multi-segment (16x سریع) با URL تازه
      تلاش ۲: single-stream با URL تازه (برای فایل‌های بزرگ که URL منقضی میشه)
      تلاش ۳: single-stream با URL تازه (بعد از ۳ ثانیه مکث)

    Returns:
        Tuple (success, error_message, file_size)
    """
    if not check_impersonation_support():
        return False, "curl_cffi not available", 0

    from curl_cffi.requests import AsyncSession

    game_url = normalize_game_url(game_url)
    # اگه URL کلید بود، کلید رو جدا کن و URL پایه رو نگه دار
    dl_key = key or extract_download_key(game_url)
    if dl_key and not key:
        game_url = _game_url_from_key(game_url)

    # اطمینان از وجود پوشه‌ی خروجی
    parent_dir = os.path.dirname(filepath)
    if parent_dir and not os.path.exists(parent_dir):
        try:
            os.makedirs(parent_dir, exist_ok=True)
        except Exception as e:
            return False, f"Cannot create output directory: {e}", 0

    last_error = "unknown error"

    for attempt in range(1, MAX_URL_ATTEMPTS + 1):
        try:
            async with AsyncSession() as session:
                # URL تازه بگیر (هر تلاش یه URL جدید)
                cdn_url, err = await _get_fresh_cdn_url(session, game_url, upload_id, key=dl_key)
                if not cdn_url:
                    last_error = err or "Failed to get CDN URL"
                    logger.warning("[itch.io] attempt %d: no CDN URL: %s", attempt, last_error)
                    # اگه خطا مربوط به خرید/claim باشه، retry بی‌فایده‌ست
                    if "buy this game" in last_error or "claim" in last_error.lower():
                        return False, last_error, 0
                    continue

                await progress_cb(f"📥 **شروع دانلود از itch.io (تلاش {attempt}/{MAX_URL_ATTEMPTS})...**")

                if attempt == 1:
                    # تلاش ۱: multi-segment سریع (16 worker موازی)
                    success, error, size = await _download_direct_multi_impl(
                        cdn_url, filepath, progress_cb,
                        referer=None,
                        max_filesize=MAX_DOWNLOAD_SIZE,
                    )
                else:
                    # تلاش‌های بعدی: single-stream (اتصال یه بار باز می‌مونه و
                    # منقضی شدن URL وسط دانلود اثری روی stream باز نداره)
                    success, error, size = await _download_direct_impl(
                        cdn_url, filepath, progress_cb,
                        referer=None,
                        max_filesize=MAX_DOWNLOAD_SIZE,
                    )

                if success:
                    return True, "", size

                last_error = error or "download failed"
                logger.warning("[itch.io] attempt %d failed: %s", attempt, last_error)
                cleanup_file(filepath)

        except asyncio.CancelledError:
            cleanup_file(filepath)
            raise
        except Exception as e:
            last_error = str(e)[:150]
            logger.warning("[itch.io] attempt %d exception: %s", attempt, last_error)
            cleanup_file(filepath)

        # قبل از تلاش بعدی کمی صبر کن
        if attempt < MAX_URL_ATTEMPTS:
            await asyncio.sleep(3)

    return False, last_error, 0


# ─── Filename helper ───────────────────────────────────────


async def check_price_status(url: str) -> Optional[dict]:
    """
    بررسی سبک‌وزن وضعیت قیمت یه بازی (بدون جریان دانلود کامل).

    برای Price Watcher — هر بار فقط یه GET می‌زنه و قیمت فعلی رو می‌خونه.

    Returns:
        dict: {min_price, actual_price, title, is_free} یا None در صورت خطا
        is_free=True یعنی بازی الان رایگانه (actual_price=0) — قابل دانلوده!
    """
    if not check_impersonation_support():
        return None

    from curl_cffi.requests import AsyncSession

    url = normalize_game_url(url)
    try:
        async with AsyncSession() as session:
            resp = await session.get(
                url, impersonate="chrome",
                headers={"User-Agent": _UA(), "Accept-Language": "en-US,en;q=0.9"},
                cookies=_get_request_cookies(),
                timeout=20,
            )
            if resp.status_code != 200:
                return None
            html = resp.text
            m = re.search(r"init_ViewGame\([^,]+,\s*(\{.*?\})\);", html, re.S)
            if not m:
                return None
            import json
            data = json.loads(m.group(1))
            game = data.get("game", {})
            min_price = int(game.get("min_price") or 0)
            actual_price = int(game.get("actual_price") or 0)
            # عنوان
            title = "Untitled"
            tm = re.search(r"<title>([^<]+?)\s+by\s+[^<]+</title>", html)
            if tm:
                title = _html_unescape(tm.group(1).strip())
            return {
                "min_price": min_price,
                "actual_price": actual_price,
                "title": title,
                "is_free": actual_price == 0,
            }
    except Exception as e:
        logger.debug("[itch.io] check_price_status error for %s: %s", url, e)
        return None


async def get_creator_free_games(game_url: str, exclude_slug: str = "") -> List[dict]:
    """
    گرفتن لیست بازی‌های رایگان سازنده‌ی یه بازی.

    برای وقتی که خود بازی پولیه — بازی‌های رایگان همون سازنده رو پیدا می‌کنه
    تا به کاربر پیشنهاد بدیم.

    Returns:
        لیست dict ها: [{title, url}] (حداکثر ۸ مورد)
        لیست خالی در صورت خطا یا نبود بازی رایگان.
    """
    if not check_impersonation_support():
        return []

    from curl_cffi.requests import AsyncSession

    try:
        # صفحه‌ی پروفایل سازنده: https://<creator>.itch.io/
        parsed = urlparse(normalize_game_url(game_url))
        host = parsed.hostname or ""
        if not host.endswith(ITCHIO_SUFFIX):
            return []
        creator_url = f"https://{host}/"

        async with AsyncSession() as session:
            resp = await session.get(
                creator_url, impersonate="chrome",
                headers={"User-Agent": _UA(), "Accept-Language": "en-US,en;q=0.9"},
                cookies=_get_request_cookies(),
                timeout=25,
            )
            if resp.status_code != 200:
                return []

            html = resp.text
            # لینک بازی‌های سازنده + عنوان‌ها
            games: List[dict] = []
            seen = set()
            # ساختار: <a class="game_cell" href="..."> ... <a class="title game_link" href="...">Title</a>
            for m in re.finditer(
                r'href="((https?://[^"]+\.itch\.io/[a-z0-9-]+))"[^>]*class="title game_link"[^>]*>([^<]+)</a>',
                html,
            ):
                url, title = m.group(1), _html_unescape(m.group(3).strip())
                slug = url.rstrip("/").split("/")[-1]
                if slug in seen or slug == exclude_slug:
                    continue
                seen.add(slug)
                games.append({"title": title, "url": url, "slug": slug})

            if not games:
                # fallback: هر لینک بازی داخل صفحه (ترتیب attribute ممکنه فرق کنه)
                for m in re.finditer(
                    r'class="title game_link"[^>]*href="((https?://[^"]+\.itch\.io/[a-z0-9-]+))"[^>]*>([^<]+)</a>',
                    html,
                ):
                    url, title = m.group(1), _html_unescape(m.group(3).strip())
                    slug = url.rstrip("/").split("/")[-1]
                    if slug in seen or slug == exclude_slug:
                        continue
                    seen.add(slug)
                    games.append({"title": title, "url": url, "slug": slug})

            # چک قیمت هر بازی (فقط رایگان‌ها رو نگه دار)
            free_games = []
            for g in games[:12]:
                try:
                    resp2 = await session.get(
                        g["url"], impersonate="chrome",
                        headers={"User-Agent": _UA()},
                        cookies=_get_request_cookies(),
                        timeout=15,
                    )
                    if resp2.status_code != 200:
                        continue
                    m = re.search(r"init_ViewGame\([^,]+,\s*(\{.*?\})\);", resp2.text, re.S)
                    if not m:
                        continue
                    import json
                    data = json.loads(m.group(1))
                    game = data.get("game", {})
                    # رایگان = قیمت فعلی صفر
                    if int(game.get("actual_price") or 0) == 0 and int(game.get("min_price") or 0) == 0:
                        free_games.append({"title": g["title"], "url": g["url"]})
                        if len(free_games) >= 8:
                            break
                except Exception:
                    continue
            return free_games
    except Exception as e:
        logger.warning("[itch.io] get_creator_free_games error: %s", e)
        return []


def suggest_filename(upload: dict, game_title: str = "") -> str:
    """
    ساخت نام فایل امن برای ذخیره.

    اولویت: نام اصلی فایل upload → عنوان بازی + id
    """
    name = (upload or {}).get("name") or ""
    name = re.sub(r'[<>:"/\\|?*]', "_", name).strip()
    if name:
        # طول رو محدود کن ولی پسوند رو نگه دار
        if len(name) > 80:
            base, ext = os.path.splitext(name)
            name = base[:75] + ext
        return name
    fallback = re.sub(r'[<>:"/\\|?*]', "_", game_title or "itchio_game")[:60].strip() or "itchio_game"
    return f"{fallback}_{upload.get('id', 'file')}.zip"
