"""
test_sar_continuity.py — pytest suite for sar_continuity.py

All tests are self-contained: no Supabase calls are made.
HTTP calls inside audit_soil_metrics() are intercepted via unittest.mock.patch.
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

import sar_continuity as sc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_response(rows: list[dict]) -> MagicMock:
    """Build a mock requests.Response that returns `rows` as JSON."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = rows
    mock_resp.raise_for_status.return_value = None
    return mock_resp


def _build_row(
    fecha: str,
    vv: float | None = -12.0,
    vh: float | None = -18.0,
    cancha_id: str = "amalfitani",
    venue_id: str = "velez",
) -> dict:
    """Convenience factory for soil_metrics rows."""
    return {
        "id": None,
        "fecha_imagen": fecha,
        "sar_vv_db": vv,
        "sar_vh_db": vh,
        "cancha_id": cancha_id,
        "venue_id": venue_id,
        "fuente": "S1",
    }


# ---------------------------------------------------------------------------
# Test 1 — Deduplication
# ---------------------------------------------------------------------------


def test_audit_deduplication():
    """
    3 rows with identical (cancha_id, fecha_imagen) → unique_dates=1, duplicates=2.
    """
    rows = [
        _build_row("2026-06-01"),
        _build_row("2026-06-01"),
        _build_row("2026-06-01"),
    ]
    mock_resp = _make_mock_response(rows)

    with patch("requests.get", return_value=mock_resp):
        result = sc.audit_soil_metrics(
            supabase_url="https://fake.supabase.co",
            supabase_key="fake-key",
            cancha_id="amalfitani",
        )

    assert result["total_raw"] == 3, f"Expected 3 raw rows, got {result['total_raw']}"
    assert result["unique_dates"] == 1, f"Expected 1 unique date, got {result['unique_dates']}"
    assert result["duplicates"] == 2, f"Expected 2 duplicates, got {result['duplicates']}"


# ---------------------------------------------------------------------------
# Test 2 — Satellite inference
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fecha, expected_satellite",
    [
        ("2026-03-05", "S1A"),       # well before retirement
        ("2026-06-29", "S1A"),       # last S1A acquisition (< 2026-06-30)
        ("2026-06-30", "S1C/D"),     # boundary: first post-retirement day
        ("2026-07-15", "S1C/D"),     # clearly post-retirement
    ],
)
def test_satellite_inference(fecha: str, expected_satellite: str):
    """
    Satellite is inferred from fecha_imagen:
      < 2026-06-30  → S1A
      >= 2026-06-30 → S1C/D
    """
    rows = [_build_row(fecha, vv=-12.0, vh=-18.0)]
    mock_resp = _make_mock_response(rows)

    with patch("requests.get", return_value=mock_resp):
        result = sc.audit_soil_metrics(
            supabase_url="https://fake.supabase.co",
            supabase_key="fake-key",
        )

    assert len(result["records"]) == 1
    assert result["records"][0]["satellite"] == expected_satellite, (
        f"fecha={fecha}: expected {expected_satellite}, got {result['records'][0]['satellite']}"
    )


# ---------------------------------------------------------------------------
# Test 3 — Outlier detection
# ---------------------------------------------------------------------------


def test_outlier_detection():
    """
    VV values [-10, -11, -12, -25.5, -11] → index 3 (-25.5 dB) is an outlier.
    The z-score for -25.5 in this distribution is well beyond 2.5.
    """
    vv_values = [-10.0, -11.0, -12.0, -25.5, -11.0]
    base_dates = [
        "2026-03-01",
        "2026-03-07",
        "2026-03-13",
        "2026-03-19",
        "2026-03-25",
    ]

    rows = [_build_row(d, vv=v) for d, v in zip(base_dates, vv_values)]
    mock_resp = _make_mock_response(rows)

    with patch("requests.get", return_value=mock_resp):
        result = sc.audit_soil_metrics(
            supabase_url="https://fake.supabase.co",
            supabase_key="fake-key",
        )

    outlier_fechas = [o["fecha"] for o in result["outliers"]]
    assert "2026-03-19" in outlier_fechas, (
        f"Expected 2026-03-19 (-25.5 dB) to be flagged as outlier, got: {outlier_fechas}"
    )

    # The outlier's z-score magnitude should exceed 2.5
    outlier_record = next(
        (o for o in result["outliers"] if o["fecha"] == "2026-03-19"), None
    )
    assert outlier_record is not None
    assert abs(outlier_record["z_score"]) > 2.5, (
        f"z-score {outlier_record['z_score']} should exceed 2.5"
    )

    # Non-outlier rows should not be flagged
    for rec in result["records"]:
        if rec["fecha"] != "2026-03-19":
            assert not rec["is_outlier"], f"{rec['fecha']} should not be an outlier"


# ---------------------------------------------------------------------------
# Test 4 — Gap detection
# ---------------------------------------------------------------------------


def test_gap_detection():
    """
    Dates [2026-06-01, 2026-06-07, 2026-06-20]:
      - 2026-06-01 → 2026-06-07 = 6 days (normal revisit, no gap)
      - 2026-06-07 → 2026-06-20 = 13 days (> 8-day threshold → gap detected)
    """
    dates = ["2026-06-01", "2026-06-07", "2026-06-20"]
    rows = [_build_row(d) for d in dates]
    mock_resp = _make_mock_response(rows)

    with patch("requests.get", return_value=mock_resp):
        result = sc.audit_soil_metrics(
            supabase_url="https://fake.supabase.co",
            supabase_key="fake-key",
        )

    gaps = result["gaps"]
    assert len(gaps) == 1, f"Expected exactly 1 gap, found {len(gaps)}: {gaps}"
    assert gaps[0]["from"] == "2026-06-07"
    assert gaps[0]["to"] == "2026-06-20"
    assert gaps[0]["days"] == 13


# ---------------------------------------------------------------------------
# Test 5 — compliance_summary: pass
# ---------------------------------------------------------------------------


def test_compliance_pass():
    """
    Audit result with 0 outliers, 5 gap_days_current, 5% duplicate rate → status='pass'.
    last_acquisition within 30 days of today.
    """
    today_str = date.today().isoformat()

    audit_result = {
        "total_raw": 20,
        "unique_dates": 19,   # 1 duplicate → 5% rate
        "duplicates": 1,
        "outliers": [],
        "gap_days_current": 5,
        "last_acquisition": today_str,
        "records": [],
    }

    summary = sc.compliance_summary(audit_result)

    assert summary["status"] == "pass", (
        f"Expected 'pass', got '{summary['status']}'. Checks: {summary['checks']}"
    )
    for chk in summary["checks"]:
        assert chk["status"] == "pass", (
            f"Check '{chk['name']}' should be pass, got '{chk['status']}': {chk['detail']}"
        )


# ---------------------------------------------------------------------------
# Test 6 — compliance_summary: warn/fail on gap
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "gap_days, expected_status",
    [
        (16, "warn"),   # 15-21 range → warn
        (22, "fail"),   # >21 → fail
    ],
)
def test_compliance_warn_gap(gap_days: int, expected_status: str):
    """
    gap_days_current in 15-21 range → warn; >21 → fail.
    The overall status must be at least as severe as the expected level.
    """
    from datetime import timedelta

    recent_date = (date.today() - timedelta(days=gap_days)).isoformat()

    audit_result = {
        "total_raw": 10,
        "unique_dates": 10,
        "duplicates": 0,
        "outliers": [],
        "gap_days_current": gap_days,
        "last_acquisition": recent_date,
        "records": [],
    }

    summary = sc.compliance_summary(audit_result)

    severity_order = {"pass": 0, "warn": 1, "fail": 2}
    actual_severity = severity_order.get(summary["status"], -1)
    expected_severity = severity_order.get(expected_status, -1)

    assert actual_severity >= expected_severity, (
        f"gap_days={gap_days}: expected status >= '{expected_status}', "
        f"got '{summary['status']}'. Checks: {summary['checks']}"
    )

    # The gap_days_current check specifically should match expected_status
    gap_check = next(
        (c for c in summary["checks"] if c["name"] == "gap_days_current"), None
    )
    assert gap_check is not None
    assert gap_check["status"] == expected_status, (
        f"gap_days_current check: expected '{expected_status}', got '{gap_check['status']}'"
    )


# ---------------------------------------------------------------------------
# Test 7 — change-point detection
# ---------------------------------------------------------------------------


def test_change_point_detection():
    """
    Stable VV around -12 dB followed by a jump to -20 dB should trigger a
    change-point detection at the jump date.
    """
    dates_vv = [
        ("2026-03-01", -12.0),
        ("2026-03-07", -11.8),
        ("2026-03-13", -12.2),
        ("2026-03-19", -11.9),
        ("2026-03-25", -12.1),
        # Abrupt change
        ("2026-03-31", -20.0),
        ("2026-04-06", -19.8),
        ("2026-04-12", -20.2),
        ("2026-04-18", -19.9),
        ("2026-04-24", -20.1),
    ]

    records = [
        {
            "fecha": d,
            "vv": v,
            "vh": v - 6.0,
            "satellite": "S1A",
            "is_outlier": False,
        }
        for d, v in dates_vv
    ]

    change_points = sc.run_change_point_detection(records)

    assert len(change_points) > 0, (
        "Expected at least one change point to be detected at the -12 → -20 dB jump"
    )

    # At minimum, one change-point should be near the transition zone
    cp_fechas = [cp["fecha"] for cp in change_points]
    transition_detected = any(
        f in cp_fechas for f in ["2026-03-19", "2026-03-25", "2026-03-31", "2026-04-06"]
    )
    assert transition_detected, (
        f"Expected a change point near the Mar 25 – Apr 06 transition zone. "
        f"Got change points at: {cp_fechas}"
    )

    # delta values should correctly reflect the direction of change
    for cp in change_points:
        assert "delta_from_mean" in cp, "Missing delta_from_mean in change point dict"
        assert "vv_db" in cp, "Missing vv_db in change point dict"
        assert "fecha" in cp, "Missing fecha in change point dict"


# ---------------------------------------------------------------------------
# Bonus: test network error handling
# ---------------------------------------------------------------------------


def test_audit_network_error_returns_error_dict():
    """
    If the HTTP call fails, audit_soil_metrics should return a dict with
    an 'error' key rather than raising an exception.
    """
    import requests as _requests

    with patch("requests.get", side_effect=_requests.exceptions.ConnectionError("timeout")):
        result = sc.audit_soil_metrics(
            supabase_url="https://fake.supabase.co",
            supabase_key="fake-key",
        )

    assert "error" in result, "Expected 'error' key in result on network failure"
    assert result["total_raw"] == 0
    assert result["records"] == []
