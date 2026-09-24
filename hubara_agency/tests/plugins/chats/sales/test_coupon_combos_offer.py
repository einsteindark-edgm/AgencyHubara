"""El bot ofrece y confirma SOLO las combinaciones del cupón con cupo.

Conversación de prueba del 2026-09-24 (campaña con AMOR2026; cupo del Cubo
Love: Lila · Lavanda, Azul · Caballero de la noche, Amarillo · Café, …): el
bot mostró los 11 aromas y los 9 colores del catálogo, el cliente eligió
Sándalo · Amarillo (fuera del cupón) y a "¿Sí está disponible con ese aroma
y color?" respondió "Sí, están disponibles" sin decir que iba a precio
normal.

Contrato:
- la nota de cada turno agrupa las combinaciones con descuento por producto;
- si el borrador tiene una combinación (o un aroma/color) sin descuento, la
  nota lo dice con el precio normal para que el bot se lo diga al cliente;
- el selector de aromas/colores pone primero las combinaciones del cupón.
"""
from __future__ import annotations

from dataclasses import asdict

from src.platform.promotions.port import PromotionDTO
from src.plugins.chats.agent.sales.use_cases.coupon_quota import as_eligible
from src.plugins.chats.agent.sales.use_cases.coupons import build_coupon_note


def _unit(handle: str, title: str, color: str, aroma: str, price: int) -> dict:
    return {"handle": handle, "title": title, "color": color, "aroma": aroma,
            "units_left": 1, "price_cop": price, "discounted_price_cop": price * 9 // 10}


UNITS = [
    _unit("cubo-love", "Cubo Love", "Lila", "Lavanda", 21000),
    _unit("cubo-love", "Cubo Love", "Azul", "Caballero de la noche", 21000),
    _unit("cubo-love", "Cubo Love", "Amarillo", "Café", 21000),
    _unit("cubo-love", "Cubo Love", "Rosado", "Caballero de la noche", 21000),
    _unit("cubo-de-corazon", "Cubo de corazón", "Rosado", "Sándalo", 22000),
]


def _promo() -> PromotionDTO:
    return PromotionDTO(
        id="promo_amor2026", code="AMOR2026", discount_type="percentage", value=10,
        currency_code=None, target_type="items", allocation="across", max_quantity=None,
        product_ids=("prod_cubo", "prod_corazon"), variant_ids=(), collection_ids=(),
        min_subtotal_cop=None, is_automatic=False, status="active", starts_at_ms=None,
        ends_at_ms=None, budget_type=None, budget_limit=None, budget_used=None,
        description="Amor y amistad",
    )


def _metadata(item: dict | None = None) -> dict:
    episode: dict = {
        "episode_id": "ep_008",
        "started_at_ms": 1,
        "closed_at_ms": None,
        "applied_coupon": {
            "code": "AMOR2026",
            "promotion": asdict(_promo()),
            "applied_at_ms": 1,
            "eligible_products": as_eligible(tuple(UNITS)),
            "quota": True,
            "units": UNITS,
        },
    }
    if item is not None:
        episode["order_draft"] = {"slots": dict(item), "items": [dict(item)]}
    return {"episodes": [episode]}


# --- Nota de cada turno -------------------------------------------------------


def test_turn_note_groups_the_coupon_combinations_by_product() -> None:
    note = build_coupon_note(_metadata()) or ""

    assert (
        "Cubo Love ($21.000 → $18.900): Lila · Lavanda (queda 1), Azul · Caballero de la "
        "noche (queda 1), Amarillo · Café (queda 1), Rosado · Caballero de la noche (queda 1)"
    ) in note
    assert "Cubo de corazón ($22.000 → $19.800): Rosado · Sándalo (queda 1)" in note
    assert "según disponibilidad" in note
    assert "precio normal" in note


def test_turn_note_warns_when_the_order_has_a_combination_outside_the_coupon() -> None:
    note = build_coupon_note(
        _metadata({"producto": "Cubo Love", "aroma": "Sándalo", "color": "Amarillo", "cantidad": "1"})
    ) or ""

    assert "Cubo Love Amarillo · Sándalo" in note
    assert "NO tiene el descuento" in note
    assert "precio normal ($21.000)" in note


def test_turn_note_warns_as_soon_as_the_chosen_aroma_has_no_coupon_combination() -> None:
    note = build_coupon_note(_metadata({"producto": "Cubo Love", "aroma": "Sándalo"})) or ""

    assert "Cubo Love Sándalo NO tiene el descuento" in note


def test_turn_note_narrows_the_colors_once_the_aroma_is_chosen() -> None:
    note = build_coupon_note(
        _metadata({"producto": "cubo love", "aroma": "caballero de la noche"})
    ) or ""

    assert "el descuento va SOLO en los colores: Azul, Rosado" in note
    assert "NO tiene el descuento" not in note


def test_turn_note_confirms_a_combination_that_has_the_discount() -> None:
    note = build_coupon_note(
        _metadata({"producto": "cubo love", "aroma": "cafe", "color": "amarillo"})
    ) or ""

    assert "lleva el descuento" in note
    assert "NO tiene el descuento" not in note


def test_products_outside_the_coupon_get_no_warning() -> None:
    note = build_coupon_note(_metadata({"producto": "Vela Buda", "aroma": "Sándalo"})) or ""

    assert "Vela Buda" not in note


# --- Selector de aromas / colores ---------------------------------------------

import json  # noqa: E402

import pytest  # noqa: E402
from exoclaw.agent.tools import ToolContext  # noqa: E402

from src.platform.state import FilesystemMetadataStore  # noqa: E402
from src.plugins.chats.agent.sales.activities.flush_ui_intents import (  # noqa: E402
    _render_variant_picker_text,
)
from src.plugins.chats.agent.sales.tools.ui_intents import PresentVariantPickerTool  # noqa: E402

KEY = "wa_test_coupon_combos"
AROMAS = ["Lavanda", "Verde menta", "Café", "Sándalo", "Caballero de la noche", "Drakar"]
COLORS = ["Blanco", "Rosado", "Lila", "Amarillo", "Azul", "Morado"]


def _picker(tmp_path, item: dict | None) -> tuple[PresentVariantPickerTool, FilesystemMetadataStore]:
    vault = tmp_path / "isolated_vault"
    store = FilesystemMetadataStore(vault)
    store.write(KEY, _metadata(item))
    return PresentVariantPickerTool(workspace=str(vault), metadata_store=store), store


async def _show(tool, store, variant_type: str, labels: list[str], handle: str = "cubo-love"):
    ctx = ToolContext(session_key=KEY, channel="whatsapp", chat_id=KEY)
    out = json.loads(await tool.execute_with_context(
        ctx, variant_type=variant_type, options=[{"label": x} for x in labels],
        intro_text="¿Qué aroma te gustaría?", handle=handle,
    ))
    [intent] = store.read(KEY)["pending_ui_intents"]
    return out, _render_variant_picker_text(intent["params"]) or ""


@pytest.mark.asyncio
async def test_aroma_picker_puts_the_coupon_combinations_first(tmp_path) -> None:
    tool, store = _picker(tmp_path, {"producto": "Cubo Love"})

    out, text = await _show(tool, store, "scent", AROMAS)

    assert "Con tu cupón AMOR2026 (10% menos)" in text
    assert "Lila · Lavanda — $18.900" in text
    assert "Amarillo · Café — $18.900" in text
    assert "Otros aromas, a precio normal ($21.000)" in text
    # Las combinaciones del cupón van ANTES que la lista completa.
    assert text.index("Lila · Lavanda — $18.900") < text.index("Sándalo")
    assert "AMOR2026" in out["summary"] and "precio normal" in out["summary"]


@pytest.mark.asyncio
async def test_color_picker_after_an_aroma_with_coupon_shows_only_its_colors(tmp_path) -> None:
    tool, store = _picker(tmp_path, {"producto": "Cubo Love", "aroma": "Caballero de la noche"})

    _, text = await _show(tool, store, "color", COLORS)

    assert "Con tu cupón AMOR2026 en Caballero de la noche" in text
    assert "Azul — $18.900" in text and "Rosado — $18.900" in text
    assert "Lila — $18.900" not in text
    assert "Otros colores, a precio normal ($21.000)" in text


@pytest.mark.asyncio
async def test_color_picker_after_an_aroma_without_coupon_says_it_goes_at_normal_price(tmp_path) -> None:
    tool, store = _picker(tmp_path, {"producto": "Cubo Love", "aroma": "Sándalo"})

    out, text = await _show(tool, store, "color", COLORS)

    assert "Sándalo" in text and "no tiene el descuento de AMOR2026" in text
    assert "precio normal ($21.000)" in text
    # …y le dice en qué combinaciones sí hay descuento, para que pueda cambiar.
    assert "Con el descuento ($18.900) están: Lila · Lavanda, Azul · Caballero de la noche" in text
    assert "no tiene el descuento" in out["summary"]


@pytest.mark.asyncio
async def test_picker_of_a_product_without_coupon_combinations_is_unchanged(tmp_path) -> None:
    tool, store = _picker(tmp_path, {"producto": "Vela Buda"})

    out, text = await _show(tool, store, "scent", AROMAS, handle="vela-buda")

    assert "cupón" not in text and "precio normal" not in text
    assert "AMOR2026" not in out["summary"]


def test_sales_worker_gives_the_variant_picker_the_session_metadata(tmp_path, monkeypatch) -> None:
    """Sin el metadata de la sesión el selector no ve el cupón aplicado."""
    monkeypatch.setenv("MEDUSA_BASE_URL", "http://medusa.test")
    monkeypatch.setenv("MEDUSA_ADMIN_TOKEN", "dummy")
    import src.plugins.chats.workers.sales  # noqa: F401  (registra las tools)
    from src.platform.tool_extensions import _EXTENSIONS  # type: ignore

    tool = dict(_EXTENSIONS)["sales.present_variant_picker"](tmp_path)
    assert tool._metadata_store is not None


# --- set_order_slot: el aviso llega en el MISMO turno --------------------------
#
# La nota de cada turno se arma cuando llega el mensaje: si el cliente dice de
# una vez "lo quiero en Sándalo y amarillo", la nota recién lo vería el turno
# siguiente. La tool que guarda la elección lo dice en su respuesta.


@pytest.mark.asyncio
async def test_set_order_slot_says_right_away_when_the_choice_has_no_coupon(tmp_path) -> None:
    from src.plugins.chats.agent.sales.tools.order_draft import SetOrderSlotTool

    vault = tmp_path / "isolated_vault"
    FilesystemMetadataStore(vault).write(KEY, _metadata({"producto": "Cubo Love"}))
    tool = SetOrderSlotTool(workspace=str(vault), vault_dir=vault)
    ctx = ToolContext(session_key=KEY, channel="whatsapp", chat_id=KEY)

    out = json.loads(await tool.execute_with_context(
        ctx, producto="Cubo Love", aroma="Sándalo", color="Amarillo",
    ))

    assert "Cubo Love Amarillo · Sándalo" in out["summary"]
    assert "NO tiene el descuento" in out["summary"]
    assert "precio normal ($21.000)" in out["summary"]


@pytest.mark.asyncio
async def test_set_order_slot_without_a_quota_coupon_says_nothing_about_coupons(tmp_path) -> None:
    from src.plugins.chats.agent.sales.tools.order_draft import SetOrderSlotTool

    vault = tmp_path / "isolated_vault"
    FilesystemMetadataStore(vault).write(KEY, {"episodes": [{"episode_id": "ep_1", "closed_at_ms": None}]})
    tool = SetOrderSlotTool(workspace=str(vault), vault_dir=vault)
    ctx = ToolContext(session_key=KEY, channel="whatsapp", chat_id=KEY)

    out = json.loads(await tool.execute_with_context(ctx, producto="Cubo Love", aroma="Sándalo"))

    assert "cupón" not in out["summary"] and "descuento" not in out["summary"]


# --- Cantidad contra lo que queda del cupo ------------------------------------
#
# Prueba en vivo 2026-09-24 (15:59 Bogotá): el cliente pidió 2 Cilindro Love
# Azul · Lavanda, el cupo de esa combinación es 1, y a "¿Si tienes 2 de esa?"
# el bot contestó "Sí, claro". La nota decía "esa combinación lleva el
# descuento" sin mirar cuántas quedaban.


def test_turn_note_says_how_many_carry_the_discount_when_the_order_wants_more() -> None:
    note = build_coupon_note(
        _metadata({"producto": "Cubo Love", "color": "Amarillo", "aroma": "Café", "cantidad": "2"})
    ) or ""

    assert "queda 1 con el descuento" in note
    assert "1 a $18.900 y 1 a precio normal ($21.000)" in note
    assert "lleva el descuento" not in note


def test_turn_note_confirms_the_discount_when_there_are_enough_units() -> None:
    note = build_coupon_note(
        _metadata({"producto": "Cubo Love", "color": "Amarillo", "aroma": "Café", "cantidad": "1"})
    ) or ""

    assert "El pedido tiene 1 de Cubo Love Amarillo · Café: lleva el descuento" in note
    assert "OJO" not in note


def test_turn_note_lists_how_many_are_left_when_the_coupon_allows_it() -> None:
    note = build_coupon_note(_metadata()) or ""

    assert "Lila · Lavanda (queda 1)" in note


def test_turn_note_hides_the_count_when_the_coupon_does_not_allow_it() -> None:
    metadata = _metadata({"producto": "Cubo Love", "color": "Amarillo", "aroma": "Café", "cantidad": "2"})
    metadata["episodes"][0]["applied_coupon"]["show_units_left"] = False

    note = build_coupon_note(metadata) or ""

    assert "(queda" not in note and "queda 1" not in note
    assert "1 a $18.900 y 1 a precio normal ($21.000)" in note


@pytest.mark.asyncio
async def test_set_order_slot_warns_in_the_same_turn_when_the_quantity_exceeds_the_units(tmp_path) -> None:
    from src.plugins.chats.agent.sales.tools.order_draft import SetOrderSlotTool

    vault = tmp_path / "isolated_vault"
    FilesystemMetadataStore(vault).write(
        KEY, _metadata({"producto": "Cubo Love", "color": "Amarillo", "aroma": "Café"})
    )
    tool = SetOrderSlotTool(workspace=str(vault), vault_dir=vault)
    ctx = ToolContext(session_key=KEY, channel="whatsapp", chat_id=KEY)

    out = json.loads(await tool.execute_with_context(ctx, producto="Cubo Love", cantidad="2"))

    assert "queda 1 con el descuento" in out["summary"]
    assert "1 a precio normal ($21.000)" in out["summary"]


@pytest.mark.asyncio
async def test_picker_rows_say_how_many_are_left_when_the_coupon_allows_it(tmp_path) -> None:
    tool, store = _picker(tmp_path, {"producto": "Cubo Love"})

    _, text = await _show(tool, store, "scent", AROMAS)

    assert "Lila · Lavanda — $18.900 (queda 1)" in text


def test_turn_note_says_when_the_chosen_combination_sold_out() -> None:
    metadata = _metadata({"producto": "Cubo Love", "color": "Lila", "aroma": "Lavanda", "cantidad": "1"})
    applied = metadata["episodes"][0]["applied_coupon"]
    applied["sold_out"] = [dict(applied["units"][0], units_left=0)]
    applied["units"] = applied["units"][1:]

    note = build_coupon_note(metadata) or ""

    assert "de Cubo Love Lila · Lavanda ya no quedan unidades con el descuento" in note
    assert "precio normal ($21.000)" in note
    assert "Lila · Lavanda (queda" not in note


def test_turn_note_when_every_unit_sold_out() -> None:
    metadata = _metadata()
    applied = metadata["episodes"][0]["applied_coupon"]
    applied.update(units=[], exhausted=True)

    note = build_coupon_note(metadata) or ""

    assert "ya no quedan unidades con descuento" in note
    assert "precio normal" in note
    assert "Lila · Lavanda" not in note
