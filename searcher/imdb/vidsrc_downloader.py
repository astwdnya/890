"""
vidsrc_downloader.py
────────────────────
دانلود ویدیو از vidsrcme.ru بدون Playwright.

الگوریتم:
  1. GET https://data.vidsrcme.ru/api.php?type=movie|tv&imdb=ID[&season=S&episode=E]&stream_urls
     -> JSON با stream_urls (encrypted) و vs.w + vs.wasm_url
  2. GET vs.wasm_url -> فایل WASM (ChaCha20 decryptor)
  3. اجرای WASM با wasmtime -> decrypt stream_urls -> لیست m3u8 URLs
  4. برای هر m3u8 URL:
     a. GET origin/generate.php -> token (IP-bound)
     b. append ?token= به master.m3u8
     c. GET master.m3u8 -> list of variants
     d. انتخاب variant (medium quality)
     e. GET variant/index.m3u8 -> list of segments
     f. دانلود همه segment‌ها (curl_cffi impersonate)
     g. ffmpeg concat -> فایل نهایی .mp4/.mkv

استفاده:
  from vidsrc_downloader import download_movie, download_episode, get_stream_info

  path = await download_movie("tt33071426", out_dir="/tmp", quality="720p")
  path = await download_episode("tt0944947", 1, 1, out_dir="/tmp", quality="720p")
"""
import asyncio
import base64
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple
from urllib.parse import urlparse, urljoin

from curl_cffi.requests import AsyncSession
import wasmtime

logger = logging.getLogger("VidsrcDownloader")

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_API_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "application/json",
    "Referer": "https://cloudorchestranova.com/",
}

_SEG_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "*/*",
    "Origin": "https://cloudorchestranova.com",
    "Referer": "https://cloudorchestranova.com/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "cross-site",
}


# ─── Stream info ────────────────────────────────────────────


@dataclass
class StreamInfo:
    """اطلاعات استریم از vidsrcme"""
    title: str
    imdb_id: str
    backdrop: str
    file_name: str
    stream_urls: List[str]      # m3u8 URLs (decrypted)
    is_series: bool = False
    season: Optional[int] = None
    episode: Optional[int] = None


# ─── WASM decryptor ─────────────────────────────────────────


_WASM_ENGINE = wasmtime.Engine()


def _decrypt_stream_urls(encrypted_b64: str, wasm_bytes: bytes) -> List[str]:
    """اجرای WASM برای decrypt stream_urls"""
    module = wasmtime.Module(_WASM_ENGINE, wasm_bytes)
    store = wasmtime.Store(_WASM_ENGINE)
    instance = wasmtime.Instance(store, module, [])
    exports = instance.exports(store)

    mem = exports["memory"]
    alloc = exports["alloc"]
    decrypt_fn = exports["decrypt"]

    enc = base64.b64decode(encrypted_b64)
    enc_len = len(enc)
    ptr = alloc(store, enc_len)
    mem.write(store, enc, ptr)
    out_len = decrypt_fn(store, ptr, enc_len)
    out_bytes = mem.read(store, ptr + 12, ptr + 12 + out_len)
    txt = out_bytes.decode("utf-8", errors="replace")
    return [u.strip() for u in txt.split("\n") if u.strip()]


# ─── Token helper ───────────────────────────────────────────


def _parse_token(text: str) -> str:
    """parseToken از player.js - token رو از response استخراج میکنه"""
    if text is None:
        return ""
    t = str(text).strip()
    if t and (t[0] == "{" or t[0] == "["):
        try:
            j = json.loads(t)
            if isinstance(j, str):
                return j
            if isinstance(j, dict):
                return j.get("token") or j.get("data") or j.get("string") or j.get("result") or ""
        except Exception:
            pass
    return t


def _apply_token(url: str, token: str) -> str:
    """append token به URL (اگه از قبل token= نداره)"""
    if not token:
        return url
    if "__TOKEN__" in url:
        return url.replace("__TOKEN__", token)
    if "token=" in url:
        return url
    return url + ("&" if "?" in url else "?") + "token=" + token


# ─── m3u8 parser ────────────────────────────────────────────


def _parse_master_m3u8(text: str) -> List[Tuple[str, int, str]]:
    """از master.m3u8 variant URL ها رو استخراج کن
    Returns: list of (url, bandwidth, resolution)
    """
    lines = text.split("\n")
    variants = []
    bandwidth = 0
    resolution = ""
    for line in lines:
        line = line.strip()
        if line.startswith("#EXT-X-STREAM-INF:"):
            m = re.search(r'BANDWIDTH=(\d+)', line)
            if m:
                bandwidth = int(m.group(1))
            m = re.search(r'RESOLUTION=(\d+x\d+)', line)
            if m:
                resolution = m.group(1)
        elif line and not line.startswith("#"):
            variants.append((line, bandwidth, resolution))
            bandwidth = 0
            resolution = ""
    return variants


def _parse_variant_m3u8(text: str) -> List[Tuple[str, float]]:
    """از variant/index.m3u8 segment URL ها رو استخراج کن
    Returns: list of (url, duration)
    """
    lines = text.split("\n")
    segments = []
    duration = 0.0
    for line in lines:
        line = line.strip()
        if line.startswith("#EXTINF:"):
            try:
                duration = float(line[8:].split(",")[0])
            except Exception:
                pass
        elif line and not line.startswith("#"):
            segments.append((line, duration))
            duration = 0.0
    return segments


def _pick_variant(variants: List[Tuple[str, int, str]], quality: str = "720p") -> Tuple[str, int, str]:
    """انتخاب variant با توجه به quality درخواستی"""
    if not variants:
        raise ValueError("No variants")
    # مرتب‌سازی بر اساس bandwidth نزولی
    sorted_v = sorted(variants, key=lambda v: v[1], reverse=True)
    # quality map: 1080p → بالاترین, 720p → متوسط, 480p → پایین
    if quality == "1080p" or quality == "best":
        return sorted_v[0]
    if quality == "480p" or quality == "worst":
        return sorted_v[-1]
    # 720p: وسط
    # پیدا کردن variant با ارتفاع ~720
    for v in sorted_v:
        res = v[2]  # "1280x720"
        if "x720" in res or "x692" in res or "x688" in res:
            return v
    # fallback: متوسط
    return sorted_v[len(sorted_v) // 2] if len(sorted_v) > 1 else sorted_v[0]


# ─── Get stream info ────────────────────────────────────────


async def get_stream_info(
    imdb_id: str,
    season: Optional[int] = None,
    episode: Optional[int] = None,
) -> Optional[StreamInfo]:
    """
    گرفتن اطلاعات استریم (شامل m3u8 URLs decrypt شده).

    برای فیلم: get_stream_info("tt33071426")
    برای سریال: get_stream_info("tt0944947", season=1, episode=1)
    """
    is_series = season is not None and episode is not None
    api_type = "tv" if is_series else "movie"

    url = f"https://data.vidsrcme.ru/api.php?type={api_type}&imdb={imdb_id}"
    if is_series:
        url += f"&season={season}&episode={episode}"
    url += "&stream_urls"

    logger.info("get_stream_info: %s", url)
    async with AsyncSession() as s:
        # API call
        r = await s.get(url, impersonate="chrome", headers=_API_HEADERS, timeout=20)
        if r.status_code != 200:
            logger.warning("API HTTP %d", r.status_code)
            return None

        d = r.json()
        if d.get("status_code") != "200":
            logger.warning("API status_code=%s", d.get("status_code"))
            return None

        data = d.get("data", {})
        encrypted = data.get("stream_urls", "")
        if not encrypted:
            logger.warning("No stream_urls in response")
            return None

        vs = d.get("vs") or data.get("vs")
        if not vs:
            logger.warning("No vs field (no wasm url)")
            return None

        # WASM
        wasm_url = vs.get("wasm_url")
        if not wasm_url:
            logger.warning("No wasm_url in vs")
            return None
        r = await s.get(wasm_url, impersonate="chrome", headers=_API_HEADERS, timeout=20)
        if r.status_code != 200 or r.content[:4] != b'\x00asm':
            logger.warning("WASM fetch failed: %d", r.status_code)
            return None
        wasm_bytes = r.content

        # Decrypt
        try:
            stream_urls = _decrypt_stream_urls(encrypted, wasm_bytes)
        except Exception as e:
            logger.error("Decrypt failed: %s", e)
            return None

        if not stream_urls:
            logger.warning("No URLs after decrypt")
            return None

        return StreamInfo(
            title=data.get("title", ""),
            imdb_id=data.get("imdb_id", imdb_id),
            backdrop=data.get("backdrop", ""),
            file_name=data.get("file_name", ""),
            stream_urls=stream_urls,
            is_series=is_series,
            season=season,
            episode=episode,
        )


# ─── Download ───────────────────────────────────────────────


async def _fetch_token(s: AsyncSession, stream_url: str) -> str:
    """تولید token برای یه stream URL"""
    p = urlparse(stream_url)
    gen_url = f"{p.scheme}://{p.netloc}/generate.php"
    r = await s.get(gen_url, impersonate="chrome", headers=_SEG_HEADERS, timeout=20)
    if r.status_code != 200:
        logger.warning("generate.php HTTP %d", r.status_code)
        return ""
    return _parse_token(r.text)


async def _download_segments(
    s: AsyncSession,
    master_url: str,
    token: str,
    out_dir: str,
    quality: str,
    progress_cb=None,
    concurrency: int = 8,
) -> Tuple[List[str], float]:
    """دانلود همه segment‌ها با concurrency
    Returns: (list of segment file paths, total_duration_sec)
    """
    master_with_token = _apply_token(master_url, token)
    p = urlparse(master_with_token)
    base = f"{p.scheme}://{p.netloc}"

    # master.m3u8
    r = await s.get(master_with_token, impersonate="chrome", headers=_SEG_HEADERS, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"master.m3u8 HTTP {r.status_code}: {r.text[:200]}")
    variants = _parse_master_m3u8(r.text)
    logger.info("master.m3u8: %d variants", len(variants))

    # pick variant
    var_url_rel, bw, res = _pick_variant(variants, quality)
    logger.info("picked variant: %s (bw=%d, res=%s)", var_url_rel[:80], bw, res)

    # variant URL absolute
    if var_url_rel.startswith("/"):
        var_url = base + var_url_rel
    elif var_url_rel.startswith("http"):
        var_url = var_url_rel
    else:
        var_url = urljoin(master_with_token, var_url_rel)
    # token (اگه variant URL خودش token داره، append نمیشه)
    if "token=" not in var_url:
        var_url = _apply_token(var_url, token)

    # variant/index.m3u8
    r = await s.get(var_url, impersonate="chrome", headers=_SEG_HEADERS, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"variant.m3u8 HTTP {r.status_code}")
    segments = _parse_variant_m3u8(r.text)
    logger.info("variant.m3u8: %d segments", len(segments))
    if not segments:
        raise RuntimeError("No segments in variant")

    # variant base (segment URLs معمولا relative هستند)
    var_base = var_url.rsplit("/", 1)[0] + "/"

    # ساخت ليست URL های absolute
    seg_urls = []
    total_dur = 0.0
    for seg_rel, dur in segments:
        if seg_rel.startswith("/"):
            su = base + seg_rel
        elif seg_rel.startswith("http"):
            su = seg_rel
        else:
            su = var_base + seg_rel
        if "token=" not in su:
            su = _apply_token(su, token)
        seg_urls.append(su)
        total_dur += dur

    # دانلود با semaphore
    sem = asyncio.Semaphore(concurrency)
    seg_paths = [None] * len(seg_urls)

    async def download_one(idx: int, url: str):
        async with sem:
            for attempt in range(3):
                try:
                    r = await s.get(url, impersonate="chrome", headers=_SEG_HEADERS, timeout=60)
                    if r.status_code == 200 and r.content:
                        path = os.path.join(out_dir, f"seg_{idx:05d}.ts")
                        with open(path, "wb") as f:
                            f.write(r.content)
                        seg_paths[idx] = path
                        if progress_cb:
                            progress_cb(idx + 1, len(seg_urls))
                        return
                except Exception as e:
                    logger.warning("seg %d attempt %d failed: %s", idx, attempt + 1, e)
                await asyncio.sleep(0.5 * (attempt + 1))
            logger.error("seg %d failed after 3 attempts", idx)

    await asyncio.gather(*[download_one(i, u) for i, u in enumerate(seg_urls)])
    seg_paths = [p for p in seg_paths if p]
    return seg_paths, total_dur


def _concat_segments(seg_paths: List[str], out_path: str) -> bool:
    """concat با ffmpeg"""
    if not seg_paths:
        return False
    # concat list
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for p in seg_paths:
            f.write(f"file '{p}'\n")
        list_path = f.name

    try:
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", list_path,
            "-c", "copy",
            "-bsf:a", "aac_adtstoasc",
            out_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            # fallback: بدون aac_adtstoasc
            cmd2 = [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0",
                "-i", list_path,
                "-c", "copy",
                out_path,
            ]
            result = subprocess.run(cmd2, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                logger.error("ffmpeg concat failed: %s", result.stderr[-500:])
                return False
        return True
    finally:
        try:
            os.unlink(list_path)
        except Exception:
            pass


async def download_movie(
    imdb_id: str,
    out_dir: str = "/tmp",
    quality: str = "720p",
    progress_cb=None,
) -> Optional[str]:
    """دانلود یه فیلم. Returns: path to downloaded file or None."""
    return await _download(imdb_id, None, None, out_dir, quality, progress_cb)


async def download_episode(
    imdb_id: str,
    season: int,
    episode: int,
    out_dir: str = "/tmp",
    quality: str = "720p",
    progress_cb=None,
) -> Optional[str]:
    """دانلود یه قسمت سریال. Returns: path to downloaded file or None."""
    return await _download(imdb_id, season, episode, out_dir, quality, progress_cb)


async def _download(
    imdb_id: str,
    season: Optional[int],
    episode: Optional[int],
    out_dir: str,
    quality: str,
    progress_cb,
) -> Optional[str]:
    """download internal"""
    info = await get_stream_info(imdb_id, season, episode)
    if not info or not info.stream_urls:
        logger.error("get_stream_info failed for %s", imdb_id)
        return None

    os.makedirs(out_dir, exist_ok=True)

    # اسم فایل خروجی
    if info.is_series:
        out_name = f"{info.title} S{season:02d}E{episode:02d}.mp4".replace("/", "_")
    else:
        out_name = f"{info.title}.mp4".replace("/", "_")
    out_path = os.path.join(out_dir, out_name)

    # temp dir برای segment‌ها
    with tempfile.TemporaryDirectory(prefix="vidsrc_") as tmp:
        async with AsyncSession() as s:
            # امتحان هر stream URL تا یکی کار کنه
            last_err = None
            for i, master_url in enumerate(info.stream_urls):
                logger.info("trying stream %d/%d: %s", i + 1, len(info.stream_urls), master_url[:80])
                try:
                    token = await _fetch_token(s, master_url)
                    if not token:
                        logger.warning("no token for stream %d", i)
                        continue
                    seg_paths, total_dur = await _download_segments(
                        s, master_url, token, tmp, quality, progress_cb,
                    )
                    if not seg_paths:
                        logger.warning("no segments for stream %d", i)
                        continue
                    logger.info("downloaded %d segments, total=%.1f sec", len(seg_paths), total_dur)

                    if _concat_segments(seg_paths, out_path):
                        logger.info("✅ saved to %s", out_path)
                        return out_path
                    else:
                        logger.error("concat failed for stream %d", i)
                except Exception as e:
                    last_err = e
                    logger.warning("stream %d failed: %s", i, e)
                    await asyncio.sleep(1)
                    continue

            logger.error("all streams failed, last error: %s", last_err)
            return None


# ─── Quick test ─────────────────────────────────────────────


async def _test():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    print("=== Test movie download (5 sec) ===")
    # فقط برای تست: یه clip کوتاه
    info = await get_stream_info("tt33071426")
    if info:
        print(f"title: {info.title}")
        print(f"backdrop: {info.backdrop}")
        print(f"file_name: {info.file_name}")
        print(f"stream_urls ({len(info.stream_urls)}):")
        for u in info.stream_urls:
            print(f"  {u[:100]}")

    # download real
    def progress(done, total):
        print(f"\r  segments: {done}/{total}", end="", flush=True)

    path = await download_movie(
        "tt33071426",
        out_dir="/home/z/my-project/download",
        quality="720p",
        progress_cb=progress,
    )
    print()
    if path:
        import os
        sz = os.path.getsize(path)
        print(f"✅ downloaded: {path} ({sz/1024/1024:.1f} MB)")
    else:
        print("❌ download failed")


if __name__ == "__main__":
    asyncio.run(_test())
