# Bitget connection — v0.2, October 6, 2026

## What is connected

The program now makes real GET requests to `https://api.bitget.com` without API
credentials. It discovers SPOT instruments and fetches public tickers, then checks
their timestamps, prices and quantities before local paper simulation. Nothing
in this package submits orders to Bitget or accesses account balances.

The live instrument list returned these Reality symbols:

| Underlying stock | Bitget instrument |
|---|---|
| NVIDIA | RNVDAUSDT |
| Tesla | RTSLAUSDT |
| Apple | RAAPLUSDT |
| Microsoft | RMSFTUSDT |
| Amazon | RAMZNUSDT |

This is an initial watchlist, not proof of hackathon eligibility or a profitable
selection. Availability can change. `online` metadata alone does not establish
that a particular account can trade the instrument.

## Run on Windows

Extract the ZIP and open PowerShell inside the `wallstreet-quant` folder:

```powershell
python -m wallstreet_quant instruments --stocks
python -m wallstreet_quant quote RNVDAUSDT
python -m wallstreet_quant market-reference
python -m unittest discover -s tests -v
```

On Linux use `python3`. Python 3.10+ is required; no external packages are needed.
`quote` returns raw metadata plus `paper_quote_usable` and either the normalized
quote or a `blocked_reason`. A successful HTTP request can still return an unusable
quote. Saved examples are historical snapshots, not current prices.

## Send a decision through real quotes

Prepare `decision.json` with the `event` and `decision` objects described in the
README. Use a configured Bitget symbol and truthful source-verification status.
The event must contain its actual publication timestamp. A model/news adapter has
not been connected yet; supplying a decision by file does not make it an AI decision.

```powershell
python -m wallstreet_quant --config config/bitget-paper.json --db bitget-paper.sqlite3 paper-live decision.json
python -m wallstreet_quant --config config/bitget-paper.json --db bitget-paper.sqlite3 status
python -m wallstreet_quant --config config/bitget-paper.json --db bitget-paper.sqlite3 export bitget-paper.jsonl
python -m wallstreet_quant --config config/bitget-paper.json --db bitget-paper.sqlite3 pause
```

Use the same config for every command on that database. Keep offline/synthetic
tests in a separate database. `paper-live` ignores supplied quotes: it fetches the
instrument and THEN its ticker, after the supplied decision already exists.
Network or unusable-data failures produce rejected ledger records for trade
attempts. Hold/skip remain no-trade decisions. Each event revision is processed
once; use an intentional new revision to reassess after a failed attempt.

## Data and execution boundaries

- The ticker route exposes best bid/ask fields. Missing or zero sizes cannot be
  replaced with estimated volume, last price or made-up liquidity.
- Reality full order-book access uses a separate authenticated, whitelisted
  endpoint. This release does not call it or claim to have that access.
- The client retains exchange `ts` rather than relabelling an old price as fresh.
  A quote older than 15 seconds or ahead of the local clock is blocked. Keep the
  host clock synchronized. Requests are bounded and public GET retries are limited.
- Paper quantities respect quantity precision, minimum quantity and minimum
  notional from current metadata. `maxOrderQty=0` is treated as uncapped for this
  simulation. Price rounding, market-specific amount caps and actual order
  acceptance still need exchange-broker implementation.
- Fee and slippage values are simulation assumptions, not verified account fees.
  Full fills are assumed only within reported best-quote size.
- Stock-info, session and holiday APIs are exposed for inspection. No session
  enforcement is implemented: a fresh ticker does not itself prove the exchange
  would execute a trade. The reference API response shape is retained unmodified.
- The 2% breaker is cumulative realized loss from initial paper capital. It is
  not a daily or mark-to-market drawdown limit. Version 0.4 adds per-position
  automatic exit checks while the continuous worker is running.
- Identical news from different publishers is not merged yet. Adapters must
  provide stable event IDs/revisions, including instrument identity when splitting
  a multi-company story into separate events.

## What comes next

The news/model interface, decision expiry, call cap, worker, fixed exits and
Telegram controls are now implemented in v0.4. Configure and live-validate those
components, then add claim-level corroboration, portfolio risk and the dashboard.
Confirm Bitget demo availability for these specific instruments before implementing
an authenticated demo broker. Bitget documents separate Demo keys and a
`paptrading: 1` header; that general guide does not establish Reality demo support.

## Official references checked

- [Public market API](https://www.bitget.com/docs/catalog/market/market-data):
  instruments and ticker schema, timestamps and instrument metadata.
- [Reality guide](https://www.bitget.com/docs/uta/reality-trading-guide):
  Reality identifiers, reused public endpoints and dedicated depth route.
- [Reality market data](https://www.bitget.com/docs/catalog/reality/market-data):
  authenticated depth access and public stock/session/calendar reference data.
- [Demo REST guide](https://www.bitget.com/docs/uta/demo-trading/rest-api):
  Demo API key and environment header requirements.

## Validation

17 automated tests cover accounting/restarts and the new public-data pipeline.
These use controlled fixtures. Live probe results are separately saved under
`examples/bitget-*`. The file `examples/bitget-live-smoke.json` records a local
paper lifecycle driven by synthetic test decisions and quotes fetched from Bitget.
It is an integration check, not an autonomous strategy or profitability backtest.
