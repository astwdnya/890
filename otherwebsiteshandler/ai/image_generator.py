"""
image_generator.py
───────────────────
هندلر AI Image Generator با استفاده از سایت perchance.org.

روش کار:
  1. کاربر /ai رو می‌فرسته و Image Generator رو انتخاب می‌کنه
  2. تنظیمات (تعداد، استایل، شکل) رو با دکمه‌های شیشه‌ای تنظیم می‌کنه
  3. prompt رو می‌فرسته
  4. ربات با Playwright تصاویر رو تولید می‌کنه
  5. تصاویر به‌صورت عکس عادی به کاربر ارسال می‌شه

سایت: perchance.org/ai-text-to-image-generator
NSFW filter: permanently disabled
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

# Art styles (from perchance.org select options)
ART_STYLES = [
    "none", "Painted Anime", "Casual Photo", "Cinematic",
    "Digital Painting", "Concept Art", "Anime", "Manga",
    "Comic Book", "Pixel Art", "Watercolor", "Oil Painting",
    "Fantasy Art", "Cyberpunk", "Steampunk", "Studio Ghibli",
    "Renaissance", "Surreal", "Pop Art", "Vaporwave",
    "Hyperrealistic", "Photorealistic", "3D Render", "Matte Painting",
    "Chibi", "Furry", "Novel AI", "Cartoonish",
    "Arcane", "Digital Art",
]

# Shape options
SHAPES = {
    "square": "Square",
    "portrait": "Portrait",
    "landscape": "Landscape",
}

MAX_IMAGES = 4

_PERCHANCE_URL = "https://perchance.org/ai-text-to-image-generator"


async def generate_image(
    prompt: str,
    art_style: str = "none",
    shape: str = "square",
    count: int = 1,
    progress_cb=None,
) -> Tuple[bool, str, List[str]]:
    """
    تولید تصویر با استفاده از perchance.org با Playwright.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return False, "Playwright not installed", []

    shape_label = SHAPES.get(shape, "Square")
    image_paths = []

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox"],
            )

            # Load the main page once and generate all images
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1024, "height": 768},
            )

            # Disable NSFW filter permanently
            await context.add_init_script("""
                localStorage.setItem('okayToShowNsfwUntil', '9999999999999');
                localStorage.setItem('possiblyNsfwImageGenerationCount', '0');
            """)

            page = await context.new_page()

            if progress_cb:
                try:
                    await progress_cb("🌐 در حال بارگذاری سایت...")
                except Exception:
                    pass

            logger.info("[AI] Loading perchance.org...")
            await page.goto(_PERCHANCE_URL, wait_until="networkidle", timeout=90000)
            await asyncio.sleep(10)

            # Find generator frame
            gen_frame = None
            for f in page.frames:
                if ".perchance.org" in f.url and "embed" not in f.url and f.url != "about:blank":
                    gen_frame = f
                    break

            if not gen_frame:
                return False, "Generator frame not found", []

            await asyncio.sleep(5)

            # Generate each image
            for i in range(count):
                if progress_cb:
                    try:
                        await progress_cb(f"🎨 در حال تولید تصویر {i+1}/{count}...")
                    except Exception:
                        pass

                logger.info("[AI] Generating image %d/%d", i+1, count)

                # 1. Fill prompt (use evaluate to find visible textarea)
                await gen_frame.evaluate("""(text) => {
                    const textareas = document.querySelectorAll('textarea.paragraph-input');
                    for (const ta of textareas) {
                        if (ta.offsetParent !== null) {
                            ta.value = text;
                            ta.dispatchEvent(new Event('input', {bubbles: true}));
                            return true;
                        }
                    }
                    return false;
                }""", prompt)

                # 2. Set art style
                if art_style and art_style != "none":
                    await gen_frame.evaluate("""(style) => {
                        const selects = document.querySelectorAll('select');
                        if (selects.length >= 1) {
                            selects[0].value = style;
                            selects[0].dispatchEvent(new Event('change', {bubbles: true}));
                        }
                    }""", art_style)

                # 3. Set shape
                await gen_frame.evaluate("""(shape) => {
                    const selects = document.querySelectorAll('select');
                    if (selects.length >= 2) {
                        selects[1].value = shape;
                        selects[1].dispatchEvent(new Event('change', {bubbles: true}));
                    }
                }""", shape_label)

                # 4. Click Generate button
                gen_clicked = await gen_frame.evaluate("""() => {
                    const btns = document.querySelectorAll('button');
                    for (const btn of btns) {
                        const text = (btn.innerText || '').toLowerCase();
                        if (text.includes('generate')) {
                            btn.click();
                            return true;
                        }
                    }
                    return false;
                }""")

                if not gen_clicked:
                    logger.warning("[AI] Generate button not found")
                    continue

                # 5. Wait for image (up to 180 seconds)
                img_found = False
                for attempt in range(36):
                    await asyncio.sleep(5)

                    # Check all frames for image
                    for frame in page.frames:
                        try:
                            result = await frame.evaluate("""() => {
                                // Check canvas
                                const canvas = document.querySelector('canvas');
                                if (canvas && canvas.width > 0) {
                                    return {type: 'canvas'};
                                }
                                // Check images
                                const imgs = document.querySelectorAll('img');
                                for (const img of imgs) {
                                    if (img.naturalWidth > 100) {
                                        return {type: 'img', src: img.src};
                                    }
                                }
                                return null;
                            }""")

                            if result:
                                logger.info("[AI] Image %d found after %ds", i+1, (attempt+1)*5)

                                # Extract image data
                                data_url = None
                                if result['type'] == 'canvas':
                                    data_url = await frame.evaluate(
                                        "() => document.querySelector('canvas').toDataURL('image/png')"
                                    )
                                elif result['type'] == 'img':
                                    src = result.get('src', '')
                                    if src.startswith('data:'):
                                        data_url = src
                                    elif src:
                                        # Download the image
                                        data_url = await frame.evaluate(f"""async () => {{
                                            try {{
                                                const resp = await fetch('{src}');
                                                const blob = await resp.blob();
                                                return await new Promise(resolve => {{
                                                    const reader = new FileReader();
                                                    reader.onloadend = () => resolve(reader.result);
                                                    reader.readAsDataURL(blob);
                                                }});
                                            }} catch(e) {{ return null; }}
                                        }}""")

                                if data_url and data_url.startswith('data:'):
                                    b64 = data_url.split(',')[1]
                                    img_data = base64.b64decode(b64)
                                    img_path = os.path.join("/tmp", f"ai_gen_{i}_{int(time.time())}.png")
                                    with open(img_path, 'wb') as f:
                                        f.write(img_data)
                                    image_paths.append(img_path)
                                    logger.info("[AI] Image %d saved: %s (%d bytes)", i+1, img_path, len(img_data))
                                    img_found = True
                                    break
                        except Exception:
                            continue

                    if img_found:
                        break

                    if attempt % 6 == 5:
                        logger.info("[AI] Still waiting... %ds", (attempt+1)*5)
                        if progress_cb:
                            try:
                                await progress_cb(f"🎨 هنوز در حال تولید تصویر {i+1}/{count}... ({(attempt+1)*5}s)")
                            except Exception:
                                pass

                if not img_found:
                    logger.warning("[AI] Image %d not found after timeout", i+1)

            await browser.close()

        if image_paths:
            return True, "", image_paths
        else:
            return False, "هیچ تصویری تولید نشد. ممکنه سایت پرload باشه. دوباره تلاش کن.", []

    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("[AI] Error: %s", e, exc_info=True)
        return False, f"خطا: {str(e)[:200]}", []
