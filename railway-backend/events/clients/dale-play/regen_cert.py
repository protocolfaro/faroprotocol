"""
regen_cert.py — Regenera el PDF del certificado Airbag con el nuevo diseño.
Usa los datos reales del último pipeline run (no re-fetcha satélites).
"""
import hashlib, pathlib, shutil, sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from dale_play_certification import _build_pdf, _compute_damage, _compute_sar_damage

# ── Datos del último pipeline run (2026-06-03) ──────────────────────────────
SAR_BASELINE = {
    "vv_db":   20.91,
    "n_px":    312,
    "fecha":   "2026-05-05",
    "item_id": "S1A_IW_GRDH_1SDV_20260505T232712_20260505T232737",
    "estado":  "ok",
}
SAR_DURANTE = {
    "vv_db":   18.27,
    "n_px":    287,
    "fecha":   "2026-05-29",
    "item_id": "S1A_IW_GRDH_1SDV_20260529T232709_20260529T232734",
    "estado":  "ok",
}
SAR_POST = {
    "vv_db":   20.16,
    "n_px":    298,
    "fecha":   "2026-06-03",
    "item_id": "S1A_IW_GRDH_1SDV_20260603T232711_20260603T232736",
    "estado":  "ok",
}

NDVI_PRE  = 0.179
NDVI_POST = None   # PENDIENTE — nubosidad total post-show

NDVI_PRE_FUENTE = (
    "Sentinel-2 L2A · Element84 Earth Search · AWS COG · "
    "2026-05-12 · n=864/864 px · SCL=[4, 5] · nube=0.0%"
)
NDVI_POST_FUENTE = (
    "Sentinel-2 L2A · Element84 · 2026-06-01 SCL=[] (0 px)  "
    "| 2026-06-03 SCL=[9] (0 px) — nubosidad total"
)

FII_DATA = {
    "fii":        35.8,
    "sello":      "RIESGO OPERATIVO",
    "semaforo":   "alerta",
    "componentes": {
        "ndvi":   {"peso": 40, "score": 22.0, "label": "Césped en dormancia estacional"},
        "egms":   {"peso": 35, "score": 48.0, "label": "Estabilidad estructural nominal"},
        "layout": {"peso": 25, "score": 35.0, "label": "Sin layout cargado — estimado"},
    },
}

# ── Computos ─────────────────────────────────────────────────────────────────
damage     = _compute_damage(NDVI_PRE, NDVI_POST, layout=None)
sar_damage = _compute_sar_damage(SAR_BASELINE["vv_db"], SAR_DURANTE["vv_db"])

ts        = datetime.now(timezone.utc)
content   = (
    f"FARO-CERT-airbag_2026-05-31-"
    f"SAR:{SAR_BASELINE['vv_db']}-{SAR_DURANTE['vv_db']}-"
    f"NDVI:{NDVI_PRE}-{NDVI_POST}-"
    f"{ts.isoformat()}"
)
cert_hash = hashlib.sha256(content.encode()).hexdigest().upper()

cert_data = {
    "show_id":          "airbag_2026-05-31",
    "show_date":        "2026-05-31",
    "artist":           "Airbag",
    "venue":            "Estadio José Amalfitani",
    "fecha_emision":    ts.strftime("%Y-%m-%d %H:%M UTC"),
    "cert_hash":        cert_hash,
    "cert_estado":      "SAR_CONFIRMADO+NDVI_PENDIENTE",
    "ndvi_pre":         NDVI_PRE,
    "ndvi_pre_fuente":  NDVI_PRE_FUENTE,
    "ndvi_post":        NDVI_POST,
    "ndvi_post_fuente": NDVI_POST_FUENTE,
    "damage":           damage,
    "sar_damage":       sar_damage,
    "layout":           None,
    "mode":             "post_show",
    "fii":              FII_DATA,
    "sar_baseline":     SAR_BASELINE,
    "sar_pre":          SAR_DURANTE,
    "sar_post":         SAR_POST,
}

# ── Generar PDF ───────────────────────────────────────────────────────────────
CERT_DIR = pathlib.Path(__file__).parent / "certificados"
CERT_DIR.mkdir(exist_ok=True)
out_path = CERT_DIR / "airbag_2026-05-31_certificado.pdf"

print("Generando PDF...")
_build_pdf(cert_data, out_path)
print(f"PDF generado: {out_path}")

# ── Copiar al escritorio ──────────────────────────────────────────────────────
desktop = pathlib.Path.home() / "Desktop" / "airbag_2026-05-31_certificado.pdf"
shutil.copy2(out_path, desktop)
print(f"Copiado al escritorio: {desktop}")
print(f"Hash SHA-256: {cert_hash}")
print(f"SAR Damage: delta={sar_damage['delta_vv_db']} dB  nivel={sar_damage['nivel']}")
