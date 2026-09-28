"""La cantidad de una respuesta compuesta la decide el motor (fase F3) en la
activity que arma el prompt (`build_prompt` de ventas).

Incidente 2026-09-07 (run 943e6bff): «Una que colores tienes?» tras
«¿Cuántas unidades deseas?» — el LLM atendió la pregunta y soltó el dato. La
regla de hoy (`agent_asked_quantity` + `parse_leading_quantity`) es
conservadora a propósito: no ve «Quiero 2» y toma «Un regalo para mi mamá»
por una unidad. Con la capacidad `cantidad` en `jev`, Jev lee las dos cosas;
el código escribe la cantidad con las compuertas de siempre. Con `reglas` (así
nace) todo es igual que hoy.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales.activities.build_prompt_stage import sales_build_prompt
from src.plugins.chats.agent.sales.decisions import bots
from src.sdk.connectorkit import TypedAnswer
from tests.plugins.chats.sales.test_quantity_capture import (
    _AGENT_ASKED,
    _input,
    _make_workspace,
    _seed_history,
    _seed_metadata,
)

SLOTS = {"producto": "Velón Amor Eterno", "aroma": "Lavanda"}


def _noul(qid: str, p: float) -> TypedAnswer:
    return TypedAnswer(id=qid, kind="noul", p=p)


def _choice(qid: str, pick: str, p: float) -> TypedAnswer:
    return TypedAnswer(id=qid, kind="choice", choice=pick, probs=((pick, p),), confidence=p)


@pytest.fixture
def oracle(monkeypatch):
    from src.sdk import connectorkit

    holder = {"fake": FakePerceptionAdapter({})}

    def _get(_oracle: str):
        return holder["fake"]

    _get.cache_clear = lambda: None
    monkeypatch.setattr(connectorkit, "get_perception_port", _get)
    return holder


@pytest.fixture
def quantity_on(monkeypatch, _isolate_vault_dir: Path):
    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    monkeypatch.setenv("SALES_CAPABILITIES_CEILING", "on")
    bots.write_capability_modes(_isolate_vault_dir, {"cantidad": "on"})


def _slots(vault: Path, sid: str) -> dict:
    meta = json.loads((vault / sid / "metadata.json").read_text("utf-8"))
    return meta["episodes"][-1]["order_draft"]["slots"]


async def _turn(tmp_path: Path, vault: Path, sid: str, message: str, *, slots: dict = SLOTS,
                agent: str = _AGENT_ASKED) -> str:
    ws = _make_workspace(tmp_path)
    _seed_metadata(vault, sid, dict(slots))
    _seed_history(ws, sid, agent)
    messages = await sales_build_prompt(_input(ws, sid, message, None))
    return messages[0]["content"]


async def test_with_rules_the_capture_is_todays(tmp_path: Path, _isolate_vault_dir: Path, oracle) -> None:
    system = await _turn(tmp_path, _isolate_vault_dir, "wa_q_rules", "Quiero 2")

    assert "cantidad" not in _slots(_isolate_vault_dir, "wa_q_rules")  # el parser no la ve (hoy)
    assert "Cantidad:" not in system
    assert oracle["fake"].calls == []


async def test_jev_pins_a_quantity_the_parser_misses(tmp_path: Path, _isolate_vault_dir: Path, oracle, quantity_on) -> None:
    oracle["fake"] = FakePerceptionAdapter({
        "cantidad.pregunto": _noul("cantidad.pregunto", 0.97), "cantidad.dio": _choice("cantidad.dio", "2", 0.95),
    })

    system = await _turn(tmp_path, _isolate_vault_dir, "wa_q_jev", "Quiero 2")

    assert _slots(_isolate_vault_dir, "wa_q_jev").get("cantidad") == "2"
    assert "Cantidad: 2" in system
    [(state, _)] = oracle["fake"].calls
    assert "¿Cuántas unidades deseas?" in state and "Quiero 2" in state


async def test_jev_does_not_pin_an_article_as_a_quantity(tmp_path: Path, _isolate_vault_dir: Path, oracle, quantity_on) -> None:
    oracle["fake"] = FakePerceptionAdapter({
        "cantidad.pregunto": _noul("cantidad.pregunto", 0.96), "cantidad.dio": _choice("cantidad.dio", "ninguna", 0.9),
    })

    await _turn(tmp_path, _isolate_vault_dir, "wa_q_article", "Un regalo para mi mamá, ¿cuál me recomiendas?")

    assert "cantidad" not in _slots(_isolate_vault_dir, "wa_q_article")  # hoy quedaba «1»


async def test_when_jev_fails_the_capture_is_todays(tmp_path: Path, _isolate_vault_dir: Path, oracle, quantity_on) -> None:
    oracle["fake"] = FakePerceptionAdapter({}, error="timeout")

    await _turn(tmp_path, _isolate_vault_dir, "wa_q_fallback", "Una que colores tienes?")

    assert _slots(_isolate_vault_dir, "wa_q_fallback")["cantidad"] == "1"


async def test_jev_never_overwrites_a_quantity_already_pinned(tmp_path: Path, _isolate_vault_dir: Path, oracle, quantity_on) -> None:
    """Con cantidad en el ítem en curso no hay dónde escribir: ni se pregunta."""
    await _turn(tmp_path, _isolate_vault_dir, "wa_q_pinned", "Quiero 3", slots={**SLOTS, "cantidad": "2"})

    assert _slots(_isolate_vault_dir, "wa_q_pinned")["cantidad"] == "2"
    assert oracle["fake"].calls == []


async def test_a_common_turn_leaves_no_trace_in_the_vault(tmp_path: Path, _isolate_vault_dir: Path, oracle, quantity_on) -> None:
    ws = _make_workspace(tmp_path)

    await sales_build_prompt(_input(ws, "wa_q_fresh", "Hola, quiero ver velas", None))

    assert not (_isolate_vault_dir / "wa_q_fresh").exists()
    assert oracle["fake"].calls == []
