"""
download_queue.py
─────────────────
صف سراسری دانلود/آپلود ربات.

قاعده: هر لحظه فقط «یک» کار (دانلود+آپلود کامل) روی سرور در جریانه.
بقیه‌ی درخواست‌ها تو صف می‌شینن؛ موقعیت‌شون (نفر ۲، نفر ۳، ...) زنده آپدیت
می‌شه و به‌محض تموم شدن کار فعال (آپلود کامل ویدیو و کپشن)، نفر بعدی
به‌صورت FIFO نوبتش می‌رسه.

طراحی ضد-گیج:
  - join()  → بلیت می‌گیری (فوری یا انتهای صف). هر کاربر فقط یک بلیت.
  - wait()  → تا نوبت صبر می‌کنه (اگه از صف لغو بشه QueueCancelledError).
  - done()  → کار فعال رو تموم اعلام می‌کنه → نفر بعدی grant می‌شه و
              بقیه‌ی صف آپدیت می‌شن. idempotent هست (چند بار صدا زدن بی‌ضرر).
  - leave() → لغو از داخل صف (دکمه‌ی «لغو نوبت»).
  - جابه‌جایی‌ها همه با قفل async؛ رقابت بین چند callback ممکن نیست.
  - اگه کار فعال بیش از ACTIVE_STALL_SECONDS معلق بمونه (باگ/هنگ)،
    watchdog آزادش می‌کنه تا صف تا ابد قفل نشه.
"""

import asyncio
import itertools
import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger("DownloadQueue")

# سقف زمانی هر کار فعال — بعد از این، watchdog خودش رد می‌کنه (۱ ساعت هشدار)
ACTIVE_STALL_SECONDS = 6 * 3600


class AlreadyInQueueError(Exception):
    """کاربر یک بلیت فعال/در انتظار دیگه هم داره."""

    def __init__(self, user_id, position: int = 0):
        self.user_id = user_id
        self.position = position
        super().__init__(f"user {user_id} already has an active/queued job")


class QueueCancelledError(Exception):
    """کاربر از داخل صف لغو کرد (دکمه‌ی لغو نوبت)."""


class _Job:
    __slots__ = ("id", "user_id", "title", "future", "on_position", "since")

    def __init__(self, job_id, user_id, title, on_position):
        self.id = job_id
        self.user_id = user_id
        self.title = title or ""
        self.on_position = on_position  # async fn(job, pos) — pos>=2 صف، 1=نوبت
        self.future: asyncio.Future = asyncio.get_event_loop().create_future()
        self.since = time.time()

    @property
    def turn_ready(self) -> bool:
        return self.future.done() and not self.future.exception()


class DownloadQueue:
    def __init__(self):
        self._active: Optional[_Job] = None
        self._waiters: list = []
        self._by_user: dict = {}
        self._ids = itertools.count(1)

    # ── helpers ──────────────────────────────────────────────

    def _notify(self, job: _Job, pos: int):
        if not job.on_position:
            return
        try:
            res = job.on_position(job, pos)
            if asyncio.iscoroutine(res):
                asyncio.ensure_future(res)
        except Exception as e:
            logger.warning("on_position cb failed: %s", e)

    def _notify_waiters(self):
        for i, w in enumerate(self._waiters, start=2):
            self._notify(w, i)

    def _grant_next(self):
        """فعلی رفته؛ نفر بعدی صف activate می‌شه (فراخوان با قفل)."""
        self._active = None
        while self._waiters:
            nxt = self._waiters.pop(0)
            if nxt.future.done():
                continue  # لغو شده وسط صف
            self._active = nxt
            if not nxt.future.done():
                nxt.future.set_result(True)
            self._notify(nxt, 1)
            self._notify_waiters()
            logger.info(
                "[DLQ] job #%s (user %s) granted; %d waiting",
                nxt.id, nxt.user_id, len(self._waiters),
            )
            return
        logger.info("[DLQ] queue empty — lane free")

    # ── API ──────────────────────────────────────────────────

    async def join(self, user_id, title: str = "",
                   on_position: Optional[Callable] = None) -> _Job:
        """بلیت بگیر. اگر نوبت فوری بود future پر شده (turn_ready=True)."""
        if user_id is not None and user_id in self._by_user:
            raise AlreadyInQueueError(user_id)
        job = _Job(next(self._ids), user_id, title, on_position)
        if self._active is None:
            self._active = job
            if user_id is not None:
                self._by_user[user_id] = job.id
            job.since = time.time()
            logger.info("[DLQ] job #%s (user %s) START now | %r",
                        job.id, user_id, job.title[:50])
            if not job.future.done():
                job.future.set_result(True)
            self._notify(job, 1)
            return job
        self._waiters.append(job)
        if user_id is not None:
            self._by_user[user_id] = job.id
        pos = len(self._waiters) + 1
        logger.info("[DLQ] job #%s (user %s) queued at %d | %r",
                    job.id, user_id, pos, job.title[:50])
        self._notify(job, pos)
        return job

    async def wait(self, job: _Job):
        """تا نوبت صبر کن. از داخل صف لغو → QueueCancelledError."""
        if not job.future.done():
            await job.future
        exc = job.future.exception()
        if exc:
            raise exc

    def done(self, job: _Job):
        """پایان کار فعال (حتی در خطا/لغو). idempotent."""
        if job is None:
            return
        if self._active is job:
            self._by_user.pop(job.user_id, None)
            self._grant_next()
        elif job in self._waiters:
            self._waiters.remove(job)
            self._by_user.pop(job.user_id, None)
            self._notify_waiters()

    def leave(self, job_id) -> bool:
        """لغو نوبت از داخل صف. True = بود و حذف شد."""
        for w in self._waiters:
            if str(w.id) == str(job_id):
                self._waiters.remove(w)
                self._by_user.pop(w.user_id, None)
                if not w.future.done():
                    w.future.set_exception(QueueCancelledError("cancelled from queue"))
                self._notify_waiters()
                logger.info("[DLQ] job #%s cancelled from queue", job_id)
                return True
        return False

    # ── introspection (برای UI) ──────────────────────────────

    def position_of(self, job: _Job) -> int:
        if self._active is job:
            return 1
        try:
            return self._waiters.index(job) + 2
        except ValueError:
            return 0

    def waiting_count(self) -> int:
        return len(self._waiters)

    def active_title(self) -> str:
        return (self._active.title if self._active else "") or ""

    def active_user_id(self):
        return self._active.user_id if self._active else None

    def has_user(self, user_id) -> bool:
        return user_id is not None and user_id in self._by_user

    def stall_check(self) -> Optional[_Job]:
        """اگه کار فعال از سقف زمانی گذشته، آزادش کن (ضد قفل‌شدن دائمی)."""
        a = self._active
        if a and time.time() - a.since > ACTIVE_STALL_SECONDS:
            logger.error("[DLQ] job #%s stalled >%ds — force releasing",
                         a.id, ACTIVE_STALL_SECONDS)
            self._by_user.pop(a.user_id, None)
            self._grant_next()
            return None
        return a
