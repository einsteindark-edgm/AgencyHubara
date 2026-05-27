"""Tests PR-C — protocol-compliance de las tools del plugin chats / sub-agente sales.

Verifica que las tools migradas en PR-C (vivían en src.sales_whatsapp.tools
pre-refactor; hoy en src.plugins.chats.agent.sales.tools y src.platform.tools):

  * Implementan el protocolo `Tool` de exoclaw (atributos `name`,
    `description`, `parameters` planos; ningun `pydantic.Field`).
  * Heredan de `ToolBase` y exponen `execute_with_context` (no `execute` con
    `ctx` como kwarg).
  * Pasan `isinstance(_, Tool)` (Protocol runtime-checkable).
  * Funcionan a traves de `ToolRegistry.execute(name, params, ctx)`, que es la
    forma como `src.platform.temporal.activities:execute_tool` las invoca en
    produccion.
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

from src.platform.tools.routing import TransferToSalesAgentTool
from src.plugins.chats.agent.sales.tools.tags import ManageConversationTagTool


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
    # Sesión c4e3416f: añadimos CONFIRMADO_SIN_DATOS al enum para el caso
    # del cliente que confirma compra pero no completa datos de envío.
    # HU verificación humana de pago: CONFIRMADO_PAGO_PENDIENTE para el
    # caso operativo donde el LLM registró la orden pero no puede
    # confirmar el pago (sin pasarela).
    assert props["tag"]["enum"] == [
        "INTERESADO",
        "RECHAZO",
        "COMPRA_EXITOSA",
        "CONFIRMADO_SIN_DATOS",
        "CONFIRMADO_PAGO_PENDIENTE",
    ]
    assert tool.parameters["required"] == ["tag", "motivo"]
    assert hasattr(tool, "execute_with_context")
    schema = tool.to_schema()
    assert schema["function"]["name"] == "manage_conversation_tag"


def test_tools_have_no_pydantic_field() -> None:
    """Regression: en pre-PR-C las tools mezclaban `pydantic.Field(...)` con
    `class TransferToSalesAgentTool(Tool):`, lo cual rompia el protocolo."""
    import src.platform.tools.routing as routing_mod
    import src.plugins.chats.agent.sales.tools.tags as tags_mod

    for mod in (routing_mod, tags_mod):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "from pydantic" not in src, f"{mod.__name__} aun importa pydantic"
        assert "Field(" not in src, f"{mod.__name__} aun usa pydantic.Field"


# ---------------------------------------------------------------------------
# Dispatch via ToolRegistry — la forma como execute_tool las invoca
# ---------------------------------------------------------------------------


async def test_transfer_tool_dispatched_via_registry(tmp_path: Path) -> None:
    # PR8: `vault_dir=tmp_path` es CRITICO. Sin él, el tool escribe a
    # `WORKSPACE_VAULT_DIR` (default `./hubara_vault`) y contamina los
    # seed metadata commiteados al repo. Ver PLUGIN_REFACTOR_PLAN.md §8.
    registry = ToolRegistry()
    registry.register(TransferToSalesAgentTool(workspace=tmp_path, vault_dir=tmp_path))

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
    # PR8: ver comentario de test_transfer_tool_dispatched_via_registry.
    registry = ToolRegistry()
    registry.register(ManageConversationTagTool(workspace=tmp_path, vault_dir=tmp_path))

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
    registry.register(ManageConversationTagTool(workspace=tmp_path, vault_dir=tmp_path))

    raw = await registry.execute(
        "manage_conversation_tag",
        {"tag": "INVALIDO", "motivo": "test"},
        _ctx(),
    )
    assert raw.startswith("Error: Invalid parameters"), raw


async def test_tag_tool_rejects_missing_motivo(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(ManageConversationTagTool(workspace=tmp_path, vault_dir=tmp_path))

    raw = await registry.execute(
        "manage_conversation_tag",
        {"tag": "RECHAZO"},
        _ctx(),
    )
    assert raw.startswith("Error: Invalid parameters"), raw


async def test_transfer_tool_rejects_missing_resumen(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(TransferToSalesAgentTool(workspace=tmp_path, vault_dir=tmp_path))

    raw = await registry.execute(
        "transfer_to_sales_agent",
        {},
        _ctx(),
    )
    assert raw.startswith("Error: Invalid parameters"), raw


# ---------------------------------------------------------------------------
# Premortem FIX #1: CONFIRMADO_PAGO_PENDIENTE requiere register_order previo
# ---------------------------------------------------------------------------


async def test_tag_tool_rejects_confirmado_pago_pendiente_without_register_order(
    tmp_path: Path,
) -> None:
    """Si el LLM intenta marcar CONFIRMADO_PAGO_PENDIENTE sin que
    `register_order` haya escrito `metadata.registered_order.success=True`,
    la tool debe devolver error claro para que el LLM corrija el orden y
    NO escribir el tag (evita metadata inconsistente: tag dice "pago
    pendiente" pero no hay orden en Medusa para verificar)."""
    session_key = "wa_57300055555"
    # metadata.json existe pero SIN registered_order (caso bug del LLM)
    chat_dir = tmp_path / session_key
    chat_dir.mkdir(parents=True, exist_ok=True)
    (chat_dir / "metadata.json").write_text(
        json.dumps({"tag": "INTERESADO"}),
        encoding="utf-8",
    )

    registry = ToolRegistry()
    registry.register(
        ManageConversationTagTool(workspace=tmp_path, vault_dir=tmp_path)
    )

    raw = await registry.execute(
        "manage_conversation_tag",
        {
            "tag": "CONFIRMADO_PAGO_PENDIENTE",
            "motivo": "el LLM se adelantó",
        },
        _ctx(session_key),
    )
    payload = json.loads(raw)
    assert "error" in payload
    assert "precondition_failed" in payload["error"]
    assert "register_order" in payload["error"]

    # El tag NO se escribió — sigue siendo INTERESADO
    data = json.loads((chat_dir / "metadata.json").read_text(encoding="utf-8"))
    assert data["tag"] == "INTERESADO"


async def test_tag_tool_accepts_confirmado_pago_pendiente_when_registered(
    tmp_path: Path,
) -> None:
    """Happy path: si `metadata.registered_order.success=True` ya está,
    el LLM puede marcar CONFIRMADO_PAGO_PENDIENTE."""
    session_key = "wa_57300066666"
    chat_dir = tmp_path / session_key
    chat_dir.mkdir(parents=True, exist_ok=True)
    (chat_dir / "metadata.json").write_text(
        json.dumps(
            {
                "tag": "INTERESADO",
                "registered_order": {
                    "success": True,
                    "order_id": "draft_01XYZ",
                },
            }
        ),
        encoding="utf-8",
    )

    registry = ToolRegistry()
    registry.register(
        ManageConversationTagTool(workspace=tmp_path, vault_dir=tmp_path)
    )

    raw = await registry.execute(
        "manage_conversation_tag",
        {
            "tag": "CONFIRMADO_PAGO_PENDIENTE",
            "motivo": "Pedido draft_01XYZ por $50.000, transferencia",
        },
        _ctx(session_key),
    )
    payload = json.loads(raw)
    assert "error" not in payload
    assert "CONFIRMADO_PAGO_PENDIENTE" in payload["message"]

    data = json.loads((chat_dir / "metadata.json").read_text(encoding="utf-8"))
    assert data["tag"] == "CONFIRMADO_PAGO_PENDIENTE"
