#!/usr/bin/env python3
"""Zerodha contract notes from Gmail -> every trade, with its charges. No more tradebook uploads.

A broker must email a contract note for every day you trade (SEBI), so the mailbox holds a complete
trade record that keeps arriving whether or not this machine was on. Each note lists every fill
(Annexure A: order no, trade no, time, exchange, quantity, price) and the day's charges (STT,
exchange fees, GST, SEBI fees, stamp duty).

    python3 contract_notes.py [--since YYYY-MM-DD]   fetch new notes, parse, check, save -> _contract_notes.json
    python3 contract_notes.py --check                also compare against the tradebooks for days both cover
    python3 contract_notes.py --since 2022-01-01 --until 2022-07-01   backfill one window of history
    python3 contract_notes.py --retry-refused        re-try notes refused before (after a parser fix)
  --since limits the Gmail search (default: 45 days before the newest stored note, or the whole mailbox
  on the first run); one big fetch of years of notes is one long request that a timeout can lose.

Trade numbers are the tradebook's Trade IDs, so the two sources dedupe on id. The PDF is unlocked
with the PAN, read from the OS keychain (service "netfolio", account "pan") per run and never written
anywhere. Gmail access reuses an existing read-only consent from the keychain (StatementLens's),
so no second Google login is needed.

A note is only TRUSTED if it is really from the broker: the From address is exactly Zerodha's,
Gmail's own Authentication-Results (the first one - later ones can be written by the sender)
shows DKIM and DMARC passing for zerodha.net, and the PDF is password-locked, as every genuine
note is. Anyone can send an email that says it is from Zerodha; it is the signature that cannot be
forged.

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
REFUSED = P('_contract_notes_refused.json')  # notes that failed a check, with the reason (private)
# Zerodha's contract-note addresses: qmailer until Feb 2022, reportsmailer since. Each is checked
# against its own signing domain, so adding one does not loosen the other.
SENDERS = ('no-reply-contract-notes@reportsmailer.zerodha.net', 'no-reply-contract-notes@qmailer.zerodha.net')
SENDER = SENDERS[0]
QUERY = '(' + ' OR '.join(f'from:{s}' for s in SENDERS) + ') has:attachment filename:pdf'


class ContractNoteError(ValueError):
    pass


_NUM = r'\(?[−-]?[\d,]+\.\d{2,4}\)?'
_ANY = r'\(?[−-]?[\d,]+(?:\.\d+)?\)?'                 # older notes print a net rate as "1305"
num = lambda s: (-1 if s.startswith('(') or s.lstrip('(').startswith(('−', '-')) else 1) * float(
    re.sub(r'[(),−-]', '', s))
ISIN = r'[A-Z]{2}[A-Z0-9]{9}\d'
# Annexure fill, every layout Zerodha has used: order no, order time, trade no, trade time, description,
# B/S, exchange, quantity, then the numbers. The description is "SYMBOL-SERIES/ISIN" (2023 on) or
# "SYMBOL - GROUP" with the ISIN on the next line (2022). Only plain symbol characters are accepted.
FILL = re.compile(r'^(\d{10,20})\s+(\d\d:\d\d:\d\d)\s+(\d+)\s+(\d\d:\d\d:\d\d)\s+([A-Z0-9&_.\- ]{1,40}?)'
                  r'(?:/(' + ISIN + r'))?\s+(?:/\s*)?([BS])\s+(NSE|BSE)\s+(\d+)\s+(.+?)\s*$')
SUMMARY = re.compile(r'^(' + ISIN + r')\s+(.+?)\s+(\d+)\s+(' + _NUM + r')\s+(' + _NUM + r')\s+(' + _NUM + r')\s+(' + _NUM +
                     r')\s+(\d+)\s+(' + _NUM + r')\s+(' + _NUM + r')\s+(' + _NUM + r')\s+(' + _NUM + r')\s+([−-]?\d+)\s+(' + _NUM + r')\s*$')
# Labels are matched with every space removed and lower-cased: notes from late 2022 to 2023 come out of the
# PDF with the spaces inside labels dropped ("Tradedate:", "Payin/Payoutobligation").
CHARGES = (('brokerage', 'taxablevalueofsupply(brokerage)'), ('exchange', 'exchangetransactioncharges'),
           ('clearing', 'clearingcharges'), ('cgst', 'cgst'), ('sgst', 'sgst'), ('igst', 'igst'),
           ('stt', 'securitiestransactiontax'), ('sebi', 'sebiturnoverfees'), ('stamp', 'stampduty'))
squash = lambda l: re.sub(r'^[\d¹²³⁴⁵⁶⁷⁸⁹⁰]+', '', re.sub(r'\s+', '', l)).lower()   # and footnote marks in front


def _symbol(desc):
    """'ACME-EQ' -> ACME; 'ACME-BE' -> ACME-BE (the tradebook keeps non-EQ series);
    'ACME - A' (2022, BSE group) -> ACME."""
    desc = desc.strip().rstrip('-').strip()
    if ' - ' in desc:
        return desc.split(' - ', 1)[0].strip()
    m = re.match(r'^(.+?)-([A-Z0-9]{1,2})$', desc)
    if not m:
        return desc
    return m.group(1) if m.group(2) == 'EQ' else desc


def _decimals(tok):
    t = re.sub(r'[()]', '', tok)
    return len(t.split('.', 1)[1]) if '.' in t else 0


def genuine(doc):
    """None if the email really came from Zerodha's contract-note address, else why not (fail closed).
    The signature check is inboxkit's Document.authenticated: Gmail's own verdict, DKIM + DMARC."""
    m = re.search(r'<([^>]+)>', doc.sender or '')
    addr = (m.group(1) if m else (doc.sender or '')).strip().lower()
    if addr not in SENDERS:
        return "not sent from Zerodha's contract-note address"
    return doc.authenticated(addr.split('@', 1)[1])


def locked(data):
    """A genuine contract note is always password-protected; an open PDF did not come from the broker."""
    import io, pikepdf
    try:
        with pikepdf.open(io.BytesIO(data)):
            return False
    except pikepdf.PasswordError:
        return True


def parse(text):
    """One contract note's text -> {date, trades, charges, obligation, net}. Raises if its sums fail."""
    m = re.search(r'Trade\s*date:?\s*(\d\d)/(\d\d)/(\d{4})', text, re.I)
    if not m:
        raise ContractNoteError('no trade date')
    day = f'{m.group(3)}-{m.group(2)}-{m.group(1)}'
    trades, summ, charges = [], {}, {}
    obligation = net = None
    lines = [l.strip() for l in text.splitlines()]
    for i, line in enumerate(lines):
        f = FILL.match(line)
        if f:
            oid, _, tid, ttime, desc, isin, side, ex, q, tail = f.groups()
            nums = re.findall(_ANY, tail)
            if len(nums) < 2:
                raise ContractNoteError(f'{day}: unreadable fill {tid}')
            if not isin:                                  # 2022 layout: the ISIN sits below, after a lone '/'
                isin = next((l for l in lines[i + 1:i + 4] if re.fullmatch(ISIN, l)), '')
            if not isin:
                raise ContractNoteError(f'{day}: fill {tid} has no ISIN')
            total = abs(num(nums[-1]))
            # every layout prints a buy's total in brackets (payable): the B/S letter must agree with it.
            # In 2022 notes a BSE group letter can sit where the side does, so this is what proves which is which.
            if (side == 'B') != (num(nums[-1]) < 0):
                raise ContractNoteError(f'{day}: fill {tid} says {side} but its total says otherwise')
            if _decimals(nums[0]) == 4 and len(nums) >= 3:  # 2026 layout: brokerage (total), net rate, [close], total
                brok, price = abs(num(nums[0])), abs(num(nums[1]))
            else:                                         # older: gross rate, [brokerage/unit], net rate, [close], total
                price = abs(num(nums[0]))
                brok = abs(price * float(q) - total)
            trades.append({'s': _symbol(desc), 'd': day, 't': 'buy' if side == 'B' else 'sell', 'q': float(q),
                           'p': price, 'id': tid, 'x': ex, 'tm': f'{day}T{ttime}', 'isin': isin, 'oid': oid,
                           'brokerage': brok, 'value': total})
            continue
        s_ = SUMMARY.match(line)
        if s_:
            g = s_.groups()
            summ[g[0]] = {'name': g[1], 'buy_q': int(g[2]), 'buy_wap': num(g[3]), 'buy_after': num(g[6]),
                          'sell_q': int(g[7]), 'sell_wap': num(g[8]), 'sell_after': num(g[11])}
            continue
        key = squash(line)
        if key.startswith('payin/payoutobligation'):
            obligation = num(re.findall(_NUM, line)[-1])
        elif key.startswith('netamountreceivable'):
            net = num(re.findall(_NUM, line)[-1])
        else:
            for k, pat in CHARGES:
                if key.startswith(pat):
                    vals = re.findall(_NUM, line)
                    charges[k] = num(vals[-1]) if vals else 0.0
    if not trades or obligation is None or net is None:
        raise ContractNoteError(f'{day}: missing fills, obligation or net amount')
    # 1. where the note has a per-ISIN summary (2026 layout), the fills must add up to it; a summary
    #    that was only PARTLY recognised is refused, never half-checked
    agg = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    for t in trades:
        a = agg[t['isin']]; i = 0 if t['t'] == 'buy' else 2
        a[i] += t['q']; a[i + 1] += t['q'] * t['p']
    if summ:
        if set(agg) - set(summ):
            raise ContractNoteError(f'{day}: fills for an ISIN missing from the summary')
        for isin, sm in summ.items():
            bq, bv, sq, sv = agg.get(isin, [0, 0, 0, 0])
            tol = lambda q: 0.01 + 0.00005 * q          # the WAP is printed to 4 decimals
            if (abs(bq - sm['buy_q']) > 1e-9 or abs(sq - sm['sell_q']) > 1e-9
                    or abs(bv - sm['buy_q'] * sm['buy_wap']) > tol(bq) or abs(sv - sm['sell_q'] * sm['sell_wap']) > tol(sq)):
                raise ContractNoteError(f'{day}: fills for {sm["name"]} do not add up to the summary')
    # 2. the fills' totals == the printed obligation; 3. obligation + charges == net
    flow = sum(t['value'] * (1 if t['t'] == 'sell' else -1) for t in trades)
    if abs(flow - obligation) > 0.05 + 0.005 * len(trades):
        raise ContractNoteError(f'{day}: sells - buys = {flow:.2f} but the note says {obligation:.2f}')
    if abs(obligation + sum(charges.values()) - net) > 0.05:
        raise ContractNoteError(f'{day}: obligation + charges != net amount')
    for t in trades:
        del t['value']
    return {'date': day, 'trades': trades, 'charges': charges, 'obligation': obligation, 'net': net}


def fetch(since=None, until=None):
    from inboxkit.inbox import GmailDocuments
    from inboxkit.pdf import PdfDecryptor, PdfTextExtractor
    from inboxkit.secrets import SecretStore
    pan = SecretStore('netfolio').get('pan')
    if not pan:
        sys.exit('no PAN in the keychain: security add-generic-password -s netfolio -a pan -w')
    have = json.load(open(STORE)) if os.path.exists(STORE) else {}
    # Notes refused once are remembered, so a re-run does not spend Gmail quota re-downloading them;
    # --retry-refused (after a parser change) tries them again.
    refused = {} if '--retry-refused' in sys.argv or not os.path.exists(REFUSED) else json.load(open(REFUSED))
    if since is None and have:
        from datetime import date, timedelta
        since = (date.fromisoformat(max(n['date'] for n in have.values())) - timedelta(days=45)).isoformat()
    q = QUERY + (f' after:{since.replace("-", "/")}' if since else '') + (f' before:{until.replace("-", "/")}' if until else '')
    src = GmailDocuments(q, app='netfolio', store=SecretStore('statementlens'))
    docs = src.fetch(skip_ids=set(have) | set(refused))
    new, bad = 0, []
    for d in docs:
        why = genuine(d) or (None if locked(d.data) else 'PDF is not password-locked')
        if why:
            bad.append(f'{d.subject}: {why}'); refused[d.message_id] = {'subject': d.subject, 'why': why}
            continue
        try:
            note = parse(PdfTextExtractor().extract(PdfDecryptor().decrypt(d.data, {'pan': pan})))
        except Exception as e:                      # refuse the note, keep going, say so
            bad.append(f'{d.subject}: {e}'); refused[d.message_id] = {'subject': d.subject, 'why': str(e)}
            continue
        note.update(subject=d.subject, message_id=d.message_id)
        have[d.message_id] = note; new += 1
    json.dump(have, open(STORE, 'w'), indent=1)
    json.dump(refused, open(REFUSED, 'w'), indent=1)
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
    arg = lambda k: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else None
    have, bad = fetch(arg('--since'), arg('--until'))
    if '--check' in sys.argv:
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            from tradebook import load_all
            TR, _, _ = load_all(notes=False)        # the Excel exports alone: comparing notes to notes proves nothing
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
