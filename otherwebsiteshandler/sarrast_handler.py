"""
sarrast_handler.py
──────────────────
هندلر برای سایت sarrast.com — داستان‌های سکسی تصویری فارسی.

ساختار سایت:
  - URL: https://sarrast.com/series/{series_slug}/{post_slug}
  - API:  https://sarrast.com/series/{series_slug}/{post_slug}/api
  - API JSON response:
      {
        "serie": {title, slug, posts: [...]},
        "files": [{path, width, height}, ...],   # لیست همه‌ی تصاویر
        "post": {title, slug, ...},
        "prev": {...}, "next": {...},
        "postNumber": "..."
      }
  - تصاویر در: https://sarrast.com{path} (path = /public/img/series/...)

امکانات:
  - is_sarrast_url(url): تشخیص URL سایت
  - extract_chapter_info(url): گرفتن اطلاعات فصل (عنوان، تعداد تصاویر، فصل قبلی/بعدی)
  - download_chapter_images(url, out_dir, progress_cb): دانلود همه‌ی تصاویر
  - download_chapter_pdf(url, out_dir, progress_cb): ترکیب همه‌ی تصاویر به‌صورت PDF

استراتژی:
  1. fetch /api endpoint → JSON
  2. extract files[] (لیست path ها)
  3. دانلود همه‌ی تصاویر به‌صورت موازی (with browser impersonation برای Cloudflare)
  4. (اختیاری) ساخت PDF با img2pdf یا PIL
"""
import asyncio
import logging
import os
import re
import time
from typing import List, Optional, Callable
from urllib.parse import urljoin, urlparse

from curl_cffi.requests import AsyncSession

logger = logging.getLogger("SarrastHandler")

# Safari UA works with sarrast's Cloudflare; Chrome gets 403
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.0 Safari/605.1.15"
)
_IMPERSONATE = "safari"

_BASE = "https://sarrast.com"


def is_sarrast_url(url: str) -> bool:
    """تشخیص آیا URL متعلق به sarrast.com هست."""
    if not url:
        return False
    parsed = urlparse(url)
    return "sarrast.com" in (parsed.netloc or "").lower()


def _parse_url(url: str) -> Optional[tuple]:
    """
    تجزیه‌ی URL به (series_slug, post_slug).

    Examples:
      https://sarrast.com/series/pervert-sexy-sibling/6-ass
        → ('pervert-sexy-sibling', '6-ass')
    """
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    # path = "series/pervert-sexy-sibling/6-ass"
    m = re.match(r"series/([^/]+)/([^/]+)", path)
    if m:
        return m.group(1), m.group(2)
    return None


async def extract_chapter_info(url: str) -> Optional[dict]:
    """
    گرفتن اطلاعات فصل از API sarrast.com.

    Returns:
        dict با فیلدهای:
        - title: عنوان فصل
        - series_title: عنوان سریال
        - series_slug: slug سریال
        - post_slug: slug فصل
        - post_number: شماره فصل (مثل "ass" یا "6")
        - images: لیست dict با {url, width, height}
        - prev: اطلاعات فصل قبلی (یا None)
        - next: اطلاعات فصل بعدی (یا None)
        - all_episodes: لیست همه‌ی قسمت‌ها
    """
    parsed = _parse_url(url)
    if not parsed:
        logger.warning("Invalid sarrast URL: %s", url)
        return None

    series_slug, post_slug = parsed
    api_url = f"{_BASE}/series/{series_slug}/{post_slug}/api"
    logger.info("Fetching sarrast API: %s", api_url)

    try:
        async with AsyncSession() as s:
            r = await s.get(
                api_url,
                impersonate=_IMPERSONATE,
                timeout=20,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": f"{_BASE}/series/{series_slug}/{post_slug}",
                },
            )
            if r.status_code != 200:
                logger.warning("sarrast API HTTP %d", r.status_code)
                return None

            data = r.json()
            serie = data.get("serie", {})
            post = data.get("post", {})
            files = data.get("files", [])

            # Build image URLs (full URLs)
            images = []
            for f in files:
                path = f.get("path", "")
                if path:
                    full_url = urljoin(_BASE, path)
                    images.append({
                        "url": full_url,
                        "width": f.get("width", 0),
                        "height": f.get("height", 0),
                    })

            return {
                "title": post.get("title", ""),
                "series_title": serie.get("title", ""),
                "series_slug": serie.get("slug", series_slug),
                "post_slug": post.get("slug", post_slug),
                "post_number": data.get("postNumber", ""),
                "images": images,
                "prev": data.get("prev"),
                "next": data.get("next"),
                "all_episodes": serie.get("posts", []),
                "api_url": api_url,
                "translate": post.get("translate"),  # ترجمه فارسی (اگه موجود باشه)
                "lang": post.get("lang", ""),  # زبان (مثل "فارسی")
            }
    except Exception as e:
        logger.error("sarrast extract_chapter_info error: %s", e)
        return None


async def download_chapter_images(
    url: str,
    out_dir: str,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    max_concurrent: int = 5,
    info: Optional[dict] = None,
) -> Optional[List[str]]:
    """
    دانلود همه‌ی تصاویر یه فصل.

    Args:
        url: URL فصل (https://sarrast.com/series/.../...)
        out_dir: مسیر ذخیره تصاویر
        progress_cb: callback(done, total, current_url)
        max_concurrent: تعداد دانلود موازی
        info: اطلاعات فصل از پیش‌دریافت‌شده (اختیاری) - اگه داده بشه،
              یک API call اضافه صرفه‌جویی می‌کنه.

    Returns:
        لیست مسیر فایل‌های دانلود شده (به ترتیب)، یا None در صورت خطا.
    """
    if info is None:
        info = await extract_chapter_info(url)
    if not info or not info["images"]:
        logger.warning("No images found for %s", url)
        return None

    os.makedirs(out_dir, exist_ok=True)
    images = info["images"]
    total = len(images)
    logger.info("Downloading %d images from sarrast (series=%s, post=%s)",
                total, info["series_slug"], info["post_slug"])

    # اسم فایل‌ها: {series_slug}_{post_slug}_{N:03d}.webp
    series_slug = info["series_slug"]
    post_slug = info["post_slug"]

    # Pre-allocate result list (ordered)
    paths: List[Optional[str]] = [None] * total
    sem = asyncio.Semaphore(max_concurrent)
    done_counter = [0]
    counter_lock = asyncio.Lock()

    async with AsyncSession() as s:
        async def download_one(idx: int, img_info: dict):
            async with sem:
                img_url = img_info["url"]
                # Filename: based on URL's basename (preserves original ordering)
                # e.g. /public/img/series/pervert-sexy-sibling/6-ass/2-875.webp → 2-875.webp
                basename = os.path.basename(urlparse(img_url).path)
                if not basename:
                    basename = f"{idx + 1:03d}.webp"
                # Prefix with order to ensure sorted order
                local_name = f"{idx + 1:03d}_{basename}"
                out_path = os.path.join(out_dir, local_name)

                for attempt in range(3):
                    try:
                        r = await s.get(
                            img_url,
                            impersonate=_IMPERSONATE,
                            timeout=30,
                            headers={
                                "User-Agent": _USER_AGENT,
                                "Referer": f"{_BASE}/series/{series_slug}/{post_slug}",
                            },
                        )
                        if r.status_code == 200 and r.content:
                            with open(out_path, "wb") as f:
                                f.write(r.content)
                            paths[idx] = out_path
                            async with counter_lock:
                                done_counter[0] += 1
                                if progress_cb:
                                    try:
                                        await progress_cb(done_counter[0], total, img_url) \
                                            if asyncio.iscoroutinefunction(progress_cb) \
                                            else progress_cb(done_counter[0], total, img_url)
                                    except Exception:
                                        pass
                            return
                        elif r.status_code in (429, 503):
                            await asyncio.sleep(1.5 * (attempt + 1))
                        else:
                            logger.warning("sarrast image %d HTTP %d", idx, r.status_code)
                            await asyncio.sleep(0.5)
                    except Exception as e:
                        logger.warning("sarrast image %d attempt %d: %s", idx, attempt, e)
                        await asyncio.sleep(1 * (attempt + 1))

        # Run all downloads in parallel
        await asyncio.gather(*[download_one(i, img) for i, img in enumerate(images)])

    valid_paths = [p for p in paths if p]
    logger.info("Downloaded %d/%d images from sarrast", len(valid_paths), total)
    return valid_paths if valid_paths else None


async def download_chapter_pdf(
    url: str,
    out_path: str,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> Optional[str]:
    """
    دانلود همه‌ی تصاویر و ساخت PDF از اون‌ها.

    Returns:
        مسیر فایل PDF، یا None در صورت خطا.
    """
    import tempfile
    import shutil

    info = await extract_chapter_info(url)
    if not info:
        return None

    out_dir = tempfile.mkdtemp(prefix="sarrast_pdf_")
    try:
        # Step 1: Download all images
        if progress_cb:
            try:
                await progress_cb(0, 1, "Downloading images...") \
                    if asyncio.iscoroutinefunction(progress_cb) \
                    else progress_cb(0, 1, "Downloading images...")
            except Exception:
                pass

        # info رو پاس می‌دیم تا extract_chapter_info دوباره صدا زده نشه
        img_paths = await download_chapter_images(url, out_dir, progress_cb, info=info)
        if not img_paths:
            logger.error("No images downloaded for PDF")
            return None

        # Step 2: Build PDF
        if progress_cb:
            try:
                await progress_cb(len(img_paths), len(img_paths), "Building PDF...") \
                    if asyncio.iscoroutinefunction(progress_cb) \
                    else progress_cb(len(img_paths), len(img_paths), "Building PDF...")
            except Exception:
                pass

        # Sort by filename (numeric prefix ensures order)
        img_paths.sort()

        # PDF construction رو داخل executor اجرا می‌کنیم تا event loop block نشه.
        # مهم: قبلاً PIL با save_all همه‌ی image‌ها رو همزمان توی RAM لود می‌کرد
        # که برای 48 تصویر 720x5929 WebP → ~1.7GB peak RAM می‌شد و container کرش می‌کرد (OOM).
        # حالا از روش append استفاده می‌کنیم: هر image رو جداگانه باز می‌کنیم،
        # به PDF اضافه می‌کنیم، بعد close می‌کنیم → peak RAM ~140MB.
        loop = asyncio.get_event_loop()
        result_path = await loop.run_in_executor(
            None, _build_pdf_sequential, img_paths, out_path
        )
        return result_path
    finally:
        # Cleanup temp dir
        try:
            shutil.rmtree(out_dir, ignore_errors=True)
        except Exception:
            pass


def _build_pdf_sequential(img_paths: List[str], out_path: str) -> Optional[str]:
    """
    ساخت PDF با PIL به‌صورت sequential append - memory-efficient.

    استراتژی:
      1. اولین image رو به‌عنوان PDF جدید ذخیره می‌کنیم
      2. بقیه image‌ها رو یکی‌یکی با append=True اضافه می‌کنیم
      3. هر image رو بعد از append بلافاصله close می‌کنیم تا RAM آزاد بشه

    این روش برای 48 تصویر 720x5929 WebP فقط ~140MB RAM مصرف می‌کنه
    (مقایسه با save_all که ~1700MB مصرف می‌کرد).
    """
    if not img_paths:
        return None

    from PIL import Image  # noqa: F401
    import os as _os

    # اطمینان از وجود پوشه‌ی parent فایل خروجی (مهم وقتی out_path
    # در مسیری هست که هنوز ساخته نشده - مثلاً توسط تابع caller)
    parent_dir = _os.path.dirname(out_path)
    if parent_dir and not _os.path.exists(parent_dir):
        try:
            _os.makedirs(parent_dir, exist_ok=True)
        except Exception as e:
            logger.error("Cannot create output directory %s: %s", parent_dir, e)
            return None

    saved_count = 0

    for i, p in enumerate(img_paths):
        try:
            img = Image.open(p)
            # Convert to RGB (PDF doesn't support RGBA/LA/P directly)
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            elif img.mode != "RGB":
                img = img.convert("RGB")

            if i == 0:
                # First image - create new PDF
                img.save(out_path, "PDF")
            else:
                # Subsequent images - append to existing PDF
                with open(out_path, "r+b") as f:
                    img.save(f, "PDF", append=True)

            img.close()
            saved_count += 1
        except Exception as e:
            logger.warning("Failed to add %s to PDF: %s", p, e)
            continue

    if saved_count == 0:
        logger.error("PIL PDF creation failed: no images could be added")
        return None

    logger.info("PDF created with PIL (sequential): %s (%d/%d images)",
                out_path, saved_count, len(img_paths))
    return out_path


async def download_chapter_as_zip(
    url: str,
    out_path: str,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> Optional[str]:
    """
    دانلود همه‌ی تصاویر و ساخت ZIP.

    Returns:
        مسیر فایل ZIP، یا None در صورت خطا.
    """
    import tempfile
    import shutil
    import zipfile

    info = await extract_chapter_info(url)
    if not info:
        return None

    out_dir = tempfile.mkdtemp(prefix="sarrast_zip_")
    try:
        # info رو پاس می‌دیم تا extract_chapter_info دوباره صدا زده نشه
        img_paths = await download_chapter_images(url, out_dir, progress_cb, info=info)
        if not img_paths:
            return None
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(img_paths):
                zf.write(p, os.path.basename(p))
        logger.info("ZIP created: %s (%d images)", out_path, len(img_paths))
        return out_path
    finally:
        try:
            shutil.rmtree(out_dir, ignore_errors=True)
        except Exception:
            pass


# ─── Translated PDF (با ترجمه فارسی) ─────────────────────


# مسیر فونت Mikhak (برای رسم متن فارسی روی تصاویر)
# این فونت از sarrast.com دانلود می‌شه و cache می‌شه
_MIKHAK_FONT_PATH = "/tmp/Mikhak-Medium.ttf"
_MIKHAK_FONT_URL = f"{_BASE}/public/fonts/Mikhak-Medium1.woff2"


async def _download_mikhak_font() -> Optional[str]:
    """دانلود فونت Mikhak از sarrast.com (cache شده در /tmp)."""
    if os.path.exists(_MIKHAK_FONT_PATH):
        try:
            if os.path.getsize(_MIKHAK_FONT_PATH) > 10000:
                return _MIKHAK_FONT_PATH
        except Exception:
            pass

    try:
        async with AsyncSession() as s:
            r = await s.get(
                _MIKHAK_FONT_URL,
                impersonate=_IMPERSONATE,
                timeout=20,
                headers={"User-Agent": _USER_AGENT, "Referer": _BASE + "/"},
            )
            if r.status_code != 200 or not r.content:
                logger.warning("Failed to download Mikhak font: HTTP %d", r.status_code)
                return None

            # Convert woff2 to ttf using fonttools
            woff2_path = "/tmp/Mikhak-Medium.woff2"
            with open(woff2_path, "wb") as f:
                f.write(r.content)

            try:
                from fontTools.ttLib import TTFont
                font = TTFont(woff2_path)
                font.flavor = None  # convert to TTF
                font.save(_MIKHAK_FONT_PATH)
                logger.info("Mikhak font downloaded and converted: %s", _MIKHAK_FONT_PATH)
                return _MIKHAK_FONT_PATH
            except Exception as e:
                logger.warning("fonttools conversion failed: %s - using woff2 directly", e)
                # PIL may not support woff2; return None
                return None
    except Exception as e:
        logger.warning("Failed to download Mikhak font: %s", e)
        return None


def _render_translation_on_image(
    img_path: str,
    out_path: str,
    translate_data: dict,
    img_y_start: int,
    img_height: int,
    editor_draw_panel_margin: float,
) -> bool:
    """
    رسم ترجمه فارسی روی یک تصویر.

    Args:
        img_path: مسیر تصویر اصلی
        out_path: مسیر تصویر خروجی (با ترجمه)
        translate_data: dict شامل html (لیست box ها)
        img_y_start: موقعیت y شروع تصویر در پنل کلی (برای جدا کردن box های هر تصویر)
        img_height: ارتفاع تصویر
        editor_draw_panel_margin: margin افقی پنل ترجمه
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
        import arabic_reshaper
        from bidi.algorithm import get_display
        import re as _re
    except ImportError as e:
        logger.warning("PIL/arabic_reshaper/bidi not available: %s", e)
        return False

    font_path = _MIKHAK_FONT_PATH
    if not os.path.exists(font_path):
        logger.warning("Mikhak font not available at %s", font_path)
        return False

    try:
        img = Image.open(img_path).convert("RGBA")
    except Exception as e:
        logger.warning("Failed to open image %s: %s", img_path, e)
        return False

    boxes = translate_data.get("html", []) if translate_data else []
    if not boxes:
        # No translation for this chapter
        try:
            img.convert("RGB").save(out_path, "JPEG", quality=85)
            return True
        except Exception:
            return False

    # Find boxes that belong to this image (y in [img_y_start, img_y_start + img_height))
    img_boxes = [
        b for b in boxes
        if img_y_start <= b.get("y", 0) < img_y_start + img_height
    ]

    if not img_boxes:
        # No boxes for this image - just save as is
        try:
            img.convert("RGB").save(out_path, "JPEG", quality=85)
            return True
        except Exception:
            return False

    # Create overlay
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    img_w, img_h_actual = img.size

    for box in img_boxes:
        # Adjust x by editor_draw_panel_margin (boxes are in editor space)
        # اگه endX/endY یا x/y برابر None بودن، skip کن
        try:
            x = (box.get("x") or 0) - editor_draw_panel_margin
            y = (box.get("y") or 0) - img_y_start  # relative to this image
            end_x = (box.get("endX") or box.get("x") or 0) - editor_draw_panel_margin
            end_y = (box.get("endY") or box.get("y") or 0) - img_y_start
        except (TypeError, ValueError):
            continue
        box_w = end_x - x
        box_h = end_y - y

        # Skip if box is outside image bounds
        if box_w <= 0 or box_h <= 0:
            continue
        if x < -50 or end_x > img_w + 50:
            # box is mostly outside image - skip but log
            continue

        # Clamp to image bounds
        x_clamped = max(0, x)
        y_clamped = max(0, y)
        end_x_clamped = min(img_w, end_x)
        end_y_clamped = min(img_h_actual, end_y)

        if end_x_clamped - x_clamped <= 5 or end_y_clamped - y_clamped <= 5:
            continue

        # Background color
        bg_color = box.get("background", "#FFFFFF")
        if bg_color and bg_color.lower() in ("#000000", "#000"):
            bg_rgba = (0, 0, 0, 230)
            text_color = (255, 255, 255, 255)
        else:
            bg_rgba = (255, 255, 255, 230)
            text_color = (0, 0, 0, 255)

        # Draw background rectangle (clamped)
        draw.rectangle(
            [x_clamped, y_clamped, end_x_clamped, end_y_clamped],
            fill=bg_rgba,
        )

        # Get text content (parse multiple divs as separate lines)
        content = box.get("content", "") or ""
        divs = _re.findall(r"<div[^>]*>([^<]*)</div>", content)
        if not divs:
            text = _re.sub(r"<[^>]+>", "", content).strip()
            divs = [text] if text else []

        if not divs:
            continue

        # Calculate font size based on box height
        num_lines = len(divs)
        # Try to fit font size to box
        font_size = min(
            int((end_y_clamped - y_clamped) / (num_lines * 1.4)),
            int((end_x_clamped - x_clamped) / 8),
        )
        font_size = max(11, min(font_size, 36))

        try:
            font = ImageFont.truetype(font_path, font_size)
        except Exception:
            continue

        # Draw each line
        line_height = (end_y_clamped - y_clamped) / (num_lines + 0.3)
        start_y = y_clamped + ((end_y_clamped - y_clamped) - line_height * num_lines) / 2

        for i, line_text in enumerate(divs):
            line_text = (line_text or "").strip()
            if not line_text:
                continue
            # Reshape Persian text for proper rendering
            try:
                reshaped = arabic_reshaper.reshape(line_text)
                bidi_text = get_display(reshaped)
            except Exception:
                bidi_text = line_text  # fallback to raw text

            # Get text size
            try:
                bbox = draw.textbbox((0, 0), bidi_text, font=font)
                text_w = bbox[2] - bbox[0]
                text_h = bbox[3] - bbox[1]
            except Exception:
                continue

            # If text is too wide, decrease font size and retry
            attempts = 0
            while text_w > (end_x_clamped - x_clamped - 10) and font_size > 9 and attempts < 5:
                font_size -= 2
                try:
                    font = ImageFont.truetype(font_path, font_size)
                    bbox = draw.textbbox((0, 0), bidi_text, font=font)
                    text_w = bbox[2] - bbox[0]
                    text_h = bbox[3] - bbox[1]
                except Exception:
                    break
                attempts += 1

            # Center horizontally
            text_x = x_clamped + ((end_x_clamped - x_clamped) - text_w) / 2
            text_y = start_y + i * line_height + (line_height - text_h) / 2

            # Draw text (with subtle shadow for readability)
            shadow_offset = 1
            draw.text(
                (text_x + shadow_offset, text_y + shadow_offset),
                bidi_text, fill=(0, 0, 0, 80), font=font,
            )
            draw.text((text_x, text_y), bidi_text, fill=text_color, font=font)

    # Composite overlay onto image
    try:
        result = Image.alpha_composite(img, overlay)
        result.convert("RGB").save(out_path, "JPEG", quality=85)
        return True
    except Exception as e:
        logger.warning("Failed to save translated image: %s", e)
        return False


async def download_chapter_pdf_translated(
    url: str,
    out_path: str,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> Optional[str]:
    """
    دانلود همه‌ی تصاویر، رسم ترجمه فارسی روی هر تصویر، و ساخت PDF.

    این تابع مثل download_chapter_pdf هست، ولی:
      - تصاویر ترجمه‌شده (با متن فارسی) رو به PDF تبدیل می‌کنه
      - از فونت Mikhak (همون فونت سایت sarrast) استفاده می‌کنه
      - اگر ترجمه موجود نباشه، به fallback به PDF بدون ترجمه

    Returns:
        مسیر فایل PDF ترجمه‌شده، یا None در صورت خطا.
    """
    import tempfile
    import shutil

    info = await extract_chapter_info(url)
    if not info:
        return None

    translate = info.get("translate")
    if not translate or not translate.get("html"):
        logger.info("No translation available - falling back to regular PDF")
        return await download_chapter_pdf(url, out_path, progress_cb)

    logger.info("Translation available: %d boxes",
                len(translate.get("html", [])))

    # Download Mikhak font
    font_path = await _download_mikhak_font()
    if not font_path:
        logger.warning("Mikhak font unavailable - falling back to regular PDF")
        return await download_chapter_pdf(url, out_path, progress_cb)

    out_dir = tempfile.mkdtemp(prefix="sarrast_translated_")
    try:
        # Step 1: Download all images
        if progress_cb:
            try:
                await progress_cb(0, 1, "Downloading images...") \
                    if asyncio.iscoroutinefunction(progress_cb) \
                    else progress_cb(0, 1, "Downloading images...")
            except Exception:
                pass

        img_paths = await download_chapter_images(url, out_dir, progress_cb, info=info)
        if not img_paths:
            logger.error("No images downloaded for translated PDF")
            return None

        # Step 2: Render translation on each image
        if progress_cb:
            try:
                await progress_cb(len(img_paths), len(img_paths), "Rendering Persian translation...") \
                    if asyncio.iscoroutinefunction(progress_cb) \
                    else progress_cb(len(img_paths), len(img_paths), "Rendering Persian translation...")
            except Exception:
                pass

        img_paths.sort()
        translated_paths = []
        editor_draw_panel_margin = translate.get("editorDrawPanelMargin", 0)
        boxes = translate.get("html", [])

        # Calculate y offset for each image (با استفاده از info["images"] که به ترتیب اصلی هست)
        y_offset = 0
        for i, img_path in enumerate(img_paths):
            # Find image height from info (به ترتیب اصلی - نه sort شده)
            if i < len(info["images"]):
                img_h = info["images"][i].get("height", 0) or 0
            else:
                # Fallback: get image size from PIL
                try:
                    from PIL import Image as _PILImage
                    with _PILImage.open(img_path) as im:
                        img_h = im.height
                except Exception:
                    img_h = 0

            # Render translation on this image
            # مسیر خروجی: همون نام + _translated.jpg
            base, _ = os.path.splitext(img_path)
            translated_path = base + "_translated.jpg"

            success = _render_translation_on_image(
                img_path,
                translated_path,
                translate,
                y_offset,
                img_h,
                editor_draw_panel_margin,
            )

            if success:
                translated_paths.append(translated_path)
            else:
                # Fallback to original image
                translated_paths.append(img_path)

            y_offset += img_h

        # Step 3: Build PDF from translated images
        if progress_cb:
            try:
                await progress_cb(len(translated_paths), len(translated_paths), "Building PDF with translation...") \
                    if asyncio.iscoroutinefunction(progress_cb) \
                    else progress_cb(len(translated_paths), len(translated_paths), "Building PDF with translation...")
            except Exception:
                pass

        # اطمینان از وجود پوشه‌ی parent فایل خروجی
        parent_dir = os.path.dirname(out_path)
        if parent_dir and not os.path.exists(parent_dir):
            try:
                os.makedirs(parent_dir, exist_ok=True)
            except Exception as e:
                logger.error("Cannot create output directory: %s", e)
                return None

        # ساخت PDF به‌صورت sequential append (memory-efficient)
        loop = asyncio.get_event_loop()
        result_path = await loop.run_in_executor(
            None, _build_translated_pdf_sequential, translated_paths, out_path
        )
        return result_path
    finally:
        try:
            shutil.rmtree(out_dir, ignore_errors=True)
        except Exception:
            pass


def _build_translated_pdf_sequential(img_paths: List[str], out_path: str) -> Optional[str]:
    """
    ساخت PDF از تصاویر ترجمه‌شده با PIL (sequential append - memory-efficient).
    """
    if not img_paths:
        return None

    from PIL import Image  # noqa: F401
    import os as _os

    saved_count = 0

    for i, p in enumerate(img_paths):
        try:
            img = Image.open(p)
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            elif img.mode != "RGB":
                img = img.convert("RGB")

            if i == 0:
                img.save(out_path, "PDF")
            else:
                with open(out_path, "r+b") as f:
                    img.save(f, "PDF", append=True)
            img.close()
            saved_count += 1
        except Exception as e:
            logger.warning("Failed to add %s to PDF: %s", p, e)
            continue

    if saved_count == 0:
        logger.error("Translated PDF creation failed: no images could be added")
        return None

    logger.info("Translated PDF created: %s (%d/%d images)",
                out_path, saved_count, len(img_paths))
    return out_path


# ─── Test ─────────────────────────────────────────────────

async def _test():
    """تست هندلر."""
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    url = "https://sarrast.com/series/pervert-sexy-sibling/6-ass"
    print(f"=== Test: extract_chapter_info({url}) ===")
    info = await extract_chapter_info(url)
    if not info:
        print("❌ Failed to extract chapter info")
        return

    print(f"Series: {info['series_title']}")
    print(f"Chapter: {info['title']}")
    print(f"Images: {len(info['images'])}")
    print(f"First image: {info['images'][0]['url'] if info['images'] else 'N/A'}")
    print(f"Prev: {info.get('prev', {}).get('title', 'None')}")
    print(f"Next: {info.get('next', {}).get('title', 'None')}")
    print(f"Total episodes: {len(info['all_episodes'])}")

    # Download just first 3 images for test
    print(f"\n=== Test: download first 3 images ===")
    info['images'] = info['images'][:3]
    # Patch download_chapter_images by overriding extract_chapter_info return
    paths = await download_chapter_images(url, "/tmp/test_sarrast_imgs", max_concurrent=3)
    if paths:
        print(f"✅ Downloaded {len(paths)} images:")
        for p in paths:
            print(f"  {p} ({os.path.getsize(p)} bytes)")


if __name__ == "__main__":
    asyncio.run(_test())
