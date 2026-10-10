Current update: v0.5.0 implements an optional collection-only reader. See
TELEGRAM-READER.md. The v0.4.2 notes below describe the earlier offline importer.

# Telegram event sources — v0.4.2

This release adds an offline post importer and the ten requested channel entries
in config/telegram-sources.json. It does not connect a Telegram reader, log in,
join channels or monitor them continuously. The existing Telegram control bot is
separate. No model credits are consumed by this import command.

## Review on 6 October 2026

- marketfeed: public preview shows current broad market and US stock headlines;
  enabled for unverified imports. Accuracy and delivery latency are unmeasured.
- news_finance: preview exists; current activity not established; disabled.
- earnings_pulse: visible posts predominantly cover Indian equities; disabled.
  Its bio links earnings_pulse_us, which remains unverified and is not enabled.
- earnings_season_america, spx_daily_america, mag7_concentration_us,
  fed_minutes_equity_us, nasdaq_open_brief_us, semi_equity_usa,
  futures_premarket_us: Telegram pages could not be independently verified.
  Third-party directory mentions do not establish existence or reliability;
  disabled pending review. Failed retrieval does not prove nonexistence.

Reviewed previews: https://t.me/s/marketfeed, https://t.me/s/news_finance,
https://t.me/s/earnings_pulse. All source content remains unverified.

## Import a post

Create posts.json containing one object or a list of objects in this envelope:

```json
{"channel":"marketfeed","message_id":123,"published_at":"2026-10-06T11:00:00Z","text":"Your actual post text"}
```

Use the actual message ID, original timezone-aware publication time and text.
For edits include edited_at; original publication time must not change.
The example is a format illustration, not a real Telegram post.

```sh
python -m wallstreet_quant news-telegram-import posts.json > events.json
```

Output contains events for explicit NVDA/Nvidia, TSLA/Tesla, AAPL/Apple,
MSFT/Microsoft and AMZN/Amazon mentions. Matching is lexical, not an assertion
of financial relevance. Other posts remain under unmatched for manual review;
no broad macro headline is automatically assigned to all stocks. Input-supplied
verified, symbol and URL fields cannot promote trust. Links are reconstructed
from handle and message ID, but local imports do not authenticate the source.
Stable IDs and edit revisions feed the existing pipeline deduplication.

Optional analysis via agent-file requires configured model credentials, costs
runtime API credits, and still cannot authorize a trade from these posts:

```sh
python -m wallstreet_quant --config config/bitget-paper.json agent-file events.json
```

Do not manually label rumours verified to unlock trading. Automated primary
corroboration is not implemented. The next integration step is an authenticated
Telegram reader and end-to-end delivery checks. A Bot API bot receives channel
posts only in channels where it is a member; it cannot read arbitrary channels
just by username. A user-client reader requires local Telegram API credentials
and account authorization. Never paste account codes or session files into chat.
Official references: https://core.telegram.org/bots/faq and
https://core.telegram.org/api/obtaining_api_id.
