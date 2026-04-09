# FARO PROTOCOL — Guía para Claude Code
> Última actualización: 2026-04-08 — V13 · SISTEMA 100% LIVE · https://faro-protocol.netlify.app
> Arquitecto del sistema: Emilio (hijo)
> Conocimiento de campo y contactos: Padre (fundador)

---

## Estado del sistema — V12 (2026-04-08) — Netlify live · Firebase live · primer cliente

### Pipeline operativo — 9 áreas globales

| Área | Vertical | Score | FSI | NDVI | SAR dB | Estado | SHA-256 |
|------|----------|-------|-----|------|--------|--------|---------|
| cordoba | agro | 49 | 49.7 Moderado | 0.4444 | -13.9 | ⚠️ ALERTA | 08ca07bf... |
| balcarce | agro | 70 | 0.0 Sin estrés | 0.5328 | 15.5 | ✅ OK | 5233ec23... |
| vaca_muerta | energia | 42 | 15.0 Sin estrés | 0.0941 | — | ✅ OK | fd2a23eb... |
| rotterdam | maritimo | 38 | 0.0 Sin estrés | 0.2799 | — | ✅ OK | 99fa9be2... |
| permian | oil_gas | 52 | — | 0.1383 | — | ✅ OK | 0b468b75... |
| pilbara | mining_iron | 41 | — | 0.0840 | — | ✅ OK | 54789e08... |
| amazonas | deforestacion | 66 | 36.0 Leve | 0.2834 | -5.4 | ✅ OK | 22dbb6ac... |
| indiana | agro | 38 | — | 0.2594 | -9.3 | ⚠️ ALERTA | 950dd57c... |
| malacca | shipping | 42 | — | — | — | ✅ OK | c3a33f4b... |

**Nota Córdoba:** Score 49 (ALERTA). SAR calibrado en -13.9 dB. Rinde 2.02 t/ha vs histórico 3.44 t/ha (MAGyP, Marcos Juárez). FSI 49.7 (Moderado) confirma estrés real — revisar con campo antes del pitch.
**Nota rotterdam:** vertical corregida de 'energia' → 'maritimo' (2026-04-08).

### Quality Gate — Certificados externos
Áreas con score ≥ 50 y sin ALERTA reciben Sello Verde: **balcarce, permian, amazonas**.
Áreas excluidas de outputs externos (score < 50 o ALERTA): cordoba, indiana, malacca, pilbara, rotterdam, vaca_muerta.
Usar `mode='internal'` para auditoría interna de todas las áreas.

---

## Arquitectura definitiva

### Pipeline principal
```
faro_pipeline.py --area <zona>       # orquestador multi-área
  → faro_sar_auto.py                 # descarga S1 + S2 + pipeline completo por área
    → faro_sar_pipeline.py           # descarga Sentinel-1 GRD
    → faro_s2_pipeline.py            # descarga Sentinel-2 L2A + NDVI
    → faro_sar_georef.py             # georreferencia SAR → dB (20*log10(DN)-52)
    → faro_fusion.py                 # fusión SAR + NDVI → Índice Faro + PNG
    → faro_vision.py                 # visión computacional: vigor + anomalías
    → faro_engine.py                 # motor central: score + rinde + alerta
    → faro_finance.py                # business case USD
```

### Módulos de salida
```
faro_certificado.py       # Sello Verde: PNG con SHA-256 verificado
faro_bricolage.py         # Imagen 1800×2400 px para LinkedIn (4 zonas)
faro_dashboard.py         # Dashboard dinámico: multicapa + ROI + alertas
faro_report_engine.py     # Reporte certificado estático: KPIs + Economic Impact
faro_card.py              # Cards LinkedIn/WhatsApp desde data.json
faro_portfolio_global.py  # Portfolio 1800×1200 px: 4 sectores + FSI + SHA compuesto
faro_stress_index.py      # Faro Stress Index (FSI): modelos por sector agro/forest/maritimo/energia
faro_quality.py           # Quality Gate compartido: score, SHA-256, ALERTA
QUALITY_STANDARD.md       # Estándar de calidad v1.0 (fuente de verdad)
```

### Automatización y entrega
```
faro_auto.py            # scheduler semanal (Task Scheduler Windows, lunes 10:00)
faro_price_updater.py   # precios reales (urea/Brent/USD-ARS → datos/faro_prices.json)
faro_deliver.py         # envío por Gmail SMTP
```

### MachinaOS (agentes IA)
```
MachinaOS/hermes_agent.py      # validación automática de outputs
MachinaOS/paperclip_agent.py   # resumen diario del sistema
MachinaOS/phone_gateway.py     # alertas WhatsApp
```

### Web
```
index.html                # GitHub Pages landing (redirige a faro_website.html)
faro_website.html         # landing institucional bilingüe EN/ES
faro_client_portal.html   # portal de clientes con login PBKDF2
faro_visor.html           # visor 3D three.js (NDVI/SAR/Fusión/Anomalías)
```

### Configuración y datos
```
faro_areas/               # configs por zona (JSON): bounds, polygon, vertical
faro_areas.py             # loader: load_area() / list_areas()
data.json                 # estado global del sistema (generado por faro_engine)
datos/                    # baselines MAGyP, precios, CSV de rinde (gitignoreado)
```

### Utilidades de datos
```
faro_rinde_import.py      # importa CSV de rinde → baseline departamental
faro_siia_download.py     # descarga histórico SIIA/MAGyP
```

### Portal y autenticación — SEGURIDAD MÁXIMA (V10)
```
gen_portal_key.py              # CAPA 7: crea clientes en Firebase, Faro Week, --extend, --revoke, --list
reset_demo_password.py         # resetea contraseña demo (legacy)
accounts.json                  # credenciales hasheadas (gitignoreado, legacy)
ver_portal.bat                 # abre el portal en el browser
faro_security_test.py          # CAPA 8: 9 categorías de pruebas de seguridad
faro_manual_cliente.py         # TAREA 2: genera PDF manual de bienvenida por cliente
expired.html                   # CAPA 9: página de conversión al vencer Faro Week
```

### Netlify Functions (seguridad servidor)
```
netlify/functions/auth-monitor.mjs        # CAPA 2+5: rate limiting + alertas por email
netlify/functions/generate-signed-url.mjs # CAPA 3+4: signed URLs por cliente + Faro Week check
netlify/functions/serve-report.mjs        # CAPA 4: sirve archivos via HMAC firmado
netlify/functions/faro-week-notifier.mjs  # CAPA 9: alertas 24h + revocación automática (daily cron)
```

---

## Arquitectura dual Dashboard vs Reporte

### Dashboard (`faro_dashboard.py`) — DINÁMICO
- Fetching automático S1+S2 con staleness check (7 días)
- Visualización multicapa: NDVI óptico · SAR radar · mapa de calor fusión
- ROI proyectado por sector:
  - Agro: CBOT precios (soja/maíz/trigo)
  - Marítimo: USD 35.000/día demurrage por buque
  - O&G: 1.5%/mes sobre CAPEX de perforación
  - Minería: precio mineral × tonelaje diario
- Alertas: desvío rinde >5% · anomalía logística >4hs · caída SAR >10%

```bash
python faro_dashboard.py --area balcarce
python faro_dashboard.py --all --no-s2   # sin descarga nueva
```

### Reporte certificado (`faro_report_engine.py`) — ESTÁTICO / INMUTABLE
- KPIs sectoriales:
  - Agro: rinde estimado · silobag count proxy · % anomalías
  - Marítimo: vessel/truck count proxy · demora acumulada
  - O&G: avance estructural % · índice actividad
  - Deforestación: cobertura perdida % · pérdida tCO2
- Economic Impact: resumen ejecutivo con supuestos explícitos para auditoría
- SHA-256 del reporte emitido + entrada en `audit_log.jsonl`
- 3 triggers:

```bash
python faro_report_engine.py --area cordoba --trigger on_demand
python faro_report_engine.py --all --trigger scheduled
python faro_report_engine.py --area cordoba --trigger event_driven --condition "score < 50"
```

---

## Comandos de uso frecuente

```bash
# Pipeline completo (todas las áreas)
python faro_pipeline.py --area cordoba balcarce vaca_muerta rotterdam permian pilbara amazonas indiana malacca

# Pipeline con Sentinel-2 forzado
python faro_pipeline.py --area balcarce --with-s2

# Regenerar certificados
python faro_certificado.py --all

# Regenerar bricolage LinkedIn
python faro_bricolage.py

# Dashboard de monitoreo (sin descarga)
python faro_dashboard.py --area balcarce --no-s2

# Reporte certificado on-demand
python faro_report_engine.py --area cordoba --trigger on_demand

# Reporte solo si hay alerta
python faro_report_engine.py --area cordoba --trigger event_driven --condition "estado == 'ALERTA'"

# Precios de mercado
python faro_price_updater.py

# Card para LinkedIn
python faro_card.py --area cordoba

# Ver portal de clientes
python -m http.server 8080
# → http://localhost:8080/faro_client_portal.html
```

---

---

## Estructura de directorios

```
faro_protocol/
├── CLAUDE.md                     ← este archivo
├── faro_areas/                   ← configs JSON por zona (9 áreas)
├── MachinaOS/                    ← agentes IA
│   ├── hermes_agent.py
│   ├── paperclip_agent.py
│   └── phone_gateway.py
├── archive/                      ← archivos legacy
├── datos/                        ← gitignoreado: baselines, precios, CSV
├── datos_sar/                    ← gitignoreado: SAFE dirs descargados
├── datos_s2/                     ← gitignoreado: SAFE dirs S2 descargados
├── logs/                         ← gitignoreado: logs del sistema
│
├── [Pipeline principal]
│   faro_pipeline.py
│   faro_sar_auto.py
│   faro_sar_pipeline.py
│   faro_s2_pipeline.py
│   faro_sar_georef.py
│   faro_closdi_pipeline.py
│   faro_fusion.py
│   faro_vision.py
│   faro_engine.py
│   faro_finance.py
│
├── [Salidas]
│   faro_certificado.py
│   faro_bricolage.py
│   faro_dashboard.py
│   faro_report_engine.py
│   faro_card.py
│
├── [Automatización]
│   faro_auto.py
│   faro_price_updater.py
│   faro_deliver.py
│
├── [Datos de campo]
│   faro_rinde_import.py
│   faro_siia_download.py
│
├── [Portal y auth]
│   gen_portal_key.py
│   reset_demo_password.py
│
├── [Web]
│   index.html
│   faro_website.html
│   faro_client_portal.html
│   faro_visor.html
│
├── [Config]
│   data.json
│   requirements.txt
│   .env (gitignoreado)
│   .env.example
│   .gitignore
│   faro_areas.py
│   faro_clientes.json
│   accounts.json (gitignoreado)
│   audit_log.jsonl (gitignoreado)
│
└── [Assets generados]
    faro_bricolage_linkedin.png
    faro_cert_*.png            ← certificados (9 áreas)
    faro_reporte_fusion_*.png  ← reportes de fusión (9 áreas)
    faro_reporte_fusion_*.sha256
    faro_report_*.png          ← reportes certificados emitidos
    faro_report_*.sha256
    faro_dashboard_*.png       ← dashboards generados
    faro_vision_*.json
    faro_clasificacion_*.png
    faro_hash.txt.ots          ← sello blockchain OpenTimestamps
```

---

## Auditoría de módulos — 2026-04-08

| Módulo | Estado | Notas |
|--------|--------|-------|
| `faro_pipeline.py` | ✅ OK | balcarce score 69.5, SHA sellado |
| `faro_certificado.py --all` | ⚠️ DEGRADADO | 3/9 pasan QualityGate externo; resto omitidos por score < 50 o ALERTA — comportamiento correcto por diseño |
| `faro_portfolio_global.py` | ✅ OK | 1800×1200px, contrast stretch, SHA compuesto |
| `faro_dashboard.py` | ✅ OK | balcarce Score 69.5, 0 alertas |
| `faro_stress_index.py` | ✅ OK | modelos por sector, scores no-zero |
| `MachinaOS/paperclip_agent.py` | ✅ OK | (fix: ndvi_tif opcional para áreas SAR-only) |
| `MachinaOS/hermes_agent.py` | ✅ OK | requiere `--area <nombre>`; SAR warning en balcarce es dato, no bug |
| `faro_price_updater.py` | ✅ OK | (fix: agregado `--dry-run`) |
| `faro_quality.py` | ✅ OK | QualityGate integrado en 3 módulos |

### Bugs corregidos en esta auditoría
1. `faro_certificado.py --all` — abortaba con `QualityViolation` en Córdoba. Fix: try/except por área.
2. `MachinaOS/paperclip_agent.py` — `KeyError: 'ndvi_tif'` en áreas sin campo (malacca, permian, pilbara). Fix: `.get()` con fallback.
3. `faro_price_updater.py` — flag `--dry-run` inexistente. Fix: agregado como alias de `--log 5`.
4. `faro_areas/rotterdam.json` — `vertical: "energia"` incorrecto. Fix: corregido a `"maritimo"`.

---

## Seguridad — Estado V10 (2026-04-08)

| Capa | Descripción | Estado | Implementación |
|------|-------------|--------|----------------|
| 1 | Firebase Auth (Email/Password, JWT RS256, 8h sesión) | ✅ | `faro_client_portal.html` |
| 2 | Rate limiting (3→captcha, 5→lockout 30min, 10→bloqueo permanente), CSRF, Headers | ✅ | `auth-monitor.mjs` + `netlify.toml` |
| 3 | Autorización por cliente (áreas asignadas, aislamiento total) | ✅ | `generate-signed-url.mjs` |
| 4 | Signed URLs HMAC-SHA256 (1h), archivos no expuestos directamente | ✅ | `serve-report.mjs` |
| 5 | Monitoreo: alertas email por IP nueva, intentos fallidos, descarga masiva | ✅ | `auth-monitor.mjs` |
| 6 | Netlify: HTTPS automático, variables de entorno, headers de seguridad | ✅ | `netlify.toml` |
| 7 | gen_portal_key.py con Firebase Admin: crear usuario, áreas, email bienvenida | ✅ | `gen_portal_key.py` |
| 8 | Penetration testing: 9 categorías de pruebas automatizadas | ✅ | `faro_security_test.py` |
| 9 | Faro Week: 7 días, alerta 24h, revocación automática, página conversión | ✅ | `faro-week-notifier.mjs` + `expired.html` |

### Comandos de seguridad
```bash
# Crear cliente con áreas + manual PDF
python gen_portal_key.py cliente@empresa.com --areas cordoba,balcarce --manual

# Extender acceso Faro Week
python gen_portal_key.py --extend cliente@empresa.com --days 7

# Revocar acceso inmediatamente
python gen_portal_key.py --revoke cliente@empresa.com

# Listar clientes
python gen_portal_key.py --list

# Correr security test (ANTES de cada onboarding nuevo)
python faro_security_test.py --url https://faro-protocol.netlify.app

# Generar manual PDF independiente
python faro_manual_cliente.py --email cliente@empresa.com --name "Empresa SA" --areas cordoba

# Enviar alertas de vencimiento manualmente (normalmente lo hace faro-week-notifier diario)
python gen_portal_key.py --check-expiry
```

### Firebase + Netlify — Estado V12 (2026-04-08) — LIVE

**Proyecto:** `faro-protocol-906a5` · **Auth domain:** `faro-protocol-906a5.firebaseapp.com`
**Portal:** https://faro-protocol.netlify.app

#### Estado de configuración

**Firebase (console.firebase.google.com/project/faro-protocol-906a5):**
```
✅ Service Account descargada → firebase-service-account.json (gitignoreado)
✅ .env local: FIREBASE_SERVICE_ACCOUNT=firebase-service-account.json
✅ Config pública integrada en faro_client_portal.html
✅ Authentication → Email/Password → Habilitado
✅ Authorized domains → faroprotocol.netlify.app autorizado
✅ Firestore → DB creada → producción → southamerica-east1
✅ Firestore → Security Rules publicadas
```

**Netlify Dashboard (app.netlify.com → Site → Environment variables):**
```
✅ FIREBASE_SERVICE_ACCOUNT  = [base64 del service account — corregido y republicado]
✅ FIREBASE_PROJECT_ID       = faro-protocol-906a5
✅ SIGNED_URL_SECRET         = 1ee45256a5233da6ccc7f1ade9568bfd823ea4c5f3593069353bf22c796d0cb0
✅ GMAIL_USER                = protocolfaro@gmail.com
✅ GMAIL_APP_PASS            = [configurado]
✅ ADMIN_EMAIL               = protocolfaro@gmail.com
✅ PORTAL_URL                = https://faro-protocol.netlify.app  ← corregido (tenía faro sin guión)
✅ Deploy final publicado → https://faro-protocol.netlify.app
```

**Nota:** Las Netlify Functions esperan `FIREBASE_SERVICE_ACCOUNT` en base64.
Generar con: `python -c "import base64; print(base64.b64encode(open('firebase-service-account.json','rb').read()).decode())"`

**Firestore Security Rules (publicar en Firebase Console):**
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

#### Primer cliente — Verónica (Smart Future Labs)
```
✅ Email  : veronica@smartfuturelabs.com
✅ UID    : SgsDkLAk2xfsQcab9m2qfsdbtpg1
✅ Áreas  : rotterdam · amazonas · vaca_muerta · malacca
✅ Expira : 2026-04-15 (Faro Week 7 días)
✅ Manual : faro_manual_veronica.pdf (SHA-256: b697914a...e6841b4b)
✅ Email de bienvenida enviado con link de primer acceso
```

## Pendientes técnicos prioritarios

### ✅ Firebase + Netlify — COMPLETADO (2026-04-08)
El portal está 100% operativo en https://faro-protocol.netlify.app
Firebase Auth, Firestore y las 9 capas de seguridad están activas en producción.

### 🔴 Datos de campo (Padre → acción inmediata)
```bash
python faro_rinde_import.py --csv datos_basso.csv
# Columnas: lote, año, cultivo, rinde_tn_ha
# Con esto el motor deja de usar promedios nacionales
# y predice contra el historial real del campo
```

### 🟡 Score Córdoba (calibración, no bug)
Score 49 (ALERTA). FSI 49.7 (Moderado). SAR calibrado en -13.9 dB.
Rinde 2.02 t/ha vs histórico departamental 3.44 t/ha.
Revisar umbrales en `faro_engine.py` antes del pitch.
El QualityGate lo excluye automáticamente de outputs externos.

### 🟡 Credenciales faltantes (pendiente acción externa)
- `GROQ_API_KEY` → console.groq.com (gratis) — Hermes/Paperclip en modo local sin esto
- `GMAIL_APP_PASS` → Google → Seguridad → App Passwords
- `COPERNICUS_USER/PASS` → dataspace.copernicus.eu — sin esto no hay descarga de escenas nuevas
- `WHATSAPP_TOKEN/PHONE_ID/DEST` → Meta for Developers

### 🟡 Portal externo
Hoy: `python -m http.server 8080` (localhost).
Para clientes: deploy en Netlify (gratis, 5 min desde GitHub).

### 🟢 Website — datos hardcodeados
`faro_website.html` tiene métricas ficticias ("98.2% accuracy", "1,020K bbl/d").
No bloqueante para el piloto pero riesgo legal si escala.

---

## Arquitectura técnica

```
Satelital:
  Copernicus CDSE API → Sentinel-1 GRD + Sentinel-2 L2A (sin GEE)
  rasterio, numpy → procesamiento raster
  Fórmula SAR: 20*log10(DN) - 52.0 (offset empírico CDSE)

Motor económico:
  FACTOR_BIOMASA_A_GRANO = 0.44 (Harvest Index, Spaeth et al. 1987)
  Rinde ref: lookup jerárquico → departamento > provincia > nacional > fallback
  Baseline: MAGyP 2026-03 + SIIA 2000-2024 (272 registros)

Verificación:
  SHA-256 en cada reporte generado
  audit_log.jsonl: append-only, no modificable

Modelo de alertas (dashboard):
  Agro: desvío >5% del histórico → ALERTA
  Marítimo: SAR < -12 dB zona portuaria → demora >4hs
  O&G/Mining: actividad < 40/100 → MEDIUM
  NDVI < 0.15 → zona crítica (todos)
```

---

## Diseño visual (coherencia en todo el proyecto)

```css
--bg:   #0a0a1a   /* fondo principal */
--bg2:  #0d1020
--gold: #c9a84c   /* dorado — color de marca */
--gl:   #e2c97e   /* dorado claro */
--w:    #f2ede4   /* texto principal */
--grey: #888899
--green:#2d8c5e
--red:  #b03030
--blue: #2a6496
--amber:#c07a30

--serif: 'Cormorant Garamond'   /* títulos */
--mono:  'JetBrains Mono'       /* hashes, datos, código */
```

Estética: oscura, premium, estilo Bloomberg / datos financieros.

---

## Pitch para primer cliente (Córdoba)

**Línea de apertura:**
> "El 4 de abril de 2026, antes de que nadie publicara nada, nuestro sistema vio estos campos y estimó 2.02 t/ha con alta confianza. Está sellado con SHA-256. Cuando salga el dato oficial del INDEC/Bolsa de Cereales, lo comparamos. ¿Querés apostar?"

**Demo (5 minutos):**
1. Abrir `faro_website.html` → tarjeta "✓ Validated · Marcos Juárez"
2. `sha256sum faro_reporte_fusion_cordoba.png` → coincide con el .sha256
3. Abrir `data.json` → "esto lo generó el sistema, sellado. Ningún humano lo editó después."
4. Abrir `faro_visor.html` → terreno 3D con NDVI/SAR en vivo

**Para escalar el pitch:**
1. Datos de Basso (Padre) → calibración específica del campo
2. Dato oficial 2026 → primer contraste real
3. Segunda área piloto (Balcarce procesada = dos puntos es tendencia)
4. GROQ_API_KEY → Hermes + Paperclip con LLM (gratis)
