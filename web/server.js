// Serves the page and proxies /api/* to the FastAPI service. No dependencies.
const http = require('http');
const fs = require('fs');
const path = require('path');

const API = new URL(process.env.API_URL || 'http://127.0.0.1:8000');
const PORT = process.env.PORT || 3000;
const PUB = path.join(__dirname, 'public');
const TYPES = { '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript' };

http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const up = http.request(
      { hostname: API.hostname, port: API.port, path: req.url, method: req.method, headers: { ...req.headers, host: API.host } },
      (r) => { res.writeHead(r.statusCode, r.headers); r.pipe(res); }
    );
    up.on('error', () => {
      if (!res.headersSent) res.writeHead(502, { 'content-type': 'application/json' });
      res.end('{"detail":"API server is not reachable."}');
    });
    req.pipe(up);
    return;
  }
  let file;
  try {
    const p = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    file = path.join(PUB, p === '/' ? 'index.html' : p);
  } catch { return res.writeHead(400).end('Bad request'); }
  if (req.method !== 'GET' || !file.startsWith(PUB + path.sep) || !TYPES[path.extname(file)]) return res.writeHead(404).end('Not found');
  fs.readFile(file, (err, buf) => {
    if (err) return res.writeHead(404).end('Not found');
    res.writeHead(200, { 'content-type': TYPES[path.extname(file)] });
    res.end(buf);
  });
}).listen(PORT, () => console.log(`http://localhost:${PORT} -> API ${API.origin}`));
