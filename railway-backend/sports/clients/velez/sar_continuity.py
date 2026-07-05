"""
sar_continuity.py — Sentinel-1 SAR data continuity audit for Amalfitani cancha.

Context:
  - Sentinel-1A retired 2026-06-29.  Last SAR acquisition in DB: 2026-06-29.
  - S1C/D will fill the gap once operational.
  - This module audits soil_metrics (Supabase), detects duplicates, outliers, and
    temporal gaps, and produces a timeline PNG for reporting.

Dependencies (stdlib only):
  requests, json, os, sys, math, datetime, statistics
  matplotlib is optional — all plotting code is wrapped in try/except ImportError.
"""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import date, datetime, timedelta
from statistics import mean, stdev
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_TODAY = date.today()
_S1A_RETIREMENT = date(2026, 6, 29)


def _date_from_str(s: str) -> date:
    """Parse YYYY-MM-DD string to date object. Returns date.min on failure."""
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return date.min


def _infer_satellite(fecha_imagen: str) -> str:
    """
    Infer satellite from acquisition date.
    fecha_imagen < '2026-06-30'  → 'S1A'
    fecha_imagen >= '2026-06-30' → 'S1C/D'
    """
    d = _date_from_str(fecha_imagen)
    if d == date.min:
        return "unknown"
    return "S1A" if d < date(2026, 6, 30) else "S1C/D"


def _zscore(values: list[float]) -> list[float]:
    """
    Robust modified z-score using Median Absolute Deviation (MAD).
    More resistant to outlier inflation than classical mean/stdev z-score.
    Formula: z = 0.6745 * (x - median) / MAD  (Iglewicz & Hoaglin 1993)
    Returns classical z-score when MAD == 0 (homogeneous data).
    """
    if len(values) < 2:
        return [0.0] * len(values)
    sorted_v = sorted(values)
    n = len(sorted_v)
    med = sorted_v[n // 2] if n % 2 else (sorted_v[n // 2 - 1] + sorted_v[n // 2]) / 2
    mad = sorted([abs(v - med) for v in values])[n // 2] if n % 2 else \
          (sorted([abs(v - med) for v in values])[n // 2 - 1] +
           sorted([abs(v - med) for v in values])[n // 2]) / 2
    if mad == 0.0:
        # Fallback to classical z-score
        m = mean(values)
        s = stdev(values) if len(values) > 1 else 0.0
        return [(v - m) / s if s > 0 else 0.0 for v in values]
    return [0.6745 * (v - med) / mad for v in values]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def audit_soil_metrics(
    supabase_url: str,
    supabase_key: str,
    cancha_id: str = "amalfitani",
    since: str = "2026-01-01",
) -> dict:
    """
    Query soil_metrics via Supabase REST API, deduplicate, and return a
    comprehensive audit dict.

    Returns
    -------
    dict with keys:
        total_raw, unique_dates, duplicates, satellite_counts,
        outliers, gaps, last_acquisition, gap_days_current,
        vv_mean, vv_std, vh_mean, vh_std, records
    """
    # ------------------------------------------------------------------
    # 1. Fetch rows from Supabase REST
    # ------------------------------------------------------------------
    endpoint = f"{supabase_url.rstrip('/')}/rest/v1/soil_metrics"
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    params = {
        "select": "id,fecha_imagen,sar_vv_db,sar_vh_db,cancha_id,venue_id,fuente",
        "cancha_id": f"eq.{cancha_id}",
        "fecha_imagen": f"gte.{since}",
        "order": "fecha_imagen.asc",
        "limit": "5000",
    }

    try:
        resp = requests.get(endpoint, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        raw_rows: list[dict] = resp.json()
    except requests.exceptions.RequestException as exc:
        # Return a minimal error dict so callers can distinguish network failure
        return {
            "error": str(exc),
            "total_raw": 0,
            "unique_dates": 0,
            "duplicates": 0,
            "satellite_counts": {},
            "outliers": [],
            "gaps": [],
            "last_acquisition": None,
            "gap_days_current": None,
            "vv_mean": None,
            "vv_std": None,
            "vh_mean": None,
            "vh_std": None,
            "records": [],
        }

    total_raw = len(raw_rows)

    # ------------------------------------------------------------------
    # 2. Deduplicate: keep first occurrence of each (cancha_id, fecha_imagen)
    # ------------------------------------------------------------------
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for row in raw_rows:
        key = (row.get("cancha_id", cancha_id), row.get("fecha_imagen", ""))
        if key not in seen:
            seen.add(key)
            deduped.append(row)

    unique_dates = len(deduped)
    duplicates = total_raw - unique_dates

    # ------------------------------------------------------------------
    # 3. Build per-date raw duplicate counts (for charting)
    # ------------------------------------------------------------------
    date_counts: dict[str, int] = {}
    for row in raw_rows:
        fd = row.get("fecha_imagen", "unknown")
        date_counts[fd] = date_counts.get(fd, 0) + 1

    # ------------------------------------------------------------------
    # 4. Infer satellite, collect VV/VH values
    # ------------------------------------------------------------------
    records: list[dict] = []
    vv_all: list[float] = []
    vh_all: list[float] = []

    for row in deduped:
        fecha = row.get("fecha_imagen", "")
        vv = row.get("sar_vv_db")
        vh = row.get("sar_vh_db")
        satellite = _infer_satellite(fecha)

        # Coerce to float; keep None if truly missing
        try:
            vv_f = float(vv) if vv is not None else None
        except (TypeError, ValueError):
            vv_f = None
        try:
            vh_f = float(vh) if vh is not None else None
        except (TypeError, ValueError):
            vh_f = None

        if vv_f is not None:
            vv_all.append(vv_f)
        if vh_f is not None:
            vh_all.append(vh_f)

        records.append(
            {
                "fecha": fecha,
                "vv": vv_f,
                "vh": vh_f,
                "satellite": satellite,
                "is_outlier": False,  # filled below
                "duplicate_count": date_counts.get(fecha, 1),
            }
        )

    # ------------------------------------------------------------------
    # 5. Satellite counts
    # ------------------------------------------------------------------
    satellite_counts: dict[str, int] = {}
    for rec in records:
        sat = rec["satellite"]
        satellite_counts[sat] = satellite_counts.get(sat, 0) + 1

    # ------------------------------------------------------------------
    # 6. Z-score outlier detection (VV and VH independently)
    # ------------------------------------------------------------------
    vv_records = [(i, r) for i, r in enumerate(records) if r["vv"] is not None]
    vh_records = [(i, r) for i, r in enumerate(records) if r["vh"] is not None]

    vv_zscores = _zscore([r["vv"] for _, r in vv_records])
    vh_zscores = _zscore([r["vh"] for _, r in vh_records])

    Z_THRESHOLD = 2.5

    outlier_indices: set[int] = set()
    for (idx, _), z in zip(vv_records, vv_zscores):
        if abs(z) > Z_THRESHOLD:
            outlier_indices.add(idx)
            records[idx]["vv_zscore"] = round(z, 3)

    for (idx, _), z in zip(vh_records, vh_zscores):
        if abs(z) > Z_THRESHOLD:
            outlier_indices.add(idx)
            if "vv_zscore" not in records[idx]:
                records[idx]["vh_zscore"] = round(z, 3)

    for idx in outlier_indices:
        records[idx]["is_outlier"] = True

    outliers = [
        {
            "fecha": records[i]["fecha"],
            "vv_db": records[i]["vv"],
            "vh_db": records[i]["vh"],
            "z_score": records[i].get("vv_zscore", records[i].get("vh_zscore", None)),
        }
        for i in sorted(outlier_indices)
    ]

    # ------------------------------------------------------------------
    # 7. Temporal gap detection (> 8 days between consecutive acquisitions)
    # ------------------------------------------------------------------
    sorted_dates = sorted(
        [r["fecha"] for r in records if r["fecha"]],
        key=lambda s: _date_from_str(s),
    )

    gaps: list[dict] = []
    GAP_THRESHOLD_DAYS = 8

    for i in range(1, len(sorted_dates)):
        d_prev = _date_from_str(sorted_dates[i - 1])
        d_curr = _date_from_str(sorted_dates[i])
        if d_prev == date.min or d_curr == date.min:
            continue
        delta = (d_curr - d_prev).days
        if delta > GAP_THRESHOLD_DAYS:
            gaps.append(
                {
                    "from": sorted_dates[i - 1],
                    "to": sorted_dates[i],
                    "days": delta,
                }
            )

    # ------------------------------------------------------------------
    # 8. Current gap (days since last acquisition)
    # ------------------------------------------------------------------
    last_acquisition: str | None = sorted_dates[-1] if sorted_dates else None
    if last_acquisition:
        last_date = _date_from_str(last_acquisition)
        gap_days_current = (_TODAY - last_date).days
    else:
        gap_days_current = None

    # ------------------------------------------------------------------
    # 9. Summary statistics
    # ------------------------------------------------------------------
    vv_mean_val = round(mean(vv_all), 4) if vv_all else None
    vv_std_val = round(stdev(vv_all), 4) if len(vv_all) >= 2 else None
    vh_mean_val = round(mean(vh_all), 4) if vh_all else None
    vh_std_val = round(stdev(vh_all), 4) if len(vh_all) >= 2 else None

    return {
        "total_raw": total_raw,
        "unique_dates": unique_dates,
        "duplicates": duplicates,
        "satellite_counts": satellite_counts,
        "outliers": outliers,
        "gaps": gaps,
        "last_acquisition": last_acquisition,
        "gap_days_current": gap_days_current,
        "vv_mean": vv_mean_val,
        "vv_std": vv_std_val,
        "vh_mean": vh_mean_val,
        "vh_std": vh_std_val,
        # Internal use for generate_sar_timeline_png
        "_date_counts": date_counts,
        "records": records,
    }


# ---------------------------------------------------------------------------
# Timeline PNG generation
# ---------------------------------------------------------------------------


def generate_sar_timeline_png(audit_result: dict, output_path: str) -> bool:
    """
    Produce a 2-panel figure:
      Top:    VV + VH time series with outlier markers and S1A retirement shading.
      Bottom: Bar chart of duplicate counts per acquisition date.

    Returns True on success, False if matplotlib is unavailable or an error occurs.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")  # non-interactive backend
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        from matplotlib.patches import Patch
    except ImportError:
        print(
            "[sar_continuity] matplotlib not available — skipping PNG generation.",
            file=sys.stderr,
        )
        return False

    try:
        records = audit_result.get("records", [])
        date_counts: dict[str, int] = audit_result.get("_date_counts", {})
        vv_mean = audit_result.get("vv_mean")
        vv_std = audit_result.get("vv_std")

        # Sort records by date
        records_sorted = sorted(
            [r for r in records if r.get("fecha")],
            key=lambda r: _date_from_str(r["fecha"]),
        )

        # Convert to Python date objects for matplotlib
        def to_mpl_date(fecha: str):
            return _date_from_str(fecha)

        dates_all = [to_mpl_date(r["fecha"]) for r in records_sorted]
        vv_vals = [r["vv"] for r in records_sorted]
        vh_vals = [r["vh"] for r in records_sorted]
        is_outlier = [r["is_outlier"] for r in records_sorted]

        fig, (ax_ts, ax_dup) = plt.subplots(
            2, 1, figsize=(14, 8), gridspec_kw={"height_ratios": [3, 1.5]}
        )
        fig.patch.set_facecolor("#0f1117")
        for ax in (ax_ts, ax_dup):
            ax.set_facecolor("#1a1d27")
            for spine in ax.spines.values():
                spine.set_edgecolor("#444")
            ax.tick_params(colors="#ccc", labelsize=9)

        # ------------------------------------------------------------------
        # Top panel: time series
        # ------------------------------------------------------------------
        normal_kw = {"linewidth": 1.2, "markersize": 5, "alpha": 0.9}

        # VV — normal points
        vv_dates_n = [d for d, v, o in zip(dates_all, vv_vals, is_outlier) if v is not None and not o]
        vv_vals_n = [v for v, o in zip(vv_vals, is_outlier) if v is not None and not o]
        ax_ts.plot(vv_dates_n, vv_vals_n, "o-", color="#4a9eff", label="VV (dB)", **normal_kw)

        # VH — normal points
        vh_dates_n = [d for d, v, o in zip(dates_all, vh_vals, is_outlier) if v is not None and not o]
        vh_vals_n = [v for v, o in zip(vh_vals, is_outlier) if v is not None and not o]
        ax_ts.plot(vh_dates_n, vh_vals_n, "o-", color="#ff9a3c", label="VH (dB)", **normal_kw)

        # Outlier markers (red X)
        out_dates = [d for d, o in zip(dates_all, is_outlier) if o]
        out_vv = [v for v, o in zip(vv_vals, is_outlier) if o]
        if out_dates and any(v is not None for v in out_vv):
            ax_ts.scatter(
                out_dates,
                [v for v in out_vv if v is not None],
                marker="X",
                color="red",
                s=120,
                zorder=5,
                label="Outlier",
            )

        # VV mean ± 2.5σ reference lines
        if vv_mean is not None and vv_std is not None:
            for sign, style in [(1, "--"), (-1, "--")]:
                ax_ts.axhline(
                    vv_mean + sign * 2.5 * vv_std,
                    color="#4a9eff",
                    linestyle=style,
                    linewidth=0.8,
                    alpha=0.5,
                    label="VV mean +/- 2.5sd" if sign == 1 else None,
                )

        # Shade the post-S1A retirement gap period
        retirement_date = _S1A_RETIREMENT + timedelta(days=1)  # June 30
        shade_end = _TODAY
        ax_ts.axvspan(
            retirement_date,
            shade_end,
            color="red",
            alpha=0.12,
            label=f"S1A gap ({(shade_end - retirement_date).days}d)",
        )
        ax_ts.axvline(
            _S1A_RETIREMENT,
            color="red",
            linestyle=":",
            linewidth=1.0,
            alpha=0.7,
        )
        ax_ts.text(
            _S1A_RETIREMENT,
            ax_ts.get_ylim()[0] if ax_ts.get_ylim()[0] != 0 else -22,
            "  S1A retired",
            color="red",
            fontsize=7,
            alpha=0.8,
            va="bottom",
        )

        ax_ts.set_title(
            "Sentinel-1 Backscatter Time Series — Amalfitani (2026)",
            color="white",
            fontsize=12,
            pad=10,
        )
        ax_ts.set_ylabel("Backscatter (dB)", color="#ccc", fontsize=10)
        ax_ts.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
        ax_ts.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
        plt.setp(ax_ts.xaxis.get_majorticklabels(), rotation=30, ha="right")
        ax_ts.legend(
            facecolor="#2a2d3a",
            edgecolor="#555",
            labelcolor="white",
            fontsize=8,
            loc="lower left",
        )
        ax_ts.grid(True, color="#333", linewidth=0.5, linestyle=":")

        # ------------------------------------------------------------------
        # Bottom panel: duplicate bar chart
        # ------------------------------------------------------------------
        sorted_dc = sorted(date_counts.items(), key=lambda x: _date_from_str(x[0]))
        bar_dates = [_date_from_str(k) for k, _ in sorted_dc]
        bar_counts = [v for _, v in sorted_dc]
        bar_colors = [
            "red" if c > 3 else ("#f0c040" if c >= 2 else "#3cb371")
            for c in bar_counts
        ]

        ax_dup.bar(bar_dates, bar_counts, color=bar_colors, width=3, alpha=0.85)
        ax_dup.axhline(1, color="#555", linewidth=0.8, linestyle="--")
        ax_dup.set_title(
            "Duplicate rows per acquisition date (database quality)",
            color="white",
            fontsize=10,
            pad=8,
        )
        ax_dup.set_ylabel("Row count", color="#ccc", fontsize=9)
        ax_dup.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
        ax_dup.xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
        plt.setp(ax_dup.xaxis.get_majorticklabels(), rotation=30, ha="right")
        ax_dup.grid(True, color="#333", linewidth=0.5, linestyle=":", axis="y")

        legend_patches = [
            Patch(color="red", label=">3 duplicates"),
            Patch(color="#f0c040", label="2-3 duplicates"),
            Patch(color="#3cb371", label="1 (clean)"),
        ]
        ax_dup.legend(
            handles=legend_patches,
            facecolor="#2a2d3a",
            edgecolor="#555",
            labelcolor="white",
            fontsize=8,
            loc="upper right",
        )

        plt.tight_layout(pad=1.5)
        plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)
        print(f"[sar_continuity] Timeline PNG saved: {output_path}")
        return True

    except Exception as exc:
        print(f"[sar_continuity] PNG generation failed: {exc}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Change-point detection (no external library)
# ---------------------------------------------------------------------------


def run_change_point_detection(records: list) -> list[dict]:
    """
    Rolling-window change point detection on VV backscatter.

    Algorithm:
      - Window = 5 dates (sorted chronologically).
      - For each date i (window//2 … N - window//2), compute rolling mean of
        the surrounding window.
      - Flag if |VV[i] - rolling_mean| > 2.0 dB.

    Parameters
    ----------
    records : list of dicts with keys 'fecha', 'vv', 'vh', ...

    Returns
    -------
    List of {"fecha": str, "vv_db": float, "delta_from_mean": float}
    """
    WINDOW = 5
    DELTA_THRESHOLD = 2.0  # dB

    # Filter to records that have a valid VV value and sort by date
    valid = sorted(
        [r for r in records if r.get("vv") is not None and r.get("fecha")],
        key=lambda r: _date_from_str(r["fecha"]),
    )

    if len(valid) < WINDOW:
        return []

    change_points: list[dict] = []
    half = WINDOW // 2

    for i in range(half, len(valid) - half):
        window_vv = [valid[j]["vv"] for j in range(i - half, i + half + 1)]
        rolling_mean = mean(window_vv)
        delta = valid[i]["vv"] - rolling_mean

        if abs(delta) > DELTA_THRESHOLD:
            change_points.append(
                {
                    "fecha": valid[i]["fecha"],
                    "vv_db": round(valid[i]["vv"], 3),
                    "delta_from_mean": round(delta, 3),
                }
            )

    return change_points


# ---------------------------------------------------------------------------
# Compliance summary
# ---------------------------------------------------------------------------


def compliance_summary(audit_result: dict) -> dict:
    """
    Run a set of compliance checks against an audit_result dict.

    Checks
    ------
    1. outlier_rate  ≤ 5% of unique dates         → pass (else warn)
    2. gap_days_current:  ≤14 → pass, 15-21 → warn, >21 → fail
    3. duplicate_rate ≤ 20% of total rows          → pass (else warn)
    4. last_acquisition within 30 days of today    → pass (else fail)

    Returns
    -------
    {"status": "pass"|"warn"|"fail", "checks": [...]}
    """
    checks: list[dict] = []
    overall = "pass"

    def _escalate(current: str, new_severity: str) -> str:
        order = {"pass": 0, "warn": 1, "fail": 2}
        return new_severity if order.get(new_severity, 0) > order.get(current, 0) else current

    unique_dates = audit_result.get("unique_dates", 0)
    total_raw = audit_result.get("total_raw", 0)
    outliers = audit_result.get("outliers", [])
    gap_days = audit_result.get("gap_days_current")
    last_acq = audit_result.get("last_acquisition")
    duplicates = audit_result.get("duplicates", 0)

    # ------------------------------------------------------------------
    # Check 1: outlier rate
    # ------------------------------------------------------------------
    outlier_count = len(outliers)
    outlier_rate = (outlier_count / unique_dates * 100) if unique_dates > 0 else 0.0
    c1_status = "pass" if outlier_rate <= 5.0 else "warn"
    checks.append(
        {
            "name": "outlier_rate",
            "status": c1_status,
            "detail": f"{outlier_count}/{unique_dates} dates ({outlier_rate:.1f}%) — threshold 5%",
        }
    )
    overall = _escalate(overall, c1_status)

    # ------------------------------------------------------------------
    # Check 2: current gap
    # ------------------------------------------------------------------
    if gap_days is None:
        c2_status = "warn"
        c2_detail = "gap_days_current unknown (no acquisition dates found)"
    elif gap_days <= 14:
        c2_status = "pass"
        c2_detail = f"{gap_days} days since last acquisition — within normal range"
    elif gap_days <= 21:
        c2_status = "warn"
        c2_detail = f"{gap_days} days since last acquisition — S1A retired, awaiting S1C/D"
    else:
        c2_status = "fail"
        c2_detail = f"{gap_days} days since last acquisition — critical data gap"
    checks.append({"name": "gap_days_current", "status": c2_status, "detail": c2_detail})
    overall = _escalate(overall, c2_status)

    # ------------------------------------------------------------------
    # Check 3: duplicate rate
    # ------------------------------------------------------------------
    dup_rate = (duplicates / total_raw * 100) if total_raw > 0 else 0.0
    c3_status = "pass" if dup_rate <= 20.0 else "warn"
    checks.append(
        {
            "name": "duplicate_rate",
            "status": c3_status,
            "detail": f"{duplicates}/{total_raw} rows duplicated ({dup_rate:.1f}%) — threshold 20%",
        }
    )
    overall = _escalate(overall, c3_status)

    # ------------------------------------------------------------------
    # Check 4: recency of last acquisition
    # ------------------------------------------------------------------
    if last_acq is None:
        c4_status = "fail"
        c4_detail = "No acquisition dates found in DB"
    else:
        last_d = _date_from_str(last_acq)
        days_since = (_TODAY - last_d).days
        if days_since <= 30:
            c4_status = "pass"
            c4_detail = f"Last acquisition {last_acq} ({days_since}d ago) — within 30-day window"
        else:
            c4_status = "fail"
            c4_detail = f"Last acquisition {last_acq} ({days_since}d ago) — exceeds 30-day recency threshold"
    checks.append({"name": "last_acquisition_recency", "status": c4_status, "detail": c4_detail})
    overall = _escalate(overall, c4_status)

    return {"status": overall, "checks": checks}


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    import json

    url = os.environ.get(
        "SUPABASE_URL", "https://xljxpzudgwhbzcnrvylo.supabase.co"
    )
    key = os.environ.get("SUPABASE_KEY", "")

    if not key:
        print(
            "Warning: SUPABASE_KEY not set — REST calls will use anon permissions.",
            file=sys.stderr,
        )

    print("Auditing soil_metrics …", file=sys.stderr)
    result = audit_soil_metrics(url, key)

    # Print summary (exclude verbose records list)
    summary_keys = {k: v for k, v in result.items() if k not in ("records", "_date_counts")}
    print(json.dumps(summary_keys, indent=2, default=str))

    # Generate timeline PNG
    png_path = os.path.join(os.path.dirname(__file__), "sar_continuity.png")
    ok = generate_sar_timeline_png(result, png_path)
    if ok:
        print(f"Timeline saved: {png_path}", file=sys.stderr)

    # Change-point detection
    cp = run_change_point_detection(result.get("records", []))
    if cp:
        print(f"\nChange points detected: {len(cp)}", file=sys.stderr)
        for c in cp:
            print(
                f"  {c['fecha']}  VV={c['vv_db']:.2f} dB  delta={c['delta_from_mean']:+.2f} dB",
                file=sys.stderr,
            )
    else:
        print("No change points detected.", file=sys.stderr)

    # Compliance
    comp = compliance_summary(result)
    print(f"\nCompliance: {comp['status']}", file=sys.stderr)
    for chk in comp["checks"]:
        icon = {"pass": "OK", "warn": "WARN", "fail": "FAIL"}.get(chk["status"], "??")
        print(f"  [{icon}] {chk['name']}: {chk['detail']}", file=sys.stderr)
