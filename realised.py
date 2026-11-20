#!/usr/bin/env python3
"""Realised P&L from a Zerodha tradebook export, FIFO, with the window problem made explicit.

WHY THIS IS NOT JUST A SUM
A sell is only a profit relative to what those particular shares cost. FIFO says the oldest shares
go first. But the export covers a WINDOW (Zerodha caps it at 365 days), so a sell inside the window
may consume shares bought BEFORE it. Those buys are invisible here, so their cost is unknown. A
naive FIFO treats the missing cost as zero and reports the entire sale proceeds as profit - which
would overstate realised gains without any error appearing. This script refuses to do that: such
sells are reported separately as UNCOSTED, never folded into the total.

WHAT THIS CANNOT TELL YOU
  - which basket sold. The export has 13 columns and none is a tag. See attribute_sells.py, which
    recovers it for symbols belonging to exactly one basket and abstains for the rest.
  - charges. Brokerage, STT, stamp duty, exchange fees and GST are not in the tradebook, so every
    figure below is GROSS. Net realised is lower.
  - dividends. Not in the tradebook and not in any Kite Connect endpoint.

Run:  python3 realised.py      (merges every tradebook*.xlsx in this folder)
"""
import sys, os
from collections import defaultdict, deque

HERE = os.path.dirname(os.path.abspath(__file__))


from tradebook import load_all, base, fifo as _fifo


def fifo(trades):
    """Returns (realised_rows, uncosted_rows, open_lots). Never invents a cost.

    A thin adapter over tradebook.fifo, the one shared implementation. This file used to carry its
    own FIFO that applied no corporate actions, so it silently disagreed with attribute_sells.py -
    and a single 1:1 bonus alone made it tens of thousands wrong.
    """
    import json
    M = json.load(open(os.path.join(HERE, '_smallcase_map.json')))
    alias = M.get('symbol_aliases', {})
    norm = lambda s: alias.get(s, alias.get(base(s), base(s)))
    matched, unc, lots, _ = _fifo(trades, M.get('corporate_actions', []), norm=norm)
    for r in matched:
        r['sold'] = r['d']
    for u in unc:
        u['sold'] = u['d']
    return matched, unc, {s: list(d) for s, d in lots.items()}


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


trades, _files, _gaps = load_all()
realised, uncosted, open_lots = fifo(trades)

buys = sum(1 for t in trades if t['t'] == 'buy')
print(f'# Realised P&L (GROSS, FIFO), all tradebook exports merged')
print(f'  {len(trades)} trades: {buys} buys, {len(trades)-buys} sells'
      f'  ·  {trades[0]["d"]} to {trades[-1]["d"]}\n')

per = defaultdict(float)
for r in realised:
    per[r['s']] += r['pnl']
total = sum(per.values())

print(f"{'symbol':<14}{'realised':>12}   matched sells")
print('-' * 58)
for s, v in sorted(per.items(), key=lambda kv: -abs(kv[1])):
    n = sum(1 for r in realised if r['s'] == s)
    print(f'{s:<14}{rup(v):>12}   {n}')
print('-' * 58)
print(f"{'TOTAL (gross)':<14}{rup(total):>12}   {len(realised)} lot matches\n")

if uncosted:
    lost = sum(u['proceeds'] for u in uncosted)
    print(f'## UNCOSTED SELLS - {len(uncosted)}, proceeds {rup(lost)}, EXCLUDED from the total above')
    print('   No lot exists for these in ANY export. Coverage is continuous, so each is a non-trade')
    print('   credit - IPO allotment, rights entitlement, bonus - missing from corporate_actions in')
    print('   _smallcase_map.json. Counting the proceeds as profit would overstate realised gains.')
    print(f"   {'symbol':<14}{'sold':<12}{'qty':>7}{'price':>10}{'proceeds':>12}")
    for u in sorted(uncosted, key=lambda u: -u['proceeds']):
        print(f"   {u['s']:<14}{u['sold']:<12}{u['q']:>7.0f}{u['sell']:>10.2f}{rup(u['proceeds']):>12}")
    print(f'\n   -> add the missing action to corporate_actions; a wider export will not help.\n')

print('## Reminders')
print('   GROSS: brokerage, STT, stamp duty, exchange fees and GST are not in the tradebook.')
print('   NO BASKET: the export has no tag column, so none of this can be split per basket.')
print(f'   Window ends {trades[-1]["d"]} - anything traded after that is not here at all.')

# self-check: every share sold is either costed or listed as uncosted, none silently dropped
sold_total = sum(t['q'] for t in trades if t['t'] != 'buy')
accounted = sum(r['q'] for r in realised) + sum(u['q'] for u in uncosted)
assert abs(sold_total - accounted) < 1e-6, f'{sold_total} sold but {accounted} accounted for'
print(f'\nselfcheck ok: all {sold_total:.0f} shares sold are either costed or flagged uncosted')
