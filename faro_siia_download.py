"""
FARO PROTOCOL — Descargador de datos históricos SIIA/MAGyP
Descarga, filtra y procesa datos oficiales de rendimiento agrícola para
Balcarce (Buenos Aires) y Marcos Juárez (Córdoba).

Fuente primaria:
    Ministerio de Agricultura, Ganadería y Pesca de Argentina
    Sistema Integrado de Información Agropecuaria (SIIA)
    "Estimaciones Agrícolas" — datos abiertos actualizados mensualmente.
    URL:      https://datos.magyp.gob.ar/dataset/estimaciones-agricolas
    Recurso:  https://datos.magyp.gob.ar/dataset/9e1e77ba-267e-4eaa-a59f-3296e86b5f36/resource/95d066e6-8a0f-4a80-b59d-6f28f88eacd5/download/estimaciones-agricolas-2026-03.csv
    Licencia: CC BY 2.5 AR — https://datos.gob.ar/acerca/licencia

Citación recomendada:
    Ministerio de Agricultura, Ganadería y Pesca de Argentina. (2026).
    Estimaciones Agrícolas — Serie histórica departamental.
    Sistema de Información Integrado Agropecuario (SIIA).
    Buenos Aires, Argentina. https://datos.magyp.gob.ar

Columnas del CSV fuente (MAGyP):
    cultivo, anio, campania, provincia, provincia_id, departamento,
    departamento_id, superficie_sembrada_ha, superficie_cosechada_ha,
    produccion_tm, rendimiento_kgxha

Uso:
    python faro_siia_download.py            # descarga, filtra y corre importador
    python faro_siia_download.py --solo-csv # solo genera el CSV filtrado
    python faro_siia_download.py --verbose  # muestra detalle año a año
"""

import argparse
import csv
import io
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

# Forzar UTF-8 en Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).parent
DATOS_DIR    = PROJECT_ROOT / 'datos'

# ── Configuración ────────────────────────────────────────────────────────────

FUENTE_URL = (
    "https://datos.magyp.gob.ar/dataset/"
    "9e1e77ba-267e-4eaa-a59f-3296e86b5f36/resource/"
    "95d066e6-8a0f-4a80-b59d-6f28f88eacd5/download/"
    "estimaciones-agricolas-2026-03.csv"
)

FUENTE_META = {
    "nombre":    "Estimaciones Agrícolas — MAGyP / SIIA",
    "url":       FUENTE_URL,
    "licencia":  "CC BY 2.5 AR — https://datos.gob.ar/acerca/licencia",
    "citacion": (
        "Ministerio de Agricultura, Ganadería y Pesca de Argentina. (2026). "
        "Estimaciones Agrícolas — Serie histórica departamental. "
        "Sistema de Información Integrado Agropecuario (SIIA). "
        "Buenos Aires, Argentina. https://datos.magyp.gob.ar"
    ),
    "cobertura_temporal": "1969/70 — 2025/26",
    "granularidad":       "departamento/partido",
}

# Departamentos objetivo y sus provincias
DEPARTAMENTOS = {
    "Balcarce":      "Buenos Aires",
    "Marcos Juárez": "Córdoba",
}

# Cultivos objetivo: clave canónica → substrings a buscar (en minúsculas, sin tildes)
CULTIVOS = {
    "soja":  ["soja", "soy"],
    "maiz":  ["maiz", "maíz"],
    "trigo": ["trigo"],
}

# Año mínimo para el dataset (descartamos pre-2000)
ANIO_MIN = 2000


# ── Helpers ──────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    """Normaliza a minúsculas sin tildes."""
    return (s.lower().strip()
            .replace('á','a').replace('é','e').replace('í','i')
            .replace('ó','o').replace('ú','u').replace('ñ','n'))


def _detectar_cultivo(nombre_raw: str) -> str | None:
    n = _norm(nombre_raw)
    for canon, keywords in CULTIVOS.items():
        if any(kw in n for kw in keywords):
            return canon
    return None


def _detectar_departamento(dept_raw: str) -> str | None:
    dept_n = _norm(dept_raw)
    for obj in DEPARTAMENTOS:
        if _norm(obj) == dept_n:
            return obj
    return None


# ── Descarga ─────────────────────────────────────────────────────────────────

def descargar_y_filtrar(url: str, chunk_kb: int = 256) -> list[dict]:
    """
    Descarga el CSV de MAGyP en chunks y filtra en streaming.
    No carga el archivo completo en memoria.
    Retorna lista de registros normalizados.
    """
    print(f"  URL: {url}")
    req = urllib.request.Request(
        url,
        headers={'User-Agent': 'FaroProtocol/1.0 (datos-publicos)'}
    )

    buffer = b''
    registros = []
    header_row = None
    col = {}          # índices de columnas
    lineas_leidas = 0
    bytes_leidos  = 0

    with urllib.request.urlopen(req, timeout=120) as response:
        print("  Conexión establecida. Leyendo en streaming...")

        while True:
            chunk = response.read(chunk_kb * 1024)
            if not chunk:
                break
            buffer += chunk
            bytes_leidos += len(chunk)

            # Procesar líneas completas
            while b'\n' in buffer:
                line_bytes, buffer = buffer.split(b'\n', 1)
                line = line_bytes.decode('utf-8', errors='replace').rstrip('\r')
                lineas_leidas += 1

                if not line.strip():
                    continue

                # Parsear como CSV (los valores pueden estar entre comillas)
                try:
                    fila = next(csv.reader(io.StringIO(line)))
                except StopIteration:
                    continue

                if header_row is None:
                    # Primera línea = cabecera
                    header_row = [c.strip().lower() for c in fila]
                    col = {c: i for i, c in enumerate(header_row)}
                    required = {'cultivo', 'anio', 'departamento', 'rendimiento_kgxha'}
                    missing = required - set(col)
                    if missing:
                        raise ValueError(f"Columnas faltantes en el CSV: {missing}\nEncontradas: {header_row}")
                    print(f"  Columnas OK: {header_row}")
                    continue

                # Filtrar
                if len(fila) <= max(col.values()):
                    continue

                dept_raw    = fila[col['departamento']].strip()
                cultivo_raw = fila[col['cultivo']].strip()
                anio_raw    = fila[col['anio']].strip()
                rend_raw    = fila[col['rendimiento_kgxha']].strip()
                campania    = fila[col.get('campania', col['anio'])].strip() if 'campania' in col else anio_raw
                provincia   = fila[col.get('provincia', 0)].strip() if 'provincia' in col else ''

                dept   = _detectar_departamento(dept_raw)
                cultivo = _detectar_cultivo(cultivo_raw)
                if dept is None or cultivo is None:
                    continue

                try:
                    anio = int(anio_raw)
                except ValueError:
                    continue
                if anio < ANIO_MIN:
                    continue

                if not rend_raw or rend_raw in ('', '-', 'S/D', 'N/A', '0'):
                    continue
                try:
                    rend_kgha = float(rend_raw.replace(',', '.'))
                    if rend_kgha <= 0:
                        continue
                    rend_tnha = round(rend_kgha / 1000.0, 3)
                except ValueError:
                    continue

                registros.append({
                    'lote':           dept,       # departamento como "lote" regional
                    'anio':           anio,
                    'cultivo':        cultivo,
                    'rinde_tn_ha':    rend_tnha,
                    '_provincia':     provincia,
                    '_campania_orig': campania,
                    '_cultivo_orig':  cultivo_raw,
                })

            # Progreso cada 5 MB
            if bytes_leidos % (5 * 1024 * 1024) < chunk_kb * 1024:
                mb = bytes_leidos / 1024 / 1024
                print(f"  ... {mb:.1f} MB leídos, {len(registros)} registros encontrados")

    mb_total = bytes_leidos / 1024 / 1024
    print(f"  Descarga completa: {mb_total:.1f} MB, {lineas_leidas:,} líneas procesadas")
    return registros


# ── Guardado ─────────────────────────────────────────────────────────────────

def guardar_csv(registros: list[dict], path: Path) -> None:
    """
    Guarda CSV en el formato exacto que espera faro_rinde_import.py.
    Columnas: lote, año, cultivo, rinde_tn_ha (+ informativas provincia, campania_orig)
    """
    DATOS_DIR.mkdir(exist_ok=True)

    registros_ord = sorted(registros, key=lambda x: (x['lote'], x['cultivo'], x['anio']))

    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        # La citación de fuente va en siia_fuente.json, no en el CSV
        # (faro_rinde_import.py no soporta líneas de comentario)
        writer.writerow(['lote', 'año', 'cultivo', 'rinde_tn_ha', 'provincia', 'campania_orig'])
        for r in registros_ord:
            writer.writerow([
                r['lote'],
                r['anio'],
                r['cultivo'],
                r['rinde_tn_ha'],
                r.get('_provincia', ''),
                r.get('_campania_orig', ''),
            ])

    print(f"  CSV guardado: {path} ({len(registros)} registros)")


def guardar_metadatos(registros: list[dict], path: Path) -> None:
    por_dept:  dict[str, dict] = {}
    por_cultivo: dict[str, list] = {}

    for r in registros:
        d = r['lote']
        c = r['cultivo']

        if d not in por_dept:
            por_dept[d] = {'cultivos': set(), 'anios': set(), 'n': 0}
        por_dept[d]['cultivos'].add(c)
        por_dept[d]['anios'].add(r['anio'])
        por_dept[d]['n'] += 1

        por_cultivo.setdefault(c, []).append(r['rinde_tn_ha'])

    meta = {
        '_fuente': FUENTE_META,
        'descargado_en': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'total_registros': len(registros),
        'anos_cubiertos': (
            f"{min(r['anio'] for r in registros)}-{max(r['anio'] for r in registros)}"
            if registros else "N/A"
        ),
        'departamentos': {
            dept: {
                'provincia':    DEPARTAMENTOS.get(dept, ''),
                'cultivos':     sorted(v['cultivos']),
                'anos':         f"{min(v['anios'])}-{max(v['anios'])}",
                'n_registros':  v['n'],
            }
            for dept, v in sorted(por_dept.items())
        },
        'por_cultivo': {
            cult: {
                'n':          len(vals),
                'media_tn_ha': round(sum(vals) / len(vals), 3),
                'min_tn_ha':  round(min(vals), 3),
                'max_tn_ha':  round(max(vals), 3),
            }
            for cult, vals in sorted(por_cultivo.items())
        },
    }

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    print(f"  Metadatos: {path}")


# ── Resumen visual ────────────────────────────────────────────────────────────

def imprimir_resumen(registros: list[dict], verbose: bool = False) -> None:
    if not registros:
        print("  (sin registros)")
        return

    from collections import defaultdict
    por_dept_cult: dict[tuple, list] = defaultdict(list)
    for r in registros:
        por_dept_cult[(r['lote'], r['cultivo'])].append((r['anio'], r['rinde_tn_ha']))

    print(f"\n  {'Departamento':<18} {'Cultivo':<8} {'Años':<12} {'Media':>7} {'Min':>7} {'Max':>7}  {'N':>4}")
    print("  " + "-" * 70)
    for (dept, cult), datos in sorted(por_dept_cult.items()):
        vals  = [v for _, v in datos]
        anios = [a for a, _ in datos]
        media = round(sum(vals) / len(vals), 2)
        print(f"  {dept:<18} {cult:<8} {min(anios)}-{max(anios):<6} {media:>6.2f}  {min(vals):>6.2f}  {max(vals):>6.2f}  {len(vals):>4}")

    if verbose:
        print()
        for (dept, cult), datos in sorted(por_dept_cult.items()):
            print(f"\n  {dept} / {cult}:")
            for anio, rinde in sorted(datos):
                print(f"    {anio}  {rinde:.3f} tn/ha")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Faro Protocol — Descargador SIIA/MAGyP')
    parser.add_argument('--solo-csv',  action='store_true',
                        help='Solo genera el CSV, no ejecuta faro_rinde_import.py')
    parser.add_argument('--verbose',   action='store_true',
                        help='Muestra detalle año a año por cultivo')
    args = parser.parse_args()

    print("=" * 65)
    print("  FARO PROTOCOL — Descargador SIIA/MAGyP")
    print(f"  Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 65)
    print(f"\n  Fuente  : {FUENTE_META['nombre']}")
    print(f"  Filtro  : Balcarce (BA) + Marcos Juárez (Cba)")
    print(f"  Cultivos: soja / maíz / trigo  (≥ {ANIO_MIN})")

    print("\n[1/4] Descargando y filtrando datos MAGyP/SIIA...")
    registros = descargar_y_filtrar(FUENTE_URL)

    if not registros:
        print("\n  ERROR: No se encontraron registros para los filtros especificados.")
        print("  Verificar nombres de departamentos en el CSV fuente.")
        sys.exit(1)

    print(f"\n  Total encontrados: {len(registros)} registros")
    imprimir_resumen(registros, verbose=args.verbose)

    print("\n[2/4] Guardando CSV filtrado...")
    csv_path = DATOS_DIR / 'siia_balcarce_mjuarez.csv'
    guardar_csv(registros, csv_path)

    meta_path = DATOS_DIR / 'siia_fuente.json'
    guardar_metadatos(registros, meta_path)

    if args.solo_csv:
        print("\nListo. Para procesar con el importador:")
        print(f"  python faro_rinde_import.py --csv {csv_path} --verbose")
        return

    print("\n[3/4] Ejecutando faro_rinde_import.py...")
    print("-" * 65)

    # Importar y llamar directamente
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "faro_rinde_import",
        PROJECT_ROOT / "faro_rinde_import.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    resultado = mod.main(csv_path=str(csv_path), verbose=args.verbose)

    print("\n[4/4] Resumen final")
    print("=" * 65)
    print(f"  Registros procesados : {resultado['n_registros']}")
    for nombre, path in resultado['salidas'].items():
        print(f"  {nombre:<22}: {path}")
    print(f"\n  Fuente oficial citada:")
    print(f"  {FUENTE_META['citacion']}")
    print("=" * 65)


if __name__ == '__main__':
    main()
