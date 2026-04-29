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


def test_routing_tool_module_does_not_import_temporal_client() -> None:
    src = inspect.getsource(TransferToSalesAgentTool)
    # Heuristica: la tool no debe contener llamadas a start_workflow ni get_temporal_client.
    assert "get_temporal_client" not in src
    assert "start_workflow" not in src
    assert ".signal(" not in src

    # Y el modulo entero tampoco importa temporal_client (R-DIP).
    import src.domains.sales_whatsapp.tools.routing as routing_mod
    mod_src = inspect.getsource(routing_mod)
    assert "get_temporal_client" not in mod_src
    assert "from src.core.temporal_client" not in mod_src


def test_tags_tool_module_does_not_import_temporal_client() -> None:
    import src.domains.sales_whatsapp.tools.tags as tags_mod
    mod_src = inspect.getsource(tags_mod)
    assert "get_temporal_client" not in mod_src
    assert "from src.core.temporal_client" not in mod_src
    assert "start_workflow" not in mod_src


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
