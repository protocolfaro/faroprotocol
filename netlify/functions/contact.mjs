/**
 * FARO PROTOCOL — Netlify Function: contact
 * ==========================================
 * Recibe solicitudes de acceso institucional y envía email a ADMIN_EMAIL.
 *
 * POST /api/contact
 * Body (form-encoded o JSON): name, company, country, plan, email
 *
 * Env vars requeridas:
 *   GMAIL_USER      → protocolfaro@gmail.com
 *   GMAIL_APP_PASS  → app password Gmail (16 chars)
 *   ADMIN_EMAIL     → protocolfaro@gmail.com (destino notificación)
 */

import nodemailer from "nodemailer";

export default async function handler(req) {
  // CORS preflight
  if (req.method === "OPTIONS") {
    return new Response(null, {
      status: 204,
      headers: corsHeaders(),
    });
  }

  if (req.method !== "POST") {
    return new Response(JSON.stringify({ error: "method_not_allowed" }), {
      status: 405,
      headers: { "Content-Type": "application/json" },
    });
  }

  // Parsear body (form-encoded o JSON)
  let name = "", company = "", country = "", plan = "", email = "";
  const ct = (req.headers.get("content-type") || "").toLowerCase();
  try {
    if (ct.includes("application/json")) {
      const j = await req.json();
      ({ name = "", company = "", country = "", plan = "", email = "" } = j);
    } else {
      const text = await req.text();
      const p = new URLSearchParams(text);
      name    = p.get("name")    || "";
      company = p.get("company") || "";
      country = p.get("country") || "";
      plan    = p.get("plan")    || "";
      email   = p.get("email")   || "";
    }
  } catch (e) {
    return json({ error: "invalid_body" }, 400);
  }

  if (!email) return json({ error: "missing_email" }, 400);

  console.log(`[Contact] ${name} <${email}> | ${company} | ${country} | ${plan}`);

  const gmailUser  = process.env.GMAIL_USER  || "";
  const gmailPass  = process.env.GMAIL_APP_PASS || "";
  const adminEmail = process.env.ADMIN_EMAIL || gmailUser;

  if (!gmailUser || !gmailPass) {
    console.warn("[Contact] GMAIL no configurado");
    return json({ ok: true, warn: "email_not_configured" });
  }

  const transporter = nodemailer.createTransport({
    host: "smtp.gmail.com",
    port: 465,
    secure: true,
    auth: { user: gmailUser, pass: gmailPass },
  });

  const now = new Date().toISOString().replace("T", " ").slice(0, 19) + " UTC";

  try {
    await transporter.sendMail({
      from:    `"FARO PROTOCOL" <${gmailUser}>`,
      to:      adminEmail,
      replyTo: email,
      subject: `FARO — Nueva solicitud de acceso: ${plan}`,
      text: [
        "FARO PROTOCOL — Solicitud de Acceso Institucional",
        "=".repeat(60),
        "",
        `Plan solicitado : ${plan}`,
        `Nombre          : ${name}`,
        `Empresa         : ${company}`,
        `País            : ${country}`,
        `Email           : ${email}`,
        "",
        "=".repeat(60),
        `Fecha           : ${now}`,
        "=".repeat(60),
        "",
        `Responder a: ${email}`,
      ].join("\n"),
    });
    console.log(`[Contact] Email enviado a ${adminEmail}`);
  } catch (err) {
    console.error(`[Contact] Error SMTP: ${err.message}`);
    // Devolvemos 200 igual — el lead fue recibido aunque el email falle
    return json({ ok: true, warn: err.message });
  }

  return json({ ok: true });
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...corsHeaders() },
  });
}

function corsHeaders() {
  return {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
  };
}
