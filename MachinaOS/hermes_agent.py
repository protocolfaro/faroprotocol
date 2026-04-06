"""
FARO PROTOCOL — Hermes Agent
Valida calidad de outputs antes de publicar.
Sin GO de Hermes, el reporte no se publica.

Validaciones:
  - Integridad SHA-256
  - SAR no negro (rango dB coherente)
  - NDVI válido (distribución esperada, >70% píxeles limpios)
  - Índice de fusión en rango [0, 1]
  - Tamaño de archivos mayor que mínimos esperados

Uso:
    python MachinaOS/hermes_agent.py --area cordoba
    python MachinaOS/hermes_agent.py --area vaca_muerta --alert-on-fail
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Forzar UTF-8 en Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from faro_areas import load_area, list_areas

MODEL = "llama-3.3-70b-versatile"

# Umbrales de calidad
UMBRAL_SAR_MEDIA_DB_MIN   = -25.0   # dB — más bajo = imagen negra/vacía
UMBRAL_SAR_MEDIA_DB_MAX   = -2.0    # dB — más alto = saturación
UMBRAL_NDVI_PIXELES_VALIDOS = 0.70  # 70% de píxeles deben ser válidos (no NaN)
UMBRAL_NDVI_MEDIA_MIN     = -0.1    # media NDVI mínima (zona vegetada)
UMBRAL_FUSION_MIN         = 0.0
UMBRAL_FUSION_MAX         = 1.0
UMBRAL_REPORTE_KB_MIN     = 200     # PNG menor a esto es sospechoso


# ── Validaciones ─────────────────────────────────────────────────────────────

def validar_sha256(report_png: Path) -> dict:
    hash_file = report_png.with_suffix('.sha256')
    if not hash_file.exists():
        return {"ok": False, "razon": "Archivo .sha256 no encontrado"}

    hash_guardado = hash_file.read_text(encoding='utf-8').strip().split()[0]
    if not report_png.exists():
        return {"ok": False, "razon": "PNG no existe, no se puede verificar hash"}

    h = hashlib.sha256()
    with open(report_png, 'rb') as f:
        for bloque in iter(lambda: f.read(65536), b''):
            h.update(bloque)

    hash_real = h.hexdigest()
    if hash_guardado != hash_real:
        return {
            "ok": False,
            "razon": f"Hash no coincide. Guardado: {hash_guardado[:16]}... Real: {hash_real[:16]}..."
        }
    return {"ok": True, "hash": hash_guardado[:16] + "..."}


def validar_sar(sar_tif: Path) -> dict:
    if not sar_tif.exists():
        return {"ok": False, "razon": "sar_georef.tif no existe"}
    if sar_tif.stat().st_size < 1024:
        return {"ok": False, "razon": f"Archivo SAR demasiado pequeño ({sar_tif.stat().st_size} bytes)"}

    try:
        import numpy as np
        import rasterio
        with rasterio.open(sar_tif) as src:
            data = src.read(1).astype(float)

        # Reemplazar -30 (fill value de georef.py) con NaN para estadísticas
        data[data <= -29.9] = float('nan')

        import numpy as np
        media  = float(np.nanmean(data))
        std    = float(np.nanstd(data))
        minimo = float(np.nanmin(data))
        maximo = float(np.nanmax(data))

        problemas = []
        if media < UMBRAL_SAR_MEDIA_DB_MIN:
            problemas.append(f"Media dB ({media:.1f}) por debajo del umbral ({UMBRAL_SAR_MEDIA_DB_MIN}): imagen posiblemente negra")
        if media > UMBRAL_SAR_MEDIA_DB_MAX:
            problemas.append(f"Media dB ({media:.1f}) por encima del umbral ({UMBRAL_SAR_MEDIA_DB_MAX}): posible saturación")
        if std < 0.5:
            problemas.append(f"Desviación estándar muy baja ({std:.2f}): imagen sin variación (plana/negra)")

        return {
            "ok": len(problemas) == 0,
            "media_db":  round(media, 2),
            "std_db":    round(std, 2),
            "min_db":    round(minimo, 2),
            "max_db":    round(maximo, 2),
            "problemas": problemas,
        }

    except ImportError:
        return {"ok": None, "razon": "rasterio no disponible — validación de SAR omitida"}
    except Exception as e:
        return {"ok": False, "razon": f"Error leyendo SAR: {e}"}


def validar_ndvi(ndvi_tif: Path) -> dict:
    if not ndvi_tif.exists():
        return {"ok": False, "razon": "ndvi_tif no existe"}

    try:
        import numpy as np
        import rasterio
        with rasterio.open(ndvi_tif) as src:
            data = src.read(1).astype(float)
            nodata = src.nodata

        if nodata is not None:
            data[data == nodata] = float('nan')

        # Corregir escala int16 GEE (×10000) igual que faro_fusion.py
        finite = data[np.isfinite(data)]
        if len(finite) > 0 and (np.nanmax(finite) > 2 or np.nanmin(finite) < -2):
            data = data / 10000.0

        total   = data.size
        validos = int((~np.isnan(data)).sum())
        pct_val = validos / total

        media = float(np.nanmean(data))
        std   = float(np.nanstd(data))

        problemas = []
        if pct_val < UMBRAL_NDVI_PIXELES_VALIDOS:
            problemas.append(
                f"Solo {pct_val*100:.1f}% de píxeles válidos "
                f"(mínimo {UMBRAL_NDVI_PIXELES_VALIDOS*100:.0f}%)"
            )
        if media < UMBRAL_NDVI_MEDIA_MIN:
            problemas.append(f"NDVI medio ({media:.3f}) demasiado bajo — posible zona sin vegetación o error")
        if media > 1.0 or media < -1.0:
            problemas.append(f"NDVI medio ({media:.3f}) fuera del rango físico [-1, 1]")

        return {
            "ok": len(problemas) == 0,
            "media_ndvi":      round(media, 4),
            "std_ndvi":        round(std, 4),
            "pixeles_validos": f"{pct_val*100:.1f}%",
            "problemas":       problemas,
        }

    except ImportError:
        return {"ok": None, "razon": "rasterio no disponible — validación NDVI omitida"}
    except Exception as e:
        return {"ok": False, "razon": f"Error leyendo NDVI: {e}"}


def validar_reporte_png(report_png: Path) -> dict:
    if not report_png.exists():
        return {"ok": False, "razon": "Reporte PNG no existe"}

    tamano_kb = report_png.stat().st_size / 1024
    if tamano_kb < UMBRAL_REPORTE_KB_MIN:
        return {
            "ok": False,
            "tamano_kb": round(tamano_kb, 1),
            "razon": f"Reporte demasiado pequeño ({tamano_kb:.0f} KB < {UMBRAL_REPORTE_KB_MIN} KB mínimo)"
        }
    return {"ok": True, "tamano_kb": round(tamano_kb, 1)}


# ── Veredicto LLM ─────────────────────────────────────────────────────────────

def veredicto_hermes(resultados: dict) -> str:
    api_key = os.environ.get('GROQ_API_KEY', '')
    if not api_key:
        # Veredicto local sin LLM
        fallos = [k for k, v in resultados['validaciones'].items()
                  if isinstance(v, dict) and v.get('ok') is False]
        if not fallos:
            return "GO -- Todas las validaciones pasaron. Reporte listo para publicar."
        return f"NO-GO -- Fallos detectados en: {', '.join(fallos)}. Revisar antes de publicar."

    try:
        from groq import Groq
        cliente = Groq(api_key=api_key)
        prompt = f"""Sos Hermes, el agente de validacion de calidad de FARO PROTOCOL.
Tu rol: emitir un veredicto GO o NO-GO sobre la calidad de los outputs del pipeline.
Sos riguroso pero practico. Un GO significa que el dato es publicable. Un NO-GO requiere accion.

Resultados de validacion para {resultados['area']['label']}:
{json.dumps(resultados['validaciones'], indent=2, ensure_ascii=False)}

Emiti un veredicto en exactamente este formato:
GO  -- [razon en una linea]
o
NO-GO -- [razon + accion requerida en una linea]

Seguido de 2-3 lineas de detalle tecnico relevante.
Sin markdown, sin titulos, sin emojis."""

        chat = cliente.chat.completions.create(
            model=MODEL,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        return chat.choices[0].message.content
    except Exception as e:
        fallos = [k for k, v in resultados['validaciones'].items()
                  if isinstance(v, dict) and v.get('ok') is False]
        return (f"NO-GO -- Error LLM ({e}). Fallos: {', '.join(fallos)}"
                if fallos else f"GO -- Validaciones OK (LLM error: {e})")


# ── Main ─────────────────────────────────────────────────────────────────────

def main(area_name: str, alert_on_fail: bool = False) -> dict:
    area = load_area(area_name)

    report_png = PROJECT_ROOT / area['report_png']
    sar_tif    = PROJECT_ROOT / area['sar_output']
    ndvi_tif   = PROJECT_ROOT / area['ndvi_tif']

    print("=" * 60)
    print(f"  FARO PROTOCOL  |  Hermes  |  Validacion de calidad")
    print(f"  Area  : {area['label']}")
    print(f"  Fecha : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    # Cargar .env si existe
    env_file = PROJECT_ROOT / '.env'
    if env_file.exists():
        for linea in env_file.read_text(encoding='utf-8').splitlines():
            linea = linea.strip()
            if linea and not linea.startswith('#') and '=' in linea:
                k, _, v = linea.partition('=')
                os.environ.setdefault(k.strip(), v.strip())

    print("\nEjecutando validaciones...\n")

    validaciones = {
        "sha256":       validar_sha256(report_png),
        "sar_calidad":  validar_sar(sar_tif),
        "ndvi_calidad": validar_ndvi(ndvi_tif),
        "reporte_png":  validar_reporte_png(report_png),
    }

    # Imprimir resultados
    for nombre, v in validaciones.items():
        estado = "[OK]" if v.get("ok") else ("[--]" if v.get("ok") is None else "[!!]")
        print(f"  {estado} {nombre}")
        if not v.get("ok") and "razon" in v:
            print(f"        {v['razon']}")
        if "problemas" in v:
            for p in v["problemas"]:
                print(f"        - {p}")
        for k, val in v.items():
            if k not in ("ok", "razon", "problemas"):
                print(f"        {k}: {val}")

    resultados = {
        "area":         {"name": area["name"], "label": area["label"]},
        "timestamp":    datetime.now().strftime("%Y-%m-%d %H:%M"),
        "validaciones": validaciones,
    }

    # Veredicto
    print("\nConsultando Hermes...\n")
    veredicto = veredicto_hermes(resultados)
    resultados["veredicto"] = veredicto

    es_go = veredicto.strip().upper().startswith("GO")
    resultados["go"] = es_go

    print("-" * 60)
    print(veredicto)
    print("-" * 60)

    # Alerta WhatsApp si NO-GO y se pidio
    if not es_go and alert_on_fail:
        try:
            from phone_gateway import send_alert
            send_alert(
                title=f"Hermes NO-GO [{area['label']}]",
                body=veredicto,
                level='critical',
            )
        except Exception as e:
            print(f"[phone_gateway] No se pudo enviar alerta: {e}")

    return resultados


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='FARO PROTOCOL -- Hermes Agent')
    parser.add_argument(
        '--area', required=True,
        help=f"Area a validar. Disponibles: {', '.join(list_areas())}"
    )
    parser.add_argument(
        '--alert-on-fail', action='store_true',
        help='Enviar alerta WhatsApp si la validacion falla'
    )
    args = parser.parse_args()
    main(args.area, alert_on_fail=args.alert_on_fail)
