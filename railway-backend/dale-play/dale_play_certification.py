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


# ── Generador PDF ─────────────────────────────────────────────────────────────

def _build_pdf(cert_data: dict, out_path: pathlib.Path) -> None:
    """Genera el PDF de certificación con reportlab + QR."""
    import io as _io
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                    Table, TableStyle, HRFlowable, Image as RLImage)

    # ── QR code ──────────────────────────────────────────────────────────────
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
    WHITE = colors.HexColor("#f2ede4")
    WDIM  = colors.HexColor("#9aa0a8")
    REDL  = colors.HexColor("#e74c3c")
    GRNL  = colors.HexColor("#27ae60")
    YELL  = colors.HexColor("#f0b429")

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("title",
        parent=styles["Normal"],
        fontName="Courier-Bold", fontSize=18,
        textColor=GOLD, spaceAfter=4,
        alignment=1,
    )
    sub_style = ParagraphStyle("sub",
        parent=styles["Normal"],
        fontName="Courier", fontSize=10,
        textColor=WHITE, spaceAfter=2,
        alignment=1,
    )
    section_style = ParagraphStyle("section",
        parent=styles["Normal"],
        fontName="Courier-Bold", fontSize=11,
        textColor=GOLD, spaceBefore=12, spaceAfter=4,
    )
    body_style = ParagraphStyle("body",
        parent=styles["Normal"],
        fontName="Courier", fontSize=9,
        textColor=WHITE, spaceAfter=3,
    )
    alert_style = ParagraphStyle("alert",
        parent=styles["Normal"],
        fontName="Courier-Bold", fontSize=9,
        textColor=REDL, spaceAfter=3,
    )
    ok_style = ParagraphStyle("ok",
        parent=styles["Normal"],
        fontName="Courier-Bold", fontSize=9,
        textColor=GRNL, spaceAfter=3,
    )
    hash_style = ParagraphStyle("hash",
        parent=styles["Normal"],
        fontName="Courier", fontSize=7,
        textColor=WDIM, spaceAfter=2,
        alignment=1,
    )

    story = []

    # Header
    story.append(Paragraph("FARO PROTOCOL", title_style))
    story.append(Paragraph("CERTIFICADO DE AUDITORÍA POST-EVENTO", title_style))
    story.append(Spacer(1, 0.3*cm))
    story.append(HRFlowable(width="100%", thickness=1, color=GOLD))
    story.append(Spacer(1, 0.2*cm))

    # Show info
    story.append(Paragraph(f"Artista: {cert_data['artist']}", sub_style))
    story.append(Paragraph(f"Show: {cert_data['show_id']}  ·  {cert_data['show_date']}", sub_style))
    story.append(Paragraph(f"Venue: {cert_data['venue']}", sub_style))
    story.append(Paragraph(f"Generado: {cert_data['fecha_emision']}", sub_style))
    story.append(Spacer(1, 0.3*cm))

    # Hash SHA-256 + QR
    story.append(Paragraph(f"CERT-SHA256: {cert_data['cert_hash']}", hash_style))
    story.append(Paragraph(f"Verificar en: {_verify_url}", hash_style))
    if _qr_img_data:
        _qr_rl = RLImage(_qr_img_data, width=2.8*cm, height=2.8*cm)
        story.append(Spacer(1, 0.15*cm))
        story.append(_qr_rl)
    story.append(HRFlowable(width="100%", thickness=0.5, color=WDIM))
    story.append(Spacer(1, 0.3*cm))

    # ── FII — Índice Faro de Integridad ──────────────────────────────────────
    _fii_result = cert_data.get("fii") or {}
    _fii_val    = _fii_result.get("fii")
    _fii_sello  = _fii_result.get("sello", "N/D")
    _fii_sem    = _fii_result.get("semaforo", "sin_datos")
    _fii_col    = (GRNL if _fii_sem == "ok"
                   else YELL if _fii_sem == "atencion" else REDL)
    _fii_comps  = _fii_result.get("componentes") or {}

    story.append(Paragraph("ÍNDICE FARO DE INTEGRIDAD (FII)", section_style))
    _fii_display = f"{_fii_val:.1f} / 100" if _fii_val is not None else "N/D"
    fii_style = ParagraphStyle("fii_val",
        parent=styles["Normal"],
        fontName="Courier-Bold", fontSize=22,
        textColor=_fii_col, spaceAfter=3, alignment=1,
    )
    sello_style = ParagraphStyle("sello",
        parent=styles["Normal"],
        fontName="Courier-Bold", fontSize=10,
        textColor=_fii_col, spaceAfter=6, alignment=1,
    )
    story.append(Paragraph(_fii_display, fii_style))
    story.append(Paragraph(f"[ {_fii_sello} ]", sello_style))
    if _fii_comps:
        fii_tbl_data = [["COMPONENTE", "PESO", "SCORE", "ESTADO"]]
        labels = {"ndvi": "NDVI campo (40%)", "egms": "EGMS estructural (35%)",
                  "layout": "Compliance layout (25%)"}
        for k, lbl in labels.items():
            c = _fii_comps.get(k) or {}
            fii_tbl_data.append([
                lbl,
                f"{c.get('peso', 0)}%",
                f"{c.get('score', 0):.1f}",
                c.get("label", "N/D"),
            ])
        ft = Table(fii_tbl_data, colWidths=[6.5*cm, 2*cm, 2.5*cm, 5*cm])
        ft.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, 0),  BG),
            ("TEXTCOLOR",    (0, 0), (-1, 0),  GOLD),
            ("FONTNAME",     (0, 0), (-1, 0),  "Courier-Bold"),
            ("FONTSIZE",     (0, 0), (-1, -1), 8),
            ("FONTNAME",     (0, 1), (-1, -1), "Courier"),
            ("TEXTCOLOR",    (0, 1), (-1, -1), WHITE),
            ("BACKGROUND",   (0, 1), (-1, -1), BG),
            ("GRID",         (0, 0), (-1, -1), 0.4, WDIM),
            ("TOPPADDING",   (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ]))
        story.append(ft)
    story.append(Spacer(1, 0.3*cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=WDIM))
    story.append(Spacer(1, 0.2*cm))

    # NDVI section
    story.append(Paragraph("1. ANÁLISIS DE IMPACTO EN CÉSPED — NDVI Sentinel-2", section_style))
    pre  = cert_data["ndvi_pre"]
    post = cert_data["ndvi_post"]
    delta = cert_data["damage"]["delta_ndvi"]

    ndvi_data = [
        ["Parámetro", "Valor", "Fuente"],
        ["NDVI Pre-Show",  f"{pre:.3f}"  if pre  is not None else "N/D", cert_data["ndvi_pre_fuente"]],
        ["NDVI Post-Show", f"{post:.3f}" if post is not None else "N/D", cert_data["ndvi_post_fuente"]],
        ["Delta NDVI",
         f"{delta:+.3f}" if delta is not None else "N/D",
         "Diferencia satelital"],
        ["Nivel de daño",
         cert_data["damage"]["nivel_dano"].upper(),
         "Clasificación Faro Protocol"],
    ]
    t = Table(ndvi_data, colWidths=[5*cm, 4*cm, 7*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0),  BG),
        ("TEXTCOLOR",    (0, 0), (-1, 0),  GOLD),
        ("FONTNAME",     (0, 0), (-1, 0),  "Courier-Bold"),
        ("FONTSIZE",     (0, 0), (-1, -1), 8),
        ("FONTNAME",     (0, 1), (-1, -1), "Courier"),
        ("TEXTCOLOR",    (0, 1), (-1, -1), WHITE),
        ("BACKGROUND",   (0, 1), (-1, -1), BG),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),   [colors.HexColor("#141c24"), BG]),
        ("GRID",         (0, 0), (-1, -1), 0.4, WDIM),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",   (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.2*cm))

    interp = cert_data["damage"]["interpretacion"]
    interp_style = ok_style if "sin_dano" in cert_data["damage"]["nivel_dano"] else (
        alert_style if cert_data["damage"]["nivel_dano"] in ("severo","moderado") else body_style)
    story.append(Paragraph(f"Interpretación: {interp}", interp_style))
    story.append(Spacer(1, 0.2*cm))

    # Zonas afectadas
    zonas = cert_data["damage"].get("sectores_afectados", [])
    if zonas:
        story.append(Paragraph("2. ZONAS AFECTADAS (desde layout subido)", section_style))
        zona_data = [["Zona", "Posición (m)", "Área (m²)", "Delta NDVI"]]
        for z in zonas:
            zona_data.append([
                z.get("zona", "N/D"),
                f"x={z.get('x_m','N/D')}  y={z.get('y_m','N/D')}",
                f"{z.get('area_m2', 0):.0f}",
                f"{z.get('ndvi_delta',0):+.3f}" if z.get("ndvi_delta") is not None else "N/D",
            ])
        tz = Table(zona_data, colWidths=[4*cm, 5*cm, 3*cm, 4*cm])
        tz.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, 0),  BG),
            ("TEXTCOLOR",    (0, 0), (-1, 0),  GOLD),
            ("FONTNAME",     (0, 0), (-1, 0),  "Courier-Bold"),
            ("FONTSIZE",     (0, 0), (-1, -1), 8),
            ("FONTNAME",     (0, 1), (-1, -1), "Courier"),
            ("TEXTCOLOR",    (0, 1), (-1, -1), WHITE),
            ("BACKGROUND",   (0, 1), (-1, -1), BG),
            ("GRID",         (0, 0), (-1, -1), 0.4, WDIM),
            ("TOPPADDING",   (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ]))
        story.append(tz)
        story.append(Spacer(1, 0.2*cm))

    # Layout planificado
    layout = cert_data.get("layout")
    if layout:
        story.append(Paragraph("3. LAYOUT PLANIFICADO vs IMPACTO REAL", section_style))
        story.append(Paragraph(
            f"Archivo: {layout.get('filename', 'N/D')}  ·  Fuente: {layout.get('fuente', 'N/D')}",
            body_style))
        struct = layout.get("estructuras") or {}
        esc = struct.get("escenario") or {}
        if esc:
            story.append(Paragraph(
                f"Escenario planificado: ancho={esc.get('ancho_m','N/D')}m  "
                f"prof={esc.get('profundidad_m','N/D')}m  alto={esc.get('alto_m','N/D')}m",
                body_style))
        _esc2 = struct.get("escenario") or {}
        area = (struct.get("area_total_m2")
                or ((_esc2.get("ancho_m") or 0) * (_esc2.get("profundidad_m") or 0))
                or 0)
        story.append(Paragraph(f"Área total planificada: {area:.0f} m²", body_style))
        story.append(Spacer(1, 0.2*cm))

    # Footer
    story.append(HRFlowable(width="100%", thickness=0.5, color=WDIM))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(
        "Este certificado fue generado automáticamente por Faro Protocol · Dale Play.",
        hash_style))
    story.append(Paragraph(
        "Los datos satelitales provienen de Copernicus/Sentinel-2 (ESA) y NASA SMAP.",
        hash_style))
    story.append(Paragraph(
        f"Hash de integridad: {cert_data['cert_hash']}",
        hash_style))

    doc.build(story)


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

    # Generar PDF
    CERT_DIR.mkdir(exist_ok=True)
    out_path = CERT_DIR / f"{show_id}_certificado.pdf"
    _build_pdf(cert_data, out_path)

    cert_data["pdf_path"] = str(out_path)
    log.info("Certificado generado → %s", out_path)

    # Persistir en Supabase
    try:
        from dale_play_storage import save_certification
        save_certification(show_id, cert_hash, str(out_path), cert_data)
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
