# FARO PROTOCOL — Checklist para Lautaro
> Actualizado: 2026-04-05 — V6 Auto-runner + Email delivery

Todo lo que falta para que el sistema corra solo y el cliente pueda contratar.
Cada paso tiene el comando exacto. Estimado total: ~45 minutos.

---

## PASO 1 — Formspree (el cliente puede contactarte)
**Sin esto, el formulario de la web no envía nada.**

1. Ir a https://formspree.io con el navegador
2. Registrarse con `protocolfaro@gmail.com`
3. Click en "New Form" → nombre: `faro-contact`
4. Copiar el ID que aparece (ej: `xpwzgkqb`)
5. Abrir `faro_website.html` y buscar la línea:
   ```
   const FORMSPREE_ID='YOUR_FORM_ID';
   ```
   Reemplazar `YOUR_FORM_ID` por el ID real (ej: `xpwzgkqb`)

---

## PASO 2 — Gmail App Password (entrega de reportes)
**Sin esto, faro_deliver.py no puede enviar emails a clientes.**

1. Ir a https://myaccount.google.com con `protocolfaro@gmail.com`
2. Seguridad → Verificación en dos pasos (activar si no está)
3. Seguridad → Contraseñas de aplicaciones
4. Crear nueva → nombre: `Faro Protocol`
5. Copiar la contraseña de 16 caracteres (ej: `abcd efgh ijkl mnop`)
6. Abrir `.env` en la raíz del proyecto y reemplazar:
   ```
   GMAIL_APP_PASS=xxxx xxxx xxxx xxxx
   ```
   por la contraseña real

7. Testear sin enviar:
   ```bash
   cd "C:\Users\Usuario\Desktop\Faro-index"
   python faro_deliver.py --area cordoba --test
   ```
   Debe mostrar `[TEST] Email listo para:` sin errores.

---

## PASO 3 — Groq API Key (agentes con LLM)
**Sin esto, Hermes y Paperclip corren en modo básico sin IA.**

1. Ir a https://console.groq.com → registrarse (gratis)
2. API Keys → Create API Key → copiar (empieza con `gsk_...`)
3. Abrir `.env` y reemplazar:
   ```
   GROQ_API_KEY=gsk_...
   ```
   por la key real

4. Testear:
   ```bash
   python MachinaOS/paperclip_agent.py --raw
   ```

---

## PASO 4 — Instalar Auto-runner en Task Scheduler
**Sin esto, el pipeline no corre automáticamente cada lunes.**

Abrir CMD **como Administrador** (click derecho → Ejecutar como administrador):

```bash
cd "C:\Users\Usuario\Desktop\Faro-index"
python faro_auto.py --instalar
python faro_auto.py --status
```

Debe mostrar `OK — Tarea registrada`. Verificar que aparecen dos tareas:
- `FaroProtocol_PriceUpdate` — lunes 08:00 (precios)
- `FaroProtocol_AutoRunner`  — lunes 07:00 (pipeline + entrega)

---

## PASO 5 — Commit + Push a GitHub Pages
**Sin esto, el website sigue mostrando la versión vieja. Todos los cambios de hoy son locales.**

### 5a — Generar token en GitHub
1. Ir a https://github.com/settings/tokens
2. "Generate new token (classic)"
3. Nombre: `faroprotocol-push` · Expiration: 90 days · Scope: tildar `repo`
4. Copiar el token (`ghp_...`) — solo se muestra una vez

### 5b — Commit de los cambios de hoy
```bash
cd "C:\Users\Usuario\Desktop\Faro-index"
git add faro_auto.py faro_deliver.py faro_sar_georef.py faro_client_portal.html faro_website.html requirements.txt .env.example .gitignore CLAUDE.md PUSH_INSTRUCTIONS.md data.json
git commit -m "V6: auto-runner semanal, email delivery, portal dinamico, fix SAR calibracion, migracion Groq"
```

### 5c — Push (reemplazar TU_TOKEN con el token del paso anterior)
```bash
git remote set-url origin https://protocolfaro:TU_TOKEN@github.com/protocolfaro/faroprotocol.git
git push origin master
```

### 5d — Verificar
Abrir https://protocolfaro.github.io/faroprotocol
GitHub Pages tarda ~2 minutos en actualizar.

---

## PASO 6 — Regenerar SAR Córdoba con calibración correcta (opcional pero recomendado)
**El SAR actual tiene escala incorrecta (19.1 dB). La fórmula ya fue corregida. Solo falta re-correr.**

```bash
cd "C:\Users\Usuario\Desktop\Faro-index"
python faro_sar_georef.py --area cordoba --sar-file "sar_downloads/S1A_IW_GRDH_1SDV_20260325T232707_20260325T232732_063790_080578_0C79.SAFE/measurement/s1a-iw-grd-vv-20260325t232707-20260325t232732-063790-080578-001.tiff"
python faro_engine.py --area cordoba
```

Hermes debería dar GO después de este paso.

---

## PASO 7 — Firebase Auth (requerido antes de trabajar con gobiernos)
**El portal actual tiene la lógica de login visible en el código fuente — no pasa auditoría de seguridad gubernamental.**

Qué hay que hacer:
1. Crear proyecto en https://firebase.google.com (gratis con cuenta Google)
2. Activar Authentication → Email/Password
3. Crear usuarios desde el panel de Firebase (sin tocar código)
4. Reemplazar el bloque ACCOUNTS del portal por verificación contra Firebase

Tiempo estimado: medio día con Claude Code.
Hacer DESPUÉS de que los pasos 1-6 estén funcionando.

---

## Resumen de lo que ya funciona sin hacer nada

| Componente | Estado |
|---|---|
| Price updater (precios semanales) | ✅ Corre automático lunes 08:00 |
| Pipeline Córdoba (manual) | ✅ `python faro_engine.py --area cordoba` |
| Portal de clientes (login PBKDF2) | ✅ `demo@faroprotocol.io` funciona |
| SHA-256 y datos reales Córdoba | ✅ Score 61.0 · NDVI 0.4444 · Rinde 2.69 t/ha |
| Website en GitHub Pages | ✅ Publicado (desactualizado hasta el push) |

## Lo que habilita cada paso de arriba

| Paso | Qué habilita |
|---|---|
| 1 — Formspree | El cliente puede contactarte desde la web |
| 2 — Gmail | El sistema envía reportes automáticamente a clientes |
| 3 — Groq | Hermes y Paperclip con resúmenes LLM reales |
| 4 — Task Scheduler | Pipeline + entrega sin intervención manual |
| 5 — Push | El website muestra los cambios de hoy |
| 6 — SAR re-run | Hermes da GO en Córdoba (calidad verificada) |
