"""
yt_direct_handler.py
─────────────────────
دانلود مستقیم و سریع ویدیو/آدیو از یوتیوب — بدون توکن، بدون اکانت، بدون Playwright.

نتیجه‌ی مهندسی معکوس (۲۰۲۶):
────────────────────────────
۱) InnerTube Player API (مغز خود یوتیوب — همون چیزی که yt-dlp استفاده می‌کنه):
       POST https://www.youtube.com/youtubei/v1/player?key=<public-key>
       client = ANDROID_VR  (کلاینت یوتیوب VR — تنها کلاینتی که بدون PO token
                              جواب می‌ده؛ بقیه: web/android/ios/tv → LOGIN_REQUIRED
                              برای IP های دیتاسنتر)
       → streamingData.formats (muxed) + adaptiveFormats (video-only/audio-only)
       → همه‌ی کیفیت‌ها تا 4K + همه‌ی کیفیت‌های صدا + حجم دقیق هر فرمت
       → URL های مستقیم googlevideo با سرعت ۶۰+ MB/s
       ⚠ محدودیت: از IP فلگ‌شده فقط بعضی ویدیوها باز می‌شن → fallback لازم

۲) cobalt instance عمومی (co.otomir23.me — v11.7.1):
       POST /  {"url", "videoQuality": "144..2160", "downloadMode": "audio", ...}
       → tunnel URL (فایل mux شده h264+aac آماده ارسال)
       → همه‌ی ویدیوها کار می‌کنن؛ کیفیت نزدیک‌ترین موجود رو برمی‌گردونه
       → صدا: opus / mp3

۳) متادیتا (برای کپشن و تگ های آدیو):
       - ytapi.apps.mattw.io/v3 (پروکسی عمومی mattw.io/youtube-metadata —
         Data API v3 با کلید foo1) → statistics (views/likes)، snippet، tags
       - returnyoutubedislikeapi.com → dislikes + rating
       - i.ytimg.com/vi/<id>/maxresdefault.jpg → تامبنیل (fallback chain)

جریان دانلود ویدیو:
    InnerTube → اگه باز شد: لیست کامل کیفیت‌ها + دانلود مستقیم ( سریع )
                → adaptive: دانلود موازی video+audio → ffmpeg mux
    cobalt    → اگه InnerTube قفل بود: tunnel (همه کیفیت‌ها، mux شده)

جریان دانلود آدیو:
    InnerTube → itag 140 (m4a 129k) / 251 (opus 128k) / ...
    cobalt    → downloadMode=audio (opus)
    + ffmpeg: تامبنیل embed (کاورآرت) + ID3 metadata (title/artist/album/description)
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

logger = logging.getLogger("YtDirect")

UA_WEB = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# ─── InnerTube (cascade: ANDROID_VR → TVHTML5) ──────────────────────────────

# ─── cobalt ────────────────────────────────────────────────────────────────
COBALT_BASES = [
    "https://co.otomir23.me",
]
COBALT_QUALITIES = ["144", "240", "360", "480", "720", "1080", "1440", "2160"]

# ─── metadata ──────────────────────────────────────────────────────────────
MATTW_API = "https://ytapi.apps.mattw.io/v3"  # پروکسی عمومی mattw.io (کلید foo1)
RYD_API = "https://returnyoutubedislikeapi.com/votes"

ProgressCallback = Optional[Callable[[str, int, int], Awaitable[None]]]


def extract_video_id(url: str) -> Optional[str]:
    m = re.search(
        r"(?:v=|youtu\.be/|/v/|/embed/|/shorts/|/live/)([a-zA-Z0-9_-]{11})", url
    )
    if m:
        return m.group(1)
    m = re.fullmatch(r"[a-zA-Z0-9_-]{11}", url.strip())
    return m.group(0) if m else None


def is_youtube_url(url: str) -> bool:
    low = url.lower()
    return any(
        d in low
        for d in (
            "youtube.com/watch",
            "youtu.be/",
            "youtube.com/shorts",
            "youtube.com/live",
            "youtube.com/embed",
            "m.youtube.com",
            "music.youtube.com",
        )
    )


# ═════════════════════════════════════════════════════════════════════════
# InnerTube
# ═════════════════════════════════════════════════════════════════════════


# کلاینت‌های پشتیبان InnerTube — به ترتیب امتحان می‌شن.
# ANDROID_VR تنها کلاینتیه که از IP های دیتاسنتر بدون PO token جواب می‌ده؛
# بقیه شانس کمتری دارن ولی از IP های دیگه (مثل هاست دیپلوی) ممکنه باز بشن.
IT_CLIENTS = [
    {
        "client": {
            "clientName": "ANDROID_VR",
            "clientVersion": "1.60.19",
            "deviceMake": "Oculus",
            "deviceModel": "Quest 3",
            "osName": "Android",
            "osVersion": "12L",
            "androidSdkVersion": 32,
            "hl": "en",
        },
        "ua": (
            "com.google.android.apps.youtube.vr.oculus/1.60.19 "
            "(Linux; U; Android 12L; eureka-user Build/SQ3A.220605.009.A1) gzip"
        ),
        "key": "AIzaSyA8eiZmM1FaDVjRy-df2KTyQ_vz_yYM39w",
        "cn": "28",
    },
    {
        "client": {
            "clientName": "TVHTML5",
            "clientVersion": "7.20250101.10.00",
            "hl": "en",
        },
        "ua": "Mozilla/5.0 (ChromiumStylePlatform) Cobalt/Version",
        "key": "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8",
        "cn": "85",
    },
]


async def _innertube_player(
    session: aiohttp.ClientSession, video_id: str
) -> Optional[dict]:
    """POST player با cascade کلاینت‌ها (ANDROID_VR اول). None یعنی قفل/خطا."""
    for cfg in IT_CLIENTS:
        payload = {
            "context": {"client": dict(cfg["client"])},
            "videoId": video_id,
            "contentCheckOk": True,
            "racyCheckOk": True,
        }
        headers = {
            "Content-Type": "application/json",
            "User-Agent": cfg["ua"],
            "X-YouTube-Client-Name": cfg["cn"],
            "X-YouTube-Client-Version": cfg["client"]["clientVersion"],
            "Origin": "https://www.youtube.com",
        }
        try:
            async with session.post(
                f"https://www.youtube.com/youtubei/v1/player?key={cfg['key']}&prettyPrint=false",
                json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=25),
            ) as r:
                if r.status != 200:
                    continue
                j = await r.json(content_type=None)
        except Exception as e:
            logger.debug("[YtDirect] innertube error: %s", e)
            continue
        if j.get("playabilityStatus", {}).get("status") != "OK":
            continue
        if not j.get("streamingData", {}).get("adaptiveFormats") and not j.get(
            "streamingData", {}
        ).get("formats"):
            continue
        return j
    return None


def _parse_innertube_formats(player: dict) -> Tuple[List[dict], List[dict]]:
    """→ (video_qualities, audio_qualities)

    هر آیتم: {itag, label, mime, size, url, fps}
    video_qualities شامل muxed + video-only (نیازمند ffmpeg mux) هنگاهای مجزا
    audio_qualities: همه‌ی کیفیت‌های صدا
    """
    sd = player.get("streamingData", {})
    videos: Dict[str, dict] = {}
    audios: Dict[str, dict] = {}

    for f in sd.get("formats", []) or []:
        # muxed (video+audio)
        if "url" not in f:
            continue
        q = f.get("qualityLabel") or str(f.get("itag"))
        videos.setdefault(
            q,
            {
                "itag": f.get("itag"),
                "label": q,
                "mime": (f.get("mimeType") or "")[:40],
                "size": int(f.get("contentLength") or 0),
                "url": f["url"],
                "muxed": True,
                "fps": f.get("fps"),
            },
        )

    for f in sd.get("adaptiveFormats", []) or []:
        if "url" not in f:
            continue
        mt = f.get("mimeType") or ""
        size = int(f.get("contentLength") or 0)
        if "audio" in mt:
            br = int(f.get("averageBitrate") or 0) // 1000
            q = f.get("audioQuality", "").replace("AUDIO_QUALITY_", "") or f"{br}k"
            label = f"{q} · {br}kbps · {'opus' if 'opus' in mt else 'm4a'}"
            audios.setdefault(label, {
                "itag": f.get("itag"),
                "label": label,
                "mime": mt[:40],
                "size": size,
                "url": f["url"],
                "bitrate": br,
                "container": "opus" if "opus" in mt else "m4a",
            })
        else:
            q = f.get("qualityLabel") or str(f.get("itag"))
            # فقط بهترین کدک هر کیفیت (h264 > vp9/av01 برای سازگاری تلگرام)
            key = q
            item = {
                "itag": f.get("itag"),
                "label": q,
                "mime": mt[:40],
                "size": size,
                "url": f["url"],
                "muxed": False,
                "fps": f.get("fps"),
                "codec": "h264" if "avc" in mt else ("vp9" if "vp9" in mt else "av01"),
            }
            old = videos.get(key)
            if not old or (item["codec"] == "h264" and old.get("codec") != "h264"):
                videos[key] = item

    # بهترین آدیو برای mux کردن با video-only
    best_audio = None
    for a in audios.values():
        if best_audio is None or a.get("bitrate", 0) > best_audio.get("bitrate", 0):
            best_audio = a

    vlist = []
    for q in sorted(
        videos.keys(), key=lambda x: int(re.sub(r"\D", "", x) or 0), reverse=True
    ):
        item = dict(videos[q])
        if not item["muxed"]:
            item["audio_url"] = best_audio["url"] if best_audio else None
            item["audio_size"] = best_audio["size"] if best_audio else 0
            if best_audio:
                item["size"] += best_audio["size"]
        vlist.append(item)

    alist = sorted(
        audios.values(), key=lambda a: a.get("bitrate", 0), reverse=True
    )
    return vlist, alist


# ═════════════════════════════════════════════════════════════════════════
# Metadata
# ═════════════════════════════════════════════════════════════════════════


async def _fetch_metadata(
    session: aiohttp.ClientSession, video_id: str
) -> dict:
    """متادیتا از mattw proxy + RYD + oEmbed (همه بدون کلید واقعی)."""
    meta = {
        "title": "",
        "author": "",
        "duration": 0,
        "views": 0,
        "likes": 0,
        "dislikes": 0,
        "description": "",
        "tags": [],
        "upload_date": "",
        "thumb": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
    }

    async def _mattw():
        try:
            params = {
                "key": "foo1",  # کلید عمومی پروکسی mattw.io — احراز هویت نمی‌خواد
                "part": "snippet,statistics,contentDetails",
                "id": video_id,
            }
            async with session.get(
                f"{MATTW_API}/videos", params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                if r.status != 200:
                    return
                j = await r.json()
                items = j.get("items") or []
                if not items:
                    return
                it = items[0]
                sn = it.get("snippet", {})
                st = it.get("statistics", {})
                meta["title"] = sn.get("title") or meta["title"]
                meta["author"] = sn.get("channelTitle") or meta["author"]
                meta["description"] = sn.get("description") or meta["description"]
                meta["tags"] = sn.get("tags") or []
                meta["upload_date"] = (sn.get("publishedAt") or "")[:10]
                meta["views"] = int(st.get("viewCount") or 0)
                meta["likes"] = int(st.get("likeCount") or 0)
                # ISO-8601 duration → seconds
                dur = (it.get("contentDetails", {}) or {}).get("duration") or ""
                m = re.match(
                    r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", dur
                )
                if m:
                    h, mi, s = (int(x) if x else 0 for x in m.groups())
                    meta["duration"] = h * 3600 + mi * 60 + s
        except Exception as e:
            logger.debug("[YtDirect] mattw error: %s", e)

    async def _ryd():
        try:
            async with session.get(
                f"{RYD_API}?videoId={video_id}",
                timeout=aiohttp.ClientTimeout(total=12),
            ) as r:
                if r.status != 200:
                    return
                j = await r.json()
                if j.get("likes") is not None and j["likes"] > meta["likes"]:
                    meta["likes"] = int(j["likes"])
                if j.get("dislikes"):
                    meta["dislikes"] = int(j["dislikes"])
        except Exception as e:
            logger.debug("[YtDirect] ryd error: %s", e)

    async def _oembed():
        try:
            async with session.get(
                f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json",
                timeout=aiohttp.ClientTimeout(total=12),
            ) as r:
                if r.status != 200:
                    return
                j = await r.json()
                if not meta["title"]:
                    meta["title"] = j.get("title") or ""
                if not meta["author"]:
                    meta["author"] = j.get("author_name") or ""
                if j.get("thumbnail_url"):
                    meta["thumb"] = j["thumbnail_url"]
        except Exception as e:
            logger.debug("[YtDirect] oembed error: %s", e)

    await asyncio.gather(_mattw(), _ryd(), _oembed())
    return meta


# ═════════════════════════════════════════════════════════════════════════
# Info + qualities
# ═════════════════════════════════════════════════════════════════════════


async def get_video_info(url: str) -> Optional[dict]:
    """اطلاعات کامل ویدیو + لیست کیفیت‌ها.

    Returns:
        {
          video_id, title, author, duration, views, likes, dislikes,
          description, tags, thumb,
          source: "innertube" | "cobalt",
          video_qualities: [ {label, itag?, url?, audio_url?, size, muxed, tunnel?} ],
          audio_qualities:  [ {label, itag?, url?, size, container} ],
        }
    """
    vid = extract_video_id(url)
    if not vid:
        return None

    async with aiohttp.ClientSession(
        headers={"User-Agent": UA_WEB}
    ) as session:
        meta_task = _fetch_metadata(session, vid)
        player_task = _innertube_player(session, vid)

        meta, player = await asyncio.gather(meta_task, player_task)

    info = {
        "video_id": vid,
        "source": "cobalt",
        "video_qualities": [],
        "audio_qualities": [],
        **meta,
    }

    if player:
        # مسیر سریع InnerTube — لیست واقعی کیفیت‌های همین ویدیو
        vd = player.get("videoDetails", {})
        if vd.get("title") and not info["title"]:
            info["title"] = vd["title"]
        if vd.get("author") and not info["author"]:
            info["author"] = vd["author"]
        if vd.get("lengthSeconds"):
            info["duration"] = int(vd["lengthSeconds"])
        if vd.get("viewCount"):
            info["views"] = max(info["views"], int(vd["viewCount"]))
        if vd.get("shortDescription") and not info["description"]:
            info["description"] = vd["shortDescription"]
        thumbs = (vd.get("thumbnail", {}) or {}).get("thumbnails") or []
        if thumbs:
            info["thumb"] = thumbs[-1].get("url") or info["thumb"]

        vq, aq = _parse_innertube_formats(player)
        if vq:
            info["video_qualities"] = vq
            info["audio_qualities"] = aq
            info["source"] = "innertube"
            return info

    # fallback cobalt — نردبان استاندارد + آدیو
    info["video_qualities"] = [
        {"label": f"{q}p", "tunnel": q, "size": 0} for q in reversed(COBALT_QUALITIES)
    ]
    info["audio_qualities"] = [
        {"label": "opus 128kbps", "container": "opus", "size": 0},
        {"label": "mp3 128kbps", "container": "mp3", "size": 0},
    ]
    return info


# ═════════════════════════════════════════════════════════════════════════
# Download engine
# ═════════════════════════════════════════════════════════════════════════


async def _stream_download(
    session: aiohttp.ClientSession,
    url: str,
    out_path: str,
    expected_size: int,
    progress_cb: ProgressCallback = None,
    label: str = "",
) -> Tuple[bool, str]:
    """دانلود استریمی با progress.

    برای googlevideo (که دانلود یکجا رو بعد از چند مگابایت throttle می‌کنه)
    از Range request های تکه‌ای موازی استفاده می‌شه — سرعت ۳۰-۶۰+ MB/s.
    برای URL های دیگه (cobalt tunnel) دانلود استریمی معمولی.
    """
    is_gvideo = "googlevideo.com" in url

    if is_gvideo:
        return await _ranged_parallel_download(
            session, url, out_path, expected_size, progress_cb, label
        )

    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=None, sock_read=120)
        ) as r:
            if r.status != 200:
                return False, f"HTTP {r.status}"
            cl = int(r.headers.get("Content-Length") or expected_size or 0)
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
                                f"{label} {done / 1024 / 1024:.1f}/{cl / 1024 / 1024:.1f}MB ({speed:.1f}MB/s)",
                                done, cl,
                            )
                        except Exception:
                            pass
        if done < 1024:
            return False, "downloaded file too small"
        return True, out_path
    except Exception as e:
        try:
            if os.path.exists(out_path):
                os.unlink(out_path)
        except Exception:
            pass
        return False, f"download error: {e}"


CHUNK_SIZE = 8 * 1024 * 1024  # 8MB per range request
PARALLEL_RANGES = 4  # تعداد range موازی


async def _ranged_parallel_download(
    session: aiohttp.ClientSession,
    url: str,
    out_path: str,
    expected_size: int,
    progress_cb: ProgressCallback = None,
    label: str = "",
) -> Tuple[bool, str]:
    """دانلود googlevideo با Range های موازی (دور زدن throttle).

    ۱. HEAD با Range: bytes=0-0 برای اندازه‌ی کل
    ۲. تقسیم به چانک های ۸MB
    ۳. ۴ چانک موازی — هر کدوم سریع (۳۰-۶۰MB/s)
    ۴. نوشتن به فایل با seek
    """
    # ── اندازه‌ی کل ──
    total = expected_size or 0
    try:
        async with session.get(
            url,
            headers={"Range": "bytes=0-0"},
            timeout=aiohttp.ClientTimeout(total=20),
        ) as r:
            if r.status not in (200, 206):
                return False, f"HTTP {r.status}"
            cr = r.headers.get("Content-Range") or ""
            if "/" in cr:
                try:
                    total = int(cr.rsplit("/", 1)[1])
                except ValueError:
                    pass
    except Exception as e:
        return False, f"range probe error: {e}"

    if not total or total < 1024:
        # fallback به دانلود معمولی
        return await _plain_download(session, url, out_path, progress_cb, label)

    # ── چانک ها ──
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
                    url,
                    headers={"Range": f"bytes={start}-{end}"},
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
                                    f"{label} {done_bytes / 1024 / 1024:.0f}/{total / 1024 / 1024:.0f}MB ({speed:.1f}MB/s)",
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
            fh.truncate(total)  # pre-allocate
            # صف چانک ها — worker های موازی می‌کشن
            queue = asyncio.Queue()
            for c in chunks:
                queue.put_nowait(c)

            async def worker():
                while True:
                    try:
                        s_, e_ = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    ok = await fetch_range(s_, e_, fh)
                    if not ok:
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
    label: str = "",
) -> Tuple[bool, str]:
    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=None, sock_read=120)
        ) as r:
            if r.status != 200:
                return False, f"HTTP {r.status}"
            with open(out_path, "wb") as f:
                async for chunk in r.content.iter_chunked(256 * 1024):
                    f.write(chunk)
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


async def _cobalt_request(
    session: aiohttp.ClientSession, payload: dict
) -> Optional[str]:
    """درخواست به cobalt → tunnel URL."""
    for base in COBALT_BASES:
        try:
            async with session.post(
                f"{base}/",
                json=payload,
                headers={"Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=90),
            ) as r:
                if r.status != 200:
                    continue
                j = await r.json(content_type=None)
                if j.get("status") in ("tunnel", "redirect") and j.get("url"):
                    return j["url"]
                logger.debug("[YtDirect] cobalt %s: %s", base, j.get("status"))
        except Exception as e:
            logger.debug("[YtDirect] cobalt %s error: %s", base, e)
    return None


# خطای «تونل خالی» — بعضی ویدیوها رو اینستنس نمی‌تونه بکشه (کش-محوره).
# با این خطای خاص، caller باید به مسیر بعدی (مثلاً snapwc) fallback کنه.
ERR_TUNNEL_EMPTY = "cobalt tunnel returned empty stream (video not available on relay)"


def _fmt_duration(sec: int) -> str:
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


async def _download_thumb(session, video_id: str, out_path: str) -> bool:
    """دانلود تامبنیل با fallback chain."""
    for q in ("maxresdefault", "sddefault", "hqdefault", "mqdefault"):
        try:
            async with session.get(
                f"https://i.ytimg.com/vi/{video_id}/{q}.jpg",
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                if r.status == 200:
                    data = await r.read()
                    if len(data) > 2000:  # 404 placeholder کوچیک هست
                        with open(out_path, "wb") as f:
                            f.write(data)
                        return True
        except Exception:
            continue
    return False


async def _mux_av(
    video_path: str, audio_path: str, out_path: str
) -> bool:
    """ترکیب video-only + audio-only با ffmpeg (بدون re-encode)."""
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", video_path, "-i", audio_path,
        "-c", "copy", "-movflags", "+faststart",
        out_path,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await asyncio.wait_for(proc.communicate(), timeout=300)
        if proc.returncode != 0:
            logger.warning("[YtDirect] ffmpeg mux error: %s", err.decode()[:200])
            return False
        return True
    except Exception as e:
        logger.warning("[YtDirect] ffmpeg mux failed: %s", e)
        return False


async def _build_audio_with_metadata(
    audio_path: str,
    thumb_path: Optional[str],
    out_path: str,
    meta: dict,
    container: str,
) -> bool:
    """آدیو نهایی: کاورآرت embed + ID3/metadata.

    mp3  → ID3v2 (تامبنیل تو تلگرام نشون داده می‌شه)
    opus → ogg metadata + METADATA_BLOCK_PICTURE
    m4a  → mp4 atom tags
    """
    if container == "mp3" and not audio_path.lower().endswith(".mp3"):
        # تبدیل به mp3 با کیفیت حفظ‌شده
        out_path = out_path.rsplit(".", 1)[0] + ".mp3"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", audio_path]
    if thumb_path and os.path.exists(thumb_path):
        cmd += ["-i", thumb_path]
        has_thumb = True
    else:
        has_thumb = False

    title = (meta.get("title") or "Unknown").replace("\n", " ")[:250]
    artist = (meta.get("author") or "")[:200]
    views = meta.get("views") or 0
    likes = meta.get("likes") or 0
    dur = _fmt_duration(meta.get("duration") or 0)
    # دیسکریپشن آدیو: خلاصه‌ی متادیتا + توضیحات کوتاه ویدیو
    desc_parts = [f"Views: {views:,} | Likes: {likes:,} | Duration: {dur}"]
    if meta.get("upload_date"):
        desc_parts.append(f"Published: {meta['upload_date']}")
    d = (meta.get("description") or "").strip()
    if d:
        desc_parts.append("Description:\n" + d[:1800])
    description = "\n".join(desc_parts)

    meta_args = [
        "-metadata", f"title={title}",
        "-metadata", f"artist={artist}",
        "-metadata", f"album=YouTube",
    ]

    if container == "mp3":
        if has_thumb:
            cmd += ["-map", "0:a", "-map", "1:v", "-c:a", "libmp3lame", "-q:a", "2",
                    "-c:v", "mjpeg", "-disposition:v", "attached_pic"]
        else:
            cmd += ["-map", "0:a", "-c:a", "libmp3lame", "-q:a", "2"]
        cmd += meta_args + ["-metadata", f"comment={description}",
                            "-id3v2_version", "3", "-write_id3v1", "1"]
        if not out_path.lower().endswith(".mp3"):
            out_path = out_path.rsplit(".", 1)[0] + ".mp3"
    elif container == "opus":
        codec = "copy" if audio_path.endswith(".opus") or ".webm" in audio_path else "libopus"
        if has_thumb:
            cmd += ["-map", "0:a", "-map", "1:v", "-c:a", codec, "-c:v", "png",
                    "-disposition:v", "attached_pic"]
        else:
            cmd += ["-map", "0:a", "-c:a", codec]
        cmd += meta_args + ["-metadata", f"comment={description}"]
        out_path = out_path.rsplit(".", 1)[0] + ".opus"
    else:  # m4a
        codec = "copy" if audio_path.endswith((".m4a", ".mp4")) else "aac"
        if has_thumb:
            cmd += ["-map", "0:a", "-map", "1:v", "-c:a", codec, "-c:v", "mjpeg",
                    "-disposition:v", "attached_pic"]
        else:
            cmd += ["-map", "0:a", "-c:a", codec]
        cmd += meta_args + ["-metadata", f"comment={description}"] + [
            "-movflags", "+faststart"]
        out_path = out_path.rsplit(".", 1)[0] + ".m4a"

    cmd += [out_path]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, err = await asyncio.wait_for(proc.communicate(), timeout=240)
        if proc.returncode != 0:
            logger.warning("[YtDirect] audio meta ffmpeg error: %s", err.decode()[:300])
            return False
        return os.path.exists(out_path) and os.path.getsize(out_path) > 1000
    except Exception as e:
        logger.warning("[YtDirect] audio meta failed: %s", e)
        return False


# ═════════════════════════════════════════════════════════════════════════
# Public API
# ═════════════════════════════════════════════════════════════════════════


async def download_video(
    url: str,
    quality: dict,
    out_dir: str,
    progress_cb: ProgressCallback = None,
) -> Tuple[bool, str]:
    """دانلود ویدیو (با صدا).

    quality: یکی از آیتم های info["video_qualities"]
    - source=innertube → دانلود مستقیم googlevideo (سریع) + mux ffmpeg اگه لازم
    - source=cobalt    → tunnel
    """
    vid = extract_video_id(url)
    if not vid:
        return False, "invalid url"
    os.makedirs(out_dir, exist_ok=True)
    base_name = f"yt_{vid}_{int(time.time())}"

    async with aiohttp.ClientSession(
        headers={"User-Agent": UA_WEB}
    ) as session:
        # ── مسیر ۱: InnerTube مستقیم ──
        if quality.get("url"):
            v_path = os.path.join(out_dir, base_name + "_v")
            ok, msg = await _stream_download(
                session, quality["url"], v_path, quality.get("size", 0),
                progress_cb, "📥",
            )
            if not ok:
                return False, msg

            # muxed؟ مستقیم استفاده کن
            if quality.get("muxed"):
                out_path = os.path.join(out_dir, base_name + ".mp4")
                os.replace(v_path, out_path)
                return True, out_path

            # video-only + audio → mux
            audio_url = quality.get("audio_url")
            if not audio_url:
                # آدیو جدا دانلود کن
                player = await _innertube_player(session, vid)
                if not player:
                    try:
                        os.unlink(v_path)
                    except Exception:
                        pass
                    return False, "audio stream unavailable"
                _, aq = _parse_innertube_formats(player)
                if not aq:
                    try:
                        os.unlink(v_path)
                    except Exception:
                        pass
                    return False, "no audio formats"
                audio_url = aq[0]["url"]

            a_path = os.path.join(out_dir, base_name + "_a")
            ok, msg = await _stream_download(
                session, audio_url, a_path, 0, None, "",
            )
            if not ok:
                try:
                    os.unlink(v_path)
                except Exception:
                    pass
                return False, msg

            out_path = os.path.join(out_dir, base_name + ".mp4")
            ok = await _mux_av(v_path, a_path, out_path)
            # پاک‌سازی موقت‌ها
            for p in (v_path, a_path):
                try:
                    os.unlink(p)
                except Exception:
                    pass
            if not ok:
                return False, "ffmpeg mux failed"
            return True, out_path

        # ── مسیر ۲: cobalt tunnel ──
        tunnel_q = quality.get("tunnel") or "720"
        last_err = "cobalt: no tunnel url"
        for attempt in range(3):  # تونل تازه بگیر و retry کن
            tunnel = await _cobalt_request(
                session,
                {
                    "url": f"https://www.youtube.com/watch?v={vid}",
                    "videoQuality": tunnel_q,
                    "filenameStyle": "basic",
                },
            )
            if not tunnel:
                return False, "cobalt: no tunnel url"
            out_path = os.path.join(out_dir, base_name + ".mp4")
            ok, msg = await _stream_download(
                session, tunnel, out_path, 0, progress_cb, "📥"
            )
            if ok:
                return True, out_path
            last_err = msg
            # تونل خالی = اینستنس این ویدیو رو نداره — retry فایده نداره
            if "too small" in msg or "empty" in msg:
                return False, ERR_TUNNEL_EMPTY
            try:
                if os.path.exists(out_path):
                    os.unlink(out_path)
            except Exception:
                pass
            await asyncio.sleep(2)
        return False, last_err


async def download_audio(
    url: str,
    audio_quality: dict,
    out_dir: str,
    meta: Optional[dict] = None,
    progress_cb: ProgressCallback = None,
) -> Tuple[bool, str]:
    """دانلود آدیو + تامبنیل embed + متادیتا.

    audio_quality: یکی از info["audio_qualities"]
    """
    vid = extract_video_id(url)
    if not vid:
        return False, "invalid url"
    os.makedirs(out_dir, exist_ok=True)
    base_name = f"yt_{vid}_{int(time.time())}"
    container = audio_quality.get("container", "mp3")

    # متادیتا اگه نداده شده بگیر
    if meta is None or not meta.get("title"):
        async with aiohttp.ClientSession(
            headers={"User-Agent": UA_WEB}
        ) as session:
            meta = await _fetch_metadata(session, vid)

    async with aiohttp.ClientSession(
        headers={"User-Agent": UA_WEB}
    ) as session:
        # تامبنیل بریز
        thumb_path = os.path.join(out_dir, base_name + "_thumb.jpg")
        has_thumb = await _download_thumb(session, vid, thumb_path)

        raw_path = os.path.join(out_dir, base_name + "_raw")

        # ── مسیر ۱: InnerTube مستقیم ──
        if audio_quality.get("url"):
            ok, msg = await _stream_download(
                session, audio_quality["url"], raw_path,
                audio_quality.get("size", 0), progress_cb, "📥",
            )
            if ok:
                final_path = os.path.join(out_dir, base_name + "." + container)
                ok2 = await _build_audio_with_metadata(
                    raw_path,
                    thumb_path if has_thumb else None,
                    final_path, meta, container,
                )
                try:
                    os.unlink(raw_path)
                except Exception:
                    pass
                if ok2:
                    if has_thumb:
                        try:
                            os.unlink(thumb_path)
                        except Exception:
                            pass
                    # مسیر نهایی ممکنه توسط ffmpeg تغییر کرده باشه (پسوند)
                    return True, final_path
                return False, "audio metadata embedding failed"
            return False, msg

        # ── مسیر ۲: cobalt audio ──
        audio_fmt = "opus" if container == "opus" else "mp3"
        last_err = "cobalt: audio unavailable"
        for attempt in range(3):
            tunnel = await _cobalt_request(
                session,
                {
                    "url": f"https://www.youtube.com/watch?v={vid}",
                    "downloadMode": "audio",
                    "audioFormat": audio_fmt,
                    "filenameStyle": "basic",
                },
            )
            if not tunnel:
                return False, "cobalt: audio unavailable"
            ok, msg = await _stream_download(
                session, tunnel, raw_path, 0, progress_cb, "📥"
            )
            if not ok:
                last_err = msg
                if "too small" in msg or "empty" in msg:
                    return False, ERR_TUNNEL_EMPTY
                await asyncio.sleep(2)
                continue
            break
        else:
            return False, last_err

        final_path = os.path.join(out_dir, base_name + "." + container)
        ok2 = await _build_audio_with_metadata(
            raw_path, thumb_path if has_thumb else None, final_path, meta, container
        )
        try:
            os.unlink(raw_path)
        except Exception:
            pass
        if has_thumb:
            try:
                os.unlink(thumb_path)
            except Exception:
                pass
        if ok2:
            return True, final_path
        return False, "audio metadata embedding failed"
