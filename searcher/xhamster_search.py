"""
xhamster_search.py
──────────────────
سرچ ویدیو از XHamster با scraping.

استفاده:
  from xhamster_search import search_xhamster
  results = await search_xhamster("query", page=1, limit=20, sort="newest")

Inline trigger: xh:query

مکانیزم سایت:
  - سرچ: /search/{query}
  - صفحه ۱ = بدون پارامتر؛ صفحه N = ?page=N  (کوئری‌استرینگی، یک-محور)
  - سورت: ?sort=newest|best|longest|views  (rating → 404 می‌دهد؛ «بهترین امتیاز» = best)
  - ترکیب: /search/{q}?page=2&sort=newest
  - هر آیتم: div.video-thumb با data-video-id و لینک /videos/{slug}-{xhID}
    عنوان روی title/aria-label لینک، مدت داخل thumb-image-container__duration،
    بازدید داخل video-thumb-views، تامبنیل xhcdn و data-previewvideo برای پیش‌نمایش
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, asdict
from typing import List, Optional
from urllib.parse import quote_plus

import aiohttp
from aiohttp import ClientTimeout

logger = logging.getLogger("XhamsterSearch")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}

_BASE_URL = "https://xhamster.com"

# مپ sort اینلاین → پارامتر sort سایت
_SORT_MAP = {
    "": "",
    "relevance": "",
    "new": "newest",
    "newest": "newest",
    "mr": "newest",
    "date": "newest",
    "top": "best",
    "best": "best",
    "rating": "best",
    "long": "longest",
    "longest": "longest",
    "lg": "longest",
    "views": "views",
    "mv": "views",
    "most": "views",
}


# ─── Data model ─────────────────────────────────────────────


@dataclass
class XhamsterVideo:
    """یک نتیجه سرچ XHamster."""

    title: str
    url: str
    thumbnail: str
    duration: str
    views: str
    quality: str
    video_id: str
    preview: str = ""
    uploader: str = ""
    source: str = "xhamster"

    def to_dict(self) -> dict:
        return asdict(self)


__all__ = ["search_xhamster", "search_xhamster_multi_page", "parse_inline_query", "XhamsterVideo"]


# ─── Inline query parser ────────────────────────────────────


def parse_inline_query(raw_query: str) -> dict:
    """
    پارس query اینلاین XHamster — همون سینتکس بقیه سرچرها.

    فرمت‌ها:
      step sis          → سرچ عادی، صفحه ۱
      step sis=2        → صفحه ۲
      step sis=new      → جدیدترین‌ها
      step sis=new=3    → جدیدترین‌ها صفحه ۳
      step sis=top      → بهترین امتیاز (sort=best)
      step sis=long     → طولانی‌ترین
      step sis=views    → بیشترین بازدید
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
        result["page"] = int(sort_val)
    elif sort_val in _SORT_MAP:
        result["sort"] = sort_val
        if page_val.isdigit():
            result["page"] = int(page_val)

    return result


# ─── Search ─────────────────────────────────────────────────


async def search_xhamster(
    query: str,
    page: int = 1,
    limit: int = 0,
    sort: str = "relevance",
) -> List[dict]:
    """
    سرچ ویدیو از XHamster.

    Args:
        query: عبارت جستجو (حداقل ۲ کاراکتر)
        page: شماره صفحه (از ۱ — صفحه ۱ بدون پارامتر page)
        limit: حداکثر نتایج (0 = همه ویدیوهای صفحه)
        sort: مرتب‌سازی
            "" / "relevance" = مرتبط‌ترین
            "new" / "newest" = جدیدترین
            "top" / "best" / "rating" = بهترین امتیاز (سایت: sort=best)
            "long" / "longest" = طولانی‌ترین
            "views" = بیشترین بازدید

    Returns:
        لیست dict با کلیدهای: title, url, thumbnail, duration, views,
        quality, video_id, preview, uploader, source
    """
    if not query or len(query.strip()) < 2:
        return []

    query = query.strip()
    encoded = quote_plus(query)

    search_url = f"{_BASE_URL}/search/{encoded}"

    sort_val = _SORT_MAP.get((sort or "").lower(), "")
    if sort_val:
        search_url += f"?sort={sort_val}"

    # صفحه ۱ = بدون پارامتر page؛ صفحه N = ?page=N (سایت یک-محوره)
    if page > 1:
        sep = "&" if "?" in search_url else "?"
        search_url += f"{sep}page={page}"

    logger.info(
        "XHamster search: q='%s' page=%d sort='%s' url=%s",
        query, page, sort, search_url,
    )

    html = await _fetch_page(search_url)
    if not html:
        return []

    results = _parse_search_results(html)

    if limit > 0 and len(results) > limit:
        results = results[:limit]

    logger.info("Found %d results for '%s' (page %d, sort %s)", len(results), query, page, sort)
    return [r.to_dict() for r in results]


async def search_xhamster_multi_page(
    query: str,
    pages: int = 3,
    limit: int = 50,
    sort: str = "relevance",
) -> List[dict]:
    """
    سرچ چند صفحه‌ای از XHamster (تا limit نتیجه).

    Args:
        query: عبارت جستجو
        pages: تعداد صفحات (از ۱)
        limit: حداکثر کل نتایج
        sort: مرتب‌سازی
    """
    if not query or len(query.strip()) < 2:
        return []

    tasks = [
        search_xhamster(query, page=p, limit=0, sort=sort)
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
            vid = video.get("video_id", "")
            if vid and vid not in seen_ids:
                seen_ids.add(vid)
                combined.append(video)
            elif not vid:
                combined.append(video)

    return combined[:limit]


# ─── HTTP ───────────────────────────────────────────────────


async def _fetch_page(url: str) -> Optional[str]:
    """دریافت صفحه HTML — اول curl_cffi (ضد آنتی‌بات)، بعد aiohttp."""
    html = await _fetch_curl_cffi(url)
    if html:
        return html

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
    """دریافت با curl_cffi با impersonate کروم."""
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


def _fmt_duration(seconds) -> str:
    """۹۰۵ → 15:05  |  3675 → 1:01:15"""
    try:
        s = int(seconds or 0)
    except (TypeError, ValueError):
        return ""
    if s <= 0:
        return ""
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def _fmt_views(views) -> str:
    """766566 → 766.6K  |  17600000 → 17.6M"""
    try:
        v = int(views or 0)
    except (TypeError, ValueError):
        return ""
    if v <= 0:
        return ""
    if v >= 1_000_000:
        val = v / 1_000_000
        return f"{val:.1f}M".replace(".0M", "M")
    if v >= 1_000:
        val = v / 1_000
        return f"{val:.1f}K".replace(".0K", "K")
    return str(v)


def _parse_initials(html: str) -> List[XhamsterVideo]:
    """
    پارس اولیه از window.initials — JSON کامل نتایج با تامبنیل، مدت،
    بازدید و آپلودر. این منبع اصلیه چون بعضی آیتم‌های HTML به‌صورت
    skeleton (لازی) رندر می‌شن و img/duration ندارن.
    """
    m = re.search(
        r'window\.initials\s*=\s*(\{.*?\})\s*;?\s*</script>',
        html,
        re.DOTALL,
    )
    if not m:
        return []

    txt = m.group(1)
    # مقادیر JS undefined رو قبل از json.loads خنثی کن
    txt = re.sub(r'(?<!["\w])(undefined)(?![\w"])', "null", txt)

    try:
        data = json.loads(txt)
    except json.JSONDecodeError:
        logger.warning("initials JSON parse failed")
        return []

    results = []
    seen_ids = set()

    def _walk(node):
        if isinstance(node, dict):
            page_url = node.get("pageURL")
            vid = node.get("id")
            title = node.get("title")
            if (
                isinstance(page_url, str)
                and "/videos/" in page_url
                and isinstance(vid, int)
                and vid > 0
                and vid not in seen_ids
                and isinstance(title, str)
                and title.strip()
                and node.get("videoType", "video") == "video"
            ):
                seen_ids.add(vid)
                landing = node.get("landing") or {}
                thumb = node.get("previewThumbURL") or node.get("thumbURL") or ""
                # previewThumbURL مثل thumbURL هست ولی .jpg (برای تلگرام امن‌تره)
                if thumb and thumb.endswith(".webp"):
                    thumb = node.get("thumbURL") or thumb
                results.append(XhamsterVideo(
                    title=_clean_html(title.strip()),
                    url=page_url,
                    thumbnail=thumb,
                    duration=_fmt_duration(node.get("duration")),
                    views=_fmt_views(node.get("views")),
                    quality="",
                    video_id=str(vid),
                    preview=node.get("trailerURL") or node.get("trailerFallbackUrl") or "",
                    uploader=str(landing.get("name") or "").strip(),
                ))
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)

    try:
        _walk(data)
    except Exception as e:
        logger.warning("initials walk failed: %s", e)

    return results


def _parse_search_results(html: str) -> List[XhamsterVideo]:
    """
    پارس نتایج سرچ XHamster.

    اول window.initials (JSON کامل)، اگه نشد fallback به regex روی بلاک‌های
    HTML (کلاس‌های container پسوند هش‌دار دارن، پس روی video-thumb /
    data-video-id مچ می‌زنیم):
      <div class="container-XXXX thumb-list__item video-thumb video-thumb--type-video"
           data-video-id="26241397" data-video-type="video">
        <a class="video-thumb__image-container ..." href="https://xhamster.com/videos/SLUG-xhxW2oe"
           data-previewvideo="...mp4" aria-label="TITLE">
          <img class="thumb-image-container__image" src="https://ic-vt-nss.xhcdn.com/.../1280x720.jpg">
          ... <div class="thumb-image-container__duration" ...>15:05</div> ...
        </a>
        <div class="video-thumb-info">
          <a class="... video-thumb-info__name ..." title="TITLE" href="...">TITLE</a>
          <div class="video-thumb-views">766.6K views</div>
        </div>
      </div>
    """
    # ۱) منبع اصلی: JSON توکار صفحه
    results = _parse_initials(html)
    if results:
        return results

    # ۲) Fallback: بلاک‌های HTML
    results = []
    seen_ids = set()

    # هر آیتم با data-video-id شروع می‌شه؛ بلاک تا آیتم بعدی برش خورده می‌شه
    starts = [
        (m.start(), m.group(1))
        for m in re.finditer(r'data-video-id="(\d+)"', html)
    ]
    for idx, (pos, vid_id) in enumerate(starts):
        if vid_id in seen_ids:
            continue

        end = starts[idx + 1][0] if idx + 1 < len(starts) else min(len(html), pos + 6000)
        block = html[pos:end]

        # لینک ویدیو — فقط /videos/ داخل دامنه خود سایت
        url_m = re.search(
            r'href="(https://(?:[a-z-]+\.)?xhamster\d*(?:\.[a-z.]+)?/videos/[^"]+)"',
            block,
        )
        if not url_m:
            continue
        url = url_m.group(1)

        video = _extract_video(vid_id, url, block)
        if video:
            seen_ids.add(vid_id)
            results.append(video)

    return results


def _extract_video(vid_id: str, url: str, block: str) -> Optional[XhamsterVideo]:
    """استخراج اطلاعات یک ویدیو از بلاک HTML."""

    # ─── Title ──────────────────────────────────────
    title = ""
    # الگو 1: title attribute روی لینک اسم (video-thumb-info__name)
    title_m = re.search(
        r'video-thumb-info__name[^>]*\btitle=["\']([^"\']+)["\']',
        block,
    )
    if not title_m:
        # الگو 2: aria-label لینک تامبنیل
        title_m = re.search(r'\baria-label=["\']([^"\']{10,})["\']', block)
    if not title_m:
        # الگو 3: title روی هر لینک /videos/
        title_m = re.search(
            r'href="https://[^"]*xhamster[^"]*/videos/[^"]*"[^>]*\btitle=["\']([^"\']+)["\']',
            block,
        )
    if title_m:
        title = _clean_html(title_m.group(1))

    if not title:
        return None

    # ─── Thumbnail ──────────────────────────────────
    thumbnail = ""
    thumb_m = re.search(
        r'(?:src|data-src)=["\'](https://[^"\']*xhcdn\.com/[^"\']+\.jpg)["\']',
        block,
    )
    if thumb_m:
        thumbnail = thumb_m.group(1)

    # ─── Preview video ──────────────────────────────
    preview = ""
    pvv_m = re.search(
        r'data-previewvideo=["\']([^"\']+)["\']',
        block,
    )
    if pvv_m:
        preview = pvv_m.group(1)

    # ─── Duration ───────────────────────────────────
    duration = ""
    dur_m = re.search(
        r'thumb-image-container__duration.*?>\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*<',
        block,
        re.DOTALL,
    )
    if not dur_m:
        # fallback: اولین الگوی زمانی داخل بلاک
        dur_m = re.search(r'>\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*<', block)
    if dur_m:
        duration = dur_m.group(1).strip()

    # ─── Views ──────────────────────────────────────
    views = ""
    views_m = re.search(
        r'video-thumb-views[^>]*>\s*([\d.,]+\s*[KkMm]?)\s*views',
        block,
    )
    if views_m:
        views = views_m.group(1).strip()
    else:
        views_m = re.search(r'([\d.,]+\s*[KkMm])\s*views', block)
        if views_m:
            views = views_m.group(1).strip()

    # ─── Quality (HD / 4K / VR badge) ───────────────
    quality = ""
    qual_m = re.search(
        r'>\s*(4K|HD|VR)\s*<',
        block,
    )
    if qual_m:
        quality = qual_m.group(1)

    # ─── Channel/Uploader ───────────────────────────
    uploader = ""
    up_m = re.search(
        r'video-uploader__name[^>]*>(?:<[^>]*>)*([^<]+)',
        block,
    )
    if up_m:
        uploader = up_m.group(1).strip()

    return XhamsterVideo(
        title=title,
        url=url,
        thumbnail=thumbnail,
        duration=duration,
        views=views,
        quality=quality,
        video_id=vid_id,
        preview=preview,
        uploader=uploader,
    )


def _clean_html(text: str) -> str:
    """تمیز کردن HTML entities."""
    text = text.replace("&#039;", "'")
    text = text.replace("&amp;", "&")
    text = text.replace("&quot;", '"')
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&#124;", "|")
    text = text.replace("&nbsp;", " ")
    return text.strip()


# ─── Quick test ─────────────────────────────────────────────


async def _test():
    import json as _json

    print("Testing XHamster search...\n")

    results = await search_xhamster("stepsis", page=1, limit=10)
    print(f"Found {len(results)} results:\n")
    for i, v in enumerate(results):
        print(f"  [{i+1}] {v['title'][:70]}")
        print(f"      URL: {v['url']}")
        print(f"      Thumb: {v['thumbnail'][:80]}")
        print(f"      Duration: {v['duration']} | Views: {v['views']} | Quality: {v['quality']}")
        print(f"      Uploader: {v['uploader']} | ID: {v['video_id']}")
        print()

    print("\n--- Newest ---")
    recent = await search_xhamster("stepsis", sort="newest", limit=3)
    for v in recent:
        print(f"  {v['title'][:60]} [{v['duration']}]")

    print("\n--- Best ---")
    best = await search_xhamster("stepsis", sort="best", limit=3)
    for v in best:
        print(f"  {v['title'][:60]} [{v['views']}]")

    print("\n--- Page 2 ---")
    p2 = await search_xhamster("stepsis", page=2, limit=3)
    for v in p2:
        print(f"  {v['title'][:60]}")

    print("\n--- Multi page (3) ---")
    mp = await search_xhamster_multi_page("stepsis", pages=3, limit=50)
    print(f"  combined: {len(mp)}")

    print("\n--- parse_inline_query ---")
    for q in ("stepsis", "stepsis=2", "stepsis=new", "stepsis=new=3", "stepsis=top", "stepsis=long"):
        print(f"  {q!r} -> {parse_inline_query(q)}")


if __name__ == "__main__":
    asyncio.run(_test())
