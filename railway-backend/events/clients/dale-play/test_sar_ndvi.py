"""
test_sar_ndvi.py — Prueba de concepto: SAR VV/VH → NDVI sintético para Amalfitani.

Metodología:
  1. Busca pares SAR+NDVI en ventana de entrenamiento (90 días antes del show)
  2. Para cada fecha SAR, busca la imagen S2 más cercana con <30% nubes
  3. Ajusta regresión lineal: NDVI = a*VV + b*VH + c
  4. Aplica al SAR del 29-30/05 (show Airbag) para estimar NDVI post-show
  5. Reporta R², RMSE, NDVI estimado y delta vs baseline

Uso:
  python test_sar_ndvi.py
  python test_sar_ndvi.py --train-days 120 --cloud-max 40
"""
from __future__ import annotations
import argparse, json, logging, os, sys
from datetime import date, datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("test_sar_ndvi")

# ── Config ────────────────────────────────────────────────────────────────────
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from dale_play_config import VENUE_BBOX
MINX, MINY, MAXX, MAXY = VENUE_BBOX

SHOW_DATE   = date(2026, 5, 31)
SAR_DATES   = ["2026-05-29", "2026-05-30"]   # pasadas S1 confirmadas en el catálogo

_E84_STAC = "https://earth-search.aws.element84.com/v1"
_PC_STAC  = "https://planetarycomputer.microsoft.com/api/stac/v1"
_S2_COL   = "sentinel-2-l2a"
_S1_COL   = "sentinel-1-grd"

NDVI_BUENO     = 0.55   # césped sano Bermuda (verano/otoño BA)
NDVI_DEGRADADO = 0.35


# ── Helpers STAC ──────────────────────────────────────────────────────────────

def _e84_search(date_from: str, date_to: str, limit: int = 30) -> list:
    import requests
    r = requests.post(
        f"{_E84_STAC}/search",
        json={
            "collections": [_S2_COL],
            "bbox":        [MINX, MINY, MAXX, MAXY],
            "datetime":    f"{date_from}T00:00:00Z/{date_to}T23:59:59Z",
            "limit":       limit,
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("features", [])


def _pc_search(date_from: str, date_to: str, limit: int = 20) -> list:
    import requests
    r = requests.post(
        f"{_PC_STAC}/search",
        json={
            "collections": [_S1_COL],
            "bbox":        [MINX, MINY, MAXX, MAXY],
            "datetime":    f"{date_from}T00:00:00Z/{date_to}T23:59:59Z",
            "limit":       limit,
            "sortby":      [{"field": "datetime", "direction": "asc"}],
        },
        timeout=20,
    )
    r.raise_for_status()
    return r.json().get("features", [])


# ── Extracción de datos satelitales ──────────────────────────────────────────

def _extract_ndvi(item: dict, date_str: str) -> float | None:
    """Lee NDVI del bbox Amalfitani de una escena S2 L2A via COG."""
    import numpy as np, rasterio
    from rasterio.windows import from_bounds
    from rasterio.warp import transform_bounds
    from rasterio.enums import Resampling

    os.environ["AWS_NO_SIGN_REQUEST"] = "YES"
    assets = item.get("assets", {})

    def _read(key, shape_ref=None):
        href = assets.get(key, {}).get("href", "")
        if not href:
            return None
        try:
            with rasterio.open(href) as src:
                native = transform_bounds("EPSG:4326", src.crs, MINX, MINY, MAXX, MAXY)
                win    = from_bounds(*native, transform=src.transform)
                if shape_ref:
                    return src.read(1, window=win, out_shape=shape_ref,
                                    resampling=Resampling.nearest).astype("float32")
                return src.read(1, window=win).astype("float32")
        except Exception as e:
            log.debug("read %s [%s]: %s", key, date_str, e)
            return None

    red = _read("red")
    nir = _read("nir")
    if red is None or nir is None:
        return None

    scl = _read("scl", shape_ref=(red.shape[0], red.shape[1]))
    if scl is not None:
        mask = np.isin(scl.astype(int), [4, 5, 6, 7])
    else:
        mask = (red > 0) & (nir > 0) & (red < 10000) & (nir < 10000)

    if mask.sum() < 4:
        return None

    r_v = red[mask] / 10_000.0
    n_v = nir[mask] / 10_000.0
    return float(round(float(np.mean((n_v - r_v) / (n_v + r_v + 1e-9))), 3))


def _extract_sar_vv_vh(item: dict, label: str) -> tuple[float | None, float | None]:
    """Lee VV y VH backscatter (dB) de un ítem S1 GRD via PC token."""
    import numpy as np, rasterio, requests
    from rasterio.windows import Window

    item_id = item.get("id", "")
    assets  = item.get("assets", {})
    i_lon0, i_lat0, i_lon1, i_lat1 = item.get("bbox", [0, 0, 1, 1])

    tok_r = requests.get(
        f"https://planetarycomputer.microsoft.com/api/sas/v1/token/{_S1_COL}",
        timeout=10,
    )
    token = tok_r.json().get("token", "") if tok_r.ok else ""

    results = {}
    for pol in ("vv", "vh"):
        href = assets.get(pol, {}).get("href", "")
        if not href:
            results[pol] = None
            continue
        if token:
            href = f"{href}?{token}"
        try:
            with rasterio.open(href) as src:
                H, W = src.shape
                fx0 = (MINX - i_lon0) / (i_lon1 - i_lon0)
                fx1 = (MAXX - i_lon0) / (i_lon1 - i_lon0)
                fy0 = (i_lat1 - MAXY) / (i_lat1 - i_lat0)
                fy1 = (i_lat1 - MINY) / (i_lat1 - i_lat0)
                c0  = max(0, int(fx0 * W));  c1 = min(W, max(c0 + 20, int(fx1 * W)))
                r0  = max(0, int(fy0 * H));  r1 = min(H, max(r0 + 20, int(fy1 * H)))
                data = src.read(1, window=Window(c0, r0, c1-c0, r1-r0)).astype("float32")
            valid = (data > 0) & np.isfinite(data)
            if valid.sum() < 4:
                results[pol] = None
            else:
                results[pol] = float(round(10.0 * float(np.log10(float(np.mean(data[valid])))), 2))
        except Exception as e:
            log.debug("%s read %s [%s]: %s", label, pol, item_id, e)
            results[pol] = None

    return results.get("vv"), results.get("vh")


# ── Construcción del dataset de entrenamiento ─────────────────────────────────

def build_training_data(train_days: int, cloud_max: float) -> list[dict]:
    """
    Para cada escena SAR en el período de entrenamiento, busca la imagen S2
    más cercana (±5 días) con nubosidad < cloud_max y extrae NDVI real.
    Retorna lista de dicts {date, vv_db, vh_db, ndvi}.
    """
    train_end   = SHOW_DATE - timedelta(days=2)
    train_start = train_end - timedelta(days=train_days)
    log.info("Ventana entrenamiento: %s → %s", train_start, train_end)

    sar_items = _pc_search(str(train_start), str(train_end))
    log.info("Escenas SAR encontradas: %d", len(sar_items))

    pairs = []
    for s1_item in sar_items:
        dt_str = (s1_item.get("properties", {}).get("datetime") or "")[:10]
        if not dt_str:
            continue

        dt = date.fromisoformat(dt_str)
        # Busca S2 en ±5 días alrededor del pase SAR
        s2_from = str(dt - timedelta(days=5))
        s2_to   = str(dt + timedelta(days=5))
        s2_feats = _e84_search(s2_from, s2_to)

        # Filtra por nubosidad y elige la más cercana al pase SAR
        clean = [
            f for f in s2_feats
            if f.get("properties", {}).get("eo:cloud_cover", 100) <= cloud_max
        ]
        if not clean:
            log.debug("SAR %s: sin S2 limpia en ±5d (cloud_max=%.0f%%)", dt_str, cloud_max)
            continue

        # Más cercana en días
        best_s2 = min(
            clean,
            key=lambda f: abs(
                (date.fromisoformat((f.get("properties", {}).get("datetime") or dt_str)[:10]) - dt).days
            ),
        )
        s2_date = (best_s2.get("properties", {}).get("datetime") or "")[:10]
        cc      = best_s2.get("properties", {}).get("eo:cloud_cover", "?")

        log.info("Par SAR %s ↔ S2 %s (%.1f%% nubes) — extrayendo...", dt_str, s2_date, cc)

        vv, vh = _extract_sar_vv_vh(s1_item, f"SAR[{dt_str}]")
        if vv is None:
            log.warning("SAR %s: VV no disponible, salteando", dt_str)
            continue

        ndvi = _extract_ndvi(best_s2, s2_date)
        if ndvi is None:
            log.warning("S2 %s: NDVI no extraíble (píxeles nulos), salteando", s2_date)
            continue

        log.info("  → VV=%.2f dB  VH=%s dB  NDVI=%.3f", vv, f"{vh:.2f}" if vh else "N/A", ndvi)
        pairs.append({
            "sar_date":  dt_str,
            "s2_date":   s2_date,
            "delta_days": abs((date.fromisoformat(s2_date) - dt).days),
            "vv_db":     vv,
            "vh_db":     vh,
            "ndvi":      ndvi,
            "s2_cloud":  cc,
        })

    log.info("Pares válidos para entrenamiento: %d", len(pairs))
    return pairs


# ── Regresión ─────────────────────────────────────────────────────────────────

def fit_model(pairs: list[dict]) -> dict:
    """
    Ajusta NDVI = a*VV + b*VH + c (OLS).
    Si VH no está disponible en todos los puntos, usa solo VV.
    """
    import numpy as np

    X_vv   = [p["vv_db"] for p in pairs]
    X_vh   = [p["vh_db"] for p in pairs]
    y_ndvi = [p["ndvi"]  for p in pairs]

    has_vh = all(v is not None for v in X_vh)
    n      = len(pairs)

    if n < 2:
        return {"ok": False, "reason": f"solo {n} pares — mínimo 2"}

    if has_vh and n >= 3:
        X = np.column_stack([X_vv, X_vh, np.ones(n)])
        feat_names = ["VV", "VH", "intercept"]
    else:
        log.info("Modelo univariado (solo VV) — VH incompleto o n<3")
        X = np.column_stack([X_vv, np.ones(n)])
        feat_names = ["VV", "intercept"]
        has_vh = False

    y = np.array(y_ndvi)

    # OLS: beta = (X'X)^-1 X'y
    try:
        beta = np.linalg.lstsq(X, y, rcond=None)[0]
    except Exception as e:
        return {"ok": False, "reason": str(e)}

    y_hat = X @ beta
    ss_res = float(np.sum((y - y_hat) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2   = float(round(1 - ss_res / ss_tot, 3)) if ss_tot > 0 else 0.0
    rmse = float(round(float(np.sqrt(ss_res / n)), 4))

    return {
        "ok":         True,
        "n":          n,
        "coef":       dict(zip(feat_names, [round(float(b), 4) for b in beta])),
        "r2":         r2,
        "rmse":       rmse,
        "has_vh":     has_vh,
        "beta":       beta.tolist(),
        "feat_names": feat_names,
    }


def predict_ndvi(model: dict, vv_db: float, vh_db: float | None) -> float:
    import numpy as np
    beta = np.array(model["beta"])
    if model["has_vh"] and vh_db is not None:
        x = np.array([vv_db, vh_db, 1.0])
    else:
        x = np.array([vv_db, 1.0])
    return float(round(float(np.dot(x, beta)), 3))


# ── Estimación post-show ──────────────────────────────────────────────────────

def estimate_post_show(model: dict) -> list[dict]:
    """Extrae SAR de los días del show y estima NDVI sintético."""
    results = []
    for sar_date in SAR_DATES:
        s1_items = _pc_search(sar_date, sar_date)
        if not s1_items:
            log.warning("Sin S1 GRD para %s", sar_date)
            results.append({"date": sar_date, "vv_db": None, "vh_db": None,
                             "ndvi_sintetico": None, "estado": "sin_escena"})
            continue
        item = s1_items[0]
        vv, vh = _extract_sar_vv_vh(item, f"SHOW[{sar_date}]")
        if vv is None:
            results.append({"date": sar_date, "vv_db": None, "vh_db": None,
                             "ndvi_sintetico": None, "estado": "sin_vv"})
            continue
        ndvi_est = predict_ndvi(model, vv, vh)
        results.append({
            "date":           sar_date,
            "vv_db":          vv,
            "vh_db":          vh,
            "ndvi_sintetico": ndvi_est,
            "estado":         "ok",
            "item_id":        item.get("id", ""),
        })
        log.info("Show %s → VV=%.2f dB  NDVI_sint=%.3f", sar_date, vv, ndvi_est)
    return results


# ── Reporte final ──────────────────────────────────────────────────────────────

def print_report(pairs: list[dict], model: dict, post_results: list[dict],
                 ndvi_baseline: float) -> None:

    SEP  = "=" * 62
    SEP2 = "-" * 62

    print(f"\n{SEP}")
    print("  TEST SAR → NDVI SINTÉTICO — Estadio Amalfitani")
    print(f"  Show: Airbag 2026-05-31  |  bbox: [{MINX},{MINY},{MAXX},{MAXY}]")
    print(SEP)

    # Dataset
    print(f"\n{'DATASET DE ENTRENAMIENTO':}")
    print(f"  Pares SAR+NDVI válidos: {len(pairs)}")
    for p in pairs:
        flag = " ⚠️ Δdays>3" if p["delta_days"] > 3 else ""
        print(f"  SAR {p['sar_date']} ↔ S2 {p['s2_date']} "
              f"(Δ{p['delta_days']}d | cloud {p['s2_cloud']:.1f}%) "
              f"→ VV={p['vv_db']:.2f}dB  NDVI={p['ndvi']:.3f}{flag}")

    print(f"\n{SEP2}")

    # Modelo
    if not model["ok"]:
        print(f"\n  ❌ MODELO NO AJUSTADO: {model['reason']}")
        print(f"\n  CONCLUSIÓN: {len(pairs)} pares insuficientes para calibrar.")
        print("  No es viable usar SAR→NDVI sintético en esta condición.\n")
        print(SEP)
        return

    print(f"\n{'MODELO LINEAL':}")
    print(f"  Tipo:    {'VV + VH' if model['has_vh'] else 'solo VV'} → NDVI (OLS)")
    print(f"  n        {model['n']}")
    print(f"  R²       {model['r2']:.3f}   (mínimo útil: 0.70)")
    print(f"  RMSE     {model['rmse']:.4f}  (±{model['rmse']:.3f} NDVI units)")
    for feat, coef in model["coef"].items():
        print(f"  {feat:<12} {coef:+.4f}")

    # Veredicto del modelo
    if model["r2"] >= 0.85:
        grade = "✅ BUENO — apto para estimación"
    elif model["r2"] >= 0.70:
        grade = "⚠️  ACEPTABLE — usar con banda de incertidumbre"
    elif model["r2"] >= 0.50:
        grade = "🟡 DÉBIL — referencial solamente"
    else:
        grade = "❌ INSUFICIENTE — no usar en certificado"
    print(f"\n  Veredicto modelo: {grade}")
    print(f"{SEP2}")

    # Resultados post-show
    print(f"\n{'ESTIMACIÓN POST-SHOW (días del recital)':}")
    ndvi_vals = [r["ndvi_sintetico"] for r in post_results if r["ndvi_sintetico"] is not None]

    for r in post_results:
        if r["estado"] != "ok":
            print(f"  {r['date']}: {r['estado']}")
            continue
        delta = round(r["ndvi_sintetico"] - ndvi_baseline, 3)
        arrow = "▼" if delta < 0 else "▲"
        print(f"  {r['date']}: VV={r['vv_db']:.2f}dB  "
              f"NDVI_sint={r['ndvi_sintetico']:.3f}  "
              f"Δ={arrow}{abs(delta):.3f} vs baseline")

    if ndvi_vals:
        ndvi_mean = round(sum(ndvi_vals) / len(ndvi_vals), 3)
        delta_mean = round(ndvi_mean - ndvi_baseline, 3)

        # Clasificación daño
        if delta_mean < -0.15:
            nivel = "SEVERO"
        elif delta_mean < -0.08:
            nivel = "MODERADO"
        elif delta_mean < -0.03:
            nivel = "LEVE"
        else:
            nivel = "SIN DAÑO"

        print(f"\n  Baseline NDVI (pre-show): {ndvi_baseline:.3f}")
        print(f"  NDVI estimado show:       {ndvi_mean:.3f}  (±{model['rmse']:.3f})")
        print(f"  Delta NDVI:               {delta_mean:+.3f}")
        print(f"  Nivel daño estimado:      {nivel}")

        # Incertidumbre: ¿el delta supera el RMSE?
        if abs(delta_mean) > model["rmse"] * 2:
            conf = "✅ Delta supera 2×RMSE — señal estadísticamente detectada"
        elif abs(delta_mean) > model["rmse"]:
            conf = "⚠️  Delta supera 1×RMSE — señal débil, usar con cautela"
        else:
            conf = "❌ Delta dentro del RMSE — no concluyente"
        print(f"  Confianza señal:          {conf}")

    print(f"\n{SEP2}")

    # Conclusión final
    print(f"\n{'CONCLUSIÓN PARA CERTIFICADO':}")
    if not model["ok"] or not ndvi_vals:
        print("  ❌ No viable — datos insuficientes.")
    elif model["r2"] >= 0.70 and ndvi_vals and abs(delta_mean) > model["rmse"]:
        print("  ✅ SAR→NDVI sintético VIABLE como capa de soporte.")
        print("     Recomendación: incluir en certificado como 'NDVI estimado (SAR)'")
        print("     con banda de incertidumbre ±{:.3f}. NO reemplaza NDVI óptico.".format(model["rmse"]))
        print("     Emitir certificado provisional SAR+NDVI-sint hasta imagen limpia.")
    elif model["r2"] >= 0.50:
        print("  🟡 SAR→NDVI viable solo como referencia cualitativa.")
        print("     No incluir valor numérico en certificado — solo indicar tendencia.")
    else:
        print("  ❌ R² insuficiente — no usar SAR→NDVI en certificado.")
        print("     Esperar imagen óptica limpia o emitir certificado SAR-only (compactación).")

    print(f"\n{SEP}\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Test SAR→NDVI sintético Amalfitani")
    parser.add_argument("--train-days", type=int, default=90,
                        help="Días de ventana de entrenamiento antes del show (default: 90)")
    parser.add_argument("--cloud-max", type=float, default=30.0,
                        help="Nubosidad máxima S2 aceptada para entrenamiento (default: 30%%)")
    parser.add_argument("--ndvi-baseline", type=float, default=None,
                        help="NDVI baseline manual (override de búsqueda pre-show)")
    parser.add_argument("--save-json", type=str, default=None,
                        help="Guardar resultados en JSON (path opcional)")
    args = parser.parse_args()

    # Verificar dependencias
    missing = []
    for pkg in ("requests", "numpy", "rasterio"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"❌ Dependencias faltantes: {', '.join(missing)}")
        print(f"   Instalar con: pip install {' '.join(missing)}")
        sys.exit(1)

    log.info("=== TEST SAR→NDVI SINTÉTICO ===")
    log.info("Train window: %d días  |  Cloud max: %.0f%%", args.train_days, args.cloud_max)

    # Paso 1: construir dataset de entrenamiento
    log.info("--- Paso 1: buscando pares SAR+NDVI (entrenamiento) ---")
    pairs = build_training_data(args.train_days, args.cloud_max)

    if len(pairs) < 2:
        log.warning("Solo %d par(es) encontrado(s). Probando con cloud_max=50%%...", len(pairs))
        pairs = build_training_data(args.train_days, 50.0)

    # Baseline NDVI (pre-show limpio)
    if args.ndvi_baseline is not None:
        ndvi_baseline = args.ndvi_baseline
        log.info("Baseline NDVI (override manual): %.3f", ndvi_baseline)
    elif pairs:
        # Usa el promedio de los pares de entrenamiento como baseline
        ndvi_baseline = round(sum(p["ndvi"] for p in pairs) / len(pairs), 3)
        log.info("Baseline NDVI (promedio training): %.3f", ndvi_baseline)
    else:
        ndvi_baseline = NDVI_BUENO
        log.warning("Sin pares de entrenamiento — usando baseline por defecto: %.3f", ndvi_baseline)

    # Paso 2: ajustar modelo
    log.info("--- Paso 2: ajustando regresión ---")
    model = fit_model(pairs)
    if model.get("ok"):
        log.info("Modelo: R²=%.3f  RMSE=%.4f  coef=%s", model["r2"], model["rmse"], model["coef"])
    else:
        log.warning("Modelo no ajustado: %s", model.get("reason"))

    # Paso 3: estimación post-show
    log.info("--- Paso 3: estimando NDVI en días del show ---")
    post_results = estimate_post_show(model) if model.get("ok") else []

    # Paso 4: reporte
    print_report(pairs, model, post_results, ndvi_baseline)

    # Guardar JSON si se pide
    if args.save_json:
        out = {
            "show_id":       "airbag_2026-05-31",
            "ndvi_baseline": ndvi_baseline,
            "training_pairs": pairs,
            "model":          {k: v for k, v in model.items() if k != "beta"},
            "post_show":      post_results,
        }
        pathlib.Path(args.save_json).write_text(json.dumps(out, indent=2, ensure_ascii=False))
        log.info("Resultados guardados en %s", args.save_json)


if __name__ == "__main__":
    main()
