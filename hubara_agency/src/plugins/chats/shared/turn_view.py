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
    ventana); lo del bot, al último turno que arrancó antes. El operador
    humano y los eventos de sistema no son de ningún turno del bot.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

#: Un mensaje del cliente espera la ráfaga (debounce) y, a lo sumo, un
#: worker reiniciado por un deploy; más allá no se adivina el turno.
_WINDOW_MS = 30 * 60_000
#: El timestamp del mensaje y el inicio del turno salen de relojes distintos.
_SLACK_MS = 5_000
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


def annotate_turn_keys(messages: list[dict[str, Any]], traces: list[dict[str, Any]], session_id: str) -> None:
    ordered = sorted(
        (t for t in traces if isinstance(t, dict) and isinstance(t.get("turn_started_ms"), (int, float))),
        key=lambda t: int(t["turn_started_ms"]),
    )
    if not ordered:
        return
    by_wamid = {
        m.get("wamid"): turn_key_of(t, session_id)
        for t in ordered
        for m in t.get("inbound") or []
        if isinstance(m, dict) and m.get("wamid")
    }
    for msg in messages:
        ui_type = msg.get("ui_type")
        at = _ms(msg.get("timestamp"))
        key: str | None = None
        if ui_type == "user_message":
            key = by_wamid.get(msg.get("wamid"))
            if key is None and at is not None:
                after = next((t for t in ordered if int(t["turn_started_ms"]) >= at - _SLACK_MS), None)
                if after is not None and int(after["turn_started_ms"]) - at <= _WINDOW_MS:
                    key = turn_key_of(after, session_id)
        elif ui_type in _BOT_TYPES and at is not None:
            before = [t for t in ordered if int(t["turn_started_ms"]) <= at + _SLACK_MS]
            if before and at - int(before[-1]["turn_started_ms"]) <= _WINDOW_MS:
                key = turn_key_of(before[-1], session_id)
        if key is not None:
            msg["turn_key"] = key
