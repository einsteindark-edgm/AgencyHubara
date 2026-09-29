"""Las lecturas del ingest dentro del sandbox (motor de decisiones F2).

El sandbox arranca el workflow directo (no pasa por el webhook), así que el
paso de lecturas del ingest no corría: el aplazamiento nunca se registraba,
el check CON-02 no se juzgaba en los brazos simulados y la confirmación de
compra llegaba con estado inconsistente. Acá cada mensaje de la ráfaga pasa
por el MISMO proveedor de lecturas (`EngineReadings`, con el bot del brazo) y
la MISMA escritura (`apply_readings`) que en producción, en orden, sobre el
metadata del sandbox.

Como el ingest, cada mensaje queda en el historial del dashboard DESPUÉS de
sus lecturas (que leen lo que el cliente vio antes de él) y antes del turno:
las capacidades que leen el historial durante el turno (`datos`,
`item_del_pedido`, el contexto de Jev con la cita del cliente) ven la ráfaga
igual que en producción.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def _history_path(vault_dir: Path, session_id: str) -> Path:
    return Path(vault_dir) / session_id / "sessions" / f"{session_id}.jsonl"


def append_history_event(vault_dir: Path, session_id: str, event: dict[str, Any]) -> None:
    """Una línea más del historial del dashboard, con el formato del store
    de producción (`FilesystemMessageHistoryStore`)."""
    path = _history_path(vault_dir, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _history(vault_dir: Path, session_id: str) -> list[dict[str, Any]]:
    path = _history_path(vault_dir, session_id)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            events.append(row)
    return events


async def apply_burst_readings(
    metadata: dict[str, Any],
    messages: Sequence[dict[str, Any]],
    *,
    session_id: str,
    vault_dir: Path,
    at_ms: int,
    records: Sequence[dict[str, Any]] | None = None,
) -> list[list[dict[str, Any]]]:
    """Corre las lecturas de cada mensaje, las escribe en `metadata`
    (mutación) y deja el mensaje en el historial del dashboard. `records`:
    el evento del dashboard de cada mensaje, como lo escribió el ingest de
    producción (`materialize.burst_records`); sin él, uno con la misma forma.
    Devuelve la traza de las capacidades por mensaje."""
    from src.plugins.chats.agent.sales.decisions.readings import EngineReadings, Inbound, apply_readings
    from src.plugins.chats.agent.sales.use_cases.funnel_stage import resolve_funnel_stage
    from src.plugins.chats.agent.sales_lab.sandbox.materialize import burst_record
    from src.sdk.messagingkit import opt_out_campaign_id, resolve_local_timezone

    tz = resolve_local_timezone(session_id)
    provider = EngineReadings(Path(vault_dir))
    traces: list[list[dict[str, Any]]] = []
    for k, message in enumerate(messages, 1):
        # Lo que el cliente vio ANTES de este mensaje (el ingest lo lee del
        # historial antes de guardar el mensaje).
        events = _history(vault_dir, session_id)
        # Como el ingest: las lecturas leen lo que escribió el cliente, no el
        # turno con lo que agregó el ingest (campaña citada, episodio anterior).
        text = str(message.get("raw_text") or message.get("text") or "") or None
        ts = message.get("ts_ms")
        now_ms = int(ts) if isinstance(ts, (int, float)) and not isinstance(ts, bool) else int(at_ms)
        wamid = str(message.get("wamid") or f"lab.{k}")
        readings = await provider.read(
            Inbound(
                session_id=session_id, text=text, now_ms=now_ms, message_id=wamid, metadata=metadata,
                events=events, stage=resolve_funnel_stage(metadata), tz=tz,
            )
        )
        apply_readings(
            metadata, readings, text=text, now_ms=now_ms, message_id=wamid, tz=tz,
            opt_out_campaign_id=opt_out_campaign_id(metadata, now_ms),
        )
        record = records[k - 1] if records is not None and k <= len(records) else burst_record(message, at_ms=at_ms)
        append_history_event(vault_dir, session_id, record)
        # Como el ingest: la señal vale para el ÚLTIMO mensaje del cliente.
        metadata["last_inbound_message_id"] = wamid
        traces.append(list(readings.verdicts))
    return traces
