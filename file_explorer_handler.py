#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════════
#  File Explorer + Renamer — واکنش به هر داکیومنت با ۲ دکمه شیشه‌ای
# ───────────────────────────────────────────────────────────────────
#  ۱) 🔍 جستجو در فایل:
#     اگه فایل آرشیو باشه (zip / apk / rar / 7z / tar و ...) محتویاتش
#     به‌صورت دکمه‌های شیشه‌ای (اینلاین) نشون داده میشه؛ پوشه‌ها قابل
#     باز شدن هستن و با زدن روی هر فایل، همون فایل جداگانه برمی‌گرده.
#  ۲) ✏️ تغییر نام:
#     ربات اسم جدید رو می‌پرسه (مثلاً mamad.apk) و همون فایل رو با
#     اسم جدید برمی‌گردونه (بدون هیچ تغییری توی محتوا).
# ═══════════════════════════════════════════════════════════════════

import asyncio
import html
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

from telethon import Button, events
from telethon.errors import MessageNotModifiedError
from telethon.tl.types import (
    DocumentAttributeAnimated,
    DocumentAttributeSticker,
    DocumentAttributeVideo,
)

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

# ⚠️ FIX: ReplyInlineMarkup(rows=[]) روی سرور تلگرام نامعتبره و خطای
# ReplyMarkupInvalidError میده. برای «غیرفعال کردن» دکمه‌های قبلی موقع ادیت
# باید یه دکمه‌ی no-op معتبر (کلیکش هیچ کاری نمی‌کنه) جایگزین بشه.
# (buttons=None یعنی دکمه‌های قبلی سر جاشون بمونن)
def _idle_rows(label: str):
    """یه ردیف دکمه no-op معتبر — جایگزین امن برای مارک‌آپ خالی."""
    return [[Button.inline(label, "fexnoop")]]


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
    """progress_callback همگام تلگرام → ادیت دوره‌ای پیام وضعیت."""

    def __init__(self, msg, label: str):
        self.msg = msg
        self.label = label
        self.last_t = 0.0

    def cb(self, current: int, total: int):
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
    """هر داکیومنتی که هندلر دیگه‌ای نمی‌گیره → ۲ دکمه شیشه‌ای."""
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
        buttons = [
            [Button.inline("🔍 جستجو در فایل", f"fexopen_{event.chat_id}_{event.id}")],
            [Button.inline("✏️ تغییر نام", f"fren_{event.chat_id}_{event.id}")],
        ]
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
        retry_buttons = [
            [Button.inline("🔍 جستجو در فایل", f"fexopen_{chat_id}_{msg_id}")],
            [Button.inline("✏️ تغییر نام", f"fren_{chat_id}_{msg_id}")],
        ]
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

            prog = _ProgEdit(event.message, "دانلود")
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
    try:
        client = event.client
        status = await event.reply(
            f"⏳ در حال آماده‌سازی <b>{_esc(new_name)}</b>...", parse_mode="html"
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

        prog = _ProgEdit(status, "دانلود")
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

        await client.send_file(
            st["chat_id"],
            final_path,
            force_document=True,
            caption=f"✏️ <b>{_esc(old_name)}</b> ← <b>{_esc(new_name)}</b>",
            parse_mode="html",
        )
        try:
            await status.edit(f"✅ فایل با اسم جدید ارسال شد: <code>{_esc(new_name)}</code>", parse_mode="html")
        except Exception:
            pass
    except events.StopPropagation:
        raise
    except Exception as e:
        logger.error(f"[FileExplorer] rename error: {e}", exc_info=True)
        if status:
            try:
                await status.edit("❌ خطا در تغییر نام — دوباره امتحان کن", parse_mode="html")
            except Exception:
                pass
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    raise events.StopPropagation


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
    # تغییر نام
    client.add_event_handler(fe_rename_cb, events.CallbackQuery(pattern=r"^fren_-?\d+_\d+$"))
    client.add_event_handler(fe_rename_cancel_cb, events.CallbackQuery(pattern=r"^frenc_\d+$"))
    client.add_event_handler(fe_rename_text_handler, events.NewMessage(incoming=True))

    asyncio.ensure_future(_session_gc_loop())
    logger.info("[FileExplorer] handlers registered (zip/apk/tar/7z/rar + rename)")
