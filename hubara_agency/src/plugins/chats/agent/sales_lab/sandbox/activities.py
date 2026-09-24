"""Activities del turno simulado (plan §3.3 y §3.6, PR 11).

El sandbox registra la MISMA lista del worker de ventas
(`workers/sales.py::SALES_ACTIVITIES`) y cambia, por nombre, solo las que
tienen efectos fuera del sandbox o no aplican a un turno aislado. Toda
activity de producción está en uno de los dos conjuntos; una nueva que no
esté hace fallar la corrida (`UnclassifiedActivityError`).

Reales: el turno (prompt, LLM, tools, historial, episodio, guardas, cierres)
escribe en el vault del sandbox. WhatsApp y CAPI también son reales: sin
llaves (el guard de la caja lo garantiza) el cliente de WhatsApp simula el
envío y CAPI se salta; el test de fugas lo verifica.

Reemplazadas:
  * `persist_turn_trace`: la real + avisa que el turno terminó (el driver
    corta ahí: es la última activity de todo turno);
  * `read_idle_timeout_seconds`: una hora (el ghosting no se simula);
  * `send_typing_indicator_activity`: no-op;
  * transferencias, remarketing, handoff y eventos entre agentes: se capturan
    y no arrancan nada;
  * `decide_ghosting_action` y `transcribe_audio_activity`: fallan en voz
    alta si algo las llama (no son parte de un turno del banco).
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

REAL_IN_SANDBOX = frozenset(
    {
        "apply_variant_enumeration_guard",
        "bootstrap_sales_session_activity",
        "build_first_contact_greeting",
        "build_prompt",
        "compute_bogota_context",
        "ensure_closing_escalation",
        "ensure_payment_pending_closure",
        "execute_tool",
        "flush_capi_outbox_activity",
        "flush_pending_ui_intents_activity",
        "get_active_episode_id",
        "llm_chat",
        "persist_assistant_message_activity",
        "read_and_clear_pending_handoff",
        "read_order_draft_note",
        "record_episode_llm_usage",
        "record_turn",
        "reset_llm_history_for_episode",
        "send_capi_event_activity",
        "send_whatsapp_message_activity",
    }
)

IDLE_TIMEOUT_S = 3600


class UnclassifiedActivityError(RuntimeError):
    """Una activity de producción que el sandbox no sabe cómo tratar."""


@dataclass
class SandboxCapture:
    """Lo que el turno simulado intentó hacer fuera del sandbox, y su fin."""

    effects: list[dict[str, Any]] = field(default_factory=list)
    trace_payload: dict[str, Any] | None = None
    turn_done: asyncio.Event = field(default_factory=asyncio.Event)


def _jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        from dataclasses import asdict

        return asdict(value)
    return value


def _fakes(capture: SandboxCapture, real: dict[str, Any]) -> dict[str, Callable[..., Any]]:
    def _record(name: str, *args: Any) -> None:
        capture.effects.append({"activity": name, "args": [_jsonable(a) for a in args]})

    @activity.defn(name="read_idle_timeout_seconds")
    async def read_idle_timeout_seconds(session_id: str) -> int:
        return IDLE_TIMEOUT_S

    @activity.defn(name="send_typing_indicator_activity")
    async def send_typing_indicator(session_id: str) -> None:
        return None

    @activity.defn(name="orchestration.dispatch_event")
    async def dispatch_event(envelope: dict) -> dict:
        _record("orchestration.dispatch_event", envelope)
        env = envelope if isinstance(envelope, dict) else {}
        return {
            "source_plugin": str(env.get("source_plugin") or "chats"),
            "source_worker": str(env.get("source_worker") or "sales"),
            "event_type": str(env.get("event_type") or ""),
            "matches": [],
            "no_matches": True,
        }

    @activity.defn(name="start_or_signal_sales_workflow")
    async def start_or_signal_sales_workflow(decision: dict) -> None:
        _record("start_or_signal_sales_workflow", decision)

    @activity.defn(name="schedule_remarketing_workflow")
    async def schedule_remarketing_workflow(decision: dict) -> None:
        _record("schedule_remarketing_workflow", decision)

    @activity.defn(name="write_pending_handoff")
    async def write_pending_handoff(session_id: str, summary: str) -> None:
        _record("write_pending_handoff", session_id, summary)

    @activity.defn(name="decide_ghosting_action")
    async def decide_ghosting_action() -> str:
        raise ApplicationError("sandbox: el ghosting no se simula (turno aislado del banco)", non_retryable=True)

    @activity.defn(name="transcribe_audio_activity")
    async def transcribe_audio(session_id: str) -> str:
        raise ApplicationError("sandbox: el audio no se transcribe en el laboratorio", non_retryable=True)

    persist_real = real.get("persist_turn_trace")

    @activity.defn(name="persist_turn_trace")
    async def persist_turn_trace(session_id: str, payload_json: str) -> bool:
        ok = await persist_real(session_id, payload_json) if persist_real is not None else True
        try:
            capture.trace_payload = json.loads(payload_json)
        except (TypeError, ValueError):
            capture.trace_payload = None
        capture.turn_done.set()
        return bool(ok)

    return {
        "read_idle_timeout_seconds": read_idle_timeout_seconds,
        "send_typing_indicator_activity": send_typing_indicator,
        "orchestration.dispatch_event": dispatch_event,
        "start_or_signal_sales_workflow": start_or_signal_sales_workflow,
        "schedule_remarketing_workflow": schedule_remarketing_workflow,
        "write_pending_handoff": write_pending_handoff,
        "decide_ghosting_action": decide_ghosting_action,
        "transcribe_audio_activity": transcribe_audio,
        "persist_turn_trace": persist_turn_trace,
    }


FAKED_IN_SANDBOX = frozenset(_fakes(SandboxCapture(), {}))


def sandbox_activities(
    production: Iterable[Any],
    *,
    capture: SandboxCapture,
    llm_chat: Any | None = None,
) -> list[Any]:
    """La lista del worker de ventas con los reemplazos del sandbox, en el
    mismo orden. `llm_chat` reemplaza al LLM real (tests)."""
    production = list(production)
    by_name = {a.__temporal_activity_definition.name: a for a in production}
    fakes = _fakes(capture, by_name)
    out: list[Any] = []
    for name, act in by_name.items():
        if name == "llm_chat" and llm_chat is not None:
            out.append(llm_chat)
        elif name in fakes:
            out.append(fakes[name])
        elif name in REAL_IN_SANDBOX:
            out.append(act)
        else:
            raise UnclassifiedActivityError(
                f"la activity {name!r} del worker de ventas no está clasificada en el sandbox del laboratorio "
                "(REAL_IN_SANDBOX o un fake en sales_lab/sandbox/activities.py)"
            )
    return out
