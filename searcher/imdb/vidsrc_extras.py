"""
vidsrc_extras.py
────────────────
توابع کمکی برای دانلود از IMDB (نسخه جدید مبتنی بر imdbplay.tech).

این فایل صرفاً یک wrapper هست که توابع رو به imdbplay_downloader دیلیگیت می‌کنه.
متد قدیمی vidsrcme (WASM + cloudorchestranova + encrypted stream_urls) کامل حذف شده.

توابع عمومی:
  - get_qualities(imdb_id, season, episode) -> List[dict]
  - download_with_quality(imdb_id, quality_label, out_dir, ...) -> Optional[str]
  - get_persian_subtitle(imdb_id, season, episode, out_dir) -> Optional[str]
  - search_subtitles(imdb_id, lang, season, episode) -> List[dict]  (OpenSubtitles, برای زبان‌های غیر فارسی)
  - download_subtitle(sub, out_dir, prefer_format) -> Optional[str]  (OpenSubtitles)

زیرنویس فارسی به‌طور خودکار از سرورهای imdbplay (Vidzee, 2Embed, Videasy) گرفته می‌شه
و نیازی به OpenSubtitles نیست. توابع search_subtitles/download_subtitle برای زبان‌های
دیگه (انگلیسی، عربی، و غیره) باقی موندن.
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

logger = logging.getLogger("VidsrcExtras")

# User agent برای OpenSubtitles و subtitle cache
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


# ═══════════════════════════════════════════════════════════
#   Wrapper functions → imdbplay_downloader
# ═══════════════════════════════════════════════════════════


async def get_qualities(
    imdb_id: str,
    season: Optional[int] = None,
    episode: Optional[int] = None,
) -> List[dict]:
    """
    گرفتن لیست کیفیت‌های موجود برای یه فیلم/قسمت از سرورهای imdbplay.tech.

    سرورها به ترتیب اولویت امتحان می‌شن:
      1. Vidzee (WASM decrypt)
      2. Videasy (XOR cipher)
      3. Vidking (XOR cipher)
      4. 2Embed (vnest API)
      5. GarageBand (vidsrcme + WASM)

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


async def download_with_quality(
    imdb_id: str,
    quality_label: str,
    out_dir: str,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    progress_cb=None,
) -> Optional[str]:
    """
    دانلود ویدیو با کیفیت مشخص از سرورهای imdbplay.tech.

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


async def get_server_info(
    imdb_id: str,
    season: Optional[int] = None,
    episode: Optional[int] = None,
) -> Optional[dict]:
    """
    گرفتن اطلاعات سرور فعال (نام، متد، نوع stream) بدون شروع دانلود.

    Returns:
        dict با فیلدهای:
        - server: نام سرور (مثلاً "Vidzee")
        - method: متد استخراج (مثلاً "WASM Decrypt")
        - server_id: شناسه سرور (مثلاً "s2")
        - stream_type: نوع stream ("hls" یا "mp4")
    """
    try:
        from imdbplay_downloader import get_server_info as _new_get_info
        return await _new_get_info(imdb_id, season=season, episode=episode)
    except Exception as e:
        logger.error("get_server_info via imdbplay failed: %s", e)
        return None


def burn_subtitle_local(video_path: str, subtitle_path: str, out_path: str) -> Optional[str]:
    """Alias for embed_subtitle_soft (softsub, not hardcode)"""
    try:
        from imdbplay_downloader import embed_subtitle_soft as _embed
        return _embed(video_path, subtitle_path, out_path)
    except Exception as e:
        logger.error("embed_subtitle_soft failed: %s", e)
        return None


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
        from imdbplay_downloader import embed_subtitle_soft as _embed
        return _embed(video_path, subtitle_path, out_path)
    except Exception as e:
        logger.error("embed_subtitle_soft failed: %s", e)
        return None


# ═══════════════════════════════════════════════════════════
#   OpenSubtitles API (for non-Persian languages)
# ═══════════════════════════════════════════════════════════


_OS_HEADERS = {
    "User-Agent": "trailers.to-UA",
    "Accept": "application/json",
}


@dataclass
class Subtitle:
    """یه زیرنویس از OpenSubtitles"""
    file_name: str
    language: str
    language_code: str
    download_link: str
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
    lang: str = "eng",
    season: Optional[int] = None,
    episode: Optional[int] = None,
) -> List[dict]:
    """
    جستجوی زیرنویس از OpenSubtitles.

    Args:
        imdb_id: tt33071426
        lang: کد زبان (eng=English, ara=Arabic, ...) — برای فارسی از get_persian_subtitle استفاده کنید
        season, episode: برای سریال

    Returns:
        list of dicts با فیلدهای: file_name, language, language_code,
        download_link, rating, downloads, file_id
    """
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
    دانلود زیرنویس از OpenSubtitles (gzip) و تبدیل به VTT یا SRT.

    Args:
        sub: dict از search_subtitles
        out_dir: مسیر ذخیره
        prefer_format: "vtt" یا "srt"

    Returns:
        path فایل زیرنویس ذخیره شده
    """
    download_link = sub.get("download_link", "")
    if not download_link:
        logger.warning("no download_link in subtitle")
        return None

    os.makedirs(out_dir, exist_ok=True)
    file_name = sub.get("file_name", f"subtitle.{prefer_format}")
    base_name = os.path.splitext(file_name)[0]
    out_path = os.path.join(out_dir, f"{base_name}.{prefer_format}")

    try:
        async with AsyncSession() as s:
            r = await s.get(download_link, impersonate="chrome",
                            headers={"User-Agent": _USER_AGENT}, timeout=30)
            if r.status_code != 200:
                logger.warning("subtitle download HTTP %d", r.status_code)
                return None

            # OpenSubtitles gzip شده
            try:
                decompressed = gzip.decompress(r.content)
                srt_text = decompressed.decode("utf-8", errors="replace")
            except Exception as e:
                logger.warning("gzip decompress failed: %s", e)
                return None

            if prefer_format == "srt":
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(srt_text)
            else:
                vtt_text = _srt_to_vtt(srt_text)
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(vtt_text)
            logger.info("subtitle saved: %s", out_path)
            return out_path
    except Exception as e:
        logger.error("download_subtitle failed: %s", e)

    return None


def _srt_to_vtt(srt_text: str) -> str:
    """تبدیل SRT به VTT"""
    lines = srt_text.replace("\r\n", "\n").split("\n")
    out = ["WEBVTT", ""]
    for line in lines:
        if "-->" in line:
            line = line.replace(",", ".")
        if line.strip().isdigit():
            continue
        out.append(line)
    return "\n".join(out)


# ═══════════════════════════════════════════════════════════
#   Quick test
# ═══════════════════════════════════════════════════════════


async def _test():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    print("=== Test get_qualities for tt33071426 ===")
    qs = await get_qualities("tt33071426")
    for q in qs:
        print(f"  {q['label']}: server={q.get('server', '')} res={q['resolution']}")

    print("\n=== Test get_persian_subtitle for tt33071426 ===")
    sub = await get_persian_subtitle("tt33071426", out_dir="/tmp/sub_test")
    print(f"  Subtitle: {sub}")


if __name__ == "__main__":
    asyncio.run(_test())
