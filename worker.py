"""Single-host continuous paper worker, with isolated SQLite connections per thread."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
import math
import signal
import threading
import time
from .core import Engine
from .bitget import BitgetPublic
from .monitor import Monitor, RuntimeStore
from .news import SECNews, SYMBOLS
from .advisor import OpenAIAdvisor
from .pipeline import Pipeline
from .telegram import Telegram


class WorkerLock:
    def __init__(self, path):
        self.path, self.file = path + ".worker.lock", None

    def __enter__(self):
        self.file = open(self.path, "a+b")
        self.file.seek(0)
        if os.name == "nt":
            import msvcrt
            if os.path.getsize(self.path) == 0:
                self.file.write(b"0")
                self.file.flush()
            self.file.seek(0)
            try:
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                self.file.close()
                raise ValueError("Another worker owns this database") from None
        else:
            import fcntl
            try:
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self.file.close()
                raise ValueError("Another worker owns this database") from None
        return self

    def __exit__(self, *args):
        self.file.close()  # OS releases the lock, including after process crashes.


def news_cycle(path, risk, ticker, daily_calls, stop):
    engine = Engine(path, risk)
    try:
        events = SECNews().fetch(ticker, limit=1)
        pipeline = Pipeline(engine, OpenAIAdvisor(), BitgetPublic(), daily_calls)
        results = []
        for event in events:
            if stop.is_set():
                break
            results.append(pipeline.process(event))
        return {"ticker": ticker, "statuses": [r["status"] for r in results]}
    finally:
        engine.close()


def telegram_cycle(path, risk):
    engine = Engine(path, risk)
    try:
        Telegram(engine).tick()
    finally:
        engine.close()


def run_worker(engine, policy, cycles=0, interval=5, news_interval=60,
               enable_news=False, enable_telegram=False, daily_calls=20):
    if (type(cycles) is not int or cycles < 0 or not math.isfinite(interval) or not math.isfinite(news_interval)
            or interval < 1 or news_interval < 30 or type(daily_calls) is not int or not 1 <= daily_calls <= 100):
        raise ValueError("Invalid worker interval or cycle count")
    path = engine.db.execute("PRAGMA database_list").fetchone()[2]
    if not path:
        raise ValueError("Worker requires a persistent file database")
    tickers = [t for t, s in SYMBOLS.items() if s in engine.risk.symbols]
    if enable_news and (not tickers or not all(os.getenv(k) for k in ("SEC_USER_AGENT", "OPENAI_API_KEY", "WQ_MODEL"))):
        raise ValueError("News worker requires configured symbols, SEC contact and runtime model credentials")
    if enable_telegram:
        Telegram(engine)  # Validate locally before starting any worker tasks.
    stop = threading.Event()
    original = {}
    def shutdown(signum, frame):
        stop.set()
    with WorkerLock(path):
        for sig in (signal.SIGINT, signal.SIGTERM):
            original[sig] = signal.signal(sig, shutdown)
        store = RuntimeStore(engine)
        monitor = Monitor(engine, BitgetPublic(), policy)
        pool = ThreadPoolExecutor(max_workers=2)
        pending = {}
        next_news = 0
        completed = 0
        store.put("worker_state", {"status": "running", "pid": os.getpid()})
        def record_future(kind, future):
            at = datetime.now(timezone.utc).isoformat()
            if future.cancelled():
                store.put(kind + "_last_result", {"at": at, "cancelled": True})
                return
            try:
                result = future.result()
                store.put(kind + "_last_result", {"at": at, "result": result})
            except Exception as exc:
                store.put(kind + "_last_result", {"at": at, "error_type": type(exc).__name__})
                store.notify(kind + ":error:" + datetime.now(timezone.utc).date().isoformat(), f"{kind} worker error; inspect local runtime status.")
        try:
            while not stop.is_set() and (cycles == 0 or completed < cycles):
                started = time.monotonic()
                monitor.tick()
                for kind, future in list(pending.items()):
                    if future.done():
                        record_future(kind, future)
                        del pending[kind]
                if enable_news and "news" not in pending and time.monotonic() >= next_news:
                    index = store.get("news_ticker_index", 0) % len(tickers)
                    pending["news"] = pool.submit(news_cycle, path, engine.risk, tickers[index], daily_calls, stop)
                    store.put("news_ticker_index", index+1)
                    next_news = time.monotonic() + news_interval
                if enable_telegram and "telegram" not in pending:
                    pending["telegram"] = pool.submit(telegram_cycle, path, engine.risk)
                completed += 1
                if cycles and completed >= cycles:
                    break
                stop.wait(max(0, interval - (time.monotonic()-started)))
        finally:
            stop.set()
            engine.db.execute("BEGIN IMMEDIATE")
            state = engine.status()
            state["paused"] = True
            engine._save(state)
            store.put("worker_state", {"status": "stopping"})
            engine.db.execute("COMMIT")
            pool.shutdown(wait=True, cancel_futures=True)
            for kind, future in pending.items():
                record_future(kind, future)
            engine.pause(True)
            store.put("worker_state", {"status": "stopped", "cycles": completed,
                                       "at": datetime.now(timezone.utc).isoformat()})
            for sig, handler in original.items():
                signal.signal(sig, handler)
        return {"cycles": completed, "entries_paused": True, "status": "stopped"}
