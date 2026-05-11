"""Preservacion de Decimal en el parseo de respuesta Medusa."""
from __future__ import annotations

from decimal import Decimal

from src.platform.medusa.models import MedusaProduct


_BASE_PAYLOAD = {
    "id": "p1",
    "title": "X",
    "handle": "x",
    "status": "published",
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
}


def test_amount_as_number_becomes_decimal():
    payload = {
        **_BASE_PAYLOAD,
        "variants": [{
            "id": "v1", "title": "u",
            "prices": [{"id": "pr1", "amount": 49.99, "currency_code": "usd"}],
        }],
    }
    p = MedusaProduct.model_validate(payload)
    assert p.variants[0].prices[0].amount == Decimal("49.99")


def test_amount_as_string_becomes_decimal():
    payload = {
        **_BASE_PAYLOAD,
        "variants": [{
            "id": "v1", "title": "u",
            "prices": [{"id": "pr1", "amount": "49.99", "currency_code": "usd"}],
        }],
    }
    p = MedusaProduct.model_validate(payload)
    assert p.variants[0].prices[0].amount == Decimal("49.99")
