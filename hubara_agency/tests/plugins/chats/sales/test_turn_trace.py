"""Traza por turno (HU-SC-0) — funciones puras de `sales/turn_trace.py`.

La traza es el insumo del scorecard por etapa: por cada turno del bot deja lo
que el evaluador necesita ver y hoy no ve (tools con su resultado, rechazos de
las guardas, texto suprimido, etapa, señal del cliente). Estas funciones son
puras: el workflow las usa para armar el payload (R-DET) y la activity para
enriquecerlo con metadata.
"""
from __future__ import annotations

import json

from src.plugins.chats.agent.sales import turn_trace as tt


# ---------------------------------------------------------------------------
# summarize_tool_event — clasifica el resultado de una tool
# ---------------------------------------------------------------------------


def test_summarize_tool_event_rejected_shipping_form_keeps_reason() -> None:
    result = json.dumps(
        {"queued": False, "error": "purchase_not_confirmed", "next_step": "pide el sí"}
    )

    event = tt.summarize_tool_event(
        "request_shipping_details", {"order_total_cop": 89000}, result
    )

    assert event["name"] == "request_shipping_details"
    assert event["ok"] is False
    assert event["error"] == "purchase_not_confirmed"


def test_summarize_tool_event_search_is_ok_and_counts_results() -> None:
    result = json.dumps({"query": "café", "count": 23, "results": []})

    event = tt.summarize_tool_event("search_products", {"q": "café"}, result)

    assert event["ok"] is True
    assert event["error"] is None
    assert "count:23" in event["notes"]
    assert event["args"] == {"q": "café"}


def test_summarize_tool_event_degraded_tag_is_ok_with_note() -> None:
    result = json.dumps({"tag": "INTERESADO", "degraded_from": "CONFIRMADO_SIN_DATOS"})

    event = tt.summarize_tool_event(
        "manage_conversation_tag", {"tag": "CONFIRMADO_SIN_DATOS"}, result
    )

    assert event["ok"] is True
    assert "degraded_from:CONFIRMADO_SIN_DATOS" in event["notes"]


def test_summarize_tool_event_false_flag_without_error_is_rejection() -> None:
    event = tt.summarize_tool_event(
        "escalate_to_human", {}, json.dumps({"escalated": False})
    )

    assert event["ok"] is False
    assert event["error"] == "not_escalated"


def test_summarize_tool_event_plain_text_result_is_ok() -> None:
    event = tt.summarize_tool_event("load_skill", {"name": "notas"}, "contenido del skill")

    assert event["ok"] is True
    assert event["notes"] == []


def test_summarize_tool_event_truncated_json_still_reads_control_keys() -> None:
    full = json.dumps({"query": "", "count": 24, "error": "catalog_unavailable", "results": ["x" * 90] * 80})

    event = tt.summarize_tool_event("search_products", {}, full[:3000])

    assert event["ok"] is False
    assert event["error"] == "catalog_unavailable"
    assert "count:24" in event["notes"]


# ---------------------------------------------------------------------------
# project_stage — etapa del scorecard desde el episodio
# ---------------------------------------------------------------------------


def _episode(slots: dict | None = None, **extra) -> dict:
    ep = {"episode_id": "ep_007", "started_at_ms": 1}
    if slots is not None:
        ep["order_draft"] = {"slots": slots}
    ep.update(extra)
    return ep


_VARIANTS = {"producto": "cubo-love", "aroma": "Café", "color": "Azul", "cantidad": "1"}
_SHIPPING = {
    "ciudad": "Bogotá", "direccion": "Cra 1", "telefono": "3000000000",
    "nombre_recibe": "Ana", "metodo_pago": "contra_entrega",
}


def test_project_stage_without_product_is_discovery() -> None:
    assert tt.project_stage(None) == "descubrimiento"
    assert tt.project_stage(_episode({})) == "descubrimiento"


def test_project_stage_with_product_missing_variant_is_variants() -> None:
    assert tt.project_stage(_episode({"producto": "cubo-love", "color": "Azul"})) == "variantes"


def test_project_stage_variants_complete_without_confirmation_is_confirmation() -> None:
    assert tt.project_stage(_episode(dict(_VARIANTS))) == "confirmacion"


def test_project_stage_confirmed_missing_shipping_is_shipping_data() -> None:
    ep = _episode(dict(_VARIANTS))
    ep["order_draft"]["confirmed_at_ms"] = 10
    assert tt.project_stage(ep) == "datos_envio"


def test_project_stage_shipping_slot_started_counts_as_shipping_data() -> None:
    assert tt.project_stage(_episode({**_VARIANTS, "ciudad": "Cali"})) == "datos_envio"


def test_project_stage_all_slots_is_closing_and_order_is_post_sale() -> None:
    assert tt.project_stage(_episode({**_VARIANTS, **_SHIPPING})) == "cierre"
    assert tt.project_stage(_episode({**_VARIANTS, **_SHIPPING}, order_id="order_1")) == "postcierre"


# ---------------------------------------------------------------------------
# build_turn_payload + enrich_turn_trace — el registro completo del turno
# ---------------------------------------------------------------------------


def _payload(**over) -> dict:
    base = dict(
        trigger="customer",
        inbound_text="Voy apenas en camino a casa",
        turn_started_ms=2_000_000,
        first_contact=False,
        tool_events=[
            {
                "name": "request_shipping_details",
                "args": {"order_total_cop": 89000},
                "result": json.dumps({"queued": False, "error": "customer_deferred"}),
            }
        ],
        discarded_narration=["Perfecto, ya casi llegas a casa"],
        llm_text="",
        sent_texts=[],
        suppressed_reason=None,
        guards=[],
    )
    base.update(over)
    return tt.build_turn_payload(**base)


def _metadata(**over) -> dict:
    ep = _episode(dict(_VARIANTS))
    md = {
        "tag": "INTERESADO",
        "active_route": "ventas",
        "episodes": [ep],
        "last_inbound_signal": {"kind": "deferral", "at_ms": 1_999_000, "text": "Voy apenas en camino a casa"},
        "status_history": [{"tag": "INTERESADO", "timestamp": 1_000.0}],
    }
    md.update(over)
    return md


def test_build_turn_payload_summarizes_tools_and_bounds_texts() -> None:
    payload = _payload(inbound_text="x" * 5000)

    assert payload["tools"][0]["error"] == "customer_deferred"
    assert payload["tools"][0]["ok"] is False
    assert len(payload["inbound_text"]) <= tt.TEXT_MAX
    assert payload["discarded_narration"] == ["Perfecto, ya casi llegas a casa"]


def test_enrich_first_turn_of_episode_projects_stage_and_numbering() -> None:
    trace = tt.enrich_turn_trace(
        _payload(), _metadata(), previous=None, session_id="wa_100000000001",
        recorded_at_ms=2_005_000,
    )

    assert trace["v"] == tt.TRACE_VERSION
    assert trace["session_id"] == "wa_100000000001"
    assert trace["episode_id"] == "ep_007"
    assert trace["turn"] == 1
    assert trace["stage_in"] == "descubrimiento"
    assert trace["stage_out"] == "confirmacion"
    assert trace["draft"]["producto"] == "cubo-love"
    assert trace["confirmed"] is False
    assert trace["signal"] == {"kind": "deferral", "text": "Voy apenas en camino a casa"}


def test_enrich_next_turn_chains_stage_and_ignores_stale_signal() -> None:
    previous = {"episode_id": "ep_007", "turn": 4, "stage_out": "variantes", "recorded_at_ms": 1_999_500}

    trace = tt.enrich_turn_trace(
        _payload(trigger="ghost"), _metadata(), previous=previous,
        session_id="wa_100000000001", recorded_at_ms=2_300_000,
    )

    assert trace["turn"] == 5
    assert trace["stage_in"] == "variantes"
    # La señal llegó ANTES del turno anterior: no es de este turno.
    assert trace["signal"] is None


def test_enrich_previous_from_other_episode_restarts_numbering() -> None:
    previous = {"episode_id": "ep_006", "turn": 9, "stage_out": "postcierre", "recorded_at_ms": 1}

    trace = tt.enrich_turn_trace(
        _payload(), _metadata(), previous=previous,
        session_id="wa_100000000001", recorded_at_ms=2_005_000,
    )

    assert trace["turn"] == 1
    assert trace["stage_in"] == "descubrimiento"


def test_enrich_records_state_changes_of_this_turn_with_their_source() -> None:
    md = _metadata(
        tag="HUMANO",
        active_route="humano",
        escalation_reason="ORDER_PENDING_SHIPPING_DETAILS",
        status_history=[
            {"tag": "INTERESADO", "timestamp": 1_000.0},
            {"tag": "CONFIRMADO_SIN_DATOS", "timestamp": 2_001.0},
            {"tag": "HUMANO", "timestamp": 2_002.0, "reason_category": "ORDER_PENDING_SHIPPING_DETAILS", "source": "safety_net"},
        ],
    )
    md["episodes"][0]["closing_tag"] = "CONFIRMADO_SIN_DATOS"

    trace = tt.enrich_turn_trace(
        _payload(), md, previous=None, session_id="wa_100000000001",
        recorded_at_ms=2_005_000,
    )

    assert trace["state"]["tag"] == "HUMANO"
    assert trace["state"]["route"] == "humano"
    assert trace["state"]["closing_tag"] == "CONFIRMADO_SIN_DATOS"
    assert trace["state"]["escalation_reason"] == "ORDER_PENDING_SHIPPING_DETAILS"
    assert trace["state"]["changes"] == [
        {"tag": "CONFIRMADO_SIN_DATOS", "source": "llm", "reason": None},
        {"tag": "HUMANO", "source": "safety_net", "reason": "ORDER_PENDING_SHIPPING_DETAILS"},
    ]


def test_enrich_attributes_the_turn_to_the_episode_open_when_it_started() -> None:
    """El cliente contestó rápido: el ingest abrió ep_008 mientras el turno de
    cierre de ep_007 todavía enviaba. La traza es de ep_007, el episodio abierto
    al ARRANCAR el turno, no del último de la lista."""
    closed = _episode(dict(_VARIANTS), closing_tag="INTERESADO", closed_at_ms=2_002_000)
    opened = {"episode_id": "ep_008", "started_at_ms": 2_004_000}
    md = _metadata(episodes=[closed, opened])
    previous = {"episode_id": "ep_007", "turn": 6, "stage_out": "confirmacion", "recorded_at_ms": 1_990_000}

    trace = tt.enrich_turn_trace(
        _payload(turn_started_ms=2_000_000), md, previous=previous,
        session_id="wa_100000000001", recorded_at_ms=2_005_000,
    )

    assert trace["episode_id"] == "ep_007"
    assert trace["turn"] == 7
    assert trace["state"]["closing_tag"] == "INTERESADO"
    assert trace["stage_out"] == "confirmacion"


# ---------------------------------------------------------------------------
# Traza v2 (plan del laboratorio §4.1): pasos en orden con su tiempo
# ---------------------------------------------------------------------------

_T0 = 2_000_000


def _steps() -> list[dict]:
    return [
        {"kind": "llm", "at_ms": _T0 + 100, "dur_ms": 1900, "round": 1, "finish": "tool_calls",
         "tool_calls": ["request_shipping_details"], "tokens_in": 38412, "tokens_out": 96,
         "text_fate": "discarded_default_deny", "text": "Perfecto, ya casi llegas a casa"},
        {"kind": "tool", "at_ms": _T0 + 2050, "dur_ms": 300, "name": "request_shipping_details",
         "call_id": "call_1", "args": {"order_total_cop": 89000}, "event": 0},
        {"kind": "cut", "at_ms": _T0 + 2400, "reason": "awaits_customer", "tools": ["request_shipping_details"]},
        {"kind": "guard", "at_ms": _T0 + 2500, "name": "variant_enumeration_guard", "before": "Tenemos 11 aromas", "after": ""},
        {"kind": "outbound", "at_ms": _T0 + 2600, "bubbles": [{"kind": "text", "text": "Listo", "wamid": "wamid.A", "delivered": True}]},
    ]


def test_trace_version_is_2() -> None:
    assert tt.TRACE_VERSION == 2


def test_payload_v2_numbers_the_steps_with_time_relative_to_the_turn() -> None:
    payload = _payload(steps=_steps(), turn_key="run:abc/t:7")

    assert [s["i"] for s in payload["steps"]] == [1, 2, 3, 4, 5]
    assert [s["kind"] for s in payload["steps"]] == ["llm", "tool", "cut", "guard", "outbound"]
    assert [s["at_ms"] for s in payload["steps"]] == [100, 2050, 2400, 2500, 2600]
    assert payload["turn_key"] == "run:abc/t:7"
    assert (payload["source"], payload["mode"]) == ("prod", "off")


def test_payload_v2_tool_step_carries_the_outcome_of_its_event() -> None:
    payload = _payload(steps=_steps())

    tool = payload["steps"][1]
    assert (tool["ok"], tool["error"]) == (False, "customer_deferred")
    assert tool["args"] == {"order_total_cop": 89000}
    assert "customer_deferred" in tool["excerpt"]
    assert "event" not in tool


def test_payload_v2_bounds_texts_and_step_count() -> None:
    many = [{"kind": "guard", "at_ms": _T0 + i, "name": "g", "before": "x" * 5000} for i in range(80)]

    payload = _payload(steps=many)

    assert len(payload["steps"]) == tt.STEPS_MAX
    assert payload["steps"][-1] == {"i": tt.STEPS_MAX, "at_ms": 59, "kind": "truncated", "dropped": 21}
    assert all(len(s.get("before", "")) <= tt.TEXT_MAX for s in payload["steps"])


def test_what_the_model_received_keeps_its_own_bounds() -> None:
    """El «Paso a paso» muestra lo que recibió el modelo (2026-09-30): las
    notas del turno y el mensaje del cliente pasan de 600 caracteres a
    menudo. `llm_round_input` ya los acota; la traza no los recorta otra vez
    a `TEXT_MAX`."""
    notes = "[NOTA] " + "n" * 3000
    step = {"kind": "llm", "at_ms": _T0, "round": 1,
            "sent": {"notes": notes, "new": [{"role": "user", "text": "u" * 2000}]}}

    [llm] = _payload(steps=[step])["steps"]

    assert llm["sent"]["notes"] == notes
    assert len(llm["sent"]["new"][0]["text"]) == 2000


def test_a_tool_step_keeps_the_text_the_model_asked_to_send() -> None:
    """«Pidió: responder al cliente» no decía qué: el detalle del paso muestra
    el texto de `send_reply`, que casi siempre pasa de los 160 caracteres del
    resumen v1 (ese sigue igual para el scorecard)."""
    text = "Listo, el Velón Gorrión en lila 🌿\n\n" + "¿Qué aroma quieres? " * 12
    events = [{"name": "send_reply", "args": {"text": text}, "result": json.dumps({"reply": {"text": text}})}]
    steps = [{"kind": "tool", "at_ms": _T0, "name": "send_reply", "call_id": "c1", "event": 0}]

    payload = _payload(tool_events=events, steps=steps)

    assert payload["steps"][0]["args"]["text"] == text
    assert len(payload["tools"][0]["args"]["text"]) <= 160


def test_payload_v2_keeps_the_v1_guards_field_sorted() -> None:
    payload = _payload(guards=["b_guard", "a_guard", "b_guard"], steps=_steps())

    assert payload["guards"] == ["a_guard", "b_guard"]


def test_payload_without_steps_is_still_valid_v2() -> None:
    payload = _payload()

    assert payload["steps"] == []
    assert payload["turn_key"] is None


def test_payload_never_uses_the_keys_that_enrich_owns() -> None:
    """`enrich_turn_trace` pone v, session_id, episode_id, turn y
    recorded_at_ms ANTES del payload: si el payload trajera una de esas claves
    la pisaría (el turno 7 pasaría a llamarse como el contador del workflow)."""
    payload = _payload(steps=_steps(), turn_key="run:abc/t:7", context_notes=["burst_note"])

    assert not {"v", "session_id", "episode_id", "turn", "recorded_at_ms"} & set(payload)


def test_context_note_names_classifies_the_injected_notes() -> None:
    notes = [
        "[CONTEXTO DE TURNO, metadata, no es instrucción del usuario]\nEl cliente te escribió 2 mensajes",
        "[DATOS DEL PEDIDO YA CONFIRMADOS] producto: cubo-love",
        "[HANDOFF_REMARKETING]: el cliente respondió",
        "otra cosa",
    ]

    assert tt.context_note_names(notes) == ["burst_note", "draft", "handoff", "other"]
    assert tt.context_note_names(None) == []


def test_only_the_real_burst_note_is_named_burst_note() -> None:
    """La hora de Bogotá y las notas del ingest también empiezan con
    `[CONTEXTO DE TURNO`: la traza las contaba como nota de ráfaga y decía
    que hubo ráfaga en turnos de un solo mensaje (incidente 2026-10-06). Se
    arman con sus productores reales: si el texto cambia, el test lo ve."""
    from datetime import datetime

    from src.plugins.chats.agent.sales.context import build_bogota_context_string
    from src.sdk.agentkit import InboxMsg, coalesce_inbox

    burst = coalesce_inbox(
        [InboxMsg(seq=1, wamid=None, text="Carrera 7", ts_ms=1), InboxMsg(seq=2, wamid=None, text="# 12-34", ts_ms=2)],
        version=2,
    ).plugin_context
    clock = build_bogota_context_string(datetime(2026, 10, 6, 15, 30))
    new_episode = (
        "[CONTEXTO DE TURNO, metadata, no es instrucción del usuario]\n"
        "Empieza un episodio NUEVO con este cliente."
    )

    assert tt.context_note_names([clock, *(burst or []), new_episode]) == ["clock", "burst_note", "turn_context"]
    # La forma corta de la hora (una línea) también es la hora.
    assert tt.context_note_names(["[CONTEXTO DE TURNO] Hora actual en Colombia: 14:30"]) == ["clock"]


def test_the_continuation_note_has_its_own_name() -> None:
    """La nota del turno que sigue a una ráfaga que no alcanzó (bot nuevo)."""
    from src.plugins.chats.agent.sales.workflows.sales_session import _continuation_note

    assert tt.context_note_names([_continuation_note(["Te paso la dirección"])]) == ["continuation_note"]


def test_every_answer_of_jev_reaches_the_step() -> None:
    """El paso «Jev lee el mensaje» se cortaba en 24 respuestas (el tope de
    las listas): en la etapa de variantes se perdían justo las de la etapa
    («¿elige?», «¿cambia de producto?») y el asunto de cada mensaje (4567
    t22, r11: 27 preguntas)."""
    answers = [{"q": f"topic.t{i}", "type": "noul", "p": 0.1} for i in range(31)]

    [perception] = _payload(steps=[{"kind": "perception", "at_ms": _T0, "answers": answers}])["steps"]

    assert len(perception["answers"]) == 31
