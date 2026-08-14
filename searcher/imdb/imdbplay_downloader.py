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


async def download_with_quality(
    imdb_id: str,
    quality_label: str,
    out_dir: str,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> Optional[str]:
    """
    دانلود فیلم یا قسمت سریال با کیفیت انتخابی.

    Args:
        imdb_id: e.g. "tt33071426"
        quality_label: e.g. "Auto", "1080p", "720p"
        out_dir: مسیر خروجی
        season, episode: برای سریال
        progress_cb: callback(done, total)

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

    # اگه کیفیت خاصی درخواست شده، سروری رو پیدا کن که اون کیفیت رو داشته باشه
    # اگه "Auto" درخواست شده، اولین سرور موفق کافیه
    target_quality = quality_label.lower() if quality_label else "auto"

    stream = None
    if target_quality == "auto":
        # برای Auto، اولین سرور موفق کافیه
        stream = await _get_first_working_stream(tmdb_id, imdb_id, season, episode)
    else:
        # برای کیفیت خاص، سرورها رو به ترتیب امتحان کن تا سروری پیدا بشه که اون کیفیت رو داشته باشه
        for server in _SERVERS:
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

        # اگه هیچ سرور کیفیت مورد نظر رو نداشت، fallback به اولین سرور موفق
        if not stream:
            logger.warning("[IMDBPlay] No server has quality %s, falling back to Auto", quality_label)
            stream = await _get_first_working_stream(tmdb_id, imdb_id, season, episode)
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
        out_path = os.path.join(out_dir, f"{int(time.time())}.mp4")
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
                    # Stream جدید با seed جدید
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

        variants.sort(key=lambda v: -v[1])

        chosen = None
        if quality_label and quality_label.lower() != "auto":
            for url, bw, res in variants:
                label = _resolution_to_label(res, bw)
                if label.lower() == quality_label.lower():
                    chosen = (url, bw, res)
                    break
            if not chosen:
                chosen = variants[0]
        else:
            chosen = variants[0]

        variant_url = _make_absolute(m3u8_url, chosen[0])
        try:
            async with AsyncSession() as s:
                r = await s.get(variant_url, impersonate=_BROWSER_IMPERSONATE, timeout=20, headers=headers)
                if r.status_code != 200:
                    raise RuntimeError(f"variant m3u8 HTTP {r.status_code}")
                text = r.text
        except Exception as e:
            raise RuntimeError(f"variant m3u8 fetch failed: {e}")

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
    sem = asyncio.Semaphore(6)  # 6 concurrent downloads

    async with AsyncSession() as shared_session:
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
                        await asyncio.sleep(2 * (attempt + 1))
                    else:
                        await asyncio.sleep(0.5 * (attempt + 1))
                except Exception as e:
                    logger.warning("init download attempt %d failed: %s", attempt, e)
                    await asyncio.sleep(1 * (attempt + 1))

            # اگه init segment دانلود نشد ولی نیاز هست، خطا بده
            if not init_path and init_url:
                logger.error("Init segment failed to download after 5 attempts — concat will likely fail")

        async def download_one(idx: int, seg_url: str):
            nonlocal seg_paths
            abs_url = _make_absolute(variant_url, seg_url)
            async with sem:
                last_err = None
                for attempt in range(5):
                    try:
                        r = await shared_session.get(
                            abs_url, impersonate=_BROWSER_IMPERSONATE,
                            timeout=90, headers=headers,
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
                        elif r.status_code in (429, 503):
                            await asyncio.sleep(2 * (attempt + 1))
                        else:
                            await asyncio.sleep(0.5 * (attempt + 1))
                    except Exception as e:
                        last_err = e
                        logger.debug("seg %d attempt %d failed: %s", idx, attempt, e)
                        await asyncio.sleep(1 * (attempt + 1))
                logger.error("seg %d failed after 5 attempts: %s", idx, last_err)

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
