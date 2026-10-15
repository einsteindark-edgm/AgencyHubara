"""El dispatch de una tool del agente: lo que el LLM manda nunca tumba el turno.

Incidente 2026-10-15 (Gemini 3.5 Flash-Lite de respaldo): el LLM llamó
`present_products` con `intro_title`, que la tool no tiene. `ToolRegistry.execute`
de exoclaw valida `required` y tipos (`ToolBase.validate_params`) pero no las
claves de más: el argumento llegó a `execute_with_context(**params)`, la tool
lanzó `TypeError`, la activity `execute_tool` falló sus 2 intentos con los mismos
argumentos y el turno murió sin responderle al cliente.

Acá, antes de exoclaw:
  * lo que el schema de la tool no declara se descarta con un WARNING (tool,
    argumento, sesión) y la tool corre con lo demás;
  * si falta un argumento obligatorio o un tipo no sirve, la tool no corre y el
    modelo recibe el rechazo de las tools outbound (`queued: false` + `error`,
    ver `_rejected_by_tool` en `workflow_helpers.py`). El texto plano de exoclaw
    no alcanza: el corte de turno lo toma por «salió» y, con una tool que espera
    al cliente (`present_products`), el bot se callaba sin leer el error;
  * si la tool lanza `TypeError` (error de programación: revienta igual con los
    mismos argumentos en cada intento), no se reintenta: ERROR con traceback
    como alerta y el modelo lee el mismo rechazo y responde sin ella. Una
    activity `execute_tool` fallida tumba el workflow con el mensaje del cliente.
"""
from __future__ import annotations

import json
from typing import Any

from exoclaw.agent.tools.protocol import ToolContext
from exoclaw.agent.tools.registry import ToolRegistry
from loguru import logger


def _declared_args(tool: Any) -> set[str] | None:
    """Los argumentos que el schema de la tool declara (lo que el LLM ve).
    `None` si el schema no los enumera: entonces no se descarta nada."""
    schema = getattr(tool, "parameters", None)
    props = schema.get("properties") if isinstance(schema, dict) else None
    return set(props) if isinstance(props, dict) else None


def _refusal(error: str, message: str) -> str:
    """El rechazo de las tools outbound: nada le llegó al cliente."""
    return json.dumps({"queued": False, "error": error, "message": message}, ensure_ascii=False)


def tool_failed_result(name: str) -> str:
    """Lo que el modelo lee cuando la tool falló por dentro: aquí (`TypeError`)
    o en el workflow, agotados los reintentos de `execute_tool`
    (`run_agent_turn`)."""
    return _refusal(
        "tool_failed",
        f"No se envió nada: {name} falló por un error interno. "
        "No la vuelvas a llamar en este turno; responde al cliente sin ella.",
    )


async def dispatch_tool(
    registry: ToolRegistry, name: str, params: dict[str, Any], ctx: ToolContext
) -> str:
    tool = registry.get(name)
    if tool is None or not isinstance(params, dict):
        return await registry.execute(name, params, ctx)
    declared = _declared_args(tool)
    if declared is not None:
        for arg in (k for k in params if k not in declared):
            logger.warning(
                "🧹 [TOOL {}] argumento inventado descartado: {} session={}",
                name, arg, ctx.session_key,
            )
        params = {k: v for k, v in params.items() if k in declared}
    # Mismos pasos que `ToolRegistry.execute`; él los repite (idempotentes) y
    # hace la llamada.
    if hasattr(tool, "cast_params"):
        params = tool.cast_params(params)
    if hasattr(tool, "validate_params"):
        problems = "; ".join(tool.validate_params(params))
        if problems:
            logger.warning(
                "🧹 [TOOL {}] argumentos inválidos: {} session={}", name, problems, ctx.session_key
            )
            return _refusal(
                "invalid_arguments",
                f"No se envió nada: {name} no corrió. Argumentos inválidos: {problems}. "
                "Corrige los argumentos y vuelve a llamarla.",
            )
    try:
        return await registry.execute(name, params, ctx)
    except TypeError:
        logger.exception(
            "💥 [TOOL {}] TypeError (determinista: no se reintenta) session={}", name, ctx.session_key
        )
        return tool_failed_result(name)
