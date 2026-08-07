"""
videotext_burn.py
─────────────────
هندلر برای videotext.io/burn-subtitles بدون Playwright.

الگوریتم:
  1. POST https://api.videotext.io/api/upload/dual (FormData)
     - video: فایل ویدیو
     - subtitles: فایل srt/vtt
     - toolType: "burn-subtitles"
     - headers: x-user-id, x-plan
     - Response: {jobId, status, jobToken}
  2. GET https://api.videotext.io/api/job/{jobId}?jobToken={jobToken} (poll هر 1.5 ثانیه)
     - Response: {status, progress, queuePosition, result}
     - status: queued | processing | completed | failed
  3. وقتی completed شد:
     - download result.downloadUrl
     - return path فایل

استفاده:
  from videotext_burn import burn_subtitles

  out_path = await burn_subtitles(
      video_path="/tmp/video.mp4",
      subtitle_path="/tmp/sub.vtt",
      out_dir="/tmp",
      on_progress=lambda p: print(f"{p}%"),
  )
"""
import asyncio
import logging
import os
from typing import Optional, Callable
from dataclasses import dataclass

import httpx
from curl_cffi.requests import AsyncSession
from curl_cffi import CurlMime

logger = logging.getLogger("VideotextBurn")

# API base - از کد frontend استخراج شده
_API_BASE = "https://api.videotext.io"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Origin": "https://videotext.io",
    "Referer": "https://videotext.io/burn-subtitles",
}

# user-id و plan به‌صورت پیش‌فرض برای free tier
_DEFAULT_USER_ID = "demo-user"
_DEFAULT_PLAN = "free"


# ─── Data models ────────────────────────────────────────────


@dataclass
class BurnOptions:
    font_size: str = "medium"           # small | medium | large
    position: str = "bottom"            # bottom | middle
    background_opacity: str = "low"     # none | low | high
    trimmed_start: Optional[float] = None  # seconds
    trimmed_end: Optional[float] = None    # seconds


@dataclass
class JobStatus:
    status: str              # queued | processing | completed | failed
    progress: int = 0        # 0-100
    queue_position: Optional[int] = None
    download_url: Optional[str] = None
    file_name: Optional[str] = None
    error: Optional[str] = None


# ─── Upload + start job ─────────────────────────────────────


async def upload_dual(
    video_path: str,
    subtitle_path: str,
    user_id: str = _DEFAULT_USER_ID,
    plan: str = _DEFAULT_PLAN,
    options: Optional[BurnOptions] = None,
    on_upload_progress: Optional[Callable[[int], None]] = None,
) -> Optional[dict]:
    """
    آپلود ویدیو + subtitle به videotext.io و شروع job.

    Returns:
        dict با jobId, status, jobToken یا None
    """
    if options is None:
        options = BurnOptions()

    if not os.path.exists(video_path):
        logger.error("video not found: %s", video_path)
        return None
    if not os.path.exists(subtitle_path):
        logger.error("subtitle not found: %s", subtitle_path)
        return None

    url = f"{_API_BASE}/api/upload/dual"
    logger.info("upload_dual: %s + %s -> %s", video_path, subtitle_path, url)

    # ساخت multipart با CurlMime
    mp = CurlMime()
    mp.addpart(name="video", filename=os.path.basename(video_path),
               content_type="video/mp4", local_path=video_path)
    mp.addpart(name="subtitles", filename=os.path.basename(subtitle_path),
               content_type="application/octet-stream", local_path=subtitle_path)
    mp.addpart(name="toolType", data=b"burn-subtitles")
    mp.addpart(name="burnFontSize", data=options.font_size.encode())
    mp.addpart(name="burnPosition", data=options.position.encode())
    mp.addpart(name="burnBackgroundOpacity", data=options.background_opacity.encode())
    if options.trimmed_start is not None:
        mp.addpart(name="trimmedStart", data=str(options.trimmed_start).encode())
    if options.trimmed_end is not None:
        mp.addpart(name="trimmedEnd", data=str(options.trimmed_end).encode())

    headers = dict(_DEFAULT_HEADERS)
    headers["x-user-id"] = user_id
    headers["x-plan"] = plan

    try:
        async with AsyncSession() as s:
            r = await s.post(
                url,
                impersonate="chrome",
                headers=headers,
                multipart=mp,
                timeout=300,  # 5 دقیقه برای آپلود
            )
            logger.info("upload response: status=%d len=%d", r.status_code, len(r.text))
            # videotext 202 Accepted برمی‌گرده وقتی job شروع میشه
            if r.status_code in (200, 201, 202):
                try:
                    d = r.json()
                    if d.get("jobId"):
                        logger.info("job started: %s", d["jobId"])
                        if on_upload_progress:
                            on_upload_progress(100)
                        return d
                    else:
                        logger.error("no jobId in response: %s", d)
                except Exception as e:
                    logger.error("parse upload response: %s", e)
            elif r.status_code == 204:
                logger.error("204 - no job ID returned")
            else:
                logger.error("upload HTTP %d: %s", r.status_code, r.text[:300])
    except Exception as e:
        logger.error("upload failed: %s", e)

    return None


# ─── Poll job status ────────────────────────────────────────


async def get_job_status(job_id: str, job_token: Optional[str] = None) -> Optional[JobStatus]:
    """گرفتن وضعیت job از videotext.io"""
    url = f"{_API_BASE}/api/job/{job_id}"
    if job_token:
        url += f"?jobToken={job_token}"

    headers = dict(_DEFAULT_HEADERS)

    try:
        async with AsyncSession() as s:
            r = await s.get(url, impersonate="chrome", headers=headers, timeout=20)
            if r.status_code == 404:
                logger.warning("job %s not found (session expired)", job_id)
                return JobStatus(status="failed", error="Session expired")
            if r.status_code != 200:
                logger.warning("job status HTTP %d", r.status_code)
                return None
            d = r.json()
            status = JobStatus(
                status=d.get("status", "unknown"),
                progress=int(d.get("progress", 0) or 0),
                queue_position=d.get("queuePosition"),
                download_url=None,
                file_name=None,
            )
            # result field
            result = d.get("result")
            if isinstance(result, dict):
                dl = result.get("downloadUrl") or result.get("download_url")
                # اگه relative باشه، به absolute تبدیل کن
                if dl and dl.startswith("/"):
                    dl = _API_BASE + dl
                # اگه job_token لازم باشه به download URL اضافه کن
                if dl and "?jobToken=" not in dl and job_token:
                    dl += ("&" if "?" in dl else "?") + f"jobToken={job_token}"
                status.download_url = dl
                status.file_name = result.get("fileName") or result.get("file_name")
            return status
    except Exception as e:
        logger.warning("get_job_status failed: %s", e)
        return None


# ─── Wait for completion ────────────────────────────────────


async def wait_for_completion(
    job_id: str,
    job_token: Optional[str] = None,
    poll_interval: float = 1.5,        # از jobPolling: 1500ms
    timeout: float = 1800,             # 30 دقیقه max
    on_progress: Optional[Callable[[JobStatus], None]] = None,
) -> Optional[JobStatus]:
    """
    Poll تا completion یا failure.

    Returns:
        JobStatus نهایی یا None اگه timeout
    """
    start = asyncio.get_event_loop().time()
    last_progress = -1
    last_status = None

    while True:
        elapsed = asyncio.get_event_loop().time() - start
        if elapsed > timeout:
            logger.error("job %s timeout after %ds", job_id, int(elapsed))
            return None

        status = await get_job_status(job_id, job_token)
        if status is None:
            # خطای موقت - ادامه بده
            await asyncio.sleep(poll_interval)
            continue

        # progress callback فقط اگه تغییر کرده
        if status.progress != last_progress or status.status != last_status:
            if on_progress:
                on_progress(status)
            last_progress = status.progress
            last_status = status.status

        if status.status == "completed":
            logger.info("job %s completed", job_id)
            return status
        if status.status == "failed":
            logger.error("job %s failed: %s", job_id, status.error)
            return status

        await asyncio.sleep(poll_interval)


# ─── Download result ────────────────────────────────────────


async def download_result(
    download_url: str,
    out_path: str,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> Optional[str]:
    """
    دانلود ویدیو خروجی از videotext.io.

    Returns:
        path فایل ذخیره شده یا None
    """
    headers = dict(_DEFAULT_HEADERS)
    headers["Referer"] = "https://videotext.io/burn-subtitles"

    try:
        async with AsyncSession() as s:
            r = await s.get(download_url, impersonate="chrome", headers=headers, timeout=600,
                            stream=True)
            if r.status_code != 200:
                logger.error("download HTTP %d", r.status_code)
                return None

            total = int(r.headers.get("content-length", 0))
            written = 0
            with open(out_path, "wb") as f:
                async for chunk in r.aiter_content(chunk_size=1024 * 256):
                    f.write(chunk)
                    written += len(chunk)
                    if on_progress:
                        on_progress(written, total)
            logger.info("downloaded: %s (%d bytes)", out_path, written)
            return out_path
    except Exception as e:
        logger.error("download_result failed: %s", e)
        return None


# ─── Main entry ─────────────────────────────────────────────


async def burn_subtitles(
    video_path: str,
    subtitle_path: str,
    out_dir: str,
    options: Optional[BurnOptions] = None,
    user_id: str = _DEFAULT_USER_ID,
    plan: str = _DEFAULT_PLAN,
    on_upload_progress: Optional[Callable[[int], None]] = None,
    on_burn_progress: Optional[Callable[[JobStatus], None]] = None,
    on_download_progress: Optional[Callable[[int, int], None]] = None,
) -> Optional[str]:
    """
    اجرای کامل: آپلود + burn + دانلود.

    Returns:
        path ویدیوی نهایی با subtitle هاردکد شده، یا None
    """
    os.makedirs(out_dir, exist_ok=True)

    # 1. upload
    if on_upload_progress:
        on_upload_progress(0)
    result = await upload_dual(
        video_path=video_path,
        subtitle_path=subtitle_path,
        user_id=user_id,
        plan=plan,
        options=options,
        on_upload_progress=on_upload_progress,
    )
    if not result or not result.get("jobId"):
        logger.error("upload failed")
        return None

    job_id = result["jobId"]
    job_token = result.get("jobToken")

    # 2. wait for completion
    status = await wait_for_completion(
        job_id, job_token,
        on_progress=on_burn_progress,
    )
    if not status or status.status != "completed":
        logger.error("burn failed")
        return None

    # 3. download result
    if not status.download_url:
        logger.error("no download URL in completed job")
        return None

    file_name = status.file_name or f"burned_{os.path.basename(video_path)}"
    if not file_name.endswith(".mp4"):
        file_name = os.path.splitext(file_name)[0] + ".mp4"
    out_path = os.path.join(out_dir, file_name)

    return await download_result(
        status.download_url, out_path,
        on_progress=on_download_progress,
    )


# ─── Quick test ─────────────────────────────────────────────


async def _test():
    """تست با فایل‌های موجود"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    # اگه فایل ویدیو و subtitle موجود نیست، skip
    video_path = "/tmp/test_video.mp4"
    sub_path = "/tmp/test_sub.vtt"
    if not os.path.exists(video_path) or not os.path.exists(sub_path):
        print(f"Test files not found: {video_path}, {sub_path}")
        return

    def on_upload(p):
        print(f"\rupload: {p}%", end="", flush=True)

    def on_burn(s):
        msg = f"burn: {s.status} {s.progress}%"
        if s.queue_position:
            msg += f" (queue: {s.queue_position})"
        print(f"\r{msg}", end="", flush=True)

    def on_download(done, total):
        if total:
            pct = done * 100 // total
            print(f"\rdownload: {pct}% ({done}/{total})", end="", flush=True)

    print("=== Test burn_subtitles ===")
    out = await burn_subtitles(
        video_path=video_path,
        subtitle_path=sub_path,
        out_dir="/tmp",
        on_upload_progress=on_upload,
        on_burn_progress=on_burn,
        on_download_progress=on_download,
    )
    print()
    if out:
        sz = os.path.getsize(out)
        print(f"✅ saved: {out} ({sz/1024/1024:.1f} MB)")
    else:
        print("❌ failed")


if __name__ == "__main__":
    asyncio.run(_test())
