# ================== Dockerfile for Railway.app ==================
# Image پایه پایتون سبک
FROM python:3.12-slim

# نصب وابستگی‌های سیستم (ffmpeg + curl + chromium deps برای playwright)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    # ─── chromium deps برای playwright (بدون نصب chromium کامل) ───
    fonts-liberation \
    libnss3 \
    libatk-bridge2.0-0 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libxshmfence1 \
    libxss1 \
    libgtk-3-0 \
    # ─── وابستگی‌های curl_cffi (libcurl-impersonate) ───
    libcurl4 \
    libbrotli1 \
    libzstd1 \
    && rm -rf /var/lib/apt/lists/*

# تنظیمات محیطی
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PLAYWRIGHT_SKIP_FFMPEG_INSTALL=1
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# محدودیت RAM برای Python GC
ENV PYTHONMALLOC=malloc
ENV MALLOC_TRIM_THRESHOLD_=65536

WORKDIR /app

# نصب پکیج‌های پایتون
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir yt-dlp[default,curl-cffi] && \
    playwright install chromium --with-deps

# کپی کد ربات
COPY bot.py .
COPY FastTelethon.py .
COPY github.py .
COPY savep_handler.py .
COPY snapwc_handler.py .
COPY y2mate.py .
COPY youtube_extractor.py .
COPY happyscribe_subtitle.py .
COPY xnxx_handler.py .
COPY ytdlp_handler.py .
COPY subtitle_extractor.py .
COPY telegram_subtitle_handler.py .
COPY uplod_ir_handler.py .
COPY otherwebsiteshandler/ otherwebsiteshandler/
COPY searcher/ searcher/

# ایجاد پوشه خروجی با دسترسی کامل
RUN mkdir -p output_files && chmod -R 777 output_files

EXPOSE 10000

# اجرای ربات
CMD ["python", "-u", "bot.py"]
