"""El lector de Jev del Order Sentinel sale de su paquete (PAQUETES_DE_DECISION.md F8).

Lo que el lector le pregunta a Jev («¿qué cambió?» con sus seis opciones, la
evidencia mensaje por mensaje y lo que cada cambio afirma), la certeza que
pide (0,85) y cómo junta la evidencia son el paquete `centinela`
(`order_sentinel/agent/decisions/bundles/`), sobre el MISMO motor genérico de
las capacidades de ventas y certificado con `decisions check`. Lo que sigue
siendo código son los builtins: el texto de la conversación que ve Jev, qué
mensajes pueden ser evidencia (el pago, solo el equipo) y la forma del
veredicto (la del LLM, para que las guardas del grafo no cambien).

Pasar el lector al paquete no cambia ni un carácter de lo que pregunta ni
de la lectura: la foto de antes (`fixtures/order_sentinel/readings_frozen.json`)
lo exige en toda la grilla.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.fixtures import generate_order_sentinel_readings_fixture as grid

FROZEN = json.loads(
    (Path(__file__).resolve().parents[2] / "fixtures/order_sentinel/readings_frozen.json").read_text(encoding="utf-8")
)
PACKAGE = "src.plugins.order_sentinel.agent.decisions"


def _decisions() -> Any:
    assert importlib.util.find_spec(PACKAGE) is not None, "falta el paquete del lector (order_sentinel/agent/decisions)"
    return importlib.import_module(PACKAGE)


@pytest.fixture(autouse=True)
def _fresh_bundle():
    yield
    if importlib.util.find_spec(PACKAGE) is not None:
        importlib.import_module(PACKAGE).reset()


def test_the_reader_bundle_is_certified() -> None:
    from src.sdk.decisionkit import check_bundle

    decisions = _decisions()

    assert check_bundle(decisions.BUNDLES_DIR / decisions.BUNDLE_ID, decisions.CATALOG_PATH) == []
    bundle = decisions.active_bundle()
    assert (bundle.ref, sorted(bundle.capabilities)) == ("centinela@1", ["cambio", "evidencia"])


def test_the_certifier_finds_it_with_the_others() -> None:
    from src.sdk.cli.decisions import discover_bundles

    decisions = _decisions()
    repo = Path(__file__).resolve().parents[4]

    assert decisions.BUNDLES_DIR / decisions.BUNDLE_ID in discover_bundles(repo)


def test_the_catalog_and_the_code_declare_the_same_builtins() -> None:
    decisions = _decisions()
    catalog = yaml.safe_load(decisions.CATALOG_PATH.read_text(encoding="utf-8"))

    assert {name: spec["kind"] for name, spec in catalog["builtins"].items()} == {
        name: kind for name, (kind, _fn) in decisions.BUILTINS.items()
    }


# ── nada cambió ──────────────────────────────────────────────────────────────


def test_the_reader_asks_exactly_what_it_asked() -> None:
    from src.plugins.order_sentinel.agent.cycle.use_cases import readings

    for name, convo in grid.CONVOS.items():
        state, questions = readings.change_request(convo)
        assert {"state": state, "questions": grid._questions(questions)} == FROZEN["requests"][name], name
        assert readings.conversation_state(convo) == state


def test_the_evidence_question_is_exactly_the_same() -> None:
    from src.plugins.order_sentinel.agent.cycle.use_cases import readings

    for name, convo in grid.CONVOS.items():
        for change in grid.CHANGES:
            for since in grid.SINCE:
                request = readings.evidence_request(convo, change, since_ms=since)
                got = None if request is None else grid._questions(request[1])
                assert got == FROZEN["evidence"][f"{name}|{change}|{since}"], (name, change, since)
                if request is not None:
                    assert request[0] == FROZEN["requests"][name]["state"]


def test_the_reading_is_exactly_the_same() -> None:
    frozen = grid.build()

    assert frozen["needs"] == FROZEN["needs"]
    assert set(frozen["readings"]) == set(FROZEN["readings"])
    for key, reading in frozen["readings"].items():
        assert reading == FROZEN["readings"][key], key


# ── el paquete manda ─────────────────────────────────────────────────────────


@pytest.fixture
def edited_bundle(tmp_path: Path, monkeypatch):
    """Una copia del paquete, editada, como la del lector."""

    def make(capability: str, edit) -> None:
        decisions = _decisions()
        bundles = tmp_path / "bundles"
        shutil.copytree(decisions.BUNDLES_DIR, bundles)
        path = bundles / decisions.BUNDLE_ID / "capabilities" / f"{capability}.yaml"
        path.write_text(edit(path.read_text(encoding="utf-8")), encoding="utf-8")
        monkeypatch.setattr(decisions, "BUNDLES_DIR", bundles)
        monkeypatch.setattr(decisions, "CATALOG_PATH", bundles / "builtins.yaml")
        decisions.reset()

    return make


def test_the_certainty_the_reader_asks_for_is_the_bundle_s(edited_bundle) -> None:
    from src.plugins.order_sentinel.agent.cycle.use_cases import readings

    first = grid._first("listo", 0.84)
    assert readings.needs_evidence(first) is None

    # Otra certeza (y su ejemplo en el borde: si no, el paquete no certifica).
    edited_bundle(
        "cambio",
        lambda text: text.replace('"yes": 0.85', '"yes": 0.80').replace("{choice: listo, p: 0.84}", "{choice: listo, p: 0.79}"),
    )

    assert readings.needs_evidence(first) == "listo"


def test_what_each_change_claims_is_the_bundle_s(edited_bundle) -> None:
    from src.plugins.order_sentinel.agent.cycle.use_cases import readings

    edited_bundle("evidencia", lambda text: text.replace("dice que el pedido ya está listo", "afirma que el pedido está listo"))

    _state, questions = readings.evidence_request(grid.CONVOS["salio"], "listo")

    assert all("afirma que el pedido está listo" in q.text for q in questions)


def test_the_photo_is_not_regenerated_by_accident() -> None:
    """La foto es del lector ANTES de su paquete: regenerarla con el lector de
    hoy la vuelve tautológica (premortem 2026-10-02). El generador se niega a
    sobrescribirla sin `--regenerar`."""
    import subprocess
    import sys

    hub = Path(__file__).resolve().parents[3]
    proc = subprocess.run(
        [sys.executable, "tests/fixtures/generate_order_sentinel_readings_fixture.py"],
        cwd=hub, capture_output=True, text=True, timeout=120,
    )

    assert proc.returncode != 0 and "--regenerar" in (proc.stderr + proc.stdout)
