#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
هندلر آپلود فایل به سایت uplod.ir
---------------------------------
- انتخاب فایل از طریق کلیک روی دکمه "انتخاب فایل"
- کلیک روی دکمه "شروع اپلود"
- نمایش درصد پیشرفت و سرعت آپلود به صورت زنده
- استخراج لینک دانلود نهایی

استفاده:
    python3 uplod_ir_handler.py <file_path> [--headed] [--timeout 600] [--log out.log]

نیازمندی:
    pip install playwright
    playwright install chromium
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Optional

try:
    from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout
except ImportError:
    print("[ERR] playwright نصب نیست. ابتدا اجرا کنید:")
    print("  pip install playwright && playwright install chromium")
    sys.exit(1)


# ---------- ثابت‌های سایت ----------
SITE_URL = "https://uplod.ir/"
UPLOAD_PAGE = "https://uplod.ir/"
FILE_INPUT_SELECTOR = "input#file_0"
START_UPLOAD_BTN_TEXT = "شروع آپلود"          # دکمه شروع آپلود
PROGRESS_DIV_SELECTOR = ".progress_div"
PROGRESS_BAR_INNER = ".progressbar-inner"
PROGRESS_COMPLETED = ".progressbar-completed"
PROGRESS_SPEED = ".progressbar-speed"
ABORT_LINK_TEXT = "Abort"

# ---------- رنگ‌های ترمینال ----------
class C:
    R = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GRN = "\033[32m"
    YLW = "\033[33m"
    BLU = "\033[34m"
    MAG = "\033[35m"
    CYN = "\033[36m"
    WHT = "\033[37m"


# ---------- توابع کمکی ----------
def human_size(n: float) -> str:
    """تبدیل بایت به واحد خوانا."""
    if n is None:
        return "?"
    n = float(n)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024.0:
            return f"{n:.2f} {unit}"
        n /= 1024.0
    return f"{n:.2f} PB"


def render_progress_bar(pct: float, width: int = 30) -> str:
    """رسم نوار پیشرفت."""
    if pct < 0: pct = 0
    if pct > 100: pct = 100
    filled = int(width * pct / 100)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}]"


def log(msg: str, level: str = "INFO", color: str = C.WHT, log_file=None):
    """لاگ رنگی ترمینال + فایل."""
    line = f"[{level}] {msg}"
    print(f"{color}{line}{C.R}", flush=True)
    if log_file:
        try:
            log_file.write(line + "\n")
            log_file.flush()
        except Exception:
            pass


# ---------- کلاس اصلی هندلر ----------
class UplodHandler:
    def __init__(
        self,
        headed: bool = False,
        timeout: int = 600,
        log_file=None,
        verbose: bool = False,
    ):
        self.headed = headed
        self.timeout = timeout
        self.log_file = log_file
        self.verbose = verbose
        self.result: Optional[dict] = None

    # ---- لاگ داخلی ----
    def _log(self, msg, level="INFO", color=C.WHT):
        log(msg, level, color, self.log_file)

    # ---- اجرای اصلی ----
    def upload(self, file_path: str) -> dict:
        file_path = str(Path(file_path).resolve())
        if not Path(file_path).is_file():
            raise FileNotFoundError(f"فایل پیدا نشد: {file_path}")

        file_size = Path(file_path).stat().st_size
        self._log(f"شروع هندلر آپلود uplod.ir", "INFO", C.CYN)
        self._log(f"فایل: {file_path}", "INFO", C.WHT)
        self._log(f"حجم: {human_size(file_size)}", "INFO", C.WHT)
        self._log(f"مرورگر: {'نمایشی' if self.headed else 'بدون سر'}", "INFO", C.DIM)

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=not self.headed,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/121.0.0.0 Safari/537.36"
                ),
                locale="fa-IR",
                timezone_id="Asia/Tehran",
            )
            # مخفی کردن نشانه‌های اتوماسیون
            context.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            )
            page = context.new_page()
            page.set_default_timeout(self.timeout * 1000)

            try:
                result = self._run_flow(page, file_path, file_size)
            finally:
                # ذخیره عکس صفحه نهایی برای دیباگ
                try:
                    snapshot_path = "/home/z/my-project/scripts/last_state.png"
                    page.screenshot(path=snapshot_path, full_page=False)
                    self._log(f"عکس وضعیت نهایی: {snapshot_path}", "DBG", C.DIM)
                except Exception:
                    pass
                context.close()
                browser.close()

        self.result = result
        return result

    # ---- جریان اصلی ----
    def _run_flow(self, page: Page, file_path: str, file_size: int) -> dict:
        # 1) باز کردن سایت
        self._log(f"باز کردن سایت: {SITE_URL}", "STEP", C.YLW)
        try:
            page.goto(SITE_URL, wait_until="domcontentloaded", timeout=60_000)
        except PWTimeout:
            raise RuntimeError("Timeout در باز کردن سایت - احتمالاً سایت در دسترس نیست")

        # بررسی 403
        if "403" in page.title() or "Forbidden" in page.title():
            raise RuntimeError(
                "سایت 403 Forbidden برمی‌گرداند - احتمالاً IP شما بلاک شده"
            )

        self._log(f"عنوان صفحه: {page.title()}", "INFO", C.GRN)

        # 2) انتظار برای input فایل
        self._log("جستجوی دکمه «انتخاب فایل»...", "STEP", C.YLW)
        try:
            page.wait_for_selector(FILE_INPUT_SELECTOR, state="attached", timeout=30_000)
        except PWTimeout:
            # شاید لازم باشد روی تب «آپلود فایل» کلیک شود
            try:
                page.click("#select_file", timeout=5_000)
                page.wait_for_selector(FILE_INPUT_SELECTOR, state="attached", timeout=15_000)
            except Exception:
                raise RuntimeError("دکمه انتخاب فایل پیدا نشد")

        # اسکرول به محل دکمه
        try:
            page.evaluate(
                "document.querySelector('#file_0').scrollIntoView({block:'center'})"
            )
        except Exception:
            pass

        # 3) تنظیم فایل روی input (معادل کلیک و انتخاب)
        self._log(f"انتخاب فایل: {Path(file_path).name}", "STEP", C.YLW)
        page.set_input_files(FILE_INPUT_SELECTOR, file_path)

        # 4) انتظار برای ظاهر شدن دکمه «شروع اپلود»
        self._log("صبر برای ظاهر شدن دکمه «شروع اپلود»...", "STEP", C.YLW)
        start_btn = None
        for attempt in range(30):  # 15 ثانیه
            try:
                # دکمه شروع آپلود به صورت dynamic ساخته می‌شود
                start_btn = page.query_selector(
                    f'#upload_controls input[value="{START_UPLOAD_BTN_TEXT}"]'
                )
                if start_btn and start_btn.is_visible():
                    break
            except Exception:
                pass
            time.sleep(0.5)
        else:
            # fallback: جستجو با متن
            try:
                start_btn = page.get_by_role("button", name=START_UPLOAD_BTN_TEXT)
            except Exception:
                pass

        if not start_btn:
            # شاید فایل انتخاب نشده یا رد شده
            self._dump_page_state(page)
            raise RuntimeError("دکمه «شروع اپلود» ظاهر نشد - احتمالاً فایل رد شده است")

        self._log("دکمه «شروع اپلود» پیدا شد", "OK", C.GRN)

        # 5) کلیک شروع آپلود
        self._log("کلیک روی «شروع اپلود» ...", "STEP", C.YLW)
        # ثبت هدر response برای گرفتن لینک نهایی
        final_urls = []
        def on_response(resp):
            try:
                u = resp.url
                if "uplod.ir" in u and ("upload" in u or "files" in u or "?" in u):
                    final_urls.append((resp.status, u))
            except Exception:
                pass
        page.on("response", on_response)

        try:
            start_btn.click()
        except Exception as e:
            # fallback: کلیک با JS
            page.evaluate(
                f"""() => {{
                    const btn = document.querySelector('#upload_controls input[value="{START_UPLOAD_BTN_TEXT}"]');
                    if (btn) btn.click();
                }}"""
            )

        # 6) مانیتورینگ پیشرفت
        self._log("شروع مانیتورینگ پیشرفت...", "STEP", C.YLW)
        result = self._monitor_progress(page, file_size, file_path)

        # 7) استخراج لینک نهایی
        self._log("استخراج لینک دانلود...", "STEP", C.YLW)
        link = self._extract_download_link(page, final_urls)
        result["download_link"] = link
        return result

    # ---- مانیتورینگ درصد + سرعت ----
    def _monitor_progress(self, page: Page, total_size: int, file_path: str) -> dict:
        start_ts = time.time()
        last_pct = -1.0
        last_speed = ""
        last_completed = ""
        max_pct = 0.0
        max_speed_bps = 0.0
        stable_finish_count = 0
        result = {
            "file": file_path,
            "size_bytes": total_size,
            "size_human": human_size(total_size),
            "duration_sec": 0,
            "max_percent": 0,
            "max_speed_bps": 0,
            "average_speed_bps": 0,
            "abort": False,
        }

        # تایم‌اوت کلی
        deadline = start_ts + self.timeout
        while time.time() < deadline:
            try:
                # خواندن درصد از width نوار پیشرفت
                pct = -1.0
                try:
                    inner = page.query_selector(PROGRESS_BAR_INNER)
                    if inner:
                        style = inner.get_attribute("style") or ""
                        m = re.search(r"width:\s*([0-9.]+)", style)
                        if m:
                            pct = float(m.group(1))
                        # اگر به صورت درصد نبود و عرض پیکسل بود، نسبت بگیر
                        elif "px" in style:
                            outer = page.query_selector(".progressbar-outer")
                            if outer:
                                box = outer.bounding_box()
                                mpx = re.search(r"width:\s*([0-9.]+)px", style)
                                if mpx and box and box["width"] > 0:
                                    pct = float(mpx.group(1)) / box["width"] * 100
                except Exception:
                    pass

                # خواندن متن completed و speed
                try:
                    comp_el = page.query_selector(PROGRESS_COMPLETED)
                    completed_text = comp_el.inner_text() if comp_el else ""
                except Exception:
                    completed_text = ""
                try:
                    spd_el = page.query_selector(PROGRESS_SPEED)
                    speed_text = spd_el.inner_text() if spd_el else ""
                except Exception:
                    speed_text = ""

                # تخمین درصد اگر پیدا نبود
                if pct < 0 and completed_text:
                    m = re.search(r"([\d.]+)\s*(B|KB|MB|GB)\s+of\s+([\d.]+)\s*(B|KB|MB|GB)",
                                  completed_text, re.I)
                    if m:
                        loaded = float(m.group(1))
                        lu = m.group(2).upper()
                        total = float(m.group(3))
                        tu = m.group(4).upper()
                        mult = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}
                        loaded_b = loaded * mult[lu]
                        total_b = total * mult[tu]
                        if total_b > 0:
                            pct = loaded_b / total_b * 100

                # اگر درصد معتبر است
                if pct >= 0:
                    if pct > max_pct:
                        max_pct = pct
                    # تخمین سرعت از متن (مثلا "Upload speed: 1.2 MB/s")
                    spd_bps = self._parse_speed(speed_text)
                    if spd_bps is not None and spd_bps > max_speed_bps:
                        max_speed_bps = spd_bps

                    # نمایش خط زنده
                    if pct != last_pct or speed_text != last_speed:
                        self._print_live(pct, speed_text, completed_text, start_ts)
                        last_pct = pct
                        last_speed = speed_text
                        last_completed = completed_text

                    # اتمام؟
                    if pct >= 99.5:
                        stable_finish_count += 1
                        if stable_finish_count >= 3:
                            self._print_live(100.0, speed_text, completed_text, start_ts)
                            self._log("آپلود به 100٪ رسید", "OK", C.GRN)
                            # صبر برای redirect صفحه به صفحه نتیجه
                            self._wait_for_redirect(page, deadline)
                            break
                    else:
                        stable_finish_count = 0
                else:
                    # شاید صفحه redirect شده یا آپلود سریع تمام شده
                    cur_url = page.url
                    if "st=" in cur_url and "fn=" in cur_url:
                        self._log("آپلود کامل شد (redirect تشخیص داده شد)", "OK", C.GRN)
                        break
                    # یا صفحه نتیجه بارگذاری شده
                    try:
                        if page.query_selector('.dlurl') or page.query_selector('a[href*="/"]'):
                            self._log("صفحه نتیجه تشخیص داده شد", "OK", C.GRN)
                            break
                    except Exception:
                        pass
                    # یا متن abort ظاهر شده
                    try:
                        if page.query_selector(PROGRESS_BAR_INNER) is None:
                            time.sleep(0.3)
                            continue
                    except Exception:
                        pass

            except Exception as e:
                if self.verbose:
                    self._log(f"warn: {e}", "WRN", C.YLW)

            time.sleep(0.4)

        duration = time.time() - start_ts
        result["duration_sec"] = round(duration, 2)
        result["max_percent"] = round(max_pct, 2)
        result["max_speed_bps"] = round(max_speed_bps, 2)
        if duration > 0 and max_pct > 0:
            # سرعت میانگین تقریبی
            result["average_speed_bps"] = round(total_size * (max_pct / 100) / duration, 2)

        # لاگ نهایی
        self._log(
            f"پایان مانیتورینگ: max={result['max_percent']}% "
            f"avg_speed={human_size(result['average_speed_bps'])}/s "
            f"duration={duration:.1f}s",
            "INFO", C.CYN
        )
        return result

    def _wait_for_redirect(self, page: Page, deadline: float, max_wait: float = 15.0):
        """پس از رسیدن به 100٪، صبر می‌کند تا صفحه به صفحه نتیجه redirect شود."""
        start = time.time()
        initial_url = page.url
        self._log("صبر برای redirect به صفحه نتیجه...", "INFO", C.DIM)
        while time.time() - start < max_wait and time.time() < deadline:
            try:
                cur = page.url
                if cur != initial_url:
                    self._log(f"صفحه به {cur} منتقل شد", "OK", C.GRN)
                    return True
                # چک کردن وجود المان‌های صفحه نتیجه
                try:
                    if page.query_selector('.dlurl'):
                        self._log("صفحه نتیجه بارگذاری شد", "OK", C.GRN)
                        return True
                except Exception:
                    pass
            except Exception:
                pass
            time.sleep(0.3)
        self._log("redirect در زمان مقرر انجام نشد", "WRN", C.YLW)
        return False

    def _parse_speed(self, text: str) -> Optional[float]:
        """Parse 'Upload speed: 1.5 MB/s' -> bytes/sec."""
        if not text:
            return None
        m = re.search(r"([\d.]+)\s*(B|KB|MB|GB)/s", text, re.I)
        if not m:
            return None
        v = float(m.group(1))
        u = m.group(2).upper()
        mult = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}
        return v * mult[u]

    def _print_live(self, pct: float, speed_text: str, completed_text: str, start_ts: float):
        """چاپ زنده خط پیشرفت."""
        elapsed = time.time() - start_ts
        bar = render_progress_bar(pct, 30)
        # فیلتر کردن speed text
        spd = speed_text.replace("Upload speed:", "").strip() if speed_text else ""
        comp = completed_text.replace("of", "از").strip() if completed_text else ""
        line = (
            f"\r{C.CYN}{bar}{C.R} "
            f"{C.BOLD}{pct:5.1f}%{C.R}  "
            f"{C.GRN}{comp}{C.R}  "
            f"{C.MAG}{spd}{C.R}  "
            f"{C.DIM}t={elapsed:5.1f}s{C.R}"
        )
        sys.stdout.write(line)
        sys.stdout.flush()
        if self.verbose:
            sys.stdout.write("\n")

    # ---- استخراج لینک دانلود ----
    def _extract_download_link(self, page: Page, final_urls: list) -> str:
        cur_url = page.url
        self._log(f"URL نهایی: {cur_url}", "INFO", C.DIM)

        # 1) جستجوی لینک در URL (مثلاً ?st=OK&fn=xxxx)
        m = re.search(r"[?&]fn=([^&]+)", cur_url)
        if m:
            file_code = m.group(1)
            link = f"https://uplod.ir/{file_code}"
            self._log(f"کد فایل: {file_code}", "OK", C.GRN)
            return link

        # 2) جستجو در صفحه
        try:
            # ممکن است لینک در صفحه نمایش داده شود
            selectors = [
                'input.dlurl',
                'input[name="fn"]',
                '.direct_link a',
                'a[href*="uplod.ir/"]',
                'input[type="text"]',
            ]
            for sel in selectors:
                try:
                    el = page.query_selector(sel)
                    if el:
                        val = el.get_attribute("value") or el.get_attribute("href") or ""
                        if not val:
                            continue
                        # اگه مستقیم URL بود
                        if "uplod.ir/" in val:
                            mm = re.search(r"uplod\.ir/([a-z0-9]{8,})", val, re.I)
                            if mm:
                                return f"https://uplod.ir/{mm.group(1)}"
                            return val
                        # اگه فقط کد فایل بود
                        if re.match(r"^[a-z0-9]{10,}$", val, re.I):
                            return f"https://uplod.ir/{val}"
                except Exception:
                    continue
        except Exception:
            pass

        # 3) جستجو در URLهای response (شامل s6.uplod.ir و mock endpoint)
        for status, u in reversed(final_urls):
            if status == 200 and "fn=" in u:
                m = re.search(r"fn=([^&]+)", u)
                if m:
                    return f"https://uplod.ir/{m.group(1)}"

        # 4) جستجو در محتوای صفحه
        try:
            body = page.content()
            # الگوی کد فایل: 12 کاراکتر hex
            m = re.search(r'(https?://[^\s"\']+uplod\.ir/[a-z0-9]{8,})', body, re.I)
            if m:
                return m.group(1)
            # یا فقط کد 12 hex
            m = re.search(r'\b([a-f0-9]{12})\b', body)
            if m:
                # فقط در صورتی که در input.dlurl یا context مناسب باشد
                if 'dlurl' in body or 'file_code' in body:
                    return f"https://uplod.ir/{m.group(1)}"
        except Exception:
            pass

        return ""

    # ---- دیباگ ----
    def _dump_page_state(self, page: Page):
        try:
            self._log(f"URL: {page.url}", "DBG", C.DIM)
            self._log(f"title: {page.title()}", "DBG", C.DIM)
            # چک کردن alert/error
            body_text = page.inner_text("body")[:500]
            self._log(f"body excerpt: {body_text}", "DBG", C.DIM)
        except Exception:
            pass


# ---------- CLI ----------
def main():
    parser = argparse.ArgumentParser(
        description="هندلر آپلود فایل به uplod.ir",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
مثال:
  python3 uplod_ir_handler.py /path/to/file.zip
  python3 uplod_ir_handler.py movie.mp4 --headed
  python3 uplod_ir_handler.py file.zip --log upload.log --timeout 1200
""",
    )
    parser.add_argument("file", help="مسیر فایل برای آپلود")
    parser.add_argument("--headed", action="store_true", help="نمایش مرورگر")
    parser.add_argument("--timeout", type=int, default=900, help="تایم‌اوت کل به ثانیه")
    parser.add_argument("--log", help="فایل لاگ", default=None)
    parser.add_argument("--json", help="ذخیره نتیجه در فایل JSON", default=None)
    parser.add_argument("--verbose", "-v", action="store_true", help="خروجی پرجزئیات")
    args = parser.parse_args()

    log_fh = open(args.log, "a", encoding="utf-8") if args.log else None

    try:
        handler = UplodHandler(
            headed=args.headed,
            timeout=args.timeout,
            log_file=log_fh,
            verbose=args.verbose,
        )
        result = handler.upload(args.file)
    except KeyboardInterrupt:
        print("\n[BYE] متوقف شد")
        if log_fh: log_fh.close()
        sys.exit(130)
    except Exception as e:
        log(f"خطا: {e}", "ERR", C.RED, log_fh)
        if log_fh: log_fh.close()
        sys.exit(1)
    finally:
        if log_fh: log_fh.close()

    # چاپ نتیجه نهایی
    print()
    print(f"{C.BOLD}{C.CYN}════════════════════════════════════════{C.R}")
    print(f"{C.BOLD}گزارش نهایی آپلود{C.R}")
    print(f"{C.BOLD}{C.CYN}════════════════════════════════════════{C.R}")
    print(f"  فایل          : {result.get('file')}")
    print(f"  حجم           : {result.get('size_human')}")
    print(f"  درصد نهایی    : {result.get('max_percent')}%")
    print(f"  مدت زمان      : {result.get('duration_sec')} ثانیه")
    print(f"  سرعت میانگین  : {human_size(result.get('average_speed_bps',0))}/s")
    print(f"  سرعت حداکثر   : {human_size(result.get('max_speed_bps',0))}/s")
    link = result.get('download_link','')
    if link:
        print(f"  {C.BOLD}{C.GRN}لینک دانلود   : {link}{C.R}")
    else:
        print(f"  {C.YLW}لینک دانلود پیدا نشد - صفحه را دستی چک کنید{C.R}")
    print()

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"{C.DIM}نتیجه در {args.json} ذخیره شد{C.R}")

    return 0 if link else 2


if __name__ == "__main__":
    sys.exit(main())
