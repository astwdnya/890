"""
subf2m_subtitle.py
──────────────────
گرفتن زیرنویس فارسی از subf2m.co (mirror از Subscene).

این منبع خارج از ایران هم قابل دسترسی هست و زیرنویس فارسی زیادی داره.

توابع عمومی:
  - search_persian_subtitle(title, year=None) -> List[dict]
      [{'version': 'Inception.2010.1080p.BluRay', 'url': '...', 'downloads': 1198}, ...]
  - download_persian_subtitle(title, out_dir, prefer_index=0) -> Optional[str]
      مسیر فایل SRT UTF-8، یا None در صورت خطا.
  - get_subtitle_for_imdb(imdb_id, title, year=None, season=None, episode=None, out_dir=None) -> Optional[str]
      جستجوی هوشمند: اول با نام انگلیسی، اگه نبود با slug IMDb.
"""
import asyncio
import logging
import os
import re
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict
from urllib.parse import urljoin, quote_plus

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("Subf2m")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_BASE = "https://subf2m.co"


def _slugify(title: str) -> str:
    """تبدیل عنوان به slug ساده‌ی URL-friendly."""
    # حذف سال و کلمات اضافی
    title = re.sub(r"\(\d{4}\)", "", title)
    title = re.sub(r"\b(19|20)\d{2}\b", "", title)
    # فقط حروف الفبا و عدد و خط‌فاصله
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug


async def search_persian_subtitle(
    title: str,
    year: Optional[int] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    timeout: int = 15,
) -> List[dict]:
    """
    جستجوی زیرنویس فارسی روی subf2m.co.

    Args:
        title: عنوان فیلم/سریال (به انگلیسی بهتره)
        year: سال انتشار (اختیاری، برای دقت بیشتر)
        season: شماره فصل (اختیاری، برای سریال‌ها)
        episode: شماره قسمت (اختیاری)

    Returns:
        لیست dict با فیلدهای:
        - url: لینک صفحه‌ی جزئیات زیرنویس
        - version: نام نسخه (مثلاً "Inception.2010.1080p.BluRay")
        - downloads: تعداد دانلودها (int)
        - fps: فریم‌ریت (اگه موجود باشه)
        - hearing_impaired: bool
    """
    slug = _slugify(title)
    if not slug:
        return []

    url = f"{_BASE}/subtitles/{slug}/farsi_persian"
    logger.info("subf2m search: %s", url)
    try:
        async with httpx.AsyncClient(http2=False, verify=False, follow_redirects=True, timeout=timeout) as client:
            r = await client.get(url, headers={
                "User-Agent": _USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
            })
            if r.status_code != 200:
                logger.warning("subf2m search HTTP %d for %s", r.status_code, url)
                return []
            soup = BeautifulSoup(r.text, "html.parser")
            results = []
            # Find all subtitle list items
            for li in soup.select("li.item"):
                # Iterate ALL <a> inside li to find the detail-page link
                detail_url = None
                for a in li.find_all("a", href=True):
                    href = a["href"]
                    # Match: /subtitles/{slug}/farsi_persian/{id}
                    if re.match(r"/subtitles/[^/]+/farsi_persian/\d+", href):
                        detail_url = urljoin(_BASE, href)
                        break
                if not detail_url:
                    continue
                # Find version text — usually 2nd <a> text or li .col1 .text-col
                version = ""
                # Look for the version: usually in second <a> or in the listing text
                all_a = li.find_all("a", href=True)
                # Find text that's not just "Farsi/Persian"
                for a in all_a:
                    text = a.get_text(" ", strip=True)
                    if text and "farsi" not in text.lower() and "persian" not in text.lower():
                        version = text[:100]
                        break
                if not version:
                    # Fallback: full li text
                    full_text = li.get_text(" ", strip=True)
                    version = full_text[:100]
                # Find download count — span with just a number
                downloads = 0
                for sp in li.find_all("span"):
                    text = sp.get_text(strip=True)
                    m = re.match(r"^([\d,]+)$", text)
                    if m:
                        downloads = int(m.group(1).replace(",", ""))
                        break
                # FPS
                fps = 0
                for sp in li.find_all("span"):
                    text = sp.get_text(strip=True)
                    m = re.match(r"^(\d+(\.\d+)?)\s*fps$", text, re.I)
                    if m:
                        fps = float(m.group(1))
                        break
                # Hearing impaired?
                hi = bool(li.select_one(".hi"))
                results.append({
                    "url": detail_url,
                    "version": version,
                    "downloads": downloads,
                    "fps": fps,
                    "hearing_impaired": hi,
                })
            # Sort: highest downloads first
            results.sort(key=lambda x: x["downloads"], reverse=True)
            logger.info("subf2m found %d Persian subtitles for '%s'", len(results), slug)
            return results
    except Exception as e:
        logger.error("subf2m search error: %s", e)
        return []


async def download_persian_subtitle(
    detail_url: str,
    out_dir: str,
    timeout: int = 30,
) -> Optional[str]:
    """
    دانلود زیرنویس از subf2m.co (URL صفحه‌ی جزئیات).

    Returns:
        مسیر فایل SRT (UTF-8)، یا None در صورت خطا.
    """
    try:
        async with httpx.AsyncClient(http2=False, verify=False, follow_redirects=True, timeout=timeout) as client:
            # 1. Fetch detail page (some sites require session)
            r = await client.get(detail_url, headers={
                "User-Agent": _USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
            })
            if r.status_code != 200:
                logger.warning("subf2m detail HTTP %d for %s", r.status_code, detail_url)
                return None
            # 2. Build download URL: detail_url + /download
            download_url = detail_url.rstrip("/") + "/download"
            r = await client.get(download_url, headers={
                "User-Agent": _USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": detail_url,
            }, timeout=timeout)
            if r.status_code != 200:
                logger.warning("subf2m download HTTP %d for %s", r.status_code, download_url)
                return None

            os.makedirs(out_dir, exist_ok=True)
            content = r.content
            # 3. Detect file type
            if content[:4] == b"PK\x03\x04":
                # ZIP
                zip_path = os.path.join(out_dir, "subtitle.zip")
                with open(zip_path, "wb") as f:
                    f.write(content)
                # Extract
                with zipfile.ZipFile(zip_path) as zf:
                    zf.extractall(out_dir)
                    srt_files = [n for n in zf.namelist() if n.endswith(".srt")]
                    if not srt_files:
                        logger.warning("subf2m: no SRT in ZIP — contents: %s", zf.namelist())
                        return None
                    srt_name = srt_files[0]
                    srt_path = os.path.join(out_dir, srt_name)
            elif content[:7] == b"Rar!\x1a\x07":
                # RAR — use unrar
                rar_path = os.path.join(out_dir, "subtitle.rar")
                with open(rar_path, "wb") as f:
                    f.write(content)
                try:
                    subprocess.run(["unrar", "x", "-y", rar_path, out_dir],
                                 check=True, capture_output=True, timeout=30)
                except Exception as e:
                    logger.error("subf2m: RAR extraction failed: %s", e)
                    return None
                # Find SRT
                srt_files = [f for f in os.listdir(out_dir) if f.endswith(".srt")]
                if not srt_files:
                    return None
                srt_path = os.path.join(out_dir, srt_files[0])
            elif b"-->" in content[:5000] or content[:3] in (b"1\r\n", b"1\n"):
                # Direct SRT
                srt_path = os.path.join(out_dir, "subtitle.srt")
                with open(srt_path, "wb") as f:
                    f.write(content)
            else:
                logger.warning("subf2m: unknown file format (first 4 bytes: %s)", content[:4])
                return None

            # 4. Detect encoding & convert to UTF-8
            with open(srt_path, "rb") as f:
                raw = f.read()
            encoding = None
            for enc in ["utf-8", "windows-1256", "arabic", "cp1256", "iso-8859-6"]:
                try:
                    decoded = raw.decode(enc)
                    if enc == "utf-8":
                        encoding = enc
                        break
                    # Sanity check: must contain Persian/Arabic letters
                    if any(c in decoded for c in ["ی", "ا", "ر", "و", "ه"]):
                        encoding = enc
                        break
                except UnicodeDecodeError:
                    continue
            if not encoding:
                encoding = "windows-1256"
                decoded = raw.decode(encoding, errors="replace")

            # Save UTF-8 version
            utf8_path = os.path.join(out_dir, "fa.srt")
            if encoding != "utf-8":
                with open(utf8_path, "w", encoding="utf-8") as f:
                    f.write(decoded)
            else:
                with open(utf8_path, "wb") as f:
                    f.write(raw)
            logger.info("subf2m: saved UTF-8 SRT at %s (encoding=%s)", utf8_path, encoding)
            return utf8_path
    except Exception as e:
        logger.error("subf2m download error: %s", e)
        return None


async def _search_by_title(title: str, timeout: int = 15) -> List[str]:
    """
    جستجوی عنوان از طریق searchbytitle در subf2m.co.
    برای سریال‌ها استفاده می‌شه چون slug فرمت خاصی دارن (مثل breaking-bad-first-season).

    Returns:
        لیست slug های پیدا شده (مثلاً ['breaking-bad-first-season', 'breaking-bad-second-season'])
    """
    url = f"{_BASE}/subtitles/searchbytitle"
    params = {"query": title}
    try:
        async with httpx.AsyncClient(http2=False, verify=False, follow_redirects=True, timeout=timeout) as client:
            r = await client.get(url, params=params, headers={
                "User-Agent": _USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
            })
            if r.status_code != 200:
                logger.warning("subf2m searchbytitle HTTP %d", r.status_code)
                return []
            soup = BeautifulSoup(r.text, "html.parser")
            slugs = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                # Match: /subtitles/{slug}
                m = re.match(r"/subtitles/([^/]+)$", href)
                if m:
                    slug = m.group(1)
                    if slug not in slugs:
                        slugs.append(slug)
            logger.info("subf2m searchbytitle '%s': %d slugs found", title, len(slugs))
            return slugs
    except Exception as e:
        logger.error("subf2m searchbytitle error: %s", e)
        return []


# تبدیل شماره فصل به عدد ترتیبی (انگلیسی)
_ORDINAL_SEASONS = {
    1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth",
    6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth",
    11: "eleventh", 12: "twelfth", 13: "thirteenth", 14: "fourteenth",
    15: "fifteenth",
}


async def get_subtitle_for_imdb(
    imdb_id: str,
    title: str,
    year: Optional[int] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    out_dir: Optional[str] = None,
) -> Optional[str]:
    """
    دریافت زیرنویس فارسی با استفاده از نام عنوان.

    Returns:
        مسیر فایل SRT UTF-8، یا None.
    """
    if out_dir is None:
        out_dir = tempfile.mkdtemp(prefix="subf2m_")

    base_slug = _slugify(title)
    candidates = [base_slug]
    if year:
        candidates.append(f"{base_slug}-{year}")
    # For series: try ordinal season slug (e.g. breaking-bad-first-season)
    if season:
        ordinal = _ORDINAL_SEASONS.get(season)
        if ordinal:
            candidates.append(f"{base_slug}-{ordinal}-season")
        candidates.append(f"{base_slug}-season-{season}")
        candidates.append(f"{base_slug}-s{season:02d}")
        candidates.append(f"{base_slug}-s{season}")

    # Search by title as fallback
    candidates_from_search = await _search_by_title(title)
    for slug in candidates_from_search:
        if slug not in candidates:
            candidates.append(slug)

    tried = set()
    for slug in candidates:
        if not slug or slug in tried:
            continue
        tried.add(slug)
        results = await search_persian_subtitle(slug)
        if not results:
            continue
        # If series and we have season/episode, try to find a version matching "SxxExx"
        if season and episode:
            target = f"S{season:02d}E{episode:02d}".lower()
            target_loose = f"S{season}E{episode}".lower()
            target_persian = f"قسمت {episode}"
            # Prefer version matching SxxExx
            for r in results:
                ver = r.get("version", "").lower()
                if target in ver or target_loose in ver or target_persian in ver:
                    logger.info("subf2m: matched S%02dE%02d in '%s'",
                               season, episode, r.get("version", "")[:50])
                    return await download_persian_subtitle(r["url"], out_dir)
        # Pick the most-downloaded one
        top = results[0]
        logger.info("subf2m: best match for '%s' = %s (downloads=%d)",
                    slug, top.get("version", "?")[:50], top.get("downloads", 0))
        return await download_persian_subtitle(top["url"], out_dir)
    return None


# ═══════════════════════════════════════════════════════════
#   Test / debug
# ═══════════════════════════════════════════════════════════

async def _test():
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    print("\n=== Test 1: search Inception subtitles ===")
    results = await search_persian_subtitle("inception", year=2010)
    print(f"Found {len(results)} subtitles")
    for i, s in enumerate(results[:5]):
        print(f"  [{i}] {s['version'][:60]:60s} | downloads={s['downloads']:5d} | fps={s['fps']}")

    if results:
        print("\n=== Test 2: download first subtitle ===")
        out_dir = "/tmp/test_subf2m"
        os.makedirs(out_dir, exist_ok=True)
        path = await download_persian_subtitle(results[0]["url"], out_dir)
        if path:
            print(f"✅ Saved: {path}")
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            print(f"Size: {len(content)} chars")
            print(f"First 500 chars:\n{content[:500]}")

    print("\n=== Test 3: get_subtitle_for_imdb (Breaking Bad S01E01) ===")
    path = await get_subtitle_for_imdb(
        imdb_id="tt0903747",
        title="Breaking Bad",
        season=1, episode=1,
        out_dir="/tmp/test_subf2m_bb",
    )
    if path:
        print(f"✅ Saved: {path}")
    else:
        print("❌ No subtitle found")


if __name__ == "__main__":
    asyncio.run(_test())
