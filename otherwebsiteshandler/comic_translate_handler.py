"""
comic_translate_handler.py
──────────────────────────
هندلر ترجمه‌ی کامیک/مانگا (PDF) به فارسی با بازنویسی روی همون صفحه.

این هندلر حاصل مهندسی معکوس چند سرویس است:

۱) OCR — OCR.space (api.ocr.space/parse/image)
   ─────────────────────────────────────────────
   POST multipart با فیلدهای:
       base64Image: data:image/png;base64,<b64>
       language:    eng
       OCREngine:   2        (engine 2 = دقیق‌تر برای متن کجوکولو/کامیک)
       scale:       true
       isOverlayRequired: true
   هدر: apikey: helloworld   (کلید دموی عمومی؛ با OCRSPACE_API_KEY قابل تعویض)
   پاسخ: ParsedResults[0].TextOverlay.Lines[] → هر Line شامل Words[]
   با Left/Top/Width/Height مطلق (پیکسل) → bounding box دقیق هر خط.
   محدودیت کلید دمو: rate-limit → backoff خودکار + retry.

۲) ترجمه — Google Translate endpoint عمومی
   ─────────────────────────────────────────
   GET https://translate.googleapis.com/translate_a/single
       ?client=dict-chrome-ex&sl=en&tl=fa&dt=t&q=<text>
   (client=dict-chrome-ex از rate-limit مسیر gtx عبور می‌کنه؛
   خطوط یک صفحه با \n به هم چسبیده و یکجا ترجمه می‌شن تا context
   صفحه حفظ بشه؛ در صورت ناهم‌خوانی تعداد خطوط → ترجمه‌ی تک‌خطی)

۳) رندر فارسی — سیستم rewrite (مشابه sarrast_handler)
   ─────────────────────────────────────────────────────
   - فونت Mikhak از sarrast.com (woff2 → ttf با fonttools، کش /tmp)
   - اگر Pillow با libraqm ساخته شده باشه: direction='rtl'
   - وگرنه: arabic_reshaper + python-bidi (fallback همه‌جا کار می‌کنه)
   - حذف متن اصلی: fill رنگ پس‌زمینه‌ی حباب (flood-fill داخل کراپ
     با محدودیت نشتی) + fallback مستطیل pad شده

جریان کامل:
    PDF → صفحات تصویر (PyMuPDF) → OCR هر صفحه → خوشه‌بندی خطوط به حباب →
    ترجمه‌ی دسته‌ای → رندر فارسی روی صفحه → ساخت PDF → ارسال

ورودی‌های اصلی برای bot.py:
    translate_comic_pdf(pdf_path, output_path=None, progress_cb=None)
        → (success, output_path_or_error)
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import re
import time
import uuid
from typing import Awaitable, Callable, List, Optional, Tuple

import aiohttp

logger = logging.getLogger("ComicTranslate")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# ─── config (env-overridable) ──────────────────────────────────────────────
OCRSPACE_API_KEY = os.getenv("OCRSPACE_API_KEY", "helloworld")
GOOGLE_SL_DEFAULT = os.getenv("COMIC_TRANS_SL", "auto")   # source language
GOOGLE_TL = os.getenv("COMIC_TRANS_TL", "fa")             # target = Persian
FONT_PATH = "/tmp/Mikhak-Medium.ttf"
FONT_URL = "https://sarrast.com/public/fonts/Mikhak-Medium1.woff2"

ProgressCallback = Optional[Callable[[str], Awaitable[None]]]

# ─── Persian font ──────────────────────────────────────────────────────────

_font_checked = False


async def _ensure_persian_font() -> bool:
    """دانلود فونت Mikhak (در صورت نبود) و تبدیل به TTF."""
    global _font_checked
    if _font_checked:
        return os.path.exists(FONT_PATH)
    _font_checked = True
    if os.path.exists(FONT_PATH) and os.path.getsize(FONT_PATH) > 10000:
        return True
    try:
        async with aiohttp.ClientSession(
            headers={"User-Agent": UA, "Referer": "https://sarrast.com/"},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as s:
            async with s.get(FONT_URL) as r:
                if r.status != 200 or not r.content:
                    logger.warning("[ComicTr] font download HTTP %s", r.status)
                    return False
                data = await r.read()
        woff2 = "/tmp/Mikhak-Medium.woff2"
        with open(woff2, "wb") as f:
            f.write(data)
        try:
            from fontTools.ttLib import TTFont

            font = TTFont(woff2)
            font.flavor = None
            font.save(FONT_PATH)
        except Exception as e:
            logger.warning("[ComicTr] woff2→ttf failed (%s); trying woff2", e)
            if len(data) > 10000:
                with open(FONT_PATH, "wb") as f:
                    f.write(data)
        logger.info("[ComicTr] Persian font ready: %s", FONT_PATH)
        return os.path.exists(FONT_PATH)
    except Exception as e:
        logger.warning("[ComicTr] font error: %s", e)
        return False


# ─── text shaping (RTL) ────────────────────────────────────────────────────

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
    _RESHAPER_OK = True
except ImportError:
    _RESHAPER_OK = False

# test once whether Pillow supports direction='rtl' (needs libraqm)
# نکته: تست باید با فونت TTF واقعی باشه؛ load_default() همیشه خطا می‌ده
try:
    from PIL import Image, ImageDraw, ImageFont, features as _pil_features

    _RAQM_OK = bool(_pil_features.check("raqm"))
except Exception:
    _RAQM_OK = False


def _shape(text: str) -> str:
    """آماده‌سازی متن فارسی برای رسم.

    - با raqm: متن خام برمی‌گرده (PIL خودش با HarfBuzz/OpenType شکل می‌ده)
    - بدون raqm: arabic_reshaper + bidi؛ کاراکترهای presentation form که
      فونت تو cmap نداره با NFKC به حرف پایه برمی‌گردن (بدون tofu)
    """
    if _RAQM_OK:
        return text
    if _RESHAPER_OK:
        shaped = get_display(arabic_reshaper.reshape(text))
        return _cmap_safe(shaped)
    return text  # بهترین تلاش؛ بدون هر دو، حروف جدا می‌افتند


# cmap فونت برای fallback — یک بار لود می‌شه
_font_cmap: Optional[set] = None


def _get_cmap() -> Optional[set]:
    global _font_cmap
    if _font_cmap is None:
        try:
            from fontTools.ttLib import TTFont

            f = TTFont(FONT_PATH)
            s = set()
            for t in f["cmap"].tables:
                s.update(t.cmap.keys())
            f.close()
            _font_cmap = s
        except Exception:
            _font_cmap = set()
    return _font_cmap


def _cmap_safe(shaped: str) -> str:
    """جایگزینی presentation form های غایب در cmap فونت با حرف پایه (NFKC)."""
    cmap = _get_cmap()
    if not cmap:
        return shaped
    import unicodedata

    out = []
    for ch in shaped:
        if ch == " " or ord(ch) in cmap:
            out.append(ch)
        else:
            base = unicodedata.normalize("NFKC", ch)
            out.append(base if len(base) >= 1 else ch)
    return "".join(out)


def _draw_text(draw, xy, text, font=None, fill=None, **kw):
    """رسم متن با بهترین روش موجود (raqm یا reshaper)."""
    shaped = _shape(text)
    if _RAQM_OK:
        try:
            draw.text(xy, shaped, font=font, fill=fill, direction="rtl", **kw)
            return
        except Exception:
            pass
    draw.text(xy, shaped, font=font, fill=fill, **kw)


def _text_width(font, text: str) -> float:
    """طول متن شکل‌گرفته برای محاسبه‌ی wrap."""
    shaped = _shape(text)
    try:
        if _RAQM_OK:
            try:
                return font.getlength(shaped, direction="rtl")
            except Exception:
                pass
        return font.getlength(shaped)
    except Exception:
        bbox = font.getbbox(shaped)
        return bbox[2] - bbox[0]


# ─── PDF → images ──────────────────────────────────────────────────────────


def _pdf_to_images_sync(pdf_path: str, out_dir: str, dpi: int = 150) -> List[str]:
    """رندر صفحات PDF به PNG با PyMuPDF."""
    import fitz  # PyMuPDF

    paths = []
    doc = fitz.open(pdf_path)
    try:
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        for i, page in enumerate(doc):
            pix = page.get_pixmap(matrix=mat, alpha=False)
            p = os.path.join(out_dir, f"page_{i:04d}.png")
            pix.save(p)
            paths.append(p)
    finally:
        doc.close()
    return paths


# ─── OCR (OCR.space) ───────────────────────────────────────────────────────


def _ocr_space_sync(image_path: str, max_retries: int = 7) -> Optional[List[dict]]:
    """OCR یک تصویر با OCR.space.

    خروجی: لیست خطوط [{"text", "x", "y", "x2", "y2"}] (پیکسل مطلق)
    یا None در صورت خطای غیرقابل retry.
    rate-limit (روز/دقیقه) با backoff پوشش داده می‌شه.
    """
    import requests

    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    last_err = None
    for attempt in range(max_retries):
        try:
            r = requests.post(
                "https://api.ocr.space/parse/image",
                data={
                    "base64Image": f"data:image/png;base64,{b64}",
                    "language": "eng",
                    "OCREngine": "2",
                    "scale": "true",
                    "isTable": "true",
                    "isOverlayRequired": "true",
                },
                headers={"apikey": OCRSPACE_API_KEY, "User-Agent": UA},
                timeout=120,
            )
            if r.status_code == 429:
                wait = 45
                last_err = "rate-limited (429)"
                logger.info("[ComicTr] OCR.space rate-limited, waiting %ss (attempt %d)", wait, attempt + 1)
                time.sleep(wait)
                continue
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}: {r.text[:150]}"
                logger.warning("[ComicTr] OCR.space %s", last_err)
                time.sleep(8)
                continue
            j = r.json()
            if j.get("IsErroredOnProcessing"):
                msg = j.get("ErrorMessage") or ["unknown"]
                msg_str = "; ".join(msg) if isinstance(msg, list) else str(msg)
                if "rate" in msg_str.lower() or "limit" in msg_str.lower():
                    logger.info("[ComicTr] OCR.space limit msg, waiting 45s: %s", msg_str[:100])
                    time.sleep(45)
                    last_err = msg_str
                    continue
                last_err = msg_str
                logger.warning("[ComicTr] OCR.space error: %s", msg_str[:150])
                time.sleep(5)
                continue
            results = j.get("ParsedResults") or []
            if not results:
                return []
            overlay = results[0].get("TextOverlay") or {}
            lines_out = []
            for ln in overlay.get("Lines", []):
                words = ln.get("Words") or []
                if not words:
                    continue
                text = " ".join(w.get("WordText", "") for w in words).strip()
                if not text:
                    continue
                try:
                    x = min(int(w["Left"]) for w in words)
                    y = min(int(w["Top"]) for w in words)
                    x2 = max(int(w["Left"]) + int(w["Width"]) for w in words)
                    y2 = max(int(w["Top"]) + int(w["Height"]) for w in words)
                except (KeyError, ValueError, TypeError):
                    continue
                # فیلتر نویز: باکس‌های خیلی کوچیک یا خیلی بزرگ
                if (x2 - x) < 8 or (y2 - y) < 6:
                    continue
                lines_out.append({"text": text, "x": x, "y": y, "x2": x2, "y2": y2})
            return lines_out
        except requests.RequestException as e:
            last_err = f"network: {e}"
            logger.warning("[ComicTr] OCR.space network error: %s", e)
            time.sleep(6)
        except Exception as e:
            last_err = f"unexpected: {e}"
            logger.warning("[ComicTr] OCR.space unexpected: %s", e)
            time.sleep(4)
    logger.error("[ComicTr] OCR.space failed permanently: %s", last_err)
    return None


# ─── line clustering (بخ به حباب) ──────────────────────────────────────────


def _cluster_lines(lines: List[dict], page_w: int, page_h: int) -> List[dict]:
    """خطوط نزدیک به هم رو به یک حباب (بلوک متن) گروه می‌کنه.

    معیار: فاصله‌ی عمودی < 1.8 × ارتفاع خط + هم‌پوشانی افقی.
    خروجی: [{"texts": [...], "x", "y", "x2", "y2"}]
    """
    if not lines:
        return []
    # مرتب‌سازی از بالا به پایین، بعد چپ به راست
    lines = sorted(lines, key=lambda l: (l["y"], l["x"]))
    clusters: List[dict] = []
    for ln in lines:
        h = ln["y2"] - ln["y"]
        placed = False
        for c in clusters:
            ch = c["y2"] - c["y"]
            avg_h = max(12, (h + ch) / 2)
            v_gap = ln["y"] - c["y2"]
            # هم‌پوشانی افقی (حداقل 25٪ عرض کوچیک‌تر)
            ov = min(ln["x2"], c["x2"]) - max(ln["x"], c["x"])
            w_min = min(ln["x2"] - ln["x"], c["x2"] - c["x"])
            h_over = ov > 0.25 * w_min if w_min > 0 else False
            if v_gap < 1.8 * avg_h and h_over:
                c["texts"].append(ln["text"])
                c["x"] = min(c["x"], ln["x"])
                c["y"] = min(c["y"], ln["y"])
                c["x2"] = max(c["x2"], ln["x2"])
                c["y2"] = max(c["y2"], ln["y2"])
                placed = True
                break
        if not placed:
            clusters.append({
                "texts": [ln["text"]],
                "x": ln["x"], "y": ln["y"], "x2": ln["x2"], "y2": ln["y2"],
            })
    # حذف باکس‌های صفحه‌عریض (واترمارک) و خیلی کوچیک
    out = []
    for c in clusters:
        w, h = c["x2"] - c["x"], c["y2"] - c["y"]
        if w > 0.85 * page_w and h > 0.5 * page_h:
            continue
        if w < 15 or h < 10:
            continue
        out.append(c)
    return out


# ─── translation (Google dict-chrome-ex) ───────────────────────────────────


async def _google_translate(session: aiohttp.ClientSession, text: str,
                            sl: str, tl: str) -> Optional[str]:
    import urllib.parse

    q = urllib.parse.quote(text)
    url = (f"https://translate.googleapis.com/translate_a/single"
           f"?client=dict-chrome-ex&sl={sl}&tl={tl}&dt=t&q={q}")
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
            if r.status != 200:
                logger.warning("[ComicTr] google HTTP %s", r.status)
                return None
            j = await r.json(content_type=None)
            segs = j[0] if isinstance(j, list) and j else []
            return "".join(s[0] for s in segs if isinstance(s, list) and s and s[0])
    except Exception as e:
        logger.warning("[ComicTr] google error: %s", e)
        return None


async def _translate_texts(texts: List[str], sl: str = None) -> List[str]:
    """ترجمه‌ی خطوط؛ اول batch (با \n) بعد fallback تک‌خطی."""
    sl = sl or GOOGLE_SL_DEFAULT
    out: List[str] = [""] * len(texts)
    async with aiohttp.ClientSession(headers={"User-Agent": UA}) as session:
        # ── تلاش batch: همه‌ی خطوط یکجا (context کامل صفحه) ──
        if len(texts) > 1:
            joined = "\n".join(texts)
            tr = await _google_translate(session, joined, sl, GOOGLE_TL)
            if tr:
                lines = [l.strip() for l in tr.split("\n") if l.strip()]
                if len(lines) == len(texts):
                    return lines
                # گوگل گاهی خطوط رو merge/split می‌کنه → تک‌خطی
                logger.info("[ComicTr] batch line mismatch (%d vs %d) → per-line", len(lines), len(texts))
        # ── ترجمه‌ی تک‌خطی ──
        for i, t in enumerate(texts):
            if not t.strip():
                out[i] = t
                continue
            tr = await _google_translate(session, t, sl, GOOGLE_TL)
            out[i] = tr if tr else t
            await asyncio.sleep(0.15)  # soft rate limit
    return out


# ─── rendering ─────────────────────────────────────────────────────────────


def _sample_bg(img, box) -> Tuple[int, int, int]:
    """نمونه‌گیری رنگ پس‌زمینه از حلقه‌ی دور باکس متن."""
    from PIL import Image

    W, H = img.size
    x, y, x2, y2 = box
    pad = max(6, int((y2 - y) * 0.35))
    pts = []
    for (px, py) in [
        (x - pad, (y + y2) // 2), (x2 + pad, (y + y2) // 2),
        ((x + x2) // 2, y - pad), ((x + x2) // 2, y2 + pad),
        (x - pad, y - pad), (x2 + pad, y2 + pad),
        (x - pad, y2 + pad), (x2 + pad, y - pad),
    ]:
        if 0 <= px < W and 0 <= py < H:
            pts.append(img.getpixel((px, py))[:3])
    if not pts:
        return (255, 255, 255)
    # میانه هر کانال
    med = tuple(int(sorted(c)[len(c) // 2]) for c in zip(*pts))
    return med


def _wrap_persian(text: str, font, max_w: int) -> List[str]:
    """شکستن متن فارسی به خطوط مناسب عرض باکس."""
    words = text.split()
    if not words:
        return [""]
    lines, cur = [], words[0]
    for w in words[1:]:
        test = cur + " " + w
        if _text_width(font, test) <= max_w:
            cur = test
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def _render_page_sync(img_path: str, out_path: str,
                      clusters: List[dict], translations: List[str]) -> bool:
    """رسم ترجمه‌های فارسی روی یک صفحه.

    clusters و translations هم‌اندازه‌ان؛ ترجمه‌ی خالی/انگلیسی → skip.
    """
    from PIL import Image, ImageDraw, ImageFont, ImageFilter

    try:
        img = Image.open(img_path).convert("RGB")
    except Exception as e:
        logger.warning("[ComicTr] open page failed: %s", e)
        return False

    draw = ImageDraw.Draw(img)
    W, H = img.size
    rendered = 0

    for c, fa_text in zip(clusters, translations):
        if not fa_text or not fa_text.strip():
            continue
        # اگر ترجمه انگلیسی موند (مثلاً اسم خاص)، بازم رسمش کن — خوانا بهتر از هیچی نیست
        x, y, x2, y2 = c["x"], c["y"], c["x2"], c["y2"]
        bw, bh = x2 - x, y2 - y
        if bw <= 4 or bh <= 4:
            continue

        bg = _sample_bg(img, (x, y, x2, y2))
        brightness = sum(bg) / 3
        is_dark_bg = brightness < 110
        fill_color = (10, 10, 10) if not is_dark_bg else (245, 245, 245)

        # ── حذف متن اصلی: flood-fill داخل کراپ محدود (بدون نشتی به کل صفحه) ──
        pad = max(8, int(min(bw, bh) * 0.22))
        cx1, cy1 = max(0, x - pad), max(0, y - pad)
        cx2, cy2 = min(W, x2 + pad), min(H, y2 + pad)
        cw, chh = cx2 - cx1, cy2 - cy1
        if cw < 4 or chh < 4:
            continue
        crop = img.crop((cx1, cy1, cx2, cy2))
        try:
            # seed از مرکز باکس؛ متن اصلی اول با رنگ bg پاک می‌شه تا fill یکدست باشه
            cdraw = ImageDraw.Draw(crop)
            tx, ty, tx2, ty2 = x - cx1, y - cy1, x2 - cx1, y2 - cy1
            cdraw.rectangle([tx, ty, tx2, ty2], fill=bg)
            seed = ((tx + tx2) // 2, (ty + ty2) // 2)
            seed = (min(max(seed[0], 0), cw - 1), min(max(seed[1], 0), chh - 1))
            marker = (253, 254, 255) if bg != (253, 254, 255) else (250, 251, 252)
            ImageDraw.floodfill(crop, seed, marker, thresh=60)
            # اگر fill تقریباً کل کراپ رو گرفت (حباب بدون border → نشتی داخل کراپ)
            colors = crop.getcolors(maxcolors=200000) or []
            marker_count = sum(cnt for cnt, col in colors if col == marker)
            if marker_count > 0.92 * cw * chh:
                # داخل کراپ border پیدا نشد → فقط باکس متن پاک شده بمونه
                pass  # rectangle از قبل رسم شده؛ marker جاش رو با bg عوض می‌کنیم
            else:
                # marker → bg (کل ناحیه‌ی حباب با رنگ پس‌زمینه یکدست شد)
                _replace_color(crop, marker, bg)
            img.paste(crop, (cx1, cy1))
        except Exception as e:
            logger.debug("[ComicTr] floodfill failed (%s) → plain box", e)
            draw.rectangle([cx1, cy1, cx2, cy2], fill=bg)

        # ── رسم متن فارسی (auto font-size) ──
        try:
            fs = max(13, min(int(bh * 0.85), int(bw * 0.28), 42))
            while fs >= 11:
                font = ImageFont.truetype(FONT_PATH, fs)
                wrapped = _wrap_persian(fa_text, font, bw - 6)
                line_h = fs * 1.45
                total_h = line_h * len(wrapped)
                fits_w = all(_text_width(font, l) <= bw - 6 for l in wrapped)
                if total_h <= bh + line_h * 0.6 and fits_w:
                    break
                fs -= 2
            font = ImageFont.truetype(FONT_PATH, fs)
            wrapped = _wrap_persian(fa_text, font, bw - 6)
            line_h = fs * 1.45
            # center متن در باکس
            cy0 = y + max(0, (bh - line_h * len(wrapped)) / 2)
            for li, line in enumerate(wrapped):
                if not line.strip():
                    continue
                lw = _text_width(font, line)
                lx = x + max(0, (bw - lw) / 2)
                ly = cy0 + li * line_h
                # سایه‌ی خفیف برای خوانایی
                _draw_text(draw, (lx + 1, ly + 1), line, font=font,
                           fill=tuple(max(0, v - 70) if not is_dark_bg else min(255, v + 70) for v in bg))
                _draw_text(draw, (lx, ly), line, font=font, fill=fill_color)
            rendered += 1
        except Exception as e:
            logger.debug("[ComicTr] render text failed: %s", e)

    try:
        img.save(out_path, "PNG")
    except Exception as e:
        logger.warning("[ComicTr] save page failed: %s", e)
        return False
    logger.info("[ComicTr] page rendered: %d/%d bubbles", rendered, len(clusters))
    return True


def _replace_color(img, old, new) -> None:
    """جایگزینی سریع یک رنگ در تصویر RGB."""
    from PIL import Image

    if img.mode == "RGB":
        # استفاده از point/lookup روی هر کانال — سریع و بدون numpy
        r, g, b = img.split()
        # ساخت mask با merge → quantize ترفندی: به جای آن از getdata پرهیز می‌کنیم
        # روش سریع: تبدیل به palette موقت
        p = img.convert("RGB")
        data = p.tobytes()
        old_b = bytes(old)
        new_b = bytes(new)
        # جایگزینی با replace روی bytes (سریع در C)
        cnt = data.count(old_b)
        if cnt:
            data = data.replace(old_b, new_b)
            img.frombytes(data)
    return


# ─── PDF build (ترتیبی، کم‌حافظه — مشابه sarrast) ─────────────────────────


def _images_to_pdf_sync(img_paths: List[str], out_path: str) -> bool:
    from PIL import Image

    try:
        first = True
        for p in img_paths:
            try:
                img = Image.open(p).convert("RGB")
            except Exception:
                continue
            if first:
                img.save(out_path, "PDF", resolution=96.0)
                first = False
            else:
                img.save(out_path, "PDF", append=True, resolution=96.0)
        return not first
    except Exception as e:
        logger.error("[ComicTr] PDF build failed: %s", e)
        return False


# ─── main entry ────────────────────────────────────────────────────────────


async def translate_comic_pdf(
    pdf_path: str,
    output_path: Optional[str] = None,
    progress_cb: ProgressCallback = None,
    max_pages: int = 40,
) -> Tuple[bool, str]:
    """ترجمه‌ی کامل PDF کامیک به فارسی.

    Args:
        pdf_path: مسیر PDF ورودی
        output_path: مسیر PDF خروجی (پیش‌فرض: <input>_fa.pdf)
        progress_cb: async callback(str) برای گزارش پیشرفت
        max_pages: سقف صفحات (محافظت RAM)

    Returns:
        (success, output_path_or_error_message)
    """

    async def _prog(msg: str):
        if progress_cb:
            try:
                await progress_cb(msg)
            except Exception:
                pass

    if not os.path.exists(pdf_path):
        return False, f"input not found: {pdf_path}"
    if output_path is None:
        base, _ = os.path.splitext(pdf_path)
        output_path = base + "_fa.pdf"

    await _prog("🔤 آماده‌سازی فونت فارسی...")
    if not await _ensure_persian_font():
        return False, "Persian font could not be downloaded (sarrast.com unreachable?)"

    workdir = os.path.join("/tmp", f"comictr_{uuid.uuid4().hex[:12]}")
    os.makedirs(workdir, exist_ok=True)
    loop = asyncio.get_event_loop()

    try:
        # 1) PDF → images
        await _prog("📄 استخراج صفحات PDF...")
        try:
            img_paths = await loop.run_in_executor(
                None, _pdf_to_images_sync, pdf_path, workdir
            )
        except ImportError:
            return False, "PyMuPDF not installed (pip install PyMuPDF)"
        if not img_paths:
            return False, "no pages could be rendered from PDF"
        if len(img_paths) > max_pages:
            img_paths = img_paths[:max_pages]
        n = len(img_paths)
        await _prog(f"📄 {n} صفحه استخراج شد")

        out_imgs = []
        total_bubbles = 0
        from PIL import Image as _PILImage
        for pi, ipath in enumerate(img_paths):
            await _prog(f"🔍 OCR صفحه {pi + 1}/{n}...")
            lines = await loop.run_in_executor(None, _ocr_space_sync, ipath)

            if lines is None:
                # OCR کامل fail شد → صفحه دست‌نخورده
                logger.warning("[ComicTr] OCR failed for page %d — keeping original", pi + 1)
                out_imgs.append(ipath)
                continue

            if not lines:
                # متنی پیدا نشد → صفحه بدون تغییر
                out_imgs.append(ipath)
                continue

            # ابعاد واقعی صفحه برای فیلتر واترمارک
            try:
                with _PILImage.open(ipath) as _im:
                    page_w, page_h = _im.size
            except Exception:
                page_w, page_h = 10**9, 10**9  # فیلتر عملاً غیرفعال

            clusters = _cluster_lines(lines, page_w, page_h)
            if not clusters:
                out_imgs.append(ipath)
                continue

            await _prog(f"🌍 ترجمه‌ی صفحه {pi + 1}/{n} ({len(clusters)} حباب)...")
            texts = [" ".join(c["texts"]) for c in clusters]
            translations = await _translate_texts(texts)
            total_bubbles += len(clusters)

            await _prog(f"✍️ بازنویسی صفحه {pi + 1}/{n}...")
            out_page = os.path.join(workdir, f"out_{os.path.basename(ipath)}")
            ok = await loop.run_in_executor(
                None, _render_page_sync, ipath, out_page, clusters, translations
            )
            out_imgs.append(out_page if ok else ipath)

        # 3) rebuild PDF
        await _prog("📦 ساخت PDF فارسی...")
        ok = await loop.run_in_executor(None, _images_to_pdf_sync, out_imgs, output_path)
        if not ok:
            return False, "PDF build failed"

        await _prog(f"✅ تمام شد! ({total_bubbles} حباب ترجمه شد)")
        logger.info("[ComicTr] DONE %s → %s (%d pages, %d bubbles)",
                    pdf_path, output_path, n, total_bubbles)
        return True, output_path

    finally:
        # cleanup صفحات موقت (خروجی PDF جدا است)
        try:
            for f in os.listdir(workdir):
                os.unlink(os.path.join(workdir, f))
            os.rmdir(workdir)
        except Exception:
            pass
