"""
FARO PROTOCOL — Pipeline unificado

Ejecuta el pipeline completo para un area:
  georef SAR -> fusion SAR + NDVI -> vision computacional -> reporte PNG + SHA-256 -> data.json

Uso:
    python faro_pipeline.py --area cordoba --sar-file ruta/al/archivo.tiff
    python faro_pipeline.py --area vaca_muerta --sar-file ruta/al/archivo.tiff
    python faro_pipeline.py --area balcarce --sar-file ruta/al/archivo.tiff

    # Solo fusion (si el SAR ya fue georreferenciado):
    python faro_pipeline.py --area cordoba --skip-georef

    # Solo busqueda SAR (sin procesar):
    python faro_pipeline.py --area cordoba --only-search

    # Omitir vision computacional (Fase 6):
    python faro_pipeline.py --area cordoba --skip-vision
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

from faro_areas import load_area, list_areas

DATA_JSON = Path(__file__).parent / 'data.json'


def sha256_archivo(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for bloque in iter(lambda: f.read(65536), b''):
            h.update(bloque)
    return h.hexdigest()


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
        description='FARO PROTOCOL — Pipeline unificado',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Areas disponibles: {', '.join(list_areas())}"
    )
    parser.add_argument(
        '--area', required=True,
        help='Nombre del area (ej: cordoba, vaca_muerta, balcarce)'
    )
    parser.add_argument(
        '--sar-file',
        help='Path al archivo SAR .tiff crudo descargado de Copernicus'
    )
    parser.add_argument(
        '--skip-georef', action='store_true',
        help='Omitir el paso de georreferenciacion (el .tif ya existe)'
    )
    parser.add_argument(
        '--only-search', action='store_true',
        help='Solo buscar imagenes SAR disponibles, sin procesar'
    )
    parser.add_argument(
        '--skip-vision', action='store_true',
        help='Omitir la clasificacion de vigor (Fase 6) — util si el NDVI no esta disponible'
    )
    args = parser.parse_args()

    area = load_area(args.area)

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
            import sys as _sys
            # Ejecutar main() directamente con los argumentos correctos
            _orig_argv = _sys.argv
            _sys.argv  = ['faro_vision.py', '--area', area['name']]
            try:
                faro_vision.main()
            except SystemExit:
                pass
            finally:
                _sys.argv = _orig_argv
            vision_json = f"faro_vision_{area['name']}.json"
        else:
            print("[3/5] Vision omitida: NDVI no disponible para esta area")
    else:
        print("[3/5] Vision omitida (--skip-vision)\n")

    # ── Paso 4: SHA-256 del reporte ─────────────────────────────
    print("[4/5] Calculando SHA-256 del reporte...")
    digest    = sha256_archivo(reporte_png)
    hash_path = Path(reporte_png).with_suffix('.sha256')
    hash_path.write_text(f"{digest}  {Path(reporte_png).name}\n", encoding='utf-8')

    # ── Paso 5: Actualizar data.json ────────────────────────────
    print("[5/5] Actualizando data.json...")
    # Agregar stats de vision si existen
    if vision_json and Path(vision_json).exists():
        with open(vision_json, encoding='utf-8') as f:
            vstats = json.load(f)
        stats['ndvi_global_medio'] = vstats.get('ndvi_global_medio')
        stats['pct_anomalia']      = vstats.get('pct_anomalia')
    actualizar_data_json(area['name'], stats, digest)

    print("\n" + "=" * 60)
    print(f"  Pipeline completado")
    print(f"  Reporte fusión : {reporte_png}")
    if vision_json:
        print(f"  Clasificacion  : faro_clasificacion_{area['name']}.png")
    print(f"  SHA-256        : {digest}")
    print(f"  data.json      : {DATA_JSON}")
    print("=" * 60 + "\n")


if __name__ == '__main__':
    main()
