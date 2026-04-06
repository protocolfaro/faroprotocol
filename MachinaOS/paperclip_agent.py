"""
FARO PROTOCOL — Paperclip Agent
Monitor de latido diario: verifica que el pipeline corrió y está OK.
Sin acción si todo está bien — alerta si hay problemas.

Uso:
    python MachinaOS/paperclip_agent.py
    python MachinaOS/paperclip_agent.py --area cordoba
    python MachinaOS/paperclip_agent.py --verbose
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Forzar UTF-8 en stdout/stderr (necesario en Windows con cp1252 por defecto)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

# ── Paths ───────────────────────────────────────────────────────────────────
# El agente vive en MachinaOS/ pero los archivos del pipeline están un nivel arriba
PROJECT_ROOT = Path(__file__).parent.parent
AREAS_DIR    = PROJECT_ROOT / 'faro_areas'

MODEL = "llama-3.3-70b-versatile"
VENTANA_HORAS = 24  # un archivo "de hoy" si fue modificado en las últimas N horas


# ── Helpers ─────────────────────────────────────────────────────────────────

def cargar_areas(solo_area: str = None) -> list[dict]:
    """Carga todas las áreas configuradas (o solo la indicada)."""
    archivos = sorted(AREAS_DIR.glob('*.json'))
    areas = []
    for f in archivos:
        if f.stem == 'areas':
            continue
        if solo_area and f.stem != solo_area:
            continue
        with open(f, encoding='utf-8') as fh:
            areas.append(json.load(fh))
    return areas


def info_archivo(path: Path) -> dict:
    """Retorna metadata de un archivo: existe, tamaño, timestamp, si es reciente."""
    if not path.exists():
        return {"existe": False}

    stat     = path.stat()
    mtime    = datetime.fromtimestamp(stat.st_mtime)
    es_hoy   = mtime >= datetime.now() - timedelta(hours=VENTANA_HORAS)
    tamano   = stat.st_size

    return {
        "existe":    True,
        "tamano_kb": round(tamano / 1024, 1),
        "timestamp": mtime.strftime("%Y-%m-%d %H:%M"),
        "reciente":  es_hoy,
    }


def verificar_sha256(report_png: Path) -> dict:
    """Verifica que el .sha256 exista y coincida con el PNG."""
    hash_file = report_png.with_suffix('.sha256')
    if not hash_file.exists():
        return {"existe": False}

    contenido = hash_file.read_text(encoding='utf-8').strip().split()[0]

    # Recalcular hash real del PNG
    if report_png.exists():
        h = hashlib.sha256()
        with open(report_png, 'rb') as f:
            for bloque in iter(lambda: f.read(65536), b''):
                h.update(bloque)
        hash_real = h.hexdigest()
        valido = (contenido == hash_real)
    else:
        hash_real = None
        valido    = False

    return {
        "existe":    True,
        "hash":      contenido[:16] + "…",
        "valido":    valido,
    }


def auditar_area(area: dict, verbose: bool = False) -> dict:
    """Audita todos los outputs del pipeline para un área."""
    nombre = area['name']
    label  = area['label']

    report_png = PROJECT_ROOT / area['report_png']
    sar_georef = PROJECT_ROOT / area['sar_output']
    ndvi_tif   = PROJECT_ROOT / area['ndvi_tif']

    reporte = info_archivo(report_png)
    sar     = info_archivo(sar_georef)
    ndvi    = info_archivo(ndvi_tif)
    sha     = verificar_sha256(report_png)

    # Determinar estado global del área
    if reporte["existe"] and reporte["reciente"] and sha.get("valido"):
        estado = "OK"
    elif reporte["existe"] and not reporte["reciente"]:
        estado = "DESACTUALIZADO"
    elif reporte["existe"] and not sha.get("valido"):
        estado = "HASH_INVALIDO"
    elif not reporte["existe"] and sar["existe"]:
        estado = "FUSION_PENDIENTE"
    elif not sar["existe"] and not reporte["existe"]:
        estado = "SIN_DATOS"
    else:
        estado = "ADVERTENCIA"

    resultado = {
        "name":     nombre,
        "label":    label,
        "vertical": area["vertical"],
        "estado":   estado,
        "archivos": {
            "reporte_png": reporte,
            "sar_georef":  sar,
            "ndvi_tif":    ndvi,
            "sha256":      sha,
        },
    }

    if verbose:
        _imprimir_area_verbose(resultado)

    return resultado


def _imprimir_area_verbose(r: dict):
    a = r["archivos"]
    estado_color = {"OK": "[OK]", "SIN_DATOS": "[!!]", "DESACTUALIZADO": "[--]", "HASH_INVALIDO": "[!!]"}.get(r["estado"], "[??]")
    print(f"\n  {estado_color} {r['label']} [{r['vertical'].upper()}] - {r['estado']}")
    for nombre_arch, info in a.items():
        if info.get("existe"):
            reciente = "hoy" if info.get("reciente") else "desactualizado"
            extra    = f" | SHA {'OK' if info.get('valido') else 'FAIL'}" if "valido" in info else ""
            print(f"      {nombre_arch:<18} {info['tamano_kb']:>8} KB  {info['timestamp']}  [{reciente}]{extra}")
        else:
            print(f"      {nombre_arch:<18} - no existe")


# ── LLM summary ─────────────────────────────────────────────────────────────

def generar_resumen_llm(telemetria: dict) -> str:
    """
    Envía la telemetría a Groq (llama-3.3-70b) y obtiene un resumen ejecutivo.
    Costo: $0 (plan gratuito de Groq).
    """
    api_key = os.environ.get('GROQ_API_KEY', '')
    if not api_key:
        return (
            "[Paperclip] GROQ_API_KEY no configurada.\n"
            "Obtenela gratis en console.groq.com -> API Keys\n"
            "Agregá al .env: GROQ_API_KEY=gsk_...\n"
            f"Estado raw:\n{json.dumps(telemetria, indent=2, ensure_ascii=False)}"
        )

    try:
        from groq import Groq
        cliente = Groq(api_key=api_key)

        prompt = f"""Sos Paperclip, el agente monitor de FARO PROTOCOL.
Tu rol: resumen diario de latido del pipeline. Sin accion si todo esta OK, alerta clara si hay problemas.
Estilo: terminal premium oscuro, conciso, datos primero. Sin emojis decorativos, solo indicadores funcionales ([OK] [!!] [--]).

Telemetría del pipeline al {telemetria['fecha_hora']}:

{json.dumps(telemetria, indent=2, ensure_ascii=False)}

Generá un reporte de latido con:
1. Estado global (una línea)
2. Estado por área (una línea cada una con los datos clave)
3. Alertas o acciones requeridas (solo si hay problemas — si todo está OK, omitir esta sección)
4. Próximo ciclo recomendado

Máximo 20 líneas. Sin markdown. Sin títulos con #."""

        chat = cliente.chat.completions.create(
            model=MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        return chat.choices[0].message.content

    except Exception as e:
        return (
            f"[Paperclip] Error LLM: {e}\n"
            f"Estado raw:\n{json.dumps(telemetria, indent=2, ensure_ascii=False)}"
        )


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='FARO PROTOCOL — Paperclip Agent')
    parser.add_argument('--area',    help='Auditar solo esta área (ej: cordoba)')
    parser.add_argument('--verbose', action='store_true', help='Mostrar detalle por archivo')
    parser.add_argument('--raw',     action='store_true', help='Solo telemetría JSON, sin LLM')
    args = parser.parse_args()

    # Cargar .env si existe (sin dependencia de python-dotenv)
    env_file = PROJECT_ROOT / '.env'
    if env_file.exists():
        for linea in env_file.read_text(encoding='utf-8').splitlines():
            linea = linea.strip()
            if linea and not linea.startswith('#') and '=' in linea:
                k, _, v = linea.partition('=')
                os.environ.setdefault(k.strip(), v.strip())

    print("=" * 60)
    print("  FARO PROTOCOL  |  Paperclip  |  Monitor de latido")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    areas = cargar_areas(solo_area=args.area)
    if not areas:
        print(f"  ERROR: área '{args.area}' no encontrada en faro_areas/")
        sys.exit(1)

    # Auditar todas las áreas
    resultados = [auditar_area(a, verbose=args.verbose) for a in areas]

    # Calcular estado global
    estados      = [r['estado'] for r in resultados]
    todo_ok      = all(e == 'OK' for e in estados)
    hay_error    = any(e in ('SIN_DATOS', 'HASH_INVALIDO') for e in estados)
    global_estado = "OK" if todo_ok else ("ERROR" if hay_error else "ADVERTENCIA")

    telemetria = {
        "fecha_hora":     datetime.now().strftime("%Y-%m-%d %H:%M"),
        "ventana_horas":  VENTANA_HORAS,
        "estado_global":  global_estado,
        "areas":          resultados,
    }

    if args.raw:
        print(json.dumps(telemetria, indent=2, ensure_ascii=False))
        return

    # Resumen LLM
    print("\nConsultando Paperclip...\n")
    resumen = generar_resumen_llm(telemetria)

    print("-" * 60)
    print(resumen)
    print("-" * 60)
    print()


if __name__ == '__main__':
    main()
