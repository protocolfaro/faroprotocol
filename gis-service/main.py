"""
MachinaOS GIS Server — FastAPI HTTP wrapper · Faro Protocol
============================================================
Expone las 4 herramientas GIS de machina_gis_server.py como REST endpoints.

Endpoints:
    POST /gis/indices     — Índices espectrales NDVI/SAVI/EVI/NDWI/NBR
    POST /gis/sar-change  — Cambio SAR log-ratio entre dos épocas
    POST /gis/report      — Mapa de reporte visual PNG
    POST /gis/insar       — Coherencia InSAR via openEO (cloud)

Deployment:
    Railway → root directory: gis-service/
    Start:   uvicorn main:app --host 0.0.0.0 --port $PORT
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# ── Mock MCP before importing machina_gis_server ─────────────────────────────
# @mcp.tool() becomes an identity decorator → functions are callable directly.
from unittest.mock import MagicMock

_fastmcp_mod      = MagicMock()
_fastmcp_instance = MagicMock()
_fastmcp_instance.tool.return_value = lambda f: f   # @mcp.tool() = no-op
_fastmcp_mod.FastMCP.return_value   = _fastmcp_instance

sys.modules["mcp"]                  = MagicMock()
sys.modules["mcp.server"]           = MagicMock()
sys.modules["mcp.server.fastmcp"]   = _fastmcp_mod

# ── Import GIS functions from repo root ───────────────────────────────────────
_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from machina_gis_server import (   # noqa: E402
    AREAS,
    calcular_indices_espectrales,
    analizar_cambio_sar,
    generar_mapa_reporte,
    computar_coherencia_insar,
)

# ── FastAPI app ───────────────────────────────────────────────────────────────
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(
    title="MachinaOS GIS Server",
    description=(
        "Faro Protocol — análisis satelital SAR + óptico. "
        "Sentinel-1 (SAR change, InSAR cloud) + Sentinel-2 (NDVI/SAVI/EVI/NDWI/NBR)."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {
        "status":  "ok",
        "service": "machina-gis",
        "version": "1.0.0",
        "areas":   list(AREAS.keys()),
    }


@app.get("/gis/areas")
def list_areas() -> dict:
    """Lista áreas disponibles con bounds y label."""
    return {
        name: {"label": a["label"], "bounds": a["bounds"]}
        for name, a in AREAS.items()
    }


# ── Request models ─────────────────────────────────────────────────────────────

class IndicesRequest(BaseModel):
    area_name:     str
    indices:       list[str] | None = None           # default: ['NDVI', 'SAVI']
    ndvi_tif_path: str | None = None
    b04_path:      str | None = None                 # RED band
    b08_path:      str | None = None                 # NIR band
    b02_path:      str | None = None                 # BLUE (EVI)
    b03_path:      str | None = None                 # GREEN (NDWI)
    b11_path:      str | None = None                 # SWIR (NBR)


class SarChangeRequest(BaseModel):
    area_name:         str
    sar_ref_path:      str | None = None
    sar_reciente_path: str | None = None
    umbral_db:         float = 3.0
    guardar_tif:       bool  = False                 # False on Railway (no persistent FS)


class ReportRequest(BaseModel):
    area_name:           str
    titulo:              str | None = None
    ndvi_tif_path:       str | None = None
    sar_tif_path:        str | None = None
    cambio_tif_path:     str | None = None
    incluir_estadisticas: bool = True
    dpi:                 int  = 150


class InsarRequest(BaseModel):
    area_name:          str
    fecha_inicio:       str                          # ISO date, e.g. '2026-01-01'
    fecha_fin:          str
    polarizacion:       str = "VV"                   # 'VV' or 'VH'
    temporal_baseline:  int = 6                      # days between SAR acquisitions
    burst_id:           int | None = None            # Sentinel-1 burst ID
    sub_swath:          str = "IW2"                  # 'IW1' | 'IW2' | 'IW3'


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.post("/gis/indices")
def gis_indices(req: IndicesRequest) -> dict[str, Any]:
    """Calcula índices espectrales a partir de Sentinel-2 (NDVI/SAVI/EVI/NDWI/NBR)."""
    try:
        return calcular_indices_espectrales(
            area_name    = req.area_name,
            indices      = req.indices,
            ndvi_tif_path= req.ndvi_tif_path,
            b04_path     = req.b04_path,
            b08_path     = req.b08_path,
            b02_path     = req.b02_path,
            b03_path     = req.b03_path,
            b11_path     = req.b11_path,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/gis/sar-change")
def gis_sar_change(req: SarChangeRequest) -> dict[str, Any]:
    """Detecta cambios SAR Sentinel-1 entre dos épocas via log-ratio en dB."""
    try:
        return analizar_cambio_sar(
            area_name         = req.area_name,
            sar_ref_path      = req.sar_ref_path,
            sar_reciente_path = req.sar_reciente_path,
            umbral_db         = req.umbral_db,
            guardar_tif       = req.guardar_tif,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/gis/report")
def gis_report(req: ReportRequest) -> dict[str, Any]:
    """Genera mapa de reporte visual PNG combinando NDVI + SAR + cambio SAR."""
    try:
        return generar_mapa_reporte(
            area_name            = req.area_name,
            titulo               = req.titulo,
            ndvi_tif_path        = req.ndvi_tif_path,
            sar_tif_path         = req.sar_tif_path,
            cambio_tif_path      = req.cambio_tif_path,
            incluir_estadisticas = req.incluir_estadisticas,
            dpi                  = req.dpi,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/gis/insar")
def gis_insar(req: InsarRequest) -> dict[str, Any]:
    """Computa coherencia interferométrica InSAR Sentinel-1 en la nube via openEO/APEx."""
    try:
        return computar_coherencia_insar(
            area_name         = req.area_name,
            fecha_inicio      = req.fecha_inicio,
            fecha_fin         = req.fecha_fin,
            polarizacion      = req.polarizacion,
            temporal_baseline = req.temporal_baseline,
            burst_id          = req.burst_id,
            sub_swath         = req.sub_swath,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Startup ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="info")
