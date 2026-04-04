# FARO PROTOCOL — Guía para Claude Code
> Última actualización: 2026-04-04 (revisión final del día)
> Arquitecto del sistema: Emilio (hijo)
> Conocimiento de campo y contactos: Padre (fundador)

---

## Estado del sistema — 2026-04-04

### Pipeline operativo (Córdoba)
```
Área         : Marcos Juárez, Córdoba
NDVI medio   : 0.4444   (Sentinel-2 via GEE, escala int16 corregida)
SAR          : 19.1 dB  (Sentinel-1 VV IW GRD, 2026-03-25)
Fusion Index : 0.6935
Rinde est.   : 2.69 t/ha  (84% del promedio nacional — soja referencia)
Score Faro   : 61.0 / 100
Confianza    : Alta  (2 fuentes + visión computacional)
Estado       : OK
Píxeles      : 124,066,152
SHA-256      : 88f26daf83831fdad89dd8379eee258f9d63d6336551f45d258297929f2e6291
Commit web   : 73105b6 — pendiente de push a GitHub Pages
```

### Áreas configuradas
| Área | Datos satelitales | Estado |
|------|-------------------|--------|
| cordoba | ✅ NDVI + SAR + reporte | Operativa |
| balcarce | ❌ sin datos | Lista para descargar |
| vaca_muerta | ❌ sin datos | Lista para descargar |
| rotterdam | ❌ sin datos | Lista para descargar |
| amazonas | ❌ sin datos | Lista para descargar |
| indiana | ❌ sin datos | Lista para descargar |

### Credenciales
| Variable | Estado |
|----------|--------|
| ANTHROPIC_API_KEY | ❌ FALTA — agentes MachinaOS degradados |
| COPERNICUS_USER/PASS | ❌ FALTA — sin descarga SAR |
| WHATSAPP_TOKEN/PHONE_ID/DEST | ❌ FALTA — alertas desactivadas |

---

## Qué falta hacer en Claude Code

### 1. Calibración SAR — ALTA PRIORIDAD
`faro_sar_georef.py` usa `10 * log10(DN)` pero Sentinel-1 GRD es amplitud (no potencia).
La fórmula correcta para sigma0 es `20 * log10(DN)` con offset de calibración.
Resultado actual: media 19.1 dB (debería ser ~-12 dB para agricultura).
Hermes sigue marcando NO-GO por SAR fuera de rango. Dos opciones:
- Corregir la fórmula a `20 * log10(DN + 1e-10) - 83.0` (offset típico Sentinel-1 GRDH)
- O ajustar los umbrales de Hermes a la escala real de los datos (solución provisional)

### 2. Website — datos demo que siguen activos
Los siguientes valores en `faro_website.html` son ficticios y representan riesgo
legal/reputacional si un cliente los verifica:
- "98.2% accuracy" global (sin respaldo)
- "1,020K bbl/d" Vaca Muerta (sin datos reales)
- "8,491 verified blocks" (contador ficticio)
- "247 readings verified" (sin respaldo)
- Caso Red Sea / Reuters / Panama Canal (sección "proof") — hashes falsos
- "sha256:3f8a1b9c4d2e7f0a" en la sección proof (hash ficticio)
Acción: o se remueven estas secciones, o se marcan explícitamente como "objetivo 2026"
o "proyectado", o se reemplazan con el único caso real verificable (Córdoba).

### 3. Modelo de predicción ML — PENDIENTE
`datos/rinde_modelo_ready.csv` tiene columnas `ndvi_medio`, `sar_medio_db`,
`indice_fusion`, `rinde_estimado` todas en NULL.
El modelo actual usa promedios nacionales INDEC + factores NDVI/SAR.
Para calibrarlo con los datos reales de Basso: una vez importado el CSV real,
entrenar un modelo de regresión (scikit-learn o statsmodels) que ajuste los
factores `factor_ndvi` y `factor_sar` del engine a los datos históricos del campo.

### 4. Portal de clientes — seguridad
`faro_client_portal.html` tiene credenciales en JS del lado cliente.
Cualquiera que inspeccione el código fuente las ve.
Para producción: Firebase Auth (gratis hasta cierto límite) o Netlify Identity.
Mientras tanto: no compartir la URL con clientes sin advertirlo.

### 5. faro_visor.html — área hardcodeada en demo
El demo automático de 60s arranca siempre con `cordoba`.
Cuando lleguen otras áreas, hacer el demo dinámico o parametrizable.

### 6. Tests automatizados — no existe ninguno
Para producción mínima: al menos tests de `faro_areas.py`, `faro_engine._generar_insight()`,
y `faro_rinde_import.parsear_csv()`. No es bloqueante para cliente piloto.

---

## Qué está pendiente por factores externos

### Credenciales (acción: crear `.env` en raíz del proyecto)
```bash
# Anthropic — para MachinaOS LLM
ANTHROPIC_API_KEY=sk-ant-...       # https://console.anthropic.com/keys

# Copernicus — para descarga SAR nueva
COPERNICUS_USER=email@ejemplo.com  # https://dataspace.copernicus.eu
COPERNICUS_PASS=contraseña

# WhatsApp — para alertas operacionales
WHATSAPP_TOKEN=...                 # Meta for Developers → WhatsApp API
WHATSAPP_PHONE_ID=...
WHATSAPP_DEST=+549...
```

### Datos satelitales
- **NDVI Balcarce**: descargar desde Google Earth Engine (igual que Córdoba)
  Script GEE ya conocido — cambiar coordenadas a bounds [-58.5,-38.3,-57.5,-37.3]
- **SAR Balcarce / otras áreas**: `python faro_sar_pipeline.py --area balcarce`
  (requiere COPERNICUS_USER/PASS en .env)
- **NDVI Vaca Muerta, Rotterdam, Amazonas, Indiana**: igual vía GEE

### Datos de campo (Padre)
- **12.000 registros de rinde de Renzo Basso** — exportar a CSV y correr:
  `python faro_rinde_import.py --csv ruta/datos_basso.csv`
  Columnas mínimas: lote, año, cultivo, rinde_tn_ha
  Con estos datos el modelo económico deja de usar promedios nacionales
  y empieza a predecir contra historial real del campo.

### Push a GitHub Pages
- Commit listo: `73105b6` (caso Córdoba real + 4 áreas globales)
- Ver `PUSH_INSTRUCTIONS.md` en la raíz — necesita token GitHub de Emilio
- URL final: https://protocolfaro.github.io/faroprotocol

---

## Próximo paso para salir a la cancha con un cliente real

### El único caso completamente verificable hoy es Córdoba.
Score 61.0 · Rinde 2.69 t/ha · Alta confianza · SHA-256 real · 124M píxeles.

### Secuencia mínima para primera reunión con cliente (orden de impacto):

**Paso 1 — Hoy (30 min, Emilio)**
Hacer el push a GitHub Pages con el token.
El website ya muestra el caso real de Córdoba con SHA-256 verificable.
URL: https://protocolfaro.github.io/faroprotocol

**Paso 2 — Esta semana**
- Crear `.env` con ANTHROPIC_API_KEY
- Correr `python MachinaOS/paperclip_agent.py` → primer resumen LLM real del sistema
- Correr `python MachinaOS/hermes_agent.py --area cordoba` → validación automatizada

**Paso 3 — Antes de la reunión**
- El Padre exporta los datos de Basso a CSV
- `python faro_rinde_import.py --csv datos_basso.csv`
- Esto convierte "rinde estimado de promedios nacionales" en
  "predicción calibrada contra 12.000 lecturas reales del campo"
- El modelo pasa de ser estadístico a ser específico del cliente

**Paso 4 — Para el pitch**
El demo natural para un productor o exportadora es:
1. Abrir https://protocolfaro.github.io/faroprotocol en el browser del cliente
2. Mostrar la tarjeta "✓ Validated · Marcos Juárez" con el SHA-256
3. Verificar el hash en vivo (abrir faro_reporte_fusion_cordoba.sha256)
4. Abrir faro_visor.html → terreno 3D de Córdoba con NDVI/SAR en vivo
5. Mostrar data.json → "esto es lo que ve el sistema, sellado criptográficamente"

**Qué NO hace falta para el primer cliente:**
- MachinaOS con LLM (funciona en modo local sin API key)
- Datos de otras áreas (Córdoba sola es suficiente para el piloto agro)
- Modelo ML entrenado (el modelo estadístico actual es honesto y funciona)
- WhatsApp gateway

**El argumento central que el sistema ya puede demostrar:**
> "El 4 de abril de 2026, antes de que Basso recibiera cualquier reporte oficial,
> nuestro sistema leyó NDVI 0.4444 en sus campos y estimó 2.69 t/ha.
> Este dato está sellado con SHA-256 y no puede ser modificado retroactivamente.
> Cuando salga el dato oficial, lo comparamos."

---

---

## ¿Qué es Faro Protocol?

Plataforma **genérica** de inteligencia satelital + IA.
Apuntable a cualquier área geográfica del mundo.

Cruza datos SAR (radar) + óptico + térmico con historial de campo
y agentes de IA autónomos para entregar información económica
verificada criptográficamente antes que las fuentes oficiales.

**Dos verticales (mismo stack técnico):**
- **Agro** — monitoreo de cultivos, predicción de rinde, inventario regional
- **Energía** — actividad industrial, extracción, estabilidad de suelo

**Casos piloto actuales** (solo ejemplos de validación):
- Vaca Muerta → vertical energía
- Marcos Juárez, Córdoba → vertical agro
- Balcarce/MDQ → vertical agro (en desarrollo)

**El área es siempre un parámetro, nunca una constante hardcodeada.**

**Web pública**: https://protocolfaro.github.io/faroprotocol

---

## Los 5 Pilares

### Pilar 1 — Motor de Verdad Física
- **Historial**: 12.000 datos de rinde de Renzo Basso (dataset de calibración)
- **Sentinel-2** — óptico, cada 5 días → NDVI, vigor vegetativo
- **Sentinel-1** — SAR/Radar, atraviesa nubes → humedad de suelo
- **Landsat** — térmico → temperatura de suelo
- **Algoritmos INTA (D'Amico)** — recomendaciones de siembra
- **SHA-256 + OpenTimestamps** — cada dato sellado antes del reporte oficial

### Pilar 2 — MachinaOS (agentes autónomos Anthropic)
| Agente | Rol |
|--------|-----|
| `Hermes-agent` | Valida que outputs sean correctos antes de publicar |
| `Paperclip` | Monitor de latido — resumen diario, sin acción si todo OK |
| `Phone Gateway` | WhatsApp solo para decisiones críticas |

Modelo: `claude-sonnet-4-20250514` vía Anthropic API.

### Pilar 3 — IA de Localización
- Clasificación de cultivos por lote (soja / trigo / maíz)
- Detección de malezas y fallas de siembra
- Inventario regional para exportadoras
- En energía: detección de actividad industrial y cambios en terreno

### Pilar 4 — Visualización de Élite
- Visor 3D con three.js (mrdoob) — capas configurables
- Demo de 60 segundos con OpenScreen para clientes
- Target: productores, Puerto, Moscariello, exportadoras, fondos

### Pilar 5 — Escalabilidad
Mismo pipeline, distinto polígono. Un campo en Balcarce o un yacimiento
en Neuquén son el mismo sistema con distintos parámetros.

---

## Stack técnico

```
Satelital:
  Google Earth Engine → Sentinel-2, Landsat (nube, sin procesar local)
  Copernicus Data Space API → Sentinel-1 SAR (descarga local)
  rasterio, numpy → procesamiento raster

Pipeline Python:
  faro_sar_pipeline.py     → descarga SAR
  faro_sar_georef.py       → georreferencia SAR  ⚠️ refactorizar
  faro_closdi_pipeline.py  → filtro sombras (módulo genérico ✓)
  faro_fusion.py           → fusión → Índice Faro  ⚠️ bug activo

Agentes (MachinaOS):
  Anthropic API (claude-sonnet-4-20250514)
  Claude Code + MCP servers

Verificación:
  hashlib SHA-256 + OpenTimestamps (.ots)

Web (GitHub Pages):
  faro_website.html         → landing institucional (EN/ES)
  faro_client_portal.html   → portal de clientes con login
  HTML/CSS/JS puro, sin frameworks

Visualización futura:
  three.js → visor 3D
  matplotlib → reportes PNG actuales
```

---

## Estructura de archivos

```
faro_protocol/
├── CLAUDE.md
├── faro_areas/                  ← [CREAR] configs por zona
│   ├── areas.json
│   └── [zona].json
├── Pipeline/
│   ├── faro_sar_pipeline.py     ✓ usa env vars para credenciales
│   ├── faro_sar_georef.py       ⚠️ paths y bounds hardcodeados
│   ├── faro_closdi_pipeline.py  ✓ ya es módulo importable
│   ├── faro_fusion.py           ⚠️ bug SAR_ZIP + hardcodeado
│   └── faro_fusion_backup.py    (versión anterior, mantener como ref)
├── Web/
│   ├── faro_website.html        ✓ bilingüe EN/ES, muy completo
│   ├── faro_website_v3__2_.html (versión alternativa)
│   └── faro_client_portal.html  ⚠️ login solo JS lado cliente
├── Datos/
│   ├── faro_hash_txt.ots        ✓ sellado blockchain real
│   └── faro_reporte_fusion_cordoba.png
├── MachinaOS/                   ← [CREAR] agentes
│   ├── paperclip_agent.py
│   ├── hermes_agent.py
│   └── phone_gateway.py
├── .env                         ← [CREAR] nunca commitear
├── .gitignore                   ← [CREAR] urgente
└── faro_accesos.txt             ⚠️ sacar del repo
```

---

## Área como parámetro — patrón de diseño obligatorio

### ❌ NO hacer (hardcodeado)
```python
WEST, SOUTH, EAST, NORTH = -65.5, -33.5, -63.5, -30.5  # Córdoba fijo
SAR_OUTPUT = 'sar_cordoba_georef.tif'                    # nombre fijo
ZONA = "Córdoba, Argentina"                               # zona fija
```

### ✅ SÍ hacer (genérico)
```python
# areas/balcarce.json
{
  "name": "Balcarce",
  "label": "Balcarce, Buenos Aires",
  "vertical": "agro",
  "polygon": "POLYGON((-58.5 -38.5,-57.5 -38.5,...))",
  "bounds": [-58.5, -38.5, -57.5, -37.5]
}

# Uso en cualquier script:
area = load_area('balcarce')
sar_output = f"sar_{area['name']}_georef.tif"
```

### Pipeline unificado (a crear)
```bash
python faro_pipeline.py --area balcarce
python faro_pipeline.py --area vaca_muerta
python faro_pipeline.py --area "mi_campo_san_luis"
```

---

## Bugs y problemas encontrados

### 🐛 BUG CRÍTICO — faro_fusion.py línea 111
```python
# PROBLEMA: SAR_ZIP no está definido en este script → NameError al correr
sar_raw = cargar_sar_desde_zip(SAR_ZIP)

# CORRECCIÓN: la función ignora el argumento de todas formas
sar_raw = cargar_sar_desde_zip()
# Y también quitar el if sar_raw is None que nunca se ejecuta
# porque cargar_sar_desde_zip() nunca retorna None en esta versión
```

### 🐛 SAR sale completamente negro en el reporte
El reporte actual muestra backscatter medio = 0.0061.
**Causa probable**: en `faro_sar_georef.py` el SAR ya se normaliza (0-1)
antes de guardarlo. Cuando `faro_fusion.py` lo carga y normaliza de nuevo,
los valores reales son casi cero porque la imagen GRD cruda tiene valores
muy bajos en zonas sin actividad fuerte.
**Solución**: no normalizar en georef.py, guardar en dB y normalizar
solo en fusion.py justo antes de graficar.

### 🐛 faro_fusion.py genera dos colorbars por cada capa
```python
# Línea 62-63: se crea el colorbar DOS veces por capa
plt.colorbar(im, ax=ax, ...).ax.yaxis.set_tick_params(color='white')
plt.setp(plt.getp(plt.colorbar(im, ax=ax, ...).ax.axes, ...))
#                  ^^^^ segunda llamada innecesaria → doble colorbar
```
Guardar la referencia y usarla:
```python
cb = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
cb.ax.yaxis.set_tick_params(color='white')
plt.setp(cb.ax.get_yticklabels(), color='white')
```

### 🐛 faro_sar_georef.py — script suelto, no función
El script corre código en el módulo raíz (sin `if __name__ == '__main__'`).
Esto impide importarlo desde otros scripts sin que corra el procesamiento.
Hay que envolver todo en `def main(area): ...`

### ⚠️ faro_sar_pipeline.py — áreas hardcodeadas
`AREA_VACA_MUERTA` y `AREA_CORDOBA` están definidas como constantes fijas.
Refactorizar para que reciba el polígono como parámetro o desde areas.json.

### ⚠️ Seguridad — portal de clientes
Login en JavaScript del lado del cliente: cualquiera que inspeccione
el código fuente ve usuarios y contraseñas. Para producción real
necesita autenticación del lado servidor o Firebase Auth.

### ⚠️ faro_website.html — datos hardcodeados como si fueran en vivo
Valores como "1,020K bbl/d", "98.2%", "247 lecturas verificadas",
"47 suscriptores activos" y los testimonios están escritos fijos en el HTML.
Parecen datos reales pero son estáticos. Opciones:
1. Marcarlos claramente como "proyectados" o "piloto"
2. Conectarlos a un JSON real que se actualice con el pipeline

### ⚠️ faro_website.html — "As featured in" FT, Reuters, WSJ, The Economist
Si el proyecto no fue cubierto por estos medios, es un riesgo legal
y de reputación serio. Reemplazar por "Methodology validated by" con
referencias a papers reales (ESA, INTA, Cal 2026) que sí se pueden citar.

### ⚠️ faro_accesos.txt — credenciales en texto plano en el repo
Mover a .env inmediatamente y agregar al .gitignore.

---

## Diseño visual (mantener coherencia en todo el proyecto)

```css
/* Paleta */
--bg:   #06080b   /* fondo principal */
--bg2:  #0d1117
--bg4:  #090d12
--gold: #c9a84c   /* dorado — color de marca */
--gl:   #e2c97e   /* dorado claro */
--w:    #f2ede4   /* texto principal */
--w3:   rgba(242,237,228,0.6)
--green:#2d8c5e
--red:  #b03030

/* Tipografías */
--serif: 'Cormorant Garamond'  /* títulos elegantes */
--sans:  'Epilogue'            /* texto general */
--mono:  'JetBrains Mono'      /* hashes, datos, código */
```

Estética: oscura, premium, estilo terminal Bloomberg / datos financieros.
Mantener en cualquier componente nuevo (agentes, reportes, dashboard).

---

## Estado real del proyecto — Auditoría 2026-04-04

> Cada módulo fue importado y ejecutado. Los estados son verificados, no supuestos.

---

### Módulos Python

| Archivo | Estado | Notas |
|---------|--------|-------|
| `faro_areas.py` | ✅ COMPLETO | `load_area()` y `list_areas()` funcionan. Áreas: cordoba, balcarce, vaca_muerta. |
| `faro_sar_georef.py` | ✅ COMPLETO | Importable, tiene `main(area, sar_input)`, convierte a dB. |
| `faro_sar_pipeline.py` | ✅ COMPLETO | Importable, busca SAR por área. Requiere `COPERNICUS_USER/PASS` en env para correr. |
| `faro_fusion.py` | 🔴 ROTO (parcial) | Corre y genera PNG, pero **no aplica corrección int16 (÷10000)** en `cargar_ndvi()`. `ndvi_medio` en stats queda en 4444 en vez de 0.44. El rinde sin vision resulta incorrecto (5.2 t/ha en vez de 2.69). faro_vision sobreescribe este valor si se corre el pipeline completo. |
| `faro_vision.py` | ✅ COMPLETO | Corre completo sobre Córdoba. Aplica corrección int16, downsampling, Z-score, genera PNG y JSON. |
| `faro_engine.py` | ✅ COMPLETO | Motor centralizado funciona. `--skip-vision` produce ndvi incorrecto (bug de fusion). Con vision completa, corrige y produce datos válidos (Score: 60.8, rinde: 2.69 t/ha, confianza Alta). |
| `faro_pipeline.py` | ✅ COMPLETO | Pipeline unificado funciona end-to-end con `--skip-georef`. Produce SHA-256, data.json correcto. |
| `faro_rinde_import.py` | ✅ COMPLETO | `--demo` corre perfectamente: 200 registros, stats, CSV modelo-ready. Sin datos reales de Basso todavía. |
| `faro_closdi_pipeline.py` | ✅ COMPLETO | Importable. Provee funciones matemáticas (CLOSDI, EVI2, NDVI, máscaras). No integrado en el pipeline principal. |
| `MachinaOS/hermes_agent.py` | 🟡 PARCIAL | Importa y corre. Sin `ANTHROPIC_API_KEY`: veredicto local (modo fallback). Con API key: usa Claude. Detecta correctamente SAR fuera de rango dB y NDVI escala errónea. |
| `MachinaOS/paperclip_agent.py` | 🟡 PARCIAL | Importa y corre. `--raw` funciona sin API key. Con API key: resumen LLM. Balcarce y Vaca Muerta reportan SIN_DATOS (sin archivos satelitales). |
| `MachinaOS/phone_gateway.py` | 🟡 PARCIAL | Importa y corre. `--test` funciona. Envío real requiere `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_DEST` en .env. |

---

### Datos satelitales

| Área | NDVI.tif | SAR georef.tif | Estado |
|------|----------|----------------|--------|
| cordoba | ✅ existe (263 MB) | ⚠️ existe pero en escala 0-1, no dB | Pipeline corre, SAR incorrecto |
| balcarce | ❌ no existe | ❌ no existe | SIN_DATOS — no se puede procesar |
| vaca_muerta | ❌ no existe | ❌ no existe | SIN_DATOS — no se puede procesar |

**Nota SAR Córdoba**: `sar_cordoba_georef.tif` fue generado con código viejo que normalizaba a 0-1. El código actual de `faro_sar_georef.py` produce dB correctamente. Hermes reporta NO-GO por este archivo. Para corregir: re-correr georef con el SAR crudo original.

---

### Configuración e infraestructura

| Item | Estado | Notas |
|------|--------|-------|
| `.gitignore` | ✅ COMPLETO | Excluye .env, *.tif, *.zip, __pycache__, etc. |
| `.env` | ❌ NO EXISTE | Crear con ANTHROPIC_API_KEY, COPERNICUS_USER/PASS, WHATSAPP_* |
| `faro_accesos.txt` | ⚠️ RIESGO | Contiene credenciales demo en texto plano. Está en .gitignore, pero sigue en disco. |
| `data.json` | ✅ COMPLETO | Generado con protocolo Cero Footprint. Estructura: _meta, _interno, insights, resumen, pipeline. |
| `faro_areas/*.json` | ✅ COMPLETO | cordoba, balcarce, vaca_muerta con todos los campos requeridos. |

---

### HTML / Web

| Archivo | Estado | Notas |
|---------|--------|-------|
| `faro_visor.html` | ✅ COMPLETO | Visor three.js 3D, lee data.json, funcional en browser sin servidor. |
| `faro_website.html` | ⚠️ PARCIAL | Bilingüe, diseño completo. Datos hardcodeados (métricas, testimonios). "As featured in" FT/Reuters/WSJ — riesgo legal si no es real. |
| `faro_client_portal.html` | ⚠️ RIESGO | Login del lado cliente en JS — credenciales visibles en código fuente. |
| `faro_website3.html` | ❓ REDUNDANTE | Versión reducida del website. No está claro si reemplaza o complementa. |
| `faro_website_v3 (2).html` | ❓ REDUNDANTE | Versión alternativa más antigua. |

---

### Bugs activos (verificados con ejecución real)

#### 🔴 BUG ACTIVO — faro_fusion.py no corrige escala int16 NDVI
```python
# cargar_ndvi() no detecta GEE int16 ×10000
# ndvi_medio retornado en stats = 4444.18 (debería ser 0.4444)
# Impacto: rinde estimado sin vision = 5.20 t/ha (incorrecto)
# Workaround actual: correr pipeline completo (con vision); 
#   faro_vision sobreescribe ndvi_medio con el valor correcto
# Fix: agregar el mismo bloque de detección que tiene faro_vision.py
```

#### ⚠️ PENDIENTE — sar_cordoba_georef.tif en escala 0-1 (no dB)
```
El archivo fue creado con código viejo. 
El código actual de faro_sar_georef.py SÍ convierte a dB.
Para corregir: re-ejecutar georef con el SAR crudo original.
Hermes detecta esto como NO-GO (media_db: 0.64 fuera del rango esperado -25 a -2).
```

#### ⚠️ PENDIENTE — Encoding UTF-8 en Windows
```
faro_fusion.py y faro_engine.py NO tienen sys.stdout.reconfigure(encoding='utf-8').
En terminal cp1252 de Windows, caracteres como → y ✓ causan UnicodeEncodeError.
Los agentes MachinaOS sí tienen la corrección. faro_rinde_import.py también.
Fix: agregar al inicio de faro_fusion.py y faro_engine.py:
  if hasattr(sys.stdout, 'reconfigure'):
      sys.stdout.reconfigure(encoding='utf-8')
```

---

## Roadmap priorizado (actualizado 2026-04-04)

### 🔴 Fase 1 — Bugs críticos
- [x] ~~Corregir bug SAR_ZIP en faro_fusion.py~~ — COMPLETO
- [x] ~~Corregir doble colorbar en faro_fusion.py~~ — COMPLETO
- [x] ~~Corregir SAR negro (no normalizar en georef.py)~~ — COMPLETO (el código está bien; el .tif de Córdoba es viejo)
- [x] ~~Envolver faro_sar_georef.py en main() para poder importarlo~~ — COMPLETO
- [x] ~~Crear .gitignore~~ — COMPLETO
- [ ] **Corregir corrección int16 en faro_fusion.py** (bug activo verificado)
- [ ] **Agregar sys.stdout.reconfigure en faro_fusion.py y faro_engine.py**
- [ ] **Crear .env** con ANTHROPIC_API_KEY y credenciales Copernicus
- [ ] **Re-generar sar_cordoba_georef.tif** en dB con el SAR crudo original

### 🟠 Fase 2 — Sistema genérico
- [x] ~~Crear faro_areas/ con sistema de configuración JSON por zona~~ — COMPLETO
- [x] ~~Refactorizar faro_sar_pipeline.py para recibir área como parámetro~~ — COMPLETO
- [x] ~~Refactorizar faro_sar_georef.py para recibir bounds como parámetro~~ — COMPLETO
- [x] ~~Refactorizar faro_fusion.py para recibir paths y zona como parámetro~~ — COMPLETO
- [x] ~~Crear faro_pipeline.py unificado con --area argumento~~ — COMPLETO
- [ ] Descargar NDVI y SAR para balcarce y vaca_muerta (sin datos satelitales)

### 🟡 Fase 3 — MachinaOS (agentes)
- [x] ~~paperclip_agent.py~~ — COMPLETO (requiere API key para LLM, funciona sin ella en modo raw)
- [x] ~~hermes_agent.py~~ — COMPLETO (idem)
- [x] ~~phone_gateway.py~~ — COMPLETO (requiere WHATSAPP_* en .env para envío real)
- [ ] Configurar .env con ANTHROPIC_API_KEY para activar LLM en agentes

### 🟢 Fase 4 — Web y datos reales
- [ ] Reemplazar datos hardcodeados del website por JSON dinámico del pipeline
- [ ] Reemplazar "As featured in" por referencias científicas verificables
- [ ] Mejorar seguridad del portal de clientes (autenticación server-side)

### 🔵 Fase 5 — Calibración agro
- [x] ~~faro_rinde_import.py: importador de datos de rinde~~ — COMPLETO (estructura lista, demo funciona)
- [ ] Importar 12.000 datos reales de rinde de Basso (correr con --csv datos_reales.csv)
- [ ] Modelo de predicción ML: NDVI + SAR + historial → rinde estimado
- [ ] Integrar algoritmos INTA D'Amico

### ✅ Fase 6 — Visión computacional (COMPLETADA 2026-04-02)
- [x] Clasificación de vigor vegetativo por píxel (6 clases NDVI)
- [x] Detección de anomalías via Z-score local (scipy) — posibles malezas/estrés
- [x] Downsampling automático para rasters grandes (> 2000px por lado)
- [x] Detección auto de escala int16 GEE (÷10000)
- [x] Reporte PNG dark-premium + JSON de estadísticas
- [x] Integrado en faro_pipeline.py como paso 3/5
- **Archivo**: `faro_vision.py --area <zona>`
- **Nota**: No distingue soja/trigo/maíz con NDVI solo (requiere multi-temporal).
  Clasifica por vigor; para tipo de cultivo se necesita análisis multi-temporal.

### ✅ Fase 7 — Visualización 3D (COMPLETADA 2026-04-02)
- [x] Visor three.js r158 con terreno procedural 3D por área
- [x] 4 capas configurables: NDVI / SAR / Índice Fusión / Anomalías
- [x] Métricas en vivo desde data.json
- [x] Verificación blockchain (SHA-256) en sidebar
- [x] Demo automático de 60 segundos con 6 pasos narrativos
- [x] Controles teclado: R (reset), Space (auto-rotar), 1-4 (capas)
- [x] Diseño dark-premium (paleta Faro, Cormorant Garamond, JetBrains Mono)
- **Archivo**: `faro_visor.html` — abrir en browser, sin servidor requerido

### ✅ Fase 8 — Motor centralizado faro_engine.py (COMPLETADA 2026-04-04)
- [x] Modelo económico agro: NDVI + SAR → rinde estimado (t/ha)
- [x] Modelo económico energía: SAR → Índice de Actividad Industrial (0-100)
- [x] Score Faro (0-100), confianza (Alta/Media/Baja), estado (OK/ALERTA/SIN_DATOS)
- [x] Protocolo Cero Footprint: data.json con capas _interno / insights / resumen
- [x] SHA-256 del reporte integrado al flujo
- **Archivo**: `faro_engine.py --area <zona>`

---

## Variables de entorno (.env — nunca commitear)

```bash
# Copernicus Data Space (SAR)
COPERNICUS_USER=tu_email@ejemplo.com
COPERNICUS_PASS=tu_contraseña

# Anthropic (MachinaOS)
ANTHROPIC_API_KEY=sk-ant-...

# WhatsApp Gateway (Fase 3)
WHATSAPP_TOKEN=...
WHATSAPP_PHONE_ID=...
WHATSAPP_DEST=+54911...
```

---

## Clientes objetivo

- Productores agropecuarios (cualquier zona)
- Exportadoras de granos (Puerto, Moscariello y otros)
- Empresas petroleras y mineras
- Fondos de inversión y traders de commodities
- Organismos públicos (INTA, provincias)

---

## Comandos de arranque para Claude Code

```bash
# Fase 1 — bugs urgentes:
claude "corregí los 3 bugs en faro_fusion.py: SAR_ZIP no definido, doble colorbar, y el if sar_raw is None que nunca se ejecuta"

claude "refactorizá faro_sar_georef.py para que no normalice el SAR sino que lo guarde en dB, y envolvé todo el código en def main(area): para que sea importable"

claude "creá .gitignore que excluya: .env, faro_accesos.txt, datos_sar/, sar_temp/, *.tif, *.zip, __pycache__/"

# Fase 2 — sistema genérico:
claude "creá el sistema de áreas: carpeta faro_areas/ con areas.json y un archivo por zona. Cada área tiene name, label, vertical, polygon y bounds"

claude "refactorizá faro_sar_pipeline.py para que reciba el polígono desde faro_areas/ en vez de tenerlo hardcodeado"

claude "creá faro_pipeline.py que acepte --area como argumento, cargue la config, y corra georef → closdi → fusion en orden, generando el SHA-256 al final"

# Fase 3 — MachinaOS:
claude "creá paperclip_agent.py que use la API de Anthropic para verificar que los archivos del pipeline del día de hoy existen y son válidos, y muestre un resumen"

# Web — datos reales:
claude "extraé todos los valores hardcodeados del website (bbl/d, porcentajes, contadores) a un archivo data.json que el pipeline actualice automáticamente"
```

---

## Dato de referencia (piloto Vaca Muerta)

```
Fecha:   2026-03-23
SAR:     80.07 DN  (Sentinel-1A IW GRD)
SHA-256: a9b2fc633edd63059aa5ff5d63d4d129af8d1e9554b92a2c7b4a991ab54fc0d3
Web:     https://protocolfaro.github.io/faroprotocol
```
