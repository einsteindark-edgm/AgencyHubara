"""Traza por turno del Asesor de Ventas (HU-SC-0) — funciones puras.

Insumo del scorecard por etapa (`sales_eval/scorecard`). El evaluador anterior
solo veía el texto que el cliente leyó y los NOMBRES de las tools; no veía los
rechazos de las guardas, el texto suprimido, los componentes de UI, la etapa ni
la señal del cliente. Con eso no podía distinguir un formulario de envío
mandado sin confirmación de uno legítimo (PR #281, runs 01a0a0eb / 01a0a0f1).

Sin I/O ni reloj: el workflow las usa para armar el payload del turno (R-DET) y
la activity `persist_turn_trace` para enriquecerlo con `metadata.json`.
"""
from __future__ import annotations

import json
import re
from typing import Any

from src.plugins.chats.shared.draft_items import draft_items

_ARG_MAX = 160
_EXCERPT_MAX = 240

# Claves booleanas que, en False, significan "la tool NO hizo su efecto".
_FALSE_MEANS_REJECTED = ("queued", "registered", "escalated", "verified")


def _short(value: Any, limit: int = _ARG_MAX) -> Any:
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 1] + "…"


_CONTROL_KEY_RE = re.compile(
    r'"(error|degraded_from)"\s*:\s*"([^"]*)"'
    r'|"(queued|registered|escalated|verified)"\s*:\s*(true|false)'
    r'|"(count)"\s*:\s*(\d+)'
)


def _parse(result: str | None) -> dict[str, Any] | None:
    """Envelope JSON de la tool. Si vino recortado (el workflow acota el
    resultado), rescata las claves de control con regex: van al inicio."""
    if not isinstance(result, str):
        return None
    try:
        parsed = json.loads(result)
    except (json.JSONDecodeError, ValueError):
        if not result.lstrip().startswith("{"):
            return None
        recovered: dict[str, Any] = {}
        for m in _CONTROL_KEY_RE.finditer(result):
            if m.group(1):
                recovered.setdefault(m.group(1), m.group(2))
            elif m.group(3):
                recovered.setdefault(m.group(3), m.group(4) == "true")
            elif m.group(5):
                recovered.setdefault("count", int(m.group(6)))
        return recovered or None
    return parsed if isinstance(parsed, dict) else None


def summarize_tool_event(
    name: str, args: dict[str, Any] | None, result: str | None
) -> dict[str, Any]:
    """Resumen compacto de una tool ejecutada: ok / rechazo + motivo + notas.

    Reglas (sobre los envelopes reales de las tools de ventas):
      * `error` string presente → rechazo con ese motivo.
      * `queued` / `registered` / `escalated` / `verified` en False → rechazo
        (motivo = `error` si vino, si no `not_<clave>`).
      * Notas que no son rechazo pero importan al evaluador: etiqueta
        degradada por la guarda (`degraded_from`), botones o slots rechazados
        (rechazo parcial), conteo de resultados de una búsqueda.
    """
    payload = _parse(result)
    error: str | None = None
    notes: list[str] = []
    if payload is not None:
        raw_error = payload.get("error")
        if isinstance(raw_error, str) and raw_error:
            error = raw_error
        for key in _FALSE_MEANS_REJECTED:
            if payload.get(key) is False and error is None:
                error = f"not_{key}"
        if isinstance(payload.get("degraded_from"), str):
            notes.append(f"degraded_from:{payload['degraded_from']}")
        rejected_buttons = payload.get("rejected_buttons")
        if isinstance(rejected_buttons, list) and rejected_buttons:
            notes.append(f"rejected_buttons:{len(rejected_buttons)}")
        rejected = payload.get("rejected")
        if isinstance(rejected, list) and rejected:
            # Motivo explícito o, si el rechazo no trae (closed-list simple),
            # el campo: "rejected_slots:None" no le decía nada al scorecard.
            reasons = sorted(
                {
                    str(r.get("reason") or r.get("field"))
                    for r in rejected
                    if isinstance(r, dict)
                }
            )
            notes.append("rejected_slots:" + ",".join(reasons))
            # Nada se guardó: es un fallo, no una nota (2026-09-22: 3
            # rechazos de "Escorpio" quedaron en verde en la traza).
            if payload.get("updated") is False and error is None:
                error = "slots_rejected"
        if isinstance(payload.get("count"), int):
            notes.append(f"count:{payload['count']}")
    compact_args = {
        str(k): _short(v) for k, v in (args or {}).items() if v not in (None, "", [])
    }
    return {
        "name": name,
        "ok": error is None,
        "error": error,
        "notes": notes,
        "args": compact_args,
        "excerpt": _short(result or "", _EXCERPT_MAX),
    }


STAGES: tuple[str, ...] = (
    "descubrimiento",
    "variantes",
    "confirmacion",
    "datos_envio",
    "cierre",
    "postcierre",
)

_VARIANT_SLOTS: tuple[str, ...] = ("aroma", "color", "cantidad")
_SHIPPING_SLOTS: tuple[str, ...] = (
    "ciudad", "direccion", "telefono", "nombre_recibe", "metodo_pago"
)


def draft_slots(episode: dict[str, Any] | None) -> dict[str, Any]:
    draft = (episode or {}).get("order_draft")
    slots = draft.get("slots") if isinstance(draft, dict) else None
    out = dict(slots) if isinstance(slots, dict) else {}
    items = draft_items(draft)
    if len(items) > 1:
        out["items"] = items  # varios productos: cada uno con sus variantes
    return out


def is_confirmed(episode: dict[str, Any] | None) -> bool:
    draft = (episode or {}).get("order_draft")
    return isinstance(draft, dict) and bool(draft.get("confirmed_at_ms"))


def project_stage(episode: dict[str, Any] | None) -> str:
    """Etapa del scorecard para un episodio (abierto o ya cerrado).

    Misma lógica de slots que `resolve_funnel_stage` (el guion que vio el LLM)
    más la etapa implícita `confirmacion`: variantes completas y el cliente
    todavía no dijo que sí ni empezó a dar datos de envío. Es el punto donde
    se rompió el episodio del PR #281, por eso el scorecard la separa. A
    diferencia de `resolve_funnel_stage`, proyecta también episodios cerrados
    (la traza del turno que cerró debe decir en qué etapa quedó).
    """
    if episode is None:
        return "descubrimiento"
    if episode.get("order_id"):
        return "postcierre"
    slots = draft_slots(episode)
    if not slots.get("producto"):
        return "descubrimiento"
    items = draft_items((episode or {}).get("order_draft"))
    if not items or not all(
        all(item.get(k) for k in _VARIANT_SLOTS) for item in items
    ):
        return "variantes"
    shipping_started = any(slots.get(k) for k in _SHIPPING_SLOTS)
    if not is_confirmed(episode) and not shipping_started:
        return "confirmacion"
    if not all(slots.get(k) for k in _SHIPPING_SLOTS):
        return "datos_envio"
    return "cierre"


# v2 (plan del laboratorio §4.1): `steps[]` en orden con su tiempo, `turn_key`,
# `source` y `mode`. Extiende v1 sin quitar campos: el scorecard no cambia.
TRACE_VERSION = 2
TEXT_MAX = 600
STEPS_MAX = 60
_MAX_TOOLS = 24

TRIGGERS: tuple[str, ...] = ("customer", "ghost", "handoff")


def _bound(text: Any, limit: int = TEXT_MAX) -> str:
    value = text if isinstance(text, str) else ""
    return value if len(value) <= limit else value[: limit - 1] + "…"


# Notas que el turno inyectó en `plugin_context` (solo el nombre va a la traza).
_CONTEXT_NOTE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("[CONTEXTO DE TURNO", "burst_note"),
    ("[DATOS DEL PEDIDO", "draft"),
    ("[HANDOFF_REMARKETING]", "handoff"),
)


def context_note_names(plugin_context: list[str] | None) -> list[str]:
    """Nombres de las notas de contexto del turno, en orden (sin su texto)."""
    names: list[str] = []
    for note in plugin_context or []:
        text = note.lstrip() if isinstance(note, str) else ""
        names.append(next((n for prefix, n in _CONTEXT_NOTE_PREFIXES if text.startswith(prefix)), "other"))
    return names


def _bound_value(value: Any) -> Any:
    if isinstance(value, str):
        return _bound(value)
    if isinstance(value, list):
        return [_bound_value(v) for v in value[:_MAX_TOOLS]]
    if isinstance(value, dict):
        return {str(k): _bound_value(v) for k, v in value.items()}
    return value


def _normalize_steps(
    steps: list[dict[str, Any]], *, turn_started_ms: int, tools: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Pasos del turno listos para la traza: numerados, con el tiempo relativo
    al inicio del turno, textos acotados y cada tool con su resultado.

    Los pasos llegan con `at_ms` absoluto (reloj del workflow). Una tool trae
    `event`, el índice en `tool_events`: de ahí sale ok / error / notas /
    extracto con el mismo resumen que el campo v1 `tools`."""
    out: list[dict[str, Any]] = []
    for raw in steps:
        if not isinstance(raw, dict):
            continue
        if len(out) == STEPS_MAX - 1 and len(steps) > STEPS_MAX:
            at = raw.get("at_ms")
            out.append(
                {
                    "i": STEPS_MAX,
                    "at_ms": int(at) - turn_started_ms if isinstance(at, (int, float)) else None,
                    "kind": "truncated",
                    "dropped": len(steps) - (STEPS_MAX - 1),
                }
            )
            break
        step = {k: _bound_value(v) for k, v in raw.items() if k not in ("at_ms", "event") and v is not None}
        at = raw.get("at_ms")
        step = {
            "i": len(out) + 1,
            "at_ms": int(at) - turn_started_ms if isinstance(at, (int, float)) else None,
            **step,
        }
        event = raw.get("event")
        if raw.get("kind") == "tool" and isinstance(event, int) and 0 <= event < len(tools):
            summary = tools[event]
            step.update(
                {
                    "ok": summary["ok"],
                    "error": summary["error"],
                    "notes": summary["notes"],
                    "args": summary["args"],
                    "excerpt": summary["excerpt"],
                }
            )
        out.append(step)
    return out


def build_turn_payload(
    *,
    trigger: str,
    inbound_text: str,
    turn_started_ms: int,
    first_contact: bool,
    tool_events: list[dict[str, Any]],
    discarded_narration: list[str],
    llm_text: str,
    sent_texts: list[str],
    suppressed_reason: str | None,
    guards: list[str],
    steps: list[dict[str, Any]] | None = None,
    turn_key: str | None = None,
    source: str = "prod",
    mode: str = "off",
    context_notes: list[str] | None = None,
) -> dict[str, Any]:
    """Payload del turno armado por el WORKFLOW (solo lo que solo él sabe).

    `tool_events` crudos (`{name, args, result}`) se resumen acá para que la
    activity reciba algo acotado (la history de Temporal guarda el input).

    v2: `steps` (llm, tool, cut, guard, restart, outbound…) en el orden en que
    pasaron, `turn_key` determinista (`run:<run_id>/t:<n>`), `source`
    (`prod` o `lab:<corrida>:<brazo>:<rep>`) y `mode` de las capas nuevas.
    Ninguna clave del payload puede llamarse v, session_id, episode_id, turn
    ni recorded_at_ms: las pone `enrich_turn_trace`.
    """
    tools = [
        summarize_tool_event(
            str(e.get("name") or ""),
            e.get("args") if isinstance(e.get("args"), dict) else None,
            e.get("result") if isinstance(e.get("result"), str) else None,
        )
        for e in tool_events[:_MAX_TOOLS]
    ]
    return {
        "trigger": trigger if trigger in TRIGGERS else "customer",
        "inbound_text": _bound(inbound_text),
        "turn_started_ms": int(turn_started_ms),
        "first_contact": bool(first_contact),
        "tools": tools,
        "discarded_narration": [_bound(t) for t in discarded_narration if t],
        "llm_text": _bound(llm_text),
        "sent_texts": [_bound(t) for t in sent_texts if t],
        "suppressed_reason": suppressed_reason,
        "guards": sorted(set(guards)),
        "turn_key": turn_key,
        "source": source,
        "mode": mode,
        "context_notes": list(context_notes or []),
        "steps": _normalize_steps(steps or [], turn_started_ms=int(turn_started_ms), tools=tools),
    }


def _state_changes(metadata: dict[str, Any], since_ms: int) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for entry in metadata.get("status_history") or []:
        if not isinstance(entry, dict):
            continue
        ts = entry.get("timestamp")
        if not isinstance(ts, (int, float)) or ts * 1000 < since_ms:
            continue
        changes.append(
            {
                "tag": entry.get("tag"),
                "source": entry.get("source") or "llm",
                "reason": entry.get("reason_category"),
            }
        )
    return changes


def _episode_for_turn(episodes: list[Any], turn_started_ms: int) -> dict[str, Any] | None:
    """Episodio al que pertenece el turno: el último que ya había empezado
    cuando el turno arrancó.

    `episodes[-1]` no sirve: si el cliente contesta rápido, el ingest abre el
    episodio siguiente mientras el turno de cierre todavía envía, y la traza del
    cierre caería en el episodio nuevo (el viejo perdería su último turno y el
    scorecard reprobaría en falso). Episodios sin `started_at_ms` (esquema
    viejo) se consideran anteriores.
    """
    candidates = [e for e in episodes if isinstance(e, dict)]
    started = [
        e
        for e in candidates
        if not isinstance(e.get("started_at_ms"), (int, float)) or e["started_at_ms"] <= turn_started_ms
    ]
    if started:
        return started[-1]
    return candidates[-1] if candidates else None


def enrich_turn_trace(
    payload: dict[str, Any],
    metadata: dict[str, Any],
    *,
    previous: dict[str, Any] | None,
    session_id: str,
    recorded_at_ms: int,
) -> dict[str, Any]:
    """Registro completo del turno: payload del workflow + estado persistido.

    `previous` es la última traza escrita de la sesión (encadena número de
    turno y etapa de entrada dentro del MISMO episodio). La señal del cliente
    solo cuenta si llegó después del turno anterior: si no, es de otro turno.
    """
    episode = _episode_for_turn(
        metadata.get("episodes") or [], int(payload.get("turn_started_ms") or recorded_at_ms)
    )
    episode_id = str((episode or {}).get("episode_id") or "")
    same_episode = bool(previous) and previous.get("episode_id") == episode_id
    turn = int(previous.get("turn") or 0) + 1 if same_episode else 1
    stage_in = str(previous.get("stage_out")) if same_episode else "descubrimiento"

    previous_at = int((previous or {}).get("recorded_at_ms") or 0)
    raw_signal = metadata.get("last_inbound_signal")
    signal = None
    if isinstance(raw_signal, dict):
        at_ms = raw_signal.get("at_ms")
        if isinstance(at_ms, (int, float)) and previous_at <= at_ms <= recorded_at_ms:
            signal = {"kind": raw_signal.get("kind"), "text": raw_signal.get("text")}

    since_ms = int(payload.get("turn_started_ms") or recorded_at_ms)
    draft = (episode or {}).get("order_draft")
    return {
        "v": TRACE_VERSION,
        "session_id": session_id,
        "episode_id": episode_id,
        "turn": turn,
        "recorded_at_ms": recorded_at_ms,
        **payload,
        "stage_in": stage_in,
        "stage_out": project_stage(episode),
        "draft": draft_slots(episode),
        "confirmed": is_confirmed(episode),
        "confirmed_by": draft.get("confirmed_by") if isinstance(draft, dict) else None,
        "signal": signal,
        "state": {
            "tag": metadata.get("tag"),
            "route": metadata.get("active_route"),
            "escalation_reason": metadata.get("escalation_reason"),
            "closing_tag": (episode or {}).get("closing_tag"),
            "order_id": (episode or {}).get("order_id"),
            "changes": _state_changes(metadata, since_ms),
        },
    }
