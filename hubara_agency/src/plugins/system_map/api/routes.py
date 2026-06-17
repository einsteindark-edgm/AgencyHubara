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

from dataclasses import asdict, replace
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.plugins.system_map.domain.builder import build_system_graph
from src.plugins.system_map.domain.certification import collect_certifications

router = APIRouter()


def _graph_to_json(graph: Any) -> dict:
    """Convierte frozen dataclasses → dict serializable.

    Uses `dataclasses.asdict` recursively. Mantiene `Literal` types como str.
    """
    return asdict(graph)


@router.get(
    "/graph",
    summary="System graph — todos los plugins + nodos + edges + orphans",
    tags=["SystemMap"],
)
def get_system_graph() -> JSONResponse:
    """Construye el grafo del sistema en runtime y lo retorna como JSON.

    NO caching: cada call lee los manifests on-disk. Útil para detectar
    cambios al instante durante desarrollo. Si el repo tiene cientos de
    plugins (escenario muy futuro), agregar Cache-Control headers.
    """
    graph = build_system_graph()
    # F-SDK-5: el catálogo lleva el veredicto TCK por plugin. Se computa EN
    # VIVO (checks filesystem-only, baratos) — cero staleness; tolerante a
    # fallas del testkit (un bug del TCK no debe tumbar el mapa: el grafo
    # sale sin certificación + warning visible).
    try:
        certifications = collect_certifications([p.id for p in graph.plugins])
        graph = replace(graph, certifications=certifications)
    except Exception as exc:  # pragma: no cover — defensivo deliberado
        graph = replace(
            graph,
            warnings=[*graph.warnings, f"certification_unavailable: {exc}"],
        )
    return JSONResponse(
        content=_graph_to_json(graph),
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
    """Liveness probe. Solo verifica que el builder no se rompe vacío."""
    try:
        # Sanity check rápido — no construye el grafo entero, solo verifica
        # que el módulo importa OK y la función es callable.
        _ = build_system_graph.__name__
        return {"status": "ok", "service": "system_map"}
    except Exception as exc:  # pragma: no cover — defensive
        return {"status": "error", "detail": str(exc)}
