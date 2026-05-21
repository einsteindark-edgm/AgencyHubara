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
    from src.platform.contracts import (
        EscalationDecision,
        ScheduleRemarketingDecision,
        TransferDecision,
    )
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

    `is_handoff` (debounce/coalesce v1): marca un mensaje sintetico que NO
    debe entrar al rol "user" de la conversacion — en su lugar se mueve a
    `plugin_context` durante el coalesce. Lo usa el flujo Remarketing→Sales:
    el bootstrap (o el refresh per-iteration) lee `pending_handoff_summary`
    de metadata.json y seedea `_pending` con un marker. Default False
    mantiene replay-safety (mensajes pre-deploy deserializan al default).
    """
    message: str
    media: list[str] | None = None
    plugin_context: list[str] | None = None
    is_handoff: bool = False


@dataclass
class TurnResult:
    """Resultado de un turno LLM-tool-LLM.

    Ademas del `final_content` y los `tools_used`, expone las decisiones que las
    tools dispararon (a interpretar por el workflow para llamar la activity
    dispatcher correspondiente, o terminar el workflow en el caso de
    escalation).
    """
    final_content: str
    tools_used: list[str] = field(default_factory=list)
    transfer_decision: TransferDecision | None = None
    schedule_remarketing: ScheduleRemarketingDecision | None = None
    escalation_decision: EscalationDecision | None = None


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


def coalesce_pending(pending: list[PendingMessage]) -> PendingMessage:
    """Combina N mensajes pendientes en un solo `PendingMessage` para un turno.

    Reglas:
      * Mensajes con `is_handoff=True` NO entran al rol "user" — su contenido se
        mueve a `plugin_context` como `[HANDOFF_REMARKETING]: ...`. Asi no
        contaminan la conversacion JSONL ni el system prompt con prompts
        sinteticos como `[SISTEMA INTERNO]: ...`.
      * Mensajes reales del cliente se concatenan con `"\n"` (preservando orden
        de llegada). Asi una rafaga "Hola si" + "Me recuerdas cuanto vale X?"
        se procesa como un solo turno con ambos mensajes en contexto.
      * Si NO hay mensajes reales pero si handoff, se usa el ultimo handoff como
        mensaje principal — caso: el cliente engaga Remarketing con "Hola", el
        dispatcher seedea handoff, pero ningun mensaje real llega antes del
        debounce (Sales saluda proactivamente).
      * `plugin_context` existente de cualquier msg se preserva (concatena).
      * `media` se concatena (raro tener media en handoff; preservar de user).

    El `PendingMessage` resultante tiene `is_handoff=False` (ya consumido).
    """
    user_msgs = [p for p in pending if not p.is_handoff]
    handoff_msgs = [p for p in pending if p.is_handoff]

    plugin_ctx: list[str] = []
    for h in handoff_msgs:
        if h.message:
            plugin_ctx.append(f"[HANDOFF_REMARKETING]: {h.message}")
    for p in pending:
        if p.plugin_context:
            plugin_ctx.extend(p.plugin_context)

    media_combined: list[str] = []
    for p in pending:
        if p.media:
            media_combined.extend(p.media)

    if user_msgs:
        combined = "\n".join(p.message for p in user_msgs if p.message)
    elif handoff_msgs:
        combined = handoff_msgs[-1].message
    else:
        combined = ""

    return PendingMessage(
        message=combined,
        media=media_combined or None,
        plugin_context=plugin_ctx or None,
        is_handoff=False,
    )


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
    escalation_decision: EscalationDecision | None = None

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
                    if "escalation_decision" in payload and isinstance(payload["escalation_decision"], dict):
                        ed = payload["escalation_decision"]
                        escalation_decision = EscalationDecision(
                            session_id=str(ed.get("session_id", session.session_id)),
                            reason_category=str(ed.get("reason_category", "OTHER")),
                            summary=str(ed.get("summary", "")),
                        )

                messages = [
                    *messages,
                    {"role": "tool", "tool_call_id": tc.id, "name": tc.name, "content": result},
                ]
        else:
            # ── Empty-content recovery (post-mortem run df5a8fe2-bb7c-4627-b861-dc19643467be) ──
            # DeepSeek v4 (and other thinking-mode models) occasionally finishes
            # with `content=""` while putting the user-facing answer in
            # `reasoning_content`. With the previous code path, the empty content
            # propagated to `send_whatsapp_message_activity` which has a falsy
            # guard `if result.final_content` → message silenced, client
            # perceives the bot as "stuck thinking". After 60s the ghosting
            # timer fires and remarketing kicks in prematurely.
            #
            # Recovery: append a workflow-side nudge as a `system` turn and
            # retry ONE iteration of the LLM loop. We do NOT promote
            # `reasoning_content` directly to the client — reasoning channels
            # may contain meta-commentary ("Debo llamar la tool X...") which
            # would break the agent's human persona. If the retry still ends
            # empty, surface a natural human fallback that keeps the
            # conversation moving without sounding scripted.
            if (
                not response.content
                and response.reasoning_content
                and iteration < session.llm.max_iterations
            ):
                workflow.logger.warning(
                    "LLM returned empty content with reasoning_content present — "
                    "nudging model to emit the final answer in `content`",
                    extra={
                        "session_id": session.session_id,
                        "reasoning_len": len(response.reasoning_content),
                        "finish_reason": response.finish_reason,
                        "iteration": iteration,
                    },
                )
                # Inject a workflow-side nudge as a system-role reminder.
                # We do NOT echo reasoning_content into the prompt — that
                # could prime the model to repeat the same failure pattern.
                messages = [
                    *messages,
                    {
                        "role": "system",
                        "content": (
                            "Tu mensaje al cliente debe ir en el campo `content`, "
                            "no en el canal de razonamiento. Responde ahora al "
                            "cliente directamente, en español natural y cálido, "
                            "sin meta-comentarios sobre tu proceso de pensamiento."
                        ),
                    },
                ]
                # Continue the loop — next iteration retries llm_chat.
                continue

            final_content = response.content
            # Final empty-content fallback: model failed to recover even after
            # the nudge (or we hit max_iterations). Use a HUMAN-sounding
            # natural recovery line that mirrors what a real seller would say
            # when something hiccups. Critical: do NOT mention "bot", "system",
            # "error", "AI" — preserve the human persona.
            if not final_content:
                workflow.logger.error(
                    "LLM final response remained empty after recovery attempt — "
                    "using human-sounding fallback line",
                    extra={
                        "session_id": session.session_id,
                        "reasoning_len": (
                            len(response.reasoning_content)
                            if response.reasoning_content
                            else 0
                        ),
                        "iteration": iteration,
                    },
                )
                final_content = "¡Perdón! Justo se me cortó un segundito. ¿Me repetís lo que necesitabas?"

            msg_dict: dict[str, Any] = {"role": "assistant", "content": final_content}
            if response.reasoning_content is not None:
                msg_dict["reasoning_content"] = response.reasoning_content
            if response.thinking_blocks:
                msg_dict["thinking_blocks"] = response.thinking_blocks
            messages = [*messages, msg_dict]
            break

    if final_content is None:
        final_content = "¡Perdón! Justo se me cortó un segundito. ¿Me repetís lo que necesitabas?"

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
        escalation_decision=escalation_decision,
    )
