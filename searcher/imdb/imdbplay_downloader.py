"""
imdbplay_downloader.py
──────────────────────
دانلودر جدید مبتنی بر imdbplay.tech (۹ سرور) — بدون Playwright.

سرورهای پشتیبانی شده (به ترتیب اولویت):
  - s2 Vidzee      (WASM decrypt) — پایدارترین
  - s1 Videasy     (XOR cipher + seed) — کیفیت‌های متعدد
  - s3 Vidking     (XOR cipher + seed) — همون API videasy
  - s9 2Embed      (vnest API) — کیفیت‌های MP4
  - s7 GarageBand  (vidsrcme + WASM) — IMDb-based

سرورهای غیرفعال (مسدود یا نیازمند Playwright):
  - s4 Vidsrc.sbs  (مسدود از این IP)
  - s5 Vidsrc.wiki (مسدود از این IP)
  - s6 Vidfast     (obfuscation سنگین)
  - s8 Vidsrc-embed.ru (Cloudflare WAF block)

روند کلی:
  1. تبدیل imdb_id → tmdb_id (با TMDB API)
  2. تلاش روی هر سرور به ترتیب اولویت — به محض موفقیت، ادامه با همون سرور
  3. استخراج URL m3u8 (با decrypt متناسب با هر سرور)
  4. استخراج کیفیت‌ها از master.m3u8 (اگه موجود باشه)
  5. دانلود سگمنت‌ها با session مشترک و concat با ffmpeg

زیرنویس:
  - تابع get_persian_subtitle از چندین منبع زیرنویس فارسی استفاده می‌کنه:
    * core.vidzee.wtf/subs (سرور Vidzee)
    * subs.videasy.to (سرورهای Videasy/Vidking)
    * sub.vdrk.site (سرور 2Embed)
  - اولین منبعی که زیرنویس فارسی پیدا کنه برمی‌گردونه.

توابع عمومی:
  - get_qualities(imdb_id, season=None, episode=None) -> List[dict]
  - download_with_quality(imdb_id, quality_label, out_dir, ...) -> Optional[str]
  - get_persian_subtitle(imdb_id, tmdb_id=None, season=None, episode=None, out_dir=None) -> Optional[str]
"""

import asyncio
import base64
import json
import logging
import os
import re
import shutil
import struct
import subprocess
import tempfile
import time
from dataclasses import dataclass, asdict
from typing import List, Optional, Tuple, Callable, Any, Dict
from urllib.parse import urlparse, urljoin, quote_plus, urlencode

from curl_cffi.requests import AsyncSession

logger = logging.getLogger("ImdbPlay")

# ─── Constants ──────────────────────────────────────────────

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_BROWSER_IMPERSONATE = "chrome"

# ─── دانلود همزمان سگمنت‌ها (z16 — موتور تطبیقی) ───
# بنچمارک زنده نشون داد: تعداد کانکشن ثابت جواب نیست!
#   sem=3 → 58MB/s | sem=16 → 105MB/s | sem=24+ → افت شدید (CDN به‌ازای هر IP محدود می‌کنه)
# پس: worker-pool داینامیک + hill-climbing روی throughput واقعی.
# نقطه‌ی بهینه برای هر CDN متفاوته و خودِ موتور پیداش می‌کنه.
SEGMENT_CONCURRENCY = int(os.environ.get("IMDB_SEG_CONCURRENCY", "12"))   # نقطه‌ی شروع
SESSION_MAX_CLIENTS = int(os.environ.get("IMDB_MAX_CLIENTS", "48"))       # حداکثر curl handle همزمان
SEG_CONCURRENCY_MIN = int(os.environ.get("IMDB_SEG_MIN", "4"))            # کف concurrency
SEG_CONCURRENCY_MAX = int(os.environ.get("IMDB_SEG_MAX", "40"))           # سقف concurrency
SEG_ADAPT_ENABLED   = os.environ.get("IMDB_SEG_ADAPT", "1") != "0"        # خاموش/روشن کردن تطبیق
SEG_ADAPT_WINDOW    = float(os.environ.get("IMDB_SEG_WINDOW", "4"))       # ثانیه بین اندازه‌گیری‌ها
SEG_TIMEOUT         = int(os.environ.get("IMDB_SEG_TIMEOUT", "45"))       # تایم‌اوت هر سگمنت
SEG_RETRIES         = int(os.environ.get("IMDB_SEG_RETRIES", "5"))        # تلاش مجدد هر سگمنت
# 🛡 z17 — ضد فریز: توقف در ۹۹٪ (مثل 1710/1714) سه علت داشت:
#   1) درخواست curl_cffi که هیچ‌وقت برنمی‌گرده → _drain برای همیشه منتظر می‌مونه
#   2) کرش غیرمنتظره‌ی worker بعد از برداشتن آیتم از صف → آیتم گم می‌شه → done+failed هیچ‌وقت به expected نمی‌رسه
#   3) concat بلاک‌کننده‌ی event loop (subprocess.run چند دقیقه‌ای) → بات مرده به نظر می‌رسه
SEG_HARD_BACKSTOP   = float(os.environ.get("IMDB_SEG_BACKSTOP", "15"))    # ⏱ سقف سخت wait_for روی هر درخواست (SEG_TIMEOUT + این)
SEG_STALL_TIMEOUT   = float(os.environ.get("IMDB_SEG_STALL", "150"))      # ⏱ اگه این‌قدر ثانیه هیچ پیشرفتی نبود → راند force-finish می‌شه
# 🆕 z29 — استریم + Resume بایتی:
# لاگ Avengers (Vidzee/cdn2.ngcorp.dad) نشون داد سگمنت‌های ۱-۱۳ مگابایتی با throttle
# ~۸۰-۱۳۰KB/s به‌ازای هر کانکشن، در سقف SEG_TIMEOUT=45s هیچ‌وقت کامل نمی‌شن و
# بایت‌های دریافت‌شده در هر تلاش دور ریخته می‌شن → سگمنت همیشه fail می‌شه.
# راه‌حل: استریم چانک‌به‌چانک روی دیسک (.part) + ادامه با «Range: bytes=<دریافتی>-»
SEG_ATTEMPT_CAP = float(os.environ.get("IMDB_SEG_ATTEMPT_CAP", "300"))  # 🆕 z29: سقف زمان هر تلاش استریم (ثانیه)
SEG_STALL_READ  = float(os.environ.get("IMDB_SEG_STALL_READ", "20"))   # 🆕 z29: بدون پیشرفت در خواندن چانک → تلاش قطع، بعداً resume
ALLOW_REENCODE      = os.environ.get("IMDB_ALLOW_REENCODE", "0") == "1"   # re-encode چندساعته پیش‌فرض خاموشه

# TMDB API key که vidzee و چند سرور دیگه استفاده می‌کنن (به صورت embedded در JS اون‌هاست).
# این کلید public در نظر گرفته شده و در فرانت‌اند سایت‌های embed استفاده می‌شه.
_TMDB_API_KEY = "adc48d20c0956934fb224de5c40bb85d"

# ─── Server list (به ترتیب اولویت) ──────────────────────────

# هر سرور یک dict با فیلدهای زیر است:
#   id: شناسه سرور (s1, s2, ...)
#   name: نام سرور برای نمایش
#   prefer_imdb: اگه True باشه، imdb_id به جای tmdb_id استفاده می‌شه
#   quality_hint: توضیح کیفیت پیش‌فرض (برای نمایش اولیه)
_SERVERS = [
    {"id": "s2", "name": "Vidzee",     "prefer_imdb": False, "quality_hint": "Auto"},
    {"id": "s1", "name": "Videasy",    "prefer_imdb": False, "quality_hint": "Auto (multi-quality)"},
    {"id": "s3", "name": "Vidking",    "prefer_imdb": False, "quality_hint": "Auto (multi-quality)"},
    {"id": "s9", "name": "2Embed",     "prefer_imdb": True,  "quality_hint": "Auto (MP4)"},
    {"id": "s7", "name": "GarageBand", "prefer_imdb": True,  "quality_hint": "Auto"},
]

# 🆕 اسم همه‌ی سرورها برای bot.py — منوی سرور همیشه همه رو نشون میده
# (تأییدشده‌ها با ✅ + سرورهایی که پروب جواب نداد با 🧪 امتحانی)
SERVER_NAMES = [s["name"] for s in _SERVERS]


# ═══════════════════════════════════════════════════════════
#   🆕 VidsrcMe multi-host (Castletv و بقیه‌ی هاست‌ها)
#   API قدیمی ربات (data.vidsrcme.ru) چند m3u8 رمزنگاری‌شده می‌ده —
#   بعد از decrypt هر URL = یه هاست/سرور با کیفیت‌های واقعی ۴۸۰/۷۲۰/۱۰۸۰
# ═══════════════════════════════════════════════════════════

# هدرهای مشترک هاست‌های vidsrcme (مثل _SEG_HEADERS در vidsrc_downloader)
_VM_HEADERS = {
    "Origin": "https://cloudorchestranova.com",
    "Referer": "https://cloudorchestranova.com/",
}

# کش هاست‌ها: key = "tt..|sSeE" یا "tt..|movie" → {name: entry}
_VIDSRCME_CACHE = {}

# 🆕 z22 کش منابع ایرانی (FJ 🇮🇷 / Film2Movie / دوستی‌ها / فارسی‌لند):
# key = _vm_key(...) → {server_name: entry} — تا download_with_quality بتونه
# بعد از انتخاب کاربر مستقیم از کش برداره (همون الگوی _VIDSRCME_CACHE)
_IRAN_CACHE = {}
_IRAN_CACHE_MAX = 48


def _iran_cache_put(key: str, entries: list) -> None:
    """ذخیره‌ی entry های ایرانی در کش (بدون بازنویسی entry های قبلی همین key)."""
    if not entries:
        return
    bucket = _IRAN_CACHE.setdefault(key, {})
    for e in entries:
        nm = e.get("server") or "FJ"
        bucket[nm] = e
    if len(_IRAN_CACHE) > _IRAN_CACHE_MAX:
        for k in list(_IRAN_CACHE.keys())[:-_IRAN_CACHE_MAX]:
            _IRAN_CACHE.pop(k, None)


def _iran_cache_get(key: str) -> dict:
    return _IRAN_CACHE.get(key, {})


def _vm_key(imdb_id: str, season=None, episode=None) -> str:
    imdb_id = imdb_id if imdb_id.startswith("tt") else f"tt{imdb_id}"
    if season is not None and episode is not None:
        return f"{imdb_id}|s{season}e{episode}"
    return f"{imdb_id}|movie"


def _pretty_host(url: str) -> str:
    """اسم خوانا از هاست URL — castletv.net → Castletv، v2.vidsrc.me → Vidsrc"""
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return ""
    labels = [l for l in host.split(".") if l]
    label = labels[0] if labels else ""
    for l in labels:
        if len(l) > 3 and not any(c.isdigit() for c in l):
            label = l
            break
    label = re.sub(r"[^a-z]", "", label) or (labels[0] if labels else host)
    return label.capitalize()


async def _probe_vidsrcme_hosts(imdb_id: str, season=None, episode=None) -> List[dict]:
    """هاست‌های vidsrcme (مثل Castletv) به‌صورت probe entry.

    ۱) get_stream_info از vidsrc_downloader → API + wasm decrypt → لیست m3u8 ها
    ۲) برای هر هاست: generate.php → token → master.m3u8 → variant ها
    نتیجه cache می‌شه تا download_with_quality بتونه مستقیم ازش برداره.
    Returns: [{"server": "Castletv", "type": "hls", "headers": ..., "url": ...,
               "qualities": [...], "unverified": bool, "vidsrcme": True}]
    """
    key = _vm_key(imdb_id, season, episode)
    if key in _VIDSRCME_CACHE:
        return list(_VIDSRCME_CACHE[key].values())
    try:
        from vidsrc_downloader import (get_stream_info as _vm_info,
                                       _fetch_token as _vm_token,
                                       _apply_token as _vm_apply)
    except Exception as e:
        logger.warning("[IMDBPlay] vidsrcme module unavailable: %s", e)
        return []
    try:
        info = await _vm_info(imdb_id, season, episode)
        urls = list(info.stream_urls or []) if info else []
    except Exception as e:
        logger.warning("[IMDBPlay] vidsrcme API failed: %s", e)
        return []
    if not urls:
        logger.info("[IMDBPlay] vidsrcme: no stream_urls for %s", key)
        return []

    entries = {}
    # 🆕 اسم‌گذاری: هاست‌های vidsrcme می‌چرخن (castletv/loquaciouslexicon/...)
    # اگه هر ۳ slot هم‌هاستن → «VidSrc 1..N»؛ اگه هاست‌ها فرق داشتن → اسم هاست
    hosts = {_pretty_host(u).lower() for u in urls}
    same_host = len(hosts) <= 1

    async def _one(idx_u):
        idx, u = idx_u
        if same_host:
            name = f"VidSrc {idx + 1}"
        else:
            name = _pretty_host(u) or "VidSrc"
            base, i = name, 2
            while name in entries:
                name = f"{base}{i}"
                i += 1
        entry = {"server": name, "type": "hls", "headers": dict(_VM_HEADERS),
                 "url": u, "qualities": [], "unverified": False, "vidsrcme": True}
        entries[name] = entry  # رزرو اسم (جلوگیری از دوبلی در پروب موازی)
        try:
            async with AsyncSession() as s:
                token = await _vm_token(s, u)
                master = _vm_apply(u, token)
                entry["url"] = master
                r = await s.get(master, impersonate=_BROWSER_IMPERSONATE,
                                timeout=15, headers=_VM_HEADERS)
            if r.status_code == 200 and "#EXT-X-STREAM-INF:" in r.text:
                variants = _parse_master_m3u8(r.text)
                variants.sort(key=lambda v: -v[1])
                for vu, bw, res in variants:
                    entry["qualities"].append({
                        "label": _resolution_to_label(res, bw),
                        "url": _make_absolute(master, vu),
                        "bandwidth": bw,
                        "resolution": res,
                    })
            else:
                logger.warning("[IMDBPlay] vidsrcme %s master HTTP %s", name,
                               getattr(r, "status_code", "?"))
        except Exception as e:
            logger.warning("[IMDBPlay] vidsrcme %s probe failed: %s", name, e)
        if not entry["qualities"]:
            # هاست هست ولی variant نشد → Auto امتحانی
            entry["unverified"] = True
            entry["qualities"].append({"label": "Auto", "url": entry["url"],
                                       "bandwidth": 0, "resolution": ""})

    await asyncio.gather(*[_one(t) for t in enumerate(urls[:6])])
    if entries:
        _VIDSRCME_CACHE[key] = entries
        if len(_VIDSRCME_CACHE) > 24:
            for k in list(_VIDSRCME_CACHE.keys())[:-24]:
                _VIDSRCME_CACHE.pop(k, None)
    logger.info("[IMDBPlay] vidsrcme %s → %d host(s): %s", key, len(entries),
                {n: [q["label"] for q in e["qualities"]] for n, e in entries.items()})
    return list(entries.values())


# ═══════════════════════════════════════════════════════════
#   TMDB / IMDb conversion
# ═══════════════════════════════════════════════════════════


_TMDB_ID_CACHE: Dict[str, Optional[str]] = {}


async def _tmdb_search_id(title: str, year=None, is_tv: bool = False) -> Optional[str]:
    """🆕 z35: جستجوی TMDB بر اساس عنوان — fallback وقتی find خالیه
    (TMDB find برای بعضی ttها مثل tt4154795/Infinity War خالی برمی‌گرده!)."""
    if not title or not str(title).strip():
        return None
    endpoint = "search/tv" if is_tv else "search/movie"
    params = {"api_key": _TMDB_API_KEY, "query": str(title).strip(), "include_adult": "false"}
    if year:
        try:
            y = int(re.match(r"(\d{4})", str(year)).group(1))
            params["year" if not is_tv else "first_air_date_year"] = str(y)
        except (AttributeError, ValueError):
            pass
    try:
        async with AsyncSession() as s:
            r = await s.get(f"https://api.themoviedb.org/3/{endpoint}", params=params,
                            impersonate=_BROWSER_IMPERSONATE, timeout=15,
                            headers={"User-Agent": _USER_AGENT})
            if r.status_code != 200:
                logger.warning("TMDB %s HTTP %d for '%s'", endpoint, r.status_code, title)
                return None
            results = r.json().get("results") or []
            if not results:
                # بدون سال یک بار دیگه امتحان کن (سال ذکرشده گاهی با سال TMDB نمی‌خونه)
                if year:
                    params.pop("year", None)
                    params.pop("first_air_date_year", None)
                    r = await s.get(f"https://api.themoviedb.org/3/{endpoint}", params=params,
                                    impersonate=_BROWSER_IMPERSONATE, timeout=15,
                                    headers={"User-Agent": _USER_AGENT})
                    if r.status_code != 200:
                        return None
                    results = r.json().get("results") or []
            if not results:
                return None
            # ترجیح: اولین نتیجه (TMDB خودش بر اساس popularity مرتب می‌کنه) —
            # برای جستجوی عنوانِ دقیقِ فیلم، اولین نتیجه تقریباً همون هست
            pick = results[0]
            tmdb_id = str(pick.get("id", "") or "")
            if tmdb_id:
                logger.info("TMDB %s '%s' (%s) -> %s (%s)",
                            endpoint, title, year or "-", tmdb_id, pick.get("title") or pick.get("name"))
            return tmdb_id or None
    except Exception as e:
        logger.warning("TMDB search failed for '%s': %s", title, e)
        return None


async def _get_tmdb_id(imdb_id: str, title: str = None, year=None,
                       is_tv: bool = False) -> Optional[str]:
    """تبدیل imdb_id به tmdb_id — 🆕 z35: find → (fallback) search با عنوان.

    TMDB find برای بعضی آیدی‌ها (مثلاً tt4154795 = Avengers: Infinity War)
    پاسخ «خالی» می‌ده؛ اونجا با عنوان+سال جستجو می‌کنیم.
    """
    if not imdb_id:
        return None
    if not imdb_id.startswith("tt"):
        imdb_id = f"tt{imdb_id}"

    # 🆕 z35 — کش مثبت درون-پردازشی
    if imdb_id in _TMDB_ID_CACHE and _TMDB_ID_CACHE[imdb_id]:
        return _TMDB_ID_CACHE[imdb_id]

    url = f"https://api.themoviedb.org/3/find/{imdb_id}?api_key={_TMDB_API_KEY}&external_source=imdb_id"
    try:
        async with AsyncSession() as s:
            r = await s.get(url, impersonate=_BROWSER_IMPERSONATE, timeout=15,
                            headers={"User-Agent": _USER_AGENT})
            if r.status_code == 200:
                d = r.json()
                movies = d.get("movie_results", [])
                tv = d.get("tv_results", [])
                if movies:
                    tmdb_id = str(movies[0].get("id", ""))
                    logger.info("TMDB find %s -> movie %s", imdb_id, tmdb_id)
                    _TMDB_ID_CACHE[imdb_id] = tmdb_id
                    return tmdb_id
                if tv:
                    tmdb_id = str(tv[0].get("id", ""))
                    logger.info("TMDB find %s -> tv %s", imdb_id, tmdb_id)
                    _TMDB_ID_CACHE[imdb_id] = tmdb_id
                    return tmdb_id
                logger.warning("TMDB find %s: no results", imdb_id)
            else:
                logger.warning("TMDB find HTTP %d for %s", r.status_code, imdb_id)
    except Exception as e:
        logger.warning("TMDB find failed: %s", e)

    # 🆕 z35 — fallback: جستجو با عنوان (اگه caller عنوان رو داده باشه)
    tmdb_id = await _tmdb_search_id(title, year, is_tv)
    if tmdb_id:
        _TMDB_ID_CACHE[imdb_id] = tmdb_id
    return tmdb_id


# ═══════════════════════════════════════════════════════════
#   Server 2: Vidzee (WASM decryption)
# ═══════════════════════════════════════════════════════════

_VIDZEE_DECRYPT_KEY = "core.vidzee.wtf"
_WASM_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vidzee_decrypt.wasm")
_WASM_INSTANCE = None
_WASM_LOCK = asyncio.Lock()


async def _get_wasm():
    """لود و cache کردن WASM instance برای vidzee decryption."""
    global _WASM_INSTANCE
    if _WASM_INSTANCE is not None:
        return _WASM_INSTANCE
    async with _WASM_LOCK:
        if _WASM_INSTANCE is not None:
            return _WASM_INSTANCE
        if not os.path.exists(_WASM_PATH):
            logger.error("WASM file not found: %s", _WASM_PATH)
            return None
        try:
            with open(_WASM_PATH, "rb") as f:
                wasm_bytes = f.read()
            import wasmtime
            engine = wasmtime.Engine()
            module = wasmtime.Module(engine, wasm_bytes)
            store = wasmtime.Store(engine)

            def _abort(msg_ptr, file_ptr, line, col):
                raise RuntimeError(f"WASM abort at {file_ptr}:{line}:{col}")

            abort_func = wasmtime.Func(
                store,
                wasmtime.FuncType([wasmtime.ValType.i32(), wasmtime.ValType.i32(),
                                   wasmtime.ValType.i32(), wasmtime.ValType.i32()], []),
                _abort,
            )
            instance = wasmtime.Instance(store, module, [abort_func])
            _WASM_INSTANCE = (store, instance, engine)
            logger.info("Vidzee WASM loaded successfully (%d bytes)", len(wasm_bytes))
            return _WASM_INSTANCE
        except Exception as e:
            logger.error("Failed to load Vidzee WASM: %s", e)
            return None


def _wasm_decrypt(store, instance, enc_bytes: bytes, key: str) -> Optional[bytes]:
    """اجرای تابع decrypt از WASM با آرگومان‌های (Uint8Array, String)."""
    try:
        exports = instance.exports(store)
        memory = exports["memory"]
        decrypt_func = exports["decrypt"]
        __new = exports["__new"]
        __pin = exports["__pin"]
        __unpin = exports["__unpin"]

        def mem_write(offset: int, data: bytes):
            memory.write(store, data, offset)

        def mem_read(offset: int, length: int) -> bytes:
            return bytes(memory.read(store, offset, offset + length))

        def mem_write_u32(offset: int, val: int):
            mem_write(offset, struct.pack("<I", val & 0xFFFFFFFF))

        def mem_read_u32(offset: int) -> int:
            return struct.unpack("<I", mem_read(offset, 4))[0]

        # Allocate ArrayBuffer (id=1) for the encrypted bytes
        buf_size = len(enc_bytes)
        backing_ptr = __pin(store, __new(store, buf_size, 1))
        mem_write(backing_ptr, enc_bytes)

        # Create Uint8Array view (id=6, 12 bytes header)
        view_ptr = __new(store, 12, 6)
        mem_write_u32(view_ptr + 0, backing_ptr)
        mem_write_u32(view_ptr + 4, backing_ptr)
        mem_write_u32(view_ptr + 8, buf_size)

        # Allocate string (id=2, UTF-16, 2 bytes per char)
        key_bytes = key.encode("utf-16-le")
        str_ptr = __new(store, len(key_bytes), 2)
        mem_write(str_ptr, key_bytes)

        # Call decrypt(view_ptr, str_ptr)
        result_ptr = decrypt_func(store, view_ptr, str_ptr)
        if hasattr(result_ptr, "value"):
            result_ptr = result_ptr.value
        result_ptr = int(result_ptr)

        try:
            __unpin(store, backing_ptr)
        except Exception:
            pass

        if result_ptr == 0:
            return None

        # Read result Uint8Array header
        result_backing = mem_read_u32(result_ptr + 0)
        result_length = mem_read_u32(result_ptr + 8)

        # Read decrypted bytes
        result = mem_read(result_backing, result_length)
        return result
    except Exception as e:
        logger.error("WASM decrypt error: %s", e)
        return None


async def _vidzee_get_stream(tmdb_id: str, season: Optional[int], episode: Optional[int]) -> Optional[dict]:
    """
    استخراج m3u8 از vidzee با استفاده از API و WASM decryption.

    Returns:
        {"url": m3u8_url, "headers": {Referer, Origin}, "server": "Vidzee", "qualities": [...]}
    """
    if season and episode:
        api_url = f"https://core.vidzee.wtf/streams/tv/{tmdb_id}/{season}/{episode}?s=dcloud&e=1"
    else:
        api_url = f"https://core.vidzee.wtf/streams/movie/{tmdb_id}?s=dcloud&e=1"

    try:
        async with AsyncSession() as s:
            r = await s.get(api_url, impersonate=_BROWSER_IMPERSONATE, timeout=15,
                            headers={"User-Agent": _USER_AGENT,
                                     "Referer": "https://player.vidzee.wtf/",
                                     "Origin": "https://player.vidzee.wtf"})
            if r.status_code != 200:
                logger.warning("vidzee API HTTP %d", r.status_code)
                return None
            d = r.json()
            if "c" not in d:
                logger.warning("vidzee API no 'c' field: %s", d)
                return None
            enc_b64 = d["c"]
            enc_bytes = base64.b64decode(enc_b64)

            wasm = await _get_wasm()
            if not wasm:
                logger.error("Vidzee WASM not available")
                return None
            store, instance, engine = wasm
            decrypted = _wasm_decrypt(store, instance, enc_bytes, _VIDZEE_DECRYPT_KEY)
            if not decrypted:
                logger.error("Vidzee decryption failed")
                return None
            try:
                result = json.loads(decrypted.decode("utf-8"))
            except Exception as e:
                logger.error("Vidzee decrypted JSON parse error: %s", e)
                return None
            url = result.get("url")
            if not url:
                logger.warning("Vidzee no url in decrypted: %s", result)
                return None
            logger.info("Vidzee %s -> %s", tmdb_id, url[:80])
            return {
                "url": url,
                "headers": {"Referer": "https://player.vidzee.wtf/", "Origin": "https://player.vidzee.wtf"},
                "server": "Vidzee",
            }
    except Exception as e:
        logger.warning("Vidzee error: %s", e)
        return None


# ═══════════════════════════════════════════════════════════
#   Servers 1 & 3: Videasy / Vidking (XOR cipher with seed)
# ═══════════════════════════════════════════════════════════

# هر دو سرور از API speedracelight استفاده می‌کنن.
# تفاوت فقط در Origin/Referer هدرهاست.
_VIDEASY_ORIGIN = "https://player.videasy.to"
_VIDKING_ORIGIN = "https://www.vidking.net"

# Constants for the XOR cipher (reverse-engineered from chunk 8351 of videasy JS)
_F = [
    1116352408, 1899447441, 3049323471, 3921009573, 961987163, 1508970993,
    2453635748, 2870763221, 3624381080, 310598401, 607225278, 1426881987,
    1925078388, 2162078206, 2614888103, 3248222580,
]
_H = b"mvm1"
_MASK32 = 0xFFFFFFFF
_GOLDEN = 0x9E3779B9

# Default provider order — سعی می‌کنیم با چندین provider در هر سرور
_SPEEDRACELIGHT_PROVIDERS = ["cdn", "hdmovie", "vsrc", "m4uhd", "superflix", "lamovie", "downloader2"]


def _w(e: int) -> int:
    """murmur3-like finalizer."""
    e &= _MASK32
    e ^= e >> 16
    e = (e * 2246822507) & _MASK32
    e ^= e >> 13
    e = (e * 3266489909) & _MASK32
    e ^= e >> 16
    return e & _MASK32


def _rotl32(e: int, t: int) -> int:
    """32-bit left-rotate."""
    e &= _MASK32
    t &= 31
    if t == 0:
        return e
    return ((e << t) | (e >> (32 - t))) & _MASK32


def _fnv1a_then_w(s: str) -> int:
    """FNV-1a 32-bit hash + finalizer."""
    t = 2166136261
    for ch in s:
        t = ((t ^ ord(ch)) * 16777619) & _MASK32
    return _w(t)


def _keystream_gen(seed: str, tmdb_id: str):
    """Build the substitution table {S, acc} for the XOR keystream."""
    S = [0] * 61
    a = _w(_fnv1a_then_w(seed) ^ _w((int(tmdb_id) & _MASK32) ^ _GOLDEN))
    for e in range(8):
        t = a % 61
        a = _rotl32((a + _GOLDEN) & _MASK32, 7 + (7 & e))
        S[t] = (a ^ _w(a)) & _MASK32
        a = _w((a + t) & _MASK32)
    return {"S": S, "acc": _w(2779096485 ^ a)}


def _next4(gen, t: int) -> int:
    """Generate next 32-bit keystream word."""
    S = gen["S"]
    o = gen["acc"]
    n = o % 61
    i = -1 if n < len(S) and S[n] != 0 else 0
    d = S[n] & _MASK32 if n < len(S) else 0
    a = (d ^ ((_GOLDEN * (t + 1)) & _MASK32)) & _MASK32
    l = (((o ^ a) & _MASK32) | (o & a & i)) & _MASK32
    l = (_rotl32((l + o) & _MASK32, 31 & n) ^ _rotl32(o, 31 & ((n * 7) & _MASK32))) & _MASK32
    o = _w((l + _GOLDEN) & _MASK32)
    S[n] = o & _MASK32
    gen["acc"] = o & _MASK32
    return o & _MASK32


def _speedracelight_decrypt(api_response: str, seed: str, tmdb_id: str) -> str:
    """Decrypt speedracelight API response."""
    b64 = api_response.replace("-", "+").replace("_", "/")
    b64 += "=" * (-len(b64) % 4)
    decoded = base64.b64decode(b64)
    n = len(decoded)

    gen = _keystream_gen(seed, tmdb_id)
    keystream = bytearray(n)
    o = 0
    i = 0
    while i < n:
        word = _next4(gen, o); o += 1
        keystream[i] = word & 0xFF; i += 1
        if i < n: keystream[i] = (word >> 8) & 0xFF; i += 1
        if i < n: keystream[i] = (word >> 16) & 0xFF; i += 1
        if i < n: keystream[i] = (word >> 24) & 0xFF; i += 1

    plain = bytes(decoded[i] ^ keystream[i] for i in range(n))
    if plain[:4] != _H:
        raise ValueError("decrypt failed: magic header mismatch (bad seed?)")
    return plain[4:].decode("utf-8")


async def _speedracelight_get_meta(tmdb_id: str, is_tv: bool, season: int = None, episode: int = None) -> Optional[dict]:
    """گرفتن metadata از TMDB proxy."""
    try:
        async with AsyncSession() as s:
            if is_tv:
                url = f"https://db.speedracelight.com/3/tv/{tmdb_id}/season/{season}/episode/{episode}?append_to_response=external_ids&language=en"
            else:
                url = f"https://db.speedracelight.com/3/movie/{tmdb_id}?append_to_response=external_ids&language=en"
            r = await s.get(url, impersonate=_BROWSER_IMPERSONATE, timeout=15,
                            headers={"User-Agent": _USER_AGENT})
            if r.status_code != 200:
                return None
            d = r.json()
            if is_tv:
                # برای TV باید show info هم بگیریم
                show_r = await s.get(f"https://db.speedracelight.com/3/tv/{tmdb_id}?language=en",
                                     impersonate=_BROWSER_IMPERSONATE, timeout=15,
                                     headers={"User-Agent": _USER_AGENT})
                show = show_r.json() if show_r.status_code == 200 else {}
                return {
                    "tmdbId": str(tmdb_id),
                    "imdbId": show.get("external_ids", {}).get("imdb_id", "") if show else "",
                    "title": show.get("name", ""),
                    "year": int((show.get("first_air_date") or "0")[:4]) if show.get("first_air_date") else 0,
                    "totalSeasons": show.get("number_of_seasons", 0),
                    "seasonId": season,
                    "episodeId": episode,
                }
            return {
                "tmdbId": str(d["id"]),
                "imdbId": d.get("imdb_id", ""),
                "title": d.get("title") or d.get("original_title", ""),
                "year": int((d.get("release_date") or "0")[:4]) if d.get("release_date") else 0,
            }
    except Exception as e:
        logger.warning("speedracelight meta failed: %s", e)
        return None


async def _speedracelight_fetch_sources(provider: str, meta: dict, is_tv: bool, origin: str) -> Optional[dict]:
    """Fetch and decrypt sources from speedracelight API."""
    tmdb_id = meta["tmdbId"]
    media_type = "tv" if is_tv else "movie"

    raw_params = {
        "title": meta["title"],
        "mediaType": media_type,
        "year": meta.get("year") or None,
        "totalSeasons": meta.get("totalSeasons") or None,
        "episodeId": meta.get("episodeId") or None,
        "seasonId": meta.get("seasonId") or None,
        "tmdbId": tmdb_id,
        "imdbId": meta.get("imdbId") or None,
        "enc": "2",
    }
    base_params = {k: v for k, v in raw_params.items() if v is not None}

    last_err = None
    for attempt in range(5):
        try:
            # fetch seed (single-use, IP-bound)
            async with AsyncSession() as s:
                seed_r = await s.get(f"https://api.speedracelight.com/seed?mediaId={tmdb_id}",
                                     impersonate=_BROWSER_IMPERSONATE, timeout=15,
                                     headers={"User-Agent": _USER_AGENT,
                                              "Referer": f"{origin}/", "Origin": origin})
                if seed_r.status_code != 200:
                    last_err = f"seed HTTP {seed_r.status_code}"
                    continue
                seed = seed_r.json().get("seed")
                if not seed:
                    last_err = "no seed in response"
                    continue

                params = dict(base_params, seed=seed)
                url = f"https://api.speedracelight.com/{provider}/sources-with-title?{urlencode(params)}"
                r = await s.get(url, impersonate=_BROWSER_IMPERSONATE, timeout=20,
                                headers={"User-Agent": _USER_AGENT,
                                         "Referer": f"{origin}/", "Origin": origin,
                                         "Accept": "application/json, text/plain, */*"})
                if r.status_code == 401:
                    last_err = "seed already consumed"
                    await asyncio.sleep(0.1)
                    continue
                if r.status_code != 200:
                    last_err = f"sources HTTP {r.status_code}"
                    continue

                plain = _speedracelight_decrypt(r.text, seed, tmdb_id)
                data = json.loads(plain)
                if data.get("sources"):
                    return data
                last_err = "no sources in response"
        except Exception as e:
            last_err = str(e)
            await asyncio.sleep(0.1)
    logger.debug("speedracelight provider %s failed: %s", provider, last_err)
    return None


async def _speedracelight_get_stream(server_name: str, origin: str, tmdb_id: str, season: Optional[int], episode: Optional[int]) -> Optional[dict]:
    """استخراج m3u8 از videasy/vidking با استفاده از API و XOR decryption."""
    is_tv = bool(season and episode)
    meta = await _speedracelight_get_meta(tmdb_id, is_tv, season, episode)
    if not meta:
        return None

    # try each provider in order
    for provider in _SPEEDRACELIGHT_PROVIDERS:
        data = await _speedracelight_fetch_sources(provider, meta, is_tv, origin)
        if data and data.get("sources"):
            sources = data["sources"]
            # pick highest quality
            priority = ["2160p", "1080p", "720p", "480p", "360p"]
            chosen = None
            for q in priority:
                for s in sources:
                    if str(s.get("quality", "")).lower() == q:
                        chosen = s
                        break
                if chosen:
                    break
            if not chosen:
                chosen = sources[0]

            url = chosen.get("url")
            if not url:
                continue

            logger.info("%s/%s %s -> %s", server_name, provider, tmdb_id, url[:80])
            return {
                "url": url,
                "headers": {"Referer": f"{origin}/", "Origin": origin, "User-Agent": _USER_AGENT},
                "server": server_name,
                "qualities": [{"label": s.get("quality", "Auto"), "url": s.get("url", "")} for s in sources],
            }
    return None


# ═══════════════════════════════════════════════════════════
#   Server 9: 2Embed (vnest API with custom base64)
# ═══════════════════════════════════════════════════════════

# Custom base64 alphabet used by vidnest.fun API
_VIDNEST_CUSTOM_ALPHABET = "RB0fpH8ZEyVLkv7c2i6MAJ5u3IKFDxlS1NTsnGaqmXYdUrtzjwObCgQP94hoeW+/="
_STANDARD_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
_VIDNEST_TRANS = str.maketrans(_VIDNEST_CUSTOM_ALPHABET, _STANDARD_B64)


def _vidnest_decrypt(data_str: str) -> str:
    """Decrypt vidnest API response using custom-alphabet base64."""
    s = data_str.translate(_VIDNEST_TRANS)
    while len(s) % 4:
        s += "="
    return base64.b64decode(s).decode("utf-8", errors="replace")


async def _2embed_get_stream(tmdb_id: str, imdb_id: str, season: Optional[int], episode: Optional[int]) -> Optional[dict]:
    """استخراج stream از 2embed.cc (sub-source: vnest/vidlink)."""
    is_tv = bool(season and episode)
    media_type = "tv" if is_tv else "movie"

    # Try multiple providers
    for provider in ["vidlink", "videasy", "hollymoviehd", "nextgencloudfabric", "klikxxi"]:
        try:
            if is_tv:
                api_url = f"https://new.vidnest.fun/{provider}/{media_type}/{tmdb_id}/{season}/{episode}"
            else:
                api_url = f"https://new.vidnest.fun/{provider}/{media_type}/{tmdb_id}"

            async with AsyncSession() as s:
                # Use impersonate="chrome" to bypass Cloudflare and rate limits
                r = await s.get(api_url, impersonate=_BROWSER_IMPERSONATE, timeout=15,
                                headers={"User-Agent": _USER_AGENT,
                                         "Referer": "https://cineby.hair/",
                                         "Origin": "https://cineby.hair",
                                         "Accept": "application/json"})
                if r.status_code != 200:
                    continue
                obj = r.json()
                if not obj.get("encrypted"):
                    continue

                decrypted = json.loads(_vidnest_decrypt(obj["data"]))

                # Parse based on response structure
                streams = []
                if "data" in decrypted and "stream" in decrypted.get("data", {}):
                    stream = decrypted["data"]["stream"]
                    for quality, info in stream.get("qualities", {}).items():
                        streams.append({
                            "quality": f"{quality}p",
                            "url": info["url"],
                            "type": info.get("type", "mp4"),
                            "headers": decrypted.get("headers", {}),
                        })
                    # vidlink provider has built-in captions
                    if stream.get("captions"):
                        return {
                            "url": streams[0]["url"] if streams else None,
                            "headers": streams[0].get("headers", {}) if streams else {},
                            "server": "2Embed",
                            "type": streams[0].get("type", "mp4") if streams else "mp4",
                            "qualities": streams,
                            "subtitles": stream.get("captions", []),
                        }
                elif "url" in decrypted:
                    streams.append({
                        "quality": "auto",
                        "url": decrypted["url"],
                        "type": "hls",
                        "headers": decrypted.get("headers", {}),
                    })
                elif "streams" in decrypted:
                    for s_item in decrypted["streams"]:
                        streams.append({
                            "quality": s_item.get("language", "auto"),
                            "url": s_item["url"],
                            "type": s_item.get("type", "hls"),
                            "headers": s_item.get("headers", {}),
                        })
                elif "all_urls" in decrypted:
                    for i, url in enumerate(decrypted["all_urls"]):
                        streams.append({
                            "quality": f"mirror_{i+1}",
                            "url": url,
                            "type": "hls",
                            "headers": {},
                        })
                elif "sources" in decrypted:
                    for s_item in decrypted["sources"]:
                        streams.append({
                            "quality": s_item.get("quality", "auto"),
                            "url": s_item["url"],
                            "type": "hls" if "hls" in s_item.get("type", "") else s_item.get("type", "hls"),
                            "headers": {},
                        })

                if streams:
                    logger.info("2Embed/%s %s -> %s", provider, tmdb_id, streams[0]["url"][:80])
                    return {
                        "url": streams[0]["url"],
                        "headers": streams[0].get("headers", {}),
                        "server": "2Embed",
                        "type": streams[0].get("type", "mp4"),
                        "qualities": streams,
                    }
        except Exception as e:
            logger.debug("2Embed provider %s failed: %s", provider, e)
            continue
    return None


# ═══════════════════════════════════════════════════════════
#   Server 7: GarageBand (vidsrcme + WASM)
# ═══════════════════════════════════════════════════════════

_GARAGE_EMBED = "https://proxy.garageband.rocks/embed"
_CLOUDORCH_HOST = "https://cloudorchestranova.com"


async def _garageband_get_stream(imdb_id: str, season: Optional[int], episode: Optional[int]) -> Optional[dict]:
    """استخراج m3u8 از proxy.garageband.rocks با استفاده از vidsrcme API + WASM."""
    is_tv = bool(season and episode)
    try:
        async with AsyncSession() as s:
            # Step 1: fetch embed page to get iframe URL
            if is_tv:
                embed_url = f"{_GARAGE_EMBED}/tv/{imdb_id}?autonext=1"
            else:
                embed_url = f"{_GARAGE_EMBED}/movie/{imdb_id}"

            r = await s.get(embed_url, impersonate=_BROWSER_IMPERSONATE, timeout=20,
                            headers={"User-Agent": _USER_AGENT,
                                     "Referer": "https://www.imdbplay.tech/"})
            if r.status_code != 200:
                return None

            m = re.search(r'<iframe[^>]+src="(https://[^"]*cloudorchestranova\.com[^"]+)"', r.text)
            if not m:
                return None
            iframe_url = m.group(1).replace("&amp;", "&")

            # Step 2: fetch iframe to get window.CONFIG
            r = await s.get(iframe_url, impersonate=_BROWSER_IMPERSONATE, timeout=20,
                            headers={"User-Agent": _USER_AGENT,
                                     "Referer": "https://proxy.garageband.rocks/"})
            if r.status_code != 200:
                return None

            m = re.search(r'window\.CONFIG\s*=\s*(\{[^<]+?\});\s*</script>', r.text, re.DOTALL)
            if not m:
                return None
            config = json.loads(m.group(1).replace("\\u0026", "&"))

            # Step 3: fetch encrypted stream_urls from API
            if config.get("streamBase") and is_tv:
                api_url = f"{config['streamBase']}&season={season}&episode={episode}&stream_urls"
            else:
                api_url = config["api"]

            r = await s.get(api_url, impersonate=_BROWSER_IMPERSONATE, timeout=20,
                            headers={"User-Agent": _USER_AGENT,
                                     "Referer": f"{_CLOUDORCH_HOST}/",
                                     "Accept": "application/json, text/plain, */*"})
            if r.status_code != 200:
                return None
            api_json = r.json()

            # Step 4: download WASM and decrypt
            vs = api_json.get("vs") or {}
            wasm_url = vs.get("wasm_url")
            if not wasm_url:
                # unencrypted
                su = api_json.get("data", {}).get("stream_urls")
                stream_urls = su if isinstance(su, list) else []
            else:
                r = await s.get(wasm_url, impersonate=_BROWSER_IMPERSONATE, timeout=30,
                                headers={"User-Agent": _USER_AGENT,
                                         "Referer": f"{_CLOUDORCH_HOST}/"})
                if r.status_code != 200:
                    return None
                wasm_bytes = r.content

                enc_b64 = api_json["data"]["stream_urls"]
                if isinstance(enc_b64, list):
                    stream_urls = enc_b64
                else:
                    enc = base64.b64decode(enc_b64)
                    try:
                        import wasmtime
                        engine = wasmtime.Engine()
                        module = wasmtime.Module(engine, wasm_bytes)
                        store = wasmtime.Store(engine)
                        instance = wasmtime.Instance(store, module, [])
                        ex = instance.exports(store)
                        ptr = ex["alloc"](store, len(enc))
                        ex["memory"].write(store, enc, ptr)
                        out_len = ex["decrypt"](store, ptr, len(enc))
                        out = bytes(ex["memory"].read(store, ptr + 12, ptr + 12 + out_len))
                        plaintext = out.decode("utf-8")
                        stream_urls = [u for u in plaintext.split("\n") if u]
                    except Exception as e:
                        logger.warning("GarageBand WASM decrypt failed: %s", e)
                        return None

            if not stream_urls:
                return None

            # Step 5: fetch per-host JWT token and get master m3u8
            first_url = stream_urls[0]
            origin = f"{urlparse(first_url).scheme}://{urlparse(first_url).netloc}"

            # fetch token
            try:
                r = await s.get(f"{origin}/generate.php", impersonate=_BROWSER_IMPERSONATE, timeout=15,
                                headers={"User-Agent": _USER_AGENT,
                                         "Referer": f"{_CLOUDORCH_HOST}/"})
                if r.status_code == 200:
                    txt = r.text.strip()
                    if txt and txt[0] in "{[":
                        try:
                            j = json.loads(txt)
                            token = j if isinstance(j, str) else (j.get("token") or j.get("data") or j.get("string") or "")
                        except Exception:
                            token = txt
                    else:
                        token = txt
                else:
                    token = ""
            except Exception:
                token = ""

            # apply token to URL
            if token:
                if "__TOKEN__" in first_url:
                    final_url = first_url.replace("__TOKEN__", token)
                else:
                    sep = "&" if "?" in first_url else "?"
                    final_url = f"{first_url}{sep}token={token}"
            else:
                final_url = first_url

            logger.info("GarageBand %s -> %s", imdb_id, final_url[:80])
            return {
                "url": final_url,
                "headers": {"Referer": f"{_CLOUDORCH_HOST}/", "Origin": _CLOUDORCH_HOST, "User-Agent": _USER_AGENT},
                "server": "GarageBand",
            }
    except Exception as e:
        logger.warning("GarageBand error: %s", e)
        return None


# ═══════════════════════════════════════════════════════════
#   Stream routing: try each server in order
# ═══════════════════════════════════════════════════════════


async def _get_stream_for_server(server: dict, tmdb_id: str, imdb_id: str, season: Optional[int], episode: Optional[int]) -> Optional[dict]:
    """گرفتن stream info از یک سرور خاص."""
    sid = server["id"]
    if sid == "ir":  # 🆕 z22 منابع ایرانی (FJ 🇮🇷 / Film2Movie / ...) — از کشِ پروب
        return _iran_cache_get(_vm_key(imdb_id, season, episode)).get(
            server.get("ir_name") or server.get("name", ""))
    if sid == "vm":  # 🆕 هاست‌های vidsrcme (مثل Castletv) — از کشِ پروب
        return _VIDSRCME_CACHE.get(_vm_key(imdb_id, season, episode), {}).get(
            server.get("vm_name") or server.get("name", ""))
    if sid == "s2":  # Vidzee
        return await _vidzee_get_stream(tmdb_id, season, episode)
    if sid == "s1":  # Videasy
        return await _speedracelight_get_stream("Videasy", _VIDEASY_ORIGIN, tmdb_id, season, episode)
    if sid == "s3":  # Vidking
        return await _speedracelight_get_stream("Vidking", _VIDKING_ORIGIN, tmdb_id, season, episode)
    if sid == "s9":  # 2Embed
        return await _2embed_get_stream(tmdb_id, imdb_id, season, episode)
    if sid == "s7":  # GarageBand
        return await _garageband_get_stream(imdb_id, season, episode)
    return None


async def _get_first_working_stream(tmdb_id: str, imdb_id: str, season: Optional[int],
                                    episode: Optional[int],
                                    expected_s: Optional[float] = None) -> Optional[dict]:
    """
    امتحان همه سرورها به ترتیب و برگرداندن اولین نتیجه موفق.
    سرورها به ترتیب اولویت در _SERVERS تعریف شدن.
    🆕 z35: اگه expected_s داده شده باشه، استریم‌هایی که مدتشون با runtime مرجع
    نمی‌خونه (فیلم اشتباه/کات متفاوت) رد می‌شن و سرور بعدی امتحان می‌شه.
    """
    for server in _SERVERS:
        try:
            logger.info("[IMDBPlay] Trying server %s (%s)...", server["id"], server["name"])
            stream = await _get_stream_for_server(server, tmdb_id, imdb_id, season, episode)
            if stream and stream.get("url"):
                if expected_s and not await _stream_duration_ok(
                        stream, expected_s, f"auto:{server['name']}"):
                    stream = None
            if stream and stream.get("url"):
                logger.info("[IMDBPlay] ✓ Server %s succeeded", server["name"])
                return stream
            else:
                logger.info("[IMDBPlay] ✗ Server %s returned no stream", server["name"])
        except Exception as e:
            logger.warning("[IMDBPlay] ✗ Server %s exception: %s", server["name"], e)
            continue
    logger.error("[IMDBPlay] All servers failed for tmdb=%s imdb=%s", tmdb_id, imdb_id)
    # 🆕 z22 — آخرین شانس: entry های ایرانی کش‌شده (FJ 🇮🇷 / Film2Movie / ...)
    # برای عناوین ایرانی که فقط منابع ایرانی دارن، Auto هم باید کار کنه.
    for _nm, _e in _iran_cache_get(_vm_key(imdb_id, season, episode)).items():
        if _e.get("url"):
            logger.info("[IMDBPlay] ✓ Iran server %s (cached) succeeded", _nm)
            return _e
    return None


# ═══════════════════════════════════════════════════════════
#   🆕 z35 — Sanity-check مدت استریم + runtime مرجع TMDB
#   بعضی سرورها/منابع گاهی فیلم اشتباه یا کات متفاوت می‌دن؛ مدتِ playlist
#   (جمع EXTINF) باید با runtime مرجع TMDB بخونه — وگرنه سرور رد می‌شه.
# ═══════════════════════════════════════════════════════════

_RUNTIME_CACHE: Dict[tuple, Optional[float]] = {}
_DURATION_TOL_S = 90.0  # runtime در TMDB دقیقه‌گرد شده؛ ±90s امنه


async def _expected_runtime_seconds(tmdb_id, season: Optional[int] = None,
                                    episode: Optional[int] = None) -> Optional[float]:
    """runtime مرجع (ثانیه) از TMDB — فیلم یا قسمت سریال. کش‌شده."""
    if not tmdb_id:
        return None
    key = (str(tmdb_id), int(season or 0), int(episode or 0))
    if key in _RUNTIME_CACHE:
        return _RUNTIME_CACHE[key]
    sec: Optional[float] = None
    try:
        async with AsyncSession() as s:
            if season is not None and episode is not None:
                url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{int(season)}/episode/{int(episode)}"
            else:
                url = f"https://api.themoviedb.org/3/movie/{tmdb_id}"
            r = await s.get(url, params={"api_key": _TMDB_API_KEY},
                            impersonate=_BROWSER_IMPERSONATE, timeout=15,
                            headers={"User-Agent": _USER_AGENT})
            if r.status_code == 200:
                d = r.json()
                rt = d.get("runtime")
                if isinstance(rt, list):  # بعضی قسمت‌ها episode_run_time لیست برمی‌گردونن
                    rt = rt[0] if rt else None
                if not rt:
                    rt = d.get("episode_run_time") if isinstance(d.get("episode_run_time"), int) else None
                if rt and float(rt) > 0:
                    sec = float(rt) * 60.0
    except Exception as e:
        logger.debug("[z35] runtime fetch failed for tmdb=%s: %s", tmdb_id, e)
    _RUNTIME_CACHE[key] = sec
    return sec


def _sum_media_playlist_durations(text: str) -> float:
    """جمع EXTINF های یک media playlist (پایه‌ی چک مدت)."""
    total = 0.0
    pending: Optional[float] = None
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("#EXTINF:"):
            try:
                pending = float(line.split(":", 1)[1].split(",")[0])
            except (ValueError, IndexError):
                pending = None
        elif line and not line.startswith("#"):
            if pending is not None:
                total += pending
                pending = None
    return total


def _pick_variant_url(master_text: str, base_url: str, prefer_height: int = 720) -> Optional[str]:
    """انتخاب نزدیک‌ترین variant به ارتفاع خواسته از master playlist (URL مطلق)."""
    variants = _parse_master_m3u8(master_text)
    if not variants:
        return None

    def _h(resolution: str) -> int:
        m = re.search(r"(\d+)x(\d+)", resolution or "")
        return int(m.group(2)) if m else 0

    best = min(variants, key=lambda v: abs(_h(v[2]) - (prefer_height or 720)))
    return _make_absolute(base_url, best[0])


async def _probe_stream_duration(url: str, headers: Optional[dict],
                                 prefer_height: int = 720) -> Optional[float]:
    """مدت استریم HLS (ثانیه) فقط با fetch پلی‌لیست — بدون دانلود محتوا.
    🆕 z35: ۲ تلاش (CDNها گاهی یک‌بار 5xx/timeout می‌دن)."""
    for attempt in range(2):
        try:
            async with AsyncSession() as s:
                r = await s.get(url, impersonate=_BROWSER_IMPERSONATE, timeout=25,
                                headers=headers or {})
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}")
                txt = r.text
                if "#EXT-X-STREAM-INF" in txt:
                    vurl = _pick_variant_url(txt, url, prefer_height)
                    if not vurl:
                        raise RuntimeError("no variant")
                    r2 = await s.get(vurl, impersonate=_BROWSER_IMPERSONATE, timeout=25,
                                     headers=headers or {})
                    if r2.status_code != 200:
                        raise RuntimeError(f"variant HTTP {r2.status_code}")
                    txt = r2.text
                total = _sum_media_playlist_durations(txt)
                if total > 0:
                    return total
                raise RuntimeError("empty playlist")
        except Exception as e:
            if attempt == 0:
                logger.debug("[z35] _probe_stream_duration retry: %s", e)
                await asyncio.sleep(0.8)
            else:
                logger.debug("[z35] _probe_stream_duration failed: %s", e)
    return None


async def _stream_duration_ok(stream: dict, expected_s: Optional[float], label: str = "") -> bool:
    """🆕 z35 — اگه مدت استریم با runtime مرجع نخونه (فیلم اشتباه/کات متفاوت)، رد."""
    if not expected_s or expected_s <= 0:
        return True
    if not stream or not stream.get("url"):
        return True
    if stream.get("type", "hls") != "hls":
        return True  # MP4 مستقیم — پروب ارزون نداریم، بلاک نکن
    dur = await _probe_stream_duration(stream["url"], stream.get("headers") or {})
    if not dur or dur <= 0:
        return True  # نشد بخونیم → مانع دانلود نشو
    dev = abs(dur - expected_s)
    ok = dev <= _DURATION_TOL_S
    logger.info("[z35] duration check %s: stream=%.0fs expected=%.0fs (dev=%.0fs) → %s",
                label or stream.get("server", "?"), dur, expected_s, dev,
                "OK" if ok else "REJECT ✗")
    return ok


def _compute_gap_spans(segments: List[Tuple[str, float]],
                       seg_paths: list) -> List[Tuple[float, float]]:
    """🆕 z35 — نقشه‌ی «پرش محتوایی» از سگمنت‌های گم‌شده:
    [(شروع پرش در زمان‌محور playlist، طول پرش), ...] — ورودی برای gap-resync ساب."""
    spans: List[Tuple[float, float]] = []
    t = 0.0
    for i, (_u, d) in enumerate(segments):
        dd = float(d or 0.0)
        if i < len(seg_paths) and not seg_paths[i]:
            spans.append((round(t, 2), round(dd, 2)))
        t += dd
    return spans


# متدهای استخراج برای نمایش به کاربر
_EXTRACTION_METHODS = {
    "s2": "WASM Decrypt (Vidzee API)",
    "s1": "XOR Cipher + Seed (speedracelight API)",
    "s3": "XOR Cipher + Seed (speedracelight API)",
    "s9": "Custom Base64 (vidnest API)",
    "s7": "WASM Decrypt (vidsrcme API)",
}


async def get_server_info(imdb_id: str, season: Optional[int] = None, episode: Optional[int] = None) -> Optional[dict]:
    """
    گرفتن اطلاعات سرور فعال بدون شروع دانلود.

    Returns:
        dict با فیلدهای:
        - server: نام سرور (مثلاً "Vidzee")
        - method: متد استخراج (مثلاً "WASM Decrypt")
        - server_id: شناسه سرور (مثلاً "s2")
        - stream_type: نوع stream ("hls" یا "mp4")
    """
    if not imdb_id:
        return None
    if not imdb_id.startswith("tt"):
        imdb_id = f"tt{imdb_id}"

    tmdb_id = await _get_tmdb_id(imdb_id)
    if not tmdb_id:
        return None

    stream = await _get_first_working_stream(tmdb_id, imdb_id, season, episode)
    if not stream:
        return None

    server_name = stream.get("server", "Unknown")
    # پیدا کردن server_id از روی server_name
    server_id = ""
    for srv in _SERVERS:
        if srv["name"] == server_name:
            server_id = srv["id"]
            break

    method = _EXTRACTION_METHODS.get(server_id, "Unknown")
    stream_type = stream.get("type", "hls")

    return {
        "server": server_name,
        "method": method,
        "server_id": server_id,
        "stream_type": stream_type,
        "tmdb_id": tmdb_id,
    }



# ═══════════════════════════════════════════════════════════
#   m3u8 parsing & quality extraction
# ═══════════════════════════════════════════════════════════


def _parse_master_m3u8(text: str) -> List[Tuple[str, int, str]]:
    """پارس master.m3u8 و استخراج variantها. Returns: list of (url, bandwidth, resolution)."""
    variants = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXT-X-STREAM-INF:"):
            attrs_str = line[len("#EXT-X-STREAM-INF:"):]
            bandwidth = 0
            resolution = ""
            for attr in attrs_str.split(","):
                if attr.startswith("BANDWIDTH="):
                    try:
                        bandwidth = int(attr.split("=", 1)[1])
                    except ValueError:
                        pass
                elif attr.startswith("RESOLUTION="):
                    resolution = attr.split("=", 1)[1].strip()
            i += 1
            while i < len(lines) and not lines[i].strip():
                i += 1
            if i < len(lines):
                url = lines[i].strip()
                if url and not url.startswith("#"):
                    variants.append((url, bandwidth, resolution))
        i += 1
    return variants


def _parse_variant_m3u8(text: str) -> Tuple[List[Tuple[str, float]], Optional[str]]:
    """
    پارس variant.m3u8 (playlist سگمنت‌ها).
    Returns:
        (segments, init_url) where segments is list of (url, duration) and
        init_url is the EXT-X-MAP URI (for fMP4 streams) or None (for MPEG-TS).
    """
    segments = []
    init_url = None
    lines = text.splitlines()
    duration = 0.0
    for line in lines:
        line = line.strip()
        if line.startswith("#EXT-X-MAP:"):
            # parse EXT-X-MAP for fMP4 init segment
            # format: #EXT-X-MAP:URI="https://..."
            m = re.search(r'URI="([^"]+)"', line)
            if m:
                init_url = m.group(1)
        elif line.startswith("#EXTINF:"):
            try:
                duration = float(line[len("#EXTINF:"):].split(",")[0])
            except (ValueError, IndexError):
                duration = 0.0
        elif line and not line.startswith("#"):
            segments.append((line, duration))
            duration = 0.0
    return segments, init_url


def _resolution_to_label(resolution: str, bandwidth: int) -> str:
    """تبدیل resolution (مثل 1920x1080) به label (مثل 1080p)."""
    if not resolution:
        if bandwidth >= 8_000_000:
            return "1080p"
        if bandwidth >= 4_000_000:
            return "720p"
        if bandwidth >= 2_000_000:
            return "480p"
        return "Auto"
    try:
        h = int(resolution.split("x")[1])
    except (ValueError, IndexError):
        return "Auto"
    if h >= 2160:
        return "4K"
    if h >= 1080:
        return "1080p"
    if h >= 720:
        return "720p"
    if h >= 480:
        return "480p"
    if h >= 360:
        return "360p"
    return f"{h}p"


def _make_absolute(base_url: str, url: str) -> str:
    """تبدیل URL نسبی به مطلق."""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("//"):
        parsed = urlparse(base_url)
        return f"{parsed.scheme}:{url}"
    if url.startswith("/"):
        parsed = urlparse(base_url)
        return f"{parsed.scheme}://{parsed.netloc}{url}"
    return urljoin(base_url, url)


# ═══════════════════════════════════════════════════════════
#   Public API: get_qualities
# ═══════════════════════════════════════════════════════════


@dataclass
class Quality:
    """کیفیت موجود برای دانلود."""
    label: str
    bandwidth: int
    resolution: str
    url: str
    server: str = ""
    is_auto: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


async def get_qualities(imdb_id: str, season: Optional[int] = None, episode: Optional[int] = None) -> List[dict]:
    """
    گرفتن لیست کیفیت‌های موجود برای یک فیلم یا قسمت سریال.

    اولویت با سرورهایی هست که کیفیت‌های متعدد دارن (مثل Videasy/Vidking).
    اگه هیچ سرور کیفیت متعدد نداشت، کیفیت Auto از اولین سرور موفق برمی‌گرده.

    Returns:
        لیست dict با فیلدهای:
        - label, bandwidth, resolution, url, server, is_auto
    """
    if not imdb_id:
        return []
    if not imdb_id.startswith("tt"):
        imdb_id = f"tt{imdb_id}"

    tmdb_id = await _get_tmdb_id(imdb_id)
    if not tmdb_id:
        logger.error("Cannot resolve tmdb_id for %s", imdb_id)
        return []

    # امتحان سرورها به ترتیب — اول سروری که کیفیت‌های متعدد داره پیدا کن
    # با retry اگه همه fail شدن
    multi_quality_stream = None
    fallback_stream = None

    for attempt in range(2):  # 2 تلاش کل
        if attempt > 0:
            logger.info("[IMDBPlay] get_qualities: retry attempt %d after delay...", attempt + 1)
            await asyncio.sleep(2)

        for server in _SERVERS:
            try:
                logger.info("[IMDBPlay] get_qualities: Trying server %s... (attempt %d)",
                            server["name"], attempt + 1)
                stream = await _get_stream_for_server(server, tmdb_id, imdb_id, season, episode)
                if not stream or not stream.get("url"):
                    continue

                # اگه fallback نداریم، این رو به‌عنوان fallback نگه دار
                if not fallback_stream:
                    fallback_stream = stream

                # اگه این سرور کیفیت‌های متعدد داره، اون رو انتخاب کن
                if stream.get("qualities") and len(stream["qualities"]) > 1:
                    multi_quality_stream = stream
                    logger.info("[IMDBPlay] ✓ Server %s has %d qualities",
                                server["name"], len(stream["qualities"]))
                    break
                elif stream.get("qualities") and len(stream["qualities"]) == 1:
                    logger.info("[IMDBPlay] Server %s has 1 quality: %s",
                                server["name"], stream["qualities"][0].get("label", "?"))
            except Exception as e:
                logger.warning("[IMDBPlay] Server %s exception: %s", server["name"], e)
                continue

        # اگه چیزی پیدا کردیم، خارج شو
        if multi_quality_stream or fallback_stream:
            break

    # اولویت با multi-quality stream هست
    stream = multi_quality_stream or fallback_stream
    if not stream:
        logger.error("No working stream found for %s (tmdb=%s)", imdb_id, tmdb_id)
        return []

    # اگه سرور خودش لیست کیفیت‌ها رو داده (مثل videasy)
    if stream.get("qualities"):
        qualities = []
        # اگه فقط یک کیفیت داریم و از نوع master m3u8 هست، fetch کن
        for q in stream["qualities"]:
            q_label = q.get("label", q.get("quality", "Auto"))
            q_url = q.get("url", "")
            qualities.append(Quality(
                label=q_label,
                bandwidth=0,
                resolution="",
                url=q_url,
                server=stream.get("server", ""),
                is_auto=q_label.lower() == "auto",
            ).to_dict())
        # اگه چند کیفیت داریم، یه Auto هم اضافه کن (بهترین کیفیت)
        if len(qualities) > 1 and not any(q["label"].lower() == "auto" for q in qualities):
            auto_q = Quality(
                label="Auto",
                bandwidth=0,
                resolution="",
                url=qualities[0]["url"],  # اولین کیفیت (معمولاً بهترین)
                server=stream.get("server", ""),
                is_auto=True,
            )
            qualities.insert(0, auto_q.to_dict())
        return qualities

    # در غیر این صورت، m3u8 رو fetch کن و بررسی کن master یا variant
    m3u8_url = stream["url"]
    headers = {"User-Agent": _USER_AGENT}
    headers.update(stream.get("headers", {}))

    try:
        async with AsyncSession() as s:
            r = await s.get(m3u8_url, impersonate=_BROWSER_IMPERSONATE, timeout=20, headers=headers)
            if r.status_code != 200:
                logger.warning("m3u8 fetch HTTP %d for %s", r.status_code, m3u8_url[:100])
                return []
            text = r.text
    except Exception as e:
        logger.warning("m3u8 fetch failed: %s", e)
        return []

    qualities = []

    if "#EXT-X-STREAM-INF:" in text:
        variants = _parse_master_m3u8(text)
        variants.sort(key=lambda v: -v[1])
        for url, bw, res in variants:
            abs_url = _make_absolute(m3u8_url, url)
            label = _resolution_to_label(res, bw)
            q = Quality(
                label=label,
                bandwidth=bw,
                resolution=res,
                url=abs_url,
                server=stream.get("server", ""),
                is_auto=False,
            )
            qualities.append(q.to_dict())
    else:
        # variant.m3u8 (playlist سگمنت‌ها) — فقط یک کیفیت
        label = "Auto"
        m = re.search(r'/(1080p|720p|480p|360p|4k|2160p)/', m3u8_url, re.IGNORECASE)
        if m:
            label = m.group(1).lower()
            if label == "4k":
                label = "4K"
            elif label == "2160p":
                label = "4K"
        q = Quality(
            label=label,
            bandwidth=0,
            resolution="",
            url=m3u8_url,
            server=stream.get("server", ""),
            is_auto=True,
        )
        qualities.append(q.to_dict())

    logger.info("get_qualities %s -> %d qualities from %s",
                imdb_id, len(qualities), stream.get("server", ""))
    return qualities


# ═══════════════════════════════════════════════════════════
#   Public API: download_with_quality
# ═══════════════════════════════════════════════════════════


async def get_all_server_qualities(imdb_id: str, season: Optional[int] = None, episode: Optional[int] = None,
                                   iran_hints: Optional[dict] = None) -> List[dict]:
    """
    🆕 پروب موازی همه‌ی سرورها → لیست کیفیت‌های هر سرور (برای منوی انتخاب سرور).

    برخلاف get_qualities که با اولین سرورِ چندکیفیتی متوقف میشه، این تابع
    همه‌ی سرورها رو امتحان می‌کنه تا کاربر ببینه کدوم سرور چه کیفیتی داره.

    🆕 z22 — iran_hints: خروجی _imdb_iran_hints از bot.py
        ({"names": [EN, FA...], "year": int, "is_series": bool})
        اگه داده بشه، منابع ایرانی (FJ 🇮🇷 = tdmmo/فیلمجو + Film2Movie +
        دوستی‌ها + فارسی‌لند) «همزمان» با سرورهای CDN پروب می‌شن و entry هاشون
        با فلگ iran=True اول لیست می‌شینه (دانلود مستقیم MP4/MKV، بدون هات‌لینک).
        این همون فیکسِ کرش «TypeError: ... unexpected keyword argument
        'iran_hints'» هست که باعث می‌شد منو تا ابد رو «⏳ در حال بررسی سرورها...»
        گیر کنه و هیچ سروری — حتی قدیمی‌ها — لود نشه.

    Returns:
        لیست dict:
        - server: نام سرور (FJ 🇮🇷/Film2Movie/... یا Vidzee/Videasy/Vidking/2Embed/GarageBand)
        - type: "hls" یا "mp4"
        - headers: هدرهای لازم برای دانلود
        - url: آدرس stream اصلی
        - qualities: [{label, url, bandwidth, resolution}]
          (url هر کیفیت = لینک variant؛ برای MP4 همون url اصلی)
    """
    if not imdb_id:
        return []
    if not imdb_id.startswith("tt"):
        imdb_id = f"tt{imdb_id}"

    tmdb_id = await _get_tmdb_id(imdb_id)
    if not tmdb_id:
        logger.error("Cannot resolve tmdb_id for %s", imdb_id)
        return []

    # 🆕 z22 — پروب منابع ایرانی (با عایق خطا؛ هیچ‌وقت فلوی اصلی رو نمی‌شکنه)
    async def _iran_probe() -> list:
        hints = iran_hints if isinstance(iran_hints, dict) else {}
        names = [str(n).strip() for n in (hints.get("names") or [])
                 if n and len(str(n).strip()) >= 2][:4]
        if not names:
            return []
        try:
            from searcher.iranserver.iranhub import probe_iran_sources
            entries = await probe_iran_sources(
                imdb_id, names, hints.get("year"),
                bool(hints.get("is_series")),
                season=season, episode=episode, timeout=12.0,
            )
            entries = [e for e in (entries or []) if e and e.get("url")]
            if entries:
                _iran_cache_put(_vm_key(imdb_id, season, episode), entries)
                logger.info("[IMDBPlay] iran sources %s → %d entry(ies): %s",
                            imdb_id, len(entries),
                            [(e.get("server"), [q.get("label") for q in e.get("qualities", [])])
                             for e in entries])
            return entries
        except Exception as e:
            logger.warning("[IMDBPlay] iran probe failed (non-fatal): %s", e)
            return []

    async def _probe(server: dict, round_no: int = 1) -> Optional[dict]:
        try:
            stream = await _get_stream_for_server(server, tmdb_id, imdb_id, season, episode)
            if not stream or not stream.get("url"):
                return None
            entry = {
                "server": stream.get("server") or server["name"],
                "type": stream.get("type", "hls"),
                "headers": stream.get("headers", {}),
                "url": stream["url"],
                "qualities": [],
                # 🆕 True = stream داریم ولی لیست variant ها نتونست گرفته بشه → فقط Auto
                "unverified": False,
            }
            # ۱) سرور خودش لیست کیفیت داده (مثل Videasy/Vidking)
            if stream.get("qualities"):
                for q in stream["qualities"]:
                    entry["qualities"].append({
                        "label": q.get("label", q.get("quality", "Auto")),
                        "url": q.get("url", "") or entry["url"],
                        "bandwidth": 0,
                        "resolution": q.get("resolution", ""),
                    })
                if entry["qualities"]:
                    return entry
                # لیست خالی بود → Auto (لینک stream معتبره)
                entry["qualities"].append({"label": "Auto", "url": entry["url"],
                                           "bandwidth": 0, "resolution": ""})
                return entry
            # ۲) MP4 لیست variant نداره — بدون HTTP GET از روی URL برچسب بگیر
            if entry["type"] == "mp4":
                label = "Auto"
                m = re.search(r'/(1080p|720p|480p|360p|4k|2160p)/', entry["url"], re.IGNORECASE)
                if m:
                    label = m.group(1).lower()
                    if label in ("4k", "2160p"):
                        label = "4K"
                entry["qualities"].append({"label": label, "url": entry["url"],
                                           "bandwidth": 0, "resolution": ""})
                return entry
            # ۳) m3u8 رو بگیر و پارس کن
            try:
                headers = {"User-Agent": _USER_AGENT}
                headers.update(entry["headers"])
                async with AsyncSession() as s:
                    r = await s.get(entry["url"], impersonate=_BROWSER_IMPERSONATE,
                                    timeout=15, headers=headers)
                if r.status_code == 200:
                    text = r.text
                    if "#EXT-X-STREAM-INF:" in text:
                        variants = _parse_master_m3u8(text)
                        variants.sort(key=lambda v: -v[1])
                        for u, bw, res in variants:
                            entry["qualities"].append({
                                "label": _resolution_to_label(res, bw),
                                "url": _make_absolute(entry["url"], u),
                                "bandwidth": bw,
                                "resolution": res,
                            })
                    else:
                        label = "Auto"
                        m = re.search(r'/(1080p|720p|480p|360p|4k|2160p)/', entry["url"], re.IGNORECASE)
                        if m:
                            label = m.group(1).lower()
                            if label in ("4k", "2160p"):
                                label = "4K"
                        entry["qualities"].append({
                            "label": label, "url": entry["url"],
                            "bandwidth": 0, "resolution": "",
                        })
                else:
                    logger.warning("[IMDBPlay] probe %s: m3u8 HTTP %d (r%d)",
                                   entry["server"], r.status_code, round_no)
            except Exception as pf:
                logger.warning("[IMDBPlay] probe %s: m3u8 fetch failed (r%d): %s",
                               entry["server"], round_no, pf)
            if not entry["qualities"]:
                # 🆕 stream داریم ولی لیست variant نشد → Auto امتحانی
                # (مثل رفتار قدیمی: سرور از قلم نمی‌افته)
                entry["unverified"] = True
                entry["qualities"].append({"label": "Auto", "url": entry["url"],
                                           "bandwidth": 0, "resolution": ""})
            return entry
        except Exception as e:
            logger.warning("[IMDBPlay] probe %s failed (r%d): %s", server["name"], round_no, e)
            return None

    # 🆕 پروب موازی ۲ رانده + هاست‌های vidsrcme (Castletv و...) همزمان
    async def _servers_probe() -> list:
        results = list(await asyncio.gather(*[_probe(s, 1) for s in _SERVERS]))
        failed = [s for s, r in zip(_SERVERS, results) if not r]
        if failed:
            logger.info("[IMDBPlay] probe round 2 for %d failed server(s): %s",
                        len(failed), [s["name"] for s in failed])
            await asyncio.sleep(1.5)
            retry_iter = iter(await asyncio.gather(*[_probe(s, 2) for s in failed]))
            for i, r in enumerate(results):
                if not r:
                    results[i] = next(retry_iter)
        return results

    # 🆕 z22 — منابع ایرانی همزمان با CDN + vidsrcme پروب می‌شن (صفر تاخیر اضافه)
    results, vm_entries, iran_entries = await asyncio.gather(
        _servers_probe(),
        _probe_vidsrcme_hosts(imdb_id, season, episode),
        _iran_probe(),
    )
    # اول منابع ایرانی (لینک مستقیم، بدون هات‌لینک) بعد CDN بعد vidsrcme
    entries = list(iran_entries or []) + [r for r in results if r] + list(vm_entries or [])
    logger.info("[IMDBPlay] get_all_server_qualities %s → %d server(s): %s",
                imdb_id, len(entries),
                [(e["server"], [q["label"] for q in e["qualities"]]) for e in entries])
    return entries


def _resp_header(hs, name: str) -> str:
    """🆕 z29: خواندن case-insensitive یک هدر از response (curl_cffi Headers)."""
    if not hs:
        return ""
    try:
        v = hs.get(name)
        if v is not None and str(v):
            return str(v)
    except Exception:
        pass
    try:
        for k in list(hs.keys()):
            if str(k).lower() == name.lower():
                v = hs.get(k)
                return str(v) if v is not None else ""
    except Exception:
        pass
    return ""


async def _hls_segment_engine(
    segments: List[Tuple[str, float]],
    variant_url: str,
    headers: dict,
    out_dir: str,
    shared_session,
    seg_paths: list,
    init_path: Optional[str],
    progress_cb: Optional[Callable[[int, int], None]] = None,
    stats_out: Optional[dict] = None,
) -> None:
    """
    🚀 z16: موتور دانلود سگمنت با concurrency تطبیقی (adaptive concurrency).

    چرا تطبیقی؟ بنچمارک زنده (Vidzee) نشون داد:
        sem=3  → 58 MB/s
        sem=16 → 105 MB/s   ← قله
        sem=24 → 52 MB/s    ← افت!
    یعنی CDNها به‌ازای هر IP سقف پهنای‌باند می‌ذارن و کانکشن زیاد «ضد» سرعته.
    نقطه‌ی بهینه برای هر CDN فرق داره (2Embed/Vidzee/Vidking هرکدوم جور دیگه).
    پس موتور چند کاندیدا رو زنده امتحان می‌کنه و روی بهترین throughput قفل می‌شه.

    ساختار:
      - worker-pool داینامیک: workerها از یک صف مشترک سگمنت برمی‌دارن
      - کنترلر hill-climbing: هر کاندیدا ۲ پنجره‌ی زمانی تست می‌شه
      - راند دوم (second-chance) برای سگمنت‌های شکست‌خورده
      - stats_out: آمار زنده {mbps, concurrency} برای نمایش در بات
    """
    total = len(segments)
    if stats_out is None:
        stats_out = {}
    stats_out["mbps"] = 0.0
    stats_out["concurrency"] = max(1, min(SEGMENT_CONCURRENCY, SEG_CONCURRENCY_MAX))

    counters = {"done": 0, "bytes": 0, "failed": 0}
    state = {
        "desired": max(1, min(SEGMENT_CONCURRENCY, SEG_CONCURRENCY_MAX)),
        "alive": 0,
        "abort": False,
        "stall": False,   # 🛡 z17: watch-dog ضد فریز فعال شد
        "session": None,  # 🛡 z17: راند سوم با سشن تازه
    }
    q: asyncio.Queue = asyncio.Queue()
    tasks: List[asyncio.Task] = []

    def _report():
        if progress_cb:
            try:
                progress_cb(counters["done"], total)
            except asyncio.CancelledError:
                state["abort"] = True
                raise
            except Exception:
                pass

    async def _fetch_one(idx: int, url: str, tries: int):
        """🆕 z29: دانلود سگمنت با استریم + Resume بایتی (Range).

        مشکل قبلی (لاگ Avengers/Vidzee): sess.get غیر‌استریم باید «کل بدنه» رو داخل
        SEG_TIMEOUT=45s می‌گرفت؛ سگمنت‌های ۱-۱۳ مگابایتی روی CDNهای throttleشده
        هیچ‌وقت کامل نمی‌شدن، تلاش fail می‌شد و بایت‌های گرفته‌شده دور ریخته می‌شد.
        حالا:
          - استریم چانک‌به‌چانک مستقیم روی فایل .part → بایت‌ها همون لحظه شمرده
            می‌شن (آمار throughput کنترلر تطبیقی واقعی می‌شه)
          - گیر/تایم‌اوت/قطع‌شدن → تلاش بعدی با «Range: bytes=<دریافتی>-» ادامه می‌ده
            (هیچ بایتی دوباره دانلود نمی‌شه)
          - هدر Content-Length/Content-Range چک می‌شه تا استریم ناقص شناسایی بشه
        """
        abs_url = _make_absolute(variant_url, url)
        sess = state.get("session") or shared_session
        ext = "m4s" if init_path else "ts"
        final_path = os.path.join(out_dir, f"seg_{idx:05d}.{ext}")
        part_path = final_path + ".part"

        # بایت‌های جمع‌شده از تلاش‌های قبلی (همون راند یا راندهای بعدی)
        have = os.path.getsize(part_path) if os.path.exists(part_path) else 0
        expected = 0  # اندازه‌ی کل سگمنت وقتی از هدر معلومه (0 = نامعلوم)

        async def _finish():
            os.replace(part_path, final_path)
            seg_paths[idx] = final_path
            counters["done"] += 1
            _report()

        for attempt in range(max(1, tries)):
            if state["abort"] or state.get("stall"):
                return
            w = None
            try:
                req_headers = dict(headers)
                if have > 0:
                    req_headers["Range"] = f"bytes={have}-"
                w = await asyncio.wait_for(
                    sess.get(
                        abs_url, impersonate=_BROWSER_IMPERSONATE,
                        timeout=SEG_ATTEMPT_CAP, headers=req_headers,
                        stream=True,
                    ),
                    # فقط تا رسیدن هدرها — بدنه با SEG_STALL_READ کنترل می‌شه
                    timeout=min(60.0, SEG_ATTEMPT_CAP) + SEG_HARD_BACKSTOP,
                )
                if w.status_code == 416:
                    # بازه‌ی درخواستی خارج از محدوده — یا part کامل شده یا خرابه
                    m416 = re.search(r"\*/(\d+)", _resp_header(w.headers, "Content-Range"))
                    total416 = int(m416.group(1)) if m416 else 0
                    if (not total416 and have > 0) or (total416 and have >= total416):
                        await _finish()
                        return
                    have = 0
                    expected = 0
                    try:
                        os.unlink(part_path)
                    except Exception:
                        pass
                    await asyncio.sleep(min(2.0, 0.4 * (attempt + 1)))
                    continue
                if w.status_code not in (200, 206):
                    # 403/429/502/… → backoff کوتاه و تلاش دوباره
                    await asyncio.sleep(min(2.0, 0.4 * (attempt + 1)))
                    continue
                # سرور به Range توجه نکرد (200 با part موجود) → از صفر
                if have > 0 and w.status_code == 200:
                    have = 0
                    expected = 0
                # اندازه‌ی کل سگمنت از هدر
                if w.status_code == 206:
                    m206 = re.search(r"/(\d+)\s*$", _resp_header(w.headers, "Content-Range"))
                    if m206:
                        expected = int(m206.group(1))
                else:
                    cl = _resp_header(w.headers, "Content-Length")
                    if cl.isdigit():
                        expected = have + int(cl)

                # ─── استریم چانک‌به‌چانک روی دیسک (هیچ بایتی دور ریخته نمی‌شه) ───
                mode = "ab" if have > 0 else "wb"
                chunks_it = w.aiter_content(chunk_size=65536).__aiter__()
                with open(part_path, mode) as f:
                    while True:
                        chunk = await asyncio.wait_for(
                            chunks_it.__anext__(), timeout=SEG_STALL_READ)
                        f.write(chunk)
                        have += len(chunk)
                        counters["bytes"] += len(chunk)  # 🆕 z29: آمار زنده‌ی واقعی
                if (not expected) or have >= expected:
                    await _finish()
                    return
                # استریم بدون خطا ولی ناقص بسته شد → تلاش بعدی ادامه می‌ده
                logger.debug("seg %d attempt %d truncated (%d/%d) — will resume",
                             idx, attempt + 1, have, expected)
                await asyncio.sleep(min(1.5, 0.3 * (attempt + 1)))
            except StopAsyncIteration:
                # بدنه سالم به انتها رسید
                if (not expected) or have >= expected:
                    try:
                        await _finish()
                    except FileNotFoundError:
                        pass
                    return
                logger.debug("seg %d attempt %d eof-truncated (%d/%d) — will resume",
                             idx, attempt + 1, have, expected)
                await asyncio.sleep(min(1.5, 0.3 * (attempt + 1)))
            except asyncio.TimeoutError:
                # ⏱ گیر (بدون پیشرفت) — بایت‌های گرفته‌شده در .part موندن
                logger.debug("seg %d attempt %d stalled at %d bytes — resume later",
                             idx, attempt + 1, have)
                await asyncio.sleep(min(3.0, 0.5 * (attempt + 1)))
            except asyncio.CancelledError:
                state["abort"] = True
                raise
            except Exception as e:
                logger.debug("seg %d attempt %d failed at %d bytes: %s",
                             idx, attempt + 1, have, e)
                await asyncio.sleep(min(3.0, 0.5 * (attempt + 1)))
            finally:
                if w is not None:
                    _close = getattr(w, "aclose", None) or getattr(w, "close", None)
                    if _close:
                        try:
                            res = _close()
                            if hasattr(res, "__await__"):
                                await asyncio.wait_for(res, timeout=5)
                        except Exception:
                            pass

        counters["failed"] += 1
        logger.warning("[SEG] seg %d incomplete after %d attempts — kept %d bytes for resume",
                       idx, max(1, tries), have)

    async def _worker():
        try:
            while not state["abort"] and not state.get("stall"):
                # اضافی‌ها (بعد از کاهش desired) بعد از آیتم فعلی تمیز خارج می‌شن
                if state["alive"] > state["desired"] and state["alive"] > 1:
                    return
                try:
                    idx, url, tries = q.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    await _fetch_one(idx, url, tries)
                except asyncio.CancelledError:
                    raise
                except Exception as w_err:
                    # 🛡 z17: هیچ آیتمی نباید بین صف و شمارنده گم بشه — اگه worker
                    # بعد از برداشتن آیتم کرش کنه و شمرده نشه، done+failed هیچ‌وقت
                    # به expected نمی‌رسه و _drain برای همیشه فریز می‌شه (باگ ۹۹٪!)
                    counters["failed"] += 1
                    logger.error("[SEG] seg %d worker crashed (counted as failed): %s",
                                 idx, w_err)
        finally:
            state["alive"] -= 1

    def _spawn(n: int):
        for _ in range(max(0, n)):
            state["alive"] += 1
            tasks.append(asyncio.create_task(_worker()))

    def _reconcile():
        """🎯 بعد از تغییر desired، تعداد worker زنده رو هماهنگ کن (z16 fix).
        بدون این، بالا بردن desired هیچ اثری روی دانلود موازی نداشت!"""
        if state["alive"] < state["desired"]:
            _spawn(state["desired"] - state["alive"])

    async def _controller():
        """hill-climbing روی concurrency: چند کاندیدا رو زنده می‌سنجه و بهترین رو قفل می‌کنه."""
        WIN = max(0.2, SEG_ADAPT_WINDOW)
        try:
            # فاز warmup: دو پنجره با نقطه‌ی شروع
            prev = counters["bytes"]
            base_mbps = 0.0
            for _ in range(2):
                await asyncio.sleep(WIN)
                if state["abort"] or counters["done"] >= total:
                    return
                now = counters["bytes"]
                base_mbps = (now - prev) / WIN / 1048576
                prev = now
                stats_out["mbps"] = base_mbps
                stats_out["concurrency"] = state["desired"]

            # اگه خیلی نزدیک پایانیم، دست نزن
            if total - counters["done"] - counters["failed"] < 30:
                return

            # نردبان کاندیداها (فقط صعودی؛ سقف با env قابل بالابردنه)
            ladder = [state["desired"]]
            c = ladder[0]
            while c + 6 <= SEG_CONCURRENCY_MAX and len(ladder) < 5:
                c += 6
                ladder.append(c)

            best_c, best_m = ladder[0], base_mbps
            for cand in ladder[1:]:
                if state["abort"] or counters["done"] >= total:
                    break
                if total - counters["done"] - counters["failed"] < 30:
                    break
                state["desired"] = cand
                _reconcile()
                m = 0.0
                for _ in range(2):
                    await asyncio.sleep(WIN)
                    if state["abort"]:
                        return
                    now = counters["bytes"]
                    m = (now - prev) / WIN / 1048576
                    prev = now
                    stats_out["mbps"] = m
                    stats_out["concurrency"] = cand
                if m > best_m * 1.05:  # حداقل ۵٪ بهبود واقعی
                    best_c, best_m = cand, m

            # 🆕 اگه هیچ کاندیدای بالاتر بهتر نشد، پایین‌ترها رو هم امتحان کن
            # (برای CDNهایی که سقف پایینی به‌ازای هر IP دارن)
            if best_c == ladder[0]:
                c = ladder[0]
                while c - 6 >= SEG_CONCURRENCY_MIN:
                    c -= 6
                    if state["abort"] or counters["done"] >= total:
                        break
                    if total - counters["done"] - counters["failed"] < 30:
                        break
                    state["desired"] = c
                    _reconcile()
                    m = 0.0
                    for _ in range(2):
                        await asyncio.sleep(WIN)
                        if state["abort"]:
                            return
                        now = counters["bytes"]
                        m = (now - prev) / WIN / 1048576
                        prev = now
                        stats_out["mbps"] = m
                        stats_out["concurrency"] = c
                    if m > best_m * 1.05:
                        best_c, best_m = c, m
                    else:
                        break  # پایین‌تر هم بهتر نشد → کافیه

            state["desired"] = best_c
            _reconcile()
            stats_out["concurrency"] = best_c
            logger.info("[SEG] adaptive concurrency settled at %d (%.1f MB/s)", best_c, best_m)

            # تا پایان: فقط آمار به‌روز کن
            while not state["abort"] and counters["done"] < total:
                await asyncio.sleep(WIN)
                now = counters["bytes"]
                stats_out["mbps"] = (now - prev) / WIN / 1048576
                prev = now
        except asyncio.CancelledError:
            raise

    async def _drain(items, tries: int, with_adapt: bool):
        """یک راند کامل: صف رو پر کن، worker بگیر، تا اتمام صبر کن."""
        expected = counters["done"] + counters["failed"] + len(items)
        for idx, url in items:
            q.put_nowait((idx, url, tries))
        _spawn(min(state["desired"], len(items)))
        ctrl = None
        if with_adapt:
            ctrl = asyncio.create_task(_controller())
        # 🛡 z17: watch-dog ضد فریز — اگه این‌قدر ثانیه هیچ پیشرفتی نبود
        # (درخواست گیرکرده/آیتم گم‌شده)، راند force-finish می‌شه و آیتم‌های
        # ناتمام تو راند بعدی (با سشن تازه) retry می‌شن.
        last_snap = (counters["done"] + counters["failed"], counters["bytes"])
        last_change = time.monotonic()
        try:
            while (not state["abort"] and not state.get("stall")
                   and (counters["done"] + counters["failed"]) < expected):
                if state["alive"] == 0:
                    # ایمنی: اگه همه‌ی workerها خارج شدن ولی هنوز کار مونده
                    _spawn(max(1, min(state["desired"],
                                      expected - counters["done"] - counters["failed"])))
                await asyncio.sleep(0.25)
                snap = (counters["done"] + counters["failed"], counters["bytes"])
                if snap != last_snap:
                    last_snap = snap
                    last_change = time.monotonic()
                elif (expected - last_snap[0]) > 0 and \
                        time.monotonic() - last_change > SEG_STALL_TIMEOUT:
                    state["stall"] = True
                    logger.warning("[SEG] ⏱ no progress for %.0fs (%d/%d) — force-finishing round",
                                   SEG_STALL_TIMEOUT, last_snap[0], expected)
        finally:
            if ctrl:
                ctrl.cancel()
                try:
                    await ctrl
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
        if state["stall"]:
            # workerهای گیرکرده رو بکش؛ آیتم‌های ناتمام تو راند بعدی retry می‌شن
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            tasks.clear()
            state["alive"] = 0
            return
        if state["abort"]:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            tasks.clear()
            state["alive"] = 0
            raise asyncio.CancelledError()

    # ─── راند اصلی ───
    stats_out["phase"] = "download"
    await _drain([(i, u) for i, (u, _d) in enumerate(segments)], SEG_RETRIES, True)

    # ─── راند دوم: شانس برای سگمنت‌های از دست رفته ───
    missing = [i for i, p in enumerate(seg_paths) if not p]
    if missing:
        logger.info("[SEG] 2nd chance: retrying %d missing segments", len(missing))
        state["abort"] = False
        state["stall"] = False
        state["desired"] = max(1, min(SEGMENT_CONCURRENCY, SEG_CONCURRENCY_MAX))
        stats_out["phase"] = "retry"
        await _drain([(i, segments[i][0]) for i in missing], 3, False)

    # ─── راند سوم (z17): سشن کاملاً تازه — کانکشن‌های خراب/گیرکرده دور ریخته می‌شن ───
    missing = [i for i, p in enumerate(seg_paths) if not p]
    if missing:
        logger.info("[SEG] 3rd chance (fresh session): retrying %d missing segments", len(missing))
        state["abort"] = False
        state["stall"] = False
        state["desired"] = max(2, min(8, SEGMENT_CONCURRENCY))
        stats_out["phase"] = "retry2"
        try:
            async with AsyncSession(max_clients=SESSION_MAX_CLIENTS) as fresh:
                state["session"] = fresh
                await _drain([(i, segments[i][0]) for i in missing], 2, False)
        finally:
            state["session"] = None

    missing = [i for i, p in enumerate(seg_paths) if not p]
    # ─── راند چهارم (z26): آخرین شانس — ترتیبی، بدون صف و بدون واتچ‌داگ ───
    # سگمنت‌های تکیِ جاافتاده (معمولاً آخر playlist) که راندهای موازی به‌خاطر
    # stall-force-finish یا کرش worker بین صف و fetch جا انداختنشون.
    # ترتیبی + فاصله‌ی ۱.۵ ثانیه‌ای → هیچ فشاری روی CDN نیست و واتچ‌داگ هم
    # نمی‌تونه وسط کار راند رو بکشه.
    if missing and len(missing) <= 8 and not state["abort"]:
        logger.info("[SEG] 4th chance (sequential, no watchdog): retrying %d missing segment(s)",
                    len(missing))
        stats_out["phase"] = "retry3"
        state["abort"] = False
        state["stall"] = False
        state["desired"] = 1
        for idx in list(missing):
            if state["abort"]:
                break
            await _fetch_one(idx, segments[idx][0], 3)
            if not seg_paths[idx]:
                # نفس کوتاه قبل از حرکت — بعضی CDNها بلافاصله بعد از قطعی جواب نمی‌دن
                await asyncio.sleep(1.5)
    missing = [i for i, p in enumerate(seg_paths) if not p]
    stats_out["missing"] = len(missing)
    stats_out["phase"] = "done"
    if counters["failed"] or missing:
        logger.info("[SEG] finished: %d failed / %d missing (of %d)",
                    counters["failed"], len(missing), total)


async def _download_direct_entry(entry: dict, out_dir: str,
                                 progress_cb: Optional[Callable[[int, int], None]] = None) -> Optional[str]:
    """🆕 z22 — دانلود مستقیم یک entry منبع ایرانی (MP4/MKV) از کش پروب.

    برای مسیر «Auto + سرور انتخابی» استفاده می‌شه؛ اگه دانلود شکست None
    برمی‌گردونه تا فلوی عادی CDN ادامه پیدا کنه (هیچ‌وقت کل دانلود رو نمی‌شکنه).
    """
    url = entry.get("url") or ""
    if not url:
        return None
    headers = {"User-Agent": _USER_AGENT}
    headers.update(entry.get("headers") or {})
    _ext = ".mp4"
    _m_ext = re.search(r"\.(mp4|mkv|avi|mov)(?:[?#]|$)", url.lower())
    if _m_ext:
        _ext = _m_ext.group(1)
    out_path = os.path.join(out_dir, f"{int(time.time())}{_ext}")
    try:
        async with AsyncSession() as s:
            r = await s.get(url, impersonate=_BROWSER_IMPERSONATE, timeout=600,
                            headers=headers, stream=True)
            if r.status_code != 200:
                logger.warning("[IMDBPlay] direct iran fetch HTTP %d (%s)",
                               r.status_code, entry.get("server"))
                return None
            total = int(r.headers.get("content-length", 0))
            done = 0
            with open(out_path, "wb") as f:
                async for chunk in r.aiter_content(chunk_size=1024 * 256):
                    f.write(chunk)
                    done += len(chunk)
                    if progress_cb:
                        try:
                            progress_cb(done, total)
                        except Exception:
                            pass
        logger.info("[IMDBPlay] direct iran download OK: %s (%.1f MB)",
                    out_path, os.path.getsize(out_path) / 1024 / 1024)
        return out_path
    except Exception as e:
        logger.warning("[IMDBPlay] direct iran download failed: %s", e)
        try:
            if os.path.exists(out_path) and os.path.getsize(out_path) == 0:
                os.remove(out_path)
        except Exception:
            pass
        return None


async def download_with_quality(
    imdb_id: str,
    quality_label: str,
    out_dir: str,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    preferred_server: Optional[str] = None,
    strict_quality: bool = False,
    stats_out: Optional[dict] = None,
    title: Optional[str] = None,
    year=None,
) -> Optional[str]:
    """
    دانلود فیلم یا قسمت سریال با کیفیت انتخابی.

    Args:
        imdb_id: e.g. "tt33071426"
        quality_label: e.g. "Auto", "1080p", "720p"
        out_dir: مسیر خروجی
        season, episode: برای سریال
        progress_cb: callback(done, total)
        preferred_server: 🆕 نام سرور انتخابی کاربر (None = خودکار)
        strict_quality: 🆕 اگه True، وقتی هیچ سروری کیفیت رو نداره به‌جای
            دانلود اشتباه با Auto (باگ 480p→431MB)، خطای واضح میده.
        stats_out: 🆕 z16 دیکشنری اختیاری که موتور سگمنت آمار زنده توش می‌ریزه
            ({"mbps": سرعت لحظه‌ای, "concurrency": تعداد کانکشن فعلی،
             🆕 z35 "gap_spans": نقشه‌ی پرش‌های سگمنت گم‌شده})
        title, year: 🆕 z35 — برای fallback جستجوی TMDB وقتی find خالیه

    Returns:
        مسیر فایل دانلود شده، یا None در صورت خطا.
    """
    if not imdb_id:
        return None
    if not imdb_id.startswith("tt"):
        imdb_id = f"tt{imdb_id}"

    os.makedirs(out_dir, exist_ok=True)

    # 🆕 z35 — find گاهی برای بعضی ttها خالیه (مثل tt4154795/Infinity War)
    # → با عنوان+سال هم جستجو می‌شه
    tmdb_id = await _get_tmdb_id(imdb_id, title=title, year=year,
                                 is_tv=bool(season and episode))
    if not tmdb_id:
        raise RuntimeError(f"Cannot resolve tmdb_id for {imdb_id}")

    # 🆕 z35 — runtime مرجع برای sanity-check مدت هر استریم
    _expected_s = await _expected_runtime_seconds(tmdb_id, season, episode)

    # اگه کیفیت خاصی درخواست شده، سروری رو پیدا کن که اون کیفیت رو داشته باشه
    # اگه "Auto" درخواست شده، اولین سرور موفق کافیه
    target_quality = quality_label.lower() if quality_label else "auto"

    stream = None
    if target_quality == "auto":
        # 🆕 اگه کاربر سرور خاصی انتخاب کرده، اول همون سرور امتحان میشه
        if preferred_server:
            # 🆕 z22 — منابع ایرانی (FJ 🇮🇷 و...) اول چک می‌شن (لینک مستقیم MP4)
            iran_entry = _iran_cache_get(_vm_key(imdb_id, season, episode)).get(preferred_server)
            if iran_entry and iran_entry.get("url"):
                logger.info("[IMDBPlay] Auto + iran server %s (cached direct link)", preferred_server)
                _p = await _download_direct_entry(iran_entry, out_dir, progress_cb)
                if _p:
                    return _p
                logger.warning("[IMDBPlay] iran server %s failed → falling back to CDN", preferred_server)
            for server in _SERVERS:
                if server["name"] == preferred_server:
                    try:
                        logger.info("[IMDBPlay] Auto + preferred server %s...", preferred_server)
                        stream = await _get_stream_for_server(server, tmdb_id, imdb_id, season, episode)
                        # 🆕 z35 — چک مدت قبل از پذیرش سرور انتخابی
                        if stream and not await _stream_duration_ok(
                                stream, _expected_s, f"auto-preferred:{preferred_server}"):
                            stream = None
                    except Exception as e:
                        logger.warning("[IMDBPlay] preferred server %s failed: %s", preferred_server, e)
                        stream = None
                    break
        if not stream:
            # برای Auto، اولین سرور موفق کافیه
            stream = await _get_first_working_stream(tmdb_id, imdb_id, season, episode,
                                                     expected_s=_expected_s)
    else:
        # برای کیفیت خاص، سرورها رو به ترتیب امتحان کن تا سروری پیدا بشه که اون کیفیت رو داشته باشه
        # 🆕 سرور انتخابی کاربر «اول» امتحان میشه؛ اگه این کیفیت رو نداشت،
        # بقیه‌ی سرورها هم امتحان می‌شن (فقط سرورهایی که دقیقاً همین کیفیت رو دارن).
        # تضمین باگ 431MB سر جاشه: هرگز با Auto جایگزین نمی‌شه مگه strict=False.
        ordered = list(_SERVERS)
        # 🆕 z22 — منابع ایرانی کش‌شده هم تو انتخاب هستن (FJ 🇮🇷 و...)
        iran_names = list(_iran_cache_get(_vm_key(imdb_id, season, episode)).keys())
        if iran_names:
            ordered = [{"id": "ir", "name": n, "ir_name": n} for n in iran_names] + ordered
        # 🆕 هاست‌های vidsrcme کش‌شده (مثل Castletv) هم تو انتخاب هستن
        vm_names = list(_VIDSRCME_CACHE.get(_vm_key(imdb_id, season, episode), {}).keys())
        if vm_names:
            ordered += [{"id": "vm", "name": n, "vm_name": n} for n in vm_names]
        if preferred_server:
            ordered.sort(key=lambda s: 0 if s["name"] == preferred_server else 1)
        for server in ordered:
            try:
                logger.info("[IMDBPlay] Trying server %s for quality %s...", server["name"], quality_label)
                candidate = await _get_stream_for_server(server, tmdb_id, imdb_id, season, episode)
                if not candidate or not candidate.get("url"):
                    continue

                # بررسی اینکه آیا این سرور کیفیت مورد نظر رو داره
                # اگه سرور لیست کیفیت‌ها رو داره (مثل Videasy/Vidking)، چک کن
                if candidate.get("qualities"):
                    has_q = any(
                        q.get("label", "").lower() == target_quality
                        for q in candidate["qualities"]
                    )
                    if has_q:
                        # این سرور کیفیت مورد نظر رو داره — URL اون کیفیت رو برگردون
                        for q in candidate["qualities"]:
                            if q.get("label", "").lower() == target_quality:
                                candidate["url"] = q["url"]
                                break
                        # 🆕 z35 — چک مدت قبل از پذیرش سرور
                        if not await _stream_duration_ok(
                                candidate, _expected_s,
                                f"q:{server['name']}:{quality_label}"):
                            stream = None
                            continue
                        stream = candidate
                        logger.info("[IMDBPlay] ✓ Server %s has quality %s", server["name"], quality_label)
                        break
                    else:
                        logger.info("[IMDBPlay] ✗ Server %s doesn't have quality %s (has: %s)",
                                    server["name"], quality_label,
                                    [q.get("label") for q in candidate["qualities"]])
                        continue
                else:
                    # سرور فقط Auto داره (مثل Vidzee) — اگه کیفیت Auto خواستیم، خوبه
                    # اگه نه، این سرور رو رد کن
                    logger.info("[IMDBPlay] ✗ Server %s only has Auto quality", server["name"])
                    continue
            except Exception as e:
                logger.warning("[IMDBPlay] ✗ Server %s exception: %s", server["name"], e)
                continue

        # اگه هیچ سرور کیفیت مورد نظر رو نداشت:
        # 🆕 strict_quality=True → به‌جای دانلود اشتباه با Auto، خطای واضح
        # (این همون باگ «480p انتخاب می‌کردم 431MB دانلود می‌شد» بود)
        if not stream:
            if strict_quality:
                # 🆕 z22 — کیفیت‌های منابع ایرانی کش‌شده (FJ 🇮🇷 و...) هم تو لیست «موجود» میان
                _ck = _vm_key(imdb_id, season, episode)
                avail = sorted({
                    q.get("label", "?")
                    for e in (list(_iran_cache_get(_ck).values())
                              + list(_VIDSRCME_CACHE.get(_ck, {}).values())
                              + (await get_all_server_qualities(imdb_id, season, episode)))
                    for q in e["qualities"]
                    if q.get("label", "").lower() != "auto"
                })
                raise RuntimeError(
                    f"کیفیت {quality_label} از هیچ سروری در دسترس نیست. "
                    f"کیفیت‌های موجود: {', '.join(avail) if avail else 'هیچ'}"
                )
            logger.warning("[IMDBPlay] No server has quality %s, falling back to Auto", quality_label)
            stream = await _get_first_working_stream(tmdb_id, imdb_id, season, episode,
                                                     expected_s=_expected_s)
            # وقتی fallback می‌کنیم، quality_label رو هم به Auto تغییر بده
            quality_label = "Auto"

    if not stream:
        raise RuntimeError(f"No working stream found for {imdb_id}")

    m3u8_url = stream["url"]
    headers = {"User-Agent": _USER_AGENT}
    headers.update(stream.get("headers", {}))

    # اگه stream از نوع MP4 باشه (مثل 2Embed/vidlink)، دانلود مستقیم
    stream_type = stream.get("type", "hls")
    if stream_type == "mp4":
        logger.info("Downloading MP4 directly from %s", stream.get("server", ""))
        # 🆕 z22 — پسوند از خود URL (فایل‌های منابع ایرانی بعضاً MKV هستن)
        _ext = ".mp4"
        _m_ext = re.search(r"\.(mp4|mkv|avi|mov)(?:[?#]|$)", (m3u8_url or "").lower())
        if _m_ext:
            _ext = _m_ext.group(1)
        out_path = os.path.join(out_dir, f"{int(time.time())}{_ext}")
        mp4_failed = False
        try:
            async with AsyncSession() as s:
                # برای MP4، دانلود با chunked
                r = await s.get(m3u8_url, impersonate=_BROWSER_IMPERSONATE, timeout=600,
                                headers=headers, stream=True)
                if r.status_code != 200:
                    # اگه 429 (rate limited) یا 5xx، به HLS fallback کن
                    if r.status_code in (429, 500, 502, 503, 504):
                        logger.warning("MP4 fetch HTTP %d — falling back to HLS server", r.status_code)
                        mp4_failed = True
                    else:
                        raise RuntimeError(f"MP4 fetch HTTP {r.status_code}")
                else:
                    total = int(r.headers.get("content-length", 0))
                    done = 0
                    with open(out_path, "wb") as f:
                        async for chunk in r.aiter_content(chunk_size=1024 * 256):
                            f.write(chunk)
                            done += len(chunk)
                            if progress_cb:
                                try:
                                    progress_cb(done, total)
                                except Exception:
                                    pass
                    logger.info("Download complete: %s (%.1f MB)",
                                out_path, os.path.getsize(out_path) / 1024 / 1024)
                    return out_path
        except Exception as e:
            logger.error("MP4 download failed: %s", e)
            mp4_failed = True

        # اگه MP4 fail شد (429 یا خطا)، fallback به سرور HLS
        if mp4_failed:
            logger.info("Falling back to HLS server (skipping MP4-only servers)...")
            # سرورها رو دوباره امتحان کن، ولی فقط HLS ها رو
            for server in _SERVERS:
                if server["name"] == stream.get("server"):
                    continue  # همین سرور رو رد کن
                try:
                    logger.info("[IMDBPlay] Fallback: trying server %s (HLS)...", server["name"])
                    fallback_stream = await _get_stream_for_server(server, tmdb_id, imdb_id, season, episode)
                    if not fallback_stream or not fallback_stream.get("url"):
                        continue
                    # فقط HLS رو بپذیر (نه MP4)
                    if fallback_stream.get("type", "hls") != "hls":
                        continue
                    logger.info("[IMDBPlay] ✓ Fallback to %s (HLS)", server["name"])
                    stream = fallback_stream
                    m3u8_url = stream["url"]
                    headers = {"User-Agent": _USER_AGENT}
                    headers.update(stream.get("headers", {}))
                    break
                except Exception as e:
                    logger.warning("[IMDBPlay] Fallback server %s failed: %s", server["name"], e)
                    continue
            else:
                raise RuntimeError("MP4 download failed and no HLS fallback available")

    # برای HLS، fetch m3u8 — با retry و re-fetch stream اگه 401/429 گرفتیم
    text = None
    for m3u8_attempt in range(3):
        try:
            async with AsyncSession() as s:
                r = await s.get(m3u8_url, impersonate=_BROWSER_IMPERSONATE, timeout=20, headers=headers)
                if r.status_code == 200:
                    text = r.text
                    break
                elif r.status_code in (401, 429):
                    # Seed منقضی شده یا rate-limited — stream جدید بگیر
                    logger.warning("m3u8 fetch HTTP %d (attempt %d) — re-fetching stream with new seed",
                                   r.status_code, m3u8_attempt + 1)
                    await asyncio.sleep(1 * (m3u8_attempt + 1))
                    # 🆕 Stream جدید — اول سرور انتخابی کاربر، بعد ترتیب پیش‌فرض
                    new_stream = None
                    if preferred_server:
                        for server in _SERVERS:
                            if server["name"] == preferred_server:
                                try:
                                    new_stream = await _get_stream_for_server(
                                        server, tmdb_id, imdb_id, season, episode)
                                except Exception:
                                    new_stream = None
                                break
                    if not new_stream or not new_stream.get("url"):
                        new_stream = await _get_first_working_stream(tmdb_id, imdb_id, season, episode)
                    if new_stream and new_stream.get("url"):
                        stream = new_stream
                        m3u8_url = stream["url"]
                        headers = {"User-Agent": _USER_AGENT}
                        headers.update(stream.get("headers", {}))
                        # اگه کیفیت خاص خواستیم، دوباره URL اون کیفیت رو پیدا کن
                        if quality_label and quality_label.lower() != "auto" and stream.get("qualities"):
                            for q in stream["qualities"]:
                                if q.get("label", "").lower() == quality_label.lower():
                                    m3u8_url = q["url"]
                                    break
                    continue
                else:
                    raise RuntimeError(f"m3u8 fetch HTTP {r.status_code}")
        except RuntimeError:
            raise
        except Exception as e:
            logger.warning("m3u8 fetch attempt %d failed: %s", m3u8_attempt + 1, e)
            await asyncio.sleep(1 * (m3u8_attempt + 1))

    if not text:
        raise RuntimeError("m3u8 fetch failed after 3 attempts")

    # اگه master.m3u8 باشه، variant انتخاب کن
    variant_url = m3u8_url
    if "#EXT-X-STREAM-INF:" in text:
        variants = _parse_master_m3u8(text)
        if not variants:
            raise RuntimeError("No variants in master.m3u8")

        # اگر کیفیت خاصی خواستیم، variantها رو بر اساس height مرتب کن (نزولی)
        # تا بتونیم نزدیک‌ترین (پایین‌تر یا مساوی) رو پیدا کنیم
        def _variant_height(v):
            """استخراج height از variant (res=1920x1080 → 1080)."""
            res = v[2]
            if not res or "x" not in res:
                # از bandwidth حدس بزن
                bw = v[1]
                if bw >= 8_000_000: return 1080
                if bw >= 4_000_000: return 720
                if bw >= 2_000_000: return 480
                return 0
            try:
                return int(res.split("x")[-1])
            except (ValueError, IndexError):
                return 0

        # Quality map: label → height
        _QUALITY_HEIGHTS = {
            "2160p": 2160, "4k": 2160, "uhd": 2160,
            "1080p fullhd": 1080, "1080p x265": 1080, "1080p": 1080,
            "720p x265": 720, "720p": 720,
            "480p": 480,
            "360p": 360,
            "240p": 240,
        }

        target_height = _QUALITY_HEIGHTS.get(quality_label.lower() if quality_label else "", 0)

        # Sort variants by height descending (best first)
        variants_with_h = [(v, _variant_height(v)) for v in variants]
        variants_with_h.sort(key=lambda x: -x[1])

        chosen = None
        if quality_label and quality_label.lower() != "auto" and target_height > 0:
            # روش 1: تطابق دقیق label
            for v, h in variants_with_h:
                url, bw, res = v
                label = _resolution_to_label(res, bw)
                if label.lower() == quality_label.lower():
                    chosen = v
                    logger.info("✓ Quality match (exact): %s → height=%d", label, h)
                    break

            # روش 2: تطابق بر اساس height (اگه label پیدا نشد)
            if not chosen:
                # نزدیک‌ترین height که ≤ target باشه
                candidates_at_or_below = [(v, h) for v, h in variants_with_h if h <= target_height]
                if candidates_at_or_below:
                    chosen = candidates_at_or_below[0][0]
                    logger.info("✓ Quality match (height ≤ %d): chose height=%d",
                                target_height, candidates_at_or_below[0][1])
                else:
                    # اگر هیچ کدوم ≤ target نبود، پایین‌ترین رو بگیر (حداقل حجم)
                    chosen = variants_with_h[-1][0]
                    logger.info("✓ Quality fallback (no variant ≤ %d): chose lowest = height=%d",
                                target_height, variants_with_h[-1][1])
        else:
            # Auto: بهترین کیفیت
            chosen = variants_with_h[0][0]

        variant_url = _make_absolute(m3u8_url, chosen[0])
        logger.info("Selected variant: %s (bandwidth=%d, resolution=%s)",
                    variant_url[:80], chosen[1], chosen[2])

        # 🆕 z16: fetch پلی‌لیست واریانت با retry — 502/429 گذراها نباید کل دانلود رو بترکونن
        text = None
        last_verr = None
        for _vatt in range(3):
            try:
                async with AsyncSession() as s:
                    r = await s.get(variant_url, impersonate=_BROWSER_IMPERSONATE, timeout=30, headers=headers)
                    if r.status_code == 200:
                        text = r.text
                        break
                    last_verr = f"variant m3u8 HTTP {r.status_code}"
                    logger.warning("%s (attempt %d/3)", last_verr, _vatt + 1)
            except Exception as e:
                last_verr = str(e)[:200]
                logger.warning("variant m3u8 fetch attempt %d/3 failed: %s", _vatt + 1, last_verr)
            await asyncio.sleep(1 + _vatt)
        if not text:
            raise RuntimeError(f"variant m3u8 fetch failed: {last_verr}")

    segments, init_url = _parse_variant_m3u8(text)
    if not segments:
        raise RuntimeError("No segments in variant m3u8")

    total = len(segments)
    server_name = stream.get("server", "unknown")
    logger.info("Downloading %d segments from %s (init=%s)",
                total, server_name, "yes" if init_url else "no")

    # download segments in parallel — 🚀 z16: موتور تطبیقی (worker-pool + hill-climbing)
    seg_paths = [None] * total
    init_path = None

    async with AsyncSession(max_clients=SESSION_MAX_CLIENTS) as shared_session:
        # اگه init segment وجود داره (fMP4)، اول اون رو دانلود کن
        if init_url:
            init_abs_url = _make_absolute(variant_url, init_url)
            logger.info("Downloading init segment: %s", init_abs_url[:80])
            for attempt in range(5):
                try:
                    r = await shared_session.get(
                        init_abs_url, impersonate=_BROWSER_IMPERSONATE,
                        timeout=60, headers=headers,
                    )
                    if r.status_code == 200 and r.content:
                        init_path = os.path.join(out_dir, "init.mp4")
                        with open(init_path, "wb") as f:
                            f.write(r.content)
                        logger.info("Init segment saved (%d bytes)", len(r.content))
                        break
                    elif r.status_code in (401, 429, 503):
                        logger.warning("init download HTTP %d (attempt %d)", r.status_code, attempt + 1)
                        await asyncio.sleep(0.5 * (attempt + 1))
                    else:
                        await asyncio.sleep(0.5 * (attempt + 1))
                except Exception as e:
                    logger.warning("init download attempt %d failed: %s", attempt, e)
                    await asyncio.sleep(1 * (attempt + 1))

            # اگه init segment دانلود نشد ولی نیاز هست، خطا بده
            if not init_path and init_url:
                logger.error("Init segment failed to download after 5 attempts — concat will likely fail")

        # 🚀 موتور تطبیقی: تعداد کانکشن همزمان رو بر اساس throughput واقعی تنظیم می‌کنه
        await _hls_segment_engine(
            segments, variant_url, headers, out_dir, shared_session,
            seg_paths, init_path,
            progress_cb=progress_cb, stats_out=stats_out,
        )

    missing = [i for i, p in enumerate(seg_paths) if not p]
    if missing:
        logger.error("Missing %d segments: %s", len(missing), missing[:5])

    valid_paths = [p for p in seg_paths if p]
    if not valid_paths:
        raise RuntimeError("All segments failed to download")

    # 🛡 z17: سگمنت ناقص = فایل خراب (دقیقاً «خراب میشه»ی کاربر!). موتور ۴ راند
    # تلاش کرد (آخرینش ترتیبی با فاصله)؛ اگه هنوز سگمنت مونده:
    # 🆕 z26 — تحمل خطای کوچک: ۱-چند سگمنت گم‌شده از صدها (۰.x٪) ≠ فایل خراب!
    # پرشِ چندثانیه‌ای تو ویدیو بهتر از ریجکت کل دانلوده. آستانه:
    #   total < 60  → صفر تحمل (ویدیو کوتاهه، هر سگمنت مهمه)
    #   else        → حداکثر ۱٪ سگمنت‌ها (۵۰۸ → ۵ سگمنت ≈ ۲۰ ثانیه پرش)
    if missing:
        miss_pct = len(missing) * 100.0 / max(1, total)
        tol = 0 if total < 60 else max(2, int(total * 0.01))
        if len(missing) <= tol:
            logger.warning("[SEG] tolerating %d/%d missing segment(s) (%.2f%%) — "
                           "concat continues, tiny skip(s) expected",
                           len(missing), total, miss_pct)
            if stats_out is not None:
                stats_out["missing"] = len(missing)
                stats_out["missing_tol"] = True
                # 🆕 z35 — نقشه‌ی دقیق پرش‌ها برای بازتایم ساب (فیکس ریشه‌ای درِیف)
                try:
                    stats_out["gap_spans"] = _compute_gap_spans(segments, seg_paths)
                except Exception as _gexp:
                    logger.warning("[SEG] gap_spans computation failed: %s", _gexp)
        else:
            raise RuntimeError(
                f"{len(missing)} سگمنت از {total} دانلود نشد ({miss_pct:.1f}٪) — "
                "منبع قطع شده یا لینک منقضی شده. دوباره امتحان کن یا سرور/کیفیت دیگه‌ای انتخاب کن."
            )

    out_path = os.path.join(out_dir, f"{int(time.time())}.mp4")
    # 🛡 z17: concat حالا کاملاً async هست — دیگه موقع سوار کردن فیلمِ چندگیگ،
    # event loop بات (و پیام‌های پیشرفت) فریز نمی‌شه
    if not await _concat_segments_async(valid_paths, out_path, init_path):
        raise RuntimeError("ffmpeg concat failed")

    # پاک کردن سگمنت‌ها و init
    for p in valid_paths:
        try:
            os.unlink(p)
        except Exception:
            pass
    if init_path:
        try:
            os.unlink(init_path)
        except Exception:
            pass

    logger.info("Download complete: %s (%.1f MB)",
                out_path, os.path.getsize(out_path) / 1024 / 1024)
    return out_path


async def _concat_segments_async(seg_paths: List[str], out_path: str,
                                 init_path: Optional[str] = None) -> bool:
    """
    🛡 z17: نسخه‌ی async کنکت — مشکل «تا آخر میره و ادامه نمیده» نیمه‌ی دومش اینجا بود:

      - _concat_segments قدیمی subprocess.run بلاک‌کننده بود → کل event loop بات
        (تلثون، پیام‌های پیشرفت، بقیه‌ی کاربرها) چند دقیقه فریز می‌شد
      - binary concat با fin.read() تک‌ضربی → چند گیگ RAM و I/O بلاک‌کننده
      - -movflags +faststart یعنی ffmpeg فایل رو دو بار می‌نویسه → روی فیلم ۲-۳ گیگی
        زمان و دیسک زیاد می‌بره
      - روش ۴ (re-encode) برای فیلم ۲ ساعته روی VPS ضعیف = ۱-۲ ساعت «هیچی نمی‌شه»

    این نسخه:
      - ffmpeg با create_subprocess_exec + wait_for (تایم‌اوت سخت، بدون بلاک شدن loop)
      - کپی باینری chunk-به-chunk در thread جدا (RAM ثابت، loop آزاد)
      - 🛡 گارد فضای دیسک: اگه جا برای روش باینری نیست، مستقیم می‌ره سراغ concat
        demuxer؛ و اگه کلاً جا نیست، خطای شفاف فضای دیسک می‌ده (نه فایل خراب)
      - re-encode پیش‌فرض خاموش (IMDB_ALLOW_REENCODE=1 برای فعال‌سازی)

    Returns:
        True اگه موفق، False در غیر این صورت. (خطای دیسک RuntimeError برمی‌گرده)
    """
    try:
        has_init = init_path and os.path.exists(init_path)

        # ─── محاسبه‌ی حجم ورودی + گارد فضای دیسک ───
        total_in = 0
        for p in ([init_path] if has_init else []) + list(seg_paths):
            try:
                if p and os.path.exists(p):
                    total_in += os.path.getsize(p)
            except OSError:
                pass
        try:
            free = shutil.disk_usage(os.path.dirname(os.path.abspath(out_path))).free
        except Exception:
            free = None
        # روش باینری (fMP4) به ~۲.۳ برابر حجم ورودی جا نیاز داره (combined + خروجی + faststart)
        binary_ok = has_init and (free is None or free > total_in * 2.3)
        if free is not None and free < total_in * 1.25 and total_in > 0:
            raise RuntimeError(
                f"فضای دیسک کمه: {free / 1073741824:.1f}GB آزاده ولی حداقل "
                f"{total_in * 1.25 / 1073741824:.1f}GB لازمه (فیلم: {total_in / 1073741824:.1f}GB) — "
                "فضای سرور رو خالی کن و دوباره امتحان کن."
            )

        async def _ffmpeg_run(cmd: List[str], timeout: float):
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except Exception:
                    pass
                await proc.wait()
                raise
            return proc.returncode, (err or b"")

        # ─── روش 1 (فقط برای fMP4 با init): binary concat + remux ───
        if has_init and binary_ok:
            combined_path = out_path + ".combined.mp4"
            try:
                def _binary_copy():
                    # chunk-به-chunk در thread جدا — RAM ثابت، event loop آزاد
                    with open(combined_path, "wb") as fout:
                        for p in [init_path] + list(seg_paths):
                            if p and os.path.exists(p):
                                with open(p, "rb") as fin:
                                    while True:
                                        chunk = fin.read(1024 * 1024)
                                        if not chunk:
                                            break
                                        fout.write(chunk)

                await asyncio.to_thread(_binary_copy)

                # remux با ffmpeg (تبدیل fragmented MP4 به MP4 استاندارد)
                cmd1 = [
                    "ffmpeg", "-y", "-i", combined_path,
                    "-c", "copy",
                    "-movflags", "+faststart",
                    out_path,
                ]
                rc1, err1 = await _ffmpeg_run(cmd1, 1800)
                if rc1 == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    logger.info("concat succeeded (method 1: binary concat + remux for fMP4)")
                    try:
                        os.unlink(combined_path)
                    except Exception:
                        pass
                    return True
                # اگه fail شد، combined رو نگه نمی‌داریم — جایی رو اشغال نکنه
                logger.warning("method 1 (binary concat) failed: %s",
                               err1.decode("utf-8", errors="ignore")[:300])
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("method 1 (binary concat) exception: %s", e)
            finally:
                try:
                    if os.path.exists(combined_path):
                        os.unlink(combined_path)
                except Exception:
                    pass

        # ─── روش 2+: concat demuxer (برای MPEG-TS) ───
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            for p in seg_paths:
                if p and os.path.exists(p):
                    p_escaped = p.replace("'", "'\\''")
                    f.write(f"file '{p_escaped}'\n")
            list_path = f.name

        try:
            cmd2 = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", list_path,
                "-c", "copy",
                "-bsf:a", "aac_adtstoasc",
                "-movflags", "+faststart",
                out_path,
            ]
            rc2, _err2 = await _ffmpeg_run(cmd2, 1800)
            if rc2 == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                logger.info("concat succeeded (method 2: concat demuxer + aac_adtstoasc)")
                return True

            # ─── روش 3: concat demuxer بدون bitstream filter ───
            cmd3 = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", list_path,
                "-c", "copy",
                "-movflags", "+faststart",
                out_path,
            ]
            rc3, _err3 = await _ffmpeg_run(cmd3, 1800)
            if rc3 == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                logger.info("concat succeeded (method 3: concat demuxer copy only)")
                return True

            # ─── روش 5: مسیر مطلق (سریع — قبل از re-encode امتحان می‌شه) ───
            try:
                all_paths = []
                if has_init:
                    all_paths.append(init_path)
                all_paths.extend([p for p in seg_paths if p and os.path.exists(p)])

                with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f2:
                    for p in all_paths:
                        p_abs = os.path.abspath(p)
                        p_escaped = p_abs.replace("'", "'\\''")
                        f2.write(f"file '{p_escaped}'\n")
                    list_path2 = f2.name

                try:
                    cmd5 = [
                        "ffmpeg", "-y",
                        "-f", "concat", "-safe", "0",
                        "-i", list_path2,
                        "-c", "copy",
                        "-movflags", "+faststart",
                        out_path,
                    ]
                    rc5, _err5 = await _ffmpeg_run(cmd5, 1800)
                    if rc5 == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                        logger.info("concat succeeded (method 5: absolute paths + copy)")
                        return True
                finally:
                    try:
                        os.unlink(list_path2)
                    except Exception:
                        pass
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("method 5 failed: %s", e)

            # ─── روش 4: re-encode (fallback نهایی) ───
            # 🛡 z17: برای فیلم ۲ ساعته روی VPS ضعیف ۱-۲ ساعت طول می‌کشه و شبیه
            # «هیچی نمی‌شه» به نظر می‌رسه — فقط با IMDB_ALLOW_REENCODE=1 فعال می‌شه
            if ALLOW_REENCODE:
                cmd4 = [
                    "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                    "-i", list_path,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                    "-c:a", "aac", "-b:a", "128k",
                    "-movflags", "+faststart",
                    out_path,
                ]
                rc4, err4 = await _ffmpeg_run(cmd4, 3600)
                if rc4 == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    logger.info("concat succeeded (method 4: re-encode)")
                    return True

            logger.error("ffmpeg concat failed all methods (last: method 2 rc=%s)", rc2)
            return False
        finally:
            try:
                os.unlink(list_path)
            except Exception:
                pass
    except RuntimeError:
        # خطای شفاف (مثل فضای دیسک) — ببر بالا تا کاربر ببینه
        raise
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("concat error: %s", e)
        return False


def _concat_segments(seg_paths: List[str], out_path: str, init_path: Optional[str] = None) -> bool:
    """
    concat سگمنت‌ها با ffmpeg (نسخه‌ی قدیمی sync — دیگر توسط download_with_quality
    استفاده نمی‌شه؛ نسخه‌ی async بالا جایگزینشه. برای سازگاری نگه داشته شده.)

    برای MPEG-TS (بدون init): از concat demuxer استفاده می‌شه.
    برای fMP4 (با init): ابتدا binary concat (init + segments)، سپس remux.

    Returns:
        True اگه موفق، False در غیر این صورت.
    """
    try:
        has_init = init_path and os.path.exists(init_path)

        # ─── روش 1 (فقط برای fMP4 با init): binary concat + remux ───
        if has_init:
            combined_path = out_path + ".combined.mp4"
            try:
                with open(combined_path, "wb") as fout:
                    # اول init
                    with open(init_path, "rb") as fin:
                        fout.write(fin.read())
                    # بعد همه segments به ترتیب
                    for p in seg_paths:
                        if p and os.path.exists(p):
                            with open(p, "rb") as fin:
                                fout.write(fin.read())

                # remux با ffmpeg (تبدیل fragmented MP4 به MP4 استاندارد)
                cmd1 = [
                    "ffmpeg", "-y", "-i", combined_path,
                    "-c", "copy",
                    "-movflags", "+faststart",
                    out_path,
                ]
                result1 = subprocess.run(cmd1, capture_output=True, timeout=1800)
                if result1.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    logger.info("concat succeeded (method 1: binary concat + remux for fMP4)")
                    try:
                        os.unlink(combined_path)
                    except Exception:
                        pass
                    return True
                # اگه fail شد، combined رو نگه می‌داریم برای fallback
                logger.warning("method 1 (binary concat) failed: %s",
                               result1.stderr.decode("utf-8", errors="ignore")[:300])
                try:
                    os.unlink(combined_path)
                except Exception:
                    pass
            except Exception as e:
                logger.warning("method 1 (binary concat) exception: %s", e)

        # ─── روش 2: concat demuxer با stream copy و aac_adtstoasc (برای MPEG-TS) ───
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            for p in seg_paths:
                if p and os.path.exists(p):
                    p_escaped = p.replace("'", "'\\''")
                    f.write(f"file '{p_escaped}'\n")
            list_path = f.name

        try:
            cmd2 = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", list_path,
                "-c", "copy",
                "-bsf:a", "aac_adtstoasc",
                "-movflags", "+faststart",
                out_path,
            ]
            result2 = subprocess.run(cmd2, capture_output=True, timeout=1800)
            if result2.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                logger.info("concat succeeded (method 2: concat demuxer + aac_adtstoasc)")
                return True

            # ─── روش 3: concat demuxer بدون bitstream filter ───
            cmd3 = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", list_path,
                "-c", "copy",
                "-movflags", "+faststart",
                out_path,
            ]
            result3 = subprocess.run(cmd3, capture_output=True, timeout=1800)
            if result3.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                logger.info("concat succeeded (method 3: concat demuxer copy only)")
                return True

            # ─── روش 4: re-encode (fallback نهایی) ───
            cmd4 = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", list_path,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                out_path,
            ]
            result4 = subprocess.run(cmd4, capture_output=True, timeout=3600)
            if result4.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                logger.info("concat succeeded (method 4: re-encode)")
                return True

            # ─── روش 5: ffmpeg concat با -f concat و -c copy و بدون -bsf (فایل list با مسیر مطلق) ───
            # این روش برای بعضی از سگمنت‌هایی که روش‌های قبلی روشون fail می‌شه کار می‌کنه
            try:
                # اگه init_path هست، اون رو هم به لیست اضافه کن
                all_paths = []
                if init_path and os.path.exists(init_path):
                    all_paths.append(init_path)
                all_paths.extend([p for p in seg_paths if p and os.path.exists(p)])

                with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
                    for p in all_paths:
                        # مسیر مطلق با escape
                        p_abs = os.path.abspath(p)
                        p_escaped = p_abs.replace("'", "'\\''")
                        f.write(f"file '{p_escaped}'\n")
                    list_path2 = f.name

                cmd5 = [
                    "ffmpeg", "-y",
                    "-f", "concat", "-safe", "0",
                    "-i", list_path2,
                    "-c", "copy",
                    "-movflags", "+faststart",
                    out_path,
                ]
                result5 = subprocess.run(cmd5, capture_output=True, timeout=3600)
                try:
                    os.unlink(list_path2)
                except Exception:
                    pass
                if result5.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    logger.info("concat succeeded (method 5: absolute paths + copy)")
                    return True
            except Exception as e:
                logger.warning("method 5 failed: %s", e)

            logger.error("ffmpeg concat failed all methods. Last stderr: %s",
                         result4.stderr.decode("utf-8", errors="ignore")[:500])
            return False
        finally:
            try:
                os.unlink(list_path)
            except Exception:
                pass
    except Exception as e:
        logger.error("concat error: %s", e)
        return False


# ═══════════════════════════════════════════════════════════
#   Subtitle extraction (Persian)
# ═══════════════════════════════════════════════════════════


async def get_persian_subtitle(
    imdb_id: str,
    tmdb_id: Optional[str] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    out_dir: Optional[str] = None,
    title: Optional[str] = None,
) -> Optional[str]:
    """
    گرفتن زیرنویس فارسی از چندین منبع.

    ترتیب جستجو:
      1. core.vidzee.wtf/subs (سرور Vidzee) — بهترین منبع
      2. sub.vdrk.site (سرور 2Embed)
      3. subs.videasy.to (سرورهای Videasy/Vidking) — عموماً انگلیسی
      4. subf2m.co (بزرگ‌ترین آرشیو فارسی — mirror Subscene)
      5. podnapisi.net (آرشیو رسمی با API)
      6. subtitlecat.com (ترجمه‌ی ماشینی on-demand — z19)

    Args:
        imdb_id: e.g. "tt33071426"
        tmdb_id: اختیاری — اگه داده نشه، از imdb_id استخراج می‌شه
        season, episode: برای سریال
        out_dir: مسیر ذخیره فایل زیرنویس

    Returns:
        مسیر فایل زیرنویس VTT، یا None اگه زیرنویس فارسی پیدا نشد.
    """
    if not imdb_id:
        return None
    if not imdb_id.startswith("tt"):
        imdb_id = f"tt{imdb_id}"

    if not tmdb_id:
        tmdb_id = await _get_tmdb_id(imdb_id)
        if not tmdb_id:
            return None

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # ─── Source 1: Vidzee subs API ──────────────────────────
    try:
        if season and episode:
            sub_api = f"https://core.vidzee.wtf/subs/tv/{tmdb_id}/{season}/{episode}"
        else:
            sub_api = f"https://core.vidzee.wtf/subs/movie/{tmdb_id}"

        async with AsyncSession() as s:
            r = await s.get(sub_api, impersonate=_BROWSER_IMPERSONATE, timeout=15,
                            headers={"User-Agent": _USER_AGENT,
                                     "Referer": "https://player.vidzee.wtf/"})
            if r.status_code == 200:
                subs_list = r.json()
                # پیدا کردن Persian
                for sub in subs_list:
                    if "persian" in sub.get("label", "").lower() or "farsi" in sub.get("label", "").lower():
                        sub_url = sub.get("file", "")
                        if sub_url:
                            logger.info("Found Persian subtitle on Vidzee: %s", sub_url[:80])
                            return await _download_subtitle_file(sub_url, out_dir, imdb_id, "vidzee")
    except Exception as e:
        logger.debug("Vidzee subs failed: %s", e)

    # ─── Source 2: sub.vdrk.site (2Embed) ──────────────────
    try:
        if season and episode:
            sub_api = f"https://sub.vdrk.site/v2/tv/{tmdb_id}/{season}/{episode}"
        else:
            sub_api = f"https://sub.vdrk.site/v2/movie/{tmdb_id}"

        async with AsyncSession() as s:
            r = await s.get(sub_api, timeout=15,
                            headers={"User-Agent": _USER_AGENT})
            if r.status_code == 200:
                subs_list = r.json()
                for sub in subs_list:
                    label = sub.get("label", "").lower()
                    if "persian" in label or "farsi" in label:
                        sub_url = sub.get("file", "")
                        if sub_url:
                            logger.info("Found Persian subtitle on vdrk: %s", sub_url[:80])
                            return await _download_subtitle_file(sub_url, out_dir, imdb_id, "vdrk")
    except Exception as e:
        logger.debug("vdrk subs failed: %s", e)

    # ─── Source 3: subs.videasy.to ─────────────────────────
    # این منبع معمولاً انگلیسی داره، ولی امتحان می‌کنیم
    try:
        sub_api = f"https://subs.videasy.to/search?id={imdb_id}"
        if season and episode:
            sub_api += f"&season={season}&episode={episode}"

        async with AsyncSession() as s:
            r = await s.get(sub_api, impersonate=_BROWSER_IMPERSONATE, timeout=15,
                            headers={"User-Agent": _USER_AGENT,
                                     "Referer": "https://player.videasy.to/"})
            if r.status_code == 200:
                subs_list = r.json()
                for sub in subs_list:
                    lang = sub.get("language", "").lower()
                    if "persian" in lang or "farsi" in lang or lang == "fa":
                        sub_url = sub.get("url", "")
                        if sub_url:
                            logger.info("Found Persian subtitle on videasy: %s", sub_url[:80])
                            return await _download_subtitle_file(sub_url, out_dir, imdb_id, "videasy")
    except Exception as e:
        logger.debug("videasy subs failed: %s", e)

    # ─── Source 4: subf2m.co (بزرگ‌ترین آرشیو زیرنویس فارسی — mirror Subscene) ──
    # ماژول آماده‌ی ریپو (subf2m_subtitle) تا حالا به هیچ‌جایی وصل نبود —
    # الان به عنوان منبع چهارم به زنجیره وصل شد. برای پیدا شدنش title لازمه.
    if title:
        try:
            try:
                from subf2m_subtitle import get_subtitle_for_imdb
            except ImportError:
                from searcher.imdb.subf2m_subtitle import get_subtitle_for_imdb
            srt_path = await get_subtitle_for_imdb(
                imdb_id, title, season=season, episode=episode, out_dir=out_dir,
            )
            if srt_path and os.path.exists(srt_path):
                logger.info("Found Persian subtitle on subf2m: %s", srt_path)
                return srt_path
        except Exception as e:
            logger.debug("subf2m failed: %s", e)

    # ─── Source 5: podnapisi.net (آرشیو رسمی چندزبانه با API) ──
    try:
        p_path = await _podnapisi_persian(imdb_id, season, episode, out_dir)
        if p_path and os.path.exists(p_path):
            logger.info("Found Persian subtitle on podnapisi: %s", p_path)
            return p_path
    except Exception as e:
        logger.debug("podnapisi failed: %s", e)

    # ─── Source 6: subtitlecat.com (ترجمه‌ی ماشینی on-demand — z19) ──
    # ریورس کامل شد: جستجو روی اسم نسخه‌ها + fa کش‌شده یا ترجمه‌ی gtx —
    # سایت هر زیرنویسی رو به فارسی ترجمه می‌کنه (Google gtx) و کش می‌کنه.
    # برای سریال فقط نسخه‌های مچ دقیق همین SxxEyy (خط‌مشی z15).
    if title:
        try:
            try:
                from subtitlecat_subtitle import get_subtitle_for_imdb
            except ImportError:
                from searcher.imdb.subtitlecat_subtitle import get_subtitle_for_imdb
            scat_path = await get_subtitle_for_imdb(
                imdb_id, title, season=season, episode=episode, out_dir=out_dir,
            )
            if scat_path and os.path.exists(scat_path):
                logger.info("Found Persian subtitle on subtitlecat: %s", scat_path)
                return scat_path
        except Exception as e:
            logger.debug("subtitlecat failed: %s", e)

    logger.info("No Persian subtitle found for %s", imdb_id)
    return None


def _srt_from_zip_bytes(content: bytes, out_dir: Optional[str], imdb_id: str,
                        source: str) -> Optional[str]:
    """استخراج اولین SRT از ZIP زیرنویس + تبدیل به UTF-8."""
    import io
    import zipfile as _zipfile
    if not out_dir:
        out_dir = "/tmp"
    os.makedirs(out_dir, exist_ok=True)
    try:
        zf = _zipfile.ZipFile(io.BytesIO(content))
    except Exception:
        return None
    srt_names = [n for n in zf.namelist() if n.lower().endswith(".srt")]
    if not srt_names:
        return None
    raw = zf.read(srt_names[0])
    decoded = None
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        for enc in ("windows-1256", "cp1256", "iso-8859-6"):
            try:
                cand = raw.decode(enc)
                if any(c in cand for c in "یوره"):
                    decoded = cand
                    break
            except UnicodeDecodeError:
                continue
        if decoded is None:
            decoded = raw.decode("utf-8", errors="replace")
    out_path = os.path.join(out_dir, f"{imdb_id}_{source}_persian.srt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(decoded)
    return out_path


async def _podnapisi_persian(imdb_id: str, season: Optional[int],
                             episode: Optional[int],
                             out_dir: Optional[str]) -> Optional[str]:
    """زیرنویس فارسی از podnapisi.net — API رسمی (همون که Sonarr/Radarr استفاده می‌کنن)."""
    imdb_num = imdb_id.replace("tt", "") if imdb_id.startswith("tt") else imdb_id
    payload = {"languages": ["fa"], "imdbId": int(imdb_num)}
    if season and episode:
        payload["seasons"] = [int(season)]
        payload["episodes"] = [int(episode)]
    async with AsyncSession() as s:
        r = await s.post("https://api.podnapisi.net/v2/subtitles/search/pairs",
                         json=payload, timeout=12,
                         headers={"User-Agent": _USER_AGENT})
        if r.status_code != 200:
            return None
        results = (r.json() or {}).get("results") or []
        if not results:
            return None
        results.sort(key=lambda x: int(x.get("downloads", 0) or 0), reverse=True)
        page_url = results[0].get("url", "") or ""
        m = re.search(r"/subtitles/(\d+)", page_url)
        if not m:
            return None
        zip_url = f"https://www.podnapisi.net/subtitles/{m.group(1)}/download"
        r2 = await s.get(zip_url, timeout=25, allow_redirects=True,
                         headers={"User-Agent": _USER_AGENT})
        if r2.status_code != 200 or r2.content[:4] != b"PK\x03\x04":
            return None
        return _srt_from_zip_bytes(r2.content, out_dir, imdb_id, "podnapisi")


async def _download_subtitle_file(url: str, out_dir: Optional[str], imdb_id: str, source: str) -> Optional[str]:
    """دانلود فایل زیرنویس VTT."""
    if not out_dir:
        out_dir = "/tmp"
    os.makedirs(out_dir, exist_ok=True)

    # تعیین پسوند فایل
    if url.endswith(".vtt"):
        ext = "vtt"
    elif url.endswith(".srt"):
        ext = "srt"
    else:
        ext = "vtt"

    out_path = os.path.join(out_dir, f"{imdb_id}_{source}_persian.{ext}")

    # تعیین Referer بر اساس source
    if source == "vidzee":
        referer = "https://player.vidzee.wtf/"
        origin = "https://player.vidzee.wtf"
    elif source == "videasy":
        referer = "https://player.videasy.to/"
        origin = "https://player.videasy.to"
    else:
        referer = "https://cineby.hair/"
        origin = "https://cineby.hair"

    try:
        async with AsyncSession() as s:
            r = await s.get(url, impersonate=_BROWSER_IMPERSONATE, timeout=30,
                            headers={"User-Agent": _USER_AGENT,
                                     "Referer": referer,
                                     "Origin": origin})
            if r.status_code != 200:
                logger.warning("Subtitle download HTTP %d for %s", r.status_code, url[:80])
                return None
            with open(out_path, "wb") as f:
                f.write(r.content)
            logger.info("Subtitle saved: %s (%d bytes)", out_path, len(r.content))
            return out_path
    except Exception as e:
        logger.error("Subtitle download failed: %s", e)
        return None


# ═══════════════════════════════════════════════════════════
#   Local ffmpeg subtitle embedding (softsub - no re-encode)
# ═══════════════════════════════════════════════════════════

# 🆕 z27: فرمت SRT/mov_text اصلاً اطلاعات رنگ/استایل نداره و پلیر پیش‌فرض سفید
# نشونش می‌ده. با تبدیل به ASS و جاسازی در MKV، استایل داخل فایل ذخیره می‌شه و
# VLC (libass) بدون هیچ تنظیمی همون رنگ/فونت رو روی هر دستگاهی نشون می‌ده.
# رنگ ASS به فرمت &HAABBGGRR هست: زرد = &H0000FFFF (B=00, G=FF, R=FF)
# 🆕 z28: MarginV از 45 به 15 کاهش پیدا کرد (PlayResY=288) — زیرنویس قبلاً ~۱۵٪
# از پایین صفحه بود که «بالا» به نظر می‌رسید؛ الان ~۵٪ = موقعیت استاندارد زیرنویس
_SUB_STYLE_TPL = (
    "Style: {name},Arial,20,&H0000FFFF,&H000000FF,&H00000000,&H00000000,"
    "-1,0,0,0,100,100,0,0,1,2,1,2,40,40,15,1"
)


def _styled_ass_from_subtitle(subtitle_path: str, out_dir: str) -> Optional[str]:
    """
    🆕 z27: تبدیل زیرنویس (SRT/VTT/ASS) به فایل ASS استایل‌دار
    (زرد + بولد + دورخط مشکی + فونت درشت‌تر — خوانا در گوشی).

    - مرحله ۱: تبدیل به ASS با ffmpeg (فرآیند متنی — سریع، بدون re-encode)
    - مرحله ۲: پچ خطوط Style — نام استایل حفظ می‌شه چون خطوط Dialogue بهش ارجاع می‌دن

    Returns:
        مسیر فایل ASS استایل‌دار اگه موفق، None در غیر این صورت
        (caller به مسیرهای قدیمی fallback می‌کنه).
    """
    try:
        os.makedirs(out_dir, exist_ok=True)
        out_ass = os.path.join(out_dir, f"styled_sub_{int(time.time())}.ass")

        # مرحله ۱: تبدیل به ASS
        cmd = ["ffmpeg", "-y", "-i", subtitle_path, "-c:s", "ass", out_ass]
        r = subprocess.run(cmd, capture_output=True, timeout=120)
        if r.returncode != 0 or not os.path.exists(out_ass) or os.path.getsize(out_ass) == 0:
            logger.warning("styled ASS convert failed: %s",
                           r.stderr.decode("utf-8", errors="ignore")[:300])
            return None

        # مرحله ۲: پچ خط Style
        with open(out_ass, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        def _repl(m):
            return _SUB_STYLE_TPL.format(name=(m.group(1).strip() or "Default"))

        new_content, n = re.subn(r"(?m)^Style:\s*([^,]+?),.*$", _repl, content)
        if n == 0:
            # فایل Style نداره — بعد از خط Format (یا خود هدر) اضافه‌ش کن
            styled_line = _SUB_STYLE_TPL.format(name="Default")
            new_content, n2 = re.subn(
                r"(?m)^(Format:.*\n)",
                lambda m2: m2.group(1) + styled_line + "\n",
                content, count=1)
            if n2 == 0:
                new_content = content.replace(
                    "[V4+ Styles]",
                    "[V4+ Styles]\n" + styled_line, 1)

        with open(out_ass, "w", encoding="utf-8") as f:
            f.write(new_content)

        logger.info("[z27] subtitle converted to yellow-styled ASS: %s",
                    os.path.basename(out_ass))
        return out_ass
    except Exception as e:
        logger.warning("styled ASS error: %s", e)
        return None


def embed_subtitle_soft(
    video_path: str,
    subtitle_path: str,
    out_path: str,
    sync_info: Optional[dict] = None,
) -> Optional[str]:
    """
    قرار دادن زیرنویس به‌صورت softsub داخل فایل ویدیو (بدون re-encode).
    این کار خیلی سریع هست (فقط remux) و زیرنویس قابل روشن/خاموش شدن در VLC هست.

    🆕 z27: روش اصلی حالا زیرنویس رو به ASS استایل‌دار (زرد + بولد + دورخط مشکی)
    تبدیل و در کانتینر MKV جاسازی می‌کنه — VLC استایل‌ها رو رعایت می‌کنه و زیرنویس
    روی هر دستگاهی زرد دیده می‌شه (بدون نیاز به تغییر تنظیمات VLC).
    اگه این مسیر شکست خورد، به روش‌های قدیمی (MP4/mov_text و MKV/srt) برمی‌گرده.

    🆕 z34: قبل از جاسازی، زیرنویس به‌طور خودکار با ویدیو همگام می‌شه
    (ویدیوهای PAL-spun/تندتر سرورها vs ساب‌های 23.976-timed → درِیف تجمعی).
    جزئیات تصمیم در sync_info پر می‌شه (applied/factor/reason).

    Args:
        video_path: مسیر فایل ویدیو
        subtitle_path: مسیر فایل زیرنویس (VTT یا SRT)
        out_path: مسیر فایل خروجی (پسوند نهایی خروجی ممکنه .mkv باشه)
        sync_info: dict اختیاری — بعد از اجرا با نتیجه‌ی sync پر می‌شه

    Returns:
        مسیر فایل خروجی اگه موفق، None در غیر این صورت.
    """
    try:
        # ─── 🆕 z34: همگام‌سازی خودکار زیرنویس با ویدیو ───
        try:
            try:
                from searcher.imdb.subtitle_sync import auto_sync_subtitle
            except ImportError:
                from subtitle_sync import auto_sync_subtitle
            subtitle_path, _sync_factor, _sync_reason = auto_sync_subtitle(
                video_path, subtitle_path, sync_info=sync_info)
        except Exception as _sync_err:
            logger.warning("[z34] subtitle auto-sync skipped: %s", _sync_err)

        out_dir = os.path.dirname(out_path) or "."
        os.makedirs(out_dir, exist_ok=True)

        # ─── 🆕 z27 روش اصلی: ASS زرد استایل‌دار داخل MKV (فقط remux) ───
        styled_ass = _styled_ass_from_subtitle(subtitle_path, out_dir)
        if styled_ass:
            mkv_out = os.path.splitext(out_path)[0] + ".mkv"
            cmd = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-i", styled_ass,
                "-map", "0:v:0",   # اولین استریم ویدیو (کاور/attached_pic رو برنمی‌داره)
                "-map", "0:a?",    # همه‌ی ترک‌های صوتی (اگه باشن)
                "-map", "1:0",     # ترک زیرنویس استایل‌دار
                "-c", "copy",      # بدون re-encode — فقط remux
                "-metadata:s:s:0", "language=far",
                "-metadata:s:s:0", "title=Persian",
                mkv_out,
            ]

            logger.info("[z27] embedding styled softsub: %s + %s -> %s",
                        os.path.basename(video_path), os.path.basename(styled_ass),
                        os.path.basename(mkv_out))

            result = subprocess.run(cmd, capture_output=True, timeout=600)  # 10 min timeout

            if result.returncode == 0 and os.path.exists(mkv_out) and os.path.getsize(mkv_out) > 0:
                logger.info("[z27] styled softsub (yellow ASS/MKV) complete: %s (%.1f MB)",
                            mkv_out, os.path.getsize(mkv_out) / 1024 / 1024)
                try:
                    os.unlink(styled_ass)
                except Exception:
                    pass
                return mkv_out

            logger.warning("styled MKV embed failed, falling back to legacy paths: %s",
                           result.stderr.decode("utf-8", errors="ignore")[:300])

        # ─── مسیرهای قدیمی (fallback) ───
        # تشخیص فرمت خروجی بر اساس پسوند
        if out_path.endswith(".mkv"):
            sub_codec = "srt"
        else:
            # MP4 از mov_text برای زیرنویس استفاده می‌کنه
            sub_codec = "mov_text"
            # اگه زیرنویس VTT هست، برای MP4 به mov_text تبدیل می‌شه

        # ffmpeg command برای softsub
        # -c copy = video و audio رو copy کن (بدون re-encode)
        # -c:s mov_text = زیرنویس رو به mov_text تبدیل کن (برای MP4)
        # -metadata:s:s:0 language=far = تنظیم زبان زیرنویس به فارسی
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", subtitle_path,
            "-c", "copy",
            "-c:s", sub_codec,
            "-metadata:s:s:0", "language=far",
            "-metadata:s:s:0", "title=Persian",
            "-movflags", "+faststart",
            out_path,
        ]

        logger.info("Embedding subtitle as softsub: %s + %s -> %s",
                    os.path.basename(video_path), os.path.basename(subtitle_path), os.path.basename(out_path))

        result = subprocess.run(cmd, capture_output=True, timeout=600)  # 10 min timeout

        if result.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            logger.info("Softsub embedding complete: %s (%.1f MB)",
                        out_path, os.path.getsize(out_path) / 1024 / 1024)
            return out_path

        # fallback: MKV (MP4 گاهی با mov_text مشکل داره)
        logger.warning("MP4 softsub failed, trying MKV: %s",
                      result.stderr.decode("utf-8", errors="ignore")[:300])

        mkv_out = out_path.rsplit(".", 1)[0] + ".mkv"
        cmd2 = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", subtitle_path,
            "-c", "copy",
            "-c:s", "srt",
            "-metadata:s:s:0", "language=far",
            "-metadata:s:s:0", "title=Persian",
            mkv_out,
        ]

        result2 = subprocess.run(cmd2, capture_output=True, timeout=600)
        if result2.returncode == 0 and os.path.exists(mkv_out) and os.path.getsize(mkv_out) > 0:
            logger.info("Softsub embedding complete (MKV): %s", mkv_out)
            return mkv_out

        logger.error("Softsub embedding failed: %s",
                     result2.stderr.decode("utf-8", errors="ignore")[:500])
        return None
    except Exception as e:
        logger.error("Softsub embedding error: %s", e)
        return None


# Compatibility alias (keep old name working)
def burn_subtitle_local(video_path: str, subtitle_path: str, out_path: str) -> Optional[str]:
    """Alias for embed_subtitle_soft (softsub, not hardcode)"""
    return embed_subtitle_soft(video_path, subtitle_path, out_path)


# ═══════════════════════════════════════════════════════════
#   Quick test
# ═══════════════════════════════════════════════════════════


async def _test():
    print("=== Test get_qualities: tt33071426 (The Drama) ===")
    qualities = await get_qualities("tt33071426")
    for q in qualities:
        print(f"  {q['label']} - {q['resolution']} - {q['bandwidth']} - {q['server']}")
        print(f"    URL: {q['url'][:100]}")

    print("\n=== Test get_persian_subtitle ===")
    sub_path = await get_persian_subtitle("tt33071426", out_dir="/tmp/imdbplay_test")
    if sub_path:
        print(f"  ✅ Subtitle saved: {sub_path}")
    else:
        print("  ❌ No Persian subtitle found")


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    asyncio.run(_test())
