"""Relleno del doble check de WhatsApp (PR #437) para lo que salió antes.

Los mensajes anteriores al deploy muestran «sin confirmación»: sus textos no
guardan `wamids` y lo que Meta informó de ellos no está en la conversación.
Este script los une, solo con datos de Meta:

* Los wamids de cada texto viejo:
  - bot: las burbujas de salida de las trazas de turno v2
    (`evals/turn_traces.jsonl`, pasos `outbound`), por texto y hora;
  - bot u operador: `metadata.outbound_text_index` (las últimas burbujas),
    solo si el texto es único en el historial y en el índice.
  Se agregan como `wamids` al evento del historial: atómico, con respaldo
  (`<sid>.jsonl.bak-delivery-backfill`) y sin perder lo que llegue mientras.
  Las tarjetas, plantillas y adjuntos ya traían su `wamid`.
* Los estados: los huérfanos (`_orphan_delivery_statuses.jsonl`) y los
  `delivery_status` del log analítico (`_analytics/<día>.jsonl`), escritos en
  `<vault>/<sid>/delivery_statuses.jsonl` con `DeliveryStatusLog`. Sin hora
  real: `at_ms` en null, nunca inventada.

Idempotente. `--since AAAA-MM-DD`: solo mensajes desde esa fecha. Sin `--apply` solo dice qué haría. Imprime solo conteos, con la
conversación enmascarada. Receta: `docs/doble-check-whatsapp/relleno.md`.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from src.platform.state import FilesystemMetadataStore
from src.platform.whatsapp.formatting import to_whatsapp_text
from src.plugins.chats.shared import turn_traces
from src.plugins.chats.shared.delivery_status import LOG_FILENAME as DELIVERY_LOG_FILENAME
from src.plugins.chats.shared.delivery_status import (
    DeliveryStatusLog,
    fold_delivery_statuses,
    message_delivery,
    message_wamids,
)
from src.sdk.identitykit import is_customer_session_id

#: `--since` es una fecha del negocio: medianoche de Bogotá.
_BOGOTA = ZoneInfo("America/Bogota")

#: El paso `outbound` de la traza se graba al volver el envío y el evento del
#: historial justo después: segundos. Más allá no se adivina.
_TRACE_WINDOW_MS = 120_000


def _chunks(text: str) -> list[str]:
    """Las burbujas en que `_send_freeform` parte un texto."""
    return [c.strip() for c in to_whatsapp_text(text).split("\n\n") if c.strip()]


def _event_ms(event: dict[str, Any]) -> int | None:
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


def _is_old_text(event: dict[str, Any]) -> bool:
    """Un texto saliente sin forma de unirle un estado de WhatsApp."""
    return (
        event.get("role") == "assistant"
        and event.get("sender") in (None, "human")
        and not event.get("tool_calls")
        and event.get("kind") != "ui_component"
        and not event.get("wamid")
        and not event.get("wamids")
        and isinstance(event.get("content"), str)
        and bool(event["content"].strip())
    )


def _trace_sends(traces: list[dict[str, Any]]) -> list[tuple[int, list[tuple[str, str]]]]:
    """(hora, [(wamid, texto)]) de cada envío de texto de las trazas v2."""
    sends = []
    for trace in traces:
        for step in trace.get("steps") or []:
            if not isinstance(step, dict) or step.get("kind") != "outbound":
                continue
            at = step.get("at_ms")
            bubbles = [
                (b.get("wamid") or "", str(b.get("text") or "").strip())
                for b in step.get("bubbles") or []
                if isinstance(b, dict) and b.get("kind") == "text" and b.get("delivered")
            ]
            if isinstance(at, int) and bubbles:
                sends.append((at, bubbles))
    return sends


def _link_from_traces(
    events: list[dict[str, Any]], traces: list[dict[str, Any]], owned: set[str]
) -> dict[int, list[str]]:
    """Índice del evento → wamids de sus burbujas, por texto y hora. Un envío
    con una burbuja que ya es de un mensaje (`owned`) no se ofrece."""
    sends = _trace_sends(traces)
    used = {k for k, (_, bubbles) in enumerate(sends) if owned.intersection(w for w, _ in bubbles)}
    linked: dict[int, list[str]] = {}
    for i, event in enumerate(events):
        if event.get("sender") or not _is_old_text(event):
            continue
        at = _event_ms(event)
        if at is None:
            continue
        chunks = _chunks(event["content"])
        candidates = [
            (abs(at - sent_at), k)
            for k, (sent_at, bubbles) in enumerate(sends)
            if k not in used
            and abs(at - sent_at) <= _TRACE_WINDOW_MS
            and [text for _, text in bubbles] == chunks[: len(bubbles)]
        ]
        if not candidates:
            continue
        _, k = min(candidates)
        used.add(k)
        wamids = [w for w, _ in sends[k][1] if w]
        if wamids:
            linked[i] = wamids
    return linked


#: El índice guarda cada burbuja recortada (`_OUTBOUND_TEXT_INDEX_MAX_CHARS`).
_INDEX_TEXT_MAX_CHARS = 300


def _author(event: dict[str, Any]) -> str:
    return "human" if event.get("sender") == "human" else "agent"


def _link_from_text_index(
    events: list[dict[str, Any]], index: object, taken: dict[int, list[str]], owned: set[str]
) -> dict[int, list[str]]:
    """Índice del evento → wamids, por `metadata.outbound_text_index` (wamid →
    {texto, autor} de cada burbuja, en orden de envío, solo las últimas).

    El índice no tiene hora: solo se une un texto que aparece UNA vez en el
    historial (de ese autor) y UNA vez en el índice, con todas sus burbujas,
    y ninguna es ya de otro mensaje (`owned`, `taken`).
    """
    if not isinstance(index, dict):
        return {}
    bubbles = [
        (w, str(b.get("text") or ""), b.get("author"))
        for w, b in index.items()
        if isinstance(w, str) and w and isinstance(b, dict)
    ]
    used = owned | {w for wamids in taken.values() for w in wamids}
    candidates = {
        i: (_author(e), [c[:_INDEX_TEXT_MAX_CHARS] for c in _chunks(e["content"])])
        for i, e in enumerate(events)
        if i not in taken and _is_old_text(e)
    }
    linked: dict[int, list[str]] = {}
    # Del más nuevo al más viejo: una burbuja que sigue en el índice es del
    # último mensaje que la contiene, no de uno viejo que ya salió del índice.
    for i, (author, chunks) in sorted(candidates.items(), reverse=True):
        if sum(1 for other in candidates.values() if other == (author, chunks)) > 1:
            continue
        runs = [
            [w for w, _, _ in bubbles[p : p + len(chunks)]]
            for p in range(len(bubbles) - len(chunks) + 1)
            if [(t, a) for _, t, a in bubbles[p : p + len(chunks)]] == [(c, author) for c in chunks]
        ]
        if len(runs) == 1 and not used.intersection(runs[0]):
            linked[i] = runs[0]
        # Una burbuja con este texto es de este mensaje aunque no se haya unido.
        for run in runs:
            used.update(run)
    return linked


def _parse_lines(raw: bytes) -> list[tuple[bytes, dict[str, Any] | None]]:
    """Cada línea con su evento (None si no es un objeto JSON). Partido por
    salto de línea como lo leen el panel y el store: `str.splitlines` también corta en
    U+2028, que `json.dumps(ensure_ascii=False)` deja crudo dentro de un texto.
    Planear y reescribir usan esto mismo, así el índice de un evento es uno."""
    out: list[tuple[bytes, dict[str, Any] | None]] = []
    for line in raw.splitlines(keepends=True):
        try:
            row = json.loads(line.decode("utf-8", errors="replace"))
        except ValueError:
            row = None
        out.append((line, row if isinstance(row, dict) else None))
    return out


def _read_jsonl(path: Path, *, only_with: str | None = None) -> list[dict[str, Any]]:
    """Las filas válidas; una línea rota se saltea. `only_with`: filtro barato
    antes de parsear (el log analítico del día trae de todo)."""
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    if only_with is not None:
        needle = only_with.encode("utf-8")
        raw = b"".join(line for line in raw.splitlines(keepends=True) if needle in line)
    return [row for _, row in _parse_lines(raw) if row is not None]


def _meta_statuses(vault: Path, since_day: str | None = None) -> dict[str, list[str]]:
    """wamid → estados que Meta informó por webhook, de dos registros:

    * `_orphan_delivery_statuses.jsonl`: los que no encontraron su mensaje
      (caso 2026-10-15: episodio sin `outbound_messages`).
    * `_analytics/<día>.jsonl` (`delivery_status`): TODOS, también los que
      entraron por el camino del costo con el episodio abierto. Un estado
      siempre llega después del envío: con `since_day` (AAAA-MM-DD, UTC) los
      días anteriores no se leen.
    """
    out: dict[str, list[str]] = {}

    def add(wamid: object, status: object) -> None:
        if isinstance(wamid, str) and wamid and isinstance(status, str):
            out.setdefault(wamid, []).append(status)

    for row in _read_jsonl(vault / "_orphan_delivery_statuses.jsonl"):
        add(row.get("wa_message_id"), row.get("status"))
    for day in sorted((vault / "_analytics").glob("*.jsonl")):
        if since_day is not None and day.stem < since_day:
            continue
        for row in _read_jsonl(day, only_with='"delivery_status"'):
            correlation, payload = row.get("correlation"), row.get("payload")
            if row.get("kind") == "delivery_status" and isinstance(correlation, dict) and isinstance(payload, dict):
                add(correlation.get("wa_message_id"), payload.get("status"))
    return out


#: Copia del historial original, al lado (no termina en `.jsonl`: ningún
#: lector la toma por un historial). Una sola: la de la primera corrida.
BACKUP_SUFFIX = ".bak-delivery-backfill"

#: Las activities abren, escriben una línea y cierran: un instante.
_LATE_WRITER_GRACE_S = 0.3


def _with_wamids(original: bytes, linked: dict[int, list[str]]) -> bytes:
    """El historial con `wamids` en los eventos unidos. Las demás líneas, byte
    a byte. Un evento que ya no es un texto viejo (otro lo completó) no se toca."""
    out = []
    i = -1
    for line, event in _parse_lines(original):
        if event is not None:
            i += 1
            if i in linked and _is_old_text(event):
                line = (json.dumps({**event, "wamids": linked[i]}, ensure_ascii=False) + "\n").encode("utf-8")
        out.append(line)
    return b"".join(out)


def _rewrite_history(path: Path, linked: dict[int, list[str]]) -> None:
    """Atómico (copia temporal + `os.replace`) y con respaldo del original.

    El historial está vivo (las activities le agregan eventos): lo que llega
    al archivo viejo entre la lectura y el reemplazo se pasa al nuevo. Por eso
    el archivo viejo queda abierto hasta el final.
    """
    with path.open("rb") as old:
        original = old.read()
        updated = _with_wamids(original, linked)
        if updated == original:
            return
        backup = path.with_name(path.name + BACKUP_SUFFIX)
        if not backup.exists():
            shutil.copy2(path, backup)
        tmp = path.with_name(f".{path.name}.backfill-tmp")
        with tmp.open("wb") as fh:
            fh.write(updated)
            fh.flush()
            os.fsync(fh.fileno())
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
        # Un escritor que abrió el archivo viejo justo antes del reemplazo.
        time.sleep(_LATE_WRITER_GRACE_S)
        late = old.read()
    if late:
        with path.open("ab") as fh:
            fh.write(late)


def _metadata(vault: Path, sid: str) -> dict[str, Any]:
    try:
        return FilesystemMetadataStore(vault).read(sid)
    except Exception:  # noqa: BLE001 — una metadata ilegible no frena el resto
        return {}


@dataclass
class _SessionPlan:
    linked: dict[int, list[str]]
    #: (wamid, estado) que faltan en el `delivery_statuses.jsonl` de la conversación.
    rows: list[tuple[str, str]]
    outbound: int
    unconfirmed_after: int


def _plan_session(
    vault: Path, sid: str, statuses: dict[str, list[str]], since_ms: int | None = None
) -> tuple[_SessionPlan, set[str]]:
    """Qué le falta a la conversación y qué va a mostrar el chat después.
    Devuelve también los wamids de sus mensajes. Con `since_ms`, solo los
    mensajes desde esa hora (las uniones se deciden mirando todo el chat)."""
    events = _read_jsonl(vault / sid / "sessions" / f"{sid}.jsonl")
    # Las burbujas que ya son de un mensaje (los de después del deploy).
    owned = {w for e in events for w in message_wamids(e)}
    linked = _link_from_traces(events, turn_traces.read_traces(vault, sid), owned)
    index = _metadata(vault, sid).get("outbound_text_index")
    linked |= _link_from_text_index(events, index, linked, owned)

    def in_range(event: dict[str, Any]) -> bool:
        at = _event_ms(event)
        return since_ms is None or (at is not None and at >= since_ms)

    linked = {i: wamids for i, wamids in linked.items() if in_range(events[i])}
    after = [
        {**e, "wamids": linked[i]} if i in linked else e for i, e in enumerate(events) if in_range(e)
    ]
    wamids = [w for e in after for w in message_wamids(e)]

    existing = _read_jsonl(vault / sid / DELIVERY_LOG_FILENAME)
    known = {(r.get("wamid"), r.get("status")) for r in existing}
    rows = []
    for wamid in wamids:
        for status in statuses.get(wamid, []):
            if (wamid, status) not in known:
                known.add((wamid, status))
                rows.append((wamid, status))

    # Lo que va a pintar el chat: el mismo plegado que el panel y la app.
    folded = fold_delivery_statuses([*existing, *({"wamid": w, "status": st} for w, st in rows)])
    outgoing = [e for e in after if e.get("role") == "assistant" and not e.get("tool_calls")]
    unconfirmed = sum(1 for e in outgoing if message_delivery(message_wamids(e), folded)["status"] == "unconfirmed")
    return _SessionPlan(linked, rows, len(outgoing), unconfirmed), set(wamids)


def _masked(sid: str) -> str:
    """La conversación sin el teléfono: sus 4 últimos dígitos."""
    return "···" + sid[-4:]


def main(argv: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vault", type=Path, required=True, help="en la caja: /app/hubara_vault")
    parser.add_argument("--apply", action="store_true", help="escribe; sin esto solo dice qué haría")
    parser.add_argument("--session", action="append", default=[], help="solo esta conversación (repetible)")
    parser.add_argument("--since", help="solo mensajes desde esta fecha (AAAA-MM-DD, medianoche de Bogotá)")
    args = parser.parse_args(argv)
    vault: Path = args.vault

    since_ms = since_day = None
    if args.since:
        since = datetime.fromisoformat(args.since).replace(tzinfo=_BOGOTA)
        since_ms = int(since.timestamp() * 1000)
        since_day = since.astimezone(timezone.utc).date().isoformat()
    statuses = _meta_statuses(vault, since_day)
    log = DeliveryStatusLog(vault)
    # Solo conversaciones de clientes: las únicas en que el chat muestra el check.
    found = {d.name for d in vault.glob("wa_*") if d.is_dir() and is_customer_session_id(d.name)}
    sids = sorted(found if not args.session else found.intersection(args.session))
    report: dict[str, Any] = {
        "mode": "apply" if args.apply else "dry_run",
        "sessions_scanned": len(sids),
        "sessions_not_found": len(set(args.session) - found),
        "text_events_linked": 0,
        "status_rows": 0,
        "sessions": {},
    }
    referenced: set[str] = set()
    for sid in sids:
        plan, wamids = _plan_session(vault, sid, statuses, since_ms)
        referenced |= wamids
        report["text_events_linked"] += len(plan.linked)
        report["status_rows"] += len(plan.rows)
        if plan.linked or plan.rows:
            report["sessions"][_masked(sid)] = {
                "text_events_linked": len(plan.linked),
                "status_rows": len(plan.rows),
                "outbound": plan.outbound,
                "unconfirmed_after": plan.unconfirmed_after,
            }
        if not args.apply:
            continue
        if plan.linked:
            _rewrite_history(vault / sid / "sessions" / f"{sid}.jsonl", plan.linked)
        for wamid, status in plan.rows:
            log.append(sid, wamid=wamid, status=status, at_ms=None)
    # Estados de Meta que no son de ningún mensaje de las conversaciones revisadas.
    report["statuses_without_message"] = len(set(statuses) - referenced)
    print(json.dumps(report, ensure_ascii=False))
    return report


if __name__ == "__main__":
    main()
