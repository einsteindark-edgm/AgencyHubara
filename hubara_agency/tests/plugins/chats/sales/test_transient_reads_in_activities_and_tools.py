"""Activities y tools que leen `metadata.json` al empezar reintentan un error
PASAJERO (PR #393, octava revisión, M2).

`read()` del store hace un intento y, ante un EMFILE o un EIO, lanza. El
workflow de ventas no atrapa esas activities y una tool que lanza corta el
turno del bot: un error de un milisegundo terminaba en un fallo. La lectura
inicial reintenta con `read_retrying_transient_errors_sync`: hasta 3 veces,
esperas cortas, solo errores pasajeros. Los workflows no se tocan (L-9).
"""
from __future__ import annotations

import errno
import inspect
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from exoclaw.agent.tools import ToolContext

SESSION = "wa_573001234567"


def _inside_store_read() -> bool:
    return any(
        frame.function == "read" and frame.filename.endswith(os.path.join("platform", "state.py"))
        for frame in inspect.stack()
    )


def _one_transient_read(monkeypatch: pytest.MonkeyPatch, code: int = errno.EMFILE, *, nth: int = 1) -> None:
    """La `nth` lectura de `metadata.json` por `FilesystemMetadataStore.read`
    falla UNA vez con un error pasajero (las lecturas directas del archivo no
    cuentan)."""
    real_read_text = Path.read_text
    seen = {"n": 0}

    def read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if self.name == "metadata.json" and _inside_store_read():
            seen["n"] += 1
            if seen["n"] == nth:
                raise OSError(code, os.strerror(code))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)


def _seed(vault: Path, data: dict[str, Any], session: str = SESSION) -> Path:
    path = vault / session / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_the_handoff_draft_note_survives_a_transient_read_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.plugins.chats.agent.sales.activities import bootstrap_session

    monkeypatch.setattr(bootstrap_session, "WORKSPACE_VAULT_DIR", tmp_path)
    _seed(tmp_path, {"episodes": [{"episode_id": "ep_001", "started_at_ms": 1,
                                   "order_draft": {"slots": {"producto": "Duo Zodiacal", "cantidad": "2"}}}]})
    _one_transient_read(monkeypatch)

    note = await bootstrap_session.read_order_draft_note_activity(SESSION)

    assert note is not None and "Duo Zodiacal" in note


@pytest.mark.asyncio
async def test_the_flush_survives_a_transient_read_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.platform import config
    from src.platform.whatsapp import client as wa_client
    from src.plugins.chats.agent.sales.activities import flush_ui_intents

    monkeypatch.setattr(config, "WORKSPACE_VAULT_DIR", tmp_path)
    _seed(tmp_path, {
        "phone_number_id": "pnid-1",
        "episodes": [{"episode_id": "ep_001", "started_at_ms": 1, "closed_at_ms": None}],
        "pending_ui_intents": [{
            "id": "foto-1", "kind": "product_detail", "queued_at_ms": int(time.time() * 1000),
            "params": {"handle": "cubo-love", "title": "Cubo Love", "image_url": "https://assets.example.com/c.webp",
                       "caption": "Cubo Love · $35.000 COP"},
        }],
        "outbound_media_index": {},
    })
    send_image = AsyncMock(return_value=SimpleNamespace(ok=True, wa_message_id="wamid.foto", error=None))
    monkeypatch.setattr(wa_client, "send_image", send_image)
    _one_transient_read(monkeypatch)

    assert await flush_ui_intents.flush_pending_ui_intents(SESSION) == 1
    assert send_image.await_count == 1


@pytest.mark.asyncio
async def test_set_order_slot_survives_a_transient_read_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.plugins.chats.agent.sales.tools.order_draft import SetOrderSlotTool
    from src.plugins.chats.agent.sales.use_cases.order_draft import ensure_active_episode

    meta: dict[str, Any] = {}
    ensure_active_episode(meta, now_ms=1_700_000_000_000)
    _seed(tmp_path, meta)
    ctx = ToolContext(session_key=SESSION, channel="whatsapp", chat_id=SESSION)
    _one_transient_read(monkeypatch)

    result = json.loads(await SetOrderSlotTool(workspace=str(tmp_path), vault_dir=tmp_path).execute_with_context(
        ctx, color="Blanco"
    ))

    assert result["updated"] is True


@pytest.mark.asyncio
async def test_present_order_confirmation_survives_a_transient_read_error(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.platform.catalog import CatalogPriceDTO, CatalogProductDTO, CatalogVariantDTO
    from src.plugins.chats.agent.sales.tools.ui_intents import PresentOrderConfirmationTool

    product = CatalogProductDTO(
        id="prod_t", handle="trilogia-del-terror", title="Trilogía del Terror", status="published",
        variants=[CatalogVariantDTO(id="variant_t", title="Unico", sku="HUB-T",
                                    prices=[CatalogPriceDTO(amount="49500", currency_code="cop")])],
    )

    class _Catalog:
        async def get_by_handle(self, handle: str) -> CatalogProductDTO:
            return product

    _seed(_isolate_vault_dir, {"episodes": [{"episode_id": "ep_001", "started_at_ms": 1, "closed_at_ms": None}]})
    ctx = ToolContext(session_key=SESSION, channel="whatsapp", chat_id=SESSION)
    # La 1.ª lectura es la del ledger de precios (que tolera el error); la 2.ª,
    # la del documento con el que la tool arma el resumen.
    _one_transient_read(monkeypatch, nth=2)

    result = json.loads(await PresentOrderConfirmationTool(
        workspace=str(_isolate_vault_dir), catalog=_Catalog()
    ).execute_with_context(
        ctx,
        items=[{"handle": "trilogia-del-terror", "quantity": 1, "unit_price_cop": 49500}],
        shipping_cop=16940,
        shipping_address_summary="Calle 1 #2-3, Centro, Cali",
        payment_method="cash_on_delivery",
    ))

    assert result["queued"] is True


def test_the_shipping_precondition_survives_a_transient_read_error(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.plugins.chats.agent.sales.tools.ui_intents import _shipping_precondition_rejection

    _seed(_isolate_vault_dir, {"episodes": [{"episode_id": "ep_001", "started_at_ms": 1, "closed_at_ms": None}]})
    expected = _shipping_precondition_rejection(SESSION, order_total_cop=49500, items_summary="1× Vela")
    _one_transient_read(monkeypatch)

    assert _shipping_precondition_rejection(SESSION, order_total_cop=49500, items_summary="1× Vela") == expected
