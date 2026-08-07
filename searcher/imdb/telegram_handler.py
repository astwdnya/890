"""
telegram_handler.py
────────────────────
هندلر سرچ و دانلود IMDB برای ربات تلگرام.

ویژگی‌ها:
  - سرچ اینلاین: @bot the drama -> نتایج با کاور
  - انتخاب فیلم/سریال -> پیغام با caption + دکمه‌های شیشه‌ای
  - برای سریال: دکمه‌های فصل + قسمت
  - انتخاب قسمت -> شروع دانلود -> آپلود به تلگرام

استفاده با python-telegram-bot:
  from telegram_handler import register_handlers
  register_handlers(application)
"""
import asyncio
import logging
import os
import re
from typing import Optional

from telegram import (
    Update,
    InlineQueryResultArticle,
    InputTextMessageContent,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
)
from telegram.constants import ParseMode, ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    InlineQueryHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

import imdb_search
import vidsrc_downloader

logger = logging.getLogger("TelegramHandler")

# ─── State management ───────────────────────────────────────
# برای نگهداری وضعیت انتخاب کاربر
# user_id -> {imdb_id, info, eps}

_user_state: dict = {}


# ─── Helpers ────────────────────────────────────────────────


def _safe_name(name: str, max_len: int = 60) -> str:
    """ایمن‌سازی اسم فایل"""
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    return name[:max_len].strip()


def _format_caption(info: dict, eps: Optional[dict] = None) -> str:
    """ساخت کپشن برای پیام فیلم/سریال"""
    title = info.get("title", "Unknown")
    year = info.get("year")
    end_year = info.get("end_year")
    title_type = info.get("title_type", "")
    plot = info.get("plot", "")
    cover = info.get("cover", "")

    lines = [f"🎬 *{title}*"]
    if year:
        if end_year:
            lines.append(f"📅 {year}–{end_year}")
        else:
            lines.append(f"📅 {year}")
    if title_type:
        lines.append(f"🎞 {title_type}")
    if plot:
        # truncate to ~400 chars
        if len(plot) > 400:
            plot = plot[:400] + "..."
        lines.append(f"\n📝 {plot}")

    if eps:
        lines.append(f"\n📺 {eps['total_seasons']} فصل · {eps['total_episodes']} قسمت")

    return "\n".join(lines)


# ─── /start ─────────────────────────────────────────────────


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 به ربات سرچ و دانلود فیلم خوش اومدی!\n\n"
        "🔍 برای سرچ، از inline mode استفاده کن:\n"
        "  `@bot_name the drama`\n\n"
        "یا با /search عبارت رو بفرست:\n"
        "  `/search the drama`",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """سرچ با /search query"""
    if not ctx.args:
        await update.message.reply_text("⚠ لطفا عبارت جستجو رو بعد از /search بنویس.")
        return

    query = " ".join(ctx.args)
    msg = await update.message.reply_text(f"🔍 در حال جستجوی `{query}`...", parse_mode=ParseMode.MARKDOWN)

    results = await imdb_search.search_imdb(query, limit=10)
    if not results:
        await msg.edit_text("❌ نتیجه‌ای پیدا نشد.")
        return

    # دکمه‌های شیشه‌ای برای هر نتیجه
    keyboard = []
    for r in results:
        label = f"🎬 {r['title']}"
        if r.get("year"):
            label += f" ({r['year']})"
        kind = r.get("kind", "")
        if kind:
            label += f" [{kind}]"
        cb_data = f"sel:{r['imdb_id']}"
        keyboard.append([InlineKeyboardButton(label, callback_data=cb_data)])

    await msg.edit_text(
        f"🔍 نتایج جستجو برای `{query}`:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN,
    )


# ─── Inline query ───────────────────────────────────────────


async def inline_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """سرچ اینلاین: @bot query"""
    query = update.inline_query.query.strip()
    if not query or len(query) < 2:
        # راهنما
        results = [
            InlineQueryResultArticle(
                id="help",
                title="🔍 عبارت جستجو رو بنویس",
                description="مثلا: the drama",
                input_message_content=InputTextMessageContent(
                    "برای سرچ، عبارت رو بعد از نام ربات بنویس."
                ),
            )
        ]
        await update.inline_query.answer(results, cache_time=5)
        return

    # سرچ
    items = await imdb_search.search_imdb(query, limit=20)
    if not items:
        await update.inline_query.answer([
            InlineQueryResultArticle(
                id="empty",
                title="❌ نتیجه‌ای پیدا نشد",
                description=query,
                input_message_content=InputTextMessageContent(
                    f"❌ برای `{query}` نتیجه‌ای پیدا نشد.",
                    parse_mode=ParseMode.MARKDOWN,
                ),
            )
        ], cache_time=30)
        return

    # ساخت نتایج
    results = []
    for i, it in enumerate(items):
        title = it["title"]
        year = it.get("year", "")
        kind = it.get("kind", "")
        stars = it.get("stars", "")
        cover = it.get("cover", "")
        is_series = it.get("is_series", False)

        # عنوان و توضیح
        desc_parts = []
        if year:
            desc_parts.append(str(year))
        if kind:
            desc_parts.append(kind)
        if stars:
            desc_parts.append(stars)
        desc = " · ".join(desc_parts) if desc_parts else "—"

        # محتوای پیام وقتی انتخاب شد
        cb_data = f"sel:{it['imdb_id']}"
        message_text = (
            f"🎬 *{title}*\n"
            + (f"📅 {year}\n" if year else "")
            + (f"🎞 {kind}\n" if kind else "")
            + (f"👥 {stars}\n" if stars else "")
            + f"\n🔗 IMDB: https://www.imdb.com/title/{it['imdb_id']}/"
        )

        # thumbnail
        thumb_url = cover if cover else None
        thumb_width = 100
        thumb_height = 150

        results.append(
            InlineQueryResultArticle(
                id=it["imdb_id"],
                title=title,
                description=desc,
                thumb_url=thumb_url,
                thumb_width=thumb_width,
                thumb_height=thumb_height,
                input_message_content=InputTextMessageContent(
                    message_text,
                    parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=False,
                ),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "📥 دانلود" + (" 📺" if is_series else " 🎬"),
                        callback_data=cb_data,
                    )
                ]]),
            )
        )

    await update.inline_query.answer(results, cache_time=60)


# ─── Callback: select title ─────────────────────────────────


async def cb_select_title(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """وقتی کاربر یه title انتخاب میکنه - دانلود فوری برای فیلم، یا انتخاب فصل برای سریال"""
    q = update.callback_query
    await q.answer()
    data = q.data

    if not data.startswith("sel:"):
        return

    imdb_id = data[4:]
    user_id = q.from_user.id

    await q.edit_message_text("⏳ در حال دریافت اطلاعات...", reply_markup=None)

    # گرفتن اطلاعات title
    info = await imdb_search.get_title_info(imdb_id)
    if not info:
        await q.edit_message_text("❌ دریافت اطلاعات ناموفق بود.")
        return

    is_series = info.get("is_series", False)

    if is_series:
        # گرفتن لیست فصل‌ها
        eps = await imdb_search.get_tv_episodes(imdb_id)
        if not eps or not eps.get("seasons"):
            await q.edit_message_text("❌ اطلاعات فصل/قسمت در دسترس نیست.")
            return

        _user_state[user_id] = {"imdb_id": imdb_id, "info": info, "eps": eps}

        # دکمه‌های فصل - 2 ستون
        keyboard = []
        season_nums = sorted(eps["seasons"].keys(), key=lambda x: -x)
        row = []
        for s in season_nums:
            ep_count = len(eps["seasons"][s])
            row.append(InlineKeyboardButton(
                f"فصل {s} ({ep_count} قسمت)",
                callback_data=f"season:{s}",
            ))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        caption = _format_caption(info, eps)
        cover = info.get("cover")

        if cover:
            # ارسال کاور با caption و دکمه‌ها
            await q.message.delete()
            await ctx.bot.send_photo(
                chat_id=q.message.chat_id,
                photo=cover,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        else:
            await q.edit_message_text(
                caption,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN,
            )

    else:
        # فیلم - شروع دانلود
        _user_state[user_id] = {"imdb_id": imdb_id, "info": info}
        caption = _format_caption(info)
        cover = info.get("cover")

        # دکمه دانلود
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("📥 دانلود فیلم", callback_data=f"dlmovie:{imdb_id}"),
        ]])

        if cover:
            await q.message.delete()
            await ctx.bot.send_photo(
                chat_id=q.message.chat_id,
                photo=cover,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard,
            )
        else:
            await q.edit_message_text(
                caption,
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN,
            )


# ─── Callback: select season ────────────────────────────────


async def cb_select_season(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """وقتی کاربر فصل رو انتخاب میکنه"""
    q = update.callback_query
    await q.answer()
    data = q.data

    if not data.startswith("season:"):
        return

    season = int(data.split(":", 1)[1])
    user_id = q.from_user.id
    state = _user_state.get(user_id)
    if not state:
        await q.edit_message_text("❌ وضعیت شما منقضی شده. دوباره سرچ کنید.")
        return

    eps = state["eps"]
    if season not in eps["seasons"]:
        await q.answer("فصل نامعتبر", show_alert=True)
        return

    episodes = sorted(eps["seasons"][season])
    state["selected_season"] = season

    # دکمه‌های قسمت - 5 ستون
    keyboard = []
    row = []
    for ep in episodes:
        row.append(InlineKeyboardButton(
            f"{ep}",
            callback_data=f"episode:{season}:{ep}",
        ))
        if len(row) == 5:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    # دکمه برگشت
    keyboard.append([InlineKeyboardButton("↩ برگشت به فصل‌ها", callback_data="backseasons")])

    await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))


# ─── Callback: back to seasons ──────────────────────────────


async def cb_back_seasons(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """برگشت به لیست فصل‌ها"""
    q = update.callback_query
    await q.answer()
    user_id = q.from_user.id
    state = _user_state.get(user_id)
    if not state:
        await q.edit_message_text("❌ وضعیت شما منقضی شده.")
        return

    eps = state["eps"]
    keyboard = []
    season_nums = sorted(eps["seasons"].keys(), key=lambda x: -x)
    row = []
    for s in season_nums:
        ep_count = len(eps["seasons"][s])
        row.append(InlineKeyboardButton(
            f"فصل {s} ({ep_count} قسمت)",
            callback_data=f"season:{s}",
        ))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))


# ─── Callback: select episode -> download ───────────────────


async def cb_select_episode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """وقتی قسمت انتخاب شد - شروع دانلود"""
    q = update.callback_query
    await q.answer()
    data = q.data

    if not data.startswith("episode:"):
        return

    parts = data.split(":")
    season = int(parts[1])
    episode = int(parts[2])
    user_id = q.from_user.id
    state = _user_state.get(user_id)
    if not state:
        await q.edit_message_text("❌ وضعیت شما منقضی شده. دوباره سرچ کنید.")
        return

    imdb_id = state["imdb_id"]
    info = state["info"]
    title = info.get("title", "Unknown")

    await q.edit_message_text(
        f"⏳ شروع دانلود قسمت {season}×{episode} از *{title}*...\n"
        f"این کار ممکنه چند دقیقه طول بکشه.",
        parse_mode=ParseMode.MARKDOWN,
    )

    # شروع دانلود در بک‌گراند
    asyncio.create_task(_download_and_send(
        ctx, q.message.chat_id, imdb_id, title, season, episode, user_id,
    ))


# ─── Callback: download movie ───────────────────────────────


async def cb_download_movie(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """شروع دانلود فیلم"""
    q = update.callback_query
    await q.answer()
    data = q.data

    if not data.startswith("dlmovie:"):
        return

    imdb_id = data[8:]
    user_id = q.from_user.id
    state = _user_state.get(user_id)
    if not state:
        await q.edit_message_text("❌ وضعیت شما منقضی شده. دوباره سرچ کنید.")
        return

    info = state["info"]
    title = info.get("title", "Unknown")

    await q.edit_message_text(
        f"⏳ شروع دانلود فیلم *{title}*...\n"
        f"این کار ممکنه چند دقیقه طول بکشه.",
        parse_mode=ParseMode.MARKDOWN,
    )

    asyncio.create_task(_download_and_send(
        ctx, q.message.chat_id, imdb_id, title, None, None, user_id,
    ))


# ─── Download + send ────────────────────────────────────────


async def _download_and_send(
    ctx: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    imdb_id: str,
    title: str,
    season: Optional[int],
    episode: Optional[int],
    user_id: int,
):
    """دانلود ویدیو و آپلود به تلگرام"""
    out_dir = "/tmp/vidsrc_downloads"
    os.makedirs(out_dir, exist_ok=True)

    # progress message
    status_msg = await ctx.bot.send_message(
        chat_id=chat_id,
        text="📊 آماده‌سازی...",
    )

    last_progress = [0]
    def progress_cb(done, total):
        last_progress[0] = (done, total)

    # periodic update
    async def update_progress():
        while True:
            await asyncio.sleep(5)
            if last_progress[0]:
                d, t = last_progress[0]
                pct = d * 100 // t if t else 0
                try:
                    await status_msg.edit_text(f"📊 دانلود سگمنت‌ها: {d}/{t} ({pct}%)")
                except Exception:
                    pass

    updater = asyncio.create_task(update_progress())

    try:
        if season is not None:
            path = await vidsrc_downloader.download_episode(
                imdb_id, season, episode,
                out_dir=out_dir, quality="720p", progress_cb=progress_cb,
            )
        else:
            path = await vidsrc_downloader.download_movie(
                imdb_id, out_dir=out_dir, quality="720p", progress_cb=progress_cb,
            )

        updater.cancel()

        if not path or not os.path.exists(path):
            await status_msg.edit_text("❌ دانلود ناموفق بود.")
            return

        size_mb = os.path.getsize(path) / 1024 / 1024
        await status_msg.edit_text(f"📤 در حال آپلود ({size_mb:.1f} MB)...")
        await ctx.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)

        # caption
        if season is not None:
            caption = f"🎬 {title} - S{season:02d}E{episode:02d}"
        else:
            caption = f"🎬 {title}"

        # آپلود به تلگرام
        # نکته: تلگرام محدودیت 2GB برای فایل داره (با bot api)
        if size_mb > 1900:
            await status_msg.edit_text(
                f"⚠ فایل خیلی بزرگه ({size_mb:.1f} MB). تلگرام محدودیت 2GB داره."
            )
            return

        with open(path, "rb") as f:
            await ctx.bot.send_video(
                chat_id=chat_id,
                video=f,
                caption=caption,
                supports_streaming=True,
            )

        await status_msg.delete()

        # پاک کردن فایل
        try:
            os.unlink(path)
        except Exception:
            pass

    except Exception as e:
        logger.exception("download_and_send failed")
        updater.cancel()
        try:
            await status_msg.edit_text(f"❌ خطا: {e}")
        except Exception:
            pass


# ─── Register handlers ──────────────────────────────────────


def register_handlers(application: Application):
    """ثبت همه هندلرها روی Application"""
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("search", cmd_search))
    application.add_handler(InlineQueryHandler(inline_query))

    # callback handlers
    application.add_handler(CallbackQueryHandler(cb_select_title, pattern=r"^sel:"))
    application.add_handler(CallbackQueryHandler(cb_select_season, pattern=r"^season:"))
    application.add_handler(CallbackQueryHandler(cb_back_seasons, pattern=r"^backseasons"))
    application.add_handler(CallbackQueryHandler(cb_select_episode, pattern=r"^episode:"))
    application.add_handler(CallbackQueryHandler(cb_download_movie, pattern=r"^dlmovie:"))


# ─── Main entry ─────────────────────────────────────────────


def main():
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
    )
    if len(sys.argv) < 2:
        print("Usage: python telegram_handler.py <bot_token>")
        sys.exit(1)

    token = sys.argv[1]
    app = Application.builder().token(token).build()
    register_handlers(app)
    print("Bot starting... press Ctrl+C to stop")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
