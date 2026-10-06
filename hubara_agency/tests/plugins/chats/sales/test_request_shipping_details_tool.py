"""Tests para `RequestShippingDetailsTool` — el intent encolado debe
traer `payment_options` dinámicas (2 ó 3 según el total de productos).

Por qué importa: el Flow JSON de Meta (single-screen, ver
`hubara_agency/docs/whatsapp_flows/shipping_v2.json`) bindea
`data-source: ${data.payment_options}` en el RadioButtonsGroup de método
de pago. El operador NO necesita re-editar y re-publicar el Flow para
cambiar las opciones de pago — esta lista, construida acá, se manda en
`flow_action_data` y Meta la renderiza tal cual.

Política Hubara: contra entrega desde $45.000 COP en productos (margen vs
costo del envío; umbral INCLUSIVO en `config/shipping.py`). Tests cubren los
3 thresholds canónicos (under, at boundary, over) + shape del payload.

Desde el incidente run ebbc203d (2026-09-16) la tool recibe `items`
(handle + cantidad) y el total sale del CATÁLOGO — el LLM no manda montos.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog import (
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
    ProductNotFoundError,
)
from src.plugins.chats.agent.sales.tools.ui_intents import (
    RequestShippingDetailsTool,
)
from src.plugins.chats.agent.sales_eval.evals.script_rubric import (
    DASH_RE,
    VOSEO_RES,
    disallowed_emojis,
    find_emojis,
)
from src.sdk.agentkit import looks_like_admin_leak, sanitize_llm_text


def _product(handle: str, title: str, price: int) -> CatalogProductDTO:
    return CatalogProductDTO(
        id=f"prod_{handle}", handle=handle, title=title, status="published",
        variants=[CatalogVariantDTO(
            id=f"variant_{handle}", title="Unico",
            prices=[CatalogPriceDTO(amount=str(price), currency_code="cop")],
        )],
    )


class _Catalog:
    products = {
        "vela-cruz-de-vida": _product("vela-cruz-de-vida", "Vela Cruz de Vida", 17000),
        "velas-pack": _product("velas-pack", "Velas pack", 15000),
        "velas-grandes": _product("velas-grandes", "Velas grandes", 45000),
        "velas": _product("velas", "Velas", 25000),
        "vela": _product("vela", "Vela", 20000),
    }

    async def get_by_handle(self, handle: str) -> CatalogProductDTO:
        try:
            return self.products[handle]
        except KeyError:
            raise ProductNotFoundError(handle) from None


def _items(handle: str, quantity: int = 1) -> list[dict]:
    return [{"handle": handle, "quantity": quantity}]


@pytest.fixture
def ctx():
    return ToolContext(
        session_key="wa_test_shipping",
        channel="whatsapp",
        chat_id="wa_test_shipping",
    )


@pytest.fixture
def seeded_vault(tmp_path, ctx):
    vault = tmp_path / "isolated_vault"
    (vault / ctx.session_key).mkdir(parents=True, exist_ok=True)
    # Guarda 2026-09-14: el formulario exige confirmación de compra en el
    # episodio activo (el cliente dijo que sí a un producto).
    (vault / ctx.session_key / "metadata.json").write_text(
        json.dumps({
            "episodes": [{
                "episode_id": "ep_001", "started_at_ms": 1, "closed_at_ms": None,
                "order_draft": {"slots": {"producto": "Cubo Love"}, "updated_at_ms": 1,
                                "confirmed_at_ms": 2, "confirmed_by": "text"},
            }],
        }),
        encoding="utf-8",
    )
    return vault


@pytest.fixture
def tool(seeded_vault):
    return RequestShippingDetailsTool(workspace=str(seeded_vault), catalog=_Catalog())


def _read_intent(vault, session_key: str) -> dict:
    data = json.loads(
        (vault / session_key / "metadata.json").read_text(encoding="utf-8")
    )
    intents = data.get("pending_ui_intents") or []
    assert len(intents) == 1, f"esperaba 1 intent, got {len(intents)}"
    return intents[0]


@pytest.mark.asyncio
async def test_payment_options_excludes_cod_below_45k(ctx, seeded_vault, tool):
    """Pedido chico ($17.000) → solo pago anticipado + link de pago, sin
    contra entrega (política Hubara: COD desde $45.000 para asegurar
    margen). Requisito 2026-08-31: 'tarjeta' ya NO es una opción — los
    pagos con tarjeta van por el link de pago (con recargo)."""
    result = json.loads(await tool.execute_with_context(ctx, items=_items("vela-cruz-de-vida")))
    assert result["queued"] is True

    intent = _read_intent(seeded_vault, ctx.session_key)
    assert intent["kind"] == "shipping_flow"

    flow_data = intent["params"]["flow_action_data"]
    payment_options = flow_data["payment_options"]
    ids = [opt["id"] for opt in payment_options]
    assert ids == ["transfer", "payment_link"]
    assert flow_data["show_cash_on_delivery"] is False


@pytest.mark.asyncio
async def test_payment_options_includes_cod_at_45k_boundary(ctx, seeded_vault, tool):
    """Incidente run ebbc203d (2026-09-16): el guion dice "contra entrega
    desde $45.000 en productos" y el bot se lo afirmó al cliente, pero el
    código usaba `> 45000` estricto → el formulario ocultó la opción y la
    clienta abandonó el Flow. La política es INCLUSIVA: $45.000 exacto
    ofrece contra entrega (una sola fuente: `config/shipping.py`)."""
    await tool.execute_with_context(ctx, items=_items("velas-pack", 3))  # 3 × 15.000

    intent = _read_intent(seeded_vault, ctx.session_key)
    flow_data = intent["params"]["flow_action_data"]
    assert flow_data["order_total_cop"] == 45000
    ids = [opt["id"] for opt in flow_data["payment_options"]]
    assert ids == ["cash_on_delivery", "transfer", "payment_link"]
    assert flow_data["show_cash_on_delivery"] is True


@pytest.mark.asyncio
async def test_payment_options_includes_cod_over_45k(ctx, seeded_vault, tool):
    """Pedido grande ($90.000) → 3 opciones con contra entrega PRIMERA
    (orden del requisito 2026-08-31), flag `show_cash_on_delivery` en true
    para data binding adicional del Flow si fuera necesario."""
    await tool.execute_with_context(ctx, items=_items("velas-grandes", 2))  # 2 × 45.000

    intent = _read_intent(seeded_vault, ctx.session_key)
    flow_data = intent["params"]["flow_action_data"]
    ids = [opt["id"] for opt in flow_data["payment_options"]]
    assert ids == ["cash_on_delivery", "transfer", "payment_link"]
    assert flow_data["show_cash_on_delivery"] is True

    # Cada opción trae title amigable con emoji
    titles = {opt["id"]: opt["title"] for opt in flow_data["payment_options"]}
    assert "Contra entrega" in titles["cash_on_delivery"]
    assert "Pago anticipado" in titles["transfer"]
    assert "Link de pago" in titles["payment_link"]


@pytest.mark.asyncio
async def test_payment_options_descriptions_inform_terms(ctx, seeded_vault, tool):
    """Requisito 2026-08-31 — cada forma de pago se informa con su condición:
    contra entrega → el valor lo calcula la transportadora; anticipado →
    Nequi o llave 3229041190; link de pago → recargo 1,5% (Nequi/
    Bancolombia) o 2,69% (otros bancos)."""
    await tool.execute_with_context(ctx, items=_items("velas-grandes", 2))

    intent = _read_intent(seeded_vault, ctx.session_key)
    options = intent["params"]["flow_action_data"]["payment_options"]
    desc = {opt["id"]: opt["description"] for opt in options}
    assert "transportadora" in desc["cash_on_delivery"].lower()
    assert "Nequi" in desc["transfer"]
    assert "3229041190" in desc["transfer"]
    assert "llave" in desc["transfer"].lower()
    assert "1,5%" in desc["payment_link"]
    assert "2,69%" in desc["payment_link"]


@pytest.mark.asyncio
async def test_intent_shape_for_meta_flow_compat(ctx, seeded_vault, tool):
    """El intent debe traer EXACTAMENTE los campos que espera el Flow JSON
    de Meta (single-screen `SHIPPING_DETAILS`). Anti-regresión: si alguien
    cambia el nombre de un campo (ej. `items_summary` → `summary`) sin
    actualizar el JSON publicado en Meta, el Flow se rompe en runtime
    (renderiza variables vacías). Esta firma debe quedar estable."""
    await tool.execute_with_context(ctx, items=_items("velas", 2))  # 2 × 25.000

    intent = _read_intent(seeded_vault, ctx.session_key)
    params = intent["params"]

    # Shape canónico para `wa_dtos.InteractiveFlowOutbound`
    assert params["flow_action"] == "navigate"
    assert params["flow_action_screen"] == "SHIPPING_DETAILS"
    assert params["flow_cta"] == "Completar datos"
    # Placeholder a propósito — el dispatcher lo resuelve desde env productivo
    assert params["flow_id"] == "FLOW_ID_SHIPPING_PLACEHOLDER"
    # `flow_token` único por sesión
    assert params["flow_token"].startswith("shipping_wa_test_shipping_")

    flow_data = params["flow_action_data"]
    # Las 4 keys que el JSON v1 espera en `data:`
    assert set(flow_data.keys()) == {
        "order_total_cop",
        "items_summary",
        "show_cash_on_delivery",
        "payment_options",
    }
    assert flow_data["order_total_cop"] == 50000
    assert flow_data["items_summary"] == "2× Velas"

    # `order_total_cop` también en params (para el fallback texto plano)
    assert params["order_total_cop"] == 50000


def _seed_draft(vault, session_key: str, draft: dict) -> None:
    """Borrador del episodio activo, ya confirmado, con sus productos y variantes."""
    (vault / session_key / "metadata.json").write_text(
        json.dumps({
            "episodes": [{
                "episode_id": "ep_001", "started_at_ms": 1, "closed_at_ms": None,
                "order_draft": {**draft, "updated_at_ms": 1, "confirmed_at_ms": 2, "confirmed_by": "text"},
            }],
        }),
        encoding="utf-8",
    )


def _form_body(vault, session_key: str) -> str:
    return _read_intent(vault, session_key)["params"]["body"]


@pytest.fixture
def flow_ready(monkeypatch):
    """El Flow nativo configurado, como en producción: el cliente lee el
    mensaje del formulario (sin él, el flush manda la lista de campos)."""
    monkeypatch.setenv("META_FLOW_ID_SHIPPING", "flow-123")


@pytest.mark.asyncio
async def test_the_form_message_says_what_the_customer_is_ordering(ctx, seeded_vault, tool, flow_ready):
    """Incidente 2026-10-06 (bot V2, turno 9): el cliente dijo «2» y el
    formulario salió con «Para enviarte *2× …* necesito unos datos. Toca el
    botón para completar el formulario — toma 30 segundos.»: sin aroma, sin
    color, sin subtotal y con guion largo. El mensaje lo arma el código con el
    borrador del pedido y los precios del catálogo, y el envelope lo devuelve
    como `customer_text` (lo que leyó el cliente)."""
    _seed_draft(seeded_vault, ctx.session_key, {
        "slots": {"producto": "Velas", "color": "Blanco", "aroma": "Lavanda", "cantidad": "2"},
    })

    result = json.loads(await tool.execute_with_context(ctx, items=_items("velas", 2)))  # 2 × 25.000

    body = _form_body(seeded_vault, ctx.session_key)
    assert "*2× Velas* (Blanco, Lavanda)" in body
    assert "Subtotal en productos: $50.000" in body
    assert "«Completar datos»" in body
    assert result["customer_text"] == body


@pytest.mark.asyncio
async def test_the_form_message_says_the_shipping_is_apart_without_promising_a_value(ctx, seeded_vault, tool):
    """El formulario sale antes de elegir el pago: la frase del envío vale con
    contra entrega (el resumen dice «Por confirmar») y con pago anticipado
    (tarifa mínima, no definitiva). Regla del operador 2026-09-07."""
    await tool.execute_with_context(ctx, items=_items("velas", 2))

    body = _form_body(seeded_vault, ctx.session_key)
    assert "El envío va aparte (lo calcula la transportadora)." in body
    assert "resumen" not in body.lower()


@pytest.mark.asyncio
async def test_the_form_message_follows_the_style_rules(ctx, seeded_vault, tool):
    """Tuteo, sin guion largo, máximo un emoji (de la lista del guion) y dentro
    del cuerpo de un mensaje interactivo de WhatsApp (1024)."""
    _seed_draft(seeded_vault, ctx.session_key, {
        "slots": {"producto": "Velas", "color": "Blanco", "aroma": "Lavanda", "cantidad": "2"},
    })

    await tool.execute_with_context(ctx, items=_items("velas", 2))

    body = _form_body(seeded_vault, ctx.session_key)
    assert not DASH_RE.search(body), body
    assert len(find_emojis(body)) <= 1 and not disallowed_emojis(body), body
    assert not [rx.pattern for rx in VOSEO_RES if rx.search(body)], body
    assert len(body) <= 1024
    # Limpio también para el saneador de textos del LLM (el flush ya no lo
    # pasa por ahí: lo escribe el código).
    assert sanitize_llm_text(body).text == body
    assert not looks_like_admin_leak(body)


@pytest.mark.asyncio
async def test_the_form_message_names_only_the_variants_of_the_product_it_sends(ctx, seeded_vault, tool):
    """Las variantes salen del borrador SOLO si el producto empareja: el color
    de otro producto no se le atribuye al que va en el formulario."""
    _seed_draft(seeded_vault, ctx.session_key, {"slots": {"producto": "Cubo Love", "color": "Azul"}})

    await tool.execute_with_context(ctx, items=_items("velas", 2))

    body = _form_body(seeded_vault, ctx.session_key)
    assert "2× Velas" in body
    assert "Azul" not in body


@pytest.mark.asyncio
async def test_a_product_split_in_variants_shows_each_line(ctx, seeded_vault, tool):
    """Un producto repartido en variantes (`set_order_slot(lineas=...)`): el
    mensaje dice cuántas van de cada una."""
    _seed_draft(seeded_vault, ctx.session_key, {
        "slots": {"producto": "Velas"},
        "items": [
            {"producto": "Velas", "color": "Lila", "aroma": "Lavanda", "cantidad": "1"},
            {"producto": "Velas", "color": "Azul", "aroma": "Lavanda", "cantidad": "1"},
        ],
    })

    await tool.execute_with_context(ctx, items=_items("velas", 2))

    body = _form_body(seeded_vault, ctx.session_key)
    assert "2× Velas" in body
    assert "*2× Velas* (1× Lila, Lavanda; 1× Azul, Lavanda)" in body


@pytest.mark.asyncio
async def test_without_the_flow_the_customer_text_is_the_list_the_flush_sends(ctx, seeded_vault, tool, monkeypatch):
    """Sin `META_FLOW_ID_SHIPPING` el flush pide los datos con un texto que
    enumera los campos: eso es lo que lee el cliente, y lo que leen la traza y
    la verificación ③ (`customer_text`), no el mensaje del Flow."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from src.platform.whatsapp import dtos as wa_dtos
    from src.plugins.chats.agent.sales.activities.flush_ui_intents import _dispatch_intent

    monkeypatch.delenv("META_FLOW_ID_SHIPPING", raising=False)
    result = json.loads(await tool.execute_with_context(ctx, items=_items("velas", 2)))
    params = _read_intent(seeded_vault, ctx.session_key)["params"]
    wa_client = SimpleNamespace(
        send_text=AsyncMock(return_value=SimpleNamespace(ok=True, wa_message_id="wamid.t")),
        send_flow=AsyncMock(),
    )

    await _dispatch_intent(
        wa_client=wa_client, wa_dtos=wa_dtos, kind="shipping_flow", params=params, fallback={},
        phone_number_id="phone-1", to_number="573000000000", last_inbound_message_id=None,
    )

    wa_client.send_flow.assert_not_awaited()
    sent = wa_client.send_text.await_args.args[2]
    assert result["customer_text"] == sent
    assert "Ciudad" in sent and sent != params["body"]


def test_a_long_order_keeps_the_subtotal_and_the_button_within_the_whatsapp_limit():
    """Muchos productos: se resumen los últimos («y N productos más») para que
    el subtotal y la instrucción del botón sigan en el mensaje (1024)."""
    from src.plugins.chats.agent.sales.card_messages import shipping_form_text

    lines = [
        {"handle": f"producto-{i}", "title": f"Producto de prueba número {i:02d} con nombre largo",
         "quantity": 1, "unit_price_cop": 10_000, "subtotal_cop": 10_000}
        for i in range(40)
    ]

    body = shipping_form_text(lines, [])

    assert len(body) <= 1024
    assert "Subtotal en productos: $400.000" in body
    assert "«Completar datos»" in body
    assert "productos más" in body


@pytest.mark.asyncio
async def test_the_summary_tells_the_llm_the_form_already_carries_its_message(ctx, seeded_vault, tool):
    """El formulario ya lleva su mensaje: el LLM no lo repite y solo usa
    `send_reply` si el cliente preguntó otra cosa."""
    result = json.loads(await tool.execute_with_context(ctx, items=_items("vela")))

    summary = result["summary"]
    assert "repitas" in summary
    assert "send_reply" in summary and "otra cosa" in summary


def test_the_flow_form_heading_is_in_tuteo():
    """El Flow canónico del repo saludaba en voseo («Completá tus datos»). El
    cliente lee ese título al abrir el formulario (publicarlo en Meta es del
    operador: el provisioning reusa el flow publicado con el mismo nombre)."""
    flow_json = Path(__file__).resolve().parents[4] / "docs" / "whatsapp_flows" / "shipping_v2.json"
    flow = json.loads(flow_json.read_text(encoding="utf-8"))

    children = flow["screens"][0]["layout"]["children"]
    heading = next(c["text"] for c in children if c["type"] == "TextHeading")
    assert heading == "Completa tus datos"


def test_the_description_says_the_system_writes_the_form_message():
    """La descripción hablaba de un «mensaje de texto enumerando los campos»
    (el modo previo al Flow) y no decía qué hacer con otra pregunta."""
    description = RequestShippingDetailsTool.description

    assert "enumerando los campos" not in description
    assert "send_reply" in description


@pytest.mark.asyncio
async def test_summary_instructs_llm_to_wait_not_repeat(ctx, seeded_vault, tool):
    """El summary que devuelve la tool al LLM debe dejar claro que NO pida
    los mismos datos otra vez (anti-eco) y que espere la respuesta del
    cliente. Este wording llega al prompt del LLM como tool_result."""
    result = json.loads(await tool.execute_with_context(ctx, items=_items("vela")))
    summary = result["summary"]
    # El LLM debe saber que vendrá la respuesta vía texto o nfm_reply
    assert "verify_order_for_checkout" in summary
    # Y NO re-pedir los datos
    assert "NO" in summary or "no" in summary
