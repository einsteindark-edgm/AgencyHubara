"""Red de seguridad: el bot prometió el formulario de envío y no lo mandó.

Incidente del 2026-10-09 (bot V2 con Jev, ventas-4, cliente sin teléfono): con
el producto, la ciudad y la forma de pago elegidos, el bot contestó bien y
cerró con «te paso el formulario para los datos de envío», pero NO llamó
`request_shipping_details`; en el turno siguiente lo volvió a prometer. El
operador tuvo que mandarlo a mano desde la app. Criterio del operador: la
respuesta estaba bien, solo faltó el formulario.

`ensure_promised_handoff_activity` (las promesas del texto, antes de enviarlo)
encola el formulario con los ítems del borrador cuando el texto lo promete y
en el episodio no salió ni está en la cola. Una sola vez por episodio.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from temporalio.testing import ActivityEnvironment

from src.plugins.chats.agent.sales.activities import episode_closure
from src.plugins.chats.agent.sales.activities.episode_closure import ensure_promised_handoff_activity
from src.sdk.catalogkit import (
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
    ProductNotFoundError,
)

_FIXED_DT = datetime(2026, 10, 9, 16, 30, 0, tzinfo=timezone.utc)
NOW = int(_FIXED_DT.timestamp() * 1000)
SID = "wa_CO9990000000000001"  # cliente sin teléfono (id sintético)
PROMISE = "¡Perfecto! Contra entrega entonces 🤍\n\nTe paso el formulario para tus datos de envío."
PROMISE_AGAIN = "Sí, llega el martes por el festivo.\n\nTe paso el formulario y dejamos todo listo 🤍"

_CALABAZA = CatalogProductDTO(
    id="prod_calabaza", handle="calabaza", title="Calabaza", status="published",
    variants=[CatalogVariantDTO(
        id="variant_calabaza", title="Unico", prices=[CatalogPriceDTO(amount="16000", currency_code="cop")],
    )],
)


class _Catalog:
    async def search(self, q: str, *, limit: int = 10, category: str | None = None) -> Any:
        return SimpleNamespace(query=q, results=[_CALABAZA])

    async def get_by_handle(self, handle: str) -> CatalogProductDTO:
        if handle != _CALABAZA.handle:
            raise ProductNotFoundError(handle)
        return _CALABAZA


@pytest.fixture(autouse=True)
def _catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(episode_closure, "_catalog_client", lambda: _Catalog())


def _episode(slots: dict[str, Any]) -> dict[str, Any]:
    return {
        "episode_id": "ep_001",
        "started_at_ms": NOW - 600_000,
        "closed_at_ms": None,
        "order_draft": {"slots": slots, "updated_at_ms": NOW - 60_000},
    }


_DRAFT = {"producto": "Calabaza", "cantidad": "3", "ciudad": "Medellín", "metodo_pago": "contra entrega"}


def _seed(vault: Path, **metadata: Any) -> Path:
    md = vault / SID / "metadata.json"
    md.parent.mkdir(parents=True, exist_ok=True)
    base = {"active_route": "ventas", "tag": "INTERESADO", "episodes": [_episode(_DRAFT)]}
    md.write_text(json.dumps({**base, **metadata}, ensure_ascii=False), encoding="utf-8")
    return md


def _intents(md: Path) -> list[dict[str, Any]]:
    return json.loads(md.read_text(encoding="utf-8")).get("pending_ui_intents") or []


def _delivered(md: Path, at_ms: int) -> None:
    row = {"id": "f1", "kind": "shipping_flow", "ok": True, "wamid": "wamid.f1", "at_ms": at_ms}
    (md.parent / "ui_intents_delivered.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")


async def _run(text: str) -> bool:
    env = ActivityEnvironment()
    env.info = ActivityEnvironment.default_info().__class__(
        **{**env.info.__dict__, "scheduled_time": _FIXED_DT, "current_attempt_scheduled_time": _FIXED_DT}
    )
    return await env.run(ensure_promised_handoff_activity, SID, text)


async def test_the_promised_form_is_queued_with_the_draft_items(_isolate_vault_dir: Path) -> None:
    md = _seed(_isolate_vault_dir)

    assert await _run(PROMISE) is False  # no escala: manda el formulario

    (intent,) = _intents(md)
    assert intent["kind"] == "shipping_flow"
    assert intent["params"]["order_total_cop"] == 48000  # 3 × $16.000 del catálogo
    assert "Calabaza" in intent["params"]["body"]


async def test_the_second_promise_does_not_queue_it_twice(_isolate_vault_dir: Path) -> None:
    md = _seed(_isolate_vault_dir)

    await _run(PROMISE)
    await _run(PROMISE_AGAIN)

    assert [i["kind"] for i in _intents(md)] == ["shipping_flow"]


async def test_a_form_the_llm_already_queued_is_not_duplicated(_isolate_vault_dir: Path) -> None:
    queued = {"id": "x1", "kind": "shipping_flow", "params": {}, "queued_at_ms": NOW - 1_000}
    md = _seed(_isolate_vault_dir, pending_ui_intents=[queued])

    await _run(PROMISE)

    assert _intents(md) == [queued]


async def test_a_form_already_delivered_in_this_episode_is_not_sent_again(_isolate_vault_dir: Path) -> None:
    md = _seed(_isolate_vault_dir)
    _delivered(md, NOW - 120_000)

    await _run(PROMISE)

    assert _intents(md) == []


async def test_a_form_delivered_in_an_earlier_episode_does_not_block_a_new_one(_isolate_vault_dir: Path) -> None:
    md = _seed(_isolate_vault_dir)
    _delivered(md, NOW - 9_000_000)

    await _run(PROMISE)

    assert [i["kind"] for i in _intents(md)] == ["shipping_flow"]


async def test_asking_whether_to_send_it_is_not_a_promise(_isolate_vault_dir: Path) -> None:
    md = _seed(_isolate_vault_dir)

    await _run("Perfecto 🤍 ¿Te paso el formulario para los datos de envío?")

    assert _intents(md) == []


async def test_a_customer_who_just_deferred_gets_no_form(_isolate_vault_dir: Path) -> None:
    md = _seed(
        _isolate_vault_dir,
        last_inbound_message_id="wamid.defer",
        last_inbound_signal={"kind": "deferral", "at_ms": NOW, "message_id": "wamid.defer", "text": "voy en camino"},
    )

    await _run(PROMISE)

    assert _intents(md) == []


async def test_a_conversation_with_a_human_gets_no_form(_isolate_vault_dir: Path) -> None:
    md = _seed(_isolate_vault_dir, active_route="humano", tag="HUMANO")

    await _run(PROMISE)

    assert _intents(md) == []


async def test_a_draft_product_missing_from_the_catalog_sends_nothing(_isolate_vault_dir: Path) -> None:
    md = _seed(_isolate_vault_dir, episodes=[_episode({**_DRAFT, "producto": "Vela inventada"})])

    assert await _run(PROMISE) is False

    assert _intents(md) == []


async def test_a_draft_without_quantity_asks_for_one(_isolate_vault_dir: Path) -> None:
    draft = {k: v for k, v in _DRAFT.items() if k != "cantidad"}
    md = _seed(_isolate_vault_dir, episodes=[_episode(draft)])

    await _run(PROMISE)

    (intent,) = _intents(md)
    assert intent["params"]["order_total_cop"] == 16000


@pytest.mark.parametrize(
    "text",
    [
        "Te envío el formulario para tus datos 🤍",
        "Listo, ya te mando el formulario.",
        "Te comparto el formulario de envío.",
        "Te voy a enviar el formulario para que completes tus datos.",
        "Ahí te va el formulario 🤍",
    ],
)
async def test_other_ways_of_promising_it(_isolate_vault_dir: Path, text: str) -> None:
    md = _seed(_isolate_vault_dir)

    await _run(text)

    assert [i["kind"] for i in _intents(md)] == ["shipping_flow"]


@pytest.mark.parametrize(
    "text",
    [
        "Ya te envié el formulario, cuando lo llenes seguimos 🤍",
        "Llena el formulario que te llegó y listo.",
        "El formulario de envío te pide ciudad, dirección y teléfono.",
    ],
)
async def test_talking_about_the_form_is_not_promising_it(_isolate_vault_dir: Path, text: str) -> None:
    md = _seed(_isolate_vault_dir)

    await _run(text)

    assert _intents(md) == []
