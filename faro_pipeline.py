"""
FARO PROTOCOL — Pipeline unificado

Para la mayoria de los casos usar faro_engine.py (punto de entrada principal):
    python faro_engine.py --area cordoba

faro_pipeline.py sigue disponible para operaciones tecnicas especificas:
  - Solo georef SAR
  - Solo fusion
  - Solo busqueda SAR
  - Modo paso a paso sin modelo economico

Uso:
    python faro_pipeline.py --area cordoba --sar-file ruta/al/archivo.tiff
    python faro_pipeline.py --area cordoba --skip-georef
    python faro_pipeline.py --area cordoba --only-search
    python faro_pipeline.py --area cordoba --skip-vision
    python faro_pipeline.py --area cordoba --certificacion-detallada
"""

import argparse
import hashlib
import json
import math
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from faro_areas import load_area, list_areas

# ── Engine path (no expuesto en git) ──────────────────────────────────────────
_ENGINE_DIR = Path(__file__).parent / 'engine'
if _ENGINE_DIR.exists() and str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))


# ── Geofencing / cuota ────────────────────────────────────────────────────────

def _check_client_auth(uid: str, areas_requested: list) -> None:
    """
    Verifica que el cliente tiene autorización para procesar las áreas solicitadas.
    Compara contra `zones` en Firestore y valida cuota max_assets.
    Termina con sys.exit(3) si hay violación (equivalente a HTTP 403).
    """
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore as fb_fs

        sa_path = os.getenv('FIREBASE_SERVICE_ACCOUNT', '')
        if not firebase_admin._apps:
            if sa_path and Path(sa_path).exists():
                cred = credentials.Certificate(sa_path)
                firebase_admin.initialize_app(cred)
            else:
                print(f"[WARN] FIREBASE_SERVICE_ACCOUNT no configurado — geofencing omitido")
                return

        db      = fb_fs.client()
        snap    = db.collection('clients').doc(uid).get()
        if not snap.exists:
            print(f"[ERROR 403] Cliente {uid!r} no encontrado en Firestore.")
            sys.exit(3)

        profile    = snap.data()
        status     = profile.get('status', '')
        is_active  = profile.get('active_subscription') or status == 'active'

        if not is_active:
            print(f"[ERROR 403] Cliente {uid!r} sin suscripción activa.")
            sys.exit(3)

        max_assets = profile.get('max_assets', 1)
        zones_used = profile.get('zones', {})

        # Cuota total: ¿el cliente intenta procesar más áreas de las que le corresponden?
        all_authorized = set(zones_used.keys())

        # Para sovereign/enterprise (max_assets >= 999) no validamos zona por zona
        if max_assets < 999 and all_authorized:
            unauthorized = [a for a in areas_requested if a not in all_authorized]
            if unauthorized:
                print(f"[ERROR 403] Área(s) no autorizadas para el cliente {uid!r}:")
                for a in unauthorized:
                    print(f"  · {a}")
                print(f"  Zonas autorizadas: {sorted(all_authorized)}")
                sys.exit(3)

        # Cuota numérica
        total_intentado = len(set(list(zones_used.keys()) + list(areas_requested)))
        if max_assets < 999 and total_intentado > max_assets:
            print(f"[ERROR 403] Cuota excedida: plan permite {max_assets} activo(s), "
                  f"se intentaron {total_intentado}.")
            sys.exit(3)

        print(f"  [Auth OK] Cliente {uid!r} · plan='{profile.get('plan','?')}' "
              f"· {len(zones_used)}/{max_assets} activos usados")

    except SystemExit:
        raise
    except Exception as e:
        print(f"[WARN] Verificación de autorización falló ({e}) — continuando sin geofencing")


DATA_JSON = Path(__file__).parent / 'data.json'


def sha256_archivo(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for bloque in iter(lambda: f.read(65536), b''):
            h.update(bloque)
    return h.hexdigest()


def sha256_con_opendata(path: str, opendata: dict) -> str:
    """SHA-256 del reporte PNG + JSON de open data (para Certificación Detallada)."""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for bloque in iter(lambda: f.read(65536), b''):
            h.update(bloque)
    h.update(json.dumps(opendata, sort_keys=True, ensure_ascii=False).encode())
    return h.hexdigest()


# ═══════════════════════════════════════════════════════════════════════════
# OPEN DATA MODULES — Lazy load: solo con --certificacion-detallada
# ═══════════════════════════════════════════════════════════════════════════

def _od_get(url: str, timeout: int = 10) -> dict | None:
    """GET stateless a URL pública; None silencioso si falla."""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'FaroProtocol/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def od_ndvi_benchmark(area: dict, ndvi_actual: float | None) -> dict | None:
    """
    OD-1: NDVI actual vs media histórica del partido/departamento.
    Fuente: SIIA siia.gov.ar datos abiertos.
    Output: {"ndvi_actual": X, "media_regional": Y, "delta": Z}
    """
    try:
        if ndvi_actual is None:
            return None
        bounds = area.get('bounds')
        if not bounds or len(bounds) < 4:
            return None

        lat_c = round((bounds[1] + bounds[3]) / 2, 4)
        lon_c = round((bounds[0] + bounds[2]) / 2, 4)

        params = urllib.parse.urlencode({
            'ids':       'ndvi_departamento',
            'limit':     24,
            'sort':      'desc',
            'georef-lat': lat_c,
            'georef-lon': lon_c,
        })
        data = _od_get(f"https://www.siia.gov.ar/series/api/series/?{params}", timeout=8)
        if not data:
            return None

        series  = data.get('data', [])
        valores = [r[1] for r in series
                   if isinstance(r, list) and len(r) > 1 and r[1] is not None]
        if not valores:
            return None

        media_regional = round(sum(valores) / len(valores), 4)
        ndvi_act       = round(float(ndvi_actual), 4)
        return {
            "ndvi_actual":    ndvi_act,
            "media_regional": media_regional,
            "delta":          round(ndvi_act - media_regional, 4),
        }
    except Exception:
        return None


def od_flood_risk_srtm(area: dict) -> dict | None:
    """
    OD-2: Riesgo de inundación por pendiente SRTM30m.
    Fuente: api.opentopodata.org/v1/srtm30m (pública, sin key).
    Output: {"flood_risk": "high/low", "slope_avg": X}
    """
    try:
        bounds = area.get('bounds')
        if not bounds or len(bounds) < 4:
            return None

        lon_min, lat_min, lon_max, lat_max = bounds

        # Grilla 3×3 de puntos para calcular pendiente
        pts  = [(lat_min + (lat_max - lat_min) * i / 2,
                 lon_min + (lon_max - lon_min) * j / 2)
                for i in range(3) for j in range(3)]
        locs = '|'.join(f"{round(la, 5)},{round(lo, 5)}" for la, lo in pts)

        data = _od_get(
            f"https://api.opentopodata.org/v1/srtm30m?locations={locs}",
            timeout=15,
        )
        if not data or data.get('status') != 'OK':
            return None

        elevs = [r['elevation'] for r in data['results']
                 if isinstance(r.get('elevation'), (int, float))]
        if len(elevs) < 2:
            return None

        # Pendiente: rango_elevación / distancia diagonal del lote
        delta_h = max(elevs) - min(elevs)
        lat_km  = abs(lat_max - lat_min) * 111.0
        lon_km  = abs(lon_max - lon_min) * 111.0 * math.cos(math.radians((lat_min + lat_max) / 2))
        diag_m  = math.sqrt(lat_km ** 2 + lon_km ** 2) * 1000 or 1.0
        slope   = round(delta_h / diag_m * 100, 3)  # % (m/m × 100)

        return {
            "flood_risk": "high" if slope < 1.0 else "low",
            "slope_avg":  slope,
        }
    except Exception:
        return None


def od_water_balance_inia(area: dict) -> dict | None:
    """
    OD-3: Balance hídrico IBH para las coordenadas del lote.
    Fuente: INIA GRAS gras.inia.uy datos abiertos.
    Output: {"ibh": X, "water_status": "ok/stress/deficit"}
    """
    try:
        center = area.get('center')
        bounds = area.get('bounds')
        if center and len(center) >= 2:
            lat, lon = center[0], center[1]
        elif bounds and len(bounds) >= 4:
            lat = (bounds[1] + bounds[3]) / 2
            lon = (bounds[0] + bounds[2]) / 2
        else:
            return None

        params = urllib.parse.urlencode({
            'lat':    round(lat, 4),
            'lon':    round(lon, 4),
            'format': 'json',
        })
        data = _od_get(f"https://gras.inia.uy/api/ibh?{params}", timeout=10)
        if not data:
            return None

        ibh_raw = data.get('ibh') or data.get('value') or data.get('IBH')
        if ibh_raw is None:
            return None

        ibh    = round(float(ibh_raw), 1)
        status = "ok" if ibh >= -50 else ("stress" if ibh >= -100 else "deficit")
        return {"ibh": ibh, "water_status": status}
    except Exception:
        return None


def actualizar_data_json(area_name: str, stats: dict, sha256: str):
    """Actualiza data.json con los stats del pipeline para el área procesada."""
    data = {}
    if DATA_JSON.exists():
        with open(DATA_JSON, encoding='utf-8') as f:
            data = json.load(f)

    ahora = datetime.now().strftime("%Y-%m-%d %H:%M")
    data.setdefault('_meta', {})['generado_en'] = ahora

    data.setdefault('pipeline', {})['ultimo_run'] = ahora
    data['pipeline'].setdefault(area_name, {}).update({
        "estado":              "OK",
        "ultimo_run":          ahora,
        "ndvi_medio":          stats.get("ndvi_medio"),
        "sar_medio_db":        stats.get("sar_medio_db"),
        "indice_fusion_medio": stats.get("indice_fusion_medio"),
        "pixeles_analizados":  stats.get("pixeles_analizados"),
        "sha256":              sha256[:16] + "...",
        # Fase 6
        "ndvi_global_medio":   stats.get("ndvi_global_medio"),
        "pct_anomalia":        stats.get("pct_anomalia"),
    })

    with open(DATA_JSON, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"  data.json actualizado con stats de {area_name}")


def main():
    parser = argparse.ArgumentParser(
        description='FARO PROTOCOL — Pipeline unificado (SAR + S2 óptico)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Areas disponibles: {', '.join(list_areas())}"
    )
    parser.add_argument(
        '--area', required=True, nargs='+',
        help='Área(s) a procesar. Admite múltiples: --area cordoba balcarce vaca_muerta'
    )
    parser.add_argument(
        '--with-s2', action='store_true', default=True,
        help='Descargar Sentinel-2 L2A y calcular NDVI (activo por defecto)'
    )
    parser.add_argument(
        '--no-s2', action='store_true',
        help='No descargar S2 (modo SAR-only forzado)'
    )
    parser.add_argument(
        '--max-nubes-s2', type=float, default=50.0,
        help='Máximo %% nubes para S2 (default: 50)'
    )
    parser.add_argument(
        '--sar-file',
        help='Path al archivo SAR .tiff crudo (solo para área única sin georef)'
    )
    parser.add_argument(
        '--skip-georef', action='store_true',
        help='Omitir georreferenciación SAR (el .tif ya existe)'
    )
    parser.add_argument(
        '--only-search', action='store_true',
        help='Solo buscar imágenes SAR disponibles, sin procesar'
    )
    parser.add_argument(
        '--skip-vision', action='store_true',
        help='Omitir clasificación de vigor'
    )
    parser.add_argument(
        '--client-uid', metavar='UID',
        help='UID Firebase del cliente (activa geofencing y verificación de cuota)'
    )
    parser.add_argument(
        '--certificacion-detallada', action='store_true',
        help='Activar módulos Open Data: NDVI benchmark SIIA, riesgo inundación SRTM, balance hídrico INIA'
    )
    args = parser.parse_args()

    # ── Geofencing: verificar autorización del cliente ──
    if args.client_uid:
        _check_client_auth(args.client_uid, args.area)

    # Modo multi-área: delegar en faro_sar_auto.pipeline_area()
    if len(args.area) > 1 or (len(args.area) == 1 and not args.sar_file and not args.only_search):
        from faro_sar_auto import pipeline_area

        areas_a_procesar = args.area
        use_s2 = args.with_s2 and not args.no_s2
        resultados = []
        errores    = []

        print()
        print('=' * 65)
        print(f'  FARO PROTOCOL — Batch pipeline ({len(areas_a_procesar)} áreas)')
        print(f'  Modo: {"SAR + S2 óptico" if use_s2 else "SAR-only"}')
        print(f'  Inicio: {datetime.now().strftime("%Y-%m-%d %H:%M")}')
        print('=' * 65)

        for i, area_name in enumerate(areas_a_procesar, 1):
            print(f'\n[{i}/{len(areas_a_procesar)}] ── {area_name.upper()} ──')
            try:
                res = pipeline_area(
                    area_name,
                    skip_georef=True,   # el georef ya existe para todas
                    with_s2=use_s2,
                    max_nubes_s2=args.max_nubes_s2,
                )
                resultados.append(res)
            except Exception as exc:
                print(f'  ERROR en {area_name}: {exc}')
                errores.append({'area': area_name, 'error': str(exc)})

        # Resumen final
        print()
        print('=' * 65)
        print(f'  RESUMEN BATCH — {datetime.now().strftime("%Y-%m-%d %H:%M")}')
        print('=' * 65)
        for r in resultados:
            ndvi_tag = '★ S2' if 'S2' in r.get('ndvi_mode', '') else 'SAR'
            print(f'  {r["area"]:<15} Score {r.get("score","?"):>5} | {ndvi_tag} | SHA {r.get("sha256","?")[:16]}...')
        for e in errores:
            print(f'  {e["area"]:<15} ERROR: {e["error"][:50]}')
        print(f'  Total OK: {len(resultados)} | Errores: {len(errores)}')
        print('=' * 65)
        return

    # Modo área única con SAR file (path legacy)
    area_name = args.area[0]
    area = load_area(area_name)

    print("\n" + "=" * 60)
    print(f"  FARO PROTOCOL -- Pipeline completo")
    print(f"  Area    : {area['label']}")
    print(f"  Vertical: {area['vertical'].upper()}")
    print(f"  Fecha   : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60 + "\n")

    # ── Modo solo busqueda ──────────────────────────────────────
    if args.only_search:
        import faro_sar_pipeline
        faro_sar_pipeline.main(args.area)
        return

    # ── Paso 1: Georreferenciacion SAR ─────────────────────────
    if not args.skip_georef:
        if not args.sar_file:
            print("ERROR: --sar-file es obligatorio para la georreferenciacion.")
            print("       Usa --skip-georef si el archivo .tif ya existe.")
            sys.exit(1)

        print("[1/4] Georreferenciando SAR...")
        import faro_sar_georef
        faro_sar_georef.main(area=area, sar_input=args.sar_file)
    else:
        print("[1/4] Georreferenciacion omitida (--skip-georef)\n")

    # ── Paso 2: Fusion SAR + NDVI ───────────────────────────────
    print("[2/4] Fusionando SAR + NDVI...")
    import faro_fusion
    resultado = faro_fusion.main(area=area)
    reporte_png = resultado['output_png']
    stats       = resultado['stats']

    # ── Paso 3: Vision computacional (Fase 6) ──────────────────
    vision_json = None
    if not args.skip_vision:
        ndvi_path = area.get('ndvi_tif')
        if ndvi_path and Path(ndvi_path).exists():
            print("[3/5] Clasificacion de vigor + deteccion de anomalias...")
            import faro_vision
            vstats      = faro_vision.run(area['name'])
            vision_json = f"faro_vision_{area['name']}.json"
        else:
            print("[3/5] Vision omitida: NDVI no disponible para esta area")
    else:
        print("[3/5] Vision omitida (--skip-vision)\n")

    # ── Paso 3.5: Open Data (solo Certificación Detallada) ─────────────────
    opendata_result: dict = {}
    if args.certificacion_detallada:
        print("[OD]  Consultando módulos Open Data...")
        ndvi_bench = od_ndvi_benchmark(area, stats.get('ndvi_medio'))
        flood_risk = od_flood_risk_srtm(area)
        water_bal  = od_water_balance_inia(area)

        opendata_result = {
            "ndvi_benchmark":     ndvi_bench,
            "flood_risk_srtm":    flood_risk,
            "water_balance_inia": water_bal,
        }

        tag = lambda v: str(v) if v else "N/A"
        print(f"  [OD-1] NDVI Benchmark    : {tag(ndvi_bench)}")
        print(f"  [OD-2] Riesgo Inundación : {tag(flood_risk)}")
        print(f"  [OD-3] Balance Hídrico   : {tag(water_bal)}")

        od_path = Path(reporte_png).parent / f"faro_opendata_{area['name']}.json"
        od_path.write_text(
            json.dumps(opendata_result, indent=2, ensure_ascii=False),
            encoding='utf-8',
        )
        print(f"  Open data guardado: {od_path.name}\n")

    # ── Paso 4: SHA-256 del reporte ─────────────────────────────
    print("[4/5] Calculando SHA-256 del reporte...")
    digest = (
        sha256_con_opendata(reporte_png, opendata_result)
        if opendata_result
        else sha256_archivo(reporte_png)
    )
    hash_path = Path(reporte_png).with_suffix('.sha256')
    hash_path.write_text(f"{digest}  {Path(reporte_png).name}\n", encoding='utf-8')

    # ── Paso 5: Modelo económico + data.json via faro_engine ────
    print("[5/5] Aplicando modelo economico (faro_engine)...")
    from faro_engine import FaroEngine, DatosCrudos, _sha256
    from faro_engine import InsightEconomico

    crudos = DatosCrudos(
        area_name            = area['name'],
        sar_medio_db         = stats.get('sar_medio_db'),
        ndvi_medio           = stats.get('ndvi_medio'),
        indice_fusion_medio  = stats.get('indice_fusion_medio'),
        pixeles_analizados   = stats.get('pixeles_analizados'),
        pct_anomalia         = 0.0,
        reporte_png          = reporte_png,
        sha256_completo      = digest,
        tiene_sar            = Path(area.get('sar_output', '')).exists(),
        tiene_ndvi           = stats.get('ndvi_medio') is not None,
        tiene_vision         = vision_json is not None,
    )
    if vision_json and Path(vision_json).exists():
        with open(vision_json, encoding='utf-8') as f:
            vstats_d = json.load(f)
        crudos.pct_anomalia = vstats_d.get('pct_anomalia', 0.0)
        crudos.ndvi_medio   = vstats_d.get('ndvi_global_medio', crudos.ndvi_medio)

    engine  = FaroEngine()
    insight = engine._generar_insight(area, crudos)
    engine._publicar(crudos, insight)

    print("\n" + "=" * 60)
    print(f"  Pipeline completado")
    print(f"  Reporte fusion : {reporte_png}")
    if vision_json:
        print(f"  Clasificacion  : faro_clasificacion_{area['name']}.png")
    print(f"  Score Faro     : {insight.score_faro} / 100")
    print(f"  SHA-256        : {digest}")
    if opendata_result:
        print(f"  Cert. Detallada: faro_opendata_{area['name']}.json")
        nb = opendata_result.get('ndvi_benchmark')
        fr = opendata_result.get('flood_risk_srtm')
        wb = opendata_result.get('water_balance_inia')
        if nb:
            print(f"    NDVI delta vs región : {nb['delta']:+.4f}")
        if fr:
            print(f"    Riesgo inundación    : {fr['flood_risk']} (slope {fr['slope_avg']}%)")
        if wb:
            print(f"    Balance hídrico IBH  : {wb['ibh']} → {wb['water_status']}")
    print(f"  data.json      : {DATA_JSON}")
    print("=" * 60 + "\n")


if __name__ == '__main__':
    main()
