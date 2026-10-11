from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import tempfile
import threading
import unittest
from wallstreet_quant.collector import Store
from wallstreet_quant.core import Engine, Risk
from wallstreet_quant.telegram_bridge import candidates, cycle


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / 'telegram.sqlite3')
        self.paper = str(Path(self.temp.name) / 'paper.sqlite3')
        self.risk = Risk(symbols=['RNVDAUSDT'])
        self.registry = {'channels': {'marketfeed': {'enabled': True}}}
        self.store = Store(self.db)
        self.addCleanup(self.store.db.close)
        self.store.pin('marketfeed', 123)
        self.now = datetime.now(timezone.utc)

    def post(self, ident=1, text='Nvidia launches a new chip', age=1, edited=False):
        self.store.save('marketfeed', 123, SimpleNamespace(
            id=ident, message=text, date=self.now-timedelta(seconds=age),
            edit_date=self.now if edited else None, entities=[]), self.registry, self.now)

    def test_stale_edit_promo_macro_are_not_candidates(self):
        self.post(age=1800, edited=True)
        self.post(2, 'Nvidia sponsored promo code')
        self.post(3, 'Nvidia and Fed inflation outlook')
        events, summary = candidates(self.db, now=self.now)
        self.assertEqual(events, [])

    def test_unverified_enter_blocked_and_restart_deduplicates(self):
        self.post()
        advisor = Mock()
        advisor.decide.return_value = {'action':'enter', 'confidence':.9,
            'notional':50, 'fraction':0, 'rationale':'Fixture', 'material_event':True,
            'evidence_ids':['telegram_post'], 'uncertainty':'unverified'}
        market = Mock()
        first = cycle(self.paper, self.risk, self.db, 20, threading.Event(), advisor, market)
        self.assertEqual(first['status'], 'blocked')
        self.assertIn('corroboration', first['reason'])
        second = cycle(self.paper, self.risk, self.db, 20, threading.Event(), advisor, market)
        self.assertEqual(second['status'], 'no_new_candidate')
        self.assertEqual(advisor.decide.call_count, 1)
        market.snapshot.assert_not_called()
        engine = Engine(self.paper, self.risk)
        self.assertEqual(engine.status()['positions'], {})
        engine.close()

    def test_budget_shared_and_one_new_attempt_per_cycle(self):
        self.post(1)
        self.post(2, 'Nvidia announces another chip')
        advisor = Mock()
        advisor.decide.return_value = {'action':'enter'}
        first = cycle(self.paper, self.risk, self.db, 1, threading.Event(), advisor, Mock())
        second = cycle(self.paper, self.risk, self.db, 1, threading.Event(), advisor, Mock())
        self.assertEqual(first['status'], 'blocked')
        self.assertEqual(second['reason'], 'Daily model-call budget exhausted')
        self.assertEqual(advisor.decide.call_count, 1)

    def test_latest_revision_only(self):
        self.post(1)
        self.post(1, 'Nvidia sponsored promo code', edited=True)
        self.assertEqual(candidates(self.db, now=self.now)[0], [])

    def test_missing_database_not_created(self):
        missing = Path(self.temp.name) / 'missing.sqlite3'
        with self.assertRaises(ValueError):
            candidates(missing)
        self.assertFalse(missing.exists())

    def test_future_post_rejected(self):
        self.post(age=-10)
        self.assertEqual(candidates(self.db, now=self.now)[0], [])
