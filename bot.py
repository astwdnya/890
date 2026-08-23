#!/usr/bin/env python3
# Telegram Ultimate Bot - v5
# Fixes: 403 auto-dirpy + FFmpeg scale/rotation fix + size_input chat_id fix + pause/resume split

import asyncio
import glob
import math
import os
import re
import sys
import logging
import time
import json
import shutil
import uuid
from urllib.parse import quote
from typing import Optional, Tuple, Dict

from flask import Flask
from threading import Thread

import aiohttp
import aiofiles
import base64
import gc
from aiohttp import ClientTimeout


from playwright.async_api import async_playwright

from telethon import TelegramClient, events, Button, utils
from telethon.errors import FloodWaitError
from telethon.tl import types as tl_types
from telethon.tl.types import (
    Message,
    DocumentAttributeVideo,
    DocumentAttributeAudio,
    InputMediaUploadedDocument,
    InputWebDocument,
    DocumentAttributeImageSize,
)
from FastTelethon import upload_file as fast_upload_file
from github import (
    upload_to_github,
    github_configured,
    GITHUB_MAX_MB,
    GITHUB_REPO,
    GITHUB_BRANCH,
    GITHUB_BASE_DIR,
)
from savep_handler import process_savep_request, trigger_savep_cancel
from xnxx_handler import (
    is_xnxx_url,
    extract_xnxx_qualities,
    download_xnxx_direct,
    download_xnxx_m3u8,
    xnxx_sessions,
)
from searcher.xnxx_search import search_xnxx, search_xnxx_multi_page, parse_inline_query
from searcher.pornhub_search import search_pornhub, search_pornhub_multi_page
from searcher.xvideos_search import search_xvideos, search_xvideos_multi_page
from searcher.eporner_search import search_eporner
from searcher.whoreshub_search import (
    search_whoreshub,
    search_whoreshub_multi_page,
    parse_inline_query as parse_wh_inline_query,
)
import sys as _sys
import os as _os
_searcher_imdb_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "searcher", "imdb")
if _searcher_imdb_dir not in _sys.path:
    _sys.path.insert(0, _searcher_imdb_dir)
from searcher.imdb.imdb_search import search_imdb, get_title_info, get_tv_episodes
from searcher.imdb.vidsrc_extras import get_qualities, search_subtitles, download_subtitle, download_with_quality, get_persian_subtitle, get_server_info, embed_subtitle_soft
# diycraft handler
from otherwebsiteshandler.diycraft_handler import is_diycraft_url, extract_video_info, extract_episode_video, download_video as diycraft_download
# sarrast handler (Persian adult visual stories)
from otherwebsiteshandler.sarrast_handler import is_sarrast_url

# Iran server (doostihaa + farsiland)
IRAN_SERVER = os.getenv('IRAN_SERVER', 'doostihaa')  # doostihaa or farsiland
dc_states = {}  # diycraft states
iran_states = {}
sr_states = {}  # sarrast states (key: "{chat_id}_{msg_id}")
user_iran_server = {}  # user_id -> 'doostihaa' or 'farsiland'
from searcher.imdb.videotext_burn import burn_subtitles
from ytdlp_handler import (
    is_ytdlp_site_url,
    extract_qualities_ytdlp,
    download_with_ytdlp,
    ytdlp_sessions,
    is_xhamster_url,
    is_pornhub_url,
    is_inxxx_url,
    is_hentaiheaven_url,
    is_tube8_url,
    is_pornhat_url,
    get_site_name,
)
from snapwc_handler import SnapWCSession
from otherwebsiteshandler.xvideos_handler import (
    is_xvideos_url,
    extract_xvideos_qualities,
    download_xvideos_direct,
    download_xvideos_m3u8,
    xvideos_sessions,
)
from otherwebsiteshandler.xgroovy_handler import (
    is_xgroovy_url,
    extract_xgroovy_qualities,
    download_xgroovy_direct,
    download_xgroovy_m3u8,
    xgroovy_sessions,
)
from otherwebsiteshandler.TeenSexVideos_handler import (
    is_teensexvideos_url,
    extract_teensexvideos_qualities,
    download_teensexvideos_direct,
    download_teensexvideos_m3u8,
    teensexvideos_sessions,
)
from otherwebsiteshandler.usersporn_handler import (
    is_usersporn_url,
    extract_usersporn_qualities,
    download_usersporn_direct,
    download_usersporn_m3u8,
    usersporn_sessions,
)
from otherwebsiteshandler.hentaihaven_handler import (
    is_hentaihaven_url,
    extract_hentaihaven_qualities,
    download_hentaihaven_direct,
    download_hentaihaven_m3u8,
    hentaihaven_sessions,
)
from otherwebsiteshandler.rat_handler import (
    is_rat_url,
    extract_rat_qualities,
    download_rat_direct,
    download_rat_m3u8,
    rat_sessions,
)
from otherwebsiteshandler.youporn_handler import (
    is_youporn_url,
    extract_youporn_qualities,
    download_youporn_direct,
    download_youporn_m3u8,
)
from otherwebsiteshandler.sexvid_handler import (
    is_sexvid_url,
    extract_sexvid_qualities,
    download_sexvid_direct,
    download_sexvid_m3u8,
    sexvid_sessions,
)
from otherwebsiteshandler.tube8_handler import (
    is_tube8_url,
    extract_tube8_qualities,
    download_tube8_direct,
    download_tube8_m3u8,
    tube8_sessions,
)
from otherwebsiteshandler.redtube_handler import (
    is_redtube_url,
    extract_redtube_qualities,
    download_redtube_direct,
    download_redtube_m3u8,
    redtube_sessions,
)
from otherwebsiteshandler.hohoj_handler import (
    is_hohoj_url,
    extract_hohoj_qualities,
    download_hohoj_direct,
    download_hohoj_m3u8,
)
from otherwebsiteshandler.porna91_handler import (
    is_91porna_url,
    extract_91porna_qualities,
    download_91porna_direct,
    download_91porna_m3u8,
)
from otherwebsiteshandler.playvids_handler import (
    is_playvids_url,
    extract_playvids_qualities,
    download_playvids_direct,
    download_playvids_m3u8,
)
from otherwebsiteshandler.porn300_handler import (
    is_porn300_url,
    extract_porn300_qualities,
    download_porn300_direct,
    download_porn300_m3u8,
)
from otherwebsiteshandler.tnaflix_handler import (
    is_tnaflix_url,
    extract_tnaflix_qualities,
    download_tnaflix_direct,
    download_tnaflix_m3u8,
)
from otherwebsiteshandler.eporner_handler import (
    is_eporner_url,
    extract_eporner_qualities,
    download_eporner_direct,
    download_eporner_m3u8,
)
from otherwebsiteshandler.pornzog_handler import (
    is_pornzog_url,
    extract_pornzog_qualities,
    download_pornzog_direct,
    download_pornzog_m3u8,
)
from otherwebsiteshandler.rule34_handler import (
    is_rule34_url,
    extract_rule34_post,
    download_rule34,
)
from otherwebsiteshandler.pornhub_handler import (
    is_pornhub_url as is_pornhub_handler_url,
    extract_pornhub_qualities,
    download_pornhub_video,
    pornhub_sessions,
)
from otherwebsiteshandler.cartoonporn_handler import (
    is_cartoonporn_url,
    extract_cartoonporn_qualities,
    download_cartoonporn_video,
    cartoonporn_sessions,
)
from otherwebsiteshandler.rule34video_handler import (
    is_rule34video_url,
    extract_rule34video_qualities,
    download_rule34video,
)
from otherwebsiteshandler.xanimu_handler import (
    is_xanimu_url,
    extract_xanimu_qualities,
    download_xanimu_video,
)
from otherwebsiteshandler.porntrex_handler import (
    is_porntrex_url,
    extract_porntrex_qualities,
    download_porntrex_video,
)
from otherwebsiteshandler.heavyr_handler import (
    is_heavyr_url,
    extract_heavyr_qualities,
    download_heavyr_video,
)
from otherwebsiteshandler.wonporn_handler import (
    is_wonporn_url,
    extract_wonporn_qualities,
    download_wonporn_video,
)
from otherwebsiteshandler.leaksextape_handler import (
    is_leaksextape_url,
    extract_leaksextape_qualities,
    download_leaksextape_video,
)
from otherwebsiteshandler.xxxpublicpornvideos_handler import (
    is_xxxpublicpornvideos_url,
    extract_xxxpublicpornvideos_qualities,
    download_xxxpublicpornvideos_video,
)
from otherwebsiteshandler.cartoonporn_com_handler import (
    is_cartoonporn_url as is_cartoonporncom_url,
    extract_cartoonporn_qualities as extract_cartoonporncom_qualities,
    download_cartoonporn_video as download_cartoonporncom_video,
)
from otherwebsiteshandler.hihentaiporn_handler import (
    is_hihentaiporn_url,
    extract_hihentaiporn_qualities,
    download_hihentaiporn_video,
)
from otherwebsiteshandler.fetishshrine_handler import (
    is_fetishshrine_url,
    extract_fetishshrine_qualities,
    download_fetishshrine_video,
)
from otherwebsiteshandler.bigfuck_handler import (
    is_bigfuck_url,
    extract_bigfuck_qualities,
    download_bigfuck_video,
)
from otherwebsiteshandler.babestube_handler import (
    is_babestube_url,
    extract_babestube_qualities,
    download_babestube_video,
)
from otherwebsiteshandler.pornwhite_handler import (
    is_pornwhite_url,
    extract_pornwhite_qualities,
    download_pornwhite_direct,
)
from otherwebsiteshandler.porndroids_handler import (
    is_porndroids_url,
    extract_porndroids_qualities,
    download_porndroids_direct,
)
from otherwebsiteshandler.xxxbp_handler import (
    is_xxxbp_url,
    extract_xxxbp_qualities,
    download_xxxbp_direct,
    cancel_download,
    clear_download_state,
)
from otherwebsiteshandler.sexxxx_handler import (
    is_sexxxx_url,
    extract_sexxxx_qualities,
    download_sexxxx_direct,
)
from otherwebsiteshandler.elliniko_handler import (
    is_elliniko_url,
    extract_elliniko_qualities,
    download_elliniko_direct,
)
from otherwebsiteshandler.rapelust_handler import (
    is_rapelust_url,
    extract_rapelust_qualities,
    download_rapelust_direct,
)
from otherwebsiteshandler.rapeinass_handler import (
    is_rapeinass_url,
    extract_rapeinass_qualities,
    download_rapeinass_direct,
)
from otherwebsiteshandler.forcedlove_handler import (
    is_forcedlove_url,
    extract_forcedlove_qualities,
    download_forcedlove_direct,
)
from otherwebsiteshandler.rapedws_handler import (
    is_rapedws_url,
    extract_rapedws_qualities,
    download_rapedws_direct,
)
from otherwebsiteshandler.sextvx_handler import (
    is_sextvx_url,
    extract_sextvx_qualities,
    download_sextvx_direct,
)
from otherwebsiteshandler.porndos_handler import (
    is_porndos_url,
    extract_porndos_qualities,
    download_porndos_direct,
)
from otherwebsiteshandler.shahvani_handler import (
    is_shahvani_url,
    extract_shahvani_qualities,
    download_shahvani_direct,
)
from otherwebsiteshandler.deviants_handler import (
    is_deviants_url,
    extract_deviants_qualities,
    download_deviants_direct,
)
from otherwebsiteshandler.xxxvids_handler import (
    is_xxxvids_url,
    extract_xxxvids_qualities,
    download_xxxvids_direct,
)
from otherwebsiteshandler.mutterfickt_handler import (
    is_mutterfickt_url,
    extract_mutterfickt_qualities,
    download_mutterfickt_direct,
)
from otherwebsiteshandler.rulexporn_handler import (
    is_rulexporn_url,
    extract_rulexporn_qualities,
    download_rulexporn_direct,
)
from otherwebsiteshandler.robbyporn_handler import (
    is_robbyporn_url,
    extract_robbyporn_qualities,
    download_robbyporn_direct,
)
from otherwebsiteshandler.bgxmonster_handler import (
    is_bgxmonster_url,
    extract_bgxmonster_qualities,
    download_bgxmonster_direct,
)
from otherwebsiteshandler.jebacina_handler import (
    is_jebacina_url,
    extract_jebacina_qualities,
    download_jebacina_direct,
)
from otherwebsiteshandler.ersties_handler import (
    is_ersties_url,
    extract_ersties_qualities,
    download_ersties_direct,
)
from otherwebsiteshandler.whoreshub_handler import (
    is_whoreshub_url,
    extract_whoreshub_qualities,
    download_whoreshub_direct,
)
from otherwebsiteshandler.xfetish_handler import (
    is_xfetish_url,
    extract_xfetish_qualities,
    download_xfetish_direct,
)
from otherwebsiteshandler.erome_handler import (
    is_erome_url,
    extract_erome_media,
    download_all_videos,
    download_all_photos,
    download_all_media,
    active_downloads as erome_active_downloads,
)
from otherwebsiteshandler.beeg_handler import (
    is_beeg_url,
    extract_beeg_qualities,
    download_beeg_direct,
)
from otherwebsiteshandler.spankbang_handler import (
    is_spankbang_url,
    extract_spankbang_qualities,
    download_spankbang_direct,
)
from otherwebsiteshandler.reddit_handler import (
    is_reddit_url,
    extract_reddit_qualities,
    download_reddit_direct,
)
from otherwebsiteshandler.ixxx_handler import (
    is_ixxx_url,
    extract_ixxx_qualities,
    download_ixxx_direct,
)
from otherwebsiteshandler.hdtube_handler import (
    is_hdtube_url,
    extract_hdtube_qualities,
    download_hdtube_direct,
)
from otherwebsiteshandler.sleazyneasy_handler import (
    is_sleazyneasy_url,
    extract_sleazyneasy_qualities,
    download_sleazyneasy_direct,
)
from otherwebsiteshandler.shameless_handler import (
    is_shameless_url,
    extract_shameless_qualities,
    download_shameless_direct,
)
from otherwebsiteshandler.hqporner_handler import (
    is_hqporner_url,
    extract_hqporner_qualities,
    download_hqporner_direct,
)
from otherwebsiteshandler.youjizz_handler import (
    is_youjizz_url,
    extract_youjizz_qualities,
    download_youjizz_direct,
)
from otherwebsiteshandler.severeporn_handler import (
    is_severeporn_url,
    extract_severeporn_qualities,
    download_severeporn_direct,
)
from otherwebsiteshandler.mat6tube_handler import (
    is_mat6tube_url,
    extract_mat6tube_qualities,
    download_mat6tube_direct,
)
from otherwebsiteshandler.peekvids_handler import (
    is_peekvids_url,
    extract_peekvids_qualities,
    download_peekvids_direct,
)
from otherwebsiteshandler.paradisehill_handler import (
    is_paradisehill_url,
    extract_paradisehill_qualities,
    download_paradisehill_direct,
)
from otherwebsiteshandler.sxyprn_handler import (
    is_sxyprn_url,
    extract_sxyprn_qualities,
    download_sxyprn_direct,
)
from otherwebsiteshandler.kick_handler import (
    is_kick_url,
    get_available_qualities,
    download_past,
)
from otherwebsiteshandler.luxuretv_handler import (
    is_luxuretv_url,
    extract_luxuretv_qualities,
    download_luxuretv_direct,
)
# ─── New site handlers (27 sites) ──────────────────────────
from otherwebsiteshandler.kvs_handler import (
    is_hellporno_url, is_alphaporno_url, is_bravoteens_url, is_bravotube_url,
    is_crocotube_url, is_porngo_url,
    extract_hellporno_qualities, extract_alphaporno_qualities,
    extract_bravoteens_qualities, extract_bravotube_qualities,
    extract_crocotube_qualities, extract_porngo_qualities,
    download_hellporno_direct, download_hellporno_m3u8,
    download_alphaporno_direct, download_alphaporno_m3u8,
    download_bravoteens_direct, download_bravoteens_m3u8,
    download_bravotube_direct, download_bravotube_m3u8,
    download_crocotube_direct, download_crocotube_m3u8,
    download_porngo_direct, download_porngo_m3u8,
    hellporno_sessions, alphaporno_sessions, bravoteens_sessions,
    bravotube_sessions, crocotube_sessions, porngo_sessions,
)
from otherwebsiteshandler.txxx_network_handler import (
    is_txxx_url, is_hclips_url, is_upornia_url, is_vjav_url, is_hdzog_url,
    extract_txxx_qualities, extract_hclips_qualities,
    extract_upornia_qualities, extract_vjav_qualities, extract_hdzog_qualities,
    download_txxx_direct, download_txxx_m3u8,
    download_hclips_direct, download_hclips_m3u8,
    download_upornia_direct, download_upornia_m3u8,
    download_vjav_direct, download_vjav_m3u8,
    download_hdzog_direct, download_hdzog_m3u8,
    txxx_sessions, hclips_sessions, upornia_sessions,
    vjav_sessions, hdzog_sessions,
)
from otherwebsiteshandler.drtuber_handler import (
    is_drtuber_url, extract_drtuber_qualities,
    download_drtuber_direct, download_drtuber_m3u8, drtuber_sessions,
)
from otherwebsiteshandler.porntop_handler import (
    is_porntop_url, extract_porntop_qualities,
    download_porntop_direct, download_porntop_m3u8, porntop_sessions,
)
from otherwebsiteshandler.generic_handler import (
    is_pornone_url, is_pornhd_url, is_xtube_url, is_mofosex_url, is_fapvid_url,
    is_monsterporn_url, is_fetishkitsch_url, is_javhihi_url, is_tokyoporn_url,
    is_javwhores_url, is_goodporn_url, is_porn365_url, is_fapcake_url, is_fux_url,
    extract_pornone_qualities, extract_pornhd_qualities, extract_xtube_qualities,
    extract_mofosex_qualities, extract_fapvid_qualities, extract_monsterporn_qualities,
    extract_fetishkitsch_qualities, extract_javhihi_qualities, extract_tokyoporn_qualities,
    extract_javwhores_qualities, extract_goodporn_qualities, extract_porn365_qualities,
    extract_fapcake_qualities, extract_fux_qualities,
    download_pornone_direct, download_pornhd_direct, download_xtube_direct,
    download_mofosex_direct, download_fapvid_direct, download_monsterporn_direct,
    download_fetishkitsch_direct, download_javhihi_direct, download_tokyoporn_direct,
    download_javwhores_direct, download_goodporn_direct, download_porn365_direct,
    download_fapcake_direct, download_fux_direct,
    download_pornone_m3u8, download_pornhd_m3u8, download_xtube_m3u8,
    download_mofosex_m3u8, download_fapvid_m3u8, download_monsterporn_m3u8,
    download_fetishkitsch_m3u8, download_javhihi_m3u8, download_tokyoporn_m3u8,
    download_javwhores_m3u8, download_goodporn_m3u8, download_porn365_m3u8,
    download_fapcake_m3u8, download_fux_m3u8,
    pornone_sessions, pornhd_sessions, xtube_sessions, mofosex_sessions,
    fapvid_sessions, monsterporn_sessions, fetishkitsch_sessions,
    javhihi_sessions, tokyoporn_sessions, javwhores_sessions,
    goodporn_sessions, porn365_sessions, fapcake_sessions, fux_sessions,
)
# ─── Comic sites handler (17 sites) ───────────────────────
from otherwebsiteshandler.comics_handler import (
    SITES as COMIC_SITES,
    extract_comic_info,
    extract_comic_search_results,
    build_comic_pdf,
    download_comic_video,
    is_hdporncomics_url, is_sexkomix2_url, is_hentai_name_url,
    is_porncomics_cloud_url, is_novelcrow_url, is_3hentai_url,
    is_erofus_url, is_nhentai_url, is_ilikecomix_url,
    is_hentai18_url, is_sexcomix_me_url, is_xlecx_url,
    is_comics_moon_url, is_comicsporn_url, is_zzcartoon_url,
    is_comicsflix_url, is_eggporncomics_url,
    _get_site_key_from_url as _get_comic_site_key,
    _is_comic_url as _is_comic_page_url,
    _is_search_url as _is_comic_search_url,
)
# sessions dict برای همه سایت‌های کمیک
comic_sessions: dict = {}
# OCR handler (extract text from image)
from otherwebsiteshandler.image_ocr_handler import extract_text_from_image
from otherwebsiteshandler.face_swap_handler import face_swap
from otherwebsiteshandler.ai.image_generator import generate_image, ART_STYLES, SHAPES as AI_SHAPES, MAX_IMAGES as AI_MAX_IMAGES, QUALITY_LEVELS as AI_QUALITY
ocr_sessions: dict = {}
faceswap_sessions: dict = {}
from y2mate import Y2MateSession
from youtube_extractor import extract_youtube_info
from happyscribe_subtitle import hardcode_subtitle_online
from subtitle_extractor import get_subtitle_streams, extract_subtitles

# ====================== CONFIGURATION ======================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
API_ID = int(os.environ.get("API_ID", os.environ.get("TELEGRAM_API_ID", 2040)))
API_HASH = os.environ.get("API_HASH", os.environ.get("TELEGRAM_API_HASH", "b18441a1ff607e10a989891a5462e627"))

def _parse_authorized_mainadmin() -> int:
    raw = os.environ.get("AUTHORIZED_MAINADMIN", os.environ.get("ADMIN_ID", "818185073")).strip()
    if raw.lstrip("-").isdigit():
        return int(raw)
    return 818185073

ADMIN_ID = _parse_authorized_mainadmin()
AUTHORIZED_MAINADMIN = ADMIN_ID

def _parse_authorized_users() -> set:
    raw = os.environ.get("AUTHORIZED_USERS", "")
    users = set()
    if raw:
        for item in raw.split(","):
            item = item.strip()
            if item.lstrip("-").isdigit():
                users.add(int(item))
    if not users:
        users = {818185073, 6936101187, 7972834913, 8228738080}
    # Always ensure main admin is in authorized users set
    users.add(ADMIN_ID)
    return users

AUTHORIZED_USERS = _parse_authorized_users()

MAX_FILE_SIZE_MB = 50000  # allow up to ~50GB (bot will split into 2GB parts)
MAX_PART_SIZE = 1900 * 1024 * 1024  # 1.9GB per part for Telegram upload
OUTPUT_FOLDER = "output_files"

os.makedirs(OUTPUT_FOLDER, exist_ok=True)
HEALTH_PORT = int(os.environ.get("HEALTH_PORT", os.environ.get("PORT", 10000)))

video_cache: Dict[str, Dict] = {}
user_state: Dict[int, Dict] = {}
admin_pending_add: Dict[int, bool] = {}
active_downloads: Dict[str, Dict] = {}
active_uploads: Dict[str, Dict] = {}
pdfimg_sessions: Dict[str, Dict] = {}  # نگه‌داری مسیر عکس‌ها برای send all
snapwc_sessions: Dict[str, SnapWCSession] = {}  # SnapWC session references
y2mate_sessions: Dict[str, dict] = {}  # Y2Mate session cache

# آپلود گیتهاب — با /startgithub فعال، با /stopgithub غیرفعال میشه
GITHUB_ENABLED: bool = False

# burn subtitle — با /sub فعال/غیرفعال میشه
SUB_BURN_ENABLED: bool = False

# نگه‌داری فایل‌های ویدیویی که کاربر فرستاده و منتظر تأیید گیتهاب هستن
video_github_pending: Dict[str, Dict] = {}

# نگه‌داری فایل‌های ویدیویی که باید به صورت video ارسال بشن (batch)
video_send_pending: Dict[str, Dict] = {}
# تسک‌های تایمر batch ویدیو
video_send_timers: Dict[str, asyncio.Task] = {}

# اشتراک‌گذاری ویدیو با لینک از طریق آرکایو کانال تلگرام
ARCHIVE_CHANNEL_ID: int = int(os.getenv("ARCHIVE_CHANNEL_ID", "0"))
MAX_FILE_SIZE_GB: int = int(os.getenv("MAX_FILE_SIZE_GB", "2"))
CLEANUP_DELAY_SECONDS: int = int(os.getenv("CLEANUP_DELAY_SECONDS", "20"))
# GitHub sponsor persistence
SPONSOR_REPO: str = os.getenv("SPONSOR_REPO", "astwdnya/data")
SPONSOR_BRANCH: str = os.getenv("SPONSOR_BRANCH", "main")
SPONSOR_FILE: str = os.getenv("SPONSOR_FILE", "data.txt")
BOT_USERNAME: str = ""
sponsors: list = []  # هر آیتم: {"name": str, "chat_id": str, "link": str}
pending_sponsor_name: Dict[int, str] = {}  # مرحله اول اضافه کردن اسپانسر

# ── Default search engine per user ──
USER_DEFAULT_SEARCH: Dict[int, str] = {}  # user_id -> "ph"|"xv"|"ep"|"xn"  (default: "ph")
USER_SETTINGS_FILE = "user_settings.json"

def _load_user_settings():
    global USER_DEFAULT_SEARCH
    try:
        if os.path.exists(USER_SETTINGS_FILE):
            with open(USER_SETTINGS_FILE, "r", encoding="utf-8") as f:
                USER_DEFAULT_SEARCH = {int(k): v for k, v in json.load(f).items()}
    except Exception as e:
        logger.warning(f"[SETTINGS] Error loading user settings: {e}")
        USER_DEFAULT_SEARCH = {}

def _save_user_settings():
    try:
        with open(USER_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(USER_DEFAULT_SEARCH, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"[SETTINGS] Error saving user settings: {e}")

def get_user_default_search(user_id: int) -> str:
    return USER_DEFAULT_SEARCH.get(user_id, "ph")

def set_user_default_search(user_id: int, source: str):
    USER_DEFAULT_SEARCH[user_id] = source
    _save_user_settings()

# نگه‌داری ویدیوهایی که منتظر فایل زیرنویس هستن
subtitle_sessions: Dict[int, Dict] = {}  # key: chat_id
subburn_cancel: Dict[int, bool] = {}  # key: chat_id, True = cancel requested


def _escape_md(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("_", "\\_")
        .replace("*", "\\*")
        .replace("[", "\\[")
        .replace("`", "\\`")
    )


# ====================== DISK UTILITIES ======================
def get_free_space(path: str = OUTPUT_FOLDER) -> int:
    os.makedirs(path, exist_ok=True)
    usage = shutil.disk_usage(path)
    return usage.free


async def _get_video_duration(filepath: str) -> float:
    """دریافت مدت زمان ویدیو با ffprobe."""
    if shutil.which("ffprobe"):
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            filepath
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            val = float(stdout.decode().strip())
            if val > 0: return val
        except Exception:
            pass
    return 0.0


async def split_video_smart(
    filepath: str,
    max_part_size: int = MAX_PART_SIZE,
    status_msg: Message = None,
) -> list:
    """
    تقسیم فوق‌العاده سریع و سبک ویدیوهای بیشتر از ۲ گیگابایت با FFmpeg -c copy (بدون رندر مجدد).
    سرعت بالا، بدون مصرف اضافی CPU/RAM، و تولید پارت‌های ویدیویی قابل پخش در تلگرام.
    """
    file_size = os.path.getsize(filepath)
    if file_size <= max_part_size:
        return [filepath]

    if status_msg:
        await safe_edit(
            status_msg,
            f"📦 **حجم ویدیو ({file_size / 1024 / 1024 / 1024:.2f} گیگابایت) بیشتر از ۲ گیگابایت است.**\n"
            f"⚡ **در حال تقسیم ویدیو به پارت‌های ۲ گیگابایتی...**",
        )

    # 1. اگر FFmpeg هست، تقسیم بر اساس استریم ویدیو انجام بدید (-c copy)
    if shutil.which("ffmpeg"):
        duration = await _get_video_duration(filepath)
        if duration > 0:
            num_parts = math.ceil(file_size / max_part_size)
            segment_seconds = int(duration / num_parts)
            base, ext = os.path.splitext(filepath)
            if not ext: ext = ".mp4"
            output_pattern = os.path.join(OUTPUT_FOLDER, f"{os.path.basename(base)}_part%03d{ext}")

            cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", filepath,
                "-c", "copy",
                "-map", "0",
                "-segment_time", str(segment_seconds),
                "-f", "segment",
                "-reset_timestamps", "1",
                output_pattern
            ]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                _, stderr = await proc.communicate()
                if proc.returncode == 0:
                    part_files = sorted(glob.glob(os.path.join(OUTPUT_FOLDER, f"{os.path.basename(base)}_part*{ext}")))
                    if part_files:
                        logger.info(f"[SPLIT] FFmpeg -c copy split {len(part_files)} video parts successfully")
                        return part_files
            except Exception as e:
                logger.warning(f"[SPLIT] FFmpeg segment failed: {e}")

    # Fallback to binary splitting
    return await split_file_into_parts(filepath, max_part_size, status_msg)


async def split_file_into_parts(
    filepath: str,
    max_part_size: int = MAX_PART_SIZE,
    status_msg: Message = None,
) -> list:
    parts = []
    file_size = os.path.getsize(filepath)
    base, ext = os.path.splitext(filepath)
    base_name = os.path.basename(base)
    total_parts = (file_size + max_part_size - 1) // max_part_size

    part_num = 1
    last_update = 0.0
    with open(filepath, "rb") as f:
        while True:
            part_filename = f"{base_name}{ext}.part{part_num:03d}"
            part_path = os.path.join(OUTPUT_FOLDER, part_filename)
            remaining = file_size - f.tell()
            if remaining <= 0:
                break
            read_size = min(max_part_size, remaining)
            with open(part_path, "wb") as pf:
                written = 0
                while written < read_size:
                    chunk = f.read(min(4 * 1024 * 1024, read_size - written))
                    if not chunk:
                        break
                    pf.write(chunk)
                    written += len(chunk)
                    if status_msg:
                        now = time.time()
                        if now - last_update >= 2.0:
                            last_update = now
                            pct = (f.tell() / file_size) * 100
                            await safe_edit(
                                status_msg,
                                f"✂️ Splitting part {part_num}/{total_parts}: {pct:.1f}%",
                            )
            parts.append(part_path)
            if status_msg:
                await safe_edit(
                    status_msg,
                    f"✂️ Part {part_num}/{total_parts} done ({human_readable_size(read_size)}) — {part_num}/{total_parts} complete.",
                )
            part_num += 1

    if status_msg:
        await safe_edit(status_msg, f"✂️ Split complete: {total_parts} parts.")

    return parts


# ====================== LOGGING ======================
import sys as _sys

# ===== LOGGING: همه چیز به stdout میره تا توی Render logs دیده بشه =====
_log_formatter = logging.Formatter(
    fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s", datefmt="%H:%M:%S"
)
_stdout_handler = logging.StreamHandler(_sys.stdout)
_stdout_handler.setFormatter(_log_formatter)
_stdout_handler.setLevel(logging.DEBUG)

logging.root.setLevel(logging.DEBUG)
logging.root.addHandler(_stdout_handler)

# کم‌حرف کردن کتابخونه‌های پرسروصدا
logging.getLogger("telethon").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)
logging.getLogger("playwright").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

logger = logging.getLogger("UltimateBot")

# ====================== FLASK KEEP-ALIVE ======================
flask_app = Flask(__name__)


@flask_app.route("/")
def health():
    return "OK", 200


def start_keep_alive():
    Thread(
        target=lambda: flask_app.run(host="0.0.0.0", port=HEALTH_PORT, debug=False),
        daemon=True,
    ).start()


# ====================== UTILITIES ======================
def human_readable_size(num_bytes: int) -> str:
    if num_bytes == 0:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB"]:
        if num_bytes < 1024:
            return f"{num_bytes:.2f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.2f} TB"


def extract_quality(filename: str) -> str:
    name = filename.lower()
    for q in ["2160", "1080", "720", "480", "360", "240"]:
        if f"{q}p" in name:
            return q
    if "4k" in name:
        return "4K"
    return ""


def extract_season_episode(name: str) -> tuple:
    s = name.lower()
    # S02E06 / s02e06 / S2E6
    m = re.search(r"s0*(\d+)\s*[. -]?\s*e0*(\d+)", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    # Season 2 Episode 6
    m = re.search(r"season\s*(\d+)\s*episode\s*(\d+)", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    # 2x06 / 02x6 — but NOT resolution like 720x480
    m = re.search(r"(?:^|[^0-9])0*(\d+)\s*x\s*0*(\d+)", s)
    if m:
        s_num, e_num = int(m.group(1)), int(m.group(2))
        if s_num <= 99 and e_num <= 999:
            return s_num, e_num
    return None, None


def clean_filename(name: str) -> str:
    name = re.sub(r"^hs_\d+_", "", name)
    name = re.sub(r"_\d+_subtitled$", "", name)
    name = re.sub(r"_subtitled$", "", name)
    return name


def build_video_caption(
    orig_name: str, size: int, duration: float = 0, subtitle_name: str = ""
) -> str:
    clean = clean_filename(orig_name)
    lines = [f"🎬 {clean}"]
    season, episode = extract_season_episode(orig_name)
    if season is not None:
        lines.append(f"فصل :{season}")
    if episode is not None:
        lines.append(f"قسمت :{episode}")
    if subtitle_name:
        lines.append(f"\nزیرنویس چسبیده: دارد ({subtitle_name}) ✅")
    else:
        lines.append(f"\nزیرنویس چسبیده: ندارد ❌")
    q = extract_quality(orig_name)
    if q:
        lines.append(f"کیفیت: {q}")
    if duration and duration > 0:
        mins = int(duration // 60)
        if mins > 0:
            lines.append(f"⏱ مدت : {mins} دقیقه")
        else:
            lines.append(f"⏱ مدت : {int(duration)} ثانیه")
    lines.append(f"📦 حجم : {human_readable_size(size)}")
    return "\n".join(lines)


def safe_filename(title: str) -> str:
    return (
        re.sub(r'[<>:"/\\|?*]', "_", title.strip()[:80]) or f"file_{int(time.time())}"
    )


def parse_size_input(text: str) -> Optional[int]:
    # FIX: regex محکم‌تر — فقط عدد+واحد
    text = text.strip().lower().replace(" ", "")
    match = re.match(r"^(\d+\.?\d*)([kmg]?)b?$", text)
    if not match:
        return None
    num = float(match.group(1))
    unit = match.group(2)
    if unit == "k":
        return int(num * 1024)
    elif unit == "m":
        return int(num * 1024 * 1024)
    elif unit == "g":
        return int(num * 1024 * 1024 * 1024)
    return int(num)


async def maybe_upload_github(
    client, chat_id: int, filepath: str, file_size: int
) -> str:
    """
    اگه GITHUB_ENABLED فعال باشه فایل رو آپلود میکنه و لینک رو برمیگردونه.
    در غیر اینصورت رشته خالی برمیگردونه.
    """
    global GITHUB_ENABLED
    if not GITHUB_ENABLED:
        return ""
    if not github_configured():
        return ""
    if file_size > GITHUB_MAX_MB * 1024 * 1024:
        return ""
    try:
        gh_ok, gh_msg, gh_url = await upload_to_github(filepath)
        if gh_ok and gh_url:
            logger.info(f"GitHub upload OK: {gh_url}")
            return gh_url
        else:
            logger.warning(f"GitHub upload failed: {gh_msg}")
    except Exception as e:
        logger.warning(f"GitHub upload exception: {e}")
    return ""


async def safe_edit(msg, text: str, buttons=None):
    try:
        if buttons is not None:
            await msg.edit(text, parse_mode="markdown", buttons=buttons)
        else:
            await msg.edit(text, parse_mode="markdown")
    except Exception:
        pass


def build_progress_text(
    operation: str, current: int, total: int, speed: float, start_time: float
) -> str:
    eta = (total - current) / speed if speed > 0 else 0
    percent = (current / total) * 100 if total > 0 else 0
    filled = int(18 * current // total) if total > 0 else 0
    bar = "█" * filled + "░" * (18 - filled)
    if eta < 60:
        eta_str = f"{int(eta)}s"
    elif eta < 3600:
        eta_str = f"{int(eta // 60)}:{int(eta % 60):02d}"
    else:
        eta_str = f"{int(eta // 3600)}h{int((eta % 3600) // 60)}m"
    return (
        f"**{operation}**\n"
        f"`[{bar}]` **{percent:.1f}%**\n"
        f"📦 {human_readable_size(current)} / {human_readable_size(total)}\n"
        f"🚀 {human_readable_size(int(speed))}/s  •  ⏱ {eta_str}"
    )


# ====================== DIRECT FILE URL DETECTION ======================
def is_direct_file_url(url: str) -> bool:
    path = url.split("?")[0].split("#")[0].lower()
    direct_extensions = (
        # Video
        ".mp4",
        ".mkv",
        ".avi",
        ".mov",
        ".wmv",
        ".flv",
        ".webm",
        ".m4v",
        ".mpg",
        ".mpeg",
        ".mpe",
        ".3gp",
        ".3g2",
        ".ogv",
        ".ogx",
        ".ts",
        ".mts",
        ".m2ts",
        ".vob",
        ".divx",
        ".xvid",
        ".f4v",
        ".rm",
        ".rmvb",
        ".asf",
        ".amv",
        ".yuv",
        ".qt",
        # Audio
        ".mp3",
        ".m4a",
        ".flac",
        ".wav",
        ".ogg",
        ".aac",
        ".wma",
        ".opus",
        ".ape",
        ".ac3",
        ".dts",
        ".ra",
        ".mid",
        ".midi",
        ".aiff",
        ".aif",
        ".au",
        ".amr",
        ".awb",
        ".voc",
        ".cda",
        ".pcm",
        ".tta",
        ".wv",
        ".mpc",
        ".mka",
        ".oga",
        ".spx",
        # Image
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".bmp",
        ".tiff",
        ".tif",
        ".svg",
        ".svgz",
        ".ico",
        ".cur",
        ".psd",
        ".ai",
        ".eps",
        ".raw",
        ".cr2",
        ".nef",
        ".arw",
        ".dng",
        ".jxr",
        ".heic",
        ".heif",
        ".avif",
        ".jfif",
        ".pjpeg",
        ".pjp",
        # Document
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".odt",
        ".ods",
        ".odp",
        ".rtf",
        ".txt",
        ".csv",
        ".tsv",
        ".epub",
        ".mobi",
        ".azw3",
        ".fb2",
        ".djvu",
        ".pages",
        ".numbers",
        ".key",
        ".md",
        ".tex",
        # Archive
        ".zip",
        ".rar",
        ".7z",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".zst",
        ".lz",
        ".lzma",
        ".lzo",
        ".arj",
        ".cab",
        ".iso",
        ".vhd",
        ".vmdk",
        ".dmg",
        ".tgz",
        ".tbz2",
        ".tlz",
        ".txz",
        ".z",
        ".sz",
        ".wim",
        ".chm",
        ".hfs",
        # Executable
        ".exe",
        ".msi",
        ".appimage",
        ".deb",
        ".rpm",
        ".apk",
        ".ipa",
        ".xapk",
        ".apks",
        ".aab",
        ".dmg",
        ".pkg",
        ".sh",
        ".bat",
        ".cmd",
        ".com",
        ".bin",
        ".elf",
        ".run",
        ".o",
        ".ko",
        ".so",
        ".dll",
        ".sys",
        # Font
        ".ttf",
        ".otf",
        ".woff",
        ".woff2",
        ".eot",
        # 3D / CAD
        ".stl",
        ".obj",
        ".fbx",
        ".blend",
        ".3ds",
        ".dae",
        ".step",
        ".stp",
        ".iges",
        ".igs",
        # Subtitles
        ".srt",
        ".ass",
        ".ssa",
        ".vtt",
        ".sub",
        ".idx",
        # Torrent
        ".torrent",
        # Disk images
        ".img",
        ".nrg",
        ".cue",
        ".bin",
        ".mdf",
        ".mds",
        # Game
        ".rom",
        ".gba",
        ".nds",
        ".n64",
        ".z64",
        ".v64",
        ".smc",
        ".sfc",
        ".gb",
        ".gbc",
        ".nes",
        # Programming
        ".py",
        ".js",
        ".ts",
        ".jsx",
        ".tsx",
        ".java",
        ".cpp",
        ".c",
        ".h",
        ".hpp",
        ".cs",
        ".rb",
        ".go",
        ".rs",
        ".swift",
        ".kt",
        ".scala",
        ".php",
        ".pl",
        ".lua",
        ".sql",
        ".r",
        ".m",
        ".mm",
        ".dart",
        # Database
        ".db",
        ".sqlite",
        ".sqlite3",
        ".mdb",
        ".accdb",
    )
    return any(path.endswith(ext) for ext in direct_extensions)


# ====================== DOWNLOAD VIA PLAYWRIGHT (REAL BROWSER) ======================
async def _download_via_curl_cffi(url: str, filepath: str, status_msg: Message, dl_id: str) -> Tuple[Optional[str], Optional[str], int]:
    """
    لایه 1: دانلود با curl_cffi (با impersonate).
    از multi-segment download با ۱۶ connection موازی استفاده می‌کنه برای سرعت بالا.
    اگه سرور Range رو پشتیبانی نکرد، fallback به single-connection می‌شه.
    """
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return None, "curl_cffi not installed", 0

    # دکمه‌های کنترل
    dl_buttons_cancel = [
        [
            Button.inline("❌ Cancel", f"dlcancel_{dl_id}"),
        ]
    ]

    # متغیرهای مشترک برای cancel
    if dl_id not in active_downloads:
        active_downloads[dl_id] = {"paused": False, "cancelled": False}

    try:
        await safe_edit(status_msg, "🌐 Downloading via curl_cffi (chrome)...", buttons=dl_buttons_cancel)

        # ابتدا HEAD request برای گرفتن حجم و نام فایل
        # timeout کوتاه (5 ثانیه) چون فقط برای metadata هست
        content_length = 0
        content_type = ""
        accept_ranges = ""
        try:
            async with AsyncSession() as session:
                head_resp = await session.head(
                    url,
                    impersonate="chrome",
                    headers={"Accept": "*/*"},
                    allow_redirects=True,
                    timeout=5,
                )
                if head_resp.status_code in (200, 206):
                    content_length = int(head_resp.headers.get("Content-Length", 0))
                    content_type = head_resp.headers.get("Content-Type", "").lower()
                    accept_ranges = head_resp.headers.get("Accept-Ranges", "").lower()
                elif head_resp.status_code == 403:
                    return None, "HTTP_403", 0
        except Exception:
            pass

        # تشخیص نام فایل از URL
        url_path = url.split("?")[0].rstrip("/")
        orig_name = os.path.basename(url_path)
        if not orig_name:
            orig_name = f"file_{int(time.time())}"
        orig_name = re.sub(r"[^\w\.\-\_\(\) ]", "_", orig_name)
        if len(orig_name) > 80:
            base, ext = os.path.splitext(orig_name)
            orig_name = base[:75] + ext

        # اگه content-type مشخصه و پسوندی نداره، اضافه کن
        if not os.path.splitext(orig_name)[1]:
            ct_map = {
                "application/x-ipa": ".ipa",
                "application/vnd.android.package-archive": ".apk",
                "application/zip": ".zip",
                "application/octet-stream": ".bin",
                "application/pdf": ".pdf",
                "video/mp4": ".mp4",
                "audio/mpeg": ".mp3",
            }
            for ct_key, ext in ct_map.items():
                if ct_key in content_type:
                    orig_name += ext
                    break

        final_path = os.path.join(OUTPUT_FOLDER, orig_name)
        counter = 1
        while os.path.exists(final_path):
            base, ext = os.path.splitext(orig_name)
            final_path = os.path.join(OUTPUT_FOLDER, f"{base}_{counter}{ext}")
            counter += 1

        # ─── تابع کمکی: ساخت progress message ───
        def _make_progress_msg(downloaded: int, total: int, start_time: float, now: float, num_seg: int = 0) -> str:
            elapsed = now - start_time
            speed = downloaded / elapsed if elapsed > 0 else 0
            dl_mb = downloaded / 1024 / 1024
            seg_info = f"\n🔗 Segments: {num_seg}" if num_seg > 0 else ""
            if total > 0:
                total_mb = total / 1024 / 1024
                pct = downloaded / total * 100
                filled = int(pct / 5)
                bar = "█" * filled + "░" * (20 - filled)
                speed_mb = min(speed / 1024 / 1024, 999)
                eta_secs = int((total - downloaded) / speed) if speed > 0 else 0
                eta_m, eta_s = divmod(eta_secs, 60)
                return (
                    f"📥 **Downloading...**\n(هندلر)\n`[{bar}]`\n"
                    f"💾 {dl_mb:.1f}/{total_mb:.1f} MB  •  ⚡ {speed_mb:.1f} MB/s\n"
                    f"📊 {pct:.1f}%  •  ⏱ ETA: {eta_m}:{eta_s:02d}"
                    f"{seg_info}"
                )
            return (
                f"📥 **Downloading...**\n(هندلر)\n💾 {dl_mb:.1f} MB  •  ⚡ {speed / 1024 / 1024:.1f} MB/s"
                f"{seg_info}"
            )

        # ─── تابع کمکی: گزارش progress ───
        async def _report_progress(downloaded: int, total: int, start_time: float, last_update: list, num_seg: int = 0):
            now = time.time()
            if now - last_update[0] >= 2.0:
                last_update[0] = now
                try:
                    msg = _make_progress_msg(downloaded, total, start_time, now, num_seg)
                    await safe_edit(status_msg, msg, buttons=dl_buttons_cancel)
                except Exception:
                    pass

        # ═══════════════════════════════════════════════════════════════════
        # روش 1: WORK-QUEUE MULTI-SEGMENT (اگه Range پشتیبانی می‌شه و فایل بزرگتر از 5MB)
        #
        # به جای ۱۶ segment ثابت، فایل رو به chunk های کوچیک (5MB) تقسیم می‌کنیم
        # و ۱۶ worker همزمان از یه queue می‌خورن. اینطوری همیشه ۱۶ connection
        # فعال هستن تا آخر دانلود و سرعت ثابت می‌مونه.
        #
        # بهینه‌سازی: از یه session اشتراکی استفاده می‌کنیم تا TLS handshake
        # فقط یه بار انجام بشه (به جای هر chunk).
        # ═══════════════════════════════════════════════════════════════════
        NUM_WORKERS = 16
        CHUNK_SIZE = 5 * 1024 * 1024  # 5 MB per chunk
        MIN_SIZE_FOR_MULTI = 5 * 1024 * 1024  # 5 MB

        if (accept_ranges == "bytes" and content_length >= MIN_SIZE_FOR_MULTI
            and content_length <= MAX_FILE_SIZE_MB * 1024 * 1024):

            # ساخت لیست chunk ها
            chunks = []
            offset = 0
            chunk_idx = 0
            while offset < content_length:
                end = min(offset + CHUNK_SIZE - 1, content_length - 1)
                chunks.append((chunk_idx, offset, end))
                offset = end + 1
                chunk_idx += 1

            total_chunks = len(chunks)
            logger.info(f"[DL-CFFI] Work-queue download: {total_chunks} chunks × {CHUNK_SIZE//1024//1024}MB, {NUM_WORKERS} workers, total={content_length}")

            # فایل خروجی رو همون اول با حجم نهایی بساز (sparse file)
            try:
                with open(final_path, "wb") as f:
                    f.truncate(content_length)
            except Exception as e:
                logger.warning(f"[DL-CFFI] Could not pre-allocate file: {e}")

            # Queue از chunk ها
            chunk_queue = asyncio.Queue()
            for c in chunks:
                await chunk_queue.put(c)

            # متغیرهای مشترک
            downloaded_bytes = [0] * total_chunks
            completed_chunks = [0]  # count
            failed_chunks = []
            start_time = time.time()
            last_update = [0.0]
            progress_lock = asyncio.Lock()
            file_write_lock = asyncio.Lock()
            first_chunk_started = [False]

            # session اشتراکی برای همه worker ها (جلوگیری از TLS handshake مکرر)
            shared_session = AsyncSession()

            async def _update_progress(force: bool = False):
                """گزارش progress به کاربر."""
                now = time.time()
                if not force and now - last_update[0] < 1.5:
                    return
                last_update[0] = now
                total_dl = sum(downloaded_bytes)
                elapsed = now - start_time
                speed = total_dl / elapsed if elapsed > 0 else 0
                dl_mb = total_dl / 1024 / 1024
                total_mb = content_length / 1024 / 1024
                pct = (total_dl / content_length * 100) if content_length > 0 else 0
                filled = int(pct / 5)
                bar = "█" * filled + "░" * (20 - filled)
                speed_mb = min(speed / 1024 / 1024, 999)
                eta_secs = int((content_length - total_dl) / speed) if speed > 0 else 0
                eta_m, eta_s = divmod(eta_secs, 60)
                try:
                    await safe_edit(
                        status_msg,
                        f"📥 **Downloading...**\n(هندلر)\n`[{bar}]`\n"
                        f"💾 {dl_mb:.1f}/{total_mb:.1f} MB  •  ⚡ {speed_mb:.1f} MB/s\n"
                        f"📊 {pct:.1f}%  •  ⏱ ETA: {eta_m}:{eta_s:02d}\n"
                        f"📦 {completed_chunks[0]}/{total_chunks} chunks • 🔥 {NUM_WORKERS}x",
                        buttons=dl_buttons_cancel,
                    )
                except Exception:
                    pass

            async def _download_worker(worker_id: int):
                """هر worker از queue chunk می‌گیره و دانلود می‌کنه."""
                while True:
                    # check cancel
                    if active_downloads.get(dl_id, {}).get("cancelled"):
                        return False

                    try:
                        chunk_info = chunk_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return True  # queue خالی، worker تموم کرد

                    c_idx, byte_start, byte_end = chunk_info
                    chunk_size = byte_end - byte_start + 1
                    max_retries = 3

                    for attempt in range(max_retries):
                        # check cancel
                        if active_downloads.get(dl_id, {}).get("cancelled"):
                            return False

                        try:
                            # استفاده از session اشتراکی (بدون ساخت session جدید)
                            resp = await shared_session.get(
                                url,
                                impersonate="chrome",
                                headers={
                                    "Accept": "*/*",
                                    "Accept-Language": "en-US,en;q=0.9",
                                    "Range": f"bytes={byte_start}-{byte_end}",
                                },
                                allow_redirects=True,
                                timeout=300,
                                stream=True,
                            )

                            if resp.status_code not in (200, 206):
                                raise Exception(f"HTTP {resp.status_code}")

                            # پیام شروع سریع (به محض اولین chunk)
                            if not first_chunk_started[0]:
                                first_chunk_started[0] = True
                                await _update_progress(force=True)

                            # دانلود chunk به memory (چون کوچیکه - 5MB)
                            chunk_data = b""
                            async for piece in resp.aiter_content():
                                if not piece:
                                    continue
                                # check cancel
                                if active_downloads.get(dl_id, {}).get("cancelled"):
                                    return False
                                chunk_data += piece

                            if len(chunk_data) != chunk_size:
                                raise Exception(f"Size mismatch: expected {chunk_size}, got {len(chunk_data)}")

                            # نوشتن به فایل با seek (با lock برای thread safety)
                            async with file_write_lock:
                                async with aiofiles.open(final_path, "r+b") as f:
                                    await f.seek(byte_start)
                                    await f.write(chunk_data)

                            downloaded_bytes[c_idx] = chunk_size
                            async with progress_lock:
                                completed_chunks[0] += 1
                                await _update_progress()
                            break  # chunk با موفقیت دانلود شد

                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            logger.warning(f"[DL-CFFI] Worker {worker_id} chunk {c_idx} attempt {attempt+1} failed: {e}")
                            if attempt < max_retries - 1:
                                await asyncio.sleep(2 * (attempt + 1))
                            else:
                                failed_chunks.append((c_idx, str(e)[:100]))
                                return False

                    # chunk بعدی رو بگیر
                    chunk_queue.task_done()

                return True

            # اجرای موازی ۱۶ worker
            try:
                results = await asyncio.gather(
                    *[_download_worker(i) for i in range(NUM_WORKERS)],
                    return_exceptions=True,
                )

                # بستن session اشتراکی
                try:
                    await shared_session.close()
                except Exception:
                    pass

                # check cancel
                if active_downloads.get(dl_id, {}).get("cancelled"):
                    try:
                        os.remove(final_path)
                    except Exception:
                        pass
                    return None, "Cancelled by user", 0

                # check failures
                worker_failures = [r for r in results if r is not True and isinstance(r, bool) and not r]
                if worker_failures or failed_chunks:
                    logger.warning(f"[DL-CFFI] {len(worker_failures)} workers failed, {len(failed_chunks)} chunks failed")
                    if failed_chunks:
                        # fallback به single connection
                        try:
                            os.remove(final_path)
                        except Exception:
                            pass
                        return await _download_single_curl_cffi(
                            url, final_path, orig_name, content_length, content_type,
                            status_msg, dl_id, dl_buttons_cancel, AsyncSession
                        )

            except Exception as e:
                logger.error(f"[DL-CFFI] Work-queue error: {e}", exc_info=True)
                try:
                    await shared_session.close()
                except Exception:
                    pass
                try:
                    os.remove(final_path)
                except Exception:
                    pass
                return await _download_single_curl_cffi(
                    url, final_path, orig_name, content_length, content_type,
                    status_msg, dl_id, dl_buttons_cancel, AsyncSession
                )

            # بررسی فایل نهایی
            file_size = os.path.getsize(final_path)
            if file_size < 1024:
                try:
                    os.remove(final_path)
                except Exception:
                    pass
                return None, f"File too small ({file_size} B)", 0

            if file_size != content_length:
                logger.warning(f"[DL-CFFI] Size mismatch: expected={content_length}, got={file_size}")

            elapsed = time.time() - start_time
            avg_speed = file_size / elapsed / 1024 / 1024 if elapsed > 0 else 0
            logger.info(f"[DL-CFFI] Work-queue DONE | size={human_readable_size(file_size)} | time={elapsed:.1f}s | avg_speed={avg_speed:.1f} MB/s | chunks={total_chunks}")
            return final_path, None, file_size


        # ═══════════════════════════════════════════════════════════════════
        # روش 2: SINGLE CONNECTION (fallback یا فایل‌های کوچک)
        # ═══════════════════════════════════════════════════════════════════
        return await _download_single_curl_cffi(
            url, final_path, orig_name, content_length, content_type,
            status_msg, dl_id, dl_buttons_cancel, AsyncSession
        )

    except Exception as e:
        logger.error(f"[DL-CFFI] Error: {e}", exc_info=True)
        return None, str(e)[:100], 0


async def _download_single_curl_cffi(
    url: str,
    final_path: str,
    orig_name: str,
    content_length: int,
    content_type: str,
    status_msg: Message,
    dl_id: str,
    dl_buttons_cancel,
    AsyncSession,
) -> Tuple[Optional[str], Optional[str], int]:
    """دانلود با single connection (fallback برای وقتی که multi-segment کار نمی‌کنه)."""
    await safe_edit(status_msg, f"📥 Downloading: {orig_name}", buttons=dl_buttons_cancel)

    async with AsyncSession() as session:
        resp = await session.get(
            url,
            impersonate="chrome",
            headers={
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
            },
            allow_redirects=True,
            timeout=600,
            stream=True,
        )

        if resp.status_code == 403:
            return None, "HTTP_403", 0
        if resp.status_code not in (200, 206):
            return None, f"HTTP {resp.status_code}", 0

        ct = (resp.headers.get("Content-Type", "") or "").lower()
        if "text/html" in ct and not orig_name.endswith((".html", ".htm")):
            return None, "Got HTML page instead of file", 0

        if content_length == 0:
            content_length = int(resp.headers.get("Content-Length", 0))

        if content_length > MAX_FILE_SIZE_MB * 1024 * 1024:
            return None, f"File too large ({human_readable_size(content_length)})", 0

        downloaded = 0
        start_time = time.time()
        last_update = [0.0]

        async with aiofiles.open(final_path, "wb") as f:
            async for chunk in resp.aiter_content():
                if not chunk:
                    continue
                if active_downloads.get(dl_id, {}).get("cancelled"):
                    try:
                        os.remove(final_path)
                    except Exception:
                        pass
                    return None, "Cancelled by user", 0

                await f.write(chunk)
                downloaded += len(chunk)

                now = time.time()
                if now - last_update[0] >= 2.0:
                    last_update[0] = now
                    elapsed = now - start_time
                    speed = downloaded / elapsed if elapsed > 0 else 0
                    if content_length > 0:
                        pct = downloaded / content_length * 100
                        filled = int(pct / 5)
                        bar = "█" * filled + "░" * (20 - filled)
                        dl_mb = downloaded / 1024 / 1024
                        total_mb = content_length / 1024 / 1024
                        speed_mb = min(speed / 1024 / 1024, 999)
                        eta_secs = int((content_length - downloaded) / speed) if speed > 0 else 0
                        eta_m, eta_s = divmod(eta_secs, 60)
                        try:
                            await safe_edit(
                                status_msg,
                                f"📥 **Downloading...**\n(هندلر)\n`[{bar}]`\n"
                                f"💾 {dl_mb:.1f}/{total_mb:.1f} MB  •  ⚡ {speed_mb:.1f} MB/s\n"
                                f"📊 {pct:.1f}%  •  ⏱ ETA: {eta_m}:{eta_s:02d}",
                                buttons=dl_buttons_cancel,
                            )
                        except Exception:
                            pass
                    else:
                        dl_mb = downloaded / 1024 / 1024
                        speed_mb = min(speed / 1024 / 1024, 999)
                        try:
                            await safe_edit(
                                status_msg,
                                f"📥 **Downloading...**\n(هندلر)\n💾 {dl_mb:.1f} MB  •  ⚡ {speed_mb:.1f} MB/s",
                                buttons=dl_buttons_cancel,
                            )
                        except Exception:
                            pass

    file_size = os.path.getsize(final_path)
    if file_size < 1024:
        try:
            os.remove(final_path)
        except Exception:
            pass
        return None, f"File too small ({file_size} B)", 0

    logger.info(f"[DL-CFFI] Single DONE | size={human_readable_size(file_size)} | file={final_path}")
    return final_path, None, file_size


async def download_with_playwright(
    url: str,
    status_msg: Message,
    dl_id: str,
) -> Tuple[Optional[str], Optional[str], int]:
    """
    Download a file using multiple strategies:
      1. curl_cffi with chrome impersonate (fastest)
      2. Real Chromium browser with response body capture
      3. Fallback to download event trigger
    """
    if dl_id not in active_downloads:
        active_downloads[dl_id] = {"paused": False, "cancelled": False}

    # ── لایه 1: curl_cffi ──
    logger.info(f"[DL-PW] Layer 1: curl_cffi | url={url[:120]}")
    cf_filepath, cf_error, cf_size = await _download_via_curl_cffi(
        url, "", status_msg, dl_id
    )
    if cf_filepath:
        return cf_filepath, None, cf_size
    if cf_error == "Cancelled by user":
        return None, cf_error, 0
    logger.info(f"[DL-PW] curl_cffi failed: {cf_error}, trying Playwright...")

    # ── لایه 2: Playwright با response body capture ──
    await safe_edit(status_msg, "🌐 Downloading via real browser...")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=_browser_args())
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720},
                accept_downloads=True,
            )
            page = await context.new_page()

            # تشخیص نام فایل از URL
            url_path = url.split("?")[0].rstrip("/")
            orig_name = os.path.basename(url_path)
            if not orig_name:
                orig_name = f"file_{int(time.time())}"
            orig_name = re.sub(r"[^\w\.\-\_\(\) ]", "_", orig_name)
            if len(orig_name) > 80:
                base, ext = os.path.splitext(orig_name)
                orig_name = base[:75] + ext

            final_path = os.path.join(OUTPUT_FOLDER, orig_name)
            counter = 1
            while os.path.exists(final_path):
                base, ext = os.path.splitext(orig_name)
                final_path = os.path.join(OUTPUT_FOLDER, f"{base}_{counter}{ext}")
                counter += 1

            # متغیرهای مشترک
            download_promise: asyncio.Future = asyncio.Future()
            response_promise: asyncio.Future = asyncio.Future()

            async def on_download(download):
                if not download_promise.done():
                    download_promise.set_result(download)

            def on_response(response):
                if not response_promise.done():
                    try:
                        ct = (response.headers.get("content-type", "") or "").lower()
                        url_check = response.url
                        if (url_check == url or url_check.rstrip("/") == url.rstrip("/")):
                            if "text/html" not in ct or url_check.endswith((".ipa", ".apk", ".zip", ".exe", ".mp4", ".mp3")):
                                response_promise.set_result(response)
                    except Exception:
                        pass

            page.on("download", on_download)
            page.on("response", on_response)

            # goto با timeout مناسب
            try:
                await page.goto(url, wait_until="commit", timeout=30000)
            except Exception as e:
                logger.warning(f"[DL-PW] goto warning: {e}")

            # صبر برای download یا response (با timeout 15 ثانیه)
            try:
                done, pending = await asyncio.wait(
                    [asyncio.ensure_future(download_promise),
                     asyncio.ensure_future(response_promise)],
                    timeout=15,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if active_downloads.get(dl_id, {}).get("cancelled"):
                    await browser.close()
                    return None, "Cancelled by user", 0

                # ─── مسیر 1: download event ───
                if download_promise in done:
                    logger.info("[DL-PW] Download event triggered")
                    download = await download_promise

                    suggested = download.suggested_filename or orig_name
                    suggested = re.sub(r"[^\w\.\-\_\(\) ]", "_", suggested)
                    if len(suggested) > 100:
                        base, ext = os.path.splitext(suggested)
                        suggested = base[:95] + ext

                    final_path = os.path.join(OUTPUT_FOLDER, suggested)
                    counter = 1
                    while os.path.exists(final_path):
                        base, ext = os.path.splitext(suggested)
                        final_path = os.path.join(OUTPUT_FOLDER, f"{base}_{counter}{ext}")
                        counter += 1

                    # progress reporting برای download
                    last_update = 0.0
                    while True:
                        if active_downloads.get(dl_id, {}).get("cancelled"):
                            try:
                                download.delete()
                            except Exception:
                                pass
                            await browser.close()
                            return None, "Cancelled by user", 0

                        try:
                            state = download.state
                            if state == "finished":
                                break
                        except Exception:
                            break

                        try:
                            failure = download.failure
                            if failure:
                                await browser.close()
                                return None, f"Download failed: {failure}", 0
                        except Exception:
                            pass

                        now = time.time()
                        if now - last_update >= 2.0:
                            last_update = now
                            try:
                                await safe_edit(status_msg, "📥 Downloading via browser...")
                            except Exception:
                                pass
                        await asyncio.sleep(1)

                    try:
                        dl_path = await download.path()
                    except Exception as e:
                        logger.warning(f"[DL-PW] download.path() error: {e}")
                        dl_path = None

                    if not dl_path or not os.path.exists(dl_path):
                        await browser.close()
                        return None, "Download path not found", 0

                    file_size = os.path.getsize(dl_path)
                    if file_size < 1024:
                        await browser.close()
                        return None, f"File too small ({file_size} B)", 0

                    import shutil
                    shutil.move(dl_path, final_path)

                    await browser.close()
                    logger.info(f"[DL-PW] DONE | size={human_readable_size(file_size)} | file={final_path}")
                    return final_path, None, file_size

                # ─── مسیر 2: response body ───
                if response_promise in done:
                    logger.info("[DL-PW] Response captured, reading body...")
                    response = await response_promise

                    try:
                        body = await response.body()
                    except Exception as e:
                        logger.warning(f"[DL-PW] response.body() error: {e}")
                        await browser.close()
                        return None, f"Failed to read response body: {e}", 0

                    if not body or len(body) < 1024:
                        await browser.close()
                        return None, f"Response body too small ({len(body) if body else 0} B)", 0

                    file_size = len(body)

                    ct = (response.headers.get("content-type", "") or "").lower()
                    if not os.path.splitext(orig_name)[1]:
                        ct_map = {
                            "application/x-ipa": ".ipa",
                            "application/vnd.android.package-archive": ".apk",
                            "application/zip": ".zip",
                            "application/octet-stream": ".bin",
                            "application/pdf": ".pdf",
                            "video/mp4": ".mp4",
                            "audio/mpeg": ".mp3",
                        }
                        for ct_key, ext_val in ct_map.items():
                            if ct_key in ct:
                                final_path += ext_val
                                break

                    counter = 1
                    while os.path.exists(final_path):
                        base, ext = os.path.splitext(final_path)
                        final_path = os.path.join(OUTPUT_FOLDER, f"{os.path.basename(base)}_{counter}{ext}")
                        counter += 1

                    async with aiofiles.open(final_path, "wb") as f:
                        await f.write(body)

                    await browser.close()
                    logger.info(f"[DL-PW] DONE (response body) | size={human_readable_size(file_size)} | file={final_path}")
                    return final_path, None, file_size

                # ─── هیچ‌کدوم trigger نشد ───
                logger.warning("[DL-PW] Neither download event nor response captured")

                try:
                    content = await page.content()
                    if content and len(content) > 512:
                        if "cloudflare" in content.lower() or "challenge" in content.lower():
                            await browser.close()
                            return None, "Cloudflare challenge blocked the download", 0
                except Exception:
                    pass

                await browser.close()
                return None, "Download did not start in browser", 0

            except asyncio.TimeoutError:
                logger.warning("[DL-PW] Timeout waiting for download/response")
                await browser.close()
                return None, "Browser download timeout (15s)", 0

    except Exception as e:
        logger.error(f"[DL-PW] Error: {e}", exc_info=True)
        return None, str(e)[:100], 0


# ====================== DOWNLOAD WITH PAUSE/CANCEL ======================
async def download_with_controls(
    url: str,
    status_msg: Message,
    dl_id: str,
    referer: Optional[str] = None,
    extra_headers: Optional[dict] = None,
) -> Tuple[Optional[str], Optional[str], int]:
    MAX_RETRIES = 3
    CHUNK_SIZE = 2 * 1024 * 1024  # 2MB chunks

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    if referer:
        headers["Referer"] = referer
        try:
            headers["Origin"] = "/".join(referer.split("/")[:3])
        except Exception:
            pass
    if extra_headers:
        headers.update(extra_headers)

    timeout = ClientTimeout(total=None, connect=30, sock_read=120)
    filepath = ""
    orig_name = ""
    downloaded = 0
    total = 0
    last_update = 0.0
    last_bytes_for_speed = 0
    last_time_for_speed = time.time()
    start_time = time.time()

    if dl_id not in active_downloads:
        active_downloads[dl_id] = {"paused": False, "cancelled": False}

    dl_buttons_pause = [
        [
            Button.inline("⏸ Pause", f"dlpause_{dl_id}"),
            Button.inline("❌ Cancel", f"dlcancel_{dl_id}"),
        ]
    ]
    dl_buttons_resume = [
        [
            Button.inline("▶️ Resume", f"dlresume_{dl_id}"),
            Button.inline("❌ Cancel", f"dlcancel_{dl_id}"),
        ]
    ]

    logger.info(f"[DL] START | url={url[:120]}")
    await safe_edit(status_msg, "📥 Connecting...", buttons=dl_buttons_pause)

    for attempt in range(1, MAX_RETRIES + 1):
        logger.info(
            f"[DL] Attempt {attempt}/{MAX_RETRIES} | downloaded_so_far={human_readable_size(downloaded)}"
        )
        try:
            attempt_headers = headers.copy()
            if downloaded > 0:
                attempt_headers["Range"] = f"bytes={downloaded}-"
                await safe_edit(
                    status_msg,
                    f"🔄 Retry {attempt}/{MAX_RETRIES} — resuming from {human_readable_size(downloaded)}...",
                    buttons=dl_buttons_pause,
                )

            connector = aiohttp.TCPConnector(limit=8, ttl_dns_cache=300, ssl=False)
            async with aiohttp.ClientSession(
                timeout=timeout, connector=connector
            ) as session:
                async with session.get(
                    url, headers=attempt_headers, allow_redirects=True
                ) as response:
                    if response.status == 403:
                        return None, "HTTP_403", 0
                    if response.status not in (200, 206):
                        return None, f"HTTP {response.status}", 0

                    ct = (response.headers.get("Content-Type", "") or "").lower()
                    if "text/html" in ct:
                        return (
                            None,
                            "Got HTML page instead of file (redirected to ad)",
                            0,
                        )

                    if total == 0:
                        content_length = int(response.headers.get("content-length", 0))
                        if response.status == 206:
                            cr = response.headers.get("content-range", "")
                            m = re.search(r"/(\d+)", cr)
                            total = (
                                int(m.group(1)) if m else content_length + downloaded
                            )
                        else:
                            total = content_length
                        if total > MAX_FILE_SIZE_MB * 1024 * 1024:
                            return (
                                None,
                                f"File too large ({human_readable_size(total)})",
                                0,
                            )

                        # Detect original filename
                        orig_name = ""
                        cd = response.headers.get("Content-Disposition", "")
                        if "filename=" in cd:
                            fm = re.search(r'filename="?([^";]+)', cd)
                            if fm:
                                orig_name = fm.group(1).strip()
                        if not orig_name:
                            url_path = url.split("?")[0].rstrip("/")
                            orig_name = os.path.basename(url_path)
                        if not orig_name:
                            orig_name = f"file_{int(time.time())}"
                        orig_name = re.sub(r"[^\w\.\-_\(\) ]", "_", orig_name)
                        if len(orig_name) > 80:
                            orig_name = orig_name[:80]

                        # Detect extension
                        ext = os.path.splitext(orig_name)[1].lower()
                        if not ext:
                            ct = (
                                response.headers.get("Content-Type", "") or ""
                            ).lower()
                            ct_map = {
                                # Video
                                "video/mp4": ".mp4",
                                "video/x-matroska": ".mkv",
                                "video/webm": ".webm",
                                "video/avi": ".avi",
                                "video/x-msvideo": ".avi",
                                "video/quicktime": ".mov",
                                "video/x-ms-wmv": ".wmv",
                                "video/x-flv": ".flv",
                                "video/mpeg": ".mpg",
                                "video/3gpp": ".3gp",
                                "video/3gpp2": ".3g2",
                                "video/ogg": ".ogv",
                                "video/mp2t": ".ts",
                                "video/vnd.dlna.mpeg-tts": ".ts",
                                "video/x-m4v": ".m4v",
                                "video/x-ms-asf": ".asf",
                                # Audio
                                "audio/mpeg": ".mp3",
                                "audio/mp4": ".m4a",
                                "audio/ogg": ".ogg",
                                "audio/wav": ".wav",
                                "audio/x-wav": ".wav",
                                "audio/flac": ".flac",
                                "audio/aac": ".aac",
                                "audio/x-aac": ".aac",
                                "audio/x-ms-wma": ".wma",
                                "audio/opus": ".opus",
                                "audio/ape": ".ape",
                                "audio/ac3": ".ac3",
                                "audio/x-ac3": ".ac3",
                                "audio/amr": ".amr",
                                "audio/midi": ".mid",
                                "audio/x-midi": ".mid",
                                "audio/aiff": ".aiff",
                                "audio/x-aiff": ".aiff",
                                "audio/basic": ".au",
                                "audio/webm": ".weba",
                                # Image
                                "image/jpeg": ".jpg",
                                "image/png": ".png",
                                "image/gif": ".gif",
                                "image/webp": ".webp",
                                "image/bmp": ".bmp",
                                "image/tiff": ".tiff",
                                "image/svg+xml": ".svg",
                                "image/x-icon": ".ico",
                                "image/vnd.microsoft.icon": ".ico",
                                "image/vnd.adobe.photoshop": ".psd",
                                "image/x-canon-cr2": ".cr2",
                                "image/x-nikon-nef": ".nef",
                                "image/heic": ".heic",
                                "image/heif": ".heif",
                                "image/avif": ".avif",
                                "image/jxr": ".jxr",
                                # Document
                                "application/pdf": ".pdf",
                                "application/msword": ".doc",
                                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
                                "application/vnd.ms-excel": ".xls",
                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
                                "application/vnd.ms-powerpoint": ".ppt",
                                "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
                                "application/vnd.oasis.opendocument.text": ".odt",
                                "application/vnd.oasis.opendocument.spreadsheet": ".ods",
                                "application/vnd.oasis.opendocument.presentation": ".odp",
                                "application/rtf": ".rtf",
                                "text/plain": ".txt",
                                "text/csv": ".csv",
                                "text/tab-separated-values": ".tsv",
                                "application/epub+zip": ".epub",
                                "application/x-mobipocket-ebook": ".mobi",
                                "application/x-fictionbook+xml": ".fb2",
                                "image/vnd.djvu": ".djvu",
                                # Archive / compressed
                                "application/zip": ".zip",
                                "application/x-rar-compressed": ".rar",
                                "application/x-7z-compressed": ".7z",
                                "application/x-tar": ".tar",
                                "application/gzip": ".gz",
                                "application/x-bzip2": ".bz2",
                                "application/x-xz": ".xz",
                                "application/x-lzma": ".lzma",
                                "application/x-lzip": ".lz",
                                "application/x-iso9660-image": ".iso",
                                "application/x-apple-diskimage": ".dmg",
                                "application/x-cd-image": ".iso",
                                "application/java-archive": ".jar",
                                # Executable / installer
                                "application/vnd.android.package-archive": ".apk",
                                "application/x-ipa": ".ipa",
                                "application/vnd.apple.installer+xml": ".ipa",
                                "application/x-msdownload": ".exe",
                                "application/x-msi": ".msi",
                                "application/x-msdos-program": ".exe",
                                "application/x-elf": ".elf",
                                "application/x-sharedlib": ".so",
                                "application/x-executable": ".bin",
                                "application/x-debian-package": ".deb",
                                "application/x-rpm": ".rpm",
                                "application/x-appimage": ".appimage",
                                # Font
                                "font/ttf": ".ttf",
                                "font/otf": ".otf",
                                "font/woff": ".woff",
                                "font/woff2": ".woff2",
                                "application/x-font-ttf": ".ttf",
                                "application/x-font-otf": ".otf",
                                # Torrent
                                "application/x-bittorrent": ".torrent",
                                # Subtitles
                                "text/vtt": ".vtt",
                                "text/x-srt": ".srt",
                                "application/x-subrip": ".srt",
                                "text/x-ass": ".ass",
                                # 3D
                                "model/stl": ".stl",
                                "model/obj": ".obj",
                                "application/sla": ".stl",
                                # Fallback binary
                                "application/octet-stream": ".bin",
                            }
                            for mtype, mext in ct_map.items():
                                if mtype in ct:
                                    ext = mext
                                    break
                        if not ext:
                            ext = ".mp4"
                        orig_name = os.path.splitext(orig_name)[0] + ext

                        filepath = os.path.join(OUTPUT_FOLDER, orig_name)
                        # Avoid overwrite: add suffix if exists
                        counter = 1
                        while os.path.exists(filepath):
                            base = os.path.splitext(orig_name)[0]
                            filepath = os.path.join(
                                OUTPUT_FOLDER, f"{base}_{counter}{ext}"
                            )
                            counter += 1

                    write_mode = "ab" if downloaded > 0 else "wb"
                    async with aiofiles.open(filepath, write_mode) as f:
                        async for chunk in response.content.iter_chunked(CHUNK_SIZE):
                            if active_downloads.get(dl_id, {}).get("cancelled"):
                                try:
                                    if os.path.exists(filepath):
                                        os.remove(filepath)
                                except Exception:
                                    pass
                                try:
                                    await status_msg.edit(
                                        "🚫 Download cancelled.", buttons=None
                                    )
                                except Exception:
                                    pass
                                return None, "Cancelled by user", 0

                            if active_downloads.get(dl_id, {}).get("paused"):
                                paused_text = build_progress_text(
                                    "⏸ Paused", downloaded, total, 0, start_time
                                )
                                await safe_edit(
                                    status_msg, paused_text, buttons=dl_buttons_resume
                                )
                                while active_downloads.get(dl_id, {}).get("paused"):
                                    if active_downloads.get(dl_id, {}).get("cancelled"):
                                        try:
                                            if os.path.exists(filepath):
                                                os.remove(filepath)
                                        except Exception:
                                            pass
                                        try:
                                            await status_msg.edit(
                                                "🚫 Download cancelled.", buttons=None
                                            )
                                        except Exception:
                                            pass
                                        return None, "Cancelled by user", 0
                                    await asyncio.sleep(0.5)
                                last_update = 0.0

                            await f.write(chunk)
                            downloaded += len(chunk)

                            now = time.time()
                            if now - last_update >= 1.5 and downloaded != total:
                                dt = now - last_time_for_speed
                                speed = (
                                    (downloaded - last_bytes_for_speed) / dt
                                    if dt > 0
                                    else 0
                                )
                                last_bytes_for_speed = downloaded
                                last_time_for_speed = now
                                last_update = now
                                text = build_progress_text(
                                    "📥 Downloading",
                                    downloaded,
                                    total,
                                    speed,
                                    start_time,
                                )
                                await safe_edit(
                                    status_msg, text, buttons=dl_buttons_pause
                                )

            active_downloads.pop(dl_id, None)
            logger.info(
                f"[DL] DONE | size={human_readable_size(downloaded)} | file={filepath}"
            )
            # Reject tiny files — likely error/placeholder, not real video
            if downloaded < 1024:
                try:
                    os.remove(filepath)
                except Exception:
                    pass
                return None, f"File too small ({downloaded} B) — not a real video", 0
            # Check first bytes for HTML content (ad/error page disguised as video)
            try:
                with open(filepath, "rb") as _f:
                    head = _f.read(512)
                if head.lstrip(b"\xef\xbb\xbf")[:1] == b"<":
                    try:
                        os.remove(filepath)
                    except Exception:
                        pass
                    return None, "Downloaded HTML page instead of video", 0
            except Exception:
                pass
            try:
                await status_msg.edit(
                    "✅ Download complete!", parse_mode="markdown", buttons=None
                )
            except Exception:
                pass
            return filepath, None, downloaded

        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            aiohttp.ServerDisconnectedError,
        ) as e:
            logger.warning(f"Download attempt {attempt} failed: {e}")
            if attempt == MAX_RETRIES:
                active_downloads.pop(dl_id, None)
                try:
                    if os.path.exists(filepath):
                        os.remove(filepath)
                except Exception:
                    pass
                return None, f"Failed after {MAX_RETRIES} retries: {str(e)[:80]}", 0
            await asyncio.sleep(3)
        except Exception as e:
            logger.error(f"Unexpected download error: {e}")
            active_downloads.pop(dl_id, None)
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except Exception:
                pass
            return None, str(e)[:100], 0

    active_downloads.pop(dl_id, None)
    return None, "Download failed", 0


# ====================== PAUSE / RESUME / CANCEL CALLBACKS ======================
# FIX: pause و resume دو callback جدا دارن — قبلاً toggle بود که race condition داشت


async def dl_pause_callback(event):
    dl_id = event.data.decode().replace("dlpause_", "")
    if dl_id not in active_downloads:
        return await event.answer("No active download found.", alert=True)
    active_downloads[dl_id]["paused"] = True
    await event.answer("⏸ Paused!", alert=False)


async def dl_resume_callback(event):
    dl_id = event.data.decode().replace("dlresume_", "")
    if dl_id not in active_downloads:
        return await event.answer("No active download found.", alert=True)
    active_downloads[dl_id]["paused"] = False
    await event.answer("▶️ Resumed!", alert=False)


async def dl_cancel_callback(event):
    dl_id = event.data.decode().replace("dlcancel_", "")
    if dl_id not in active_downloads:
        return await event.answer("No active download found.", alert=True)
    active_downloads[dl_id]["cancelled"] = True
    active_downloads[dl_id]["paused"] = False
    await event.answer("❌ Cancelling...", alert=False)
    try:
        await event.edit(buttons=None)
    except Exception:
        pass


async def ul_cancel_callback(event):
    ul_id = event.data.decode().replace("ulcancel_", "")
    if ul_id not in active_uploads:
        return await event.answer("No active upload found.", alert=True)
    active_uploads[ul_id]["cancelled"] = True
    await event.answer("❌ Cancelling upload...", alert=False)
    try:
        await event.edit(buttons=None)
    except Exception:
        pass


# ====================== UPLOAD WITH PROGRESS ======================
async def get_video_thumbnail(filepath: str) -> Optional[str]:
    """یه فریم از وسط ویدیو به عنوان thumbnail می‌گیره"""
    try:
        thumb_path = filepath + "_thumb.jpg"
        # مدت ویدیو رو بگیر تا فریم از وسط باشه
        probe = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            filepath,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await probe.communicate()
        duration = 0.0
        try:
            duration = float(
                json.loads(stdout.decode()).get("format", {}).get("duration", 0)
            )
        except Exception:
            pass
        seek_time = max(duration / 2, 1) if duration > 2 else 0

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-ss",
            str(seek_time),
            "-i",
            filepath,
            "-vframes",
            "1",
            "-q:v",
            "2",
            "-vf",
            "scale=320:-1",
            thumb_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
            return thumb_path
    except Exception:
        pass
    return None


async def send_file_with_progress(
    client,
    chat_id: int,
    filepath: str,
    caption: str,
    status_msg: Message,
    buttons=None,
    supports_streaming: bool = True,
    thumb_filepath: str = None,
    ul_id: str = None,
    force_document: bool = False,
):
    file_size = os.path.getsize(filepath)
    start_time = time.time()
    last_update = [0.0]
    last_bytes = [0]
    last_time = [start_time]
    ext = os.path.splitext(filepath)[1].lower()

    if ul_id:
        if ul_id not in active_uploads:
            active_uploads[ul_id] = {"paused": False, "cancelled": False}

    duration, width, height = await get_video_info(filepath)
    is_video = duration is not None and duration > 0 and width > 0 and height > 0
    is_audio = ext in (".mp3", ".m4a", ".ogg", ".wav", ".flac", ".aac", ".wma", ".opus")

    # ---- Preprocessing ----
    orig_filepath = filepath
    tmp_files = []
    thumb_path = None

    try:
        # ویدیو: moov atom رو ببر اول فایل (Fast Start) برای استریمینگ
        # اما اگه force_document=True هست، skip کن چون faststart زیرنویس softsub رو حذف می‌کنه
        if is_video and not force_document:
            fast_path = filepath + "_faststart.mp4"
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-i",
                filepath,
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                "-y",
                fast_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if os.path.exists(fast_path) and os.path.getsize(fast_path) > 0:
                filepath = fast_path
                tmp_files.append(fast_path)

        # صدا: استخراج کاور از تگ‌های ID3
        audio_title = ""
        audio_performer = ""
        if is_audio and not thumb_filepath:
            cover_path = filepath + "_cover.jpg"
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-i",
                filepath,
                "-an",
                "-vcodec",
                "copy",
                "-y",
                cover_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if os.path.exists(cover_path) and os.path.getsize(cover_path) > 0:
                thumb_filepath = cover_path
                tmp_files.append(cover_path)

        # متادیتای صدا (عنوان و هنرمند)
        if is_audio:
            probe = await asyncio.create_subprocess_exec(
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                orig_filepath,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await probe.communicate()
            try:
                tags = json.loads(out.decode()).get("format", {}).get("tags", {})
                audio_title = tags.get("title", "")
                audio_performer = tags.get("artist", "") or tags.get("TPE1", "")
            except Exception:
                pass

        thumb_path = thumb_filepath or (
            await get_video_thumbnail(filepath) if is_video else None
        )

        ul_buttons = None
        if ul_id:
            ul_buttons = [Button.inline("❌ Cancel", f"ulcancel_{ul_id}")]

        async def progress_cb(current: int, total: int):
            if ul_id and active_uploads.get(ul_id, {}).get("cancelled"):
                raise asyncio.CancelledError("Upload cancelled by user")
            now = time.time()
            if now - last_update[0] < 3.0 and current != total:
                return
            last_update[0] = now
            dt = now - last_time[0]
            speed = (current - last_bytes[0]) / dt if dt > 0 else 0
            last_bytes[0] = current
            last_time[0] = now
            text = build_progress_text(
                "📤 Uploading", current, total, speed, start_time
            )
            asyncio.ensure_future(_safe_edit_text(status_msg, text, ul_buttons))

        sent = None
        try:
            with open(filepath, "rb") as f:
                uploaded = await asyncio.wait_for(
                    fast_upload_file(
                        client, f, progress_callback=progress_cb, connection_count=15
                    ),
                    timeout=1200,  # 20 min per part
                )
        except asyncio.TimeoutError:
            try:
                await status_msg.edit("🚫 Upload timed out (20min).", buttons=None)
            except Exception:
                pass
            raise
        except asyncio.CancelledError:
            try:
                await status_msg.edit("🚫 Upload cancelled.", buttons=None)
            except Exception:
                pass
            raise

        if is_video and not force_document:
            duration_int = int(duration) if duration else 0
            attributes, mime_type = utils.get_attributes(
                filepath,
                attributes=[
                    DocumentAttributeVideo(
                        duration=duration_int,
                        w=width if width else 0,
                        h=height if height else 0,
                        supports_streaming=supports_streaming,
                    )
                ],
            )
            thumb_input = None
            if thumb_path and os.path.exists(thumb_path):
                with open(thumb_path, "rb") as tf:
                    thumb_input = await fast_upload_file(client, tf)
            media = InputMediaUploadedDocument(
                file=uploaded,
                mime_type=mime_type,
                attributes=attributes,
                thumb=thumb_input,
                force_file=False,
            )
        elif is_video and force_document:
            # Send as plain document (no streaming, no video player)
            # This preserves softsub subtitles inside the file
            attributes, mime_type = utils.get_attributes(filepath)
            thumb_input = None
            if thumb_path and os.path.exists(thumb_path):
                with open(thumb_path, "rb") as tf:
                    thumb_input = await fast_upload_file(client, tf)
            media = InputMediaUploadedDocument(
                file=uploaded,
                mime_type=mime_type,
                attributes=attributes,
                thumb=thumb_input,
                force_file=True,
            )
        elif is_audio:
            audio_dur = int(duration) if duration and duration > 0 else 0
            attributes, mime_type = utils.get_attributes(
                filepath,
                attributes=[
                    DocumentAttributeAudio(
                        duration=audio_dur,
                        voice=False,
                        title=audio_title or None,
                        performer=audio_performer or None,
                    )
                ],
            )
            thumb_input = None
            if thumb_path and os.path.exists(thumb_path):
                with open(thumb_path, "rb") as tf:
                    thumb_input = await fast_upload_file(client, tf)
            media = InputMediaUploadedDocument(
                file=uploaded,
                mime_type=mime_type,
                attributes=attributes,
                thumb=thumb_input,
                force_file=False,
            )
        else:
            attributes, mime_type = utils.get_attributes(filepath)
            is_video_ext = ext in (".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".3gp")
            media = InputMediaUploadedDocument(
                file=uploaded,
                mime_type=mime_type,
                attributes=attributes,
                force_file=not is_video_ext,
            )

        try:
            sent = await client.send_file(
                chat_id,
                media,
                caption=caption,
                buttons=buttons,
                parse_mode="markdown",
            )
        except Exception:
            sent = await client.send_file(
                chat_id,
                media,
                caption=caption,
                buttons=buttons,
                parse_mode=None,
            )
    finally:
        if thumb_path and os.path.exists(thumb_path) and thumb_path != thumb_filepath:
            try:
                os.remove(thumb_path)
            except Exception:
                pass
        for fp in tmp_files:
            try:
                if os.path.exists(fp):
                    os.remove(fp)
            except Exception:
                pass

    if status_msg:
        try:
            await status_msg.delete()
        except Exception:
            pass

    return sent


async def _safe_edit_text(msg: Message, text: str, buttons=None):
    try:
        await msg.edit(text, parse_mode="markdown", buttons=buttons)
    except Exception:
        pass


# ====================== DOWNLOAD AND SEND ======================
async def do_download_and_send(
    event,
    status_msg,
    direct_url: str,
    source_url: str,
    extra_headers: Optional[dict] = None,
    title: str = "",
) -> bool:
    dl_id = f"dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}

    filepath, dl_error, final_size = await download_with_controls(
        direct_url, status_msg, dl_id, referer=source_url, extra_headers=extra_headers
    )

    # FIX: 403 → auto-retry via dirpy
    if dl_error == "HTTP_403":
        if is_direct_file_url(direct_url):
            await safe_edit(status_msg, "🔄 403 — retrying via real browser...")
            dl_id3 = f"dl_{event.chat_id}_{event.id}_{int(time.time())}_pw2"
            active_downloads[dl_id3] = {"paused": False, "cancelled": False}
            filepath, dl_error, final_size = await download_with_playwright(
                direct_url, status_msg, dl_id3
            )
            if dl_error or not filepath:
                await safe_edit(
                    status_msg,
                    "❌ 403 Forbidden — سرور دانلود توسط ربات را مسدود کرده است.\n"
                    "لینک در مرورگر کار می‌کند اما CDN درخواست‌های خودکار را رد می‌کند.",
                )
                return False
        else:
            await safe_edit(status_msg, "🔄 403 received — extracting via Dirpy...")
        (
            found_urls,
            session_headers,
            intercept_err,
            page_title,
        ) = await extract_video_url_smart(source_url, status_msg)
        if not found_urls:
            await safe_edit(
                status_msg, f"❌ Could not extract via Dirpy either:\n{intercept_err}"
            )
            return False
        if page_title and not title:
            title = page_title
        direct_url = found_urls[0]
        extra_headers = session_headers
        dl_id2 = f"dl_{event.chat_id}_{event.id}_{int(time.time())}_r"
        active_downloads[dl_id2] = {"paused": False, "cancelled": False}
        filepath, dl_error, final_size = await download_with_controls(
            direct_url,
            status_msg,
            dl_id2,
            referer=source_url,
            extra_headers=extra_headers,
        )

    if dl_error or not filepath:
        if dl_error != "Cancelled by user":
            await safe_edit(status_msg, f"❌ Download failed: {dl_error}")
        return False

    await safe_edit(status_msg, "📤 Uploading...")
    try:
        ext = os.path.splitext(filepath)[1].lower()
        vid_duration, vw, vh = await get_video_info(filepath)
        is_video = vid_duration is not None and vid_duration > 0 and vw > 0 and vh > 0

        if is_video:
            mins, secs = divmod(int(vid_duration), 60)
            hours, mins = divmod(mins, 60)
            if hours > 0:
                dur_str = f"\n⏱ Duration: {hours}:{mins:02d}:{secs:02d}"
            else:
                dur_str = f"\n⏱ Duration: {mins}:{secs:02d}"
            caption_start = f"🎬 {title}" if title else "🎬 **Video Downloaded**"
        else:
            dur_str = ""
            fname = os.path.basename(filepath)
            caption_start = f"📄 **{fname}**" if not title else f"📄 **{title}**"

        gh_line = ""
        if GITHUB_ENABLED:
            await safe_edit(status_msg, "☁️ Uploading to GitHub...")
            gh_url = await maybe_upload_github(
                event.client, event.chat_id, filepath, final_size
            )
            if gh_url:
                gh_line = f"\n☁️ [GitHub DL]({gh_url})"

        ul_id = f"ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await send_file_with_progress(
            client=event.client,
            chat_id=event.chat_id,
            filepath=filepath,
            caption=(
                f"{caption_start}\n"
                f"📦 Size: {human_readable_size(final_size)}"
                f"{dur_str}\n"
                f"🔗 [Source]({source_url})\n"
                f"⬇️ [DW Link]({direct_url})"
                f"{gh_line}"
            ),
            status_msg=status_msg,
            supports_streaming=True,
            ul_id=ul_id,
        )
        try:
            os.remove(filepath)
        except Exception:
            pass
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
        return False
    except Exception as e:
        logger.error(f"Upload error: {e}")
        await safe_edit(status_msg, f"❌ Upload failed: {str(e)[:100]}")
        return False
    return True


# ====================== GET FILE SIZE ======================
async def get_file_size(url: str) -> int:
    try:
        timeout = ClientTimeout(connect=10, sock_read=10, total=15)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.head(
                url, headers=headers, allow_redirects=True, ssl=False
            ) as resp:
                return int(resp.headers.get("content-length", 0))
    except Exception:
        return 0


def _url_label(url: str, size: int, index: int) -> str:
    u = url.lower()
    quality = "Unknown"
    for q in ["2160p", "1080p", "720p", "480p", "360p", "240p", "4k", "hd", "sd"]:
        if q in u:
            quality = q.upper()
            break
    sz_str = human_readable_size(size) if size > 0 else "? MB"
    try:
        from urllib.parse import urlparse

        domain = urlparse(url).netloc.replace("www.", "")[:20]
    except Exception:
        domain = f"Link {index + 1}"
    return f"#{index + 1} {quality} • {sz_str} • {domain}"


# ====================== VIDEO URL EXTRACTOR ======================
SKIP_KEYWORDS = [
    "thumb",
    "preview",
    "poster",
    "banner",
    "logo",
    "icon",
    "sprite",
    "storyboard",
    "tracking",
    "analytics",
    "pixel",
    "ad/",
    "/ads/",
]
MIN_SIZE = 2 * 1024 * 1024  # 2MB


def _browser_args() -> list:
    """آرگومان‌های chromium برای مصرف RAM کم."""
    return [
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--disable-software-rasterizer",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-translate",
        "--hide-scrollbars",
        "--mute-audio",
        "--no-first-run",
        "--js-flags=--max-old-space-size=96",
    ]


KNOWN_CDN_DOMAINS = [
    "rdtcdn.com",
    "phncdn.com",
    "xnxx-cdn.com",
    "media4.luxuretv",
    "media.luxuretv",
    "rule34.xxx",
    "rule34video",
    "kv-ph.",
    "ev-ph.",
    "di-ph.",
    "googlevideo.com",
    "videoplayback",
    "p300cdn",
    "x-tg.tube/get_file",
]


def _should_capture(url: str, content_type: str = "", content_length: int = 0) -> bool:
    ul = url.lower()
    if any(k in ul for k in SKIP_KEYWORDS):
        return False
    if "video/" in content_type and content_length > MIN_SIZE:
        return True
    is_known_cdn = any(d in ul for d in KNOWN_CDN_DOMAINS)
    has_video_ext = (
        ".mp4" in ul or ".webm" in ul or "videoplayback" in ul or "/get_file/" in ul
    )
    if is_known_cdn and has_video_ext:
        if "rdtcdn.com" in ul or "phncdn.com" in ul:
            quality_signals = [
                "_720p_",
                "_1080p_",
                "_480p_",
                "_240p_",
                "_2160p_",
                "_4000k_",
                "_2000k_",
                "_1000k_",
                "_500k_",
                "_800k_",
                "p_720",
                "p_1080",
                "p_480",
                "p_240",
            ]
            return any(q in ul for q in quality_signals)
        return True
    return False


def _extract_from_html(html: str, seen: set, captured_urls: list, label: str):
    for m in re.findall(r"https?://[^\x22\x27<>\s]+", html):
        if _should_capture(m):
            norm = m.split("?")[0]
            if norm not in seen:
                seen.add(norm)
                captured_urls.append(m)
                logger.info(f"[{label}-URL] {m[:180]}")

    kv_patterns = [
        r"video_url\s*:\s*[\x22\x27](?:function/\d+/)?(https?://[^\x22\x27\s]+)[\x22\x27]",
        r"video_url_hd\s*:\s*[\x22\x27](?:function/\d+/)?(https?://[^\x22\x27\s]+)[\x22\x27]",
        r"event_reporting2\s*:\s*[\x22\x27]([^\x22\x27\s]+/get_file/[^\x22\x27\s]+)[\x22\x27]",
        r"[\x22\x27](?:file|src|url|video_url)[\x22\x27\s]*:\s*[\x22\x27](?:function/\d+/)?(https?://[^\x22\x27\s]+\.mp4[^\x22\x27\s]*)[\x22\x27]",
    ]
    for pat in kv_patterns:
        for m in re.findall(pat, html, re.IGNORECASE):
            url = m.rstrip("/")
            if not url.startswith("http"):
                continue
            norm = url.split("?")[0]
            if norm not in seen and not any(k in url.lower() for k in SKIP_KEYWORDS):
                seen.add(norm)
                captured_urls.append(url)
                logger.info(f"[{label}-KV] {url[:180]}")

    for m in re.findall(
        r"[\x22\x27]([^\x22\x27]*?/get_file/[^\x22\x27]+\.mp4[^\x22\x27]*)[\x22\x27]",
        html,
    ):
        if m.startswith("http"):
            url = m.rstrip("/")
            norm = url.split("?")[0]
            if norm not in seen:
                seen.add(norm)
                captured_urls.append(url)
                logger.info(f"[{label}-GETFILE] {url[:180]}")


async def _collect_from_page(page, label: str, captured_urls: list, seen: set):
    async def on_response(response):
        try:
            ct = response.headers.get("content-type", "")
            cl = int(response.headers.get("content-length", 0))
            ru = response.url
            if _should_capture(ru, ct, cl):
                norm = ru.split("?")[0]
                if norm not in seen:
                    seen.add(norm)
                    captured_urls.append(ru)
                    logger.info(f"[{label}] {ru[:180]}")
        except Exception:
            pass

    page.on("response", on_response)

    try:
        html = await page.content()
        _extract_from_html(html, seen, captured_urls, label + "-FAST")
    except Exception:
        pass

    if not captured_urls:
        await page.wait_for_timeout(5000)
        try:
            html = await page.content()
            _extract_from_html(html, seen, captured_urls, label + "-AFTER5S")
        except Exception:
            pass

    if not captured_urls:
        await page.evaluate(
            '() => { try { document.querySelector("video")?.play(); } catch(e){} }'
        )
        await page.wait_for_timeout(6000)
        try:
            html = await page.content()
            _extract_from_html(html, seen, captured_urls, label + "-AFTERPLAY")
        except Exception:
            pass


async def extract_video_url_smart(
    video_url: str, status_msg: Message
) -> Tuple[list, dict, Optional[str], str]:
    async with async_playwright() as p:
        browser = None
        captured_urls: list = []
        seen: set = set()
        session_headers: dict = {}
        video_title: str = ""

        try:
            browser = await p.chromium.launch(headless=True, args=_browser_args())
            logger.info(f"[PLAYWRIGHT] Browser launched")

            async def make_context():
                return await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
                    viewport={"width": 1280, "height": 720},
                )

            # مرحله ۱: Dirpy
            await safe_edit(status_msg, "🔗 Opening Dirpy Studio...")
            ctx1 = await make_context()
            page1 = await ctx1.new_page()
            dirpy_url = f"https://dirpy.com/studio?url={quote(video_url)}"
            try:
                logger.info(f"[PLAYWRIGHT] Opening Dirpy: {dirpy_url[:120]}")
                await page1.goto(
                    dirpy_url, wait_until="domcontentloaded", timeout=60000
                )
                await _collect_from_page(page1, "DIRPY", captured_urls, seen)
                try:
                    raw = await page1.title()
                    video_title = raw.replace("Dirpy Studio", "").strip(" -|").strip()
                except:
                    pass
                if captured_urls:
                    session_headers = {"Referer": video_url}
            except Exception as e:
                logger.warning(f"Dirpy page error: {e}")
            finally:
                await page1.close()
                await ctx1.close()

            # مرحله ۲: Direct site fallback
            if not captured_urls:
                await safe_edit(
                    status_msg, "🌐 Dirpy failed — trying direct site extraction..."
                )
                ctx2 = await make_context()
                page2 = await ctx2.new_page()
                try:
                    logger.info(f"[PLAYWRIGHT] Direct goto: {video_url[:120]}")

                    async def handle_dialog(dialog):
                        await dialog.accept()

                    page2.on("dialog", handle_dialog)

                    await page2.goto(
                        video_url, wait_until="domcontentloaded", timeout=60000
                    )

                    age_selectors = [
                        'button:has-text("I AM 18")',
                        'button:has-text("ENTER")',
                        'button:has-text("Yes")',
                        ".age-gate button",
                        "button.y",
                        'button:has-text("Enter")',
                        'button:has-text("Confirm")',
                        'a:has-text("I AM 18")',
                        'a:has-text("ENTER")',
                    ]
                    for sel in age_selectors:
                        try:
                            el = page2.locator(sel).first
                            if await el.is_visible(timeout=800):
                                await el.click()
                                await asyncio.sleep(1.5)
                                break
                        except Exception:
                            continue

                    await _collect_from_page(page2, "DIRECT", captured_urls, seen)

                    if captured_urls:
                        raw_cookies = await ctx2.cookies()
                        cookie_str = "; ".join(
                            f"{c['name']}={c['value']}"
                            for c in raw_cookies
                            if video_url.split("/")[2].replace("www.", "")
                            in c.get("domain", "")
                            or c.get("domain", "").lstrip(".") in video_url
                        )
                        session_headers = {
                            "Referer": video_url,
                            "Origin": "/".join(video_url.split("/")[:3]),
                        }
                        if cookie_str:
                            session_headers["Cookie"] = cookie_str

                except Exception as e:
                    logger.warning(f"Direct page error: {e}")
                finally:
                    await page2.close()
                    await ctx2.close()

            if captured_urls:
                return captured_urls, session_headers, None, video_title
            return (
                [],
                {},
                "Could not capture video link via Dirpy or direct extraction",
                video_title,
            )

        except Exception as e:
            logger.error(f"Extractor error: {e}")
            return [], {}, str(e), ""
        finally:
            if browser:
                await browser.close()


# ====================== HTML TO PDF ======================
async def html_to_pdf(
    url: str, status_msg: Message
) -> Tuple[Optional[str], Optional[str], int]:
    async with async_playwright() as p:
        browser = None
        try:
            browser = await p.chromium.launch(headless=True, args=_browser_args())
            page = await browser.new_page(viewport={"width": 1280, "height": 900})
            await safe_edit(status_msg, "🌐 Loading page...")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=120000)
            except Exception:
                pass
            try:
                for sel in [
                    'button:has-text("I AM 18")',
                    'button:has-text("ENTER")',
                    'button:has-text("Yes")',
                    ".age-gate button",
                    "button.y",
                ]:
                    try:
                        el = page.locator(sel).first
                        if await el.is_visible(timeout=1000):
                            await el.click()
                            await asyncio.sleep(2)
                            break
                    except Exception:
                        continue
            except Exception:
                pass
            await safe_edit(status_msg, "📜 Scrolling to load all images...")
            await page.evaluate("""
                async () => {
                    const delay = ms => new Promise(r => setTimeout(r, ms));
                    const totalHeight = document.body.scrollHeight;
                    const step = Math.floor(window.innerHeight * 0.8);
                    let current = 0;
                    while (current < totalHeight) {
                        window.scrollTo(0, current);
                        await delay(300);
                        current += step;
                    }
                    window.scrollTo(0, totalHeight);
                    await delay(500);
                }
            """)
            await asyncio.sleep(4)
            await safe_edit(status_msg, "📄 Rendering PDF...")
            filepath = os.path.join(OUTPUT_FOLDER, f"pdf_{int(time.time())}.pdf")
            await page.pdf(
                path=filepath,
                format="A4",
                print_background=True,
                margin={"top": "10mm", "bottom": "10mm", "left": "8mm", "right": "8mm"},
            )
            return filepath, None, os.path.getsize(filepath)
        except Exception as e:
            err = str(e)
            if "connection closed" in err.lower() or "browser" in err.lower():
                return None, "PDF Error: Browser crashed. Please try again.", 0
            return None, f"PDF Error: {err[:80]}", 0
        finally:
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass


# ====================== CAPTURE MHTML ======================
async def capture_mhtml(
    url: str, status_msg: Message
) -> Tuple[Optional[str], Optional[str], int]:
    async with async_playwright() as p:
        browser = None
        try:
            await safe_edit(status_msg, "🌐 Capturing full webpage as MHTML...")
            browser = await p.chromium.launch(headless=True, args=_browser_args())
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(url, wait_until="networkidle", timeout=60000)
            await asyncio.sleep(3)
            cdp = await context.new_cdp_session(page)
            result = await cdp.send("Page.captureSnapshot", {"format": "mhtml"})
            mhtml_data = result.get("data", "")
            if not mhtml_data:
                return None, "Failed to capture MHTML", 0
            filepath = os.path.join(OUTPUT_FOLDER, f"page_{int(time.time())}.mhtml")
            async with aiofiles.open(filepath, "w", encoding="utf-8") as f:
                await f.write(mhtml_data)
            return filepath, None, os.path.getsize(filepath)
        except Exception as e:
            return None, f"MHTML Error: {str(e)[:80]}", 0
        finally:
            if browser:
                await browser.close()


# ====================== VIDEO COMPRESSION ======================
async def _run_ffmpeg(args: list) -> Tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    return proc.returncode, stderr.decode(errors="replace")


async def get_video_info(input_path: str) -> Tuple[Optional[float], int, int]:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        input_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return None, 0, 0
    try:
        info = json.loads(stdout.decode())
        dur = float(info.get("format", {}).get("duration", 0))
        w, h = 0, 0
        for s in info.get("streams", []):
            if s.get("codec_type") == "video":
                w = int(s.get("width", 0))
                h = int(s.get("height", 0))
                if not dur:
                    dur = float(s.get("duration", 0))
                break
        return dur or None, w, h
    except Exception:
        return None, 0, 0


PERSIAN_SUB_TAGS = {"fa", "farsi", "persian", "parsi"}


async def extract_persian_subtitle(
    video_path: str,
) -> Tuple[Optional[str], Optional[dict]]:
    """
    با ffprobe چک میکنه ویدیو soft subtitle فارسی داره یا نه.
    اگر زیرنویس فارسی داشت: برمیگردونه (مسیر srt, None)
    اگر زیرنویس غیرفارسی داشت: برمیگردونه (None, dict info)
    اگر هیچ زیرنویسی نداشت: برمیگردونه (None, None)
    """
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_streams",
        "-select_streams",
        "s",
        video_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return None, None

    try:
        info = json.loads(stdout.decode())
        streams = info.get("streams", [])
    except Exception:
        return None, None

    target_index = None
    first_sub = None
    for s in streams:
        tags = s.get("tags", {})
        lang = (tags.get("language") or tags.get("title") or "").lower().strip()
        if first_sub is None:
            first_sub = {
                "index": s.get("index"),
                "lang": lang or "unknown",
                "title": tags.get("title", ""),
                "codec": s.get("codec_name", ""),
            }
        if any(tag in lang for tag in PERSIAN_SUB_TAGS):
            target_index = s.get("index")
            break

    if target_index is None:
        if first_sub is None:
            return None, None
        return None, first_sub

    out_srt = os.path.join(OUTPUT_FOLDER, f"extracted_sub_{int(time.time())}.srt")
    proc2 = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-map",
        f"0:{target_index}",
        "-c:s",
        "srt",
        out_srt,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc2.communicate()

    if os.path.exists(out_srt) and os.path.getsize(out_srt) > 0:
        return out_srt, None
    return None, None

    try:
        info = json.loads(stdout.decode())
        streams = info.get("streams", [])
    except Exception:
        return None

    target_index = None
    first_sub_index = None
    for s in streams:
        tags = s.get("tags", {})
        lang = (tags.get("language") or tags.get("title") or "").lower().strip()
        if first_sub_index is None:
            first_sub_index = s.get("index")
        if any(tag in lang for tag in PERSIAN_SUB_TAGS):
            target_index = s.get("index")
            break

    if target_index is None:
        target_index = first_sub_index
    if target_index is None:
        return None

    out_srt = os.path.join(OUTPUT_FOLDER, f"extracted_sub_{int(time.time())}.srt")
    proc2 = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-map",
        f"0:{target_index}",
        "-c:s",
        "srt",
        out_srt,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc2.communicate()

    if os.path.exists(out_srt) and os.path.getsize(out_srt) > 0:
        return out_srt
    return None


async def compress_video(
    input_path: str, target_size_bytes: int, status_msg: Message
) -> Tuple[Optional[str], str]:
    target_mb = target_size_bytes / 1024 / 1024
    output_path = os.path.join(
        OUTPUT_FOLDER, f"compressed_{int(target_mb)}mb_{int(time.time())}.mp4"
    )
    passlog = os.path.join(OUTPUT_FOLDER, f"passlog_{int(time.time())}")

    await safe_edit(status_msg, "🔍 Analyzing video...")

    try:
        duration, width, height = await get_video_info(input_path)
        if not duration or duration <= 0:
            return None, "Could not read video duration."

        audio_bitrate_bps = 64_000 if target_size_bytes <= 20 * 1024 * 1024 else 128_000
        total_bitrate_bps = int((target_size_bytes * 8) / duration * 0.95)
        video_bitrate_bps = max(total_bitrate_bps - audio_bitrate_bps, 10_000)
        audio_bitrate_k = audio_bitrate_bps // 1000

        # FIX: scale + format=yuv420p + noautorotate
        # - format=yuv420p: مطمئن میشه pixel format با libx264 سازگاره
        # - noautorotate: جلوگیری از تداخل rotation metadata با scale filter
        SCALE_VF = "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p"
        COMMON_INPUT = ["-noautorotate", "-i", input_path]

        await safe_edit(
            status_msg,
            f"⚙️ Compressing to ≈ {human_readable_size(target_size_bytes)}\n"
            f"📊 Duration: {int(duration)}s  |  Video: {video_bitrate_bps // 1000}kbps\n"
            f"🔄 Pass 1/2...",
        )

        pass1_args = [
            "ffmpeg",
            "-y",
            *COMMON_INPUT,
            "-vf",
            SCALE_VF,
            "-c:v",
            "libx264",
            "-b:v",
            str(video_bitrate_bps),
            "-pass",
            "1",
            "-passlogfile",
            passlog,
            "-an",
            "-f",
            "null",
            "/dev/null",
        ]
        rc, err = await _run_ffmpeg(pass1_args)

        if rc != 0:
            logger.warning(f"Two-pass pass1 failed → single-pass CRF. err: {err[:200]}")
            await safe_edit(status_msg, "⚙️ Single-pass encoding (CRF mode)...")
            sp_args = [
                "ffmpeg",
                "-y",
                *COMMON_INPUT,
                "-vf",
                SCALE_VF,
                "-c:v",
                "libx264",
                "-crf",
                "28",
                "-maxrate",
                str(video_bitrate_bps),
                "-bufsize",
                str(video_bitrate_bps * 2),
                "-preset",
                "fast",
                "-c:a",
                "aac",
                "-b:a",
                f"{audio_bitrate_k}k",
                "-movflags",
                "+faststart",
                output_path,
            ]
            rc2, err2 = await _run_ffmpeg(sp_args)
            if rc2 != 0:
                return None, f"FFmpeg error: {err2[-300:]}"
        else:
            await safe_edit(
                status_msg,
                f"⚙️ Compressing to ≈ {human_readable_size(target_size_bytes)}\n"
                f"📊 Duration: {int(duration)}s  |  Video: {video_bitrate_bps // 1000}kbps\n"
                f"🔄 Pass 2/2...",
            )
            pass2_args = [
                "ffmpeg",
                "-y",
                *COMMON_INPUT,
                "-vf",
                SCALE_VF,
                "-c:v",
                "libx264",
                "-b:v",
                str(video_bitrate_bps),
                "-pass",
                "2",
                "-passlogfile",
                passlog,
                "-preset",
                "fast",
                "-c:a",
                "aac",
                "-b:a",
                f"{audio_bitrate_k}k",
                "-movflags",
                "+faststart",
                output_path,
            ]
            rc, err = await _run_ffmpeg(pass2_args)
            if rc != 0:
                return None, f"FFmpeg pass2 error: {err[-300:]}"

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            return None, "Output file is empty or missing."

        return (
            output_path,
            f"✅ Compressed: {human_readable_size(os.path.getsize(output_path))}",
        )

    except FileNotFoundError:
        return None, "ffmpeg/ffprobe not found. Please install ffmpeg on the server."
    except Exception as e:
        logger.error(f"Compression error: {e}", exc_info=True)
        return None, f"Unexpected error: {str(e)[:150]}"
    finally:
        for ext in [".log", "-0.log", "-0.log.mbtree"]:
            try:
                pp = passlog + ext
                if os.path.exists(pp):
                    os.remove(pp)
            except Exception:
                pass


# ====================== SUBTITLE BURN-IN ======================
async def burn_subtitle(
    video_path: str,
    subtitle_path: str,
    status_msg,
) -> Tuple[Optional[str], str]:
    """
    زیرنویس رو روی ویدیو می‌سوزونه (hard subtitle).
    رنگ زرد با outline سیاه.
    """
    output_path = os.path.join(OUTPUT_FOLDER, f"subbed_{int(time.time())}.mp4")
    sub_ext = os.path.splitext(subtitle_path)[1].lower()

    # escape مسیر فایل برای ffmpeg filter — کاراکترهای خاص رو escape کن
    escaped_sub = subtitle_path.replace("\\", "/").replace(":", "\\:")

    if sub_ext in (".ass", ".ssa"):
        # فایل ASS استایل خودش رو داره، فقط override رنگ و outline میکنیم
        vf = f"ass={escaped_sub}"
    else:
        # SRT و بقیه — استایل دستی: زرد با outline سیاه
        vf = (
            f"subtitles={escaped_sub}:force_style='"
            "FontName=Arial,FontSize=22,PrimaryColour=&H00FFFF00,"
            "OutlineColour=&H00000000,Outline=2,Shadow=1,"
            "Bold=1,Alignment=2'"
        )

    await safe_edit(status_msg, "🔥 Burning subtitles into video...")

    args = [
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        output_path,
    ]

    rc, err = await _run_ffmpeg(args)
    if rc != 0:
        logger.error(f"[SUBTITLE] FFmpeg error: {err[-300:]}")
        return None, f"FFmpeg error: {err[-200:]}"

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        return None, "Output file is empty or missing."

    return output_path, "✅ Subtitles burned successfully"


# ====================== DIRPY FLOW ======================
processing_messages = set()


async def process_dirpy_request(event, url: str):
    msg_id = f"{event.chat_id}_{event.id}"
    if msg_id in processing_messages:
        return
    processing_messages.add(msg_id)
    logger.info(f"[DIRPY] START | chat={event.chat_id} | url={url[:120]}")
    status_msg = await event.reply("🔄 Starting extraction...", parse_mode="markdown")
    try:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        (
            found_urls,
            session_headers,
            intercept_err,
            video_title,
        ) = await extract_video_url_smart(url, status_msg)
        if not found_urls:
            logger.warning(
                f"[DIRPY] No URLs found | chat={event.chat_id} | err={intercept_err}"
            )
            await safe_edit(status_msg, f"❌ Could not capture video:\n{intercept_err}")
            return
        logger.info(f"[DIRPY] Found {len(found_urls)} URLs | chat={event.chat_id}")
        if len(found_urls) == 1:
            await do_download_and_send(
                event,
                status_msg,
                found_urls[0],
                url,
                extra_headers=session_headers,
                title=video_title,
            )
            return
        await safe_edit(
            status_msg, f"🔍 Found {len(found_urls)} links, checking sizes..."
        )
        sized_urls = []
        for u in found_urls:
            sz = await get_file_size(u)
            sized_urls.append((u, sz))
        pick_id = f"pick_{event.chat_id}_{int(time.time())}"
        video_cache[pick_id] = {
            "urls": sized_urls,
            "source_url": url,
            "chat_id": event.chat_id,
            "session_headers": session_headers,
            "title": video_title,
        }
        buttons = [
            [Button.inline(_url_label(u, sz, i), f"pickurl_{pick_id}_{i}")]
            for i, (u, sz) in enumerate(sized_urls)
        ]
        await safe_edit(status_msg, "📋 **Select video to download:**")
        await event.client.send_message(
            event.chat_id,
            f"🎬 Found **{len(sized_urls)}** video links.\nChoose one to download:",
            buttons=buttons,
            parse_mode="markdown",
        )
        await status_msg.delete()
    except Exception as e:
        logger.error(f"Dirpy process error: {e}", exc_info=True)
        err_str = str(e)
        if "connection closed" in err_str.lower() or "browser" in err_str.lower():
            await safe_edit(status_msg, "❌ Browser crashed. Please try again.")
        else:
            await safe_edit(status_msg, f"❌ Error: {err_str[:120]}")
    finally:
        processing_messages.discard(msg_id)


# ====================== CALLBACK HANDLERS ======================
async def compress_callback(event):
    video_id = event.data.decode().replace("compress_", "")
    if video_id not in video_cache:
        return await event.answer("Video not found or expired.", alert=True)
    await event.answer("Send desired size (e.g: 15mb or 800kb)", alert=False)
    # FIX: chat_id رو ذخیره میکنیم (نه sender_id) — در private chat یکیه ولی در گروه فرق دارن
    user_state[event.chat_id] = {
        "action": "wait_for_compression_size",
        "video_id": video_id,
    }


async def check_callback(event):
    video_id = event.data.decode().replace("check_", "")
    if video_id not in video_cache:
        return await event.answer("Video already deleted.", alert=True)
    data = video_cache[video_id]
    try:
        if os.path.exists(data["filepath"]):
            os.remove(data["filepath"])
        await event.answer("✅ Video deleted from server.", alert=False)
        await event.edit(buttons=None)
    except Exception:
        await event.answer("Error deleting file.", alert=True)
    video_cache.pop(video_id, None)


async def pickurl_callback(event):
    parts = event.data.decode().rsplit("_", 1)
    idx = int(parts[1])
    pick_id = parts[0].replace("pickurl_", "")
    if pick_id not in video_cache:
        return await event.answer(
            "Session expired. Please resend /dirpy command.", alert=True
        )
    data = video_cache[pick_id]
    if idx >= len(data["urls"]):
        return await event.answer("Invalid selection.", alert=True)
    chosen_url, _ = data["urls"][idx]
    source_url = data["source_url"]
    session_headers = data.get("session_headers", {})
    saved_title = data.get("title", "")
    await event.answer(f"Starting download #{idx + 1}...", alert=False)
    try:
        await event.delete()
    except Exception:
        pass
    status_msg = await event.client.send_message(
        event.chat_id, "📥 Starting download..."
    )
    del video_cache[pick_id]
    await do_download_and_send(
        event,
        status_msg,
        chosen_url,
        source_url,
        extra_headers=session_headers,
        title=saved_title,
    )


# ====================== ADMIN HANDLERS ======================
async def admin_input_handler(event):
    if event.sender_id != ADMIN_ID:
        return
    if event.sender_id not in admin_pending_add:
        return
    action = admin_pending_add.pop(event.sender_id)
    raw = event.raw_text.strip()

    if action == "sponsor_set_name":
        # مرحله ۱: ذخیره اسم و درخواست لینک
        pending_sponsor_name[event.sender_id] = raw
        admin_pending_add[event.sender_id] = "sponsor_set_link"
        await event.reply(
            f"✅ اسم `{raw}` ذخیره شد.\nحالا لینک دعوت یا آیدی کانال رو بفرست:\n"
            "مثلاً:\n"
            "`https://t.me/joinchat/xxx`\n"
            "یا `@channel_id`\n"
            "یا `-1001234567890`",
            parse_mode="markdown",
        )
        raise events.StopPropagation

    if action == "sponsor_set_link":
        # مرحله ۲: ذخیره لینک و ساخت اسپانسر
        name = pending_sponsor_name.pop(event.sender_id, "Unknown")
        txt = raw.strip()
        link = txt
        entry = {"name": name, "link": link, "chat_id": txt}
        if txt.startswith("https://t.me/+") or txt.startswith("https://t.me/joinchat/"):
            m = re.search(r"(?:joinchat/|\+)([a-zA-Z0-9_-]+)", txt)
            if m:
                entry["invite_hash"] = m.group(1)
                try:
                    from telethon.tl.functions.messages import CheckChatInviteRequest
                    invite = await event.client(CheckChatInviteRequest(entry["invite_hash"]))
                    if hasattr(invite, "chat") and invite.chat:
                        entry["resolved_id"] = invite.chat.id
                        # access_hash رو حتماً ذخیره کن (حتی ۰) چون بات میتونه با ۰ کار کنه
                        ah = invite.chat.access_hash if hasattr(invite.chat, "access_hash") else 0
                        entry["access_hash"] = ah or 0
                except Exception:
                    pass
        elif txt.startswith("-") and txt.lstrip("-").isdigit():
            entry["chat_id"] = int(txt)
        elif txt.isdigit():
            entry["chat_id"] = int(txt)
        sponsors.append(entry)
        asyncio.ensure_future(_save_sponsors())
        await event.reply(
            f"✅ **اسپانسر اضافه شد!**\n📢 `{name}` — `{txt}`",
            parse_mode="markdown",
        )
        raise events.StopPropagation

    if not raw.isdigit():
        await event.reply(
            "❌ Invalid ID! Please send a numeric ID only.", parse_mode="markdown"
        )
        raise events.StopPropagation
    uid = int(raw)
    if action == "add":
        if uid in AUTHORIZED_USERS:
            await event.reply(
                f"⚠️ User `{uid}` is already authorized.", parse_mode="markdown"
            )
        else:
            AUTHORIZED_USERS.add(uid)
            await event.reply(
                f"✅ User `{uid}` added!\nTotal: **{len(AUTHORIZED_USERS)}**",
                parse_mode="markdown",
            )
    elif action == "remove":
        if uid == ADMIN_ID:
            await event.reply("❌ You cannot remove yourself!", parse_mode="markdown")
        elif uid not in AUTHORIZED_USERS:
            await event.reply(f"⚠️ User `{uid}` not found.", parse_mode="markdown")
        else:
            AUTHORIZED_USERS.discard(uid)
            await event.reply(
                f"✅ User `{uid}` removed!\nTotal: **{len(AUTHORIZED_USERS)}**",
                parse_mode="markdown",
            )
    raise events.StopPropagation


# ====================== SIZE INPUT HANDLER ======================
async def size_input_handler(event):
    if event.sender_id not in AUTHORIZED_USERS:
        return
    # FIX: chat_id (نه sender_id) — اصلاح اصلی برای "Invalid size format" bug
    state = user_state.get(event.chat_id)
    if not state or state.get("action") != "wait_for_compression_size":
        return

    video_id = state["video_id"]
    if video_id not in video_cache:
        user_state.pop(event.chat_id, None)
        raise events.StopPropagation

    target_bytes = parse_size_input(event.raw_text)
    if not target_bytes:
        await event.reply(
            "❌ Invalid size format!\nExamples: `15mb`, `800kb`, `1.5gb`",
            parse_mode="markdown",
        )
        raise events.StopPropagation

    data = video_cache[video_id]
    if target_bytes >= data["original_size"]:
        await event.reply(
            "❌ Target size must be smaller than original size.", parse_mode="markdown"
        )
        raise events.StopPropagation

    # state رو قبل از شروع پاک کن — جلوگیری از double-trigger
    user_state.pop(event.chat_id, None)

    status_msg = await event.reply(
        f"⚙️ Starting compression → {human_readable_size(target_bytes)}..."
    )
    compressed_path, result = await compress_video(
        data["filepath"], target_bytes, status_msg
    )

    if compressed_path and os.path.exists(compressed_path):
        await safe_edit(status_msg, "📤 Uploading compressed video...")
        try:
            comp_size = os.path.getsize(compressed_path)
            gh_line = ""
            if GITHUB_ENABLED:
                await safe_edit(status_msg, "☁️ Uploading to GitHub...")
                gh_url = await maybe_upload_github(
                    event.client, event.chat_id, compressed_path, comp_size
                )
                if gh_url:
                    gh_line = f"\n☁️ [GitHub DL]({gh_url})"
            await send_file_with_progress(
                client=event.client,
                chat_id=event.chat_id,
                filepath=compressed_path,
                caption=(
                    f"✅ **Compressed Video**\n"
                    f"🎯 Requested: {human_readable_size(target_bytes)}\n"
                    f"📦 Final Size: {human_readable_size(comp_size)}"
                    f"{gh_line}"
                ),
                status_msg=status_msg,
            )
        except Exception as e:
            await safe_edit(status_msg, f"❌ Upload failed: {str(e)[:100]}")
        try:
            os.remove(compressed_path)
            os.remove(data["filepath"])
        except Exception:
            pass
    else:
        await safe_edit(status_msg, f"❌ Compression failed: {result}")

    video_cache.pop(video_id, None)
    raise events.StopPropagation


# ====================== PDF & HTML COMMANDS ======================


async def _fetch_hd_url(
    post_url: str, thumb_url: str, session: aiohttp.ClientSession
) -> str:
    """برای یه post URL لینک عکس اصلی رو میگیره (برای سایت‌هایی مثل rule34)."""
    try:
        async with session.get(post_url, timeout=ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return thumb_url
            html = await resp.text()
            # rule34: id="image" src="..."
            m = re.search(
                r"id=[\x22\x27]image[\x22\x27][^>]*src=[\x22\x27]([^\x22\x27]+)[\x22\x27]",
                html,
            )
            if not m:
                m = re.search(
                    r"src=[\x22\x27]([^\x22\x27]+)[\x22\x27][^>]*id=[\x22\x27]image[\x22\x27]",
                    html,
                )
            if m:
                src = m.group(1)
                if src.startswith("//"):
                    src = "https:" + src
                return src
    except Exception:
        pass
    return thumb_url


async def process_pdfimg_request(event, url: str):
    """عکس‌های صفحه رو دانلود، grid preview میسازه، دو دکمه Send All / Send All HD داره."""
    msg_id = f"{event.chat_id}_{event.id}"
    if msg_id in processing_messages:
        return
    processing_messages.add(msg_id)
    logger.info(f"[PDFIMG] START | chat={event.chat_id} | url={url[:120]}")
    status = await event.reply("🌐 Loading page...", parse_mode="markdown")
    tmp_dir = f"/app/output_files/pdfimg_{event.chat_id}_{event.id}"

    try:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        os.makedirs(tmp_dir, exist_ok=True)

        # ---- مرحله 1: استخراج URL عکس‌ها + لینک post اصلی با playwright ----
        img_data = []  # list of {"thumb": url, "post": url_or_None, "orig": url_or_None}

        JS_EXTRACT = """() => {
            const results = [];
            const seen = new Set();
            document.querySelectorAll('img').forEach(img => {
                const src = img.src || img.getAttribute('data-src') ||
                            img.getAttribute('data-original') ||
                            img.getAttribute('data-lazy') || '';
                if (!src || !src.startsWith('http') || seen.has(src)) return;
                seen.add(src);
                const a = img.closest('a');
                const postUrl = a ? a.href : null;
                const origSrc = img.getAttribute('data-original-url') ||
                                img.getAttribute('data-full') || null;
                results.push({thumb: src, post: postUrl, orig: origSrc});
            });
            return results;
        }"""

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=_browser_args())
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124"',
                    "Sec-Ch-Ua-Mobile": "?0",
                    "Sec-Ch-Ua-Platform": '"Windows"',
                },
                java_script_enabled=True,
                bypass_csp=True,
            )
            page = await context.new_page()
            page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))

            await safe_edit(status, "🌐 Opening page...")
            load_ok = False
            for wait_mode in ("domcontentloaded", "commit"):
                try:
                    await page.goto(url, wait_until=wait_mode, timeout=45000)
                    load_ok = True
                    break
                except Exception as _e:
                    logger.warning(f"[PDFIMG] goto({wait_mode}) failed: {_e}")

            if not load_ok:
                await browser.close()
                await safe_edit(
                    status, "❌ Could not load the page (timeout or blocked)."
                )
                return

            await page.wait_for_timeout(3000)

            # Cloudflare challenge detection
            for _cf_attempt in range(6):
                title = await page.title()
                if (
                    "just a moment" in title.lower()
                    or "checking your browser" in title.lower()
                    or "please wait" in title.lower()
                ):
                    await safe_edit(
                        status, f"⏳ Bypassing protection... ({_cf_attempt + 1}/6)"
                    )
                    await page.wait_for_timeout(5000)
                else:
                    break

            await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            await page.wait_for_timeout(1500)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1500)

            img_data = await page.evaluate(JS_EXTRACT)
            await browser.close()

        if not img_data:
            logger.warning(f"[PDFIMG] No images found on page | chat={event.chat_id}")
            await safe_edit(status, "No images found on this page.")
            return

        logger.info(
            f"[PDFIMG] Found {len(img_data)} images on page | chat={event.chat_id}"
        )
        await safe_edit(status, f"Found {len(img_data)} images. Downloading...")

        # ---- مرحله 2: دانلود thumbnail ها (JPG/PNG) + ذخیره GIF به همان فرمت ----
        import io as _io
        from PIL import Image as PILImage

        saved = []  # list of {"path": str, "is_gif": bool, "thumb_url": str, "post_url": str|None}
        dl_headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": url,
        }
        connector = aiohttp.TCPConnector(ssl=False, limit=8)
        async with aiohttp.ClientSession(
            connector=connector, headers=dl_headers, timeout=ClientTimeout(total=20)
        ) as http:
            for i, item in enumerate(img_data[:300]):
                thumb_url = item["thumb"]
                post_url = item.get("post")
                orig_url = item.get("orig")
                try:
                    async with http.get(thumb_url) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.read()
                        ct = resp.content_type or ""

                        is_gif = ct == "image/gif" or thumb_url.lower().endswith(".gif")

                        if is_gif:
                            # GIF رو همون‌طور ذخیره کن
                            gif_path = f"{tmp_dir}/img_{len(saved):04d}.gif"
                            async with aiofiles.open(gif_path, "wb") as f:
                                await f.write(data)
                            saved.append(
                                {
                                    "path": gif_path,
                                    "is_gif": True,
                                    "thumb_url": thumb_url,
                                    "post_url": post_url,
                                    "orig_url": orig_url,
                                }
                            )
                        else:
                            img = PILImage.open(_io.BytesIO(data)).convert("RGB")
                            if img.width < 80 or img.height < 80:
                                continue
                            img_path = f"{tmp_dir}/img_{len(saved):04d}.jpg"
                            img.save(img_path, "JPEG", quality=92)
                            img.close()
                            saved.append(
                                {
                                    "path": img_path,
                                    "is_gif": False,
                                    "thumb_url": thumb_url,
                                    "post_url": post_url,
                                    "orig_url": orig_url,
                                }
                            )

                        if len(saved) % 10 == 0:
                            await safe_edit(
                                status, f"Downloaded {len(saved)} images..."
                            )
                except Exception:
                    continue

        if not saved:
            await safe_edit(status, "Could not download any valid images.")
            return

        # ---- مرحله 3: ذخیره session و نمایش دکمه‌ها ----
        session_key = f"pdfimg_{event.chat_id}_{event.id}"
        pdfimg_sessions[session_key] = {
            "items": saved,
            "tmp_dir": tmp_dir,
            "chat_id": event.chat_id,
            "source_url": url,
        }

        n = len(saved)
        n_gif = sum(1 for s in saved if s["is_gif"])
        info = f"🖼 **{n} media ready**"
        if n_gif:
            info += f" ({n_gif} GIF)"
        info += "\nChoose how to send:"

        await status.delete()
        await event.client.send_message(
            event.chat_id,
            info,
            parse_mode="markdown",
            buttons=[
                [
                    Button.inline(f"📨 Send All ({n})", f"pdfimg_send|{session_key}"),
                    Button.inline(f"🔷 Send All HD ({n})", f"pdfimg_hd|{session_key}"),
                ],
                [Button.inline("🗑 Delete from server", f"pdfimg_del|{session_key}")],
            ],
        )

    except Exception as e:
        logger.error(f"pdfimg error: {e}", exc_info=True)
        err = str(e)
        if "connection closed" in err.lower() or "browser" in err.lower():
            await safe_edit(status, "❌ Browser crashed. Please try again.")
        else:
            await safe_edit(status, f"❌ Error: {err[:200]}")
    finally:
        processing_messages.discard(msg_id)


async def process_pdf_request(event, url: str):
    msg_id = f"{event.chat_id}_{event.id}"
    if msg_id in processing_messages:
        return
    processing_messages.add(msg_id)
    logger.info(f"[PDF] START | chat={event.chat_id} | url={url[:120]}")
    status = await event.reply("📄 Converting to PDF...", parse_mode="markdown")
    filepath = None
    try:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        filepath, error, size = await html_to_pdf(url, status)
        if error:
            await safe_edit(status, f"❌ {error}")
            return
        gh_line = ""
        if GITHUB_ENABLED:
            await safe_edit(status, "☁️ Uploading to GitHub...")
            gh_url = await maybe_upload_github(
                event.client, event.chat_id, filepath, size
            )
            if gh_url:
                gh_line = f"\n☁️ [GitHub DL]({gh_url})"
        await event.client.send_file(
            event.chat_id,
            filepath,
            caption=f"📑 PDF • {human_readable_size(size)}{gh_line}",
            force_document=True,
        )
        await status.delete()
    except Exception as e:
        await safe_edit(status, f"❌ Unexpected error: {str(e)[:120]}")
    finally:
        processing_messages.discard(msg_id)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def process_html_request(event, url: str):
    msg_id = f"{event.chat_id}_{event.id}"
    if msg_id in processing_messages:
        return
    processing_messages.add(msg_id)
    logger.info(f"[HTML] START | chat={event.chat_id} | url={url[:120]}")
    status = await event.reply("🌐 Capturing full webpage...", parse_mode="markdown")
    filepath = None
    try:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        filepath, error, size = await capture_mhtml(url, status)
        if error:
            await safe_edit(status, f"❌ {error}")
            return
        gh_line = ""
        if GITHUB_ENABLED:
            await safe_edit(status, "☁️ Uploading to GitHub...")
            gh_url = await maybe_upload_github(
                event.client, event.chat_id, filepath, size
            )
            if gh_url:
                gh_line = f"\n☁️ [GitHub DL]({gh_url})"
        await event.client.send_file(
            event.chat_id,
            filepath,
            caption=f"📦 Complete Webpage Snapshot (MHTML){gh_line}",
        )
        await status.delete()
    except Exception as e:
        await safe_edit(status, f"❌ Unexpected error: {str(e)[:120]}")
    finally:
        processing_messages.discard(msg_id)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


# ====================== TELEGRAM COMMANDS ======================
async def admin_cmd(event):
    logger.info(f"[CMD] /admin from user={event.sender_id}")
    if event.sender_id != ADMIN_ID:
        return await event.reply("⛔ Unauthorized")
    users_list = "\n".join([f"• `{uid}`" for uid in sorted(AUTHORIZED_USERS)])
    sponsor_count = len(sponsors)
    sponsor_lines = (
        "\n".join([f"• {s['name']}" for s in sponsors])
        if sponsors
        else "❌ No sponsors"
    )
    await event.reply(
        f"👑 **Admin Panel**\n\n**Authorized Users ({len(AUTHORIZED_USERS)}):**\n{users_list}\n\n"
        f"**Sponsors ({sponsor_count}):**\n{sponsor_lines}\n"
        f"Choose an action:",
        parse_mode="markdown",
        buttons=[
            [Button.inline("➕ Add User", "admin_add")],
            [Button.inline("➖ Remove User", "admin_remove")],
            [Button.inline("🔄 Refresh List", "admin_refresh")],
            [Button.inline("📢 Add Sponsor", "admin_sponsor_add")],
            [Button.inline("🚫 Remove Sponsor", "admin_sponsor_rmlist")],
        ],
    )


async def admin_add_callback(event):
    if event.sender_id != ADMIN_ID:
        return await event.answer("Unauthorized", alert=True)
    admin_pending_add[event.sender_id] = "add"
    await event.answer("", alert=False)
    await event.client.send_message(
        event.chat_id,
        "📩 Send me the **numeric user ID** to add:",
        parse_mode="markdown",
        buttons=[[Button.inline("❌ Cancel", "admin_cancel")]],
    )


async def admin_remove_callback(event):
    if event.sender_id != ADMIN_ID:
        return await event.answer("Unauthorized", alert=True)
    admin_pending_add[event.sender_id] = "remove"
    await event.answer("", alert=False)
    await event.client.send_message(
        event.chat_id,
        "📩 Send me the **numeric user ID** to remove:",
        parse_mode="markdown",
        buttons=[[Button.inline("❌ Cancel", "admin_cancel")]],
    )


async def admin_refresh_callback(event):
    if event.sender_id != ADMIN_ID:
        return await event.answer("Unauthorized", alert=True)
    users_list = "\n".join([f"• `{uid}`" for uid in sorted(AUTHORIZED_USERS)])
    sponsor_count = len(sponsors)
    sponsor_lines = (
        "\n".join([f"• {s['name']}" for s in sponsors])
        if sponsors
        else "❌ No sponsors"
    )
    await event.answer("✅ Refreshed", alert=False)
    try:
        await event.edit(
            f"👑 **Admin Panel**\n\n**Authorized Users ({len(AUTHORIZED_USERS)}):**\n{users_list}\n\n"
            f"**Sponsors ({sponsor_count}):**\n{sponsor_lines}\n"
            f"Choose an action:",
            parse_mode="markdown",
            buttons=[
                [Button.inline("➕ Add User", "admin_add")],
                [Button.inline("➖ Remove User", "admin_remove")],
                [Button.inline("🔄 Refresh List", "admin_refresh")],
                [Button.inline("📢 Add Sponsor", "admin_sponsor_add")],
                [Button.inline("🚫 Remove Sponsor", "admin_sponsor_rmlist")],
            ],
        )
    except Exception:
        pass


async def admin_cancel_callback(event):
    if event.sender_id != ADMIN_ID:
        return await event.answer("Unauthorized", alert=True)
    admin_pending_add.pop(event.sender_id, None)
    await event.answer("Cancelled", alert=False)
    try:
        await event.delete()
    except Exception:
        pass


# ====================== SPONSOR PERSIST (GitHub) ======================


async def _save_sponsors(client=None):
    global sponsors
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return
    text = json.dumps(sponsors, ensure_ascii=False)
    content_b64 = base64.b64encode(text.encode()).decode()
    try:
        async with aiohttp.ClientSession() as session:
            api_url = f"https://api.github.com/repos/{SPONSOR_REPO}/contents/{SPONSOR_FILE}"
            sha = None
            headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
            async with session.get(api_url, headers=headers) as check:
                if check.status == 200:
                    data = await check.json()
                    sha = data.get("sha")
            payload = {"message": "update sponsors", "content": content_b64, "branch": SPONSOR_BRANCH}
            if sha:
                payload["sha"] = sha
            async with session.put(api_url, json=payload, headers=headers) as resp:
                if resp.status not in (200, 201):
                    body = await resp.text()
                    logger.error(f"[SPONSOR] GitHub save failed: {resp.status} {body[:200]}")
    except Exception as e:
        logger.error(f"[SPONSOR] GitHub save error: {e}")


async def _load_sponsors(client=None):
    global sponsors
    sponsors = []
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        logger.info("[BOOT] No GITHUB_TOKEN, skipping sponsor load")
        return
    try:
        url = f"https://raw.githubusercontent.com/{SPONSOR_REPO}/{SPONSOR_BRANCH}/{SPONSOR_FILE}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    sponsors = json.loads(text)
                    logger.info(f"[BOOT] Loaded {len(sponsors)} sponsors from GitHub")
    except Exception as e:
        sponsors = []
        logger.error(f"[BOOT] GitHub sponsor load failed: {e}")


# ====================== SPONSOR HANDLERS ======================


async def admin_sponsor_add_callback(event):
    """مرحله ۱: گرفتن اسم کانال اسپانسر"""
    if event.sender_id != ADMIN_ID:
        return await event.answer("Unauthorized", alert=True)
    admin_pending_add[event.sender_id] = "sponsor_set_name"
    await event.answer("", alert=False)
    await event.client.send_message(
        event.chat_id,
        "📢 اسم کانال اسپانسر رو بفرست:\nمثلاً: `کانال اول`",
        parse_mode="markdown",
        buttons=[[Button.inline("❌ Cancel", "admin_cancel")]],
    )


async def admin_sponsor_rmlist_callback(event):
    """نمایش لیست اسپانسرها برای حذف"""
    if event.sender_id != ADMIN_ID:
        return await event.answer("Unauthorized", alert=True)
    if not sponsors:
        await event.answer("❌ هیچ اسپانسری تنظیم نشده.", alert=True)
        return
    buttons = []
    for i, s in enumerate(sponsors):
        buttons.append([Button.inline(f"❌ {s['name']}", f"admin_sponsor_rm_{i}")])
    buttons.append([Button.inline("🔙 برگشت", "admin_refresh")])
    await event.answer("", alert=False)
    try:
        await event.edit(
            "🚫 **روی اسپانسر مورد نظر کلیک کنید تا حذف شود:**",
            buttons=buttons,
        )
    except Exception:
        pass


async def admin_sponsor_rm_callback(event):
    """حذف یک اسپانسر خاص"""
    if event.sender_id != ADMIN_ID:
        return await event.answer("Unauthorized", alert=True)
    idx_str = event.data.decode().replace("admin_sponsor_rm_", "")
    try:
        idx = int(idx_str)
        removed = sponsors.pop(idx)
        asyncio.ensure_future(_save_sponsors())
        await event.answer(f"✅ {removed['name']} حذف شد!", alert=False)
    except (IndexError, ValueError):
        await event.answer("❌ خطا در حذف.", alert=True)
        return
    # برگشت به پنل
    await admin_refresh_callback(event)


async def _check_invite_membership(client, invite_hash: str, target_user_id: int):
    """بررسی عضویت کاربر در کانال خصوصی با invite hash.

    استراتژی:
      1. ImportChatInviteRequest میزنه — اگه بات قبلاً عضو بوده UserAlreadyParticipantError میگیره
      2. از CheckChatInviteRequest برای گرفتن chatId و access_hash استفاده میکنه
      3. GetParticipantRequest میزنه برای چک کردن کاربر هدف

      اگه بات دسترسی نداشته باشه (ChatInvite برگرده بدون chat)، استثنا میندازه
      تا کالر بتونه از ادامه صرفنظر کنه.
    """
    from telethon.tl.functions.messages import ImportChatInviteRequest, CheckChatInviteRequest
    from telethon.tl.functions.channels import GetParticipantRequest
    from telethon.tl.types import InputPeerChannel
    from telethon.errors import UserAlreadyParticipantError, UserNotParticipantError

    try:
        join_result = await client(ImportChatInviteRequest(invite_hash))
        # بات تازه عضو شد — entity from result.chats[0]
        if join_result and hasattr(join_result, "chats") and join_result.chats:
            chat = join_result.chats[0]
            cid = chat.id
            ah = getattr(chat, "access_hash", 0) or 0
            await client(GetParticipantRequest(InputPeerChannel(cid, ah), target_user_id))
            return
        raise ValueError("ImportChatInvite returned no chats")
    except UserAlreadyParticipantError:
        # بات قبلاً عضو بوده
        invite = await client(CheckChatInviteRequest(invite_hash))
        if hasattr(invite, "chat") and invite.chat:
            cid = invite.chat.id
            ah = getattr(invite.chat, "access_hash", 0) or 0
            await client(GetParticipantRequest(InputPeerChannel(cid, ah), target_user_id))
        else:
            raise ValueError("ChatInvite without chat field - bot cannot access channel")


async def sponsor_join_check_callback(event):
    """کاربر روی ✅ عضو شدم زده — چک میکنه عضو همه اسپانسرها هست یا نه."""
    data = event.data.decode()
    parts = data.split("_", 3)
    if len(parts) < 4:
        return await event.answer("❌ خطا", alert=True)
    try:
        manifest_msg_id = int(parts[2])
    except ValueError:
        return await event.answer("❌ خطا", alert=True)
    original_user_id = int(parts[3])
    if event.sender_id != original_user_id:
        return await event.answer("⛔ این دکمه مال شما نیست.", alert=True)

    if not ARCHIVE_CHANNEL_ID:
        return await event.answer("❌ آرکایو کانال تنظیم نشده.", alert=True)

    if not sponsors:
        await event.answer("", alert=False)
        await _forward_share_videos(event, manifest_msg_id)
        return

    user_id = event.sender_id
    not_joined = []
    for s in sponsors:
        try:
            from telethon.tl.functions.channels import GetParticipantRequest
            from telethon.tl.types import InputPeerChannel
            from telethon.errors import UserNotParticipantError

            invite_hash = s.get("invite_hash")
            resolved_id = s.get("resolved_id")
            access_hash = s.get("access_hash", 0) or 0

            if resolved_id:
                await event.client(GetParticipantRequest(
                    InputPeerChannel(resolved_id, access_hash), user_id
                ))
            elif invite_hash:
                await _check_invite_membership(event.client, invite_hash, user_id)
            else:
                # @username یا عدد
                resolver = await event.client.get_entity(s["chat_id"])
                await event.client.get_permissions(resolver, user_id)
        except UserNotParticipantError:
            not_joined.append(s)
        except Exception as e:
            logger.warning(f"[SPONSOR] Can't verify {s.get('name')}: {type(e).__name__}: {e}")
            # اگه بات دسترسی نداره، بذار کاربر رد بشه به جای بلاک کاذب
            continue

    if not_joined:
        await event.answer("❌ هنوز عضو همه کانال‌ها نشدید!", alert=True)
        return

    await event.answer("✅ عضویت تأیید شد!", alert=False)
    try:
        await event.delete()
    except Exception:
        pass
    await _forward_share_videos(event, manifest_msg_id)


async def _forward_share_videos(event, manifest_msg_id: int):
    """فوروارد ویدیوها از آرکایو کانال به کاربر و حذف بعد ۲۰ ثانیه."""
    msg_ids = await _get_manifest_msg_ids(event.client, manifest_msg_id)
    if not msg_ids:
        await event.reply("❌ ویدیویی وجود نداره.")
        return

    forwarded_msgs = []
    for msg_id in msg_ids:
        try:
            msg = await event.client.get_messages(ARCHIVE_CHANNEL_ID, ids=msg_id)
            if msg:
                fwd = await event.client.send_file(
                    event.sender_id,
                    file=msg.media,
                )
                forwarded_msgs.append(fwd)
        except Exception as e:
            logger.error(f"[SHARE] Forward error: {e}")

    if not forwarded_msgs:
        return

    await asyncio.sleep(20)

    for fwd in forwarded_msgs:
        try:
            await fwd.delete()
        except Exception:
            pass

    try:
        refresh_link = f"https://t.me/{BOT_USERNAME}?start=share_{manifest_msg_id}"
        await event.client.send_message(
            event.sender_id,
            "⏰ ویدیو بعد از ۲۰ ثانیه حذف شد.\n"
            "💾 برای ذخیره، پیام رو به **Saved Messages** فوروارد کنید.",
            parse_mode="markdown",
            buttons=[[Button.url("📥 دریافت مجدد فیلم‌ها", refresh_link)]],
        )
    except Exception:
        pass


async def sub_cmd(event):
    global SUB_BURN_ENABLED
    if event.sender_id not in AUTHORIZED_USERS:
        return await event.reply("⛔ Unauthorized")
    SUB_BURN_ENABLED = not SUB_BURN_ENABLED
    status = "✅ ON" if SUB_BURN_ENABLED else "🔴 OFF"
    await event.reply(
        f"🔤 **Subtitle Burn Mode: {status}**\n\n"
        + (
            "From now on, when a video is downloaded:\n"
            "• If it has a Persian soft subtitle → burned automatically\n"
            "• If not → you'll be asked to send a subtitle file"
            if SUB_BURN_ENABLED
            else "Videos will be uploaded directly without subtitle processing."
        ),
        parse_mode="markdown",
    )


async def suboff_cmd(event):
    global SUB_BURN_ENABLED
    if event.sender_id not in AUTHORIZED_USERS:
        return await event.reply("⛔ Unauthorized")
    SUB_BURN_ENABLED = False
    await event.reply(
        "🔴 **Subtitle Burn Mode: OFF**\nVideos will be uploaded directly without subtitle processing.",
        parse_mode="markdown",
    )


async def startgithub_cmd(event):
    global GITHUB_ENABLED
    logger.info(f"[CMD] /startgithub from user={event.sender_id}")
    if event.sender_id != ADMIN_ID:
        return await event.reply("⛔ Unauthorized")
    if not github_configured():
        return await event.reply("❌ GitHub not configured (token or repo missing)")
    GITHUB_ENABLED = True
    await event.reply(
        "✅ **GitHub upload ENABLED**\n\n"
        f"📁 Repo: `{GITHUB_REPO}`\n"
        f"🌿 Branch: `{GITHUB_BRANCH}`\n"
        f"📦 Max size: `{GITHUB_MAX_MB}MB`\n\n"
        "From now on, all files sent by the bot will also be uploaded to GitHub with a direct download link.",
        parse_mode="markdown",
    )


async def stopgithub_cmd(event):
    global GITHUB_ENABLED
    logger.info(f"[CMD] /stopgithub from user={event.sender_id}")
    if event.sender_id != ADMIN_ID:
        return await event.reply("⛔ Unauthorized")
    GITHUB_ENABLED = False
    await event.reply(
        "🔴 **GitHub upload DISABLED**\nFiles will no longer be uploaded to GitHub.",
        parse_mode="markdown",
    )


async def github_cmd(event):
    logger.info(f"[CMD] /github from user={event.sender_id}")
    if event.sender_id != ADMIN_ID:
        return await event.reply("⛔ Unauthorized")
    status_icon = "✅ Active" if GITHUB_ENABLED else "⏸ Paused"
    if github_configured():
        await event.reply(
            f"☁️ **GitHub Status: {status_icon}**\n\n"
            f"📁 Repo: `{GITHUB_REPO}`\n"
            f"🌿 Branch: `{GITHUB_BRANCH}`\n"
            f"📂 Base dir: `{GITHUB_BASE_DIR}`\n"
            f"📦 Max file size: `{GITHUB_MAX_MB}MB`\n\n"
            f"• `/startgithub` — enable auto-upload\n"
            f"• `/stopgithub` — disable auto-upload",
            parse_mode="markdown",
        )
    else:
        await event.reply(
            "☁️ **GitHub Status: ❌ Not configured**\n\n"
            "Set these environment variables:\n"
            "`GITHUB_TOKEN` — Personal Access Token\n"
            "`GITHUB_REPO` — e.g. `username/myrepo`\n"
            "`GITHUB_BRANCH` — default: `main`\n"
            "`GITHUB_BASE_DIR` — default: `files`",
            parse_mode="markdown",
        )


async def debug_hentaihaven(event):
    """مرحله 13: زنجیره کامل با data تازه از watch page"""
    import re
    import json
    import base64
    import codecs
    from curl_cffi.requests import AsyncSession

    page_url = "https://hentaihaven.xxx/watch/oyasumi-sex/episode-1/"

    def rot13(s):
        return codecs.encode(s, "rot_13")

    def safe_b64(s):
        s = s.strip()
        m = len(s) % 4
        if m:
            s += "=" * (4 - m)
        return base64.b64decode(s).decode("utf-8")

    lines = []
    async with AsyncSession() as session:
        await session.get("https://hentaihaven.xxx/", impersonate="chrome", timeout=15)

        # 1. data تازه از watch page
        pr = await session.get(
            page_url,
            impersonate="chrome",
            headers={"Referer": "https://hentaihaven.xxx/"},
            timeout=15,
        )
        m = re.search(r"player\.php\?data=([A-Za-z0-9+/=_-]+)", pr.text)
        if not m:
            await event.reply("❌ data not found in watch page")
            return
        data_param = m.group(1)
        player_url = f"https://hentaihaven.xxx/wp-content/plugins/player-logic/player.php?data={data_param}"
        lines.append(f"🎬 fresh data len={len(data_param)}")

        # 2. player.php → token
        pl = await session.get(
            player_url,
            impersonate="chrome",
            headers={"Referer": page_url},
            timeout=15,
        )
        tm = re.search(
            r'x-secure-token["\']?\s+content=["\']([^"\']+)["\']',
            pl.text,
            re.IGNORECASE,
        )
        if not tm:
            await event.reply("❌ token not found")
            return

        # 3. decode token
        val = tm.group(1).replace("sha512-", "")
        for _ in range(3):
            val = safe_b64(rot13(val))
        config = json.loads(val)
        en, iv, uri = config.get("en", ""), config.get("iv", ""), config.get("uri", "")
        if uri.startswith("//"):
            uri = "https:" + uri
        lines.append(f"🔐 en len={len(en)}, iv='{iv}'")

        # 4. api.php با data تازه
        api_url = f"{uri}api.php"
        ar = await session.post(
            api_url,
            data={"action": "zarat_get_data_player_ajax", "a": en, "b": iv},
            impersonate="chrome",
            headers={
                "Referer": player_url,
                "Origin": "https://hentaihaven.xxx",
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=15,
        )
        lines.append(f"\n🎯 api.php status={ar.status_code} len={len(ar.text)}")
        lines.append(f"📦 Body:\n{ar.text[:2500]}")

    result = "\n".join(lines)
    for i in range(0, len(result), 4000):
        await event.reply(result[i : i + 4000])


async def start_cmd(event):
    logger.info(
        f"[CMD] /start from user={event.sender_id} | text={event.raw_text[:100]}"
    )

    # ===== Share link handling (برای کاربرای غیرمجاز) =====
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) > 1:
        param = parts[1].strip()
        if param.startswith("share_"):
            manifest_str = param[6:].strip()
            try:
                manifest_msg_id = int(manifest_str)
            except ValueError:
                return await event.reply("❌ لینک نامعتبر.")

            if not ARCHIVE_CHANNEL_ID:
                return await event.reply("❌ آرکایو کانال تنظیم نشده.")

            # بررسی اسپانسرها
            if sponsors:
                user_id = event.sender_id
                not_joined = []
                for s in sponsors:
                    try:
                        resolver = await event.client.get_entity(s["chat_id"])
                        await event.client.get_permissions(resolver, user_id)
                    except Exception:
                        not_joined.append(s)

                if not_joined:
                    buttons = []
                    row = []
                    for s in not_joined:
                        link = s.get("link") or s.get("chat_id", "")
                        row.append(Button.url(s["name"], link))
                    buttons.append(row)
                    buttons.append(
                        [Button.inline("✅ عضو شدم", f"sponsor_ok_{manifest_msg_id}_{user_id}")]
                    )

                    await event.reply(
                        "🔒 **لطفاً قبل از مشاهده ویدیو، عضو کانال‌های اسپانسر شوید:**\n\n"
                        "روی هر کدام کلیک کنید و بعد دکمه ✅ رو بزنید.",
                        parse_mode="markdown",
                        buttons=buttons,
                    )
                    return

            await _forward_share_videos(event, manifest_msg_id)
            return

    if event.sender_id not in AUTHORIZED_USERS:
        return await event.reply("⛔ Unauthorized")
    await event.reply(
        "🚀 **Ultimate Bot v5**\n\n"
        "• `/dirpy <url>` → Download video\n"
        "• `/snapwc <url>` → Download via SnapWC\n"
        "• `/savep <url>` → Download via SaveTheVideo\n"
        "• `/pdf <url>` → Webpage to PDF\n"
        "• `/html <url>` → Save as MHTML\n"
        "• `/pdfimg <url>` → Download all images\n"
        "• `/github` → GitHub upload status\n"
        "• `/startgithub` → Enable GitHub upload\n"
        "• `/stopgithub` → Disable GitHub upload\n"
        "• `/setsearch` → Change default inline search engine\n\n"
        "**Inline search:** `@telformatbot hardcore` — searches your default engine\n"
        "**Override:** `ph:xxx` `xv:xxx` `ep:xxx` `xn:xxx`\n\n"
        "**During download:** ⏸ Pause  •  ❌ Cancel\n"
        "**After download:** 🗜 Compress  •  ✅ Delete",
        parse_mode="markdown",
    )


async def dirpy_command(event):
    logger.info(
        f"[CMD] /dirpy from user={event.sender_id} | text={event.raw_text[:100]}"
    )
    if event.sender_id not in AUTHORIZED_USERS:
        return await event.reply("⛔ Unauthorized")
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await event.reply("❌ Usage: `/dirpy <url>`", parse_mode="markdown")
    await process_dirpy_request(event, parts[1].strip())


async def savep_command(event):
    logger.info(
        f"[CMD] /savep from user={event.sender_id} | text={event.raw_text[:100]}"
    )
    if event.sender_id not in AUTHORIZED_USERS:
        return await event.reply("⛔ Unauthorized")
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await event.reply("❌ Usage: `/savep <url>`", parse_mode="markdown")
    await process_savep_request(
        event=event,
        url=parts[1].strip(),
        safe_edit_fn=safe_edit,
        send_file_fn=send_file_with_progress,
        download_dir=OUTPUT_FOLDER,
    )


async def pdf_command(event):
    logger.info(f"[CMD] /pdf from user={event.sender_id} | text={event.raw_text[:100]}")
    if event.sender_id not in AUTHORIZED_USERS:
        return await event.reply("⛔ Unauthorized")
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await event.reply("❌ Usage: `/pdf <url>`", parse_mode="markdown")
    await process_pdf_request(event, parts[1].strip())


async def pdfimg_del_callback(event):
    if event.sender_id not in AUTHORIZED_USERS:
        return await event.answer("⛔ Unauthorized")
    session_key = event.data.decode().split("|", 1)[1]
    session = pdfimg_sessions.pop(session_key, None)
    if session:
        import shutil

        try:
            shutil.rmtree(session["tmp_dir"], ignore_errors=True)
        except Exception:
            pass
    await event.edit(buttons=None)
    await event.answer("🗑 Deleted from server.")


async def _do_send_pdfimg(event, session_key: str, hd: bool):
    """ارسال عکس‌ها — normal: thumbnail، HD: لینک اصلی از صفحه post"""
    session = pdfimg_sessions.get(session_key)
    if not session:
        return await event.answer("❌ Session expired. Run /pdfimg again.", alert=True)

    await event.answer("📨 Sending..." if not hd else "🔷 Fetching HD...", alert=False)
    items = [it for it in session["items"] if os.path.exists(it["path"])]
    chat_id = session["chat_id"]
    source_url = session.get("source_url", "")
    total = len(items)

    if total == 0:
        return await event.client.send_message(chat_id, "❌ No images found on server.")

    label = "HD" if hd else "normal"
    status = await event.client.send_message(
        chat_id, f"📨 Sending {total} files ({label})..."
    )
    sent = 0

    dl_headers = {"User-Agent": "Mozilla/5.0", "Referer": source_url}
    connector = aiohttp.TCPConnector(ssl=False, limit=4)
    import io as _io
    from PIL import Image as PILImage

    async with aiohttp.ClientSession(
        connector=connector, headers=dl_headers, timeout=ClientTimeout(total=30)
    ) as http:
        for item in items:
            try:
                send_path = item["path"]

                if hd:
                    # پیدا کردن لینک اصلی
                    hd_url = item.get("orig_url") or item["thumb_url"]

                    # اگه post_url داره، برو صفحه پست و عکس اصلی رو بگیر
                    post_url = item.get("post_url")
                    if post_url and post_url.startswith("http"):
                        fetched = await _fetch_hd_url(post_url, hd_url, http)
                        if fetched != hd_url:
                            hd_url = fetched

                    # دانلود HD
                    async with http.get(hd_url) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            ct = resp.content_type or ""
                            is_gif = ct == "image/gif" or hd_url.lower().endswith(
                                ".gif"
                            )
                            ext = ".gif" if is_gif else ".jpg"
                            hd_path = (
                                item["path"]
                                .replace(".jpg", "_hd" + ext)
                                .replace(".gif", "_hd" + ext)
                            )
                            if not is_gif:
                                # convert به JPEG
                                img = PILImage.open(_io.BytesIO(data)).convert("RGB")
                                img.save(hd_path, "JPEG", quality=97)
                            else:
                                async with aiofiles.open(hd_path, "wb") as f:
                                    await f.write(data)
                            send_path = hd_path

                await event.client.send_file(
                    chat_id,
                    send_path,
                    force_document=False,
                )
                sent += 1

                # آپلود به گیتهاب اگه فعاله
                if GITHUB_ENABLED:
                    try:
                        img_size = os.path.getsize(send_path)
                        gh_url = await maybe_upload_github(
                            event.client, chat_id, send_path, img_size
                        )
                        if gh_url:
                            await event.client.send_message(
                                chat_id,
                                f"☁️ [GitHub DL]({gh_url})",
                                parse_mode="markdown",
                            )
                    except Exception:
                        pass

                if sent % 5 == 0 or sent == total:
                    try:
                        await status.edit(f"📨 Sending... {sent}/{total}")
                    except Exception:
                        pass

                # پاک کردن HD temp
                if hd and send_path != item["path"] and os.path.exists(send_path):
                    try:
                        os.remove(send_path)
                    except Exception:
                        pass

            except Exception as e:
                logger.warning(f"pdfimg send error: {e}")
                try:
                    await status.edit(f"⚠️ Error on {sent + 1}: {str(e)[:60]}")
                except Exception:
                    pass

    # cleanup
    import shutil

    pdfimg_sessions.pop(session_key, None)
    try:
        shutil.rmtree(session["tmp_dir"], ignore_errors=True)
    except Exception:
        pass
    try:
        await event.edit(buttons=None)
    except Exception:
        pass
    try:
        await status.edit(f"✅ Sent {sent}/{total} files!")
    except Exception:
        pass


async def pdfimg_send_callback(event):
    if event.sender_id not in AUTHORIZED_USERS:
        return await event.answer("⛔ Unauthorized")
    session_key = event.data.decode().split("|", 1)[1]
    await _do_send_pdfimg(event, session_key, hd=False)


async def pdfimg_hd_callback(event):
    if event.sender_id not in AUTHORIZED_USERS:
        return await event.answer("⛔ Unauthorized")
    session_key = event.data.decode().split("|", 1)[1]
    await _do_send_pdfimg(event, session_key, hd=True)


async def pdfimg_command(event):
    logger.info(
        f"[CMD] /pdfimg from user={event.sender_id} | text={event.raw_text[:100]}"
    )
    if event.sender_id not in AUTHORIZED_USERS:
        return await event.reply("⛔ Unauthorized")
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await event.reply("❌ Usage: `/pdfimg <url>`", parse_mode="markdown")
    await process_pdfimg_request(event, parts[1].strip())


async def html_command(event):
    logger.info(
        f"[CMD] /html from user={event.sender_id} | text={event.raw_text[:100]}"
    )
    if event.sender_id not in AUTHORIZED_USERS:
        return await event.reply("⛔ Unauthorized")
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await event.reply("❌ Usage: `/html <url>`", parse_mode="markdown")
    await process_html_request(event, parts[1].strip())


_SUBDOMAIN_MAP = {
    "pornhub.com": ["de", "it", "fr", "es", "pt", "nl", "jp", "cn", "pl", "ru", "ar", "api"],
    "youporn.com": ["de", "it", "fr", "es", "pt", "nl", "jp", "pl", "ru", "ar", "br"],
    "redtube.com": ["de", "it", "fr", "es", "jp", "pl", "ru", "ar", "br"],
    "xvideos.com": ["de", "it", "fr", "es", "pt", "nl", "jp", "cn", "pl", "ru", "ar", "br"],
    "eporner.com": ["de", "it", "fr", "es", "pt", "nl", "jp", "cn", "pl", "ru", "ar", "br"],
    "xnxx.com": ["www", "nl", "sa", "sample", "events", "noc"],
    "xhamster.com": ["www", "de", "deu", "usernames", "event", "seo", "sc", "webdev"],
}

def _normalize_subdomain(url: str) -> str:
    for domain, subs in _SUBDOMAIN_MAP.items():
        for sub in subs:
            url = re.sub(rf"https?://{re.escape(sub)}\.{re.escape(domain)}", f"https://{domain}", url)
    return url

async def generic_url_handler(event):
    if getattr(event, "_inline_auto", False):
        pass
    elif event.sender_id not in AUTHORIZED_USERS or event.raw_text.startswith("/"):
        return

    # ─── Face Swap: بررسی آیا کاربر عکس face برای face swap فرستاده ───
    if event.chat_id in user_state and user_state[event.chat_id].get("action") == "wait_for_face_swap":
        handled = await faceswap_process_callback(event)
        if handled:
            return

    # ─── AI Image Generator: بررسی آیا کاربر prompt فرستاده ───
    if event.chat_id in user_state and user_state[event.chat_id].get("action") == "wait_for_ai_prompt":
        prompt_text = event.raw_text.strip()
        if not prompt_text or prompt_text.startswith("/"):
            return
        state_info = user_state.pop(event.chat_id)
        ai_session_id = state_info.get("ai_session_id")
        ai_state = ai_sessions.get(ai_session_id)
        if not ai_state:
            await event.reply("⏰ نشست منقضی شده. /ai رو دوباره بزن.")
            return

        count = ai_state.get("count", 1)
        style = ai_state.get("style", "none")
        shape = ai_state.get("shape", "square")
        quality = ai_state.get("quality", "hd")

        status_msg = await event.reply(f"🎨 در حال تولید {count} تصویر... این ممکنه چند ثانیه طول بکشه.")

        async def _ai_progress(text):
            try:
                await status_msg.edit(text, buttons=None)
            except Exception:
                pass

        try:
            success, error, image_paths = await generate_image(
                prompt=prompt_text,
                art_style=style,
                shape=shape,
                count=count,
                quality=quality,
                progress_cb=_ai_progress,
            )

            if success and image_paths:
                await status_msg.edit(f"✅ {len(image_paths)} تصویر تولید شد!\n📤 در حال ارسال...")
                # Send images as photos
                for i, img_path in enumerate(image_paths):
                    try:
                        caption = f"🎨 {prompt_text[:60]}" if i == 0 else ""
                        await event.client.send_file(
                            event.chat_id,
                            img_path,
                            caption=caption,
                            parse_mode="md",
                            force_document=False,
                            silent=True,
                        )
                        # Cleanup
                        try:
                            os.unlink(img_path)
                        except Exception:
                            pass
                    except Exception as e:
                        logger.error(f"[AI] Send error: {e}")
                        # Try as document
                        try:
                            await event.client.send_file(
                                event.chat_id, img_path,
                                force_document=True, silent=True,
                            )
                            os.unlink(img_path)
                        except Exception:
                            pass

                await status_msg.edit(f"✅ {len(image_paths)} تصویر ارسال شد!")
            else:
                await status_msg.edit(f"❌ {error}")
        except Exception as e:
            logger.error(f"[AI] Error: {e}", exc_info=True)
            await status_msg.edit(f"❌ خطا: {e}")

        ai_sessions.pop(ai_session_id, None)
        return

    # ─── Image OCR: وقتی کاربر عکس می‌فرسته ───
    if event.message and event.message.photo:
        # Save the photo and show inline button
        try:
            photo = event.message.photo
            # Save to temp file
            temp_path = os.path.join(OUTPUT_FOLDER, f"ocr_img_{event.chat_id}_{event.id}.jpg")
            await event.message.download_media(temp_path)
            session_id = f"ocr_{event.chat_id}_{event.id}_{int(time.time())}"
            ocr_sessions[session_id] = {
                "image_path": temp_path,
                "chat_id": event.chat_id,
            }
            await event.reply(
                "📷 عکس دریافت شد!",
                buttons=[
                    [Button.inline("📖 استخراج متن از عکس", f"ocrex_{session_id}")],
                    [Button.inline("🎭 Face Swap", f"fsinit_{session_id}")],
                    [Button.inline("🔞 Face Swap (+18)", f"fsnsfw_{session_id}")],
                ],
            )
        except Exception as e:
            logger.error(f"[OCR] Image receive error: {e}", exc_info=True)
        return

    # ─── Document OCR: وقتی کاربر فایل عکس به‌عنوان document می‌فرسته ───
    if event.message and event.message.document:
        doc = event.message.document
        # Check if it's an image document
        mime = getattr(doc, "mime_type", "") or ""
        if mime.startswith("image/"):
            try:
                temp_path = os.path.join(OUTPUT_FOLDER, f"ocr_doc_{event.chat_id}_{event.id}")
                await event.message.download_media(temp_path)
                session_id = f"ocr_{event.chat_id}_{event.id}_{int(time.time())}"
                ocr_sessions[session_id] = {
                    "image_path": temp_path,
                    "chat_id": event.chat_id,
                }
                await event.reply(
                    "📷 فایل تصویر دریافت شد!",
                    buttons=[
                        [Button.inline("📖 استخراج متن از عکس", f"ocrex_{session_id}")],
                        [Button.inline("🎭 Face Swap", f"fsinit_{session_id}")],
                        [Button.inline("🔞 Face Swap (+18)", f"fsnsfw_{session_id}")],
                    ],
                )
            except Exception as e:
                logger.error(f"[OCR] Document receive error: {e}", exc_info=True)
            return

    if (
        event.chat_id in user_state
        and user_state[event.chat_id].get("action") == "wait_for_compression_size"
    ):
        return
    msg_id = f"gen_{event.chat_id}_{event.id}"
    if msg_id in processing_messages:
        return
    processing_messages.add(msg_id)
    urls = re.findall(r'https?://[^\s<>"\']+', event.raw_text)
    if not urls:
        processing_messages.discard(msg_id)
        return
    target_url = urls[0]
    target_url = _normalize_subdomain(target_url)

    # Skip IMDb URLs — these come from inline search results and should not be downloaded
    if "imdb.com" in target_url or "imdbplay.tech" in target_url:
        processing_messages.discard(msg_id)
        return

    # diycraftsguide.com handler
    if is_diycraft_url(target_url):
        logger.info(f"[URL] diycraftsguide detected | url={target_url[:120]}")
        status_msg = await event.reply("🎬 در حال دریافت اطلاعات...")
        try:
            info = await extract_video_info(target_url)
            if not info:
                await status_msg.edit("❌ ویدیو پیدا نشد.")
                return
            title = info["title"]
            thumb = info.get("thumbnail", "")

            # Check if it's a series
            if info.get("is_series") and info.get("episodes"):
                # Series — show episode buttons
                episodes = info["episodes"]
                buttons = []
                row = []
                for ep in episodes:
                    ep_num = ep["episode"]
                    row.append(Button.inline(f"قسمت {ep_num}", f"dcep_{ep_num}"))
                    if len(row) == 3:
                        buttons.append(row)
                        row = []
                if row:
                    buttons.append(row)
                buttons.append([Button.inline("🚫 بستن", "dcclose")])

                # Save state
                dc_states[event.sender_id] = {
                    "title": title,
                    "episodes": {ep["episode"]: ep for ep in episodes},
                    "page_url": info.get("page_url", target_url),
                    "thumb": thumb,
                }
                await status_msg.edit(
                    f"📺 **{title}**\n\n📺 یکی از قسمت‌ها رو انتخاب کن:",
                    buttons=buttons,
                    parse_mode="md",
                )
                return

            # Movie — direct download
            video_url = info.get("video_url", "")
            if not video_url:
                await status_msg.edit("❌ لینک ویدیو پیدا نشد.")
                return

            await status_msg.edit(f"🎬 **{title}**\n\n⏳ در حال دانلود...")

            out_dir = os.path.join(OUTPUT_FOLDER, f"diycraft_{event.chat_id}_{int(time.time())}")
            os.makedirs(out_dir, exist_ok=True)

            dl_id = f"dc_{event.chat_id}_{event.id}_{int(time.time())}"
            active_downloads[dl_id] = {"paused": False, "cancelled": False}

            video_path = await diycraft_download(
                video_url, out_dir,
                referer=target_url,
            )

            if not video_path or not os.path.exists(video_path):
                await status_msg.edit("❌ دانلود ناموفق بود.")
                return

            size_mb = os.path.getsize(video_path) / 1024 / 1024
            await status_msg.edit(f"✅ دانلود شد ({size_mb:.1f} MB)\n📤 در حال آپلود...")

            thumb_path = None
            if thumb:
                try:
                    async with aiohttp.ClientSession(timeout=ClientTimeout(total=20)) as session:
                        async with session.get(thumb) as resp:
                            if resp.status == 200:
                                thumb_path = os.path.join(out_dir, "thumb.jpg")
                                with open(thumb_path, "wb") as f:
                                    f.write(await resp.read())
                except Exception:
                    pass

            caption = f"🎬 **{title}**\n💾 {size_mb:.1f} MB\n📀 diycraftsguide"

            await send_file_with_progress(
                client=event.client,
                chat_id=event.chat_id,
                filepath=video_path,
                caption=caption,
                status_msg=status_msg,
                buttons=None,
                supports_streaming=True,
                thumb_filepath=thumb_path,
                ul_id=f"dc_ul_{dl_id}",
            )
            active_downloads.pop(dl_id, None)

        except Exception as dc_err:
            logger.error(f"[URL] diycraft error: {dc_err}", exc_info=True)
            await status_msg.edit(f"❌ خطا: {dc_err}")
        finally:
            processing_messages.discard(msg_id)
        return

    # sarrast.com handler (Persian adult visual stories)
    if is_sarrast_url(target_url):
        logger.info(f"[URL] sarrast detected | url={target_url[:120]}")
        status_msg = await event.reply("📖 در حال دریافت اطلاعات فصل...")
        try:
            from otherwebsiteshandler.sarrast_handler import (
                extract_chapter_info, download_chapter_pdf, download_chapter_images,
            )
            info = await extract_chapter_info(target_url)
            if not info:
                await status_msg.edit("❌ فصل پیدا نشد.")
                processing_messages.discard(msg_id)
                return
            series_title = info.get("series_title", "")
            chapter_title = info.get("title", "")
            total_imgs = len(info.get("images", []))
            # بررسی آیا ترجمه فارسی موجود هست
            has_translation = bool(
                info.get("translate") and info.get("translate", {}).get("html")
            )
            lang = info.get("lang", "")
            translation_info = ""
            if has_translation:
                translation_info = f"\n🌐 ترجمه: {lang if lang else 'فارسی'} ✅"
            
            buttons = []
            # اگه ترجمه موجود بود، دکمه PDF با ترجمه رو اول بذار
            if has_translation:
                buttons.append([Button.inline("📄 PDF با ترجمه فارسی 🌐", f"sr_pdftr_{event.id}")])
                buttons.append([Button.inline("📄 PDF بدون ترجمه", f"sr_pdf_{event.id}")])
            else:
                buttons.append([Button.inline("📄 دریافت PDF (تمام تصاویر)", f"sr_pdf_{event.id}")])
            buttons.append([Button.inline("🖼 دریافت تک‌تک تصاویر (سریع)", f"sr_imgs_{event.id}")])
            buttons.append([Button.inline("📦 دریافت ZIP", f"sr_zip_{event.id}")])
            
            await status_msg.edit(
                f"📖 **{series_title}**\n"
                f"📺 **{chapter_title}**\n"
                f"🖼 تعداد تصاویر: {total_imgs}{translation_info}\n\n"
                f"یکی از گزینه‌ها رو انتخاب کن:",
                buttons=buttons,
                parse_mode="md",
            )
            # Save state for callback
            sr_states[f"{event.chat_id}_{event.id}"] = {
                "url": target_url,
                "info": info,
                "chat_id": event.chat_id,
            }
        except Exception as sr_err:
            logger.error(f"[URL] sarrast error: {sr_err}", exc_info=True)
            await status_msg.edit(f"❌ خطا: {sr_err}")
        finally:
            processing_messages.discard(msg_id)
        return

    # ─── Comic sites (17 sites) ─────────────────────────────────
    # تشخیص URL کمیک
    _comic_url_fns = [
        is_hdporncomics_url, is_sexkomix2_url, is_hentai_name_url,
        is_porncomics_cloud_url, is_novelcrow_url, is_3hentai_url,
        is_erofus_url, is_nhentai_url, is_ilikecomix_url,
        is_hentai18_url, is_sexcomix_me_url, is_xlecx_url,
        is_comics_moon_url, is_comicsporn_url, is_zzcartoon_url,
        is_comicsflix_url, is_eggporncomics_url,
    ]
    for _is_comic_fn in _comic_url_fns:
        if _is_comic_fn(target_url):
            site_key = _get_comic_site_key(target_url)
            if site_key:
                site_name = COMIC_SITES[site_key]["display_name"]
                logger.info(f"[URL] {site_name} comic detected | url={target_url[:120]}")
                status_msg = await event.reply("📚 در حال دریافت اطلاعات کمیک...")
                try:
                    await process_comic_request(event, target_url, status_msg, site_key)
                except Exception as _e:
                    logger.error(f"[URL] {site_name} comic error: {_e}", exc_info=True)
                    try:
                        await status_msg.edit(f"❌ خطا: {_e}")
                    except Exception:
                        pass
                finally:
                    processing_messages.discard(msg_id)
                return
            break

    # ─── New sites (27 sites) - generic dispatch loop ──────────
    for _is_url_fn, _process_fn, _log_name in NEW_SITE_HANDLERS:
        if _is_url_fn(target_url):
            logger.info(f"[URL] {_log_name} detected | url={target_url[:120]}")
            status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
            try:
                await _process_fn(event, target_url, status_msg)
            except Exception as _e:
                logger.error(f"[URL] {_log_name} error: {_e}", exc_info=True)
                try:
                    await status_msg.edit(f"❌ خطا: {_e}")
                except Exception:
                    pass
            finally:
                processing_messages.discard(msg_id)
            return

    if (
        YOUTUBE_RE.match(target_url)
        or "youtube.com" in target_url
        or "youtu.be" in target_url
    ):
        logger.info(f"[URL] YouTube detected | url={target_url[:120]}")
        status_msg = await event.reply("⏬ Processing...")
        try:
            await process_y2mate_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_reddit_url(target_url):
        logger.info(f"[URL] Reddit detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_reddit_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_xnxx_url(target_url):
        logger.info(f"[URL] XNXX detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_xnxx_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_pornhub_handler_url(target_url):
        logger.info(f"[URL] PornHub detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_pornhub_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_cartoonporn_url(target_url):
        logger.info(f"[URL] CartoonPorn detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_cartoonporn_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_cartoonporncom_url(target_url):
        logger.info(f"[URL] CartoonPorn.com detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_cartoonporncom_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_hihentaiporn_url(target_url):
        logger.info(f"[URL] HiHentaiPorn detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_hihentaiporn_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_fetishshrine_url(target_url):
        logger.info(f"[URL] FetishShrine detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_fetishshrine_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_bigfuck_url(target_url):
        logger.info(f"[URL] BigFuck detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_bigfuck_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_babestube_url(target_url):
        logger.info(f"[URL] BabesTube detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_babestube_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_pornwhite_url(target_url):
        logger.info(f"[URL] PornWhite detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_pornwhite_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_porndroids_url(target_url):
        logger.info(f"[URL] PornDroids detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_porndroids_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_hdtube_url(target_url):
        logger.info(f"[URL] HDTube detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_hdtube_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_sleazyneasy_url(target_url):
        logger.info(f"[URL] SleazyNeasy detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_sleazyneasy_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_shameless_url(target_url):
        logger.info(f"[URL] Shameless detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_shameless_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_hqporner_url(target_url):
        logger.info(f"[URL] HQPerner detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_hqporner_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_youjizz_url(target_url):
        logger.info(f"[URL] YouJizz detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_youjizz_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_severeporn_url(target_url):
        logger.info(f"[URL] SeverePorn detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_severeporn_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_mat6tube_url(target_url):
        logger.info(f"[URL] Mat6Tube detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_mat6tube_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_peekvids_url(target_url):
        logger.info(f"[URL] PeekVids detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_peekvids_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_paradisehill_url(target_url):
        logger.info(f"[URL] ParadiseHill detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_paradisehill_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_sxyprn_url(target_url):
        logger.info(f"[URL] SxyPrn detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_sxyprn_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_kick_url(target_url):
        logger.info(f"[URL] Kick detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال دریافت کیفیت‌ها...")
        try:
            await process_kick_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_luxuretv_url(target_url):
        logger.info(f"[URL] LuxureTV detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_luxuretv_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_rule34video_url(target_url):
        logger.info(f"[URL] Rule34Video detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_rule34video_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_xanimu_url(target_url):
        logger.info(f"[URL] XAnimu detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_xanimu_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_porntrex_url(target_url):
        logger.info(f"[URL] Porntrex detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_porntrex_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_heavyr_url(target_url):
        logger.info(f"[URL] HeavyR detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_heavyr_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_wonporn_url(target_url):
        logger.info(f"[URL] WonPorn detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_wonporn_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_leaksextape_url(target_url):
        logger.info(f"[URL] LeaksExtape detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_leaksextape_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_xxxpublicpornvideos_url(target_url):
        logger.info(f"[URL] XXXPublicPornVideos detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_xxxpublicpornvideos_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_xvideos_url(target_url):
        logger.info(f"[URL] XVideos detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_xvideos_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_xgroovy_url(target_url):
        logger.info(f"[URL] XGroovy detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_xgroovy_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_teensexvideos_url(target_url):
        logger.info(f"[URL] TeenSexVideos detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_teensexvideos_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_usersporn_url(target_url):
        logger.info(f"[URL] UsersPorn detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_usersporn_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_hentaihaven_url(target_url):
        logger.info(f"[URL] HentaiHaven detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_hentaihaven_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_rat_url(target_url):
        logger.info(f"[URL] Rat detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_rat_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_youporn_url(target_url):
        logger.info(f"[URL] YouPorn detected | url={target_url[:120]}")
        # نرمالایز سابدامین (fr., de., etc) به دامنه اصلی
        target_url = re.sub(r'^https?://[^/]*?\.youporn\.com', 'https://youporn.com', target_url)
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_youporn_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_sexvid_url(target_url):
        logger.info(f"[URL] Sexvid detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_sexvid_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_tube8_url(target_url):
        logger.info(f"[URL] Tube8 detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_tube8_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_redtube_url(target_url):
        logger.info(f"[URL] RedTube detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_redtube_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_hohoj_url(target_url):
        logger.info(f"[URL] Hohoj detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_hohoj_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_91porna_url(target_url):
        logger.info(f"[URL] 91Porna detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_91porna_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_playvids_url(target_url):
        logger.info(f"[URL] Playvids detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_playvids_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_porn300_url(target_url):
        logger.info(f"[URL] Porn300 detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_porn300_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_tnaflix_url(target_url):
        logger.info(f"[URL] Tnaflix detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_tnaflix_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_pornzog_url(target_url):
        logger.info(f"[URL] Pornzog detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_pornzog_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_xxxbp_url(target_url):
        logger.info(f"[URL] XXXBP detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_xxxbp_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_sexxxx_url(target_url):
        logger.info(f"[URL] SexXXX detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_sexxxx_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_elliniko_url(target_url):
        logger.info(f"[URL] Elliniko detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_elliniko_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_rapelust_url(target_url):
        logger.info(f"[URL] RapeLust detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_rapelust_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_rapeinass_url(target_url):
        logger.info(f"[URL] RapeInAss detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_rapeinass_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_forcedlove_url(target_url):
        logger.info(f"[URL] ForcedLove detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_forcedlove_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_rapedws_url(target_url):
        logger.info(f"[URL] RapedWS detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_rapedws_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_sextvx_url(target_url):
        logger.info(f"[URL] SEXTVX detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_sextvx_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_porndos_url(target_url):
        logger.info(f"[URL] PornDos detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_porndos_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_shahvani_url(target_url):
        logger.info(f"[URL] Shahvani detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_shahvani_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_deviants_url(target_url):
        logger.info(f"[URL] Deviants detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_deviants_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_xxxvids_url(target_url):
        logger.info(f"[URL] XXXVids detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_xxxvids_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_mutterfickt_url(target_url):
        logger.info(f"[URL] Mutterfickt detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_mutterfickt_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_rulexporn_url(target_url):
        logger.info(f"[URL] RulexPorn detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_rulexporn_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_robbyporn_url(target_url):
        logger.info(f"[URL] RobbyPorn detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_robbyporn_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_bgxmonster_url(target_url):
        logger.info(f"[URL] BGXMonster detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_bgxmonster_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_jebacina_url(target_url):
        logger.info(f"[URL] Jebacina detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیتها...")
        try:
            await process_jebacina_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_ersties_url(target_url):
        logger.info(f"[URL] Ersties detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیتها...")
        try:
            await process_ersties_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_whoreshub_url(target_url):
        logger.info(f"[URL] WhoresHub detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیتها...")
        try:
            await process_whoreshub_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_xfetish_url(target_url):
        logger.info(f"[URL] XFetish detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیتها...")
        try:
            await process_xfetish_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_erome_url(target_url):
        logger.info(f"[URL] Erome detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج محتوا...")
        try:
            await process_erome_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_beeg_url(target_url):
        logger.info(f"[URL] Beeg detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_beeg_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_spankbang_url(target_url):
        logger.info(f"[URL] SpankBang detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_spankbang_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_ixxx_url(target_url):
        logger.info(f"[URL] IXXX detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_ixxx_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return


    if is_eporner_url(target_url):
        logger.info(f"[URL] Eporner detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_eporner_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_rule34_url(target_url):
        logger.info(f"[URL] Rule34 detected | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج...")
        try:
            await process_rule34_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_pornhub_url(target_url):
        logger.info(
            f"[URL] PornHub detected, routing via SnapWC | url={target_url[:120]}"
        )
        try:
            await _run_snapwc_flow(event, target_url, None)
        finally:
            processing_messages.discard(msg_id)
        return

    if is_ytdlp_site_url(target_url):
        site_name = get_site_name(target_url)
        logger.info(f"[URL] {site_name} detected via yt-dlp | url={target_url[:120]}")
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")
        try:
            await process_ytdlp_request(event, target_url, status_msg)
        finally:
            processing_messages.discard(msg_id)
        return

    # Safety guard: skip URLs that should have been handled by dedicated handlers
    if (
        is_xnxx_url(target_url)
        or is_xvideos_url(target_url)
        or is_xgroovy_url(target_url)
        or is_teensexvideos_url(target_url)
        or is_usersporn_url(target_url)
        or is_hentaihaven_url(target_url)
        or is_rat_url(target_url)
        or is_youporn_url(target_url)
        or is_sexvid_url(target_url)
        or is_tube8_url(target_url)
        or is_redtube_url(target_url)
        or is_hohoj_url(target_url)
        or is_91porna_url(target_url)
        or is_playvids_url(target_url)
        or is_porn300_url(target_url)
        or is_tnaflix_url(target_url)
        or is_pornzog_url(target_url)
        or is_pornhub_url(target_url)
        or is_ytdlp_site_url(target_url)
    ):
        logger.warning(
            f"[URL] Dedicated-site URL fell through to direct download — skipping | url={target_url[:120]}"
        )
        processing_messages.discard(msg_id)
        return

    logger.info(
        f"[URL] Direct URL received | chat={event.chat_id} | url={target_url[:120]}"
    )
    dl_id = f"dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    status_msg = await event.reply("⏬ Downloading...")
    try:
        filepath, error, size = await download_with_controls(
            target_url, status_msg, dl_id, referer=None
        )

        if error == "HTTP_403":
            if is_direct_file_url(target_url):
                await safe_edit(status_msg, "🔄 403 — retrying via real browser...")
                dl_id2 = f"dl_{event.chat_id}_{event.id}_{int(time.time())}_pw"
                active_downloads[dl_id2] = {"paused": False, "cancelled": False}
                filepath, error, size = await download_with_playwright(
                    target_url, status_msg, dl_id2
                )
                if error or not filepath:
                    await safe_edit(
                        status_msg,
                        "❌ 403 Forbidden — سرور دانلود توسط ربات را مسدود کرده است.\n"
                        "لینک در مرورگر کار می‌کند اما CDN درخواست‌های خودکار را رد می‌کند.",
                    )
                    return
            else:
                await safe_edit(status_msg, "🔄 403 — trying via Dirpy...")
                await process_dirpy_request(event, target_url)
                try:
                    await status_msg.delete()
                except Exception:
                    pass
                return

        if error or not filepath:
            if error != "Cancelled by user":
                await safe_edit(status_msg, f"❌ {error or 'Failed'}")
            return

        # Check free space before proceeding
        fname = os.path.basename(filepath)
        free_space = get_free_space()
        file_size = os.path.getsize(filepath)

        if file_size > MAX_PART_SIZE:
            # Large file — split into parts and upload each separately
            need_space = file_size + 512 * 1024 * 1024  # extra 512MB margin
            if free_space < need_space:
                await safe_edit(
                    status_msg,
                    f"❌ Not enough disk space for split & upload.\n"
                    f"Need: {human_readable_size(need_space)}, Free: {human_readable_size(free_space)}",
                )
                try:
                    os.remove(filepath)
                except:
                    pass
                return

            await safe_edit(
                status_msg,
                f"✂️ Splitting large file ({human_readable_size(file_size)}) into parts...",
            )
            parts = await split_video_smart(filepath, status_msg=status_msg)
            if not parts:
                await safe_edit(status_msg, "❌ Failed to split file.")
                return

            try:
                os.remove(filepath)
            except:
                pass

            total_parts = len(parts)
            base_name = os.path.basename(filepath)
            ul_id_all = f"ul_{event.chat_id}_{event.id}_{int(time.time())}"
            active_uploads[ul_id_all] = {"paused": False, "cancelled": False}
            upload_failed = False
            for i, part_path in enumerate(parts):
                if active_uploads.get(ul_id_all, {}).get("cancelled"):
                    await safe_edit(status_msg, "🚫 Multi-part upload cancelled.")
                    for remaining in parts[i:]:
                        try:
                            os.remove(remaining)
                        except:
                            pass
                    upload_failed = True
                    break
                part_size = os.path.getsize(part_path)
                part_label = os.path.basename(part_path)
                pct_done = (i / total_parts) * 100
                await safe_edit(
                    status_msg,
                    f"📤 Uploading part {i + 1}/{total_parts} ({pct_done:.0f}% complete):\n{part_label}\n📏 {human_readable_size(part_size)}",
                )
                gh_line = ""
                if GITHUB_ENABLED:
                    gh_url = await maybe_upload_github(
                        event.client, event.chat_id, part_path, part_size
                    )
                    if gh_url:
                        gh_line = f"\n☁️ [GitHub DL]({gh_url})"
                try:
                    await send_file_with_progress(
                        client=event.client,
                        chat_id=event.chat_id,
                        filepath=part_path,
                        caption=(
                            f"📦 {base_name}\n"
                            f"🧩 Part {i + 1}/{total_parts}\n"
                            f"📏 {human_readable_size(part_size)}{gh_line}"
                        ),
                        status_msg=status_msg,
                        ul_id=ul_id_all,
                    )
                except asyncio.CancelledError:
                    upload_failed = True
                    for remaining in parts[i:]:
                        try:
                            os.remove(remaining)
                        except:
                            pass
                    break
                except Exception as e:
                    await safe_edit(
                        status_msg, f"❌ Part {i + 1} upload failed: {str(e)[:80]}"
                    )
                    upload_failed = True
                    for remaining in parts[i:]:
                        try:
                            os.remove(remaining)
                        except:
                            pass
                    break

                try:
                    os.remove(part_path)
                except:
                    pass

            active_uploads.pop(ul_id_all, None)

            if not upload_failed:
                orig_fname = os.path.basename(filepath)
                join_help = (
                    "📎 **Join parts into one file:**\n\n"
                    f"**Linux/Mac:**\n"
                    f'`cat "{orig_fname}.part*" > "{orig_fname}"`\n\n'
                    f"**Windows (CMD):**\n"
                    f'`copy /b "{orig_fname}.part*" "{orig_fname}"`'
                )
                await event.client.send_message(
                    event.chat_id,
                    f"✅ **All {total_parts} parts uploaded!**\n{join_help}",
                    parse_mode="markdown",
                )
        else:
            # Normal file — upload directly
            await safe_edit(status_msg, "📤 Uploading...")
            try:
                vid_duration, vw, vh = await get_video_info(filepath)
                is_video = (
                    vid_duration is not None and vid_duration > 0 and vw > 0 and vh > 0
                )
                if is_video:
                    mins, secs = divmod(int(vid_duration), 60)
                    hours, mins = divmod(mins, 60)
                    if hours > 0:
                        dur_str = f" | ⏱ {hours}:{mins:02d}:{secs:02d}"
                    else:
                        dur_str = f" | ⏱ {mins}:{secs:02d}"
                else:
                    dur_str = ""

                orig_name = os.path.basename(filepath)

                # ── Subtitle burn flow ──────────────────────────────────
                subtitle_name = ""
                if is_video and SUB_BURN_ENABLED:
                    await safe_edit(status_msg, "🔍 Checking for subtitles...")
                    sub_streams = get_subtitle_streams(filepath)

                    if sub_streams:
                        sub_list = "\n".join(
                            f"  {i+1}. {s['language']}" + (f" — {s['title']}" if s['title'] else "")
                            for i, s in enumerate(sub_streams)
                        )
                        prompt_msg = await event.client.send_message(
                            event.chat_id,
                            f"🎬 **{orig_name}**\n\n"
                            f"{len(sub_streams)} soft subtitle(s) found:\n{sub_list}\n\n"
                            "Send video as-is or burn a subtitle?",
                            parse_mode="markdown",
                            buttons=[
                                [Button.inline("📤 ارسال ویدیو", f"subsend_{event.chat_id}_{event.id}")],
                                [Button.inline("🔥 سوختن زیرنویس", f"subburn_list_{event.chat_id}_{event.id}")],
                                [Button.inline("❌ Cancel", f"subcancl_{event.chat_id}")],
                            ],
                        )
                        subtitle_sessions[event.chat_id] = {
                            "video_path": filepath,
                            "video_orig_name": orig_name,
                            "status_msg": status_msg,
                            "status_msg_id": prompt_msg.id,
                            "size": size,
                            "dur_str": dur_str,
                            "sub_streams": sub_streams,
                        }
                        return
                    else:
                        # هیچ زیرنویسی توی فایل نبود — از کاربر بخواه فایل بفرسته
                        prompt_msg = await event.client.send_message(
                            event.chat_id,
                            f"🔤 **Send subtitle file** for:\n`{orig_name}`\n\n"
                            "Formats: `.srt` `.ass` `.ssa` `.vtt`\n"
                            "Or skip to upload without subtitle.",
                            parse_mode="markdown",
                            buttons=[
                                [
                                    Button.inline(
                                        "⏭ Skip — upload as-is",
                                        f"subskip_{event.chat_id}_{event.id}",
                                    )
                                ],
                                [
                                    Button.inline(
                                        "❌ Cancel", f"subcancl_{event.chat_id}"
                                    )
                                ],
                            ],
                        )
                        subtitle_sessions[event.chat_id] = {
                            "video_path": filepath,
                            "video_orig_name": orig_name,
                            "status_msg": status_msg,
                            "status_msg_id": prompt_msg.id,
                            "size": size,
                            "dur_str": dur_str,
                        }
                        # اینجا return میکنیم — ادامه آپلود توی subtitle_receive_handler یا subskip_callback
                        return
                # ── پایان subtitle flow ─────────────────────────────────

                gh_line = ""
                if GITHUB_ENABLED:
                    await safe_edit(status_msg, "☁️ Uploading to GitHub...")
                    gh_url = await maybe_upload_github(
                        event.client, event.chat_id, filepath, size
                    )
                    if gh_url:
                        gh_line = f"\n☁️ [GitHub DL]({gh_url})"
                    await safe_edit(status_msg, "📤 Uploading...")
                _ul_id = f"ul_{event.chat_id}_{event.id}"
                cap = build_video_caption(orig_name, size, vid_duration, subtitle_name)
                if gh_line:
                    cap += gh_line
                await send_file_with_progress(
                    client=event.client,
                    chat_id=event.chat_id,
                    filepath=filepath,
                    caption=cap,
                    status_msg=status_msg,
                    ul_id=_ul_id,
                )
                active_uploads.pop(_ul_id, None)
                try:
                    await status_msg.delete()
                except Exception:
                    pass
            except Exception as e:
                active_uploads.pop(_ul_id, None)
                await safe_edit(status_msg, f"❌ Upload failed: {str(e)[:100]}")
                return
            try:
                os.remove(filepath)
            except Exception:
                pass
    finally:
        processing_messages.discard(msg_id)


# ====================== Y2MATE INTEGRATION ======================

YOUTUBE_RE = re.compile(r"(?:https?://)?(?:www\.|m\.)?(?:youtube\.com|youtu\.be)/")


async def process_y2mate_request(event, url: str, status_msg):
    logger.info(f"[Y2MATE] START | chat={event.chat_id} | url={url[:120]}")
    await safe_edit(status_msg, "🔄 Processing via Y2Mate...")
    session = Y2MateSession()
    try:
        result = await asyncio.wait_for(session.run_full_flow(url), timeout=120)
        if not result["success"]:
            await safe_edit(
                status_msg, f"❌ Y2Mate error: {result.get('error', 'Unknown')}"
            )
            ss = result.get("screenshot_b64", "")
            if ss:
                try:
                    await event.client.send_file(
                        event.chat_id, base64.b64decode(ss), caption="📸 Y2Mate error"
                    )
                except Exception:
                    pass
            await session.close_browser()
            return

        qualities = result.get("qualities", [])
        if not qualities:
            await safe_edit(status_msg, "❌ No quality options found.")
            await session.close_browser()
            return

        yt_title = session.title_text or ""
        pick_id = f"{event.chat_id}_{int(time.time())}"
        y2mate_sessions[pick_id] = {
            "session": session,
            "qualities": qualities,
            "source_url": url,
            "title": yt_title,
            "chat_id": event.chat_id,
        }

        buttons = []
        row = []
        for i, q in enumerate(qualities):
            label = f"{q['label']} ({q.get('size', '?')})"
            btn = Button.inline(label, f"y2mq_{pick_id}_{i}")
            row.append(btn)
            if len(row) >= 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([Button.inline("❌ Cancel", f"y2mc_{pick_id}")])

        title_line = f"\n🎬 **{yt_title}**" if yt_title else ""
        await safe_edit(
            status_msg,
            f"📋 **Choose quality:**{title_line}",
            buttons=buttons,
        )
    except asyncio.TimeoutError:
        await safe_edit(status_msg, "❌ Y2Mate timed out (120s).")
        await session.close_browser()
    except Exception as e:
        logger.error(f"[Y2MATE] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ Y2Mate error: {str(e)[:120]}")
        try:
            await session.close_browser()
        except Exception:
            pass
            await session.close_browser()
            return

        qualities = result.get("qualities", [])
        if not qualities:
            await safe_edit(status_msg, "❌ No quality options found.")
            await session.close_browser()
            return

        # Only keep video (mp4) qualities
        video_qs = [
            (i, q)
            for i, q in enumerate(qualities)
            if q.get("format", "mp4") == "mp4" and "p" in q.get("label", "").lower()
        ]
        audio_qs = [
            (i, q)
            for i, q in enumerate(qualities)
            if q.get("format") == "mp3" or "kbps" in q.get("label", "").lower()
        ]

        is_audio = False
        if video_qs:
            sel_idx, selected = video_qs[-1]
        elif audio_qs:
            sel_idx, selected = audio_qs[-1]
            is_audio = True
        else:
            sel_idx, selected = len(qualities) - 1, qualities[-1]

        await safe_edit(
            status_msg,
            f"📥 Downloading {selected['label']} ({selected.get('size', '?')})...",
        )
        dl_result = await session.select_quality(sel_idx)
        if not dl_result["success"]:
            await safe_edit(
                status_msg,
                f"❌ Y2Mate download failed: {dl_result.get('error', 'Unknown')}",
            )
            await session.close_browser()
            return

        dl_url = dl_result["download_url"]
        await session.close_browser()

        await safe_edit(status_msg, "📥 Downloading file...")
        yt_title = session.title_text or ""

        extra_ext = ".mp3" if is_audio else ".mp4"
        dl_id = f"dl_{event.chat_id}_{event.id}_{int(time.time())}"
        active_downloads[dl_id] = {"paused": False, "cancelled": False}
        filepath, dl_error, final_size = await download_with_controls(
            dl_url,
            status_msg,
            dl_id,
            referer="https://v21.www-y2mate.com/",
            extra_headers={"Referer": "https://v21.www-y2mate.com/"},
        )

        if dl_error or not filepath:
            await safe_edit(status_msg, f"❌ Download failed: {dl_error}")
            return

        await safe_edit(status_msg, "📤 Uploading...")
        try:
            # Ensure correct extension for audio
            if is_audio:
                base = os.path.splitext(filepath)[0]
                new_path = base + ".mp3"
                if filepath != new_path:
                    try:
                        os.rename(filepath, new_path)
                        filepath = new_path
                    except Exception:
                        pass

            fname = os.path.basename(filepath)
            yt_clean = yt_title
            caption_start = (
                f"🎬 {_escape_md(yt_clean)}"
                if yt_clean
                else ("🎵 Audio" if is_audio else f"📄 {_escape_md(fname)}")
            )
            gh_line = ""
            if GITHUB_ENABLED:
                gh_url = await maybe_upload_github(
                    event.client, event.chat_id, filepath, final_size
                )
                if gh_url:
                    gh_line = f"\n☁️ [GitHub DL]({gh_url})"

            # دانلود تامبنیل یوتیوب
            thumb_fp = None
            if not is_audio and "youtube" in url.lower():
                try:
                    import re as _re

                    ym = _re.search(
                        r"(?:v=|youtu\.be/|/v/|/embed/|/shorts/)([a-zA-Z0-9_-]{11})",
                        url,
                    )
                    if ym:
                        vid = ym.group(1)
                        turl = f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg"
                        async with aiohttp.ClientSession() as sess:
                            async with sess.get(
                                turl, timeout=aiohttp.ClientTimeout(total=10)
                            ) as resp:
                                if resp.status == 200:
                                    tfp = filepath + "_ytthumb.jpg"
                                    async with aiofiles.open(tfp, "wb") as f:
                                        async for chunk in resp.content.iter_chunked(
                                            65536
                                        ):
                                            await f.write(chunk)
                                    if os.path.getsize(tfp) > 0:
                                        thumb_fp = tfp
                except Exception:
                    pass

            ul_id = f"y2m_ul_{event.chat_id}_{event.id}_{int(time.time())}"
            await send_file_with_progress(
                client=event.client,
                chat_id=event.chat_id,
                filepath=filepath,
                caption=f"{caption_start}\n📦 {human_readable_size(final_size)}\n🔗 [Source]({url}){gh_line}",
                status_msg=status_msg,
                thumb_filepath=thumb_fp,
                ul_id=ul_id,
            )
            if thumb_fp and os.path.exists(thumb_fp):
                try:
                    os.remove(thumb_fp)
                except Exception:
                    pass
        except Exception as e:
            await safe_edit(status_msg, f"❌ Upload failed: {str(e)[:100]}")
            return
        try:
            os.remove(filepath)
        except Exception:
            pass
    except asyncio.TimeoutError:
        await safe_edit(status_msg, "❌ Y2Mate timed out (120s).")
        await session.close_browser()
    except Exception as e:
        logger.error(f"[Y2MATE] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ Y2Mate error: {str(e)[:120]}")
        try:
            await session.close_browser()
        except Exception:
            pass
    except asyncio.TimeoutError:
        await safe_edit(status_msg, "❌ Y2Mate timed out (120s).")
        await session.close_browser()
    except Exception as e:
        logger.error(f"[Y2MATE] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ Y2Mate error: {str(e)[:120]}")
        try:
            await session.close_browser()
        except Exception:
            pass


# ====================== VIDEO RECEIVE -> GITHUB OFFER ======================


async def _flush_video_send_batch(
    batch_key: str, client, chat_id: int, reply_to_id: int
):
    """بعد از ۳ ثانیه، پیام batch ویدیو رو ارسال میکنه."""
    await asyncio.sleep(3)
    video_send_timers.pop(batch_key, None)
    batch = video_send_pending.get(batch_key)
    if not batch or not batch.get("files"):
        return

    files = batch["files"]
    count = len(files)
    total_size = sum(f["file_size"] for f in files)
    size_str = human_readable_size(total_size)

    lines = [
        f"🎬 **{count} video file{'s' if count > 1 else ''} received** — {size_str}\n"
    ]
    for i, f in enumerate(files, 1):
        lines.append(
            f"  {i}. `{f['filename']}` ({human_readable_size(f['file_size'])})"
        )

    buttons = [
        [
            Button.inline(
                f"▶️ Send as Video ({count} file{'s' if count > 1 else ''})",
                f"vsend_{batch_key}",
            )
        ]
    ]

    # دکمه زیرنویس فقط برای یه فایل منطقی‌تره
    if count == 1:
        buttons.append([Button.inline("🔤 Burn Subtitle", f"subburn_{batch_key}")])

    # دکمه اشتراک‌گذاری با لینک
    buttons.append([Button.inline("🔗 Share Link", f"sharelink_{batch_key}")])

    # دکمه آپلود به uplod.ir
    buttons.append([Button.inline("📤 Upload to uplod.ir", f"uplod_{batch_key}")])

    if GITHUB_ENABLED:
        buttons.append([Button.inline("☁️ Upload to GitHub", f"vgh_batch_{batch_key}")])

    try:
        await client.send_message(
            chat_id,
            "\n".join(lines),
            parse_mode="markdown",
            buttons=buttons,
            reply_to=reply_to_id,
        )
    except Exception as e:
        logger.warning(f"[VBATCH] Failed to send batch message: {e}")


async def video_receive_handler(event):
    """وقتی کاربر ویدیو/document ویدیویی میفرسته:
    - ۳ ثانیه صبر میکنه تا فایل‌های بیشتری جمع بشه (batch)
    - یه دکمه 'ارسال به عنوان ویدیو' نشون میده
    - اگه GITHUB_ENABLED باشه، دکمه گیتهاب هم نشون میده
    """
    if event.sender_id not in AUTHORIZED_USERS:
        return

    # تشخیص ویدیو: video یا document با mime_type ویدیو
    media = event.video or event.document
    if not media:
        return
    mime = getattr(media, "mime_type", "") or ""
    is_video_mime = mime.startswith("video/")
    is_video_attr = bool(event.video)
    # بررسی پسوند فایل برای document هایی که mime ویدیو ندارن
    fname_attr = ""
    for attr in getattr(media, "attributes", []):
        fn = getattr(attr, "file_name", None)
        if fn:
            fname_attr = fn
            break
    video_exts = {
        ".mp4",
        ".mkv",
        ".avi",
        ".mov",
        ".wmv",
        ".flv",
        ".webm",
        ".m4v",
        ".mpg",
        ".mpeg",
        ".3gp",
        ".ts",
        ".mts",
        ".ogv",
        ".rmvb",
        ".f4v",
    }
    ext = os.path.splitext(fname_attr)[1].lower() if fname_attr else ""
    is_video_ext = ext in video_exts

    if not (is_video_mime or is_video_attr or is_video_ext):
        return

    file_size = getattr(media, "size", 0) or 0
    if file_size == 0:
        return

    filename = fname_attr or f"video_{event.id}{ext or '.mp4'}"

    # batch key برای همه ویدیوهای همزمان (تا ۳ ثانیه)
    batch_key = f"vbatch_{event.chat_id}"

    # اگه batch قبلی هنوز بازه (تایمر تموم نشده)، فایل جدید اضافه کن
    if batch_key in video_send_pending:
        video_send_pending[batch_key]["files"].append({
            "message_id": event.id,
            "file_size": file_size,
            "filename": filename,
        })
    else:
        video_send_pending[batch_key] = {
            "chat_id": event.chat_id,
            "reply_to_id": event.id,
            "files": [
                {
                    "message_id": event.id,
                    "file_size": file_size,
                    "filename": filename,
                }
            ],
        }

    # شروع (یا ریست) تایمر ۳ ثانیه‌ای
    if batch_key in video_send_timers:
        video_send_timers[batch_key].cancel()
    task = asyncio.get_event_loop().create_task(
        _flush_video_send_batch(batch_key, event.client, event.chat_id, event.id)
    )
    video_send_timers[batch_key] = task

    # اگه فقط github offer قبلی هم لازم بود (وقتی GITHUB_ENABLED بود):
    if GITHUB_ENABLED and file_size <= GITHUB_MAX_MB * 1024 * 1024:
        pending_id = f"vgh_{event.chat_id}_{event.id}_{int(time.time())}"
        video_github_pending[pending_id] = {
            "chat_id": event.chat_id,
            "message_id": event.id,
            "file_size": file_size,
        }


async def vgh_yes_callback(event):
    if event.sender_id not in AUTHORIZED_USERS:
        return await event.answer("⛔ Unauthorized", alert=True)
    pending_id = event.data.decode().replace("vgh_yes_", "")
    data = video_github_pending.pop(pending_id, None)
    if not data:
        return await event.answer("❌ Session expired.", alert=True)

    await event.answer("⏳ Downloading and uploading...", alert=False)
    try:
        await event.edit("⏳ Downloading video from Telegram...", buttons=None)
    except Exception:
        pass

    # دانلود ویدیو از تلگرام
    tmp_path = os.path.join(OUTPUT_FOLDER, f"vgh_{int(time.time())}.mp4")
    try:
        msg = await event.client.get_messages(data["chat_id"], ids=data["message_id"])
        await event.client.download_media(msg, file=tmp_path)
    except Exception as e:
        try:
            await event.edit(f"❌ Download failed: {str(e)[:100]}", buttons=None)
        except Exception:
            pass
        return

    if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
        try:
            await event.edit("❌ Failed to download video from Telegram.", buttons=None)
        except Exception:
            pass
        return

    actual_size = os.path.getsize(tmp_path) if os.path.exists(tmp_path) else 0
    size_mb = actual_size / (1024 * 1024)
    from github import CONTENT_API_MAX_MB as _CMAX

    if size_mb > _CMAX:
        upload_note = (
            f"📦 {size_mb:.1f} MB — using Releases API (may take a few minutes)..."
        )
    else:
        upload_note = f"📦 {size_mb:.1f} MB — uploading..."
    try:
        await event.edit(f"☁️ **Uploading to GitHub**\n{upload_note}", buttons=None)
    except Exception:
        pass

    gh_ok, gh_msg, gh_url = await upload_to_github(tmp_path)

    try:
        os.remove(tmp_path)
    except Exception:
        pass

    if gh_ok and gh_url:
        try:
            await event.edit(
                f"✅ **Uploaded to GitHub!**\n\n"
                f"🔗 [Direct Download Link]({gh_url})\n"
                f"`{gh_url}`",
                parse_mode="markdown",
                buttons=None,
            )
        except Exception:
            pass
    else:
        try:
            await event.edit(f"❌ GitHub upload failed:\n{gh_msg[:200]}", buttons=None)
        except Exception:
            pass


async def vgh_no_callback(event):
    if event.sender_id not in AUTHORIZED_USERS:
        return await event.answer("⛔ Unauthorized", alert=True)
    pending_id = event.data.decode().replace("vgh_no_", "")
    video_github_pending.pop(pending_id, None)
    await event.answer("OK", alert=False)
    try:
        await event.delete()
    except Exception:
        pass


async def vsend_callback(event):
    """دانلود فایل‌های ویدیویی از تلگرام و ارسال مجدد به عنوان video با تایتل فایل."""
    if event.sender_id not in AUTHORIZED_USERS:
        return await event.answer("⛔ Unauthorized", alert=True)

    batch_key = event.data.decode().replace("vsend_", "")
    batch = video_send_pending.pop(batch_key, None)
    if not batch:
        return await event.answer(
            "❌ Session expired or already processed.", alert=True
        )

    await event.answer("⏳ Sending as video...", alert=False)
    chat_id = batch["chat_id"]
    files = batch["files"]
    total = len(files)

    try:
        await event.edit(
            f"⏳ Downloading and sending {total} video{'s' if total > 1 else ''}...",
            buttons=None,
        )
    except Exception:
        pass

    sent = 0
    for i, file_info in enumerate(files):
        msg_id = file_info["message_id"]
        filename = file_info["filename"]
        title = os.path.splitext(filename)[0]

        tmp_path = os.path.join(
            OUTPUT_FOLDER, f"vsend_{int(time.time())}_{i}_{filename}"
        )
        try:
            msg = await event.client.get_messages(chat_id, ids=msg_id)
            if not msg:
                logger.warning(f"[VSEND] Message {msg_id} not found")
                continue

            try:
                await event.edit(
                    f"⬇️ Downloading {i + 1}/{total}: `{filename}`...",
                    parse_mode="markdown",
                    buttons=None,
                )
            except Exception:
                pass

            await event.client.download_media(msg, file=tmp_path)

            if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
                logger.warning(f"[VSEND] Download failed for {filename}")
                continue

            try:
                await event.edit(
                    f"📤 Uploading {i + 1}/{total}: `{filename}`...",
                    parse_mode="markdown",
                    buttons=None,
                )
            except Exception:
                pass

            ul_id = f"vsend_{chat_id}_{msg_id}"
            active_uploads[ul_id] = {"paused": False, "cancelled": False}
            try:
                await send_file_with_progress(
                    client=event.client,
                    chat_id=chat_id,
                    filepath=tmp_path,
                    caption=title,
                    status_msg=None,
                    ul_id=ul_id,
                )
                sent += 1
            finally:
                active_uploads.pop(ul_id, None)

        except Exception as e:
            logger.error(f"[VSEND] Error sending {filename}: {e}", exc_info=True)
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass

    try:
        result_text = (
            f"✅ Sent {sent}/{total} video{'s' if total > 1 else ''} successfully!"
        )
        await event.edit(result_text, buttons=None)
    except Exception:
        pass


# ====================== SHARE LINK ======================


async def _get_manifest_msg_ids(client, manifest_msg_id: int) -> list:
    """خوندن لیست message_idهای ویدیو از پیام manifes توی آرکایو."""
    try:
        msg = await client.get_messages(ARCHIVE_CHANNEL_ID, ids=manifest_msg_id)
        if not msg:
            return []
        text = msg.text or ""
        if not text.startswith("SHARE_MANIFEST:"):
            return []
        parts = text.split(":", 2)
        if len(parts) < 3:
            return []
        return [int(x) for x in parts[2].split(",") if x]
    except Exception:
        return []


async def sharelink_callback(event):
    if event.sender_id not in AUTHORIZED_USERS:
        return await event.answer("⛔ Unauthorized", alert=True)

    batch_key = event.data.decode().replace("sharelink_", "")
    batch = video_send_pending.get(batch_key)
    if not batch or not batch.get("files"):
        return await event.answer("❌ Session expired.", alert=True)

    files = batch["files"]
    chat_id = batch["chat_id"]

    if not ARCHIVE_CHANNEL_ID:
        return await event.answer("❌ آرکایو کانال تنظیم نشده.", alert=True)

    # فوروارد ویدیوها به آرکایو کانال
    manifest_ids = []
    for f in files:
        try:
            msg = await event.client.get_messages(chat_id, ids=f["message_id"])
            if msg:
                fwd = await event.client.send_file(
                    ARCHIVE_CHANNEL_ID,
                    file=msg.media,
                )
                manifest_ids.append(str(fwd.id))
        except Exception as e:
            logger.error(f"[SHARE] Forward to archive failed: {e}")

    if not manifest_ids:
        return await event.answer("❌ خطا در فوروارد به آرکایو.", alert=True)

    import uuid
    key = uuid.uuid4().hex[:12]
    manifest_text = f"SHARE_MANIFEST:{key}:{','.join(manifest_ids)}"
    try:
        manifest = await event.client.send_message(ARCHIVE_CHANNEL_ID, manifest_text)
        manifest_msg_id = manifest.id
    except Exception as e:
        logger.error(f"[SHARE] Failed to send manifest: {e}")
        return await event.answer("❌ خطا در ذخیره آرکایو.", alert=True)

    link = f"https://t.me/{BOT_USERNAME}?start=share_{manifest_msg_id}"
    await event.answer("✅ لینک ساخته شد!", alert=False)
    count = len(manifest_ids)
    try:
        await event.edit(
            f"🔗 **لینک اشتراک‌گذاری ({count} ویدیو):**\n`{link}`\n\n"
            f"هر کاربری با این لینک استارت بزنه، ویدیو براش **فوروارد** میشه و بعد ۲۰ ثانیه حذف میشه.",
            buttons=None,
            parse_mode="markdown",
        )
    except Exception:
        pass


# ====================== UPLOD HANDLER ======================

async def uplod_callback(event):
    """Upload video to uplod.ir and return download link."""
    if event.sender_id not in AUTHORIZED_USERS:
        return await event.answer("⛔ Unauthorized", alert=True)

    batch_key = event.data.decode().replace("uplod_", "")
    batch = video_send_pending.get(batch_key)
    if not batch or not batch.get("files"):
        return await event.answer("❌ Session expired.", alert=True)

    await event.answer("⏳ Working...", alert=False)

    log_lines = []
    def log_msg(msg):
        log_lines.append(msg)
        logger.info(f"[UPLOD] {msg}")

    async def update_status(text):
        try:
            await event.edit(text, buttons=None, parse_mode="md")
        except Exception:
            pass

    files = batch["files"]
    chat_id = batch["chat_id"]
    links = []

    log_msg(f"📦 Found {len(files)} file(s) in batch")
    await update_status(f"📦 Found {len(files)} file(s)\n⏳ Starting upload process...")

    for i, file_info in enumerate(files):
        msg_id = file_info["message_id"]
        filename = file_info["filename"]
        status = f"📄 **File {i + 1}/{len(files)}:** `{filename}`\n"

        tmp_path = os.path.join(
            OUTPUT_FOLDER, f"uplod_{int(time.time())}_{i}_{filename}"
        )
        file_size = 0
        try:
            # ── Step 1: Download from Telegram ──
            log_msg(f"⬇️ Downloading: {filename}")
            await update_status(status + "⬇️ Step 1/5: Downloading file from Telegram...")

            msg = await event.client.get_messages(chat_id, ids=msg_id)
            if not msg:
                log_msg(f"❌ Message {msg_id} not found")
                status += f"❌ Message not found (ID: {msg_id})\n"
                await update_status(status)
                continue

            await event.client.download_media(msg, file=tmp_path)

            if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
                log_msg(f"❌ Download failed — file empty or missing")
                status += "❌ Download failed — file empty or missing\n"
                await update_status(status)
                continue

            file_size = os.path.getsize(tmp_path)
            log_msg(f"✅ Downloaded: {filename} ({human_readable_size(file_size)})")
            status += f"✅ Downloaded ({human_readable_size(file_size)})\n"
            await update_status(status + "⬆️ Step 2/5: Starting uplod.ir uploader...")

            # ── Step 2: Import UplodHandler ──
            log_msg(f"📦 Importing UplodHandler...")
            try:
                from uplod_ir_handler import UplodHandler
            except ImportError as ie:
                log_msg(f"❌ Failed to import UplodHandler: {ie}")
                status += f"❌ ImportError: {ie}\n"
                status += "❗ playwright not installed? Try: `pip install playwright && playwright install chromium`\n"
                await update_status(status)
                continue
            log_msg(f"✅ UplodHandler imported")
            status += "✅ uplod.ir handler loaded\n"

            # ── Step 3: Launch browser & upload ──
            log_msg(f"🌐 Launching browser & uploading to uplod.ir...")
            await update_status(status + "🌐 Step 3/5: Launching browser & uploading to `https://uplod.ir/`...\n⏳ Transfer may take a while depending on file size & network.")

            # Show periodic "still alive" updates while upload runs in executor
            async def keep_alive(status_ref):
                dots = 0
                while True:
                    await asyncio.sleep(10)
                    dots = (dots + 1) % 4
                    bar = "⏳" + "." * dots
                    try:
                        await event.edit(
                            status_ref + f"\n{bar} Still uploading... (check console for progress)",
                            buttons=None, parse_mode="md",
                        )
                    except Exception:
                        pass

            loop = asyncio.get_event_loop()
            result = None
            ka_task = asyncio.create_task(keep_alive(status))
            try:
                result = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda p=tmp_path: UplodHandler(
                            timeout=120, verbose=True, log_file=None
                        ).upload(p),
                    ),
                    timeout=180,
                )
            except asyncio.TimeoutError:
                log_msg(f"❌ Upload timed out after 180 seconds")
                status += "❌ Upload timed out (180s)\n"
                status += "🌐 The site `uplod.ir` may be blocked or unreachable.\n"
                ka_task.cancel()
                await update_status(status)
                continue
            except Exception as upload_err:
                err_msg = f"{type(upload_err).__name__}: {upload_err}"
                log_msg(f"❌ Upload exception: {err_msg}")
                status += f"❌ Upload failed: `{err_msg}`\n"
                import traceback
                tb = traceback.format_exc()
                log_msg(f"Traceback:\n{tb}")
                if len(status + f"📋 Traceback:\n`{tb[:1500]}`\n") < 3800:
                    status += f"📋 Traceback:\n`{tb[:1500]}`\n"
                ka_task.cancel()
                await update_status(status)
                continue
            finally:
                ka_task.cancel()

            if not result:
                log_msg(f"❌ No result object returned from upload")
                status += "❌ Upload returned no result\n"
                await update_status(status)
                continue

            # ── Step 4: Parse result ──
            link = result.get("download_link", "")
            max_pct = result.get("max_percent", 0)
            duration = result.get("duration_sec", 0)
            avg_speed = result.get("average_speed_bps", 0)
            file_size_result = result.get("size_human", "")

            log_msg(f"📊 Upload result: {max_pct}% | {duration}s | {human_readable_size(avg_speed)}/s")
            log_msg(f"🔗 Link: {link if link else 'NO LINK'}")

            if link:
                links.append(link)
                status += f"✅ Upload complete!\n"
                status += f"📊 {max_pct}% | ⏱ {duration}s | 🚀 {human_readable_size(avg_speed)}/s\n"
                status += f"🔗 **Link:** {link}\n"
            else:
                status += f"❌ No download link returned\n"
                status += f"📊 max_percent={max_pct}%\n"
                if file_size_result:
                    status += f"📦 size={file_size_result}\n"
                log_msg(f"❌ Result dict keys: {list(result.keys())}")

            await update_status(status)

        except Exception as e:
            err_msg = f"{type(e).__name__}: {e}"
            log_msg(f"❌ Unhandled error: {err_msg}")
            import traceback
            tb = traceback.format_exc()
            log_msg(f"Traceback:\n{tb}")
            status += f"❌ Error: `{err_msg}`\n"
            if len(status + f"📋 Traceback:\n`{tb[:1500]}`\n") < 3800:
                status += f"📋 Traceback:\n`{tb[:1500]}`\n"
            await update_status(status)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                    log_msg(f"🧹 Cleaned up temp file: {tmp_path}")
                except Exception as e:
                    log_msg(f"⚠️ Failed to clean up: {e}")

    # ── Final result ──
    if not links:
        final = "❌ **Upload failed — no link received.**\n\n"
        final += "📋 **Full log:**\n" + "\n".join(log_lines[-20:])
        try:
            await event.edit(final, buttons=None, parse_mode="md")
        except Exception:
            pass
        return await event.answer("❌ Upload failed.", alert=True)

    final = f"✅ **Uploaded {len(links)} file(s) to uplod.ir:**\n\n"
    for link in links:
        final += f"🔗 {link}\n"
    final += "\n📋 **Log:**\n" + "\n".join(log_lines[-10:])

    try:
        await event.edit(final, buttons=None, parse_mode="md", link_preview=False)
    except Exception:
        pass
    await event.answer("✅ Upload complete!", alert=False)


# ====================== SUBTITLE HANDLER ======================


async def subburn_callback(event):
    """دکمه Burn Subtitle — ویدیو رو دانلود میکنه و منتظر فایل زیرنویس میمونه."""
    if event.sender_id not in AUTHORIZED_USERS:
        return await event.answer("⛔ Unauthorized", alert=True)

    batch_key = event.data.decode().replace("subburn_vbatch_", "")
    batch = video_send_pending.pop(batch_key, None)
    if not batch or not batch.get("files"):
        return await event.answer("❌ Session expired.", alert=True)

    await event.answer("⬇️ Downloading video...", alert=False)
    chat_id = batch["chat_id"]
    file_info = batch["files"][0]
    msg_id = file_info["message_id"]
    filename = file_info["filename"]

    try:
        await event.edit(
            f"⬇️ Downloading `{filename}`...", parse_mode="markdown", buttons=None
        )
    except Exception:
        pass

    tmp_path = os.path.join(OUTPUT_FOLDER, f"subvid_{int(time.time())}_{filename}")
    try:
        msg = await event.client.get_messages(chat_id, ids=msg_id)
        if not msg:
            await event.edit("❌ Could not find the video message.", buttons=None)
            return
        await event.client.download_media(msg, file=tmp_path)
    except Exception as e:
        try:
            await event.edit(f"❌ Download failed: {str(e)[:80]}", buttons=None)
        except Exception:
            pass
        return

    if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
        try:
            await event.edit("❌ Download failed.", buttons=None)
        except Exception:
            pass
        return

    # ذخیره session و منتظر زیرنویس
    prompt_msg = await event.client.send_message(
        chat_id,
        "🔤 **Send the subtitle file** (`.srt`, `.ass`, `.ssa`)\n\nVideo is ready and waiting.",
        parse_mode="markdown",
        buttons=[[Button.inline("❌ Cancel", f"subcancl_{chat_id}")]],
    )
    subtitle_sessions[chat_id] = {
        "video_path": tmp_path,
        "video_orig_name": filename,
        "status_msg_id": prompt_msg.id,
    }
    try:
        await event.edit(
            f"✅ Video downloaded. Now send the subtitle file.", buttons=None
        )
    except Exception:
        pass


async def subtitle_receive_handler(event):
    """وقتی کاربر فایل زیرنویس میفرسته → HappyScribe burn-in آنلاین."""
    if event.sender_id not in AUTHORIZED_USERS:
        return

    chat_id = event.chat_id
    session = subtitle_sessions.get(chat_id)
    if not session:
        return

    doc = event.document
    if not doc:
        return
    fname = ""
    for attr in getattr(doc, "attributes", []):
        fn = getattr(attr, "file_name", None)
        if fn:
            fname = fn
            break
    sub_ext = os.path.splitext(fname)[1].lower()
    if sub_ext not in (".srt", ".ass", ".ssa", ".vtt", ".sub"):
        return

    video_path = session.get("video_path")
    video_orig_name = session.get("video_orig_name", "video")
    status_msg_id = session.get("status_msg_id")
    subtitle_sessions.pop(chat_id, None)

    if status_msg_id:
        try:
            await event.client.delete_messages(chat_id, status_msg_id)
        except Exception:
            pass

    if not video_path or not os.path.exists(video_path):
        await event.reply("❌ Video file expired. Please send the video again.")
        raise events.StopPropagation

    status_msg = await event.reply("⬇️ Downloading subtitle file...")

    sub_path = os.path.join(OUTPUT_FOLDER, f"sub_{int(time.time())}{sub_ext}")
    try:
        await event.client.download_media(event.message, file=sub_path)
    except Exception as e:
        await safe_edit(status_msg, f"❌ Failed to download subtitle: {str(e)[:80]}")
        try:
            os.remove(video_path)
        except Exception:
            pass
        raise events.StopPropagation

    if not os.path.exists(sub_path) or os.path.getsize(sub_path) == 0:
        await safe_edit(status_msg, "❌ Subtitle file is empty.")
        try:
            os.remove(video_path)
        except Exception:
            pass
        raise events.StopPropagation

    # ── HappyScribe burn-in ──────────────────────────────────────────────
    async def _progress(text: str):
        await safe_edit(status_msg, text)

    download_url, error = await hardcode_subtitle_online(
        video_path=video_path,
        subtitle_path=sub_path,
        progress_callback=_progress,
    )

    # cleanup فایل‌های موقت
    for p in (video_path, sub_path):
        try:
            os.remove(p)
        except Exception:
            pass

    if error or not download_url:
        await safe_edit(
            status_msg, f"❌ HappyScribe error: {error or 'No download link received.'}"
        )
        raise events.StopPropagation

    # ── دانلود نتیجه از HappyScribe ─────────────────────────────────────
    out_name = os.path.splitext(video_orig_name)[0] + "_subtitled.mp4"
    out_path = os.path.join(OUTPUT_FOLDER, f"hs_{int(time.time())}_{out_name}")

    await safe_edit(status_msg, "⬇️ Downloading result from HappyScribe...")
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(download_url, timeout=ClientTimeout(total=600)) as resp:
                if resp.status != 200:
                    await safe_edit(
                        status_msg, f"❌ Download failed (HTTP {resp.status})"
                    )
                    raise events.StopPropagation
                async with aiofiles.open(out_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(1024 * 512):
                        await f.write(chunk)
    except Exception as e:
        await safe_edit(status_msg, f"❌ Download error: {str(e)[:80]}")
        raise events.StopPropagation

    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        await safe_edit(status_msg, "❌ Downloaded file is empty.")
        raise events.StopPropagation

    # ── آپلود به تلگرام ──────────────────────────────────────────────────
    out_size = os.path.getsize(out_path)
    vid_duration, _, _ = await get_video_info(out_path)
    sub_name = os.path.splitext(fname)[0]
    ul_id = f"sub_{chat_id}_{event.id}"
    cap = build_video_caption(out_name, out_size, vid_duration, sub_name)
    try:
        await send_file_with_progress(
            client=event.client,
            chat_id=chat_id,
            filepath=out_path,
            caption=cap,
            status_msg=status_msg,
            ul_id=ul_id,
        )
    except Exception as e:
        await safe_edit(status_msg, f"❌ Upload failed: {str(e)[:80]}")
    finally:
        active_uploads.pop(ul_id, None)
        try:
            os.remove(out_path)
        except Exception:
            pass
        try:
            await status_msg.delete()
        except Exception:
            pass

    raise events.StopPropagation


async def subextr_callback(event):
    """کاربر تأیید کرد از زیرنویس غیرفارسی داخل ویدیو استفاده کنه."""
    if event.sender_id not in AUTHORIZED_USERS:
        return await event.answer("⛔ Unauthorized", alert=True)

    chat_id = int(event.data.decode().replace("subextr_", ""))
    session = subtitle_sessions.pop(chat_id, None)
    if not session:
        return await event.answer("❌ Session expired.", alert=True)

    video_path = session["video_path"]
    orig_name = session["video_orig_name"]
    status_msg = session["status_msg"]
    sub_index = session.get("pending_sub_index")
    if sub_index is None:
        return await event.answer("❌ No subtitle stream info.", alert=True)

    await event.answer("⏳ Extracting subtitle...", alert=False)
    try:
        await event.edit("⏳ Extracting subtitle from video...", buttons=_sub_btn(chat_id))
    except Exception:
        pass

    subtitle_name = session.get("subtitle_name", "")

    out_srt = os.path.join(OUTPUT_FOLDER, f"extracted_sub_{int(time.time())}.srt")
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-map",
        f"0:{sub_index}",
        "-c:s",
        "srt",
        out_srt,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    if not os.path.exists(out_srt) or os.path.getsize(out_srt) == 0:
        try:
            await event.edit("❌ Failed to extract subtitle.", buttons=None)
        except Exception:
            pass
        try:
            os.remove(video_path)
        except Exception:
            pass
        return

    # Check if video is too large for HappyScribe (490MB+)
    video_size_mb = os.path.getsize(video_path) / (1024 * 1024)
    out_name = os.path.splitext(orig_name)[0] + "_subtitled.mp4"
    if video_size_mb > 490:
        await safe_edit(status_msg, f"📏 Video is {video_size_mb:.0f}MB — splitting into parts...", buttons=_sub_btn(chat_id))
        merged_path = await _burn_subtitle_split(
            event, chat_id, video_path, out_srt, subtitle_name,
            status_msg, orig_name,
        )
        try: os.remove(out_srt)
        except: pass
        try: os.remove(video_path)
        except: pass
        if merged_path:
            filepath = merged_path
            size = os.path.getsize(filepath)
        elif subburn_cancel.get(chat_id):
            subburn_cancel.pop(chat_id, None)
            try:
                await status_msg.delete()
            except Exception:
                pass
            try:
                await event.delete()
            except Exception:
                pass
            raise events.StopPropagation
        else:
            await safe_edit(status_msg, "⚠️ Split-burn failed, uploading original...", buttons=_sub_btn(chat_id))
            raise events.StopPropagation
    else:
        await safe_edit(status_msg, "🔤 Subtitle extracted! Sending to HappyScribe...", buttons=_sub_btn(chat_id))

        async def _prog(text):
            await safe_edit(status_msg, text, buttons=_sub_btn(chat_id))

        dl_url, err = await hardcode_subtitle_online(
            video_path=video_path,
            subtitle_path=out_srt,
            progress_callback=_prog,
        )
        try:
            os.remove(out_srt)
        except Exception:
            pass

        if not dl_url:
            await safe_edit(
                status_msg, f"⚠️ HappyScribe error: {err[:80]}\nUploading original...",
                buttons=_sub_btn(chat_id),
            )
            raise events.StopPropagation

        if subburn_cancel.get(chat_id):
            subburn_cancel.pop(chat_id, None)
            try:
                await status_msg.delete()
            except Exception:
                pass
            try:
                os.remove(video_path)
            except Exception:
                pass
            raise events.StopPropagation

        out_path = os.path.join(OUTPUT_FOLDER, f"hs_{int(time.time())}_{out_name}")
        await safe_edit(status_msg, "⬇️ Downloading result...", buttons=_sub_btn(chat_id))
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(dl_url, timeout=ClientTimeout(total=600)) as resp:
                    if resp.status == 200:
                        async with aiofiles.open(out_path, "wb") as f:
                            async for chunk in resp.content.iter_chunked(524288):
                                if subburn_cancel.get(chat_id):
                                    raise Exception("Cancelled by user")
                                await f.write(chunk)
        except Exception as e:
            if str(e) == "Cancelled by user":
                subburn_cancel.pop(chat_id, None)
                try: os.remove(video_path)
                except: pass
                try: os.remove(out_path)
                except: pass
                try:
                    await status_msg.delete()
                except Exception:
                    pass
                raise events.StopPropagation
            await safe_edit(status_msg, f"❌ Download error: {str(e)[:80]}", buttons=_sub_btn(chat_id))
            raise events.StopPropagation

        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            await safe_edit(status_msg, "⚠️ HappyScribe failed, uploading original...", buttons=_sub_btn(chat_id))
            raise events.StopPropagation

        try:
            os.remove(video_path)
        except Exception:
            pass

        filepath = out_path
        size = os.path.getsize(filepath)
    vid_duration, _, _ = await get_video_info(filepath)
    gh_line = ""
    if GITHUB_ENABLED:
        await safe_edit(status_msg, "☁️ Uploading to GitHub...", buttons=_sub_btn(chat_id))
        gh_url = await maybe_upload_github(event.client, chat_id, filepath, size)
        if gh_url:
            gh_line = f"\n☁️ [GitHub DL]({gh_url})"
        await safe_edit(status_msg, "📤 Uploading...", buttons=_sub_btn(chat_id))
    _ul_id = f"ul_{chat_id}_{int(time.time())}"
    cap = build_video_caption(out_name, size, vid_duration, subtitle_name)
    if gh_line:
        cap += gh_line
    await send_file_with_progress(
        client=event.client,
        chat_id=chat_id,
        filepath=filepath,
        caption=cap,
        status_msg=status_msg,
        ul_id=_ul_id,
    )
    active_uploads.pop(_ul_id, None)
    try:
        os.remove(filepath)
    except Exception:
        pass
    try:
        await status_msg.delete()
    except Exception:
        pass
    try:
        await event.delete()
    except Exception:
        pass
    raise events.StopPropagation


def _sub_btn(chat_id: int):
    return [[Button.inline("❌ Cancel", f"subburn_proc_cancel_{chat_id}")]]


async def _burn_subtitle_split(
    event, chat_id: int, video_path: str, subtitle_path: str,
    subtitle_name: str, status_msg, orig_name: str,
) -> Optional[str]:
    """
    برای ویدیوهای >490MB: split به پارت‌های <500MB، هرکدوم رو با زیرنویس
    به HappyScribe بده، دانلود کن، بعد concat کن.
    برمی‌گردونه مسیر فایل نهایی یا None.
    """
    total_size = os.path.getsize(video_path)
    MAX_PART = 485 * 1024 * 1024
    num_parts = math.ceil(total_size / MAX_PART)
    if num_parts < 2:
        return None

    await safe_edit(status_msg, f"✂️ Splitting video into {num_parts} parts...", buttons=_sub_btn(chat_id))
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    split_dir = os.path.join(OUTPUT_FOLDER, f"split_{int(time.time())}")
    os.makedirs(split_dir, exist_ok=True)
    split_pat = os.path.join(split_dir, "part_%03d.mp4")

    # Get total duration
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", video_path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    try:
        total_dur = float(json.loads(stdout.decode()).get("format", {}).get("duration", 0))
    except Exception:
        total_dur = 0
    if total_dur <= 0:
        total_dur = 600

    part_dur = total_dur / num_parts

    # Split by duration (segment muxer, re-encode to ensure clean keyframes)
    if subburn_cancel.get(chat_id):
        return None
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", video_path,
        "-c:v", "libx264", "-crf", "23", "-preset", "fast",
        "-c:a", "aac", "-b:a", "128k",
        "-map", "0",
        "-f", "segment", "-segment_time", str(part_dur),
        "-reset_timestamps", "1", "-avoid_negative_ts", "make_zero",
        split_pat,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    parts = sorted(glob.glob(os.path.join(split_dir, "part_*.mp4")))
    if not parts:
        shutil.rmtree(split_dir, ignore_errors=True)
        return None

    processed = []
    try:
        for i, part_path in enumerate(parts):
            if subburn_cancel.get(chat_id):
                raise Exception("Cancelled by user")
            part_size = os.path.getsize(part_path)
            if part_size < 1024:
                try: os.remove(part_path)
                except: pass
                continue

            await safe_edit(status_msg, f"🔄 Part {i+1}/{len(parts)} — sending to HappyScribe...", buttons=_sub_btn(chat_id))

            # Get actual part duration for subtitle trim
            proc = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", part_path,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            try:
                part_info = json.loads(stdout.decode())
                pdur = float(part_info.get("format", {}).get("duration", 0))
            except Exception:
                pdur = part_dur

            # Trim subtitle to match this part
            part_start = i * part_dur
            trimmed_sub = os.path.join(split_dir, f"sub_{i:03d}.srt")
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y",
                "-ss", str(part_start), "-t", str(pdur),
                "-i", subtitle_path,
                "-c:s", "srt", trimmed_sub,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()

            sub_for_part = trimmed_sub if (os.path.exists(trimmed_sub) and os.path.getsize(trimmed_sub) > 0) else subtitle_path

            if subburn_cancel.get(chat_id):
                raise Exception("Cancelled by user")

            # HappyScribe
            async def _prog(t):
                await safe_edit(status_msg, t, buttons=_sub_btn(chat_id))
            dl_url, err = await hardcode_subtitle_online(
                video_path=part_path, subtitle_path=sub_for_part,
                progress_callback=_prog,
            )
            if not dl_url:
                await safe_edit(status_msg, f"⚠️ Part {i+1} failed: {err[:80]}", buttons=_sub_btn(chat_id))
                raise Exception(f"Part {i+1} failed")

            if subburn_cancel.get(chat_id):
                raise Exception("Cancelled by user")

            # Download result
            out_part = os.path.join(split_dir, f"done_{i:03d}.mp4")
            await safe_edit(status_msg, f"⬇️ Downloading part {i+1}/{len(parts)}...", buttons=_sub_btn(chat_id))
            async with aiohttp.ClientSession() as sess:
                async with sess.get(dl_url, timeout=ClientTimeout(total=600)) as resp:
                    if resp.status != 200:
                        raise Exception(f"HTTP {resp.status}")
                    async with aiofiles.open(out_part, "wb") as f:
                        async for chunk in resp.content.iter_chunked(524288):
                            await f.write(chunk)
            if os.path.exists(out_part) and os.path.getsize(out_part) > 0:
                processed.append(out_part)

        if not processed:
            raise Exception("No parts processed")

        if subburn_cancel.get(chat_id):
            raise Exception("Cancelled by user")

        # Concatenate
        await safe_edit(status_msg, "🔗 Joining parts...", buttons=_sub_btn(chat_id))
        final_path = os.path.join(OUTPUT_FOLDER, f"merged_{int(time.time())}_{orig_name}")
        concat_file = os.path.join(split_dir, "concat.txt")
        with open(concat_file, "w") as f:
            for p in processed:
                f.write(f"file '{os.path.abspath(p)}'\n")

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", concat_file,
            "-c:v", "libx264", "-crf", "23", "-preset", "fast",
            "-c:a", "aac", "-b:a", "128k",
            final_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        if os.path.exists(final_path) and os.path.getsize(final_path) > 0:
            return final_path
    except Exception as e:
        if str(e) == "Cancelled by user":
            logger.info(f"[SPLIT-BURN] Cancelled by user for chat {chat_id}")
        else:
            logger.error(f"[SPLIT-BURN] Error: {e}")
    finally:
        shutil.rmtree(split_dir, ignore_errors=True)
        subburn_cancel.pop(chat_id, None)

    return None


async def subsend_callback(event):
    """کاربر دکمه ارسال ویدیو رو زد — بدون زیرنویس آپلود کن."""
    if event.sender_id not in AUTHORIZED_USERS:
        return await event.answer("⛔ Unauthorized", alert=True)
    m = re.match(r"subsend_(-?\d+)", event.data.decode())
    if not m:
        return await event.answer("❌ Invalid data.", alert=True)
    chat_id = int(m.group(1))
    session = subtitle_sessions.pop(chat_id, None)
    if not session:
        return await event.answer("❌ Session expired.", alert=True)
    await event.answer("📤 Sending video...", alert=False)
    video_path = session.get("video_path")
    video_orig_name = session.get("video_orig_name", "video")
    status_msg = session.get("status_msg")
    size = session.get("size", 0)
    dur_str = session.get("dur_str", "")
    status_msg_id = session.get("status_msg_id")
    if status_msg_id:
        try:
            await event.client.delete_messages(chat_id, status_msg_id)
        except Exception:
            pass
    try:
        await event.delete()
    except Exception:
        pass
    if not video_path or not os.path.exists(video_path):
        if status_msg:
            await safe_edit(status_msg, "❌ Video file expired.")
        return
    if status_msg:
        await safe_edit(status_msg, "📤 Uploading...")
    gh_line = ""
    if GITHUB_ENABLED:
        if status_msg:
            await safe_edit(status_msg, "☁️ Uploading to GitHub...")
        gh_url = await maybe_upload_github(event.client, chat_id, video_path, size)
        if gh_url:
            gh_line = f"\n☁️ [GitHub DL]({gh_url})"
        if status_msg:
            await safe_edit(status_msg, "📤 Uploading...")
    _ul_id = f"subsend_{chat_id}_{event.id}"
    vid_duration, _, _ = await get_video_info(video_path)
    cap = build_video_caption(video_orig_name, size, vid_duration)
    if gh_line:
        cap += gh_line
    try:
        await send_file_with_progress(
            client=event.client,
            chat_id=chat_id,
            filepath=video_path,
            caption=cap,
            status_msg=status_msg,
            ul_id=_ul_id,
        )
    except Exception as e:
        if status_msg:
            await safe_edit(status_msg, f"❌ Upload failed: {str(e)[:80]}")
    finally:
        active_uploads.pop(_ul_id, None)
        try:
            os.remove(video_path)
        except Exception:
            pass


async def subburn_list_callback(event):
    """کاربر دکمه سوختن زیرنویس رو زد — لیست زیرنویس‌ها رو نشون بده."""
    if event.sender_id not in AUTHORIZED_USERS:
        return await event.answer("⛔ Unauthorized", alert=True)
    m = re.match(r"subburn_list_(-?\d+)", event.data.decode())
    if not m:
        return await event.answer("❌ Invalid data.", alert=True)
    chat_id = int(m.group(1))
    session = subtitle_sessions.get(chat_id)
    if not session:
        return await event.answer("❌ Session expired.", alert=True)
    sub_streams = session.get("sub_streams", [])
    if not sub_streams:
        return await event.answer("❌ No subtitle streams found.", alert=True)
    buttons = []
    for i, s in enumerate(sub_streams):
        lang = s.get("language", "und")
        title = s.get("title", "")
        label = f"{lang}" + (f" — {title}" if title else "")
        buttons.append([Button.inline(label, f"subburn_sel_{chat_id}_{i}")])
    buttons.append([Button.inline("❌ Cancel", f"subcancl_{chat_id}")])
    try:
        await event.edit("🔥 **Choose a subtitle to burn:**", buttons=buttons)
    except Exception:
        pass


async def subburn_sel_callback(event):
    """کاربر یکی از زیرنویس‌ها رو انتخاب کرد — استخراج و HardSub."""
    if event.sender_id not in AUTHORIZED_USERS:
        return await event.answer("⛔ Unauthorized", alert=True)
    m = re.match(r"subburn_sel_(-?\d+)_(\d+)$", event.data.decode())
    if not m:
        return await event.answer("❌ Invalid data.", alert=True)
    chat_id = int(m.group(1))
    sub_idx = int(m.group(2))
    session = subtitle_sessions.pop(chat_id, None)
    if not session:
        return await event.answer("❌ Session expired.", alert=True)
    video_path = session["video_path"]
    orig_name = session["video_orig_name"]
    status_msg = session["status_msg"]
    sub_streams = session.get("sub_streams", [])
    if sub_idx >= len(sub_streams):
        return await event.answer("❌ Invalid subtitle index.", alert=True)
    sub_info = sub_streams[sub_idx]
    sub_index = sub_info["index"]
    sub_lang = sub_info.get("language", "und")
    sub_title = sub_info.get("title", "")
    subtitle_name = sub_lang + (f" — {sub_title}" if sub_title else "")
    await event.answer("⏳ Extracting subtitle...", alert=False)
    try:
        await event.edit("⏳ Extracting subtitle from video...", buttons=_sub_btn(chat_id))
    except Exception:
        pass
    out_srt = os.path.join(OUTPUT_FOLDER, f"extracted_sub_{int(time.time())}.srt")
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-i", video_path,
        "-map", f"0:{sub_index}",
        "-c:s", "srt",
        out_srt,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    if not os.path.exists(out_srt) or os.path.getsize(out_srt) == 0:
        try:
            await event.edit("❌ Failed to extract subtitle.", buttons=None)
        except Exception:
            pass
        try:
            os.remove(video_path)
        except Exception:
            pass
        return
    # Check if video is too large for HappyScribe (490MB+)
    video_size_mb = os.path.getsize(video_path) / (1024 * 1024)
    out_name = os.path.splitext(orig_name)[0] + "_subtitled.mp4"
    if video_size_mb > 490:
        await safe_edit(status_msg, f"📏 Video is {video_size_mb:.0f}MB — splitting into parts...", buttons=_sub_btn(chat_id))
        merged_path = await _burn_subtitle_split(
            event, chat_id, video_path, out_srt, subtitle_name,
            status_msg, orig_name,
        )
        try: os.remove(out_srt)
        except: pass
        try: os.remove(video_path)
        except: pass
        if merged_path:
            filepath = merged_path
            size = os.path.getsize(filepath)
        elif subburn_cancel.get(chat_id):
            subburn_cancel.pop(chat_id, None)
            try:
                await status_msg.delete()
            except Exception:
                pass
            try:
                await event.delete()
            except Exception:
                pass
            raise events.StopPropagation
        else:
            await safe_edit(status_msg, "⚠️ Split-burn failed, uploading original...", buttons=_sub_btn(chat_id))
            raise events.StopPropagation
    else:
        await safe_edit(status_msg, "🔤 Subtitle extracted! Sending to HappyScribe...", buttons=_sub_btn(chat_id))
        async def _prog(text):
            await safe_edit(status_msg, text, buttons=_sub_btn(chat_id))
        dl_url, err = await hardcode_subtitle_online(
            video_path=video_path,
            subtitle_path=out_srt,
            progress_callback=_prog,
        )
        try:
            os.remove(out_srt)
        except Exception:
            pass
        if not dl_url:
            await safe_edit(status_msg, f"⚠️ HappyScribe error: {err[:80]}\nUploading original...", buttons=_sub_btn(chat_id))
            raise events.StopPropagation
        if subburn_cancel.get(chat_id):
            subburn_cancel.pop(chat_id, None)
            try:
                await status_msg.delete()
            except Exception:
                pass
            try:
                os.remove(video_path)
            except Exception:
                pass
            raise events.StopPropagation
        out_path = os.path.join(OUTPUT_FOLDER, f"hs_{int(time.time())}_{out_name}")
        await safe_edit(status_msg, "⬇️ Downloading result...", buttons=_sub_btn(chat_id))
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(dl_url, timeout=ClientTimeout(total=600)) as resp:
                    if resp.status == 200:
                        async with aiofiles.open(out_path, "wb") as f:
                            async for chunk in resp.content.iter_chunked(524288):
                                if subburn_cancel.get(chat_id):
                                    raise Exception("Cancelled by user")
                                await f.write(chunk)
        except Exception as e:
            if str(e) == "Cancelled by user":
                subburn_cancel.pop(chat_id, None)
                try: os.remove(video_path)
                except: pass
                try: os.remove(out_path)
                except: pass
                try:
                    await status_msg.delete()
                except Exception:
                    pass
                raise events.StopPropagation
            await safe_edit(status_msg, f"❌ Download error: {str(e)[:80]}", buttons=_sub_btn(chat_id))
            raise events.StopPropagation
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            await safe_edit(status_msg, "⚠️ HappyScribe failed, uploading original...", buttons=_sub_btn(chat_id))
            raise events.StopPropagation
        try:
            os.remove(video_path)
        except Exception:
            pass
        filepath = out_path
        size = os.path.getsize(filepath)
    vid_duration, _, _ = await get_video_info(filepath)
    gh_line = ""
    if GITHUB_ENABLED:
        await safe_edit(status_msg, "☁️ Uploading to GitHub...", buttons=_sub_btn(chat_id))
        gh_url = await maybe_upload_github(event.client, chat_id, filepath, size)
        if gh_url:
            gh_line = f"\n☁️ [GitHub DL]({gh_url})"
        await safe_edit(status_msg, "📤 Uploading...", buttons=_sub_btn(chat_id))
    _ul_id = f"subburn_{chat_id}_{int(time.time())}"
    cap = build_video_caption(out_name, size, vid_duration, subtitle_name)
    if gh_line:
        cap += gh_line
    await send_file_with_progress(
        client=event.client,
        chat_id=chat_id,
        filepath=filepath,
        caption=cap,
        status_msg=status_msg,
        ul_id=_ul_id,
    )
    active_uploads.pop(_ul_id, None)
    try:
        os.remove(filepath)
    except Exception:
        pass
    try:
        await status_msg.delete()
    except Exception:
        pass
    try:
        await event.delete()
    except Exception:
        pass
    raise events.StopPropagation


async def subskip_callback(event):
    """کاربر skip زد — ویدیو رو بدون subtitle آپلود کن."""
    if event.sender_id not in AUTHORIZED_USERS:
        return await event.answer("⛔ Unauthorized", alert=True)

    parts = event.data.decode().replace("subskip_", "").split("_")
    chat_id = int(parts[0])

    session = subtitle_sessions.pop(chat_id, None)
    if not session:
        return await event.answer("❌ Session expired.", alert=True)

    await event.answer("⏭ Skipping subtitle...", alert=False)

    video_path = session.get("video_path")
    video_orig_name = session.get("video_orig_name", "video")
    status_msg = session.get("status_msg")
    size = session.get("size", 0)
    dur_str = session.get("dur_str", "")

    # پاک کردن پیام prompt
    status_msg_id = session.get("status_msg_id")
    if status_msg_id:
        try:
            await event.client.delete_messages(chat_id, status_msg_id)
        except Exception:
            pass
    try:
        await event.delete()
    except Exception:
        pass

    if not video_path or not os.path.exists(video_path):
        if status_msg:
            await safe_edit(status_msg, "❌ Video file expired.")
        return

    if status_msg:
        await safe_edit(status_msg, "📤 Uploading...")

    gh_line = ""
    if GITHUB_ENABLED:
        if status_msg:
            await safe_edit(status_msg, "☁️ Uploading to GitHub...")
        gh_url = await maybe_upload_github(event.client, chat_id, video_path, size)
        if gh_url:
            gh_line = f"\n☁️ [GitHub DL]({gh_url})"
        if status_msg:
            await safe_edit(status_msg, "📤 Uploading...")

    _ul_id = f"subskip_{chat_id}_{event.id}"
    vid_duration, _, _ = await get_video_info(video_path)
    cap = build_video_caption(video_orig_name, size, vid_duration)
    if gh_line:
        cap += gh_line
    try:
        await send_file_with_progress(
            client=event.client,
            chat_id=chat_id,
            filepath=video_path,
            caption=cap,
            status_msg=status_msg,
            ul_id=_ul_id,
        )
    except Exception as e:
        if status_msg:
            await safe_edit(status_msg, f"❌ Upload failed: {str(e)[:80]}")
    finally:
        active_uploads.pop(_ul_id, None)
        try:
            os.remove(video_path)
        except Exception:
            pass


async def subburn_proc_cancel_callback(event):
    """Cancel a running subtitle burn process mid-way."""
    if event.sender_id not in AUTHORIZED_USERS:
        return await event.answer("⛔ Unauthorized", alert=True)
    raw = event.data.decode().replace("subburn_proc_cancel_", "")
    try:
        chat_id = int(raw.split("_")[0])
    except Exception:
        chat_id = int(raw)
    subburn_cancel[chat_id] = True
    session = subtitle_sessions.get(chat_id)
    if session:
        try:
            os.remove(session.get("video_path", ""))
        except Exception:
            pass
        subtitle_sessions.pop(chat_id, None)
    await event.answer("🚫 Cancelling subtitle burn...", alert=False)
    try:
        await event.delete()
    except Exception:
        pass


async def subtitle_cancel_callback(event):
    if event.sender_id not in AUTHORIZED_USERS:
        return await event.answer("⛔ Unauthorized", alert=True)
    raw = event.data.decode().replace("subcancl_", "")
    try:
        chat_id = int(raw.split("_")[0])
    except Exception:
        chat_id = int(raw)
    session = subtitle_sessions.pop(chat_id, None)
    if session:
        try:
            os.remove(session["video_path"])
        except Exception:
            pass
        status_msg = session.get("status_msg")
        if status_msg:
            try:
                await safe_edit(status_msg, "🚫 Subtitle burn cancelled.")
            except Exception:
                pass
    await event.answer("Cancelled", alert=False)
    try:
        await event.delete()
    except Exception:
        pass


# ====================== SNAPWC HANDLERS ======================


async def snapwc_command(event):
    if event.sender_id not in AUTHORIZED_USERS:
        return await event.reply("⛔ Unauthorized")
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await event.reply("❌ Usage: `/snapwc <url>`", parse_mode="markdown")

    url = parts[1].strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    status_msg = await event.reply("🔄 Starting SnapWC session...")
    logger.info(f"[SNAPWC] START | chat={event.chat_id} | url={url[:120]}")

    await _run_snapwc_flow(event, url, status_msg)


async def _run_snapwc_flow(event, url, status_msg):

    if status_msg is None:
        status_msg = await event.reply("🔍 در حال استخراج کیفیت‌ها...")

    session = SnapWCSession()
    try:
        result = await asyncio.wait_for(session.run_full_flow(url), timeout=180)

        if not result["success"]:
            steps = result.get("steps", [])
            err = result.get("error", "Unknown")
            log = "\n".join(f"  • {s}" for s in steps)
            logger.error(f"[SNAPWC] run_full_flow failed: {err} | steps: {log}")
            await safe_edit(status_msg, f"❌ SnapWC error: {err}")
            ss = result.get("screenshot_b64", "")
            if ss:
                try:
                    await event.client.send_file(
                        event.chat_id,
                        base64.b64decode(ss),
                        caption=f"📸 SnapWC screenshot: {err[:80]}",
                    )
                except Exception:
                    pass
            await session.close_browser()
            return

        qualities = result.get("qualities", [])
        if not qualities:
            await safe_edit(status_msg, "❌ No quality options found.")
            await session.close_browser()
            return

        session_id = f"snapwc_{event.chat_id}_{event.id}_{int(time.time())}"
        snapwc_sessions[session_id] = session
        user_state[event.chat_id] = {
            "action": "snapwc_quality",
            "session_id": session_id,
            "video_url": url,
        }

        grouped = {"Video": [], "No Sound": [], "Audio": []}
        for q in qualities:
            cat = q["category"]
            if cat in grouped:
                grouped[cat].append(q)

        msg_lines = [f"🎬 **SnapWC — {len(qualities)} options found:**\n"]
        cat_icons = {"Video": "🎬", "No Sound": "🔇", "Audio": "🎵"}
        idx = 1
        buttons = []
        for cat in ["Video", "No Sound", "Audio"]:
            items = grouped.get(cat, [])
            if not items:
                continue
            msg_lines.append(f"\n{cat_icons[cat]} **{cat}**")
            for q in items:
                sz = f" ({q['size']})" if q.get("size") else ""
                msg_lines.append(f"  {idx}. {q['label']}{sz}")
                btn_emoji = cat_icons.get(q["category"], "📁")
                buttons.append(
                    [
                        Button.inline(
                            f"{btn_emoji} {q['label']}{sz}",
                            f"snapwc_q_{session_id}_{q['index']}",
                        )
                    ]
                )
                idx += 1

        buttons.append([Button.inline("❌ Cancel", f"snapwc_cancel_{session_id}")])

        await safe_edit(
            status_msg,
            "\n".join(msg_lines),
            buttons=buttons,
        )

    except Exception as e:
        logger.error(f"[SNAPWC] Command error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ SnapWC error: {str(e)[:120]}")
        try:
            ss = await session.take_screenshot()
            if ss:
                await event.client.send_file(
                    event.chat_id,
                    base64.b64decode(ss),
                    caption=f"📸 SnapWC error screenshot",
                )
        except Exception:
            pass
        try:
            await session.close_browser()
        except Exception:
            pass


async def snapwc_select_callback(event):
    data = event.data.decode()
    prefix_removed = data.replace("snapwc_q_", "")
    session_id = prefix_removed.rsplit("_", 1)[0]
    index = int(prefix_removed.rsplit("_", 1)[1])

    if session_id not in snapwc_sessions:
        return await event.answer("❌ Session expired. Run /snapwc again.", alert=True)

    session = snapwc_sessions.pop(session_id, None)
    if not session:
        return await event.answer("❌ Session expired. Run /snapwc again.", alert=True)

    await event.answer("⏳ Processing...", alert=False)

    try:
        result = await session.continue_with_quality(index)

        if result.get("captcha"):
            captcha_b64 = result["captcha_image"]
            if "," in captcha_b64:
                raw_b64 = captcha_b64.split(",", 1)[1]
            else:
                raw_b64 = captcha_b64
            captcha_data = base64.b64decode(raw_b64)

            captcha_path = os.path.join(OUTPUT_FOLDER, f"captcha_{session_id}.png")
            async with aiofiles.open(captcha_path, "wb") as f:
                await f.write(captcha_data)

            await event.client.send_file(
                event.chat_id,
                captcha_path,
                caption="🔐 **Captcha detected!**\nPlease enter the code from the image.",
                buttons=[Button.inline("❌ Cancel", f"snapwc_cancel_{session_id}")],
            )

            try:
                os.remove(captcha_path)
            except Exception:
                pass

            user_state[event.chat_id] = {
                "action": "snapwc_captcha",
                "session_id": session_id,
                "selected_index": index,
                "video_url": user_state.get(event.chat_id, {}).get("video_url", ""),
            }

            await safe_edit(event, "🔐 Captcha required — check the image sent above.")
            return

        if result["success"]:
            download_url = result["download_url"]
            download_headers = result.get("download_headers", {})
            title = result.get("title", "")
            download_data = result.get("download_data", {})

            steps = result.get("steps", [])
            logger.info(f"[SNAPWC] Quality selected OK | steps: {' → '.join(steps)}")

            # If browser already downloaded the file, send directly
            if download_data.get("browser_download") and download_data.get("filepath"):
                filepath = download_data["filepath"]
                file_size = download_data.get("file_size", 0)
                status_msg = await event.client.send_message(
                    event.chat_id, "✅ File downloaded via browser! Uploading..."
                )
                caption_start = f"🎬 {title}" if title else "📄 **SnapWC Download**"
                await send_file_with_progress(
                    client=event.client,
                    chat_id=event.chat_id,
                    filepath=filepath,
                    caption=(
                        f"{caption_start}\n📦 Size: {human_readable_size(file_size)}"
                    ),
                    status_msg=status_msg,
                )
                try:
                    os.remove(filepath)
                except Exception:
                    pass
                user_state.pop(event.chat_id, None)
                return

            status_msg = await event.client.send_message(
                event.chat_id, "✅ Got download link! Downloading..."
            )

            video_url = user_state.get(event.chat_id, {}).get("video_url", "")
            dl_ok = await do_download_and_send(
                event,
                status_msg,
                download_url,
                video_url,
                title=title,
                extra_headers=download_headers if download_headers else None,
            )

            # Even if download failed, send the direct link to user
            if not dl_ok and download_url:
                try:
                    await event.client.send_message(
                        event.chat_id,
                        f"⬇️ **Direct download link (try manually):**\n`{download_url}`\n_Links may expire quickly._",
                        parse_mode="markdown",
                        link_preview=False,
                    )
                except Exception:
                    pass

            # Retry once on failure: get fresh URL from SnapWC
            if not dl_ok and video_url:
                retry_msg = await event.client.send_message(
                    event.chat_id,
                    f"🔄 **Retrying SnapWC — fresh download link...**\nPrevious error logged.",
                )
                logger.info(
                    f"[SNAPWC] Retry started | index={index} | url={video_url[:80]}"
                )
                new_session = None
                try:
                    new_session = SnapWCSession()
                    await safe_edit(retry_msg, "🔄 Step 1/3: Loading SnapWC...")
                    new_result = await new_session.run_full_flow(video_url)
                    if new_result["success"]:
                        await safe_edit(retry_msg, "🔄 Step 2/3: Selecting quality...")
                        new_dl = await new_session.continue_with_quality(index)
                        if new_dl.get("success") and not new_dl.get("captcha"):
                            fresh_url = new_dl["download_url"]
                            fresh_headers = new_dl.get("download_headers", {})
                            fresh_title = new_dl.get("title", title)
                            fresh_download_data = new_dl.get("download_data", {})

                            # If browser already downloaded the file, send directly
                            if fresh_download_data.get(
                                "browser_download"
                            ) and fresh_download_data.get("filepath"):
                                filepath = fresh_download_data["filepath"]
                                file_size = fresh_download_data.get("file_size", 0)
                                await safe_edit(
                                    retry_msg,
                                    "✅ File downloaded via browser! Uploading...",
                                )
                                caption_start = (
                                    f"🎬 {fresh_title}"
                                    if fresh_title
                                    else "📄 **SnapWC Download**"
                                )
                                await send_file_with_progress(
                                    client=event.client,
                                    chat_id=event.chat_id,
                                    filepath=filepath,
                                    caption=(
                                        f"{caption_start}\n"
                                        f"📦 Size: {human_readable_size(file_size)}"
                                    ),
                                    status_msg=retry_msg,
                                )
                                try:
                                    os.remove(filepath)
                                except Exception:
                                    pass
                            else:
                                await safe_edit(
                                    retry_msg, "🔄 Step 3/3: Retrying download..."
                                )
                                retry_ok = await do_download_and_send(
                                    event,
                                    retry_msg,
                                    fresh_url,
                                    video_url,
                                    extra_headers=fresh_headers
                                    if fresh_headers
                                    else None,
                                    title=fresh_title,
                                )
                                if not retry_ok:
                                    await safe_edit(
                                        retry_msg,
                                        "❌ Retry also failed. SnapWC may be having issues.",
                                    )
                        elif new_dl.get("captcha"):
                            await safe_edit(
                                retry_msg, "🔐 Captcha on retry — run /snapwc again."
                            )
                        else:
                            err = new_dl.get("error", "Unknown")
                            steps = " → ".join(new_dl.get("steps", []))
                            logger.error(
                                f"[SNAPWC] Retry continue_with_quality failed: {err} | steps: {steps}"
                            )
                            await safe_edit(retry_msg, f"❌ Retry failed: {err}")
                    else:
                        err = new_result.get("error", "Unknown")
                        steps = " → ".join(new_result.get("steps", []))
                        logger.error(
                            f"[SNAPWC] Retry run_full_flow failed: {err} | steps: {steps}"
                        )
                        await safe_edit(retry_msg, f"❌ SnapWC retry failed: {err}")
                except Exception as retry_e:
                    logger.error(f"[SNAPWC] Retry error: {retry_e}", exc_info=True)
                    await safe_edit(retry_msg, f"❌ Retry error: {str(retry_e)[:120]}")
                finally:
                    if new_session:
                        try:
                            await new_session.close_browser()
                        except Exception:
                            pass

            user_state.pop(event.chat_id, None)
        else:
            err = result.get("error", "Unknown")
            steps = result.get("steps", [])
            log = "\n".join(f"  • {s}" for s in steps)
            logger.error(f"[SNAPWC] continue_with_quality failed: {err}\n{log}")
            await safe_edit(event, f"❌ Error: {err}")
            ss = result.get("screenshot_b64", "")
            if ss:
                try:
                    await event.client.send_file(
                        event.chat_id,
                        base64.b64decode(ss),
                        caption=f"📸 SnapWC screenshot: {err[:80]}",
                    )
                except Exception:
                    pass
            user_state.pop(event.chat_id, None)

    except Exception as e:
        logger.error(f"[SNAPWC] Select callback error: {e}", exc_info=True)
        await safe_edit(event, f"❌ Error: {str(e)[:120]}")
        snapwc_sessions.pop(session_id, None)
        user_state.pop(event.chat_id, None)


async def snapwc_captcha_handler(event):
    if event.sender_id not in AUTHORIZED_USERS:
        return
    state = user_state.get(event.chat_id)
    if not state or state.get("action") != "snapwc_captcha":
        return

    session_id = state.get("session_id", "")
    index = state.get("selected_index", 0)
    code = event.raw_text.strip()

    if session_id not in snapwc_sessions:
        await event.reply("❌ Session expired. Please run /snapwc again.")
        user_state.pop(event.chat_id, None)
        raise events.StopPropagation

    session = snapwc_sessions[session_id]
    status_msg = await event.reply("⏳ Submitting captcha...")

    try:
        result = await session.continue_after_captcha(code, index)

        if result["success"]:
            download_url = result["download_url"]
            download_headers = result.get("download_headers", {})
            title = result.get("title", "")
            download_data = result.get("download_data", {})

            # If browser already downloaded the file, send directly
            if download_data.get("browser_download") and download_data.get("filepath"):
                filepath = download_data["filepath"]
                file_size = download_data.get("file_size", 0)
                await safe_edit(
                    status_msg, "✅ File downloaded via browser! Uploading..."
                )
                caption_start = f"🎬 {title}" if title else "📄 **SnapWC Download**"
                await send_file_with_progress(
                    client=event.client,
                    chat_id=event.chat_id,
                    filepath=filepath,
                    caption=(
                        f"{caption_start}\n📦 Size: {human_readable_size(file_size)}"
                    ),
                    status_msg=status_msg,
                )
                try:
                    os.remove(filepath)
                except Exception:
                    pass
                return

            await safe_edit(status_msg, "✅ Captcha solved! Starting download...")
            video_url = state.get("video_url", "")
            await do_download_and_send(
                event,
                status_msg,
                download_url,
                video_url,
                extra_headers=download_headers if download_headers else None,
                title=title,
            )
        else:
            await safe_edit(status_msg, f"❌ {result.get('error', 'Captcha failed')}")
    except Exception as e:
        logger.error(f"[SNAPWC] Captcha error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ Error: {str(e)[:120]}")
    finally:
        snapwc_sessions.pop(session_id, None)
        user_state.pop(event.chat_id, None)

    raise events.StopPropagation


async def snapwc_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("snapwc_cancel_", "")
    if session_id in snapwc_sessions:
        session = snapwc_sessions.pop(session_id)
        try:
            await session.close_browser()
        except Exception:
            pass
    user_state.pop(event.chat_id, None)
    await event.answer("❌ Cancelled", alert=False)
    try:
        await event.edit("❌ SnapWC session cancelled.", buttons=None)
    except Exception:
        pass


# ====================== Y2MATE CALLBACK HANDLERS ======================


async def y2mate_quality_callback(event):
    data = event.data.decode()
    rest = data[5:]
    idx_pos = rest.rfind("_")
    if idx_pos == -1:
        return await event.answer("Invalid callback.", alert=True)
    pick_id = rest[:idx_pos]
    try:
        idx = int(rest[idx_pos + 1 :])
    except ValueError:
        return await event.answer("Invalid quality index.", alert=True)

    if pick_id not in y2mate_sessions:
        return await event.answer("Session expired. Send link again.", alert=True)

    entry = y2mate_sessions.pop(pick_id)
    session = entry["session"]
    qualities = entry["qualities"]
    source_url = entry["source_url"]
    yt_title = entry["title"]

    try:
        await event.answer("⏬ Downloading...", alert=False)
        await event.edit("📥 Processing your selection...", buttons=None)

        q = qualities[idx]
        dl_result = await session.select_quality(idx)
        if not dl_result["success"]:
            await event.edit(f"❌ Failed: {dl_result.get('error', 'Unknown')}")
            await session.close_browser()
            return

        dl_url = dl_result["download_url"]
        await session.close_browser()

        status_msg = await event.get_message()
        await safe_edit(status_msg, "📥 Downloading file...")
        is_audio = q.get("format") == "mp3" or "kbps" in q.get("label", "").lower()
        dl_id = f"dl_{event.chat_id}_{event.id}_{int(time.time())}"
        active_downloads[dl_id] = {"paused": False, "cancelled": False}
        filepath, dl_error, final_size = await download_with_controls(
            dl_url,
            status_msg,
            dl_id,
            referer="https://v21.www-y2mate.com/",
            extra_headers={"Referer": "https://v21.www-y2mate.com/"},
        )

        if dl_error or not filepath:
            await safe_edit(status_msg, f"❌ Download failed: {dl_error}")
            return

        await safe_edit(status_msg, "📤 Uploading...")
        try:
            if is_audio:
                base = os.path.splitext(filepath)[0]
                new_path = base + ".mp3"
                if filepath != new_path:
                    try:
                        os.rename(filepath, new_path)
                        filepath = new_path
                    except Exception:
                        pass

            clean_title = (
                yt_title if yt_title and "free download" not in yt_title.lower() else ""
            )
            caption_start = (
                f"🎬 {_escape_md(clean_title)}"
                if clean_title
                else (
                    "🎵 Audio"
                    if is_audio
                    else f"📄 {_escape_md(os.path.basename(filepath))}"
                )
            )
            gh_line = ""
            if GITHUB_ENABLED:
                gh_url = await maybe_upload_github(
                    event.client, event.chat_id, filepath, final_size
                )
                if gh_url:
                    gh_line = f"\n☁️ [GitHub DL]({gh_url})"

            # دانلود تامبنیل یوتیوب
            thumb_fp = None
            if not is_audio and "youtube" in source_url.lower():
                try:
                    import re as _re

                    ym = _re.search(
                        r"(?:v=|youtu\.be/|/v/|/embed/|/shorts/)([a-zA-Z0-9_-]{11})",
                        source_url,
                    )
                    if ym:
                        vid = ym.group(1)
                        turl = f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg"
                        async with aiohttp.ClientSession() as sess:
                            async with sess.get(
                                turl, timeout=aiohttp.ClientTimeout(total=10)
                            ) as resp:
                                if resp.status == 200:
                                    tfp = filepath + "_ytthumb.jpg"
                                    async with aiofiles.open(tfp, "wb") as f:
                                        async for chunk in resp.content.iter_chunked(
                                            65536
                                        ):
                                            await f.write(chunk)
                                    if os.path.getsize(tfp) > 0:
                                        thumb_fp = tfp
                except Exception:
                    pass

            ul_id = f"y2mcb_ul_{event.chat_id}_{event.id}_{int(time.time())}"
            sent_msg = await send_file_with_progress(
                client=event.client,
                chat_id=event.chat_id,
                filepath=filepath,
                caption=f"{caption_start}\n📦 {human_readable_size(final_size)}\n🔗 [Source]({source_url}){gh_line}",
                status_msg=status_msg,
                thumb_filepath=thumb_fp,
                ul_id=ul_id,
            )
            # پاک کردن تامبنیل موقت
            if thumb_fp and os.path.exists(thumb_fp):
                try:
                    os.remove(thumb_fp)
                except Exception:
                    pass
            try:
                os.remove(filepath)
            except Exception:
                pass

            if sent_msg and "youtube" in source_url.lower():
                try:
                    await safe_edit(status_msg, "📝 Getting video info...")
                    info = await asyncio.wait_for(
                        extract_youtube_info(source_url), timeout=60
                    )
                    if isinstance(info, dict):
                        title = info.get("title", "")
                        desc = info.get("description", "")
                    else:
                        lines = info.split("\n")
                        clean_lines = [
                            l.strip()
                            for l in lines
                            if l.strip()
                            and l.strip()
                            not in ("Free Download", "TITLE & DESCRIPTION:", "---")
                        ]
                        title = clean_lines[0] if clean_lines else yt_title
                        desc = (
                            "\n".join(clean_lines[1:]).strip()
                            if len(clean_lines) > 1
                            else ""
                        )
                    extra = ""
                    if title:
                        extra += f"\n🎬 **{_escape_md(title)}**"
                    if desc:
                        extra += f"\n📝 {_escape_md(desc)}"
                    if extra:
                        new_caption = f"{caption_start}\n📦 {human_readable_size(final_size)}\n🔗 [Source]({source_url}){gh_line}{extra}"
                        try:
                            await event.client.edit_message(
                                event.chat_id, sent_msg.id, text=new_caption
                            )
                        except Exception:
                            try:
                                await event.client.edit_message(
                                    event.chat_id,
                                    sent_msg.id,
                                    text=new_caption,
                                    parse_mode=None,
                                )
                            except Exception:
                                pass
                except Exception as e:
                    logger.error(f"[Y2MATE_EXTRACT] Error: {e}", exc_info=True)
                    try:
                        err_msg = str(e)[:200]
                        ss_b64 = getattr(e, "screenshot_b64", "")
                        if ss_b64:
                            await event.client.send_file(
                                event.chat_id,
                                base64.b64decode(ss_b64),
                                caption=f"⚠️ Extractor failed:\n{err_msg}",
                            )
                        else:
                            await event.client.send_message(
                                event.chat_id, f"⚠️ Extractor log: {err_msg}"
                            )
                    except Exception:
                        pass
                except Exception:
                    pass
        except Exception as e:
            await safe_edit(status_msg, f"❌ Upload failed: {str(e)[:100]}")
    except Exception as e:
        logger.error(f"[Y2MATE_CB] Error: {e}", exc_info=True)
        try:
            await event.edit(f"❌ Error: {str(e)[:100]}")
        except Exception:
            pass
        try:
            await session.close_browser()
        except Exception:
            pass
    raise events.StopPropagation


async def y2mate_cancel_callback(event):
    pick_id = event.data.decode()[5:]
    if pick_id in y2mate_sessions:
        entry = y2mate_sessions.pop(pick_id)
        try:
            await entry["session"].close_browser()
        except Exception:
            pass
    await event.answer("❌ Cancelled", alert=False)
    try:
        await event.edit("❌ Y2Mate cancelled.", buttons=None)
    except Exception:
        pass


async def savep_cancel_callback(event):
    session_id = event.data.decode().replace("savep_cancel_", "")
    cancelled = trigger_savep_cancel(session_id)
    await event.answer("🚫 Cancelling..." if cancelled else "Already done", alert=False)
    if cancelled:
        try:
            await event.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass


# ====================== XNXX HANDLER ======================


async def process_xnxx_request(event, url: str, status_msg):
    qualities, title = await extract_xnxx_qualities(url)
    if not qualities:
        await safe_edit(status_msg, "❌ کیفیتی پیدا نشد. لینک رو چک کن.")
        return
    session_id = f"xnxx_{event.chat_id}_{event.id}_{int(time.time())}"
    xnxx_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
    }
    title_display = title[:60] if title else "ویدیو XNXX"
    text = f"🎬 **{title_display}**\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"xnxx_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"xnxx_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def xnxx_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in xnxx_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = xnxx_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "xnxx_video"
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "xnxx_video"
    filename = f"{safe_title}_{int(time.time())}.mp4"
    filepath = os.path.join(OUTPUT_FOLDER, filename)
    dl_id = f"xnxx_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        if chosen["method"] == "direct":
            success, error, size = await download_xnxx_direct(
                chosen["url"], filepath, progress_cb
            )
        else:
            success, error, size = await download_xnxx_m3u8(
                chosen["url"], filepath, progress_cb
            )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success or not os.path.exists(filepath) or size < 1024:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return
        ul_id = f"xnxx_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = (
            f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(size)}"
        )
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[XNXX] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def xnxx_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("xnxx_cancel_", "")
    xnxx_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ====================== PORNHUB HANDLER ======================


async def process_pornhub_request(event, url: str, status_msg):
    qualities, title = await extract_pornhub_qualities(url)
    if not qualities:
        await safe_edit(status_msg, "❌ کیفیتی پیدا نشد. لینک رو چک کن.")
        return
    session_id = f"pornhub_{event.chat_id}_{event.id}_{int(time.time())}"
    pornhub_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
    }
    title_display = title[:60] if title else "ویدیو PornHub"
    text = f"🎬 **{title_display}**\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"pornhub_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"pornhub_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def pornhub_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in pornhub_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = pornhub_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "pornhub_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "pornhub_video"

    dl_id = f"pornhub_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    filepath = os.path.join(OUTPUT_FOLDER, f"ph_{safe_title}_{int(time.time())}.mp4")
    try:
        success, error, file_size = await download_pornhub_video(
            url, chosen["format_id"], filepath, progress_cb
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return

        ul_id = f"pornhub_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(file_size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[PORNHUB] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def pornhub_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("pornhub_cancel_", "")
    pornhub_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ====================== CARTOONPORN HANDLER ======================


async def process_cartoonporn_request(event, url: str, status_msg):
    qualities, title = await extract_cartoonporn_qualities(url)
    if not qualities:
        await safe_edit(status_msg, "❌ کیفیتی پیدا نشد. لینک رو چک کن.")
        return
    session_id = f"cp_{event.chat_id}_{event.id}_{int(time.time())}"
    cartoonporn_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
        "created_at": time.time(),
    }
    title_display = title[:60] if title else "ویدیو CartoonPorn"
    text = f"🎬 **{title_display}**\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"cp_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"cp_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def cartoonporn_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in cartoonporn_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = cartoonporn_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "cartoonporn_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "cartoonporn_video"

    filepath = os.path.join(OUTPUT_FOLDER, f"cp_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"cp_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, file_size = await download_cartoonporn_video(
            url=url,
            video_url=chosen.get("url", ""),
            filepath=filepath,
            method=chosen.get("method", "direct"),
            format_id=chosen.get("format_id", ""),
            progress_cb=progress_cb,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return

        ul_id = f"cp_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(file_size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[CP] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def cartoonporn_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("cp_cancel_", "")
    cartoonporn_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ====================== RULE34 HANDLER ======================


async def process_rule34_request(event, url: str, status_msg):
    try:
        post, error = await extract_rule34_post(url)
        if not post:
            await safe_edit(status_msg, f"❌ {error}")
            return

        file_url = post.get("file_url", "")
        title = post.get("title", "Rule34 Post")
        media_type = post.get("media_type", "image")
        tags = post.get("tags", "")

        if not file_url:
            await safe_edit(status_msg, "❌ لینک فایل پیدا نشد")
            return

        logger.info(f"[RULE34] Post #{post['post_id']} | {media_type} | {title[:50]}")

        ext_map = {
            "video": ".mp4",
            "gif": ".gif",
            "image": ".jpg",
        }
        ext = ext_map.get(media_type, ".mp4")
        filepath = os.path.join("output_files", f"rule34_{post['post_id']}{ext}")

        os.makedirs("output_files", exist_ok=True)

        await safe_edit(status_msg, f"📥 **در حال دانلود...**\n🎬 {title}")

        async def progress_cb(text):
            try:
                await status_msg.edit(text)
            except Exception:
                pass

        success, dl_error, size = await download_rule34(file_url, filepath, progress_cb)
        if not success or not os.path.exists(filepath):
            await safe_edit(status_msg, f"❌ دانلود ناموفق: {dl_error or 'Unknown'}")
            return

        media_type_display = {"video": "🎬", "image": "🖼", "gif": "🎞"}.get(
            media_type, "📄"
        )
        caption = (
            f"{media_type_display} **{title[:80]}**\n"
            f"🏷 `{tags[:200]}`\n"
            f"📦 {human_readable_size(size)}"
        )[:1024]

        await safe_edit(status_msg, "📤 **در حال آپلود...**")

        if media_type == "video":
            await send_file_with_progress(
                client=event.client,
                chat_id=event.chat_id,
                filepath=filepath,
                caption=caption,
                status_msg=status_msg,
                supports_streaming=True,
            )
        else:
            await send_file_with_progress(
                client=event.client,
                chat_id=event.chat_id,
                filepath=filepath,
                caption=caption,
                status_msg=status_msg,
            )
    except Exception as e:
        logger.error(f"[RULE34] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


# ====================== RULE34VIDEO HANDLER ======================


async def process_rule34video_request(event, url: str, status_msg):
    qualities, title = await extract_rule34video_qualities(url)
    if not qualities:
        await safe_edit(status_msg, "❌ کیفیتی پیدا نشد. لینک رو چک کن.")
        return
    session_id = f"r34v_{event.chat_id}_{event.id}_{int(time.time())}"
    rule34video_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
        "created_at": time.time(),
    }
    title_display = title[:60] if title else "ویدیو Rule34Video"
    text = f"🎬 **{title_display}**\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"r34v_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"r34v_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def rule34video_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in rule34video_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = rule34video_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "rule34video_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "rule34video_video"

    filepath = os.path.join(OUTPUT_FOLDER, f"r34v_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"r34v_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, file_size = await download_rule34video(
            url=url,
            video_url=chosen.get("url", ""),
            filepath=filepath,
            method=chosen.get("method", "direct"),
            format_id=chosen.get("format_id", ""),
            progress_cb=progress_cb,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return

        ul_id = f"r34v_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(file_size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[R34V] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def rule34video_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("r34v_cancel_", "")
    rule34video_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


rule34video_sessions: Dict[str, dict] = {}


# ====================== XANIMU HANDLER ======================


async def process_xanimu_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities, title, info = await extract_xanimu_qualities(
        url,
        progress_cb=progress_cb,
    )
    if not qualities:
        await safe_edit(status_msg, "❌ کیفیتی پیدا نشد.")
        return
    session_id = f"xa_{event.chat_id}_{event.id}_{int(time.time())}"
    xanimu_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
        "created_at": time.time(),
    }
    title_display = title[:60] if title else "ویدیو XAnimu"
    text = f"🎬 **{title_display}**\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"xa_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"xa_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def xanimu_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in xanimu_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = xanimu_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "xanimu_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "xanimu_video"

    filepath = os.path.join(OUTPUT_FOLDER, f"xa_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"xa_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, file_size = await download_xanimu_video(
            page_url=url,
            video_url=chosen.get("url", ""),
            filepath=filepath,
            progress_cb=progress_cb,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return

        ul_id = f"xa_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(file_size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[XA] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def xanimu_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("xa_cancel_", "")
    xanimu_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ====================== PORNTREX HANDLER ======================


async def process_porntrex_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities, title, info = await extract_porntrex_qualities(
        url,
        progress_cb=progress_cb,
    )
    if not qualities:
        await safe_edit(status_msg, "❌ کیفیتی پیدا نشد.")
        return
    session_id = f"pt_{event.chat_id}_{event.id}_{int(time.time())}"
    porntrex_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
        "created_at": time.time(),
    }
    title_display = title[:60] if title else "ویدیو Porntrex"
    text = f"🎬 **{title_display}**\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"pt_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"pt_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def porntrex_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in porntrex_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = porntrex_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "porntrex_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "porntrex_video"

    filepath = os.path.join(OUTPUT_FOLDER, f"pt_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"pt_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, file_size = await download_porntrex_video(
            page_url=url,
            video_url=chosen.get("url", ""),
            filepath=filepath,
            progress_cb=progress_cb,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return

        ul_id = f"pt_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(file_size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[PT] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def porntrex_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("pt_cancel_", "")
    porntrex_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ====================== HEAVYR HANDLER ======================


async def process_heavyr_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities, title, info = await extract_heavyr_qualities(
        url,
        progress_cb=progress_cb,
    )
    if not qualities:
        await safe_edit(status_msg, "❌ کیفیتی پیدا نشد.")
        return
    session_id = f"hr_{event.chat_id}_{event.id}_{int(time.time())}"
    heavyr_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
        "created_at": time.time(),
    }
    title_display = title[:60] if title else "ویدیو HeavyR"
    text = f"🎬 **{title_display}**\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"hr_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"hr_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def heavyr_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in heavyr_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = heavyr_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "heavyr_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "heavyr_video"

    filepath = os.path.join(OUTPUT_FOLDER, f"hr_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"hr_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, file_size = await download_heavyr_video(
            page_url=url,
            video_url=chosen.get("url", ""),
            filepath=filepath,
            progress_cb=progress_cb,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return

        ul_id = f"hr_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(file_size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[HR] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def heavyr_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("hr_cancel_", "")
    heavyr_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ====================== WONPORN HANDLER ======================


async def process_wonporn_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities, title, info = await extract_wonporn_qualities(
        url,
        progress_cb=progress_cb,
    )
    if not qualities:
        await safe_edit(status_msg, "❌ کیفیتی پیدا نشد.")
        return
    session_id = f"wp_{event.chat_id}_{event.id}_{int(time.time())}"
    wonporn_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
        "created_at": time.time(),
    }
    title_display = title[:60] if title else "ویدیو WonPorn"
    text = f"🎬 **{title_display}**\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"wp_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"wp_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def wonporn_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in wonporn_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = wonporn_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "wonporn_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "wonporn_video"

    filepath = os.path.join(OUTPUT_FOLDER, f"wp_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"wp_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, file_size = await download_wonporn_video(
            page_url=url,
            video_url=chosen.get("url", ""),
            filepath=filepath,
            progress_cb=progress_cb,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return

        ul_id = f"wp_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(file_size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[WP] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def wonporn_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("wp_cancel_", "")
    wonporn_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ====================== LEAKSEXTAPE HANDLER ======================


async def process_leaksextape_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities, title, info = await extract_leaksextape_qualities(
        url,
        progress_cb=progress_cb,
    )
    if not qualities:
        await safe_edit(status_msg, "❌ کیفیتی پیدا نشد.")
        return
    session_id = f"ls_{event.chat_id}_{event.id}_{int(time.time())}"
    leaksextape_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
        "created_at": time.time(),
    }
    title_display = title[:60] if title else "ویدیو LeaksExtape"
    text = f"🎬 **{title_display}**\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"ls_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"ls_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def leaksextape_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in leaksextape_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = leaksextape_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "leaksextape_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "leaksextape_video"

    filepath = os.path.join(OUTPUT_FOLDER, f"ls_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"ls_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, file_size = await download_leaksextape_video(
            page_url=url,
            video_url=chosen.get("url", ""),
            filepath=filepath,
            progress_cb=progress_cb,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return

        ul_id = f"ls_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(file_size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[LS] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def leaksextape_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("ls_cancel_", "")
    leaksextape_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ====================== XXXPUBLICPORNVIDEOS HANDLER ======================


async def process_xxxpublicpornvideos_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities, title, info = await extract_xxxpublicpornvideos_qualities(
        url,
        progress_cb=progress_cb,
    )
    if not qualities:
        await safe_edit(status_msg, "❌ کیفیتی پیدا نشد.")
        return
    session_id = f"xp_{event.chat_id}_{event.id}_{int(time.time())}"
    xxxpublicpornvideos_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
        "created_at": time.time(),
    }
    title_display = title[:60] if title else "ویدیو XXXPublicPornVideos"
    text = f"🎬 **{title_display}**\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"xp_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"xp_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def xxxpublicpornvideos_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in xxxpublicpornvideos_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = xxxpublicpornvideos_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "xxxpublicpornvideos_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "xxxpublicpornvideos_video"

    filepath = os.path.join(OUTPUT_FOLDER, f"xp_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"xp_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, file_size = await download_xxxpublicpornvideos_video(
            page_url=url,
            video_url=chosen.get("url", ""),
            filepath=filepath,
            progress_cb=progress_cb,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return

        ul_id = f"xp_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(file_size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[XP] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def xxxpublicpornvideos_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("xp_cancel_", "")
    xxxpublicpornvideos_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ====================== CARTOONPORN.COM HANDLER ======================


async def process_cartoonporncom_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities, title, info = await extract_cartoonporncom_qualities(
        url,
        progress_cb=progress_cb,
    )
    if not qualities:
        await safe_edit(status_msg, "❌ کیفیتی پیدا نشد.")
        return
    session_id = f"cc_{event.chat_id}_{event.id}_{int(time.time())}"
    cartoonporncom_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
        "created_at": time.time(),
    }
    title_display = title[:60] if title else "ویدیو CartoonPorn.com"
    text = f"🎬 **{title_display}**\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"cc_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"cc_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def cartoonporncom_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in cartoonporncom_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = cartoonporncom_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "cartoonporncom_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "cartoonporncom_video"

    filepath = os.path.join(OUTPUT_FOLDER, f"cc_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"cc_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, file_size = await download_cartoonporncom_video(
            page_url=url,
            video_url=chosen.get("url", ""),
            filepath=filepath,
            progress_cb=progress_cb,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return

        ul_id = f"cc_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(file_size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[CC] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def cartoonporncom_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("cc_cancel_", "")
    cartoonporncom_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ====================== HIHENTAIPORN HANDLER ======================


async def process_hihentaiporn_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities, title, info = await extract_hihentaiporn_qualities(
        url,
        progress_cb=progress_cb,
    )
    if not qualities:
        await safe_edit(status_msg, "❌ کیفیتی پیدا نشد.")
        return
    session_id = f"hh_{event.chat_id}_{event.id}_{int(time.time())}"
    hihentaiporn_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
        "created_at": time.time(),
    }
    title_display = title[:60] if title else "ویدیو HiHentaiPorn"
    text = f"🎬 **{title_display}**\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"hh_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"hh_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def hihentaiporn_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in hihentaiporn_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = hihentaiporn_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "hihentaiporn_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "hihentaiporn_video"

    filepath = os.path.join(OUTPUT_FOLDER, f"hh_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"hh_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, file_size = await download_hihentaiporn_video(
            page_url=url,
            video_url=chosen.get("url", ""),
            filepath=filepath,
            progress_cb=progress_cb,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return

        ul_id = f"hh_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(file_size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[HH] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def hihentaiporn_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("hh_cancel_", "")
    hihentaiporn_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ====================== FETISHSHRINE HANDLER ======================


async def process_fetishshrine_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities, title, info = await extract_fetishshrine_qualities(
        url,
        progress_cb=progress_cb,
    )
    if not qualities:
        await safe_edit(status_msg, "❌ کیفیتی پیدا نشد.")
        return
    session_id = f"fs_{event.chat_id}_{event.id}_{int(time.time())}"
    fetishshrine_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
        "created_at": time.time(),
    }
    title_display = title[:60] if title else "ویدیو FetishShrine"
    text = f"🎬 **{title_display}**\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"fs_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"fs_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def fetishshrine_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in fetishshrine_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = fetishshrine_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "fetishshrine_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "fetishshrine_video"

    filepath = os.path.join(OUTPUT_FOLDER, f"fs_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"fs_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, file_size = await download_fetishshrine_video(
            page_url=url,
            video_url=chosen.get("url", ""),
            filepath=filepath,
            progress_cb=progress_cb,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return

        ul_id = f"fs_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(file_size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[FS] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def fetishshrine_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("fs_cancel_", "")
    fetishshrine_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ====================== BIGFUCK HANDLER ======================


async def process_bigfuck_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities, title, info = await extract_bigfuck_qualities(
        url,
        progress_cb=progress_cb,
    )
    if not qualities:
        await safe_edit(status_msg, "❌ کیفیتی پیدا نشد.")
        return
    session_id = f"bf_{event.chat_id}_{event.id}_{int(time.time())}"
    bigfuck_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
        "created_at": time.time(),
    }
    title_display = title[:60] if title else "ویدیو BigFuck"
    text = f"🎬 **{title_display}**\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"bf_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"bf_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def bigfuck_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in bigfuck_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = bigfuck_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "bigfuck_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "bigfuck_video"

    filepath = os.path.join(OUTPUT_FOLDER, f"bf_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"bf_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, file_size = await download_bigfuck_video(
            page_url=url,
            video_url=chosen.get("url", ""),
            filepath=filepath,
            progress_cb=progress_cb,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return

        ul_id = f"bf_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(file_size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[BF] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def bigfuck_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("bf_cancel_", "")
    bigfuck_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ====================== BABESTUBE HANDLER ======================


async def process_babestube_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities, title, info = await extract_babestube_qualities(
        url,
        progress_cb=progress_cb,
    )
    if not qualities:
        await safe_edit(status_msg, "❌ کیفیتی پیدا نشد.")
        return
    session_id = f"bb_{event.chat_id}_{event.id}_{int(time.time())}"
    babestube_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
        "created_at": time.time(),
    }
    title_display = title[:60] if title else "ویدیو BabesTube"
    text = f"🎬 **{title_display}**\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"bb_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"bb_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def babestube_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in babestube_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = babestube_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "babestube_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "babestube_video"

    filepath = os.path.join(OUTPUT_FOLDER, f"bb_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"bb_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, file_size = await download_babestube_video(
            page_url=url,
            video_url=chosen.get("url", ""),
            filepath=filepath,
            progress_cb=progress_cb,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return

        ul_id = f"bb_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(file_size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[BB] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def babestube_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("bb_cancel_", "")
    babestube_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ====================== PORNHUB HANDLER ======================


async def process_pornwhite_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities, title, info = await extract_pornwhite_qualities(
        url,
        progress_cb=progress_cb,
    )
    if not qualities:
        await safe_edit(status_msg, "❌ کیفیتی پیدا نشد.")
        return
    session_id = f"pw_{event.chat_id}_{event.id}_{int(time.time())}"
    pornwhite_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
        "created_at": time.time(),
    }
    title_display = title[:60] if title else "ویدیو PornWhite"
    text = f"🎬 **{title_display}**\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"pw_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"pw_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def pornwhite_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in pornwhite_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = pornwhite_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "pornwhite_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "pornwhite_video"

    filepath = os.path.join(OUTPUT_FOLDER, f"pw_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"pw_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, file_size = await download_pornwhite_direct(
            url=url,
            filepath=filepath,
            progress_cb=progress_cb,
            video_url=chosen.get("url", ""),
            quality=chosen.get("label", "high"),
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return

        ul_id = f"pw_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(file_size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[PW] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def pornwhite_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("pw_cancel_", "")
    pornwhite_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ====================== PORNDROIDS HANDLER ======================


async def process_porndroids_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities, title, info = await extract_porndroids_qualities(
        url,
        progress_cb=progress_cb,
    )
    if not qualities:
        await safe_edit(status_msg, "❌ کیفیتی پیدا نشد.")
        return
    session_id = f"pd_{event.chat_id}_{event.id}_{int(time.time())}"
    porndroids_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
        "created_at": time.time(),
    }
    title_display = title[:60] if title else "ویدیو PornDroids"
    text = f"🎬 **{title_display}**\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"pd_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"pd_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def porndroids_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in porndroids_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = porndroids_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "porndroids_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "porndroids_video"

    filepath = os.path.join(OUTPUT_FOLDER, f"pd_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"pd_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, file_size = await download_porndroids_direct(
            url=url,
            filepath=filepath,
            progress_cb=progress_cb,
            video_url=chosen.get("url", ""),
            quality=chosen.get("label", "high"),
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return

        ul_id = f"pd_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(file_size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[PD] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def porndroids_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("pd_cancel_", "")
    porndroids_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ====================== HDTUBE HANDLER ======================


async def process_hdtube_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities, title, info = await extract_hdtube_qualities(
        url,
        progress_cb=progress_cb,
    )
    if not qualities:
        await safe_edit(status_msg, "❌ کیفیتی پیدا نشد.")
        return
    session_id = f"hd_{event.chat_id}_{event.id}_{int(time.time())}"
    hdtube_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
        "created_at": time.time(),
    }
    title_display = title[:60] if title else "ویدیو HDTube"
    text = f"🎬 **{title_display}**\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"hd_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"hd_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def hdtube_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in hdtube_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = hdtube_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "hdtube_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "hdtube_video"

    filepath = os.path.join(OUTPUT_FOLDER, f"hd_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"hd_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, file_size = await download_hdtube_direct(
            url=url,
            filepath=filepath,
            progress_cb=progress_cb,
            video_url=chosen.get("url", ""),
            quality=chosen.get("label", "high"),
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return

        ul_id = f"hd_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(file_size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[HD] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def hdtube_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("hd_cancel_", "")
    hdtube_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ====================== SLEAZYNEASY HANDLER ======================


async def process_sleazyneasy_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities, title, info = await extract_sleazyneasy_qualities(
        url,
        progress_cb=progress_cb,
    )
    if not qualities:
        await safe_edit(status_msg, "❌ کیفیتی پیدا نشد.")
        return
    session_id = f"sn_{event.chat_id}_{event.id}_{int(time.time())}"
    sleazyneasy_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
        "created_at": time.time(),
    }
    title_display = title[:60] if title else "ویدیو SleazyNeasy"
    text = f"🎬 **{title_display}**\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"sn_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"sn_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def sleazyneasy_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in sleazyneasy_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = sleazyneasy_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "sleazyneasy_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "sleazyneasy_video"

    filepath = os.path.join(OUTPUT_FOLDER, f"sn_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"sn_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, file_size = await download_sleazyneasy_direct(
            url=url,
            filepath=filepath,
            progress_cb=progress_cb,
            video_url=chosen.get("url", ""),
            quality=chosen.get("label", "high"),
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return

        ul_id = f"sn_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(file_size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[SN] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def sleazyneasy_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("sn_cancel_", "")
    sleazyneasy_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ====================== SHAMELESS HANDLER ======================


async def process_shameless_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities, title, info = await extract_shameless_qualities(
        url,
        progress_cb=progress_cb,
    )
    if not qualities:
        await safe_edit(status_msg, "❌ کیفیتی پیدا نشد.")
        return
    session_id = f"sl_{event.chat_id}_{event.id}_{int(time.time())}"
    shameless_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
        "created_at": time.time(),
    }
    title_display = title[:60] if title else "ویدیو Shameless"
    text = f"🎬 **{title_display}**\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"sl_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"sl_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def shameless_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in shameless_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = shameless_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "shameless_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "shameless_video"

    filepath = os.path.join(OUTPUT_FOLDER, f"sl_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"sl_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, file_size = await download_shameless_direct(
            url=url,
            filepath=filepath,
            progress_cb=progress_cb,
            video_url=chosen.get("url", ""),
            quality=chosen.get("label", "high"),
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return

        ul_id = f"sl_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(file_size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[SL] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def shameless_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("sl_cancel_", "")
    shameless_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ====================== HQPORNER HANDLER ======================


async def process_hqporner_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities, title, info = await extract_hqporner_qualities(
        url,
        progress_cb=progress_cb,
    )
    if not qualities:
        await safe_edit(status_msg, "❌ کیفیتی پیدا نشد.")
        return
    session_id = f"hq_{event.chat_id}_{event.id}_{int(time.time())}"
    hqporner_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
        "created_at": time.time(),
    }
    title_display = title[:60] if title else "ویدیو HQPerner"
    text = f"🎬 **{title_display}**\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"hq_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"hq_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def hqporner_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in hqporner_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = hqporner_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "hqporner_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "hqporner_video"

    filepath = os.path.join(OUTPUT_FOLDER, f"hq_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"hq_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, file_size = await download_hqporner_direct(
            url=url,
            filepath=filepath,
            progress_cb=progress_cb,
            video_url=chosen.get("url", ""),
            quality=chosen.get("label", "high"),
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return

        ul_id = f"hq_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(file_size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[HQ] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def hqporner_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("hq_cancel_", "")
    hqporner_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ====================== YOUJIZZ HANDLER ======================


async def process_youjizz_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities, title, info = await extract_youjizz_qualities(
        url,
        progress_cb=progress_cb,
    )
    if not qualities:
        await safe_edit(status_msg, "❌ کیفیتی پیدا نشد.")
        return
    session_id = f"yj_{event.chat_id}_{event.id}_{int(time.time())}"
    youjizz_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
        "created_at": time.time(),
    }
    title_display = title[:60] if title else "ویدیو YouJizz"
    text = f"🎬 **{title_display}**\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"yj_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"yj_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def youjizz_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in youjizz_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = youjizz_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "youjizz_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "youjizz_video"

    filepath = os.path.join(OUTPUT_FOLDER, f"yj_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"yj_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, file_size = await download_youjizz_direct(
            url=url,
            filepath=filepath,
            progress_cb=progress_cb,
            video_url=chosen.get("url", ""),
            quality=chosen.get("label", "high"),
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return

        ul_id = f"yj_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(file_size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[YJ] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def youjizz_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("yj_cancel_", "")
    youjizz_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ====================== SEVEREPORN HANDLER ======================


async def process_severeporn_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities, title, info = await extract_severeporn_qualities(
        url,
        progress_cb=progress_cb,
    )
    if not qualities:
        await safe_edit(status_msg, "❌ کیفیتی پیدا نشد.")
        return
    session_id = f"sp_{event.chat_id}_{event.id}_{int(time.time())}"
    severeporn_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
        "created_at": time.time(),
    }
    title_display = title[:60] if title else "ویدیو SeverePorn"
    text = f"🎬 **{title_display}**\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"sp_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"sp_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def severeporn_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in severeporn_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = severeporn_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "severeporn_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "severeporn_video"

    filepath = os.path.join(OUTPUT_FOLDER, f"sp_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"sp_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, file_size = await download_severeporn_direct(
            url=url,
            filepath=filepath,
            progress_cb=progress_cb,
            video_url=chosen.get("url", ""),
            quality=chosen.get("label", "high"),
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return

        ul_id = f"sp_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(file_size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[SP] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def severeporn_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("sp_cancel_", "")
    severeporn_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ====================== MAT6TUBE HANDLER ======================


async def process_mat6tube_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities, title, info = await extract_mat6tube_qualities(
        url,
        progress_cb=progress_cb,
    )
    if not qualities:
        await safe_edit(status_msg, "❌ کیفیتی پیدا نشد.")
        return
    session_id = f"m6_{event.chat_id}_{event.id}_{int(time.time())}"
    mat6tube_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
        "created_at": time.time(),
    }
    title_display = title[:60] if title else "ویدیو Mat6Tube"
    text = f"🎬 **{title_display}**\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"m6_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"m6_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def mat6tube_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in mat6tube_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = mat6tube_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "mat6tube_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "mat6tube_video"

    filepath = os.path.join(OUTPUT_FOLDER, f"m6_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"m6_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, file_size = await download_mat6tube_direct(
            url=url,
            filepath=filepath,
            progress_cb=progress_cb,
            video_url=chosen.get("url", ""),
            quality=chosen.get("label", "high"),
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return

        ul_id = f"m6_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(file_size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[M6] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def mat6tube_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("m6_cancel_", "")
    mat6tube_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ====================== PEEKVIDS HANDLER ======================


async def process_peekvids_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities, title, info = await extract_peekvids_qualities(
        url,
        progress_cb=progress_cb,
    )
    if not qualities:
        await safe_edit(status_msg, "❌ کیفیتی پیدا نشد.")
        return
    session_id = f"pv_{event.chat_id}_{event.id}_{int(time.time())}"
    peekvids_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
        "created_at": time.time(),
    }
    title_display = title[:60] if title else "ویدیو PeekVids"
    text = f"🎬 **{title_display}**\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"pv_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"pv_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def peekvids_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in peekvids_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = peekvids_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "peekvids_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "peekvids_video"

    filepath = os.path.join(OUTPUT_FOLDER, f"pv_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"pv_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, file_size = await download_peekvids_direct(
            url=url,
            filepath=filepath,
            progress_cb=progress_cb,
            video_url=chosen.get("url", ""),
            quality=chosen.get("label", "high"),
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return

        ul_id = f"pv_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(file_size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[PV] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def peekvids_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("pv_cancel_", "")
    peekvids_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ====================== PARADISEHILL HANDLER ======================


async def process_paradisehill_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities, title, info = await extract_paradisehill_qualities(
        url,
        progress_cb=progress_cb,
    )
    if not qualities:
        await safe_edit(status_msg, "❌ کیفیتی پیدا نشد.")
        return
    session_id = f"ph_{event.chat_id}_{event.id}_{int(time.time())}"
    paradisehill_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
        "created_at": time.time(),
    }
    title_display = title[:60] if title else "ویدیو ParadiseHill"
    text = f"🎬 **{title_display}**\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"ph_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"ph_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def paradisehill_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in paradisehill_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = paradisehill_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "paradisehill_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "paradisehill_video"

    filepath = os.path.join(OUTPUT_FOLDER, f"ph_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"ph_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, file_size = await download_paradisehill_direct(
            url=url,
            filepath=filepath,
            progress_cb=progress_cb,
            video_url=chosen.get("url", ""),
            quality=chosen.get("label", "high"),
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return

        ul_id = f"ph_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(file_size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[PH] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def paradisehill_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("ph_cancel_", "")
    paradisehill_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ====================== SXYPRN HANDLER ======================


async def process_sxyprn_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities, title, info = await extract_sxyprn_qualities(
        url,
        progress_cb=progress_cb,
    )
    if not qualities:
        await safe_edit(status_msg, "❌ کیفیتی پیدا نشد.")
        return
    session_id = f"sx_{event.chat_id}_{event.id}_{int(time.time())}"
    sxyprn_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
        "created_at": time.time(),
    }
    title_display = title[:60] if title else "ویدیو SxyPrn"
    text = f"🎬 **{title_display}**\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"sx_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"sx_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def sxyprn_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in sxyprn_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = sxyprn_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "sxyprn_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "sxyprn_video"

    filepath = os.path.join(OUTPUT_FOLDER, f"sx_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"sx_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, file_size = await download_sxyprn_direct(
            url=url,
            filepath=filepath,
            progress_cb=progress_cb,
            video_url=chosen.get("url", ""),
            quality=chosen.get("label", "high"),
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return

        ul_id = f"sx_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(file_size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[SX] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def sxyprn_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("sx_cancel_", "")
    sxyprn_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ====================== KICK HANDLER ======================


async def process_kick_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities = await get_available_qualities(url)
    if not qualities:
        await safe_edit(status_msg, "❌ کیفیتی پیدا نشد. (لایو آنلاین نیست؟)")
        return
    session_id = f"kc_{event.chat_id}_{event.id}_{int(time.time())}"
    kick_sessions[session_id] = {
        "url": url,
        "qualities": qualities,
        "chat_id": event.chat_id,
        "created_at": time.time(),
    }
    text = f"🎬 **Kick Live**\n\n🎚 کیفیت مورد نظر رو انتخاب کن (دانلود از ابتدای لایو):"
    buttons = []
    for i, q in enumerate(qualities):
        label = f"{q.get('height', '?')}p ({q.get('resolution', '?')})"
        buttons.append([Button.inline(label, f"kc_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"kc_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def kick_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in kick_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = kick_sessions.pop(session_id)
    qualities = entry["qualities"]
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    quality_str = f"{chosen.get('height', '720')}p"
    await event.answer(f"✅ {quality_str}", alert=False)

    output_dir = os.path.join(OUTPUT_FOLDER, f"kick_{int(time.time())}")
    os.makedirs(output_dir, exist_ok=True)

    dl_id = f"kc_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود لایو Kick...**\n🎚 {quality_str}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        result = await download_past(
            url=url,
            output_dir=output_dir,
            quality=quality_str,
            progress_cb=progress_cb,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not result.get("success"):
            err_msg = result.get("error", "Unknown error")
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return

        parts = result.get("parts", [])
        total_size = result.get("total_size", 0)

        if not parts:
            await safe_edit(status_msg, "❌ هیچ پخشی دانلود نشد.")
            return

        await safe_edit(status_msg, f"📤 **در حال آپلود {len(parts)} پارت...**")
        for i, part in enumerate(parts):
            filepath = part["filepath"]
            if not os.path.exists(filepath):
                continue
            ul_id = f"kc_ul_{event.chat_id}_{event.id}_{i}_{int(time.time())}"
            caption = f"🎬 **Kick Live {quality_str}**\n📦 پارت {i+1}/{len(parts)}\n💾 {part.get('size', 0) / 1024 / 1024:.0f} MB"
            await send_file_with_progress(
                client=event.client,
                chat_id=entry["chat_id"],
                filepath=filepath,
                caption=caption,
                status_msg=status_msg,
                buttons=None,
                supports_streaming=True,
                ul_id=ul_id,
            )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[KC] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if os.path.exists(output_dir):
                for f in os.listdir(output_dir):
                    fp = os.path.join(output_dir, f)
                    try:
                        os.remove(fp)
                    except Exception:
                        pass
                os.rmdir(output_dir)
        except Exception:
            pass


async def kick_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("kc_cancel_", "")
    kick_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ====================== LUXURETV HANDLER ======================


async def process_luxuretv_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities, title, info = await extract_luxuretv_qualities(
        url,
        progress_cb=progress_cb,
    )
    if not qualities:
        await safe_edit(status_msg, "❌ کیفیتی پیدا نشد.")
        return
    session_id = f"lx_{event.chat_id}_{event.id}_{int(time.time())}"
    luxuretv_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
        "created_at": time.time(),
    }
    title_display = title[:60] if title else "ویدیو LuxureTV"
    text = f"🎬 **{title_display}**\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"lx_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"lx_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def luxuretv_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in luxuretv_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = luxuretv_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "luxuretv_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "luxuretv_video"

    filepath = os.path.join(OUTPUT_FOLDER, f"lx_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"lx_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, file_size = await download_luxuretv_direct(
            url=url,
            filepath=filepath,
            progress_cb=progress_cb,
            video_url=chosen.get("url", ""),
            quality=chosen.get("label", "high"),
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return

        ul_id = f"lx_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(file_size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[LX] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def luxuretv_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("lx_cancel_", "")
    luxuretv_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


xanimu_sessions: Dict[str, dict] = {}
porntrex_sessions: Dict[str, dict] = {}
heavyr_sessions: Dict[str, dict] = {}
wonporn_sessions: Dict[str, dict] = {}
leaksextape_sessions: Dict[str, dict] = {}
xxxpublicpornvideos_sessions: Dict[str, dict] = {}
cartoonporncom_sessions: Dict[str, dict] = {}
hihentaiporn_sessions: Dict[str, dict] = {}
fetishshrine_sessions: Dict[str, dict] = {}
bigfuck_sessions: Dict[str, dict] = {}
babestube_sessions: Dict[str, dict] = {}
pornwhite_sessions: Dict[str, dict] = {}
porndroids_sessions: Dict[str, dict] = {}
hdtube_sessions: Dict[str, dict] = {}
sleazyneasy_sessions: Dict[str, dict] = {}
shameless_sessions: Dict[str, dict] = {}
hqporner_sessions: Dict[str, dict] = {}
youjizz_sessions: Dict[str, dict] = {}
severeporn_sessions: Dict[str, dict] = {}
mat6tube_sessions: Dict[str, dict] = {}
peekvids_sessions: Dict[str, dict] = {}
paradisehill_sessions: Dict[str, dict] = {}
sxyprn_sessions: Dict[str, dict] = {}
kick_sessions: Dict[str, dict] = {}
luxuretv_sessions: Dict[str, dict] = {}


# ====================== INLINE SEARCH ======================


INLINE_CACHE_TIME = 60
INLINE_RESULTS_LIMIT = 50
INLINE_PICK_TTL = 30 * 60  # توکن‌های دکمه شروع دانلود ۳۰ دقیقه معتبرند
inline_pick_urls: dict = {}


def _store_inline_pick(url: str) -> str:
    """ذخیره URL نتیجه اینلاین با یک توکن کوتاه برای دکمه شروع دانلود."""
    now = time.time()
    expired = [k for k, v in inline_pick_urls.items() if now - v.get("ts", 0) > INLINE_PICK_TTL]
    for k in expired:
        inline_pick_urls.pop(k, None)
    token = uuid.uuid4().hex[:12]
    inline_pick_urls[token] = {"url": url, "ts": now}
    return token


PH_SORT_MAP = {"new": "mr", "month": "mr", "top": "tr", "rating": "tr", "long": "lg", "length": "lg", "best": "tr", "views": "mv", "most": "mv"}

# ── Default search engine selection ──

async def setsearch_cmd(event):
    if event.sender_id not in AUTHORIZED_USERS:
        return await event.reply("⛔ Unauthorized")
    current = get_user_default_search(event.sender_id)
    labels = {"ph": "PornHub", "xv": "XVideos", "ep": "Eporner", "xn": "XNXX", "imd": "IMDB", "wh": "WhoresHub"}
    buttons = [
        [Button.inline(f"{'✅ ' if current == k else ''}{v}", f"setsearch_{k}") for k, v in labels.items()]
    ]
    await event.reply(
        f"🔍 **Default Search Engine**\n\nCurrent: **{labels.get(current, 'PornHub')}**\n\nChoose your default search engine for inline queries without prefix:",
        parse_mode="markdown",
        buttons=buttons,
    )

async def setsearch_callback(event):
    user_id = event.sender_id
    if user_id not in AUTHORIZED_USERS:
        await event.answer("⛔ Unauthorized", alert=True)
        return
    data = event.data.decode()
    if not data.startswith("setsearch_"):
        return
    src = data.replace("setsearch_", "")
    if src not in ("ph", "xv", "ep", "xn", "imd", "wh"):
        return
    set_user_default_search(user_id, src)
    labels = {"ph": "PornHub", "xv": "XVideos", "ep": "Eporner", "xn": "XNXX", "imd": "IMDB", "wh": "WhoresHub"}
    buttons = [
        [Button.inline(f"{'✅ ' if src == k else ''}{v}", f"setsearch_{k}") for k, v in labels.items()]
    ]
    await event.edit(
        f"🔍 **Default Search Engine**\n\n✅ Changed to **{labels[src]}**\n\nNow when you type `@telformatbot hardcore` without prefix, it will search **{labels[src]}**:",
        parse_mode="markdown",
        buttons=buttons,
    )
    await event.answer(f"Default search changed to {labels[src]}", alert=True)


# ====================== IMDB (vidsrc) SEARCH & DOWNLOAD ======================

imdb_states: Dict[int, dict] = {}
IMDB_OUTPUT_FOLDER = os.path.join(OUTPUT_FOLDER, "imdb")


def _imdb_format_caption(info: dict, eps=None) -> str:
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


def _imdb_dl_caption(title: str, season, episode, subtitle_name, file_size_mb: float) -> str:
    lines = []
    if season and episode:
        lines.append(f"🎬 **{title}** - S{season:02d}E{episode:02d}")
    else:
        lines.append(f"🎬 **{title}**")
    if subtitle_name:
        lines.append(f"📝 زیرنویس هاردکد: `{subtitle_name}`")
    lines.append(f"💾 حجم: {file_size_mb:.1f} MB")
    return "\n".join(lines)


def _imdb_seasons_buttons(eps) -> list:
    buttons = []
    row = []
    for s in sorted(eps["seasons"].keys(), key=lambda x: -x):
        ep_count = len(eps["seasons"][s])
        row.append(Button.inline(f"📂 فصل {s} · {ep_count} قسمت", f"imd_season_{s}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([Button.inline("🚫 بستن", "imd_close")])
    return buttons


def _imdb_quality_buttons(qualities: list, is_episode: bool) -> list:
    buttons = []
    row = []
    for q in qualities:
        label = q["label"]
        if q.get("resolution"):
            label += f" ({q['resolution']})"
        row.append(Button.inline(label, f"imd_eq_{q['label']}" if is_episode else f"imd_q_{q['label']}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([Button.inline("⏭ بدون زیرنویس", "imd_enosub" if is_episode else "imd_nosub")])
    buttons.append([Button.inline("🚫 بستن", "imd_close")])
    return buttons


def _imdb_sub_buttons(subs: list, is_episode: bool) -> list:
    """دکمه‌های انتخاب زیرنویس — لیست زیرنویس‌های موجود + بدون زیرنویس"""
    buttons = []
    if subs:
        for i, s in enumerate(subs[:5]):
            label = f"📄 {s['file_name'][:40]} (↓{s['downloads']})"
            buttons.append([Button.inline(label, f"imd_esub_{i}" if is_episode else f"imd_sub_{i}")])
    # اگه زیرنویس از OpenSubtitles پیدا نشد، گزینه imdbplay رو نشون بده
    if not subs:
        buttons.append([Button.inline("🎬 با زیرنویس imdbplay (softsub)", "imd_withsub")])
    buttons.append([Button.inline("⏭ بدون زیرنویس", "imd_enosub" if is_episode else "imd_nosub")])
    buttons.append([Button.inline("🚫 بستن", "imd_close")])
    return buttons


def _imdb_delivery_buttons(is_episode: bool) -> list:
    """دکمه‌های انتخاب نحوه ارسال زیرنویس — بعد از انتخاب زیرنویس"""
    buttons = []
    buttons.append([Button.inline("🎬 softsub (تو ویدیو)", "imd_softsub")])
    buttons.append([Button.inline("📄 فایل جداگانه", "imd_sepsub")])
    buttons.append([Button.inline("🚫 بستن", "imd_close")])
    return buttons


async def imdb_cb_title(event):
    data = event.data.decode()
    imdb_id = data.replace("imd_sel_", "")
    user_id = event.sender_id

    await event.edit("⏳ در حال دریافت اطلاعات...", buttons=None)

    info = await get_title_info(imdb_id)
    if not info:
        await event.edit("❌ دریافت اطلاعات ناموفق بود.")
        return

    is_series = info.get("is_series", False)
    caption = _imdb_format_caption(info)

    if is_series:
        eps = await get_tv_episodes(imdb_id)
        if not eps or not eps.get("seasons"):
            await event.edit("❌ اطلاعات فصل/قسمت در دسترس نیست.")
            return
        imdb_states[user_id] = {"imdb_id": imdb_id, "info": info, "eps": eps}
        buttons = _imdb_seasons_buttons(eps)
        cover = info.get("cover")
        if cover:
            try:
                await event.delete()
                await event.respond(cover, text=caption, parse_mode="md", buttons=buttons)
                return
            except Exception:
                pass
        await event.edit(caption, buttons=buttons, parse_mode="md")
    else:
        imdb_states[user_id] = {"imdb_id": imdb_id, "info": info}
        await event.edit(f"{caption}\n\n⏳ در حال گرفتن لیست کیفیت‌ها...", parse_mode="md")
        qualities = await get_qualities(imdb_id)
        if not qualities:
            await event.edit(f"{caption}\n\n❌ کیفیت‌ها در دسترس نیست.", parse_mode="md")
            return
        imdb_states[user_id]["qualities"] = qualities
        q_buttons = _imdb_quality_buttons(qualities, is_episode=False)
        cover = info.get("cover")
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


async def imdb_cb_season(event):
    user_id = event.sender_id
    state = imdb_states.get(user_id)
    if not state:
        await event.answer("⏰ نشست شما منقضی شده.\n🔄 لطفاً دوباره سرچ کنید.", alert=True)
        return
    data = event.data.decode()
    season = int(data.replace("imd_season_", ""))
    eps = state["eps"]
    if season not in eps["seasons"]:
        await event.answer("فصل نامعتبر", alert=True)
        return
    state["selected_season"] = season
    episodes = sorted(eps["seasons"][season])
    buttons = []
    row = []
    for ep in episodes:
        row.append(Button.inline(f"🎬 {ep}", f"imd_ep_{season}_{ep}"))
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([Button.inline("⬅️ برگشت", "imd_back")])
    buttons.append([Button.inline("🚫 بستن", "imd_close")])
    await event.edit(f"📺 فصل {season} — یکی از قسمت‌ها رو انتخاب کن:", buttons=buttons)


async def imdb_cb_back(event):
    user_id = event.sender_id
    state = imdb_states.get(user_id)
    if not state:
        await event.answer("وضعیت شما منقضی شده.", alert=True)
        return
    await event.edit("📺 یکی از فصل‌ها رو انتخاب کن:", buttons=_imdb_seasons_buttons(state["eps"]))


async def imdb_cb_close(event):
    user_id = event.sender_id
    imdb_states.pop(user_id, None)
    try:
        await event.delete()
    except Exception:
        await event.edit("✖ بسته شد", buttons=None)


async def imdb_cb_episode(event):
    user_id = event.sender_id
    state = imdb_states.get(user_id)
    if not state:
        await event.answer("وضعیت شما منقضی شده.", alert=True)
        return
    data = event.data.decode()
    parts = data.split("_")
    season = int(parts[2])
    episode = int(parts[3])
    imdb_id = state["imdb_id"]
    title = state["info"].get("title", "Unknown")
    state["selected_season"] = season
    state["selected_episode"] = episode
    await event.edit(
        f"🎬 **{title}** - S{season:02d}E{episode:02d}\n\n⏳ در حال گرفتن لیست کیفیت‌ها...",
        parse_mode="md",
    )
    qualities = await get_qualities(imdb_id, season, episode)
    if not qualities:
        await event.edit("❌ کیفیت‌ها در دسترس نیست.")
        return
    state["qualities"] = qualities
    await event.edit(
        f"🎬 **{title}** - S{season:02d}E{episode:02d}\n\n🎯 کیفیت رو انتخاب کن:",
        buttons=_imdb_quality_buttons(qualities, is_episode=True),
        parse_mode="md",
    )


async def imdb_cb_quality(event):
    data = event.data.decode()
    quality_label = data.replace("imd_q_", "")
    user_id = event.sender_id
    state = imdb_states.get(user_id)
    if not state:
        await event.answer("⏰ نشست شما منقضی شده.\n🔄 لطفاً دوباره سرچ کنید.", alert=True)
        return
    state["quality"] = quality_label
    await event.edit(f"✅ کیفیت: **{quality_label}**\n\n🔍 در حال جستجوی زیرنویس فارسی...")
    imdb_id = state["imdb_id"]
    # Search Persian subtitles from OpenSubtitles
    subs = await search_subtitles(imdb_id, "per")
    state["subs"] = subs
    sub_count_text = f"📄 {len(subs)} زیرنویس پیدا شد:" if subs else "❌ زیرنویسی پیدا نشد"
    await event.edit(
        f"✅ کیفیت: **{quality_label}**\n\n📝 زیرنویس فارسی:\n{sub_count_text}",
        buttons=_imdb_sub_buttons(subs, is_episode=False),
        parse_mode="md",
    )


async def _check_persian_subtitle_available(imdb_id, season=None, episode=None):
    """بررسی موجود بودن زیرنویس فارسی بدون دانلود."""
    try:
        from searcher.imdb.imdbplay_downloader import _get_tmdb_id, get_persian_subtitle
        tmdb_id = await _get_tmdb_id(imdb_id)
        if not tmdb_id:
            return {"available": False}
        # Try to get subtitle (just check if it exists)
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            sub_path = await get_persian_subtitle(imdb_id, tmdb_id=tmdb_id, season=season, episode=episode, out_dir=tmpdir)
            if sub_path:
                # Find which source it came from
                source = "imdbplay"
                if "vidzee" in sub_path.lower():
                    source = "Vidzee"
                elif "vdrk" in sub_path.lower():
                    source = "2Embed"
                elif "videasy" in sub_path.lower():
                    source = "Videasy"
                return {"available": True, "source": source}
        return {"available": False}
    except Exception:
        return {"available": False}


async def imdb_cb_equality(event):
    data = event.data.decode()
    quality_label = data.replace("imd_eq_", "")
    user_id = event.sender_id
    state = imdb_states.get(user_id)
    if not state:
        await event.answer("⏰ نشست شما منقضی شده.\n🔄 لطفاً دوباره سرچ کنید.", alert=True)
        return
    state["quality"] = quality_label
    season = state.get("selected_season")
    episode = state.get("selected_episode")
    await event.edit(f"✅ کیفیت: **{quality_label}**\n\n🔍 در حال جستجوی زیرنویس فارسی...")
    imdb_id = state["imdb_id"]
    # Search Persian subtitles from OpenSubtitles
    subs = await search_subtitles(imdb_id, "per", season, episode)
    state["subs"] = subs
    sub_count_text = f"📄 {len(subs)} زیرنویس پیدا شد:" if subs else "❌ زیرنویسی پیدا نشد"
    await event.edit(
        f"✅ کیفیت: **{quality_label}**\n\n📝 زیرنویس فارسی:\n{sub_count_text}",
        buttons=_imdb_sub_buttons(subs, is_episode=True),
        parse_mode="md",
    )


async def imdb_cb_sub(event):
    """Handler for subtitle selection — show delivery options after selecting a subtitle"""
    data = event.data.decode()
    user_id = event.sender_id
    state = imdb_states.get(user_id)
    if not state:
        await event.answer("⏰ نشست شما منقضی شده.\n🔄 لطفاً دوباره سرچ کنید.", alert=True)
        return
    if data == "imd_withsub":
        # imdbplay softsub — download Persian sub from imdbplay and embed
        state["selected_sub"] = None
        state["use_imdbplay_sub"] = True
        await event.answer("✅ شروع دانلود با softsub...", alert=False)
        asyncio.create_task(_imdb_download_task(event, user_id, with_subtitle=True))
    elif data == "imd_softsub":
        # User chose softsub delivery for previously selected subtitle
        await event.answer("✅ شروع دانلود با softsub...", alert=False)
        asyncio.create_task(_imdb_download_task(event, user_id, with_subtitle=True, softsub=True))
    elif data == "imd_sepsub":
        # User chose separate file delivery for previously selected subtitle
        await event.answer("✅ شروع دانلود...", alert=False)
        asyncio.create_task(_imdb_download_task(event, user_id, with_subtitle=True, softsub=False))
    else:
        # Specific subtitle from OpenSubtitles — show delivery options
        sub_idx = int(data.replace("imd_sub_", ""))
        if sub_idx >= len(state.get("subs", [])):
            await event.answer("زیرنویس نامعتبر", alert=True)
            return
        state["selected_sub"] = state["subs"][sub_idx]
        state["use_imdbplay_sub"] = False
        sub_name = state["subs"][sub_idx].get("file_name", "")[:40]
        await event.edit(
            f"✅ زیرنویس انتخاب شد: **{sub_name}**\n\n📄 نحوه ارسال رو انتخاب کن:",
            buttons=_imdb_delivery_buttons(is_episode=False),
            parse_mode="md",
        )


async def imdb_cb_esub(event):
    """Handler for subtitle selection (episode) — show delivery options after selecting"""
    data = event.data.decode()
    user_id = event.sender_id
    state = imdb_states.get(user_id)
    if not state:
        await event.answer("⏰ نشست شما منقضی شده.\n🔄 لطفاً دوباره سرچ کنید.", alert=True)
        return
    if data == "imd_withsub":
        state["selected_sub"] = None
        state["use_imdbplay_sub"] = True
        await event.answer("✅ شروع دانلود با softsub...", alert=False)
        asyncio.create_task(_imdb_download_task(event, user_id, with_subtitle=True))
    elif data == "imd_softsub":
        await event.answer("✅ شروع دانلود با softsub...", alert=False)
        asyncio.create_task(_imdb_download_task(event, user_id, with_subtitle=True, softsub=True))
    elif data == "imd_sepsub":
        await event.answer("✅ شروع دانلود...", alert=False)
        asyncio.create_task(_imdb_download_task(event, user_id, with_subtitle=True, softsub=False))
    else:
        sub_idx = int(data.replace("imd_esub_", ""))
        if sub_idx >= len(state.get("subs", [])):
            await event.answer("زیرنویس نامعتبر", alert=True)
            return
        state["selected_sub"] = state["subs"][sub_idx]
        state["use_imdbplay_sub"] = False
        sub_name = state["subs"][sub_idx].get("file_name", "")[:40]
        await event.edit(
            f"✅ زیرنویس انتخاب شد: **{sub_name}**\n\n📄 نحوه ارسال رو انتخاب کن:",
            buttons=_imdb_delivery_buttons(is_episode=True),
            parse_mode="md",
        )


async def imdb_cb_nosub(event):
    user_id = event.sender_id
    state = imdb_states.get(user_id)
    if not state:
        await event.answer("وضعیت شما منقضی شده.", alert=True)
        return
    await event.answer("⏭ بدون زیرنویس", alert=False)
    asyncio.create_task(_imdb_download_task(event, user_id, with_subtitle=False))


async def imdb_cb_enosub(event):
    user_id = event.sender_id
    state = imdb_states.get(user_id)
    if not state:
        await event.answer("وضعیت شما منقضی شده.", alert=True)
        return
    await event.answer("⏭ بدون زیرنویس", alert=False)
    asyncio.create_task(_imdb_download_task(event, user_id, with_subtitle=False))


async def _imdb_download_cover(url: str, out_dir: str) -> Optional[str]:
    try:
        async with aiohttp.ClientSession(timeout=ClientTimeout(total=20)) as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    path = os.path.join(out_dir, "cover.jpg")
                    with open(path, "wb") as f:
                        f.write(await resp.read())
                    return path
    except Exception as e:
        logger.warning(f"[IMDB] cover download failed: {e}")
    return None


async def _imdb_download_task(event, user_id: int, with_subtitle: bool, softsub: bool = None):
    state = imdb_states.get(user_id)
    if not state:
        await event.answer("وضعیت شما منقضی شده.", alert=True)
        return

    imdb_id = state["imdb_id"]
    info = state["info"]
    title = info.get("title", "Unknown")
    quality = state.get("quality", "Auto")
    season = state.get("selected_season")
    episode = state.get("selected_episode")

    out_dir = os.path.join(IMDB_OUTPUT_FOLDER, f"{user_id}_{int(time.time())}")
    os.makedirs(out_dir, exist_ok=True)

    dl_id = f"imd_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    video_path = None
    sub_path = None
    final_path = None
    updater = None
    cover_path = None
    status_msg = None

    def check_cancel():
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError()

    try:
        try:
            status_msg = await event.client.send_message(event.chat_id, "📊 آماده‌سازی...")
        except Exception:
            try:
                status_msg = await event.edit("📊 آماده‌سازی...")
            except Exception:
                status_msg = None

        label = f"S{season:02d}E{episode:02d}" if season and episode else ""
        # Get server info before download to show in status
        server_info_text = ""
        server_info = None
        try:
            server_info = await get_server_info(imdb_id, season, episode)
            if server_info:
                srv_name = server_info.get("server", "Unknown")
                srv_method = server_info.get("method", "Unknown")
                srv_type = server_info.get("stream_type", "unknown").upper()
                server_info_text = f"\n🖥 سرور: {srv_name} | 🔧 متد: {srv_method} | 📡 نوع: {srv_type}"
        except Exception:
            pass
        await status_msg.edit(f"📥 دانلود {label} با کیفیت {quality}{server_info_text}", buttons=cancel_btn)

        sub_name = None
        use_imdbplay_sub = state.get("use_imdbplay_sub", False)
        if with_subtitle and state.get("selected_sub") and not use_imdbplay_sub:
            # User selected a specific subtitle from OpenSubtitles
            await status_msg.edit("📝 دانلود زیرنویس از OpenSubtitles...")
            sub_path = await download_subtitle(state["selected_sub"], out_dir)
            if sub_path:
                sub_name = state["selected_sub"].get("file_name", "")
                await status_msg.edit(f"✅ زیرنویس: `{sub_name}`", parse_mode="md")
            else:
                await status_msg.edit("⚠ زیرنویس دانلود نشد، ادامه بدون زیرنویس.")
                with_subtitle = False

        last_progress = [0]

        def vid_progress(done, total):
            check_cancel()
            last_progress[0] = (done, total)

        async def update_vid():
            while True:
                await asyncio.sleep(5)
                check_cancel()
                if last_progress[0]:
                    d, t = last_progress[0]
                    pct = d * 100 // t if t else 0
                    try:
                        srv_short = server_info.get("server", "") if server_info else ""
                        srv_text = f" [{srv_short}]" if srv_short else ""
                        await status_msg.edit(f"📥 دانلود سگمنت: {d}/{t} ({pct}%){srv_text}", buttons=cancel_btn)
                    except Exception:
                        pass

        updater = asyncio.create_task(update_vid())

        try:
            video_path = await download_with_quality(
                imdb_id, quality, out_dir, season, episode, progress_cb=vid_progress
            )
        except Exception as dl_err:
            logger.error(f"[IMDB] video download error: {dl_err}", exc_info=True)
            ep_ctx = f" S{season}E{episode}" if season and episode else ""
            await status_msg.edit(
                f"❌ دانلود ویدیو ناموفق بود ({imdb_id}{ep_ctx} | {quality}):\n\n"
                f"<code>{str(dl_err)[:1500]}</code>",
                buttons=None,
                parse_mode="html",
            )
            return
        finally:
            if updater:
                updater.cancel()
                updater = None

        if not video_path or not os.path.exists(video_path):
            await status_msg.edit(
                f"❌ دانلود ویدیو ناموفق بود.\n\n"
                f"DEBUG: video_path={video_path!r} — این معمولاً یعنی ربات با کد قدیمی در حال اجراست؛ "
                f"ری‌استارتش کنید.",
                buttons=None,
            )
            return

        vid_size = os.path.getsize(video_path) / 1024 / 1024
        await status_msg.edit(f"✅ ویدیو دانلود شد ({vid_size:.1f} MB)")

        final_path = video_path
        persian_sub_path = None

        # Determine if we should do softsub or separate subtitle file
        # softsub=True → embed subtitle in video and send as document
        # softsub=False → send video as streaming + subtitle as separate file
        # softsub=None → default: if with_subtitle and imdbplay → softsub, else separate
        if softsub is None:
            softsub = with_subtitle and state.get("use_imdbplay_sub", False)

        do_softsub = with_subtitle and softsub
        do_separate_sub = with_subtitle and not softsub

        if do_softsub:
            # Embed subtitle as softsub in the video
            try:
                if sub_path and os.path.exists(sub_path):
                    persian_sub_path = sub_path
                elif state.get("use_imdbplay_sub"):
                    sub_out_dir = os.path.join(IMDB_OUTPUT_FOLDER, f"sub_{user_id}_{int(time.time())}")
                    os.makedirs(sub_out_dir, exist_ok=True)
                    try:
                        await status_msg.edit("💬 در حال جستجوی زیرنویس فارسی...")
                    except Exception:
                        pass
                    persian_sub_path = await get_persian_subtitle(
                        imdb_id,
                        season=season,
                        episode=episode,
                        out_dir=sub_out_dir,
                    )
                else:
                    persian_sub_path = sub_path

                if persian_sub_path and os.path.exists(persian_sub_path):
                    try:
                        await status_msg.edit("📝 در حال جاسازی زیرنویس درون ویدیو (softsub)...")
                    except Exception:
                        pass
                    softsub_out = os.path.join(out_dir, f"softsub_{int(time.time())}.mp4")
                    embedded = embed_subtitle_soft(video_path, persian_sub_path, softsub_out)
                    if embedded and os.path.exists(embedded):
                        final_path = embedded
                        if not sub_name:
                            sub_name = "Persian (softsub)"
                        try:
                            await status_msg.edit("✅ زیرنویس داخل ویدیو قرار گرفت!")
                        except Exception:
                            pass
                    else:
                        try:
                            await status_msg.edit("⚠ جاسازی نشد، ویدیو بدون زیرنویس ارسال می‌شه")
                        except Exception:
                            pass
                        do_separate_sub = True  # fallback to separate
                else:
                    logger.info(f"[IMDB] No Persian subtitle found for {imdb_id}")
                    try:
                        await status_msg.edit("⚠ زیرنویس فارسی پیدا نشد، ویدیو بدون زیرنویس ارسال می‌شه")
                    except Exception:
                        pass
            except Exception as sub_err:
                logger.warning(f"[IMDB] Persian subtitle fetch/embed failed: {sub_err}", exc_info=True)
                do_separate_sub = True  # fallback

        file_size = os.path.getsize(final_path)
        size_mb = file_size / 1024 / 1024
        if size_mb > 1900:
            await status_msg.edit(f"⚠ فایل خیلی بزرگه ({size_mb:.1f} MB). محدودیت تلگرام 2GB.")
            return

        await status_msg.edit(f"📤 در حال آپلود ({size_mb:.1f} MB)...", buttons=None)
        caption = _imdb_dl_caption(title, season, episode, sub_name if with_subtitle else None, size_mb)

        cover = info.get("cover")
        if cover:
            cover_path = await _imdb_download_cover(cover, out_dir)

        await send_file_with_progress(
            client=event.client,
            chat_id=event.chat_id,
            filepath=final_path,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=not do_softsub,
            thumb_filepath=cover_path,
            ul_id=f"imd_ul_{dl_id}",
            force_document=do_softsub,
        )
        active_downloads.pop(dl_id, None)

        # If separate subtitle (not softsub), send subtitle file after video
        if do_separate_sub:
            if persian_sub_path and os.path.exists(persian_sub_path):
                # Already downloaded — just send it
                sub_size_kb = os.path.getsize(persian_sub_path) / 1024
                if season and episode:
                    sub_caption = f"📄 زیرنویس فارسی | **{title}** - S{season:02d}E{episode:02d}"
                else:
                    sub_caption = f"📄 زیرنویس فارسی | **{title}**"
                await event.client.send_file(
                    entity=event.chat_id,
                    file=persian_sub_path,
                    caption=sub_caption,
                    parse_mode="md",
                    force_document=True,
                )
                logger.info(f"[IMDB] Subtitle sent separately: {persian_sub_path} ({sub_size_kb:.1f} KB)")
            elif not with_subtitle:
                # No subtitle selected — try imdbplay
                try:
                    sub_out_dir = os.path.join(IMDB_OUTPUT_FOLDER, f"sub_{user_id}_{int(time.time())}")
                    os.makedirs(sub_out_dir, exist_ok=True)
                    try:
                        await status_msg.edit("💬 در حال جستجوی زیرنویس فارسی...")
                    except Exception:
                        pass
                    persian_sub_path = await get_persian_subtitle(
                        imdb_id,
                        season=season,
                        episode=episode,
                        out_dir=sub_out_dir,
                    )
                    if persian_sub_path and os.path.exists(persian_sub_path):
                        sub_size_kb = os.path.getsize(persian_sub_path) / 1024
                        if season and episode:
                            sub_caption = f"📄 زیرنویس فارسی | **{title}** - S{season:02d}E{episode:02d}\n\n📀 از سرورهای imdbplay"
                        else:
                            sub_caption = f"📄 زیرنویس فارسی | **{title}**\n\n📀 از سرورهای imdbplay"
                        await event.client.send_file(
                            entity=event.chat_id,
                            file=persian_sub_path,
                            caption=sub_caption,
                            parse_mode="md",
                            force_document=True,
                        )
                        logger.info(f"[IMDB] Persian subtitle sent: {persian_sub_path} ({sub_size_kb:.1f} KB)")
                    else:
                        logger.info(f"[IMDB] No Persian subtitle found for {imdb_id}")
                except Exception as sub_err:
                    logger.warning(f"[IMDB] Persian subtitle fetch/send failed: {sub_err}", exc_info=True)
                finally:
                    if persian_sub_path and os.path.exists(persian_sub_path):
                        try:
                            os.unlink(persian_sub_path)
                        except Exception:
                            pass
        # End Persian subtitle section

    except asyncio.CancelledError:
        active_downloads.pop(dl_id, None)
        try:
            await status_msg.edit("❌ دانلود لغو شد.", buttons=None)
        except Exception:
            pass
    except Exception as e:
        active_downloads.pop(dl_id, None)
        logger.error(f"[IMDB] download failed: {e}", exc_info=True)
        try:
            await status_msg.edit(f"❌ خطا: {e}", buttons=None)
        except Exception:
            pass
    finally:
        if updater:
            updater.cancel()
        imdb_states.pop(user_id, None)
        for p in [cover_path, sub_path, video_path]:
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except Exception:
                    pass
        if final_path and final_path != video_path and os.path.exists(final_path):
            try:
                os.unlink(final_path)
            except Exception:
                pass
        try:
            os.rmdir(out_dir)
        except Exception:
            pass


async def server_cmd(event):
    """انتخاب سرور بین IMDb و Doostihaa و Farsiland"""
    user_id = event.sender_id
    buttons = [
        [Button.inline("🌍 IMDb (imdbplay)", "srv_imdb")],
        [Button.inline("🇮🇷 دوستی‌ها (doostihaa)", "srv_doostihaa")],
        [Button.inline("🇮🇷 فارسی‌لند (farsiland)", "srv_farsiland")],
    ]
    current = user_iran_server.get(user_id, "none")
    await event.reply(
        f"🖥 انتخاب سرور جستجو\n\nسرور فعلی: **{current}**\n\nیکی را انتخاب کنید:",
        buttons=buttons,
        parse_mode="md",
    )


async def server_callback(event):
    """Handle server selection callback"""
    data = event.data.decode()
    user_id = event.sender_id
    server = data.replace("srv_", "")
    user_iran_server[user_id] = server
    names = {"imdb": "IMDb (imdbplay)", "doostihaa": "دوستی‌ها (doostihaa)", "farsiland": "فارسی‌لند (farsiland)"}
    await event.answer(f"سرور انتخاب شد: {names.get(server, server)}", alert=False)
    await event.edit(f"✅ سرور انتخاب شد: **{names.get(server, server)}**\n\nبرای جستجو از @bot استفاده کنید با پیشوند iran: (برای دوستی‌ها) یا imd: (برای IMDb)", parse_mode="md")



# ─── Iran server (doostihaa) callbacks ───

async def iran_cb_title(event):
    """Handle iran title selection — show qualities"""
    data = event.data.decode()
    post_id = data.replace("irn_sel_", "")
    user_id = event.sender_id
    await event.edit("\U0001F50D \u062F\u0631 \u062D\u0627\u0644 \u062F\u0631\u06CC\u0627\u0641\u062A \u0644\u06CC\u0633\u062A \u06A9\u06CC\u0641\u06CC\u062A\u200C\u0647\u0627...", buttons=None)
    from searcher.iranserver.doostihaa_search import get_qualities_doostihaa
    qualities = await get_qualities_doostihaa(post_id)
    if not qualities:
        await event.edit("\u274C \u06A9\u06CC\u0641\u06CC\u062A\u06CC \u067E\u06CC\u062F\u0627 \u0646\u0634\u062F.", buttons=None)
        return
    iran_states[user_id] = {"post_id": post_id, "qualities": qualities}
    # Build quality buttons
    buttons = []
    row = []
    seen_labels = set()
    for q in qualities:
        label = q["label"]
        if label in seen_labels:
            continue
        seen_labels.add(label)
        row.append(Button.inline(label, f"irn_q_{label}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([Button.inline("\U0001F6AB \u0628\u0633\u062A\u0646", "irn_close")])
    await event.edit("\U0001F3AF \u06A9\u06CC\u0641\u06CC\u062A \u0631\u0648 \u0627\u0646\u062A\u062E\u0627\u0628 \u06A9\u0646:", buttons=buttons)


async def iran_cb_quality(event):
    """Handle quality selection — start download"""
    data = event.data.decode()
    quality_label = data.replace("irn_q_", "")
    user_id = event.sender_id
    state = iran_states.get(user_id)
    if not state:
        await event.answer("\u23F0 \u0646\u0634\u0633\u062A \u0634\u0645\u0627 \u0645\u0646\u0642\u0636\u06CC \u0634\u062F\u0647. \u062F\u0648\u0628\u0627\u0631\u0647 \u0633\u0631\u0686 \u06A9\u0646\u06CC\u062F.", alert=True)
        return
    # Find the URL for this quality
    url = None
    for q in state["qualities"]:
        if q["label"] == quality_label:
            url = q["url"]
            break
    if not url:
        await event.answer("\u06A9\u06CC\u0641\u06CC\u062A \u0646\u0627\u0645\u0639\u062A\u0628\u0631", alert=True)
        return
    await event.answer("\u2705 \u0634\u0631\u0648\u0639 \u062F\u0627\u0646\u0644\u0648\u062F...", alert=False)
    asyncio.create_task(_iran_download_task(event, user_id, url, quality_label))


async def iran_cb_nosub(event):
    pass


async def iran_cb_close(event):
    user_id = event.sender_id
    iran_states.pop(user_id, None)
    try:
        await event.delete()
    except Exception:
        await event.edit("\U0001F6AB \u0628\u0633\u062A\u0647 \u0634\u062F", buttons=None)


async def _iran_download_task(event, user_id, url, quality_label):
    """Download task for iran server (doostihaa)"""
    out_dir = os.path.join(IMDB_OUTPUT_FOLDER, f"iran_{user_id}_{int(time.time())}")
    os.makedirs(out_dir, exist_ok=True)
    dl_id = f"iran_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("\u274C Cancel", f"dlcancel_{dl_id}")]]
    try:
        try:
            status_msg = await event.client.send_message(event.chat_id, "\U0001F4E5 \u062F\u0627\u0646\u0644\u0648\u062F \u0628\u0627 \u06A9\u06CC\u0641\u06CC\u062A " + quality_label + "...")
        except Exception:
            status_msg = await event.edit("\U0001F4E5 \u062F\u0627\u0646\u0644\u0648\u062F...", buttons=cancel_btn)
        from searcher.iranserver.doostihaa_search import download_doostihaa
        video_path = await download_doostihaa(url, out_dir)
        if not video_path or not os.path.exists(video_path):
            await status_msg.edit("\u274C \u062F\u0627\u0646\u0644\u0648\u062F \u0646\u0627\u0645\u0648\u0641 \u0628\u0648\u062F.", buttons=None)
            return
        size_mb = os.path.getsize(video_path) / 1024 / 1024
        await status_msg.edit(f"\u2705 \u0648\u06CC\u062F\u06CC\u0648 \u062F\u0627\u0646\u0644\u0648\u062F \u0634\u062F ({size_mb:.1f} MB)", buttons=None)
        await send_file_with_progress(
            client=event.client,
            chat_id=event.chat_id,
            filepath=video_path,
            caption=f"\U0001F3AC {quality_label} \U0001F4C0 \u062F\u0648\u0633\u062A\u06CC\u200C\u0647\u0627\n\U0001F4BE {size_mb:.1f} MB",
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=f"iran_ul_{dl_id}",
        )
        active_downloads.pop(dl_id, None)
    except asyncio.CancelledError:
        active_downloads.pop(dl_id, None)
        try: await status_msg.edit("\u274C \u062F\u0627\u0646\u0644\u0648\u062F \u0644\u063A\u0648 \u0634\u062F.", buttons=None)
        except: pass
    except Exception as e:
        active_downloads.pop(dl_id, None)
        logger.error(f"[IRAN] download failed: {e}", exc_info=True)
        try: await status_msg.edit(f"\u274C \u062E\u0637\u0627: {e}", buttons=None)
        except: pass
    finally:
        iran_states.pop(user_id, None)
        if video_path and os.path.exists(video_path):
            try: os.unlink(video_path)
            except: pass
        try: os.rmdir(out_dir)
        except: pass




# ─── diycraft series episode callbacks ───

dc_states = {}  # diycraft states (defined here if not already)


# ─── sarrast.com callbacks (PDF/ZIP/images) ───

async def sarrast_pdf_callback(event):
    """ساخت PDF از فصل sarrast."""
    data = event.data.decode()
    msg_id = data.replace("sr_pdf_", "")
    state_key = f"{event.chat_id}_{msg_id}"
    state = sr_states.get(state_key)
    if not state:
        await event.answer("⏰ نشست منقضی شده. دوباره لینک رو بفرست.", alert=True)
        return
    await event.answer("📄 شروع ساخت PDF...", alert=False)
    from otherwebsiteshandler.sarrast_handler import download_chapter_pdf
    info = state["info"]
    url = state["url"]
    series_title = info.get("series_title", "sarrast")
    chapter_title = info.get("title", "")
    safe_series = re.sub(r'[<>:"/\\|?*]', "_", series_title)[:60]
    safe_chapter = re.sub(r'[<>:"/\\|?*]', "_", chapter_title)[:60]
    # نام فایل: فقط نام داستان و قسمت
    out_path = os.path.join(OUTPUT_FOLDER, f"{safe_series}_{safe_chapter}.pdf")
    await event.edit(f"📄 در حال دانلود {len(info['images'])} تصویر و ساخت PDF...", buttons=None)

    # progress callback برای نمایش پیشرفت دانلود به کاربر
    last_pct = [-1]
    last_update_time = [0.0]
    total_images = len(info['images'])

    async def _progress_cb(done, total, current_url):
        if not total:
            return
        pct = done * 100 // total
        now = time.time()
        # فقط هر ۵ ثانیه یا در درصد کلیدی آپدیت کن (محدود کردن rate)
        if pct != last_pct[0] and (pct % 20 == 0 or now - last_update_time[0] > 5):
            last_pct[0] = pct
            last_update_time[0] = now
            try:
                if done < total:
                    await event.edit(
                        f"📄 در حال دانلود تصاویر: {done}/{total} ({pct}%)",
                        buttons=None,
                    )
                else:
                    await event.edit(
                        f"📄 دانلود کامل شد. در حال ساخت PDF از {total} تصویر...",
                        buttons=None,
                    )
            except Exception:
                pass  # ignore edit errors (event may be stale)

    try:
        result = await download_chapter_pdf(url, out_path, progress_cb=_progress_cb)
        if not result or not os.path.exists(result):
            await event.edit("❌ ساخت PDF ناموفق بود. لطفاً دوباره تلاش کن.")
            sr_states.pop(state_key, None)
            return
        size_mb = os.path.getsize(result) / 1024 / 1024
        await event.edit(f"✅ PDF ساخته شد ({size_mb:.1f} MB)\n📤 در حال آپلود...")
        await send_file_with_progress(
            client=event.client,
            chat_id=event.chat_id,
            filepath=result,
            caption=f"📖 **{series_title}**\n📺 **{chapter_title}**\n🖼 {len(info['images'])} تصویر\n💾 {size_mb:.1f} MB\n📄 PDF",
            status_msg=event,
            buttons=None,
            supports_streaming=False,
            force_document=True,
        )
        # Cleanup
        try:
            os.unlink(result)
        except Exception:
            pass
        # Cleanup state
        sr_states.pop(state_key, None)
    except Exception as e:
        logger.error(f"[Sarrast] PDF error: {e}", exc_info=True)
        try:
            await event.edit(f"❌ خطا در ساخت PDF: {e}\n\nدوباره تلاش کن یا از گزینه ZIP استفاده کن.")
        except Exception:
            pass
        sr_states.pop(state_key, None)


async def sarrast_pdf_translated_callback(event):
    """ساخت PDF با ترجمه فارسی از فصل sarrast."""
    data = event.data.decode()
    msg_id = data.replace("sr_pdftr_", "")
    state_key = f"{event.chat_id}_{msg_id}"
    state = sr_states.get(state_key)
    if not state:
        await event.answer("⏰ نشست منقضی شده. دوباره لینک رو بفرست.", alert=True)
        return
    await event.answer("📄 شروع ساخت PDF با ترجمه...", alert=False)
    from otherwebsiteshandler.sarrast_handler import download_chapter_pdf_translated
    info = state["info"]
    url = state["url"]
    series_title = info.get("series_title", "sarrast")
    chapter_title = info.get("title", "")
    safe_series = re.sub(r'[<>:"/\\|?*]', "_", series_title)[:60]
    safe_chapter = re.sub(r'[<>:"/\\|?*]', "_", chapter_title)[:60]
    # نام فایل: فقط نام داستان و قسمت - بدون prefix و timestamp
    out_path = os.path.join(OUTPUT_FOLDER, f"{safe_series}_{safe_chapter}.pdf")
    await event.edit(
        f"🌐 در حال ساخت PDF با ترجمه فارسی از {len(info['images'])} تصویر...",
        buttons=None,
    )

    # progress callback برای نمایش پیشرفت
    last_pct = [-1]
    last_update_time = [0.0]
    total_images = len(info['images'])

    async def _progress_cb(done, total, current_url):
        if not total:
            return
        pct = done * 100 // total
        now = time.time()
        if pct != last_pct[0] and (pct % 20 == 0 or now - last_update_time[0] > 5):
            last_pct[0] = pct
            last_update_time[0] = now
            try:
                if done < total:
                    await event.edit(
                        f"🌐 در حال دانلود تصاویر: {done}/{total} ({pct}%)",
                        buttons=None,
                    )
                else:
                    await event.edit(
                        f"🌐 دانلود کامل شد. در حال رسم ترجمه فارسی و ساخت PDF از {total} تصویر...",
                        buttons=None,
                    )
            except Exception:
                pass

    try:
        result = await download_chapter_pdf_translated(url, out_path, progress_cb=_progress_cb)
        if not result or not os.path.exists(result):
            await event.edit("❌ ساخت PDF با ترجمه ناموفق بود. لطفاً دوباره تلاش کن.")
            sr_states.pop(state_key, None)
            return
        size_mb = os.path.getsize(result) / 1024 / 1024
        await event.edit(f"✅ PDF با ترجمه ساخته شد ({size_mb:.1f} MB)\n📤 در حال آپلود...")
        await send_file_with_progress(
            client=event.client,
            chat_id=event.chat_id,
            filepath=result,
            caption=f"📖 **{series_title}**\n📺 **{chapter_title}**\n🖼 {len(info['images'])} تصویر\n💾 {size_mb:.1f} MB\n🌐 PDF با ترجمه فارسی",
            status_msg=event,
            buttons=None,
            supports_streaming=False,
            force_document=True,
        )
        # Cleanup
        try:
            os.unlink(result)
        except Exception:
            pass
        # Cleanup state
        sr_states.pop(state_key, None)
    except RuntimeError as e:
        # خطاهای مربوط به وابستگی‌ها یا فونت - به کاربر نشون بده
        logger.error(f"[Sarrast] PDF translated RuntimeError: {e}", exc_info=True)
        err_str = str(e)
        # اگه خطا درباره وابستگی‌هاست، پیام واضح بده
        if "نصب نیست" in err_str or "pip install" in err_str:
            try:
                await event.edit(
                    f"❌ **ترجمه کار نمی‌کنه!**\n\n"
                    f"وابستگی‌های لازم نصب نیست:\n`{err_str[:300]}`\n\n"
                    f"این پکیج‌ها رو به requirements.txt اضافه کن و کانتینر رو rebuild کن:\n"
                    f"```\nfonttools\nPillow\n```"
                )
            except Exception:
                pass
        elif "فونت" in err_str:
            try:
                await event.edit(
                    f"❌ **دانلود فونت ناموفق بود**\n\n"
                    f"{err_str[:300]}\n\n"
                    f"ممکنه سایت sarrast موقتاً در دسترس نباشه. دوباره تلاش کن."
                )
            except Exception:
                pass
        else:
            try:
                await event.edit(
                    f"❌ خطا در ساخت PDF با ترجمه:\n`{err_str[:300]}`\n\n"
                    f"دوباره تلاش کن یا از گزینه بدون ترجمه استفاده کن."
                )
            except Exception:
                pass
        sr_states.pop(state_key, None)
    except Exception as e:
        logger.error(f"[Sarrast] PDF translated error: {e}", exc_info=True)
        try:
            await event.edit(f"❌ خطا در ساخت PDF با ترجمه: {e}\n\nدوباره تلاش کن یا از گزینه بدون ترجمه استفاده کن.")
        except Exception:
            pass
        sr_states.pop(state_key, None)


async def sarrast_zip_callback(event):
    """دانلود همه‌ی تصاویر به‌صورت ZIP."""
    data = event.data.decode()
    msg_id = data.replace("sr_zip_", "")
    state_key = f"{event.chat_id}_{msg_id}"
    state = sr_states.get(state_key)
    if not state:
        await event.answer("⏰ نشست منقضی شده.", alert=True)
        return
    await event.answer("🖼 شروع دانلود ZIP...", alert=False)
    from otherwebsiteshandler.sarrast_handler import download_chapter_as_zip
    info = state["info"]
    url = state["url"]
    series_title = info.get("series_title", "sarrast")
    chapter_title = info.get("title", "")
    safe_series = re.sub(r'[<>:"/\\|?*]', "_", series_title)[:60]
    safe_chapter = re.sub(r'[<>:"/\\|?*]', "_", chapter_title)[:60]
    # نام فایل: فقط نام داستان و قسمت
    out_path = os.path.join(OUTPUT_FOLDER, f"{safe_series}_{safe_chapter}.zip")
    await event.edit(f"🖼 در حال دانلود {len(info['images'])} تصویر و ساخت ZIP...", buttons=None)
    try:
        result = await download_chapter_as_zip(url, out_path, progress_cb=None)
        if not result or not os.path.exists(result):
            await event.edit("❌ ساخت ZIP ناموفق بود.")
            return
        size_mb = os.path.getsize(result) / 1024 / 1024
        await event.edit(f"✅ ZIP ساخته شد ({size_mb:.1f} MB)\n📤 در حال آپلود...")
        await send_file_with_progress(
            client=event.client,
            chat_id=event.chat_id,
            filepath=result,
            caption=f"📖 **{series_title}**\n📺 **{chapter_title}**\n🖼 {len(info['images'])} تصویر\n💾 {size_mb:.1f} MB\n📦 ZIP",
            status_msg=event,
            buttons=None,
            supports_streaming=False,
            force_document=True,
        )
        try:
            os.unlink(result)
        except Exception:
            pass
        sr_states.pop(state_key, None)
    except Exception as e:
        logger.error(f"[Sarrast] ZIP error: {e}", exc_info=True)
        await event.edit(f"❌ خطا: {e}")


async def sarrast_imgs_callback(event):
    """ارسال تک‌تک تصاویر به‌صورت آلبوم‌های ۱۰ تایی (سریع - دانلود + ارسال موازی)."""
    data = event.data.decode()
    msg_id = data.replace("sr_imgs_", "")
    state_key = f"{event.chat_id}_{msg_id}"
    state = sr_states.get(state_key)
    if not state:
        await event.answer("⏰ نشست منقضی شده.", alert=True)
        return
    await event.answer("🖼 شروع دانلود و ارسال تصاویر...", alert=False)
    from otherwebsiteshandler.sarrast_handler import download_chapter_images
    info = state["info"]
    url = state["url"]
    series_title = info.get("series_title", "sarrast")
    chapter_title = info.get("title", "")
    total = len(info["images"])
    out_dir = os.path.join(OUTPUT_FOLDER, f"sr_imgs_{event.chat_id}_{int(time.time())}")
    os.makedirs(out_dir, exist_ok=True)

    await event.edit(f"🖼 در حال دانلود {total} تصویر (موازی)...", buttons=None)

    # Step 1: دانلود همه‌ی تصاویر موازی
    last_pct = [-1]
    async def progress_cb(done, total_count, current_url):
        pct = done * 100 // total_count if total_count else 0
        if pct != last_pct[0] and pct % 10 == 0:
            last_pct[0] = pct
            try:
                await event.edit(f"⬇️ دانلود تصاویر: {done}/{total_count} ({pct}%)", buttons=None)
            except Exception:
                pass

    try:
        paths = await download_chapter_images(url, out_dir, progress_cb=progress_cb, max_concurrent=8)
        if not paths:
            await event.edit("❌ هیچ تصویری دانلود نشد.")
            return
    except Exception as e:
        logger.error(f"[Sarrast] download error: {e}", exc_info=True)
        await event.edit(f"❌ خطا در دانلود: {e}")
        return

    # Step 2: ارسال به‌صورت آلبوم‌های ۱۰ تایی موازی
    # ابتدا WebP → JPEG تبدیل می‌کنیم چون Telegram WebP رو به‌عنوان استیکر می‌شناسه
    await event.edit(f"🔄 در حال تبدیل WebP → JPEG و ارسال {len(paths)} تصویر...", buttons=None)
    import shutil

    def _convert_to_jpeg(src_path: str) -> str:
        """تبدیل WebP به JPEG (در همان پوشه، با پسوند .jpg)."""
        try:
            from PIL import Image
            img = Image.open(src_path)
            # Convert to RGB (JPEG doesn't support alpha)
            if img.mode in ("RGBA", "P", "LA"):
                # Composite over white background
                bg = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "P":
                    img = img.convert("RGBA")
                bg.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
                img = bg
            elif img.mode != "RGB":
                img = img.convert("RGB")
            jpg_path = src_path.rsplit(".", 1)[0] + ".jpg"
            img.save(jpg_path, "JPEG", quality=92, optimize=True)
            return jpg_path
        except Exception as e:
            logger.warning(f"[Sarrast] JPEG conversion failed for {src_path}: {e}")
            return src_path  # Fallback to original

    # Convert all WebP to JPEG (in thread executor to not block event loop)
    loop = asyncio.get_event_loop()
    jpg_paths = []
    for p in paths:
        try:
            jpg_p = await loop.run_in_executor(None, _convert_to_jpeg, p)
            jpg_paths.append(jpg_p)
        except Exception as e:
            logger.warning(f"[Sarrast] convert error: {e}")
            jpg_paths.append(p)  # Fallback

    await event.edit(f"📤 در حال ارسال {len(jpg_paths)} تصویر به‌صورت photo...", buttons=None)

    BATCH_SIZE = 10
    sent_count = 0
    total_paths = len(jpg_paths)

    async def send_batch(batch_start_idx: int, batch_paths: list):
        """ارسال یه آلبوم ۱۰ تایی به‌صورت photo (JPEG)."""
        nonlocal sent_count
        # Caption فقط روی اولین تصویر
        caption = (
            f"📖 **{series_title}**\n📺 **{chapter_title}**\n"
            f"🖼 تصاویر {batch_start_idx + 1}-{batch_start_idx + len(batch_paths)}/{total_paths}"
        ) if batch_paths else ""
        try:
            # client.send_file با لیست فایل‌ها → آلبوم خودکار می‌سازه
            # force_document=False → به‌صورت photo (نه document) ارسال می‌شه
            # چون JPEG هست، به‌عنوان photo شناخته می‌شه (نه استیکر)
            await event.client.send_file(
                event.chat_id,
                batch_paths,
                caption=caption,
                parse_mode="md",
                force_document=False,  # ← به‌صورت photo
                silent=True,  # بدون نوتیفیکیشن صدا
            )
            return len(batch_paths)
        except Exception as e:
            logger.error(f"[Sarrast] Album send failed (batch {batch_start_idx}): {e}")
            # Fallback: send one by one
            count = 0
            for i, p in enumerate(batch_paths):
                try:
                    cap = caption if i == 0 else ""
                    await event.client.send_file(
                        event.chat_id,
                        p,
                        caption=cap,
                        parse_mode="md",
                        force_document=False,  # photo
                        silent=True,
                    )
                    count += 1
                except Exception as e2:
                    logger.warning(f"[Sarrast] Single send failed {batch_start_idx + i}: {e2}")
            return count

    # Process batches sequentially (Telethon's album send is already efficient)
    # But we can run 2 in parallel for better throughput
    sem = asyncio.Semaphore(2)
    async def process_batch(start: int, batch: list):
        async with sem:
            return await send_batch(start, batch)

    batch_tasks = []
    for batch_start in range(0, len(jpg_paths), BATCH_SIZE):
        batch = jpg_paths[batch_start:batch_start + BATCH_SIZE]
        batch_tasks.append(process_batch(batch_start, batch))

    # Wait for all batches to complete
    results = await asyncio.gather(*batch_tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, int):
            sent_count += r
        elif isinstance(r, Exception):
            logger.error(f"[Sarrast] Batch exception: {r}")

    await event.edit(f"✅ {sent_count}/{total_paths} تصویر ارسال شد.")
    # Cleanup
    try:
        shutil.rmtree(out_dir, ignore_errors=True)
    except Exception:
        pass
    sr_states.pop(state_key, None)


# ─── Comic sites callbacks ───


async def process_comic_request(event, url: str, status_msg, site_key: str):
    """پردازش URL کمیک - تشخیص صفحه کمیک یا صفحه سرچ."""
    # بررسی آیا URL صفحه کمیک هست یا سرچ
    if _is_comic_page_url(url, site_key):
        # صفحه کمیک - استخراج تصاویر/ویدیو
        info = await extract_comic_info(url, site_key)
        if not info:
            await status_msg.edit("❌ کمیک پیدا نشد یا تصویری موجود نیست.")
            return

        title = info.get("title", "Comic")
        images = info.get("images", [])
        videos = info.get("videos", [])
        site_name = info.get("display_name", "Comic")

        # ذخیره state
        session_id = f"comic_{event.chat_id}_{event.id}_{int(time.time())}"
        comic_sessions[session_id] = {
            "url": url,
            "info": info,
            "site_key": site_key,
            "chat_id": event.chat_id,
        }

        # نمایش دکمه‌ها
        buttons = []
        if images:
            buttons.append([Button.inline(f"📄 PDF ({len(images)} تصویر)", f"cmpdf_{session_id}")])
            buttons.append([Button.inline(f"🖼 تک‌تک تصاویر ({len(images)})", f"cmimg_{session_id}")])
        if videos:
            buttons.append([Button.inline(f"🎬 ویدیو ({len(videos)})", f"cmvid_{session_id}")])

        if not buttons:
            await status_msg.edit("❌ هیچ تصویر یا ویدیویی پیدا نشد.")
            comic_sessions.pop(session_id, None)
            return

        await status_msg.edit(
            f"📚 **{title[:80]}**\n"
            f"🌐 {site_name}\n"
            f"🖼 تصاویر: {len(images)}\n"
            f"🎬 ویدیو: {len(videos)}\n\n"
            f"یکی از گزینه‌ها رو انتخاب کن:",
            buttons=buttons,
            parse_mode="md",
        )

    elif _is_comic_search_url(url, site_key):
        # صفحه سرچ - استخراج لیست کمیک‌ها
        results = await extract_comic_search_results(url, site_key)
        if not results or not results.get("comics"):
            await status_msg.edit("❌ کمیک‌ای پیدا نشد.")
            return

        comics = results["comics"]
        site_name = results.get("display_name", "Comic")

        # ذخیره state
        session_id = f"comsearch_{event.chat_id}_{event.id}_{int(time.time())}"
        comic_sessions[session_id] = {
            "comics": comics,
            "site_key": site_key,
            "chat_id": event.chat_id,
        }

        # نمایش لیست کمیک‌ها (20 تا در هر صفحه)
        # ذخیره offset در state
        comic_sessions[session_id]["offset"] = 0

        buttons = _build_comic_search_buttons(session_id, comics, 0)

        await status_msg.edit(
            f"🔍 نتایج جستجو در {site_name}\n"
            f"📚 {len(comics)} کمیک پیدا شد\n"
            f"📄 صفحه 1/{(len(comics) - 1) // 20 + 1}\n\n"
            f"یکی رو انتخاب کن:",
            buttons=buttons,
            parse_mode="md",
        )
    else:
        await status_msg.edit("❌ URL قابل تشخیص نیست.")


def _build_comic_search_buttons(session_id: str, comics: list, offset: int) -> list:
    """ساخت دکمه‌های لیست کمیک با pagination (صفحه قبل/بعد)."""
    PER_PAGE = 20
    buttons = []
    end = min(offset + PER_PAGE, len(comics))
    page_num = offset // PER_PAGE + 1
    total_pages = (len(comics) - 1) // PER_PAGE + 1

    for i in range(offset, end):
        comic_url, comic_title = comics[i]
        safe_title = comic_title[:50] if comic_title else comic_url.split("/")[-1][:50]
        buttons.append([Button.inline(f"📚 {safe_title}", f"cmsel_{session_id}_{i}")])

    # دکمه‌های صفحه قبل/بعد
    nav_buttons = []
    if offset > 0:
        nav_buttons.append(Button.inline("⬅️ صفحه قبل", f"cmpage_{session_id}_{offset - PER_PAGE}"))
    nav_buttons.append(Button.inline(f"📄 {page_num}/{total_pages}", "noop_"))
    if end < len(comics):
        nav_buttons.append(Button.inline("صفحه بعد ➡️", f"cmpage_{session_id}_{offset + PER_PAGE}"))

    if len(nav_buttons) > 1:
        buttons.append(nav_buttons)

    return buttons


async def comic_page_callback(event):
    """تغییر صفحه در لیست سرچ کمیک."""
    data = event.data.decode()
    # format: cmpage_{session_id}_{offset}
    # پیدا کردن offset (آخرین عدد)
    parts = data.split("_")
    offset = int(parts[-1])
    session_id = "_".join(parts[1:-1])

    state = comic_sessions.get(session_id)
    if not state:
        await event.answer("⏰ نشست منقضی شده.", alert=True)
        return

    comics = state.get("comics", [])
    site_key = state.get("site_key", "")
    site_name = SITES.get(site_key, {}).get("display_name", "Comic") if site_key else "Comic"

    buttons = _build_comic_search_buttons(session_id, comics, offset)
    page_num = offset // 20 + 1
    total_pages = (len(comics) - 1) // 20 + 1

    await event.answer()
    try:
        await event.edit(
            f"🔍 نتایج جستجو در {site_name}\n"
            f"📚 {len(comics)} کمیک پیدا شد\n"
            f"📄 صفحه {page_num}/{total_pages}\n\n"
            f"یکی رو انتخاب کن:",
            buttons=buttons,
            parse_mode="md",
        )
    except Exception:
        pass


async def faceswap_nsfw_init_callback(event):
    """شروع Face Swap NSFW - درخواست عکس face از کاربر."""
    data = event.data.decode()
    session_id = data.replace("fsnsfw_", "")

    ocr_state = ocr_sessions.get(session_id)
    if not ocr_state:
        await event.answer("⏰ نشست منقضی شده.", alert=True)
        return

    target_image_path = ocr_state["image_path"]

    fs_session_id = f"fs_{event.chat_id}_{event.id}_{int(time.time())}"
    faceswap_sessions[fs_session_id] = {
        "target_image_path": target_image_path,
        "chat_id": event.chat_id,
        "waiting_for_face": True,
        "nsfw": True,
    }

    user_state[event.chat_id] = {
        "action": "wait_for_face_swap",
        "fs_session_id": fs_session_id,
    }

    await event.answer()
    await event.edit(
        "🔞 **Face Swap (+18)**\n\n"
        "عکس اصلی ذخیره شد!\n"
        "حالا عکس **face** (چهره‌ای که می‌خوای جایگزین کنی) رو بفرست.",
        buttons=None,
        parse_mode="md",
    )


async def faceswap_init_callback(event):
    """شروع Face Swap - درخواست عکس face از کاربر."""
    data = event.data.decode()
    session_id = data.replace("fsinit_", "")

    # پیدا کردن عکس target از OCR sessions
    ocr_state = ocr_sessions.get(session_id)
    if not ocr_state:
        await event.answer("⏰ نشست منقضی شده.", alert=True)
        return

    target_image_path = ocr_state["image_path"]

    # ساخت session جدید برای face swap
    fs_session_id = f"fs_{event.chat_id}_{event.id}_{int(time.time())}"
    faceswap_sessions[fs_session_id] = {
        "target_image_path": target_image_path,
        "chat_id": event.chat_id,
        "waiting_for_face": True,
    }

    # تنظیم user_state برای دریافت عکس بعدی
    user_state[event.chat_id] = {
        "action": "wait_for_face_swap",
        "fs_session_id": fs_session_id,
    }

    await event.answer()
    await event.edit(
        "🎭 **Face Swap**\n\n"
        "عکس اصلی ذخیره شد!\n"
        "حالا عکس **face** (چهره‌ای که می‌خوای جایگزین کنی) رو بفرست.",
        buttons=None,
        parse_mode="md",
    )


async def faceswap_process_callback(event):
    """پردازش Face Swap با عکس face که کاربر فرستاده."""
    chat_id = event.chat_id
    state = user_state.get(chat_id)

    if not state or state.get("action") != "wait_for_face_swap":
        return False

    fs_session_id = state.get("fs_session_id")
    fs_state = faceswap_sessions.get(fs_session_id)
    if not fs_state:
        return False

    # دریافت عکس face
    if event.message and event.message.photo:
        face_path = os.path.join(OUTPUT_FOLDER, f"fs_face_{chat_id}_{event.id}.jpg")
        await event.message.download_media(face_path)
    elif event.message and event.message.document:
        mime = getattr(event.message.document, "mime_type", "") or ""
        if mime.startswith("image/"):
            face_path = os.path.join(OUTPUT_FOLDER, f"fs_face_{chat_id}_{event.id}")
            await event.message.download_media(face_path)
        else:
            return False
    else:
        return False

    # پاک کردن user_state
    user_state.pop(chat_id, None)

    target_path = fs_state["target_image_path"]
    status_msg = await event.reply("🎭 در حال انجام Face Swap... این ممکنه چند ثانیه طول بکشه.")

    try:
        success, result = await face_swap(target_path, face_path)
        if success and os.path.exists(result):
            size_mb = os.path.getsize(result) / 1024 / 1024
            await status_msg.edit(f"✅ Face Swap انجام شد! ({size_mb:.1f} MB)\n📤 در حال آپلود...")
            await send_file_with_progress(
                client=event.client,
                chat_id=event.chat_id,
                filepath=result,
                caption="🎭 Face Swap Result",
                status_msg=status_msg,
                buttons=None,
                supports_streaming=False,
                force_document=False,
            )
            # Cleanup
            try:
                os.unlink(result)
                os.unlink(face_path)
                if os.path.exists(target_path):
                    os.unlink(target_path)
            except Exception:
                pass
        else:
            await status_msg.edit(f"❌ Face Swap ناموفق: {result}")
            # Cleanup
            try:
                os.unlink(face_path)
            except Exception:
                pass
    except Exception as e:
        logger.error(f"[FaceSwap] Error: {e}", exc_info=True)
        await status_msg.edit(f"❌ خطا: {e}")

    faceswap_sessions.pop(fs_session_id, None)
    return True


ai_sessions: dict = {}


async def ai_command(event):
    """نمایش لیست ابزارهای AI."""
    await event.reply(
        "🤖 **AI Tools**\n\nیکی از ابزارها رو انتخاب کن:",
        buttons=[
            [Button.inline("🎨 Image Generator", f"aisel_img_{event.chat_id}_{event.id}")],
        ],
        parse_mode="md",
    )


async def ai_select_callback(event):
    """وقتی کاربر یه ابزار AI انتخاب می‌کنه."""
    data = event.data.decode()
    session_id = data.replace("aisel_", "")
    # Create AI session with default settings
    ai_sessions[session_id] = {
        "count": 1,
        "style": "none",
        "shape": "square",
        "chat_id": event.chat_id,
    }
    await event.answer()
    await ai_show_settings(event, session_id)


async def ai_show_settings(event, session_id):
    """نمایش تنظیمات Image Generator با دکمه‌های قابل کلیک."""
    state = ai_sessions.get(session_id)
    if not state:
        await event.edit("⏰ نشست منقضی شده. /ai رو دوباره بزن.")
        return

    count = state.get("count", 1)
    style = state.get("style", "none")
    shape = state.get("shape", "square")
    quality = state.get("quality", "hd")

    # Build inline buttons
    # Row 1: Count buttons
    count_buttons = []
    for c in range(1, AI_MAX_IMAGES + 1):
        label = f"✅ {c}" if c == count else str(c)
        count_buttons.append(Button.inline(label, f"aicount_{session_id}_{c}"))

    # Row 2: Shape buttons
    shape_labels = {"square": "⬜ Square", "portrait": "📱 Portrait", "landscape": "🖥 Landscape"}
    shape_buttons = []
    for s_key, s_label in shape_labels.items():
        label = f"✅ {s_label}" if s_key == shape else s_label
        shape_buttons.append(Button.inline(label, f"aishape_{session_id}_{s_key}"))

    # Row 3: Style + Quality buttons
    style_display = style if style != "none" else "None"
    quality_display = "✅ HD" if quality == "hd" else "Standard"
    config_buttons = [
        Button.inline(f"🎨 Style: {style_display}", f"aistyle_{session_id}_0"),
        Button.inline(f"💎 {quality_display}", f"aiqual_{session_id}_{'standard' if quality == 'hd' else 'hd'}"),
    ]

    # Row 4: Generate button
    gen_buttons = [Button.inline("🚀 Generate", f"aigen_{session_id}")]

    await event.edit(
        f"🎨 **Image Generator**\n\n"
        f"🖼 تعداد: {count}\n"
        f"📐 شکل: {shape}\n"
        f"🎨 استایل: {style_display}\n"
        f"💎 کیفیت: {quality.upper()}\n\n"
        f"حالا prompt خودت رو بفرست، یا تنظیمات رو تغییر بده:",
        buttons=[
            count_buttons,
            shape_buttons,
            config_buttons,
            gen_buttons,
        ],
        parse_mode="md",
    )


async def ai_count_callback(event):
    """تغییر تعداد تصاویر."""
    data = event.data.decode()
    parts = data.split("_")
    count = int(parts[-1])
    session_id = "_".join(parts[1:-1])
    state = ai_sessions.get(session_id)
    if not state:
        await event.answer("⏰ نشست منقضی شده.", alert=True)
        return
    state["count"] = count
    await event.answer()
    await ai_show_settings(event, session_id)


async def ai_shape_callback(event):
    """تغییر شکل تصویر."""
    data = event.data.decode()
    parts = data.split("_")
    shape = parts[-1]
    session_id = "_".join(parts[1:-1])
    state = ai_sessions.get(session_id)
    if not state:
        await event.answer("⏰ نشست منقضی شده.", alert=True)
        return
    state["shape"] = shape
    await event.answer()
    await ai_show_settings(event, session_id)


async def ai_quality_callback(event):
    """تغییر کیفیت تصویر (Standard/HD)."""
    data = event.data.decode()
    parts = data.split("_")
    quality = parts[-1]  # "hd" or "standard"
    session_id = "_".join(parts[1:-1])
    state = ai_sessions.get(session_id)
    if not state:
        await event.answer("⏰ نشست منقضی شده.", alert=True)
        return
    state["quality"] = quality
    await event.answer()
    await ai_show_settings(event, session_id)


async def ai_style_callback(event):
    """نمایش لیست استایل‌ها."""
    data = event.data.decode()
    parts = data.split("_")
    page = int(parts[-1]) if len(parts) > 2 else 0
    session_id = "_".join(parts[1:-1])
    state = ai_sessions.get(session_id)
    if not state:
        await event.answer("⏰ نشست منقضی شده.", alert=True)
        return

    await event.answer()
    current_style = state.get("style", "none")
    per_page = 12
    start = page * per_page
    end = min(start + per_page, len(ART_STYLES))
    total_pages = (len(ART_STYLES) - 1) // per_page + 1

    buttons = []
    row = []
    for i in range(start, end):
        style = ART_STYLES[i]
        label = f"✅ {style}" if style == current_style else style
        row.append(Button.inline(label[:20], f"aistyle_{session_id}_{i}_sel"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # Navigation
    nav = []
    if page > 0:
        nav.append(Button.inline("⬅️ قبل", f"aistyle_{session_id}_{page-1}"))
    nav.append(Button.inline(f"📄 {page+1}/{total_pages}", "noop_"))
    if end < len(ART_STYLES):
        nav.append(Button.inline("بعد ➡️", f"aistyle_{session_id}_{page+1}"))
    if nav:
        buttons.append(nav)

    await event.edit(
        f"🎨 **استایل‌ها** (صفحه {page+1}/{total_pages})\n\n"
        f"استایل فعلی: {current_style}\n\nیکی رو انتخاب کن:",
        buttons=buttons,
        parse_mode="md",
    )

    # Handle style selection via separate pattern
    # We need to handle the _sel suffix


async def ai_style_select_callback(event):
    """انتخاب استایل یا تغییر صفحه استایل‌ها."""
    data = event.data.decode()
    # format: aistyle_{session_id}_{index}_sel  (style selection)
    #     or: aistyle_{session_id}_{page}       (page navigation)
    parts = data.split("_")

    if parts[-1] == "sel":
        # Style selection: aistyle_{session_id}_{index}_sel
        index = int(parts[-2])
        session_id = "_".join(parts[1:-2])
        state = ai_sessions.get(session_id)
        if not state:
            await event.answer("⏰ نشست منقضی شده.", alert=True)
            return
        if 0 <= index < len(ART_STYLES):
            state["style"] = ART_STYLES[index]
            await event.answer()
            await ai_show_settings(event, session_id)
    else:
        # Page navigation: aistyle_{session_id}_{page}
        page = int(parts[-1])
        session_id = "_".join(parts[1:-1])
        state = ai_sessions.get(session_id)
        if not state:
            await event.answer("⏰ نشست منقضی شده.", alert=True)
            return

        await event.answer()
        current_style = state.get("style", "none")
        per_page = 12
        start = page * per_page
        end = min(start + per_page, len(ART_STYLES))
        total_pages = (len(ART_STYLES) - 1) // per_page + 1

        buttons = []
        row = []
        for i in range(start, end):
            style_name = ART_STYLES[i]
            label = f"✅ {style_name}" if style_name == current_style else style_name
            row.append(Button.inline(label[:20], f"aistyle_{session_id}_{i}_sel"))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        nav = []
        if page > 0:
            nav.append(Button.inline("⬅️ قبل", f"aistyle_{session_id}_{page-1}"))
        nav.append(Button.inline(f"📄 {page+1}/{total_pages}", "noop_"))
        if end < len(ART_STYLES):
            nav.append(Button.inline("بعد ➡️", f"aistyle_{session_id}_{page+1}"))
        if nav:
            buttons.append(nav)

        await event.edit(
            f"🎨 **استایل‌ها** (صفحه {page+1}/{total_pages})\n\n"
            f"استایل فعلی: {current_style}\n\nیکی رو انتخاب کن:",
            buttons=buttons,
            parse_mode="md",
        )


async def ai_generate_callback(event):
    """شروع تولید تصویر - درخواست prompt از کاربر."""
    data = event.data.decode()
    session_id = data.replace("aigen_", "")
    state = ai_sessions.get(session_id)
    if not state:
        await event.answer("⏰ نشست منقضی شده.", alert=True)
        return

    # تنظیم user_state برای دریافت prompt
    user_state[event.chat_id] = {
        "action": "wait_for_ai_prompt",
        "ai_session_id": session_id,
    }

    await event.answer()
    await event.edit(
        "🎨 **Image Generator**\n\n"
        "تنظیمات ذخیره شد!\n"
        f"🖼 تعداد: {state.get('count', 1)}\n"
        f"📐 شکل: {state.get('shape', 'square')}\n"
        f"🎨 استایل: {state.get('style', 'none')}\n\n"
        "حالا **prompt** خودت رو بفرست:\n"
        "مثال: `a beautiful sunset over mountains`",
        buttons=None,
        parse_mode="md",
    )


async def ocr_extract_callback(event):
    """استخراج متن از تصویر با کلیک روی دکمه شیشه‌ای."""
    data = event.data.decode()
    session_id = data.replace("ocrex_", "")
    state = ocr_sessions.get(session_id)
    if not state:
        await event.answer("⏰ نشست منقضی شده.", alert=True)
        return
    await event.answer("📖 استخراج متن شروع شد...", alert=False)

    image_path = state["image_path"]
    if not os.path.exists(image_path):
        await event.edit("❌ فایل تصویر پیدا نشد. دوباره عکس رو بفرست.")
        ocr_sessions.pop(session_id, None)
        return

    await event.edit("📖 در حال استخراج متن از تصویر... لطفاً صبر کن.", buttons=None)

    try:
        success, text = await extract_text_from_image(image_path)
        if success:
            # Clean up and send text
            if len(text) > 4000:
                # Split long text
                for i in range(0, len(text), 4000):
                    await event.reply(f"```\n{text[i:i+4000]}\n```", parse_mode="md")
            else:
                await event.edit(
                    f"✅ متن استخراج‌شده:\n\n```\n{text}\n```",
                    parse_mode="md",
                    buttons=None,
                )
        else:
            await event.edit(f"❌ {text}", buttons=None)
    except Exception as e:
        logger.error(f"[OCR] Callback error: {e}", exc_info=True)
        await event.edit(f"❌ خطا: {e}", buttons=None)
    finally:
        # Cleanup
        try:
            if os.path.exists(image_path):
                os.unlink(image_path)
        except Exception:
            pass
        ocr_sessions.pop(session_id, None)


async def comic_pdf_callback(event):
    """ساخت PDF از تصاویر کمیک."""
    data = event.data.decode()
    session_id = data.replace("cmpdf_", "")
    state = comic_sessions.get(session_id)
    if not state:
        await event.answer("⏰ نشست منقضی شده.", alert=True)
        return
    await event.answer("📄 شروع ساخت PDF...", alert=False)

    info = state["info"]
    site_key = state["site_key"]
    images = info.get("images", [])
    title = info.get("title", "comic")

    safe_title = re.sub(r'[<>:"/\\|?*]', "_", title)[:60]
    out_path = os.path.join(OUTPUT_FOLDER, f"{safe_title}.pdf")

    await event.edit(f"📄 در حال دانلود {len(images)} تصویر و ساخت PDF...", buttons=None)

    async def _progress_cb(done, total, current):
        if total and done % max(1, total // 10) == 0:
            try:
                await event.edit(f"📄 دانلود تصاویر: {done}/{total}", buttons=None)
            except Exception:
                pass

    try:
        result = await build_comic_pdf(images, out_path, site_key, progress_cb=_progress_cb)
        if not result or not os.path.exists(result):
            await event.edit("❌ ساخت PDF ناموفق بود.")
            comic_sessions.pop(session_id, None)
            return

        size_mb = os.path.getsize(result) / 1024 / 1024
        await event.edit(f"✅ PDF ساخته شد ({size_mb:.1f} MB)\n📤 در حال آپلود...")
        await send_file_with_progress(
            client=event.client,
            chat_id=event.chat_id,
            filepath=result,
            caption=f"📚 **{title[:80]}**\n🖼 {len(images)} تصویر\n💾 {size_mb:.1f} MB",
            status_msg=event,
            buttons=None,
            supports_streaming=False,
            force_document=True,
        )
        try:
            os.unlink(result)
        except Exception:
            pass
        comic_sessions.pop(session_id, None)
    except Exception as e:
        logger.error(f"[Comic] PDF error: {e}", exc_info=True)
        try:
            await event.edit(f"❌ خطا: {e}")
        except Exception:
            pass
        comic_sessions.pop(session_id, None)


async def comic_images_callback(event):
    """ارسال تک‌تک تصاویر کمیک."""
    data = event.data.decode()
    session_id = data.replace("cmimg_", "")
    state = comic_sessions.get(session_id)
    if not state:
        await event.answer("⏰ نشست منقضی شده.", alert=True)
        return
    await event.answer("🖼 شروع ارسال تصاویر...", alert=False)

    info = state["info"]
    site_key = state["site_key"]
    images = info.get("images", [])
    title = info.get("title", "comic")

    await event.edit(f"🖼 در حال دانلود {len(images)} تصویر...", buttons=None)

    # دانلود تصاویر
    import tempfile, shutil
    out_dir = tempfile.mkdtemp(prefix="comic_imgs_")
    try:
        from otherwebsiteshandler.comics_handler import download_comic_images
        paths = await download_comic_images(images, out_dir, site_key, max_concurrent=8)
        if not paths:
            await event.edit("❌ هیچ تصویری دانلود نشد.")
            return

        await event.edit(f"📤 در حال ارسال {len(paths)} تصویر...", buttons=None)

        # ارسال به‌صورت آلبوم‌های ۱۰ تایی
        BATCH_SIZE = 10
        sent_count = 0
        for batch_start in range(0, len(paths), BATCH_SIZE):
            batch = paths[batch_start:batch_start + BATCH_SIZE]
            caption = f"📚 **{title[:60]}**\n🖼 {batch_start + 1}-{batch_start + len(batch)}/{len(paths)}" if batch_start == 0 else ""
            try:
                await event.client.send_file(
                    event.chat_id, batch,
                    caption=caption, parse_mode="md",
                    force_document=False, silent=True,
                )
                sent_count += len(batch)
            except Exception as e:
                logger.error(f"[Comic] batch send error: {e}")
                # fallback: send one by one
                for p in batch:
                    try:
                        await event.client.send_file(event.chat_id, p, force_document=False, silent=True)
                        sent_count += 1
                    except Exception:
                        pass

        await event.edit(f"✅ {sent_count}/{len(paths)} تصویر ارسال شد.")
        comic_sessions.pop(session_id, None)
    finally:
        try:
            shutil.rmtree(out_dir, ignore_errors=True)
        except Exception:
            pass


async def comic_video_callback(event):
    """دانلود و ارسال ویدیوی کمیک."""
    data = event.data.decode()
    session_id = data.replace("cmvid_", "")
    state = comic_sessions.get(session_id)
    if not state:
        await event.answer("⏰ نشست منقضی شده.", alert=True)
        return
    await event.answer("🎬 شروع دانلود ویدیو...", alert=False)

    info = state["info"]
    site_key = state["site_key"]
    videos = info.get("videos", [])
    title = info.get("title", "comic")

    if not videos:
        await event.edit("❌ ویدیویی موجود نیست.")
        return

    safe_title = re.sub(r'[<>:"/\\|?*]', "_", title)[:60]
    out_path = os.path.join(OUTPUT_FOLDER, f"{safe_title}.mp4")

    await event.edit(f"🎬 در حال دانلود ویدیو...", buttons=None)

    async def _progress_cb(text):
        try:
            await event.edit(text[:200], buttons=None)
        except Exception:
            pass

    try:
        success, error, size = await download_comic_video(
            videos[0], out_path, site_key, _progress_cb
        )
        if not success or not os.path.exists(out_path):
            await event.edit(f"❌ دانلود ناموفق: {error[:200]}")
            return

        size_mb = os.path.getsize(out_path) / 1024 / 1024
        await event.edit(f"✅ ویدیو دانلود شد ({size_mb:.1f} MB)\n📤 در حال آپلود...")
        await send_file_with_progress(
            client=event.client,
            chat_id=event.chat_id,
            filepath=out_path,
            caption=f"🎬 **{title[:80]}**\n💾 {size_mb:.1f} MB",
            status_msg=event,
            buttons=None,
            supports_streaming=True,
        )
        try:
            os.unlink(out_path)
        except Exception:
            pass
        comic_sessions.pop(session_id, None)
    except Exception as e:
        logger.error(f"[Comic] video error: {e}", exc_info=True)
        try:
            await event.edit(f"❌ خطا: {e}")
        except Exception:
            pass


async def comic_select_callback(event):
    """وقتی کاربر از لیست سرچ یه کمیک انتخاب می‌کنه."""
    data = event.data.decode()
    # format: cmsel_{session_id}_{index}
    parts = data.split("_")
    # find the index (last part)
    index = int(parts[-1])
    session_id = "_".join(parts[1:-1])

    state = comic_sessions.get(session_id)
    if not state:
        await event.answer("⏰ نشست منقضی شده.", alert=True)
        return

    comics = state.get("comics", [])
    if index >= len(comics):
        await event.answer("❌ کمیک پیدا نشد.", alert=True)
        return

    comic_url, comic_title = comics[index]
    site_key = state["site_key"]

    await event.answer()
    await event.edit(f"📚 در حال دریافت: {comic_title[:60]}...", buttons=None)

    # استخراج اطلاعات کمیک انتخاب‌شده
    info = await extract_comic_info(comic_url, site_key)
    if not info:
        await event.edit("❌ کمیک پیدا نشد یا تصویری موجود نیست.")
        return

    # ذخیره state جدید برای این کمیک
    new_session_id = f"comic_{event.chat_id}_{event.id}_{int(time.time())}"
    comic_sessions[new_session_id] = {
        "url": comic_url,
        "info": info,
        "site_key": site_key,
        "chat_id": event.chat_id,
    }

    title = info.get("title", comic_title)
    images = info.get("images", [])
    videos = info.get("videos", [])

    buttons = []
    if images:
        buttons.append([Button.inline(f"📄 PDF ({len(images)} تصویر)", f"cmpdf_{new_session_id}")])
        buttons.append([Button.inline(f"🖼 تک‌تک تصاویر ({len(images)})", f"cmimg_{new_session_id}")])
    if videos:
        buttons.append([Button.inline(f"🎬 ویدیو ({len(videos)})", f"cmvid_{new_session_id}")])

    if not buttons:
        await event.edit("❌ هیچ تصویر یا ویدیویی پیدا نشد.")
        comic_sessions.pop(new_session_id, None)
        return

    await event.edit(
        f"📚 **{title[:80]}**\n"
        f"🖼 تصاویر: {len(images)}\n"
        f"🎬 ویدیو: {len(videos)}\n\n"
        f"یکی از گزینه‌ها رو انتخاب کن:",
        buttons=buttons,
        parse_mode="md",
    )


async def diycraft_cb_episode(event):
    """Handle episode selection for diycraft series"""
    data = event.data.decode()
    user_id = event.sender_id
    state = dc_states.get(user_id)
    if not state:
        await event.answer("نشست منقضی شده. دوباره لینک رو بفرست.", alert=True)
        return

    ep_num_str = data.replace("dcep_", "")
    ep_num = int(ep_num_str)
    episodes = state.get("episodes", {})
    if ep_num not in episodes:
        await event.answer("قسمت نامعتبر", alert=True)
        return

    ep_info = episodes[ep_num]
    ep_key = ep_info["key"]
    page_url = state.get("page_url", "")
    title = state.get("title", "Unknown")
    thumb = state.get("thumb", "")

    await event.edit(f"🎬 **{title}** - قسمت {ep_num}\n\n⏳ در حال دریافت لینک ویدیو...", parse_mode="md")

    # Extract video URL for this episode
    video_url = await extract_episode_video(page_url, ep_key)
    if not video_url:
        await event.edit("❌ لینک ویدیو پیدا نشد.", buttons=None)
        return

    await event.edit(f"🎬 **{title}** - قسمت {ep_num}\n\n⏳ در حال دانلود...", parse_mode="md")

    out_dir = os.path.join(OUTPUT_FOLDER, f"diycraft_{user_id}_{int(time.time())}")
    os.makedirs(out_dir, exist_ok=True)

    dl_id = f"dcep_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}

    video_path = await diycraft_download(
        video_url, out_dir,
        referer=page_url,
    )

    if not video_path or not os.path.exists(video_path):
        await event.edit("❌ دانلود ناموفق بود.", buttons=None)
        return

    size_mb = os.path.getsize(video_path) / 1024 / 1024
    await event.edit(f"✅ دانلود شد ({size_mb:.1f} MB)\n📤 در حال آپلود...", buttons=None)

    # Download thumbnail
    thumb_path = None
    if thumb:
        try:
            async with aiohttp.ClientSession(timeout=ClientTimeout(total=20)) as session:
                async with session.get(thumb) as resp:
                    if resp.status == 200:
                        thumb_path = os.path.join(out_dir, "thumb.jpg")
                        with open(thumb_path, "wb") as f:
                            f.write(await resp.read())
        except Exception:
            pass

    caption = f"🎬 **{title}** - قسمت {ep_num}\n💾 {size_mb:.1f} MB\n📀 diycraftsguide"

    await send_file_with_progress(
        client=event.client,
        chat_id=event.chat_id,
        filepath=video_path,
        caption=caption,
        status_msg=event,
        buttons=None,
        supports_streaming=True,
        thumb_filepath=thumb_path,
        ul_id=f"dcep_ul_{dl_id}",
    )
    active_downloads.pop(dl_id, None)
    dc_states.pop(user_id, None)


async def diycraft_cb_close(event):
    """Close diycraft series selection"""
    user_id = event.sender_id
    dc_states.pop(user_id, None)
    try:
        await event.delete()
    except Exception:
        await event.edit("🚫 بسته شد", buttons=None)


async def xnxx_inline_handler(event):
    try:
        if event.sender_id not in AUTHORIZED_USERS:
            await event.answer([], cache_time=60)
            return

        raw = event.text.strip() if event.text else ""
        logger.info(f"[INLINE] Raw: '{raw}' from {event.sender_id}")

        if len(raw) < 3:
            await event.answer([], cache_time=5)
            return

        # تشخیص منبع: ph:xxx → PornHub, xv:xxx → XVideos, ep:xxx → Eporner, xn:xxx → XNXX, imd:xxx → IMDB, wh:xxx → WhoresHub
        is_ph = raw.lower().startswith("ph:")
        is_xv = raw.lower().startswith("xv:")
        is_ep = raw.lower().startswith("ep:")
        is_xn = raw.lower().startswith("xn:")
        is_imd = raw.lower().startswith("imd:")
        is_iran = raw.lower().startswith("iran:")
        is_wh = raw.lower().startswith("wh:")
        if is_ph:
            inner = raw[3:].strip()
            parsed = parse_inline_query(inner)
            ph_sort = PH_SORT_MAP.get(parsed["sort"], "")
        elif is_xv:
            inner = raw[3:].strip()
            parsed = parse_inline_query(inner)
        elif is_ep:
            inner = raw[3:].strip()
            parsed = parse_inline_query(inner)
        elif is_xn:
            inner = raw[3:].strip()
            parsed = parse_inline_query(inner)
        elif is_wh:
            inner = raw[3:].strip()
            parsed = parse_wh_inline_query(inner)
        elif is_imd:
            inner = raw[4:].strip()
        elif is_iran:
            inner = raw[5:].strip()
        else:
            # بدون پیشوند — استفاده از سرچر دیفالت کاربر
            # اولویت با /setsearch هست — اگه کاربر صریحاً سرور PH/XV/... رو
            # انتخاب کرده، از همون استفاده می‌شه
            default_src = get_user_default_search(event.sender_id)
            iran_srv = user_iran_server.get(event.sender_id)

            if default_src and default_src != "xn":
                # کاربر با /setsearch سرور خاصی رو انتخاب کرده — اولویت با اونه
                inner = raw
                if default_src == "ph":
                    is_ph = True
                    parsed = parse_inline_query(inner)
                    ph_sort = PH_SORT_MAP.get(parsed["sort"], "")
                elif default_src == "xv":
                    is_xv = True
                    parsed = parse_inline_query(inner)
                elif default_src == "ep":
                    is_ep = True
                    parsed = parse_inline_query(inner)
                elif default_src == "imd":
                    is_imd = True
                elif default_src == "wh":
                    is_wh = True
                    parsed = parse_wh_inline_query(inner)
            elif iran_srv and iran_srv in ("doostihaa", "farsiland"):
                # کاربر /server رو روی سرور ایرانی گذاشته
                is_iran = True
                inner = raw
            else:
                # دیفالت: XNXX
                is_xn = True
                inner = raw
                parsed = parse_inline_query(inner)

        if is_imd:
            query = inner
            page = 1
            sort = ""
        elif is_iran:
            query = inner
            page = 1
            sort = ""
        else:
            query = parsed["query"]
            page = parsed["page"]
            sort = parsed["sort"]

        source = "IMDB" if is_imd else ("IRAN" if is_iran else ("EP" if is_ep else ("WH" if is_wh else ("XV" if is_xv else ("PH" if is_ph else "XNXX")))))
        logger.info(f"[INLINE] {source}: q='{query}' page={page} sort={sort}")

        if is_imd:
            results = await search_imdb(query, limit=INLINE_RESULTS_LIMIT)
        elif is_ph:
            if page == 0:
                results = await search_pornhub_multi_page(
                    query, pages=3, limit=INLINE_RESULTS_LIMIT, sort=ph_sort
                )
            else:
                results = await search_pornhub(
                    query, page=page, limit=INLINE_RESULTS_LIMIT, sort=ph_sort
                )
        elif is_xv:
            if page == 0:
                results = await search_xvideos_multi_page(
                    query, pages=3, limit=INLINE_RESULTS_LIMIT, sort=sort
                )
            else:
                results = await search_xvideos(
                    query, page=page, limit=INLINE_RESULTS_LIMIT, sort=sort
                )
        elif is_ep:
            results = await search_eporner(
                query, page=page, limit=INLINE_RESULTS_LIMIT, sort=sort
            )
        elif is_wh:
            if page == 0:
                results = await search_whoreshub_multi_page(
                    query, pages=3, limit=INLINE_RESULTS_LIMIT, sort=sort
                )
            else:
                results = await search_whoreshub(
                    query, page=page, limit=INLINE_RESULTS_LIMIT, sort=sort
                )
        else:  # XNXX
            if page == 0:
                results = await search_xnxx_multi_page(
                    query, pages=3, limit=INLINE_RESULTS_LIMIT
                )
            else:
                xnxx_page = max(0, page - 1)
                results = await search_xnxx(
                    query, page=xnxx_page, limit=INLINE_RESULTS_LIMIT, sort=sort
                )

        if not results:
            await event.answer([], cache_time=30)
            return

        if is_imd:
            imdb_results = []
            builder = event.builder
            for i, item in enumerate(results):
                title = item.get("title", "Untitled")[:128]
                imdb_id = item.get("imdb_id", "")
                year = item.get("year") or ""
                kind = item.get("kind") or ""
                stars = item.get("stars") or ""
                cover = item.get("cover") or ""
                is_series = item.get("is_series", False)

                # Build description with year, kind, stars
                desc_parts = []
                if year:
                    desc_parts.append(f"📅 {year}")
                if kind:
                    kind_label = kind
                    if kind == "feature":
                        kind_label = "🎬 فیلم"
                    elif kind == "TV series":
                        kind_label = "📺 سریال"
                    elif kind == "TV movie":
                        kind_label = "🎬 TV Movie"
                    elif kind == "TV mini-series":
                        kind_label = "📺 مینی‌سریال"
                    elif kind == "short":
                        kind_label = "🎬 کوتاه"
                    desc_parts.append(kind_label)
                if stars:
                    stars_short = stars[:60] + "..." if len(stars) > 60 else stars
                    desc_parts.append(f"👥 {stars_short}")
                description = " | ".join(desc_parts) or "—"

                # Build display title with year and type indicator
                type_icon = "📺" if is_series else "🎬"
                display_title = f"{type_icon} {title}"
                if year:
                    display_title += f" ({year})"

                message_text = (
                    f"🎬 **{title}**\n\n"
                    + (f"📅 Year: {year}\n" if year else "")
                    + (f"🎞 Kind: {kind}\n" if kind else "")
                    + (f"👥 Stars: {stars}\n" if stars else "")
                    + f"\n🔗 https://www.imdb.com/title/{imdb_id}/"
                )

                buttons = [
                    [
                        Button.inline(
                            "📥 دانلود" + (" 📺" if is_series else " 🎬"),
                            f"imd_sel_{imdb_id}",
                        )
                    ]
                ]

                try:
                    # Build article with thumbnail (InputWebDocument required by Telethon)
                    if cover:
                        try:
                            thumb_doc = InputWebDocument(
                                url=cover,
                                size=0,
                                mime_type="image/jpeg",
                                attributes=[]
                            )
                            imdb_results.append(
                                builder.article(
                                    title=display_title,
                                    description=description,
                                    thumb=thumb_doc,
                                    text=message_text,
                                    buttons=buttons,
                                    parse_mode="md",
                                    link_preview=False,
                                )
                            )
                        except Exception:
                            # Fallback: article without thumb
                            imdb_results.append(
                                builder.article(
                                    title=display_title,
                                    description=description,
                                    text=message_text,
                                    buttons=buttons,
                                    parse_mode="md",
                                    link_preview=False,
                                )
                            )
                    else:
                        imdb_results.append(
                            builder.article(
                                title=display_title,
                                description=description,
                                text=message_text,
                                buttons=buttons,
                                parse_mode="md",
                                link_preview=False,
                            )
                        )
                except Exception:
                    try:
                        imdb_results.append(
                            builder.article(
                                title=display_title,
                                description=description,
                                text=message_text,
                                buttons=buttons,
                                parse_mode="md",
                                link_preview=False,
                            )
                        )
                    except Exception:
                        continue

            await event.answer(
                imdb_results,
                cache_time=30,
            )
            logger.info(f"[INLINE] IMDB: {len(imdb_results)} results for '{query}'")
            return

        if is_iran:
            query = inner
            page = 1
            sort = ""
            logger.info(f"[INLINE] IRAN: q='{query}'")
            # Use doostihaa search
            from searcher.iranserver.doostihaa_search import search_doostihaa
            results = await search_doostihaa(query, limit=10)  # محدود کردن نتایج برای سرعت بیشتر
            if not results:
                await event.answer([], cache_time=30)
                return
            iran_results = []
            builder = event.builder
            for item in results:
                title = item.get("title", "Untitled")[:128]
                post_id = item.get("id", "")
                url = item.get("url", "")
                cover = item.get("img", "")
                is_series = item.get("is_series", False)
                type_icon = "\U0001F4FA" if is_series else "\U0001F3AC"
                display_title = f"{type_icon} {title}"
                desc = "\U0001F4C0 \u062F\u0648\u0633\u062A\u06CC\u200C\u0647\u0627 | \U0001F310 \u062F\u0648\u0633\u062A\u06CC\u200C\u0647\u0627"
                message_text = f"\U0001F3AC **{title}**\n\n\U0001F510 {url}"
                buttons = [[Button.inline("\U0001F4E5 \u062F\u0627\u0646\u0644\u0648\u062F", f"irn_sel_{post_id}")]]
                try:
                    if cover:
                        thumb_doc = InputWebDocument(url=cover, size=0, mime_type="image/jpeg", attributes=[])
                        iran_results.append(builder.article(title=display_title, description=desc, thumb=thumb_doc, text=message_text, buttons=buttons, parse_mode="md", link_preview=False))
                    else:
                        iran_results.append(builder.article(title=display_title, description=desc, text=message_text, buttons=buttons, parse_mode="md", link_preview=False))
                except Exception:
                    iran_results.append(builder.article(title=display_title, description=desc, text=message_text, buttons=buttons, parse_mode="md", link_preview=False))
            try:
                await event.answer(iran_results, cache_time=30)
            except Exception as ans_err:
                logger.warning(f"[INLINE] IRAN answer error: {ans_err}")
            logger.info(f"[INLINE] IRAN: {len(iran_results)} results for '{query}'")
            return



        inline_results = []
        builder = event.builder
        for i, video in enumerate(results):
            title = video.get("title", "Untitled")[:128]
            url = video.get("url", "")
            thumb_url = video.get("thumbnail", "")
            duration = video.get("duration", "?")
            views = video.get("views", "?")

            if is_ph:
                quality = video.get("rating", "")
                hd_tag = " 📺 HD" if video.get("hd") else ""
                source_tag = "PH"
            elif is_xv:
                quality = video.get("quality", "")
                hd_tag = ""
                source_tag = "XV"
            elif is_ep:
                quality = video.get("quality", "")
                hd_tag = f" | 🏆 {video.get('rating', '')}" if video.get("rating") else ""
                source_tag = "EP"
            elif is_wh:
                quality = video.get("quality", "")
                hd_tag = ""
                source_tag = "WH"
            else:
                quality = video.get("quality", "")
                hd_tag = ""
                source_tag = "XNXX"

            description = f"⏱ {duration}"
            if views:
                description += f" | 👁 {views}"
            if quality:
                description += f" | 🎚 {quality}"
            if hd_tag:
                description += hd_tag

            message_text = (
                f"🎬 **{title}**\n\n"
                f"⏱ Duration: {duration}\n"
                f"👁 Views: {views}\n"
                f"🎚 Quality: {quality}\n"
                f"📌 Source: {source_tag}\n\n"
                f"🔗 {url}"
            )

            thumb = None
            if thumb_url:
                thumb = InputWebDocument(
                    url=thumb_url,
                    size=0,
                    mime_type="image/jpeg",
                    attributes=[DocumentAttributeImageSize(w=320, h=180)],
                )

            pick_token = _store_inline_pick(url)
            inline_results.append(
                builder.article(
                    title=title,
                    description=description,
                    url=url,
                    thumb=thumb,
                    text=message_text,
                    buttons=[[Button.inline("⚡️ شروع دانلود", f"inl_{pick_token}")]],
                    parse_mode="md",
                    id=str(i),
                )
            )

        await event.answer(
            inline_results,
            cache_time=30,
        )
        logger.info(
            f"[INLINE] {source}: {len(inline_results)} results for '{query}'"
        )

    except Exception as e:
        logger.error(f"[INLINE] Error: {e}", exc_info=True)
        try:
            await event.answer([], cache_time=5)
        except Exception:
            pass


async def inline_start_callback(event):
    """وقتی کاربر روی «⚡️ شروع دانلود» می‌زند، همان لینک به‌صورت یک پیام
    داخل ربات ارسال می‌شود و جریان عادی لینک شروع می‌شود (ربات با
    دکمه‌های کیفیت جواب می‌دهد) — دقیقاً مثل اینکه کاربر خودش لینک را
    فرستاده باشد.

    نکته: از get_message استفاده نمی‌کنیم چون پیام‌های ارسالی از طریق
    اینلاین آیدی متفاوتی در چت می‌گیرند و برای ربات قابل fetch نیستند؛
    به جایش URL در زمان ساخت نتیجه در inline_pick_urls ذخیره شده و
    توکن آن داخل دیتای دکمه قرار دارد.
    """
    if event.sender_id not in AUTHORIZED_USERS:
        await event.answer("⛔ Unauthorized", alert=True)
        return
    token = event.data.decode().replace("inl_", "")
    entry = inline_pick_urls.get(token)
    if not entry:
        await event.answer("❌ لینک منقضی شد. دوباره از اینلاین انتخاب کن.", alert=True)
        return
    link = entry["url"]
    await event.answer("⚡️ ارسال شد", alert=False)
    try:
        sent = await event.client.send_message(event.chat_id, link)
    except Exception as e:
        logger.error(f"[INLINE-START] send failed: {e}")
        await event.answer("❌ ارسال لینک ناموفق بود", alert=True)
        return
    sent._inline_auto = True
    try:
        await generic_url_handler(sent)
    except Exception as e:
        logger.error(f"[INLINE-START] Error: {e}", exc_info=True)


# ====================== YT-DLP GENERIC HANDLER ======================


async def process_ytdlp_request(event, url: str, status_msg):
    qualities, title = await extract_qualities_ytdlp(url)
    if not qualities:
        err_detail = f" — `{title[:150]}`" if title else ""
        await safe_edit(status_msg, f"❌ کیفیتی پیدا نشد{err_detail}")
        return
    session_id = f"ytdlp_{event.chat_id}_{event.id}_{int(time.time())}"
    ytdlp_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
    }
    title_display = title[:60] if title else "ویدیو"
    site_name = get_site_name(url).upper()
    text = f"🎬 **{title_display}**\n🌐 {site_name}\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"ytdlp_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"ytdlp_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def ytdlp_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in ytdlp_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = ytdlp_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "video"
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "video"
    filename = f"{safe_title}_{int(time.time())}.mp4"
    filepath = os.path.join(OUTPUT_FOLDER, filename)
    dl_id = f"ytdlp_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, size = await download_with_ytdlp(
            entry["url"], chosen["format_id"], filepath, progress_cb
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success or not os.path.exists(filepath) or size < 1024:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return
        ul_id = f"ytdlp_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = (
            f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(size)}"
        )
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[YTDLP] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def ytdlp_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("ytdlp_cancel_", "")
    ytdlp_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ====================== DEDICATED SITE HANDLER (XVideos / XGroovy) ======================


def _make_site_handler(
    prefix,
    extract_fn,
    download_direct_fn,
    download_m3u8_fn,
    sessions_dict,
    display_name,
):
    """ساخت handler های process, quality_callback, cancel_callback برای یه سایت خاص."""

    async def process_request(event, url: str, status_msg):
        qualities, title = await extract_fn(url)
        if not qualities:
            err_detail = f" — `{title[:150]}`" if title else ""
            await safe_edit(status_msg, f"❌ کیفیتی پیدا نشد{err_detail}")
            return
        session_id = f"{prefix}_{event.chat_id}_{event.id}_{int(time.time())}"
        sessions_dict[session_id] = {
            "url": url,
            "title": title,
            "qualities": qualities,
            "chat_id": event.chat_id,
        }
        title_display = title[:60] if title else f"ویدیو {display_name}"
        text = f"🎬 **{title_display}**\n🌐 {display_name}\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
        buttons = []
        for i, q in enumerate(qualities):
            buttons.append([Button.inline(q["label"], f"{prefix}_q_{session_id}_{i}")])
        buttons.append([Button.inline("❌ لغو", f"{prefix}_cancel_{session_id}")])
        await safe_edit(status_msg, text, buttons=buttons)

    async def quality_callback(event):
        data = event.data.decode()
        parts = data.split("_")
        quality_index = int(parts[-1])
        session_id = "_".join(parts[2:-1])
        if session_id not in sessions_dict:
            await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
            return
        entry = sessions_dict.pop(session_id)
        qualities = entry["qualities"]
        title = entry["title"] or f"{display_name}_video"
        if quality_index >= len(qualities):
            await event.answer("❌ خطا", alert=True)
            return
        chosen = qualities[quality_index]
        await event.answer(f"✅ {chosen['label']}", alert=False)
        safe_title = (
            re.sub(r"[^\w\s\-]", "", title)[:60].strip() or f"{display_name}_video"
        )
        filename = f"{safe_title}_{int(time.time())}.mp4"
        filepath = os.path.join(OUTPUT_FOLDER, filename)

        dl_id = f"{prefix}_dl_{event.chat_id}_{event.id}_{int(time.time())}"
        active_downloads[dl_id] = {"paused": False, "cancelled": False}
        cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

        try:
            await event.edit(
                f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
                buttons=cancel_btn,
            )
        except Exception:
            pass
        status_msg = await event.get_message()

        async def progress_cb(text):
            if active_downloads.get(dl_id, {}).get("cancelled"):
                raise asyncio.CancelledError("Download cancelled by user")
            try:
                await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
            except Exception:
                pass

        try:
            if chosen["method"] == "direct":
                success, error, size = await download_direct_fn(
                    chosen["url"], filepath, progress_cb
                )
            else:
                success, error, size = await download_m3u8_fn(
                    chosen["url"], filepath, progress_cb
                )
            if active_downloads.get(dl_id, {}).get("cancelled"):
                raise asyncio.CancelledError("Download cancelled by user")
            if not success or not os.path.exists(filepath) or size < 1024:
                err_msg = error or "Unknown error"
                await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
                return
            ul_id = f"{prefix}_ul_{event.chat_id}_{event.id}_{int(time.time())}"
            await safe_edit(status_msg, "📤 **در حال آپلود...**")
            caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(size)}"
            await send_file_with_progress(
                client=event.client,
                chat_id=entry["chat_id"],
                filepath=filepath,
                caption=caption,
                status_msg=status_msg,
                buttons=None,
                supports_streaming=True,
                ul_id=ul_id,
            )
        except asyncio.CancelledError:
            try:
                await status_msg.edit("🚫 **Cancelled.**", buttons=None)
            except Exception:
                pass
        except Exception as e:
            logger.error(f"[{display_name}] Error: {e}", exc_info=True)
            await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
        finally:
            active_downloads.pop(dl_id, None)
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except Exception:
                pass

    async def cancel_callback(event):
        data = event.data.decode()
        session_id = data.replace(f"{prefix}_cancel_", "")
        sessions_dict.pop(session_id, None)
        await event.answer("❌ لغو شد", alert=False)
        try:
            await event.edit("❌ **لغو شد.**", buttons=None)
        except Exception:
            pass

    return process_request, quality_callback, cancel_callback


process_xvideos_request, xvideos_quality_callback, xvideos_cancel_callback = (
    _make_site_handler(
        "xv",
        extract_xvideos_qualities,
        download_xvideos_direct,
        download_xvideos_m3u8,
        xvideos_sessions,
        "XVideos",
    )
)

process_xgroovy_request, xgroovy_quality_callback, xgroovy_cancel_callback = (
    _make_site_handler(
        "xg",
        extract_xgroovy_qualities,
        download_xgroovy_direct,
        download_xgroovy_m3u8,
        xgroovy_sessions,
        "XGroovy",
    )
)

(
    process_teensexvideos_request,
    teensexvideos_quality_callback,
    teensexvideos_cancel_callback,
) = _make_site_handler(
    "tsv",
    extract_teensexvideos_qualities,
    download_teensexvideos_direct,
    download_teensexvideos_m3u8,
    teensexvideos_sessions,
    "TeenSexVideos",
)

(
    process_usersporn_request,
    usersporn_quality_callback,
    usersporn_cancel_callback,
) = _make_site_handler(
    "up",
    extract_usersporn_qualities,
    download_usersporn_direct,
    download_usersporn_m3u8,
    usersporn_sessions,
    "UsersPorn",
)

(
    process_hentaihaven_request,
    hentaihaven_quality_callback,
    hentaihaven_cancel_callback,
) = _make_site_handler(
    "hh",
    extract_hentaihaven_qualities,
    download_hentaihaven_direct,
    download_hentaihaven_m3u8,
    hentaihaven_sessions,
    "HentaiHaven",
)

(
    process_rat_request,
    rat_quality_callback,
    rat_cancel_callback,
) = _make_site_handler(
    "rat",
    extract_rat_qualities,
    download_rat_direct,
    download_rat_m3u8,
    rat_sessions,
    "Rat",
)

(
    process_sexvid_request,
    sexvid_quality_callback,
    sexvid_cancel_callback,
) = _make_site_handler(
    "sx",
    extract_sexvid_qualities,
    download_sexvid_direct,
    download_sexvid_m3u8,
    sexvid_sessions,
    "Sexvid",
)

(
    process_tube8_request,
    tube8_quality_callback,
    tube8_cancel_callback,
) = _make_site_handler(
    "tb",
    extract_tube8_qualities,
    download_tube8_direct,
    download_tube8_m3u8,
    tube8_sessions,
    "Tube8",
)

(
    process_redtube_request,
    redtube_quality_callback,
    redtube_cancel_callback,
) = _make_site_handler(
    "rt",
    extract_redtube_qualities,
    download_redtube_direct,
    download_redtube_m3u8,
    redtube_sessions,
    "RedTube",
)

hohoj_sessions: dict = {}

(
    process_hohoj_request,
    hohoj_quality_callback,
    hohoj_cancel_callback,
) = _make_site_handler(
    "hj",
    extract_hohoj_qualities,
    download_hohoj_direct,
    download_hohoj_m3u8,
    hohoj_sessions,
    "Hohoj",
)

porna91_sessions: dict = {}

(
    process_91porna_request,
    porna91_quality_callback,
    porna91_cancel_callback,
) = _make_site_handler(
    "p91",
    extract_91porna_qualities,
    download_91porna_direct,
    download_91porna_m3u8,
    porna91_sessions,
    "91Porna",
)

playvids_sessions: dict = {}

(
    process_playvids_request,
    playvids_quality_callback,
    playvids_cancel_callback,
) = _make_site_handler(
    "pv",
    extract_playvids_qualities,
    download_playvids_direct,
    download_playvids_m3u8,
    playvids_sessions,
    "Playvids",
)

porn300_sessions: dict = {}

(
    process_porn300_request,
    porn300_quality_callback,
    porn300_cancel_callback,
) = _make_site_handler(
    "p3",
    extract_porn300_qualities,
    download_porn300_direct,
    download_porn300_m3u8,
    porn300_sessions,
    "Porn300",
)

tnaflix_sessions: dict = {}

(
    process_tnaflix_request,
    tnaflix_quality_callback,
    tnaflix_cancel_callback,
) = _make_site_handler(
    "tf",
    extract_tnaflix_qualities,
    download_tnaflix_direct,
    download_tnaflix_m3u8,
    tnaflix_sessions,
    "Tnaflix",
)

eporner_sessions: dict = {}

(
    process_eporner_request,
    eporner_quality_callback,
    eporner_cancel_callback,
) = _make_site_handler(
    "ep",
    extract_eporner_qualities,
    download_eporner_direct,
    download_eporner_m3u8,
    eporner_sessions,
    "Eporner",
)

pornzog_sessions: dict = {}


async def process_pornzog_request(event, url: str, status_msg):
    debug_lines: list[str] = []

    def debug_cb(msg: str) -> None:
        debug_lines.append(msg)

    qualities, title = await extract_pornzog_qualities(url, debug_cb=debug_cb)
    if not qualities:
        debug_text = "\n".join(debug_lines[-30:])
        err_detail = f" — `{title[:150]}`" if title else ""
        await safe_edit(status_msg, f"❌ کیفیتی پیدا نشد{err_detail}")
        try:
            await event.client.send_message(
                event.chat_id,
                f"🔬 **Pornzog Debug Log:**\n```\n{debug_text[:3500]}\n```",
                parse_mode="markdown",
            )
        except Exception as e:
            logger.warning("Failed to send debug msg: %s", e)
        return
    session_id = f"pz_{event.chat_id}_{event.id}_{int(time.time())}"
    pornzog_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
    }
    title_display = title[:60] if title else "ویدیو Pornzog"
    text = f"🎬 **{title_display}**\n🌐 Pornzog\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"pz_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"pz_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


(
    pornzog_quality_callback,
    pornzog_cancel_callback,
) = _make_site_handler(
    "pz",
    extract_pornzog_qualities,
    download_pornzog_direct,
    download_pornzog_m3u8,
    pornzog_sessions,
    "Pornzog",
)[1:]

# ─── New site handlers (27 sites) ──────────────────────────
# هر سایت یه prefix منحصر به فرد داره (هیچ تداخلی با prefix‌های موجود نداره)

# KVS-based sites (6 sites)
process_hellporno_request, hellporno_quality_callback, hellporno_cancel_callback = _make_site_handler(
    "hpo", extract_hellporno_qualities, download_hellporno_direct, download_hellporno_m3u8,
    hellporno_sessions, "HellPorno",
)
process_alphaporno_request, alphaporno_quality_callback, alphaporno_cancel_callback = _make_site_handler(
    "apo", extract_alphaporno_qualities, download_alphaporno_direct, download_alphaporno_m3u8,
    alphaporno_sessions, "AlphaPorno",
)
process_bravoteens_request, bravoteens_quality_callback, bravoteens_cancel_callback = _make_site_handler(
    "bte", extract_bravoteens_qualities, download_bravoteens_direct, download_bravoteens_m3u8,
    bravoteens_sessions, "BravoTeens",
)
process_bravotube_request, bravotube_quality_callback, bravotube_cancel_callback = _make_site_handler(
    "btu", extract_bravotube_qualities, download_bravotube_direct, download_bravotube_m3u8,
    bravotube_sessions, "BravoTube",
)
process_crocotube_request, crocotube_quality_callback, crocotube_cancel_callback = _make_site_handler(
    "ctu", extract_crocotube_qualities, download_crocotube_direct, download_crocotube_m3u8,
    crocotube_sessions, "CrocoTube",
)
process_porngo_request, porngo_quality_callback, porngo_cancel_callback = _make_site_handler(
    "pgo", extract_porngo_qualities, download_porngo_direct, download_porngo_m3u8,
    porngo_sessions, "PornGo",
)

# Txxx network (5 sites)
process_txxx_request, txxx_quality_callback, txxx_cancel_callback = _make_site_handler(
    "txx", extract_txxx_qualities, download_txxx_direct, download_txxx_m3u8,
    txxx_sessions, "Txxx",
)
process_hclips_request, hclips_quality_callback, hclips_cancel_callback = _make_site_handler(
    "hcl", extract_hclips_qualities, download_hclips_direct, download_hclips_m3u8,
    hclips_sessions, "HClips",
)
process_upornia_request, upornia_quality_callback, upornia_cancel_callback = _make_site_handler(
    "upn2", extract_upornia_qualities, download_upornia_direct, download_upornia_m3u8,
    upornia_sessions, "Upornia",
)
process_vjav_request, vjav_quality_callback, vjav_cancel_callback = _make_site_handler(
    "vja", extract_vjav_qualities, download_vjav_direct, download_vjav_m3u8,
    vjav_sessions, "VJAV",
)
process_hdzog_request, hdzog_quality_callback, hdzog_cancel_callback = _make_site_handler(
    "hdz2", extract_hdzog_qualities, download_hdzog_direct, download_hdzog_m3u8,
    hdzog_sessions, "HDzog",
)

# DrTuber (custom API extraction)
process_drtuber_request, drtuber_quality_callback, drtuber_cancel_callback = _make_site_handler(
    "drt", extract_drtuber_qualities, download_drtuber_direct, download_drtuber_m3u8,
    drtuber_sessions, "DrTuber",
)

# PornTop (KVS + yt-dlp fallback)
process_porntop_request, porntop_quality_callback, porntop_cancel_callback = _make_site_handler(
    "ptp2", extract_porntop_qualities, download_porntop_direct, download_porntop_m3u8,
    porntop_sessions, "PornTop",
)

# Generic yt-dlp fallback (14 sites)
process_pornone_request, pornone_quality_callback, pornone_cancel_callback = _make_site_handler(
    "pon", extract_pornone_qualities, download_pornone_direct, download_pornone_m3u8,
    pornone_sessions, "PornOne",
)
process_pornhd_request, pornhd_quality_callback, pornhd_cancel_callback = _make_site_handler(
    "phd2", extract_pornhd_qualities, download_pornhd_direct, download_pornhd_m3u8,
    pornhd_sessions, "PornHD",
)
process_xtube_request, xtube_quality_callback, xtube_cancel_callback = _make_site_handler(
    "xtu", extract_xtube_qualities, download_xtube_direct, download_xtube_m3u8,
    xtube_sessions, "xTube",
)
process_mofosex_request, mofosex_quality_callback, mofosex_cancel_callback = _make_site_handler(
    "mfs2", extract_mofosex_qualities, download_mofosex_direct, download_mofosex_m3u8,
    mofosex_sessions, "MofoSex",
)
process_fapvid_request, fapvid_quality_callback, fapvid_cancel_callback = _make_site_handler(
    "fpv", extract_fapvid_qualities, download_fapvid_direct, download_fapvid_m3u8,
    fapvid_sessions, "FapVid",
)
process_monsterporn_request, monsterporn_quality_callback, monsterporn_cancel_callback = _make_site_handler(
    "mst", extract_monsterporn_qualities, download_monsterporn_direct, download_monsterporn_m3u8,
    monsterporn_sessions, "MonsterPorn",
)
process_fetishkitsch_request, fetishkitsch_quality_callback, fetishkitsch_cancel_callback = _make_site_handler(
    "ftk", extract_fetishkitsch_qualities, download_fetishkitsch_direct, download_fetishkitsch_m3u8,
    fetishkitsch_sessions, "FetishKitsch",
)
process_javhihi_request, javhihi_quality_callback, javhihi_cancel_callback = _make_site_handler(
    "jhh", extract_javhihi_qualities, download_javhihi_direct, download_javhihi_m3u8,
    javhihi_sessions, "JAVHiHi",
)
process_tokyoporn_request, tokyoporn_quality_callback, tokyoporn_cancel_callback = _make_site_handler(
    "tkp", extract_tokyoporn_qualities, download_tokyoporn_direct, download_tokyoporn_m3u8,
    tokyoporn_sessions, "TokyoPorn",
)
process_javwhores_request, javwhores_quality_callback, javwhores_cancel_callback = _make_site_handler(
    "jwh", extract_javwhores_qualities, download_javwhores_direct, download_javwhores_m3u8,
    javwhores_sessions, "JAVWhores",
)
process_goodporn_request, goodporn_quality_callback, goodporn_cancel_callback = _make_site_handler(
    "gdp", extract_goodporn_qualities, download_goodporn_direct, download_goodporn_m3u8,
    goodporn_sessions, "GoodPorn",
)
process_porn365_request, porn365_quality_callback, porn365_cancel_callback = _make_site_handler(
    "p3652", extract_porn365_qualities, download_porn365_direct, download_porn365_m3u8,
    porn365_sessions, "Porn365",
)
process_fapcake_request, fapcake_quality_callback, fapcake_cancel_callback = _make_site_handler(
    "fpc", extract_fapcake_qualities, download_fapcake_direct, download_fapcake_m3u8,
    fapcake_sessions, "FapCup",
)
process_fux_request, fux_quality_callback, fux_cancel_callback = _make_site_handler(
    "fux", extract_fux_qualities, download_fux_direct, download_fux_m3u8,
    fux_sessions, "Fux",
)


# Helper: list of (is_url_fn, process_fn, log_name) for fast URL dispatch
NEW_SITE_HANDLERS = [
    (is_hellporno_url, process_hellporno_request, "HellPorno"),
    (is_alphaporno_url, process_alphaporno_request, "AlphaPorno"),
    (is_bravoteens_url, process_bravoteens_request, "BravoTeens"),
    (is_bravotube_url, process_bravotube_request, "BravoTube"),
    (is_crocotube_url, process_crocotube_request, "CrocoTube"),
    (is_porngo_url, process_porngo_request, "PornGo"),
    (is_txxx_url, process_txxx_request, "Txxx"),
    (is_hclips_url, process_hclips_request, "HClips"),
    (is_upornia_url, process_upornia_request, "Upornia"),
    (is_vjav_url, process_vjav_request, "VJAV"),
    (is_hdzog_url, process_hdzog_request, "HDzog"),
    (is_drtuber_url, process_drtuber_request, "DrTuber"),
    (is_porntop_url, process_porntop_request, "PornTop"),
    (is_pornone_url, process_pornone_request, "PornOne"),
    (is_pornhd_url, process_pornhd_request, "PornHD"),
    (is_xtube_url, process_xtube_request, "xTube"),
    (is_mofosex_url, process_mofosex_request, "MofoSex"),
    (is_fapvid_url, process_fapvid_request, "FapVid"),
    (is_monsterporn_url, process_monsterporn_request, "MonsterPorn"),
    (is_fetishkitsch_url, process_fetishkitsch_request, "FetishKitsch"),
    (is_javhihi_url, process_javhihi_request, "JAVHiHi"),
    (is_tokyoporn_url, process_tokyoporn_request, "TokyoPorn"),
    (is_javwhores_url, process_javwhores_request, "JAVWhores"),
    (is_goodporn_url, process_goodporn_request, "GoodPorn"),
    (is_porn365_url, process_porn365_request, "Porn365"),
    (is_fapcake_url, process_fapcake_request, "FapCup"),
    (is_fux_url, process_fux_request, "Fux"),
]

# ─── XXXBP (custom handlers: needs page_url + video_url) ───

xxxbp_sessions: dict = {}


async def process_xxxbp_request(event, url: str, status_msg):
    qualities, title, info = await extract_xxxbp_qualities(url)
    if not qualities:
        err_detail = f" — `{title[:150]}`" if title else ""
        await safe_edit(status_msg, f"❌ کیفیتی پیدا نشد{err_detail}")
        return
    session_id = f"xb_{event.chat_id}_{event.id}_{int(time.time())}"
    xxxbp_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "info": info,
        "chat_id": event.chat_id,
    }
    title_display = title[:60] if title else "ویدیو XXXBP"
    text = f"🎬 **{title_display}**\n🌐 XXXBP\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"xb_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"xb_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def xxxbp_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in xxxbp_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = xxxbp_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "xxxbp_video"
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = (
        re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "xxxbp_video"
    )
    filename = f"{safe_title}_{int(time.time())}.mp4"
    filepath = os.path.join(OUTPUT_FOLDER, filename)

    dl_id = f"xb_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, size = await download_xxxbp_direct(
            entry["url"],
            filepath,
            progress_cb,
            video_url=chosen["url"],
            quality="high",
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success or not os.path.exists(filepath) or size < 1024:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return
        ul_id = f"xb_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[XXXBP] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            clear_download_state(dl_id)
        except Exception:
            pass
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def xxxbp_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("xb_cancel_", "")
    xxxbp_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ─── SexXXX (custom handlers: needs page_url + video_url) ───

sexxxx_sessions: dict = {}


async def process_sexxxx_request(event, url: str, status_msg):
    qualities, title, info = await extract_sexxxx_qualities(url)
    if not qualities:
        err_detail = f" — `{title[:150]}`" if title else ""
        await safe_edit(status_msg, f"❌ کیفیتی پیدا نشد{err_detail}")
        return
    session_id = f"sxx_{event.chat_id}_{event.id}_{int(time.time())}"
    sexxxx_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "info": info,
        "chat_id": event.chat_id,
    }
    title_display = title[:60] if title else "ویدیو SexXXX"
    text = f"🎬 **{title_display}**\n🌐 SexXXX\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"sxx_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"sxx_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def sexxxx_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in sexxxx_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = sexxxx_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "sexxxx_video"
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = (
        re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "sexxxx_video"
    )
    filename = f"{safe_title}_{int(time.time())}.mp4"
    filepath = os.path.join(OUTPUT_FOLDER, filename)

    dl_id = f"sx_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, size = await download_sexxxx_direct(
            entry["url"],
            filepath,
            progress_cb,
            video_url=chosen["url"],
            quality="high",
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success or not os.path.exists(filepath) or size < 1024:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return
        ul_id = f"sx_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[SEXXX] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            clear_download_state(dl_id)
        except Exception:
            pass
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def sexxxx_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("sxx_cancel_", "")
    sexxxx_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ─── Elliniko (custom handlers: needs page_url + video_url) ───

elliniko_sessions: dict = {}


async def process_elliniko_request(event, url: str, status_msg):
    qualities, title, info = await extract_elliniko_qualities(url)
    if not qualities:
        err_detail = f" — `{title[:150]}`" if title else ""
        await safe_edit(status_msg, f"❌ کیفیتی پیدا نشد{err_detail}")
        return
    session_id = f"el_{event.chat_id}_{event.id}_{int(time.time())}"
    elliniko_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "info": info,
        "chat_id": event.chat_id,
    }
    title_display = title[:60] if title else "ویدیو Elliniko"
    text = f"🎬 **{title_display}**\n🌐 Elliniko\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"el_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"el_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def elliniko_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in elliniko_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = elliniko_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "elliniko_video"
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = (
        re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "elliniko_video"
    )
    filename = f"{safe_title}_{int(time.time())}.mp4"
    filepath = os.path.join(OUTPUT_FOLDER, filename)

    dl_id = f"el_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, size = await download_elliniko_direct(
            entry["url"],
            filepath,
            progress_cb,
            video_url=chosen["url"],
            quality="high",
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success or not os.path.exists(filepath) or size < 1024:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return
        ul_id = f"el_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[ELLINIKO] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            clear_download_state(dl_id)
        except Exception:
            pass
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def elliniko_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("el_cancel_", "")
    elliniko_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ─── RapeLust (custom handlers: needs page_url + video_url) ───

rapelust_sessions: dict = {}


async def process_rapelust_request(event, url: str, status_msg):
    qualities, title, info = await extract_rapelust_qualities(url)
    if not qualities:
        err_detail = f" — `{title[:150]}`" if title else ""
        await safe_edit(status_msg, f"❌ کیفیتی پیدا نشد{err_detail}")
        return
    session_id = f"rp_{event.chat_id}_{event.id}_{int(time.time())}"
    rapelust_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "info": info,
        "chat_id": event.chat_id,
    }
    title_display = title[:60] if title else "ویدیو RapeLust"
    text = f"🎬 **{title_display}**\n🌐 RapeLust\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"rp_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"rp_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def rapelust_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in rapelust_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = rapelust_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "rapelust_video"
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = (
        re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "rapelust_video"
    )
    filename = f"{safe_title}_{int(time.time())}.mp4"
    filepath = os.path.join(OUTPUT_FOLDER, filename)

    dl_id = f"rp_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, size = await download_rapelust_direct(
            entry["url"],
            filepath,
            progress_cb,
            video_url=chosen["url"],
            quality="high",
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success or not os.path.exists(filepath) or size < 1024:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return
        ul_id = f"rp_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[RAPELUST] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            clear_download_state(dl_id)
        except Exception:
            pass
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def rapelust_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("rp_cancel_", "")
    rapelust_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ─── RapeInAss (custom handlers: needs page_url + video_url) ───

rapeinass_sessions: dict = {}


async def process_rapeinass_request(event, url: str, status_msg):
    qualities, title, info = await extract_rapeinass_qualities(url)
    if not qualities:
        err_detail = f" — `{title[:150]}`" if title else ""
        await safe_edit(status_msg, f"❌ کیفیتی پیدا نشد{err_detail}")
        return
    session_id = f"ra_{event.chat_id}_{event.id}_{int(time.time())}"
    rapeinass_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "info": info,
        "chat_id": event.chat_id,
    }
    title_display = title[:60] if title else "ویدیو RapeInAss"
    text = f"🎬 **{title_display}**\n🌐 RapeInAss\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"ra_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"ra_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def rapeinass_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in rapeinass_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = rapeinass_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "rapeinass_video"
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = (
        re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "rapeinass_video"
    )
    filename = f"{safe_title}_{int(time.time())}.mp4"
    filepath = os.path.join(OUTPUT_FOLDER, filename)

    dl_id = f"ra_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, size = await download_rapeinass_direct(
            entry["url"],
            filepath,
            progress_cb,
            video_url=chosen["url"],
            quality="high",
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success or not os.path.exists(filepath) or size < 1024:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return
        ul_id = f"ra_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[RAPEINASS] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            clear_download_state(dl_id)
        except Exception:
            pass
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def rapeinass_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("ra_cancel_", "")
    rapeinass_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ─── ForcedLove (custom handlers: needs page_url + video_url) ───

forcedlove_sessions: dict = {}


async def process_forcedlove_request(event, url: str, status_msg):
    qualities, title, info = await extract_forcedlove_qualities(url)
    if not qualities:
        err_detail = f" — `{title[:150]}`" if title else ""
        await safe_edit(status_msg, f"❌ کیفیتی پیدا نشد{err_detail}")
        return
    session_id = f"fl_{event.chat_id}_{event.id}_{int(time.time())}"
    forcedlove_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "info": info,
        "chat_id": event.chat_id,
    }
    title_display = title[:60] if title else "ویدیو ForcedLove"
    text = f"🎬 **{title_display}**\n🌐 ForcedLove\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"fl_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"fl_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def forcedlove_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in forcedlove_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = forcedlove_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "forcedlove_video"
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = (
        re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "forcedlove_video"
    )
    filename = f"{safe_title}_{int(time.time())}.mp4"
    filepath = os.path.join(OUTPUT_FOLDER, filename)

    dl_id = f"fl_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, size = await download_forcedlove_direct(
            entry["url"],
            filepath,
            progress_cb,
            video_url=chosen["url"],
            quality="high",
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success or not os.path.exists(filepath) or size < 1024:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return
        ul_id = f"fl_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[FORCEDLOVE] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            clear_download_state(dl_id)
        except Exception:
            pass
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def forcedlove_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("fl_cancel_", "")
    forcedlove_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ─── RapedWS (custom handlers: needs page_url + video_url) ───

rapedws_sessions: dict = {}


async def process_rapedws_request(event, url: str, status_msg):
    qualities, title, info = await extract_rapedws_qualities(url)
    if not qualities:
        err_detail = f" — `{title[:150]}`" if title else ""
        await safe_edit(status_msg, f"❌ کیفیتی پیدا نشد{err_detail}")
        return
    session_id = f"rd_{event.chat_id}_{event.id}_{int(time.time())}"
    rapedws_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "info": info,
        "chat_id": event.chat_id,
    }
    title_display = title[:60] if title else "ویدیو RapedWS"
    text = f"🎬 **{title_display}**\n🌐 RapedWS\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"rd_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"rd_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def rapedws_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in rapedws_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = rapedws_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "rapedws_video"
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = (
        re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "rapedws_video"
    )
    filename = f"{safe_title}_{int(time.time())}.mp4"
    filepath = os.path.join(OUTPUT_FOLDER, filename)

    dl_id = f"rd_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, size = await download_rapedws_direct(
            entry["url"],
            filepath,
            progress_cb,
            video_url=chosen["url"],
            quality="high",
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success or not os.path.exists(filepath) or size < 1024:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return
        ul_id = f"rd_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[RAPEDWS] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            clear_download_state(dl_id)
        except Exception:
            pass
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def rapedws_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("rd_cancel_", "")
    rapedws_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ─── SEXTVX (custom handlers: needs page_url + video_url) ───

sextvx_sessions: dict = {}


async def process_sextvx_request(event, url: str, status_msg):
    qualities, title, info = await extract_sextvx_qualities(url)
    if not qualities:
        err_detail = f" — `{title[:150]}`" if title else ""
        await safe_edit(status_msg, f"❌ کیفیتی پیدا نشد{err_detail}")
        return
    session_id = f"st_{event.chat_id}_{event.id}_{int(time.time())}"
    sextvx_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "info": info,
        "chat_id": event.chat_id,
    }
    title_display = title[:60] if title else "ویدیو SEXTVX"
    text = f"🎬 **{title_display}**\n🌐 SEXTVX\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"st_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"st_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def sextvx_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in sextvx_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = sextvx_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "sextvx_video"
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = (
        re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "sextvx_video"
    )
    filename = f"{safe_title}_{int(time.time())}.mp4"
    filepath = os.path.join(OUTPUT_FOLDER, filename)

    dl_id = f"st_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, size = await download_sextvx_direct(
            entry["url"],
            filepath,
            progress_cb,
            video_url=chosen["url"],
            quality="high",
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success or not os.path.exists(filepath) or size < 1024:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return
        ul_id = f"st_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[SEXTVX] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            clear_download_state(dl_id)
        except Exception:
            pass
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def sextvx_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("st_cancel_", "")
    sextvx_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ─── PornDos (custom handlers: needs page_url + video_url) ───

porndos_sessions: dict = {}


async def process_porndos_request(event, url: str, status_msg):
    qualities, title, info = await extract_porndos_qualities(url)
    if not qualities:
        err_detail = f" — `{title[:150]}`" if title else ""
        await safe_edit(status_msg, f"❌ کیفیتی پیدا نشد{err_detail}")
        return
    session_id = f"pn_{event.chat_id}_{event.id}_{int(time.time())}"
    porndos_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "info": info,
        "chat_id": event.chat_id,
    }
    title_display = title[:60] if title else "ویدیو PornDos"
    text = f"🎬 **{title_display}**\n🌐 PornDos\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"pn_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"pn_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def porndos_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in porndos_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = porndos_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "porndos_video"
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = (
        re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "porndos_video"
    )
    filename = f"{safe_title}_{int(time.time())}.mp4"
    filepath = os.path.join(OUTPUT_FOLDER, filename)

    dl_id = f"pn_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, size = await download_porndos_direct(
            entry["url"],
            filepath,
            progress_cb,
            video_url=chosen["url"],
            quality="high",
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success or not os.path.exists(filepath) or size < 1024:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return
        ul_id = f"pn_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[PORNDOS] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            clear_download_state(dl_id)
        except Exception:
            pass
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def porndos_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("pn_cancel_", "")
    porndos_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ─── Shahvani (custom handlers: needs page_url + video_url) ───

shahvani_sessions: dict = {}


async def process_shahvani_request(event, url: str, status_msg):
    qualities, title, info = await extract_shahvani_qualities(url)
    if not qualities:
        err_detail = f" — `{title[:150]}`" if title else ""
        await safe_edit(status_msg, f"❌ کیفیتی پیدا نشد{err_detail}")
        return
    session_id = f"sh_{event.chat_id}_{event.id}_{int(time.time())}"
    shahvani_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "info": info,
        "chat_id": event.chat_id,
    }
    title_display = title[:60] if title else "ویدیو Shahvani"
    text = f"🎬 **{title_display}**\n🌐 Shahvani\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"sh_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"sh_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def shahvani_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in shahvani_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = shahvani_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "shahvani_video"
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = (
        re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "shahvani_video"
    )
    filename = f"{safe_title}_{int(time.time())}.mp4"
    filepath = os.path.join(OUTPUT_FOLDER, filename)

    dl_id = f"sh_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, size = await download_shahvani_direct(
            entry["url"],
            filepath,
            progress_cb,
            video_url=chosen["url"],
            quality="high",
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success or not os.path.exists(filepath) or size < 1024:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return
        ul_id = f"sh_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[SHAHVANI] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            clear_download_state(dl_id)
        except Exception:
            pass
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def shahvani_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("sh_cancel_", "")
    shahvani_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ─── Deviants (custom handlers: needs page_url + video_url) ───

deviants_sessions: dict = {}


async def process_deviants_request(event, url: str, status_msg):
    qualities, title, info = await extract_deviants_qualities(url)
    if not qualities:
        err_detail = f" — `{title[:150]}`" if title else ""
        await safe_edit(status_msg, f"❌ کیفیتی پیدا نشد{err_detail}")
        return
    session_id = f"dv_{event.chat_id}_{event.id}_{int(time.time())}"
    deviants_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "info": info,
        "chat_id": event.chat_id,
    }
    title_display = title[:60] if title else "ویدیو Deviants"
    text = f"🎬 **{title_display}**\n🌐 Deviants\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"dv_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"dv_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def deviants_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in deviants_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = deviants_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "deviants_video"
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = (
        re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "deviants_video"
    )
    filename = f"{safe_title}_{int(time.time())}.mp4"
    filepath = os.path.join(OUTPUT_FOLDER, filename)

    dl_id = f"dv_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, size = await download_deviants_direct(
            entry["url"],
            filepath,
            progress_cb,
            video_url=chosen["url"],
            quality="high",
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success or not os.path.exists(filepath) or size < 1024:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return
        ul_id = f"dv_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[DEVIANTS] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            clear_download_state(dl_id)
        except Exception:
            pass
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def deviants_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("dv_cancel_", "")
    deviants_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ─── XXXVids (custom handlers: needs page_url + video_url) ───

xxxvids_sessions: dict = {}


async def process_xxxvids_request(event, url: str, status_msg):
    qualities, title, info = await extract_xxxvids_qualities(url)
    if not qualities:
        err_detail = f" — `{title[:150]}`" if title else ""
        await safe_edit(status_msg, f"❌ کیفیتی پیدا نشد{err_detail}")
        return
    session_id = f"xx_{event.chat_id}_{event.id}_{int(time.time())}"
    xxxvids_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "info": info,
        "chat_id": event.chat_id,
    }
    title_display = title[:60] if title else "ویدیو XXXVids"
    text = f"🎬 **{title_display}**\n🌐 XXXVids\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"xx_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"xx_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def xxxvids_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in xxxvids_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = xxxvids_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "xxxvids_video"
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = (
        re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "xxxvids_video"
    )
    filename = f"{safe_title}_{int(time.time())}.mp4"
    filepath = os.path.join(OUTPUT_FOLDER, filename)

    dl_id = f"xx_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, size = await download_xxxvids_direct(
            entry["url"],
            filepath,
            progress_cb,
            video_url=chosen["url"],
            quality="high",
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success or not os.path.exists(filepath) or size < 1024:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return
        ul_id = f"xx_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[XXXVIDS] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            clear_download_state(dl_id)
        except Exception:
            pass
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def xxxvids_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("xx_cancel_", "")
    xxxvids_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ─── Mutterfickt (custom handlers: needs page_url + video_url) ───

mutterfickt_sessions: dict = {}


async def process_mutterfickt_request(event, url: str, status_msg):
    qualities, title, info = await extract_mutterfickt_qualities(url)
    if not qualities:
        err_detail = f" — `{title[:150]}`" if title else ""
        await safe_edit(status_msg, f"❌ کیفیتی پیدا نشد{err_detail}")
        return
    session_id = f"mf_{event.chat_id}_{event.id}_{int(time.time())}"
    mutterfickt_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "info": info,
        "chat_id": event.chat_id,
    }
    title_display = title[:60] if title else "ویدیو Mutterfickt"
    text = f"🎬 **{title_display}**\n🌐 Mutterfickt\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"mf_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"mf_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def mutterfickt_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in mutterfickt_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = mutterfickt_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "mutterfickt_video"
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = (
        re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "mutterfickt_video"
    )
    filename = f"{safe_title}_{int(time.time())}.mp4"
    filepath = os.path.join(OUTPUT_FOLDER, filename)

    dl_id = f"mf_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, size = await download_mutterfickt_direct(
            entry["url"],
            filepath,
            progress_cb,
            video_url=chosen["url"],
            quality="high",
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success or not os.path.exists(filepath) or size < 1024:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return
        ul_id = f"mf_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[MUTTERFICKT] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            clear_download_state(dl_id)
        except Exception:
            pass
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def mutterfickt_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("mf_cancel_", "")
    mutterfickt_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ─── RulexPorn (custom handlers: needs page_url + video_url) ───

rulexporn_sessions: dict = {}


async def process_rulexporn_request(event, url: str, status_msg):
    qualities, title, info = await extract_rulexporn_qualities(url)
    if not qualities:
        err_detail = f" — `{title[:150]}`" if title else ""
        await safe_edit(status_msg, f"❌ کیفیتی پیدا نشد{err_detail}")
        return
    session_id = f"rx_{event.chat_id}_{event.id}_{int(time.time())}"
    rulexporn_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "info": info,
        "chat_id": event.chat_id,
    }
    title_display = title[:60] if title else "ویدیو RulexPorn"
    text = f"🎬 **{title_display}**\n🌐 RulexPorn\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"rx_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"rx_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def rulexporn_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in rulexporn_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = rulexporn_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "rulexporn_video"
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = (
        re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "rulexporn_video"
    )
    filename = f"{safe_title}_{int(time.time())}.mp4"
    filepath = os.path.join(OUTPUT_FOLDER, filename)

    dl_id = f"rx_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, size = await download_rulexporn_direct(
            entry["url"],
            filepath,
            progress_cb,
            video_url=chosen["url"],
            quality="high",
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success or not os.path.exists(filepath) or size < 1024:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return
        ul_id = f"rx_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[RULEXPORN] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            clear_download_state(dl_id)
        except Exception:
            pass
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def rulexporn_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("rx_cancel_", "")
    rulexporn_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ─── RobbyPorn (custom handlers: needs page_url + video_url) ───

robbyporn_sessions: dict = {}


async def process_robbyporn_request(event, url: str, status_msg):
    qualities, title, info = await extract_robbyporn_qualities(url)
    if not qualities:
        err_detail = f" — `{title[:150]}`" if title else ""
        await safe_edit(status_msg, f"❌ کیفیتی پیدا نشد{err_detail}")
        return
    session_id = f"rb_{event.chat_id}_{event.id}_{int(time.time())}"
    robbyporn_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "info": info,
        "chat_id": event.chat_id,
    }
    title_display = title[:60] if title else "ویدیو RobbyPorn"
    text = f"🎬 **{title_display}**\n🌐 RobbyPorn\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"rb_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"rb_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def robbyporn_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in robbyporn_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = robbyporn_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "robbyporn_video"
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = (
        re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "robbyporn_video"
    )
    filename = f"{safe_title}_{int(time.time())}.mp4"
    filepath = os.path.join(OUTPUT_FOLDER, filename)

    dl_id = f"rb_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, size = await download_robbyporn_direct(
            entry["url"],
            filepath,
            progress_cb,
            video_url=chosen["url"],
            quality="high",
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success or not os.path.exists(filepath) or size < 1024:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return
        ul_id = f"rb_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[ROBBYPORN] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            clear_download_state(dl_id)
        except Exception:
            pass
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def robbyporn_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("rb_cancel_", "")
    robbyporn_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ─── BGXMonster (custom handlers: needs page_url + video_url) ───

bgxmonster_sessions: dict = {}


async def process_bgxmonster_request(event, url: str, status_msg):
    qualities, title, info = await extract_bgxmonster_qualities(url)
    if not qualities:
        err_detail = f" — `{title[:150]}`" if title else ""
        await safe_edit(status_msg, f"❌ کیفیتی پیدا نشد{err_detail}")
        return
    session_id = f"bg_{event.chat_id}_{event.id}_{int(time.time())}"
    bgxmonster_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "info": info,
        "chat_id": event.chat_id,
    }
    title_display = title[:60] if title else "ویدیو BGXMonster"
    text = f"🎬 **{title_display}**\n🌐 BGXMonster\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"bg_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"bg_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def bgxmonster_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in bgxmonster_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = bgxmonster_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "bgxmonster_video"
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = (
        re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "bgxmonster_video"
    )
    filename = f"{safe_title}_{int(time.time())}.mp4"
    filepath = os.path.join(OUTPUT_FOLDER, filename)

    dl_id = f"bg_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, size = await download_bgxmonster_direct(
            entry["url"],
            filepath,
            progress_cb,
            video_url=chosen["url"],
            quality="high",
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success or not os.path.exists(filepath) or size < 1024:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return
        ul_id = f"bg_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[BGXMONSTER] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            clear_download_state(dl_id)
        except Exception:
            pass
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def bgxmonster_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("bg_cancel_", "")
    bgxmonster_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ─── Jebacina (custom handlers: needs page_url + video_url) ───

jebacina_sessions: dict = {}


async def process_jebacina_request(event, url: str, status_msg):
    qualities, title, info = await extract_jebacina_qualities(url)
    if not qualities:
        err_detail = f" — `{title[:150]}`" if title else ""
        await safe_edit(status_msg, f"❌ کیفیتی پیدا نشد{err_detail}")
        return
    session_id = f"jb_{event.chat_id}_{event.id}_{int(time.time())}"
    jebacina_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "info": info,
        "chat_id": event.chat_id,
    }
    title_display = title[:60] if title else "ویدیو Jebacina"
    text = f"🎬 **{title_display}**\n🌐 Jebacina\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"jb_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"jb_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def jebacina_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in jebacina_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = jebacina_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "jebacina_video"
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = (
        re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "jebacina_video"
    )
    filename = f"{safe_title}_{int(time.time())}.mp4"
    filepath = os.path.join(OUTPUT_FOLDER, filename)

    dl_id = f"jb_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, size = await download_jebacina_direct(
            entry["url"],
            filepath,
            progress_cb,
            video_url=chosen["url"],
            quality="high",
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success or not os.path.exists(filepath) or size < 1024:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return
        ul_id = f"jb_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[JEBACINA] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            clear_download_state(dl_id)
        except Exception:
            pass
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def jebacina_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("jb_cancel_", "")
    jebacina_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ─── Ersties (custom handlers: needs page_url + hls_url) ───

ersties_sessions: dict = {}


async def process_ersties_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities, title, info = await extract_ersties_qualities(
        url,
        progress_cb=progress_cb,
    )
    if not qualities:
        err_detail = f" — `{title[:150]}`" if title else ""
        await safe_edit(status_msg, f"❌ کیفیتی پیدا نشد{err_detail}")
        return
    session_id = f"es_{event.chat_id}_{event.id}_{int(time.time())}"
    ersties_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
    }
    title_display = title[:60] if title else "ویدیو Ersties"
    text = f"🎬 **{title_display}**\n🌐 Ersties\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"es_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"es_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def ersties_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in ersties_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = ersties_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "ersties_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "ersties_video"
    filepath = os.path.join(OUTPUT_FOLDER, f"es_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"es_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, size = await download_ersties_direct(
            url=url,
            filepath=filepath,
            progress_cb=progress_cb,
            video_url=chosen.get("url", ""),
            quality=chosen.get("quality_key", "720p"),
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success or not os.path.exists(filepath) or size < 1024:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return
        ul_id = f"es_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[ERSTIES] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def ersties_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("es_cancel_", "")
    ersties_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ─── WhoresHub (custom handlers: needs page_url + video_url) ───

whoreshub_sessions: dict = {}


async def process_whoreshub_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities, title, info = await extract_whoreshub_qualities(
        url,
        progress_cb=progress_cb,
    )
    if not qualities:
        err_detail = f" — `{title[:150]}`" if title else ""
        await safe_edit(status_msg, f"❌ کیفیتی پیدا نشد{err_detail}")
        return
    session_id = f"wh_{event.chat_id}_{event.id}_{int(time.time())}"
    whoreshub_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
    }
    title_display = title[:60] if title else "ویدیو WhoresHub"
    text = f"🎬 **{title_display}**\n🌐 WhoresHub\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"wh_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"wh_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def whoreshub_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in whoreshub_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = whoreshub_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "whoreshub_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "whoreshub_video"
    filepath = os.path.join(OUTPUT_FOLDER, f"wh_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"wh_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, size = await download_whoreshub_direct(
            url=url,
            filepath=filepath,
            progress_cb=progress_cb,
            video_url=chosen.get("url", ""),
            quality=chosen.get("quality_key", "high"),
            dl_id=dl_id,
            all_sources=qualities,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success or not os.path.exists(filepath) or size < 1024:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return
        ul_id = f"wh_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[WHORESHUB] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def whoreshub_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("wh_cancel_", "")
    whoreshub_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ─── Reddit (via RedVid without Playwright) ───

reddit_sessions: Dict[str, dict] = {}


async def process_reddit_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities, title, info = await extract_reddit_qualities(
        url,
        progress_cb=progress_cb,
    )
    if not qualities:
        err_detail = f" — `{title[:150]}`" if title else ""
        await safe_edit(status_msg, f"❌ کیفیتی پیدا نشد{err_detail}")
        return
    session_id = f"red_{event.chat_id}_{event.id}_{int(time.time())}"
    reddit_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
        "created_at": time.time(),
    }
    title_display = title[:60] if title else "ویدیو Reddit"
    text = f"🎬 **{title_display}**\n🌐 Reddit (via RedVid)\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"red_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"red_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def reddit_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in reddit_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = reddit_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "reddit_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "reddit_video"

    filepath = os.path.join(OUTPUT_FOLDER, f"red_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"red_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, file_size = await download_reddit_direct(
            url=url,
            filepath=filepath,
            progress_cb=progress_cb,
            video_url=chosen.get("url", ""),
            quality=chosen.get("quality_key", "720p"),
            dl_id=dl_id,
            all_sources=qualities,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return

        ul_id = f"red_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(file_size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[REDDIT] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def reddit_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("red_cancel_", "")
    reddit_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ─── XFetish (custom handlers: needs page_url + video_url) ───

xfetish_sessions: dict = {}


async def process_xfetish_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities, title, info = await extract_xfetish_qualities(
        url,
        progress_cb=progress_cb,
    )
    if not qualities:
        err_detail = f" — `{title[:150]}`" if title else ""
        await safe_edit(status_msg, f"❌ کیفیتی پیدا نشد{err_detail}")
        return
    session_id = f"xf_{event.chat_id}_{event.id}_{int(time.time())}"
    xfetish_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
    }
    title_display = title[:60] if title else "ویدیو XFetish"
    text = f"🎬 **{title_display}**\n🌐 XFetish\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"xf_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"xf_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def xfetish_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in xfetish_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = xfetish_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "xfetish_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "xfetish_video"
    filepath = os.path.join(OUTPUT_FOLDER, f"xf_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"xf_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, size = await download_xfetish_direct(
            url=url,
            filepath=filepath,
            progress_cb=progress_cb,
            video_url=chosen.get("url", ""),
            quality=chosen.get("quality_key", "high"),
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success or not os.path.exists(filepath) or size < 1024:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return
        ul_id = f"xf_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[XFETISH] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def xfetish_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("xf_cancel_", "")
    xfetish_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ─── Erome (album: videos + photos) ───

erome_sessions: dict = {}


async def process_erome_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    media = await extract_erome_media(url, progress_cb=progress_cb)
    if media.get("error"):
        await safe_edit(status_msg, f"❌ {media['error']}")
        return
    if not media.get("has_videos") and not media.get("has_photos"):
        await safe_edit(status_msg, "❌ ویدیو یا عکسی در این آلبوم پیدا نشد")
        return
    session_id = f"er_{event.chat_id}_{event.id}_{int(time.time())}"
    erome_sessions[session_id] = {
        "media": media,
        "chat_id": event.chat_id,
    }
    title_display = (media.get("title") or "آلبوم Erome")[:60]
    parts = []
    if media.get("has_videos"):
        parts.append(f"🎬 {len(media['videos'])} ویدیو")
    if media.get("has_photos"):
        parts.append(f"🖼 {len(media['photos'])} عکس")
    text = f"📂 **{title_display}**\n🌐 Erome\n\n🎞 {', '.join(parts)}\n\nکدوم رو دانلود کنم؟"
    buttons = [
        [Button.inline("📥 همه (ویدیو و عکس)", f"er_pick_{session_id}_all")],
        [Button.inline("🎬 فقط ویدیوها", f"er_pick_{session_id}_videos")],
        [Button.inline("🖼 فقط عکس‌ها", f"er_pick_{session_id}_photos")],
        [Button.inline("❌ لغو", f"er_cancel_{session_id}")],
    ]
    await safe_edit(status_msg, text, buttons=buttons)


async def erome_pick_callback(event):
    data = event.data.decode()
    mode = data.rsplit("_", 1)[-1]
    session_id = data[len("er_pick_"):].rsplit("_", 1)[0]
    if session_id not in erome_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = erome_sessions.pop(session_id)
    media = entry["media"]
    if mode == "videos" and not media.get("has_videos"):
        await event.answer("🎬 ویدیویی در این آلبوم نیست", alert=True)
        return
    if mode == "photos" and not media.get("has_photos"):
        await event.answer("🖼 عکسی در این آلبوم نیست", alert=True)
        return

    title = media.get("title") or "erome_album"
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:40].strip() or "erome_album"
    out_dir = os.path.join(OUTPUT_FOLDER, f"erome_{safe_title}_{int(time.time())}")
    os.makedirs(out_dir, exist_ok=True)

    dl_id = f"er_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    erome_active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit("⏬ **در حال دانلود...**", buttons=cancel_btn)
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        if mode == "videos":
            video_results = await download_all_videos(
                media, out_dir, progress_cb=progress_cb, dl_id=dl_id
            )
            photo_results = []
        elif mode == "photos":
            photo_results = await download_all_photos(
                media, out_dir, progress_cb=progress_cb, dl_id=dl_id
            )
            video_results = []
        else:
            out = await download_all_media(
                media, out_dir, progress_cb=progress_cb, dl_id=dl_id
            )
            video_results = out.get("videos", [])
            photo_results = out.get("photos", [])
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")

        ok_videos = [r for r in video_results if r.get("success")]
        ok_photos = [r for r in photo_results if r.get("success")]
        if not ok_videos and not ok_photos:
            err = next(
                (r.get("error") for r in (video_results + photo_results) if r.get("error")),
                "Unknown error",
            )
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err[:100]}`")
            return

        sent_v = sent_p = 0
        for i, r in enumerate(ok_videos, 1):
            await safe_edit(status_msg, f"📤 **در حال آپلود ویدیو {i}/{len(ok_videos)}...**")
            ul_id = f"er_ul_{event.chat_id}_{event.id}_{i}_{int(time.time())}"
            caption = f"🎬 **{title[:80]}**\n🎞 ویدیو {i}/{len(ok_videos)}\n📦 {human_readable_size(r.get('size', 0))}"
            try:
                await send_file_with_progress(
                    client=event.client,
                    chat_id=entry["chat_id"],
                    filepath=r["filepath"],
                    caption=caption,
                    status_msg=status_msg,
                    buttons=None,
                    supports_streaming=True,
                    ul_id=ul_id,
                )
                sent_v += 1
            except Exception as e:
                logger.error(f"[EROME] upload video failed: {e}", exc_info=True)
        for i, r in enumerate(ok_photos, 1):
            await safe_edit(status_msg, f"📤 **در حال ارسال عکس {i}/{len(ok_photos)}...**")
            try:
                await event.client.send_file(
                    entry["chat_id"],
                    r["filepath"],
                    caption=f"📸 {title[:80]}\n🖼 عکس {i}/{len(ok_photos)}",
                )
                sent_p += 1
            except Exception as e:
                logger.error(f"[EROME] upload photo failed: {e}", exc_info=True)

        if sent_v or sent_p:
            summary = f"✅ **ارسال شد:** {sent_v} ویدیو و {sent_p} عکس"
        else:
            summary = "❌ چیزی ارسال نشد"
        await safe_edit(status_msg, summary)
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[EROME] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        erome_active_downloads.pop(dl_id, None)
        try:
            if os.path.isdir(out_dir):
                shutil.rmtree(out_dir, ignore_errors=True)
        except Exception:
            pass


async def erome_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("er_cancel_", "")
    erome_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ─── Beeg (same flow as XFetish: needs page_url + video_url) ───

beeg_sessions: dict = {}


async def process_beeg_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities, title, info = await extract_beeg_qualities(url, progress_cb=progress_cb)
    if not qualities:
        err_detail = f" — `{title[:150]}`" if title else ""
        await safe_edit(status_msg, f"❌ کیفیتی پیدا نشد{err_detail}")
        return
    session_id = f"bee_{event.chat_id}_{event.id}_{int(time.time())}"
    beeg_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
    }
    title_display = title[:60] if title else "ویدیو Beeg"
    text = f"🎬 **{title_display}**\n🌐 Beeg\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"bee_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"bee_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def beeg_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in beeg_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = beeg_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "beeg_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "beeg_video"
    filepath = os.path.join(OUTPUT_FOLDER, f"bee_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"bee_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, size = await download_beeg_direct(
            url=url,
            filepath=filepath,
            progress_cb=progress_cb,
            video_url=chosen.get("url", ""),
            quality=chosen.get("quality_key", "high"),
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success or not os.path.exists(filepath) or size < 1024:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return
        ul_id = f"bee_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[BEEG] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def beeg_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("bee_cancel_", "")
    beeg_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ─── SpankBang (same flow as XFetish: needs page_url + video_url) ───

spankbang_sessions: dict = {}


async def process_spankbang_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities, title, info = await extract_spankbang_qualities(url, progress_cb=progress_cb)
    if not qualities:
        err_detail = f" — `{title[:150]}`" if title else ""
        await safe_edit(status_msg, f"❌ کیفیتی پیدا نشد{err_detail}")
        return
    session_id = f"sb_{event.chat_id}_{event.id}_{int(time.time())}"
    spankbang_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
    }
    title_display = title[:60] if title else "ویدیو SpankBang"
    text = f"🎬 **{title_display}**\n🌐 SpankBang\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"sb_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"sb_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def spankbang_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in spankbang_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = spankbang_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "spankbang_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "spankbang_video"
    filepath = os.path.join(OUTPUT_FOLDER, f"sb_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"sb_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, size = await download_spankbang_direct(
            url=url,
            filepath=filepath,
            progress_cb=progress_cb,
            video_url=chosen.get("url", ""),
            quality=chosen.get("quality_key", "high"),
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success or not os.path.exists(filepath) or size < 1024:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return
        ul_id = f"sb_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[SPANKBANG] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def spankbang_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("sb_cancel_", "")
    spankbang_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ─── IXXX (same flow as XFetish: needs page_url + video_url) ───

ixxx_sessions: dict = {}


async def process_ixxx_request(event, url: str, status_msg):
    async def progress_cb(text):
        try:
            await status_msg.edit(text, parse_mode="markdown")
        except Exception:
            pass

    qualities, title, info = await extract_ixxx_qualities(url, progress_cb=progress_cb)
    if not qualities:
        err_detail = f" — `{title[:150]}`" if title else ""
        await safe_edit(status_msg, f"❌ کیفیتی پیدا نشد{err_detail}")
        return
    session_id = f"ix_{event.chat_id}_{event.id}_{int(time.time())}"
    ixxx_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
    }
    title_display = title[:60] if title else "ویدیو IXXX"
    text = f"🎬 **{title_display}**\n🌐 IXXX\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = []
    for i, q in enumerate(qualities):
        buttons.append([Button.inline(q["label"], f"ix_q_{session_id}_{i}")])
    buttons.append([Button.inline("❌ لغو", f"ix_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def ixxx_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in ixxx_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = ixxx_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "ixxx_video"
    url = entry["url"]
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "ixxx_video"
    filepath = os.path.join(OUTPUT_FOLDER, f"ix_{safe_title}_{int(time.time())}.mp4")

    dl_id = f"ix_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}",
            buttons=cancel_btn,
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, size = await download_ixxx_direct(
            url=url,
            filepath=filepath,
            progress_cb=progress_cb,
            video_url=chosen.get("url", ""),
            quality=chosen.get("quality_key", "high"),
            dl_id=dl_id,
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success or not os.path.exists(filepath) or size < 1024:
            err_msg = error or "Unknown error"
            await safe_edit(status_msg, f"❌ دانلود ناموفق: `{err_msg}`")
            return
        ul_id = f"ix_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(size)}"
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[IXXX] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def ixxx_cancel_callback(event):
    data = event.data.decode()
    session_id = data.replace("ix_cancel_", "")
    ixxx_sessions.pop(session_id, None)
    await event.answer("❌ لغو شد", alert=False)
    try:
        await event.edit("❌ **لغو شد.**", buttons=None)
    except Exception:
        pass


# ─── YouPorn (custom handlers: passes format_id + page_url) ───

youporn_sessions: dict = {}


async def process_youporn_request(event, url: str, status_msg):
    qualities, title = await extract_youporn_qualities(url)
    if not qualities:
        err_detail = f" — `{title[:150]}`" if title else ""
        await safe_edit(status_msg, f"❌ کیفیتی پیدا نشد{err_detail}")
        return
    session_id = f"yp_{event.chat_id}_{event.id}_{int(time.time())}"
    youporn_sessions[session_id] = {
        "url": url,
        "title": title,
        "qualities": qualities,
        "chat_id": event.chat_id,
    }
    title_display = title[:60] if title else "ویدیو YouPorn"
    text = f"🎬 **{title_display}**\n🌐 YouPorn\n\n🎚 کیفیت مورد نظر رو انتخاب کن:"
    buttons = [
        [Button.inline(q["label"], f"yp_q_{session_id}_{i}")]
        for i, q in enumerate(qualities)
    ]
    buttons.append([Button.inline("❌ لغو", f"yp_cancel_{session_id}")])
    await safe_edit(status_msg, text, buttons=buttons)


async def youporn_quality_callback(event):
    data = event.data.decode()
    parts = data.split("_")
    quality_index = int(parts[-1])
    session_id = "_".join(parts[2:-1])
    if session_id not in youporn_sessions:
        await event.answer("❌ Session منقضی شده. دوباره لینک بفرست.", alert=True)
        return
    entry = youporn_sessions.pop(session_id)
    qualities = entry["qualities"]
    title = entry["title"] or "YouPorn_video"
    if quality_index >= len(qualities):
        await event.answer("❌ خطا", alert=True)
        return
    chosen = qualities[quality_index]
    await event.answer(f"✅ {chosen['label']}", alert=False)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "YouPorn_video"
    filename = f"{safe_title}_{int(time.time())}.mp4"
    filepath = os.path.join(OUTPUT_FOLDER, filename)

    dl_id = f"yp_dl_{event.chat_id}_{event.id}_{int(time.time())}"
    active_downloads[dl_id] = {"paused": False, "cancelled": False}
    cancel_btn = [[Button.inline("❌ Cancel", f"dlcancel_{dl_id}")]]

    try:
        await event.edit(
            f"⏬ **در حال دانلود...**\n🎚 {chosen['label']}", buttons=cancel_btn
        )
    except Exception:
        pass
    status_msg = await event.get_message()

    async def progress_cb(text):
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        try:
            await status_msg.edit(text, parse_mode="markdown", buttons=cancel_btn)
        except Exception:
            pass

    try:
        success, error, size = await download_youporn_direct(
            entry["url"],
            filepath,
            progress_cb,
            format_id=chosen.get("format_id"),
        )
        if active_downloads.get(dl_id, {}).get("cancelled"):
            raise asyncio.CancelledError("Download cancelled by user")
        if not success or not os.path.exists(filepath) or size < 1024:
            await safe_edit(
                status_msg, f"❌ دانلود ناموفق: `{error or 'Unknown error'}`"
            )
            return
        ul_id = f"yp_ul_{event.chat_id}_{event.id}_{int(time.time())}"
        await safe_edit(status_msg, "📤 **در حال آپلود...**")
        caption = (
            f"🎬 **{title[:80]}**\n🎚 {chosen['label']}\n📦 {human_readable_size(size)}"
        )
        await send_file_with_progress(
            client=event.client,
            chat_id=entry["chat_id"],
            filepath=filepath,
            caption=caption,
            status_msg=status_msg,
            buttons=None,
            supports_streaming=True,
            ul_id=ul_id,
        )
    except asyncio.CancelledError:
        try:
            await status_msg.edit("🚫 **Cancelled.**", buttons=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[YouPorn] Error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ خطا: `{str(e)[:100]}`")
    finally:
        active_downloads.pop(dl_id, None)
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


async def youporn_cancel_callback(event):
    youporn_sessions.pop("_".join(event.data.decode().split("_")[2:]), None)
    await event.edit("❌ **لغو شد**", buttons=None)


async def main():
    print("\n" + "=" * 60)
    print("🚀 ULTIMATE BOT v5")
    print("   FIX 1: 403 → auto-retry via Dirpy")
    print("   FIX 2: FFmpeg -noautorotate + yuv420p")
    print("   FIX 3: size_input uses chat_id (not sender_id)")
    print("   FIX 4: pause/resume split callbacks")
    print("   FIX 5: command pattern conflict resolved")
    print("   FIX 6: detailed logging enabled")
    print("=" * 60)
    logger.info("[BOOT] Starting bot...")

    start_keep_alive()
    client = TelegramClient(
        "ultimate_bot_session",
        API_ID,
        API_HASH,
        connection_retries=5,
    )
    for attempt in range(5):
        try:
            await client.start(bot_token=BOT_TOKEN)
            break
        except FloodWaitError as e:
            wait = e.seconds + 5
            logger.warning(
                f"[BOOT] FloodWait — waiting {wait}s before retry (attempt {attempt + 1}/5)"
            )
            await asyncio.sleep(wait)
    else:
        logger.critical("[BOOT] Could not connect after 5 FloodWait retries. Exiting.")
        return

    # ===== CallbackQuery handlers =====
    client.add_event_handler(
        dl_pause_callback, events.CallbackQuery(pattern=r"dlpause_(.+)")
    )
    client.add_event_handler(
        dl_resume_callback, events.CallbackQuery(pattern=r"dlresume_(.+)")
    )
    client.add_event_handler(
        dl_cancel_callback, events.CallbackQuery(pattern=r"dlcancel_(.+)")
    )
    client.add_event_handler(
        ul_cancel_callback, events.CallbackQuery(pattern=r"ulcancel_(.+)")
    )
    client.add_event_handler(
        compress_callback, events.CallbackQuery(pattern=r"compress_(.+)")
    )
    client.add_event_handler(
        check_callback, events.CallbackQuery(pattern=r"check_(.+)")
    )
    client.add_event_handler(
        pickurl_callback, events.CallbackQuery(pattern=r"pickurl_(.+)_(\d+)$")
    )
    client.add_event_handler(
        admin_add_callback, events.CallbackQuery(pattern=r"admin_add")
    )
    client.add_event_handler(
        admin_remove_callback, events.CallbackQuery(pattern=r"admin_remove")
    )
    client.add_event_handler(
        admin_refresh_callback, events.CallbackQuery(pattern=r"admin_refresh")
    )
    client.add_event_handler(
        admin_cancel_callback, events.CallbackQuery(pattern=r"admin_cancel")
    )
    client.add_event_handler(
        admin_sponsor_add_callback, events.CallbackQuery(pattern=r"admin_sponsor_add")
    )
    client.add_event_handler(
        admin_sponsor_rmlist_callback,
        events.CallbackQuery(pattern=r"admin_sponsor_rmlist"),
    )
    client.add_event_handler(
        admin_sponsor_rm_callback, events.CallbackQuery(pattern=r"admin_sponsor_rm_\d+")
    )
    client.add_event_handler(
        sponsor_join_check_callback, events.CallbackQuery(pattern=r"sponsor_ok_.+")
    )
    client.add_event_handler(
        pdfimg_del_callback, events.CallbackQuery(pattern=rb"pdfimg_del\|")
    )
    client.add_event_handler(
        pdfimg_send_callback, events.CallbackQuery(pattern=rb"pdfimg_send\|")
    )
    client.add_event_handler(
        pdfimg_hd_callback, events.CallbackQuery(pattern=rb"pdfimg_hd\|")
    )
    client.add_event_handler(
        vgh_yes_callback, events.CallbackQuery(pattern=r"vgh_yes_(.+)")
    )
    client.add_event_handler(
        vgh_no_callback, events.CallbackQuery(pattern=r"vgh_no_(.+)")
    )
    client.add_event_handler(
        vsend_callback, events.CallbackQuery(pattern=r"vsend_(.+)")
    )
    client.add_event_handler(
        subburn_callback, events.CallbackQuery(pattern=r"subburn_vbatch_(.+)")
    )
    client.add_event_handler(
        subsend_callback, events.CallbackQuery(pattern=r"subsend_(.+)")
    )
    client.add_event_handler(
        subburn_list_callback, events.CallbackQuery(pattern=r"subburn_list_(.+)")
    )
    client.add_event_handler(
        subburn_sel_callback, events.CallbackQuery(pattern=r"subburn_sel_(.+)")
    )
    client.add_event_handler(
        sharelink_callback, events.CallbackQuery(pattern=r"sharelink_(.+)")
    )
    client.add_event_handler(
        uplod_callback, events.CallbackQuery(pattern=r"uplod_(.+)")
    )
    client.add_event_handler(
        subextr_callback, events.CallbackQuery(pattern=r"subextr_(.+)")
    )
    client.add_event_handler(
        subskip_callback, events.CallbackQuery(pattern=r"subskip_(.+)")
    )
    client.add_event_handler(
        subburn_proc_cancel_callback, events.CallbackQuery(pattern=r"subburn_proc_cancel_(.+)")
    )
    client.add_event_handler(
        subtitle_cancel_callback, events.CallbackQuery(pattern=r"subcancl_(.+)")
    )
    client.add_event_handler(
        snapwc_select_callback, events.CallbackQuery(pattern=r"snapwc_q_(.+)")
    )
    client.add_event_handler(
        snapwc_cancel_callback, events.CallbackQuery(pattern=r"snapwc_cancel_(.+)")
    )
    client.add_event_handler(
        y2mate_quality_callback,
        events.CallbackQuery(pattern=r"y2m_(?!cancel)(.+)_(\d+)"),
    )
    client.add_event_handler(
        y2mate_quality_callback, events.CallbackQuery(pattern=r"y2mq_.+")
    )
    client.add_event_handler(
        y2mate_cancel_callback, events.CallbackQuery(pattern=r"y2mc_.+")
    )
    client.add_event_handler(
        savep_cancel_callback, events.CallbackQuery(pattern=r"savep_cancel_.+")
    )
    client.add_event_handler(
        xnxx_quality_callback, events.CallbackQuery(pattern=r"xnxx_q_.+")
    )
    client.add_event_handler(
        xnxx_cancel_callback, events.CallbackQuery(pattern=r"xnxx_cancel_.+")
    )
    client.add_event_handler(
        pornhub_quality_callback, events.CallbackQuery(pattern=r"pornhub_q_.+")
    )
    client.add_event_handler(
        pornhub_cancel_callback, events.CallbackQuery(pattern=r"pornhub_cancel_.+")
    )
    client.add_event_handler(
        cartoonporn_quality_callback, events.CallbackQuery(pattern=r"cp_q_.+")
    )
    client.add_event_handler(
        cartoonporn_cancel_callback, events.CallbackQuery(pattern=r"cp_cancel_.+")
    )
    client.add_event_handler(
        rule34video_quality_callback, events.CallbackQuery(pattern=r"r34v_q_.+")
    )
    client.add_event_handler(
        rule34video_cancel_callback, events.CallbackQuery(pattern=r"r34v_cancel_.+")
    )
    client.add_event_handler(
        xanimu_quality_callback, events.CallbackQuery(pattern=r"xa_q_.+")
    )
    client.add_event_handler(
        xanimu_cancel_callback, events.CallbackQuery(pattern=r"xa_cancel_.+")
    )
    client.add_event_handler(
        porntrex_quality_callback, events.CallbackQuery(pattern=r"pt_q_.+")
    )
    client.add_event_handler(
        porntrex_cancel_callback, events.CallbackQuery(pattern=r"pt_cancel_.+")
    )
    client.add_event_handler(
        heavyr_quality_callback, events.CallbackQuery(pattern=r"hr_q_.+")
    )
    client.add_event_handler(
        heavyr_cancel_callback, events.CallbackQuery(pattern=r"hr_cancel_.+")
    )
    client.add_event_handler(
        wonporn_quality_callback, events.CallbackQuery(pattern=r"wp_q_.+")
    )
    client.add_event_handler(
        wonporn_cancel_callback, events.CallbackQuery(pattern=r"wp_cancel_.+")
    )
    client.add_event_handler(
        leaksextape_quality_callback, events.CallbackQuery(pattern=r"ls_q_.+")
    )
    client.add_event_handler(
        leaksextape_cancel_callback, events.CallbackQuery(pattern=r"ls_cancel_.+")
    )
    client.add_event_handler(
        xxxpublicpornvideos_quality_callback, events.CallbackQuery(pattern=r"xp_q_.+")
    )
    client.add_event_handler(
        xxxpublicpornvideos_cancel_callback, events.CallbackQuery(pattern=r"xp_cancel_.+")
    )
    client.add_event_handler(
        cartoonporncom_quality_callback, events.CallbackQuery(pattern=r"cc_q_.+")
    )
    client.add_event_handler(
        cartoonporncom_cancel_callback, events.CallbackQuery(pattern=r"cc_cancel_.+")
    )
    client.add_event_handler(
        hihentaiporn_quality_callback, events.CallbackQuery(pattern=r"hh_q_.+")
    )
    client.add_event_handler(
        hihentaiporn_cancel_callback, events.CallbackQuery(pattern=r"hh_cancel_.+")
    )
    client.add_event_handler(
        fetishshrine_quality_callback, events.CallbackQuery(pattern=r"fs_q_.+")
    )
    client.add_event_handler(
        fetishshrine_cancel_callback, events.CallbackQuery(pattern=r"fs_cancel_.+")
    )
    client.add_event_handler(
        bigfuck_quality_callback, events.CallbackQuery(pattern=r"bf_q_.+")
    )
    client.add_event_handler(
        bigfuck_cancel_callback, events.CallbackQuery(pattern=r"bf_cancel_.+")
    )
    client.add_event_handler(
        babestube_quality_callback, events.CallbackQuery(pattern=r"bb_q_.+")
    )
    client.add_event_handler(
        babestube_cancel_callback, events.CallbackQuery(pattern=r"bb_cancel_.+")
    )
    client.add_event_handler(
        pornwhite_quality_callback, events.CallbackQuery(pattern=r"pw_q_.+")
    )
    client.add_event_handler(
        pornwhite_cancel_callback, events.CallbackQuery(pattern=r"pw_cancel_.+")
    )
    client.add_event_handler(
        porndroids_quality_callback, events.CallbackQuery(pattern=r"pd_q_.+")
    )
    client.add_event_handler(
        porndroids_cancel_callback, events.CallbackQuery(pattern=r"pd_cancel_.+")
    )
    client.add_event_handler(
        hdtube_quality_callback, events.CallbackQuery(pattern=r"hd_q_.+")
    )
    client.add_event_handler(
        hdtube_cancel_callback, events.CallbackQuery(pattern=r"hd_cancel_.+")
    )
    client.add_event_handler(
        sleazyneasy_quality_callback, events.CallbackQuery(pattern=r"sn_q_.+")
    )
    client.add_event_handler(
        sleazyneasy_cancel_callback, events.CallbackQuery(pattern=r"sn_cancel_.+")
    )
    client.add_event_handler(
        shameless_quality_callback, events.CallbackQuery(pattern=r"sl_q_.+")
    )
    client.add_event_handler(
        shameless_cancel_callback, events.CallbackQuery(pattern=r"sl_cancel_.+")
    )
    client.add_event_handler(
        hqporner_quality_callback, events.CallbackQuery(pattern=r"hq_q_.+")
    )
    client.add_event_handler(
        hqporner_cancel_callback, events.CallbackQuery(pattern=r"hq_cancel_.+")
    )
    client.add_event_handler(
        youjizz_quality_callback, events.CallbackQuery(pattern=r"yj_q_.+")
    )
    client.add_event_handler(
        youjizz_cancel_callback, events.CallbackQuery(pattern=r"yj_cancel_.+")
    )
    client.add_event_handler(
        severeporn_quality_callback, events.CallbackQuery(pattern=r"sp_q_.+")
    )
    client.add_event_handler(
        severeporn_cancel_callback, events.CallbackQuery(pattern=r"sp_cancel_.+")
    )
    client.add_event_handler(
        mat6tube_quality_callback, events.CallbackQuery(pattern=r"m6_q_.+")
    )
    client.add_event_handler(
        mat6tube_cancel_callback, events.CallbackQuery(pattern=r"m6_cancel_.+")
    )
    client.add_event_handler(
        peekvids_quality_callback, events.CallbackQuery(pattern=r"pv_q_.+")
    )
    client.add_event_handler(
        peekvids_cancel_callback, events.CallbackQuery(pattern=r"pv_cancel_.+")
    )
    client.add_event_handler(
        paradisehill_quality_callback, events.CallbackQuery(pattern=r"ph_q_.+")
    )
    client.add_event_handler(
        paradisehill_cancel_callback, events.CallbackQuery(pattern=r"ph_cancel_.+")
    )
    client.add_event_handler(
        sxyprn_quality_callback, events.CallbackQuery(pattern=r"sx_q_.+")
    )
    client.add_event_handler(
        sxyprn_cancel_callback, events.CallbackQuery(pattern=r"sx_cancel_.+")
    )
    client.add_event_handler(
        kick_quality_callback, events.CallbackQuery(pattern=r"kc_q_.+")
    )
    client.add_event_handler(
        kick_cancel_callback, events.CallbackQuery(pattern=r"kc_cancel_.+")
    )
    client.add_event_handler(
        luxuretv_quality_callback, events.CallbackQuery(pattern=r"lx_q_.+")
    )
    client.add_event_handler(
        luxuretv_cancel_callback, events.CallbackQuery(pattern=r"lx_cancel_.+")
    )
    client.add_event_handler(
        ytdlp_quality_callback, events.CallbackQuery(pattern=r"ytdlp_q_.+")
    )
    client.add_event_handler(
        ytdlp_cancel_callback, events.CallbackQuery(pattern=r"ytdlp_cancel_.+")
    )
    client.add_event_handler(
        xvideos_quality_callback, events.CallbackQuery(pattern=r"xv_q_.+")
    )
    client.add_event_handler(
        xvideos_cancel_callback, events.CallbackQuery(pattern=r"xv_cancel_.+")
    )
    client.add_event_handler(
        xgroovy_quality_callback, events.CallbackQuery(pattern=r"xg_q_.+")
    )
    client.add_event_handler(
        xgroovy_cancel_callback, events.CallbackQuery(pattern=r"xg_cancel_.+")
    )
    client.add_event_handler(
        teensexvideos_quality_callback, events.CallbackQuery(pattern=r"tsv_q_.+")
    )
    client.add_event_handler(
        teensexvideos_cancel_callback, events.CallbackQuery(pattern=r"tsv_cancel_.+")
    )
    client.add_event_handler(
        usersporn_quality_callback, events.CallbackQuery(pattern=r"up_q_.+")
    )
    client.add_event_handler(
        usersporn_cancel_callback, events.CallbackQuery(pattern=r"up_cancel_.+")
    )
    client.add_event_handler(
        hentaihaven_quality_callback, events.CallbackQuery(pattern=r"hh_q_.+")
    )
    client.add_event_handler(
        hentaihaven_cancel_callback, events.CallbackQuery(pattern=r"hh_cancel_.+")
    )
    client.add_event_handler(
        rat_quality_callback, events.CallbackQuery(pattern=r"rat_q_.+")
    )
    client.add_event_handler(
        rat_cancel_callback, events.CallbackQuery(pattern=r"rat_cancel_.+")
    )
    client.add_event_handler(
        sexvid_quality_callback, events.CallbackQuery(pattern=r"sx_q_.+")
    )
    client.add_event_handler(
        sexvid_cancel_callback, events.CallbackQuery(pattern=r"sx_cancel_.+")
    )
    client.add_event_handler(
        tube8_quality_callback, events.CallbackQuery(pattern=r"tb_q_.+")
    )
    client.add_event_handler(
        tube8_cancel_callback, events.CallbackQuery(pattern=r"tb_cancel_.+")
    )
    client.add_event_handler(
        redtube_quality_callback, events.CallbackQuery(pattern=r"rt_q_.+")
    )
    client.add_event_handler(
        redtube_cancel_callback, events.CallbackQuery(pattern=r"rt_cancel_.+")
    )
    client.add_event_handler(
        youporn_quality_callback, events.CallbackQuery(pattern=r"yp_q_.+")
    )
    client.add_event_handler(
        youporn_cancel_callback, events.CallbackQuery(pattern=r"yp_cancel_.+")
    )
    client.add_event_handler(
        hohoj_quality_callback, events.CallbackQuery(pattern=r"hj_q_.+")
    )
    client.add_event_handler(
        hohoj_cancel_callback, events.CallbackQuery(pattern=r"hj_cancel_.+")
    )
    client.add_event_handler(
        porna91_quality_callback, events.CallbackQuery(pattern=r"p91_q_.+")
    )
    client.add_event_handler(
        porna91_cancel_callback, events.CallbackQuery(pattern=r"p91_cancel_.+")
    )
    client.add_event_handler(
        playvids_quality_callback, events.CallbackQuery(pattern=r"pv_q_.+")
    )
    client.add_event_handler(
        playvids_cancel_callback, events.CallbackQuery(pattern=r"pv_cancel_.+")
    )
    client.add_event_handler(
        porn300_quality_callback, events.CallbackQuery(pattern=r"p3_q_.+")
    )
    client.add_event_handler(
        porn300_cancel_callback, events.CallbackQuery(pattern=r"p3_cancel_.+")
    )
    client.add_event_handler(
        tnaflix_quality_callback, events.CallbackQuery(pattern=r"tf_q_.+")
    )
    client.add_event_handler(
        tnaflix_cancel_callback, events.CallbackQuery(pattern=r"tf_cancel_.+")
    )
    client.add_event_handler(
        eporner_quality_callback, events.CallbackQuery(pattern=r"ep_q_.+")
    )
    client.add_event_handler(
        eporner_cancel_callback, events.CallbackQuery(pattern=r"ep_cancel_.+")
    )
    client.add_event_handler(
        pornzog_quality_callback, events.CallbackQuery(pattern=r"pz_q_.+")
    )
    client.add_event_handler(
        pornzog_cancel_callback, events.CallbackQuery(pattern=r"pz_cancel_.+")
    )
    client.add_event_handler(
        xxxbp_quality_callback, events.CallbackQuery(pattern=r"xb_q_.+")
    )
    client.add_event_handler(
        xxxbp_cancel_callback, events.CallbackQuery(pattern=r"xb_cancel_.+")
    )
    client.add_event_handler(
        sexxxx_quality_callback, events.CallbackQuery(pattern=r"sxx_q_.+")
    )
    client.add_event_handler(
        sexxxx_cancel_callback, events.CallbackQuery(pattern=r"sxx_cancel_.+")
    )
    client.add_event_handler(
        elliniko_quality_callback, events.CallbackQuery(pattern=r"el_q_.+")
    )
    client.add_event_handler(
        elliniko_cancel_callback, events.CallbackQuery(pattern=r"el_cancel_.+")
    )
    client.add_event_handler(
        rapelust_quality_callback, events.CallbackQuery(pattern=r"rp_q_.+")
    )
    client.add_event_handler(
        rapelust_cancel_callback, events.CallbackQuery(pattern=r"rp_cancel_.+")
    )
    client.add_event_handler(
        rapeinass_quality_callback, events.CallbackQuery(pattern=r"ra_q_.+")
    )
    client.add_event_handler(
        rapeinass_cancel_callback, events.CallbackQuery(pattern=r"ra_cancel_.+")
    )
    client.add_event_handler(
        forcedlove_quality_callback, events.CallbackQuery(pattern=r"fl_q_.+")
    )
    client.add_event_handler(
        forcedlove_cancel_callback, events.CallbackQuery(pattern=r"fl_cancel_.+")
    )
    client.add_event_handler(
        rapedws_quality_callback, events.CallbackQuery(pattern=r"rd_q_.+")
    )
    client.add_event_handler(
        rapedws_cancel_callback, events.CallbackQuery(pattern=r"rd_cancel_.+")
    )
    client.add_event_handler(
        sextvx_quality_callback, events.CallbackQuery(pattern=r"st_q_.+")
    )
    client.add_event_handler(
        sextvx_cancel_callback, events.CallbackQuery(pattern=r"st_cancel_.+")
    )
    client.add_event_handler(
        porndos_quality_callback, events.CallbackQuery(pattern=r"pn_q_.+")
    )
    client.add_event_handler(
        porndos_cancel_callback, events.CallbackQuery(pattern=r"pn_cancel_.+")
    )
    client.add_event_handler(
        shahvani_quality_callback, events.CallbackQuery(pattern=r"sh_q_.+")
    )
    client.add_event_handler(
        shahvani_cancel_callback, events.CallbackQuery(pattern=r"sh_cancel_.+")
    )
    client.add_event_handler(
        deviants_quality_callback, events.CallbackQuery(pattern=r"dv_q_.+")
    )
    client.add_event_handler(
        deviants_cancel_callback, events.CallbackQuery(pattern=r"dv_cancel_.+")
    )
    client.add_event_handler(
        xxxvids_quality_callback, events.CallbackQuery(pattern=r"xx_q_.+")
    )
    client.add_event_handler(
        xxxvids_cancel_callback, events.CallbackQuery(pattern=r"xx_cancel_.+")
    )
    client.add_event_handler(
        mutterfickt_quality_callback, events.CallbackQuery(pattern=r"mf_q_.+")
    )
    client.add_event_handler(
        mutterfickt_cancel_callback, events.CallbackQuery(pattern=r"mf_cancel_.+")
    )
    client.add_event_handler(
        rulexporn_quality_callback, events.CallbackQuery(pattern=r"rx_q_.+")
    )
    client.add_event_handler(
        rulexporn_cancel_callback, events.CallbackQuery(pattern=r"rx_cancel_.+")
    )
    client.add_event_handler(
        robbyporn_quality_callback, events.CallbackQuery(pattern=r"rb_q_.+")
    )
    client.add_event_handler(
        robbyporn_cancel_callback, events.CallbackQuery(pattern=r"rb_cancel_.+")
    )
    client.add_event_handler(
        bgxmonster_quality_callback, events.CallbackQuery(pattern=r"bg_q_.+")
    )
    client.add_event_handler(
        bgxmonster_cancel_callback, events.CallbackQuery(pattern=r"bg_cancel_.+")
    )
    client.add_event_handler(
        jebacina_quality_callback, events.CallbackQuery(pattern=r"jb_q_.+")
    )
    client.add_event_handler(
        jebacina_cancel_callback, events.CallbackQuery(pattern=r"jb_cancel_.+")
    )
    client.add_event_handler(
        ersties_quality_callback, events.CallbackQuery(pattern=r"es_q_.+")
    )
    client.add_event_handler(
        ersties_cancel_callback, events.CallbackQuery(pattern=r"es_cancel_.+")
    )
    client.add_event_handler(
        whoreshub_quality_callback, events.CallbackQuery(pattern=r"wh_q_.+")
    )
    client.add_event_handler(
        whoreshub_cancel_callback, events.CallbackQuery(pattern=r"wh_cancel_.+")
    )
    client.add_event_handler(
        xfetish_quality_callback, events.CallbackQuery(pattern=r"xf_q_.+")
    )
    client.add_event_handler(
        xfetish_cancel_callback, events.CallbackQuery(pattern=r"xf_cancel_.+")
    )
    client.add_event_handler(
        erome_pick_callback, events.CallbackQuery(pattern=r"er_pick_.+")
    )
    client.add_event_handler(
        erome_cancel_callback, events.CallbackQuery(pattern=r"er_cancel_.+")
    )
    client.add_event_handler(
        beeg_quality_callback, events.CallbackQuery(pattern=r"bee_q_.+")
    )
    client.add_event_handler(
        beeg_cancel_callback, events.CallbackQuery(pattern=r"bee_cancel_.+")
    )
    client.add_event_handler(
        spankbang_quality_callback, events.CallbackQuery(pattern=r"sb_q_.+")
    )
    client.add_event_handler(
        spankbang_cancel_callback, events.CallbackQuery(pattern=r"sb_cancel_.+")
    )
    client.add_event_handler(
        ixxx_quality_callback, events.CallbackQuery(pattern=r"ix_q_.+")
    )
    client.add_event_handler(
        ixxx_cancel_callback, events.CallbackQuery(pattern=r"ix_cancel_.+")
    )
    client.add_event_handler(
        reddit_quality_callback, events.CallbackQuery(pattern=r"red_q_.+")
    )
    client.add_event_handler(
        reddit_cancel_callback, events.CallbackQuery(pattern=r"red_cancel_.+")
    )
    client.add_event_handler(
        setsearch_callback, events.CallbackQuery(pattern=r"setsearch_.+")
    )

    # ─── New site handlers (27 sites) ─────────────────
    # ثبت callback‌های همه‌ی سایت‌های جدید
    client.add_event_handler(hellporno_quality_callback, events.CallbackQuery(pattern=r"hpo_q_.+"))
    client.add_event_handler(hellporno_cancel_callback, events.CallbackQuery(pattern=r"hpo_cancel_.+"))
    client.add_event_handler(alphaporno_quality_callback, events.CallbackQuery(pattern=r"apo_q_.+"))
    client.add_event_handler(alphaporno_cancel_callback, events.CallbackQuery(pattern=r"apo_cancel_.+"))
    client.add_event_handler(bravoteens_quality_callback, events.CallbackQuery(pattern=r"bte_q_.+"))
    client.add_event_handler(bravoteens_cancel_callback, events.CallbackQuery(pattern=r"bte_cancel_.+"))
    client.add_event_handler(bravotube_quality_callback, events.CallbackQuery(pattern=r"btu_q_.+"))
    client.add_event_handler(bravotube_cancel_callback, events.CallbackQuery(pattern=r"btu_cancel_.+"))
    client.add_event_handler(crocotube_quality_callback, events.CallbackQuery(pattern=r"ctu_q_.+"))
    client.add_event_handler(crocotube_cancel_callback, events.CallbackQuery(pattern=r"ctu_cancel_.+"))
    client.add_event_handler(porngo_quality_callback, events.CallbackQuery(pattern=r"pgo_q_.+"))
    client.add_event_handler(porngo_cancel_callback, events.CallbackQuery(pattern=r"pgo_cancel_.+"))
    client.add_event_handler(txxx_quality_callback, events.CallbackQuery(pattern=r"txx_q_.+"))
    client.add_event_handler(txxx_cancel_callback, events.CallbackQuery(pattern=r"txx_cancel_.+"))
    client.add_event_handler(hclips_quality_callback, events.CallbackQuery(pattern=r"hcl_q_.+"))
    client.add_event_handler(hclips_cancel_callback, events.CallbackQuery(pattern=r"hcl_cancel_.+"))
    client.add_event_handler(upornia_quality_callback, events.CallbackQuery(pattern=r"upn2_q_.+"))
    client.add_event_handler(upornia_cancel_callback, events.CallbackQuery(pattern=r"upn2_cancel_.+"))
    client.add_event_handler(vjav_quality_callback, events.CallbackQuery(pattern=r"vja_q_.+"))
    client.add_event_handler(vjav_cancel_callback, events.CallbackQuery(pattern=r"vja_cancel_.+"))
    client.add_event_handler(hdzog_quality_callback, events.CallbackQuery(pattern=r"hdz2_q_.+"))
    client.add_event_handler(hdzog_cancel_callback, events.CallbackQuery(pattern=r"hdz2_cancel_.+"))
    client.add_event_handler(drtuber_quality_callback, events.CallbackQuery(pattern=r"drt_q_.+"))
    client.add_event_handler(drtuber_cancel_callback, events.CallbackQuery(pattern=r"drt_cancel_.+"))
    client.add_event_handler(porntop_quality_callback, events.CallbackQuery(pattern=r"ptp2_q_.+"))
    client.add_event_handler(porntop_cancel_callback, events.CallbackQuery(pattern=r"ptp2_cancel_.+"))
    client.add_event_handler(pornone_quality_callback, events.CallbackQuery(pattern=r"pon_q_.+"))
    client.add_event_handler(pornone_cancel_callback, events.CallbackQuery(pattern=r"pon_cancel_.+"))
    client.add_event_handler(pornhd_quality_callback, events.CallbackQuery(pattern=r"phd2_q_.+"))
    client.add_event_handler(pornhd_cancel_callback, events.CallbackQuery(pattern=r"phd2_cancel_.+"))
    client.add_event_handler(xtube_quality_callback, events.CallbackQuery(pattern=r"xtu_q_.+"))
    client.add_event_handler(xtube_cancel_callback, events.CallbackQuery(pattern=r"xtu_cancel_.+"))
    client.add_event_handler(mofosex_quality_callback, events.CallbackQuery(pattern=r"mfs2_q_.+"))
    client.add_event_handler(mofosex_cancel_callback, events.CallbackQuery(pattern=r"mfs2_cancel_.+"))
    client.add_event_handler(fapvid_quality_callback, events.CallbackQuery(pattern=r"fpv_q_.+"))
    client.add_event_handler(fapvid_cancel_callback, events.CallbackQuery(pattern=r"fpv_cancel_.+"))
    client.add_event_handler(monsterporn_quality_callback, events.CallbackQuery(pattern=r"mst_q_.+"))
    client.add_event_handler(monsterporn_cancel_callback, events.CallbackQuery(pattern=r"mst_cancel_.+"))
    client.add_event_handler(fetishkitsch_quality_callback, events.CallbackQuery(pattern=r"ftk_q_.+"))
    client.add_event_handler(fetishkitsch_cancel_callback, events.CallbackQuery(pattern=r"ftk_cancel_.+"))
    client.add_event_handler(javhihi_quality_callback, events.CallbackQuery(pattern=r"jhh_q_.+"))
    client.add_event_handler(javhihi_cancel_callback, events.CallbackQuery(pattern=r"jhh_cancel_.+"))
    client.add_event_handler(tokyoporn_quality_callback, events.CallbackQuery(pattern=r"tkp_q_.+"))
    client.add_event_handler(tokyoporn_cancel_callback, events.CallbackQuery(pattern=r"tkp_cancel_.+"))
    client.add_event_handler(javwhores_quality_callback, events.CallbackQuery(pattern=r"jwh_q_.+"))
    client.add_event_handler(javwhores_cancel_callback, events.CallbackQuery(pattern=r"jwh_cancel_.+"))
    client.add_event_handler(goodporn_quality_callback, events.CallbackQuery(pattern=r"gdp_q_.+"))
    client.add_event_handler(goodporn_cancel_callback, events.CallbackQuery(pattern=r"gdp_cancel_.+"))
    client.add_event_handler(porn365_quality_callback, events.CallbackQuery(pattern=r"p3652_q_.+"))
    client.add_event_handler(porn365_cancel_callback, events.CallbackQuery(pattern=r"p3652_cancel_.+"))
    client.add_event_handler(fapcake_quality_callback, events.CallbackQuery(pattern=r"fpc_q_.+"))
    client.add_event_handler(fapcake_cancel_callback, events.CallbackQuery(pattern=r"fpc_cancel_.+"))
    client.add_event_handler(fux_quality_callback, events.CallbackQuery(pattern=r"fux_q_.+"))
    client.add_event_handler(fux_cancel_callback, events.CallbackQuery(pattern=r"fux_cancel_.+"))

    # ===== Command handlers =====
    client.add_event_handler(
        start_cmd, events.NewMessage(pattern=r"^/start(\s|$)", incoming=True)
    )
    client.add_event_handler(
        startgithub_cmd,
        events.NewMessage(pattern=r"^/startgithub(\s|$)", incoming=True),
    )
    client.add_event_handler(
        stopgithub_cmd, events.NewMessage(pattern=r"^/stopgithub(\s|$)", incoming=True)
    )
    client.add_event_handler(
        github_cmd, events.NewMessage(pattern=r"^/github(\s|$)", incoming=True)
    )
    client.add_event_handler(
        sub_cmd, events.NewMessage(pattern=r"^/sub(\s|$)", incoming=True)
    )
    client.add_event_handler(
        suboff_cmd, events.NewMessage(pattern=r"^/suboff(\s|$)", incoming=True)
    )
    client.add_event_handler(
        admin_cmd, events.NewMessage(pattern=r"^/admin(\s|$)", incoming=True)
    )
    client.add_event_handler(
        dirpy_command, events.NewMessage(pattern=r"^/dirpy(\s|$)", incoming=True)
    )
    client.add_event_handler(
        snapwc_command, events.NewMessage(pattern=r"^/snapwc(\s|$)", incoming=True)
    )
    client.add_event_handler(
        savep_command, events.NewMessage(pattern=r"^/savep(\s|$)", incoming=True)
    )
    client.add_event_handler(
        debug_hentaihaven, events.NewMessage(pattern=r"^/debughh(\s|$)", incoming=True)
    )
    client.add_event_handler(
        pdf_command, events.NewMessage(pattern=r"^/pdf(\s|$)", incoming=True)
    )
    client.add_event_handler(
        pdfimg_command, events.NewMessage(pattern=r"^/pdfimg(\s|$)", incoming=True)
    )
    client.add_event_handler(
        html_command, events.NewMessage(pattern=r"^/html(\s|$)", incoming=True)
    )
    client.add_event_handler(
        setsearch_cmd, events.NewMessage(pattern=r"^/setsearch(\s|$)", incoming=True)
    )
    # AI command
    client.add_event_handler(ai_command, events.NewMessage(pattern=r"^/ai(\s|$)", incoming=True))

    # ===== Message handlers (order matters - specific before generic) =====
    client.add_event_handler(admin_input_handler, events.NewMessage(incoming=True))
    client.add_event_handler(size_input_handler, events.NewMessage(incoming=True))
    client.add_event_handler(
        subtitle_receive_handler,
        events.NewMessage(incoming=True, func=lambda e: bool(e.document)),
    )
    client.add_event_handler(
        video_receive_handler,
        events.NewMessage(incoming=True, func=lambda e: bool(e.video or e.document)),
    )
    client.add_event_handler(snapwc_captcha_handler, events.NewMessage(incoming=True))
    client.add_event_handler(generic_url_handler, events.NewMessage(incoming=True))

    # Inline search handler
    client.add_event_handler(xnxx_inline_handler, events.InlineQuery())
    client.add_event_handler(
        inline_start_callback, events.CallbackQuery(pattern=r"^inl_")
    )

    # Server selection handler
    client.add_event_handler(server_cmd, events.NewMessage(pattern=r"^/server", incoming=True))
    client.add_event_handler(server_callback, events.CallbackQuery(pattern=r"srv_"))

    # IMDB (vidsrc) search & download callbacks
    # diycraft series callbacks
    client.add_event_handler(diycraft_cb_episode, events.CallbackQuery(pattern=r"dcep_"))
    client.add_event_handler(diycraft_cb_close, events.CallbackQuery(pattern=r"dcclose$"))
    # sarrast.com callbacks
    # مهم: sr_pdftr_ باید قبل از sr_pdf_ ثبت بشه تا اولویت داشته باشه
    client.add_event_handler(sarrast_pdf_translated_callback, events.CallbackQuery(pattern=r"sr_pdftr_"))
    client.add_event_handler(sarrast_pdf_callback, events.CallbackQuery(pattern=r"sr_pdf_"))
    client.add_event_handler(sarrast_zip_callback, events.CallbackQuery(pattern=r"sr_zip_"))
    client.add_event_handler(sarrast_imgs_callback, events.CallbackQuery(pattern=r"sr_imgs_"))
    # comic sites callbacks
    client.add_event_handler(comic_pdf_callback, events.CallbackQuery(pattern=r"cmpdf_"))
    client.add_event_handler(comic_images_callback, events.CallbackQuery(pattern=r"cmimg_"))
    client.add_event_handler(comic_video_callback, events.CallbackQuery(pattern=r"cmvid_"))
    client.add_event_handler(comic_select_callback, events.CallbackQuery(pattern=r"cmsel_"))
    client.add_event_handler(comic_page_callback, events.CallbackQuery(pattern=r"cmpage_"))
    # OCR callback
    client.add_event_handler(ocr_extract_callback, events.CallbackQuery(pattern=r"ocrex_"))
    # AI callbacks
    client.add_event_handler(ai_select_callback, events.CallbackQuery(pattern=r"aisel_"))
    client.add_event_handler(ai_count_callback, events.CallbackQuery(pattern=r"aicount_"))
    client.add_event_handler(ai_style_select_callback, events.CallbackQuery(pattern=r"aistyle_"))
    client.add_event_handler(ai_shape_callback, events.CallbackQuery(pattern=r"aishape_"))
    client.add_event_handler(ai_quality_callback, events.CallbackQuery(pattern=r"aiqual_"))
    client.add_event_handler(ai_generate_callback, events.CallbackQuery(pattern=r"aigen_"))
    # Face Swap callback
    client.add_event_handler(faceswap_init_callback, events.CallbackQuery(pattern=r"fsinit_"))
    client.add_event_handler(faceswap_nsfw_init_callback, events.CallbackQuery(pattern=r"fsnsfw_"))

        # Iran server (doostihaa) callbacks
    client.add_event_handler(iran_cb_title, events.CallbackQuery(pattern=r"irn_sel_"))
    client.add_event_handler(iran_cb_quality, events.CallbackQuery(pattern=r"irn_q_"))
    client.add_event_handler(iran_cb_nosub, events.CallbackQuery(pattern=r"irn_nosub$"))
    client.add_event_handler(iran_cb_close, events.CallbackQuery(pattern=r"irn_close$"))

    client.add_event_handler(imdb_cb_title, events.CallbackQuery(pattern=r"imd_sel_"))
    client.add_event_handler(imdb_cb_season, events.CallbackQuery(pattern=r"imd_season_"))
    client.add_event_handler(imdb_cb_episode, events.CallbackQuery(pattern=r"imd_ep_"))
    client.add_event_handler(imdb_cb_back, events.CallbackQuery(pattern=r"imd_back$"))
    client.add_event_handler(imdb_cb_close, events.CallbackQuery(pattern=r"imd_close$"))
    client.add_event_handler(imdb_cb_quality, events.CallbackQuery(pattern=r"imd_q_"))
    client.add_event_handler(imdb_cb_equality, events.CallbackQuery(pattern=r"imd_eq_"))
    client.add_event_handler(imdb_cb_sub, events.CallbackQuery(pattern=r"imd_sub_"))
    client.add_event_handler(imdb_cb_esub, events.CallbackQuery(pattern=r"imd_esub_"))
    client.add_event_handler(imdb_cb_sub, events.CallbackQuery(pattern=r"imd_withsub$"))
    client.add_event_handler(imdb_cb_sub, events.CallbackQuery(pattern=r"imd_softsub$"))
    client.add_event_handler(imdb_cb_sub, events.CallbackQuery(pattern=r"imd_sepsub$"))
    client.add_event_handler(imdb_cb_nosub, events.CallbackQuery(pattern=r"imd_nosub$"))
    client.add_event_handler(imdb_cb_enosub, events.CallbackQuery(pattern=r"imd_enosub$"))

    me = await client.get_me()
    global BOT_USERNAME
    BOT_USERNAME = me.username

    await _load_sponsors()
    _load_user_settings()

    logger.info(f"[BOOT] Bot connected as @{me.username} (id={me.id})")
    logger.info(f"[BOOT] Authorized users: {AUTHORIZED_USERS}")
    logger.info(
        f"[BOOT] GitHub enabled: {GITHUB_ENABLED} | repo: {GITHUB_REPO if github_configured() else 'not configured'}"
    )
    print(f"✅ Bot is online → @{me.username}")
    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        import uvloop

        uvloop.run(main())
    except ImportError:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Bot stopped.")
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
