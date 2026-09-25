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
# ۴) 🎬 آپلود برای پخش در VLC (زنجیره‌ی فالباک چند‌هاسته):
#     فایل رو روی اولین هاستِ سالمِ «لینک مستقیم» می‌ذاره که لینکش بایت
#     خام میده و مستقیم تو VLC پلی میشه (برخلاف Filebin که صفحه تأیید
#     داره). ترتیب زنجیره:
#       • سرور خود ربات (بدون آپلود، بدون سقف — راه‌حل همیشگی؛ پورت
#         VLC_PORT پیش‌فرض 8099، یا VLC_PUBLIC_BASE تو .env)
#       • Pixeldrain اگه PIXDRAIN_API_KEY تو .env باشه (تا ۲۰ گیگ)
#       • Litterbox (تا ۱ گیگ، لینک ۷۲ ساعته)
#       • Catbox (تا ۲۰۰ مگ، دائمی) → 0x0.st (تا ۵۱۲ مگ، ۳۰ روز)
#     هر هاست ناشناس اول با یه آپلود ۶۴ کیلوبایتی «سلامت‌سنجی» میشه تا
#     برای هاست خراب چند صد مگ هدر نره؛ هاست شکست‌خورده هم ۲۰ تا ۴۵
#     دقیقه cooldown می‌گیره و دفعات بعد خودکار رد میشه.
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

# aiohttp — برای آپلود به Filebin/VLC + سرور HTTP خودمیزبان VLC
try:
    import aiohttp  # type: ignore
    from aiohttp import web as _web  # type: ignore
except Exception:
    aiohttp = None
    _web = None

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

# ───── VLC: زنجیره‌ی هاست‌های «لینک مستقیم» + فالباک خودکار ─────
# برخلاف filebin (که برای دانلود صفحه تأیید HTML میده)، لینک این‌ها بایت
# خام + Accept-Ranges میده و مستقیم تو VLC پلی میشه. نتایج تست تجربی
# (۲۰۲۶-۰۹ از IP دیتاسنتر):
#   • litterbox: POST multipart به api.php → لینک https://litter.catbox.moe/xx.ext
#     (سقف ۱ گیگ، ۷۲ ساعت، Range → 206 ✓) — گاهی 403/500 میده (بلاک IP
#     دیتاسنتر یا اورلود بک‌اند) → همون لحظه میریم سراغ فالباک بعدی
#   • pixeldrain: PUT /api/file/{name} با BasicAuth (پسورد = API Key
#     اکانت رایگان) → سقف ۲۰ گیگ، لینک /api/file/{id} تو VLC پلی میشه؛
#     بدون کلید «authentication_required» میده → فقط با کلید
#   • catbox: هم‌خانواده‌ی litterbox — لینک دائمی ولی سقف ۲۰۰ مگ
#   • 0x0.st: POST multipart → لینک مستقیم (سقف ۵۱۲ مگ، ۳۰+ روز)
#   • gofile (لینک مستقیم فقط پرمیوم)، temp.sh (بایت خام فقط با POST
#     که VLC پشتیبانی نمی‌کنه)، bashupload/pomf/oshi/transfer.sh (مرده)،
#     tmpfiles (ریدایرکت به صفحه) → همگی تست و از زنجیره حذف شدن
PIXDRAIN_BASE = os.environ.get("PIXDRAIN_BASE", "https://pixeldrain.com").rstrip("/")
PIXDRAIN_MAX_BYTES = 20 * 1024 * 1024 * 1024        # 20GB — سقف اکانت رایگان
LITTERBOX_BASE = os.environ.get("LITTERBOX_BASE", "https://litterbox.catbox.moe").rstrip("/")
LITTERBOX_MAX_BYTES = 1000 * 1024 * 1024            # ۱ گیگ — سقف بدون ثبت‌نام
CATBOX_BASE = "https://catbox.moe"
CATBOX_MAX_BYTES = 200 * 1024 * 1024                # ۲۰۰ مگ — سقف بدون ثبت‌نام
ZEROX0_BASE = os.environ.get("ZEROX0_BASE", "https://0x0.st").rstrip("/")
ZEROX0_MAX_BYTES = 512 * 1024 * 1024                # ۵۱۲ مگ

# ⭐ سرور خودمیزبان VLC — «راه حل همیشگی»: فایل از خود سرور ربات با
# پشتیبانی Range سرو میشه؛ نه آپلود بیرونی می‌خواد نه ثبت‌نام و نه سقف
# حجمی، و هیچ هاست ثالثی هم نمی‌تونه بلاکش کنه. تنظیمات (همه اختیاری):
#   VLC_PORT        پورت HTTP سرور (پیش‌فرض 8099)
#   VLC_PUBLIC_BASE آدرس عمومی اگه دامنه/پورت‌فوروارد خاصی داری
#                   (مثلاً https://vlc.example.com یا http://5.6.7.8:9000)
#   VLC_SELF_HOST   بذار 0 تا کلاً غیرفعال بشه
VLC_SELF_PORT = int(os.environ.get("VLC_PORT", "8099") or "8099")
VLC_PUBLIC_BASE = os.environ.get("VLC_PUBLIC_BASE", "").strip().rstrip("/")
VLC_SELF_ENABLED = os.environ.get("VLC_SELF_HOST", "1").strip().lower() not in {"0", "false", "no", "off"}
VLC_SELF_TTL = 6 * 3600                             # فایل ۶ ساعت سرو میشه
_VLC_STREAM_DIR = "/tmp/vlc_streams"
_VLC_WEB_FILES: Dict[str, dict] = {}                # token → اطلاعات فایل
_VLC_WEB_READY = asyncio.Event()
_VLC_SERVER_TASK: Optional[asyncio.Task] = None
_VLC_SERVER_BASE: Optional[str] = None              # وقتی سالم شد ست میشه
_VLC_SERVER_CHECKED_AT = 0.0
_VLC_CANARY_PATH = "/tmp/.vlc_canary_64k.bin"

# 🧠 حافظه‌ی سلامت هاست‌ها — هاستی که شکست بخوره تا این‌دیگر امتحان
# نمیشه (که هر بار چند صد مگ برای هاست مرده آپلود و هدر نشه)
_HOST_COOLDOWN_UNTIL: Dict[str, float] = {}         # host_key → زمان رفع محرومیت
_HOST_COOLDOWN_SECS = {"litterbox": 45 * 60, "catbox": 20 * 60, "zerox0": 20 * 60}
_CANARY_HOSTS = {"litterbox", "catbox", "zerox0"}
_CANARY_OK_UNTIL: Dict[str, float] = {}             # نتیجه‌ی canary ۱۰ دقیقه‌ای کش میشه

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
                    return {
                        "name": remote_name,
                        "play_url": f"{PIXDRAIN_BASE}/api/file/{fid}",
                        "dl_url": f"{PIXDRAIN_BASE}/api/file/{fid}?download",
                        "page_url": f"{PIXDRAIN_BASE}/u/{fid}",
                        "host_fa": "پیکسل‌درین",
                        "expires_fa": "تا ۳۰ روز بی‌فعالیت",
                    }
                if resp.status == 401:
                    raise _FeError(
                        "کلید API پیکسل‌درین معتبر نیست — مقدار "
                        "<code>PIXDRAIN_API_KEY</code> رو تو <code>.env</code> چک کن"
                    )
                msg = str(data.get("message") or body[:120])
                raise _FeError(
                    f"پیکسل‌درین آپلود رو قبول نکرد (کد {resp.status}):\n<code>{_esc(msg)}</code>"
                )
    except _FeError:
        raise
    except Exception as e:
        raise _FeError(f"خطای اتصال به پیکسل‌درین:\n<code>{_esc(str(e)[:120])}</code>")


async def _litterbox_upload(remote_name: str, local_path: str, prog, ttl: str = "72h") -> dict:
    """آپلود استریمی به litterbox.catbox.moe — بدون ثبت‌نام (سقف ۱ گیگ، ۷۲ ساعت).

    POST multipart به api.php با فیلدهای reqtype=fileupload / time / 
    fileToUpload → جواب: متن ساده‌ی لینک مستقیم (بایت خام + Range → VLC ✓)
    خروجی: dict با play_url (همون لینک مستقیم برای پخش و دانلود).
    ttl برای canary سلامت‌سنجی «1h» میشه (کم‌هزینه‌ترین گزینه)."""
    url = f"{LITTERBOX_BASE}/resources/internals/api.php"
    total = os.path.getsize(local_path)
    payload = _ProgressFilePayload(local_path, prog, total)
    form = aiohttp.FormData()
    form.add_field("reqtype", "fileupload")
    form.add_field("time", ttl)
    form.add_field("fileToUpload", payload, filename=remote_name or "file.bin")
    headers = {"Accept": "*/*", "User-Agent": FILEBIN_UA}
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=180)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, data=form, headers=headers) as resp:
                body = (await resp.text(errors="ignore")).strip()
                if resp.status == 200 and body.startswith("http"):
                    link = body.split()[0][:300]
                    return {
                        "name": remote_name,
                        "play_url": link,
                        "dl_url": link,
                        "page_url": "",
                        "host_fa": "Litterbox",
                        "expires_fa": "۷۲ ساعت" if ttl == "72h" else ttl,
                    }
                raise _FeError(
                    f"Litterbox آپلود رو قبول نکرد (کد {resp.status}):\n"
                    f"<code>{_esc(body[:120])}</code>"
                )
    except _FeError:
        raise
    except Exception as e:
        raise _FeError(f"خطای اتصال به Litterbox:\n<code>{_esc(str(e)[:120])}</code>")


async def _catbox_upload(remote_name: str, local_path: str, prog) -> dict:
    """آپلود استریمی به catbox.moe — هم‌خانواده‌ی litterbox ولی لینکش دائمی‌ست (سقف ۲۰۰ مگ).

    POST multipart به user/api.php با reqtype=fileupload → جواب: متن ساده‌ی
    لینک https://files.catbox.moe/xx.ext (بایت خام + Range → VLC ✓)"""
    url = f"{CATBOX_BASE}/user/api.php"
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
                    return {
                        "name": remote_name,
                        "play_url": link,
                        "dl_url": link,
                        "page_url": "",
                        "host_fa": "Catbox",
                        "expires_fa": "دائمی",
                    }
                raise _FeError(
                    f"Catbox آپلود رو قبول نکرد (کد {resp.status}):\n"
                    f"<code>{_esc(body[:120])}</code>"
                )
    except _FeError:
        raise
    except Exception as e:
        raise _FeError(f"خطای اتصال به Catbox:\n<code>{_esc(str(e)[:120])}</code>")


async def _zerox0_upload(remote_name: str, local_path: str, prog) -> dict:
    """آپلود استریمی به 0x0.st — بدون ثبت‌نام (سقف ۵۱۲ مگ، نگهداری ۳۰+ روز).

    POST multipart با فیلد file → جواب: متن ساده‌ی لینک مستقیم.
    ⚠️ بعضی IPهای دیتاسنتری رو بلاک می‌کنه — فقط یه فالباک ارزونه."""
    url = ZEROX0_BASE
    total = os.path.getsize(local_path)
    payload = _ProgressFilePayload(local_path, prog, total)
    form = aiohttp.FormData()
    form.add_field("file", payload, filename=remote_name or "file.bin")
    headers = {"Accept": "*/*", "User-Agent": FILEBIN_UA}
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=180)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, data=form, headers=headers) as resp:
                body = (await resp.text(errors="ignore")).strip()
                if resp.status == 200 and body.startswith("http"):
                    link = body.split()[0][:300]
                    return {
                        "name": remote_name,
                        "play_url": link,
                        "dl_url": link,
                        "page_url": "",
                        "host_fa": "0x0.st",
                        "expires_fa": "حداقل ۳۰ روز",
                    }
                raise _FeError(
                    f"0x0.st آپلود رو قبول نکرد (کد {resp.status}):\n"
                    f"<code>{_esc(body[:120])}</code>"
                )
    except _FeError:
        raise
    except Exception as e:
        raise _FeError(f"خطای اتصال به 0x0.st:\n<code>{_esc(str(e)[:120])}</code>")


# ═══════ سرور خودمیزبان VLC — راه‌حل همیشگی بدون هاست ثالث ═══════
# یه HTTP server کوچیک با aiohttp.web که فایل رو با پشتیبانی کامل Range
# (برای seek در VLC) سرو می‌کنه. فقط یک‌بار راه می‌افته و تا خاموشی ربات
# زنده می‌مونه. دسترسی عمومی با self-check تأیید میشه (اگه پورت بسته باشه
# خودکار از زنجیره حذف میشه و بقیه هاست‌ها امتحان میشن).


def _vlc_guess_type(name: str) -> str:
    """تشخیص content-type برای سرو ویدیو/صدا/بقیه."""
    ext = os.path.splitext(name)[1].lower()
    return {
        ".mp4": "video/mp4", ".m4v": "video/mp4", ".mkv": "video/x-matroska",
        ".webm": "video/webm", ".avi": "video/x-msvideo", ".mov": "video/quicktime",
        ".wmv": "video/x-msvideo", ".flv": "video/x-flv", ".ts": "video/mp2t",
        ".mpg": "video/mpeg", ".mpeg": "video/mpeg", ".3gp": "video/3gpp",
        ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".flac": "audio/flac",
        ".ogg": "audio/ogg", ".wav": "audio/wav", ".opus": "audio/opus",
        ".srt": "application/x-subrip", ".vtt": "text/vtt",
        ".pdf": "application/pdf", ".zip": "application/zip",
    }.get(ext, "application/octet-stream")


def _vlc_sweep_expired():
    """پاکسازی فایل‌های منقضی‌شده‌ی سرور خودمیزبان."""
    now = time.time()
    expired = [t for t, v in _VLC_WEB_FILES.items() if v["expires_at"] < now]
    for t in expired:
        v = _VLC_WEB_FILES.pop(t)
        try:
            if os.path.islink(v["path"]) or os.path.exists(v["path"]):
                os.unlink(v["path"])
        except Exception:
            pass


async def _vlc_health_handler(request):
    """GET /vlc_health — برای self-check دسترسی عمومی."""
    return _web.Response(text="ok")


async def _vlc_web_handler(request):
    """GET /vlc/{token}[/{name}] — بایت خام + پشتیبانی کامل Range (206).

    فرم‌های Range پشتیبانی‌شده: bytes=start-end / bytes=start- / bytes=-suffix
    (همون چیزی که VLC و بقیه پلیرها برای seek می‌فرستن)."""
    token = request.match_info.get("token", "")
    info = _VLC_WEB_FILES.get(token)
    if not info:
        return _web.Response(status=404, text="not found")
    if time.time() > info["expires_at"]:
        _VLC_WEB_FILES.pop(token, None)
        try:
            if os.path.islink(info["path"]) or os.path.exists(info["path"]):
                os.unlink(info["path"])
        except Exception:
            pass
        return _web.Response(status=410, text="expired")
    path = info["path"]
    if not os.path.exists(path):
        return _web.Response(status=404, text="file gone")
    size = info["size"]
    hdr_name = re.sub(r"[^A-Za-z0-9._()\[\]-]", "_", info["name"]) or "file.bin"
    start, end, status = 0, size - 1, 200
    m = re.match(r"^bytes=(\d*)-(\d*)$", (request.headers.get("Range") or "").strip())
    if m and (m.group(1) or m.group(2)):
        try:
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:  # پسوندی: N بایت آخر
                start = max(0, size - int(m.group(2)))
                end = size - 1
        except ValueError:
            start, end = 0, size - 1
        if start >= size:
            return _web.Response(status=416, headers={"Content-Range": f"bytes */{size}"})
        start, end = max(0, start), min(end, size - 1)
        if start > end:
            start, end, status = 0, size - 1, 200
        else:
            status = 206
    length = end - start + 1
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": info["content_type"],
        "Content-Disposition": f'inline; filename="{hdr_name}"',
        "Cache-Control": "no-store",
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    resp = _web.StreamResponse(status=status, headers=headers)
    resp.content_length = length
    await resp.prepare(request)
    remaining = length
    try:
        with open(path, "rb") as f:
            f.seek(start)
            while remaining > 0:
                chunk = f.read(min(512 * 1024, remaining))
                if not chunk:
                    break
                await resp.write(chunk)
                remaining -= len(chunk)
        await resp.write_eof()
    except (ConnectionResetError, asyncio.CancelledError):
        pass  # پلیر وسط پخش قطع شد — طبیعیه
    except Exception as e:
        logger.warning(f"[FileExplorer] vlc stream error: {e}")
    return resp


async def _start_vlc_web_server():
    """راه‌اندازی یک‌باره‌ی سرور HTTP خودمیزبان (تا خاموشی ربات زنده می‌مونه)."""
    try:
        from aiohttp import web as web_mod
    except Exception as e:
        logger.warning(f"[FileExplorer] aiohttp.web unavailable: {e}")
        return
    try:
        os.makedirs(_VLC_STREAM_DIR, exist_ok=True)
        app = web_mod.Application()
        app.router.add_get("/vlc_health", _vlc_health_handler)
        app.router.add_get("/vlc/{token}", _vlc_web_handler)
        app.router.add_get("/vlc/{token}/{name}", _vlc_web_handler)
        runner = web_mod.AppRunner(app, access_log=None)
        await runner.setup()
        site = web_mod.TCPSite(runner, "0.0.0.0", VLC_SELF_PORT, reuse_address=True)
        await site.start()
        _VLC_WEB_READY.set()
        logger.info(f"[FileExplorer] VLC self-host HTTP server on 0.0.0.0:{VLC_SELF_PORT}")
        while True:
            await asyncio.sleep(3600)
    except Exception as e:
        logger.warning(
            f"[FileExplorer] VLC self-host could not bind :{VLC_SELF_PORT} ({e}) "
            "→ از زنجیره حذف شد؛ VLC_PORT دیگه‌ای تو .env تنظیم کن"
        )


async def _detect_public_base() -> Optional[str]:
    """IP عمومی سرور رو از سرویس‌های echo می‌گیره (برای لینک self-host)."""
    if aiohttp is None:
        return None
    timeout = aiohttp.ClientTimeout(total=10, sock_connect=8)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as s:
            for u in ("https://api.ipify.org/", "https://ifconfig.me/ip"):
                try:
                    async with s.get(u, headers={"User-Agent": FILEBIN_UA}) as r:
                        if r.status == 200:
                            t = (await r.text()).strip()
                            if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", t):
                                return f"http://{t}:{VLC_SELF_PORT}"
                except Exception:
                    continue
    except Exception:
        pass
    return None


async def _ensure_vlc_web_server() -> Optional[str]:
    """مطمئن میشه سرور خودمیزبان بالاست و از مسیر عمومی هم قابل دسترسی‌ست.

    خروجی: base URL سالم (مثل http://5.6.7.8:8099) یا None (پورت بسته/غیرفعال).
    نتیجه‌ی سالم ۱۰ دقیقه کش میشه که هر کلیک چک مجدد نشه."""
    global _VLC_SERVER_TASK, _VLC_SERVER_BASE, _VLC_SERVER_CHECKED_AT
    if not VLC_SELF_ENABLED or aiohttp is None or _web is None:
        return None
    now = time.time()
    if _VLC_SERVER_BASE and (now - _VLC_SERVER_CHECKED_AT) < 600:
        return _VLC_SERVER_BASE
    if _VLC_SERVER_TASK is None or _VLC_SERVER_TASK.done():
        _VLC_SERVER_TASK = asyncio.ensure_future(_start_vlc_web_server())
        try:
            await asyncio.wait_for(_VLC_WEB_READY.wait(), timeout=5)
        except Exception:
            pass  # بند نشد — health check پایین هم به‌هرحال رد می‌کنه
    base = VLC_PUBLIC_BASE or await _detect_public_base()
    if not base:
        logger.warning("[FileExplorer] VLC self-host: آدرس عمومی تشخیص داده نشد")
        return None
    try:
        timeout = aiohttp.ClientTimeout(total=12, sock_connect=8)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(f"{base.rstrip('/')}/vlc_health") as r:
                if r.status == 200 and (await r.text()).strip() == "ok":
                    _VLC_SERVER_BASE = base.rstrip("/")
                    _VLC_SERVER_CHECKED_AT = now
                    logger.info(f"[FileExplorer] VLC self-host healthy: {_VLC_SERVER_BASE}")
                    return _VLC_SERVER_BASE
    except Exception as e:
        logger.warning(f"[FileExplorer] VLC self-host health check failed ({base}): {e}")
    return None


async def _selfhost_upload(remote_name: str, local_path: str, prog) -> dict:
    """⭐ راه‌حل همیشگی — فایل روی HTTP سرور خودِ ربات سرو میشه.

    نه آپلود بیرونی لازمه (لینک فوریه)، نه ثبت‌نام، نه سقف حجم؛ و هیچ
    هاست ثالثی هم نمی‌تونه بلاکش کنه. فایل به پوشه‌ی سرو منتقل (move)
    میشه و ۶ ساعت قابل پخشه."""
    base = await _ensure_vlc_web_server()
    if not base:
        raise _FeError(
            "سرور VLC خودِ ربات در دسترس نیست — پورت <code>"
            f"{VLC_SELF_PORT}</code> تو فایروال سرور بسته‌ست "
            "(یا تو <code>.env</code> بذار <code>VLC_SELF_HOST=0</code> تا کلاً رد بشه)"
        )
    _vlc_sweep_expired()
    token = secrets.token_hex(10)
    safe = _safe_disk_name(remote_name) or "file.bin"
    os.makedirs(_VLC_STREAM_DIR, exist_ok=True)
    dest = os.path.join(_VLC_STREAM_DIR, f"{token}_{safe}")
    shutil.move(local_path, dest)   # cross-device هم خودش کپی+پاک می‌کنه
    _VLC_WEB_FILES[token] = {
        "path": dest,
        "name": safe,
        "size": os.path.getsize(dest),
        "content_type": _vlc_guess_type(safe),
        "expires_at": time.time() + VLC_SELF_TTL,
    }
    url = f"{base}/vlc/{token}/{quote(safe)}"
    prog.cb(1, 1)
    logger.info(f"[FileExplorer] vlc self-host: {safe} → {url}")
    return {
        "name": remote_name,
        "play_url": url,
        "dl_url": url,
        "page_url": "",
        "host_fa": "سرور خود ربات",
        "expires_fa": "۶ ساعت (تا ری‌استارت ربات)",
    }


# ═══════ سلامت‌سنجی + حافظه‌ی هاست‌ها + زنجیره‌ی فالباک ═══════


class _NoopProg:
    """پیشرفت ساختگی برای آپلودهای سلامت‌سنج (canary)."""

    def cb(self, current: int, total: int):
        pass


def _vlc_canary_file() -> str:
    """فایل ۶۴ کیلوبایتی ثابت برای canary — یک‌بار ساخته میشه."""
    try:
        if not os.path.exists(_VLC_CANARY_PATH) or os.path.getsize(_VLC_CANARY_PATH) != 65536:
            with open(_VLC_CANARY_PATH, "wb") as f:
                f.write(os.urandom(65536))
    except Exception:
        pass
    return _VLC_CANARY_PATH


async def _canary_ok(host_key: str) -> bool:
    """سلامت‌سنجی ۶۴ کیلوبایتی هاست‌های ناشناس — تا برای هاست مرده چند
    صد مگ آپلود و هدر نره. نتیجه‌ی موفق ۱۰ دقیقه کش میشه."""
    if time.time() < _CANARY_OK_UNTIL.get(host_key, 0.0):
        return True
    path = _vlc_canary_file()
    try:
        if host_key == "litterbox":
            await _litterbox_upload("canary.bin", path, _NoopProg(), ttl="1h")
        elif host_key == "catbox":
            await _catbox_upload("canary.bin", path, _NoopProg())
        elif host_key == "zerox0":
            await _zerox0_upload("canary.bin", path, _NoopProg())
        else:
            return True
    except Exception as e:
        logger.info(f"[FileExplorer] canary {host_key} failed: {e}")
        return False
    _CANARY_OK_UNTIL[host_key] = time.time() + 600
    return True


def _host_in_cooldown(key: str) -> bool:
    return time.time() < _HOST_COOLDOWN_UNTIL.get(key, 0.0)


def _host_fail(key: str):
    """هاست شکست‌خورده رو یه مدت استراحت می‌ده (مدت‌ها تو _HOST_COOLDOWN_SECS)."""
    secs = _HOST_COOLDOWN_SECS.get(key, 20 * 60)
    _HOST_COOLDOWN_UNTIL[key] = time.time() + secs
    logger.warning(f"[FileExplorer] host {key} failed → cooldown {secs // 60}m")


def _host_ok(key: str):
    _HOST_COOLDOWN_UNTIL.pop(key, None)


def _vlc_chain(size: int, api_key: str):
    """زنجیره‌ی هاست‌های VLC به ترتیب تلاش — بر اساس حجم/کلید/cooldown.

    خروجی: لیستی از (key, نام‌فارسی, coroutine_factory) که coroutine_factory
    امضای (remote_name, local_path, prog) داره."""
    items = []
    if VLC_SELF_ENABLED:
        items.append(("selfhost", "سرور خود ربات", 1 << 60,
                      lambda rn, p, pr: _selfhost_upload(rn, p, pr)))
    if api_key:
        items.append(("pixeldrain", "پیکسل‌درین", PIXDRAIN_MAX_BYTES,
                      lambda rn, p, pr: _pixeldrain_upload(rn, p, pr, api_key)))
    items.append(("litterbox", "Litterbox", LITTERBOX_MAX_BYTES,
                  lambda rn, p, pr: _litterbox_upload(rn, p, pr)))
    items.append(("catbox", "Catbox", CATBOX_MAX_BYTES,
                  lambda rn, p, pr: _catbox_upload(rn, p, pr)))
    items.append(("zerox0", "0x0.st", ZEROX0_MAX_BYTES,
                  lambda rn, p, pr: _zerox0_upload(rn, p, pr)))
    return [
        (k, fa, mk)
        for k, fa, mx, mk in items
        if size <= mx and not _host_in_cooldown(k)
    ]


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

    فایل رو دانلود می‌کنه و روی اولین هاست سالمِ «لینک مستقیم» می‌ذاره که
    بایت خام میده و مستقیم تو VLC پلی میشه (برخلاف filebin که صفحه تأیید
    داره). زنجیره‌ی فالباک (به ترتیب تلاش):
    • سرور خود ربات (بدون آپلود، بدون سقف — پورت VLC_PORT؛ پیش‌فرض 8099)
    • Pixeldrain اگه PIXDRAIN_API_KEY تو .env تنظیم شده باشه (تا ۲۰ گیگ)
    • Litterbox (تا ۱ گیگ، لینک ۷۲ ساعته)
    • Catbox (تا ۲۰۰ مگ، دائمی) → 0x0.st (تا ۵۱۲ مگ، ۳۰ روز)
    هر هاست ناشناس اول با آپلود ۶۴ کیلوبایتی سلامت‌سنجی میشه و هاست
    خراب cooldown می‌گیره تا دفعات بعد خودکار رد بشه.
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

        # 🆕 انتخاب هوشمند هاست — زنجیره‌ی فالباک چند‌هاسته
        api_key = (os.environ.get("PIXDRAIN_API_KEY") or "").strip()
        chain = _vlc_chain(size, api_key)
        if not chain:
            # فقط وقتی سرور خودمیزبان خاموشه، کلید هم نیست و حجم از سقف
            # همه‌ی هاست‌های ناشناس (۱ گیگ) بزرگ‌تره
            try:
                await event.answer("⚠️ برای این حجم تنظیمات لازمه", alert=True)
            except Exception:
                pass
            await _safe_edit(
                event,
                "⚠️ حجم فایل <b>" + _esc(_fmt_size(size)) + "</b> هست و بدون تنظیمات، "
                "سقف لینکِ قابل‌پخش (VLC) برای فایل‌ها <b>۱ گیگ</b>ه. دو راه داری:\n\n"
                "راه ۱ — فایل‌های تا <b>۲۰ گیگ</b> با پیکسل‌درین:\n"
                "۱️⃣ تو سایت pixeldrain.com یه اکانت رایگان بساز\n"
                "۲️⃣ از بخش تنظیمات اکانت، API Key رو کپی کن\n"
                "۳️⃣ تو فایل <code>.env</code> ربات این خط رو اضافه کن: "
                "<code>PIXDRAIN_API_KEY=کلید-شما</code>\n"
                "۴️⃣ ربات رو ری‌استارت کن\n\n"
                "راه ۲ — بدون محدودیت با سرور خود ربات:\n"
                "پورت <code>VLC_PORT</code> (پیش‌فرض 8099) رو تو فایروال سرور باز کن "
                "(یا آدرس عمومی رو تو <code>.env</code> بذار: "
                "<code>VLC_PUBLIC_BASE=http://IP-سرور:پورت</code>)",
                _menu_rows(chat_id, msg_id),
            )
            return

        try:
            await event.answer("🎬 شروع آماده‌سازی لینک پخش...")
        except Exception:
            pass

        # 🆕 توکن یکتا برای دکمه لغو این عملیات
        abort_token = secrets.token_hex(6)

        await _safe_edit(
            event,
            f"🎬 در حال دانلود <b>{_esc(fname)}</b> برای آپلود...",
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
        # ⚠️ حجم قبل از حلقه — هاست selfhost فایل رو move می‌کنه
        fsize = os.path.getsize(got)
        # ⚠️ آپلود بدون توکن‌ریز در cb — لغو با کنسلِ تسک (بخش Filebin)

        errors: list = []
        res = None
        used_host = ""
        for host_key, host_fa, make_coro in chain:
            # 🔎 سلامت‌سنجی هاست‌های ناشناس — هدررفت آپلود سنگین ممنوع
            if host_key in _CANARY_HOSTS:
                await _safe_edit(
                    event,
                    f"🔎 سلامت‌سنجی {host_fa}...",
                    _abort_rows(abort_token),
                )
                try:
                    alive = await _run_upload_with_abort(abort_token, _canary_ok(host_key))
                except _UploadAborted:
                    raise
                except Exception:
                    alive = False
                if not alive:
                    _host_fail(host_key)
                    errors.append(f"{host_fa}: فعلاً پاسخگو نیست (سلامت‌سنجی رد شد)")
                    logger.info(f"[FileExplorer] vlc: {host_key} canary failed → skip")
                    continue
            await _safe_edit(
                event,
                f"⬆️ آپلود <b>{_esc(fname)}</b> به {host_fa}...",
                _abort_rows(abort_token),
            )
            prog_up = _ProgEdit(btn_msg, f"آپلود {host_fa}")
            try:
                res = await _run_upload_with_abort(
                    abort_token, make_coro(remote_name, got, prog_up)
                )
                used_host = host_fa
                _host_ok(host_key)
                break
            except _UploadAborted:
                raise
            except Exception as e:
                _host_fail(host_key)
                errors.append(f"{host_fa}: {_esc(str(e).splitlines()[0][:110])}")
                logger.warning(f"[FileExplorer] vlc: host {host_key} failed → next", exc_info=True)
                continue

        if res is None:
            err_block = "\n".join(f"  • {x}" for x in errors) or "  • خطای نامشخص"
            await _safe_edit(
                event,
                "❌ <b>هیچ‌کدوم از هاست‌ها جواب ندادن:</b>\n"
                f"{err_block}\n\n"
                "💡 راه‌های دائمی:\n"
                "• کلید رایگان پیکسل‌درین (تا ۲۰ گیگ): <code>PIXDRAIN_API_KEY</code> تو <code>.env</code>\n"
                f"• سرور خود ربات (بدون سقف): باز کردن پورت <code>{VLC_SELF_PORT}</code> "
                "تو فایروال (یا <code>VLC_PUBLIC_BASE</code> تو <code>.env</code>)",
                _menu_rows(chat_id, msg_id),
            )
            return

        vlc_hint = "🎬 پخش تو VLC: Media → Open Network Stream (Ctrl+N) → لینک رو Paste کن"
        fallback_note = (
            "\n\nℹ️ هاست قبلی جواب نداده بود؛ از فالباک استفاده شد."
            if errors
            else ""
        )
        text = (
            f"✅ <b>فایل برای پخش آماده شد! ({_esc(used_host)})</b>\n\n"
            f"📄 فایل: <code>{_esc(res['name'])}</code>\n"
            f"📏 حجم: {_fmt_size(fsize)}\n\n"
            "▶️ لینک مستقیم پخش در VLC:\n"
            f"{res['play_url']}\n\n"
        )
        if res["dl_url"] and res["dl_url"] != res["play_url"]:
            text += f"⬇️ لینک دانلود مستقیم:\n{res['dl_url']}\n\n"
        if res.get("page_url"):
            text += f"📄 لینک صفحه:\n{res['page_url']}\n\n"
        text += (
            f"⏳ اعتبار این لینک: {res.get('expires_fa', 'موقت')}\n"
            f"{vlc_hint}{fallback_note}"
        )

        btn_row = [Button.url("▶️ پخش در VLC", res["play_url"])]
        if res["dl_url"] and res["dl_url"] != res["play_url"]:
            btn_row.append(Button.url("⬇️ دانلود", res["dl_url"]))
        rows = [btn_row]
        if res.get("page_url"):
            rows.append([Button.url("📄 صفحه فایل", res["page_url"])])
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
    # آپلود برای پخش در VLC (زنجیره‌ی چند‌هاسته + سرور خودمیزبان)
    client.add_event_handler(fe_vlc_cb, events.CallbackQuery(pattern=r"^fvlc_-?\d+_\d+$"))
    # تغییر نام
    client.add_event_handler(fe_rename_cb, events.CallbackQuery(pattern=r"^fren_-?\d+_\d+$"))
    client.add_event_handler(fe_rename_cancel_cb, events.CallbackQuery(pattern=r"^frenc_\d+$"))
    client.add_event_handler(fe_rename_text_handler, events.NewMessage(incoming=True))
    # 🆕 بستن منوی اصلی + لغو عملیات‌ها
    client.add_event_handler(fe_dismiss_cb, events.CallbackQuery(pattern=r"^fexdism_-?\d+_\d+$"))
    client.add_event_handler(fe_abort_cb, events.CallbackQuery(pattern=r"^fexab_[0-9a-f]+$"))

    asyncio.ensure_future(_session_gc_loop())
    logger.info("[FileExplorer] handlers registered (zip/apk/tar/7z/rar + rename + filebin + vlc + cancel)")
