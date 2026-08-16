"""
file_server.py
──────────────
هاست کردن فایل‌ها روی سرور بات برای ۶ ساعت + ارائه‌ی لینک مستقیم قابل play در VLC.

استراتژی:
- فایل‌ها در پوشه‌ی `/tmp/bot_files/` ذخیره می‌شن.
- هر فایل یه token (UUID) می‌گیره.
- endpoint ها:
    GET /f/<token>           → سرو فایل با پشتیبانی از Range (برای seek در VLC)
    GET /f/<token>/info      → اطلاعات فایل (نام، حجم، expiry)
    GET /f/<token>/play.m3u8 → تولید مini HLS playlist برای play در VLC (برای softsub)
- بعد از ۶ ساعت، فایل خودکار حذف می‌شه.

استفاده:
    from file_server import serve_file, get_file_info, cleanup_expired

    token = await serve_file('/path/to/video.mp4', expires_in_hours=6, title='Movie')
    # → returns 'https://your-host/f/abc123'
"""
import asyncio
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, asdict
from typing import Optional, Dict
from urllib.parse import quote

logger = logging.getLogger("FileServer")

# ─── State ──────────────────────────────────────────────────
_FILES: Dict[str, dict] = {}  # token → {path, original_name, expires_at, content_type, title}
_LOCK = threading.Lock()
_CLEANUP_STARTED = False

# Path where served files live (symlinks or copies)
_STORAGE_DIR = "/tmp/bot_files"
os.makedirs(_STORAGE_DIR, exist_ok=True)


def _ensure_cleanup_started():
    """شروع background cleanup thread."""
    global _CLEANUP_STARTED
    if _CLEANUP_STARTED:
        return
    _CLEANUP_STARTED = True

    def _cleanup_loop():
        while True:
            try:
                time.sleep(60)  # هر دقیقه
                now = time.time()
                with _LOCK:
                    expired = [t for t, v in _FILES.items() if v["expires_at"] < now]
                    for t in expired:
                        v = _FILES.pop(t)
                        # اگه فایل symlink هست، فقط symlink رو پاک کن (نه فایل اصلی)
                        path = v["path"]
                        try:
                            if os.path.islink(path):
                                os.unlink(path)
                            elif os.path.exists(path):
                                # اگه کپی شده، پاک کن
                                os.unlink(path)
                        except Exception:
                            pass
                logger.info("[FileServer] Cleanup: removed %d expired files", len(expired))
            except Exception as e:
                logger.warning("[FileServer] Cleanup error: %s", e)

    t = threading.Thread(target=_cleanup_loop, daemon=True)
    t.start()


def _guess_content_type(filename: str) -> str:
    """تشخیص content-type از پسوند فایل."""
    ext = os.path.splitext(filename)[1].lower()
    return {
        ".mp4": "video/mp4",
        ".mkv": "video/x-matroska",
        ".webm": "video/webm",
        ".avi": "video/x-msvideo",
        ".mov": "video/quicktime",
        ".m4v": "video/x-m4v",
        ".ts": "video/mp2t",
        ".m3u8": "application/vnd.apple.mpegurl",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".ogg": "audio/ogg",
        ".wav": "audio/wav",
        ".srt": "application/x-subrip",
        ".vtt": "text/vtt",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".pdf": "application/pdf",
        ".zip": "application/zip",
        ".tar": "application/x-tar",
        ".gz": "application/gzip",
    }.get(ext, "application/octet-stream")


def serve_file(
    file_path: str,
    title: str = "",
    expires_in_hours: float = 6.0,
    public_base_url: Optional[str] = None,
    copy: bool = False,
) -> dict:
    """
    ثبت فایل برای سرو شدن به مدت expires_in_hours.

    Args:
        file_path: مسیر فایل روی دیسک
        title: عنوان نمایشی (مثلاً "Inception 2010")
        expires_in_hours: مدت زمان سرو شدن
        public_base_url: URL پایه عمومی (مثلاً 'https://my-bot.example.com')
            اگه None باشه، از متغیر محیطی PUBLIC_BASE_URL استفاده می‌شه.
        copy: اگه True، فایل کپی می‌شه (به‌جای symlink). برای فایل‌های موقت مناسب‌تره.

    Returns:
        dict با فیلدهای:
        - token: UUID
        - url: URL عمومی فایل
        - info_url: URL اطلاعات فایل
        - expires_at: timestamp انقضا
        - size: اندازه فایل (bytes)
    """
    _ensure_cleanup_started()
    if not os.path.exists(file_path):
        raise FileNotFoundError(file_path)

    token = uuid.uuid4().hex[:16]
    storage_path = os.path.join(_STORAGE_DIR, f"{token}_{os.path.basename(file_path)}")

    # ترجیح: symlink (سریع، بدون کپی)
    # اگه فایل روی همون filesystem باشه، symlink کن
    try:
        if not copy:
            os.symlink(os.path.abspath(file_path), storage_path)
        else:
            # کپی فایل
            import shutil
            shutil.copy2(file_path, storage_path)
    except OSError:
        # اگه symlink نشد (مثلاً cross-filesystem)، کپی کن
        import shutil
        if os.path.exists(storage_path):
            os.unlink(storage_path)
        shutil.copy2(file_path, storage_path)

    expires_at = time.time() + (expires_in_hours * 3600)
    original_name = os.path.basename(file_path)
    content_type = _guess_content_type(original_name)

    with _LOCK:
        _FILES[token] = {
            "path": storage_path,
            "original_name": original_name,
            "title": title or original_name,
            "expires_at": expires_at,
            "content_type": content_type,
            "size": os.path.getsize(storage_path),
        }

    base = public_base_url or os.environ.get("PUBLIC_BASE_URL", "")
    if not base:
        # Fallback — به‌جای URL عمومی، فقط token رو برگردون
        url = f"/f/{token}"
        info_url = f"/f/{token}/info"
        play_url = f"/f/{token}/play.m3u8"
    else:
        base = base.rstrip("/")
        url = f"{base}/f/{token}"
        info_url = f"{base}/f/{token}/info"
        play_url = f"{base}/f/{token}/play.m3u8"

    logger.info("[FileServer] Registered token=%s, file=%s, expires_in=%.1fh",
                token, original_name, expires_in_hours)
    return {
        "token": token,
        "url": url,
        "info_url": info_url,
        "play_url": play_url,
        "expires_at": expires_at,
        "size": _FILES[token]["size"],
    }


def get_file_info(token: str) -> Optional[dict]:
    """گرفتن اطلاعات فایل با token."""
    with _LOCK:
        v = _FILES.get(token)
        if not v:
            return None
        if time.time() > v["expires_at"]:
            return None
        return {
            "token": token,
            "title": v["title"],
            "original_name": v["original_name"],
            "size": v["size"],
            "content_type": v["content_type"],
            "expires_at": v["expires_at"],
            "expires_in_seconds": int(v["expires_at"] - time.time()),
        }


def delete_file(token: str) -> bool:
    """حذف دستی فایل قبل از انقضا."""
    with _LOCK:
        v = _FILES.pop(token, None)
        if not v:
            return False
    try:
        if os.path.islink(v["path"]):
            os.unlink(v["path"])
        elif os.path.exists(v["path"]):
            os.unlink(v["path"])
        return True
    except Exception as e:
        logger.warning("[FileServer] Failed to delete file for token=%s: %s", token, e)
        return False


def get_served_file(token: str) -> Optional[dict]:
    """برای Flask handler: گرفتن info فایل + path. اگه انقضا شده یا موجود نبود، None."""
    with _LOCK:
        v = _FILES.get(token)
        if not v:
            return None
        if time.time() > v["expires_at"]:
            # انقضا شده — پاک کن
            _FILES.pop(token, None)
            try:
                if os.path.islink(v["path"]) or os.path.exists(v["path"]):
                    os.unlink(v["path"])
            except Exception:
                pass
            return None
        return v


def register_flask_routes(flask_app):
    """ثبت endpointهای سرو فایل روی Flask app موجود."""
    from flask import Response, request, jsonify, send_file, abort
    import mimetypes

    @flask_app.route("/f/<token>")
    def serve_file_route(token):
        v = get_served_file(token)
        if not v:
            return abort(404, description="File not found or expired")
        if not os.path.exists(v["path"]):
            return abort(404, description="File deleted from disk")

        # Range header support for seek in VLC
        range_header = request.headers.get("Range", "")

        # File size
        file_size = os.path.getsize(v["path"])

        if range_header:
            # Parse Range: bytes=0-1023
            m = re.match(r"bytes=(\d*)-(\d*)", range_header)
            if m:
                start = int(m.group(1)) if m.group(1) else 0
                end = int(m.group(2)) if m.group(2) else file_size - 1
                if end >= file_size:
                    end = file_size - 1
                length = end - start + 1

                def generate():
                    with open(v["path"], "rb") as f:
                        f.seek(start)
                        remaining = length
                        while remaining > 0:
                            chunk = f.read(min(64 * 1024, remaining))
                            if not chunk:
                                break
                            remaining -= len(chunk)
                            yield chunk

                resp = Response(
                    generate(),
                    206,
                    mimetype=v["content_type"],
                    direct_passthrough=True,
                )
                resp.headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
                resp.headers["Accept-Ranges"] = "bytes"
                resp.headers["Content-Length"] = str(length)
                resp.headers["Content-Disposition"] = f'inline; filename="{quote(v["original_name"])}"'
                return resp

        # Full file (no range)
        def generate_full():
            with open(v["path"], "rb") as f:
                while True:
                    chunk = f.read(64 * 1024)
                    if not chunk:
                        break
                    yield chunk

        resp = Response(
            generate_full(),
            200,
            mimetype=v["content_type"],
            direct_passthrough=True,
        )
        resp.headers["Accept-Ranges"] = "bytes"
        resp.headers["Content-Length"] = str(file_size)
        resp.headers["Content-Disposition"] = f'inline; filename="{quote(v["original_name"])}"'
        return resp

    @flask_app.route("/f/<token>/info")
    def file_info_route(token):
        info = get_file_info(token)
        if not info:
            return jsonify({"error": "not found or expired"}), 404
        return jsonify(info)

    @flask_app.route("/f/<token>/play.m3u8")
    def play_m3u8_route(token):
        """تولید mini HLS playlist برای play در VLC (به‌جای URL مستقیم).
        این برای فایل‌های MKV/MP4 که VLC می‌تونه play کنه استفاده می‌شه."""
        v = get_served_file(token)
        if not v:
            return abort(404, description="File not found or expired")

        base = request.host_url.rstrip("/")
        file_url = f"{base}/f/{token}"

        # HLS playlist ساده برای یه فایل MP4/MKV
        m3u8_content = f"""#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-PLAYLIST-TYPE:VOD
#EXTINF:10.0,
{file_url}
#EXT-X-ENDLIST
"""
        # البته این یه playlist غیر واقعیه — VLC فقط یه chunk رو می‌بینه.
        # برای play واقعی، URL مستقیم /f/<token> رو به کاربر بده.

        # بازگشت متن ساده برای نسخه‌ی demo:
        return Response(m3u8_content, mimetype="application/vnd.apple.mpegurl")

    logger.info("[FileServer] Registered Flask routes: /f/<token>, /f/<token>/info, /f/<token>/play.m3u8")


# ─── Self-test ─────────────────────────────────────────────

if __name__ == "__main__":
    # Test
    import tempfile
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
    tmp.write(b"fake video content")
    tmp.close()

    info = serve_file(tmp.name, title="Test Movie", expires_in_hours=0.01)
    print("Registered:", info)
    print("File info:", get_file_info(info["token"]))
    print("\nCleanup test...")
    time.sleep(2)
    print("After expiry:", get_file_info(info["token"]))
