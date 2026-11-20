#!/usr/bin/env python3
"""A made-up portfolio to try Netfolio on, and the only source of its public screenshots.

    python3 demo.py [DIR] [--no-serve]   build it in DIR (default: a new temp dir), run the pipeline,
                                         then serve the pages on http://localhost:8901

It copies this repo's code into DIR, writes an invented contract-note history and basket map there,
and runs the same scripts you would: _snapshot.py, _daily.py, build_live.py, test_money.py. Every
script finds its data next to itself, so the demo never reads or writes your files. The server runs on
port 8901, a different browser origin from your real :8899, so the demo can't overwrite your saved data.

The trades are invented, but their PRICES are real NSE closes on those days, so the daily curve against
Nifty is honest. Only stocks with no split, bonus or demerger in the window are used, and that is
checked here, because otherwise their quantities would need corporate actions.
"""
import datetime as dt, glob, json, os, shutil, subprocess, sys, tempfile, urllib.request
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
SHARECASE = os.environ.get('SHARECASE_REPO') or os.path.join(HERE, '..', 'folio-terminal')
PORT = 8901

BASKETS = {   # tag: (label, start, symbols)
    'CORE': ('Large Cap Core', '2023-02-06', ['INFY', 'ICICIBANK', 'BHARTIARTL', 'SBIN']),
    'POWER': ('Power & Utilities', '2023-06-12', ['NHPC', 'COALINDIA', 'TORNTPOWER', 'GAIL']),
    'PICKS': ('Hand-picked', '2023-09-18', ['SUNPHARMA', 'MARUTI', 'HEROMOTOCO']),
}
# (date, symbol, side, quantity). A date that isn't a trading day takes the next session's close.
PLAN = [(BASKETS['CORE'][1], s, 'buy', q) for s, q in (('INFY', 20), ('ICICIBANK', 30), ('BHARTIARTL', 30), ('SBIN', 40))] + \
       [(BASKETS['POWER'][1], s, 'buy', q) for s, q in (('NHPC', 600), ('COALINDIA', 120), ('TORNTPOWER', 40), ('GAIL', 300))] + \
       [(BASKETS['PICKS'][1], s, 'buy', q) for s, q in (('SUNPHARMA', 25), ('MARUTI', 3), ('HEROMOTOCO', 10))] + [
    ('2024-01-15', 'INFY', 'buy', 10), ('2024-03-11', 'SBIN', 'sell', 15), ('2024-05-20', 'HEROMOTOCO', 'sell', 5),
    ('2024-07-29', 'NHPC', 'sell', 300), ('2024-10-07', 'ICICIBANK', 'buy', 10), ('2024-12-16', 'HEROMOTOCO', 'sell', 5),
    ('2025-02-10', 'SBIN', 'buy', 20), ('2025-04-07', 'COALINDIA', 'buy', 40), ('2025-06-23', 'TORNTPOWER', 'sell', 15),
    ('2025-09-15', 'INFY', 'sell', 12), ('2026-01-12', 'MARUTI', 'buy', 2), ('2026-04-20', 'GAIL', 'sell', 100),
    ('2026-08-10', 'NHPC', 'buy', 200), ('2026-10-12', 'BHARTIARTL', 'sell', 10)]


def closes(sym, start):
    """{date: close} since `start` from Yahoo, refusing a stock that split in the window."""
    p1 = int(dt.datetime.fromisoformat(start).timestamp()) - 86400
    url = (f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}.NS?period1={p1}'
           f'&period2={int(dt.datetime.now().timestamp())}&interval=1d&events=split')
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; netfolio-demo/1.0)'})
    r = json.load(urllib.request.urlopen(req, timeout=20))['chart']['result'][0]
    if r.get('events', {}).get('splits'):
        sys.exit(f'{sym} split or issued a bonus in the window; pick another stock for the demo')
    day = lambda t: dt.datetime.fromtimestamp(t, dt.timezone(dt.timedelta(hours=5, minutes=30))).date().isoformat()
    return {day(t): c for t, c in zip(r['timestamp'], r['indicators']['quote'][0]['close']) if c}


def build(out):
    sys.path.insert(0, HERE)
    from tradebook import fifo
    px = {s: closes(s, b[1]) for b in BASKETS.values() for s in b[2]}
    trades, n = [], 0
    for d, s, side, q in sorted(PLAN):
        day = min(x for x in px[s] if x >= d)
        n += 1
        trades.append({'s': s, 'd': day, 't': side, 'q': float(q), 'p': round(px[s][day], 2), 'id': f'{n:08d}',
                       'x': 'NSE', 'tm': f'{day}T10:{n % 60:02d}:00', 'isin': '', 'oid': f'{n:016d}', 'brokerage': 0.0})
    # one contract note per trading day, with charges at the usual delivery rates, so its sums hold
    notes = {}
    for day in sorted({t['d'] for t in trades}):
        tr = [t for t in trades if t['d'] == day]
        val = lambda side: sum(t['q'] * t['p'] for t in tr if t['t'] == side)
        turnover = val('buy') + val('sell')
        exch = round(turnover * 0.0000297, 2)
        ch = {'stt': -round(turnover * 0.001, 2), 'stamp': -round(val('buy') * 0.00015, 2), 'exchange': -exch,
              'sebi': -round(turnover * 0.000001, 2), 'igst': -round(exch * 0.18, 2), 'brokerage': 0.0}
        ob = round(val('sell') - val('buy'), 2)
        notes[f'demo-{day}'] = {'date': day, 'trades': tr, 'charges': ch, 'obligation': ob,
                                'net': round(ob + sum(ch.values()), 2), 'subject': f'Demo contract note {day}',
                                'message_id': f'demo-{day}'}
    json.dump(notes, open(os.path.join(out, '_contract_notes.json'), 'w'), indent=1)

    # baskets hold exactly what FIFO says is still open, at the open lots' cost: the tests demand it
    _, _, lots, _ = fifo([{k: t[k] for k in ('s', 'd', 't', 'q', 'p', 'tm')} for t in trades], log=lambda *a: None)
    last_sell = {t['s']: t['d'] for t in trades if t['t'] == 'sell'}
    batches = {}
    for tag, (label, start, syms) in BASKETS.items():
        held = {}
        for s in syms:
            q = sum(l[0] for l in lots.get(s, ()))
            if q > 1e-9:
                held[s] = {'q': q, 'avg': round(sum(l[0] * l[1] for l in lots[s]) / q, 4)}
        batches[tag] = {'label': label, 'tag': tag, 'executed': start, 'holdings': held,
                        'invested': round(sum(h['q'] * h['avg'] for h in held.values()), 2),
                        'membership': {s: {'from': start, 'to': None if s in held else last_sell[s]} for s in syms},
                        'ever_held': syms, 'exited': [s for s in syms if s not in held]}
    json.dump({'_note': 'A made-up portfolio, written by demo.py. None of it is anyone\'s real data.',
               'batches': batches}, open(os.path.join(out, '_smallcase_map.json'), 'w'), indent=1)
    return trades


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    out = os.path.abspath(args[0]) if args else tempfile.mkdtemp(prefix='netfolio-demo-')
    os.makedirs(out, exist_ok=True)
    try:   # copy the code only: the files git tracks, or every script when this isn't a git checkout
        code = subprocess.run(['git', 'ls-files', '*.py', '*.mjs'], cwd=HERE, capture_output=True, text=True,
                              check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        code = [os.path.basename(f) for f in glob.glob(os.path.join(HERE, '*.py')) + glob.glob(os.path.join(HERE, '*.mjs'))]
    for f in code:
        if '/' not in f:
            shutil.copy(os.path.join(HERE, f), out)
    trades = build(out)
    print(f'demo portfolio in {out}: {len(trades)} trades on {len({t["d"] for t in trades})} days, '
          f'{len({t["s"] for t in trades})} stocks in {len(BASKETS)} baskets')
    env = dict(os.environ, SHARECASE_REPO=os.path.abspath(SHARECASE))
    run = lambda *cmd: subprocess.run(cmd, cwd=out, env=env, check=True)
    run(sys.executable, '_snapshot.py')
    snap = max(glob.glob(os.path.join(out, '_holdings_*.json')), key=os.path.getmtime)
    run(sys.executable, '_daily.py')                 # before build_live, which embeds its curve
    run(sys.executable, 'build_live.py', snap)
    run(sys.executable, 'test_money.py')
    if '--no-serve' in sys.argv:
        return
    print(f'\nserving the demo on http://localhost:{PORT}/dashboard.html (Ctrl-C to stop)')
    os.execvpe('node', ['node', os.path.join(out, '_devserver.mjs')], dict(env, NETFOLIO_PORT=str(PORT)))


if __name__ == '__main__':
    main()
