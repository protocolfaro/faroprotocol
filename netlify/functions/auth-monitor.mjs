/**
 * FARO PROTOCOL — Netlify Function: auth-monitor
 * ================================================
 * CAPA 2: Rate limiting + alertas de seguridad
 * CAPA 5: Monitoreo y detección de anomalías
 *
 * POST /api/auth-monitor
 * Body: { event: 'login_failed'|'login_ok'|'new_ip'|'bulk_download', email, ip, uid? }
 *
 * Acciones por evento:
 *   login_failed → incrementa contador en Firestore
 *                  3 intentos  → responde { action: 'captcha' }
 *                  5 intentos  → responde { action: 'lockout', minutes: 30 }
 *                  10 intentos → responde { action: 'permanent_block' } + email admin
 *   login_ok     → resetea contador, registra IP conocida
 *   new_ip       → alerta email al cliente
 *   bulk_download→ alerta email al admin
 */

import { createTransport } from 'nodemailer';
import { initializeApp, getApps, cert } from 'firebase-admin/app';
import { getAuth }      from 'firebase-admin/auth';
import { getFirestore } from 'firebase-admin/firestore';

function getFirebaseApp() {
  if (getApps().length) return getApps()[0];
  const sa = JSON.parse(process.env.FIREBASE_SERVICE_ACCOUNT || '{}');
  return initializeApp({ credential: cert(sa) });
}

// ── Email alertas ─────────────────────────────────────────────
async function sendAlert({ to, subject, html }) {
  const user = process.env.GMAIL_USER;
  const pass = process.env.GMAIL_APP_PASS;
  if (!user || !pass) return;

  const transporter = createTransport({
    service: 'gmail',
    auth: { user, pass },
  });

  await transporter.sendMail({
    from:    `"Faro Protocol Security" <${user}>`,
    to,
    subject,
    html,
  });
}

const ALERT_TEMPLATE = (title, body) => `
<!DOCTYPE html>
<html>
<head><style>
  body { background: #06080b; color: #f2ede4; font-family: monospace; padding: 32px; }
  h2   { color: #c9a84c; border-bottom: 1px solid rgba(201,168,76,0.2); padding-bottom: 12px; }
  .kv  { margin: 8px 0; font-size: 13px; }
  .key { color: #978f82; display: inline-block; width: 140px; }
  .val { color: #f2ede4; }
  .warn{ background: rgba(176,48,48,0.15); border-left: 3px solid #b03030; padding: 12px 16px; margin-top: 20px; }
</style></head>
<body>
  <h2>🔐 Faro Protocol — Alerta de Seguridad</h2>
  <p style="color:#978f82;font-size:12px;margin-bottom:24px;">${new Date().toISOString()}</p>
  <h3 style="color:#e2c97e;">${title}</h3>
  ${body}
  <div class="warn">Este mensaje fue generado automáticamente por el sistema de monitoreo Faro Protocol.</div>
</body>
</html>`;

const kv = (key, val) => `<div class="kv"><span class="key">${key}</span><span class="val">${val}</span></div>`;

// ── Firestore rate limit state ────────────────────────────────
async function getAttemptState(db, email) {
  const ref  = db.collection('auth_attempts').doc(email.replace(/[^a-z0-9]/gi, '_'));
  const snap = await ref.get();
  return { ref, data: snap.exists ? snap.data() : { count: 0, first_attempt: Date.now(), permanent_block: false } };
}

// ── Handler ───────────────────────────────────────────────────
export default async function handler(req, context) {
  if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: corsHeaders() });
  if (req.method !== 'POST')   return json({ error: 'Method not allowed' }, 405);

  let body;
  try { body = await req.json(); } catch { return json({ error: 'Invalid JSON' }, 400); }

  const { event, email, ip, uid, userAgent } = body;
  if (!event || !email) return json({ error: 'event and email required' }, 400);

  const adminEmail = process.env.ADMIN_EMAIL || process.env.GMAIL_USER;

  try {
    const app = getFirebaseApp();
    const db  = getFirestore(app);
    const auth = getAuth(app);

    // ── login_failed ─────────────────────────────────────────
    if (event === 'login_failed') {
      const { ref, data } = await getAttemptState(db, email);

      if (data.permanent_block) {
        return json({ action: 'permanent_block', message: 'Account permanently blocked.' }, 403);
      }

      // Check window (30 min)
      const windowElapsed = Date.now() - (data.first_attempt || Date.now());
      const inWindow = windowElapsed < 30 * 60 * 1000;
      const newCount = inWindow ? (data.count || 0) + 1 : 1;
      const newFirst = inWindow ? data.first_attempt : Date.now();

      // Permanent block at 10 attempts
      if (newCount >= 10) {
        await ref.set({ count: newCount, first_attempt: newFirst, permanent_block: true, blocked_at: Date.now() });

        // Disable Firebase user
        if (uid) {
          try { await auth.updateUser(uid, { disabled: true }); } catch {}
        }

        // Alert admin
        await sendAlert({
          to:      adminEmail,
          subject: `[FARO SEGURIDAD] Cuenta bloqueada permanentemente: ${email}`,
          html: ALERT_TEMPLATE('Bloqueo Permanente por Intentos Fallidos',
            kv('Email', email) + kv('IP', ip || 'unknown') + kv('Intentos', String(newCount)) + kv('User-Agent', userAgent || '—')),
        });

        return json({ action: 'permanent_block', message: 'Account permanently blocked. Contact support.' }, 403);
      }

      // Lockout at 5 attempts
      if (newCount >= 5) {
        const lockoutUntil = Date.now() + 30 * 60 * 1000;
        await ref.set({ count: newCount, first_attempt: newFirst, lockout_until: lockoutUntil });

        await sendAlert({
          to:      adminEmail,
          subject: `[FARO SEGURIDAD] 5 intentos fallidos: ${email}`,
          html: ALERT_TEMPLATE('5 Intentos Fallidos — Bloqueo 30 Minutos',
            kv('Email', email) + kv('IP', ip || 'unknown') + kv('Intentos', String(newCount))),
        });

        return json({ action: 'lockout', minutes: 30, lockoutUntil }, 429);
      }

      await ref.set({ count: newCount, first_attempt: newFirst });

      // Captcha at 3 attempts
      if (newCount >= 3) {
        return json({ action: 'captcha', attemptsLeft: 10 - newCount }, 200);
      }

      return json({ action: 'allow', attemptsLeft: 5 - newCount }, 200);
    }

    // ── login_ok ─────────────────────────────────────────────
    if (event === 'login_ok') {
      const { ref, data } = await getAttemptState(db, email);

      // Reset attempt counter
      await ref.set({ count: 0, first_attempt: Date.now(), permanent_block: false });

      // Check if this is a new IP
      const knownIPs = data.known_ips || [];
      const cleanIP  = (ip || '').split(',')[0].trim();
      const isNewIP  = cleanIP && !knownIPs.includes(cleanIP);

      if (isNewIP) {
        // Update known IPs (keep last 10)
        const updated = [...knownIPs.slice(-9), cleanIP];
        await ref.update({ known_ips: updated });

        // Alert user about new IP login
        await sendAlert({
          to:      email,
          subject: '[Faro Protocol] Nuevo acceso desde IP no reconocida',
          html: ALERT_TEMPLATE('Login desde IP nueva',
            kv('Email', email) + kv('IP', cleanIP) +
            kv('Timestamp', new Date().toISOString()) +
            kv('User-Agent', userAgent || '—') +
            `<div style="margin-top:20px;color:#b03030;">Si no fuiste vos, contactá a soporte inmediatamente: <a href="mailto:${adminEmail}" style="color:#c9a84c;">${adminEmail}</a></div>`),
        });
      }

      return json({ action: 'allow', newIP: isNewIP }, 200);
    }

    // ── new_ip ───────────────────────────────────────────────
    if (event === 'new_ip') {
      await sendAlert({
        to:      email,
        subject: '[Faro Protocol] Acceso desde ubicación nueva',
        html: ALERT_TEMPLATE('Acceso desde IP nueva detectada',
          kv('Email', email) + kv('IP', ip || 'unknown') + kv('Timestamp', new Date().toISOString())),
      });
      return json({ action: 'alerted' }, 200);
    }

    // ── bulk_download ────────────────────────────────────────
    if (event === 'bulk_download') {
      await sendAlert({
        to:      adminEmail,
        subject: `[FARO SEGURIDAD] Descarga masiva detectada: ${email}`,
        html: ALERT_TEMPLATE('Descarga Masiva de Reportes',
          kv('Email', email) + kv('IP', ip || 'unknown') + kv('UID', uid || '—')),
      });
      return json({ action: 'flagged' }, 200);
    }

    return json({ error: 'Unknown event' }, 400);

  } catch (err) {
    console.error('[auth-monitor] Error:', err);
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
