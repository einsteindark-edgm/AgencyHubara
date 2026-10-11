"""El pedido que arma el operador con el bot apagado (`chats/shared/operator_order`).

Con un humano al mando el bot no corre turnos y nadie escribe el borrador del
pedido. Caso 2026-10-09 (App Operador): el episodio tenía el borrador vacío,
«Pedir datos de envío» y «Resumen para confirmar» respondían «faltan datos
para esta acción» y las burbujas nunca ofrecieron los colores. El lector
completa el borrador, sin escribirlo, con lo que pasó con el humano al mando:
el producto que eligió el operador y lo que el cliente llenó en el formulario.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.plugins.chats.shared.draft_items import draft_items
from src.plugins.chats.shared.operator_order import operator_draft

T0 = 1_790_000_000_000
_MIN = 60_000


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _move(tool: str, at_ms: int, args: dict, *, sent: bool = True) -> dict:
    return {"id": f"act-{at_ms}", "tool": tool, "sent": sent, "at_ms": at_ms, "args": args}


def _form_reply(at_ms: int, fields: str) -> dict:
    return {"role": "user", "timestamp": _iso(at_ms), "content": f"[datos de envío recibidos] {fields}"}


_FORM = ("city=Bogotá; neighborhood=Chapinero; address=Cl 1 # 2-3; phone=3000000000; "
         "receiver_name=Ana; national_id=123; payment_method=transfer; order_total_cop=179800; "
         "items_summary=2× Dúo Zodiacal; flow_token=shipping_x")


def test_with_an_empty_draft_the_products_are_the_ones_the_operator_sent_the_form_for() -> None:
    view = operator_draft(
        None,
        ledger=[_move("request_shipping_details", T0, {"product": "duo-zodiacal", "quantity": "2"})],
        events=[],
        since_ms=T0 - _MIN,
    )

    assert draft_items(view) == [{"producto": "duo-zodiacal", "cantidad": "2", "operador": True}]


def test_the_colors_or_aromas_the_operator_sent_put_that_product_in_play_without_a_quantity() -> None:
    view = operator_draft(
        {"slots": {}},
        ledger=[_move("present_variant_picker", T0, {"product": "duo-zodiacal", "attribute": "color"})],
        events=[],
        since_ms=T0 - _MIN,
    )

    assert draft_items(view) == [{"producto": "duo-zodiacal"}]


def test_the_form_the_operator_sent_wins_over_an_earlier_color_list() -> None:
    view = operator_draft(
        None,
        ledger=[
            _move("present_variant_picker", T0, {"product": "luz-serena", "attribute": "aroma"}),
            _move("request_shipping_details", T0 + _MIN, {"product": "duo-zodiacal", "quantity": 3}),
            _move("present_variant_picker", T0 + 2 * _MIN, {"product": "luz-serena", "attribute": "aroma"}),
        ],
        events=[],
        since_ms=T0 - _MIN,
    )

    assert draft_items(view) == [{"producto": "duo-zodiacal", "cantidad": "3", "operador": True}]


def test_a_form_sent_with_the_native_items_counts_each_item() -> None:
    view = operator_draft(
        None,
        ledger=[_move("request_shipping_details", T0, {"items": [
            {"handle": "duo-zodiacal", "quantity": 2}, {"handle": "luz-serena", "quantity": 1},
        ]})],
        events=[],
        since_ms=T0 - _MIN,
    )

    assert [(i["producto"], i["cantidad"]) for i in draft_items(view)] == [("duo-zodiacal", "2"), ("luz-serena", "1")]


def test_what_the_bot_captured_stays_unless_the_operator_sent_the_form_afterwards() -> None:
    draft = {"items": [{"producto": "Luz Serena", "aroma": "Lavanda", "cantidad": "1"}],
             "slots": {"producto": "Luz Serena"}, "updated_at_ms": T0}
    picker_after = [_move("present_variant_picker", T0 + _MIN, {"product": "duo-zodiacal", "attribute": "color"})]
    form_before = [_move("request_shipping_details", T0 - _MIN, {"product": "duo-zodiacal", "quantity": 2})]
    form_after = [_move("request_shipping_details", T0 + _MIN, {"product": "duo-zodiacal", "quantity": 2})]

    for ledger in (picker_after, form_before):
        assert draft_items(operator_draft(draft, ledger=ledger, events=[], since_ms=T0 - 60 * _MIN)) == draft["items"]
    assert draft_items(operator_draft(draft, ledger=form_after, events=[], since_ms=T0 - 60 * _MIN)) == [
        {"producto": "duo-zodiacal", "cantidad": "2", "operador": True}
    ]


def test_the_shipping_data_comes_from_what_the_customer_filled_in_the_form() -> None:
    view = operator_draft(None, ledger=[], events=[_form_reply(T0, _FORM)], since_ms=T0 - _MIN)

    assert view["slots"] == {
        "ciudad": "Bogotá", "barrio": "Chapinero", "direccion": "Cl 1 # 2-3", "telefono": "3000000000",
        "nombre_recibe": "Ana", "cedula": "123", "metodo_pago": "transfer",
    }


def test_a_form_reply_newer_than_the_draft_wins_and_an_older_one_only_fills_gaps() -> None:
    draft = {"slots": {"ciudad": "Medellín", "notas": "regalo"}, "updated_at_ms": T0}

    newer = operator_draft(draft, ledger=[], events=[_form_reply(T0 + _MIN, _FORM)], since_ms=T0 - 60 * _MIN)
    assert newer["slots"]["ciudad"] == "Bogotá" and newer["slots"]["notas"] == "regalo"

    older = operator_draft(draft, ledger=[], events=[_form_reply(T0 - _MIN, _FORM)], since_ms=T0 - 60 * _MIN)
    assert older["slots"]["ciudad"] == "Medellín" and older["slots"]["direccion"] == "Cl 1 # 2-3"


def test_only_what_happened_in_this_episode_counts() -> None:
    """Caso 2026-10-09: el formulario y el pedido del episodio anterior (otra
    compra) no son los de esta conversación."""
    since = T0
    view = operator_draft(
        None,
        ledger=[
            _move("request_shipping_details", since - _MIN, {"product": "duo-zodiacal", "quantity": 2}),
            _move("request_shipping_details", since + _MIN, {"product": "luz-serena", "quantity": 1}, sent=False),
        ],
        events=[_form_reply(since - _MIN, _FORM)],
        since_ms=since,
    )

    assert draft_items(view) == [] and view["slots"] == {}


def test_moves_that_name_no_usable_product_or_quantity_are_ignored() -> None:
    view = operator_draft(
        None,
        ledger=[
            _move("request_shipping_details", T0, {}),
            _move("request_shipping_details", T0 + _MIN, {"product": "duo-zodiacal", "quantity": "muchas"}),
            _move("present_variant_picker", T0 + 2 * _MIN, {"variant_type": "color", "options": []}),
            {"tool": "request_shipping_details", "at_ms": T0, "sent": True},  # entrada vieja sin args
        ],
        events=[{"role": "user", "content": "[datos de envío recibidos] (sin datos)", "timestamp": _iso(T0)}],
        since_ms=T0 - _MIN,
    )

    assert draft_items(view) == [] and view["slots"] == {}


def test_the_draft_is_not_modified() -> None:
    draft = {"slots": {"ciudad": "Medellín"}, "updated_at_ms": T0}

    operator_draft(draft, ledger=[_move("request_shipping_details", T0 + _MIN, {"product": "duo-zodiacal"})],
                   events=[_form_reply(T0 + _MIN, _FORM)], since_ms=None)

    assert draft == {"slots": {"ciudad": "Medellín"}, "updated_at_ms": T0}


# ── ¿el cliente cerró la compra? (habilita «Crear pedido» en la app) ──────────

from src.plugins.chats.shared.operator_order import summary_confirmed  # noqa: E402


def _summary(at_ms: int) -> dict:
    return {"role": "assistant", "kind": "ui_component", "component_kind": "order_confirmation",
            "timestamp": _iso(at_ms), "content": "🧾 El operador envió el resumen del pedido con botones para confirmar"}


def _tap(at_ms: int, title: str) -> dict:
    return {"role": "user", "timestamp": _iso(at_ms), "content": f"[el cliente tocó el botón: {title}]"}


def test_the_customer_confirmed_when_they_tap_confirm_on_the_last_summary() -> None:
    assert summary_confirmed([_summary(T0), _tap(T0 + _MIN, "✅ Confirmar")], since_ms=T0 - _MIN) is True


def test_modify_a_summary_sent_again_or_another_episode_is_not_a_confirmation() -> None:
    since = T0
    assert summary_confirmed([_summary(T0), _tap(T0 + _MIN, "✏️ Modificar")], since_ms=since) is False
    # cuenta el último toque: confirmó y después pidió modificar
    assert summary_confirmed(
        [_summary(T0), _tap(T0 + _MIN, "✅ Confirmar"), _tap(T0 + 2 * _MIN, "✏️ Modificar")], since_ms=since) is False
    # confirmó un resumen y después se le mandó otro: falta que confirme el nuevo
    assert summary_confirmed(
        [_summary(T0), _tap(T0 + _MIN, "✅ Confirmar"), _summary(T0 + 2 * _MIN)], since_ms=since) is False
    # lo confirmado en un episodio anterior es otra compra
    assert summary_confirmed([_summary(since - 2 * _MIN), _tap(since - _MIN, "✅ Confirmar")], since_ms=since) is False
    assert summary_confirmed([_tap(T0 + _MIN, "✅ Confirmar")], since_ms=since) is False  # sin resumen
