"""Eventos CAPI de etapa del pedido llevan valor, moneda y productos.

Prod 2026-09-17 14:43Z y 2026-09-18 19:29Z: Meta rechazó ``OrderShipped``
(source ``order_stage:shipping``) con HTTP 400 "OrderShipped event currency
missing" — ``emit_order_stage_activity`` lo encolaba sin value/currency/
contents. Regla: el total y la moneda salen de ``OrderFacts`` (Medusa vivo);
la copia ``registered_order`` del chat es solo respaldo si Medusa no responde.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.platform.orders.facts import InMemoryOrderFacts, OrderFacts
from src.platform.whatsapp.capi_outbox import CapiConfig, flush_capi_outbox
from src.plugins.orders.agent.activities import emit_stage

SESSION = "wa_573001234567"
ORDER = "order_01SHIP"
NOW_MS = 1_758_200_000_000
CONTENTS = [{"id": "SKU-VELA-1", "quantity": 2, "item_price": 60000}]


def _metadata(**extra: Any) -> dict[str, Any]:
    md: dict[str, Any] = {
        "ctwa_referrals": [{"ctwa_clid": "CLID_1", "captured_at_ms": NOW_MS - 1000}],
        "registered_order": {
            "success": True,
            "order_id": ORDER,
            "total_cop": 120000,
            "currency": "COP",
            "capi_contents": CONTENTS,
        },
    }
    md.update(extra)
    return md


_FACTS: dict[str, InMemoryOrderFacts] = {}


def _use_facts(port: InMemoryOrderFacts) -> None:
    _FACTS["port"] = port


def _fact(total: int) -> OrderFacts:
    return OrderFacts(
        order_id=ORDER, display_id="#31", total_cop=total, currency_code="cop",
        pay_status="paid", stage="shipping", customer="Cliente", is_draft=False,
    )


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import src.sdk.connectorkit as connectorkit

    _use_facts(InMemoryOrderFacts(available=False))
    monkeypatch.setattr(connectorkit, "get_order_facts_port", lambda: _FACTS["port"])

    async def _no_flush(session_id: str, **_: Any) -> None:
        return None

    monkeypatch.setattr(emit_stage, "WORKSPACE_VAULT_DIR", tmp_path)
    monkeypatch.setattr(connectorkit, "flush_capi_outbox", _no_flush)
    return tmp_path


def _seed(vault: Path, metadata: dict[str, Any]) -> Path:
    path = vault / SESSION / "metadata.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(metadata), encoding="utf-8")
    return path


def _queued(path: Path, event_name: str) -> dict[str, Any]:
    outbox = json.loads(path.read_text(encoding="utf-8"))["capi_outbox"]
    return next(e for e in outbox if e["event_name"] == event_name)


@pytest.mark.parametrize(
    ("stage", "event_name"),
    [("shipping", "OrderShipped"), ("delivered", "OrderDelivered"), ("cancelled", "OrderCanceled")],
)
async def test_stage_event_uses_live_order_total(vault: Path, stage: str, event_name: str) -> None:
    # Total editado en Medusa (caso pedido #31): gana el valor vivo.
    path = _seed(vault, _metadata())
    _use_facts(InMemoryOrderFacts([_fact(150000)]))

    await emit_stage._emit_stage_capi(SESSION, ORDER, stage)

    entry = _queued(path, event_name)
    assert (entry["value"], entry["currency"], entry["contents"]) == (150000, "COP", CONTENTS)


async def test_medusa_down_falls_back_to_registered_order(vault: Path) -> None:
    path = _seed(vault, _metadata())

    _use_facts(InMemoryOrderFacts(available=False))
    await emit_stage._emit_stage_capi(SESSION, ORDER, "shipping")

    entry = _queued(path, "OrderShipped")
    assert (entry["value"], entry["currency"], entry["contents"]) == (120000, "COP", CONTENTS)


async def test_unknown_total_still_sends_currency_to_meta(vault: Path) -> None:
    # Sin registered_order y sin Medusa: el evento sale igual, con moneda.
    path = _seed(vault, _metadata(registered_order=None))

    _use_facts(InMemoryOrderFacts(available=False))
    await emit_stage._emit_stage_capi(SESSION, ORDER, "shipping")

    entry = _queued(path, "OrderShipped")
    posted: list[dict[str, Any]] = []

    async def _post(url: str, body: dict[str, Any], token: str) -> httpx.Response:
        posted.append(body)
        return httpx.Response(200, json={"events_received": 1})

    cfg = CapiConfig(dataset_id="DS1", access_token="TOK", waba_id="WABA1",
                     test_event_code="", vault_dir=vault)
    await flush_capi_outbox(SESSION, config=cfg, post=_post, now_ms=NOW_MS)

    assert entry["value"] is None
    assert posted[0]["data"][0]["custom_data"] == {"currency": "COP", "order_id": ORDER}


async def test_outbox_replays_the_same_payload_to_meta(vault: Path) -> None:
    path = _seed(vault, _metadata())
    _use_facts(InMemoryOrderFacts([_fact(150000)]))
    await emit_stage._emit_stage_capi(SESSION, ORDER, "shipping")
    posted: list[dict[str, Any]] = []

    async def _post(url: str, body: dict[str, Any], token: str) -> httpx.Response:
        posted.append(body)
        # 1er intento: Meta caído (5xx) → queda en el outbox para el replay.
        if len(posted) == 1:
            return httpx.Response(503, json={"error": {"message": "busy"}})
        return httpx.Response(200, json={"events_received": 1})

    cfg = CapiConfig(dataset_id="DS1", access_token="TOK", waba_id="WABA1",
                     test_event_code="", vault_dir=vault)
    await flush_capi_outbox(SESSION, config=cfg, post=_post, now_ms=NOW_MS)
    await flush_capi_outbox(SESSION, config=cfg, post=_post, now_ms=NOW_MS + 60_000)

    assert len(posted) == 2
    first, replay = (b["data"][0] for b in posted)
    assert first == replay
    assert first["event_name"] == "OrderShipped"
    assert first["custom_data"]["value"] == 150000
    assert first["custom_data"]["currency"] == "COP"
    assert first["custom_data"]["contents"] == CONTENTS
    sent = json.loads(path.read_text(encoding="utf-8"))["capi_events_sent"]
    assert sent[-1]["status"] == "sent"
