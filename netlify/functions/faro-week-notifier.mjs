/**
 * FARO PROTOCOL — Netlify Scheduled Function: faro-week-notifier
 * ==============================================================
 * CAPA 9: Envía alertas de vencimiento 24 horas antes de que expire el Faro Week.
 * También revoca automáticamente accesos vencidos.
 *
 * Scheduled: @daily (se configura en netlify.toml)
 *
 * Acciones:
 *   - Busca clientes activos cuyo faroWeekExpiry vence en las próximas 25 horas
 *   - Envía email: "Tu Faro Week vence mañana"
 *   - Busca clientes cuyo faroWeekExpiry ya pasó → deshabilita en Firebase Auth
 *   - Log en Firestore audit_log collection
 *
 * Env vars:
 *   FIREBASE_SERVICE_ACCOUNT → service account JSON string
 *   GMAIL_USER               → protocolfaro@gmail.com
 *   GMAIL_APP_PASS           → app password de Gmail
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

async function sendEmail({ to, subject, html }) {
  const user = process.env.GMAIL_USER;
  const pass = process.env.GMAIL_APP_PASS;
  if (!user || !pass) {
    console.warn('[faro-week-notifier] Email no configurado (GMAIL_USER/GMAIL_APP_PASS)');
    return false;
  }
  const transporter = createTransport({
    service: 'gmail',
    auth: { user, pass },
  });
  await transporter.sendMail({
    from:    `"Faro Protocol" <${user}>`,
    to,
    subject,
    html,
  });
  return true;
}

function warningEmailHtml(name, expiryDate, email) {
  const emailEncoded = encodeURIComponent(email);
  return `<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8">
<style>
*{margin:0;padding:0;box-sizing:border-box;}
body{background:#06080b;color:#f2ede4;font-family:'Helvetica Neue',Arial,sans-serif;}
.wrap{max-width:600px;margin:0 auto;padding:48px 28px;}
.logo{font-size:22px;letter-spacing:0.35em;color:#c9a84c;text-transform:uppercase;font-weight:300;}
h2{color:#c8881a;font-weight:300;font-size:24px;margin:32px 0 14px;}
p{color:rgba(242,237,228,0.72);font-size:14px;line-height:1.85;margin-bottom:18px;}
.timer{background:rgba(200,136,26,0.06);border:1px solid rgba(200,136,26,0.25);
       border-left:3px solid #c8881a;padding:16px 20px;margin:24px 0;
       font-family:monospace;font-size:13px;color:#c8881a;}
.btn{display:inline-block;background:#c9a84c;color:#06080b;padding:14px 30px;
     text-decoration:none;font-weight:700;font-size:12px;letter-spacing:0.12em;
     text-transform:uppercase;margin:24px 0;}
.plans{background:#0d1117;border:1px solid rgba(201,168,76,0.12);padding:20px 24px;margin:24px 0;}
.plan{font-size:13px;color:rgba(242,237,228,0.65);margin:10px 0;padding-left:14px;
      border-left:2px solid rgba(201,168,76,0.2);}
.plan strong{color:#e2c97e;}
.footer{border-top:1px solid rgba(201,168,76,0.08);margin-top:40px;padding-top:18px;
        font-size:10px;color:rgba(242,237,228,0.2);font-family:monospace;}
a{color:#c9a84c;text-decoration:none;}
</style></head>
<body>
<div class="wrap">
  <div class="logo">FARO PROTOCOL</div>
  <h2>⚠ Tu Faro Week vence mañana</h2>
  <p>Hola <strong style="color:#f2ede4;">${name}</strong>,</p>
  <p>Tu período de acceso a Faro Protocol está por terminar.</p>
  <div class="timer">📅 Vencimiento: <strong>${expiryDate}</strong></div>
  <p>Si querés seguir monitoreando tus áreas con inteligencia satelital,
     activá tu suscripción:</p>
  <div style="text-align:center;">
    <a href="mailto:protocolfaro@gmail.com?subject=Quiero%20continuar%20con%20Faro%20Protocol%20-%20${emailEncoded}" class="btn">
      Activar suscripción →
    </a>
  </div>
  <div class="plans">
    <div style="font-family:monospace;font-size:8px;color:rgba(201,168,76,0.4);
                letter-spacing:0.2em;text-transform:uppercase;margin-bottom:14px;">Planes</div>
    <div class="plan"><strong>Observer — USD 2.500/mes</strong><br>1 zona · Reporte semanal · SHA-256 certificado</div>
    <div class="plan"><strong>Analyst — USD 9.000/mes</strong><br>3 zonas · Alertas en tiempo real · Dashboard</div>
    <div class="plan"><strong>Sovereign — USD 17.000/mes</strong><br>Zonas ilimitadas · API access · SLA</div>
  </div>
  <div class="footer">
    <div>Faro Protocol · protocolfaro@gmail.com</div>
    <div>© 2026 Faro Protocol</div>
  </div>
</div>
</body></html>`;
}

export default async function handler(req, context) {
  const now       = new Date();
  const in25Hours = new Date(now.getTime() + 25 * 60 * 60 * 1000);

  try {
    const app  = getFirebaseApp();
    const db   = getFirestore(app);
    const auth = getAuth(app);

    const clients = await db.collection('clients').where('status', '==', 'active').get();

    let alertsSent   = 0;
    let accessRevoked = 0;

    for (const doc of clients.docs) {
      const p      = doc.data();
      const expiry = p.faroWeekExpiry;
      if (!expiry) continue;

      const expiryMs = expiry.toMillis ? expiry.toMillis() : Number(expiry);
      const expiryDt = new Date(expiryMs);

      // ── Acceso vencido → revocar ──────────────────────────────
      if (expiryMs <= now.getTime()) {
        try {
          await auth.updateUser(p.uid, { disabled: true });
          await auth.revokeRefreshTokens(p.uid);
          await doc.ref.update({ status: 'expired', updatedAt: now });
          await db.collection('audit_log').add({
            type:    'faro_week_expired_auto',
            uid:     p.uid,
            email:   p.email,
            expiry:  expiryDt.toISOString(),
            ts:      now.toISOString(),
          });
          accessRevoked++;
          console.log(`[faro-week-notifier] Revocado: ${p.email}`);
        } catch (err) {
          console.error(`[faro-week-notifier] Error revocando ${p.email}:`, err.message);
        }
        continue;
      }

      // ── Vence en 24h → alerta ─────────────────────────────────
      if (expiryMs <= in25Hours.getTime() && !p.expiryAlertSent) {
        const expiryDateStr = expiryDt.toLocaleDateString('es-AR', {
          day: '2-digit', month: '2-digit', year: 'numeric',
          hour: '2-digit', minute: '2-digit', timeZone: 'UTC',
        }) + ' UTC';

        try {
          await sendEmail({
            to:      p.email,
            subject: 'Faro Protocol — Tu Faro Week vence mañana',
            html:    warningEmailHtml(p.name || p.email, expiryDateStr, p.email),
          });
          await doc.ref.update({
            expiryAlertSent:   true,
            expiryAlertSentAt: now,
          });
          await db.collection('audit_log').add({
            type:    'expiry_alert_sent',
            uid:     p.uid,
            email:   p.email,
            expiry:  expiryDt.toISOString(),
            ts:      now.toISOString(),
          });
          alertsSent++;
          console.log(`[faro-week-notifier] Alerta enviada a: ${p.email}`);
        } catch (err) {
          console.error(`[faro-week-notifier] Error enviando alerta a ${p.email}:`, err.message);
        }
      }
    }

    console.log(`[faro-week-notifier] Completado: ${alertsSent} alertas, ${accessRevoked} revocados`);

    return new Response(JSON.stringify({
      ok: true,
      alertsSent,
      accessRevoked,
      runAt: now.toISOString(),
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });

  } catch (err) {
    console.error('[faro-week-notifier] Error fatal:', err);
    return new Response(JSON.stringify({ error: err.message }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    });
  }
}
