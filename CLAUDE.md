# FARO PROTOCOL — Guía para Claude Code
> Última actualización: Abril 2026
> Arquitecto del sistema: Emilio (hijo)
> Conocimiento de campo y contactos: Padre (fundador)

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

## Roadmap priorizado

### 🔴 Fase 1 — Bugs críticos (hacer antes que cualquier otra cosa)
- [ ] Corregir bug SAR_ZIP en faro_fusion.py
- [ ] Corregir doble colorbar en faro_fusion.py
- [ ] Corregir SAR negro (no normalizar en georef.py)
- [ ] Envolver faro_sar_georef.py en main() para poder importarlo
- [ ] Crear .gitignore y mover faro_accesos.txt a .env

### 🟠 Fase 2 — Hacer el sistema genérico
- [ ] Crear faro_areas/ con sistema de configuración JSON por zona
- [ ] Refactorizar faro_sar_pipeline.py para recibir área como parámetro
- [ ] Refactorizar faro_sar_georef.py para recibir bounds como parámetro
- [ ] Refactorizar faro_fusion.py para recibir paths y zona como parámetro
- [ ] Crear faro_pipeline.py unificado con --area argumento

### 🟡 Fase 3 — MachinaOS (agentes)
- [ ] paperclip_agent.py — verificar que pipeline corrió y reportar
- [ ] hermes_agent.py — validar calidad de outputs (SAR no negro, NDVI válido)
- [ ] phone_gateway.py — alertas WhatsApp para decisiones críticas

### 🟢 Fase 4 — Web y datos reales
- [ ] Reemplazar datos hardcodeados del website por JSON dinámico del pipeline
- [ ] Reemplazar "As featured in" por referencias científicas verificables
- [ ] Mejorar seguridad del portal de clientes

### 🔵 Fase 5 — Calibración agro
- [ ] Importar 12.000 datos de rinde de Basso
- [ ] Modelo de predicción: NDVI + SAR + historial → rinde estimado
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
