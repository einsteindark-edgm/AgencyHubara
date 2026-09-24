"""Arma el sandbox de UN turno desde el banco (plan §3.4 y §3.6, PR 11).

"Teacher forcing": el bot simulado ve exactamente lo que había ANTES del turno
real y nada de después.

  <dest>/vault/<sim>/metadata.json               como estaba al empezar el turno
  <dest>/vault/<sim>/sessions/<sim>.jsonl        historial del dashboard, cortado
  <dest>/vault/<sim>/evals/turn_traces.jsonl     trazas de los turnos anteriores
  <dest>/agent_state/<ws>/sessions/<sim>.jsonl   historial del LLM, cortado
  <dest>/catalog/                                copia del snapshot del banco

`<sim>` es un número ficticio con la forma del real (`wa_570…`: el 570 no
existe en Colombia) y el número real se reemplaza en TODO lo que se escribe.
El banco no se toca: se lee y se copia.

Reglas del estado del inicio (`metadata_as_of`), con la traza anterior como
fuente (`case.draft_before` / `case.state_before`, ver `cases.py`):
  * tag, ruta y escalación: los de la traza anterior; si no hay, sin valor;
  * episodio del turno: el borrador si no cambió después del inicio (si
    cambió, las casillas de la traza anterior), la orden y el cierre de la
    traza anterior del mismo episodio, el cupón si se aplicó antes;
  * el reset del historial del LLM queda pendiente en el primer turno del
    episodio (en producción se aplicó justo ahí) y aplicado en los demás;
  * nada pendiente que dispare efectos o dedupe (intents de UI, CAPI, envíos
    recientes, señal del cliente, handoff): el turno de handoff siembra su
    propio resumen;
  * `status_history` y la espera del formulario de envío, hasta el inicio;
  * `phone_number_id` ficticio: el envío simulado nunca apunta al negocio.
"""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DROP_KEYS = (
    "pending_ui_intents",
    "recent_freeform_sends",
    "capi_outbox",
    "capi_terminal_event",
    "last_inbound_signal",
    "pending_handoff_summary",
)
_ORDER_LEDGERS = ("registered_order", "checkout_verification")
#: El envío del sandbox (sin llave de WhatsApp: simulado) necesita un
#: phone_number_id; nunca el del negocio.
SANDBOX_PHONE_NUMBER_ID = "lab-sandbox"
_SESSION_STATE = (("tag", "tag"), ("active_route", "route"), ("escalation_reason", "escalation_reason"))


@dataclass(frozen=True)
class SandboxPaths:
    session_id: str
    root: Path
    vault_dir: Path
    state_dir: Path
    catalog_dir: Path


def sim_session_id(session_id: str) -> str:
    """Número ficticio estable con la forma del real: `wa_570` + dígitos."""
    digits = session_id.removeprefix("wa_")
    width = max(len(digits) - 3, 1)
    h = int(hashlib.sha256(session_id.encode()).hexdigest(), 16)
    return f"wa_570{h % 10**width:0{width}d}"


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


def _episode_as_of(ep: dict[str, Any], case: dict[str, Any], *, sales_workspace_path: str) -> dict[str, Any]:
    at = int(case["at_ms"])
    before = case.get("state_before") or {}
    out = copy.deepcopy(ep)
    for key, value in list(out.items()):
        if key.endswith("_at_ms") and key != "started_at_ms" and isinstance(value, (int, float)) and value > at:
            out.pop(key)
    draft = out.get("order_draft")
    updated = draft.get("updated_at_ms") if isinstance(draft, dict) else None
    if not (isinstance(updated, (int, float)) and updated <= at):
        out.pop("order_draft", None)
        if case.get("draft_before"):
            out["order_draft"] = {"slots": dict(case["draft_before"])}
    for key in ("order_id", "closing_tag"):
        if before.get(key):
            out[key] = before[key]
        else:
            out.pop(key, None)
    coupon = out.get("applied_coupon")
    applied_at = coupon.get("applied_at_ms") if isinstance(coupon, dict) else None
    if not (isinstance(applied_at, (int, float)) and applied_at <= at):
        out.pop("applied_coupon", None)
    reset = out.get("llm_history_reset")
    if isinstance(reset, dict):
        applied = [p for p in reset.get("applied") or [] if p != sales_workspace_path]
        if not case.get("first_in_episode"):
            applied.append(sales_workspace_path)  # ya se aplicó en un turno anterior del episodio
        reset["applied"] = applied
    return out


def metadata_as_of(metadata: dict[str, Any], case: dict[str, Any], *, sales_workspace_path: str) -> dict[str, Any]:
    """La metadata como estaba al EMPEZAR el turno del caso (ver docstring)."""
    at = int(case["at_ms"])
    before = case.get("state_before") or {}
    meta = {k: copy.deepcopy(v) for k, v in metadata.items() if k not in _DROP_KEYS}
    for key, field in _SESSION_STATE:
        if before.get(field) is None:
            meta.pop(key, None)
        else:
            meta[key] = before[field]
    if not before.get("order_id"):
        for key in _ORDER_LEDGERS:
            meta.pop(key, None)
    if "status_history" in meta:
        meta["status_history"] = [
            h for h in meta["status_history"] or []
            if isinstance(h, dict) and isinstance(h.get("timestamp"), (int, float)) and h["timestamp"] * 1000 <= at
        ]
    waiting = meta.get("shipping_flow_awaiting_reply_since_ms")
    if isinstance(waiting, (int, float)) and waiting > at:
        meta.pop("shipping_flow_awaiting_reply_since_ms")
    episodes = [dict(e) for e in case.get("episodes_at") or [] if isinstance(e, dict)]
    for i, ep in enumerate(episodes):
        if ep.get("episode_id") == case.get("episode_id"):
            episodes[i] = _episode_as_of(ep, case, sales_workspace_path=sales_workspace_path)
    meta["episodes"] = episodes
    meta["phone_number_id"] = SANDBOX_PHONE_NUMBER_ID
    if case.get("trigger") == "handoff":
        meta["pending_handoff_summary"] = str((case.get("real") or {}).get("inbound_text") or "")
    return meta


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


def _llm_lines(lines: list[dict[str, Any]], case: dict[str, Any]) -> list[dict[str, Any]]:
    """Metadatos de exoclaw + los mensajes del prefijo. `last_consolidated`
    y `summary` que se movieron DESPUÉS del turno no se conocen al inicio:
    el corte vuelve al inicio del episodio (si tuvo reset) o al principio (la
    ventana de memoria del loader acota lo que ve el LLM)."""
    head = next((dict(line) for line in lines if line.get("_type") == "metadata"), None)
    messages = [line for line in lines if line.get("_type") != "metadata"][: int(case.get("llm_prefix") or 0)]
    if head is None:
        return messages
    prefix = len(messages)
    consolidated = head.get("last_consolidated")
    if not (isinstance(consolidated, int) and consolidated <= prefix):
        episode = next((e for e in case.get("episodes_at") or [] if e.get("episode_id") == case.get("episode_id")), {})
        start = _ms(episode.get("started_at_ms"))
        reset_at = 0
        if episode.get("llm_history_reset") and start is not None:
            reset_at = sum(1 for m in messages if (_ms(m.get("timestamp")) or 0) < start)
        head["last_consolidated"] = reset_at
        head.pop("summary", None)
    return [head, *messages]


def _scrub(text: str, real_sid: str, sim_sid: str) -> str:
    real, sim = real_sid.removeprefix("wa_"), sim_sid.removeprefix("wa_")
    text = text.replace(real, sim)
    if len(real) > 10:
        text = text.replace(real[-10:], sim[-10:])
    return text


def _write_json(path: Path, value: Any, real: str, sim: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_scrub(json.dumps(value, ensure_ascii=False), real, sim), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]], real: str, sim: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    path.write_text(_scrub(body, real, sim), encoding="utf-8")


def materialize_case(
    bench_dir: Path,
    case: dict[str, Any],
    dest: Path,
    *,
    bench_workspace: str,
    sales_workspace: str,
    sales_workspace_path: str,
) -> SandboxPaths:
    """`bench_workspace`: slug del historial del LLM en el banco (el de
    producción); `sales_workspace`: el slug local (sale del path absoluto del
    workspace de código, como en exoclaw); `sales_workspace_path`: ese path."""
    real = str(case["session_id"])
    sim = sim_session_id(real)
    at = int(case["at_ms"])
    src = bench_dir / "vault" / real
    paths = SandboxPaths(
        session_id=sim,
        root=dest,
        vault_dir=dest / "vault",
        state_dir=dest / "agent_state",
        catalog_dir=dest / "catalog",
    )
    metadata = json.loads((src / "metadata.json").read_text(encoding="utf-8"))
    box = paths.vault_dir / sim
    _write_json(box / "metadata.json", metadata_as_of(metadata, case, sales_workspace_path=sales_workspace_path), real, sim)
    events = _jsonl(src / "sessions" / f"{real}.jsonl")[: int(case.get("dashboard_prefix") or 0)]
    _write_jsonl(box / "sessions" / f"{sim}.jsonl", events, real, sim)
    traces = [t for t in _jsonl(src / "evals" / "turn_traces.jsonl") if (_ms(t.get("turn_started_ms")) or 0) < at]
    _write_jsonl(box / "evals" / "turn_traces.jsonl", traces, real, sim)
    llm = _jsonl(bench_dir / "agent_state" / bench_workspace / "sessions" / f"{real}.jsonl")
    _write_jsonl(paths.state_dir / sales_workspace / "sessions" / f"{sim}.jsonl", _llm_lines(llm, case), real, sim)
    if (bench_dir / "catalog").is_dir():
        shutil.copytree(bench_dir / "catalog", paths.catalog_dir, dirs_exist_ok=True)
    else:
        paths.catalog_dir.mkdir(parents=True, exist_ok=True)
    return paths
