"""Offline news triage. Labels are heuristics, never verification or trade approval."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from .channel_news import match_companies
from .core import timestamp
from .news import SYMBOLS


def story_text(body):
    text = unicodedata.normalize('NFKC', body).casefold()
    text = re.sub(r'https?://\S+|\(@\w+\)', ' ', text)
    text = re.sub(r'\b(?:source|read more)\s*:?\s*$', '', text.strip())
    text = text.replace('’', "'").replace('‘', "'")
    # Keep decimals, signs and percent values; never merge changed numbers.
    return ' '.join(re.findall(r'\w+|[+\-]?\d+(?:[.,]\d+)*%?|[%+\-]', text))


def content_flags(body):
    text = re.sub(r'https?://\S+', '', body)
    rules = {
        'promotion': r'\b(?:promo code|sponsored|register now|grab your|exhibit table|prop firm|which broker|starts tomorrow|are you trading|do you really know)\b',
        'opinion_or_analysis': r'\b(?:opinion|forecast|price target|target price|retracement|elliott wave|analysts?|warns?|outlook|why|definition of|seen at)\b',
        'price_update': r'\b(?:shares? (?:climb|rise|fall|gain|extend|erase)|all.time high|record high|new high|futures extend|after market open)\b',
        'macro': r'\b(?:fed|federal reserve|treasury|inflation|cpi|payrolls|employment|gdpnow|interest rates?|ecb|boj)\b',
    }
    return [label for label, pattern in rules.items() if re.search(pattern, text, re.I)]


def review(payload, now=None, max_age=900):
    now = now or datetime.now(timezone.utc)
    if not 1 <= max_age <= 86400:
        raise ValueError('max_age must be 1–86400 seconds')
    if not isinstance(payload, dict) or any(not isinstance(payload.get(k), list) for k in ('events', 'unmatched')):
        raise ValueError('Expected a collector export with events and unmatched lists')
    rows = payload['events'] + payload['unmatched']
    if len(rows) > 50000:
        raise ValueError('Review limit is 50000 event rows')
    # One post can already have multiple ticker rows. Retain one latest revision.
    posts = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('body'), str) or not row['body'].strip():
            raise ValueError('Invalid news row')
        published, updated, retrieved = (timestamp(row[k]) for k in ('published_at', 'updated_at', 'retrieved_at'))
        if updated < published:
            raise ValueError('Edit precedes publication')
        key = (row['source'], row['url'])
        if key not in posts or updated > timestamp(posts[key]['updated_at']):
            posts[key] = row
    events, unmatched, groups = [], [], []
    buckets = {}
    statuses, categories = Counter(), Counter()
    for row in sorted(posts.values(), key=lambda r: (timestamp(r['published_at']), r['source'], r['url'])):
        published = timestamp(row['published_at'])
        age = (now - published).total_seconds()
        initial_age = (timestamp(row['retrieved_at']) - published).total_seconds()
        freshness = 'future_timestamp' if age < 0 or initial_age < 0 else 'fresh' if age <= max_age else 'stale'
        flags = content_flags(row['body'])
        matches = match_companies(row['body'])
        category = next((f for f in ('promotion','opinion_or_analysis','price_update','macro') if f in flags),
                        'company_mention' if matches else 'other')
        statuses[freshness] += 1
        categories[category] += 1
        normalized = story_text(row['body'])
        # Equivalent wording only, within six hours. Similarity never proves truth.
        group = buckets.get(normalized)
        if not group or (published - timestamp(group['first_published_at'])).total_seconds() > 21600:
            gid = hashlib.sha256((normalized + row['published_at'] + row['url']).encode()).hexdigest()[:20]
            group = dict(id=gid, first_published_at=row['published_at'], posts=[], publishers=[],
                         independent_confirmation=False)
            buckets[normalized] = group
            groups.append(group)
        group['posts'].append(row['url'])
        group['publishers'] = sorted(set(group['publishers'] + [row['publisher']]))
        base = {k: v for k, v in row.items() if k not in ('ticker','symbol','matched_terms','match_method')}
        base.update(verified=False, claim_status='unverified', freshness=freshness,
                    age_seconds=round(age, 3), age_at_collection_seconds=round(initial_age, 3),
                    category=category, content_flags=flags, story_group=group['id'],
                    trade_eligible=False,
                    review_candidate=freshness == 'fresh' and category == 'company_mention',
                    verification='offline_triage; primary corroboration required')
        post_id = row['id']
        if row.get('ticker') and post_id.endswith(':' + row['ticker']):
            post_id = post_id.rsplit(':', 1)[0]
        if matches:
            for ticker, terms in matches.items():
                events.append(dict(base, id=f'{post_id}:{ticker}', ticker=ticker, symbol=SYMBOLS[ticker],
                                   matched_terms=terms, match_method='company_or_product_mention'))
        else:
            unmatched.append(dict(base, id=post_id, review_candidate=False))
    return dict(mode='offline_review_only', reviewed_at=now.isoformat(), max_age_seconds=max_age,
                summary=dict(posts=len(posts), stock_event_rows=len(events), unmatched_posts=len(unmatched),
                             freshness=dict(statuses), categories=dict(categories),
                             repeated_story_groups=sum(len(g['posts']) > 1 for g in groups),
                             review_candidates=sum(e['review_candidate'] for e in events)),
                events=events, unmatched=unmatched, story_groups=groups,
                notes=['Heuristic labels require review; a mention does not prove material impact.',
                       'Age uses original publication time; an edit never refreshes an old story.',
                       'Groups retain every post and do not count as independent confirmation.',
                       'Collection age includes historical catch-up; it is not wire latency.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', help='Existing collector export JSON (optional)')
    parser.add_argument('--db', default='telegram_news.sqlite3')
    parser.add_argument('--output', default='telegram-reviewed.json')
    parser.add_argument('--max-age', type=int, default=900)
    args = parser.parse_args()
    try:
        output = Path(args.output).resolve()
        protected = [Path(args.db).resolve()] + ([Path(args.input).resolve()] if args.input else [])
        if output in protected:
            raise ValueError('Output must differ from the database and input')
        if args.input:
            path = Path(args.input)
            if path.stat().st_size > 50_000_000:
                raise ValueError('Input exceeds 50 MB')
            payload = json.loads(path.read_text(encoding='utf-8-sig'))
        else:
            from .collector import Store
            if not Path(args.db).is_file():
                raise ValueError('Collector database not found')
            store = Store(args.db)
            try:
                payload = store.export()
            finally:
                store.db.close()
        result = review(payload, max_age=args.max_age)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result['summary'], indent=2))
        print(f'Saved review: {output}')
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, f'Review failed: {exc}\n')


if __name__ == '__main__':
    main()
