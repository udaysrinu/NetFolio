# Netfolio

What you own and owe, priced and reconciled, for Indian investors. It starts with stocks and smallcase
baskets: every trade FIFO-matched the way Zerodha does it, cost basis that equals Kite's own average, and
daily performance against Nifty since your first trade. Mutual funds, NPS, FDs, loans and bank balances
come next, adding up to net worth.

Netfolio runs on your own machine. Your tradebooks, holdings and statements never leave it: this repo
is code only. Every file of personal data is git-ignored by an allowlist, so an accidental `git add .`
can't publish it.

Part of a small family:
- **[inboxkit](https://github.com/udaysrinu/inboxkit):** gets the documents out of Gmail: contract notes,
  CAS statements, loan/FD/NPS statements, unlocked and parsed.
- **[sharecase](https://github.com/udaysrinu/sharecase):** build and share stock baskets, sized to whole
  shares.
- **[StatementLens](https://github.com/udaysrinu/StatementLens):** spending, from bank and card statements.

> **Early and personal.** This was built for one account first. The core (FIFO, daily performance,
> attribution, the invariant checks) is generic, but expect rough edges.

## Run

    python3 tradebook.py          load every tradebook*.xlsx: trade count, date range, coverage gaps
    python3 _daily.py [start]     daily value, P&L and time-weighted return vs Nifty -> _daily_perf.csv
    python3 attribute_sells.py    realised P&L per basket, dated by who held the stock on the sale day
    python3 realised.py           realised per symbol, with uncosted sells flagged
    python3 build_live.py <snap>  reconcile the basket map against holdings -> live.json for the pages
    node _devserver.mjs           serve the pages on http://localhost:8899 with your data injected

Needs Python 3.10+ (`pip install -r requirements.txt`) and Node 18+ for the page checks.

## Your data (stays on your machine, next to the code)

    tradebook*.xlsx         Zerodha Console exports. Merged and deduped on trade id, overlaps are safe.
    _smallcase_map.json     which basket owns which shares, at what cost, and the account facts the
                            code must not hardcode: symbol aliases, price aliases for renamed tickers,
                            corporate actions, known quiet months, uncosted allotments.
    live.json               built by build_live.py; what the pages read.

## Rules, each from a real bug

1. **Never invent a cost.** A sale whose buy isn't in the data is reported as uncosted, never as profit.
2. **Derive or abstain.** A price that can't be observed is never 0, and an aliased price history is
   checked against your own trade prices before it is used.
3. **Membership is dated.** A sale belongs to whoever held the stock on the day it was sold.
4. **A check that can't fail teaches nothing.** Every invariant states what it would catch.

## Tests

    python3 tests/run.py      public tests on made-up trades (no data needed)
    python3 test_money.py     private invariants on YOUR data; skips cleanly when there is none

`test_money.py` also runs the sharecase pages' own JavaScript (`parity.mjs`) to prove the browser and
Python agree to the rupee, and feeds hostile input through them (`xss_test.mjs`). Set `SHARECASE_REPO`
if sharecase isn't checked out beside this repo.

MIT licensed.
