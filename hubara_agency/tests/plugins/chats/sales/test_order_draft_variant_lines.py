"""Un mismo producto en varias variantes, cada una con SU cantidad.

Laboratorio, caso 4567 (turno 22, producción y los dos bots): el cliente
contestó «Mejor 2, una lila y otra azul» y el bot guardó `cantidad=2`,
`color=Lila` y `notas="Segunda unidad en azul"`. La confirmación salió con
`{velon-gorrion, Lila, Lavanda, quantity 2}`: el cliente confirmó dos lilas y
la orden iba a registrar dos lilas. El borrador identificaba cada ítem solo
por producto, así que no podía guardar «1 lila + 1 azul».

Contrato:
  * `set_product_lines` reparte el producto en una línea por variante, cada
    una con su cantidad. Lo que no cambia entre líneas (el aroma) se conserva
    de lo que ya tenía el producto. Las líneas iguales se juntan.
  * Escribir sobre un producto repartido sin líneas: lo que comparten todas
    sus líneas se cambia en todas; lo que las distingue y la cantidad no se
    tocan (lo dicen las líneas).
  * La nota del turno muestra cada línea y pide una línea por cada una en la
    confirmación y el registro.
"""
from __future__ import annotations

from src.plugins.chats.agent.sales.use_cases.order_draft import (
    build_order_draft_note,
    get_projectable_draft,
    line_conflicts,
    set_product_lines,
    update_order_draft,
)
from src.plugins.chats.shared.draft_items import draft_items

_NOW = 1_790_000_000_000
GORRION = "Velón Gorrión"


def _meta() -> dict:
    return {"episodes": [{"episode_id": "ep_001", "started_at_ms": _NOW}]}


def _draft(meta: dict) -> dict:
    return meta["episodes"][-1]["order_draft"]


def _gorrion_lila_x2() -> dict:
    """El borrador del turno 22 antes de repartir: dos lilas en Lavanda."""
    meta = _meta()
    update_order_draft(
        meta,
        slots={"producto": GORRION, "aroma": "Lavanda", "color": "Lila", "cantidad": "2"},
        now_ms=_NOW,
    )
    return meta


def _split(meta: dict, *lines: dict) -> dict:
    set_product_lines(meta, producto=GORRION, lines=list(lines), now_ms=_NOW)
    return meta


def test_one_line_per_variant_with_its_own_quantity():
    meta = _split(
        _gorrion_lila_x2(),
        {"color": "Lila", "cantidad": "1"},
        {"color": "Azul", "cantidad": "1"},
    )

    assert draft_items(_draft(meta)) == [
        {"producto": GORRION, "aroma": "Lavanda", "color": "Lila", "cantidad": "1"},
        {"producto": GORRION, "aroma": "Lavanda", "color": "Azul", "cantidad": "1"},
    ]


def test_the_flat_view_names_the_product_once():
    meta = _split(
        _gorrion_lila_x2(),
        {"color": "Lila", "cantidad": "1"},
        {"color": "Azul", "cantidad": "1"},
    )

    assert _draft(meta)["slots"] == {"producto": GORRION}


def test_equal_lines_are_joined_and_one_line_is_a_plain_item():
    meta = _split(
        _gorrion_lila_x2(),
        {"color": "Lila", "cantidad": "1"},
        {"color": "lila", "cantidad": "2"},
    )

    assert draft_items(_draft(meta)) == [
        {"producto": GORRION, "aroma": "Lavanda", "color": "Lila", "cantidad": "3"}
    ]
    assert _draft(meta)["slots"]["cantidad"] == "3"


def test_other_products_keep_their_place():
    meta = _meta()
    update_order_draft(meta, slots={"producto": GORRION, "aroma": "Lavanda"}, now_ms=_NOW)
    update_order_draft(meta, slots={"producto": "Cubo Love", "cantidad": "1"}, now_ms=_NOW)

    _split(meta, {"color": "Lila", "cantidad": "2"}, {"color": "Azul", "cantidad": "1"})

    assert [(i["producto"], i.get("color"), i.get("cantidad")) for i in draft_items(_draft(meta))] == [
        (GORRION, "Lila", "2"),
        (GORRION, "Azul", "1"),
        ("Cubo Love", None, "1"),
    ]
    assert _draft(meta)["slots"]["producto"] == f"{GORRION} + Cubo Love"


def test_a_new_product_can_arrive_already_split():
    meta = _meta()

    _split(meta, {"color": "Lila", "cantidad": "1"}, {"color": "Azul", "cantidad": "1"})

    assert [i.get("color") for i in draft_items(_draft(meta))] == ["Lila", "Azul"]


def test_the_note_shows_each_line_and_asks_for_one_line_each():
    meta = _split(
        _gorrion_lila_x2(),
        {"color": "Lila", "cantidad": "1"},
        {"color": "Azul", "cantidad": "1"},
    )

    note = build_order_draft_note(get_projectable_draft(meta))

    assert "Líneas del pedido (2):" in note
    assert f"1. {GORRION} · Aroma: Lavanda · Color: Lila · Cantidad: 1" in note
    assert f"2. {GORRION} · Aroma: Lavanda · Color: Azul · Cantidad: 1" in note
    assert f"{GORRION} va en 2 líneas" in note
    assert "una línea por cada una" in note


def test_what_all_lines_share_changes_in_all_of_them():
    meta = _split(
        _gorrion_lila_x2(),
        {"color": "Lila", "cantidad": "1"},
        {"color": "Azul", "cantidad": "1"},
    )

    update_order_draft(meta, slots={"producto": GORRION, "aroma": "Limoncillo"}, now_ms=_NOW)

    assert [i["aroma"] for i in draft_items(_draft(meta))] == ["Limoncillo", "Limoncillo"]


def test_what_tells_the_lines_apart_and_the_quantity_are_not_touched():
    meta = _split(
        _gorrion_lila_x2(),
        {"color": "Lila", "cantidad": "1"},
        {"color": "Azul", "cantidad": "1"},
    )
    before = draft_items(_draft(meta))

    update_order_draft(meta, slots={"producto": GORRION, "color": "Rosado", "cantidad": "4"}, now_ms=_NOW)
    update_order_draft(meta, slots={"cantidad": "3"}, now_ms=_NOW)
    # La cantidad que ya suman no se copia en cada línea (serían 2 + 2).
    update_order_draft(meta, slots={"producto": GORRION, "cantidad": "2"}, now_ms=_NOW)

    assert draft_items(_draft(meta)) == before


def test_line_conflicts_names_what_needs_the_lines():
    meta = _split(
        _gorrion_lila_x2(),
        {"color": "Lila", "cantidad": "1"},
        {"color": "Azul", "cantidad": "1"},
    )
    items = draft_items(_draft(meta))

    assert line_conflicts(items, GORRION, {"color": "Rosado", "cantidad": "4", "aroma": "Limoncillo"}) == [
        "color",
        "cantidad",
    ]
    # La cantidad total que ya suman las líneas no contradice nada.
    assert line_conflicts(items, GORRION, {"cantidad": "2"}) == []
    # Un producto de una sola línea no tiene conflictos.
    assert line_conflicts(draft_items(_draft(_gorrion_lila_x2())), GORRION, {"color": "Azul"}) == []


def test_removing_a_split_product_drops_all_its_lines():
    meta = _split(
        _gorrion_lila_x2(),
        {"color": "Lila", "cantidad": "1"},
        {"color": "Azul", "cantidad": "1"},
    )
    update_order_draft(meta, slots={"producto": "Cubo Love"}, now_ms=_NOW)

    update_order_draft(meta, slots={"producto": GORRION}, now_ms=_NOW, remove_product=True)

    assert draft_items(_draft(meta)) == [{"producto": "Cubo Love"}]


def test_quantity_capture_never_writes_into_a_split_product():
    from src.plugins.chats.agent.sales.use_cases.quantity_capture import (
        apply_reply_quantity,
        quantity_slot_open,
    )

    meta = _split(
        _gorrion_lila_x2(),
        {"color": "Lila", "cantidad": "1"},
        {"color": "Azul", "cantidad": "1"},
    )

    assert quantity_slot_open(meta) is False
    assert apply_reply_quantity(meta, 5, now_ms=_NOW) is None
    assert [i["cantidad"] for i in draft_items(_draft(meta))] == ["1", "1"]


def test_split_lines_mismatch_names_the_lines_the_order_does_not_keep():
    from src.plugins.chats.agent.sales.use_cases.order_draft import split_lines_mismatch

    meta = _split(
        _gorrion_lila_x2(),
        {"color": "Lila", "cantidad": "1"},
        {"color": "Azul", "cantidad": "1"},
    )

    assert split_lines_mismatch(meta, [(GORRION, 2, "Lila", "Lavanda")]) == [
        f"{GORRION}: 1× Lila · Lavanda y 1× Azul · Lavanda"
    ]
    assert split_lines_mismatch(meta, [(GORRION, 1, "azul", "lavanda"), (GORRION, 1, "Lila", "Lavanda")]) == []


def test_split_lines_mismatch_degrades_open_without_the_product_in_the_order():
    """Catálogo caído: la línea se nombra por su handle y no se reconoce el
    producto; no se bloquea la confirmación por infraestructura."""
    from src.plugins.chats.agent.sales.use_cases.order_draft import split_lines_mismatch

    meta = _split(
        _gorrion_lila_x2(),
        {"color": "Lila", "cantidad": "1"},
        {"color": "Azul", "cantidad": "1"},
    )

    assert split_lines_mismatch(meta, [("velon-gorrion", 2, None, None)]) == []
