"""D1.6 — ``ReleaseThread``: devolver el hilo a Meta Business Agent.

Junta la política pura (``domain.release_policy``) con el port de Thread
Control y la sesión del vault (por canal 1, ``FilesystemMetadataStore``):

1. Guardas fail-closed: flag ``MBA_STANDBY_ENABLED`` (``mba_disabled``),
   lista cerrada de clientes (``customer_not_enabled``, ERROR), sesión
   existente (``session_unknown``).
2. Hechos: ``control_owner`` (lo escribe chats desde ``messaging_handovers``,
   D1.5), ``release_pending`` (nuestro último release exitoso es posterior a
   la última confirmación de dueño de Meta), y lo que el caller sabe
   (``order_registered``, ``agent_event_emitted``).
3. Si la política dice sí: ``release`` con ``to`` = el cliente,
   ``metadata`` = ``hubara:<trigger> <detalle>`` (Meta lo devuelve verbatim
   en el ``messaging_handovers`` de vuelta → trazabilidad).
4. Registro en ``metadata.json`` bajo ``store.update``: ``thread_control``
   (último pedido, ok/error) + evento ``release_requested`` en
   ``control_history`` (``source="thread_control"``). NUNCA toca
   ``control_owner``: el dueño lo confirma Meta. Un fallo (``rejected`` /
   ``unavailable`` / ``not_configured``) se registra y se devuelve en el
   outcome, nunca se levanta; NO deja el release como pendiente (se vuelve a
   intentar en el próximo trigger).

Riesgo §4 del roadmap ("olvidar release deja a MBA mudo"): ``thread_control``
en la sesión y ``GET .../control`` lo hacen visible; la alerta por hilos
demasiado tiempo en ``hubara`` es de D1.7.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from loguru import logger

from src.plugins.mba.adapters.thread_control import ThreadControlError, ThreadControlPort
from src.plugins.mba.domain.release_policy import ReleaseFacts, ReleaseTrigger, decide_release

__all__ = ["CONTROL_HISTORY_CAP", "ReleaseOutcome", "ReleaseThread"]

CONTROL_HISTORY_CAP = 50  # mismo tope que el escritor de chats (D1.5)
_SESSION_KEY_RE = re.compile(r"^wa_(\d{6,15})$")
_SOURCE = "thread_control"


@dataclass(frozen=True)
class ReleaseOutcome:
    session_key: str
    released: bool
    reason: str
    action_at_ms: int | None = None
    error: str | None = None


def _now_ms() -> int:
    return int(time.time() * 1000)


def _release_pending(data: dict[str, Any]) -> bool:
    tc = data.get("thread_control")
    if not isinstance(tc, dict) or tc.get("last_action") != "release" or not tc.get("last_ok"):
        return False
    at = tc.get("last_action_at_ms")
    confirmed = data.get("control_owner_updated_at_ms")
    return isinstance(at, int) and at > (confirmed if isinstance(confirmed, int) else 0)


class ReleaseThread:
    def __init__(
        self,
        *,
        metadata_store: Any,  # FilesystemMetadataStore (read/update)
        port: ThreadControlPort,
        is_customer_allowed: Callable[[str], bool],
        is_enabled: Callable[[], bool],
        phone_number_id_fallback: Callable[[], str],
        now_ms: Callable[[], int] = _now_ms,
    ) -> None:
        self._store = metadata_store
        self._port = port
        self._is_customer_allowed = is_customer_allowed
        self._is_enabled = is_enabled
        self._phone_fallback = phone_number_id_fallback
        self._now_ms = now_ms

    async def execute(
        self,
        session_key: str,
        trigger: ReleaseTrigger,
        *,
        order_registered: bool = False,
        agent_event_emitted: bool = False,
        metadata: str | None = None,
    ) -> ReleaseOutcome:
        if not self._is_enabled():
            return ReleaseOutcome(session_key, False, "mba_disabled")
        m = _SESSION_KEY_RE.fullmatch(session_key)
        if m is None:
            return ReleaseOutcome(session_key, False, "session_unknown")
        customer = m.group(1)
        if not self._is_customer_allowed(customer):
            logger.error("[mba.release] cliente FUERA de la lista cerrada: ***{} — nada que soltar", customer[-4:])
            return ReleaseOutcome(session_key, False, "customer_not_enabled")
        data = self._store.read(session_key)
        if not data:
            return ReleaseOutcome(session_key, False, "session_unknown")
        facts = ReleaseFacts(
            control_owner=data.get("control_owner") if isinstance(data.get("control_owner"), str) else None,
            order_registered=order_registered,
            agent_event_emitted=agent_event_emitted,
            release_pending=_release_pending(data),
        )
        decision = decide_release(trigger, facts)
        if not decision.release:
            logger.info("[mba.release] {} trigger={} → no ({})", session_key, trigger.value, decision.reason)
            return ReleaseOutcome(session_key, False, decision.reason)
        phone_number_id = data.get("phone_number_id") or self._phone_fallback()
        if not phone_number_id:
            logger.warning("[mba.release] {} sin phone_number_id (sesión ni WHATSAPP_PHONE_NUMBER_ID)", session_key)
            return ReleaseOutcome(session_key, False, "phone_number_id_missing")
        now_ms = self._now_ms()
        meta_str = f"hubara:{trigger.value}" + (f" {metadata}" if metadata else "")
        try:
            await self._port.release(phone_number_id=str(phone_number_id), to=customer, metadata=meta_str)
        except ThreadControlError as exc:
            error = f"{exc.status} {exc.detail}".strip() if exc.status else exc.detail
            logger.warning("[mba.release] {} trigger={} falló: {} {}", session_key, trigger.value, exc.kind, error)
            self._record(session_key, trigger, metadata, now_ms, error={
                "kind": exc.kind, "status": exc.status, "detail": exc.detail, "at_ms": now_ms, "trigger": trigger.value,
            })
            return ReleaseOutcome(session_key, False, exc.kind, action_at_ms=now_ms, error=error)
        self._record(session_key, trigger, metadata, now_ms, error=None)
        logger.info("[mba.release] {} trigger={} → release pedido a Meta", session_key, trigger.value)
        return ReleaseOutcome(session_key, True, decision.reason, action_at_ms=now_ms)

    def _record(self, session_key: str, trigger: ReleaseTrigger, metadata: str | None, now_ms: int,
                *, error: dict[str, Any] | None) -> None:
        def _mutate(data: dict[str, Any]) -> dict[str, Any] | None:
            if not data:
                return None  # lectura fresca vacía: no pisar la sesión con un dict a medias
            data["thread_control"] = {
                "last_action": "release",
                "last_action_at_ms": now_ms,
                "trigger": trigger.value,
                "metadata": metadata,
                "last_ok": error is None,
                "last_error": error,
            }
            if error is None:
                history = data.get("control_history")
                history = [h for h in history if isinstance(h, dict)] if isinstance(history, list) else []
                history.append({
                    "owner": data.get("control_owner"),
                    "kind": "release_requested",
                    "at_ms": now_ms,
                    "trigger": trigger.value,
                    "metadata": metadata,
                    "source": _SOURCE,
                })
                data["control_history"] = history[-CONTROL_HISTORY_CAP:]
            return data

        self._store.update(session_key, _mutate)
