"""Batch driver de reconciliación — escanea pendientes y reintenta cada uno.

Vive en el plugin `orders` (no en `platform/`) porque combina DOS piezas:
  * `scan_vault_orders` (este plugin) — encuentra los pendientes en el vault.
  * `reconcile_one` (platform/orders/reconciliation) — el núcleo idempotente.

R-DIP: plugin → platform está permitido. El núcleo (`reconcile_one`) NO puede
importar el scanner (sería platform → plugin), así que el "barrer todos" se
ensambla acá, del lado del plugin.

Quién lo dispara:
  * `scripts/reconcile_pending_orders.py` — CLI operacionalizable como
    k8s CronJob / crontab / Temporal Schedule (cada N minutos). Idempotente:
    correrlo de más es seguro.
  * (Futuro) una `@activity.defn` Temporal que lo envuelva, si se quiere
    durabilidad de ejecución intra-run — la lógica ya está acá, lista.

Solo reintenta los `status="pending"`. Los `abandoned` (auto-retry agotado)
quedan para intervención manual desde el dashboard — NO se re-reintentan en
cada tick.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from src.platform.orders.port import OrderRegistrationPort
from src.platform.orders.reconciliation import (
    DEFAULT_MAX_ATTEMPTS,
    OUTCOME_ABANDONED,
    OUTCOME_ERROR,
    OUTCOME_RESOLVED,
    OUTCOME_STILL_FAILING,
    STATUS_PENDING,
    ReconciliationOutcome,
    reconcile_one,
)
from src.plugins.orders.vault_scanner import scan_vault_orders

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReconciliationSummary:
    """Resumen de un barrido de reconciliación (R-JSON-safe)."""

    total: int          # pendientes encontrados y procesados
    resolved: int       # reintentos exitosos (ahora en Medusa)
    still_failing: int  # siguen fallando (Medusa aún caído)
    abandoned: int      # alcanzaron max_attempts en esta corrida
    errors: int         # records malformados
    outcomes: list[ReconciliationOutcome] = field(default_factory=list)


async def reconcile_all_pending(
    *,
    vault_dir: str | Path,
    port: OrderRegistrationPort,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> ReconciliationSummary:
    """Escanea el vault y reintenta cada pedido `pending` una vez.

    Devuelve un `ReconciliationSummary`. Idempotente: los ya resueltos no
    aparecen en el scan; los abandoned se excluyen del reintento.
    """
    pending = [
        r for r in scan_vault_orders(vault_dir)
        if r.status == STATUS_PENDING
    ]
    log.info("reconcile_all_pending: %d pedidos pendientes", len(pending))

    outcomes: list[ReconciliationOutcome] = []
    for r in pending:
        outcome = await reconcile_one(
            vault_dir=vault_dir,
            session_key=r.session_key,
            audit_id=r.order_id,
            port=port,
            max_attempts=max_attempts,
        )
        outcomes.append(outcome)

    summary = ReconciliationSummary(
        total=len(outcomes),
        resolved=sum(1 for o in outcomes if o.outcome == OUTCOME_RESOLVED),
        still_failing=sum(1 for o in outcomes if o.outcome == OUTCOME_STILL_FAILING),
        abandoned=sum(1 for o in outcomes if o.outcome == OUTCOME_ABANDONED),
        errors=sum(1 for o in outcomes if o.outcome == OUTCOME_ERROR),
        outcomes=outcomes,
    )
    log.info(
        "reconcile_all_pending: total=%d resolved=%d still_failing=%d "
        "abandoned=%d errors=%d",
        summary.total, summary.resolved, summary.still_failing,
        summary.abandoned, summary.errors,
    )
    return summary
