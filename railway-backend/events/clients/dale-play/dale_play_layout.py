"""
dale_play_layout.py — Parseo de rider/layout (PDF, DXF, SDE) + Claude Vision.
Extrae posiciones reales de estructuras del evento sobre el campo.
SDE (System Design Exchange) — estándar L-Acoustics/d&b 2026 — soportado.
Output: shows/{show_id}_layout.json
"""
from __future__ import annotations
import base64, io, json, logging, os, pathlib, xml.etree.ElementTree as _ET
from datetime import datetime

log = logging.getLogger(__name__)

SHOWS_DIR = pathlib.Path(__file__).parent / "shows"
CAMPO     = {"largo_m": 105, "ancho_m": 68}

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


def _img_b64(img_bytes: bytes) -> str:
    return base64.standard_b64encode(img_bytes).decode()


def _pdf_to_image_bytes(pdf_bytes: bytes) -> bytes | None:
    """Renderiza la primera página del PDF a PNG."""
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            page = pdf.pages[0]
            img  = page.to_image(resolution=150)
            buf  = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
    except Exception as e:
        log.warning("PDF render: %s", e)
        return None


def _dxf_to_image_bytes(dxf_bytes: bytes) -> bytes | None:
    """Renderiza entidades DXF a PNG usando ezdxf + matplotlib."""
    try:
        import ezdxf
        from ezdxf.addons.drawing import RenderContext, Frontend
        from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        doc = ezdxf.read(io.BytesIO(dxf_bytes))
        msp = doc.modelspace()
        fig = plt.figure(figsize=(12, 8))
        ax  = fig.add_axes([0, 0, 1, 1])
        ctx = RenderContext(doc)
        out = MatplotlibBackend(ax)
        Frontend(ctx, out).draw_layout(msp, finalize=True)
        buf = io.BytesIO()
        fig.savefig(buf, format="PNG", dpi=150, bbox_inches="tight")
        plt.close(fig)
        return buf.getvalue()
    except Exception as e:
        log.warning("DXF render: %s", e)
        return None


def _extract_pdf_geometry(pdf_bytes: bytes) -> dict:
    """Extrae rectángulos, líneas y texto con coordenadas."""
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            page = pdf.pages[0]
            rects = [
                {"x0": r["x0"], "y0": r["top"], "x1": r["x1"], "y1": r["bottom"],
                 "w": r["width"], "h": r["height"]}
                for r in (page.rects or [])
            ]
            words = [
                {"text": w["text"], "x": w["x0"], "y": w["top"]}
                for w in (page.extract_words() or [])
            ]
            lines = [
                {"x0": l["x0"], "y0": l["top"], "x1": l["x1"], "y1": l["bottom"]}
                for l in (page.lines or [])
            ]
            return {
                "page_width":  float(page.width),
                "page_height": float(page.height),
                "rects":  rects[:50],
                "lines":  lines[:50],
                "words":  words[:100],
            }
    except Exception as e:
        log.warning("PDF geometry: %s", e)
        return {}


def _extract_dxf_geometry(dxf_bytes: bytes) -> dict:
    """Extrae entidades geométricas del DXF con coordenadas."""
    try:
        import ezdxf
        doc      = ezdxf.read(io.BytesIO(dxf_bytes))
        msp      = doc.modelspace()
        entities = []
        for e in msp:
            etype = e.dxftype()
            try:
                if etype == "LINE":
                    entities.append({
                        "type":  "line",
                        "start": list(e.dxf.start)[:2],
                        "end":   list(e.dxf.end)[:2],
                        "layer": e.dxf.layer,
                    })
                elif etype in ("LWPOLYLINE", "POLYLINE"):
                    pts = [(p[0], p[1]) for p in e.get_points()]
                    entities.append({
                        "type":   "polyline",
                        "points": pts[:20],
                        "layer":  e.dxf.layer,
                        "closed": bool(getattr(e.dxf, "closed", False)),
                    })
                elif etype == "INSERT":
                    entities.append({
                        "type":   "block_insert",
                        "name":   e.dxf.name,
                        "insert": list(e.dxf.insert)[:2],
                        "layer":  e.dxf.layer,
                    })
                elif etype == "TEXT":
                    entities.append({
                        "type":   "text",
                        "text":   e.dxf.text,
                        "insert": list(e.dxf.insert)[:2],
                        "layer":  e.dxf.layer,
                    })
                elif etype == "MTEXT":
                    entities.append({
                        "type":   "mtext",
                        "text":   e.text[:100],
                        "insert": list(e.dxf.insert)[:2],
                        "layer":  e.dxf.layer,
                    })
            except Exception:
                pass
        return {"entities": entities[:200]}
    except Exception as e:
        log.warning("DXF geometry: %s", e)
        return {}


def _vision_analyze(img_bytes: bytes | None, geometry: dict, file_type: str) -> dict:
    """Claude Vision extrae posiciones de estructuras desde el plano."""
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    geo_str = json.dumps(geometry, ensure_ascii=False)[:2000]
    prompt = f"""Sos un ingeniero de producción analizando un plano técnico de rider/layout para un show en el Estadio José Amalfitani.
Campo: 105m largo × 68m ancho. Escenario en sector SUR. Origen (0,0) en centro del campo. Eje Y positivo = Norte.

Datos geométricos extraídos del {file_type.upper()}:
{geo_str}

Extraé en metros desde el centro del campo:
1. Escenario: x (E/O), y (N/S), ancho, profundidad, alto estimado
2. Torres de sonido L y R: x e y de cada una
3. Pantallas LED: x e y (si están indicadas)
4. Barricada/pit: y desde centro, ancho
5. Área total afectada m²

Si un valor no es determinable → null.

SOLO JSON válido:
{{
  "escenario": {{"x_m": null, "y_m": null, "ancho_m": null, "profundidad_m": null, "alto_m": null}},
  "torres_lr": [{{"id": "L", "x_m": null, "y_m": null}}, {{"id": "R", "x_m": null, "y_m": null}}],
  "pantallas_led": [],
  "barricada": {{"y_m": null, "ancho_m": null}},
  "area_total_m2": null,
  "observaciones": [],
  "confianza": "alta|media|baja"
}}"""

    content = [{"type": "text", "text": prompt}]
    if img_bytes:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": _img_b64(img_bytes)},
        })
    content.append({"type": "text", "text": "JSON:"})

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        system=FARO_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    )
    raw   = msg.content[0].text.strip()
    start = raw.find("{")
    end   = raw.rfind("}") + 1
    return json.loads(raw[start:end])


# ── SDE (System Design Exchange) parser ──────────────────────────────────────
# Estándar XML de L-Acoustics (K-series, Kara, Kiva) y d&b audiotechnik 2026.
# El schema SDE incluye: arrays de cabinets, posición 3D, ángulo, potencia W, modelo.

def _is_sde_xml(xml_bytes: bytes) -> bool:
    """Detecta si el XML tiene estructura SDE (SystemDesign root o namespace SDE)."""
    try:
        root = _ET.fromstring(xml_bytes)
        tag = root.tag.lower()
        # Acepta: <SystemDesign>, <SDE>, <ArrayProcessing>, xmlns con sde
        if any(k in tag for k in ("systemdesign", "sde", "arrayprocessing", "designfile")):
            return True
        # Busca elementos hijos típicos SDE
        child_tags = {c.tag.split("}")[-1].lower() for c in root}
        return bool(child_tags & {"array", "arrays", "speaker", "sources", "rigging"})
    except Exception:
        return False


def _parse_sde_xml(sde_bytes: bytes) -> dict:
    """
    Parsea archivo SDE (System Design Exchange, XML) de L-Acoustics / d&b audiotechnik.
    Extrae: arrays de torres (posición x/y/z, ángulo, potencia W, modelo de sistema).
    Retorna dict en el mismo formato que _vision_analyze para compatibilidad.
    """
    try:
        root = _ET.fromstring(sde_bytes)
    except _ET.ParseError as e:
        return {
            "escenario": None, "torres_lr": [], "pantallas_led": [],
            "barricada": None, "area_total_m2": None,
            "observaciones": [f"SDE parse error: {e}"],
            "confianza": "baja",
            "sde_raw": {},
        }

    def _txt(el, attr: str, default=None):
        if el is None:
            return default
        v = el.get(attr) or el.findtext(attr)
        if v is None:
            # Busca sub-elemento case-insensitive
            for child in el:
                if child.tag.split("}")[-1].lower() == attr.lower():
                    return child.text or child.get("value") or default
        return v or default

    def _float(v, default=0.0) -> float:
        try:
            return float(v) if v is not None else default
        except (ValueError, TypeError):
            return default

    # Namespaces — strip para simplificar búsqueda
    def _strip_ns(tag: str) -> str:
        return tag.split("}")[-1].lower()

    # Buscar todos los arrays/sources/towers
    arrays: list[dict] = []
    for el in root.iter():
        etag = _strip_ns(el.tag)
        if etag in ("array", "source", "speaker", "loudspeakerarray", "cluster"):
            array_id   = el.get("id") or el.get("name") or f"array_{len(arrays)}"
            x_m        = _float(el.get("x") or el.get("xPos") or el.findtext("x"))
            y_m        = _float(el.get("y") or el.get("yPos") or el.findtext("y"))
            z_m        = _float(el.get("z") or el.get("zPos") or el.findtext("z"))
            azimuth    = _float(el.get("azimuth") or el.get("rz") or el.findtext("azimuth"))
            elevation  = _float(el.get("elevation") or el.get("ry") or el.findtext("elevation"))
            power_w    = _float(el.get("power") or el.get("powerW") or el.findtext("power"))
            model      = (el.get("model") or el.get("type") or
                          el.findtext("model") or el.findtext("type") or "unknown")

            # Buscar cabinets hijos
            cabinets: list[dict] = []
            for cab in el:
                ctag = _strip_ns(cab.tag)
                if ctag in ("cabinet", "element", "module", "unit"):
                    cabinets.append({
                        "model":   cab.get("model") or cab.get("type") or model,
                        "power_w": _float(cab.get("power") or cab.get("powerW") or power_w),
                        "angle":   _float(cab.get("angle") or cab.get("splay")),
                    })

            arrays.append({
                "id":         array_id,
                "x_m":        x_m,
                "y_m":        y_m,
                "z_m":        z_m,
                "azimuth_deg": azimuth,
                "elevation_deg": elevation,
                "power_w":    power_w,
                "model":      model,
                "n_cabinets": len(cabinets),
                "cabinets":   cabinets[:8],  # limit for JSON size
            })

    # Intentar identificar torres L / R / Delay según posición o ID
    torres_lr: list[dict] = []
    for arr in arrays:
        aid = arr["id"].lower()
        if any(k in aid for k in ("main_l", "left", "_l", "mhl", "ml")):
            torres_lr.append({"id": "L", "x_m": arr["x_m"], "y_m": arr["y_m"],
                              "z_m": arr["z_m"], "power_w": arr["power_w"],
                              "model": arr["model"], "azimuth_deg": arr["azimuth_deg"]})
        elif any(k in aid for k in ("main_r", "right", "_r", "mhr", "mr")):
            torres_lr.append({"id": "R", "x_m": arr["x_m"], "y_m": arr["y_m"],
                              "z_m": arr["z_m"], "power_w": arr["power_w"],
                              "model": arr["model"], "azimuth_deg": arr["azimuth_deg"]})

    # Si no se identificaron L/R por nombre, tomar primer y segundo array
    if not torres_lr and len(arrays) >= 2:
        for i, lr in enumerate(("L", "R")):
            a = arrays[i]
            torres_lr.append({"id": lr, "x_m": a["x_m"], "y_m": a["y_m"],
                              "z_m": a["z_m"], "power_w": a["power_w"],
                              "model": a["model"], "azimuth_deg": a["azimuth_deg"]})

    # Escenario: buscar elemento stage/palco/scene
    escenario = None
    for el in root.iter():
        if _strip_ns(el.tag) in ("stage", "palco", "scene", "platform"):
            escenario = {
                "x_m":          _float(el.get("x")),
                "y_m":          _float(el.get("y")),
                "ancho_m":      _float(el.get("width") or el.get("w")),
                "profundidad_m": _float(el.get("depth") or el.get("d")),
                "alto_m":       _float(el.get("height") or el.get("h") or el.get("z"), 1.6),
            }
            break

    obs = [f"SDE: {len(arrays)} arrays detectados · {len(torres_lr)} torres L/R identificadas"]
    if arrays:
        obs.append(f"Sistema: {arrays[0]['model']} — potencia total: "
                   f"{sum(a['power_w'] for a in arrays):.0f} W")
    confianza = "alta" if (len(arrays) >= 2 and torres_lr) else ("media" if arrays else "baja")

    return {
        "escenario":      escenario,
        "torres_lr":      torres_lr,
        "pantallas_led":  [],
        "barricada":      None,
        "area_total_m2":  None,
        "observaciones":  obs,
        "confianza":      confianza,
        "sde_arrays":     arrays,
    }


def parse_layout_file(file_bytes: bytes, filename: str, show_id: str) -> dict:
    """
    Parsea PDF, DXF/DWG o SDE/XML, llama a Claude Vision, guarda shows/{show_id}_layout.json.
    SDE: parseo directo sin Vision (datos exactos). PDF/DXF: Vision + geometría.
    """
    ext       = pathlib.Path(filename).suffix.lower()

    if ext == ".pdf":
        file_type = "pdf"
    elif ext in (".dxf", ".dwg"):
        file_type = "dxf"
    elif ext == ".sde":
        file_type = "sde"
    elif ext in (".xml", ".sdx"):
        # XML puede ser SDE — verificar estructura
        file_type = "sde" if _is_sde_xml(file_bytes) else None
    else:
        file_type = None

    if file_type is None:
        raise ValueError(
            f"Formato no soportado: {ext}. "
            "Usar PDF, DXF/DWG, SDE, o XML con formato SDE (L-Acoustics/d&b)."
        )

    if file_type == "sde":
        # SDE: parseo directo — datos exactos del sistema de sonido
        log.info("Layout: SDE detectado — parseo directo (sin Vision)")
        estructuras = _parse_sde_xml(file_bytes)

        # Si hay torres L/R con posición: cruzar con pyroomacoustics
        if estructuras.get("sde_arrays"):
            try:
                from dale_play_acoustic import analyze_acoustic_sightlines
                # Reconstruir rider desde datos SDE
                total_power_w = sum(
                    a.get("power_w", 0) for a in estructuras["sde_arrays"]
                )
                # Convertir potencia total a Lw aproximado: Lw = 10*log10(P/1e-12)
                import math
                lw_approx = 10 * math.log10(max(total_power_w, 1) / 1e-12) if total_power_w > 0 else 130
                lw_approx = min(140, max(110, lw_approx))
                rider_sde = {
                    "stage": {"lw_db": lw_approx, "throws": ["main", "delay"]},
                    "artist": "SDE import",
                }
                ac_result = analyze_acoustic_sightlines(rider_sde, show_id=show_id)
                estructuras["acoustic_sde"] = {
                    "lw_db_calculado":  round(lw_approx, 1),
                    "rt60_s":           ac_result.get("rt60_s"),
                    "spl_promedio_db":  ac_result.get("spl_promedio_db"),
                    "cobertura_optima": ac_result.get("cobertura_optima_pct"),
                    "modelo":           ac_result.get("modelo_acustico"),
                }
                log.info("SDE → pyroomacoustics: Lw=%.1f dB RT60=%.2fs",
                         lw_approx, ac_result.get("rt60_s", 0))
            except Exception as exc:
                log.warning("SDE acoustic cross: %s", exc)

        geometry = {"source": "SDE XML", "n_arrays": len(estructuras.get("sde_arrays", []))}
        fuente   = "SDE (System Design Exchange) · L-Acoustics/d&b 2026 · Faro Protocol"

    elif file_type == "pdf":
        geometry  = _extract_pdf_geometry(file_bytes)
        img_bytes = _pdf_to_image_bytes(file_bytes)
        try:
            estructuras = _vision_analyze(img_bytes, geometry, file_type)
        except Exception as e:
            log.warning("Vision failed: %s", e)
            estructuras = {
                "escenario": None, "torres_lr": [], "pantallas_led": [],
                "barricada": None, "area_total_m2": None,
                "observaciones": [f"Vision no disponible: {e}"],
                "confianza": "baja",
            }
        fuente = "Claude Vision + pdfplumber"

    else:  # dxf/dwg
        geometry  = _extract_dxf_geometry(file_bytes)
        img_bytes = _dxf_to_image_bytes(file_bytes)
        try:
            estructuras = _vision_analyze(img_bytes, geometry, file_type)
        except Exception as e:
            log.warning("Vision failed: %s", e)
            estructuras = {
                "escenario": None, "torres_lr": [], "pantallas_led": [],
                "barricada": None, "area_total_m2": None,
                "observaciones": [f"Vision no disponible: {e}"],
                "confianza": "baja",
            }
        fuente = "Claude Vision + ezdxf"

    result = {
        "show_id":        show_id,
        "filename":       filename,
        "file_type":      file_type,
        "fecha_analisis": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "campo":          CAMPO,
        "estructuras":    estructuras,
        "geometry_raw":   geometry,
        "fuente":         fuente,
    }

    SHOWS_DIR.mkdir(exist_ok=True)
    out = SHOWS_DIR / f"{show_id}_layout.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Layout saved → %s  (file_type=%s)", out, file_type)
    return result


def load_layout(show_id: str) -> dict | None:
    """Carga layout si existe. Retorna None si no hay layout subido."""
    path = SHOWS_DIR / f"{show_id}_layout.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("Layout load failed %s: %s", show_id, e)
        return None
