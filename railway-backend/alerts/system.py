from __future__ import annotations
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests as _req

log = logging.getLogger(__name__)

_SUPA_URL     = os.environ.get("SUPABASE_URL", "").rstrip("/")
_SUPA_KEY     = os.environ.get("SUPABASE_KEY", "")
_VENUE_ID     = os.environ.get("FARO_VENUE_ID", "velez")
_SLACK_HOOK   = os.environ.get("SLACK_WEBHOOK_URL", "")
_ALERT_EMAIL  = os.environ.get("ALERT_EMAIL", "protocolfaro@gmail.com")


def _hdrs() -> dict:
    return {
        "apikey":        _SUPA_KEY,
        "Authorization": f"Bearer {_SUPA_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "count=none",
    }


def _sb(table: str, params: dict) -> list:
    if not _SUPA_URL or not _SUPA_KEY:
        return []
    try:
        r = _req.get(f"{_SUPA_URL}/rest/v1/{table}",
                     headers=_hdrs(), params=params, timeout=8)
        return r.json() if r.ok and isinstance(r.json(), list) else []
    except Exception:
        return []


def _age_hours(ts: str | None) -> int | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return max(0, int((datetime.now(timezone.utc) - dt).total_seconds() / 3600))
    except Exception:
        return None


class AlertSystem:
    ERA5_LAG_ERR_H:   int = 24
    DPRVI_LAG_ERR_H:  int = 36
    SAR_LAG_ERR_H:    int = 96
    KALMAN_LAG_ERR_H: int = 96
    SCORE_CRIT:       int = 40

    def __init__(self):
        self._cooldowns: dict[str, float] = {}
        self._lock = threading.Lock()

    def _cooled_down(self, key: str, seconds: int = 1800) -> bool:
        now = time.monotonic()
        with self._lock:
            last = self._cooldowns.get(key, 0)
            if now - last >= seconds:
                self._cooldowns[key] = now
                return True
            return False

    def _slack(self, title: str, body: str, color: str = "#e84c4c") -> bool:
        if not _SLACK_HOOK:
            log.warning("SLACK_WEBHOOK_URL not set — alert not sent: %s", title)
            return False
        payload = {
            "text": f":rotating_light: *[{_VENUE_ID.upper()}] {title}*",
            "attachments": [{
                "color": color,
                "text":  body,
                "footer": "Faro Protocol · alerts/system.py",
                "ts":     int(time.time()),
            }],
        }
        try:
            r = _req.post(_SLACK_HOOK, json=payload, timeout=8)
            return r.status_code == 200
        except Exception as exc:
            log.warning("slack: %s", exc)
            return False

    def check_era5_lag(self) -> dict:
        rows = _sb("climate_metrics_sectorial", {
            "order": "created_at.desc", "limit": "1", "select": "created_at",
        })
        if not rows:
            rows = _sb("climate_metrics", {
                "venue_id": f"eq.{_VENUE_ID}",
                "order": "created_at.desc", "limit": "1", "select": "created_at",
            })
        lag = _age_hours(rows[0].get("created_at") if rows else None)
        result = {"service": "era5", "lag_hours": lag, "threshold": self.ERA5_LAG_ERR_H, "fired": False}
        if lag is not None and lag >= self.ERA5_LAG_ERR_H:
            if self._cooled_down("era5"):
                msg = f"ERA5-Land sin actualizar hace *{lag}h* (umbral: {self.ERA5_LAG_ERR_H}h)\n"
                msg += f"Último dato: `{rows[0].get('created_at', 'N/A')[:19] if rows else 'N/A'}`\n"
                msg += "Verificar: GitHub Actions · era5_land_daily.yml · CDS_API_KEY"
                fired = self._slack("ERA5-Land lag crítico", msg)
                result["fired"] = fired
                log.warning("ERA5 alert: lag=%dh", lag)
        return result

    def check_dprvi_lag(self) -> dict:
        rows = _sb("dprvi_metrics", {
            "order": "created_at.desc", "limit": "1", "select": "created_at,cancha_id",
        })
        lag = _age_hours(rows[0].get("created_at") if rows else None)
        result = {"service": "dprvi", "lag_hours": lag, "threshold": self.DPRVI_LAG_ERR_H, "fired": False}
        if lag is not None and lag >= self.DPRVI_LAG_ERR_H:
            if self._cooled_down("dprvi"):
                msg = f"DpRVIc sin actualizar hace *{lag}h* (umbral: {self.DPRVI_LAG_ERR_H}h)\n"
                msg += "Verificar: GCS bucket `faro-dprvi-exports` · GEE script · GCS_SERVICE_ACCOUNT_JSON"
                fired = self._slack("DpRVIc lag crítico", msg, color="#e8b84c")
                result["fired"] = fired
                log.warning("DpRVIc alert: lag=%dh", lag)
        return result

    def check_kalman_lag(self) -> dict:
        rows = _sb("vegetation_metrics", {
            "venue_id":        f"eq.{_VENUE_ID}",
            "ndvi_sintetico":  "not.is.null",
            "order":           "created_at.desc",
            "limit":           "1",
            "select":          "created_at,margen_error_kalman",
        })
        lag = _age_hours(rows[0].get("created_at") if rows else None)
        margen = rows[0].get("margen_error_kalman") if rows else None
        result = {"service": "kalman", "lag_hours": lag, "margen_error": margen, "fired": False}
        if lag is not None and lag >= self.KALMAN_LAG_ERR_H:
            if self._cooled_down("kalman"):
                msg = f"Kalman sin actualizar hace *{lag}h* (umbral: {self.KALMAN_LAG_ERR_H}h)\n"
                msg += "Gap-filling NDVI inactivo — verificar satellite_pipeline."
                fired = self._slack("Kalman lag crítico", msg, color="#e8b84c")
                result["fired"] = fired
        if margen is not None and float(margen) > 0.15:
            if self._cooled_down("kalman_margen"):
                msg = f"Kalman margen_error_kalman = *{margen:.3f}* (umbral: 0.15)\nIncertidumbre alta — usar física de suelos como señal."
                self._slack("Kalman incertidumbre alta", msg, color="#e8b84c")
        return result

    def check_sar_lag(self) -> dict:
        rows = _sb("soil_metrics", {
            "venue_id": f"eq.{_VENUE_ID}",
            "order":    "created_at.desc",
            "limit":    "1",
            "select":   "created_at,sar_vv_db",
        })
        lag = _age_hours(rows[0].get("created_at") if rows else None)
        result = {"service": "sar", "lag_hours": lag, "threshold": self.SAR_LAG_ERR_H, "fired": False}
        if lag is not None and lag >= self.SAR_LAG_ERR_H:
            if self._cooled_down("sar"):
                msg = f"SAR/Sentinel-1 sin actualizar hace *{lag}h* (umbral: {self.SAR_LAG_ERR_H}h)\n"
                msg += "Verificar: faro_sar_s1_backfill.py · CDSE_USER/PASS · PC SAS token"
                self._slack("SAR lag crítico", msg)
                result["fired"] = True
        return result

    def check_score_global(self) -> dict:
        rows = _sb("velez_canchas", {"select": "cancha_id,score,sem"})
        if not rows:
            return {"service": "score_global", "fired": False}
        críticas = [r for r in rows if r.get("sem") == "rojo"]
        score_vals = [r["score"] for r in rows if r.get("score") is not None]
        score_med  = int(sum(score_vals) / len(score_vals)) if score_vals else None
        result = {
            "service":       "score_global",
            "score_median":  score_med,
            "canchas_rojas": len(críticas),
            "fired":         False,
        }
        if score_med is not None and score_med < self.SCORE_CRIT:
            if self._cooled_down("score_global"):
                names = ", ".join(r["cancha_id"] for r in críticas[:5])
                msg = (f"Score global *{score_med}/100* — {len(críticas)} canchas en rojo\n"
                       f"Canchas críticas: {names or 'N/A'}")
                fired = self._slack("Score global crítico", msg)
                result["fired"] = fired
        return result

    def run_checks(self) -> dict:
        checks = {
            "era5":         self.check_era5_lag(),
            "dprvi":        self.check_dprvi_lag(),
            "kalman":       self.check_kalman_lag(),
            "sar":          self.check_sar_lag(),
            "score_global": self.check_score_global(),
        }
        fired = [k for k, v in checks.items() if v.get("fired")]
        log.info("AlertSystem.run_checks: fired=%s", fired or "none")
        return {"timestamp": datetime.now(timezone.utc).isoformat(), "checks": checks, "fired": fired}


_system = AlertSystem()


def run_checks() -> dict:
    return _system.run_checks()


def register_alert_scheduler(scheduler) -> None:
    try:
        from apscheduler.triggers.cron import CronTrigger
        scheduler.add_job(
            run_checks,
            CronTrigger(minute="*/30"),
            id="alert_system_30min",
            replace_existing=True,
        )
        log.info("AlertSystem: scheduled every 30 min")
    except Exception as exc:
        log.warning("AlertSystem scheduler registration: %s", exc)
