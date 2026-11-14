"""PII de `ads_get_conversations`: enmascarada por defecto, con el shape del contrato intacto.

Formato = `maskPhone`/`maskName` de `src/shared/views.ts` del plugin; `id` («wa_<tel>__<ep>»)
se reemplaza como hace el plugin (`conv-<episode_id|started_at_ms>`).
"""

import copy

from hubara_gateway.backend import BackendResponse
from hubara_gateway.tools import call_tool
from tests.test_tools import FakeBackend

CONV = {
    "id": "wa_573001234567__ep_001",
    "phone_number": "573001234567",
    "episode_id": "ep_001",
    "started_at_ms": 1760000000000,
    "last_msg_at_ms": None,
    "msgs_count": 4,
    "ad_headline": "Velas",
    "agent": "sales",
    "state": "ganado",
    "value": 120000,
    "duration_ms": None,
    "name": "Cliente Fb764 Pérez",
    "city": "Bogotá",
    "llm_cost_usd": 0.01,
    "llm_tokens": 900,
}
LEGACY = {**CONV, "id": "wa_573001112233", "phone_number": "573001112233", "episode_id": None, "name": None}
BODY = {"campaign_id": "c1", "conversations": [CONV, LEGACY], "orders_stale": False}


async def test_conversations_mask_phone_name_and_id_by_default() -> None:
    result = await call_tool("ads_get_conversations", {"campaign_id": "c1"}, FakeBackend(BackendResponse(200, copy.deepcopy(BODY))))

    first, legacy = result.structured_content["conversations"]
    assert first["phone_number"] == "•••••••4567"
    assert first["name"] == "C. F. P."
    assert first["id"] == "conv-ep_001"
    assert legacy["id"] == "conv-1760000000000"
    assert legacy["name"] is None
    assert first["city"] is None
    # Lo demás queda intacto y el shape sigue siendo el del contrato.
    pii = {"phone_number", "name", "id", "city"}
    assert {k: v for k, v in first.items() if k not in pii} == {k: v for k, v in CONV.items() if k not in pii}
    assert set(first) == set(CONV)
    assert "573001234567" not in result.content[0].text
    assert result.meta == {"hubara/pii_masked": True}


async def test_conversations_unmasked_when_pii_allowed() -> None:
    result = await call_tool(
        "ads_get_conversations",
        {"campaign_id": "c1"},
        FakeBackend(BackendResponse(200, copy.deepcopy(BODY))),
        pii_allowed=True,
    )

    assert result.structured_content == BODY
    assert result.meta == {"hubara/pii_masked": False}


async def test_short_phone_is_fully_masked() -> None:
    body = {"campaign_id": "c1", "conversations": [{**CONV, "phone_number": "123"}]}
    result = await call_tool("ads_get_conversations", {"campaign_id": "c1"}, FakeBackend(BackendResponse(200, body)))

    assert result.structured_content["conversations"][0]["phone_number"] == "••••"


async def test_masked_rows_are_projected_on_contract_fields_unknown_pii_never_leaks() -> None:
    """I2: enmascarar por exclusión deja pasar un campo nuevo con PII; se proyecta por allowlist."""
    row = {**CONV, "customer_email": "ana@example.com", "address": "Calle 1 # 2-3"}
    body = {"campaign_id": "c1", "conversations": [row], "orders_stale": False}

    masked = await call_tool("ads_get_conversations", {"campaign_id": "c1"}, FakeBackend(BackendResponse(200, copy.deepcopy(body))))
    admin = await call_tool(
        "ads_get_conversations", {"campaign_id": "c1"}, FakeBackend(BackendResponse(200, copy.deepcopy(body))), pii_allowed=True
    )

    got = masked.structured_content["conversations"][0]
    assert "customer_email" not in got and "address" not in got
    assert "ana@example.com" not in masked.content[0].text
    assert got["city"] is None
    assert admin.structured_content["conversations"][0]["customer_email"] == "ana@example.com"


async def test_delivery_neighborhood_is_hidden_like_the_city() -> None:
    """Auditoría 2026-11-10: `neighborhood` (#458) entró al contrato y salía sin tapar."""
    row = {**CONV, "neighborhood": "Chapinero Alto"}
    body = {"campaign_id": "c1", "conversations": [row], "orders_stale": False}

    masked = await call_tool("ads_get_conversations", {"campaign_id": "c1"}, FakeBackend(BackendResponse(200, body)))

    got = masked.structured_content["conversations"][0]
    assert got["neighborhood"] is None
    assert "Chapinero" not in masked.content[0].text


def test_every_contract_field_is_classified_as_safe_or_personal() -> None:
    """Un campo nuevo del contrato NO viaja hasta que alguien lo clasifique acá.

    La lista de campos seguros es explícita (no se deriva del schema generado): regenerar
    los schemas tras un cambio del dashboard no puede desclasificar datos personales.
    """
    from hubara_gateway.pii import PERSONAL_FIELDS, SAFE_FIELDS
    from hubara_gateway.schemas import output_schema

    contract = set(output_schema("ads_get_conversations")["properties"]["conversations"]["items"]["properties"])

    assert not (SAFE_FIELDS & PERSONAL_FIELDS)
    unclassified = contract - SAFE_FIELDS - PERSONAL_FIELDS
    assert not unclassified, (
        f"campos del contrato sin clasificar en hubara_gateway/pii.py: {sorted(unclassified)} — "
        "¿identifican o ubican a una persona? → PERSONAL_FIELDS (y cómo se tapa); si no → SAFE_FIELDS"
    )
    assert PERSONAL_FIELDS == {"phone_number", "name", "id", "city", "neighborhood"}


async def test_masked_rows_carry_only_classified_fields_even_if_the_contract_grows(monkeypatch) -> None:
    from hubara_gateway import pii

    monkeypatch.setattr(pii, "SAFE_FIELDS", pii.SAFE_FIELDS - {"ad_headline"})
    body = {"campaign_id": "c1", "conversations": [dict(CONV)], "orders_stale": False}

    masked = await call_tool("ads_get_conversations", {"campaign_id": "c1"}, FakeBackend(BackendResponse(200, body)))

    assert "ad_headline" not in masked.structured_content["conversations"][0]


async def test_masked_body_carries_only_known_top_level_fields() -> None:
    """Revisión 2026-11-10: el nivel superior del body viajaba tal cual (`{**body}`)."""
    body = {
        "campaign_id": "c1",
        "conversations": [dict(CONV)],
        "orders_stale": True,
        "debug_customers": [{"phone": "573001234567"}],
    }

    masked = await call_tool("ads_get_conversations", {"campaign_id": "c1"}, FakeBackend(BackendResponse(200, body)))

    assert set(masked.structured_content) == {"campaign_id", "conversations", "orders_stale"}
    assert masked.structured_content["orders_stale"] is True
    assert "573001234567" not in masked.content[0].text


def test_rows_that_are_not_objects_are_dropped_when_masking() -> None:
    from hubara_gateway.pii import mask_conversations_body

    body = {"campaign_id": "c1", "conversations": [dict(CONV), "wa_573001234567", 573001234567, None]}

    assert len(mask_conversations_body(body)["conversations"]) == 1
