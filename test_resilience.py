"""Concurrency, crash, malformed-input and lifecycle regression coverage."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
import random
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from wallstreet_quant.core import Engine, Risk
from wallstreet_quant.pipeline import Pipeline
from wallstreet_quant.advisor import OpenAIAdvisor
from wallstreet_quant.monitor import RuntimeStore, ExitPolicy
from wallstreet_quant.telegram import Telegram
from wallstreet_quant.worker import WorkerLock, run_worker


def event(identity="one"):
    return {"source":"fixture", "id":identity, "revision":"1", "symbol":"DEMO_EQUITY",
            "published_at":datetime.now(timezone.utc).isoformat(), "verified":True,
            "url":"https://example.com/fixture", "headline":identity, "body":"Synthetic evidence " + identity,
            "evidence":[{"id":"e", "text":"Synthetic evidence", "url":"https://example.com/fixture"}]}


def decision(action="enter", **changes):
    return {"action":action, "confidence":.9, "notional":500, "fraction":.5,
            "rationale":"Synthetic proposal", "uncertainty":"Test only",
            "evidence_ids":["e"], "material_event":True, **changes}


class Market:
    def __init__(self, price="100"):
        self.price=price

    def snapshot(self, symbol):
        return {"quote":{"symbol":symbol, "bid":self.price, "ask":self.price,
                         "bid_size":"10000", "ask_size":"10000",
                         "observed_at":datetime.now(timezone.utc).isoformat()}}


class ResilienceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=self.tmp.name+"/paper.db"
        self.engine=Engine(self.path)
        self.market=Market()

    def tearDown(self):
        self.engine.close()
        self.tmp.cleanup()

    def process(self, identity, action="enter", **changes):
        return self.engine.process(event(identity), decision(action, **changes), self.market.snapshot("DEMO_EQUITY")["quote"])

    def test_eight_simultaneous_duplicate_orders_fill_once(self):
        barrier=threading.Barrier(8)
        e=event()
        def submit(_):
            engine=Engine(self.path)
            try:
                barrier.wait(timeout=10)
                return engine.process(e,decision(),self.market.snapshot("DEMO_EQUITY")["quote"])["status"]
            finally:
                engine.close()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(submit,range(8)))
        self.assertEqual(results.count("filled"),1)
        self.assertEqual(results.count("duplicate"),7)
        self.assertEqual(len(self.engine.records()),1)

    def test_daily_model_cap_is_atomic_across_connections(self):
        # Initialize schema before simultaneous starts; each event has distinct content.
        Pipeline(self.engine,None,None)
        barrier=threading.Barrier(4)
        calls=[]
        class Advisor:
            def decide(self,e,account):
                calls.append(e["id"])
                return decision("skip")
        def submit(i):
            engine=Engine(self.path)
            try:
                p=Pipeline(engine,Advisor(),self.market,daily_call_limit=1)
                barrier.wait(timeout=10)
                return p.process(event(str(i)))["status"]
            finally:
                engine.close()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(submit,range(4)))
        self.assertEqual(len(calls),1)
        self.assertEqual(results.count("blocked"),3)

    def test_partial_reduction_during_ai_call_invalidates_proposal(self):
        self.process("entry")
        def provider(payload):
            self.process("concurrent-reduction","reduce",fraction=.5)
            return {"status":"completed","output":[{"type":"message","content":[
                {"type":"output_text","text":json.dumps(decision("exit"))}]}]}
        pipeline=Pipeline(self.engine,OpenAIAdvisor(provider),self.market)
        result=pipeline.process(event("late-model"))
        self.assertEqual(result["execution"]["status"],"rejected")
        self.assertTrue(self.engine.status()["positions"])

    def test_seeded_partial_fills_conserve_account_value(self):
        randomizer=random.Random(4096)
        total=Decimal(0)
        for i in range(100):
            self.market.price=str(randomizer.randint(20,300))
            entry=self.process(f"entry-{i}",notional=randomizer.randint(20,100))
            self.assertEqual(entry["status"],"filled")
            for j in range(2):
                self.market.price=str(Decimal(self.market.price)*Decimal("1.005"))
                self.process(f"reduce-{i}-{j}","reduce",fraction=.25)
            result=self.process(f"exit-{i}","exit")
            self.assertEqual(result["status"],"filled")
            self.assertFalse(self.engine.status()["positions"])
            self.assertGreaterEqual(Decimal(self.engine.status()["cash"]),0)
        total=sum((Decimal(r["fill"]["realized_pnl"]) for r in self.engine.records() if r.get("fill")),Decimal(0))
        self.assertAlmostEqual(Decimal(self.engine.status()["cash"])-Decimal("10000"),total,places=16)
        self.assertAlmostEqual(Decimal(self.engine.status()["realized_pnl"]),total,places=16)

    def bot(self):
        return Telegram(self.engine,transport=lambda *args:None,user_id=123,chat_id=123,clock=lambda:1000)

    def update(self,uid=1,**changes):
        return {"update_id":uid,"message":{"from":{"id":123},"chat":{"id":123,"type":"private"},
            "date":999,"text":"/resume",**changes}}

    def test_forwarded_text_is_not_a_control_command(self):
        self.engine.pause(True)
        self.bot().handle(self.update(forward_origin={"type":"user"}))
        self.assertTrue(self.engine.status()["paused"])

    def test_malformed_message_does_not_stall_later_commands(self):
        bot=self.bot()
        bot.handle(self.update(date=None))
        bot.handle(self.update(2,text="/pause"))
        self.assertTrue(self.engine.status()["paused"])
        self.assertEqual(bot.store.get("telegram_offset"),3)

    def test_telegram_failure_at_shutdown_is_recorded(self):
        with patch("wallstreet_quant.worker.Telegram"), patch("wallstreet_quant.worker.telegram_cycle",side_effect=ValueError("fixture failure")):
            run_worker(self.engine,ExitPolicy(),cycles=1,enable_telegram=True)
        result=RuntimeStore(self.engine).get("telegram_last_result")
        self.assertIsNotNone(result)
        self.assertEqual(result["error_type"],"ValueError")

    @unittest.skipIf(os.name=="nt","POSIX process signal validation")
    def test_forced_crash_rolls_back_transaction_and_releases_lock(self):
        code='''import sys,time
from wallstreet_quant.core import Engine
from wallstreet_quant.worker import WorkerLock
engine=Engine(sys.argv[1])
with WorkerLock(sys.argv[1]):
    engine.db.execute("BEGIN IMMEDIATE")
    state=engine.status(); state["cash"]="0"; engine._save(state)
    print("ready",flush=True)
    time.sleep(30)
'''
        child=subprocess.Popen([sys.executable,"-c",code,self.path],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            import select
            ready,_,_=select.select([child.stdout],[],[],10)
            self.assertTrue(ready)
            self.assertEqual(child.stdout.readline().strip(),"ready")
            child.kill()
            child.communicate(timeout=5)
            self.assertEqual(self.engine.status()["cash"],"10000")
            with WorkerLock(self.path):
                pass
            self.assertEqual(self.engine.db.execute("PRAGMA integrity_check").fetchone()[0],"ok")
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)

    @unittest.skipIf(os.name=="nt","POSIX process signal validation")
    def test_sigterm_stops_real_worker_and_preserves_pause(self):
        code='''import sys
from wallstreet_quant.core import Engine
from wallstreet_quant.worker import run_worker
from wallstreet_quant.monitor import ExitPolicy
engine=Engine(sys.argv[1])
try: run_worker(engine,ExitPolicy(),interval=1)
finally: engine.close()
'''
        child=subprocess.Popen([sys.executable,"-c",code,self.path],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            import time
            store=RuntimeStore(self.engine)
            deadline=time.monotonic()+5
            while time.monotonic()<deadline and store.get("worker_state",{}).get("status")!="running":
                time.sleep(.02)
            self.assertEqual(store.get("worker_state",{}).get("status"),"running")
            child.terminate()
            _,error=child.communicate(timeout=5)
            self.assertEqual(child.returncode,0,error)
            self.assertTrue(self.engine.status()["paused"])
            self.assertEqual(store.get("worker_state")["status"],"stopped")
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)


if __name__=="__main__":
    unittest.main()
