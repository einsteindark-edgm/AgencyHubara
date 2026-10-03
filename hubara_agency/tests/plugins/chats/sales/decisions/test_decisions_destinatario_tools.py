"""«Destinatario» en las tools y activities (diseño v2 §07; F5): «¿Qué es
este texto?» {mensaje al cliente, razonamiento, reporte interno, acuse al
sistema, deliberación} reemplaza al detector de fugas en `send_reply`, en el
flush de los intents y en el filtro de oraciones de las tools de cierre y de
escalación. Con `reglas` (así nace) cada sitio hace lo de hoy.

El falso positivo de hoy (memoria `coupon_tag_shape_collision`): «Usa el
código VELAS_10 al pagar» parece un token interno; `send_reply` lo rechaza,
el flush lo cambia por un texto neutro y el cierre lo borra. Con Jev pasa.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.sdk.connectorkit import TypedAnswer

SID = "wa_573001234567"
COUPON = "Usa el código VELAS_10 al pagar 🤍"


def _choice(qid: str, pick: str, p: float) -> TypedAnswer:
    return TypedAnswer(id=qid, kind="choice", choice=pick, probs=((pick, p),), confidence=p)


class _AllForTheCustomer(FakePerceptionAdapter):
    """Jev falso: todo lo que le preguntan «¿qué es?» es para el cliente."""

    async def ask(self, state, questions, *, timeout_s, redact=()):
        from src.sdk.connectorkit import PerceptionResult

        self.calls.append((state, tuple(q.id for q in questions)))
        answers = tuple(
            _choice(q.id, "mensaje_al_cliente", 0.96) if q.kind == "choice" else TypedAnswer(id=q.id, kind="noul", p=0.02)
            for q in questions
        )
        return PerceptionResult(ok=True, answers=answers, provider="fake", model="typesafe/jev-1.13-x")


@pytest.fixture
def jev(monkeypatch):
    from src.sdk import connectorkit

    fake = _AllForTheCustomer({})
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: fake)
    monkeypatch.setenv("DECISIONS_BOT", "B")
    return fake


@pytest.fixture
def today(monkeypatch):
    monkeypatch.delenv("DECISIONS_BOT", raising=False)


def _ctx() -> ToolContext:
    return ToolContext(session_key=SID, channel="whatsapp", chat_id=SID)


async def test_send_reply_today_rejects_the_coupon_code(tmp_path: Path, today) -> None:
    from src.plugins.chats.agent.sales.tools.reply import SendReplyTool

    out = json.loads(await SendReplyTool(workspace=str(tmp_path), vault_dir=tmp_path).execute_with_context(_ctx(), text=COUPON))

    assert out["sent"] is False and out["error"] == "internal_text"


async def test_with_jev_send_reply_lets_the_coupon_code_through(tmp_path: Path, jev) -> None:
    from src.plugins.chats.agent.sales.tools.reply import SendReplyTool

    out = json.loads(await SendReplyTool(workspace=str(tmp_path), vault_dir=tmp_path).execute_with_context(_ctx(), text=COUPON))

    assert out["reply"]["text"] == COUPON
    assert jev.calls


async def test_the_flush_today_neutralizes_the_coupon_intro(today) -> None:
    from src.plugins.chats.agent.sales.activities.flush_ui_intents import (
        _INTENT_TEXT_NEUTRAL,
        _sanitize_intent_client_text,
    )

    out = await _sanitize_intent_client_text("products", {"intro_text": COUPON}, session_id=SID)

    assert out["intro_text"] == _INTENT_TEXT_NEUTRAL["intro_text"]


async def test_with_jev_the_flush_keeps_the_coupon_intro(jev) -> None:
    from src.plugins.chats.agent.sales.activities.flush_ui_intents import _sanitize_intent_client_text

    out = await _sanitize_intent_client_text("products", {"intro_text": COUPON}, session_id=SID)

    assert out["intro_text"] == COUPON


async def test_the_sentence_filter_today_drops_the_coupon_sentence(tmp_path: Path, today) -> None:
    from src.plugins.chats.agent.sales.decisions.guards import safe_customer_text

    out = await safe_customer_text(f"¡Gracias por tu compra! {COUPON}", session_id=SID, vault_dir=tmp_path)

    assert out == "¡Gracias por tu compra!"


async def test_with_jev_the_sentence_filter_keeps_the_coupon_sentence(tmp_path: Path, jev) -> None:
    from src.plugins.chats.agent.sales.decisions.guards import safe_customer_text

    out = await safe_customer_text(f"¡Gracias por tu compra! {COUPON}", session_id=SID, vault_dir=tmp_path)

    assert out == f"¡Gracias por tu compra! {COUPON}"
