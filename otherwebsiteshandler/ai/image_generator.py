"""
image_generator.py
───────────────────
هندلر AI Image Generator با استفاده از سایت perchance.org.

روش کار:
  1. کاربر /ai رو می‌فرسته
  2. ربات لیست AI tools رو نشون می‌ده (فعلا فقط Image Generator)
  3. وقتی انتخاب کرد، دکمه‌های تنظیمات نشون داده می‌شه:
     - تعداد عکس (1-4)
     - Art Style (لیست از استایل‌ها)
     - Shape (square, portrait, landscape)
  4. کاربر prompt رو می‌فرسته
  5. ربات با Playwright تصاویر رو تولید می‌کنه
  6. تصاویر به‌صورت عکس عادی به کاربر ارسال می‌شه

سایت: perchance.org/ai-text-to-image-generator
API: image-generation.perchance.org (با Cloudflare Turnstile)
NSFW filter: permanently disabled (okayToShowNsfwUntil = very large number)

نکته: perchance.org از Cloudflare Turnstile برای verification استفاده می‌کنه.
      این فقط با مرورگر واقعی قابل حل هست (Turnstile در حالت invisible خودکار حل می‌شه).
      پس از Playwright (headless browser) استفاده می‌کنیم.
"""

import asyncio
import base64
import json
import logging
import os
import random
import time
from typing import List, Optional, Tuple

logger = logging.getLogger("ImageGenerator")

# Art styles available on perchance.org
ART_STYLES = [
    "none", "2d", "3d", "anime", "arcane", "cartoonish", "cinematic",
    "comic", "concept art", "cyberpunk", "digital art", "fantasy",
    "hyperrealistic", "manga", "matte painting", "oil painting",
    "painted anime", "photorealistic", "pixel art", "pixelated",
    "pop art", "renaissance", "steampunk", "studio ghibli", "surreal",
    "vaporwave", "watercolor", "novel ai", "furry", "chibi",
]

# Resolution/shape options
SHAPES = {
    "square": "512x512",
    "portrait": "512x768",
    "landscape": "768x512",
}

# Max images per generation
MAX_IMAGES = 4

# Perchance embed URL
_EMBED_URL = "https://image-generation.perchance.org/embed"
_PERCHANCE_URL = "https://perchance.org/ai-text-to-image-generator"


async def generate_image(
    prompt: str,
    art_style: str = "none",
    shape: str = "square",
    count: int = 1,
    progress_cb=None,
) -> Tuple[bool, str, List[str]]:
    """
    تولید تصویر با استفاده از perchance.org.

    Args:
        prompt: متن prompt
        art_style: استایل هنری (مثل "anime", "painted anime", etc.)
        shape: شکل تصویر ("square", "portrait", "landscape")
        count: تعداد تصاویر (1-4)
        progress_cb: callback async برای گزارش پیشرفت

    Returns:
        Tuple (success, error_message, list_of_image_paths)
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return False, "Playwright not installed", []

    resolution = SHAPES.get(shape, "512x512")
    full_prompt = f"{art_style}, {prompt}" if art_style and art_style != "none" else prompt

    image_paths = []

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox"],
            )

            for i in range(count):
                if progress_cb:
                    try:
                        await progress_cb(f"🎨 در حال تولید تصویر {i+1}/{count}...")
                    except Exception:
                        pass

                seed = random.randint(1000000, 9999999)

                # Build URL hash data
                hash_data = {
                    "prompt": full_prompt,
                    "seed": seed,
                    "resolution": resolution,
                    "negativePrompt": "",
                    "guidanceScale": 7,
                    "channel": "ai-text-to-image-generator",
                    "saveChannel": "ai-text-to-image-generator",
                    "saveTitle": full_prompt[:60],
                    "saveDescription": "",
                    "iframeId": f"img_{i}_{int(time.time())}",
                }

                hash_encoded = json.dumps(hash_data, separators=(',', ':'))
                url = f"{_EMBED_URL}#{hash_encoded}"

                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    viewport={"width": 1024, "height": 768},
                )

                # Set localStorage to disable NSFW filter permanently
                await context.add_init_script("""
                    localStorage.setItem('okayToShowNsfwUntil', '9999999999999');
                    localStorage.setItem('possiblyNsfwImageGenerationCount', '0');
                """)

                page = await context.new_page()

                logger.info("[AI] Generating image %d/%d: %s", i+1, count, full_prompt[:60])

                # Navigate to embed page
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)

                # Wait for image generation to complete
                # The embed page generates the image and creates a canvas or data URL
                try:
                    # Wait for the image to appear (canvas or img with data URL)
                    await page.wait_for_function(
                        """() => {
                            // Check main page
                            const canvas = document.querySelector('canvas');
                            if (canvas && canvas.width > 0 && canvas.height > 0) return true;
                            const img = document.querySelector('img[src^="data:"]');
                            if (img && img.src.length > 100) return true;
                            // Check iframes
                            for (const frame of document.querySelectorAll('iframe')) {
                                try {
                                    const doc = frame.contentDocument || frame.contentWindow?.document;
                                    if (doc) {
                                        const c = doc.querySelector('canvas');
                                        if (c && c.width > 0) return true;
                                        const i = doc.querySelector('img[src^="data:"]');
                                        if (i && i.src.length > 100) return true;
                                    }
                                } catch(e) {}
                            }
                            return false;
                        }""",
                        timeout=180000,  # 3 minutes max
                    )
                except Exception as e:
                    logger.warning("[AI] Wait for image failed: %s", e)

                # Extract image data
                data_url = None

                # Method 1: Try from main page
                try:
                    data_url = await page.evaluate("""() => {
                        const canvas = document.querySelector('canvas');
                        if (canvas && canvas.width > 0) return canvas.toDataURL('image/png');
                        const img = document.querySelector('img[src^="data:"]');
                        if (img) return img.src;
                        return null;
                    }""")
                except Exception:
                    pass

                # Method 2: Try from iframes
                if not data_url:
                    for frame in page.frames:
                        try:
                            data_url = await frame.evaluate("""() => {
                                const canvas = document.querySelector('canvas');
                                if (canvas && canvas.width > 0) return canvas.toDataURL('image/png');
                                const img = document.querySelector('img[src^="data:"]');
                                if (img) return img.src;
                                return null;
                            }""")
                            if data_url:
                                break
                        except Exception:
                            continue

                # Method 3: Try to find image in page HTML
                if not data_url:
                    try:
                        content = await page.content()
                        # Search for data:image in HTML
                        import re
                        matches = re.findall(r'src="(data:image/[^"]+)"', content)
                        if matches:
                            data_url = matches[0]
                    except Exception:
                        pass

                if data_url and data_url.startswith("data:"):
                    # Save the image
                    base64_data = data_url.split(",", 1)[1] if "," in data_url else ""
                    if base64_data:
                        img_data = base64.b64decode(base64_data)
                        img_path = os.path.join("/tmp", f"ai_gen_{i}_{int(time.time())}.png")
                        with open(img_path, "wb") as f:
                            f.write(img_data)
                        image_paths.append(img_path)
                        logger.info("[AI] Image %d saved: %s (%d bytes)", i+1, img_path, len(img_data))
                    else:
                        logger.warning("[AI] No base64 data in data URL")
                else:
                    logger.warning("[AI] No data URL found for image %d", i+1)

                await context.close()

            await browser.close()

        if image_paths:
            return True, "", image_paths
        else:
            return False, "هیچ تصویری تولید نشد. ممکنه سایت پر负载 باشه. دوباره تلاش کن.", []

    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("[AI] Error: %s", e, exc_info=True)
        return False, f"خطا: {str(e)[:200]}", []
