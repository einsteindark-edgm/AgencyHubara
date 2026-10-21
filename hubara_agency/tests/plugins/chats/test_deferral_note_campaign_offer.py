"""La nota del aplazamiento con la oferta de la campaña (operador, 2026-10-20).

Cuando el cliente aplaza, el bot responde una frase breve; si la campaña le
pasa la oferta del cupón, va en esa misma frase. Con una fecha de retoma, la
nota ya no dice «no prometas otra fecha» a secas: el cupón trae su propia
fecha límite y el bot la borraba o se confundía.
"""
from __future__ import annotations

from pathlib import Path

from src.plugins.chats.shared.purchase_signals import SIGNAL_KEY, build_deferral_note

_META = {"last_inbound_message_id": "wamid.X", SIGNAL_KEY: {"kind": "deferral", "message_id": "wamid.X", "text": "te aviso"}}


def test_the_offer_goes_in_the_same_brief_reply() -> None:
    plain = build_deferral_note(dict(_META))
    with_offer = build_deferral_note(dict(_META), campaign_offer=True)

    assert "oferta del cupón" not in plain
    assert with_offer.startswith(plain) and "oferta del cupón que te pasa el GUION DE LA CAMPAÑA" in with_offer


def test_the_resume_date_does_not_erase_the_date_of_the_coupon() -> None:
    note = build_deferral_note(dict(_META), resume_label="el lunes 26")

    assert "no prometas otra fecha para escribirle" in note


def test_the_remarketing_system_prompt_lets_the_campaign_coupon_through() -> None:
    """La regla absoluta de descuentos del remarketing (AGENTS.md) dice «ni del
    5 %»; sin la excepción, el toque de la oferta chocaba con ella."""
    agents = (Path(__file__).resolve().parents[3] / "src/plugins/chats/agent/remarketing/workspace/AGENTS.md").read_text(
        encoding="utf-8"
    )

    assert "NUNCA ofrezcas envío gratis, descuentos" in agents
    assert "La única excepción es el cupón que te pasa el sistema en el GUION DE LA CAMPAÑA" in agents
