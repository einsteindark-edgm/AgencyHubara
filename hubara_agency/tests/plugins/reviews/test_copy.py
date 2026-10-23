"""Lo que lee el cliente en la invitación a calificar (puro).

Decisión del operador (2026-10-13): la invitación dice el descuento «para que
la gente la abra» (plantilla de marketing fuera de la ventana; dentro, el
mismo texto como mensaje normal). Tuteo, sin voseo; la plantilla no saluda por
nombre (un parámetro vacío la tumba con 131008).
"""
from __future__ import annotations

from src.plugins.reviews.domain.copy import (
    expires_label,
    flow_action_data,
    invitation_body,
    items_summary,
    template_variables,
)

# 2026-12-12 15:00 Bogotá (20:00 UTC)
DEC_12 = 1_765_569_600_000


def test_items_summary_names_the_first_product_and_counts_the_rest():
    assert items_summary(["Velón Encanto Silvestre"]) == "Velón Encanto Silvestre"
    assert items_summary(["Velón A", "Vela B", "Vela C"]) == "Velón A y 2 más"
    assert items_summary([]) == "Tu pedido"


def test_invitation_mentions_order_discount_and_photo_bonus():
    body = invitation_body("#31")
    assert "#31" in body
    assert "5 %" in body
    assert "10 %" in body and "foto" in body


def test_invitation_uses_tuteo():
    body = invitation_body("#31").lower()
    for vos in ("contanos", "calificá", "tenés", "podés"):
        assert vos not in body


def test_template_variables_are_the_order_reference_only():
    assert template_variables("#31") == {"order_reference": "#31"}


def test_flow_action_data_feeds_the_first_screen():
    data = flow_action_data("#31", ["Velón A", "Vela B"])
    assert data == {
        "order_label": "Pedido #31",
        "items_summary": "Velón A y 1 más",
        "reward_note": "Al enviar te guardamos 5 % para tu próxima compra (10 % si compartes una foto y nos dejas publicarla).",
    }


def test_expires_label_is_the_bogota_day_in_spanish():
    assert expires_label(DEC_12) == "12 de diciembre"
