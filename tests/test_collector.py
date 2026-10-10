import asyncio
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace as NS
import unittest
import tempfile
from pathlib import Path
from wallstreet_quant.collector import Store, collect_channel, canonical_url
from wallstreet_quant.news import NewsError

NOW = datetime.now(timezone.utc)
def msg(i, text='NVDA headline', edit=None, entities=None):
    return NS(id=i, message=text, date=NOW-timedelta(minutes=5), edit_date=edit, entities=entities)

class FakeClient:
    def __init__(self, messages, peer=123):
        self.messages, self.peer = messages, peer
    async def get_entity(self, handle):
        return NS(id=self.peer, username=handle, broadcast=True)
    async def iter_messages(self, entity, min_id=0, max_id=None, reverse=False, limit=50):
        rows=[m for m in self.messages if m.id > min_id and (max_id is None or m.id < max_id)]
        for m in sorted(rows,key=lambda x:x.id,reverse=not reverse)[:limit]:
            yield m

class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.store=Store(':memory:')
        self.registry={'channels':{'marketfeed':{'enabled':True}}}
        self.store.pin('marketfeed',123)
    def tearDown(self): self.store.db.close()
    def save(self,m): return self.store.save('marketfeed',123,m,self.registry,NOW)
    def test_replay_no_duplicate_and_edit_retained(self):
        self.assertTrue(self.save(msg(1)))
        self.assertFalse(self.save(msg(1)))
        self.assertTrue(self.save(msg(1,'NVDA corrected',NOW-timedelta(seconds=5))))
        self.assertEqual(self.store.report()['revisions'],2)
        events=self.store.export()['events']
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]['body'],'NVDA corrected')
        self.assertFalse(events[0]['verified'])
    def test_peer_reassignment_blocked(self):
        with self.assertRaises(NewsError): self.store.pin('marketfeed',999)
    def test_backlog_drains_without_cursor_skips(self):
        self.save(msg(1))
        client=FakeClient([msg(i) for i in range(1,252)])
        asyncio.run(collect_channel(client,self.store,'marketfeed',self.registry))
        self.assertEqual(self.store.cursor('marketfeed'),101)
        asyncio.run(collect_channel(client,self.store,'marketfeed',self.registry))
        self.assertEqual(self.store.cursor('marketfeed'),201)
        asyncio.run(collect_channel(client,self.store,'marketfeed',self.registry))
        self.assertEqual(self.store.report()['revisions'],251)
    def test_initial_history_bounded(self):
        asyncio.run(collect_channel(FakeClient([msg(i) for i in range(1,201)]),self.store,'marketfeed',self.registry))
        self.assertEqual(self.store.report()['revisions'],50)
        self.assertEqual(self.store.cursor('marketfeed'),200)
    def test_url_tracking_duplicates_and_substantive_changes(self):
        self.save(msg(1,'NVDA news',entities=[NS(url='https://example.com/story?utm_source=tg')]))
        self.save(msg(2,'NVDA news',entities=[NS(url='https://example.com/story?utm_source=x')]))
        self.assertEqual(len(self.store.export()['events']),1)
        self.save(msg(3,'NVDA updated news',entities=[NS(url='https://example.com/story')]))
        self.assertEqual(len(self.store.export()['events']),2)
    def test_hidden_link_edit_changes_revision(self):
        self.save(msg(1,entities=[NS(url='https://example.com/a')]))
        self.save(msg(1,entities=[NS(url='https://example.com/b')]))
        self.assertEqual(self.store.report()['revisions'],2)
    def test_media_only_advances_cursor(self):
        self.assertFalse(self.save(msg(1,'')))
        self.assertEqual(self.store.cursor('marketfeed'),1)
    def test_invalid_post_does_not_advance(self):
        with self.assertRaises(NewsError): self.save(msg(1,'a'*12001))
        self.assertEqual(self.store.cursor('marketfeed'),0)
    def test_urls_and_delay(self):
        self.assertIsNone(canonical_url('https://user:pass@example.com/a'))
        self.assertEqual(canonical_url('https://EXAMPLE.com/a?utm_source=x&id=3#x'),'https://example.com/a?id=3')
        self.save(msg(1))
        self.assertEqual(self.store.db.execute('SELECT delay FROM posts').fetchone()[0],300)
    def test_out_of_order_edit_does_not_replace_latest(self):
        self.save(msg(1,'NVDA latest',NOW-timedelta(seconds=1)))
        self.save(msg(1,'NVDA older',NOW-timedelta(seconds=10)))
        self.assertEqual(self.store.export()['events'][0]['body'],'NVDA latest')

    def test_restart_recovers_cursor_and_dedup(self):
        with tempfile.TemporaryDirectory() as d:
            path=str(Path(d)/'news.sqlite3')
            store=Store(path)
            store.pin('marketfeed',123)
            store.save('marketfeed',123,msg(7),self.registry,NOW)
            store.db.close()
            store=Store(path)
            try:
                self.assertEqual(store.cursor('marketfeed'),7)
                self.assertFalse(store.save('marketfeed',123,msg(7),self.registry,NOW))
                self.assertEqual(store.report()['revisions'],1)
            finally: store.db.close()
    def test_disconnect_preserves_only_committed_progress(self):
        self.save(msg(1))
        class Interrupted(FakeClient):
            async def iter_messages(self,*args,**kwargs):
                yield msg(2)
                raise ConnectionError('fixture disconnect')
        with self.assertRaises(ConnectionError):
            asyncio.run(collect_channel(Interrupted([]),self.store,'marketfeed',self.registry))
        self.assertEqual(self.store.cursor('marketfeed'),2)
        asyncio.run(collect_channel(FakeClient([msg(i) for i in range(1,5)]),self.store,'marketfeed',self.registry))
        self.assertEqual(self.store.report()['revisions'],4)
    def alias_client(self, active=True, broadcast=True, peer=123):
        class Aliased(FakeClient):
            async def get_entity(self, handle):
                return NS(id=peer, username=None, broadcast=broadcast,
                          usernames=[NS(username=handle.upper(),active=active)])
        return Aliased([msg(1)])
    def test_active_alias_without_primary_username(self):
        asyncio.run(collect_channel(self.alias_client(),self.store,'marketfeed',self.registry))
        self.assertEqual(self.store.report()['revisions'],1)
    def test_inactive_alias_and_nonbroadcast_rejected(self):
        for client in (self.alias_client(active=False),self.alias_client(broadcast=False)):
            with self.assertRaises(NewsError):
                asyncio.run(collect_channel(client,self.store,'marketfeed',self.registry))
        self.assertEqual(self.store.cursor('marketfeed'),0)
    def test_active_alias_does_not_bypass_peer_pin(self):
        with self.assertRaises(NewsError):
            asyncio.run(collect_channel(self.alias_client(peer=999),self.store,'marketfeed',self.registry))
