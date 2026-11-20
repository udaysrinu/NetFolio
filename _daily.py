#!/usr/bin/env python3
"""Daily portfolio performance, rebuilt from every trade and daily closes (Yahoo v8, server-side).

    python3 _daily.py [YYYY-MM-DD start]     -> _daily_perf.csv + a summary; default start: the first trade

  value_t   sum of shares held at the close x that day's close
  flow_t    rupees put in that day: buys - sells, at the traded prices
  pnl_t     value_t - value_{t-1} - flow_t        (cash-flow adjusted, so deposits are not "profit")
  ret_t     pnl_t / (value_{t-1} + flow_t / 2)    (Modified Dietz; a day's flows count half)
  twr       product of (1 + ret_t) - 1            (time-weighted: what the stock picking earned)

Yahoo closes are back-adjusted for splits and bonuses, so quantities are put on the same basis: every
share bought before a split event counts ratio times. Without that a bonus reads as a 67% one-day crash.
Demerger children and credits come from the map, at zero cash flow. Dividends are not included, since
Kite exposes none, so returns are understated by whatever was paid.
"""
import json, sys, csv, datetime as dt, urllib.request, io, contextlib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
with contextlib.redirect_stdout(io.StringIO()):
    from tradebook import load_all, map_fills, base
    TR, _, _ = load_all()

START = sys.argv[1] if len(sys.argv) > 1 else min(t['d'] for t in TR)
M = json.load(open('_smallcase_map.json'))
ALIAS = M.get('symbol_aliases', {})
norm = lambda s: ALIAS.get(s, ALIAS.get(base(s), base(s)))
trades = [dict(t, s=norm(t['s'])) for t in TR] + map_fills(M, max(t['d'] for t in TR), norm)
# Shares with no known cost (e.g. an IPO allotment that appears in no export) are left out of the daily
# curve entirely, as the FIFO leaves them out of realised. Keeping their SALES alone made a phantom short
# position that moved with the stock's price and leaked fake P&L into the curve. Derive or abstain.
UNCOSTED = {norm(k) for k in M.get('known_uncosted', {})}
trades = [t for t in trades if t['s'] not in UNCOSTED]
for a in M.get('corporate_actions', []):        # shares that arrive with no trade and no cash
    if a['type'] == 'credit':
        trades.append({'s': norm(a['s']), 'd': a['effective'], 't': 'buy', 'q': float(a['qty']), 'p': 0.0,
                       'flow': False})
    elif a['type'] == 'demerger':
        rec = a.get('record') or a['effective']
        held = sum(t['q'] * (1 if t['t'] == 'buy' else -1) for t in trades
                   if t['s'] == norm(a['s']) and t['d'] <= rec)
        trades.append({'s': norm(a['child']), 'd': rec, 't': 'buy', 'q': held * float(a['ratio']), 'p': 0.0,
                       'flow': False})

END = dt.date.today()
# PRICE LOOKUP ONLY, from the private map (`price_aliases`, {"OLDNAME": "NEWTICKER"}): symbols that trade
# under an old name in the tradebook while Yahoo files the whole history under a new ticker (a rename, or
# a demerger's continuing listing). Each is CHECKED against our own trades below, and rescaled when
# Yahoo back-adjusted it.
PRICE_ALIAS = M.get('price_aliases', {})
# Instruments with no exchange series at all (`no_price_series`, e.g. a rights entitlement): valued at
# their own traded price, the only market evidence there is - never at 0, which fakes a gain on sale day.
NO_SERIES = set(M.get('no_price_series', []))
# A demerged child exists from the record date but only lists weeks later. Until it lists it is valued at
# its FIRST listed close: unobservable before that, and 0 would book a fake loss on the record date and a
# fake gain on listing day.
CHILDREN = {norm(a['child']) for a in M.get('corporate_actions', []) if a['type'] == 'demerger'}
def yahoo(sym, a, b):
    p1 = int(dt.datetime.combine(a, dt.time()).timestamp()); p2 = int(dt.datetime.combine(b, dt.time(23)).timestamp())
    u = f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1={p1}&period2={p2}&interval=1d&events=split'
    try:
        j = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'}),
                                             timeout=20))['chart']['result'][0]
    except Exception:
        return None
    c = {dt.date.fromtimestamp(t).isoformat(): v for t, v in zip(j.get('timestamp', []),
                                                               j['indicators']['quote'][0]['close']) if v}
    ev = [(dt.date.fromtimestamp(e['date']).isoformat(), e['numerator'] / e['denominator'])
          for e in (j.get('events', {}).get('splits') or {}).values()]
    return c, ev

s0 = dt.date.fromisoformat(START)
def held_at(s, d):
    return sum(t['q'] * (1 if t['t'] == 'buy' else -1) for t in trades if t['s'] == s and t['d'] <= d)
# every symbol held at the start or traded in the window
syms = sorted({t['s'] for t in trades if t['d'] >= START} | {s for s in {t['s'] for t in trades}
                                                            if held_at(s, START) > 1e-9})
fetch = ['^NSEI', '^CNXSC'] + [s + '.NS' for s in syms]
def one(s):     # NSE and BSE merged per DAY: a counter can list on one exchange months after the other
    a = s0 - dt.timedelta(days=10)
    if s.endswith('.NS') and s[:-3] in NO_SERIES:
        return {}, []
    q = PRICE_ALIAS.get(s[:-3], s[:-3]) + '.NS' if s.endswith('.NS') else s
    ns = yahoo(q, a, END) or ({}, [])
    if not s.endswith('.NS'):
        return ns
    bo = yahoo(q[:-3] + '.BO', a, END) or ({}, [])
    return {**bo[0], **ns[0]}, (ns[1] if len(ns[1]) >= len(bo[1]) else bo[1])
with ThreadPoolExecutor(16) as ex:
    got = dict(zip(fetch, ex.map(one, fetch)))
missing = [s for s in fetch if (not got[s] or not got[s][0]) and s[:-3] not in NO_SERIES]
# An aliased history must price the OLD symbol. Compare Yahoo's close with our own traded price on every
# trade date. One renamed stock's older closes sat ~11% below what it actually traded at (a rights
# adjustment Yahoo applies but does not publish as an event), so derive the scale from our trades
# (median price/close), apply it, and refuse to run if any trade still disagrees by more than 6%.
for old in PRICE_ALIAS:
    k = old + '.NS'
    if k not in got or not got[k][0]:
        continue
    c = got[k][0]; keys = sorted(c)
    pts = [(t['p'], c[max(d for d in keys if d <= t['d'])]) for t in trades
           if t['s'] == old and t['p'] > 0 and keys[0] <= t['d']]
    if not pts:
        continue
    r = sorted(p_ / c_ for p_, c_ in pts); scale = r[len(r) // 2]
    if abs(scale - 1) < 0.03:
        scale = 1.0
    worst = max(abs(p_ / (c_ * scale) - 1) for p_, c_ in pts)
    if worst > 0.06:
        sys.exit(f'{old} -> {PRICE_ALIAS[old]}: closes disagree with our trades by {worst:.0%} - wrong alias?')
    if scale != 1.0:
        got[k] = ({d: v * scale for d, v in c.items()}, got[k][1])
    print(f'  alias {old} -> {PRICE_ALIAS[old]}: {len(pts)} trades, scale {scale:.4f}, worst gap {worst:.1%}')
if missing:
    sys.exit(f'no Yahoo prices for {missing} - add an alias or extend the map before trusting any figure')

days = sorted(d for d in got['^NSEI'][0] if d >= START)
factor = lambda s, d: __import__('math').prod(r for e, r in got[s + '.NS'][1] if e > d)
qadj = defaultdict(float); flow = defaultdict(float); last = {}
by_day = defaultdict(list)
for t in trades:
    by_day[t['d']].append(t)
for t in trades:                                     # opening book: everything before the first day
    if t['d'] < days[0] and t['s'] in syms:          # anything else was fully sold before the window
        qadj[t['s']] += t['q'] * (1 if t['t'] == 'buy' else -1) * factor(t['s'], t['d'])

def close(s, d):
    c = got[s + '.NS'][0]
    if d in c:
        last[s] = c[d]
    elif s not in last:
        prior = [k for k in c if k <= d]
        if prior:
            last[s] = c[max(prior)]
        else:   # no market close yet on either exchange: mark at our own last traded price, never at 0
            px = [t['p'] * 1 for t in trades if t['s'] == s and t['d'] <= d and t['p'] > 0]
            if not px and s in NO_SERIES:            # credited at nil cost: its sale price is the evidence
                px = [t['p'] for t in trades if t['s'] == s and t['p'] > 0][:1]
            if not px and s in CHILDREN and c:      # not listed yet: its first listed close
                last[s] = c[min(c)]
                return last[s]
            if not px:
                sys.exit(f'no price for {s} on or before {d} - refusing to value it at 0')
            last[s] = px[-1] / factor(s, d)
    return last[s]

pre = [d for d in got['^NSEI'][0] if d < days[0]][-1]
V_prev = sum(q * close(s, pre) for s, q in qadj.items() if abs(q) > 1e-9)
_sc = got['^CNXSC'][0]
rows, twr, n0 = [], 1.0, got['^NSEI'][0][pre]
s0c = _sc[max(k for k in _sc if k <= pre)] if any(k <= pre for k in _sc) else None
pv = {}; by_sym = defaultdict(float)
for d in days:
    f = 0.0; fs = defaultdict(float)
    for dd in [k for k in by_day if (rows[-1]['date'] if rows else pre) < k <= d]:   # trades since last row
        for t in by_day[dd]:
            sign = 1 if t['t'] == 'buy' else -1
            qadj[t['s']] += sign * t['q'] * factor(t['s'], t['d'])
            if t.get('flow', True):
                f += sign * t['q'] * t['p']; fs[t['s']] += sign * t['q'] * t['p']
    vals = {s: q * close(s, d) for s, q in qadj.items() if abs(q) > 1e-9}
    V = sum(vals.values())
    # each stock's own P&L today, so a flagged day can name what moved it
    move = {s: vals.get(s, 0) - pv.get(s, 0) - fs.get(s, 0) for s in set(vals) | set(pv) | set(fs)}
    pv = vals
    for k_, v_ in move.items(): by_sym[k_] += v_
    pnl = V - V_prev - f
    den = V_prev + f / 2
    r = pnl / den if den > 0 else 0.0
    twr *= 1 + r
    sc = got['^CNXSC'][0].get(d)
    rows.append({'date': d, 'value': round(V), 'flow': round(f), 'pnl': round(pnl), 'ret_pct': round(r * 100, 3),
                 'twr_pct': round((twr - 1) * 100, 2),
                 'nifty_pct': round((got['^NSEI'][0][d] / n0 - 1) * 100, 2),
                 'smallcap_pct': round((sc / s0c - 1) * 100, 2) if sc and s0c else '',
                 '_top': sorted(move.items(), key=lambda kv: -abs(kv[1]))[:3], '_V0': V_prev})
    V_prev = V

# A bad price does not break any identity (the FIFO cross-check in test_money.py balances whatever the
# prices are), so eyeball the outliers: a whole-portfolio move this big is either a crash or a data hole.
for r in rows:
    if abs(r['ret_pct']) > 6:
        top = ', '.join(f"{s} {v:+,.0f}" for s, v in r['_top'])
        print(f"  CHECK {r['date']}: portfolio {r['ret_pct']:+.1f}% on Rs {r['_V0']:,.0f} - moved by {top}")
json.dump({k: {'pnl': round(v, 2), 'q': round(qadj.get(k, 0), 4), 'v': round(pv.get(k, 0), 2)} for k, v in by_sym.items()},
          open('_daily_by_symbol.json', 'w'))   # per-stock P&L, for test_money's identity
with open('_daily_perf.csv', 'w', newline='') as fh:
    w = csv.DictWriter(fh, fieldnames=[k for k in rows[0] if not k.startswith('_')], extrasaction='ignore')
    w.writeheader(); w.writerows(rows)
print(json.dumps({'start': days[0], 'end': days[-1], 'days': len(rows), 'symbols': len(syms),
                  'value_end': rows[-1]['value'], 'pnl_total': sum(r['pnl'] for r in rows),
                  'flow_total': sum(r['flow'] for r in rows), 'twr_pct': rows[-1]['twr_pct'],
                  'nifty_pct': rows[-1]['nifty_pct'], 'smallcap_pct': rows[-1]['smallcap_pct']}))
