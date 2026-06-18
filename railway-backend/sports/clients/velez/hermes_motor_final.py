import numpy as np
from scipy.stats import lognorm
from pydantic import BaseModel, field_validator
from typing import List, Optional


class TenantContract(BaseModel):
    tenant_id: str
    vertical: str
    client_name: str
    polygon_gps: List[List[float]]
    window_alignment_days: int = 15
    min_cobertura_requerida: float = 0.30
    blockchain_heartbeat: bool = False
    tipo_superficie: str = "natural"  # natural | hibrida | sintetica | arcilla | indoor

    @field_validator('vertical')
    def verificar_vertical(cls, v):
        if v not in ["faro_sports", "faro_eventos", "faro_agro"]:
            raise ValueError(f"Vertical '{v}' invalida.")
        return v

    @field_validator('tipo_superficie')
    def verificar_superficie(cls, v):
        validas = {"natural", "hibrida", "sintetica", "arcilla", "indoor"}
        if v not in validas:
            raise ValueError(f"Superficie '{v}' invalida. Validas: {validas}")
        return v


class ResultadoAuditoria(BaseModel):
    tenant_id: str
    status: str           # NOMINAL_GREEN | ALERTA_ANOMALIA | INDETERMINADO
    confianza: str        # ALTA | MEDIA | BAJA
    reason: str
    umbral_dinamico: float
    score_desfase_temporal: float
    ndvi_fuente: str      # OPTICO_DIRECTO | SAR_ESTIMADO | SIN_DATO
    ndvi_valor: Optional[float]
    sar_vv_db: Optional[float]


class HermesMotor:
    """
    Motor canonico de auditoria Hermes para Faro Protocol.
    Integra:
      - Calibracion dinamica de umbral con PoD + guardrail FAR
      - TWDTW acotado para deteccion de desfase temporal
      - Fusion SAR-optico para gap-filling bajo nubosidad
      - 3 niveles de confianza inspirados en QF de NASA/AIRS
    Fixes v6 incluidos:
      - Ordenamiento y deduplicacion antes de interp (bug silencioso)
      - Guardrail de cobertura minima contra nubosidad extrema
      - Calibracion con historico independiente, no contaminado
    """

    # Coeficientes de fusion SAR->NDVI por tipo de superficie.
    # Derivados de literatura (R2 0.58-0.96 segun tipo de cobertura).
    # Se sobrescriben al llamar calibrar_fusion_sar() con datos reales.
    _COEF_SAR_DEFAULT = {
        "natural":   {"slope": 0.022, "intercept": 0.88, "r2_ref": 0.85},
        "hibrida":   {"slope": 0.018, "intercept": 0.75, "r2_ref": 0.72},
        "sintetica": {"slope": 0.000, "intercept": 0.00, "r2_ref": 0.00},
        "arcilla":   {"slope": 0.000, "intercept": 0.00, "r2_ref": 0.00},
        "indoor":    {"slope": 0.000, "intercept": 0.00, "r2_ref": 0.00},
    }

    def __init__(self, contract: TenantContract):
        self.contract = contract
        sup = contract.tipo_superficie
        self._coef_sar = dict(self._COEF_SAR_DEFAULT[sup])
        self._sar_calibrado = False

    # ------------------------------------------------------------------
    # 1. CALIBRACION SAR-OPTICO (se llama UNA VEZ con historico real)
    # ------------------------------------------------------------------
    def calibrar_fusion_sar(self, sar_vv_historico: np.ndarray,
                             ndvi_historico: np.ndarray) -> dict:
        """
        Ajusta la regresion SAR->NDVI con datos historicos reales de la cancha.
        Requiere pares (SAR, NDVI) de dias sin nube donde ambos datos esten disponibles.
        Minimo recomendado: 20 pares (equivale a ~4 meses con 5-6 dias/mes despejados).
        """
        if self.contract.tipo_superficie in {"sintetica", "arcilla", "indoor"}:
            return {"status": "OMITIDO", "razon": "Superficie no biologica, NDVI forzado nulo."}

        if len(sar_vv_historico) < 20:
            return {"status": "INSUFICIENTE", "razon": f"Solo {len(sar_vv_historico)} pares, minimo 20."}

        x = sar_vv_historico.reshape(-1, 1)
        y = ndvi_historico
        # Regresion lineal por minimos cuadrados (sin sklearn, puro numpy)
        A = np.column_stack([x, np.ones(len(x))])
        coefs, _, _, _ = np.linalg.lstsq(A, y, rcond=None)
        slope, intercept = coefs[0], coefs[1]
        y_pred = slope * sar_vv_historico + intercept
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - y.mean()) ** 2)
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0

        self._coef_sar = {"slope": slope, "intercept": intercept, "r2_ref": r2}
        self._sar_calibrado = True
        return {"status": "OK", "slope": round(slope, 5),
                "intercept": round(intercept, 4), "r2": round(r2, 3),
                "n_pares": len(sar_vv_historico)}

    # ------------------------------------------------------------------
    # 2. ESTIMACION NDVI DESDE SAR (gap-filling bajo nubes)
    # ------------------------------------------------------------------
    def estimar_ndvi_desde_sar(self, sar_vv_db: float) -> tuple[Optional[float], str]:
        """
        Retorna (ndvi_estimado, fuente).
        Si la superficie no es biologica o el R2 es bajo, retorna (None, 'SIN_DATO').
        """
        sup = self.contract.tipo_superficie
        if sup in {"sintetica", "arcilla", "indoor"}:
            return None, "SIN_DATO"

        coef = self._coef_sar
        if coef["r2_ref"] < 0.40:
            return None, "SIN_DATO"

        ndvi_est = coef["slope"] * sar_vv_db + coef["intercept"]
        ndvi_est = float(np.clip(ndvi_est, 0.0, 1.0))
        return ndvi_est, "SAR_ESTIMADO"

    # ------------------------------------------------------------------
    # 3. CALIBRACION UMBRAL POD (con guardrail FAR)
    # ------------------------------------------------------------------
    def calibrar_umbral_pod(self, hist_sano: np.ndarray,
                             hist_estres: np.ndarray,
                             target_pod: float = 0.95) -> float:
        if len(hist_estres) == 0 or len(hist_sano) == 0:
            return 0.45

        shape, loc, scale = lognorm.fit(hist_estres)
        umbral = lognorm.ppf(target_pod, shape, loc, scale)

        falsas = np.sum(hist_sano < umbral)
        total = falsas + np.sum(hist_estres < umbral)
        far = falsas / total if total > 0 else 0

        if far > 0.05:
            umbral = float(np.median(hist_sano) - 1.645 * np.std(hist_sano))
        return float(umbral)

    # ------------------------------------------------------------------
    # 4. NORMALIZACION TEMPORAL (con fix de orden y deduplicacion)
    # ------------------------------------------------------------------
    def normalizar_eje_temporal(self, dias: np.ndarray,
                                 valores: np.ndarray,
                                 dias_totales: int = 30) -> np.ndarray:
        cobertura = len(dias) / dias_totales
        if cobertura < self.contract.min_cobertura_requerida:
            raise ValueError(
                f"Cobertura insuficiente ({cobertura:.0%}) por nubosidad extrema.")

        dias = np.asarray(dias, dtype=float)
        valores = np.asarray(valores, dtype=float)

        # FIX v6: np.interp exige xp estrictamente creciente -- sin esto el
        # resultado cambia segun el orden de llegada del array (bug silencioso)
        orden = np.argsort(dias)
        dias = dias[orden]
        valores = valores[orden]

        # FIX v6: deduplicar fechas repetidas antes de interpolar
        unicos, idx = np.unique(dias, return_index=True)
        if len(unicos) < len(dias):
            vals_unicos = np.array([valores[dias == d].mean() for d in unicos])
        else:
            vals_unicos = valores

        return np.interp(np.arange(1, dias_totales + 1), unicos, vals_unicos)

    # ------------------------------------------------------------------
    # 5. TWDTW ACOTADO
    # ------------------------------------------------------------------
    def calcular_twdtw(self, serie_actual: np.ndarray,
                        serie_patron: np.ndarray) -> float:
        N, M = len(serie_actual), len(serie_patron)
        w = max(self.contract.window_alignment_days, abs(N - M))
        mat = np.full((N + 1, M + 1), np.inf)
        mat[0, 0] = 0.0
        for i in range(1, N + 1):
            for j in range(max(1, i - w), min(M + 1, i + w + 1)):
                costo = abs(serie_actual[i - 1] - serie_patron[j - 1])
                mat[i, j] = costo + min(mat[i-1, j], mat[i, j-1], mat[i-1, j-1])
        return float(mat[N, M] / (N + M))

    # ------------------------------------------------------------------
    # 6. PIPELINE PRINCIPAL
    # ------------------------------------------------------------------
    def auditar(self, ndvi_optico: Optional[float],
                 sar_vv_db: Optional[float],
                 historicos: dict,
                 geo_series: dict) -> ResultadoAuditoria:
        """
        Parametros:
          ndvi_optico : valor directo de Sentinel-2, None si hay nubes
          sar_vv_db   : backscatter Sentinel-1 en dB, None si no disponible
          historicos  : {"sano": np.array, "estres": np.array}
          geo_series  : {"dias_actual": [], "ndvi_actual": [],
                         "dias_patron": [], "ndvi_patron": []}
        """
        # --- Determinar fuente y valor de NDVI ---
        ndvi_valor = None
        ndvi_fuente = "SIN_DATO"
        confianza = "BAJA"

        if ndvi_optico is not None:
            ndvi_valor = ndvi_optico
            ndvi_fuente = "OPTICO_DIRECTO"
            confianza = "ALTA"
        elif sar_vv_db is not None:
            ndvi_est, fuente = self.estimar_ndvi_desde_sar(sar_vv_db)
            if ndvi_est is not None:
                ndvi_valor = ndvi_est
                ndvi_fuente = fuente
                confianza = "MEDIA"

        # --- Si no hay ningun dato, retornar INDETERMINADO ---
        if ndvi_valor is None:
            return ResultadoAuditoria(
                tenant_id=self.contract.tenant_id,
                status="INDETERMINADO",
                confianza="BAJA",
                reason="Sin dato optico ni SAR disponible.",
                umbral_dinamico=0.0,
                score_desfase_temporal=0.0,
                ndvi_fuente="SIN_DATO",
                ndvi_valor=None,
                sar_vv_db=sar_vv_db,
            )

        # --- Pipeline de evaluacion ---
        try:
            s_actual = self.normalizar_eje_temporal(
                geo_series["dias_actual"], geo_series["ndvi_actual"])
            s_patron = self.normalizar_eje_temporal(
                geo_series["dias_patron"], geo_series["ndvi_patron"])

            umbral = self.calibrar_umbral_pod(
                historicos["sano"], historicos["estres"])
            score = self.calcular_twdtw(s_actual, s_patron)

            alerta = (ndvi_valor < umbral) or (score > 0.12)
            status = "ALERTA_ANOMALIA" if alerta else "NOMINAL_GREEN"
            reason = (
                f"NDVI={ndvi_valor:.3f} umbral={umbral:.3f} "
                f"TWDTW={score:.4f} fuente={ndvi_fuente}"
            )

        except ValueError as e:
            status = "INDETERMINADO"
            reason = str(e)
            umbral, score = 0.0, 0.0

        return ResultadoAuditoria(
            tenant_id=self.contract.tenant_id,
            status=status,
            confianza=confianza,
            reason=reason,
            umbral_dinamico=round(umbral, 4),
            score_desfase_temporal=round(score, 4),
            ndvi_fuente=ndvi_fuente,
            ndvi_valor=round(ndvi_valor, 4) if ndvi_valor else None,
            sar_vv_db=sar_vv_db,
        )
