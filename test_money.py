#!/usr/bin/env python3
"""Invariants that must hold for every money figure this project reports.

Not unit tests of functions - assertions about the DATA, which is where every bug here has actually
lived. Each check corresponds to a class of error that already shipped a plausible wrong number at
least once. A check that cannot fail teaches nothing, so each one states what it would catch.

Run:  python3 test_money.py          (exits non-zero on the first failure, prints every result)
"""
import json, os, re, sys
from collections import defaultdict, deque

HERE = os.path.dirname(os.path.abspath(__file__))
P = lambda n: os.path.join(HERE, n)
# These are PRIVATE checks: they assert invariants on one account's real data (tradebooks, the basket
# map, live.json), which never leave that machine. Without the data there is nothing to check here; the
# public tests on synthetic trades are tests/run.py.
if not os.path.exists(P('_smallcase_map.json')):
    print('private data not present (no _smallcase_map.json) - skipping. Public tests: python3 tests/run.py')
    sys.exit(0)
from tradebook import load_all, base

FAIL = []
def check(name, ok, detail=''):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}{('  -> ' + detail) if detail else ''}")
    if not ok:
        FAIL.append(name)
    return ok

def rup(v):
    n = f'{abs(round(v)):d}'
    if len(n) > 3:
        h, t = n[:-3], n[-3:]
        parts = []
        while len(h) > 2:
            parts.insert(0, h[-2:]); h = h[:-2]
        if h:
            parts.insert(0, h)
        n = ','.join(parts + [t])
    return ('-' if v < 0 else '') + n


TRADES, FILES, _ = load_all()
MAP = json.load(open(P('_smallcase_map.json')))


# ---------------------------------------------------------------- 1. the trade list itself
print('\n1. TRADE LIST')

# Would catch: a dedupe key that collapses two genuinely different trades into one.
sig = lambda t: (t['s'], t['d'], t['t'], t['q'], t['p'], str(t.get('id', '')), str(t.get('x', '')))
check('no two trades share a full signature',
      len({sig(t) for t in TRADES}) == len(TRADES),
      f'{len(TRADES)} trades, {len({sig(t) for t in TRADES})} distinct')

# Would catch: a loader that silently drops the date, making every date-gated figure meaningless.
undated = [t for t in TRADES if not re.match(r'^\d{4}-\d{2}-\d{2}$', str(t['d']))]
check('every trade carries an ISO date', not undated, f'{len(undated)} undated')

# Would catch: sign confusion. A sell must never arrive already negated - FIFO negates it itself.
check('all quantities and prices are positive',
      all(t['q'] > 0 and t['p'] > 0 for t in TRADES),
      f"{sum(1 for t in TRADES if t['q'] <= 0 or t['p'] <= 0)} bad rows")

check('trade types are only buy/sell',
      {t['t'] for t in TRADES} <= {'buy', 'sell'},
      str(sorted({t['t'] for t in TRADES})))

# Would catch: an unsorted list. FIFO over unsorted trades matches a sell against a later buy.
check('trades are date-sorted', all(TRADES[i]['d'] <= TRADES[i+1]['d'] for i in range(len(TRADES)-1)))


# ---------------------------------------------------------------- 2. FIFO share conservation
print('\n2. FIFO')

# The REAL implementation, imported - not a re-implementation. An earlier version of this file carried
# its own FIFO, so a bug shared by both copies passed silently and the test partly tested itself. The
# checks below are INVARIANTS over its output (share conservation, pnl identity, open lots vs held),
# which stay independent of the code under test even though they now exercise it.
from tradebook import fifo
_ALIAS0 = MAP.get('symbol_aliases', {})
_n0 = lambda s: _ALIAS0.get(s, _ALIAS0.get(base(s), base(s)))
matched, uncosted, lots, ca_added = fifo(TRADES, MAP.get('corporate_actions', []), norm=_n0,
                                         log=lambda m: print('     ' + m))
lots = defaultdict(deque, lots)
ca_added = defaultdict(float, ca_added)

# Would catch: the browser seeing different money from Python. dashboard.html runs its own FIFO with no
# notion of corporate actions, so it is handed adjusted_trades() instead - splits folded into history,
# bonuses and credits as synthetic buys. That is only safe if a PLAIN FIFO over that list reproduces
# fifo() exactly, so assert it, on realised, uncosted AND every open lot.
from tradebook import adjusted_trades
_m2, _u2, _l2, _ = fifo(adjusted_trades(TRADES, MAP.get('corporate_actions', []), norm=_n0),
                        log=lambda m: None)
_r1, _r2 = sum(x['pnl'] for x in matched), sum(x['pnl'] for x in _m2)
check('plain FIFO over adjusted_trades() == fifo() with actions (realised)', abs(_r1 - _r2) < 0.01,
      f'{rup(_r1)} vs {rup(_r2)}')
check('... and uncosted', abs(sum(u['proceeds'] for u in uncosted) - sum(u['proceeds'] for u in _u2)) < 0.01)
_o1 = {k: round(sum(l[0] for l in d), 6) for k, d in lots.items() if d}
_o2 = {k: round(sum(l[0] for l in d), 6) for k, d in _l2.items() if d}
check('... and every open lot', _o1 == _o2, str(set(_o1.items()) ^ set(_o2.items()))[:120])

# Would catch: a corporate action missing from the map. Once export coverage is continuous, a
# sell with NO lot behind it is no longer "an older export we lack" - it is a bonus, rights credit or
# IPO allotment the FIFO was never told about. A 1:1 bonus hid as exactly this for weeks,
# booking a -19,125 loss on a sale that made +12,214, because nothing failed on uncosted sells.
_known = MAP.get('known_uncosted', {})
_unexp = sorted({u['s'] for u in uncosted} - set(_known))
check('no UNEXPLAINED uncosted sell (each needs a corporate action or a known_uncosted entry)',
      not _unexp, str(_unexp))
for s_ in sorted({u['s'] for u in uncosted} & set(_known)):
    print(f"      note: {s_} uncosted, acknowledged - {_known[s_][:70]}")

# Would catch: a partial-lot bug that loses or duplicates shares - the most dangerous class here,
# because the P&L stays plausible while the share count silently drifts.
bought = defaultdict(float); sold = defaultdict(float)
for t in TRADES:
    (bought if t['t'] == 'buy' else sold)[t['s']] += t['q']
openq = {s: sum(l[0] for l in dq) for s, dq in lots.items()}
consumed = defaultdict(float)
for m in matched:
    consumed[m['s']] += m['q']
unc = defaultdict(float)
for u in uncosted:
    unc[u['s']] += u['q']
# `+ ca_added`: a bonus creates shares nobody bought and a split multiplies them, so the naive
# identity bought == matched + open is FALSE once corporate actions are applied - and it broke the
# moment they were, which is the check doing its job rather than a regression.
bad = [(s, bought[s], ca_added.get(s, 0), sold[s], openq.get(s, 0), consumed[s], unc[s])
       for s in set(bought) | set(sold)
       if abs(bought[s] + ca_added.get(s, 0) - consumed[s] - openq.get(s, 0)) > 1e-6
       or abs(sold[s] - consumed[s] - unc[s]) > 1e-6]
check('shares conserve per symbol (bought + corporate actions = matched + open; '
      'sold = matched + uncosted)', not bad, f'{len(bad)} symbols off: {bad[:3]}')

# Would catch: cost created or destroyed - e.g. netting a day at an average that does not reproduce the
# day's buy value, or a corporate action that moves cost between lots without preserving it. Every
# rupee spent on buys (plus credits at their stated price; bonuses are nil-cost, splits and demergers
# preserve cost) must be either matched or still open. Share conservation alone cannot see a price bug.
_buy_cost = sum(t['q'] * t['p'] for t in TRADES if t['t'] == 'buy') + sum(
    float(a['qty']) * float(a.get('price', 0)) for a in MAP.get('corporate_actions', []) if a['type'] == 'credit')
_out_cost = sum(m['cost'] for m in matched) + sum(q * p for d in lots.values() for q, p, _d in d)
check('cost conserves: buys + credits == matched cost + open-lot cost', abs(_buy_cost - _out_cost) < 1,
      f'{rup(_buy_cost)} vs {rup(_out_cost)}')

# Would catch: cost/proceeds drifting apart from pnl, i.e. a lot priced with the wrong number.
drift = [m for m in matched if abs((m['proceeds'] - m['cost']) - m['pnl']) > 0.01]
check('pnl == proceeds - cost on every matched slice', not drift, f'{len(drift)} rows')

# Would catch: a same-day ordering dependency. If a symbol buys and sells on one day, FIFO's answer
# depends on their order, so the loader must sort by actual execution time.
sameday = defaultdict(lambda: {'buy': 0, 'sell': 0})
for t in TRADES:
    sameday[(t['s'], t['d'])][t['t']] += t['q']
risky = [k for k, v in sameday.items() if v['buy'] and v['sell']]
print(f'      note: {len(risky)} (symbol, day) pairs have BOTH a buy and a sell: {risky[:4]}')

# Would catch: sorting a day's fills by anything other than the clock. Codex found this one - the key
# was (date, buys-first, EXCHANGE, id), so BSE sorted before NSE whatever time it traded, and one
# sale ate the wrong lots. Both implementations now sort on Order Execution Time.
check('every trade carries an execution time', all(t.get('tm') for t in TRADES),
      f"{sum(1 for t in TRADES if not t.get('tm'))} missing")
check('trades are sorted chronologically, not by exchange',
      all((TRADES[i]['d'], TRADES[i].get('tm', '')) <= (TRADES[i+1]['d'], TRADES[i+1].get('tm', ''))
          for i in range(len(TRADES) - 1)))
# Would catch: a day where the exchange-first key and the clock disagree - the exact bug shape.
mixed = {k for k, v in defaultdict(set, {}).items()}
byday = defaultdict(list)
for t in TRADES:
    byday[(t['s'], t['d'])].append(t)
crossx = [k for k, v in byday.items() if len({t['x'] for t in v}) > 1]
print(f'      note: {len(crossx)} (symbol, day) pairs traded on BOTH exchanges - where the old '
      f'exchange-first sort could pick the wrong lot: {crossx[:4]}')

# ---- corporate actions: FIFO sees only TRADES, but a bonus or split changes quantity with no trade
# row. So FIFO's open lots can disagree with the shares actually held, and every per-share figure
# built on them is then wrong by the action's ratio. Codex found this; my earlier share-conservation
# check could not, because it only tested the trade list against ITSELF.
print('\n2b. FIFO OPEN LOTS vs SHARES ACTUALLY HELD')
held = defaultdict(float)
for bt in MAP['batches'].values():
    for s, h in bt['holdings'].items():
        held[base(s)] += h['q']
ALIAS = MAP.get('symbol_aliases', {})
nrm = lambda s: ALIAS.get(s, ALIAS.get(base(s), base(s)))
held_n = defaultdict(float)
for bt in MAP['batches'].values():
    for s, h in bt['holdings'].items():
        held_n[nrm(s)] += h['q']
ca_known = MAP.get('corporate_action_notes', {})   # splits/bonuses known to change a share count (private map)
# Trades done AFTER the newest export are legitimately absent from FIFO. Read their net share delta
# out of the map's own fill records so the comparison is against what FIFO COULD have known.
TB_LAST = max(t['d'] for t in TRADES)
post = defaultdict(float)
for bt in MAP['batches'].values():
    for rk in [k for k in bt if k.startswith(('rebalance_', 'topup_', 'exit_'))]:
        rec = bt[rk]
        # Every record carries an explicit `date`. Guessing it defaulted one rebalance
        # to "today", counted fills the export already holds, and produced nine phantom gaps that
        # looked exactly like corporate actions.
        d = rec.get('date')
        check(f'{bt.get("tag", "?")}.{rk} carries a date', bool(d))
        if not d or d <= TB_LAST:
            continue                    # already inside the tradebook, so FIFO has it
        for s, f in (rec.get('fills') or {}).items():
            if isinstance(f, dict) and 'qty' in f:
                post[nrm(s)] += f['qty'] if f.get('side', 'BUY') == 'BUY' else -f['qty']
gap = []
for s, q in sorted(held_n.items()):
    fq = sum(l[0] for l in lots.get(s, []))
    expect = fq + post.get(s, 0.0)
    if abs(expect - q) > 1e-6:
        gap.append((s, fq, post.get(s, 0.0), q, ca_known.get(s, 'UNEXPLAINED')))
for s, fq, pd, q, why in gap:
    print(f'      {s:<13} FIFO open {fq:>7.0f} + post-export {pd:>+6.0f} = {fq+pd:>7.0f}   '
          f'held {q:>7.0f}   {why}')
KNOWN = set(MAP.get('known_discrepancies', {}))
for s, fq, pd, q, why in gap:
    if s in KNOWN:
        print(f'      {s:<13} ^^ acknowledged open question, see map.known_discrepancies')
# Fail on anything NOT already acknowledged. A suite that always fails gets ignored; one that hides
# a gap is worse. So known gaps stay visible in the output but do not break the build.
unexplained = [g for g in gap if g[4] == 'UNEXPLAINED' and g[0] not in KNOWN]
check('every FIFO-vs-held gap is an explained corporate action or an acknowledged open question',
      not unexplained, str([(g[0], g[1] + g[2], g[3]) for g in unexplained]))
pass  # corporate actions are now applied by tradebook.fifo, so held == open lots is asserted above


# ---------------------------------------------------------------- 2c. cost basis
# Realised is FIFO; unrealised is held quantity x the map's avg. Those two books only add up if the
# map's cost of what is still held IS the cost of FIFO's open lots - otherwise a lot's gain is counted
# once as realised and again as unrealised. Every check above was on QUANTITY; until 13 Nov 2026 cost
# had no check at all, and a dozen symbols had drifted: some kept a blended avg after a FIFO partial
# sale, others were bought and sold on one day, which Zerodha nets as intraday and plain FIFO did not.
print('\n2c. COST BASIS (map avg == FIFO open lots == Kite)')
from tradebook import map_fills

# Would catch: same-day trades run through plain FIFO. Synthetic, so the expected answer is by hand.
_s = [{'s': 'X', 'd': '2026-01-01', 't': 'buy', 'q': 10, 'p': 100},
      {'s': 'X', 'd': '2026-01-02', 't': 'buy', 'q': 5, 'p': 120},     # same day as the sell below
      {'s': 'X', 'd': '2026-01-02', 't': 'sell', 'q': 8, 'p': 130},
      {'s': 'X', 'd': '2026-01-03', 't': 'buy', 'q': 4, 'p': 110},
      {'s': 'X', 'd': '2026-01-03', 't': 'sell', 'q': 1, 'p': 115}]
_m, _u, _l, _ = fifo(_s, log=lambda m: None)
_intra = sum(x['pnl'] for x in _m if x.get('intraday'))
_deliv = sum(x['pnl'] for x in _m if not x.get('intraday'))
_open = [(round(q, 6), round(p, 6)) for q, p, _d in _l['X']]
check('same-day buy+sell nets as intraday (5 x 10 + 1 x 5 = 55), only the 3-share remainder is FIFO '
      '(3 x 30 = 90), open lots 7@100 + 3@110',
      abs(_intra - 55) < 1e-9 and abs(_deliv - 90) < 1e-9 and _open == [(7, 100), (3, 110)] and not _u,
      f'intraday {_intra}, delivery {_deliv}, open {_open}')

# The full book: tradebooks + the map's fills for days no export covers yet, exactly as attribute_sells.
_all = sorted(TRADES + map_fills(MAP, TB_LAST, _n0),
              key=lambda x: (x['d'], x.get('tm', ''), x['t'] != 'buy', x['x'], str(x['id'])))
_, _, LOTS, _ = fifo(_all, MAP.get('corporate_actions', []), norm=_n0, log=lambda m: None)
fifo_cost = {s: sum(q * p for q, p, _d in d) for s, d in LOTS.items()}
map_cost = defaultdict(float)
for bt in MAP['batches'].values():
    for s, h in bt['holdings'].items():
        map_cost[_n0(s)] += h['q'] * h['avg']

# Would catch: a partial sale recorded by lowering q and keeping the blended avg - the lot FIFO just
# sold stays in unrealised at its old cost, so its gain is double-counted.
off = [(s, round(c - fifo_cost.get(s, 0))) for s, c in sorted(map_cost.items())
       if abs(c - fifo_cost.get(s, 0)) > 2]
check('map cost of every holding == FIFO open-lot cost (within Rs 2)', not off, str(off)[:200])

# Would catch: OUR FIFO disagreeing with the broker's - the same-day netting bug, a missed corporate
# action, a wrong map fill price. Against the newest Kite average snapshot; skipped if none exists.
_ks = sorted((f for f in os.listdir(HERE) if f.startswith('_kite_avg_') and f.endswith('.json')),
             key=lambda f: json.load(open(P(f)))['asof'])
_last_trade = _all[-1]['d']
if _ks and json.load(open(P(_ks[-1])))['asof'] < _last_trade:
    print(f'      note: newest Kite snapshot {_ks[-1]} predates the last trade {_last_trade} - broker '
          f'cost check skipped until a fresh one is saved')
elif _ks:
    K = json.load(open(P(_ks[-1])))
    kc = {_n0(s): q * a for s, (q, a) in K['avg'].items()}
    koff = [(s, round(fifo_cost.get(s, 0) - c)) for s, c in sorted(kc.items())
            if abs(fifo_cost.get(s, 0) - c) > 2]
    check(f'FIFO open-lot cost == Kite average x qty on every holding ({_ks[-1]}, within Rs 2)',
          not koff, str(koff)[:200])
else:
    print('      note: no _kite_avg_*.json snapshot - broker cost check skipped')


# ---------------------------------------------------------------- 3. membership windows
print('\n3. MEMBERSHIP WINDOWS')

windows = defaultdict(list)
for key, bt in MAP['batches'].items():
    tag = bt.get('tag', key)
    mem = bt.get('membership')
    check(f'{tag} has a membership block', mem is not None)
    for s, w in (mem or {}).items():
        windows[base(s)].append((tag, w.get('from'), w.get('to')))

# Would catch: a from/to entered backwards, which would make the window empty and silently move
# every sale in it to UNKNOWN.
inverted = [(s, t, f, to) for s, ws in windows.items() for t, f, to in ws if f and to and f > to]
check('no window has from > to', not inverted, str(inverted))

# Would catch: a hand-entered `to` that predates a real sale - the sale would fall outside the window
# and lose its basket. This is the exact bug class that once hid a basket's realised loss.
sells_by_sym = defaultdict(list)
for t in TRADES:
    if t['t'] == 'sell':
        sells_by_sym[t['s']].append(t['d'])
orphan = []
for s, ws in windows.items():
    for d in sells_by_sym.get(s, []):
        if not any((f is None or d >= f) and (to is None or d <= to) for _, f, to in ws):
            orphan.append((s, d))
print(f'      note: {len(orphan)} (symbol, sell date) pairs fall outside EVERY window for that '
      f'symbol - expected for pre-basket trading, suspicious otherwise: {sorted(set(orphan))[:5]}')

# Would catch: a `from` later than the basket's own first buy of that symbol, which would orphan the
# opening purchase.
first_buy = {}
for t in TRADES:
    if t['t'] == 'buy' and t['s'] not in first_buy:
        first_buy[t['s']] = t['d']
late = [(s, t, f, first_buy[s]) for s, ws in windows.items() for t, f, _ in ws
        if f and s in first_buy and first_buy[s] < f]
print(f'      note: {len(late)} windows open AFTER the symbol was first ever bought - legitimate '
      f'when another basket bought it first: {late[:4]}')


# ---------------------------------------------------------------- 4. attribution completeness
print('\n4. ATTRIBUTION')

start, undateable = {}, set()
for key, bt in MAP['batches'].items():
    tag = bt.get('tag', key)
    m = re.match(r'(\d{4}-\d{2}-\d{2})', str(bt.get('executed', '')))
    if m:
        start[tag] = min(start.get(tag, m.group(1)), m.group(1))
    else:
        undateable.add(tag)

def held_on(sym, day):
    return {tag for tag, f, to in windows.get(sym, ())
            if (f is None or day >= f) and (to is None or day <= to)}

buckets = defaultdict(float)
for m in matched:
    o = held_on(m['s'], m['d'])
    key = 'UNKNOWN' if not o else (next(iter(o)) if len(o) == 1 else 'AMBIGUOUS')
    if len(o) == 1 and key not in undateable and key in start and m['d'] < start[key]:
        key = 'PRE-BASKET'
    if not o and any(f and m['d'] < f for _, f, _ in windows.get(m['s'], ())):
        key = 'PRE-BASKET'
    buckets[key] += m['pnl']

# Would catch: a rupee landing in two buckets or none - the selfcheck attribute_sells.py relies on.
total = sum(m['pnl'] for m in matched)
check('every costed rupee lands in exactly one bucket',
      abs(total - sum(buckets.values())) < 0.01,
      f"matched {rup(total)} vs buckets {rup(sum(buckets.values()))}")

# Would catch: a basket credited with a sale of a symbol it never held.
for tag, v in buckets.items():
    if tag in ('UNKNOWN', 'AMBIGUOUS', 'PRE-BASKET'):
        continue
    syms = {m['s'] for m in matched if held_on(m['s'], m['d']) == {tag}}
    ever = {base(s) for s in MAP['batches'][
        next(k for k, b in MAP['batches'].items() if b.get('tag', k) == tag)].get('membership', {})}
    check(f'{tag} is only credited symbols in its own membership', syms <= ever,
          str(sorted(syms - ever)))


# ---------------------------------------------------------------- 5. the map's own arithmetic
print('\n5. MAP')

for key, bt in MAP['batches'].items():
    tag = bt.get('tag', key)
    H = bt['holdings']
    # Would catch: an `invested` left stale after a rebalance - it was wrong on two baskets today.
    calc = sum(h['q'] * h['avg'] for h in H.values())
    check(f'{tag} invested matches its holdings', abs(calc - bt['invested']) < 1.0,
          f"stated {rup(bt['invested'])} vs computed {rup(calc)}")
    # Would catch: a negative or zero position left behind by a sell.
    check(f'{tag} has no non-positive quantity', all(h['q'] > 0 for h in H.values()))
    # Would catch: a holding with no cost, which reads as free money in every percentage.
    check(f'{tag} has no zero average cost', all(h['avg'] > 0 for h in H.values()))
    # Would catch: a sells ledger whose rows do not add up to its own stated realised.
    for rk in [k for k in bt if k.startswith(('rebalance_', 'topup_', 'exit_'))]:
        rec = bt[rk]
        if 'realised_gross' in rec and 'fills' in rec:
            s = sum(f.get('realised_gross', 0) for f in rec['fills'].values()
                    if isinstance(f, dict)) or None
            if s is not None and rec.get('realised_gross'):
                check(f'{tag}.{rk} fills sum to its realised_gross',
                      abs(s - rec['realised_gross']) < 1.0,
                      f"fills {rup(s)} vs stated {rup(rec['realised_gross'])}")
    for x in bt.get('sells', []):
        # Would catch: a ledger row whose own three numbers disagree.
        if all(k in x for k in ('q', 'price', 'proceeds')):
            check(f"{tag} sell {x['s']} proceeds == q*price",
                  abs(x['q'] * x['price'] - x['proceeds']) < 1.0)
        if all(k in x for k in ('proceeds', 'cost', 'realised_gross')):
            check(f"{tag} sell {x['s']} realised == proceeds-cost",
                  abs((x['proceeds'] - x['cost']) - x['realised_gross']) < 1.0)


# ---------------------------------------------------------------- 6. live.json
print('\n6. LIVE.JSON')

if os.path.exists(P('live.json')):
    L = json.load(open(P('live.json')))
    rows = L['holdings']
    # Would catch: an unpriced row reading as zero value - it turned +81,196 into -43,725 today.
    unpriced = [r['s'] for r in rows if not r.get('ltp')]
    check('every holding is priced', not unpriced, str(unpriced))
    # Would catch: pnl rows that do not reconcile to the totals shown on screen.
    iv = sum(r['q'] * r['avg'] for r in rows)
    mk = sum(r['q'] * r['ltp'] for r in rows)
    pn = sum(r['pnl'] for r in rows)
    check('pnl rows sum to market - invested', abs(pn - (mk - iv)) < 1.0,
          f'rows {rup(pn)} vs {rup(mk - iv)}')
    if 'totals' in L:
        check('stated totals match the rows',
              abs(L['totals']['invested'] - iv) < 1.0 and abs(L['totals']['market'] - mk) < 1.0)
    # Would catch: the portfolio double-counting a symbol held by two baskets.
    agg = defaultdict(float)
    for bt in MAP['batches'].values():
        for s, h in bt['holdings'].items():
            agg[base(s)] += h['q']
    mism = [(r['s'], r['q'], agg.get(r['s'])) for r in rows
            if abs(r['q'] - agg.get(r['s'], -1)) > 1e-6]
    check('live.json quantity == summed map quantity per symbol', not mism, str(mism[:4]))
    check('live.json has one row per symbol (no duplicates)',
          len({r['s'] for r in rows}) == len(rows))
    # Equality is CORRECT here, and an earlier version of this comment claimed the opposite. A
    # symbol in two baskets is two DISTINCT lots of shares, not the same shares counted twice, so
    # the per-basket sum and the portfolio total must agree exactly. What would be a real bug is
    # summing per-basket MARKET VALUE against a portfolio value built by deduping symbols.
    per = sum(bt['invested'] for bt in MAP['batches'].values())
    check('per-basket invested sums to portfolio invested',
          abs(per - iv) < 1.0, f'per-basket {rup(per)} vs portfolio {rup(iv)}')
    # The check above is CIRCULAR - live.json is built by summing the map - so it can only catch a
    # transcription slip. The independent source is the broker. Kite applies a day's net SELLS to
    # holdings at once but a net BUY only settles T+1, so the identity to assert is
    # map == kite_settled + todays_unsettled_net_buy. Without the broker side this is unverifiable,
    # so record when it was last confirmed rather than pretend the circular check covers it.
    print(f"      note: last reconciled against Kite manually: {MAP.get('kite_reconciled', 'never')}. "
          f"Re-run that when holdings change.")
else:
    print('      live.json absent - skipped')




# ---------------------------------------------------------------- 7. Python == both browser pages
print('\n7. PARITY: Python vs the pages\' own JavaScript')
# Would catch: a corporate action, alias or attribution rule added to one implementation and not the
# other. It already happened once - a demerger was applied in Python and silently ignored by
# baskets.html, a disagreement no Python-only check can see. parity.mjs runs the REAL page code.
import subprocess
_BUCK = {'__unknown__': 'UNKNOWN', '__prebasket__': 'PRE-BASKET', '__ambiguous__': 'AMBIGUOUS'}
try:
    _py = json.loads(next(l for l in subprocess.run(['python3', P('attribute_sells.py'), '--json'],
                     capture_output=True, text=True, cwd=HERE).stdout.splitlines() if l.startswith('JSON:'))[5:])
    _js = json.loads(subprocess.run(['node', P('parity.mjs')], capture_output=True, text=True,
                                    cwd=HERE, timeout=60).stdout.strip().splitlines()[-1])
    _jb = {_BUCK.get(k, k): v for k, v in _js['buckets'].items()}
    check('baskets.html total realised == Python', abs(_js['baskets_total'] - _py['total']) < 0.01,
          f"{rup(_js['baskets_total'])} vs {rup(_py['total'])}")
    _off = {k: (round(_py['buckets'].get(k, 0)), round(_jb.get(k, 0)))
            for k in set(_py['buckets']) | set(_jb) if abs(_py['buckets'].get(k, 0) - _jb.get(k, 0)) > 0.01}
    check('baskets.html == Python, bucket for bucket', not _off, str(_off))
    check('baskets.html uncosted == Python', _js['baskets_uncosted'] == _py['uncosted'],
          f"{_js['baskets_uncosted']} vs {_py['uncosted']}")
    check('dashboard.html total realised == Python', abs(_js['dashboard_total'] - _py['total']) < 0.01,
          f"{rup(_js['dashboard_total'])} vs {rup(_py['total'])}")
    # The public CLI (build_dashboard.py) is the FOURTH FIFO, the one other users run on their exports.
    # Would catch: it drifting from the page it feeds - it had no same-day netting until 13 Nov 2026.
    sys.path.insert(0, os.path.join(HERE, '..', 'folio-terminal'))
    import build_dashboard as _BD
    _cli = sum(t['pnl'] for t in _BD.fifo([dict(t) for t in json.load(open(P('live.json')))['_trades']])[0])
    check('build_dashboard.py total realised == Python', abs(_cli - _py['total']) < 0.01,
          f"{rup(_cli)} vs {rup(_py['total'])}")
except (StopIteration, IndexError, subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as e:
    check('parity harness ran', False, repr(e))

# 9. HOSTILE INPUT. A basket sheet is a file a friend sends, and an upload can be anything: both pages
# interpolate symbols into HTML, so markup in a symbol or a basket name was script on this origin, where
# the Kite API key and the whole portfolio sit in localStorage. xss_test.mjs feeds hostile input through
# the REAL page code; it failed every check on the code before the fix.
print('\n9. HOSTILE INPUT CANNOT BECOME MARKUP (xss_test.mjs, real page code)')
_x = subprocess.run(['node', P('xss_test.mjs')], capture_output=True, text=True)
check('basket sheets and uploads reject or escape markup', _x.returncode == 0,
      '' if _x.returncode == 0 else (_x.stdout + _x.stderr)[-300:])



# ---------------------------------------------------------------- 8. daily mark-to-market vs FIFO books
# Would catch: anything that makes the two independent views of "how much did I make" disagree - a
# missed trade, a corporate action on one side only, a wrong fill price, a quantity basis bug. _daily.py
# values the book every day at market closes with cash-flow adjustment; FIFO books realised and cost.
# Over the same window: sum(daily pnl) == realised in window + unrealised(end) - unrealised(start).
print('\n8. DAILY MARK-TO-MARKET == REALISED + CHANGE IN UNREALISED')
if os.path.exists(P('_daily_perf.csv')):
    import csv
    _rows = list(csv.DictReader(open(P('_daily_perf.csv'))))
    _st, _en = _rows[0]['date'], _rows[-1]['date']
    if _en < _last_trade:
        print(f'      note: _daily_perf.csv ends {_en}, before the last trade {_last_trade} - rerun _daily.py')
    else:
        _pre = [t for t in _all if t['d'] < _st]
        _, _, _L0, _ = fifo(_pre, MAP.get('corporate_actions', []), norm=_n0, until=_st, log=lambda m: None)
        _c0 = sum(q * p for d in _L0.values() for q, p, _d in d)
        _cN = sum(fifo_cost.values())
        _M, _, _, _ = fifo(_all, MAP.get('corporate_actions', []), norm=_n0, log=lambda m: None)
        _real = sum(x['pnl'] for x in _M if x['d'] >= _st)
        _v0 = float(_rows[0]['value']) - float(_rows[0]['pnl']) - float(_rows[0]['flow'])
        _mtm = sum(float(r['pnl']) for r in _rows)
        _acc = _real + (float(_rows[-1]['value']) - _cN) - (_v0 - _c0)
        check(f'daily P&L {_st}..{_en} == realised + change in unrealised (within Rs 50)',
              abs(_mtm - _acc) < 50, f'{rup(_mtm)} vs {rup(_acc)}')
else:
    print('      note: no _daily_perf.csv - run _daily.py')


# The verdict comes LAST: it used to sit above section 8, so a daily-P&L failure printed FAIL and still
# exited 0, and anything trusting the exit code (a scheduled run) would have passed a broken book.
print(f"\n{'='*66}")
if FAIL:
    print(f"{len(FAIL)} FAILED:")
    for f in FAIL:
        print(f'   - {f}')
    sys.exit(1)
print('all invariants hold')
