"""Las reglas de hoy de las capacidades nuevas del motor (fase F3: cupón,
fuera de catálogo, cantidad y mapeos), partidas en LEER y ESCRIBIR.

Para que el motor ponga a Jev (o la regla) en la LECTURA sin tocar lo que se
escribe, cada regla se parte en funciones puras; la función de siempre queda
como la composición de las partes y hace exactamente lo mismo:

* fuera de catálogo: `unavailable_terms` (los términos) + `catalog_gap_note`
  (la nota con los que quedan);
* cantidad: `read_reply_quantity` (la lee) + `apply_reply_quantity` (la
  escribe con sus compuertas);
* zona de envío: `shipping_zone` (la zona de la ciudad) +
  `is_published_rate_for_zone` (la tarifa que vale en esa zona);
* ítem del pedido: `item_for_values` (a cuál ítem va un dato sin producto);
* cupón: `coupon_talk_subject` (de qué cupón se le pregunta a Jev, o nada si
  la respuesta de hoy no lee el texto).
"""
from __future__ import annotations

import copy
from dataclasses import asdict

import pytest

from src.plugins.chats.agent.sales.config.shipping import (
    SHIPPING_RATE_BOGOTA_COP,
    SHIPPING_RATE_NATIONAL_COP,
    is_published_rate_for_zone,
    is_published_shipping_rate,
    shipping_zone,
)
from src.plugins.chats.agent.sales.use_cases.catalog_gap import (
    build_catalog_gap_note,
    catalog_gap_note,
)
from src.plugins.chats.agent.sales.use_cases.coupons import coupon_talk_subject
from src.plugins.chats.agent.sales.use_cases.order_draft import item_for_values
from src.plugins.chats.agent.sales.use_cases.quantity_capture import (
    apply_reply_quantity,
    capture_quantity_from_reply,
    quantity_slot_open,
    read_reply_quantity,
)
from src.plugins.chats.shared.product_truth import unavailable_terms
from src.sdk.catalogkit import CatalogProductDTO, CatalogVariantDTO
from src.sdk.connectorkit import PromotionDTO

_TAGS = ["Aroma: Café", "Aroma: Lavanda", "Color: Azul", "Color: Rosado", "Color: Rojo"]


def _product(title: str, handle: str, description: str = "") -> CatalogProductDTO:
    return CatalogProductDTO(
        id=f"prod_{handle}", handle=handle, title=title, status="published", description=description,
        variants=[CatalogVariantDTO(id=f"var_{handle}", title="Unico")], tags=list(_TAGS), options={"Unico": ["Unico"]},
    )


CATALOG = [
    _product("Cubo Love", "cubo-love", "La clásica vela cúbica con la palabra LOVE."),
    _product("Cilindro Love", "cilindro-love", "Vela cilíndrica con la palabra LOVE."),
    _product("Cubo de corazón", "cubo-de-corazon", "Vela de corazones entrelazados."),
]


# --- fuera de catálogo -----------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Estás y en vaso también",
        "Vivo en Cartagena, ¿la tienen en vaso?",
        "[el cliente envió una foto: vela rosa con diseño de dragón]",
        "¿El cubo de corazón viene en azul?",
        "De acuerdo, en cuanto pueda te pago con nequi",
        "",
    ],
)
def test_the_gap_note_is_the_terms_then_the_note(text: str) -> None:
    assert catalog_gap_note(unavailable_terms(text, CATALOG)) == build_catalog_gap_note(text, CATALOG)


def test_the_gap_note_names_only_the_terms_it_gets() -> None:
    note = catalog_gap_note(["vaso"]) or ""

    assert "«vaso»" in note and "NO existe" in note
    assert "cartagena" not in note
    assert catalog_gap_note([]) is None


# --- cantidad --------------------------------------------------------------

_ASKED = "¡Excelente elección! 🤍 ¿Cuántas unidades deseas?"


def _draft(slots: dict, *, order_id: str | None = None) -> dict:
    episode: dict = {"episode_id": "ep_1", "closed_at_ms": None, "order_draft": {"slots": dict(slots)}}
    if order_id:
        episode["order_id"] = order_id
    return {"episodes": [episode]}


@pytest.mark.parametrize(
    ("slots", "agent", "text"),
    [
        ({"producto": "Cubo Love"}, _ASKED, "Una que colores tienes?"),
        ({"producto": "Cubo Love"}, _ASKED, "Quiero 2"),
        ({"producto": "Cubo Love"}, "¿Qué aroma prefieres?", "una"),
        ({"producto": "Cubo Love", "cantidad": "1"}, _ASKED, "una"),
        ({"producto": "Cubo Love", "cantidad": "2"}, _ASKED, "una"),
        ({}, _ASKED, "dos"),
        ({"producto": "Cubo Love"}, _ASKED, "Una pregunta, ¿hacen envíos?"),
        ({"producto": "Cubo Love"}, None, "tres"),
    ],
)
def test_the_quantity_capture_is_read_then_apply(slots: dict, agent: str | None, text: str) -> None:
    by_capture, by_parts = _draft(slots), _draft(slots)

    got = capture_quantity_from_reply(by_capture, last_agent_text=agent, inbound_text=text, now_ms=10)
    parts = apply_reply_quantity(by_parts, read_reply_quantity(agent, text), now_ms=10)

    assert got == parts and by_capture == by_parts


def test_reading_the_quantity_writes_nothing() -> None:
    assert read_reply_quantity(_ASKED, "Una que colores tienes?") == 1
    assert read_reply_quantity("¿Qué aroma prefieres?", "una") is None
    assert read_reply_quantity(_ASKED, "Quiero 2") is None  # arranca con «quiero»: la regla no la ve


def test_the_order_is_already_registered_so_nothing_is_written() -> None:
    metadata = _draft({"producto": "Cubo Love"}, order_id="order_1")
    before = copy.deepcopy(metadata)

    assert apply_reply_quantity(metadata, 2, now_ms=10) is None
    assert metadata == before


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        (_draft({"producto": "Cubo Love"}), True),
        (_draft({"producto": "Cubo Love", "cantidad": "1"}), False),
        (_draft({}), False),
        (_draft({"producto": "Cubo Love"}, order_id="order_1"), False),
        ({}, False),
    ],
)
def test_a_quantity_slot_is_open_only_for_a_product_in_progress_without_one(metadata: dict, expected: bool) -> None:
    assert quantity_slot_open(metadata) is expected


# --- zona de envío ---------------------------------------------------------


@pytest.mark.parametrize("city", ["Bogotá", "bogota d.c.", "Chía", "Medellín", "", None])
@pytest.mark.parametrize("cop", [0, SHIPPING_RATE_BOGOTA_COP, SHIPPING_RATE_NATIONAL_COP, 12_000])
def test_the_bot_shipping_check_is_the_zone_then_the_rate(city: str | None, cop: int) -> None:
    assert is_published_shipping_rate(cop, city) == is_published_rate_for_zone(cop, shipping_zone(city))


def test_the_zone_of_today_only_knows_bogota() -> None:
    assert shipping_zone("Bogotá D.C.") == "bogota"
    assert shipping_zone("Chía") is None and shipping_zone(None) is None


def test_a_national_zone_takes_only_the_national_rate() -> None:
    """La zona «nacional» no la da la regla de hoy (no hay lista de municipios
    cercanos): solo llega si el motor la decide. Entonces vale solo la tarifa
    nacional."""
    assert is_published_rate_for_zone(SHIPPING_RATE_NATIONAL_COP, "nacional")
    assert not is_published_rate_for_zone(SHIPPING_RATE_BOGOTA_COP, "nacional")
    assert is_published_rate_for_zone(SHIPPING_RATE_BOGOTA_COP, "bogota")
    assert not is_published_rate_for_zone(SHIPPING_RATE_NATIONAL_COP, "bogota")
    assert not is_published_rate_for_zone(0, None)


# --- ítem del pedido -------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "accepts", "expected"),
    [
        (0, [True, True], 0),  # el ítem en curso lo acepta: se queda ahí
        (1, [True, True], 1),
        (0, [False, True], 1),  # «Escorpio» no es de la Trilogía: va al Duo
        (0, [False, None, True], 2),  # un producto fuera del catálogo no se mira
        (0, [False, False], 0),  # nadie lo acepta: se queda (el rechazo lo explica)
        (1, [True, False, True], 0),  # el primero que lo acepta, en orden
        (0, [None, True], 0),  # sin producto del catálogo no se valida
        (0, [False], 0),
    ],
)
def test_a_value_without_product_goes_to_the_item_in_progress_unless_another_takes_it(
    current: int, accepts: list, expected: int
) -> None:
    assert item_for_values(current, accepts) == expected


# --- cupón ------------------------------------------------------------------


def _promo(**kw) -> PromotionDTO:
    base = dict(
        id="promo_amor", code="AMOR2026", discount_type="percentage", value=10, currency_code=None,
        target_type="items", allocation="across", max_quantity=None, product_ids=("prod_cubo",), variant_ids=(),
        collection_ids=(), min_subtotal_cop=None, is_automatic=False, status="active", starts_at_ms=None,
        ends_at_ms=None, budget_type=None, budget_limit=None, budget_used=None, description="Amor y amistad",
    )
    return PromotionDTO(**{**base, **kw})


def _with_coupon(promo: PromotionDTO) -> dict:
    return {
        "episodes": [
            {
                "episode_id": "ep_1", "closed_at_ms": None,
                "applied_coupon": {
                    "code": promo.code, "promotion": asdict(promo), "applied_at_ms": 1,
                    "eligible_products": [{"handle": "cubo-love", "title": "Cubo Love"}],
                },
            }
        ]
    }


def test_the_coupon_question_names_the_coupon_and_its_products() -> None:
    assert coupon_talk_subject(_with_coupon(_promo())) == ("AMOR2026", ["Cubo Love"])


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"episodes": [{"episode_id": "ep_1", "closed_at_ms": None}]},
        _with_coupon(_promo(product_ids=())),  # todo el catálogo: siempre en juego
        _with_coupon(_promo(target_type="shipping_methods")),  # de envío: nunca en juego
    ],
    ids=["sin-metadata", "sin-cupon", "todo-el-catalogo", "de-envio"],
)
def test_there_is_nothing_to_ask_when_todays_answer_does_not_read_the_text(metadata: dict) -> None:
    assert coupon_talk_subject(metadata) is None
