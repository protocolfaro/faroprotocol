# FARO PROTOCOL — Guía para Claude Code
> Última actualización: 2026-04-18 — V15 · GitHub Pages · https://protocolfaro.github.io/faroprotocol
> Webhook Railway: https://faroprotocol-production-45fd.up.railway.app
> Tienda Lemon Squeezy: https://faroprotocol.lemonsqueezy.com
> Arquitecto del sistema: Emilio (hijo)
> Conocimiento de campo y contactos: Padre (fundador)

## Hosting — V15 (2026-04-18)
```
✅ GitHub Pages: https://protocolfaro.github.io/faroprotocol (rama main, raíz /)
⚠️  Netlify: suspendido por límite de créditos (era https://faro-protocol.netlify.app)
✅ Railway webhook: activo 24/7
```

### Regla de oro — sincronización HTML
Siempre editar `faro_website.html`. Nunca editar `index.html` directamente.
El pre-commit hook `.git/hooks/pre-commit` copia automáticamente
`faro_website.html` → `index.html` cada vez que se commitea el primero.
```bash
# El hook hace esto automáticamente al hacer git add faro_website.html + git commit
cp faro_website.html index.html
git add index.html
```

---

## Estado del sistema — V15 (2026-04-18) — GitHub Pages

### Concepto fundamental
El cliente NO elige de una lista predefinida. Dibuja en el visor 3D cualquier zona del planeta.
El pipeline corre automáticamente para esa zona y devuelve datos en ~24 horas.

### Pipeline operativo — 10 áreas demo activas

| Área | Vertical | Score | Estado |
|------|----------|-------|--------|
| cordoba | agro | 49 | ⚠️ ALERTA |
| balcarce | agro | 70 | ✅ OK |
| vaca_muerta | energia | 42 | ✅ OK |
| rotterdam | maritimo | 38 | ✅ OK |
| permian | oil_gas | 52 | ✅ OK |
| pilbara | mining_iron | 41 | ✅ OK |
| amazonas | deforestacion | 66 | ✅ OK |
| indiana | agro | 38 | ⚠️ ALERTA |
| malacca | shipping | 42 | ✅ OK |
| punta_colorada | oil_gas | 41 | ✅ OK |

---

## Arquitectura V14

### Flujo de un cliente nuevo
```
1. Compra en faro_website.html (Lemon Squeezy)
   → faro_webhook.py recibe pago → crea Firebase user
   → Firestore: { plan, max_assets, zones: {} }
   → WhatsApp + email de bienvenida (Twilio + Gmail)

2. Cliente abre visor_faro_v2.html
   → Firebase Auth verifica sesión
   → Carga zones de Firestore
   → Si no tiene zones: muestra demo de data.json

3. Cliente dibuja zona en el mapa (Cesium)
   → POST /api/zone/new → faro_webhook.py
   → Se verifica cuota (max_assets)
   → Se crea faro_areas/<slug>.json
   → Pipeline arranca en background (faro_pipeline.py --area <slug>)
   → Zona aparece en sidebar como "PROCESANDO"

4. ~24 horas después
   → Pipeline completo: SAR + NDVI + Score + SHA-256
   → data.json actualizado
   → WhatsApp: notificación de datos disponibles
   → Visor muestra score real
```

### Planes y cuotas (max_assets guardado en Firestore)
| Plan | Precio | max_assets | Zonas |
|------|--------|-----------|-------|
| Observer | USD 2.500/mes | 1 | 1 zona a elección |
| Analyst | USD 9.000/mes | 3 | 3 zonas a elección |
| Sovereign | USD 17.000/mes | 999 | ilimitadas |
| Enterprise | USD 3.200/sector/mes | 999 | sectores completos |

### Seguridad por capas
| Capa | Qué protege | Dónde |
|------|-------------|-------|
| Firebase Auth | Login / JWT RS256 | `visor_faro_v2.html` |
| Cuota max_assets | Observer=1, Analyst=3, Sovereign=999 | `faro_webhook.py` + Firestore |
| Geofencing pipeline | Verifica UID antes de correr pipeline | `faro_pipeline.py --client-uid` |
| HMAC webhook | Firma Lemon Squeezy verificada | `faro_webhook.py` |
| Rate limiting | 3→captcha, 5→lockout, 10→bloqueo | `auth-monitor.mjs` |
| Signed URLs | Archivos no expuestos directamente (1h) | `serve-report.mjs` |
| Faro Week | 7 días trial, revocación automática | `faro-week-notifier.mjs` |
| Security tests | 9 categorías automatizadas | `faro_security_test.py` |

---

## Estructura de archivos

### Pipeline principal (público en git)
```
faro_pipeline.py      # orquestador — --area <zona> [--client-uid <uid>]
faro_areas.py         # loader: load_area() / list_areas()
faro_areas/           # configs JSON por zona (creadas automáticamente al dibujar)
data.json             # estado global del sistema (generado por el engine)
```

### Engine privado (en /engine/, NO en git — .gitignored)
```
engine/
  faro_sar_auto.py      # descarga S1 + S2 + pipeline completo por área
  faro_sar_pipeline.py  # descarga Sentinel-1 GRD
  faro_s2_pipeline.py   # descarga Sentinel-2 L2A + NDVI
  faro_sar_georef.py    # georreferencia SAR → dB
  faro_fusion.py        # fusión SAR + NDVI → Índice Faro
  faro_vision.py        # visión computacional: vigor + anomalías
  faro_engine.py        # motor central: score + rinde + alerta
  faro_finance.py       # business case USD
```

### Módulos de salida
```
faro_certificado.py     # Sello Verde: PNG con SHA-256
faro_bricolage.py       # Imagen LinkedIn (4 zonas)
faro_dashboard.py       # Dashboard dinámico
faro_report_engine.py   # Reporte certificado estático
faro_card.py            # Cards LinkedIn/WhatsApp
faro_portfolio_global.py
faro_stress_index.py
faro_quality.py
```

### Core operativo (pagos + notificaciones)
```
faro_webhook.py       # Flask server: Lemon Squeezy + /api/zone/new
faro_notifier.py      # Twilio WhatsApp
gen_portal_key.py     # Firebase Admin: crear/extender/revocar clientes
faro_auto.py          # scheduler semanal (Task Scheduler Windows, lun 10:00 ART)
faro_deliver.py       # envío por Gmail SMTP
faro_manual_cliente.py
```

### Web
```
faro_website.html          # landing + 4 planes Lemon Squeezy
outputs/visor_faro_v2.html # visor 3D = portal único del cliente
index.html                 # GitHub Pages redirect
```

### Netlify Functions
```
netlify/functions/auth-monitor.mjs
netlify/functions/generate-signed-url.mjs
netlify/functions/serve-report.mjs
netlify/functions/faro-week-notifier.mjs
```

---

## Comandos frecuentes

```bash
# Pipeline para un área (con geofencing de cliente)
python faro_pipeline.py --area mi_zona
python faro_pipeline.py --area mi_zona --client-uid <firebase_uid>

# Regenerar certificados y bricolage
python faro_certificado.py --all
python faro_bricolage.py

# Servidor webhook (pagos + zona nueva)
python faro_webhook.py
# ngrok http 5000 → pegar URL en Lemon Squeezy Dashboard

# Gestión de clientes
python gen_portal_key.py cliente@empresa.com --name "Empresa SA"
python gen_portal_key.py --extend cliente@empresa.com --days 7
python gen_portal_key.py --revoke cliente@empresa.com
python gen_portal_key.py --list

# Security test
python faro_security_test.py --url https://faro-protocol.netlify.app
```

---

## Railway + Lemon Squeezy — Estado (2026-04-18)

```
✅ Railway webhook live: https://faroprotocol-production-45fd.up.railway.app
✅ Lemon Squeezy tienda: https://faroprotocol.lemonsqueezy.com
✅ Producto Observer USD 2.500/mes creado
⏳ Analyst / Sovereign / Enterprise — pendiente crear en Lemon Squeezy
⏳ One-Shot × 5 productos — pendiente crear en Lemon Squeezy
⏳ Webhook URL en Lemon Squeezy → Railway — pendiente configurar
⏳ Conexión bancaria — pendiente
⏳ CTAs del sitio apuntan a formulario, no a checkout Lemon Squeezy (pendiente lunes)
```

**Variables de entorno Railway (.env o Railway Dashboard):**
```
LEMON_SQUEEZY_SECRET=<webhook secret del dashboard>
FIREBASE_SERVICE_ACCOUNT=<JSON string del service account>
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
TWILIO_WHATSAPP_TO=whatsapp:+54911XXXXXXXX
PORT=5000
```

---

## Firebase — Estado (2026-04-18)

**Proyecto:** `faro-protocol-906a5`
**Portal/Visor:** https://faro-protocol.netlify.app

```
✅ Firebase Auth — Email/Password activo
✅ Firestore — southamerica-east1 · Security Rules publicadas
✅ Netlify Functions — 4 funciones activas
✅ SIGNED_URL_SECRET configurado
✅ GMAIL_USER / GMAIL_APP_PASS configurados
```

**Firestore schema por cliente:**
```json
{
  "email": "...",
  "plan": "analyst",
  "max_assets": 3,
  "zones": {
    "slug_zona": {
      "name": "Mi Campo Norte",
      "vertical": "agro",
      "bounds": [lon_min, lat_min, lon_max, lat_max],
      "center": [lat, lon],
      "status": "active"
    }
  },
  "active_subscription": true,
  "status": "active",
  "source": "lemon_squeezy"
}
```

**Firestore Security Rules:**
```
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    match /clients/{uid} {
      allow read: if request.auth.uid == uid;
      allow write: if false;
    }
    match /auth_attempts/{doc} { allow read, write: if false; }
    match /rate_limits/{doc}   { allow read, write: if false; }
    match /audit_log/{doc}     { allow read, write: if false; }
  }
}
```

---

## Diseño visual

```css
--bg:   #0a0a1a   /* fondo principal */
--gold: #c9a84c   /* dorado — color de marca */
--gl:   #e2c97e   /* dorado claro */
--w:    #f2ede4   /* texto principal */
--green:#2d8c5e
--red:  #b03030

--serif: 'Cormorant Garamond'
--mono:  'JetBrains Mono'
```

Estética: oscura, premium, Bloomberg / datos financieros.

---

## Arquitectura técnica

```
Satelital:
  Copernicus CDSE API → Sentinel-1 GRD + Sentinel-2 L2A (sin GEE)
  rasterio, numpy → procesamiento raster
  Fórmula SAR: 20*log10(DN) - 52.0 (offset empírico CDSE)

Motor económico:
  FACTOR_BIOMASA_A_GRANO = 0.44 (Harvest Index)
  Rinde ref: lookup jerárquico → departamento > provincia > nacional
  Baseline: MAGyP 2026-03 + SIIA 2000-2024

Verificación:
  SHA-256 en cada reporte generado
  audit_log.jsonl: append-only, local (gitignoreado)

Cuotas:
  Observer=1, Analyst=3, Sovereign=999 — campo max_assets en Firestore
  Geofencing: faro_pipeline.py --client-uid verifica zonas autorizadas
```
