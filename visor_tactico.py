#!/usr/bin/env python3
"""
visor_tactico.py — Generador de visores tácticos Faro Protocol
Usa Leaflet.js + ESRI World Imagery (gratuito, sin token, sin límites).

Uso:
    python visor_tactico.py --area cauchari
    python visor_tactico.py --area vaca_muerta
    python visor_tactico.py --area pilbara
    python visor_tactico.py --area balcarce --output custom.html

Salida: outputs/visor_<area>.html
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT    = Path(__file__).parent
AREAS   = ROOT / 'faro_areas'
OUTPUTS = ROOT / 'outputs'
DATA    = ROOT / 'data.json'

# ── Configuración por vertical ────────────────────────────────────────────────
VERTICAL_META = {
    'agro':          {'label': 'Agricultura · Monitoreo de cultivos',  'color': '#86efac'},
    'deforestacion': {'label': 'Deforestación · Cobertura forestal',   'color': '#4ade80'},
    'energia':       {'label': 'Energía · O&G · Infraestructura',      'color': '#fb923c'},
    'oil_gas':       {'label': 'Oil & Gas · Upstream',                 'color': '#fb923c'},
    'maritimo':      {'label': 'Marítimo · Tráfico portuario',         'color': '#38bdf8'},
    'shipping':      {'label': 'Shipping · Rutas oceánicas',           'color': '#38bdf8'},
    'mineria':       {'label': 'Minería · Open-cut',                   'color': '#f59e0b'},
    'mining_iron':   {'label': 'Minería Hierro · Iron Ore Belt',       'color': '#f59e0b'},
    'tierras_raras': {'label': 'Tierras Raras · Li₂CO₃',              'color': '#22d3ee'},
}

# ── Áreas especiales con polígonos detallados ─────────────────────────────────
# polygons: lista de [ [[lat,lon],...], ... ] en formato Leaflet (lat, lon)
AREA_EXTRA: dict[str, dict] = {
    'cauchari': {
        'label':        'Cauchari-Olaroz · Jujuy, Argentina',
        'vertical':     'tierras_raras',
        'country_name': 'Argentina',
        'center':       [-66.7530, -24.0130],   # lon, lat
        'bounds':       [-66.85, -24.15, -66.65, -23.90],
        'zoom':         13,
        'nota':         'Proyecto litio Cauchari-Olaroz · Lithium Americas + JEMSE',
        'sar_ref':      'Sentinel-1A IW GRD',
        # Polígonos de piletas de evaporación (lat, lon para Leaflet)
        'polygons': [
            [[-23.990, -66.782], [-23.990, -66.748], [-23.965, -66.748], [-23.965, -66.782]],
            [[-23.990, -66.748], [-23.990, -66.715], [-23.965, -66.715], [-23.965, -66.748]],
            [[-24.028, -66.782], [-24.028, -66.715], [-23.990, -66.715], [-23.990, -66.782]],
            [[-24.065, -66.782], [-24.065, -66.715], [-24.028, -66.715], [-24.028, -66.782]],
            [[-24.110, -66.810], [-24.110, -66.720], [-24.065, -66.720], [-24.065, -66.810]],
        ],
    },
}

# ── Zoom por defecto según tamaño del área ────────────────────────────────────
def zoom_from_bounds(bounds: list[float] | None, default: int = 11) -> int:
    if not bounds:
        return default
    span = max(abs(bounds[2] - bounds[0]), abs(bounds[3] - bounds[1]))
    if span > 10: return 7
    if span > 5:  return 8
    if span > 2:  return 9
    if span > 1:  return 10
    if span > 0.5:return 11
    if span > 0.2:return 12
    return 13


def load_area_config(area_name: str) -> dict:
    if area_name in AREA_EXTRA:
        return AREA_EXTRA[area_name]
    p = AREAS / f'{area_name}.json'
    if not p.exists():
        sys.exit(f'[ERROR] Area no encontrada: {area_name}  (busqué en {p})')
    with open(p) as f:
        return json.load(f)


def load_system_data(area_name: str) -> dict:
    if not DATA.exists():
        return {}
    with open(DATA) as f:
        d = json.load(f)
    return {
        **d.get('_interno', {}).get(area_name, {}),
        **d.get('pipeline', {}).get(area_name, {}),
        **d.get('insights', {}).get(area_name, {}),
    }


def get_cert_sha256(area_name: str) -> str | None:
    for fname in [f'faro_cert_{area_name}.png', f'faro_reporte_fusion_{area_name}.png']:
        p = ROOT / fname
        if p.exists():
            with open(p, 'rb') as f:
                return hashlib.sha256(f.read()).hexdigest()
    return None


def center_from_config(cfg: dict) -> list[float]:
    """Retorna [lon, lat]."""
    if 'center' in cfg:
        c = cfg['center']
        # Algunos JSONs tienen [lat, lon] cuando lat < lon (ej: Pilbara center: [-22.5, 117.8])
        if abs(c[0]) <= 90 and abs(c[1]) > 90:
            return [c[1], c[0]]   # era [lat, lon] → convertir a [lon, lat]
        return list(c)
    if 'bounds' in cfg:
        b = cfg['bounds']
        return [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]
    return [0.0, 0.0]


def format_sha(sha: str) -> str:
    if len(sha) == 64:
        return sha[:32] + '<br>' + sha[32:]
    return sha


def bounds_to_leaflet_polygon(bounds: list[float]) -> list:
    """Convierte [lon_min, lat_min, lon_max, lat_max] a lista [[lat,lon],...] para Leaflet."""
    lon_min, lat_min, lon_max, lat_max = bounds
    return [
        [lat_min, lon_min], [lat_min, lon_max],
        [lat_max, lon_max], [lat_max, lon_min],
    ]


def generate_html(area_name: str, cfg: dict, data: dict, sha: str | None) -> str:
    vertical  = cfg.get('vertical', 'agro')
    vmeta     = VERTICAL_META.get(vertical, {'label': vertical, 'color': '#22d3ee'})
    accent    = vmeta['color']

    center_lonlat = center_from_config(cfg)
    lon, lat      = center_lonlat
    bounds        = cfg.get('bounds')
    zoom          = cfg.get('zoom', zoom_from_bounds(bounds))

    label     = cfg.get('label', area_name.replace('_', ' ').title())
    country   = cfg.get('country_name', '')
    sar_ref   = cfg.get('sar_ref', 'Sentinel-1 IW GRD')
    nota      = cfg.get('nota', 'Datos procesados con pipeline Faro Protocol.')

    score     = data.get('score_faro', None)
    ndvi      = data.get('ndvi_medio', None)
    sar_db    = data.get('sar_medio_db', None)
    actividad = data.get('indice_actividad', None)
    estado    = data.get('estado', None)
    ultimo    = data.get('ultimo_run', '—')
    sha_data  = data.get('sha256_completo', sha)

    score_str = f'{score:.1f}' if isinstance(score, (int, float)) else '—'
    ndvi_str  = f'{ndvi:.4f}' if isinstance(ndvi, (int, float)) else '—'
    sar_str   = f'{sar_db:.2f} dB' if isinstance(sar_db, (int, float)) else '—'
    act_str   = f'{actividad:.1f}' if isinstance(actividad, (int, float)) else '—'

    status_color = '#00ff41' if estado == 'OK' else ('#ff4444' if estado == 'ALERTA' else '#94a3b8')
    status_text  = f'✓ PIPELINE ACTIVE · {estado}' if estado else '· DATA INCOMING'

    sha_display = format_sha(sha_data) if sha_data else '— hash no disponible —'

    # Polígonos SAR — usa lista específica si existe, si no usa bounding box
    if 'polygons' in cfg:
        polygons_js = json.dumps(cfg['polygons'])
    elif bounds:
        polygons_js = json.dumps([bounds_to_leaflet_polygon(bounds)])
    else:
        d = 0.1
        polygons_js = json.dumps([[
            [lat - d, lon - d], [lat - d, lon + d],
            [lat + d, lon + d], [lat + d, lon - d],
        ]])

    lat_label  = f'{abs(lat):.4f}°{"S" if lat < 0 else "N"}'
    lon_label  = f'{abs(lon):.4f}°{"W" if lon < 0 else "E"}'
    area_upper = area_name.upper()
    country_up = country.upper()
    title_lbl  = label.split('·')[0].strip()

    html = f'''<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Faro Protocol · Visor Táctico · {label}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  :root {{
    --bg:     #0f172a;
    --accent: {accent};
    --green:  #00ff41;
    --gold:   #c9a84c;
    --mono:   'Courier New', monospace;
  }}
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{ width: 100%; height: 100%; overflow: hidden;
                background: var(--bg); color: #f1f5f9; font-family: var(--mono); }}

  /* ── Contenedor tilt (perspectiva 3D) ── */
  #tilt-wrap {{
    position: fixed; inset: 0;
    /* perspective se aplica aquí: la cámara está 900px delante del plano */
    perspective: 900px;
    perspective-origin: 50% 100%;   /* punto de fuga en el borde inferior */
    background: #000;
  }}
  /* El mapa rota sobre su borde inferior → simula vista inclinada */
  #map {{
    position: absolute; inset: 0;
    width: 100%; height: 140%;      /* extra alto para no descubrir fondo al inclinar */
    transform-origin: 50% 100%;
    transition: transform 0.35s ease;
    will-change: transform;
  }}

  /* ── Leaflet theme ── */
  .leaflet-control-attribution {{
    background: rgba(15,23,42,0.75) !important;
    color: rgba(148,163,184,0.5) !important;
    font-family: var(--mono) !important; font-size: 9px !important;
  }}
  .leaflet-control-attribution a {{ color: rgba(34,211,238,0.4) !important; }}
  .leaflet-control-zoom a {{
    background: rgba(15,23,42,0.9) !important;
    color: {accent} !important;
    border-color: rgba(34,211,238,0.25) !important;
    font-family: var(--mono) !important;
  }}
  .leaflet-control-zoom a:hover {{ background: rgba(34,211,238,0.12) !important; }}

  /* ── Capa UI — encima del tilt, sin transform ── */
  #ui {{
    position: fixed; inset: 0;
    z-index: 100;
    pointer-events: none;   /* deja pasar eventos al mapa */
  }}
  /* Hijos del UI recuperan pointer-events */
  #ui > * {{ pointer-events: auto; }}

  /* ── Panel lateral ── */
  #panel {{
    position: absolute; top: 20px; right: 20px;
    width: 340px;
    background: rgba(15,23,42,0.93);
    border: 1px solid color-mix(in srgb, {accent} 35%, transparent);
    border-radius: 4px; padding: 20px;
    backdrop-filter: blur(12px);
    box-shadow: 0 0 32px color-mix(in srgb, {accent} 8%, transparent);
  }}
  #panel::before {{
    content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
    background: linear-gradient(90deg, transparent, {accent}, transparent);
    opacity: 0.6;
  }}
  .ph  {{ font-size: 10px; letter-spacing: 0.2em;
          color: color-mix(in srgb, {accent} 55%, transparent);
          margin-bottom: 14px;
          border-bottom: 1px solid color-mix(in srgb, {accent} 12%, transparent);
          padding-bottom: 10px; }}
  .pt  {{ font-size: 15px; font-weight: bold; color: #f1f5f9; letter-spacing: 0.06em; margin-bottom: 2px; }}
  .ps  {{ font-size: 11px; color: color-mix(in srgb, {accent} 65%, transparent);
          letter-spacing: 0.12em; margin-bottom: 16px; }}
  .kv  {{ display: flex; justify-content: space-between; align-items: flex-start;
          margin-bottom: 9px; gap: 8px; }}
  .kk  {{ color: rgba(148,163,184,0.7); letter-spacing: 0.08em; flex-shrink: 0;
          text-transform: uppercase; font-size: 10px; }}
  .vv  {{ color: #e2e8f0; text-align: right; word-break: break-all; font-size: 11px; }}
  .vv.ac {{ color: {accent}; }}
  .sha-box {{ margin-top: 12px; background: rgba(15,23,42,0.5);
              border: 1px solid color-mix(in srgb, {accent} 18%, transparent);
              border-radius: 2px; padding: 8px 10px; }}
  .sha-lbl {{ font-size: 9px; letter-spacing: 0.15em; color: rgba(148,163,184,0.5);
              margin-bottom: 4px; text-transform: uppercase; }}
  .sha-v   {{ font-size: 9.5px; color: color-mix(in srgb, {accent} 75%, transparent);
              word-break: break-all; line-height: 1.6; }}
  .st  {{ margin-top: 14px; display: flex; align-items: center; gap: 8px;
          font-size: 11px; letter-spacing: 0.08em; }}
  .sd  {{ width: 8px; height: 8px; border-radius: 50%; background: {status_color};
          flex-shrink: 0; animation: pd 1.4s ease-in-out infinite; }}
  .st-t {{ color: {status_color}; font-weight: bold; letter-spacing: 0.12em; }}
  .nota {{ margin-top: 10px; font-size: 9.5px; color: rgba(148,163,184,0.5); line-height: 1.55;
           font-style: italic;
           border-top: 1px solid color-mix(in srgb, {accent} 8%, transparent);
           padding-top: 9px; }}

  /* ── Botón SAR ── */
  #btn-sar {{
    position: absolute; bottom: 80px; left: 50%; transform: translateX(-50%);
    background: rgba(15,23,42,0.92); border: 1px solid {accent};
    color: {accent}; font-family: var(--mono); font-size: 12px;
    letter-spacing: 0.16em; padding: 11px 28px; border-radius: 3px; cursor: pointer;
    backdrop-filter: blur(8px); transition: all 0.25s ease; text-transform: uppercase;
    box-shadow: 0 0 18px color-mix(in srgb, {accent} 15%, transparent);
    white-space: nowrap;
  }}
  #btn-sar:hover  {{ background: color-mix(in srgb, {accent} 12%, transparent);
                     box-shadow: 0 0 28px color-mix(in srgb, {accent} 30%, transparent); }}
  #btn-sar.active {{ background: color-mix(in srgb, {accent} 15%, transparent);
                     border-color: var(--green); color: var(--green);
                     box-shadow: 0 0 32px rgba(0,255,65,0.25); }}

  /* ── Slider de inclinación ── */
  #tilt-ctrl {{
    position: absolute; bottom: 28px; left: 50%; transform: translateX(-50%);
    display: flex; align-items: center; gap: 10px;
    background: rgba(15,23,42,0.85); border: 1px solid rgba(34,211,238,0.2);
    border-radius: 3px; padding: 7px 14px;
    backdrop-filter: blur(8px);
    white-space: nowrap;
  }}
  .tilt-lbl {{ font-size: 9px; letter-spacing: 0.18em; color: color-mix(in srgb, {accent} 55%, transparent);
               text-transform: uppercase; }}
  #tilt-slider {{
    -webkit-appearance: none; appearance: none;
    width: 120px; height: 3px;
    background: color-mix(in srgb, {accent} 25%, transparent);
    border-radius: 2px; outline: none; cursor: pointer;
  }}
  #tilt-slider::-webkit-slider-thumb {{
    -webkit-appearance: none; appearance: none;
    width: 14px; height: 14px; border-radius: 50%;
    background: {accent}; cursor: pointer;
    box-shadow: 0 0 8px color-mix(in srgb, {accent} 60%, transparent);
  }}
  #tilt-slider::-moz-range-thumb {{
    width: 14px; height: 14px; border-radius: 50%; border: none;
    background: {accent}; cursor: pointer;
  }}
  #tilt-val {{ font-size: 10px; color: {accent}; min-width: 28px; text-align: right; }}

  /* ── Logo ── */
  #logo {{ position: absolute; top: 20px; left: 20px; font-family: var(--mono); }}
  #logo .brand   {{ font-size: 14px; color: var(--gold); letter-spacing: 0.2em; font-weight: bold; }}
  #logo .tagline {{ font-size: 9px; color: rgba(201,168,76,0.5); letter-spacing: 0.15em; margin-top: 3px; }}

  /* ── Scan line decorativa ── */
  #scan {{ position: absolute; inset: 0; pointer-events: none; overflow: hidden; }}
  #scan::after {{
    content: ''; position: absolute; left: 0; right: 0; height: 2px;
    background: linear-gradient(90deg, transparent, color-mix(in srgb, {accent} 20%, transparent), transparent);
    animation: sl 7s linear infinite;
  }}

  /* ── Coordenadas ── */
  #coords {{ position: absolute; bottom: 10px; left: 20px;
             font-size: 10px; color: color-mix(in srgb, {accent} 45%, transparent); letter-spacing: 0.1em; }}

  @keyframes pd {{ 0%,100% {{ opacity:1; box-shadow:0 0 6px {status_color}; }} 50% {{ opacity:0.4; }} }}
  @keyframes sl {{ 0% {{ top:-2px; }} 100% {{ top:100%; }} }}
</style>
</head>
<body>

<!-- Mapa con perspectiva 3D -->
<div id="tilt-wrap">
  <div id="map"></div>
</div>

<!-- UI fija encima, sin distorsión -->
<div id="ui">
  <div id="scan"></div>

  <div id="logo">
    <div class="brand">FARO PROTOCOL</div>
    <div class="tagline">SATELLITE ECONOMIC INTELLIGENCE</div>
  </div>

  <div id="panel">
    <div class="ph">VISOR TÁCTICO · {area_upper} · {country_up}</div>
    <div class="pt">{title_lbl}</div>
    <div class="ps">{vmeta["label"]}</div>

    <div class="kv"><span class="kk">Asset</span>    <span class="vv">{label}</span></div>
    <div class="kv"><span class="kk">Sensor</span>   <span class="vv ac">{sar_ref}</span></div>
    <div class="kv"><span class="kk">Centro</span>   <span class="vv">{lat_label} · {lon_label}</span></div>
    <div class="kv"><span class="kk">Score Faro</span><span class="vv ac">{score_str} / 100</span></div>
    <div class="kv"><span class="kk">NDVI</span>     <span class="vv">{ndvi_str}</span></div>
    <div class="kv"><span class="kk">SAR VV</span>   <span class="vv">{sar_str}</span></div>
    <div class="kv"><span class="kk">Actividad</span><span class="vv">{act_str}</span></div>
    <div class="kv"><span class="kk">Último run</span><span class="vv">{ultimo}</span></div>

    <div class="sha-box">
      <div class="sha-lbl">SHA-256 · Cert Faro Protocol</div>
      <div class="sha-v">{sha_display}</div>
    </div>

    <div class="st">
      <div class="sd"></div>
      <span class="st-t">{status_text}</span>
    </div>

    <div class="nota">{nota}<br>Certificación emitida al adquirir datos SAR de la zona.</div>
  </div>

  <button id="btn-sar">Activar Capa SAR (Faro Protocol)</button>

  <div id="tilt-ctrl">
    <span class="tilt-lbl">Inclinacion</span>
    <input type="range" id="tilt-slider" min="0" max="42" value="0" step="1">
    <span id="tilt-val">0°</span>
  </div>

  <div id="coords">LAT — · LON —</div>
</div>

<script>
// ── Mapa Leaflet + ESRI World Imagery (gratuito, sin token) ──────────────────
const map = L.map('map', {{
  center: [{lat}, {lon}],
  zoom: {zoom},
  zoomControl: false,
}});

L.tileLayer(
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}',
  {{
    attribution: 'Imagery &copy; Esri &mdash; Faro Protocol · Sentinel-1/2',
    maxZoom: 19,
  }}
).addTo(map);

L.control.zoom({{ position: 'bottomright' }}).addTo(map);

// ── Polígonos SAR ─────────────────────────────────────────────────────────────
// FIX: los polígonos NO se añaden al mapa inicialmente.
// addTo(map) / remove() garantiza que Leaflet los renderice correctamente
// cuando el usuario activa/desactiva la capa (evita bug de opacity:0 en SVG).
const POLYGONS = {polygons_js};
const ACCENT   = '{accent}';

const sarGroup = L.layerGroup(
  POLYGONS.map(coords => L.polygon(coords, {{
    color:       ACCENT,
    fillColor:   ACCENT,
    weight:      2.5,
    opacity:     0.9,
    fillOpacity: 0.35,
  }}))
);

// ── Toggle SAR con pulso animado ──────────────────────────────────────────────
let sarActive  = false;
let sarRaf     = null;
let sarDir     = 1;
let sarFill    = 0.35;

function animateSar() {{
  sarFill += sarDir * 0.012;
  if (sarFill >= 0.60) {{ sarFill = 0.60; sarDir = -1; }}
  if (sarFill <= 0.20) {{ sarFill = 0.20; sarDir =  1; }}
  sarGroup.eachLayer(l => l.setStyle({{ fillOpacity: sarFill, opacity: 0.7 + sarFill * 0.5 }}));
  sarRaf = requestAnimationFrame(animateSar);
}}

document.getElementById('btn-sar').addEventListener('click', function() {{
  sarActive = !sarActive;
  if (sarActive) {{
    this.textContent = 'SAR Activo · Faro Protocol  [ON]';
    this.classList.add('active');
    sarGroup.addTo(map);           // añadir al mapa → Leaflet renderiza SVG fresco
    sarRaf = requestAnimationFrame(animateSar);
  }} else {{
    this.textContent = 'Activar Capa SAR (Faro Protocol)';
    this.classList.remove('active');
    cancelAnimationFrame(sarRaf);
    map.removeLayer(sarGroup);    // quitar del mapa → limpio
  }}
}});

// ── Slider de inclinación 3D (CSS perspective + rotateX) ─────────────────────
const mapEl   = document.getElementById('map');
const slider  = document.getElementById('tilt-slider');
const tiltVal = document.getElementById('tilt-val');

slider.addEventListener('input', function() {{
  const deg = parseInt(this.value, 10);
  tiltVal.textContent = deg + 'deg';
  // rotateX negativo: la parte superior del mapa se aleja (vista oblicua)
  mapEl.style.transform = deg > 0 ? 'rotateX(-' + deg + 'deg)' : 'none';
  // Leaflet necesita recalcular tiles después del resize visual
  setTimeout(() => map.invalidateSize(), 350);
}});

// ── Coordenadas vivas ─────────────────────────────────────────────────────────
map.on('mousemove', function(e) {{
  const la = e.latlng.lat, lo = e.latlng.lng;
  const ls = (la < 0 ? Math.abs(la).toFixed(5) + 'S' : la.toFixed(5) + 'N');
  const lo2 = (lo < 0 ? Math.abs(lo).toFixed(5) + 'W' : lo.toFixed(5) + 'E');
  document.getElementById('coords').textContent = 'LAT ' + ls + ' · LON ' + lo2;
}});
</script>
</body>
</html>'''
    return html


def main():
    parser = argparse.ArgumentParser(description='Generador de visores tácticos Faro Protocol')
    parser.add_argument('--area',   required=True, help='Nombre del área (e.g. pilbara, vaca_muerta)')
    parser.add_argument('--output', default=None,  help='Nombre de archivo de salida (opcional)')
    args = parser.parse_args()

    area_name = args.area.lower().strip()
    print(f'[Faro Protocol] Generando visor: {area_name}')

    cfg  = load_area_config(area_name)
    data = load_system_data(area_name)
    sha  = get_cert_sha256(area_name)

    if sha:
        print(f'  SHA-256: {sha[:16]}...')
    else:
        print(f'  SHA-256: no hay cert para {area_name} — usando hash de data.json si existe')

    html = generate_html(area_name, cfg, data, sha)

    OUTPUTS.mkdir(exist_ok=True)
    out_name = args.output or f'visor_{area_name}.html'
    out_path = OUTPUTS / out_name
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)

    ctr = center_from_config(cfg)
    print(f'  OK >> {out_path}')
    print(f'     Centro: lon={ctr[0]}, lat={ctr[1]}  zoom={cfg.get("zoom", zoom_from_bounds(cfg.get("bounds")))}')
    print(f'     Vertical: {cfg.get("vertical", "?")}  Score: {data.get("score_faro", "—")}')
    return str(out_path)


if __name__ == '__main__':
    main()
