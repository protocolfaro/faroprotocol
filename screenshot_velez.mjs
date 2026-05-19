import puppeteer from 'puppeteer';
import http from 'http';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SERVE_ROOT = path.join(__dirname, 'velez');
const PORT = 8787;
const DESKTOP = path.join(process.env.USERPROFILE || process.env.HOME, 'Desktop');

// ── Minimal static file server ────────────────────────────────────────────────
function startServer() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      let urlPath = req.url.split('?')[0].split('#')[0];
      if (urlPath === '/' || urlPath === '') urlPath = '/index.html';
      const filePath = path.join(SERVE_ROOT, urlPath);
      const ext = path.extname(filePath).toLowerCase();
      const mime = { '.html': 'text/html', '.json': 'application/json', '.js': 'text/javascript', '.css': 'text/css' };
      fs.readFile(filePath, (err, data) => {
        if (err) { res.writeHead(404); res.end('Not found'); return; }
        res.writeHead(200, { 'Content-Type': mime[ext] || 'text/plain', 'Access-Control-Allow-Origin': '*' });
        res.end(data);
      });
    });
    server.listen(PORT, '127.0.0.1', () => resolve(server));
  });
}

// ── iPhone 14 viewport ────────────────────────────────────────────────────────
const IPHONE14 = {
  width: 390,
  height: 844,
  deviceScaleFactor: 3,
  isMobile: true,
  hasTouch: true,
  isLandscape: false,
};

const SHOTS = [
  { slug: 'roger', file: 'preview_roger_v4.png' },
  { slug: 'juan',  file: 'preview_juan_v4.png' },
];

// ── Main ──────────────────────────────────────────────────────────────────────
(async () => {
  console.log('Starting local server on port', PORT, '...');
  const server = await startServer();

  const browser = await puppeteer.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });

  for (const { slug, file } of SHOTS) {
    const page = await browser.newPage();
    await page.setViewport(IPHONE14);
    await page.setUserAgent(
      'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1'
    );

    const url = `http://127.0.0.1:${PORT}/#${slug}`;
    console.log(`Navigating → ${url}`);
    await page.goto(url, { waitUntil: 'networkidle0', timeout: 15000 });

    // Wait for #content to be visible (gauge animation starts)
    await page.waitForSelector('#content', { visible: true, timeout: 8000 }).catch(() => {});

    // Let gauge animation play through
    await new Promise(r => setTimeout(r, 1400));

    // Full-page height screenshot
    const bodyHeight = await page.evaluate(() => document.body.scrollHeight);
    await page.setViewport({ ...IPHONE14, height: Math.max(IPHONE14.height, bodyHeight) });

    const outPath = path.join(DESKTOP, file);
    await page.screenshot({ path: outPath, fullPage: false });
    console.log(`  Saved → ${outPath}`);
    await page.close();
  }

  await browser.close();
  server.close();
  console.log('Done.');
})();
