# Panel Roger — Protocolo de Validación

## Source of Truth — Regla absoluta

```
EDITAR AQUÍ:   Faro-index/velez/admin-roger/index.html   ← ÚNICA fuente canónica
                        ↓  (sync-paneles.yml, automático en cada push a main)
DESPLEGADO EN: github.com/protocolfaro/faro-paneles/velez/admin-roger/index.html
                        ↓  (GitHub Pages)
URL PRODUCCIÓN: https://protocolfaro.github.io/faro-paneles/velez/admin-roger/
```

**NUNCA editar directamente en `faro-paneles-git`.** El sync hace `git push --force`
y pisaría cualquier cambio hecho allí con lo que haya en Faro-index.

---

## Archivos HTML — Mapa completo

| Ruta en Faro-index | URL en producción |
|---|---|
| `velez/index.html` | `/faro-paneles/velez/` → redirige a `admin-roger/` |
| `velez/admin-roger/index.html` | `/faro-paneles/velez/admin-roger/` ← panel principal Roger |
| `velez/alerts-config/index.html` | `/faro-paneles/velez/alerts-config/` |
| `velez/health-dashboard/index.html` | `/faro-paneles/velez/health-dashboard/` |
| `velez/verify/index.html` | `/faro-paneles/velez/verify/` |

---

## Protocolo de escritura — 4 pasos obligatorios

### Paso 1 — Verificar ruta ANTES de escribir
```
¿El archivo está en Faro-index/velez/?  → OK, editar
¿El archivo está en faro-paneles-git/?  → STOP. Editar en Faro-index.
¿El archivo está en protocolfaro.github.io/faro-paneles/?  → STOP. Editar en Faro-index.
```

### Paso 2 — Verificar POST-WRITE (obligatorio antes de commit)
```bash
wc -l velez/admin-roger/index.html   # debe ser ≥ 752 líneas
git diff --stat velez/                # mostrar exactamente qué cambió
```

### Paso 3 — Commit + push en Faro-index
```bash
cd /c/Users/Usuario/Desktop/Faro-index
git add velez/<archivo>
git commit -m "panel roger: <descripción del cambio>"
git push origin main
```

### Paso 4 — Verificar sync completó en GitHub Actions
```
https://github.com/protocolfaro/faroprotocol/actions
→ Buscar "Sync paneles → faro-paneles" → debe terminar en SUCCESS (≈30 segundos)
→ Después: https://protocolfaro.github.io/faro-paneles/velez/admin-roger/
→ Hard refresh (Ctrl+Shift+R) y verificar visualmente
```

---

## Verificación visual — checklist mínimo

Antes de reportar "done" en cualquier cambio al panel Roger:

- [ ] Header dorado visible con logo Faro Protocol
- [ ] Campo de fútbol SVG con heatmap overlay renderizado
- [ ] Grid 3×3 SAR cards visibles (o mensajes de datos pendientes)
- [ ] Tabs de navegación funcionales (campo, clima, SAR, solar, etc.)
- [ ] No hay errores en DevTools Console (F12)
- [ ] Datos reales cargados (no "N/D" en todos los campos)

---

## Estado del stash en faro-paneles-git — NO aplicar sin revisión

```
stash@{0}: WIP on main: 5077e7b
Feature: "fecha imagen satelital visible + aviso antigüedad + contexto invernal NDVI"
Estado: DIVERGENTE — basado en versión 867 líneas (pre-commits 84fec4d..8b95420)
Riesgo: conflictos masivos si se popea sobre HEAD actual (752 líneas)
Decisión: requiere re-implementación manual sobre la base actual, no pop automático
```

Para rescatar la feature del stash en el futuro:
```bash
git -C /c/Users/Usuario/Desktop/faro-paneles-git stash show -p stash@{0} > /tmp/stash_feature.diff
# Revisar el diff, identificar solo las líneas de "fecha imagen satelital"
# Aplicar manualmente en Faro-index/velez/admin-roger/index.html
```

---

## Causa raíz del incidente (2026-07-10)

- 12 commits de panel Roger se hicieron en `faro-paneles-git` y se pushearon directo al repo secundario
- `Faro-index/velez/admin-roger/index.html` quedó en 479 líneas (versión vieja)
- `faro-paneles/velez/admin-roger/index.html` tenía 752 líneas (versión nueva, correcta)
- El próximo sync hubiera pisado producción con la versión vieja (bomba de tiempo)
- Fix: copiar el HTML correcto a Faro-index y pushear → sync automático propagó la versión correcta

---

## Repos y sus roles

| Repo local | Remote | Rol |
|---|---|---|
| `Desktop/Faro-index` | `protocolfaro/faroprotocol` | **SOURCE OF TRUTH** — editar aquí |
| `Desktop/faro-paneles-git` | `protocolfaro/faro-paneles` | Deploy target — no editar directo |
| `Desktop/protocolfaro.github.io` | `protocolfaro/protocolfaro.github.io` | Landing page — no confundir con paneles |
| `Desktop/faro-paneles` | ninguno (sin git) | Borrador obsoleto — ignorar |
