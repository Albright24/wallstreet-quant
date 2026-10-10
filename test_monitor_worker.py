from datetime import datetime, timezone, timedelta
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from wallstreet_quant.core import Engine
from wallstreet_quant.monitor import Monitor, ExitPolicy, RuntimeStore
from wallstreet_quant.telegram import Telegram
from wallstreet_quant.worker import WorkerLock, run_worker


class Quotes:
    def __init__(self, bid="100", stale=False, size="1000"):
        self.bid, self.stale, self.size = bid, stale, size

    def snapshot(self, symbol):
        now = datetime.now(timezone.utc) - timedelta(seconds=60 if self.stale else 0)
        return {"quote": {"symbol": symbol, "bid": self.bid, "ask": self.bid,
                "bid_size": self.size, "ask_size": self.size, "observed_at": now.isoformat()}}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = self.tmp.name + "/paper.db"
        self.engine = Engine(self.path)
        self.now = datetime.now(timezone.utc)
        self.event = {"source": "fixture", "id": "entry", "revision": "1", "symbol": "DEMO_EQUITY",
                      "published_at": self.now.isoformat(), "verified": True, "url": "https://example.com/test"}
        self.decision = {"action": "enter", "confidence": ".9", "notional": "500", "rationale": "fixture"}

    def tearDown(self):
        self.engine.close()
        self.tmp.cleanup()

    def enter(self, at=None):
        quote = Quotes().snapshot("DEMO_EQUITY")["quote"]
        now = at or datetime.now(timezone.utc)
        quote["observed_at"] = now.isoformat()
        result = self.engine.process(dict(self.event, published_at=now.isoformat()), self.decision, quote, now)
        self.assertEqual(result["status"], "filled")

    def test_stop_loss_while_paused_and_restart_no_duplicate(self):
        self.enter()
        self.engine.pause(True)
        self.engine.close()
        self.engine = Engine(self.path)
        result = Monitor(self.engine, Quotes("95")).tick()
        self.assertEqual(result[0]["trigger"], "stop_loss")
        self.assertEqual(result[0]["status"], "filled")
        self.assertEqual(Monitor(self.engine, Quotes("95")).tick(), [])
        self.assertEqual(len(self.engine.records()), 2)

    def test_take_profit(self):
        self.enter()
        result = Monitor(self.engine, Quotes("110")).tick()
        self.assertEqual(result[0]["trigger"], "take_profit")
        self.assertEqual(result[0]["status"], "filled")

    def test_timeout_and_partial_reduce_preserve_entry_time(self):
        self.enter(self.now - timedelta(hours=5))
        original = self.engine.status()["positions"]["DEMO_EQUITY"]
        result = self.engine.process(dict(self.event, id="reduce"), dict(self.decision, action="reduce", fraction=".5"), Quotes().snapshot("DEMO_EQUITY")["quote"])
        self.assertEqual(result["status"], "filled")
        remaining = self.engine.status()["positions"]["DEMO_EQUITY"]
        self.assertEqual(original["opened_at"], remaining["opened_at"])
        self.assertEqual(original["position_id"], remaining["position_id"])
        result = Monitor(self.engine, Quotes()).tick()
        self.assertEqual(result[0]["trigger"], "time_limit")
        self.assertEqual(result[0]["status"], "filled")

    def test_stale_quote_cannot_trigger_fill(self):
        self.enter()
        result = Monitor(self.engine, Quotes("90", stale=True)).tick()
        self.assertEqual(result[0]["status"], "blocked")
        self.assertTrue(self.engine.status()["positions"])

    def test_rejected_exit_retries_with_new_quote(self):
        self.enter()
        result = Monitor(self.engine, Quotes("90", size=".01")).tick()
        self.assertEqual(result[0]["status"], "rejected")
        result = Monitor(self.engine, Quotes("90")).tick()
        self.assertEqual(result[0]["status"], "filled")
        self.assertFalse(self.engine.status()["positions"])

    def test_old_position_decision_cannot_close_new_lifecycle(self):
        self.enter()
        pid = self.engine.status()["positions"]["DEMO_EQUITY"]["position_id"]
        close = dict(self.decision, action="exit", expected_position_id=pid)
        self.engine.process(dict(self.event, id="close"), close, Quotes().snapshot("DEMO_EQUITY")["quote"])
        self.engine.process(dict(self.event, id="new"), self.decision, Quotes().snapshot("DEMO_EQUITY")["quote"])
        result = self.engine.process(dict(self.event, id="late-close"), close, Quotes("90").snapshot("DEMO_EQUITY")["quote"])
        self.assertEqual(result["status"], "rejected")
        self.assertTrue(self.engine.status()["positions"])

    def test_legacy_metadata_recovered_from_ledger(self):
        self.enter()
        state = self.engine.status()
        del state["positions"]["DEMO_EQUITY"]["opened_at"]
        del state["positions"]["DEMO_EQUITY"]["position_id"]
        self.engine._save(state)
        self.engine.close()
        self.engine = Engine(self.path)
        p = self.engine.status()["positions"]["DEMO_EQUITY"]
        self.assertEqual(p["opened_at"], self.engine.records()[0]["fill"]["timestamp"])
        self.assertTrue(p["position_id"])

    def test_single_worker_lock_released_normally(self):
        with WorkerLock(self.path):
            with self.assertRaises(ValueError):
                with WorkerLock(self.path):
                    pass
        with WorkerLock(self.path):
            pass

    def test_bounded_worker_stops_and_pauses_entries(self):
        result = run_worker(self.engine, ExitPolicy(), cycles=1)
        self.assertEqual(result["cycles"], 1)
        self.assertTrue(self.engine.status()["paused"])
        self.assertEqual(RuntimeStore(self.engine).get("worker_state")["status"], "stopped")

    def test_slow_news_does_not_block_monitor_cycles(self):
        from wallstreet_quant.core import Risk
        self.engine.close()
        self.engine = Engine(self.tmp.name + "/news.db", Risk(symbols=("RNVDAUSDT",)))
        checks = []
        def slow_news(path, risk, ticker, daily_calls, stop):
            stop.wait(10)
        with patch.dict(os.environ, {"SEC_USER_AGENT": "Test test@example.com", "OPENAI_API_KEY": "fixture", "WQ_MODEL": "fixture"}), \
             patch("wallstreet_quant.worker.news_cycle", side_effect=slow_news), \
             patch("wallstreet_quant.worker.Monitor.tick", side_effect=lambda: checks.append(1)):
            result = run_worker(self.engine, ExitPolicy(), cycles=2, interval=1, enable_news=True)
        self.assertEqual(result["cycles"], 2)
        self.assertEqual(len(checks), 2)


class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = self.tmp.name + "/bot.db"
        self.engine = Engine(self.path)
        self.sent = []
        self.bot = Telegram(self.engine, transport=lambda method,data: self.sent.append((method,data)), user_id=123, chat_id=123, clock=lambda: 1000)

    def tearDown(self):
        self.engine.close()
        self.tmp.cleanup()

    def update(self, uid, text, user=123, chat=123, kind="private", date=999):
        return {"update_id": uid, "message": {"from": {"id": user}, "chat": {"id": chat, "type": kind}, "date": date, "text": text}}

    def test_authorization_and_replay(self):
        for i, update in enumerate([self.update(1,"/pause",user=999), self.update(2,"/pause",chat=999),
                                   self.update(3,"/pause",kind="group"), self.update(4,"/pause",date=1)]):
            self.bot.handle(update)
            self.assertFalse(self.engine.status()["paused"])
        self.bot.handle(self.update(5,"/pause"))
        self.assertTrue(self.engine.status()["paused"])
        self.bot.handle(self.update(6,"/resume"))
        self.assertFalse(self.engine.status()["paused"])
        self.bot.handle(self.update(5,"/pause"))
        self.assertFalse(self.engine.status()["paused"])

    def test_offset_and_reply_queue_survive_restart(self):
        self.bot.handle(self.update(1,"/pause"))
        self.engine.close()
        self.engine=Engine(self.path)
        self.bot=Telegram(self.engine,transport=lambda method,data:self.sent.append((method,data)),user_id=123,chat_id=123,clock=lambda:1000)
        self.bot.handle(self.update(1,"/resume"))
        self.assertTrue(self.engine.status()["paused"])
        self.bot.flush()
        self.bot.flush()
        self.assertEqual(len(self.sent), 1)

    def test_uncertain_delivery_not_retried(self):
        calls=[]
        def fail(method,data):
            calls.append(method)
            raise ValueError("fixture timeout")
        self.bot.transport=fail
        self.bot.handle(self.update(1,"/status"))
        self.bot.flush()
        self.bot.flush()
        self.assertEqual(calls,["sendMessage"])
        self.assertEqual(self.engine.db.execute("SELECT status FROM notices").fetchone()[0],"delivery_unknown")

    def test_shutdown_prevents_telegram_resume(self):
        self.bot.store.put("worker_state",{"status":"stopping"})
        self.bot.handle(self.update(1,"/resume"))
        self.assertTrue(self.engine.status()["paused"])


if __name__ == "__main__":
    unittest.main()
