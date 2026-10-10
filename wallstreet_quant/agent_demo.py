"""Explicit offline fixture demo of the orchestration; no external model or news."""
from datetime import datetime, timezone
from .pipeline import Pipeline
from .advisor import validate_decision


class FixtureAdvisor:
    def decide(self, event, account):
        action = "exit" if "DEMO_EQUITY" in account["positions"] else "enter"
        value = {"action": action, "confidence": .85, "notional": 50, "fraction": 0,
                 "rationale": "Synthetic fixture proposal; no AI model called",
                 "uncertainty": "This is a connectivity test, not a market forecast",
                 "evidence_ids": ["fixture"], "material_event": True}
        decision = validate_decision(value, event)
        decision.update(advisor="synthetic_fixture", decision_started_at=datetime.now(timezone.utc).isoformat())
        return decision


class FixtureMarket:
    def snapshot(self, symbol):
        return {"quote": {"symbol": symbol, "observed_at": datetime.now(timezone.utc).isoformat(),
            "bid": "100", "ask": "100.1", "bid_size": "1000", "ask_size": "1000",
            "source": "synthetic_fixture", "synthetic": True}}


def run(engine):
    now = datetime.now(timezone.utc).isoformat()
    event = {"source": "synthetic_fixture", "id": now + ":enter", "revision": "1",
             "symbol": "DEMO_EQUITY", "published_at": now,
             "headline": "Synthetic primary event " + now, "body": "Invented demonstration event",
             "url": "https://example.com/fixture", "verified": True, "synthetic": True,
             "verification": "synthetic fixture only",
             "evidence": [{"id": "fixture", "url": "https://example.com/fixture", "text": "Invented demonstration event"}]}
    pipeline = Pipeline(engine, FixtureAdvisor(), FixtureMarket())
    results = [pipeline.process(event), pipeline.process(event),
               pipeline.process(dict(event, id=now + ":secondary", headline="Synthetic secondary story " + now, verified=False)),
               pipeline.process(dict(event, id=now + ":exit", headline="Synthetic exit event " + now))]
    return {"label": "ALL INPUTS AND DECISIONS SYNTHETIC. NO AI/NEWS/API CALLS.",
            "results": results, "account": engine.status()}
