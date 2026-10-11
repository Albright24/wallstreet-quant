"""Read-only Telegram triage into the existing bounded paper pipeline.

Telegram claims never become verified here. Decisions and block reasons share
news_runs with SEC events, so the existing dashboard displays them.
"""
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sqlite3
from .core import Engine
from .advisor import OpenAIAdvisor
from .bitget import BitgetPublic
from .news_review import review
from .pipeline import Pipeline


def candidates(db_path, max_age=900, now=None):
    now = now or datetime.now(timezone.utc)
    path = Path(db_path).resolve()
    if not path.is_file():
        raise ValueError('Telegram collector database missing')
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=10)
    try:
        rows = db.execute('''SELECT p.body FROM posts p
            WHERE julianday(p.published) >= julianday(?)
              AND p.rowid=(SELECT q.rowid FROM posts q
                WHERE q.peer=p.peer AND q.message=p.message
                ORDER BY q.updated DESC,q.rowid DESC LIMIT 1)
            ORDER BY p.rowid DESC LIMIT 500''',
            ((now - timedelta(seconds=max_age)).isoformat(),)).fetchall()
    finally:
        db.close()
    payload = {'events': [], 'unmatched': []}
    for (body,) in rows:
        post = json.loads(body)
        for name in payload:
            payload[name].extend(post[name])
    result = review(payload, now=now, max_age=max_age)
    eligible = [dict(e, verified=False, trade_eligible=False)
                for e in result['events'] if e['review_candidate']]
    eligible.sort(key=lambda e: e['published_at'], reverse=True)
    return eligible, result['summary']


def cycle(path, risk, collector_db, daily_calls, stop, advisor=None, market=None):
    engine = Engine(path, risk)
    try:
        events, summary = candidates(collector_db, risk.event_age_seconds)
        pipeline = Pipeline(engine, advisor or OpenAIAdvisor(), market or BitgetPublic(), daily_calls)
        duplicates = 0
        for event in events:
            if stop.is_set():
                break
            result = pipeline.process(event)
            if result['status'] == 'duplicate':
                duplicates += 1
                continue
            # At most one new pipeline attempt per cycle. Daily reservations are
            # atomic and shared with the SEC worker in the same paper database.
            return {'status': result['status'], 'run_key': result['run_key'],
                    'reason': result.get('reason'), 'review_summary': summary,
                    'duplicates': duplicates, 'source': 'telegram',
                    'primary_corroboration': False}
        return {'status': 'no_fresh_candidate' if not events else 'no_new_candidate',
                'review_summary': summary, 'duplicates': duplicates,
                'source': 'telegram', 'primary_corroboration': False}
    finally:
        engine.close()
