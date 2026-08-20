"""
_common.py
──────────
کد مشترک برای همه‌ی هندلرهای سایت.

این ماژول شامل توابع کمکی برای:
  - استخراج کیفیت‌ها از صفحه‌ی ویدیو با curl_cffi (browser impersonation)
  - دانلود با yt-dlp (بهترین گزینه برای اکثر سایت‌ها)
  - مدیریت فایل‌های موقت
  - مدیریت host‌های مجاز
"""

import asyncio
import html as html_lib
import logging
import os
import re
import shutil
import time
from typing import Awaitable, Callable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

logger = logging.getLogger("SiteHandlers")

ProgressCallback = Callable[[str], Awaitable[None]]

# محدودیت حجم دانلود (50 GB)
MAX_DOWNLOAD_SIZE = 50 * 1024 * 1024 * 1024

# User-Agent استاندارد (Chrome روی Windows)
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ─── Utility ────────────────────────────────────────────────


def default_user_agent() -> str:
    return _DEFAULT_UA


def is_url_in_domains(url: str, allowed_hosts: set, host_suffixes: tuple = ()) -> bool:
    """بررسی می‌کنه که آیا URL در دامنه‌های مجاز هست."""
    try:
        host = (urlparse(url).hostname or "").lower()
        if host in allowed_hosts:
            return True
        for suffix in host_suffixes:
            if host.endswith(suffix):
                return True
    except Exception:
        pass
    return False


def cleanup_file(filepath: str) -> None:
    """حذف فایل در صورت وجود."""
    try:
        if filepath and os.path.exists(filepath):
            os.remove(filepath)
    except OSError as e:
        logger.warning("Failed to cleanup file %s: %s", filepath, e)


def check_impersonation_support() -> bool:
    """بررسی می‌کنه که curl_cffi (browser impersonation) در دسترس هست یا نه."""
    try:
        import curl_cffi  # noqa: F401
        return True
    except ImportError:
        return False


def find_output_file(filepath: str) -> Optional[str]:
    """پیدا کردن فایل خروجی - yt-dlp ممکنه پسوند رو تغییر بده."""
    if os.path.exists(filepath):
        return filepath
    base, _ = os.path.splitext(filepath)
    for ext in (".mp4", ".mkv", ".webm", ".ts"):
        candidate = base + ext
        if os.path.exists(candidate):
            try:
                os.rename(candidate, filepath)
                return filepath
            except OSError:
                return candidate
    return None


def quality_sort_key(q: dict) -> int:
    """مرتب‌سازی کیفیت‌ها بر اساس resolution."""
    nums = re.findall(r"\d+", q.get("label", ""))
    return int(nums[-1]) if nums else 0


def extract_title_from_html(html: str, site_name: str = "") -> str:
    """استخراج عنوان از HTML."""
    # 1. og:title
    m = re.search(
        r'<meta[^>]+og:title["\'][^>]+content=["\']([^"\']+)["\']', html, re.I
    )
    if m:
        return html_lib.unescape(m.group(1).strip())
    # 2. <title>
    m = re.search(r"<title>([^<]+)</title>", html, re.I)
    if m:
        title = m.group(1).strip()
        # حذف پسوند نام سایت
        if site_name:
            title = re.sub(
                rf"\s*[-|]\s*{re.escape(site_name)}.*$", "", title, flags=re.I
            ).strip()
        return html_lib.unescape(title) or "Untitled"
    return "Untitled"


def safe_filename(title: str, max_len: int = 60) -> str:
    """تبدیل عنوان به نام فایل امن."""
    safe = re.sub(r"[^\w\s\-]", "", title)[:max_len].strip()
    return safe or "video"


# ─── HTTP fetch (curl_cffi) ────────────────────────────────


async def fetch_html(
    url: str,
    referer: Optional[str] = None,
    user_agent: Optional[str] = None,
    extra_headers: Optional[dict] = None,
    impersonate: str = "chrome",
    timeout: int = 25,
    visit_homepage_first: Optional[str] = None,
) -> Tuple[Optional[str], int]:
    """
    دریافت HTML با curl_cffi (browser impersonation برای دور زدن Cloudflare و WAF‌ها).

    Args:
        url: URL صفحه‌ای که می‌خوایم fetch کنیم
        referer: صفحه‌ای که ازش آمیدم
        user_agent: UA سفارشی
        extra_headers: هدرهای اضافی
        impersonate: نوع browser impersonation (chrome/safari/firefox)
        timeout: timeout بر حسب ثانیه
        visit_homepage_first: اگه داده بشه، اول این URL رو visit می‌کنه
                              (برای ذخیره cookies)

    Returns:
        Tuple (html_content, status_code). اگه خطا باشه (None, 0).
    """
    if not check_impersonation_support():
        logger.warning("curl_cffi not available - HTTP fetch will fail")
        return None, 0

    ua = user_agent or _DEFAULT_UA
    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if referer:
        headers["Referer"] = referer
    if extra_headers:
        headers.update(extra_headers)

    try:
        from curl_cffi.requests import AsyncSession

        async with AsyncSession() as session:
            # اگه خواسته شده، اول homepage رو visit کن
            if visit_homepage_first:
                try:
                    await session.get(
                        visit_homepage_first,
                        impersonate=impersonate,
                        headers={"User-Agent": ua},
                        timeout=15,
                    )
                except Exception:
                    pass  # ignore errors visiting homepage

            resp = await session.get(
                url,
                impersonate=impersonate,
                headers=headers,
                timeout=timeout,
            )
            return resp.text, resp.status_code
    except Exception as e:
        logger.debug("fetch_html failed for %s: %s", url, e)
        return None, 0


async def fetch_json(
    url: str,
    referer: Optional[str] = None,
    user_agent: Optional[str] = None,
    extra_headers: Optional[dict] = None,
    impersonate: str = "chrome",
    timeout: int = 25,
    visit_homepage_first: Optional[str] = None,
    visit_video_page: Optional[str] = None,
) -> Tuple[Optional[dict], int]:
    """
    دریافت JSON با curl_cffi.

    Args:
        visit_homepage_first: URL homepage که اول visit می‌شه
        visit_video_page: URL صفحه ویدیو که بعد از homepage visit می‌شه
                          (برای گرفتن cookies session)

    Returns:
        Tuple (parsed_json, status_code). اگه خطا باشه (None, 0).
    """
    if not check_impersonation_support():
        return None, 0

    ua = user_agent or _DEFAULT_UA
    headers = {
        "User-Agent": ua,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.9",
        "X-Requested-With": "XMLHttpRequest",
    }
    if referer:
        headers["Referer"] = referer
    if extra_headers:
        headers.update(extra_headers)

    try:
        from curl_cffi.requests import AsyncSession

        async with AsyncSession() as session:
            # Step 1: Visit homepage (for initial cookies)
            if visit_homepage_first:
                try:
                    await session.get(
                        visit_homepage_first,
                        impersonate=impersonate,
                        headers={"User-Agent": ua},
                        timeout=15,
                    )
                except Exception:
                    pass

            # Step 2: Visit video page (for session cookies)
            if visit_video_page:
                try:
                    await session.get(
                        visit_video_page,
                        impersonate=impersonate,
                        headers={"User-Agent": ua},
                        timeout=15,
                    )
                except Exception:
                    pass

            resp = await session.get(
                url,
                impersonate=impersonate,
                headers=headers,
                timeout=timeout,
            )
            try:
                return resp.json(), resp.status_code
            except Exception:
                return None, resp.status_code
    except Exception as e:
        logger.debug("fetch_json failed for %s: %s", url, e)
        return None, 0


# ─── yt-dlp download helper ───────────────────────────────


async def download_with_ytdlp(
    url: str,
    filepath: str,
    progress_cb: ProgressCallback,
    referer: Optional[str] = None,
    user_agent: Optional[str] = None,
    max_filesize: int = MAX_DOWNLOAD_SIZE,
    extra_headers: Optional[dict] = None,
) -> Tuple[bool, str, int]:
    """
    دانلود ویدیو با yt-dlp (با aria2c اگه موجود باشه، وگرنه concurrent fragments).

    Args:
        url: URL ویدیو (mp4/m3u8/سایت)
        filepath: مسیر ذخیره فایل
        progress_cb: callback async برای گزارش پیشرفت
        referer: Referer header سفارشی
        user_agent: UA سفارشی
        max_filesize: حداکثر حجم مجاز (بایت)
        extra_headers: هدرهای اضافی برای yt-dlp

    Returns:
        Tuple (success, error_message, file_size).
    """
    if not shutil.which("yt-dlp"):
        return False, "yt-dlp not installed", 0

    has_aria2c = shutil.which("aria2c") is not None
    mode = "aria2c" if has_aria2c else "concurrent x16"
    await progress_cb(f"📥 **شروع دانلود (yt-dlp · {mode})...**")

    try:
        ua = user_agent or _DEFAULT_UA
        cmd = [
            "yt-dlp",
            "--no-warnings",
            "--progress",
            "--newline",
            "--no-check-certificates",
            "--concurrent-fragments",
            "16",
            "--retries",
            "10",
            "--fragment-retries",
            "10",
            "--retry-sleep",
            "fragment:exp=1:30",
            "--buffer-size",
            "16K",
            "--max-filesize",
            str(max_filesize),
            "--add-header",
            f"User-Agent:{ua}",
            "--merge-output-format",
            "mp4",
            "-o",
            filepath,
        ]

        if referer:
            cmd.extend(["--add-header", f"Referer:{referer}"])

        if extra_headers:
            for k, v in extra_headers.items():
                cmd.extend(["--add-header", f"{k}:{v}"])

        if has_aria2c:
            cmd.extend(
                [
                    "--downloader",
                    "aria2c",
                    "--downloader-args",
                    "aria2c:-x16 -s16 -k1M --max-connection-per-server=16 "
                    "--min-split-size=1M --console-log-level=warn",
                ]
            )

        if check_impersonation_support():
            cmd.extend(["--impersonate", "chrome"])

        cmd.append(url)

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        last_update = 0.0
        tail: List[str] = []
        while True:
            try:
                line = await asyncio.wait_for(process.stdout.readline(), timeout=180)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                cleanup_file(filepath)
                return False, "Download timed out", 0
            if not line:
                break
            text = line.decode(errors="replace").strip()
            if text:
                tail.append(text)
                if len(tail) > 15:
                    tail.pop(0)
            now = time.time()
            if now - last_update >= 2.0 and text:
                last_update = now
                await progress_cb(f"📥 **Downloading (via yt-dlp ⚡ 32x)...**\n`{text[:80]}`")

        await process.wait()
        if process.returncode != 0:
            err = "\n".join(tail[-5:]) or "yt-dlp failed"
            return False, err[:200], 0

        actual_path = find_output_file(filepath)
        if not actual_path:
            return False, "Output file not found", 0

        size = os.path.getsize(actual_path)
        if size == 0:
            cleanup_file(actual_path)
            return False, "Downloaded file is empty", 0
        if size > max_filesize:
            cleanup_file(actual_path)
            return False, "File exceeds size limit", 0
        return True, "", size

    except asyncio.CancelledError:
        cleanup_file(filepath)
        raise
    except Exception as e:
        return False, str(e)[:150], 0


# ─── Direct HTTP download (for direct mp4 URLs) ────────────


async def download_direct(
    url: str,
    filepath: str,
    progress_cb: ProgressCallback,
    referer: Optional[str] = None,
    user_agent: Optional[str] = None,
    max_filesize: int = MAX_DOWNLOAD_SIZE,
) -> Tuple[bool, str, int]:
    """
    دانلود مستقیم یه فایل mp4 (نه m3u8).
    مناسب برای سایت‌هایی که لینک mp4 مستقیم می‌دن (مثل KVS).

    از curl_cffi برای browser impersonation استفاده می‌کنه.
    """
    if not check_impersonation_support():
        # fallback به yt-dlp
        return await download_with_ytdlp(
            url, filepath, progress_cb, referer=referer, user_agent=user_agent,
            max_filesize=max_filesize,
        )

    ua = user_agent or _DEFAULT_UA
    headers = {
        "User-Agent": ua,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if referer:
        headers["Referer"] = referer

    await progress_cb(f"📥 **شروع دانلود مستقیم...**")

    try:
        from curl_cffi.requests import AsyncSession

        async with AsyncSession() as session:
            resp = await session.get(
                url,
                impersonate="chrome",
                headers=headers,
                timeout=300,  # 5 min for large files
            )
            if resp.status_code != 200:
                return False, f"HTTP {resp.status_code}", 0

            content_length = resp.headers.get("Content-Length")
            if content_length:
                size = int(content_length)
                if size > max_filesize:
                    return False, "File exceeds size limit", 0
                if size == 0:
                    return False, "Empty file", 0

            # Write to file
            total_written = 0
            last_update = 0.0
            with open(filepath, "wb") as f:
                # Iterate over content in chunks
                async for chunk in resp.aiter_content(chunk_size=1024 * 256):
                    if not chunk:
                        break
                    f.write(chunk)
                    total_written += len(chunk)
                    now = time.time()
                    if now - last_update >= 2.0:
                        last_update = now
                        size_str = f"{total_written/1024/1024:.1f} MB"
                        if content_length:
                            pct = total_written * 100 // int(content_length)
                            await progress_cb(
                                f"📥 **Downloading: {size_str} ({pct}%)...**"
                            )
                        else:
                            await progress_cb(
                                f"📥 **Downloading: {size_str}...**"
                            )

            if total_written == 0:
                cleanup_file(filepath)
                return False, "Downloaded file is empty", 0
            if total_written > max_filesize:
                cleanup_file(filepath)
                return False, "File exceeds size limit", 0
            return True, "", total_written

    except asyncio.CancelledError:
        cleanup_file(filepath)
        raise
    except Exception as e:
        cleanup_file(filepath)
        return False, str(e)[:150], 0


# ─── Generic m3u8 download (uses yt-dlp) ──────────────────


async def download_m3u8(
    url: str,
    filepath: str,
    progress_cb: ProgressCallback,
    referer: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Tuple[bool, str, int]:
    """
    دانلود HLS/m3u8 stream با yt-dlp.
    این تابع همون download_with_ytdlp رو صدا می‌زنه ولی اسمش رو گذاشتیم
    m3u8 برای سازگاری با API هندلرها.
    """
    return await download_with_ytdlp(
        url, filepath, progress_cb, referer=referer, user_agent=user_agent
    )


# ─── Generic extractor helpers ────────────────────────────


async def extract_qualities_with_ytdlp(
    url: str,
    site_display_name: str,
    prefer_quality_labels: bool = True,
) -> Tuple[List[dict], str]:
    """
    استخراج کیفیت‌های موجود با yt-dlp (به‌عنوان fallback).

    برای سایت‌هایی که yt-dlp پشتیبانی می‌کنه ولی هندلر اختصاصی نداریم.
    """
    if not shutil.which("yt-dlp"):
        return [], "yt-dlp not installed"

    try:
        # از -J برای JSON output استفاده می‌کنیم (نه --list-formats + --print-json)
        cmd = [
            "yt-dlp",
            "--no-warnings",
            "--no-check-certificates",
            "--skip-download",
            "-J",
            url,
        ]
        if check_impersonation_support():
            cmd.extend(["--impersonate", "chrome"])

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            err = stderr.decode()[:200]
            return [], err

        # Parse JSON output
        import json
        try:
            data = json.loads(stdout.decode())
        except json.JSONDecodeError as e:
            logger.debug("yt-dlp JSON parse error: %s", e)
            return [], "Failed to parse yt-dlp output"

        title = data.get("title", "") or site_display_name
        formats = data.get("formats", [])
        if not formats:
            return [], title

        qualities: List[dict] = []
        for fmt in formats:
            vcodec = fmt.get("vcodec", "")
            if vcodec == "none":
                continue  # audio only
            height = fmt.get("height") or 0
            ext = fmt.get("ext", "mp4")
            fmt_url = fmt.get("url", "")
            if not fmt_url:
                continue
            format_id = fmt.get("format_id", "") or ""
            
            # تشخیص label کیفیت از height یا format_id
            if height:
                label = f"{height}p"
            elif "hq" in format_id.lower():
                label = "HQ (720p)"
            elif "lq" in format_id.lower():
                label = "LQ (360p)"
            elif "4k" in format_id.lower():
                label = "4K (2160p)"
            elif "_hd" in format_id.lower() or "hd" in format_id.lower():
                label = "HD"
            else:
                # اگه هیچ اطلاعاتی نبود، از format_id استفاده کن
                label = format_id or "Auto"
            
            method = "m3u8" if ext == "m3u8" or "m3u8" in fmt_url else "direct"
            qualities.append({
                "label": f"📡 {label}",
                "url": fmt_url,
                "method": method,
            })

        # Dedupe by label
        seen_labels = set()
        unique = []
        for q in qualities:
            if q["label"] not in seen_labels:
                seen_labels.add(q["label"])
                unique.append(q)
        unique.sort(key=quality_sort_key, reverse=True)

        if not unique:
            # اگه هیچ کیفیت ویدیویی پیدا نشد، خود yt-dlp به‌عنوان fallback استفاده می‌شه
            # در این صورت، URL اصلی رو می‌دیم به download function
            unique = [{
                "label": "📡 Auto (via yt-dlp)",
                "url": url,
                "method": "m3u8",  # yt-dlp خودش تشخیص می‌ده
            }]

        return unique, title

    except Exception as e:
        return [], str(e)[:200]
