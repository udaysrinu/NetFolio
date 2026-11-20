"""Public tests: FIFO on made-up trades. No account data is needed or read.

The private invariants (test_money.py) run only where a real account's data exists. These pin the
same rules on synthetic trades, so anyone can run them: python3 tests/run.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from tradebook import fifo

T = lambda d, t, q, p, s='X': {'s': s, 'd': d, 't': t, 'q': q, 'p': p}
quiet = lambda m: None


def test_same_day_buy_and_sell_net_as_intraday_like_zerodha():
    # 5 bought and 8 sold on the same day: 5 match intraday at that day's prices (5 x 10), the other 3
    # come out of the oldest lot (3 x 30). On the 3rd, 1 of the 4 bought is sold the same day (1 x 5).
    m, u, lots, _ = fifo([T('2026-01-01', 'buy', 10, 100), T('2026-01-02', 'buy', 5, 120),
                          T('2026-01-02', 'sell', 8, 130), T('2026-01-03', 'buy', 4, 110),
                          T('2026-01-03', 'sell', 1, 115)], log=quiet)
    intra = sum(x['pnl'] for x in m if x.get('intraday'))
    deliv = sum(x['pnl'] for x in m if not x.get('intraday'))
    assert abs(intra - 55) < 1e-9 and abs(deliv - 90) < 1e-9, (intra, deliv)
    assert [(round(q, 6), round(p, 6)) for q, p, _ in lots['X']] == [(7, 100), (3, 110)] and not u


def test_cost_is_conserved():
    # cost of everything bought == cost of what was sold + cost of what is still open
    tr = [T('2026-01-01', 'buy', 10, 100), T('2026-02-01', 'buy', 10, 140), T('2026-03-01', 'sell', 15, 150)]
    m, u, lots, _ = fifo(tr, log=quiet)
    bought = 10 * 100 + 10 * 140
    sold_cost = sum(x['q'] * x['buy'] for x in m)
    open_cost = sum(q * p for q, p, _ in lots['X'])
    assert abs(bought - sold_cost - open_cost) < 1e-6 and not u


def test_a_sell_with_no_buy_is_uncosted_never_profit():
    # "Never invent a cost": a sale whose buy is not in the data must not be counted as a gain
    m, u, lots, _ = fifo([T('2026-01-01', 'sell', 5, 100)], log=quiet)
    assert not m and u, (m, u)


def test_a_bonus_credit_adds_shares_at_zero_cost():
    act = [{'s': 'X', 'type': 'credit', 'qty': 10, 'price': 0, 'effective': '2026-02-01'}]
    m, u, lots, _ = fifo([T('2026-01-01', 'buy', 10, 100), T('2026-03-01', 'sell', 20, 60)], act, log=quiet)
    assert not u, u
    assert abs(sum(x['pnl'] for x in m) - (20 * 60 - 10 * 100)) < 1e-6     # 10 at cost 100 + 10 bonus at 0
