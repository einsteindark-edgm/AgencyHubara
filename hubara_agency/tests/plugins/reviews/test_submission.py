"""La respuesta del formulario de calificación → reseña, premio y gracias.

Reglas del operador (2026-10-13): calificación por pedido; 5 % por calificar y
10 % si además comparte una foto y autoriza publicarla; el cupón se da con
cualquier número de estrellas y el enlace a Google va a todos (Google prohíbe
premiar su reseña o pedirla solo a los contentos).
"""
from __future__ import annotations

import pytest

from src.plugins.reviews.domain.submission import (
    author_display,
    order_id_from_flow_token,
    parse_review_response,
    reward_percent,
    review_flow_token,
    thanks_text,
)


def test_flow_token_round_trips_the_order_id():
    token = review_flow_token("order_01JABC", "n1")
    assert token == "review:order_01JABC:n1"
    assert order_id_from_flow_token(token) == "order_01JABC"


@pytest.mark.parametrize("token", ["shipping_wa_573001234567_1760000000", "", "review:", None])
def test_other_flow_tokens_are_not_reviews(token):
    assert order_id_from_flow_token(token) is None


def _response(**overrides):
    base = {
        "flow_token": "review:order_01JABC:n1",
        "rating": "5",
        "comment": "  El aroma llena toda la sala.  ",
        "photo": [{"id": "MEDIA_P1", "mime_type": "image/jpeg"}],
        "publish_consent": True,
    }
    base.update(overrides)
    return base


def test_parse_full_response():
    sub = parse_review_response(_response())
    assert sub is not None
    assert sub.order_id == "order_01JABC"
    assert sub.rating == 5
    assert sub.comment == "El aroma llena toda la sala."
    assert sub.photo_media_ids == ("MEDIA_P1",)
    assert sub.publish_consent is True


def test_parse_minimal_response_without_comment_photo_or_consent():
    sub = parse_review_response(
        {"flow_token": "review:order_01JABC:n1", "rating": "3", "comment": "", "publish_consent": False}
    )
    assert sub is not None
    assert (sub.rating, sub.comment, sub.photo_media_ids, sub.publish_consent) == (3, None, (), False)


def test_parse_accepts_consent_as_text_and_media_id_key():
    sub = parse_review_response(_response(publish_consent="true", photo=[{"media_id": "M2"}]))
    assert sub is not None
    assert sub.publish_consent is True
    assert sub.photo_media_ids == ("M2",)


@pytest.mark.parametrize("rating", ["0", "6", "cinco", None])
def test_invalid_rating_is_not_a_review(rating):
    assert parse_review_response(_response(rating=rating)) is None


def test_non_review_token_is_not_a_review():
    assert parse_review_response(_response(flow_token="shipping_wa_1_2")) is None


def test_reward_is_ten_only_with_photo_and_consent():
    assert reward_percent(parse_review_response(_response())) == 10
    assert reward_percent(parse_review_response(_response(publish_consent=False))) == 5
    assert reward_percent(parse_review_response(_response(photo=[]))) == 5


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Ana María Gómez", "Ana G."),
        ("  jorge   pérez ", "Jorge P."),
        ("Ana", "Ana"),
        ("", "Cliente de Hubara"),
        ("Cliente WhatsApp", "Cliente de Hubara"),
    ],
)
def test_author_display_is_first_name_and_last_initial(name, expected):
    assert author_display(name) == expected


def test_thanks_for_a_good_rating_mentions_coupon_expiry_and_google():
    text = thanks_text(rating=5, percent=5, expires_label="12 de diciembre")
    assert "5 estrellas" in text
    assert "5 %" in text
    assert "12 de diciembre" in text
    assert "Google" in text


def test_thanks_for_a_low_rating_promises_the_team_and_still_gives_coupon_and_google():
    text = thanks_text(rating=2, percent=10, expires_label="12 de diciembre")
    assert "equipo" in text
    assert "10 %" in text
    assert "Google" in text


def test_thanks_uses_tuteo_not_voseo():
    for rating in (1, 3, 5):
        text = thanks_text(rating=rating, percent=5, expires_label="1 de enero").lower()
        for vos in ("contanos", "escribinos", "tenés", "podés", "querés"):
            assert vos not in text


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("ana.gomez@example.com", "Cliente de Hubara"),
        ("</script><b>", "Cliente de Hubara"),
        ("Mamá 💕", "Mamá"),
        ("josé-luis o'neil", "José-luis O."),
        ("Cliente sin nombre", "Cliente de Hubara"),
    ],
)
def test_the_public_name_keeps_only_a_real_name(raw, shown):
    """Premortem: el nombre sale en la web (y en el JSON-LD de la ficha). Un
    correo, etiquetas o un relleno nunca salen; solo letras."""
    assert author_display(raw) == shown
