"""F-SDK-5 — el catálogo lleva el veredicto TCK por plugin.

Cubre las tres patas: el colector de dominio (en vivo, 7 plugins reales),
el endpoint HTTP (payload con `certifications`) y el skip honesto de P-27
cuando el artefacto de deploy no trae `tests/` (containers).
"""
from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plugins.system_map.api.routes import router
from src.plugins.system_map.domain.certification import collect_certifications
from src.sdk.manifest_model import ARCHETYPES
from src.sdk.testkit import build_context
from src.sdk.testkit.checks import check_p27_conformance_file


def test_collect_certifications_covers_all_real_plugins() -> None:
    from src.platform.plugin_manifest import all_manifests

    ids = [pid for pid, _ in all_manifests()]
    certs = collect_certifications(ids)
    assert [c.plugin_id for c in certs] == ids
    for cert in certs:
        assert cert.level == "C2", (
            f"{cert.plugin_id}: el repo real debe certificar C2 "
            f"(fails={cert.failed_checks})"
        )
        assert cert.archetype in ARCHETYPES
        assert cert.git_sha and cert.generated_at and cert.sdk


def test_graph_endpoint_ships_certifications() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api/system-map")
    client = TestClient(app)
    payload = client.get("/api/system-map/graph").json()
    certs = payload.get("certifications")
    assert isinstance(certs, list) and certs, "el grafo no trae certifications"
    by_id = {c["plugin_id"]: c for c in certs}
    assert by_id["eta"]["level"] == "C2"
    assert by_id["eta"]["archetype"] == "notifier"
    # los warnings de migración (domain/ en full_stack) viajan para la UI:
    assert any(c["warning_checks"] for c in certs)


def test_p27_skips_honestly_without_tests_dir(tmp_path: Path) -> None:
    """Deploy artifact sin tests/ ⇒ skip con explicación — ni fail falso ni verde inventado."""
    (tmp_path / "frontend_dashboard" / "src" / "plugins" / "solo").mkdir(parents=True)
    (tmp_path / "hubara_agency" / "src" / "plugins" / "solo").mkdir(parents=True)
    manifest = {"id": "solo", "version": "0.1.0", "archetype": "api_only",
                "api": {"python_module": "src.plugins.solo.api"}}
    (tmp_path / "frontend_dashboard" / "src" / "plugins" / "solo" / "plugin.yaml").write_text(
        yaml.safe_dump(manifest), encoding="utf-8"
    )
    ctx = build_context("solo", repo_root=tmp_path)  # sin tests/conformance
    results = check_p27_conformance_file(ctx)
    assert len(results) == 1 and results[0].status == "skip"
    assert "se verifica en CI" in results[0].detail
