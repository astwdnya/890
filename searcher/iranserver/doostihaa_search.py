"""
doostihaa_search.py
───────────────────
سرچ و استخراج لینک‌های دانلود از doostihaa.com (آرشیو فیلم و سریال ایرانی).

سایت WordPress است و API رایگان داره. بدون Cloudflare قابل دسترس است.

API: /wp-json/wp/v2/posts?search={query}&per_page={limit}

ساختار:
  - فیلم: 3 لینک دانلود مستقیم (1080p, 720p, 480p) از irdanlod.ir
  - سریال: چندین قسمت، هر قسمت 3 کیفیت
  - عکس: از _embedded wp:featuredmedia

توابع عمومی:
  - search_doostihaa(query, limit=20) -> List[dict]
  - get_qualities_doostihaa(post_id) -> List[dict]
  - download_doostihaa(url, out_dir) -> Optional[str]
"""

import asyncio
import logging
import os
import re
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict
from urllib.parse import quote_plus

from curl_cffi.requests import AsyncSession

logger = logging.getLogger("DoostihaaSearch")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_BASE_URL = "https://www.doostihaa.com"


@dataclass
class DoostihaaTitle:
    """نتیجه سرچ doostihaa"""
    id: str
    title: str
    url: str
    img: str = ""
    is_series: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = "tv" if self.is_series else "movie"
        return d


async def search_doostihaa(query: str, limit: int = 20) -> List[dict]:
    """
    سرچ در doostihaa.com با استفاده از WordPress REST API.

    Returns:
        لیست dict با فیلدهای:
        - id, title, url, img, is_series, type
    """
    if not query or len(query.strip()) < 2:
        return []

    query = query.strip()
    # فقط 5 پست برای سرعت بیشتر — تلگرام query رو بعد از ~10s منقضی می‌کنه
    search_url = f"{_BASE_URL}/wp-json/wp/v2/posts?search={quote_plus(query)}&per_page=5"

    try:
        async with AsyncSession() as s:
            r = await s.get(
                search_url,
                impersonate="chrome",
                timeout=8,  # timeout کوتاه برای جلوگیری از منقضی شدن query تلگرام
                headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            )
            if r.status_code != 200:
                logger.warning("doostihaa search HTTP %d", r.status_code)
                return []

            posts = r.json()
            results = []

            for post in posts:
                pid = str(post.get("id", ""))
                title_html = post.get("title", {}).get("rendered", "")
                # Remove HTML tags from title
                title = re.sub(r"<[^>]+>", "", title_html).strip()
                url = post.get("link", "")
                content = post.get("content", {}).get("rendered", "")

                # Check if it's a series
                is_series = bool(re.search(r"سریال|قسمت|E\d+|S\d+E\d+", title + content, re.IGNORECASE))

                # Find download links
                dl_links = re.findall(r'href="(https?://[^"]*(?:irdanlod|dl)[^"]*)"', content, re.IGNORECASE)

                # Only include if it has download links
                if not dl_links:
                    continue

                # Get featured image from _embedded (بدون درخواست جداگانه)
                img = ""
                embedded = post.get("_embedded", {})
                featured_media = embedded.get("wp:featuredmedia", [])
                if featured_media and isinstance(featured_media, list) and len(featured_media) > 0:
                    img = featured_media[0].get("source_url", "")

                # Also try to find image in content
                if not img:
                    img_match = re.search(r'<img[^>]*src="([^"]*(?:uploads|img)[^"]*)"', content, re.IGNORECASE)
                    if img_match:
                        img = img_match.group(1)

                t = DoostihaaTitle(
                    id=pid,
                    title=title,
                    url=url,
                    img=img,
                    is_series=is_series,
                )
                results.append(t.to_dict())

                if len(results) >= limit:
                    break

            logger.info("doostihaa search q='%s' -> %d results", query, len(results))
            return results
    except Exception as e:
        logger.warning("doostihaa search failed: %s", e)
        return []


async def get_qualities_doostihaa(post_id: str) -> List[dict]:
    """
    گرفتن لیست کیفیت‌ها و لینک‌های دانلود برای یک پست.

    برای فیلم: لیست کیفیت‌ها (1080p, 720p, 480p)
    برای سریال: لیست قسمت‌ها، هر قسمت با کیفیت‌های خودش

    Returns:
        لیست dict با فیلدهای:
        - label: "1080p" یا "S01E01 1080p"
        - url: لینک مستقیم دانلود
        - episode: شماره قسمت (برای سریال) یا None
        - quality: "1080p", "720p", "480p"
    """
    try:
        async with AsyncSession() as s:
            r = await s.get(
                f"{_BASE_URL}/wp-json/wp/v2/posts/{post_id}",
                impersonate="chrome",
                timeout=30,
                headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            )
            if r.status_code != 200:
                logger.warning("doostihaa post %s HTTP %d", post_id, r.status_code)
                return []

            post = r.json()
            content = post.get("content", {}).get("rendered", "")

            # Find all download links
            dl_links = re.findall(r'href="(https?://[^"]*(?:irdanlod|dl)[^"]*)"', content, re.IGNORECASE)

            if not dl_links:
                return []

            # Parse links into qualities/episodes
            qualities = []

            for url in dl_links:
                # Extract quality
                qual_m = re.search(r"(\d{3,4}p)", url)
                quality = qual_m.group(1) if qual_m else "unknown"

                # Extract episode number (for series)
                ep_m = re.search(r"S\d+E(\d+)", url, re.IGNORECASE)
                episode = int(ep_m.group(1)) if ep_m else None

                # Build label
                if episode:
                    label = f"E{episode:02d} {quality}"
                else:
                    label = quality

                qualities.append({
                    "label": label,
                    "url": url,
                    "episode": episode,
                    "quality": quality,
                    "is_auto": False,
                })

            # Sort: episodes first, then by quality
            qualities.sort(key=lambda x: (x["episode"] or 999, x["quality"]))

            logger.info("doostihaa post %s -> %d qualities", post_id, len(qualities))
            return qualities
    except Exception as e:
        logger.warning("doostihaa qualities failed: %s", e)
        return []


async def download_doostihaa(url: str, out_dir: str, progress_cb=None) -> Optional[str]:
    """
    دانلود فایل مستقیم از doostihaa (MKV/MP4 از irdanlod.ir).

    Returns:
        مسیر فایل دانلود شده یا None
    """
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{int(asyncio.get_event_loop().time())}.mkv")

    try:
        async with AsyncSession() as s:
            r = await s.get(
                url,
                impersonate="chrome",
                timeout=600,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Referer": f"{_BASE_URL}/",
                },
            )
            if r.status_code != 200:
                logger.warning("doostihaa download HTTP %d", r.status_code)
                return None

            with open(out_path, "wb") as f:
                f.write(r.content)

            if progress_cb:
                try:
                    progress_cb(os.path.getsize(out_path), os.path.getsize(out_path))
                except Exception:
                    pass

            logger.info("doostihaa download complete: %s (%.1f MB)",
                        out_path, os.path.getsize(out_path) / 1024 / 1024)
            return out_path
    except Exception as e:
        logger.warning("doostihaa download failed: %s", e)
        return None


# ─── Quick test ─────────────────────────────────────────────


async def _test():
    print("=== Test search ===")
    results = await search_doostihaa("در حاشیه", limit=5)
    for r in results[:3]:
        print(f"  [{r['id']}] {r['title']} - {r['url']}")

    if results:
        print("\n=== Test qualities ===")
        qs = await get_qualities_doostihaa(results[0]["id"])
        for q in qs[:5]:
            print(f"  {q['label']} → {q['url'][:80]}")


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    asyncio.run(_test())
