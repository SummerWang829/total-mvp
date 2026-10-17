"""Event-loop stall watchdog (switch on with LOOP_WATCH_S=<seconds>, e.g. 2).

Every request of an `async def` handler runs on the single event-loop thread. If such a handler does blocking work (a database call that
waits for a lock, a slow file write), every other request waits behind it, which looks like "the whole site froze". When the loop has not
reached its heartbeat for longer than the threshold, a separate thread logs a WARNING with the stack the loop thread is stuck in, so the
culprit is named in the log instead of guessed.
"""
import asyncio
import logging
import sys
import threading
import time
import traceback

log = logging.getLogger("app.loopwatch")


def pools() -> str:
    """How busy the two shared resources are right now: worker threads for the sync handlers, and database connections."""
    from anyio.to_thread import current_default_thread_limiter

    from .db import engine

    lim = current_default_thread_limiter()
    return f"threads {int(lim.borrowed_tokens)}/{int(lim.total_tokens)} busy; db {engine.pool.status()}"


async def slow_request_logger(request, call_next):  # noqa: ANN001 - Starlette middleware signature
    """Log every request that took longer than the threshold, with how busy the threads and the connection pool were when it finished."""
    t0 = time.monotonic()
    try:
        return await call_next(request)
    finally:
        took = time.monotonic() - t0
        if took > _threshold[0]:
            log.warning("slow request %.1fs: %s %s | %s", took, request.method, request.url.path, pools())


_threshold = [2.0]


def _other_threads(skip: int) -> str:
    """Where every other thread is right now (the one holding the GIL or the CPU is usually among them)."""
    names = {t.ident: t.name for t in threading.enumerate()}
    out = []
    for tid, frame in sys._current_frames().items():
        if tid == skip or names.get(tid) == "loop-watchdog":
            continue
        out.append(f"-- {names.get(tid, tid)}:\n" + "".join(traceback.format_stack(frame)[-3:]))
    return "\n".join(out)


def start(threshold_s: float) -> None:
    """Start the heartbeat (on the running loop) and the watchdog thread. Call from the app's startup, inside the event loop."""
    beat = [time.monotonic()]
    _threshold[0] = threshold_s
    loop_thread = threading.get_ident()

    async def pulse() -> None:
        while True:
            beat[0] = time.monotonic()
            await asyncio.sleep(0.2)

    def watch() -> None:
        reported = 0.0
        while True:
            time.sleep(0.5)
            stalled = time.monotonic() - beat[0]
            if stalled > threshold_s and beat[0] != reported:
                reported = beat[0]  # one report per stall
                frame = sys._current_frames().get(loop_thread)
                where = "".join(traceback.format_stack(frame)[-6:]) if frame else "(no frame)"
                log.warning("event loop blocked for more than %.1fs; the loop thread is in:\n%s", stalled, where)
                log.warning("other threads at that moment:\n%s", _other_threads(loop_thread))

    asyncio.get_running_loop().create_task(pulse())
    threading.Thread(target=watch, daemon=True, name="loop-watchdog").start()
