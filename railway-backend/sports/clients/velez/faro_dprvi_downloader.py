"""
faro_dprvi_downloader.py — Descarga JSON de DpRVIc desde GCS e inserta en Supabase.

Pipeline:
  1. Lee el JSON más reciente del bucket GCS (exportado por GEE)
  2. Parsea dprvi_value por cuadrante/sector
  3. INSERT/UPSERT en dprvi_metrics (Supabase)

Variables de entorno requeridas:
  GCS_BUCKET                 — nombre del bucket (ej: "faro-dprvi-exports")
  GCS_SERVICE_ACCOUNT_JSON   — contenido JSON de la service account (base64 o raw string)
  SUPABASE_URL               — https://xljxpzudgwhbzcnrvylo.supabase.co
  SUPABASE_KEY               — service role key con permisos INSERT en dprvi_metrics

Ejecutar: 13:00 UTC diariamente (1h post-ERA5)
"""
from __future__ import annotations

import base64
import json
import logging
import os
import sys
import tempfile
from datetime import date, datetime, timezone

import requests

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ── Config ────────────────────────────────────────────────────────────────────

GCS_BUCKET   = os.environ.get("GCS_BUCKET", "faro-dprvi-exports")
GCS_SA_JSON  = os.environ.get("GCS_SERVICE_ACCOUNT_JSON", "")
SUPA_URL     = os.environ.get("SUPABASE_URL", "https://xljxpzudgwhbzcnrvylo.supabase.co").rstrip("/")
SUPA_KEY     = os.environ.get("SUPABASE_KEY", "")

# Mapeo cuadrante GEE → cancha_id Supabase
CANCHA_MAP = {
    "amalfitani_norte":   "amalfitani_central",
    "amalfitani_sur":     "amalfitani_central",
    "amalfitani_central": "amalfitani_central",
    "vo_bloque_a":        "vo_bloque_a",
    "vo_bloque_b":        "vo_bloque_b",
    "vo_bloque_c":        "vo_bloque_c",
    "vo_bloque_d":        "vo_bloque_d",
}


# ── GCS helpers ───────────────────────────────────────────────────────────────

def _gcs_token() -> str:
    """Obtiene access token de GCS via service account JSON."""
    import urllib.request, urllib.parse, time, hmac, hashlib

    sa_json_raw = GCS_SA_JSON
    if not sa_json_raw:
        raise RuntimeError("GCS_SERVICE_ACCOUNT_JSON no configurado")

    # Soporta base64 o raw JSON string
    try:
        if sa_json_raw.strip().startswith("{"):
            sa = json.loads(sa_json_raw)
        else:
            sa = json.loads(base64.b64decode(sa_json_raw))
    except Exception as e:
        raise RuntimeError(f"GCS_SERVICE_ACCOUNT_JSON inválido: {e}") from e

    try:
        import google.oauth2.service_account as _sa
        import google.auth.transport.requests as _tr
        creds = _sa.Credentials.from_service_account_info(
            sa, scopes=["https://www.googleapis.com/auth/devstorage.read_only"]
        )
        creds.refresh(_tr.Request())
        return creds.token
    except ImportError:
        pass

    # Fallback: JWT manual sin google-auth
    now = int(time.time())
    header  = base64.urlsafe_b64encode(json.dumps({"alg":"RS256","typ":"JWT"}).encode()).rstrip(b"=")
    payload = base64.urlsafe_b64encode(json.dumps({
        "iss": sa["client_email"],
        "scope": "https://www.googleapis.com/auth/devstorage.read_only",
        "aud": "https://oauth2.googleapis.com/token",
        "iat": now, "exp": now + 3600,
    }).encode()).rstrip(b"=")

    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    from cryptography.hazmat.primitives.asymmetric.padding import PKCS1v15
    from cryptography.hazmat.primitives.hashes import SHA256

    key = load_pem_private_key(sa["private_key"].encode(), password=None)
    sig_input = header + b"." + payload
    sig = base64.urlsafe_b64encode(key.sign(sig_input, PKCS1v15(), SHA256())).rstrip(b"=")
    jwt_token = (sig_input + b"." + sig).decode()

    resp = urllib.request.urlopen(
        urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=urllib.parse.urlencode({
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": jwt_token,
            }).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ), timeout=15
    )
    return json.loads(resp.read())["access_token"]


def _gcs_list_blobs(token: str, prefix: str) -> list[str]:
    """Lista blobs en el bucket con el prefijo dado."""
    url = f"https://storage.googleapis.com/storage/v1/b/{GCS_BUCKET}/o"
    r = requests.get(url, params={"prefix": prefix, "maxResults": 50},
                     headers={"Authorization": f"Bearer {token}"}, timeout=15)
    r.raise_for_status()
    return [item["name"] for item in r.json().get("items", [])]


def _gcs_download(token: str, blob_name: str) -> dict:
    """Descarga y parsea JSON de GCS."""
    url = f"https://storage.googleapis.com/storage/v1/b/{GCS_BUCKET}/o/{requests.utils.quote(blob_name, safe='')}"
    r = requests.get(url, params={"alt": "media"},
                     headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    return r.json()


def _latest_blob(token: str) -> tuple[str, dict]:
    """Retorna (blob_name, data) del JSON más reciente de hoy o ayer."""
    today = datetime.now(timezone.utc).date()
    for d in [today, date(today.year, today.month, today.day)]:
        prefix = f"dprvi/{d.isoformat()}"
        blobs = _gcs_list_blobs(token, prefix)
        if blobs:
            blob = sorted(blobs)[-1]
            log.info("Descargando %s", blob)
            return blob, _gcs_download(token, blob)
    raise RuntimeError(f"No hay blobs DpRVIc para hoy ({today})")


# ── Supabase helpers ──────────────────────────────────────────────────────────

def _sb_headers() -> dict:
    return {
        "apikey": SUPA_KEY,
        "Authorization": f"Bearer {SUPA_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }


def _upsert_dprvi(rows: list[dict]) -> bool:
    if not SUPA_URL or not SUPA_KEY:
        log.error("SUPABASE_URL o SUPABASE_KEY no configurados")
        return False
    r = requests.post(
        f"{SUPA_URL}/rest/v1/dprvi_metrics",
        headers=_sb_headers(), json=rows, timeout=20,
    )
    if r.status_code in (200, 201, 204):
        log.info("Upsert OK — %d filas", len(rows))
        return True
    log.error("Supabase upsert HTTP %d: %s", r.status_code, r.text[:300])
    return False


# ── Parseo GEE output ─────────────────────────────────────────────────────────

def _parse_gee_json(data: dict, fecha: date) -> list[dict]:
    """
    Parsea el JSON exportado por el script GEE.

    Formato esperado:
    {
      "fecha": "2026-07-01",
      "features": [
        {
          "sector": "amalfitani_central",
          "dprvi_value": 0.42,
          "dprvi_confidence_pct": 78,
          "vh_db": -14.3,
          "vv_db": -8.7,
          "vh_vv_ratio": 0.316
        },
        ...
      ]
    }
    """
    rows = []
    features = data.get("features", data.get("sectors", []))
    if not features:
        log.warning("JSON GEE sin features: %s", list(data.keys()))
        return rows

    for f in features:
        sector_raw = f.get("sector") or f.get("cancha_id") or f.get("id")
        if not sector_raw:
            continue
        cancha_id = CANCHA_MAP.get(sector_raw, sector_raw)

        dprvi_val = f.get("dprvi_value") or f.get("dprvi")
        if dprvi_val is None:
            log.warning("Sector %s sin dprvi_value — skip", sector_raw)
            continue

        # Confianza: usar la del GEE si viene, sino estimar por N pixeles válidos
        confidence = f.get("dprvi_confidence_pct") or f.get("confidence_pct")
        if confidence is None:
            n_px = f.get("n_pixels_valid", 0)
            confidence = min(95, max(0, int(n_px / 10)))  # heurística: 1000px → 95%

        row = {
            "cancha_id":             cancha_id,
            "fecha":                 str(fecha),
            "dprvi_value":           round(float(dprvi_val), 4),
            "dprvi_confidence_pct":  int(confidence),
            "vh_db":                 _safe_float(f.get("vh_db")),
            "vv_db":                 _safe_float(f.get("vv_db")),
            "vh_vv_ratio":           _safe_float(f.get("vh_vv_ratio")),
        }
        rows.append(row)

    return rows


def _safe_float(v) -> float | None:
    try:
        return round(float(v), 4) if v is not None else None
    except (TypeError, ValueError):
        return None


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    log.info("=== faro_dprvi_downloader START ===")

    if not GCS_SA_JSON:
        log.error("GCS_SERVICE_ACCOUNT_JSON no configurado — saliendo")
        return 1
    if not SUPA_KEY:
        log.error("SUPABASE_KEY no configurado — saliendo")
        return 1

    try:
        token = _gcs_token()
        log.info("GCS token OK")
    except Exception as e:
        log.error("GCS auth error: %s", e)
        return 1

    try:
        blob_name, data = _latest_blob(token)
    except Exception as e:
        log.error("GCS download error: %s", e)
        return 1

    # Extraer fecha del blob name o del JSON
    fecha_str = data.get("fecha") or blob_name.split("/")[1] if "/" in blob_name else str(date.today())
    try:
        fecha = date.fromisoformat(fecha_str)
    except ValueError:
        fecha = date.today()

    rows = _parse_gee_json(data, fecha)
    if not rows:
        log.error("Sin filas parseadas desde %s", blob_name)
        return 1

    log.info("Filas parseadas: %d (fecha=%s)", len(rows), fecha)
    for r in rows:
        log.info("  %s → dprvi=%.4f conf=%d%%", r["cancha_id"], r["dprvi_value"], r["dprvi_confidence_pct"])

    ok = _upsert_dprvi(rows)
    if not ok:
        return 1

    log.info("=== faro_dprvi_downloader OK ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
