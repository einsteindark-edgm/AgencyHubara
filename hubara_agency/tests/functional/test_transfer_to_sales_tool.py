"""Functional smoke for TransferToSalesAgentTool — Pattern #1 (Tool).

Acts as both:
  (a) the "first ever working functional test" in this directory, so the
      pipeline gate has something to run while feature-1 is in flight, and
  (b) the canonical template for tool-level functional tests. Future feature
      tasks that introduce a new tool should copy this file structure.

Why this tool: it's the one we just moved from sales_whatsapp/ to platform/
(see commit history). Validating it end-to-end here doubles as a regression
test for that refactor.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.tools.routing import TransferToSalesAgentTool


def _ctx(session: str = "wa_functional_test") -> ToolContext:
    return ToolContext(session_key=session, channel="whatsapp", chat_id=session)


async def test_transfer_tool_returns_decision_payload(tmp_path: Path) -> None:
    """Acceptance: invoking the tool with a valid `resumen` returns a
    `transfer_decision` JSON envelope the workflow can route on, and writes
    the routing decision into the per-session metadata.json."""
    tool = TransferToSalesAgentTool(workspace=str(tmp_path), vault_dir=tmp_path)

    raw = await tool.execute_with_context(
        _ctx("wa_5491111111111"),
        resumen="cliente confirmó interés en retomar la compra",
    )
    payload = json.loads(raw)

    # 1. Envelope shape — workflow_helpers._try_parse_decision_payload depends on this.
    assert "transfer_decision" in payload
    assert payload["transfer_decision"]["session_id"] == "wa_5491111111111"
    assert payload["transfer_decision"]["target_route"] == "ventas"
    assert payload["transfer_decision"]["summary"] == "cliente confirmó interés en retomar la compra"

    # 2. Side effect — metadata.json persists the decision.
    metadata_path = tmp_path / "wa_5491111111111" / "metadata.json"
    assert metadata_path.exists(), "tool must persist metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["active_route"] == "ventas"
    assert metadata["tag"] == "RETOMA_VENTA"


async def test_transfer_tool_rejects_empty_resumen_via_registry(tmp_path: Path) -> None:
    """Acceptance: when invoked through the registry path the LLM uses
    (`registry.execute(name, params, ctx)`), the JSON schema validation
    (minLength=1) rejects an empty `resumen`. This is the realistic path —
    the LLM never calls `execute_with_context` directly."""
    from exoclaw.agent.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register(TransferToSalesAgentTool(workspace=str(tmp_path), vault_dir=tmp_path))

    # The registry returns an error STRING (not raises) — that's the
    # convention so the LLM sees it as a regular tool response and can
    # course-correct.
    result = await registry.execute(
        "transfer_to_sales_agent",
        {"resumen": ""},
        _ctx(),
    )
    assert "error" in result.lower() or "invalid" in result.lower(), (
        f"empty resumen should surface as an error to the LLM, got: {result!r}"
    )
