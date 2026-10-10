"""Private-chat controls. No real messages sent unless explicitly enabled by CLI."""
import json
import os
import re
import time
import urllib.request
from .bitget import NoRedirect
from .monitor import RuntimeStore


class Telegram:
    def __init__(self, engine, transport=None, user_id=None, chat_id=None, clock=None):
        self.engine, self.store = engine, RuntimeStore(engine)
        self.transport, self.clock = transport, clock or time.time
        self.user_id = int(user_id if user_id is not None else os.getenv("WQ_TELEGRAM_USER_ID", "0"))
        self.chat_id = int(chat_id if chat_id is not None else os.getenv("WQ_TELEGRAM_CHAT_ID", "0"))
        self.token = os.getenv("WQ_TELEGRAM_BOT_TOKEN", "")
        if self.user_id <= 0 or self.chat_id <= 0:
            raise ValueError("Configure numeric Telegram owner and private chat IDs")
        if not transport and not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", self.token):
            raise ValueError("Configure WQ_TELEGRAM_BOT_TOKEN locally")

    def call(self, method, data):
        if method not in ("getUpdates", "sendMessage"):
            raise ValueError("Unsupported Telegram operation")
        if self.transport:
            return self.transport(method, data)
        try:
            req = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/{method}",
                data=json.dumps(data).encode(), headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.build_opener(NoRedirect()).open(req, timeout=10) as response:
                raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise ValueError("Telegram response too large")
            result = json.loads(raw)
            if not isinstance(result, dict) or result.get("ok") is not True:
                raise ValueError("Telegram request not accepted")
            return result["result"]
        except (OSError, ValueError, KeyError):
            # urllib exceptions contain the URL, which includes the token. Never log them.
            raise ValueError("Telegram request failed; details redacted") from None

    def poll(self):
        offset = self.store.get("telegram_offset", 0)
        updates = self.call("getUpdates", {"offset": offset, "timeout": 0, "limit": 100,
                                          "allowed_updates": ["message"]})
        if not isinstance(updates, list):
            raise ValueError("Invalid Telegram update list")
        if any(not isinstance(u, dict) or type(u.get("update_id")) is not int for u in updates):
            raise ValueError("Invalid Telegram update identity")
        for update in sorted(updates, key=lambda u: u["update_id"]):
            self.handle(update)

    def handle(self, update):
        if not isinstance(update, dict):
            raise ValueError("Invalid Telegram update")
        uid = update.get("update_id")
        if type(uid) is not int:
            raise ValueError("Invalid Telegram update ID")
        db = self.engine.db
        db.execute("BEGIN IMMEDIATE")
        try:
            if uid < self.store.get("telegram_offset", 0):
                db.execute("COMMIT")
                return
            msg = update.get("message", {})
            msg = msg if isinstance(msg, dict) else {}
            sender, chat = msg.get("from", {}), msg.get("chat", {})
            sender = sender if isinstance(sender, dict) else {}
            chat = chat if isinstance(chat, dict) else {}
            date = msg.get("date")
            age = self.clock() - date if type(date) is int else -1
            allowed = (sender.get("id") == self.user_id and not sender.get("is_bot", False)
                       and chat.get("id") == self.chat_id and chat.get("type") == "private"
                       and isinstance(msg.get("text"), str)
                       and not any(k in msg for k in ("forward_origin", "forward_date", "is_automatic_forward", "via_bot"))
                       and 0 <= age <= 120)
            if allowed:
                text = msg.get("text", "").strip()
                command = text.split()[0] if text else ""
                state = self.engine.status()
                if command in ("/pause", "/resume"):
                    stopping = self.store.get("worker_state", {}).get("status") == "stopping"
                    state["paused"] = command == "/pause" or stopping
                    self.engine._save(state)
                    reply = "New entries paused; automatic exits remain active." if state["paused"] else "New entries enabled; fixed risk limits remain active."
                elif command == "/status":
                    reply = (f"LOCAL PAPER | paused={state['paused']} | cash={state['cash']} | "
                             f"realized P&L={state['realized_pnl']} | positions={len(state['positions'])}\n"
                             f"Monitor heartbeat: {self.store.get('monitor_heartbeat', 'not started')}")
                elif command == "/positions":
                    reply = "LOCAL PAPER positions:\n" + ("\n".join(f"{s}: quantity={p['quantity']}, cost={p['cost']}, opened={p.get('opened_at')}" for s,p in state["positions"].items()) or "None")
                else:
                    reply = "Commands: /status /positions /pause /resume. Local paper trading only."
                self.store.notify("telegram-command:" + str(uid), reply)
            # Commit command side effects, reply queue and update offset atomically.
            self.store.put("telegram_offset", uid + 1)
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise

    def collect_fills(self):
        db = self.engine.db
        db.execute("BEGIN IMMEDIATE")
        try:
            cursor = self.store.get("telegram_ledger_cursor", 0)
            for seq, raw in db.execute("SELECT sequence,body FROM ledger WHERE sequence>? ORDER BY sequence LIMIT 100", (cursor,)).fetchall():
                row = json.loads(raw)
                if row.get("fill"):
                    fill = row["fill"]
                    self.store.notify("fill:"+str(seq), f"LOCAL PAPER {fill['direction']} {fill['instrument']} | quantity {fill['quantity']} | price {fill['price']} | {fill['timestamp']}")
                self.store.put("telegram_ledger_cursor", seq)
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise

    def flush(self):
        # One delivery attempt: a timeout may mean delivered. Do not blindly resend.
        for key, text in self.engine.db.execute("SELECT key,text FROM notices WHERE status='pending' ORDER BY rowid LIMIT 5").fetchall():
            changed = self.engine.db.execute("UPDATE notices SET status='sending' WHERE key=? AND status='pending'", (key,)).rowcount
            if not changed:
                continue
            try:
                self.call("sendMessage", {"chat_id": self.chat_id, "text": text})
                status = "sent"
            except ValueError:
                status = "delivery_unknown"
            self.engine.db.execute("UPDATE notices SET status=? WHERE key=?", (status, key))

    def tick(self):
        self.poll()
        self.collect_fills()
        self.flush()
