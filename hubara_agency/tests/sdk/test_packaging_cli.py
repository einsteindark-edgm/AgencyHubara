"""Verbos ``package`` del CLI hubara — la superficie que consume Acktos Studio.

Salida ``--json`` estable (Studio la parsea); exit codes: 0 ok · 1 problema ·
2 uso inválido. ``--repo`` apunta el CLI a cualquier repo Hubara-shaped
(default: el repo actual) — así el MISMO binario exporta del central e
instala en un clon forjado.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.sdk.cli import main

from tests.sdk.test_packaging import _mini_repo, _target_repo


def test_package_plan_json(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    root = _mini_repo(tmp_path)
    assert main(["package", "plan", "beta", "--repo", str(root), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [u["id"] for u in payload["units"]] == ["alpha", "beta"]
    beta = payload["units"][1]
    assert "WHATSAPP_PHONE_NUMBER_ID" in beta["requires"]["env_vars"]
    assert beta["requires"]["secrets"] == ["WHATSAPP_ACCESS_TOKEN"]


def test_package_build_e_inspect(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    root = _mini_repo(tmp_path)
    out = tmp_path / "dist" / "beta.acktospkg"
    assert (
        main(["package", "build", "beta", "--repo", str(root), "-o", str(out)]) == 0
    )
    assert out.exists()
    capsys.readouterr()
    assert main(["package", "inspect", str(out), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["format"] == "acktospkg/1"
    assert sorted(u["id"] for u in payload["units"]) == ["alpha", "beta"]


def test_package_plan_install_e_install(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    origen = _mini_repo(tmp_path)
    out = tmp_path / "beta.acktospkg"
    main(["package", "build", "beta", "--repo", str(origen), "-o", str(out)])
    capsys.readouterr()

    destino = _target_repo(tmp_path)
    assert (
        main(["package", "plan-install", str(out), "--repo", str(destino), "--json"])
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert {u["id"]: u["action"] for u in payload["units"]} == {
        "alpha": "new",
        "beta": "new",
    }
    beta = next(u for u in payload["units"] if u["id"] == "beta")
    assert beta["version"] == "0.1.0", "Studio muestra pkg → destino"
    assert beta["target_version"] is None and beta["downgrade"] is False
    assert beta["bump_pending"] is False, "campo presente para Studio"
    assert payload["missing_plugins"] == []
    assert payload["post_steps"], "Studio muestra los pasos post-install"

    assert main(["package", "install", str(out), "--repo", str(destino)]) == 0
    assert (destino / "hubara_agency/src/plugins/beta/domain/logic.py").exists()
    assert (
        destino / "hubara_agency/tests/conformance/test_beta_conformance.py"
    ).exists()
    capsys.readouterr()

    # reinstalar lo mismo: idempotente, y el JSON lo dice (Studio hace no-op)
    assert (
        main(["package", "install", str(out), "--repo", str(destino), "--json"]) == 0
    )
    rpayload = json.loads(capsys.readouterr().out)
    assert sorted(rpayload["skipped_unchanged"]) == ["alpha", "beta"]
    assert rpayload["installed"] == [] and rpayload["replaced"] == []


def test_package_plan_plugin_inexistente_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    root = _mini_repo(tmp_path)
    assert main(["package", "plan", "nope", "--repo", str(root)]) == 2
    assert "nope" in capsys.readouterr().err
