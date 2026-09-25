"""Señales deterministas del cliente sobre la compra (2026-09-14, runs
01a0a0eb / 01a0a0f1): "Voy apenas en camino a casa" es un APLAZAMIENTO, no
una confirmación. El LLM lo leyó como "siguiente paso: datos de envío" y
mandó el formulario. Estas funciones puras son la base de las guardas."""
from __future__ import annotations

import pytest

from src.plugins.chats.shared.purchase_signals import (
    detect_deferral,
    detect_purchase_affirmation,
    has_purchase_confirmation,
    is_current_inbound_deferral,
    register_inbound_purchase_signals,
)

NOW = 1_789_406_554_683


@pytest.mark.parametrize(
    "text",
    [
        "Voy apenas en camino a casa",
        "luego te digo",
        "Ahora no puedo, más tarde",
        "Déjame miro , x ahora me ocupe cita medica",
        "en un rato te escribo",
        "mañana te confirmo",
        "cuando llegue a la casa lo veo",
        "lo pienso y te aviso",
    ],
)
def test_deferral_phrases_are_detected(text: str) -> None:
    assert detect_deferral(text) is True


@pytest.mark.parametrize("text", ["sí, dale", "El primero azulito", "¿Dónde están ubicados?", "Opciones", "listo, lo quiero"])
def test_non_deferral_phrases_are_not_flagged(text: str) -> None:
    assert detect_deferral(text) is False


@pytest.mark.parametrize(
    "text",
    ["sí", "Si", "dale", "listo, así está bien", "lo quiero", "me lo llevo", "confirmo", "de una", "Dame 2", "quiero 3", "hágale"],
)
def test_purchase_affirmations_are_detected(text: str) -> None:
    assert detect_purchase_affirmation(text) is True


@pytest.mark.parametrize("text", ["Voy apenas en camino a casa", "¿cuánto vale?", "Aroma", "no, gracias", "sí pero luego te digo"])
def test_affirmation_not_detected_on_questions_deferrals_or_negations(text: str) -> None:
    # "sí pero luego" es aplazamiento: el aplazamiento gana.
    assert detect_purchase_affirmation(text) is False or detect_deferral(text) is True
    if detect_deferral(text):
        assert register_inbound_purchase_signals({}, text, now_ms=NOW, message_id="m") == "deferral"


def _metadata_with_draft(**slots: str) -> dict:
    return {
        "last_inbound_message_id": "wamid.prev",
        "episodes": [
            {"episode_id": "ep_001", "started_at_ms": NOW - 5000, "closed_at_ms": None,
             "order_draft": {"slots": slots, "updated_at_ms": NOW - 1000}},
        ],
    }


def test_affirmation_with_product_in_draft_confirms_the_purchase() -> None:
    md = _metadata_with_draft(producto="Cubo Love", color="Azul")
    kind = register_inbound_purchase_signals(md, "sí, déjalo en azul", now_ms=NOW, message_id="wamid.yes")
    assert kind == "affirmation"
    assert has_purchase_confirmation(md) is True
    draft = md["episodes"][0]["order_draft"]
    assert draft["confirmed_at_ms"] == NOW
    assert draft["confirmed_by"] == "text"
    assert md["last_inbound_signal"] == {"kind": "affirmation", "at_ms": NOW, "message_id": "wamid.yes", "text": "sí, déjalo en azul"}


def test_affirmation_without_product_in_draft_does_not_confirm() -> None:
    md = {"episodes": [{"episode_id": "ep_001", "closed_at_ms": None}]}
    assert register_inbound_purchase_signals(md, "sí", now_ms=NOW, message_id="m1") == "affirmation"
    assert has_purchase_confirmation(md) is False


def test_button_confirm_counts_as_confirmation() -> None:
    md = _metadata_with_draft(producto="Cubo Love")
    kind = register_inbound_purchase_signals(
        md, "[el cliente tocó el botón: ✅ Confirmar]", now_ms=NOW, message_id="m2",
        interactive={"type": "button_reply", "id": "order.confirm", "title": "✅ Confirmar"},
    )
    assert kind == "affirmation"
    assert md["episodes"][0]["order_draft"]["confirmed_by"] == "button"


def test_deferral_is_current_only_for_the_latest_inbound() -> None:
    md = _metadata_with_draft(producto="Cubo Love")
    register_inbound_purchase_signals(md, "Voy apenas en camino a casa", now_ms=NOW, message_id="wamid.defer")
    md["last_inbound_message_id"] = "wamid.defer"
    assert is_current_inbound_deferral(md) is True
    assert has_purchase_confirmation(md) is False
    md["last_inbound_message_id"] = "wamid.newer"
    assert is_current_inbound_deferral(md) is False


def test_registered_order_counts_as_confirmation() -> None:
    md = {"registered_order": {"success": True, "order_id": "o1"}, "episodes": []}
    assert has_purchase_confirmation(md) is True


def test_confirmation_is_episode_scoped() -> None:
    md = _metadata_with_draft(producto="Cubo Love")
    register_inbound_purchase_signals(md, "dale", now_ms=NOW, message_id="m")
    md["episodes"][0]["closed_at_ms"] = NOW + 10
    md["episodes"].append({"episode_id": "ep_002", "closed_at_ms": None})
    assert has_purchase_confirmation(md) is False


def test_a_question_that_starts_with_si_is_not_a_purchase_yes():
    """Prueba en vivo 2026-09-24: "Si tienes 2 de esa ?" (pregunta por la
    existencia) quedó registrado como confirmación de compra."""
    assert detect_purchase_affirmation("Si tienes 2 de esa ?") is False
    assert detect_purchase_affirmation("¿Sí hay en azul?") is False
    assert detect_purchase_affirmation("si hay de lavanda?") is False


def test_a_question_with_an_explicit_purchase_phrase_still_counts():
    assert detect_purchase_affirmation("¿me lo mandas? lo quiero") is True
    assert detect_purchase_affirmation("Sí, lo quiero") is True
    assert detect_purchase_affirmation("sí") is True
