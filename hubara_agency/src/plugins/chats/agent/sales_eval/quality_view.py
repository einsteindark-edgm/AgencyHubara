"""Calidad LLM con la vista del laboratorio, sobre producción (2026-10-02).

El operador reemplaza la vista de Calidad LLM por la del laboratorio: cada
conversación real como un hilo con cada turno del bot calificado, la ventana
del turno (resultado, paso a paso, decisiones de Jev) y el informe de Jev. Lo
que el laboratorio publica de su banco (`run/publish.py`), producción lo arma
del vault con las mismas piezas:

  production_thread   el hilo: mensajes del dashboard + un turno por respuesta
                      al cliente con su ráfaga (`cases.turn_bursts`, la misma
                      regla del laboratorio)
  turn_decisions      las decisiones de Jev de UN turno (`evals/decisions.jsonl`,
                      ubicadas por su mensaje o su hora; sin registro, la
                      salida de Jev que dejó la traza del workflow nuevo)
  conversation_rows   la lista: por conversación, sus episodios con veredicto
                      y bot (`scorecard.bot.episode_bot`)
  jev_report          el informe de Jev de los turnos reales (`arena_metrics`
                      del laboratorio)

PURO salvo la lectura del vault. Lo sirve `chats/api/quality.py`.
"""
from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.plugins.chats.agent.sales.decisions.decision_log import compact_verdict, read_decisions
from src.plugins.chats.agent.sales_eval.scorecard.bot import episode_bot
from src.plugins.chats.shared import turn_traces
from src.plugins.chats.shared.turn_view import turn_key_of

#: Los campos de un mensaje del dashboard que el hilo muestra (los mismos que
#: publica el laboratorio).
MESSAGE_FIELDS = ("role", "content", "timestamp", "sender", "kind", "component_kind", "wamid")
_CUSTOMER_TRIGGERS = frozenset({"customer", "handoff"})
_STAGE_ORDER = {"ingest": 0, "turno": 1, "complemento": 2}


def thread_message(event: Mapping[str, Any]) -> dict[str, Any]:
    msg = {k: event[k] for k in MESSAGE_FIELDS if k in event}
    if event.get("image_url"):
        msg["has_image"] = True  # la foto vive en el media store
    return msg


def _jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in raw.splitlines():
        try:
            item = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def _metadata(vault_dir: Path, session_id: str) -> dict[str, Any]:
    try:
        data = json.loads((Path(vault_dir) / session_id / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _num(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _ordered(traces: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        (dict(t) for t in traces if isinstance(t, Mapping)),
        key=lambda t: (_num(t.get("turn_started_ms")), _num(t.get("turn"))),
    )


def _is_customer(trace: Mapping[str, Any]) -> bool:
    return str(trace.get("trigger") or "customer") in _CUSTOMER_TRIGGERS


# ── el hilo ────────────────────────────────────────────────────────────────


def production_thread(vault_dir: Path, session_id: str) -> dict[str, Any]:
    """El hilo de la conversación con la forma del que publica el laboratorio
    (sin las salidas simuladas: aquí solo existe la respuesta real)."""
    from src.plugins.chats.agent.sales_lab.cases import turn_bursts

    events = _jsonl(Path(vault_dir) / session_id / "sessions" / f"{session_id}.jsonl")
    traces = turn_traces.read_traces(vault_dir, session_id)
    turns = [
        {
            "turn_key": turn_key_of(tb.trace, session_id),
            "episode_id": tb.trace.get("episode_id"),
            "turn": tb.trace.get("turn"),
            "at_ms": tb.started,
            "burst": tb.burst,
            "dashboard_prefix": tb.dashboard_prefix,
        }
        for tb in turn_bursts(events, traces)
        if tb.is_case
    ]
    return {
        "session_id": session_id,
        "episodes": _metadata(vault_dir, session_id).get("episodes") or [],
        "messages": [thread_message(e) for e in events],
        "turns": turns,
    }


def _event_ms(event: Mapping[str, Any]) -> int | None:
    value = event.get("timestamp")
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def episode_window(episodes: Iterable[Any], episode_id: str) -> tuple[int, int | None] | None:
    """Desde 5 s antes de que el episodio empezó hasta que empezó el siguiente."""
    ordered = sorted((e for e in episodes if isinstance(e, dict)), key=lambda e: e.get("started_at_ms") or 0)
    for i, ep in enumerate(ordered):
        if ep.get("episode_id") == episode_id:
            start = int(ep.get("started_at_ms") or 0) - 5_000
            nxt = ordered[i + 1].get("started_at_ms") if i + 1 < len(ordered) else None
            return start, int(nxt) if isinstance(nxt, (int, float)) else None
    return None


def episode_slice(thread: Mapping[str, Any], episode_id: str) -> dict[str, Any] | None:
    """El hilo de UN episodio (sus mensajes y sus turnos), o None si no existe."""
    window = episode_window(thread.get("episodes") or [], episode_id)
    if window is None:
        return None
    start, end = window
    messages = [
        m for m in thread.get("messages") or []
        if (ts := _event_ms(m)) is not None and ts >= start and (end is None or ts < end)
    ]
    turns = [t for t in thread.get("turns") or [] if t.get("episode_id") == episode_id]
    return {**thread, "episode_id": episode_id, "messages": messages, "turns": turns}


# ── las decisiones de Jev de cada turno ─────────────────────────────────────


def read_session_decisions(vault_dir: Path, session_id: str) -> list[dict[str, Any]]:
    return read_decisions(vault_dir, session_id)


def _owner(decision: Mapping[str, Any], ordered: list[dict[str, Any]]) -> tuple[dict[str, Any], str, int | None] | None:
    """(turno del cliente, etapa, número del mensaje) de una decisión."""
    stage, at = str(decision.get("stage") or "turno"), _num(decision.get("at_ms"))
    customer = [t for t in ordered if _is_customer(t)]
    if stage == "ingest":
        message_id = decision.get("message_id")
        if message_id:
            for trace in customer:
                wamids = [m.get("wamid") for m in trace.get("inbound") or [] if isinstance(m, dict)]
                if message_id in wamids:
                    return trace, "ingest", wamids.index(message_id) + 1
        nxt = next((t for t in customer if _num(t.get("turn_started_ms")) >= at), None)
        return (nxt, "ingest", None) if nxt is not None else None
    if stage != "turno":
        return None  # remarketing, cierre: fuera de un turno de ventas
    before = [t for t in ordered if _num(t.get("turn_started_ms")) <= at]
    if not before:
        return None
    current = before[-1]
    if _is_customer(current):
        return current, "turno", None
    if str(current.get("trigger")) == "complement":
        start = _num(current.get("turn_started_ms"))
        owner = next((t for t in reversed(customer) if _num(t.get("turn_started_ms")) <= start), None)
        return (owner, "complemento", None) if owner is not None else None
    return None


def _shown(decision: Mapping[str, Any], stage: str, message: int | None) -> dict[str, Any]:
    row = {k: v for k, v in decision.items() if k not in ("at_ms", "message_id", "stage")}
    head: dict[str, Any] = {"stage": stage}
    if message is not None:
        head["message"] = message
    return {**head, **row}


def turn_decisions(
    traces: Iterable[Mapping[str, Any]], decisions: Iterable[Mapping[str, Any]], turn_key: str, *, session_id: str
) -> list[dict[str, Any]]:
    """Las decisiones de Jev del turno `turn_key`, como las publica el
    laboratorio (`stage`, `message` y el veredicto compacto): primero las del
    ingest (por su mensaje), después las del turno y las del complemento.
    Sin registro propio (trazas de antes), la salida de Jev que dejó la traza
    del workflow nuevo (`egress`)."""
    ordered = _ordered(traces)
    target = next((t for t in ordered if turn_key_of(t, session_id) == turn_key), None)
    if target is None:
        return []
    shown: list[tuple[int, int, int, dict[str, Any]]] = []
    for decision in decisions:
        owner = _owner(decision, ordered)
        if owner is None or owner[0] is not target:
            continue
        _, stage, message = owner
        shown.append((_STAGE_ORDER.get(stage, 9), message or 0, _num(decision.get("at_ms")), _shown(decision, stage, message)))
    if not any(row["stage"] != "ingest" for *_, row in shown):
        shown.extend((1, 0, i, {"stage": "turno", **compact_verdict(v)}) for i, v in enumerate(_egress(target)))
        complement = _complement_of(target, ordered)
        if complement is not None:
            shown.extend((2, 0, i, {"stage": "complemento", **compact_verdict(v)}) for i, v in enumerate(_egress(complement)))
    return [row for *_, row in sorted(shown, key=lambda item: item[:3])]


def _egress(trace: Mapping[str, Any]) -> list[dict[str, Any]]:
    egress = trace.get("egress")
    verdicts = egress.get("verdicts") if isinstance(egress, Mapping) else None
    return [v for v in verdicts or [] if isinstance(v, dict)]


def _complement_of(target: Mapping[str, Any], ordered: list[dict[str, Any]]) -> dict[str, Any] | None:
    """La traza del complemento del turno (la que sigue, antes del próximo turno del cliente)."""
    after = ordered[ordered.index(target) + 1:] if target in ordered else []
    for trace in after:
        if _is_customer(trace):
            return None
        if str(trace.get("trigger")) == "complement":
            return trace
    return None


# ── la lista de conversaciones ──────────────────────────────────────────────


def conversation_rows(
    records: Iterable[Mapping[str, Any]], traces_by_session: Mapping[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    """Una fila por conversación con sus episodios calificados: veredicto y
    bot de cada uno, turnos del cliente y cuándo fue lo último (para ordenar,
    la más reciente primero)."""
    verdicts: dict[str, dict[str, str]] = defaultdict(dict)
    for record in records:
        sid, ep = str(record.get("session_id") or ""), str(record.get("episode_id") or "")
        if sid and ep:
            verdicts[sid][ep] = str(record.get("verdict") or "SIN_DATOS")
    rows: list[dict[str, Any]] = []
    for sid, by_episode in verdicts.items():
        episodes = sorted(by_episode)
        traces = [t for t in traces_by_session.get(sid) or [] if isinstance(t, dict) and t.get("episode_id") in by_episode]
        rows.append(
            {
                "session_id": sid,
                "episodes": episodes,
                "turns": sum(1 for t in traces if _is_customer(t)),
                "last_at_ms": max((_num(t.get("turn_started_ms")) for t in traces), default=0),
                "verdicts": {ep: by_episode[ep] for ep in episodes},
                "bots": {ep: episode_bot(t for t in traces if t.get("episode_id") == ep) for ep in episodes},
            }
        )
    return sorted(rows, key=lambda r: (r["last_at_ms"], r["session_id"]), reverse=True)


# ── el informe de Jev ───────────────────────────────────────────────────────


def _llm_cost(metadata: Mapping[str, Any], episode_id: str) -> float:
    for ep in metadata.get("episodes") or []:
        if isinstance(ep, dict) and ep.get("episode_id") == episode_id:
            usage = ep.get("llm_usage")
            cost = usage.get("cost_usd") if isinstance(usage, dict) else None
            return float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else 0.0
    return 0.0


def jev_report(vault_dir: Path, units: Iterable[tuple[str, str]]) -> dict[str, Any]:
    """El informe de Jev del laboratorio (`arena_metrics`) sobre los turnos
    reales de los episodios: la percepción y la verificación de cada turno,
    las rondas extra, las decisiones de capacidades (quién decidió y cuántas
    cayeron a la regla porque Jev falló) y el costo por turno (el LLM del
    episodio repartido entre sus turnos, más lo que cobró Jev)."""
    from src.plugins.chats.agent.sales_lab.run.arena import arena_metrics

    results: list[dict[str, Any]] = []
    episodes = 0
    by_session: dict[str, list[str]] = defaultdict(list)
    for sid, ep in units:
        by_session[sid].append(ep)
    for sid, eps in sorted(by_session.items()):
        all_traces = _ordered(turn_traces.read_traces(vault_dir, sid))
        decisions = read_decisions(vault_dir, sid)
        metadata = _metadata(vault_dir, sid)
        for ep in sorted(set(eps)):
            ordered = [t for t in all_traces if t.get("episode_id") == ep]
            customer = [t for t in ordered if _is_customer(t)]
            if not customer:
                continue
            episodes += 1
            llm_per_turn = _llm_cost(metadata, ep) / len(customer)
            for trace in customer:
                key = turn_key_of(trace, sid)
                results.append(
                    {
                        "trace": trace,
                        "complement_trace": _complement_of(trace, ordered),
                        "decisions": turn_decisions(all_traces, decisions, key, session_id=sid),
                        "llm_cost_usd": llm_per_turn,
                    }
                )
    return {"episodes": episodes, **arena_metrics(results)} if results else {"episodes": 0, "turns": 0}
