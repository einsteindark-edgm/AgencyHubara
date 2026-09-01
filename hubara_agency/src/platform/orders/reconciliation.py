"""Reconciliación idempotente de pedidos que NO llegaron a Medusa.

Premortem F2+K1 (segunda mitad): el `RegisterOrderTool` persiste cada intento
de registro fallido en `metadata.failed_order_registrations[]` y lo deja
visible en el dashboard (`vault_scanner` + `/api/orders/vault-orders`). Pero
hasta acá el loop estaba ABIERTO: nadie reintentaba cuando Medusa volvía, no
había forma de marcar un intento como resuelto, y el banner crecía para
siempre (alert fatigue → el operador deja de mirarlo).

Este módulo cierra el loop con un **núcleo idempotente** que opera sobre UN
record a la vez:

  * `reconcile_one(...)` — reintenta el registro contra el `OrderRegistrationPort`.
    Si el port confirma la orden en Medusa (provider != "stub"), marca el
    record `status="resolved"` con el `resolved_order_id` real. Si sigue
    fallando, apendea el intento y, pasado `max_attempts`, lo marca
    `status="abandoned"` para no reintentar infinito.
  * `mark_resolved_manually(...)` — el operador registró la orden a mano en
    Medusa: marca el record resuelto con una nota, sin tocar Medusa.

DEHA:
  * **R-DIP**: vive en `platform/` y NO importa de ningún plugin. Depende del
    `OrderRegistrationPort` (abstracción) y de DTOs de `platform/orders/port.py`.
    El BATCH driver (escanear todos los pendientes) vive en el plugin `orders`
    (`reconcile_runner.py`) porque ahí sí se puede importar `vault_scanner`.
  * **R-JSON**: `ReconciliationOutcome` es un `@dataclass(frozen=True)` plano.
  * NO importa `temporalio` — es lógica de dominio pura. La activity/script/
    endpoint que la disparan son adapters delgados.

Idempotencia (la propiedad crítica):
  * Un record ya `resolved` → no-op (`already_resolved`). Reintentar N veces
    es seguro.
  * El `OrderRegistrationPort` (Medusa adapter) hace su propio pre-check por
    `order_fingerprint` antes de crear, así que un reintento que cruza con un
    draft ya creado NO duplica — reusa el existente.
  * Un reintento que cae al `StubOrderRegistration` (Medusa aún sin config)
    devuelve `provider="stub"` y NO se considera resuelto — el record sigue
    `pending` hasta que Medusa esté configurado de verdad. Esto evita el
    falso-resuelto de "migrar un stub a otro stub".

Persistencia:
  * Read-modify-write de `metadata.json` con escritura atómica (`os.replace`
    sobre un tmp) para que un crash a mitad de escritura no deje el JSON
    corrupto (que el scanner skipearía, perdiendo el record).
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.platform.orders.port import (
    OrderItem,
    OrderRegistrationPort,
    OrderRegistrationResult,
    OrderShipping,
)

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Estado del record (persistido en metadata.failed_order_registrations[])
# ----------------------------------------------------------------------

STATUS_PENDING = "pending"
STATUS_RESOLVED = "resolved"
STATUS_ABANDONED = "abandoned"

# Estados terminales — no se reintentan.
_TERMINAL_STATUSES = frozenset({STATUS_RESOLVED, STATUS_ABANDONED})

# Tras este número de reintentos fallidos, el record se marca `abandoned`
# para no reintentar infinito (y para que el operador sepa que necesita
# intervención manual). El intento original que creó el record NO cuenta —
# solo los reintentos de reconciliación.
DEFAULT_MAX_ATTEMPTS = 5


# ----------------------------------------------------------------------
# Outcome (resultado de reconciliar UN record) — R-JSON
# ----------------------------------------------------------------------

# Valores posibles de `ReconciliationOutcome.outcome`.
OUTCOME_RESOLVED = "resolved"            # reintento exitoso → registrado en Medusa
OUTCOME_STILL_FAILING = "still_failing"  # reintento falló, sigue pending
OUTCOME_ALREADY_RESOLVED = "already_resolved"  # idempotente: ya estaba resuelto
OUTCOME_ABANDONED = "abandoned"          # alcanzó max_attempts → terminal
OUTCOME_NOT_FOUND = "not_found"          # no existe ese (session_key, audit_id)
OUTCOME_ERROR = "error"                  # metadata corrupto / record malformado


@dataclass(frozen=True)
class ReconciliationOutcome:
    """Resultado de reconciliar (o intentar) UN record.

    `outcome` es uno de los `OUTCOME_*`. `resolved_order_id` solo está
    presente cuando `outcome == OUTCOME_RESOLVED` (o un resolve manual con
    id provisto). `attempts` es la cuenta de reintentos de reconciliación
    acumulados en el record DESPUÉS de esta operación.
    """

    session_key: str
    audit_id: str
    outcome: str
    resolved_order_id: str | None = None
    provider: str | None = None
    error_detail: str | None = None
    attempts: int = 0

    @property
    def is_resolved(self) -> bool:
        return self.outcome in (OUTCOME_RESOLVED, OUTCOME_ALREADY_RESOLVED)


# ----------------------------------------------------------------------
# Núcleo: reconcile_one
# ----------------------------------------------------------------------


async def reconcile_one(
    *,
    vault_dir: str | Path,
    session_key: str,
    audit_id: str,
    port: OrderRegistrationPort,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> ReconciliationOutcome:
    """Reintenta registrar en Medusa UN pedido fallido. Idempotente.

    Busca el record por `order_id == audit_id` en
    `failed_order_registrations[]` (caso Medusa-caído) y, como fallback, en
    `registered_order` cuando es un stub (caso Medusa-sin-config). Reintenta
    `port.register_order(...)` con los datos originales del record.

    Reglas:
      * Si el record ya está `resolved` → `OUTCOME_ALREADY_RESOLVED` (no-op).
      * Si ya está `abandoned` → `OUTCOME_ABANDONED` (no-op).
      * Si los reintentos previos ya alcanzaron `max_attempts` → se marca
        `abandoned` y devuelve `OUTCOME_ABANDONED`.
      * El reintento se considera EXITOSO solo si el port confirma con un
        provider real (≠ "stub"). Un stub→stub NO resuelve.
    """
    metadata_file = Path(vault_dir) / session_key / "metadata.json"
    data = _read_metadata(metadata_file)
    if data is None:
        return ReconciliationOutcome(
            session_key=session_key,
            audit_id=audit_id,
            outcome=OUTCOME_NOT_FOUND,
            error_detail=f"metadata.json no existe o es ilegible: {metadata_file}",
        )

    located = _locate_record(data, audit_id)
    if located is None:
        return ReconciliationOutcome(
            session_key=session_key,
            audit_id=audit_id,
            outcome=OUTCOME_NOT_FOUND,
            error_detail=f"no se encontró order_id={audit_id} en la sesión",
        )
    record, _commit = located

    status = str(record.get("status", STATUS_PENDING))
    prior_attempts = list(record.get("reconciliation_attempts") or [])

    if status == STATUS_RESOLVED:
        return ReconciliationOutcome(
            session_key=session_key,
            audit_id=audit_id,
            outcome=OUTCOME_ALREADY_RESOLVED,
            resolved_order_id=record.get("resolved_order_id"),
            provider=record.get("provider"),
            attempts=len(prior_attempts),
        )
    if status == STATUS_ABANDONED:
        return ReconciliationOutcome(
            session_key=session_key,
            audit_id=audit_id,
            outcome=OUTCOME_ABANDONED,
            error_detail="record previamente abandonado (max_attempts alcanzado)",
            attempts=len(prior_attempts),
        )

    if len(prior_attempts) >= max_attempts:
        record["status"] = STATUS_ABANDONED
        record["abandoned_at_ms"] = _now_ms()
        _commit(data, record)
        _atomic_write_json(metadata_file, data)
        log.warning(
            "reconcile_one: audit_id=%s abandonado tras %d reintentos",
            audit_id, len(prior_attempts),
        )
        return ReconciliationOutcome(
            session_key=session_key,
            audit_id=audit_id,
            outcome=OUTCOME_ABANDONED,
            error_detail=f"max_attempts={max_attempts} alcanzado",
            attempts=len(prior_attempts),
        )

    # Reconstruir los DTOs del port desde el record persistido.
    try:
        items, shipping, kwargs = _rebuild_order_args(record)
    except (KeyError, TypeError, ValueError) as exc:
        log.error(
            "reconcile_one: record audit_id=%s malformado: %s", audit_id, exc
        )
        return ReconciliationOutcome(
            session_key=session_key,
            audit_id=audit_id,
            outcome=OUTCOME_ERROR,
            error_detail=f"record malformado: {type(exc).__name__}: {exc}",
            attempts=len(prior_attempts),
        )

    result: OrderRegistrationResult = await port.register_order(
        session_key=session_key,
        items=items,
        shipping=shipping,
        **kwargs,
    )

    now = _now_ms()
    # Un reintento solo "resuelve" si fue a un provider real. Un stub→stub
    # (Medusa aún sin config) NO cuenta — el record sigue pending.
    real_success = result.success and result.provider != "stub"

    attempt_entry = {
        "ts_ms": now,
        "ok": real_success,
        "provider": result.provider,
        "order_id": result.order_id,
        "error_detail": result.error_detail,
    }
    new_attempts = [*prior_attempts, attempt_entry]
    record["reconciliation_attempts"] = new_attempts

    if real_success:
        record["status"] = STATUS_RESOLVED
        record["resolved_order_id"] = result.order_id
        record["resolved_at_ms"] = now
        record["resolution"] = "auto"
        _commit(data, record)
        _atomic_write_json(metadata_file, data)
        log.info(
            "reconcile_one: audit_id=%s RESUELTO → order_id=%s provider=%s",
            audit_id, result.order_id, result.provider,
        )
        return ReconciliationOutcome(
            session_key=session_key,
            audit_id=audit_id,
            outcome=OUTCOME_RESOLVED,
            resolved_order_id=result.order_id,
            provider=result.provider,
            attempts=len(new_attempts),
        )

    # Falló de nuevo. ¿Alcanzó el cap con este intento?
    if len(new_attempts) >= max_attempts:
        record["status"] = STATUS_ABANDONED
        record["abandoned_at_ms"] = now
        outcome = OUTCOME_ABANDONED
    else:
        outcome = OUTCOME_STILL_FAILING
    _commit(data, record)
    _atomic_write_json(metadata_file, data)
    log.warning(
        "reconcile_one: audit_id=%s sigue fallando (intento %d/%d): %s",
        audit_id, len(new_attempts), max_attempts, result.error_detail,
    )
    return ReconciliationOutcome(
        session_key=session_key,
        audit_id=audit_id,
        outcome=outcome,
        provider=result.provider,
        error_detail=result.error_detail,
        attempts=len(new_attempts),
    )


def mark_resolved_manually(
    *,
    vault_dir: str | Path,
    session_key: str,
    audit_id: str,
    note: str | None = None,
    resolved_order_id: str | None = None,
) -> ReconciliationOutcome:
    """Marca un record como resuelto a mano (el operador lo registró en Medusa).

    NO toca Medusa — solo actualiza el estado local para sacarlo del banner.
    Idempotente: si ya estaba resuelto, no-op.
    """
    metadata_file = Path(vault_dir) / session_key / "metadata.json"
    data = _read_metadata(metadata_file)
    if data is None:
        return ReconciliationOutcome(
            session_key=session_key,
            audit_id=audit_id,
            outcome=OUTCOME_NOT_FOUND,
            error_detail=f"metadata.json no existe o es ilegible: {metadata_file}",
        )

    located = _locate_record(data, audit_id)
    if located is None:
        return ReconciliationOutcome(
            session_key=session_key,
            audit_id=audit_id,
            outcome=OUTCOME_NOT_FOUND,
            error_detail=f"no se encontró order_id={audit_id} en la sesión",
        )
    record, _commit = located

    if str(record.get("status", STATUS_PENDING)) == STATUS_RESOLVED:
        return ReconciliationOutcome(
            session_key=session_key,
            audit_id=audit_id,
            outcome=OUTCOME_ALREADY_RESOLVED,
            resolved_order_id=record.get("resolved_order_id"),
            attempts=len(record.get("reconciliation_attempts") or []),
        )

    now = _now_ms()
    record["status"] = STATUS_RESOLVED
    record["resolved_at_ms"] = now
    record["resolution"] = "manual"
    if resolved_order_id:
        record["resolved_order_id"] = resolved_order_id
    if note:
        record["resolution_note"] = note
    _commit(data, record)
    _atomic_write_json(metadata_file, data)
    log.info(
        "mark_resolved_manually: audit_id=%s resuelto manualmente (order_id=%s)",
        audit_id, resolved_order_id or "(sin id)",
    )
    return ReconciliationOutcome(
        session_key=session_key,
        audit_id=audit_id,
        outcome=OUTCOME_RESOLVED,
        resolved_order_id=resolved_order_id,
        attempts=len(record.get("reconciliation_attempts") or []),
    )


# ----------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------


def _now_ms() -> int:
    return int(time.time() * 1000)


def _read_metadata(path: Path) -> dict[str, Any] | None:
    """Lee metadata.json. Devuelve None si no existe o está corrupto."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("reconciliation: metadata ilegible %s: %s", path, exc)
        return None


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Escribe JSON atómicamente (tmp + os.replace) para no corromper en crash."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp, path)


def _locate_record(data: dict[str, Any], audit_id: str):
    """Encuentra el record por order_id y devuelve (record, commit_fn).

    `commit_fn(data, record)` re-inserta el record mutado en su sitio
    original (lista `failed_order_registrations[]` o el objeto `registered_order`
    cuando es un stub). Devuelve None si no se encuentra.

    Busca en dos lugares:
      1. `failed_order_registrations[]` — el caso Medusa-rechazó/cayó.
      2. `registered_order` si `provider == "stub"` — el caso Medusa-sin-config
         (el stub reportó éxito al cliente pero no existe en Medusa).
    """
    failed = data.get("failed_order_registrations")
    if isinstance(failed, list):
        for idx, rec in enumerate(failed):
            if isinstance(rec, dict) and str(rec.get("order_id")) == audit_id:
                def _commit(d: dict[str, Any], r: dict[str, Any], _i=idx) -> None:
                    d["failed_order_registrations"][_i] = r
                return rec, _commit

    registered = data.get("registered_order")
    if (
        isinstance(registered, dict)
        and registered.get("provider") == "stub"
        and str(registered.get("order_id")) == audit_id
    ):
        def _commit_stub(d: dict[str, Any], r: dict[str, Any]) -> None:
            d["registered_order"] = r
        return registered, _commit_stub

    return None


def _rebuild_order_args(
    record: dict[str, Any],
) -> tuple[list[OrderItem], OrderShipping, dict[str, Any]]:
    """Reconstruye los argumentos de `port.register_order` desde el record.

    El record guarda `items` (raw del LLM) y `shipping` tal cual los pasó la
    tool. Levanta KeyError/TypeError/ValueError si el shape no es válido — el
    caller lo captura y devuelve OUTCOME_ERROR.
    """
    raw_items = record["items"]
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("record.items vacío o no es lista")
    items = [
        OrderItem(
            handle=str(it["handle"]),
            quantity=int(it["quantity"]),
            unit_price_cop=int(it["unit_price_cop"]),
            variant_label=(
                str(it["variant_label"])
                if it.get("variant_label") is not None
                else None
            ),
        )
        for it in raw_items
    ]
    raw_shipping = record["shipping"]
    shipping = OrderShipping(
        city=str(raw_shipping["city"]),
        neighborhood=str(raw_shipping["neighborhood"]),
        address=str(raw_shipping["address"]),
        phone=str(raw_shipping["phone"]),
        # Records pre-requisito 2026-08-31 no traen estos campos — defaults
        # laxos (el retry no debe morir por un pedido viejo).
        receiver_name=str(raw_shipping.get("receiver_name") or ""),
        national_id=(
            str(raw_shipping["national_id"])
            if raw_shipping.get("national_id")
            else None
        ),
    )
    kwargs = {
        "payment_method": str(record["payment_method"]),
        "subtotal_cop": int(record["subtotal_cop"]),
        "shipping_cop": int(record["shipping_cop"]),
        "total_cop": int(record["total_cop"]),
        "currency": str(record.get("currency", "COP")),
    }
    return items, shipping, kwargs
