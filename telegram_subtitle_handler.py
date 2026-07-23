"""
telegram_subtitle_handler.py

اتصال subtitle_extractor.py به یک ربات تلگرامی نوشته شده با Telethon.
دو حالت پشتیبانی می‌شه:
    1) کاربر فایل ویدیویی رو مستقیم توی تلگرام می‌فرسته (video یا document)
    2) کاربر یک لینک مستقیم دانلود (URL) می‌فرسته

نیازمندی:
    pip install telethon aiohttp

نحوه استفاده در ربات اصلی‌ت:
    from telegram_subtitle_handler import register_subtitle_handlers
    register_subtitle_handlers(client)

    (client همون TelegramClient خودته که قبلاً ساختی)
"""

import os
import re
import tempfile
import shutil

import aiohttp
from telethon import events

from subtitle_extractor import extract_subtitles


URL_REGEX = re.compile(r"https?://\S+")

# پسوندهایی که به‌عنوان فایل ویدیویی در نظر می‌گیریم (وقتی کاربر document می‌فرسته)
VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".avi", ".mov", ".webm", ".flv", ".ts", ".m4v", ".wmv"
}


async def download_video_from_event(event, download_dir):
    """
    فایل ویدیویی پیوست‌شده به پیام تلگرام (video یا document) رو دانلود می‌کنه.
    برمی‌گردونه: مسیر فایل دانلود شده
    """
    file_path = await event.download_media(file=download_dir)
    if file_path is None:
        raise ValueError("امکان دانلود فایل از پیام وجود نداشت.")
    return file_path


async def download_video_from_url(url, download_dir):
    """
    فایل ویدیویی رو از یک لینک مستقیم (http/https) دانلود می‌کنه.
    برمی‌گردونه: مسیر فایل دانلود شده
    """
    filename = os.path.basename(url.split("?")[0]) or "downloaded_video"
    if not os.path.splitext(filename)[1]:
        filename += ".mkv"  # فرض پیش‌فرض اگه پسوند مشخص نبود

    out_path = os.path.join(download_dir, filename)

    timeout = aiohttp.ClientTimeout(total=None)  # فایل‌های حجیم -> بدون تایم‌اوت کلی
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise ValueError(f"دانلود ناموفق بود، کد وضعیت: {resp.status}")
            with open(out_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(1024 * 1024):
                    f.write(chunk)

    return out_path


async def process_video_and_send_subs(event, video_path):
    """
    از روی مسیر یک فایل ویدیویی، سافت‌ساب‌ها رو استخراج و برای کاربر ارسال می‌کنه.
    """
    temp_out_dir = tempfile.mkdtemp(prefix="subs_")
    try:
        await event.respond("در حال بررسی و استخراج زیرنویس‌ها... ⏳")

        sub_files = extract_subtitles(video_path, output_dir=temp_out_dir)

        if not sub_files:
            await event.respond("هیچ سافت‌سابی توی این فایل پیدا نشد.")
            return

        for sub_path in sub_files:
            await event.respond(file=sub_path)

        await event.respond(f"تعداد {len(sub_files)} فایل زیرنویس استخراج و ارسال شد. ✅")

    finally:
        shutil.rmtree(temp_out_dir, ignore_errors=True)


def register_subtitle_handlers(client):
    """
    هندلرهای مربوط به دریافت فایل و لینک رو روی client (TelegramClient) ثبت می‌کنه.
    """

    @client.on(events.NewMessage(func=lambda e: e.video or _is_video_document(e)))
    async def handle_video_message(event):
        download_dir = tempfile.mkdtemp(prefix="video_")
        try:
            await event.respond("در حال دانلود فایل ویدیویی... 📥")
            video_path = await download_video_from_event(event, download_dir)
            await process_video_and_send_subs(event, video_path)
        except Exception as e:
            await event.respond(f"خطا: {e}")
        finally:
            shutil.rmtree(download_dir, ignore_errors=True)

    @client.on(events.NewMessage(func=lambda e: bool(e.raw_text and URL_REGEX.search(e.raw_text))))
    async def handle_link_message(event):
        url_match = URL_REGEX.search(event.raw_text)
        if not url_match:
            return
        url = url_match.group(0)

        download_dir = tempfile.mkdtemp(prefix="video_")
        try:
            await event.respond("در حال دانلود فایل از لینک... 📥")
            video_path = await download_video_from_url(url, download_dir)
            await process_video_and_send_subs(event, video_path)
        except Exception as e:
            await event.respond(f"خطا در دانلود یا پردازش لینک: {e}")
        finally:
            shutil.rmtree(download_dir, ignore_errors=True)


def _is_video_document(event):
    """چک می‌کنه که آیا document ارسال‌شده یک فایل ویدیویی هست یا نه."""
    if not event.document:
        return False
    for attr in event.document.attributes:
        if hasattr(attr, "file_name") and attr.file_name:
            ext = os.path.splitext(attr.file_name)[1].lower()
            if ext in VIDEO_EXTENSIONS:
                return True
    mime = event.document.mime_type or ""
    return mime.startswith("video/")
