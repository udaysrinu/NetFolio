#!/usr/bin/env python3
"""Zerodha contract notes from Gmail -> every trade, with its charges. No more tradebook uploads.

A broker must email a contract note for every day you trade (SEBI), so the mailbox holds a complete
trade record that keeps arriving whether or not this machine was on. Each note lists every fill
(Annexure A: order no, trade no, time, exchange, quantity, price) and the day's charges (STT,
exchange fees, GST, SEBI fees, stamp duty).

    python3 contract_notes.py [--since YYYY-MM-DD]   fetch new notes, parse, check, save -> _contract_notes.json
    python3 contract_notes.py --check                also compare against the tradebooks for days both cover
  --since limits the Gmail search (default: 45 days before the newest stored note, or the whole mailbox
  on the first run); one big fetch of years of notes is one long request that a timeout can lose.

Trade numbers are the tradebook's Trade IDs, so the two sources dedupe on id. The PDF is unlocked
with the PAN, read from the OS keychain (service "netfolio", account "pan") per run and never written
anywhere. Gmail access reuses an existing read-only consent from the keychain (StatementLens's),
so no second Google login is needed.

A note is REFUSED, never half-used, unless its own sums hold:
  1. per ISIN and side, the fills add up to the summary's quantity, and to quantity x its gross
     average price (fills are gross; the summary's totals are AFTER brokerage, which is charged on
     intraday and F&O but zero on delivery);
  2. gross sells - buys (from the fills) == the printed pay-in/pay-out obligation;
  3. obligation + charges == the printed net amount;
  4. the fills' own brokerage == the note's brokerage line (it is charged once, on that line).
"""
import json, os, re, subprocess, sys
from collections import defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
P = lambda n: os.path.join(HERE, n)
STORE = P('_contract_notes.json')            # private: git-ignored by the allowlist
QUERY = 'from:no-reply-contract-notes@reportsmailer.zerodha.net has:attachment filename:pdf'


class ContractNoteError(ValueError):
    pass


_NUM = r'\(?[−-]?[\d,]+\.\d{2,4}\)?'
num = lambda s: (-1 if s.startswith('(') or s.lstrip('(').startswith(('−', '-')) else 1) * float(
    re.sub(r'[(),−-]', '', s))
FILL = re.compile(r'^(\d{10,20})\s+(\d\d:\d\d:\d\d)\s+(\d+)\s+(\d\d:\d\d:\d\d)\s+(\S+)/([A-Z]{2}[A-Z0-9]{9}\d)\s+'
                  r'([BS])\s+(NSE|BSE)\s+(\d+)\s+(' + _NUM + r')\s+(' + _NUM + r')\s+(?:(' + _NUM + r')\s+)?(' + _NUM + r')\s*$')
SUMMARY = re.compile(r'^([A-Z]{2}[A-Z0-9]{9}\d)\s+(.+?)\s+(\d+)\s+(' + _NUM + r')\s+(' + _NUM + r')\s+(' + _NUM + r')\s+(' + _NUM +
                     r')\s+(\d+)\s+(' + _NUM + r')\s+(' + _NUM + r')\s+(' + _NUM + r')\s+(' + _NUM + r')\s+([−-]?\d+)\s+(' + _NUM + r')\s*$')
CHARGES = (('brokerage', r'^Taxable value of Supply \(Brokerage\)'), ('exchange', r'^Exchange transaction charges'),
           ('clearing', r'^Clearing charges'), ('cgst', r'^CGST'), ('sgst', r'^SGST'), ('igst', r'^IGST'),
           ('stt', r'^Securities transaction tax'), ('sebi', r'^SEBI turnover fees'), ('stamp', r'^Stamp duty'))


def _symbol(desc):
    """'AXISCADES-EQ' -> AXISCADES; 'MTARTECH-BE' -> MTARTECH-BE (the tradebook keeps non-EQ series)."""
    m = re.match(r'^(.+?)-([A-Z0-9]{1,2})$', desc)
    if not m:
        return desc
    return m.group(1) if m.group(2) == 'EQ' else desc


def parse(text):
    """One contract note's text -> {date, trades, charges, obligation, net}. Raises if its sums fail."""
    m = re.search(r'Trade Date:\s*(\d\d)/(\d\d)/(\d{4})', text)
    if not m:
        raise ContractNoteError('no trade date')
    day = f'{m.group(3)}-{m.group(2)}-{m.group(1)}'
    trades, summ, charges = [], {}, {}
    obligation = net = None
    for line in text.splitlines():
        line = line.strip()
        f = FILL.match(line)
        if f:
            oid, _, tid, ttime, desc, isin, side, ex, q, brok, rate, _close, total = f.groups()
            trades.append({'s': _symbol(desc), 'd': day, 't': 'buy' if side == 'B' else 'sell', 'q': float(q),
                           'p': num(rate), 'id': tid, 'x': ex, 'tm': f'{day}T{ttime}', 'isin': isin, 'oid': oid,
                           'brokerage': abs(num(brok)), 'value': abs(num(total))})
            continue
        s = SUMMARY.match(line)
        if s:
            g = s.groups()
            summ[g[0]] = {'name': g[1], 'buy_q': int(g[2]), 'buy_wap': num(g[3]), 'buy_after': num(g[6]),
                          'sell_q': int(g[7]), 'sell_wap': num(g[8]), 'sell_after': num(g[11])}
            continue
        if line.startswith('Pay in/Pay out obligation'):
            obligation = num(re.findall(_NUM, line)[-1])
        elif line.startswith('Net amount receivable'):
            net = num(re.findall(_NUM, line)[-1])
        else:
            for k, pat in CHARGES:
                if re.match(pat, line):
                    vals = re.findall(_NUM, line)
                    charges[k] = num(vals[-1]) if vals else 0.0
    if not trades or obligation is None or net is None:
        raise ContractNoteError(f'{day}: missing fills, obligation or net amount')
    # 1. fills add up to the summary, per ISIN and side
    agg = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    for t in trades:
        a = agg[t['isin']]; i = 0 if t['t'] == 'buy' else 2
        a[i] += t['q']; a[i + 1] += t['value']
    for isin, sm in summ.items():
        bq, bv, sq, sv = agg.get(isin, [0, 0, 0, 0])
        tol = lambda q: 0.01 + 0.00005 * q          # the WAP is printed to 4 decimals
        if (abs(bq - sm['buy_q']) > 1e-9 or abs(sq - sm['sell_q']) > 1e-9
                or abs(bv - sm['buy_q'] * sm['buy_wap']) > tol(bq) or abs(sv - sm['sell_q'] * sm['sell_wap']) > tol(sq)):
            raise ContractNoteError(f'{day}: fills for {sm["name"]} do not add up to the summary')
    if set(agg) - set(summ):
        raise ContractNoteError(f'{day}: fills for an ISIN missing from the summary')
    # 2. sells - buys == obligation; 3. obligation + charges == net
    flow = sum(t['value'] * (1 if t['t'] == 'sell' else -1) for t in trades)
    if abs(flow - obligation) > 0.05:
        raise ContractNoteError(f'{day}: sells - buys = {flow:.2f} but the note says {obligation:.2f}')
    brok = sum(t['brokerage'] for t in trades)
    if abs(brok + charges.get('brokerage', 0.0)) > 0.05 + 0.0001 * len(trades):
        raise ContractNoteError(f'{day}: fills carry {brok:.2f} of brokerage, the note charges {-charges.get("brokerage", 0):.2f}')
    if abs(obligation + sum(charges.values()) - net) > 0.05:
        raise ContractNoteError(f'{day}: obligation + charges != net amount')
    for t in trades:
        del t['value']
    return {'date': day, 'trades': trades, 'charges': charges, 'obligation': obligation, 'net': net}


def fetch(since=None):
    from inboxkit.inbox import GmailDocuments
    from inboxkit.pdf import PdfDecryptor, PdfTextExtractor
    from inboxkit.secrets import SecretStore
    pan = SecretStore('netfolio').get('pan')
    if not pan:
        sys.exit('no PAN in the keychain: security add-generic-password -s netfolio -a pan -w')
    have = json.load(open(STORE)) if os.path.exists(STORE) else {}
    if since is None and have:
        from datetime import date, timedelta
        since = (date.fromisoformat(max(n['date'] for n in have.values())) - timedelta(days=45)).isoformat()
    q = QUERY + (f' after:{since.replace("-", "/")}' if since else '')
    src = GmailDocuments(q, app='netfolio', store=SecretStore('statementlens'))
    docs = src.fetch(skip_ids=set(have))
    new, bad = 0, []
    for d in docs:
        try:
            note = parse(PdfTextExtractor().extract(PdfDecryptor().decrypt(d.data, {'pan': pan})))
        except Exception as e:                      # refuse the note, keep going, say so
            bad.append(f'{d.subject}: {e}')
            continue
        note.update(subject=d.subject, message_id=d.message_id)
        have[d.message_id] = note; new += 1
    json.dump(have, open(STORE, 'w'), indent=1)
    days = sorted(n['date'] for n in have.values())
    print(f'{new} new note(s); {len(have)} stored, {days[0] if days else "-"} .. {days[-1] if days else "-"}'
          + (' (Gmail result truncated: raise the limit)' if src.truncated else ''))
    for b in bad:
        print('  REFUSED', b)
    return have, bad


def trades():
    """Every stored fill, in the tradebook's row shape (s d t q p id x tm isin)."""
    have = json.load(open(STORE)) if os.path.exists(STORE) else {}
    return [{k: v for k, v in t.items() if k != 'oid'} for n in have.values() for t in n['trades']]


if __name__ == '__main__':
    since = sys.argv[sys.argv.index('--since') + 1] if '--since' in sys.argv else None
    have, bad = fetch(since)
    if '--check' in sys.argv:
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            from tradebook import load_all
            TR, _, _ = load_all()
        last = max(t['d'] for t in TR)
        tb = {t['id']: t for t in TR}
        cn = [t for t in trades() if t['d'] <= last]
        miss = [t for t in cn if t['id'] not in tb]
        diff = [t for t in cn if t['id'] in tb and (abs(tb[t['id']]['q'] - t['q']) > 1e-9 or abs(tb[t['id']]['p'] - t['p']) > 0.005
                                                     or tb[t['id']]['t'] != t['t'])]
        days = sorted({t['d'] for t in cn})
        tbd = [t for t in TR if t['d'] in set(days)]
        print(f'check vs tradebooks on {len(days)} shared day(s): {len(cn)} fills in notes, {len(tbd)} in tradebooks, '
              f'{len(miss)} not in a tradebook, {len(diff)} differ')
        for t in (miss + diff)[:10]:
            print('   ', t)
        sys.exit(1 if (miss or diff or bad) else 0)
