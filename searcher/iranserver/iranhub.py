"""
iranhub.py — تجمیع‌کننده‌ی منابع ایرانی برای جریان IMDb
════════════════════════════════════════════════════════════
z20: وقتی کاربر فیلم/سریال ایرانی سرچ می‌کنه (متری شیش و نیم، قورباغه،
برادران لیلا و...)، سرورهای خارجی CDN (Vidzee/2Embed/...) معمولاً عنوان رو
ندارن. این ماژول همون عنوان رو بین منابع ایرانی پیدا می‌کنه و لینک‌های
مستقیم MP4/MKV رو به‌صورت entry با همون شکل سرورهای CDN برمی‌گردونه تا
توی همون منوی کیفیت/سرور دیده بشن و با همون download_with_quality دانلود بشن.

منابع:
  1. film2movie.asia  — WP REST API + محتوای پست (دارای tt IMDb برای مچ دقیق)
  2. doostihaa.com    — سرچ HTML سایت (?s=) + WP REST محتوا (لینک irdanlod)
  3. farsiland.com    — best-effort (از بعضی IP ها 403 می‌ده)

نکته‌ی حیاتی (قورباغه!): بین «قورباغه ایرانی» (tt12920838، ۲۰۲۰) و «The Frog
کره‌ای» (tt26767508، ۲۰۲۴) با tt دقیق تفکیک می‌شه — محتوای film2movie شماره‌ی
IMDb رو داخل خودش داره؛ doostihaa با سالِ داخل عنوان رد می‌شه.
"""

import asyncio
import logging
import os
import re
from typing import Dict, List, Optional

from curl_cffi.requests import AsyncSession

logger = logging.getLogger("IranHub")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_F2M_BASE = "https://www.film2movie.asia"
_DOOST_BASE = "https://www.doostihaa.com"
_FARSI_BASE = "https://farsiland.com"

# هاست‌های دانلود مستقیم قابل قبول (بدون دیوار پرداخت)
_DL_HOST_OK = re.compile(
    r"(irdanlod|salamcinama|upera\.tv|\.upera\.tv|ftk\.pw|metafilm|cactus|"
    r"digimoviez|dl\d*\.|host\d*\.|movie\.dl|dl\.my|dlmy|mihan|tex\.)",
    re.IGNORECASE,
)
# لینک‌های «خرید/پرداختی» که فایل مستقیم نمی‌دن (upera.shop = صفحه‌ی فروش)
_DL_HOST_BAD = re.compile(r"(upera\.shop|upera\.tv/episode/download|zaya\.io|uptune\.ir)", re.IGNORECASE)

# ─── اعداد فارسی → ASCII ──────────────────────────────────────
_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def fa_digits(text: str) -> str:
    """تبدیل ارقام فارسی/عربی به ASCII"""
    return (text or "").translate(_FA_DIGITS)


def _strip_html(html: str) -> str:
    txt = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", txt).strip()


# ─── تشخیص کیفیت از URL/متن ───────────────────────────────────

_QUALITY_PATTERNS = [
    (r"2160p|4k\b|uhd", "2160p"),
    (r"1080hq|1080\.hq|fullhd|full[-_. ]?hd", "1080p HQ"),
    (r"bluray|blu[-_. ]?ray|بلوری", "BluRay"),
    (r"1080p|x265|hevc", "1080p"),
    (r"720p", "720p"),
    (r"480p", "480p"),
    (r"360p", "360p"),
    (r"240p", "240p"),
]


def detect_quality(url: str = "", text: str = "") -> str:
    """کیفیت از URL + متن — ارقام فارسی، 1080 بدون p، HQ و BluRay رو هم می‌گیره."""
    combined = fa_digits(f"{url} {text}")
    # اول کیفیت‌های خاص (bluray/hq اهمیت دارن)
    if re.search(r"bluray|blu[-_. ]?ray|بلوری", combined, re.IGNORECASE):
        if re.search(r"2160|4k", combined, re.IGNORECASE):
            return "2160p BluRay"
        if re.search(r"720", combined):
            return "720p BluRay"
        return "1080p BluRay"
    if re.search(r"1080hq|1080\.hq|fullhd|full[-_. ]?hd", combined, re.IGNORECASE):
        return "1080p HQ"
    # عدد کیفیت: 1080p / -1080. / .1080. / ۱۰۸۰p
    m = re.search(r"[\W_](2160|1440|1080|720|480|360|240)[\W_.]?", combined)
    if m:
        q = m.group(1)
        if q == "2160":
            return "2160p"
        if q == "1440":
            return "1440p"
        if q == "1080":
            return "1080p"
        if q == "720":
            return "720p"
        if q == "480":
            return "480p"
        if q == "360":
            return "360p"
        if q == "240":
            return "240p"
    m2 = re.search(r"\b(2160|1080|720|480|360|240)p\b", combined, re.IGNORECASE)
    if m2:
        return f"{m2.group(1)}p"
    return ""


def _sort_key(q: dict):
    """ترتیب نمایش کیفیت‌ها: بزرگ‌تر اول"""
    order = {"2160p BluRay": 0, "2160p": 1, "1440p": 2, "1080p BluRay": 3,
             "1080p HQ": 4, "1080p": 5, "720p BluRay": 6, "720p": 7,
             "480p": 8, "360p": 9, "240p": 10}
    lbl = q.get("label", "")
    return (order.get(lbl, 20), lbl)


# ─── جلسه‌ی HTTP مشترک ────────────────────────────────────────

async def _http_get(url: str, *, timeout: float = 15.0, headers: dict = None,
                    is_json: bool = False):
    """GET با curl_cffi — None در خطا"""
    try:
        async with AsyncSession() as s:
            r = await s.get(
                url,
                impersonate="chrome",
                timeout=timeout,
                headers=headers or {"User-Agent": _USER_AGENT,
                                    "Accept": "application/json,text/html,*/*"},
            )
            if r.status_code != 200:
                logger.warning("[IranHub] GET %s → HTTP %d", url[:90], r.status_code)
                return None
            return r.json() if is_json else r.text
    except Exception as e:
        logger.warning("[IranHub] GET %s failed: %s", url[:90], e)
        return None


# ═══════════════════════════════════════════════════════════
#   film2movie.asia
# ═══════════════════════════════════════════════════════════

_F2M_CACHE: Dict[str, dict] = {}


async def _f2m_post(post_id: str) -> dict:
    """محتوای WP پست film2movie (کش‌شده)"""
    if post_id in _F2M_CACHE:
        return _F2M_CACHE[post_id]
    data = await _http_get(f"{_F2M_BASE}/wp-json/wp/v2/posts/{post_id}",
                           timeout=20, is_json=True)
    post = data if isinstance(data, dict) and data.get("content") else {}
    if post:
        if len(_F2M_CACHE) > 300:
            _F2M_CACHE.clear()
        _F2M_CACHE[post_id] = post
    return post


def _f2m_tt_ids(content: str) -> set:
    """شماره‌های IMDb داخل محتوای پست"""
    tids = set(re.findall(r"imdb\.com/title/(tt\d+)", content, re.IGNORECASE))
    tids |= set(re.findall(r"\b(tt\d{7,10})\b", content))
    return tids


def _f2m_direct_links(content: str) -> List[str]:
    """لینک‌های مستقیم فیلم (بدون پنجره‌ی خرید)"""
    out, seen = [], set()
    for href in re.findall(r'href="(https?://[^"]+)"', content or ""):
        href = href.replace("&amp;", "&")
        if href in seen:
            continue
        seen.add(href)
        if not re.search(r"\.(mp4|mkv|avi)(\?|$)", href, re.IGNORECASE):
            continue
        if _DL_HOST_BAD.search(href):
            continue
        if not (_DL_HOST_OK.search(href) or re.search(r"\.(mp4|mkv|avi)", href, re.IGNORECASE)):
            continue
        out.append(href)
    return out


def _f2m_episode_links(content: str, season: int, episode: int) -> List[dict]:
    """لینک‌های قسمت مشخص از پاراگراف‌های «قسمت ۰۱ _ ۷۲۰p» یا URL های SxxEyy."""
    out = []
    seen = set()
    # ۱) پاراگراف‌های قسمت‌دار
    for p in re.findall(r"<p[^>]*>(.*?)</p>", content or "", re.S):
        if "upera.shop" in p or "upera.tv/episode/download" in p:
            # پاراگراف‌های پولی → skip اگر فقط همون باشن
            if not _DL_HOST_OK.search(p):
                continue
        hrefs = re.findall(r'href="(https?://[^"]+)"', p)
        if not hrefs:
            continue
        txt = fa_digits(_strip_html(p))
        m_ep = re.search(r"قسمت\s*(\d{1,2})", txt)
        if not m_ep:
            continue
        ep = int(m_ep.group(1))
        if ep != episode:
            continue
        m_se = re.search(r"[sS](\d{1,2})\s*[eE]\s*(\d{1,2})", txt)
        if m_se and int(m_se.group(1)) != season and int(m_se.group(2)) == episode:
            continue  # قسمت با همین شماره ولی فصل دیگه
        for href in hrefs:
            href = href.replace("&amp;", "&")
            if href in seen:
                continue
            seen.add(href)
            if not re.search(r"\.(mp4|mkv|avi)(\?|$)", href, re.IGNORECASE):
                continue
            if _DL_HOST_BAD.search(href):
                continue
            out.append({
                "url": href,
                "label": detect_quality(href, txt),
                "episode": ep,
                "season": m_se.group(1) if m_se else season,
            })
    # ۲) URL های SxxEyy مستقیم
    if not out:
        for href in re.findall(r'href="(https?://[^"]+)"', content or ""):
            href = href.replace("&amp;", "&")
            if href in seen or not re.search(r"\.(mp4|mkv|avi)(\?|$)", href, re.IGNORECASE):
                continue
            if _DL_HOST_BAD.search(href):
                continue
            m = re.search(r"[sS](\d{1,2})[\s._-]*[eE][p]?(\d{1,2})", fa_digits(href))
            if m and int(m.group(1)) == season and int(m.group(2)) == episode:
                seen.add(href)
                out.append({
                    "url": href,
                    "label": detect_quality(href),
                    "episode": episode,
                    "season": season,
                })
    return out


async def _f2m_search(query: str, limit: int = 8) -> List[dict]:
    """سرچ film2movie با WP REST — فقط پست‌های دانلود"""
    q = quote_safe(query)
    data = await _http_get(
        f"{_F2M_BASE}/wp-json/wp/v2/posts?search={q}&per_page={limit}",
        timeout=15, is_json=True)
    posts = data if isinstance(data, list) else []
    out = []
    for post in posts:
        title = _strip_html(post.get("title", {}).get("rendered", ""))
        link = post.get("link", "") or ""
        if "دانلود" not in title:
            continue
        out.append({
            "source": "f2m",
            "id": str(post.get("id", "")),
            "title": title,
            "url": link,
            "year": next((y for y in re.findall(r"\b(19\d{2}|20\d{2})\b", title)[:1]), ""),
        })
    return out


def quote_safe(q: str) -> str:
    import urllib.parse
    return urllib.parse.quote_plus(q or "")


# ═══════════════════════════════════════════════════════════
#   doostihaa.com
# ═══════════════════════════════════════════════════════════

_DOOST_CACHE: Dict[str, dict] = {}


async def _doost_post(post_id: str) -> dict:
    """محتوای WP پست doostihaa (کش‌شده)"""
    if post_id in _DOOST_CACHE:
        return _DOOST_CACHE[post_id]
    data = await _http_get(f"{_DOOST_BASE}/wp-json/wp/v2/posts/{post_id}",
                           timeout=20, is_json=True)
    post = data if isinstance(data, dict) and data.get("content") else {}
    if post:
        if len(_DOOST_CACHE) > 300:
            _DOOST_CACHE.clear()
        _DOOST_CACHE[post_id] = post
    return post


async def _doost_postid_from_url(url: str) -> Optional[str]:
    """post id از صفحه‌ی HTML پست doostihaa"""
    html = await _http_get(url, timeout=20)
    if not html:
        return None
    m = re.search(r"postid-(\d+)", html)
    return m.group(1) if m else None


async def _doost_search(query: str, limit: int = 8) -> List[dict]:
    """سرچ doostihaa با صفحه‌ی HTML سایت (?s=) — دقیق‌تر از REST search."""
    q = quote_safe(query)
    html = await _http_get(
        f"{_DOOST_BASE}/?s={q}",
        timeout=15,
        headers={"User-Agent": _USER_AGENT, "Accept": "text/html",
                 "Referer": f"{_DOOST_BASE}/"},
    )
    if not html:
        return []
    out, seen = [], set()
    for href, title in re.findall(
            r'<h2[^>]*>\s*<a[^>]*href="(https://www\.doostihaa\.com/post/[^"]+)"[^>]*>([^<]+)</a>',
            html):
        title = title.strip()
        if href in seen:
            continue
        seen.add(href)
        out.append({
            "source": "doost",
            "id": "",  # بعداً از صفحه‌ی پست گرفته می‌شه
            "title": title,
            "url": href,
            "year": next((y for y in re.findall(r"\b(19\d{2}|20\d{2})\b", title)[:1]), ""),
        })
        if len(out) >= limit:
            break
    return out


def _doost_direct_links(content: str) -> List[str]:
    """لینک‌های مستقیم doostihaa (irdanlod/upera.tv/...)"""
    out, seen = [], set()
    for href in re.findall(r'href="(https?://[^"]+)"', content or ""):
        href = href.replace("&amp;", "&")
        if href in seen:
            continue
        seen.add(href)
        if not re.search(r"\.(mp4|mkv|avi)(\?|$)", href, re.IGNORECASE):
            continue
        if _DL_HOST_BAD.search(href):
            continue
        out.append(href)
    return out


_SEASON_WORDS = {
    "اول": 1, "دوم": 2, "سوم": 3, "چهارم": 4, "پنجم": 5, "ششم": 6, "هفتم": 7,
    "هشتم": 8, "نهم": 9, "دهم": 10, "یازدهم": 11, "دوازدهم": 12,
}


def _f2m_episode_links_seasoned(content: str, season: int, episode: int) -> List[dict]:
    """لینک قسمت با تشخیص فصل: پست‌های film2movie فصل‌ها رو با هدینگ
    «فصل اول/فصل دوم» جدا می‌کنن؛ «قسمت N» داخل هر سکشن متعلق به فصل همونه.

    ۱) اول SxxEyy صریح (بهترین سیگنال)
    ۲) سکشن‌بندی با هدینگ «فصل X» (ارقام فارسی + حروفی) → قسمت N داخل سکشنِ فصل درخواستی
    ۳) اگر هدینگ فصل نبود → کل پست = فصل درخواستی (سریال تک‌فصلی مثل قورباغه)
    """
    if not content:
        return []
    # ۱) SxxEyy صریح در URL
    out = []
    for href in re.findall(r'href="(https?://[^"]+)"', content):
        href = href.replace("&amp;", "&")
        if not re.search(r"\.(mp4|mkv|avi)(\?|$)", href, re.IGNORECASE):
            continue
        if _DL_HOST_BAD.search(href):
            continue
        m = re.search(r"[sS](\d{1,2})[\s._-]*[eE][p]?(\d{1,2})", fa_digits(href))
        if m and int(m.group(1)) == season and int(m.group(2)) == episode:
            out.append({"url": href, "label": detect_quality(href),
                        "episode": episode, "season": season})
    if out:
        return out

    # ۲) سکشن‌بندی با هدینگ فصل
    fa = fa_digits(content)
    marks = []
    for m in re.finditer(r"فصل\s*(\d{1,2})\b", fa):
        marks.append((m.start(), int(m.group(1))))
    for word, num in _SEASON_WORDS.items():
        for m in re.finditer(rf"فصل\s+{word}\b", fa):
            marks.append((m.start(), num))
    marks.sort()

    def _links_in(seg: str) -> List[dict]:
        rows = []
        seen = set()
        for p in re.findall(r"<p[^>]*>(.*?)</p>", seg, re.S):
            hrefs = re.findall(r'href="(https?://[^"]+)"', p)
            if not hrefs:
                continue
            txt = _strip_html(p)
            m_ep = re.search(r"قسمت\s*(\d{1,2})", txt)
            if not m_ep:
                continue
            ep = int(m_ep.group(1))
            if ep != episode:
                continue
            for href in hrefs:
                href = href.replace("&amp;", "&")
                if href in seen:
                    continue
                seen.add(href)
                if not re.search(r"\.(mp4|mkv|avi)(\?|$)", href, re.IGNORECASE):
                    continue
                if _DL_HOST_BAD.search(href):
                    continue
                if not _DL_HOST_OK.search(href):
                    continue
                rows.append({"url": href, "label": detect_quality(href, txt),
                             "episode": ep, "season": season})
        return rows

    if marks:
        # سکشن i = از mark[i] تا mark[i+1]
        for idx, (pos, snum) in enumerate(marks):
            if snum != season:
                continue
            end = marks[idx + 1][0] if idx + 1 < len(marks) else len(fa)
            rows = _links_in(fa[pos:end])
            if rows:
                return rows
        return []
    # ۳) بدون هدینگ فصل → کل پست (تک‌فصلی)
    return _links_in(fa)


# ═══════════════════════════════════════════════════════════
#   farsiland.com (best-effort)
# ═══════════════════════════════════════════════════════════

async def _farsi_search(query: str, limit: int = 6) -> List[dict]:
    """سرچ farsiland — ممکنه 403 بده (Cloudflare)؛ در این‌صورت خالی."""
    try:
        from searcher.iranserver.farsiland_search import search_farsiland
        rows = await asyncio.wait_for(search_farsiland(query, limit=limit), timeout=12)
        return [{
            "source": "farsi",
            "id": r.get("id", ""),
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "year": str(r.get("year", "") or ""),
            "imdb": str(r.get("imdb", "") or ""),
        } for r in rows if isinstance(r, dict)]
    except Exception as e:
        logger.debug("[IranHub] farsiland unavailable: %s", e)
        return []


# ═══════════════════════════════════════════════════════════
#   مچ کردن عنوان‌ها
# ═══════════════════════════════════════════════════════════

_STOP = {"دانلود", "فیلم", "سریال", "با", "کیفیت", "ایرانی", "خارجی", "رایگان",
         "کامل", "قسمت", "فصل", "سینمایی", "انیمیشن", "مستند", "دوبله", "زیرنویس",
         "شده", "اضافه", "بدون", "سانسور", "hd", "bluray", "web", "dl", "dub", "dubbed"}


def _tokens(text: str) -> set:
    """توکن‌های معنادار عنوان (حذف کلمات عمومی)"""
    text = fa_digits(text or "").lower()
    toks = set()
    for t in re.split(r"[\s\-_.:,،()«»]+", text):
        t = t.strip()
        if len(t) >= 2 and t not in _STOP:
            toks.add(t)
    return toks


def score_candidate(cand: dict, names: List[str], year: Optional[int],
                    tt_exact_possible: bool = False) -> float:
    """امتیاز کاندیدا نسبت به اسم‌ها/سال — tt دقیق بعداً جدا چک می‌شه."""
    title = fa_digits(cand.get("title", "")).lower()
    ctoks = _tokens(title)
    best = 0.0
    for nm in names:
        if not nm:
            continue
        nm_l = fa_digits(nm).lower()
        # مچ کامل عنوان
        if nm_l in title:
            best = max(best, 40.0)
        ntoks = _tokens(nm)
        if ntoks:
            overlap = len(ntoks & ctoks) / max(1, len(ntoks))
            best = max(best, 34.0 * overlap + (6.0 if overlap == 1.0 else 0.0))
    # سال
    cy = str(cand.get("year", "") or "")
    if year and cy:
        if abs(int(cy) - year) <= 1:
            best += 14.0
        else:
            best -= 22.0  # سال متفاوت = احتمال فیلم دیگه (مثل قورباغه کره‌ای ۲۰۲۴!)
    # جریمه‌ی کره‌ای/کشور اشتباه — وقتی اسم فارسی سرچ شده
    if any(re.search(r"کره|کوره[ای]|korean", title) for _ in [0]):
        best -= 18.0
    return best


# ═══════════════════════════════════════════════════════════
#   API عمومی — پراب سرورهای ایرانی
# ═══════════════════════════════════════════════════════════

def _mk_entry(server: str, quality_rows: List[dict], meta: dict) -> Optional[dict]:
    """entry با شکل پروب‌های CDN → توی همون منوی کیفیت/سرور می‌شینه."""
    rows = []
    seen = set()
    for q in quality_rows:
        url = q.get("url", "")
        if not url or url in seen:
            continue
        seen.add(url)
        rows.append({
            "label": q.get("label") or "Auto",
            "url": url,
            "bandwidth": 0,
            "resolution": "",
        })
    if not rows:
        return None
    rows.sort(key=_sort_key)
    return {
        "server": server,
        "type": "mp4",          # دانلود مستقیم
        "headers": {"User-Agent": _USER_AGENT, "Referer": meta.get("referer", "")},
        "url": rows[0]["url"],  # بهترین کیفیت
        "qualities": rows,
        "unverified": False,
        "iran": True,           # 🆕 فلگ منابع ایرانی (برچسب 🇮🇷 در منو)
        "iran_meta": {k: meta.get(k) for k in ("post_url", "post_title", "source")},
    }


async def _resolve_candidate(cand: dict, season: Optional[int],
                             episode: Optional[int], timeout: float) -> Optional[dict]:
    """برای یک کاندیدا: محتوای پست → tt ids + لینک‌های مستقیم (فیلم/قسمت)."""
    if cand["source"] == "f2m":
        post = await asyncio.wait_for(_f2m_post(cand["id"]), timeout)
        content = post.get("content", {}).get("rendered", "")
        return {
            "tt_ids": _f2m_tt_ids(content),
            "content": content,
            "movie_links": _f2m_direct_links(content),
            "ep_links": [],
            "referer": f"{_F2M_BASE}/",
        }
    if cand["source"] == "doost":
        pid = cand.get("id") or await asyncio.wait_for(_doost_postid_from_url(cand["url"]), timeout)
        if not pid:
            return None
        post = await asyncio.wait_for(_doost_post(pid), timeout)
        content = post.get("content", {}).get("rendered", "")
        return {
            "tt_ids": _f2m_tt_ids(content),
            "content": content,
            "movie_links": _doost_direct_links(content),
            "ep_links": [],
            "referer": f"{_DOOST_BASE}/",
        }
    return None


async def probe_iran_sources(
    imdb_id: str,
    names: List[str],
    year: Optional[int],
    is_series: bool,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    timeout: float = 12.0,
    lazy: bool = False,
) -> List[dict]:
    """
    🆕 z20 — سرچ همزمان منابع ایرانی برای اسم‌ها → انتخاب بهترین پست (tt دقیق
    اولویت مطلق) → لینک‌های مستقیم کیفیت/قسمت → entry های هم‌شکل سرورهای CDN.
    🆕 z21 — سرور FJ (tdmmo.xyz، اکانت‌دار + کپچای ریاضی) همزمان با زنجیره‌ی بالا
    پروب می‌شه (تسک موازی — صفر تاخیر اضافه) و entry های خودش رو با برچسب FJ 🇮🇷 اضافه می‌کنه.
    🆕 z24 — lazy=True: FJ بدون resolve اولیه جواب می‌ده (برای اینلاین — سریع؛
    resolve فقط موقع کلیک کاربر انجام می‌شه).
    """
    if not names:
        return []
    names = [n for n in names if n and len(str(n).strip()) >= 2][:4]

    # 🆕 z21: FJ از همین لحظه به‌صورت موازی شروع می‌شه — با فلوی legacy تداخل زمانی نداره
    tdmmo_task = asyncio.create_task(_probe_tdmmo_safe(
        imdb_id, names, year, is_series, season, episode, timeout, lazy))

    legacy = []
    try:
        legacy = await _probe_f2m_doost_farsi(
            imdb_id, names, year, is_series, season, episode, timeout)
    except Exception as e:
        logger.warning("[IranHub] legacy iran probe failed: %s", e)

    try:
        # 🆕 z24 — wait به‌جای wait_for: تایم‌اوت تسک FJ رو «نمی‌کشه» — بک‌گراند
        # ادامه می‌ده و کش سرچ/صفحه/لاگین tdmmo رو گرم می‌کنه (کوئری بعدی فوریه).
        # لاگین FJ بار اول ~۵-۱۰ ثانیه می‌بره؛ بعدش با سشن keep-alive فقط ۲-۴ ثانیه.
        _done, _pending = await asyncio.wait({tdmmo_task}, timeout=max(8.0, timeout))
        if tdmmo_task in _done and not tdmmo_task.exception():
            fj_entries = tdmmo_task.result() or []
    except Exception as e:
        logger.warning("[IranHub] FJ probe dropped: %s", e)
        fj_entries = []
    if fj_entries:
        logger.info("[IranHub] %s: FJ 🇮🇷 joined with %d quality row(s)",
                    imdb_id, sum(len(e.get("qualities") or []) for e in fj_entries))
    return list(legacy or []) + list(fj_entries or [])


async def _probe_tdmmo_safe(
    imdb_id: str,
    names: List[str],
    year: Optional[int],
    is_series: bool,
    season: Optional[int],
    episode: Optional[int],
    timeout: float,
    lazy: bool = False,
) -> List[dict]:
    """پروب FJ با عایق خطا — هرگز exception به بالا نمی‌ده."""
    try:
        from searcher.iranserver.tdmmo import probe_tdmmo
        return await probe_tdmmo(imdb_id, names, year, is_series,
                                 season=season, episode=episode, timeout=timeout,
                                 lazy=lazy)
    except Exception as e:
        logger.warning("[IranHub] FJ probe failed: %s", e)
        return []


async def _probe_f2m_doost_farsi(
    imdb_id: str,
    names: List[str],
    year: Optional[int],
    is_series: bool,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    timeout: float = 12.0,
) -> List[dict]:
    """زنجیره‌ی z20 (فیلم‌تو‌مووی/دوستی‌ها/فارسی‌لند) — بدون تغییر منطق."""

    # ۱) سرچ موازی همه‌ی اسم‌ها در همه‌ی منابع
    search_jobs = []
    for nm in names:
        search_jobs.append(_f2m_search(nm, limit=8))
        search_jobs.append(_doost_search(nm, limit=6))
    search_jobs.append(_farsi_search(names[0], limit=6))
    try:
        batches = await asyncio.wait_for(asyncio.gather(*search_jobs, return_exceptions=True), timeout)
    except Exception as e:
        logger.warning("[IranHub] search timeout for %s: %s", imdb_id, e)
        return []

    cands: Dict[str, dict] = {}
    for batch in batches:
        if isinstance(batch, Exception):
            continue
        for c in batch or []:
            key = c["url"] or f"{c['source']}:{c['id']}"
            if key in cands:
                old = cands[key]
                old["score"] = max(old.get("score", 0), score_candidate(c, names, year))
            else:
                c = dict(c)
                c["score"] = score_candidate(c, names, year)
                cands[key] = c
    if not cands:
        logger.info("[IranHub] %s: no candidate in iran sources", imdb_id)
        return []
    ranked = sorted(cands.values(), key=lambda c: -c["score"])
    # فقط کاندیداهای امتیاز مثبت — بقیه احتمالاً عنوان غلطه
    ranked = [c for c in ranked if c["score"] > 4][:5]
    if not ranked:
        logger.info("[IranHub] %s: candidates all low-score → skip", imdb_id)
        return []

    # ۲) تفکیک پست با tt دقیق:
    #    🆕 حیاتی (قورباغه!): کاندیدایی که tt داخل محتوش معلومه و با tt هدف
    #    فرق داره → قطعاً فیلم/سریال دیگه‌ست (مثل The Frog کره‌ای tt26767508)
    #    → کامل رد می‌شه حتی اگه اسمش یکسان بود.
    target = f"tt{re.sub(r'[^0-9]', '', imdb_id)}" if imdb_id else ""
    details = await asyncio.gather(
        *[_resolve_candidate(c, season, episode, timeout) for c in ranked],
        return_exceptions=True,
    )
    chosen_idx, chosen, chosen_tt = -1, None, False
    for i, det in enumerate(details):
        if isinstance(det, Exception) or not det:
            continue
        tids = det.get("tt_ids") or set()
        if target and target in tids:
            chosen_idx, chosen, chosen_tt = i, ranked[i], True
            break
        if target and tids and target not in tids:
            # tt معلوم ولی مخالف → این کاندیدا عنوان دیگه‌ایه — رد
            logger.info("[IranHub] %s: reject «%s» (tt=%s ≠ target)",
                        imdb_id, ranked[i]["title"][:40], sorted(tids)[:2])
            ranked[i]["rejected"] = True
            continue
    if not chosen:
        # بدون tt: با امتیاز (سال + اسم) — کاندیداهای tt-مخالف رد شدن
        for i, det in enumerate(details):
            if isinstance(det, Exception) or not det:
                continue
            if ranked[i].get("rejected"):
                continue
            chosen_idx, chosen = i, ranked[i]
            break
    if not chosen:
        logger.info("[IranHub] %s: candidate details failed", imdb_id)
        return []

    det = details[chosen_idx]
    logger.info("[IranHub] %s → «%s» (%s) tt_exact=%s score=%.1f",
                imdb_id, chosen["title"][:40], chosen["source"], chosen_tt, chosen["score"])

    # ۳) لینک‌های مستقیم: فیلم یا قسمت
    meta = {"post_url": chosen["url"], "post_title": chosen["title"],
            "source": chosen["source"], "referer": det.get("referer", "")}
    if is_series and season and episode:
        # 🆕 چند-فصلی: پست‌های film2movie فصل‌ها رو با هدینگ «فصل اول/دوم» جدا می‌کنن؛
        # توی متن‌های بین دو هدینگ، «قسمت N» رو نسبت به فصلِ هدینگ مچ می‌کنیم
        ep_rows = _f2m_episode_links_seasoned(det["content"], season, episode)
        if not ep_rows:
            # doostihaa: SxxEyy در URL
            for href in re.findall(r'href="(https?://[^"]+)"', det["content"]):
                href = href.replace("&amp;", "&")
                m = re.search(r"[sS](\d{1,2})[\s._-]*[eE][p]?(\d{1,2})", fa_digits(href))
                if m and int(m.group(1)) == season and int(m.group(2)) == episode:
                    if not _DL_HOST_BAD.search(href) and re.search(r"\.(mp4|mkv|avi)(\?|$)", href, re.IGNORECASE):
                        ep_rows.append({"url": href, "label": detect_quality(href),
                                        "episode": episode, "season": season})
        entry = _mk_entry(_SERVER_LABELS.get(chosen["source"], chosen["source"]), ep_rows, meta)
        return [entry] if entry else []

    rows = [{"url": u, "label": detect_quality(u)} for u in det.get("movie_links", [])]
    rows = [r for r in rows if r["label"]]
    entry = _mk_entry(_SERVER_LABELS.get(chosen["source"], chosen["source"]), rows, meta)
    return [entry] if entry else []


_SERVER_LABELS = {"f2m": "Film2Movie", "doost": "دوستی‌ها", "farsi": "فارسی‌لند"}


# ─── Quick test ─────────────────────────────────────────────

async def _test():
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    cases = [
        ("tt9817070", ["Law of Tehran", "متری شیش و نیم"], 2019, False, None, None),
        ("tt12920838", ["The Frog", "قورباغه"], 2020, True, 1, 8),
        ("tt19653180", ["Leila's Brothers", "برادران لیلا"], 2022, False, None, None),
    ]
    for imdb_id, names, year, is_series, s, e in cases:
        print(f"\n══ {imdb_id} {names} ══")
        entries = await probe_iran_sources(imdb_id, names, year, is_series, s, e)
        for ent in entries:
            print(f"  🇮🇷 {ent['server']}:")
            for q in ent["qualities"][:8]:
                print(f"    {q['label']} → {q['url'][:95]}")
        if not entries:
            print("  (چیزی پیدا نشد)")


if __name__ == "__main__":
    asyncio.run(_test())
