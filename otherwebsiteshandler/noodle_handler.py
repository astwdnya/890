"""
noodle_handler.py
──────────────────
هندلر دانلود ویدیو از noodlemagazine.com — کاملاً HTTP، بدون مرورگر.

نتیجه‌ی مهندسی معکوس:
─────────────────────
ساختار داخلی سایت:
  ۱) watch URL:    https://noodlemagazine.com/watch/{watch_id}
                   (مثال: -160687358_456239055)
  ۲) صفحه‌ی watch شامل یک JSON توی `window.playlist` با ساختار:
       {
         "image": "https://cdn2.pvvstream.pro/videos/{cdn_id}/preview_800.jpg",
         "sources": [
           {"file":"https://cdn.pvvstream.pro/videos/{cdn_id}/vid_720p.mp4?secure=...","label":"720","type":"mp4"},
           {"file":"...vid_480p.mp4?...","label":"480","type":"mp4","default":true},
           {"file":"...vid_360p.mp4?...","label":"360","type":"mp4"},
           {"file":"...vid_240p.mp4?...","label":"240","type":"mp4"}
         ],
         "trusted": true
       }
     نکته: cdn_id (داخلی) با watch_id متفاوته ولی به همون ویدیو اشاره می‌کنه.
  ۳) URL های CDN (cdn.pvvstream.pro / cdn2.pvvstream.pro) مستقیمن و Range ساپورت
     می‌کنن → دانلود موازی تکه‌ای با سرعت بالا (مثل yt_direct_handler).
  ۴) متادیتا: JSON-LD + meta tags → title, description, thumbnail, duration,
     uploadDate, tags.
  ۵) مهر امنیتی secure=... توی URL هست؛ نیازی به توکن یا session نیست.
     فقط User-Agent و Referer مرورگر کافیه.

جریان دانلود:
    GET /watch/{watch_id}  (با کوکی‌های session)
    → parse window.playlist JSON
    → برای هر کیفیت: {label, file, type, size?}
    → انتخاب کاربر → دانلود موازی Range از CDN → ارسال

نکات:
    - نیازی به ServiceWorker یا JS نیست — صفحه‌ی HTML خودش شامل URL های
      مستقیم امضاشده‌ست.
    - کوکی‌های session (__cf_bm, _cfuvid, PHPSESSID, csrftoken) از Cloudflare
      میان؛ با یه session requests به‌خوبی حفظ می‌شن.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

import aiohttp

logger = logging.getLogger("Noodle")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

ProgressCallback = Optional[Callable[[str, int, int], Awaitable[None]]]

# Regex استخراج watch_id از URL
# watch_id معمولاً مثل -160687358_456239055 (با علامت منفی شروع می‌شه)
_WATCH_ID_RE = re.compile(
    r"noodlemagazine\.com/watch/(-?\d+_\d+)", re.IGNORECASE
)


def is_noodle_url(url: str) -> bool:
    return "noodlemagazine.com/watch/" in url.lower()


def extract_video_id(url: str) -> Optional[str]:
    m = _WATCH_ID_RE.search(url)
    return m.group(1) if m else None


# ─── parse window.playlist از HTML ────────────────────────────────────────


def _parse_playlist_html(html: str) -> Optional[dict]:
    """استخراج window.playlist = {...} از HTML."""
    # پاترن: window.playlist = { ... };
    m = re.search(r"window\.playlist\s*=\s*(\{.*?\})\s*;", html, re.DOTALL)
    if not m:
        # انعطاف‌پذیرتر: تا انتهای خط
        m = re.search(r"window\.playlist\s*=\s*(\{.*?\})\s*\n", html)
    if not m:
        return None
    raw = m.group(1)
    # JSON ممکن بود \u0026 داشته باشه (escape شده برای &) — decode
    raw = raw.replace("\\u0026", "&").replace("\\/", "/")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # تلاش نهایی با eval ایمن‌تر: حذف escape های اضافه
        try:
            return json.loads(raw.replace("\\", ""))
        except Exception:
            return None


def _parse_ld_json(html: str) -> dict:
    """استخراج JSON-LD برای متادیتا."""
    m = re.search(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE,
    )
    if not m:
        return {}
    try:
        return json.loads(m.group(1).strip())
    except Exception:
        return {}


def _parse_iso_duration(iso: str) -> int:
    """PT1H8M23S → 4103 (seconds)."""
    if not iso:
        return 0
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    if not m:
        return 0
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def _parse_meta(html: str, prop: str) -> str:
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


# ─── get_video_info ────────────────────────────────────────────────────────


async def get_video_info(url: str) -> Optional[dict]:
    """اطلاعات کامل ویدیو + لیست کیفیت‌ها.

    Returns:
        {
          video_id, title, description, duration, upload_date, thumb, tags,
          qualities: [ {label, file, type, size?} ],
        }
    """
    vid = extract_video_id(url)
    if not vid:
        return None

    try:
        async with aiohttp.ClientSession(
            headers={
                "User-Agent": UA,
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml",
            }
        ) as session:
            watch_url = f"https://noodlemagazine.com/watch/{vid}"
            async with session.get(watch_url, timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status != 200:
                    logger.warning("[Noodle] watch HTTP %s", r.status)
                    return None
                html = await r.text()
    except Exception as e:
        logger.error("[Noodle] watch fetch error: %s", e)
        return None

    playlist = _parse_playlist_html(html)
    if not playlist or not playlist.get("sources"):
        logger.warning("[Noodle] playlist not found in HTML")
        return None

    ld = _parse_ld_json(html)
    title = ld.get("name") or _parse_meta(html, "og:title") or f"noodle_{vid}"
    description = (ld.get("description") or "").strip()
    duration_iso = ld.get("duration") or ""
    duration = _parse_iso_duration(duration_iso)
    upload_date = (ld.get("uploadDate") or "")[:10]
    thumb = ld.get("thumbnailUrl") or _parse_meta(html, "og:image") or playlist.get("image", "")
    tags = []
    tag_str = _parse_meta(html, "video:tag")
    if tag_str:
        tags = [t.strip() for t in tag_str.split(",") if t.strip()]

    # کیفیت‌ها: مرتب از بالا به پایین
    qualities = []
    for src in playlist.get("sources", []):
        file_url = src.get("file")
        if not file_url:
            continue
        # decode \u0026 اگر هنوز هست ( نباید باشه چون _parse_playlist_html دیکد می‌کنه )
        file_url = file_url.replace("\\u0026", "&").replace("\\/", "/")
        label = src.get("label", "?")
        qualities.append({
            "label": f"{label}p" if label.isdigit() else label,
            "label_raw": label,
            "file": file_url,
            "type": src.get("type", "mp4"),
            "default": bool(src.get("default", False)),
        })
    # مرتب‌سازی نزولی بر اساس رقم‌های label_raw
    def _qkey(q):
        n = re.sub(r"\D", "", q["label_raw"])
        return -int(n) if n else 0
    qualities.sort(key=_qkey)

    return {
        "video_id": vid,
        "title": title,
        "description": description,
        "duration": duration,
        "upload_date": upload_date,
        "thumb": thumb,
        "tags": tags,
        "qualities": qualities,
    }


# ─── download engine (موازی Range — مثل yt_direct) ────────────────────────


CHUNK_SIZE = 8 * 1024 * 1024
PARALLEL_RANGES = 4


async def _probe_size(session: aiohttp.ClientSession, url: str) -> int:
    """اندازه‌ی کل فایل رو با Range: bytes=0-0 می‌گیره."""
    try:
        async with session.get(
            url,
            headers={"Range": "bytes=0-0", "User-Agent": UA,
                     "Referer": "https://noodlemagazine.com/"},
            timeout=aiohttp.ClientTimeout(total=20),
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
        logger.debug("[Noodle] probe error: %s", e)
    return 0


async def download_video(
    url: str,
    quality: dict,
    out_dir: str,
    progress_cb: ProgressCallback = None,
) -> Tuple[bool, str]:
    """دانلود مستقیم از CDN با Range های موازی."""
    file_url = quality.get("file")
    if not file_url:
        return False, "no file URL in quality"

    os.makedirs(out_dir, exist_ok=True)
    base_name = f"noodle_{quality.get('label_raw', 'video')}_{int(time.time())}.mp4"
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
                            "Referer": "https://noodlemagazine.com/",
                        },
                        timeout=aiohttp.ClientTimeout(total=120, sock_read=60),
                    ) as r:
                        if r.status != 206:
                            await asyncio.sleep(1.5)
                            continue
                        data = await r.read()
                        if len(data) != end - start + 1:
                            await asyncio.sleep(1)
                            continue
                        async with lock:
                            fh.seek(start)
                            fh.write(data)
                            done_bytes += len(data)
                            if progress_cb and done_bytes % (4 * 1024 * 1024) < CHUNK_SIZE:
                                speed = done_bytes / 1024 / 1024 / max(0.1, time.time() - t0)
                                try:
                                    await progress_cb(
                                        f"{done_bytes / 1024 / 1024:.0f}/{total / 1024 / 1024:.0f}MB ({speed:.1f}MB/s)",
                                        done_bytes, total,
                                    )
                                except Exception:
                                    pass
                        return True
                except Exception:
                    await asyncio.sleep(1.5)
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
    """fallback دانلود معمولی."""
    try:
        async with session.get(
            url,
            headers={"User-Agent": UA, "Referer": "https://noodlemagazine.com/"},
            timeout=aiohttp.ClientTimeout(total=None, sock_read=120),
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
                            await progress_cb(f"{done / 1024 / 1024:.1f}/{cl / 1024 / 1024:.1f}MB ({speed:.1f}MB/s)", done, cl)
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
