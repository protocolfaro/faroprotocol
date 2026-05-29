"""
dale_play_layout.py — Parseo de rider/layout (PDF o DXF) + Claude Vision.
Extrae posiciones reales de estructuras del evento sobre el campo.
Output: shows/{show_id}_layout.json
"""
from __future__ import annotations
import base64, io, json, logging, os, pathlib
from datetime import datetime

log = logging.getLogger(__name__)

SHOWS_DIR = pathlib.Path(__file__).parent / "shows"
CAMPO     = {"largo_m": 105, "ancho_m": 68}


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
        messages=[{"role": "user", "content": content}],
    )
    raw   = msg.content[0].text.strip()
    start = raw.find("{")
    end   = raw.rfind("}") + 1
    return json.loads(raw[start:end])


def parse_layout_file(file_bytes: bytes, filename: str, show_id: str) -> dict:
    """
    Parsea PDF o DXF, llama a Claude Vision, guarda shows/{show_id}_layout.json.
    Retorna el dict del layout.
    """
    ext       = pathlib.Path(filename).suffix.lower()
    file_type = "pdf" if ext == ".pdf" else "dxf" if ext in (".dxf", ".dwg") else None

    if file_type is None:
        raise ValueError(f"Formato no soportado: {ext}. Usar PDF o DXF/DWG.")

    if file_type == "pdf":
        geometry  = _extract_pdf_geometry(file_bytes)
        img_bytes = _pdf_to_image_bytes(file_bytes)
    else:
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

    fuente = ("Claude Vision + pdfplumber" if file_type == "pdf"
              else "Claude Vision + ezdxf")
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
    log.info("Layout saved → %s", out)
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
