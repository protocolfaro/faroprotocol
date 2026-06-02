"""
dale_play_source_log.py — Registro de auditoría de fuentes de datos.

Escribe SOURCE_LOG.md en la raíz del repo con cada evento:
  - Cambio de fuente (fallback activado)
  - Resultado de cada módulo (fuente usada, confianza)
  - Nuevas fuentes descubiertas por el job semanal de auto-mejora

Formato Markdown tabla, append-only.
Compatible con GitHub commit automático vía dale_play_github.py.
"""
from __future__ import annotations

import logging
import pathlib
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

# Raíz del repo: dale-play/ → clients/ → events/ → railway-backend/
_REPO_ROOT  = pathlib.Path(__file__).parent.parent.parent.parent
_LOG_FILE   = _REPO_ROOT / "SOURCE_LOG.md"

_HEADER = """\
# SOURCE_LOG — Faro Protocol

Registro de auditoría automático. Actualizado en cada ejecución del pipeline y job semanal.

| Timestamp (UTC) | Módulo | Fuente Usada | Fuente Intentadas | Motivo Fallback | Confianza | Venue Cubierto |
|---|---|---|---|---|---|---|
"""


def _ensure_header() -> None:
    """Crea el archivo con header si no existe."""
    if not _LOG_FILE.exists():
        try:
            _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            _LOG_FILE.write_text(_HEADER, encoding="utf-8")
        except Exception as exc:
            log.warning("source_log: no se pudo crear SOURCE_LOG.md: %s", exc)


def log_source_event(
    modulo:          str,
    fuente_usada:    str,
    confianza:       float,
    venue_cubierto:  Optional[bool] = None,
    cascade_log:     Optional[list] = None,
    motivo_fallback: str            = "",
    extra_nota:      str            = "",
) -> None:
    """
    Agrega una fila a SOURCE_LOG.md.

    Args:
        modulo:          Nombre del módulo (ej: "SAR", "Satellite", "Weather")
        fuente_usada:    Fuente que finalmente se usó (ej: "S1_RTC_PC")
        confianza:       0.0 a 1.0
        venue_cubierto:  True/False/None
        cascade_log:     Lista de intentos [{"fuente": str, "resultado": str, ...}]
        motivo_fallback: Razón del fallback si lo hubo
        extra_nota:      Nota adicional (ej: "nueva fuente descubierta")
    """
    try:
        _ensure_header()

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # Construir columna "fuentes intentadas"
        fuentes_intentadas = ""
        if cascade_log:
            intentos = []
            for entry in cascade_log:
                src = entry.get("fuente", "?")
                res = entry.get("resultado", "?")
                intentos.append(f"{src}={res}")
            fuentes_intentadas = " → ".join(intentos)
        fuentes_intentadas = fuentes_intentadas or "—"

        # Motivo fallback
        if not motivo_fallback and cascade_log:
            # Extraer razón del último intento fallido antes de la fuente usada
            for entry in cascade_log:
                if entry.get("fuente") == fuente_usada:
                    break
                razon = entry.get("razon", "")
                if razon and "ok" not in entry.get("resultado", ""):
                    motivo_fallback = razon[:80]
                    break
        motivo_fallback = motivo_fallback[:100] if motivo_fallback else "—"

        cubierto_str = {True: "✅", False: "❌", None: "—"}.get(venue_cubierto, "—")

        conf_str = f"{confianza:.2f}"
        if confianza >= 0.70:
            conf_str = f"**{conf_str}**"
        elif confianza <= 0.20:
            conf_str = f"⚠ {conf_str}"

        nota_col = f" _{extra_nota}_" if extra_nota else ""

        row = (
            f"| {ts} | {modulo} | `{fuente_usada}` | {fuentes_intentadas} "
            f"| {motivo_fallback}{nota_col} | {conf_str} | {cubierto_str} |\n"
        )

        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(row)

        log.debug("source_log: entrada añadida [%s] %s conf=%.2f", modulo, fuente_usada, confianza)

    except Exception as exc:
        log.warning("source_log: error escribiendo SOURCE_LOG.md: %s", exc)


def log_discovery_event(
    fuente_nombre: str,
    fuente_tipo:   str,
    recomendacion: str,
    evaluacion:    str = "",
) -> None:
    """
    Registra una nueva fuente descubierta por el job semanal de auto-mejora.
    """
    try:
        _ensure_header()

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        evaluacion_short = evaluacion[:80] if evaluacion else "—"
        row = (
            f"| {ts} | **SCOUT_SEMANAL** | `{fuente_nombre}` | — "
            f"| {fuente_tipo} · rec={recomendacion}: {evaluacion_short} | — | 🔍 |\n"
        )

        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(row)

        log.info("source_log: nueva fuente descubierta: %s (%s)", fuente_nombre, recomendacion)

    except Exception as exc:
        log.warning("source_log: error escribiendo discovery: %s", exc)


def get_log_tail(n_lines: int = 50) -> str:
    """Retorna las últimas n_lines del SOURCE_LOG.md como string."""
    try:
        if not _LOG_FILE.exists():
            return "(SOURCE_LOG.md no existe todavía)"
        lines = _LOG_FILE.read_text(encoding="utf-8").splitlines()
        return "\n".join(lines[-n_lines:])
    except Exception as exc:
        return f"(error leyendo SOURCE_LOG.md: {exc})"


def push_log_to_github(show_id: str = "source_log") -> Optional[str]:
    """
    Commitea SOURCE_LOG.md a GitHub vía dale_play_github.
    Llamado desde el pipeline y desde el job semanal.
    """
    try:
        from dale_play_github import push_show_data

        content = _LOG_FILE.read_text(encoding="utf-8") if _LOG_FILE.exists() else _HEADER
        result  = push_show_data(
            path    = "SOURCE_LOG.md",
            content = content,
            message = f"[Faro Protocol] SOURCE_LOG actualizado — {show_id}",
        )
        log.info("source_log: SOURCE_LOG.md pushed a GitHub: %s", result.get("url", ""))
        return result.get("url")
    except Exception as exc:
        log.warning("source_log: push_log_to_github: %s", exc)
        return None
