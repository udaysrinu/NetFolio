#!/usr/bin/env python3
"""Load every Zerodha tradebook export in this folder as one deduped, ordered trade list.

Why a shared loader: Zerodha caps an export at 365 days, so full history arrives as several files
with overlapping ranges. Both realised.py and attribute_sells.py previously picked ONE file - the
biggest - which silently threw away years of buys and left their cost unknown.

Dedupe is on (Trade Date, Exchange, Trade ID). Trade ID alone is NOT globally unique - measured on
745 real trades it descends 92 times when sorted by date and spans 12,200 to 610,750,974, so it is a
per-day per-exchange counter. There are no collisions in the current data, but keying on it alone
would silently drop a trade the first time there is one.

Also reports gaps: a month with no trades is normal, but a multi-month hole at a file boundary
usually means an export is missing, and FIFO run over a hole silently invents uncosted sells.
"""
import glob, json, os, re
from collections import Counter
import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
COLS = ('Symbol', 'Trade Date', 'Trade Type', 'Quantity', 'Price', 'Trade ID', 'Exchange')
# Present in every export but not required, so an older file missing one still loads:
#   Order Execution Time - the ONLY correct intra-day FIFO order. See the sort below.
#   ISIN                 - the instrument's real identity, unlike the symbol string, which Zerodha
#                          spells differently between the tradebook and the holdings API.
OPT = ('Order Execution Time', 'ISIN')


def base(s):
    s = str(s).upper()
    for suf in ('-BE', '-BZ', '-BL'):
        if s.endswith(suf):
            return s[:-len(suf)]
    return s


# A warning that fires on a known non-issue teaches you to ignore warnings, so known-quiet months are recorded.
COVERAGE = []   # (from, to, file) declared by each export, filled by load_all

def _known_quiet():
    """Months confirmed to have no trades, so a gap there is not a missing export. They are facts about
    one account, so they live in the private map (`known_quiet`: [{from, to, why}]), not in code. Prove
    one from the exports where possible: a complete export that brackets a month and contains nothing
    in it is evidence that nothing happened, not evidence that a file is missing."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_smallcase_map.json')
    try:
        return {(q['from'], q['to']): q.get('why', '') for q in json.load(open(p)).get('known_quiet', [])}
    except (OSError, ValueError):
        return {}


KNOWN_QUIET = _known_quiet()


def _months(a, b):
    """Every YYYY-MM from a to b inclusive."""
    y, m = map(int, a[:7].split('-'))
    ey, em = map(int, b[:7].split('-'))
    out = set()
    while (y, m) <= (ey, em):
        out.add(f'{y:04d}-{m:02d}')
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def _covered(g):
    """A gap is proven quiet if EVERY month in it lies inside SOME declared range. Checked against
    the union, not one range at a time: a gap across a financial-year boundary (Feb..Apr) sits in
    two consecutive exports and no single one of them covers it."""
    have = set().union(*(_months(a, b) for a, b, _ in COVERAGE)) if COVERAGE else set()
    return _months(*g) <= have


def load_all(verbose=True, notes=True):
    """Returns (trades, files, gaps). Trades are sorted by date, buys before sells same-day."""
    seen, files = {}, []
    COVERAGE.clear()
    for f in sorted(glob.glob(os.path.join(HERE, 'tradebook*.xls*'))):
        try:
            ws = openpyxl.load_workbook(f)['Equity']
        except Exception:
            continue
        rows = [r for r in ws.iter_rows(values_only=True)]
        hdr = next((i for i, r in enumerate(rows)
                    if r and any(str(c).strip() == 'Symbol' for c in r if c)), None)
        if hdr is None:
            continue
        # Zerodha writes "Tradebook for Equity from YYYY-MM-DD to YYYY-MM-DD" above the header. That
        # is the only evidence that a trade-free month is genuinely quiet rather than an export we
        # lack: a month INSIDE a declared range with no rows in it had no trades. Used for gap
        # warnings only - NEVER as "the tradebook's last date", because an export declares coverage
        # to the day it was downloaded while holding trades only to T-1 or earlier. Treating a declared
        # end as the last trade date silently dropped the ledger realised booked on that last day.
        for r in rows[:hdr]:
            m = re.search(r'from (\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})',
                          ' '.join(str(c) for c in (r or ()) if c is not None))
            if m:
                COVERAGE.append((m.group(1), m.group(2), os.path.basename(f)))
                break
        H = [str(c).strip() if c else '' for c in rows[hdr]]
        if not all(c in H for c in COLS):
            continue
        ix = {k: H.index(k) for k in COLS}
        ix.update({k: H.index(k) for k in OPT if k in H})
        n = 0
        for r in rows[hdr + 1:]:
            if not r or not r[ix['Symbol']]:
                continue
            # (date, exchange, id) - see the module docstring on why the id alone is not a key.
            tid = (str(r[ix['Trade Date']])[:10], str(r[ix['Exchange']]).strip().upper(),
                   str(r[ix['Trade ID']]))
            if tid in seen:
                continue
            seen[tid] = {'s': base(r[ix['Symbol']]), 'd': str(r[ix['Trade Date']])[:10],
                         't': str(r[ix['Trade Type']]).strip().lower(),
                         'q': float(r[ix['Quantity']]), 'p': float(r[ix['Price']]),
                         'id': str(r[ix['Trade ID']]), 'x': tid[1],
                         'tm': str(r[ix['Order Execution Time']]) if 'Order Execution Time' in ix
                               and r[ix['Order Execution Time']] is not None else '',
                         'isin': str(r[ix['ISIN']]).strip().upper() if 'ISIN' in ix
                                 and r[ix['ISIN']] is not None else ''}
            n += 1
        files.append((os.path.basename(f), n))

    # Contract notes from Gmail (contract_notes.py) cover the days no export does yet. The broker's trade
    # numbers ARE the tradebook's Trade IDs, so a day both cover dedupes on the same (date, exchange, id)
    # key - verified fill for fill on every shared day. For days only the notes cover, their real fills
    # replace the map's hand-recorded ones, because map_fills only emits days after the newest trade here.
    cnp = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_contract_notes.json')
    if notes and os.path.exists(cnp):
        n = 0
        for note in json.load(open(cnp)).values():
            for t in note['trades']:
                tid = (t['d'], t['x'], str(t['id']))
                if tid in seen:
                    continue
                seen[tid] = {'s': base(t['s']), 'd': t['d'], 't': t['t'], 'q': float(t['q']), 'p': float(t['p']),
                             'id': str(t['id']), 'x': t['x'], 'tm': t['tm'], 'isin': t['isin']}
                n += 1
        files.append(('_contract_notes.json (Gmail)', n))

    # FIFO order is CHRONOLOGICAL, and the export tells us the time - so use it. The previous key was
    # (date, buys-first, EXCHANGE, trade id), which sorted BSE before NSE regardless of clock time and
    # therefore consumed the wrong lots whenever one day held fills on both exchanges. Measured: a day
    # with NSE fills in the morning and BSE fills in the afternoon made a later sale eat the BSE lots
    # first and understate its profit by ~17%, on one sale, from a sort key alone.
    # `tm` is an ISO timestamp so it sorts lexically. Buys-before-sells survives only as a tiebreak
    # for identical timestamps, where the true order is genuinely unknowable; exchange and trade id
    # follow, purely to keep the result deterministic. Older exports without the column get tm='',
    # which sorts first within the day - the old behaviour, not a crash.
    trades = sorted(seen.values(),
                    key=lambda x: (x['d'], x.get('tm', ''), x['t'] != 'buy', x['x'], str(x['id'])))

    # ---- RENAMES. FIFO keys lots on the symbol, so a security that changed name mid-history becomes
    # two chains: the buys sit under the old name and are never matched, and the sells are reported as
    # "uncosted, bought before the window". The ISIN column is the security's real identity and proves
    # it. Measured: two renamed securities, one a real loss and one a real gain,
    # both vanished; realised was overstated by tens of thousands, and phantom open lots were left
    # behind inflating every unrealised figure too.
    #
    # Key on the SYMBOL, merging symbols that share an ISIN - NOT on the ISIN itself. A corporate
    # action can reissue the ISIN under an unchanged symbol: a 1:2 split can move a stock to a new
    # ISIN while its symbol stays the same, and an ISIN key would wrongly split one continuous holding into
    # two queues. Symbol-with-ISIN-merge handles both directions.
    isin_syms = {}
    for t in trades:
        if t.get('isin'):
            isin_syms.setdefault(t['isin'], []).append(t['s'])
    renames = {}
    for isin, syms in isin_syms.items():
        uniq = list(dict.fromkeys(syms))
        if len(uniq) > 1:
            canon = uniq[-1]                      # the most recently used name
            for s in uniq[:-1]:
                renames[s] = canon
    if renames:
        for t in trades:
            if t['s'] in renames:
                t['s'] = renames[t['s']]
        if verbose:
            for old, new in sorted(renames.items()):
                print(f'    renamed {old} -> {new} (same ISIN); without this its buys and sells '
                      f'would never match')

    # A hole of two or more consecutive months between the first and last trade is worth naming:
    # FIFO across it will report uncosted sells that are really just a missing export.
    months = sorted({t['d'][:7] for t in trades})
    gaps = []
    if months:
        y, m = map(int, months[0].split('-'))
        ey, em = map(int, months[-1].split('-'))
        run = []
        while (y, m) <= (ey, em):
            key = f'{y:04d}-{m:02d}'
            if key not in months:
                run.append(key)
            else:
                if len(run) >= 2:
                    gaps.append((run[0], run[-1]))
                run = []
            m += 1
            if m > 12:
                m, y = 1, y + 1
        if len(run) >= 2:
            gaps.append((run[0], run[-1]))

    if verbose:
        print(f'# tradebook: {len(trades)} unique trades from {len(files)} file(s), '
              f'{trades[0]["d"]} to {trades[-1]["d"]}')
        for name, n in files:
            print(f'    {name:<36}{n:>5} new')
        covered = _covered
        unexplained = [g for g in gaps if g not in KNOWN_QUIET and not covered(g)]
        if COVERAGE:
            lo, hi = min(c[0] for c in COVERAGE), max(c[1] for c in COVERAGE)
            print(f'  exports declare coverage {lo} .. {hi}')
        if unexplained:
            print('  !! months with no trades - could be a missing export, unconfirmed:')
            for a, b in unexplained:
                print(f'       {a} .. {b}')
        for g in gaps:
            if g in KNOWN_QUIET:
                print(f'  quiet {g[0]} .. {g[1]} - {KNOWN_QUIET[g]}')
            elif covered(g):
                print(f'  quiet {g[0]} .. {g[1]} - inside a declared export range, so no trades happened')
        print()
    covered = _covered
    return trades, files, [g for g in gaps if g not in KNOWN_QUIET and not covered(g)]


if __name__ == '__main__':
    trades, files, gaps = load_all()
    c = Counter(t['t'] for t in trades)
    print(f'  {c["buy"]} buys, {sum(v for k, v in c.items() if k != "buy")} sells')
    print(f'  {len({t["s"] for t in trades})} distinct symbols')
    # NOT "every id is unique" - that would just assert that the dedupe key is a key, which is
    # vacuous. Assert the thing that actually matters: no two DIFFERENT trades share a dedupe key.
    keys = {(t['d'], t['x'], t['id']) for t in trades}
    assert len(keys) == len(trades), 'two different trades share a dedupe key'
    print(f'\nselfcheck ok: {len(trades)} trades, {len(keys)} distinct (date, exchange, id) keys')


# ================================================================== the ONE FIFO
# Before this existed there were three Python FIFOs (attribute_sells.py, realised.py, test_money.py)
# plus the browser's, and only two of them applied corporate actions - so test_money partly tested
# itself, and realised.py silently disagreed with attribute_sells.py. Every Python caller uses this.
#
# Corporate actions, from _smallcase_map.json -> corporate_actions. A tradebook sees only TRADES, but
# these change the share count with no trade row:
#   split   {s, ratio, effective}           every lot held on `effective`: qty x ratio, price / ratio.
#           Total cost is unchanged, which is exactly why no value-based check can ever see a miss.
#   bonus   {s, ratio, record, effective}   `ratio` new shares per 1 held at the close of `record`,
#           credited on `effective` (allotment) at NIL cost as a NEW lot at the BACK of the queue.
#           Entitlement is fixed on the RECORD date: a share bought after record and before allotment
#           earns nothing. Nil cost + back of queue is also the correct TAX treatment - bonus shares
#           take cost 0 and a holding period from allotment, and tax FIFO runs by demat credit date.
#   credit  {s, qty, price, effective}      shares that arrived without a trade: a rights entitlement,
#           an IPO allotment. Appended as a lot at `price` (0 for a free entitlement).
#   demerger {s, child, ratio, cost_fraction, record, effective}   each parent lot held at the close
#           of `record` spawns ratio x qty CHILD shares carrying cost_fraction of that lot's cost, and
#           the parent lot keeps the rest. Total cost is preserved; the child inherits the parent lot's
#           purchase date (a demerger carries the holding period over). Without it the child shows
#           more shares than were ever bought, and the parent's later sale is costed too high.
# Measured misses this fixes: a 1:1 bonus left the bonus shares looking "bought before the export
# window" and booked a large loss on a sale that made a profit; a rights entitlement was left uncosted.

def fifo(trades, actions=(), norm=lambda s: s, until=None, log=print):
    """FIFO over date/time-sorted trades, applying corporate actions in date order.

    Returns (matched, uncosted, lots, ca_added):
      matched  [{s, d, bought, q, buy, sell, pnl, proceeds, cost}]   one row per lot slice
      uncosted [{s, d, q, sell, proceeds}]   shares sold with NO lot behind them
      lots     {symbol: deque[[qty, price, date]]}                   what is still open
      ca_added {symbol: shares}   created by actions, which nobody bought - needed for any
               share-conservation check, since `bought == matched + open` is false once they apply
    """
    from collections import defaultdict, deque
    from datetime import date as _date
    from itertools import groupby
    acts = sorted(actions, key=lambda a: (a['effective'], a.get('record', '')))
    lots, matched, uncosted = defaultdict(deque), [], []
    known = {'split', 'bonus', 'credit', 'demerger'}
    for a in acts:
        if a['type'] not in known:
            raise ValueError(f'unknown corporate action type {a["type"]!r}')
    ca_added = defaultdict(float)
    snap, done = {}, set()

    def apply(upto):
        # `upto` is the date of the trade ABOUT to be processed, so every trade dated before it -
        # including all of the record date - is already in the queue.
        for i, a in enumerate(acts):
            if i in done:
                continue
            sym = norm(a['s'])
            dq = lots[sym]
            # Entitlement is fixed at the CLOSE of the record date, so wait until strictly after it.
            if a['type'] == 'demerger' and (a.get('record') or a['effective']) >= upto:
                continue
            if a['type'] == 'bonus':
                rec = a.get('record') or a['effective']
                if i not in snap and rec < upto:
                    snap[i] = sum(l[0] for l in dq)          # entitlement fixed at close of record
                if i not in snap or a['effective'] > upto:
                    continue
                extra = snap[i] * float(a['ratio'])
                if extra <= 0:
                    log(f'  !! {a["s"]} bonus on {rec}: nothing held on the record date - check the '
                        f'symbol or the date, it was NOT applied')
                else:
                    dq.append([extra, 0.0, a['effective']])
                    ca_added[sym] += extra
            elif a['effective'] > upto:
                continue
            elif a['type'] == 'split':
                r = float(a['ratio'])
                before = sum(l[0] for l in dq)
                if not before:
                    log(f'  !! {a["s"]} split on {a["effective"]}: no lots held - check the symbol '
                        f'or the date, it was NOT applied')
                for lot in dq:
                    lot[0] *= r
                    lot[1] /= r
                ca_added[sym] += before * (r - 1)
            elif a['type'] == 'credit':
                dq.append([float(a['qty']), float(a.get('price', 0.0)), a['effective']])
                ca_added[sym] += float(a['qty'])
            elif a['type'] == 'demerger':
                if not dq:
                    log(f'  !! {a["s"]} demerger on {a["effective"]}: no parent lots held - NOT applied')
                f, r, child = float(a['cost_fraction']), float(a['ratio']), norm(a['child'])
                for lot in dq:
                    cq = lot[0] * r
                    lots[child].append([cq, lot[1] * f / r, lot[2]])
                    ca_added[child] += cq
                    lot[1] *= (1 - f)
            done.add(i)

    def sell(s, d, q, px):
        dq, left = lots[s], q
        while left > 1e-9 and dq:
            lot = dq[0]
            take = min(left, lot[0])
            matched.append({'s': s, 'd': d, 'bought': lot[2], 'q': take,
                            'buy': lot[1], 'sell': px, 'cost': take * lot[1],
                            'proceeds': take * px, 'pnl': take * (px - lot[1])})
            lot[0] -= take
            left -= take
            if lot[0] <= 1e-9:
                dq.popleft()
        if left > 1e-9:
            uncosted.append({'s': s, 'd': d, 'q': left, 'sell': px, 'proceeds': left * px})

    # SAME-DAY NETTING. Zerodha settles a symbol bought AND sold on one day as INTRADAY on the day's
    # totals: min(bought, sold) is matched at the day's average buy vs average sell (speculative income
    # for tax, slab rate), and only the remainder touches holdings - a net sell comes out of the oldest
    # lots, a net buy becomes ONE lot at the day's average buy. Plain FIFO instead sold every share out
    # of old lots and queued the buys, so symbols traded both ways on one day drifted from Kite's
    # average by thousands each and their realised was booked as delivery gains. This rule reproduces
    # Kite's averages to the rupee. Must stay identical to baskets.html and dashboard.html.
    for d, day in groupby(trades, key=lambda t: t['d']):
        apply(d)
        day = list(day)
        tot = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])          # buy q, buy value, sell q, sell value
        for tr in day:
            k = 0 if tr['t'] == 'buy' else 2
            tot[tr['s']][k] += tr['q']
            tot[tr['s']][k + 1] += tr['q'] * tr['p']
        for tr in day:
            bq, _, sq, _ = tot[tr['s']]
            if bq and sq:
                continue                                          # both sides today: netted below
            if tr['t'] == 'buy':
                lots[tr['s']].append([tr['q'], tr['p'], tr['d']])
            else:
                sell(tr['s'], d, tr['q'], tr['p'])
        for s, (bq, bv, sq, sv) in tot.items():
            if not (bq and sq):
                continue
            bp, sp, m = bv / bq, sv / sq, min(bq, sq)
            matched.append({'s': s, 'd': d, 'bought': d, 'q': m, 'buy': bp, 'sell': sp, 'cost': m * bp,
                            'proceeds': m * sp, 'pnl': m * (sp - bp), 'intraday': True})
            if sq > bq:
                sell(s, d, sq - bq, sp)
            elif bq > sq:
                lots[s].append([bq - sq, bp, d])
    # Flush actions dated after the last trade but not in the future, so open lots match what is
    # held TODAY. Without this an action with no later trade never applied at all.
    apply(until or _date.today().isoformat())
    return matched, uncosted, {s: d for s, d in lots.items() if d}, dict(ca_added)


def adjusted_trades(trades, actions=(), norm=lambda s: s):
    """The same corporate actions, expressed as a TRADE LIST any plain FIFO can consume - for a
    consumer that cannot run fifo() itself (dashboard.html's own FIFO, which has no notion of them).

      split   every trade of that symbol dated BEFORE `effective`: qty x ratio, price / ratio.
              A FIFO match k@sell against a lot q@buy becomes kr@sell/r against qr@buy/r - the pnl
              k(sell-buy) is unchanged, and post-split sells see exactly the lots fifo() leaves.
      bonus   a synthetic nil-cost BUY on `effective`, sized on holdings at the close of `record`.
      credit  a synthetic BUY on `effective` at its stated price.
    Synthetic rows carry id 'CA:<symbol>:<date>' and time 00:00:00 so they sort before that day's
    real trades - the same moment fifo() applies them. test_money.py asserts plain-FIFO over this
    list reproduces fifo() to the paisa, which is the only reason it is safe to hand to a browser.
    """
    out = [dict(t) for t in trades]
    for a in sorted(actions, key=lambda a: (a['effective'], a.get('record', ''))):
        sym = norm(a['s'])
        if a['type'] == 'split':
            r = float(a['ratio'])
            for t in out:
                if t['s'] == sym and t['d'] < a['effective']:
                    t['q'] *= r
                    t['p'] /= r
            continue
        if a['type'] == 'demerger':
            # Only lots still OPEN at the close of `record` carry cost to the child, and FIFO decides
            # which those are - so replay the parent's queue to the record date. A buy that is partly
            # consumed is SPLIT: the consumed part keeps its price, the open part (id + '~dm', sorting
            # after it at the same timestamp) is marked down by cost_fraction. Scaling every earlier buy
            # instead would re-price sales made BEFORE the demerger.
            rec = a.get('record') or a['effective']
            f, r, child = float(a['cost_fraction']), float(a['ratio']), norm(a['child'])
            out.sort(key=lambda x: (x['d'], x.get('tm', ''), x['t'] != 'buy', x['x'], str(x['id'])))
            q_ = []
            for t in out:
                if t['s'] != sym or t['d'] > rec:
                    continue
                if t['t'] == 'buy':
                    q_.append([t, t['q']])
                    continue
                left = t['q']
                while left > 1e-9 and q_:
                    take = min(left, q_[0][1])
                    q_[0][1] -= take
                    left -= take
                    if q_[0][1] <= 1e-9:
                        q_.pop(0)
            for t, rem in q_:
                orig = t['p']
                if rem < t['q'] - 1e-9:
                    part = dict(t, q=rem, p=orig * (1 - f), id=f"{t['id']}~dm")
                    t['q'] -= rem
                    out.append(part)
                else:
                    t['p'] = orig * (1 - f)
                out.append({'s': child, 'd': rec, 't': 'buy', 'q': rem * r, 'p': orig * f / r,
                            'id': f"CA:{child}:{rec}:{t['id']}", 'x': 'CA', 'tm': rec + 'T23:59:59',
                            'isin': '', 'ca': 'demerger'})
            continue
        if a['type'] == 'bonus':
            rec = a.get('record') or a['effective']
            held = sum(t['q'] if t['t'] == 'buy' else -t['q'] for t in out
                       if t['s'] == sym and t['d'] <= rec)
            q, p = held * float(a['ratio']), 0.0
        elif a['type'] == 'credit':
            q, p = float(a['qty']), float(a.get('price', 0.0))
        else:
            raise ValueError(f'unknown corporate action type {a["type"]!r}')
        if q > 0:
            out.append({'s': sym, 'd': a['effective'], 't': 'buy', 'q': q, 'p': p,
                        'id': f"CA:{sym}:{a['effective']}", 'x': 'CA', 'tm': a['effective'] + 'T00:00:00',
                        'isin': '', 'ca': a['type']})
    out.sort(key=lambda x: (x['d'], x.get('tm', ''), x['t'] != 'buy', x['x'], str(x['id'])))
    return out


def map_fills(M, after, norm=lambda s: s):
    """Fills recorded in the map's rebalance records, dated AFTER `after` (the last tradebook trade), as
    trade rows fifo() can consume.

    A Zerodha export is T+1, so the day's trades are in no tradebook until tomorrow's download. Without
    these the day's sales were either missing (Python) or costed at the map's blended average (the
    pages' ledger top-up) - two conventions for one number, over a thousand apart on one day's trims. With them,
    realised is FIFO everywhere. Only dates after `after` are emitted, so when a newer export lands the
    real rows replace these and nothing is counted twice. Order ids are fixed-width and monotonic within
    a day, so `tm` = date + 'T~' + order_id orders same-day fills correctly and after any real trade."""
    out = []
    for b in M['batches'].values():
        for rk, rec in b.items():
            if not (rk.startswith(('rebalance_', 'topup_', 'exit_')) and isinstance(rec, dict)):
                continue
            d = rec.get('date')
            if not d or d <= after:
                continue
            for s, f in (rec.get('fills') or {}).items():
                if not isinstance(f, dict) or 'qty' not in f:
                    continue
                oid = str(f.get('order_id', ''))
                out.append({'s': norm(s), 'd': d, 't': 'buy' if str(f.get('side', 'BUY')).upper() == 'BUY' else 'sell',
                            'q': float(f['qty']), 'p': float(f['avg_fill']), 'id': oid, 'x': 'MAP',
                            'tm': f'{d}T~{oid}', 'isin': ''})
    return sorted(out, key=lambda x: (x['d'], x['tm']))
