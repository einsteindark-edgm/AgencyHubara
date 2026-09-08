"""D1.5 — ``IngestHandover``: el webhook ``messaging_handovers`` de Meta
Business Agent → ``control_owner`` por sesión.

Quién responde al cliente en un número con MBA activo lo decide Meta y nos lo
avisa con ``messaging_handovers`` (ver ``parsers.parse_messaging_handovers``):

* enviar un mensaje de servicio desde nuestra app = tomar el control (Meta
  emite el evento con ``new_owner_app_id`` = NUESTRO app id);
* ``thread_control release`` (D1.6) = devolverlo a MBA (``new_owner_app_id``
  = el app de Business Agent).

La regla es pura (``control_owner_for``): ``new_owner_app_id`` igual a
``WHATSAPP_APP_ID`` → ``hubara``; distinto → ``mba``. Sin ``WHATSAPP_APP_ID``
configurado NO se decide nada (se loguea y se descarta: mejor un hueco
visible que un dueño inventado). Lo que se persiste en ``metadata.json``
(bajo ``store.update``, flock por sesión):

* ``control_owner`` ∈ ``CONTROL_OWNERS`` + ``control_owner_app_id``;
* ``control_owner_since_ms`` (cuándo cambió el dueño) y
  ``control_owner_updated_at_ms`` (último evento, aunque repita dueño);
* ``control_history[]`` (últimos ``CONTROL_HISTORY_CAP``): cada evento con
  ``kind``, ambos app ids, ``at_ms`` (timestamp de Meta) y ``received_at_ms``.

Reentregas y orden (Meta reintenta entregas fallidas, y dos webhooks
seguidos corren como background tasks concurrentes):

* un evento con el mismo ``(at_ms, kind, new_owner_app_id)`` que alguno de
  la historia acotada es una reentrega → nada se reescribe (``unchanged``);
* un evento con ``at_ms`` ANTERIOR al último aplicado es viejo → ``stale``,
  no se aplica (una reentrega tardía del take no revierte el release);
* sin timestamp de Meta (shape no publicado) ``at_ms`` es la hora de
  recepción, así que el dedupe por tiempo no existe: un evento idéntico
  (``kind`` + ambos app ids + ``metadata``) al último de la historia se
  trata como reentrega; dos eventos distintos sin timestamp se aplican en
  el orden en que llegan.

Lista cerrada de clientes como en ``IngestStandby`` (fuera de ella: ERROR +
nada escrito).

Lo que NO hace: no toca ``active_route``/``tag`` (el eje bot/humano de
Hubara es otro), no despacha a Temporal ni emite eventos, no llama a Meta.
P-28: importa solo ``src.sdk`` + chats.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from src.plugins.chats.agent.sales.parsers import HandoverEvent, HandoversEvent
from src.sdk.runtime import CONTROL_OWNER_HUBARA, CONTROL_OWNER_MBA

__all__ = ["CONTROL_HISTORY_CAP", "HandoverIngestResult", "IngestHandover", "control_owner_for"]

SESSION_PREFIX = "wa_"
CONTROL_HISTORY_CAP = 50
_SOURCE = "messaging_handovers"


@dataclass(frozen=True)
class HandoverIngestResult:
    applied: int = 0
    unchanged: int = 0  # reentregas
    stale: int = 0  # más viejos que el último aplicado (llegaron tarde)
    rejected: int = 0  # clientes fuera de la lista cerrada (nada escrito)
    skipped: int = 0  # sin decisión posible (app id propio no configurado / dueño nulo)
    sessions: tuple[str, ...] = ()


def _now_ms() -> int:
    return int(time.time() * 1000)


def control_owner_for(new_owner_app_id: str | None, *, our_app_id: str) -> str | None:
    """Pura. ``None`` cuando no se puede decidir (sin app id propio o sin dueño)."""
    ours = (our_app_id or "").strip()
    new = (new_owner_app_id or "").strip()
    if not ours or not new:
        return None
    return CONTROL_OWNER_HUBARA if new == ours else CONTROL_OWNER_MBA


class IngestHandover:
    def __init__(
        self,
        *,
        metadata_store: Any,  # FilesystemMetadataStore (update con flock)
        vault_dir: Path,
        now_ms: Callable[[], int] = _now_ms,
        is_customer_allowed: Callable[[str], bool],
        our_app_id: Callable[[], str],
    ) -> None:
        self._metadata_store = metadata_store
        self._vault_dir = Path(vault_dir)
        self._now_ms = now_ms
        self._is_customer_allowed = is_customer_allowed
        # Leído en cada evento (no al construir): la config puede parchearse en
        # tests y el singleton de composition vive todo el proceso.
        self._our_app_id = our_app_id

    async def execute(self, event: HandoversEvent) -> HandoverIngestResult:
        applied = unchanged = stale = rejected = skipped = 0
        sessions: list[str] = []
        for handover in event.handovers:
            if not self._is_customer_allowed(handover.customer):
                # ERROR a propósito: Meta está moviendo el control de un cliente
                # que no habilitamos (rollout más abierto de lo previsto).
                logger.error(
                    "[chats.handover] handover de cliente FUERA de la lista cerrada de MBA: ***{} — descartado",
                    handover.customer[-4:],
                )
                rejected += 1
                continue
            owner = control_owner_for(handover.new_owner_app_id, our_app_id=self._our_app_id())
            if owner is None:
                logger.warning(
                    "[chats.handover] sin decisión: new_owner_app_id={!r} previous={!r} kind={} "
                    "(WHATSAPP_APP_ID configurado: {}) — descartado",
                    handover.new_owner_app_id, handover.previous_owner_app_id, handover.kind,
                    bool((self._our_app_id() or "").strip()),
                )
                skipped += 1
                continue
            session = f"{SESSION_PREFIX}{handover.customer}"
            outcome = self._apply(session, handover, owner)
            if outcome == "applied":
                applied += 1
            elif outcome == "stale":
                stale += 1
            else:
                unchanged += 1
            if session not in sessions:
                sessions.append(session)
        logger.info(
            "[chats.handover] sessions={} applied={} unchanged={} stale={} rejected={} skipped={} unparsed={}",
            sessions, applied, unchanged, stale, rejected, skipped, event.unparsed,
        )
        return HandoverIngestResult(
            applied=applied, unchanged=unchanged, stale=stale, rejected=rejected, skipped=skipped,
            sessions=tuple(sessions),
        )

    def _apply(self, session: str, handover: HandoverEvent, owner: str) -> str:
        """``applied`` | ``unchanged`` (reentrega) | ``stale`` (más viejo que el último aplicado)."""
        received_at_ms = self._now_ms()
        at_ms = handover.timestamp_ms or received_at_ms
        (self._vault_dir / session).mkdir(parents=True, exist_ok=True)
        outcome: dict[str, str] = {}

        def _mutate(data: dict[str, Any]) -> dict[str, Any] | None:
            history = data.get("control_history")
            history = [h for h in history if isinstance(h, dict)] if isinstance(history, list) else []
            if handover.timestamp_ms is not None:
                if any(
                    h.get("at_ms") == at_ms and h.get("kind") == handover.kind
                    and h.get("new_owner_app_id") == handover.new_owner_app_id
                    for h in history
                ):
                    outcome["result"] = "unchanged"
                    return None
                last_at = data.get("control_owner_updated_at_ms")
                if isinstance(last_at, int) and at_ms < last_at:
                    outcome["result"] = "stale"
                    return None
            elif history and all(
                history[-1].get(k) == v
                for k, v in (
                    ("kind", handover.kind),
                    ("previous_owner_app_id", handover.previous_owner_app_id),
                    ("new_owner_app_id", handover.new_owner_app_id),
                    ("metadata", handover.metadata),
                )
            ):
                outcome["result"] = "unchanged"
                return None
            if data.get("control_owner") != owner:
                data["control_owner_since_ms"] = at_ms
            data["control_owner"] = owner
            data["control_owner_app_id"] = handover.new_owner_app_id
            data["control_owner_updated_at_ms"] = at_ms
            history.append({
                "owner": owner,
                "kind": handover.kind,
                "previous_owner_app_id": handover.previous_owner_app_id,
                "new_owner_app_id": handover.new_owner_app_id,
                "at_ms": at_ms,
                "received_at_ms": received_at_ms,
                "metadata": handover.metadata,
                "source": _SOURCE,
            })
            data["control_history"] = history[-CONTROL_HISTORY_CAP:]
            outcome["result"] = "applied"
            return data

        self._metadata_store.update(session, _mutate)
        result = outcome.get("result", "unchanged")
        if result == "applied":
            logger.info("[chats.handover] {} control_owner={} kind={} app_id={}", session, owner, handover.kind,
                        handover.new_owner_app_id)
        elif result == "stale":
            logger.warning("[chats.handover] {} evento viejo descartado: at_ms={} kind={} app_id={}", session, at_ms,
                           handover.kind, handover.new_owner_app_id)
        return result
