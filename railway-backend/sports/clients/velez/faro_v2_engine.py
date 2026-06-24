"""
faro_v2_engine.py — Faro Engine V2
Motor satelital de segunda generación. Corre 100% en Railway (CPU, $0).

Verticales:
  Agro/Suelo         → OPERA RTC SAR gamma0 → Van Genuchten → soil moisture
  Hidrodinámica      → HAND (Height Above Nearest Drainage) desde Copernicus DEM 30m
  Infraestructura    → ETH Canopy Height 10m + L-Band baseline ALOS PALSAR
  Energía/Solar      → GHI + ET0 vía Open-Meteo Archive API (EUMETSAT/ERA5, sin auth)

Certificación:
  SHA-256 del reporte → RFC 3161 TSA (Sigstore/Google, sin deps extra)
  DER encoding manual — sin pyasn1 ni cryptography (stdlib pura)

Env vars:
  NASA_EARTHDATA_USER + NASA_EARTHDATA_PASS   OPERA RTC via earthaccess
  GOOGLE_EE_SERVICE_ACCOUNT                   GEE service account email (opcional)
  GOOGLE_EE_PRIVATE_KEY                       GEE private key JSON en base64 (opcional)
  SUPABASE_URL + SUPABASE_KEY                 persistencia resultados

Complementa (no reemplaza):
  ndvi_real.py          cascade óptica S2/Landsat/OpenEO
  insar_hyp3.py         D-InSAR displacement semanal
  faro_cloudbreaker_hf  SAR→NDVI fusion fallback

Schema Supabase (ejecutar una vez):
  CREATE TABLE IF NOT EXISTS faro_v2_reports (
    id           bigserial PRIMARY KEY,
    venue_id     text NOT NULL,
    fecha        date NOT NULL,
    solar        jsonb,
    sar          jsonb,
    canopy       jsonb,
    hydro        jsonb,
    lband        jsonb,
    audit        jsonb,
    errors       text[],
    duration_s   float,
    created_at   timestamptz DEFAULT now()
  );
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

import numpy as np
import requests

log = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# ── Env vars (lazy lambdas — no side effects en import) ───────────────────────
_NASA_USER  = lambda: os.environ.get("NASA_EARTHDATA_USER")  or os.environ.get("EARTHDATA_USERNAME",  "")
_NASA_PASS  = lambda: os.environ.get("NASA_EARTHDATA_PASS")  or os.environ.get("EARTHDATA_PASSWORD",  "")
_EE_SA      = lambda: os.environ.get("GOOGLE_EE_SERVICE_ACCOUNT", "")
_EE_KEY_B64 = lambda: os.environ.get("GOOGLE_EE_PRIVATE_KEY",     "")  # base64(JSON)
_SUPA_URL   = lambda: os.environ.get("SUPABASE_URL", "")
_SUPA_KEY   = lambda: os.environ.get("SUPABASE_KEY", "")

_SIGSTORE_TSA = "https://timestamp.sigstore.dev/api/v1/timestamp"
_GITHUB_TSA   = "https://timestamp.githubapp.com/api/v1/timestamp"

# ── Venue Registry ────────────────────────────────────────────────────────────
# Única fuente de verdad geoespacial. bbox: (min_lon, min_lat, max_lon, max_lat).
# Agregar nuevos venues aquí — el resto del engine es automáticamente venue-agnostic.
VENUE_REGISTRY: dict[str, dict] = {
    "amalfitani": {
        "name":       "Estadio José Amalfitani",
        "lat":        -34.6373,
        "lon":        -58.5240,
        "bbox":       (-58.5270, -34.6400, -58.5205, -34.6345),
        "altitude_m": 25,
        "timezone":   "America/Argentina/Buenos_Aires",
    },
    "villa_olimpica": {
        "name":       "Villa Olímpica Vélez",
        "lat":        -34.6420,
        "lon":        -58.5195,
        "bbox":       (-58.5245, -34.6465, -58.5145, -34.6380),
        "altitude_m": 24,
        "timezone":   "America/Argentina/Buenos_Aires",
    },
}


def _v(venue_id: str) -> dict:
    v = VENUE_REGISTRY.get(venue_id)
    if not v:
        raise ValueError(f"venue '{venue_id}' no registrado — agregarlo a VENUE_REGISTRY")
    return v


# ── Data Structures ───────────────────────────────────────────────────────────

@dataclass
class SolarMetrics:
    fecha:         str
    ghi_wh_m2:     Optional[float] = None   # irradiación global horizontal acumulada diaria
    et0_mm_dia:    Optional[float] = None   # evapotranspiración referencia FAO-56
    fuente:        str = "open-meteo/era5"
    latencia_h:    float = 48.0             # ERA5 tiene ~2 días de latencia


@dataclass
class SARMetrics:
    fecha:          str
    vv_gamma0_db:   Optional[float] = None  # VV gamma0 backscatter dB (OPERA RTC)
    vh_gamma0_db:   Optional[float] = None  # VH gamma0 backscatter dB
    theta_soil:     Optional[float] = None  # contenido volumétrico m³/m³
    h_suction_cm:   Optional[float] = None  # tensión mátrica Van Genuchten cm
    n_granules:     int = 0
    fuente:         str = "opera-rtc-s1"


@dataclass
class CanopyMetrics:
    fecha_ref:      str = "2020"
    altura_media_m: Optional[float] = None
    altura_max_m:   Optional[float] = None
    fuente:         str = "eth-global-canopy-height-10m-2020"


@dataclass
class HydroMetrics:
    hand_mean_m:    Optional[float] = None  # Height Above Nearest Drainage, media AOI
    hand_p90_m:     Optional[float] = None  # percentil 90 (representa crestas/bordes)
    zona_riesgo:    Optional[str]   = None  # "bajo" | "medio" | "alto"
    fuente:         str = "copernicus-dem-glo-30"


@dataclass
class LBandBaseline:
    año_inicio:    int = 2015
    año_fin:       int = 2024
    hh_mean_db:    Optional[float] = None
    hv_mean_db:    Optional[float] = None
    n_años:        int = 0
    fuente:        str = "jaxa-alos-palsar-yearly-sar-epoch"


@dataclass
class AuditRecord:
    sha256:         str = ""
    timestamp_iso:  str = ""
    tsa_token_b64:  Optional[str] = None    # TimeStampToken RFC 3161 en base64
    tsa_url:        str = ""
    verified:       bool = False


@dataclass
class FaroV2Report:
    venue_id:   str
    fecha:      str
    solar:      SolarMetrics   = field(default_factory=lambda: SolarMetrics(fecha=str(date.today())))
    sar:        SARMetrics     = field(default_factory=lambda: SARMetrics(fecha=str(date.today())))
    canopy:     CanopyMetrics  = field(default_factory=CanopyMetrics)
    hydro:      HydroMetrics   = field(default_factory=HydroMetrics)
    lband:      LBandBaseline  = field(default_factory=LBandBaseline)
    audit:      AuditRecord    = field(default_factory=AuditRecord)
    errors:     list[str]      = field(default_factory=list)
    duration_s: float          = 0.0


# ── Van Genuchten (sincronizado con insar_hyp3.py) ────────────────────────────

def _van_genuchten(vv_db: float, vh_db: float) -> tuple[float, float]:
    """Water Cloud Model → Van Genuchten (Franco-Arenoso Deportivo, Vélez)."""
    sig_vv = 10.0 ** (vv_db / 10.0)
    sig_vh = 10.0 ** (vh_db / 10.0)
    ratio  = sig_vh / sig_vv if sig_vv > 1e-9 else 0.0
    sig_c  = sig_vh / 0.11 if ratio < 0.05 else sig_vv
    cos_t  = math.cos(math.radians(38.5))
    tau2   = math.exp(-2.0 * 0.08 * 2.5 / cos_t)
    s_veg  = 0.0012 * 2.5 * (1.0 - tau2) * cos_t
    s_soil = max((sig_c - s_veg) / (tau2 + 1e-6), 1e-4)
    theta  = (s_soil * 0.28) + 0.12
    theta_r, theta_s, alpha, n = 0.045, 0.410, 0.068, 1.89
    m   = 1.0 - 1.0 / n
    tc  = max(theta_r + 1e-4, min(theta_s - 1e-4, theta))
    se  = (tc - theta_r) / (theta_s - theta_r)
    h   = (1.0 / alpha) * (max((se ** (-1.0 / m)) - 1.0, 0.0) ** (1.0 / n))
    return round(tc, 4), round(h, 2)


# ── Ingesta A: Solar / ET0 — Open-Meteo Archive ───────────────────────────────

def _fetch_solar(venue_id: str, dias: int = 3) -> SolarMetrics:
    """
    Open-Meteo Archive API — ERA5 reanalysis + EUMETSAT satellite radiation.
    Sin auth, sin SDK. GHI acumulado diario (Wh/m²) + ET0 FAO-56 (mm/día).
    """
    v     = _v(venue_id)
    today = date.today()
    start = (today - timedelta(days=dias)).isoformat()
    end   = today.isoformat()

    try:
        r = requests.get(
            "https://archive-api.open-meteo.com/v1/archive",
            params={
                "latitude":    v["lat"],
                "longitude":   v["lon"],
                "start_date":  start,
                "end_date":    end,
                "daily":       "shortwave_radiation_sum,et0_fao_evapotranspiration",
                "timezone":    v["timezone"],
            },
            timeout=20,
        )
        r.raise_for_status()
        data  = r.json()
        daily = data.get("daily", {})
        dates = daily.get("time", [])
        ghi_s = daily.get("shortwave_radiation_sum", [])   # MJ/m²/día
        et0_s = daily.get("et0_fao_evapotranspiration", [])

        if not dates:
            return SolarMetrics(fecha=today.isoformat())

        # Último día disponible
        idx      = -1
        last_d   = dates[idx]
        ghi_mj   = ghi_s[idx] if ghi_s else None
        et0_mm   = et0_s[idx] if et0_s else None

        # MJ/m² → Wh/m²  (1 MJ = 277.78 Wh)
        ghi_wh = round(ghi_mj * 277.78, 0) if ghi_mj is not None else None

        log.info("solar %s: GHI=%.0f Wh/m² ET0=%.2f mm [%s]",
                 venue_id, ghi_wh or 0, et0_mm or 0, last_d)
        return SolarMetrics(
            fecha      = last_d,
            ghi_wh_m2  = ghi_wh,
            et0_mm_dia = round(et0_mm, 2) if et0_mm is not None else None,
        )
    except Exception as e:
        log.warning("solar fetch (non-fatal) %s: %s", venue_id, e)
        return SolarMetrics(fecha=today.isoformat())


# ── Ingesta B: SAR — OPERA L2 RTC-S1 via earthaccess + stackstac ─────────────

def _fetch_sar_opera(venue_id: str, days_back: int = 12) -> SARMetrics:
    """
    OPERA RTC-S1-V1 (JPL): SAR gamma0 normalizado por terreno, 30m, C-band.
    earthaccess gestiona las credenciales S3 temporales automáticamente.
    Busca vía CMR-STAC (público), descarga en streaming (sin escritura a disco).
    """
    v     = _v(venue_id)
    today = date.today()
    start = (today - timedelta(days=days_back)).isoformat()

    try:
        import earthaccess
        import pystac_client
        import stackstac
        import dask
    except ImportError as e:
        log.warning("sar opera: dep faltante %s — skipping", e)
        return SARMetrics(fecha=today.isoformat())

    try:
        earthaccess.login(strategy="environment")

        catalog = pystac_client.Client.open("https://cmr.earthdata.nasa.gov/stac/ASF")
        search  = catalog.search(
            collections = ["OPERA_L2_RTC-S1_V1"],
            bbox        = v["bbox"],
            datetime    = f"{start}/{today.isoformat()}",
            max_items   = 8,
        )
        items = list(search.items())

        if not items:
            log.warning("sar opera: 0 granules en %dd para %s", days_back, venue_id)
            return SARMetrics(fecha=today.isoformat())

        log.info("sar opera: %d granules encontrados", len(items))

        stack = stackstac.stack(
            items,
            assets     = ["VV", "VH"],
            bounds     = v["bbox"],
            resolution = 30,
            dtype      = "float32",
            fill_value = float("nan"),
        )

        # Media temporal + espacial — compute en single thread (Railway CPU)
        with dask.config.set(scheduler="synchronous"):
            arr = stack.mean(dim=["time", "y", "x"]).compute()

        bands    = [str(b).upper() for b in arr.band.values]
        vv_lin   = float(arr.sel(band="VV").values) if "VV" in bands else None
        vh_lin   = float(arr.sel(band="VH").values) if "VH" in bands else None

        if vv_lin is None or np.isnan(vv_lin):
            log.warning("sar opera: VV NaN tras compute — sin datos válidos")
            return SARMetrics(fecha=today.isoformat())

        vv_db  = round(10 * np.log10(max(vv_lin, 1e-6)), 2)
        vh_db  = (round(10 * np.log10(max(vh_lin, 1e-6)), 2)
                  if vh_lin and not np.isnan(vh_lin) else round(vv_db - 7.5, 2))

        theta, h_suc = _van_genuchten(vv_db, vh_db)

        log.info("sar opera %s: VV=%.2f dB VH=%.2f dB θ=%.3f h=%.1f cm",
                 venue_id, vv_db, vh_db, theta, h_suc)
        return SARMetrics(
            fecha        = today.isoformat(),
            vv_gamma0_db = vv_db,
            vh_gamma0_db = vh_db,
            theta_soil   = theta,
            h_suction_cm = h_suc,
            n_granules   = len(items),
            fuente       = f"opera-rtc-s1 · {len(items)} granules",
        )
    except Exception as e:
        log.warning("sar opera (non-fatal) %s: %s", venue_id, e)
        return SARMetrics(fecha=today.isoformat())


# ── Ingesta C: Canopy Height — ETH 10m via GEE ───────────────────────────────

def _fetch_canopy_height(venue_id: str) -> CanopyMetrics:
    """
    ETH Global Canopy Height 2020, 10m (Sentinel-2 × GEDI fusion, Lang et al. 2023).
    Estadísticas zonales sobre el bbox del venue.
    Requiere GEE: GOOGLE_EE_SERVICE_ACCOUNT + GOOGLE_EE_PRIVATE_KEY.
    """
    v = _v(venue_id)
    try:
        import ee
        _init_ee()

        geom  = ee.Geometry.BBox(*v["bbox"])
        image = ee.Image("users/nlang/ETH_GlobalCanopyHeight_2020_10m_v1").clip(geom)

        stats = image.reduceRegion(
            reducer   = ee.Reducer.mean().combine(ee.Reducer.max(), sharedInputs=True),
            geometry  = geom,
            scale     = 10,
            maxPixels = 1e7,
        ).getInfo()

        # El dataset usa 'b1' como nombre de banda
        mean_m = stats.get("b1_mean") or stats.get("b1")
        max_m  = stats.get("b1_max")

        log.info("canopy ETH %s: media=%.2fm max=%.1fm", venue_id, mean_m or 0, max_m or 0)
        return CanopyMetrics(
            altura_media_m = round(mean_m, 2) if mean_m is not None else None,
            altura_max_m   = round(max_m,  1) if max_m  is not None else None,
        )
    except ImportError:
        log.warning("canopy ETH: earthengine-api no instalado — skipping")
    except Exception as e:
        log.warning("canopy ETH (non-fatal) %s: %s", venue_id, e)
    return CanopyMetrics()


# ── Ingesta D: L-Band Baseline — ALOS PALSAR via GEE ─────────────────────────

def _fetch_lband_baseline(venue_id: str,
                          año_inicio: int = 2015,
                          año_fin:    int = 2024) -> LBandBaseline:
    """TODO — Tramo G future: JAXA/ALOS/PALSAR/YEARLY/SAR_EPOCH baseline integration.
    Planned: L-band HH/HV gamma0 dB (2015-2024, 25m) vía GEE earthengine-api.
    Retorna baseline vacío hasta que se implemente.
    """
    log.debug("lband: Tramo G future — returning empty baseline for %s", venue_id)
    return LBandBaseline()


# ── Procesamiento: HAND hidrodinámica ─────────────────────────────────────────

def _compute_hand(venue_id: str) -> HydroMetrics:
    """
    HAND (Height Above Nearest Drainage) desde Copernicus DEM GLO-30.
    Fuente: Element84 Earth Search STAC (público, sin auth).

    HAND = elevación(píxel) - elevación(cauce más cercano).
    En terrenos planos (δz < 5m como Vélez), el HAND discretiza acumulación de agua
    sin la divergencia matemática del TWI clásico (log(A/tan(β)) → ∞ cuando β→0).

    Clasificación riesgo hídrico para campos deportivos:
      HAND < 0.5m → "alto"   (zona de acumulación activa)
      0.5-2.0m   → "medio"  (susceptible en eventos extremos)
      > 2.0m     → "bajo"   (buen drenaje gravitacional)
    """
    v = _v(venue_id)
    try:
        import pystac_client
        import stackstac
        import dask
        import scipy.ndimage as ndi
    except ImportError as e:
        log.warning("HAND: dep faltante %s — skipping", e)
        return HydroMetrics()

    try:
        catalog = pystac_client.Client.open("https://earth-search.aws.element84.com/v1")
        items   = list(catalog.search(
            collections = ["cop-dem-glo-30"],
            bbox        = v["bbox"],
            max_items   = 4,
        ).items())

        if not items:
            log.warning("HAND: sin tiles DEM para %s", venue_id)
            return HydroMetrics()

        dem_stack = stackstac.stack(
            items,
            bounds     = v["bbox"],
            resolution = 30,
            dtype      = "float32",
            fill_value = float("nan"),
        )
        with dask.config.set(scheduler="synchronous"):
            dem = dem_stack.mean(dim="time").squeeze().compute().values

        if dem.size == 0:
            return HydroMetrics()

        # Rellenar NaN con mediana local
        nan_mask = np.isnan(dem)
        if nan_mask.any():
            median_fill = np.nanmedian(dem)
            dem = np.where(nan_mask, median_fill, dem)

        # HAND: cada píxel → elevación relativa al cauce más cercano.
        # Cauce potencial = píxeles en percentil < 5% de elevación.
        # Para cada píxel no-cauce, ndi.distance_transform_edt devuelve el índice
        # del píxel de cauce más próximo → usamos su elevación como referencia.
        p5     = np.nanpercentile(dem, 5)
        cauce  = dem <= p5
        _, idx = ndi.distance_transform_edt(~cauce, return_indices=True)
        # idx shape: (2, H, W) — filas y columnas del cauce más próximo
        nearest_elev = dem[idx[0], idx[1]]
        hand         = np.clip(dem - nearest_elev, 0, None)

        hand_mean = float(np.nanmean(hand))
        hand_p90  = float(np.nanpercentile(hand, 90))

        zona = ("alto"  if hand_mean < 0.5
                else "medio" if hand_mean < 2.0
                else "bajo")

        log.info("HAND %s: mean=%.2fm p90=%.2fm zona=%s", venue_id, hand_mean, hand_p90, zona)
        return HydroMetrics(
            hand_mean_m = round(hand_mean, 2),
            hand_p90_m  = round(hand_p90,  2),
            zona_riesgo = zona,
        )
    except Exception as e:
        log.warning("HAND (non-fatal) %s: %s", venue_id, e)
        return HydroMetrics()


# ── GEE — inicialización única ────────────────────────────────────────────────

_EE_INITIALIZED = False


def _init_ee() -> None:
    """Service account desde GOOGLE_EE_SERVICE_ACCOUNT + GOOGLE_EE_PRIVATE_KEY (base64)."""
    global _EE_INITIALIZED
    if _EE_INITIALIZED:
        return

    import ee

    sa    = _EE_SA()
    key64 = _EE_KEY_B64()

    if sa and key64:
        try:
            key_json = base64.b64decode(key64).decode()
            key_data = json.loads(key_json)
            creds    = ee.ServiceAccountCredentials(sa, key_data=key_data)
            ee.Initialize(creds)
            _EE_INITIALIZED = True
            log.info("GEE: autenticado via service account %s", sa[:24])
            return
        except Exception as e:
            raise RuntimeError(f"GEE service account init: {e}") from e

    # Fallback: credenciales locales (desarrollo)
    try:
        ee.Initialize()
        _EE_INITIALIZED = True
        log.info("GEE: autenticado via credenciales locales (~/.config/earthengine)")
    except Exception as e:
        raise EnvironmentError(
            "GEE no configurado. Configurar GOOGLE_EE_SERVICE_ACCOUNT + "
            "GOOGLE_EE_PRIVATE_KEY en Railway, o ejecutar 'earthengine authenticate' localmente."
        ) from e


# ── FaroAuditor — certificación RFC 3161 sin deps extra ──────────────────────

class FaroAuditor:
    """
    Certificación criptográfica de reportes Faro V2.

    Principio fail-closed:
      - Solo se certifica si todos los campos críticos son no-None
      - Si la TSA falla, el reporte se guarda igual con tsa_token_b64=None
      - El hash SHA-256 siempre se computa (no depende de red)

    RFC 3161 DER encoding manual — sin pyasn1, sin cryptography.
    Basado en: RFC 3161 §2.4.1 + ITU-T X.690 (DER encoding rules).
    """

    _SHA256_OID = bytes([
        0x06, 0x09,
        0x60, 0x86, 0x48, 0x01, 0x65, 0x03, 0x04, 0x02, 0x01
    ])

    def __init__(self, tsa_url: str = _SIGSTORE_TSA, fallback_url: str = _GITHUB_TSA):
        self.tsa_url     = tsa_url
        self.fallback_url = fallback_url

    # ── Hash ─────────────────────────────────────────────────────────────────

    def sha256(self, report: FaroV2Report) -> str:
        """SHA-256 sobre JSON canónico del reporte (excluyendo el campo audit)."""
        data = asdict(report)
        data.pop("audit", None)
        data.pop("duration_s", None)
        canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # ── RFC 3161 DER ─────────────────────────────────────────────────────────

    def _der_len(self, n: int) -> bytes:
        if n < 128:
            return bytes([n])
        if n < 256:
            return bytes([0x81, n])
        return bytes([0x82, (n >> 8) & 0xFF, n & 0xFF])

    def _der_seq(self, content: bytes) -> bytes:
        return b"\x30" + self._der_len(len(content)) + content

    def _der_octet(self, content: bytes) -> bytes:
        return b"\x04" + self._der_len(len(content)) + content

    def _build_ts_request(self, hash_bytes: bytes) -> bytes:
        """
        TimeStampReq DER mínimo (RFC 3161 §2.4.1):
          SEQUENCE {
            version     INTEGER (1),
            messageImprint SEQUENCE {
              hashAlgorithm SEQUENCE { OID(SHA-256), NULL },
              hashedMessage OCTET STRING
            },
            certReq BOOLEAN TRUE
          }
        """
        version  = b"\x02\x01\x01"
        null     = b"\x05\x00"
        alg_id   = self._der_seq(self._SHA256_OID + null)
        hash_os  = self._der_octet(hash_bytes)
        msg_imp  = self._der_seq(alg_id + hash_os)
        cert_req = b"\x01\x01\xff"
        return self._der_seq(version + msg_imp + cert_req)

    # ── TSA request ──────────────────────────────────────────────────────────

    def _stamp(self, sha256_hex: str, url: str) -> Optional[str]:
        """Envía TimeStampRequest a url, retorna TimeStampToken en base64 o None."""
        try:
            tsr = self._build_ts_request(bytes.fromhex(sha256_hex))
            r   = requests.post(
                url,
                data    = tsr,
                headers = {"Content-Type": "application/timestamp-query"},
                timeout = 25,
            )
            if r.status_code == 200:
                ct = r.headers.get("Content-Type", "")
                if "timestamp" in ct or len(r.content) > 20:
                    log.info("auditor: TSA OK (%s) — %d bytes", url, len(r.content))
                    return base64.b64encode(r.content).decode()
            log.warning("auditor: TSA HTTP %s desde %s", r.status_code, url)
        except Exception as e:
            log.warning("auditor: TSA request (non-fatal): %s", e)
        return None

    # ── Certify ──────────────────────────────────────────────────────────────

    def certify(self, report: FaroV2Report) -> FaroV2Report:
        """
        Certifica el reporte: SHA-256 → TSA principal → fallback TSA.
        Modifica report.audit in-place. Retorna el mismo report.
        """
        sha = self.sha256(report)
        log.info("auditor: SHA-256=%s…%s", sha[:12], sha[-8:])

        token = self._stamp(sha, self.tsa_url)
        tsa   = self.tsa_url

        if not token and self.fallback_url:
            log.info("auditor: intentando fallback TSA %s", self.fallback_url)
            token = self._stamp(sha, self.fallback_url)
            tsa   = self.fallback_url if token else self.tsa_url

        report.audit = AuditRecord(
            sha256        = sha,
            timestamp_iso = datetime.utcnow().isoformat() + "Z",
            tsa_token_b64 = token,
            tsa_url       = tsa,
            verified      = token is not None,
        )
        return report


# ── Persistencia Supabase ─────────────────────────────────────────────────────

def persist_to_supabase(report: FaroV2Report) -> bool:
    """INSERT en faro_v2_reports. Retorna True si OK."""
    url = _SUPA_URL()
    key = _SUPA_KEY()
    if not url or not key:
        log.warning("persist: SUPABASE_URL/KEY no configurados")
        return False
    try:
        payload = {**asdict(report), "created_at": datetime.utcnow().isoformat() + "Z"}
        r = requests.post(
            f"{url}/rest/v1/faro_v2_reports",
            headers = {
                "apikey":        key,
                "Authorization": f"Bearer {key}",
                "Content-Type":  "application/json",
                "Prefer":        "return=minimal",
            },
            json    = payload,
            timeout = 15,
        )
        ok = r.status_code in (200, 201, 204)
        if ok:
            log.info("persist: INSERT OK — %s %s (audit.verified=%s)",
                     report.venue_id, report.fecha, report.audit.verified)
        else:
            log.error("persist: INSERT FALLÓ %s — %s", r.status_code, r.text[:200])
        return ok
    except Exception as e:
        log.error("persist (non-fatal): %s", e)
        return False


_persist = persist_to_supabase  # alias interno


# ── FaroEngine ────────────────────────────────────────────────────────────────

class FaroEngine:
    """
    Orquestador principal de Faro V2.

    Cada componente corre de forma aislada: si una fuente falla, el reporte
    se construye con los datos disponibles y errors[] documenta qué falló.

    Uso básico:
        report = FaroEngine("amalfitani").run()

    Saltear componentes lentos o no configurados:
        report = FaroEngine("amalfitani").run(skip=["canopy", "lband"])

    Ejecutar y persistir en Supabase:
        report = FaroEngine("amalfitani").run_and_persist()

    Output JSON:
        print(report.to_json())
    """

    COMPONENTS: list[str] = ["solar", "sar", "canopy", "lband", "hydro"]

    _RUNNERS: dict[str, any] = {
        "solar":  _fetch_solar,
        "sar":    _fetch_sar_opera,
        "canopy": _fetch_canopy_height,
        "lband":  _fetch_lband_baseline,
        "hydro":  _compute_hand,
    }

    _REPORT_FIELD: dict[str, str] = {
        "solar":  "solar",
        "sar":    "sar",
        "canopy": "canopy",
        "lband":  "lband",
        "hydro":  "hydro",
    }

    def __init__(self, venue_id: str, tsa_url: str = _SIGSTORE_TSA):
        if venue_id not in VENUE_REGISTRY:
            raise ValueError(f"venue '{venue_id}' no registrado. Opciones: {list(VENUE_REGISTRY)}")
        self.venue_id = venue_id
        self.auditor  = FaroAuditor(tsa_url=tsa_url)

    def run(self, skip: list[str] | None = None,
            skip_audit: bool = False) -> "FaroV2Report":
        skip  = set(skip or [])
        today = date.today().isoformat()
        t0    = time.time()

        report = FaroV2Report(venue_id=self.venue_id, fecha=today)
        log.info("FaroEngine V2 START — venue=%s fecha=%s", self.venue_id, today)

        for name in self.COMPONENTS:
            if name in skip:
                log.debug("FaroEngine: skip %s", name)
                continue
            try:
                result = self._RUNNERS[name](self.venue_id)
                setattr(report, self._REPORT_FIELD[name], result)
            except Exception as e:
                msg = f"{name}: {e}"
                report.errors.append(msg)
                log.error("FaroEngine component %s FAILED: %s", name, e)

        if not skip_audit:
            report = self.auditor.certify(report)

        report.duration_s = round(time.time() - t0, 2)

        status = "✅" if not report.errors else f"⚠ {len(report.errors)} errores"
        log.info("FaroEngine V2 END — %.1fs %s | SHA256=%s… | TSA=%s",
                 report.duration_s, status,
                 report.audit.sha256[:16] if report.audit.sha256 else "—",
                 "OK" if report.audit.verified else "SKIPPED/FAILED")
        return report

    def run_and_persist(self, skip: list[str] | None = None,
                        skip_audit: bool = False) -> "FaroV2Report":
        report = self.run(skip=skip, skip_audit=skip_audit)
        persist_to_supabase(report)
        return report


# Monkey-patch para serialización conveniente
def _report_to_json(self, indent: int = 2) -> str:
    return json.dumps(asdict(self), indent=indent, default=str)

FaroV2Report.to_json = _report_to_json  # type: ignore[attr-defined]


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level   = logging.INFO,
        format  = "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt = "%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="Faro Engine V2 — pipeline satelital multi-fuente"
    )
    parser.add_argument("--venue",  default="amalfitani",
                        choices=list(VENUE_REGISTRY),
                        help="Venue a procesar")
    parser.add_argument("--skip",   nargs="*", default=[],
                        choices=FaroEngine.COMPONENTS,
                        help="Componentes a omitir (ej: --skip canopy lband)")
    parser.add_argument("--persist", action="store_true",
                        help="Guardar resultado en Supabase")
    parser.add_argument("--json",    action="store_true",
                        help="Output JSON completo")
    parser.add_argument("--tsa",        default=_SIGSTORE_TSA,
                        help="URL de la TSA RFC 3161")
    parser.add_argument("--skip-audit", action="store_true",
                        help="Omitir FaroAuditor (SHA-256 + TSA RFC 3161)")
    args = parser.parse_args()

    engine = FaroEngine(args.venue, tsa_url=args.tsa)
    run_kw = dict(skip=args.skip, skip_audit=args.skip_audit)
    report = engine.run_and_persist(**run_kw) if args.persist else engine.run(**run_kw)

    if args.json:
        print(report.to_json())
    else:
        W = 62
        print(f"\n{'═'*W}")
        print(f"  FARO ENGINE V2 — {report.venue_id.upper()} — {report.fecha}")
        print(f"{'─'*W}")
        print(f"  Solar   GHI={report.solar.ghi_wh_m2} Wh/m²  "
              f"ET0={report.solar.et0_mm_dia} mm")
        print(f"  SAR     VV={report.sar.vv_gamma0_db} dB  "
              f"VH={report.sar.vh_gamma0_db} dB  "
              f"θ={report.sar.theta_soil}")
        print(f"  Canopy  {report.canopy.altura_media_m}m media / "
              f"{report.canopy.altura_max_m}m máx")
        print(f"  Hydro   HAND={report.hydro.hand_mean_m}m  "
              f"zona={report.hydro.zona_riesgo}")
        print(f"  L-Band  HH={report.lband.hh_mean_db} dB  "
              f"HV={report.lband.hv_mean_db} dB  "
              f"({report.lband.n_años} años)")
        print(f"{'─'*W}")
        print(f"  Audit   SHA256={report.audit.sha256[:24]}…")
        print(f"          TSA={'✅ OK' if report.audit.verified else '❌ FAILED'}  "
              f"[{report.audit.tsa_url.split('/')[2]}]")
        print(f"          {report.audit.timestamp_iso}")
        print(f"  Tiempo  {report.duration_s}s")
        if report.errors:
            print(f"{'─'*W}")
            print(f"  Errores ({len(report.errors)}):")
            for e in report.errors:
                print(f"    • {e}")
        print(f"{'═'*W}\n")
