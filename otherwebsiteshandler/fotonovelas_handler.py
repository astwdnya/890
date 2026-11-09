r"""
fotonovelas_handler.py
──────────────────────
هندلر دانلود فوتونولا (کمیک عکسی) از fotonovelasxxx.com.

ساختار سایت (مهندسی معکوس):
  - صفحه کمیک انگلیسی:   https://fotonovelasxxx.com/english/<slug>/
    عکس‌ها: https://fotonovelasxxx.com/english/img/<folder>/NN.jpg
    (شماره‌گذاری zero-padded، lazy-load: data-lazy-src)
  - صفحه کمیک اسپانیایی: https://fotonovelasxxx.com/<slug>/   (بدون prefix — در root)
    عکس‌ها: https://fotonovelasxxx.com/img3/<folder>/NN.webp   (بدون lazy-load، webp)
    نکته: نام <folder> لزوماً برابر slug نیست
    (مثال: slug=cornudo-al-telefono → folder=cornudo-telefono)
  - عنوان کمیک: <h1 class="entry-title"> یا og:title
  - thumbnailهای related posts در wp-content هستن → با الگوی /img\d*/ خودکار حذف می‌شن

زنجیره محافظت سایت (reverse-engineered):
  1. دامنه‌ی اصلی (apex) پشت Cloudflare هست → چالش Turnstile («Just a moment...»)
     که برنامه‌نویسی قابل حل نیست.
  2. زیردامنه‌ی www مستقیم به سرور origin (BananaHosting/openresty) اشاره می‌کنه،
     نه Cloudflare → ولی روی origin یه interstitial دیگه هست: «One moment, please...»
     (DDoS-Guard) که با JS قابل شبیه‌سازیه.
  3. اگه از www بری، WordPress بعد از پاس چالش ۳۰۱ می‌ده به apex (canonical) و
     دوباره گیر Cloudflare.

استراتژی bypass (کاملاً برنامه‌نویسی‌شده):
  1. DNSresolve کردنه www.fotonovelasxxx.com → IP سرور origin (دور زدن Cloudflare)
  2. اتصال به IP اوریجین با Host/SNI = fotonovelasxxx.com (جلوگیری از ریدایرکت www→apex)
     با curl_cffi RESOLVE
  3. حل چالش DDoS-Guard:
     - GET صفحه → HTML چالش «One moment, please...»
     - استخراج ts (timestamp) و pdata و مسیر چالش از HTML
     - محاسبه‌ی wsidchk: دو عبارت JSFuck (Z و q) که با شبیه‌سازی eval محاسبه می‌شن
       (عبارت‌ها از +!+[] و !![] ساخته می‌شن = شمارش تعداد «۱»ها در هر گروه)
     - GET <مسیر چالش>?wsidchk=<عدد>&pdata=<url>&id=<هش>&ts=<ts>&cttl=0
       → 302 + کوکی wssplashchk
     - دوباره GET صفحه → محتوای واقعی
     نکته: پارامتر id اعتبارسنجی نمی‌شه، ولی wsidchk و ts حتماً باید درست باشن.
     کوکی wssplashchk فقط تو همون session معتبره (بین session‌ها منتقل نمی‌شه).

API:
  - is_fotonovelas_url(url): تشخیص صفحه‌ی فوتونولا (english / spanish / root)
  - extract_fotonovelas_info(url): {title, images, language, slug, url, ...}
  - download_fotonovelas_images(images, out_dir, ...): دانلود موازی عکس‌ها
  - build_fotonovelas_pdf(images, out_path, ...): دانلود + ساخت PDF (PIL sequential)
  - build_fotonovelas_zip(images, out_path, ...): دانلود + ساخت ZIP (STORED)
  - fotonovelas_sessions: state برای bot.py

خروجی برای کاربر (طبق الگوی هندلر کمیک‌ها):
  سوال: «عکس‌های جداگانه می‌خوای یا PDF؟»
  - عکس جداگانه → ارسال مستقیم عکس‌ها به‌صورت عکس تلگرام
    (آلبوم‌های ۱۰ تایی در bot.py — fnimg_؛ webp→JPEG چون تلگرام webp رو عکس نمایش نمی‌ده)
  - ZIP → همه‌ی عکس‌ها در یه فایل ZIP (fnzip_)
  - PDF → یه فایل PDF ترتیبی (fnpdf_)
"""

import asyncio
import html as html_lib
import logging
import os
import re
import shutil
import socket
import tempfile
import time
from typing import Callable, List, Optional
from urllib.parse import urlparse

from ._common import check_impersonation_support

logger = logging.getLogger("FotonovelasHandler")

# ─── Site config ────────────────────────────────────────────

DISPLAY_NAME = "FotonovelasXXX"
DOMAIN = "fotonovelasxxx.com"
BASE_URL = "https://fotonovelasxxx.com"
HOMEPAGE = "https://fotonovelasxxx.com/"

# الگوی URL صفحه‌ی فوتونولا:
#   - انگلیسی:  /english/<slug>/  (عکس‌ها: /english/img/<folder>/NN.jpg)
#   - اسپانیایی: /<slug>/         (عکس‌ها: /img3/<folder>/NN.webp — بدون prefix)
# صفحات لیست/دسته‌بندی (page/category/tag/author/feed) قبول نمی‌شن.
_URL_PREFIXED_RE = re.compile(
    r"^https?://(?:www\.|m\.)?fotonovelasxxx\.com/(english|spanish)/([a-z0-9\-]+)/?(?:\?.*)?(?:#.*)?$",
    re.I,
)
_URL_ROOT_RE = re.compile(
    r"^https?://(?:www\.|m\.)?fotonovelasxxx\.com/([a-z0-9\-]+)/?(?:\?.*)?(?:#.*)?$",
    re.I,
)
_EXCLUDE_SLUGS = frozenset({
    "page", "category", "tag", "author", "feed", "search", "img", "img3",
    "wp-content", "wp-login", "wp-admin", "wp-json", "comments", "xmlrpc.php",
    "english", "spanish", "en", "es", "it", "de", "fr", "pt", "ru",
    "contact", "dmca", "about", "privacy", "terms", "sitemap", "sitemap.xml",
})

# عکس‌های محتوای کمیک:
#   - انگلیسی:  /english/img/<folder>/NN.jpg  (lazy-load: data-lazy-src)
#   - اسپانیایی: /img3/<folder>/NN.webp        (بدون lazy-load: src)
# نام folder لزوماً برابر slug نیست (مثلاً slug=cornudo-al-telefono → folder=cornudo-telefono)
_IMG_URL_RE = re.compile(
    r"(?:data-lazy-src|data-src|src)\s*=\s*[\"']([^\"']*?/img\d*/[a-z0-9\-]+/\d+\.(?:jpe?g|png|webp|gif))[\"']",
    re.I,
)

# state برای bot.py (مشابه comic_sessions)
fotonovelas_sessions: dict = {}


# ─── URL detection ──────────────────────────────────────────


def is_fotonovelas_url(url: str) -> bool:
    """تشخیص این که URL صفحه‌ی فوتونولا هست یا نه.

    URLهای پشتیبانی‌شده:
      - https://fotonovelasxxx.com/english/<slug>/   (بخش انگلیسی)
      - https://fotonovelasxxx.com/spanish/<slug>/    (اگه وجود داشته باشه)
      - https://fotonovelasxxx.com/<slug>/            (بخش اسپانیایی — root)
      - با/بدون www و با/بدون slash انتهایی
    صفحات لیست/دسته‌بندی/مدیریتی رد می‌شن.
    """
    if not url:
        return False
    u = url.strip()
    m = _URL_PREFIXED_RE.match(u)
    if m:
        if m.group(2).lower() in _EXCLUDE_SLUGS:
            return False
        return True
    m = _URL_ROOT_RE.match(u)
    if m:
        if m.group(1).lower() in _EXCLUDE_SLUGS:
            return False
        return True
    return False


# ─── Origin discovery (Cloudflare bypass) ───────────────────

# کش IP سرور origin (TTL یک‌ساعته)
_origin_ip_cache: dict = {"ip": None, "ts": 0.0}
_ORIGIN_IP_TTL = 3600.0


def _resolve_origin_ip() -> Optional[str]:
    """
    پیدا کردن IP سرور origin از طریق DNS رزولو www.

    رکورد www مستقیماً به سرور هاستینگ (origin) اشاره می‌کنه، نه Cloudflare.
    با این کار کل لایه‌ی Cloudflare دور می‌زده.
    """
    now = time.time()
    if _origin_ip_cache["ip"] and now - _origin_ip_cache["ts"] < _ORIGIN_IP_TTL:
        return _origin_ip_cache["ip"]
    try:
        infos = socket.getaddrinfo(f"www.{DOMAIN}", 443, proto=socket.IPPROTO_TCP)
        for inf in infos:
            ip = inf[4][0]
            # IPv4 رو ترجیح می‌دیم (RESOLVE با v4 پایدارتره)
            if ":" not in ip:
                _origin_ip_cache["ip"] = ip
                _origin_ip_cache["ts"] = now
                logger.info("[Foto] Origin IP resolved: %s", ip)
                return ip
        # اگه فقط v6 بود
        if infos:
            ip = infos[0][4][0]
            _origin_ip_cache["ip"] = ip
            _origin_ip_cache["ts"] = now
            return ip
    except Exception as e:
        logger.warning("[Foto] Origin DNS resolve failed: %s", e)
    return None


def _make_session(origin_ip: Optional[str] = None):
    """
    ساخت AsyncSession با browser impersonation.

    اگه origin_ip داده بشه، دامنه‌ی apex با RESOLVE به IP اوریجین مپ می‌شه
    (دور زدن Cloudflare). verify=False چون cert سرور اوریجین برای
    singleph-xxxx.banahosting.com صادر شده، نه دامنه‌ی سایت.
    """
    from curl_cffi import requests as cr
    from curl_cffi import CurlOpt

    opts = {}
    if origin_ip:
        opts[CurlOpt.RESOLVE] = [f"{DOMAIN}:443:{origin_ip}".encode()]
    return cr.AsyncSession(impersonate="chrome124", verify=False, curl_options=opts)


# ─── DDoS-Guard challenge solver ────────────────────────────


def _extract_jsfuck_expr(chal: str, var: str) -> str:
    """استخراج عبارت JSFuck متعادل‌شده با پرانتز (var=+((...)) )."""
    marker = var + "=+(("
    i = chal.find(marker)
    if i < 0:
        return ""
    j = i + len(var) + 1  # خمیر اول '+'
    depth, k = 0, j
    while k < len(chal):
        c = chal[k]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                break
        k += 1
    return chal[j : k + 1]


def _eval_jsfuck_number(expr: str) -> int:
    """
    محاسبه‌ی عدد از عبارت JSFuck.

    عبارت‌ها از واحدهای «۱» ساخته شدن:
      +!+[] → 1   و   !![] → 1
    هر گروه (+!+[]+!![]+...) مجموع تعداد واحدهاست.
    گروه‌هایی که آخرشون +[] دارن به string تبدیل می‌شن،
    و جمع نهایی یعنی concat رشته‌ای، بعد تبدیل به عدد با +().
    مثال: (6)+("5")+(1)+("2")+(9)+("9")+(7) → 6512997
    """
    body = expr.strip()
    if not body.startswith("+"):
        raise ValueError("bad jsfuck expr")
    body = body[1:]           # strip leading '+'
    body = body[1:-1]         # پرانتز بیرونی
    groups, depth, cur = [], 0, ""
    for c in body:
        if c == "(" and depth == 0:
            depth, cur = 1, ""
        elif c == ")" and depth == 1:
            depth = 0
            groups.append(cur)
        elif depth == 1:
            cur += c
    if not groups:
        raise ValueError("no jsfuck groups")
    parts = []
    for g in groups:
        units = len(re.findall(r"\+!\+\[\]|\!\!\[\]", g))
        parts.append(str(units))
    return int("".join(parts))


def _parse_ddos_guard_challenge(html: str) -> Optional[dict]:
    """
    پارس چالش DDoS-Guard («One moment, please...»).

    مقادیر مورد نیاز سرور:
      - wsidchk: عدد JSFuck (اعتبارسنجی می‌شه — حتماً درست)
      - ts:      timestamp داخل صفحه (اعتبارسنجی می‌شه)
      - pdata:   URL صفحه (encode شده)
      - مسیر چالش (مثل /z0f76a1d14fd21a8fb5fd0d03e0fdc3d3cedae52f)
      - id:      هش (اعتبارسنجی نمی‌شه — هر مقداری قبول می‌شه)
    """
    try:
        scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
        if not scripts:
            return None
        # چالش همیشه آخرین اسکریپت صفحه‌ست
        chal = scripts[-1]
        ts_m = re.search(r"'ts','(\d+)'", chal)
        pdata_m = re.search(r"T='([^']+)'", chal)
        path_m = re.search(r"N='(/[^']+)'", chal)
        if not (ts_m and pdata_m and path_m):
            return None
        z = _eval_jsfuck_number(_extract_jsfuck_expr(chal, "Z"))
        q = _eval_jsfuck_number(_extract_jsfuck_expr(chal, "q"))
        return {
            "ts": ts_m.group(1),
            "pdata": pdata_m.group(1),
            "path": path_m.group(1),
            "wsidchk": z + q,
            # هش ثابت (سرور اعتبارسنجی‌ش نمی‌کنه)
            "id": "7fa3b767c460b54a2be4d49030b349c7",
        }
    except Exception as e:
        logger.debug("[Foto] challenge parse error: %s", e)
        return None


def _is_challenge(html: str) -> bool:
    """تشخیص این که HTML صفحه‌ی چالش DDoS-Guard هست یا نه."""
    if not html or len(html) > 20000:
        return False
    if "One moment, please" not in html:
        return False
    return True


def _normalize_url(url: str) -> str:
    """www → apex تا همه‌ی درخواست‌ها روی host رزولوشده بمونن."""
    return re.sub(r"^(https?://)(?:www\.|m\.)?" + re.escape(DOMAIN), r"\1" + DOMAIN, url, flags=re.I)


async def _fetch_with_guard(session, url: str, referer: Optional[str] = None,
                            max_attempts: int = 3) -> "object":
    """
    GET یک URL با حل خودکار چالش DDoS-Guard.

    جریان:
      GET → اگه چالش بود → پارس → GET مسیر clearance → GET دوباره
    ریدایرکت‌ها دستی دنبال می‌شن (با نرمال‌سازی www→apex).
    """
    from urllib.parse import urlencode

    url = _normalize_url(url)
    headers = {"Referer": referer} if referer else {}

    for attempt in range(max_attempts):
        r = await session.get(url, timeout=40, allow_redirects=False, headers=headers)

        # دنبال کردن ریدایرکت دستی
        hops = 0
        while r.status_code in (301, 302, 303, 307, 308) and hops < 5:
            loc = r.headers.get("location", "")
            if not loc:
                break
            if loc.startswith("/"):
                p = urlparse(url)
                loc = f"{p.scheme}://{p.netloc}{loc}"
            url = _normalize_url(loc)
            r = await session.get(url, timeout=40, allow_redirects=False, headers=headers)
            hops += 1

        if r.status_code == 200 and _is_challenge(r.text):
            params = _parse_ddos_guard_challenge(r.text)
            if not params:
                raise RuntimeError("DDoS-Guard challenge parse failed")
            qs = urlencode({
                "wsidchk": params["wsidchk"],
                "pdata": params["pdata"],
                "id": params["id"],
                "ts": params["ts"],
                "cttl": "0",
            })
            p = urlparse(url)
            clearance_url = f"{p.scheme}://{p.netloc}{params['path']}?{qs}"
            await session.get(clearance_url, timeout=40, allow_redirects=False,
                              headers={"Referer": url})
            continue  # دوباره صفحه رو بگیر

        return r

    raise RuntimeError("DDoS-Guard challenge could not be solved after retries")


# ─── Content extraction ─────────────────────────────────────


def _extract_title(html: str) -> str:
    """عنوان کمیک: h1.entry-title → og:title → <title>."""
    m = re.search(
        r"<h1[^>]*class=[\"'][^\"']*entry-title[^\"']*[\"'][^>]*>(.*?)</h1>",
        html, re.S | re.I,
    )
    if m:
        t = html_lib.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
        if t:
            return t
    m = re.search(
        r"<meta[^>]+property=[\"']og:title[\"'][^>]+content=[\"']([^\"']+)[\"']", html, re.I
    )
    if m:
        return html_lib.unescape(m.group(1).strip())
    m = re.search(r"<title>([^<]+)</title>", html, re.I)
    if m:
        t = html_lib.unescape(m.group(1).strip())
        t = re.sub(r"\s*[|\-]\s*(FotonovelasXXX|Comics Porno).*$", "", t, flags=re.I)
        return t.strip() or "Fotonovela"
    return "Fotonovela"


def _extract_images(html: str) -> List[str]:
    """
    استخراج عکس‌های صفحات کمیک (به ترتیب).

    فقط URLهایی که الگوی /img/<slug>/NN.<ext> دارن به عنوان محتوا
    در نظر گرفته می‌شن (thumbnailهای related posts تو wp-content هستن
    و خودکار حذف می‌شن). dedupe با حفظ ترتیب + sort عددی.
    """
    urls: List[str] = []
    seen = set()
    for m in _IMG_URL_RE.finditer(html):
        u = m.group(1)
        if u not in seen:
            seen.add(u)
            urls.append(u)

    def _num(u: str) -> int:
        mm = re.search(r"/(\d+)\.(?:jpe?g|png|webp|gif)$", u, re.I)
        return int(mm.group(1)) if mm else 0

    urls.sort(key=_num)
    return urls


async def extract_fotonovelas_info(url: str) -> Optional[dict]:
    """
    استخراج اطلاعات فوتونولا از صفحه.

    Args:
        url: URL صفحه کمیک (مثل https://fotonovelasxxx.com/english/<slug>/)

    Returns:
        {title, images, language, slug, url, display_name, num_pages}
        یا None اگه صفحه پیدا نشد / قابل دسترسی نبود.
    """
    if not check_impersonation_support():
        logger.error("[Foto] curl_cffi not installed")
        return None

    url = _normalize_url(url.strip())
    m = _URL_PREFIXED_RE.match(url)
    if m:
        language = m.group(1).lower()          # english / spanish
        slug = m.group(2).lower()
    else:
        m2 = _URL_ROOT_RE.match(url)
        if not m2 or m2.group(1).lower() in _EXCLUDE_SLUGS:
            logger.warning("[Foto] URL does not match comic page pattern: %s", url[:100])
            return None
        language = "spanish"                   # کمیک‌های اسپانیایی در root هستن
        slug = m2.group(1).lower()

    logger.info("[Foto] Extracting: %s", url[:100])

    # روش اصلی: اتصال مستقیم به origin (دور زدن Cloudflare) + حل DDoS-Guard
    html = None
    origin_ip = _resolve_origin_ip()
    if origin_ip:
        try:
            async with _make_session(origin_ip) as session:
                r = await _fetch_with_guard(session, url, referer=HOMEPAGE)
                if r.status_code == 200 and not _is_challenge(r.text):
                    html = r.text
        except Exception as e:
            logger.warning("[Foto] origin-direct fetch failed: %s", e)

    # fallback: fetch معمولی (اگه IP سرور ربات توسط Cloudflare چالش نشه)
    if not html:
        try:
            async with _make_session(None) as session:
                r = await _fetch_with_guard(session, url, referer=HOMEPAGE)
                if r.status_code == 200 and not _is_challenge(r.text):
                    html = r.text
        except Exception as e:
            logger.warning("[Foto] direct fetch failed: %s", e)

    if not html:
        logger.error("[Foto] Could not fetch page (Cloudflare/DDoS-Guard)")
        return None

    # صفحه 404؟
    if re.search(r"(Página no encontrada|Page not found)", html, re.I) and "<h1" in html:
        # 404 WordPress عنوانش اینه — ولی عنوان واقعی کمیک هم h1 داره؛
        # بررسی دقیق‌تر: اگه هیچ عکسی پیدا نشد و متن 404 هست → ناموجود
        images = _extract_images(html)
        if not images:
            logger.warning("[Foto] 404 or empty page: %s", url[:80])
            return None

    images = _extract_images(html)
    if not images:
        logger.warning("[Foto] no images found on page")
        return None

    title = _extract_title(html)
    logger.info("[Foto] OK: %s | %d images | title=%s", slug, len(images), title[:50])

    return {
        "title": title,
        "images": images,
        "language": "english" if language == "english" else "spanish",
        "slug": slug,
        "url": url,
        "display_name": DISPLAY_NAME,
        "num_pages": len(images),
    }


# ─── Download ───────────────────────────────────────────────


async def download_fotonovelas_images(
    images: List[str],
    out_dir: str,
    progress_cb=None,
    page_url: Optional[str] = None,
    max_concurrent: int = 6,
) -> List[str]:
    """
    دانلود موازی عکس‌های فوتونولا.

    Args:
        images: لیست URL عکس‌ها (از extract_fotonovelas_info)
        out_dir: پوشه خروجی
        progress_cb: callback پیشرفت (done, total)
        page_url: URL صفحه کمیک (برای گرفتن کوکی clearance قبل از دانلود)
        max_concurrent: تعداد دانلود موازی

    Returns:
        لیست مسیر فایل‌های دانلودشده (به ترتیب صفحه).
    """
    if not images:
        return []

    os.makedirs(out_dir, exist_ok=True)
    origin_ip = _resolve_origin_ip()

    async def _report(done: int, total: int):
        if progress_cb:
            try:
                res = progress_cb(done, total)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                pass

    async def _download_all():
        paths: List[str] = []
        sem = asyncio.Semaphore(max_concurrent)
        done_count = 0
        lock = asyncio.Lock()

        async def _dl(idx: int, img_url: str):
            nonlocal done_count
            async with sem:
                # نام فایل: NN.ext از خود URL (ترتیب حفظ می‌شه)
                base = os.path.basename(urlparse(img_url).path) or f"{idx:02d}.jpg"
                # جلوگیری از تداخل نام
                name, ext = os.path.splitext(base)
                fpath = os.path.join(out_dir, f"{name}{ext or '.jpg'}")
                if os.path.exists(fpath):
                    fpath = os.path.join(out_dir, f"{idx:03d}_{base}")
                try:
                    r = await session.get(
                        _normalize_url(img_url), timeout=60,
                        headers={"Referer": page_url or HOMEPAGE},
                    )
                    if r.status_code == 200 and r.content:
                        ct = (r.headers.get("content-type") or "").lower()
                        if "text/html" in ct:
                            # صفحه چالش به جای عکس — گزارش خطا
                            logger.warning("[Foto] got HTML instead of image: %s", base)
                            return
                        with open(fpath, "wb") as f:
                            f.write(r.content)
                        async with lock:
                            paths.append((idx, fpath))
                            done_count += 1
                            await _report(done_count, len(images))
                except Exception as e:
                    logger.warning("[Foto] image download error (%s): %s", base, e)

        tasks = [asyncio.create_task(_dl(i, u)) for i, u in enumerate(images)]
        await asyncio.gather(*tasks)
        # مرتب‌سازی بر اساس index (ترتیب صفحه)
        paths.sort(key=lambda x: x[0])
        return [p for _, p in paths]

    # سشن با اتصال origin-direct + حل چالش روی صفحه کمیک (برای کوکی)
    async with _make_session(origin_ip) as session:
        if page_url:
            try:
                await _fetch_with_guard(session, _normalize_url(page_url), referer=HOMEPAGE)
            except Exception as e:
                logger.debug("[Foto] pre-fetch page for cookie failed: %s", e)
        result = await _download_all()

    # fallback بدون origin-direct
    if not result:
        async with _make_session(None) as session:
            if page_url:
                try:
                    await _fetch_with_guard(session, _normalize_url(page_url), referer=HOMEPAGE)
                except Exception:
                    pass
            result = await _download_all()

    return result


# ─── PDF build ──────────────────────────────────────────────


def _build_pdf_sequential(img_paths: List[str], out_path: str) -> Optional[str]:
    """ساخت PDF با PIL sequential append (کم‌حافظه — مشابه comics_handler)."""
    if not img_paths:
        return None
    from PIL import Image

    try:
        parent = os.path.dirname(out_path)
        if parent and not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)

        for i, p in enumerate(img_paths):
            try:
                img = Image.open(p)
                if img.mode != "RGB":
                    img = img.convert("RGB")
                if i == 0:
                    img.save(out_path, "PDF", resolution=96.0)
                else:
                    with open(out_path, "r+b") as f:
                        img.save(f, "PDF", append=True, resolution=96.0)
                img.close()
            except Exception as e:
                logger.warning("[Foto] PDF append failed for %s: %s", p, e)
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return out_path
    except Exception as e:
        logger.error("[Foto] PDF build failed: %s", e)
    return None


async def build_fotonovelas_pdf(
    images: List[str],
    out_path: str,
    progress_cb=None,
    page_url: Optional[str] = None,
) -> Optional[str]:
    """
    دانلود عکس‌ها و ساخت PDF ترتیبی.

    Args:
        images: لیست URL عکس‌ها
        out_path: مسیر PDF خروجی
        progress_cb: callback پیشرفت (done, total)
        page_url: URL صفحه کمیک (برای کوکی)

    Returns:
        مسیر PDF یا None.
    """
    out_dir = tempfile.mkdtemp(prefix="fotonovelas_")
    try:
        async def _prog(done, total):
            if progress_cb:
                try:
                    res = progress_cb(done, total)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:
                    pass

        if progress_cb:
            await _prog(0, len(images))

        img_paths = await download_fotonovelas_images(
            images, out_dir, progress_cb=progress_cb, page_url=page_url
        )
        if not img_paths:
            logger.error("[Foto] no images downloaded for PDF")
            return None

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _build_pdf_sequential, img_paths, out_path)
    finally:
        try:
            shutil.rmtree(out_dir, ignore_errors=True)
        except Exception:
            pass


# ─── ZIP build (برای گزینه‌ی «عکس‌های جداگانه») ─────────────


async def build_fotonovelas_zip(
    images: List[str],
    out_path: str,
    progress_cb=None,
    page_url: Optional[str] = None,
) -> Optional[str]:
    """
    دانلود عکس‌ها و ساخت ZIP (JPEG داخل ZIP دیگه فشرده نمی‌شه → STORED سریع).

    Returns:
        مسیر ZIP یا None.
    """
    out_dir = tempfile.mkdtemp(prefix="fotonovelas_zip_")
    try:
        img_paths = await download_fotonovelas_images(
            images, out_dir, progress_cb=progress_cb, page_url=page_url
        )
        if not img_paths:
            return None

        def _zip_sync():
            import zipfile
            with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_STORED) as zf:
                for p in img_paths:
                    zf.write(p, arcname=os.path.basename(p))
            return out_path if os.path.exists(out_path) else None

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _zip_sync)
    finally:
        try:
            shutil.rmtree(out_dir, ignore_errors=True)
        except Exception:
            pass


# ─── Self-test ──────────────────────────────────────────────


async def _self_test():
    """تست سریع — اجرا با: python -m otherwebsiteshandler.fotonovelas_handler"""
    test_url = "https://fotonovelasxxx.com/english/experimenting-with-my-sister/"
    print(f"is_fotonovelas_url({test_url}) = {is_fotonovelas_url(test_url)}")
    neg = "https://fotonovelasxxx.com/english/page/15"
    print(f"is_fotonovelas_url({neg}) = {is_fotonovelas_url(neg)} (must be False)")

    info = await extract_fotonovelas_info(test_url)
    if not info:
        print("❌ extract failed")
        return
    print(f"title: {info['title']}")
    print(f"language: {info['language']} | slug: {info['slug']}")
    print(f"images ({info['num_pages']}):")
    for u in info["images"][:3]:
        print(f"  {u}")
    print(f"  ... آخرین: {info['images'][-1]}")

    # تست دانلود ۲ عکس اول
    import tempfile
    d = tempfile.mkdtemp(prefix="foto_test_")
    paths = await download_fotonovelas_images(
        info["images"][:2], d, page_url=info["url"]
    )
    print(f"downloaded {len(paths)}/{2}:")
    for p in paths:
        print(f"  {p} ({os.path.getsize(p)} bytes)")


if __name__ == "__main__":
    asyncio.run(_self_test())
