"""No exchange credentials or live order paths exist in this module."""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
import hashlib
import json
import sqlite3
import uuid


def number(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid number") from exc
    if not result.is_finite():
        raise ValueError("Non-finite number")
    return result


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Timestamp must be a string")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamp requires a timezone")
    return result.astimezone(timezone.utc)


@dataclass(frozen=True)
class Risk:
    initial_cash: str = "10000"
    position_cap: str = "0.10"
    exposure_cap: str = "0.30"
    realized_loss_cap: str = "0.02"
    minimum_confidence: str = "0.75"
    maximum_spread: str = "0.005"
    fee_rate: str = "0.001"
    slippage_rate: str = "0.0005"
    event_age_seconds: int = 900
    quote_age_seconds: int = 15
    symbols: tuple = ("DEMO_EQUITY",)

    def __post_init__(self):
        if number(self.initial_cash) <= 0:
            raise ValueError("Initial cash must be positive")
        for key in ("position_cap", "exposure_cap", "realized_loss_cap", "minimum_confidence", "maximum_spread"):
            if not 0 < number(getattr(self, key)) <= 1:
                raise ValueError(f"{key} must be within (0,1]")
        for key in ("fee_rate", "slippage_rate"):
            if not 0 <= number(getattr(self, key)) < 1:
                raise ValueError(f"{key} must be within [0,1)")
        if self.event_age_seconds <= 0 or self.quote_age_seconds <= 0:
            raise ValueError("Freshness windows must be positive")
        if not self.symbols or len(set(self.symbols)) != len(self.symbols):
            raise ValueError("Allowlist must be nonempty and unique")


class Engine:
    """Transactional paper ledger; one serialized transaction per event revision.

    Input decisions may come from an LLM adapter, but never control risk limits.
    The fixture advisor used by the CLI is explicitly not an LLM.
    """
    def __init__(self, path, risk=None):
        self.risk = risk or Risk()
        self.db = sqlite3.connect(path, isolation_level=None, timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ledger (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT UNIQUE NOT NULL, body TEXT NOT NULL);
        """)
        self.db.execute("INSERT OR IGNORE INTO state VALUES (1, ?)", (json.dumps({
            "cash": self.risk.initial_cash, "realized_pnl": "0",
            "positions": {}, "paused": False, "risk": asdict(self.risk)
        }),))
        # A persisted account cannot silently be reopened with different limits.
        expected = json.loads(json.dumps(asdict(self.risk)))
        if self.status()["risk"] != expected:
            self.db.close()
            raise ValueError("Database risk settings differ; use its original settings")
        self._migrate_positions()

    def _migrate_positions(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            state = self.status()
            for symbol, position in state["positions"].items():
                if "position_id" not in position:
                    buys = [r for r in self.records() if r.get("fill") and
                            r["fill"]["instrument"] == symbol and r["fill"]["direction"] == "buy"]
                    position["position_id"] = uuid.uuid4().hex
                    position["opened_at"] = buys[-1]["fill"]["timestamp"] if buys else None
            self._save(state)
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def close(self):
        self.db.close()

    def status(self):
        return json.loads(self.db.execute("SELECT body FROM state WHERE id=1").fetchone()[0])

    def records(self):
        return [json.loads(row[0]) for row in self.db.execute("SELECT body FROM ledger ORDER BY sequence")]

    def _save(self, state):
        self.db.execute("UPDATE state SET body=? WHERE id=1", (json.dumps(state),))

    def pause(self, enabled):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            state = self.status()
            state["paused"] = enabled
            self._save(state)
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def process(self, event, decision, quote, now=None):
        if not all(isinstance(x, dict) for x in (event, decision, quote)):
            raise ValueError("Event, decision and quote must be JSON objects")
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("Processing clock requires timezone")
        # Hash the identity tuple; content changes require a new revision.
        identity = [event.get(k) for k in ("source", "id", "revision")]
        if not all(isinstance(x, str) and x.strip() for x in identity):
            raise ValueError("Event requires nonempty string source, id, revision")
        key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            previous = self.db.execute("SELECT body FROM ledger WHERE event_key=?", (key,)).fetchone()
            if previous:
                result = {"status": "duplicate", "event_key": key}
            else:
                state = self.status()
                result = {
                    "mode": "LOCAL_PAPER", "event_key": key,
                    "processed_at": now.isoformat(), "event": event,
                    "decision": decision, "quote": quote, "status": "rejected",
                    "cash_before": state["cash"], "fill": None,
                }
                try:
                    candidate = json.loads(json.dumps(state))
                    result.update(self._execute(candidate, event, decision, quote, now))
                    state = candidate
                except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                    result["reason"] = str(exc)
                result["cash_after"] = state["cash"]
                result["balance_change"] = str(number(state["cash"]) - number(result["cash_before"]))
                result["realized_pnl_total"] = state["realized_pnl"]
                self._save(state)
                self.db.execute("INSERT INTO ledger(event_key,body) VALUES (?,?)", (key, json.dumps(result, allow_nan=False)))
            self.db.execute("COMMIT")
            return result
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def _execute(self, state, event, decision, quote, now):
        r = self.risk
        symbol = event["symbol"]
        if symbol not in r.symbols:
            raise ValueError("Instrument is not in the configured allowlist")
        action = decision["action"]
        if action not in ("enter", "hold", "reduce", "exit", "skip"):
            raise ValueError("Unknown action")
        confidence = number(decision["confidence"])
        if not 0 <= confidence <= 1:
            raise ValueError("Confidence outside [0,1]")
        if not isinstance(decision["rationale"], str) or not decision["rationale"].strip():
            raise ValueError("Decision needs a rationale")
        if action in ("hold", "skip"):
            return {"status": "no_trade", "reason": decision["rationale"]}
        if "decision_started_at" in decision:
            decision_age = (now - timestamp(decision["decision_started_at"])).total_seconds()
            if not 0 <= decision_age <= 60:
                raise ValueError("AI decision expired; maximum age is 60 seconds from request start")
        if quote.get("unavailable_reason"):
            raise ValueError("Market data unavailable: " + str(quote["unavailable_reason"]))
        if quote["symbol"] != symbol:
            raise ValueError("Quote instrument mismatch")
        age = (now - timestamp(quote["observed_at"])).total_seconds()
        if not 0 <= age <= r.quote_age_seconds:
            raise ValueError("Stale or future quote")
        if "received_at" in quote:
            received_age = (now - timestamp(quote["received_at"])).total_seconds()
            if not 0 <= received_age <= r.quote_age_seconds:
                raise ValueError("Stale or future receipt time")
        limits = quote.get("instrument_limits", {})
        precision = limits.get("quantityPrecision", 6)
        if type(precision) is not int or not 0 <= precision <= 12:
            raise ValueError("Invalid quantity precision")
        step = Decimal(1).scaleb(-precision)
        bid, ask = number(quote["bid"]), number(quote["ask"])
        if bid <= 0 or ask < bid:
            raise ValueError("Invalid bid/ask")
        if (ask - bid) / ((ask + bid) / 2) > number(r.maximum_spread):
            raise ValueError("Spread limit exceeded")
        position = state["positions"].get(symbol)
        if "expected_position_id" in decision:
            actual_id = position.get("position_id") if position else None
            if actual_id != decision["expected_position_id"]:
                raise ValueError("Position changed while decision was pending")
        if "expected_position_quantity" in decision:
            actual_qty = number(position["quantity"]) if position else None
            expected_qty = decision["expected_position_quantity"]
            expected_qty = number(expected_qty) if expected_qty is not None else None
            if actual_qty != expected_qty:
                raise ValueError("Position quantity changed while decision was pending")
        cash = number(state["cash"])
        fee_rate, slip = number(r.fee_rate), number(r.slippage_rate)
        if action == "enter":
            if state["paused"]:
                raise ValueError("New entries paused")
            if position:
                raise ValueError("Already holding instrument; no pyramiding")
            age = (now - timestamp(event["published_at"])).total_seconds()
            if not 0 <= age <= r.event_age_seconds:
                raise ValueError("Stale or future event")
            if event.get("verified") is not True or not str(event.get("url", "")).startswith("https://"):
                raise ValueError("Entry requires a verified source with HTTPS evidence URL")
            if confidence < number(r.minimum_confidence):
                raise ValueError("Confidence below entry threshold")
            if number(state["realized_pnl"]) <= -number(r.initial_cash) * number(r.realized_loss_cap):
                raise ValueError("Session realized-loss limit reached")
            exposure = sum((number(p["cost"]) for p in state["positions"].values()), Decimal(0))
            budget = min(number(decision["notional"]), cash,
                         number(r.initial_cash) * number(r.position_cap),
                         number(r.initial_cash) * number(r.exposure_cap) - exposure)
            price = ask * (1 + slip)
            qty = (budget / (price * (1 + fee_rate))).quantize(step, rounding=ROUND_DOWN)
            side, depth = "buy", number(quote["ask_size"])
        else:
            if not position:
                raise ValueError("No position to close")
            fraction = Decimal(1) if action == "exit" else number(decision["fraction"])
            if not 0 < fraction <= 1:
                raise ValueError("Reduction fraction outside (0,1]")
            qty = (number(position["quantity"]) * fraction).quantize(step, rounding=ROUND_DOWN)
            price = bid * (1 - slip)
            side, depth = "sell", number(quote["bid_size"])
        if qty <= 0:
            raise ValueError("Order is below minimum simulated quantity")
        if qty > depth:
            raise ValueError("Insufficient displayed liquidity; no partial fills")
        gross = price * qty
        if limits:
            if qty < number(limits["minOrderQty"]) or gross < number(limits["minOrderAmount"]):
                raise ValueError("Below instrument minimum quantity or notional")
            maximum = number(limits["maxOrderQty"])
            if maximum > 0 and qty > maximum:
                raise ValueError("Above instrument maximum quantity")
        fee = gross * fee_rate
        # All validations precede account mutation.
        realized = Decimal(0)
        if side == "buy":
            cost = gross + fee
            state["cash"] = str(cash - cost)
            state["positions"][symbol] = {"quantity": str(qty), "cost": str(cost),
                                         "position_id": uuid.uuid4().hex, "opened_at": now.isoformat()}
        else:
            allocated_cost = number(position["cost"]) * qty / number(position["quantity"])
            realized = gross - fee - allocated_cost
            state["cash"] = str(cash + gross - fee)
            remaining = number(position["quantity"]) - qty
            if remaining == 0:
                del state["positions"][symbol]
            else:
                state["positions"][symbol] = {**position, "quantity": str(remaining), "cost": str(number(position["cost"]) - allocated_cost)}
            state["realized_pnl"] = str(number(state["realized_pnl"]) + realized)
        return {"status": "filled", "reason": "Paper risk checks passed",
                "fill": {"timestamp": now.isoformat(), "instrument": symbol,
                         "direction": side, "price": str(price), "quantity": str(qty),
                         "fee": str(fee), "realized_pnl": str(realized)}}
