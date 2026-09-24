"""Matching de variantes para `duo-zodiacal` (verificado en prod 2026-09-17).

`duo-zodiacal` es el único producto con 2+ variantes: una sola opción
"Signo" con 12 valores. Aromas y colores son TAGS del producto
("Aroma: Limoncillo", "Color: Amarillo"), no variantes. El LLM manda labels
compuestos ("Aries, Limoncillo", "Café, Sándalo · Leo", "Capricornio morado,
Capricornio verde, Sagitario azul") y el matching viejo (substring en una
sola dirección) caía al fallback `variants[0]` → órdenes con signo equivocado.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.platform.medusa.client import HttpMedusaClient
from src.platform.medusa.service import MedusaProductService
from src.platform.orders.medusa_order import MedusaOrderRegistration
from src.platform.orders.port import DiscountedUnits, OrderItem

from .test_medusa_order_registration import _BASE_URL, _settings

_SIGNS = [
    "Acuario", "Aries", "Cáncer", "Capricornio", "Escorpio", "Géminis",
    "Leo", "Libra", "Piscis", "Sagitario", "Tauro", "Virgo",
]
_TAGS = [
    "Aroma: Limoncillo", "Aroma: Sándalo", "Aroma: Coco cremoso",
    "Color: Amarillo", "Color: Café", "Color: Morado", "Color: Verde",
    "Color: Azul",
]


def _duo_zodiacal() -> SimpleNamespace:
    variants = [
        SimpleNamespace(
            id=f"var_{sign.lower()}",
            title=sign,
            sku=f"DZ-{sign[:3].upper()}",
            options=[SimpleNamespace(id=f"ov_{i}", value=sign)],
        )
        for i, sign in enumerate(_SIGNS)
    ]
    return SimpleNamespace(
        id="prod_dz",
        title="Duo Zodiacal",
        handle="duo-zodiacal",
        variants=variants,
        tags=[SimpleNamespace(id=f"tag_{i}", value=v) for i, v in enumerate(_TAGS)],
    )


class _FakeProducts:
    def __init__(self, product: SimpleNamespace) -> None:
        self._product = product

    async def list(self, *, handle: str, limit: int = 1) -> SimpleNamespace:
        return SimpleNamespace(products=[self._product])


@pytest.fixture
def adapter() -> MedusaOrderRegistration:
    client = HttpMedusaClient(base_url=_BASE_URL, admin_token="sk_test", timeout=5.0)
    ad = MedusaOrderRegistration(client, MedusaProductService(client), _settings())
    ad._products = _FakeProducts(_duo_zodiacal())  # type: ignore[assignment]
    return ad


# ---------------------------------------------------------------------------
# 1) Matching por tokens, bidireccional, sin acentos
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "expected_title"),
    [
        ("Capricornio morado", "Capricornio"),
        ("Café, Sándalo · Leo", "Leo"),
        ("Aries, Limoncillo", "Aries"),
        ("cancer", "Cáncer"),
    ],
)
def test_sign_found_inside_compound_label(adapter, label, expected_title):
    chosen, _ = adapter._pick_variant_with_status(_duo_zodiacal(), label)
    assert chosen.title == expected_title


# ---------------------------------------------------------------------------
# 2) "·" es separador, y los separadores se combinan dentro del mismo label
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Café, Sándalo · Leo", ["Café", "Sándalo", "Leo"]),
        ("Sándalo·Leo", ["Sándalo", "Leo"]),
        ("Lavanda / Blanco - Leo", ["Lavanda", "Blanco", "Leo"]),
    ],
)
def test_split_handles_middle_dot_and_mixed_separators(label, expected):
    from src.platform.orders.medusa_order import _split_variant_label

    assert _split_variant_label(label) == expected


# ---------------------------------------------------------------------------
# 3) Signo resuelto + tokens sobrantes (tags) → NO es mismatch
# ---------------------------------------------------------------------------


async def _resolve(adapter, label: str, quantity: int = 1):
    return await adapter._resolve_items(
        [OrderItem(handle="duo-zodiacal", quantity=quantity,
                   unit_price_cop=45000, variant_label=label)]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "variant_id", "unresolved", "tag_kinds"),
    [
        ("Aries, Limoncillo", "var_aries", ["Limoncillo"], ["aroma"]),
        ("Café, Sándalo · Leo", "var_leo", ["Café", "Sándalo"], ["color", "aroma"]),
        ("Capricornio morado", "var_capricornio", ["morado"], ["color"]),
        ("Géminis, Coco cremoso, Azul", "var_géminis", ["Coco cremoso", "Azul"],
         ["aroma", "color"]),
        ("Tauro, Brillante", "var_tauro", ["Brillante"], []),
    ],
)
async def test_resolved_sign_with_leftover_tokens_is_partial_not_mismatch(
    adapter, label, variant_id, unresolved, tag_kinds
):
    resolved, mismatches = await _resolve(adapter, label)

    assert [r["variant_id"] for r in resolved] == [variant_id]
    meta = resolved[0]["metadata"]
    assert "variant_label_mismatch" not in meta
    assert meta["variant_match_kind"] == "partial"
    assert meta["variant_unresolved_tokens"] == unresolved
    assert meta["variant_unresolved_tag_kinds"] == tag_kinds
    assert mismatches == []


@pytest.mark.asyncio
async def test_exact_sign_has_no_match_annotations(adapter):
    resolved, mismatches = await _resolve(adapter, "Leo")

    meta = resolved[0]["metadata"]
    assert resolved[0]["variant_id"] == "var_leo"
    assert meta == {"handle": "duo-zodiacal", "variant_label": "Leo"}
    assert mismatches == []


# ---------------------------------------------------------------------------
# 4) Varios signos en un label → una línea por variante si la cantidad cuadra
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "quantity", "expected_lines"),
    [
        ("Capricornio morado, Capricornio verde, Sagitario azul", 3,
         [("var_capricornio", 2), ("var_sagitario", 1)]),
        ("Leo y Aries", 2, [("var_leo", 1), ("var_aries", 1)]),
    ],
)
async def test_multi_sign_label_splits_into_one_line_per_variant(
    adapter, label, quantity, expected_lines
):
    resolved, mismatches = await _resolve(adapter, label, quantity)

    assert [(r["variant_id"], r["quantity"]) for r in resolved] == expected_lines
    assert all(r["unit_price"] == 45000 for r in resolved)
    for r in resolved:
        assert "variant_label_mismatch" not in r["metadata"]
        assert r["metadata"]["variant_label"] == label
        assert r["metadata"]["variant_split_from_quantity"] == quantity
    assert mismatches == []


@pytest.mark.asyncio
async def test_coupon_units_cross_variant_lines_in_order(adapter):
    """Cupón sobre un label de varios signos (L-26): las unidades con descuento
    se consumen en el orden de las líneas de variante y pasan de una a otra;
    las que sobran van a precio de lista. La cantidad total se conserva."""
    resolved, _ = await adapter._resolve_items(
        [
            OrderItem(
                handle="duo-zodiacal",
                quantity=3,
                unit_price_cop=45000,
                variant_label="Sagitario azul, Capricornio morado, Capricornio verde",
                discounted_units=(DiscountedUnits(units=2, discount_unit_cop=4500),),
            )
        ],
        coupon_code="AMOR26",
    )

    assert [
        (r["variant_id"], r["quantity"], r["unit_price"], r["metadata"].get("coupon_code"))
        for r in resolved
    ] == [
        ("var_sagitario", 1, 40500, "AMOR26"),
        ("var_capricornio", 1, 40500, "AMOR26"),
        ("var_capricornio", 1, 45000, None),
    ]


@pytest.mark.asyncio
async def test_multi_sign_label_that_does_not_add_up_is_a_mismatch(adapter):
    resolved, mismatches = await _resolve(adapter, "Aries, Leo", 3)

    assert len(resolved) == 1
    assert resolved[0]["quantity"] == 3
    meta = resolved[0]["metadata"]
    assert meta["variant_label_mismatch"] is True
    assert meta["variant_match_kind"] == "multi_variant_unresolved"
    assert [m["reason"] for m in mismatches] == ["multi_variant_unresolved"]


# ---------------------------------------------------------------------------
# 5) Fallback real → mismatch fuerte con clase explícita
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_label_without_any_sign_falls_back_with_explicit_kind(adapter):
    resolved, mismatches = await _resolve(adapter, "Limoncillo, Amarillo")

    meta = resolved[0]["metadata"]
    assert resolved[0]["variant_id"] == "var_acuario"  # variants[0]
    assert meta["variant_label_mismatch"] is True
    assert meta["variant_match_kind"] == "fallback_first_variant"
    assert meta["variant_unresolved_tokens"] == ["Limoncillo", "Amarillo"]
    assert meta["variant_unresolved_tag_kinds"] == ["aroma", "color"]
    assert mismatches == [{
        "handle": "duo-zodiacal",
        "requested_label": "Limoncillo, Amarillo",
        "selected_variant_id": "var_acuario",
        "selected_variant_title": "Acuario",
        "reason": "fallback_first_variant",
    }]


# ---------------------------------------------------------------------------
# 6) Regresión — labels reales de las órdenes #20, #21, #23, #30, #31
#    (antes: fallback a la primera variante o falsa alarma de mismatch)
# ---------------------------------------------------------------------------

_PROD_LABELS = [
    # (label, quantity, líneas esperadas, clase esperada)
    ("Aries, Limoncillo", 1, [("var_aries", 1)], "partial"),
    ("Café, Sándalo · Leo", 1, [("var_leo", 1)], "partial"),
    ("Capricornio morado, Capricornio verde, Sagitario azul", 3,
     [("var_capricornio", 2), ("var_sagitario", 1)], "partial"),
    ("Géminis, Coco cremoso, Azul", 1, [("var_géminis", 1)], "partial"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("label", "quantity", "lines", "kind"), _PROD_LABELS)
async def test_prod_labels_resolve_to_the_requested_sign(
    adapter, label, quantity, lines, kind
):
    resolved, mismatches = await _resolve(adapter, label, quantity)

    assert [(r["variant_id"], r["quantity"]) for r in resolved] == lines
    assert sum(r["quantity"] for r in resolved) == quantity
    assert {r["metadata"]["variant_match_kind"] for r in resolved} == {kind}
    assert not any(r["metadata"].get("variant_label_mismatch") for r in resolved)
    assert mismatches == []
