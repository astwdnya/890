"""
vidsrc_pw.py
────────────
دانلود ویدیو از vidsrcme.ru با playwright.
این ماژول به‌جای curl_cffi از playwright استفاده می‌کنه تا:
  - از rate limit/IP block عبور کنه (browser واقعی)
  - token خودکار گرفته بشه
  - segment URLs با token capture بشن

الگوریتم:
  1. playwright browser باز کن
  2. به https://vidsrcme.ru/embed/movie/{imdb_id} برو
  3. کلیک روی bigPlay
  4. صبر کن تا m3u8 و segment‌ها شروع به لود کنن
  5. segment URLs رو از network capture بگیر (همه با token)
  6. browser رو باز نگه دار (برای token معتبر)
  7. segment‌ها رو با httpx (با cookies browser) دانلود کن
  8. ffmpeg concat → فایل نهایی
"""
import asyncio
import logging
import os
import re
import subprocess
import tempfile
from typing import List, Optional, Tuple
from urllib.parse import urlparse, urljoin, parse_qs, urlencode

import httpx
from playwright.async_api import async_playwright

logger = logging.getLogger("VidsrcPW")

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


# ─── Capture m3u8 + segments via playwright ─────────────────


async def capture_video_urls(
    imdb_id: str,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    timeout_sec: int = 60,
    max_segments_to_wait: int = 5,
) -> Tuple[List[str], dict]:
    """
    با playwright به صفحه embed میره و URL های m3u8 و segments رو capture می‌کنه.
    browser باز می‌مونه تا caller ببنده (با close_browser).

    Returns:
        (segment_urls, info)
    """
    if season and episode:
        url = f"https://vidsrcme.ru/embed/tv/{imdb_id}/{season}/{episode}"
    else:
        url = f"https://vidsrcme.ru/embed/movie/{imdb_id}"

    logger.info("capture_video_urls: %s", url)

    segment_urls: List[str] = []
    m3u8_urls: List[str] = []
    master_m3u8_urls: List[str] = []
    master_m3u8_text: List[str] = []   # body of master.m3u8
    variant_m3u8_urls: List[str] = []
    variant_m3u8_text: List[str] = []
    api_responses: List[dict] = []
    cookies: List[dict] = []
    info = {
        "title": "",
        "m3u8_master": "",
        "qualities": [],
        "is_series": bool(season and episode),
        "season": season,
        "episode": episode,
        "imdb_id": imdb_id,
    }

    # بدون async with - browser رو باز نگه دار
    pw = await async_playwright().start()
    # استفاده از args برای محدود کردن حافظه و جلوگیری از crash روی سرورهای کم‌حافظه
    browser = await pw.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",  # مهم برای docker/کانتینر
            "--disable-gpu",
            "--disable-extensions",
            "--disable-software-rasterizer",
            "--single-process",  # کم‌حافظه‌تر
            "--no-zygote",       # کم‌حافظه‌تر
            "--disable-background-networking",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
            "--disable-features=TranslateUI",
            "--disable-ipc-flooding-protection",
        ],
    )
    context = await browser.new_context(
        user_agent=_USER_AGENT,
        viewport={"width": 1280, "height": 720},
    )
    page = await context.new_page()

    # capture requests (URLs)
    def on_request(req):
        u = req.url
        if "/content/" in u and ".html" in u and "token=" in u:
            if u not in segment_urls:
                segment_urls.append(u)
        elif ".m3u8" in u and u not in m3u8_urls:
            m3u8_urls.append(u)
        elif "master.m3u8" in u and u not in master_m3u8_urls:
            master_m3u8_urls.append(u)
            info["m3u8_master"] = u

    page.on("request", on_request)

    # capture responses (body) برای m3u8 و api.php
    async def on_response(resp):
        try:
            u = resp.url
            # capture api.php
            if "api.php" in u and ("type=movie" in u or "type=tv" in u):
                body = await resp.text()
                if body and body.startswith("{"):
                    import json
                    d = json.loads(body)
                    api_responses.append(d)
                    if d.get("data", {}).get("title"):
                        info["title"] = d["data"]["title"]
            # capture master.m3u8 body
            elif "master.m3u8" in u and not master_m3u8_text:
                body = await resp.text()
                if body and "#EXTM3U" in body:
                    master_m3u8_text.append(body)
                    logger.info("captured master.m3u8 body (%d bytes)", len(body))
            # capture variant/index.m3u8 body (نه master) - همه variants رو capture کن
            elif ".m3u8" in u and "master.m3u8" not in u:
                body = await resp.text()
                if body and "#EXTM3U" in body and "EXT-X-STREAM-INF" not in body:
                    if u not in variant_m3u8_urls:
                        variant_m3u8_text.append(body)
                        variant_m3u8_urls.append(u)
                        logger.info("captured variant m3u8 body (%d bytes): %s", len(body), u[:80])
        except Exception as e:
            logger.debug("on_response: %s", e)

    page.on("response", on_response)

    # goto
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        logger.warning("goto: %s", e)

    # صبر کوچک برای لود iframes
    await asyncio.sleep(2)

    # پیدا کردن iframe داخلی (cloudorchestranova)
    for frame in page.frames:
        if "cloudorch" in frame.url:
            logger.info("inner frame: %s", frame.url[:100])
            try:
                bp = frame.locator("#bigPlay")
                cnt = await bp.count()
                if cnt > 0:
                    await bp.first.click(timeout=5000, force=True)
                    logger.info("clicked inner bigPlay")
            except Exception as e:
                logger.warning("inner click: %s", e)
            break

    # صبر کن segments شروع بشن و variant m3u8 حتماً capture بشه
    logger.info("waiting for variant m3u8 and segments...")
    start = asyncio.get_event_loop().time()
    while True:
        await asyncio.sleep(1)
        elapsed = asyncio.get_event_loop().time() - start
        # اگه هم variant m3u8 داریم هم چند segment، کافیه
        if len(segment_urls) >= max_segments_to_wait and variant_m3u8_text:
            logger.info("got %d segments + %d variant after %.1fs",
                        len(segment_urls), len(variant_m3u8_text), elapsed)
            break
        # اگه variant m3u8 داریم ولی segments هنوز شروع نشدن، یه کم بیشتر صبر کن
        if variant_m3u8_text and elapsed > timeout_sec:
            logger.info("have variant but timeout on segments, proceeding")
            break
        if elapsed > timeout_sec:
            logger.warning("timeout: %d segments, %d variants",
                           len(segment_urls), len(variant_m3u8_text))
            break

    # cookies رو نگه دار
    try:
        cookies = await context.cookies()
    except Exception:
        pass

    # browser و context و pw رو ذخیره کن تا بعداً ببندیم
    info["_pw"] = pw
    info["_browser"] = browser
    info["_context"] = context
    info["_page"] = page
    info["_cookies"] = cookies
    info["_master_m3u8"] = master_m3u8_urls
    info["_master_m3u8_text"] = master_m3u8_text
    info["_variant_m3u8_urls"] = variant_m3u8_urls
    info["_variant_m3u8_text"] = variant_m3u8_text
    info["_m3u8_urls"] = m3u8_urls

    return segment_urls, info


async def close_browser(info: dict):
    """بستن browser و playwright بعد از اتمام دانلود"""
    try:
        if "_browser" in info:
            await info["_browser"].close()
    except Exception:
        pass
    try:
        if "_pw" in info:
            await info["_pw"].stop()
    except Exception:
        pass


# ─── Get qualities via playwright ───────────────────────────


async def get_qualities_via_pw(
    imdb_id: str,
    season: Optional[int] = None,
    episode: Optional[int] = None,
) -> List[dict]:
    """
    گرفتن لیست کیفیت‌ها با playwright.
    ابتدا capture می‌کنه، بعد master.m3u8 رو با context.request.fetch (browser) می‌گیره.
    """
    qualities = []

    segment_urls, info = await capture_video_urls(
        imdb_id, season, episode, timeout_sec=30, max_segments_to_wait=2,
    )

    try:
        master_url = info.get("_master_m3u8", [None])[0] if info.get("_master_m3u8") else None
        if not master_url:
            m3u8_list = info.get("_m3u8_urls", [])
            master_url = m3u8_list[0] if m3u8_list else None

        if not master_url:
            logger.warning("no master m3u8 captured")
            await close_browser(info)
            return []

        logger.info("master: %s", master_url[:100])

        # از master_m3u8_text استفاده کن (captured body)
        master_text_list = info.get("_master_m3u8_text", [])
        if not master_text_list:
            logger.warning("master.m3u8 body not captured")
            await close_browser(info)
            return []

        master_text = master_text_list[0]
        variants = _parse_master_m3u8(master_text)
        from vidsrc_extras import _variant_to_quality
        qs = [_variant_to_quality(v) for v in variants]
        seen = set()
        for q in sorted(qs, key=lambda x: x.height, reverse=True):
            if q.label in seen:
                continue
            seen.add(q.label)
            qualities.append(q.to_dict())

        # اضافه کردن Auto
        auto = {"label": "Auto", "bandwidth": 0, "resolution": "", "height": 0, "url": "", "is_auto": True}
        qualities = [auto] + qualities

    finally:
        await close_browser(info)

    return qualities


def _parse_master_m3u8(text: str):
    """از master.m3u8 variant URL ها رو استخراج کن"""
    lines = text.split("\n")
    variants = []
    bandwidth = 0
    resolution = ""
    for line in lines:
        line = line.strip()
        if line.startswith("#EXT-X-STREAM-INF:"):
            m = re.search(r'BANDWIDTH=(\d+)', line)
            if m:
                bandwidth = int(m.group(1))
            m = re.search(r'RESOLUTION=(\d+x\d+)', line)
            if m:
                resolution = m.group(1)
        elif line and not line.startswith("#"):
            variants.append((line, bandwidth, resolution))
            bandwidth = 0
            resolution = ""
    return variants


# ─── Download via playwright capture ─────────────────────────


async def download_via_pw(
    imdb_id: str,
    out_dir: str,
    quality_label: str = "Auto",
    season: Optional[int] = None,
    episode: Optional[int] = None,
    progress_cb=None,
) -> Optional[str]:
    """
    دانلود ویدیو با playwright:
      1. browser باز کن، m3u8 و segments رو capture کن
      2. master.m3u8 رو با context.request (browser) fetch کن
      3. variant m3u8 رو fetch کن
      4. segment‌ها رو با context.request دانلود کن
      5. ffmpeg concat
    """
    segment_urls, info = await capture_video_urls(
        imdb_id, season, episode, timeout_sec=45, max_segments_to_wait=3,
    )

    try:
        master_urls = info.get("_master_m3u8", [])
        master_text_list = info.get("_master_m3u8_text", [])
        variant_text_list = info.get("_variant_m3u8_text", [])
        variant_url_list = info.get("_variant_m3u8_urls", [])

        if not master_text_list:
            logger.error("master.m3u8 body not captured")
            return None

        # master URL (اگه نسخه URL هم capture شده، از اون استفاده کن؛ وگرنه از variant URL استخراج کن)
        if master_urls:
            master_url = master_urls[0]
        elif variant_url_list:
            # ساخت master URL از variant URL (همون host و token)
            variant_url = variant_url_list[0]
            p = urlparse(variant_url)
            qs_v = parse_qs(p.query)
            token_v = qs_v.get("token", [""])[0]
            # master URL: همان host با /pl/.../master.m3u8 - ولی ما فقط host و token رو میخوایم
            master_url = variant_url  # فقط برای استخراج base
        else:
            logger.error("no master or variant URL")
            return None

        master_text = master_text_list[0]
        variants = _parse_master_m3u8(master_text)
        logger.info("master.m3u8: %d variants", len(variants))
        if not variants:
            return None

        # 2. pick variant
        from vidsrc_extras import _variant_to_quality
        qs = [_variant_to_quality(v) for v in variants]
        sorted_qs = sorted(qs, key=lambda x: x.height, reverse=True)

        chosen = None
        if quality_label in ("Auto", "best", ""):
            chosen = sorted_qs[0]
        elif quality_label == "worst":
            chosen = sorted_qs[-1]
        else:
            for q in sorted_qs:
                if q.label == quality_label:
                    chosen = q
                    break
            if not chosen:
                target_h = 720
                m = re.search(r'\d+', quality_label)
                if m:
                    target_h = int(m.group(0))
                chosen = min(sorted_qs, key=lambda q: abs(q.height - target_h) if q.height else 9999)

        logger.info("chosen: %s (%s) bw=%d", chosen.label, chosen.resolution, chosen.bandwidth)

        # 3. پیدا کردن captured variant که با chosen match میشه
        p = urlparse(master_url)
        base = f"{p.scheme}://{p.netloc}"
        qs_master = parse_qs(p.query)
        token = qs_master.get("token", [""])[0]

        chosen_var_url = None
        chosen_var_text = None

        if chosen.url.startswith("/"):
            chosen_full_url = base + chosen.url
        elif chosen.url.startswith("http"):
            chosen_full_url = chosen.url
        else:
            chosen_full_url = urljoin(master_url, chosen.url)

        chosen_path = chosen_full_url.split("?")[0]

        for i, vu in enumerate(variant_url_list):
            vu_path = vu.split("?")[0]
            if vu_path == chosen_path:
                chosen_var_url = vu
                chosen_var_text = variant_text_list[i] if i < len(variant_text_list) else None
                logger.info("matched captured variant: %s", chosen_var_url[:80])
                break

        if not chosen_var_text:
            # اگه chosen match نشد، اولین captured variant رو استفاده کن
            # این ممکنه کیفیت متفاوتی داشته باشه ولی کار میکنه
            if variant_text_list:
                # پیدا کردن نزدیک‌ترین variant بر اساس quality
                # برای سادگی، اولین رو میگیریم
                chosen_var_text = variant_text_list[0]
                chosen_var_url = variant_url_list[0] if variant_url_list else ""
                # token رو از chosen_var_url بگیر (اگه از master نیومد)
                if not token and chosen_var_url:
                    qs_v = parse_qs(urlparse(chosen_var_url).query)
                    token = qs_v.get("token", [""])[0]
                logger.info("using first captured variant: %s", chosen_var_url[:80])
            else:
                logger.error("variant m3u8 not captured")
                return None

        # 4. parse segment URLs
        segments = _parse_variant_m3u8(chosen_var_text)
        logger.info("variant.m3u8: %d segments", len(segments))
        if not segments:
            return None

        # base URL برای variant (از chosen_var_url)
        if chosen_var_url:
            var_base_url = chosen_var_url.rsplit("/", 1)[0] + "/"
            # base رو هم از chosen_var_url بگیر (ممکنه با master فرق کنه - CDN مختلف)
            p_v = urlparse(chosen_var_url)
            base = f"{p_v.scheme}://{p_v.netloc}"
            if not token:
                qs_v = parse_qs(p_v.query)
                token = qs_v.get("token", [""])[0]
        else:
            var_base_url = chosen_full_url.rsplit("/", 1)[0] + "/"

        logger.info("base: %s, token: %s...", base, token[:30] if token else "NONE")

        seg_urls = []
        for seg_rel, _dur in segments:
            if seg_rel.startswith("/"):
                su = base + seg_rel
            elif seg_rel.startswith("http"):
                su = seg_rel
            else:
                su = var_base_url + seg_rel
            if token and "token=" not in su:
                su += ("&" if "?" in su else "?") + f"token={token}"
            seg_urls.append(su)

        logger.info("first segment URL: %s", seg_urls[0][:150] if seg_urls else "NONE")

        # 5. download segments با context.request (browser)
        os.makedirs(out_dir, exist_ok=True)
        title = info.get("title") or imdb_id
        if info.get("is_series"):
            out_name = f"{title} S{season:02d}E{episode:02d}.mp4".replace("/", "_")
        else:
            out_name = f"{title}.mp4".replace("/", "_")
        out_path = os.path.join(out_dir, out_name)

        context = info.get("_context")
        if not context:
            logger.error("no browser context")
            return None

        with tempfile.TemporaryDirectory(prefix="vidsrc_pw_") as tmp:
            seg_paths = await _download_segments_parallel_pw(
                seg_urls, tmp, context, progress_cb,
            )
            if not seg_paths:
                logger.error("no segments downloaded")
                return None

            if _concat_segments(seg_paths, out_path):
                logger.info("✅ saved: %s", out_path)
                return out_path
            return None

    finally:
        await close_browser(info)


async def _download_segments_parallel_pw(
    seg_urls: List[str],
    out_dir: str,
    context,
    progress_cb=None,
    concurrency: int = 2,
):
    """دانلود موازی segment‌ها با context.request (browser)
    توجه: concurrency کم هست چون سرور rate limit داره
    """
    sem = asyncio.Semaphore(concurrency)
    seg_paths = [None] * len(seg_urls)

    # header‌های لازم برای segment download
    seg_headers = {
        "Referer": "https://cloudorchestranova.com/",
        "Origin": "https://cloudorchestranova.com",
        "Accept": "*/*",
    }

    async def download_one(idx: int, url: str):
        async with sem:
            for attempt in range(5):
                try:
                    r = await context.request.get(url, headers=seg_headers, timeout=60000)
                    if r.ok:
                        body = await r.body()
                        if body:
                            path = os.path.join(out_dir, f"seg_{idx:05d}.ts")
                            with open(path, "wb") as f:
                                f.write(body)
                            seg_paths[idx] = path
                            if progress_cb:
                                progress_cb(idx + 1, len(seg_urls))
                            return
                    elif r.status in (429, 403):
                        # rate limit - تاخیر بیشتر
                        wait = 3 * (attempt + 1)
                        logger.warning("seg %d HTTP %d, waiting %ds", idx, r.status, wait)
                        await asyncio.sleep(wait)
                        continue
                    else:
                        logger.warning("seg %d HTTP %d", idx, r.status)
                except Exception as e:
                    logger.warning("seg %d attempt %d: %s", idx, attempt + 1, e)
                await asyncio.sleep(1 * (attempt + 1))

    await asyncio.gather(*[download_one(i, u) for i, u in enumerate(seg_urls)])
    return [p for p in seg_paths if p]


def _parse_variant_m3u8(text: str):
    """از variant m3u8 segment URL ها رو استخراج کن"""
    lines = text.split("\n")
    segments = []
    duration = 0.0
    for line in lines:
        line = line.strip()
        if line.startswith("#EXTINF:"):
            try:
                duration = float(line[8:].split(",")[0])
            except Exception:
                pass
        elif line and not line.startswith("#"):
            segments.append((line, duration))
            duration = 0.0
    return segments


async def _download_segments_parallel(
    seg_urls: List[str],
    out_dir: str,
    headers: dict,
    progress_cb=None,
    concurrency: int = 8,
) -> List[str]:
    """دانلود موازی segment‌ها با httpx"""
    sem = asyncio.Semaphore(concurrency)
    seg_paths = [None] * len(seg_urls)

    async with httpx.AsyncClient(timeout=60, headers=headers, follow_redirects=True) as cli:
        async def download_one(idx: int, url: str):
            async with sem:
                for attempt in range(3):
                    try:
                        r = await cli.get(url)
                        if r.status_code == 200 and r.content:
                            path = os.path.join(out_dir, f"seg_{idx:05d}.ts")
                            with open(path, "wb") as f:
                                f.write(r.content)
                            seg_paths[idx] = path
                            if progress_cb:
                                progress_cb(idx + 1, len(seg_urls))
                            return
                        else:
                            logger.warning("seg %d HTTP %d", idx, r.status_code)
                    except Exception as e:
                        logger.warning("seg %d attempt %d: %s", idx, attempt + 1, e)
                    await asyncio.sleep(0.5 * (attempt + 1))

        await asyncio.gather(*[download_one(i, u) for i, u in enumerate(seg_urls)])

    return [p for p in seg_paths if p]


def _concat_segments(seg_paths: List[str], out_path: str) -> bool:
    """concat با ffmpeg"""
    if not seg_paths:
        return False
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for p in seg_paths:
            f.write(f"file '{p}'\n")
        list_path = f.name

    try:
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", list_path,
            "-c", "copy",
            "-bsf:a", "aac_adtstoasc",
            out_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            cmd2 = [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0",
                "-i", list_path,
                "-c", "copy",
                out_path,
            ]
            result = subprocess.run(cmd2, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                logger.error("ffmpeg concat failed: %s", result.stderr[-500:])
                return False
        return True
    finally:
        try:
            os.unlink(list_path)
        except Exception:
            pass


# ─── Quick test ─────────────────────────────────────────────


async def _test():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    print("=== Test 1: get_qualities ===")
    qs = await get_qualities_via_pw("tt33071426")
    for q in qs:
        print(f"  {q['label']}: {q['resolution']} bw={q['bandwidth']}")

    print("\n=== Test 2: download (480p) ===")
    last = [0, 0]
    def on_prog(d, t):
        last[0] = (d, t)

    async def upd():
        while True:
            await asyncio.sleep(5)
            if last[0]:
                d, t = last[0]
                print(f"\r  segs: {d}/{t} ({d*100//t if t else 0}%)", end="", flush=True)

    u = asyncio.create_task(upd())
    path = await download_via_pw(
        "tt33071426", "/tmp/test_pw_dl", "480p",
        progress_cb=on_prog,
    )
    u.cancel()
    print()
    if path:
        sz = os.path.getsize(path) / 1024 / 1024
        print(f"✅ {path} ({sz:.1f} MB)")
    else:
        print("❌ failed")


if __name__ == "__main__":
    asyncio.run(_test())
