from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from temporalio import workflow
from temporalio.common import RetryPolicy

from exoclaw_temporal.config import (
    BuildPromptInput,
    ExecuteToolInput,
    LLMChatInput,
    RecordTurnInput,
    SessionInput,
    TurnOutput,
)

with workflow.unsafe.imports_passed_through():
    from typing import Any
    from exoclaw_temporal.activities.conversation import build_prompt, record_turn
    from exoclaw_temporal.activities.llm import llm_chat
    from src.core.activities import execute_tool, claim_conversation_routing, send_whatsapp_message_activity, read_workspace_memory_activity
    from src.core.registries import build_default_llm_config, build_workspace_config, get_base_tools_json, get_base_tools_registry
    from src.domains.sales_whatsapp import integrations as whatsapp_client


_CONTINUE_AS_NEW_AFTER_TURNS = 50
_IDLE_TIMEOUT = timedelta(hours=24)

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

@dataclass
class PendingMessage:
    message: str
    media: list[str] | None = None
    plugin_context: list[str] | None = None


def _load_remarketing_brain() -> list[str]:
    """Carga los md del Cerebro de Remarketing."""
    brain_dir = Path(__file__).parent.parent / "shared_brain"
    files = ["identity.md", "knowledge.md", "instructions.md"]
    context = []
    for f in files:
        filepath = brain_dir / f
        if filepath.exists():
            context.append(filepath.read_text(encoding="utf-8"))
    return context


@workflow.defn(name="RemarketingWorkflow")
class RemarketingSessionWorkflow:
    """Long-running session workflow for Remarketing."""

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
        """Signal a new message into the session from the Webhook."""
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
    async def run(self, session_id: str, motivo: str) -> None:
        workflow.logger.info(f"Activando Sesión Conversacional de Remarketing para session: {session_id}")

        llm = build_default_llm_config()
        ws = build_workspace_config(session_id)
        registry = get_base_tools_registry(Path(ws.path))
        
        # 1. Construir el Input estándar
        input_data = SessionInput(
            session_id=session_id,
            channel="whatsapp",
            chat_id=session_id,
            llm=llm,
            workspace=ws,
            tool_definitions_json=get_base_tools_json(registry),
            turn_count=0
        )

        # 2. Tomar el control del Router (PVC) invocando la Actividad
        await workflow.execute_activity(
            claim_conversation_routing,
            args=[input_data.workspace.path, "remarketing"],
            start_to_close_timeout=workflow.timedelta(seconds=15),
        )

        # 2.5 Leer el memory.md del PVC generado por el Agente de Ventas
        memory_context = await workflow.execute_activity(
            read_workspace_memory_activity,
            args=[session_id],
            start_to_close_timeout=workflow.timedelta(seconds=15),
        )

        # 3. Inyectar el trigger proactivo inicial con el contexto de memoria si existe
        system_trigger_msg = f"[SISTEMA INTERNO]: El cliente abortó o pausó la venta hace 48 horas. MOTIVO REGISTRADO: '{motivo}'.{memory_context} Tu tarea es generar inmediatamente un saludo de contacto proactivo ofreciéndole envío gratis como beneficio para revivir la venta. REDACTA EL MENSAJE COMO SI LE ESTUVIERAS HABLANDO DIRECTAMENTE A ÉL POR WHATSAPP."
        
        self._pending.append(PendingMessage(
            message=system_trigger_msg, 
            plugin_context=_load_remarketing_brain()
        ))

        turn_count = input_data.turn_count

        # 4. El Loop infinito idéntico a AgentSessionWorkflow
        while True:
            # Wait for a message or idle timeout
            try:
                await workflow.wait_condition(
                    lambda: len(self._pending) > 0,
                    timeout=_IDLE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                workflow.logger.info("Remarketing session idle timeout, exiting")
                return

            while self._pending:
                msg = self._pending.pop(0)
                self._processing = True

                try:
                    output = await self._run_turn(input_data, msg)
                    self._last_response = output.final_content
                    turn_count += 1
                    
                    # Intercepción proactiva: Si esto es la primera iteración, enviamos el primer saludo al cliente físicamente por WS.
                    # También si el Webhook nos contesta, ya lo enviamos. Actually, await send_whatsapp.
                    if output.final_content:
                        # Extraemos el numero de `wa_12345`
                        from_number = session_id.replace("wa_", "")
                        # Llamar a la Actividad segura en lugar de usar comandos HTTP imperativos o logger print
                        await workflow.execute_activity(
                            send_whatsapp_message_activity,
                            args=[session_id, output.final_content],
                            start_to_close_timeout=workflow.timedelta(seconds=20),
                        )
                        
                finally:
                    self._processing = False

                # continue_as_new to keep history bounded
                if turn_count >= _CONTINUE_AS_NEW_AFTER_TURNS and not self._pending:
                    # En remarketing raras veces pasará esto, pero para completitud
                    return

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
                plugin_context=msg.plugin_context if msg.plugin_context else _load_remarketing_brain(),
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
