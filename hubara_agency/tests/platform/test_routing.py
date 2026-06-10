"""Unit tests del route registry declarativo (platform/routing — F6)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import src.platform.plugin_manifest as pm
import src.platform.routing as routing


@pytest.fixture(autouse=True)
def _clear_route_cache():
    """El registry cachea por proceso; estos tests apuntan manifests temporales
    — limpiar antes Y después para no envenenar suites vecinas."""
    routing._cached_registry.cache_clear()
    yield
    routing._cached_registry.cache_clear()


def _write_manifest(plugins_dir: Path, pid: str, body: dict) -> None:
    d = plugins_dir / pid
    d.mkdir(parents=True, exist_ok=True)
    (d / "plugin.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")


def _isolate(monkeypatch: pytest.MonkeyPatch, plugins_dir: Path) -> None:
    monkeypatch.setattr(pm, "_PLUGINS_MANIFEST_DIR", plugins_dir)
    routing._cached_registry.cache_clear()


def _worker(name: str, **extra) -> dict:
    return {"name": name, "module": f"src.plugins.x.workers.{name}", **extra}


def test_resolves_declared_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_manifest(
        tmp_path,
        "alpha",
        {
            "id": "alpha",
            "agent": {
                "workers": [
                    _worker(
                        "a",
                        owns_route="alpha",
                        route_workflow_id_template="alpha-{session_id}",
                    )
                ]
            },
        },
    )
    _isolate(monkeypatch, tmp_path)
    monkeypatch.delenv("ENABLED_PLUGINS", raising=False)

    assert routing.resolve_route_workflow_id("alpha", "wa_9") == "alpha-wa_9"
    # Rutas core / desconocidas → None (el caller hace fallback a Sales).
    assert routing.resolve_route_workflow_id("ventas", "wa_9") is None
    assert routing.resolve_route_workflow_id("nope", "wa_9") is None


def test_disabled_plugin_route_does_not_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INV-2: apagar el plugin dueño ⇒ su ruta no resuelve ⇒ fallback limpio
    (espejo del dispatcher-skip P-7 para el camino INBOUND)."""
    _write_manifest(
        tmp_path,
        "alpha",
        {
            "id": "alpha",
            "agent": {
                "workers": [
                    _worker(
                        "a",
                        owns_route="alpha",
                        route_workflow_id_template="alpha-{session_id}",
                    )
                ]
            },
        },
    )
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("ENABLED_PLUGINS", "otro")
    assert routing.resolve_route_workflow_id("alpha", "wa_9") is None
    routing._cached_registry.cache_clear()
    monkeypatch.setenv("ENABLED_PLUGINS", "alpha")
    assert routing.resolve_route_workflow_id("alpha", "wa_9") == "alpha-wa_9"


@pytest.mark.parametrize(
    "worker_extra, match",
    [
        (
            {"owns_route": "ventas", "route_workflow_id_template": "v-{session_id}"},
            "CORE",
        ),
        ({"owns_route": "alpha", "route_workflow_id_template": "alpha-fijo"}, "session_id"),
    ],
)
def test_invalid_route_declarations_fail_fast(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_extra: dict,
    match: str,
) -> None:
    _write_manifest(
        tmp_path,
        "alpha",
        {"id": "alpha", "agent": {"workers": [_worker("a", **worker_extra)]}},
    )
    _isolate(monkeypatch, tmp_path)
    monkeypatch.delenv("ENABLED_PLUGINS", raising=False)
    with pytest.raises(routing.RouteRegistryError, match=match):
        routing.conversation_routes()


def test_route_collision_fails_fast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for pid in ("alpha", "beta"):
        _write_manifest(
            tmp_path,
            pid,
            {
                "id": pid,
                "agent": {
                    "workers": [
                        _worker(
                            "w",
                            owns_route="shared",
                            route_workflow_id_template="s-{session_id}",
                        )
                    ]
                },
            },
        )
    _isolate(monkeypatch, tmp_path)
    monkeypatch.delenv("ENABLED_PLUGINS", raising=False)
    with pytest.raises(routing.RouteRegistryError, match="exclusivas"):
        routing.conversation_routes()
