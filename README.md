# Wallstreet Quant

AI-assisted event analysis and risk-controlled local paper trading for Bitget tokenized US equities.

**Hackathon prototype · Python 3.10+ · Version 0.6.4 · No real-money order submission**

Wallstreet Quant separates news collection, model proposals and execution rules. The model can propose an action, but deterministic checks decide whether a local paper fill is allowed. Every processed decision retains its evidence and outcome in an audit ledger.

## What works

- Public Bitget instrument discovery, ticker snapshots and tokenized-stock mapping.
- Telegram public-channel collection using a locally authorized Telethon user session, with cursors and edit history.
- Company/product matching, original-publication freshness checks, heuristic content filtering and repeated-story grouping.
- Qwen through Groq with structured JSON decisions and local evidence validation. Windows uses built-in PowerShell for HTTP requests; no Groq SDK installation is required.
- News → model → refreshed quote → risk checks → local paper execution pipeline.
- Duplicate detection, a daily model-request cap, long-only position limits, simulated fees/slippage, pause controls and a SQLite audit ledger.
- Optional worker, exit monitoring and private Telegram controls are implemented; their continuous live operation has not been verified.

## Validation and current limits

| Check | Result |
| --- | --- |
| Local automated suite | 94 tests passed |
| Windows Groq/Qwen connection | User-run check passed with validated JSON and `skip` on empty evidence |
| Real Qwen + synthetic rumor + simulated quote | `skip`, no paper fill, unchanged 10,000-unit test balance |
| Duplicate protection | Repeated event made no additional model call |
| Stale-event protection | Distinct old event blocked before model/market calls |
| Telegram collection/review | User-run review contained 4,397 posts and 152 stock-event rows |
| Fresh Telegram → Qwen analysis | Not verified: all posts in the latest review were older than 15 minutes, so zero AI calls were made |

The synthetic demo is **not a backtest or evidence of profitability**. There is no two-week paper-performance record. Local simulated fills are not Bitget exchange-demo orders. No live exchange execution, deployed dashboard or VPS deployment is included. The complete fresh Telegram-to-AI path is not yet demonstrated and Telegram collection remains separate from the continuous trading worker.

## Run the offline demo

Clone or download the repository, open its root folder, and use Python 3.10 or newer:

```sh
python -m wallstreet_quant --db demo.sqlite3 demo
python -m wallstreet_quant --db demo.sqlite3 status
python -m wallstreet_quant --db demo.sqlite3 export demo-ledger.jsonl
python -m wallstreet_quant --db agent-demo.sqlite3 agent-demo
```

On systems where the executable is named `python3`, substitute it for `python`.
No keys, paid API, network or third-party packages are needed for these demos.
Use a new database filename for an isolated run. Existing files preserve balances and history.

`demo` illustrates enter, hold, reduce, exit, duplicate and stale-event checks.
`agent-demo` illustrates orchestration with a hand-authored advisor and simulated prices; it does **not** call an LLM. All fixture events and prices are labelled synthetic, including their fixture-only verification flags.

Run the automated tests:

```sh
python -m unittest discover -s tests -v
```

## Test real AI without trading

See [GROQ-SETUP.md](GROQ-SETUP.md) for entering your key privately in PowerShell.
Select `WQ_AI_PROVIDER=groq` and `WQ_MODEL=qwen/qwen3.8-27b`, then run:

```sh
python -m wallstreet_quant.ai_check
```

This sends one synthetic test to Groq and places no trades. It validates structured output and abstention; it does not establish investment quality. Provider availability and free-tier limits may change. Keys remain local environment variables and are not included in this repository.

Windows Groq connectivity was verified by the builder. Linux uses urllib and requires its own connectivity check. AgentRouter is a legacy experimental adapter; the builder's standalone Python requests were denied by that provider's supported-client policy. Use Groq for the demonstrated path.

## Telegram collection and review

Install the optional collector dependency and follow [TELEGRAM-READER.md](TELEGRAM-READER.md) for local authorization:

```sh
python -m pip install -r requirements-telegram.txt
python -m wallstreet_quant.collector run --cycles 1
python -m wallstreet_quant.news_review
```

Review never upgrades Telegram posts to verified evidence. Repeated headlines do not establish independent corroboration. Old posts retain their original publication age; edits do not make them fresh. Collection delay includes historical catch-up and is not a measurement of news-wire latency.

## Architecture and risk controls

`collector / news adapters → normalized events → review / pipeline → advisor → fixed risk checks → local paper ledger`

The model has no trading tools. It returns proposals with evidence IDs, rationale and uncertainty; local code rejects invalid references and malformed responses. The pipeline blocks trading proposals based on unverified secondary news. The paper engine uses Decimal accounting and transactional updates.

Illustrative defaults: 10% per-position cost cap, 30% total cost exposure cap, 2% cumulative realized-loss breaker, 15-minute event age and 15-second quote age. These are not calibrated or proven trading parameters. The loss breaker excludes unrealized losses and is not a full portfolio drawdown control. Exposure is measured at entry cost. Exits require a running worker and valid quotes.

Primary-source provenance does not prove every claim in a document. Verification flags from trusted adapters still need stronger claim-level corroboration. The synthetic matching model assumes fills within displayed top-of-book size and does not reproduce an exchange matching engine.

## Documentation

- [GROQ-SETUP.md](GROQ-SETUP.md): demonstrated model setup.
- [BITGET-INTEGRATION.md](BITGET-INTEGRATION.md): public market-data scope.
- [TELEGRAM-READER.md](TELEGRAM-READER.md): optional collector setup.
- [TELEGRAM-SOURCES.md](TELEGRAM-SOURCES.md): source registry and limitations.
- [NEWS-AI-SETUP.md](NEWS-AI-SETUP.md): news and model configuration.
- [WORKER-SETUP.md](WORKER-SETUP.md): optional worker and private controls.
- [VALIDATION.md](VALIDATION.md): chronological testing notes; this README summarizes current status.
- `examples/`: labelled synthetic outputs and dated public-data snapshots, not current market quotes.

## Submission scope

Built for the Bitget AI Base Camp Hackathon S2. This repository presents an event-driven trading-agent prototype with local paper execution. It does not claim organizer approval, competition eligibility, returns or a completed live deployment. Document the actual test duration and submit only genuine logs.

## Keep private files out of GitHub

Do not commit API keys, `.env` files, Telegram sessions, local databases, exports of collected channel posts or personal account information. The included `.gitignore` covers common generated and credential-bearing files. Inspect changes before committing; ignore rules do not remove secrets already committed.
