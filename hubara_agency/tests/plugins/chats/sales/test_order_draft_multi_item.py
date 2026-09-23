"""El borrador del pedido lleva VARIOS productos, cada uno con sus variantes.

Dos incidentes con la misma causa (el borrador era de UN solo producto):
  * Orden #31 (2026-09-16): Duo Zodiacal + Velón. El color "verde" del Velón
    pisó el "Morado" del Duo y el signo pasó de Aries a Cáncer.
  * 2026-09-22 (ep_002): Trilogía del Terror + Duo Zodiacal. El borrador tenía
    producto=Trilogía; `set_order_slot(diseno="Escorpio")` se validó contra
    las opciones de la Trilogía (["Unico"]) y se rechazó 3 veces: el Duo nunca
    entró al pedido y el bot preguntó dos veces "¿te dejo el Duo en Escorpio?".

Contrato:
  * `items` guarda un ítem por producto (producto, aroma, color, diseno,
    cantidad); los datos del pedido (envío, pago, notas) quedan en `slots`.
  * `slots` sigue exponiendo la vista plana de siempre: con un producto es
    IDÉNTICA a la forma vieja; con varios, `producto` junta los nombres (los
    lectores que solo preguntan "¿hay producto?" no cambian).
  * Un borrador viejo (sin `items`) se lee como un solo ítem y se migra en la
    primera escritura.
"""
from __future__ import annotations

from src.plugins.chats.agent.sales.use_cases.order_draft import (
    update_order_draft,
)
from src.plugins.chats.shared.draft_items import draft_items

_NOW = 1_790_000_000_000


def _meta() -> dict:
    return {"episodes": [{"episode_id": "ep_001", "started_at_ms": _NOW}]}


def _draft(meta: dict) -> dict:
    return meta["episodes"][-1]["order_draft"]


def test_second_product_becomes_its_own_item():
    meta = _meta()
    update_order_draft(meta, slots={"producto": "Trilogía del Terror"}, now_ms=_NOW)
    update_order_draft(
        meta, slots={"producto": "Duo Zodiacal", "diseno": "Escorpio"}, now_ms=_NOW
    )

    assert draft_items(_draft(meta)) == [
        {"producto": "Trilogía del Terror"},
        {"producto": "Duo Zodiacal", "diseno": "Escorpio"},
    ]


def test_single_product_keeps_the_legacy_flat_slots():
    meta = _meta()
    update_order_draft(
        meta, slots={"producto": "Cubo Love", "color": "Blanco"}, now_ms=_NOW
    )
    update_order_draft(meta, slots={"cantidad": "2", "ciudad": "Cali"}, now_ms=_NOW)

    assert _draft(meta)["slots"] == {
        "producto": "Cubo Love",
        "color": "Blanco",
        "cantidad": "2",
        "ciudad": "Cali",
    }


def test_several_products_join_names_and_keep_variants_per_item():
    meta = _meta()
    update_order_draft(
        meta, slots={"producto": "Duo Zodiacal", "color": "Morado"}, now_ms=_NOW
    )
    update_order_draft(
        meta, slots={"producto": "Velón Amor Eterno", "color": "verde"}, now_ms=_NOW
    )

    assert _draft(meta)["slots"] == {"producto": "Duo Zodiacal + Velón Amor Eterno"}
    # Caso orden #31: el color del segundo NO pisa el del primero.
    assert [i.get("color") for i in draft_items(_draft(meta))] == ["Morado", "verde"]


def test_field_without_product_goes_to_the_item_in_progress():
    meta = _meta()
    update_order_draft(meta, slots={"producto": "Trilogía del Terror"}, now_ms=_NOW)
    update_order_draft(meta, slots={"producto": "Duo Zodiacal"}, now_ms=_NOW)
    update_order_draft(meta, slots={"cantidad": "1"}, now_ms=_NOW)

    assert draft_items(_draft(meta)) == [
        {"producto": "Trilogía del Terror"},
        {"producto": "Duo Zodiacal", "cantidad": "1"},
    ]


def test_same_product_written_differently_is_the_same_item():
    meta = _meta()
    update_order_draft(meta, slots={"producto": "Duo Zodiacal"}, now_ms=_NOW)
    update_order_draft(
        meta, slots={"producto": "dúo  zodiacal", "diseno": "Leo"}, now_ms=_NOW
    )

    assert draft_items(_draft(meta)) == [{"producto": "Duo Zodiacal", "diseno": "Leo"}]


def test_legacy_flat_draft_migrates_on_first_write():
    """Borradores vivos en el vault antes del deploy: un producto en slots."""
    meta = _meta()
    meta["episodes"][-1]["order_draft"] = {
        "slots": {"producto": "Trilogía del Terror", "cantidad": "1", "ciudad": "Bogotá"},
        "updated_at_ms": _NOW,
    }

    update_order_draft(
        meta, slots={"producto": "Duo Zodiacal", "diseno": "Escorpio"}, now_ms=_NOW
    )

    assert draft_items(_draft(meta)) == [
        {"producto": "Trilogía del Terror", "cantidad": "1"},
        {"producto": "Duo Zodiacal", "diseno": "Escorpio"},
    ]
    assert _draft(meta)["slots"] == {
        "producto": "Trilogía del Terror + Duo Zodiacal",
        "ciudad": "Bogotá",
    }


def test_legacy_flat_draft_reads_as_one_item_without_writing():
    draft = {"slots": {"producto": "Cubo Love", "aroma": "Lavanda", "ciudad": "Cali"}}

    assert draft_items(draft) == [{"producto": "Cubo Love", "aroma": "Lavanda"}]


def test_removing_a_product_drops_its_item():
    meta = _meta()
    update_order_draft(meta, slots={"producto": "Cubo Love"}, now_ms=_NOW)
    update_order_draft(meta, slots={"producto": "Cilindro Love"}, now_ms=_NOW)

    update_order_draft(
        meta, slots={"producto": "Cubo Love"}, now_ms=_NOW, remove_product=True
    )

    assert draft_items(_draft(meta)) == [{"producto": "Cilindro Love"}]
    assert _draft(meta)["slots"] == {"producto": "Cilindro Love"}


def _two_products() -> dict:
    meta = _meta()
    update_order_draft(
        meta, slots={"producto": "Trilogía del Terror", "cantidad": "1"}, now_ms=_NOW
    )
    update_order_draft(
        meta,
        slots={"producto": "Duo Zodiacal", "diseno": "Escorpio", "color": "Blanco"},
        now_ms=_NOW,
    )
    update_order_draft(meta, slots={"notas": "Plato: Leo"}, now_ms=_NOW)
    return meta


def test_projection_lists_each_product_when_there_are_several():
    from src.plugins.chats.agent.sales.use_cases.order_draft import (
        get_projectable_draft,
    )

    projection = get_projectable_draft(_two_products())

    assert projection["items"] == [
        {"producto": "Trilogía del Terror", "cantidad": "1"},
        {"producto": "Duo Zodiacal", "diseno": "Escorpio", "color": "Blanco"},
    ]
    assert projection["notas"] == "Plato: Leo"


def test_projection_of_a_single_product_is_the_legacy_shape():
    from src.plugins.chats.agent.sales.use_cases.order_draft import (
        get_projectable_draft,
    )

    meta = _meta()
    update_order_draft(meta, slots={"producto": "Cubo Love", "aroma": "Lavanda"}, now_ms=_NOW)

    assert get_projectable_draft(meta) == {"producto": "Cubo Love", "aroma": "Lavanda"}


def test_prompt_note_shows_one_line_per_product():
    from src.plugins.chats.agent.sales.use_cases.order_draft import (
        build_order_draft_note,
        get_projectable_draft,
    )

    note = build_order_draft_note(get_projectable_draft(_two_products()))

    assert note.startswith("[DATOS DEL PEDIDO YA CONFIRMADOS POR EL CLIENTE")
    assert "Productos del pedido (2):" in note
    assert "1. Trilogía del Terror · Cantidad: 1" in note
    assert "2. Duo Zodiacal · Color: Blanco · Diseño/Signo: Escorpio" in note
    assert "Notas: Plato: Leo" in note
    # La etiqueta plana "A + B" no se repite como si fuera UN producto.
    assert "Producto: Trilogía del Terror + Duo Zodiacal" not in note


def _complete(producto: str) -> dict:
    return {"producto": producto, "aroma": "Frutos rojos", "color": "Blanco", "cantidad": "1"}


def test_stage_stays_in_variants_until_every_product_has_its_choices():
    from src.plugins.chats.agent.sales.turn_trace import project_stage
    from src.plugins.chats.agent.sales.use_cases.funnel_stage import resolve_funnel_stage

    meta = _meta()
    update_order_draft(meta, slots=_complete("Trilogía del Terror"), now_ms=_NOW)
    update_order_draft(
        meta, slots={"producto": "Duo Zodiacal", "diseno": "Escorpio"}, now_ms=_NOW
    )

    assert resolve_funnel_stage(meta) == "etapa_variantes"
    assert project_stage(meta["episodes"][-1]) == "variantes"

    update_order_draft(meta, slots=_complete("Duo Zodiacal"), now_ms=_NOW)

    assert resolve_funnel_stage(meta) == "etapa_datos_envio"
    assert project_stage(meta["episodes"][-1]) == "confirmacion"


_ASKED = "Perfecto 🤍 ¿Cuántas unidades deseas?"


def test_quantity_reply_goes_to_the_product_in_progress():
    from src.plugins.chats.agent.sales.use_cases.quantity_capture import (
        capture_quantity_from_reply,
    )

    meta = _meta()
    update_order_draft(
        meta, slots={"producto": "Trilogía del Terror", "cantidad": "1"}, now_ms=_NOW
    )
    update_order_draft(meta, slots={"producto": "Duo Zodiacal"}, now_ms=_NOW)

    captured = capture_quantity_from_reply(
        meta, last_agent_text=_ASKED, inbound_text="2", now_ms=_NOW
    )

    assert captured == 2
    assert [i.get("cantidad") for i in draft_items(_draft(meta))] == ["1", "2"]


def test_quantity_reply_never_overwrites_a_product_that_has_one():
    """El ítem en curso ya tiene cantidad: el "2" podría ser de OTRO producto
    — no se adivina (lo resuelve el LLM con el contexto)."""
    from src.plugins.chats.agent.sales.use_cases.quantity_capture import (
        capture_quantity_from_reply,
    )

    meta = _meta()
    update_order_draft(meta, slots={"producto": "Duo Zodiacal"}, now_ms=_NOW)
    update_order_draft(
        meta, slots={"producto": "Trilogía del Terror", "cantidad": "1"}, now_ms=_NOW
    )

    captured = capture_quantity_from_reply(
        meta, last_agent_text=_ASKED, inbound_text="2", now_ms=_NOW
    )

    assert captured is None
    assert [i.get("cantidad") for i in draft_items(_draft(meta))] == [None, "1"]


def test_human_order_intake_sees_every_product_with_its_variants():
    """"Crear pedido" (el humano) le pasa el borrador al LLM que arma el
    pedido: con varios productos, cada uno con SUS variantes."""
    from src.plugins.chats.shared.order_intake import build_prompt, draft_slots_of

    prompt = build_prompt(
        conversation="(chat)", catalog=[], draft_slots=draft_slots_of(_two_products())
    )

    assert "Trilogía del Terror (cantidad: 1)" in prompt
    assert "Duo Zodiacal (color: Blanco, diseno: Escorpio)" in prompt


def test_turn_trace_draft_carries_every_product():
    from src.plugins.chats.agent.sales.turn_trace import draft_slots

    trace_draft = draft_slots(_two_products()["episodes"][-1])

    assert [i["producto"] for i in trace_draft["items"]] == [
        "Trilogía del Terror",
        "Duo Zodiacal",
    ]
