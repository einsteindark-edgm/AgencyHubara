"""El texto que el modelo redacta para una tarjeta (el cuerpo de la lista, del
selector, de los botones, el pie de la ficha, el texto sobre el enlace) pasa
por los mismos hechos que `send_reply` (incidente 2026-10-13): el contrato del
turno manda poner ahí el texto («…va en intro_text»), y ninguna revisión lo
miraba. Lo que no coincide vuelve al modelo y la tarjeta no se encola. La
retención es UNA por mensaje del cliente, compartida con `send_reply`.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from exoclaw.agent.tools import ToolContext

from src.plugins.chats.agent.sales.decisions import registry
from src.plugins.chats.agent.sales.state import FilesystemMetadataStore
from src.plugins.chats.agent.sales.tools.fact_check import FactCheck
from src.plugins.chats.agent.sales.tools.reply import SendReplyTool
from src.plugins.chats.agent.sales.tools.ui_intents import PresentProductsTool, SendQuickRepliesTool

KEY = "wa_573001111111"
INCIDENT = "¿Qué aroma prefieres? Las de deseos cuadradas vienen en *Chanel*, *Drakar* y *Ylang Ylang*."


def _product(title: str, handle: str, aromas: list[str], price: int) -> SimpleNamespace:
    return SimpleNamespace(
        title=title, handle=handle, tags=[f"Aroma: {a}" for a in aromas], options={"Unico": ["Unico"]},
        metadata={}, variants=[SimpleNamespace(prices=[SimpleNamespace(amount=price, currency_code="cop")])],
    )


DESEOS = _product("Velas de deseos cuadradas", "velas-de-deseos-cuadradas", ["Noche de Navidad"], 25_000)
PLEGARIA = _product("Plegaria de Luz", "plegaria-de-luz", ["Drakar", "Chanel", "Ylan ylang", "Lavanda"], 29_000)


class _Catalog:
    async def search(self, q: str = "", limit: int = 30):
        return SimpleNamespace(results=[DESEOS, PLEGARIA])


@pytest.fixture(autouse=True)
def _bundle(monkeypatch):
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-7")
    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    registry.reset()
    yield
    registry.reset()


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(session_key=KEY, channel="whatsapp", chat_id=KEY)


def _facts(vault: Path, quantity: str = "2") -> FactCheck:
    items = [{"producto": "Velas de deseos cuadradas", "cantidad": quantity}]
    episode = {"episode_id": "ep_001", "closed_at_ms": None, "order_draft": {"items": items}}
    FilesystemMetadataStore(vault).write(KEY, {"last_inbound_message_id": "wamid.T6", "episodes": [episode]})
    return FactCheck(vault, _Catalog())


def _queued(vault: Path) -> list:
    return list((FilesystemMetadataStore(vault).read(KEY) or {}).get("pending_ui_intents") or [])


async def test_the_list_body_with_foreign_aromas_goes_back_and_the_card_is_not_queued(
    tmp_path: Path, ctx: ToolContext
) -> None:
    tool = PresentProductsTool(workspace=str(tmp_path), catalog=_Catalog(), fact_check=_facts(tmp_path))

    held = json.loads(await tool.execute_with_context(ctx, handles=["velas-de-deseos-cuadradas"], intro_text=INCIDENT))

    assert held["error"] == "fact_check" and held["queued"] is False
    assert "«Chanel», «Drakar» y «Ylan ylang»" in held["message"] and "present_products" in held["message"]
    assert _queued(tmp_path) == []


async def test_the_buttons_body_with_cash_on_delivery_for_a_small_order_goes_back(
    tmp_path: Path, ctx: ToolContext, monkeypatch
) -> None:
    from src.platform.perception.adapters.fake import FakePerceptionAdapter
    from src.sdk import connectorkit
    from src.sdk.connectorkit import TypedAnswer

    fake = FakePerceptionAdapter({"pago_ajeno.termino_1": TypedAnswer(id="pago_ajeno.termino_1", kind="noul", p=0.95)})

    def _get(_oracle: str):
        return fake

    _get.cache_clear = lambda: None
    monkeypatch.setattr(connectorkit, "get_perception_port", _get)
    monkeypatch.setenv("DECISIONS_BOT", "B")
    tool = SendQuickRepliesTool(workspace=str(tmp_path), catalog=_Catalog(), fact_check=_facts(tmp_path, quantity="1"))
    buttons = [{"id": "si", "title": "Sí"}, {"id": "no", "title": "No"}]

    held = json.loads(await tool.execute_with_context(ctx, body="¿Lo pagas contra entrega?", buttons=buttons))

    assert held["error"] == "fact_check" and "contra entrega es desde $45.000" in held["message"]


async def test_send_reply_and_the_cards_share_one_hold_per_customer_message(tmp_path: Path, ctx: ToolContext) -> None:
    facts = _facts(tmp_path)
    reply = SendReplyTool(workspace=str(tmp_path), vault_dir=tmp_path, catalog=_Catalog(), fact_check=facts)
    cards = PresentProductsTool(workspace=str(tmp_path), catalog=_Catalog(), fact_check=facts)

    first = json.loads(await reply.execute_with_context(ctx, text=INCIDENT))
    again = json.loads(await cards.execute_with_context(ctx, handles=["velas-de-deseos-cuadradas"], intro_text=INCIDENT))

    assert first["error"] == "fact_check"
    assert again.get("error") != "fact_check"  # el modelo ya corrigió una vez en este mensaje


@pytest.mark.parametrize(
    "name",
    ["send_reply", "present_products", "present_variant_picker", "present_product_detail", "send_quick_replies",
     "send_cta_url"],
)
def test_the_sales_worker_gives_every_text_tool_the_facts_with_their_sources(tmp_path: Path, monkeypatch, name: str) -> None:
    """Gotcha #6: ejecutar la fábrica caza el nombre que falte. El cupón y el
    pedido salen de Medusa: sin esas fuentes, esos hechos no se revisarían."""
    monkeypatch.setenv("MEDUSA_BASE_URL", "http://medusa.test")
    monkeypatch.setenv("MEDUSA_ADMIN_TOKEN", "dummy")
    import src.plugins.chats.workers.sales  # noqa: F401  (registra las tools)
    from src.platform.tool_extensions import _EXTENSIONS  # type: ignore

    facts = dict(_EXTENSIONS)[f"sales.{name}"](tmp_path)._fact_check

    assert isinstance(facts, FactCheck)
    assert facts._promotions is not None and facts._catalog is not None


class _DetailCatalog(_Catalog):
    async def get_by_handle(self, handle: str):
        return {"plegaria-de-luz": PLEGARIA, "velas-de-deseos-cuadradas": DESEOS}[handle]


async def test_a_card_of_another_product_is_checked_against_that_product(tmp_path: Path) -> None:
    """Revisión 2026-10-13: con las velas de deseos en el pedido, la ficha de
    la Plegaria con SUS aromas se retenía («no son de Velas de deseos») y el
    mensaje empujaba al modelo a ponerle a la Plegaria el aroma de otra vela.
    El producto de la tarjeta es el foco."""
    facts = _facts(tmp_path)

    held = await facts.held(KEY, "Viene en Drakar, Chanel o Lavanda 🤍", tool="present_product_detail",
                            handles=["plegaria-de-luz"])

    assert held is None


async def test_the_detail_card_passes_its_product_as_the_focus(tmp_path: Path, ctx: ToolContext) -> None:
    """Sin pedido, la ficha de las velas de deseos con «Drakar» se retiene:
    el foco salió del `handle` de la tarjeta."""
    from src.plugins.chats.agent.sales.tools.ui_intents import PresentProductDetailTool

    FilesystemMetadataStore(tmp_path).write(KEY, {"last_inbound_message_id": "wamid.T6", "episodes": []})
    tool = PresentProductDetailTool(
        workspace=str(tmp_path), catalog=_DetailCatalog(), fact_check=FactCheck(tmp_path, _Catalog())
    )

    held = json.loads(await tool.execute_with_context(
        ctx, handle="velas-de-deseos-cuadradas", caption_suffix="Vienen en Drakar 🤍"
    ))

    assert held["error"] == "fact_check" and "«Drakar»" in held["message"]
