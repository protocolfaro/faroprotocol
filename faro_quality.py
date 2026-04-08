"""
FARO PROTOCOL — Módulo de Calidad
===================================

Lee QUALITY_STANDARD.md y provee enforcements programáticos.
Importado automáticamente por:
  - faro_certificado.py
  - faro_bricolage.py
  - faro_report_engine.py

Uso:
    from faro_quality import QualityGate
    QualityGate.check(area_name, score, estado, report_png, mode='external')
"""

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT       = Path(__file__).parent
STANDARD   = ROOT / 'QUALITY_STANDARD.md'
AUDIT_LOG  = ROOT / 'audit_log.jsonl'

SCORE_MIN_EXTERNAL = 50     # scores por debajo → bloqueante en modo externo
ALERTA_BLOCK       = True   # ALERTA bloquea outputs externos


# ── Lectura del estándar ──────────────────────────────────────────────────────

def _load_standard() -> str:
    if STANDARD.exists():
        return STANDARD.read_text(encoding='utf-8')
    return ''


def print_quality_header(output_type: str):
    """
    Imprime un encabezado confirmando que el estándar fue leído.
    Llamar al inicio de cada script de generación.
    """
    ok = STANDARD.exists()
    mark = '✓' if ok else '!'
    print(f'  [{mark}] QUALITY_STANDARD.md {"leído" if ok else "NO ENCONTRADO — continuando sin estándar"}')
    if ok:
        # Extraer versión del archivo
        for line in _load_standard().splitlines():
            if line.startswith('> Versión'):
                print(f'      {line.strip("> ")}')
                break
    print(f'  [·] Output type: {output_type}')


# ── SHA-256 ───────────────────────────────────────────────────────────────────

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def verify_sha256(area_name: str, report_png: str | Path) -> tuple[bool, str]:
    """
    Verifica el PNG de fusión contra su archivo .sha256.
    Retorna (ok, hex_actual).
    """
    png = ROOT / report_png if not Path(report_png).is_absolute() else Path(report_png)
    sha = ROOT / f'faro_reporte_fusion_{area_name}.sha256'

    if not png.exists():
        return False, f'PNG no encontrado: {png}'
    if not sha.exists():
        return False, f'SHA256 no encontrado: {sha}'

    archivado = sha.read_text(encoding='utf-8').split()[0].strip()
    actual    = _sha256(png)
    return actual == archivado, actual


# ── Quality Gate ─────────────────────────────────────────────────────────────

class QualityViolation(Exception):
    """Raised cuando una violación bloqueante impide generar el output."""
    pass


class QualityGate:
    """
    Aplica las reglas del QUALITY_STANDARD.md.

    mode='external' → reglas estrictas (publicación, pitch, bricolage)
    mode='internal' → alertas y scores bajos permitidos (auditoría interna)
    """

    @staticmethod
    def check(area_name: str,
              score,
              estado: str,
              report_png: str | Path,
              mode: str = 'external') -> list[str]:
        """
        Valida un área antes de incluirla en un output.

        Retorna lista de warnings (modo internal) o lanza QualityViolation
        si hay errores bloqueantes en modo external.

        Args:
            area_name:  nombre del área (ej. 'balcarce')
            score:      score Faro (float o None)
            estado:     estado del pipeline ('OK', 'ALERTA', 'SIN_DATOS')
            report_png: path al PNG de fusión fuente
            mode:       'external' (publicación) | 'internal' (auditoría)

        Returns:
            Lista de strings con violaciones/warnings (vacía = todo OK)
        """
        violations = []
        blocking   = []

        # ── 1. Archivo fuente existe ──────────────────────────────────────────
        png_path = ROOT / report_png if not Path(str(report_png)).is_absolute() else Path(report_png)
        if not png_path.exists():
            msg = f'Archivo fuente no encontrado: {png_path}'
            blocking.append(msg)

        # ── 2. SHA-256 verificado ─────────────────────────────────────────────
        if png_path.exists():
            sha_ok, sha_val = verify_sha256(area_name, png_path)
            if not sha_ok:
                if sha_val.startswith('PNG') or sha_val.startswith('SHA'):
                    blocking.append(f'SHA-256 error: {sha_val}')
                else:
                    blocking.append(
                        f'SHA-256 NO coincide para {area_name} — '
                        f'archivo puede haber sido modificado'
                    )

        # ── 3. Score mínimo para externos ────────────────────────────────────
        if mode == 'external' and score is not None and score < SCORE_MIN_EXTERNAL:
            blocking.append(
                f'Score {score:.0f} < {SCORE_MIN_EXTERNAL} — '
                f'área {area_name} excluida de outputs externos'
            )

        # ── 4. Estado ALERTA en externos ─────────────────────────────────────
        if mode == 'external' and ALERTA_BLOCK and estado == 'ALERTA':
            blocking.append(
                f'Estado ALERTA — área {area_name} excluida de outputs externos'
            )

        # ── 5. SIN_DATOS ──────────────────────────────────────────────────────
        if estado == 'SIN_DATOS':
            violations.append(f'Estado SIN_DATOS — datos satelitales pendientes para {area_name}')

        violations.extend(blocking)

        # En modo externo, los errores bloqueantes abortan
        if mode == 'external' and blocking:
            for b in blocking:
                print(f'  [✗] CALIDAD: {b}')
            raise QualityViolation(
                f'{area_name}: {len(blocking)} violación(es) bloqueante(s). '
                f'Usar mode="internal" para auditoría.'
            )

        # En modo interno, solo warnings
        for v in violations:
            print(f'  [!] CALIDAD: {v}')

        return violations

    @staticmethod
    def filter_areas(areas_data: list[dict], mode: str = 'external') -> list[dict]:
        """
        Filtra una lista de dicts de área, retornando solo los que pasan
        el quality gate para el modo dado.

        Cada dict debe tener: name, score, estado, report_png
        """
        passed = []
        for a in areas_data:
            try:
                viols = QualityGate.check(
                    area_name  = a['name'],
                    score      = a.get('score'),
                    estado     = a.get('estado', 'OK'),
                    report_png = a.get('report_png',
                                       f'faro_reporte_fusion_{a["name"]}.png'),
                    mode       = mode,
                )
                passed.append({**a, '_quality_violations': viols})
                status = '✓' if not viols else f'! {len(viols)} warnings'
                print(f'  [{status}] {a["name"]} — score={a.get("score")} estado={a.get("estado")}')
            except QualityViolation as e:
                print(f'  [✗] {a["name"]} excluido: {e}')
        return passed


# ── Audit log ─────────────────────────────────────────────────────────────────

def log_generation(output_type: str, area_name: str | list,
                   output_path: str, violations: list[str],
                   score=None, estado: str = ''):
    """
    Registra una generación de output en audit_log.jsonl.
    Llamar siempre al finalizar la generación (éxito o fallo).
    """
    entry = {
        'ts':         datetime.now().strftime('%Y-%m-%d %H:%M'),
        'type':       output_type,
        'area':       area_name,
        'output':     str(Path(output_path).name) if output_path else None,
        'score':      score,
        'estado':     estado,
        'violations': violations,
        'standard':   'QUALITY_STANDARD.md v1.0',
    }
    try:
        with open(AUDIT_LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception as e:
        print(f'  [!] No se pudo escribir audit_log: {e}')
