# PENDIENTES — Faro Protocol
> ⚠️ NO tocar nada hasta que Verónica use el portal y confirme que funciona.
> Para cambios en el portal: solo de noche (Argentina) para no afectar sesiones activas.
> Usar `python faro_admin.py` para verificar si hay sesiones activas antes de tocar algo.

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
