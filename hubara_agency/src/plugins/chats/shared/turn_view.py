"""Hilo de un turno para el dashboard (plan del laboratorio §4.1 y PR 17). PURO.

  * `trace_view`: la traza del turno lista para el diagrama de secuencia.
    Traza v2: sus pasos en orden, con la ráfaga como primer paso. Traza v1:
    los pasos que se saben (ráfaga, tools, guardas, envío), sin tiempos.
    La usan el Laboratorio (`lab@v1`) y Chats.
  * `turn_key_of`: el id del turno (`turn_key` de la traza v2, o uno
    sintetizado `<sesión>/<episodio>/t<n>` para la v1).
  * `annotate_turn_keys`: a cada burbuja del chat, el turno que la produjo.
    El mensaje del cliente va al turno que lo procesó (por su wamid si la
    traza lo trae; si no, el primer turno que arrancó después, dentro de la
    ventana); lo del bot, al último turno que arrancó antes Y que todavía no
    había terminado (`recorded_at_ms` de la traza): una plantilla de ETA, de
    remarketing o de una campaña que llega después no es de ese turno. El
    operador humano, el eco de otro agente (`sender`) y los eventos de
    sistema no son de ningún turno del bot. Una traza rota se salta.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import datetime, timezone
from typing import Any

#: Un mensaje del cliente espera la ráfaga (debounce) y, a lo sumo, un
#: worker reiniciado por un deploy; más allá no se adivina el turno.
_WINDOW_MS = 30 * 60_000
#: El timestamp del mensaje y el inicio del turno salen de relojes distintos.
_SLACK_MS = 5_000
#: Lo que el turno manda después de escribir su traza (cierre, escalación).
_AFTER_TRACE_MS = 60_000
#: Sin `recorded_at_ms` (traza vieja), un turno nunca dura más que esto.
_TURN_MAX_MS = 10 * 60_000
_BOT_TYPES = frozenset({"agent_message", "agent_tool_call", "tool_execution_result", "ui_component_sent"})


def turn_key_of(trace: dict[str, Any], session_id: str) -> str:
    key = trace.get("turn_key")
    if isinstance(key, str) and key:
        return key
    return f"{session_id}/{trace.get('episode_id')}/t{trace.get('turn')}"


def _steps_v1(trace: dict[str, Any]) -> list[dict[str, Any]]:
    text = str(trace.get("inbound_text") or "")
    steps: list[dict[str, Any]] = [
        {"kind": "inbound", "messages": [{"text": line} for line in text.splitlines() if line.strip()] or [{"text": text}]}
    ]
    for tool in trace.get("tools") or []:
        if isinstance(tool, dict):
            steps.append({"kind": "tool", **{k: tool.get(k) for k in ("name", "ok", "error", "args", "notes", "excerpt")}})
    for guard in trace.get("guards") or []:
        steps.append({"kind": "guard", "name": guard})
    sent = [t for t in trace.get("sent_texts") or [] if t]
    if sent:
        steps.append({"kind": "outbound", "bubbles": [{"kind": "text", "text": t, "delivered": None} for t in sent]})
    return [{"i": i, "at_ms": None, **step} for i, step in enumerate(steps, 1)]


def trace_view(trace: dict[str, Any]) -> dict[str, Any]:
    steps = trace.get("steps")
    if isinstance(steps, list) and steps:
        inbound = trace.get("inbound") or [
            {"text": line} for line in str(trace.get("inbound_text") or "").splitlines()
        ]
        return {"fidelity": "v2", "trace": trace, "steps": [{"i": 0, "at_ms": 0, "kind": "inbound", "messages": inbound}, *steps]}
    return {"fidelity": "v1", "trace": trace, "steps": _steps_v1(trace)}


def _ms(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value if value > 1e12 else value * 1000)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    return None


def _number(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _turn_end(trace: dict[str, Any], started: int) -> int:
    recorded = _number(trace.get("recorded_at_ms"))
    return recorded + _AFTER_TRACE_MS if recorded is not None else started + _TURN_MAX_MS


def annotate_turn_keys(messages: list[dict[str, Any]], traces: list[dict[str, Any]], session_id: str) -> None:
    ordered = sorted(
        (t for t in traces if isinstance(t, dict) and _number(t.get("turn_started_ms")) is not None),
        key=lambda t: int(t["turn_started_ms"]),
    )
    if not ordered:
        return
    starts = [int(t["turn_started_ms"]) for t in ordered]
    by_wamid: dict[str, str] = {}
    for trace in ordered:
        inbound = trace.get("inbound")
        for m in inbound if isinstance(inbound, list) else []:
            if isinstance(m, dict) and isinstance(m.get("wamid"), str) and m["wamid"]:
                by_wamid[m["wamid"]] = turn_key_of(trace, session_id)
    for msg in messages:
        ui_type = msg.get("ui_type")
        at = _ms(msg.get("timestamp"))
        key: str | None = None
        if ui_type == "user_message":
            wamid = msg.get("wamid")
            key = by_wamid.get(wamid) if isinstance(wamid, str) else None
            if key is None and at is not None:
                i = bisect_left(starts, at - _SLACK_MS)
                if i < len(ordered) and starts[i] - at <= _WINDOW_MS:
                    key = turn_key_of(ordered[i], session_id)
        elif ui_type in _BOT_TYPES and at is not None and not msg.get("sender"):
            i = bisect_right(starts, at + _SLACK_MS) - 1
            if i >= 0 and at <= _turn_end(ordered[i], starts[i]):
                key = turn_key_of(ordered[i], session_id)
        if key is not None:
            msg["turn_key"] = key
