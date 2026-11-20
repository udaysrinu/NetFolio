// The CLEAN server, for public screenshots: the sharecase pages exactly as a stranger gets them, plus the
// real /api/price, and NOTHING of yours. No map, no trades, no live.json is injected or served (that is
// _devserver.mjs on :8899, which must never be used for anything public). Load the repo's samples/ in the
// page instead.
//   node _cleanserver.mjs            -> http://localhost:8900/
import http from 'node:http';
import { readFile } from 'node:fs/promises';
import { join, extname, normalize } from 'node:path';

const REPO = process.env.SHARECASE_REPO || new URL('../folio-terminal/', import.meta.url).pathname;
const PORT = 8900;
const handler = (await import(join(REPO, 'api/price.js'))).default;
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css',
  '.json': 'application/json', '.woff2': 'font/woff2', '.png': 'image/png', '.svg': 'image/svg+xml', '.csv': 'text/csv' };
const HOSTS = new Set([`localhost:${PORT}`, `127.0.0.1:${PORT}`]);   // DNS-rebinding guard, as in _devserver.mjs

http.createServer(async (req, res) => {
  if (!HOSTS.has(String(req.headers.host || '').toLowerCase())) { res.statusCode = 403; return res.end('forbidden host'); }
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/api/price') {
    req.query = Object.fromEntries(url.searchParams);
    res.status = c => (res.statusCode = c, res);
    res.json = o => (res.setHeader('Content-Type', 'application/json'), res.end(JSON.stringify(o)));
    try { await handler(req, res); } catch (e) { res.statusCode = 500; res.end(String(e)); }
    return;
  }
  const rel = normalize(decodeURIComponent(url.pathname === '/' ? '/index.html' : url.pathname)).replace(/^(\.\.[/\\])+/, '');
  if (rel.split(/[/\\]/).some(p => p.startsWith('.'))) { res.statusCode = 404; return res.end(); }   // no dotfiles, no .git
  try {
    const body = await readFile(join(REPO, rel));
    res.setHeader('Content-Type', TYPES[extname(rel)] || 'application/octet-stream');
    res.end(body);
  } catch { res.statusCode = 404; res.end('not found'); }
}).listen(PORT, '127.0.0.1', () => console.log(`clean sharecase on http://localhost:${PORT}/ (samples only)`));
