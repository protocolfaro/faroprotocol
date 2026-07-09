"""
faro_pipeline_runner.py — Orquestador de pipeline satelital Faro Protocol.

Patrón: PipelineStep con retry automático solo en errores transitorios.

Distinción clave que el código propuesto original ignoraba:
  - Error de red / timeout → retriable (tenacity backoff)
  - fn() retorna None      → NO retriable (cascade interna ya agotó alternativas)
  - ImportError / bug      → NO retriable (problema de entorno, no de red)

Pipeline abort logic:
  required=True  + falla definitiva → aborta cadena, Cerebro lo ve en Supabase
  required=False + falla            → log warning, continúa (ej: insar, faro_v2)

Dependencia nueva: tenacity>=8.2.0 (~200KB, zero deps externas)
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from tenacity import (
    RetryError,
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger("faro.pipeline")

# Errores transitorios que merecen retry: red, timeout, I/O
# ImportError, ValueError, KeyError → bugs, no se reintentan
try:
    import requests as _req_mod
    _TRANSIENT = (ConnectionError, TimeoutError, OSError,
                  _req_mod.exceptions.RequestException)
except ImportError:
    _TRANSIENT = (ConnectionError, TimeoutError, OSError)


class _NoneResultError(Exception):
    """fn() retornó None en un step required — no es error de red, no retriable."""


@dataclass
class StepResult:
    name: str
    ok: bool
    data: Any = None
    error: Optional[str] = None
    attempts: int = 1
    duration_s: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def summary(self) -> str:
        status = "OK" if self.ok else "FAIL"
        return f"{self.name} [{status}] {self.duration_s:.1f}s intento={self.attempts}"


@dataclass
class PipelineStep:
    """
    Un paso del pipeline con retry inteligente.

    none_is_ok=True  → fn() → None se trata como éxito silencioso
                        Usar para ndvi_real (cascade ya intentó todo) e insar.
    none_is_ok=False → fn() → None en required=True aborta sin retry
                        Usar para satellite_pipeline.push que debe escribir algo.
    """
    name: str
    fn: Callable[[], Any]
    retries: int = 3
    wait_min_s: int = 30
    wait_max_s: int = 300
    required: bool = True
    none_is_ok: bool = False

    def execute(self) -> StepResult:
        t0 = time.monotonic()
        attempts = 0

        @retry(
            stop=stop_after_attempt(self.retries),
            wait=wait_exponential(
                multiplier=1,
                min=int(os.environ.get("PIPELINE_RETRY_MIN_S", self.wait_min_s)),
                max=int(os.environ.get("PIPELINE_RETRY_MAX_S", self.wait_max_s)),
            ),
            retry=retry_if_exception_type(_TRANSIENT),
            before_sleep=before_sleep_log(log, logging.WARNING),
            reraise=True,
        )
        def _inner():
            nonlocal attempts
            attempts += 1
            result = self.fn()
            if result is None and not self.none_is_ok and self.required:
                # No relanzamos como transient — no queremos retry de un None lógico
                raise _NoneResultError(
                    f"{self.name}: funcion retorno None "
                    f"(cascade agoto alternativas o sin datos disponibles)"
                )
            return result

        try:
            data = _inner()
            duration = time.monotonic() - t0
            log.info("pipeline: %s OK (%.1fs, %d intento/s)", self.name, duration, attempts)
            return StepResult(name=self.name, ok=True, data=data,
                              attempts=attempts, duration_s=duration)

        except _NoneResultError as e:
            duration = time.monotonic() - t0
            msg = str(e)
            log.warning("pipeline: %s sin datos — %s", self.name, msg)
            return StepResult(name=self.name, ok=False, error=msg,
                              attempts=attempts, duration_s=duration)

        except RetryError as e:
            duration = time.monotonic() - t0
            inner = e.last_attempt.exception()
            msg = f"agoto {self.retries} reintentos: {inner}"
            log.error("pipeline: %s FAIL — %s", self.name, msg)
            return StepResult(name=self.name, ok=False, error=msg,
                              attempts=self.retries, duration_s=duration)

        except Exception as e:
            # Error no-retriable (ImportError, TypeError, bug)
            duration = time.monotonic() - t0
            msg = f"{type(e).__name__}: {e}"
            log.error("pipeline: %s error no-retriable — %s", self.name, msg)
            return StepResult(name=self.name, ok=False, error=msg,
                              attempts=attempts, duration_s=duration)


def run_pipeline(steps: list[PipelineStep]) -> dict:
    """
    Ejecuta steps en orden.
      required=True  + fallo → aborta la cadena
      required=False + fallo → log warning, continúa
    Retorna resumen legible por el Cerebro.
    """
    results: list[StepResult] = []
    aborted_at: Optional[str] = None

    for step in steps:
        log.info("pipeline: >> '%s' (required=%s)", step.name, step.required)
        result = step.execute()
        results.append(result)
        _log_to_supabase(result)

        if not result.ok:
            if step.required:
                aborted_at = step.name
                log.error("pipeline: step requerido '%s' fallo — abortando cadena", step.name)
                break
            log.warning("pipeline: step opcional '%s' fallo — continuando", step.name)

    ok_count  = sum(1 for r in results if r.ok)
    total     = len(results)
    total_dur = round(sum(r.duration_s for r in results), 1)
    pipeline_ok = aborted_at is None and all(r.ok for r in results if r.required)

    summary = {
        "ok":           pipeline_ok,
        "aborted_at":   aborted_at,
        "steps":        [
            {"name": r.name, "ok": r.ok, "attempts": r.attempts,
             "duration_s": round(r.duration_s, 1), "error": r.error}
            for r in results
        ],
        "ok_count":     ok_count,
        "total_steps":  total,
        "duration_s":   total_dur,
        "timestamp":    datetime.now(timezone.utc).isoformat(),
    }

    _label = "COMPLETO" if pipeline_ok else f"FALLIDO (abortado en '{aborted_at}')" if aborted_at else "PARCIAL"
    (log.info if pipeline_ok else log.error)(
        "pipeline: %s — %d/%d OK — %.1fs", _label, ok_count, total, total_dur
    )
    return summary


# ── Supabase logging — best-effort, nunca bloquea el pipeline ─────────────────

def _log_to_supabase(result: StepResult) -> None:
    """Persiste resultado en faro_cerebro_log para visibilidad del Cerebro."""
    try:
        import requests as _r
        url = os.environ.get("SUPABASE_URL", "").rstrip("/")
        key = os.environ.get("SUPABASE_KEY", "")
        if not url or not key:
            return
        _r.post(
            f"{url}/rest/v1/faro_cerebro_log",
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            json={
                "accion":    f"pipeline_{result.name}",
                "resultado": result.summary(),
                "error":     result.error,
                "escalado":  not result.ok,
                "timestamp": result.timestamp,
            },
            timeout=5,
        )
    except Exception:
        pass
