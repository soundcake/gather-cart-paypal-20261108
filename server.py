"""Single-user loopback demo; prices and payment decisions stay on the server."""
import argparse
import hmac
import json
import secrets
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from cart import CATALOG, QuoteStore, make_plan, validate_request
from paypal import Sandbox, create_checkout, finish_checkout
from providers import Samples, LocalModel, MLXModel, SAMPLES

ROOT = Path(__file__).resolve().parent


def make_handler(planner, gateway, store, origin, mode):
    sessions = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def host_ok(self):
            return self.headers.get('Host') == urlsplit(origin).netloc

        def session(self):
            cookie = SimpleCookie()
            cookie.load(self.headers.get('Cookie', ''))
            sid = cookie['gather'].value if 'gather' in cookie else None
            for key in list(sessions):
                if time.time() - sessions[key]['created'] > 7200:
                    del sessions[key]
            if sid not in sessions:
                if len(sessions) >= 100:
                    raise ValueError('Session capacity reached. Restart the local demo.')
                sid = secrets.token_hex(24)
                sessions[sid] = {'csrf': secrets.token_hex(24), 'created': time.time()}
            self.sid = sid
            return sessions[sid]

        def reply(self, code, data, content_type='application/json'):
            raw = json.dumps(data).encode() if content_type == 'application/json' else data
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if getattr(self, 'sid', None):
                # Lax permits the top-level GET back from PayPal. POST still
                # requires the exact Origin and a session-bound CSRF token.
                self.send_header('Set-Cookie', f'gather={self.sid}; HttpOnly; SameSite=Lax; Path=/')
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if not self.host_ok():
                return self.reply(403, {'error': 'Use the local application URL.'})
            path = urlsplit(self.path).path
            try:
                session = self.session()
                if path == '/api/config':
                    return self.reply(200, {'csrf': session['csrf'], 'mode': mode,
                        'checkout_ready': gateway.ready and mode != 'samples', 'catalog': CATALOG, 'samples': SAMPLES})
                if path == '/api/quote':
                    qid = parse_qs(urlsplit(self.path).query).get('id', [''])[0]
                    q = store.get(qid, self.sid)
                    return self.reply(200, {'id': q['id'], 'plan': q['plan'], 'receipt': q['receipt'],
                                            'has_order': bool(q['paypal_id'])})
                assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                          '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                          '/style.css': ('style.css', 'text/css; charset=utf-8')}
                if path in assets:
                    filename, mime = assets[path]
                    return self.reply(200, (ROOT / filename).read_bytes(), mime)
                self.reply(404, {'error': 'Not found.'})
            except ValueError as exc:
                self.reply(400, {'error': str(exc)})

        def do_POST(self):
            if not self.host_ok() or self.headers.get('Origin') != origin:
                return self.reply(403, {'error': 'Use the local application to make changes.'})
            try:
                session = self.session()
                if not hmac.compare_digest(self.headers.get('X-CSRF-Token', ''), session['csrf']):
                    return self.reply(403, {'error': 'Reload the page before continuing.'})
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 8192 or self.headers.get('Content-Type') != 'application/json':
                    raise ValueError('Send a small JSON request.')
                body = json.loads(self.rfile.read(length))
                path = urlsplit(self.path).path
                if path == '/api/plan':
                    request = validate_request(body)
                    selection, elapsed = planner.choose(request['brief'])
                    plan = make_plan(request, selection)
                    qid = store.put(plan, self.sid)
                    return self.reply(200, {'id': qid, 'plan': plan, 'elapsed_seconds': elapsed, 'mode': mode})
                if path in {'/api/checkout', '/api/capture'}:
                    if mode == 'samples':
                        raise ValueError('Sample rehearsal does not create sandbox orders.')
                    if not isinstance(body, dict) or set(body) != {'quote_id'} or not isinstance(body['quote_id'], str):
                        raise ValueError('Choose a saved basket.')
                    q = store.get(body['quote_id'], self.sid)
                    if q['receipt']:
                        return self.reply(200, {'receipt': q['receipt']})
                    # A quote can be captured only while its reviewed price is valid.
                    store.get(body['quote_id'], self.sid, for_payment=True)
                    if path == '/api/checkout':
                        return self.reply(200, create_checkout(gateway, q, origin))
                    return self.reply(200, {'receipt': finish_checkout(gateway, q)})
                self.reply(404, {'error': 'Not found.'})
            except (ValueError, TypeError) as exc:
                self.reply(400, {'error': str(exc)})
            except Exception:
                # Tokens, HTTP bodies and model paths are never exposed to the browser.
                self.reply(502, {'error': 'The local model or PayPal sandbox could not complete this request. The basket is preserved; retry it after checking the connection.'})
    return Handler


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--port', type=int, default=8786)
    p.add_argument('--provider', choices=['samples', 'ollama', 'mlx', 'spark'], default='samples')
    p.add_argument('--model')
    args = p.parse_args()
    if args.provider == 'spark':
        if not args.model:
            p.error('Provide the local Spark model directory with --model.')
        from planner import Planner
        planner = Planner(args.model)
    elif args.provider == 'mlx':
        planner = MLXModel(args.model)
    else:
        planner = Samples() if args.provider == 'samples' else LocalModel(args.model)
    origin = f'http://127.0.0.1:{args.port}'
    server = HTTPServer(('127.0.0.1', args.port), make_handler(planner, Sandbox(), QuoteStore(), origin, args.provider))
    print(f'Gather Cart: {origin} ({args.provider})', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
