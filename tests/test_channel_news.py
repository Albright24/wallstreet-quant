import unittest
from datetime import datetime, timezone
from wallstreet_quant.channel_news import normalize_post, import_posts
from wallstreet_quant.news import NewsError

class ChannelTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
        self.registry = {'channels': {'marketfeed': {'enabled': True}, 'disabled': {'enabled': False}}}
        self.post = {'channel': '@MarketFeed', 'message_id': 12,
                     'published_at': '2026-10-06T11:55:00Z', 'text': 'Rumour: Nvidia and Microsoft announce deal'}
    def run_post(self, **changes):
        return normalize_post(dict(self.post, **changes), self.registry, self.now)
    def test_trust_and_mapping(self):
        events = self.run_post(verified=True, url='https://evil.example', symbol='WRONG')['events']
        self.assertEqual({e['symbol'] for e in events}, {'RNVDAUSDT', 'RMSFTUSDT'})
        self.assertTrue(all(e['verified'] is False for e in events))
        self.assertEqual(events[0]['url'], 'https://t.me/marketfeed/12')
    def test_disabled_and_unknown(self):
        for handle in ['disabled', 'unknown', '../marketfeed']:
            with self.assertRaises(NewsError): self.run_post(channel=handle)
    def test_edit_keeps_age_and_changes_revision(self):
        first = self.run_post()['events'][0]
        second = self.run_post(text='Nvidia denies rumour', edited_at='2026-10-06T11:58:00Z')['events'][0]
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(first['published_at'], second['published_at'])
        self.assertNotEqual(first['revision'], second['revision'])
        self.assertEqual(first['revision'], self.run_post()['events'][0]['revision'])
    def test_macro_retained_without_forced_mapping(self):
        result = self.run_post(text='Fed announces rate decision')
        self.assertFalse(result['events'])
        self.assertEqual(len(result['unmatched']), 1)
    def test_invalid_payloads(self):
        for changes in [dict(message_id=True),dict(message_id=0),dict(text=''),dict(text='a'*12001),
                        dict(published_at='2026-10-06T12:00:00'),
                        dict(published_at='2026-10-07T12:00:00Z'),dict(edited_at='2026-10-05T12:00:00Z')]:
            with self.assertRaises(NewsError): self.run_post(**changes)
    def test_batch(self):
        result = import_posts([self.post,self.post],self.registry,self.now)
        self.assertEqual(len(result['events']),4)
        self.assertEqual(result['connection'],'offline_import_only')
        with self.assertRaises(NewsError): import_posts([self.post]*1001,self.registry,self.now)
