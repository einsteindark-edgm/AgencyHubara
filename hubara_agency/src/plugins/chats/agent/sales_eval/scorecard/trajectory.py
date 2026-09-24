"""Trayectoria de un episodio (HU-SC-1) — la estructura única del scorecard.

Los checks evalúan una `Trajectory` y la tira del dashboard dibuja la MISMA
estructura: lo que se ve es lo que se evaluó. Dos fuentes:

  * **trace** — trazas por turno de `chats/shared/turn_traces` (HU-SC-0):
    tools con su resultado, texto enviado y suprimido, guardas, etapa, señal
    del cliente y estado. Fidelidad completa.
  * **legacy** — episodios anteriores a la traza: se agrupan los eventos del
    JSONL del dashboard en turnos. Sin resultado de tools, sin etapa ni
    guardas: los checks que dependen de eso devuelven `desconocido`, nunca
    `pasa`.

Funciones puras (sin I/O): el caller lee el vault.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from src.plugins.chats.shared.purchase_signals import (
    detect_deferral,
    detect_purchase_affirmation,
)

# Tool que hizo su efecto → componente de UI que recibió el cliente.
INTENT_BY_TOOL: dict[str, str] = {
    "present_products": "products_list",
    "present_product_detail": "product_detail",
    "present_product_gallery": "product_gallery",
    "present_variant_picker": "variant_picker",
    "send_quick_replies": "quick_replies",
    "request_shipping_details": "shipping_flow",
    "present_order_confirmation": "order_confirmation",
    "send_shipping_rates": "shipping_rates",
    "send_cta_url": "cta_url",
    "send_contact_card": "contact_card",
    "react_to_message": "reaction",
}

CATALOG_DISPLAY_INTENTS = frozenset(
    {"products_list", "product_detail", "product_gallery", "variant_picker"}
)

_CONFIRM_BUTTON_MARKERS = ("[el cliente tocó el botón: ✅ confirmar", "[el cliente tocó el botón: confirmar")


@dataclass(frozen=True)
class ToolCall:
    name: str
    ok: bool | None
    error: str | None = None
    notes: tuple[str, ...] = ()
    args: dict[str, Any] = field(default_factory=dict)

    def note(self, prefix: str) -> str | None:
        for n in self.notes:
            if n.startswith(prefix):
                return n[len(prefix):].lstrip(":")
        return None


@dataclass(frozen=True)
class InboundMsg:
    """Un mensaje de la ráfaga del turno (traza v2, `inbound[]`)."""

    seq: int | None
    ts_ms: int | None
    kind: str
    text: str


@dataclass(frozen=True)
class Turn:
    turn: int
    at_ms: int | None
    trigger: str
    inbound_text: str
    signal: str | None
    sent_texts: tuple[str, ...]
    llm_text: str
    suppressed_reason: str | None
    discarded_narration: tuple[str, ...]
    tools: tuple[ToolCall, ...]
    intents: tuple[str, ...]
    guards: tuple[str, ...]
    stage_in: str | None
    stage_out: str | None
    draft: dict[str, Any] | None
    confirmed: bool | None
    state: dict[str, Any]
    first_contact: bool | None
    # Los mensajes de la ráfaga con su hora (traza v2). Vacío en trazas v1 y
    # en episodios legados: ahí solo está `inbound_text`, unido por saltos de línea.
    inbound: tuple[InboundMsg, ...] = ()

    # ── helpers de lectura para los checks ────────────────────────────────
    def tool(self, name: str) -> ToolCall | None:
        return next((t for t in self.tools if t.name == name), None)

    def tools_named(self, name: str) -> list[ToolCall]:
        return [t for t in self.tools if t.name == name]

    def tool_ok(self, name: str) -> bool:
        return any(t.name == name and t.ok is True for t in self.tools)

    def tool_attempted(self, name: str) -> bool:
        return any(t.name == name for t in self.tools)

    @property
    def is_customer(self) -> bool:
        return self.trigger == "customer"

    @property
    def is_ghost(self) -> bool:
        return self.trigger == "ghost"

    @property
    def confirm_button(self) -> bool:
        low = self.inbound_text.lower()
        return any(low.startswith(m) for m in _CONFIRM_BUTTON_MARKERS)

    @property
    def state_changes(self) -> list[dict[str, Any]]:
        changes = self.state.get("changes") if isinstance(self.state, dict) else None
        return [c for c in changes or [] if isinstance(c, dict)]


@dataclass(frozen=True)
class Trajectory:
    session_id: str
    episode_id: str
    fidelity: str  # trace | legacy | empty
    turns: tuple[Turn, ...]
    closing_tag: str | None = None
    order_id: str | None = None
    closing_motivo: str | None = None
    started_at_ms: int | None = None
    closed_at_ms: int | None = None

    @property
    def first_contact(self) -> bool | None:
        return self.turns[0].first_contact if self.turns else None

    @property
    def last(self) -> Turn | None:
        return self.turns[-1] if self.turns else None

    @property
    def stage_final(self) -> str | None:
        return self.turns[-1].stage_out if self.turns else None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _as_int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def _tool_from_trace(raw: dict[str, Any]) -> ToolCall:
    ok = raw.get("ok")
    return ToolCall(
        name=str(raw.get("name") or ""),
        ok=ok if isinstance(ok, bool) else None,
        error=raw.get("error") if isinstance(raw.get("error"), str) else None,
        notes=tuple(str(n) for n in raw.get("notes") or []),
        args=dict(raw.get("args") or {}) if isinstance(raw.get("args"), dict) else {},
    )


def _inbound_from_trace(raw: Any) -> tuple[InboundMsg, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(
        InboundMsg(
            seq=_as_int(m.get("seq")),
            ts_ms=_as_int(m.get("ts_ms")),
            kind=str(m.get("kind") or "text"),
            text=str(m.get("text") or ""),
        )
        for m in raw
        if isinstance(m, dict)
    )


def _intents_for(tools: tuple[ToolCall, ...], guards: tuple[str, ...]) -> tuple[str, ...]:
    intents: list[str] = []
    for t in tools:
        kind = INTENT_BY_TOOL.get(t.name)
        if kind and t.ok is True and kind not in intents:
            intents.append(kind)
    if "variant_enumeration_guard" in guards and "variant_picker" not in intents:
        intents.append("variant_picker")
    return tuple(intents)


def _episode_fields(episode: dict[str, Any]) -> dict[str, Any]:
    return {
        "closing_tag": episode.get("closing_tag"),
        "order_id": episode.get("order_id"),
        "closing_motivo": episode.get("closing_motivo"),
        "started_at_ms": _as_int(episode.get("started_at_ms")),
        "closed_at_ms": _as_int(episode.get("closed_at_ms")),
    }


def build_trajectory(
    traces: list[dict[str, Any]], *, session_id: str, episode: dict[str, Any]
) -> Trajectory:
    """Trayectoria desde las trazas del episodio (fidelidad completa)."""
    episode_id = str(episode.get("episode_id") or "")
    ordered = sorted(
        (t for t in traces if isinstance(t, dict)),
        key=lambda t: (int(t.get("turn") or 0), int(t.get("recorded_at_ms") or 0)),
    )
    turns: list[Turn] = []
    for raw in ordered:
        tools = tuple(_tool_from_trace(x) for x in raw.get("tools") or [] if isinstance(x, dict))
        guards = tuple(str(g) for g in raw.get("guards") or [])
        signal = raw.get("signal")
        turns.append(
            Turn(
                turn=int(raw.get("turn") or len(turns) + 1),
                at_ms=_as_int(raw.get("turn_started_ms")) or _as_int(raw.get("recorded_at_ms")),
                trigger=str(raw.get("trigger") or "customer"),
                inbound_text=str(raw.get("inbound_text") or ""),
                signal=signal.get("kind") if isinstance(signal, dict) else None,
                sent_texts=tuple(str(x) for x in raw.get("sent_texts") or []),
                llm_text=str(raw.get("llm_text") or ""),
                suppressed_reason=raw.get("suppressed_reason"),
                discarded_narration=tuple(str(x) for x in raw.get("discarded_narration") or []),
                tools=tools,
                intents=_intents_for(tools, guards),
                guards=guards,
                stage_in=raw.get("stage_in"),
                stage_out=raw.get("stage_out"),
                draft=dict(raw.get("draft") or {}),
                confirmed=bool(raw.get("confirmed")),
                state=dict(raw.get("state") or {}),
                first_contact=bool(raw.get("first_contact")),
                inbound=_inbound_from_trace(raw.get("inbound")),
            )
        )
    return Trajectory(
        session_id=session_id,
        episode_id=episode_id,
        fidelity="trace" if turns else "empty",
        turns=tuple(turns),
        **_episode_fields(episode),
    )


def detect_signal(text: str) -> str | None:
    """Señal del cliente desde su texto (sin ingest): botón Confirmar,
    aplazamiento o afirmación de compra. Reconstrucción legada y goldens."""
    return _legacy_signal(text)


def _legacy_signal(text: str) -> str | None:
    low = text.lower()
    if any(low.startswith(m) for m in _CONFIRM_BUTTON_MARKERS):
        return "affirmation"
    if detect_deferral(text):
        return "deferral"
    if detect_purchase_affirmation(text):
        return "affirmation"
    return None


# Un mensaje del bot que llega más de esto después del último mensaje del
# cliente no es la respuesta del asesor de ventas: es remarketing ("Hola de
# nuevo…"), una notificación de envío o un recordatorio. El JSONL del dashboard
# no trae quién lo escribió. El asesor responde en segundos o pocos minutos
# (debounce + LLM + reintentos del envío).
PROACTIVE_GAP_MS = 30 * 60 * 1000


def _iso_ms(value: Any) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    from datetime import datetime

    try:
        return int(datetime.fromisoformat(value).timestamp() * 1000)
    except ValueError:
        return None


def build_legacy_trajectory(
    events: list[dict[str, Any]], *, session_id: str, episode: dict[str, Any]
) -> Trajectory:
    """Trayectoria aproximada desde el JSONL del dashboard (sin trazas).

    Un turno arranca con cada mensaje del cliente (los consecutivos se
    agrupan); los eventos del bot que siguen le pertenecen, salvo los que
    llegan más de `PROACTIVE_GAP_MS` después del último mensaje del cliente
    (mensajes proactivos de otros agentes). Corta en el primer mensaje del
    humano operador (desde ahí no evaluamos al bot).
    """
    groups: list[dict[str, Any]] = []
    last_inbound_ms: int | None = None
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if ev.get("sender") == "human":
            break
        role = ev.get("role")
        content = str(ev.get("content") or "")
        if role == "user":
            last_inbound_ms = _iso_ms(ev.get("timestamp")) or last_inbound_ms
            if groups and not groups[-1]["bot"]:
                groups[-1]["inbound"].append(content)
            else:
                groups.append({"inbound": [content], "bot": [], "intents": [], "tools": []})
            continue
        if role != "assistant" or not groups:
            continue
        at_ms = _iso_ms(ev.get("timestamp"))
        if at_ms is not None and last_inbound_ms is not None and at_ms - last_inbound_ms > PROACTIVE_GAP_MS:
            continue
        g = groups[-1]
        g["bot"].append(ev)
        if ev.get("kind") == "ui_component":
            kind = ev.get("component_kind")
            if isinstance(kind, str) and kind not in g["intents"]:
                g["intents"].append(kind)
        elif content:
            g.setdefault("texts", []).append(content)
        for name in ev.get("tools_used") or []:
            g["tools"].append(str(name))

    turns: list[Turn] = []
    for i, g in enumerate(groups, 1):
        inbound = "\n".join(g["inbound"])
        turns.append(
            Turn(
                turn=i,
                at_ms=None,
                trigger="customer",
                inbound_text=inbound,
                signal=_legacy_signal(g["inbound"][-1] if g["inbound"] else ""),
                sent_texts=tuple(g.get("texts", [])),
                llm_text="",
                suppressed_reason=None,
                discarded_narration=(),
                tools=tuple(ToolCall(name=n, ok=None) for n in g["tools"]),
                intents=tuple(g["intents"]),
                guards=(),
                stage_in=None,
                stage_out=None,
                draft=None,
                confirmed=None,
                state={},
                first_contact=None,
            )
        )
    return Trajectory(
        session_id=session_id,
        episode_id=str(episode.get("episode_id") or ""),
        fidelity="legacy" if turns else "empty",
        turns=tuple(turns),
        **_episode_fields(episode),
    )
