#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
هندلر آپلود فایل به سایت uplod.ir (نسخه ضد-تشخیص)
-------------------------------------------------
- استفاده از Playwright با Stealth Mode کامل
- هدرهای واقعی مرورگر شامل Client Hints (Sec-Ch-Ua, Sec-Fetch-*)
- Pre-warm صفحه برای حل چالش JS  و دریافت کوکی session
- Persistent Context برای حفظ کوکی‌ها بین اجراها
- شبیه‌سازی رفتار کاربر (حرکت موس، scroll)
- Retry logic با fallback

استفاده:
    python3 uplod_ir_handler.py <file_path> [--headed] [--timeout 600] [--log out.log]

نیازمندی:
    pip install playwright
    playwright install chromium
"""

import argparse
import json
import os
import re
import sys
import time
import random
from pathlib import Path
from typing import Optional

try:
    from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout, Response
except ImportError:
    print("[ERR] playwright نصب نیست. ابتدا اجرا کنید:")
    print("  pip install playwright && playwright install chromium")
    sys.exit(1)


# ---------- ثابت‌های سایت ----------
SITE_URL = "https://uplod.ir/"
UPLOAD_PAGE = "https://uplod.ir/"
FILE_INPUT_SELECTOR = "input#file_0"
START_UPLOAD_BTN_TEXT = "شروع آپلود"          # دکمه شروع آپلود (ویرایش شده: آ ی)
PROGRESS_DIV_SELECTOR = ".progress_div"
PROGRESS_BAR_INNER = ".progressbar-inner"
PROGRESS_COMPLETED = ".progressbar-completed"
PROGRESS_SPEED = ".progressbar-speed"
ABORT_LINK_TEXT = "Abort"

# مسیر ذخیره پروفایل دائمی مرورگر (برای حفظ کوکی‌ها)
PROFILE_DIR = "/tmp/uplod_ir_profile"

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
    if n is None: return "?"
    n = float(n)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024.0:
            return f"{n:.2f} {unit}"
        n /= 1024.0
    return f"{n:.2f} PB"


def render_progress_bar(pct: float, width: int = 30) -> str:
    if pct < 0: pct = 0
    if pct > 100: pct = 100
    filled = int(width * pct / 100)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}]"


def log(msg: str, level: str = "INFO", color: str = C.WHT, log_file=None):
    line = f"[{level}] {msg}"
    print(f"{color}{line}{C.R}", flush=True)
    if log_file:
        try:
            log_file.write(line + "\n")
            log_file.flush()
        except Exception:
            pass


# ---------- اسکریپت Stealth کامل ----------
# این اسکریپت قبل از لود هر صفحه اجرا می‌شه تا نشونه‌های اتوماسیون رو پاک کنه
STEALTH_JS = r"""
() => {
    // 1) navigator.webdriver → undefined (مهم‌ترین نشانه)
    try {
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
            configurable: true
        });
    } catch(e) {}

    // 2) window.chrome object (نشان دهنده کروم واقعی)
    if (!window.chrome) {
        window.chrome = {
            runtime: {},
            loadTimes: function(){},
            csi: function(){},
            app: {},
        };
    }

    // 3) navigator.plugins (کروم واقعی حداقل 3 پلاگین داره)
    try {
        Object.defineProperty(navigator, 'plugins', {
            get: () => {
                const plugins = [
                    {name: 'Chrome PDF Plugin'},
                    {name: 'Chrome PDF Viewer'},
                    {name: 'Native Client'},
                ];
                plugins.length = 3;
                return plugins;
            },
            configurable: true
        });
    } catch(e) {}

    // 4) navigator.languages
    try {
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en', 'fa'],
            configurable: true
        });
    } catch(e) {}

    // 5) navigator.permissions - Patched to look normal
    try {
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) =>
            parameters.name === 'notifications'
                ? Promise.resolve({state: Notification.permission})
                : originalQuery(parameters);
    } catch(e) {}

    // 6) WebGL Vendor & Renderer (نشانه‌های کارت گرافیک واقعی)
    try {
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {
            if (parameter === 37445) return 'Intel Inc.';          // UNMASKED_VENDOR_WEBGL
            if (parameter === 37446) return 'Intel Iris OpenGL Engine'; // UNMASKED_RENDERER_WEBGL
            return getParameter.call(this, parameter);
        };
    } catch(e) {}

    // 7) navigator.hardwareConcurrency (تعداد هسته‌های واقعی)
    try {
        Object.defineProperty(navigator, 'hardwareConcurrency', {
            get: () => 8,
            configurable: true
        });
    } catch(e) {}

    // 8) navigator.deviceMemory
    try {
        Object.defineProperty(navigator, 'deviceMemory', {
            get: () => 8,
            configurable: true
        });
    } catch(e) {}

    // 9) navigator.platform (به جای Linux، Windows نشون بده)
    try {
        Object.defineProperty(navigator, 'platform', {
            get: () => 'Win32',
            configurable: true
        });
    } catch(e) {}

    // 10) navigator.userAgentData (Client Hints API)
    try {
        if (!navigator.userAgentData) {
            navigator.userAgentData = {
                brands: [
                    {brand: 'Not.A/Brand', version: '8'},
                    {brand: 'Chromium', version: '121'},
                    {brand: 'Google Chrome', version: '121'}
                ],
                mobile: false,
                platform: 'Windows',
                getHighEntropyValues: () => Promise.resolve({
                    architecture: 'x86',
                    bitness: '64',
                    fullVersionList: [
                        {brand: 'Not.A/Brand', version: '8'},
                        {brand: 'Chromium', version: '121'},
                        {brand: 'Google Chrome', version: '121'}
                    ],
                    mobile: false,
                    model: '',
                    platform: 'Windows',
                    platformVersion: '15.0.0',
                    uaFullVersion: '121.0.6167.85'
                })
            };
        }
    } catch(e) {}

    // 11) Hide automation in iframe contentWindow
    try {
        const elementDescriptor = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow');
        Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
            get: function() {
                const frame = this;
                const result = elementDescriptor.get.call(frame);
                if (result) {
                    try {
                        Object.defineProperty(result, 'chrome', {value: window.chrome});
                    } catch(e) {}
                }
                return result;
            }
        });
    } catch(e) {}

    // 12) Permissions API - mask notifications
    try {
        const originalQuery2 = navigator.permissions.query;
        navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications'
                ? Promise.resolve({state: Notification.permission})
                : originalQuery2(parameters)
        );
    } catch(e) {}

    // 13) navigator.connection (network info)
    try {
        if (!navigator.connection) {
            Object.defineProperty(navigator, 'connection', {
                value: {
                    effectiveType: '4g',
                    rtt: 50,
                    downlink: 10,
                    saveData: false
                },
                configurable: true
            });
        }
    } catch(e) {}

    // 14) Hide CDP runtime
    try {
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
    } catch(e) {}

    // 15) Mock toString to hide patches
    try {
        const originalToString = Function.prototype.toString;
        Function.prototype.toString = function() {
            if (this === window.navigator.permissions.query) {
                return 'function query() { [native code] }';
            }
            return originalToString.call(this);
        };
    } catch(e) {}

    // 16) navigator.vendor
    try {
        Object.defineProperty(navigator, 'vendor', {
            get: () => 'Google Inc.',
            configurable: true
        });
    } catch(e) {}

    // 17) navigator.maxTouchPoints (desktop browser = 0)
    try {
        Object.defineProperty(navigator, 'maxTouchPoints', {
            get: () => 0,
            configurable: true
        });
    } catch(e) {}

    // 18) window.outerWidth / outerHeight
    try {
        Object.defineProperty(window, 'outerWidth', {
            get: () => window.innerWidth + 16,
            configurable: true
        });
        Object.defineProperty(window, 'outerHeight', {
            get: () => window.innerHeight + 88,
            configurable: true
        });
    } catch(e) {}

    // 19) screen properties
    try {
        Object.defineProperty(screen, 'availWidth', {get: () => 1920, configurable: true});
        Object.defineProperty(screen, 'availHeight', {get: () => 1040, configurable: true});
        Object.defineProperty(screen, 'width', {get: () => 1920, configurable: true});
        Object.defineProperty(screen, 'height', {get: () => 1080, configurable: true});
        Object.defineProperty(screen, 'colorDepth', {get: () => 24, configurable: true});
        Object.defineProperty(screen, 'pixelDepth', {get: () => 24, configurable: true});
    } catch(e) {}
}
"""


# ---------- User-Agent و هدرهای واقعی ----------
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)

# هدرهای واقعی مرورگر - برای هر درخواست ست می‌شه
EXTRA_HTTP_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "en-US,en;q=0.9,fa;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "max-age=0",
    "Sec-Ch-Ua": '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Connection": "keep-alive",
}


# ---------- کلاس اصلی هندلر ----------
class UplodHandler:
    def __init__(
        self,
        headed: bool = False,
        timeout: int = 600,
        log_file=None,
        verbose: bool = False,
        persistent: bool = True,
    ):
        self.headed = headed
        self.timeout = timeout
        self.log_file = log_file
        self.verbose = verbose
        self.persistent = persistent
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
        self._log("شروع هندلر آپلود uplod.ir (Stealth Mode)", "INFO", C.CYN)
        self._log(f"فایل: {file_path}", "INFO", C.WHT)
        self._log(f"حجم: {human_size(file_size)}", "INFO", C.WHT)
        self._log(f"مرورگر: {'نمایشی' if self.headed else 'بدون سر'}", "INFO", C.DIM)
        self._log(f"Persistent: {self.persistent}", "INFO", C.DIM)

        # اطمینان از وجود پوشه پروفایل
        if self.persistent:
            os.makedirs(PROFILE_DIR, exist_ok=True)

        with sync_playwright() as p:
            # تلاش با کانال chrome واقعی اگه نصب باشه، در غیر این صورت chromium
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-infobars",
                "--window-position=0,0",
                "--ignore-certificates-errors",
                "--ignore-certificates-errors-spki-list",
                "--enable-features=NetworkService,NetworkServiceInProcess",
                "--disable-extensions",
                "--disable-default-apps",
                "--no-first-run",
                "--no-default-browser-check",
                "--password-store=basic",
                "--use-mock-keychain",
                "--enable-webgl",
                "--enable-precise-memory-info",
                "--lang=en-US,en",
                f"--user-agent={USER_AGENT}",
                "--remote-debugging-port=0",
            ]

            try:
                if self.persistent:
                    # استفاده از Persistent Context برای حفظ کوکی‌ها
                    self._log(f"استفاده از پروفایل دائمی: {PROFILE_DIR}", "INFO", C.DIM)
                    context = p.chromium.launch_persistent_context(
                        user_data_dir=PROFILE_DIR,
                        headless=not self.headed,
                        args=launch_args,
                        viewport={"width": 1920, "height": 1080},
                        screen={"width": 1920, "height": 1080},
                        user_agent=USER_AGENT,
                        locale="en-US",
                        timezone_id="Asia/Singapore",
                        color_scheme="light",
                        reduced_motion="no-preference",
                        java_script_enabled=True,
                        ignore_https_errors=True,
                        extra_http_headers=EXTRA_HTTP_HEADERS,
                    )
                    browser = None  # در persistent mode، context باید close بشه
                else:
                    raise Exception("Falling back to non-persistent")

            except Exception as e:
                self._log(f"Persistent mode ناموفق: {e} - استفاده از حالت معمولی", "WRN", C.YLW)
                browser = p.chromium.launch(
                    headless=not self.headed,
                    args=launch_args,
                )
                context = browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    screen={"width": 1920, "height": 1080},
                    user_agent=USER_AGENT,
                    locale="en-US",
                    timezone_id="Asia/Singapore",
                    color_scheme="light",
                    reduced_motion="no-preference",
                    java_script_enabled=True,
                    ignore_https_errors=True,
                    extra_http_headers=EXTRA_HTTP_HEADERS,
                )

            # اضافه کردن Stealth Script به context
            # این اسکریپت قبل از لود هر صفحه اجرا می‌شه
            context.add_init_script(STEALTH_JS)

            page = context.new_page()
            page.set_default_timeout(self.timeout * 1000)

            # اضافه کردن هدرهای اضافی به page level
            try:
                page.set_extra_http_headers(EXTRA_HTTP_HEADERS)
            except Exception:
                pass

            # شبیه‌سازی mouse و keyboard واقعی
            try:
                page.mouse.move(100, 100)
                page.mouse.move(200, 200, steps=10)
            except Exception:
                pass

            try:
                result = self._run_flow(page, file_path, file_size)
            finally:
                try:
                    snapshot_path = "/home/z/my-project/scripts/last_state.png"
                    if not os.path.exists(os.path.dirname(snapshot_path)):
                        snapshot_path = "/tmp/last_state.png"
                    page.screenshot(path=snapshot_path, full_page=False)
                    self._log(f"عکس وضعیت نهایی: {snapshot_path}", "DBG", C.DIM)
                except Exception:
                    pass
                # ذخیره کوکی‌ها برای دیباگ
                try:
                    cookies = context.cookies()
                    self._log(f"کوکی‌های session: {len(cookies)}", "DBG", C.DIM)
                    if self.verbose:
                        for c in cookies:
                            self._log(f"  - {c['name']}={c['value'][:30]}...", "DBG", C.DIM)
                except Exception:
                    pass
                context.close()
                if browser:
                    browser.close()

        self.result = result
        return result

    # ---- جریان اصلی ----
    def _run_flow(self, page: Page, file_path: str, file_size: int) -> dict:
        # 1) باز کردن سایت با retry و pre-warming
        if not self._open_site_with_retry(page):
            raise RuntimeError("سایت پس از چند تلاش قابل دسترس نبود (403/timeout)")

        # بررسی 403
        if "403" in page.title() or "Forbidden" in page.title():
            raise RuntimeError(
                "سایت 403 Forbidden برمی‌گرداند - احتمالاً IP شما بلاک شده"
            )

        self._log(f"عنوان صفحه: {page.title()}", "INFO", C.GRN)
        self._log(f"URL فعلی: {page.url}", "INFO", C.DIM)

        # 2) انتظار برای input فایل
        self._log("جستجوی دکمه «انتخاب فایل»...", "STEP", C.YLW)
        try:
            page.wait_for_selector(FILE_INPUT_SELECTOR, state="attached", timeout=30_000)
        except PWTimeout:
            try:
                page.click("#select_file", timeout=5_000)
                page.wait_for_selector(FILE_INPUT_SELECTOR, state="attached", timeout=15_000)
            except Exception:
                raise RuntimeError("دکمه انتخاب فایل پیدا نشد")

        try:
            page.evaluate(
                "document.querySelector('#file_0').scrollIntoView({block:'center'})"
            )
        except Exception:
            pass

        # حرکت موس به محل دکمه برای شبیه‌سازی کاربر
        try:
            file_input = page.query_selector(FILE_INPUT_SELECTOR)
            if file_input:
                box = file_input.bounding_box()
                if box:
                    page.mouse.move(
                        box["x"] + box["width"] / 2,
                        box["y"] + box["height"] / 2,
                        steps=15
                    )
                    time.sleep(random.uniform(0.3, 0.8))
        except Exception:
            pass

        # 3) تنظیم فایل روی input
        self._log(f"انتخاب فایل: {Path(file_path).name}", "STEP", C.YLW)
        page.set_input_files(FILE_INPUT_SELECTOR, file_path)

        # 4) انتظار برای ظاهر شدن دکمه «شروع آپلود»
        self._log("صبر برای ظاهر شدن دکمه «شروع آپلود»...", "STEP", C.YLW)
        start_btn = None
        for attempt in range(40):  # 20 ثانیه
            try:
                # دکمه شروع آپلود به صورت dynamic ساخته می‌شود
                start_btn = page.query_selector(
                    f'#upload_controls input[value="{START_UPLOAD_BTN_TEXT}"]'
                )
                if start_btn and start_btn.is_visible():
                    break
                # fallback: جستجوی دکمه با value مشابه
                start_btn = page.query_selector(
                    '#upload_controls input[type="button"][value*="اپلود"]'
                )
                if start_btn and start_btn.is_visible():
                    break
            except Exception:
                pass
            time.sleep(0.5)
        else:
            try:
                start_btn = page.get_by_role("button", name=START_UPLOAD_BTN_TEXT)
            except Exception:
                pass

        if not start_btn:
            self._dump_page_state(page)
            raise RuntimeError("دکمه «شروع آپلود» ظاهر نشد - احتمالاً فایل رد شده است")

        self._log("دکمه «شروع آپلود» پیدا شد", "OK", C.GRN)

        # 5) کلیک شروع آپلود
        self._log("کلیک روی «شروع آپلود» ...", "STEP", C.YLW)
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
            # شبیه‌سازی حرکت موس قبل از کلیک
            box = start_btn.bounding_box()
            if box:
                page.mouse.move(
                    box["x"] + box["width"] / 2,
                    box["y"] + box["height"] / 2,
                    steps=10
                )
                time.sleep(random.uniform(0.2, 0.5))
            start_btn.click()
        except Exception as e:
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

    # ---- باز کردن سایت با retry ----
    def _open_site_with_retry(self, page: Page, max_retries: int = 3) -> bool:
        """سایت رو با retry باز می‌کنه و در صورت نیاز pre-warm انجام می‌ده."""
        for attempt in range(max_retries):
            self._log(f"باز کردن سایت (تلاش {attempt+1}/{max_retries}): {SITE_URL}", "STEP", C.YLW)
            try:
                # مرحله 1: goto با wait_until=commit (سریع‌ترین)
                # این کار کوکی‌های اولیه و session رو دریافت می‌کنه
                page.goto(SITE_URL, wait_until="commit", timeout=60_000)

                # مرحله 2: صبر برای load کامل
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=30_000)
                except Exception:
                    pass

                # مرحله 3: pre-warm - 3-5 ثانیه صبر برای حل چالش JS احتمالی
                wait_sec = random.uniform(3.0, 5.0)
                self._log(f"Pre-warm: صبر {wait_sec:.1f}s برای حل چالش JS...", "INFO", C.DIM)
                time.sleep(wait_sec)

                # مرحله 4: اگه صفحه redirect شده یا title عوض شده، چک کن
                cur_title = ""
                try:
                    cur_title = page.title()
                except Exception:
                    pass

                if "403" in cur_title or "Forbidden" in cur_title:
                    self._log(f"تلاش {attempt+1}: 403 Forbidden - در حال retry...", "WRN", C.YLW)
                    # پاک کردن کوکی‌ها و retry
                    time.sleep(random.uniform(2.0, 4.0))
                    continue

                # مرحله 5: صبر برای networkidle (صفحه کامل لود شده)
                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except Exception:
                    pass

                # مرحله 6: شبیه‌سازی scroll و حرکت موس
                try:
                    page.mouse.move(random.randint(100, 800), random.randint(100, 600), steps=15)
                    page.evaluate("window.scrollBy(0, 100)")
                    time.sleep(0.5)
                    page.evaluate("window.scrollBy(0, -100)")
                except Exception:
                    pass

                # بررسی نهایی
                cur_title = page.title()
                if "403" in cur_title or "Forbidden" in cur_title:
                    self._log(f"تلاش {attempt+1}: همچنان 403 - retry...", "WRN", C.YLW)
                    continue

                self._log("سایت با موفقیت باز شد", "OK", C.GRN)
                return True

            except PWTimeout:
                self._log(f"تلاش {attempt+1}: Timeout - retry...", "WRN", C.YLW)
                time.sleep(random.uniform(2.0, 4.0))
                continue
            except Exception as e:
                self._log(f"تلاش {attempt+1}: خطا - {e}", "WRN", C.YLW)
                time.sleep(random.uniform(2.0, 4.0))
                continue

        return False

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

        deadline = start_ts + self.timeout
        while time.time() < deadline:
            try:
                pct = -1.0
                try:
                    inner = page.query_selector(PROGRESS_BAR_INNER)
                    if inner:
                        style = inner.get_attribute("style") or ""
                        m = re.search(r"width:\s*([0-9.]+)", style)
                        if m:
                            pct = float(m.group(1))
                        elif "px" in style:
                            outer = page.query_selector(".progressbar-outer")
                            if outer:
                                box = outer.bounding_box()
                                mpx = re.search(r"width:\s*([0-9.]+)px", style)
                                if mpx and box and box["width"] > 0:
                                    pct = float(mpx.group(1)) / box["width"] * 100
                except Exception:
                    pass

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

                if pct >= 0:
                    if pct > max_pct:
                        max_pct = pct
                    spd_bps = self._parse_speed(speed_text)
                    if spd_bps is not None and spd_bps > max_speed_bps:
                        max_speed_bps = spd_bps

                    if pct != last_pct or speed_text != last_speed:
                        self._print_live(pct, speed_text, completed_text, start_ts)
                        last_pct = pct
                        last_speed = speed_text
                        last_completed = completed_text

                    if pct >= 99.5:
                        stable_finish_count += 1
                        if stable_finish_count >= 3:
                            self._print_live(100.0, speed_text, completed_text, start_ts)
                            self._log("آپلود به 100٪ رسید", "OK", C.GRN)
                            self._wait_for_redirect(page, deadline)
                            break
                    else:
                        stable_finish_count = 0
                else:
                    cur_url = page.url
                    if "st=" in cur_url and "fn=" in cur_url:
                        self._log("آپلود کامل شد (redirect تشخیص داده شد)", "OK", C.GRN)
                        break
                    try:
                        if page.query_selector('.dlurl') or page.query_selector('a[href*="/"]'):
                            self._log("صفحه نتیجه تشخیص داده شد", "OK", C.GRN)
                            break
                    except Exception:
                        pass
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

        # اگر هیچ درصدی ثبت نشده ولی صفحه نتیجه/موفقیت رو نشون میده → 100%
        if max_pct <= 0:
            try:
                if (page.query_selector('textarea[name="download_links"]') or
                    page.query_selector('h2:has-text("آپلود با موفقیت انجام شد")')):
                    max_pct = 100.0
                    self._log("آپلود کامل شد (صفحه نتیجه تشخیص داده شد)", "OK", C.GRN)
            except Exception:
                pass

        result["duration_sec"] = round(duration, 2)
        result["max_percent"] = round(max_pct, 2)
        result["max_speed_bps"] = round(max_speed_bps, 2)
        if duration > 0 and max_pct > 0:
            result["average_speed_bps"] = round(total_size * (max_pct / 100) / duration, 2)

        self._log(
            f"پایان مانیتورینگ: max={result['max_percent']}% "
            f"avg_speed={human_size(result['average_speed_bps'])}/s "
            f"duration={duration:.1f}s",
            "INFO", C.CYN
        )
        return result

    def _wait_for_redirect(self, page: Page, deadline: float, max_wait: float = 15.0):
        start = time.time()
        initial_url = page.url
        self._log("صبر برای redirect به صفحه نتیجه...", "INFO", C.DIM)
        while time.time() - start < max_wait and time.time() < deadline:
            try:
                cur = page.url
                if cur != initial_url:
                    self._log(f"صفحه به {cur} منتقل شد", "OK", C.GRN)
                    return True
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
        elapsed = time.time() - start_ts
        bar = render_progress_bar(pct, 30)
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

        # 0) جستجوی textarea[name="download_links"]
        try:
            ta = page.query_selector('textarea[name="download_links"]')
            if ta:
                val = ta.get_attribute("value") or ta.inner_text() or ""
                val = val.strip()
                if val:
                    self._log(f"لینک از textarea: {val}", "OK", C.GRN)
                    return val
        except Exception:
            pass

        # 1) جستجوی لینک در URL (مثلاً ?st=OK&fn=xxxx)
        m = re.search(r"[?&]fn=([^&]+)", cur_url)
        if m:
            file_code = m.group(1)
            link = f"https://uplod.ir/{file_code}"
            self._log(f"کد فایل: {file_code}", "OK", C.GRN)
            return link

        # 2) جستجو در صفحه
        try:
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
                        if "uplod.ir/" in val:
                            mm = re.search(r"uplod\.ir/([a-z0-9]{8,})", val, re.I)
                            if mm:
                                return f"https://uplod.ir/{mm.group(1)}"
                            return val
                        if re.match(r"^[a-z0-9]{10,}$", val, re.I):
                            return f"https://uplod.ir/{val}"
                except Exception:
                    continue
        except Exception:
            pass

        # 3) جستجو در URLهای response
        for status, u in reversed(final_urls):
            if status == 200 and "fn=" in u:
                m = re.search(r"fn=([^&]+)", u)
                if m:
                    return f"https://uplod.ir/{m.group(1)}"

        # 4) جستجو در محتوای صفحه
        try:
            body = page.content()
            m = re.search(r'(https?://[^\s"\']+uplod\.ir/[a-z0-9]{8,})', body, re.I)
            if m:
                return m.group(1)
            m = re.search(r'\b([a-f0-9]{12})\b', body)
            if m:
                if 'dlurl' in body or 'file_code' in body:
                    return f"https://uplod.ir/{m.group(1)}"
        except Exception:
            pass

        return cur_url

    # ---- دیباگ ----
    def _dump_page_state(self, page: Page):
        try:
            self._log(f"URL: {page.url}", "DBG", C.DIM)
            self._log(f"title: {page.title()}", "DBG", C.DIM)
            body_text = page.inner_text("body")[:500]
            self._log(f"body excerpt: {body_text}", "DBG", C.DIM)
        except Exception:
            pass


# ---------- CLI ----------
def main():
    parser = argparse.ArgumentParser(
        description="هندلر آپلود فایل به uplod.ir (Stealth Mode)",
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
    parser.add_argument("--no-persistent", action="store_true",
                        help="عدم استفاده از پروفایل دائمی (هر بار مرورگر تازه)")
    args = parser.parse_args()

    log_fh = open(args.log, "a", encoding="utf-8") if args.log else None

    try:
        handler = UplodHandler(
            headed=args.headed,
            timeout=args.timeout,
            log_file=log_fh,
            verbose=args.verbose,
            persistent=not args.no_persistent,
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
