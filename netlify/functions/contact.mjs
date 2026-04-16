/**
 * FARO PROTOCOL — Netlify Function: contact
 * ==========================================
 * Reenvía solicitudes de acceso institucional a Railway /api/contact
 * que las procesa con Gmail SMTP.
 *
 * POST /api/contact
 * Body (form-encoded): name, company, country, plan, email
 */

const RAILWAY_URL = "https://faroprotocol-production-45fd.up.railway.app/api/contact";

export default async function handler(req) {
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204 });
  }

  if (req.method !== "POST") {
    return new Response(JSON.stringify({ error: "method_not_allowed" }), {
      status: 405,
      headers: { "Content-Type": "application/json" },
    });
  }

  const body = await req.text();
  console.log(`[Contact] Forwarding to Railway: ${body.slice(0, 120)}`);

  try {
    const resp = await fetch(RAILWAY_URL, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    });
    const data = await resp.json().catch(() => ({ ok: true }));
    console.log(`[Contact] Railway response: ${JSON.stringify(data)}`);
    return new Response(JSON.stringify(data), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  } catch (err) {
    console.error(`[Contact] Railway error: ${err.message}`);
    return new Response(JSON.stringify({ ok: true, warn: err.message }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }
}
