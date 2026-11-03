"""Plantilla con botón FLOW — la invitación a calificar el pedido.

Cuando la ventana de servicio está cerrada, la única forma de abrirle al
cliente el formulario de calificación (un WhatsApp Flow) es una plantilla con
un botón de tipo FLOW. Meta fija el Flow al aprobar la plantilla; al enviarla,
el botón lleva el `flow_token` que identifica el pedido calificado.

Contrato:
  * El catálogo declara `buttons: [{type: flow, text, navigate_screen}]`.
  * El builder emite el componente `button` (sub_type flow, índice 0) con el
    `flow_token`, y se niega a construir el payload si falta el token o si se
    pasa un token a una plantilla sin botón FLOW.
"""
from __future__ import annotations

from typing import Any

import pytest

from src.platform.whatsapp.templates.registry import _build_registry_from_dict


def _entry(**overrides: Any) -> dict[str, Any]:
    entry = {
        "name": "review_v1",
        "category": "marketing",
        "language": "es_CO",
        "waba_template_name": "review_v1",
        "semantics": "...",
        "triggers_when_window_expiring": False,
        "body": "Tu pedido {{1}} ya llegó.",
        "variables": [{"name": "order_reference", "type": "string", "max_length": 20}],
        "buttons": [
            {"type": "flow", "text": "Calificar mi pedido", "navigate_screen": "RATING"}
        ],
    }
    entry.update(overrides)
    return entry


# ---------- catálogo ----------


def test_catalog_parses_flow_button():
    spec = _build_registry_from_dict({"templates": [_entry()]})["review_v1"]
    assert len(spec.buttons) == 1
    button = spec.buttons[0]
    assert button.type == "flow"
    assert button.text == "Calificar mi pedido"
    assert button.navigate_screen == "RATING"


def test_template_without_buttons_has_none():
    entry = _entry()
    entry.pop("buttons")
    spec = _build_registry_from_dict({"templates": [entry]})["review_v1"]
    assert spec.buttons == ()


@pytest.mark.parametrize(
    ("button", "match"),
    [
        ({"type": "phone", "text": "Llamar"}, "type"),
        ({"type": "flow", "text": "x" * 26, "navigate_screen": "RATING"}, "25"),
        ({"type": "flow", "text": "Calificar"}, "navigate_screen"),
    ],
)
def test_catalog_rejects_invalid_buttons(button, match):
    with pytest.raises(ValueError, match=match):
        _build_registry_from_dict({"templates": [_entry(buttons=[button])]})


# ---------- builder ----------


def _spec():
    return _build_registry_from_dict({"templates": [_entry()]})["review_v1"]


def test_builder_emits_flow_button_with_token():
    from src.platform.whatsapp.outbound import build_template_message

    payload = build_template_message(
        "+573000000000",
        _spec(),
        {"order_reference": "#31"},
        flow_token="review_ord_1_abc",
    )
    components = payload["template"]["components"]
    assert components[-1] == {
        "type": "button",
        "sub_type": "flow",
        "index": "0",
        "parameters": [{"type": "action", "action": {"flow_token": "review_ord_1_abc"}}],
    }


def test_builder_passes_flow_action_data_to_first_screen():
    from src.platform.whatsapp.outbound import build_template_message

    payload = build_template_message(
        "+573000000000",
        _spec(),
        {"order_reference": "#31"},
        flow_token="review_ord_1_abc",
        flow_action_data={"order_label": "Pedido #31"},
    )
    action = payload["template"]["components"][-1]["parameters"][0]["action"]
    assert action == {
        "flow_token": "review_ord_1_abc",
        "flow_action_data": {"order_label": "Pedido #31"},
    }


def test_builder_refuses_flow_template_without_token():
    from src.platform.whatsapp.outbound import build_template_message

    with pytest.raises(ValueError, match="flow_token"):
        build_template_message("+573000000000", _spec(), {"order_reference": "#31"})


def test_builder_refuses_token_on_template_without_flow_button():
    from src.platform.whatsapp.outbound import build_template_message

    entry = _entry()
    entry.pop("buttons")
    spec = _build_registry_from_dict({"templates": [entry]})["review_v1"]
    with pytest.raises(ValueError, match="FLOW"):
        build_template_message(
            "+573000000000", spec, {"order_reference": "#31"}, flow_token="t"
        )


# ---------- envío ----------


@pytest.fixture
def isolated_vault(tmp_path, monkeypatch):
    import json

    monkeypatch.setattr("src.platform.whatsapp.activities.WORKSPACE_VAULT_DIR", tmp_path)
    monkeypatch.setattr(
        "src.platform.whatsapp.activities.get_template_registry",
        lambda: {"review_v1": _spec()},
    )
    folder = tmp_path / "wa_573001234567"
    folder.mkdir(parents=True)
    (folder / "metadata.json").write_text(
        json.dumps({"phone_number_id": "PHONE_TEST"}), encoding="utf-8"
    )
    return tmp_path


@pytest.mark.asyncio
async def test_send_passes_flow_token_to_meta(isolated_vault, monkeypatch):
    from unittest.mock import AsyncMock

    from src.platform.whatsapp.activities import send_template_to_session
    from src.platform.whatsapp.dtos import OutboundResult

    mock_send = AsyncMock(return_value=OutboundResult(wa_message_id="wamid.F", ok=True))
    monkeypatch.setattr(
        "src.platform.whatsapp.activities.whatsapp_client.send_template", mock_send
    )

    result = await send_template_to_session(
        "wa_573001234567",
        "review_v1",
        {"order_reference": "#31"},
        flow_token="review_ord_1_abc",
        flow_action_data={"order_label": "Pedido #31"},
    )

    assert result.ok
    assert mock_send.call_args.kwargs["flow_token"] == "review_ord_1_abc"
    assert mock_send.call_args.kwargs["flow_action_data"] == {"order_label": "Pedido #31"}


@pytest.mark.asyncio
async def test_two_orders_with_the_same_reference_are_not_deduped(isolated_vault, monkeypatch):
    """El token distingue el pedido: misma referencia visible con otro token
    (otro pedido) = otro envío, no un reintento."""
    from unittest.mock import AsyncMock

    from src.platform.whatsapp.activities import send_template_to_session
    from src.platform.whatsapp.dtos import OutboundResult

    mock_send = AsyncMock(
        side_effect=[
            OutboundResult(wa_message_id="wamid.1", ok=True),
            OutboundResult(wa_message_id="wamid.2", ok=True),
        ]
    )
    monkeypatch.setattr(
        "src.platform.whatsapp.activities.whatsapp_client.send_template", mock_send
    )
    for token in ("review_a", "review_b"):
        await send_template_to_session(
            "wa_573001234567", "review_v1", {"order_reference": "#31"}, flow_token=token
        )
    assert mock_send.await_count == 2


def test_client_forwards_flow_token_to_builder(monkeypatch):
    import asyncio

    from src.platform.whatsapp import client

    captured: dict = {}

    async def fake_post(phone_number_id, data, label):
        captured["data"] = data
        from src.platform.whatsapp.dtos import OutboundResult

        return OutboundResult(wa_message_id="wamid.X", ok=True)

    monkeypatch.setattr(client, "_post_json", fake_post)
    asyncio.run(
        client.send_template(
            "PHONE", "+573000000000", _spec(), {"order_reference": "#31"},
            flow_token="review_x",
        )
    )
    button = captured["data"]["template"]["components"][-1]
    assert button["parameters"][0]["action"]["flow_token"] == "review_x"
