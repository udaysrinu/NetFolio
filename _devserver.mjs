// PRIVATE local server for the sharecase pages, with your real data pre-loaded (replaces /tmp/sc-dev.mjs,
// which a reboot deleted). Lives in Stocks/ on purpose: it reads private files and must never ship.
//
//   node _devserver.mjs        -> http://localhost:8899/baskets.html  and  /dashboard.html
//
// Static files come from the public repo; /live.json comes from Stocks/; every HTML response gets a
// tiny script that seeds the map and tradebook into localStorage before the page reads them, so
// nothing has to be uploaded. NEVER take public screenshots from here - use a clean server.
import http from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = process.env.SHARECASE_REPO || join(HERE, '..', 'folio-terminal');
// demo.py serves its made-up portfolio on another port: another port is another browser origin, so the
// demo's seed can never overwrite the localStorage your real :8899 pages keep.
const PORT = Number(process.env.NETFOLIO_PORT) || 8899;
const handler = (await import(join(ROOT, 'api/price.js'))).default;   // the real /api/price
const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json',
               '.png': 'image/png', '.woff2': 'font/woff2', '.svg': 'image/svg+xml', '.csv': 'text/csv' };

// JSON is not safe inside <script>: a "</script>" in any string (a crafted symbol or basket name, from a
// file or an email) ends the tag and runs what follows. Escape what HTML cares about; JS reads it back as-is.
const js = v => JSON.stringify(v).replace(/</g, '\\u003c').replace(/>/g, '\\u003e').replace(/&/g, '\\u0026')
  .replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029');
async function seed() {   // read per request, so a rebuilt map/live.json shows on reload
  const map = await readFile(join(HERE, '_smallcase_map.json'), 'utf8');
  const trades = await readFile(join(HERE, '_trades_seed.json'), 'utf8');
  return `<script>try{localStorage.setItem('sc_basket_data',JSON.stringify({text:${js(map)},` +
         `at:new Date().toISOString()}));localStorage.setItem('sc_trades_v2',${js(trades)});}` +
         `catch(e){console.error('seed failed',e)}</script>`;
}

let SEED = await seed();
setInterval(async () => { try { SEED = await seed(); } catch {} }, 5000);

// DNS rebinding guard. Binding to 127.0.0.1 is not enough: a hostile page can point its own domain at
// 127.0.0.1, and the browser then treats this server as same-origin with that page and lets it read
// live.json and the injected portfolio. Its requests still carry ITS hostname, so accept only our own.
const HOSTS = new Set([`localhost:${PORT}`, `127.0.0.1:${PORT}`]);
http.createServer(async (req, res) => {
  if (!HOSTS.has(String(req.headers.host || '').toLowerCase())) {
    res.statusCode = 403; return res.end('forbidden host');
  }
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/__frame') {   // phone-width harness for the real-Chrome checks (_chrome.sh phone)
    const w = +url.searchParams.get('w') || 390, h = +url.searchParams.get('h') || 844;
    const u = String(url.searchParams.get('u') || '/baskets.html').replace(/[^\w\/.\-?=&]/g, '');
    res.setHeader('Content-Type', 'text/html');
    return res.end(`<!doctype html><meta charset="utf-8"><title>phone ${w} · ${u}</title><body style="margin:0;background:#888;display:flex;justify-content:center;padding:16px">`
      + `<iframe id="f" src="${u}" width="${w}" height="${h}" style="border:0;background:#fff"></iframe></body>`);
  }
  if (url.pathname === '/api/price') {
    req.query = Object.fromEntries(url.searchParams);
    res.status = c => (res.statusCode = c, res);
    res.json = o => (res.setHeader('Content-Type', 'application/json'), res.end(JSON.stringify(o)));
    try { await handler(req, res); } catch (e) { res.statusCode = 500; res.end(String(e)); }
    return;
  }
  const isLive = url.pathname === '/live.json';
  const p = isLive ? join(HERE, 'live.json')
                   : join(ROOT, normalize(url.pathname === '/' ? '/index.html' : url.pathname));
  if (!isLive && !p.startsWith(ROOT)) { res.statusCode = 403; return res.end(); }   // path traversal
  try {
    let body = await readFile(p);
    if (extname(p) === '.html') body = String(body).replace(/<head[^>]*>/i, m => m + SEED);
    res.setHeader('Content-Type', MIME[extname(p)] || 'application/octet-stream');
    res.setHeader('Cache-Control', 'no-store');
    res.end(body);
  } catch { res.statusCode = 404; res.end('not found'); }
}).listen(PORT, '127.0.0.1', () => console.log(`http://localhost:${PORT}/baskets.html`));

