"""Producción se califica como el laboratorio: turno por turno (2026-10-02).

Calidad LLM reemplaza su vista por la del laboratorio (decisión del
operador): cada respuesta del bot con su veredicto, en el hilo, y el
episodio con la misma regla. El scorecard de producción califica el
episodio real en modo turno, igual que el laboratorio califica su brazo de
producción (A0): cada turno con el prefijo real como contexto y el episodio
como estaba al inicio de ese turno (sin el cierre que vino después; la orden,
solo si ya existía).
"""
from __future__ import annotations

from pathlib import Path

from temporalio.testing import ActivityEnvironment

from src.plugins.chats.agent.sales_eval.activities import eval_activities as ea
from src.plugins.chats.agent.sales_eval.scorecard import service, store
from tests.evals.scorecard.incidents import CATALOG_CTX
from tests.evals.scorecard.test_scorecard_activity import SESSION, _patch, _seed


async def test_production_is_graded_turn_by_turn_like_the_lab(tmp_path: Path, monkeypatch) -> None:
    _seed(tmp_path)
    _patch(monkeypatch, tmp_path, [])

    summary = await ActivityEnvironment().run(ea.score_episode_scorecard_activity, SESSION, "ep_007", True)

    saved = store.find_latest(store.scorecards_dir(tmp_path), SESSION, "ep_007")
    assert saved is not None and saved.get("mode") == "turn"
    real = service.load_trajectory(tmp_path, SESSION, "ep_007")
    assert [t["turn"] for t in saved.get("by_turn") or []] == [t.turn for t in real.turns]
    by_turn = {t["turn"]: t for t in saved["by_turn"]}
    assert all(r["turn"] == k for k, t in by_turn.items() for r in t["results"])
    assert (by_turn[4]["verdict"], by_turn[5]["verdict"], by_turn[9]["verdict"]) == ("ALERTA", "FALLA", "FALLA")
    assert saved["verdict"] == "FALLA" == summary.verdict
    # El incidente del PR #281: las mismas fallas, en los mismos turnos, que la
    # calificación del episodio entero.
    episode = service.score_trajectory(real, CATALOG_CTX)
    failing = lambda rows: {(r["check_id"], r["turn"]) for r in rows if r["verdict"] == "falla"}  # noqa: E731
    assert failing(saved["results"]) == failing(episode["results"])
    # Lo que necesitan la lista, la matriz y el embudo, como antes.
    for key in ("fidelity", "stage_final", "closing_tag", "turns", "compliance", "episode_date", "judge", "order_id"):
        assert key in saved, key
    assert (saved["turns"], saved["judge"]) == (len(real.turns), False)


def test_each_turn_sees_the_episode_as_it_was_when_the_turn_started() -> None:
    metadata = {"episodes": [{"episode_id": "ep_001", "started_at_ms": 0, "closed_at_ms": 5_000,
                              "closing_tag": "COMPRA_EXITOSA", "closing_motivo": "pagó", "order_id": "order_9"}]}
    traces = [
        {"episode_id": "ep_001", "turn": 1, "trigger": "customer", "turn_started_ms": 1_000, "state": {"order_id": None}},
        {"episode_id": "ep_001", "turn": 2, "trigger": "customer", "turn_started_ms": 2_000, "state": {"order_id": "order_9"}},
        {"episode_id": "ep_001", "turn": 3, "trigger": "customer", "turn_started_ms": 6_000, "state": {"order_id": "order_9"}},
        {"episode_id": "ep_002", "turn": 1, "trigger": "customer", "turn_started_ms": 7_000, "state": {}},
    ]

    states = service.turn_episode_states(metadata, traces, "ep_001")

    assert set(states) == {1, 2, 3}
    # Sin el cierre que vino después del turno; la orden, la del turno anterior.
    assert states[1]["closed_at_ms"] is None and "closing_tag" not in states[1] and states[1]["order_id"] is None
    assert states[2]["order_id"] is None and states[3]["order_id"] == "order_9"
    # Un turno después del cierre ve el episodio cerrado.
    assert states[3]["closed_at_ms"] == 5_000 and states[3]["closing_tag"] == "COMPRA_EXITOSA"


def test_the_matrix_row_of_a_turn_record_keeps_the_strongest_verdict_per_check() -> None:
    record = {
        "session_id": SESSION, "episode_id": "ep_001", "verdict": "FALLA", "mode": "turn",
        "results": [{"check_id": "DES-09", "verdict": "pasa", "turn": 1}, {"check_id": "DES-09", "verdict": "falla", "turn": 2},
                    {"check_id": "APE-01", "verdict": "pasa", "turn": 1}, {"check_id": "APE-01", "verdict": "no_aplica", "turn": 2}],
        "by_turn": [{"turn": 1}, {"turn": 2}],
    }

    row = store.to_row(record)

    assert row["checks"] == {"DES-09": "falla", "APE-01": "pasa"} and "by_turn" not in row


def test_the_backfill_grades_turn_by_turn_too(tmp_path: Path) -> None:
    """Lo que el backfill califica se lee en la misma vista: turno por turno."""
    from src.plugins.chats.agent.sales_eval.scorecard.backfill import backfill_scorecards
    from tests.evals.scorecard.test_backfill import _events, _session

    _session(tmp_path, "wa_100000000001",
             [{"episode_id": "ep_001", "msgs_count_at_start": 0, "msgs_count_at_close": 8, "closed_at_ms": 1}],
             _events(6))

    assert backfill_scorecards(tmp_path, ctx=CATALOG_CTX).scored == 1

    found = store.find_latest(store.scorecards_dir(tmp_path), "wa_100000000001", "ep_001")
    assert found is not None and found.get("mode") == "turn" and found["fidelity"] == "legacy"


def test_recalculating_from_the_api_grades_turn_by_turn_and_keeps_the_judge(tmp_path: Path, monkeypatch) -> None:
    """Un recálculo nunca vuelve a guardar el formato viejo (el último registro
    manda en la vista) y conserva el juicio que ya había, en su turno."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.plugins.chats.agent.sales_eval.scorecard import catalog_context
    from src.plugins.chats.api import evals as evals_api
    from src.plugins.chats.api import scorecards as api

    _seed(tmp_path)
    monkeypatch.setattr(api, "get_vault_dir", lambda: tmp_path)

    async def ctx(catalog=None):
        return CATALOG_CTX

    monkeypatch.setattr(catalog_context, "build_check_context", ctx)
    store.append_scorecard(store.scorecards_dir(tmp_path), {
        "session_id": SESSION, "episode_id": "ep_007", "verdict": "FALLA", "judge": True,
        "results": [{"check_id": "DES-04", "verdict": "falla", "turn": 3, "evidence": "e", "critique": "c", "source": "judge"}],
    })
    app = FastAPI()
    app.include_router(evals_api.router, prefix="/api/chats")

    TestClient(app).post("/api/chats/evals/scorecard/rescore", json={"session_id": SESSION, "episode_id": "ep_007"})

    saved = store.find_latest(store.scorecards_dir(tmp_path), SESSION, "ep_007")
    assert saved is not None and saved.get("mode") == "turn"
    turn3 = next(t for t in saved["by_turn"] if t["turn"] == 3)
    assert any(r["check_id"] == "DES-04" and r["source"] == "judge" for r in turn3["results"])
