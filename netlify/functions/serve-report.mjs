/**
 * FARO PROTOCOL — Netlify Function: serve-report
 * ===============================================
 * CAPA 3 + CAPA 4: Sirve reportes via signed URL validado con HMAC-SHA256
 *
 * Flujo:
 *   1. Cliente recibe signed URL desde generate-signed-url
 *   2. GET /api/serve-report?file=...&uid=...&exp=...&sig=...
 *   3. Esta función valida HMAC, verifica expiración y sirve el archivo
 *
 * GET /api/serve-report?file=<file>&uid=<uid>&exp=<timestamp>&sig=<hmac>
 *
 * Env vars:
 *   SIGNED_URL_SECRET   → 64 chars random (openssl rand -hex 32)
 *   FIREBASE_SERVICE_ACCOUNT → service account JSON para verificar UID
 */

import { createHmac } from 'node:crypto';
import { readFile }   from 'node:fs/promises';
import { join }       from 'node:path';
import { fileURLToPath } from 'node:url';
import { dirname }    from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
// Static files están 2 niveles arriba (netlify/functions/ → raíz del proyecto)
const SITE_ROOT = join(__dirname, '..', '..');

const ALLOWED_EXTENSIONS = new Set(['.png', '.sha256', '.pdf']);
const ALLOWED_PREFIXES   = ['faro_reporte_fusion_', 'faro_report_', 'faro_manual_'];

const MIME_TYPES = {
  '.png':    'image/png',
  '.sha256': 'text/plain; charset=utf-8',
  '.pdf':    'application/pdf',
};

// ── Validación del nombre de archivo ─────────────────────────────────────────
function isAllowedFile(filename) {
  if (!filename || typeof filename !== 'string') return false;
  if (filename.includes('..') || filename.includes('/') || filename.includes('\\')) return false;

  const ext = '.' + filename.split('.').pop().toLowerCase();
  if (!ALLOWED_EXTENSIONS.has(ext)) return false;

  return ALLOWED_PREFIXES.some(prefix => filename.startsWith(prefix));
}

// ── Verificación de firma HMAC ────────────────────────────────────────────────
function verifySignature(file, uid, exp, sig, secret) {
  const payload  = `${file}|${uid}|${exp}`;
  const expected = createHmac('sha256', secret).update(payload).digest('hex');
  // Comparación en tiempo constante
  if (expected.length !== sig.length) return false;
  let diff = 0;
  for (let i = 0; i < expected.length; i++) {
    diff |= expected.charCodeAt(i) ^ sig.charCodeAt(i);
  }
  return diff === 0;
}

const ALLOWED_ORIGINS = new Set([
  'https://faro-protocol.netlify.app',
  'https://faroprotocol.com',
  'https://www.faroprotocol.com',
]);

// ── Handler principal ─────────────────────────────────────────────────────────
export default async function handler(req, context) {
  const origin = req.headers.get('Origin') || '';
  const allowedOrigin = ALLOWED_ORIGINS.has(origin) ? origin : 'https://faro-protocol.netlify.app';

  // Solo GET
  if (req.method !== 'GET') {
    return new Response('Method Not Allowed', { status: 405 });
  }

  const url    = new URL(req.url);
  const file   = url.searchParams.get('file') || '';
  const uid    = url.searchParams.get('uid')  || '';
  const expStr = url.searchParams.get('exp')  || '';
  const sig    = url.searchParams.get('sig')  || '';

  const secret = process.env.SIGNED_URL_SECRET;

  // 1. Validar parámetros básicos
  if (!file || !uid || !expStr || !sig || !secret) {
    return errorResponse('Missing parameters or server misconfiguration', 400);
  }

  // 2. Validar nombre de archivo permitido
  if (!isAllowedFile(file)) {
    return errorResponse('File not allowed', 403);
  }

  // 3. Verificar expiración
  const exp = parseInt(expStr, 10);
  if (!exp || isNaN(exp)) {
    return errorResponse('Invalid expiry', 400);
  }
  if (Date.now() > exp) {
    return errorResponse('Signed URL expired. Request a new download link.', 410);
  }

  // 4. Verificar firma HMAC-SHA256
  if (!verifySignature(file, uid, expStr, sig, secret)) {
    return errorResponse('Invalid signature', 403);
  }

  // 5. Leer archivo
  const filePath = join(SITE_ROOT, file);
  let fileBuffer;
  try {
    fileBuffer = await readFile(filePath);
  } catch (err) {
    console.error(`[serve-report] File not found: ${filePath}`, err.code);
    return errorResponse('Report not found', 404);
  }

  // 6. Determinar MIME type
  const ext  = '.' + file.split('.').pop().toLowerCase();
  const mime = MIME_TYPES[ext] || 'application/octet-stream';

  // 7. Headers de seguridad + disposición
  const disposition = ext === '.png' || ext === '.pdf'
    ? `attachment; filename="${file}"`
    : `inline; filename="${file}"`;

  return new Response(fileBuffer, {
    status: 200,
    headers: {
      'Content-Type':              mime,
      'Content-Disposition':       disposition,
      'Content-Length':            String(fileBuffer.byteLength),
      'Cache-Control':             'no-store, no-cache, private',
      'X-Content-Type-Options':    'nosniff',
      'X-Frame-Options':           'DENY',
      'Referrer-Policy':           'no-referrer',
      'X-Faro-Served':             'authenticated',
      'Access-Control-Allow-Origin': allowedOrigin,
      'Vary':                       'Origin',
    },
  });
}

function errorResponse(message, status) {
  return new Response(JSON.stringify({ error: message }), {
    status,
    headers: {
      'Content-Type':           'application/json',
      'Cache-Control':          'no-store',
      'X-Content-Type-Options': 'nosniff',
    },
  });
}
