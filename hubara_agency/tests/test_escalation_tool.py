"""Tests de `EscalateToHumanTool` (platform/tools/escalation.py).

Verifica que la tool:
  * NO importa `temporal_client`,
  * NO llama `start_workflow` ni `signal`,
  * Escribe `metadata.json` con `tag=HUMANO`, `active_route=humano`,
    `motivo=summary`, `escalation_reason=reason_category` y un append a
    `status_history`,
  * Devuelve JSON con `escalation_decision` listo para que el workflow
    helper lo parsee.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.tools.escalation import EscalateToHumanTool


def _strip_python_comments(src: str) -> str:
    """Mismo helper que test_transfer_tool: ignora docstrings/comments al
    inspeccionar codigo ejecutable."""
    import io
    import tokenize

    out: list[str] = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(src).readline)
        prev_toktype = tokenize.INDENT
        for tok in tokens:
            toktype, tokval = tok.type, tok.string
            if toktype == tokenize.COMMENT:
                continue
            if toktype == tokenize.STRING and prev_toktype in (
                tokenize.INDENT,
                tokenize.NEWLINE,
                tokenize.NL,
            ):
                continue
            out.append(tokval + " ")
            prev_toktype = toktype
    except tokenize.TokenizeError:
        return src
    return "".join(out)


def _ctx(session: str) -> ToolContext:
    return ToolContext(session_key=session, channel="whatsapp", chat_id=session)


def test_escalation_tool_does_not_import_temporal_client() -> None:
    import src.platform.tools.escalation as escalation_mod

    src = _strip_python_comments(inspect.getsource(escalation_mod))
    assert "get_temporal_client" not in src
    assert "from src.platform.temporal.client" not in src
    assert "start_workflow" not in src
    assert ".signal(" not in src


@pytest.mark.asyncio
async def test_escalation_tool_writes_metadata_and_emits_decision(tmp_path: Path) -> None:
    tool = EscalateToHumanTool(workspace=str(tmp_path), vault_dir=tmp_path)

    raw = await tool.execute_with_context(
        _ctx("wa_5491111111111"),
        reason_category="BULK_ORDER",
        summary="cliente quiere 50 velas para evento corporativo",
    )
    payload = json.loads(raw)

    # Envelope incluye la decision para que el workflow la consuma.
    assert "escalation_decision" in payload
    decision = payload["escalation_decision"]
    assert decision["session_id"] == "wa_5491111111111"
    assert decision["reason_category"] == "BULK_ORDER"
    assert decision["summary"] == "cliente quiere 50 velas para evento corporativo"
    assert "message" in payload

    # metadata.json se actualizo en el vault PER-SESION (no en el workspace
    # canonico, mismo patron que TransferToSalesAgentTool / ManageConversationTagTool).
    metadata_path = tmp_path / "wa_5491111111111" / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["active_route"] == "humano"
    assert metadata["tag"] == "HUMANO"
    assert metadata["motivo"] == "cliente quiere 50 velas para evento corporativo"
    assert metadata["escalation_reason"] == "BULK_ORDER"
    assert len(metadata["status_history"]) == 1
    entry = metadata["status_history"][0]
    assert entry["tag"] == "HUMANO"
    assert entry["active_route"] == "humano"
    assert entry["reason_category"] == "BULK_ORDER"
    assert isinstance(entry["timestamp"], float)


@pytest.mark.asyncio
async def test_escalation_tool_appends_to_existing_status_history(tmp_path: Path) -> None:
    """Si la sesion ya tenia status_history, lo extendemos sin perderlo."""
    session_id = "wa_5492222222222"
    metadata_dir = tmp_path / session_id
    metadata_dir.mkdir()
    metadata_path = metadata_dir / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            {
                "active_route": "ventas",
                "tag": "INTERESADO",
                "motivo": "preguntó por aromas",
                "status_history": [
                    {
                        "tag": "INTERESADO",
                        "motivo": "preguntó por aromas",
                        "active_route": "ventas",
                        "timestamp": 1.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    tool = EscalateToHumanTool(workspace=str(tmp_path), vault_dir=tmp_path)
    await tool.execute_with_context(
        _ctx(session_id),
        reason_category="POST_SALE_ISSUE",
        summary="cliente reporta vela rota",
    )

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["tag"] == "HUMANO"
    assert metadata["active_route"] == "humano"
    # El history conserva la entrada previa + agrega la nueva.
    assert len(metadata["status_history"]) == 2
    assert metadata["status_history"][0]["tag"] == "INTERESADO"
    assert metadata["status_history"][1]["tag"] == "HUMANO"
    assert metadata["status_history"][1]["reason_category"] == "POST_SALE_ISSUE"


@pytest.mark.asyncio
async def test_escalation_tool_rejects_invalid_reason_category(tmp_path: Path) -> None:
    """El JSON schema enum rechaza categorias fuera de la taxonomia."""

    tool = EscalateToHumanTool(workspace=str(tmp_path), vault_dir=tmp_path)
    # ToolBase.validate_params es el chequeo que dispara el dispatch real.
    # Una categoria inventada debe fallar el schema validation.
    err = tool.validate_params(
        {"reason_category": "INVENTED_CATEGORY", "summary": "x"}
    )
    assert err is not None
    # validate_params puede devolver str o list[str] segun la version de
    # exoclaw.agent.tools — normalizo antes de buscar.
    err_text = " ".join(err) if isinstance(err, list) else str(err)
    assert "reason_category" in err_text or "enum" in err_text.lower()


@pytest.mark.parametrize(
    "category",
    [
        "BULK_ORDER",
        "DISCOUNT_REQUEST",
        "WHOLESALE_B2B",
        "CORPORATE_EVENT",
        "CUSTOMIZATION",
        "POST_SALE_ISSUE",
        "SHIPPING_ISSUE",
        "HEALTH_SAFETY",
        "RITUAL_GUIDANCE",
        "INTERNATIONAL",
        "PAYMENT_EDGECASE",
        "CHECKOUT_VERIFY_FAILED",
        "EXPLICIT_REQUEST",
        "CATALOG_GAP",
        # Sesión c4e3416f — agregada para el caso "cliente confirmó pero no
        # completó datos de envío" (LLM lo combina con tag CONFIRMADO_SIN_DATOS).
        "ORDER_PENDING_SHIPPING_DETAILS",
        "OTHER",
    ],
)
def test_escalation_tool_accepts_each_valid_category(category: str, tmp_path: Path) -> None:
    tool = EscalateToHumanTool(workspace=str(tmp_path), vault_dir=tmp_path)
    err = tool.validate_params(
        {"reason_category": category, "summary": "valid summary"}
    )
    # validate_params devuelve [] o None para "sin errores" segun version.
    assert not err
