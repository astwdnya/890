"""
whoreshub_search.py
───────────────────
سرچ ویدیو از Whoreshub با scraping.

استفاده:
  from whoreshub_search import search_whoreshub
  results = await search_whoreshub("query", page=0, limit=20, sort="latest")

نکته مهم: Whoreshub از AJAX برای pagination/sort استفاده می‌کنه.
  URL: /search/{query}/?mode=async&function=get_block&block_id=list_videos_videos_list_search_result&q={query}&sort_by={sort}&from_videos+from_albums={page}

sort options:
  - relevance (default)
  - latest (post_date — جدیدترین)
  - views (video_viewed — بیشترین بازدید)
  - rating (rating — بهترین امتیاز)
  - duration (duration — طولانی‌ترین)
  - comments (most_commented)
  - favourites (most_favourited)

parse_inline_query:
  Sister           → سرچ عادی، صفحه 0
  Sister=2         → صفحه 2
  Sister=new        → جدیدترین‌ها
  Sister=new=3      → جدیدترین‌ها صفحه 3
  Sister=top        → بهترین امتیاز
  Sister=views      → بیشترین بازدید
  Sister=long       → طولانی‌ترین
"""

import asyncio
import logging
import re
from dataclasses import dataclass, asdict
from typing import List, Optional
from urllib.parse import quote_plus

import aiohttp
from aiohttp import ClientTimeout

logger = logging.getLogger("WhoresHubSearch")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "X-Requested-With": "XMLHttpRequest",
}

_BASE_URL = "https://www.whoreshub.com"
_BLOCK_ID = "list_videos_videos_list_search_result"
_RESULTS_PER_PAGE = 24


# ─── Sort mapping ───────────────────────────────────────────

_SORT_MAP = {
    "relevance": "",          # default (no sort_by)
    "latest": "post_date",
    "new": "post_date",
    "views": "video_viewed",
    "rating": "rating",
    "top": "rating",
    "duration": "duration",
    "long": "duration",
    "comments": "most_commented",
    "favourites": "most_favourited",
}


# ─── Data model ─────────────────────────────────────────────


@dataclass
class WhoresHubVideo:
    """یک نتیجه سرچ Whoreshub."""

    title: str
    url: str
    thumbnail: str
    duration: str
    video_id: str
    preview: str
    source: str = "whoreshub"

    def to_dict(self) -> dict:
        return asdict(self)


# ─── Search ─────────────────────────────────────────────────


async def search_whoreshub(
    query: str,
    page: int = 0,
    limit: int = 0,
    sort: str = "relevance",
) -> List[dict]:
    """
    سرچ ویدیو از Whoreshub.

    Args:
        query: عبارت جستجو
        page: شماره صفحه (0 = صفحه اول)
        limit: حداکثر تعداد نتایج (0 = همه ویدیوهای صفحه)
        sort: مرتب‌سازی (relevance, latest, views, rating, duration, comments, favourites)

    Returns:
        لیست dict با کلیدهای: title, url, thumbnail, duration, video_id, preview, source
    """
    if not query or len(query.strip()) < 2:
        return []

    query = query.strip()
    encoded = quote_plus(query)

    sort_by = _SORT_MAP.get(sort.lower(), "")

    params = f"mode=async&function=get_block&block_id={_BLOCK_ID}&q={encoded}"
    if sort_by:
        params += f"&sort_by={sort_by}"

    if page > 0:
        params += f"&from_videos={page + 1}&from_albums={page + 1}"

    search_url = f"{_BASE_URL}/search/{encoded}/?{params}"

    logger.info(
        "Whoreshub search: q='%s' page=%d sort=%s url=%s", query, page, sort, search_url[:120]
    )

    html = await _fetch_page(search_url, encoded)
    if not html:
        return []

    results = _parse_search_results(html)

    if limit > 0 and len(results) > limit:
        results = results[:limit]

    logger.info(
        "Found %d results for '%s' (page %d, sort %s)", len(results), query, page, sort
    )
    return [r.to_dict() for r in results]


async def search_whoreshub_multi_page(
    query: str,
    pages: int = 3,
    limit: int = 50,
    sort: str = "relevance",
) -> List[dict]:
    """
    سرچ چند صفحه‌ای از Whoreshub (تا limit نتیجه).

    Args:
        query: عبارت جستجو
        pages: تعداد صفحات (از 0)
        limit: حداکثر کل نتایج
        sort: مرتب‌سازی
    """
    if not query or len(query.strip()) < 2:
        return []

    tasks = [
        search_whoreshub(query, page=p, limit=limit, sort=sort)
        for p in range(pages)
    ]
    all_results = await asyncio.gather(*tasks, return_exceptions=True)

    combined = []
    seen_ids = set()
    for page_results in all_results:
        if isinstance(page_results, Exception):
            logger.warning("Page search failed: %s", page_results)
            continue
        for video in page_results:
            vid = video.get("video_id", "")
            if vid and vid not in seen_ids:
                seen_ids.add(vid)
                combined.append(video)
            elif not vid:
                combined.append(video)

    return combined[:limit]


# ─── Inline query parser ────────────────────────────────────


def parse_inline_query(raw_query: str) -> dict:
    """
    پارس query اینلاین Whoreshub.

    فرمت‌ها:
      Sister           → سرچ عادی، صفحه 0
      Sister=2         → صفحه 2
      Sister=new        → جدیدترین‌ها
      Sister=new=3      → جدیدترین‌ها صفحه 3
      Sister=top        → بهترین امتیاز
      Sister=views      → بیشترین بازدید
      Sister=long       → طولانی‌ترین
    """
    raw_query = raw_query.strip()

    result = {"query": raw_query, "page": 0, "sort": "relevance"}

    parts = raw_query.split("=")
    if len(parts) < 2:
        return result

    result["query"] = parts[0].strip()
    sort_val = parts[1].strip().lower() if len(parts) >= 2 else ""
    page_val = parts[2].strip() if len(parts) >= 3 else ""

    if sort_val.isdigit():
        # فقط شماره صفحه
        result["page"] = int(sort_val)
    elif sort_val in ("new", "latest"):
        result["sort"] = "latest"
        if page_val.isdigit():
            result["page"] = int(page_val)
    elif sort_val in ("top", "rating", "best"):
        result["sort"] = "rating"
        if page_val.isdigit():
            result["page"] = int(page_val)
    elif sort_val in ("views", "viewed"):
        result["sort"] = "views"
        if page_val.isdigit():
            result["page"] = int(page_val)
    elif sort_val in ("long", "duration"):
        result["sort"] = "duration"
        if page_val.isdigit():
            result["page"] = int(page_val)
    elif sort_val in ("comments", "commented"):
        result["sort"] = "comments"
        if page_val.isdigit():
            result["page"] = int(page_val)
    elif sort_val in ("favourites", "favorites", "faved"):
        result["sort"] = "favourites"
        if page_val.isdigit():
            result["page"] = int(page_val)

    return result


# ─── HTTP ───────────────────────────────────────────────────


async def _fetch_page(url: str, query: str = "") -> Optional[str]:
    """دریافت صفحه HTML با AJAX headers."""
    timeout = ClientTimeout(total=15, connect=10)

    headers = dict(_HEADERS)
    if query:
        headers["Referer"] = f"{_BASE_URL}/search/{quote_plus(query)}/"

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers, allow_redirects=True) as resp:
                if resp.status == 200:
                    return await resp.text(errors="replace")
                logger.warning("HTTP %d for %s", resp.status, url[:100])
                return None
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("Fetch failed for %s: %s", url[:80], e)
        return None


# ─── HTML Parsing ───────────────────────────────────────────


def _parse_search_results(html: str) -> List[WhoresHubVideo]:
    """
    پارس نتایج سرچ از HTML.

    ساختار Whoreshub:
      <a class="item" href="https://www.whoreshub.com/videos/ID/..." title="Video Title">
        <span class="thumb-img">
          <img class="img lazyload" src="..." data-src="//wh.cdntrex.com/.../1.jpg"
               alt="..." data-preview="https://www.whoreshub.com/get_file/.../preview.mp4/" />
        </span>
        <span class="duration">12:42</span>
      </a>
    """
    results = []
    seen_ids = set()

    # پیدا کردن همه video items
    # Pattern: <a class="item" href="URL/videos/ID/..." title="TITLE" ...> ... data-src="THUMB" ... data-preview="PREVIEW" ... </a>
    # با DOTALL برای multiline match
    item_pattern = re.compile(
        r'<a\s+class="item"\s+href="(https?://[^"]+/videos/(\d+)/[^"]+)"\s+title="([^"]*)"[^>]*>'
        r'.*?data-src="([^"]*)"[^>]*?'
        r'(?:data-preview="([^"]*)")?',
        re.DOTALL | re.IGNORECASE,
    )

    for m in item_pattern.finditer(html):
        url = m.group(1)
        video_id = m.group(2)
        title = m.group(3)
        thumbnail = m.group(4)
        preview = m.group(5) or ""

        if video_id in seen_ids:
            continue
        seen_ids.add(video_id)

        # Clean thumbnail URL
        if thumbnail and thumbnail.startswith("//"):
            thumbnail = "https:" + thumbnail

        # Find duration near this item
        # Search in the surrounding HTML
        pos = m.end()
        # Look for duration within next 500 chars
        search_area = html[pos:pos + 500]
        dur_match = re.search(r'class="duration[^"]*"[^>]*>([^<]+)<', search_area, re.IGNORECASE)
        duration = dur_match.group(1).strip() if dur_match else ""

        # Clean title
        title = _clean_title(title)

        results.append(WhoresHubVideo(
            title=title,
            url=url,
            thumbnail=thumbnail,
            duration=duration,
            video_id=video_id,
            preview=preview,
        ))

    # اگر regex بالا چیزی پیدا نکرد، fallback ساده‌تر
    if not results:
        results = _parse_fallback(html)

    return results


def _parse_fallback(html: str) -> List[WhoresHubVideo]:
    """Fallback: استخراج ساده‌تر."""
    results = []
    seen_ids = set()

    # پیدا کردن همه /videos/ID/ links
    for m in re.finditer(
        r'href="(https?://[^"]+/videos/(\d+)/[^"]+)"[^>]*title="([^"]*)"',
        html, re.IGNORECASE,
    ):
        url = m.group(1)
        video_id = m.group(2)
        title = m.group(3)

        if video_id in seen_ids:
            continue
        seen_ids.add(video_id)

        # Find thumbnail near
        pos = m.start()
        search_before = html[max(0, pos - 500):pos]
        thumb_m = re.search(r'data-src="([^"]*cdntrex[^"]*)"', search_before, re.IGNORECASE)
        thumbnail = thumb_m.group(1) if thumb_m else ""
        if thumbnail and thumbnail.startswith("//"):
            thumbnail = "https:" + thumbnail

        # Find duration near
        search_after = html[m.end():m.end() + 500]
        dur_m = re.search(r'class="duration[^"]*"[^>]*>([^<]+)<', search_after, re.IGNORECASE)
        duration = dur_m.group(1).strip() if dur_m else ""

        # Find preview
        preview_m = re.search(r'data-preview="([^"]*)"', search_after, re.IGNORECASE)
        preview = preview_m.group(1) if preview_m else ""

        results.append(WhoresHubVideo(
            title=_clean_title(title),
            url=url,
            thumbnail=thumbnail,
            duration=duration,
            video_id=video_id,
            preview=preview,
        ))

    return results


def _clean_title(title: str) -> str:
    """تمیز کردن عنوان."""
    title = title.replace("&#039;", "'")
    title = title.replace("&amp;", "&")
    title = title.replace("&quot;", '"')
    title = title.replace("&lt;", "<")
    title = title.replace("&gt;", ">")
    title = title.replace("&#x27;", "'")
    return title.strip()


# ─── Quick test ─────────────────────────────────────────────


async def _test():
    """تست سریع."""
    print("Testing Whoreshub search...")

    # Test relevance
    print("\n--- Relevance (default) ---")
    results = await search_whoreshub("Sister", limit=5)
    print(f"\nFound {len(results)} results:\n")
    for i, v in enumerate(results):
        print(f"  [{i + 1}] {v['title'][:70]}")
        print(f"      URL: {v['url'][:80]}")
        print(f"      Thumb: {v['thumbnail'][:80]}")
        print(f"      Duration: {v['duration']} | ID: {v['video_id']}")
        print()

    # Test latest sort
    print("\n--- Latest ---")
    results2 = await search_whoreshub("Sister", limit=5, sort="latest")
    for i, v in enumerate(results2):
        print(f"  [{i + 1}] {v['title'][:70]} | {v['duration']}")

    # Test page 2
    print("\n--- Page 2 ---")
    results3 = await search_whoreshub("Sister", limit=5, page=1)
    for i, v in enumerate(results3):
        print(f"  [{i + 1}] {v['title'][:70]} | {v['duration']}")

    # Test inline query parser
    print("\n--- Inline query parser ---")
    tests = [
        "Sister",
        "Sister=2",
        "Sister=new",
        "Sister=new=3",
        "Sister=top",
        "Sister=views",
        "Sister=long",
    ]
    for t in tests:
        parsed = parse_inline_query(t)
        print(f"  '{t}' → query='{parsed['query']}' page={parsed['page']} sort='{parsed['sort']}'")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    asyncio.run(_test())
