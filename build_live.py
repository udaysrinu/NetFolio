#!/usr/bin/env python3
"""Build live.json for dashboard.html from the map, a Kite holdings snapshot, and the tradebooks.

    python3 build_live.py _holdings_25sep.json

Why the MAP supplies quantities, not Kite: Kite applies a day's net SELLS to holdings at once but a net
BUY only settles T+1, so on a day with both, get_holdings understates the real position. The snapshot
supplies PRICES, and every row is reconciled as map == kite settled + t1.

Why the trades are ADJUSTED: dashboard.html's own FIFO knows nothing about bonuses, splits or
demergers, so it receives tradebook.adjusted_trades() - test_money.py asserts a plain FIFO over that
list reproduces tradebook.fifo() exactly.

Why map FILLS are appended: a Zerodha export is T+1, so trades done after its last date are in no
tradebook. Their realised gain was in neither half of "Combined P&L" - realised stopped at the export,
while holdings were already the post-trade book. Appended only for dates AFTER the last tradebook
trade, so the moment a newer export lands they drop out and the real rows take over. No duplicates.

Private output - never commit live.json.
"""
import json, os, sys
from collections import defaultdict
from tradebook import load_all, adjusted_trades, base, map_fills

HERE = os.path.dirname(os.path.abspath(__file__))
P = lambda n: os.path.join(HERE, n)


def _daily_rows():
    """The dashboard's portfolio-vs-Nifty curve. Empty unless _daily_perf.csv exists (python3 _daily.py)."""
    import csv
    f = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_daily_perf.csv')
    if not os.path.exists(f):
        return []
    return [{'d': r['date'], 'v': float(r['value']), 'twr': float(r['twr_pct']), 'nifty': float(r['nifty_pct'])}
            for r in csv.DictReader(open(f))]


def main(snap_path):
    snap = json.load(open(P(snap_path) if not os.path.isabs(snap_path) else snap_path))
    asof, K = snap['asof'], snap['holdings']
    M = json.load(open(P('_smallcase_map.json')))
    alias = M.get('symbol_aliases', {})
    norm = lambda s: alias.get(s, alias.get(base(s), base(s)))

    # ---- positions from the map, one row per symbol
    agg = defaultdict(lambda: [0.0, 0.0])
    for b in M['batches'].values():
        for s, h in b['holdings'].items():
            a = agg[base(s)]; a[0] += h['q']; a[1] += h['q'] * h['avg']

    # ---- fills the tradebook cannot contain yet, from the map's own rebalance records
    trades, _, _ = load_all(verbose=False)
    tb_last = max(t['d'] for t in trades)
    fills = [dict(f, oid=f['id']) for f in map_fills(M, tb_last, norm)]

    # shares bought ON the snapshot date: their day move runs from the FILL, not yesterday's close
    bought_today = defaultdict(lambda: [0.0, 0.0])
    for f in fills:
        if f['d'] == asof and f['t'] == 'buy':
            a = bought_today[f['s']]; a[0] += f['q']; a[1] += f['q'] * f['p']

    rows, bad = [], []
    for s, (q, cost) in sorted(agg.items()):
        k = K.get(s)
        if not k:
            bad.append(f'{s}: in the map, not in the snapshot')
            continue
        if abs(k['q'] - q) > 1e-6:
            bad.append(f'{s}: map {q:g} vs Kite {k["q"]:g}')
        ltp, close, avg = k['ltp'], k['close'], cost / q
        bq, bc = bought_today.get(s, (0.0, 0.0))
        dpnl = (q - bq) * (ltp - close) + (bq * ltp - bc)
        rows.append({'s': s, 'q': q, 'avg': round(avg, 4), 'ltp': ltp, 'close': close,
                     'pnl': round(q * (ltp - avg), 2), 'dpnl': round(dpnl, 2),
                     'day': round((ltp / close - 1) * 100, 2) if close else 0.0})
    for s in sorted(set(K) - set(agg)):
        bad.append(f'{s}: held at Kite, in no basket')
    if bad:
        print('DID NOT RECONCILE - live.json not written:')
        for b in bad:
            print('   ' + b)
        sys.exit(1)

    adj = adjusted_trades(trades, M.get('corporate_actions', []), norm=norm)
    tr_rows = [{'sym': t['s'], 'date': t['d'], 'type': t['t'], 'qty': float(t['q']), 'price': float(t['p']),
                'time': t.get('tm') or t['d']} for t in adj]
    # order ids are fixed-width and monotonic within a day, so they order same-day fills correctly
    tr_rows += [{'sym': f['s'], 'date': f['d'], 'type': f['t'], 'qty': f['q'], 'price': f['p'],
                 'time': f['d'] + 'T~' + f['oid']} for f in sorted(fills, key=lambda f: (f['d'], f['oid']))]

    iv = sum(r['q'] * r['avg'] for r in rows); mk = sum(r['q'] * r['ltp'] for r in rows)
    out = {
        '_note': 'Built by build_live.py. PRIVATE - never commit. Quantities from _smallcase_map.json, prices '
                 f'from {os.path.basename(snap_path)}. Every row reconciles map == Kite settled + t1.',
        'asof': asof, 'tradebook_last': tb_last, 'holdings': rows,
        'totals': {'invested': round(iv, 2), 'market': round(mk, 2), 'unrealised': round(mk - iv, 2),
                   'day_pnl': round(sum(r['dpnl'] for r in rows), 2), 'holdings': len(rows)},
        '_trades': tr_rows,
        # Daily performance from _daily.py (value, time-weighted return, Nifty), when it has been run.
        '_daily': _daily_rows(),
        '_trades_note': f'{len(trades)} tradebook trades to {tb_last}, {len(adj) - len(trades)} synthetic '
                        f'corporate-action rows, and {len(fills)} map fills dated after {tb_last}.',
    }
    json.dump(out, open(P('live.json'), 'w'), indent=1)
    print(f'live.json: {len(rows)} holdings, asof {asof}, invested {iv:,.0f}, market {mk:,.0f}, '
          f'day {out["totals"]["day_pnl"]:+,.0f}')
    print(f'  _trades: {len(trades)} tradebook + {len(adj) - len(trades)} corporate-action + '
          f'{len(fills)} fills after {tb_last}')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else '_holdings.json')
