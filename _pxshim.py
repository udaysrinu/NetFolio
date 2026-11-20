#!/usr/bin/env python3
"""Static file server + a stand-in for /api/price, for verifying the Tracker locally.

Serves the folio-terminal directory and answers /api/price from the same Kite snapshot
bucket_report.py uses. Deterministic on purpose: the page's numbers must then equal the
report's to the rupee, which live Yahoo prices could never prove.

  python3 _pxshim.py 8899
"""
import json, os, re, sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP = max(f for f in os.listdir(HERE) if f.startswith('_live_') and f.endswith('.json'))
PX = {h['s'].upper(): h['ltp'] for h in json.load(open(os.path.join(HERE, SNAP)))['holdings']}
# Mirror api/price.js exactly: try the symbol as named, then de-suffixed, and vice versa. The map may
# hold ACME-BE (its NSE series symbol on the day it was bought) while the broker now reports plain ACME.
SERIES = ('-BE', '-BZ', '-BL', '-SM', '-ST', '-IL', '-GB', '-GS')
ALIAS = {}
for k, v in PX.items():
    for suf in SERIES:
        if k.endswith(suf):
            ALIAS[k[:-len(suf)]] = v          # ACME-BE held  -> answer a request for ACME
    for suf in SERIES:
        ALIAS.setdefault(k + suf, v)          # ACME held     -> answer a request for ACME-BE
ROOT = os.environ.get('SHARECASE_REPO') or os.path.join(os.path.dirname(HERE), 'folio-terminal')

# Read the real handler's cap out of the real handler. A shim that is MORE permissive than production
# is worse than no shim: it reports success on input production rejects. That is exactly what happened
# - 65 symbols priced fine here and returned HTTP 400 live - so the number is parsed, never retyped.
_src = open(os.path.join(ROOT, 'api', 'price.js')).read()
MAX_SYMBOLS = int(re.search(r'MAX_SYMBOLS\s*=\s*(\d+)', _src).group(1))


class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def do_GET(self):
        # DNS rebinding guard, same as _devserver.mjs: a hostile page that points its own domain at
        # 127.0.0.1 would otherwise read these pages and the snapshot's prices as same-origin. Its
        # requests still carry ITS hostname, so accept only our own.
        if (self.headers.get('Host') or '').lower() not in (f'localhost:{port}', f'127.0.0.1:{port}'):
            self.send_error(403, 'forbidden host')
            return
        u = urlparse(self.path)
        if u.path != '/api/price':
            return super().do_GET()
        want = [s.strip().upper() for s in parse_qs(u.query).get('symbols', [''])[0].split(',') if s.strip()]
        if len(want) > MAX_SYMBOLS:                       # fail exactly the way production fails
            return self._json(400, {'error': f'at most {MAX_SYMBOLS} symbols per request',
                                    'got': len(want)})
        prices = {s: PX[s] for s in want if s in PX}
        for s in want:
            if s not in prices and s in ALIAS:
                prices[s] = ALIAS[s]
        self._json(200, {'prices': prices, 'missing': [s for s in want if s not in prices],
                         'source': SNAP})

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
print(f'serving {ROOT} on :{port}, prices from {SNAP} ({len(PX)} symbols)', flush=True)
ThreadingHTTPServer(('127.0.0.1', port), H).serve_forever()
