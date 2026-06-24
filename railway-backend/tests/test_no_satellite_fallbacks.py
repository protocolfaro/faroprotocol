"""
test_no_satellite_fallbacks.py

Regla determinística: ningún campo satelital tiene un fallback numérico literal
que haga que mediciones ausentes parezcan datos reales.

Detecta tres patrones via análisis AST (sin ejecutar el código):
  A: campo.get('ndvi') or 0.XX        — BoolOp con Constant numérico en .get()
  B: x if x is not None else 0.XX     — IfExp con sat field en el test y else numérico ≠ 0
  C: campo.get('ndvi', 0.XX)          — default explícito numérico en .get()

  D: ndvi * 0.65                         — BinOp con Name de sat_field sin guard None previo

Para suprimir un caso legítimo documentado, añadir en la misma línea:
  # sat-fallback: ok — <razón>
"""
import ast
from pathlib import Path

_REPO      = Path(__file__).parent.parent
_SCAN_DIRS = [_REPO / 'sports', _REPO / 'events']
_SUPPRESS  = '# sat-fallback: ok'

_SAT_FIELDS = frozenset({
    'ndvi', 'ndre', 'ccci',
    'sar_vv', 'sar_vh', 'sar_vv_db', 'sar_vh_db',
    'sigma0', 'coherence',
    'ndvi_2d', 'pct_baja_ndvi', 'focos_reales',
    'theta_soil', 'alpha', 'entropy', 'anisotropy',
})


def _is_numeric(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, (int, float))


def _find_violations(path: Path) -> list[tuple[int, str]]:
    try:
        text = path.read_text(encoding='utf-8')
        tree = ast.parse(text)
    except SyntaxError:
        return []

    lines   = text.splitlines()
    results = []

    def suppressed(lineno: int) -> bool:
        return _SUPPRESS in (lines[lineno - 1] if 1 <= lineno <= len(lines) else '')

    for node in ast.walk(tree):

        # Pattern A: campo.get('sat_field') or NUMBER
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            left = node.values[0]
            if (isinstance(left, ast.Call)
                    and isinstance(left.func, ast.Attribute)
                    and left.func.attr == 'get'
                    and left.args
                    and isinstance(left.args[0], ast.Constant)
                    and left.args[0].value in _SAT_FIELDS):
                for val in node.values[1:]:
                    if _is_numeric(val):
                        ln = getattr(node, 'lineno', 0)
                        if not suppressed(ln):
                            results.append((ln,
                                f"PatternA  .get('{left.args[0].value}') or {val.value}"))

        # Pattern B: sat_field if sat_field is not None else NON_ZERO_NUMBER
        if isinstance(node, ast.IfExp) and _is_numeric(node.orelse):
            if abs(node.orelse.value) > 0.001:    # excluye 0.0 genuino en cálculos
                ln = getattr(node, 'lineno', 0)
                if not suppressed(ln):
                    for sub in ast.walk(node.test):
                        if isinstance(sub, ast.Name) and sub.id in _SAT_FIELDS:
                            results.append((ln,
                                f"PatternB  '{sub.id}' if ... else {node.orelse.value}"))
                            break

        # Pattern C: campo.get('sat_field', NUMBER)
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'get'
                and len(node.args) == 2
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in _SAT_FIELDS
                and _is_numeric(node.args[1])):
            ln = getattr(node, 'lineno', 0)
            if not suppressed(ln):
                results.append((ln,
                    f"PatternC  .get('{node.args[0].value}', {node.args[1].value})"))

        # Pattern D: obj.get("sat_field") usado directamente en BinOp sin intermediar None check.
        # Ejemplo: row.get("ndvi") * 100  — TypeError si None, falso valor si tiene default.
        # No detecta: ndvi = obj.get("ndvi"); ndvi * 100  (variable intermedia, cubierto por A/B/C).
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mult, ast.Div, ast.Add, ast.Sub)):
            ln = getattr(node, 'lineno', 0)
            if not suppressed(ln):
                for operand in (node.left, node.right):
                    if (isinstance(operand, ast.Call)
                            and isinstance(operand.func, ast.Attribute)
                            and operand.func.attr == 'get'
                            and operand.args
                            and isinstance(operand.args[0], ast.Constant)
                            and operand.args[0].value in _SAT_FIELDS):
                        results.append((ln,
                            f"PatternD  .get('{operand.args[0].value}') in BinOp without None guard"))
                        break

    return results


def test_no_satellite_numeric_fallbacks():
    """Ningún campo satelital tiene fallback numérico literal en el código fuente."""
    all_violations: list[str] = []
    for d in _SCAN_DIRS:
        if not d.exists():
            continue
        for py_file in sorted(d.rglob('*.py')):
            rel  = py_file.relative_to(_REPO)
            text = None
            for ln, desc in _find_violations(py_file):
                if text is None:
                    text = py_file.read_text(encoding='utf-8').splitlines()
                src_line = text[ln - 1].strip() if 1 <= ln <= len(text) else ''
                all_violations.append(f"  {rel}:{ln} — {desc}\n    {src_line}")

    assert not all_violations, (
        "\nFallback numérico en campo satelital detectado.\n"
        "Corregir (usar None) o documentar con '# sat-fallback: ok — <razón>':\n\n"
        + "\n".join(all_violations)
    )
