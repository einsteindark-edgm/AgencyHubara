"""El `session_key` de la metadata de Medusa es dato de AFUERA: el sync del chat no sale del vault.

Hallazgo de endurecimiento (lectura de código, 2026-09-21; NO explotado —
precondición: escritura en Medusa Admin + un JSON dict existente en el destino).
`confirm_payment`, `cancel_order` y `reverse_payment` sincronizan el chat
leyendo Y ESCRIBIENDO `<vault>/<order.metadata.session_key>/metadata.json`. El
`session_key` lo escribe `register_order`, pero es editable en Medusa Admin: con
una sesión real delante, `wa_<real>/../../x` resuelve FUERA del vault (el primer
componente existe, así que `/../../` resuelve) y el JSON que hubiera ahí se
reescribía con el tag de cierre — más un flush de CAPI agendado para ese "id".

Contrato: el sync es best-effort. Un `session_key` que no pasa el piso
(`src.platform.state.is_vault_session_id`) se SALTEA; el comando del lado Medusa
sigue siendo exitoso. Mismo dato y mismo criterio que
`orders/api::_resolve_session_for_order`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import respx
from httpx import Response

import src.platform.orders.medusa_order_command as command_mod
from src.platform.medusa.client import HttpMedusaClient
from src.platform.orders.command_port import (
    CancelOrderCommand,
    ConfirmPaymentCommand,
    OrderCommandResult,
    ReversePaymentCommand,
)
from src.platform.orders.medusa_order_command import MedusaOrderCommand

_BASE_URL = "http://medusa.test"
_ORDER = "order_1"
_REAL = "wa_15550001111"


@pytest.fixture
async def adapter():
    client = HttpMedusaClient(base_url=_BASE_URL, admin_token="sk_test", timeout=5.0)
    yield MedusaOrderCommand(client)
    await client.aclose()


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """Vault como SUBDIRECTORIO de tmp_path (el padre es controlable) y el
    flush de CAPI capturado: ni red ni tasks colgadas."""
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    flushed: list[str] = []
    monkeypatch.setattr(command_mod, "WORKSPACE_VAULT_DIR", vault_dir)
    monkeypatch.setattr(command_mod, "schedule_capi_flush", flushed.append)
    return vault_dir, flushed


def _write(directory: Path, data: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "metadata.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _order(session_key: str, **extra) -> dict:
    return {
        "order": {
            "id": _ORDER,
            "status": "pending",
            "total": 17000,
            "metadata": {"hubara_stage": "preparing", "session_key": session_key},
            **extra,
        }
    }


def _patched(**metadata) -> Response:
    return Response(200, json={"order": {"id": _ORDER, "metadata": metadata}})


async def _confirm(adapter: MedusaOrderCommand, session_key: str) -> OrderCommandResult:
    with respx.mock(base_url=_BASE_URL) as m:
        m.get(f"/admin/orders/{_ORDER}").mock(
            return_value=Response(200, json=_order(session_key, payment_status="not_paid"))
        )
        m.post("/admin/payment-collections").mock(
            return_value=Response(200, json={"payment_collection": {"id": "pc_1"}})
        )
        m.post("/admin/payment-collections/pc_1/mark-as-paid").mock(
            return_value=Response(200, json={"payment_collection": {"id": "pc_1"}})
        )
        m.post(f"/admin/orders/{_ORDER}").mock(
            return_value=_patched(hubara_stage="preparing", hubara_payment_confirmed=True)
        )
        return await adapter.confirm_payment(ConfirmPaymentCommand(order_id=_ORDER, by="ed"))


async def _cancel(adapter: MedusaOrderCommand, session_key: str) -> OrderCommandResult:
    with respx.mock(base_url=_BASE_URL) as m:
        m.get(f"/admin/orders/{_ORDER}").mock(
            return_value=Response(200, json=_order(session_key))
        )
        m.post(f"/admin/orders/{_ORDER}").mock(return_value=_patched(hubara_stage="cancelled"))
        m.post(f"/admin/orders/{_ORDER}/cancel").mock(
            return_value=Response(200, json={"order": {"id": _ORDER, "status": "canceled"}})
        )
        return await adapter.cancel_order(
            CancelOrderCommand(order_id=_ORDER, reason="cliente se arrepintió", by="ed")
        )


async def _reverse(adapter: MedusaOrderCommand, session_key: str) -> OrderCommandResult:
    refunded = [
        {"id": "pay_1", "amount": 17000, "captures": [{"amount": 17000}], "refunds": [{"amount": 17000}]}
    ]
    order = _order(
        session_key,
        payment_status="refunded",
        payment_collections=[{"id": "pc_1", "payments": refunded}],
    )
    order["order"]["metadata"]["hubara_payment_confirmed"] = True
    with respx.mock(base_url=_BASE_URL) as m:
        m.get(f"/admin/orders/{_ORDER}").mock(return_value=Response(200, json=order))
        m.post(f"/admin/orders/{_ORDER}").mock(
            return_value=_patched(hubara_stage="preparing", hubara_payment_confirmed=False)
        )
        return await adapter.reverse_payment(
            ReversePaymentCommand(order_id=_ORDER, reason="clic por error", by="ed")
        )


#: comando → (runner, un chat que ESE sync reescribiría hoy, tag tras sincronizar)
_COMMANDS = {
    "confirm_payment": (_confirm, {"tag": "HUMANO", "active_route": "humano"}, "COMPRA_EXITOSA"),
    "cancel_order": (_cancel, {"tag": "HUMANO", "active_route": "humano"}, "RECHAZO"),
    "reverse_payment": (_reverse, {"tag": "COMPRA_EXITOSA", "active_route": "ventas"}, "HUMANO"),
}

_OUTSIDE_KEYS = [
    f"{_REAL}/../../x",  # con una sesión real delante, `/../../` resuelve
    "..",  # el padre del vault
    ".",  # el vault mismo
    "_analytics",  # directorio del vault que no es una sesión
]


@pytest.mark.parametrize("session_key", _OUTSIDE_KEYS)
@pytest.mark.parametrize("command", list(_COMMANDS))
async def test_session_key_from_medusa_never_makes_the_sync_touch_a_non_session_file(
    adapter, vault, command, session_key
):
    vault_dir, flushed = vault
    run, chat, _tag = _COMMANDS[command]
    _write(vault_dir / _REAL, {"tag": "HUMANO"})
    canary = _write(vault_dir / session_key, {**chat, "customer_name": "canario"})
    before = canary.read_bytes()

    result = await run(adapter, session_key)

    assert result.success is True  # el lado Medusa no se entera
    assert canary.read_bytes() == before  # byte a byte
    assert flushed == []


@pytest.mark.parametrize("session", [_REAL, "wa_+15550001111"])
@pytest.mark.parametrize("command", list(_COMMANDS))
async def test_a_real_session_still_gets_synced(adapter, vault, command, session):
    vault_dir, _flushed = vault
    run, chat, tag_after = _COMMANDS[command]
    chat_file = _write(vault_dir / session, chat)

    result = await run(adapter, session)

    assert result.success is True
    assert json.loads(chat_file.read_text(encoding="utf-8"))["tag"] == tag_after
