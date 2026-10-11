"""Token-protected paper-account reader for an HTTPS reverse proxy."""
import argparse
import hmac
import json
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from .dashboard import snapshot


def handler_for(db_path, token):
    if len(token) < 32 or not token.isascii():
        raise ValueError('WQ_DASHBOARD_TOKEN must contain at least 32 ASCII characters')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, data):
            body = json.dumps(data, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            received = self.headers.get('Authorization', '').encode('utf-8')
            if not hmac.compare_digest(received, ('Bearer ' + token).encode()):
                return self.reply(401, {'error': 'Unauthorized'})
            if self.path.split('?', 1)[0] != '/api/state':
                return self.reply(404, {'error': 'Not found'})
            try:
                value = snapshot(db_path)
                value.pop('database_name', None)
                value['mode'] = 'PAPER'
                return self.reply(200, value)
            except (sqlite3.Error, ValueError, OSError):
                return self.reply(503, {'error': 'Paper account unavailable'})

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    try:
        handler = handler_for(args.db, os.environ.get('WQ_DASHBOARD_TOKEN', ''))
        snapshot(args.db)
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(1, str(error) + '\n')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler)
    print('Paper account reader ready on loopback port', args.port, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
