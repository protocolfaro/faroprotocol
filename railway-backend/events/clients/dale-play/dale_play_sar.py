"""
dale_play_sar.py — Cascada SAR con redundancia de aviación: nunca null, siempre confianza documentada.

Cascada completa (A → B → C → D → E):
  A. Umbra Open Data       X-band  0.26m   CC-BY-4.0          confianza: 0.95 (directo) / 0.25 (proxy)
  B. Sentinel-1 RTC/PC     C-band  20m     SAS token libre     confianza: 0.80 (directo) / 0.35 (proxy)
  C. Capella Open Data     X-band  0.50m   CC-BY-4.0          confianza: 0.90 (directo) / 0.20 (proxy)
  D. ALOS-2 ASF search     L-band  25m     ASF public API     confianza: 0.15 (metadata only)
  E. EGMS proxy            C-band  20m     Copernicus hist.   confianza: 0.10 (estimado siempre)

Lógica de cascada:
  - Prueba A → B → C en orden.
  - Si una fuente retorna confianza >= CONFIDENCE_THRESHOLD_STOP (0.70), para.
  - Si la mejor confianza es < 0.70, continúa buscando una mejor.
  - Si ninguna da pixels, usa D (metadata) y E (proxy) como complemento.
  - Nunca retorna null. Siempre incluye cascade_log y sar_nivel_confianza.

Cada entrada en cascade_log:
  {fuente, intentado, resultado, razon, tiempo_ms, confianza_obtenida}
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date, timedelta
from typing import Optional
from urllib.parse import quote

log = logging.getLogger(__name__)

# ── Configuración del venue ────────────────────────────────────────────────────
_VENUE_LAT  = -34.6379
_VENUE_LON  = -58.5288
_VENUE_BBOX = (-58.5305, -34.6391, -58.5271, -34.6367)   # W, S, E, N

# ── Umbrales de decisión ───────────────────────────────────────────────────────
_CONFIDENCE_STOP   = 0.70   # >= este valor: parar cascada
_CONFIDENCE_PROXY  = 0.30   # < este valor: proxy, seguir buscando mejor

# ── Constantes Umbra ──────────────────────────────────────────────────────────
_UMBRA_S3_BASE = "https://umbra-open-data-catalog.s3.us-west-2.amazonaws.com"

_KNOWN_BA_SCENE = {
    "item_id":    "eadb4e53-c7fc-4f10-8da6-2cdf2995b37d",
    "task_path":  "sar-data/tasks/Buenos Aires, ARG/0ef0eb34-90ac-44f9-8221-26d5ed431de6/2024-07-15-01-38-25_UMBRA-05/2024-07-15-01-38-25_UMBRA-05_GEC.tif",
    "datetime":   "2024-07-15",
    "platform":   "UMBRA-05",
    "bbox":       (-58.41833, -34.63345, -58.34628, -34.57387),
    "band":       "X",
    "polarization": "VV",
    "res_m":      0.267,
    "license":    "CC-BY-4.0",
}

# ── Labels de fuentes ─────────────────────────────────────────────────────────
_TIER_LABELS = {
    "UMBRA_X":    "Umbra Open Data CC-BY-4.0 · X-band VV Spotlight · 0.267m · COG HTTP range",
    "S1_RTC_PC":  "Sentinel-1 RTC · C-band VV · 20m · Planetary Computer · SAS token libre",
    "CAPELLA_X":  "Capella Open Data CC-BY-4.0 · X-band · 0.5m · COG HTTP range",
    "ALOS2_META": "ALOS-2 PALSAR-2 · L-band · 25m · ASF metadata (sin pixels — auth requerida)",
    "EGMS_PROXY": "EGMS Copernicus 2015-2022 · C-band proxy · histórico · ESTIMADO",
}


# ─────────────────────────────────────────────────────────────────────────────
# Lector de ventana COG — compartido por todos los tiers
# ─────────────────────────────────────────────────────────────────────────────

def _read_sar_window_cog(
    url:       str,
    lon:       float,
    lat:       float,
    half_deg:  float = 0.002,   # 0.002° ≈ 170-220m en BsAs
    is_power:  bool  = False,   # True → dB = 10*log10(val); False → 20*log10(val/255)
) -> Optional[dict]:
    """
    Lee ventana SAR de un COG via HTTP range request (sin descarga completa).

    is_power=False → uint8 amplitud (Umbra, Capella): dB = 20·log10(amp/255)
    is_power=True  → float32 linear power (S1 RTC): dB = 10·log10(power)
    """
    t0 = time.time()
    try:
        import numpy as np
        import rasterio
        from rasterio.windows import Window

        os.environ["AWS_NO_SIGN_REQUEST"] = "YES"

        with rasterio.open(url) as src:
            t = src.transform
            a, e_t, c, f = t.a, t.e, t.c, t.f
            H, W = src.height, src.width

            col_c = (lon - c) / a
            row_c = (lat - f) / e_t
            hc    = half_deg / abs(a)
            hr    = half_deg / abs(e_t)

            col_off = max(0, int(col_c - hc))
            row_off = max(0, int(row_c - hr))
            col_end = min(W, int(col_c + hc))
            row_end = min(H, int(row_c + hr))

            if col_end <= col_off or row_end <= row_off:
                return {"error": "ventana fuera del raster", "n_px": 0}

            win  = Window(col_off, row_off, col_end - col_off, row_end - row_off)
            data = src.read(1, window=win).astype(np.float64)
            pixel_a = abs(a)

        valid = data[data > 0]
        n_px  = int(valid.size)
        if n_px < 4:
            return {"error": "sin pixeles validos en ventana", "n_px": 0}

        if is_power:
            db_vals = 10.0 * np.log10(valid + 1e-10)
        else:
            db_vals = 20.0 * np.log10(valid / 255.0 + 1e-12)

        pixel_m = pixel_a * 100_000   # ° → m aprox a BsAs (-34°)

        return {
            "n_px":         n_px,
            "db_mean":      round(float(np.mean(db_vals)), 2),
            "db_std":       round(float(np.std(db_vals)), 2),
            "db_p10":       round(float(np.percentile(db_vals, 10)), 2),
            "db_p50":       round(float(np.percentile(db_vals, 50)), 2),
            "db_p90":       round(float(np.percentile(db_vals, 90)), 2),
            "pixel_size_m": round(pixel_m, 2),
            "tiempo_lectura_ms": round((time.time() - t0) * 1000),
        }
    except Exception as exc:
        log.warning("dale_play_sar: _read_sar_window_cog: %s", exc)
        return {"error": str(exc)[:200], "n_px": 0}


def _bbox_contains(lon: float, lat: float, bbox) -> bool:
    w, s, e, n = bbox[0], bbox[1], bbox[2], bbox[3]
    return w <= lon <= e and s <= lat <= n


# ─────────────────────────────────────────────────────────────────────────────
# Tier A: Umbra Open Data — X-band Spotlight 0.26m
# ─────────────────────────────────────────────────────────────────────────────

def _try_umbra(venue_lon: float, venue_lat: float, window_m: int) -> dict:
    """
    Tier A: Umbra Open Data CC-BY-4.0.
    1. STAC search para escena que cubra venue → confianza 0.95.
    2. Si no hay cobertura directa → usa escena BA conocida → confianza 0.25.
    """
    t0   = time.time()
    log_entry: dict = {
        "fuente":    "UMBRA_X",
        "intentado": True,
        "resultado": None,
        "razon":     None,
        "confianza_obtenida": 0.0,
    }

    try:
        import requests

        half_deg = (window_m / 2) / 100_000

        # ── 1. STAC search ────────────────────────────────────────────────────
        stac_url = f"{_UMBRA_S3_BASE}/stac/catalog.json"
        r = requests.get(stac_url, timeout=15)

        direct_item = None
        if r.ok:
            for link in r.json().get("links", []):
                if link.get("rel") not in ("child", "item"):
                    continue
                href = link.get("href", "")
                if not href.startswith("http"):
                    href = f"{_UMBRA_S3_BASE}/{href.lstrip('/')}"
                try:
                    rc = requests.get(href, timeout=8)
                    if not rc.ok:
                        continue
                    child = rc.json()
                    if child.get("type") == "Feature":
                        bbox = child.get("bbox", [])
                        if len(bbox) == 4 and _bbox_contains(venue_lon, venue_lat, bbox):
                            direct_item = child
                            break
                except Exception:
                    continue

        # ── 2a. Cobertura directa ─────────────────────────────────────────────
        if direct_item:
            assets = direct_item.get("assets", {})
            gec_href = (
                assets.get("GEC.tif", {}).get("href") or
                assets.get("GEC",     {}).get("href") or
                next((a.get("href", "") for a in assets.values()
                      if "GEC" in a.get("href", "")), "")
            )
            if gec_href.startswith("s3://umbra-open-data-catalog/"):
                gec_href = gec_href.replace(
                    "s3://umbra-open-data-catalog/", f"{_UMBRA_S3_BASE}/"
                )
            stats = _read_sar_window_cog(gec_href, venue_lon, venue_lat, half_deg) if gec_href else {}
            if stats and stats.get("n_px", 0) >= 4:
                props = direct_item.get("properties", {})
                log_entry.update({
                    "resultado": "ok_directo",
                    "razon": f"STAC cobertura directa id={direct_item.get('id','')[:20]}",
                    "confianza_obtenida": 0.95,
                })
                log_entry["tiempo_ms"] = round((time.time() - t0) * 1000)
                return {
                    "ok":              True,
                    "confianza":       0.95,
                    "venue_cubierto":  True,
                    "escena_id":       direct_item.get("id", "")[:40],
                    "escena_datetime": (props.get("datetime") or "")[:10],
                    "escena_platform": props.get("platform", "UMBRA"),
                    "escena_res_m":    props.get("umbra:range_resolution_m", 0.267),
                    "escena_bbox":     direct_item.get("bbox"),
                    "banda":           "X",
                    "polarizacion":    "VV",
                    "licencia":        "CC-BY-4.0",
                    **{f"backscatter_{k}": v for k, v in stats.items()},
                    "_log":            log_entry,
                }

        # ── 2b. Proxy — escena BA conocida ────────────────────────────────────
        sc  = _KNOWN_BA_SCENE
        url = f"{_UMBRA_S3_BASE}/{quote(sc['task_path'])}"
        cx  = (sc["bbox"][0] + sc["bbox"][2]) / 2
        cy  = (sc["bbox"][1] + sc["bbox"][3]) / 2
        stats = _read_sar_window_cog(url, cx, cy, half_deg=0.02) or {}

        dist_km = round(abs(venue_lon - sc["bbox"][2]) * 85, 1)
        nota = (
            f"Sin cobertura directa de Amalfitani. Proxy: escena BA "
            f"{sc['datetime']} {dist_km}km al este (Flores/Caballito/Palermo)."
        )
        log_entry.update({
            "resultado": "ok_proxy",
            "razon": nota,
            "confianza_obtenida": 0.25 if stats.get("n_px", 0) >= 4 else 0.0,
        })
        log_entry["tiempo_ms"] = round((time.time() - t0) * 1000)

        if stats.get("n_px", 0) >= 4:
            return {
                "ok":              True,
                "confianza":       0.25,
                "venue_cubierto":  False,
                "escena_id":       sc["item_id"],
                "escena_datetime": sc["datetime"],
                "escena_platform": sc["platform"],
                "escena_res_m":    sc["res_m"],
                "escena_bbox":     sc["bbox"],
                "banda":           "X",
                "polarizacion":    sc["polarization"],
                "licencia":        sc["license"],
                "cobertura_nota":  nota,
                **{f"backscatter_{k}": v for k, v in stats.items()},
                "_log":            log_entry,
            }

        log_entry.update({"resultado": "error_lectura", "razon": stats.get("error", "n_px=0")})
        log_entry["tiempo_ms"] = round((time.time() - t0) * 1000)
        return {"ok": False, "_log": log_entry}

    except Exception as exc:
        log_entry.update({"resultado": "excepcion", "razon": str(exc)[:200]})
        log_entry["tiempo_ms"] = round((time.time() - t0) * 1000)
        log.warning("dale_play_sar: Tier A (Umbra): %s", exc)
        return {"ok": False, "_log": log_entry}


# ─────────────────────────────────────────────────────────────────────────────
# Tier B: Sentinel-1 RTC — C-band 20m via Planetary Computer
# ─────────────────────────────────────────────────────────────────────────────

def _try_s1_rtc(venue_lon: float, venue_lat: float, window_m: int) -> dict:
    """
    Tier B: Sentinel-1 RTC via Planetary Computer.
    Busca en STAC la imagen más reciente del área. SAS token libre.
    Backscatter en sigma0 linear → convertido a dB.
    Revisita: ~6 días (S1A+S1B) o ~3 días (con S1C desde 2024).
    """
    t0 = time.time()
    log_entry: dict = {
        "fuente":    "S1_RTC_PC",
        "intentado": True,
        "resultado": None,
        "razon":     None,
        "confianza_obtenida": 0.0,
    }

    try:
        import requests

        w, s, e, n = _VENUE_BBOX
        end_dt   = date.today()
        start_dt = end_dt - timedelta(days=45)

        # ── STAC search ───────────────────────────────────────────────────────
        payload = {
            "collections": ["sentinel-1-rtc"],
            "bbox":        [w, s, e, n],
            "datetime":    f"{start_dt.isoformat()}T00:00:00Z/{end_dt.isoformat()}T23:59:59Z",
            "limit":       5,
            "sortby":      [{"field": "datetime", "direction": "desc"}],
        }
        r = requests.post(
            "https://planetarycomputer.microsoft.com/api/stac/v1/search",
            json=payload, timeout=20,
        )
        if not r.ok:
            log_entry.update({
                "resultado": "stac_error",
                "razon": f"PC STAC HTTP {r.status_code}",
            })
            log_entry["tiempo_ms"] = round((time.time() - t0) * 1000)
            return {"ok": False, "_log": log_entry}

        features = r.json().get("features", [])
        if not features:
            log_entry.update({
                "resultado": "sin_escenas",
                "razon": f"No S1 RTC en {start_dt.isoformat()} → {end_dt.isoformat()} para bbox Amalfitani",
            })
            log_entry["tiempo_ms"] = round((time.time() - t0) * 1000)
            return {"ok": False, "_log": log_entry}

        item = features[0]

        # ── SAS token ─────────────────────────────────────────────────────────
        tok_r = requests.get(
            "https://planetarycomputer.microsoft.com/api/sas/v1/token/sentinel-1-rtc",
            timeout=10,
        )
        token = tok_r.json().get("token", "") if tok_r.ok else ""
        if not token:
            log_entry.update({
                "resultado": "sin_token",
                "razon": f"SAS token failed HTTP {tok_r.status_code}",
            })
            log_entry["tiempo_ms"] = round((time.time() - t0) * 1000)
            return {"ok": False, "_log": log_entry}

        # ── Leer banda VV ─────────────────────────────────────────────────────
        assets   = item.get("assets", {})
        vv_href  = assets.get("vv", {}).get("href", "") or assets.get("VV", {}).get("href", "")
        if not vv_href:
            log_entry.update({"resultado": "sin_asset_vv", "razon": f"Keys: {list(assets.keys())}"})
            log_entry["tiempo_ms"] = round((time.time() - t0) * 1000)
            return {"ok": False, "_log": log_entry}

        vv_signed = f"{vv_href}?{token}"
        half_deg  = (window_m / 2) / 100_000
        stats = _read_sar_window_cog(vv_signed, venue_lon, venue_lat, half_deg, is_power=True)

        if not stats or stats.get("n_px", 0) < 4:
            log_entry.update({
                "resultado": "sin_pixeles",
                "razon": stats.get("error", "n_px < 4") if stats else "None",
            })
            log_entry["tiempo_ms"] = round((time.time() - t0) * 1000)
            return {"ok": False, "_log": log_entry}

        props   = item.get("properties", {})
        dt_str  = (props.get("datetime") or "")[:10]
        bbox    = item.get("bbox", [])
        covers  = len(bbox) == 4 and _bbox_contains(venue_lon, venue_lat, bbox)
        conf    = 0.80 if covers else 0.35
        month   = int(dt_str[5:7]) if len(dt_str) >= 7 else None

        log_entry.update({
            "resultado": "ok_directo" if covers else "ok_proxy",
            "razon":     f"S1 RTC fecha={dt_str} venue_cubierto={covers} VV sigma0→dB",
            "confianza_obtenida": conf,
        })
        log_entry["tiempo_ms"] = round((time.time() - t0) * 1000)

        return {
            "ok":              True,
            "confianza":       conf,
            "venue_cubierto":  covers,
            "escena_id":       item.get("id", "")[:40],
            "escena_datetime": dt_str,
            "escena_platform": props.get("platform", "Sentinel-1"),
            "escena_res_m":    20.0,
            "escena_bbox":     bbox,
            "banda":           "C",
            "polarizacion":    "VV",
            "licencia":        "Copernicus Open Access",
            "orbit_state":     props.get("sat:orbit_state", ""),
            **{f"backscatter_{k}": v for k, v in stats.items()},
            "_log":            log_entry,
        }

    except Exception as exc:
        log_entry.update({"resultado": "excepcion", "razon": str(exc)[:200]})
        log_entry["tiempo_ms"] = round((time.time() - t0) * 1000)
        log.warning("dale_play_sar: Tier B (S1 RTC): %s", exc)
        return {"ok": False, "_log": log_entry}


# ─────────────────────────────────────────────────────────────────────────────
# Tier C: Capella Open Data — X-band Spotlight ~0.5m
# ─────────────────────────────────────────────────────────────────────────────

def _try_capella(venue_lon: float, venue_lat: float, window_m: int) -> dict:
    """
    Tier C: Capella Space Open Data CC-BY-4.0.
    Catálogo estático STAC en AWS S3. Cubre locaciones específicas.
    Muy probable que no tenga Amalfitani — falla rápido y limpio.
    """
    t0 = time.time()
    log_entry: dict = {
        "fuente":    "CAPELLA_X",
        "intentado": True,
        "resultado": None,
        "razon":     None,
        "confianza_obtenida": 0.0,
    }

    try:
        import requests

        CAPELLA_STAC = "https://capella-open-data.s3.us-east-1.amazonaws.com/stac/catalog.json"
        r = requests.get(CAPELLA_STAC, timeout=12)
        if not r.ok:
            log_entry.update({
                "resultado": "catalog_error",
                "razon": f"Capella STAC HTTP {r.status_code}",
            })
            log_entry["tiempo_ms"] = round((time.time() - t0) * 1000)
            return {"ok": False, "_log": log_entry}

        catalog = r.json()
        found_item = None

        for link in catalog.get("links", []):
            if link.get("rel") not in ("item", "child"):
                continue
            href = link.get("href", "")
            if not href.startswith("http"):
                href = f"https://capella-open-data.s3.us-east-1.amazonaws.com/{href.lstrip('/')}"
            try:
                ri = requests.get(href, timeout=8)
                if not ri.ok:
                    continue
                item = ri.json()
                if item.get("type") == "Feature":
                    bbox = item.get("bbox", [])
                    if len(bbox) == 4 and _bbox_contains(venue_lon, venue_lat, bbox):
                        found_item = item
                        break
                elif item.get("type") == "FeatureCollection":
                    for feat in item.get("features", []):
                        bbox = feat.get("bbox", [])
                        if len(bbox) == 4 and _bbox_contains(venue_lon, venue_lat, bbox):
                            found_item = feat
                            break
                    if found_item:
                        break
            except Exception:
                continue

        if not found_item:
            log_entry.update({
                "resultado": "sin_cobertura",
                "razon": "Catálogo Capella no tiene escenas sobre Amalfitani bbox (-58.53,-34.64)",
            })
            log_entry["tiempo_ms"] = round((time.time() - t0) * 1000)
            return {"ok": False, "_log": log_entry}

        # Buscar asset GEC o GEO
        assets = found_item.get("assets", {})
        cog_href = ""
        for key in ("GEC", "GEO", "data", "image"):
            href = assets.get(key, {}).get("href", "")
            if href:
                cog_href = href
                break

        if not cog_href:
            log_entry.update({
                "resultado": "sin_asset",
                "razon": f"No COG asset. Keys disponibles: {list(assets.keys())}",
            })
            log_entry["tiempo_ms"] = round((time.time() - t0) * 1000)
            return {"ok": False, "_log": log_entry}

        half_deg = (window_m / 2) / 100_000
        stats    = _read_sar_window_cog(cog_href, venue_lon, venue_lat, half_deg)

        if not stats or stats.get("n_px", 0) < 4:
            log_entry.update({
                "resultado": "sin_pixeles",
                "razon": stats.get("error", "n_px < 4") if stats else "None",
            })
            log_entry["tiempo_ms"] = round((time.time() - t0) * 1000)
            return {"ok": False, "_log": log_entry}

        props  = found_item.get("properties", {})
        dt_str = (props.get("datetime") or props.get("start_datetime") or "")[:10]

        log_entry.update({
            "resultado": "ok_directo",
            "razon": f"Capella GEC fecha={dt_str}",
            "confianza_obtenida": 0.90,
        })
        log_entry["tiempo_ms"] = round((time.time() - t0) * 1000)

        return {
            "ok":              True,
            "confianza":       0.90,
            "venue_cubierto":  True,
            "escena_id":       found_item.get("id", "")[:40],
            "escena_datetime": dt_str,
            "escena_platform": props.get("platform", "CAPELLA"),
            "escena_res_m":    0.5,
            "escena_bbox":     found_item.get("bbox", []),
            "banda":           "X",
            "polarizacion":    (props.get("polarizations") or ["VV"])[0],
            "licencia":        "CC-BY-4.0",
            **{f"backscatter_{k}": v for k, v in stats.items()},
            "_log":            log_entry,
        }

    except Exception as exc:
        log_entry.update({"resultado": "excepcion", "razon": str(exc)[:200]})
        log_entry["tiempo_ms"] = round((time.time() - t0) * 1000)
        log.warning("dale_play_sar: Tier C (Capella): %s", exc)
        return {"ok": False, "_log": log_entry}


# ─────────────────────────────────────────────────────────────────────────────
# Tier D: ALOS-2 PALSAR-2 — L-band via ASF public search API
# ─────────────────────────────────────────────────────────────────────────────

def _try_alos2_meta(venue_lon: float, venue_lat: float) -> dict:
    """
    Tier D: ALOS-2 PALSAR-2 L-band via ASF search (metadata only).
    La búsqueda en ASF no requiere auth. La descarga sí (NASA Earthdata).
    Retorna metadata de escenas disponibles pero sin pixels.
    Confianza: 0.15 (metadata — sin backscatter real).
    """
    t0 = time.time()
    log_entry: dict = {
        "fuente":    "ALOS2_META",
        "intentado": True,
        "resultado": None,
        "razon":     None,
        "confianza_obtenida": 0.0,
    }

    try:
        import requests

        w, s, e, n = _VENUE_BBOX
        # ASF Vertex search API — sin autenticación para metadata
        params = {
            "platform":        "ALOS-2",
            "processingLevel": "GRD",
            "bbox":            f"{w},{s},{e},{n}",
            "maxResults":      5,
            "output":          "json",
        }
        r = requests.get(
            "https://api.daac.asf.alaska.edu/services/search/param",
            params=params, timeout=20,
        )
        if not r.ok:
            log_entry.update({
                "resultado": "api_error",
                "razon": f"ASF search HTTP {r.status_code}",
            })
            log_entry["tiempo_ms"] = round((time.time() - t0) * 1000)
            return {"ok": False, "_log": log_entry}

        data = r.json()
        results = data if isinstance(data, list) else data.get("results", [])

        if not results:
            log_entry.update({
                "resultado": "sin_escenas",
                "razon": "No ALOS-2 GRD en ASF para bbox Amalfitani",
            })
            log_entry["tiempo_ms"] = round((time.time() - t0) * 1000)
            return {"ok": False, "_log": log_entry}

        scene = results[0]
        dt_str = (scene.get("startTime") or scene.get("processingDate") or "")[:10]

        log_entry.update({
            "resultado": "ok_meta",
            "razon": f"ALOS-2 GRD metadata fecha={dt_str} — sin pixels (descarga requiere Earthdata auth)",
            "confianza_obtenida": 0.15,
        })
        log_entry["tiempo_ms"] = round((time.time() - t0) * 1000)

        return {
            "ok":              True,
            "confianza":       0.15,
            "venue_cubierto":  True,
            "escena_id":       scene.get("granuleName", "")[:40],
            "escena_datetime": dt_str,
            "escena_platform": "ALOS-2",
            "escena_res_m":    25.0,
            "banda":           "L",
            "polarizacion":    scene.get("polarization", ""),
            "licencia":        "JAXA — licencia de investigación",
            "nota_pixeles":    "Metadata only. Descarga: earthaccess + NASA_EARTHDATA_USER",
            "n_escenas_encontradas": len(results),
            "_log":            log_entry,
        }

    except Exception as exc:
        log_entry.update({"resultado": "excepcion", "razon": str(exc)[:200]})
        log_entry["tiempo_ms"] = round((time.time() - t0) * 1000)
        log.warning("dale_play_sar: Tier D (ALOS-2): %s", exc)
        return {"ok": False, "_log": log_entry}


# ─────────────────────────────────────────────────────────────────────────────
# Tier E: EGMS proxy — siempre disponible, confianza 0.10
# ─────────────────────────────────────────────────────────────────────────────

def _egms_proxy(cascade_log: list) -> dict:
    """
    Tier E: Proxy desde EGMS Copernicus 2015-2022.
    Siempre disponible. Retorna velocidad de subsidencia como proxy SAR.
    No hay backscatter real — se usa LOS velocity como indicador de deformación.
    Confianza: 0.10 — histórico, no tiempo real.
    """
    log.warning(
        "dale_play_sar: todos los tiers A-D fallaron — usando EGMS proxy (confianza 0.10)"
    )
    try:
        from dale_play_egms import fetch_egms_amalfitani
        egms = fetch_egms_amalfitani()
        vel  = egms.get("vel_max_abs_mm_yr", 3.8)
        sector_critico = egms.get("sector_critico", "tribuna_oeste")
    except Exception:
        vel            = 3.8
        sector_critico = "tribuna_oeste"
        egms           = {}

    interp = (
        f"Proxy EGMS: subsidencia máxima {vel:.1f} mm/año en {sector_critico}. "
        "Sin backscatter SAR disponible — usar EGMS como indicador de deformación estructural."
    )

    cascade_log.append({
        "fuente":    "EGMS_PROXY",
        "intentado": True,
        "resultado": "ok_proxy",
        "razon":     "Fallback de último recurso — datos EGMS 2015-2022",
        "confianza_obtenida": 0.10,
        "tiempo_ms": 0,
    })

    return {
        "sar_fuente_usada":      "EGMS_PROXY",
        "sar_nivel_confianza":   0.10,
        "sar_cascade_log":       cascade_log,
        "venue_cubierto":        True,
        "escena_datetime":       "2015-2022",
        "escena_platform":       "Sentinel-1 (EGMS procesado)",
        "escena_res_m":          20.0,
        "banda":                 "C",
        "polarizacion":          "VV+VH (EGMS)",
        "licencia":              "Copernicus Open Access",
        "fuente":                _TIER_LABELS["EGMS_PROXY"],
        "fuente_tipo":           "EGMS_PROXY",
        "umbra_cobre_venue":     False,
        "fallback_usado":        True,
        "estimado":              True,
        "backscatter_db_mean":   None,
        "egms_vel_max_mm_yr":    vel,
        "egms_sector_critico":   sector_critico,
        "interpretacion":        interp,
        "nota_confianza": (
            "Tier E (mínima confianza). Ninguna fuente SAR A-D disponible. "
            "EGMS como proxy de deformación — no es backscatter real."
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Detección de cambio entre dos escenas SAR
# ─────────────────────────────────────────────────────────────────────────────

def compare_sar_scenes(scene_pre: dict, scene_post: dict) -> dict:
    """
    Delta de backscatter entre escena pre y post-show.
    Umbral compactación suelo: |Δ| > 1.5 dB.
    Umbral cambio estructural: |Δ| > 3.0 dB.
    """
    db_pre  = scene_pre.get("backscatter_db_mean")
    db_post = scene_post.get("backscatter_db_mean")

    if db_pre is None or db_post is None:
        return {
            "delta_db":            None,
            "alerta_compactacion": False,
            "alerta_estructural":  False,
            "interpretacion":      "Delta no computable — falta escena pre o post con backscatter real",
        }

    delta        = round(db_post - db_pre, 2)
    alerta_comp  = abs(delta) > 1.5
    alerta_est   = abs(delta) > 3.0

    if alerta_est:
        interp = (f"CAMBIO ESTRUCTURAL DETECTADO: Δ={delta:+.2f} dB. "
                  "Posible deformación de superficie o alteración de estructuras.")
    elif alerta_comp:
        interp = (f"ALERTA COMPACTACIÓN: Δ={delta:+.2f} dB. "
                  "Cambio consistente con compactación de suelo post-evento.")
    elif abs(delta) > 0.5:
        interp = f"Cambio leve: Δ={delta:+.2f} dB. Monitoreo recomendado."
    else:
        interp = f"Sin cambio significativo: Δ={delta:+.2f} dB. Suelo estable."

    return {
        "delta_db":               delta,
        "db_pre":                 db_pre,
        "db_post":                db_post,
        "fuente_pre":             scene_pre.get("sar_fuente_usada", ""),
        "fuente_post":            scene_post.get("sar_fuente_usada", ""),
        "alerta_compactacion":    alerta_comp,
        "alerta_estructural":     alerta_est,
        "umbral_compactacion_db": 1.5,
        "umbral_estructural_db":  3.0,
        "interpretacion":         interp,
        "nota_banda": (
            "Comparación válida solo si ambas escenas son de la misma banda (X/C/L). "
            f"Pre: {scene_pre.get('banda','?')} / Post: {scene_post.get('banda','?')}"
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Entry point principal — cascada completa
# ─────────────────────────────────────────────────────────────────────────────

def fetch_sar(
    venue_lat: float = _VENUE_LAT,
    venue_lon: float = _VENUE_LON,
    show_id:   str   = "unknown",
    window_m:  int   = 500,
) -> dict:
    """
    Cascada SAR con redundancia de aviación: Umbra → S1 RTC → Capella → EGMS proxy.

    Lógica de parada:
    - Si confianza >= 0.70 (cobertura directa): parar y usar esa fuente.
    - Si confianza < 0.70: registrar, continuar buscando fuente mejor.
    - Si ninguna fuente da pixels: usar EGMS proxy (confianza 0.10).

    Siempre retorna:
      sar_fuente_usada:     str  — tier ID usado
      sar_nivel_confianza:  float — 0.10 a 0.95
      sar_cascade_log:      list — log de todos los intentos
      backscatter_db_mean:  float | None
      venue_cubierto:       bool
      fallback_usado:       bool
    """
    cascade_log: list[dict] = []
    best: Optional[dict]    = None
    best_conf: float        = -1.0

    tiers = [
        (_try_umbra,       "UMBRA_X"),
        (_try_s1_rtc,      "S1_RTC_PC"),
        (_try_capella,     "CAPELLA_X"),
    ]

    for tier_fn, tier_id in tiers:
        try:
            result = tier_fn(venue_lon, venue_lat, window_m)
        except Exception as exc:
            result = {
                "ok": False,
                "_log": {
                    "fuente":    tier_id,
                    "intentado": True,
                    "resultado": "excepcion_no_capturada",
                    "razon":     str(exc)[:200],
                    "tiempo_ms": 0,
                },
            }

        entry = result.pop("_log", {"fuente": tier_id, "intentado": True})
        cascade_log.append(entry)

        if result.get("ok"):
            conf = result.get("confianza", 0.0)
            log.info("dale_play_sar: Tier %s → conf=%.2f venue=%s",
                     tier_id, conf, result.get("venue_cubierto"))

            if conf > best_conf:
                best      = result
                best_conf = conf
                best["_tier_id"] = tier_id

            if best_conf >= _CONFIDENCE_STOP:
                log.info("dale_play_sar: conf %.2f >= %.2f → cascada parada en %s",
                         best_conf, _CONFIDENCE_STOP, tier_id)
                break
        else:
            log.info("dale_play_sar: Tier %s → fallo (%s)",
                     tier_id, entry.get("resultado"))

    # ── ALOS-2 metadata complementario (siempre intentar si no hay pixels directos) ──
    if best_conf < _CONFIDENCE_STOP:
        alos_result = _try_alos2_meta(venue_lon, venue_lat)
        alos_log    = alos_result.pop("_log", {"fuente": "ALOS2_META"})
        cascade_log.append(alos_log)
        if alos_result.get("ok") and best_conf < 0.15:
            alos_result["_tier_id"] = "ALOS2_META"
            if not best:
                best      = alos_result
                best_conf = 0.15

    # ── Si ninguna fuente dio resultado: EGMS proxy ───────────────────────────
    if not best:
        return _egms_proxy(cascade_log)

    # ── Armar resultado final ─────────────────────────────────────────────────
    tier_id_usado = best.pop("_tier_id", "UNKNOWN")
    best.pop("ok", None)
    best.pop("confianza", None)

    best["sar_fuente_usada"]    = tier_id_usado
    best["sar_nivel_confianza"] = best_conf
    best["sar_cascade_log"]     = cascade_log
    best["fuente"]              = _TIER_LABELS.get(tier_id_usado, tier_id_usado)
    best["fuente_tipo"]         = tier_id_usado
    best["umbra_cobre_venue"]   = best.get("venue_cubierto", False)
    best["fallback_usado"]      = not best.get("venue_cubierto", False)
    best["estimado"]            = best_conf < 0.30

    # Campo de compatibilidad con el módulo anterior (dale_play_umbra)
    best.setdefault("escena_id",       "")
    best.setdefault("backscatter_db_mean", None)
    best.setdefault("backscatter_n_px",    0)

    log.info(
        "dale_play_sar: resultado final fuente=%s conf=%.2f venue=%s db_mean=%s n_px=%s",
        tier_id_usado, best_conf,
        best.get("venue_cubierto"),
        best.get("backscatter_db_mean"),
        best.get("backscatter_n_px"),
    )

    # Registrar en SOURCE_LOG.md automáticamente
    try:
        from dale_play_source_log import log_source_event
        log_source_event(
            modulo="SAR",
            fuente_usada=tier_id_usado,
            confianza=best_conf,
            venue_cubierto=best.get("venue_cubierto"),
            cascade_log=cascade_log,
        )
    except Exception:
        pass

    return best
