#!/usr/bin/env python3
"""Set every basket's avg cost from FIFO's open lots, so realised (FIFO) and unrealised (map avg) are one book.

    python3 _sync_costs.py        -> backs up the map to _archive/, rewrites avg, prints every change

Run after recording any sale. test_money.py fails until map cost == FIFO open-lot cost on every symbol.
"""
import json, shutil, datetime, io, contextlib
from collections import defaultdict
with contextlib.redirect_stdout(io.StringIO()):
    import attribute_sells as A          # its FIFO already includes map fills and corporate actions

M = json.load(open('_smallcase_map.json'))   # fresh copy: only norm + lots come from A
fifo_cost = {s: sum(q * p for q, p, _d in d) for s, d in A.lots.items()}
held = defaultdict(list)                 # normalised symbol -> [(holding dict, current cost)]
for b in M['batches'].values():
    for s, h in b['holdings'].items():
        held[A.norm(s)].append((h, h['q'] * h['avg'], b['tag'], s))

changes = []
for s, rows in held.items():
    want, have = fifo_cost.get(s), sum(c for _, c, _, _ in rows)
    if want is None or abs(want - have) <= 2:
        continue
    # ponytail: a symbol in two baskets has ONE FIFO queue (Kite's is account-wide), so its cost is split
    # in proportion to each basket's current cost. Exact per-basket lots would need per-basket FIFO.
    for h, c, tag, raw in rows:
        new = round(want * (c / have) / h['q'], 6)
        changes.append((raw, tag, h['avg'], new, round(h['q'] * (new - h['avg']))))
        h['avg'] = new
for b in M['batches'].values():
    b['invested'] = round(sum(h['q'] * h['avg'] for h in b['holdings'].values()), 2)

if changes:
    bak = f"_archive/_smallcase_map.bak-{datetime.date.today():%d%b}-sync.json".lower()
    shutil.copy('_smallcase_map.json', bak)
    json.dump(M, open('_smallcase_map.json', 'w'), indent=1, ensure_ascii=False)
    for raw, tag, old, new, d in changes:
        print(f'  {raw:<12} {tag:<9} avg {old:>11,.4f} -> {new:>11,.4f}   cost {d:>+7,}')
    print(f'{len(changes)} rows synced, backup {bak}')
else:
    print('map cost already equals FIFO open-lot cost on every symbol')
