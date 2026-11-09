"""یوتیوب از طریق سایت واسط — بدون هیچ اتصال مستقیم به یوتیوب و بدون yt-dlp.

معماری (بدون cobalt — حذف شد):
- لیست فرمت‌های «دقیقاً واقعی» ویدیو + تایتل/تامبنیل/مدت:
    clipto  →  POST https://www.clipto.com/api/youtube  {"url": ...}
    هر فرمتی که برمی‌گردونه (mp4/webm/m4a/opus با ابعاد/fps/bitrate/itag)
    همون چیزیه که واقعاً روی ویدیوئه — هیچ لیست هاردکدی وجود نداره.
- متادیتای تکمیلی (دیسکریپشن/بازدید/لایک/تاریخ):
    پروکسی YouTube Data API  →  https://ytapi.apps.mattw.io/v3
- بایت‌های دانلود از طریق موتور loader.to (host API: p.savenow.to —
    y2mate و savetube و ddownr قدیمیNXDOMAIN شدن و کاملاً مُردن):
    ۱) PoW:  GET /api/pow/challenge → حل sha256(salt + ":" + nonce) با
       «difficulty» صفرِ هگز اولیه (چند هزار تلاش، <۱ ثانیه)
    ۲) POST /api/pow/verify → Bearer token (≈۱۰ دقیقه اعتبار — کش می‌شه)
    ۳) GET /api/v2/download?format=<q>&url=...&apikey=<کلید عمومی فرانت‌اند>
    ۴) poll /api/progress?id=... تا success=1 → download_url روی CDN خودش
       (paulineNN.savenow.to) — نه googlevideo، نه قفل IP → از Railway هم
       مستقیم قابل دانلوده. (تست زنده: 720p ~۱.۴MB/s، m4a، mp3 ✓)

نکته‌ی شفاف: clipto لینک‌هایش را به IP سرور خودش قفل می‌کند (تست شد، 403
cross-IP) پس فقط «لیست فرمت/متادیتا» از آن می‌آید؛ بایت‌ها همیشه از
واسطه‌ی دانلود (CDN واسطه) می‌آید، هرگز مستقیم از یوتیوب.
"""

import asyncio
import hashlib
import logging
import re
import time
from typing import Dict, List, Optional
from urllib.parse import quote

import aiohttp
from aiohttp import ClientTimeout
from curl_cffi.requests import AsyncSession

logger = logging.getLogger("YtIntermediary")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# ─── ارائه‌دهنده‌ی فرمت/متادیتا ───
CLIPTO_API = "https://www.clipto.com/api/youtube"
META_API = "https://ytapi.apps.mattw.io/v3"
META_KEY = "foo1"

# ─── ارائه‌دهنده‌ی بایت: موتور loader.to روی p.savenow.to ───
SAVENOW_BASE = "https://p.savenow.to"
SAVENOW_KEY = "dfcb6d76f2f6a9894gjkege8a4ab232222"   # کلید عمومی فرانت‌اند loader.to
SAVENOW_REF = "https://en.loader.to/"

CACHE_TTL = 600            # کش meta/formats (ثانیه)
_dl_health: Dict[str, float] = {}   # provider name → آخرین موفقیت
_meta_cache: Dict[str, dict] = {}
_savenow_tok: Dict[str, object] = {}   # {"token": str, "exp": float}
_sn_sess: Optional[AsyncSession] = None   # سشن curl_cffi مشترک (فینگرپرینت کروم)


def _sn_session() -> AsyncSession:
    global _sn_sess
    if _sn_sess is None:
        _sn_sess = AsyncSession(impersonate="chrome124")
    return _sn_sess


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


# ═══════════════ بایت‌ها: موتور loader.to (p.savenow.to) ═══════════════


def _savenow_fmt(want: dict) -> str:
    """فرمت savenow: ویدیو → «1080/720/480/360»، صدا → «m4a/mp3»."""
    if want["kind"] == "audio":
        return "m4a" if (want.get("ext") or "") == "m4a" else "mp3"
    h = int(want.get("height") or 360)
    for q in (1080, 720, 480, 360):
        if h >= q:
            return str(q)
    return "360"


async def _savenow_token() -> str:
    """PoW challenge → حل → verify → Bearer token (با کش تا انقضا).

    مهم: کل فلو با curl_cffi (جعل TLS کروم) می‌ره — سرور به کلاینت‌های
    با فینگرپرینت پایتون/aiohttp جواب api_key_required/403 می‌ده.
    """
    now = time.time()
    tok = _savenow_tok.get("token")
    if tok and now < float(_savenow_tok.get("exp") or 0) - 60:
        return str(tok)
    s = _sn_session()
    r = await s.get(
        SAVENOW_BASE + "/api/pow/challenge",
        timeout=25, headers={"User-Agent": UA, "Referer": SAVENOW_REF},
    )
    ch = r.json()
    salt = str(ch.get("salt") or "")
    diff = int(ch.get("difficulty") or 3)
    prefix = "0" * diff

    def _solve() -> str:
        n = 0
        while True:
            ns = str(n)
            if hashlib.sha256(f"{salt}:{ns}".encode()).hexdigest().startswith(prefix):
                return ns
            n += 1

    nonce = await asyncio.to_thread(_solve)
    payload = {
        "salt": ch.get("salt"), "difficulty": ch.get("difficulty"),
        "expires_at": ch.get("expires_at"), "signature": ch.get("signature"),
        "nonce": nonce,
    }
    r2 = await s.post(
        SAVENOW_BASE + "/api/pow/verify", json=payload,
        timeout=25, headers={"User-Agent": UA, "Referer": SAVENOW_REF},
    )
    d = r2.json()
    if not d.get("success") or not d.get("token"):
        raise RuntimeError(f"pow verify: {str(d)[:100]}")
    _savenow_tok.clear()
    _savenow_tok.update({
        "token": d["token"],
        "exp": float(d.get("expires_at") or (now + 540)),
    })
    return str(d["token"])


async def _bytes_via_savenow(video_url: str, want: dict) -> Optional[str]:
    """loader.to: PoW → v2/download → poll progress → CDN خودش (بدون قید IP)."""
    tok = await _savenow_token()
    s = _sn_session()
    hdr = {"User-Agent": UA, "Referer": SAVENOW_REF, "Authorization": f"Bearer {tok}"}
    fmt = _savenow_fmt(want)
    chain = [fmt]
    if want["kind"] == "video":
        order = ["1080", "720", "480", "360"]
        i = order.index(fmt) if fmt in order else len(order) - 1
        chain = order[i:i + 2]   # کیفیت خواسته + یک پله پایین‌تر به‌عنوان فالبک
    last_err = ""
    for f in chain:
        try:
            # url باید دقیقاً مثل مرورگر کامل quote بشه — yarl دستکاریش می‌کنه
            full = (SAVENOW_BASE + "/api/v2/download?format=" + f
                    + "&url=" + quote(video_url, safe="") + "&apikey=" + SAVENOW_KEY)
            r = await s.get(full, timeout=45, headers=hdr)
            d = r.json()
            if not d.get("success") or not d.get("id"):
                last_err = f"{f}: {str(d)[:100]}"
                continue
            pid = d["id"]
            deadline = time.time() + (300 if want["kind"] == "video" else 150)
            while time.time() < deadline:
                await asyncio.sleep(3)
                pr = await s.get(
                    SAVENOW_BASE + "/api/progress?id=" + str(pid),
                    timeout=25, headers=hdr,
                )
                p = pr.json()
                if p.get("success") == 1 and p.get("download_url"):
                    dl = str(p["download_url"])
                    if "googlevideo.com" in dl:   # هرگز مستقیم از یوتیوب
                        last_err = f"{f}: googlevideo link رد شد"
                        break
                    _dl_health["savenow"] = time.time()
                    return dl
                if p.get("success") not in (0, 1):
                    last_err = f"{f}: progress {str(p)[:100]}"
                    break
            else:
                last_err = f"{f}: timeout progress"
                continue
            if last_err:
                continue
        except Exception as e:
            last_err = f"{f}: {type(e).__name__}: {str(e)[:80]}"
    logger.info(f"[YTVIA] savenow failed: {last_err}")
    return None


async def get_download_url(video_url: str, want: dict) -> str:
    """لینک دانلود از واسطه — هرگز googlevideo مستقیم. خطا → RuntimeError."""
    order = ["savenow"]
    order.sort(key=lambda n: -_dl_health.get(n, 0))
    providers = {"savenow": _bytes_via_savenow}
    errs = []
    for name in order:
        try:
            url = await providers[name](video_url, want)
        except Exception as e:
            url = None
            errs.append(f"{name}: {type(e).__name__}")
        if url:
            return url
        errs.append(name)
    raise RuntimeError(
        "واسطه‌ی دانلود (loader.to) به این کیفیت جواب نداد: " + "; ".join(errs[:2])
    )
