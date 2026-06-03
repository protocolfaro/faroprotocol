"""
dale_play_certification.py — Certificación post-evento automatizada.
Compara NDVI pre/post show, genera PDF certificado con hash SHA-256.
Output: dale-play/certificados/{show_id}_certificado.pdf
"""
from __future__ import annotations
import hashlib, json, logging, os, pathlib
from datetime import datetime, timezone

log = logging.getLogger(__name__)

CERT_DIR   = pathlib.Path(__file__).parent / "certificados"
SHOWS_DIR  = pathlib.Path(__file__).parent / "shows"
MODELS_DIR = pathlib.Path(__file__).parent / "models"

FARO_SYSTEM_PROMPT = (
    "Actuás como el motor de inteligencia de Faro Protocol, sistema de auditoría técnica "
    "inmutable para estadios. Reglas: "
    "1) Si falta un dato nunca inventes — respondé con nivel_dano=sin_datos o "
    "alerta_de_integridad=baja_confianza. "
    "2) Cuando analizés un layout detectá superposición con zonas de riesgo y generá "
    "alertas específicas con coordenadas. "
    "3) Tu objetivo es prevenir multas, no reportar daños. "
    "4) Tono profesional, técnico y pericial."
)


# ── Fetch NDVI post-show ──────────────────────────────────────────────────────

def _fetch_ndvi_post(lat: float = -34.6379, lon: float = -58.5288,
                     days_offset: int = 3) -> dict:
    """
    Descarga NDVI post-show desde Sentinel-2 via Copernicus Data Space.
    Usa el mismo pipeline satelital existente (dale_play_satellite).
    """
    try:
        from dale_play_satellite import fetch_satellite_baseline
        data = fetch_satellite_baseline()
        return {
            "ndvi":         data.get("ndvi"),
            "fecha":        data.get("fecha") or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "nube_pct":     data.get("nube_pct"),
            "fuente":       data.get("fuente", "Sentinel-2 Copernicus"),
            "estado":       data.get("ndvi_status", {}).get("descripcion", "N/D"),
        }
    except Exception as e:
        log.warning("NDVI post-show fetch failed: %s", e)
        return {"ndvi": None, "fecha": None, "fuente": "sin_datos", "estado": "N/D"}


def _compute_damage(ndvi_pre: float | None, ndvi_post: float | None,
                    layout: dict | None) -> dict:
    """
    Calcula delta de daño por sector y zonas afectadas.
    Si no hay layout real, usa datos del comparativa existente.
    """
    if ndvi_pre is None or ndvi_post is None:
        return {
            "delta_ndvi": None,
            "interpretacion": "N/D — datos satelitales insuficientes",
            "sectores_afectados": [],
            "nivel_dano": "sin_datos",
        }

    delta = round(ndvi_post - ndvi_pre, 3)
    nivel = ("severo" if delta < -0.15
             else "moderado" if delta < -0.08
             else "leve" if delta < -0.03
             else "sin_dano")
    interp = {
        "severo":   f"Daño severo al césped — NDVI cayó {abs(delta):.3f} ({abs(delta)/ndvi_pre*100:.0f}% del valor pre-show)",
        "moderado": f"Daño moderado — NDVI cayó {abs(delta):.3f}. Recuperación estimada 2-3 semanas.",
        "leve":     f"Daño leve — NDVI cayó {abs(delta):.3f}. Recuperación normal.",
        "sin_dano": f"Sin daño detectable — NDVI estable (Δ={delta:+.3f}).",
    }[nivel]

    # Nota estacional bermuda (dormancia otoño/invierno BsAs, abril-agosto)
    if ndvi_post is not None and 0.08 <= ndvi_post <= 0.25:
        _month = datetime.now(timezone.utc).month
        if 4 <= _month <= 8:
            interp += (
                " Nota: El NDVI bajo corresponde a dormancia estacional esperada del"
                " césped bermuda en otoño/invierno en Buenos Aires (abril-agosto)."
                " No indica daño por el evento."
            )

    # Zonas afectadas desde layout
    zonas = []
    if layout:
        struct = layout.get("estructuras") or {}
        esc = struct.get("escenario")
        if esc:
            zonas.append({
                "zona": "Zona escenario",
                "x_m": esc.get("x_m"), "y_m": esc.get("y_m"),
                "area_m2": ((esc.get("ancho_m") or 28) * (esc.get("profundidad_m") or 20)),
                "ndvi_delta": delta,
            })
        for t in (struct.get("torres_lr") or []):
            zonas.append({
                "zona": f"Torre {t.get('id','?')}",
                "x_m": t.get("x_m"), "y_m": t.get("y_m"),
                "area_m2": 4.0,
                "ndvi_delta": delta * 0.4,
            })

    return {
        "delta_ndvi":          delta,
        "interpretacion":      interp,
        "sectores_afectados":  zonas,
        "nivel_dano":          nivel,
    }


def _compute_sar_damage(vv_baseline: "float | None", vv_show: "float | None") -> dict:
    """
    Clasifica daño por compactación a partir del delta de backscatter VV.
    Umbral basado en |ΔVV|: severo ≥3.0 dB, moderado ≥1.5, leve ≥0.5, sin_impacto <0.5.
    El signo informa la dirección del cambio (negativo = descenso de retorno radar).
    """
    if vv_baseline is None or vv_show is None:
        return {
            "delta_vv_db":    None,
            "nivel":          "sin_datos",
            "interpretacion": "N/D — datos SAR insuficientes para clasificar.",
        }
    delta     = round(vv_show - vv_baseline, 2)
    abs_delta = abs(delta)
    nivel = ("severo"     if abs_delta >= 3.0
             else "moderado"   if abs_delta >= 1.5
             else "leve"       if abs_delta >= 0.5
             else "sin_impacto")
    signo_txt = "descenso" if delta < 0 else "aumento"
    interp = {
        "severo":
            f"Cambio severo en backscatter ({signo_txt} de {abs_delta:.2f} dB). "
            "Alteración significativa de la superficie registrada por SAR.",
        "moderado":
            f"Cambio moderado en backscatter ({signo_txt} de {abs_delta:.2f} dB). "
            "Compactación o cambio de humedad detectado sobre el campo.",
        "leve":
            f"Cambio leve ({signo_txt} de {abs_delta:.2f} dB). "
            "Dentro del rango de variación normal con actividad de superficie.",
        "sin_impacto":
            f"Sin cambio significativo (ΔVV = {delta:+.2f} dB). "
            "Campo sin alteraciones detectables por radar.",
    }[nivel]
    return {
        "delta_vv_db":    delta,
        "nivel":          nivel,
        "interpretacion": interp,
    }


# ── Generador PDF ─────────────────────────────────────────────────────────────

def _build_pdf(cert_data: dict, out_path: pathlib.Path) -> None:
    """Genera PDF certificado premium Faro Protocol — diseño oscuro con acento dorado."""
    import io as _io
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm, mm
    from reportlab.lib import colors
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                    Table, TableStyle, HRFlowable,
                                    Image as RLImage, KeepTogether)
    from reportlab.graphics.shapes import Drawing, Rect, Line
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    # ── Paleta ───────────────────────────────────────────────────────────────
    GOLD  = colors.HexColor("#c9a84c")
    BG    = colors.HexColor("#0a0a0a")
    BG2   = colors.HexColor("#111318")
    BG3   = colors.HexColor("#161b22")
    WHITE = colors.HexColor("#f2ede4")
    WDIM  = colors.HexColor("#6e7681")
    GREEN = colors.HexColor("#2ecc71")
    AMBER = colors.HexColor("#f39c12")
    RED   = colors.HexColor("#e74c3c")
    PAGE_W, PAGE_H = A4

    # ── Fonts — Georgia serif (sistema Windows/Linux) ─────────────────────
    _FT = "Helvetica-Bold"   # título
    _FB = "Helvetica"        # cuerpo
    _FM = "Courier"          # mono hash

    _reg: set = set()
    for _fname, _paths in [
        ("Georgia",   [r"C:\Windows\Fonts\georgia.ttf",
                       "/usr/share/fonts/truetype/msttcorefonts/Georgia.ttf"]),
        ("GeorgiaBd", [r"C:\Windows\Fonts\georgiab.ttf",
                       "/usr/share/fonts/truetype/msttcorefonts/Georgia_Bold.ttf"]),
        ("Calibri",   [r"C:\Windows\Fonts\calibri.ttf"]),
        ("CalibriBd", [r"C:\Windows\Fonts\calibrib.ttf"]),
    ]:
        for _p in _paths:
            if pathlib.Path(_p).exists():
                try:
                    pdfmetrics.registerFont(TTFont(_fname, _p))
                    _reg.add(_fname)
                    break
                except Exception:
                    pass

    if "GeorgiaBd" in _reg:
        _FT = "GeorgiaBd"
    elif "Georgia" in _reg:
        _FT = "Georgia"
    if "Calibri" in _reg:
        _FB = "Calibri"
    _FB_BD = "CalibriBd" if "CalibriBd" in _reg else _FT

    # ── QR dorado sobre fondo negro ───────────────────────────────────────
    _verify_url = (
        "https://faroprotocol-production-45fd.up.railway.app"
        f"/dale-play/verify/{cert_data['cert_hash']}"
    )
    _qr_buf = None
    try:
        import qrcode
        qr = qrcode.QRCode(version=2, box_size=7, border=2,
                           error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(_verify_url)
        qr.make(fit=True)
        _pil = qr.make_image(fill_color="#c9a84c", back_color="#0a0a0a")
        _qr_buf = _io.BytesIO()
        _pil.save(_qr_buf, format="PNG")
        _qr_buf.seek(0)
    except Exception as _qe:
        log.warning("QR gen failed: %s", _qe)

    # ── Callback fondo oscuro — se pasa a doc.build() ─────────────────────
    def _bg(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(BG)
        canvas.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
        canvas.setFillColor(GOLD)
        canvas.rect(0, PAGE_H - 4 * mm, PAGE_W, 4 * mm, fill=1, stroke=0)
        canvas.rect(0, 0, PAGE_W, 2 * mm, fill=1, stroke=0)
        canvas.restoreState()

    # ── Fábrica de estilos ────────────────────────────────────────────────
    def _P(name, fn=None, fs=9, tc=WHITE, sa=3, sb=0, align=0):
        return ParagraphStyle(name, fontName=fn or _FB, fontSize=fs,
                               textColor=tc, spaceAfter=sa, spaceBefore=sb,
                               alignment=align, leading=max(fs * 1.35, fs + 3))

    P_brand  = _P("brand",  fn=_FT,   fs=20, tc=GOLD,  sa=1,  align=1)
    P_subbr  = _P("subbr",  fn=_FB,   fs=8,  tc=WHITE, sa=6,  align=1)
    P_sec    = _P("sec",    fn=_FT,   fs=10, tc=GOLD,  sa=5,  sb=8)
    P_body   = _P("body",   fn=_FB,   fs=8.5,tc=WHITE, sa=3)
    P_dim    = _P("dim",    fn=_FB,   fs=7.5,tc=WDIM,  sa=2)
    P_mono   = _P("mono",   fn=_FM,   fs=7,  tc=WDIM,  sa=1,  align=1)
    P_hash   = _P("phash",  fn=_FM,   fs=6,  tc=WDIM,  sa=1,  align=1)
    P_pend   = _P("pend",   fn=_FB_BD,fs=8,  tc=AMBER, sa=3)
    P_ok     = _P("pok",    fn=_FB_BD,fs=8,  tc=GREEN, sa=3)
    P_alert  = _P("palert", fn=_FB_BD,fs=8,  tc=RED,   sa=3)

    # ── Estilo base para tablas ───────────────────────────────────────────
    def _TS(*extra):
        cmds = [
            ("BACKGROUND",    (0, 0), (-1, 0),  BG2),
            ("TEXTCOLOR",     (0, 0), (-1, 0),  GOLD),
            ("FONTNAME",      (0, 0), (-1, 0),  _FT),
            ("FONTSIZE",      (0, 0), (-1, -1), 8),
            ("TOPPADDING",    (0, 0), (-1, 0),  8),
            ("BOTTOMPADDING", (0, 0), (-1, 0),  8),
            ("FONTNAME",      (0, 1), (-1, -1), _FB),
            ("TEXTCOLOR",     (0, 1), (-1, -1), WHITE),
            ("TOPPADDING",    (0, 1), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [BG3, BG2]),
            ("BOX",           (0, 0), (-1, -1), 1,   GOLD),
            ("LINEBELOW",     (0, 0), (-1, 0),  1,   GOLD),
            ("INNERGRID",     (0, 0), (-1, -1), 0.3, WDIM),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ]
        return TableStyle(cmds + list(extra))

    # ── Gauge FII como Drawing ─────────────────────────────────────────────
    def _gauge(val, w=13.4 * cm, h=8 * mm, fill=GOLD):
        d = Drawing(w, h)
        d.add(Rect(0, 0, w, h, fillColor=BG2, strokeColor=WDIM, strokeWidth=0.5))
        if val is not None:
            fw = max(1 * mm, w * (float(val) / 100.0))
            d.add(Rect(0, 0, fw, h, fillColor=fill, strokeColor=None, strokeWidth=0))
        for tick in (0.25, 0.50, 0.75):
            tx = w * tick
            d.add(Line(tx, 0, tx, h * 0.35, strokeColor=BG, strokeWidth=1.2))
        return d

    # ── Document ──────────────────────────────────────────────────────────
    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=1.8 * cm, rightMargin=1.8 * cm,
        topMargin=2.4 * cm, bottomMargin=2 * cm,
    )
    story = []

    # ═══════════════════════════════════════════════════════════════════════
    # HEADER: info izq | QR der
    # ═══════════════════════════════════════════════════════════════════════
    left_cell = [
        Paragraph("FARO PROTOCOL", P_brand),
        Paragraph("CERTIFICADO DE AUDITORÍA POST-EVENTO", P_subbr),
        Paragraph(f"Artista:  {cert_data['artist']}", P_body),
        Paragraph(f"Show:     {cert_data['show_id']}  ·  {cert_data['show_date']}", P_body),
        Paragraph(f"Venue:    {cert_data['venue']}", P_body),
        Paragraph(f"Emitido:  {cert_data['fecha_emision']}", P_dim),
    ]

    if _qr_buf:
        right_cell = [
            RLImage(_qr_buf, width=3.6 * cm, height=3.6 * cm),
            Paragraph("VERIFICAR", _P("qrl", fn=_FM, fs=6, tc=GOLD, sa=0, align=1)),
        ]
    else:
        right_cell = [Paragraph("QR N/D", P_dim)]

    hdr = Table([[left_cell, right_cell]], colWidths=[12 * cm, 4.4 * cm])
    hdr.setStyle(TableStyle([
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN",         (1, 0), (1, 0),   "CENTER"),
        ("BOX",           (1, 0), (1, 0),   1.2, GOLD),
        ("BACKGROUND",    (1, 0), (1, 0),   BG2),
        ("TOPPADDING",    (1, 0), (1, 0),   8),
        ("BOTTOMPADDING", (1, 0), (1, 0),   8),
        ("LEFTPADDING",   (0, 0), (0, 0),   0),
        ("RIGHTPADDING",  (0, 0), (0, 0),   0),
    ]))
    story.append(hdr)
    story.append(Spacer(1, 0.3 * cm))
    story.append(HRFlowable(width="100%", thickness=1, color=GOLD))
    story.append(Spacer(1, 0.35 * cm))

    # ═══════════════════════════════════════════════════════════════════════
    # ESTADO BIFÁSICO — banners de color
    # ═══════════════════════════════════════════════════════════════════════
    cert_estado = cert_data.get("cert_estado", "SAR_CONFIRMADO+NDVI_PENDIENTE")
    sar_ok      = "SAR_CONFIRMADO"  in cert_estado
    ndvi_ok     = "NDVI_CONFIRMADO" in cert_estado

    sar_lbl  = "SAR_CONFIRMADO"  if sar_ok  else "SAR_PENDIENTE"
    ndvi_lbl = "NDVI_CONFIRMADO" if ndvi_ok else "NDVI_PENDIENTE"
    sar_bc   = GREEN if sar_ok  else AMBER
    ndvi_bc  = GREEN if ndvi_ok else AMBER

    badge_tbl = Table(
        [[Paragraph(sar_lbl,  _P("sl", fn=_FT, fs=11, tc=BG, sa=0, align=1)),
          Paragraph(ndvi_lbl, _P("nl", fn=_FT, fs=11, tc=BG, sa=0, align=1))]],
        colWidths=[8.1 * cm, 8.3 * cm],
        rowHeights=[1.05 * cm],
    )
    badge_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (0, 0), sar_bc),
        ("BACKGROUND",    (1, 0), (1, 0), ndvi_bc),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("BOX",           (0, 0), (-1, -1), 1.5, GOLD),
        ("LINEAFTER",     (0, 0), (0, 0),   1,   BG),
    ]))

    story.append(KeepTogether([
        Paragraph("ESTADO DEL CERTIFICADO", P_sec),
        badge_tbl,
        Spacer(1, 0.15 * cm),
        (Paragraph(
            "NDVI óptico pendiente de observación libre de nubes. "
            "El certificado se actualiza automáticamente cuando Sentinel-2 registre "
            "una escena válida sobre el estadio (SLA: 30 días).", P_pend)
         if not ndvi_ok else Spacer(1, 1)),
    ]))
    story.append(Spacer(1, 0.25 * cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=WDIM))
    story.append(Spacer(1, 0.3 * cm))

    # ═══════════════════════════════════════════════════════════════════════
    # SECCIÓN 1 — SAR (métrica primaria)
    # ═══════════════════════════════════════════════════════════════════════
    sar_dmg   = cert_data.get("sar_damage") or {}
    delta_vv  = sar_dmg.get("delta_vv_db")
    sar_nivel = sar_dmg.get("nivel", "sin_datos")
    sar_interp= sar_dmg.get("interpretacion", "N/D")
    sar_base_d= cert_data.get("sar_baseline") or {}
    sar_pre_d = cert_data.get("sar_pre")      or {}
    sar_post_d= cert_data.get("sar_post")     or {}

    vv_base = sar_base_d.get("vv_db")
    vv_show = sar_pre_d.get("vv_db")
    vv_post = sar_post_d.get("vv_db")

    _snc = (RED   if sar_nivel in ("severo", "moderado")
            else AMBER if sar_nivel == "leve"
            else GREEN)

    def _iid(d):
        s = d.get("item_id") or "—"
        return (s[:26] + "…") if len(s) > 26 else s

    sar_rows = [
        ["Medición", "Valor dB", "Fecha", "Escena Sentinel-1 GRD"],
        ["Baseline (−45d a −16d)",
         f"{vv_base:+.2f} dB" if vv_base is not None else "N/D",
         sar_base_d.get("fecha", "—"), _iid(sar_base_d)],
        ["Durante show (−3d a show)",
         f"{vv_show:+.2f} dB" if vv_show is not None else "N/D",
         sar_pre_d.get("fecha", "—"),  _iid(sar_pre_d)],
        ["Post-show",
         f"{vv_post:+.2f} dB" if vv_post is not None else "PENDIENTE",
         sar_post_d.get("fecha", "—"), _iid(sar_post_d)],
        ["ΔVV (durante − baseline)",
         f"{delta_vv:+.2f} dB" if delta_vv is not None else "N/D",
         "—", "Diferencia backscatter radar"],
        ["Nivel de impacto",
         sar_nivel.upper().replace("_", " "),
         "—", "Faro: |ΔVV| ≥ 1.5 dB = moderado  ≥ 3.0 dB = severo"],
    ]
    sar_tbl = Table(sar_rows, colWidths=[4.6 * cm, 2.8 * cm, 2.4 * cm, 6.6 * cm])
    sar_tbl.setStyle(_TS(
        ("TEXTCOLOR", (1, 4), (1, 4), _snc),
        ("TEXTCOLOR", (1, 5), (1, 5), _snc),
        ("FONTNAME",  (1, 5), (1, 5), _FT),
    ))

    _sar_is = (P_alert if sar_nivel in ("severo", "moderado")
               else P_ok if sar_nivel == "sin_impacto" else P_body)

    story.append(KeepTogether([
        Paragraph("1.  SAR SENTINEL-1 GRD — COMPACTACIÓN DE SUELO  (MÉTRICA PRIMARIA)", P_sec),
        sar_tbl,
        Spacer(1, 0.15 * cm),
        Paragraph(f"Interpretación: {sar_interp}", _sar_is),
    ]))
    story.append(Spacer(1, 0.3 * cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=WDIM))
    story.append(Spacer(1, 0.3 * cm))

    # ═══════════════════════════════════════════════════════════════════════
    # FII — Índice Faro de Integridad (gauge visual)
    # ═══════════════════════════════════════════════════════════════════════
    _fii_r    = cert_data.get("fii") or {}
    _fii_val  = _fii_r.get("fii")
    _fii_sell = _fii_r.get("sello", "N/D")
    _fii_sem  = _fii_r.get("semaforo", "sin_datos")
    _fii_fill = GREEN if _fii_sem == "ok" else (AMBER if _fii_sem == "atencion" else RED)
    _fii_comp = _fii_r.get("componentes") or {}

    _fii_str  = f"{_fii_val:.1f}" if _fii_val is not None else "N/D"

    # Score + sello en tabla para evitar superposición
    _score_tbl = Table(
        [[Paragraph(_fii_str, _P("fsn", fn=_FT, fs=34, tc=_fii_fill, sa=0, align=1)),
          Paragraph("/ 100", _P("f100", fn=_FB, fs=13, tc=WDIM, sa=0, align=0)),
          Paragraph(f"[ {_fii_sell} ]",
                    _P("fsl", fn=_FT, fs=10, tc=_fii_fill, sa=0, align=2))]],
        colWidths=[3.6 * cm, 2.0 * cm, 10.8 * cm],
        rowHeights=[1.4 * cm],
    )
    _score_tbl.setStyle(TableStyle([
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("TOPPADDING",    (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))

    fii_block = [
        Paragraph("ÍNDICE FARO DE INTEGRIDAD (FII)", P_sec),
        _score_tbl,
        Spacer(1, 0.2 * cm),
        _gauge(_fii_val, fill=_fii_fill),
        Spacer(1, 2),
        Paragraph(
            "0 ·················· 25 ·················· 50 ·················· 75 ···················· 100",
            _P("gt", fn=_FM, fs=6, tc=WDIM, sa=0)),
    ]

    if _fii_comp:
        fc_rows = [["Componente", "Peso", "Score", "Estado"]]
        for k, lbl in [("ndvi", "NDVI campo"),
                        ("egms", "EGMS estructural"),
                        ("layout", "Layout compliance")]:
            c = _fii_comp.get(k) or {}
            fc_rows.append([lbl, f"{c.get('peso',0)}%",
                            f"{c.get('score',0):.1f}", c.get("label","N/D")])
        fc_tbl = Table(fc_rows, colWidths=[5.5 * cm, 2 * cm, 2.5 * cm, 6.4 * cm])
        fc_tbl.setStyle(_TS())
        fii_block += [Spacer(1, 0.2 * cm), fc_tbl]

    story.append(KeepTogether(fii_block))
    story.append(Spacer(1, 0.3 * cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=WDIM))
    story.append(Spacer(1, 0.3 * cm))

    # ═══════════════════════════════════════════════════════════════════════
    # SECCIÓN 2 — NDVI (métrica secundaria)
    # ═══════════════════════════════════════════════════════════════════════
    ndvi_pre  = cert_data.get("ndvi_pre")
    ndvi_post = cert_data.get("ndvi_post")
    dmg       = cert_data.get("damage") or {}
    delta_nd  = dmg.get("delta_ndvi")
    ndvi_nvl  = dmg.get("nivel_dano", "sin_datos")
    ndvi_int  = dmg.get("interpretacion", "N/D")

    _np_c = AMBER if ndvi_post is None else WHITE
    _nd_c = AMBER if delta_nd  is None else WHITE

    def _src_short(s: str) -> str:
        return s[:70] + "…" if len(s) > 70 else s

    ndvi_rows = [
        ["Medición",        "Valor",                                              "Fuente"],
        ["NDVI Pre-Show",
         f"{ndvi_pre:.3f}"  if ndvi_pre  is not None else "N/D",
         _src_short(cert_data.get("ndvi_pre_fuente","N/D"))],
        ["NDVI Post-Show",
         f"{ndvi_post:.3f}" if ndvi_post is not None else "PENDIENTE",
         _src_short(cert_data.get("ndvi_post_fuente","Pendiente — nubosidad post-show"))],
        ["Delta NDVI",
         f"{delta_nd:+.3f}" if delta_nd is not None else "PENDIENTE",
         "Diferencia reflectancia óptica"],
        ["Nivel daño óptico",
         ndvi_nvl.upper().replace("_", " "),
         "Clasificación Faro Protocol"],
    ]
    ndvi_tbl = Table(ndvi_rows, colWidths=[3.8 * cm, 2.8 * cm, 9.8 * cm])
    ndvi_tbl.setStyle(_TS(
        ("TEXTCOLOR", (1, 2), (1, 2), _np_c),
        ("TEXTCOLOR", (1, 3), (1, 3), _nd_c),
        ("FONTNAME",  (1, 2), (1, 2), _FT if ndvi_post is None else _FB),
        ("FONTNAME",  (1, 3), (1, 3), _FT if delta_nd  is None else _FB),
    ))

    _ndvi_is = (P_ok    if ndvi_nvl == "sin_dano"           else
                P_alert if ndvi_nvl in ("severo","moderado") else P_pend)
    ndvi_note = (
        Paragraph(
            "NDVI post-show PENDIENTE. Sin imagen óptica limpia disponible sobre el estadio. "
            "El delta de daño se calculará automáticamente al recibir la próxima "
            "escena válida de Sentinel-2.", P_pend)
        if not ndvi_ok
        else Paragraph(f"Interpretación: {ndvi_int}", _ndvi_is)
    )

    story.append(KeepTogether([
        Paragraph("2.  NDVI SENTINEL-2 — DAÑO AL CÉSPED  (MÉTRICA SECUNDARIA)", P_sec),
        ndvi_tbl,
        Spacer(1, 0.15 * cm),
        ndvi_note,
    ]))
    story.append(Spacer(1, 0.25 * cm))

    # ═══════════════════════════════════════════════════════════════════════
    # SECCIÓN 3 — Zonas afectadas (opcional)
    # ═══════════════════════════════════════════════════════════════════════
    zonas = dmg.get("sectores_afectados", [])
    if zonas:
        z_rows = [["Zona", "Posición (m)", "Área m²", "Delta NDVI"]]
        for z in zonas:
            z_rows.append([
                z.get("zona", "N/D"),
                f"x={z.get('x_m','?')}  y={z.get('y_m','?')}",
                f"{z.get('area_m2',0):.0f}",
                f"{z.get('ndvi_delta',0):+.3f}" if z.get("ndvi_delta") is not None else "N/D",
            ])
        zt = Table(z_rows, colWidths=[4 * cm, 5 * cm, 2.8 * cm, 4.6 * cm])
        zt.setStyle(_TS())
        story.append(KeepTogether([
            Paragraph("3.  ZONAS AFECTADAS", P_sec),
            zt,
        ]))
        story.append(Spacer(1, 0.25 * cm))

    # ═══════════════════════════════════════════════════════════════════════
    # FOOTER — hash + fuentes
    # ═══════════════════════════════════════════════════════════════════════
    story.append(HRFlowable(width="100%", thickness=1, color=GOLD))
    story.append(Spacer(1, 0.2 * cm))
    story.append(Paragraph(
        "Certificado generado automáticamente por Faro Protocol · Dale Play. "
        "SAR: Copernicus / Sentinel-1 GRD (ESA) via Microsoft Planetary Computer. "
        "Óptico: Copernicus / Sentinel-2 L2A (ESA) via AWS Earth Search (Element84).",
        P_mono))
    story.append(Spacer(1, 0.1 * cm))
    story.append(Paragraph(f"CERT-SHA256: {cert_data['cert_hash']}", P_hash))
    story.append(Paragraph(f"Verificar: {_verify_url}", P_hash))

    doc.build(story, onFirstPage=_bg, onLaterPages=_bg)


# ── Certifier — clase pública ─────────────────────────────────────────────────

class Certifier:
    """
    Genera el certificado PDF post-evento.
    Uso: Certifier().generate(data) → {hash, pdf_path, qr_url}
    data debe contener cert_hash, show_id y todos los campos de cert_data.
    """

    VERIFY_BASE_URL = (
        "https://faroprotocol-production-45fd.up.railway.app/dale-play/verify"
    )

    def generate(self, data: dict) -> dict:
        cert_hash = data["cert_hash"]
        show_id   = data["show_id"]
        qr_url    = f"{self.VERIFY_BASE_URL}/{cert_hash}"

        CERT_DIR.mkdir(exist_ok=True)
        out_path = CERT_DIR / f"{show_id}_certificado.pdf"
        _build_pdf(data, out_path)

        return {
            "hash":     cert_hash,
            "pdf_path": str(out_path),
            "qr_url":   qr_url,
        }


# ── Orquestador público ───────────────────────────────────────────────────────

def run_certification(show_id: str, mode: str = "post_show") -> dict:
    """
    Genera certificado post-evento para show_id.
    Retorna dict con ruta del PDF y todos los datos del certificado.
    """
    if mode != "post_show":
        raise ValueError(f"mode debe ser 'post_show', recibido: {mode!r}")

    # Cargar show config
    show_path = SHOWS_DIR / f"{show_id}.json"
    if not show_path.exists():
        raise FileNotFoundError(f"Show config no encontrado: {show_id}")
    show_cfg = json.loads(show_path.read_text(encoding="utf-8"))

    # Cargar layout si existe
    layout_path = SHOWS_DIR / f"{show_id}_layout.json"
    layout = (json.loads(layout_path.read_text(encoding="utf-8"))
              if layout_path.exists() else None)

    # NDVI pre-show — Supabase primero, luego fallbacks
    ndvi_pre   = None
    ndvi_pre_f = "N/D"
    try:
        from dale_play_storage import get_show_baseline
        _baseline = get_show_baseline(show_id)
        if _baseline and _baseline.get("ndvi") is not None:
            ndvi_pre   = _baseline["ndvi"]
            ndvi_pre_f = (_baseline.get("satellite") or {}).get(
                "fuente", "Supabase / Sentinel-2 Copernicus")
            log.info("certification: ndvi_pre=%s desde Supabase", ndvi_pre)
    except Exception as _se:
        log.warning("certification: Supabase baseline error: %s", _se)

    if ndvi_pre is None:
        sat_path = MODELS_DIR / "satellite_baseline.json"
        if sat_path.exists():
            _sat = json.loads(sat_path.read_text(encoding="utf-8"))
            ndvi_pre   = _sat.get("ndvi")
            ndvi_pre_f = _sat.get("fuente", "Sentinel-2 Copernicus")
    if ndvi_pre is None:
        ndvi_pre   = show_cfg.get("rider", {}).get("ndvi_pre")
        ndvi_pre_f = "rider_config"

    # NDVI post-show (fetch real)
    post_data   = _fetch_ndvi_post()
    ndvi_post   = post_data.get("ndvi")
    ndvi_post_f = post_data.get("fuente", "Sentinel-2 Copernicus")

    # Daño
    damage = _compute_damage(ndvi_pre, ndvi_post, layout)

    # Hash SHA-256 del certificado
    ts      = datetime.now(timezone.utc)
    content = (f"FARO-CERT-{show_id}-"
               f"{ndvi_pre}-{ndvi_post}-"
               f"{damage['delta_ndvi']}-"
               f"{ts.isoformat()}")
    cert_hash = hashlib.sha256(content.encode()).hexdigest().upper()

    cert_data = {
        "show_id":       show_id,
        "show_date":     show_cfg.get("show_date", ""),
        "artist":        show_cfg.get("artist", ""),
        "venue":         show_cfg.get("venue", "Estadio José Amalfitani"),
        "fecha_emision": ts.strftime("%Y-%m-%d %H:%M UTC"),
        "cert_hash":     cert_hash,
        "ndvi_pre":      ndvi_pre,
        "ndvi_pre_fuente": ndvi_pre_f,
        "ndvi_post":     ndvi_post,
        "ndvi_post_fuente": ndvi_post_f,
        "damage":        damage,
        "layout":        layout,
        "mode":          mode,
        "fii":           {},
    }

    # FII — Índice Faro de Integridad
    try:
        from dale_play_fii import compute_fii
        _egms_path_fii = MODELS_DIR / "egms_amalfitani.json"
        _egms_fii      = (json.loads(_egms_path_fii.read_text(encoding="utf-8"))
                          if _egms_path_fii.exists() else None)
        _dr_path_fii   = MODELS_DIR / "drainage_amalfitani.json"
        _dr_fii        = (json.loads(_dr_path_fii.read_text(encoding="utf-8"))
                          if _dr_path_fii.exists() else None)
        cert_data["fii"] = compute_fii(ndvi_post, _egms_fii, layout, _dr_fii)
        log.info("certification FII=%.1f sello=%s",
                 cert_data["fii"].get("fii", 0),
                 cert_data["fii"].get("sello", "N/D"))
    except Exception as _fii_e:
        log.warning("FII compute error: %s", _fii_e)

    # Generar PDF via Certifier
    _cert_result = Certifier().generate(cert_data)
    cert_data["pdf_path"] = _cert_result["pdf_path"]
    log.info("Certificado generado → %s", _cert_result["pdf_path"])

    # Persistir en Supabase
    try:
        from dale_play_storage import save_certification
        save_certification(show_id, cert_hash, _cert_result["pdf_path"], cert_data)
    except Exception as _se:
        log.warning("certification: Supabase save error: %s", _se)

    return cert_data


if __name__ == "__main__":
    import sys
    sid = sys.argv[1] if len(sys.argv) > 1 else "airbag_2026-05-31"
    r = run_certification(sid)
    print(f"Certificado: {r['pdf_path']}")
    print(f"Hash: {r['cert_hash']}")
    print(f"NDVI pre={r['ndvi_pre']}  post={r['ndvi_post']}  delta={r['damage']['delta_ndvi']}")
    print(f"Nivel daño: {r['damage']['nivel_dano']}")
    fii = r.get("fii") or {}
    print(f"FII={fii.get('fii','N/D')}  sello={fii.get('sello','N/D')}  semaforo={fii.get('semaforo','N/D')}")
