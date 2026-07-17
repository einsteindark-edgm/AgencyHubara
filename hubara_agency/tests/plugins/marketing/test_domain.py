"""Tests de DOMINIO de marketing (los de arquitectura los da el TCK)."""
import pytest

from src.plugins.marketing.domain.campaigns import segment_for_metadata
from src.plugins.marketing.domain.logic import health_payload


def test_health_payload_shape() -> None:
    payload = health_payload()
    assert payload["plugin"] == "marketing"
    assert payload["status"] == "ok"


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("COMPRA_EXITOSA", "clientes"),
        ("INTERESADO", "interesados"),
        ("CONFIRMADO_PAGO_PENDIENTE", "interesados"),
        ("NO_ETIQUETADO", "frios"),
        (None, "frios"),
    ],
)
def test_segment_for_metadata_mapea_tag_a_segmento(tag, expected) -> None:
    metadata = {"tag": tag} if tag is not None else {}
    assert segment_for_metadata(metadata) == expected


@pytest.mark.parametrize(
    "metadata",
    [
        {"tag": "HUMANO"},
        {"tag": "INTERESADO", "active_route": "humano"},
        {"tag": "COMPRA_EXITOSA", "marketing_opt_out": True},
    ],
)
def test_segment_for_metadata_excluye_humano_y_opt_out(metadata) -> None:
    assert segment_for_metadata(metadata) is None


class _FakeRateEntry:
    def __init__(self, micros):
        self.usd_micros_per_message = micros


class _FakeRateCard:
    def __init__(self, rates):
        self.rates = rates


def test_estimate_send_cost_usa_tarifa_marketing_del_rate_card() -> None:
    from src.plugins.marketing.domain.campaigns import estimate_send_cost

    card = _FakeRateCard({"marketing": _FakeRateEntry(12500)})
    estimate = estimate_send_cost(recipient_count=200, rate_card=card)
    assert estimate.unit_cost_usd_micros == 12500
    assert estimate.total_usd_micros == 2_500_000  # 200 × $0.0125 = $2.50


def test_estimate_send_cost_rate_card_sin_marketing_estima_cero() -> None:
    from src.plugins.marketing.domain.campaigns import estimate_send_cost

    estimate = estimate_send_cost(recipient_count=10, rate_card=_FakeRateCard({}))
    assert estimate.unit_cost_usd_micros == 0
    assert estimate.total_usd_micros == 0
