"""
farsiland_search.py
───────────────────
سرچ و استخراج اطلاعات فیلم/سریال از farsiland.com (آرشیو فیلم‌های ایرانی).

سایت از DooPlay (قالب وردپرس) استفاده می‌کنه و پشت Cloudflare هست.
API ها:
  - Search: /wp-json/dooplay/search/?keyword={query}&nonce={nonce}
  - Player: admin-ajax.php action=doo_player_ajax post_id={id} nonce={nonce} type={movie|tv}
  - TV Show: /tvshows/{slug}/ — شامل لیست فصل‌ها و قسمت‌ها
  - Episode: /episodes/{slug}-season-{s}-episodes-{e}/

توابع عمومی:
  - search_farsiland(query, limit=20) -> List[dict]
  - get_tv_seasons(slug) -> Optional[dict]
  - get_episode_post_id(episode_url) -> Optional[str]
  - get_player_embed(post_id, nonce) -> Optional[str]
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict
from urllib.parse import quote_plus, urljoin

from curl_cffi.requests import AsyncSession

logger = logging.getLogger("FarsilandSearch")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_BASE_URL = "https://farsiland.com"
_PLAYER_URL = "https://flnd.buzz"


@dataclass
class FarsilandTitle:
    """نتیجه سرچ farsiland"""
    id: str
    title: str
    url: str
    img: str = ""
    year: str = ""
    imdb: str = ""
    is_series: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = "tv" if self.is_series else "movie"
        return d


async def _get_nonce() -> str:
    """گرفتن nonce از صفحه اصلی farsiland."""
    try:
        async with AsyncSession() as s:
            r = await s.get(
                f"{_BASE_URL}/",
                impersonate="chrome",
                timeout=20,
                headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
            )
            if r.status_code != 200:
                logger.warning("farsiland nonce fetch HTTP %d", r.status_code)
                return ""

            # Find nonce in JavaScript
            m = re.search(r'"nonce"\s*:\s*"([^"]+)"', r.text)
            if m:
                logger.info("farsiland nonce: %s", m.group(1))
                return m.group(1)
            return ""
    except Exception as e:
        logger.warning("farsiland nonce fetch failed: %s", e)
        return ""


async def search_farsiland(query: str, limit: int = 20) -> List[dict]:
    """
    سرچ در farsiland.com با استفاده از DooPlay search API.

    Returns:
        لیست dict با فیلدهای:
        - id, title, url, img, year, imdb, is_series, type
    """
    if not query or len(query.strip()) < 2:
        return []

    query = query.strip()
    nonce = await _get_nonce()
    if not nonce:
        logger.error("Cannot get farsiland nonce")
        return []

    search_url = f"{_BASE_URL}/wp-json/dooplay/search/?keyword={quote_plus(query)}&nonce={nonce}"

    try:
        async with AsyncSession() as s:
            r = await s.get(
                search_url,
                impersonate="chrome",
                timeout=20,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept": "application/json",
                    "Referer": f"{_BASE_URL}/",
                },
            )
            if r.status_code != 200:
                logger.warning("farsiland search HTTP %d", r.status_code)
                return []

            data = r.json()
            if not isinstance(data, dict) or "error" in data:
                logger.info("farsiland search: %s", data.get("title", "no results"))
                return []

            results = []
            for post_id, item in data.items():
                if not isinstance(item, dict):
                    continue
                title = item.get("title", "")
                url = item.get("url", "")
                img = item.get("img", "")
                extra = item.get("extra", {})
                year = extra.get("date", "") if isinstance(extra, dict) else ""
                imdb = extra.get("imdb", "") if isinstance(extra, dict) else ""

                is_series = "/tvshows/" in url

                t = FarsilandTitle(
                    id=post_id,
                    title=title,
                    url=url,
                    img=img,
                    year=str(year) if year else "",
                    imdb=str(imdb) if imdb else "",
                    is_series=is_series,
                )
                results.append(t.to_dict())

                if len(results) >= limit:
                    break

            logger.info("farsiland search q='%s' -> %d results", query, len(results))
            return results
    except Exception as e:
        logger.warning("farsiland search failed: %s", e)
        return []


async def get_tv_seasons(show_url: str) -> Optional[dict]:
    """
    گرفتن لیست فصل/قسمت سریال از صفحه TV show.

    Args:
        show_url: URL صفحه سریال (مثلا https://farsiland.com/tvshows/on-the-side-lines/)

    Returns:
        dict با فیلدهای:
        - title: عنوان سریال
        - seasons: {season_num: [{episode: num, url: str, title: str}]}
        - total_seasons: int
    """
    try:
        async with AsyncSession() as s:
            r = await s.get(
                show_url,
                impersonate="chrome",
                timeout=20,
                headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
            )
            if r.status_code != 200:
                logger.warning("farsiland TV show HTTP %d", r.status_code)
                return None

            html = r.text

            # Find title
            title_match = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.DOTALL)
            title = title_match.group(1).strip() if title_match else ""

            # Find all episode links
            # Pattern: /episodes/{slug}-season-{s}-episodes-{e}/
            episode_links = re.findall(
                r'href="(https?://farsiland\.com/episodes/[^"]+)"[^>]*>(?:.*?)(?:Episode|قسمت)\s*(\d+)',
                html,
                re.DOTALL | re.IGNORECASE,
            )

            if not episode_links:
                # Try another pattern - just find episode URLs
                episode_links_raw = re.findall(
                    r'href="(https?://farsiland\.com/episodes/[^"]+)"',
                    html,
                    re.IGNORECASE,
                )
                episode_links = [(url, str(i + 1)) for i, url in enumerate(episode_links_raw)]

            # Parse season numbers from URLs
            seasons: Dict[int, List[dict]] = {}
            for url, ep_text in episode_links:
                # Extract season and episode from URL
                # Pattern: /episodes/{slug}-season-{s}-episodes-{e}/
                m = re.search(r"season-(\d+)-episodes-(\d+)", url, re.IGNORECASE)
                if m:
                    s_num = int(m.group(1))
                    e_num = int(m.group(2))
                else:
                    # Try to extract from URL slug
                    m2 = re.search(r"-s(\d+)-e(\d+)", url, re.IGNORECASE)
                    if m2:
                        s_num = int(m2.group(1))
                        e_num = int(m2.group(2))
                    else:
                        # Default to season 1, episode number from text
                        s_num = 1
                        try:
                            e_num = int(ep_text)
                        except ValueError:
                            e_num = 0

                if e_num == 0:
                    continue

                if s_num not in seasons:
                    seasons[s_num] = []

                # Avoid duplicates
                existing = [e for e in seasons[s_num] if e["episode"] == e_num]
                if not existing:
                    seasons[s_num].append({
                        "episode": e_num,
                        "url": url,
                        "title": f"Episode {e_num}",
                    })

            # Sort episodes
            for s in seasons:
                seasons[s].sort(key=lambda x: x["episode"])

            # Sort seasons descending (newest first)
            seasons = dict(sorted(seasons.items(), key=lambda x: -x[0]))

            result = {
                "title": title,
                "seasons": {str(k): v for k, v in seasons.items()},
                "total_seasons": len(seasons),
            }
            logger.info("farsiland TV show %s -> %d seasons", show_url, len(seasons))
            return result
    except Exception as e:
        logger.warning("farsiland TV show failed: %s", e)
        return None


async def get_post_id_from_page(url: str) -> Optional[str]:
    """
    گرفتن post ID از یک صفحه farsiland.
    """
    try:
        async with AsyncSession() as s:
            r = await s.get(
                url,
                impersonate="chrome",
                timeout=20,
                headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
            )
            if r.status_code != 200:
                return None

            # Find post ID from body class
            m = re.search(r"postid-(\d+)", r.text)
            if m:
                return m.group(1)

            # Try data-post attribute
            m2 = re.search(r'data-post="(\d+)"', r.text)
            if m2:
                return m2.group(1)

            return None
    except Exception as e:
        logger.warning("farsiland post ID fetch failed: %s", e)
        return None


async def get_player_embed(post_id: str, content_type: str = "movie") -> Optional[str]:
    """
    گرفتن embed URL از DooPlay player API.

    Args:
        post_id: WordPress post ID
        content_type: "movie" یا "tv"

    Returns:
        embed URL یا None
    """
    nonce = await _get_nonce()
    if not nonce:
        return None

    try:
        async with AsyncSession() as s:
            r = await s.post(
                f"{_BASE_URL}/wp-admin/admin-ajax.php",
                impersonate="chrome",
                timeout=15,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept": "application/json",
                    "Referer": f"{_BASE_URL}/",
                    "X-Requested-With": "XMLHttpRequest",
                },
                data={
                    "action": "doo_player_ajax",
                    "post_id": post_id,
                    "nonce": nonce,
                    "type": content_type,
                },
            )
            if r.status_code != 200:
                logger.warning("farsiland player API HTTP %d", r.status_code)
                return None

            data = r.json()
            embed_url = data.get("embed_url", "")
            if embed_url:
                logger.info("farsiland player %s -> %s", post_id, embed_url[:80])
                return embed_url
            logger.warning("farsiland player %s: no embed_url", post_id)
            return None
    except Exception as e:
        logger.warning("farsiland player API failed: %s", e)
        return None


async def extract_m3u8_from_embed(embed_url: str) -> Optional[dict]:
    """
    استخراج m3u8 URL از صفحه embed پلیر.

    Returns:
        dict با فیلدهای:
        - url: m3u8 URL
        - qualities: list of {label, url}
    """
    try:
        async with AsyncSession() as s:
            r = await s.get(
                embed_url,
                impersonate="chrome",
                timeout=20,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept": "text/html",
                    "Referer": f"{_BASE_URL}/",
                },
            )
            if r.status_code != 200:
                return None

            html = r.text

            # Look for m3u8 URLs
            m3u8_urls = re.findall(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', html)

            # Look for quality options
            qualities = []
            # Common patterns: data-quality="720", quality="720p", etc.
            quality_matches = re.findall(
                r'data-(?:quality|resolution)="(\d+)"[^>]*data-(?:src|url)="([^"]+)"',
                html,
                re.IGNORECASE,
            )
            if not quality_matches:
                quality_matches = re.findall(
                    r'(?:quality|res)["\']?\s*[:=]\s*["\']?(\d+)[^"]*["\']\s*[,}].*?(?:src|url)["\']?\s*[:=]\s*["\']([^"\']+)',
                    html,
                    re.IGNORECASE | re.DOTALL,
                )

            for q, url in quality_matches:
                qualities.append({"label": f"{q}p", "url": url})

            # If no quality-specific URLs, use the first m3u8
            if not qualities and m3u8_urls:
                qualities = [{"label": "Auto", "url": m3u8_urls[0]}]

            if qualities:
                return {
                    "url": qualities[0]["url"],
                    "qualities": qualities,
                }

            # Look for video source tags
            sources = re.findall(r'<source[^>]*src="([^"]+)"', html, re.IGNORECASE)
            if sources:
                return {
                    "url": sources[0],
                    "qualities": [{"label": "Auto", "url": s} for s in sources],
                }

            return None
    except Exception as e:
        logger.warning("farsiland embed extraction failed: %s", e)
        return None


# ─── Quick test ─────────────────────────────────────────────


async def _test():
    print("=== Test search ===")
    results = await search_farsiland("در حاشیه")
    for r in results[:5]:
        print(f"  [{r['id']}] {r['title']} ({r['year']}) - {r['url']}")


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    asyncio.run(_test())
