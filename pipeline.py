"""Persistent, bounded news -> model -> fresh quote -> local paper execution."""
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from .core import timestamp
from .bitget import process_live_paper


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


class Pipeline:
    def __init__(self, engine, advisor, market, daily_call_limit=20, clock=None):
        if type(daily_call_limit) is not int or not 1 <= daily_call_limit <= 100:
            raise ValueError("Daily call limit must be 1–100")
        self.engine, self.advisor, self.market = engine, advisor, market
        self.limit = daily_call_limit
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        engine.db.execute("""CREATE TABLE IF NOT EXISTS news_runs (
            run_key TEXT PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL,
            day TEXT NOT NULL, model_reserved INTEGER NOT NULL, body TEXT NOT NULL)""")

    def records(self):
        return [json.loads(r[0]) for r in self.engine.db.execute("SELECT body FROM news_runs ORDER BY rowid")]

    def process(self, event):
        now = self.clock()
        required = ("source", "id", "revision", "symbol", "headline", "body", "url", "published_at")
        if not isinstance(event, dict) or any(not isinstance(event.get(k), str) or not event[k].strip() for k in required):
            raise ValueError("Normalized news event is incomplete")
        published = timestamp(event["published_at"])
        evidence = event.get("evidence")
        if not isinstance(evidence, list) or not evidence or any(
            not isinstance(x, dict) or not isinstance(x.get("id"), str) or not isinstance(x.get("text"), str)
            for x in evidence):
            raise ValueError("Normalized news requires evidence records")
        key = digest([event[k] for k in ("source", "id", "revision", "symbol")])
        fingerprint = digest([event["symbol"], published.date().isoformat(),
                              " ".join(event["headline"].lower().split()), " ".join(event["body"].lower().split())])
        record = {"run_key": key, "event": event, "started_at": now.isoformat(),
                  "status": "started", "decision": None, "execution": None}
        age = (now - published).total_seconds()
        preflight = None
        if event["symbol"] not in self.engine.risk.symbols:
            preflight = "Instrument outside configured allowlist"
        elif not 0 <= age <= self.engine.risk.event_age_seconds:
            preflight = "News is stale or future-dated; no model call"
        elif self.engine.status()["paused"] and event["symbol"] not in self.engine.status()["positions"]:
            preflight = "Entries paused and no position to manage; no model call"
        db = self.engine.db
        db.execute("BEGIN IMMEDIATE")
        try:
            if db.execute("SELECT 1 FROM news_runs WHERE run_key=? OR fingerprint=?", (key, fingerprint)).fetchone():
                db.execute("COMMIT")
                return {"status": "duplicate", "run_key": key, "model_called": False}
            used = db.execute("SELECT COALESCE(SUM(model_reserved),0) FROM news_runs WHERE day=?", (now.date().isoformat(),)).fetchone()[0]
            if not preflight and used >= self.limit:
                preflight = "Daily model-call budget exhausted"
            if preflight:
                record.update(status="blocked", reason=preflight)
            # A claim survives process crashes and prevents automatic paid retries.
            db.execute("INSERT INTO news_runs VALUES (?,?,?,?,?)", (key, fingerprint, now.date().isoformat(),
                       0 if preflight else 1, json.dumps(record, allow_nan=False)))
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        if preflight:
            return record
        try:
            account = self.engine.status()
            decision = self.advisor.decide(event, account)
            position = account["positions"].get(event["symbol"])
            decision["expected_position_id"] = position.get("position_id") if position else None
            decision["expected_position_quantity"] = position.get("quantity") if position else None
            record["decision"] = decision
            record["status"] = "decided"
            self._save(record)  # Preserve proposal even if process dies during quote fetch.
            if event.get("verified") is not True and decision["action"] in ("enter", "reduce", "exit"):
                record.update(status="blocked", reason="Secondary news lacks primary-source corroboration")
            elif not 0 <= (self.clock() - published).total_seconds() <= self.engine.risk.event_age_seconds:
                record.update(status="blocked", reason="News expired while model was running")
            else:
                execution_event = dict(event, id=event["id"] + ":pipeline:" + event["symbol"])
                record["execution"] = process_live_paper(self.engine, self.market, execution_event, decision)
                record["status"] = "finished"
        except (ValueError, KeyError, TypeError, OSError) as exc:
            # Fixed, local errors only; never dump transport responses or credentials.
            record.update(status="error", reason=str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
        record["finished_at"] = self.clock().isoformat()
        self._save(record)
        return record

    def _save(self, record):
        self.engine.db.execute("UPDATE news_runs SET body=? WHERE run_key=?", (json.dumps(record, allow_nan=False), record["run_key"]))
