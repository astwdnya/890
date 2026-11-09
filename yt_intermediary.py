"""یوتیوب از طریق سایت واسط — بدون هیچ اتصال مستقیم به یوتیوب و بدون yt-dlp.

معماری (بدون cobalt — حذف شد):
- لیست فرمت‌های «دقیقاً واقعی» ویدیو + تایتل/تامبنیل/مدت:
    clipto  →  POST https://www.clipto.com/api/youtube  {"url": ...}
    هر فرمتی که برمی‌گردونه (mp4/webm/m4a/opus با ابعاد/fps/bitrate/itag)
    همون چیزیه که واقعاً روی ویدیوئه — هیچ لیست هاردکدی وجود نداره.
- متادیتای تکمیلی (دیسکریپشن/بازدید/لایک/تاریخ):
    پروکسی YouTube Data API  →  https://ytapi.apps.mattw.io/v3
- بایت‌های دانلود از طریق سایت واسط (سرور واسطه از یوتیوب می‌گیره،
    ما فقط از CDN خود واسطه دانلود می‌کنیم) — کسکید:
    ۱) y2mate AJAX (analyzeV2 → fetchConvert → dlink روی CDN خودش)
    ۲) savetube (info → download → downloadUrl روی CDN خودش)
    ارائه‌دهنده‌ی سالم cache می‌شه و دفعه‌های بعد همون استفاده می‌شه.

نکته‌ی شفاف: clipto لینک‌هایش را به IP سرور خودش قفل می‌کند (تست شد، 403
cross-IP) پس فقط «لیست فرمت/متادیتا» از آن می‌آید؛ بایت‌ها همیشه از
واسطه‌ی دانلود (CDN واسطه) می‌آید، هرگز مستقیم از یوتیوب.
"""

import asyncio
import logging
import re
import time
from typing import Dict, List, Optional

import aiohttp
from aiohttp import ClientTimeout

logger = logging.getLogger("YtIntermediary")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# ─── ارائه‌دهنده‌ی فرمت/متادیتا ───
CLIPTO_API = "https://www.clipto.com/api/youtube"
META_API = "https://ytapi.apps.mattw.io/v3"
META_KEY = "foo1"

# ─── ارائه‌دهنده‌های بایت (دانلود پراکسی‌شده) — به ترتیب امتحان ───
Y2MATE_HOSTS = ["https://www.y2mate.com", "https://y2mate.is", "https://y2mate.pro"]
SAVETUBE_API = "https://api.savetube.me"

CACHE_TTL = 600            # کش meta/formats (ثانیه)
_dl_health: Dict[str, float] = {}   # provider name → آخرین موفقیت
_meta_cache: Dict[str, dict] = {}


def extract_youtube_id(url: str) -> Optional[str]:
    m = re.search(r"(?:v=|youtu\.be/|/v/|/embed/|/shorts/|/live/)([a-zA-Z0-9_-]{11})", url)
    if m:
        return m.group(1)
    m = re.fullmatch(r"[a-zA-Z0-9_-]{11}", url.strip())
    return m.group(0) if m else None


def _parse_iso8601_duration(s: str) -> int:
    m = re.match(r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", (s or "").strip())
    if not m:
        return 0
    d, h, mi, se = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


# ═══════════════ فرمت‌های واقعی + متادیتا (clipto) ═══════════════


def _normalize_medias(medias: List[dict], duration: int) -> List[dict]:
    """medias خام clipto → فرمت‌های تمیز؛ هیچ چیزِ از پیش تعیین‌شده‌ای اضافه نمی‌شه."""
    out = []
    for m in medias or []:
        ext = (m.get("ext") or m.get("extension") or "").lower()
        label = m.get("label") or m.get("quality") or ext
        h = m.get("height") or 0
        # برچسب «... (360p)» یعنی فرمت ویدیویی — حتی اگر height نیامده باشه
        prog = re.search(r"\((\d{3,4})p\)", label)
        if prog:
            h = h or int(prog.group(1))
            is_audio = False
        elif m.get("is_audio") is not None:
            is_audio = bool(m.get("is_audio"))
        else:
            is_audio = (m.get("type") == "audio") or (
                not h and ext in ("m4a", "opus", "mp3", "weba", "ogg")
            )
        if not is_audio and not h:
            continue
        try:
            br = int(m.get("bitrate") or 0)
        except Exception:
            br = 0
        size_est = int(br * duration / 8) if (br and duration) else 0
        if is_audio:
            q_label = re.search(r"\((\d+)\s*kb", label)
            ql = f"{q_label.group(1)}kbps" if q_label else (label or ext)
        else:
            ql = f"{h}p"
        out.append({
            "kind": "audio" if is_audio else "video",
            "label": label,
            "quality": ql,
            "ext": ext or ("m4a" if is_audio else "mp4"),
            "height": h,
            "width": m.get("width") or 0,
            "fps": m.get("fps") or 0,
            "bitrate": br,
            "size_est": size_est,
            "format_id": m.get("formatId") or "",
        })
    # دکمه‌های تکراری (همان کیفیت+اکستنشن) یکی می‌شن — بیشترین bitrate می‌مونه
    best: Dict[tuple, dict] = {}
    for f in out:
        key = (f["kind"], f["quality"], f["ext"])
        if key not in best or f["bitrate"] > best[key]["bitrate"]:
            best[key] = f
    uniq = sorted(best.values(), key=lambda f: 0 if f["kind"] == "video" else 1)
    return uniq


async def fetch_video_meta(video_url: str) -> dict:
    """متادیتا + فرمت‌های دقیق: clipto (اصلی) + mattw (تکمیلی/فالبک)."""
    vid = extract_youtube_id(video_url)
    if not vid:
        raise ValueError("لینک یوتیوب معتبر نیست")
    now = time.time()
    hit = _meta_cache.get(vid)
    if hit and now - hit["ts"] < CACHE_TTL:
        return dict(hit["meta"])

    meta: dict = {}
    # ۱) clipto — فرمت‌های دقیق + تایتل/تامبنیل/مدت
    clipto_ok = False
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                CLIPTO_API, json={"url": video_url},
                timeout=ClientTimeout(total=45),
                headers={"User-Agent": UA, "Content-Type": "application/json",
                         "Accept": "application/json"},
            ) as r:
                d = await r.json(content_type=None) if r.status == 200 else {}
        if d.get("success") and (d.get("medias") or d.get("title")):
            duration = int(d.get("duration") or 0)
            meta.update({
                "video_id": vid,
                "link": f"https://www.youtube.com/watch?v={vid}",
                "title": d.get("title") or "",
                "author": d.get("author") or "",
                "thumbnail": d.get("thumbnail") or "",
                "duration": duration,
                "formats": _normalize_medias(d.get("medias") or [], duration),
            })
            clipto_ok = True
    except Exception as e:
        logger.warning(f"[YTVIA] clipto meta failed: {e}")

    # ۲) mattw — دیسکریپشن/آمار (و فالبک متادیتای پایه)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                META_API + "/videos",
                params={"part": "snippet,statistics,contentDetails", "id": vid, "key": META_KEY},
                timeout=ClientTimeout(total=30),
                headers={"User-Agent": UA, "Accept": "application/json"},
            ) as r:
                data = await r.json(content_type=None) if r.status == 200 else {}
        items = (data or {}).get("items") or []
        if items:
            it = items[0]
            sn, st, cd = it.get("snippet", {}), it.get("statistics", {}), it.get("contentDetails", {})
            thumbs = sn.get("thumbnails") or {}
            thumb = ""
            for k in ("maxres", "standard", "high", "medium", "default"):
                if k in thumbs and thumbs[k].get("url"):
                    thumb = thumbs[k]["url"]
                    break
            meta.setdefault("video_id", vid)
            meta.setdefault("link", f"https://www.youtube.com/watch?v={vid}")
            meta["title"] = meta.get("title") or sn.get("title") or ""
            meta["author"] = meta.get("author") or sn.get("channelTitle") or ""
            meta["thumbnail"] = meta.get("thumbnail") or thumb
            meta["description"] = sn.get("description") or ""
            meta["published"] = (sn.get("publishedAt") or "")[:10]
            meta["duration"] = meta.get("duration") or _parse_iso8601_duration(cd.get("duration", ""))
            try:
                meta["views"] = int(st.get("viewCount", 0) or 0)
                meta["likes"] = int(st.get("likeCount", 0) or 0)
            except Exception:
                meta["views"] = meta["likes"] = 0
    except Exception as e:
        logger.warning(f"[YTVIA] mattw meta failed: {e}")

    if not meta.get("title"):
        raise RuntimeError("هیچ واسطه‌ای نتونست اطلاعات این ویدیو رو بگیره")
    meta["formats_source"] = "clipto" if clipto_ok else "none"
    _meta_cache[vid] = {"ts": now, "meta": meta}
    if len(_meta_cache) > 200:
        for k in sorted(_meta_cache, key=lambda k: _meta_cache[k]["ts"])[:100]:
            _meta_cache.pop(k, None)
    return meta


async def fetch_thumb_bytes(thumb_url: str) -> Optional[bytes]:
    if not thumb_url:
        return None
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(thumb_url, timeout=ClientTimeout(total=20),
                             headers={"User-Agent": UA}) as r:
                if r.status != 200:
                    return None
                return await r.read()
    except Exception:
        return None


# ═══════════════ بایت‌ها: کسکید واسطه‌های دانلود ═══════════════


async def _bytes_via_y2mate(session: aiohttp.ClientSession, video_url: str,
                            want: dict) -> Optional[str]:
    """y2mate: analyzeV2 → fetchConvert → dlink روی CDN خود y2mate."""
    last_err = ""
    for host in Y2MATE_HOSTS:
        try:
            async with session.post(
                f"{host}/mates/analyzeV2/ajax",
                data={"k_query": video_url, "k_av": "true", "k_mode": "ajax", "k_auto": "0"},
                timeout=ClientTimeout(total=30),
                headers={"User-Agent": UA, "X-Requested-With": "XMLHttpRequest",
                         "Origin": host, "Referer": f"{host}/",
                         "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
            ) as r:
                if r.status != 200:
                    continue
                d = await r.json(content_type=None)
            if d.get("status") != "ok":
                last_err = str(d)[:80]
                continue
            links = d.get("links") or {}
            best = None
            for ext, qs in links.items():
                for q, info in (qs or {}).items():
                    if not isinstance(info, dict) or not info.get("k_id"):
                        continue
                    if want["kind"] == "audio":
                        if ext in ("mp3", "m4a") and (best is None or ext == "mp3"):
                            best = info
                    else:
                        m = re.match(r"(\d{3,4})p", str(info.get("q") or q))
                        if m and int(m.group(1)) == int(want.get("height") or 0):
                            best = info
                            break
                if best and want["kind"] == "video":
                    break
            if not best:
                last_err = "quality not offered by y2mate"
                continue
            fd = {"k_id": best["k_id"], "k_token": best.get("k_token", ""),
                  "k_type": best.get("k", ""), "k_type2": best.get("k", ""),
                  "k_stream": "", "k_mode": "ajax"}
            d2 = {}
            for _ in range(6):  # گاهی processing برمی‌گردونه
                async with session.post(
                    f"{host}/mates/fetchConvert/ajax", data=fd,
                    timeout=ClientTimeout(total=40),
                    headers={"User-Agent": UA, "X-Requested-With": "XMLHttpRequest",
                             "Origin": host, "Referer": f"{host}/"},
                ) as r:
                    d2 = await r.json(content_type=None)
                dlink = d2.get("dlink") or ""
                if d2.get("status") == "ok" and dlink:
                    if dlink.startswith("/"):
                        dlink = host + dlink
                    _dl_health["y2mate"] = time.time()
                    return dlink
                await asyncio.sleep(2.5)
            last_err = f"fetchConvert: {str(d2)[:80]}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            continue
    logger.info(f"[YTVIA] y2mate failed: {last_err}")
    return None


async def _bytes_via_savetube(session: aiohttp.ClientSession, video_url: str,
                              want: dict) -> Optional[str]:
    """savetube: info → download → downloadUrl روی CDN خودش."""
    try:
        q = "audio" if want["kind"] == "audio" else str(want.get("height") or "")
        async with session.get(
            SAVETUBE_API + "/info", params={"url": video_url},
            timeout=ClientTimeout(total=25),
            headers={"User-Agent": UA, "Accept": "application/json"},
        ) as r:
            if r.status != 200:
                return None
            d = await r.json(content_type=None)
        data = d.get("data") or {}
        avail = data.get("availableQualities") or []
        if avail and q != "audio":
            qs = [str(x) for x in avail]
            h = str(want.get("height") or "")
            if h not in qs:
                lowers = [x for x in qs if x.isdigit() and int(x) <= int(h or 0)]
                if not lowers:
                    return None
                q = max(lowers, key=int)
        async with session.get(
            SAVETUBE_API + "/download", params={"url": video_url, "quality": q},
            timeout=ClientTimeout(total=30),
            headers={"User-Agent": UA, "Accept": "application/json"},
        ) as r:
            if r.status != 200:
                return None
            d2 = await r.json(content_type=None)
        dl = ((d2.get("data") or {}).get("downloadUrl")) or ""
        if dl and "googlevideo.com" not in dl:
            _dl_health["savetube"] = time.time()
            return dl
        return None
    except Exception as e:
        logger.info(f"[YTVIA] savetube failed: {type(e).__name__}: {e}")
        return None


async def get_download_url(video_url: str, want: dict) -> str:
    """لینک دانلود از واسطه — هرگز googlevideo مستقیم. خطا → RuntimeError."""
    order = ["y2mate", "savetube"]
    order.sort(key=lambda n: -_dl_health.get(n, 0))
    providers = {"y2mate": _bytes_via_y2mate, "savetube": _bytes_via_savetube}
    errs = []
    async with aiohttp.ClientSession() as s:
        for name in order:
            try:
                url = await providers[name](s, video_url, want)
            except Exception as e:
                url = None
                errs.append(f"{name}: {type(e).__name__}")
            if url:
                return url
            errs.append(name)
    raise RuntimeError(
        "واسطه‌های دانلود (y2mate/savetube) به این کیفیت جواب ندادن: " + "; ".join(errs[:2])
    )
