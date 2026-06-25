"""Discovery del router del plugin ``graphagents`` por el loader real (``src.main``).

``test_api.py`` testea el router AISLADO (lo monta a mano en un ``FastAPI``
fresco). Esto cierra el otro hueco: que el LOADER del repo (``_bootstrap_routers``
→ ``_discover_plugin_manifests`` + ``_register_router_from_module``) DESCUBRA el
manifest real y monte las rutas — y que las monte PROTEGIDAS (``require_auth``),
no públicas. Es la verificación de comportamiento del gotcha #1 del CLAUDE.md:
no basta con que el manifest declare ``api:``; hay que confirmar que el backend
EMITE las rutas montadas y con la dependency de auth.

``require_auth`` es NO-OP si Cognito no está configurado (dev/tests), así que un
401 no es asertable acá sin mockear el pool. La señal determinista equivalente
es: la dependency ``require_auth`` está COLGADA de cada ruta del plugin (en prod,
con Cognito seteado por tenant, eso ES el enforce). El loader además loguea
``auth=required`` al registrar.
"""
from __future__ import annotations

import sys

import pytest
from fastapi import FastAPI

from src.platform.auth import require_auth

#: Las 5 rutas que el manifest del plugin promete bajo ``prefix: /api/graphagents``.
_EXPECTED_ROUTES = {
    "/api/graphagents/agents",
    "/api/graphagents/runs",
    "/api/graphagents/runs/{run_id}",
    "/api/graphagents/runs/{run_id}/approve",
    "/api/graphagents/runs/{run_id}/events",
}


@pytest.fixture()
def graphagents_app(monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], FastAPI]:
    """Boot del loader real con ``ENABLED_PLUGINS=graphagents`` sobre un app fresco.

    Reusa el patrón de ``test_main_loader._isolated_loader``: NO mutamos
    ``src.main.app`` (singleton compartido); pasamos una ``FastAPI`` propia a
    ``_bootstrap_routers``. Apuntamos al dir de manifests REAL del repo — esto
    es un test de comportamiento end-to-end del discovery, no un fixture sintético.
    """
    monkeypatch.setenv("ENABLED_PLUGINS", "graphagents")
    sys.modules.pop("src.main", None)
    import src.main as mod

    fresh = FastAPI()
    loaded = mod._bootstrap_routers(fresh)
    return loaded, fresh


def test_graphagents_descubierto_por_enabled_plugins(
    graphagents_app: tuple[list[str], FastAPI],
) -> None:
    """``ENABLED_PLUGINS=graphagents`` → el loader descubre y contribuye el plugin."""
    loaded, _ = graphagents_app
    assert loaded == ["graphagents"], (
        f"esperaba que el loader montara solo graphagents, montó {loaded}"
    )


def test_graphagents_rutas_montadas(
    graphagents_app: tuple[list[str], FastAPI],
) -> None:
    """Las 5 rutas del buzón quedan montadas bajo ``/api/graphagents`` (manifest prefix)."""
    _, app = graphagents_app
    paths = {r.path for r in app.routes if hasattr(r, "path")}
    missing = _EXPECTED_ROUTES - paths
    assert not missing, f"rutas del manifest no montadas: {sorted(missing)}"


def test_graphagents_rutas_protegidas_por_require_auth(
    graphagents_app: tuple[list[str], FastAPI],
) -> None:
    """Cada ruta del plugin cuelga ``require_auth`` (fail-closed; en prod = enforce).

    El loader NO marca ``PUBLIC_ROUTER`` para graphagents, así que ``main.py``
    le adjunta ``Depends(require_auth)``. Verificamos la dependency en cada ruta
    del plugin — la prueba de que el buzón nace protegido como el resto del
    dashboard (no como el webhook de Meta).
    """
    _, app = graphagents_app
    ga_routes = [
        r
        for r in app.routes
        if hasattr(r, "path") and r.path.startswith("/api/graphagents")
    ]
    assert ga_routes, "no se montó ninguna ruta /api/graphagents/*"
    for route in ga_routes:
        deps = [d.call for d in route.dependant.dependencies]
        assert require_auth in deps, (
            f"ruta {route.path} no está protegida por require_auth — "
            f"dependencies={deps}"
        )
