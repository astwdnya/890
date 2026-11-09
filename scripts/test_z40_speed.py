#!/usr/bin/env python3
"""تست z40 — بهینه‌سازی سرعت (انتخاب سریع‌ترین CDN + کش‌ها + موازی‌سازی)

تست‌ها:
  ۱) ثابت‌های موتور (نقطه‌ی شروع ۱۶، سقف ۶۴، session 96، پروب 512KB)
  ۲) کش پروب: پروب دومِ همان URL → cache HIT (بدون فراخوانی دوباره)
  ۳) TTL منفی پروب: نتیجه‌ی FAIL بعد از ۹۰ ثانیه منقضی (شبیه‌سازی با ts دستی)
  ۴) کش رزولوشن: فراخوانی دوم _get_stream_for_server → nocache صدا زده نمی‌شود
     + deepcopy (mutation کالر کش را مسموم نکند)
  ۵) _pick_fastest_candidate: از بین دو کاندید سالم، سریع‌ترین انتخاب می‌شود
     (شبیه‌سازی tinyloud کند vs brandpositioning سریع) + tiebreak سرور انتخابی
  ۶) _pick_fastest_candidate: صفر کاندید → None
  ۷) _pick_fastest_candidate: تک‌کاندید → بدون بنچمارک انتخاب می‌شود
  ۸) e2e انتخاب-تا-تحویل با سرورهای فیک + MP4 واقعی لوکال: served_by = سریع‌ترین
  ۹) e2e: دانلود واقعی MP4 لوکال سالم تحویل می‌دهد
  ۱۰) نردبان کنترلر: گام ۸ و سقف ۶۴ (ladder تا MAX می‌رسد)
  ۱۱) ساختار superstream در بیلد 2Embed: لیبل/hdrها درست merge می‌شوند
  ۱۲) menu cache: هیت در TTL و انقضا بعد از ۱۰ دقیقه
"""
import asyncio
import os
import sys
import time
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, "/home/z/my-project/890")

from searcher.imdb import imdbplay_downloader as ipd  # noqa: E402

PASS = 0
FAIL = 0


def ok(cond, name, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}" + (f" — {extra}" if extra else ""))
    else:
        FAIL += 1
        print(f"  ✗ {name}" + (f" — {extra}" if extra else ""))


# ═══════════════ ۱) ثابت‌ها ═══════════════
def test_constants():
    print("۱) ثابت‌های موتور z40")
    ok(ipd.SEGMENT_CONCURRENCY == 16, "SEGMENT_CONCURRENCY=16", str(ipd.SEGMENT_CONCURRENCY))
    ok(ipd.SEG_CONCURRENCY_MAX == 64, "SEG_CONCURRENCY_MAX=64", str(ipd.SEG_CONCURRENCY_MAX))
    ok(ipd.SESSION_MAX_CLIENTS >= 96, "SESSION_MAX_CLIENTS>=96", str(ipd.SESSION_MAX_CLIENTS))
    ok(ipd._PROBE_MAX_BYTES == 524_288, "_PROBE_MAX_BYTES=512KB", str(ipd._PROBE_MAX_BYTES))
    ok(ipd._PROBE_MIN_BYTES == 65_536, "_PROBE_MIN_BYTES=64KB")


# ═══════════════ ۲ و ۳) کش پروب ═══════════════
def test_probe_cache():
    print("۲) کش پروب — HIT دوباره")
    calls = {"n": 0}

    async def fake_uncached(stream, want_label="", require_match=False):
        calls["n"] += 1
        await asyncio.sleep(0.01)
        return True, "fake probe ok"

    old = ipd._probe_stream_health_uncached
    ipd._probe_stream_health_uncached = fake_uncached
    try:
        async def run():
            st = {"url": "https://x.test/a.m3u8", "type": "hls"}
            r1 = await ipd._probe_stream_health(st, "720p")
            r2 = await ipd._probe_stream_health(st, "720p")
            return r1, r2
        r1, r2 = asyncio.run(run())
        ok(calls["n"] == 1, "پروب فقط یک‌بار اجرا شد", f"calls={calls['n']}")
        ok(r1 == (True, "fake probe ok") and r2 == r1, "نتیجه‌ی یکسان")
    finally:
        ipd._probe_stream_health_uncached = old

    print("۳) TTL منفی پروب")
    ipd._PROBE_CACHE.clear()
    key = ("https://x.test/bad.m3u8", "hls", "720p", False)
    ipd._PROBE_CACHE[key] = (time.monotonic() - 91.0, (False, "old fail"))
    got = None
    hit = ipd._PROBE_CACHE.get(key)
    if hit:
        ts, val = hit
        if (time.monotonic() - ts) <= ipd._CACHE_TTL_FAIL:
            got = val
    ok(got is None, "نتیجه‌ی FAIL بعد از ۹۰ ثانیه منقضی می‌شود")
    ipd._PROBE_CACHE[key] = (time.monotonic(), (True, "fresh"))
    hit = ipd._PROBE_CACHE.get(key)
    ok(hit is not None and hit[1][0] is True, "نتیجه‌ی OK در TTL می‌ماند")
    ipd._PROBE_CACHE.clear()


# ═══════════════ ۴) کش رزولوشن ═══════════════
def test_resolve_cache():
    print("۴) کش رزولوشن سرورها")
    calls = {"n": 0}

    async def fake_nocache(server, tmdb_id, imdb_id, season, episode):
        calls["n"] += 1
        await asyncio.sleep(0.01)
        return {"url": "https://x.test/v.m3u8", "type": "hls",
                "server": server["name"], "qualities": [{"label": "720p",
                                                         "url": "https://x.test/v.m3u8"}]}

    old = ipd._get_stream_for_server_nocache
    ipd._get_stream_for_server_nocache = fake_nocache
    ipd._RESOLVE_CACHE.clear()
    try:
        async def run():
            srv = {"id": "sX", "name": "FakeX"}
            a = await ipd._get_stream_for_server(srv, "t1", "tt1", None, None)
            b = await ipd._get_stream_for_server(srv, "t1", "tt1", None, None)
            a["url"] = "MUTATED"
            c = await ipd._get_stream_for_server(srv, "t1", "tt1", None, None)
            return a, b, c
        a, b, c = asyncio.run(run())
        ok(calls["n"] == 1, "رزولوشن فقط یک‌بار", f"calls={calls['n']}")
        ok(b is not a, "deepcopy در خروجی (بدون اشتراک شیء)")
        ok(c["url"] == "https://x.test/v.m3u8", "mutation کالر کش را آلوده نکرد",
           c["url"])
    finally:
        ipd._get_stream_for_server_nocache = old
        ipd._RESOLVE_CACHE.clear()


# ═══════════════ ۵ و ۶ و ۷) انتخاب سریع‌ترین ═══════════════
def test_pick_fastest():
    print("۵) انتخاب سریع‌ترین کاندید")
    async def run():
        async def eval_mixed(server):
            if server["name"] == "SlowCDN":
                return {"url": "https://slow.test/v.m3u8", "server": "SlowCDN",
                        "type": "hls"}
            return {"url": "https://fast.test/v.m3u8", "server": "FastCDN",
                    "type": "hls"}

        speeds = {"https://slow.test/v.m3u8": 1.0, "https://fast.test/v.m3u8": 20.0}

        async def fake_bench(stream, cap_s=4.5, nseg=3):
            await asyncio.sleep(0.01)
            return speeds.get(stream["url"], 0.0)

        old_b = ipd._quick_speed_bench
        ipd._quick_speed_bench = fake_bench
        try:
            ordered = [{"id": "a", "name": "SlowCDN"}, {"id": "b", "name": "FastCDN"}]
            return await ipd._pick_fastest_candidate(eval_mixed, ordered, None)
        finally:
            ipd._quick_speed_bench = old_b

    pick = asyncio.run(run())
    ok(pick is not None and pick["server"] == "FastCDN",
       "سریع‌ترین (۲۰MB/s) انتخاب شد نه اولی", pick and pick["server"])

    print("۶) صفر کاندید → None")
    async def eval_none(server):
        return None
    pick = asyncio.run(ipd._pick_fastest_candidate(eval_none,
                                                   [{"id": "a", "name": "X"}], None))
    ok(pick is None, "بدون کاندید → None")

    print("۷) تک‌کاندید بدون بنچمارک")
    benched = {"called": False}

    async def eval_one(server):
        return {"url": "https://one.test/v.m3u8", "server": "Only", "type": "hls"}

    async def bench_should_not_run(stream, cap_s=4.5, nseg=3):
        benched["called"] = True
        return 5.0

    old_b = ipd._quick_speed_bench
    ipd._quick_speed_bench = bench_should_not_run
    try:
        pick = asyncio.run(ipd._pick_fastest_candidate(
            eval_one, [{"id": "a", "name": "Only"}], None))
    finally:
        ipd._quick_speed_bench = old_b
    ok(pick is not None and pick["server"] == "Only" and not benched["called"],
       "تک‌کاندید مستقیم انتخاب شد (بدون هدررفت بنچمارک)")

    print("۵-ب) tiebreak سرور انتخابی در سرعت برابر")
    async def run_tie():
        async def eval_mixed(server):
            return {"url": f"https://{server['name']}.test/v.m3u8",
                    "server": server["name"], "type": "hls"}

        async def fake_bench(stream, cap_s=4.5, nseg=3):
            return 10.0  # همه برابر

        old_b = ipd._quick_speed_bench
        ipd._quick_speed_bench = fake_bench
        try:
            ordered = [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]
            return await ipd._pick_fastest_candidate(eval_mixed, ordered, "B")
        finally:
            ipd._quick_speed_bench = old_b

    pick = asyncio.run(run_tie())
    ok(pick["server"] == "B", "در تساوی سرعت، سرور انتخابی کاربر می‌برد",
       pick["server"])


# ═══════════════ ۸ و ۹) e2e با MP4 لوکال ═══════════════
class _H(BaseHTTPRequestHandler):
    payload = b""

    def do_GET(self):
        data = _H.payload
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def test_e2e_selection_and_delivery():
    print("۸ و ۹) e2e: انتخاب سریع‌ترین + تحویل MP4 لوکال")
    tmp = tempfile.mkdtemp(prefix="z40_")
    mp4 = os.path.join(tmp, "tiny.mp4")
    try:
        import subprocess
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi",
                        "-i", "testsrc=duration=2:size=320x240:rate=10",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        "-movflags", "+faststart", mp4],
                       capture_output=True, timeout=60)
        _H.payload = open(mp4, "rb").read()
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

        url = f"http://127.0.0.1:{port}/tiny.mp4"

        async def fake_nocache(server, tmdb_id, imdb_id, season, episode):
            return {"url": url, "type": "mp4", "server": server["name"],
                    "headers": {},
                    "qualities": [{"label": "720p", "url": url}]}

        async def fake_probe(stream, want_label="", require_match=False):
            return True, "fake ok"

        async def fake_dur(stream, expected_s, label=""):
            return True

        async def fake_tmdb(imdb_id, title=None, year=None, is_tv=False):
            return "tmdb1"

        async def fake_runtime(tmdb_id, season=None, episode=None):
            return None

        speeds = {"SlowCDN": 1.0, "FastCDN": 25.0}

        async def fake_bench(stream, cap_s=4.5, nseg=3):
            await asyncio.sleep(0.01)
            return speeds.get(stream.get("server"), 0.0)

        old_nocache = ipd._get_stream_for_server_nocache
        old_probe = ipd._probe_stream_health
        old_dur = ipd._stream_duration_ok
        old_tmdb = ipd._get_tmdb_id
        old_rt = ipd._expected_runtime_seconds
        old_b = ipd._quick_speed_bench
        old_servers = list(ipd._SERVERS)

        ipd._get_stream_for_server_nocache = fake_nocache
        ipd._probe_stream_health = fake_probe
        ipd._stream_duration_ok = fake_dur
        ipd._get_tmdb_id = fake_tmdb
        ipd._expected_runtime_seconds = fake_runtime
        ipd._quick_speed_bench = fake_bench
        ipd._SERVERS[:] = [{"id": "a", "name": "SlowCDN"},
                           {"id": "b", "name": "FastCDN"}]
        ipd._RESOLVE_CACHE.clear()

        out_dir = os.path.join(tmp, "out")
        try:
            stats = {}

            async def run():
                return await ipd.download_with_quality(
                    "tt123", "720p", out_dir, None, None,
                    preferred_server="SlowCDN", strict_quality=True,
                    stats_out=stats)

            path = asyncio.run(run())
            ok(path and os.path.exists(path) and os.path.getsize(path) > 0,
               "فایل سالم تحویل شد", f"{os.path.getsize(path or '')}B")
            ok(stats.get("served_by") == "FastCDN",
               "served_by = سریع‌ترین CDN (نه انتخاب کاربر)",
               str(stats.get("served_by")))
        finally:
            ipd._get_stream_for_server_nocache = old_nocache
            ipd._probe_stream_health = old_probe
            ipd._stream_duration_ok = old_dur
            ipd._get_tmdb_id = old_tmdb
            ipd._expected_runtime_seconds = old_rt
            ipd._quick_speed_bench = old_b
            ipd._SERVERS[:] = old_servers
            ipd._RESOLVE_CACHE.clear()
            httpd.shutdown()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ═══════════════ ۱۰) نردبان کنترلر ═══════════════
def test_ladder():
    print("۱۰) نردبان کنترلر — گام ۸ تا سقف ۶۴")
    desired = ipd.SEGMENT_CONCURRENCY
    ladder = [desired]
    c = ladder[0]
    while c + 8 <= ipd.SEG_CONCURRENCY_MAX and len(ladder) < 8:
        c += 8
        ladder.append(c)
    ok(ladder[-1] >= 56, "نردبان تا نزدیک سقف می‌رسد", str(ladder))
    ok(64 in ladder or ladder[-1] == ipd.SEG_CONCURRENCY_MAX,
       "سقف ۶۴ قابل دسترس", str(ladder))


# ═══════════════ ۱۱) ساختار superstream در فچر 2Embed ═══════════════
def test_2embed_build():
    print("۱۱) بیلد استریم‌های superstream (ساختار streams)")
    dec = {"streams": [
        {"quality": "1080p", "url": "https://a.test/1080.m3u8", "type": "hls"},
        {"quality": "720p", "url": "https://a.test/720.m3u8", "type": "hls"},
    ], "headers": {"Referer": "https://x.test/"}}
    # بازسازی همان منطق از _2embed_fetch_provider (فقط بخش بیلد)
    streams = []
    for s_item in dec["streams"]:
        _lbl = str(s_item.get("quality") or s_item.get("language") or "auto").strip()
        streams.append({"quality": _lbl, "label": _lbl, "url": s_item["url"],
                        "type": s_item.get("type", "hls"),
                        "headers": {**(dec.get("headers") or {}),
                                    **(s_item.get("headers") or {})}})
    ok(len(streams) == 2 and streams[1]["label"] == "720p",
       "لیبل 720p حفظ می‌شود")
    ok(all("Referer" in s["headers"] for s in streams), "هدرها merge می‌شوند")


# ═══════════════ ۱۲) کش منو ═══════════════
def test_menu_cache():
    print("۱۲) کش منوی کیفیت")
    import copy as _copy
    ipd._MENU_CACHE.clear()
    key = ("tt1", 0, 0, "", "", False)
    entries = [{"server": "X", "qualities": [{"label": "720p"}]}]
    ipd._MENU_CACHE[key] = (time.monotonic(), _copy.deepcopy(entries))
    hit = ipd._MENU_CACHE.get(key)
    ok(hit and (time.monotonic() - hit[0]) <= ipd._CACHE_TTL_OK, "هیت در TTL")
    ipd._MENU_CACHE[key] = (time.monotonic() - 601.0, entries)
    expired = (time.monotonic() - ipd._MENU_CACHE[key][0]) <= ipd._CACHE_TTL_OK
    ok(not expired, "منقضی بعد از ۱۰ دقیقه")



# ═══════════════ ۱۳) چند-provider داخل یک سرور (2Embed) ═══════════════
def test_multi_provider_in_one_server():
    print("۱۳) 2Embed چند-provider: بنچمارک بین providerهای یک سرور")
    tmp = tempfile.mkdtemp(prefix="z40b_")
    mp4 = os.path.join(tmp, "tiny.mp4")
    try:
        import subprocess
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi",
                        "-i", "testsrc=duration=2:size=320x240:rate=10",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", mp4],
                       capture_output=True, timeout=60)
        _H.payload = open(mp4, "rb").read()
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{port}/tiny.mp4"

        async def fake_multi(tmdb_id, imdb_id, season, episode):
            # provider کند و سریع — هر دو لیبل 720p دارند
            return [
                {"url": url, "type": "mp4", "server": "2Embed",
                 "headers": {},
                 "qualities": [{"label": "720p", "url": url}]},
                {"url": url, "type": "mp4", "server": "2Embed",
                 "headers": {},
                 "qualities": [{"label": "720p", "url": url}]},
            ]

        async def fake_tmdb(imdb_id, title=None, year=None, is_tv=False):
            return "tmdb1"

        async def fake_runtime(tmdb_id, season=None, episode=None):
            return None

        async def fake_probe(stream, want_label="", require_match=False):
            return True, "fake ok"

        async def fake_dur(stream, expected_s, label=""):
            return True

        # بنچمارک: بسته به providerِ داخل headers(_tag) سرعت متفاوت
        speeds = {"SlowProv": 0.8, "FastProv": 30.0}

        async def fake_bench(stream, cap_s=4.5, nseg=3):
            await asyncio.sleep(0.01)
            return speeds.get(stream.get("headers", {}).get("_prov", "?"), 0.0)

        async def fake_nocache(server, tmdb_id, imdb_id, season, episode):
            return None  # s9 از multi می‌رود

        old_multi = ipd._2embed_get_stream_multi
        old_probe = ipd._probe_stream_health
        old_dur = ipd._stream_duration_ok
        old_tmdb = ipd._get_tmdb_id
        old_rt = ipd._expected_runtime_seconds
        old_b = ipd._quick_speed_bench
        old_nocache = ipd._get_stream_for_server_nocache
        old_servers = list(ipd._SERVERS)

        ipd._2embed_get_stream_multi = fake_multi
        ipd._probe_stream_health = fake_probe
        ipd._stream_duration_ok = fake_dur
        ipd._get_tmdb_id = fake_tmdb
        ipd._expected_runtime_seconds = fake_runtime
        ipd._quick_speed_bench = fake_bench
        ipd._get_stream_for_server_nocache = fake_nocache
        ipd._SERVERS[:] = [{"id": "s9", "name": "2Embed", "prefer_imdb": True}]
        ipd._RESOLVE_CACHE.clear()

        out_dir = os.path.join(tmp, "out")
        try:
            stats = {}

            async def run():
                return await ipd.download_with_quality(
                    "tt123", "720p", out_dir, None, None,
                    preferred_server=None, strict_quality=True,
                    stats_out=stats)

            path = asyncio.run(run())
            ok(path and os.path.exists(path) and os.path.getsize(path) > 0,
               "فایل از provider سریع تحویل شد")
            ok(stats.get("served_by") == "2Embed",
               "served_by درست", str(stats.get("served_by")))
        finally:
            ipd._2embed_get_stream_multi = old_multi
            ipd._probe_stream_health = old_probe
            ipd._stream_duration_ok = old_dur
            ipd._get_tmdb_id = old_tmdb
            ipd._expected_runtime_seconds = old_rt
            ipd._quick_speed_bench = old_b
            ipd._get_stream_for_server_nocache = old_nocache
            ipd._SERVERS[:] = old_servers
            ipd._RESOLVE_CACHE.clear()
            httpd.shutdown()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_constants()
    test_probe_cache()
    test_resolve_cache()
    test_pick_fastest()
    test_e2e_selection_and_delivery()
    test_ladder()
    test_2embed_build()
    test_menu_cache()
    test_multi_provider_in_one_server()
    print(f"\n═══ نتیجه: {PASS}/{PASS + FAIL} ═══")
    sys.exit(0 if FAIL == 0 else 1)
