"""Resumen de una corrida (plan §5 y PR 13): lo que lee la pestaña Resumen
del laboratorio por `/api/chats/lab/runs/{corrida}/summary` y `/diff`.

  * `arms.<brazo>`: la MISMA forma que Calidad LLM (`stats.compute_stats`),
    ahora en modo turno para las cuatro columnas, + pass^k;
  * `production`: lo que decía el scorecard de producción (modo episodio),
    como referencia;
  * `diffs`: A0→A1 (fidelidad) y A1→B, A1→C, con intervalo;
  * `fidelity`: A1 contra A0 en checks de código;
  * `arena`: métricas de los bots nuevos y acuerdo con los asuntos del juez.
"""
from __future__ import annotations

from src.plugins.chats.agent.sales_lab.run.summary import build_summary, with_verdicts


def _rec(arm: str, verdict: str, *, turns: dict[int, dict[str, str]], topics: list[dict] | None = None) -> dict:
    by_turn = []
    results = []
    for k, checks in sorted(turns.items()):
        rows = [{"check_id": cid, "verdict": v, "turn": k} for cid, v in checks.items()]
        if topics is not None and k == 1:
            rows.append({"check_id": "EST-08", "verdict": "pasa", "turn": 1, "topics": topics})
        by_turn.append({"turn": k, "verdict": "FALLA" if "falla" in checks.values() else "PASA", "results": rows})
        results.extend(rows)
    return {"mode": "turn", "arm": arm, "session_id": "wa_1", "episode_id": "ep_1", "verdict": verdict,
            "stage_final": "descubrimiento", "episode_date": "2026-09-15", "by_turn": by_turn, "results": results}


def _summary() -> dict:
    topics = [{"topic": "catálogo"}, {"topic": "envío a Bogotá"}]
    scores = {
        "A0": [[_rec("A0", "FALLA", turns={1: {"EST-06": "falla", "APE-01": "pasa"}})]],
        "A1": [[_rec("A1", "FALLA", turns={1: {"EST-06": "falla", "APE-01": "pasa"}})]],
        "B": [[_rec("B", "PASA", turns={1: {"EST-06": "pasa", "APE-01": "pasa"}}, topics=topics)]],
    }
    rows = {"B": [[{"session_id": "wa_1", "episode_id": "ep_1", "turn": 1, "steps": [{"kind": "perception", "answers": [
        {"q": "topic.catalogo", "p": 0.9, "picked": True}, {"q": "topic.envio", "p": 0.3, "picked": False}]}]}]]}
    metrics = {"B": [{"arm": "B", "rep": 0, "turns": 1}], "A1": [{"arm": "A1", "rep": 0, "turns": 1}]}
    previous = {"arms": {"A0": {"reps": 1, "episodes": 1, "verdicts": {"FALLA": 1}}}}
    return build_summary(run_id="run-1", registry_version=4, previous=previous, scores=scores, metrics=metrics,
                         rows=rows, code_checks={"EST-06", "APE-01"})


def test_every_arm_gets_the_quality_charts_in_turn_mode() -> None:
    s = _summary()

    assert set(s["arms"]) == {"A0", "A1", "B"}
    b = s["arms"]["B"]
    assert (b["mode"], b["reps"], b["episodes"]) == ("turn", 1, 1)
    assert b["verdicts"]["PASA"] == 1 and b["pass_k"]["rate"] == 1.0
    assert {p["check_id"] for p in s["arms"]["A1"]["pareto"]} == {"EST-06"}
    assert s["production"] == {"reps": 1, "episodes": 1, "verdicts": {"FALLA": 1}}
    assert s["arms_pending"] == [] and s["registry_version"] == 4


def test_diffs_fidelity_and_arena() -> None:
    s = _summary()

    assert set(s["diffs"]) == {"A0:A1", "A1:B"}
    assert s["diffs"]["A1:B"]["episode_pass"]["delta"] == 1.0
    assert s["fidelity"]["agreement"] == 1.0 and s["fidelity"]["ok"] is True
    arena_b = s["arena"]["B"]
    assert arena_b["profile"] == "jev-v1" and arena_b["metrics"] == [{"arm": "B", "rep": 0, "turns": 1}]
    assert arena_b["topics"]["turns"] == 1
    assert (arena_b["topics"]["precision"], arena_b["topics"]["recall"]) == (1.0, 0.5)
    assert "A1" not in s["arena"]  # el bot actual no tiene clasificador


def test_the_conversation_index_carries_the_verdict_of_each_arm() -> None:
    index = [{"session_id": "wa_1", "verdicts": {"A0": {"ep_1": "FALLA"}}}]
    scores = {"A0": [[_rec("A0", "ALERTA", turns={1: {}})]], "B": [[_rec("B", "PASA", turns={1: {}})]]}

    out = with_verdicts(index, scores)

    assert out[0]["verdicts"] == {"A0": {"ep_1": "ALERTA"}, "B": {"ep_1": "PASA"}}
    assert out[0]["verdicts_production"] == {"ep_1": "FALLA"}


def test_validation_compares_the_production_scorecard_with_turn_mode() -> None:
    """Plan §5.3: el modo turno tiene que reproducir las fallas del scorecard
    de producción. La primera corrida real lo mide sobre todo el banco (los
    91 episodios) dentro de la caja: solo conteos por check, sin datos del
    cliente."""
    from src.plugins.chats.agent.sales_lab.run.summary import validation

    prod = [
        {"session_id": "wa_1", "episode_id": "ep_1", "verdict": "FALLA", "registry_version": 3,
         "results": [{"check_id": "EST-06", "verdict": "falla"}, {"check_id": "APE-01", "verdict": "pasa"}]},
        {"session_id": "wa_2", "episode_id": "ep_1", "verdict": "PASA", "registry_version": 3,
         "results": [{"check_id": "EST-06", "verdict": "pasa"}, {"check_id": "APE-01", "verdict": "pasa"}]},
    ]
    turn = [
        _rec("A0", "FALLA", turns={1: {"EST-06": "pasa"}, 2: {"EST-06": "falla", "APE-01": "pasa"}}),
        {**_rec("A0", "ALERTA", turns={1: {"EST-06": "falla", "APE-01": "pasa"}}), "session_id": "wa_2"},
    ]

    out = validation(prod, turn, code_checks={"EST-06", "APE-01"})

    assert out["episodes"] == 2 and out["prod_registry_versions"] == [3]
    est06 = out["checks"]["EST-06"]
    assert (est06["both_fail"], est06["only_production"], est06["only_turn"], est06["agree"]) == (1, 0, 1, 1)
    assert out["checks"]["APE-01"]["agree"] == 2
    assert out["agreement"] == 3 / 4 and out["verdict_agreement"] == 1 / 2
