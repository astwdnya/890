# -*- coding: utf-8 -*-
"""
🆕 z21 — FJ (tdmmo.xyz / فیلمجو) — منبع ایرانی با اکانت
═══════════════════════════════════════════════════════════
ریورس کامل (زنده، تست‌شده):

■ لاگین (اجباری — بدون کوکی همه‌چیز 302 → /login):
    GET  /login        → PHPSESSID
    GET  /captcha      → PNG جمع/تفریق دو عدد (فونت ثابت 100×22)
    POST /login        → mobile=... & password=... & captcha=جواب & submit=ورود
    کپچا با تمپلیت‌مچ محلی حل می‌شه (فونت دیترمینیستیک — 35/35 تست زنده، بدون OCR خارجی)
    سشن: کوکی PHPSESSID — با هر درخواست عضو 302→login شد → لاگین مجدد خودکار

■ سرچ:  GET /search?q=QUERY  → <div class="movie_item"> ... movie?m=ID
    سال برای محتوای ایرانی «شمسی» است (قورباغه=1399) → مچ با سال IMDb با تبدیل J+621/J+622

■ صفحه: GET /movie?m=ID
    لیست دانلود داخل <div id="myList2"> → <a href=".../play?a=d&i=TOKEN&f=FILE.mkv">
    هر لینک: سایز («423 مگابایت») + لیبل («قسمت 1 - کیفیت : 720» / «کیفیت 1080»)
    ⚠ لینک‌های a=d (دانلود) اشتراک فعال می‌خوان («اشتراک شما به پایان رسیده است»)
    ✅ لینک‌های a=p (پخش) بدون اشتراک کار می‌کنن → بات همیشه از a=p استفاده می‌کنه

■ رزولوشن لینک واقعی:
    GET /play?a=p&i=TOKEN&f=FILE  →  302 Location: vlc://HOST/path/FILE?expire=..&hash=..
    vlc:// → http://  →  لینک مستقیم CDN (dt9.tdmm2.ir / dl5.tdmm2.ir / ...)
    بدون کوکی کار می‌کنه، Range پشتیبانی می‌شه (206)، اعتبار ~۲ ساعت، mkv/mp4 واقعی
    مسیر شامل «faslN/» = فصل واقعی (حقیقت فصل برای فایل‌های بدون مارکر فصل در اسم)

■ ساختار چند-فصلی: همه‌ی فصل‌ها توی یک صفحه‌اند؛ فصل/قسمت از اسم فایل:
    Paytakht.S07E01.480p.mkv / Paytakht.S2.E01.mp4 / Paytakht_s3_01.mp4 / Paytakht.7.E02.480p.mp4
    بدون مارکر → Ghoorbaghe_E01_720p.mkv (قسمت از اسم، فصل از مسیر faslN بعد از resolve)
    لیبل HTML («قسمت N») به‌عنوان fallback قسمت

■ env: TDMMO_USER / TDMMO_PASS (اگه ست نباشه → مقادیر فعلی حساب کاربر)

خروجی probe_tdmmo = entry هم‌شکل سرورهای CDN با server="FJ" و iran=True
→ توی منوی کیفیت/سرور با برچسب 🇮🇷 FJ می‌شینه (bot.py خودش پرچم رو اضافه می‌کنه)
"""

import asyncio
import io
import json
import logging
import os
import re
import time
import urllib.parse
from typing import Dict, List, Optional, Tuple

from curl_cffi.requests import AsyncSession

logger = logging.getLogger("Tdmmo")

_BASE = "https://tdmmo.xyz"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# ═══════════════════════════════════════════════════════════
#   کپچا — تمپلیت‌مچ (فونت ثابت؛ استخراج از ۲۵ نمونه‌ی زنده، 35/35 صحت)
# ═══════════════════════════════════════════════════════════

# هر گلیف: ماتریس باینری برش‌خورده (bbox). '1'=جوهر، '0'=زمینه
_CAP_TPL_RAW = {
    "0": ["00011000", "00111100", "01100110", "11000011", "11000011", "11000011",
          "11000011", "01100110", "00111100", "00011000"],
    "1": ["001100", "011100", "111100", "001100", "001100", "001100", "001100",
          "001100", "001100", "111111"],
    "2": ["00111100", "01100110", "11000011", "00000011", "00000110", "00001100",
          "00011000", "00110000", "01100000", "11111111"],
    "3": ["01111100", "11000110", "00000011", "00000110", "00011100", "00000110",
          "00000011", "00000011", "11000110", "01111100"],
    "4": ["00000110", "00001110", "00011110", "00110110", "01100110", "11000110",
          "11111111", "00000110", "00000110", "00000110"],
    "5": ["11111110", "11000000", "11000000", "11011100", "11100110", "00000011",
          "00000011", "11000011", "01100110", "00111100"],
    "6": ["00111100", "01100110", "11000010", "11000000", "11011100", "11100110",
          "11000011", "11000011", "01100110", "00111100"],
    "7": ["11111111", "00000011", "00000011", "00000110", "00001100", "00011000",
          "00110000", "01100000", "11000000", "11000000"],
    "8": ["00111100", "01100110", "11000011", "01100110", "00111100", "01100110",
          "11000011", "11000011", "01100110", "00111100"],
    "9": ["00111100", "01100110", "11000011", "11000011", "01100111", "00111011",
          "00000011", "01000011", "01100110", "00111100"],
    "+": ["00011000", "00011000", "00011000", "11111111", "00011000", "00011000",
          "00011000"],
    # تفریق (سایت طبق گفته‌ی کاربر ممکنه تفریق هم بده) — میله‌ی افقی (فرم کراپ‌شده‌ی bbox —
    # چون سولور گلیف رو به bbox جوهر برش می‌ده، تمپلیت هم باید کراپ‌شده باشه)
    "-": ["01111110", "01111110"],
}
def _crop_cols(rows: List[Tuple[int, ...]]) -> List[Tuple[int, ...]]:
    """برش ستون‌های بدون جوهر (تمپلیت‌ها هم‌شکل گلیفِ کراپ‌شده می‌شن)."""
    if not rows:
        return rows
    w = len(rows[0])
    cols = [x for x in range(w) if any(r[x] for r in rows)]
    if not cols:
        return rows
    return [tuple(r[cols[0]:cols[-1] + 1]) for r in rows]


_CAP_TPL: Dict[str, List[Tuple[Tuple[int, ...], ...]]] = {}
for _ch, _row_strings in _CAP_TPL_RAW.items():          # یک فرم به‌ازای هر گلیف
    _rows = [tuple(int(c) for c in s) for s in _row_strings]
    _CAP_TPL[_ch] = [tuple(_crop_cols(_rows))]  # برش ستونی — هم‌تراز با برش گلیف در زمان حل


def _cap_binarize(png_bytes: bytes) -> Optional[Tuple[List[List[int]], int, int]]:
    """PNG کپچا → گرید باینری (Pillow جزو requirements باته)."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(png_bytes)).convert("L")
        w, h = im.size
        px = im.load()
        return [[1 if px[x, y] < 140 else 0 for x in range(w)] for y in range(h)], w, h
    except Exception:
        return None


def _cap_segment(grid: List[List[int]], w: int, h: int) -> List[Tuple[int, int]]:
    """ستون‌های جوهر → بازه‌های گلیف (شکاف ستون خالی جداکننده‌ست)."""
    prof = [sum(grid[y][x] for y in range(h)) for x in range(w)]
    spans, x = [], 0
    while x < w:
        if prof[x] > 0:
            x0 = x
            while x < w and prof[x] > 0:
                x += 1
            spans.append((x0, x))
        else:
            x += 1
    return spans


def _crop_cols(rows: List[Tuple[int, ...]]) -> List[Tuple[int, ...]]:
    """برش ستون‌های بدون جوهر (تمپلیت‌ها هم‌شکل گلیفِ کراپ‌شده می‌شن)."""
    if not rows:
        return rows
    w = len(rows[0])
    cols = [x for x in range(w) if any(r[x] for r in rows)]
    if not cols:
        return rows
    return [tuple(r[cols[0]:cols[-1] + 1]) for r in rows]


def _cap_glyph(grid: List[List[int]], x0: int, x1: int) -> Tuple[Tuple[int, ...], ...]:
    """برش bbox یک گلیف (عمودی + ستونی — فقط جوهر)."""
    h = len(grid)
    ys = [y for y in range(h) for x in range(x0, x1) if grid[y][x]]
    if not ys:
        return tuple()
    y0, y1 = min(ys), max(ys) + 1
    seg = [tuple(grid[y][x] for x in range(x0, x1)) for y in range(y0, y1)]
    return tuple(_crop_cols(seg))


def solve_captcha(png_bytes: bytes) -> Optional[str]:
    """PNG کپچا → جواب عددی (رشته) یا None — عبارت فقط [0-9+-] است؛ ارزیابی امن."""
    try:
        bz = _cap_binarize(png_bytes)
        if not bz:
            return None
        grid, w, h = bz
        out = ""
        for (x0, x1) in _cap_segment(grid, w, h):
            gb = _cap_glyph(grid, x0, x1)
            if not gb:
                continue
            best, bestd = "?", 10 ** 9
            for ch, forms in _CAP_TPL.items():
                for tb in forms:
                    if len(tb) != len(gb) or (tb and len(tb[0]) != len(gb[0])):
                        continue
                    d = sum(1 for yy in range(len(gb))
                            for xx in range(len(gb[0])) if gb[yy][xx] != tb[yy][xx])
                    nd = d / max(1, len(gb) * max(1, len(gb[0])))
                    if nd < bestd:
                        best, bestd = ch, nd
            if bestd > 0.12:
                logger.debug("[Tdmmo] captcha unknown glyph (dist=%.2f)", bestd)
                return None
            out += best
        if not out or not all(c in "0123456789+-" for c in out):
            return None
        return str(eval(out))  # ایمن: ورودی فقط رقم و +- است
    except Exception as e:
        logger.debug("[Tdmmo] captcha solve error: %s", e)
        return None


# ═══════════════════════════════════════════════════════════
#   کلاینت + لاگین (single-flight)
# ═══════════════════════════════════════════════════════════

def _creds() -> Tuple[str, str]:
    return (os.getenv("TDMMO_USER", "09010883258"),
            os.getenv("TDMMO_PASS", "mamm1386"))


_COOKIES: Dict[str, str] = {}          # PHPSESSID و ...
_LOGIN_OK_UNTIL: float = 0.0           # سشن سالم تا این زمان
_LOGIN_LOCK: Optional[asyncio.Lock] = None

# کپچا/لاگین نایاب‌سازی نشه — لاگین دوباره بعد از خطای کپچا هم اینجا هندل می‌شه
_PAGE_CACHE: Dict[str, dict] = {}      # m_id → page dict (TTL 30min)
_RESOLVE_CACHE: Dict[str, dict] = {}   # token → {"url","season_path"} (TTL 90min — لینک ~2h اعتبار داره)


def _now() -> float:
    return time.time()


def _login_lock() -> asyncio.Lock:
    global _LOGIN_LOCK
    if _LOGIN_LOCK is None:
        _LOGIN_LOCK = asyncio.Lock()
    return _LOGIN_LOCK


async def _req(url: str, *, data: Optional[dict] = None,
               timeout: float = 15.0, allow_redirects: bool = True,
               referer: Optional[str] = None):
    """GET/POST با کوکی‌های سشن → (status, text, final_url, headers) یا None."""
    try:
        async with AsyncSession() as s:
            for k, v in _COOKIES.items():
                s.cookies.set(k, v)
            headers = {"User-Agent": _USER_AGENT,
                       "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
            if referer:
                headers["Referer"] = referer
            if data is not None:
                r = await s.post(url, data=data, headers=headers, timeout=timeout,
                                 impersonate="chrome", allow_redirects=allow_redirects)
            else:
                r = await s.get(url, headers=headers, timeout=timeout,
                                impersonate="chrome", allow_redirects=allow_redirects)
            try:
                _COOKIES.update({k: v for k, v in s.cookies.items()})
            except Exception:
                pass
            return r.status_code, r.text, str(r.url), r.headers
    except Exception as e:
        logger.debug("[Tdmmo] req %s failed: %s", url[:90], e)
        return None


def _is_login_page(text: str) -> bool:
    if not text:
        return True
    t = text[:4000]
    return ('id="captcha"' in t and 'name="mobile"' in t) or ('action="" method="POST"' in t)


async def ensure_login(force: bool = False) -> bool:
    """لاگین با حل خودکار کپچا. True = سشن عضو آماده‌ست."""
    global _LOGIN_OK_UNTIL
    if not force and _COOKIES and _now() < _LOGIN_OK_UNTIL:
        return True
    async with _login_lock():
        if not force and _COOKIES and _now() < _LOGIN_OK_UNTIL:
            return True
        mobile, password = _creds()
        # سشن تازه
        _COOKIES.clear()
        r = await _req(f"{_BASE}/login", timeout=15)
        if not r:
            return False
        ok = False
        for attempt in range(3):  # کپچا ممکنه یک‌بار خراب حل بشه → سشن همون می‌مونه
            cap_png = await _req_bytes(f"{_BASE}/captcha", referer=f"{_BASE}/login")
            ans = solve_captcha(cap_png) if cap_png else None
            if not ans:
                logger.warning("[Tdmmo] captcha unsolved (attempt %d)", attempt + 1)
                continue
            r2 = await _req(f"{_BASE}/login",
                            data={"mobile": mobile, "password": password,
                                  "captcha": ans, "submit": "ورود"},
                            timeout=20, referer=f"{_BASE}/login")
            if not r2:
                return False
            # تأیید قطعی عضویت: صفحه‌ی عضو بدون ریدایرکت 200 می‌ده
            r3 = await _req(f"{_BASE}/movie?m=35675", timeout=15, allow_redirects=False)
            if r3 and r3[0] == 200:
                ok = True
                break
        if ok:
            _LOGIN_OK_UNTIL = _now() + 45 * 60  # سشن ~بی‌نهایت می‌مونه؛ 45 دقیقه اطمینان
            logger.info("[Tdmmo] 🔐 login OK (%s****) — FJ session ready", mobile[:4])
        else:
            logger.warning("[Tdmmo] login FAILED after captcha attempts")
        return ok


async def _req_bytes(url: str, *, referer: Optional[str] = None,
                     timeout: float = 15.0) -> Optional[bytes]:
    try:
        async with AsyncSession() as s:
            for k, v in _COOKIES.items():
                s.cookies.set(k, v)
            headers = {"User-Agent": _USER_AGENT}
            if referer:
                headers["Referer"] = referer
            r = await s.get(url, headers=headers, timeout=timeout, impersonate="chrome")
            try:
                _COOKIES.update({k: v for k, v in s.cookies.items()})
            except Exception:
                pass
            if r.status_code == 200:
                return r.content
            return None
    except Exception as e:
        logger.debug("[Tdmmo] bytes req %s failed: %s", url[:90], e)
        return None


# ═══════════════════════════════════════════════════════════
#   پارس‌ها
# ═══════════════════════════════════════════════════════════

_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

_ITEM_RE = re.compile(
    r'<a href="movie\?m=(\d+)"\s*target="_blank">\s*'
    r'<img[^>]*class="movie_item_img"/><span class="movie_item_year">([^<]*)</span>'
    r'<span class="movie_item_imdb">([^<]*)</span><span class="movie_item_title">([^<]*)</span>',
    re.S)
_LINK_RE = re.compile(
    r'<a href="(?:https?://tdmmo\.xyz)?/play\?a=([pd])&i=([\w\-]+)&f=([^"]+)">'
    r'(.*?)</a>', re.S)
_SPAN_RE = re.compile(r'<span class="w\d+[^"]*">([^<]*)</span>')

_EP_PATTERNS = [
    # SxxEyy / Sxx.Eyy / Sxx-EPyy / Sxx_Eyy
    re.compile(r"[sS](\d{1,2})[\s._-]*[eE][pP]?\.?\s*(\d{1,3})"),
    # sX_Y / sX-YY / sX YY  (Paytakht_s3_01)
    re.compile(r"_s(\d{1,2})[\s._-]*(\d{1,3})(?:\b|_)"),
    # N.Eyy  (Paytakht.7.E02) — فصل تکی رقم قبل از .E
    re.compile(r"(?:^|[^0-9a-z])(\d{1,2})\.[eE](\d{1,3})(?:\b|[^0-9])"),
    # _Eyy_ / .Eyy. / Eyy بدون فصل (Ghoorbaghe_E01_720p)
    re.compile(r"(?:^|[^0-9a-zA-Z])[eE][pP]?\.?\s*(\d{1,3})(?:\b|[^0-9])"),
]

_QUAL_RES = [
    (re.compile(r"\b1080\s*(?:p|hq)?\b", re.I), "1080p"),
    (re.compile(r"\b720\s*(?:p|hq)?\b", re.I), "720p"),
    (re.compile(r"\b480\s*p?\b", re.I), "480p"),
    (re.compile(r"\b360\s*p?\b", re.I), "360p"),
    (re.compile(r"\b2160\s*p?\b|\b4k\b", re.I), "2160p"),
]


def _quality_of(fname: str, label: str = "") -> str:
    # ⚠ زیرخط مرز کلمه نیست (\b) → قبل از مچ، زیرخط/نقطه فاصله می‌شن (_720p → 720p)
    hay = f"{fname} {label}".translate(_FA_DIGITS).replace("_", " ").replace(".", " ")
    for rx, q in _QUAL_RES:
        if rx.search(hay):
            return q
    return ""


def _size_mb(size_text: str) -> int:
    t = size_text.translate(_FA_DIGITS)
    m = re.search(r"([\d.,]+)", t)
    if not m:
        return 0
    try:
        v = float(m.group(1).replace(",", ""))
    except ValueError:
        return 0
    if "گیگابایت" in t or "GB" in t.upper():
        return int(v * 1024)
    return int(v)


def _parse_episode(fname: str, label: str = "") -> Tuple[Optional[int], Optional[int]]:
    """(season, episode) از اسم فایل؛ fallback لیبل «قسمت N»."""
    f = fname.translate(_FA_DIGITS)
    lb = label.translate(_FA_DIGITS)
    # SxxEyy صریح
    m = _EP_PATTERNS[0].search(f)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = _EP_PATTERNS[1].search(f)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = _EP_PATTERNS[2].search(f)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = _EP_PATTERNS[3].search(f)
    ep = int(m.group(1)) if m else None
    if ep is None:
        m = re.search(r"قسمت\s*(\d{1,3})", lb)
        if m:
            ep = int(m.group(1))
    return None, ep


def _fasl_of(path: str) -> Optional[int]:
    m = re.search(r"fasl[\s._-]*(\d{1,2})", path, re.I)
    return int(m.group(1)) if m else None


def _tokens(text: str) -> set:
    t = re.sub(r"[^\w\s\u0600-\u06FF]", " ", (text or "").translate(_FA_DIGITS)).lower()
    return {w for w in t.split() if len(w) >= 2 and not w.isdigit()}


def _norm_title(text: str) -> str:
    t = (text or "").translate(_FA_DIGITS).lower().strip()
    return re.sub(r"\s+", " ", t)


def _name_coverage(title: str, name: str) -> float:
    """کوورینس توکن‌های «اسم IMDb» داخل عنوان کاندیدا — هر دو طرف باید معنادار باشن.
    برادران لیلا در برابر برادران منندز → 0.5 (رد)؛ قورباغه در برابر قورباغه → 1.0"""
    nt, tt = _tokens(name), _tokens(title)
    if not nt or not tt:
        return 0.0
    return len(nt & tt) / len(nt)


def _year_score(year_raw: str, year: Optional[int]) -> float:
    """+6 مچ دقیق، +4 فاصله‌ی ۱، +1 فاصله‌ی ۲، فاصله‌ی ۳+ → -100 قطعی.
    (سال از IMDb معتبره؛ فاصله‌ی بزرگ یعنی «هم‌اسم ولی اثر دیگه» — مثل
    قورباغه‌ی کره‌ای 2024 در برابر قورباغه‌ی ایرانی 1399≈2020). سال شمسی تبدیل می‌شه."""
    if not year:
        return 0.0
    yr = (year_raw or "").translate(_FA_DIGITS).strip()
    if not yr.isdigit():
        return 0.0            # سال نامعلوم → نه جریمه، نه جایزه
    y = int(yr)
    cands = [y]
    if 1300 <= y <= 1500:          # سال شمسی (محتوای ایرانی) → میلادی
        cands += [y + 621, y + 622]
    diff = min(abs(c - year) for c in cands)
    if diff == 0:
        return 6.0
    if diff == 1:
        return 4.0
    if diff == 2:
        return 1.0
    return -100.0   # فاصله‌ی ≥۳: هم‌اسمِ متفاوت — قطعی رد


def _score_page(title: str, year_raw: str, names: List[str],
                year: Optional[int], is_series: bool,
                page_files: Optional[List[dict]] = None) -> float:
    """امتیاز مچ صفحه‌ی tdmmo با عنوان IMDb.
    • کوورینس اسم داخل عنوان صفحه (فارسی) — max روی اسم‌ها؛ <0.6 → رد قطعی
    • اسم‌های لاتین داخل «اسم فایل‌ها» هم چک می‌شن (محتوای خارجی عنوان فارسی نمایشی داره)
    • سال شمسی/میلادی + بونوس مچ دقیق اسم + ساختار قسمت برای سریال"""
    best_cov, best_exact = 0.0, False
    file_blob = " ".join(f.get("fname", "") for f in (page_files or []))
    for nm in names or []:
        cov = _name_coverage(title, nm)
        if page_files:
            cov = max(cov, _name_coverage(file_blob, nm))
        if cov > best_cov:
            best_cov = cov
        if _norm_title(title) == _norm_title(nm):
            best_exact = True
    if best_cov < 0.6:
        return -100.0          # اسم اصلاً نیامده → قطعاً عنوان دیگه‌ست (برادران منندز ≠ برادران لیلا)
    score = best_cov * 10.0
    if best_exact:
        score += 3.0
    score += _year_score(year_raw, year)
    if is_series and page_files:
        eps = {f.get("episode") for f in page_files if f.get("episode")}
        if eps:
            score += 3.0
        else:
            score -= 2.0
    return score


# ═══════════════════════════════════════════════════════════
#   سرچ / صفحه / رزولوشن
# ═══════════════════════════════════════════════════════════

async def _search(query: str, limit: int = 10) -> List[dict]:
    """سرچ سایت → [{id, year_raw, rating, title}] — None/خطا → []"""
    if not await ensure_login():
        return []
    q = urllib.parse.quote(str(query).strip())
    r = await _req(f"{_BASE}/search?q={q}&submit=", timeout=15, referer=f"{_BASE}/search")
    if not r:
        return []
    status, text, final_url, _ = r
    if status != 200 or _is_login_page(text):
        return []
    out = []
    for m in _ITEM_RE.finditer(text):
        out.append({"id": m.group(1), "year_raw": m.group(2).strip(),
                    "rating": m.group(3).strip(), "title": m.group(4).strip()})
        if len(out) >= limit:
            break
    return out


def _parse_page(text: str) -> dict:
    """صفحه‌ی movie → {title, files:[{a, token, fname, size_mb, quality, label, season, episode}]}"""
    title = ""
    m = re.search(r'DetilesTitlesLarg">([^<]+)<', text)
    if m:
        title = m.group(1).strip()
    files, seen = [], set()
    for m in _LINK_RE.finditer(text):
        a, token, fname, inner = m.group(1), m.group(2), m.group(3), m.group(4)
        if token in seen:      # لیست پخش و دانلود هم‌تکن‌ند
            continue
        seen.add(token)
        spans = _SPAN_RE.findall(inner)
        size_text = spans[0] if spans else ""
        label = spans[1] if len(spans) > 1 else ""
        season, episode = _parse_episode(fname, label)
        files.append({"a": "p",   # ⚠ همیشه a=p — a=d اشتراک می‌خواد، a=p بدون اشتراک 302 مستقیم می‌ده
                      "token": token, "fname": fname,
                      "size_mb": _size_mb(size_text),
                      "quality": _quality_of(fname, label), "label": label,
                      "season": season, "episode": episode})
    return {"title": title, "files": files}


async def _page(m_id: str) -> Optional[dict]:
    """صفحه با کش ۳۰ دقیقه‌ای — لاگین‌خورده."""
    c = _PAGE_CACHE.get(m_id)
    if c and _now() - c["ts"] < 1800:
        return c["page"]
    if not await ensure_login():
        return None
    r = await _req(f"{_BASE}/movie?m={m_id}", timeout=20, referer=f"{_BASE}/search")
    if not r:
        return None
    status, text, final_url, _ = r
    looks_logged_out = (status in (301, 302, 303, 307)) or _is_login_page(text)
    looks_like_page = ("DetilesTitlesLarg" in text) or ("movie_item" in text)
    if status != 200 or looks_logged_out or not looks_like_page:
        if looks_logged_out:
            # سشن مُده — لاگین دوباره و یک تلاش دیگر
            if not await ensure_login(force=True):
                return None
            r = await _req(f"{_BASE}/movie?m={m_id}", timeout=20, referer=f"{_BASE}/search")
            if not r:
                return None
            status, text, final_url, _ = r
            if status != 200 or _is_login_page(text):
                return None
        else:
            return None
    page = _parse_page(text)
    _PAGE_CACHE[m_id] = {"ts": _now(), "page": page}
    if len(_PAGE_CACHE) > 64:
        for k in list(_PAGE_CACHE)[:32]:
            _PAGE_CACHE.pop(k, None)
    return page


async def _resolve(token: str, fname: str) -> Optional[dict]:
    """توکن play → URL مستقیم CDN + فصل واقعی از مسیر (faslN). کش ۹۰ دقیقه."""
    c = _RESOLVE_CACHE.get(token)
    if c and _now() - c["ts"] < 5400:
        return c
    if not await ensure_login():
        return None
    url = f"{_BASE}/play?a=p&i={token}&f={urllib.parse.quote(fname)}"
    r = await _req(url, timeout=20, allow_redirects=False)
    if not r:
        return None
    status, text, final_url, headers = r
    loc = headers.get("location", "") if headers else ""
    if status != 302 or not loc:
        if status in (301, 302, 303, 307) and not loc:
            return None
        # 200 → صفحه‌ی «اشتراک به پایان رسیده» یا لاگین
        if _is_login_page(text):
            if not await ensure_login(force=True):
                return None
            return await _resolve(token, fname)
        logger.debug("[Tdmmo] resolve %s → HTTP %d (no loc) — subscription?", token, status)
        return None
    if not loc.startswith("vlc://"):
        logger.debug("[Tdmmo] resolve %s → unexpected scheme: %s", token, loc[:60])
        return None
    direct = loc.replace("vlc://", "http://", 1)
    out = {"url": direct, "season_path": _fasl_of(direct), "ts": _now()}
    _RESOLVE_CACHE[token] = out
    if len(_RESOLVE_CACHE) > 256:
        for k in list(_RESOLVE_CACHE)[:96]:
            _RESOLVE_CACHE.pop(k, None)
    return out


async def _resolve_many(items: List[dict], concurrency: int = 8) -> List[Optional[dict]]:
    sem = asyncio.Semaphore(concurrency)

    async def one(it):
        async with sem:
            try:
                return await asyncio.wait_for(_resolve(it["token"], it["fname"]), 12.0)
            except Exception:
                return None

    return await asyncio.gather(*[one(i) for i in items])


# ═══════════════════════════════════════════════════════════
#   پروب اصلی — entry هم‌شکل بقیه‌ی سرورها (server="FJ", iran=True)
# ═══════════════════════════════════════════════════════════

async def probe_tdmmo(imdb_id: str, names: List[str], year: Optional[int],
                      is_series: bool, season: Optional[int] = None,
                      episode: Optional[int] = None, timeout: float = 14.0) -> List[dict]:
    """
    سرچ موازی اسم‌ها در tdmmo → بهترین صفحه (اسم+سال، جریمه‌ی سال مخالف) →
    فیلم: همه‌ی کیفیت‌ها | سریال: فایل‌های (فصل،قسمت) دقیق → entry یا [].
    """
    if not names:
        return []
    names = [n for n in names if n and len(str(n).strip()) >= 2][:4]

    async def _guarded():
        # ۱) سرچ موازی
        jobs = [_search(nm, limit=10) for nm in names]
        try:
            batches = await asyncio.wait_for(asyncio.gather(*jobs, return_exceptions=True), timeout)
        except Exception as e:
            logger.info("[Tdmmo] search timeout for %s: %s", imdb_id, e)
            return []
        cands: Dict[str, dict] = {}
        for b in batches:
            if isinstance(b, Exception):
                continue
            for c in b or []:
                if c["id"] not in cands:
                    cands[c["id"]] = c
        if not cands:
            logger.info("[Tdmmo] %s: no candidate", imdb_id)
            return []
        # ۲) رنک ارزون با داده‌ی سرچ (عنوان+سال) → فقط ۳ تا اول صفحه می‌گیرن
        pre = []
        for c in cands.values():
            cov = max((_name_coverage(c["title"], nm) for nm in names), default=0.0)
            pre.append((cov * 10.0 + _year_score(c["year_raw"], year), cov, c))
        pre.sort(key=lambda x: -x[0])
        shortlist = [c for s, cov, c in pre[:3] if cov >= 0.6] or [c for _, cov, c in pre[:2] if cov > 0]
        if not shortlist:
            logger.info("[Tdmmo] %s: no title-matched candidate (%s)", imdb_id,
                        [(c["title"][:20], c["year_raw"]) for s, cov, c in pre[:3]])
            return []
        # ۳) صفحه‌ی shortlist → امتیاز کامل (اسم فایل‌ها + ساختار قسمت)
        pages = await asyncio.gather(*[_page(c["id"]) for c in shortlist],
                                     return_exceptions=True)
        ranked = []
        for c, pg in zip(shortlist, pages):
            if isinstance(pg, Exception) or not pg:
                continue
            sc = _score_page(pg.get("title", "") or c["title"], c["year_raw"],
                             names, year, is_series, pg.get("files") or [])
            ranked.append((sc, c, pg))
        ranked.sort(key=lambda x: -x[0])
        if not ranked or ranked[0][0] <= 3.0:
            logger.info("[Tdmmo] %s: all low-score (%s)", imdb_id,
                        [(round(s, 1), p.get("title", "")[:20]) for s, _, p in ranked[:3]])
            return []
        sc, best, page = ranked[0]
        logger.info("[Tdmmo] %s → «%s» (m=%s, year=%s) score=%.1f files=%d",
                    imdb_id, (page.get("title") or best["title"])[:30],
                    best["id"], best["year_raw"], sc, len(page["files"]))
        files = page["files"]
        if not files:
            return []

        # ۳) انتخاب فایل‌ها
        if is_series:
            if not (season and episode):
                return []  # سریال فقط در سطح قسمت سرو می‌شه (پروب بعد از انتخاب قسمت میاد)
            pool = [f for f in files if f.get("episode") == episode]
            if not pool:
                logger.info("[Tdmmo] %s: S%sE%s not on page (%d files)",
                            imdb_id, season, episode, len(files))
                return []
            explicit = [f for f in pool if f.get("season") == season]
            ambiguous = [f for f in pool if f.get("season") is None]
            picked = list(explicit)
            resolved: Dict[str, Optional[dict]] = {}
            if ambiguous:
                # فصل واقعی از مسیر CDN (faslN) — حقیقت، نه حدس (یک بار resolve، بعد فیلتر)
                rs_list = await _resolve_many(ambiguous)
                for f, rs in zip(ambiguous, rs_list):
                    resolved[f["token"]] = rs
                    if rs and (rs.get("season_path") == season
                               or (rs.get("season_path") is None and season == 1)):
                        picked.append(f)
            if not picked:
                logger.info("[Tdmmo] %s: S%sE%s files exist but other season",
                            imdb_id, season, episode)
                return []
            for f in picked:
                if f["token"] not in resolved:
                    resolved[f["token"]] = await _resolve(f["token"], f["fname"])
            rows = []
            for f in picked:
                rs = resolved.get(f["token"])
                if rs and rs.get("url"):
                    rows.append({"url": rs["url"], "label": f["quality"] or "Auto",
                                 "size_mb": f["size_mb"], "fname": f["fname"]})
            return _mk_entry(rows, best, page)
        # فیلم
        resolved = await _resolve_many(files)
        rows = []
        for f, rs in zip(files, resolved):
            if rs and rs.get("url"):
                rows.append({"url": rs["url"], "label": f["quality"] or "Auto",
                             "size_mb": f["size_mb"], "fname": f["fname"]})
        return _mk_entry(rows, best, page)

    try:
        return await asyncio.wait_for(_guarded(), timeout + 10.0)
    except Exception as e:
        logger.warning("[Tdmmo] probe %s failed: %s", imdb_id, e)
        return []


def _mk_entry(rows: List[dict], cand: dict, page: dict) -> List[dict]:
    """entry استاندارد (شکل سرورهای CDN) — لیبل کیفیت یکتا؛ تکراری → پسوند سایز."""
    if not rows:
        return []
    rows.sort(key=lambda r: -(int(re.sub(r"\D", "", r["label"] or "0") or 0)))
    seen = set()
    quals = []
    for r in rows:
        lab = r["label"] or "Auto"
        if lab in seen and lab != "Auto":
            lab = f"{lab} ({r['size_mb']}MB)" if r.get("size_mb") else lab + " ∙"
        seen.add(lab)
        quals.append({"label": lab, "url": r["url"], "bandwidth": 0,
                      "resolution": ""})
    return [{
        "server": "FJ",
        "type": "mp4",
        "headers": {"User-Agent": _USER_AGENT, "Referer": f"{_BASE}/"},
        "url": quals[0]["url"],
        "qualities": quals,
        "unverified": False,
        "iran": True,
        "iran_meta": {"post_url": f"{_BASE}/movie?m={cand['id']}",
                      "post_title": page.get("title") or cand["title"],
                      "source": "fj"},
    }]


# ═══════════════════════════════════════════════════════════
#   تست زنده
# ═══════════════════════════════════════════════════════════

async def _test():
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    # ۱) کپچا
    cap_png = await _req_bytes(f"{_BASE}/captcha", referer=f"{_BASE}/login")
    ans = solve_captcha(cap_png or b"")
    print("captcha solve:", ans)

    # ۲) قورباغه ایرانی tt12920838 (نه کره‌ای!) — قسمت ۵
    ents = await probe_tdmmo("tt12920838", ["The Frog", "قورباغه"], 2020, True, 1, 5)
    for e in ents:
        print("FJ entry:", e["server"], [(q["label"]) for q in e["qualities"]])
        print("  url0:", e["url"][:100])

    # ۳) متری شیش و نیم (فیلم)
    ents2 = await probe_tdmmo("tt9817070", ["Just 6.5", "متری شیش و نیم"], 2019, False)
    for e in ents2:
        print("FJ movie:", [(q["label"]) for q in e["qualities"]])

    # ۴) برادران لیلا — نباید چیزی بده (سایت نداره)
    ents3 = await probe_tdmmo("tt19653180", ["Leila's Brothers", "برادران لیلا"], 2022, False)
    print("Leila's Brothers (must be empty):", ents3)


if __name__ == "__main__":
    asyncio.run(_test())
