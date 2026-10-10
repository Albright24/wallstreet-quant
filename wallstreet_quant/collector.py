"""Collection-only Telegram user client. Never invokes a model or trading engine."""
import argparse
import asyncio
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import signal
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from .channel_news import normalize_post
from .news import NewsError
from .worker import WorkerLock

DEFAULT_SOURCES = Path(__file__).resolve().parent.parent / 'config' / 'telegram-sources.json'


def canonical_url(value):
    try:
        p = urlsplit(value.rstrip('.,;)'))
        if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
            return None
        query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                 if not k.lower().startswith('utm_') and k.lower() not in ('fbclid', 'gclid')]
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or '/', urlencode(sorted(query)), ''))
    except ValueError:
        return None


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=15)
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS channels(handle TEXT PRIMARY KEY, peer INTEGER NOT NULL, cursor INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS posts(peer INTEGER, message INTEGER, revision TEXT, handle TEXT,
          observed TEXT, published TEXT, updated TEXT, delay REAL, fingerprint TEXT, body TEXT,
          PRIMARY KEY(peer,message,revision));
        CREATE TABLE IF NOT EXISTS health(handle TEXT PRIMARY KEY, checked TEXT, status TEXT);
        ''')

    def pin(self, handle, peer):
        with self.db:
            row = self.db.execute('SELECT peer FROM channels WHERE handle=?', (handle,)).fetchone()
            if row and row[0] != peer:
                raise NewsError('Channel identity changed; manual review required')
            self.db.execute('INSERT OR IGNORE INTO channels(handle,peer) VALUES (?,?)', (handle, peer))

    def cursor(self, handle):
        return self.db.execute('SELECT cursor FROM channels WHERE handle=?', (handle,)).fetchone()[0]

    def health(self, handle, status):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO health VALUES (?,?,?)',
                            (handle, datetime.now(timezone.utc).isoformat(), status))

    def save(self, handle, peer, message, registry, now=None):
        now = now or datetime.now(timezone.utc)
        text = message.message or ''
        envelope = dict(channel=handle, message_id=message.id, text=text,
                        published_at=message.date.isoformat(),
                        edited_at=message.edit_date.isoformat() if message.edit_date else None)
        # Service/media-only messages still advance the durable cursor.
        if not text.strip():
            with self.db:
                self.db.execute('UPDATE channels SET cursor=MAX(cursor,?) WHERE handle=?', (message.id, handle))
            return False
        normalized = normalize_post(envelope, registry, now)
        links = re.findall(r'https?://[^\s<>"\]]+', text)
        for entity in message.entities or []:
            if getattr(entity, 'url', None):
                links.append(entity.url)
        urls = sorted({u for link in links if (u := canonical_url(link))})
        row = (normalized['events'] or normalized['unmatched'])[0]
        # Same article links mark possible duplication; revisions are always preserved.
        fingerprint = hashlib.sha256(json.dumps(urls or [' '.join(text.lower().split())]).encode()).hexdigest()
        for event in normalized['events'] + normalized['unmatched']:
            event.update(article_urls=urls, telegram_peer_id=peer,
                         verification='telegram_delivery; claims_unverified',
                         id=f"{peer}:{event['id']}")
        revision = hashlib.sha256(json.dumps([row['revision'], urls]).encode()).hexdigest()
        for event in normalized['events'] + normalized['unmatched']:
            event['revision'] = revision
        payload = dict(normalized, article_urls=urls, duplicate_key=fingerprint)
        with self.db:
            inserted = self.db.execute('INSERT OR IGNORE INTO posts VALUES (?,?,?,?,?,?,?,?,?,?)',
                (peer, message.id, revision, handle, now.isoformat(), row['published_at'],
                 row['updated_at'], (now - datetime.fromisoformat(row['updated_at'])).total_seconds(),
                 fingerprint, json.dumps(payload))).rowcount
            self.db.execute('UPDATE channels SET cursor=MAX(cursor,?) WHERE handle=?', (message.id, handle))
        return bool(inserted)

    def report(self):
        return {'mode': 'collection_only', 'revisions': self.db.execute('SELECT COUNT(*) FROM posts').fetchone()[0],
                'channels': [dict(zip(('handle','peer','cursor'), r)) for r in self.db.execute('SELECT * FROM channels')],
                'delays': [dict(zip(('handle', 'observations', 'average_seconds', 'maximum_seconds'), r))
                           for r in self.db.execute('SELECT handle,COUNT(*),AVG(delay),MAX(delay) FROM posts GROUP BY handle')],
                'health': [dict(zip(('handle','checked','status'),r)) for r in self.db.execute('SELECT * FROM health')],
                'delay_note': 'Observed minus publication/edit time, including polling and catch-up; not wire latency.'}

    def export(self):
        # Export latest known revision per post, collapse exact duplicate story payloads.
        records = self.db.execute('''SELECT p.body,p.fingerprint FROM posts p
           WHERE p.rowid=(SELECT q.rowid FROM posts q WHERE q.peer=p.peer AND q.message=p.message
                         ORDER BY q.updated DESC,q.rowid DESC LIMIT 1) ORDER BY p.rowid''')
        result, seen = {'events': [], 'unmatched': []}, set()
        for body, fingerprint in records:
            item = json.loads(body)
            for key in ('events', 'unmatched'):
                for event in item[key]:
                    # Different text at the same URL may be a substantive update.
                    identity = (fingerprint, event.get('symbol'), event['body'])
                    if identity not in seen:
                        seen.add(identity)
                        result[key].append(event)
        return result


async def collect_channel(client, store, handle, registry):
    entity = await client.get_entity(handle)
    names = {(getattr(entity, 'username', None) or '').lower()}
    names.update((u.username or '').lower() for u in (getattr(entity, 'usernames', None) or [])
                 if getattr(u, 'active', False))
    if not getattr(entity, 'broadcast', False) or handle.lower() not in names:
        raise NewsError('Expected a public broadcast channel')
    store.pin(handle, entity.id)
    cursor = store.cursor(handle)
    added = 0
    if cursor:
        # Ascending pages ensure cursor never jumps over a backlog.
        async for message in client.iter_messages(entity, min_id=cursor, reverse=True, limit=100):
            added += store.save(handle, entity.id, message, registry)
    else:
        initial = [m async for m in client.iter_messages(entity, limit=50)]
        for message in reversed(initial):
            added += store.save(handle, entity.id, message, registry)
    # Only reconcile already-covered IDs: do not advance across an undrained backlog.
    cursor = store.cursor(handle)
    async for message in client.iter_messages(entity, max_id=cursor + 1, limit=50):
        added += store.save(handle, entity.id, message, registry)
    store.health(handle, 'ok')
    return added


def make_client(session):
    try:
        from telethon import TelegramClient
    except ImportError:
        raise NewsError('Install requirements-telegram.txt first') from None
    api_id, api_hash = os.getenv('WQ_TELEGRAM_API_ID', ''), os.getenv('WQ_TELEGRAM_API_HASH', '')
    if not api_id.isdigit() or int(api_id) <= 0 or not re.fullmatch(r'[a-fA-F0-9]{32}', api_hash):
        raise NewsError('Set WQ_TELEGRAM_API_ID and WQ_TELEGRAM_API_HASH locally')
    path = Path(session).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return TelegramClient(str(path), int(api_id), api_hash, auto_reconnect=True,
                          connection_retries=5, request_retries=2, flood_sleep_threshold=0)


async def run(args):
    client = make_client(args.session)
    loop = asyncio.get_running_loop()
    current = asyncio.current_task()
    try:
        loop.add_signal_handler(signal.SIGTERM, current.cancel)
    except (NotImplementedError, RuntimeError):
        pass  # Windows supports Ctrl+C through asyncio.run.
    try:
        if args.command == 'login':
            await client.start(phone=lambda: input('Telegram phone number: '),
                               code_callback=lambda: getpass.getpass('Telegram login code: '),
                               password=lambda: getpass.getpass('Telegram 2FA password: '))
            print('Local Telegram session authorized. No channels joined or messages sent.')
            return
        await client.connect()
        if not await client.is_user_authorized():
            raise NewsError('Run the login command locally first')
        registry = json.loads(Path(args.sources).read_text())
        handles = [h for h, v in registry['channels'].items() if v.get('collect') is True and v.get('enabled') is True]
        if not handles:
            raise NewsError('No collection channels enabled')
        store = Store(args.db)
        try:
            cycle = 0
            while not args.cycles or cycle < args.cycles:
                for handle in handles:
                    try:
                        if not client.is_connected():
                            await client.connect()
                        await collect_channel(client, store, handle, registry)
                    except NewsError:
                        store.health(handle, 'validation_failed; review source or post')
                    except Exception as exc:
                        # Telegram flood waits apply to the account, not only this channel.
                        from telethon.errors import FloodWaitError, RPCError
                        if isinstance(exc, FloodWaitError):
                            store.health(handle, 'rate_limited')
                            await asyncio.sleep(max(1, exc.seconds))
                            break
                        if isinstance(exc, (RPCError, OSError, ConnectionError, asyncio.TimeoutError)):
                            store.health(handle, 'request_failed; retry next cycle')
                        else:
                            raise
                cycle += 1
                if not args.cycles or cycle < args.cycles:
                    await asyncio.sleep(args.interval)
            print(json.dumps(store.report(), indent=2))
        finally:
            store.db.close()
    finally:
        await client.disconnect()


def main():
    parser = argparse.ArgumentParser(description='Wallstreet Quant collection-only Telegram reader')
    parser.add_argument('command', choices=('login','run','status','export'))
    parser.add_argument('--session', default=str(Path.home() / '.wallstreet-quant' / 'reader'))
    parser.add_argument('--db', default='telegram_news.sqlite3')
    parser.add_argument('--sources', default=str(DEFAULT_SOURCES))
    parser.add_argument('--cycles', type=int, default=0)
    parser.add_argument('--interval', type=float, default=15)
    args = parser.parse_args()
    if args.cycles < 0 or not 5 <= args.interval <= 3600:
        parser.error('cycles must be nonnegative; interval must be 5–3600 seconds')
    try:
        if args.command in ('status','export'):
            store = Store(args.db)
            try:
                print(json.dumps(store.report() if args.command == 'status' else store.export(), indent=2))
            finally:
                store.db.close()
        else:
            # Also lock session: sharing it across processes can corrupt update state.
            session_path = Path(args.session).expanduser().resolve()
            session_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.umask(0o077)
            with WorkerLock(str(session_path)):
                with WorkerLock(str(Path(args.db).resolve())):
                    asyncio.run(run(args))
    except (KeyboardInterrupt, asyncio.CancelledError):
        print('Collector stopped; committed posts are retained.')
    except Exception as exc:
        parser.exit(2, f'Collector stopped ({type(exc).__name__}); check local setup and health.\n')


if __name__ == '__main__':
    main()
