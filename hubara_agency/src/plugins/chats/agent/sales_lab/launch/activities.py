"""Activities del lanzador de corridas (worker `sales_eval` de producción).

Plan §3.4 y §3.7. Solo LEEN el vault; escriben en el S3 del laboratorio
(`bench/`, `orders/`) y hablan con la caja por SSM (lanzador). Nada acá tiene
red hacia la caja ni llaves suyas.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

from src.plugins.chats.agent.sales_lab.launch.bench_export import exclude_test_orders, plan_bench_export, upload_bench
from src.plugins.chats.agent.sales_lab.launch.contracts import (
    TERMINAL_PHASES,
    BenchInfo,
    DispatchInput,
    ExportBenchInput,
    LabOrder,
    LabProgress,
    PollInput,
)
from src.sdk.connectorkit import OrderFactsSnapshot, get_order_facts_port, get_promotions_port
from src.sdk.labkit import LabStorePort, get_lab_launcher, get_lab_store
from src.sdk.runtime import WORKSPACE_VAULT_DIR, with_heartbeat


#: Si Medusa no contesta en este tiempo, el banco sale sin excluir pedidos de prueba y con nota.
_ORDER_FACTS_TIMEOUT_S = 120.0


def _vault_dir() -> Path:
    return Path(WORKSPACE_VAULT_DIR)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _store() -> LabStorePort:
    store = get_lab_store()
    if store is None:
        raise ApplicationError(
            "el laboratorio no tiene almacén: falta LAB_BUCKET (terraform de compute)", non_retryable=True
        )
    return store


def _internal_numbers() -> list[str]:
    return [n.strip() for n in (os.getenv("LAB_INTERNAL_NUMBERS") or "").split(",") if n.strip()]


async def _order_facts(order_ids: frozenset[str]) -> OrderFactsSnapshot:
    """Los pedidos del banco según OrderFacts, leídos de Medusa en el momento: el
    store de este worker no oye los eventos del dashboard (viven en la API) y un
    valor vencido se serviría tal cual, sin la marca "prueba" que se puso
    después. Nunca tumba el export: si falla o no contesta a tiempo, quedan sin
    resolver y el manifiesto lo anota."""
    try:
        port = get_order_facts_port()
        for order_id in order_ids:
            port.invalidate(order_id)
        return await asyncio.wait_for(port.get_facts(order_ids), timeout=_ORDER_FACTS_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — OrderFacts caído no tumba el banco
        activity.logger.warning("lab.bench_order_facts_unavailable", extra={"error": str(exc)[:200]})
        return OrderFactsSnapshot(unresolved=frozenset(order_ids), stale=True)


def _jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    return value


@activity.defn(name="lab_export_bench_snapshot")
@with_heartbeat(every=10)
async def export_bench_snapshot_activity(inp: ExportBenchInput) -> BenchInfo:
    """Arma el banco y lo sube archivo por archivo (el manifiesto al final)."""
    store = _store()
    vault = _vault_dir()
    state_dir = Path(os.getenv("EXOCLAW_STATE_DIR") or vault / "agent_state")
    catalog_dir = Path(os.getenv("CATALOG_SNAPSHOT_DIR") or vault / "catalog")
    plan = await asyncio.to_thread(
        plan_bench_export,
        vault,
        state_dir=state_dir,
        catalog_dir=catalog_dir,
        bench_id=inp.bench_id,
        since_ms=inp.since_ms,
        now_ms=_now_ms(),
        internal_numbers=_internal_numbers(),
    )
    snapshot = await _order_facts(plan.order_ids)
    plan = exclude_test_orders(plan, snapshot)
    promotions = [_jsonable(p) for p in await get_promotions_port().list_active()]
    in_bench = plan.order_ids
    facts = {oid: _jsonable(f) for oid, f in snapshot.facts.items() if oid in in_bench}
    extra = {
        "promotions.json": json.dumps(promotions, ensure_ascii=False, default=str).encode(),
        "order_facts.json": json.dumps(facts, ensure_ascii=False, default=str).encode(),
    }
    result = await asyncio.to_thread(upload_bench, plan, store, extra=extra)
    activity.logger.info(
        "lab.bench_exported",
        extra={"bench_id": inp.bench_id, "files": result.files, "bytes": result.bytes_uploaded, "notes": list(plan.notes)},
    )
    return BenchInfo(
        bench_id=inp.bench_id,
        sessions=len(plan.sessions),
        customer_turns=plan.customer_turns,
        files=result.files,
    )


@activity.defn(name="lab_read_bench_info")
async def read_bench_info_activity(bench_id: str) -> BenchInfo:
    raw = await asyncio.to_thread(_store().get_bytes, f"bench/{bench_id}/manifest.json")
    if raw is None:
        raise ApplicationError(f"el banco {bench_id} no existe o está incompleto (sin manifiesto)", non_retryable=True)
    manifest = json.loads(raw)
    counts = manifest.get("counts") or {}
    return BenchInfo(
        bench_id=bench_id,
        sessions=int(counts.get("sessions") or 0),
        customer_turns=int(counts.get("customer_turns") or 0),
        files=int(counts.get("files") or 0),
    )


@activity.defn(name="lab_write_order")
async def write_order_activity(order: LabOrder) -> None:
    data = json.dumps(asdict(order), ensure_ascii=False).encode()
    await asyncio.to_thread(_store().put_bytes, f"orders/{order.run_id}.json", data)


@activity.defn(name="lab_start_box")
@with_heartbeat(every=10)
async def start_box_activity() -> None:
    await asyncio.to_thread(get_lab_launcher().start_box)


@activity.defn(name="lab_dispatch_run")
async def dispatch_run_activity(inp: DispatchInput) -> str:
    """Idempotente: si se reintenta, la caja reconoce el id (`already_dispatched`)."""
    return await asyncio.to_thread(get_lab_launcher().dispatch, inp.run_id, inp.image)


@activity.defn(name="lab_cancel_run")
async def cancel_run_activity(run_id: str) -> str:
    return await asyncio.to_thread(get_lab_launcher().cancel, run_id)


def _progress(raw: bytes | None, run_id: str) -> LabProgress | None:
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return LabProgress(
        run_id=run_id,
        phase=str(data.get("phase") or "preparing"),
        turns_done=int(data.get("turns_done") or 0),
        turns_total=int(data.get("turns_total") or 0),
        spent_usd=float(data.get("spent_usd") or 0.0),
        error=data.get("error") if isinstance(data.get("error"), str) else None,
        started_at_ms=data.get("started_at_ms") if isinstance(data.get("started_at_ms"), int) else None,
        updated_at_ms=data.get("updated_at_ms") if isinstance(data.get("updated_at_ms"), int) else None,
    )


@activity.defn(name="lab_poll_run")
async def poll_run_activity(inp: PollInput) -> LabProgress:
    """Sigue `runs/<corrida>/progress.json` hasta una fase terminal. Heartbeat
    con el último avance; si la caja nunca reporta o deja de reportar, la
    corrida falla con el motivo (sin reintentos: no se relanza sola)."""
    store = _store()
    key = f"runs/{inp.run_id}/progress.json"
    started = time.monotonic()
    while True:
        progress = _progress(await asyncio.to_thread(store.get_bytes, key), inp.run_id)
        activity.heartbeat(asdict(progress) if progress else {"phase": "waiting_box"})
        if progress is not None and progress.phase in TERMINAL_PHASES:
            return progress
        if progress is None and time.monotonic() - started > inp.start_grace_s:
            raise ApplicationError("la caja no reportó avance: revisar el runner en la caja", non_retryable=True)
        if (
            progress is not None
            and progress.updated_at_ms is not None
            and _now_ms() - progress.updated_at_ms > inp.stale_after_s * 1000
        ):
            raise ApplicationError(
                f"la caja dejó de reportar hace más de {int(inp.stale_after_s // 60)} min", non_retryable=True
            )
        await asyncio.sleep(inp.poll_s)


LAB_LAUNCH_ACTIVITIES = [
    export_bench_snapshot_activity,
    read_bench_info_activity,
    write_order_activity,
    start_box_activity,
    dispatch_run_activity,
    cancel_run_activity,
    poll_run_activity,
]
