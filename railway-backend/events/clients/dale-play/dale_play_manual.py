"""
dale_play_manual.py — Utilidad de escritura append-only al MANUAL.md.

Regla de oro: MANUAL.md nunca se sobreescribe.
Cada actualización AGREGA al final de la sección correspondiente.
append_to_manual() es el único punto de escritura autorizado.
"""
from __future__ import annotations
import logging
import pathlib
import re
from datetime import datetime, timezone

log = logging.getLogger(__name__)

MANUAL_PATH = pathlib.Path(__file__).parent / "MANUAL.md"

# Versión actual del MANUAL — se incrementa cada vez que se agrega una entrada
_CURRENT_VERSION = "1.1"


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _next_version(current: str) -> str:
    """Incrementa el patch de un semver '1.1' → '1.2'."""
    try:
        parts = current.split(".")
        parts[-1] = str(int(parts[-1]) + 1)
        return ".".join(parts)
    except Exception:
        return current


def _find_section_bounds(lines: list[str], section: str) -> tuple[int, int]:
    """
    Devuelve (start_line, end_line) del cuerpo de la sección que matchea `section`.
    start_line: índice de la línea del encabezado ## (inclusivo)
    end_line:   índice de la línea del separador --- o del siguiente ## (exclusivo)
    Lanza ValueError si no encuentra la sección.
    """
    section_lower = section.lower()
    header_line = -1

    for i, line in enumerate(lines):
        if line.startswith("## ") and section_lower in line.lower():
            header_line = i
            break

    if header_line == -1:
        raise ValueError(f"Sección no encontrada en MANUAL.md: {section!r}")

    # Buscar fin: próximo ## o --- separador de sección
    for j in range(header_line + 1, len(lines)):
        stripped = lines[j].strip()
        if stripped.startswith("## "):
            return header_line, j
        # Separador --- que esté solo en la línea (no dentro de tablas)
        if stripped == "---":
            return header_line, j

    return header_line, len(lines)


def append_to_manual(
    section: str,
    content: str,
    version: str | None = None,
    tag: str | None = None,
) -> bool:
    """
    Agrega `content` al final del cuerpo de `section` en MANUAL.md.

    Args:
        section: fragmento del encabezado ## a buscar (case-insensitive).
                 Ej: "15. ESTADO", "3.1", "BUGS CORREGIDOS", "FUENTES NUEVAS"
        content: texto Markdown a agregar (sin salto de línea inicial)
        version: tag de versión del MANUAL (default: autoincremento)
        tag:     etiqueta opcional para la entrada (default: fecha UTC)

    Returns:
        True si escribió, False si algo falló.

    Nunca lanza excepciones al caller.
    """
    if not MANUAL_PATH.exists():
        log.error("append_to_manual: MANUAL.md no existe en %s", MANUAL_PATH)
        return False

    try:
        raw  = MANUAL_PATH.read_text(encoding="utf-8")
        lines = raw.splitlines(keepends=True)

        stamp   = tag or _now_stamp()
        ver_new = version or _next_version(_current_manual_version(raw))

        # Construir bloque a insertar
        entry = (
            f"\n<!-- append:{stamp} v{ver_new} -->\n"
            f"{content.rstrip()}\n"
        )
        entry_lines = entry.splitlines(keepends=True)

        try:
            _start, end = _find_section_bounds(lines, section)
        except ValueError as e:
            log.warning("append_to_manual: %s — creando sección al final", e)
            # Si la sección no existe, la creamos al final antes del EOF
            new_section = (
                f"\n## {section}\n\n"
                f"<!-- append:{stamp} v{ver_new} -->\n"
                f"{content.rstrip()}\n\n---\n"
            )
            raw += new_section
            MANUAL_PATH.write_text(raw, encoding="utf-8")
            _update_header_version(ver_new, stamp)
            log.info("append_to_manual: nueva sección '%s' creada  v%s", section, ver_new)
            return True

        # Insertar justo antes del separador/próxima sección
        # Retroceder para no quedar pegado al --- si hay línea en blanco antes
        insert_at = end
        while insert_at > _start + 1 and lines[insert_at - 1].strip() == "":
            insert_at -= 1

        lines[insert_at:insert_at] = entry_lines

        new_raw = "".join(lines)
        MANUAL_PATH.write_text(new_raw, encoding="utf-8")
        _update_header_version(ver_new, stamp)
        log.info("append_to_manual: sección '%s' → %d chars agregados  v%s",
                 section, len(content), ver_new)
        return True

    except Exception as e:
        log.error("append_to_manual ERROR: %s", e, exc_info=True)
        return False


def _current_manual_version(raw: str) -> str:
    """Extrae la versión actual del encabezado del MANUAL."""
    m = re.search(r"Versión:\s*([\d.]+)", raw)
    return m.group(1) if m else _CURRENT_VERSION


def _update_header_version(new_version: str, stamp: str) -> None:
    """Actualiza la línea de versión y fecha en el encabezado del MANUAL."""
    try:
        raw = MANUAL_PATH.read_text(encoding="utf-8")
        raw = re.sub(
            r"(Fecha de última actualización:)\s*[\d-]+",
            f"\\1 {stamp}",
            raw,
            count=1,
        )
        raw = re.sub(
            r"(Versión:)\s*[\d.]+",
            f"\\1 {new_version}",
            raw,
            count=1,
        )
        MANUAL_PATH.write_text(raw, encoding="utf-8")
    except Exception as e:
        log.warning("_update_header_version: %s", e)


# ── Helpers especializados para secciones conocidas ──────────────────────────

def append_bug(description: str, archivo: str, causa: str,
               fix: str, impacto: str = "") -> bool:
    """Agrega un bug corregido a la sección 5. BUGS CORREGIDOS."""
    stamp = _now_stamp()
    n_existing = _count_bugs()
    numero = n_existing + 1
    content = (
        f"### Bug #{numero} — {description} ({stamp})\n"
        f"- **Archivo:** `{archivo}`\n"
        f"- **Causa:** {causa}\n"
        f"- **Fix:** {fix}\n"
    )
    if impacto:
        content += f"- **Impacto:** {impacto}\n"
    return append_to_manual("5. BUGS CORREGIDOS", content)


def append_new_source(
    source_name: str,
    modulo: str,
    descripcion: str,
    resolucion: str,
    frecuencia: str,
    credenciales: str,
    mejora: str,
) -> bool:
    """Agrega una fuente nueva a la sección 15. ESTADO ACTUAL — Fuentes nuevas detectadas."""
    stamp = _now_stamp()
    content = (
        f"#### {source_name} · detectada {stamp}\n"
        f"- **Módulo mejorado:** `{modulo}`\n"
        f"- **Descripción:** {descripcion}\n"
        f"- **Resolución:** {resolucion}  ·  **Frecuencia:** {frecuencia}\n"
        f"- **Credenciales:** {credenciales}\n"
        f"- **Mejora:** {mejora}\n"
    )
    # Intentar agregar a la subsección de módulos pendientes/futuros
    # Si no existe la sección de fuentes nuevas, va al final de §15
    ok = append_to_manual("Fuentes nuevas detectadas automáticamente", content)
    if not ok:
        ok = append_to_manual("15. ESTADO ACTUAL", content)
    return ok


def append_module_added(
    result_key: str,
    archivo: str,
    data_source: str,
    descripcion: str,
) -> bool:
    """Registra un módulo nuevo integrado automáticamente."""
    stamp = _now_stamp()
    content = (
        f"#### {result_key} · integrado {stamp}\n"
        f"- **Archivo:** `{archivo}`\n"
        f"- **Fuente:** {data_source}\n"
        f"- **Descripción:** {descripcion}\n"
        f"- **Integración:** automática vía `check_new_sources.py`\n"
    )
    return append_to_manual("11. CÓMO AGREGAR UN MÓDULO NUEVO", content)


def _count_bugs() -> int:
    """Cuenta bugs existentes en la sección 5."""
    try:
        raw = MANUAL_PATH.read_text(encoding="utf-8")
        return len(re.findall(r"^### Bug #\d+", raw, re.MULTILINE))
    except Exception:
        return 0


def get_section_content(section: str) -> str | None:
    """Retorna el contenido de texto de una sección, o None si no existe."""
    try:
        raw   = MANUAL_PATH.read_text(encoding="utf-8")
        lines = raw.splitlines(keepends=True)
        start, end = _find_section_bounds(lines, section)
        return "".join(lines[start:end])
    except Exception:
        return None
