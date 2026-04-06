"""
FARO PROTOCOL — Generador de cards para LinkedIn / WhatsApp
============================================================

Lee data.json y genera un bloque de texto formateado listo para
copiar y pegar en LinkedIn, Twitter/X, o enviar por WhatsApp.

Uso:
    python faro_card.py                      # card de todas las áreas activas
    python faro_card.py --area cordoba       # solo Córdoba
    python faro_card.py --format whatsapp    # versión compacta para WhatsApp
    python faro_card.py --format linkedin    # versión completa para LinkedIn (default)
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).parent
DATA_JSON    = PROJECT_ROOT / 'data.json'
AUDIT_LOG    = PROJECT_ROOT / 'audit_log.jsonl'


def _cargar_datos(area_filter: str = None) -> list[dict]:
    if not DATA_JSON.exists():
        print("ERROR: data.json no encontrado. Correr faro_engine.py primero.")
        sys.exit(1)

    with open(DATA_JSON, encoding='utf-8') as f:
        d = json.load(f)

    insights  = d.get('insights', {})
    pipeline  = d.get('pipeline', {})
    finance   = d.get('finance', {}).get('business_cases', {})

    areas = []
    for nombre, ins in insights.items():
        if area_filter and nombre != area_filter:
            continue
        pip  = pipeline.get(nombre, {})
        fin  = finance.get(nombre, {})
        areas.append({
            "nombre":       nombre,
            "zona":         ins.get('zona', nombre),
            "vertical":     ins.get('vertical', ''),
            "fecha":        ins.get('fecha') or pip.get('ultimo_run', '—'),
            "ndvi":         pip.get('ndvi_medio'),
            "sar_db":       pip.get('sar_medio_db'),
            "rinde":        ins.get('rinde_estimado_tha'),
            "score":        ins.get('score_faro'),
            "confianza":    ins.get('confianza', ''),
            "estado":       ins.get('estado', ''),
            "alerta":       ins.get('alerta', ''),
            "sello_datos":  ins.get('sello_datos', ''),
            "hash_png":     ins.get('sha256_reporte', pip.get('sha256', '')),
            "roi_x":        fin.get('roi_x'),
            "ahorro_ha":    fin.get('ahorro_total_ha'),
        })

    return areas


def _ultimo_audit(area: str) -> dict:
    if not AUDIT_LOG.exists():
        return {}
    ultima = {}
    with open(AUDIT_LOG, encoding='utf-8') as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                if entry.get('area') == area:
                    ultima = entry
            except json.JSONDecodeError:
                continue
    return ultima


def card_linkedin(area: dict) -> str:
    estado_emoji = "✅" if area['estado'] == 'OK' else "⚠️"
    rinde_s  = f"{area['rinde']:.2f} t/ha" if area['rinde'] else "N/D"
    score_s  = f"{area['score']:.0f}/100" if area['score'] else "N/D"
    ndvi_s   = f"{area['ndvi']:.4f}" if area['ndvi'] else "N/D"
    sar_s    = f"{area['sar_db']:.1f} dB" if area['sar_db'] else "N/D"
    roi_s    = f"{area['roi_x']}x" if area['roi_x'] else ""
    ahorro_s = f"USD {area['ahorro_ha']}/ha" if area['ahorro_ha'] else ""
    sello    = area['sello_datos'][:16] + "…" if area['sello_datos'] else area['hash_png'][:16] + "…"
    fecha    = area['fecha']

    alerta_bloque = ""
    if area['alerta'] and area['estado'] == 'ALERTA':
        alerta_bloque = f"\n⚠️ {area['alerta'][:120]}…\n"

    fin_bloque = ""
    if roi_s:
        fin_bloque = f"\n💰 ROI {roi_s} · Ahorro {ahorro_s}"

    card = f"""📡 FARO PROTOCOL — Reporte Satelital
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{estado_emoji} {area['zona']}
📅 {fecha}

🛰️ DATOS SATELITALES
  · NDVI (vigor vegetativo): {ndvi_s}
  · SAR Sentinel-1 VV:       {sar_s}
  · Score Faro:               {score_s}
  · Rinde estimado:           {rinde_s}
  · Confianza:                {area['confianza']}{alerta_bloque}
🔐 SELLO CRIPTOGRÁFICO
  sha256:{sello}
  Este dato fue sellado antes de cualquier informe oficial.
  No puede ser modificado retroactivamente.{fin_bloque}

#FaroProtocol #Agro #Satelital #SHA256 #DataDriven #AgTech
—
🌐 protocolfaro.github.io/faroprotocol"""

    return card.strip()


def card_whatsapp(area: dict) -> str:
    rinde_s = f"{area['rinde']:.2f} t/ha" if area['rinde'] else "N/D"
    score_s = f"{area['score']:.0f}/100" if area['score'] else "N/D"
    estado  = "✅ OK" if area['estado'] == 'OK' else "⚠️ ALERTA"
    sello   = area['sello_datos'][:16] + "…" if area['sello_datos'] else "—"

    return f"""📡 *FARO PROTOCOL* — {area['zona']}
{area['fecha']} | {estado}

Rinde est: *{rinde_s}* | Score: *{score_s}*
SHA256: `{sello}`

Sellado satelitalmente antes del dato oficial."""


def main():
    parser = argparse.ArgumentParser(description='FARO PROTOCOL — Card generator')
    parser.add_argument('--area',   help='Área específica (ej: cordoba)')
    parser.add_argument('--format', choices=['linkedin', 'whatsapp'], default='linkedin')
    args = parser.parse_args()

    areas = _cargar_datos(args.area)

    if not areas:
        print(f"Sin datos para {'área ' + args.area if args.area else 'ningún área'}.")
        sys.exit(0)

    for a in areas:
        if args.format == 'whatsapp':
            print(card_whatsapp(a))
        else:
            print(card_linkedin(a))

        if len(areas) > 1:
            print("\n" + "─" * 50 + "\n")

    # Guardar en archivo
    out = PROJECT_ROOT / f"faro_card_{args.area or 'all'}_{datetime.now().strftime('%Y%m%d')}.txt"
    with open(out, 'w', encoding='utf-8') as f:
        for a in areas:
            fn = card_whatsapp if args.format == 'whatsapp' else card_linkedin
            f.write(fn(a) + "\n\n")
    print(f"\n→ Guardado: {out.name}")


if __name__ == '__main__':
    main()
