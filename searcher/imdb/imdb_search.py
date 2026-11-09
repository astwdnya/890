"""
imdb_search.py
──────────────
سرچ و اطلاعات فیلم/سریال از IMDB بدون Playwright.

استفاده:
  from imdb_search import search_imdb, get_title_info, get_tv_episodes

  results = await search_imdb("the drama")
  info = await get_title_info("tt33071426")
  eps = await get_tv_episodes("tt0944947")  # سریال
"""
import asyncio
import logging
import re
from dataclasses import dataclass, asdict, field
from typing import List, Optional, Dict
from urllib.parse import quote_plus

import httpx

logger = logging.getLogger("ImdbSearch")

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.imdb.com/",
    "Origin": "https://www.imdb.com",
}

_GRAPHQL_HEADERS = dict(_HEADERS)
_GRAPHQL_HEADERS.update({
    "Content-Type": "application/json",
    "x-imdb-client-name": "imdb-web-next",
    "x-imdb-client-version": "1.0.0",
})


# ─── Data models ────────────────────────────────────────────


@dataclass
class ImdbTitle:
    """نتیجه سرچ IMDB"""
    imdb_id: str
    title: str
    year: Optional[int] = None
    kind: str = ""        # feature, TV series, TV movie, ...
    qid: str = ""          # movie, tvSeries, ...
    cover: str = ""
    stars: str = ""        # "Zendaya, Robert Pattinson"
    is_series: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TitleInfo:
    """اطلاعات کامل یه فیلم/سریال"""
    imdb_id: str
    title: str
    original_title: str = ""
    year: Optional[int] = None
    end_year: Optional[int] = None
    plot: str = ""
    cover: str = ""
    title_type: str = ""   # "Movie", "TV Series", ...
    is_series: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TvEpisodes:
    """اطلاعات فصل/قسمت یه سریال"""
    imdb_id: str
    title: str = ""
    seasons: Dict[int, List[int]] = field(default_factory=dict)  # {1: [1,2,3,...], 2: [...]}

    @property
    def total_seasons(self) -> int:
        return len(self.seasons)

    @property
    def total_episodes(self) -> int:
        return sum(len(eps) for eps in self.seasons.values())

    def to_dict(self) -> dict:
        return {
            "imdb_id": self.imdb_id,
            "title": self.title,
            "seasons": self.seasons,
            "total_seasons": self.total_seasons,
            "total_episodes": self.total_episodes,
        }


# ─── Search ─────────────────────────────────────────────────


async def search_imdb(query: str, limit: int = 20) -> List[dict]:
    """
    سرچ IMDB با suggestion API.

    Returns:
        لیست dict با فیلدهای:
        - imdb_id, title, year, kind, qid, cover, stars, is_series
    """
    if not query or len(query.strip()) < 2:
        return []

    query = query.strip()
    # حرف اول query برای hash URL
    encoded = quote_plus(query)
    first_letter = query[0].lower() if query[0].isalpha() else "x"
    url = f"https://v3.sg.media-imdb.com/suggestion/h/{first_letter}/{encoded}.json"
    # fallback به URL بدون hash
    fallback_url = f"https://v3.sg.media-imdb.com/suggestion/h/{encoded}.json"

    async with httpx.AsyncClient(timeout=20, headers=_HEADERS) as cli:
        for u in (url, fallback_url):
            try:
                r = await cli.get(u)
                if r.status_code == 200:
                    data = r.json()
                    items = data.get("d", [])
                    results = [_parse_suggestion(it) for it in items]
                    results = [r for r in results if r is not None]
                    if limit:
                        results = results[:limit]
                    logger.info("IMDB search q='%s' -> %d results", query, len(results))
                    return [r.to_dict() for r in results]
            except Exception as e:
                logger.warning("search_imdb %s failed: %s", u, e)

    return []


def _parse_suggestion(item: dict) -> Optional[ImdbTitle]:
    """پارس یه آیتم از suggestion API"""
    try:
        imdb_id = item.get("id", "")
        if not imdb_id or not imdb_id.startswith("tt"):
            return None

        title = item.get("l", "")
        if not title:
            return None

        year = item.get("y")
        kind = item.get("q", "")        # feature, TV series, TV movie, short, ...
        qid = item.get("qid", "")        # movie, tvSeries, tvMovie, ...
        stars = item.get("s", "")        # "Zendaya, Robert Pattinson"

        # cover
        cover = ""
        img = item.get("i")
        if isinstance(img, dict):
            cover = img.get("imageUrl", "")

        is_series = qid in ("tvSeries", "tvMiniSeries") or kind in ("TV series", "TV Series", "TV mini-series")

        return ImdbTitle(
            imdb_id=imdb_id,
            title=title,
            year=year,
            kind=kind,
            qid=qid,
            cover=cover,
            stars=stars,
            is_series=is_series,
        )
    except Exception:
        return None


# ─── Title info ─────────────────────────────────────────────


_QUERY_TITLE = """
query Title($id: ID!) {
    title(id: $id) {
        id
        titleText { text }
        originalTitleText { text }
        releaseYear { year endYear }
        plot { plotText { plainText } }
        primaryImage { url }
        titleType { id text }
    }
}
"""


async def get_title_info(imdb_id: str) -> Optional[dict]:
    """
    گرفتن اطلاعات کامل یه فیلم/سریال با GraphQL.

    Returns:
        dict با فیلدهای:
        - imdb_id, title, original_title, year, end_year, plot, cover, title_type, is_series
    """
    if not imdb_id or not imdb_id.startswith("tt"):
        return None

    async with httpx.AsyncClient(timeout=30, headers=_GRAPHQL_HEADERS) as cli:
        try:
            r = await cli.post("https://caching.graphql.imdb.com/", json={
                "query": _QUERY_TITLE,
                "operationName": "Title",
                "variables": {"id": imdb_id},
            })
            if r.status_code != 200:
                logger.warning("get_title_info HTTP %d", r.status_code)
                return None

            d = r.json()
            if "errors" in d:
                logger.warning("get_title_info GraphQL errors: %s", d["errors"][:200])
                return None

            title = d.get("data", {}).get("title", {})
            if not title:
                return None

            info = TitleInfo(
                imdb_id=title.get("id", imdb_id),
                title=title.get("titleText", {}).get("text", ""),
                original_title=title.get("originalTitleText", {}).get("text", ""),
                year=title.get("releaseYear", {}).get("year"),
                end_year=title.get("releaseYear", {}).get("endYear"),
                plot=title.get("plot", {}).get("plotText", {}).get("plainText", "") if title.get("plot") else "",
                cover=title.get("primaryImage", {}).get("url", "") if title.get("primaryImage") else "",
                title_type=title.get("titleType", {}).get("text", ""),
                is_series=title.get("titleType", {}).get("id") in ("tvSeries", "tvMiniSeries"),
            )
            logger.info("get_title_info %s -> %s (%s)", imdb_id, info.title, info.title_type)
            return info.to_dict()

        except Exception as e:
            logger.warning("get_title_info failed: %s", e)
            return None


# ─── TV episodes (via vidsrcme) ─────────────────────────────


async def get_tv_episodes(imdb_id: str) -> Optional[dict]:
    """
    گرفتن لیست فصل/قسمت سریال.

    ابتدا از vidsrcme API استفاده می‌کنه، اگه کار نکرد از TMDB API به‌عنوان fallback.

    Returns:
        dict با فیلدهای:
        - imdb_id, title, seasons (dict {season_num: [episode_nums]}),
          total_seasons, total_episodes
    """
    if not imdb_id or not imdb_id.startswith("tt"):
        return None

    # ─── روش 1: vidsrcme API (با curl_cffi) ───
    try:
        from curl_cffi.requests import AsyncSession
        api_url = f"https://data.vidsrcme.ru/api.php?type=tv&imdb={imdb_id}"
        async with AsyncSession() as s:
            r = await s.get(api_url, impersonate="chrome", timeout=20, headers=_HEADERS)
            if r.status_code == 200:
                d = r.json()
                data = d.get("data", {})
                eps_raw = data.get("eps", {})

                seasons = {}
                for s_str, ep_list in eps_raw.items():
                    try:
                        s_num = int(s_str)
                        seasons[s_num] = [int(e) for e in ep_list if e.isdigit()]
                    except (ValueError, TypeError):
                        continue

                if seasons:
                    # مرتب‌سازی نزولی (جدیدترین فصل اول)
                    seasons = dict(sorted(seasons.items(), key=lambda x: -x[0]))
                    result = TvEpisodes(
                        imdb_id=imdb_id,
                        title=data.get("title", ""),
                        seasons=seasons,
                    )
                    logger.info("get_tv_episodes %s (vidsrcme) -> %d seasons, %d eps",
                                imdb_id, result.total_seasons, result.total_episodes)
                    return result.to_dict()
            else:
                logger.warning("get_tv_episodes vidsrcme HTTP %d", r.status_code)
    except Exception as e:
        logger.warning("get_tv_episodes vidsrcme failed: %s", e)

    # ─── روش 2: TMDB API (fallback) ───
    try:
        from curl_cffi.requests import AsyncSession
        _TMDB_KEY = "adc48d20c0956934fb224de5c40bb85d"

        # اول imdb_id → tmdb_id
        find_url = f"https://api.themoviedb.org/3/find/{imdb_id}?api_key={_TMDB_KEY}&external_source=imdb_id"
        async with AsyncSession() as s:
            r = await s.get(find_url, impersonate="chrome", timeout=15, headers=_HEADERS)
            if r.status_code != 200:
                logger.warning("get_tv_episodes TMDB find HTTP %d", r.status_code)
                return None
            find_data = r.json()
            tv_results = find_data.get("tv_results", [])
            if not tv_results:
                logger.warning("get_tv_episodes TMDB: no TV results for %s", imdb_id)
                return None

            tmdb_id = str(tv_results[0].get("id", ""))
            show_title = tv_results[0].get("name", "")

            # گرفتن اطلاعات فصل‌ها
            show_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={_TMDB_KEY}"
            r2 = await s.get(show_url, impersonate="chrome", timeout=15, headers=_HEADERS)
            if r2.status_code != 200:
                logger.warning("get_tv_episodes TMDB show HTTP %d", r2.status_code)
                return None

            show_data = r2.json()
            seasons_data = show_data.get("seasons", [])

            seasons = {}
            for season in seasons_data:
                s_num = season.get("season_number", 0)
                ep_count = season.get("episode_count", 0)
                if s_num > 0 and ep_count > 0:  # رد کردن Season 0 (Specials)
                    seasons[s_num] = list(range(1, ep_count + 1))

            if seasons:
                seasons = dict(sorted(seasons.items(), key=lambda x: -x[0]))
                result = TvEpisodes(
                    imdb_id=imdb_id,
                    title=show_title,
                    seasons=seasons,
                )
                logger.info("get_tv_episodes %s (TMDB) -> %d seasons, %d eps",
                            imdb_id, result.total_seasons, result.total_episodes)
                return result.to_dict()

            logger.warning("get_tv_episodes TMDB: no seasons found for %s", imdb_id)
            return None
    except Exception as e:
        logger.warning("get_tv_episodes TMDB failed: %s", e)
        return None


# ─── Quick test ─────────────────────────────────────────────


async def _test():
    print("=== Test IMDB search ===")
    results = await search_imdb("the drama", limit=5)
    for r in results:
        print(f"  [{r['imdb_id']}] {r['title']} ({r.get('year', '?')})  kind={r['kind']}  series={r['is_series']}")

    print("\n=== Test get_title_info: tt33071426 (movie) ===")
    info = await get_title_info("tt33071426")
    if info:
        print(f"  title: {info['title']}")
        print(f"  type: {info['title_type']}")
        print(f"  year: {info['year']}")
        print(f"  plot: {info['plot'][:200]}")
        print(f"  cover: {info['cover'][:80]}")

    print("\n=== Test get_title_info: tt0944947 (Game of Thrones) ===")
    info = await get_title_info("tt0944947")
    if info:
        print(f"  title: {info['title']} ({info['year']}-{info['end_year']})")
        print(f"  type: {info['title_type']}  series={info['is_series']}")

    print("\n=== Test get_tv_episodes: tt0944947 ===")
    eps = await get_tv_episodes("tt0944947")
    if eps:
        print(f"  title: {eps['title']}")
        print(f"  seasons: {eps['total_seasons']}  total_eps: {eps['total_episodes']}")
        for s, eplist in list(eps["seasons"].items())[:3]:
            print(f"    S{s}: {len(eplist)} eps -> {eplist[:5]}")


if __name__ == "__main__":
    asyncio.run(_test())
