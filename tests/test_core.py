import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from wallstreet_quant.core import Engine


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = self.tmp.name + "/ledger.db"
        self.engine = Engine(self.path)
        self.now = datetime.now(timezone.utc)
        self.event = {"source": "fixture", "id": "1", "revision": "1", "symbol": "DEMO_EQUITY",
                      "published_at": self.now.isoformat(), "verified": True, "url": "https://example.com"}
        self.decision = {"action": "enter", "confidence": ".9", "notional": "500", "rationale": "fixture"}
        self.quote = {"symbol": "DEMO_EQUITY", "bid": "100", "ask": "100.1", "bid_size": "1000",
                      "ask_size": "1000", "observed_at": self.now.isoformat()}

    def tearDown(self):
        self.engine.close()
        self.tmp.cleanup()

    def run_event(self, event=None, decision=None, quote=None):
        return self.engine.process(event or self.event, decision or self.decision, quote or self.quote, self.now)

    def test_duplicate_survives_restart(self):
        self.assertEqual(self.run_event()["status"], "filled")
        balance = self.engine.status()["cash"]
        self.engine.close()
        self.engine = Engine(self.path)
        self.assertEqual(self.run_event()["status"], "duplicate")
        self.assertEqual(self.engine.status()["cash"], balance)
        self.assertEqual(len(self.engine.records()), 1)

    def test_round_trip_accounting_and_partial_close(self):
        self.run_event()
        self.run_event(dict(self.event, id="2"), dict(self.decision, action="reduce", fraction=".5"))
        self.assertTrue(self.engine.status()["positions"])
        self.run_event(dict(self.event, id="3"), dict(self.decision, action="exit"))
        state = self.engine.status()
        self.assertEqual(state["positions"], {})
        self.assertAlmostEqual(Decimal(state["cash"]) - Decimal("10000"), Decimal(state["realized_pnl"]), places=18)
        self.assertLess(Decimal(state["realized_pnl"]), 0)  # spread + fees + slippage

    def test_invalid_inputs_do_not_change_cash(self):
        for index, (event, decision, quote) in enumerate([
            (dict(self.event, verified=False), self.decision, self.quote),
            (dict(self.event, symbol="UNLISTED"), self.decision, self.quote),
            (dict(self.event, published_at=(self.now-timedelta(hours=1)).isoformat()), self.decision, self.quote),
            (self.event, dict(self.decision, confidence=".1"), self.quote),
            (self.event, dict(self.decision, notional="NaN"), self.quote),
            (self.event, dict(self.decision, action="anything"), self.quote),
            (self.event, self.decision, dict(self.quote, bid="90")),
            (self.event, self.decision, dict(self.quote, ask_size=".001")),
            (self.event, self.decision, dict(self.quote, symbol="WRONG")),
            (self.event, self.decision, dict(self.quote, observed_at=(self.now+timedelta(seconds=1)).isoformat())),
            (self.event, self.decision, dict(self.quote, observed_at=(self.now-timedelta(seconds=16)).isoformat())),
        ]):
            with self.subTest(index=index):
                self.assertEqual(self.run_event(dict(event, id=str(index)), decision, quote)["status"], "rejected")
                self.assertEqual(self.engine.status()["cash"], "10000")

    def test_position_cap_includes_fee(self):
        self.run_event(decision=dict(self.decision, notional="999999"))
        cost = Decimal(self.engine.status()["positions"]["DEMO_EQUITY"]["cost"])
        self.assertLessEqual(cost, Decimal("1000"))
        self.assertGreater(cost, Decimal("999.99"))

    def test_pause_persisted_and_exit_available(self):
        self.run_event()
        self.engine.pause(True)
        self.engine.close()
        self.engine = Engine(self.path)
        self.assertTrue(self.engine.status()["paused"])
        self.assertEqual(self.run_event(dict(self.event, id="2"), dict(self.decision, action="exit"))["status"], "filled")
        self.assertEqual(self.run_event(dict(self.event, id="3"))["reason"], "New entries paused")

    def test_loss_limit_blocks_entries_but_not_exit(self):
        self.run_event(decision=dict(self.decision, notional="1000"))
        self.run_event(dict(self.event, id="2"), dict(self.decision, action="exit"), dict(self.quote, bid="50", ask="50.1"))
        self.assertEqual(self.run_event(dict(self.event, id="3"))["reason"], "Session realized-loss limit reached")

    def test_no_pyramiding_or_naked_sells(self):
        self.assertEqual(self.run_event(decision=dict(self.decision, action="exit"))["status"], "rejected")
        self.run_event(dict(self.event, id="2"))
        self.assertEqual(self.run_event(dict(self.event, id="3"))["status"], "rejected")

    def test_rejected_event_requires_new_revision(self):
        self.run_event(decision=dict(self.decision, confidence=".1"))
        self.assertEqual(self.run_event()["status"], "duplicate")
        self.assertEqual(self.run_event(dict(self.event, revision="2"))["status"], "filled")


if __name__ == "__main__":
    unittest.main()
