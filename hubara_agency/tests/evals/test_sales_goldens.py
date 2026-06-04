"""Regresión de calidad con DeepEval real (Superficie 1: unit eval con juez).

Filosofía (calibrada con el juez real, ver `LLM_EVAL_HARNESS_PLAN.md`):

  * Las métricas DETERMINISTAS (saludo/estilo) son el GATE de regresión: estrictas,
    estables, no flaky → `assert_test` las exige pasar.
  * Las métricas LLM-JUEZ son ruidosas con un juez chico (gemini-flash-lite puntúa
    conversaciones ejemplares en 0.2–0.6 y varía entre corridas). NO se gatea CI con
    eso: acá solo verificamos SALUD DEL PIPELINE (cada métrica produce un score en
    [0,1] sin excepción). El bar de calidad real son las TENDENCIAS online en SigNoz
    (Superficie 2), no un umbral en un unit test. Subí el rigor cuando uses un juez
    más fuerte (`EVAL_JUDGE_MODEL`).

GATING: `importorskip("deepeval")` (gate de arquitectura saltea) + `RUN_EVAL_TESTS`
(necesita proxy litellm + juez vivos):  RUN_EVAL_TESTS=1 uv run --extra evals pytest -m eval
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("deepeval", reason="requiere el extra `evals` (deepeval)")

if not os.getenv("RUN_EVAL_TESTS"):
    pytest.skip(
        "tests de eval con juez LLM desactivados — exportá RUN_EVAL_TESTS=1 "
        "(y un proxy litellm + EVAL_JUDGE_MODEL alcanzables) para correrlos",
        allow_module_level=True,
    )

from deepeval import assert_test  # noqa: E402

from src.plugins.chats.agent.sales_eval.evals import metrics as M  # noqa: E402
from src.plugins.chats.agent.sales_eval.evals import reconstruct  # noqa: E402
from src.plugins.chats.agent.sales_eval.evals.composition import get_judge  # noqa: E402

_GOLDENS = Path(__file__).parent / "goldens" / "sales" / "curated.json"


def _load_goldens() -> list[dict]:
    return json.loads(_GOLDENS.read_text(encoding="utf-8"))


def _tc(golden: dict):
    turns = [{"role": t["role"], "content": t["content"], "tools": []} for t in golden["turns"]]
    return reconstruct.build_conversational_test_case(turns, name=golden["name"])


@pytest.fixture(scope="module")
def judge():
    return get_judge()


@pytest.mark.eval
@pytest.mark.parametrize("golden", _load_goldens(), ids=lambda g: g["name"])
def test_seed_golden_deterministic_strict(golden: dict):
    """GATE de regresión: los goldens ejemplares pasan saludo + estilo (deterministas)."""
    assert_test(test_case=_tc(golden), metrics=M.deterministic_metrics())


@pytest.mark.eval
@pytest.mark.parametrize("golden", _load_goldens(), ids=lambda g: g["name"])
async def test_seed_golden_judge_pipeline(golden: dict, judge):
    """Salud del pipeline: cada métrica-juez produce un score válido en [0,1]."""
    tc = _tc(golden)
    judge_metrics = [
        M.script_adherence_metric(judge),
        M.proactive_offering_metric(judge),
        M.correct_handoff_metric(judge),
        M.role_adherence_metric(judge),
    ]
    for m in judge_metrics:
        await m.a_measure(tc)
        key = M.metric_key(m)
        assert m.score is not None, f"{golden['name']}/{key}: sin score"
        assert 0.0 <= float(m.score) <= 1.0, f"{golden['name']}/{key}: score fuera de [0,1]"
        assert isinstance(m.reason, str) and m.reason, f"{golden['name']}/{key}: sin reason"


@pytest.mark.eval
async def test_judge_is_reachable(judge):
    """Smoke: el juez responde (valida proxy litellm + alias EVAL_JUDGE_MODEL)."""
    res = await judge.a_generate("Responde solo con la palabra: OK")
    out = res[0] if isinstance(res, tuple) else res
    assert isinstance(out, str) and out.strip()
