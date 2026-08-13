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

    NOTE: این تابع اکنون از imdbplay_downloader (سرورهای imdbplay.tech) استفاده می‌کند.
    روش قدیمی vidsrcme که WASM و stream_urls encrypted داشت حذف شده.

    Returns:
        list of dicts:
        - label: "Auto", "1080p", "720p", ...
        - bandwidth, resolution, height, url, is_auto, server
    """
    try:
        from imdbplay_downloader import get_qualities as _new_get_qualities
        result = await _new_get_qualities(imdb_id, season, episode)
        # normalize: اگه فیلد height نبود، از resolution استخراج کن
        for q in result:
            if "height" not in q:
                res = q.get("resolution", "")
                h = 0
                if res and "x" in res:
                    try:
                        h = int(res.split("x")[1])
                    except ValueError:
                        pass
                q["height"] = h
        return result
    except Exception as e:
        logger.error("get_qualities via imdbplay failed: %s", e)
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

    NOTE: این تابع اکنون از imdbplay_downloader (سرورهای imdbplay.tech) استفاده می‌کند.
    روش قدیمی vidsrcme که WASM و stream_urls encrypted داشت حذف شده.

    quality_label="Auto" → بهترین کیفیت
    quality_label="720p" → کیفیت 720p (اگه نباشه، نزدیک‌ترین)
    """
    try:
        from imdbplay_downloader import download_with_quality as _new_download
        return await _new_download(
            imdb_id, quality_label, out_dir, season, episode, progress_cb=progress_cb
        )
    except Exception as e:
        logger.error("download_with_quality via imdbplay failed: %s", e)
        raise


# ─── Persian subtitle fetching (new, from imdbplay servers) ────


async def get_persian_subtitle(
    imdb_id: str,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    out_dir: Optional[str] = None,
) -> Optional[str]:
    """
    گرفتن زیرنویس فارسی از سرورهای imdbplay.

    ترتیب جستجو:
      1. Vidzee (core.vidzee.wtf/subs)
      2. 2Embed (sub.vdrk.site)
      3. Videasy (subs.videasy.to)

    Returns:
        مسیر فایل زیرنویس VTT، یا None.
    """
    try:
        from imdbplay_downloader import get_persian_subtitle as _new_get_sub
        return await _new_get_sub(imdb_id, season=season, episode=episode, out_dir=out_dir)
    except Exception as e:
        logger.error("get_persian_subtitle via imdbplay failed: %s", e)
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


# NOTE: تابع قدیمی get_persian_subtitle (که از OpenSubtitles استفاده می‌کرد) حذف شد.
# تابع جدید در ابتدای همین فایل (از imdbplay_downloader) تعریف شده و از سرورهای
# imdbplay.tech (Vidzee, 2Embed, Videasy) زیرنویس فارسی می‌گیره.


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
