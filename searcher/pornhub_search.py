"""
pornhub_search.py
─────────────────
سرچ ویدیو از PornHub با scraping.

استفاده:
  from pornhub_search import search_pornhub
  results = await search_pornhub("query", page=1, sort="mr")
"""

import asyncio
import logging
import re
from dataclasses import dataclass, asdict
from typing import List, Optional
from urllib.parse import quote_plus

import aiohttp
from aiohttp import ClientTimeout

logger = logging.getLogger("PornhubSearch")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}

_BASE_URL = "https://www.pornhub.com"


# ─── Data model ─────────────────────────────────────────────


@dataclass
class PornhubVideo:
    title: str
    url: str
    thumbnail: str
    duration: str
    views: str
    rating: str
    vkey: str
    hd: bool
    source: str = "pornhub"

    def to_dict(self) -> dict:
        return asdict(self)


__all__ = ["search_pornhub", "search_pornhub_multi_page", "PornhubVideo"]


# ─── Search ─────────────────────────────────────────────────


async def search_pornhub(
    query: str,
    page: int = 1,
    limit: int = 0,
    sort: str = "",
    hd: bool = False,
) -> List[dict]:
    """
    سرچ ویدیو از PornHub.

    Args:
        query: عبارت جستجو
        page: شماره صفحه (از 1)
        limit: حداکثر نتایج (0 = همه ویدیوهای صفحه)
        sort: مرتب‌سازی
            "" = مرتبط‌ترین (default)
            "mr" = جدیدترین
            "tr" = بهترین امتیاز
            "lg" = طولانی‌ترین
        hd: فقط HD

    Returns:
        لیست dict
    """
    if not query or len(query.strip()) < 2:
        return []

    query = query.strip()
    encoded = quote_plus(query)

    search_url = f"{_BASE_URL}/video/search?search={encoded}"

    if sort:
        search_url += f"&o={sort}"
    if hd:
        search_url += "&hd=1"
    if page > 1:
        search_url += f"&page={page}"

    logger.info("PornHub search: q='%s' page=%d sort='%s' hd=%s", query, page, sort, hd)

    html = await _fetch_page(search_url)
    if not html:
        return []

    results = _parse_search_results(html)

    if limit > 0 and len(results) > limit:
        results = results[:limit]

    logger.info("Found %d results for '%s'", len(results), query)
    return [r.to_dict() for r in results]


async def search_pornhub_multi_page(
    query: str,
    pages: int = 3,
    limit: int = 50,
    sort: str = "",
    hd: bool = False,
) -> List[dict]:
    """
    سرچ چند صفحه‌ای از PornHub (تا limit نتیجه).

    Args:
        query: عبارت جستجو
        pages: تعداد صفحات (از 1)
        limit: حداکثر کل نتایج
        sort: مرتب‌سازی
        hd: فقط HD
    """
    if not query or len(query.strip()) < 2:
        return []

    tasks = [
        search_pornhub(query, page=p, limit=limit, sort=sort, hd=hd)
        for p in range(1, pages + 1)
    ]
    all_results = await asyncio.gather(*tasks, return_exceptions=True)

    combined = []
    seen_ids = set()
    for page_results in all_results:
        if isinstance(page_results, Exception):
            logger.warning("Page search failed: %s", page_results)
            continue
        for video in page_results:
            vid = video.get("vkey", "")
            if vid and vid not in seen_ids:
                seen_ids.add(vid)
                combined.append(video)
            elif not vid:
                combined.append(video)

    return combined[:limit]


# ─── HTTP ───────────────────────────────────────────────────


async def _fetch_page(url: str) -> Optional[str]:
    """دریافت صفحه HTML."""
    # اول curl_cffi
    html = await _fetch_curl_cffi(url)
    if html:
        return html

    # fallback aiohttp
    timeout = ClientTimeout(total=20, connect=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                url, headers=_HEADERS, allow_redirects=True
            ) as resp:
                if resp.status == 200:
                    return await resp.text(errors="replace")
                logger.warning("HTTP %d for %s", resp.status, url)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("aiohttp fetch failed: %s", e)

    return None


async def _fetch_curl_cffi(url: str) -> Optional[str]:
    """دریافت با curl_cffi."""
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return None

    try:
        async with AsyncSession() as session:
            resp = await session.get(
                url,
                impersonate="chrome",
                timeout=20,
            )
            if resp.status_code == 200:
                return resp.text
    except Exception as e:
        logger.debug("curl_cffi failed: %s", e)

    return None


# ─── HTML Parsing ───────────────────────────────────────────


def _parse_search_results(html: str) -> List[PornhubVideo]:
    """پارس نتایج سرچ PornHub."""
    results = []
    seen_vkeys = set()

    # پیدا کردن همه vkey ها
    vkeys = re.findall(r'data-video-vkey=["\']([^"\']+)["\']', html)
    unique_vkeys = list(dict.fromkeys(vkeys))

    for vkey in unique_vkeys:
        if vkey in seen_vkeys:
            continue
        seen_vkeys.add(vkey)

        video = _extract_video_info(html, vkey)
        if video:
            results.append(video)

    return results


def _extract_video_info(html: str, vkey: str) -> Optional[PornhubVideo]:
    """استخراج اطلاعات یک ویدیو از HTML."""
    # پیدا کردن بلاک مربوط به این vkey
    # بلاک از pcVideoListItem شروع میشه
    pattern = rf'data-video-vkey="{re.escape(vkey)}"'
    idx = html.find(f'data-video-vkey="{vkey}"')
    if idx < 0:
        idx = html.find(f"data-video-vkey='{vkey}'")
    if idx < 0:
        return None

    # بلاک: 1000 کاراکتر قبل و 2000 بعد
    start = max(0, idx - 1000)
    end = min(len(html), idx + 2000)
    block = html[start:end]

    # Title
    title = ""
    # الگو 1: title attribute روی لینک view_video
    title_m = re.search(
        rf'href="/view_video\.php\?viewkey={re.escape(vkey)}"[^>]*title=["\']([^"\']+)["\']',
        block,
    )
    if not title_m:
        # الگو 2: title روی هر تگ نزدیک
        title_m = re.search(r'title=["\']([^"\']{10,})["\']', block)
    if not title_m:
        # الگو 3: alt attribute
        title_m = re.search(r'alt=["\']([^"\']{10,})["\']', block)
    if title_m:
        title = _clean_html(title_m.group(1))

    if not title:
        return None

    # URL
    url = f"{_BASE_URL}/view_video.php?viewkey={vkey}"

    # Thumbnail
    thumb = ""
    thumb_patterns = [
        rf'data-video-vkey="{re.escape(vkey)}"[^>]*data-thumb_url=["\']([^"\']+)["\']',
        r'data-thumb_url=["\']([^"\']+)["\']',
        r'data-src=["\']([^"\']+phncdn[^"\']+)["\']',
        r'src=["\']([^"\']*phncdn[^"\']*\.jpg[^"\']*)["\']',
    ]
    for tp in thumb_patterns:
        tm = re.search(tp, block)
        if tm:
            thumb = tm.group(1)
            break

    # Duration
    duration = ""
    dur_patterns = [
        r'class=["\'][^"\']*duration[^"\']*["\'][^>]*>([^<]+)<',
        r'<var\s+class=["\']duration["\'][^>]*>([^<]+)<',
        r'>(\d{1,2}:\d{2}(?::\d{2})?)<',
    ]
    for dp in dur_patterns:
        dm = re.search(dp, block)
        if dm:
            duration = dm.group(1).strip()
            break

    # Views
    views = ""
    views_patterns = [
        r'class=["\'][^"\']*views[^"\']*["\'][^>]*>.*?<var>([^<]+)</var>',
        r'<span class="views"[^>]*>([^<]+)<',
        r'(\d[\d,.]*[KkMm]?)\s*(?:views|Views)',
    ]
    for vp in views_patterns:
        vm = re.search(vp, block, re.DOTALL)
        if vm:
            views = vm.group(1).strip()
            break

    # Rating
    rating = ""
    rating_patterns = [
        r'class=["\'][^"\']*rating[^"\']*value[^"\']*["\'][^>]*>([^<]+)<',
        r'class=["\'][^"\']*value[^"\']*["\'][^>]*>(\d+%)<',
        r'<div class="rating[^"]*"[^>]*>.*?<var>([^<]+)</var>',
    ]
    for rp in rating_patterns:
        rm = re.search(rp, block, re.DOTALL)
        if rm:
            rating = rm.group(1).strip()
            break

    # HD
    hd = bool(re.search(r'class=["\'][^"\']*hd-thumbnail[^"\']*["\']', block, re.IGNORECASE))

    return PornhubVideo(
        title=title,
        url=url,
        thumbnail=thumb,
        duration=duration,
        views=views,
        rating=rating,
        vkey=vkey,
        hd=hd,
    )


def _clean_html(text: str) -> str:
    """تمیز کردن HTML entities."""
    text = text.replace("&#039;", "'")
    text = text.replace("&amp;", "&")
    text = text.replace("&quot;", '"')
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&#124;", "|")
    return text.strip()


# ─── Quick test ─────────────────────────────────────────────


async def _test():
    print("Testing PornHub search...\n")

    results = await search_pornhub("step sister", page=1, limit=5)
    print(f"Found {len(results)} results:\n")
    for i, v in enumerate(results):
        print(f"  [{i+1}] {v['title'][:70]}")
        print(f"      URL: {v['url']}")
        print(f"      Thumb: {v['thumbnail'][:80]}")
        print(f"      Duration: {v['duration']} | Views: {v['views']} | Rating: {v['rating']} | HD: {v['hd']}")
        print()

    # تست sort
    print("\n--- Most Recent ---")
    recent = await search_pornhub("step sister", sort="mr", limit=3)
    for v in recent:
        print(f"  {v['title'][:60]}")


if __name__ == "__main__":
    asyncio.run(_test())
