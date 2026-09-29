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
  * cada mensaje de la ráfaga lleva lo que el webhook traía además del texto
    efectivo (`ingest_fields`): el tipo (`kind`, el de la traza v2), el texto
    que el cliente puso en una foto (`caption`) y el botón o el carrito
    (`interactive`, `order`). Las lecturas del ingest leen eso, no la
    descripción de la foto ni el marcador del botón.
"""
from __future__ import annotations

import json
import re
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


# ── Lo que el webhook traía además del texto efectivo ────────────────────────
# El texto efectivo lo arma el ingest (`sales/translate.py` y el reentry de
# visión de `ingest_inbound_message.py`) con marcas fijas; con ellas se sabe
# qué leyeron las lecturas en producción (`parsed.text`, `interactive`,
# `order`; en una foto, solo el texto que el cliente puso en ella).

#: Banner que el ingest antepone al primer mensaje que llega de un anuncio.
_REFERRAL_BANNER = "[el cliente vino desde "
#: Reentry de visión: foto, comprobante o foto que no se pudo ver.
_PHOTO_PREFIXES = (
    "[el cliente envió una foto: ",
    "[el cliente envió un comprobante de pago: ",
    "[el cliente envió una imagen que no pude ver bien]",
)
_CAPTION_MARK = '] con el texto: "'
_BUTTON_RE = re.compile(r"\[el cliente tocó el botón: (.*)\]", re.DOTALL)
_LIST_RE = re.compile(r"\[el cliente seleccionó: (.*)\]", re.DOTALL)
_CART_RE = re.compile(r'\[el cliente armó un carrito con: (.*?)\](?: "(.*)")?', re.DOTALL)
#: Mensajes sin texto del cliente (`parsed.text` es None), por su tipo.
_NO_TEXT_PREFIXES = (
    ("[datos de envío recibidos]", "interactive"),
    ("[el cliente envió una interacción no soportada: ", "interactive"),
    ("[el cliente compartió su ubicación", "location"),
    ("[el cliente envió un documento PDF", "document"),
    ("[el cliente reaccionó con ", "reaction"),
    ("[el cliente quitó su reacción]", "reaction"),
    ("[el cliente compartió un contacto: ", "contacts"),
)
_MEDIA_RE = re.compile(r"\[el cliente envió un (\w+)\]")


def without_referral_banner(text: str) -> str:
    """El texto efectivo sin el banner del anuncio (el cliente no lo escribió)."""
    if text.startswith(_REFERRAL_BANNER):
        end = text.find("]\n")
        if end != -1:
            return text[end + 2:]
    return text


def _cart_items(summary: str) -> list[dict[str, Any]]:
    items = []
    for part in summary.split(", "):
        qty, sep, retailer_id = part.partition("× ")
        if sep and qty.strip().isdigit():
            items.append({"product_retailer_id": retailer_id, "quantity": int(qty)})
    return items


def ingest_fields(text: str, kind: str | None = None) -> dict[str, Any]:
    """Lo que el webhook traía además de `text` (el texto efectivo): el tipo
    (`kind`: el de la traza si lo trae; si no, el que dice el texto), y según
    el caso el texto de la foto (`caption`: None si no puso nada), el botón
    (`interactive`) o el carrito (`order`). La foto reentra como `text`, igual
    que en la traza de producción."""
    body = without_referral_banner(text)
    if body.startswith(_PHOTO_PREFIXES):
        at = body.find(_CAPTION_MARK)
        caption = body[at + len(_CAPTION_MARK):-1] if at != -1 and body.endswith('"') else None
        return {"kind": kind or "text", "caption": caption or None}
    if match := _BUTTON_RE.fullmatch(body):
        return {"kind": "interactive", "interactive": {"type": "button_reply", "id": "", "title": match.group(1)}}
    if match := _LIST_RE.fullmatch(body):
        return {"kind": "interactive", "interactive": {"type": "list_reply", "id": "", "title": match.group(1)}}
    if match := _CART_RE.fullmatch(body):
        order: dict[str, Any] = {"product_items": _cart_items(match.group(1))}
        if match.group(2):
            order["text"] = match.group(2)
        return {"kind": "order", "order": order}
    for prefix, inferred in _NO_TEXT_PREFIXES:
        if body.startswith(prefix):
            return {"kind": inferred}
    if match := _MEDIA_RE.fullmatch(body):
        return {"kind": match.group(1)}
    return {"kind": kind or "text"}


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
        content = str(e.get("content") or "")
        burst.append({"text": content, "ts_ms": ts, "wamid": e.get("wamid"), **ingest_fields(content)})
    prefix = first_index if first_index is not None else sum(1 for _, ts in timed if ts is not None and ts <= started_ms)
    return burst, prefix


def _inbound_message(m: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    """Un mensaje de la ráfaga desde `inbound[]` de la traza. `text` es el
    turno como lo recibió el workflow (con lo que agregó el ingest: la
    campaña citada, el episodio anterior); `raw_text`, lo que escribió el
    cliente, que es lo que lee el clasificador. La traza nueva lo guarda; en
    las viejas sale del dashboard (mismo wamid) si el turno lo trae envuelto."""
    text = str(m.get("text") or "")
    out: dict[str, Any] = {"text": text, "ts_ms": _ms(m.get("ts_ms")), "wamid": m.get("wamid")}
    raw = m.get("raw_text")
    if not (isinstance(raw, str) and raw.strip()):
        wamid = m.get("wamid")
        dash = next(
            (str(e.get("content") or "") for e in events if wamid and e.get("role") == "user" and e.get("wamid") == wamid),
            "",
        )
        raw = dash if dash.strip() and dash != text and dash in text else None
    if raw:
        out["raw_text"] = raw
    kind = m.get("kind")
    out.update(ingest_fields(raw or text, kind if isinstance(kind, str) and kind else None))
    return out


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
                burst = [_inbound_message(m, events) for m in inbound if isinstance(m, dict)]
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
