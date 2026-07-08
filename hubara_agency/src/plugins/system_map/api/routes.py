"""HTTP API del system_map.

Endpoints:
    GET /api/system-map/graph → SystemGraph completo (nodos + edges + stats).
    GET /api/system-map/health → healthcheck simple.

Diseño: el endpoint es READ-ONLY. NO modifica state, NO escribe archivos.
Cada GET reconstruye el grafo desde manifests on-disk. Es seguro porque:
  - manifests son YAML estáticos (no requieren imports Python)
  - el builder es idempotent
  - typical response <100 KB para ~10 plugins

CORS: configurado en `main.py` global (no per-router).
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.plugins.system_map.domain.serialize import certified_graph_payload, health_payload

router = APIRouter()


@router.get(
    "/graph",
    summary="System graph — todos los plugins + nodos + edges + orphans",
    tags=["SystemMap"],
)
def get_system_graph() -> JSONResponse:
    """Construye el grafo del sistema en runtime y lo retorna como JSON.

    El payload lo arma `domain.serialize.certified_graph_payload` — fuente
    única compartida con el puente stdio de VS Code (guard anti-drift en
    tests/plugins/system_map/test_serialize.py).

    NO caching: cada call lee los manifests on-disk. Útil para detectar
    cambios al instante durante desarrollo. Si el repo tiene cientos de
    plugins (escenario muy futuro), agregar Cache-Control headers.
    """
    return JSONResponse(
        content=certified_graph_payload(),
        headers={
            # Sin cache — el grafo refleja state on-disk
            "Cache-Control": "no-store",
        },
    )


@router.get(
    "/health",
    summary="Healthcheck del system_map plugin",
    tags=["SystemMap"],
)
def health() -> dict:
    """Liveness probe — mismo `health_payload()` que sirve el puente stdio
    de VS Code (fuente única, guard anti-drift en test_serialize.py)."""
    return health_payload()
