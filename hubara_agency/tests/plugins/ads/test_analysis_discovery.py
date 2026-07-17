"""Discovery del buzón de análisis del plugin ``ads`` por el loader real (``src.main``).

``test_analysis_api.py`` testea el router AISLADO (lo monta a mano en un ``FastAPI``
fresco). Esto cierra el otro hueco: que el LOADER del repo (``_bootstrap_routers``
→ ``_discover_plugin_manifests`` + ``_register_router_from_module``) DESCUBRA el
manifest real de ``ads`` y monte las rutas del buzón ``/api/ads/analysis/*`` — y que
las monte PROTEGIDAS (``require_auth``), no públicas. Es la verificación de
comportamiento del gotcha #1 del CLAUDE.md: no basta con que el manifest declare
``api:``; hay que confirmar que el backend EMITE las rutas montadas y con la
dependency de auth.

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

#: Las 5 rutas del buzón de análisis que ``ads`` promete bajo ``prefix: /api/ads``
#: (sub-router ``/analysis``).
_EXPECTED_ROUTES = {
    "/api/ads/analysis/agents",
    "/api/ads/analysis/runs",
    "/api/ads/analysis/runs/{run_id}",
    "/api/ads/analysis/runs/{run_id}/approve",
    "/api/ads/analysis/runs/{run_id}/events",
}


@pytest.fixture()
def ads_app(monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], FastAPI]:
    """Boot del loader real con ``ENABLED_PLUGINS=ads`` sobre un app fresco.

    Reusa el patrón de ``test_main_loader._isolated_loader``: NO mutamos
    ``src.main.app`` (singleton compartido); pasamos una ``FastAPI`` propia a
    ``_bootstrap_routers``. Apuntamos al dir de manifests REAL del repo — esto
    es un test de comportamiento end-to-end del discovery, no un fixture sintético.
    """
    monkeypatch.setenv("ENABLED_PLUGINS", "ads")
    saved = sys.modules.get("src.main")
    sys.modules.pop("src.main", None)
    import src.main as mod

    # Restaurar el módulo ORIGINAL en el registry: el re-import de arriba dejó
    # en sys.modules un src.main cuyo `app` global se construyó con
    # ENABLED_PLUGINS=ads — cualquier test posterior que haga
    # `from src.main import app` recibía una app SOLO-ads (404 en rutas de
    # chats; fallo dependiente del orden). El módulo fresco solo se usa acá
    # vía la referencia local `mod`.
    if saved is not None:
        sys.modules["src.main"] = saved
    else:
        sys.modules.pop("src.main", None)

    fresh = FastAPI()
    loaded = mod._bootstrap_routers(fresh)
    return loaded, fresh


def test_ads_descubierto_por_enabled_plugins(
    ads_app: tuple[list[str], FastAPI],
) -> None:
    """``ENABLED_PLUGINS=ads`` → el loader descubre y contribuye el plugin."""
    loaded, _ = ads_app
    assert loaded == ["ads"], (
        f"esperaba que el loader montara solo ads, montó {loaded}"
    )


def test_analysis_rutas_montadas(
    ads_app: tuple[list[str], FastAPI],
) -> None:
    """Las 5 rutas del buzón quedan montadas bajo ``/api/ads/analysis`` (prefix + sub-router)."""
    _, app = ads_app
    paths = {r.path for r in app.routes if hasattr(r, "path")}
    missing = _EXPECTED_ROUTES - paths
    assert not missing, f"rutas del buzón no montadas: {sorted(missing)}"


def test_analysis_rutas_protegidas_por_require_auth(
    ads_app: tuple[list[str], FastAPI],
) -> None:
    """Cada ruta del buzón cuelga ``require_auth`` (fail-closed; en prod = enforce).

    El loader NO marca ``PUBLIC_ROUTER`` para ads, así que ``main.py`` le adjunta
    ``Depends(require_auth)``. Verificamos la dependency en cada ruta ``/api/ads/analysis``
    — la prueba de que el buzón nace protegido como el resto del dashboard (no como
    el webhook de Meta).
    """
    _, app = ads_app
    analysis_routes = [
        r
        for r in app.routes
        if hasattr(r, "path") and r.path.startswith("/api/ads/analysis")
    ]
    assert analysis_routes, "no se montó ninguna ruta /api/ads/analysis/*"
    for route in analysis_routes:
        deps = [d.call for d in route.dependant.dependencies]
        assert require_auth in deps, (
            f"ruta {route.path} no está protegida por require_auth — "
            f"dependencies={deps}"
        )
