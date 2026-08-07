"""
vidsrc_extras.py
────────────────
توابع اضافی برای vidsrcme:
  - get_qualities(): لیست کیفیت‌های موجود از master.m3u8
  - get_subtitle(): دانلود زیرنویس فارسی از OpenSubtitles (با fallback)
  - download_with_quality(): دانلود ویدیو با کیفیت انتخابی

استفاده:
  from vidsrc_extras import get_qualities, get_subtitle, download_with_quality

  qualities = await get_qualities("tt33071426")
  sub_path = await get_subtitle("tt33071426", out_dir="/tmp")
  path = await download_with_quality("tt33071426", "720p", "/tmp")
"""
import asyncio
import base64
import gzip
import io
import logging
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from typing import List, Optional, Tuple
from urllib.parse import urlparse, urljoin

from curl_cffi.requests import AsyncSession
import httpx
import wasmtime

logger = logging.getLogger("VidsrcExtras")

# import از ماژول اصلی
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vidsrc_downloader import (
    _USER_AGENT, _API_HEADERS, _SEG_HEADERS,
    _decrypt_stream_urls, _parse_token, _apply_token,
    _parse_master_m3u8, _parse_variant_m3u8,
    get_stream_info, StreamInfo,
    _fetch_token, _download_segments, _concat_segments,
)


# ─── Quality helpers ────────────────────────────────────────


@dataclass
class Quality:
    """یه کیفیت از master.m3u8"""
    label: str            # "1080p", "720p", "480p", "Auto"
    bandwidth: int        # bits per second
    resolution: str       # "1920x1080"
    height: int           # 1080
    url: str              # variant URL (relative یا absolute)
    is_auto: bool = False

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "bandwidth": self.bandwidth,
            "resolution": self.resolution,
            "height": self.height,
            "url": self.url,
            "is_auto": self.is_auto,
        }


def _resolution_to_label(resolution: str, bandwidth: int) -> str:
    """تبدیل "1920x1080" → "1080p" """
    if not resolution:
        return f"{bandwidth // 1000}kbps"
    parts = resolution.split("x")
    if len(parts) == 2:
        try:
            h = int(parts[1])
            if h >= 1080:
                return "1080p"
            if h >= 720:
                return "720p"
            if h >= 480:
                return "480p"
            if h >= 360:
                return "360p"
            return f"{h}p"
        except ValueError:
            pass
    return "Auto"


def _variant_to_quality(variant: Tuple[str, int, str]) -> Quality:
    url, bw, res = variant
    label = _resolution_to_label(res, bw)
    h = 0
    if res and "x" in res:
        try:
            h = int(res.split("x")[1])
        except ValueError:
            pass
    return Quality(
        label=label,
        bandwidth=bw,
        resolution=res,
        height=h,
        url=url,
        is_auto=False,
    )


async def get_qualities(
    imdb_id: str,
    season: Optional[int] = None,
    episode: Optional[int] = None,
) -> List[dict]:
    """
    گرفتن لیست کیفیت‌های موجود برای یه فیلم/قسمت.

    Returns:
        list of dicts:
        - label: "1080p", "720p", ...
        - bandwidth, resolution, height, url, is_auto
    """
    info = await get_stream_info(imdb_id, season, episode)
    if not info or not info.stream_urls:
        logger.warning("get_qualities: no stream info")
        return []

    async with AsyncSession() as s:
        for master_url in info.stream_urls:
            try:
                token = await _fetch_token(s, master_url)
                if not token:
                    continue
                master_with_token = _apply_token(master_url, token)
                r = await s.get(master_with_token, impersonate="chrome",
                                headers=_SEG_HEADERS, timeout=20)
                if r.status_code != 200:
                    continue
                variants = _parse_master_m3u8(r.text)
                if not variants:
                    continue
                # تبدیل به Quality و حذف تکراری‌ها بر اساس label
                qualities = [_variant_to_quality(v) for v in variants]
                seen_labels = set()
                unique = []
                for q in sorted(qualities, key=lambda x: x.height, reverse=True):
                    if q.label in seen_labels:
                        continue
                    seen_labels.add(q.label)
                    unique.append(q)

                # اضافه کردن Auto
                auto = Quality(label="Auto", bandwidth=0, resolution="", height=0, url="", is_auto=True)
                return [auto.to_dict()] + [q.to_dict() for q in unique]
            except Exception as e:
                logger.warning("get_qualities: stream failed: %s", e)
                continue
    return []


# ─── Download with specific quality ─────────────────────────


async def download_with_quality(
    imdb_id: str,
    quality_label: str,        # "Auto", "1080p", "720p", ...
    out_dir: str,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    progress_cb=None,
) -> Optional[str]:
    """
    دانلود ویدیو با کیفیت مشخص.

    quality_label="Auto" → بهترین کیفیت
    quality_label="720p" → کیفیت 720p (اگه نباشه، نزدیک‌ترین)
    """
    info = await get_stream_info(imdb_id, season, episode)
    if not info or not info.stream_urls:
        return None

    os.makedirs(out_dir, exist_ok=True)
    if info.is_series:
        out_name = f"{info.title} S{season:02d}E{episode:02d}.mp4".replace("/", "_")
    else:
        out_name = f"{info.title}.mp4".replace("/", "_")
    out_path = os.path.join(out_dir, out_name)

    with tempfile.TemporaryDirectory(prefix="vidsrc_") as tmp:
        async with AsyncSession() as s:
            last_err = None
            for master_url in info.stream_urls:
                try:
                    token = await _fetch_token(s, master_url)
                    if not token:
                        continue
                    master_with_token = _apply_token(master_url, token)
                    r = await s.get(master_with_token, impersonate="chrome",
                                    headers=_SEG_HEADERS, timeout=20)
                    if r.status_code != 200:
                        continue
                    variants = _parse_master_m3u8(r.text)
                    if not variants:
                        continue

                    # انتخاب variant بر اساس quality_label
                    qualities = [_variant_to_quality(v) for v in variants]
                    sorted_qs = sorted(qualities, key=lambda x: x.height, reverse=True)

                    chosen = None
                    if quality_label == "Auto" or quality_label == "best":
                        chosen = sorted_qs[0]
                    elif quality_label == "worst":
                        chosen = sorted_qs[-1]
                    else:
                        # پیدا کردن variant با label مساوی
                        for q in sorted_qs:
                            if q.label == quality_label:
                                chosen = q
                                break
                        if not chosen:
                            # نزدیک‌ترین
                            target_h = int(re.search(r'\d+', quality_label).group(0)) if re.search(r'\d+', quality_label) else 720
                            chosen = min(sorted_qs, key=lambda q: abs(q.height - target_h) if q.height else 9999)

                    logger.info("chosen quality: %s (%s) bw=%d", chosen.label, chosen.resolution, chosen.bandwidth)

                    # URL absolute
                    p = urlparse(master_with_token)
                    base = f"{p.scheme}://{p.netloc}"
                    if chosen.url.startswith("/"):
                        var_url = base + chosen.url
                    elif chosen.url.startswith("http"):
                        var_url = chosen.url
                    else:
                        var_url = urljoin(master_with_token, chosen.url)
                    if "token=" not in var_url:
                        var_url = _apply_token(var_url, token)

                    # fetch variant m3u8
                    r = await s.get(var_url, impersonate="chrome", headers=_SEG_HEADERS, timeout=20)
                    if r.status_code != 200:
                        continue
                    segments = _parse_variant_m3u8(r.text)
                    if not segments:
                        continue

                    var_base = var_url.rsplit("/", 1)[0] + "/"
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

                    logger.info("downloading %d segments (%.1f sec)", len(seg_urls), total_dur)

                    # download with concurrency
                    sem = asyncio.Semaphore(8)
                    seg_paths = [None] * len(seg_urls)

                    async def download_one(idx: int, url: str):
                        async with sem:
                            for attempt in range(3):
                                try:
                                    rr = await s.get(url, impersonate="chrome",
                                                     headers=_SEG_HEADERS, timeout=60)
                                    if rr.status_code == 200 and rr.content:
                                        path = os.path.join(tmp, f"seg_{idx:05d}.ts")
                                        with open(path, "wb") as f:
                                            f.write(rr.content)
                                        seg_paths[idx] = path
                                        if progress_cb:
                                            progress_cb(idx + 1, len(seg_urls))
                                        return
                                except Exception as e:
                                    logger.warning("seg %d attempt %d: %s", idx, attempt + 1, e)
                                await asyncio.sleep(0.5 * (attempt + 1))

                    await asyncio.gather(*[download_one(i, u) for i, u in enumerate(seg_urls)])
                    seg_paths = [p for p in seg_paths if p]

                    if not seg_paths:
                        continue

                    if _concat_segments(seg_paths, out_path):
                        logger.info("✅ saved: %s", out_path)
                        return out_path

                except Exception as e:
                    last_err = e
                    logger.warning("stream failed: %s", e)
                    await asyncio.sleep(1)
                    continue

            logger.error("all streams failed: %s", last_err)
            return None


# ─── Subtitle fetching ──────────────────────────────────────


_OS_HEADERS = {
    "User-Agent": "trailers.to-UA",
    "Accept": "application/json",
}

_CACHE_URL = "https://cloudorchestranova.com/embed/iframe_player/cache.php"

_CACHE_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "*/*",
    "Referer": "https://cloudorchestranova.com/",
}


@dataclass
class Subtitle:
    """یه زیرنویس از OpenSubtitles"""
    file_name: str
    language: str           # "Persian", "English", ...
    language_code: str      # "per", "eng", ...
    download_link: str      # gzip URL
    rating: float = 0.0
    downloads: int = 0
    file_id: str = ""

    def to_dict(self) -> dict:
        return {
            "file_name": self.file_name,
            "language": self.language,
            "language_code": self.language_code,
            "download_link": self.download_link,
            "rating": self.rating,
            "downloads": self.downloads,
            "file_id": self.file_id,
        }


async def search_subtitles(
    imdb_id: str,
    lang: str = "per",
    season: Optional[int] = None,
    episode: Optional[int] = None,
) -> List[dict]:
    """
    جستجوی زیرنویس از OpenSubtitles.

    Args:
        imdb_id: tt33071426
        lang: کد زبان (per=Persian, eng=English, ara=Arabic, ...)
        season, episode: برای سریال

    Returns:
        list of dicts با فیلدهای: file_name, language, language_code,
        download_link, rating, downloads, file_id
    """
    # حذف "tt" prefix برای imdb_id
    imdb_num = imdb_id.replace("tt", "") if imdb_id.startswith("tt") else imdb_id

    if season and episode:
        url = f"https://rest.opensubtitles.org/search/episode-{episode}/imdbid-{imdb_num}/season-{season}/sublanguageid-{lang}"
    else:
        url = f"https://rest.opensubtitles.org/search/imdbid-{imdb_num}/sublanguageid-{lang}"

    logger.info("search_subtitles: %s", url)
    try:
        async with AsyncSession() as s:
            r = await s.get(url, impersonate="chrome", headers=_OS_HEADERS, timeout=20)
            if r.status_code != 200:
                logger.warning("OS search HTTP %d", r.status_code)
                return []
            data = r.json()
            if not isinstance(data, list):
                return []

            subs = []
            for item in data:
                link = item.get("SubDownloadLink", "")
                m = re.search(r'/filead/(\d+)/', link)
                file_id = m.group(1) if m else ""
                subs.append(Subtitle(
                    file_name=item.get("SubFileName", ""),
                    language=item.get("LanguageName", ""),
                    language_code=lang,
                    download_link=link,
                    rating=float(item.get("SubRating", 0) or 0),
                    downloads=int(item.get("SubDownloadsCnt", 0) or 0),
                    file_id=file_id,
                ))
            # مرتب‌سازی بر اساس downloads نزولی
            subs.sort(key=lambda x: x.downloads, reverse=True)
            return [s.to_dict() for s in subs]
    except Exception as e:
        logger.warning("search_subtitles failed: %s", e)
        return []


async def download_subtitle(
    sub: dict,
    out_dir: str,
    prefer_format: str = "vtt",
) -> Optional[str]:
    """
    دانلود و تبدیل زیرنویس به VTT یا SRT.
    از cache.php vidsrcme استفاده میکنه (proxy).

    Args:
        sub: dict از search_subtitles
        out_dir: مسیر ذخیره
        prefer_format: "vtt" یا "srt"

    Returns:
        path فایل زیرنویس ذخیره شده
    """
    file_id = sub.get("file_id", "")
    if not file_id:
        logger.warning("no file_id in subtitle")
        return None

    os.makedirs(out_dir, exist_ok=True)
    file_name = sub.get("file_name", f"subtitle.{prefer_format}")
    # تبدیل پسوند به فرمت درخواستی
    base_name = os.path.splitext(file_name)[0]
    out_path = os.path.join(out_dir, f"{base_name}.{prefer_format}")

    try:
        async with AsyncSession() as s:
            # 1. اگه cached باشه
            cache_get_url = f"{_CACHE_URL}?action=get&file_id={file_id}"
            r = await s.get(cache_get_url, impersonate="chrome",
                            headers=_CACHE_HEADERS, timeout=20)
            if r.status_code == 200:
                try:
                    d = r.json()
                    if d.get("success") and d.get("vtt_url"):
                        vtt_url = d["vtt_url"]
                        # download VTT
                        r = await s.get(vtt_url, impersonate="chrome",
                                        headers=_CACHE_HEADERS, timeout=20)
                        if r.status_code == 200:
                            with open(out_path, "wb") as f:
                                f.write(r.content)
                            logger.info("subtitle cached: %s", out_path)
                            return out_path
                except Exception:
                    pass

            # 2. download + cache
            download_link = sub.get("download_link", "")
            if not download_link:
                return None
            r = await s.get(download_link, impersonate="chrome",
                            headers=_CACHE_HEADERS, timeout=30)
            if r.status_code != 200:
                logger.warning("subtitle download HTTP %d", r.status_code)
                return None

            # POST به cache.php برای تبدیل
            cache_post_url = f"{_CACHE_URL}?action=cache_subtitle&file_id={file_id}&encoding=UTF-8"
            r = await s.post(cache_post_url, impersonate="chrome",
                             headers={**_CACHE_HEADERS, "Content-Type": "application/octet-stream"},
                             data=r.content, timeout=30)
            if r.status_code == 200:
                try:
                    d = r.json()
                    if d.get("success") and d.get("vtt_url"):
                        vtt_url = d["vtt_url"]
                        r = await s.get(vtt_url, impersonate="chrome",
                                        headers=_CACHE_HEADERS, timeout=20)
                        if r.status_code == 200:
                            with open(out_path, "wb") as f:
                                f.write(r.content)
                            logger.info("subtitle converted: %s", out_path)
                            return out_path
                except Exception as e:
                    logger.warning("cache_subtitle parse: %s", e)

            # 3. fallback: خودمون gzip رو decode کنیم
            r = await s.get(download_link, impersonate="chrome",
                            headers=_CACHE_HEADERS, timeout=30)
            if r.status_code == 200:
                try:
                    decompressed = gzip.decompress(r.content)
                    srt_text = decompressed.decode("utf-8", errors="replace")
                    # اگه srt خواستیم
                    if prefer_format == "srt":
                        with open(out_path, "w", encoding="utf-8") as f:
                            f.write(srt_text)
                    else:
                        # تبدیل srt به vtt
                        vtt_text = _srt_to_vtt(srt_text)
                        with open(out_path, "w", encoding="utf-8") as f:
                            f.write(vtt_text)
                    logger.info("subtitle fallback: %s", out_path)
                    return out_path
                except Exception as e:
                    logger.warning("fallback failed: %s", e)

    except Exception as e:
        logger.error("download_subtitle failed: %s", e)

    return None


def _srt_to_vtt(srt_text: str) -> str:
    """تبدیل SRT به VTT"""
    lines = srt_text.replace("\r\n", "\n").split("\n")
    out = ["WEBVTT", ""]
    for line in lines:
        # تبدیل timestamp‌ها از کاما به نقطه
        if "-->" in line:
            line = line.replace(",", ".")
        # حذف شماره‌های بلوک (در VTT لازم نیست)
        if line.strip().isdigit():
            continue
        out.append(line)
    return "\n".join(out)


async def get_persian_subtitle(
    imdb_id: str,
    out_dir: str,
    season: Optional[int] = None,
    episode: Optional[int] = None,
) -> Optional[str]:
    """
    میانبر: گرفتن اولین زیرنویس فارسی موجود و دانلودش.

    Returns:
        path فایل زیرنویس یا None
    """
    subs = await search_subtitles(imdb_id, "per", season, episode)
    if not subs:
        logger.info("no Persian subtitle found")
        return None
    # اولین رو بگیر (already sorted by downloads)
    return await download_subtitle(subs[0], out_dir, prefer_format="vtt")


# ─── Quick test ─────────────────────────────────────────────


async def _test():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    print("=== Test get_qualities for tt33071426 ===")
    qs = await get_qualities("tt33071426")
    for q in qs:
        print(f"  {q['label']}: res={q['resolution']} bw={q['bandwidth']}")

    print("\n=== Test get_qualities for tt0903747 S01E01 ===")
    qs = await get_qualities("tt0903747", season=1, episode=1)
    for q in qs:
        print(f"  {q['label']}: res={q['resolution']} bw={q['bandwidth']}")

    print("\n=== Test subtitle search ===")
    subs = await search_subtitles("tt33071426", "per")
    print(f"Found {len(subs)} Persian subs")
    for s in subs[:5]:
        print(f"  {s['file_name']} | downloads={s['downloads']} | rating={s['rating']}")


if __name__ == "__main__":
    asyncio.run(_test())
