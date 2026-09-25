"""El turno de humo prueba cada bot nuevo con su clasificador (premortem, PR 15).

La percepción falla abierto (el turno sale como hoy): con la llave de
OpenRouter del laboratorio en placeholder o la API cambiada, B y C
responderían IGUAL que A1 y la corrida gastaría dos tercios de su tope para
decir "aún no concluyente". El humo corre el primer caso también con B y C y
corta la corrida si su clasificador cayó.
"""
from __future__ import annotations

import json

import pytest

from tests.plugins.chats.lab.test_lab_run_arms import sims  # noqa: F401  (fixture)
from tests.plugins.chats.lab.test_lab_run_box import RUN, _run, box  # noqa: F401  (fixture)


def _classifier(fallback):
    def decorate(result: dict, arm: str) -> dict:
        if arm == "A1" or not result.get("trace"):
            return result
        return {**result, "trace": {**result["trace"], "steps": [{"kind": "perception", "fallback": fallback}]}}

    return decorate


@pytest.mark.asyncio
async def test_a_new_bot_whose_classifier_falls_back_stops_the_run_before_simulating(box, sims) -> None:  # noqa: F811
    sims["decorate"] = _classifier("no_api_key")

    result = await _run(box)

    assert result["phase"] == "failed"
    progress = json.loads(box["store"].get_bytes(f"runs/{RUN}/progress.json"))
    assert "B" in progress["error"] and "clasificador" in progress["error"] and "no_api_key" in progress["error"]
    assert [c for c in sims["calls"] if c[1][0] != "smoke"] == [], "no se simula nada"


@pytest.mark.asyncio
async def test_a_healthy_classifier_lets_the_run_go_on(box, sims) -> None:  # noqa: F811
    sims["decorate"] = _classifier(None)

    result = await _run(box)

    assert result["phase"] == "done"
    assert {c[2] for c in sims["calls"] if c[1][0] == "smoke"} == {"A1", "B"}
