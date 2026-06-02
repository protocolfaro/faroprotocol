"""
dale_play_module.py — Clase base de producción para todos los módulos Dale Play.

Contrato obligatorio:
  - run() → dict con datos reales O {"error": ..., "fuente": ..., "fallback_usado": bool}
  - validate() → lista de errores en TODOS los campos requeridos (incluye None)
  - confianza() → 'verde' | 'amarillo' | 'rojo' según calidad del dato

Principio: el sistema nunca miente. Datos faltantes se declaran explícitamente.
"""
from __future__ import annotations
from abc import ABC, abstractmethod


class DalePlayModule(ABC):
    RESULT_KEY:      str
    REQUIRED_FIELDS: list[str] = []
    MODES:           tuple[str, ...] = ("full", "post_show", "weather_only")
    CRITICAL:        bool = False   # True → pipeline para si falla
    TIMEOUT_S:       int  = 30      # segundos antes de timeout
    DATA_SOURCE:     str  = "unknown"

    @abstractmethod
    def run(self, rider: dict, **kwargs) -> dict:
        """
        Ejecuta el módulo.
        - Éxito: dict con campos de REQUIRED_FIELDS + "fuente" + "fallback_usado"
        - Fallo: {"error": str, "fuente": str, "fallback_usado": bool}
        NUNCA lanza excepciones al caller.
        """

    def validate(self, output: dict) -> list[str]:
        """Verifica todos los campos requeridos. None cuenta como faltante."""
        if output.get("error"):
            return [f"[{self.RESULT_KEY}] módulo falló: {output['error']}"]
        missing = []
        for f in self.REQUIRED_FIELDS:
            if f not in output or output[f] is None:
                missing.append(f"MISSING/NULL: {f} en {self.RESULT_KEY}")
        return missing

    def confianza(self, output: dict) -> str:
        """
        'verde'    — dato real, fuente verificada
        'amarillo' — fallback o estimado
        'rojo'     — módulo falló, no usar para decisiones
        """
        if not output or output.get("error"):
            return "rojo"
        if output.get("fallback_usado") or output.get("estimado"):
            return "amarillo"
        fuente = str(output.get("fuente", ""))
        if "[ESTIMADO]" in fuente or "ESTIMADO" in fuente:
            return "amarillo"
        return "verde"

    def render(self, ax, output: dict):
        """Opcional: renderer de sección para el reporte."""

    def extract(self, result: dict) -> dict:
        """Accessor seguro — nunca strings hardcodeados en el reporte."""
        return result.get(self.RESULT_KEY) or {}
