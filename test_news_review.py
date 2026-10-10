import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from wallstreet_quant.channel_news import normalize_post, match_companies
from wallstreet_quant.news_review import review

NOW = datetime(2026, 10, 6, 16, tzinfo=timezone.utc)
REGISTRY = {'channels': {'marketfeed': {'enabled': True}, 'firstsquaw': {'enabled': True}}}


def payload(text='Nvidia announces new chip', **changes):
    post = dict(channel='marketfeed', message_id=1, text=text,
                published_at='2026-10-06T15:55:00Z')
    post.update(changes)
    return normalize_post(post, REGISTRY, NOW)


class ReviewTests(unittest.TestCase):
    def test_product_matches_exclude_urls(self):
        self.assertEqual(set(match_companies('AWS and Kindle; iPhone and Azure')), {'AMZN','AAPL','MSFT'})
        self.assertEqual(match_companies('News https://example.com/nvidia (@Amazon)'), {})
        self.assertEqual(match_companies('lawson kindled a fire'), {})

    def test_old_edit_not_refreshed_and_never_trusted(self):
        p = payload(published_at='2026-10-05T15:55:00Z', edited_at='2026-10-06T15:59:00Z')
        p['events'][0]['verified'] = True
        e = review(p, NOW)['events'][0]
        self.assertEqual(e['freshness'], 'stale')
        self.assertFalse(e['verified'])
        self.assertFalse(e['trade_eligible'])
        self.assertFalse(e['review_candidate'])

    def test_fresh_boundary_and_future(self):
        self.assertEqual(review(payload(published_at='2026-10-06T15:45:00Z'), NOW)['events'][0]['freshness'], 'fresh')
        self.assertEqual(review(payload(published_at='2026-10-06T15:44:59Z'), NOW)['events'][0]['freshness'], 'stale')
        p = payload()
        p['events'][0]['published_at'] = p['events'][0]['updated_at'] = '2026-10-07T00:00:00Z'
        self.assertEqual(review(p, NOW)['events'][0]['freshness'], 'future_timestamp')

    def test_reclassifies_old_unmatched_and_multiticker_once(self):
        p = payload('AWS and Nvidia announce accelerator')
        # Simulate old normalizer, which only matched Nvidia.
        p['events'] = [e for e in p['events'] if e['ticker'] == 'NVDA']
        r = review(p, NOW)
        self.assertEqual({e['ticker'] for e in r['events']}, {'AMZN','NVDA'})
        self.assertEqual(review(r, NOW)['summary']['posts'], 1)
        p = payload('Kindle introduces a device')
        e = p['events'][0]
        del e['ticker']; del e['symbol']
        e['id'] = '1'
        r = review(dict(events=[], unmatched=[e]), NOW)
        self.assertEqual(r['events'][0]['ticker'], 'AMZN')

    def test_group_equivalent_wording_keep_all_evidence(self):
        a = payload("FED’S BOWMAN: AIMING EARLY NEXT YEAR ...")
        b = payload("Fed's Bowman: aiming early next year\n(@FirstSquaw)", channel='firstsquaw')
        r = review(dict(events=[], unmatched=a['unmatched'] + b['unmatched']), NOW)
        self.assertEqual(r['summary']['repeated_story_groups'], 1)
        self.assertEqual(len(r['unmatched']), 2)
        self.assertFalse(r['story_groups'][0]['independent_confirmation'])

    def test_changed_values_negation_and_old_recurrence_separate(self):
        rows = []
        for i, text in enumerate(['Nvidia rises 1.3%', 'Nvidia rises 1.4%', 'Nvidia does not rise 1.3%']):
            rows += payload(text, message_id=i+1)['events']
        rows += payload('Nvidia rises 1.3%', message_id=4, published_at='2026-10-05T00:00:00Z')['events']
        r = review(dict(events=rows, unmatched=[]), NOW)
        self.assertEqual(len(r['story_groups']), 4)

    def test_promos_analysis_price_and_macro(self):
        for text, category in [('Nvidia prop firm promotion','promotion'),
                               ('Nvidia price forecast','opinion_or_analysis'),
                               ('NVIDIA SHARES CLIMB 1.3% TO ALL-TIME HIGH','price_update'),
                               ('Fed announces rates','macro')]:
            r = review(payload(text), NOW)
            row = (r['events'] + r['unmatched'])[0]
            self.assertEqual(row['category'], category)
            self.assertFalse(row['review_candidate'])

    def test_windows_bom_cli_and_no_input_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp)/'input.json', Path(tmp)/'review.json'
            source.write_text(json.dumps(payload()), encoding='utf-8-sig')
            cmd = [sys.executable, '-m', 'wallstreet_quant.news_review', '--input', str(source), '--output']
            result = subprocess.run(cmd+[str(target)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(target.read_text())['mode'], 'offline_review_only')
            self.assertNotEqual(subprocess.run(cmd+[str(source)], capture_output=True).returncode, 0)
