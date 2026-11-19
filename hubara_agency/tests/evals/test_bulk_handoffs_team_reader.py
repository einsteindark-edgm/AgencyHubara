"""El lector del equipo de los relevos al por mayor (tarjeta 34 del ciclo).

Jev lee lo que escribió el equipo después del relevo y dice cuál de los cinco
datos le pidió al cliente. Si no contesta alguna pregunta con certeza, no se
adivina: None. Jev es el falso; textos sintéticos.
"""
from __future__ import annotations

import pytest

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales_eval.bulk_handoffs import BULK_DATA, jev_team_reader
from src.sdk.connectorkit import TypedAnswer

TEAM = "Hola, ¿para qué fecha las necesitas y a qué ciudad te las enviamos?"


@pytest.fixture
def jev(monkeypatch):
    from src.sdk import connectorkit

    holder: dict = {}

    def _get(_oracle: str):
        return holder["fake"]

    _get.cache_clear = lambda: None
    monkeypatch.setattr(connectorkit, "get_perception_port", _get)

    def says(**given: float) -> FakePerceptionAdapter:
        holder["fake"] = FakePerceptionAdapter({
            f"equipo_por_mayor.{d}": TypedAnswer(id=f"equipo_por_mayor.{d}", kind="noul", p=p)
            for d, p in given.items()
        })
        return holder["fake"]

    return says


async def test_it_returns_what_the_team_asked(jev) -> None:
    fake = jev(producto=0.05, cantidad=0.1, fecha=0.95, ciudad=0.9, empresa=0.05)

    assert await jev_team_reader(TEAM) == frozenset({"fecha", "ciudad"})
    [(state, ids)] = fake.calls
    assert TEAM in state
    assert list(ids) == [f"equipo_por_mayor.{d}" for d in BULK_DATA]


async def test_the_team_that_asked_nothing_is_an_empty_reading(jev) -> None:
    jev(**dict.fromkeys(BULK_DATA, 0.05))

    assert await jev_team_reader("Listo, ya te paso el precio") == frozenset()


async def test_an_unsure_answer_is_not_guessed(jev) -> None:
    jev(producto=0.05, cantidad=0.5, fecha=0.95, ciudad=0.9, empresa=0.05)

    assert await jev_team_reader(TEAM) is None


async def test_a_failing_jev_is_no_reading(jev, monkeypatch) -> None:
    from src.sdk import connectorkit

    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _o: FakePerceptionAdapter(error="timeout"))

    assert await jev_team_reader(TEAM) is None
