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
    """Genera el PDF de certificación con layout bifásico: SAR primario + NDVI secundario."""
    import io as _io
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                    Table, TableStyle, HRFlowable, Image as RLImage)

    _verify_url = (
        f"https://faroprotocol-production-45fd.up.railway.app"
        f"/dale-play/verify/{cert_data['cert_hash']}"
    )
    _qr_img_data = None
    try:
        import qrcode
        qr = qrcode.QRCode(version=1, box_size=4, border=2)
        qr.add_data(_verify_url)
        qr.make(fit=True)
        _qr_pil = qr.make_image(fill_color="black", back_color="white")
        _qr_buf = _io.BytesIO()
        _qr_pil.save(_qr_buf, format="PNG")
        _qr_buf.seek(0)
        _qr_img_data = _qr_buf
    except Exception as _qe:
        log.warning("QR generation failed: %s", _qe)

    GOLD  = colors.HexColor("#c9a84c")
    BG    = colors.HexColor("#0d1117")
    BG2   = colors.HexColor("#141c24")
    WHITE = colors.HexColor("#f2ede4")
    WDIM  = colors.HexColor("#9aa0a8")
    REDL  = colors.HexColor("#e74c3c")
    GRNL  = colors.HexColor("#27ae60")
    YELL  = colors.HexColor("#f0b429")
    CYAN  = colors.HexColor("#00b4d8")

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm,
    )

    styles = getSampleStyleSheet()

    def _style(name, base="Normal", **kw):
        return ParagraphStyle(name, parent=styles[base], **kw)

    title_s   = _style("title",   fontName="Courier-Bold", fontSize=18, textColor=GOLD, spaceAfter=4, alignment=1)
    sub_s     = _style("sub",     fontName="Courier",      fontSize=10, textColor=WHITE, spaceAfter=2, alignment=1)
    section_s = _style("section", fontName="Courier-Bold", fontSize=11, textColor=GOLD, spaceBefore=12, spaceAfter=4)
    body_s    = _style("body",    fontName="Courier",      fontSize=9,  textColor=WHITE, spaceAfter=3)
    alert_s   = _style("alert",   fontName="Courier-Bold", fontSize=9,  textColor=REDL,  spaceAfter=3)
    ok_s      = _style("ok",      fontName="Courier-Bold", fontSize=9,  textColor=GRNL,  spaceAfter=3)
    hash_s    = _style("hash",    fontName="Courier",      fontSize=7,  textColor=WDIM,  spaceAfter=2, alignment=1)
    pend_s    = _style("pend",    fontName="Courier-Bold", fontSize=9,  textColor=YELL,  spaceAfter=3)
    cyan_s    = _style("cyan",    fontName="Courier-Bold", fontSize=14, textColor=CYAN,  spaceAfter=3, alignment=1)

    story = []

    # ── Header ───────────────────────────────────────────────────────────────
    story.append(Paragraph("FARO PROTOCOL", title_s))
    story.append(Paragraph("CERTIFICADO DE AUDITORÍA POST-EVENTO", title_s))
    story.append(Spacer(1, 0.3*cm))
    story.append(HRFlowable(width="100%", thickness=1, color=GOLD))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(f"Artista: {cert_data['artist']}", sub_s))
    story.append(Paragraph(f"Show: {cert_data['show_id']}  ·  {cert_data['show_date']}", sub_s))
    story.append(Paragraph(f"Venue: {cert_data['venue']}", sub_s))
    story.append(Paragraph(f"Generado: {cert_data['fecha_emision']}", sub_s))
    story.append(Spacer(1, 0.25*cm))
    story.append(Paragraph(f"CERT-SHA256: {cert_data['cert_hash']}", hash_s))
    story.append(Paragraph(f"Verificar en: {_verify_url}", hash_s))
    if _qr_img_data:
        story.append(Spacer(1, 0.1*cm))
        story.append(RLImage(_qr_img_data, width=2.8*cm, height=2.8*cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=WDIM))
    story.append(Spacer(1, 0.3*cm))

    # ── Estado del certificado (bifásico) ─────────────────────────────────────
    cert_estado  = cert_data.get("cert_estado", "SAR_CONFIRMADO+NDVI_PENDIENTE")
    sar_ok       = "SAR_CONFIRMADO"  in cert_estado
    ndvi_ok      = "NDVI_CONFIRMADO" in cert_estado

    sar_lbl      = "SAR_CONFIRMADO"  if sar_ok  else "SAR_PENDIENTE"
    ndvi_lbl     = "NDVI_CONFIRMADO" if ndvi_ok else "NDVI_PENDIENTE"
    sar_badge_c  = GRNL if sar_ok  else YELL
    ndvi_badge_c = GRNL if ndvi_ok else YELL

    story.append(Paragraph("ESTADO DEL CERTIFICADO", section_s))
    estado_tbl = Table(
        [
            ["MÉTRICA",            "ESTADO",      "DISPONIBILIDAD",              "DESCRIPCIÓN"],
            ["SAR Sentinel-1 GRD", sar_lbl,       "48 h post-evento · siempre",  "Compactación por backscatter VV (métrica primaria)"],
            ["NDVI Sentinel-2",    ndvi_lbl,       "SLA 30 días · libre de nubes","Daño al césped por reflectancia óptica (métrica secundaria)"],
        ],
        colWidths=[3.8*cm, 3.8*cm, 4.2*cm, 5.2*cm],
    )
    estado_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0),  BG),
        ("TEXTCOLOR",     (0, 0), (-1, 0),  GOLD),
        ("FONTNAME",      (0, 0), (-1, 0),  "Courier-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 8),
        ("FONTNAME",      (0, 1), (-1, -1), "Courier"),
        ("TEXTCOLOR",     (0, 1), (-1, 1),  WHITE),
        ("TEXTCOLOR",     (1, 1), (1, 1),   sar_badge_c),
        ("TEXTCOLOR",     (1, 2), (1, 2),   ndvi_badge_c),
        ("FONTNAME",      (1, 1), (1, 2),   "Courier-Bold"),
        ("BACKGROUND",    (0, 1), (-1, -1), BG2),
        ("GRID",          (0, 0), (-1, -1), 0.4, WDIM),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(estado_tbl)
    if not ndvi_ok:
        story.append(Spacer(1, 0.1*cm))
        story.append(Paragraph(
            "NDVI optico pendiente de observacion libre de nubes. "
            "El certificado se actualiza automaticamente cuando Sentinel-2 registre "
            "una escena valida sobre el estadio (SLA: 30 dias).",
            pend_s,
        ))
    story.append(Spacer(1, 0.2*cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=WDIM))
    story.append(Spacer(1, 0.2*cm))

    # ── 1. SAR — Métrica primaria ─────────────────────────────────────────────
    story.append(Paragraph("1. SAR SENTINEL-1 GRD — COMPACTACION DE SUELO (METRICA PRIMARIA)", section_s))

    sar_dmg      = cert_data.get("sar_damage") or {}
    delta_vv     = sar_dmg.get("delta_vv_db")
    sar_nivel    = sar_dmg.get("nivel", "sin_datos")
    sar_interp   = sar_dmg.get("interpretacion", "N/D")
    sar_pre_d    = cert_data.get("sar_pre")  or {}
    sar_post_d   = cert_data.get("sar_post") or {}
    sar_base_d   = cert_data.get("sar_baseline") or {}

    vv_base_val  = sar_base_d.get("vv_db")
    vv_show_val  = sar_pre_d.get("vv_db")   # sar_pre = show-night SAR

    sar_tbl_data = [
        ["Parámetro", "Valor", "Fecha", "Fuente"],
        [
            "VV Baseline (pre-show)",
            f"{vv_base_val:+.2f} dB" if vv_base_val is not None else "N/D",
            sar_base_d.get("fecha", "—"),
            f"Sentinel-1 GRD · {sar_base_d.get('item_id', 'N/D')[:20] if sar_base_d.get('item_id') else 'N/D'}",
        ],
        [
            "VV Durante show",
            f"{vv_show_val:+.2f} dB" if vv_show_val is not None else "N/D",
            sar_pre_d.get("fecha", "—"),
            f"Sentinel-1 GRD · {sar_pre_d.get('item_id', 'N/D')[:20] if sar_pre_d.get('item_id') else 'N/D'}",
        ],
        [
            "Delta VV (show - baseline)",
            f"{delta_vv:+.2f} dB" if delta_vv is not None else "N/D",
            "—",
            "Diferencia de backscatter radar",
        ],
        [
            "Nivel de impacto SAR",
            sar_nivel.upper().replace("_", " "),
            "—",
            "Clasificacion Faro Protocol (umbral 1.5 dB)",
        ],
    ]
    sar_tbl = Table(sar_tbl_data, colWidths=[4.5*cm, 3*cm, 2.5*cm, 7*cm])
    _nivel_col = (REDL if sar_nivel in ("severo", "moderado")
                  else YELL if sar_nivel == "leve" else GRNL)
    sar_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0),  BG),
        ("TEXTCOLOR",     (0, 0), (-1, 0),  GOLD),
        ("FONTNAME",      (0, 0), (-1, 0),  "Courier-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 8),
        ("FONTNAME",      (0, 1), (-1, -1), "Courier"),
        ("TEXTCOLOR",     (0, 1), (-1, -1), WHITE),
        ("TEXTCOLOR",     (1, 4), (1, 4),   _nivel_col),
        ("FONTNAME",      (1, 4), (1, 4),   "Courier-Bold"),
        ("BACKGROUND",    (0, 1), (-1, -1), BG2),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [BG2, BG]),
        ("GRID",          (0, 0), (-1, -1), 0.4, WDIM),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(sar_tbl)
    story.append(Spacer(1, 0.15*cm))
    _sar_i_style = (alert_s if sar_nivel in ("severo","moderado")
                    else ok_s if sar_nivel == "sin_impacto" else body_s)
    story.append(Paragraph(f"Interpretacion: {sar_interp}", _sar_i_style))
    story.append(Spacer(1, 0.2*cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=WDIM))
    story.append(Spacer(1, 0.2*cm))

    # ── FII — Índice Faro de Integridad ──────────────────────────────────────
    _fii_result = cert_data.get("fii") or {}
    _fii_val    = _fii_result.get("fii")
    _fii_sello  = _fii_result.get("sello", "N/D")
    _fii_sem    = _fii_result.get("semaforo", "sin_datos")
    _fii_col    = GRNL if _fii_sem == "ok" else YELL if _fii_sem == "atencion" else REDL
    _fii_comps  = _fii_result.get("componentes") or {}

    story.append(Paragraph("INDICE FARO DE INTEGRIDAD (FII)", section_s))
    _fii_display = f"{_fii_val:.1f} / 100" if _fii_val is not None else "N/D"
    story.append(Paragraph(_fii_display, _style("fii_v", fontName="Courier-Bold", fontSize=22, textColor=_fii_col, spaceAfter=3, alignment=1)))
    story.append(Paragraph(f"[ {_fii_sello} ]", _style("sello", fontName="Courier-Bold", fontSize=10, textColor=_fii_col, spaceAfter=6, alignment=1)))
    if _fii_comps:
        fii_tbl_data = [["COMPONENTE", "PESO", "SCORE", "ESTADO"]]
        for k, lbl in {"ndvi": "NDVI campo (40%)", "egms": "EGMS estructural (35%)", "layout": "Compliance layout (25%)"}.items():
            c = _fii_comps.get(k) or {}
            fii_tbl_data.append([lbl, f"{c.get('peso',0)}%", f"{c.get('score',0):.1f}", c.get("label","N/D")])
        ft = Table(fii_tbl_data, colWidths=[6.5*cm, 2*cm, 2.5*cm, 5*cm])
        ft.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,0),  BG),  ("TEXTCOLOR",(0,0),(-1,0), GOLD),
            ("FONTNAME",      (0,0),(-1,0),  "Courier-Bold"), ("FONTSIZE",(0,0),(-1,-1),8),
            ("FONTNAME",      (0,1),(-1,-1), "Courier"),      ("TEXTCOLOR",(0,1),(-1,-1),WHITE),
            ("BACKGROUND",    (0,1),(-1,-1), BG),  ("GRID",(0,0),(-1,-1),0.4,WDIM),
            ("TOPPADDING",    (0,0),(-1,-1), 4),   ("BOTTOMPADDING",(0,0),(-1,-1),4),
        ]))
        story.append(ft)
    story.append(Spacer(1, 0.3*cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=WDIM))
    story.append(Spacer(1, 0.2*cm))

    # ── 2. NDVI — Métrica secundaria ──────────────────────────────────────────
    story.append(Paragraph("2. NDVI SENTINEL-2 — DANO AL CESPED (METRICA SECUNDARIA)", section_s))

    ndvi_pre  = cert_data.get("ndvi_pre")
    ndvi_post = cert_data.get("ndvi_post")
    delta_ndvi = cert_data["damage"]["delta_ndvi"]

    ndvi_tbl_data = [
        ["Parámetro", "Valor", "Fuente"],
        ["NDVI Pre-Show",
         f"{ndvi_pre:.3f}" if ndvi_pre is not None else "N/D",
         cert_data.get("ndvi_pre_fuente", "N/D")],
        ["NDVI Post-Show",
         f"{ndvi_post:.3f}" if ndvi_post is not None else "PENDIENTE",
         cert_data.get("ndvi_post_fuente", "Pendiente — nubosidad post-show")],
        ["Delta NDVI",
         f"{delta_ndvi:+.3f}" if delta_ndvi is not None else "PENDIENTE",
         "Diferencia satelital"],
        ["Nivel de daño optico",
         cert_data["damage"]["nivel_dano"].upper(),
         "Clasificacion Faro Protocol"],
    ]
    _ndvi_post_col = YELL if ndvi_post is None else WHITE
    ndvi_tbl = Table(ndvi_tbl_data, colWidths=[5*cm, 4*cm, 7*cm])
    ndvi_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,0),  BG),  ("TEXTCOLOR",(0,0),(-1,0),GOLD),
        ("FONTNAME",      (0,0),(-1,0),  "Courier-Bold"), ("FONTSIZE",(0,0),(-1,-1),8),
        ("FONTNAME",      (0,1),(-1,-1), "Courier"),      ("TEXTCOLOR",(0,1),(-1,-1),WHITE),
        ("TEXTCOLOR",     (1,2),(1,2),   _ndvi_post_col), ("FONTNAME",(1,2),(1,2),"Courier-Bold"),
        ("TEXTCOLOR",     (1,3),(1,3),   _ndvi_post_col),
        ("BACKGROUND",    (0,1),(-1,-1), BG2), ("ROWBACKGROUNDS",(0,1),(-1,-1),[BG2,BG]),
        ("GRID",          (0,0),(-1,-1), 0.4,WDIM), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("TOPPADDING",    (0,0),(-1,-1), 4),   ("BOTTOMPADDING",(0,0),(-1,-1),4),
    ]))
    story.append(ndvi_tbl)
    story.append(Spacer(1, 0.15*cm))

    ndvi_interp = cert_data["damage"]["interpretacion"]
    if not ndvi_ok:
        story.append(Paragraph(
            "Estado: NDVI post-show PENDIENTE. Sin imagen optica limpia disponible "
            "sobre el estadio desde el evento. El delta de dano se calculara y "
            "actualizara automaticamente al recibir la proxima escena valida de Sentinel-2.",
            pend_s,
        ))
    else:
        _ndvi_i_s = ok_s if "sin_dano" in cert_data["damage"]["nivel_dano"] else (
            alert_s if cert_data["damage"]["nivel_dano"] in ("severo","moderado") else body_s)
        story.append(Paragraph(f"Interpretacion: {ndvi_interp}", _ndvi_i_s))
    story.append(Spacer(1, 0.2*cm))

    # ── 3. Zonas afectadas / Layout ───────────────────────────────────────────
    zonas = cert_data["damage"].get("sectores_afectados", [])
    if zonas:
        story.append(Paragraph("3. ZONAS AFECTADAS (desde layout subido)", section_s))
        zona_data = [["Zona", "Posicion (m)", "Area (m2)", "Delta NDVI"]]
        for z in zonas:
            zona_data.append([
                z.get("zona","N/D"),
                f"x={z.get('x_m','N/D')}  y={z.get('y_m','N/D')}",
                f"{z.get('area_m2',0):.0f}",
                f"{z.get('ndvi_delta',0):+.3f}" if z.get("ndvi_delta") is not None else "N/D",
            ])
        tz = Table(zona_data, colWidths=[4*cm,5*cm,3*cm,4*cm])
        tz.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),BG), ("TEXTCOLOR",(0,0),(-1,0),GOLD),
            ("FONTNAME",(0,0),(-1,0),"Courier-Bold"), ("FONTSIZE",(0,0),(-1,-1),8),
            ("FONTNAME",(0,1),(-1,-1),"Courier"), ("TEXTCOLOR",(0,1),(-1,-1),WHITE),
            ("BACKGROUND",(0,1),(-1,-1),BG2), ("GRID",(0,0),(-1,-1),0.4,WDIM),
            ("TOPPADDING",(0,0),(-1,-1),4), ("BOTTOMPADDING",(0,0),(-1,-1),4),
        ]))
        story.append(tz)
        story.append(Spacer(1, 0.2*cm))

    layout = cert_data.get("layout")
    if layout:
        story.append(Paragraph("4. LAYOUT PLANIFICADO vs IMPACTO REAL", section_s))
        story.append(Paragraph(
            f"Archivo: {layout.get('filename','N/D')}  ·  Fuente: {layout.get('fuente','N/D')}",
            body_s))
        struct = layout.get("estructuras") or {}
        esc    = struct.get("escenario") or {}
        if esc:
            story.append(Paragraph(
                f"Escenario: ancho={esc.get('ancho_m','N/D')}m  "
                f"prof={esc.get('profundidad_m','N/D')}m  alto={esc.get('alto_m','N/D')}m",
                body_s))
        area = (struct.get("area_total_m2")
                or ((esc.get("ancho_m") or 0) * (esc.get("profundidad_m") or 0)) or 0)
        story.append(Paragraph(f"Area total planificada: {area:.0f} m2", body_s))
        story.append(Spacer(1, 0.2*cm))

    # ── Footer ────────────────────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.5, color=WDIM))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(
        "Este certificado fue generado automaticamente por Faro Protocol · Dale Play.",
        hash_s))
    story.append(Paragraph(
        "Datos SAR: Copernicus/Sentinel-1 GRD (ESA) via Microsoft Planetary Computer.",
        hash_s))
    story.append(Paragraph(
        "Datos opticos: Copernicus/Sentinel-2 L2A (ESA) via AWS Earth Search (Element84).",
        hash_s))
    story.append(Paragraph(f"Hash de integridad: {cert_data['cert_hash']}", hash_s))

    doc.build(story)


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
