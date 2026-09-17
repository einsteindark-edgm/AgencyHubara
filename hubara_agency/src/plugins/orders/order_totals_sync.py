"""Sync del total VIVO de Medusa → `episode.order_total_cop` del chat.

Por qué existe (pedido #31, 2026-09-17): el operador cambió un producto de la
orden desde Medusa Admin. Orders mostró el total nuevo (lo lee en vivo), pero
Ads y Campañas siguieron con el viejo: suman `episode.order_total_cop`, que el
bot congela al registrar la venta (`attach_order_to_active_episode`). Hubara no
se entera de las ediciones hechas en Medusa, así que el barrido periódico de
orders (`OrderReconciliationWorkflow`) copia el total vivo al vault.

  * `apply_live_totals` — mutación pura sobre un `metadata.json`. El primer
    total congelado se preserva en `order_total_cop_at_close` (auditoría).
  * `sync_order_totals` — driver: junta los `order_id` del vault, pagina el
    `OrderQueryPort` hasta encontrarlos todos (cap de páginas) y reescribe
    SOLO los chats que cambiaron (escritura atómica).

Medusa caído → no toca nada. Un total ≤ 0 se ignora (dato roto, no venta).
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.sdk.connectorkit import OrderQueryPort

log = logging.getLogger(__name__)

_MAX_PAGES = 20


@dataclass(frozen=True)
class TotalsSyncSummary:
    """Resumen de un barrido (R-JSON-safe)."""

    wanted_orders: int = 0
    found_orders: int = 0
    updated_sessions: int = 0


def _is_int(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def apply_live_totals(
    metadata: dict[str, Any], totals: dict[str, int], *, now_ms: int
) -> bool:
    """Alinea los totales del chat con `totals` (order_id → COP). Mutates.

    Devuelve True si cambió algo. Toca los episodios con ese `order_id` y el
    `registered_order` de la sesión si es el mismo pedido.
    """
    changed = False
    for ep in metadata.get("episodes") or []:
        if not isinstance(ep, dict):
            continue
        live = totals.get(ep.get("order_id"))  # type: ignore[arg-type]
        if not _is_int(live) or live <= 0:
            continue
        current = ep.get("order_total_cop")
        if _is_int(current) and int(current) == int(live):
            continue
        if "order_total_cop_at_close" not in ep:
            ep["order_total_cop_at_close"] = current
        ep["order_total_cop"] = int(live)
        ep["order_total_synced_at_ms"] = now_ms
        changed = True

    reg = metadata.get("registered_order")
    if isinstance(reg, dict):
        live = totals.get(reg.get("order_id"))  # type: ignore[arg-type]
        current = reg.get("total_cop")
        if _is_int(live) and live > 0 and not (_is_int(current) and int(current) == int(live)):
            reg["total_cop"] = int(live)
            changed = True
    return changed


def _order_ids(metadata: dict[str, Any]) -> set[str]:
    ids = {
        ep.get("order_id")
        for ep in metadata.get("episodes") or []
        if isinstance(ep, dict)
    }
    reg = metadata.get("registered_order")
    if isinstance(reg, dict):
        ids.add(reg.get("order_id"))
    return {i for i in ids if isinstance(i, str) and i}


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".totals.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


async def _fetch_totals(
    port: OrderQueryPort, wanted: set[str], page_size: int
) -> dict[str, int] | None:
    """Pagina hasta encontrar todos los `wanted`. None = Medusa no disponible."""
    totals: dict[str, int] = {}
    offset = 0
    for _ in range(_MAX_PAGES):
        page = await port.list(limit=page_size, offset=offset, include_drafts=True)
        if not page.catalog_available:
            return None
        for summary in page.orders:
            if summary.id in wanted:
                totals[summary.id] = int(summary.total_cop)
        if not page.orders or wanted <= totals.keys():
            break
        offset += page_size
        if offset >= page.count:
            break
    return totals


async def sync_order_totals(
    *,
    vault_dir: str | Path,
    port: OrderQueryPort,
    page_size: int = 100,
    now_ms: int | None = None,
) -> TotalsSyncSummary:
    """Barre el vault y alinea `order_total_cop` con Medusa. Idempotente."""
    ts = now_ms if now_ms is not None else int(time.time() * 1000)
    files = sorted(Path(vault_dir).glob("*/metadata.json"))
    by_file: dict[Path, set[str]] = {}
    for path in files:
        data = _read(path)
        ids = _order_ids(data) if data else set()
        if ids:
            by_file[path] = ids
    wanted = set().union(*by_file.values()) if by_file else set()
    if not wanted:
        return TotalsSyncSummary()

    totals = await _fetch_totals(port, wanted, page_size)
    if totals is None:
        log.warning("order_totals_sync: Medusa no disponible — sin cambios")
        return TotalsSyncSummary(wanted_orders=len(wanted))

    updated = 0
    for path, ids in by_file.items():
        if not ids & totals.keys():
            continue
        # Re-leer justo antes de escribir: achica la ventana de carrera con
        # los workers que escriben el mismo metadata.json.
        data = _read(path)
        if data is None or not apply_live_totals(data, totals, now_ms=ts):
            continue
        _atomic_write(path, data)
        updated += 1
        log.info(
            "order_totals_sync: total actualizado",
            extra={"session_key": path.parent.name},
        )
    return TotalsSyncSummary(
        wanted_orders=len(wanted), found_orders=len(totals), updated_sessions=updated
    )
