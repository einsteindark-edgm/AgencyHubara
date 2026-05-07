"""Tests unitarios de los policies de prompts (puros, sin Temporal)."""
from __future__ import annotations

from src.remarketing_whatsapp.prompts import build_remarketing_trigger
from src.sales_whatsapp.prompts import build_ghosting_prompt


def test_ghosting_prompt_mentions_required_tool() -> None:
    out = build_ghosting_prompt()
    assert "manage_conversation_tag" in out
    assert "INTERESADO" in out
    assert "RECHAZO" in out


def test_ghosting_prompt_is_stable() -> None:
    # Determinismo: dos llamadas devuelven exactamente el mismo string.
    assert build_ghosting_prompt() == build_ghosting_prompt()


def test_remarketing_trigger_includes_motivo_and_memory() -> None:
    out = build_remarketing_trigger("cliente pidió tiempo", " >>memoria<<")
    assert "cliente pidió tiempo" in out
    assert ">>memoria<<" in out
    assert "envío gratis" in out


def test_remarketing_trigger_works_with_empty_memory() -> None:
    out = build_remarketing_trigger("ghosting total", "")
    assert "ghosting total" in out
    # Empty memory aún produce una salida valida.
    assert len(out) > 100
