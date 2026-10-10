# News and AI phase — v0.3

## Status

Implemented and tested with fixtures: SEC filing ingestion, optional Alpaca REST
news, OpenAI structured proposals, duplicate suppression, a persistent workflow
audit, and fresh Bitget quotes before local paper fills. Previous v0.2 Bitget
public-data live tests are retained in the archive.

No paid model request or authenticated news call has been made during development.
SEC requests require a real contact setting which has not been supplied. Therefore
the new source/model adapters are implemented but have NOT been validated against
live accounts. There is no claim of real news-driven trading performance.

## Try without keys

In PowerShell, from the extracted project folder:

```powershell
python -m wallstreet_quant --db agent-demo.sqlite3 agent-demo
python -m wallstreet_quant --db agent-demo.sqlite3 news-audit
python -m unittest discover -s tests -v
```

Use a fresh demo database for the expected enter → duplicate → secondary-source
block → exit sequence. The synthetic `verified` flag belongs only to this fixture.
All events, decisions and prices in this demo are invented. No network calls occur.

## Live configuration

Set the following environment variables locally, outside your repository:

| Variable | Needed for |
|---|---|
| `SEC_USER_AGENT` | Application name plus your real contact email, per SEC access guidance |
| `OPENAI_API_KEY` | Runtime OpenAI API calls |
| `WQ_MODEL` | An exact model ID available to your API project that supports Responses structured output |
| `APCA_API_KEY_ID` | Optional Alpaca news access |
| `APCA_API_SECRET_KEY` | Optional Alpaca news access |

No model is silently selected. Model availability, cost and output-format support
must be checked for your API account. Codex usage credits are not treated as the
runtime key or runtime budget. No credentials are requested in chat. `.env` files
are ignored by git, but this standard-library app does not auto-load `.env`.
Use your shell's environment or a service manager's secret configuration.

Once configured:

```powershell
python -m wallstreet_quant news-sec NVDA
python -m wallstreet_quant --config config/bitget-paper.json --db agent-paper.sqlite3 agent-sec NVDA --daily-calls 20
python -m wallstreet_quant --config config/bitget-paper.json --db agent-paper.sqlite3 news-audit
```

`agent-sec` is one bounded run over up to three recent filings. It resolves the
company CIK from SEC data, reads the primary document, checks publication age,
asks the model, validates its proposal, and fetches a new Bitget quote before a
trade attempt. An old filing produces a blocked record without a model call;
do not change filing timestamps to make them appear fresh.

Optional secondary news, substituting an actual recent UTC start time:

```powershell
python -m wallstreet_quant news-alpaca --start 2026-10-06T11:00:00Z > news.json
python -m wallstreet_quant --config config/bitget-paper.json --db agent-paper.sqlite3 agent-file news.json
```

The example timestamp will become stale. The news command returns one page of up
to 50 articles and a `next_page_token`; use `--page-token` to continue explicitly.
This is REST ingestion, not a WebSocket stream or an always-running service.
Alpaca account entitlement can delay data, so real-time access must be verified.
Imported files are always marked unverified, even if someone edited a trust flag.

## What “verified” means here

A successful SEC fetch establishes the source of the primary filing and its
identity. It does not independently establish the truth of its claims or imply
that its release creates a profitable trade. Only the first 12,000 characters of
the primary document are analysed. Earnings exhibits and attachments are not
fetched yet. The prompt requires abstention when the available text is insufficient.

Alpaca delivery alone does not corroborate news. Secondary and imported events
may receive an AI assessment, but cannot authorize enter/reduce/exit actions in
this pipeline. It does not automatically match a headline to a SEC filing and
declare the claims verified based on shared ticker or timing.

## Controls and limitations

- Article text stays in the untrusted data portion of the prompt. The model has
  no tools, API credentials or ability to change fixed limits. This reduces the
  attack surface; it is not a guarantee against misleading interpretations.
- Structured decisions require known evidence IDs, finite numbers, supported
  actions and rationale/uncertainty. Citations are checked for identity, not
  semantic entailment. Confidence is subjective, not a profit probability.
- Refusals, incomplete responses and invalid proposals produce no trade.
  A trade decision expires 60 seconds after the model request starts; this includes
  quote-fetch time. API slowness can cause a safe rejection.
- News revisions and exact normalized headline/body duplicates per instrument
  and publication date are persisted. Paraphrased duplicate news is not detected.
  Corrections with changed contents can be reassessed as new revisions.
- Default call reservation cap is 20 per UTC day per database, with 1,200 maximum
  output tokens per request and a 48 KB model-input cap. Failed/uncertain requests
  consume a reservation; paid requests are never automatically retried. These
  limits are NOT a dollar-denominated spending guarantee. Provider billing limits
  must also be configured. Separate databases have separate caps.
- A crash leaves a persistent `started` or `decided` record for inspection and
  prevents automatic retry. Operator recovery is not implemented. The execution
  ledger remains transactional; claims are not silently reclaimed after crashes.
- The model currently sees news and account state, not a pre-decision market
  snapshot or calibrated signal model. Fixed code checks fresh prices afterward.
- The commands shown above are single-run operations. Version 0.4 adds continuous
  polling, fixed exits and Telegram through `worker`; see `WORKER-SETUP.md`.
  Claim-level corroboration, portfolio-wide mark-to-market risk, dashboard and
  exchange demo execution still remain. Existing position/account limitations
  in the README apply. Keep synthetic and real-observation databases separate.

## References checked October 6, 2026

- [OpenAI structured output](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)
- [SEC submissions API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- [SEC access guidance](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)
- [Alpaca news REST API](https://docs.alpaca.markets/us/reference/news-3)

Validation: 31 automated tests pass. See `examples/synthetic-agent-pipeline.json`
for a captured offline orchestration run, explicitly labelled synthetic.
