"""
telethon_handler.py
───────────────────
هندلر سرچ و دانلود IMDB برای ربات تلگرام با Telethon.

ویژگی‌ها:
  - سرچ اینلاین: @bot query -> نتایج با کاور
  - انتخاب فیلم/سریال -> پیغام با caption + دکمه‌های شیشه‌ای
  - برای سریال: دکمه‌های فصل + قسمت
  - انتخاب کیفیت (Auto, 1080p, 720p, 480p, ...)
  - دانلود زیرنویس فارسی (اگه موجود باشه)
  - burn کردن subtitle با videotext.io
  - آپلود به تلگرام با مشخصات کامل

استفاده:
  python3 telethon_handler.py <api_id> <api_hash> <bot_token>
"""
import asyncio
import logging
import os
import re
import sys
from typing import Optional

from telethon import TelegramClient, events, Button
from telethon.tl.types import (
    InputMediaPhoto,
    InputPhoto,
    InputPeerUser,
    DocumentAttributeFilename,
)
from telethon.tl.functions.messages import SetInlineBotResultsRequest
from telethon.tl.types import (
    InputBotInlineMessageText,
    InputBotInlineResultPhoto,
    InputBotInlineResult,
    InputWebFileLocation,
)
from telethon import types

import imdb_search
import vidsrc_downloader
import vidsrc_extras
import videotext_burn

logger = logging.getLogger("TelethonHandler")

# ─── State management ───────────────────────────────────────
# نگهداری وضعیت کاربر در حین انتخاب
# user_id -> {"imdb_id": ..., "info": ..., "eps": ..., "season": ..., "quality": ...}

_user_state: dict = {}


# ─── Helpers ────────────────────────────────────────────────


def _safe_name(name: str, max_len: int = 80) -> str:
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    return name[:max_len].strip()


def _format_caption(info: dict, eps: Optional[dict] = None) -> str:
    """ساخت کپشن برای پیام فیلم/سریال"""
    title = info.get("title", "Unknown")
    year = info.get("year")
    end_year = info.get("end_year")
    title_type = info.get("title_type", "")
    plot = info.get("plot", "")

    lines = [f"🎬 **{title}**"]
    if year:
        if end_year:
            lines.append(f"📅 {year}–{end_year}")
        else:
            lines.append(f"📅 {year}")
    if title_type:
        lines.append(f"🎞 {title_type}")
    if plot:
        if len(plot) > 400:
            plot = plot[:400] + "..."
        lines.append(f"\n📝 {plot}")

    if eps:
        lines.append(f"\n📺 {eps['total_seasons']} فصل · {eps['total_episodes']} قسمت")

    return "\n".join(lines)


def _dl_caption(title: str, season: Optional[int], episode: Optional[int],
                subtitle_name: Optional[str], file_size_mb: float) -> str:
    """کپشن فایل نهایی ارسالی"""
    lines = []
    if season and episode:
        lines.append(f"🎬 **{title}** - S{season:02d}E{episode:02d}")
    else:
        lines.append(f"🎬 **{title}**")
    if subtitle_name:
        lines.append(f"📝 زیرنویس هاردکد: `{subtitle_name}`")
    lines.append(f"💾 حجم: {file_size_mb:.1f} MB")
    return "\n".join(lines)


# ─── /start ─────────────────────────────────────────────────


def register_handlers(client: TelegramClient):
    """ثبت همه هندلرها روی Telethon client"""

    @client.on(events.NewMessage(pattern=r"^/start"))
    async def cmd_start(event):
        await event.reply(
            "👋 به ربات سرچ و دانلود فیلم خوش اومدی!\n\n"
            "🔍 برای سرچ، از inline mode استفاده کن:\n"
            "  `@bot_username the drama`\n\n"
            "یا با /search عبارت رو بفرست:\n"
            "  `/search the drama`",
            parse_mode="md",
            link_preview=False,
        )

    @client.on(events.NewMessage(pattern=r"^/search\s+(.+)"))
    async def cmd_search(event):
        """سرچ با /search query"""
        query = event.pattern_match.group(1).strip()
        if not query:
            await event.reply("⚠ لطفا عبارت جستجو رو بنویس.")
            return

        msg = await event.reply(f"🔍 در حال جستجوی `{query}`...", parse_mode="md")

        results = await imdb_search.search_imdb(query, limit=10)
        if not results:
            await msg.edit("❌ نتیجه‌ای پیدا نشد.")
            return

        # دکمه‌های شیشه‌ای برای هر نتیجه
        buttons = []
        for r in results:
            label = f"🎬 {r['title']}"
            if r.get("year"):
                label += f" ({r['year']})"
            kind = r.get("kind", "")
            if kind:
                label += f" [{kind}]"
            buttons.append([Button.inline(label, data=f"sel:{r['imdb_id']}")])

        await msg.edit(
            f"🔍 نتایج جستجو برای `{query}`:",
            buttons=buttons,
            parse_mode="md",
        )

    # ─── Inline query ──────────────────────────────────────

    @client.on(events.InlineQuery())
    async def inline_handler(event):
        """سرچ اینلاین: @bot query"""
        query = event.text.strip()
        if not query or len(query) < 2:
            await event.answer([], switch_inline=("@bot_name عبارت جستجو", "Search movies..."))
            return

        items = await imdb_search.search_imdb(query, limit=20)
        if not items:
            await event.answer([], switch_inline=("No results", "Try another query"))
            return

        # ساخت نتایج inline
        results = []
        for it in items:
            title = it["title"]
            year = it.get("year", "")
            kind = it.get("kind", "")
            stars = it.get("stars", "")
            cover = it.get("cover", "")
            is_series = it.get("is_series", False)
            imdb_id = it["imdb_id"]

            # توضیح کوتاه
            desc_parts = []
            if year:
                desc_parts.append(str(year))
            if kind:
                desc_parts.append(kind)
            if stars:
                desc_parts.append(stars)
            description = " · ".join(desc_parts) if desc_parts else "—"

            message_text = (
                f"🎬 **{title}**\n"
                + (f"📅 {year}\n" if year else "")
                + (f"🎞 {kind}\n" if kind else "")
                + (f"👥 {stars}\n" if stars else "")
                + f"\n🔗 IMDB: https://www.imdb.com/title/{imdb_id}/"
            )

            # دکمه دانلود
            buttons = [Button.inline(
                "📥 دانلود" + (" 📺" if is_series else " 🎬"),
                data=f"sel:{imdb_id}",
            )]

            # اگه کاور داره، یه result از نوع photo بساز
            if cover:
                try:
                    # با thumbnail
                    result = event.builder.photo(
                        file=cover,
                        text=message_text,
                        buttons=buttons,
                        parse_mode="md",
                        link_preview=False,
                    )
                except Exception:
                    # fallback به article
                    result = event.builder.article(
                        title=title,
                        description=description,
                        text=message_text,
                        buttons=buttons,
                        parse_mode="md",
                        link_preview=False,
                    )
            else:
                result = event.builder.article(
                    title=title,
                    description=description,
                    text=message_text,
                    buttons=buttons,
                    parse_mode="md",
                    link_preview=False,
                )
            results.append(result)

        await event.answer(results, gallery=False)

    # ─── Callback: select title ────────────────────────────

    @client.on(events.CallbackQuery(pattern=b"^sel:"))
    async def cb_select_title(event):
        """وقتی کاربر یه title انتخاب میکنه"""
        data = event.data.decode()
        imdb_id = data[4:]
        user_id = event.sender_id

        await event.edit("⏳ در حال دریافت اطلاعات...", buttons=None)

        info = await imdb_search.get_title_info(imdb_id)
        if not info:
            await event.edit("❌ دریافت اطلاعات ناموفق بود.")
            return

        is_series = info.get("is_series", False)

        if is_series:
            eps = await imdb_search.get_tv_episodes(imdb_id)
            if not eps or not eps.get("seasons"):
                await event.edit("❌ اطلاعات فصل/قسمت در دسترس نیست.")
                return

            _user_state[user_id] = {"imdb_id": imdb_id, "info": info, "eps": eps}

            # دکمه‌های فصل - 2 ستون
            buttons = []
            season_nums = sorted(eps["seasons"].keys(), key=lambda x: -x)
            row = []
            for s in season_nums:
                ep_count = len(eps["seasons"][s])
                row.append(Button.inline(
                    f"فصل {s} ({ep_count} ق)",
                    data=f"season:{s}".encode(),
                ))
                if len(row) == 2:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)

            caption = _format_caption(info, eps)
            cover = info.get("cover")

            if cover:
                try:
                    await event.delete()
                    await event.respond(
                        cover,
                        text=caption,
                        parse_mode="md",
                        buttons=buttons,
                    )
                    return
                except Exception:
                    pass
            await event.edit(caption, buttons=buttons, parse_mode="md")

        else:
            # فیلم - حالت انتخاب کیفیت
            _user_state[user_id] = {"imdb_id": imdb_id, "info": info}
            caption = _format_caption(info)
            cover = info.get("cover")

            await event.edit(f"{caption}\n\n⏳ در حال گرفتن لیست کیفیت‌ها...", parse_mode="md")

            # گرفتن لیست کیفیت‌ها
            qualities = await vidsrc_extras.get_qualities(imdb_id)
            if not qualities:
                await event.edit(f"{caption}\n\n❌ کیفیت‌ها در دسترس نیست.", parse_mode="md")
                return

            # دکمه‌های کیفیت
            q_buttons = []
            row = []
            for q in qualities:
                label = q["label"]
                if q["resolution"]:
                    label += f" ({q['resolution']})"
                row.append(Button.inline(label, data=f"q:{q['label']}".encode()))
                if len(row) == 2:
                    q_buttons.append(row)
                    row = []
            if row:
                q_buttons.append(row)

            # دکمه skip subtitle
            q_buttons.append([Button.inline("⏭ بدون زیرنویس", data=b"nosub")])

            # ذخیره کیفیت‌ها در state
            _user_state[user_id]["qualities"] = qualities

            if cover:
                try:
                    await event.delete()
                    await event.respond(
                        cover,
                        text=f"{caption}\n\n🎯 کیفیت رو انتخاب کن:",
                        parse_mode="md",
                        buttons=q_buttons,
                    )
                    return
                except Exception:
                    pass
            await event.edit(f"{caption}\n\n🎯 کیفیت رو انتخاب کن:", buttons=q_buttons, parse_mode="md")

    # ─── Callback: select season ───────────────────────────

    @client.on(events.CallbackQuery(pattern=b"^season:"))
    async def cb_select_season(event):
        data = event.data.decode()
        season = int(data.split(":", 1)[1])
        user_id = event.sender_id
        state = _user_state.get(user_id)
        if not state:
            await event.answer("وضعیت شما منقضی شده. دوباره سرچ کنید.", alert=True)
            return

        eps = state["eps"]
        if season not in eps["seasons"]:
            await event.answer("فصل نامعتبر", alert=True)
            return

        episodes = sorted(eps["seasons"][season])
        state["selected_season"] = season

        # دکمه‌های قسمت - 5 ستون
        buttons = []
        row = []
        for ep in episodes:
            row.append(Button.inline(f"{ep}", data=f"episode:{season}:{ep}".encode()))
            if len(row) == 5:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        # دکمه برگشت
        buttons.append([Button.inline("↩ برگشت به فصل‌ها", data=b"backseasons")])

        await event.edit(
            f"📺 فصل {season} - یکی از قسمت‌ها رو انتخاب کن:",
            buttons=buttons,
        )

    @client.on(events.CallbackQuery(pattern=b"^backseasons"))
    async def cb_back_seasons(event):
        user_id = event.sender_id
        state = _user_state.get(user_id)
        if not state:
            await event.answer("وضعیت شما منقضی شده.", alert=True)
            return

        eps = state["eps"]
        buttons = []
        season_nums = sorted(eps["seasons"].keys(), key=lambda x: -x)
        row = []
        for s in season_nums:
            ep_count = len(eps["seasons"][s])
            row.append(Button.inline(
                f"فصل {s} ({ep_count} ق)",
                data=f"season:{s}".encode(),
            ))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        await event.edit("📺 یکی از فصل‌ها رو انتخاب کن:", buttons=buttons)

    # ─── Callback: select episode -> fetch qualities ───────

    @client.on(events.CallbackQuery(pattern=b"^episode:"))
    async def cb_select_episode(event):
        data = event.data.decode()
        parts = data.split(":")
        season = int(parts[1])
        episode = int(parts[2])
        user_id = event.sender_id
        state = _user_state.get(user_id)
        if not state:
            await event.answer("وضعیت شما منقضی شده.", alert=True)
            return

        imdb_id = state["imdb_id"]
        info = state["info"]
        title = info.get("title", "Unknown")

        state["selected_season"] = season
        state["selected_episode"] = episode

        await event.edit(
            f"🎬 **{title}** - S{season:02d}E{episode:02d}\n\n⏳ در حال گرفتن لیست کیفیت‌ها...",
            parse_mode="md",
        )

        # گرفتن کیفیت‌ها برای این قسمت
        qualities = await vidsrc_extras.get_qualities(imdb_id, season, episode)
        if not qualities:
            await event.edit("❌ کیفیت‌ها در دسترس نیست.")
            return

        # دکمه‌های کیفیت
        q_buttons = []
        row = []
        for q in qualities:
            label = q["label"]
            if q["resolution"]:
                label += f" ({q['resolution']})"
            row.append(Button.inline(label, data=f"eq:{q['label']}".encode()))
            if len(row) == 2:
                q_buttons.append(row)
                row = []
        if row:
            q_buttons.append(row)
        # دکمه skip subtitle
        q_buttons.append([Button.inline("⏭ بدون زیرنویس", data=b"enosub")])

        state["qualities"] = qualities
        await event.edit(
            f"🎬 **{title}** - S{season:02d}E{episode:02d}\n\n🎯 کیفیت رو انتخاب کن:",
            buttons=q_buttons,
            parse_mode="md",
        )

    # ─── Callback: select quality for movie ────────────────

    @client.on(events.CallbackQuery(pattern=b"^q:"))
    async def cb_select_quality_movie(event):
        data = event.data.decode()
        quality_label = data[2:]
        user_id = event.sender_id
        state = _user_state.get(user_id)
        if not state:
            await event.answer("وضعیت شما منقضی شده.", alert=True)
            return

        state["quality"] = quality_label
        imdb_id = state["imdb_id"]

        # حالا دکمه‌های subtitle
        await event.edit(
            f"✅ کیفیت: **{quality_label}**\n\n📝 زیرنویس فارسی:\n"
            f"⏳ در حال جستجوی زیرنویس...",
            parse_mode="md",
        )

        # جستجوی subtitle فارسی
        subs = await vidsrc_extras.search_subtitles(imdb_id, "per")
        state["subs"] = subs

        sub_buttons = []
        if subs:
            for i, s in enumerate(subs[:5]):
                label = f"📄 {s['file_name'][:40]} (↓{s['downloads']})"
                sub_buttons.append([Button.inline(label, data=f"sub:{i}".encode())])

        sub_buttons.append([Button.inline("⏭ بدون زیرنویس", data=b"nosub")])

        await event.edit(
            f"✅ کیفیت: **{quality_label}**\n\n📝 زیرنویس فارسی:\n"
            + (f"📄 {len(subs)} زیرنویس پیدا شد:" if subs else "❌ زیرنویسی پیدا نشد"),
            buttons=sub_buttons,
            parse_mode="md",
        )

    # ─── Callback: select quality for episode ──────────────

    @client.on(events.CallbackQuery(pattern=b"^eq:"))
    async def cb_select_quality_episode(event):
        data = event.data.decode()
        quality_label = data[3:]
        user_id = event.sender_id
        state = _user_state.get(user_id)
        if not state:
            await event.answer("وضعیت شما منقضی شده.", alert=True)
            return

        state["quality"] = quality_label
        imdb_id = state["imdb_id"]
        season = state["selected_season"]
        episode = state["selected_episode"]

        await event.edit(
            f"✅ کیفیت: **{quality_label}**\n\n📝 زیرنویس فارسی:\n"
            f"⏳ در حال جستجوی زیرنویس...",
            parse_mode="md",
        )

        # جستجوی subtitle برای قسمت
        subs = await vidsrc_extras.search_subtitles(imdb_id, "per", season, episode)
        state["subs"] = subs

        sub_buttons = []
        if subs:
            for i, s in enumerate(subs[:5]):
                label = f"📄 {s['file_name'][:40]} (↓{s['downloads']})"
                sub_buttons.append([Button.inline(label, data=f"esub:{i}".encode())])

        sub_buttons.append([Button.inline("⏭ بدون زیرنویس", data=b"enosub")])

        await event.edit(
            f"✅ کیفیت: **{quality_label}**\n\n📝 زیرنویس فارسی:\n"
            + (f"📄 {len(subs)} زیرنویس پیدا شد:" if subs else "❌ زیرنویسی پیدا نشد"),
            buttons=sub_buttons,
            parse_mode="md",
        )

    # ─── Callback: select subtitle (movie) ─────────────────

    @client.on(events.CallbackQuery(pattern=b"^sub:"))
    async def cb_select_sub_movie(event):
        data = event.data.decode()
        sub_idx = int(data[4:])
        user_id = event.sender_id
        state = _user_state.get(user_id)
        if not state:
            await event.answer("وضعیت شما منقضی شده.", alert=True)
            return

        sub = state["subs"][sub_idx]
        state["selected_sub"] = sub

        # شروع دانلود
        asyncio.create_task(_download_and_send_movie(
            event, state, with_subtitle=True,
        ))

    # ─── Callback: select subtitle (episode) ───────────────

    @client.on(events.CallbackQuery(pattern=b"^esub:"))
    async def cb_select_sub_episode(event):
        data = event.data.decode()
        sub_idx = int(data[5:])
        user_id = event.sender_id
        state = _user_state.get(user_id)
        if not state:
            await event.answer("وضعیت شما منقضی شده.", alert=True)
            return

        sub = state["subs"][sub_idx]
        state["selected_sub"] = sub

        asyncio.create_task(_download_and_send_episode(
            event, state, with_subtitle=True,
        ))

    # ─── Callback: no subtitle (movie) ─────────────────────

    @client.on(events.CallbackQuery(pattern=b"^nosub"))
    async def cb_no_sub_movie(event):
        user_id = event.sender_id
        state = _user_state.get(user_id)
        if not state:
            await event.answer("وضعیت شما منقضی شده.", alert=True)
            return

        asyncio.create_task(_download_and_send_movie(
            event, state, with_subtitle=False,
        ))

    # ─── Callback: no subtitle (episode) ───────────────────

    @client.on(events.CallbackQuery(pattern=b"^enosub"))
    async def cb_no_sub_episode(event):
        user_id = event.sender_id
        state = _user_state.get(user_id)
        if not state:
            await event.answer("وضعیت شما منقضی شده.", alert=True)
            return

        asyncio.create_task(_download_and_send_episode(
            event, state, with_subtitle=False,
        ))


# ─── Download + send (background tasks) ─────────────────────


async def _download_and_send_movie(event, state, with_subtitle: bool):
    """دانلود فیلم + subtitle + burn + ارسال"""
    imdb_id = state["imdb_id"]
    info = state["info"]
    title = info.get("title", "Unknown")
    quality = state.get("quality", "Auto")

    out_dir = "/tmp/vidsrc_dl"
    os.makedirs(out_dir, exist_ok=True)

    status_msg = await event.respond("📊 آماده‌سازی...")

    try:
        # 1. دانلود ویدیو
        await status_msg.edit(f"📥 دانلود ویدیو با کیفیت {quality}...")

        last_progress = [0]
        def vid_progress(done, total):
            last_progress[0] = (done, total)

        async def update_vid():
            while True:
                await asyncio.sleep(5)
                if last_progress[0]:
                    d, t = last_progress[0]
                    pct = d * 100 // t if t else 0
                    try:
                        await status_msg.edit(f"📥 دانلود سگمنت: {d}/{t} ({pct}%)")
                    except Exception:
                        pass

        updater = asyncio.create_task(update_vid())

        video_path = await vidsrc_extras.download_with_quality(
            imdb_id, quality, out_dir,
        )
        updater.cancel()

        if not video_path or not os.path.exists(video_path):
            await status_msg.edit("❌ دانلود ویدیو ناموفق بود.")
            return

        vid_size = os.path.getsize(video_path) / 1024 / 1024
        await status_msg.edit(f"✅ ویدیو دانلود شد ({vid_size:.1f} MB)")

        sub_path = None
        sub_name = None

        if with_subtitle and state.get("selected_sub"):
            # 2. دانلود subtitle
            await status_msg.edit("📝 دانلود زیرنویس...")
            sub_path = await vidsrc_extras.download_subtitle(
                state["selected_sub"], out_dir,
            )
            if sub_path:
                sub_name = state["selected_sub"].get("file_name", "")
                await status_msg.edit(f"✅ زیرنویس: `{sub_name}`", parse_mode="md")
            else:
                await status_msg.edit("⚠ زیرنویس دانلود نشد، بدون burn ادامه میدیم.")
                with_subtitle = False

        final_path = video_path
        if with_subtitle and sub_path:
            # 3. burn subtitle با videotext.io
            await status_msg.edit("🔥 در حال burn زیرنویس...\n⏳ این مرحله چند دقیقه طول میکشه.")

            burn_progress = [0, ""]
            def on_burn(s):
                burn_progress[0] = s.progress
                burn_progress[1] = s.status

            async def update_burn():
                while True:
                    await asyncio.sleep(3)
                    p, st = burn_progress
                    msg = f"🔥 burn: {st} {p}%"
                    try:
                        await status_msg.edit(msg)
                    except Exception:
                        pass

            updater = asyncio.create_task(update_burn())

            final_path = await videotext_burn.burn_subtitles(
                video_path=video_path,
                subtitle_path=sub_path,
                out_dir=out_dir,
                on_burn_progress=on_burn,
            )
            updater.cancel()

            if not final_path or not os.path.exists(final_path):
                await status_msg.edit("❌ burn ناموفق بود. ویدیوی بدون subtitle ارسال میشه.")
                final_path = video_path
            else:
                await status_msg.edit("✅ burn کامل شد!")

        # 4. آپلود به تلگرام
        file_size = os.path.getsize(final_path)
        size_mb = file_size / 1024 / 1024

        if size_mb > 1900:
            await status_msg.edit(f"⚠ فایل خیلی بزرگه ({size_mb:.1f} MB). محدودیت تلگرام 2GB.")
            return

        await status_msg.edit(f"📤 در حال آپلود ({size_mb:.1f} MB)...")
        caption = _dl_caption(title, None, None, sub_name if with_subtitle else None, size_mb)

        # thumbnail از info
        thumb = info.get("cover")

        await event.respond(
            file=final_path,
            caption=caption,
            parse_mode="md",
            thumb=thumb if thumb else None,
            supports_streaming=True,
        )

        await status_msg.delete()

        # پاک کردن فایل‌های موقت
        for p in [video_path, sub_path, final_path]:
            if p and p != video_path:  # اگه final_path == video_path نباشه پاک کن
                try:
                    os.unlink(p)
                except Exception:
                    pass
        try:
            os.unlink(video_path)
        except Exception:
            pass

    except Exception as e:
        logger.exception("download_and_send_movie failed")
        try:
            await status_msg.edit(f"❌ خطا: {e}")
        except Exception:
            pass


async def _download_and_send_episode(event, state, with_subtitle: bool):
    """دانلود قسمت سریال + subtitle + burn + ارسال"""
    imdb_id = state["imdb_id"]
    info = state["info"]
    title = info.get("title", "Unknown")
    quality = state.get("quality", "Auto")
    season = state["selected_season"]
    episode = state["selected_episode"]

    out_dir = "/tmp/vidsrc_dl"
    os.makedirs(out_dir, exist_ok=True)

    status_msg = await event.respond("📊 آماده‌سازی...")

    try:
        await status_msg.edit(f"📥 دانلود S{season:02d}E{episode:02d} با کیفیت {quality}...")

        last_progress = [0]
        def vid_progress(done, total):
            last_progress[0] = (done, total)

        async def update_vid():
            while True:
                await asyncio.sleep(5)
                if last_progress[0]:
                    d, t = last_progress[0]
                    pct = d * 100 // t if t else 0
                    try:
                        await status_msg.edit(f"📥 دانلود سگمنت: {d}/{t} ({pct}%)")
                    except Exception:
                        pass

        updater = asyncio.create_task(update_vid())

        video_path = await vidsrc_extras.download_with_quality(
            imdb_id, quality, out_dir, season, episode,
        )
        updater.cancel()

        if not video_path or not os.path.exists(video_path):
            await status_msg.edit("❌ دانلود ویدیو ناموفق بود.")
            return

        vid_size = os.path.getsize(video_path) / 1024 / 1024
        await status_msg.edit(f"✅ ویدیو دانلود شد ({vid_size:.1f} MB)")

        sub_path = None
        sub_name = None

        if with_subtitle and state.get("selected_sub"):
            await status_msg.edit("📝 دانلود زیرنویس...")
            sub_path = await vidsrc_extras.download_subtitle(
                state["selected_sub"], out_dir,
            )
            if sub_path:
                sub_name = state["selected_sub"].get("file_name", "")
                await status_msg.edit(f"✅ زیرنویس: `{sub_name}`", parse_mode="md")
            else:
                await status_msg.edit("⚠ زیرنویس دانلود نشد، بدون burn ادامه میدیم.")
                with_subtitle = False

        final_path = video_path
        if with_subtitle and sub_path:
            await status_msg.edit("🔥 در حال burn زیرنویس...")

            burn_progress = [0, ""]
            def on_burn(s):
                burn_progress[0] = s.progress
                burn_progress[1] = s.status

            async def update_burn():
                while True:
                    await asyncio.sleep(3)
                    p, st = burn_progress
                    try:
                        await status_msg.edit(f"🔥 burn: {st} {p}%")
                    except Exception:
                        pass

            updater = asyncio.create_task(update_burn())

            final_path = await videotext_burn.burn_subtitles(
                video_path=video_path,
                subtitle_path=sub_path,
                out_dir=out_dir,
                on_burn_progress=on_burn,
            )
            updater.cancel()

            if not final_path or not os.path.exists(final_path):
                await status_msg.edit("❌ burn ناموفق بود. ویدیوی بدون subtitle ارسال میشه.")
                final_path = video_path
            else:
                await status_msg.edit("✅ burn کامل شد!")

        file_size = os.path.getsize(final_path)
        size_mb = file_size / 1024 / 1024

        if size_mb > 1900:
            await status_msg.edit(f"⚠ فایل خیلی بزرگه ({size_mb:.1f} MB). محدودیت تلگرام 2GB.")
            return

        await status_msg.edit(f"📤 در حال آپلود ({size_mb:.1f} MB)...")
        caption = _dl_caption(title, season, episode, sub_name if with_subtitle else None, size_mb)

        thumb = info.get("cover")
        await event.respond(
            file=final_path,
            caption=caption,
            parse_mode="md",
            thumb=thumb if thumb else None,
            supports_streaming=True,
        )

        await status_msg.delete()

        # پاک کردن
        for p in [video_path, sub_path, final_path]:
            if p and p != video_path:
                try:
                    os.unlink(p)
                except Exception:
                    pass
        try:
            os.unlink(video_path)
        except Exception:
            pass

    except Exception as e:
        logger.exception("download_and_send_episode failed")
        try:
            await status_msg.edit(f"❌ خطا: {e}")
        except Exception:
            pass


# ─── Main entry ─────────────────────────────────────────────


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
    )

    if len(sys.argv) < 4:
        print("Usage: python3 telethon_handler.py <api_id> <api_hash> <bot_token>")
        sys.exit(1)

    api_id = int(sys.argv[1])
    api_hash = sys.argv[2]
    bot_token = sys.argv[3]

    client = TelegramClient(
        "vidsrc_bot",
        api_id,
        api_hash,
    )
    client.start(bot_token=bot_token)
    register_handlers(client)

    print("Bot started. Press Ctrl+C to stop.")
    client.run_until_disconnected()


if __name__ == "__main__":
    main()
