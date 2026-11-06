"""Contrato (golden) contra respuestas reales capturadas de prod, seudonimizadas.

Para cada captura de `fixtures/ads/prod-2026-10-09/clean/` del plugin:
1. se reconstruyen los argumentos de la tool (rol + ventana + ids),
2. el gateway debe pedir EXACTAMENTE el `path` + `query` capturados,
3. y devolver en `structuredContent` EXACTAMENTE el body (agents → `{items}`).

Los fixtures viven en el repo del plugin (este repo es público: no se copian métricas
de prod). `HUBARA_ADS_FIXTURES` apunta al dataset; sin él, el módulo se salta.
Nunca se lee `raw/` (PII).
"""

import copy
import json
import os
from pathlib import Path
from typing import Any

import pytest

from hubara_gateway.backend import BackendResponse
from hubara_gateway.pii import _contract_fields
from hubara_gateway.tools import call_tool

_DATASET = "deepseekHarness/AcktosAgency/salesProductsBot/fixtures/ads/prod-2026-10-09/clean"


def _default_fixtures() -> Path:
    """El repo del plugin como hermano de AgencyHubara (vale en el checkout y en un worktree)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / _DATASET).is_dir():
            return parent / _DATASET
    return Path(_DATASET)


FIXTURES = Path(os.environ.get("HUBARA_ADS_FIXTURES") or _default_fixtures())

pytestmark = pytest.mark.skipif(
    not (FIXTURES / "manifest.json").is_file(), reason=f"fixtures del plugin no disponibles en {FIXTURES}"
)
assert FIXTURES.name == "clean", "los tests de contrato solo leen el dataset seudonimizado (clean/)"

ROLE_TO_TOOL = {
    "campaigns": "ads_get_campaigns",
    "adsets": "ads_get_adsets",
    "ads": "ads_get_ads",
    "conversations": "ads_get_conversations",
    "daily": "ads_get_daily",
    "creative": "ads_get_creative",
    "agent-referrals": "ads_get_agent_referrals",
    "meta-status": "ads_get_meta_status",
    "analysis-agents": "ads_get_analysis_agents",
    "analysis-runs": "ads_get_analysis_runs",
    "analysis-run": "ads_get_analysis_run",
}


def _entries() -> list[dict[str, Any]]:
    if not (FIXTURES / "manifest.json").is_file():
        return []
    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    return [e for e in manifest["entries"] if e["meta"]["role"] in ROLE_TO_TOOL]


def _args(entry: dict[str, Any]) -> dict[str, str]:
    meta = entry["meta"]
    args = {k: meta[k] for k in ("window", "campaign_id", "adset_id", "ad_id") if k in meta}
    if meta["role"] == "analysis-run":
        args["run_id"] = entry["path"].rsplit("/", 1)[-1]
    if meta["role"] == "analysis-runs" and entry["query"].get("limit") not in (None, "20"):
        # El plugin llama sin `limit` y confía en el default 20 del gateway.
        args["limit"] = entry["query"]["limit"]
    return args


class ReplayBackend:
    """Responde SOLO al request capturado; cualquier otro → 404 (y el test falla)."""

    def __init__(self, entry: dict[str, Any], body: Any) -> None:
        self.entry, self.body, self.seen = entry, body, []

    async def get(self, path: str, query: dict[str, str], *, on_behalf_of: str | None = None) -> BackendResponse:
        self.seen.append((path, query))
        if path == self.entry["path"] and query == self.entry["query"]:
            return BackendResponse(self.entry["status"], copy.deepcopy(self.body))
        return BackendResponse(404, {"detail": "not captured"})


@pytest.mark.parametrize("entry", _entries(), ids=lambda e: e["file"])
async def test_tool_reproduces_captured_request_and_body(entry: dict[str, Any]) -> None:
    body = json.loads((FIXTURES / entry["file"]).read_text())["body"]
    backend = ReplayBackend(entry, body)
    tool = ROLE_TO_TOOL[entry["meta"]["role"]]

    result = await call_tool(tool, _args(entry), backend, pii_allowed=True)

    assert backend.seen == [(entry["path"], entry["query"])]
    assert result.is_error is not True
    expected = {"items": body} if tool == "ads_get_analysis_agents" else body
    assert result.structured_content == expected
    assert json.loads(result.content[0].text) == expected


@pytest.mark.parametrize(
    "entry", [e for e in _entries() if e["meta"]["role"] == "conversations"], ids=lambda e: e["file"]
)
async def test_masked_conversations_keep_contract_shape(entry: dict[str, Any]) -> None:
    body = json.loads((FIXTURES / entry["file"]).read_text())["body"]

    result = await call_tool("ads_get_conversations", _args(entry), ReplayBackend(entry, body))

    masked = result.structured_content
    assert set(masked) == set(body)
    assert len(masked["conversations"]) == len(body["conversations"])
    for got, orig in zip(masked["conversations"], body["conversations"], strict=True):
        # Proyección sobre el contrato (allowlist): lo que el backend emite fuera del
        # contrato Zod (hoy order_status, order_value_cop, source_id) no viaja enmascarado.
        assert set(got) == set(orig) & _contract_fields()
        assert orig["phone_number"] not in result.content[0].text
