import copy
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
from wallstreet_quant.bitget import (BitgetPublic, MarketDataError, normalize_quote,
                                    process_live_paper, INSTRUMENTS, TICKERS, stock_candidate)
from wallstreet_quant.core import Engine, Risk


class BitgetTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.instrument = {"symbol": "RNVDAUSDT", "category": "SPOT", "quoteCoin": "USDT",
                           "status": "online", "isReality": "yes", "quantityPrecision": "4",
                           "minOrderQty": "0.0001", "maxOrderQty": "0", "minOrderAmount": "10"}
        self.ticker = {"symbol": "RNVDAUSDT", "category": "SPOT", "ts": str(int(self.now.timestamp()*1000)),
                       "bid1Price": "100", "ask1Price": "100.1", "bid1Size": "100", "ask1Size": "100"}
        self.calls = []
        self.client = BitgetPublic(transport=self.transport)

    def transport(self, path, params):
        self.calls.append(path)
        return {"code": "00000", "data": [copy.deepcopy(self.instrument if path == INSTRUMENTS else self.ticker)]}

    def test_live_shape_normalization_preserves_exchange_time(self):
        q = normalize_quote(self.instrument, self.ticker, self.now)
        self.assertEqual(q["source"], "bitget_public_ticker")
        self.assertFalse(q["synthetic"])
        self.assertEqual(q["ask_size"], "100")
        self.assertEqual(q["instrument_limits"]["quantityPrecision"], 4)
        self.assertLessEqual(datetime.fromisoformat(q["observed_at"]), self.now)

    def test_no_price_or_liquidity_fabrication(self):
        for change in ({"ask1Size": ""}, {"ask1Size": "0"}, {"bid1Price": "0"},
                       {"bid1Price": "101"}, {"ask1Price": "NaN"}, {"symbol": "OTHER"},
                       {"ts": str(int((self.now-timedelta(seconds=20)).timestamp()*1000))},
                       {"ts": str(int((self.now+timedelta(seconds=20)).timestamp()*1000))}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                normalize_quote(self.instrument, dict(self.ticker, **change), self.now)

    def test_disabled_or_wrong_currency_instrument_rejected(self):
        for change in ({"status": "offline"}, {"quoteCoin": "BTC"}, {"category": "USDT-FUTURES"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                normalize_quote(dict(self.instrument, **change), self.ticker, self.now)

    def test_snapshot_fetches_price_after_instrument(self):
        self.assertTrue(self.client.snapshot("RNVDAUSDT")["paper_quote_usable"])
        self.assertEqual(self.calls, [INSTRUMENTS, TICKERS])

    def test_stale_snapshot_keeps_raw_evidence_but_no_quote(self):
        self.ticker["ts"] = "1000"
        result = self.client.snapshot("RNVDAUSDT")
        self.assertFalse(result["paper_quote_usable"])
        self.assertIsNone(result["quote"])
        self.assertEqual(result["ticker"]["ts"], "1000")

    def test_api_failure_and_private_endpoint_are_blocked(self):
        client = BitgetPublic(transport=lambda *args: {"code": "40001", "data": []})
        with self.assertRaises(MarketDataError):
            client.instruments()
        with self.assertRaises(MarketDataError):
            self.client.get("/api/v3/trade/place-order")
        self.assertEqual(self.calls, [])

    def test_bounded_network_retry(self):
        count = []
        def fail(*args):
            count.append(1)
            raise urllib.error.URLError("offline")
        with self.assertRaises(MarketDataError):
            BitgetPublic(transport=fail).instruments()
        self.assertEqual(len(count), 2)

    def test_rwa_not_automatically_stock(self):
        self.assertFalse(stock_candidate({"isRwa": "YES"}))
        self.assertTrue(stock_candidate(self.instrument))

    def test_paper_pipeline_limits_accounting_and_failure_audit(self):
        event = {"source": "test", "id": "1", "revision": "1", "symbol": "RNVDAUSDT",
                 "published_at": self.now.isoformat(), "verified": True, "url": "https://example.com/test"}
        decision = {"action": "enter", "confidence": ".9", "notional": "500", "rationale": "test only"}
        with tempfile.TemporaryDirectory() as folder:
            engine = Engine(folder + "/paper.db", Risk(symbols=("RNVDAUSDT",)))
            try:
                result = process_live_paper(engine, self.client, event, decision)
                self.assertEqual(result["status"], "filled")
                qty = Decimal(result["fill"]["quantity"])
                self.assertEqual(qty, qty.quantize(Decimal(".0001")))
                process_live_paper(engine, self.client, dict(event, id="2"), dict(decision, action="exit"))
                self.assertEqual(engine.status()["positions"], {})
                small = process_live_paper(engine, self.client, dict(event, id="3"), dict(decision, notional="1"))
                self.assertEqual(small["status"], "rejected")
                cash = engine.status()["cash"]
                with patch.object(self.client, "snapshot", side_effect=MarketDataError("network unavailable")):
                    failed = process_live_paper(engine, self.client, dict(event, id="4"), decision)
                self.assertEqual(failed["status"], "rejected")
                self.assertIn("network unavailable", failed["reason"])
                self.assertEqual(engine.status()["cash"], cash)
                self.assertEqual(len(engine.records()), 4)
            finally:
                engine.close()


if __name__ == "__main__":
    unittest.main()
