"""Un turno simulado del bot de ventas, en el sandbox (plan §3.3–§3.6, PR 11).

Corre el MISMO workflow de ventas de producción que el bot del brazo dice
(registro de bots: A1 el V1 `HubaraSalesSessionWorkflow`; B0 y B el V2
`HubaraSalesSessionWorkflowV2`), con el mismo `run_agent_turn`, la misma
coalescencia de la ráfaga, las mismas guardas y el mismo manejo de episodios.
Lo único distinto:

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
coalescencia arma el turno como en producción; el bot nuevo B lleva el modo
`on` y su perfil en el 4.º argumento, como un canary), cada uno con el
contexto que el ingest le arma (hora de Bogotá, borrador del pedido, carrito
web, producto web, aplazamiento, cupón, foto citada y fuera de catálogo)
después de pasar por el ingest del sandbox (`sandbox/readings.py`). No se
reconstruyen las notas de la respuesta a una campaña ni de la frontera de
episodio: el reporte de fidelidad lo mide.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from src.plugins.chats.agent.sales.decisions.bots import bot_for_arm
from src.plugins.chats.agent.sales.decisions.capabilities import watching_verdicts
from src.plugins.chats.agent.sales_lab.arms import signal_meta
from src.plugins.chats.agent.sales_lab.sandbox.activities import SandboxCapture, sandbox_activities
from src.plugins.chats.agent.sales_lab.sandbox.clock import frozen_clock
from src.plugins.chats.agent.sales_lab.sandbox.decisions import CaseDecisions, case_disagreements, case_redact_terms
from src.plugins.chats.agent.sales_lab.sandbox.materialize import materialize_case, scrub_text
from src.plugins.chats.agent.sales_lab.sandbox.photos import LabPhotoStep
from src.plugins.chats.agent.sales_lab.sandbox.readings import ingest_burst

PROD_SALES_WORKSPACE = "app-hubara-agency-src-plugins-chats-agent-sales-workspace"
DEFAULT_TIMEOUT_S = 600.0


@contextmanager
def _pinned_bot(arm: str):
    """El bot del brazo para todo lo que corre en este proceso (lecturas,
    activities y tools: `bot_for_session` lee `DECISIONS_BOT`)."""
    previous = os.environ.get("DECISIONS_BOT")
    os.environ["DECISIONS_BOT"] = arm
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("DECISIONS_BOT", None)
        else:
            os.environ["DECISIONS_BOT"] = previous


def workspace_slug(path: str) -> str:
    """El slug del historial del LLM, como lo calcula exoclaw
    (`_state_workspace_for`): el path absoluto del workspace de código."""
    return re.sub(r"[^A-Za-z0-9]+", "-", str(Path(path).resolve())).strip("-")


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
    # Cada decisión del motor en el caso (ingest y turno), con su etapa: el
    # rastro que deja en `_decisions/` se borra con el sandbox.
    decisions = CaseDecisions(capture)
    # El bot del brazo vale para TODO el caso: las lecturas del ingest, las
    # activities y las tools consultan el registro de bots (`DECISIONS_BOT`).
    with _pinned_bot(arm), installed_sandbox_ports(
        promotions_path=bench_dir / "promotions.json", catalog=get_catalog_client()
    ), frozen_clock(at_ms), watching_verdicts(decisions):
        # Los workflows y sus activities salen del WORKER de ventas (R-DIP #10: un
        # agente no importa los contratos ni los workflows de otro); se arranca
        # por nombre con la entrada como JSON, igual que el dispatcher: el del
        # bot del brazo (A1 → V1; B0 y B → V2, motor de decisiones F4).
        import src.plugins.chats.workers.sales as sales_worker

        workflow_name = bot_for_arm(arm).workflow

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
        # Cada mensaje de la ráfaga con su evento del dashboard (el que
        # escribió el ingest de producción: `materialize.burst_records`).
        pairs = [
            (m, r)
            for m, r in zip(case.get("burst") or [], box.burst_records)
            if isinstance(m, dict) and str(m.get("text") or "").strip()
        ]
        messages = [m for m, _ in pairs]
        records: list[dict[str, Any]] | None = [r for _, r in pairs]
        if not messages and case.get("trigger") != "handoff":
            messages, records = [{"text": str((case.get("real") or {}).get("inbound_text") or "")}], None
        # El ingest de cada mensaje (`sandbox/readings.py`), con el bot del
        # brazo: las lecturas, el mensaje en el historial del dashboard y el
        # `plugin_context` de su señal (cupón y fuera de catálogo decididos por
        # el motor), lo que en producción pasa antes del turno. En un turno de
        # handoff el resumen de remarketing NO es un mensaje del cliente: solo
        # pasan por el ingest los mensajes que el cliente mandó antes.
        # Las fotos del cliente, como las leyó la visión de hoy al preparar la
        # corrida (`sandbox/photos.py`): dentro del caso solo se lee esa lectura
        # (sin red, sin escribir fuera del sandbox).
        photos = LabPhotoStep(
            media_dir=bench_dir / "vault" / str(case["session_id"]) / "media",
            vision=None,
            identifier=None,
            cache_dir=bench_dir / "photo_reads",
        )
        ingested = await ingest_burst(
            metadata, messages, session_id=box.session_id, vault_dir=box.vault_dir, at_ms=at_ms, records=records,
            catalog=get_catalog_client(), on_message=decisions.ingest_message, between=box.between_records,
            photos=photos,
        )
        result["readings"] = [m.readings for m in ingested]
        result["photos"] = [m.photo for m in ingested if m.photo is not None]
        contexts = [m.context for m in ingested]
        decisions.turn()

        def _args(message: dict[str, Any], context: list[str]) -> list[Any]:
            meta = signal_meta(arm, message)
            base: list[Any] = [str(message.get("text") or ""), None, context]
            return base if meta is None else [*base, meta]

        workflow_id = f"lab-sim-{box.session_id}-{uuid.uuid4().hex[:8]}"
        async with Worker(
            client,
            task_queue=queue,
            workflows=list(sales_worker.SALES_WORKFLOWS),
            activities=activities,
            workflow_runner=sales_worker.otel_workflow_runner(),
        ):
            start = {"session_id": box.session_id, "turn_count": 0, "runtime_workspace_path": None}
            if case.get("trigger") == "handoff":
                signaled: list[list[str]] = []
                handle = await client.start_workflow(workflow_name, start, id=workflow_id, task_queue=queue)
            else:
                # Cada mensaje con SU contexto, como las señales del ingest.
                signaled = contexts
                handle = await client.start_workflow(
                    workflow_name,
                    start,
                    id=workflow_id,
                    task_queue=queue,
                    start_signal="send_message",
                    start_signal_args=_args(messages[0], contexts[0]),
                )
                for message, context in zip(messages[1:], contexts[1:]):
                    await handle.signal("send_message", args=_args(message, context))
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
    # Lo que el turno recibió de las señales, sin repetir (la coalescencia del
    # workflow junta los contextos de la ráfaga igual).
    result["plugin_context"] = list(dict.fromkeys(note for context in signaled for note in context))
    result["tool_replay"] = capture.tool_replay
    # Qué decidió cada capacidad (Jev, la regla, el piso o el respaldo, y por
    # qué) y la cola de desacuerdos del sandbox, antes de que el proceso del
    # caso lo borre; con lo personal del cliente tapado, como en la cola.
    redact = case_redact_terms(metadata, after)
    result["decisions"] = decisions.published(redact=redact)
    result["disagreements"] = case_disagreements(box.vault_dir, redact=redact)
    return result
