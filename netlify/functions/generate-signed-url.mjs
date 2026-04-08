/**
 * FARO PROTOCOL — Netlify Function: generate-signed-url
 * ======================================================
 * CAPA 3 + CAPA 4: Autorización por cliente + Signed URLs
 *
 * Flujo:
 *   1. Recibe Firebase ID token (JWT) del cliente
 *   2. Verifica token con Firebase Admin SDK
 *   3. Comprueba que el cliente tiene acceso al área solicitada
 *   4. Devuelve URL firmada con HMAC-SHA256, válida 1 hora
 *   5. Registra acceso en audit log
 *
 * POST /api/generate-signed-url
 * Body: { idToken: string, file: string }
 * Returns: { signedUrl: string, expiresAt: number }
 *
 * Env vars requeridas:
 *   FIREBASE_PROJECT_ID, FIREBASE_SERVICE_ACCOUNT, SIGNED_URL_SECRET
 */

import { createHmac } from 'node:crypto';
import { initializeApp, getApps, cert } from 'firebase-admin/app';
import { getAuth }      from 'firebase-admin/auth';
import { getFirestore } from 'firebase-admin/firestore';

// ── Bootstrap Firebase Admin (singleton) ─────────────────────
function getFirebaseApp() {
  if (getApps().length) return getApps()[0];
  const sa = JSON.parse(process.env.FIREBASE_SERVICE_ACCOUNT || '{}');
  return initializeApp({ credential: cert(sa) });
}

// ── HMAC-SHA256 signed URL ────────────────────────────────────
// Format: /api/serve-report?file=<file>&uid=<uid>&exp=<ts>&sig=<hmac>
function buildSignedUrl(file, uid, secret, baseUrl) {
  const exp = Date.now() + 60 * 60 * 1000; // 1 hora
  const payload = `${file}|${uid}|${exp}`;
  const sig = createHmac('sha256', secret).update(payload).digest('hex');
  const params = new URLSearchParams({ file, uid, exp: String(exp), sig });
  return { url: `${baseUrl}/api/serve-report?${params}`, expiresAt: exp };
}

// ── Rate limit check ──────────────────────────────────────────
async function checkRateLimit(db, uid) {
  const ref  = db.collection('rate_limits').doc(uid);
  const snap = await ref.get();
  const now  = Date.now();
  const data = snap.exists ? snap.data() : { count: 0, window_start: now };

  // Reset window each hour
  if (now - data.window_start > 3600_000) {
    await ref.set({ count: 1, window_start: now });
    return true;
  }

  if (data.count >= 60) return false; // Max 60 URL gen/hora

  await ref.update({ count: data.count + 1 });
  return true;
}

// ── Audit log ─────────────────────────────────────────────────
async function auditLog(db, entry) {
  await db.collection('audit_log').add({
    ...entry,
    ts: new Date().toISOString(),
  });
}

// ── Allowed files for a client (Firestore profile) ───────────
// Firestore path: clients/{uid} → { areas: ['cordoba','balcarce'], plan: 'Analyst', ... }
const AREA_FILES = {
  cordoba:     ['faro_reporte_fusion_cordoba.png',     'faro_reporte_fusion_cordoba.sha256'],
  balcarce:    ['faro_reporte_fusion_balcarce.png',    'faro_reporte_fusion_balcarce.sha256'],
  vaca_muerta: ['faro_reporte_fusion_vaca_muerta.png', 'faro_reporte_fusion_vaca_muerta.sha256'],
  rotterdam:   ['faro_reporte_fusion_rotterdam.png',   'faro_reporte_fusion_rotterdam.sha256'],
  permian:     ['faro_reporte_fusion_permian.png',     'faro_reporte_fusion_permian.sha256'],
  pilbara:     ['faro_reporte_fusion_pilbara.png',     'faro_reporte_fusion_pilbara.sha256'],
  amazonas:    ['faro_reporte_fusion_amazonas.png',    'faro_reporte_fusion_amazonas.sha256'],
  indiana:     ['faro_reporte_fusion_indiana.png',     'faro_reporte_fusion_indiana.sha256'],
  malacca:     ['faro_reporte_fusion_malacca.png',     'faro_reporte_fusion_malacca.sha256'],
};

function clientCanAccess(clientAreas, file) {
  for (const area of (clientAreas || [])) {
    const allowed = AREA_FILES[area] || [];
    if (allowed.includes(file)) return true;
  }
  return false;
}

// ── Main handler ──────────────────────────────────────────────
export default async function handler(req, context) {
  // CORS preflight
  if (req.method === 'OPTIONS') {
    return new Response(null, {
      status: 204,
      headers: corsHeaders(),
    });
  }

  if (req.method !== 'POST') {
    return json({ error: 'Method not allowed' }, 405);
  }

  // Parse body
  let body;
  try {
    body = await req.json();
  } catch {
    return json({ error: 'Invalid JSON' }, 400);
  }

  const { idToken, file, csrfToken } = body;

  if (!idToken || !file) {
    return json({ error: 'idToken and file are required' }, 400);
  }

  // Validate CSRF token (must be present, format: 64 hex chars)
  if (!csrfToken || !/^[a-f0-9]{64}$/.test(csrfToken)) {
    return json({ error: 'Invalid CSRF token' }, 403);
  }

  const secret = process.env.SIGNED_URL_SECRET;
  if (!secret || secret.length < 32) {
    return json({ error: 'Server misconfiguration' }, 500);
  }

  try {
    const app = getFirebaseApp();
    const auth = getAuth(app);
    const db   = getFirestore(app);

    // 1. Verify Firebase ID token
    let decoded;
    try {
      decoded = await auth.verifyIdToken(idToken, true); // checkRevoked=true
    } catch (err) {
      return json({ error: 'Invalid or expired token', detail: err.code }, 401);
    }

    const uid   = decoded.uid;
    const email = decoded.email;

    // 2. Check Faro Week expiry
    const clientRef  = db.collection('clients').doc(uid);
    const clientSnap = await clientRef.get();

    if (!clientSnap.exists) {
      return json({ error: 'Client profile not found' }, 403);
    }

    const profile = clientSnap.data();

    if (profile.faroWeekExpiry) {
      const expiry = profile.faroWeekExpiry.toMillis
        ? profile.faroWeekExpiry.toMillis()
        : Number(profile.faroWeekExpiry);
      if (Date.now() > expiry) {
        // Revoke tokens + return redirect
        await auth.revokeRefreshTokens(uid);
        await auditLog(db, { type: 'faro_week_expired', uid, email, file });
        return json({ error: 'Faro Week expired', redirect: '/expired.html' }, 403);
      }
    }

    // 3. Check file authorization
    if (!clientCanAccess(profile.areas, file)) {
      // CAPA 3: acceso a datos de otro cliente → alerta
      await auditLog(db, { type: 'unauthorized_file_access', uid, email, file, areas: profile.areas });
      return json({ error: 'Access denied — unauthorized area' }, 403);
    }

    // 4. Rate limit
    const allowed = await checkRateLimit(db, uid);
    if (!allowed) {
      return json({ error: 'Rate limit exceeded. Try again in 1 hour.' }, 429);
    }

    // 5. Build signed URL
    const baseUrl = context.url
      ? new URL(context.url).origin
      : 'https://faroprotocol.netlify.app';

    const { url: signedUrl, expiresAt } = buildSignedUrl(file, uid, secret, baseUrl);

    // 6. Audit log
    const ip = req.headers.get('x-forwarded-for') || req.headers.get('x-real-ip') || 'unknown';
    await auditLog(db, {
      type:      'signed_url_generated',
      uid,
      email,
      file,
      ip,
      expiresAt: new Date(expiresAt).toISOString(),
    });

    return json({ signedUrl, expiresAt }, 200);

  } catch (err) {
    console.error('[signed-url] Error:', err);
    return json({ error: 'Internal server error' }, 500);
  }
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json', ...corsHeaders() },
  });
}

function corsHeaders() {
  return {
    'Access-Control-Allow-Origin':  'https://faroprotocol.netlify.app',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
  };
}
