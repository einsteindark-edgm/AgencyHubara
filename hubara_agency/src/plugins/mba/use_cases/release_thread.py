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
   ``unavailable`` / ``not_configured``) se registra en ``last_error`` y se
   devuelve en el outcome, nunca se levanta; NO deja el release como
   pendiente (se vuelve a intentar en el próximo trigger) y NO pisa un
   release exitoso todavía pendiente (dos triggers concurrentes: el segundo
   recibe 4xx de Meta y solo anota el error). Un timeout (``ambiguous``:
   Meta pudo haberlo procesado) se registra como pendiente igual que un
   éxito, con el error anotado, hasta que Meta confirme o venza el TTL.
5. ``release_pending`` vence a los ``RELEASE_PENDING_TTL_MS`` (15 min): si
   Meta no confirmó en ese lapso (webhook caído, o el hilo no era nuestro),
   los triggers automáticos vuelven a intentar; el manual del operador pasa
   siempre por encima (es la salida cuando el estado quedó colgado).
6. Si Meta aceptó pero el registro en el vault falló (``OSError``), el
   outcome dice ``released=True, recorded=False`` y se loguea ERROR: el
   release ocurrió; ``release_pending`` no lo protege hasta que Meta avise.

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
RELEASE_PENDING_TTL_MS = 15 * 60 * 1000
METADATA_MAX = 2000  # tope de Meta, prefijo incluido
_SESSION_KEY_RE = re.compile(r"^wa_(\d{6,15})$")
_SOURCE = "thread_control"
_PENDING_KINDS = ("ok", "ambiguous")


@dataclass(frozen=True)
class ReleaseOutcome:
    session_key: str
    released: bool
    reason: str
    action_at_ms: int | None = None
    error: str | None = None
    recorded: bool = True  # False: Meta aceptó pero el vault no pudo anotarlo


def _now_ms() -> int:
    return int(time.time() * 1000)


def _pending_since(data: dict[str, Any]) -> int | None:
    """``at_ms`` de nuestro último release exitoso (o ambiguo) que Meta aún no
    confirmó con un ``messaging_handovers`` posterior; ``None`` si no hay."""
    tc = data.get("thread_control")
    if not isinstance(tc, dict) or tc.get("last_action") != "release" or tc.get("last_result") not in _PENDING_KINDS:
        return None
    at = tc.get("last_action_at_ms")
    confirmed = data.get("control_owner_updated_at_ms")
    if isinstance(at, int) and at > (confirmed if isinstance(confirmed, int) else 0):
        return at
    return None


def _release_pending(data: dict[str, Any], now_ms: int) -> bool:
    since = _pending_since(data)
    return since is not None and now_ms - since < RELEASE_PENDING_TTL_MS


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
        now_ms = self._now_ms()
        facts = ReleaseFacts(
            control_owner=data.get("control_owner") if isinstance(data.get("control_owner"), str) else None,
            order_registered=order_registered,
            agent_event_emitted=agent_event_emitted,
            release_pending=_release_pending(data, now_ms),
        )
        decision = decide_release(trigger, facts)
        if not decision.release:
            logger.info("[mba.release] {} trigger={} → no ({})", session_key, trigger.value, decision.reason)
            return ReleaseOutcome(session_key, False, decision.reason)
        phone_number_id = data.get("phone_number_id") or self._phone_fallback()
        if not phone_number_id:
            logger.warning("[mba.release] {} sin phone_number_id (sesión ni WHATSAPP_PHONE_NUMBER_ID)", session_key)
            return ReleaseOutcome(session_key, False, "phone_number_id_missing")
        meta_str = (f"hubara:{trigger.value}" + (f" {metadata}" if metadata else ""))[:METADATA_MAX]
        try:
            await self._port.release(phone_number_id=str(phone_number_id), to=customer, metadata=meta_str)
        except ThreadControlError as exc:
            error = f"{exc.status} {exc.detail}".strip() if exc.status else exc.detail
            logger.warning("[mba.release] {} trigger={} falló: {} {}", session_key, trigger.value, exc.kind, error)
            err = {"kind": exc.kind, "status": exc.status, "detail": exc.detail, "at_ms": now_ms, "trigger": trigger.value}
            recorded = self._record(session_key, trigger, metadata, now_ms, result=exc.kind, error=err)
            return ReleaseOutcome(session_key, False, exc.kind, action_at_ms=now_ms, error=error, recorded=recorded)
        recorded = self._record(session_key, trigger, metadata, now_ms, result="ok", error=None)
        logger.info("[mba.release] {} trigger={} → release pedido a Meta (registrado={})", session_key, trigger.value,
                    recorded)
        return ReleaseOutcome(session_key, True, decision.reason, action_at_ms=now_ms, recorded=recorded)

    def _record(self, session_key: str, trigger: ReleaseTrigger, metadata: str | None, now_ms: int,
                *, result: str, error: dict[str, Any] | None) -> bool:
        """``result`` ∈ {ok, ambiguous, rejected, unavailable, not_configured}.
        ``ok`` y ``ambiguous`` dejan el release pendiente de confirmación de
        Meta. Un fallo no pisa un release pendiente anterior (doble trigger):
        solo anota ``last_error``. Devuelve si se pudo escribir."""
        pending_result = result in _PENDING_KINDS

        def _mutate(data: dict[str, Any]) -> dict[str, Any] | None:
            if not data:
                return None  # lectura fresca vacía: no pisar la sesión con un dict a medias
            current = data.get("thread_control") if isinstance(data.get("thread_control"), dict) else {}
            if not pending_result and _pending_since(data) is not None:
                data["thread_control"] = {**current, "last_error": error}
                return data
            data["thread_control"] = {
                "last_action": "release",
                "last_action_at_ms": now_ms,
                "trigger": trigger.value,
                "metadata": metadata,
                "last_result": result,
                "last_ok": result == "ok",
                "last_error": error,
            }
            if pending_result:
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

        try:
            written = self._store.update(session_key, _mutate)
        except OSError as exc:
            logger.error("[mba.release] {} release {} pero el vault NO lo registró: {}", session_key, result, exc)
            return False
        if written is None:
            logger.error("[mba.release] {} release {} pero la sesión leyó vacía: no registrado", session_key, result)
            return False
        return True
