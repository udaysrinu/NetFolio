#!/usr/bin/env python3
"""Attribute realised P&L to baskets, and refuse to guess where it cannot be done.

A tradebook has 13 columns and none is a tag, so a sale carries no basket. But most symbols
belong to exactly one basket, so symbol alone attributes them. Three cases, kept apart:

  ATTRIBUTED  the symbol belongs to exactly one basket, current or historical
  AMBIGUOUS   held through two or more baskets - nothing in any file can say which sold
  UNKNOWN     in no basket we ever recorded (older self-managed positions, now closed)

Historical membership matters: stocks exited in a rebalance are removed from the map, so matching
against the CURRENT map alone would misfile their sales as UNKNOWN - silently stripping a basket of
its realised losses.

Run:  python3 attribute_sells.py
"""
import json, os, re, sys
from collections import defaultdict, deque

HERE = os.path.dirname(os.path.abspath(__file__))
P = lambda n: os.path.join(HERE, n)


from tradebook import base


def rup(v):
    neg = v < 0
    n = f'{abs(round(v)):d}'
    if len(n) > 3:
        head, tail = n[:-3], n[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:]); head = head[:-2]
        if head:
            parts.insert(0, head)
        n = ','.join(parts + [tail])
    return ('-' if neg else '') + n


# ---------------------------------------------------------------- who owned what, AND WHEN
# Membership is DATED. Using membership as of today lets a trade made later rewrite the past: one basket
# exited a stock at a loss, another basket later bought the same stock, and that settled loss silently
# moved to AMBIGUOUS, understating the first basket's realised loss several times over. So: ask who held the
# symbol ON THE DAY OF THE SALE, not who holds it now.
#   from=None  the symbol predates our records. Correct for a position a basket EXITED: it was held
#              before the exit, we just cannot say from when.
#   to=None    still held.
_MAP = json.load(open(P('_smallcase_map.json')))
# Zerodha's tradebook export and its holdings API do not always name the same ISIN identically, and
# base() strips only -BE/-BZ/-BL. A symbol can be named one way in the map and holdings and another in
# every tradebook row (an InvIT's -IV suffix, for one), so without an explicit bridge (`symbol_aliases`
# in the map) its sales find no window and sit in UNKNOWN. Explicit, not heuristic: stripping more suffixes could merge two real instruments.
_ALIAS = _MAP.get('symbol_aliases', {})
norm = lambda s: _ALIAS.get(s, _ALIAS.get(base(s), base(s)))

windows = defaultdict(list)          # symbol -> [(tag, from, to)]
hist_only = set()
for key, bt in _MAP['batches'].items():
    tag = bt.get('tag', key)
    # UNION, never `membership or fallback`. A membership block that is a strict SUBSET of ever_held
    # used to SHADOW it: positions that were fully sold but never listed in `exited` dropped out, so
    # that basket's own recorded realised fell into UNKNOWN beside unrelated positions, or into
    # AMBIGUOUS. The dated windows, added to FIX attribution, made that basket strictly worse than the
    # undated code they replaced. Anything ever held but unwindowed now gets an OPEN window, which is
    # the honest default: we know the basket held it, we just do not know when it started or stopped.
    # Keyed on the NORMALISED symbol, so `ACME` from ever_held and `ACME-BE` from membership
    # collapse to ONE window instead of two. Keying on the raw string pushed the same basket twice for
    # every -BE series symbol - harmless for the tag SET that decides attribution, but it double-
    # counted them into hist_only and reported three symbols as "exited" that are still held.
    # ever_held UNION holdings, so an empty or stale ever_held can never drop a held symbol - and the
    # JS does exactly the same, where `[] || fallback` used to keep the empty list and diverge.
    mem = {}
    for s in list(bt.get('ever_held') or []) + list(bt['holdings']):
        mem[norm(s)] = {'from': None, 'to': None}
    for s, w in (bt.get('membership') or {}).items():
        mem[norm(s)] = w or {'from': None, 'to': None}   # explicit wins; a null value = open window
    held_n = {norm(s) for s in bt['holdings']}
    windowed = {norm(s) for s in (bt.get('membership') or {})}
    for s in sorted(mem):
        # An unwindowed leftover is a data gap worth naming, not a silent default.
        if s not in windowed:
            print(f'  !! {tag}: {s} was ever held but has NO membership window - treated as a member '
                  f'for all time, which may over-credit this basket.')
        windows[s].append((tag, mem[s].get('from'), mem[s].get('to')))
        if s not in held_n:
            hist_only.add(s)
hist_only -= {norm(s) for _bt in _MAP['batches'].values() for s in _bt['holdings']}


def held_on(sym, day):
    """Every basket that held `sym` on `day`. A None bound is open in that direction."""
    return {tag for tag, frm, to in windows.get(sym, ())
            if (frm is None or day >= frm) and (to is None or day <= to)}


# ---------------------------------------------------------------- the ledger knows what FIFO cannot
# A tradebook says WHAT was sold; only the recorded ledger says WHICH BASKET sold it. For a symbol
# held by two live baskets, symbol-based attribution can only answer AMBIGUOUS - but if we wrote the
# sale down at the time, we already know. When a stock held by two baskets is sold out of
# both on the same day, FIFO alone must dump that realised into AMBIGUOUS the moment an export covering
# the date loads, discarding the more precise record we already hold.
# So: ledger wins on ATTRIBUTION, FIFO still wins on COST. Three rules, each from a real defect:
#  1. Rows are ordered GLOBALLY by order_id across every basket, then keyed. Sorting within each basket
#     and appending in map order put one basket's sale ahead of another's that executed first,
#     swapping realised between the two baskets the moment an export covering that day loads.
#  2. Used only when the ledger's quantity for (symbol, date) EQUALS the tradebook's. If a basket's
#     sells go unrecorded and it sold a shared symbol first that day, its shares would
#     silently consume - and take the tag of - another basket's row.
#  3. A FIFO slice that spans two ledger rows is SPLIT between them by quantity, rather than handed
#     wholesale to whichever row it started in.
_ledger_rows = []
for key, bt in _MAP['batches'].items():
    tag = bt.get('tag', key)
    for x in bt.get('sells', []):
        if x.get('date') and x.get('q'):
            _ledger_rows.append((str(x.get('order_id', '')), norm(x['s']), x['date'], float(x['q']), tag))
ledger = defaultdict(deque)          # (symbol, date) -> deque of [qty_remaining, tag]
for _oid, _s, _d, _q, _tag in sorted(_ledger_rows):
    ledger[(_s, _d)].append([_q, _tag])
_ledger_ok = {}


def ledger_split(sym, day, qty):
    """Consume `qty` shares from the ledger for (sym, day). Returns [(tag, shares)] or None."""
    k = (sym, day)
    if k not in ledger:
        return None
    if k not in _ledger_ok:
        want = sum(t['q'] for t in trades if t['t'] == 'sell' and t['s'] == sym and t['d'] == day)
        _ledger_ok[k] = abs(sum(r[0] for r in ledger[k]) - want) < 1e-6
        if not _ledger_ok[k]:
            print(f'  !! ledger for {sym} {day} records {sum(r[0] for r in ledger[k]):.0f} shares but the '
                  f'tradebook sold {want:.0f} - not trusted, left AMBIGUOUS')
    if not _ledger_ok[k]:
        return None
    dq, out, left = ledger[k], [], qty
    while left > 1e-9 and dq:
        take = min(left, dq[0][0])
        out.append((dq[0][1], take))
        dq[0][0] -= take
        left -= take
        if dq[0][0] <= 1e-9:
            dq.popleft()
    return out or None

# ---------------------------------------------------------------- trades, FIFO
# Every export merged and deduped, then ONE shared FIFO (tradebook.fifo) that applies the corporate
# actions in the map. This file used to carry its own copy; so did realised.py and test_money.py, and
# they disagreed about which actions existed.
from tradebook import load_all, fifo, map_fills
trades, _files, _gaps = load_all()
TB_LAST = max(t['d'] for t in trades)
# The map's own fills for dates no export covers yet, so today's sales are FIFO-costed here exactly as
# on both pages - one realised convention everywhere.
_fills = map_fills(_MAP, TB_LAST, norm)
trades = sorted(trades + _fills, key=lambda x: (x['d'], x.get('tm', ''), x['t'] != 'buy', x['x'], str(x['id'])))
matched, uncosted, lots, _ca_added = fifo(trades, _MAP.get('corporate_actions', []), norm=norm)

# ---------------------------------------------------------------- when did each basket BEGIN
# A basket is defined by symbols AND BY TIME. Attributing on the symbol alone swept sales made months
# BEFORE a basket existed into it, turning the basket's real loss into a phantom gain. It was caught only
# because the account holder remembered booking that loss. Read the leading date out of `executed`; batches
# whose executed is prose (e.g. "various (pre-tagging)") have no knowable start and are
# reported as un-gated rather than given an invented cutoff.
start, undateable = {}, set()
for k, bt in json.load(open(P('_smallcase_map.json')))['batches'].items():
    tag = bt.get('tag', k)
    m = re.match(r'(\d{4}-\d{2}-\d{2})', str(bt.get('executed', '')))
    if not m:
        undateable.add(tag)
    else:                                  # two batches on one tag: the basket began at the earlier
        start[tag] = min(start.get(tag, m.group(1)), m.group(1))

# ---------------------------------------------------------------- attribute
buckets = defaultdict(lambda: {'pnl': 0.0, 'proceeds': 0.0, 'syms': defaultdict(float)})
for m in matched:
    o = held_on(m['s'], m['d'])          # membership ON THE SALE DATE, not today
    key = 'UNKNOWN' if not o else (next(iter(o)) if len(o) == 1 else 'AMBIGUOUS')
    # A recorded sale beats an inference. Only consult the ledger where the symbol alone cannot
    # decide, so a ledger slip can never silently override a case the windows already answer.
    if key == 'AMBIGUOUS':
        parts = ledger_split(m['s'], m['d'], m['q'])
        if parts and all(t in o for t, _ in parts):
            for t, q in parts:                   # split the slice by the recorded quantities
                f = q / m['q']
                bk = buckets[t]
                bk['pnl'] += m['pnl'] * f; bk['proceeds'] += m['proceeds'] * f
                bk['syms'][m['s']] += m['pnl'] * f
            continue
    # No basket held it that day, but one holds it NOW and its window opens later: that is not an
    # unrelated symbol, it is your own trading in a stock a basket later bought. Worth naming, or the
    # windowing above quietly demotes that history into UNKNOWN.
    if not o and any(frm and m['d'] < frm for _, frm, _ in windows.get(m['s'], ())):
        key = 'PRE-BASKET'
    # A dateable basket cannot own a sale that predates its own creation. Still needed after the
    # windowing above, because a currently-held symbol's window opens at the basket's own start only
    # when `executed` carries a date - batches that say "various" have open windows, and
    # this gate is all that stands between them and pre-basket trading.
    if len(o) == 1 and key not in undateable and key in start and m['d'] < start[key]:
        key = 'PRE-BASKET'
    b = buckets[key]
    b['pnl'] += m['pnl']; b['proceeds'] += m['proceeds']; b['syms'][m['s']] += m['pnl']

print(f'# Realised P&L by basket, FIFO, GROSS of charges')
print(f'  {trades[0]["d"]} to {TB_LAST} from every export, plus {len(_fills)} recorded fills to '
      f'{trades[-1]["d"]} that no export covers yet\n')
print(f"  {'basket':<12}{'realised':>11}{'proceeds':>12}   symbols")
print('  ' + '-' * 74)
order = sorted(buckets, key=lambda k: (k in ('AMBIGUOUS', 'UNKNOWN', 'PRE-BASKET'), -abs(buckets[k]['pnl'])))
for k in order:
    b = buckets[k]
    top = ', '.join(f'{s} {rup(v)}' for s, v in
                    sorted(b['syms'].items(), key=lambda kv: -abs(kv[1]))[:5])
    print(f"  {k:<12}{rup(b['pnl']):>11}{rup(b['proceeds']):>12}   {top}")
print('  ' + '-' * 74)
print(f"  {'TOTAL':<12}{rup(sum(b['pnl'] for b in buckets.values())):>11}"
      f"{rup(sum(b['proceeds'] for b in buckets.values())):>12}")

if hist_only:
    print(f"\n  attributed via HISTORICAL membership only (exited, no longer in the map):")
    print(f"    {', '.join(sorted(hist_only))}")

if 'PRE-BASKET' in buckets:
    print(f"\n  !! PRE-BASKET {rup(buckets['PRE-BASKET']['pnl'])} - your own trading in these symbols")
    print(f"     BEFORE the basket that now holds them existed. Same stock, not the basket's result.")
    print(f"     Basket start dates read from `executed`: "
          + ', '.join(f'{t} {d}' for t, d in sorted(start.items())))
if undateable:
    print(f"\n  !! NOT DATE-GATED: {', '.join(sorted(undateable))} - `executed` is prose, not a date,")
    print(f"     so their sales cannot be split before/after and may still include pre-basket trading.")

if 'AMBIGUOUS' in buckets:
    print(f"\n  !! AMBIGUOUS {rup(buckets['AMBIGUOUS']['pnl'])} - held through 2+ baskets, so no file")
    print(f"     can say which one sold. Left unattributed rather than split by a guess.")

unc = sum(u['proceeds'] for u in uncosted)
if unc:
    print(f"\n  !! {len(uncosted)} UNCOSTED sells, {rup(unc)} of proceeds, excluded entirely.")
    print(f"     No lot exists for them in ANY export. With continuous coverage that means a")
    print(f"     non-trade credit - IPO allotment, rights, bonus - missing from corporate_actions:")
    for s_ in sorted({u['s'] for u in uncosted}):
        print(f"       {s_:<12}{rup(sum(u['proceeds'] for u in uncosted if u['s'] == s_)):>10}   "
              f"{'known: ' + _MAP.get('known_uncosted', {})[s_][:48] if s_ in _MAP.get('known_uncosted', {}) else 'UNEXPLAINED'}")

print(f"\n  GROSS: brokerage, STT, stamp duty and GST are not in a tradebook. Console's P&L report")
print(f"  gives the net figure; this is the basket split the P&L report cannot give.")

# one check: every costed rupee lands in exactly one bucket
tot = sum(m['pnl'] for m in matched)
assert abs(tot - sum(b['pnl'] for b in buckets.values())) < 0.01, 'realised lost in attribution'
print(f"\nselfcheck ok: all {rup(tot)} of costed realised P&L is in exactly one bucket")

# machine-readable tail for test_money.py's Python-vs-browser parity check
if '--json' in sys.argv:
    print('JSON:' + json.dumps({'total': sum(b['pnl'] for b in buckets.values()),
                                'buckets': {k: b['pnl'] for k, b in buckets.items()},
                                'uncosted': sorted({u['s'] for u in uncosted})}))
