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
            }
    except Exception as e:
        logger.error("sarrast extract_chapter_info error: %s", e)
        return None


async def download_chapter_images(
    url: str,
    out_dir: str,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    max_concurrent: int = 5,
) -> Optional[List[str]]:
    """
    دانلود همه‌ی تصاویر یه فصل.

    Args:
        url: URL فصل (https://sarrast.com/series/.../...)
        out_dir: مسیر ذخیره تصاویر
        progress_cb: callback(done, total, current_url)
        max_concurrent: تعداد دانلود موازی

    Returns:
        لیست مسیر فایل‌های دانلود شده (به ترتیب)، یا None در صورت خطا.
    """
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

        img_paths = await download_chapter_images(url, out_dir, progress_cb)
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

        # Try img2pdf first (preserves quality, fast)
        try:
            import img2pdf
            with open(out_path, "wb") as f:
                f.write(img2pdf.convert(img_paths))
            logger.info("PDF created with img2pdf: %s (%d images)", out_path, len(img_paths))
            return out_path
        except ImportError:
            logger.info("img2pdf not available, using PIL")
        except Exception as e:
            logger.warning("img2pdf failed: %s, falling back to PIL", e)

        # Fallback: PIL
        try:
            from PIL import Image
            images_pil = []
            for p in img_paths:
                try:
                    img = Image.open(p)
                    # Convert to RGB (PDF doesn't support RGBA)
                    if img.mode in ("RGBA", "P", "LA"):
                        img = img.convert("RGB")
                    images_pil.append(img)
                except Exception as e:
                    logger.warning("Failed to open %s: %s", p, e)
                    continue
            if not images_pil:
                return None
            # Save as PDF
            first = images_pil[0]
            rest = images_pil[1:]
            first.save(out_path, "PDF", save_all=True, append_images=rest)
            logger.info("PDF created with PIL: %s (%d images)", out_path, len(images_pil))
            return out_path
        except Exception as e:
            logger.error("PIL PDF creation failed: %s", e)
            return None
    finally:
        # Cleanup temp dir
        try:
            shutil.rmtree(out_dir, ignore_errors=True)
        except Exception:
            pass


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

    out_dir = tempfile.mkdtemp(prefix="sarrast_zip_")
    try:
        img_paths = await download_chapter_images(url, out_dir, progress_cb)
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
