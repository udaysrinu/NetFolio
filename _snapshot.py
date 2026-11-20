#!/usr/bin/env python3
"""Price snapshot for build_live.py, fetched server-side from Yahoo (same v8 chart call as api/price.js).

    python3 _snapshot.py            -> writes _holdings_<dd><mon>.json, prints the build_live command

Quantities come from the map (build_live reconciles them anyway); this only supplies ltp + prev close,
so nothing has to be hand-copied out of Kite. A symbol Yahoo cannot price is a hard failure, not a gap.
"""
import json, re, sys, datetime, urllib.request
from concurrent.futures import ThreadPoolExecutor

SERIES = re.compile(r'-(BE|BZ|BL|SM|ST|IL|GB|GS)$')
M = json.load(open('_smallcase_map.json'))['batches']
Q = {}
for b in M.values():
    for s, h in b.get('holdings', {}).items():
        k = SERIES.sub('', s)                      # snapshot keys are base names, as in earlier files
        Q[k] = Q.get(k, 0) + (h['q'] if isinstance(h, dict) else h)


def yahoo(sym):
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}.NS?range=1d&interval=1d'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; sharecase/1.0)'})
    try:
        m = json.load(urllib.request.urlopen(req, timeout=10))['chart']['result'][0]['meta']
    except Exception:
        return None
    px, pc = m.get('regularMarketPrice'), m.get('chartPreviousClose') or m.get('previousClose')
    if m.get('currency') != 'INR' or not px or not pc:
        return None
    return round(px, 2), round(pc, 2)


with ThreadPoolExecutor(16) as ex:
    got = dict(zip(Q, ex.map(yahoo, Q)))
bad = [s for s, v in got.items() if v is None]
if bad:
    sys.exit(f'unpriced: {bad}')
today = datetime.date.today()
out = f"_holdings_{today.strftime('%d%b').lower()}.json"
json.dump({'asof': today.isoformat(), 'source': f'Yahoo v8 chart via _snapshot.py, {datetime.datetime.now():%d %b %H:%M}',
           'holdings': {s: {'q': Q[s], 'ltp': got[s][0], 'close': got[s][1]} for s in sorted(Q)}},
          open(out, 'w'), indent=1)
print(f'{out}: {len(Q)} symbols priced\npython3 build_live.py {out}')
