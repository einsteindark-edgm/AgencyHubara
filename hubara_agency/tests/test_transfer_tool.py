"""Tests de Fase 4 (ADR-001).

Verifica que `TransferToSalesAgentTool.execute` y `ManageConversationTagTool.execute`:
  * NO importan `temporal_client`,
  * NO llaman `start_workflow` ni `signal`,
  * devuelven JSON con la decision (`transfer_decision` / `schedule_remarketing`).
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

from src.domains.sales_whatsapp.tools.routing import TransferToSalesAgentTool
from src.domains.sales_whatsapp.tools.tags import ManageConversationTagTool


def _strip_python_comments(src: str) -> str:
    """Elimina comments y docstrings simples para que las heuristicas anti-temporal
    inspeccionen solo codigo ejecutable. Permite que ADR-001 siga documentado en
    el modulo sin romper los asserts."""
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
                # docstring suelto
                continue
            out.append(tokval + " ")
            prev_toktype = toktype
    except tokenize.TokenizeError:
        return src
    return "".join(out)


def test_routing_tool_module_does_not_import_temporal_client() -> None:
    src = inspect.getsource(TransferToSalesAgentTool)
    code = _strip_python_comments(src)
    # Heuristica: la tool no debe contener llamadas a start_workflow ni get_temporal_client.
    assert "get_temporal_client" not in code
    assert "start_workflow" not in code
    assert ".signal(" not in code

    # Y el modulo entero tampoco importa temporal_client (R-DIP).
    import src.domains.sales_whatsapp.tools.routing as routing_mod
    mod_code = _strip_python_comments(inspect.getsource(routing_mod))
    assert "get_temporal_client" not in mod_code
    assert "from src.core.temporal_client" not in mod_code


def test_tags_tool_module_does_not_import_temporal_client() -> None:
    import src.domains.sales_whatsapp.tools.tags as tags_mod
    mod_code = _strip_python_comments(inspect.getsource(tags_mod))
    assert "get_temporal_client" not in mod_code
    assert "from src.core.temporal_client" not in mod_code
    assert "start_workflow" not in mod_code


async def test_transfer_tool_returns_decision_payload(tmp_path: Path) -> None:
    workspace = tmp_path
    tool = TransferToSalesAgentTool(workspace=str(workspace))

    class _Ctx:
        session_key = "wa_5491111111111"
        channel = "whatsapp"
        chat_id = "wa_5491111111111"

    raw = await tool.execute(ctx=_Ctx(), resumen="quiere precio nuevo")
    payload = json.loads(raw)

    assert "transfer_decision" in payload
    assert payload["transfer_decision"]["session_id"] == "wa_5491111111111"
    assert payload["transfer_decision"]["target_route"] == "ventas"
    assert payload["transfer_decision"]["summary"] == "quiere precio nuevo"
    # metadata.json fue actualizado
    metadata = json.loads((workspace / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["active_route"] == "ventas"
    assert metadata["tag"] == "RETOMA_VENTA"


async def test_tags_tool_emits_schedule_remarketing_for_interesado(tmp_path: Path) -> None:
    workspace = tmp_path
    tool = ManageConversationTagTool(workspace=str(workspace))

    class _Ctx:
        session_key = "wa_5492222222222"
        channel = "whatsapp"
        chat_id = "wa_5492222222222"

    raw = await tool.execute(ctx=_Ctx(), tag="INTERESADO", motivo="cliente dudo del precio")
    payload = json.loads(raw)

    assert payload["schedule_remarketing"]["session_id"] == "wa_5492222222222"
    assert payload["schedule_remarketing"]["motivo"] == "cliente dudo del precio"
    assert payload["schedule_remarketing"]["delay_seconds"] == 60


async def test_tags_tool_no_decision_for_rechazo(tmp_path: Path) -> None:
    workspace = tmp_path
    tool = ManageConversationTagTool(workspace=str(workspace))

    class _Ctx:
        session_key = "wa_5493333333333"
        channel = "whatsapp"
        chat_id = "wa_5493333333333"

    raw = await tool.execute(ctx=_Ctx(), tag="RECHAZO", motivo="no le interesa")
    payload = json.loads(raw)

    assert "schedule_remarketing" not in payload
    assert "message" in payload
