// Runs the REAL page code (baskets.html's tbRealised/tbAttribute, dashboard.html's fifoJS) against the
// same private data Python uses, and prints the numbers as JSON for test_money.py to compare.
// Exists because a demerger was applied in Python and silently ignored by the page - a
// disagreement that no Python-only check could ever see.
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
// the sharecase checkout, beside this repo unless SHARECASE_REPO says otherwise
// with a trailing slash either way: the README's `SHARECASE_REPO=/path/to/sharecase` has none, and REPO+file
// then read '/path/to/sharecasebasket.html'
const REPO = (process.env.SHARECASE_REPO || new URL('../folio-terminal/', import.meta.url).pathname).replace(/\/?$/, '/');
const stub = () => new Proxy(function(){}, { get: (t, k) => k === Symbol.toPrimitive ? () => '' : stub(),
                                             apply: () => stub(), set: () => true });
function load(file) {
  const html = readFileSync(REPO + file, 'utf8');
  const blocks = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
  const code = blocks[blocks.length - 1][1];
  const ctx = { console, document: stub(), localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
                addEventListener() {}, setInterval() {}, setTimeout() {}, fetch: () => new Promise(() => {}),
                window: {}, navigator: {}, location: {}, XLSX: undefined };
  vm.createContext(ctx);
  // top-level let/const are not properties of the context, so re-export what we need
  const pick = n => `${n}:typeof ${n}!=="undefined"?${n}:null`;
  vm.runInContext(code + `\n;globalThis.__x={${['normalise','tbRealised','tbAttribute','fifoJS'].map(pick).join(',')},
    setTrades(v){ if(typeof TRADES!=="undefined") TRADES=v; }};`, ctx);
  return ctx.__x;
}
const map = JSON.parse(readFileSync('_smallcase_map.json', 'utf8'));
const seed = JSON.parse(readFileSync('_trades_seed.json', 'utf8'));
const b = load('baskets.html');
b.normalise(map);
b.setTrades(seed);
const TB = b.tbRealised(), all = b.tbAttribute(TB.matched);
const buckets = {}; for (const [k, v] of Object.entries(all)) buckets[k] = v.pnl;
const d = load('dashboard.html');
const live = JSON.parse(readFileSync('live.json', 'utf8'));
const f = d.fifoJS(live._trades);
const dashTotal = f.roundtrips.reduce((a, t) => a + t.pnl, 0);
console.log(JSON.stringify({ baskets_total: TB.matched.reduce((a, m) => a + m.pnl, 0), buckets,
  baskets_uncosted: TB.uncosted.map(u => u.s), dashboard_total: dashTotal }));
