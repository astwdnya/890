"""
diycraft_handler.py
───────────────────
هندلر دانلود از سایت diycraftsguide.com (فیلم/سریال ایرانی).

روش کار:
  1. کاربر لینک watch رو می‌فرسته
  2. هندلر صفحه رو fetch می‌کنه
  3. لینک مستقیم MP4 از source رو استخراج می‌کنه
  4. ویدیو رو دانلود و ارسال می‌کنه

استفاده:
  کاربر لینک می‌فرسته → ربات خودکار تشخیص می‌ده و دانلود می‌کنه
"""

import asyncio
import logging
import os
import re
import subprocess
import tempfile
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

    Returns:
        dict با فیلدهای:
        - title: عنوان
        - thumbnail: عکس
        - video_url: لینک مستقیم MP4
        - video_id: شناسه ویدیو
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
            # Pattern: sources: [{ src: 'URL', type: 'video/mp4' }]
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

            if not video_url:
                logger.error("diycraft: no video URL found")
                return None

            # Extract title
            title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
            title = title_match.group(1).strip() if title_match else "Video"
            # Clean title
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

            logger.info("diycraft: %s -> %s", title[:40], video_url[:80])

            return {
                "title": title,
                "thumbnail": thumbnail,
                "video_url": video_url,
                "video_id": video_id,
                "page_url": url,
            }
    except Exception as e:
        logger.warning("diycraft extract failed: %s", e)
        return None


async def download_video(video_url: str, out_dir: str, referer: str = "", progress_cb=None) -> Optional[str]:
    """
    دانلود ویدیو از لینک مستقیم.

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

    # روش 1: curl_cffi
    try:
        from curl_cffi.requests import AsyncSession

        async with AsyncSession() as s:
            r = await s.get(
                video_url,
                impersonate="chrome",
                timeout=600,
                headers=headers,
            )
            if r.status_code == 200 and r.content:
                with open(out_path, "wb") as f:
                    f.write(r.content)

                if progress_cb:
                    try:
                        progress_cb(os.path.getsize(out_path), os.path.getsize(out_path))
                    except Exception:
                        pass

                logger.info("diycraft download complete (curl): %s (%.1f MB)",
                            out_path, os.path.getsize(out_path) / 1024 / 1024)
                return out_path
            else:
                logger.warning("diycraft download HTTP %d", r.status_code)
    except Exception as e:
        logger.warning("diycraft download curl_cffi failed: %s", e)

    # روش 2: wget
    logger.info("Trying wget fallback...")
    try:
        wget_cmd = [
            "wget", "-q", "--no-check-certificate",
            "-U", _USER_AGENT,
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

    # روش 3: urllib
    logger.info("Trying urllib fallback...")
    try:
        import urllib.request
        req = urllib.request.Request(video_url, headers=headers)
        with urllib.request.urlopen(req, timeout=600) as response:
            with open(out_path, "wb") as f:
                while True:
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    f.write(chunk)
                    if progress_cb:
                        try:
                            progress_cb(os.path.getsize(out_path), 0)
                        except Exception:
                            pass
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            logger.info("diycraft download complete (urllib): %s (%.1f MB)",
                        out_path, os.path.getsize(out_path) / 1024 / 1024)
            return out_path
    except Exception as e:
        logger.warning("urllib failed: %s", e)

    return None
