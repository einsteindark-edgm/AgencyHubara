"""Buzón de los avisos de etapa que Temporal no aceptó, hasta que salen
(premortem de la Ola 0 de Temporal, 2026-10-16).

La etapa de un pedido cambia en Medusa y su aviso (el Agente ETA al cliente y
el evento de Meta) es un workflow, `EmitOrderStageWorkflow`. Si Temporal no
acepta el arranque, la Ola 0 respondía 503 y el aviso se perdía igual: el
tablero devolvía la tarjeta a la columna vieja, ninguna pantalla deja
reintentar una etapa que ya cambió, «Confirmar pago» se trababa en su primer
paso y el Order Sentinel contaba como fallida una transición que sí se aplicó.
Una falla técnica no pasa al equipo (auto-recuperación + alerta): el aviso queda
acá y la API lo reintenta sola hasta que sale.

Un JSON por aviso en `<vault>/_orders/stage_notices/`, con escritura atómica.
El id del workflow es el de siempre (`order-stage-changed-<pedido>-<etapa>`):
si el arranque original sí había llegado y solo se perdió la respuesta,
Temporal contesta `WorkflowAlreadyStartedError` y cuenta como enviado; si ya
había terminado, el aviso corre otra vez y el ETA lo descarta por
`notified_stages`. Un aviso que no sale en un día se abandona con un error en
el log: un «tu pedido va en camino» de ayer ya no le sirve al cliente.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, replace
from datetime import timedelta
from pathlib import Path
from typing import Any

from temporalio.exceptions import WorkflowAlreadyStartedError

from src.sdk.runtime import WORKSPACE_VAULT_DIR, atomic_write_json

log = logging.getLogger(__name__)

#: Pasado esto sin salir, el aviso se abandona (con un error en el log).
MAX_AGE = timedelta(hours=24)
_ERROR_MAX = 500


@dataclass(frozen=True)
class PendingNotice:
    workflow_id: str
    workflow_input: dict[str, Any]
    created_at_ms: int
    attempts: int = 0
    last_error: str = ""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _dir() -> Path:
    return WORKSPACE_VAULT_DIR / "_orders" / "stage_notices"


def _path(workflow_id: str) -> Path:
    return _dir() / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', workflow_id)}.json"


def park(workflow_id: str, workflow_input: dict[str, Any], error: str, *, now_ms: int | None = None) -> None:
    """Deja el aviso en el buzón. El mismo aviso otra vez lo reemplaza (con los
    datos del último intento: la guía o el valor del envío corregidos)."""
    notice = PendingNotice(
        workflow_id=workflow_id,
        workflow_input=dict(workflow_input),
        created_at_ms=_now_ms() if now_ms is None else now_ms,
        last_error=str(error)[:_ERROR_MAX],
    )
    path = _path(workflow_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, asdict(notice))


def pending() -> list[PendingNotice]:
    """Los avisos que esperan, del más viejo al más nuevo."""
    folder = _dir()
    if not folder.exists():
        return []
    out: list[PendingNotice] = []
    for path in folder.glob("*.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            out.append(
                PendingNotice(
                    workflow_id=str(raw["workflow_id"]),
                    workflow_input=dict(raw["workflow_input"]),
                    created_at_ms=int(raw["created_at_ms"]),
                    attempts=int(raw.get("attempts") or 0),
                    last_error=str(raw.get("last_error") or ""),
                )
            )
        except (OSError, ValueError, TypeError, KeyError):
            log.error("stage_notice_outbox: aviso ilegible en el buzón, se deja para revisar: %s", path)
    return sorted(out, key=lambda n: n.created_at_ms)


async def drain(
    start: Callable[[str, dict[str, Any]], Awaitable[None]], *, now_ms: int | None = None
) -> dict[str, int]:
    """Intenta cada aviso del buzón con `start(workflow_id, workflow_input)`. Lo
    que sale (o ya estaba en vuelo) se borra; lo que vuelve a fallar se queda
    con su intento contado; lo de más de un día se abandona."""
    now = _now_ms() if now_ms is None else now_ms
    summary = {"sent": 0, "pending": 0, "given_up": 0}
    for notice in pending():
        path = _path(notice.workflow_id)
        if now - notice.created_at_ms > MAX_AGE.total_seconds() * 1000:
            log.error(
                "stage_notice_outbox: aviso de etapa abandonado tras un día sin salir: %s (intentos=%s, último error: %s)",
                notice.workflow_id, notice.attempts, notice.last_error,
            )
            path.unlink(missing_ok=True)
            summary["given_up"] += 1
            continue
        try:
            await start(notice.workflow_id, notice.workflow_input)
        except WorkflowAlreadyStartedError:
            pass  # el arranque original sí había llegado
        except Exception as exc:  # noqa: BLE001 — se reintenta en la próxima vuelta
            atomic_write_json(path, asdict(replace(notice, attempts=notice.attempts + 1, last_error=str(exc)[:_ERROR_MAX])))
            summary["pending"] += 1
            continue
        path.unlink(missing_ok=True)
        summary["sent"] += 1
        log.info("stage_notice_outbox: salió el aviso de etapa que esperaba en el buzón: %s", notice.workflow_id)
    return summary
