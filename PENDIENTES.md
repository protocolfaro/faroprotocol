# PENDIENTES — Faro Protocol
> Última actualización: 2026-04-14

---

## COMPLETADO HOY — SESIÓN 2 (2026-04-14)

- [x] CORS multi-origen: funciones Netlify + webhook Railway aceptan faroprotocol.com y www.faroprotocol.com
- [x] CSP corregido: agrega Cesium (script/style/img), Railway (connect-src), worker-src blob: — bug silencioso que bloqueaba el visor
- [x] FIREBASE_SERVICE_ACCOUNT en Railway: acepta JSON string (Railway) y path de archivo (local dev)
- [x] CORS Railway: reemplaza wildcard `*` por lista de orígenes específicos
- [x] Redirect `/portal` → `outputs/visor_faro_v2.html` en Netlify (URL limpia para emails)
- [x] PORTAL_URL default apunta a `/portal` en vez del root
- Commits: `3e9027c`, `390c727`, `c6f1a7f`

---

## COMPLETADO HOY — SESIÓN 1 (2026-04-14)

- [x] Railway webhook live: https://faroprotocol-production.up.railway.app
- [x] Lemon Squeezy tienda creada: https://faroprotocol.lemonsqueezy.com
- [x] Producto Observer USD 2.500/mes creado en Lemon Squeezy
- [x] `faro_webhook.py` reconstruido (estaba 0 bytes) — Flask server completo
- [x] `main.py` + `Procfile` + `railway.json` para deploy Railway
- [x] `_WEBHOOK_URL` en visor apunta a Railway production
- [x] CORS Netlify functions corregido (faro-protocol.netlify.app)
- [x] Arquitectura global V14: cliente dibuja zona en mapa, pipeline 24h

---

## PENDIENTES — ACCIÓN EXTERNA REQUERIDA

### Lemon Squeezy (dashboard manual)
- [ ] Crear producto **Analyst** — USD 9.000/mes
- [ ] Crear producto **Sovereign** — USD 17.000/mes
- [ ] Crear producto **Enterprise** — USD 3.200/sector/mes
- [ ] Copiar IDs de los 4 productos al `faro_website.html` (reemplazar `LEMON_PRODUCT_ID_*`)
- [ ] Configurar webhook URL en Lemon Squeezy Dashboard → `https://faroprotocol-production.up.railway.app/webhooks/lemon`
- [ ] Copiar Webhook Secret al `.env` Railway como `LEMON_SQUEEZY_SECRET`
- [ ] Conectar banco para pagos reales

### Comercial
- [ ] Responder a Verónica con demo del visor 3D

---

## CORRECCIONES SISTEMA

- [x] Agregar sector `tierras_raras` en `faro_stress_index.py` — modelo FSI Li/REE/minerales críticos (2026-04-09)
- [x] Crear área `punta_colorada` para Emiliano González — Score 41, SAR -11.7 dB, NDVI 0.098 (2026-04-09)
- [x] Separar Minería de Tierras Raras como sector independiente — `_stress_mining()` + `_stress_tierras_raras()` (2026-04-09)

---

## CORRECCIONES NETLIFY / FIREBASE

- [ ] Verificar que Firebase Auth funciona con login real (esperar a Verónica)
- [ ] Testear Faro Week — que el email de vencimiento llega (vence 2026-04-15)
- [ ] Verificar que signed URLs funcionan en producción (descargar un reporte real)

---

## PENDIENTES EMILIANO GONZÁLEZ

- [ ] Confirmar su email
- [ ] Crear acceso con 5 zonas:
  - `punta_colorada` — Minería / Tierras Raras
  - `vaca_muerta`    — O&G / Energía
  - `rotterdam`      — Marítimo
  - `malacca`        — Shipping
  - `pilbara`        — Minería
- [ ] Una zona por sector: O&G · Energía · Marítimo · Shipping · Minería/Tierras Raras
- [x] Crear área `punta_colorada` en `faro_areas/` antes de hacer el onboarding — ✅ completado (2026-04-09)

Comando cuando esté listo:
```bash
python gen_portal_key.py emiliano@empresa.com \
  --areas punta_colorada,vaca_muerta,rotterdam,malacca,pilbara \
  --name "Emiliano González" \
  --manual
```

---

## PRECIOS EN MANUAL — CONFIRMADOS (2026-04-09)

| Plan | Precio | Áreas |
|------|--------|-------|
| Observer | USD 2.500/mes | 1 área |
| Analyst | USD 9.000/mes | 3 áreas |
| Sovereign | USD 17.000/mes | ilimitadas |
| Enterprise | USD 3.200/sector/mes | multi-sector |

---

## BRICOLAGE

- [ ] Versión de 4 paneles con imágenes reales sin errores
- [ ] Publicar en LinkedIn y X cuando esté perfecto
- [ ] No publicar hasta tener al menos 2 clientes confirmados

---

## PORTAL HTML

- [ ] **Solo hacer cambios de noche Argentina** para no afectar a Verónica mientras usa el portal
- [ ] Correr `python faro_admin.py` antes de cualquier cambio — si hay sesión activa, esperar
- [ ] Ventana segura sugerida: 01:00–07:00 ART (04:00–10:00 UTC)
