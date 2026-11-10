"""El texto final suelto pasa por las mismas revisiones que `send_reply` (2026-11-03).

`send_reply` retiene UNA vez por mensaje del cliente lo que no debe salir así:
la negación de un producto que la foto ya reconoció, «te confirmo después» (el
bot no puede volver a escribir), los hechos contra el catálogo y el pedido y
la lista de aromas que va en el selector. El texto final suelto (el modelo
contesta sin `send_reply`) se saltaba todo eso (testigo `texto_suelto_*` de
`sales/huecos.py`, plan del motor §6.6). Ahora el egreso del V2 le pasa el
texto suelto que va a salir a las MISMAS revisiones de `send_reply`
(`SendReplyTool.held_reason`) y, si lo retienen, su mensaje es la nota con que
`run_agent_turn` le da UNA ronda más al modelo.

El texto de `send_reply` ya pasó por ellas (no se revisa dos veces), y la
marca de la retención es la misma: una por mensaje del cliente, también entre
los dos caminos. Datos sintéticos.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.plugins.chats.agent.sales.decisions import egress_activities, registry
from src.plugins.chats.agent.sales.decisions.contracts import EgressInput, EgressOutput
from src.plugins.chats.agent.sales.state import FilesystemMetadataStore
from src.plugins.chats.agent.sales.tools.reply import SendReplyTool
from src.plugins.chats.agent.sales.use_cases.photo_product import PROMISE_MESSAGE

SID = "wa_573000000002"
LATER = "Déjame revisar las dos y te confirmo en un momento 🤍"
PLAIN = "¿Qué aroma te gustaría? 🤍"


@pytest.fixture
def vault(tmp_path: Path, monkeypatch) -> Path:
    from src.platform.perception.adapters.fake import FakePerceptionAdapter
    from src.sdk import connectorkit

    monkeypatch.setattr(egress_activities, "_vault_dir", lambda: tmp_path)
    monkeypatch.setattr(
        egress_activities, "_reply_checker", lambda: SendReplyTool(workspace=str(tmp_path), vault_dir=tmp_path)
    )
    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    monkeypatch.delenv("SALES_DECISIONS_BUNDLE", raising=False)
    fake = FakePerceptionAdapter({})

    def _get(_oracle: str):
        return fake

    _get.cache_clear = lambda: None
    monkeypatch.setattr(connectorkit, "get_perception_port", _get)
    registry.reset()
    FilesystemMetadataStore(tmp_path).write(SID, {"last_inbound_message_id": "wamid.sintetico.1"})
    yield tmp_path
    registry.reset()


async def _egress(text: str, *, loose: bool = True, **kw) -> EgressOutput:
    from temporalio.testing import ActivityEnvironment

    inp = EgressInput(session_id=SID, final_text=text, raw_text=text if loose else None, **kw)
    return await ActivityEnvironment().run(egress_activities.decide_egress_activity, inp)


async def test_a_loose_text_that_promises_to_answer_later_is_retained(vault: Path) -> None:
    out = await _egress(LATER)

    assert out.retain_note == PROMISE_MESSAGE
    assert out.text == LATER  # sale tal cual si el turno no tiene otra vuelta


async def test_the_send_reply_text_is_not_checked_twice(vault: Path) -> None:
    """El caso legítimo: el texto de `send_reply` ya pasó por sus revisiones."""
    out = await _egress(LATER, loose=False)

    assert out.retain_note == ""


async def test_the_second_attempt_in_the_same_customer_message_goes_out(vault: Path) -> None:
    """Una retención por mensaje del cliente (la misma marca de `send_reply`):
    si el modelo insiste, el texto sale."""
    first = await _egress(LATER)
    second = await _egress(LATER)

    assert first.retain_note == PROMISE_MESSAGE
    assert second.retain_note == ""


async def test_a_plain_loose_text_goes_out(vault: Path) -> None:
    out = await _egress(PLAIN)

    assert out.retain_note == "" and out.text == PLAIN


async def test_an_admin_turn_is_never_checked(vault: Path) -> None:
    out = await _egress(LATER, admin_turn=True)

    assert out.retain_note == ""
