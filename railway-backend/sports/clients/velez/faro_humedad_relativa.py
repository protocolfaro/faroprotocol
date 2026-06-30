"""
faro_humedad_relativa.py — Estimación honesta de estado hídrico del suelo.

NO usa Van Genuchten (requiere calibración TDR local inexistente en Vélez).
Combina SAR change (relativo, confiable) + ERA5-Land como referencia calibrada.

Salida:
  - cambio_relativo: dB (negativo = más seco, positivo = más húmedo)
  - era5_ref_pct: % (volumétrico × 100, capa 7-28cm — zona raíz principal)
  - status: "MAS_SECO" | "SECANDO" | "ESTABLE" | "HUMEDO" | "MAS_HUMEDO"
  - confianza: 0.0 – 1.0
  - razon: string explicativo

Confianza:
  - 0.75 si SAR change + ERA5 coinciden en dirección
  - 0.60 si solo ERA5 disponible
  - 0.50 si solo SAR change disponible
  - 0.35 si ninguno disponible
"""
from __future__ import annotations
import logging

log = logging.getLogger(__name__)


def get_humidity_relative(
    sar_vv_today:   float | None,
    sar_vv_6d_ago:  float | None,
    era5_sm_7_28cm: float | None = None,   # m³/m³
    era5_sm_0_7cm:  float | None = None,   # m³/m³ (capa superficial, alternativa)
) -> dict:
    """
    Estimación relativa del estado hídrico del suelo.
    SAR change es el proxy principal; ERA5 calibra la referencia absoluta.
    """
    # ── SAR change ────────────────────────────────────────────────────────────
    sar_change: float | None = None
    if sar_vv_today is not None and sar_vv_6d_ago is not None:
        sar_change = round(sar_vv_today - sar_vv_6d_ago, 2)

    # ── ERA5 referencia (prioridad 7-28cm, fallback 0-7cm) ───────────────────
    era5_ref_raw: float | None = era5_sm_7_28cm if era5_sm_7_28cm is not None else era5_sm_0_7cm
    era5_ref_pct: float | None = round(era5_ref_raw * 100, 1) if era5_ref_raw is not None else None

    # ── Clasificación relativa ────────────────────────────────────────────────
    if sar_change is None and era5_ref_pct is None:
        return {
            "cambio_relativo": None,
            "era5_ref_pct":    None,
            "status":          "SIN_DATO",
            "confianza":       0.35,
            "razon":           "Sin SAR ni ERA5 disponibles",
        }

    # Determine direction from SAR (primary signal)
    if sar_change is not None:
        if   sar_change < -1.5: sar_dir = "MAS_SECO"
        elif sar_change < -0.5: sar_dir = "SECANDO"
        elif sar_change <  0.5: sar_dir = "ESTABLE"
        elif sar_change <  1.5: sar_dir = "HUMEDO"
        else:                   sar_dir = "MAS_HUMEDO"
    else:
        sar_dir = None

    # ERA5 reference category (umbrales Franco-Arenoso Deportivo, Vélez)
    if era5_ref_pct is not None:
        if   era5_ref_pct < 20: era5_dir = "MAS_SECO"
        elif era5_ref_pct < 28: era5_dir = "SECANDO"
        elif era5_ref_pct < 38: era5_dir = "ESTABLE"
        elif era5_ref_pct < 48: era5_dir = "HUMEDO"
        else:                   era5_dir = "MAS_HUMEDO"
    else:
        era5_dir = None

    # ── Confianza y status final ──────────────────────────────────────────────
    if sar_dir is not None and era5_dir is not None:
        # Both available
        if sar_dir == era5_dir:
            status     = sar_dir
            confianza  = 0.80           # SAR + ERA5 concuerdan
        else:
            # Discrepancia — usar SAR como primario, bajar confianza
            status     = sar_dir
            confianza  = 0.55
        razon = (f"SAR {sar_change:+.1f} dB ({sar_dir}) · "
                 f"ERA5 {era5_ref_pct:.0f}% ({era5_dir})")
    elif sar_dir is not None:
        status    = sar_dir
        confianza = 0.60
        razon     = f"SAR {sar_change:+.1f} dB vs 6d · ERA5 no disponible"
    else:
        status    = era5_dir
        confianza = 0.55
        razon     = f"ERA5 {era5_ref_pct:.0f}% (sin SAR reciente)"

    return {
        "cambio_relativo": sar_change,
        "era5_ref_pct":    era5_ref_pct,
        "status":          status,        # MAS_SECO | SECANDO | ESTABLE | HUMEDO | MAS_HUMEDO
        "confianza":       confianza,
        "razon":           razon,
    }


def deficit_hidrico_validado(
    et0_serie_7d:    list[float],   # mm/día, últimos 7 días
    precip_serie_7d: list[float],   # mm/día, últimos 7 días
    sar_vv_today:    float | None = None,
    sar_vv_6d:       float | None = None,
) -> dict:
    """
    Balance hídrico corregido con SAR como validador de dirección.
    Sin TDR local, no garantiza mm exactos — sí confirma si hay déficit real.
    """
    et0_total    = sum(et0_serie_7d)   if et0_serie_7d    else 0.0
    precip_total = sum(precip_serie_7d) if precip_serie_7d else 0.0
    deficit_base = et0_total - precip_total

    sar_change: float | None = None
    if sar_vv_today is not None and sar_vv_6d is not None:
        sar_change = sar_vv_today - sar_vv_6d

    if sar_change is not None:
        if sar_change < -0.5:
            # SAR confirma secado — déficit válido
            deficit_final, confianza = max(deficit_base, 0), 0.75
            validation = f"SAR confirma secado ({sar_change:+.1f} dB)"
        elif sar_change > 0.5:
            # SAR detecta humedecimiento — revisar si deficit es real
            deficit_final, confianza = max(deficit_base * 0.6, 0), 0.55
            validation = f"SAR detecta humedecimiento ({sar_change:+.1f} dB), déficit reducido"
        else:
            deficit_final, confianza = max(deficit_base, 0), 0.65
            validation = f"SAR estable ({sar_change:+.1f} dB)"
    else:
        deficit_final, confianza = max(deficit_base, 0), 0.50
        validation = "Sin SAR reciente"

    return {
        "deficit_mm":    round(deficit_final, 1),
        "et0_total_mm":  round(et0_total, 1),
        "precip_total_mm": round(precip_total, 1),
        "confianza":     confianza,
        "razon":         (f"ET₀ {et0_total:.0f}mm − Precip {precip_total:.0f}mm"
                          f" = {deficit_base:.0f}mm · {validation}"),
    }
