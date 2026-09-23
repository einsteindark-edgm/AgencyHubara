"""`send_reply` — el canal del texto del asesor al cliente (run 28a8e407).

Con thinking apagado el modelo razona en su texto libre ("El cliente pregunta
si… Le aclaro y le pregunto…") y ese mismo texto era el mensaje al cliente:
cada paráfrasis que el detector no conocía llegaba al WhatsApp. Ahora el
cliente lee SOLO lo que el modelo pasa en `send_reply(text)`; el texto libre
es borrador y nunca sale.

La tool no envía nada: valida el texto y lo devuelve en `reply.text`, que es
lo que el workflow manda (y lo que queda en el historial). Si el texto es
vacío o solo nota interna, NO hay `reply` y el mensaje le dice al modelo que
lo reescriba — el turno sigue.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.plugins.chats.agent.sales.tools.reply import SendReplyTool

KEY = "wa_test_reply"


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(session_key=KEY, channel="whatsapp", chat_id=KEY)


async def _send(tmp_path: Path, ctx: ToolContext, text: str) -> dict:
    tool = SendReplyTool(workspace=str(tmp_path))
    return json.loads(await tool.execute_with_context(ctx, text=text))


@pytest.mark.asyncio
async def test_clean_text_goes_as_is(tmp_path: Path, ctx: ToolContext) -> None:
    env = await _send(tmp_path, ctx, "¿Qué aroma te gustaría? 🤍")

    assert env["reply"] == {"text": "¿Qué aroma te gustaría? 🤍"}
    assert "espera" in env["summary"].lower()


@pytest.mark.asyncio
async def test_reasoning_paragraph_is_dropped_and_the_answer_kept(
    tmp_path: Path, ctx: ToolContext
) -> None:
    env = await _send(
        tmp_path,
        ctx,
        "El cliente pregunta si todos los productos tienen cupón. Le aclaro.\n\n"
        "No, el cupón aplica solo a las piezas de Amor y Amistad 🤍",
    )

    assert env["reply"] == {
        "text": "No, el cupón aplica solo a las piezas de Amor y Amistad 🤍"
    }


@pytest.mark.asyncio
async def test_only_internal_text_is_not_sent_and_the_model_rewrites(
    tmp_path: Path, ctx: ToolContext
) -> None:
    env = await _send(
        tmp_path, ctx, "El cliente pregunta por el cupón. Le respondo que aplica."
    )

    assert "reply" not in env
    assert env["sent"] is False
    assert "send_reply" in env["message"]


@pytest.mark.asyncio
async def test_empty_text_is_rejected(tmp_path: Path, ctx: ToolContext) -> None:
    env = await _send(tmp_path, ctx, "   ")

    assert "reply" not in env
    assert env["sent"] is False


def test_the_tool_asks_only_for_the_customer_text() -> None:
    tool = SendReplyTool(workspace=".")

    assert tool.name == "send_reply"
    assert tool.parameters["required"] == ["text"]
    assert "borrador" in tool.description


def test_sales_worker_registers_send_reply(tmp_path: Path, monkeypatch) -> None:
    """Gotcha #6: el lambda carga limpio aunque falte el import; ejecutarlo
    caza el NameError antes de producción."""
    monkeypatch.setenv("MEDUSA_BASE_URL", "http://medusa.test")
    monkeypatch.setenv("MEDUSA_ADMIN_TOKEN", "dummy")
    import src.plugins.chats.workers.sales  # noqa: F401  (registra las tools)
    from src.platform.tool_extensions import _EXTENSIONS  # type: ignore

    factory = dict(_EXTENSIONS).get("sales.send_reply")
    assert factory is not None, "sales.send_reply no está registrada"
    assert factory(tmp_path).name == "send_reply"
