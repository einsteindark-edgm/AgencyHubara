"""Testigo de huecos (2026-10-07): cuenta, después de enviar, lo que hoy nadie
frena y el scorecard no ve, para decidir si vale la pena arreglarlo.

Un turno del cliente = una línea (también los que no tienen huecos: son el
denominador). Sin textos del cliente ni del bot: solo qué pasó.
"""
from __future__ import annotations

from src.plugins.chats.agent.sales import huecos

SESSION = "wa_100000000001"


def _record(**over) -> dict:
    base = {
        "session_id": SESSION,
        "episode_id": "ep_003",
        "turn": 4,
        "recorded_at_ms": 1_759_800_000_000,
        "trigger": "customer",
        "workflow": "v2",
        "llm_text": "Claro, te ayudo",
        "sent_texts": ["Claro, te ayudo"],
        "tools": [],
        "guards": [],
    }
    base.update(over)
    return base


_VERIFIED_PHOTO = {
    "episodes": [{"episode_id": "ep_003", "started_at_ms": 1}],
    "recent_image_descriptions": [
        {"episode_id": "ep_003", "product": {"title": "Luz Serena", "handle": "luz-serena"}},
    ],
}


def test_every_customer_turn_is_counted_even_without_holes() -> None:
    line = huecos.witness_line(_record(), {})

    assert line is not None
    assert (line["session"], line["episode"], line["turn"], line["workflow"]) == (SESSION, "ep_003", 4, "v2")
    assert (line["suelto"], line["huecos"]) == (True, [])
    assert "Claro" not in str(line)  # sin textos


def test_a_loose_text_that_promises_to_come_back_later_is_a_hole() -> None:
    text = "Dame un momento y te confirmo"
    line = huecos.witness_line(_record(llm_text=text, sent_texts=[text]), {})

    assert line["huecos"] == ["texto_suelto_promete_volver"]


def test_the_same_text_through_send_reply_is_not_a_hole() -> None:
    text = "Dame un momento y te confirmo"
    tools = [{"name": "send_reply", "ok": True}]
    line = huecos.witness_line(_record(llm_text=text, sent_texts=[text], tools=tools), {})

    assert (line["suelto"], line["huecos"]) == (False, [])


def test_a_loose_denial_counts_only_when_the_photo_was_already_recognized() -> None:
    text = "Esa vela no la tenemos"
    rec = _record(llm_text=text, sent_texts=[text])

    assert huecos.witness_line(rec, _VERIFIED_PHOTO)["huecos"] == ["texto_suelto_niega_foto"]
    assert huecos.witness_line(rec, {})["huecos"] == []


def test_a_loose_list_that_the_guard_had_to_replace_is_a_hole() -> None:
    rec = _record(llm_text="Tenemos lavanda, vainilla, canela y coco", sent_texts=[],
                  guards=["variant_enumeration_guard"])

    assert huecos.witness_line(rec, {})["huecos"] == ["texto_suelto_lista"]


def test_an_unconsulted_claim_that_jev_saw_is_counted() -> None:
    rec = _record(claims={"capability": "afirmacion", "value": True, "by": "jev"})

    assert huecos.witness_line(rec, {})["huecos"] == ["afirmacion_sin_consultar"]


def test_ghost_and_complement_turns_are_not_customer_turns() -> None:
    assert huecos.witness_line(_record(trigger="ghost"), {}) is None
    assert huecos.witness_line(_record(trigger="complement"), {}) is None
