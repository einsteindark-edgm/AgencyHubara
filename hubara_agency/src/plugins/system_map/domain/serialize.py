"""Serialización canónica del SystemGraph — fuente única para TODOS los
transportes (FastAPI en `api/routes.py` y el puente stdio de VS Code en
`scripts/system_map_bridge.py`).

Extraído de `routes.get_system_graph` para que el grafo que ve el dashboard y
el que ve la extensión no puedan divergir: ambos llaman esta función.
"""
from __future__ import annotations

from dataclasses import asdict, replace

from src.plugins.system_map.domain.builder import build_system_graph


def certified_graph_payload() -> dict:
    """SystemGraph completo + veredicto TCK por plugin, como dict JSON-ready.

    F-SDK-5: la certificación se computa EN VIVO (checks filesystem-only,
    baratos) — cero staleness. Tolerante a fallas del testkit: si el TCK
    truena (incluso al IMPORTAR — por eso el import es lazy y vive dentro del
    try), el grafo sale sin certificación + warning visible. Un bug del TCK
    no debe tumbar el mapa.
    """
    graph = build_system_graph()
    try:
        from src.plugins.system_map.domain.certification import collect_certifications

        certifications = collect_certifications([p.id for p in graph.plugins])
        graph = replace(graph, certifications=certifications)
    except Exception as exc:  # pragma: no cover — defensivo deliberado
        graph = replace(
            graph,
            warnings=[*graph.warnings, f"certification_unavailable: {exc}"],
        )
    return asdict(graph)


def health_payload() -> dict:
    """Liveness del plugin, compartido por FastAPI y el puente stdio.

    Importar el builder (lazy, dentro del try) atestigua que la cadena de
    imports del plugin está sana — el mismo shape en ambos transportes.
    """
    try:
        from src.plugins.system_map.domain.builder import build_system_graph

        _ = build_system_graph.__name__
        return {"status": "ok", "service": "system_map"}
    except Exception as exc:  # pragma: no cover — liveness honesto, no crash
        return {"status": "error", "detail": str(exc)}
