"""Vault scanner — lee `failed_order_registrations[]` + stub orders del vault.

Premortem F2 + K1: Medusa puede caer durante un cierre de venta, dejando el
intento en `metadata.failed_order_registrations[]`. Si nadie surface esos
intentos, el operador se entera SOLO por el escalation a humano — que puede
perderse si el operador trabaja en backlog.

Este modulo escanea `hubara_vault/wa_*/metadata.json` y agrega los registros
relevantes: pedidos rechazados por Medusa + pedidos registrados con
`provider=stub` (que NO están en Medusa).

R-DIP: este modulo vive en el plugin `orders` y lee filesystem directamente.
NO importa de ningun agente sibling. Aceptable porque es lectura simple
de un path bien conocido (`WORKSPACE_VAULT_DIR`).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.platform.orders.reconciliation import STATUS_PENDING, STATUS_RESOLVED

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class VaultOrderRecord:
    """Un registro de orden encontrado en el vault local (NO en Medusa).

    Origenes posibles:
      * `kind="failed"`: intento de registro que Medusa rechazo (5xx, network,
        config). El operador debe registrarlo manualmente o esperar a que
        Medusa vuelva y reintentar.
      * `kind="stub"`: pedido registrado por StubOrderRegistration (cuando
        Medusa no estaba configurado al momento del cierre). NO esta en
        Medusa, pero el cliente ya recibio confirmacion del agente.
    """
    kind: str           # "failed" | "stub"
    session_key: str    # session WA del cliente
    order_id: str       # local audit id (puede ser HUB-* o AUDIT-*)
    customer_phone: str | None
    customer_city: str | None
    total_cop: int
    currency: str
    items_count: int
    payment_method: str | None
    error_detail: str | None   # solo para kind="failed"
    registered_at_ms: int
    status: str = STATUS_PENDING       # pending | resolved | abandoned
    attempts: int = 0                  # reintentos de reconciliación acumulados
    raw: dict[str, Any] = field(default_factory=dict)


def scan_vault_orders(
    vault_dir: Path | str, *, include_resolved: bool = False
) -> list[VaultOrderRecord]:
    """Walk `vault_dir/wa_*/metadata.json` y extrae:
      * Todos los `failed_order_registrations[]` (kind=failed).
      * Los `registered_order` con `provider=stub` (kind=stub).

    Ordenados por `registered_at_ms` descendente (más recientes primero).

    Por default OMITE los records ya resueltos (`status="resolved"`) — el
    banner solo debe mostrar lo que falta reconciliar. Los `abandoned`
    (auto-retry agotado) SÍ se muestran: necesitan intervención manual.
    Pasá `include_resolved=True` para traer todo (auditoría / histórico).

    Records legacy sin campo `status` se tratan como `pending` (se muestran).

    Si el vault no existe o esta vacio, devuelve `[]`. Cualquier
    metadata.json corrupto se ignora con log warning.
    """
    vault = Path(vault_dir)
    if not vault.exists():
        log.debug("scan_vault_orders: vault dir %s does not exist", vault)
        return []

    records: list[VaultOrderRecord] = []
    for session_dir in sorted(vault.iterdir()):
        if not session_dir.is_dir() or not session_dir.name.startswith("wa_"):
            continue
        metadata_file = session_dir / "metadata.json"
        if not metadata_file.exists():
            continue
        try:
            data = json.loads(metadata_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning(
                "scan_vault_orders: skipping corrupt metadata %s: %s",
                metadata_file, exc,
            )
            continue

        session_key = session_dir.name

        # Failed registrations.
        for failed in data.get("failed_order_registrations") or []:
            rec = _to_record(failed, kind="failed", session_key=session_key)
            if include_resolved or rec.status != STATUS_RESOLVED:
                records.append(rec)

        # Stub orders (registered exitosamente pero solo localmente).
        registered = data.get("registered_order")
        if isinstance(registered, dict) and registered.get("provider") == "stub":
            rec = _to_record(registered, kind="stub", session_key=session_key)
            if include_resolved or rec.status != STATUS_RESOLVED:
                records.append(rec)

    records.sort(key=lambda r: r.registered_at_ms, reverse=True)
    return records


def _to_record(
    raw: dict[str, Any], *, kind: str, session_key: str
) -> VaultOrderRecord:
    shipping = raw.get("shipping") or {}
    items = raw.get("items") or []
    return VaultOrderRecord(
        kind=kind,
        session_key=session_key,
        order_id=str(raw.get("order_id") or raw.get("audit_id") or "unknown"),
        customer_phone=shipping.get("phone"),
        customer_city=shipping.get("city"),
        total_cop=int(raw.get("total_cop") or 0),
        currency=str(raw.get("currency") or "COP"),
        items_count=len(items),
        payment_method=raw.get("payment_method"),
        error_detail=raw.get("error_detail"),
        registered_at_ms=int(raw.get("registered_at_ms") or 0),
        status=str(raw.get("status") or STATUS_PENDING),
        attempts=len(raw.get("reconciliation_attempts") or []),
        raw=raw,
    )
