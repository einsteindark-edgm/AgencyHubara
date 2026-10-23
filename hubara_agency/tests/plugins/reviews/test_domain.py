"""Tests de DOMINIO de reviews (los de arquitectura los da el TCK)."""
from src.plugins.reviews.domain.logic import health_payload


def test_health_payload_shape() -> None:
    payload = health_payload()
    assert payload["plugin"] == "reviews"
    assert payload["status"] == "ok"
