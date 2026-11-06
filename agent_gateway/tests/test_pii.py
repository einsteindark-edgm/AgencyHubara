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
