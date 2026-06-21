"""TCK del catálogo de CASOS — el artefacto que fija el TRIPLE input de un nodo (seed +
port-fixtures + llm-reply) y su golden, para replayear un tool/agente/flujo desde UNA sola
fuente (lo que el viewer va a listar en un select). El determinismo es la base: un caso es un
golden-replay nombrado.

El rojo es doble (TDD): (1) un caso real REPLAYEA EXACTO a su golden (golden-replay); (2) el
check CAZA un golden desactualizado / un $ref roto / un target inexistente (el caso NEGATIVO
primero) — lo que hace CONFIABLE el catálogo: un golden mentiroso no pasa en silencio.
"""
from __future__ import annotations

from pathlib import Path

from sdk.case_model import Case, discover_cases, load_case, resolve
from sdk.replay import replay_case
from sdk.testkit.case_checks import run_case_checks

GA = Path(__file__).resolve().parents[2]
CASES = GA / "fixtures" / "cases"


def test_load_case_parsea_target_seed_golden() -> None:
    c = load_case(CASES / "diagnose-scale.case.yaml")
    assert c.target == "tool:diagnose"
    assert c.seed == {"payload": {"mer": "3.0", "drop_off_rate": "0.2"}}
    assert c.golden["recommendation"] == "scale_budget"


def test_replay_tool_case_matchea_su_golden() -> None:
    # el caso más chico: un tool PURO, input único → output (tu intuición). impl(**seed).
    c = load_case(CASES / "diagnose-scale.case.yaml")
    assert replay_case(c, GA) == resolve(c.golden, GA)


def test_replay_agent_case_inyecta_el_fixture_del_port() -> None:
    # meta-insights CONSUME el port meta_marketing_api → el caso trae el fixture por $ref, el
    # replay arma FixtureMetaInsights y la inyecta. Esto es lo que /api/run rechaza hoy.
    c = load_case(CASES / "meta-insights-sample.case.yaml")
    out = replay_case(c, GA)
    assert out.get("blended_roas") == 2.3333
    assert [x["campaign_id"] for x in out.get("campaigns", [])] == ["c1", "c2"]


def test_replay_flow_case_threadea_las_5_etapas() -> None:
    # el FLUJO completo: el seed {meta_insights, manual_sales, entities_payload} threadeado por
    # los 5 agentes. "Día del padre" (purchase-conversion) entró por Graph /insights (conv 160).
    c = load_case(CASES / "dia-del-padre-flujo.case.yaml")
    out = replay_case(c, GA)
    assert out.get("currency") == "COP"
    assert out.get("days", [{}])[0].get("conversations_started") == 160
    assert "Día del padre 2026 - mundia" in out.get("markdown", "")


def test_replay_flow_case_matchea_su_golden_exacto() -> None:
    # golden-replay del FLUJO: el output completo == el golden pineado ($ref). Guarda la
    # COMPOSICIÓN (orden de los agentes + bindings), no solo las unidades.
    c = load_case(CASES / "dia-del-padre-flujo.case.yaml")
    assert replay_case(c, GA) == resolve(c.golden, GA)


def test_check_caza_un_golden_desactualizado() -> None:
    # NEGATIVO primero: un caso cuyo golden NO matchea el replay → el check lo caza.
    bad = Case(
        id="diagnose-roto", target="tool:diagnose",
        seed={"payload": {"mer": "3.0", "drop_off_rate": "0.2"}},
        golden={"recommendation": "rotate_creative"},  # el real es scale_budget
    )
    errs = run_case_checks(bad, GA)["errors"]
    assert any("CASE-GOLDEN" in e for e in errs), errs


def test_check_caza_un_ref_que_no_resuelve() -> None:
    bad = Case(
        id="ref-roto", target="tool:diagnose",
        seed={"payload": {"$ref": "fixtures/no-existe.json"}}, golden={},
    )
    errs = run_case_checks(bad, GA)["errors"]
    assert any("CASE-REF" in e for e in errs), errs


def test_check_caza_un_target_inexistente() -> None:
    bad = Case(id="target-roto", target="tool:no-existe", seed={}, golden={})
    errs = run_case_checks(bad, GA)["errors"]
    assert any("CASE-TARGET" in e for e in errs), errs


def test_check_caza_un_port_sin_fixture_vendor() -> None:
    # el 4º negativo: un port inyectado que no tiene fixture vendor (no se puede replayear sin
    # red) → CASE-PORT. Sin esto, un FIXTURE_VENDORS mal mapeado pasaría silencioso.
    bad = Case(
        id="port-roto", target="agent:meta-insights", seed={},
        ports={"no_vendor": {"x": 1}}, golden={},
    )
    errs = run_case_checks(bad, GA)["errors"]
    assert any("CASE-PORT" in e for e in errs), errs


def test_todos_los_casos_del_catalogo_pasan_el_check() -> None:
    # el catálogo en vivo: cada .case.yaml resuelve sus refs y REPLAYEA a su golden.
    cases = discover_cases(GA)
    assert cases, "no hay casos en fixtures/cases/"
    for c in cases:
        res = run_case_checks(c, GA)
        assert res["errors"] == [], f"{c.id}: {res['errors']}"
