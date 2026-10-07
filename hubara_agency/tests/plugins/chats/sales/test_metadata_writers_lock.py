"""Los escritores del metadata de la sesión no pisan lo que otro proceso guardó mientras tanto.

El flush de UI intents perdía lo que el ingest del webhook escribía durante un envío (lost update) y se
arregló con `FilesystemMetadataStore.update()`: flock en `metadata.json.lock` + lectura fresca + escritura
atómica, el mismo camino del ingest (ver `test_flush_concurrent_writes.py`). Quedaban dos escritores que
leían y reescribían el archivo ENTERO sin ese lock:

* `_append_intent`, por donde encolan todas las tools de UI del bot;
* `register_order`, que además escribía con `write_text` plano (un lector concurrente podía ver JSON a medias).

Aquí otro proceso (el ingest, en la API) guarda con `update()` justo en el hueco entre la lectura y la
escritura del escritor: sin lock, el escritor lo pisa con su copia vieja.
"""
from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.orders.port import OrderItem, OrderRegistrationResult, OrderShipping
from src.platform.state import FilesystemMetadataStore
from src.plugins.chats.agent.sales.tools import order_registration
from src.plugins.chats.agent.sales.tools.order_registration import RegisterOrderTool
from src.plugins.chats.agent.sales.tools.ui_intents import SendShippingRatesTool

_SID = "wa_test_writers_race"
_T0 = 1_790_000_000_000  # último mensaje del cliente cuando el escritor lee
_T1 = _T0 + 2_000  # el cliente escribe en el hueco


class _OtherProcess:
    """Otro proceso que guarda con `update()` en el hueco entre la lectura y la escritura del escritor.

    Corre en un hilo. Si el escritor no toma el lock, el hilo termina enseguida y el escritor lo pisa al
    escribir su copia vieja. Si lo toma, el hilo espera y escribe DESPUÉS, sobre lo que dejó el escritor.
    """

    def __init__(self, vault: Path, mutate: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self._store = FilesystemMetadataStore(vault)
        self._mutate = mutate
        self._thread: threading.Thread | None = None

    def strike(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._store.update, args=(_SID, self._mutate))
        self._thread.start()
        self._thread.join(timeout=0.3)

    def finish(self) -> None:
        assert self._thread is not None, "el otro proceso nunca escribió: el hueco no se ejercitó"
        self._thread.join(timeout=5)
        assert not self._thread.is_alive()


def _customer_writes(fresh: dict[str, Any]) -> dict[str, Any]:
    """Lo que hace el ingest con un mensaje nuevo del cliente."""
    fresh["last_inbound_at_ms"] = _T1
    fresh["last_inbound_message_id"] = "wamid.en_el_hueco"
    return fresh


def _seed(vault: Path, **extra: Any) -> None:
    (vault / _SID).mkdir(parents=True, exist_ok=True)
    metadata = {"phone_number_id": "pnid-1", "last_inbound_at_ms": _T0, **extra}
    (vault / _SID / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")


def _meta(vault: Path) -> dict[str, Any]:
    return json.loads((vault / _SID / "metadata.json").read_text(encoding="utf-8"))


def _ctx() -> ToolContext:
    return ToolContext(session_key=_SID, channel="whatsapp", chat_id=_SID)


@pytest.mark.asyncio
async def test_a_ui_tool_that_enqueues_keeps_what_the_ingest_saved_meanwhile(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _isolate_vault_dir
    _seed(vault, pending_ui_intents=[])
    other = _OtherProcess(vault, _customer_writes)
    # El hueco: la lectura fresca de `update()` (bajo el candado) y su escritura.
    real_fresh = FilesystemMetadataStore._fresh_locked

    def read_then_the_ingest_writes(self: FilesystemMetadataStore, path: Path) -> Any:
        snapshot = real_fresh(self, path)
        other.strike()
        return snapshot

    monkeypatch.setattr(FilesystemMetadataStore, "_fresh_locked", read_then_the_ingest_writes)

    await SendShippingRatesTool(workspace=str(vault)).execute_with_context(_ctx())
    other.finish()

    meta = _meta(vault)
    assert meta["last_inbound_at_ms"] == _T1  # el mensaje del cliente no se borró
    assert meta["last_inbound_message_id"] == "wamid.en_el_hueco"
    assert [i["kind"] for i in meta["pending_ui_intents"]] == ["shipping_rates"]


@dataclass
class _Port:
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def register_order(self, *, session_key: str, items: list[OrderItem], shipping: OrderShipping,
                             **kwargs: Any) -> OrderRegistrationResult:
        self.calls.append({"session_key": session_key, **kwargs})
        return OrderRegistrationResult(
            success=True, order_id="draft_test_race", provider="medusa", customer_id="cus_test",
            raw_payload={"id": "draft_test_race", "display_id": 7, "status": "draft"},
            items_resolved=[{"title": "Cruz de Vida", "variant_id": "var_test", "quantity": 1, "unit_price": 17000}],
        )


@pytest.mark.asyncio
async def test_register_order_keeps_what_the_ingest_saved_while_it_wrote_the_order(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _isolate_vault_dir
    _seed(vault, episodes=[{"episode_id": "ep_001", "started_at_ms": _T0 - 60_000, "closed_at_ms": None}])
    other = _OtherProcess(vault, _customer_writes)
    real_attach = order_registration.attach_order_to_active_episode

    def attach_while_the_ingest_writes(data: dict[str, Any], **kwargs: Any) -> Any:
        other.strike()
        return real_attach(data, **kwargs)

    # Entre leer el metadata y escribir el pedido, la tool anota la orden en el episodio: ahí cae el ingest.
    monkeypatch.setattr(order_registration, "attach_order_to_active_episode", attach_while_the_ingest_writes)
    tool = RegisterOrderTool(workspace=str(vault), vault_dir=vault, port=_Port())

    result = json.loads(await tool.execute_with_context(
        _ctx(),
        items=[{"handle": "cruz-de-vida", "quantity": 1, "unit_price_cop": 17000, "variant_label": "Lavanda"}],
        shipping={"city": "Bogotá", "neighborhood": "Chapinero", "address": "Calle 1 # 2-3",
                  "phone": "3000000000", "receiver_name": "Ana Prueba"},
        payment_method="transfer", subtotal_cop=17000, shipping_cop=7900, total_cop=24900,
    ))
    other.finish()

    assert result["registered"] is True
    meta = _meta(vault)
    assert meta["last_inbound_at_ms"] == _T1  # el mensaje del cliente no se borró
    assert meta["last_inbound_message_id"] == "wamid.en_el_hueco"
    assert meta["registered_order"]["order_id"] == "draft_test_race"  # y el pedido quedó
    assert [i["id"] for i in meta["pending_ui_intents"]] == ["payinstr-draft_test_race"]
