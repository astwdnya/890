#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════════
#  File Explorer + Renamer — واکنش به هر داکیومنت با منوی شیشه‌ای
# ───────────────────────────────────────────────────────────────────
#  ۱) 🔍 جستجو در فایل:
#     اگه فایل آرشیو باشه (zip / apk / rar / 7z / tar و ...) محتویاتش
#     به‌صورت دکمه‌های شیشه‌ای (اینلاین) نشون داده میشه؛ پوشه‌ها قابل
#     باز شدن هستن و با زدن روی هر فایل، همون فایل جداگانه برمی‌گرده.
#  ۲) ✏️ تغییر نام:
#     ربات اسم جدید رو می‌پرسه (مثلاً mamad.apk) و همون فایل رو با
#     اسم جدید برمی‌گردونه (بدون هیچ تغییری توی محتوا).
#  ۳) ☁️ آپلود به Filebin (https://filebin.net):
#     فایل رو استریمی و با نوار پیشرفت روی filebin.net آپلود می‌کنه.
#     هوشمند: پسوندهای بلاک‌شده (exe/apk/msi/dll/scr) مستقیم با پسوند
#     .zip آپلود میشن (مثلاً mamad.apk → mamad.apk.zip) و اگه سایت هر
#     پسوند دیگه‌ای رو رد کرد، دوباره با .zip تلاش میشه.
#     نتیجه سه تا لینک میده: ⚡️ لینک دانلود مستقیم واقعی (URL امضاشده
#     S3 — ربات قدم تأیید کوکی رو خودکار انجام میده؛ حدود ۱۵ دقیقه
#     اعتبار داره) + 📄 لینک صفحه فایل + 🗃 لینک صفحه باکس.
# ۴) 🎬 آپلود برای پخش در VLC:
#     فایل رو روی هاستِ «لینک مستقیم» می‌ذاره که لینکش بایت خام میده و
#     مستقیم تو VLC و بقیه پلیرها پلی میشه (برخلاف Filebin که صفحه
#     تأیید داره).
#     🆕 زنجیره‌ی fallback خودکار — اگه هاستی شکست بخوره (مثل خطای 500
#     لیترباکس) بدون دخالت کاربر سراغ بعدی میره:
#       ۱. سرور خودم (PUBLIC_BASE_URL + file_server.py → لینک پابلیک
#          با Range، بدون آپلود شبکه‌ای، ۶ ساعت اعتبار)
#       ۲. Pixeldrain (اگه PIXDRAIN_API_KEY ست باشه — تا ۲۰ گیگ،
#          لینک پخش + دانلود + صفحه)
#       ۳. Litterbox (بدون ثبت‌نام، تا ۱ گیگ، ۷۲ ساعته)
#       ۴. Catbox (بدون ثبت‌نام، تا ۲۰۰ مگ، دائمی)
#       ۵. Uguu (بدون ثبت‌نام، تا ۱۲۸ مگ، ۳ ساعته — بایت خام + Range)
#       ۶. Gofile (با GOFILE_API_KEY → directLink واقعی؛ بدون کلید فقط
#          صفحه‌ی دانلود — آخرِ خط)
#     هر هاست حداکثر ۲ تلاش میشه (خطای احراز هویت استثنا — مستقیم بعدی).
#  ۴.۵ 🧹 حذف خودکار + دستور /clean:
#     فایل‌های «سرور خودمون» بعد از SELF_EXPIRY_HOURS (۶ ساعت) و فایل‌های
#     پیکسل‌درین بعد از PIXELDRAIN_AUTO_DELETE_HOURS (۶ ساعت) خودکار پاک
#     میشن. دستور /clean هم منوی پاکسازی دستی میده: پیکسل‌درین / گوفایل /
#     سرور خودمون → لیست فایل‌ها → حذف همه یا یکی‌یکی.
#  ۵) ❌ لغو (در همه مراحل):
#     منوی اصلی دکمه «بستن» داره؛ آپلودهای Filebin/VLC و عملیات تغییر نام
#     هم موقع دانلود/آپلود دکمه «لغو» دارن که همون لحظه عملیات رو قطع
#     می‌کنه و فایل‌های موقت رو پاک می‌کنه.
# ═══════════════════════════════════════════════════════════════════

import asyncio
import html
import json
import logging
import math
import os
import re
import secrets
import shutil
import tarfile
import time
import zipfile
from typing import Callable, Dict, Optional
from urllib.parse import quote

from telethon import Button, events
from telethon.errors import MessageNotModifiedError
from telethon.tl.types import (
    DocumentAttributeAnimated,
    DocumentAttributeSticker,
    DocumentAttributeVideo,
)

# aiohttp — برای آپلود به Filebin (تو requirements.txt هست)
try:
    import aiohttp  # type: ignore
except Exception:
    aiohttp = None

# کتابخانه‌های اختیاری — اگه نصب نباشن فقط فرمت مربوطه غیرفعال میشه
try:
    import py7zr  # type: ignore
except Exception:
    py7zr = None

try:
    import rarfile  # type: ignore
except Exception:
    rarfile = None

logger = logging.getLogger("file_explorer")

# ───────────────────────── فرمت‌های پشتیبانی‌شده ─────────────────────────
# خانواده ZIP — همه اینا ساختار zip دارن و با zipfile باز میشن
ZIP_FAMILY = {
    ".zip", ".apk", ".jar", ".ipa", ".xapk", ".apks", ".apkm",
    ".docx", ".xlsx", ".pptx", ".epub", ".cbz", ".odt", ".ods",
    ".odp", ".whl", ".aar", ".vsix", ".crx",
}
TAR_FAMILY = {".tar", ".tgz", ".tbz2", ".txz"}
SEVENZ_FAMILY = {".7z"}
RAR_FAMILY = {".rar"}

# ویدیوها هندلر جدا دارن (video_receive_handler) — دست نمی‌زنیم
VIDEO_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
    ".mpg", ".mpeg", ".3gp", ".ts", ".mts", ".ogv", ".rmvb", ".f4v",
}

MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024     # سقف دانلود آرشیو برای باز کردن
MAX_SEND_BYTES = 1900 * 1024 * 1024            # سقف ارسال فایل به تلگرام (~2GB)
SESSION_TTL = 45 * 60                          # عمر سشن مرور آرشیو
RENAME_TTL = 15 * 60                           # مهلت فرستادن اسم جدید
PAGE_SIZE = 28                                 # تعداد دکمه در هر صفحه
MAX_SESSIONS = 6                               # حداکثر آرشیو باز همزمان (محدودیت دیسک)
MAX_NAME_LEN = 120

# ───────────────────────── Filebin (آپلود ابری) ─────────────────────────
# API ساده: POST /{bin}/{filename} با بدنه‌ی باینری → 201 Created + JSON
# پسوندهای بلاک‌شده روی filebin.net (تست تجربی + منطق filebin2):
#   403 "Illegal file extension" — برای اینا مستقیم با .zip آپلود می‌کنیم
FILEBIN_BASE = os.environ.get("FILEBIN_BASE", "https://filebin.net").rstrip("/")
FILEBIN_MAX_BYTES = 4 * 1024 * 1024 * 1024          # 4GB — بیشتر از سقف تلگرام
FILEBIN_BLOCKED_EXTS = {".exe", ".apk", ".msi", ".dll", ".scr"}
FILEBIN_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# ───────── VLC: Pixeldrain / Litterbox (لینک مستقیم قابل پخش) ─────────
# برخلاف filebin (که برای دانلود صفحه تأیید HTML میده)، لینک این‌ها بایت
# خام + Accept-Ranges میده و مستقیم تو VLC پلی میشه. تست تجربی:
#   • litterbox: POST multipart به api.php → لینک https://litter.catbox.moe/xx.ext
#     (بدون ثبت‌نام، سقف ۱ گیگ، ۷۲ ساعت، Range → 206 ✓)
#   • pixeldrain: PUT /api/file/{name} با BasicAuth (پسورد = API Key
#     اکانت رایگان) → سقف ۲۰ گیگ، لینک /api/file/{id} تو VLC پلی میشه
PIXDRAIN_BASE = os.environ.get("PIXDRAIN_BASE", "https://pixeldrain.com").rstrip("/")
PIXDRAIN_MAX_BYTES = 10 * 1024 * 1024 * 1024        # 10GB — سقف اکانت رایگان (GET /api/user)
LITTERBOX_BASE = os.environ.get("LITTERBOX_BASE", "https://litterbox.catbox.moe").rstrip("/")
LITTERBOX_MAX_BYTES = 1000 * 1024 * 1024            # ۱ گیگ — سقف بدون ثبت‌نام

# ── 🆕 سرور خودم (Railway) — لینک پابلیک برای VLC ──
# تو Railway Variables بذار: PUBLIC_BASE_URL = دامنه‌ای که Generate Domain میده
# (مثل https://890-production-xxxx.up.railway.app). file_server.py فایل رو با
# توکن ثبت می‌کنه و Flask (keep-alive) با پشتیبانی Range سروش میده.
SELF_MAX_BYTES = int(os.environ.get("SELF_MAX_MB", "10000")) * 1024 * 1024
SELF_EXPIRY_HOURS = float(os.environ.get("SELF_EXPIRY_HOURS", "6"))
CATBOX_MAX_BYTES = 200 * 1024 * 1024                # سقف catbox.moe (دائمی)
UGUU_BASE = os.environ.get("UGUU_BASE", "https://uguu.se/upload")
UGUU_MAX_BYTES = 128 * 1024 * 1024                  # سقف uguu.se (۳ ساعت)

# ── 🆕 حذف خودکار فایل‌ها بعد از چند ساعت (دیفالت روشن) ──
# سرور خودم: file_server بعد از SELF_EXPIRY_HOURS خودش پاک می‌کنه.
# پیکسل‌درین: شناسه‌ی هر آپلود تو فایل state ثبت میشه و یه تاسک پس‌زمینه
#   هر ۱۰ دقیقه فایل‌های قدیمی‌تر از PIXELDRAIN_AUTO_DELETE_HOURS رو با
#   DELETE /api/file/{id} پاک می‌کنه. 0 = غیرفعال.
#   ⚠️ برای امنیت، دیفالت فقط فایل‌هایی که خود ربات آپلود کرده پاک میشن؛
#   اگه می‌خوای هر فایل قدیمی اکانت (حتی دستی) هم پاک بشه:
#   PIXELDRAIN_DELETE_ALL_OLD="1"
PIXELDRAIN_AUTO_DELETE_HOURS = float(os.environ.get("PIXELDRAIN_AUTO_DELETE_HOURS", "6"))
PIXELDRAIN_DELETE_ALL_OLD = os.environ.get("PIXELDRAIN_DELETE_ALL_OLD", "0") == "1"
# گوفایل: دیفالت خاموش (خودش بعد از ۱۰ روز بی‌دانلود پاک می‌کنه)؛
# اگه خواستی ساعت‌محور پاک بشه مثلاً GOFILE_AUTO_DELETE_HOURS="6" بذار.
GOFILE_AUTO_DELETE_HOURS = float(os.environ.get("GOFILE_AUTO_DELETE_HOURS", "0"))

# ⚠️ FIX: ReplyInlineMarkup(rows=[]) روی سرور تلگرام نامعتبره و خطای
# ReplyMarkupInvalidError میده. برای «غیرفعال کردن» دکمه‌های قبلی موقع ادیت
# باید یه دکمه‌ی no-op معتبر (کلیکش هیچ کاری نمی‌کنه) جایگزین بشه.
# (buttons=None یعنی دکمه‌های قبلی سر جاشون بمونن)
def _idle_rows(label: str):
    """یه ردیف دکمه no-op معتبر — جایگزین امن برای مارک‌آپ خالی."""
    return [[Button.inline(label, "fexnoop")]]


def _menu_rows(chat_id: int, msg_id: int):
    """منوی اصلی هر داکیومنت (جستجو / تغییر نام / Filebin / VLC / بستن)."""
    return [
        [Button.inline("🔍 جستجو در فایل", f"fexopen_{chat_id}_{msg_id}")],
        [Button.inline("✏️ تغییر نام", f"fren_{chat_id}_{msg_id}")],
        [Button.inline("☁️ آپلود به Filebin", f"fbin_{chat_id}_{msg_id}")],
        [Button.inline("🎬 آپلود برای پخش در VLC", f"fvlc_{chat_id}_{msg_id}")],
        [Button.inline("❌ بستن", f"fexdism_{chat_id}_{msg_id}")],
    ]


# ───────────── 🆕 زیرساخت لغو (دکمه ❌ لغو در همه مراحل) ─────────────
# هر عملیات (آپلود Filebin/VLC، تغییر نام) یه توکن یکتا می‌گیره؛ دکمه‌ی
# «لغو» اون توکن رو تو این ست ثبت می‌کنه و حلقه‌ی دانلود/آپلود تو اولین
# چانک بعدی با _UploadAborted می‌ایسته. توکن‌ها موقع پایان عملیات پاک می‌شن.
_ABORT_FLAGS: set = set()
# 🆕 تسک‌های آپلود جاری (توکن → تسک) — دکمه لغو با کنسل کردنِ تسک، آپلود
# aiohttp رو قطع می‌کنه (مسیر استاندارد لغو؛ raise از داخل payload باعث
# هنگ می‌شه، برای همین آپلود با کنسل-تسک قطع میشه)
_UPLOADER_TASKS: dict = {}


class _UploadAborted(BaseException):
    """سیگنال داخلی: کاربر دکمه ❌ لغو رو زده.

    ⚠️ عمداً از BaseException ارث می‌بره تا توسط except Exception های
    میانی (مثل مدیریت خطای شبکه‌ی آپلودر) بلعیده نشه."""


async def _run_upload_with_abort(token: str, coro):
    """🆕 آپلود رو به‌صورت تسکِ جدا اجرا می‌کنه تا دکمه ❌ لغو بتونه قطعش کنه.

    اگه پرچم لغو ست شده باشه و تسک کنسل بشه → _UploadAborted؛ اگه کنسل
    شدنِ بیرونی بود (مثل خاموشی ربات) → CancelledError دوباره raise میشه."""
    # اگه لغو قبل از شروع آپلود (مثلاً وسط دانلود) زده شده بود
    if token in _ABORT_FLAGS:
        raise _UploadAborted()
    task = asyncio.ensure_future(coro)
    _UPLOADER_TASKS[token] = task
    try:
        return await task
    except asyncio.CancelledError:
        if token in _ABORT_FLAGS:
            raise _UploadAborted() from None
        raise
    finally:
        _UPLOADER_TASKS.pop(token, None)


def _abort_rows(token: str):
    """ردیف دکمه‌ی لغو برای پیام‌های پیشرفت دانلود/آپلود."""
    return [[Button.inline("❌ لغو", f"fexab_{token}")]]


def _filebin_remote_name(name: str) -> str:
    """اسم فایل برای آپلود به Filebin — فارسی/فاصله مجازه، فقط کنترلی/مسیر تمیز میشه."""
    name = _sanitize_filename(name) or "file"
    return name[:180]


class _ProgressFilePayload(aiohttp.payload.Payload):
    """Payload استریمی aiohttp با حجم مشخص (Content-Length درست) + نوار پیشرفت.

    ⚠️ سایت filebin.net آپلود chunked بدون Content-Length رو با 411 رد می‌کنه،
    پس باید payload دارای size بدیم — جنریتور ساده کافی نیست."""

    def __init__(self, path: str, prog, total: int, chunk_size: int = 512 * 1024, **kwargs):
        self._file = open(path, "rb")
        self._path = path
        self._prog = prog
        self._total = max(int(total), 1)
        self._chunk_size = chunk_size
        self._sent = 0
        super().__init__(
            self._file,
            content_type=kwargs.pop("content_type", "application/octet-stream"),
        )
        # ⚠️ aiohttp 3.13+ کیورد size رو تو __init__ نمی‌خونه — مستقیم ست می‌کنیم
        # تا Content-Length درست ارسال بشه (filebin بدون Content-Length → 411)
        self._size = os.path.getsize(path)

    def decode(self, encoding: str = "utf-8", errors: str = "strict") -> str:
        # payload باینری — نمایش رشته‌ای نداره
        return ""

    async def write(self, writer):
        while True:
            chunk = self._file.read(self._chunk_size)
            if not chunk:
                break
            await writer.write(chunk)
            self._sent += len(chunk)
            self._prog.cb(self._sent, self._total)

    async def close(self):
        if not self._file.closed:
            self._file.close()


async def _filebin_upload(bin_name: str, remote_name: str, local_path: str, prog) -> tuple:
    """آپلود استریمی یه فایل به Filebin — خروجی: (status_code, body_text).

    bin_name: اسم باکس (تصادفی)، remote_name: اسم فایل روی سایت،
    prog: نمونه‌ی _ProgEdit برای نوار پیشرفت آپلود."""
    url = f"{FILEBIN_BASE}/{bin_name}/{quote(remote_name)}"
    total = os.path.getsize(local_path)

    ctype = "application/zip" if remote_name.lower().endswith(".zip") else "application/octet-stream"
    headers = {
        "Accept": "application/json, */*",
        "User-Agent": FILEBIN_UA,
    }
    payload = _ProgressFilePayload(local_path, prog, total, content_type=ctype)
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=180)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, data=payload, headers=headers) as resp:
            try:
                body = (await resp.text(errors="ignore"))[:400]
            except Exception:
                body = ""
            return resp.status, body


async def _filebin_direct_url(bin_name: str, remote_name: str) -> Optional[str]:
    """لینک دانلود مستقیمِ واقعی (URL امضاشده S3) رو از filebin بیرون می‌کشه.

    filebin.net برای GET بدون کوکی «verified» صفحه HTML برمی‌گردونه (نه فایل!)
    — طبق مستندات رسمی‌شون، بعد از تأیید، کلاینت به URL امضاشده S3 ریدایرکت
    میشه که بایت خام + Range میده. ربات این قدم رو خودکار انجام میده:
      GET اول → صفحه تأیید + Set-Cookie: verified=...
      GET دوم (همون کوکی) → 302 → Location: URL امضاشده S3 (≈۱۵ دقیقه اعتبار)
    هر مشکلی پیش بیاد None برمی‌گرده تا لینک ساده صفحه فایل جایگزین بشه."""
    if aiohttp is None:
        return None
    url = f"{FILEBIN_BASE}/{bin_name}/{quote(remote_name)}"
    headers = {"Accept": "application/json, */*", "User-Agent": FILEBIN_UA}
    timeout = aiohttp.ClientTimeout(total=90, sock_connect=30, sock_read=30)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # قدم ۱ — گرفتن صفحه تأیید و برداشت دستی کوکی verified
            # (⚠️ aiohttp کوکی هاست‌های IP رو تو jar ذخیره نمی‌کنه — دستی می‌بریم)
            async with session.get(url, headers=headers) as r1:
                await r1.read()
                if r1.status != 200:
                    return None
                set_cookie = r1.headers.get("Set-Cookie", "")
            cookie_pair = set_cookie.split(";")[0].strip()  # «verified=...»
            if not cookie_pair or "=" not in cookie_pair:
                return None
            # قدم ۲ — با کوکی تأیید: 302 به URL امضاشده S3 (بدون دنبال کردن)
            headers2 = {**headers, "Cookie": cookie_pair}
            async with session.get(url, headers=headers2, allow_redirects=False) as r2:
                if r2.status in (301, 302, 303, 307, 308):
                    return r2.headers.get("Location") or None
    except Exception as e:
        logger.warning(f"[FileExplorer] filebin direct-url probe failed: {e}")
    return None


async def _pixeldrain_upload(remote_name: str, local_path: str, prog, api_key: str) -> dict:
    """آپلود استریمی به pixeldrain.com — نیاز به API Key اکانت رایگان داره.

    PUT /api/file/{name} با BasicAuth (پسورد = کلید) → JSON {"success":true,"id":...}
    خروجی: dict با play_url (لینک پخش در VLC) / dl_url / page_url."""
    url = f"{PIXDRAIN_BASE}/api/file/{quote(remote_name)}"
    total = os.path.getsize(local_path)
    headers = {"Accept": "application/json", "User-Agent": FILEBIN_UA}
    auth = aiohttp.BasicAuth("", api_key)  # طبق مستندات: کلید تو فیلد پسورد
    payload = _ProgressFilePayload(local_path, prog, total)
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=180)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.put(url, data=payload, headers=headers, auth=auth) as resp:
                body = (await resp.text(errors="ignore"))[:500]
                try:
                    data = json.loads(body)
                except Exception:
                    data = {}
                if resp.status == 200 and data.get("success") and data.get("id"):
                    fid = str(data["id"])
                    # 🆕 ثبت برای حذف خودکار بعد از PIXELDRAIN_AUTO_DELETE_HOURS
                    _pd_track_upload(fid, remote_name)
                    return {
                        "name": remote_name,
                        "play_url": f"{PIXDRAIN_BASE}/api/file/{fid}",
                        "dl_url": f"{PIXDRAIN_BASE}/api/file/{fid}?download",
                        "page_url": f"{PIXDRAIN_BASE}/u/{fid}",
                    }
                if resp.status == 401:
                    raise _FeAuthError(
                        "کلید API پیکسل‌درین معتبر نیست — مقدار "
                        "<code>PIXDRAIN_API_KEY</code> رو تو <code>.env</code> چک کن"
                    )
                # 🆕 اکانت با ایمیل تأییدنشده اجازه‌ی آپلود نداره (تست تجربی 403)
                # — مثل خطای کلید، بدون ری‌تای مستقیم هاست بعدی
                if resp.status == 403 and "verify" in body.lower():
                    raise _FeAuthError(
                        "اکانت پیکسل‌درین ایمیل‌ش رو تأیید نکرده — وارد "
                        f"<code>{PIXDRAIN_BASE}</code> شو، ایمیل mamadjavad900 رو "
                        "verify کن تا آپلود فعال بشه"
                    )
                msg = str(data.get("message") or body[:120])
                raise _FeError(
                    f"پیکسل‌درین آپلود رو قبول نکرد (کد {resp.status}):\n<code>{_esc(msg)}</code>"
                )
    except _FeError:
        raise
    except Exception as e:
        raise _FeError(f"خطای اتصال به پیکسل‌درین:\n<code>{_esc(str(e)[:120])}</code>")


async def _litterbox_upload(remote_name: str, local_path: str, prog) -> dict:
    """آپلود استریمی به litterbox.catbox.moe — بدون ثبت‌نام (سقف ۱ گیگ، ۷۲ ساعت).

    POST multipart به api.php با فیلدهای reqtype=fileupload / time=72h /
    fileToUpload → جواب: متن ساده‌ی لینک مستقیم (بایت خام + Range → VLC ✓)
    خروجی: dict با play_url (همون لینک مستقیم برای پخش و دانلود)."""
    url = f"{LITTERBOX_BASE}/resources/internals/api.php"
    total = os.path.getsize(local_path)
    payload = _ProgressFilePayload(local_path, prog, total)
    form = aiohttp.FormData()
    form.add_field("reqtype", "fileupload")
    form.add_field("time", "72h")
    form.add_field("fileToUpload", payload, filename=remote_name or "file.bin")
    headers = {"Accept": "*/*", "User-Agent": FILEBIN_UA}
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=180)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, data=form, headers=headers) as resp:
                body = (await resp.text(errors="ignore")).strip()
                if resp.status == 200 and body.startswith("http"):
                    link = body.split()[0][:300]
                    return {"name": remote_name, "play_url": link, "dl_url": link, "page_url": ""}
                raise _FeError(
                    f"Litterbox آپلود رو قبول نکرد (کد {resp.status}):\n"
                    f"<code>{_esc(body[:120])}</code>"
                )
    except _FeError:
        raise
    except Exception as e:
        raise _FeError(f"خطای اتصال به Litterbox:\n<code>{_esc(str(e)[:120])}</code>")


# ───────── 🆕 سرور خودم (PUBLIC_BASE_URL) — لینک پابلیک بدون آپلود شبکه‌ای ─────────
def _self_server_base() -> str:
    """دامنه‌ی عمومی سرور خودم — از PUBLIC_BASE_URL؛ خالی/نامعتبر → "".

    تو Railway Variables بذار: PUBLIC_BASE_URL=https://<دامنه‌ی Generate Domain>"""
    base = (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    return base if base.startswith(("http://", "https://")) else ""


def _self_upload(remote_name: str, local_path: str, prog) -> dict:
    """ثبت فایل روی file_server → لینک پابلیک از دامنه‌ی خودم (سرور خودت).

    بدون آپلود شبکه‌ای — فقط symlink + توکن (فوری). Flask مسیر /f/<token>
    رو با پشتیبانی Range سرو می‌کنه (همون چیزی که VLC برای Seek لازم داره).
    فایل بعد از SELF_EXPIRY_HOURS (پیش‌فرض ۶ ساعت) خودکار پاک میشه."""
    from file_server import serve_file
    info = serve_file(
        local_path,
        title=remote_name,
        expires_in_hours=SELF_EXPIRY_HOURS,
        public_base_url=_self_server_base(),
    )
    return {"name": remote_name, "play_url": info["url"], "dl_url": info["url"], "page_url": ""}


async def _catbox_upload(remote_name: str, local_path: str, prog) -> dict:
    """آپلود استریمی به catbox.moe — دائمی (سقف ۲۰۰ مگ)، همون API لیترباکس.

    POST multipart به user/api.php با reqtype=fileupload → متن لینک
    https://files.catbox.moe/xx.ext (بایت خام + Range → VLC ✓)"""
    url = "https://catbox.moe/user/api.php"
    total = os.path.getsize(local_path)
    payload = _ProgressFilePayload(local_path, prog, total)
    form = aiohttp.FormData()
    form.add_field("reqtype", "fileupload")
    form.add_field("fileToUpload", payload, filename=remote_name or "file.bin")
    headers = {"Accept": "*/*", "User-Agent": FILEBIN_UA}
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=180)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, data=form, headers=headers) as resp:
                body = (await resp.text(errors="ignore")).strip()
                if resp.status == 200 and body.startswith("http"):
                    link = body.split()[0][:300]
                    return {"name": remote_name, "play_url": link, "dl_url": link, "page_url": ""}
                raise _FeError(
                    f"Catbox آپلود رو قبول نکرد (کد {resp.status}):\n"
                    f"<code>{_esc(body[:120])}</code>"
                )
    except _FeError:
        raise
    except Exception as e:
        raise _FeError(f"خطای اتصال به Catbox:\n<code>{_esc(str(e)[:120])}</code>")


async def _gofile_upload(remote_name: str, local_path: str, prog) -> dict:
    """آپلود به gofile.io — اگه GOFILE_API_KEY ست باشه با اکانت آپلود میشه
    و directLink واقعی (بایت خام) می‌گیره که تو VLC پخش میشه.

    بدون کلید: فقط downloadPage میده (HTML) — پخش مستقیم نداره.
    ۱) GET api.gofile.io/servers → اسم سرور  ۲) POST multipart به
    {server}.gofile.io/contents/uploadfile  ۳) با توکن: GET
    /contents/{fileId} → directLink"""
    total = os.path.getsize(local_path)
    token = _gofile_token()
    headers = {"Accept": "application/json, */*", "User-Agent": FILEBIN_UA}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=180)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # قدم ۱ — انتخاب سرور فعال
            async with session.get("https://api.gofile.io/servers", headers=headers) as r1:
                if r1.status == 401:
                    raise _FeAuthError(
                        "کلید گوفایل معتبر نیست — <code>GOFILE_API_KEY</code> رو چک کن"
                    )
                js = await r1.json(content_type=None)
            data = js.get("data") if isinstance(js, dict) else None
            servers = (data or {}).get("servers") or []
            names = [s.get("name") for s in servers if isinstance(s, dict) and s.get("name")]
            if not names:
                raise _FeError("Gofile لیست سرورها رو نداد — احتمالاً سرویسش در دسترس نیست")
            # قدم ۲ — آپلود استریمی با نوار پیشرفت
            payload = _ProgressFilePayload(local_path, prog, total)
            form = aiohttp.FormData()
            form.add_field("file", payload, filename=remote_name or "file.bin")
            async with session.post(
                f"https://{names[0]}.gofile.io/contents/uploadfile",
                data=form, headers=headers,
            ) as resp:
                if resp.status == 401:
                    raise _FeAuthError(
                        "کلید گوفایل معتبر نیست — <code>GOFILE_API_KEY</code> رو چک کن"
                    )
                body = (await resp.text(errors="ignore"))[:800]
                try:
                    js2 = json.loads(body)
                except Exception:
                    js2 = {}
                d = (js2.get("data") or {}) if isinstance(js2, dict) else {}
                page = d.get("downloadPage") or ""
                fid = d.get("fileId") or d.get("id") or ""
                # فقط اگر directLink واقعی از API اومد پخش مستقیم حساب میشه
                # (ساخت دستی URL بدون directLink → صفحه HTML میده، نه بایت خام)
                direct = d.get("directLink") or ""
                # 🆕 با توکن اکانت — directLink رو از API محتوا بگیر
                if token and fid and not direct:
                    try:
                        async with session.get(
                            f"https://api.gofile.io/contents/{fid}",
                            headers=_gofile_auth_headers(token),
                        ) as r3:
                            js3 = await r3.json(content_type=None)
                        d3 = (js3.get("data") or {}) if isinstance(js3, dict) else {}
                        direct = str(d3.get("directLink") or "")
                    except Exception:
                        direct = ""
                # 🆕 ثبت برای حذف خودکار اختیاری (GOFILE_AUTO_DELETE_HOURS)
                # + ذخیره‌ی rootFolder برای لیست‌کردن اکانت در /clean
                if fid:
                    _gofile_track_upload(fid, remote_name, str(d.get("parentFolder") or ""))
                if isinstance(js2, dict) and js2.get("status") == "ok" and (direct or page):
                    return {
                        "name": remote_name,
                        "play_url": direct,
                        "dl_url": direct or page,
                        "page_url": page,
                    }
                raise _FeError(
                    f"Gofile آپلود رو قبول نکرد (کد {resp.status}):\n<code>{_esc(body[:120])}</code>"
                )
    except _FeError:
        raise
    except Exception as e:
        raise _FeError(f"خطای اتصال به Gofile:\n<code>{_esc(str(e)[:120])}</code>")


async def _uguu_upload(remote_name: str, local_path: str, prog) -> dict:
    """آپلود استریمی به uguu.se — بدون ثبت‌نام (سقف ۱۲۸ مگ، ۳ ساعت).

    POST multipart به /upload با فیلد files[] → JSON با url مستقیم؛
    تست تجربی: بایت خام + Range → 206 ✓ (VLC پخش و Seek اوکیه)."""
    total = os.path.getsize(local_path)
    payload = _ProgressFilePayload(local_path, prog, total)
    form = aiohttp.FormData()
    form.add_field("files[]", payload, filename=remote_name or "file.bin")
    headers = {"Accept": "application/json, */*", "User-Agent": FILEBIN_UA}
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=180)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(UGUU_BASE, data=form, headers=headers) as resp:
                body = (await resp.text(errors="ignore"))[:600]
                try:
                    js = json.loads(body)
                except Exception:
                    js = {}
                files = (js or {}).get("files") or []
                url = (files[0].get("url") or "") if files else ""
                if js.get("success") and url:
                    return {"name": remote_name, "play_url": url, "dl_url": url, "page_url": ""}
                raise _FeError(
                    f"Uguu آپلود رو قبول نکرد (کد {resp.status}):\n<code>{_esc(body[:120])}</code>"
                )
    except _FeError:
        raise
    except Exception as e:
        raise _FeError(f"خطای اتصال به Uguu:\n<code>{_esc(str(e)[:120])}</code>")


# ═══════════ 🆕 حذف خودکار ابری (پیکسل‌درین / گوفایل) + لیست/حذف API ═══════════
# state آپلودهای پیکسل‌درین — برای اینکه حتی بعد از ری‌استارت هم بدونیم
# کدوم فایل‌ها مالِ رباته و کی آپلود شدن. (گوفایل لیست اکانت داره، state
# فقط به‌عنوان پشتیبان نگه میشه.)

def _state_path(kind: str) -> str:
    """مسیر فایل state — اول پوشه‌ی جاری، اگه نوشتن نشد /tmp."""
    name = f"{kind}_uploads.json"
    for base in (os.getcwd(), "/tmp"):
        try:
            p = os.path.join(base, name)
            with open(p, "a", encoding="utf-8"):
                pass
            return p
        except Exception:
            continue
    return os.path.join("/tmp", name)


def _load_state(kind: str) -> list:
    try:
        with open(_state_path(kind), "r", encoding="utf-8") as f:
            js = json.load(f)
        return js.get("uploads") or [] if isinstance(js, dict) else []
    except Exception:
        return []


def _save_state(kind: str, items: list) -> None:
    try:
        with open(_state_path(kind), "w", encoding="utf-8") as f:
            json.dump({"uploads": items[-2000:]}, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"[AutoDel] state save failed ({kind}): {e}")


def _pd_track_upload(fid: str, name: str) -> None:
    """ثبت آپلود پیکسل‌درین در state (برای حذف خودکار ساعت‌محور)."""
    if not fid:
        return
    items = _load_state("pixeldrain")
    items.append({"id": str(fid), "name": name or "", "ts": time.time()})
    _save_state("pixeldrain", items)


def _gofile_track_upload(fid: str, name: str, root_folder: str = "") -> None:
    if not fid:
        return
    items = _load_state("gofile")
    # rootFolder اکانت رو هم ذخیره کن — برای لیست‌کردن محتوا لازمه
    if root_folder and not any(x.get("id") == "__root__" for x in items):
        items.append({"id": "__root__", "root": root_folder, "ts": 0})
    items.append({"id": str(fid), "name": name or "", "ts": time.time()})
    _save_state("gofile", items)


def _gofile_root() -> str:
    """ایدی پوشه‌ی ریشه‌ی اکانت گوفایل — از آپلودهای قبلی ذخیره شده."""
    for x in _load_state("gofile"):
        if x.get("id") == "__root__":
            return str(x.get("root") or "")
    return ""


def _gofile_state_files() -> list:
    """فایل‌های ثبت‌شده در state (آپلودهای خود ربات) — وقتی API لیست نمی‌ده."""
    return [
        {
            "id": str(x.get("id") or ""),
            "name": str(x.get("name") or x.get("id") or ""),
            "size": int(x.get("size") or 0),
            "created_ts": float(x.get("ts") or 0),
            "direct_link": "",
        }
        for x in _load_state("gofile")
        if x.get("id") and x.get("id") != "__root__"
    ]


def _parse_ts(v) -> float:
    """تبدیل timestamp به unix — عدد یا ISO 8601 (پیکسل‌درین ISO میده)."""
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    try:
        return float(s)
    except Exception:
        pass
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return 0.0


def _gofile_token() -> str:
    return (os.environ.get("GOFILE_API_KEY") or "").strip()


def _gofile_auth_headers(token: str) -> dict:
    h = {"Accept": "application/json", "User-Agent": FILEBIN_UA}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


async def _pd_list_files(api_key: str) -> list:
    """لیست همه‌ی فایل‌های اکانت پیکسل‌درین — GET /api/user/files (BasicAuth).

    ⚠️ تست تجربی: /api/files وجود نداره (404) — مسیر درست /api/user/files.
    خروجی: [{id, name, size, created_ts}]"""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45)) as s:
        async with s.get(
            f"{PIXDRAIN_BASE}/api/user/files",
            auth=aiohttp.BasicAuth("", api_key),
            headers={"Accept": "application/json", "User-Agent": FILEBIN_UA},
        ) as r:
            if r.status == 401:
                raise _FeAuthError(
                    "کلید API پیکسل‌درین معتبر نیست — "
                    "<code>PIXDRAIN_API_KEY</code> رو چک کن"
                )
            try:
                js = await r.json(content_type=None)
            except Exception:
                js = {}
    files = (js.get("files") or []) if isinstance(js, dict) else []
    out = []
    for f in files:
        if not isinstance(f, dict) or not f.get("id"):
            continue
        out.append({
            "id": str(f["id"]),
            "name": str(f.get("name") or f["id"]),
            "size": int(f.get("size") or 0),
            "created_ts": _parse_ts(f.get("date_created") or f.get("dateCreated")),
        })
    return out


async def _pd_api_delete(session, api_key: str, fid: str) -> bool:
    """DELETE /api/file/{id} — حذف فایل از اکانت پیکسل‌درین."""
    try:
        async with session.delete(
            f"{PIXDRAIN_BASE}/api/file/{fid}",
            auth=aiohttp.BasicAuth("", api_key),
            headers={"Accept": "application/json", "User-Agent": FILEBIN_UA},
        ) as r:
            return r.status in (200, 204)
    except Exception:
        return False


async def _gofile_list_files(token: str) -> list:
    """لیست فایل‌های اکانت گوفایل برای /clean.

    ⚠️ تست تجربی (اکانت رایگان):
      • GET /contents/{rootFolder} → 401 error-notPremium (لیستِ محتوا پرمیومه)
      • directLink هم پرمیومه — لینک download/web/ فقط به صفحه‌ی HTML ریدایرکت می‌کنه
    پس برای اکانت رایگان از state محلی (آپلودهای خودِ ربات) استفاده می‌شه؛
    اگه اکانت پرمیوم بشه، لیست واقعی API برمی‌گرده.
    خروجی: [{id, name, size, created_ts, direct_link}]"""
    root = _gofile_root()
    url = f"https://api.gofile.io/contents/{root}" if root else "https://api.gofile.io/contents"
    js = {}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            async with s.get(url, headers=_gofile_auth_headers(token)) as r:
                body = await r.text(errors="ignore")
                low = body.lower()
                if "notpremium" in low:
                    return _gofile_state_files()
                if r.status == 401 or "wrongtoken" in low or "error-token" in low:
                    raise _FeAuthError(
                        "کلید گوفایل معتبر نیست — <code>GOFILE_API_KEY</code> رو چک کن"
                    )
                try:
                    js = json.loads(body)
                except Exception:
                    js = {}
    except _FeAuthError:
        raise
    except Exception:
        # timeout / قطع شبکه و ... → state محلی بهتر از شکستن /clean است
        return _gofile_state_files()
    if not (isinstance(js, dict) and js.get("status") == "ok"):
        # API لیست نداد (مثلاً error-notFound قبل از اولین آپلود) → state محلی
        return _gofile_state_files()
    d = js.get("data") or {}
    children = d.get("children") or []
    if isinstance(children, dict):
        children = list(children.values())
    out = []
    for c in children:
        if not isinstance(c, dict) or not c.get("id") or c.get("type") == "folder":
            continue
        out.append({
            "id": str(c["id"]),
            "name": str(c.get("name") or c["id"]),
            "size": int(c.get("size") or 0),
            "created_ts": _parse_ts(c.get("createTime")),
            "direct_link": str(c.get("directLink") or ""),
        })
    return out


async def _gofile_delete_contents(token: str, ids: list) -> bool:
    """DELETE api.gofile.io/contents — حذف یک یا چند محتوا از اکانت گوفایل."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as s:
            async with s.delete(
                "https://api.gofile.io/contents",
                headers=_gofile_auth_headers(token),
                json={"contentsId": [str(i) for i in ids]},
            ) as r:
                try:
                    js = await r.json(content_type=None)
                except Exception:
                    js = {}
                return isinstance(js, dict) and js.get("status") == "ok"
    except Exception:
        return False


async def _pd_autodel_sweep() -> tuple:
    """حذف خودکار فایل‌های قدیمی پیکسل‌درین — (deleted, failed).

    مرجع اصلی: لیست اکانت (GET /api/user/files) — حتی اگه state ری‌استارت شده
    باشه، فایل‌های ردیابی‌شده قدیمی پاک میشن. اگه لیست اکانت در دسترس نبود،
    از state محلی استفاده میشه. دیفالت فقط فایل‌های خودِ ربات (state)."""
    hours = PIXELDRAIN_AUTO_DELETE_HOURS
    api_key = (os.environ.get("PIXDRAIN_API_KEY") or "").strip()
    if hours <= 0 or not api_key or aiohttp is None:
        return (0, 0)
    cutoff = time.time() - hours * 3600
    deleted = failed = 0
    entries = []
    try:
        entries = await _pd_list_files(api_key)
    except Exception as e:
        logger.warning(f"[AutoDel] pixeldrain list failed: {e}")

    if entries:
        live_ids = {f["id"] for f in entries}
        tracked = {x.get("id") for x in _load_state("pixeldrain")}
        # فایل‌های خود ربات (ردیابی‌شده) قدیمی‌تر از cutoff → پاک
        # (+ همه‌ی فایل‌های قدیمی اکانت اگه PIXELDRAIN_DELETE_ALL_OLD=1)
        victims = [
            f for f in entries
            if f["created_ts"] > 0 and f["created_ts"] < cutoff
            and (f["id"] in tracked or PIXELDRAIN_DELETE_ALL_OLD)
        ]
        if victims:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45)) as s:
                for f in victims:
                    if await _pd_api_delete(s, api_key, f["id"]):
                        deleted += 1
                    else:
                        failed += 1
                    await asyncio.sleep(0.3)
            if deleted:
                logger.info("[AutoDel] pixeldrain: %d expired file(s) deleted", deleted)
        # sync state — اونی که تو اکانت نیست یعنی قبلاً پاک شده
        _save_state("pixeldrain", [x for x in _load_state("pixeldrain") if x.get("id") in live_ids])
    else:
        # لیست اکانت نشد → state محلی
        st = _load_state("pixeldrain")
        victims = [x for x in st if float(x.get("ts") or 0) > 0 and float(x.get("ts") or 0) < cutoff]
        if victims:
            vid = {x["id"] for x in victims}
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45)) as s:
                for x in victims:
                    if await _pd_api_delete(s, api_key, x["id"]):
                        deleted += 1
                    else:
                        failed += 1
                    await asyncio.sleep(0.3)
            if deleted:
                logger.info("[AutoDel] pixeldrain(state): %d expired file(s) deleted", deleted)
            _save_state("pixeldrain", [x for x in st if x.get("id") not in vid])
    return (deleted, failed)


async def _gofile_autodel_sweep() -> tuple:
    """حذف خودکار گوفایل — فقط اگه GOFILE_AUTO_DELETE_HOURS > 0 (دیفالت خاموش)."""
    hours = GOFILE_AUTO_DELETE_HOURS
    token = _gofile_token()
    if hours <= 0 or not token or aiohttp is None:
        return (0, 0)
    cutoff = time.time() - hours * 3600
    try:
        files = await _gofile_list_files(token)
    except Exception as e:
        logger.warning(f"[AutoDel] gofile list failed: {e}")
        return (0, 0)
    victims = [f["id"] for f in files if f["created_ts"] > 0 and f["created_ts"] < cutoff]
    deleted = failed = 0
    for i in range(0, len(victims), 50):
        chunk = victims[i:i + 50]
        if await _gofile_delete_contents(token, chunk):
            deleted += len(chunk)
        else:
            failed += len(chunk)
        await asyncio.sleep(0.5)
    if deleted:
        logger.info("[AutoDel] gofile: %d expired file(s) deleted", deleted)
    return (deleted, failed)


# ═══════════ 🆕 دستور /clean — پاکسازی دستی: پیکسل‌درین / گوفایل / سرور خودم ═══════════
_CLEAN_SESSIONS: dict = {}          # sid → {"host","files","ts","chat"}
_CLEAN_TTL = 30 * 60                # عمر سشن /clean
_CLEAN_HOSTS = {
    "pd": {"title": "پیکسل‌درین", "emoji": "🟣"},
    "go": {"title": "گوفایل", "emoji": "🟢"},
    "self": {"title": "سرور خودمون", "emoji": "🖥"},
}
_CLEAN_SHOW = 20                    # حداکثر دکمه‌ی فایل در هر لیست
_CLEAN_MAX_FILES = 200              # سقف فایل‌های لودشده در هر سشن


def _clean_host_rows():
    return [
        [Button.inline("🟣 پیکسل‌درین", "fclnh_pd")],
        [Button.inline("🟢 گوفایل", "fclnh_go")],
        [Button.inline("🖥 سرور خودمون", "fclnh_self")],
        [Button.inline("❌ بستن", "fclnx_none")],
    ]


def _clean_gc() -> None:
    now = time.time()
    stale = [k for k, v in _CLEAN_SESSIONS.items() if now - v.get("ts", 0) > _CLEAN_TTL]
    for k in stale:
        _CLEAN_SESSIONS.pop(k, None)


async def _clean_fetch(host: str) -> list:
    """لیست فایل‌های هاست انتخابی — برای /clean."""
    if host == "pd":
        api_key = (os.environ.get("PIXDRAIN_API_KEY") or "").strip()
        if not api_key:
            raise _FeError("کلید <code>PIXDRAIN_API_KEY</code> تو env تنظیم نیست")
        files = await _pd_list_files(api_key)
        files.sort(key=lambda x: x.get("created_ts") or 0, reverse=True)
        return files[:_CLEAN_MAX_FILES]
    if host == "go":
        token = _gofile_token()
        if not token:
            raise _FeError("کلید <code>GOFILE_API_KEY</code> تو env تنظیم نیست")
        files = await _gofile_list_files(token)
        files.sort(key=lambda x: x.get("created_ts") or 0, reverse=True)
        return files[:_CLEAN_MAX_FILES]
    if host == "self":
        from file_server import list_files
        out = []
        for f in list_files()[:_CLEAN_MAX_FILES]:
            out.append({
                "id": f["token"],
                "name": f["name"],
                "size": f["size"],
                "created_ts": 0,
                "expires_at": f["expires_at"],
            })
        return out
    return []


def _clean_remaining_secs(f: dict, host: str) -> str:
    """برچسب زمانی کوچک برای هر فایل."""
    if host == "self":
        left = int(f.get("expires_at", 0) - time.time())
        if left > 0:
            h = left // 3600
            m = (left % 3600) // 60
            return f"{h}س {m}د" if h else f"{m} دقیقه"
        return "منقضی"
    if host == "pd" and f.get("created_ts"):
        age_h = (time.time() - f["created_ts"]) / 3600
        if age_h >= 1:
            return f"{int(age_h)} ساعت پیش"
        return f"{int(age_h * 60)} دقیقه پیش"
    return ""


def _clean_render(sess: dict) -> tuple:
    """رندر لیست فایل‌ها برای /clean → (text, rows)."""
    host, sid = sess["host"], sess["sid"]
    files = sess["files"]
    meta = _CLEAN_HOSTS[host]
    shown = files[:_CLEAN_SHOW]
    lines = [
        f"{meta['emoji']} <b>فایل‌های {meta['title']}</b>",
        f"📄 تعداد کل: <b>{len(files)}</b>",
    ]
    if host == "self":
        lines.append(f"⏳ این فایل‌ها بعد از {SELF_EXPIRY_HOURS:g} ساعت خودکار پاک میشن")
    elif host == "pd" and PIXELDRAIN_AUTO_DELETE_HOURS > 0:
        lines.append(f"⏳ فایل‌های ربات بعد از {PIXELDRAIN_AUTO_DELETE_HOURS:g} ساعت خودکار پاک میشن")
    lines.append("")
    for f in shown:
        tag = _clean_remaining_secs(f, host)
        lines.append(
            f"• <code>{_esc(_short(f['name'], 34))}</code> ({_fmt_size(f.get('size', 0))})"
            + (f" — {tag}" if tag else "")
        )
    if len(files) > len(shown):
        lines.append(f"… و {len(files) - len(shown)} فایل دیگه")
    rows = []
    for i, f in enumerate(shown):
        rows.append([Button.inline(
            f"🗑 {_short(f['name'], 24)} ({_fmt_size(f.get('size', 0))})",
            f"fclnd_{sid}_{i}",
        )])
    rows.append([Button.inline(f"☢️ حذف همه ({len(files)})", f"fclnda_{sid}")])
    rows.append([
        Button.inline("🔄 بروزرسانی", f"fclnr_{sid}"),
        Button.inline("⬅️ هاست‌ها", f"fclnm_{sid}"),
    ])
    rows.append([Button.inline("❌ بستن", f"fclnx_{sid}")])
    return "\n".join(lines), rows


async def _clean_delete_one(host: str, f: dict) -> bool:
    """حذف یک فایل از هاست مربوطه."""
    try:
        if host == "self":
            from file_server import delete_file
            return bool(delete_file(f["id"]))
        if host == "pd":
            api_key = (os.environ.get("PIXDRAIN_API_KEY") or "").strip()
            if not api_key:
                return False
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45)) as s:
                return await _pd_api_delete(s, api_key, f["id"])
        if host == "go":
            token = _gofile_token()
            if not token:
                return False
            return await _gofile_delete_contents(token, [f["id"]])
    except Exception:
        return False
    return False


async def fe_clean_cmd(event):
    """دستور /clean — منوی انتخاب هاست برای پاکسازی."""
    try:
        if not _is_authorized(event.sender_id):
            return  # غیرمجاز — بی‌صدا نادیده
    except Exception:
        pass
    _clean_gc()
    await event.respond(
        "🧹 <b>پاکسازی فایل‌های ابری</b>\n\n"
        "فایل‌های کدوم هاست رو می‌خوای ببینی و پاک کنی؟",
        buttons=_clean_host_rows(),
        parse_mode="html",
    )


async def fe_clean_cb(event):
    """دکمه‌های /clean — fclnh/fclnd/fclnda/fclnda2/fclnr/fclnm/fclnx."""
    sid = None
    try:
        if not _is_authorized(event.sender_id):
            await event.answer("⛔️ اجازه نداری", alert=True)
            return
        data = event.data.decode("utf-8", "ignore")
        parts = data.split("_")
        action = parts[0]
        _clean_gc()

        # ── انتخاب هاست ──
        if action == "fclnh" and len(parts) == 2:
            host = parts[1]
            if host not in _CLEAN_HOSTS:
                await event.answer("هاست نامعتبره", alert=True)
                return
            await event.answer("🔄 در حال گرفتن لیست...")
            await _safe_edit(event, "⏳ در حال گرفتن لیست فایل‌ها...", _idle_rows("⏳ صبر کن..."))
            files = await _clean_fetch(host)
            sid = secrets.token_hex(5)
            _CLEAN_SESSIONS[sid] = {"host": host, "files": files, "sid": sid, "ts": time.time()}
            text, rows = _clean_render(_CLEAN_SESSIONS[sid])
            await _safe_edit(event, text, rows)
            return

        if len(parts) < 2:
            await event.answer("داده نامعتبره", alert=True)
            return
        sid = parts[1]
        sess = _CLEAN_SESSIONS.get(sid)

        # ── بستن ──
        if action == "fclnx":
            if sid in _CLEAN_SESSIONS:
                _CLEAN_SESSIONS.pop(sid, None)
            await event.answer("بسته شد")
            await _safe_edit(event, "🧹 پاکسازی بسته شد.", _idle_rows("✖️ بسته شد — دوباره /clean بزن"))
            return

        if sess is None:
            await event.answer("⌛️ این منو منقضی شده — دوباره /clean بزن", alert=True)
            await _safe_edit(event, "⌛️ این منو منقضی شد — دوباره <code>/clean</code> بزن.", None)
            return
        sess["ts"] = time.time()
        host = sess["host"]
        meta = _CLEAN_HOSTS[host]

        # ── حذف تکی ──
        if action == "fclnd" and len(parts) == 3:
            idx = int(parts[2])
            if idx >= len(sess["files"]):
                await event.answer("این فایل دیگه تو لیست نیست — بروزرسانی کن", alert=True)
                return
            f = sess["files"][idx]
            await event.answer("🗑 در حال حذف...")
            ok = await _clean_delete_one(host, f)
            if ok:
                sess["files"].pop(idx)
                text, rows = _clean_render(sess)
                await _safe_edit(event, f"✅ حذف شد: <code>{_esc(_short(f['name'], 30))}</code>\n\n" + text, rows)
            else:
                await event.answer("❌ حذف ناموفق — دوباره امتحان کن", alert=True)
            return

        # ── حذف همه — مرحله تأیید ──
        if action == "fclnda":
            n = len(sess["files"])
            if n == 0:
                await event.answer("لیست خالیه", alert=True)
                return
            await event.answer()
            await _safe_edit(
                event,
                f"⚠️ <b>مطمئنی؟</b>\n\nهمه‌ی <b>{n}</b> فایل از {meta['emoji']} <b>{meta['title']}</b> "
                "برای همیشه پاک بشن؟ این عمل برگشت‌پذیر نیست!",
                [
                    [Button.inline(f"☢️ آره، همه‌ی {n} تا رو پاک کن", f"fclnda2_{sid}")],
                    [Button.inline("⬅️ برگشت", f"fclnr_{sid}")],
                ],
            )
            return

        # ── حذف همه — اجرا ──
        if action == "fclnda2":
            files = list(sess["files"])
            n = len(files)
            if n == 0:
                await event.answer("لیست خالیه", alert=True)
                return
            await _safe_edit(event, f"🧹 در حال حذف {n} فایل از {meta['title']}...", _idle_rows("⏳ در حال حذف..."))
            deleted = failed = 0
            if host == "go":
                token = _gofile_token()
                for i in range(0, n, 50):
                    chunk = [f["id"] for f in files[i:i + 50]]
                    if await _gofile_delete_contents(token, chunk):
                        deleted += len(chunk)
                    else:
                        failed += len(chunk)
                    await asyncio.sleep(0.4)
            else:
                for f in files:
                    if await _clean_delete_one(host, f):
                        deleted += 1
                    else:
                        failed += 1
                    if (deleted + failed) % 10 == 0:
                        await _safe_edit(event, f"🧹 {deleted + failed}/{n} فایل پردازش شد...", None)
                    await asyncio.sleep(0.25)
            sess["files"] = []
            text, rows = _clean_render(sess)
            result = f"🧹 <b>تمیزکاری تموم شد!</b>\n✅ حذف شد: {deleted}   ❌ ناموفق: {failed}\n\n"
            await _safe_edit(event, result + text, rows)
            return

        # ── بروزرسانی لیست ──
        if action == "fclnr":
            await event.answer("🔄 در حال بروزرسانی...")
            await _safe_edit(event, "⏳ در حال گرفتن لیست تازه...", _idle_rows("⏳ صبر کن..."))
            sess["files"] = await _clean_fetch(host)
            text, rows = _clean_render(sess)
            await _safe_edit(event, text, rows)
            return

        # ── برگشت به منوی هاست‌ها ──
        if action == "fclnm":
            if sid in _CLEAN_SESSIONS:
                _CLEAN_SESSIONS.pop(sid, None)
            await event.answer()
            await _safe_edit(
                event,
                "🧹 <b>پاکسازی فایل‌های ابری</b>\n\nفایل‌های کدوم هاست رو می‌خوای ببینی و پاک کنی؟",
                _clean_host_rows(),
            )
            return

        await event.answer("دستور ناشناخته", alert=True)
    except _FeAuthError as e:
        try:
            await event.answer("🔑 کلید API مشکل داره", alert=True)
            await _safe_edit(event, f"❌ {e}", _clean_host_rows())
        except Exception:
            pass
    except _FeError as e:
        try:
            await event.answer("❌ خطا", alert=True)
            await _safe_edit(event, f"❌ {e}", _clean_host_rows())
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[FileExplorer] clean cb error: {e}", exc_info=True)
        try:
            await event.answer("❌ خطای غیرمنتظره", alert=True)
        except Exception:
            pass


async def _cloud_gc_loop():
    """تاسک پس‌زمینه: حذف خودکار پیکسل‌درین/گوفایل + گاربیج‌کالکتور /clean.

    اولین اجرا ۲ دقیقه بعد از بوت؛ بعدش هر ۱۰ دقیقه."""
    await asyncio.sleep(120)
    while True:
        try:
            d, f = await _pd_autodel_sweep()
            if d or f:
                logger.info("[AutoDel] pixeldrain sweep: deleted=%d failed=%d", d, f)
        except Exception as e:
            logger.warning(f"[AutoDel] pixeldrain sweep error: {e}")
        try:
            d, f = await _gofile_autodel_sweep()
            if d or f:
                logger.info("[AutoDel] gofile sweep: deleted=%d failed=%d", d, f)
        except Exception as e:
            logger.warning(f"[AutoDel] gofile sweep error: {e}")
        try:
            _clean_gc()
        except Exception:
            pass
        await asyncio.sleep(600)


async def _safe_edit(event, text: str, rows=None) -> bool:
    """ادیت امن پیام — اگه ادیت ممکن نبود (پیام پاک شده، مارک‌آپ رد شده و ...)
    به‌جاش همون متن رو به‌صورت پیام جدید می‌فرسته که کاربر همیشه UI ببینه."""
    try:
        await event.edit(text, buttons=rows, parse_mode="html")
        return True
    except MessageNotModifiedError:
        return True
    except Exception as e:
        logger.warning(
            f"[FileExplorer] edit failed ({e.__class__.__name__}) → send new message instead"
        )
        try:
            await event.respond(text, buttons=rows, parse_mode="html")
            return True
        except Exception as e2:
            logger.error(f"[FileExplorer] fallback send failed: {e2}", exc_info=True)
            return False

# ───────────────────────── وضعیت سراسری ─────────────────────────
# sid → اطلاعات سشن مرور آرشیو
fex_sessions: Dict[str, dict] = {}
# user_id → وضعیت انتظار برای اسم جدید (تغییر نام)
frename_pending: Dict[int, dict] = {}
# گارد دابل‌کلیک — (chat_id, msg_id) در حال پردازش + (sid, pid) در حال ارسال
_open_inflight = set()
_send_inflight = set()

_client = None
_skip_check: Optional[Callable] = None
_is_authorized: Callable[[int], bool] = lambda uid: True
_output_folder = "output_files"


# ═══════════════════════ کمکی‌ها ═══════════════════════
def _esc(s: str) -> str:
    return html.escape(str(s), quote=False)


def _fmt_size(n) -> str:
    try:
        n = int(n)
    except Exception:
        return "?"
    if n <= 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n:.1f} TB"


def _short(name: str, n: int = 40) -> str:
    name = name or ""
    return name if len(name) <= n else name[: n - 1] + "…"


def _doc_filename(doc) -> str:
    for attr in getattr(doc, "attributes", []) or []:
        fn = getattr(attr, "file_name", None)
        if fn:
            return fn
    return ""


def _file_emoji(name: str) -> str:
    ext = os.path.splitext(name or "")[1].lower()
    if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
        return "🖼"
    if ext in VIDEO_EXTS:
        return "🎬"
    if ext in (".mp3", ".m4a", ".ogg", ".wav", ".flac", ".aac", ".opus", ".wma"):
        return "🎵"
    if ext in (".apk", ".ipa", ".xapk", ".apks", ".apkm", ".exe", ".msi", ".aab"):
        return "📦"
    if ext in (".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"):
        return "🗜"
    if ext in (".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt"):
        return "📝"
    if ext in (".ttf", ".otf", ".woff", ".woff2"):
        return "🔤"
    return "📄"


def _sanitize_filename(name: str) -> str:
    """اسم ورودی کاربر رو برای استفاده به‌عنوان نام فایل تمیز می‌کنه."""
    name = (name or "").strip().strip("\"'`«»")
    name = name.replace("\\", "_").replace("/", "_")
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    if name.startswith("."):
        name = "_" + name[1:]
    return name[:MAX_NAME_LEN]


def _safe_disk_name(name: str) -> str:
    """همون sanitize ولی برای ذخیره روی دیسک (کاراکترهای ممنوع ویندوز/لینوکس)."""
    name = _sanitize_filename(name)
    name = re.sub(r'[<>:"|?*]', "_", name)
    name = name.strip().rstrip(".")
    return name or "file"


def _norm_arc_path(name: str) -> str:
    """نرمال‌سازی مسیر فایل داخل آرشیو — حذف ./ و // و .. برای امنیت."""
    name = (name or "").replace("\\", "/")
    name = re.sub(r"/{2,}", "/", name)
    parts = [p for p in name.split("/") if p not in ("", ".", "..")]
    return "/".join(parts)


def _archive_kind(filename: str) -> Optional[str]:
    n = (filename or "").lower()
    for suffix, kind in (
        (".tar.gz", "tar"), (".tar.bz2", "tar"), (".tar.xz", "tar"),
        (".tar.lz", "tar"), (".taz", "tar"),
    ):
        if n.endswith(suffix):
            return kind
    ext = os.path.splitext(n)[1]
    if ext in ZIP_FAMILY:
        return "zip"
    if ext in TAR_FAMILY:
        return "tar"
    if ext in SEVENZ_FAMILY:
        return "7z"
    if ext in RAR_FAMILY:
        return "rar"
    return None


def _fix_zip_name(info) -> str:
    """اصلاح اسم فایل‌های zip که با فارسی/یونیکد ساخته شدن ولی فلگ UTF-8 ندارن."""
    raw = getattr(info, "orig_filename", None) or info.filename or ""
    if getattr(info, "flag_bits", 0) & 0x800:
        return raw
    try:
        return raw.encode("cp437").decode("utf-8")
    except Exception:
        return raw


class _FeError(Exception):
    """خطای دوستانه — متنش مستقیم به کاربر نشون داده میشه."""


class _FeAuthError(_FeError):
    """🆕 خطای احراز هویت (مثل کلید نامعتبر پیکسل‌درین) — ری‌تای بی‌فایده‌ست،
    زنجیره‌ی fallback مستقیم میره سراغ هاست بعدی."""


# ═══════════════════════ ساخت درخت آرشیو ═══════════════════════
def _new_node() -> dict:
    return {"dirs": {}, "files": []}


def _tree_insert(root: dict, norm: str, is_dir: bool, size: int, raw: str):
    parts = norm.split("/")
    node = root
    for p in parts[:-1]:
        nxt = node["dirs"].get(p)
        if nxt is None:
            nxt = _new_node()
            node["dirs"][p] = nxt
        node = nxt
    last = parts[-1]
    if is_dir:
        if last not in node["dirs"]:
            node["dirs"][last] = _new_node()
    else:
        node["files"].append({"name": last, "size": size, "raw": raw})


def _node_at(tree: dict, cwd: str) -> Optional[dict]:
    node = tree
    for p in (cwd.split("/") if cwd else []):
        nxt = node["dirs"].get(p)
        if nxt is None:
            return None
        node = nxt
    return node


def _build_archive_tree(path: str, kind: str):
    """لیست کامل محتویات آرشیو → درخت. خروجی: (tree, count, err)"""
    entries = []
    try:
        if kind == "zip":
            with zipfile.ZipFile(path) as zf:
                for info in zf.infolist():
                    raw = info.filename or ""
                    disp = _fix_zip_name(info)
                    is_dir = raw.endswith("/") or info.is_dir()
                    norm = _norm_arc_path(disp)
                    if not norm:
                        continue
                    entries.append((norm, is_dir, info.file_size, raw))
        elif kind == "tar":
            with tarfile.open(path, "r:*") as tf:
                for m in tf.getmembers():
                    norm = _norm_arc_path(m.name)
                    if not norm:
                        continue
                    entries.append((norm, m.isdir(), m.size, m.name))
        elif kind == "7z":
            if py7zr is None:
                return None, 0, "کتابخانه py7zr روی سرور نصب نیست (requirements.txt رو آپدیت کن)"
            with py7zr.SevenZipFile(path, mode="r") as z:
                for fi in z.list():
                    norm = _norm_arc_path(fi.filename)
                    if not norm:
                        continue
                    is_dir = bool(getattr(fi, "is_directory", False))
                    try:
                        size = int(fi.uncompressed)
                    except Exception:
                        size = 0
                    entries.append((norm, is_dir, size, fi.filename))
        elif kind == "rar":
            if rarfile is None:
                return None, 0, "کتابخانه rarfile روی سرور نصب نیست (requirements.txt رو آپدیت کن)"
            with rarfile.RarFile(path) as rf:
                for info in rf.infolist():
                    norm = _norm_arc_path(info.filename)
                    if not norm:
                        continue
                    entries.append((norm, info.isdir(), info.file_size, info.filename))
        else:
            return None, 0, "فرمت پشتیبانی نمیشه"
    except _FeError:
        raise
    except Exception as e:
        msg = str(e) or e.__class__.__name__
        if "encrypted" in msg.lower() or "password" in msg.lower():
            return None, 0, "آرشیو رمز (پسورد) داره — فعلاً پشتیبانی نمیشه"
        return None, 0, msg[:150]

    root = _new_node()
    seen_dirs = set()
    for norm, is_dir, size, raw in entries:
        _tree_insert(root, norm, is_dir, size, raw)
        if is_dir:
            seen_dirs.add(norm)
    return root, len(entries), None


# ═══════════════════════ رندر لیست (دکمه‌های شیشه‌ای) ═══════════════════════
def _render_listing(sess: dict):
    node = _node_at(sess["tree"], sess["cwd"])
    if node is None:
        sess["cwd"] = ""
        node = sess["tree"]

    dirs = sorted(node["dirs"].keys(), key=lambda s: s.lower())
    files = sorted(node["files"], key=lambda f: f["name"].lower())
    items = [("d", d) for d in dirs] + [("f", f) for f in files]

    total = len(items)
    pages = max(1, math.ceil(total / PAGE_SIZE))
    sess["page"] = max(0, min(sess["page"], pages - 1))
    start = sess["page"] * PAGE_SIZE
    chunk = items[start : start + PAGE_SIZE]

    sid = sess["sid"]
    pid_map = sess["pid_map"]
    rows = []
    for kind_, item in chunk:
        pid = sess["next_pid"]
        sess["next_pid"] += 1
        if kind_ == "d":
            pid_map[pid] = ("d", item)
            rows.append([Button.inline(f"📁 {_short(item)}", f"fexn_{sid}_{pid}")])
        else:
            pid_map[pid] = ("f", item["raw"], item["name"], item["size"])
            label = f"{_file_emoji(item['name'])} {_short(item['name'])} ({_fmt_size(item['size'])})"
            rows.append([Button.inline(label, f"fexf_{sid}_{pid}")])

    if not rows:
        rows.append([Button.inline("🫙 این پوشه خالیه", "fexnoop")])

    if pages > 1:
        prev_p = (sess["page"] - 1) % pages
        next_p = (sess["page"] + 1) % pages
        rows.append([
            Button.inline("◀️ قبلی", f"fexp_{sid}_{prev_p}"),
            Button.inline(f"📄 {sess['page'] + 1}/{pages}", "fexnoop"),
            Button.inline("بعدی ▶️", f"fexp_{sid}_{next_p}"),
        ])

    rows.append([Button.inline("🔙 پوشه بالاتر", f"fexup_{sid}")])
    rows.append([Button.inline("❌ بستن", f"fexclose_{sid}")])

    cwd_disp = _esc(sess["cwd"]) if sess["cwd"] else "ریشه /"
    text = (
        f"📂 <b>{_esc(sess['file_name'])}</b>\n"
        f"📁 مسیر: <code>{cwd_disp}</code>\n"
        f"🧭 {len(dirs)} پوشه • {len(files)} فایل در این مسیر"
        + (f" • کل آرشیو: {sess['count']} آیتم" if sess.get("count") else "")
        + "\n\n👇 روی هر فایل بزنی، همون فایل جداگانه برات ارسال میشه:"
    )
    return text, rows


# ═══════════════════════ نمایش درصد پیشرفت ═══════════════════════
class _ProgEdit:
    """progress_callback همگام تلگرام → ادیت دوره‌ای پیام وضعیت.
    اگه msg صفر (None) باشه (مثلاً پیام پاک شده باشه) فقط بی‌صدا رد میشه.

    🆕 اگه abort_token داده بشه، با زدن دکمه «لغو» (ثبت توکن تو
    _ABORT_FLAGS) از همون فراخوانی بعدیِ cb عملیات با _UploadAborted قطع
    میشه — بدون تأخیرِ محدودیت ۳.۵ ثانیه‌ای ادیت."""

    def __init__(self, msg, label: str, abort_token: Optional[str] = None):
        self.msg = msg
        self.label = label
        self.last_t = 0.0
        self.abort_token = abort_token

    def cb(self, current: int, total: int):
        # 🆕 چک لغو — قبل از هر چیز و بدون تrottle تا فوری قطع بشه
        if self.abort_token and self.abort_token in _ABORT_FLAGS:
            raise _UploadAborted()
        if self.msg is None:
            return
        now = time.time()
        if now - self.last_t < 3.5:
            return
        self.last_t = now
        try:
            pct = (current * 100.0) / max(total, 1)
            text = (
                f"⏳ {self.label}: {pct:.1f}%\n"
                f"({_fmt_size(current)} / {_fmt_size(total)})"
            )
            asyncio.get_running_loop().create_task(self._edit(text))
        except Exception:
            pass

    async def _edit(self, text: str):
        try:
            if self.msg is None:
                return
            await self.msg.edit(text, parse_mode="html")
        except Exception:
            pass


# ═══════════════════════ مدیریت سشن ═══════════════════════
def _cleanup_session(sid: str):
    sess = fex_sessions.pop(sid, None)
    if sess:
        shutil.rmtree(sess.get("sess_dir", ""), ignore_errors=True)


def _evict_old_sessions():
    while len(fex_sessions) >= MAX_SESSIONS:
        oldest_sid = min(fex_sessions, key=lambda s: fex_sessions[s]["last_access"])
        _cleanup_session(oldest_sid)


def _touch(sess: dict):
    sess["last_access"] = time.time()


async def _session_gc_loop():
    """هر ۳ دقیقه چک می‌کنه — سشن‌های منقضی و فایل‌هاشون رو پاک می‌کنه."""
    while True:
        try:
            await asyncio.sleep(180)
            now = time.time()
            for sid in list(fex_sessions.keys()):
                sess = fex_sessions.get(sid)
                if sess and now - sess.get("last_access", sess["created"]) > SESSION_TTL:
                    _cleanup_session(sid)
            for uid in list(frename_pending.keys()):
                st = frename_pending.get(uid)
                if st and now - st.get("created", now) > RENAME_TTL:
                    frename_pending.pop(uid, None)
        except Exception as e:
            logger.error(f"[FileExplorer] GC error: {e}", exc_info=True)


# ═══════════════════════ ۱) واکنش به داکیومنت ═══════════════════════
async def document_receive_handler(event):
    """هر داکیومنتی که هندلر دیگه‌ای نمی‌گیره → منوی شیشه‌ای چهاردکمه‌ای."""
    try:
        doc = event.document
        if doc is None:
            return
        if not _is_authorized(event.sender_id):
            return
        if _skip_check and _skip_check(event):
            return

        mime = (getattr(doc, "mime_type", "") or "").lower()
        fname = _doc_filename(doc) or f"file_{event.id}"
        ext = os.path.splitext(fname)[1].lower()

        # ویدیو → video_receive_handler خودش دکمه میده
        if mime.startswith("video/"):
            return
        for attr in getattr(doc, "attributes", []) or []:
            if isinstance(attr, DocumentAttributeVideo):
                return
        if ext in VIDEO_EXTS:
            return

        # عکس / استیکر / گیف → هندلر OCR و بقیه جریان‌ها
        if mime.startswith("image/"):
            return
        for attr in getattr(doc, "attributes", []) or []:
            if isinstance(attr, (DocumentAttributeSticker, DocumentAttributeAnimated)):
                return

        # PDF → هندلر ترجمه کامیک خودش دکمه داره
        if mime == "application/pdf" or ext == ".pdf":
            return

        size = getattr(doc, "size", 0) or 0
        buttons = _menu_rows(event.chat_id, event.id)
        await event.reply(
            f"📎 <b>{_esc(fname)}</b>"
            + (f" ({_fmt_size(size)})" if size else "")
            + "\nیکی از عملیات‌ها رو انتخاب کن:",
            buttons=buttons,
            parse_mode="html",
        )
    except Exception as e:
        logger.error(f"[FileExplorer] document receive error: {e}", exc_info=True)


# ═══════════════════════ ۲) باز کردن مرورگر آرشیو ═══════════════════════
async def fe_open_cb(event):
    try:
        if not _is_authorized(event.sender_id):
            await event.answer("⛔️ اجازه نداری", alert=True)
            return
        data = event.data.decode("utf-8", "ignore")
        _, chat_s, msg_s = data.split("_", 2)
        chat_id, msg_id = int(chat_s), int(msg_s)

        # گارد دابل‌کلیک
        inflight_key = (chat_id, msg_id)
        if inflight_key in _open_inflight:
            await event.answer("⏳ هنوز در حال پردازش همونه...", alert=True)
            return

        msg = await event.client.get_messages(chat_id, ids=msg_id)
        doc = msg.document if msg else None
        if doc is None:
            await event.answer("⚠️ فایل اصلی پیدا نشد — احتمالاً پاک شده", alert=True)
            return

        fname = _doc_filename(doc) or f"file_{msg_id}"
        kind = _archive_kind(fname)
        if not kind:
            await event.answer(
                "❌ این فایل آرشیو نیست!\n"
                "پشتیبانی میشه: zip apk jar ipa xapk rar 7z tar gz docx xlsx pptx epub ...",
                alert=True,
            )
            return
        if kind == "rar" and rarfile is None:
            await event.answer("⚠️ پشتیبانی RAR روی سرور فعال نیست", alert=True)
            return
        if kind == "7z" and py7zr is None:
            await event.answer("⚠️ پشتیبانی 7z روی سرور فعال نیست", alert=True)
            return

        size = getattr(doc, "size", 0) or 0
        if size > MAX_ARCHIVE_BYTES:
            await event.answer("⚠️ فایل بزرگ‌تر از ۲ گیگابایته — نمیتونم بازش کنم", alert=True)
            return

        _open_inflight.add(inflight_key)
        retry_buttons = _menu_rows(chat_id, msg_id)
        try:
            # دکمه‌های قبلی با یه دکمه «لطفاً صبر کن» جایگزین بشن که وسط کار
            # دوباره زده نشن (مارک‌آپ خالی روی تلگرام ReplyMarkupInvalid میده)
            await _safe_edit(
                event,
                "⏳ در حال دانلود فایل برای بررسی...",
                _idle_rows("⏳ لطفاً صبر کن..."),
            )

            sess_dir = os.path.join(_output_folder, f"fex_{secrets.token_hex(4)}_{int(time.time())}")
            os.makedirs(sess_dir, exist_ok=True)
            local_name = _safe_disk_name(fname) or f"archive{os.path.splitext(fname)[1] or '.bin'}"
            local_path = os.path.join(sess_dir, local_name)

            # ⚠️ FIX: روی CallbackQuery.Event ویژگی message وجود نداره —
            # باید از get_message() استفاده بشه (اگه پیام پاک شده باشه None برمی‌گرده)
            try:
                btn_msg = await event.get_message()
            except Exception:
                btn_msg = None
            prog = _ProgEdit(btn_msg, "دانلود")
            try:
                got = await event.client.download_media(msg, file=local_path, progress_callback=prog.cb)
            except Exception as e:
                logger.error(f"[FileExplorer] archive download failed: {e}", exc_info=True)
                shutil.rmtree(sess_dir, ignore_errors=True)
                await _safe_edit(
                    event,
                    "❌ دانلود فایل ناموفق بود — دوباره امتحان کن",
                    retry_buttons,
                )
                return

            if not got or not os.path.exists(got) or os.path.getsize(got) == 0:
                shutil.rmtree(sess_dir, ignore_errors=True)
                await _safe_edit(
                    event,
                    "❌ فایل دانلود نشد — دوباره امتحان کن",
                    retry_buttons,
                )
                return

            await _safe_edit(event, "📂 در حال باز کردن آرشیو...")

            tree, count, err = await asyncio.to_thread(_build_archive_tree, got, kind)
            if err or tree is None:
                shutil.rmtree(sess_dir, ignore_errors=True)
                await _safe_edit(
                    event,
                    f"❌ باز کردن آرشیو ناموفق بود:\n<code>{_esc(err or 'unknown')}</code>",
                    retry_buttons,
                )
                return
        finally:
            _open_inflight.discard(inflight_key)

        _evict_old_sessions()
        sid = secrets.token_hex(4)
        while sid in fex_sessions:
            sid = secrets.token_hex(4)

        fex_sessions[sid] = {
            "sid": sid,
            "chat_id": chat_id,
            "owner": event.sender_id,
            "file_msg_id": msg_id,
            "file_name": fname,
            "file_path": got,
            "sess_dir": sess_dir,
            "kind": kind,
            "tree": tree,
            "count": count,
            "cwd": "",
            "page": 0,
            "pid_map": {},
            "next_pid": 0,
            "created": time.time(),
            "last_access": time.time(),
        }
        text, rows = _render_listing(fex_sessions[sid])
        await _safe_edit(event, text, rows)
    except MessageNotModifiedError:
        pass
    except Exception as e:
        logger.error(f"[FileExplorer] open error: {e}", exc_info=True)
        try:
            await event.answer("❌ خطا در باز کردن فایل", alert=True)
        except Exception:
            pass


# ═══════════════════════ ۳) ناوبری پوشه‌ها ═══════════════════════
async def fe_nav_cb(event):
    """رفتن داخل یه پوشه: fexn_<sid>_<pid>"""
    try:
        if not _is_authorized(event.sender_id):
            await event.answer("⛔️ اجازه نداری", alert=True)
            return
        _, sid, pid_s = event.data.decode("utf-8", "ignore").split("_", 2)
        sess = fex_sessions.get(sid)
        if not sess:
            await event.answer("⌛️ این سشن منقضی شده — دوباره 🔍 جستجو در فایل رو بزن", alert=True)
            return
        entry = sess["pid_map"].get(int(pid_s))
        if not entry or entry[0] != "d":
            await event.answer("⚠️ این پوشه دیگه در دسترس نیست", alert=True)
            return
        _touch(sess)
        name = entry[1]
        sess["cwd"] = f"{sess['cwd']}/{name}" if sess["cwd"] else name
        sess["page"] = 0
        text, rows = _render_listing(sess)
        await _safe_edit(event, text, rows)
        await event.answer()
    except MessageNotModifiedError:
        pass
    except Exception as e:
        logger.error(f"[FileExplorer] nav error: {e}", exc_info=True)
        try:
            await event.answer("❌ خطا", alert=True)
        except Exception:
            pass


async def fe_up_cb(event):
    """یك پوشه برخاستن بالا: fexup_<sid>"""
    try:
        if not _is_authorized(event.sender_id):
            await event.answer("⛔️ اجازه نداری", alert=True)
            return
        sid = event.data.decode("utf-8", "ignore").split("_", 1)[1]
        sess = fex_sessions.get(sid)
        if not sess:
            await event.answer("⌛️ این سشن منقضی شده", alert=True)
            return
        _touch(sess)
        if "/" in sess["cwd"]:
            sess["cwd"] = sess["cwd"].rsplit("/", 1)[0]
        else:
            sess["cwd"] = ""
        sess["page"] = 0
        text, rows = _render_listing(sess)
        await _safe_edit(event, text, rows)
        await event.answer()
    except MessageNotModifiedError:
        pass
    except Exception as e:
        logger.error(f"[FileExplorer] up error: {e}", exc_info=True)
        try:
            await event.answer("❌ خطا", alert=True)
        except Exception:
            pass


async def fe_page_cb(event):
    """صفحه‌بندی: fexp_<sid>_<page>"""
    try:
        if not _is_authorized(event.sender_id):
            await event.answer("⛔️ اجازه نداری", alert=True)
            return
        _, sid, page_s = event.data.decode("utf-8", "ignore").split("_", 2)
        sess = fex_sessions.get(sid)
        if not sess:
            await event.answer("⌛️ این سشن منقضی شده", alert=True)
            return
        _touch(sess)
        try:
            sess["page"] = max(0, int(page_s))
        except Exception:
            sess["page"] = 0
        text, rows = _render_listing(sess)
        await _safe_edit(event, text, rows)
        await event.answer()
    except MessageNotModifiedError:
        pass
    except Exception as e:
        logger.error(f"[FileExplorer] page error: {e}", exc_info=True)
        try:
            await event.answer("❌ خطا", alert=True)
        except Exception:
            pass


async def fe_noop_cb(event):
    try:
        await event.answer()
    except Exception:
        pass


async def fe_close_cb(event):
    """بستن مرورگر + پاک کردن فایل موقت: fexclose_<sid>"""
    try:
        sid = event.data.decode("utf-8", "ignore").split("_", 1)[1]
        if not _is_authorized(event.sender_id):
            await event.answer("⛔️ اجازه نداری", alert=True)
            return
        _cleanup_session(sid)
        await _safe_edit(
            event,
            "🔒 بسته شد — برای شروع دوباره، فایل رو بفرست",
            _idle_rows("🔒 بسته شد"),
        )
        await event.answer()
    except Exception as e:
        logger.error(f"[FileExplorer] close error: {e}", exc_info=True)
        try:
            await event.answer("❌ خطا", alert=True)
        except Exception:
            pass


# ═══════════════════════ ۴) استخراج و ارسال تک‌فایل ═══════════════════════
def _extract_member(sess: dict, raw: str, display_name: str, dest_dir: str) -> str:
    """استخراج یه فایل از آرشیو (بلاکینگ — داخل to_thread صدا زده میشه)."""
    kind = sess["kind"]
    path = sess["file_path"]
    os.makedirs(dest_dir, exist_ok=True)
    out_path = os.path.join(dest_dir, _safe_disk_name(display_name) or "file")

    if kind == "zip":
        with zipfile.ZipFile(path) as zf:
            with zf.open(raw) as src, open(out_path, "wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
        return out_path

    if kind == "tar":
        with tarfile.open(path, "r:*") as tf:
            member = tf.getmember(raw)
            src = tf.extractfile(member)
            if src is None:
                raise _FeError("این آیتم قابل استخراج نیست (لینک یا ورودی خاصه)")
            with src, open(out_path, "wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
        return out_path

    if kind == "7z":
        if py7zr is None:
            raise _FeError("کتابخانه py7zr نصب نیست")
        with py7zr.SevenZipFile(path, mode="r") as z:
            z.extract(path=dest_dir, targets=[raw])
        cand = os.path.join(dest_dir, raw)
        if os.path.isfile(cand):
            return cand
        for root_dir, _, file_list in os.walk(dest_dir):
            for f in file_list:
                if f == os.path.basename(raw):
                    return os.path.join(root_dir, f)
        raise _FeError("فایل بعد از استخراج پیدا نشد")

    if kind == "rar":
        if rarfile is None:
            raise _FeError("کتابخانه rarfile نصب نیست")
        with rarfile.RarFile(path) as rf:
            with rf.open(raw) as src, open(out_path, "wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
        return out_path

    raise _FeError("فرمت پشتیبانی نمیشه")


async def fe_file_cb(event):
    """ارسال یه فایل از داخل آرشیو: fexf_<sid>_<pid>"""
    status = None
    dest_dir = None
    try:
        if not _is_authorized(event.sender_id):
            await event.answer("⛔️ اجازه نداری", alert=True)
            return
        _, sid, pid_s = event.data.decode("utf-8", "ignore").split("_", 2)
        sess = fex_sessions.get(sid)
        if not sess:
            await event.answer("⌛️ این سشن منقضی شده — دوباره 🔍 جستجو در فایل رو بزن", alert=True)
            return
        entry = sess["pid_map"].get(int(pid_s))
        if not entry or entry[0] != "f":
            await event.answer("⚠️ این فایل دیگه در دسترس نیست", alert=True)
            return

        # گارد دابل‌کلیک روی همون فایل
        inflight_key = (sid, pid_s)
        if inflight_key in _send_inflight:
            await event.answer("⏳ همین الان داره ارسال میشه...", alert=True)
            return

        _touch(sess)
        _, raw, disp_name, fsize = entry
        if fsize and fsize > MAX_SEND_BYTES:
            await event.answer(
                f"⚠️ این فایل {_fmt_size(fsize)} هست — بزرگ‌تر از حد ارسال تلگرامه",
                alert=True,
            )
            return

        _send_inflight.add(inflight_key)
        try:
            await event.answer("📤 در حال استخراج...")
        except Exception:
            pass

        dest_dir = os.path.join(sess["sess_dir"], f"x{int(time.time() * 1000)}")
        status = await event.client.send_message(
            sess["chat_id"], f"⏳ در حال استخراج <b>{_esc(disp_name)}</b>...", parse_mode="html"
        )

        try:
            out_path = await asyncio.to_thread(_extract_member, sess, raw, disp_name, dest_dir)
        except _FeError as fe:
            await status.edit(f"❌ {_esc(str(fe))}", parse_mode="html")
            return
        except Exception as e:
            msg = str(e) or e.__class__.__name__
            if "encrypted" in msg.lower() or "password" in msg.lower():
                await status.edit("🔒 این فایل رمز داره — فعلاً پشتیبانی نمیشه", parse_mode="html")
            else:
                logger.error(f"[FileExplorer] extract error: {e}", exc_info=True)
                await status.edit("❌ استخراج فایل ناموفق بود", parse_mode="html")
            return

        real_size = os.path.getsize(out_path)
        if real_size > MAX_SEND_BYTES:
            await status.edit(
                f"⚠️ حجم واقعی فایل {_fmt_size(real_size)} هست — بزرگ‌تر از حد ارسال تلگرامه",
                parse_mode="html",
            )
            return

        try:
            await event.client.send_file(
                sess["chat_id"],
                out_path,
                force_document=True,
                caption=f"📄 <b>{_esc(disp_name)}</b> ({_fmt_size(real_size)})",
                parse_mode="html",
            )
            await status.delete()
        except Exception as e:
            logger.error(f"[FileExplorer] send member error: {e}", exc_info=True)
            try:
                await status.edit("❌ ارسال فایل ناموفق بود", parse_mode="html")
            except Exception:
                pass
        finally:
            _send_inflight.discard(inflight_key)
    except Exception as e:
        logger.error(f"[FileExplorer] file cb error: {e}", exc_info=True)
        try:
            await event.answer("❌ خطا", alert=True)
        except Exception:
            pass
    finally:
        if dest_dir:
            shutil.rmtree(dest_dir, ignore_errors=True)


# ═══════════════════════ ۵) تغییر نام فایل ═══════════════════════
async def fe_rename_cb(event):
    """دکمه ✏️ تغییر نام: fren_<chat_id>_<msg_id>"""
    try:
        if not _is_authorized(event.sender_id):
            await event.answer("⛔️ اجازه نداری", alert=True)
            return
        data = event.data.decode("utf-8", "ignore")
        _, chat_s, msg_s = data.split("_", 2)
        chat_id, msg_id = int(chat_s), int(msg_s)

        msg = await event.client.get_messages(chat_id, ids=msg_id)
        doc = msg.document if msg else None
        if doc is None:
            await event.answer("⚠️ فایل اصلی پیدا نشد — احتمالاً پاک شده", alert=True)
            return

        fname = _doc_filename(doc) or f"file_{msg_id}"
        size = getattr(doc, "size", 0) or 0
        if size > MAX_SEND_BYTES:
            await event.answer("⚠️ فایل بزرگ‌تر از حد ارسال تلگرامه", alert=True)
            return

        frename_pending[event.sender_id] = {
            "chat_id": chat_id,
            "msg_id": msg_id,
            "file_name": fname,
            "created": time.time(),
        }
        await _safe_edit(
            event,
            f"✏️ فایل: <b>{_esc(fname)}</b>\n\n"
            "چه اسمی میخوای براش بزاری؟ اسم کامل همراه پسوند رو بفرست.\n"
            "مثلاً: <code>mamad.apk</code>",
            [[Button.inline("❌ لغو", f"frenc_{event.sender_id}")]],
        )
        await event.answer()
    except Exception as e:
        logger.error(f"[FileExplorer] rename cb error: {e}", exc_info=True)
        try:
            await event.answer("❌ خطا", alert=True)
        except Exception:
            pass


async def fe_rename_cancel_cb(event):
    """لغو تغییر نام: frenc_<uid>"""
    try:
        uid = int(event.data.decode("utf-8", "ignore").split("_", 1)[1])
        if event.sender_id != uid:
            await event.answer("⛔️ این عملیات مال تو نیست", alert=True)
            return
        frename_pending.pop(uid, None)
        await _safe_edit(event, "❌ تغییر نام لغو شد", _idle_rows("❌ لغو شد"))
        await event.answer()
    except Exception as e:
        logger.error(f"[FileExplorer] rename cancel error: {e}", exc_info=True)
        try:
            await event.answer("❌ خطا", alert=True)
        except Exception:
            pass


async def fe_dismiss_cb(event):
    """🆕 دکمه ❌ بستن منوی اصلی: fexdism_<chat_id>_<msg_id>

    پیام منو رو کامل پاک می‌کنه (فایل اصلی دست‌نخورده می‌مونه)."""
    try:
        if not _is_authorized(event.sender_id):
            await event.answer("⛔️ اجازه نداری", alert=True)
            return
        try:
            await event.answer()
        except Exception:
            pass
        try:
            await event.delete()
        except Exception:
            # اگه پاک کردن نشد (مثلاً پیام قدیمی شده)، خالی‌ش کن
            await _safe_edit(event, "❌ بسته شد", _idle_rows("❌ بسته شد"))
    except Exception as e:
        logger.error(f"[FileExplorer] dismiss error: {e}", exc_info=True)


async def fe_abort_cb(event):
    """🆕 دکمه ❌ لغو وسط عملیات (آپلود Filebin/VLC، تغییر نام): fexab_<token>

    فقط توکن رو تو _ABORT_FLAGS ثبت می‌کنه؛ حلقه‌ی دانلود/آپلود تو اولین
    فراخوانی بعدیِ progress_callback با _UploadAborted می‌ایسته و پیام
    «لغو شد» + منو نمایش داده میشه."""
    try:
        if not _is_authorized(event.sender_id):
            await event.answer("⛔️ اجازه نداری", alert=True)
            return
        token = event.data.decode("utf-8", "ignore").split("_", 1)[1]
        if not token:
            await event.answer("❌ توکن نامعتبر", alert=True)
            return
        _ABORT_FLAGS.add(token)
        # 🆕 اگه آپلودی در جریانه، تسکش رو همون لحظه کنسل کن (aiohttp مسیر
        # استاندارد لغو رو تمیز می‌کنه)؛ دانلود تلگرام با پرچم تو cb قطع میشه
        t = _UPLOADER_TASKS.get(token)
        if t and not t.done():
            t.cancel()
        await event.answer("🚫 در حال لغو... چند لحظه صبر کن", alert=False)
    except Exception as e:
        logger.error(f"[FileExplorer] abort cb error: {e}", exc_info=True)
        try:
            await event.answer("❌ خطا", alert=True)
        except Exception:
            pass


async def fe_rename_text_handler(event):
    """دریافت اسم جدید از کاربر و ارسال فایل با اسم جدید."""
    st = frename_pending.get(event.sender_id)
    if not st:
        return
    if not _is_authorized(event.sender_id):
        return

    raw = (event.raw_text or "").strip()
    if not raw:
        # فایل بدون کپشن یا پیام خاص — دست نمی‌زنیم (هندلر داکیومنت خودش واکنش میده)
        return
    if raw.startswith("/"):
        if raw == "/cancel":
            frename_pending.pop(event.sender_id, None)
            await event.reply("❌ تغییر نام لغو شد")
            raise events.StopPropagation
        return

    frename_pending.pop(event.sender_id, None)

    if time.time() - st["created"] > RENAME_TTL:
        await event.reply("⌛️ مهلت تغییر نام تمام شد — دوباره فایل رو بفرست")
        raise events.StopPropagation

    new_name = _sanitize_filename(raw)
    if not new_name:
        await event.reply("❌ اسم نامعتبره — یه اسم دیگه بفرست (مثلاً: <code>mamad.apk</code>)", parse_mode="html")
        raise events.StopPropagation

    work_dir = os.path.join(_output_folder, f"fren_{event.sender_id}_{secrets.token_hex(4)}")
    status = None
    # 🆕 توکن لغو — دکمه «❌ لغو» روی پیام وضعیت می‌شینه و وسط دانلود/ارسال
    # عملیات رو قطع می‌کنه
    abort_token = secrets.token_hex(6)
    try:
        client = event.client
        status = await event.reply(
            f"⏳ در حال آماده‌سازی <b>{_esc(new_name)}</b>...",
            parse_mode="html",
            buttons=_abort_rows(abort_token),
        )
        msg = await client.get_messages(st["chat_id"], ids=st["msg_id"])
        if msg is None or msg.document is None:
            await status.edit("⚠️ فایل اصلی دیگه در دسترس نیست — دوباره فایل رو بفرست", parse_mode="html")
            raise events.StopPropagation

        old_name = _doc_filename(msg.document) or f"file_{st['msg_id']}"
        size = getattr(msg.document, "size", 0) or 0
        if size > MAX_SEND_BYTES:
            await status.edit("⚠️ فایل بزرگ‌تر از حد ارسال تلگرامه", parse_mode="html")
            raise events.StopPropagation

        os.makedirs(work_dir, exist_ok=True)
        tmp_path = os.path.join(work_dir, _safe_disk_name(old_name) or "file.bin")

        prog = _ProgEdit(status, "دانلود", abort_token=abort_token)
        got = await client.download_media(msg, file=tmp_path, progress_callback=prog.cb)
        if not got or not os.path.exists(got) or os.path.getsize(got) == 0:
            await status.edit("❌ دانلود فایل ناموفق بود — دوباره امتحان کن", parse_mode="html")
            raise events.StopPropagation

        final_path = os.path.join(work_dir, _safe_disk_name(new_name))
        if os.path.abspath(final_path) != os.path.abspath(got):
            os.replace(got, final_path)

        try:
            await status.edit("📤 در حال ارسال با اسم جدید...", parse_mode="html")
        except MessageNotModifiedError:
            pass

        # 🆕 progress_callback برای ارسال — هم درصد پیشرفت میده هم نقطه‌ی لغو
        prog_up = _ProgEdit(status, "ارسال", abort_token=abort_token)
        await client.send_file(
            st["chat_id"],
            final_path,
            force_document=True,
            caption=f"✏️ <b>{_esc(old_name)}</b> ← <b>{_esc(new_name)}</b>",
            parse_mode="html",
            progress_callback=prog_up.cb,
        )
        try:
            await status.edit(
                f"✅ فایل با اسم جدید ارسال شد: <code>{_esc(new_name)}</code>",
                parse_mode="html",
                buttons=None,
            )
        except Exception:
            pass
    except events.StopPropagation:
        raise
    except _UploadAborted:
        # 🆕 کاربر دکمه لغو رو زده
        logger.info("[FileExplorer] rename cancelled by user mid-operation")
        if status:
            try:
                await status.edit(
                    "❌ تغییر نام لغو شد.",
                    parse_mode="html",
                    buttons=None,
                )
            except Exception:
                pass
        raise events.StopPropagation
    except Exception as e:
        logger.error(f"[FileExplorer] rename error: {e}", exc_info=True)
        if status:
            try:
                await status.edit("❌ خطا در تغییر نام — دوباره امتحان کن", parse_mode="html")
            except Exception:
                pass
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        # 🆕 پاکسازی پرچم لغو
        try:
            _ABORT_FLAGS.discard(abort_token)
        except Exception:
            pass
    raise events.StopPropagation


# ═══════════════════════ ۶) آپلود به Filebin ═══════════════════════
async def fe_filebin_cb(event):
    """دکمه ☁️ آپلود به Filebin: fbin_<chat_id>_<msg_id>

    هوشمند:
    • پسوندهای بلاک‌شده سایت (exe/apk/msi/dll/scr) مستقیم با .zip آپلود میشن
    • اگه سایت پسوند دیگه‌ای رو رد کرد، یه بار دیگه با پسوند .zip تلاش میشه
    """
    chat_id = msg_id = None
    work_dir = None
    abort_token = None
    try:
        if not _is_authorized(event.sender_id):
            await event.answer("⛔️ اجازه نداری", alert=True)
            return
        if aiohttp is None:
            await event.answer("⚠️ کتابخانه aiohttp روی سرور نصب نیست", alert=True)
            return
        _, chat_s, msg_s = event.data.decode("utf-8", "ignore").split("_", 2)
        chat_id, msg_id = int(chat_s), int(msg_s)

        # گارد دابل‌کلیک
        inflight_key = ("fbin", chat_id, msg_id)
        if inflight_key in _send_inflight:
            await event.answer("⏳ همین الان داره آپلود میشه...", alert=True)
            return

        msg = await event.client.get_messages(chat_id, ids=msg_id)
        doc = msg.document if msg else None
        if doc is None:
            await event.answer("⚠️ فایل اصلی پیدا نشد — احتمالاً پاک شده", alert=True)
            return

        fname = _doc_filename(doc) or f"file_{msg_id}"
        size = getattr(doc, "size", 0) or 0
        if size > FILEBIN_MAX_BYTES:
            await event.answer(
                f"⚠️ فایل {_fmt_size(size)} هست — از حد مجاز Filebin بزرگ‌تره",
                alert=True,
            )
            return

        _send_inflight.add(inflight_key)
        try:
            await event.answer("☁️ شروع آپلود به Filebin...")
        except Exception:
            pass

        # 🆕 توکن یکتا برای دکمه لغو این عملیات
        abort_token = secrets.token_hex(6)

        await _safe_edit(
            event,
            f"☁️ در حال دانلود <b>{_esc(fname)}</b> برای آپلود به Filebin...",
            _abort_rows(abort_token),
        )

        work_dir = os.path.join(_output_folder, f"fbin_{event.sender_id}_{secrets.token_hex(4)}")
        os.makedirs(work_dir, exist_ok=True)
        local_path = os.path.join(work_dir, _safe_disk_name(fname) or "file.bin")

        try:
            btn_msg = await event.get_message()
        except Exception:
            btn_msg = None
        prog = _ProgEdit(btn_msg, "دانلود", abort_token=abort_token)
        got = await event.client.download_media(msg, file=local_path, progress_callback=prog.cb)
        if not got or not os.path.exists(got) or os.path.getsize(got) == 0:
            await _safe_edit(
                event,
                "❌ دانلود فایل ناموفق بود — دوباره امتحان کن",
                _menu_rows(chat_id, msg_id),
            )
            return

        remote_name = _filebin_remote_name(fname)
        ext = os.path.splitext(remote_name)[1].lower()
        bin_name = secrets.token_hex(4)  # اسم باکس تصادفی
        # ⚠️ آپلود بدون توکن‌ریز در cb — لغوِ آپلود aiohttp با کنسلِ تسک انجام
        # میشه (raise از داخل payload باعث هنگ می‌شه)
        prog_up = _ProgEdit(btn_msg, "آپلود")

        # ترتیب تلاش‌ها — منطق هوشمند پسوند:
        if ext in FILEBIN_BLOCKED_EXTS:
            # بلاک معلومه → مستقیم با .zip (مثلاً mamad.apk → mamad.apk.zip)
            attempts = [remote_name + ".zip"]
        elif remote_name.lower().endswith(".zip"):
            attempts = [remote_name]
        else:
            attempts = [remote_name, remote_name + ".zip"]

        last_status, last_body = 0, ""
        uploaded = None
        for i, name in enumerate(attempts):
            if i > 0:
                await _safe_edit(
                    event,
                    "⚠️ سایت این پسوند رو قبول نکرد — با پسوند <code>.zip</code> دوباره تلاش می‌کنم...",
                    _abort_rows(abort_token),
                )
            try:
                status_code, body = await _run_upload_with_abort(
                    abort_token, _filebin_upload(bin_name, name, got, prog_up)
                )
            except _UploadAborted:
                raise  # 🆕 کاربر لغو کرده — مستقیم برو به هندلر لغو
            except Exception as e:
                # 🆕 ممکنه خطا در واقع لغو باشه (چانک وسط آپلود قطع شده)
                if abort_token in _ABORT_FLAGS:
                    raise _UploadAborted() from None
                # خطای شبکه/تایم‌اوت — با .zip هم حل نمیشه
                logger.error(f"[FileExplorer] filebin upload network error: {e}", exc_info=True)
                last_status, last_body = -1, f"{e.__class__.__name__}: {e}"
                break
            last_status, last_body = status_code, body
            logger.info(f"[FileExplorer] filebin upload {name!r} → HTTP {status_code}")
            if status_code in (200, 201, 202):
                uploaded = name
                break
            if status_code in (429, 413):
                # محدودیت/حجم — تلاش با .zip بی‌فایده‌ست
                break
            # بقیه‌ی خطاهای HTTP (مثل 403 پسوند بلاک) → تلاش بعدی با .zip

        if uploaded:
            fsize = os.path.getsize(got)
            bin_url = f"{FILEBIN_BASE}/{bin_name}"
            file_url = f"{FILEBIN_BASE}/{bin_name}/{quote(uploaded)}"
            # ⚡️ لینک دانلود مستقیم واقعی: filebin بدون کوکی verified صفحه HTML
            # برمی‌گردونه — ربات خودش قدم تأیید رو انجام میده و URL امضاشده‌ی
            # S3 (بایت خام + Range، حدوداً ۱۵ دقیقه اعتبار) رو استخراج می‌کنه
            s3_url = await _filebin_direct_url(bin_name, uploaded)
            parts = [
                "✅ <b>آپلود به Filebin انجام شد!</b>\n\n",
                f"📄 فایل: <code>{_esc(uploaded)}</code>\n",
                f"📏 حجم: {_fmt_size(fsize)}\n\n",
            ]
            rows = []
            if s3_url:
                parts.append("⚡️ لینک دانلود مستقیم (سریع — حدود ۱۵ دقیقه اعتبار داره):\n")
                parts.append(f"{_esc(s3_url)}\n\n")
                rows.append([Button.url("⬇️ دانلود مستقیم", s3_url)])
            parts.append("📄 لینک صفحه فایل (دانلود در مرورگر):\n")
            parts.append(f"{file_url}\n\n")
            parts.append("🗃 لینک صفحه باکس:\n")
            parts.append(f"{bin_url}\n\n")
            parts.append(
                "ℹ️ فایل‌های Filebin حدود ۶ روز بعد پاک میشن. "
                "برای پخش ویدیو تو VLC از دکمه‌ی «آپلود برای پخش در VLC» استفاده کن."
            )
            rows.append([Button.url("📄 صفحه فایل", file_url), Button.url("🗃 صفحه باکس", bin_url)])
            await _safe_edit(event, "".join(parts), rows)
        else:
            if last_status == 429:
                detail = "سایت Filebin فعلاً محدودیت زده (429) — چند دقیقه بعد دوباره امتحان کن"
            elif last_status == 413:
                detail = "حجم فایل برای Filebin زیاده (413)"
            elif last_status == -1:
                detail = f"خطای اتصال به Filebin:\n<code>{_esc(last_body[:150])}</code>"
            else:
                detail = (
                    f"آپلود انجام نشد (کد {last_status})\n"
                    f"<code>{_esc(last_body[:150])}</code>"
                )
            await _safe_edit(
                event,
                f"❌ آپلود به Filebin ناموفق بود.\n{detail}",
                _menu_rows(chat_id, msg_id),
            )
    except _UploadAborted:
        # 🆕 کاربر دکمه لغو رو زده — پیام لغو + برگشت منو
        logger.info("[FileExplorer] filebin upload cancelled by user")
        try:
            rows = _menu_rows(chat_id, msg_id) if chat_id is not None and msg_id is not None else None
            await _safe_edit(event, "❌ آپلود به Filebin لغو شد.", rows)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[FileExplorer] filebin cb error: {e}", exc_info=True)
        try:
            await event.answer("❌ خطا در آپلود به Filebin", alert=True)
        except Exception:
            pass
    finally:
        try:
            _send_inflight.discard(("fbin", chat_id, msg_id))
        except Exception:
            pass
        # 🆕 پاکسازی پرچم لغو این عملیات
        try:
            if abort_token:
                _ABORT_FLAGS.discard(abort_token)
        except Exception:
            pass
        if work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)


# ═══════════════════════ ۴) آپلود برای پخش در VLC ═══════════════════════
async def fe_vlc_cb(event):
    """دکمه 🎬 آپلود برای پخش در VLC: fvlc_<chat_id>_<msg_id>

    فایل رو دانلود می‌کنه و روی هاستی می‌ذاره که لینکش بایت خام میده و
    مستقیم تو VLC پلی میشه (برخلاف filebin که صفحه تأیید داره).
    🆕 زنجیره‌ی fallback خودکار (اولین هاستی که جواب داد برنده‌ست):
    ۱. سرور خودم (اگه PUBLIC_BASE_URL ست باشه) — لینک پابلیک با Range، فوری
    ۲. Pixeldrain (اگه PIXDRAIN_API_KEY ست باشه) — تا ۲۰ گیگ
    ۳. Litterbox — تا ۱ گیگ، ۷۲ ساعته
    ۴. Catbox — تا ۲۰۰ مگ، دائمی
    ۵. Uguu — تا ۱۲۸ مگ، ۳ ساعته (بایت خام + Range)
    ۶. Gofile — با کلید اکانت directLink میده؛ بدون کلید فقط صفحه‌ی دانلود
    هر هاست حداکثر ۲ تلاش؛ کلید نامعتبر (401) → بدون ری‌تای هاست بعدی.
    """
    chat_id = msg_id = None
    work_dir = None
    abort_token = None
    try:
        if not _is_authorized(event.sender_id):
            await event.answer("⛔️ اجازه نداری", alert=True)
            return
        if aiohttp is None:
            await event.answer("⚠️ کتابخانه aiohttp روی سرور نصب نیست", alert=True)
            return
        _, chat_s, msg_s = event.data.decode("utf-8", "ignore").split("_", 2)
        chat_id, msg_id = int(chat_s), int(msg_s)

        # گارد دابل‌کلیک
        inflight_key = ("vlc", chat_id, msg_id)
        if inflight_key in _send_inflight:
            await event.answer("⏳ همین الان داره آپلود میشه...", alert=True)
            return
        _send_inflight.add(inflight_key)

        msg = await event.client.get_messages(chat_id, ids=msg_id)
        doc = msg.document if msg else None
        if doc is None:
            await event.answer("⚠️ فایل اصلی پیدا نشد — احتمالاً پاک شده", alert=True)
            return

        fname = _doc_filename(doc) or f"file_{msg_id}"
        size = getattr(doc, "size", 0) or 0

        # 🆕 ساخت زنجیره‌ی هاست‌ها — fallback خودکار:
        # سرور خودم → پیکسل‌درین → Litterbox → Catbox → Uguu → Gofile
        # (هاست‌هایی که سقف‌شون کمتر از حجم فایله حذف میشن)
        api_key = (os.environ.get("PIXDRAIN_API_KEY") or "").strip()
        chain: list = []
        if _self_server_base() and size <= SELF_MAX_BYTES:
            chain.append("self")
        if api_key and size <= PIXDRAIN_MAX_BYTES:
            chain.append("pixeldrain")
        if size <= LITTERBOX_MAX_BYTES:
            chain.append("litterbox")
        if size <= CATBOX_MAX_BYTES:
            chain.append("catbox")
        if size <= UGUU_MAX_BYTES:
            chain.append("uguu")
        chain.append("gofile")  # بی‌سقف عملی — همیشه آخرین امید (فقط صفحه)

        try:
            await event.answer("🎬 شروع آماده‌سازی لینک پخش...")
        except Exception:
            pass

        # 🆕 توکن یکتا برای دکمه لغو این عملیات
        abort_token = secrets.token_hex(6)

        await _safe_edit(
            event,
            f"🎬 در حال دانلود <b>{_esc(fname)}</b> برای آماده‌سازی لینک پخش...",
            _abort_rows(abort_token),
        )

        work_dir = os.path.join(_output_folder, f"vlc_{event.sender_id}_{secrets.token_hex(4)}")
        os.makedirs(work_dir, exist_ok=True)
        local_path = os.path.join(work_dir, _safe_disk_name(fname) or "file.bin")

        try:
            btn_msg = await event.get_message()
        except Exception:
            btn_msg = None
        prog = _ProgEdit(btn_msg, "دانلود", abort_token=abort_token)
        got = await event.client.download_media(msg, file=local_path, progress_callback=prog.cb)
        if not got or not os.path.exists(got) or os.path.getsize(got) == 0:
            await _safe_edit(
                event,
                "❌ دانلود فایل ناموفق بود — دوباره امتحان کن",
                _menu_rows(chat_id, msg_id),
            )
            return

        remote_name = _filebin_remote_name(fname)
        # 🆕 حلقه‌ی زنجیره‌ی آپلود — هر هاست حداکثر ۲ تلاش، شکست → هاست بعدی
        prov_fa = {
            "self": "سرور خودت",
            "pixeldrain": "پیکسل‌درین",
            "litterbox": "Litterbox",
            "catbox": "Catbox",
            "uguu": "Uguu",
            "gofile": "Gofile",
        }
        res = None
        used_prov = None
        err_lines: list = []
        for prov in chain:
            fa = prov_fa[prov]
            attempts = 1 if prov == "self" else 2
            for att in range(1, attempts + 1):
                label = fa if attempts == 1 else f"{fa} (تلاش {att}/{attempts})"
                await _safe_edit(
                    event,
                    f"⬆️ در حال آپلود به <b>{_esc(label)}</b>...",
                    _abort_rows(abort_token),
                )
                # ⚠️ آپلود بدون توکن‌ریز در cb — لغو با کنسلِ تسک (بخش Filebin)
                prog_up = _ProgEdit(btn_msg, "آپلود")
                try:
                    if prov == "self":
                        try:
                            res = _self_upload(remote_name, got, prog_up)
                        except Exception as e:
                            raise _FeError(
                                f"ثبت روی سرور خودم ناموفق: {_esc(str(e)[:150])}"
                            ) from e
                    else:
                        if prov == "pixeldrain":
                            coro = _pixeldrain_upload(remote_name, got, prog_up, api_key)
                        elif prov == "litterbox":
                            coro = _litterbox_upload(remote_name, got, prog_up)
                        elif prov == "catbox":
                            coro = _catbox_upload(remote_name, got, prog_up)
                        elif prov == "uguu":
                            coro = _uguu_upload(remote_name, got, prog_up)
                        else:
                            coro = _gofile_upload(remote_name, got, prog_up)
                        res = await _run_upload_with_abort(abort_token, coro)
                    used_prov = prov
                    break
                except _UploadAborted:
                    raise
                except _FeAuthError as e:
                    # کلید نامعتبره — ری‌تای بی‌فایده؛ مستقیم هاست بعدی
                    err_lines.append(f"• {fa}: {e}")
                    break
                except _FeError as e:
                    err_lines.append(f"• {fa} (تلاش {att}): {e}")
                    if att < attempts:
                        await asyncio.sleep(2)
            if res:
                break
        if not res:
            hint = (
                "\n\n💡 برای پایداری بیشتر، کلید رایگان پیکسل‌درین (تا ۲۰ گیگ) رو تو "
                "<code>PIXDRAIN_API_KEY</code> بذار و دامنه‌ی سرور خودت رو تو "
                "<code>PUBLIC_BASE_URL</code>."
                if "pixeldrain" not in chain else ""
            )
            raise _FeError(
                "همه‌ی هاست‌ها شکست خوردن:\n" + "\n".join(err_lines[-4:]) + hint
            )

        fsize = os.path.getsize(got)
        vlc_hint = "🎬 پخش تو VLC: Media → Open Network Stream (Ctrl+N) → لینک رو Paste کن"
        # 🆕 پیام موفقیت یکسان برای همه‌ی هاست‌ها + نکته‌ی اعتبار لینک
        exp_note = {
            "self": f"⏳ اعتبار لینک: {SELF_EXPIRY_HOURS:g} ساعت (هاست: سرور خودت — لینک پابلیک)",
            # 🆕 پیکسل‌درین هم دیفالت بعد از چند ساعت خودکار پاک میشه
            "pixeldrain": (
                f"⏳ اعتبار لینک: {PIXELDRAIN_AUTO_DELETE_HOURS:g} ساعت (بعدش خودکار از پیکسل‌درین پاک میشه)"
                if PIXELDRAIN_AUTO_DELETE_HOURS > 0
                else "♾ این لینک تا وقتی فایل رو از پیکسل‌درین پاک نکنی معتبره"
            ),
            "litterbox": "⏳ اعتبار این لینک: ۷۲ ساعت",
            "catbox": "♾ این لینک دائمیـه",
            "uguu": "⏳ اعتبار این لینک: ۳ ساعت",
            "gofile": (
                f"⏳ اعتبار لینک: {GOFILE_AUTO_DELETE_HOURS:g} ساعت (خودکار پاک میشه)"
                if GOFILE_AUTO_DELETE_HOURS > 0
                else "♾ تا وقتی اکانت فعال باشه معتبره (اگه ۱۰ روز دانلود نشه گوفایل پاکش می‌کنه)"
            ),
        }[used_prov]
        if res["play_url"]:
            # ✅ هاست لینک پخش مستقیم (بایت خام) داد
            text = (
                f"✅ <b>آپلود با موفقیت انجام شد! ({_esc(prov_fa[used_prov])})</b>\n\n"
                f"📄 فایل: <code>{_esc(res['name'])}</code>\n"
                f"📏 حجم: {_fmt_size(fsize)}\n\n"
                "▶️ لینک مستقیم پخش در VLC:\n"
                f"{res['play_url']}\n\n"
                + (
                    f"⬇️ لینک دانلود مستقیم:\n{res['dl_url']}\n\n"
                    if res["dl_url"] and res["dl_url"] != res["play_url"] else ""
                )
                + (
                    f"📄 لینک صفحه:\n{res['page_url']}\n\n"
                    if res.get("page_url") else ""
                )
                + f"{exp_note}\n\n{vlc_hint}"
            )
            rows = [
                [Button.url("▶️ پخش در VLC", res["play_url"]),
                 Button.url("⬇️ دانلود", res["dl_url"] or res["play_url"])],
            ]
            if res.get("page_url"):
                rows.append([Button.url("📄 صفحه فایل", res["page_url"])])
        else:
            # ⚠️ فقط صفحه‌ی دانلود داد (گوفایل بدون directLink) — صادقانه بگو
            text = (
                f"⚠️ <b>آپلود انجام شد ولی پخش مستقیم نداد ({_esc(prov_fa[used_prov])})</b>\n\n"
                f"📄 فایل: <code>{_esc(res['name'])}</code>\n"
                f"📏 حجم: {_fmt_size(fsize)}\n\n"
                "⬇️ لینک دانلود (صفحه‌ی فایل — تو VLC پخش نمیشه):\n"
                f"{res['dl_url']}\n\n"
                f"{exp_note}\n\n"
                "💡 برای لینک پخش مستقیم، کلید پیکسل‌درین یا دامنه‌ی سرور خودت رو تنظیم کن."
            )
            rows = [
                [Button.url("⬇️ صفحه‌ی دانلود", res["dl_url"])],
            ]
        await _safe_edit(event, text, rows)
    except _UploadAborted:
        # 🆕 کاربر دکمه لغو رو زده — پیام لغو + برگشت منو
        logger.info("[FileExplorer] vlc upload cancelled by user")
        try:
            rows = _menu_rows(chat_id, msg_id) if chat_id is not None and msg_id is not None else None
            await _safe_edit(event, "❌ آپلود لغو شد.", rows)
        except Exception:
            pass
    except _FeError as e:
        # خطای دوستانه از آپلودرها — متنش HTML امنه
        logger.warning(f"[FileExplorer] vlc upload failed: {e}")
        try:
            rows = _menu_rows(chat_id, msg_id) if chat_id is not None and msg_id is not None else None
            await _safe_edit(event, f"❌ {e}", rows)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[FileExplorer] vlc cb error: {e}", exc_info=True)
        try:
            await event.answer("❌ خطا در آپلود برای پخش", alert=True)
        except Exception:
            pass
    finally:
        try:
            _send_inflight.discard(("vlc", chat_id, msg_id))
        except Exception:
            pass
        # 🆕 پاکسازی پرچم لغو این عملیات
        try:
            if abort_token:
                _ABORT_FLAGS.discard(abort_token)
        except Exception:
            pass
        if work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)


# ═══════════════════════ ثبت هندلرها ═══════════════════════
def register_file_explorer_handlers(
    client,
    skip_check: Optional[Callable] = None,
    is_authorized: Optional[Callable[[int], bool]] = None,
    output_folder: str = "output_files",
):
    global _client, _skip_check, _is_authorized, _output_folder
    _client = client
    _skip_check = skip_check
    _is_authorized = is_authorized or (lambda uid: True)
    _output_folder = output_folder or "output_files"
    os.makedirs(_output_folder, exist_ok=True)

    # واکنش به داکیومنت‌ها (باید بعد از هندلرهای video/subtitle ثبت بشه)
    client.add_event_handler(
        document_receive_handler,
        events.NewMessage(incoming=True, func=lambda e: bool(e.document)),
    )
    # دکمه‌های مرورگر آرشیو
    client.add_event_handler(fe_open_cb, events.CallbackQuery(pattern=r"^fexopen_-?\d+_\d+$"))
    client.add_event_handler(fe_nav_cb, events.CallbackQuery(pattern=r"^fexn_[0-9a-f]+_\d+$"))
    client.add_event_handler(fe_file_cb, events.CallbackQuery(pattern=r"^fexf_[0-9a-f]+_\d+$"))
    client.add_event_handler(fe_page_cb, events.CallbackQuery(pattern=r"^fexp_[0-9a-f]+_\d+$"))
    client.add_event_handler(fe_up_cb, events.CallbackQuery(pattern=r"^fexup_[0-9a-f]+$"))
    client.add_event_handler(fe_close_cb, events.CallbackQuery(pattern=r"^fexclose_[0-9a-f]+$"))
    client.add_event_handler(fe_noop_cb, events.CallbackQuery(pattern=r"^fexnoop$"))
    # آپلود به Filebin
    client.add_event_handler(fe_filebin_cb, events.CallbackQuery(pattern=r"^fbin_-?\d+_\d+$"))
    # آپلود برای پخش در VLC (Litterbox / Pixeldrain)
    client.add_event_handler(fe_vlc_cb, events.CallbackQuery(pattern=r"^fvlc_-?\d+_\d+$"))
    # تغییر نام
    client.add_event_handler(fe_rename_cb, events.CallbackQuery(pattern=r"^fren_-?\d+_\d+$"))
    client.add_event_handler(fe_rename_cancel_cb, events.CallbackQuery(pattern=r"^frenc_\d+$"))
    client.add_event_handler(fe_rename_text_handler, events.NewMessage(incoming=True))
    # 🆕 بستن منوی اصلی + لغو عملیات‌ها
    client.add_event_handler(fe_dismiss_cb, events.CallbackQuery(pattern=r"^fexdism_-?\d+_\d+$"))
    client.add_event_handler(fe_abort_cb, events.CallbackQuery(pattern=r"^fexab_[0-9a-f]+$"))
    # 🆕 دستور /clean — پاکسازی پیکسل‌درین / گوفایل / سرور خودمون
    client.add_event_handler(fe_clean_cmd, events.NewMessage(pattern=r"^/clean(@\w+)?\s*$"))
    client.add_event_handler(fe_clean_cb, events.CallbackQuery(pattern=r"^fcln[a-z0-9_]*$"))

    asyncio.ensure_future(_session_gc_loop())
    # 🆕 حلقه‌ی حذف خودکار ابری (پیکسل‌درین/گوفایل) + گاربیج سشن /clean
    asyncio.ensure_future(_cloud_gc_loop())
    logger.info("[FileExplorer] handlers registered (zip/apk/tar/7z/rar + rename + filebin + vlc + cancel + /clean + autodel)")
