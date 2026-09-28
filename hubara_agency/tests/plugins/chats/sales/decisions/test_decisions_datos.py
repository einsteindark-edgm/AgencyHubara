"""Revisión de cada dato de `set_order_slot` (diseño v2 §04, fase F6).

El LLM a veces guarda un dato de envío que el cliente nunca dio (lo supone,
lo trae de un pedido anterior). Antes de escribirlo, el motor le pregunta a
Jev si el cliente lo dio en la conversación (con sus palabras o
confirmándolo). Solo BLOQUEA con certeza alta (p ≤ 0,15); si Jev duda, cae o
tarda, el dato se guarda como hoy. Hoy no hay ninguna revisión: con
`reglas` todo pasa.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.plugins.chats.agent.sales.decisions.capabilities.datos import DATOS, DatosDelPedido
from src.sdk.connectorkit import PerceptionResult, TypedAnswer

EVENTS = (
    {"role": "assistant", "content": "¿A qué ciudad y dirección te lo enviamos?"},
    {"role": "user", "content": "A Medellín, calle 10 # 43-12"},
)


def _noul(qid: str, p: float) -> TypedAnswer:
    return TypedAnswer(id=qid, kind="noul", p=p)


def _result(*answers: TypedAnswer) -> PerceptionResult:
    return PerceptionResult(ok=True, answers=answers, provider="fake", model="typesafe/jev-1.13-20260917")


def _inp(**values: str) -> DatosDelPedido:
    return DatosDelPedido(values=tuple(values.items()), events=EVENTS)


def test_today_every_value_passes() -> None:
    assert DATOS.rule(_inp(ciudad="Medellín", telefono="3001234567")) == ()


def test_one_question_per_value_with_the_conversation_in_view() -> None:
    state, questions = DATOS.ask(_inp(ciudad="Medellín", telefono="3001234567"))

    assert [q.id for q in questions] == ["datos.ciudad", "datos.telefono"]
    assert "[cliente] A Medellín, calle 10 # 43-12" in state
    assert DATOS.ask(DatosDelPedido(values=(), events=EVENTS)) is None


def test_only_a_confident_no_blocks_a_value() -> None:
    inp = _inp(ciudad="Medellín", telefono="3001234567")

    assert DATOS.decide(inp, _result(_noul("datos.ciudad", 0.97), _noul("datos.telefono", 0.04)), (), {}) == ("telefono",)
    assert DATOS.decide(inp, _result(_noul("datos.ciudad", 0.97), _noul("datos.telefono", 0.4)), (), {}) == ()
    assert DATOS.decide(inp, _result(), (), {}) is None


# --- la tool -----------------------------------------------------------------

SID = "wa_573001234567"


def _tool(vault: Path) -> "object":
    from src.plugins.chats.agent.sales.tools.order_draft import SetOrderSlotTool

    history = [dict(e) for e in EVENTS]
    return SetOrderSlotTool(workspace=str(vault), vault_dir=vault, history_reader=lambda _sid: history)


def _seed(vault: Path) -> None:
    session = vault / SID
    session.mkdir(parents=True, exist_ok=True)
    episode = {"episode_id": "ep_1", "started_at_ms": 1, "closed_at_ms": None}
    (session / "metadata.json").write_text(json.dumps({"episodes": [episode]}), encoding="utf-8")


@pytest.fixture
def jev_says_the_phone_was_never_given(monkeypatch):
    from src.platform.perception.adapters.fake import FakePerceptionAdapter
    from src.sdk import connectorkit

    fake = FakePerceptionAdapter({"datos.ciudad": _noul("datos.ciudad", 0.97), "datos.telefono": _noul("datos.telefono", 0.03)})
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: fake)
    return fake


async def test_with_jev_an_invented_value_is_not_saved_and_the_llm_is_told(tmp_path: Path, monkeypatch,
                                                                            jev_says_the_phone_was_never_given) -> None:
    monkeypatch.setenv("DECISIONS_BOT", "B")
    _seed(tmp_path)
    ctx = ToolContext(session_key=SID, channel="whatsapp", chat_id=SID)

    out = json.loads(await _tool(tmp_path).execute_with_context(ctx, ciudad="Medellín", telefono="3001234567"))

    assert out["captured"] == {"ciudad": "Medellín"}
    assert [r["field"] for r in out["rejected"]] == ["telefono"]
    assert "pídeselo" in out["summary"]


async def test_by_default_every_value_is_saved_like_today(tmp_path: Path, monkeypatch,
                                                          jev_says_the_phone_was_never_given) -> None:
    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    _seed(tmp_path)
    ctx = ToolContext(session_key=SID, channel="whatsapp", chat_id=SID)

    out = json.loads(await _tool(tmp_path).execute_with_context(ctx, ciudad="Medellín", telefono="3001234567"))

    assert out["captured"] == {"ciudad": "Medellín", "telefono": "3001234567"}
    assert "rejected" not in out
