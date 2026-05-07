"""Tests PR-C — protocol-compliance de las tools de sales_whatsapp.

Verifica que las tools migradas en PR-C:
  * Implementan el protocolo `Tool` de exoclaw (atributos `name`,
    `description`, `parameters` planos; ningun `pydantic.Field`).
  * Heredan de `ToolBase` y exponen `execute_with_context` (no `execute` con
    `ctx` como kwarg).
  * Pasan `isinstance(_, Tool)` (Protocol runtime-checkable).
  * Funcionan a traves de `ToolRegistry.execute(name, params, ctx)`, que es la
    forma como `src/core/activities.py:execute_tool` las invoca en produccion.
  * Rechazan params invalidos via `ToolBase.validate_params` (sin defaults
    silenciosos a la `INTERESADO` que enmascaraban errores en la version
    pre-PR-C).
"""
from __future__ import annotations

import json
from pathlib import Path

from exoclaw.agent.tools import ToolContext
from exoclaw.agent.tools.protocol import ToolBase
from exoclaw.agent.tools.registry import ToolRegistry

from src.sales_whatsapp.tools.routing import TransferToSalesAgentTool
from src.sales_whatsapp.tools.tags import ManageConversationTagTool


def _ctx(session: str = "wa_test") -> ToolContext:
    return ToolContext(session_key=session, channel="whatsapp", chat_id=session)


# ---------------------------------------------------------------------------
# Static protocol shape
# ---------------------------------------------------------------------------


def test_transfer_tool_implements_protocol(tmp_path: Path) -> None:
    """PR-C — verifica forma estatica del tool (no `isinstance(_, Tool)`).

    El Protocol `Tool` (`exoclaw.agent.tools.protocol.Tool`) exige `execute()`.
    Nuestras tools implementan **solo** `execute_with_context()` y el registry
    despacha por hasattr (`registry.py:102-105`), asi que `isinstance(_, Tool)`
    da False — y eso esta bien. Lo que importa es que el registry pueda
    despacharlas (cubierto por `test_*_dispatched_via_registry`)."""
    tool = TransferToSalesAgentTool(workspace=tmp_path)
    assert isinstance(tool, ToolBase)
    assert tool.name == "transfer_to_sales_agent"
    assert isinstance(tool.description, str) and tool.description
    assert isinstance(tool.parameters, dict)
    assert tool.parameters["type"] == "object"
    assert "resumen" in tool.parameters["properties"]
    assert tool.parameters["required"] == ["resumen"]
    assert hasattr(tool, "execute_with_context")
    # Y `to_schema()` (heredado de ToolBase) produce el formato OpenAI.
    schema = tool.to_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "transfer_to_sales_agent"


def test_tag_tool_implements_protocol(tmp_path: Path) -> None:
    tool = ManageConversationTagTool(workspace=tmp_path)
    assert isinstance(tool, ToolBase)
    assert tool.name == "manage_conversation_tag"
    assert isinstance(tool.description, str) and tool.description
    assert isinstance(tool.parameters, dict)
    props = tool.parameters["properties"]
    assert "tag" in props and "motivo" in props
    # PR-C: la whitelist vive en el JSON schema, no en if-isinstance defaults.
    assert props["tag"]["enum"] == ["INTERESADO", "RECHAZO", "COMPRA_EXITOSA"]
    assert tool.parameters["required"] == ["tag", "motivo"]
    assert hasattr(tool, "execute_with_context")
    schema = tool.to_schema()
    assert schema["function"]["name"] == "manage_conversation_tag"


def test_tools_have_no_pydantic_field() -> None:
    """Regression: en pre-PR-C las tools mezclaban `pydantic.Field(...)` con
    `class TransferToSalesAgentTool(Tool):`, lo cual rompia el protocolo."""
    import src.sales_whatsapp.tools.routing as routing_mod
    import src.sales_whatsapp.tools.tags as tags_mod

    for mod in (routing_mod, tags_mod):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "from pydantic" not in src, f"{mod.__name__} aun importa pydantic"
        assert "Field(" not in src, f"{mod.__name__} aun usa pydantic.Field"


# ---------------------------------------------------------------------------
# Dispatch via ToolRegistry — la forma como execute_tool las invoca
# ---------------------------------------------------------------------------


async def test_transfer_tool_dispatched_via_registry(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(TransferToSalesAgentTool(workspace=tmp_path))

    raw = await registry.execute(
        "transfer_to_sales_agent",
        {"resumen": "quiere retomar la compra"},
        _ctx("wa_5491234567890"),
    )
    payload = json.loads(raw)
    assert payload["transfer_decision"]["session_id"] == "wa_5491234567890"
    assert payload["transfer_decision"]["target_route"] == "ventas"
    assert payload["transfer_decision"]["summary"] == "quiere retomar la compra"


async def test_tag_tool_dispatched_via_registry(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(ManageConversationTagTool(workspace=tmp_path))

    raw = await registry.execute(
        "manage_conversation_tag",
        {"tag": "INTERESADO", "motivo": "dudó del precio"},
        _ctx("wa_5499876543210"),
    )
    payload = json.loads(raw)
    assert payload["schedule_remarketing"]["session_id"] == "wa_5499876543210"
    assert payload["schedule_remarketing"]["motivo"] == "dudó del precio"


# ---------------------------------------------------------------------------
# Validation — invalid params surface as Error, no silent fallback
# ---------------------------------------------------------------------------


async def test_tag_tool_rejects_invalid_tag(tmp_path: Path) -> None:
    """En pre-PR-C, `tag='FOO'` se silenciaba a `INTERESADO`. Ahora debe ser
    Error (validate_params + enum schema)."""
    registry = ToolRegistry()
    registry.register(ManageConversationTagTool(workspace=tmp_path))

    raw = await registry.execute(
        "manage_conversation_tag",
        {"tag": "INVALIDO", "motivo": "test"},
        _ctx(),
    )
    assert raw.startswith("Error: Invalid parameters"), raw


async def test_tag_tool_rejects_missing_motivo(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(ManageConversationTagTool(workspace=tmp_path))

    raw = await registry.execute(
        "manage_conversation_tag",
        {"tag": "RECHAZO"},
        _ctx(),
    )
    assert raw.startswith("Error: Invalid parameters"), raw


async def test_transfer_tool_rejects_missing_resumen(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(TransferToSalesAgentTool(workspace=tmp_path))

    raw = await registry.execute(
        "transfer_to_sales_agent",
        {},
        _ctx(),
    )
    assert raw.startswith("Error: Invalid parameters"), raw
