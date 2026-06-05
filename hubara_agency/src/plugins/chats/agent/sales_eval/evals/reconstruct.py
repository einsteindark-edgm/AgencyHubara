"""Reconstrucción de conversaciones del vault → casos de test de DeepEval.

Fuente de verdad del CONTENIDO de la conversación: el JSONL del history de la
sesión (`<vault>/<session_id>/sessions/<session_id>.jsonl`), escrito por
`platform/session_history/store.py`. Cada línea es un evento:

  * `{"role": "user", "content": ...}`                         — inbound cliente
  * `{"role": "assistant", "content": ..., "tool_calls"?: [...]}` — turno del BOT
  * `{"role": "assistant", "sender": "human", "content": ...}`   — takeover humano

`deepeval` se importa **lazy** (dentro de `build_conversational_test_case`): las
funciones de lectura/normalización/redacción son puras y testeables sin el extra.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.plugins.chats.agent.sales_eval.evals.redaction import redact_turn_content


def whatsapp_number_from_session(session_id: str) -> str:
    """`wa_+573001112233` → `+573001112233`. Tolerante a session sin prefijo."""
    # Import local: constants es spinal file (solo lectura del prefijo).
    from src.platform.constants import WHATSAPP_SESSION_PREFIX

    if session_id.startswith(WHATSAPP_SESSION_PREFIX):
        return session_id[len(WHATSAPP_SESSION_PREFIX):]
    return session_id


def _session_jsonl_path(vault_dir: Path, session_id: str) -> Path:
    return vault_dir / session_id / "sessions" / f"{session_id}.jsonl"


def read_session_events(vault_dir: Path, session_id: str) -> list[dict[str, Any]]:
    """Lee el JSONL crudo de la sesión. `[]` si no existe o está corrupto.

    Tolerante línea-a-línea: una línea malformada se saltea sin tumbar el resto
    (el JSONL puede tener una última línea parcial si hubo un crash al escribir).
    """
    path = _session_jsonl_path(vault_dir, session_id)
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    events.append(obj)
    except OSError:
        return []
    return events


def _tool_name(raw_tc: Any) -> str:
    """Extrae el nombre de una tool call best-effort (varias formas posibles)."""
    if isinstance(raw_tc, str):
        return raw_tc
    if isinstance(raw_tc, dict):
        if raw_tc.get("name"):
            return str(raw_tc["name"])
        fn = raw_tc.get("function")
        if isinstance(fn, dict) and fn.get("name"):
            return str(fn["name"])
    return ""


def to_evaluable_turns(
    events: list[dict[str, Any]],
    *,
    redact: bool = True,
    stop_at_human_takeover: bool = True,
) -> list[dict[str, Any]]:
    """Normaliza eventos crudos → turnos evaluables del BOT.

    Devuelve `[{"role": "user"|"assistant", "content": str, "tools": [str, ...]}]`.

    * Saltea turnos sin `content` (turnos tool-only no se persisten como mensaje,
      pero por las dudas se filtran).
    * `stop_at_human_takeover`: corta en el primer evento `sender == "human"` —
      a partir de ahí responde un humano, no el bot, así que no se juzga al bot
      por eso. (Evalúa el segmento liderado por el bot.)
    * `redact`: aplica `redact_pii` al content de cada turno.
    """
    turns: list[dict[str, Any]] = []
    for ev in events:
        if stop_at_human_takeover and ev.get("sender") == "human":
            break
        role = ev.get("role")
        if role not in ("user", "assistant"):
            continue
        content = ev.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        tools = [
            name
            for name in (_tool_name(tc) for tc in (ev.get("tool_calls") or []))
            if name
        ]
        # FUTURO (paridad de input online vs golden): acá solo extraemos los NOMBRES
        # de las tools, no sus RESULTADOS. El golden sí le pasa los outputs al juez
        # (build_conversational_test_case acepta `tool_outputs`), por eso su
        # `no_hallucination` ve el grounding (ej. el search devolvió $17.000) y el
        # online no -> el online es más estricto en esa métrica (sesga hacia abajo,
        # nunca oculta problemas). Cerrarlo requiere PERSISTIR los `role:tool` en el
        # session_history (write-path de prod) y reconstruirlos como tool_outputs.
        text = redact_turn_content(role, content) if redact else content
        turns.append({"role": role, "content": text, "tools": tools})
    return turns


def build_conversational_test_case(
    turns: list[dict[str, Any]],
    *,
    scenario: str = "Conversación real de ventas por WhatsApp.",
    chatbot_role: str = "Asesor de ventas premium de Hubara (velas artesanales colombianas)",
    context: list[str] | None = None,
    name: str | None = None,
) -> Any:
    """Arma un `ConversationalTestCase` de DeepEval desde turnos normalizados.

    Import lazy de deepeval (solo disponible con el extra `evals`). `tools` de
    cada turno se proyecta a `Turn.tools_called` (clave para evaluar adherencia
    al guion: qué tool se llamó y en qué momento del funnel).
    """
    from deepeval.test_case import ConversationalTestCase, ToolCall, Turn

    dt_turns = []
    for t in turns:
        # `tool_outputs` (opcional) permite que el juez vea QUE devolvio cada tool
        # (grounding para no_hallucination). Backward-compatible: si no viene, el
        # output queda None y el comportamiento es identico al previo (solo nombre).
        _outs = {o["name"]: o.get("output") for o in t.get("tool_outputs", [])} \
            if t.get("tool_outputs") else {}
        tools_called = [ToolCall(name=n, output=_outs.get(n)) for n in t.get("tools", [])] or None
        dt_turns.append(
            Turn(
                role=t["role"],
                content=t["content"],
                tools_called=tools_called if t["role"] == "assistant" else None,
            )
        )
    return ConversationalTestCase(
        turns=dt_turns,
        scenario=scenario,
        chatbot_role=chatbot_role,
        context=context,
        name=name,
    )
