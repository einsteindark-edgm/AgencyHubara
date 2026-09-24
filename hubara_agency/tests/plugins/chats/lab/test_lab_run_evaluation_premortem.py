"""La evaluación de una corrida no engaña ni se cae (premortem del laboratorio, PR 13b).

  * La caja reporta avance DURANTE la evaluación: con juez, un brazo tarda
    ~1 h y el lanzador de producción da la corrida por caída a los 20 min sin
    noticias (y le pide a la caja que pare). Se califica por pedazos de
    sesiones enteras y cada pedazo deja su avance.
  * El juez también gasta: se suma al gasto de la corrida, el estimado
    cuenta la pasada sobre el control real (A0) y, si el juez no cabe en lo
    que queda del tope, la corrida se califica sin juez (igual para todos).
  * Un bot que el tope nunca alcanzó a simular queda pendiente: no es un bot
    que falló todo.
  * "Concluyente" pide al menos 15 conversaciones, y las diferencias por
    check se corrigen por comparaciones múltiples (Holm).
  * Una corrida grande no llega al límite de historia de Temporal: deja de
    simular antes y califica lo que alcanzó.
  * Un reintento del resumen no pierde la referencia de producción.
"""
from __future__ import annotations

import json

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.plugins.chats.agent.sales_lab.launch.costs import JUDGE_USD_PER_TURN, estimate_run_usd
from src.plugins.chats.agent.sales_lab.run import activities as run_acts
from src.plugins.chats.agent.sales_lab.run.arena import judge_topic_codes, topic_arena
from src.plugins.chats.agent.sales_lab.run.compare import (
    MIN_CONCLUSIVE_SESSIONS,
    diff_entry,
    paired_bootstrap,
    pass_k,
)
from src.plugins.chats.agent.sales_lab.run.contracts import LAB_TASK_QUEUE, LabRunInput
from src.plugins.chats.agent.sales_lab.run.evaluate import evaluation_chunk
from src.plugins.chats.agent.sales_lab.run.summary import build_summary
from src.plugins.chats.agent.sales_lab.run.workflow import HISTORY_NOTE, JUDGE_TURN_USD, LabRunWorkflow
from tests.plugins.chats.lab.test_lab_cases import SID
from tests.plugins.chats.lab.test_lab_run_arms import sims  # noqa: F401  (fixture)
from tests.plugins.chats.lab.test_lab_run_box import RUN, _run, box  # noqa: F401  (fixture)

SID2 = "wa_573007654321"


def _order(box, **over) -> None:  # noqa: F811
    order = json.loads(box["store"].get_bytes(f"orders/{RUN}.json"))
    box["store"].put_bytes(f"orders/{RUN}.json", json.dumps({**order, **over}).encode())


def _progress(box) -> dict:  # noqa: F811
    return json.loads(box["store"].get_bytes(f"runs/{RUN}/progress.json"))


def _summary(box) -> dict:  # noqa: F811
    return json.loads(box["store"].get_bytes(f"runs/{RUN}/summary.json"))


def _second_session(box) -> None:  # noqa: F811
    """Copia la sesión del banco con otro número: dos conversaciones de 2 turnos."""
    store = box["store"]
    for key in store.list_keys("bench/bench-x/"):
        if SID not in key:
            continue
        raw = store.get_bytes(key) or b""
        store.put_bytes(key.replace(SID, SID2), raw.replace(SID.encode(), SID2.encode()))
    manifest = json.loads(store.get_bytes("bench/bench-x/manifest.json"))
    store.put_bytes("bench/bench-x/manifest.json", json.dumps({**manifest, "sessions": [SID, SID2]}).encode())


class _Judge:
    """Juez falso: responde, pero sin veredictos (cada check queda desconocido)."""

    def __init__(self) -> None:
        self.prompts = 0

    async def a_generate(self, prompt: str) -> str:
        self.prompts += 1
        return '{"turnos": []}'


# ── C-H1: avance durante la evaluación ─────────────────────────────────────


def test_evaluation_chunks_are_whole_sessions_that_fit_in_the_turn_budget() -> None:
    cases = [{"session_id": s, "turn": k} for s, n in (("wa_a", 2), ("wa_b", 5), ("wa_c", 1)) for k in range(n)]

    first, nxt = evaluation_chunk(cases, 0, max_turns=4)
    second, nxt2 = evaluation_chunk(cases, nxt, max_turns=4)
    third, end = evaluation_chunk(cases, nxt2, max_turns=4)

    assert ({c["session_id"] for c in first}, nxt) == ({"wa_a"}, 1)
    assert ({c["session_id"] for c in second}, len(second), nxt2) == ({"wa_b"}, 5, 2)  # una sesión nunca se parte
    assert ({c["session_id"] for c in third}, end) == ({"wa_c"}, None)


@pytest.mark.asyncio
async def test_the_box_reports_progress_after_every_evaluation_chunk(box, sims, monkeypatch) -> None:  # noqa: F811
    _second_session(box)
    monkeypatch.setenv("LAB_EVAL_CHUNK_TURNS", "2")  # un pedazo por conversación
    store = box["store"]
    phases: list[str] = []
    put = store.put_bytes

    def spy(key, data):
        if key == f"runs/{RUN}/progress.json":
            phases.append(json.loads(data)["phase"])
        return put(key, data)

    monkeypatch.setattr(store, "put_bytes", spy)

    result = await _run(box)  # A1 y B, 1 repetición

    assert result["phase"] == "done"
    # A0, A1 y B × 2 conversaciones = 6 pedazos, cada uno con su avance
    assert phases.count("evaluating") >= 1 + 6
    for arm in ("A0", "A1", "B"):
        for sid in (SID, SID2):
            assert store.get_bytes(f"runs/{RUN}/scores/{arm}/0/{sid}.jsonl") is not None


# ── C-H4: el gasto del juez ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_judge_spend_counts_toward_the_run(box, sims, monkeypatch) -> None:  # noqa: F811
    _order(box, arms=["A1"], reps=1)
    judge = _Judge()
    monkeypatch.setattr(run_acts, "_judge", lambda: judge)

    await _run(box)

    assert judge.prompts > 0
    simulated = 3 * 0.01  # el humo y los 2 casos de A1
    judged = 4 * JUDGE_USD_PER_TURN  # 2 turnos de A0 y 2 de A1
    assert _progress(box)["spent_usd"] == pytest.approx(simulated + judged, abs=1e-5)


@pytest.mark.asyncio
async def test_a_judge_that_does_not_fit_in_the_cap_is_skipped_for_every_bot(box, sims, monkeypatch) -> None:  # noqa: F811
    _order(box, arms=["A1"], reps=1, spend_limit_usd=0.05)  # simular (0,03) cabe; el juez (0,033) no
    judge = _Judge()
    monkeypatch.setattr(run_acts, "_judge", lambda: judge)

    result = await _run(box)

    assert result["phase"] == "done"
    assert judge.prompts == 0
    progress = _progress(box)
    assert progress["spent_usd"] <= 0.05
    assert any("sin juez" in n for n in progress["notes"])


def test_the_workflow_charges_the_judge_rate_the_launcher_estimates() -> None:
    """El workflow no importa el módulo de costos (sandbox de Temporal): la
    tarifa del juez se repite y este guarda las mantiene iguales."""
    assert JUDGE_TURN_USD == JUDGE_USD_PER_TURN


def test_the_estimate_counts_the_judge_pass_over_the_real_control() -> None:
    """La caja re-mide A0 con el juez antes de comparar: una pasada más del
    juez sobre el banco que el estimado no contaba."""
    with_control = estimate_run_usd(["A1"], reps=1, turns=1000)
    one_pass = round(1000 * JUDGE_USD_PER_TURN, 2)

    assert with_control == pytest.approx(estimate_run_usd(["A1"], reps=1, turns=1000, control=False) + one_pass, abs=0.02)


# ── C-H5: bots que el tope no alcanzó a simular ────────────────────────────


@pytest.mark.asyncio
async def test_a_bot_the_run_never_simulated_is_pending_not_failed(box, sims) -> None:  # noqa: F811
    _order(box, arms=["A1", "B"], reps=1, spend_limit_usd=0.02)  # el humo + A1 llegan al tope
    sims["cost"] = 0.01

    await _run(box)

    summary = _summary(box)
    assert "B" not in summary["arms"] and summary["arms_pending"] == ["B"]
    assert "A1:B" not in summary["diffs"] and "B" not in summary["arena"]
    assert box["store"].get_bytes(f"runs/{RUN}/scores/B/0/{SID}.jsonl") is None


def _rec(sid: str, verdict: str, *, turns: int = 1) -> dict:
    by_turn = [{"turn": k, "verdict": verdict, "results": [{"check_id": "EST-06", "verdict": "pasa", "turn": k}]}
               for k in range(1, turns + 1)]
    return {"mode": "turn", "session_id": sid, "episode_id": "ep_001", "verdict": verdict, "by_turn": by_turn,
            "results": [r for t in by_turn for r in t["results"]]}


def test_episodes_without_simulated_turns_do_not_count_as_failures() -> None:
    """El tope cortó la repetición a mitad: los episodios que no llegó a
    simular salen `SIN_DATOS` y sin turnos. No son fallas del bot."""
    base = [[_rec(f"wa_{i}", "PASA") for i in range(4)]]
    cand = [[_rec("wa_0", "PASA"), _rec("wa_1", "PASA"), _rec("wa_2", "SIN_DATOS", turns=0), _rec("wa_3", "SIN_DATOS", turns=0)]]

    diff = diff_entry("A1", "B", base, cand)

    assert diff["episode_pass"]["sessions"] == 2 and diff["episode_pass"]["delta"] == 0.0
    assert pass_k(cand) == {"k": 1, "episodes": 2, "rate": 1.0}


# ── C-H6: concluyente con suficientes conversaciones y corrección de Holm ──


def test_a_difference_over_few_conversations_is_never_conclusive() -> None:
    n = MIN_CONCLUSIVE_SESSIONS - 1
    base = {f"wa_{i}": [0.0] for i in range(n)}
    cand = {f"wa_{i}": [1.0] for i in range(n)}

    boot = paired_bootstrap(base, cand)

    assert boot["low"] > 0 and boot["conclusive"] is False


def test_a_large_consistent_difference_is_conclusive() -> None:
    n = MIN_CONCLUSIVE_SESSIONS + 5
    boot = paired_bootstrap({f"wa_{i}": [0.0] for i in range(n)}, {f"wa_{i}": [1.0] for i in range(n)})

    assert boot["conclusive"] is True and boot["p"] < 0.05


def _check_reps(passes: dict[str, list[int]]) -> list[list[dict]]:
    """Un registro por conversación con varios checks: 1 = pasa, 0 = falla."""
    sessions = len(next(iter(passes.values())))
    records = []
    for i in range(sessions):
        rows = [{"check_id": cid, "verdict": "pasa" if v[i] else "falla", "turn": 1} for cid, v in passes.items()]
        records.append({"mode": "turn", "session_id": f"wa_{i}", "episode_id": "ep_001", "verdict": "PASA",
                        "by_turn": [{"turn": 1, "verdict": "PASA", "results": rows}], "results": rows})
    return [records]


def test_check_differences_are_corrected_for_multiple_comparisons() -> None:
    """Con 30 checks, uno que se mueve apenas por azar no es un hallazgo: el
    intervalo sin corregir no cruza el cero, pero tras Holm no es concluyente."""
    n = 40
    base = {f"CHK-{j:02d}": [1] * n for j in range(30)}
    cand = {f"CHK-{j:02d}": [1] * n for j in range(30)}
    cand["CHK-00"] = [0] * 5 + [1] * (n - 5)  # 5 de 40 empeoran: p ≈ 0,01 sin corregir

    diff = diff_entry("A1", "B", _check_reps(base), _check_reps(cand))

    [moved] = [c for c in diff["checks"] if c["check_id"] == "CHK-00"]
    assert moved["high"] < 0  # el intervalo sin corregir no cruza el cero…
    assert moved["conclusive"] is False  # …pero con 30 checks no alcanza


# ── C-M5: el límite de historia de Temporal ────────────────────────────────


@pytest.mark.asyncio
async def test_a_run_near_the_temporal_history_limit_stops_simulating_and_scores_what_it_has(box, sims) -> None:  # noqa: F811
    _order(box, arms=["A1", "B"], reps=1)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(env.client, task_queue=LAB_TASK_QUEUE, workflows=[LabRunWorkflow], activities=run_acts.LAB_RUN_ACTIVITIES):
            result = await env.client.execute_workflow(
                LabRunWorkflow.run, LabRunInput(run_id=RUN, history_limit=1), id=f"lab-run-{RUN}", task_queue=LAB_TASK_QUEUE
            )

    assert result["phase"] == "done"
    progress = _progress(box)
    assert HISTORY_NOTE in progress["notes"]
    summary = _summary(box)
    assert "A1" in summary["arms"] and summary["arms_pending"] == ["B"]


# ── C-L1: un reintento del resumen no pierde la referencia de producción ───


def test_a_retried_summary_keeps_the_production_reference() -> None:
    production = {"reps": 1, "episodes": 1, "verdicts": {"FALLA": 1}}
    scores = {"A0": [[_rec("wa_0", "PASA")]], "A1": [[_rec("wa_0", "PASA")]]}
    first = build_summary(run_id="run-1", registry_version=4, previous={"arms": {"A0": production}}, scores=scores,
                          metrics={}, rows={}, code_checks={"EST-06"})

    retried = build_summary(run_id="run-1", registry_version=4, previous=first, scores=scores,
                            metrics={}, rows={}, code_checks={"EST-06"})

    assert first["production"] == production
    assert retried["production"] == production  # no el A0 re-medido en modo turno


# ── Arena: lecturas que no engañan ─────────────────────────────────────────


def test_a_classifier_that_predicted_nothing_has_no_precision() -> None:
    """Sin predicciones no hay precisión que medir: antes daba 1,0 (perfecta)
    justo cuando el clasificador caía siempre a "turno como hoy"."""
    out = topic_arena([({"envio": (0.2, False)}, {"envio"})])

    assert out["precision"] is None and out["recall"] == 0.0 and out["f1"] == 0.0


def test_judge_topics_map_on_word_starts_not_inside_words() -> None:
    assert judge_topic_codes([{"topic": "enviar fotos del producto"}])[0] == {"foto"}
    assert "pagos" not in judge_topic_codes([{"topic": "la vela se apagó"}])[0]
    assert "personalizacion" not in judge_topic_codes([{"topic": "pedir el nombre del cliente"}])[0]
    assert judge_topic_codes([{"topic": "personalizar con un nombre"}])[0] == {"personalizacion"}
