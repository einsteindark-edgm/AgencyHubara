"""TCK de manifests: el extractor `meta-insights` y el supervisor `ads-supervisor`
(que bindea la tool de catálogo `recommend-budget` — ejercita G-BIND)."""
from __future__ import annotations

from pathlib import Path

from sdk.manifest_model import load_manifest
from sdk.testkit.checks import level_of, run_checks

GA = Path(__file__).resolve().parents[2]


def test_meta_insights_carga_y_alcanza_al_menos_C1() -> None:
    m = load_manifest(GA / "manifests" / "meta-insights.agent.yaml")
    res = run_checks(m, GA)
    assert res["errors"] == [], res["errors"]  # C1: la capability importa
    assert level_of(m, GA) in {"C1", "C2", "C3"}


def test_supervisor_certifica_al_menos_C2() -> None:
    m = load_manifest(GA / "manifests" / "ads-supervisor.taskgraph.yaml")
    # G-BIND: recommend-budget existe en el catálogo; las capabilities importan.
    assert level_of(m, GA) in {"C2", "C3"}, run_checks(m, GA)
