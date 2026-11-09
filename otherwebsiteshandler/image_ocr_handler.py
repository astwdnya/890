"""
image_ocr_handler.py
────────────────────
هندلر برای استخراج متن از تصویر با استفاده از API سایت imagetotext.info.

روش کار:
  1. کاربر عکس می‌فرسته (عادی یا document)
  2. ربات دکمه شیشه‌ای "📖 Extract text from image" نشون می‌ده
  3. وقتی کاربر کلیک کنه:
     a. Homepage سایت رو fetch می‌کنه (برای CSRF token + cookies)
     b. captcha-verify رو صدا می‌زنه (با static hash)
     c. req_key و verify_key رو از پاسخ می‌گیره
     d. تصویر رو به base64 (با data URI prefix) تبدیل می‌کنه
     e. POST به /free-image-to-text می‌فرسته
     f. متن استخراج‌شده رو به کاربر می‌فرسته

نکته: هیچ ماژول OCR محلی استفاده نمی‌شه - کاملاً از API سایت استفاده می‌شه.

محدودیت روزانه و راه دور زدن:
  - سایت محدودیت روزانه IP داره (free users: ~15 images/day)
  - با ارسال هدر X-Forwarded-For و X-Real-IP با IP‌های تصادفی،
    محدودیت دور زده می‌شه - هر درخواست با IP متفاوت ارسال می‌شه.
  - سایت هدرهای X-Forwarded-For رو قبول می‌کنه (تأیید شده با تست).
"""

import asyncio
import base64
import logging
import os
import random
import re
import string
import time
from typing import Optional, Tuple

logger = logging.getLogger("ImageOCR")

_SITE_URL = "https://www.imagetotext.info/"
_API_OCR_URL = "https://www.imagetotext.info/free-image-to-text"
_CAPTCHA_VERIFY_URL = "https://www.imagetotext.info/emd/captcha-verify/"
_CAPTCHA_HASH = "1nRoesLngnKKQfAIl5NubyRZXWIcLEUXlKOSJI539G"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def _generate_req_key() -> str:
    """تولید req_key (مشابه emdGenerateReqKey در JS)."""
    timestamp = str(int(time.time() * 1000))
    random_chars = ''.join(random.choices(string.ascii_lowercase + string.digits, k=20))
    return timestamp + random_chars


def _random_ip() -> str:
    """تولید IP تصادفی برای دور زدن محدودیت روزانه."""
    return f"{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}"


def _image_to_data_uri(image_path: str) -> str:
    """تبدیل فایل تصویر به data URI (base64)."""
    # تشخیص MIME type
    ext = os.path.splitext(image_path)[1].lower()
    mime_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".tiff": "image/tiff",
        ".tif": "image/tiff",
    }
    mime = mime_map.get(ext, "image/png")
    
    with open(image_path, "rb") as f:
        img_data = f.read()
    
    b64 = base64.b64encode(img_data).decode()
    return f"data:{mime};base64,{b64}"


async def extract_text_from_image(image_path: str) -> Tuple[bool, str]:
    """
    استخراج متن از تصویر با استفاده از API imagetotext.info.

    Args:
        image_path: مسیر فایل تصویر

    Returns:
        Tuple (success, text_or_error)
    """
    try:
        from curl_cffi.requests import AsyncSession
        from curl_cffi import CurlMime
    except ImportError:
        return False, "curl_cffi not available"

    try:
        async with AsyncSession() as session:
            # Step 1: Fetch homepage to get CSRF token + cookies
            logger.info("[OCR] Fetching homepage...")
            r = await session.get(
                _SITE_URL,
                impersonate="chrome",
                headers={"User-Agent": _UA},
                timeout=20,
                verify=False,
            )
            if r.status_code != 200:
                return False, f"Failed to fetch homepage (HTTP {r.status_code})"

            # Extract CSRF token
            m = re.search(r'name="_token"\s+content="([^"]+)"', r.text)
            if not m:
                m = re.search(r'content="([^"]+)"\s+name="_token"', r.text)
            if not m:
                return False, "Could not extract CSRF token"
            csrf_token = m.group(1)
            logger.info("[OCR] CSRF token: %s", csrf_token[:20] + "...")

            # Step 2: Captcha verify (با IP تصادفی برای دور زدن محدودیت روزانه)
            logger.info("[OCR] Calling captcha-verify...")
            fake_ip = _random_ip()
            captcha_ts = str(int(time.time() * 1000))
            r2 = await session.post(
                f"{_CAPTCHA_VERIFY_URL}{captcha_ts}",
                impersonate="chrome",
                headers={
                    "User-Agent": _UA,
                    "Referer": _SITE_URL,
                    "X-CSRF-TOKEN": csrf_token,
                    "X-Requested-With": "XMLHttpRequest",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-Forwarded-For": fake_ip,
                    "X-Real-IP": fake_ip,
                },
                data={
                    "emd_captcha_hash": _CAPTCHA_HASH,
                    "emd_captcha_token": "",
                    "emd_is_tool_premium": "0",
                },
                timeout=30,
                verify=False,
            )

            if r2.status_code != 200:
                return False, f"Captcha verify failed (HTTP {r2.status_code})"

            try:
                captcha_data = r2.json()
            except Exception:
                return False, "Captcha verify returned invalid JSON"

            if not captcha_data.get("request"):
                return False, f"Captcha verify rejected: {captcha_data.get('message', 'unknown')}"

            req_key = captcha_data.get("req_key", "")
            verify_key = captcha_data.get("verify_key", "")
            logger.info("[OCR] req_key: %s, verify_key: %s", req_key[:20] + "...", verify_key[:20] + "...")

            if not req_key:
                return False, "No req_key in captcha response"

            # Step 3: Convert image to data URI
            logger.info("[OCR] Converting image to base64...")
            img_data_uri = _image_to_data_uri(image_path)
            img_size = os.path.getsize(image_path)

            # Get image dimensions
            try:
                from PIL import Image as PILImage
                with PILImage.open(image_path) as img:
                    width, height = img.size
                dimension = f"{width}x{height}"
            except Exception:
                dimension = "0x0"

            # Step 4: Send OCR request
            logger.info("[OCR] Sending OCR request...")
            multipart = CurlMime()
            multipart.addpart(name="base64", data=img_data_uri)
            multipart.addpart(name="count", data="1")
            multipart.addpart(name="_token", data=csrf_token)
            multipart.addpart(name="req_key", data=req_key)
            multipart.addpart(name="verify_key", data=verify_key)
            multipart.addpart(name="e_track_key", data=_generate_req_key())
            multipart.addpart(name="tool_id", data="1")
            multipart.addpart(name="parent_id", data="114")
            multipart.addpart(name="tool_key", data="image_to_text")
            multipart.addpart(name="dimension", data=dimension)
            multipart.addpart(name="size", data=f"{img_size / (1024 * 1024):.2f} MB")
            multipart.addpart(name="name", data=os.path.basename(image_path))
            multipart.addpart(name="ocr_mode", data="simple_ocr")
            multipart.addpart(name="fetchUrl", data="false")

            r3 = await session.post(
                _API_OCR_URL,
                impersonate="chrome",
                headers={
                    "User-Agent": _UA,
                    "Referer": _SITE_URL,
                    "X-CSRF-TOKEN": csrf_token,
                    "X-Requested-With": "XMLHttpRequest",
                    "X-Forwarded-For": fake_ip,
                    "X-Real-IP": fake_ip,
                },
                multipart=multipart,
                timeout=60,
                verify=False,
            )

            if r3.status_code != 200:
                # Check for rate limit / IP block
                try:
                    err_data = r3.json()
                    if "IP Blocked" in str(err_data.get("message", "")):
                        return False, "محدودیت روزانه IP رسید. فردا دوباره تلاش کن."
                except Exception:
                    pass
                return False, f"OCR request failed (HTTP {r3.status_code})"

            try:
                result = r3.json()
            except Exception:
                return False, "OCR returned invalid JSON"

            if result.get("error"):
                api_status = result.get("api_status", "?")
                return False, f"OCR error (api_status={api_status})"

            text = result.get("text", "").strip()
            # Clean up HTML tags and BOM
            text = text.replace("\ufeff", "").replace("\r", "")
            text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
            text = text.replace("<br />", "\n").strip()

            if not text:
                return False, "No text found in image"

            logger.info("[OCR] Success! Text length: %d", len(text))
            return True, text

    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("[OCR] Error: %s", e, exc_info=True)
        return False, f"Error: {str(e)[:200]}"
