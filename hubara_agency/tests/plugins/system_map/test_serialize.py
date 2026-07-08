"""Guard anti-drift: TODOS los transportes del system_map sirven el MISMO
payload, construido por `domain.serialize.certified_graph_payload`.

Nació de una revisión (2026-07-07): el puente stdio de VS Code copió el body
de `routes.get_system_graph` y el health ya había divergido el día uno. La
fuente única es la función del domain; los transportes solo la llaman.
"""
from __future__ import annotations

import json

from src.plugins.system_map.domain.serialize import certified_graph_payload, health_payload


def test_payload_shape_completo():
    """El payload trae grafo + certificaciones en vivo, JSON-serializable."""
    payload = certified_graph_payload()
    assert isinstance(payload, dict)
    assert isinstance(payload["nodes"], list) and payload["nodes"]
    assert isinstance(payload["edges"], list)
    assert isinstance(payload["plugins"], list) and payload["plugins"]
    # F-SDK-5: el veredicto TCK viaja con el grafo (una cert por plugin)
    assert len(payload["certifications"]) == len(payload["plugins"])
    json.dumps(payload)  # JSON-ready de punta a punta


def test_tck_roto_degrada_a_warning(monkeypatch):
    """Un bug del TCK no tumba el mapa: grafo sin certs + warning visible.

    El import de collect_certifications es lazy DENTRO del try — un error de
    import del testkit también degrada (no 500)."""

    def _boom(_ids):
        raise RuntimeError("TCK explotó")

    import src.plugins.system_map.domain.certification as certification

    monkeypatch.setattr(certification, "collect_certifications", _boom)
    payload = certified_graph_payload()
    assert payload["certifications"] == []
    assert any("certification_unavailable" in w for w in payload["warnings"])


def test_health_payload_shape():
    """Liveness OK: mismo shape que siempre sirvió routes.health."""
    assert health_payload() == {"status": "ok", "service": "system_map"}


def test_route_health_delega_en_serialize(monkeypatch):
    """`GET /api/system-map/health` sirve EXACTAMENTE health_payload — el
    puente stdio usa la misma función; si el route reimplementa el body
    (drift, como pasó el día uno), este test rompe."""
    from src.plugins.system_map.api import routes

    sentinel = {"status": "sentinel"}
    monkeypatch.setattr(routes, "health_payload", lambda: sentinel)
    assert routes.health() == sentinel


def test_route_http_delega_en_serialize(monkeypatch):
    """`GET /api/system-map/graph` sirve EXACTAMENTE certified_graph_payload —
    si el route reimplementa el body (drift), este test rompe."""
    from src.plugins.system_map.api import routes

    sentinel = {"nodes": [{"id": "sentinel"}], "edges": []}
    monkeypatch.setattr(routes, "certified_graph_payload", lambda: sentinel)
    response = routes.get_system_graph()
    assert json.loads(response.body) == sentinel
    assert response.headers["cache-control"] == "no-store"
