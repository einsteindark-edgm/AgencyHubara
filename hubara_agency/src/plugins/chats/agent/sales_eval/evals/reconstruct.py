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


# --------------------------------------------------------------------------- #
# Episodios — la UNIDAD de evaluación (no la sesión entera).
#
# `metadata["episodes"]` (ver chats/agent/sales/use_cases/episode_lifecycle.py)
# delimita cada tramo de intención del cliente. Evaluar la sesión completa
# mezcla episodios (una compra de hace 2 meses + la cotización de hoy) y hace
# imposible saber QUÉ conversación generó el mal score. Acá se rebana el JSONL
# por episodio:
#   * exacto, por `msgs_count_at_start/close` (snapshot de líneas — FU3);
#   * fallback por timestamps ISO de los eventos vs started/closed_at_ms;
#   * sesión legacy sin episodes[] → se evalúa entera (episode_id = "").
# --------------------------------------------------------------------------- #

EPISODE_SEP = "::"
"""Separador del unit id `<session_id>::<episode_id>` que viaja por el
workflow de eval. Va en strings (no DTO nuevo) para no cambiar la firma de
las activities ya registradas — los runs vivos replayean sin patch (L-9)."""


def make_eval_unit_id(session_id: str, episode_id: str = "") -> str:
    return f"{session_id}{EPISODE_SEP}{episode_id}" if episode_id else session_id


def parse_eval_unit_id(unit_id: str) -> tuple[str, str]:
    """`wa_x::ep_002` → (wa_x, ep_002); `wa_x` (legacy/sesión entera) → (wa_x, "")."""
    session_id, _, episode_id = unit_id.partition(EPISODE_SEP)
    return session_id, episode_id


def read_session_metadata(vault_dir: Path, session_id: str) -> dict[str, Any]:
    """Lee `<vault>/<session>/metadata.json`. `{}` si no existe o está corrupto."""
    path = vault_dir / session_id / "metadata.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def find_episode(metadata: dict[str, Any], episode_id: str) -> dict[str, Any] | None:
    for ep in metadata.get("episodes") or []:
        if isinstance(ep, dict) and ep.get("episode_id") == episode_id:
            return ep
    return None


def _event_ts_ms(event: dict[str, Any]) -> int | None:
    """Timestamp ISO del evento → epoch ms. None si falta o no parsea."""
    raw = event.get("timestamp")
    if not isinstance(raw, str) or not raw:
        return None
    from datetime import datetime

    try:
        return int(datetime.fromisoformat(raw).timestamp() * 1000)
    except ValueError:
        return None


def slice_episode_events(
    events: list[dict[str, Any]], episode: dict[str, Any]
) -> list[dict[str, Any]]:
    """Recorta los eventos de la sesión al tramo del episodio.

    Preferencia: `msgs_count_at_start/close` (índices exactos de línea,
    snapshoteados por el lifecycle al abrir/cerrar). Fallback: timestamps de
    los eventos dentro de `[started_at_ms, closed_at_ms)` — eventos sin
    timestamp parseable se omiten en ese modo (no hay forma de ubicarlos).
    """
    start_count = episode.get("msgs_count_at_start")
    close_count = episode.get("msgs_count_at_close")
    if isinstance(start_count, int) and start_count >= 0:
        end = close_count if isinstance(close_count, int) and close_count >= start_count else len(events)
        return events[start_count:end]

    started_ms = episode.get("started_at_ms")
    if not isinstance(started_ms, int):
        return events  # episodio sin límites usables → mejor la sesión entera
    closed_ms = episode.get("closed_at_ms")
    upper = closed_ms if isinstance(closed_ms, int) else None
    out: list[dict[str, Any]] = []
    for ev in events:
        ts = _event_ts_ms(ev)
        if ts is None:
            continue
        if ts < started_ms:
            continue
        if upper is not None and ts >= upper:
            continue
        out.append(ev)
    return out


def read_episode_events(
    vault_dir: Path, session_id: str, episode_id: str
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Eventos del episodio + el dict del episodio (None si no existe en metadata).

    `episode_id` vacío → sesión entera (legacy). Episodio no encontrado →
    sesión entera también (defensivo: mejor evaluar de más que devolver []).
    """
    events = read_session_events(vault_dir, session_id)
    if not episode_id:
        return events, None
    episode = find_episode(read_session_metadata(vault_dir, session_id), episode_id)
    if episode is None:
        return events, None
    return slice_episode_events(events, episode), episode


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
