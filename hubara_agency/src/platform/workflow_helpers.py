"""Helper compartido `run_agent_turn` para deduplicar `_run_turn` entre Sales y Remarketing.

# DETERMINISTIC: importado vía `workflow.unsafe.imports_passed_through()` desde
# cada workflow. Solo invoca `workflow.execute_activity` y manipula listas locales
# y dataclasses. Cero I/O directo, cero `time.time`, cero `random`.

Ademas extrae las **decisiones** que las tools emitan en su payload de respuesta:
las tools devuelven JSON con keys conocidas (`transfer_decision`,
`schedule_remarketing`); el helper las parsea a dataclasses (`TransferDecision`,
`ScheduleRemarketingDecision`) y las expone via `TurnResult` para que el workflow
ejecute la activity-dispatcher correspondiente (ADR-001).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from exoclaw_temporal.activities.conversation import build_prompt, record_turn
    from exoclaw_temporal.activities.llm import llm_chat
    from exoclaw_temporal.config import (
        BuildPromptInput,
        ExecuteToolInput,
        LLMChatInput,
        RecordTurnInput,
        SessionInput,
    )

    from src.platform.temporal.activities import execute_tool
    from src.platform.contracts import ScheduleRemarketingDecision, TransferDecision
    from src.platform.temporal.retry_policies import (
        _CONV_OPTIONS,
        _LLM_OPTIONS,
        _TOOL_OPTIONS,
    )


@dataclass
class PendingMessage:
    """DTO compartido para el queue de mensajes pendientes en cada workflow.

    Replicado del shape de `PendingMessage` que vivia inline en cada workflow.

    `plugin_context` (PR-D, opcion a): hueco para **datos volatiles del turno**
    — A-MEM contextual, snippets retrieved, motivo de la inyeccion sintetica.
    **NO es para identidad ni catalogo**: esos viven en el workspace canonico
    del agente (`workspace/{IDENTITY,SOUL,USER,TOOLS,AGENTS}.md` y
    `workspace/skills/<name>/SKILL.md`) y los lee `ContextBuilder` dentro de
    `build_prompt`. El path Sales pasa siempre `None`. El path Remarketing
    aun usa este campo para forwardear el legacy `shared_brain/*.md` hasta
    que su workspace migre a la layout DEHA. Cuando esa migracion aterrice,
    `plugin_context` puede quedarse vacio para Remarketing tambien — o
    repurposearse para A-MEM.

    El campo sobrevive en la signature del signal (`send_message`) por
    compatibilidad de replay (R-JSON, fixture v2 en sales / v1 en remarketing).
    Cambiar la signature del signal implica fixture bump (v3) y posiblemente
    drain operativo (ADR-009).
    """
    message: str
    media: list[str] | None = None
    plugin_context: list[str] | None = None


@dataclass
class TurnResult:
    """Resultado de un turno LLM-tool-LLM.

    Ademas del `final_content` y los `tools_used`, expone las decisiones que las
    tools dispararon (a interpretar por el workflow para llamar la activity
    dispatcher correspondiente).
    """
    final_content: str
    tools_used: list[str] = field(default_factory=list)
    transfer_decision: TransferDecision | None = None
    schedule_remarketing: ScheduleRemarketingDecision | None = None


def _try_parse_decision_payload(raw: str) -> dict[str, Any] | None:
    """Intenta parsear el resultado JSON de una tool que emitio una decision.

    Las tools que emiten decisiones (routing.py, tags.py) devuelven JSON con un
    campo `transfer_decision` o `schedule_remarketing`. Si el parse falla o el
    campo no existe, retorna None: la respuesta es texto plano para el LLM.
    """
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


async def run_agent_turn(
    session: SessionInput,
    msg: PendingMessage,
    fallback_plugin_context: list[str] | None = None,
) -> TurnResult:
    """Ejecuta un turno completo de LLM con tool-loop. Es invocado desde `@workflow.run`.

    `msg.plugin_context` y `fallback_plugin_context` cargan **datos volatiles
    del turno** (PR-D, opcion a): A-MEM, snippets retrieved, motivos. La
    identidad / catalogo del agente ya NO viaja por aqui — se lee desde
    `workspace/*.md` en `build_prompt` via `ContextBuilder`. PR-D global
    cleanup (ADR-2026-05-06-10): tanto Sales como Remarketing pasan siempre
    `None` a estos parametros (la migracion DEHA workspace de Remarketing
    elimino el `_brain_cache` y `load_remarketing_brain_activity`). El campo
    sobrevive en la signature como hueco para futuros datos volatiles.

    `fallback_plugin_context` se mantiene como parametro opcional por
    compatibilidad de signature (replay-safe). En el futuro podria
    repurposearse para A-MEM (long-term memory contextual del turno).
    """
    plugin_context = msg.plugin_context if msg.plugin_context else fallback_plugin_context

    messages: list[dict[str, Any]] = await workflow.execute_activity(
        build_prompt,
        BuildPromptInput(
            session_id=session.session_id,
            message=msg.message,
            channel=session.channel,
            chat_id=session.chat_id,
            llm=session.llm,
            workspace=session.workspace,
            media=msg.media,
            plugin_context=plugin_context,
        ),
        **_CONV_OPTIONS,  # type: ignore[arg-type]
    )
    initial_len = len(messages)

    iteration = 0
    final_content: str | None = None
    tools_used: list[str] = []
    transfer_decision: TransferDecision | None = None
    schedule_remarketing: ScheduleRemarketingDecision | None = None

    while iteration < session.llm.max_iterations:
        iteration += 1

        response = await workflow.execute_activity(
            llm_chat,
            LLMChatInput(
                messages=messages,
                llm=session.llm,
                tool_definitions_json=session.tool_definitions_json,
            ),
            **_LLM_OPTIONS,  # type: ignore[arg-type]
        )

        if response.has_tool_calls:
            messages = [*messages, response.to_assistant_message()]
            for tc in response.tool_calls:
                tools_used.append(tc.name)
                result = await workflow.execute_activity(
                    execute_tool,
                    ExecuteToolInput(
                        name=tc.name,
                        params=tc.arguments,
                        session_id=session.session_id,
                        channel=session.channel,
                        chat_id=session.chat_id,
                        workspace=session.workspace,
                    ),
                    **_TOOL_OPTIONS,  # type: ignore[arg-type]
                )

                # Intentar extraer decisiones del payload JSON (ADR-001).
                payload = _try_parse_decision_payload(result)
                if payload is not None:
                    if "transfer_decision" in payload and isinstance(payload["transfer_decision"], dict):
                        td = payload["transfer_decision"]
                        transfer_decision = TransferDecision(
                            session_id=str(td.get("session_id", session.session_id)),
                            target_route=str(td.get("target_route", "ventas")),
                            summary=td.get("summary"),
                        )
                    if "schedule_remarketing" in payload and isinstance(payload["schedule_remarketing"], dict):
                        sr = payload["schedule_remarketing"]
                        schedule_remarketing = ScheduleRemarketingDecision(
                            session_id=str(sr.get("session_id", session.session_id)),
                            motivo=str(sr.get("motivo", "")),
                            delay_seconds=int(sr.get("delay_seconds", 60)),
                        )

                messages = [
                    *messages,
                    {"role": "tool", "tool_call_id": tc.id, "name": tc.name, "content": result},
                ]
        else:
            final_content = response.content
            msg_dict: dict[str, Any] = {"role": "assistant", "content": final_content}
            if response.reasoning_content is not None:
                msg_dict["reasoning_content"] = response.reasoning_content
            if response.thinking_blocks:
                msg_dict["thinking_blocks"] = response.thinking_blocks
            messages = [*messages, msg_dict]
            break

    if final_content is None:
        final_content = f"Reached max iterations ({session.llm.max_iterations})."

    await workflow.execute_activity(
        record_turn,
        RecordTurnInput(
            session_id=session.session_id,
            new_messages=messages[initial_len:],
            llm=session.llm,
            workspace=session.workspace,
        ),
        **_CONV_OPTIONS,  # type: ignore[arg-type]
    )

    return TurnResult(
        final_content=final_content,
        tools_used=tools_used,
        transfer_decision=transfer_decision,
        schedule_remarketing=schedule_remarketing,
    )
