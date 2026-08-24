"""
photoroom_handler.py
────────────────────
هندلر برای حذف پس‌زمینه عکس با کیفیت بالا (PNG با شفافیت).

بررسی مهندسی معکوس سایت photoroom.com/tools/background-remover
────────────────────────────────────────────────────────────
سایت PhotoRoom از endpoint زیر برای حذف پس‌زمینه استفاده می‌کنه:

    POST https://sdk.photoroom.com/v1/segment

    Headers:
        x-api-key: 10148f33e3f8d09a9b9aa6b775372a4ebf18b938   (هاردکد شده توی JS کلاینت)
        x-captcha: CLOUDFLARE_<Turnstile_token>               (توکن Cloudflare Turnstile)

    Body (multipart/form-data):
        image_file: <binary image data, image/png|jpeg|webp>

    Response:
        Body: PNG با شفافیت (image/png)
        Headers:
            x-foreground-top, x-foreground-left,
            x-foreground-width, x-foreground-height  (مختصات کراپ foreground)

    Cloudflare Turnstile sitekey استفاده‌شده:
        0x4AAAAAAAApeO5gC2AwBbrW
        (ویجت با appearance: "interaction-only" رندر می‌شه)

به دلیل محدودیت Turnstile:
    -   توکن Turnstile برای هر request لازمه و فقط با اجرای JS توی مرورگر تولید می‌شه.
    -   سرور دیتاسنتر ما همیشه توسط Cloudflare در مرحله PoW (PAT) رد می‌شه
        (همان /cdn-cgi/challenge-platform/h/b/pat/ که 401 برمی‌گردونه).
    -   بنابراین API مستقیم PhotoRoom از این سرور قابل استفاده نیست.

راه‌حل جایگزین (pure-Python، بدون Playwright، بدون کپچا):
    از rembg با مدل U2Net استفاده می‌کنیم. این روش:
        ✅ کاملاً pure-Python (هیچ Playwright لازم نیست)
        ✅ بدون کپچا، بدون rate limit، بدون API key
        ✅ مدل دفعه اول دانلود می‌شه (u2net.onnx ~176MB) و کش می‌شه
        ✅ خروجی PNG با شفافیت
        ✅ کیفیت خوب برای اکثر عکس‌ها

روند کار:
    1. کاربر عکس می‌فرسته
    2. ربات دکمه شیشه‌ای "🗑 Remove Background" رو نشون می‌ده
    3. وقتی کلیک کنه، ربات می‌پرسه: «استیکر بفرستم یا فایل PNG؟»
    4. کاربر یکی رو انتخاب می‌کنه
    5. ربات عکس رو با rembg پردازش می‌کنه و به همون فرمت می‌فرسته
"""

import asyncio
import logging
import os
import threading
from typing import Optional, Tuple

logger = logging.getLogger("Photoroom")

# ─── مدل U2Net کش می‌شه تا هر بار لود نشه ───
_rembg_session = None
_rembg_session_lock = threading.Lock()
_rembg_initializing = False


def _get_rembg_session():
    """Get or initialize the cached U2Net session."""
    global _rembg_session, _rembg_initializing
    if _rembg_session is not None:
        return _rembg_session

    with _rembg_session_lock:
        if _rembg_session is not None:
            return _rembg_session
        if _rembg_initializing:
            # Wait for another thread to finish
            while _rembg_initializing:
                pass
            return _rembg_session

        _rembg_initializing = True
        try:
            logger.info("[Photoroom] Initializing U2Net model (first-time download ~176MB)...")
            from rembg import new_session
            _rembg_session = new_session(model_name="u2net")
            logger.info("[Photoroom] U2Net model loaded successfully.")
            return _rembg_session
        except Exception as e:
            logger.error(f"[Photoroom] Failed to load U2Net model: {e}", exc_info=True)
            return None
        finally:
            _rembg_initializing = False


async def remove_background(image_path: str, output_path: Optional[str] = None) -> Tuple[bool, str]:
    """
    حذف پس‌زمینه عکس و بازگرداندن فایل PNG با شفافیت.

    Args:
        image_path: مسیر عکس ورودی (jpg, png, webp, ...)
        output_path: مسیر خروجی (اختیاری؛ اگه None باشه،
                     از نام فایل ورودی + "_nobg.png" استفاده می‌شه)

    Returns:
        Tuple (success, output_path_or_error_message)
    """
    if not os.path.exists(image_path):
        return False, f"Input file not found: {image_path}"

    if output_path is None:
        base, _ = os.path.splitext(image_path)
        output_path = base + "_nobg.png"

    try:
        # Get the cached session (this will block on first call ~5-10 sec after model download)
        session = await asyncio.get_event_loop().run_in_executor(None, _get_rembg_session)
        if session is None:
            return False, "U2Net model failed to load. Check the logs."

        from rembg import remove as _remove

        # Read input image bytes
        with open(image_path, "rb") as f:
            input_bytes = f.read()

        logger.info("[Photoroom] Removing background (input: %d bytes)...", len(input_bytes))

        # Run the (CPU-bound) removal in a thread pool so we don't block the event loop
        def _do_remove():
            return _remove(input_bytes, session=session)

        output_bytes = await asyncio.get_event_loop().run_in_executor(None, _do_remove)

        if not output_bytes or len(output_bytes) < 100:
            return False, "Background removal produced an empty result."

        # Verify it's a valid PNG (starts with PNG magic)
        if output_bytes[:8] != b"\x89PNG\r\n\x1a\n":
            logger.warning("[Photoroom] Output doesn't start with PNG magic; got: %r", output_bytes[:16])
            # Still save it but warn

        with open(output_path, "wb") as f:
            f.write(output_bytes)

        logger.info("[Photoroom] Background removed successfully: %s (%d bytes)", output_path, len(output_bytes))
        return True, output_path

    except ImportError as e:
        logger.error("[Photoroom] rembg not installed: %s", e)
        return False, "rembg library not available. Install with: pip install 'rembg[cpu]'"
    except Exception as e:
        logger.error("[Photoroom] Error removing background: %s", e, exc_info=True)
        return False, f"Error: {str(e)[:200]}"


async def remove_background_to_sticker(image_path: str, output_path: Optional[str] = None) -> Tuple[bool, str]:
    """
    حذف پس‌زمینه و آماده‌سازی به‌عنوان استیکر Telegram.

    Telegram stickers باید 512x512 (با حفظ aspect ratio) و فرمت PNG یا WebP باشن.
    ربات از ماکسیمم 512x512 (یک ضلع 512، ضلع دیگه متناسب) استفاده می‌کنه.

    Returns:
        Tuple (success, sticker_path)
    """
    success, result = await remove_background(image_path, output_path)
    if not success:
        return False, result

    try:
        from PIL import Image
        img = Image.open(result)
        if img.mode != "RGBA":
            img = img.convert("RGBA")

        # Telegram sticker rule: max 512 on one side, the other side ≤ 512
        # We make the LONGER side = 512, the shorter side proportionally smaller
        w, h = img.size
        if w >= h:
            new_w = 512
            new_h = max(1, int(round(h * 512 / w)))
        else:
            new_h = 512
            new_w = max(1, int(round(w * 512 / h)))

        img_resized = img.resize((new_w, new_h), Image.LANCZOS)

        sticker_path = os.path.splitext(result)[0] + "_sticker.png"
        img_resized.save(sticker_path, format="PNG", optimize=True)
        logger.info("[Photoroom] Sticker saved: %s (%dx%d)", sticker_path, new_w, new_h)
        return True, sticker_path
    except Exception as e:
        logger.error("[Photoroom] Sticker conversion error: %s", e, exc_info=True)
        return False, f"Sticker conversion error: {str(e)[:200]}"


# ─── تابع کمکی برای کوکی/توکن PhotoRoom (در صورت نیاز در آینده) ───
PHOTOROOM_API_URL = "https://sdk.photoroom.com/v1/segment"
PHOTOROOM_API_KEY = "10148f33e3f8d09a9b9aa6b775372a4ebf18b938"
PHOTOROOM_TURNSTILE_SITEKEY = "0x4AAAAAAAApeO5gC2AwBbrW"


async def remove_background_photoroom_api(image_path: str, captcha_token: str) -> Tuple[bool, str]:
    """
    (در صورت داشتن توکن Turnstile معتبر) فراخوانی مستقیم API واقعی PhotoRoom.

    این تابع در حال حاضر به دلیل عدم دسترسی به توکن Turnstile از سرور ما
    استفاده نمی‌شه ولی برای پشتیبانی آینده (اگه سرویس حل کپچا اضافه بشه)
    باقی موند. برای استفاده:

        success, path = await remove_background_photoroom_api(img_path, token)

    Args:
        image_path: مسیر عکس ورودی
        captcha_token: توکن Cloudflare Turnstile (بدون پیشوند CLOUDFLARE_)

    Returns:
        Tuple (success, output_path_or_error)
    """
    try:
        from curl_cffi import CurlMime
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return False, "curl_cffi not available"

    UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

    try:
        with open(image_path, "rb") as f:
            img = f.read()

        mp = CurlMime()
        mp.addpart(
            name="image_file",
            content_type="image/png",
            filename="image.png",
            data=img,
        )

        async with AsyncSession() as s:
            r = await s.post(
                PHOTOROOM_API_URL,
                headers={
                    "User-Agent": UA,
                    "Origin": "https://www.photoroom.com",
                    "Referer": "https://www.photoroom.com/",
                    "x-api-key": PHOTOROOM_API_KEY,
                    "x-captcha": f"CLOUDFLARE_{captcha_token}",
                },
                multipart=mp,
                impersonate="chrome",
                timeout=60,
                verify=False,
            )

            if r.status_code == 200 and r.content:
                output_path = os.path.splitext(image_path)[0] + "_photoroom_nobg.png"
                with open(output_path, "wb") as f:
                    f.write(r.content)
                logger.info("[Photoroom-API] Real PhotoRoom API success: %s (%d bytes)",
                            output_path, len(r.content))
                return True, output_path

            err = r.content[:300].decode("utf-8", "replace")
            logger.error("[Photoroom-API] API error: HTTP %s | %s", r.status_code, err)
            return False, f"PhotoRoom API HTTP {r.status_code}: {err[:200]}"

    except Exception as e:
        logger.error("[Photoroom-API] Error: %s", e, exc_info=True)
        return False, f"Error: {str(e)[:200]}"
