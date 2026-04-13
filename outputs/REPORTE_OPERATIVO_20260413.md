# REPORTE OPERATIVO — FARO PROTOCOL
**Fecha:** 13/04/2026  
**Auditor:** Sistema interno  
**Scope:** Ciclo de negocio completo — perspectiva cliente y admin

---

## PARTE 1: PERSPECTIVA CLIENTE (10 checkpoints)

### C1 — Descubrimiento: ¿El sitio web es accesible y profesional?
**✅ Funciona**  
`faro_website.html` está desplegado en Netlify con diseño oscuro profesional, secciones de producto, métricas y CTA. Headers de seguridad configurados en `netlify.toml` (X-Frame-Options, CSP, HSTS, etc.).

---

### C2 — Contacto inicial: ¿El formulario de contacto funciona?
**⚠️ Funciona parcialmente — falta backend real**  
El formulario usa `mailto:` directo. Depende del cliente de correo instalado en el dispositivo del visitante. No hay Formspree, Netlify Forms ni endpoint POST. En mobile o entornos sin cliente de correo, el CTA de contacto falla silenciosamente.

---

### C3 — Onboarding: ¿El cliente puede registrarse y acceder al portal?
**✅ Funciona**  
Firebase Auth (Email/Password) activo en proyecto `faro-protocol-906a5`. Firestore `/clients/{uid}` almacena perfil. Netlify Function `auth-monitor.mjs` registra eventos de autenticación en `audit_log.jsonl`.

---

### C4 — Portal: ¿El cliente puede ver su reporte en el portal?
**⚠️ Funciona parcialmente — reportes pueden estar desactualizados**  
Los reportes se sirven via `serve-report.mjs` con signed URLs HMAC-SHA256 (1h expiry). Sin embargo, el pipeline semanal (`faro_auto.py`) **no hace `git commit + push`** después de generar nuevos reportes. Netlify sirve los archivos del último commit — si el reporte nuevo no se commitea, el cliente ve la versión anterior.

---

### C5 — Seguridad de acceso: ¿Los reportes son privados y protegidos?
**✅ Funciona**  
Arquitectura de 9 capas: Firebase Auth JWT → custom claims → autorización por área → signed URLs (1h) → rate limiting → monitoring → Netlify headers → Firebase Admin SDK → pen testing documentado. `accounts.json` y `.env` bloqueados en `netlify.toml`.

---

### C6 — Visor satelital: ¿El cliente puede visualizar su área en el visor?
**✅ Funciona**  
Tres visores disponibles:
- `visor_faro.html` — Leaflet + ESRI World Imagery, 6 sectores, overlay SAR pulsante
- `visor_faro_v2.html` — CesiumJS 1.104, terreno 3D real, 6 sectores, SAR entities
- `visor_3d.html` — Three.js r160, terreno sintético FBM, shader SAR GLSL

---

### C7 — Datos del reporte: ¿Los datos son reales y verificables?
**✅ Funciona**  
`data.json` contiene valores reales de NDVI, SAR backscatter y scores por área. SHA-256 del estado de escena disponible via SubtleCrypto en los visores. Cadena completa: Copernicus → `faro_engine.py` → `data.json` → portal.

---

### C8 — Notificaciones: ¿El cliente recibe alertas del sistema?
**⚠️ Funciona parcialmente — solo email, sin WhatsApp**  
Notificaciones por email funcionales via Gmail (`GMAIL_USER` + `GMAIL_APP_PASS` configurados). `faro-week-notifier` en Netlify Functions (`schedule = "0 9 * * *"`). **WhatsApp no operativo**: `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_DEST` son placeholders sin configurar en `.env`.

---

### C9 — Credibilidad: ¿Las métricas del sitio son verificables?
**⚠️ Riesgo legal — datos ficticios en marketing**  
`faro_website.html` muestra "98.2% accuracy", "1,020K bbl/d analyzed" y otras métricas sin respaldo verificable. A escala comercial, esto representa riesgo regulatorio (publicidad engañosa). Aceptable en MVP, debe documentarse como estimaciones o removerse.

---

### C10 — Entrega del producto: ¿El ciclo completo entrega valor al cliente?
**✅ Funciona**  
El ciclo end-to-end está operativo: adquisición Copernicus → procesamiento → reporte PNG/HTML → portal con signed URLs → visor satelital interactivo. El producto central funciona.

---

## PARTE 2: PERSPECTIVA ADMIN (8 checkpoints)

### A1 — Pipeline automático: ¿El pipeline semanal corre sin intervención?
**⚠️ Funciona parcialmente — no auto-publica resultados**  
`FaroProtocol_AutoRunner` registrado en Windows Task Scheduler, estado: "Listo", próxima ejecución: 20/04/2026 10:00. El pipeline ejecuta `faro_engine.py` → Hermes → `faro_deliver.py`. **Problema crítico**: no hay `git commit + push` después de generar reportes, por lo que Netlify nunca recibe los archivos nuevos automáticamente.

---

### A2 — Generación de bricolage y certificados: ¿Se generan automáticamente?
**❌ No funciona — no están en el pipeline**  
`faro_auto.py` no llama a `faro_bricolage.py` ni a `faro_certificado.py`. Estos deben ejecutarse manualmente. Si el flujo de negocio requiere entregar certificados o reportes bricolage como parte del servicio, el admin debe intervenir manualmente en cada ciclo.

---

### A3 — Monitoreo de seguridad: ¿El sistema detecta y registra anomalías?
**✅ Funciona**  
`auth-monitor.mjs` en Netlify Functions registra eventos de auth. `audit_log.jsonl` (local, gitignoreado) registra 36+ eventos incluyendo ejecuciones de pipeline, creación de clientes (Verónica, Smart Future Labs) y generación de reportes. Bloqueado desde la web por `netlify.toml`.

---

### A4 — Gestión de clientes: ¿Se puede crear y gestionar clientes?
**✅ Funciona**  
Firebase Admin SDK + Firestore operativos. Hay registro de creación de clientes reales en `audit_log.jsonl`. Panel de admin implícito en la arquitectura.

---

### A5 — Horario del Task Scheduler: ¿El horario es correcto?
**⚠️ Inconsistencia de horario**  
`faro_auto.py` tiene comentario "07:00 ART" pero el trigger de PowerShell usa `-At 10am` (hora del sistema Windows). Si el sistema corre en UTC-3 (ART), 10:00 local = 13:00 UTC, no 10:00 UTC. El comentario dice "07:00 ART" = 10:00 UTC, lo que sugiere que la intención era 10:00 UTC pero el código configura 10:00 local. Requiere verificación del timezone del servidor.

---

### A6 — Seguridad de credenciales: ¿Las claves están protegidas?
**✅ Funciona**  
`.env` gitignoreado. Variables sensibles: `COPERNICUS_USER/PASS`, `GROQ_API_KEY`, `GMAIL_USER/APP_PASS`, `FIREBASE_SERVICE_ACCOUNT`, `FIREBASE_PROJECT_ID`, `PORTAL_URL`, `SIGNED_URL_SECRET`, `ADMIN_EMAIL` — todas configuradas. `audit_log.jsonl` gitignoreado. PNGs sí trackeados en git (28 archivos) — esto es intencional ya que `netlify.toml` los incluye en `serve-report`.

---

### A7 — Test de seguridad: ¿El pen testing está documentado y es ejecutable?
**⚠️ Funciona parcialmente — no verificado en producción**  
`faro_security_test.py` existe y está documentado. No fue posible verificar su ejecución contra la URL live en este entorno (requiere `requests` library + acceso a red). El script existe como evidencia de diligencia de seguridad pero no hay registro de última ejecución exitosa en `audit_log.jsonl`.

---

### A8 — Trazabilidad: ¿Hay audit trail completo del ciclo?
**✅ Funciona**  
`audit_log.jsonl` es append-only, local (no expuesto), con timestamps y metadata por evento. Cubre: ejecuciones de pipeline, creación de clientes, generación de reportes. Bloqueado en Netlify. La única brecha es que no registra accesos al portal (solo eventos de generación).

---

## RESUMEN EJECUTIVO

| # | Checkpoint | Estado |
|---|-----------|--------|
| C1 | Sitio web accesible y profesional | ✅ |
| C2 | Formulario de contacto | ⚠️ |
| C3 | Registro y acceso al portal | ✅ |
| C4 | Reportes actualizados en portal | ⚠️ |
| C5 | Seguridad de acceso | ✅ |
| C6 | Visor satelital | ✅ |
| C7 | Datos reales y verificables | ✅ |
| C8 | Notificaciones (email + WhatsApp) | ⚠️ |
| C9 | Métricas de marketing | ⚠️ |
| C10 | Ciclo completo de valor | ✅ |
| A1 | Pipeline automático | ⚠️ |
| A2 | Bricolage y certificados automáticos | ❌ |
| A3 | Monitoreo de seguridad | ✅ |
| A4 | Gestión de clientes | ✅ |
| A5 | Horario Task Scheduler | ⚠️ |
| A6 | Seguridad de credenciales | ✅ |
| A7 | Pen testing documentado | ⚠️ |
| A8 | Audit trail | ✅ |

**Totales: 9 ✅ — 7 ⚠️ — 1 ❌**

---

## ISSUES CRÍTICOS CON SOLUCIÓN PROPUESTA

### ❌ A2 — Bricolage y certificados no se generan automáticamente

**Problema:** `faro_auto.py` ejecuta solo `faro_engine → Hermes → faro_deliver`. `faro_bricolage.py` y `faro_certificado.py` no están en el pipeline.

**Solución:**
```python
# En faro_auto.py, después de faro_deliver:
import subprocess
subprocess.run(["python", "faro_bricolage.py"], check=True)
subprocess.run(["python", "faro_certificado.py"], check=True)
```
Agregar manejo de errores para que un fallo en bricolage/certificado no detenga el pipeline principal.

---

### ⚠️ C4 / A1 — Reportes no se publican automáticamente en Netlify

**Problema:** El pipeline genera reportes localmente pero no hace `git commit + push`. Netlify sirve la última versión commiteada, no la nueva.

**Solución:**
```python
# Al final del pipeline en faro_auto.py:
import subprocess
fecha = datetime.now().strftime("%Y%m%d_%H%M")
subprocess.run(["git", "add", "outputs/faro_report_*.png", "outputs/faro_reporte_*.png", "data.json"], check=True)
subprocess.run(["git", "commit", "-m", f"chore: pipeline auto {fecha}"], check=True)
subprocess.run(["git", "push", "origin", "master"], check=True)
```
Requiere que git esté autenticado en el servidor (SSH key o token en URL remota).

---

### ⚠️ C2 — Formulario de contacto sin backend

**Problema:** `mailto:` falla en entornos sin cliente de correo (mobile, webmail-only).

**Solución opción A (gratis):** Activar Netlify Forms en `faro_website.html`:
```html
<form name="contacto" method="POST" data-netlify="true">
  <input type="hidden" name="form-name" value="contacto" />
  ...
</form>
```

**Solución opción B:** Integrar Formspree (plan free: 50 envíos/mes):
```html
<form action="https://formspree.io/f/YOUR_ID" method="POST">
```

---

### ⚠️ C8 — WhatsApp no configurado

**Problema:** `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_DEST` son placeholders.

**Solución:** Registrar número en Meta Business > WhatsApp Business API. Completar en `.env`:
```
WHATSAPP_TOKEN=EAAxxxxxxxxxx
WHATSAPP_PHONE_ID=1234567890
WHATSAPP_DEST=5491112345678
```
El código ya está implementado — solo falta configurar las credenciales.

---

### ⚠️ C9 — Métricas de marketing sin respaldo

**Problema:** "98.2% accuracy", "1,020K bbl/d analyzed" sin fuente verificable.

**Solución:** Reemplazar con métricas reales de `data.json` (scores, NDVI, SAR), agregar disclaimer "basado en datos de muestra" o mover cifras a sección "Metodología" con contexto. Alternativa: remover métricas específicas y reemplazar con rangos ("scores entre 38–70 en áreas monitoreadas").

---

### ⚠️ A5 — Inconsistencia de horario en Task Scheduler

**Problema:** Comentario dice "07:00 ART" pero trigger configurado a `10am` hora local.

**Solución:** Verificar timezone del servidor Windows. Si el sistema corre en ART (UTC-3):
- Para ejecutar a 07:00 ART = 10:00 UTC → el trigger a `10am` es correcto si el servidor está en UTC
- Si el servidor está en ART, el trigger a `10am` = 13:00 UTC (incorrecto)

Acción: `Get-TimeZone` en PowerShell para confirmar. Actualizar comentario en código para reflejar la realidad, o ajustar el trigger.

---

### ⚠️ A7 — Pen testing sin registro de última ejecución

**Problema:** No hay entrada en `audit_log.jsonl` que registre ejecución de `faro_security_test.py`.

**Solución:** Agregar al final de `faro_security_test.py`:
```python
import json, datetime
with open("audit_log.jsonl", "a") as f:
    f.write(json.dumps({
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "event": "security_test_run",
        "results": results_summary
    }) + "\n")
```
Ejecutar manualmente ahora para tener baseline documentado.

---

*Reporte generado: 2026-04-13 | Faro Protocol v1.x | Branch: master*
