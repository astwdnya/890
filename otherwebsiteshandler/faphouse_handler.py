"""
faphouse_handler.py
──────────────────
هندلر دانلود ویدیو از faphouse.com — کاملاً HTTP، بدون مرورگر.

نتیجه‌ی مهندسی معکوس:
─────────────────────
ساختار داخلی سایت:
  ۱) Public URL:    https://faphouse.com/videos/{slug-with-title}-{xc_id}
                   (مثال: stepsister-nice-fuck-first-hV5npB → xc_id = hV5npB)
  ۲) صفحه‌ی ویدیو شامل یک تگ <video> با این attributeهاست:
       poster="https://ic-nss.flixcdn.com/.../xc/{first_2_chars}/{xc_id}/frame/original/12.jpg"
       data-fallback="https://video-nss.flixcdn.com/{sign},{expiry}/{first_2_chars}/{xc_id}/trailer/1080.mp4"
       data-av1-fallback="https://video-nss.flixcdn.com/{sign},{expiry}/{first_2_chars}/{xc_id}/trailer/1080.av1.mp4"
     نکته‌ی کلیدی: sign+expiry یک مهر زمان‌دار امضاشده‌ست که در page HTML نشون داده می‌شه
     و نیازی به login نداره. فقط trailer/ مسیر قابل دسترسبنه؛ full/ و گزینه‌های
     دیگر 403 برمی‌گردن (محتوای premium پشت paywall).
  ۳) URL pattern با همون sign/expiry/xc_id برای کیفیت‌های دیگر هم کار می‌کنه:
       trailer/240.mp4   ≈ 2-3 MB   (کوچک‌ترین)
       trailer/480.mp4   ≈ 4-5 MB
       trailer/720.mp4   ≈ 7-9 MB
       trailer/1080.mp4  ≈ 17-20 MB
     + AV1 variants:
       trailer/{q}.av1.mp4  (همون کیفیت، حجم کمتر، codec AV1)
  ۴) متادیتا: og:title, og:description, og:image, <title>, data-el-* attributes:
       data-el-video-id (FH internal numeric id, e.g., 4495077)
       data-el-duration (seconds, e.g., 908 for full video)
       data-el-content-poster (thumbnail URL)
       data-el-video-access-type ("premium" or "moment")
       data-el-content-price (price in rubies, e.g., 24)
  ۵) CDN از Range ساپورت می‌کنه → دانلود موازی تکه‌ای با سرعت بالا
     (همون موتور yt_direct / noodle_handler).
  ۶) سایت کاملاً premium-onlyه؛ هیچ ویدیوی رایگانی وجود نداره. trailerها
     ~30-60 ثانیه‌ان و به‌صورت پابلیک قابل دسترسن.

نکته‌ی مهم درباره‌ی CDN routing:
    سایت به‌صورت A/B CDN routing داره:
    - آدرس‌های `video-nss.flixcdn.com` با prefix امضاشده‌ی `{sign},{expiry}/`
      روی همه‌ی کیفیت‌ها کار می‌کنن (یه sign برای همه‌ی کیفیت‌ها).
    - آدرس‌های `video-pr.xhcdn.com` با `key=...,end=...,data=...,speed=0k/`
      فقط روی همون یک فایل کار می‌کنن (per-file signature) و فقط کیفیت 1080p
      قابل دسترسه (در اصل 302 redirect به ahcdn.com می‌ده).
    سهم: ≈80% flixcdn, ≈20% xhcdn. handler چندبار صفحه رو fetch می‌کنه تا
    flixcdn بگیره؛ اگر فقط xhcdn دسترس بود، فقط 1080p رو پیشنهاد می‌ده.
    برای عبور از Cloudflare bot protection از curl_cffi با impersonate='chrome131'
    استفاده می‌شه.

نکات:
    - نیازی به ServiceWorker یا JS نیست — صفحه‌ی HTML خودش شامل URL های
      مستقیم امضاشده‌ست.
    - کوکی‌های session از Cloudflare میان؛ curl_cffi به‌صورت خودکار handle می‌کنه.
      کوکی `aged-enough=1` siteMode=desktop locale=en اضافی برای پایداری ست می‌شه.
    - اگر ویدیو trailer نداشت (e.g., some studio-only videos)، handler یک
      پیام کاربرپسند برمی‌گردنه.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

import aiohttp

# curl_cffi برای عبور از Cloudflare bot protection با impersonate chrome131
try:
    from curl_cffi import requests as cc_requests
    _HAS_CURL_CFFI = True
except ImportError:
    _HAS_CURL_CFFI = False
    cc_requests = None

logger = logging.getLogger("Faphouse")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

ProgressCallback = Optional[Callable[[str, int, int], Awaitable[None]]]

# Regex استخراج xc_id از URL
# slug-with-title-{xc_id} → آخرین قسمت بعد از آخرین خط‌تیره
# xc_id معمولاً ترکیبی از حروف بزرگ/کوچک و عدد (6-7 کاراکتر) مثل hV5npB یا KjRj16
_URL_XCID_RE = re.compile(
    r"faphouse\.com/videos/(?:[a-zA-Z0-9-]+-)?([a-zA-Z0-9]{4,12})(?:[/?#]|$)",
    re.IGNORECASE,
)

# پاترن data-fallback با URL امضاشده‌ی CDN (هر دو flixcdn و xhcdn)
# مثال flixcdn: data-fallback="https://video-nss.flixcdn.com/g4wzdDr-phrjvBDjtfD3bA==,1787702400/hV/hV5npB/trailer/1080.mp4"
# مثال xhcdn:   data-fallback="https://video-pr.xhcdn.com/key=...,end=1787702400/data=.../speed=0k/hV/hV5npB/trailer/1080.mp4"
_FALLBACK_RE = re.compile(
    r'data-fallback="([^"]*video-nss\.flixcdn\.com/[^"]+/trailer/[^"]+\.mp4)"',
    re.IGNORECASE,
)
_FALLBACK_XHCDN_RE = re.compile(
    r'data-fallback="([^"]*video-pr\.xhcdn\.com/[^"]+/trailer/[^"]+\.mp4)"',
    re.IGNORECASE,
)

# لیست کیفیت‌های پشتیبانی‌شده (به ترتیب نزولی برای نمایش)
QUALITIES = [1080, 720, 480, 240]

# حداکثر دفعات fetch صفحه برای گرفتن URL از CDN اول (flixcdn)
_MAX_PAGE_FETCH_ATTEMPTS = 6


def is_faphouse_url(url: str) -> bool:
    return "faphouse.com/videos/" in url.lower()


def extract_video_id(url: str) -> Optional[str]:
    """استخراج xc_id (آخرین بخش URL slug) از URL."""
    m = _URL_XCID_RE.search(url)
    if m:
        return m.group(1)
    # fallback: آخرین بخش path
    m = re.search(r"/videos/([^/?#]+)", url)
    if m:
        slug = m.group(1)
        # آخرین قسمت بعد از خط تیره
        if "-" in slug:
            tail = slug.rsplit("-", 1)[-1]
            if re.match(r"^[A-Za-z0-9]{4,12}$", tail):
                return tail
        if re.match(r"^[A-Za-z0-9]{4,12}$", slug):
            return slug
    return None


# ─── parse trailer URL از HTML ───────────────────────────────────────────────


def _parse_trailer_url(html: str) -> Tuple[Optional[str], str]:
    """استخراج URL امضاشده‌ی trailer از data-fallback attribute.

    Returns:
        (url, cdn_type) که cdn_type یکی از "flixcdn" یا "xhcdn" یا "" است.
    """
    m = _FALLBACK_RE.search(html)
    if m:
        return m.group(1).replace("&amp;", "&"), "flixcdn"
    m = _FALLBACK_XHCDN_RE.search(html)
    if m:
        return m.group(1).replace("&amp;", "&"), "xhcdn"
    # fallback: پیدا کردن هر URL با pattern trailer/*.mp4
    m = re.search(
        r'(https?://video-nss\.flixcdn\.com/[^"\s\\]+/trailer/\d+\.mp4)',
        html, re.IGNORECASE,
    )
    if m:
        return m.group(1).replace("&amp;", "&"), "flixcdn"
    m = re.search(
        r'(https?://video-pr\.xhcdn\.com/[^"\s\\]+/trailer/\d+\.mp4)',
        html, re.IGNORECASE,
    )
    if m:
        return m.group(1).replace("&amp;", "&"), "xhcdn"
    return None, ""


def _parse_trailer_base_flixcdn(trailer_url: str) -> Optional[Tuple[str, str, str]]:
    """از URL کامل trailer (flixcdn)، base سه قسمتی برمی‌گردونه:
       (cdn_base, sign_with_expiry, xc_path)
       مثال برای:
       https://video-nss.flixcdn.com/g4wzdDr-phrjvBDjtfD3bA==,1787702400/hV/hV5npB/trailer/1080.mp4
       → ("https://video-nss.flixcdn.com",
          "g4wzdDr-phrjvBDjtfD3bA==,1787702400",
          "hV/hV5npB")
    """
    m = re.match(
        r"^(https?://video-nss\.flixcdn\.com)/([^/]+)/((?:[^/]+/)*?)/?trailer/\d+(?:\.av1)?\.mp4$",
        trailer_url, re.IGNORECASE,
    )
    if m:
        return m.group(1), m.group(2), m.group(3).rstrip("/")
    # regex simpler fallback
    m = re.match(
        r"^(https?://video-nss\.flixcdn\.com)/([^/]+)/(.+?)/trailer/\d+(?:\.av1)?\.mp4$",
        trailer_url, re.IGNORECASE,
    )
    if m:
        return m.group(1), m.group(2), m.group(3)
    return None


def _parse_meta(html: str, prop: str) -> str:
    """استخراج محتوای meta tag با property یا name."""
    m = re.search(
        rf'<meta\s+property="{re.escape(prop)}"\s+content="([^"]+)"',
        html, re.IGNORECASE,
    )
    if not m:
        m = re.search(
            rf'<meta\s+name="{re.escape(prop)}"\s+content="([^"]+)"',
            html, re.IGNORECASE,
        )
    return m.group(1) if m else ""


def _parse_data_attr(html: str, name: str) -> str:
    """استخراج مقدار data-el-{name} از اولین تطابق."""
    m = re.search(
        rf'data-el-{re.escape(name)}="([^"]+)"',
        html, re.IGNORECASE,
    )
    return m.group(1) if m else ""


def _parse_title(html: str) -> str:
    """از <title> ... | Faphouse فقط بخش اصلی قبل از ' ft.' یا ' | '."""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL)
    if not m:
        return ""
    t = m.group(1).strip()
    # الگوی معمول: "Main Title ft. X by Studio: tags... | Faphouse"
    if " ft." in t:
        t = t.split(" ft.", 1)[0]
    if " | Faphouse" in t:
        t = t.split(" | Faphouse", 1)[0]
    if " by " in t and ": " in t:
        # حذف بخش "by Studio: tags..."
        t = re.split(r" by [^:]+: ", t)[0]
    return t.strip()


# ─── page fetch helper (curl_cffi) ───────────────────────────────────────────


def _fetch_page_sync(url: str, cookies: Optional[dict] = None) -> Optional[str]:
    """صفحه‌ی ویدیو رو با curl_cffi (chrome131 impersonation) می‌گیره.

    cookieهای کوچک برای پایداری ست می‌شن، اما نه aged-enough که xhcdn trigger کنه.
    اگر curl_cffi موجود نبود، None برمی‌گرده.
    """
    if not _HAS_CURL_CFFI:
        return None
    try:
        r = cc_requests.get(
            url,
            impersonate="chrome131",
            cookies=cookies or {"siteMode": "desktop", "locale": "en"},
            timeout=30,
        )
        if r.status_code != 200:
            logger.warning(f"[Faphouse] curl_cffi HTTP {r.status_code}")
            return None
        return r.text
    except Exception as e:
        logger.warning(f"[Faphouse] curl_cffi fetch error: {e}")
        return None


async def _fetch_page(url: str, cookies: Optional[dict] = None) -> Optional[str]:
    """Wrapper async برای fetch_page_sync."""
    return await asyncio.to_thread(_fetch_page_sync, url, cookies)


# ─── get_video_info ──────────────────────────────────────────────────────────


async def get_video_info(url: str) -> Optional[dict]:
    """اطلاعات کامل ویدیو + لیست کیفیت‌های trailer.

    Returns:
        {
          video_id, title, description, duration, thumb, studio, price,
          access_type, trailer_url_1080,
          qualities: [ {label, file, type, size?} ],
        }
    """
    xc_id = extract_video_id(url)
    if not xc_id:
        logger.warning(f"[Faphouse] could not extract xc_id from {url[:120]}")
        return None

    # نرمال‌سازی URL: slug کامل رو از URL اصلی بگیر
    m = re.search(r"faphouse\.com/videos/([^/?#]+)", url, re.IGNORECASE)
    slug_full = m.group(1) if m else xc_id
    norm_url = f"https://faphouse.com/videos/{slug_full}"

    # چندبار صفحه رو fetch کن تا URL از CDN اول (flixcdn) رو بگیریم
    # xhcdn فقط 1080p رو پشتیبانی می‌کنه (per-file signature)
    html = ""
    trailer_url = None
    cdn_type = ""
    for attempt in range(_MAX_PAGE_FETCH_ATTEMPTS):
        html = await _fetch_page(norm_url)
        if not html:
            await asyncio.sleep(0.6)
            continue
        trailer_url, cdn_type = _parse_trailer_url(html)
        if trailer_url and cdn_type == "flixcdn":
            break
        if trailer_url and cdn_type == "xhcdn":
            # بعد از ۳ تلاش اگه هنوز xhcdn بود، همان را قبول کن
            if attempt >= 2:
                logger.info(f"[Faphouse] stuck with xhcdn URL after {attempt+1} tries — using 1080p only")
                break
        await asyncio.sleep(0.5)

    if not html:
        logger.error("[Faphouse] could not fetch page after retries")
        return None

    if not trailer_url:
        logger.warning("[Faphouse] no trailer URL in page (premium without trailer?)")

    # متادیتا
    title = _parse_title(html) or _parse_meta(html, "og:title") or f"faphouse_{xc_id}"
    description = _parse_meta(html, "og:description") or _parse_data_attr(html, "content-description")
    thumb = _parse_meta(html, "og:image") or _parse_data_attr(html, "content-poster")
    duration_str = _parse_data_attr(html, "duration")
    try:
        duration = int(duration_str) if duration_str else 0
    except ValueError:
        duration = 0
    fh_id = _parse_data_attr(html, "video-id") or _parse_data_attr(html, "content-id")
    access_type = _parse_data_attr(html, "video-access-type") or "premium"
    price_str = _parse_data_attr(html, "content-price")
    try:
        price = int(price_str) if price_str else 0
    except ValueError:
        price = 0

    # استخراج studio از لینک /studios/ یا /studio/ در صفحه
    studio_match = re.search(
        r'<a[^>]*href="/studios?/[^"?#]+"[^>]*>([^<]+)</a>',
        html, re.IGNORECASE,
    )
    studio = studio_match.group(1).strip() if studio_match else ""

    # استخراج tags / categories
    tags = []
    for m in re.finditer(
        r'<a[^>]*href="/category/[^"?#]+"[^>]*>([^<]+)</a>',
        html, re.IGNORECASE,
    ):
        t = m.group(1).strip()
        if t and t not in tags:
            tags.append(t)
        if len(tags) >= 8:
            break

    qualities: List[dict] = []
    if trailer_url:
        if cdn_type == "flixcdn":
            base = _parse_trailer_base_flixcdn(trailer_url)
            if base:
                cdn_base, sign_expiry, xc_path = base
                # پروپ هر کیفیت به‌صورت موازی
                async def _probe(session, q):
                    u = f"{cdn_base}/{sign_expiry}/{xc_path}/trailer/{q}.mp4"
                    size = await _probe_size(session, u)
                    return {
                        "label": f"{q}p",
                        "label_raw": str(q),
                        "file": u,
                        "type": "mp4",
                        "codec": "h264",
                        "size": size,
                        "default": q == 1080,
                    }
                try:
                    async with aiohttp.ClientSession(headers={"User-Agent": UA}) as session:
                        results = await asyncio.gather(*[_probe(session, q) for q in QUALITIES])
                        for q in results:
                            if q["size"] > 0:
                                qualities.append(q)
                except Exception as e:
                    logger.warning(f"[Faphouse] probe error: {e}")
                # fallback اگه چیزی پیدا نشد: فقط خود 1080 رو بذار
                if not qualities:
                    qualities.append({
                        "label": "1080p",
                        "label_raw": "1080",
                        "file": trailer_url,
                        "type": "mp4",
                        "codec": "h264",
                        "size": 0,
                        "default": True,
                    })
            else:
                # اگر base parse نشد، فقط 1080 رو از خود URL داریم
                qualities.append({
                    "label": "1080p",
                    "label_raw": "1080",
                    "file": trailer_url,
                    "type": "mp4",
                    "codec": "h264",
                    "size": 0,
                    "default": True,
                })
        else:  # xhcdn — per-file signature, فقط 1080p
            qualities.append({
                "label": "1080p",
                "label_raw": "1080",
                "file": trailer_url,
                "type": "mp4",
                "codec": "h264",
                "size": 0,  # xhcdn needs HEAD with redirect follow
                "default": True,
                "cdn": "xhcdn",
            })

    return {
        "video_id": fh_id or xc_id,
        "xc_id": xc_id,
        "title": title,
        "description": description,
        "duration": duration,
        "thumb": thumb,
        "studio": studio,
        "tags": tags,
        "price": price,
        "access_type": access_type,
        "trailer_url_1080": trailer_url or "",
        "cdn_type": cdn_type,
        "qualities": qualities,
    }


# ─── download engine (موازی Range — مثل yt_direct / noodle) ─────────────────


CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB chunks (trailerها کوچیکن، 4MB بهتره)
PARALLEL_RANGES = 4


async def _probe_size(session: aiohttp.ClientSession, url: str) -> int:
    """اندازه‌ی کل فایل رو با Range: bytes=0-0 می‌گیره.

    اگر 302 برمی‌گرده، خود aiohttp به‌صورت خودکار follow می‌کنه.
    """
    try:
        async with session.get(
            url,
            headers={"Range": "bytes=0-0", "User-Agent": UA},
            timeout=aiohttp.ClientTimeout(total=20),
            allow_redirects=True,
        ) as r:
            if r.status not in (200, 206):
                return 0
            cr = r.headers.get("Content-Range") or ""
            if "/" in cr:
                try:
                    return int(cr.rsplit("/", 1)[1])
                except ValueError:
                    pass
            cl = r.headers.get("Content-Length")
            if cl and cl != "1":
                try:
                    return int(cl)
                except ValueError:
                    pass
    except Exception as e:
        logger.debug(f"[Faphouse] probe error: {e}")
    return 0


async def download_video(
    url: str,
    quality: dict,
    out_dir: str,
    progress_cb: ProgressCallback = None,
) -> Tuple[bool, str]:
    """دانلود مستقیم trailer از CDN با Range های موازی."""
    file_url = quality.get("file")
    if not file_url:
        return False, "no file URL in quality"

    os.makedirs(out_dir, exist_ok=True)
    label = quality.get("label_raw", "1080")
    base_name = f"faphouse_{label}p_{int(time.time())}.mp4"
    out_path = os.path.join(out_dir, base_name)

    async with aiohttp.ClientSession(headers={"User-Agent": UA}) as session:
        total = await _probe_size(session, file_url)
        if not total:
            # fallback به دانلود معمولی
            return await _plain_download(session, file_url, out_path, progress_cb)

        chunks = []
        start = 0
        while start < total:
            end = min(start + CHUNK_SIZE - 1, total - 1)
            chunks.append((start, end))
            start = end + 1

        t0 = time.time()
        done_bytes = 0
        lock = asyncio.Lock()

        async def fetch_range(start: int, end: int, fh) -> bool:
            nonlocal done_bytes
            for attempt in range(4):
                try:
                    async with session.get(
                        file_url,
                        headers={
                            "Range": f"bytes={start}-{end}",
                            "User-Agent": UA,
                        },
                        timeout=aiohttp.ClientTimeout(total=120, sock_read=60),
                        allow_redirects=True,
                    ) as r:
                        if r.status != 206:
                            await asyncio.sleep(1.0)
                            continue
                        data = await r.read()
                        if len(data) != end - start + 1:
                            await asyncio.sleep(0.8)
                            continue
                        async with lock:
                            fh.seek(start)
                            fh.write(data)
                            done_bytes += len(data)
                            if progress_cb and done_bytes % (2 * 1024 * 1024) < CHUNK_SIZE:
                                speed = done_bytes / 1024 / 1024 / max(0.1, time.time() - t0)
                                try:
                                    await progress_cb(
                                        f"{done_bytes / 1024 / 1024:.1f}/{total / 1024 / 1024:.1f}MB ({speed:.1f}MB/s)",
                                        done_bytes, total,
                                    )
                                except Exception:
                                    pass
                        return True
                except Exception:
                    await asyncio.sleep(1.0)
            return False

        try:
            with open(out_path, "wb") as fh:
                fh.truncate(total)
                queue = asyncio.Queue()
                for c in chunks:
                    queue.put_nowait(c)

                async def worker():
                    while True:
                        try:
                            s_, e_ = queue.get_nowait()
                        except asyncio.QueueEmpty:
                            return
                        if not await fetch_range(s_, e_, fh):
                            raise RuntimeError(f"range {s_}-{e_} failed after retries")
                        queue.task_done()

                workers = [asyncio.create_task(worker()) for _ in range(PARALLEL_RANGES)]
                await asyncio.gather(*workers)

            sz = os.path.getsize(out_path)
            if sz != total:
                return False, f"incomplete download ({sz}/{total})"
            return True, out_path
        except Exception as e:
            try:
                if os.path.exists(out_path):
                    os.unlink(out_path)
            except Exception:
                pass
            return False, f"ranged download error: {e}"


async def _plain_download(
    session: aiohttp.ClientSession,
    url: str,
    out_path: str,
    progress_cb: ProgressCallback = None,
) -> Tuple[bool, str]:
    """fallback دانلود معمولی (برای xhcdn با redirect)."""
    try:
        async with session.get(
            url,
            headers={"User-Agent": UA},
            timeout=aiohttp.ClientTimeout(total=None, sock_read=120),
            allow_redirects=True,
        ) as r:
            if r.status not in (200, 206):
                return False, f"HTTP {r.status}"
            cl = int(r.headers.get("Content-Length") or 0)
            done = 0
            t0 = time.time()
            with open(out_path, "wb") as f:
                async for chunk in r.content.iter_chunked(256 * 1024):
                    f.write(chunk)
                    done += len(chunk)
                    if progress_cb and cl and done % (2 * 1024 * 1024) < 256 * 1024:
                        speed = done / 1024 / 1024 / max(0.1, time.time() - t0)
                        try:
                            await progress_cb(
                                f"{done / 1024 / 1024:.1f}/{cl / 1024 / 1024:.1f}MB ({speed:.1f}MB/s)",
                                done, cl,
                            )
                        except Exception:
                            pass
        if os.path.getsize(out_path) < 1024:
            return False, "downloaded file too small"
        return True, out_path
    except Exception as e:
        try:
            if os.path.exists(out_path):
                os.unlink(out_path)
        except Exception:
            pass
        return False, f"download error: {e}"
