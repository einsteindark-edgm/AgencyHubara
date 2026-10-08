"""Reglas PURAS de la app móvil del operador (`chats/shared/mobile_rules`).

La app Android pinta lo que estas funciones deciden: las burbujas de acción
sugeridas por etapa del embudo (`suggest_actions`), los "incendios" de la
bandeja (`detect_fires`) y las ventas calientes del widget (`hot_sales`).
Deterministas (`decided_by: "rules"`): mismos hechos → misma respuesta.
"""
from __future__ import annotations

from dataclasses import replace

from src.plugins.chats.shared.mobile_rules import (
    DraftItemFacts,
    OperatorMove,
    SuggestionFacts,
    last_sent_action,
    suggest_actions,
)

_SID = "wa_test_laura"


def _facts(**over) -> SuggestionFacts:
    base = SuggestionFacts(
        session_id=_SID,
        version=1_727_640_000_000,
        stage="etapa_descubrimiento",
        window_open=True,
        in_control="human",
        catalog_available=True,
        payment_methods_available=True,
    )
    return replace(base, **over)


def _ids(out: dict) -> list[str]:
    return [s["id"] for s in out["suggestions"]]


# ── suggest_actions: etapas ──────────────────────────────────────────────────


def test_discovery_offers_products_then_shipping_rates_then_payment_methods() -> None:
    out = suggest_actions(_facts(stage="etapa_descubrimiento"))

    assert _ids(out) == ["present_products", "send_shipping_rates", "send_payment_methods"]
    assert out["suggestions"][0] == {
        "id": "present_products",
        "label": "Enviar productos",
        "prominence": "primary",
        "editable": True,
        "action": {"name": "present_products", "args": {}},
    }
    assert [s["prominence"] for s in out["suggestions"]] == ["primary", "normal", "normal"]
    assert [s["label"] for s in out["suggestions"][1:]] == ["Tarifas de envío", "Medios de pago"]
    assert out["decided_by"] == "rules" and out["session_id"] == _SID


def test_closed_window_suggests_nothing_so_the_app_offers_the_template() -> None:
    out = suggest_actions(_facts(stage="etapa_descubrimiento", window_open=False))

    assert out["suggestions"] == []
    assert out["window_open"] is False and out["stage"] == "etapa_descubrimiento"


def test_only_legal_moves_are_offered_and_the_first_legal_one_is_primary() -> None:
    """Sin catálogo no hay productos que mandar; sin datos de pago configurados
    `send_payment_methods` no tiene qué enviar (el render daría None)."""
    out = suggest_actions(_facts(catalog_available=False, payment_methods_available=False))

    assert _ids(out) == ["send_shipping_rates"]
    assert out["suggestions"][0]["prominence"] == "primary"


_DUO = DraftItemFacts(handle="duo-zodiacal", quantity=2, missing=("aroma",), image_count=3, unit_price_cop=89_900)


def test_variants_stage_offers_the_picker_for_the_missing_attribute_then_more_photos() -> None:
    out = suggest_actions(_facts(stage="etapa_variantes", items=(_DUO,)))

    assert _ids(out) == ["present_variant_picker", "present_product_gallery"]
    assert out["suggestions"][0] == {
        "id": "present_variant_picker",
        "label": "Enviar aromas",
        "prominence": "primary",
        "editable": True,
        "action": {
            "name": "present_variant_picker",
            "args": {"product": "duo-zodiacal", "attribute": "aroma"},
        },
    }
    gallery = out["suggestions"][1]
    assert gallery["label"] == "Más fotos" and gallery["prominence"] == "normal"
    assert gallery["action"] == {"name": "present_product_gallery", "args": {"handle": "duo-zodiacal"}}


def test_picker_targets_the_first_item_missing_something_and_says_colors_for_color() -> None:
    complete = DraftItemFacts(handle="luz-serena", quantity=1, missing=(), image_count=1, unit_price_cop=29_000)
    needs_color = DraftItemFacts(handle="cubo-love", quantity=1, missing=("color",), image_count=1, unit_price_cop=35_000)

    out = suggest_actions(_facts(stage="etapa_variantes", items=(complete, needs_color)))

    picker = out["suggestions"][0]
    assert picker["label"] == "Enviar colores"
    assert picker["action"]["args"] == {"product": "cubo-love", "attribute": "color"}
    # ninguno tiene fotos además de la portada → sin "Más fotos"
    assert "present_product_gallery" not in _ids(out)


def test_asking_shipping_details_needs_a_confirmed_purchase_and_complete_items() -> None:
    ready = replace(_DUO, missing=())
    offered = suggest_actions(_facts(stage="etapa_variantes", items=(ready,), purchase_confirmed=True))
    assert _ids(offered) == ["present_product_gallery", "request_shipping_details"]
    ask = offered["suggestions"][1]
    assert ask == {
        "id": "request_shipping_details",
        "label": "Pedir datos de envío",
        "prominence": "normal",
        "editable": False,
        "action": {"name": "request_shipping_details", "args": {}},
    }
    # la misma guarda que la tool del bot: sin "sí" del cliente, o si acaba de aplazar, no
    assert "request_shipping_details" not in _ids(
        suggest_actions(_facts(stage="etapa_variantes", items=(ready,), purchase_confirmed=False))
    )
    assert "request_shipping_details" not in _ids(
        suggest_actions(_facts(stage="etapa_variantes", items=(ready,), purchase_confirmed=True, customer_deferred=True))
    )
    # un producto que no resolvió al catálogo o sin cantidad: no se pueden armar los ítems
    for broken in (replace(ready, handle=None), replace(ready, quantity=None)):
        assert "request_shipping_details" not in _ids(
            suggest_actions(_facts(stage="etapa_variantes", items=(broken,), purchase_confirmed=True))
        )


_READY = replace(_DUO, missing=())


def test_shipping_stage_puts_the_shipping_form_first() -> None:
    out = suggest_actions(_facts(stage="etapa_datos_envio", items=(_READY,), purchase_confirmed=True))

    assert _ids(out) == ["request_shipping_details", "send_shipping_rates", "send_payment_methods"]
    assert out["suggestions"][0]["prominence"] == "primary"


def test_closing_stage_offers_the_order_summary_when_it_can_be_built() -> None:
    out = suggest_actions(_facts(stage="etapa_cierre", items=(_READY,), shipping_ready=True))

    assert _ids(out) == ["present_order_confirmation", "send_payment_methods"]
    assert out["suggestions"][0] == {
        "id": "present_order_confirmation",
        "label": "Resumen para confirmar",
        "prominence": "primary",
        "editable": False,
        "action": {"name": "present_order_confirmation", "args": {}},
    }
    # sin ciudad/dirección/medio de pago o sin precio de catálogo no hay resumen
    assert _ids(suggest_actions(_facts(stage="etapa_cierre", items=(_READY,), shipping_ready=False))) == [
        "send_payment_methods"
    ]
    no_price = replace(_READY, unit_price_cop=None)
    assert _ids(suggest_actions(_facts(stage="etapa_cierre", items=(no_price,), shipping_ready=True))) == [
        "send_payment_methods"
    ]


def test_after_the_order_only_payment_methods_and_only_while_payment_is_pending() -> None:
    pending = suggest_actions(_facts(stage="etapa_postcierre", payment_pending=True))
    assert _ids(pending) == ["send_payment_methods"]
    assert pending["suggestions"][0]["prominence"] == "primary"

    assert suggest_actions(_facts(stage="etapa_postcierre", payment_pending=False))["suggestions"] == []


def test_the_action_sent_last_is_never_suggested_again() -> None:
    out = suggest_actions(_facts(stage="etapa_descubrimiento", last_action="present_products"))

    assert _ids(out) == ["send_shipping_rates", "send_payment_methods"]
    assert out["suggestions"][0]["prominence"] == "primary"


def test_an_operator_move_since_the_customer_last_wrote_is_not_suggested_until_the_customer_writes_again() -> None:
    """Con un humano al mando nada actualiza el borrador: la jugada del operador
    vuelve al estado. Sin esto, tras mandar los aromas y después las fotos,
    «Enviar aromas» volvía como primaria."""
    t0 = 1_790_000_000_000
    base = _facts(stage="etapa_variantes", items=(_DUO,), last_inbound_ms=t0)
    # entradas viejas del ledger (sin args): cuentan por el nombre de la acción
    aromas = OperatorMove("present_variant_picker", t0 + 60_000)
    photos = OperatorMove("present_product_gallery", t0 + 120_000)

    after_aromas = replace(base, operator_moves=(aromas,), last_action="present_variant_picker")
    assert _ids(suggest_actions(after_aromas)) == ["present_product_gallery"]

    after_photos = replace(base, operator_moves=(aromas, photos), last_action="present_product_gallery")
    assert _ids(suggest_actions(after_photos)) == []

    # el cliente escribe de nuevo: lo de antes vuelve a ser jugada legal (menos lo último enviado)
    customer_wrote = suggest_actions(replace(after_photos, last_inbound_ms=t0 + 180_000))
    assert _ids(customer_wrote) == ["present_variant_picker"]
    assert customer_wrote["suggestions"][0]["prominence"] == "primary"


def test_after_sending_the_aromas_the_colors_of_the_same_product_are_still_offered() -> None:
    """La identidad de una jugada es la acción + sus args: «Enviar aromas» y
    «Enviar colores» son el mismo `present_variant_picker` con otro
    `attribute` ("te mando aromas y colores")."""
    t0 = 1_790_000_000_000
    duo = replace(_DUO, missing=("aroma", "color"))
    sent_aromas = OperatorMove(
        "present_variant_picker", t0 + 60_000, args={"product": "duo-zodiacal", "attribute": "aroma"}
    )

    out = suggest_actions(_facts(stage="etapa_variantes", items=(duo,), last_inbound_ms=t0,
                                 operator_moves=(sent_aromas,), last_action="present_variant_picker"))

    assert [s["label"] for s in out["suggestions"]] == ["Enviar colores", "Más fotos"]
    assert out["suggestions"][0]["action"]["args"] == {"product": "duo-zodiacal", "attribute": "color"}


def test_never_more_than_four_suggestions(monkeypatch) -> None:
    from src.plugins.chats.shared import mobile_rules

    monkeypatch.setitem(
        mobile_rules._STAGE_ACTIONS,
        "etapa_prueba",
        ("present_products", "send_shipping_rates", "send_payment_methods", "present_product_gallery",
         "present_variant_picker", "present_order_confirmation"),
    )
    many = (_DUO,)
    out = suggest_actions(_facts(stage="etapa_prueba", items=many, shipping_ready=True))

    assert len(out["suggestions"]) == 4
    assert [s["prominence"] for s in out["suggestions"]] == ["primary", "normal", "normal", "normal"]



# ── last_sent_action: la última acción que salió, leída del JSONL ────────────


def test_last_action_reads_the_operator_marker_the_bot_card_or_the_bot_turn() -> None:
    user = {"role": "user", "content": "y cuánto vale?"}
    operator_text = {"role": "assistant", "sender": "human", "content": "Tarifas…", "operator_tool": "send_shipping_rates"}
    bot_card = {"role": "assistant", "kind": "ui_component", "component_kind": "products_list", "content": "🛍️ …"}
    # el picker de variantes sale como TEXTO: lo delata el `tools_used` del turno
    bot_turn = {"role": "assistant", "content": "", "tools_used": ["search_products", "present_variant_picker"]}
    picker_text = {"role": "assistant", "content": "Tenemos estos aromas:\n💜 Lavanda"}

    assert last_sent_action([bot_card, user, operator_text, user]) == "send_shipping_rates"
    assert last_sent_action([operator_text, bot_card, user]) == "present_products"
    assert last_sent_action([bot_card, bot_turn, picker_text, user]) == "present_variant_picker"


def test_last_action_maps_the_order_registration_to_payment_methods_and_skips_non_ui_turns() -> None:
    registered = {"role": "assistant", "content": "¡Listo!", "tools_used": ["register_order"]}
    chatter = {"role": "assistant", "content": "Claro", "tools_used": ["search_products", "set_order_slot"]}

    assert last_sent_action([registered, chatter]) == "send_payment_methods"
    assert last_sent_action([chatter, {"role": "user", "content": "hola"}]) is None
    assert last_sent_action([]) is None


def test_every_funnel_stage_has_a_suggestion_table() -> None:
    """Las etapas viven en `funnel_stage` (agente); las reglas no importan el
    agente — este guard atrapa una etapa nueva sin burbujas."""
    from src.plugins.chats.agent.sales.use_cases.funnel_stage import ALL_STAGES
    from src.plugins.chats.shared import mobile_rules

    assert set(mobile_rules._STAGE_ACTIONS) == set(ALL_STAGES)


# ── detect_fires: conversaciones ─────────────────────────────────────────────

NOW = 1_790_000_000_000
MIN = 60_000


def _chat(**over):
    from src.plugins.chats.shared.mobile_rules import ChatFireFacts

    base = ChatFireFacts(
        session_id="wa_test_sofia", name="Sofía Pérez", in_human=True, escalation_reason="EXPLICIT_REQUEST",
        unanswered_count=4, waiting_since_ms=NOW - 12 * MIN, last_inbound_ms=NOW - 8 * MIN,
    )
    return replace(base, **over)


def _fires(chats=(), orders=(), today="2026-09-21"):
    from src.plugins.chats.shared.mobile_rules import detect_fires

    return detect_fires(list(chats), list(orders), now_ms=NOW, today_iso=today)


def test_a_customer_waiting_12_minutes_for_a_human_is_a_grave_fire() -> None:
    assert _fires([_chat()]) == [{
        "fire_id": "chat:wa_test_sofia",
        "subject": {"kind": "chat", "session_id": "wa_test_sofia", "order_id": None},
        "severity": "grave",
        "kind": "wants_human",
        "getting_worse": False,
        "title": "Sofía pide un humano",
        "subtitle": "12 min sin respuesta · 4 mensajes",
        "primary_action": {"name": "open_chat", "args": {"session_id": "wa_test_sofia"}},
        "updated_ms": NOW - 8 * MIN,
    }]


def test_wait_thresholds_decide_the_severity() -> None:
    def severity(wait_min: float) -> str:
        chat = _chat(waiting_since_ms=int(NOW - wait_min * MIN), last_inbound_ms=int(NOW - wait_min * MIN))
        return _fires([chat])[0]["severity"]

    assert [severity(m) for m in (0.5, 1.9, 2, 9.9, 10, 60)] == ["espera", "espera", "hoy", "hoy", "grave", "grave"]


def test_escalation_reason_decides_the_kind_and_health_or_failed_orders_are_always_grave() -> None:
    fresh = {"waiting_since_ms": NOW - MIN // 2, "last_inbound_ms": NOW - MIN // 2}  # espera por tiempo
    cases = {
        "EXPLICIT_REQUEST": ("wants_human", "espera"),
        "HEALTH_SAFETY": ("health", "grave"),
        "POST_SALE_ISSUE": ("order_problem", "espera"),
        "SHIPPING_ISSUE": ("order_problem", "espera"),
        "PAYMENT_VERIFICATION_PENDING": ("payment_proof", "espera"),
        "ORDER_REGISTRATION_FAILED": ("order_problem", "grave"),
        "CHECKOUT_VERIFY_FAILED": ("bot_stuck", "espera"),
        "DISCOUNT_REQUEST": ("other", "espera"),
        None: ("other", "espera"),
    }
    for reason, expected in cases.items():
        fire = _fires([_chat(escalation_reason=reason, **fresh)])[0]
        assert (fire["kind"], fire["severity"]) == expected, reason
        assert "Sofía" in fire["title"] and fire["title"] != "Sofía"


def test_no_fire_while_the_bot_has_the_chat_or_nobody_is_waiting() -> None:
    assert _fires([_chat(in_human=False)]) == []
    assert _fires([_chat(unanswered_count=0)]) == []


def test_a_customer_still_writing_right_now_makes_it_worse() -> None:
    insisting = _chat(unanswered_count=3, last_inbound_ms=NOW - MIN)
    assert _fires([insisting])[0]["getting_worse"] is True
    # un solo mensaje, o el último hace rato: no está escalando
    assert _fires([replace(insisting, unanswered_count=1)])[0]["getting_worse"] is False
    assert _fires([replace(insisting, last_inbound_ms=NOW - 6 * MIN)])[0]["getting_worse"] is False


def test_without_a_profile_name_the_title_says_cliente_and_never_a_phone() -> None:
    fire = _fires([_chat(name=None, session_id="wa_555000111222")])[0]
    assert fire["title"] == "Cliente pide un humano"
    assert "555" not in fire["title"] + fire["subtitle"]


# ── detect_fires: pedidos (OrderFacts) ───────────────────────────────────────

DAY = 24 * 60 * MIN


def _order(**over):
    from src.plugins.chats.shared.mobile_rules import OrderFireFacts

    base = OrderFireFacts(
        session_id="wa_test_ana", order_id="order_32", display_id="#32", name="Ana María", total_cop=179_800,
        stage="preparing", due_iso=None, overdue_since_ms=None, payment_pending=False, pending_since_ms=None,
    )
    return replace(base, **over)


def test_an_order_two_days_late_is_for_today_and_four_days_late_is_grave() -> None:
    late = _order(due_iso="2026-09-19", overdue_since_ms=NOW - 2 * DAY)

    assert _fires(orders=[late]) == [{
        "fire_id": "order:order_32",
        "subject": {"kind": "order", "session_id": "wa_test_ana", "order_id": "order_32"},
        "severity": "hoy",
        "kind": "delayed",
        "getting_worse": False,
        "title": "El pedido #32 de Ana va retrasado",
        "subtitle": "2 días de retraso",
        "primary_action": {"name": "open_order", "args": {"order_id": "order_32", "session_id": "wa_test_ana"}},
        "updated_ms": NOW - 2 * DAY,
    }]
    very_late = _fires(orders=[replace(late, due_iso="2026-09-17")])[0]
    assert (very_late["severity"], very_late["subtitle"]) == ("grave", "4 días de retraso")


def test_orders_due_today_delivered_cancelled_or_unscheduled_are_not_late() -> None:
    assert _fires(orders=[_order(due_iso="2026-09-21")]) == []
    assert _fires(orders=[_order(due_iso="2026-09-10", stage="delivered")]) == []
    assert _fires(orders=[_order(due_iso="2026-09-10", stage="cancelled")]) == []
    assert _fires(orders=[_order(due_iso=None)]) == []


def test_an_order_waiting_for_payment_verification_is_for_today() -> None:
    fire = _fires(orders=[_order(payment_pending=True, pending_since_ms=NOW - 3 * 60 * MIN)])[0]

    assert (fire["kind"], fire["severity"]) == ("payment_proof", "hoy")
    assert fire["title"] == "Verificar el pago de Ana"
    assert fire["subtitle"] == "Pedido #32 · $179.800"
    assert fire["primary_action"]["name"] == "open_order"
    assert fire["updated_ms"] == NOW - 3 * 60 * MIN


# ── detect_fires: una tarjeta por cliente + orden de la bandeja ──────────────


def test_one_card_per_customer_keeps_the_most_severe_fire() -> None:
    ana_chat = _chat(session_id="wa_test_ana", name="Ana", escalation_reason="PAYMENT_VERIFICATION_PENDING")
    ana_payment = _order(payment_pending=True, pending_since_ms=NOW - 60 * MIN)

    # chat grave (12 min esperando) vs pago por verificar (hoy) → queda el chat
    only = _fires([ana_chat], [ana_payment])
    assert [f["fire_id"] for f in only] == ["chat:wa_test_ana"]
    assert only[0]["subject"] == {"kind": "chat", "session_id": "wa_test_ana", "order_id": None}

    # chat recién llegado (espera) vs pedido 5 días tarde (grave) → queda el pedido, con su chat
    recent = replace(ana_chat, waiting_since_ms=NOW - MIN // 2, last_inbound_ms=NOW - MIN // 2)
    late = _order(due_iso="2026-09-16", overdue_since_ms=NOW - 5 * DAY)
    only = _fires([recent], [late])
    assert [f["fire_id"] for f in only] == ["order:order_32"]
    assert only[0]["subject"] == {"kind": "order", "session_id": "wa_test_ana", "order_id": "order_32"}

    # el mismo pedido atrasado Y sin verificar: una sola tarjeta (la que espera hace más)
    both = replace(late, due_iso="2026-09-19", overdue_since_ms=NOW - 2 * DAY,
                   payment_pending=True, pending_since_ms=NOW - 60 * MIN)
    only = _fires(orders=[both])
    assert len(only) == 1 and only[0]["kind"] == "delayed"


def test_the_inbox_is_sorted_grave_then_today_then_waiting_oldest_first() -> None:
    fires = _fires(
        [
            _chat(session_id="wa_test_a", waiting_since_ms=NOW - MIN, last_inbound_ms=NOW - MIN),       # espera
            _chat(session_id="wa_test_b", waiting_since_ms=NOW - 3 * MIN, last_inbound_ms=NOW - 3 * MIN),  # hoy
            _chat(session_id="wa_test_c", waiting_since_ms=NOW - 15 * MIN, last_inbound_ms=NOW - 9 * MIN),  # grave
            _chat(session_id="wa_test_d", waiting_since_ms=NOW - 40 * MIN, last_inbound_ms=NOW - 30 * MIN),  # grave, más vieja
        ],
        [_order(session_id="wa_test_e", order_id="order_e", payment_pending=True, pending_since_ms=NOW - 2 * DAY)],  # hoy, más vieja
    )

    assert [f["fire_id"] for f in fires] == [
        "chat:wa_test_d", "chat:wa_test_c", "order:order_e", "chat:wa_test_b", "chat:wa_test_a",
    ]


# ── unanswered_since: desde cuándo espera el cliente ─────────────────────────


def _ts(ms: int) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def test_waiting_starts_at_the_first_customer_message_after_the_last_reply() -> None:
    from src.plugins.chats.shared.mobile_rules import unanswered_since

    events = [
        {"role": "user", "content": "hola", "timestamp": _ts(NOW - 60 * MIN)},
        {"role": "assistant", "sender": "human", "content": "¡Hola!", "timestamp": _ts(NOW - 59 * MIN)},
        {"role": "user", "content": "quiero hablar con alguien", "timestamp": _ts(NOW - 12 * MIN)},
        # un turno del bot con tool_calls no es respuesta (su texto no sale)
        {"role": "assistant", "content": "", "tool_calls": [{"name": "escalate_to_human"}], "timestamp": _ts(NOW - 11 * MIN)},
        {"role": "user", "content": "?", "timestamp": _ts(NOW - 5 * MIN)},
    ]

    assert unanswered_since(events) == (2, NOW - 12 * MIN)
    # una tarjeta enviada (ui_component) SÍ es respuesta
    card = {"role": "assistant", "kind": "ui_component", "content": "🛍️", "timestamp": _ts(NOW - MIN)}
    assert unanswered_since([*events, card]) == (0, None)
    assert unanswered_since([]) == (0, None)


def test_waiting_count_is_what_the_customer_wrote_since_the_last_reply() -> None:
    """Los mensajes SIN RESPUESTA del incendio («12 min sin respuesta · 2 mensajes»). Desde #384 la
    bandeja cuenta otra cosa (no leídos por el operador), así que ya no se comparan."""
    from src.plugins.chats.shared.mobile_rules import unanswered_since

    events = [
        {"role": "user", "content": "a", "timestamp": _ts(NOW - 9 * MIN)},
        {"role": "assistant", "content": "b", "timestamp": _ts(NOW - 8 * MIN)},
        {"role": "user", "content": "c", "timestamp": _ts(NOW - 7 * MIN)},
        {"role": "assistant", "content": "", "tool_calls": [{"name": "x"}]},
        {"role": "user", "content": "d"},  # legacy sin timestamp: cuenta, no fecha
    ]
    assert unanswered_since(events)[0] == 2
    assert unanswered_since(events)[1] == NOW - 7 * MIN


def test_the_bot_handing_off_is_not_an_answer_the_customer_waits_for_a_person() -> None:
    """Prueba del operador (2026-10-07): «necesito hablar con alguien urgente» → el bot respondió «te comunico…» y
    pasó a humano. Ese mensaje contaba como respuesta: cero sin responder, sin incendio y sin aviso."""
    from src.plugins.chats.shared.mobile_rules import handoff_pending, unanswered_since

    events = [
        {"role": "user", "content": "hola", "timestamp": _ts(NOW - 3 * MIN)},
        {"role": "assistant", "content": "¡Hola! ¿En qué te ayudo?", "timestamp": _ts(NOW - 3 * MIN)},
        {"role": "user", "content": "necesito hablar con alguien urgente", "timestamp": _ts(NOW - MIN)},
        {"role": "assistant", "content": "Te comunico con una persona del equipo 🙏",
         "tools_used": ["escalate_to_human"], "timestamp": _ts(NOW - MIN + 5_000)},
    ]
    assert unanswered_since(events) == (1, NOW - MIN)
    assert handoff_pending(events) is True

    # El operador respondió: ya no espera el traspaso.
    reply = {"role": "assistant", "sender": "human", "content": "Hola, soy Ana", "timestamp": _ts(NOW)}
    assert unanswered_since([*events, reply]) == (0, None)
    assert handoff_pending([*events, reply]) is False
    # Vuelve a escribir después de la respuesta del operador: espera normal, no un traspaso nuevo.
    again = {"role": "user", "content": "?", "timestamp": _ts(NOW)}
    assert handoff_pending([*events, reply, again]) is False
    # Un bot que contestó sin traspasar sí es respuesta.
    assert handoff_pending(events[:2]) is False


def test_a_handoff_is_a_grave_fire_from_the_first_second() -> None:
    """Decisión del operador (2026-10-07): aviso inmediato en TODO traspaso a humano, sin esperar los 10 min."""
    just_handed_off = _chat(unanswered_count=1, waiting_since_ms=NOW - MIN // 2, last_inbound_ms=NOW - MIN // 2,
                            escalation_reason=None, handoff_pending=True)
    assert _fires([just_handed_off])[0]["severity"] == "grave"
    # El mismo chat sin traspaso pendiente (el operador ya contestó y el cliente volvió a escribir): por tiempo.
    assert _fires([replace(just_handed_off, handoff_pending=False)])[0]["severity"] == "espera"


def test_display_name_is_a_real_first_name_or_nothing() -> None:
    from src.plugins.chats.shared.mobile_rules import display_name

    assert display_name("  Sofía   Pérez ") == "Sofía"
    # placeholders de Medusa, correos y teléfonos NO son nombres (y no se muestran)
    for bad in ("Cliente WhatsApp", "cliente", "ana@correo.test", "3000000000", "", None, 42):
        assert display_name(bad) is None, bad


# ── hot_sales: ventas calientes del widget ───────────────────────────────────


def _hot(**over):
    from src.plugins.chats.shared.mobile_rules import HotFacts, HotItem

    base = HotFacts(
        session_id="wa_test_laura", name="Laura", stage="etapa_cierre", in_human=False, episode_has_order=False,
        last_inbound_ms=NOW - 4 * MIN,
        items=(HotItem(name="Duo Zodiacal", variant="azul", quantity=2, unit_price_cop=89_900),),
    )
    return replace(base, **over)


def _hot_sales(*candidates):
    from src.plugins.chats.shared.mobile_rules import hot_sales

    return hot_sales(list(candidates), now_ms=NOW)


def test_a_bot_sale_about_to_close_is_hot() -> None:
    assert _hot_sales(_hot()) == [{
        "session_id": "wa_test_laura",
        "name": "Laura",
        "stage": "etapa_cierre",
        "product": "Duo Zodiacal azul × 2",
        "cart_value_cop": 179_800,
        "risk": False,
        "updated_ms": NOW - 4 * MIN,
    }]


def test_only_bot_sales_in_shipping_or_closing_stage_active_in_30_minutes_and_without_order() -> None:
    assert _hot_sales(_hot(stage="etapa_datos_envio"))  # también datos de envío
    assert _hot_sales(_hot(last_inbound_ms=NOW - 30 * MIN))  # justo 30 min: sí
    assert _hot_sales(_hot(in_human=True)) == []  # la tiene un humano: no es del widget del bot
    assert _hot_sales(_hot(stage="etapa_variantes")) == []
    assert _hot_sales(_hot(stage="etapa_postcierre")) == []
    assert _hot_sales(_hot(last_inbound_ms=NOW - 31 * MIN)) == []
    assert _hot_sales(_hot(last_inbound_ms=None)) == []
    assert _hot_sales(_hot(episode_has_order=True)) == []


def test_closing_sales_first_then_the_most_recent_and_never_more_than_ten() -> None:
    shipping_new = _hot(session_id="wa_test_a", stage="etapa_datos_envio", last_inbound_ms=NOW - MIN)
    closing_old = _hot(session_id="wa_test_b", stage="etapa_cierre", last_inbound_ms=NOW - 20 * MIN)
    closing_new = _hot(session_id="wa_test_c", stage="etapa_cierre", last_inbound_ms=NOW - 2 * MIN)

    assert [h["session_id"] for h in _hot_sales(shipping_new, closing_old, closing_new)] == [
        "wa_test_c", "wa_test_b", "wa_test_a",
    ]
    many = [_hot(session_id=f"wa_test_{i}", last_inbound_ms=NOW - i * MIN) for i in range(12)]
    assert len(_hot_sales(*many)) == 10


def test_several_products_one_line_and_no_cart_value_without_every_price() -> None:
    from src.plugins.chats.shared.mobile_rules import HotItem

    items = (HotItem(name="Duo Zodiacal", variant="azul", quantity=2, unit_price_cop=89_900),
             HotItem(name="Luz Serena", quantity=None, unit_price_cop=29_000))
    [sale] = _hot_sales(_hot(items=items, customer_deferred=True, name=None))

    assert sale["product"] == "Duo Zodiacal azul × 2 + Luz Serena"
    assert sale["cart_value_cop"] is None
    assert sale["risk"] is True and sale["name"] == "Cliente"
    assert _hot_sales(_hot(items=()))[0]["product"] is None
