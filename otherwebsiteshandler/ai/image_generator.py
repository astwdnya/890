"""
image_generator.py
───────────────────
هندلر AI Image Generator با استفاده از API سایت Pollinations.ai.

دو موتور تولید تصویر:
  1. FLUX (پیش‌فرض) - کیفیت بالا، سرعت متوسط
  2. FLUX HD - کیفیت خیلی بالا (1024x1024)، سرعت کمتر

روش کار:
  1. کاربر /ai رو می‌فرسته و Image Generator رو انتخاب می‌کنه
  2. تنظیمات (تعداد، استایل، شکل، کیفیت) رو با دکمه‌های شیشه‌ای تنظیم می‌کنه
  3. prompt رو می‌فرسته
  4. ربات تصاویر رو تولید می‌کنه
  5. تصاویر به‌صورت عکس عادی به کاربر ارسال می‌شه

API: Pollinations.ai (کاملاً رایگان، بدون auth، بدون محدودیت)
NSFW: بدون فیلتر
"""

import asyncio
import logging
import os
import random
import urllib.parse
from typing import List, Optional, Tuple

logger = logging.getLogger("ImageGenerator")

# Art styles
ART_STYLES = [
    "none", "anime", "painted anime", "cinematic", "digital painting",
    "concept art", "oil painting", "watercolor", "manga", "comic book",
    "pixel art", "fantasy art", "cyberpunk", "steampunk", "studio ghibli",
    "photorealistic", "hyperrealistic", "3d render", "vaporwave", "pop art",
    "renaissance", "surreal", "chibi", "furry", "cartoonish",
    "arcane", "digital art", "matte painting", "illustration", "pixelated",
]

# Shape options (width x height)
# HD = 1024px, Standard = 768px
SHAPES = {
    "square": (1024, 1024),
    "portrait": (768, 1024),
    "landscape": (1024, 768),
}

# Quality levels
QUALITY_LEVELS = {
    "standard": "",           # Default quality
    "hd": "&enhance=true",    # Enhanced quality (longer prompt processing)
}

MAX_IMAGES = 4

_API_BASE = "https://image.pollinations.ai/prompt/"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


async def generate_image(
    prompt: str,
    art_style: str = "none",
    shape: str = "square",
    count: int = 1,
    quality: str = "hd",
    progress_cb=None,
) -> Tuple[bool, str, List[str]]:
    """
    تولید تصویر با استفاده از Pollinations.ai.

    Args:
        prompt: متن prompt
        art_style: استایل هنری
        shape: شکل تصویر ("square", "portrait", "landscape")
        count: تعداد تصاویر (1-4)
        quality: کیفیت ("standard" یا "hd")
        progress_cb: callback async برای گزارش پیشرفت

    Returns:
        Tuple (success, error_message, list_of_image_paths)
    """
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return False, "curl_cffi not available", []

    # Build full prompt with art style
    if art_style and art_style != "none":
        full_prompt = f"{prompt}, {art_style} style, high quality, highly detailed, professional, 4k"
    else:
        full_prompt = f"{prompt}, high quality, highly detailed, professional, 4k"

    # Get dimensions
    width, height = SHAPES.get(shape, (1024, 1024))

    # Get quality parameter
    quality_param = QUALITY_LEVELS.get(quality, QUALITY_LEVELS["hd"])

    image_paths = []

    try:
        async with AsyncSession() as session:
            for i in range(count):
                if progress_cb:
                    try:
                        await progress_cb(f"🎨 در حال تولید تصویر {i+1}/{count}...")
                    except Exception:
                        pass

                # Generate unique seed for each image
                seed = random.randint(1000000, 9999999)

                # Build URL with quality params
                encoded_prompt = urllib.parse.quote(full_prompt)
                url = f"{_API_BASE}{encoded_prompt}?width={width}&height={height}&seed={seed}&nologo=true{quality_param}"

                logger.info("[AI] Generating image %d/%d: %s", i+1, count, full_prompt[:60])

                # Fetch image with retry
                success = False
                for attempt in range(3):
                    try:
                        r = await session.get(
                            url,
                            impersonate="chrome",
                            headers={"User-Agent": _UA},
                            timeout=120,
                            verify=False,
                        )

                        if r.status_code == 200 and r.content and len(r.content) > 5000:
                            img_path = os.path.join("/tmp", f"ai_gen_{i}_{int(asyncio.get_event_loop().time())}.jpg")
                            with open(img_path, "wb") as f:
                                f.write(r.content)
                            image_paths.append(img_path)
                            logger.info("[AI] Image %d saved: %s (%d bytes)", i+1, img_path, len(r.content))
                            success = True
                            break
                        else:
                            logger.warning("[AI] Image %d attempt %d: HTTP %d, size %d", i+1, attempt+1, r.status_code, len(r.content) if r.content else 0)
                            await asyncio.sleep(2)
                    except Exception as e:
                        logger.warning("[AI] Image %d attempt %d error: %s", i+1, attempt+1, e)
                        await asyncio.sleep(2)

                if not success:
                    logger.warning("[AI] Image %d failed after 3 attempts", i+1)

        if image_paths:
            return True, "", image_paths
        else:
            return False, "هیچ تصویری تولید نشد. دوباره تلاش کن.", []

    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("[AI] Error: %s", e, exc_info=True)
        return False, f"خطا: {str(e)[:200]}", []
