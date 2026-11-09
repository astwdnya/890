"""
diycraft_handler.py
───────────────────
هندلر دانلود از سایت diycraftsguide.com (فیلم/سریال ایرانی).

روش کار:
  1. کاربر لینک watch رو می‌فرسته
  2. هندلر صفحه رو fetch می‌کنه
  3. اگه فیلم باشه: لینک مستقیم MP4 استخراج و دانلود می‌شه
  4. اگه سریال باشه: لیست قسمت‌ها استخراج می‌شه، کاربر قسمت رو انتخاب می‌کنه
  5. ویدیو دانلود و ارسال می‌شه

استفاده:
  کاربر لینک می‌فرسته → ربات خودکار تشخیص می‌ده و دانلود می‌کنه
"""

import asyncio
import logging
import os
import re
import subprocess
import time
from typing import Optional, List
from urllib.parse import urlparse

logger = logging.getLogger("DiycraftHandler")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_BASE_URL = "https://www.diycraftsguide.com"


def is_diycraft_url(url: str) -> bool:
    """بررسی آیا URL مربوط به diycraftsguide.com هست."""
    return "diycraftsguide.com" in url and "/watch/" in url


async def extract_video_info(url: str) -> Optional[dict]:
    """
    استخراج اطلاعات ویدیو از صفحه watch.

    برای فیلم: لینک مستقیم MP4 از sources استخراج می‌شه.
    برای سریال: اگه صفحه قسمت خاص نباشه، لیست قسمت‌ها استخراج می‌شه.

    Returns:
        dict با فیلدهای:
        - title: عنوان
        - thumbnail: عکس
        - video_url: لینک مستقیم MP4 (برای فیلم)
        - video_id: شناسه ویدیو
        - is_series: آیا سریاله
        - episodes: لیست قسمت‌ها (برای سریال) [{episode: num, url: str, key: str}]
    """
    try:
        from curl_cffi.requests import AsyncSession

        async with AsyncSession() as s:
            r = await s.get(
                url,
                impersonate="chrome",
                timeout=60,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept": "text/html",
                    "Referer": _BASE_URL + "/",
                },
            )
            if r.status_code != 200:
                logger.warning("diycraft page HTTP %d", r.status_code)
                return None

            html = r.text

            # Extract video source from JavaScript
            source_match = re.search(
                r"sources\s*:\s*\[\s*\{\s*src\s*:\s*['\"]([^'\"]+)['\"]",
                html,
                re.IGNORECASE,
            )

            video_url = ""
            if source_match:
                video_url = source_match.group(1)

            # Also try contentUrl from JSON-LD
            if not video_url:
                content_url = re.search(r'"contentUrl"\s*:\s*"([^"]+)"', html)
                if content_url:
                    video_url = content_url.group(1)

            if not video_url:
                # Last resort: find any .mp4 URL
                mp4_urls = re.findall(r"https?://[^\s\"'<>]+\.mp4[^\s\"'<>]*", html)
                if mp4_urls:
                    video_url = mp4_urls[0]

            # Extract title
            title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORE_CASE | re.DOTALL)
            title = title_match.group(1).strip() if title_match else "Video"
            title = re.sub(r"\s*-\s*LiveFarsi.*$", "", title)
            title = re.sub(r"\s*-\s*DIY.*$", "", title)

            # Extract thumbnail
            thumb_match = re.search(
                r'<meta[^>]*property="og:image"[^>]*content="([^"]+)"',
                html,
                re.IGNORECASE,
            )
            thumbnail = thumb_match.group(1) if thumb_match else ""

            # Extract video ID
            vid_id_match = re.search(r'video_id\s*[:=]\s*["\']?(\d+)', html)
            video_id = vid_id_match.group(1) if vid_id_match else ""

            # Check if it's a series (no video URL but has episode links)
            if not video_url:
                # This might be a series page - extract episode links
                ep_pattern = re.findall(
                    r'href="(https://www\.diycraftsguide\.com/watch/[^"]+\.html\?key=([a-z0-9]+))"[^>]*>.*?(?:E(\d+)|Episode\s*(\d+)|قسمت\s*(\d+))',
                    html,
                    re.DOTALL | re.IGNORECASE,
                )

                if ep_pattern:
                    episodes = []
                    seen = set()
                    for full_url, key, ep1, ep2, ep3 in ep_pattern:
                        ep_num = int(ep1 or ep2 or ep3 or 0)
                        if ep_num > 0 and ep_num not in seen:
                            seen.add(ep_num)
                            episodes.append({
                                "episode": ep_num,
                                "url": full_url,
                                "key": key,
                            })

                    episodes.sort(key=lambda x: x["episode"])

                    logger.info("diycraft: series '%s' with %d episodes", title[:40], len(episodes))
                    return {
                        "title": title,
                        "thumbnail": thumbnail,
                        "video_url": None,
                        "video_id": video_id,
                        "page_url": url,
                        "is_series": True,
                        "episodes": episodes,
                    }

                logger.error("diycraft: no video URL found and no episodes")
                return None

            logger.info("diycraft: %s -> %s", title[:40], video_url[:80])

            return {
                "title": title,
                "thumbnail": thumbnail,
                "video_url": video_url,
                "video_id": video_id,
                "page_url": url,
                "is_series": False,
                "episodes": [],
            }
    except Exception as e:
        logger.warning("diycraft extract failed: %s", e)
        return None


async def extract_episode_video(url: str, key: str) -> Optional[str]:
    """
    استخراج لینک مستقیم MP4 برای یک قسمت خاص از سریال.

    Args:
        url: صفحه watch (مثلا https://www.diycraftsguide.com/watch/bad-naam.html)
        key: کلید قسمت (مثلا p0v6hbwqmhcl)

    Returns:
        لینک مستقیم MP4 یا None
    """
    full_url = f"{url}?key={key}" if "?" not in url else f"{url}&key={key}"

    try:
        from curl_cffi.requests import AsyncSession

        async with AsyncSession() as s:
            r = await s.get(
                full_url,
                impersonate="chrome",
                timeout=60,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept": "text/html",
                    "Referer": _BASE_URL + "/",
                },
            )
            if r.status_code != 200:
                logger.warning("diycraft episode HTTP %d", r.status_code)
                return None

            html = r.text

            # Extract video source
            source_match = re.search(
                r"sources\s*:\s*\[\s*\{\s*src\s*:\s*['\"]([^'\"]+)['\"]",
                html,
                re.IGNORECASE,
            )

            if source_match:
                video_url = source_match.group(1)
                logger.info("diycraft episode: %s -> %s", key, video_url[:80])
                return video_url

            # Try contentUrl
            content_url = re.search(r'"contentUrl"\s*:\s*"([^"]+)"', html)
            if content_url:
                return content_url.group(1)

            # Last resort: find any .mp4 URL
            mp4_urls = re.findall(r"https?://[^\s\"'<>]+\.mp4[^\s\"'<>]*", html)
            if mp4_urls:
                return mp4_urls[0]

            logger.warning("diycraft episode: no video URL for key=%s", key)
            return None
    except Exception as e:
        logger.warning("diycraft episode extract failed: %s", e)
        return None


async def download_video(video_url: str, out_dir: str, referer: str = "", progress_cb=None) -> Optional[str]:
    """
    دانلود ویدیو از لینک مستقیم (streaming — بدون OOM).

    Returns:
        مسیر فایل دانلود شده یا None
    """
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{int(time.time())}.mp4")

    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "*/*",
    }
    if referer:
        headers["Referer"] = referer

    # روش 1: curl_cffi با streaming (بدون load کامل در memory)
    try:
        from curl_cffi.requests import AsyncSession

        async with AsyncSession() as s:
            r = await s.get(
                video_url,
                impersonate="chrome",
                timeout=600,
                headers=headers,
                stream=True,
            )
            if r.status_code == 200:
                total = int(r.headers.get("content-length", 0))
                done = 0
                with open(out_path, "wb") as f:
                    async for chunk in r.aiter_content(chunk_size=1024 * 256):
                        f.write(chunk)
                        done += len(chunk)
                        if progress_cb:
                            try:
                                progress_cb(done, total)
                            except Exception:
                                pass

                if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    logger.info("diycraft download complete (curl stream): %s (%.1f MB)",
                                out_path, os.path.getsize(out_path) / 1024 / 1024)
                    return out_path
            else:
                logger.warning("diycraft download HTTP %d", r.status_code)
    except Exception as e:
        logger.warning("diycraft download curl_cffi failed: %s", e)

    # روش 2: wget (با progress)
    logger.info("Trying wget fallback...")
    try:
        wget_cmd = [
            "wget", "--no-check-certificate",
            "-U", _USER_AGENT,
            "--progress=dot:mega",
        ]
        if referer:
            wget_cmd += ["--referer", referer]
        wget_cmd += ["-O", out_path, video_url]

        result = subprocess.run(wget_cmd, capture_output=True, timeout=1800)
        if result.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            logger.info("diycraft download complete (wget): %s (%.1f MB)",
                        out_path, os.path.getsize(out_path) / 1024 / 1024)
            return out_path
        else:
            stderr = result.stderr.decode("utf-8", errors="ignore")[:300]
            logger.warning("wget failed: %s", stderr)
    except Exception as e:
        logger.warning("wget failed: %s", e)

    # روش 3: urllib (همیشه streaming)
    logger.info("Trying urllib fallback...")
    try:
        import urllib.request
        req = urllib.request.Request(video_url, headers=headers)
        with urllib.request.urlopen(req, timeout=600) as response:
            total = int(response.headers.get("Content-Length", 0))
            done = 0
            with open(out_path, "wb") as f:
                while True:
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if progress_cb:
                        try:
                            progress_cb(done, total)
                        except Exception:
                            pass
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            logger.info("diycraft download complete (urllib): %s (%.1f MB)",
                        out_path, os.path.getsize(out_path) / 1024 / 1024)
            return out_path
    except Exception as e:
        logger.warning("urllib failed: %s", e)

    return None
