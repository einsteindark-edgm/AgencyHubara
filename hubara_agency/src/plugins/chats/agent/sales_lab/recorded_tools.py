"""Lo que devolvieron en el turno REAL las tools que leen el estado del pedido
en vivo (premortem del laboratorio, C-M2). PURO.

`check_order_status` lee el seguimiento de entrega de la metadata y Medusa en
vivo (etapa y `pay_status`). En el sandbox no hay Medusa y la metadata del
banco es la del final del día: la tool contestaba otro estado que el que vio
producción (o uno del futuro) y el bot simulado se apartaba del real por el
entorno, no por el bot. El caso lleva lo que la tool devolvió en ese turno y
el sandbox se lo da al bot simulado (`sandbox/activities.py`).

La fuente es el historial del LLM del banco: el resultado completo, tal como
lo leyó el LLM de producción (la traza guarda solo un extracto de 240
caracteres, que corta el JSON). El turno real va desde su primer mensaje (el
del cliente, índice `llm_prefix` del caso) hasta el siguiente mensaje de
cliente.
"""
from __future__ import annotations

import json
from typing import Any

#: Tools de SOLO LECTURA cuyo resultado depende del estado en vivo del pedido.
#: Las que escriben (registrar, aplicar cupón) corren siempre en el sandbox.
REPLAYED_TOOLS: frozenset[str] = frozenset({"check_order_status"})


def _args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def recorded_tool_results(
    lines: list[dict[str, Any]], prefix: int, *, names: frozenset[str] = REPLAYED_TOOLS
) -> list[dict[str, Any]]:
    """Las llamadas del turno real a `names`, en orden: `{name, args, content}`.
    `lines` es el historial del LLM del banco (con la línea de metadatos de
    exoclaw); `prefix`, cuántos mensajes había antes del turno."""
    messages = [line for line in lines if isinstance(line, dict) and line.get("_type") != "metadata"]
    window: list[dict[str, Any]] = []
    for i, message in enumerate(messages[max(0, prefix):]):
        if i > 0 and message.get("role") == "user":
            break
        window.append(message)
    calls: dict[str, tuple[str, dict[str, Any]]] = {}
    for message in window:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            function = call.get("function") if isinstance(call.get("function"), dict) else {}
            name = function.get("name") or call.get("name")
            if isinstance(call.get("id"), str) and isinstance(name, str):
                calls[call["id"]] = (name, _args(function.get("arguments", call.get("arguments"))))
    out: list[dict[str, Any]] = []
    for message in window:
        if message.get("role") != "tool" or not isinstance(message.get("content"), str):
            continue
        name, args = calls.get(str(message.get("tool_call_id")), (message.get("name"), {}))
        if name in names:
            out.append({"name": name, "args": args, "content": message["content"]})
    return out
