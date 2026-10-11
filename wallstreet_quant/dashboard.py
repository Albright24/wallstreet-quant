"""Local, read-only dashboard. No credentials, external requests or order routes."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import sqlite3
import tempfile
import webbrowser

WEB = Path(__file__).with_name('web')


def snapshot(path):
    # Read-only URI avoids accidentally creating or modifying a trading database.
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
        db.execute('BEGIN')
        row = db.execute('SELECT body FROM state WHERE id=1').fetchone()
        if not row:
            raise ValueError('No account state')
        account = json.loads(row[0])
        ledger = [json.loads(r[0]) for r in db.execute('SELECT body FROM ledger ORDER BY sequence DESC LIMIT 200')]
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        runs = [json.loads(r[0]) for r in db.execute('SELECT body FROM news_runs ORDER BY rowid DESC LIMIT 200')] if 'news_runs' in tables else []
    return {'account': account, 'ledger': ledger, 'runs': runs,
            'as_of': datetime.now(timezone.utc).isoformat(), 'limit': 200,
            'mode': 'LOCAL_PAPER', 'database_name': Path(path).name}


def make_demo():
    from .core import Engine
    from .agent_demo import run, FixtureAdvisor, FixtureMarket
    from .pipeline import Pipeline
    from datetime import timedelta
    path = Path(tempfile.mkdtemp(prefix='wq-dashboard-demo-')) / 'synthetic-demo.sqlite3'
    engine = Engine(str(path))
    try:
        run(engine)
        now = datetime.now(timezone.utc)
        event = {'source': 'synthetic_fixture', 'id': 'stale-demo', 'revision': '1',
                 'symbol': 'DEMO_EQUITY', 'published_at': (now-timedelta(hours=1)).isoformat(),
                 'headline': 'Synthetic: expired earnings headline', 'body': 'Invented stale event for a freshness check.',
                 'url': 'https://example.com/fixture', 'verified': True, 'synthetic': True,
                 'evidence': [{'id': 'fixture', 'text': 'Invented stale event for a freshness check.'}]}
        Pipeline(engine, FixtureAdvisor(), FixtureMarket()).process(event)
    finally:
        engine.close()
    return path


def handler_for(path, demo=False):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.headers.get('Host') not in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}:
                self.send_error(403)
                return
            route = self.path.split('?', 1)[0]
            try:
                if route in ('/api/state', '/api/export'):
                    value = snapshot(path)
                    value['demo_session'] = demo
                    payload = json.dumps(value, allow_nan=False).encode()
                    kind = 'application/json; charset=utf-8'
                elif route in ('/', '/app.js', '/style.css'):
                    name = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}[route]
                    payload = (WEB / name).read_bytes()
                    kind = {'/': 'text/html; charset=utf-8', '/app.js': 'text/javascript; charset=utf-8', '/style.css': 'text/css; charset=utf-8'}[route]
                else:
                    self.send_error(404)
                    return
            except (sqlite3.Error, ValueError, OSError):
                self.send_error(503, 'Account data unavailable; check the selected database')
                return
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            if route == '/api/export':
                self.send_header('Content-Disposition', 'attachment; filename="wallstreet-quant-audit.json"')
            self.end_headers()
            self.wfile.write(payload)
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--demo', action='store_true', help='Create isolated synthetic fixture records; no AI or exchange requests')
    mode.add_argument('--db', help='Read an existing paper account SQLite database without modifying it')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    path = make_demo() if args.demo else Path(args.db).resolve()
    try:
        snapshot(path)
        server = HTTPServer(('127.0.0.1', args.port), handler_for(path, args.demo))
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f'Cannot start dashboard: {error}\n')
    url = f'http://127.0.0.1:{server.server_port}'
    print(f'Wallstreet Quant dashboard: {url}', flush=True)
    print('SYNTHETIC DEMO: no AI, news or exchange calls.' if args.demo else 'Read-only view of your local paper account.', flush=True)
    print('Keep this window open. Press Ctrl+C to stop.', flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
