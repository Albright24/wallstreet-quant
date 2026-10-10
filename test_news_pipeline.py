import copy
from datetime import datetime, timezone, timedelta
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from wallstreet_quant.news import alpaca_events, SECNews, NewsError, clean_text
from wallstreet_quant.advisor import OpenAIAdvisor, validate_decision
from wallstreet_quant.pipeline import Pipeline
from wallstreet_quant.core import Engine, Risk


def event(now):
    return {"source": "fixture_primary", "id": "1", "revision": "1", "symbol": "RNVDAUSDT",
        "published_at": now.isoformat(), "headline": "SYNTHETIC event", "body": "SYNTHETIC guidance event",
        "url": "https://example.com/fixture", "verified": True, "synthetic": True,
        "evidence": [{"id": "filing", "url": "https://example.com/fixture", "text": "SYNTHETIC guidance event"}]}


def proposal(action="enter"):
    return {"action": action, "confidence": .85, "notional": 50, "fraction": .5,
            "rationale": "Synthetic test decision", "uncertainty": "Test fixture, no forecast",
            "evidence_ids": ["filing"], "material_event": True}


def response(decision):
    return {"id": "fixture-response", "status": "completed", "usage": {"input_tokens": 100, "output_tokens": 80},
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(decision)}]}]}


class FakeMarket:
    def __init__(self):
        self.calls = 0

    def snapshot(self, symbol):
        self.calls += 1
        return {"quote": {"symbol": symbol, "bid": "100", "ask": "100.1", "bid_size": "100",
                "ask_size": "100", "observed_at": datetime.now(timezone.utc).isoformat(),
                "source": "synthetic_fixture", "synthetic": True}}


class NewsTests(unittest.TestCase):
    def test_alpaca_mapping_revision_and_no_trust_promotion(self):
        article = {"id": 12, "created_at": "2026-10-06T11:00:00Z", "updated_at": "2026-10-06T11:01:00Z",
            "url": "https://example.com/story", "symbols": ["NVDA", "TSLA", "UNLISTED"],
            "headline": "Example news", "content": "<p>Body</p>", "source": "fixture"}
        events = alpaca_events(article)
        self.assertEqual(len(events), 2)
        self.assertNotEqual(events[0]["id"], events[1]["id"])
        self.assertFalse(events[0]["verified"])
        self.assertNotEqual(events[0]["revision"], alpaca_events(dict(article, content="Corrected news"))[0]["revision"])

    def test_html_is_data(self):
        self.assertEqual(clean_text('<script>secret()</script><style>hidden</style><p>Visible</p>'), "Visible")

    def test_sec_needs_real_contact_setting(self):
        with patch.dict(os.environ, {"SEC_USER_AGENT": ""}), self.assertRaises(NewsError):
            SECNews().fetch("NVDA")

    def test_sec_resolves_identity_and_fetches_primary_document(self):
        calls = []
        class HTTP:
            def get(self, url, headers, as_json=True):
                calls.append(url)
                if "company_tickers" in url:
                    return {"fields": ["cik", "name", "ticker", "exchange"], "data": [[1045810, "NVIDIA", "NVDA", "Nasdaq"]]}
                if "submissions" in url:
                    return {"filings": {"recent": {"form": ["8-K"], "accessionNumber": ["0001045810-26-000001"],
                        "primaryDocument": ["report.htm"], "acceptanceDateTime": ["2026-10-06T11:00:00Z"]}}}
                return "<p>" + ("Synthetic primary filing content. " * 10) + "</p>"
        with patch.dict(os.environ, {"SEC_USER_AGENT": "UnitTest tests@example.com"}):
            events = SECNews(HTTP()).fetch("NVDA")
        self.assertEqual(len(calls), 3)
        self.assertTrue(events[0]["verified"])
        self.assertIn("primary_document", events[0]["verification"])
        self.assertIn("/1045810/000104581026000001/report.htm", calls[-1])


class AdvisorTests(unittest.TestCase):
    def setUp(self):
        self.event = event(datetime.now(timezone.utc))

    def test_structured_request_has_no_tools_and_records_usage(self):
        payloads = []
        def transport(payload):
            payloads.append(payload)
            return response(proposal())
        decision = OpenAIAdvisor(transport).decide(self.event, {"cash": "1000"})
        self.assertNotIn("tools", payloads[0])
        self.assertFalse(payloads[0]["store"])
        self.assertTrue(payloads[0]["text"]["format"]["strict"])
        self.assertEqual(decision["usage"]["output_tokens"], 80)

    def test_refusal_incomplete_and_invalid_evidence_blocked(self):
        responses = [None, {"status": "completed", "output": [None]},
                     dict(response(proposal()), status="incomplete"),
                     {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]},
                     response(dict(proposal(), evidence_ids=["invented"]))]
        for item in responses:
            with self.subTest(item=item), self.assertRaises(ValueError):
                OpenAIAdvisor(lambda payload: item).decide(self.event, {})

    def test_invalid_numbers_and_extra_fields_rejected(self):
        for change in ({"confidence": float("nan")}, {"notional": -1}, {"confidence": True},
                       {"fraction": 2}, {"action": "short"}, {"material_event": False}, {"risk_override": True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_decision(dict(proposal(), **change), self.event)

    def test_missing_credentials_do_not_call_provider(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "", "WQ_MODEL": ""}), self.assertRaises(ValueError):
            OpenAIAdvisor().decide(self.event, {})


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.risk = Risk(symbols=("RNVDAUSDT",))
        self.engine = Engine(self.tmp.name + "/agent.db", self.risk)
        self.market = FakeMarket()
        self.now = datetime.now(timezone.utc)
        self.event = event(self.now)
        self.calls = []
        def transport(payload):
            self.calls.append(payload)
            return response(proposal())
        self.advisor = OpenAIAdvisor(transport)
        self.pipeline = Pipeline(self.engine, self.advisor, self.market)

    def tearDown(self):
        self.engine.close()
        self.tmp.cleanup()

    def test_full_pipeline_quotes_after_model_and_restart_dedup(self):
        result = self.pipeline.process(self.event)
        self.assertEqual(result["execution"]["status"], "filled")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.market.calls, 1)
        self.engine.close()
        self.engine = Engine(self.tmp.name + "/agent.db", self.risk)
        self.pipeline = Pipeline(self.engine, self.advisor, self.market)
        self.assertEqual(self.pipeline.process(self.event)["status"], "duplicate")
        self.assertEqual(len(self.calls), 1)

    def test_exact_content_duplicates_across_publishers(self):
        self.pipeline.process(self.event)
        result = self.pipeline.process(dict(self.event, source="other-publisher", id="2"))
        self.assertEqual(result["status"], "duplicate")
        self.assertEqual(len(self.calls), 1)

    def test_stale_event_and_daily_budget_avoid_paid_calls(self):
        stale = dict(self.event, published_at=(self.now-timedelta(hours=1)).isoformat())
        self.assertEqual(self.pipeline.process(stale)["status"], "blocked")
        self.assertEqual(len(self.calls), 0)
        pipeline = Pipeline(self.engine, self.advisor, self.market, daily_call_limit=1)
        pipeline.process(dict(self.event, id="2", headline="Different current event"))
        blocked = pipeline.process(dict(self.event, id="3", headline="Another current event"))
        self.assertEqual(blocked["reason"], "Daily model-call budget exhausted")
        self.assertEqual(len(self.calls), 1)

    def test_secondary_story_cannot_authorize_trade(self):
        result = self.pipeline.process(dict(self.event, verified=False))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.engine.status()["cash"], "10000")
        self.assertEqual(self.market.calls, 0)

    def test_model_failure_recorded_without_fill_or_retry(self):
        advisor = OpenAIAdvisor(lambda payload: {"status": "incomplete"})
        pipeline = Pipeline(self.engine, advisor, self.market)
        self.assertEqual(pipeline.process(self.event)["status"], "error")
        self.assertEqual(pipeline.process(self.event)["status"], "duplicate")
        self.assertEqual(self.engine.status()["cash"], "10000")

    def test_old_ai_decision_blocked_even_with_fresh_quote(self):
        decision = dict(proposal(), decision_started_at=(self.now-timedelta(seconds=61)).isoformat())
        result = self.engine.process(self.event, decision, self.market.snapshot("RNVDAUSDT")["quote"])
        self.assertEqual(result["status"], "rejected")
        self.assertIn("expired", result["reason"])


if __name__ == "__main__":
    unittest.main()
