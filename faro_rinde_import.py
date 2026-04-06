"""
FARO PROTOCOL — Importador y calibrador de datos de rinde
==========================================================

Soporta dos formatos:

  1. MAGyP Nacional (estimaciones-agricolas-*.csv)
     Columnas: cultivo, anio, campania, provincia, departamento,
               superficie_sembrada_ha, superficie_cosechada_ha,
               produccion_tm, rendimiento_kgxha
     Salida  : datos/baseline_nacional.json
               datos/baseline_departamental.json

  2. Campo específico (datos Basso / cualquier productor)
     Columnas: lote, año, cultivo, rinde_tn_ha  (nombres flexibles)
     Salida  : datos/rinde_procesado.json
               datos/rinde_modelo_ready.csv

Uso:
    python faro_rinde_import.py --nacional datos/estimaciones-agricolas-2026-03.csv
    python faro_rinde_import.py --csv datos_basso.csv --area cordoba
    python faro_rinde_import.py --demo
    python faro_rinde_import.py --baseline-query --provincia Córdoba --cultivo soja
"""

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).parent
DATOS_DIR    = PROJECT_ROOT / 'datos'

# Período reciente para el baseline (últimas N campañas)
PERIODO_BASELINE_ANOS = 15  # 2010-2024

# Cultivos principales del núcleo productivo argentino
CULTIVOS_PRINCIPALES = {
    'soja total', 'soja 1ra', 'soja 2da',
    'maíz', 'trigo total', 'girasol',
    'sorgo', 'cebada cervecera',
}

# Mapeo a nombre canónico para el engine
CULTIVO_CANONICO = {
    'soja total': 'soja', 'soja 1ra': 'soja', 'soja 2da': 'soja',
    'maíz': 'maiz', 'trigo total': 'trigo', 'trigo candeal': 'trigo',
    'girasol': 'girasol', 'sorgo': 'sorgo',
    'cebada cervecera': 'cebada', 'cebada forrajera': 'cebada',
}

# Provincias del núcleo productivo (para el análisis prioritario)
NUCLEO_PRODUCTIVO = {
    'Buenos Aires', 'Córdoba', 'Santa Fe',
    'Entre Ríos', 'La Pampa', 'Santiago del Estero',
    'Tucumán', 'Salta', 'Chaco',
}

# Alias flexibles para formato campo (Basso)
ALIAS_LOTE    = {'lote', 'lote_id', 'lot', 'field', 'campo', 'parcela', 'potrero'}
ALIAS_ANIO    = {'año', 'anio', 'ano', 'year', 'campana', 'campaign', 'temporada'}
ALIAS_RINDE   = {'rinde', 'yield', 'rendimiento', 'tn_ha', 'kg_ha', 'ton_ha',
                 'rinde_tn_ha', 'rinde_kg_ha', 'yield_tn_ha', 'yield_kg_ha'}
ALIAS_CULTIVO = {'cultivo', 'crop', 'cultura', 'especie', 'grano'}
COLS_KG_HA    = {'kg_ha', 'rinde_kg_ha', 'yield_kg_ha', 'rendimiento_kg_ha'}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _stats(valores):
    n = len(valores)
    if n == 0:
        return {"n": 0, "media_tn_ha": None, "std": None, "min": None, "max": None, "cv_pct": None}
    media = sum(valores) / n
    std   = (sum((v - media) ** 2 for v in valores) / n) ** 0.5 if n > 1 else 0.0
    cv    = (std / media * 100) if media > 0 else 0.0
    return {
        "n":           n,
        "media_tn_ha": round(media, 3),
        "std":         round(std, 3),
        "min":         round(min(valores), 3),
        "max":         round(max(valores), 3),
        "cv_pct":      round(cv, 1),
    }

def _detectar_encoding(path):
    for enc in ('utf-8-sig', 'utf-8', 'latin-1', 'cp1252'):
        try:
            with open(path, encoding=enc) as f:
                f.read(2048)
            return enc
        except UnicodeDecodeError:
            continue
    return 'latin-1'

def _detectar_sep(path, enc):
    with open(path, encoding=enc) as f:
        line = f.readline()
    if line.count(';') > line.count(','):
        return ';'
    if '\t' in line:
        return '\t'
    return ','

def _normalizar_cultivo(nombre):
    s = nombre.lower().strip()
    if 'soja' in s: return 'soja'
    if 'maíz' in s or 'maiz' in s: return 'maiz'
    if 'trigo' in s: return 'trigo'
    if 'girasol' in s: return 'girasol'
    if 'sorgo' in s: return 'sorgo'
    if 'cebada' in s: return 'cebada'
    if 'algod' in s: return 'algodón'
    if 'arroz' in s: return 'arroz'
    if 'man' in s and 'í' in s: return 'maní'
    return s or 'desconocido'


# ══════════════════════════════════════════════════════════════════════════════
# FORMATO 1 — MAGyP Nacional
# ══════════════════════════════════════════════════════════════════════════════

def procesar_nacional(csv_path: str, verbose: bool = False) -> dict:
    """
    Procesa el CSV nacional de MAGyP y genera:
      - datos/baseline_nacional.json    → por provincia + cultivo canónico
      - datos/baseline_departamental.json → por departamento + cultivo
    """
    path = Path(csv_path)
    enc  = _detectar_encoding(path)
    sep  = _detectar_sep(path, enc)

    print(f"  Encoding: {enc} | Separador: '{sep}'")

    # Leer y filtrar
    filas_validas = 0
    filas_error   = 0

    # Acumuladores: provincia → cultivo_canonico → lista de rinde tn/ha
    acc_prov  = defaultdict(lambda: defaultdict(list))
    # departamento → cultivo_canonico → lista
    acc_depto = defaultdict(lambda: defaultdict(list))
    # nacional → cultivo_canonico → lista
    acc_nac   = defaultdict(list)
    # serie temporal: provincia → cultivo → año → [rindes]
    acc_serie = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))

    with open(path, encoding=enc, newline='') as f:
        reader = csv.DictReader(f, delimiter=sep)
        for row in reader:
            try:
                cultivo_raw = row.get('cultivo', '').strip()
                anio_raw    = row.get('anio', '').strip()
                provincia   = row.get('provincia', '').strip()
                departamento = row.get('departamento', '').strip()
                rend_raw    = row.get('rendimiento_kgxha', '').strip()

                if not all([cultivo_raw, anio_raw, provincia, rend_raw]):
                    continue
                if provincia in ('NULL', '', 'null'):
                    continue

                rend_kg = float(rend_raw.replace(',', '.'))
                if rend_kg <= 0 or rend_kg > 20000:  # sanity check
                    continue

                anio = int(anio_raw)
                rend_tn = rend_kg / 1000.0
                cultivo_can = CULTIVO_CANONICO.get(cultivo_raw, _normalizar_cultivo(cultivo_raw))

                # Solo período baseline
                if anio >= (2024 - PERIODO_BASELINE_ANOS + 1):
                    acc_prov[provincia][cultivo_can].append(rend_tn)
                    if departamento:
                        acc_depto[departamento][cultivo_can].append(rend_tn)
                    acc_nac[cultivo_can].append(rend_tn)

                # Serie histórica completa
                acc_serie[provincia][cultivo_can][anio].append(rend_tn)

                filas_validas += 1

            except (ValueError, KeyError):
                filas_error += 1
                continue

    print(f"  Filas válidas : {filas_validas:,}")
    print(f"  Filas error   : {filas_error:,}")

    # ── Baseline provincial ────────────────────────────────────────────────────
    baseline_prov = {}
    for prov, cultivos in sorted(acc_prov.items()):
        baseline_prov[prov] = {}
        for cult, vals in sorted(cultivos.items()):
            s = _stats(vals)
            s['periodo'] = f"{2024 - PERIODO_BASELINE_ANOS + 1}-2024"
            s['es_nucleo'] = prov in NUCLEO_PRODUCTIVO
            baseline_prov[prov][cult] = s

    # ── Baseline departamental ─────────────────────────────────────────────────
    baseline_depto = {}
    for depto, cultivos in sorted(acc_depto.items()):
        baseline_depto[depto] = {}
        for cult, vals in sorted(cultivos.items()):
            s = _stats(vals)
            s['periodo'] = f"{2024 - PERIODO_BASELINE_ANOS + 1}-2024"
            baseline_depto[depto][cult] = s

    # ── Baseline nacional ──────────────────────────────────────────────────────
    baseline_nac = {}
    for cult, vals in sorted(acc_nac.items()):
        s = _stats(vals)
        s['periodo'] = f"{2024 - PERIODO_BASELINE_ANOS + 1}-2024"
        baseline_nac[cult] = s

    # ── Serie temporal (tendencia) ─────────────────────────────────────────────
    tendencias = {}
    for prov in sorted(acc_serie):
        tendencias[prov] = {}
        for cult in sorted(acc_serie[prov]):
            serie = {}
            for anio in sorted(acc_serie[prov][cult]):
                vals = acc_serie[prov][cult][anio]
                serie[str(anio)] = round(sum(vals) / len(vals), 3)
            tendencias[prov][cult] = serie

    # ── Guardar ───────────────────────────────────────────────────────────────
    DATOS_DIR.mkdir(exist_ok=True)

    meta = {
        "_meta": {
            "fuente": "MAGyP — Estimaciones Agrícolas",
            "generado_en": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "archivo_origen": path.name,
            "filas_procesadas": filas_validas,
            "periodo_baseline_anos": PERIODO_BASELINE_ANOS,
            "provincias": len(baseline_prov),
            "departamentos": len(baseline_depto),
        }
    }

    out_prov = DATOS_DIR / 'baseline_nacional.json'
    with open(out_prov, 'w', encoding='utf-8') as f:
        json.dump({**meta, "nacional": baseline_nac,
                   "por_provincia": baseline_prov,
                   "tendencias": tendencias}, f, indent=2, ensure_ascii=False)

    out_depto = DATOS_DIR / 'baseline_departamental.json'
    with open(out_depto, 'w', encoding='utf-8') as f:
        json.dump({**meta, "por_departamento": baseline_depto}, f, indent=2, ensure_ascii=False)

    # ── Imprimir resumen ───────────────────────────────────────────────────────
    print()
    print("  BASELINE NACIONAL — Núcleo productivo (últimos 15 años):")
    print(f"  {'Cultivo':<12} {'Media t/ha':>10} {'CV%':>6} {'N':>6}")
    print("  " + "-" * 36)
    for cult in ['soja', 'maiz', 'trigo', 'girasol', 'sorgo']:
        if cult in baseline_nac:
            b = baseline_nac[cult]
            print(f"  {cult:<12} {b['media_tn_ha']:>10.3f} {b['cv_pct']:>6.1f} {b['n']:>6,}")

    print()
    print("  CÓRDOBA — Soja (referencia piloto):")
    if 'Córdoba' in baseline_prov and 'soja' in baseline_prov['Córdoba']:
        b = baseline_prov['Córdoba']['soja']
        print(f"  Media: {b['media_tn_ha']} t/ha | CV: {b['cv_pct']}% | n={b['n']:,}")
        # Tendencia reciente
        if 'Córdoba' in tendencias and 'soja' in tendencias['Córdoba']:
            serie = tendencias['Córdoba']['soja']
            recientes = {k: v for k, v in serie.items() if int(k) >= 2019}
            print(f"  Tendencia 2019-2024: " + " | ".join(f"{k}:{v}" for k, v in sorted(recientes.items())))

    if verbose:
        print()
        print("  TOP PROVINCIAS por rinde soja:")
        ranking = [(p, d['soja']['media_tn_ha'])
                   for p, d in baseline_prov.items()
                   if 'soja' in d and d['soja']['n'] >= 10]
        for p, m in sorted(ranking, key=lambda x: -x[1])[:8]:
            print(f"    {p:<25} {m:.3f} t/ha")

    return {
        "baseline_nacional": str(out_prov),
        "baseline_departamental": str(out_depto),
        "provincias": len(baseline_prov),
        "departamentos": len(baseline_depto),
    }


# ══════════════════════════════════════════════════════════════════════════════
# FORMATO 2 — Campo específico (Basso)
# ══════════════════════════════════════════════════════════════════════════════

def _detectar_columnas_campo(headers):
    norm = [h.lower().strip()
            .replace('á','a').replace('é','e').replace('í','i')
            .replace('ó','o').replace('ú','u').replace('ñ','n')
            for h in headers]
    mapa = {"lote": None, "anio": None, "rinde": None, "cultivo": None, "rinde_en_kg": False}
    for i, h in enumerate(norm):
        if h in ALIAS_LOTE    and mapa["lote"]    is None: mapa["lote"]    = i
        if h in ALIAS_ANIO    and mapa["anio"]    is None: mapa["anio"]    = i
        if h in ALIAS_RINDE   and mapa["rinde"]   is None:
            mapa["rinde"] = i
            mapa["rinde_en_kg"] = h in COLS_KG_HA
        if h in ALIAS_CULTIVO and mapa["cultivo"] is None: mapa["cultivo"] = i
    faltantes = [k for k in ("lote", "anio", "rinde") if mapa[k] is None]
    if faltantes:
        raise ValueError(f"Columnas obligatorias no encontradas: {faltantes}. Headers: {headers}")
    return mapa


def procesar_campo(csv_path: str, area_name: str = None, verbose: bool = False) -> dict:
    """Procesa CSV de campo específico (Basso). Genera rinde_procesado.json y modelo_ready.csv."""
    path = Path(csv_path)
    enc  = _detectar_encoding(path)
    sep  = _detectar_sep(path, enc)

    registros = []
    errores   = 0

    with open(path, encoding=enc, newline='') as f:
        reader  = csv.reader(f, delimiter=sep)
        headers = [h.strip() for h in next(reader)]
        mapa    = _detectar_columnas_campo(headers)

        for fila in reader:
            if not any(fila):
                continue
            try:
                lote     = fila[mapa["lote"]].strip()
                anio_raw = fila[mapa["anio"]].strip()
                rind_raw = fila[mapa["rinde"]].replace(',', '.').strip()
                cultivo  = fila[mapa["cultivo"]].strip() if mapa["cultivo"] is not None else "desconocido"

                if not all([lote, anio_raw, rind_raw]):
                    continue

                anio_str = anio_raw.split('/')[0].strip()
                if len(anio_str) == 2:
                    anio_str = ("20" if int(anio_str) < 50 else "19") + anio_str
                anio  = int(anio_str)
                rinde = float(rind_raw)

                if rinde < 0:
                    continue
                if mapa["rinde_en_kg"]:
                    rinde /= 1000.0

                registros.append({
                    "lote":       lote,
                    "anio":       anio,
                    "rinde_tn_ha": round(rinde, 3),
                    "cultivo":    _normalizar_cultivo(cultivo),
                    "area":       area_name or "desconocido",
                })
            except (ValueError, IndexError):
                errores += 1

    print(f"  Registros parseados : {len(registros):,}")
    if errores:
        print(f"  Errores ignorados   : {errores:,}")

    # Estadísticas
    acc_lote    = defaultdict(list)
    acc_anio    = defaultdict(list)
    acc_cultivo = defaultdict(list)
    for r in registros:
        acc_lote[r["lote"]].append(r["rinde_tn_ha"])
        acc_anio[r["anio"]].append(r["rinde_tn_ha"])
        acc_cultivo[r["cultivo"]].append(r["rinde_tn_ha"])

    estadisticas = {
        "global":      _stats([r["rinde_tn_ha"] for r in registros]),
        "por_lote":    {k: _stats(v) for k, v in sorted(acc_lote.items())},
        "por_anio":    {str(k): _stats(v) for k, v in sorted(acc_anio.items())},
        "por_cultivo": {k: _stats(v) for k, v in sorted(acc_cultivo.items())},
    }

    # Cruzar con baseline nacional si existe
    calibracion = {}
    baseline_path = DATOS_DIR / 'baseline_nacional.json'
    if baseline_path.exists():
        with open(baseline_path, encoding='utf-8') as f:
            bl = json.load(f)
        for cult, s in estadisticas["por_cultivo"].items():
            nac = bl.get("nacional", {}).get(cult)
            if nac and s["media_tn_ha"] and nac["media_tn_ha"]:
                factor = round(s["media_tn_ha"] / nac["media_tn_ha"], 3)
                calibracion[cult] = {
                    "campo_media":    s["media_tn_ha"],
                    "nacional_media": nac["media_tn_ha"],
                    "factor_campo_vs_nacional": factor,
                    "interpretacion": (
                        "campo por encima del promedio nacional" if factor > 1.05
                        else "campo por debajo del promedio nacional" if factor < 0.95
                        else "campo alineado con el promedio nacional"
                    )
                }
        if calibracion:
            print()
            print("  CALIBRACIÓN vs baseline nacional:")
            for cult, c in calibracion.items():
                print(f"  {cult:<10} campo={c['campo_media']:.3f} | nacional={c['nacional_media']:.3f} | factor={c['factor_campo_vs_nacional']} ({c['interpretacion']})")

    # Guardar
    DATOS_DIR.mkdir(exist_ok=True)

    out_proc = DATOS_DIR / 'rinde_procesado.json'
    with open(out_proc, 'w', encoding='utf-8') as f:
        json.dump({
            "_meta": {
                "generado_en": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "total_registros": len(registros),
                "area": area_name,
                "cultivos": sorted(estadisticas["por_cultivo"]),
            },
            "estadisticas": estadisticas,
            "calibracion_nacional": calibracion,
            "registros": registros,
        }, f, indent=2, ensure_ascii=False)

    # Modelo-ready CSV
    out_modelo = DATOS_DIR / 'rinde_modelo_ready.csv'
    modelo_rows = sorted(registros, key=lambda x: (x["lote"], x["anio"]))
    with open(out_modelo, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=[
            "lote_id", "anio", "cultivo", "area", "rinde_tn_ha",
            "ndvi_medio", "sar_medio_db", "indice_fusion", "rinde_estimado"
        ])
        writer.writeheader()
        for r in modelo_rows:
            writer.writerow({
                "lote_id": r["lote"], "anio": r["anio"],
                "cultivo": r["cultivo"], "area": r["area"],
                "rinde_tn_ha": r["rinde_tn_ha"],
                "ndvi_medio": None, "sar_medio_db": None,
                "indice_fusion": None, "rinde_estimado": None,
            })

    g = estadisticas["global"]
    print()
    print(f"  Rinde global : {g['media_tn_ha']} ± {g['std']} t/ha (CV {g['cv_pct']}%)")
    print(f"  Cultivos     : {', '.join(sorted(estadisticas['por_cultivo']))}")

    return {
        "rinde_procesado": str(out_proc),
        "modelo_ready_csv": str(out_modelo),
        "n_registros": len(registros),
        "estadisticas": estadisticas,
        "calibracion": calibracion,
    }


# ══════════════════════════════════════════════════════════════════════════════
# CONSULTA DE BASELINE — para faro_engine.py
# ══════════════════════════════════════════════════════════════════════════════

def get_rinde_ref(cultivo: str, provincia: str = None, departamento: str = None) -> dict:
    """
    Retorna el rinde de referencia histórico para un cultivo y zona.
    Jerarquía: departamento > provincia > nacional > fallback hardcoded.

    Uso desde faro_engine.py:
        from faro_rinde_import import get_rinde_ref
        ref = get_rinde_ref('soja', provincia='Córdoba', departamento='Marcos Juárez')
        rinde_ref = ref['media_tn_ha']  # reemplaza RINDE_REF hardcodeado
    """
    cult_can = _normalizar_cultivo(cultivo)

    # Fallbacks hardcodeados (usados si no hay baseline)
    FALLBACK = {'soja': 2.7, 'maiz': 7.5, 'trigo': 3.2, 'girasol': 2.2}

    # Intentar departamental
    depto_path = DATOS_DIR / 'baseline_departamental.json'
    if departamento and depto_path.exists():
        with open(depto_path, encoding='utf-8') as f:
            bl = json.load(f)
        entry = bl.get("por_departamento", {}).get(departamento, {}).get(cult_can)
        if entry and entry.get("media_tn_ha"):
            return {**entry, "fuente": f"departamento:{departamento}", "cultivo": cult_can}

    # Intentar provincial
    nac_path = DATOS_DIR / 'baseline_nacional.json'
    if provincia and nac_path.exists():
        with open(nac_path, encoding='utf-8') as f:
            bl = json.load(f)
        entry = bl.get("por_provincia", {}).get(provincia, {}).get(cult_can)
        if entry and entry.get("media_tn_ha"):
            return {**entry, "fuente": f"provincia:{provincia}", "cultivo": cult_can}

        # Nacional
        entry = bl.get("nacional", {}).get(cult_can)
        if entry and entry.get("media_tn_ha"):
            return {**entry, "fuente": "nacional", "cultivo": cult_can}

    # Fallback
    return {
        "media_tn_ha": FALLBACK.get(cult_can, 3.0),
        "fuente": "fallback_hardcoded",
        "cultivo": cult_can, "n": 0,
    }


def cmd_baseline_query(provincia: str, cultivo: str, departamento: str = None):
    ref = get_rinde_ref(cultivo, provincia=provincia, departamento=departamento)
    print(f"\nConsulta baseline:")
    print(f"  Cultivo    : {ref['cultivo']}")
    print(f"  Fuente     : {ref['fuente']}")
    print(f"  Media      : {ref['media_tn_ha']} t/ha")
    if ref.get('std'):     print(f"  Std        : {ref['std']} t/ha")
    if ref.get('cv_pct'):  print(f"  CV         : {ref['cv_pct']}%")
    if ref.get('n'):       print(f"  N registros: {ref['n']:,}")
    if ref.get('periodo'): print(f"  Período    : {ref['periodo']}")


# ══════════════════════════════════════════════════════════════════════════════
# DEMO
# ══════════════════════════════════════════════════════════════════════════════

def _generar_demo_csv(path, n=200):
    import random; random.seed(42)
    cultivos = ['soja', 'maiz', 'trigo']
    lotes    = [f"LOTE_{i:03d}" for i in range(1, 21)]
    bases    = {'soja': 3.2, 'maiz': 8.5, 'trigo': 3.8}
    factores = {l: random.uniform(0.80, 1.20) for l in lotes}
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['lote', 'año', 'cultivo', 'rinde_tn_ha'])
        for _ in range(n):
            l = random.choice(lotes); c = random.choice(cultivos)
            r = round(max(0.5, random.gauss(bases[c] * factores[l], bases[c] * 0.12)), 2)
            w.writerow([l, random.randint(2015, 2024), c, r])
    print(f"  Demo CSV generado: {path} ({n} registros)")


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='FARO PROTOCOL — Importador y calibrador de datos de rinde',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--nacional', metavar='CSV',
        help='CSV MAGyP nacional (estimaciones-agricolas-*.csv)')
    group.add_argument('--csv', metavar='CSV',
        help='CSV de campo específico (Basso, etc.)')
    group.add_argument('--demo', action='store_true',
        help='Generar y procesar CSV de demostración')
    group.add_argument('--baseline-query', action='store_true',
        help='Consultar baseline para una provincia/cultivo')

    parser.add_argument('--area',        help='Nombre del área (ej: cordoba)')
    parser.add_argument('--provincia',   help='Para --baseline-query')
    parser.add_argument('--departamento',help='Para --baseline-query (más granular)')
    parser.add_argument('--cultivo',     help='Para --baseline-query')
    parser.add_argument('--verbose', action='store_true')

    args = parser.parse_args()

    print("=" * 60)
    print("  FARO PROTOCOL — Datos de rinde")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    if args.nacional:
        print(f"\nModo: MAGyP Nacional — {Path(args.nacional).name}")
        print()
        salidas = procesar_nacional(args.nacional, verbose=args.verbose)
        print()
        print("  Archivos generados:")
        for k, v in salidas.items():
            if isinstance(v, str):
                print(f"  {k:<28} : {v}")

    elif args.csv:
        print(f"\nModo: Campo específico — {Path(args.csv).name}")
        print()
        procesar_campo(args.csv, area_name=args.area, verbose=args.verbose)

    elif args.demo:
        print("\nModo: Demo")
        DATOS_DIR.mkdir(exist_ok=True)
        demo_path = DATOS_DIR / 'demo_rinde.csv'
        _generar_demo_csv(demo_path)
        procesar_campo(str(demo_path), area_name='demo', verbose=args.verbose)

    elif args.baseline_query:
        if not args.provincia or not args.cultivo:
            print("ERROR: --baseline-query requiere --provincia y --cultivo")
            sys.exit(1)
        cmd_baseline_query(args.provincia, args.cultivo, args.departamento)

    print("\n" + "=" * 60)
