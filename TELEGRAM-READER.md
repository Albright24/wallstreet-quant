# Telegram reader — v0.6.0

## Updating an existing installation

Stop any running collector with Ctrl+C. Extract the new archive into a separate
folder. Copy its inner `wallstreet_quant` code folder into your existing
`wallstreet-quant` project folder and choose Replace for existing files. Keep
your existing `.venv`, database and session. No new login or dependency installation
is required for this update.

From PowerShell in your existing project folder, run:

```powershell
.\.venv\Scripts\python.exe -m wallstreet_quant.news_review
```

This rechecks the existing database and saves `telegram-reviewed.json` directly
as UTF-8. Alternatively, review a previously exported file:

```powershell
.\.venv\Scripts\python.exe -m wallstreet_quant.news_review --input telegram-events.json
```

The review accepts Windows UTF-8 BOM exports. It never changes the collector
database. Each run recalculates freshness against current time, using original
publication rather than edit time (default 900 seconds). Historical collection
age is not a measurement of live delivery speed.

Product aliases include AWS, Kindle, Audible, Fire TV, iPhone, iPad, MacBook and
Azure. A match indicates a mention, not a material business impact. Promotion,
opinion/analysis, price-update and macro labels are conservative keyword heuristics;
they are incomplete and can be wrong. Unclassified company mentions still require
manual review and primary corroboration. `review_candidate` never permits trading;
every output is unverified and `trade_eligible` is false.

Story groups normalize case, punctuation and Telegram signatures within six hours.
They retain every post, preserve numeric and negation differences, and do not
establish independent confirmation. Paraphrases can remain in separate groups;
this is not semantic fact checking. Shared article URLs with changed text are
not automatically merged. This review is not automatically consumed by the worker.

Optional, collection-only Telethon reader for marketfeed, firstsquaw, forexnews,
financialTimesJournal and techcrunchcom. It reads public broadcast-channel history
using your locally authorized Telegram user account. It does not join channels,
send messages, call an AI model or submit orders. It runs separately from the
paper trading worker and its private Telegram control bot.

## Windows PowerShell setup

Extract the project and open PowerShell in wallstreet-quant. Python 3.10+ required.
Create a virtual environment, then install the optional pinned dependency:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-telegram.txt
```

Get your own API ID and API hash from https://my.telegram.org under API development
tools. Official instructions: https://core.telegram.org/api/obtaining_api_id.
These are user-client credentials, different from a BotFather token.
Set them only on your computer. This prompts for the hash without displaying it:

```powershell
$env:WQ_TELEGRAM_API_ID = Read-Host 'Telegram API ID'
$privateHash = Read-Host 'Telegram API hash' -AsSecureString
$env:WQ_TELEGRAM_API_HASH = [System.Net.NetworkCredential]::new('', $privateHash).Password
.\.venv\Scripts\python.exe -m wallstreet_quant.collector login
```

Enter your phone number, login code and any Telegram 2FA password at the local
prompts. No credentials should be shared in chat. The session is stored under
`$HOME\.wallstreet-quant\reader.session`, outside the project. The session grants
account access, so do not upload it, commit it, or share it. On a shared Windows
machine, restrict that directory to your Windows user. POSIX-created files use
restrictive permissions. Do not run another program using the same session.

## First trial

```powershell
.\.venv\Scripts\python.exe -m wallstreet_quant.collector run --cycles 3
.\.venv\Scripts\python.exe -m wallstreet_quant.collector status
.\.venv\Scripts\python.exe -m wallstreet_quant.collector export > telegram-events.json
```

For ongoing collection, omit --cycles. Stop with Ctrl+C. Linux also handles
SIGTERM. The collector saves data to telegram_news.sqlite3; do not point --db at
the paper trading database. Status/export are offline and need no credentials.
If a run stops, restart the same command with the same database/session paths.
Configuration comes from config/telegram-sources.json; collect=true and
enabled=true are both required. Other source entries remain disabled.

## What it measures and preserves

- Polls channels sequentially, with a 15-second pause between complete cycles.
  Network calls and Telegram rate limits add delay. This is not a subsecond push feed.
- First run fetches at most 50 recent messages per channel. Thereafter it reads
  at most 100 newer messages per cycle, oldest first, retaining the backlog for
  later cycles. Recent history includes old news and is not a new trading signal.
- Rechecks the latest 50 already-covered messages per channel for edits. It does
  not discover edits outside that window, deletions, or media-only news content.
- Saves channel ID, message ID, text revisions, original publication time, edit
  time, first observation of each revision, article links and lexical stock matches.
- Pins channel IDs. A reassigned username fails closed pending operator review.
- Keeps repeated deliveries once per message/revision. Preserves each publisher's
  record for audit. Export collapses exact same-text stories sharing normalized
  URLs and symbol; differing text remains available as a potential update.
- Removes common tracking parameters, but does not resolve shortened URLs or
  fetch articles. URL matches are duplication hints, not independent corroboration.
- Delay statistics are observed time minus publication/edit time. They include
  backlog, polling, network and local clock effects; they do not measure the
  publisher's delay relative to the real-world event. Synchronize the host clock.
- Reports per-channel health. Request failures retry next cycle. Telegram flood
  waits pause the whole collector for the specified duration. Invalid posts
  block that channel's progress for review rather than silently skipping them.

All events remain verified=false. Unmatched macro/other posts remain in the
unmatched list. Mapping is limited to NVDA, TSLA, AAPL, MSFT and AMZN. No model
or trading pipeline consumes this database automatically. No paid API credits are
required by this collector; running it uses your internet connection and host.
The next gate is a real collection trial after local login: verify each source's
health, inspect saved posts/links, and measure delays before AI integration.

## Validation boundary

The complete offline suite passes, including mocked channel history, restart
cursors, duplicate deliveries, edits, ID changes and backlog recovery. Telethon
1.45.0 was installed and its API signatures checked locally. Live Telegram login,
channel access, rate limits and delivery speed have not been tested with your
account. Windows setup is documented but has not been executed on Windows.

Reference: https://docs.telethon.dev/en/stable/concepts/sessions.html
