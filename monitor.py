"""Fixed exit rules for local paper positions. Does not submit exchange orders."""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
import uuid
from .core import number, timestamp


@dataclass(frozen=True)
class ExitPolicy:
    stop_loss: str = "0.02"
    take_profit: str = "0.04"
    max_hold_seconds: int = 14400

    def __post_init__(self):
        if not 0 < number(self.stop_loss) < 1 or not 0 < number(self.take_profit) < 10:
            raise ValueError("Invalid exit percentages")
        if type(self.max_hold_seconds) is not int or self.max_hold_seconds <= 0:
            raise ValueError("Holding time must be positive whole seconds")


class RuntimeStore:
    def __init__(self, engine):
        self.db = engine.db
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS runtime (key TEXT PRIMARY KEY, body TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS notices (key TEXT PRIMARY KEY, text TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending');
        """)

    def get(self, key, default=None):
        row = self.db.execute("SELECT body FROM runtime WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO runtime VALUES (?,?)", (key, json.dumps(value)))

    def notify(self, key, text):
        self.db.execute("INSERT OR IGNORE INTO notices(key,text) VALUES (?,?)", (key, text[:3500]))


class Monitor:
    def __init__(self, engine, market, policy=None, clock=None):
        self.engine, self.market = engine, market
        self.policy = policy or ExitPolicy()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.store = RuntimeStore(engine)
        self.store.put("exit_policy", asdict(self.policy))

    def tick(self):
        checks = []
        for symbol, position in self.engine.status()["positions"].items():
            check = {"symbol": symbol, "position_id": position["position_id"], "status": "monitoring"}
            try:
                snapshot = self.market.snapshot(symbol)
                quote = snapshot.get("quote")
                if not quote:
                    raise ValueError(snapshot.get("blocked_reason", "No usable quote"))
                now = self.clock()
                age = (now - timestamp(quote["observed_at"])).total_seconds()
                if quote.get("symbol") != symbol or not 0 <= age <= self.engine.risk.quote_age_seconds:
                    raise ValueError("Stale, future or mismatched monitoring quote")
                bid = number(quote["bid"])
                if bid <= 0:
                    raise ValueError("Invalid monitoring bid")
                # Conservative hypothetical liquidation value, net of modeled fees/slippage.
                net_value = number(position["quantity"]) * bid * (1-number(self.engine.risk.slippage_rate)) * (1-number(self.engine.risk.fee_rate))
                change = net_value / number(position["cost"]) - 1
                if not position.get("opened_at"):
                    raise ValueError("Missing original entry timestamp; inspect legacy position")
                held = (now - timestamp(position["opened_at"])).total_seconds()
                if held < 0:
                    raise ValueError("Entry timestamp is in the future")
                reason = ("stop_loss" if change <= -number(self.policy.stop_loss) else
                          "take_profit" if change >= number(self.policy.take_profit) else
                          "time_limit" if held >= self.policy.max_hold_seconds else None)
                check.update(net_liquidation_value=str(net_value), return_fraction=str(change),
                             held_seconds=held, checked_at=now.isoformat(), trigger=reason)
                if reason:
                    event = {"source": "fixed_exit_monitor", "id": position["position_id"],
                        "revision": uuid.uuid4().hex, "symbol": symbol, "published_at": now.isoformat()}
                    decision = {"action": "exit", "confidence": "1", "rationale": reason,
                        "advisor": "fixed_risk_rule", "expected_position_id": position["position_id"]}
                    result = self.engine.process(event, decision, quote, now)
                    check.update(status=result["status"], execution=result)
                    self.store.notify("exit:"+position["position_id"]+":"+result["status"],
                        f"LOCAL PAPER {symbol}: {reason}; {result['status']}. {result.get('reason', '')}")
                # Store one latest check per instrument; ledger retains every attempted exit.
            except (ValueError, KeyError, TypeError, OSError) as exc:
                check.update(status="blocked", reason=str(exc) if isinstance(exc, ValueError) else type(exc).__name__,
                             checked_at=self.clock().isoformat())
                self.store.notify("blocked:"+position["position_id"], f"LOCAL PAPER {symbol}: monitoring blocked; inspect worker status.")
            self.store.put("monitor:" + symbol, check)
            checks.append(check)
        self.store.put("monitor_heartbeat", self.clock().isoformat())
        return checks
