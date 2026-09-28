"""
subtitlecat_subtitle.py
───────────────────────
گرفتن زیرنویس فارسی از subtitlecat.com — کاملاً ریورس‌شده (Task 19 / z19).

نحوه‌ی کار سایت (از مهندسی معکوس واقعیِ HTML و JS سایت):

  ۱. جستجو:  GET https://www.subtitlecat.com/index.php?search={query}
     · سرچ، substring روی «اسم نسخه‌ها» (release name) هست — نه متادیتای فیلم
     · جدول نتایج: هر <tr> شامل
         <a href="subs/{dir}/{name}.html">{release name}</a>  +  Size  +  Downloads  +  N languages
     · فیلتر زبان سمت سرور نداره (پارامتر language=fa نادیده گرفته می‌شه)

  ۲. صفحه‌ی جزئیات:  GET /subs/{dir}/{name}.html
     · برای هر زبان یک بلوک <div class="sub-single">:
       - ترجمه‌شده‌ی قبلی (کش‌شده):
           <a id="download_fa" href="/subs/{dir2}/{name}-fa.srt" class="green-link">Download</a>
         ← لینک مستقیم SRT بدون لاگین/کپچا
       - هنوز ترجمه نشده:
           <button id="fa" onclick="translate_from_server_folder('fa',
               '{name}-orig.srt', '/subs/{dir}/')">Translate</button>

  ۳. ترجمه‌ی on-demand — ریورس /js/translate.js (با RequireJS لود می‌شه):
     · GET {folder}{name}-orig.srt  → متن اصلی (SRT انگلیسی یا هر زبان مبدا)
     · خطوط «فقط عدد» و «تایم‌کد -->» بدون ترجمه رد می‌شن
     · بقیه‌ی خطوط به دسته‌های ~۵۰۰ کاراکتری (join با \n) تقسیم می‌شن؛
       تگ‌های <font> حذف و "&" → "and" می‌شه (دقیقاً مثل translate.js)
     · هر دسته:  GET https://translate.googleapis.com/translate_a/single
                 ?client=gtx&sl=auto&tl=fa&dt=t&q={batch}
       نتیجه = کنکات seg[0][0] روی resp[0]  |  زبان مبدا = resp[2]
     · اگه تعداد خطوطِ ترجمه با دسته‌ی اصلی نخوند → ترجمه‌ی خط‌به‌خط fallback
     · در انتها نتیجه با POST /upload_subtitles.php
       {filename: '{name}.srt', content, language: 'fa', orig_language} کش می‌شه
       (بدون احراز هویت!) و سایت لینک /subs/{id}/{name}-fa.srt می‌ده —
       ما هم POST می‌کنیم (best-effort) تا کش سایت برای بقیه گرم بمونه.

خروجی توابع مثل subf2m_subtitle هست:
  - search_subtitles(title, season, episode) -> List[dict]
  - list_menu_subtitles(...) -> List[dict]        (برای دکمه‌های منوی ربات)
  - download_persian_subtitle(detail_url, out_dir, ...) -> Optional[str]  (مسیر SRT UTF-8)
  - get_subtitle_for_imdb(imdb_id, title, ...) -> Optional[str]           (برای زنجیره‌ی خودکار)
"""

import asyncio
import html as _html
import logging
import os
import re
import time
from typing import List, Optional, Tuple

from curl_cffi.requests import AsyncSession

logger = logging.getLogger("SubtitleCat")

_BASE = "https://www.subtitlecat.com"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# 🆕 gtx گوگل از IP دیتاسنتر با کلاینت معمولی «Sorry...» می‌ده —
# با TLS fingerprint مرورگر (chrome131) تأیید شد که پاس ۲۰۰ سالم می‌ده
_IMPERSONATE = "chrome131"

_GTX_URL = "https://translate.googleapis.com/translate_a/single"

# ─── تنظیم‌های محیطی ─────────────────────────────────────────
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default

BUDGET_SECONDS = _env_int("SUBTITLECAT_BUDGET", 110)      # سقف کل ترجمه‌ی ماشینی
GTX_CONCURRENCY = _env_int("SUBTITLECAT_CONCURRENCY", 6)  # دسته‌های موازی gtx
BATCH_CHARS = _env_int("SUBTITLECAT_BATCH_CHARS", 500)    # همون chars_per_batch سایت
MAX_CANDIDATES = _env_int("SUBTITLECAT_MAX_CANDIDATES", 4)
SITE_TIMEOUT = _env_int("SUBTITLECAT_TIMEOUT", 20)        # per-request سایت
GTX_TIMEOUT = _env_int("SUBTITLECAT_GTX_TIMEOUT", 12)     # per-request گوگل
# 🛡 حداقل تعداد کیو (تایم‌کد -->) برای قبول fa کش‌شده —
# کش سایت ممکنه آلوده/ناقص باشه (مثلاً ترجمه‌ی نیمه‌کاره‌ی بازدیدکننده‌ی قبلی)
MIN_CUES = _env_int("SUBTITLECAT_MIN_CUES", 60)
# 🛡 حداقل تعداد کیو (تایم‌کد -->) برای قبول fa کش‌شده —
# کش سایت ممکنه آلوده/ناقص باشه (مثلاً ترجمه‌ی نیمه‌کاره‌ی بازدیدکننده‌ی قبلی)
MIN_CUES = _env_int("SUBTITLECAT_MIN_CUES", 60)

# ─── regex های ریورس‌شده ────────────────────────────────────
# خطوط عدد/تایم‌کد — عیناً از translate.js:
#   /^[0-9 \r]*$/  و  /^[0-9,: ]*-->[0-9,: \r]*$/
_NUM_LINE_RE = re.compile(r"^[0-9 \r]*$")
_TS_LINE_RE = re.compile(r"^[0-9,: ]*-->[0-9,: \r]*$")

_FONT_RE = re.compile(r"<font[^>]*>|</font>", re.I)

# ردیف جدول نتایج
_ROW_HREF_RE = re.compile(r'<a href="(subs/[^"\' ]+\.html)"[^>]*>(.*?)</a>', re.S)
_ROW_DL_RE = re.compile(r">([\d,]+)<span[^>]*>\s*downloads", re.I)
_ROW_LANG_RE = re.compile(r">([\d,]+)<span[^>]*>\s*languages", re.I)

# صفحه‌ی جزئیات — fa
_FA_DOWNLOAD_RE = re.compile(r'<a[^>]+id="download_fa"[^>]*href="([^"]+)"', re.I)
_FA_TRANSLATE_RE = re.compile(
    r"<button[^>]+id=\"fa\"[^>]*onclick=\"translate_from_server_folder\("
    r"'fa'\s*,\s*'([^']+)'\s*,\s*'([^']+)'\)", re.I)

# الگوهای فصل/قسمت در اسم نسخه
_SXXEYY_RE = re.compile(r"(?<![a-z0-9])s(\d{1,2})\s*e(\d{1,3})(?![0-9])", re.I)
_XEYY_RE = re.compile(r"(?<![a-z0-9])(\d{1,2})x(\d{1,3})(?![0-9])", re.I)

_PERSIAN_CHARS = re.compile(r"[\u0600-\u06FF]")


def _episode_pairs(name: str) -> set:
    """همه‌ی جفت‌های (فصل، قسمت) داخل اسم نسخه — s01e02 / 1x02."""
    pairs = set()
    for m in _SXXEYY_RE.finditer(name or ""):
        pairs.add((int(m.group(1)), int(m.group(2))))
    for m in _XEYY_RE.finditer(name or ""):
        pairs.add((int(m.group(1)), int(m.group(2))))
    return pairs


def _query_tokens(query: str) -> List[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (query or "").lower()) if len(t) >= 2]


# ═══════════════════════════════════════════════════════════
#   ۱) جستجو
# ═══════════════════════════════════════════════════════════

def _parse_search_rows(page_html: str, tokens: List[str]) -> List[dict]:
    """پارس جدول نتایج — فیلتر توکن + dedupe."""
    rows, seen = [], set()
    for chunk in re.split(r"<tr[^>]*>", page_html):
        m = _ROW_HREF_RE.search(chunk)
        if not m:
            continue
        href, name = m.group(1), _html.unescape(m.group(2)).strip()
        name = re.sub(r"\s+", " ", name)
        low = name.lower()
        if tokens and not all(t in low for t in tokens):
            continue  # نویزهای سرچ فازی
        url = f"{_BASE}/{href}" if not href.startswith("http") else href
        if url in seen:
            continue
        seen.add(url)
        dm = _ROW_DL_RE.search(chunk)
        lm = _ROW_LANG_RE.search(chunk)
        try:
            downloads = int(dm.group(1).replace(",", "")) if dm else 0
        except ValueError:
            downloads = 0
        try:
            languages = int(lm.group(1).replace(",", "")) if lm else 0
        except ValueError:
            languages = 0
        rows.append({"name": name, "url": url,
                     "downloads": downloads, "languages": languages})
    return rows


def _rank_rows(rows: List[dict], season: Optional[int],
               episode: Optional[int]) -> List[dict]:
    if season and episode:
        # فقط نسخه‌های همین قسمت دقیق — خط‌مشی z15 (ساب اشتباه ممنوع)
        exact = [r for r in rows
                 if (int(season), int(episode)) in _episode_pairs(r["name"])]
        exact.sort(key=lambda x: -x["downloads"])
        return exact
    rows.sort(key=lambda x: (-x["downloads"], -x["languages"]))
    return rows


async def search_subtitles(title: str, season: Optional[int] = None,
                           episode: Optional[int] = None,
                           timeout: int = 0) -> List[dict]:
    """جستجوی نسخه‌ها روی subtitlecat — خروجی رنک‌شده (مچ دقیق قسمت اول).

    هر آیتم: {'name', 'url' (صفحه‌ی جزئیات), 'downloads', 'languages'}
    """
    title = (title or "").strip()
    if not title:
        return []
    timeout = timeout or SITE_TIMEOUT

    queries = [title]
    if season and episode:
        queries = [
            f"{title} s{int(season):02d}e{int(episode):02d}",
            f"{title} s{int(season)}e{int(episode)}",
            f"{title} {int(season)}x{int(episode):02d}",
        ]

    merged: List[dict] = []
    seen_urls = set()
    try:
        async with AsyncSession() as s:
            for q in queries:
                tokens = _query_tokens(q)
                try:
                    r = await s.get(f"{_BASE}/index.php", params={"search": q},
                                    impersonate=_IMPERSONATE, timeout=timeout,
                                    headers={"User-Agent": _USER_AGENT,
                                             "Accept-Language": "en-US,en;q=0.9"})
                except Exception as e:
                    logger.warning("subtitlecat search '%s' failed: %s", q, e)
                    continue
                if r.status_code != 200:
                    logger.warning("subtitlecat search '%s' HTTP %d", q, r.status_code)
                    continue
                for row in _parse_search_rows(r.text, tokens):
                    if row["url"] in seen_urls:
                        continue
                    seen_urls.add(row["url"])
                    merged.append(row)
                if merged and season and episode:
                    break  # کوئری دقیق جواب داد — نیازی به کوئری‌های بعدی نیست
    except Exception as e:
        logger.error("subtitlecat search error: %s", e)
        return []

    ranked = _rank_rows(merged, season, episode)
    logger.info("subtitlecat: %d candidates for '%s'%s",
                len(ranked), title,
                f" S{season:02d}E{episode:02d}" if (season and episode) else "")
    return ranked


async def list_menu_subtitles(title: str, season: Optional[int] = None,
                              episode: Optional[int] = None,
                              max_items: int = 3) -> List[dict]:
    """لیست کوتاه برای دکمه‌های منوی ربات — بدون دانلود/ترجمه (فوری)."""
    rows = await search_subtitles(title, season, episode)
    return rows[:max_items]


# ═══════════════════════════════════════════════════════════
#   ۲) صفحه‌ی جزئیات → وضعیت fa
# ═══════════════════════════════════════════════════════════

async def _fetch_detail(detail_url: str, timeout: int = 0) -> Optional[str]:
    timeout = timeout or SITE_TIMEOUT
    try:
        async with AsyncSession() as s:
            r = await s.get(detail_url, impersonate=_IMPERSONATE, timeout=timeout,
                            headers={"User-Agent": _USER_AGENT,
                                     "Accept-Language": "en-US,en;q=0.9"})
            if r.status_code == 200:
                return r.text
            logger.warning("subtitlecat detail HTTP %d: %s", r.status_code, detail_url[:90])
    except Exception as e:
        logger.warning("subtitlecat detail error: %s", e)
    return None


def _parse_detail(detail_html: str, detail_url: str = "") -> dict:
    """از HTML جزئیات: یا لینک fa کش‌شده، یا پلن ترجمه (orig file + folder).

    اگه دکمه‌ی Translate نبود (چون fa کش شده)، orig رو از خود URL جزئیات
    می‌سازیم: /subs/{dir}/{name}.html → /subs/{dir}/{name}-orig.srt
    (همون الگویی که translate_from_server_folder صدا می‌زنه)
    """
    out = {"cached_url": None, "orig_file": None, "orig_dir": None,
           "orig_url": None}
    m = _FA_DOWNLOAD_RE.search(detail_html)
    if m and m.group(1):
        out["cached_url"] = m.group(1)
        if not out["cached_url"].startswith("http"):
            out["cached_url"] = _BASE + out["cached_url"]
    t = _FA_TRANSLATE_RE.search(detail_html)
    if t:
        out["orig_file"] = t.group(1)
        out["orig_dir"] = t.group(2)
        from urllib.parse import urljoin
        out["orig_url"] = urljoin(_BASE + "/", out["orig_dir"] + out["orig_file"])
    elif detail_url:
        # fallback: ساخت orig از الگوی URL جزئیات
        try:
            from urllib.parse import urlsplit, urlunsplit
            parts = urlsplit(detail_url)
            path = parts.path or ""
            if path.endswith(".html") and "/subs/" in path:
                orig_path = path[:-5] + "-orig.srt"
                out["orig_dir"] = path[:path.rfind("/") + 1]
                out["orig_file"] = path[path.rfind("/") + 1:-5] + "-orig.srt"
                out["orig_url"] = urlunsplit((parts.scheme, parts.netloc, orig_path, "", ""))
        except Exception:
            pass
    return out


def _srt_looks_complete(text: str) -> bool:
    """🛡 sanity ساختاری — فایل کش‌شده نباید ناقص/آلوده باشه.

    حداقل MIN_CUES تایم‌کد '-->' + متن قابل‌قبول. فایل‌های ترجمه‌شده‌ی
    واقعی (فیلم/قسمت) معمولاً ۳۰۰ تا ۱۵۰۰ کیو دارن؛ فایل‌های نیمه‌کاره
    (مثل ترجمه‌ی to‌شده‌ی بازدیدکننده‌ی قبلی) کم‌کیو و کوتاه‌اند.
    """
    if not text or "-->" not in text:
        return False
    cues = len(re.findall(r"-->", text))
    return cues >= MIN_CUES


# ═══════════════════════════════════════════════════════════
#   ۳) ترجمه‌ی on-demand — پیاده‌سازی سمت سرورِ translate.js
# ═══════════════════════════════════════════════════════════

def _build_batches(text: str, chars_per_batch: int = 0):
    """دقیقاً الگوریتم translate_file در translate.js:
    خطوط عدد/تایم‌کد پاس‌-through، بقیه دسته‌بندی ~۵۰۰ کاراکتری.

    Returns: (lines, contents, batches, batch_line_index)
      contents[i] = None یعنی خط i باید ترجمه بشه
      batches[k] = متن دسته k؛ batch_line_index[k] = ایندکس خطوط دسته k
    """
    chars_per_batch = chars_per_batch or BATCH_CHARS
    lines = text.split("\n")
    contents: list = [None] * len(lines)
    batches: List[str] = []
    batch_idx: List[List[int]] = []
    cur, cur_chars, idxs = "", 0, []
    for i, ln in enumerate(lines):
        if _NUM_LINE_RE.match(ln) or _TS_LINE_RE.match(ln):
            contents[i] = ln
            continue
        clean = _FONT_RE.sub("", ln).replace("&", "and")
        if not cur:
            cur, cur_chars, idxs = clean, len(ln) + 1, [i]
        elif cur_chars + len(ln) + 1 < chars_per_batch:
            cur += "\n" + clean
            cur_chars += len(ln) + 1
            idxs.append(i)
        else:
            batches.append(cur)
            batch_idx.append(idxs)
            cur, cur_chars, idxs = clean, len(ln) + 1, [i]
    if cur:
        batches.append(cur)
        batch_idx.append(idxs)
    return lines, contents, batches, batch_idx


def _gtx_result_text(data) -> Tuple[Optional[str], Optional[str]]:
    """resp[0][i][0] ها را کنکات می‌کنه — عین result_to_string در translate.js."""
    try:
        parts = data[0] or []
        out = "".join(seg[0] for seg in parts if seg and seg[0] is not None)
        src = data[2] if len(data) > 2 else None
        return out, src
    except Exception:
        return None, None


async def _translate_srt_text(text: str,
                              sess: AsyncSession) -> Tuple[Optional[str], str]:
    """ترجمه‌ی کامل متن SRT به فارسی با gtx — موازی + بودجه‌ی زمانی + fallback.

    Returns: (متن SRT فارسی، زبان مبدا)  یا  (None، '') اگه کیفیت زیر حد بود.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines, contents, batches, batch_idx = _build_batches(text)
    total = len(batches)
    if not total:
        return None, ""

    deadline = time.monotonic() + BUDGET_SECONDS
    ok_batches = [0]
    src_lang = ["en"]
    sem = asyncio.Semaphore(max(1, GTX_CONCURRENCY))

    async def _gtx(q: str) -> Tuple[Optional[str], Optional[str]]:
        for attempt in range(3):
            if time.monotonic() > deadline:
                return None, None
            try:
                async with sem:
                    r = await sess.get(
                        _GTX_URL,
                        params={"client": "gtx", "sl": "auto", "tl": "fa",
                                "dt": "t", "q": q},
                        impersonate=_IMPERSONATE, timeout=GTX_TIMEOUT,
                        headers={"User-Agent": _USER_AGENT})
                if r.status_code == 200:
                    out, src = _gtx_result_text(r.json())
                    if out is not None:
                        return out, src
                elif r.status_code == 429:
                    await asyncio.sleep(2.0 + attempt * 2.0)
                    continue
                else:
                    await asyncio.sleep(0.8 + attempt)
            except Exception:
                await asyncio.sleep(0.8 + attempt)
        return None, None

    async def _do_batch(bi: int) -> None:
        out, src = await _gtx(batches[bi])
        if out is None:
            return  # خطوط این دسته انگلیسی می‌مونن (fallback نرم)
        if src:
            src_lang[0] = src
        tlines = out.split("\n")
        if len(tlines) == len(batch_idx[bi]):
            for k, li in enumerate(batch_idx[bi]):
                contents[li] = tlines[k]
            ok_batches[0] += 1
            return
        # تعداد خطوط نخوند → ترجمه‌ی خط‌به‌خط (مثل fallback خود سایت)
        olines = batches[bi].split("\n")
        results = await asyncio.gather(*[_gtx(ol) for ol in olines])
        ok_lines = 0
        for k, (tr, _s) in enumerate(results):
            if tr:
                # ترجمه‌ی یک خط نباید چندخطی بشه — فقط خط اول
                contents[batch_idx[bi][k]] = tr.split("\n")[0]
                ok_lines += 1
        if ok_lines >= max(1, len(olines) // 2):
            ok_batches[0] += 1

    try:
        await asyncio.gather(*[_do_batch(i) for i in range(total)])
    except Exception as e:
        logger.warning("subtitlecat translate gather error: %s", e)

    ratio = ok_batches[0] / max(1, total)
    logger.info("subtitlecat translate: %d/%d batches ok (%.0f%%)",
                ok_batches[0], total, ratio * 100)
    if ratio < 0.5:
        return None, ""  # بیشتر از نصف ترجمه نشد — ساب نیمه‌انگلیسی نمی‌دیم

    final = "\n".join(
        contents[i] if contents[i] is not None else lines[i]
        for i in range(len(lines)))
    # sanity: باید واقعاً فارسی توش باشه
    if len(_PERSIAN_CHARS.findall(final)) < 10:
        logger.warning("subtitlecat: translated text has no Persian chars")
        return None, ""
    return final, src_lang[0]


async def _fetch_orig_text(orig_url: str, sess: AsyncSession,
                           timeout: int = 0) -> Optional[str]:
    """متن SRT اصلی (decode هوشمند مثل بقیه‌ی منبع‌ها)."""
    try:
        r = await sess.get(orig_url, impersonate=_IMPERSONATE,
                           timeout=timeout or SITE_TIMEOUT,
                           headers={"User-Agent": _USER_AGENT})
        if r.status_code != 200:
            logger.warning("subtitlecat orig HTTP %d: %s", r.status_code, orig_url[:90])
            return None
        raw = r.content
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            for enc in ("windows-1256", "cp1256", "iso-8859-6", "latin-1"):
                try:
                    return raw.decode(enc)
                except (UnicodeDecodeError, LookupError):
                    continue
            return raw.decode("utf-8", errors="replace")
    except Exception as e:
        logger.warning("subtitlecat orig error: %s", e)
        return None


async def _warm_cache(filename: str, content: str, referer: str,
                      sess: AsyncSession, orig_lang: str) -> None:
    """POST /upload_subtitles.php — کش سایت رو گرم می‌کنه (بدون نیاز به لاگین).

    fail نرم: هر چیزی پیش بیاد فقط لاگ می‌شه؛ به نتیجه‌ی ترجمه ربطی نداره.
    """
    try:
        r = await sess.post(
            f"{_BASE}/upload_subtitles.php",
            data={"filename": filename, "content": content,
                  "language": "fa", "orig_language": orig_lang or "en"},
            impersonate=_IMPERSONATE, timeout=10,
            headers={"User-Agent": _USER_AGENT,
                     "Referer": referer,
                     "X-Requested-With": "XMLHttpRequest"})
        try:
            j = r.json()
            logger.info("subtitlecat cache warmed: %s", (j or {}).get("url", "?"))
        except Exception:
            pass
    except Exception as e:
        logger.debug("subtitlecat cache warm skipped: %s", e)


def _save_srt(text: str, out_dir: str, out_name: str) -> Optional[str]:
    try:
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"{out_name}.srt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path
    except Exception as e:
        logger.error("subtitlecat save error: %s", e)
        return None


# ═══════════════════════════════════════════════════════════
#   ۴) دانلود عمومی + wrapper زنجیره
# ═══════════════════════════════════════════════════════════

async def _download_cached_text(sess: AsyncSession, url: str,
                                timeout: int = 0) -> Optional[str]:
    """دانلود fa کش‌شده + decode + 🛡 sanity ساختاری.

    کش سایت ممکنه فایل ناقص داشته باشه (ترجمه‌ی نیمه‌کاره‌ی بازدیدکننده‌ی
    قبلی) — به همین خاطر علاوه بر کاراکتر فارسی، تعداد کیو هم چک می‌شه.
    """
    try:
        r = await sess.get(url, impersonate=_IMPERSONATE,
                           timeout=timeout or SITE_TIMEOUT,
                           headers={"User-Agent": _USER_AGENT})
        if r.status_code != 200 or not r.content:
            return None
        text = None
        try:
            text = r.content.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = r.content.decode("cp1256")
            except UnicodeDecodeError:
                return None
        if len(_PERSIAN_CHARS.findall(text)) < 10:
            return None
        if not _srt_looks_complete(text):
            logger.warning("subtitlecat: cached fa looks INCOMPLETE "
                           "(cues=%d) → %s", len(re.findall(r"-->", text)), url[:80])
            return None
        return text
    except Exception as e:
        logger.warning("subtitlecat cached download failed: %s", e)
        return None


async def _translate_plan(info: dict, detail_url: str, out_dir: str,
                          out_name: str, timeout: int = 0) -> Optional[str]:
    """پلن ترجمه‌ی on-demand برای یک صفحه‌ی جزئیات (orig → gtx → کش → ذخیره)."""
    orig_url = info.get("orig_url") or (
        (info.get("orig_dir") or "") + (info.get("orig_file") or ""))
    if not orig_url or not orig_url.startswith("http"):
        logger.info("subtitlecat: no translate plan for %s", detail_url[:80])
        return None
    async with AsyncSession() as sess:
        orig_text = await _fetch_orig_text(orig_url, sess, timeout)
        if not orig_text or "-->" not in orig_text:
            return None
        translated, src_lang = await _translate_srt_text(orig_text, sess)
        if not translated:
            return None
        # کش سایت رو گرم کن با نسخه‌ی کامل (اسم فایل عین translate.js)
        filename = re.sub(r"-orig\.srt$", ".srt", info.get("orig_file") or "")
        if filename:
            asyncio.create_task(_warm_cache(filename, translated, detail_url,
                                            sess, src_lang))
        return _save_srt(translated, out_dir, out_name)


async def download_persian_subtitle(detail_url: str, out_dir: str,
                                    out_name: str = "subtitlecat_fa",
                                    timeout: int = 0) -> Optional[str]:
    """دانلود زیرنویس فارسی از یک صفحه‌ی جزئیات subtitlecat.

    اول fa کش‌شده (با sanity ساختاری)؛ ناقص/نبود → ترجمه‌ی ماشینی on-demand.
    Returns: مسیر SRT UTF-8 یا None.
    """
    if not detail_url:
        return None
    detail_html = await _fetch_detail(detail_url, timeout)
    if not detail_html:
        return None
    info = _parse_detail(detail_html, detail_url)

    # ۱) fa کش‌شده — فوری
    if info.get("cached_url"):
        async with AsyncSession() as sess:
            text = await _download_cached_text(sess, info["cached_url"], timeout)
        if text:
            logger.info("subtitlecat: cached fa SRT from %s",
                        info["cached_url"][:80])
            return _save_srt(text, out_dir, out_name)

    # ۲) ترجمه‌ی on-demand (حتی وقتی کش هست ولی ناقصه)
    return await _translate_plan(info, detail_url, out_dir, out_name, timeout)


async def get_subtitle_for_imdb(imdb_id: str, title: str,
                                year: Optional[int] = None,
                                season: Optional[int] = None,
                                episode: Optional[int] = None,
                                out_dir: Optional[str] = None) -> Optional[str]:
    """wrapper زنجیره‌ی خودکار — اول fa کش‌شده بین کاندیداها، بعد ترجمه‌ی بهترین.

    برای سریال فقط نسخه‌های مچ دقیق همین SxxEyy (خط‌مشی z15).
    """
    if not title or title == "Unknown":
        return None
    if not out_dir:
        import tempfile
        out_dir = tempfile.mkdtemp(prefix="subtitlecat_")
    out_name = f"{imdb_id}_subtitlecat_persian"

    candidates = (await search_subtitles(title, season, episode))[:MAX_CANDIDATES]
    if not candidates:
        logger.info("subtitlecat: no candidates for '%s'", title)
        return None

    # ۱) بین کاندیداها دنبال fa کش‌شده‌ی سالم بگرد (هر کدوم ~۱ ثانیه)
    plans = []
    for cand in candidates:
        detail_html = await _fetch_detail(cand["url"])
        if not detail_html:
            continue
        info = _parse_detail(detail_html, cand["url"])
        if info.get("cached_url"):
            async with AsyncSession() as sess:
                text = await _download_cached_text(sess, info["cached_url"])
            if text:
                path = _save_srt(text, out_dir, out_name)
                if path:
                    logger.info("subtitlecat: cached fa for '%s' → %s",
                                cand["name"][:60], path)
                    return path
        if info.get("orig_url"):
            plans.append((cand, info))

    # ۲) هیچ‌جا کش سالم نبود → بهترین کاندیدا رو ترجمه‌ی ماشینی کن
    for cand, info in plans:
        path = await _translate_plan(info, cand["url"], out_dir, out_name)
        if path:
            logger.info("subtitlecat: machine-translated '%s' → %s",
                        cand["name"][:60], path)
            return path
    logger.info("subtitlecat: no Persian subtitle produced for '%s'", title)
    return None


# ═══════════════════════════════════════════════════════════
#   Test / debug
# ═══════════════════════════════════════════════════════════

async def _test():
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    print("\n=== Test 1: search movie (Inception) ===")
    rows = await search_subtitles("inception")
    for r in rows[:5]:
        print(f"  {r['name'][:55]:55s} | dl={r['downloads']:4d} | langs={r['languages']}")

    print("\n=== Test 2: search episode (Breaking Bad S01E01) ===")
    rows = await search_subtitles("breaking bad", season=1, episode=1)
    for r in rows[:5]:
        print(f"  {r['name'][:55]:55s} | dl={r['downloads']:4d}")

    if rows:
        print("\n=== Test 3: download/translate first candidate ===")
        path = await download_persian_subtitle(rows[0]["url"], "/tmp/test_scat",
                                               "bb_s01e01")
        if path:
            with open(path, encoding="utf-8") as f:
                content = f.read()
            persian = len(_PERSIAN_CHARS.findall(content))
            print(f"✅ saved: {path} | {len(content)} chars | persian chars: {persian}")
            print("sample:", content[:300].replace("\n", " ⏎ "))
        else:
            print("❌ download failed")

    print("\n=== Test 4: get_subtitle_for_imdb (movie chain wrapper) ===")
    path = await get_subtitle_for_imdb("tt1375666", "Inception", year=2010,
                                       out_dir="/tmp/test_scat_chain")
    print("✅", path) if path else print("❌ no subtitle")


if __name__ == "__main__":
    asyncio.run(_test())
