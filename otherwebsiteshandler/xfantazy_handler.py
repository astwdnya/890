"""
xfantazy_handler.py
──────────────────
هندلر اختصاصی xfantazy.org — پلتفرم KVS (Kernel Video Sharing) 🆕

معماری سایت (RE — تست‌شده با پراب واقعی):
  - پلیر kt_player معمولی KVS؛ لینک‌ها داخل var flashvars = {...} صفحه:
      video_url / video_alt_url(2,3,4) + video_url_text / ..._text
      (مثل: https://xfantazy.org/get_file/5/<hash>/487000/487437/487437.mp4/
            ?v-acctoken=<base64> )
  - لینک‌ها امضاشده‌ان (get_file/5/<md5>/...) و «بدون کوکی» 403 میدن!

نکته‌های تست‌شده:
  • 403 بدون کوکی؛ با کوکی‌های session صفحه جواب میده. کوکی‌های ست‌شده:
      PHPSESSID / kt_qparams / kt_acctoken
  • 🔑 kt_acctoken = base64(IP مبدا)! (مثلا OC4yMTIuMTAuMTU5 = 8.212.10.159)
    ولی عملاً PHPSESSID هم کافیه؛ در تست پارالول ۱۲ کانکشن همزمان با
    کوکی‌های یک session همه 206 دادن → سشن کافیه، per-connection چک نیست
  • لینک get_file با impersonate=chrome و کوکی سشن → 206 video/mp4 ✓
  • ایمیپریشن برای خود صفحه‌ی xfantazy لازمه (بدون اون صفحات مشکل دارن)
  • برای هر ویدیو معمولاً یک کیفیت هست (مثلا SD 480p)؛ بعضی ویدیوها
    video_alt_url برای کیفیت‌های بالاتر دارن

معماری دانلود:
  - لینک‌های get_file به سشن (PHPSESSID) و IP وابسته‌ان و منقضی میشن →
    مثل ninevids، موقع دانلود «از نو» صفحه fetch میشه و لینک تازه
    براش انتخاب میشه. آدرس صفحه + ایندکس کیفیت توی #fragment لینک
    حمل میشه (#xfz=<page>#i=<idx>).
  - دانلود با AsyncSession که اول صفحه رو می‌گیره (کوکی‌ها داخل همون
    سشن ست میشن) بعد ۱۶ وورکر موازی Range می‌زنن.
"""

import asyncio
import logging
import re
from typing import List, Optional, Tuple
from urllib.parse import unquote, urljoin

from ._common import (
    ProgressCallback,
    check_impersonation_support,
    cleanup_file,
    download_m3u8 as _download_m3u8_impl,
    extract_title_from_html,
)

logger = logging.getLogger("XfantazyHandler")


# ─── Site configuration ────────────────────────────────────

DISPLAY_NAME = "Xfantazy"
DOMAIN = "xfantazy.org"
HOMEPAGE = "https://xfantazy.org/"

# صفحه‌ی ویدیو: xfantazy.org/<category>/<slug> — دو سگمنت؛ صفحات سیستمی
# و embed حذف میشن.
_URL_RE = re.compile(
    r"^https?://(?:www\.)?xfantazy\.org/"
    r"(?!embed(?:/|$)|search(?:/|$)|members(?:/|$)|signup(?:/|$)|login(?:/|$)|"
    r"categories(?:/|$)|tags(?:/|$)|playlists(?:/|$)|upload(?:/|$)|wp-)"
    r"[\w\-]+/[\w\-]+/?$",
    re.I,
)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# بلوک flashvars صفحه
_FLASHVARS_RE = re.compile(r"var\s+flashvars\s*=\s*\{(.*?)\};", re.DOTALL)

# پسوند fragment برای حمل صفحه + ایندکس کیفیت
_FRAGMENT_TAG = "xfz"


def is_xfantazy_url(url: str) -> bool:
    """بررسی اینکه URL مربوط به xfantazy.org هست."""
    if not url:
        return False
    return bool(_URL_RE.match(url.strip()))


def _split_fragment(url: str) -> Tuple[str, Optional[int]]:
    """جدا کردن fragment حمل‌شده (#xfz=<page>#i=<idx>) از لینک."""
    m = re.search(r"#%s=([^#]+)#i=(\d+)$" % _FRAGMENT_TAG, url)
    if not m:
        return url, None
    return url[: m.start()], int(m.group(2))


def _parse_flashvars(html: str) -> Tuple[str, List[dict]]:
    """استخراج title + لیست {label, url} از بلوک flashvars."""
    fv = _FLASHVARS_RE.search(html)
    if not fv:
        raise ValueError("flashvars not found on page")
    block = fv.group(1)

    m_title = re.search(r"video_title\s*:\s*'([^']*?)'", block)
    title = m_title.group(1).strip() if m_title else ""

    entries: List[Tuple[Optional[str], str]] = []

    def _get(pattern: str) -> Optional[str]:
        m = re.search(pattern, block)
        return m.group(1) if m else None

    # ترتیب: video_url (بالاترین در KVS) بعد alt urls — ولی بعداً بر اساس
    # متن کیفیت (مثلا 1080p) مرتب می‌کنیم.
    pairs = [("video_url", "video_url_text")]
    for i in ("", "2", "3", "4"):
        pairs.append((f"video_alt_url{i}", f"video_alt_url{i}_text"))
    for ukey, tkey in pairs:
        u = _get(rf"{ukey}\s*:\s*'([^']+)'")
        if not u:
            continue
        t = _get(rf"{tkey}\s*:\s*'([^']+)'")
        # اسکیپ پیش‌نمایش‌ها (preview_mp4 و امثالش)
        if "_preview" in u.lower() or "trailer_" in u.lower():
            continue
        entries.append((t, urljoin(HOMEPAGE, u)))

    if not entries:
        raise ValueError("no video_url in flashvars")

    # مرتب‌سازی بر اساس عدد داخل لیبل (کافیه عدد ۳-۴ رقمی گرفته بشه)
    def _qkey(item: Tuple[Optional[str], str]) -> int:
        label = item[0] or ""
        m = re.search(r"(\d{3,4})p", label, re.I)
        return int(m.group(1)) if m else 0

    entries.sort(key=_qkey, reverse=True)
    parsed = []
    for t, u in entries:
        label = t or "Auto"
        parsed.append({"label": f"📡 {label}", "url": u, "method": "direct"})
    return title or "Untitled", parsed


async def _fetch_page_fresh(url: str) -> Tuple[str, str]:
    """fetch تازه‌ی صفحه با سشن (کوکی‌ها برای get_file لازمن).

    Returns: (html, url) — html خام.
    """
    from curl_cffi import requests as _req

    def _sync() -> str:
        s = _req.Session()
        r = s.get(
            url,
            impersonate="chrome",
            timeout=30,
            headers={"User-Agent": _UA, "Referer": HOMEPAGE},
        )
        r.raise_for_status()
        return r.text

    return await asyncio.to_thread(_sync)


async def extract_xfantazy_qualities(url: str) -> Tuple[List[dict], str]:
    """استخراج کیفیت‌های ویدیو از flashvars صفحه‌ی xfantazy.org."""
    if not is_xfantazy_url(url):
        return [], "Invalid Xfantazy URL"
    if not check_impersonation_support():
        return [], "curl_cffi لازمه: pip install curl_cffi"

    logger.info("[Xfantazy] Extracting: %s", url[:80])
    try:
        html = await _fetch_page_fresh(url)
    except Exception as e:
        logger.warning("[Xfantazy] page fetch failed: %s", e)
        return [], f"{type(e).__name__}: {str(e)[:120]}"

    try:
        title, entries = _parse_flashvars(html)
    except Exception as e:
        logger.warning("[Xfantazy] parse failed: %s", e)
        return [], f"{type(e).__name__}: {str(e)[:120]}"

    # حمل صفحه + ایندکس در fragment تا دانلود بتونه لینک تازه بگیره
    qualities = []
    for i, q in enumerate(entries):
        qualities.append({
            "label": q["label"],
            "url": f"{q['url']}#{_FRAGMENT_TAG}={url}#i={i}",
            "method": "direct",
        })

    logger.info("[Xfantazy] Extracted %d qualities | %s", len(qualities), title[:60])
    return qualities, title


# ─── Download (custom: سشن + کوکی + ۱۶ وورکر موازی) ────────

_DL_WORKERS = 16
_DL_CHUNK = 5 * 1024 * 1024  # 5MB per chunk


async def _download_with_session(
    page_url: str,
    video_url: str,
    filepath: str,
    progress_cb: ProgressCallback,
    qidx: Optional[int] = None,
    max_filesize: int = 4 * 1024 * 1024 * 1024,
) -> Tuple[bool, str, int]:
    """دانلود لینک get_file با کوکی‌های سشنِ تازه.

    سشن اول صفحه رو می‌گیره (PHPSESSID/kt_* ست میشن)، بعد لینک تازه از
    flashvars همون پاسخ برچسب‌گذاری میشه و ۱۶ وورکر موازی Range می‌زنن.
    """
    from curl_cffi.requests import AsyncSession

    async with AsyncSession() as session:
        # مرحله ۱: صفحه (کوکی‌ها + لینک تازه)
        try:
            resp = await session.get(
                page_url,
                impersonate="chrome",
                timeout=30,
                headers={"User-Agent": _UA, "Referer": HOMEPAGE},
            )
            html = resp.text
        except Exception as e:
            return False, f"page fetch failed: {type(e).__name__}: {str(e)[:100]}", 0
        try:
            _, entries = _parse_flashvars(html)
        except Exception as e:
            return False, f"flashvars parse failed: {str(e)[:100]}", 0

        target = entries[0]["url"] if video_url is None else video_url
        # لینک پاس‌شده ممکنه ایندکس‌دار باشه — اینجا فقط URL خام لازمه
        target, _ = _split_fragment(target)
        # 🔑 لینک extract ممکنه منقضی باشه؛ انتخاب «همون کیفیت» از لیست تازه
        if qidx is not None and 0 <= qidx < len(entries):
            target = entries[qidx]["url"]

        # مرحله ۲: probe محدوده
        try:
            head = await session.get(
                target,
                impersonate="chrome",
                headers={"User-Agent": _UA, "Referer": page_url,
                         "Range": "bytes=0-1023"},
                timeout=30,
            )
        except Exception as e:
            return False, f"probe failed: {type(e).__name__}: {str(e)[:100]}", 0

        if head.status_code not in (200, 206):
            return False, f"probe HTTP {head.status_code}", 0
        try:
            await head.acontent()  # آزاد کردن connection
        except Exception:
            pass

        content_range = head.headers.get("Content-Range", "")
        accept_ranges = head.headers.get("Accept-Ranges", "").lower()
        supports_range = (
            head.status_code == 206 or accept_ranges == "bytes" or content_range
        )
        total_size = 0
        m = re.search(r"/(\d+)$", content_range)
        if m:
            total_size = int(m.group(1))
        if not total_size:
            try:
                total_size = int(head.headers.get("Content-Length") or 0)
            except ValueError:
                total_size = 0
        if not total_size:
            return False, "cannot determine file size", 0
        if total_size > max_filesize:
            return False, "File exceeds size limit", 0

        if not supports_range:
            # دانلود تک‌بخشی (بدون Range)
            logger.info("[Xfantazy] Range not supported — single stream")
            full = await session.get(
                target, impersonate="chrome", timeout=3600,
                headers={"User-Agent": _UA, "Referer": page_url},
            )
            data = full.content
            if len(data) < 1024:
                return False, f"too small ({len(data)}B)", 0
            with open(filepath, "wb") as f:
                f.write(data)
            await progress_cb(f"📥 دریافت کامل — {len(data) / 1024 / 1024:.1f}MB")
            return True, "", len(data)

        # مرحله ۳: chunk queue + ۱۶ وورکر
        chunks = []
        offset = 0
        idx = 0
        while offset < total_size:
            end = min(offset + _DL_CHUNK - 1, total_size - 1)
            chunks.append((idx, offset, end))
            offset = end + 1
            idx += 1
        total_chunks = len(chunks)
        logger.info(
            "[Xfantazy] multi-segment: %d chunks × %dMB, %d workers, total=%dMB",
            total_chunks, _DL_CHUNK // 1024 // 1024, _DL_WORKERS,
            total_size // 1024 // 1024,
        )

        import os

        with open(filepath, "wb") as f:
            f.truncate(total_size)

        chunk_queue: asyncio.Queue = asyncio.Queue()
        for c in chunks:
            await chunk_queue.put(c)

        downloaded = 0
        lock = asyncio.Lock()
        errors: List[str] = []
        last_reported = -1

        async def _worker():
            nonlocal downloaded, last_reported
            while True:
                try:
                    cidx, start, end = chunk_queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                ok = False
                for attempt in range(3):  # retry هر chunk
                    try:
                        r = await session.get(
                            target,
                            impersonate="chrome",
                            headers={"User-Agent": _UA, "Referer": page_url,
                                     "Range": f"bytes={start}-{end}"},
                            timeout=120,
                        )
                        if r.status_code in (200, 206):
                            data = r.content
                            if len(data) != (end - start + 1):
                                raise ValueError(
                                    f"chunk size mismatch: {len(data)}"
                                )
                            with open(filepath, "r+b") as f:
                                f.seek(start)
                                f.write(data)
                                f.flush()
                            ok = True
                            break
                        else:
                            logger.warning(
                                "[Xfantazy] chunk %d HTTP %s (attempt %d)",
                                cidx, r.status_code, attempt + 1,
                            )
                    except Exception as e:
                        logger.warning(
                            "[Xfantazy] chunk %d err: %s (attempt %d)",
                            cidx, str(e)[:80], attempt + 1,
                        )
                        await asyncio.sleep(1.5 * (attempt + 1))
                if not ok:
                    errors.append(f"chunk {cidx}")
                    chunk_queue.task_done()
                    return
                async with lock:
                    downloaded += 1
                    mb = total_size / 1024 / 1024
                    pct = downloaded * 100 // total_chunks
                    if pct >= last_reported + 2 or downloaded == total_chunks:
                        last_reported = pct
                        cur = downloaded * _DL_CHUNK / 1024 / 1024
                        cur = min(cur, mb)
                        await progress_cb(
                            f"📥 دریافت {downloaded}/{total_chunks} — "
                            f"{min(cur, mb):.1f}MB از {mb:.1f}MB ({pct}%)"
                        )
                chunk_queue.task_done()

        workers = [asyncio.create_task(_worker()) for _ in range(_DL_WORKERS)]
        await asyncio.gather(*workers)

        if errors:
            cleanup_file(filepath)
            return False, f"chunks failed: {', '.join(errors[:5])}", 0

        # اگر لینک progress نهایی نداد، اینجا بده
        await progress_cb(
            f"📥 کامل شد — {total_size / 1024 / 1024:.1f}MB"
        )
        return True, "", total_size


async def download_xfantazy_direct(
    url: str, filepath: str, progress_cb: ProgressCallback
) -> Tuple[bool, str, int]:
    """دانلود مستقیم get_file با کوکی سشن (لینک تازه از نو گرفته میشه)."""
    if not url:
        return False, "Empty URL", 0

    real_url, qidx = _split_fragment(url)

    # صفحه‌ی ویدیو از fragment (extract همیشه میذاره)
    m = re.search(r"#%s=([^#]+)#i=(\d+)$" % _FRAGMENT_TAG, url)
    if not m:
        return False, "no page url fragment", 0
    page_url = unquote(m.group(1))

    # مرحله ۱: دانلود با سشن تازه (لینک تازه از داخل همون سشن، با ایندکس)
    success, error, size = await _download_with_session(
        page_url, real_url, filepath, progress_cb, qidx=qidx
    )
    if success:
        return True, "", size
    cleanup_file(filepath)

    # مرحله ۲: fallback — yt-dlp با referer (بعضی وقتا هدر می‌خواد)
    from ._common import download_with_ytdlp

    success, error, size = await download_with_ytdlp(
        real_url,
        filepath,
        progress_cb,
        referer=page_url,
        extra_headers={"Referer": page_url},
    )
    if success:
        return True, "", size
    cleanup_file(filepath)
    return False, error or "download failed", 0


async def download_xfantazy_m3u8(
    url: str, filepath: str, progress_cb: ProgressCallback
) -> Tuple[bool, str, int]:
    """xfantazy معمولاً MP4 مستقیم میده؛ برای سازگاری اینترفیس."""
    success, error, size = await _download_m3u8_impl(
        url, filepath, progress_cb, referer=HOMEPAGE
    )
    if success:
        return True, "", size
    cleanup_file(filepath)
    return False, error, 0


# ─── Session store (برای _make_site_handler در bot.py) ─────

xfantazy_sessions: dict = {}
