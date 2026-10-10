# Validation history

## v0.6.1 — October 10, 2026

86 local fixture tests passed. Added checks for separate provider credentials,
unknown-provider rejection, AgentRouter endpoint selection, and HTTP error
redaction without automatic retries. Existing schema/risk tests still pass.
The user also ran the synthetic agent demo on Windows: buy, duplicate block,
uncorroborated-news block, and sell; no remaining positions. No live AI request
has been verified. `ai_check` is the next local, one-request compatibility check.

## Resilience validation — v0.4.1

Date: October 6, 2026. Environment: Linux, Python 3.12.

Result: **54 tests passed**, including 9 new resilience tests. The suite ran in
approximately 1.6 seconds locally. Tests are deterministic fixtures except the
separately captured Bitget public quote check. Test duration is not a trading
latency measurement.

## Issues reproduced and fixed

| Finding in v0.4 | Fix in v0.4.1 |
|---|---|
| Partial position reduction during an AI call did not invalidate the proposal | Compare both position identity and quantity under the execution transaction |
| A malformed Telegram message date raised an exception before advancing the offset | Ignore malformed message contents while safely advancing a valid update ID |
| Forwarded text from the owner could trigger a command | Reject forwarded and via-bot messages as controls |
| Background-task failure during the last worker cycle could be omitted from status | Collect and record remaining task results/errors after shutdown joins |

All four cases were reproduced by failing tests before the changes; those tests
now pass. No production trading occurred during reproduction.

## Additional checks

- Eight simultaneous attempts to process one event: exactly one fill, seven
  duplicate results, one ledger record.
- Four concurrent news events with a one-call daily cap: exactly one model
  invocation, three budget blocks. The model was a test fixture.
- 100 seeded paper round trips, each with two partial reductions: checked
  nonnegative cash, closed positions, and agreement between cash movement and
  accumulated realized P&L. This is accounting validation, not backtesting.
- A real child process was killed with an uncommitted SQLite balance change:
  the change rolled back, the OS worker lock released, and SQLite reported
  database integrity `ok`.
- A real worker child process received SIGTERM: clean exit, paused entries,
  persisted stopped state. No news, Telegram or live orders were enabled.
- Existing tests cover stale/future quotes, min-size/precision, price/depth
  rejection, stop/take-profit/time exits, restart deduplication, refusals and
  malformed model responses, authorization and uncertain notification delivery.
- A fresh public NVIDIA ticker check is captured in
  `examples/bitget-revalidation.json`, separately from synthetic tests.

## Reproduce

```sh
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s tests -p test_resilience.py -v
python3 -m wallstreet_quant quote RNVDAUSDT
```

Use `python` on Windows. Two process-signal tests are POSIX-only and explicitly
skip there; the Windows file-lock branch still requires a native Windows check.

## Limits of this result

This is not a production-security audit, prolonged network soak test or proof of
profitability. Paid OpenAI, authenticated news and real Telegram delivery remain
unvalidated with user credentials. No exchange trading endpoints were called.
Worker position checks remain sequential and can be delayed by slow quote
requests. Real quote/depth failures can prevent a simulated stop from filling.
Notification delivery remains one attempt with uncertainty recorded, not guaranteed.
VPS deployment, dashboard and hackathon submission are not completed by these tests.

## v0.4.2 Telegram import validation

60 tests passed on Python 3.12/Linux, including six new tests for trust overrides,
channel allowlisting, edit identity and original age, unmatched macro posts,
malformed/future timestamps and bounded batches. An offline CLI import smoke
also passed. These are fixture tests; no live Telegram reader, account login,
latency measurement or real news accuracy benchmark was performed.

## v0.5.0 collector validation

72 offline tests pass. Twelve collector tests cover bounded initial history,
ascending backlog recovery, edit retention, out-of-order revisions, channel ID
reassignment, replay deduplication, hidden links, URL normalization, delay values,
invalid records, media-only cursor progress, disconnect recovery and on-disk
restart persistence. Telethon 1.45.0 installed;
no Telegram login, network collection or model calls were performed.

## v0.5.1 active username aliases

User screenshots confirm a Windows live trial collected 202 post revisions across
four channels; forexnews failed validation. A local diagnostic showed no primary
username but active fxstreetforex and forexnews aliases. Collector now accepts
active Telegram aliases and preserves broadcast-channel and pinned-ID checks.
75 offline tests pass, including active aliases, inactive aliases, nonbroadcast
entities and changed channel IDs. Subsequent user terminal output confirms all
five channels healthy with 267 saved revisions, including 50 forexnews observations.

## v0.6.0 offline news review

83 tests pass. Eight new tests cover product aliases, URL-only false matches,
old posts with new edits, freshness boundaries, future timestamps, legacy exports,
multi-ticker posts, cross-channel grouping, numeric and negation differences,
recurring headlines, content labels and Windows BOM CLI input/output protection.

Replayed the user's October 6 export (259 posts) at 16:00 UTC: 12 stock event rows
(previously 8), 248 unmatched posts, 11 repeated-story groups, 7 promotion-labelled
posts and no fresh review candidates. AWS adds an Amazon row to the Nvidia story;
iPhone, Audible and Kindle add three previously unmatched posts. All 259 posts
were older than 15 minutes at that review time. No posts are discarded by grouping.
These results assess processing, not factual accuracy or financial performance.
No live Telegram connection, model calls or trades were made during this update.

## v0.6.2 Groq adapter
90 local tests passed. Added mocked endpoint/schema, key isolation, incomplete/refusal/tool rejection, evidence validation and rate-limit no-retry tests. Live Groq authentication and model availability still require the user-run ai_check. No trades placed.

## v0.6.3 Windows Groq transport
94 local tests passed, including mocked Windows routing, secret/news argument isolation, child environment filtering, timeout/error redaction and no retries. User verified a live temporary PowerShell transport with Qwen: passed, skip, 445 tokens, 0 trades. Packaged Windows transport requires user connection check; no Windows runtime here.

## v0.6.4 evidence instructions
User v0.6.3 live Windows call reached model but was rejected by evidence validation. Clarified exact evidence ID source and explicit empty-evidence abstention instructions. Evidence validator unchanged; 94 local tests passed. Prompt improvement is not a guarantee of model correctness; a fresh user-run connection check is required.

## Final user-run checks (October 10, 2026)
Version 0.6.4 Windows Groq/Qwen check passed (553 tokens, skip, no trades). Real Qwen synthetic-rumor pipeline check passed (772 tokens, no trade, balance unchanged); duplicate was blocked. A separate in-memory stale-event check blocked before AI/market calls. Telegram review showed 4,397 posts, all stale, 152 stock-event rows and zero fresh candidates: live fresh Telegram-to-AI analysis remains unverified. These are builder-reported outputs, not a two-week performance history.
