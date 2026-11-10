"""`send_reply` no le vuelve a pedir al cliente un dato que el pedido ya tiene
(capacidad `repregunta`, desde `ventas-8`).

Informe de huecos de producción del 2026-11-03 (bot V2): con la cantidad ya en
el pedido, el bot preguntó si dejaba la caja completa o ajustaba la cantidad
(VAR-06); con la ciudad, el barrio y la dirección ya anotados, volvió a pedir
«dos cosas de la dirección» (ENV-04). Los dos textos salieron por `send_reply`.

Los datos salen del borrador del pedido (el piso: nunca uno que falta); Jev dice
si el texto se lo vuelve a pedir; el texto vuelve al modelo UNA vez por mensaje
del cliente con lo que ya está en el pedido, y el segundo intento sale. Sin Jev,
o con un paquete anterior, el texto sale igual. Textos sintéticos.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from exoclaw.agent.tools import ToolContext

from src.plugins.chats.agent.sales.decisions import registry
from src.plugins.chats.agent.sales.state import FilesystemMetadataStore
from src.plugins.chats.agent.sales.tools.fact_check import FactCheck
from src.plugins.chats.agent.sales.tools.reply import SendReplyTool

KEY = "wa_573001111111"
IN_FORGE_CLONE = not (Path(__file__).resolve().parents[5] / "forge").is_dir()
ASKS_QUANTITY = "La caja trae 12 velitas. ¿Te sirve la caja completa o prefieres ajustar la cantidad?"
ASKS_ADDRESS = "Me falta confirmar algo: ¿la dirección y el barrio son los mismos que me diste?"
#: Lo que el preámbulo y la presentación no deben tocar en estas pruebas.
KEEP = {**{f"preambulo.{n}": 0.01 for n in range(1, 6)}, "variante.ajena": 0.05}


class _Catalog:
    async def search(self, q: str = "", limit: int = 30):
        from types import SimpleNamespace

        return SimpleNamespace(results=[])


@pytest.fixture(autouse=True)
def _bundle(monkeypatch):
    if IN_FORGE_CLONE:
        pytest.skip("clon de forge: los paquetes de prueba de esta tienda no viajan")
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-8")
    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    registry.reset()
    yield
    registry.reset()


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(session_key=KEY, channel="whatsapp", chat_id=KEY)


def _jev(monkeypatch, answers: dict[str, float]):
    """El bot nuevo: Jev decide, con estas respuestas fijas."""
    from src.platform.perception.adapters.fake import FakePerceptionAdapter
    from src.sdk import connectorkit
    from src.sdk.connectorkit import TypedAnswer

    fake = FakePerceptionAdapter({q: TypedAnswer(id=q, kind="noul", p=p) for q, p in {**KEEP, **answers}.items()})

    def _get(_oracle: str):
        return fake

    _get.cache_clear = lambda: None
    monkeypatch.setattr(connectorkit, "get_perception_port", _get)
    monkeypatch.setenv("DECISIONS_BOT", "B")
    return fake


def _tool(vault: Path, draft: dict[str, Any]) -> SendReplyTool:
    episode: dict[str, Any] = {"episode_id": "ep_001", "closed_at_ms": None, "order_draft": draft}
    FilesystemMetadataStore(vault).write(KEY, {"last_inbound_message_id": "wamid.T9", "episodes": [episode]})
    catalog = _Catalog()
    fact_check = FactCheck(vault, catalog, history_reader=lambda _key: [])
    return SendReplyTool(workspace=str(vault), vault_dir=vault, catalog=catalog, fact_check=fact_check)


async def _send(tool: SendReplyTool, ctx: ToolContext, text: str) -> dict:
    return json.loads(await tool.execute_with_context(ctx, text=text))


QUANTITY_DRAFT = {"items": [{"producto": "Velas de deseos cuadradas", "cantidad": "1", "color": "Multicolor"}]}
SHIPPING_DRAFT = {
    **QUANTITY_DRAFT,
    "slots": {"ciudad": "Bogotá", "barrio": "Centro", "direccion": "Calle 1 # 2-3"},
}


def _repregunta_ids(fake) -> list[str]:
    return [q for _state, ids in fake.calls for q in ids if q.startswith("repregunta.")]


async def test_asking_again_for_the_quantity_goes_back_once_with_what_the_order_has(
    tmp_path: Path, ctx: ToolContext, monkeypatch
) -> None:
    fake = _jev(monkeypatch, {"repregunta.dato_1": 0.93})
    tool = _tool(tmp_path, QUANTITY_DRAFT)

    first = await _send(tool, ctx, ASKS_QUANTITY)
    again = await _send(tool, ctx, ASKS_QUANTITY)

    assert first.get("reply") is None and (first["sent"], first["error"]) == (False, "known_data")
    assert "el dato «cantidad»" in first["message"]
    assert "1 × Velas de deseos cuadradas" in first["message"]
    # UNA vez por mensaje del cliente: el segundo intento sale (nunca un bucle).
    assert again["reply"]["text"] == ASKS_QUANTITY
    assert _repregunta_ids(fake) == ["repregunta.dato_1"]


async def test_asking_again_for_shipping_data_names_them_without_personal_values(
    tmp_path: Path, ctx: ToolContext, monkeypatch
) -> None:
    # Ítems: [1] cantidad, [2] ciudad, [3] barrio, [4] dirección.
    _jev(monkeypatch, {"repregunta.dato_1": 0.02, "repregunta.dato_2": 0.05, "repregunta.dato_3": 0.9,
                       "repregunta.dato_4": 0.92})
    tool = _tool(tmp_path, SHIPPING_DRAFT)

    held = await _send(tool, ctx, ASKS_ADDRESS)

    assert held["error"] == "known_data"
    assert "el dato «barrio»" in held["message"] and "el dato «dirección de envío»" in held["message"]
    assert "cantidad" not in held["message"] and "Calle 1" not in held["message"] and "Centro" not in held["message"]


async def test_a_text_that_does_not_ask_again_goes_out(tmp_path: Path, ctx: ToolContext, monkeypatch) -> None:
    _jev(monkeypatch, {"repregunta.dato_1": 0.04})
    tool = _tool(tmp_path, QUANTITY_DRAFT)

    sent = await _send(tool, ctx, "Perfecto, te dejo 1 caja. ¿Te paso el resumen del pedido?")

    assert sent["reply"]["text"] == "Perfecto, te dejo 1 caja. ¿Te paso el resumen del pedido?"


async def test_an_empty_draft_never_asks_jev(tmp_path: Path, ctx: ToolContext, monkeypatch) -> None:
    fake = _jev(monkeypatch, {})
    tool = _tool(tmp_path, {})

    sent = await _send(tool, ctx, ASKS_QUANTITY)

    assert sent["reply"]["text"] == ASKS_QUANTITY and _repregunta_ids(fake) == []


async def test_without_jev_the_text_goes_out(tmp_path: Path, ctx: ToolContext) -> None:
    tool = _tool(tmp_path, QUANTITY_DRAFT)

    sent = await _send(tool, ctx, ASKS_QUANTITY)

    assert sent["reply"]["text"] == ASKS_QUANTITY


async def test_with_ventas_7_the_text_goes_out_without_asking(
    tmp_path: Path, ctx: ToolContext, monkeypatch
) -> None:
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-7")
    registry.reset()
    fake = _jev(monkeypatch, {"repregunta.dato_1": 0.93})
    tool = _tool(tmp_path, QUANTITY_DRAFT)

    sent = await _send(tool, ctx, ASKS_QUANTITY)

    assert sent["reply"]["text"] == ASKS_QUANTITY and _repregunta_ids(fake) == []
