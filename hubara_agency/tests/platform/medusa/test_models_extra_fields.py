"""Extra fields en la respuesta de Medusa se ignoran sin levantar."""
from __future__ import annotations

from src.platform.medusa.models import MedusaProduct


def test_extra_field_is_ignored():
    payload = {
        "id": "p1",
        "title": "X",
        "handle": "x",
        "status": "published",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "weight": 100.0,
        "future_field_medusa_might_add": "ignored",
    }
    p = MedusaProduct.model_validate(payload)
    assert p.weight == 100.0
    assert not hasattr(p, "future_field_medusa_might_add")
