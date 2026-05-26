"""State machine helpers for order operational stages.

Source of truth: `metadata.hubara_stage` field on Medusa Order / DraftOrder.
Hubara owns this field — Medusa stores it as opaque JSON.

Stages (closed-list):
  * `new`         — recién registrada por el LLM. Para drafts: aún no agendada.
  * `preparing`   — agendada con fecha de entrega + opcional nota del humano.
  * `ready`       — preparada físicamente, lista para enviarse.
  * `shipping`    — entregada al transportista, en camino.
  * `delivered`   — recibida por el cliente.
  * `cancelled`   — cancelada (cliente, humano, sistema). Terminal.

Transiciones permitidas (DAG):

    new ──► preparing ──► ready ──► shipping ──► delivered
     │          │           │           │
     └──────────┴───────────┴───────────┴──► cancelled

Reglas:
  * `new` solo puede ir a `preparing` o `cancelled`.
  * `delivered` y `cancelled` son terminales.
  * El operador puede ir hacia atrás (ej. `ready → preparing`) solo via
    `force=True` (operación correctiva). El default es DAG estricto.

R-DET-friendly: módulo puro. NO imports de temporalio, httpx, ni I/O. Solo
funciones que operan sobre dicts. Llamable desde activities y desde tests.

R-JSON-friendly: no devuelve datetimes ni dataclasses opacas, solo dicts
JSON-safe (el caller los serializa a metadata).
"""
from __future__ import annotations

import time
from typing import Any, Literal

# Closed-list de stages. Match exact con `OrderUiStatus` del query_port
# (mismo orden, mismos nombres) para que el frontend no requiera mapeo extra.
OrderStage = Literal[
    "new", "preparing", "ready", "shipping", "delivered", "cancelled",
]

STAGE_VALUES: tuple[OrderStage, ...] = (
    "new", "preparing", "ready", "shipping", "delivered", "cancelled",
)

# Transiciones permitidas. Key = from, Value = set de stages alcanzables.
# `cancelled` es alcanzable desde cualquier no-terminal.
_ALLOWED_TRANSITIONS: dict[OrderStage, frozenset[OrderStage]] = {
    "new":       frozenset({"preparing", "cancelled"}),
    "preparing": frozenset({"ready", "cancelled"}),
    "ready":     frozenset({"shipping", "cancelled"}),
    "shipping":  frozenset({"delivered", "cancelled"}),
    "delivered": frozenset(),   # terminal
    "cancelled": frozenset(),   # terminal
}

# Metadata keys (prefijo `hubara_` para que no choquen con campos Medusa
# o de otros plugins que escriban a metadata).
META_KEY_STAGE = "hubara_stage"
META_KEY_HISTORY = "hubara_stage_history"
META_KEY_SCHEDULED_DELIVERY_ISO = "hubara_scheduled_delivery_iso"
META_KEY_SCHEDULED_DELIVERY_TIME = "hubara_scheduled_delivery_time"
META_KEY_HUMAN_NOTE = "hubara_human_note"
META_KEY_PAYMENT_CONFIRMED = "hubara_payment_confirmed"
META_KEY_PAYMENT_CONFIRMED_AT_MS = "hubara_payment_confirmed_at_ms"
META_KEY_PAYMENT_CONFIRMED_BY = "hubara_payment_confirmed_by"
META_KEY_CANCELLED_REASON = "hubara_cancelled_reason"


class InvalidStageTransitionError(ValueError):
    """Raised when transition_stage is called with an invalid from→to pair.

    `from_stage` y `to_stage` se expone como attributes para que el caller
    pueda traducir a HTTP 409 / 422 con detalle al cliente.
    """

    def __init__(
        self, from_stage: str, to_stage: str, reason: str = ""
    ) -> None:
        suffix = f": {reason}" if reason else ""
        super().__init__(
            f"Transición de stage inválida: {from_stage!r} → {to_stage!r}{suffix}"
        )
        self.from_stage = from_stage
        self.to_stage = to_stage


def read_stage(metadata: dict[str, Any] | None) -> OrderStage:
    """Devuelve el stage actual leyendo `metadata.hubara_stage`.

    Si la metadata es None o no tiene la key (caso: orders viejas pre-feature),
    devuelve `"new"` por convención — el frontend las trata como nuevas. El
    primer write hace transition desde "new" y persiste el campo, así que
    esto solo aplica a la primera lectura.

    Si el valor está pero NO es un stage válido (mismatch entre schema y
    metadata escrita por algo externo), devuelve `"new"` defensivamente
    para que el flujo no se rompa.
    """
    if not metadata:
        return "new"
    raw = metadata.get(META_KEY_STAGE)
    if raw in STAGE_VALUES:
        return raw  # type: ignore[return-value]
    return "new"


def validate_transition(
    from_stage: OrderStage,
    to_stage: OrderStage,
    *,
    force: bool = False,
) -> None:
    """Levanta `InvalidStageTransitionError` si la transición no está permitida.

    `force=True` bypassa el check (operación correctiva del operador, ej.
    deshacer un click accidental). Idempotente: `from == to` se considera
    no-op (no raisea, no escribe history — el caller decide ignorar).
    """
    if from_stage == to_stage:
        return  # no-op: idempotente
    if force:
        return
    if to_stage not in STAGE_VALUES:
        raise InvalidStageTransitionError(
            from_stage, to_stage,
            reason=f"to_stage no es un valor válido (debe estar en {STAGE_VALUES})",
        )
    allowed = _ALLOWED_TRANSITIONS.get(from_stage, frozenset())
    if to_stage not in allowed:
        # Razón pedagógica para el caller (humano operador).
        if from_stage in {"delivered", "cancelled"}:
            reason = f"{from_stage!r} es estado terminal — no se puede transicionar"
        else:
            allowed_list = sorted(allowed)
            reason = f"desde {from_stage!r} solo se permite ir a {allowed_list}"
        raise InvalidStageTransitionError(from_stage, to_stage, reason=reason)


def transition_stage(
    metadata: dict[str, Any] | None,
    *,
    to_stage: OrderStage,
    by: str,
    note: str | None = None,
    force: bool = False,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Construye el patch de metadata para una transición de stage.

    No muta el input — devuelve un dict NUEVO con los campos a aplicar via
    `patch_*_metadata` del MedusaClient. Esto incluye:
      * `hubara_stage`: el nuevo valor
      * `hubara_stage_history`: lista APPENDED con la nueva entrada
        (preserva las entradas previas — leyéndolas de `metadata` input)

    `by`: identifier de quién hizo la transición. Valores convencionales:
      * `"agent"` — el LLM/sales workflow (initial create de "new")
      * `"human"` — operador desde el dashboard
      * `"system"` — auto-transitions (raro hoy, reservado)

    `note`: texto opcional contextual. Para el agendamiento del humano sería
    la nota que escribió. Para drag-and-drop puede ser None.

    `now_ms`: override del timestamp (útil para tests determinísticos). Si
    es None, se lee `time.time()` (best-effort wall-clock; ver discusión
    `activity.info().scheduled_time` en el adapter wrapper para idempotencia
    de retries).

    Raises `InvalidStageTransitionError` si la transición no es válida.
    """
    current_metadata = metadata or {}
    from_stage = read_stage(current_metadata)

    # Idempotente: si ya estamos en to_stage, no escribimos history nuevo.
    if from_stage == to_stage:
        return {}

    validate_transition(from_stage, to_stage, force=force)

    # Business rule (2026-05-26): new → preparing REQUIERE fecha de entrega
    # agendada. Sin esto el operador puede arrastrar una card de "Nueva" a
    # "En preparación" sin asignar fecha, lo cual deja a producción sin
    # información para planear. `force=True` bypassa (operación correctiva
    # explícita). El path canónico para schedule + transition es
    # `build_schedule_patch` que setea la fecha+stage atómicamente.
    if (
        not force
        and from_stage == "new"
        and to_stage == "preparing"
        and not current_metadata.get(META_KEY_SCHEDULED_DELIVERY_ISO)
    ):
        raise InvalidStageTransitionError(
            from_stage, to_stage,
            reason=(
                "no se puede mover a 'En preparación' sin agendar la fecha "
                "de entrega primero. Usá el panel de agendamiento del "
                "inspector para asignar fecha + hora antes de avanzar."
            ),
        )

    ts_ms = now_ms if now_ms is not None else int(time.time() * 1000)

    history = list(current_metadata.get(META_KEY_HISTORY) or [])
    entry: dict[str, Any] = {
        "from": from_stage,
        "to": to_stage,
        "at_ms": ts_ms,
        "by": by,
    }
    if note:
        entry["note"] = note
    if force:
        entry["forced"] = True
    history.append(entry)

    return {
        META_KEY_STAGE: to_stage,
        META_KEY_HISTORY: history,
    }


def build_initial_stage_patch(
    *,
    by: str = "agent",
    note: str | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Patch inicial cuando el LLM crea una orden (estado `new`).

    Por convención, escribir el stage explícitamente desde el inicio nos da
    audit trail consistente: el primer entry de history es `from=None to=new`.
    Sin esto, el `read_stage` defaultearía a `"new"` pero el history estaría
    vacío hasta la primera transición — perdemos el momento de creación.

    Llamado por la tool `register_order` después del POST exitoso a Medusa.
    """
    ts_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    entry: dict[str, Any] = {
        "from": None,
        "to": "new",
        "at_ms": ts_ms,
        "by": by,
    }
    if note:
        entry["note"] = note
    return {
        META_KEY_STAGE: "new",
        META_KEY_HISTORY: [entry],
    }


def build_schedule_patch(
    metadata: dict[str, Any] | None,
    *,
    delivery_iso: str,
    delivery_time: str | None = None,
    note: str | None = None,
    by: str = "human",
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Patch para agendar la entrega + transicionar de `new` → `preparing`.

    El humano da click en "Confirmar agendamiento" desde el inspector. El
    patch combina:
      * Campos de scheduling: `hubara_scheduled_delivery_iso`, `_time`,
        `hubara_human_note`.
      * Transición de stage `new` → `preparing` con la nota.

    Si el order ya estaba en `preparing` (humano edita el agendamiento sin
    cambiar de columna), NO hace transition (idempotente) — solo actualiza
    los campos scheduling y registra una entrada history "scheduled_updated".
    """
    current_metadata = metadata or {}
    current_stage = read_stage(current_metadata)

    patch: dict[str, Any] = {
        META_KEY_SCHEDULED_DELIVERY_ISO: delivery_iso,
    }
    if delivery_time:
        patch[META_KEY_SCHEDULED_DELIVERY_TIME] = delivery_time
    if note:
        patch[META_KEY_HUMAN_NOTE] = note

    if current_stage == "new":
        # First-time schedule → transition new → preparing.
        # Sintetizamos una vista de metadata con la fecha YA aplicada para que
        # la validación de `transition_stage` (que exige scheduled_iso para
        # new→preparing) pase — el patch atómicamente setea fecha + stage,
        # así que la condición se cumple desde el punto de vista del invariante.
        synth_metadata = {
            **current_metadata,
            META_KEY_SCHEDULED_DELIVERY_ISO: delivery_iso,
        }
        transition_patch = transition_stage(
            synth_metadata,
            to_stage="preparing",
            by=by,
            note=note or f"Agendado para {delivery_iso}",
            now_ms=now_ms,
        )
        patch.update(transition_patch)
    elif current_stage == "preparing":
        # Re-schedule (edit) — log a non-transitioning entry to history.
        ts_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        history = list(current_metadata.get(META_KEY_HISTORY) or [])
        history.append({
            "from": "preparing",
            "to": "preparing",  # no transition, just metadata edit
            "at_ms": ts_ms,
            "by": by,
            "note": note or f"Reagendado para {delivery_iso}",
            "event": "schedule_updated",
        })
        patch[META_KEY_HISTORY] = history
    # else: stage avanzado (ready/shipping/delivered/cancelled) — no permitimos
    # reagendar. El caller debe validar antes (HTTP 409).

    return patch


def build_confirm_payment_patch(
    metadata: dict[str, Any] | None,
    *,
    by: str = "human",
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Patch para marcar el pago como confirmado.

    Idempotente: si `hubara_payment_confirmed` ya es True, no hace nada
    (devuelve {}). Caller puede skip el patch call.

    NO transiciona stage — el pago es ortogonal al pipeline operacional.
    Una orden puede estar `preparing` con pago confirmado, o `delivered` con
    pago pending (típico de COD donde el pago se confirma POST-delivery).
    """
    current_metadata = metadata or {}
    if current_metadata.get(META_KEY_PAYMENT_CONFIRMED) is True:
        return {}
    ts_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    history = list(current_metadata.get(META_KEY_HISTORY) or [])
    history.append({
        "from": read_stage(current_metadata),
        "to": read_stage(current_metadata),  # no stage change
        "at_ms": ts_ms,
        "by": by,
        "event": "payment_confirmed",
    })
    return {
        META_KEY_PAYMENT_CONFIRMED: True,
        META_KEY_PAYMENT_CONFIRMED_AT_MS: ts_ms,
        META_KEY_PAYMENT_CONFIRMED_BY: by,
        META_KEY_HISTORY: history,
    }


def build_cancel_patch(
    metadata: dict[str, Any] | None,
    *,
    reason: str | None = None,
    by: str = "human",
    force: bool = True,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Patch para cancelar la orden.

    `force=True` por default porque `cancelled` es alcanzable desde cualquier
    estado no terminal (ver `_ALLOWED_TRANSITIONS`). El humano puede cancelar
    incluso desde `shipping` (caso real: paquete devuelto en logística).
    """
    current_metadata = metadata or {}
    current_stage = read_stage(current_metadata)
    if current_stage == "cancelled":
        # Idempotente: ya está cancelada.
        return {}
    transition_patch = transition_stage(
        current_metadata,
        to_stage="cancelled",
        by=by,
        note=reason or "Cancelada por operador",
        force=force,
        now_ms=now_ms,
    )
    patch = dict(transition_patch)
    if reason:
        patch[META_KEY_CANCELLED_REASON] = reason
    return patch
