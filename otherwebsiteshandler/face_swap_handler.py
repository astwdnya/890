"""
face_swap_handler.py
────────────────────
هندلر برای Face Swap با استفاده از API سایت remaker.ai.

روش کار:
  1. کاربر عکس می‌فرسته
  2. ربات دکمه شیشه‌ای "🎭 Face Swap" نشون می‌ده
  3. وقتی کلیک کنه، ربات درخواست عکس face می‌کنه
  4. کاربر عکس face رو می‌فرسته
  5. ربات هر دو عکس رو به API remaker.ai می‌فرسته
  6. نتیجه face swap رو به کاربر می‌فرسته

API: remaker.ai (completely free, no auth needed, just need random Product-Serial)
"""

import asyncio
import logging
import os
import uuid
from typing import Optional, Tuple

logger = logging.getLogger("FaceSwap")

_API_BASE = "https://api.remaker.ai"
_CREATE_JOB_URL = f"{_API_BASE}/api/pai/v3/ai-facevary/appapi/create-job"
_GET_JOB_URL = f"{_API_BASE}/api/pai/v3/ai-facevary/appapi/get-job"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


async def face_swap(target_image_path: str, swap_image_path: str) -> Tuple[bool, str]:
    """
    انجام Face Swap با API remaker.ai.

    Args:
        target_image_path: مسیر عکس اصلی (که face قراره عوض بشه)
        swap_image_path: مسیر عکس face (face جدید)

    Returns:
        Tuple (success, result_path_or_error)
    """
    try:
        from curl_cffi.requests import AsyncSession
        from curl_cffi import CurlMime
    except ImportError:
        return False, "curl_cffi not available"

    try:
        async with AsyncSession() as session:
            # Step 1: Visit homepage for cookies
            logger.info("[FaceSwap] Fetching homepage...")
            r = await session.get(
                "https://remaker.ai/face-swap-free/",
                impersonate="chrome",
                headers={"User-Agent": _UA},
                timeout=20,
                verify=False,
            )
            if r.status_code != 200:
                return False, f"Failed to fetch homepage (HTTP {r.status_code})"

            # Step 2: Create job
            logger.info("[FaceSwap] Creating job...")
            product_serial = uuid.uuid4().hex

            with open(target_image_path, "rb") as f:
                target_data = f.read()
            with open(swap_image_path, "rb") as f:
                swap_data = f.read()

            multipart = CurlMime()
            multipart.addpart(name="target_image", content_type="image/jpeg",
                            filename="target.jpg", data=target_data)
            multipart.addpart(name="swap_image", content_type="image/jpeg",
                           filename="swap.jpg", data=swap_data)

            r2 = await session.post(
                _CREATE_JOB_URL,
                impersonate="chrome",
                headers={
                    "User-Agent": _UA,
                    "Referer": "https://remaker.ai/face-swap-free/",
                    "source": "ai_face_vary",
                    "Product-Code": "067003",
                    "Product-Serial": product_serial,
                    "Origin": "https://remaker.ai",
                },
                multipart=multipart,
                timeout=60,
                verify=False,
            )

            if r2.status_code != 200:
                return False, f"Create job failed (HTTP {r2.status_code})"

            try:
                data = r2.json()
            except Exception:
                return False, "Create job returned invalid JSON"

            if data.get("code") != 100000:
                msg = data.get("message", {})
                err_msg = msg.get("en", str(msg)) if isinstance(msg, dict) else str(msg)
                return False, f"Create job error: {err_msg}"

            job_id = data["result"]["job_id"]
            logger.info("[FaceSwap] Job created: %s", job_id)

            # Step 3: Poll for result
            logger.info("[FaceSwap] Waiting for result...")
            for attempt in range(60):  # 5 minutes max
                await asyncio.sleep(5)

                r3 = await session.get(
                    f"{_GET_JOB_URL}/{job_id}",
                    impersonate="chrome",
                    headers={
                        "User-Agent": _UA,
                        "Referer": "https://remaker.ai/face-swap-free/",
                        "source": "ai_face_vary",
                        "Product-Code": "067003",
                    },
                    timeout=30,
                    verify=False,
                )

                try:
                    result = r3.json()
                except Exception:
                    continue

                code = result.get("code")
                result_data = result.get("result") or {}
                output_url = result_data.get("output_image_url", "")

                # output_image_url می‌تونه string یا list باشه
                if isinstance(output_url, list):
                    if len(output_url) > 0:
                        output_url = output_url[0]
                    else:
                        output_url = ""

                if code == 100000 and output_url and isinstance(output_url, str):
                    # Download result
                    logger.info("[FaceSwap] Success! Downloading result...")
                    r4 = await session.get(
                        output_url,
                        impersonate="chrome",
                        headers={"User-Agent": _UA},
                        timeout=60,
                        verify=False,
                    )

                    if r4.status_code == 200 and r4.content:
                        result_path = target_image_path.replace(".", "_swap.", 1)
                        if not result_path.endswith((".jpg", ".png", ".jpeg")):
                            result_path = result_path + ".jpg"

                        with open(result_path, "wb") as f:
                            f.write(r4.content)

                        logger.info("[FaceSwap] Result saved: %s (%d bytes)", result_path, len(r4.content))
                        return True, result_path
                    else:
                        return False, f"Failed to download result (HTTP {r4.status_code})"

                if attempt % 6 == 5:
                    logger.info("[FaceSwap] Still processing... (attempt %d)", attempt + 1)

            return False, "Timeout waiting for face swap result"

    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("[FaceSwap] Error: %s", e, exc_info=True)
        return False, f"Error: {str(e)[:200]}"
