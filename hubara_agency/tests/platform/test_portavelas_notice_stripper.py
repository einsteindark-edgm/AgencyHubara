"""`strip_portavelas_notice` — última línea DETERMINISTA del incidente
943e6bff: si el pedido no incluye portavelas, ninguna oración que hable del
portavelas sale al cliente, aunque el LLM la escriba igual.

Puro, stdlib-only: se ejecuta dentro del workflow sandbox de Temporal.
"""
from __future__ import annotations

from src.sdk.agentkit import strip_portavelas_notice

_FAREWELL_WITH_NOTICE = (
    "Listo, tu pedido quedó registrado 🤍 Al finalizar el pago del pedido se "
    "escogen los colores del portavelas, según disponibilidad. Gracias por "
    "elegir a Hubara."
)


def test_removes_the_portavelas_sentence_and_keeps_the_rest() -> None:
    out = strip_portavelas_notice(_FAREWELL_WITH_NOTICE)
    assert "portavela" not in out.lower()
    assert "pedido quedó registrado" in out
    assert "Gracias por elegir a Hubara" in out


def test_exact_incident_message_is_cleaned() -> None:
    """Texto literal recibido por el cliente en el run 943e6bff."""
    raw = (
        "Listo, tu pedido quedó registrado 🤍 Al finalizar el pago del pedido "
        "se escogen los colores del portavelas, según disponibilidad. Gracias "
        "por elegir a Hubara."
    )
    assert strip_portavelas_notice(raw) == (
        "Listo, tu pedido quedó registrado 🤍 Gracias por elegir a Hubara."
    )


def test_text_without_mention_is_untouched() -> None:
    raw = "Listo, tu pedido quedó registrado 🤍. Gracias por elegir a Hubara."
    assert strip_portavelas_notice(raw) == raw


def test_multiline_sentence_is_removed() -> None:
    raw = (
        "Listo, tu pedido quedó registrado 🤍\n"
        "Al finalizar el pago se escogen los colores del portavelas.\n"
        "Gracias por elegir a Hubara."
    )
    out = strip_portavelas_notice(raw)
    assert "portavela" not in out.lower()
    assert "pedido quedó registrado" in out
    assert "Gracias por elegir a Hubara" in out


def test_message_that_is_only_about_portavelas_becomes_empty() -> None:
    assert strip_portavelas_notice(
        "El color del portavelas se escoge al finalizar el pago."
    ) == ""


def test_none_and_empty_are_safe() -> None:
    assert strip_portavelas_notice(None) == ""
    assert strip_portavelas_notice("") == ""
