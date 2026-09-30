"""Flush ACOTADO + envío del operador (app móvil).

`flush_pending_ui_intents` sin argumentos manda TODA la cola (el turno del
bot). Una acción del operador desde la app encola sus intents y manda SOLO
esos (`only_ids`): nunca otros de la cola — p. ej. las instrucciones de pago
que "Crear pedido" dejó encoladas sin enviar a propósito, o un remanente del
bot. Lo que sale queda en el historial marcado como enviado por el humano
(`sender: "human"`, como `append_human_event`), con la acción (`operator_tool`).
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.plugins.chats.agent.sales.activities import flush_ui_intents
from src.plugins.chats.agent.sales.config.shipping import SHIPPING_RATES_MESSAGE

_SID = "wa_test_flush_scoped"


def _now() -> int:
    return int(time.time() * 1000)


_PAYINSTR = {"id": "payinstr-ord_1", "kind": "payment_instructions", "params": {"method": "transfer"}}
_RATES = {"id": "ui_op_rates", "kind": "shipping_rates", "params": {}}
_BUTTONS = {"id": "ui_op_buttons", "kind": "quick_replies",
            "params": {"body": "¿Seguimos?", "buttons": [{"id": "b.si", "title": "Sí"}, {"id": "b.no", "title": "No"}]}}
_BOT_LIST = {"id": "ui_bot_list", "kind": "products_list", "params": {"intro_text": "x", "sections": []}}


@pytest.fixture
def vault(tmp_path, monkeypatch):
    from src.platform import config
    from src.platform.whatsapp import client as wa_client

    monkeypatch.setattr(config, "WORKSPACE_VAULT_DIR", tmp_path)
    monkeypatch.setenv("PAYMENT_NEQUI_NUMBER", "3000000000")
    for fn in ("send_text", "send_interactive_buttons", "send_interactive_list", "send_product_list"):
        monkeypatch.setattr(
            wa_client, fn, AsyncMock(return_value=SimpleNamespace(ok=True, wa_message_id=f"wamid.{fn}", error=None))
        )
    return tmp_path


def _seed(vault, intents: list[dict]) -> None:
    (vault / _SID).mkdir(parents=True, exist_ok=True)
    (vault / _SID / "metadata.json").write_text(
        json.dumps({"phone_number_id": "pnid-1", "pending_ui_intents": intents}), encoding="utf-8"
    )


def _meta(vault) -> dict:
    return json.loads((vault / _SID / "metadata.json").read_text(encoding="utf-8"))


def _history(vault) -> list[dict]:
    path = vault / _SID / "sessions" / f"{_SID}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.asyncio
async def test_scoped_flush_sends_only_its_intents_and_leaves_the_queue_untouched(vault) -> None:
    from src.platform.whatsapp import client as wa_client

    stale_bot = {**_BOT_LIST, "queued_at_ms": 1}  # remanente viejo: tampoco se toca
    queue = [{**_PAYINSTR, "queued_at_ms": _now()}, {**_RATES, "queued_at_ms": _now()}, stale_bot]
    _seed(vault, queue)

    sent = await flush_ui_intents.flush_pending_ui_intents(_SID, only_ids={"ui_op_rates"})

    assert sent == 1
    wa_client.send_text.assert_awaited_once()
    assert wa_client.send_text.await_args.args[2] == SHIPPING_RATES_MESSAGE
    assert _meta(vault)["pending_ui_intents"] == [queue[0], stale_bot]


@pytest.mark.asyncio
async def test_operator_flush_leaves_the_history_marked_as_sent_by_the_human(vault) -> None:
    _seed(vault, [{**_RATES, "queued_at_ms": _now()}, {**_BUTTONS, "queued_at_ms": _now()}])

    sent = await flush_ui_intents.flush_pending_ui_intents(
        _SID, only_ids={"ui_op_rates", "ui_op_buttons"}, operator_tool="send_shipping_rates"
    )

    assert sent == 2
    rates, buttons = _history(vault)
    # texto real que recibió el cliente → burbuja del humano, con su acción
    assert rates["role"] == "assistant" and rates["sender"] == "human"
    assert rates["content"] == SHIPPING_RATES_MESSAGE and rates["operator_tool"] == "send_shipping_rates"
    # envío no textual → nota del componente, firmada por el operador (no "el bot")
    assert (buttons["kind"], buttons["component_kind"], buttons["sender"]) == ("ui_component", "quick_replies", "human")
    assert buttons["content"].startswith("🔘 El operador envió botones: Sí · No")
    assert buttons["wamid"] == "wamid.send_interactive_buttons"
    assert _meta(vault)["pending_ui_intents"] == []


@pytest.mark.asyncio
async def test_the_bot_flush_is_unchanged_it_sends_the_whole_queue_as_the_bot(vault) -> None:
    _seed(vault, [{**_RATES, "queued_at_ms": _now()}, {**_BUTTONS, "queued_at_ms": _now()}])

    assert await flush_ui_intents.flush_pending_ui_intents(_SID) == 2

    rates, buttons = _history(vault)
    assert "sender" not in rates and "operator_tool" not in rates
    assert buttons["content"].startswith("🔘 El bot envió botones")
