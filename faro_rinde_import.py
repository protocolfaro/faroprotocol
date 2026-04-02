"""
FARO PROTOCOL — Importador de datos de rinde
Fase 5: importa el historial de rinde de Renzo Basso (~12.000 registros),
calcula estadísticas por lote y por año, y genera el dataset listo
para el modelo de predicción (NDVI + SAR + historial → rinde estimado).

Uso:
    python faro_rinde_import.py --csv datos_rinde.csv
    python faro_rinde_import.py --csv datos_rinde.csv --area cordoba --verbose
    python faro_rinde_import.py --demo              # genera datos de ejemplo y los procesa

Salida:
    datos/rinde_procesado.json   — dataset completo estructurado
    datos/rinde_stats.json       — estadísticas por lote, año, cultivo
    datos/rinde_modelo_ready.csv — tabla plana lista para el modelo ML

Columnas CSV soportadas (detección automática):
    lote, lote_id, lot, field              → identificador de lote
    año, anio, year, campana, campaign     → año o campaña
    rinde, yield, tn_ha, kg_ha, rendimiento → rinde
    cultivo, crop, cultura                  → tipo de cultivo
"""

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# Forzar UTF-8 en Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).parent
DATOS_DIR    = PROJECT_ROOT / 'datos'

# Mapeo flexible de nombres de columna
ALIAS_LOTE    = {'lote', 'lote_id', 'lot', 'field', 'campo', 'parcela', 'potrero'}
ALIAS_ANIO    = {'año', 'anio', 'ano', 'year', 'campana', 'campaign', 'temporada', 'zafra'}
ALIAS_RINDE   = {'rinde', 'yield', 'rendimiento', 'tn_ha', 'kg_ha', 'ton_ha',
                 'rinde_tn_ha', 'rinde_kg_ha', 'yield_tn_ha', 'yield_kg_ha'}
ALIAS_CULTIVO = {'cultivo', 'crop', 'cultura', 'especie', 'grano', 'cereal'}

# Si el nombre de columna sugiere kg/ha en vez de tn/ha, convertir
COLS_KG_HA = {'kg_ha', 'rinde_kg_ha', 'yield_kg_ha', 'rendimiento_kg_ha'}


# ── Detección de columnas ────────────────────────────────────────────────────

def detectar_columnas(headers: list[str]) -> dict:
    """
    Detecta qué columna del CSV corresponde a lote, año, rinde y cultivo.
    La detección es case-insensitive y tolera acentos.
    """
    headers_norm = [h.lower().strip()
                    .replace('á','a').replace('é','e').replace('í','i')
                    .replace('ó','o').replace('ú','u').replace('ñ','n')
                    for h in headers]

    mapa = {"lote": None, "anio": None, "rinde": None, "cultivo": None,
            "rinde_en_kg": False}

    for i, h in enumerate(headers_norm):
        if h in ALIAS_LOTE    and mapa["lote"]    is None: mapa["lote"]    = i
        if h in ALIAS_ANIO    and mapa["anio"]    is None: mapa["anio"]    = i
        if h in ALIAS_RINDE   and mapa["rinde"]   is None:
            mapa["rinde"]    = i
            mapa["rinde_en_kg"] = h in COLS_KG_HA
        if h in ALIAS_CULTIVO and mapa["cultivo"] is None: mapa["cultivo"] = i

    faltantes = [k for k in ("lote", "anio", "rinde") if mapa[k] is None]
    if faltantes:
        raise ValueError(
            f"No se encontraron columnas obligatorias: {faltantes}.\n"
            f"Columnas detectadas: {headers}\n"
            f"Columnas esperadas (cualquier alias):\n"
            f"  lote   : {ALIAS_LOTE}\n"
            f"  anio   : {ALIAS_ANIO}\n"
            f"  rinde  : {ALIAS_RINDE}"
        )
    return mapa


# ── Parseo CSV ───────────────────────────────────────────────────────────────

def parsear_csv(csv_path: str) -> tuple[list[dict], dict]:
    """
    Lee el CSV y retorna una lista de registros normalizados.
    Retorna (registros, mapa_columnas).
    """
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Archivo no encontrado: {csv_path}")

    # Detectar encoding
    for enc in ('utf-8-sig', 'utf-8', 'latin-1', 'cp1252'):
        try:
            with open(path, encoding=enc) as f:
                sample = f.read(1024)
            encoding = enc
            break
        except UnicodeDecodeError:
            continue

    registros = []
    errores   = 0

    with open(path, encoding=encoding, newline='') as f:
        # Detectar separador (coma, punto y coma, tab)
        sample_line = f.readline()
        f.seek(0)
        if sample_line.count(';') > sample_line.count(','):
            sep = ';'
        elif '\t' in sample_line:
            sep = '\t'
        else:
            sep = ','

        reader = csv.reader(f, delimiter=sep)
        headers = [h.strip() for h in next(reader)]
        mapa    = detectar_columnas(headers)

        for num_linea, fila in enumerate(reader, start=2):
            if not any(fila):  # línea vacía
                continue
            try:
                lote    = fila[mapa["lote"]].strip()
                anio_raw = fila[mapa["anio"]].strip()
                rinde_raw = fila[mapa["rinde"]].replace(',', '.').strip()
                cultivo  = fila[mapa["cultivo"]].strip().lower() if mapa["cultivo"] is not None else "desconocido"

                if not lote or not anio_raw or not rinde_raw:
                    continue

                # Normalizar año (acepta "2018/19" → 2018, "19/20" → 2019, "2018" → 2018)
                anio_str = anio_raw.split('/')[0].strip()
                if len(anio_str) == 2:   # "18" → 2018
                    anio_str = ("20" if int(anio_str) < 50 else "19") + anio_str
                anio = int(anio_str)

                rinde = float(rinde_raw)
                if rinde < 0:
                    continue  # dato inválido

                # Convertir kg/ha → tn/ha
                if mapa["rinde_en_kg"]:
                    rinde = rinde / 1000.0

                # Normalizar nombre de cultivo
                cultivo = _normalizar_cultivo(cultivo)

                registros.append({
                    "lote":       lote,
                    "anio":       anio,
                    "rinde_tn_ha": round(rinde, 3),
                    "cultivo":    cultivo,
                })

            except (ValueError, IndexError):
                errores += 1
                continue

    print(f"  Registros parseados : {len(registros):,}")
    if errores:
        print(f"  Registros con error : {errores:,} (ignorados)")

    return registros, mapa


def _normalizar_cultivo(cultivo: str) -> str:
    """Normaliza variantes de nombres de cultivo al nombre canónico."""
    cultivo = cultivo.lower().strip()
    if any(x in cultivo for x in ('soja', 'soy', 'soya')): return 'soja'
    if any(x in cultivo for x in ('maiz', 'maíz', 'corn', 'maize')): return 'maiz'
    if any(x in cultivo for x in ('trigo', 'wheat')): return 'trigo'
    if any(x in cultivo for x in ('girasol', 'sunflower')): return 'girasol'
    if any(x in cultivo for x in ('sorgo', 'sorghum')): return 'sorgo'
    if any(x in cultivo for x in ('cebada', 'barley')): return 'cebada'
    return cultivo or 'desconocido'


# ── Cálculo de estadísticas ──────────────────────────────────────────────────

def calcular_estadisticas(registros: list[dict]) -> dict:
    """
    Calcula estadísticas por lote, por año y por cultivo.
    """

    def stats(valores: list[float]) -> dict:
        n   = len(valores)
        if n == 0:
            return {"n": 0, "media": None, "std": None, "min": None, "max": None, "cv_pct": None}
        media = sum(valores) / n
        varianza = sum((v - media) ** 2 for v in valores) / n if n > 1 else 0.0
        std   = varianza ** 0.5
        cv    = (std / media * 100) if media > 0 else 0.0
        return {
            "n":      n,
            "media":  round(media, 3),
            "std":    round(std, 3),
            "min":    round(min(valores), 3),
            "max":    round(max(valores), 3),
            "cv_pct": round(cv, 1),
        }

    # Por lote
    por_lote_raw = defaultdict(list)
    for r in registros:
        por_lote_raw[r["lote"]].append(r["rinde_tn_ha"])

    por_lote = {}
    for lote_id, valores in sorted(por_lote_raw.items()):
        por_lote[lote_id] = {
            "lote_id": lote_id,
            **stats(valores),
            "cultivos": sorted({r["cultivo"] for r in registros if r["lote"] == lote_id}),
            "anios":    sorted({r["anio"]    for r in registros if r["lote"] == lote_id}),
        }

    # Por año
    por_anio_raw = defaultdict(list)
    for r in registros:
        por_anio_raw[r["anio"]].append(r["rinde_tn_ha"])

    por_anio = {
        str(anio): {"anio": anio, **stats(vals)}
        for anio, vals in sorted(por_anio_raw.items())
    }

    # Por cultivo
    por_cultivo_raw = defaultdict(list)
    for r in registros:
        por_cultivo_raw[r["cultivo"]].append(r["rinde_tn_ha"])

    por_cultivo = {
        cult: {"cultivo": cult, **stats(vals)}
        for cult, vals in sorted(por_cultivo_raw.items())
    }

    # Global
    todos = [r["rinde_tn_ha"] for r in registros]
    global_stats = stats(todos)

    return {
        "global":     global_stats,
        "por_lote":   por_lote,
        "por_anio":   por_anio,
        "por_cultivo": por_cultivo,
    }


# ── Dataset modelo-ready ─────────────────────────────────────────────────────

def generar_modelo_ready(registros: list[dict]) -> list[dict]:
    """
    Genera la tabla plana para el modelo de predicción.
    Cada fila tiene: lote_id, anio, cultivo, rinde_tn_ha,
    y columnas vacías para ndvi_medio, sar_medio_db que el
    pipeline completa cuando corre para esa área y campaña.
    """
    return [
        {
            "lote_id":          r["lote"],
            "anio":             r["anio"],
            "cultivo":          r["cultivo"],
            "rinde_tn_ha":      r["rinde_tn_ha"],
            "ndvi_medio":       None,  # → completar con faro_fusion.py
            "sar_medio_db":     None,  # → completar con faro_sar_georef.py
            "indice_fusion":    None,  # → completar con faro_fusion.py
            "rinde_estimado":   None,  # → completar con modelo de predicción
        }
        for r in sorted(registros, key=lambda x: (x["lote"], x["anio"]))
    ]


# ── Salida ───────────────────────────────────────────────────────────────────

def guardar_resultados(registros: list[dict], estadisticas: dict,
                       modelo_ready: list[dict]) -> dict:
    DATOS_DIR.mkdir(exist_ok=True)

    rinde_procesado = {
        "_meta": {
            "generado_en":  datetime.now().strftime("%Y-%m-%d %H:%M"),
            "total_registros": len(registros),
            "lotes_unicos":   len(estadisticas["por_lote"]),
            "anios_cubiertos": sorted(estadisticas["por_anio"].keys()),
            "cultivos":       sorted(estadisticas["por_cultivo"].keys()),
        },
        "estadisticas": estadisticas,
        "registros":    registros,
    }

    path_procesado = DATOS_DIR / 'rinde_procesado.json'
    with open(path_procesado, 'w', encoding='utf-8') as f:
        json.dump(rinde_procesado, f, indent=2, ensure_ascii=False)

    path_stats = DATOS_DIR / 'rinde_stats.json'
    with open(path_stats, 'w', encoding='utf-8') as f:
        json.dump({
            "_meta":      rinde_procesado["_meta"],
            "estadisticas": estadisticas,
        }, f, indent=2, ensure_ascii=False)

    # CSV modelo-ready
    path_modelo = DATOS_DIR / 'rinde_modelo_ready.csv'
    if modelo_ready:
        with open(path_modelo, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=modelo_ready[0].keys())
            writer.writeheader()
            writer.writerows(modelo_ready)

    return {
        "rinde_procesado": str(path_procesado),
        "rinde_stats":     str(path_stats),
        "modelo_ready_csv": str(path_modelo),
    }


# ── Demo dataset ─────────────────────────────────────────────────────────────

def generar_demo_csv(path: Path, n: int = 200) -> None:
    """
    Genera un CSV de demostración con estructura realista
    para testear el pipeline sin datos reales.
    """
    import random
    random.seed(42)

    cultivos    = ['soja', 'maiz', 'trigo']
    n_lotes     = 20
    lotes       = [f"LOTE_{i:03d}" for i in range(1, n_lotes + 1)]
    anios       = list(range(2015, 2026))

    # Rendimientos base por cultivo (tn/ha) con variabilidad
    base_rinde  = {'soja': 3.2, 'maiz': 8.5, 'trigo': 3.8}
    # Factor por lote: algunos son mejores que otros
    factor_lote = {l: random.uniform(0.80, 1.20) for l in lotes}

    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['lote', 'año', 'cultivo', 'rinde_tn_ha'])

        count = 0
        while count < n:
            lote    = random.choice(lotes)
            anio    = random.choice(anios)
            cultivo = random.choice(cultivos)
            base    = base_rinde[cultivo] * factor_lote[lote]
            rinde   = round(max(0.5, random.gauss(base, base * 0.12)), 2)
            writer.writerow([lote, anio, cultivo, rinde])
            count += 1

    print(f"  Demo CSV generado: {path} ({n} registros)")


# ── Main ─────────────────────────────────────────────────────────────────────

def main(csv_path: str = None, verbose: bool = False) -> dict:
    print("=" * 60)
    print("  FARO PROTOCOL -- Importador de datos de rinde")
    print(f"  Fecha : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    print("\n[1/4] Parseando CSV...")
    registros, mapa = parsear_csv(csv_path)

    if not registros:
        print("  ERROR: No se encontraron registros validos en el CSV.")
        sys.exit(1)

    print("\n[2/4] Calculando estadisticas...")
    estadisticas = calcular_estadisticas(registros)

    g = estadisticas["global"]
    print(f"  Total registros  : {g['n']:,}")
    print(f"  Lotes unicos     : {len(estadisticas['por_lote']):,}")
    print(f"  Anos cubiertos   : {min(estadisticas['por_anio'])} - {max(estadisticas['por_anio'])}")
    print(f"  Cultivos         : {', '.join(sorted(estadisticas['por_cultivo']))}")
    print(f"  Rinde global     : {g['media']} ± {g['std']} tn/ha  (CV {g['cv_pct']}%)")
    print(f"  Rango            : {g['min']} - {g['max']} tn/ha")

    if verbose:
        print("\n  Por año:")
        for anio, s in sorted(estadisticas["por_anio"].items()):
            print(f"    {anio}  {s['media']:>6} tn/ha  (n={s['n']:>4}, CV={s['cv_pct']}%)")
        print("\n  Top 5 lotes por rinde medio:")
        top = sorted(estadisticas["por_lote"].values(),
                     key=lambda x: x["media"] or 0, reverse=True)[:5]
        for l in top:
            print(f"    {l['lote_id']:15} {l['media']:>6} tn/ha  (n={l['n']:>3})")

    print("\n[3/4] Generando dataset modelo-ready...")
    modelo_ready = generar_modelo_ready(registros)
    print(f"  Filas en modelo_ready: {len(modelo_ready):,}")
    print("  Columnas: lote_id, anio, cultivo, rinde_tn_ha,")
    print("            ndvi_medio [null], sar_medio_db [null],")
    print("            indice_fusion [null], rinde_estimado [null]")

    print("\n[4/4] Guardando resultados...")
    salidas = guardar_resultados(registros, estadisticas, modelo_ready)
    for nombre, path in salidas.items():
        print(f"  {nombre:<20} : {path}")

    print("\n" + "=" * 60)
    print("  Importacion completada")
    print("  Proximo paso: completar ndvi_medio y sar_medio_db")
    print("  usando los outputs de faro_fusion.py por campana")
    print("=" * 60 + "\n")

    return {"estadisticas": estadisticas, "n_registros": len(registros), "salidas": salidas}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='FARO PROTOCOL -- Importador de datos de rinde',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--csv',  help='Ruta al CSV de datos de rinde')
    group.add_argument('--demo', action='store_true',
                       help='Generar CSV de demo y procesarlo')
    parser.add_argument('--verbose', action='store_true',
                        help='Mostrar detalle por año y top lotes')
    args = parser.parse_args()

    if args.demo:
        demo_path = DATOS_DIR / 'demo_rinde.csv'
        DATOS_DIR.mkdir(exist_ok=True)
        generar_demo_csv(demo_path, n=200)
        main(csv_path=str(demo_path), verbose=args.verbose)
    else:
        main(csv_path=args.csv, verbose=args.verbose)
