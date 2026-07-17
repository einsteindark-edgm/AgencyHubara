"""Resolución de audiencia de una campaña — puro, sobre (session_id, metadata)."""
from src.plugins.marketing.domain.campaigns import (
    customer_name_from_metadata,
    new_campaign,
    resolve_campaign_audience,
)


def _campaign(segments):
    campaign = new_campaign(campaign_id="mkt-1", name="Promo", now_ms=1)
    campaign["segments"] = segments
    return campaign


def test_resolve_campaign_audience_filtra_por_segmento_y_exclusiones() -> None:
    sessions = [
        ("wa_+571", {"tag": "COMPRA_EXITOSA"}),
        ("wa_+572", {"tag": "INTERESADO"}),
        ("wa_+573", {}),
        ("wa_+574", {"tag": "HUMANO"}),
        ("wa_+575", {"tag": "COMPRA_EXITOSA", "marketing_opt_out": True}),
    ]
    audience = resolve_campaign_audience(
        _campaign(["clientes", "interesados"]), sessions
    )
    assert [r.session_id for r in audience.recipients] == ["wa_+571", "wa_+572"]
    assert [r.segment for r in audience.recipients] == ["clientes", "interesados"]
    reasons = {s.session_id: s.reason for s in audience.skipped}
    assert reasons["wa_+573"] == "fuera_de_segmento"
    assert reasons["wa_+574"] == "excluido"
    assert reasons["wa_+575"] == "excluido"


def test_resolve_campaign_audience_sin_segmentos_no_manda_a_nadie() -> None:
    audience = resolve_campaign_audience(
        _campaign([]), [("wa_+571", {"tag": "COMPRA_EXITOSA"})]
    )
    assert audience.recipients == []


_HOUR = 60 * 60 * 1000
_NOW = 1_750_000_000_000


def test_resolve_campaign_audience_skipea_quiet_hours() -> None:
    sessions = [
        ("wa_+571", {"tag": "COMPRA_EXITOSA"}),
        ("wa_+572", {"tag": "COMPRA_EXITOSA"}),
    ]
    audience = resolve_campaign_audience(
        _campaign(["clientes"]),
        sessions,
        is_quiet_hours=lambda session_id: session_id == "wa_+572",
    )
    assert [r.session_id for r in audience.recipients] == ["wa_+571"]
    reasons = {s.session_id: s.reason for s in audience.skipped}
    assert reasons["wa_+572"] == "quiet_hours"


def test_resolve_campaign_audience_skipea_campana_reciente() -> None:
    recent = {"campaign_id": "mkt-otra", "sent_at_ms": _NOW - 3 * _HOUR}
    old = {"campaign_id": "mkt-vieja", "sent_at_ms": _NOW - 72 * _HOUR}
    sessions = [
        ("wa_+571", {"tag": "COMPRA_EXITOSA", "campaign_touches": [recent]}),
        ("wa_+572", {"tag": "COMPRA_EXITOSA", "campaign_touches": [old]}),
    ]
    audience = resolve_campaign_audience(
        _campaign(["clientes"]), sessions, now_ms=_NOW
    )
    # 48h de respiro entre campañas: el touch de hace 3h skipea, el de 72h no.
    assert [r.session_id for r in audience.recipients] == ["wa_+572"]
    reasons = {s.session_id: s.reason for s in audience.skipped}
    assert reasons["wa_+571"] == "campana_reciente"


def test_customer_name_from_metadata_filtra_placeholder() -> None:
    assert (
        customer_name_from_metadata(
            {"registered_order": {"customer_name": "Diana Marcela Rodríguez"}}
        )
        == "Diana"
    )
    assert (
        customer_name_from_metadata(
            {"registered_order": {"customer_name": "Cliente WhatsApp"}}
        )
        is None
    )
    assert customer_name_from_metadata({}) is None
