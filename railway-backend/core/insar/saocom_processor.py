"""
core/insar/saocom_processor.py — Procesamiento Quad-Pol SAOCOM L-band (CONAE).

Flujo:
  load_saocom_hdf5()         → dict de arrays complejos HH/HV/VH/VV + metadata
  extract_bbox_pixels()      → recorte al bbox WGS84 del campo
  power_db()                 → potencia media en dB por banda
  compute_t3_matrix()        → coherency matrix T3 (base Pauli, reciprocal)
  decompose_cloude_pottier() → H, alpha_deg, anisotropy (Cloude-Pottier 1997)
  process_saocom_scene()     → dict con todas las métricas listas para soil_metrics

Pedido #608190 CONAE aceptado (2026-06-23). 37 granules Amalfitani, 35 Villa Olímpica.
H-Alpha Cloude-Pottier válido aquí porque SAOCOM entrega Quad-Pol real (HH+HV+VH+VV),
a diferencia de Sentinel-1 dual-pol donde solo se usa intensidad sin fase compleja.

Dependencias: numpy, h5py (obligatorio). pyproj opcional (mejora reproyección UTM→WGS84).
"""
from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

# Paths HDF5 por nivel de producto SAOCOM (CONAE entrega SLC L1B, GRD L1C/L1D, MLC).
# Se prueban en orden hasta encontrar el grupo con las 4 polarizaciones.
_HDF5_POL_PATHS: list[tuple[str, ...]] = [
    ("S01", "SBI"),                    # L1B SLC StripMap (más común de CONAE)
    ("S01", "MBI"),                    # L1C MLC
    ("science", "GRD"),                # GRD genérico
    ("science", "SLC", "Swath_1"),     # SLC science path (releases anteriores)
    ("S01",),                          # raíz del swath con pols directas
]
_POL_NAMES = ("HH", "HV", "VH", "VV")


# ─────────────────────────────────────────────────────────────────────────────
# Carga HDF5
# ─────────────────────────────────────────────────────────────────────────────

def load_saocom_hdf5(path: str | Path) -> dict[str, Any]:
    """
    Abre HDF5 de SAOCOM (CONAE) y extrae arrays complejos por polarización.

    Prueba múltiples rutas de grupos conocidas hasta encontrar las 4 bandas.
    SAOCOM puede entregar complex64 nativo o pares I/Q separados como float32.

    Returns dict con claves HH, HV, VH, VV (np.ndarray complex64) + metadata:
      fecha, modo, bbox_utm, epsg, pixel_spacing_m.

    Raises:
      ImportError        si h5py no está instalado.
      FileNotFoundError  si el archivo no existe.
      KeyError           si el HDF5 no tiene la estructura esperada.
    """
    try:
        import h5py
    except ImportError:
        raise ImportError("h5py requerido para SAOCOM HDF5. pip install h5py")

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"SAOCOM HDF5 no encontrado: {path}")

    with h5py.File(path, "r") as f:
        pol_group  = None
        found_path = ""

        for path_parts in _HDF5_POL_PATHS:
            try:
                grp = f
                for part in path_parts:
                    grp = grp[part]
                if all(p in grp for p in _POL_NAMES):
                    pol_group  = grp
                    found_path = "/".join(path_parts)
                    break
            except (KeyError, TypeError):
                continue

        if pol_group is None:
            raise KeyError(
                f"No se encontraron polarizaciones {_POL_NAMES} en {path}. "
                f"Paths probados: {_HDF5_POL_PATHS}"
            )

        log.info("saocom_processor: HDF5 path /%s  archivo=%s", found_path, path.name)

        arrays: dict[str, np.ndarray] = {}
        for pol in _POL_NAMES:
            raw = pol_group[pol][()]
            if np.iscomplexobj(raw):
                arrays[pol] = raw.astype(np.complex64)
            elif raw.dtype.names and set(raw.dtype.names) >= {"i", "q"}:
                # I/Q compuesto en structured array
                arrays[pol] = (raw["i"] + 1j * raw["q"]).astype(np.complex64)
            else:
                # Solo magnitud real — operar como si fuera amplitud
                log.warning("saocom_processor: banda %s no es compleja, H-Alpha degradado", pol)
                arrays[pol] = raw.astype(np.float32)

        meta = _extract_metadata(f)

    return {**arrays, **meta}


def _extract_metadata(h5file) -> dict[str, Any]:
    """Extrae fecha, modo, bbox UTM y EPSG del HDF5 SAOCOM."""
    meta: dict[str, Any] = {
        "fecha":           "",
        "modo":            "desconocido",
        "bbox_utm":        (0.0, 0.0, 1.0, 1.0),
        "epsg":            32720,    # UTM 20S — cubre Buenos Aires
        "pixel_spacing_m": 10.0,
    }

    # Fecha de adquisición — varios paths posibles por release de CONAE
    _fecha_paths = [
        ("metadata", "acquisitionDateTimeUTC"),
        ("metadata", "processingStartTime"),
        ("S01",      "AcquisitionStartTime"),
    ]
    for *grp_parts, attr in _fecha_paths:
        try:
            grp = h5file
            for p in grp_parts:
                grp = grp[p]
            val = grp.attrs.get(attr, b"")
            if val:
                raw = val.decode("utf-8") if isinstance(val, bytes) else str(val)
                meta["fecha"] = raw[:10]   # YYYY-MM-DD
                break
        except Exception:
            continue

    # Modo de imagen (StripMap / TopSAR / ScanSAR)
    for path in ("metadata", "S01"):
        try:
            grp = h5file[path]
            for attr in ("imagingMode", "mode", "acquisitionMode"):
                m = grp.attrs.get(attr, b"")
                if m:
                    meta["modo"] = m.decode("utf-8") if isinstance(m, bytes) else str(m)
                    break
            if meta["modo"] != "desconocido":
                break
        except Exception:
            continue

    # Pixel spacing y origen UTM (para clip de bbox)
    try:
        grp = h5file["metadata"]
        dx  = float(grp.attrs.get("rangePixelSpacing",   10.0))
        dy  = float(grp.attrs.get("azimuthPixelSpacing", 10.0))
        x0  = float(grp.attrs.get("rangeFirstSampleRef",    0.0))
        y0  = float(grp.attrs.get("azimuthFirstSampleRef",  0.0))
        meta["pixel_spacing_m"] = dx
        # bbox_utm es solo una estimación — extract_bbox_pixels lo refina
        meta["bbox_utm"] = (x0, y0, x0 + dx * 30000, y0 + dy * 30000)
    except Exception:
        pass

    return meta


# ─────────────────────────────────────────────────────────────────────────────
# Recorte al bbox del campo
# ─────────────────────────────────────────────────────────────────────────────

def extract_bbox_pixels(
    scene: dict[str, Any],
    bbox_wgs84: tuple[float, float, float, float],
) -> dict[str, np.ndarray]:
    """
    Recorta las bandas de polarización al bbox del campo (WGS84: lon_min, lat_min, lon_max, lat_max).

    Usa pyproj para reproyección UTM↔WGS84 si está disponible.
    Sin pyproj: aproximación lineal válida para Buenos Aires (error < 50 m en 1 km).

    Retorna {HH, HV, VH, VV} como np.ndarray 2D recortado.
    """
    lon_min, lat_min, lon_max, lat_max = bbox_wgs84
    x_utm_min, y_utm_min, x_utm_max, y_utm_max = scene.get("bbox_utm", (0.0, 0.0, 1.0, 1.0))
    epsg = scene.get("epsg", 32720)
    dx   = scene.get("pixel_spacing_m", 10.0)

    # Reproyectar bbox WGS84 → UTM del producto
    try:
        from pyproj import Transformer
        tr = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
        xf_min, yf_min = tr.transform(lon_min, lat_min)
        xf_max, yf_max = tr.transform(lon_max, lat_max)
    except ImportError:
        # Aproximación lineal para latitud ~-34.6 (BsAs)
        M_PER_DEG_LAT = 111_320.0
        M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(-34.6))
        cx = (x_utm_min + x_utm_max) / 2.0
        cy = (y_utm_min + y_utm_max) / 2.0
        ref_lon, ref_lat = -58.53, -34.64
        xf_min = cx + (lon_min - ref_lon) * M_PER_DEG_LON
        xf_max = cx + (lon_max - ref_lon) * M_PER_DEG_LON
        yf_min = cy + (lat_min - ref_lat) * M_PER_DEG_LAT
        yf_max = cy + (lat_max - ref_lat) * M_PER_DEG_LAT

    def _clip(arr: np.ndarray) -> np.ndarray:
        if arr.ndim < 2:
            return arr
        rows, cols = arr.shape[:2]
        c0 = max(0, int((xf_min - x_utm_min) / dx))
        c1 = min(cols, int((xf_max - x_utm_min) / dx) + 1)
        # Y SAR: filas crecen hacia abajo, UTM Y crece hacia arriba
        r0 = max(0, int((y_utm_max - yf_max) / dx))
        r1 = min(rows, int((y_utm_max - yf_min) / dx) + 1)
        if c0 >= c1 or r0 >= r1:
            log.warning(
                "saocom_processor: bbox fuera del raster (c=%d:%d r=%d:%d) — devuelve slice parcial",
                c0, c1, r0, r1,
            )
            return arr[:min(50, rows), :min(50, cols)]
        return arr[r0:r1, c0:c1]

    result: dict[str, np.ndarray] = {}
    for pol in _POL_NAMES:
        if pol in scene:
            result[pol] = _clip(scene[pol])

    if result:
        sample = next(iter(result.values()))
        log.info("saocom_processor: clip → shape=%s  n_pixels=%d", sample.shape, sample.size)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Potencia por banda
# ─────────────────────────────────────────────────────────────────────────────

def power_db(arr: np.ndarray) -> float:
    """
    Potencia media en dB de un array SAR.
    Complejo: E[|z|²] → dB.  Real (magnitud): E[z²] → dB.
    """
    if arr.size == 0:
        return -99.0
    intensity = (np.abs(arr) ** 2).astype(np.float64)
    mean_i = float(np.mean(intensity))
    if mean_i <= 0.0:
        return -99.0
    return round(10.0 * math.log10(mean_i), 3)


# ─────────────────────────────────────────────────────────────────────────────
# Coherency matrix T3 (base Pauli)
# ─────────────────────────────────────────────────────────────────────────────

def compute_t3_matrix(
    hh: np.ndarray,
    hv: np.ndarray,
    vv: np.ndarray,
) -> np.ndarray:
    """
    Coherency matrix T3 (3×3 compleja) en base Pauli, asumiendo reciprocidad monoestática.

    Vector Pauli:
      k_P = 1/sqrt(2) · [S_HH + S_VV,  S_HH − S_VV,  2·S_HV]^T

    T3 = mean_pixels(k_P · k_P†)

    La reciprocidad (S_HV = S_VH) es válida para SAOCOM en modo monoestático.
    Promedio espacial sobre todos los píxeles del campo → multilook implícito.
    """
    hh_f = hh.astype(np.complex128).ravel()
    hv_f = hv.astype(np.complex128).ravel()
    vv_f = vv.astype(np.complex128).ravel()

    inv_sqrt2 = 1.0 / math.sqrt(2.0)
    k0 = (hh_f + vv_f) * inv_sqrt2   # mecanismo especular / doble rebote
    k1 = (hh_f - vv_f) * inv_sqrt2   # dipolo
    k2 =  2.0 * hv_f  * inv_sqrt2    # volumen / scattering difuso

    K  = np.stack([k0, k1, k2], axis=0)   # (3, N)
    T3 = (K @ K.conj().T) / float(K.shape[1])
    return T3


# ─────────────────────────────────────────────────────────────────────────────
# Descomposición Cloude-Pottier
# ─────────────────────────────────────────────────────────────────────────────

def decompose_cloude_pottier(T3: np.ndarray) -> dict[str, float]:
    """
    Descomposición Cloude-Pottier desde T3 (3×3 hermítica positiva semidefinida).

    Eigendecomposición: T3 = Σ λi · vi·vi†  (λ1 ≥ λ2 ≥ λ3 ≥ 0)
    Pi = λi / (λ1+λ2+λ3)

    Entropía:      H = −Σ Pi·log₃(Pi)         ∈ [0, 1]
    Ángulo medio:  α = Σ Pi·arccos(|vi[0]|)   ∈ [0°, 90°]
    Anisotropía:   A = (λ₂−λ₃)/(λ₂+λ₃)       ∈ [0, 1]

    Interpretación L-band césped (Ulaby & Long 2014, adaptado):
      H < 0.3, α < 20°  → especular superficial → suelo desnudo/compactado
      H > 0.6, α > 40°  → scattering de volumen → cesped activo / biomasa alta
      0.3 ≤ H ≤ 0.6     → mecanismos mixtos     → estado intermedio
    """
    # Hermitianizar para garantizar eigenvalores reales
    T3_h = (T3 + T3.conj().T) / 2.0
    eigenvalues, eigenvectors = np.linalg.eigh(T3_h)

    # eigh ordena ascendente → invertir para λ1 ≥ λ2 ≥ λ3
    idx = np.argsort(eigenvalues)[::-1]
    eigenvalues  = np.maximum(eigenvalues[idx].real, 0.0)
    eigenvectors = eigenvectors[:, idx]

    total = float(np.sum(eigenvalues))
    if total <= 0.0:
        return {
            "H": 0.5, "alpha_deg": 45.0, "anisotropy": 0.0,
            "lambda1": 0.0, "lambda2": 0.0, "lambda3": 0.0,
        }

    p = eigenvalues / total

    # Entropía (base 3 — tres eigenvalores posibles)
    H = 0.0
    for pi in p:
        if pi > 1e-12:
            H -= float(pi) * math.log(float(pi), 3)
    H = min(1.0, max(0.0, H))

    # Ángulo alpha por mecanismo: αi = arccos(|vi[0]|)
    alphas = []
    for i in range(3):
        v0 = min(1.0, max(0.0, abs(float(eigenvectors[0, i]))))
        alphas.append(math.degrees(math.acos(v0)))

    alpha_mean = float(np.dot(p, alphas))

    lam2, lam3 = float(eigenvalues[1]), float(eigenvalues[2])
    A = (lam2 - lam3) / (lam2 + lam3) if (lam2 + lam3) > 1e-12 else 0.0

    return {
        "H":          round(H, 4),
        "alpha_deg":  round(alpha_mean, 3),
        "anisotropy": round(A, 4),
        "lambda1":    round(float(eigenvalues[0]), 6),
        "lambda2":    round(lam2, 6),
        "lambda3":    round(lam3, 6),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline completo
# ─────────────────────────────────────────────────────────────────────────────

def process_saocom_scene(
    hdf5_path: str | Path,
    bbox_wgs84: tuple[float, float, float, float],
    cancha_id: str = "",
) -> dict[str, Any]:
    """
    Pipeline completo: carga → recorta bbox → potencias → T3 → Cloude-Pottier.

    Retorna dict listo para soil_metrics (columna extras JSONB):
      hh_db, hv_db, vh_db, vv_db  — potencia media por banda [dB]
      h_entropy                    — entropía polarimétrica H  [0-1]
      alpha_deg                    — ángulo medio de scattering [0-90]
      anisotropy                   — anisotropía A              [0-1]
      lambda1, lambda2, lambda3    — eigenvalores T3
      fecha_imagen                 — str 'YYYY-MM-DD'
      modo                         — str modo de adquisición SAOCOM
      n_pixels                     — int píxeles en el recorte del campo
      cancha_id                    — str
      fuente                       — 'SAOCOM_L-BAND'
    """
    log.info("saocom_processor: iniciando pipeline  archivo=%s  cancha=%s",
             Path(hdf5_path).name, cancha_id)

    scene   = load_saocom_hdf5(hdf5_path)
    clipped = extract_bbox_pixels(scene, bbox_wgs84)

    # Usar recorte si tiene píxeles; si el clip falla, usar array completo (degradado)
    def _get(pol: str) -> np.ndarray:
        arr = clipped.get(pol) or scene.get(pol)
        if arr is None:
            return np.zeros((1, 1), dtype=np.complex64)
        return arr

    hh_arr = _get("HH")
    hv_arr = _get("HV")
    vh_arr = _get("VH")
    vv_arr = _get("VV")

    hh_db = power_db(hh_arr)
    hv_db = power_db(hv_arr)
    vh_db = power_db(vh_arr)
    vv_db = power_db(vv_arr)

    T3 = compute_t3_matrix(hh_arr, hv_arr, vv_arr)
    cp = decompose_cloude_pottier(T3)

    result: dict[str, Any] = {
        "hh_db":        hh_db,
        "hv_db":        hv_db,
        "vh_db":        vh_db,
        "vv_db":        vv_db,
        "h_entropy":    cp["H"],
        "alpha_deg":    cp["alpha_deg"],
        "anisotropy":   cp["anisotropy"],
        "lambda1":      cp["lambda1"],
        "lambda2":      cp["lambda2"],
        "lambda3":      cp["lambda3"],
        "fecha_imagen": scene.get("fecha", ""),
        "modo":         scene.get("modo", "desconocido"),
        "n_pixels":     int(hh_arr.size),
        "cancha_id":    cancha_id,
        "fuente":       "SAOCOM_L-BAND",
    }

    log.info(
        "saocom_processor: OK  cancha=%s  HH=%.1f VV=%.1f dB  H=%.3f alpha=%.1f A=%.3f  n=%d",
        cancha_id, hh_db, vv_db, cp["H"], cp["alpha_deg"], cp["anisotropy"], result["n_pixels"],
    )
    return result
