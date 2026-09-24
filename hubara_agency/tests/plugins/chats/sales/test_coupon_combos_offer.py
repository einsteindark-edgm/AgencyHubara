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
        "Cubo Love ($21.000 → $18.900): Lila · Lavanda, Azul · Caballero de la noche, "
        "Amarillo · Café, Rosado · Caballero de la noche"
    ) in note
    assert "Cubo de corazón ($22.000 → $19.800): Rosado · Sándalo" in note
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
