"""Public tests for contract_notes.py on a MADE-UP note: no real document, no Gmail, no PAN."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import contract_notes as cn

NOTE = """Trade Date: 16/10/2026 Settlement No: 1
INE000A01011 ACME 2 100.0000 0.0000 100.0000 200.00 0 0.0000 0.0000 0.0000 0.00 2 −200.00
INE000B01011 BETA 0 0.0000 0.0000 0.0000 0.00 3 50.0000 0.0000 50.0000 150.00 −3 150.00
Pay in/Pay out obligation (₹) (50.00) 0.00 (50.00)
Taxable value of Supply (Brokerage) (₹)2 (0.00) (0.00)
Securities transaction tax (₹) (1.00) (1.00)
Stamp duty (₹) (0.50) (0.50)
Net amount receivable/(payable by client) (₹) (51.50) 0.00 (51.50)
1000000000000001 10:00:00 111 10:00:00 ACME-EQ/INE000A01011 B NSE 2 0.0000 100.00 (200.00)
1000000000000002 10:00:01 112 10:00:01 BETA-EQ/INE000B01011 S NSE 3 0.0000 50.00 150.00
"""


def test_a_consistent_note_parses_into_tradebook_rows():
    n = cn.parse(NOTE)
    assert n['date'] == '2026-10-16' and n['obligation'] == -50 and n['net'] == -51.5
    assert [(t['s'], t['t'], t['q'], t['p'], t['id']) for t in n['trades']] == [
        ('ACME', 'buy', 2.0, 100.0, '111'), ('BETA', 'sell', 3.0, 50.0, '112')]


def test_a_note_whose_sums_fail_is_refused():
    for tamper in (NOTE.replace('B NSE 2 0.0000 100.00 (200.00)', 'B NSE 2 0.0000 90.00 (180.00)'),   # fill vs summary
                   NOTE.replace('obligation (₹) (50.00) 0.00 (50.00)', 'obligation (₹) (40.00) 0.00 (40.00)'),  # pay-in
                   NOTE.replace('(51.50) 0.00 (51.50)', '(52.50) 0.00 (52.50)')):                       # net vs charges
        try:
            cn.parse(tamper)
        except cn.ContractNoteError:
            continue
        raise AssertionError('a tampered note was accepted')


def test_a_symbol_that_could_carry_markup_never_parses_as_a_fill():
    evil = NOTE.replace('ACME-EQ/INE000A01011', '<img/src=x>-EQ/INE000A01011')
    try:
        cn.parse(evil)
    except cn.ContractNoteError:
        return
    raise AssertionError('a markup symbol was accepted')


def test_only_a_signed_mail_from_the_broker_is_genuine():
    from inboxkit.inbox import Document
    auth = ('mx.google.com; dkim=pass header.i=@reportsmailer.zerodha.net header.s=k header.b=x; '
            'dmarc=pass (p=QUARANTINE) header.from=reportsmailer.zerodha.net')
    good = [('Authentication-Results', auth), ('From', f'Broker <{cn.SENDER}>'), ('Subject', 's'), ('Date', 'd')]
    mk = lambda h: Document('m', 'n.pdf', b'', subject='s', sender=dict(h)['From'], headers=h)
    assert cn.genuine(mk(good)) is None
    spoof = [('Authentication-Results', auth.replace('dmarc=pass', 'dmarc=fail'))] + good[1:]
    assert cn.genuine(mk(spoof)), 'an unsigned mail passed'
    other = [good[0], ('From', 'Broker <notes@evil.example>')] + good[2:]
    assert cn.genuine(mk(other)), 'a different sender passed'
    old = 'no-reply-contract-notes@qmailer.zerodha.net'           # the pre-Feb-2022 address
    cross = [good[0], ('From', f'Broker <{old}>')] + good[2:]     # signed by reportsmailer, From qmailer
    assert cn.genuine(mk(cross)), "one address's signature vouched for the other"
    own = [(good[0][0], auth.replace('reportsmailer', 'qmailer')), ('From', f'Broker <{old}>')] + good[2:]
    assert cn.genuine(mk(own)) is None, 'the old address, signed by its own domain, was refused'
