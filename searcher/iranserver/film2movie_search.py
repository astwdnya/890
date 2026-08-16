"""
film2movie_search.py
────────────────────
سرچ و استخراج لینک‌های دانلود از film2movie.asia (آرشیو بزرگ فیلم و سریال فارسی).

سایت:
  - WordPress با REST API قابل دسترس (بدون Cloudflare)
  - از افزونه PDB (Persian Download Box) استفاده می‌کنه برای نمایش کیفیت‌ها و قسمت‌ها
  - همه فیلم‌ها/سریال‌ها هاردساب فارسی دارن (زیرنویس چسبیده فارسی)
  - کیفیت‌های 480p, 720p, 1080p (و بعضاً 360p, 240p, x265, 1080p Full HD)
  - آرشیو بسیار بزرگ فیلم و سریال (هر دو ایرانی و خارجی)

API ها:
  - Search: /wp-json/wp/v2/posts?search={query}&per_page={limit}
  - Categories: /wp-json/wp/v2/categories?per_page=100
  - Post detail: GET post.link (HTML page with PDB markup)

ساختار PDB (افزونه نمایش دانلود):
  - <div class="pdb-quality-content"> - کل باکس کیفیت
  - <div class="pdb-episodes-grid">   - گرید قسمت‌ها
  - <div class="pdb-episode-btn">     - هر قسمت
    - <span class="pdb-episode-text">قسمت 01</span>
    - <div class="pdb_links">
      - <a class="pdb_download" href="...">  - لینک دانلود مستقیم
      - <a class="pdb_play" href="...">      - لینک پخش آنلاین
  - برای فیلم‌ها: <div class="pdb-movie-links"> (ساختار مشابه بدون episode-text)

نام‌گذاری کیفیت‌ها:
  - 1080p, 720p, 480p, 360p, 240p
  - 1080p.HardSub (هاردساب فارسی)
  - 1080p.x265 (HEVC کم‌حجم)
  - 1080p.FullHD

توابع عمومی:
  - search_film2movie(query, limit=20) -> List[dict]
  - get_qualities_film2movie(post_url) -> List[dict]
  - get_episodes_film2movie(post_url) -> Dict[int, List[int]]
  - get_episode_links_film2movie(post_url, season, episode) -> List[dict]
  - download_film2movie(url, out_dir, ...) -> Optional[str]
"""

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Tuple
from urllib.parse import quote_plus, urlparse

from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup

logger = logging.getLogger("Film2Movie")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_BASE_URL = "https://film2movie.asia"

# کیفیت‌های قابل تشخیص (به ترتیب اولویت)
_QUALITY_PATTERNS = [
    (r"2160p|4k|uhd", "2160p"),
    (r"1080p\.fullhd|1080p\.full", "1080p FullHD"),
    (r"1080p\.x265|1080p\.hevc", "1080p x265"),
    (r"1080p", "1080p"),
    (r"720p\.x265|720p\.hevc", "720p x265"),
    (r"720p", "720p"),
    (r"480p", "480p"),
    (r"360p", "360p"),
    (r"240p", "240p"),
]


@dataclass
class Film2MovieTitle:
    """نتیجه سرچ film2movie"""
    id: str
    title: str
    url: str
    img: str = ""
    is_series: bool = False
    year: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = "tv" if self.is_series else "movie"
        return d


def _strip_html(text: str) -> str:
    """حذف تگ‌های HTML"""
    return re.sub(r"<[^>]+>", "", text).strip()


def _detect_quality(url: str, text: str = "") -> str:
    """تشخیص کیفیت از URL و متن"""
    combined = (url + " " + text).lower()
    for pattern, label in _QUALITY_PATTERNS:
        if re.search(pattern, combined):
            return label
    return "unknown"


def _detect_season_episode(url: str, text: str = "") -> Tuple[Optional[int], Optional[int]]:
    """تشخیص شماره فصل و قسمت از URL"""
    combined = url + " " + text
    # S01E01, S1E1, s01e01
    m = re.search(r"[sS](\d{1,2})[eE](\d{1,2})", combined)
    if m:
        return int(m.group(1)), int(m.group(2))
    # قسمت 01 (Persian)
    m = re.search(r"قسمت\s*(\d+)", text)
    if m:
        # season not in URL, fallback
        season_match = re.search(r"[sS](\d{1,2})", url)
        season = int(season_match.group(1)) if season_match else 1
        return season, int(m.group(1))
    return None, None


def _is_series_post(post: dict) -> bool:
    """تشخیص سریال بودن پست از title یا categories یا URL"""
    title = post.get("title", {}).get("rendered", "")
    link = post.get("link", "")
    # سریال keyword (Persian)
    if "سریال" in title or "دانلود سریال" in title:
        return True
    # اگر URL حاوی /tvshows/ نباشه اما categories نشون بده سریال
    cats = post.get("categories", [])
    # category 32134 (سریال) — but we don't always know IDs
    return False


async def search_film2movie(query: str, limit: int = 10) -> List[dict]:
    """
    سرچ در film2movie.asia با WordPress REST API.

    Returns:
        لیست dict با فیلدهای:
        - id, title, url, img, is_series, year, type
    """
    if not query or len(query.strip()) < 2:
        return []

    query = query.strip()
    search_url = f"{_BASE_URL}/wp-json/wp/v2/posts?search={quote_plus(query)}&per_page={limit}"

    try:
        async with AsyncSession() as s:
            r = await s.get(
                search_url,
                impersonate="chrome",
                timeout=10,
                headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            )
            if r.status_code != 200:
                logger.warning("film2movie search HTTP %d", r.status_code)
                return []

            posts = r.json()
            if not isinstance(posts, list):
                return []

            results = []
            for post in posts:
                post_id = str(post.get("id", ""))
                title_html = post.get("title", {}).get("rendered", "")
                title = _strip_html(title_html)
                link = post.get("link", "")
                # Featured image
                img = ""
                media = post.get("_embedded", {}).get("wp:featuredmedia", [])
                if media and isinstance(media, list) and len(media) > 0:
                    img = media[0].get("source_url", "")
                # Detect series: title contains "سریال"
                is_series = "سریال" in title or "دانلود سریال" in title
                # Year extraction
                year = ""
                m = re.search(r"\b(19\d{2}|20\d{2})\b", title)
                if m:
                    year = m.group(1)

                # Skip news posts (Articles without download links)
                # Heuristic: if title doesn't contain "دانلود" (download), skip
                if "دانلود" not in title and "Download" not in title.lower():
                    continue

                results.append({
                    "id": post_id,
                    "title": title,
                    "url": link,
                    "img": img,
                    "is_series": is_series,
                    "year": year,
                    "type": "tv" if is_series else "movie",
                })

            logger.info("film2movie search '%s': %d results", query, len(results))
            return results

    except Exception as e:
        logger.error("film2movie search error: %s", e)
        return []


async def get_qualities_film2movie(post_url: str) -> List[dict]:
    """
    گرفتن لیست کیفیت‌های موجود برای یک فیلم از film2movie.

    برای سریال‌ها، این تابع کیفیت‌های مشترک رو برمی‌گردونه
    (برای لیست قسمت‌ها از get_episodes_film2movie استفاده کنید).

    Returns:
        list of dicts:
        - label: "1080p", "720p", "480p"
        - url: لینک مستقیم دانلود
        - is_hardsub: آیا هاردساب فارسی هست
    """
    try:
        async with AsyncSession() as s:
            r = await s.get(
                post_url,
                impersonate="chrome",
                timeout=30,
                headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
            )
            if r.status_code != 200:
                return []

            soup = BeautifulSoup(r.text, "html.parser")
            qualities = []
            seen_urls = set()

            # Pattern 1: PDB movie links
            # <a class="pdb_download" href="...">
            dl_links = soup.select("a.pdb_download")
            for a in dl_links:
                href = a.get("href", "")
                if not href or href in seen_urls:
                    continue
                # Find quality context — parent or sibling text
                parent = a.find_parent(["div", "p", "li"])
                parent_text = parent.get_text(" ", strip=True) if parent else ""
                quality = _detect_quality(href, parent_text)
                is_hardsub = "hardsub" in href.lower() or "هاردساب" in parent_text
                seen_urls.add(href)
                qualities.append({
                    "label": quality,
                    "url": href,
                    "is_hardsub": is_hardsub,
                })

            # Pattern 2: Generic download links (no PDB plugin)
            if not qualities:
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if any(k in href.lower() for k in ["cdn.ftk.pw", "upera.tv", "metafilm.ir", "irdanlod"]):
                        if href in seen_urls:
                            continue
                        # Quality from URL
                        q_match = re.search(r"(2160p|1080p|720p|480p|360p|240p)", href, re.I)
                        if q_match:
                            quality = q_match.group(1).lower()
                        else:
                            quality = "?"
                        is_hardsub = "hardsub" in href.lower()
                        # Find context text
                        parent = a.find_parent(["div", "p", "li"])
                        parent_text = parent.get_text(" ", strip=True) if parent else ""
                        # Skip parts of multi-part RAR
                        if re.search(r"\.part\d+\.rar", href):
                            continue
                        seen_urls.add(href)
                        qualities.append({
                            "label": quality,
                            "url": href,
                            "is_hardsub": is_hardsub,
                        })

            # Deduplicate by quality label (keep first)
            seen_labels = set()
            unique = []
            for q in qualities:
                if q["label"] not in seen_labels:
                    seen_labels.add(q["label"])
                    unique.append(q)

            # Sort by quality (1080p first)
            quality_order = {"2160p": 0, "1080p fullhd": 1, "1080p x265": 2, "1080p": 3,
                             "720p x265": 4, "720p": 5, "480p": 6, "360p": 7, "240p": 8,
                             "unknown": 99}
            unique.sort(key=lambda q: quality_order.get(q["label"].lower(), 99))

            logger.info("film2movie qualities for %s: %d", post_url, len(unique))
            return unique

    except Exception as e:
        logger.error("film2movie get_qualities error: %s", e)
        return []


async def get_episodes_film2movie(post_url: str) -> Dict[int, List[int]]:
    """
    گرفتن لیست فصل‌ها و قسمت‌های یک سریال.

    Returns:
        dict به فرمت {1: [1, 2, 3, ...], 2: [1, 2, ...]}
    """
    try:
        async with AsyncSession() as s:
            r = await s.get(
                post_url,
                impersonate="chrome",
                timeout=30,
                headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
            )
            if r.status_code != 200:
                return {}

            soup = BeautifulSoup(r.text, "html.parser")
            episodes: Dict[int, set] = {}

            # Pattern: <span class="pdb-episode-text">قسمت 01</span>
            # Or direct download URLs with S01E01 pattern
            for a in soup.find_all("a", href=True):
                href = a["href"]
                # Find S/E in URL
                season, episode = _detect_season_episode(href, a.get_text(" ", strip=True))
                if season and episode:
                    if season not in episodes:
                        episodes[season] = set()
                    episodes[season].add(episode)

            # Convert to sorted lists
            result: Dict[int, List[int]] = {}
            for s in sorted(episodes.keys()):
                result[s] = sorted(episodes[s])

            logger.info("film2movie episodes for %s: %s", post_url,
                        {k: len(v) for k, v in result.items()})
            return result

    except Exception as e:
        logger.error("film2movie get_episodes error: %s", e)
        return {}


async def get_episode_links_film2movie(
    post_url: str,
    season: int,
    episode: int,
) -> List[dict]:
    """
    گرفتن لیست کیفیت‌های یک قسمت مشخص از سریال.

    Returns:
        list of dicts:
        - label: "1080p", "720p", "480p"
        - url: لینک مستقیم دانلود
        - is_hardsub: bool
    """
    try:
        async with AsyncSession() as s:
            r = await s.get(
                post_url,
                impersonate="chrome",
                timeout=30,
                headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
            )
            if r.status_code != 200:
                return []

            soup = BeautifulSoup(r.text, "html.parser")
            qualities = []
            seen_urls = set()

            # Find all episode download links matching this S/E
            target_ep = f"S{season:02d}E{episode:02d}"
            target_ep_loose = f"S{season}E{episode}"

            for a in soup.find_all("a", href=True):
                href = a["href"]
                # Match S01E01 pattern (case insensitive)
                if target_ep.lower() not in href.lower() and target_ep_loose.lower() not in href.lower():
                    continue
                if href in seen_urls:
                    continue
                # Must be a download URL
                if not any(k in href.lower() for k in ["cdn.ftk.pw", "upera", "metafilm", "irdanlod"]):
                    continue
                # Skip multi-part RARs
                if re.search(r"\.part\d+\.rar", href):
                    continue
                q_match = re.search(r"(2160p|1080p|720p|480p|360p|240p)", href, re.I)
                quality = q_match.group(1).lower() if q_match else "?"
                is_hardsub = "hardsub" in href.lower()
                # Also check if "HardSub" version exists separately (Black and white version etc)
                seen_urls.add(href)
                qualities.append({
                    "label": quality,
                    "url": href,
                    "is_hardsub": is_hardsub,
                    "variant": "bw" if "black.and.white" in href.lower() else "main",
                })

            # Sort: main variant first, then by quality
            quality_order = {"2160p": 0, "1080p": 1, "720p": 2, "480p": 3, "360p": 4, "240p": 5, "?": 99}
            qualities.sort(key=lambda q: (q.get("variant", "main") != "main", quality_order.get(q["label"], 99)))

            logger.info("film2movie episode links for S%02dE%02d: %d", season, episode, len(qualities))
            return qualities

    except Exception as e:
        logger.error("film2movie get_episode_links error: %s", e)
        return []


async def download_film2movie(
    url: str,
    out_dir: str,
    progress_cb=None,
) -> Optional[str]:
    """
    دانلود فایل از film2movie (لینک مستقیم cdn.ftk.pw یا upera).

    Returns:
        مسیر فایل دانلود شده، یا None در صورت خطا.
    """
    try:
        os.makedirs(out_dir, exist_ok=True)
        # Get filename from URL
        parsed = urlparse(url)
        filename = os.path.basename(parsed.path)
        if not filename:
            filename = f"film2movie_{int(time.time())}.mp4"
        # Clean filename
        filename = re.sub(r"[^\w.\-]", "_", filename)
        out_path = os.path.join(out_dir, filename)

        async with AsyncSession() as s:
            # First check redirect / get file size
            r = await s.head(url, impersonate="chrome", timeout=15,
                             headers={"User-Agent": _USER_AGENT, "Referer": _BASE_URL + "/"},
                             allow_redirects=True)
            total_size = int(r.headers.get("content-length", 0))
            content_type = r.headers.get("content-type", "")

            # If HEAD not allowed, GET with stream
            if r.status_code not in (200, 206) or not total_size:
                r = await s.get(url, impersonate="chrome", timeout=15,
                               headers={"User-Agent": _USER_AGENT, "Referer": _BASE_URL + "/"},
                               stream=True, allow_redirects=True)
                if r.status_code not in (200, 206):
                    logger.error("film2movie download HTTP %d for %s", r.status_code, url)
                    return None
                total_size = int(r.headers.get("content-length", 0))

            # Stream download
            downloaded = 0
            with open(out_path, "wb") as f:
                async for chunk in r.aiter_content(chunk_size=65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb:
                        try:
                            await progress_cb(downloaded, total_size) if asyncio.iscoroutinefunction(progress_cb) else progress_cb(downloaded, total_size)
                        except Exception:
                            pass

            logger.info("film2movie downloaded %s (%d bytes)", out_path, downloaded)
            return out_path

    except Exception as e:
        logger.error("film2movie download error: %s", e)
        return None


# ═══════════════════════════════════════════════════════════
#   Test / debug
# ═══════════════════════════════════════════════════════════

async def _test():
    """تست سریع."""
    import sys
    print("=== Test 1: search_film2movie('inception') ===")
    results = await search_film2movie("inception", limit=5)
    for r in results:
        print(f"  [{'📺' if r['is_series'] else '🎬'}] {r['title'][:60]} | {r['url']}")
        print(f"     year={r.get('year', '')} img={'yes' if r.get('img') else 'no'}")

    if results:
        # Find a movie result
        movie = next((r for r in results if not r["is_series"]), None)
        if movie:
            print(f"\n=== Test 2: get_qualities_film2movie('{movie['title'][:30]}') ===")
            qs = await get_qualities_film2movie(movie["url"])
            for q in qs:
                print(f"  [{q['label']:15s}] hardsub={q['is_hardsub']} | {q['url'][:90]}")

        # Find a series result
        series = next((r for r in results if r["is_series"]), None)
        if series:
            print(f"\n=== Test 3: get_episodes_film2movie('{series['title'][:30]}') ===")
            eps = await get_episodes_film2movie(series["url"])
            for s, eps_list in eps.items():
                print(f"  Season {s}: {len(eps_list)} episodes — {eps_list[:5]}...")
            if eps:
                s1 = sorted(eps.keys())[0]
                e1 = eps[s1][0]
                print(f"\n=== Test 4: get_episode_links_film2movie(S{s1:02d}E{e1:02d}) ===")
                links = await get_episode_links_film2movie(series["url"], s1, e1)
                for l in links:
                    print(f"  [{l['label']:8s}] hardsub={l['is_hardsub']} var={l['variant']} | {l['url'][:90]}")

    # Search series only
    print("\n=== Test 5: search_film2movie('breaking bad') ===")
    results = await search_film2movie("breaking bad", limit=10)
    for r in results:
        print(f"  [{'📺' if r['is_series'] else '🎬'}] {r['title'][:60]} | year={r.get('year','')} | {r['url'][:80]}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    asyncio.run(_test())
