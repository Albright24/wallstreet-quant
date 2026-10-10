import argparse
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
from .core import Engine, Risk
from .bitget import BitgetPublic, stock_candidate, process_live_paper, MarketDataError


def demo(engine):
    """Synthetic decisions and quotes; never represented as live performance."""
    now = datetime.now(timezone.utc)
    run = now.isoformat()
    event = {"source": "synthetic_fixture", "id": run + "-entry", "revision": "1",
             "symbol": "DEMO_EQUITY", "published_at": now.isoformat(),
             "headline": "SYNTHETIC: example company raises guidance",
             "url": "https://example.com/synthetic-event", "verified": True,
             "synthetic": True}
    quote = {"symbol": "DEMO_EQUITY", "observed_at": now.isoformat(),
             "bid": "99.90", "ask": "100", "bid_size": "1000", "ask_size": "1000"}
    decision = {"action": "enter", "confidence": "0.85", "notional": "500",
                "rationale": "Synthetic fixture decision; no AI model was called.",
                "advisor": "fixture"}
    results = [engine.process(event, decision, quote, now), engine.process(event, decision, quote, now)]
    stale = dict(event, id=run + "-stale", published_at=(now - timedelta(hours=1)).isoformat())
    # Close first to show a full lifecycle and allow a stale entry check.
    for action, bid, extra in [("hold", "101", {}), ("reduce", "102", {"fraction": "0.5"}), ("exit", "103", {})]:
        e = dict(event, id=run + "-" + action)
        d = dict(decision, action=action, **extra)
        q = dict(quote, bid=bid, ask=str(float(bid) + .10))
        results.append(engine.process(e, d, q, now))
    results.append(engine.process(stale, decision, quote, now))
    return {"label": "SYNTHETIC DEMO — NOT LIVE OR BACKTEST PERFORMANCE", "results": results, "account": engine.status()}


def main():
    parser = argparse.ArgumentParser(description="Wallstreet Quant paper-only backend")
    parser.add_argument("--db", default="wallstreet_quant.sqlite3")
    parser.add_argument("--config", help="JSON Risk settings; use the same config for each database command")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo")
    commands.add_parser("agent-demo", help="Offline news/decision/quote pipeline using labelled fixtures")
    commands.add_parser("status")
    commands.add_parser("pause")
    commands.add_parser("resume")
    instruments = commands.add_parser("instruments", help="Discover current Bitget SPOT instruments")
    instruments.add_argument("--stocks", action="store_true")
    quote = commands.add_parser("quote", help="Fetch current public Bitget ticker and validate it")
    quote.add_argument("symbol")
    commands.add_parser("market-reference", help="Fetch stock mapping, session and holiday reference data")
    live = commands.add_parser("paper-live", help="Use a fresh Bitget quote with a supplied decision; local paper only")
    live.add_argument("file", help="JSON object containing event and decision")
    news_sec = commands.add_parser("news-sec", help="Fetch primary SEC filing events (requires contact User-Agent)")
    news_sec.add_argument("ticker")
    news_alpaca = commands.add_parser("news-alpaca", help="Fetch one authenticated news page")
    news_alpaca.add_argument("--start", required=True)
    news_alpaca.add_argument("--page-token")
    agent_sec = commands.add_parser("agent-sec", help="Fetch SEC events, ask the model, then attempt local paper execution")
    agent_sec.add_argument("ticker")
    agent_sec.add_argument("--daily-calls", type=int, default=20)
    agent_file = commands.add_parser("agent-file", help="Analyse normalized news JSON; imported events are unverified")
    agent_file.add_argument("file")
    agent_file.add_argument("--daily-calls", type=int, default=20)
    channel_import = commands.add_parser("news-telegram-import", help="Normalize offline Telegram post envelopes; no network or model calls")
    channel_import.add_argument("file")
    channel_import.add_argument("--sources", default=str(Path(__file__).resolve().parent.parent / "config" / "telegram-sources.json"))
    commands.add_parser("news-audit", help="Print news/model workflow records")
    worker = commands.add_parser("worker", help="Continuous local paper monitoring; optional news/Telegram")
    worker.add_argument("--cycles", type=int, default=0, help="0 runs until Ctrl+C; positive number bounds iterations")
    worker.add_argument("--interval", type=float, default=5)
    worker.add_argument("--news-interval", type=float, default=60)
    worker.add_argument("--enable-news", action="store_true")
    worker.add_argument("--enable-telegram", action="store_true")
    worker.add_argument("--daily-calls", type=int, default=20)
    worker.add_argument("--exit-policy", default=None)
    monitor = commands.add_parser("monitor-once", help="Check open positions once against fixed exit rules")
    monitor.add_argument("--exit-policy", default=None)
    commands.add_parser("worker-status", help="Local runtime health and notification delivery state")
    process = commands.add_parser("process")
    process.add_argument("file", help="JSON object containing event, decision and refreshed quote")
    export = commands.add_parser("export")
    export.add_argument("file", help="Destination JSONL audit log")
    args = parser.parse_args()
    if args.command == "news-telegram-import":
        from .channel_news import import_posts
        from .news import NewsError
        try:
            path = Path(args.file)
            if path.stat().st_size > 5_000_000:
                raise NewsError("Import exceeds 5 MB")
            result = import_posts(json.loads(path.read_text()), json.loads(Path(args.sources).read_text()))
            print(json.dumps(result, indent=2))
        except (OSError, ValueError, TypeError) as exc:
            parser.exit(2, str(exc) + "\n")
        return
    if args.command in ("news-sec", "news-alpaca"):
        from .news import SECNews, AlpacaNews, NewsError
        try:
            result = SECNews().fetch(args.ticker) if args.command == "news-sec" else AlpacaNews().fetch(args.start, args.page_token)
            print(json.dumps(result, indent=2))
        except (NewsError, KeyError, TypeError) as exc:
            parser.exit(2, str(exc) + "\n")
        return
    if args.command in ("instruments", "quote", "market-reference"):
        client = BitgetPublic()
        try:
            if args.command == "instruments":
                result = client.instruments()
                if args.stocks:
                    result = [x for x in result if stock_candidate(x)]
            elif args.command == "quote":
                result = client.snapshot(args.symbol)
            else:
                from .bitget import STOCK_INFO, STATES, CALENDAR
                result = {"received_at": datetime.now(timezone.utc).isoformat(),
                          "stock_info": client.get(STOCK_INFO), "sessions": client.get(STATES),
                          "calendar": client.get(CALENDAR)}
            print(json.dumps(result, indent=2))
        except MarketDataError as exc:
            parser.exit(2, str(exc) + "\n")
        return
    risk = Risk(**json.loads(Path(args.config).read_text())) if args.config else Risk()
    if args.command in ("paper-live", "agent-sec", "agent-file", "worker", "monitor-once") and not args.config:
        parser.error("This command requires --config with an explicit instrument allowlist")
    engine = Engine(args.db, risk)
    try:
        if args.command == "demo":
            result = demo(engine)
        elif args.command == "agent-demo":
            from .agent_demo import run
            result = run(engine)
        elif args.command in ("worker", "monitor-once", "worker-status"):
            from .monitor import Monitor, ExitPolicy, RuntimeStore
            from .worker import run_worker, WorkerLock
            store = RuntimeStore(engine)
            if args.command == "worker-status":
                result = {"runtime": {k: json.loads(v) for k,v in engine.db.execute("SELECT key,body FROM runtime")},
                          "notifications": dict(engine.db.execute("SELECT status,COUNT(*) FROM notices GROUP BY status").fetchall())}
            else:
                policy = ExitPolicy(**json.loads(Path(args.exit_policy).read_text())) if args.exit_policy else ExitPolicy()
                if args.command == "worker":
                    result = run_worker(engine, policy, args.cycles, args.interval, args.news_interval,
                                        args.enable_news, args.enable_telegram, args.daily_calls)
                else:
                    path = engine.db.execute("PRAGMA database_list").fetchone()[2]
                    with WorkerLock(path):
                        result = Monitor(engine, BitgetPublic(), policy).tick()
        elif args.command == "process":
            data = json.loads(Path(args.file).read_text(), parse_constant=lambda x: (_ for _ in ()).throw(ValueError("Non-finite JSON")))
            result = engine.process(data["event"], data["decision"], data["quote"])
        elif args.command == "paper-live":
            data = json.loads(Path(args.file).read_text())
            result = process_live_paper(engine, BitgetPublic(), data["event"], data["decision"])
        elif args.command in ("agent-sec", "agent-file", "news-audit"):
            from .news import SECNews
            from .advisor import OpenAIAdvisor
            from .pipeline import Pipeline
            pipeline = Pipeline(engine, OpenAIAdvisor(), BitgetPublic(), getattr(args, "daily_calls", 20))
            if args.command == "news-audit":
                result = pipeline.records()
            else:
                if args.command == "agent-sec":
                    events = SECNews().fetch(args.ticker)
                else:
                    events = json.loads(Path(args.file).read_text())
                    if isinstance(events, dict):
                        events = events.get("events", [events])
                    # A file can be edited: it never establishes remote source provenance.
                    events = [dict(e, verified=False, verification="local_import_unverified") for e in events]
                result = [pipeline.process(event) for event in events]
        elif args.command in ("pause", "resume"):
            engine.pause(args.command == "pause")
            result = engine.status()
        elif args.command == "export":
            records = engine.records()
            Path(args.file).write_text("".join(json.dumps(row) + "\n" for row in records))
            result = {"exported_records": len(records), "file": args.file}
        else:
            result = engine.status()
        print(json.dumps(result, indent=2))
    finally:
        engine.close()


if __name__ == "__main__":
    main()
