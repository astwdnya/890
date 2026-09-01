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

# aiofiles برای نوشتن موازی فایل‌های بزرگ (multi-segment download)
try:
    import aiofiles
    _HAS_AIOFILES = True
except ImportError:
    _HAS_AIOFILES = False

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
    format_spec: Optional[str] = None,
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
        format_spec: انتخاب کیفیت با yt-dlp format selector
            (مثلاً 'best[height<=720]' برای محدود کردن به 720p).
            اگه None باشه، بهترین کیفیت پیش‌فرض دانلود می‌شه.

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

        if format_spec:
            cmd.extend(["-f", format_spec])

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
                # 5 min per line - یعنی اگه 5 دقیقه هیچ خروجی نبود، timeout
                # (برای اتصال‌های کند یا فایل‌های بزرگ)
                line = await asyncio.wait_for(process.stdout.readline(), timeout=300)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                cleanup_file(filepath)
                return False, "Download timed out (5 min no output)", 0
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

    از curl_cffi با stream=True برای browser impersonation استفاده می‌کنه.
    retry می‌کنه با yt-dlp اگه curl_cffi شکست بخوره.
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

    # اطمینان از وجود پوشه‌ی parent فایل خروجی
    parent_dir = os.path.dirname(filepath)
    if parent_dir and not os.path.exists(parent_dir):
        try:
            os.makedirs(parent_dir, exist_ok=True)
        except Exception as e:
            logger.error("Cannot create output directory %s: %s", parent_dir, e)
            return False, f"Cannot create output directory: {e}", 0

    try:
        from curl_cffi.requests import AsyncSession

        # استفاده از stream=True برای جلوگیری از لود کامل محتوا در RAM
        # و اجازه دادن به curl_cffi برای receive incremental
        async with AsyncSession() as session:
            resp = await session.get(
                url,
                impersonate="chrome",
                headers=headers,
                timeout=600,  # 10 min for large files
                stream=True,
            )
            if resp.status_code != 200:
                # اگه کد وضعیت خطا بود، fallback به yt-dlp
                logger.warning(
                    "download_direct HTTP %s for %s - falling back to yt-dlp",
                    resp.status_code, url[:80]
                )
                return await download_with_ytdlp(
                    url, filepath, progress_cb, referer=referer,
                    user_agent=user_agent, max_filesize=max_filesize,
                )

            content_length = resp.headers.get("Content-Length")
            total_size = int(content_length) if content_length else 0
            if total_size:
                if total_size > max_filesize:
                    return False, "File exceeds size limit", 0
                if total_size == 0:
                    return False, "Empty file", 0

            # Write to file با streaming
            total_written = 0
            last_update = 0.0
            last_speed_time = [time.time()]
            last_speed_bytes = [0]
            file_handle = open(filepath, "wb")
            try:
                # تابع aiter_content روی Response وجود داره وقتی stream=True باشه
                async for chunk in resp.aiter_content(chunk_size=1024 * 256):
                    if not chunk:
                        break
                    file_handle.write(chunk)
                    total_written += len(chunk)
                    now = time.time()
                    if now - last_update >= 3.0:
                        last_update = now
                        size_str = f"{total_written/1024/1024:.1f} MB"
                        # محاسبه سرعت
                        dt = now - last_speed_time[0]
                        if dt > 0:
                            speed = (total_written - last_speed_bytes[0]) / dt / 1024
                            speed_str = f" · {speed:.0f} KB/s"
                        else:
                            speed_str = ""
                        last_speed_time[0] = now
                        last_speed_bytes[0] = total_written
                        if total_size:
                            pct = total_written * 100 // total_size
                            await progress_cb(
                                f"📥 **Downloading: {size_str} / {total_size/1024/1024:.1f} MB ({pct}%){speed_str}**"
                            )
                        else:
                            await progress_cb(
                                f"📥 **Downloading: {size_str}{speed_str}**"
                            )
            finally:
                file_handle.close()

            if total_written == 0:
                cleanup_file(filepath)
                return False, "Downloaded file is empty", 0
            if total_written > max_filesize:
                cleanup_file(filepath)
                return False, "File exceeds size limit", 0
            # اگه Content-Length داشت و دانلود ناقص بود، fallback به yt-dlp
            if total_size and total_written < total_size * 0.95:
                logger.warning(
                    "download_direct incomplete: %d/%d bytes - falling back to yt-dlp",
                    total_written, total_size
                )
                cleanup_file(filepath)
                return await download_with_ytdlp(
                    url, filepath, progress_cb, referer=referer,
                    user_agent=user_agent, max_filesize=max_filesize,
                )
            return True, "", total_written

    except asyncio.CancelledError:
        cleanup_file(filepath)
        raise
    except Exception as e:
        # اگه curl_cffi خطا داد، fallback به yt-dlp
        logger.warning(
            "download_direct error: %s - falling back to yt-dlp",
            str(e)[:150]
        )
        cleanup_file(filepath)
        return await download_with_ytdlp(
            url, filepath, progress_cb, referer=referer,
            user_agent=user_agent, max_filesize=max_filesize,
        )


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


# ─── Multi-segment parallel download (FAST! 16x speed) ────


def _format_progress_bar(downloaded: int, total: int, speed: float, elapsed: float,
                         completed_chunks: int = 0, total_chunks: int = 0) -> str:
    """
    ساخت progress message زیبا با progress bar (مثل هندلرهای قدیمی).

    Args:
        downloaded: bytes downloaded so far
        total: total bytes
        speed: bytes per second
        elapsed: seconds since start
        completed_chunks: تعداد chunk های تکمیل‌شده (اختیاری)
        total_chunks: کل chunk ها (اختیاری)

    Returns:
        formatted message string
    """
    if total > 0:
        total_mb = total / 1024 / 1024
        dl_mb = downloaded / 1024 / 1024
        pct = (downloaded / total * 100)
        filled = int(pct / 5)
        bar = "█" * filled + "░" * (20 - filled)
        speed_mb = min(speed / 1024 / 1024, 999)
        eta_secs = int((total - downloaded) / speed) if speed > 0 else 0
        eta_m, eta_s = divmod(eta_secs, 60)
        chunks_info = ""
        if total_chunks > 0:
            chunks_info = f"\n📦 {completed_chunks}/{total_chunks} chunks • 🔥 16x"
        return (
            f"📥 **Downloading...**\n`[{bar}]`\n"
            f"💾 {dl_mb:.1f}/{total_mb:.1f} MB  •  ⚡ {speed_mb:.1f} MB/s\n"
            f"📊 {pct:.1f}%  •  ⏱ ETA: {eta_m}:{eta_s:02d}"
            f"{chunks_info}"
        )
    else:
        dl_mb = downloaded / 1024 / 1024
        speed_mb = min(speed / 1024 / 1024, 999)
        return (
            f"📥 **Downloading...**\n💾 {dl_mb:.1f} MB  •  ⚡ {speed_mb:.1f} MB/s"
        )


async def download_direct_multi(
    url: str,
    filepath: str,
    progress_cb: ProgressCallback,
    referer: Optional[str] = None,
    user_agent: Optional[str] = None,
    max_filesize: int = MAX_DOWNLOAD_SIZE,
    num_workers: int = 16,
    chunk_size: int = 5 * 1024 * 1024,  # 5 MB per chunk
) -> Tuple[bool, str, int]:
    """
    دانلود سریع با Work-Queue Multi-Segment (16 worker موازی).

    این تابع فایل رو به chunk های 5MB تقسیم می‌کنه و 16 worker همزمان
    از یه queue می‌خورن. اینطوری همیشه 16 connection فعال هستن تا آخر
    دانلود و سرعت ثابت می‌مونه.

    از session اشتراکی استفاده می‌کنه تا TLS handshake فقط یه بار انجام بشه.
    با progress bar زیبا (مثل هندلرهای قدیمی).

    Args:
        url: URL ویدیو (mp4 مستقیم)
        filepath: مسیر ذخیره فایل
        progress_cb: callback async برای گزارش پیشرفت
        referer: Referer header
        user_agent: UA سفارشی
        max_filesize: حداکثر حجم مجاز (بایت)
        num_workers: تعداد worker موازی (پیش‌فرض 16)
        chunk_size: حجم هر chunk (پیش‌فرض 5MB)

    Returns:
        Tuple (success, error_message, file_size)
    """
    if not check_impersonation_support():
        # fallback به download_direct ساده
        return await download_direct(
            url, filepath, progress_cb, referer=referer,
            user_agent=user_agent, max_filesize=max_filesize,
        )

    ua = user_agent or _DEFAULT_UA
    headers = {
        "User-Agent": ua,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if referer:
        headers["Referer"] = referer

    # اطمینان از وجود پوشه‌ی parent فایل خروجی
    parent_dir = os.path.dirname(filepath)
    if parent_dir and not os.path.exists(parent_dir):
        try:
            os.makedirs(parent_dir, exist_ok=True)
        except Exception as e:
            logger.error("Cannot create output directory %s: %s", parent_dir, e)
            return False, f"Cannot create output directory: {e}", 0

    try:
        from curl_cffi.requests import AsyncSession

        # Step 1: HEAD request برای بررسی Content-Length و Accept-Ranges
        async with AsyncSession() as session:
            # اول یه range request کوچیک بزن ببین Accept-Ranges داره یا نه
            head_resp = await session.get(
                url, impersonate="chrome",
                headers={**headers, "Range": "bytes=0-1023"},
                timeout=30, verify=False, stream=True,
            )
            # نباید بسته بشه - باید status_code رو نگه داریم و بعد ببندیم
            head_status = head_resp.status_code
            content_length_header = head_resp.headers.get("Content-Length")
            accept_ranges = head_resp.headers.get("Accept-Ranges", "").lower()
            content_range = head_resp.headers.get("Content-Range", "")

            # اگه Range پشتیبانی نمی‌شه (status != 206)، fallback به download_direct
            supports_range = (
                head_status == 206
                or accept_ranges == "bytes"
                or content_range
            )

            if not supports_range:
                logger.info("download_direct_multi: Range not supported - falling back to download_direct")
                # close the response properly
                await head_resp.acontent()
                return await download_direct(
                    url, filepath, progress_cb, referer=referer,
                    user_agent=user_agent, max_filesize=max_filesize,
                )

            # استخراج total size از Content-Range یا Content-Length
            total_size = 0
            if content_range:
                # bytes 0-1023/24006226 → 24006226
                m = re.search(r"/(\d+)$", content_range)
                if m:
                    total_size = int(m.group(1))
            if not total_size and content_length_header:
                # اگه Content-Range نبود، Content-Length کل size هست (وقتی Range ست نشده)
                total_size = int(content_length_header)

            if not total_size:
                # نمی‌تونیم total size رو تشخیص بدیم - fallback
                logger.info("download_direct_multi: Cannot determine total size - falling back to download_direct")
                await head_resp.acontent()
                return await download_direct(
                    url, filepath, progress_cb, referer=referer,
                    user_agent=user_agent, max_filesize=max_filesize,
                )

            if total_size > max_filesize:
                await head_resp.acontent()
                return False, "File exceeds size limit", 0

            # اگه فایل خیلی کوچیکه (کمتر از 5MB)، نیازی به multi-segment نیست
            if total_size < chunk_size:
                logger.info("download_direct_multi: File too small (%d bytes) - using direct", total_size)
                await head_resp.acontent()
                return await download_direct(
                    url, filepath, progress_cb, referer=referer,
                    user_agent=user_agent, max_filesize=max_filesize,
                )

            # close head response
            await head_resp.acontent()

            # Step 2: ساخت لیست chunk ها
            chunks = []
            offset = 0
            chunk_idx = 0
            while offset < total_size:
                end = min(offset + chunk_size - 1, total_size - 1)
                chunks.append((chunk_idx, offset, end))
                offset = end + 1
                chunk_idx += 1

            total_chunks = len(chunks)
            logger.info(
                "Multi-segment download: %d chunks × %dMB, %d workers, total=%d MB",
                total_chunks, chunk_size // 1024 // 1024, num_workers,
                total_size // 1024 // 1024
            )

            # Step 3: فایل خروجی رو همون اول با حجم نهایی بساز (sparse file)
            try:
                with open(filepath, "wb") as f:
                    f.truncate(total_size)
            except Exception as e:
                logger.warning("Could not pre-allocate file: %s", e)

            # Queue از chunk ها
            chunk_queue = asyncio.Queue()
            for c in chunks:
                await chunk_queue.put(c)

            # متغیرهای مشترک
            downloaded_bytes = [0] * total_chunks
            completed_chunks = [0]
            failed_chunks = []
            start_time = time.time()
            last_update = [0.0]
            progress_lock = asyncio.Lock()
            file_write_lock = asyncio.Lock()

            # session اشتراکی برای همه worker ها (جلوگیری از TLS handshake مکرر)
            shared_session = AsyncSession()

            async def _update_progress(force: bool = False):
                """گزارش progress به کاربر با progress bar زیبا."""
                now = time.time()
                if not force and now - last_update[0] < 1.5:
                    return
                last_update[0] = now
                total_dl = sum(downloaded_bytes)
                elapsed = now - start_time
                speed = total_dl / elapsed if elapsed > 0 else 0
                msg = _format_progress_bar(
                    total_dl, total_size, speed, elapsed,
                    completed_chunks[0], total_chunks
                )
                try:
                    await progress_cb(msg)
                except Exception:
                    pass

            async def _download_worker(worker_id: int):
                """هر worker از queue chunk می‌گیره و دانلود می‌کنه."""
                while True:
                    try:
                        chunk_info = chunk_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return True  # queue خالی، worker تموم کرد

                    c_idx, byte_start, byte_end = chunk_info
                    expected_size = byte_end - byte_start + 1
                    max_retries = 3

                    for attempt in range(max_retries):
                        try:
                            resp = await shared_session.get(
                                url, impersonate="chrome",
                                headers={
                                    "User-Agent": ua,
                                    "Accept": "*/*",
                                    "Accept-Language": "en-US,en;q=0.9",
                                    "Referer": referer or "",
                                    "Range": f"bytes={byte_start}-{byte_end}",
                                },
                                allow_redirects=True,
                                timeout=300,
                                stream=True,
                            )

                            if resp.status_code not in (200, 206):
                                raise Exception(f"HTTP {resp.status_code}")

                            # دانلود chunk به memory (چون کوچیکه - 5MB)
                            chunk_data = b""
                            async for piece in resp.aiter_content():
                                if not piece:
                                    continue
                                chunk_data += piece

                            if len(chunk_data) != expected_size:
                                raise Exception(
                                    f"Size mismatch: expected {expected_size}, got {len(chunk_data)}"
                                )

                            # نوشتن به فایل با seek (با lock برای thread safety)
                            if _HAS_AIOFILES:
                                async with file_write_lock:
                                    async with aiofiles.open(filepath, "r+b") as f:
                                        await f.seek(byte_start)
                                        await f.write(chunk_data)
                            else:
                                # fallback بدون aiofiles
                                async with file_write_lock:
                                    with open(filepath, "r+b") as f:
                                        f.seek(byte_start)
                                        f.write(chunk_data)

                            downloaded_bytes[c_idx] = expected_size
                            async with progress_lock:
                                completed_chunks[0] += 1
                                await _update_progress()
                            break  # chunk با موفقیت دانلود شد

                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            logger.debug(
                                "Worker %d chunk %d attempt %d failed: %s",
                                worker_id, c_idx, attempt + 1, e
                            )
                            if attempt < max_retries - 1:
                                await asyncio.sleep(1.5 * (attempt + 1))
                            else:
                                failed_chunks.append((c_idx, str(e)))
                                return False
                return True

            # پیام شروع
            await progress_cb(f"📥 **Downloading...**\n`[{('░' * 20)}]`\n💾 0.0/{total_size/1024/1024:.1f} MB  •  ⚡ 0.0 MB/s\n📊 0.0%  •  ⏱ ETA: --:--\n📦 0/{total_chunks} chunks • 🔥 16x")

            # Step 4: اجرای worker ها به‌صورت موازی
            workers = [
                asyncio.create_task(_download_worker(i))
                for i in range(num_workers)
            ]
            try:
                results = await asyncio.gather(*workers, return_exceptions=True)
            except asyncio.CancelledError:
                # Cancel all workers
                for w in workers:
                    w.cancel()
                await shared_session.close()
                cleanup_file(filepath)
                raise

            # close shared session
            await shared_session.close()

            # Check failures
            had_failures = []
            for r in results:
                if isinstance(r, Exception):
                    had_failures.append(str(r))
                elif r is False:
                    had_failures.append("Worker returned False")

            if failed_chunks:
                logger.warning(
                    "Multi-segment: %d/%d chunks failed: %s",
                    len(failed_chunks), total_chunks,
                    "; ".join(f"{c}:{e[:50]}" for c, e in failed_chunks[:3])
                )

            # اگه بیش از 5% chunk ها fail شدن، خطا
            if len(failed_chunks) > total_chunks * 0.05:
                cleanup_file(filepath)
                err_msg = "; ".join(f"{c}:{e[:80]}" for c, e in failed_chunks[:3])
                return False, f"Multi-segment download failed: {len(failed_chunks)}/{total_chunks} chunks failed. {err_msg[:100]}", 0

            # Step 5: بررسی نهایی فایل
            total_dl = sum(downloaded_bytes)
            if total_dl == 0:
                cleanup_file(filepath)
                return False, "Downloaded file is empty", 0
            if total_dl < total_size * 0.95:
                # اگه کمتر از 95% دانلود شده، fallback به download_direct
                logger.warning(
                    "Multi-segment incomplete: %d/%d bytes - falling back to direct",
                    total_dl, total_size
                )
                cleanup_file(filepath)
                return await download_direct(
                    url, filepath, progress_cb, referer=referer,
                    user_agent=user_agent, max_filesize=max_filesize,
                )

            # پیام پایان
            elapsed = time.time() - start_time
            avg_speed = total_dl / elapsed if elapsed > 0 else 0
            await progress_cb(
                f"📥 **Download complete!**\n"
                f"💾 {total_dl/1024/1024:.1f} MB in {elapsed:.1f}s\n"
                f"⚡ Avg: {avg_speed/1024/1024:.1f} MB/s"
            )

            actual_size = os.path.getsize(filepath)
            return True, "", actual_size

    except asyncio.CancelledError:
        cleanup_file(filepath)
        raise
    except Exception as e:
        logger.warning(
            "download_direct_multi error: %s - falling back to download_direct",
            str(e)[:150]
        )
        cleanup_file(filepath)
        return await download_direct(
            url, filepath, progress_cb, referer=referer,
            user_agent=user_agent, max_filesize=max_filesize,
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
