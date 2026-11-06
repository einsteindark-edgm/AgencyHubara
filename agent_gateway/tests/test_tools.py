"""Tool call → GET al backend → CallToolResult, con el contrato que espera `McpAdsSource`.

`McpAdsSource` (plugin, `src/host/sources/mcp.ts:54-75`):
- `isError` + texto que matchea `/\\b404\\b|not[_ ]found/i` → resultado de dominio (404);
- cualquier otro `isError` → fallo de infraestructura;
- éxito → lee `structuredContent` (y desenvuelve `items` en analysis-agents).
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any

import pytest

from hubara_gateway.backend import BackendResponse, BackendUnavailable
from hubara_gateway.tools import call_tool

PLUGIN_DOMAIN_404 = re.compile(r"\b404\b|not[_ ]found", re.IGNORECASE)


@dataclass
class FakeBackend:
    response: BackendResponse | Exception
    calls: list[tuple[str, dict[str, str], str | None]] = field(default_factory=list)

    async def get(self, path: str, query: dict[str, str], *, on_behalf_of: str | None = None) -> BackendResponse:
        self.calls.append((path, query, on_behalf_of))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _text(result: Any) -> str:
    return "".join(c.text for c in result.content if c.type == "text")


async def test_call_tool_success_returns_exact_body_as_structured_content() -> None:
    body = {"campaigns": [], "orders_stale": False}
    backend = FakeBackend(BackendResponse(200, body))

    result = await call_tool("ads_get_campaigns", {"window": "7d"}, backend)

    assert backend.calls == [("/api/ads/campaigns", {"days": "7"}, None)]
    assert result.is_error is not True
    assert result.structured_content == body
    assert json.loads(_text(result)) == body


async def test_call_tool_analysis_agents_wraps_array_in_items() -> None:
    agents = [{"id": "ads-analytics", "label": "x", "description": "y", "example_input": "z"}]
    result = await call_tool("ads_get_analysis_agents", {}, FakeBackend(BackendResponse(200, agents)))

    assert result.structured_content == {"items": agents}


async def test_call_tool_backend_404_is_domain_error_for_the_plugin() -> None:
    backend = FakeBackend(BackendResponse(404, {"detail": "creative_unavailable"}))

    result = await call_tool("ads_get_creative", {"ad_id": "123"}, backend)

    assert result.is_error is True
    assert result.structured_content is None
    assert PLUGIN_DOMAIN_404.search(_text(result))
    assert "creative_unavailable" in _text(result)


@pytest.mark.parametrize("status", [400, 401, 403, 422, 500, 502, 503])
async def test_call_tool_backend_non_404_error_is_infra_error_for_the_plugin(status: int) -> None:
    backend = FakeBackend(BackendResponse(status, {"detail": "boom"}))

    result = await call_tool("ads_get_meta_status", {}, backend)

    assert result.is_error is True
    assert not PLUGIN_DOMAIN_404.search(_text(result))
    assert str(status) in _text(result)


async def test_call_tool_backend_unreachable_is_infra_error() -> None:
    backend = FakeBackend(BackendUnavailable("timeout"))

    result = await call_tool("ads_get_meta_status", {}, backend)

    assert result.is_error is True
    assert "backend_unavailable" in _text(result)
    assert not PLUGIN_DOMAIN_404.search(_text(result))


async def test_call_tool_bad_arguments_never_reach_the_backend() -> None:
    backend = FakeBackend(BackendResponse(200, {}))

    result = await call_tool("ads_get_adsets", {"window": "30d"}, backend)

    assert backend.calls == []
    assert result.is_error is True
    assert 'missing parameter "campaign_id"' in _text(result)
    assert not PLUGIN_DOMAIN_404.search(_text(result))


async def test_call_tool_unknown_tool_is_error_without_backend_call() -> None:
    backend = FakeBackend(BackendResponse(200, {}))

    result = await call_tool("ads_set_campaign_status", {}, backend)

    assert backend.calls == []
    assert result.is_error is True
    assert "unknown tool" in _text(result)


async def test_call_tool_non_json_body_is_infra_error() -> None:
    backend = FakeBackend(BackendResponse(200, None))

    result = await call_tool("ads_get_meta_status", {}, backend)

    assert result.is_error is True
    assert not PLUGIN_DOMAIN_404.search(_text(result))


async def test_call_tool_forwards_subject_only_as_audit_header() -> None:
    backend = FakeBackend(BackendResponse(200, {"connected": False}))

    await call_tool("ads_get_meta_status", {}, backend, subject="user-sub-123")

    assert backend.calls[0][2] == "user-sub-123"


@pytest.mark.parametrize("detail", ["Not Found", "upstream 404 from meta", "token not_found"])
async def test_call_tool_non_404_error_detail_cannot_masquerade_as_domain_404(detail: str) -> None:
    backend = FakeBackend(BackendResponse(500, {"detail": detail}))

    result = await call_tool("ads_get_meta_status", {}, backend)

    assert result.is_error is True
    assert not PLUGIN_DOMAIN_404.search(_text(result))


async def test_call_tool_argument_error_echo_cannot_masquerade_as_domain_404() -> None:
    result = await call_tool("ads_get_campaigns", {"window": "not found"}, FakeBackend(BackendResponse(200, {})))

    assert result.is_error is True
    assert not PLUGIN_DOMAIN_404.search(_text(result))
