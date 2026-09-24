"""El gasto de una corrida nunca se subcuenta (premortem de la caja, PR 13).

`progress.json` es lo que suma el tope del MES en producción
(`launch/costs.month_spent_usd`): una corrida que falla a mitad no puede
dejar `spent_usd: 0` (el mes se olvidaba de lo gastado y dejaba lanzar otra),
y un caso que murió sin reportar su costo (timeout, proceso matado) igual
gastó LLM: se le carga la tarifa medida por turno.
"""
from __future__ import annotations

import json

import pytest

from src.plugins.chats.agent.sales_lab.launch.costs import AGENT_USD_PER_TURN
from tests.plugins.chats.lab.test_lab_run_arms import _fake_result, sims  # noqa: F401  (fixture)
from tests.plugins.chats.lab.test_lab_run_box import RUN, _run, box  # noqa: F401  (fixture)


def _order(box, **over) -> None:  # noqa: F811
    order = json.loads(box["store"].get_bytes(f"orders/{RUN}.json"))
    box["store"].put_bytes(f"orders/{RUN}.json", json.dumps({**order, **over}).encode())


@pytest.mark.asyncio
async def test_a_run_that_fails_midway_keeps_what_it_spent(box, sims, monkeypatch) -> None:  # noqa: F811
    from src.plugins.chats.agent.sales_lab.run import activities as run_acts

    _order(box, arms=["A1"], reps=2)
    seen: list[int] = []

    async def breaks_on_the_second_rep(case, *, bench_dir, sandbox_dir, timeout_s):
        seen.append(1)
        if sandbox_dir.parts[-3:-1] == ("A1", "1"):
            raise RuntimeError("el disco de la caja se llenó")
        return _fake_result(case, cost=0.25)

    monkeypatch.setattr(run_acts, "run_case_in_subprocess", breaks_on_the_second_rep)

    result = await _run(box)

    assert result["phase"] == "failed"
    progress = json.loads(box["store"].get_bytes(f"runs/{RUN}/progress.json"))
    assert progress["phase"] == "failed"
    assert progress["spent_usd"] >= 0.5  # los dos casos de la repetición 0 (más el humo)


@pytest.mark.asyncio
async def test_a_case_that_never_reported_its_cost_is_charged_the_measured_rate(box, sims, monkeypatch) -> None:  # noqa: F811
    from src.plugins.chats.agent.sales_lab.run import activities as run_acts

    _order(box, arms=["A1"], reps=1)

    async def killed(case, *, bench_dir, sandbox_dir, timeout_s):
        if sandbox_dir.parts[-3] == "A1":
            return _fake_result(case, cost=0.0, error="el turno no terminó en 600 s")
        return _fake_result(case, cost=0.0)

    monkeypatch.setattr(run_acts, "run_case_in_subprocess", killed)

    await _run(box)

    progress = json.loads(box["store"].get_bytes(f"runs/{RUN}/progress.json"))
    assert progress["spent_usd"] >= round(2 * AGENT_USD_PER_TURN, 6) - 1e-6  # progress guarda 6 decimales


@pytest.mark.asyncio
async def test_a_finished_case_without_llm_cost_is_charged_the_measured_rate(box, sims) -> None:  # noqa: F811
    """Sin la tabla de precios (o con un modelo que no está en ella) el turno
    termina bien pero reporta US$0 de LLM: igual gastó, y el tope no puede
    quedar ciego."""
    _order(box, arms=["A1"], reps=1)
    sims["cost"] = 0.0

    await _run(box)

    progress = json.loads(box["store"].get_bytes(f"runs/{RUN}/progress.json"))
    assert progress["spent_usd"] == pytest.approx(3 * AGENT_USD_PER_TURN, abs=1e-5)  # el humo y los 2 casos


def test_the_workflow_charges_the_same_rate_the_launcher_estimates() -> None:
    """El workflow no importa el módulo de costos (sandbox de Temporal): la
    tarifa se repite y este guarda las mantiene iguales."""
    from src.plugins.chats.agent.sales_lab.run.workflow import UNREPORTED_CASE_USD

    assert UNREPORTED_CASE_USD == AGENT_USD_PER_TURN
