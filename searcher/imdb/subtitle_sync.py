"""
subtitle_sync.py
────────────────
🆕 z34: همگام‌سازی خودکار زیرنویس با ویدیو (رفع درِیفِ تایمینگ)

مشکل:
  ویدیوهای سرورهای imdbplay (Vidzee/2Embed/Videasy) اغلب نسخه‌های PAL-spun یا
  تندتر-انکودشده هستن (25fps به‌جای 23.976 = 4.27% سریع‌تر، یا اسپیدآپ دلخواه سرور)،
  در حالی که زیرنویس‌های فارسی برای ریلیز اصلی 23.976fps تایم شدن.
  چون اختلاف «سرعت» هست (نه آفست ثابت)، خطا تجمعی می‌شه — کاربر هر چند دقیقه
  باید زیرنویس رو عقب/جلو کنه.

راه‌حل:
  قبل از جاسازی/ارسال، با ffprobe مدت و fps واقعی ویدیو خونده می‌شه؛
  اولین/آخرین cue زیرنویس پارس می‌شه؛ اگه الگو با ناهماهنگی سرعت بخونه
  (fps ویدیو ≠ fps زیرنویس)، کل تایم‌استمپ‌ها با ضریب درست مقیاس می‌شن:
      t_video = t_sub × (fps_زیرنویس / fps_ویدیو)

گاردها (که سابِ سالم دست نخوره):
  - اگه آخرین cue ≤ 45 ثانیه قبل از پایان ویدیو باشه → ساب جفته، دست نمی‌خوره
  - کلیپ < 5 دقیقه، ساب با cue کم، یا سابی که < 50% ویدیو رو پوشش می‌ده → رد
  - ضریب فقط وقتی اعمال می‌شه که «فضای کردیت» منطقی بمونه (0 تا 15% طول ویدیو)
  - هر خطایی → برگشت فایل اصلی (non-fatal)

استفاده:
  from searcher.imdb.subtitle_sync import auto_sync_subtitle
  sub_path, factor, reason = auto_sync_subtitle(video_path, sub_path)
  # factor != 1.0 یعنی بازتایم شد

غیرفعال‌سازی:  SUB_AUTOSYNC=0
"""

import json
import logging
import os
import re
import subprocess
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("SubtitleSync")

_FPS_23976 = 24000.0 / 1001.0  # 23.976023...
_FPS_2997 = 30000.0 / 1001.0   # 29.970029...

# fps های «اصلی» که زیرنویس‌ها برای اون‌ها تایم می‌شن
_ORIG_FPS_VALUES = (_FPS_23976, 24.0, 25.0, _FPS_2997)

# ─── گاردهای تصمیم ───
_MIN_CUES = 15            # کمتر از این = داده‌ی معتبر نیست
_MIN_DURATION_S = 300.0   # کلیپ‌های کوتاه اصلاً بررسی نمی‌شن
_MIN_COVERAGE = 0.5       # آخرین cue باید ≥ 50% ویدیو رو پوشش بده (وگرنه نسخه‌ی متفاوته)
_FIT_TOLERANCE_S = 45.0   # |پایان ویدیو − آخرین cue| ≤ این → ساب درسته، دست نمی‌خوره
_MAX_CREDITS_FRAC = 0.15  # بعد از ضریب، فاصله‌ی آخرین cue تا پایان ویدیو ≤ 15% (کردیت منطقی)
_MIN_END_SLACK_S = -15.0  # ساب می‌تونه حداکثر 15 ثانیه از پایان ویدیو بیرون بزنه
_MIN_DRIFT_S = 8.0        # اصلاح کمتر از 8 ثانیه (تجمعی در انتها) معنی نداره

_SUPPORTED_EXT = (".srt", ".vtt", ".ass", ".ssa")


# ═══════════════════════════════════════════════════════════
#   Timestamp parse / format
# ═══════════════════════════════════════════════════════════

# SRT: 00:00:59,123   VTT: 00:00:59.123 یا 59:59.123 یا 01:02.5 (ساعت اختیاری)
_TS_SRT_VTT = re.compile(r"^(?:(\d{1,3}):)?(\d{1,2}):(\d{1,2})[.,](\d{1,3})$")
# ASS/SSA: 0:00:59.12 (ساعت بدون صفر پیشرو، دودقیعه)
_TS_ASS = re.compile(r"^(\d+):(\d{1,2}):(\d{1,2})[.,](\d{1,3})$")


def _parse_srt_vtt_ts(tok: str) -> Optional[float]:
    m = _TS_SRT_VTT.match(tok.strip())
    if not m:
        return None
    h = int(m.group(1) or 0)
    mi = int(m.group(2))
    s = int(m.group(3))
    ms = int(m.group(4).ljust(3, "0"))
    return h * 3600 + mi * 60 + s + ms / 1000.0


def _parse_ass_ts(tok: str) -> Optional[float]:
    m = _TS_ASS.match(tok.strip())
    if not m:
        return None
    h = int(m.group(1))
    mi = int(m.group(2))
    s = int(m.group(3))
    cs = int(m.group(4).ljust(2, "0")[:2] if len(m.group(4)) >= 2 else m.group(4).ljust(2, "0"))
    return h * 3600 + mi * 60 + s + cs / 100.0


def _fmt_srt_vtt(t: float, sep: str = ",") -> str:
    """همیشه HH:MM:SS<sep>mmm — گرد کردن درست (بدون 60.000!)."""
    total_ms = max(0, int(round(t * 1000)))
    h, rem = divmod(total_ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _fmt_ass(t: float) -> str:
    total_cs = max(0, int(round(t * 100)))
    h, rem = divmod(total_cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


# ═══════════════════════════════════════════════════════════
#   Rescale (بازنویسی تایم‌استمپ‌ها با ضریب)
# ═══════════════════════════════════════════════════════════

def rescale_srt_text(text: str, factor: float) -> str:
    out = []
    for line in text.splitlines():
        if "-->" in line:
            left, _, right = line.partition("-->")
            ta = _parse_srt_vtt_ts(left)
            tb = _parse_srt_vtt_ts(right)
            if ta is not None and tb is not None:
                sep = "," if "," in left else "."
                out.append(f"{_fmt_srt_vtt(ta * factor, sep)} --> {_fmt_srt_vtt(tb * factor, sep)}")
                continue
        out.append(line)
    return "\n".join(out)


def rescale_vtt_text(text: str, factor: float) -> str:
    """VTT — فقط خطوط cue (دارای -->) دست می‌خورن؛ WEBVTT و X-TIMESTAMP-MAP دست‌نخورده."""
    out = []
    for line in text.splitlines():
        if "-->" in line:
            left, _, right = line.partition("-->")
            ta = _parse_srt_vtt_ts(left)
            tb = _parse_srt_vtt_ts(right)
            if ta is not None and tb is not None:
                sep = "," if "," in left else "."
                out.append(f"{_fmt_srt_vtt(ta * factor, sep)} --> {_fmt_srt_vtt(tb * factor, sep)}")
                continue
        out.append(line)
    return "\n".join(out)


def rescale_ass_text(text: str, factor: float) -> str:
    """ASS/SSA — فقط فیلدهای 1 و 2 (Start/End) در خطوط Dialogue دست می‌خورن."""
    out = []
    for line in text.splitlines():
        if line.startswith("Dialogue:"):
            head, sep, rest = line.partition("Dialogue:")
            fields = rest.split(",")
            if len(fields) >= 3:
                ta = _parse_ass_ts(fields[1])
                tb = _parse_ass_ts(fields[2])
                if ta is not None and tb is not None:
                    fields[1] = _fmt_ass(ta * factor)
                    fields[2] = _fmt_ass(tb * factor)
                    out.append(head + "Dialogue:" + ",".join(fields))
                    continue
        out.append(line)
    return "\n".join(out)


def rescale_subtitle_file(sub_path: str, factor: float) -> Optional[str]:
    """
    بازتایم فایل زیرنویس با ضریب مشخص → فایل جدید کنار اصلیش (*_synced.ext).
    Returns: مسیر فایل جدید، یا None در خطا.
    """
    if abs(factor - 1.0) < 1e-9:
        return sub_path
    ext = os.path.splitext(sub_path)[1].lower()
    if ext not in _SUPPORTED_EXT:
        return None
    try:
        with open(sub_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        if ext == ".srt":
            new_text = rescale_srt_text(text, factor)
        elif ext == ".vtt":
            new_text = rescale_vtt_text(text, factor)
        else:  # .ass / .ssa
            new_text = rescale_ass_text(text, factor)

        base, ext_real = os.path.splitext(sub_path)
        out_path = f"{base}_synced{ext_real}"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(new_text)
        return out_path
    except Exception as e:
        logger.warning("rescale_subtitle_file failed for %s: %s", sub_path, e)
        return None


# ═══════════════════════════════════════════════════════════
#   Cue stats (اولین/آخرین cue)
# ═══════════════════════════════════════════════════════════

def get_cue_stats(sub_path: str) -> Optional[Dict[str, float]]:
    """
    آمار cue: count, first (شروع اولین), last (پایان آخرین).
    SRT/VTT: خطوط دارای -->؛  ASS/SSA: خطوط Dialogue (فیلد 1 و 2).
    """
    ext = os.path.splitext(sub_path)[1].lower()
    count = 0
    first: Optional[float] = None
    last: Optional[float] = None
    try:
        with open(sub_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n")
                if ext in (".ass", ".ssa"):
                    if not line.startswith("Dialogue:"):
                        continue
                    fields = line.partition("Dialogue:")[2].split(",")
                    if len(fields) < 3:
                        continue
                    ta = _parse_ass_ts(fields[1])
                    tb = _parse_ass_ts(fields[2])
                else:
                    if "-->" not in line:
                        continue
                    left, _, right = line.partition("-->")
                    ta = _parse_srt_vtt_ts(left)
                    tb = _parse_srt_vtt_ts(right)
                if ta is None or tb is None:
                    continue
                count += 1
                if first is None or ta < first:
                    first = ta
                if last is None or tb > last:
                    last = tb
    except Exception as e:
        logger.warning("get_cue_stats failed for %s: %s", sub_path, e)
        return None
    if count == 0:
        return None
    return {"count": count, "first": first or 0.0, "last": last or 0.0}


# ═══════════════════════════════════════════════════════════
#   ffprobe (مدت + fps ویدیو)
# ═══════════════════════════════════════════════════════════

def _parse_fps(rate: str) -> Optional[float]:
    """'24000/1001' یا '25/1' یا '23.976' → float"""
    if not rate or rate in ("0/0", "N/A"):
        return None
    try:
        if "/" in rate:
            num, den = rate.split("/", 1)
            num_f, den_f = float(num), float(den)
            if den_f <= 0:
                return None
            return num_f / den_f
        return float(rate)
    except Exception:
        return None


def probe_video(video_path: str) -> Tuple[Optional[float], Optional[float]]:
    """
    (duration_seconds, fps) از ffprobe — هر کدوم نبود None.
    """
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", video_path],
            capture_output=True, timeout=60,
        )
        if proc.returncode != 0:
            return None, None
        info = json.loads(proc.stdout.decode("utf-8", errors="replace"))

        duration: Optional[float] = None
        fmt = info.get("format") or {}
        try:
            duration = float(fmt.get("duration"))
        except (TypeError, ValueError):
            duration = None
        fps: Optional[float] = None
        for st in info.get("streams") or []:
            if st.get("codec_type") == "video":
                fps = _parse_fps(st.get("avg_frame_rate")) or _parse_fps(st.get("r_frame_rate")) or fps
                if duration is None:
                    try:
                        duration = float(st.get("duration"))
                    except (TypeError, ValueError):
                        pass
        return duration, fps
    except Exception as e:
        logger.warning("probe_video failed for %s: %s", video_path, e)
        return None, None


# ═══════════════════════════════════════════════════════════
#   موتور تصمیم (pure — تست‌پذیر)
# ═══════════════════════════════════════════════════════════

def _fps_candidates(fps: float) -> List[Tuple[float, str]]:
    """
    لیست ضریب‌های کاندید بر اساس fps ویدیو — به‌ترتیب اولویت (t_v = f × t_s):
      - ویدیو 25/50fps → محتوا PAL-spun شده؛ ساب برای 23.976/24 تایم شده → f < 1
      - ویدیو fps غیراستاندارد → speedup دستی سرور؛ f = fps_اصلی / fps_ویدیو
      - ویدیو 23.976/24 و ساب PAL-timed → f > 1
    """
    cands: List[Tuple[float, str]] = []
    if 24.6 <= fps <= 25.4 or 49.2 <= fps <= 50.8:
        cands.append((_FPS_23976 / 25.0, "ویدیو PAL-spun 25fps، ساب 23.976-timed"))
        cands.append((24.0 / 25.0, "ویدیو PAL-spun 25fps، ساب 24-timed"))
    elif 24.2 < fps < 24.6 or 25.4 < fps < 29.5 or 30.5 < fps < 49.2 or fps > 50.8:
        orig = min(_ORIG_FPS_VALUES, key=lambda v: abs(v - fps))
        if abs(orig - fps) / fps > 0.004:  # فقط وقتی واقعاً غیراستاندارده
            cands.append((orig / fps, f"speedup دستی سرور (fps={fps:.2f} ← {orig:.3f})"))
    elif 29.5 <= fps <= 30.5:
        cands.append((_FPS_23976 / _FPS_2997, "ویدیو NTSC-spun 29.97fps"))
        cands.append((24.0 / _FPS_2997, "ویدیو NTSC-spun (ساب 24-timed)"))
    elif 23.7 <= fps <= 24.2:
        cands.append((25.0 / _FPS_23976, "ساب PAL-timed روی ویدیو 23.976"))
        cands.append((25.0 / 24.0, "ساب 25-timed روی ویدیو 24"))
    return cands


def decide_sync_factor(
    duration: Optional[float],
    fps: Optional[float],
    first: float,
    last: float,
    cue_count: int,
) -> Tuple[float, str]:
    """
    ضریب scale زیرنویس رو تصمیم می‌گیره.  1.0 = دست نزن.
    منطق:
      1) گاردهای داده (تعداد cue، طول، پوشش)
      2) اگه ساب خودش می‌خونه (فاصله‌ی آخرین cue تا پایان ≤ 45s) → 1.0
      3) کاندیدهای fps-محور؛ اعتبارسنجی: فضای کردیت باقیمانده C = D − L×f
         باید توی بازه‌ی منطقی باشه (−15s تا 15% طول ویدیو) و درِیف ≥ 8s
    """
    if cue_count < _MIN_CUES:
        return 1.0, f"cue کم است ({cue_count})"
    if not duration or duration < _MIN_DURATION_S:
        return 1.0, f"کلیپ کوتاه است ({int(duration or 0)}s)"
    if not last or last <= 0:
        return 1.0, "زیرنویس بدون cue معتبر"
    if last < duration * _MIN_COVERAGE:
        return 1.0, f"زیرنویس فقط {100 * last / duration:.0f}% ویدیو رو پوشش می‌ده (نسخه‌ی متفاوت)"

    gap1 = duration - last
    if _MIN_END_SLACK_S <= gap1 <= _FIT_TOLERANCE_S:
        return 1.0, f"زیرنویس از قبل جفت است (gap={gap1:.0f}s)"

    if not fps or fps <= 0:
        return 1.0, f"fps ویدیو نامشخص (gap={gap1:.0f}s) — بدون تغییر"

    for f, why in _fps_candidates(fps):
        if abs(f - 1.0) < 0.0015:
            continue
        drift = last * abs(1.0 - f)
        if drift < _MIN_DRIFT_S:
            continue
        credits = duration - last * f
        if credits < _MIN_END_SLACK_S or credits > duration * _MAX_CREDITS_FRAC:
            continue
        return f, (f"{why} | درِیف≈{drift:.0f}s، کِردیت≈{credits:.0f}s")

    return 1.0, f"هیچ ضریبی با اطمینان کافی پیدا نشد (fps={fps:.2f}, gap={gap1:.0f}s) — بدون تغییر"


# ═══════════════════════════════════════════════════════════
#   API اصلی
# ═══════════════════════════════════════════════════════════

def auto_sync_subtitle(
    video_path: str,
    sub_path: str,
    sync_info: Optional[Dict] = None,
) -> Tuple[str, float, str]:
    """
    همگام‌سازی خودکار زیرنویس با ویدیو.

    Returns:
        (مسیر زیرنویس نهایی, ضریب اعمال‌شده, دلیل/توضیح)
        ضریب 1.0 یعنی دست نخورد. هر خطایی → فایل اصلی برگردونده می‌شه.
    """
    if sync_info is None:
        sync_info = {}

    def _fill(applied: bool, factor: float, reason: str, **extra):
        sync_info.clear()
        sync_info.update({
            "applied": applied, "factor": factor, "reason": reason,
            "video": video_path, "sub": sub_path, **extra,
        })

    try:
        if os.environ.get("SUB_AUTOSYNC", "1").strip().lower() in ("0", "false", "no", "off"):
            _fill(False, 1.0, "غیرفعال (SUB_AUTOSYNC=0)")
            return sub_path, 1.0, "auto-sync غیرفعال است"

        if not video_path or not os.path.exists(video_path) or not sub_path or not os.path.exists(sub_path):
            _fill(False, 1.0, "فایل ویدیو/زیرنویس در دسترس نیست")
            return sub_path, 1.0, "فایل در دسترس نیست"

        ext = os.path.splitext(sub_path)[1].lower()
        if ext not in _SUPPORTED_EXT:
            _fill(False, 1.0, f"فرمت {ext} پشتیبانی نمی‌شود")
            return sub_path, 1.0, f"فرمت {ext} پشتیبانی نمی‌شود"

        duration, fps = probe_video(video_path)
        stats = get_cue_stats(sub_path)
        if not stats:
            _fill(False, 1.0, "پارس زیرنویس ناموفق")
            return sub_path, 1.0, "پارس زیرنویس ناموفق"

        factor, reason = decide_sync_factor(
            duration, fps, stats["first"], stats["last"], int(stats["count"]),
        )
        logger.info(
            "[z34] sub-sync decision: video=%s dur=%.1fs fps=%s | sub cues=%d first=%.1fs last=%.1fs "
            "| factor=%.5f (%s)",
            os.path.basename(video_path), duration or 0, fps, stats["count"],
            stats["first"], stats["last"], factor, reason,
        )

        if abs(factor - 1.0) < 1e-6:
            _fill(False, 1.0, reason, duration=duration, fps=fps, cues=stats["count"])
            return sub_path, 1.0, reason

        new_path = rescale_subtitle_file(sub_path, factor)
        if not new_path:
            _fill(False, 1.0, "بازنویسی زیرنویس شکست خورد", duration=duration, fps=fps)
            return sub_path, 1.0, "بازنویسی زیرنویس شکست خورد"

        _fill(True, factor, reason, duration=duration, fps=fps, cues=stats["count"],
              first=stats["first"], last=stats["last"], synced_file=new_path)
        logger.info("[z34] subtitle RESYNCED ×%.5f → %s", factor, os.path.basename(new_path))
        return new_path, factor, reason

    except Exception as e:
        logger.warning("[z34] auto_sync_subtitle error (non-fatal): %s", e, exc_info=True)
        try:
            _fill(False, 1.0, f"خطا: {e}")
        except Exception:
            pass
        return sub_path, 1.0, f"خطا: {e}"
