from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from typing import Any
    from exoclaw_temporal.activities.conversation import build_prompt, record_turn
    from exoclaw_temporal.activities.llm import llm_chat
    from src.core.activities import execute_tool, send_whatsapp_message_activity
    from exoclaw_temporal.config import (
        BuildPromptInput,
        ExecuteToolInput,
        LLMChatInput,
        RecordTurnInput,
        SessionInput,
        TurnOutput,
    )

_CONTINUE_AS_NEW_AFTER_TURNS = 50
_LLM_OPTIONS = {
    "start_to_close_timeout": timedelta(minutes=5),
    "retry_policy": RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=2)),
}
_TOOL_OPTIONS = {
    "start_to_close_timeout": timedelta(minutes=10),
    "heartbeat_timeout": timedelta(seconds=30),
    "retry_policy": RetryPolicy(maximum_attempts=2, initial_interval=timedelta(seconds=1)),
}
_CONV_OPTIONS = {
    "start_to_close_timeout": timedelta(minutes=2),
    "retry_policy": RetryPolicy(maximum_attempts=5, initial_interval=timedelta(seconds=1)),
}

# Hubara Specific: 3 minutes to trigger Ghosting mechanism
_IDLE_TIMEOUT = timedelta(minutes=1)

@dataclass
class PendingMessage:
    message: str
    media: list[str] | None = None
    plugin_context: list[str] | None = None


@workflow.defn(name="HubaraSalesSessionWorkflow")
class HubaraSalesSessionWorkflow:
    """Long-running session workflow with ghosting injection mechanism."""

    def __init__(self) -> None:
        self._pending: list[PendingMessage] = []
        self._last_response: str | None = None
        self._processing = False

    @workflow.signal
    async def send_message(
        self,
        message: str,
        media: list[str] | None = None,
        plugin_context: list[str] | None = None,
    ) -> None:
        self._pending.append(
            PendingMessage(message=message, media=media, plugin_context=plugin_context)
        )

    @workflow.query
    def get_last_response(self) -> str | None:
        return self._last_response

    @workflow.query
    def is_processing(self) -> bool:
        return self._processing

    @workflow.run
    async def run(self, input: SessionInput) -> None:
        turn_count = input.turn_count

        while True:
            try:
                await workflow.wait_condition(
                    lambda: len(self._pending) > 0,
                    timeout=_IDLE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                workflow.logger.info(f"Ghosting detectado para sesión {input.session_id}. Inyectando trigger de auto-etiquetado.")
                ghost_trigger = "[SISTEMA]: El usuario no ha respondido nada nuevo durante bastante tiempo (Ghosting). Evalúa la conversación completa rápidamente. Tu tarea ES OBLIGATORIAMENTE usar la herramienta manage_conversation_tag marcándolo como INTERESADO si vimos alguna intención, o RECHAZO si era Spam o desinterés total. REGLA DE ORO: NO generes ninguna respuesta en crudo ni le dirijas la palabra textualmente al usuario, debes SOLO llamar a la herramienta en silencio y luego termina tus labores."
                
                self._pending.append(PendingMessage(message=ghost_trigger))
                
                # Flag to force exit workflow after this pending message is processed
                self._force_shutdown = True

            while self._pending:
                msg = self._pending.pop(0)
                self._processing = True

                try:
                    output = await self._run_turn(input, msg)
                    self._last_response = output.final_content
                    turn_count += 1
                    
                    if output.final_content and not getattr(self, '_force_shutdown', False):
                        # Evitamos enviar respuestas vacías o alucinar respuestas internas durante auto-cierres
                        from_number = input.session_id.replace("wa_", "")
                        await workflow.execute_activity(
                            send_whatsapp_message_activity,
                            args=[input.session_id, output.final_content],
                            start_to_close_timeout=workflow.timedelta(seconds=20),
                        )
                    
                    if getattr(self, '_force_shutdown', False):
                        workflow.logger.info(f"Auto-diagnóstico concluido. Apagando sesión {input.session_id} por abandono de usuario o transferencia.")
                        return
                        
                finally:
                    self._processing = False

            # continue_as_new to keep history bounded
            if turn_count >= _CONTINUE_AS_NEW_AFTER_TURNS and not self._pending:
                workflow.logger.info("Reached {} turns, continuing as new", _CONTINUE_AS_NEW_AFTER_TURNS)
                workflow.continue_as_new(
                    SessionInput(
                        session_id=input.session_id,
                        channel=input.channel,
                        chat_id=input.chat_id,
                        llm=input.llm,
                        workspace=input.workspace,
                        tool_definitions_json=input.tool_definitions_json,
                        turn_count=turn_count,
                    )
                )

    async def _run_turn(self, input: SessionInput, msg: PendingMessage) -> TurnOutput:
        messages: list[dict[str, Any]] = await workflow.execute_activity(
            build_prompt,
            BuildPromptInput(
                session_id=input.session_id,
                message=msg.message,
                channel=input.channel,
                chat_id=input.chat_id,
                llm=input.llm,
                workspace=input.workspace,
                media=msg.media,
                plugin_context=msg.plugin_context,
            ),
            **_CONV_OPTIONS,  # type: ignore[arg-type]
        )
        initial_len = len(messages)

        iteration = 0
        final_content: str | None = None
        tools_used: list[str] = []

        while iteration < input.llm.max_iterations:
            iteration += 1

            response = await workflow.execute_activity(
                llm_chat,
                LLMChatInput(
                    messages=messages,
                    llm=input.llm,
                    tool_definitions_json=input.tool_definitions_json,
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
                            session_id=input.session_id,
                            channel=input.channel,
                            chat_id=input.chat_id,
                            workspace=input.workspace,
                        ),
                        **_TOOL_OPTIONS,  # type: ignore[arg-type]
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
            final_content = f"Reached max iterations ({input.llm.max_iterations})."

        await workflow.execute_activity(
            record_turn,
            RecordTurnInput(
                session_id=input.session_id,
                new_messages=messages[initial_len:],
                llm=input.llm,
                workspace=input.workspace,
            ),
            **_CONV_OPTIONS,  # type: ignore[arg-type]
        )

        return TurnOutput(final_content=final_content, tools_used=tools_used)
