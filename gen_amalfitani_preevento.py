"""
gen_amalfitani_preevento.py
Faro Protocol — Análisis Pre-Evento Amalfitani
Fusión: Vision 5 fotos Roger 27/05/2026 + Baseline Sentinel-2 17/05/2026
"""
import warnings; warnings.filterwarnings("ignore")

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.patches import FancyBboxPatch, Circle
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
from PIL import Image
import pathlib, hashlib
from datetime import datetime

# ── PALETA FARO ───────────────────────────────────────────────────────────────
BG    = "#0a0a0a"
BG2   = "#0d1117"
BG3   = "#141c24"
GOLD  = "#c9a84c"
GOLDL = "#e2c97e"
WHITE = "#f2ede4"
WDIM  = "#9aa0a8"
BORDER= "#1e2a38"
RED   = "#c0392b"
AMBER = "#d4a017"
GREEN = "#27ae60"
DPI   = 150

NOW   = datetime.utcnow()
CERT  = hashlib.sha256(f"FARO-ALF-PRE-{NOW.isoformat()}".encode()).hexdigest()[:20].upper()
FECHA = NOW.strftime("%d/%m/%Y %H:%M UTC")

ROOT = pathlib.Path(__file__).parent

# ── FOTOS DE ROGER ────────────────────────────────────────────────────────────
FOTOS = {
    "Sur · Frente Escenario": ROOT / "WhatsApp Image 2026-05-27 at 15.00.25.jpeg",
    "Norte":                   ROOT / "WhatsApp Image 2026-05-27 at 15.00.25 (1).jpeg",
    "Centro · General":        ROOT / "WhatsApp Image 2026-05-27 at 15.00.26.jpeg",
    "Lateral Izquierdo":       ROOT / "WhatsApp Image 2026-05-27 at 15.00.26 (1).jpeg",
    "Primer plano césped":     ROOT / "WhatsApp Image 2026-05-27 at 15.00.27.jpeg",
}

# ── ANÁLISIS VISION (Claude) + FUSIÓN NDVI BASELINE (S2C 17/05) ──────────────
# Baseline NDVI Sentinel-2 L2A S2C 17/05/2026 (filtro vegetación >0.15)
SECTORES = [
    {
        "nombre":    "Norte",
        "ndvi":       0.182,
        "ndvi_std":   0.071,
        "sem":        "verde",
        "vision": (
            "Césped verde uniforme, alta densidad, sin equipos ni material de producción. "
            "Tribuna popular visible al fondo. Estado óptimo pre-evento."
        ),
        "riesgo_post": "Bajo — sector sin infraestructura de producción. "
                       "Tráfico de personal moderado durante el evento.",
        "riesgo_nivel": "bajo",
        "vision_match": "positivo",   # visual coincide con NDVI
    },
    {
        "nombre":    "Centro",
        "ndvi":       0.237,
        "ndvi_std":   0.116,
        "sem":        "amarillo",
        "vision": (
            "Campo despejado, sin material pesado sobre el pasto. "
            "Rigs de audio suspendidos en estructuras laterales (sin contacto con césped). "
            "Algunas variaciones de tono → posible diferencia de humedad entre zonas."
        ),
        "riesgo_post": "Medio — zona de mayor tráfico peatonal durante show. "
                       "Compactación esperada pero recuperable.",
        "riesgo_nivel": "medio",
        "vision_match": "neutro",
    },
    {
        "nombre":    "Sur",
        "ndvi":       0.199,
        "ndvi_std":   0.103,
        "sem":        "rojo",
        "vision": (
            "Escenario ya montado: dos pantallas gigantes, andamios, luces. "
            "Presión de carga sobre el extremo Sur del campo. "
            "Líneas de corte visibles → mantenimiento reciente antes del montaje."
        ),
        "riesgo_post": "Alto — carga estructural del escenario sobre el pasto. "
                       "Compactación severa y posible daño de raíces esperado.",
        "riesgo_nivel": "alto",
        "vision_match": "negativo",   # visual peor que lo que indica el NDVI solo
    },
    {
        "nombre":    "Lateral Izq.",
        "ndvi":       None,           # sin NDVI específico de este sector en baseline
        "ndvi_std":   None,
        "sem":        "rojo",
        "vision": (
            "Material de producción apilado en el borde del campo: vallas, "
            "estructuras metálicas. Equipos pesados muy próximos al límite del pasto. "
            "Riesgo de compactación en el perímetro lateral izquierdo."
        ),
        "riesgo_post": "Alto — peso de material sobre banda lateral. "
                       "Posible daño de capa superficial y zona radicular.",
        "riesgo_nivel": "alto",
        "vision_match": "negativo",
    },
    {
        "nombre":    "Frente Escenario",
        "ndvi":       0.199,
        "ndvi_std":   0.052,
        "sem":        "rojo",
        "vision": (
            "Zona directamente bajo el escenario. Andamiaje, base de estructura "
            "y pasarelas técnicas ya instaladas. Pasto aún visible pero bajo presión "
            "máxima. Es la zona de mayor riesgo de daño irreversible."
        ),
        "riesgo_post": "Crítico — daño severo esperado. Compactación extrema, "
                       "pérdida de cobertura vegetal y posible daño de suelo.",
        "riesgo_nivel": "critico",
        "vision_match": "negativo",
    },
]

ACCIONES = [
    ("URGENTE — post 24h",  "rojo",
     "Frente Escenario: resembrado + aireación profunda en zona base del escenario."),
    ("URGENTE — post 24h",  "rojo",
     "Lateral Izquierdo: retirar material, inspección de compactación, riego intensivo."),
    ("esta semana",          "amarillo",
     "Sur: aireación + riego 30 min/sector por 5 días consecutivos."),
    ("esta semana",          "amarillo",
     "Centro: monitoreo diario de NDVI en campo. Riego preventivo si déficit hídrico."),
    ("próx. pasada S2",      "verde",
     "Captura satelital post-evento ~01/06 (S2B). Comparar NDVI vs baseline 17/05."),
    ("próx. pasada S1",      "verde",
     "SAR post-evento ~04/06 (S1A IW). Evaluar compactación por backscatter VV/VH."),
]

SEM_COLOR = {"verde": GREEN, "amarillo": AMBER, "rojo": RED, "critico": "#8b0000"}
SEM_LABEL = {"verde": "OK", "amarillo": "ATENCIÓN", "rojo": "RIESGO", "critico": "CRÍTICO"}

# ── FIGURA ────────────────────────────────────────────────────────────────────
fig = plt.figure(figsize=(16, 11), facecolor=BG)

gs = gridspec.GridSpec(
    3, 3,
    figure=fig,
    left=0.03, right=0.97, top=0.895, bottom=0.07,
    hspace=0.42, wspace=0.28,
)

# ── HEADER ────────────────────────────────────────────────────────────────────
ax_hdr = fig.add_axes([0, 0.912, 1, 0.088])
ax_hdr.set_facecolor(BG3); ax_hdr.axis("off")
ax_hdr.set_xlim(0, 1); ax_hdr.set_ylim(0, 1)
ax_hdr.plot([0, 1], [0.06, 0.06], color=GOLD, lw=2.5)
ax_hdr.text(0.5, 0.76,
    "FARO PROTOCOL  ·  AMALFITANI  ·  Análisis Pre-Evento  ·  27/05/2026",
    color=GOLD, fontsize=13, fontweight="bold", ha="center", va="center",
    fontfamily="monospace")
ax_hdr.text(0.5, 0.28,
    "Vision: 5 fotos Roger Bernal (15:00 hs)  ·  Fusionado con Sentinel-2 L2A S2C 17/05/2026",
    color=WHITE, fontsize=8.5, ha="center", va="center")
ax_hdr.text(0.985, 0.55, f"#{CERT}",
    color=WDIM, fontsize=6, fontfamily="monospace", ha="right", va="center")

# ── PANEL FOTOS (columna izquierda, 3 filas) ─────────────────────────────────
foto_keys = [
    "Sur · Frente Escenario",
    "Norte",
    "Lateral Izquierdo",
]
for fi, key in enumerate(foto_keys):
    ax_f = fig.add_subplot(gs[fi, 0])
    ax_f.set_facecolor(BG2)
    path = FOTOS[key]
    try:
        img = Image.open(path)
        img.thumbnail((400, 400))
        ax_f.imshow(np.array(img), aspect="auto")
    except Exception as e:
        ax_f.text(0.5, 0.5, f"[foto]\n{key}", color=WDIM, ha="center", va="center",
                  fontsize=7, transform=ax_f.transAxes)
    ax_f.set_title(key, color=GOLDL, fontsize=7, fontfamily="monospace", pad=3)
    ax_f.axis("off")
    for sp in ax_f.spines.values():
        sp.set_edgecolor(BORDER); sp.set_linewidth(0.8)

# ── ANÁLISIS SECTORIAL (columna centro + derecha, filas 0-1) ─────────────────
ax_sec = fig.add_subplot(gs[0:2, 1:])
ax_sec.set_facecolor(BG2); ax_sec.axis("off")
ax_sec.set_xlim(0, 1); ax_sec.set_ylim(0, 1)
ax_sec.set_title("Análisis por Sector — Vision + NDVI Baseline",
                 color=GOLD, fontsize=8.5, fontfamily="monospace", pad=5)
for sp in ax_sec.spines.values():
    sp.set_edgecolor(BORDER); sp.set_linewidth(0.8)

def _wrap(text, max_chars):
    """Parte texto en líneas de max_chars caracteres respetando palabras."""
    words = text.split()
    lines, cur, n = [], [], 0
    for w in words:
        if n + len(w) + (1 if cur else 0) > max_chars and cur:
            lines.append(" ".join(cur))
            cur, n = [], 0
        cur.append(w)
        n += len(w) + (1 if len(cur) > 1 else 0)
    if cur:
        lines.append(" ".join(cur))
    return lines

# Anchos de columna calibrados para evitar overflow:
#   SECTOR 0.02–0.15 | ESTADO 0.16–0.30 | NDVI 0.31–0.43
#   ANÁLISIS VISUAL 0.44–0.68 | RIESGO POST 0.70–0.99
cols_x   = [0.02, 0.16, 0.31, 0.44, 0.70]
col_hdrs = ["SECTOR", "ESTADO", "NDVI BASE", "ANÁLISIS VISUAL · VISION", "RIESGO POST"]
for x, h in zip(cols_x, col_hdrs):
    ax_sec.text(x, 0.96, h, color=GOLD, fontsize=6.5, fontfamily="monospace",
                fontweight="bold", va="top")
ax_sec.plot([0.01, 0.99], [0.925, 0.925], color=GOLD + "55", lw=0.7)

# Línea divisoria vertical entre ANÁLISIS VISUAL y RIESGO POST
ax_sec.plot([0.685, 0.685], [0.01, 0.92], color=BORDER, lw=0.6, linestyle=":")

row_h = 0.155
LINE_V = 0.033   # espaciado vertical entre líneas de texto

for i, s in enumerate(SECTORES):
    y = 0.89 - i * row_h
    sc = s["sem"]
    col = SEM_COLOR[sc]

    # Borde lateral de color por semáforo
    ax_sec.add_patch(plt.Rectangle((0.01, y - row_h + 0.01), 0.003, row_h - 0.015,
                                   facecolor=col, transform=ax_sec.transAxes, clip_on=False))

    # Nombre sector
    ax_sec.text(cols_x[0] + 0.005, y - 0.01, s["nombre"],
                color=WHITE, fontsize=7, fontfamily="monospace",
                fontweight="bold", va="top")

    # Semáforo pill
    pill = FancyBboxPatch((cols_x[1], y - 0.06), 0.115, 0.045,
                           boxstyle="round,pad=0.008",
                           facecolor=col + "33", edgecolor=col, lw=1.0,
                           transform=ax_sec.transAxes)
    ax_sec.add_patch(pill)
    ax_sec.text(cols_x[1] + 0.058, y - 0.038, SEM_LABEL[sc],
                color=col, fontsize=6.5, fontfamily="monospace",
                fontweight="bold", ha="center", va="center")

    # NDVI
    if s["ndvi"] is not None:
        ndvi_color = GREEN if s["ndvi"] >= 0.25 else AMBER if s["ndvi"] >= 0.15 else RED
        ax_sec.text(cols_x[2], y - 0.01, f"{s['ndvi']:.3f}",
                    color=ndvi_color, fontsize=8.5, fontfamily="monospace",
                    fontweight="bold", va="top")
        ax_sec.text(cols_x[2], y - 0.055, f"±{s['ndvi_std']:.3f}",
                    color=WDIM, fontsize=6, fontfamily="monospace", va="top")
        ax_sec.text(cols_x[2], y - 0.085, "S2C 17/05",
                    color=WDIM + "aa", fontsize=5.5, fontfamily="monospace", va="top",
                    style="italic")
    else:
        ax_sec.text(cols_x[2], y - 0.01, "N/D",
                    color=WDIM, fontsize=8, fontfamily="monospace", va="top")
        ax_sec.text(cols_x[2], y - 0.045, "sin sector\nen baseline",
                    color=WDIM + "88", fontsize=5.5, fontfamily="monospace", va="top",
                    style="italic")

    # Análisis visual — wrap a 32 chars, cabe en columna 0.44–0.68
    for li, ln in enumerate(_wrap(s["vision"], 32)[:3]):
        clr = WHITE if li < 2 else WDIM
        ax_sec.text(cols_x[3], y - 0.008 - li * LINE_V, ln,
                    color=clr, fontsize=6.2, va="top")

    # RIESGO POST — match symbol + texto wrapeado a 28 chars
    match_sym = {"positivo": "▲ coherente", "neutro": "◆ neutro", "negativo": "▼ alerta"}
    match_col = {"positivo": GREEN, "neutro": AMBER, "negativo": RED}
    vm = s.get("vision_match", "neutro")
    ax_sec.text(cols_x[4], y - 0.008, match_sym[vm],
                color=match_col[vm], fontsize=6.3, fontfamily="monospace",
                fontweight="bold", va="top")
    for li, ln in enumerate(_wrap(s["riesgo_post"], 28)[:3]):
        ax_sec.text(cols_x[4], y - 0.008 - (li + 1) * LINE_V, ln,
                    color=WDIM, fontsize=5.8, va="top")

    # Separador
    if i < len(SECTORES) - 1:
        ax_sec.plot([0.01, 0.99], [y - row_h + 0.015, y - row_h + 0.015],
                    color=BORDER, lw=0.5, linestyle=":")

# ── PANEL ACCIONES RECOMENDADAS (fila 2, columna 1-2) ─────────────────────────
ax_acc = fig.add_subplot(gs[2, 1:])
ax_acc.set_facecolor(BG2); ax_acc.axis("off")
ax_acc.set_xlim(0, 1); ax_acc.set_ylim(0, 1)
ax_acc.set_title("Acciones Recomendadas Post-Evento",
                 color=GOLD, fontsize=8.5, fontfamily="monospace", pad=5)
for sp in ax_acc.spines.values():
    sp.set_edgecolor(BORDER); sp.set_linewidth(0.8)

col_w = [0.17, 0.83]
ax_acc.text(0.01, 0.95, "PLAZO", color=GOLD, fontsize=6.5,
            fontfamily="monospace", fontweight="bold", va="top")
ax_acc.text(0.19, 0.95, "ACCIÓN", color=GOLD, fontsize=6.5,
            fontfamily="monospace", fontweight="bold", va="top")
ax_acc.plot([0, 1], [0.88, 0.88], color=GOLD + "55", lw=0.6)

for i, (plazo, nivel, texto) in enumerate(ACCIONES):
    y = 0.82 - i * 0.145
    col = SEM_COLOR.get(nivel, WDIM)
    # Pill plazo
    pill = FancyBboxPatch((0.01, y - 0.05), 0.155, 0.06,
                           boxstyle="round,pad=0.006",
                           facecolor=col + "22", edgecolor=col, lw=0.9,
                           transform=ax_acc.transAxes)
    ax_acc.add_patch(pill)
    ax_acc.text(0.088, y - 0.02, plazo, color=col, fontsize=6,
                fontfamily="monospace", fontweight="bold", ha="center", va="center")
    ax_acc.text(0.19, y - 0.005, texto, color=WHITE, fontsize=6.8, va="top")

# ── PANEL FOTO EXTRA (fila 2, columna 0) — primer plano cesped ───────────────
ax_fp = fig.add_subplot(gs[2, 0])
ax_fp.set_facecolor(BG2)
path_fp = FOTOS["Primer plano césped"]
try:
    img_fp = Image.open(path_fp)
    img_fp.thumbnail((400, 400))
    ax_fp.imshow(np.array(img_fp), aspect="auto")
except Exception:
    ax_fp.text(0.5, 0.5, "[primer plano]", color=WDIM, ha="center", va="center",
               fontsize=7, transform=ax_fp.transAxes)
ax_fp.set_title("Primer plano · textura césped", color=GOLDL, fontsize=7,
                fontfamily="monospace", pad=3)
ax_fp.axis("off")
for sp in ax_fp.spines.values():
    sp.set_edgecolor(BORDER); sp.set_linewidth(0.8)

# Anotación sobre la foto
ax_fp.text(0.5, 0.06,
    "Verde · buena densidad · manchas leves visibles",
    color=WHITE, fontsize=6, ha="center", va="bottom",
    fontfamily="monospace", transform=ax_fp.transAxes,
    bbox=dict(boxstyle="round,pad=0.2", facecolor=BG + "cc", edgecolor="none"))

# ── FOOTER ────────────────────────────────────────────────────────────────────
ax_ftr = fig.add_axes([0, 0, 1, 0.062])
ax_ftr.set_facecolor(BG3); ax_ftr.axis("off")
ax_ftr.set_xlim(0, 1); ax_ftr.set_ylim(0, 1)
ax_ftr.plot([0, 1], [0.90, 0.90], color=BORDER, lw=0.5)
ax_ftr.text(0.015, 0.58,
    "Fuentes: Sentinel-2 L2A S2C 17/05/2026 (baseline)  ·  Inspección visual 27/05/2026 (fotos Roger Bernal, 15:00 hs)  ·  Faro Protocol",
    color=WDIM, fontsize=6, fontfamily="monospace", va="center")
ax_ftr.text(0.015, 0.22,
    f"Coord. campo: Lat −34.6379  Lon −58.5288  ·  WGS-84  ·  Generado: {FECHA}",
    color=WDIM, fontsize=6, fontfamily="monospace", va="center")
ax_ftr.text(0.985, 0.40, f"© Faro Protocol {NOW.year}",
    color=WDIM, fontsize=6, fontfamily="monospace", ha="right", va="center")

# ── SAVE ──────────────────────────────────────────────────────────────────────
out = ROOT / "reportes_velez" / "faro_reporte_amalfitani_preevento_27052026.png"
out.parent.mkdir(exist_ok=True)
fig.savefig(str(out), dpi=DPI, facecolor=BG, bbox_inches="tight", pad_inches=0.04)
plt.close(fig)
print(f"Guardado: {out}")
