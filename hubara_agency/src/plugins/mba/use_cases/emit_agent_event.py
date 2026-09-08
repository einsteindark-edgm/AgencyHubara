"""D1.9 — ``EmitAgentEvent``: contarle a Meta Business Agent una novedad.

Con MBA al frente, si Hubara manda el aviso de estado del pedido por Cloud
API toma el hilo (§0.5) y MBA queda mudo hasta el ``release``. En vez de
eso, el productor (hoy el ETA; D1.10 el cierre de episodio) le pasa la
novedad a MBA por ``agent_event`` y MBA se la cuenta al cliente.

1. Guardas fail-closed: flag ``MBA_STANDBY_ENABLED`` (``mba_disabled``),
   lista cerrada (``customer_not_enabled``, ERROR), sesión existente
   (``session_unknown``), tipo del catálogo (``unknown_event_type``) y
   **MBA controla el hilo** (``hubara_controls``: si responde Hubara o un
   humano, el aviso lo manda Hubara como siempre).
2. Dedupe por (``type``, ``order_id``) contra ``agent_events[]``: un evento
   ``accepted`` o ``ambiguous`` (Meta pudo aceptarlo) no se repite
   (``already_emitted``); un ``rejected`` / ``unavailable`` sí deja reintentar.
   Sin ``order_id`` no hay clave (p.ej. ``episode_closed`` por episodio).
   El chequeo y la reserva son UNA escritura bajo el flock: antes de llamar
   a Meta se anota ``status="pending"`` (dos emisiones concurrentes — p.ej.
   la activity del ETA vencida y su retry en paralelo — ven la reserva de
   la otra y devuelven ``already_emitted``); ``pending`` vence a los
   ``PENDING_TTL_MS`` por si el proceso murió entre la reserva y el POST.
3. ``entity_id`` = ``phone_number_id`` de la sesión (lo escribe el
   ``standby``) o el configurado (``WHATSAPP_PHONE_NUMBER_ID``); ``to`` = el
   cliente en E.164.
4. Registro bajo ``store.update`` en ``agent_events[]`` (cap 50): tipo,
   pedido, resultado, id de Meta, error. Nunca toca ``control_owner`` ni el
   resto de la sesión. Un fallo se registra y se devuelve, no se levanta.
   Si Meta aceptó pero el vault no pudo anotarlo: ``recorded=False`` + ERROR.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from loguru import logger

from src.plugins.mba.adapters.agent_event import AgentEventError, AgentEventPort
from src.plugins.mba.domain.agent_events import AGENT_EVENT_TYPES, build_description

__all__ = ["AGENT_EVENTS_CAP", "PENDING_TTL_MS", "AgentEventOutcome", "EmitAgentEvent"]

AGENT_EVENTS_CAP = 50
#: Una reserva ``pending`` más vieja que esto (proceso muerto entre la reserva
#: y el POST) deja de bloquear el dedupe. Mayor que el peor caso del adapter
#: (≈13,5 s) y que el hop del ETA (15 s).
PENDING_TTL_MS = 60_000
_SESSION_KEY_RE = re.compile(r"^wa_(\d{6,15})$")
#: Resultados que cuentan como "MBA (quizá) ya lo tiene" para el dedupe.
_DELIVERED_STATUSES = ("accepted", "ambiguous")


@dataclass(frozen=True)
class AgentEventOutcome:
    session_key: str
    emitted: bool
    reason: str
    agent_event_id: str | None = None
    at_ms: int | None = None
    error: str | None = None
    recorded: bool = True


def _now_ms() -> int:
    return int(time.time() * 1000)


def _events(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = data.get("agent_events")
    return [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []


def _already_emitted(
    data: dict[str, Any], event_type: str, order_id: str | None, now_ms: int
) -> dict[str, Any] | None:
    if not order_id:
        return None
    for e in reversed(_events(data)):
        if e.get("type") != event_type or e.get("order_id") != order_id:
            continue
        status = e.get("status")
        if status in _DELIVERED_STATUSES:
            return e
        at = e.get("at_ms")
        if status == "pending" and isinstance(at, int) and now_ms - at < PENDING_TTL_MS:
            return e
    return None


class EmitAgentEvent:
    def __init__(
        self,
        *,
        metadata_store: Any,  # FilesystemMetadataStore (read/update)
        port: AgentEventPort,
        is_customer_allowed: Callable[[str], bool],
        is_enabled: Callable[[], bool],
        controls_thread: Callable[[dict[str, Any], str], bool],
        entity_id_fallback: Callable[[], str],
        now_ms: Callable[[], int] = _now_ms,
    ) -> None:
        self._store = metadata_store
        self._port = port
        self._is_customer_allowed = is_customer_allowed
        self._is_enabled = is_enabled
        self._controls_thread = controls_thread
        self._entity_fallback = entity_id_fallback
        self._now_ms = now_ms

    async def execute(
        self,
        session_key: str,
        event_type: str,
        *,
        order_id: str | None = None,
        message: str = "",
        payload: dict[str, Any] | None = None,
        source: str | None = None,
    ) -> AgentEventOutcome:
        if not self._is_enabled():
            return AgentEventOutcome(session_key, False, "mba_disabled")
        m = _SESSION_KEY_RE.fullmatch(session_key)
        if m is None:
            return AgentEventOutcome(session_key, False, "session_unknown")
        customer = m.group(1)
        if not self._is_customer_allowed(customer):
            logger.error("[mba.agent_event] cliente FUERA de la lista cerrada: ***{} — nada que emitir", customer[-4:])
            return AgentEventOutcome(session_key, False, "customer_not_enabled")
        data = self._store.read(session_key)
        if not data:
            return AgentEventOutcome(session_key, False, "session_unknown")
        if event_type not in AGENT_EVENT_TYPES:
            return AgentEventOutcome(session_key, False, "unknown_event_type")
        if not self._controls_thread(data, session_key):
            logger.info("[mba.agent_event] {} {} → no (hubara_controls)", session_key, event_type)
            return AgentEventOutcome(session_key, False, "hubara_controls")
        entity_id = data.get("phone_number_id") or self._entity_fallback()
        if not entity_id:
            logger.warning("[mba.agent_event] {} sin phone_number_id (sesión ni WHATSAPP_PHONE_NUMBER_ID)", session_key)
            return AgentEventOutcome(session_key, False, "entity_id_missing")
        now_ms = self._now_ms()
        reserved, previous = self._reserve(session_key, event_type, order_id, now_ms, source=source)
        if previous is not None:
            return AgentEventOutcome(session_key, False, "already_emitted",
                                     agent_event_id=previous.get("agent_event_id"), at_ms=previous.get("at_ms"))
        if not reserved:
            # Sin reserva no se emite: sin auditoría ni dedupe un retry duplicaría el aviso.
            return AgentEventOutcome(session_key, False, "vault_unavailable", at_ms=now_ms, recorded=False)
        full_payload = {**(payload or {}), **({"order_id": order_id} if order_id else {})} or None
        try:
            result = await self._port.emit(
                entity_id=str(entity_id), to=f"+{customer}", event_type=event_type,
                description=build_description(event_type, message), payload=full_payload,
            )
        except AgentEventError as exc:
            error = f"{exc.status} {exc.detail}".strip() if exc.status else exc.detail
            logger.warning("[mba.agent_event] {} {} falló: {} {}", session_key, event_type, exc.kind, error)
            recorded = self._finalize(session_key, event_type, order_id, now_ms, status=exc.kind, agent_event_id=None,
                                      error=error)
            return AgentEventOutcome(session_key, False, exc.kind, at_ms=now_ms, error=error, recorded=recorded)
        recorded = self._finalize(session_key, event_type, order_id, now_ms, status=result.status,
                                  agent_event_id=result.agent_event_id, error=None)
        logger.info("[mba.agent_event] {} {} → {} id={} (registrado={})", session_key, event_type, result.status,
                    result.agent_event_id, recorded)
        return AgentEventOutcome(session_key, True, result.status, agent_event_id=result.agent_event_id, at_ms=now_ms,
                                 recorded=recorded)

    def _reserve(self, session_key: str, event_type: str, order_id: str | None, now_ms: int, *,
                 source: str | None) -> tuple[bool, dict[str, Any] | None]:
        """Chequeo + reserva en UNA escritura bajo el flock. Devuelve
        ``(reservado, evento_previo)``: previo ≠ None → ``already_emitted``;
        ``(False, None)`` → el vault no pudo escribir (no se emite)."""
        found: dict[str, dict[str, Any]] = {}
        entry = {"type": event_type, "order_id": order_id, "at_ms": now_ms, "status": "pending",
                 "agent_event_id": None, "error": None, "source": source}

        def _mutate(data: dict[str, Any]) -> dict[str, Any] | None:
            if not data:
                return None  # lectura fresca vacía: no pisar la sesión con un dict a medias
            previous = _already_emitted(data, event_type, order_id, now_ms)
            if previous is not None:
                found["previous"] = previous
                return None
            data["agent_events"] = (_events(data) + [entry])[-AGENT_EVENTS_CAP:]
            return data

        try:
            written = self._store.update(session_key, _mutate)
        except OSError as exc:
            logger.error("[mba.agent_event] {} {} no se pudo reservar en el vault: {}", session_key, event_type, exc)
            return False, None
        if "previous" in found:
            return False, found["previous"]
        if written is None:
            logger.error("[mba.agent_event] {} {} la sesión leyó vacía: no reservado", session_key, event_type)
            return False, None
        return True, None

    def _finalize(self, session_key: str, event_type: str, order_id: str | None, reserved_at_ms: int, *,
                  status: str, agent_event_id: str | None, error: str | None) -> bool:
        """Cierra la reserva ``pending`` con el resultado de Meta. Devuelve si
        se pudo escribir (si no, la reserva vence sola a los ``PENDING_TTL_MS``)."""

        def _mutate(data: dict[str, Any]) -> dict[str, Any] | None:
            if not data:
                return None
            events = _events(data)
            for e in reversed(events):
                if (e.get("type") == event_type and e.get("order_id") == order_id
                        and e.get("at_ms") == reserved_at_ms and e.get("status") == "pending"):
                    e.update({"status": status, "agent_event_id": agent_event_id, "error": error})
                    break
            else:
                events.append({"type": event_type, "order_id": order_id, "at_ms": reserved_at_ms, "status": status,
                               "agent_event_id": agent_event_id, "error": error, "source": None})
            data["agent_events"] = events[-AGENT_EVENTS_CAP:]
            return data

        try:
            written = self._store.update(session_key, _mutate)
        except OSError as exc:
            logger.error("[mba.agent_event] {} {} {} pero el vault NO lo registró: {}", session_key, event_type, status,
                         exc)
            return False
        if written is None:
            logger.error("[mba.agent_event] {} {} {} pero la sesión leyó vacía: no registrado", session_key, event_type,
                         status)
            return False
        return True
