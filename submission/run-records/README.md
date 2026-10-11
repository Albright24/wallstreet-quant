# Wallstreet Quant run records

## Scope and provenance

This folder separates the observed VPS run from an existing synthetic execution fixture. It does **not** claim a completed autonomous trade driven by fresh real-world news, a backtest, or a profitable strategy.

## 1. Observed VPS account snapshot

- Captured from the public connected dashboard API: https://wallstreet-quant-demo.vercel.app/api/state
- Snapshot timestamp (UTC): **2026-10-11T06:21:55.200400+00:00**.
- Mode: PAPER; virtual cash: 10,000; open positions: 0.
- Returned execution-ledger records: **0**.
- Returned workflow records: **5**, all blocked because news was stale or future-dated; each record says no model call.
- [VPS account snapshot (JSON)](vps-account-snapshot.json) preserves the API response, including event timestamps and block reasons.
- This endpoint returns at most the latest 200 ledger and workflow records, not a guaranteed complete historical export. At capture it returned zero ledger entries and five workflow entries.

These records demonstrate deployed workflow checks, not completed paper trades. Snapshot `as_of` is the account read time, not proof of a worker heartbeat.

## 2. Synthetic paper-execution fixture

- [Paper-fill table (CSV)](synthetic-paper-fills.csv) contains the three filled records from the pre-existing [complete synthetic ledger](../../examples/synthetic-ledger.jsonl).
- Columns include timestamp, instrument, buy/sell direction, execution price, quantity, fee, account balances and balance change.
- The complete ledger also includes one hold/no-trade result and one stale-event rejection.
- Instrument: `DEMO_EQUITY`, a fictional fixture symbol. News, quotes and decisions are synthetic; **no model or exchange API was called for this fixture**.
- The three fills share a timestamp because they were generated in a deterministic test sequence. They are not independently timed market observations.
- A fixture `verified: true` field exercises a test path; it does not mean a real-world headline was verified.
- This is a pre-existing development fixture, not an export of the narrated video session or the VPS trading account. Its simulated profit is not evidence of strategy performance.

### Reproduce the fixture logic

From the repository root, choose a new database filename and run:

```bash
python -m wallstreet_quant --db submission-fixture-new.sqlite3 demo
python -m wallstreet_quant --db submission-fixture-new.sqlite3 export submission-fixture-new.jsonl
```

This deliberately runs the offline fixture without news, AI or exchange calls. A new run uses new timestamps and event IDs, so it will not reproduce the original file byte-for-byte. Source: [demo entry point](../../wallstreet_quant/__main__.py) and [paper engine](../../wallstreet_quant/core.py).

## Submission limitation

The supplied submission form requests paper-trading logs for Agentic Trading. These files expose all available evidence in this package, but **the synthetic fixture is not a substitute for an observed autonomous market-data paper-trading history**. Acceptance of this evidence is for the organizers to determine. No missing trades, prices, timestamps or performance metrics have been fabricated.
