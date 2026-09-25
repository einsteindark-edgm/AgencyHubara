"""Un turno simulado del bot de ventas, en el sandbox (plan §3.3–§3.6, PR 11).

Corre el MISMO `HubaraSalesSessionWorkflow` de producción, con el mismo
`run_agent_turn`, la misma coalescencia de la ráfaga, las mismas guardas y el
mismo manejo de episodios. Lo único distinto:

  * el servidor de Temporal (el de la caja; en CI, el de pruebas del SDK);
  * el vault, el historial del LLM y el catálogo: los del sandbox del caso,
    armados desde el banco y cortados al inicio del turno real;
  * los puertos con efectos (Medusa y compañía) y las activities que salen
    del sandbox (ver `sandbox/activities.py` y `src.sdk.labkit`);
  * el reloj de lo que el LLM ve (saludo, hora de Bogotá, "Current Time").

El proceso tiene que traer el entorno del caso ya preparado
(`sandbox/env.py`) ANTES de importar la app: un proceso por caso. El
entrypoint de la caja (`sandbox/entrypoint.py`) hace eso.

Lo que el turno recibe: los mensajes de la ráfaga real, uno por señal (la
coalescencia arma el turno como en producción; los bots nuevos B y C llevan
el modo `on` y su perfil en el 4.º argumento, como un canary), y el contexto que el ingest
agrega y que sale de la metadata (hora de Bogotá, borrador del pedido,
carrito web, producto web, aplazamiento, cupón). No se reconstruyen las
notas del ingest que dependen del mensaje entrante (respuesta a campaña,
frontera de episodio, cita de foto): el reporte de fidelidad lo mide.
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.plugins.chats.agent.sales_lab.arms import signal_meta
from src.plugins.chats.agent.sales_lab.sandbox.activities import SandboxCapture, sandbox_activities
from src.plugins.chats.agent.sales_lab.sandbox.clock import frozen_clock
from src.plugins.chats.agent.sales_lab.sandbox.materialize import materialize_case, scrub_text

PROD_SALES_WORKSPACE = "app-hubara-agency-src-plugins-chats-agent-sales-workspace"
DEFAULT_TIMEOUT_S = 600.0
SALES_WORKFLOW = "HubaraSalesSessionWorkflow"


def workspace_slug(path: str) -> str:
    """El slug del historial del LLM, como lo calcula exoclaw
    (`_state_workspace_for`): el path absoluto del workspace de código."""
    return re.sub(r"[^A-Za-z0-9]+", "-", str(Path(path).resolve())).strip("-")


def turn_context(metadata: dict[str, Any], *, at_ms: int) -> list[str]:
    """El `plugin_context` que el ingest habría armado desde la metadata."""
    from src.plugins.chats.agent.sales.context import build_bogota_context_string
    from src.plugins.chats.agent.sales.use_cases.coupons import build_coupon_note
    from src.plugins.chats.agent.sales.use_cases.order_draft import build_order_draft_note, get_projectable_draft
    from src.plugins.chats.agent.sales.use_cases.web_cart import build_web_cart_note
    from src.plugins.chats.agent.sales.use_cases.web_product_ref import build_web_product_note
    from src.plugins.chats.shared.purchase_signals import build_deferral_note
    from src.sdk.messagingkit import fresh_resume_label

    draft = get_projectable_draft(metadata)
    notes = [
        build_deferral_note(metadata, resume_label=fresh_resume_label(metadata)),
        build_web_cart_note(metadata),
        build_web_product_note(metadata),
        build_order_draft_note(draft) if draft else None,
        build_coupon_note(metadata),
    ]
    bogota = build_bogota_context_string(now=datetime.fromtimestamp(at_ms / 1000, tz=timezone.utc))
    return [bogota, *(n for n in notes if n)]


def llm_cost_usd(metadata: dict[str, Any]) -> float:
    """Costo LLM acumulado en la metadata (`episodes[].llm_usage.cost_usd`,
    lo escribe `record_episode_llm_usage`). El del caso = después − antes."""
    total = 0.0
    for ep in metadata.get("episodes") or []:
        usage = ep.get("llm_usage") if isinstance(ep, dict) else None
        if isinstance(usage, dict) and isinstance(usage.get("cost_usd"), (int, float)):
            total += float(usage["cost_usd"])
    return total


def _jsonl_rows(path: Path) -> list[dict[str, Any]]:
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def classifier_cost_usd(traces: list[dict[str, Any]]) -> float:
    """Lo que costó el clasificador (percepción + verificación) en el caso:
    no pasa por el LLM del agente, así que no está en `llm_usage`."""
    total = 0.0
    for trace in traces:
        for step in trace.get("steps") or []:
            if isinstance(step, dict) and step.get("kind") in ("perception", "verify"):
                cost = step.get("cost_usd")
                if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                    total += float(cost)
    return total


async def run_case(
    case: dict[str, Any],
    *,
    bench_dir: Path,
    sandbox_dir: Path,
    client: Any,
    task_queue: str | None = None,
    llm_chat: Any | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    bench_workspace: str = PROD_SALES_WORKSPACE,
    arm: str = "A1",
) -> dict[str, Any]:
    from temporalio.worker import Worker

    from src.plugins.chats.agent.sales.config.env import get_workspace_path
    from src.sdk.connectorkit import get_catalog_client
    from src.sdk.labkit import installed_sandbox_ports

    workspace_path = str(Path(get_workspace_path()).resolve())
    box = materialize_case(
        bench_dir,
        case,
        sandbox_dir,
        bench_workspace=bench_workspace,
        sales_workspace=workspace_slug(workspace_path),
        sales_workspace_path=workspace_path,
    )
    metadata_path = box.vault_dir / box.session_id / "metadata.json"
    traces_path = box.vault_dir / box.session_id / "evals" / "turn_traces.jsonl"
    traces_before = len(_jsonl_rows(traces_path))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    cost_before = llm_cost_usd(metadata)
    at_ms = int(case["at_ms"])
    queue = task_queue or f"lab-sim-{uuid.uuid4().hex[:12]}"
    capture = SandboxCapture()
    result: dict[str, Any] = {
        "case_id": case.get("case_id"),
        "session_id": case.get("session_id"),
        "sim_session_id": box.session_id,
        "turn_key": case.get("turn_key"),
        "arm": arm,
        "error": None,
    }
    with installed_sandbox_ports(promotions_path=bench_dir / "promotions.json", catalog=get_catalog_client()), frozen_clock(at_ms):
        # El workflow y sus activities salen del WORKER de ventas (R-DIP #10: un
        # agente no importa los contratos ni los workflows de otro); se arranca
        # por nombre con la entrada como JSON, igual que el dispatcher.
        import src.plugins.chats.workers.sales as sales_worker

        workflow_cls = sales_worker.HubaraSalesSessionWorkflow

        activities = sandbox_activities(
            sales_worker.SALES_ACTIVITIES,
            capture=capture,
            llm_chat=llm_chat,
            # Lo que las tools de lectura del pedido devolvieron en el turno real,
            # con el número ficticio del sandbox (el real nunca entra al sandbox).
            recorded_tools=[
                {**r, "content": scrub_text(str(r.get("content") or ""), str(case["session_id"]), box.session_id)}
                for r in case.get("recorded_tools") or []
                if isinstance(r, dict)
            ],
        )
        context = turn_context(metadata, at_ms=at_ms)
        messages = [m for m in case.get("burst") or [] if isinstance(m, dict) and str(m.get("text") or "").strip()]
        if not messages:
            messages = [{"text": str((case.get("real") or {}).get("inbound_text") or "")}]

        def _args(message: dict[str, Any]) -> list[Any]:
            meta = signal_meta(arm, message)
            base: list[Any] = [str(message.get("text") or ""), None, context]
            return base if meta is None else [*base, meta]

        workflow_id = f"lab-sim-{box.session_id}-{uuid.uuid4().hex[:8]}"
        async with Worker(
            client,
            task_queue=queue,
            workflows=[workflow_cls],
            activities=activities,
            workflow_runner=sales_worker.otel_workflow_runner(),
        ):
            start = {"session_id": box.session_id, "turn_count": 0, "runtime_workspace_path": None}
            if case.get("trigger") == "handoff":
                handle = await client.start_workflow(SALES_WORKFLOW, start, id=workflow_id, task_queue=queue)
            else:
                handle = await client.start_workflow(
                    SALES_WORKFLOW,
                    start,
                    id=workflow_id,
                    task_queue=queue,
                    start_signal="send_message",
                    start_signal_args=_args(messages[0]),
                )
                for message in messages[1:]:
                    await handle.signal("send_message", args=_args(message))
            try:
                await asyncio.wait_for(capture.turn_done.wait(), timeout=timeout_s)
            except TimeoutError:
                result["error"] = f"el turno no terminó en {int(timeout_s)} s"
            finally:
                try:
                    await handle.terminate("laboratorio: turno simulado terminado")
                except Exception:  # noqa: BLE001 — el workflow pudo haber cerrado solo (escalación)
                    pass
    # Las trazas de ESTE caso: la del turno y, si la verificación agendó un
    # complemento, la de ese segundo turno (parte de la respuesta del caso).
    new_traces = _jsonl_rows(traces_path)[traces_before:]
    result["trace"] = new_traces[0] if new_traces else None
    result["complement_trace"] = next((t for t in new_traces[1:] if t.get("trigger") == "complement"), None)
    try:
        after = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        after = metadata
    llm = round(max(llm_cost_usd(after) - cost_before, 0.0), 8)
    classifier = round(classifier_cost_usd(new_traces), 8)
    result["llm_cost_usd"] = llm
    result["perception_cost_usd"] = classifier
    result["cost_usd"] = round(llm + classifier, 8)
    result["effects"] = capture.effects
    result["plugin_context"] = context
    result["tool_replay"] = capture.tool_replay
    return result
