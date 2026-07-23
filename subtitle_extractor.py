"""
subtitle_extractor.py

استخراج تمام سافت‌ساب‌های (soft subtitles) موجود در یک فایل ویدیویی
(مثلاً mkv, mp4, avi و ...) با استفاده از ffmpeg/ffprobe.

نیازمندی:
    - نصب بودن ffmpeg و ffprobe روی سیستم و در PATH
      (دانلود: https://ffmpeg.org/download.html)

استفاده به‌عنوان ماژول:
    from subtitle_extractor import extract_subtitles
    extract_subtitles("movie.mkv", "subs_output")

استفاده مستقیم از خط فرمان:
    python subtitle_extractor.py movie.mkv [output_dir]
"""

import json
import os
import subprocess
import sys


# کدک‌های متنی که می‌تونیم مستقیم به srt تبدیلشون کنیم
TEXT_BASED_CODECS = {
    "subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text"
}

# کدک‌های تصویری (bitmap) که تبدیل به srt بدون OCR ممکن نیست
# پس همون فرمت اصلی نگه داشته می‌شن
IMAGE_BASED_CODECS = {
    "hdmv_pgs_subtitle": "sup",
    "dvd_subtitle": "sub",
    "dvb_subtitle": "sub",
}


def _run(cmd):
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )


def get_subtitle_streams(video_path):
    """
    با ffprobe لیست تمام استریم‌های زیرنویس داخل فایل رو برمی‌گردونه.
    خروجی: لیستی از دیکشنری‌ها شامل index، codec_name، language، title
    """
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-select_streams", "s",
        video_path,
    ]
    result = _run(cmd)
    data = json.loads(result.stdout)
    streams = data.get("streams", [])

    subs = []
    for s in streams:
        tags = s.get("tags", {}) or {}
        subs.append({
            "index": s["index"],
            "codec_name": s.get("codec_name", "unknown"),
            "language": tags.get("language", "und"),
            "title": tags.get("title", ""),
        })
    return subs


def extract_subtitles(video_path, output_dir=None):
    """
    تمام سافت‌ساب‌های موجود در video_path رو استخراج و ذخیره می‌کنه.
    برمی‌گردونه: لیستی از مسیر فایل‌های خروجی
    """
    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"فایل پیدا نشد: {video_path}")

    if output_dir is None:
        output_dir = os.path.join(
            os.path.dirname(os.path.abspath(video_path)),
            "extracted_subs"
        )
    os.makedirs(output_dir, exist_ok=True)

    base_name = os.path.splitext(os.path.basename(video_path))[0]
    subs = get_subtitle_streams(video_path)

    if not subs:
        print("هیچ سافت‌سابی در این فایل پیدا نشد.")
        return []

    output_files = []

    for sub_order, sub in enumerate(subs):
        codec = sub["codec_name"]
        lang = sub["language"]
        title = sub["title"]

        # ساخت اسم فایل خروجی
        suffix_parts = [lang]
        if title:
            safe_title = "".join(c for c in title if c.isalnum() or c in " -_").strip()
            if safe_title:
                suffix_parts.append(safe_title)
        suffix = "_".join(suffix_parts) if suffix_parts else str(sub_order)

        if codec in TEXT_BASED_CODECS:
            ext = "srt"
            out_codec = "srt"
        elif codec in IMAGE_BASED_CODECS:
            ext = IMAGE_BASED_CODECS[codec]
            out_codec = "copy"
        else:
            # کدک ناشناخته -> فقط copy کن با پسوند عمومی
            ext = "sub"
            out_codec = "copy"

        out_filename = f"{base_name}_sub{sub_order}_{suffix}.{ext}"
        out_path = os.path.join(output_dir, out_filename)

        cmd = [
            "ffmpeg",
            "-y",
            "-i", video_path,
            "-map", f"0:{sub['index']}",
            "-c:s", out_codec,
            out_path,
        ]

        print(f"در حال استخراج stream #{sub['index']} (زبان: {lang}, کدک: {codec}) ...")
        try:
            _run(cmd)
            output_files.append(out_path)
            print(f"  ذخیره شد: {out_path}")
        except subprocess.CalledProcessError as e:
            print(f"  خطا در استخراج این زیرنویس: {e.stderr}")

    return output_files


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("استفاده: python subtitle_extractor.py <video_file> [output_dir]")
        sys.exit(1)

    video_file = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) > 2 else None

    result_files = extract_subtitles(video_file, out_dir)

    print(f"\nتعداد {len(result_files)} فایل زیرنویس استخراج شد.")
