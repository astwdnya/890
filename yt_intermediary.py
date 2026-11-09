"""یوتیوب از طریق سایت واسط — بدون هیچ اتصال مستقیم به یوتیوب.

لایه‌ی خالص API (بدون تلگرام). گلو (صف/دانلود/ارسال) توی bot.py هست.

- متادیتا (تایتل/دیسکریپشن/تامبنیل/آمار): پروکسی عمومی YouTube Data API v3
  (ytapi.apps.mattw.io) — سرور واسطه به یوتیوب وصل می‌شه، نه ما.
- کشف فرمت‌های «واقعی»: پیمایش نردبان کیفیت از cobalt و خوندن کیفیت واقعی
  از filename پاسخ (cobalt به بهترین کیفیت ≤ درخواست fallback می‌کنه و
  واقعیت رو تو اسم فایل می‌گه) — پس چیزی نمایش داده نمی‌شه مگه اینکه
  واسطه وجودش رو تأیید کنه. هیچ لیست هاردکدشده‌ای به کاربر نشون داده نمی‌شه.
- دانلود: tunnel خود cobalt — بایت‌ها از سرور واسط میان.

همه‌ی کیفیت‌هایی که واسطه تأیید کنه (از ۱۴۴p تا بالاترین موجود + صدا)
به کاربر نمایش داده می‌شه و کاربر انتخاب می‌کنه.
"""

import asyncio
import json
import logging
import re
import time
from typing import Dict, List, Optional

import aiohttp
from aiohttp import ClientTimeout

logger = logging.getLogger("YtIntermediary")

# ─── cobalt instance ها — به ترتیب امتحان می‌شن ───
COBALT_BASES = [
    "https://co.otomir23.me",
    "https://cobalt-api.kwiatekmiki.com",
]

# پروکسی عمومی YouTube Data API v3 (متادیتا)
META_API = "https://ytapi.apps.mattw.io/v3"
META_KEY = "foo1"

# نردبان کیفیت برای «کشف» — نمایشی نیست؛ فقط برای پرس‌وجو از واسطه.
# چیزی که به کاربر نمایش داده می‌شه فقط چیزیه که cobalt واقعاً برگردونده
# (کیفیت واقعی از filename پاسخ خونده می‌شه).
LADDER = ["2160", "1440", "1080", "720", "480", "360", "240", "144"]

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

CACHE_TTL = 600          # کش نتیجه‌ی کشف فرمت‌ها (ثانیه)
_PROBE_GAP = 0.25        # مکث مودبانه بین probe ها

# video_url → {"ts": ..., "videos": {quality: {...}}, "audio": {...}}
_resolve_cache: Dict[str, dict] = {}


def extract_youtube_id(url: str) -> Optional[str]:
    """استخراج video id از هر شکل لینک یوتیوب."""
    m = re.search(
        r"(?:v=|youtu\.be/|/v/|/embed/|/shorts/|/live/)([a-zA-Z0-9_-]{11})", url
    )
    if m:
        return m.group(1)
    m = re.fullmatch(r"[a-zA-Z0-9_-]{11}", url.strip())
    return m.group(0) if m else None


# ═══════════════════════ cobalt ─══════════════════════


async def _cobalt_resolve(
    session: aiohttp.ClientSession,
    base: str,
    video_url: str,
    quality: Optional[str] = None,
    audio: bool = False,
) -> Optional[dict]:
    """یه درخواست resolve به cobalt — خروجی {status,url,filename} یا None."""
    payload: dict = {
        "url": video_url,
        "filenameStyle": "basic",
    }
    if audio:
        payload["downloadMode"] = "audio"
        payload["audioFormat"] = "opus"
    else:
        payload["downloadMode"] = "auto"
        payload["videoQuality"] = quality
        payload["youtubeVideoCodec"] = "h264"
    try:
        async with session.post(
            base + "/",
            json=payload,
            timeout=ClientTimeout(total=30),
            headers={"Accept": "application/json", "User-Agent": UA},
        ) as r:
            if r.status != 200:
                return None
            d = await r.json(content_type=None)
    except Exception:
        return None
    if not isinstance(d, dict) or d.get("status") not in ("tunnel", "redirect"):
        return None
    # سیاست: فقط tunnel — یعنی بایت‌ها از سرور واسط میان، نه مستقیم از گوگل.
    if d.get("status") != "tunnel":
        return None
    return d


def _real_quality_from_filename(filename: str) -> Optional[str]:
    """کیفیت واقعی از اسم فایل cobalt: '...(720p, h264).mp4' → '720p'."""
    m = re.search(r"\((\d{3,4})p,\s*(h264|vp9|av1)\)\.\w+$", filename or "")
    return f"{m.group(1)}p" if m else None


def _ext_from_filename(filename: str, default: str) -> str:
    m = re.search(r"\.(\w{2,5})$", filename or "")
    return f".{m.group(1).lower()}" if m else default


async def resolve_formats(video_url: str) -> dict:
    """کشف فرمت‌های واقعی ویدیو از طریق cobalt.

    خروجی:
    {
      "videos": {"720p": {"tunnel": url, "ext": ".mp4", "codec": "h264", "filename": ...}, ...},
      "audio":  {"tunnel": url, "ext": ".opus", "filename": ...} | None,
      "from_cache": bool,
    }
    کلیدهای videos دقیقاً کیفیت‌هاییه که cobalt واقعاً برگردونده.
    """
    now = time.time()
    hit = _resolve_cache.get(video_url)
    if hit and now - hit["ts"] < CACHE_TTL:
        out = {"videos": dict(hit["videos"]), "audio": hit["audio"], "from_cache": True}
        return out

    videos: Dict[str, dict] = {}
    audio: Optional[dict] = None
    async with aiohttp.ClientSession() as session:
        for base in COBALT_BASES:
            # آدیو
            if audio is None:
                d = await _cobalt_resolve(session, base, video_url, audio=True)
                if d:
                    audio = {
                        "tunnel": d["url"],
                        "ext": _ext_from_filename(d.get("filename", ""), ".opus"),
                        "filename": d.get("filename", ""),
                    }
                await asyncio.sleep(_PROBE_GAP)
            # نردبان کیفیت — از بالا به پایین
            for q in LADDER:
                d = await _cobalt_resolve(session, base, video_url, quality=q)
                if not d:
                    continue
                real = _real_quality_from_filename(d.get("filename", "")) or f"{q}p"
                if real not in videos:
                    videos[real] = {
                        "tunnel": d["url"],
                        "ext": _ext_from_filename(d.get("filename", ""), ".mp4"),
                        "codec": "h264",
                        "filename": d.get("filename", ""),
                    }
                await asyncio.sleep(_PROBE_GAP)
            if videos or audio:
                break

    if not videos and not audio:
        raise RuntimeError("هیچ واسطه‌ای نتونست این ویدیو رو resolve کنه")

    _resolve_cache[video_url] = {"ts": now, "videos": videos, "audio": audio}
    if len(_resolve_cache) > 200:
        for k in sorted(_resolve_cache, key=lambda k: _resolve_cache[k]["ts"])[:100]:
            _resolve_cache.pop(k, None)
    return {"videos": videos, "audio": audio, "from_cache": False}


async def refresh_format(video_url: str, quality: Optional[str], audio: bool) -> Optional[dict]:
    """یه فرمت خاص رو دوباره resolve کن (برای tunnel منقضی‌شده)."""
    async with aiohttp.ClientSession() as session:
        for base in COBALT_BASES:
            d = await _cobalt_resolve(session, base, video_url, quality=quality, audio=audio)
            if d:
                if audio:
                    return {
                        "tunnel": d["url"],
                        "ext": _ext_from_filename(d.get("filename", ""), ".opus"),
                        "filename": d.get("filename", ""),
                    }
                real = _real_quality_from_filename(d.get("filename", "")) or f"{quality}p"
                return {
                    "tunnel": d["url"],
                    "ext": _ext_from_filename(d.get("filename", ""), ".mp4"),
                    "codec": "h264",
                    "quality": real,
                    "filename": d.get("filename", ""),
                }
    return None


# ═══════════════════════ متادیتا ─══════════════════════


def _parse_iso8601_duration(s: str) -> int:
    """PT1H2M3S → ثانیه."""
    m = re.match(
        r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", (s or "").strip()
    )
    if not m:
        return 0
    d, h, mi, se = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


async def fetch_video_meta(video_url: str) -> dict:
    """متادیتای کامل ویدیو از پروکسی YouTube Data API (واسطه).

    خروجی: title, author, description, duration (ثانیه), views, likes,
    published (تاریخ), thumb_url, video_id, link
    """
    vid = extract_youtube_id(video_url)
    if not vid:
        raise ValueError("لینک یوتیوب معتبر نیست")
    params = {
        "part": "snippet,statistics,contentDetails",
        "id": vid,
        "key": META_KEY,
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(
            META_API + "/videos",
            params=params,
            timeout=ClientTimeout(total=30),
            headers={"User-Agent": UA, "Accept": "application/json"},
        ) as r:
            if r.status != 200:
                raise RuntimeError(f"متادیتا: HTTP {r.status}")
            data = await r.json(content_type=None)
    items = (data or {}).get("items") or []
    if not items:
        raise RuntimeError("ویدیو پیدا نشد (خصوصی/حذف‌شده؟)")
    it = items[0]
    sn, st, cd = it.get("snippet", {}), it.get("statistics", {}), it.get("contentDetails", {})
    thumbs = (sn.get("thumbnails") or {})
    thumb_url = ""
    for k in ("maxres", "standard", "high", "medium", "default"):
        if k in thumbs and thumbs[k].get("url"):
            thumb_url = thumbs[k]["url"]
            break
    return {
        "video_id": vid,
        "link": f"https://www.youtube.com/watch?v={vid}",
        "title": sn.get("title") or "",
        "author": sn.get("channelTitle") or "",
        "description": sn.get("description") or "",
        "published": (sn.get("publishedAt") or "")[:10],
        "duration": _parse_iso8601_duration(cd.get("duration", "")),
        "views": int(st.get("viewCount", 0) or 0),
        "likes": int(st.get("likeCount", 0) or 0),
        "thumb_url": thumb_url,
    }


async def fetch_thumb_bytes(thumb_url: str) -> Optional[bytes]:
    """دریافت بایت‌های تامبنیل (عکس CDN — مثل هر مرورگری)."""
    if not thumb_url:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                thumb_url,
                timeout=ClientTimeout(total=20),
                headers={"User-Agent": UA},
            ) as r:
                if r.status != 200:
                    return None
                return await r.read()
    except Exception:
        return None
