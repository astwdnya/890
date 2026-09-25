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
  - s10 CastleTV   (AES-128-CBC + اپ هندی) — 480p/720p/1080p بومی
  - s11 VaPlayer   (API مستقیم) — master مولتی‌کیفیت
  - s12 VixSrc     (token embed) — master مولتی‌کیفیت

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

# ─── دانلود همزمان سگمنت‌ها (قابل تنظیم با متغیرهای محیطی) ───
# گلوگاه قبلی: Semaphore(10) + AsyncSession با max_clients پیش‌فرض 10
# یعنی هر لحظه حداکثر 10 سگمنت در حال دانلود بود. برای لینک‌های کم‌سرعت/
# پرتأخیر (مثل مسیر بین‌الملل ایران) هر کانکشن TCP محدود میشه و افزایش
# تعداد کانکشن‌های همزمان سرعت را چند برابر می‌کند.
SEGMENT_CONCURRENCY = int(os.environ.get("IMDB_SEG_CONCURRENCY", "24"))   # سگمنت همزمان
SESSION_MAX_CLIENTS = int(os.environ.get("IMDB_MAX_CLIENTS", "32"))       # حداکثر curl handle همزمان

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
    {"id": "s10", "name": "CastleTV",  "prefer_imdb": False, "quality_hint": "Auto (480/720/1080)"},
    {"id": "s11", "name": "VaPlayer",  "prefer_imdb": True,  "quality_hint": "Auto (multi-quality)"},
    {"id": "s12", "name": "VixSrc",    "prefer_imdb": False, "quality_hint": "Auto (multi-quality)"},
]


# ═══════════════════════════════════════════════════════════
#   TMDB / IMDb conversion
# ═══════════════════════════════════════════════════════════


async def _get_tmdb_id(imdb_id: str) -> Optional[str]:
    """تبدیل imdb_id به tmdb_id با استفاده از TMDB API."""
    if not imdb_id:
        return None
    if not imdb_id.startswith("tt"):
        imdb_id = f"tt{imdb_id}"
    url = f"https://api.themoviedb.org/3/find/{imdb_id}?api_key={_TMDB_API_KEY}&external_source=imdb_id"
    try:
        async with AsyncSession() as s:
            r = await s.get(url, impersonate=_BROWSER_IMPERSONATE, timeout=15,
                            headers={"User-Agent": _USER_AGENT})
            if r.status_code != 200:
                logger.warning("TMDB find HTTP %d for %s", r.status_code, imdb_id)
                return None
            d = r.json()
            movies = d.get("movie_results", [])
            tv = d.get("tv_results", [])
            if movies:
                tmdb_id = str(movies[0].get("id", ""))
                logger.info("TMDB find %s -> movie %s", imdb_id, tmdb_id)
                return tmdb_id
            if tv:
                tmdb_id = str(tv[0].get("id", ""))
                logger.info("TMDB find %s -> tv %s", imdb_id, tmdb_id)
                return tmdb_id
            logger.warning("TMDB find %s: no results", imdb_id)
            return None
    except Exception as e:
        logger.warning("TMDB find failed: %s", e)
        return None


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
#   Servers 10-12: CastleTV / VaPlayer / VixSrc
#   (اضافه‌شده برای کیفیت‌های پایین‌تر واقعی — بخصوص 480p)
# ═══════════════════════════════════════════════════════════

# ─── Server 10: CastleTV (api.hlowb.com — بک‌اند اپ اندروید) ───
# کیفیت‌ها با «رزولوشن عددی» گرفته می‌شن: 1=480p، 2=720p، 3=1080p
# یعنی 480p بومی داره (بقیه سرورها معمولاً 1080/720/360 می‌دن).
# پاسخ API با AES-128-CBC رمزنگاری شده؛ کلید از getSecurityKey می‌آد.
_CASTLE_BASE = "https://api.hlowb.com"
_CASTLE_PKG = "com.external.castle"
_CASTLE_CHANNEL = "IndiaA"
_CASTLE_CLIENT = "1"
_CASTLE_LANG = "en-US"
_CASTLE_APK_SIGN_KEY = "ED0955EB04E67A1D9F3305B95454FED485261475"
_CASTLE_API_HEADERS = {
    "User-Agent": "okhttp/4.9.3",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "Keep-Alive",
    "Referer": "https://api.hlowb.com",
}
_CASTLE_SUFFIX = b"T!BgJB"
_CASTLE_RES_HEIGHT = {3: 1080, 2: 720, 1: 480}
_CASTLE_KNOWN_HEIGHTS = {240, 360, 480, 540, 576, 720, 1080, 1440, 2160}

try:  # cryptography از قبل در requirements.txt پروژه هست
    from cryptography.hazmat.primitives.ciphers import Cipher as _CastleCipher
    from cryptography.hazmat.primitives.ciphers import algorithms as _CastleAlgo
    from cryptography.hazmat.primitives.ciphers import modes as _CastleMode
    _CASTLE_CRYPTO_OK = True
except Exception:
    _CASTLE_CRYPTO_OK = False


def _castle_derive_key(security_key_b64: str) -> bytes:
    """کلید AES-128 از security key (base64) + پسوند ثابت."""
    kb = base64.b64decode(security_key_b64)
    combined = kb + _CASTLE_SUFFIX
    if len(combined) < 16:
        combined = combined + b"\x00" * (16 - len(combined))
    return combined[:16]


def _castle_decrypt(cipher_b64: str, key: bytes) -> str:
    """AES-128-CBC (key=iv) + حذف padding PKCS7."""
    raw = base64.b64decode(cipher_b64)
    dec = _CastleCipher(_CastleAlgo.AES(key), _CastleMode.CBC(key)).decryptor()
    out = dec.update(raw) + dec.finalize()
    if out:
        pad = out[-1]
        if 1 <= pad <= 16:
            out = out[:-pad]
    return out.decode("utf-8", errors="replace")


def _castle_extract_cipher(text: str) -> str:
    """پاسخ ممکنه JSON با data یا متن خام cipher باشه (مثل extractCipher اصلی)."""
    t = (text or "").strip()
    if t.startswith("{"):
        try:
            j = json.loads(t)
            if isinstance(j.get("data"), str) and j["data"].strip():
                return j["data"].strip()
        except Exception:
            pass
    return t


def _castle_safe_parse(txt: str) -> dict:
    """مثل castleSafeParse: اعداد ۱۶+ رقمی داخل JSON به رشته تبدیل می‌شن."""
    safe = re.sub(r"([:{\[,]\s*)(\d{16,})", r'\1"\2"', txt)
    return json.loads(safe)


def _castle_year_of(date_str) -> Optional[int]:
    try:
        return int((date_str or "")[:4]) or None
    except (ValueError, TypeError):
        return None


async def _get_tmdb_find_info(imdb_id: str) -> Optional[dict]:
    """عنوان/سال/نوع از TMDB find — برای جستجوی CastleTV و تبدیل season/episode."""
    if not imdb_id:
        return None
    if not imdb_id.startswith("tt"):
        imdb_id = f"tt{imdb_id}"
    url = (f"https://api.themoviedb.org/3/find/{imdb_id}"
           f"?api_key={_TMDB_API_KEY}&external_source=imdb_id")
    try:
        async with AsyncSession() as s:
            r = await s.get(url, impersonate=_BROWSER_IMPERSONATE, timeout=15,
                            headers={"User-Agent": _USER_AGENT})
            if r.status_code != 200:
                return None
            d = r.json()
        if d.get("movie_results"):
            m = d["movie_results"][0]
            return {"tmdb_id": str(m.get("id", "")),
                    "title": m.get("title") or m.get("original_title", ""),
                    "year": _castle_year_of(m.get("release_date")), "is_tv": False}
        if d.get("tv_results"):
            t = d["tv_results"][0]
            return {"tmdb_id": str(t.get("id", "")),
                    "title": t.get("name") or t.get("original_name", ""),
                    "year": _castle_year_of(t.get("first_air_date")), "is_tv": True}
    except Exception as e:
        logger.debug("TMDB find info failed for %s: %s", imdb_id, e)
    return None


async def _castletv_get_stream(tmdb_id: str, imdb_id: str,
                               season: Optional[int], episode: Optional[int]) -> Optional[dict]:
    """استخراج استریم از CastleTV — تنها سرور با 480p بومی تضمین‌شده.

    جریان: getSecurityKey → searchByKeyword (عنوان+سال) → movie details
           → (سریال: movieId فصل مربوطه) → episode → getVideo2 برای res=3/2/1.
    خروجی: استریم HLS با لیست qualities (هر رزولوشن یک URL مستقل).
    """
    if not _CASTLE_CRYPTO_OK:
        logger.debug("CastleTV skipped: cryptography module missing")
        return None
    info = await _get_tmdb_find_info(imdb_id)
    if not info or not info.get("title"):
        return None
    title = (info["title"] or "").strip()
    year = info.get("year")
    is_tv = bool(season and episode)

    try:
        async with AsyncSession() as s:
            # ۱) security key
            r = await s.get(
                f"{_CASTLE_BASE}/v0.1/system/getSecurityKey/1"
                f"?channel={_CASTLE_CHANNEL}&clientType={_CASTLE_CLIENT}&lang={_CASTLE_LANG}",
                impersonate=_BROWSER_IMPERSONATE, timeout=15, headers=_CASTLE_API_HEADERS)
            if r.status_code != 200:
                return None
            j = r.json()
            if str(j.get("code")) != "200" or not j.get("data"):
                return None
            key = _castle_derive_key(j["data"])

            # ۲) جستجو با «عنوان سال»
            kw = quote_plus(f"{title} {year}" if year else title)
            r2 = await s.get(
                f"{_CASTLE_BASE}/film-api/v1.1.0/movie/searchByKeyword"
                f"?channel={_CASTLE_CHANNEL}&clientType={_CASTLE_CLIENT}&keyword={kw}"
                f"&lang={_CASTLE_LANG}&mode=1&packageName={_CASTLE_PKG}&page=1&size=30",
                impersonate=_BROWSER_IMPERSONATE, timeout=15, headers=_CASTLE_API_HEADERS)
            if r2.status_code != 200:
                return None
            search = _castle_safe_parse(_castle_decrypt(_castle_extract_cipher(r2.text), key))
            sdata = search.get("data") if isinstance(search.get("data"), dict) else search
            rows = sdata.get("rows") or []
            if not rows:
                logger.info("CastleTV: no search results for %r", title)
                return None

            # ۳) تطبیق عنوان: دقیق → contains → اولین نتیجه
            tl = title.lower().strip()
            match = None
            for row in rows:
                nm = str(row.get("title") or row.get("name") or "").lower().strip()
                if nm == tl:
                    match = row
                    break
            if match is None:
                for row in rows:
                    nm = str(row.get("title") or row.get("name") or "").lower()
                    if tl and (tl in nm or nm in tl):
                        match = row
                        break
            if match is None:
                match = rows[0]
            castle_id = str(match.get("id") or match.get("redirectId") or "")
            if not castle_id:
                return None

            # ۴) details (+ سوییچ به movieId فصل برای سریال‌ها)
            async def _details(mid: str) -> dict:
                url = (f"{_CASTLE_BASE}/film-api/v1.9.9/movie?channel={_CASTLE_CHANNEL}"
                       f"&clientType={_CASTLE_CLIENT}&lang={_CASTLE_LANG}"
                       f"&movieId={mid}&packageName={_CASTLE_PKG}")
                rr = await s.get(url, impersonate=_BROWSER_IMPERSONATE, timeout=15,
                                 headers=_CASTLE_API_HEADERS)
                if rr.status_code != 200:
                    return {}
                det = _castle_safe_parse(_castle_decrypt(_castle_extract_cipher(rr.text), key))
                return det.get("data") if isinstance(det.get("data"), dict) else det

            det = await _details(castle_id)
            active_id = castle_id
            if is_tv:
                for sv in det.get("seasons") or []:
                    if sv.get("number") == season and sv.get("movieId"):
                        new_id = str(sv["movieId"])
                        if new_id != castle_id:
                            active_id = new_id
                            det = await _details(active_id)
                        break

            # ۵) قسمت مورد نظر
            eps = det.get("episodes") or []
            if is_tv:
                ep = next((e for e in eps if e.get("number") == episode), None)
            else:
                ep = eps[0] if eps else None
            if not ep or not ep.get("id"):
                logger.info("CastleTV: episode %s/%s not found for %r", season, episode, title)
                return None
            episode_id = str(ep["id"])

            # ۶) زبان: انگلیسی → اولین زبان → بدون زبان (shared)
            tracks = ep.get("tracks") or []
            lang_id = None
            for t in tracks:
                nm = str(t.get("languageName") or t.get("abbreviate") or "").lower()
                if nm.startswith("en"):
                    lang_id = str(t.get("languageId"))
                    break
            if lang_id is None and tracks:
                lang_id = str(tracks[0].get("languageId"))

            async def _getvideo(reso: int, lang: Optional[str]) -> list:
                body = {
                    "mode": "1", "appMarket": "GuanWang", "clientType": _CASTLE_CLIENT,
                    "woolUser": "false", "apkSignKey": _CASTLE_APK_SIGN_KEY,
                    "androidVersion": "13", "movieId": active_id, "episodeId": episode_id,
                    "isNewUser": "true", "resolution": str(reso), "packageName": _CASTLE_PKG,
                }
                if lang:
                    body["languageId"] = lang
                r4 = await s.post(
                    f"{_CASTLE_BASE}/film-api/v2.0.1/movie/getVideo2"
                    f"?clientType={_CASTLE_CLIENT}&packageName={_CASTLE_PKG}"
                    f"&channel={_CASTLE_CHANNEL}&lang={_CASTLE_LANG}",
                    impersonate=_BROWSER_IMPERSONATE, timeout=20,
                    headers={**_CASTLE_API_HEADERS, "Content-Type": "application/json"},
                    data=json.dumps(body))
                if r4.status_code != 200:
                    return []
                v = _castle_safe_parse(_castle_decrypt(_castle_extract_cipher(r4.text), key))
                vd = v.get("data") if isinstance(v.get("data"), dict) else v
                urls = [x.get("url") for x in (vd.get("videos") or []) if x.get("url")]
                if not urls and vd.get("videoUrl"):
                    urls = [vd["videoUrl"]]
                return urls or []

            # ۷) هر ۳ رزولوشن (۱=480، ۲=720، ۳=1080) — هر کدوم URL مستقل
            #    CDN سگمنت هاست‌های چرخشی داره؛ بعضی هاست‌ها بعضی IPها رو 403 می‌دن،
            #    پس هر URL قبل از پذیرش probe می‌شه و روی شکست، getVideo2 دوباره
            #    صدا زده می‌شه تا هاست سالم جدید بگیریم.
            collected: List[Tuple[int, str]] = []
            seen_urls = set()

            def _height_from_url(u: str) -> int:
                path = urlparse(u).path
                m = re.search(r"/(\d{3,4})/", path)
                if m and int(m.group(1)) in _CASTLE_KNOWN_HEIGHTS:
                    return int(m.group(1))
                return 0

            async def _url_works(u: str) -> bool:
                try:
                    rr = await s.get(u, impersonate=_BROWSER_IMPERSONATE, timeout=12,
                                     headers={"User-Agent": _USER_AGENT,
                                              "Accept-Encoding": "identity"})
                    return rr.status_code == 200
                except Exception:
                    return False

            async def _collect(reso_seq) -> None:
                for reso in reso_seq:
                    reso_height = _CASTLE_RES_HEIGHT.get(reso, 0)
                    for _attempt in range(3):
                        try:
                            urls = await _getvideo(reso, lang_id)
                            if not urls and lang_id:
                                urls = await _getvideo(reso, None)  # بدون زبان (shared)
                        except Exception as e:
                            logger.debug("CastleTV getVideo2 res=%s failed: %s", reso, e)
                            break
                        fresh = [u for u in urls if u and u not in seen_urls]
                        if not fresh:
                            # همه URLهای تکراری بودن؛ getVideo2 دوباره؟ نه — بی‌خیال
                            if urls:
                                break
                            continue
                        got = False
                        for u in fresh:
                            if not await _url_works(u):
                                continue  # هاست 403/خراب — URL بعدی/تازه
                            seen_urls.add(u)
                            collected.append((_height_from_url(u) or reso_height, u))
                            got = True
                            break
                        if got:
                            break
                        # هیچ‌کدوم از URLهای این دور کار نکرد → دور بعد هاست تازه می‌دیم

            await _collect((3, 2, 1))
            if not collected and lang_id:
                # آخرین تلاش: بدون languageId
                lang_id = None
                await _collect((3, 2, 1))
            if not collected:
                logger.info("CastleTV: no stream urls for %r (%s/%s)", title, season, episode)
                return None

            collected.sort(key=lambda x: -x[0])
            qualities = [{"label": f"{h}p", "url": u} for h, u in collected if h]
            logger.info("CastleTV %s S%sE%s -> %s quality url(s)",
                        imdb_id, season, episode, [q["label"] for q in qualities])
            return {
                "url": collected[0][1],
                "type": "hls",
                "headers": {"User-Agent": _USER_AGENT, "Accept-Encoding": "identity"},
                "server": "CastleTV",
                "qualities": qualities,
            }
    except Exception as e:
        logger.warning("CastleTV error: %s", e)
        return None


# ─── Server 11: VaPlayer (streamdata.vaplayer.ru) ────────────
# یک درخواست ساده با imdb_id؛ لیست master m3u8 مولتی‌کیفیت برمی‌گردونه.
_VAPLAYER_API = "https://streamdata.vaplayer.ru/api.php"
_VAPLAYER_ORIGIN = "https://nextgencloudfabric.com"


async def _vaplayer_get_stream(imdb_id: str, season: Optional[int],
                               episode: Optional[int]) -> Optional[dict]:
    if not imdb_id:
        return None
    is_tv = bool(season and episode)
    params = f"imdb={imdb_id}&type={'tv' if is_tv else 'movie'}"
    if is_tv:
        params += f"&season={season}&episode={episode}"
    referer = (f"{_VAPLAYER_ORIGIN}/embed/tv/{imdb_id}/{season}/{episode}" if is_tv
               else f"{_VAPLAYER_ORIGIN}/embed/movie/{imdb_id}")
    try:
        async with AsyncSession() as s:
            r = await s.get(f"{_VAPLAYER_API}?{params}",
                            impersonate=_BROWSER_IMPERSONATE, timeout=20,
                            headers={"User-Agent": _USER_AGENT, "Referer": referer,
                                     "Origin": _VAPLAYER_ORIGIN})
            if r.status_code != 200:
                logger.debug("VaPlayer HTTP %s", r.status_code)
                return None
            d = r.json()
            if str(d.get("status_code", "200")) not in ("200", 200):
                return None
            urls = ((d.get("data") or {}).get("stream_urls")) or []
            for u in urls:
                if u and ".m3u8" in u:
                    logger.info("VaPlayer %s -> %s", imdb_id, u[:80])
                    return {
                        "url": u,
                        "type": "hls",
                        "headers": {"Referer": f"{_VAPLAYER_ORIGIN}/",
                                    "Origin": _VAPLAYER_ORIGIN,
                                    "User-Agent": _USER_AGENT},
                        "server": "VaPlayer",
                    }
        return None
    except Exception as e:
        logger.warning("VaPlayer error: %s", e)
        return None


# ─── Server 12: VixSrc (vixsrc.to — master مولتی‌کیفیت ایتالیایی) ───
# نکته: از بعضی IPهای دیتاسنتری Cloudflare بلاک می‌شه (403)؛ در آن حالت
# خطا graceful نادیده گرفته می‌شه و سرورهای بعدی امتحان می‌شن.
_VIXSRC_BASE = "https://vixsrc.to"


async def _vixsrc_get_stream(tmdb_id: str, season: Optional[int],
                             episode: Optional[int]) -> Optional[dict]:
    if not tmdb_id:
        return None
    is_tv = bool(season and episode)
    try:
        async with AsyncSession() as s:
            api_url = (f"{_VIXSRC_BASE}/api/tv/{tmdb_id}/{season}/{episode}" if is_tv
                       else f"{_VIXSRC_BASE}/api/movie/{tmdb_id}")
            r = await s.get(api_url, impersonate=_BROWSER_IMPERSONATE, timeout=15,
                            headers={"User-Agent": _USER_AGENT,
                                     "Referer": f"{_VIXSRC_BASE}/",
                                     "Origin": _VIXSRC_BASE,
                                     "Accept": "application/json, text/javascript, */*; q=0.01"})
            if r.status_code != 200:
                logger.debug("VixSrc api HTTP %s", r.status_code)
                return None
            src = r.json().get("src")
            if not src:
                return None
            # صفحه embed → token/expires/playlist
            r2 = await s.get(f"{_VIXSRC_BASE}{src}", impersonate=_BROWSER_IMPERSONATE,
                             timeout=15, headers={"User-Agent": _USER_AGENT,
                                                  "Referer": api_url,
                                                  "Accept": "text/html,application/xhtml+xml,*/*"})
            if r2.status_code != 200:
                return None
            html = r2.text
            token = re.search(r"token['\"]\s*:\s*['\"]([^'\"]+)", html)
            expires = re.search(r"expires['\"]\s*:\s*['\"]([^'\"]+)", html)
            playlist = re.search(r"url\s*:\s*['\"]([^'\"]+)", html)
            if not (token and expires and playlist):
                logger.info("VixSrc: token/expires/playlist not found in embed page")
                return None
            pl = playlist.group(1)
            sep = "&" if "?" in pl else "?"
            master = f"{pl}{sep}token={token.group(1)}&expires={expires.group(1)}&h=1"
            logger.info("VixSrc %s -> %s", tmdb_id, master[:80])
            return {
                "url": master,
                "type": "hls",
                "headers": {"Referer": api_url, "User-Agent": _USER_AGENT},
                "server": "VixSrc",
            }
    except Exception as e:
        logger.warning("VixSrc error: %s", e)
        return None


# ═══════════════════════════════════════════════════════════
#   Stream routing: try each server in order
# ═══════════════════════════════════════════════════════════


async def _get_stream_for_server(server: dict, tmdb_id: str, imdb_id: str, season: Optional[int], episode: Optional[int]) -> Optional[dict]:
    """گرفتن stream info از یک سرور خاص."""
    sid = server["id"]
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
    if sid == "s10":  # CastleTV — 480p بومی
        return await _castletv_get_stream(tmdb_id, imdb_id, season, episode)
    if sid == "s11":  # VaPlayer
        return await _vaplayer_get_stream(imdb_id, season, episode)
    if sid == "s12":  # VixSrc
        return await _vixsrc_get_stream(tmdb_id, season, episode)
    return None


async def _get_first_working_stream(tmdb_id: str, imdb_id: str, season: Optional[int], episode: Optional[int]) -> Optional[dict]:
    """
    امتحان همه سرورها به ترتیب و برگرداندن اولین نتیجه موفق.
    سرورها به ترتیب اولویت در _SERVERS تعریف شدن.
    """
    for server in _SERVERS:
        try:
            logger.info("[IMDBPlay] Trying server %s (%s)...", server["id"], server["name"])
            stream = await _get_stream_for_server(server, tmdb_id, imdb_id, season, episode)
            if stream and stream.get("url"):
                logger.info("[IMDBPlay] ✓ Server %s succeeded", server["name"])
                return stream
            else:
                logger.info("[IMDBPlay] ✗ Server %s returned no stream", server["name"])
        except Exception as e:
            logger.warning("[IMDBPlay] ✗ Server %s exception: %s", server["name"], e)
            continue
    logger.error("[IMDBPlay] All servers failed for tmdb=%s imdb=%s", tmdb_id, imdb_id)
    return None


# متدهای استخراج برای نمایش به کاربر
_EXTRACTION_METHODS = {
    "s2": "WASM Decrypt (Vidzee API)",
    "s1": "XOR Cipher + Seed (speedracelight API)",
    "s3": "XOR Cipher + Seed (speedracelight API)",
    "s9": "Custom Base64 (vidnest API)",
    "s7": "WASM Decrypt (vidsrcme API)",
    "s10": "AES-128-CBC (CastleTV app API — 480/720/1080)",
    "s11": "Direct API (VaPlayer streamdata)",
    "s12": "Token Embed (VixSrc master m3u8)",
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
#   Quality matching helpers (انتخاب کیفیت واقعی)
# ═══════════════════════════════════════════════════════════

# label → height مورد انتظار
_QUALITY_HEIGHTS = {
    "2160p": 2160, "4k": 2160, "uhd": 2160,
    "1080p fullhd": 1080, "1080p x265": 1080, "1080p": 1080,
    "720p x265": 720, "720p": 720,
    "480p": 480,
    "360p": 360,
    "240p": 240,
}


class QualityNotAvailable(RuntimeError):
    """هیچ variantی با height ≤ کیفیت درخواستی روی این سرور نبود (فقط بالاتر).

    این خطا باعث میشه caller بره سراغ سرور بعدی که شاید کیفیت پایین‌تر
    واقعی داشته باشه — به‌جای اینکه بی‌سروصدا 1080p دانلود کنه.
    """

    def __init__(self, message: str, best_height: int = 0):
        super().__init__(message)
        self.best_height = best_height


def _height_of(resolution: str, bandwidth: int) -> int:
    """حدس زدن height از resolution یا bandwidth (مثل 1920x1080 → 1080)."""
    if resolution and "x" in resolution:
        try:
            return int(resolution.split("x")[-1])
        except (ValueError, IndexError):
            pass
    if bandwidth >= 8_000_000:
        return 1080
    if bandwidth >= 4_000_000:
        return 720
    if bandwidth >= 2_000_000:
        return 480
    if bandwidth >= 1_000_000:
        return 360
    return 0


def _rank_variants_for_height(variants: List[Tuple[str, int, str]], target_height: int):
    """بهترین variant برای height هدف رو پیدا می‌کنه.

    Returns:
        (kind, delta, rel_url) که kind یکی از:
        - "exact": تطابق دقیق height (delta=0)
        - "below": نزدیک‌ترین پایین‌تر از هدف (delta = target - h)
        - "above": نزدیک‌ترین بالاتر از هدف (delta = h - target)
        None اگه variant نبود یا target نامعتبره.
    """
    if not variants or target_height <= 0:
        return None
    entries = [(v, _height_of(v[2], v[1])) for v in variants]
    exact = [(v, h) for v, h in entries if h == target_height]
    if exact:
        return ("exact", 0, exact[0][0][0])
    below = [(v, h) for v, h in entries if 0 < h < target_height]
    if below:
        v, h = max(below, key=lambda x: x[1])
        return ("below", target_height - h, v[0])
    above = [(v, h) for v, h in entries if h > target_height]
    if above:
        v, h = min(above, key=lambda x: x[1])
        return ("above", h - target_height, v[0])
    return None


async def _probe_variants(stream: dict) -> List[Tuple[str, int, str]]:
    """fetch کردن m3u8 استریم؛ اگه master بود variantها برمی‌گردونه، وگرنه لیست خالی.

    برای رتبه‌بندی سرورها بر اساس کیفیت واقعی موجود استفاده میشه.
    """
    url = stream.get("url", "")
    if not url or stream.get("type", "hls") != "hls":
        return []
    headers = {"User-Agent": _USER_AGENT}
    headers.update(stream.get("headers", {}))
    try:
        async with AsyncSession() as s:
            r = await s.get(url, impersonate=_BROWSER_IMPERSONATE, timeout=15, headers=headers)
        if r.status_code != 200:
            return []
        text = r.text
        if "#EXT-X-STREAM-INF:" not in text:
            return []
        return _parse_master_m3u8(text)
    except Exception as e:
        logger.debug("probe variants failed for %s: %s", url[:80], e)
        return []


# ═══════════════════════════════════════════════════════════
#   Public API: get_server_qualities  (پروب سروربه‌سرور + کش)
# ═══════════════════════════════════════════════════════════


_SERVER_PROBE_CACHE: Dict[tuple, tuple] = {}   # (imdb, season, ep) → (expires, result)
_SERVER_PROBE_TTL = 600                        # ۱۰ دقیقه — منوی کیفیت و منوی سرور روی همین کش کار می‌کنن


async def get_server_qualities(
    imdb_id: str,
    season: Optional[int] = None,
    episode: Optional[int] = None,
) -> List[dict]:
    """
    پروب «سروربه‌سرور»: برای هر سرورِ موجود، کیفیت‌های واقعی اون فیلم/قسمت
    جمع می‌شه (هم لیست خود سرور، هم probe کردن master.m3u8) تا UI بتونه
    منوی «انتخاب سرور» بسازه — کاربر ببینه کدوم سرور دقیقاً 480p داره.

    خروجی (به ترتیب اولویت _SERVERS) لیستی از:
      {
        "server": "CastleTV",
        "stream_type": "hls" | "mp4",
        "auto_url": آدرس استریم پایه (برای MP4 هم همون URL مستقیمه),
        "qualities": {label_lower: {"label","url","resolution","bandwidth"}},
        "quality_list": [همون values، مرتب‌شده نزولی],
      }

    نتیجه ۱۰ دقیقه کش می‌شه (کلید: imdb+season+episode) که منوی کیفیت و
    بعدش منوی انتخاب سرور دوبار پروب سنگین نزنن. سروری که جواب نداده هم
    با لیست خالی ثبت می‌شه تا UI بتونه نشون بده «فعلاً در دسترس نیست».
    """
    if not imdb_id:
        return []
    if not imdb_id.startswith("tt"):
        imdb_id = f"tt{imdb_id}"

    key = (imdb_id, season or 0, episode or 0)
    now = time.time()
    cached = _SERVER_PROBE_CACHE.get(key)
    if cached and cached[0] > now:
        return cached[1]

    tmdb_id = await _get_tmdb_id(imdb_id)
    if not tmdb_id:
        logger.error("Cannot resolve tmdb_id for %s", imdb_id)
        return []

    result: List[dict] = []
    for server in _SERVERS:
        entry = {
            "server": server["name"],
            "stream_type": "hls",
            "auto_url": "",
            "qualities": {},
            "quality_list": [],
        }
        try:
            stream = await _get_stream_for_server(server, tmdb_id, imdb_id, season, episode)
        except Exception as e:
            logger.warning("[IMDBPlay] get_server_qualities: %s exception: %s", server["name"], e)
            stream = None
        if not stream or not stream.get("url"):
            result.append(entry)      # سرور جواب نداد — با لیست خالی ثبت می‌شه
            continue

        entry["server"] = stream.get("server", server["name"])
        entry["auto_url"] = stream["url"]

        # ۱) لیست کیفیت خود سرور (مثل Videasy/Vidking) — لیبل Auto جمع نمی‌شه
        for q in stream.get("qualities") or []:
            label = str(q.get("label", q.get("quality", ""))).strip()
            if not label or label.lower() == "auto":
                continue
            if q.get("url") and label.lower() not in entry["qualities"]:
                entry["qualities"][label.lower()] = {
                    "label": label, "url": q["url"],
                    "resolution": "", "bandwidth": 0,
                }

        # ۲) سرورهای MP4 انتخاب کیفیت ندارن
        if stream.get("type", "hls") != "hls":
            entry["stream_type"] = "mp4"
            result.append(entry)
            continue

        # ۳) probe کردن master.m3u8 برای variantهای واقعی
        base_url = stream["url"]
        for vurl, bw, res in await _probe_variants(stream):
            label = _resolution_to_label(res, bw)
            if label.lower() in entry["qualities"]:
                continue
            entry["qualities"][label.lower()] = {
                "label": label, "url": _make_absolute(base_url, vurl),
                "resolution": res, "bandwidth": bw,
            }
        result.append(entry)

    # مرتب‌سازی کیفیت‌های هر سرور نزولی (بر اساس height واقعی)
    for entry in result:
        entry["quality_list"] = sorted(
            entry["qualities"].values(),
            key=lambda q: -_height_of(q["resolution"], q["bandwidth"]),
        )

    _SERVER_PROBE_CACHE[key] = (now + _SERVER_PROBE_TTL, result)
    if len(_SERVER_PROBE_CACHE) > 64:                       # جلوگیری از رشد بی‌نهایت
        try:
            oldest = min(_SERVER_PROBE_CACHE, key=lambda k: _SERVER_PROBE_CACHE[k][0])
            _SERVER_PROBE_CACHE.pop(oldest, None)
        except Exception:
            pass
    logger.info("get_server_qualities %s S%sE%s -> %d server(s) probed",
                imdb_id, season, episode, len(result))
    return result


def clear_server_qualities_cache(imdb_id: str = "") -> None:
    """پاک کردن کش پروب سرورها (برای تست یا refresh دستی)."""
    if not imdb_id:
        _SERVER_PROBE_CACHE.clear()
        return
    norm = imdb_id if imdb_id.startswith("tt") else f"tt{imdb_id}"
    for k in [k for k in _SERVER_PROBE_CACHE if k[0] == norm]:
        _SERVER_PROBE_CACHE.pop(k, None)


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

    برخلاف نسخه قبلی که فقط از «اولین» سروری که جواب می‌داد لیست می‌گرفت,
    اینجا از «همه» سرورها کیفیت‌های واقعی جمع میشه (با probe کردن master.m3u8)
    تا کاربر گزینه‌های واقعی ببینه — مثلاً 480p/360p که فقط روی یکی از سرورها هست.

    پیاده‌سازی: روی get_server_qualities سوار شده (همون پروب، کش مشترک) —
    بعدش فقط تجمیع سروربه‌سرور به لیست کلی با سمانتیک قبلی:
    اولین سرورِ دارای هر لیبل اولویت داره + Auto = اولین استریم موفق.
    """
    if not imdb_id:
        return []
    if not imdb_id.startswith("tt"):
        imdb_id = f"tt{imdb_id}"

    per_server = await get_server_qualities(imdb_id, season, episode)

    collected = {}          # label.lower() → Quality dict (اولین سرور اولویت داره)
    auto_url = None
    auto_server = ""

    for entry in per_server:
        # اولین استریم موفق = مبنای Auto
        if entry.get("auto_url") and not auto_url:
            auto_url = entry["auto_url"]
            auto_server = entry["server"]

        for label_lower, q in (entry.get("qualities") or {}).items():
            if label_lower in collected:
                continue
            collected[label_lower] = Quality(
                label=q["label"], bandwidth=q.get("bandwidth", 0),
                resolution=q.get("resolution", ""), url=q["url"],
                server=entry["server"], is_auto=False,
            ).to_dict()

    if not auto_url:
        logger.error("No working stream found for %s", imdb_id)
        return []

    # مرتب‌سازی نزولی بر اساس height واقعی
    qualities = list(collected.values())
    qualities.sort(key=lambda q: -_height_of(q["resolution"], q["bandwidth"]))

    # Auto (بهترین استریم موفق) همیشه اول
    qualities.insert(0, Quality(
        label="Auto", bandwidth=0, resolution="", url=auto_url,
        server=auto_server, is_auto=True,
    ).to_dict())

    logger.info("get_qualities %s -> %d quality label(s) aggregated from servers",
                imdb_id, len(qualities))
    return qualities


# ═══════════════════════════════════════════════════════════
#   Public API: download_with_quality
# ═══════════════════════════════════════════════════════════


async def download_with_quality(
    imdb_id: str,
    quality_label: str,
    out_dir: str,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    preferred_server: Optional[str] = None,
) -> Optional[str]:
    """
    دانلود فیلم یا قسمت سریال با کیفیت انتخابی.

    Args:
        imdb_id: e.g. "tt33071426"
        quality_label: e.g. "Auto", "1080p", "720p"
        out_dir: مسیر خروجی
        season, episode: برای سریال
        progress_cb: callback(done, total)
        preferred_server: نام سرور انتخابی کاربر (مثل "CastleTV") — این سرور
            اول امتحان می‌شه (با همون سمانتیک strict کیفیت) و اگه شکست خورد
            زنجیره‌ی عادی فالباک ادامه پیدا می‌کنه تا فایل به‌هر حال برسه.

    رفتار برای کیفیت خاص (مثلاً 480p):
      1. همه سرورها probe میشن؛ سروری که دقیقاً 480p داره اولویت داره.
      2. اگه هیچ‌کس 480p نداشت، نزدیک‌ترین پایین‌تر (مثلاً 360p) دانلود میشه
         — هرگز مثل قبل بی‌سروصدا به 1080p آپگرید نمیشه.
      3. vidsrcme (که اغلب کیفیت‌های پایین‌تر واقعی داره) قبل از آپگرید امتحان میشه.
      4. آپگرید به کیفیت بالاتر فقط وقتی انجام میشه که هیچ گزینه ≤ هدف
         روی هیچ سروری پیدا نشده باشه.

    Returns:
        مسیر فایل دانلود شده، یا None در صورت خطا.
    """
    if not imdb_id:
        return None
    if not imdb_id.startswith("tt"):
        imdb_id = f"tt{imdb_id}"

    os.makedirs(out_dir, exist_ok=True)

    tmdb_id = await _get_tmdb_id(imdb_id)
    if not tmdb_id:
        raise RuntimeError(f"Cannot resolve tmdb_id for {imdb_id}")

    # اگه کیفیت خاصی درخواست شده، بین همه سرورها دنبال «واقعی‌ترین» تطابق بگرد
    # اگه "Auto" درخواست شده، اولین سرور موفق کافیه
    target_quality = quality_label.lower() if quality_label else "auto"

    if target_quality == "auto":
        # ─── مسیر Auto: اول سرور انتخابی کاربر، بعد اولین سرور موفق ───
        stream = None
        if preferred_server:
            _pref = preferred_server.strip().lower()
            for server in _SERVERS:
                if server["name"].strip().lower() != _pref:
                    continue
                try:
                    cand = await _get_stream_for_server(server, tmdb_id, imdb_id, season, episode)
                    if cand and cand.get("url"):
                        stream = cand
                        logger.info("[IMDBPlay] Auto: using preferred server %s", preferred_server)
                        break
                except Exception as e:
                    logger.warning("[IMDBPlay] Auto: preferred %s failed: %s", preferred_server, e)
            if not stream:
                logger.info("[IMDBPlay] Auto: preferred %s unavailable → normal order", preferred_server)
        if not stream:
            stream = await _get_first_working_stream(tmdb_id, imdb_id, season, episode)
        if not stream:
            raise RuntimeError(f"No working stream found for {imdb_id}")

        # اگه stream از نوع MP4 باشه (مثل 2Embed/vidlink)، دانلود مستقیم
        if stream.get("type", "hls") == "mp4":
            try:
                return await _download_mp4_stream(stream, out_dir, progress_cb)
            except Exception as e:
                logger.warning("MP4 download from %s failed: %s — falling back to HLS servers",
                               stream.get("server", "?"), e)
                _hls_replacement = None
                # سرورها رو امتحان کن، ولی فقط HLS ها رو
                for server in _SERVERS:
                    if server["name"] == stream.get("server"):
                        continue  # همین سرور رو رد کن
                    try:
                        fallback_stream = await _get_stream_for_server(
                            server, tmdb_id, imdb_id, season, episode)
                        if fallback_stream and fallback_stream.get("url") \
                                and fallback_stream.get("type", "hls") == "hls":
                            logger.info("[IMDBPlay] ✓ Fallback to %s (HLS)", server["name"])
                            _hls_replacement = fallback_stream
                            break
                    except Exception as e2:
                        logger.warning("[IMDBPlay] Fallback server %s failed: %s", server["name"], e2)
                        continue
                if not _hls_replacement:
                    raise RuntimeError("MP4 download failed and no HLS fallback available")
                stream = _hls_replacement

        return await _hls_multiround(stream, quality_label, out_dir, progress_cb,
                                     tmdb_id, imdb_id, season, episode)

    # ─── مسیر کیفیت خاص: رتبه‌بندی همه سرورها بر اساس نزدیکی واقعی به کیفیت ───
    #
    # باگ قبلی: اولین سروری که «لیبل دقیق» داشت انتخاب می‌شد و اگه هیچ سروری
    # لیبل رو نداشت (مثلاً کاربر 480p خواست ولی سرورها 1080/720/360 دارن)،
    # بی‌سروصدا به Auto (بهترین کیفیت) fallback می‌شد و 1080p دانلود می‌شد!
    #
    # الان همه سرورها probe میشن و بر اساس نزدیکی واقعی رتبه می‌گیرن:
    #   rank 0 = دقیقاً همون height  → دانلود
    #   rank 1 = نزدیک‌ترین پایین‌تر → دانلود (strict — هیچ‌وقت بالاتر نمی‌گیره)
    #   rank 2 = نزدیک‌ترین بالاتر   → فقط آخرین راه (آپگرید)
    #   rank 3 = نامشخص (MP4 یا probe نشد)
    target_height = _QUALITY_HEIGHTS.get(target_quality, 0)
    ranked: List[tuple] = []   # (rank, delta, order, server, stream)

    for order, server in enumerate(_SERVERS):
        try:
            cand = await _get_stream_for_server(server, tmdb_id, imdb_id, season, episode)
        except Exception as e:
            logger.warning("[IMDBPlay] ✗ Server %s exception: %s", server["name"], e)
            continue
        if not cand or not cand.get("url"):
            continue

        if cand.get("type", "hls") == "mp4":
            # MP4 انتخاب کیفیت نداره — فقط به‌عنوان آخرین راه
            ranked.append((3, 9999, order, server, cand))
            continue

        rank, delta = 3, 9999
        matched = None
        # ۱) لیست کیفیت خود سرور (مثل Videasy/Vidking)
        if cand.get("qualities"):
            for q in cand["qualities"]:
                if str(q.get("label", "")).strip().lower() == target_quality and q.get("url"):
                    cand = dict(cand)
                    cand["url"] = q["url"]
                    matched = ("exact", 0)
                    break
        # ۲) probe کردن master.m3u8 برای variantهای واقعی
        if matched is None:
            variants = await _probe_variants(cand)
            res = _rank_variants_for_height(variants, target_height)
            if res:
                kind, d, vurl = res
                matched = (kind, d)
                cand = dict(cand)
                cand["url"] = _make_absolute(cand["url"], vurl)

        if matched:
            rank = {"exact": 0, "below": 1, "above": 2}[matched[0]]
            delta = matched[1]
        logger.info("[IMDBPlay] Server %s → rank %d (delta=%s) for %s",
                    server["name"], rank, delta, quality_label)
        ranked.append((rank, delta, order, server, cand))

    ranked.sort(key=lambda x: (x[0], x[1], x[2]))
    # 🖰 سرور انتخابی کاربر به صدر می‌ره (سمانتیک strict کیفیت دست نمی‌خوره؛
    # فقط ترتیب امتحان عوض می‌شه و بقیه سرورها فالباک خودکار می‌مونن)
    if preferred_server:
        _pref = preferred_server.strip().lower()
        ranked.sort(key=lambda r: 0 if str(r[3]["name"]).strip().lower() == _pref else 1)
        logger.info("[IMDBPlay] preferred server %s moved to front of %d candidate(s)",
                    preferred_server, len(ranked))

    async def _try_candidate(cand: dict, allow_upgrade: bool) -> str:
        return await _download_hls_stream(
            cand, quality_label, out_dir, progress_cb,
            tmdb_id, imdb_id, season, episode,
            allow_upgrade=allow_upgrade,
        )

    # ─── فاز ۱: تطابق دقیق و نزدیک‌ترین پایین‌تر (سخت‌گیرانه) ───
    strict_cands = [r for r in ranked if r[0] <= 1]
    for rank, delta, order, server, cand in strict_cands:
        try:
            logger.info("[IMDBPlay] Phase 1: %s (rank %d) for %s",
                        cand.get("server", "?"), rank, quality_label)
            return await _try_candidate(cand, allow_upgrade=False)
        except QualityNotAvailable as e:
            logger.info("[IMDBPlay] %s: no variant ≤ %s (%s)",
                        cand.get("server", "?"), quality_label, e)
            continue
        except Exception as e:
            logger.warning("[IMDBPlay] Phase 1: %s failed: %s", cand.get("server", "?"), e)
            continue

    # ─── فاز ۲: تلاش strict دوباره با stream تازه (خطای گذرا / منقضی شدن seed) ───
    if strict_cands:
        await asyncio.sleep(2)
        for rank, delta, order, server, cand in strict_cands:
            try:
                fresh = await _get_stream_for_server(server, tmdb_id, imdb_id, season, episode)
                if fresh and fresh.get("url"):
                    cand = fresh
            except Exception:
                pass
            try:
                return await _try_candidate(cand, allow_upgrade=False)
            except QualityNotAvailable:
                continue
            except Exception as e:
                logger.warning("[IMDBPlay] Phase 2: %s failed: %s", cand.get("server", "?"), e)
                continue

    # ─── فاز ۳: vidsrcme — معمولاً کیفیت‌های پایین‌تر واقعی داره (360/480/720/1080) ───
    try:
        from vidsrc_downloader import download_episode as _vde, download_movie as _vdm
        logger.info("[IMDBPlay] Phase 3: vidsrcme for exact %s", quality_label)
        if season and episode:
            _p = await _vde(imdb_id, season, episode, out_dir=out_dir,
                            quality=quality_label, progress_cb=progress_cb)
        else:
            _p = await _vdm(imdb_id, out_dir=out_dir,
                            quality=quality_label, progress_cb=progress_cb)
        if _p:
            return _p
    except Exception as e:
        logger.warning("[IMDBPlay] Phase 3: vidsrcme failed: %s", e)

    # ─── فاز ۴: آپگرید — نزدیک‌ترین بالاتر (فقط وقتی هیچ گزینه ≤ هدف نبود یا همه فیل شدن) ───
    for rank, delta, order, server, cand in ranked:
        if cand.get("type", "hls") == "mp4":
            try:
                logger.info("[IMDBPlay] Phase 4: MP4 from %s (آخرین راه — کیفیت دقیق پیدا نشد)",
                            cand.get("server", "?"))
                return await _download_mp4_stream(cand, out_dir, progress_cb)
            except Exception as e:
                logger.warning("[IMDBPlay] Phase 4: MP4 %s failed: %s", cand.get("server", "?"), e)
                continue
        try:
            logger.info("[IMDBPlay] Phase 4: upgrade attempt on %s (rank %d)",
                        cand.get("server", "?"), rank)
            return await _try_candidate(cand, allow_upgrade=True)
        except QualityNotAvailable:
            continue
        except Exception as e:
            logger.warning("[IMDBPlay] Phase 4: %s failed: %s", cand.get("server", "?"), e)
            continue

    # ─── فاز ۵: آخرین راه — Auto مثل قبل ───
    logger.warning("[IMDBPlay] All quality-specific phases failed for %s — falling back to Auto",
                   quality_label)
    stream = await _get_first_working_stream(tmdb_id, imdb_id, season, episode)
    if not stream:
        raise RuntimeError(f"No working stream found for {imdb_id} (quality {quality_label})")
    quality_label = "Auto"
    if stream.get("type", "hls") == "mp4":
        try:
            return await _download_mp4_stream(stream, out_dir, progress_cb)
        except Exception:
            pass
    return await _hls_multiround(stream, quality_label, out_dir, progress_cb,
                                 tmdb_id, imdb_id, season, episode)


async def _download_mp4_stream(
    stream: dict,
    out_dir: str,
    progress_cb: Optional[Callable[[int, int], None]],
) -> str:
    """دانلود مستقیم MP4 از یک استریم. در صورت خطا raise می‌کنه تا caller تصمیم بگیره."""
    m3u8_url = stream["url"]
    headers = {"User-Agent": _USER_AGENT}
    headers.update(stream.get("headers", {}))
    logger.info("Downloading MP4 directly from %s", stream.get("server", ""))
    out_path = os.path.join(out_dir, f"{int(time.time())}.mp4")
    async with AsyncSession() as s:
        # برای MP4، دانلود با chunked
        r = await s.get(m3u8_url, impersonate=_BROWSER_IMPERSONATE, timeout=600,
                        headers=headers, stream=True)
        if r.status_code != 200:
            raise RuntimeError(f"MP4 fetch HTTP {r.status_code}")
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


async def _hls_multiround(
    stream: dict,
    quality_label: str,
    out_dir: str,
    progress_cb: Optional[Callable[[int, int], None]],
    tmdb_id: str,
    imdb_id: str,
    season: Optional[int],
    episode: Optional[int],
) -> str:
    """دانلود HLS با fallback بین همه سرورها (۲ دور تلاش) — آخرین لایه محافظت.

    استریم انتخاب‌شده اول امتحان میشه؛ اگه fail شد (مثلاً variant HTTP 502
    یا CDN خراب)، سرورهای بعدی به ترتیب اولویت امتحان میشن.
    دور دوم با stream/seed تازه انجام میشه چون بعضی خطاها (502 گذرا،
    منقضی شدن seed) بعد از چند ثانیه خودشون درست میشن.
    """
    _last_err = None
    for _round in range(2):
        if _round > 0:
            await asyncio.sleep(3)
            logger.info("[IMDBPlay] HLS retry round %d: fetching fresh streams...", _round + 1)
        candidate_streams = []
        _tried = set()
        if _round == 0:
            candidate_streams.append(stream)
            _tried.add(stream.get("server", ""))
        for server in _SERVERS:
            if server["name"] in _tried:
                continue
            _tried.add(server["name"])
            try:
                alt = await _get_stream_for_server(server, tmdb_id, imdb_id, season, episode)
            except Exception as e:
                logger.warning("[IMDBPlay] fallback server %s exception: %s", server["name"], e)
                continue
            if alt and alt.get("url") and alt.get("type", "hls") == "hls":
                candidate_streams.append(alt)
        logger.info("[IMDBPlay] round %d: %d candidate HLS stream(s)", _round + 1, len(candidate_streams))

        for _cand in candidate_streams:
            try:
                return await _download_hls_stream(
                    _cand, quality_label, out_dir, progress_cb,
                    tmdb_id, imdb_id, season, episode,
                )
            except Exception as e:
                _last_err = e
                logger.warning("[IMDBPlay] HLS download from %s failed: %s — trying next server",
                               _cand.get("server", "?"), e)
    raise RuntimeError(f"All HLS servers failed; last error: {_last_err}")


async def _download_hls_stream(
    stream: dict,
    quality_label: str,
    out_dir: str,
    progress_cb: Optional[Callable[[int, int], None]],
    tmdb_id: str,
    imdb_id: str,
    season: Optional[int],
    episode: Optional[int],
    allow_upgrade: bool = False,
) -> str:
    """دانلود HLS از یک استریم مشخص + fallback بین variantها.

    - اگه master.m3u8 باشه، variantها به ترتیب اولویت (مطابق quality_label)
      امتحان میشن تا اولین variant سالم پیدا بشه.
    - برای خطاهای موقت CDN (مثل 502) هر variant دو بار تلاش میشه.
    - اگه هیچ variant سالم نبود، RuntimeError بالا میده تا caller
      سرور بعدی رو امتحان کنه.
    - allow_upgrade=False (حالت strict): هرگز variant بالاتر از کیفیت
      درخواستی انتخاب نمیشه — به‌جاش QualityNotAvailable برمی‌گرده تا
      caller سرور دیگه‌ای که کیفیت پایین‌تر واقعی داره رو امتحان کنه.
      این جلوی «کاربر 480p خواست، 1080p گرفت» رو می‌گیره.
    """
    m3u8_url = stream["url"]
    headers = {"User-Agent": _USER_AGENT}
    headers.update(stream.get("headers", {}))

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
                    # Stream جدید با seed جدید — اول از «همین سرور» (برای حفظ کیفیت انتخابی)
                    same_server = next(
                        (sv for sv in _SERVERS if sv["name"] == stream.get("server")), None)
                    new_stream = None
                    if same_server:
                        try:
                            new_stream = await _get_stream_for_server(
                                same_server, tmdb_id, imdb_id, season, episode)
                        except Exception:
                            new_stream = None
                    if not (new_stream and new_stream.get("url")):
                        new_stream = await _get_first_working_stream(
                            tmdb_id, imdb_id, season, episode)
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
        target_height = _QUALITY_HEIGHTS.get(quality_label.lower() if quality_label else "", 0)

        # Sort variants by height descending (best first)
        variants_with_h = [(v, _height_of(v[2], v[1])) for v in variants]
        variants_with_h.sort(key=lambda x: -x[1])

        # ─── ساخت لیست اولویت‌دار variantها ───
        # اولین variant سالم استفاده میشه؛ بقیه به عنوان fallback.
        # با allow_upgrade=False (حالت strict) هرگز variant بالاتر از هدف
        # انتخاب نمیشه — این جلوی «کاربر 480p خواست، 1080p گرفت» رو می‌گیره.
        chosen_list: List[tuple] = []
        if quality_label and quality_label.lower() != "auto" and target_height > 0:
            # 1) تطابق دقیق label
            for v, h in variants_with_h:
                label = _resolution_to_label(v[2], v[1])
                if label.lower() == quality_label.lower():
                    chosen_list.append(v)
                    logger.info("✓ Quality match (exact): %s → height=%d", label, h)
                    break
            # 2) نزدیک‌ترین height ≤ target (نزولی — نزدیک‌ترین اول)
            seen = {v[0] for v in chosen_list}
            for v, h in variants_with_h:
                if v[0] in seen:
                    continue
                if 0 < h <= target_height:
                    chosen_list.append(v)
                    seen.add(v[0])
            # 3) آپگرید فقط وقتی allow_upgrade=True — نزدیک‌ترین بالاتر اول (صعودی)
            if allow_upgrade:
                above_asc = [x for x in variants_with_h
                             if x[0] not in seen and x[1] > target_height]
                above_asc.sort(key=lambda x: x[1])
                for v, h in above_asc:
                    chosen_list.append(v)
                    seen.add(v[0])
                # هر چیز باقی‌مونده (مثلاً height نامشخص) به عنوان آخرین fallback
                for v, h in variants_with_h:
                    if v[0] not in seen:
                        chosen_list.append(v)
                        seen.add(v[0])
            elif not chosen_list:
                # حالت strict و هیچ variant ≤ هدف نیست — بالاتر نگیر، برو سرور بعدی
                heights = sorted({h for _, h in variants_with_h}, reverse=True)
                raise QualityNotAvailable(
                    f"only heights {heights} available, all > target "
                    f"{target_height} ({quality_label})",
                    best_height=heights[0] if heights else 0,
                )
        else:
            # Auto: بهترین کیفیت اول، بعد بقیه
            chosen_list = [v for v, _ in variants_with_h]

        if not chosen_list:
            raise RuntimeError("No candidate variants in master.m3u8")

        # ─── fetch اولین variant سالم (با fallback روی بقیه variantها) ───
        # خطای 502/5xx معمولاً گذراست؛ هر variant دو بار تلاش میشه و
        # اگه نشد، variant بعدی امتحان میشه.
        text = None
        variant_url = None
        last_v_err = None
        chosen_v = None
        for vi, v in enumerate(chosen_list):
            v_url = _make_absolute(m3u8_url, v[0])
            for attempt in range(2):
                try:
                    async with AsyncSession() as s:
                        r = await s.get(v_url, impersonate=_BROWSER_IMPERSONATE, timeout=20, headers=headers)
                    if r.status_code == 200:
                        text = r.text
                        variant_url = v_url
                        chosen_v = v
                        break
                    last_v_err = RuntimeError(f"variant m3u8 HTTP {r.status_code}")
                    logger.warning("variant %d/%d HTTP %d (attempt %d): %s",
                                   vi + 1, len(chosen_list), r.status_code, attempt + 1, v_url[:80])
                    if r.status_code < 500:
                        break  # 4xx — retry همین variant بی‌فایده، برو سراغ بعدی
                except Exception as e:
                    last_v_err = e
                    logger.warning("variant %d/%d fetch error (attempt %d): %s",
                                   vi + 1, len(chosen_list), attempt + 1, e)
                await asyncio.sleep(1)
            if text:
                break
            logger.warning("variant %d/%d failed — trying next variant", vi + 1, len(chosen_list))

        if not text or not variant_url or chosen_v is None:
            raise RuntimeError(
                f"variant m3u8 fetch failed: all {len(chosen_list)} variant(s) tried; "
                f"last error: {last_v_err}"
            )
        logger.info("Selected variant: %s (bandwidth=%d, resolution=%s)",
                    variant_url[:80], chosen_v[1], chosen_v[2])

    segments, init_url = _parse_variant_m3u8(text)
    if not segments:
        raise RuntimeError("No segments in variant m3u8")

    total = len(segments)
    server_name = stream.get("server", "unknown")
    logger.info("Downloading %d segments from %s (init=%s)",
                total, server_name, "yes" if init_url else "no")

    # download segments in parallel — با session مشترک و retries بیشتر
    seg_paths = [None] * total
    init_path = None
    sem = asyncio.Semaphore(SEGMENT_CONCURRENCY)  # دانلود همزمان سگمنت‌ها (قابل تنظیم با IMDB_SEG_CONCURRENCY)

    # abort زودهنگام: اگه تعداد زیادی سگمنت پشت‌سرهم fail شد (مثلاً پروکسی
    # 403 می‌ده یا origin down هست)، بقیه رو بی‌خود امتحان نکن — فاز بعدی
    # (سرور بعدی / vidsrcme) رو سریع‌تر شروع کن.
    _fail_count = 0
    _max_fail = max(8, min(30, total // 10))
    _abort = False

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

        async def download_one(idx: int, seg_url: str):
            nonlocal seg_paths, _fail_count, _abort
            if _abort:
                return
            abs_url = _make_absolute(variant_url, seg_url)
            async with sem:
                if _abort:
                    return
                last_err = None
                for attempt in range(5):
                    try:
                        r = await shared_session.get(
                            abs_url, impersonate=_BROWSER_IMPERSONATE,
                            timeout=60, headers=headers,
                        )
                        if r.status_code == 200 and r.content:
                            data = r.content
                            # تشخیص فرمت از روی URL یا محتوا
                            if init_path:
                                # fMP4 — پسوند .m4s
                                seg_path = os.path.join(out_dir, f"seg_{idx:05d}.m4s")
                            else:
                                # MPEG-TS — پسوند .ts
                                seg_path = os.path.join(out_dir, f"seg_{idx:05d}.ts")
                            with open(seg_path, "wb") as f:
                                f.write(data)
                            seg_paths[idx] = seg_path
                            if progress_cb:
                                done = sum(1 for p in seg_paths if p)
                                try:
                                    progress_cb(done, total)
                                except Exception:
                                    pass
                            return
                        elif r.status_code in (403, 410):
                            # خطای دائمی (مثلاً بلاک شدن پروکسی) — retry بی‌فایده
                            last_err = RuntimeError(f"HTTP {r.status_code}")
                            break
                        elif r.status_code in (429, 503):
                            await asyncio.sleep(0.5 * (attempt + 1))
                        else:
                            await asyncio.sleep(0.5 * (attempt + 1))
                    except Exception as e:
                        last_err = e
                        logger.debug("seg %d attempt %d failed: %s", idx, attempt, e)
                        await asyncio.sleep(1 * (attempt + 1))
                logger.error("seg %d failed after 5 attempts: %s", idx, last_err)
                _fail_count += 1
                if _fail_count >= _max_fail and not _abort:
                    _abort = True
                    logger.error("Too many failed segments (%d/%d) — aborting remaining segment downloads early",
                                 _fail_count, total)

        await asyncio.gather(*[download_one(i, u) for i, (u, _) in enumerate(segments)])

    missing = [i for i, p in enumerate(seg_paths) if not p]
    if missing:
        logger.error("Missing %d segments: %s", len(missing), missing[:5])

    valid_paths = [p for p in seg_paths if p]
    if not valid_paths:
        raise RuntimeError("All segments failed to download")

    out_path = os.path.join(out_dir, f"{int(time.time())}.mp4")
    if not _concat_segments(valid_paths, out_path, init_path):
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


def _concat_segments(seg_paths: List[str], out_path: str, init_path: Optional[str] = None) -> bool:
    """
    concat سگمنت‌ها با ffmpeg.

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
) -> Optional[str]:
    """
    گرفتن زیرنویس فارسی از چندین منبع.

    ترتیب جستجو:
      1. core.vidzee.wtf/subs (سرور Vidzee) — بهترین منبع
      2. sub.vdrk.site (سرور 2Embed)
      3. subs.videasy.to (سرورهای Videasy/Vidking) — عموماً انگلیسی

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

    logger.info("No Persian subtitle found for %s", imdb_id)
    return None


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


def embed_subtitle_soft(video_path: str, subtitle_path: str, out_path: str) -> Optional[str]:
    """
    قرار دادن زیرنویس به‌صورت softsub داخل فایل ویدیو (بدون re-encode).
    این کار خیلی سریع هست (فقط remux) و زیرنویس قابل روشن/خاموش شدن در VLC هست.

    Args:
        video_path: مسیر فایل ویدیو
        subtitle_path: مسیر فایل زیرنویس (VTT یا SRT)
        out_path: مسیر فایل خروجی

    Returns:
        مسیر فایل خروجی اگه موفق، None در غیر این صورت.
    """
    try:
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
