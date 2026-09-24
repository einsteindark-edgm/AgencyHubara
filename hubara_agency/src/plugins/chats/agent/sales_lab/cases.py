"""Armado de casos del laboratorio: un caso por turno real del cliente. PURO.

Plan §0 ("teacher forcing") y PR 8. Cada bot responde cada turno sobre el
PREFIJO REAL de la conversación: no se inventa al cliente. El caso guarda
dónde cortar (el sandbox del PR 11 materializa los archivos truncados) y el
control (la salida real del bot, A0).

Lee un banco ya bajado a disco (`<bench>/vault/<sid>/…`,
`<bench>/agent_state/<workspace>/sessions/<sid>.jsonl`, `<bench>/manifest.json`).

Reglas:
  * caso = traza con `trigger` customer o handoff y `turn_started_ms` desde el
    corte del banco. El ghosting es un turno del sistema: exclusión con motivo.
  * ráfaga = los mensajes del cliente desde la última respuesta (del bot o de
    una persona del equipo) hasta el inicio del turno. Si la traza trae
    `inbound[]` (traza v2) se usan sus wamid.
  * prefijo del dashboard = eventos ANTES del primer mensaje de la ráfaga;
    prefijo del LLM = mensajes grabados ANTES del inicio del turno (la línea
    de metadatos de exoclaw no cuenta).
  * episodios del momento = los que ya habían empezado; uno que cerró después
    se ve abierto (sin los campos del cierre).
  * estado al INICIO del turno (`draft_before`, `state_before`) = el que dejó
    la traza anterior: el borrador y el cierre/orden, de la traza anterior del
    MISMO episodio; el tag, la ruta y la escalación (de la sesión), de la
    última traza anterior de cualquier episodio. `draft` y `state` son los de
    la traza del turno: el estado al TERMINAR (lo que evalúa el scorecard).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.plugins.chats.agent.sales_lab.recorded_tools import recorded_tool_results

_CASE_TRIGGERS = frozenset({"customer", "handoff"})
_CLOSING_FIELDS = ("closed_at_ms", "closing_tag", "closing_motivo")
_REAL_FIELDS = (
    "inbound_text",
    "sent_texts",
    "tools",
    "guards",
    "llm_text",
    "suppressed_reason",
    "discarded_narration",
    "stage_out",
    "steps",
    "first_contact",
)


@dataclass(frozen=True)
class LabCase:
    case_id: str
    session_id: str
    episode_id: str
    turn: int
    turn_key: str
    at_ms: int
    trigger: str
    burst: list[dict[str, Any]]
    dashboard_prefix: int
    llm_prefix: int
    stage_in: str | None
    draft: dict[str, Any]
    state: dict[str, Any]
    episodes_at: list[dict[str, Any]]
    real: dict[str, Any] = field(default_factory=dict)
    draft_before: dict[str, Any] = field(default_factory=dict)
    state_before: dict[str, Any] = field(default_factory=dict)
    first_in_episode: bool = True
    # Lo que devolvieron en el turno real las tools que leen el pedido en vivo
    # (`recorded_tools.REPLAYED_TOOLS`): el sandbox se lo da al bot simulado.
    recorded_tools: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CaseSet:
    cases: tuple[LabCase, ...]
    exclusions: tuple[tuple[str, str], ...]


def _ms(value: Any) -> int | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    return None


def _jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def _burst(events: list[dict[str, Any]], started_ms: int) -> tuple[list[dict[str, Any]], int]:
    """(mensajes de la ráfaga, cantidad de eventos del dashboard antes de ella)."""
    timed = [(e, _ms(e.get("timestamp"))) for e in events]
    last_reply = max(
        (ts for e, ts in timed if ts is not None and ts <= started_ms and e.get("role") == "assistant"),
        default=None,
    )
    burst: list[dict[str, Any]] = []
    first_index: int | None = None
    for i, (e, ts) in enumerate(timed):
        if ts is None or ts > started_ms or e.get("role") != "user":
            continue
        if last_reply is not None and ts <= last_reply:
            continue
        if first_index is None:
            first_index = i
        burst.append({"text": str(e.get("content") or ""), "ts_ms": ts, "wamid": e.get("wamid")})
    prefix = first_index if first_index is not None else sum(1 for _, ts in timed if ts is not None and ts <= started_ms)
    return burst, prefix


def _llm_prefix(lines: list[dict[str, Any]], started_ms: int) -> int:
    return sum(
        1
        for line in lines
        if line.get("_type") != "metadata" and (_ms(line.get("timestamp")) or 0) < started_ms
    )


def _episodes_at(episodes: list[Any], started_ms: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ep in episodes:
        if not isinstance(ep, dict):
            continue
        started = _ms(ep.get("started_at_ms"))
        if started is not None and started > started_ms:
            continue
        closed = _ms(ep.get("closed_at_ms"))
        if closed is not None and closed > started_ms:
            ep = {k: v for k, v in ep.items() if k not in _CLOSING_FIELDS} | {"closed_at_ms": None}
        out.append(dict(ep))
    return out


_SESSION_STATE = ("tag", "route", "escalation_reason")
_EPISODE_STATE = ("closing_tag", "order_id")


def _state_before(prev_any: dict[str, Any] | None, prev_same: dict[str, Any] | None) -> dict[str, Any]:
    session = (prev_any or {}).get("state") or {}
    episode = (prev_same or {}).get("state") or {}
    return {k: session.get(k) for k in _SESSION_STATE} | {k: episode.get(k) for k in _EPISODE_STATE}


def build_cases(bench_dir: Path, *, sales_workspace: str) -> CaseSet:
    manifest = json.loads((bench_dir / "manifest.json").read_text(encoding="utf-8"))
    since_ms = int(manifest.get("since_ms") or 0)
    cases: list[LabCase] = []
    exclusions: list[tuple[str, str]] = []
    for sid in manifest.get("sessions") or []:
        sdir = bench_dir / "vault" / sid
        try:
            metadata = json.loads((sdir / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            exclusions.append((sid, "sin_metadata"))
            continue
        events = _jsonl(sdir / "sessions" / f"{sid}.jsonl")
        llm_lines = _jsonl(bench_dir / "agent_state" / sales_workspace / "sessions" / f"{sid}.jsonl")
        traces = sorted(
            _jsonl(sdir / "evals" / "turn_traces.jsonl"),
            key=lambda t: (_ms(t.get("turn_started_ms")) or 0, int(t.get("turn") or 0)),
        )
        previous: list[dict[str, Any]] = []
        for trace in traces:
            prev_any = previous[-1] if previous else None
            prev_same = next((t for t in reversed(previous) if t.get("episode_id") == trace.get("episode_id")), None)
            previous.append(trace)
            started = _ms(trace.get("turn_started_ms"))
            if started is None or started < since_ms:
                continue
            episode_id = str(trace.get("episode_id") or "")
            turn = int(trace.get("turn") or 0)
            case_id = f"{sid}/{episode_id}/t{turn}"
            trigger = str(trace.get("trigger") or "customer")
            if trigger not in _CASE_TRIGGERS:
                exclusions.append((case_id, "turno_del_sistema"))
                continue
            burst, dashboard_prefix = _burst(events, started)
            inbound = trace.get("inbound")
            if isinstance(inbound, list) and inbound:
                burst = [
                    {"text": str(m.get("text") or ""), "ts_ms": _ms(m.get("ts_ms")), "wamid": m.get("wamid")}
                    for m in inbound
                    if isinstance(m, dict)
                ]
            llm_prefix = _llm_prefix(llm_lines, started)
            cases.append(
                LabCase(
                    case_id=case_id,
                    session_id=sid,
                    episode_id=episode_id,
                    turn=turn,
                    turn_key=str(trace.get("turn_key") or case_id),
                    at_ms=started,
                    trigger=trigger,
                    burst=burst,
                    dashboard_prefix=dashboard_prefix,
                    llm_prefix=llm_prefix,
                    stage_in=trace.get("stage_in"),
                    draft=dict(trace.get("draft") or {}),
                    state=dict(trace.get("state") or {}),
                    episodes_at=_episodes_at(metadata.get("episodes") or [], started),
                    real={k: trace[k] for k in _REAL_FIELDS if k in trace},
                    draft_before=dict((prev_same or {}).get("draft") or {}),
                    state_before=_state_before(prev_any, prev_same),
                    first_in_episode=prev_same is None,
                    recorded_tools=recorded_tool_results(llm_lines, llm_prefix),
                )
            )
    return CaseSet(cases=tuple(cases), exclusions=tuple(exclusions))
