# Continuous worker and Telegram — v0.4

## Current state

54 automated tests pass in v0.4.1. New monitoring and Telegram code was tested locally
using controlled quotes and mocked Telegram requests. No Telegram message was
sent to a real account. No VPS worker was deployed or left running.
Bitget public market data was live-tested in v0.2; the AI/news services still need
the credentials and validation described in `NEWS-AI-SETUP.md`.

## Start with a bounded check

From the extracted project folder on Windows:

```powershell
python -m wallstreet_quant --config config/bitget-paper.json --db agent-paper.sqlite3 worker --cycles 1 --exit-policy config/exits.json
python -m wallstreet_quant --config config/bitget-paper.json --db agent-paper.sqlite3 worker-status
```

Use `python3` on Linux. This checks existing positions once, then exits with
entries paused. An empty account needs no network calls. No positions are opened
by monitoring alone. Use the same risk config and database for every command.
Existing version 0.1–0.3 positions receive IDs and their original entry time from
the ledger. A legacy position without a discoverable entry time is blocked for
operator review rather than assigned an invented timestamp.

## Continuous monitoring

```powershell
python -m wallstreet_quant --config config/bitget-paper.json --db agent-paper.sqlite3 worker --exit-policy config/exits.json
```

Default monitoring interval is 5 seconds, measured between iteration starts when
requests finish in time. Open positions are checked sequentially; network delays
can make a cycle longer. This is not a guaranteed 5-second stop service.

`config/exits.json` contains illustrative, uncalibrated paper settings:

| Rule | Default |
|---|---|
| Stop-loss trigger | Net hypothetical liquidation return at or below -2% |
| Take-profit trigger | Net hypothetical liquidation return at or above +4% |
| Maximum holding time | 4 hours from original entry |

Returns use current bid, simulated exit slippage/fee and remaining entry cost,
including entry fees. Partial reductions preserve original age and position ID.
These are software test settings, not an optimized or proven trading strategy.

An exit trigger proposes a full local paper close. The normal spread, freshness,
depth and instrument minimum checks still apply. If they fail, the holding stays
open, the rejection is recorded, and a later cycle retries with a fresh quote.
This can happen during outages, illiquid markets or dust positions. These rules
do not place exchange-native stop orders and cannot guarantee a maximum loss.

Pausing stops new entries; monitoring continues to manage open positions. Ctrl+C
or SIGTERM pauses entries, waits for in-flight tasks, then stops. Open positions
remain open if no exit succeeded. Monitoring is inactive while the program is
stopped or the laptop sleeps. A hard process kill releases the OS worker lock,
but cannot perform graceful shutdown. Always inspect status after restart.

One worker or `monitor-once` command may own a database at a time. The lock is
single-host, not distributed/NFS coordination. The Linux lock was tested; the
Windows branch needs a check on your laptop. Do not run duplicate copies against
different aliases of the same file.

## Enable news and AI

After local credentials are configured:

```powershell
python -m wallstreet_quant --config config/bitget-paper.json --db agent-paper.sqlite3 resume
python -m wallstreet_quant --config config/bitget-paper.json --db agent-paper.sqlite3 worker --enable-news --news-interval 60 --daily-calls 20 --exit-policy config/exits.json
```

One news task rotates over the configured companies and checks the latest eligible
SEC filing for one company per interval. Five companies therefore take at least
five minutes per rotation at the default. This can miss multiple intervening
filings and is not a low-latency news stream. Broader filing coverage and streaming
news remain future work. Old filings are rejected before a model call.

The news task has its own database connection and network client. Slow model
requests do not prevent the main monitoring loop from running. Position identity
and quantity are rechecked before execution so an old proposal cannot act on a
new or partially changed position. The model-call cap remains per database and
UTC day; it is not a guaranteed dollar spending cap.

## Optional Telegram controls

Create your own Telegram bot through the official BotFather flow and configure
these environment variables locally:

| Variable | Value |
|---|---|
| `WQ_TELEGRAM_BOT_TOKEN` | Your bot token |
| `WQ_TELEGRAM_USER_ID` | Your numeric Telegram user ID |
| `WQ_TELEGRAM_CHAT_ID` | The numeric ID of your private chat with the bot |

Start a private conversation with your bot. This integration uses polling, so a
webhook must not already be active for the bot. The program does not remove a
webhook automatically. Avoid putting your token in screenshots or chat messages.

Add `--enable-telegram` when starting the worker. Enabling this flag allows the
program to send command replies and local paper alerts to the configured chat.
Telegram has a separate background task; it cannot submit exchange orders.

| Command | Effect |
|---|---|
| `/status` | Cash, realized P&L, pause state, position count, monitor heartbeat |
| `/positions` | Open paper quantities, cost and original entry times |
| `/pause` | Stop new entries; keep automatic exit checks active |
| `/resume` | Allow new entries, subject to all fixed risk checks |

Both sender and private chat ID must match. Forwarded or malformed messages,
group messages, other users, messages
older than two minutes and replayed updates cannot issue commands. During shutdown,
`/resume` cannot override the pause. Commands, reply queue and update offset are
committed together so replay after a crash does not repeat an old action.

Fill alerts include timestamps and may include older ledger fills when Telegram
is first enabled on an existing database. Monitoring failures are notified once
per position; each exit outcome is also queued. Inspect `worker-status` for current
health rather than relying solely on messages.

Notification delivery has one automatic attempt. If a timeout makes delivery
uncertain, the queue records `delivery_unknown`; a process crash during sending
can leave `sending`. Neither is automatically resent. This avoids repeated
messages but can lose an alert. Local ledgers remain the authoritative record.
Notice rows and the execution ledger need a future retention/archival policy.

## What still remains

- Live account validation for model/news/Telegram, followed by VPS deployment.
- Dashboard and the hackathon demonstration/submission materials.
- Claim-level news corroboration and broader document/exhibit coverage.
- Portfolio-wide mark-to-market risk, session enforcement and production recovery.
- Bitget exchange demo execution, after verifying those instruments are supported.

Official Telegram reference: https://core.telegram.org/bots/api
