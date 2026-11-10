"""
madou_handler.py
────────────────
استخراج و دانلود ویدیو از madou.io (麻豆 — و میرورهای MacCMS مثل 9191md.me)

ساختار سایت (تست زنده 2026-11):
  - MacCMS v10 (苹果CMS) — مسیر پخش:
      /index.php/vod/play/id/{id}/sid/{sid}/nid/{nid}.html
  - داخل HTML صفحه‌ی پخش یه JSON سراسری هست:
      player_aaaa={"flag":"play","encrypt":0,"trysee":0,...,
        "vod_data":{"vod_name":"عنوان (چینی، unicode-escaped)"},
        "url":"https://t0.97img.com/a1001206/a.m3u8",
        "from":"mahua","server":"","note":"","id":"20026","sid":1,"nid":1}
  - url = مانیفست HLS (media playlist تک‌کیفیتی، سگمنت‌های ~۵ ثانیه‌ای)
  - سگمنت‌ها نسبی‌ان (a0.ts ...) و کنار همون m3u8 سرو می‌شن:
      https://t0.97img.com/a1001206/a0.ts  (video/mp2t)
  - CDN رفرر/کوکی نمی‌خواد (تست زنده) ولی برای احتیاط Referer ست می‌شه
  - سگمنت اولِ /z.ts قبل از #EXT-X-DISCONTINUITY، تیزر/تبلیغ ~۵ ثانیه‌ای
    خود سایت است — با ffmpeg copy سرجاش می‌مونه (حذفش ریسک خرابی صدا داره)

استراتژی:
  1) GET صفحه‌ی پخش با curl_cffi (fetch_html از _common) → پارس player_aaaa
     (بدون مرورگر — ساده و سریع؛ encrypt=0 توی تست زنده)
  2) دانلود HLS با yt-dlp (_common.download_m3u8) → اگه yt-dlp نبود یا
     شکست خورد، fallback به ffmpeg مستقیم (-c copy + aac_adtstoasc)
"""

import asyncio
import base64
import json
import logging
import os
import re
import shutil
import time
from typing import Awaitable, Callable, List, Optional, Tuple
from urllib.parse import urlparse, unquote

logger = logging.getLogger("MadouHandler")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_REFERER = "https://www.madou.io/"

# دامنه‌های مجاز صفحه‌ی پخش (میرور 9191md.me همون MacCMS است — تو player_aaaa
# خود سایت بهش اشاره شده)
_ALLOWED_HOSTS = frozenset({"madou.io", "www.madou.io", "9191md.me", "www.9191md.me"})
_ALLOWED_SUFFIXES = (".madou.io", ".9191md.me")

MAX_DOWNLOAD_SIZE = 50 * 1024 * 1024 * 1024
MIN_VALID_VIDEO_SIZE = 100 * 1024
PROGRESS_INTERVAL = 1.0

ProgressCallback = Callable[[str], Awaitable[None]]

# ⚠️ خود factory داخل bot.py سشن رو تو این دیکشنری نگه می‌داره — فقط باید
# موجود باشه (همین الگو تو hentaihaven/beeg هم هست)
madou_sessions: dict = {}


def is_madou_url(url: str) -> bool:
    """مچ کردن URLهای madou.io و میرورهاش (صفحه‌ی پخش/جزئیات MacCMS)."""
    try:
        host = urlparse(url).hostname or ""
        return (
            host in _ALLOWED_HOSTS
            or any(host.endswith(sfx) for sfx in _ALLOWED_SUFFIXES)
        )
    except Exception:
        return False


def _cleanup_file(filepath: str) -> None:
    try:
        if filepath and os.path.exists(filepath):
            os.remove(filepath)
    except OSError as e:
        logger.warning("cleanup %s: %s", filepath, e)


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024: return f"{size_bytes} B"
    if size_bytes < 1024*1024: return f"{size_bytes/1024:.1f} KB"
    if size_bytes < 1024*1024*1024: return f"{size_bytes/1024/1024:.1f} MB"
    return f"{size_bytes/1024/1024/1024:.2f} GB"


# ─── پارس player_aaaa ─────────────────────────────────────────────────────


def _parse_player_aaaa(html: str) -> Optional[dict]:
    """استخراج JSON متغیر سراسری player_aaaa از HTML صفحه‌ی پخش.

    regex ساده روی HTML های MacCMS معمولا جواب میده ولی برای اطمینان از
    آخرِ درستِ JSON، بریس‌ها رو متوازن می‌شماریم (رشته‌ها هم در نظر گرفته
    می‌شن تا {} داخل متنِ عنوان خرابش نکنه)."""
    idx = html.find("player_aaaa")
    if idx < 0:
        return None
    start = html.find("{", idx)
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, min(len(html), start + 200_000)):
        ch = html[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start:i + 1])
                except Exception as e:
                    logger.warning("player_aaaa JSON parse failed: %s", e)
                    return None
    return None


def _decode_stream_url(player: dict) -> str:
    """خروجی url طبق فیلد encrypt دیکود میشه (مکانیسم استاندارد MacCMS):
      0 = plaintext  |  1 = base64  |  2 = base64 تو در تو / urlencoded"""
    url = str(player.get("url") or "").strip()
    enc = 0
    try:
        enc = int(player.get("encrypt") or 0)
    except (TypeError, ValueError):
        pass
    if not url:
        return ""
    if enc == 1:
        try:
            url = base64.b64decode(url).decode("utf-8", "ignore").strip()
        except Exception:
            pass
    elif enc >= 2:
        for _ in range(2):
            try:
                candidate = base64.b64decode(unquote(url)).decode("utf-8", "ignore").strip()
                if candidate.lower().startswith(("http", "//")):
                    url = candidate
            except Exception:
                break
    if url.startswith("//"):
        url = "https:" + url
    return url


def _extract_title(player: dict, html: str) -> str:
    """عنوان: اول vod_data.vod_name از player_aaaa، بعد og:title / <title>."""
    try:
        name = str(((player.get("vod_data") or {}).get("vod_name")) or "").strip()
        if name:
            return name
    except Exception:
        pass
    m = re.search(
        r'(?:property|name)=["\']og:title["\']\s+content=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    m = re.search(r"<title>([^<]{2,150})</title>", html, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


# ─── Extraction (بدون مرورگر) ─────────────────────────────────────────────


async def extract_madou_qualities(url: str) -> Tuple[List[dict], str]:
    """استخراج کیفیت‌ها از صفحه‌ی پخش madou → ([sources], title)."""
    if not is_madou_url(url):
        return [], "Invalid URL"

    from ._common import fetch_html

    html, status = await fetch_html(
        url, referer=_REFERER, user_agent=_USER_AGENT, timeout=30,
    )
    if not html:
        return [], f"صفحه‌ی پخش باز نشد (HTTP {status})"

    player = _parse_player_aaaa(html)
    if not player:
        return [], "player_aaaa تو صفحه پیدا نشد (شاید لینک صفحه‌ی پخش نیست)"

    stream_url = _decode_stream_url(player)
    if not stream_url:
        return [], "لینک استریم داخل player_aaaa خالیه"

    if not stream_url.lower().startswith(("http://", "https://")):
        # نسبی → مطلق (بر اساس میزبان صفحه‌ی پخش)
        if stream_url.startswith("//"):
            stream_url = "https:" + stream_url
        elif stream_url.startswith("/"):
            p = urlparse(url)
            stream_url = f"{p.scheme}://{p.netloc}{stream_url}"
        else:
            from urllib.parse import urljoin as _urljoin
            stream_url = _urljoin(url, stream_url)

    title = _extract_title(player, html) or "madou_video"

    host = urlparse(stream_url).hostname or ""
    if not (host.endswith("97img.com") or is_madou_url(f"https://{host}")):
        # CDN ممکنه عوض شه — بلاک نمی‌کنیم، فقط تو لاگ می‌ذاریم
        logger.warning("madou stream on unexpected host: %s", host)

    if stream_url.lower().split("?")[0].endswith(".mp4"):
        sources = [{
            "label": "📺 MP4 (مستقیم)",
            "url": stream_url,
            "height": 0,
            "quality_key": "direct",
            "method": "direct",
            "is_hd": True,
        }]
    else:
        sources = [{
            "label": "📺 MP4 (HLS)",
            "url": stream_url,
            "height": 0,
            "quality_key": "hls",
            "method": "m3u8",
            "is_hd": True,
        }]

    logger.info("madou extract OK | title=%r | url=%s", title[:60], stream_url[:120])
    return sources, title


# ─── دانلود m3u8: yt-dlp → ffmpeg fallback ────────────────────────────────


async def _download_with_ffmpeg(
    video_url: str, filepath: str, progress_cb: ProgressCallback,
) -> Tuple[bool, str, int]:
    """دانلود HLS با خود ffmpeg (fallback وقتی yt-dlp نیست یا شکست خورد)."""
    if not shutil.which("ffmpeg"):
        return False, "ffmpeg not installed", 0

    await progress_cb("📥 **Downloading...**\n(هندلر Madou)")

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning"]
    is_http = video_url.lower().startswith(("http://", "https://"))
    if is_http:
        cmd += ["-user_agent", _USER_AGENT, "-referer", _REFERER]
    # ⚠️ اگه ورودی پلی‌لیست فایل محلی باشه، ffmpeg پیش‌فرض فقط
    # file/crypto/data رو مجاز می‌دونه → سگمنت‌های http/https دانلود
    # نمیشن («Protocol 'https' not on whitelist»)
    cmd += [
        "-protocol_whitelist", "file,http,https,tcp,tls,crypto",
        "-i", video_url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        "-movflags", "+faststart",
        filepath,
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )

        last_update = 0.0
        start_time = time.time()
        stderr_buffer: list = []

        async def _read_stderr():
            nonlocal last_update
            try:
                while True:
                    line = await process.stderr.readline()
                    if not line:
                        break
                    text = line.decode(errors="replace").strip()
                    if text:
                        stderr_buffer.append(text)
                    now = time.time()
                    if now - last_update >= PROGRESS_INTERVAL:
                        last_update = now
                        try:
                            current_size = (
                                os.path.getsize(filepath)
                                if os.path.exists(filepath) else 0
                            )
                        except OSError:
                            current_size = 0
                        elapsed = now - start_time
                        speed = current_size / elapsed if elapsed > 0 else 0
                        dl_mb = current_size / 1024 / 1024
                        speed_mb = min(speed / 1024 / 1024, 999)
                        # ⚠️ CancelledError از داخل progress_cb (دکمه‌ی لغو
                        # factory) از همین‌جا بالا میاد → kill + raise
                        try:
                            await progress_cb(
                                f"📥 **Downloading...**\n(هندلر Madou)\n"
                                f"💾 {dl_mb:.1f} MB  •  ⚡ {speed_mb:.1f} MB/s"
                            )
                        except asyncio.CancelledError:
                            process.kill()
                            raise
            except asyncio.CancelledError:
                process.kill()
                raise
            except Exception as e:
                logger.warning("stderr read error: %s", e)

        stderr_task = asyncio.create_task(_read_stderr())
        await process.wait()
        try:
            stderr_task.cancel()
        except Exception:
            pass

        if process.returncode != 0:
            err_tail = "\n".join(stderr_buffer[-5:])[:300]
            _cleanup_file(filepath)
            return False, f"ffmpeg failed: {err_tail[:200]}", 0

        file_size = os.path.getsize(filepath)
        if file_size < MIN_VALID_VIDEO_SIZE:
            _cleanup_file(filepath)
            return False, f"File too small ({file_size} bytes)", 0

        logger.info("madou ffmpeg DONE | size=%s", _format_size(file_size))
        return True, "", file_size

    except asyncio.CancelledError:
        _cleanup_file(filepath)
        raise
    except Exception as e:
        logger.error("madou ffmpeg error: %s", e)
        _cleanup_file(filepath)
        return False, str(e)[:200], 0


async def download_madou_m3u8(
    m3u8_url: str,
    filepath: str,
    progress_cb: ProgressCallback,
) -> Tuple[bool, str, int]:
    """دانلود HLS madou — اول yt-dlp (سرور)، fallback ffmpeg."""
    if not m3u8_url:
        return False, "Empty stream URL", 0

    from ._common import download_m3u8 as common_download_m3u8

    # مسیر اصلی: yt-dlp (روی سرور نصبه) — concurrent fragments، سریع
    success, error, size = await common_download_m3u8(
        m3u8_url, filepath, progress_cb,
        referer=_REFERER, user_agent=_USER_AGENT,
    )
    if success:
        return True, "", size

    logger.warning("madou yt-dlp path failed (%s) — ffmpeg fallback", (error or "")[:120])
    _cleanup_file(filepath)
    return await _download_with_ffmpeg(m3u8_url, filepath, progress_cb)


async def download_madou_direct(
    url: str,
    filepath: str,
    progress_cb: ProgressCallback,
) -> Tuple[bool, str, int]:
    """دانلود مستقیم mp4 (اگه player_aaaa لینک mp4 بده به‌جای m3u8)."""
    from ._common import download_direct

    success, error, size = await download_direct(
        url, filepath, progress_cb,
        referer=_REFERER, user_agent=_USER_AGENT,
        max_filesize=MAX_DOWNLOAD_SIZE,
    )
    if success and size >= MIN_VALID_VIDEO_SIZE:
        return True, "", size
    _cleanup_file(filepath)
    return False, error or "Download failed", 0


# ─── Self-test ─────────────────────────────────────────────────────────────


async def _self_test():
    url = "https://www.madou.io/index.php/vod/play/id/20026/sid/1/nid/1.html"
    print(f"\n{'═' * 80}\nSelf-test: {url}\n{'═' * 80}\n")

    sources, title = await extract_madou_qualities(url)
    print(f"  Title: {title}")
    for s in sources:
        print(f"    [{s['quality_key']:8s}] {s['method']:7s} {s['url'][:120]}")
    return sources, title


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    asyncio.run(_self_test())
