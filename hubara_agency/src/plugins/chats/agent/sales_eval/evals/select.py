"""Selección de conversaciones a evaluar en una ventana (muestreo).

Enumera las conversaciones candidatas y prioriza cuáles evaluar dentro del
presupuesto de juez (`max_conversations`). Diseño:

  * **Base confiable = scan del vault**: enumera `<vault>/wa_*/sessions/*.jsonl`,
    filtra por ventana (mtime dentro de `lookback_hours`) y por `min_turns`, y
    prioriza por señal (handoff a humano > conversación larga > reciente).
  * **Unidad = episodio** (`select_eval_units`): cada sesión en ventana se
    expande a sus episodios elegibles (activo o cerrado dentro de la ventana) y
    se evalúa CADA episodio por separado — unit id `<session>::<episode>`. Las
    sesiones legacy sin `episodes[]` se evalúan enteras (unit id = session id).
  * **Enhancement opcional = SigNoz**: si `SIGNOZ_API_URL` está seteado, se puede
    reordenar por costo/latencia consultando las trazas. Es best-effort y degrada
    a [] si SigNoz no responde — la selección NUNCA depende de que SigNoz esté
    arriba (el harness corre igual).

Es código de **activity** (lee filesystem / red) — NO se llama desde un workflow.
"""
from __future__ import annotations

import time
from pathlib import Path

from src.plugins.chats.agent.sales_eval.evals import reconstruct
from src.plugins.chats.agent.sales_eval.evals.contracts import EvalWindowInput


def _session_dirs(vault_dir: Path) -> list[Path]:
    if not vault_dir.exists():
        return []
    return [p for p in vault_dir.iterdir() if p.is_dir() and p.name.startswith("wa_")]


def _jsonl_path(session_dir: Path) -> Path:
    return session_dir / "sessions" / f"{session_dir.name}.jsonl"


def _count_lines(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0


def _is_handoff(session_dir: Path) -> bool:
    """¿La sesión terminó (o pasó) por ruta humana? Señal de caso interesante."""
    import json

    from src.platform.constants import ROUTE_HUMANO

    meta = session_dir / "metadata.json"
    if not meta.exists():
        return False
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if data.get("active_route") == ROUTE_HUMANO or data.get("tag") == "HUMANO":
        return True
    # Cualquier escalación histórica también es señal.
    for entry in data.get("status_history", []):
        if entry.get("active_route") == ROUTE_HUMANO:
            return True
    return False


def select_sessions(
    window: EvalWindowInput,
    *,
    vault_dir: Path | None = None,
    now: float | None = None,
) -> list[str]:
    """Devuelve los `session_id` a evaluar, priorizados, hasta `max_conversations`.

    Args:
        window: ventana + tope + min_turns.
        vault_dir: override (default = `WORKSPACE_VAULT_DIR`). Inyectable en tests.
        now: timestamp override (tests). Default = `time.time()`.
    """
    if vault_dir is None:
        from src.platform.config import WORKSPACE_VAULT_DIR

        vault_dir = WORKSPACE_VAULT_DIR
    now = now if now is not None else time.time()
    cutoff = now - window.lookback_hours * 3600

    candidates: list[tuple[str, float, int, bool]] = []
    for sd in _session_dirs(vault_dir):
        jsonl = _jsonl_path(sd)
        if not jsonl.exists():
            continue
        try:
            mtime = jsonl.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            continue
        nlines = _count_lines(jsonl)
        if nlines < window.min_turns:
            continue
        candidates.append((sd.name, mtime, nlines, _is_handoff(sd)))

    # Prioridad: handoff primero, luego conversación más larga, luego más reciente.
    candidates.sort(key=lambda c: (c[3], c[2], c[1]), reverse=True)
    selected = [sid for sid, _, _, _ in candidates[: window.max_conversations]]

    # Enhancement opcional: reordenar por costo vía SigNoz (best-effort).
    selected = _maybe_prioritize_by_signoz(selected, window) or selected
    return selected


def select_eval_units(
    window: EvalWindowInput,
    *,
    vault_dir: Path | None = None,
    now: float | None = None,
) -> list[str]:
    """Devuelve unit ids `<session>::<episode>` a evaluar, hasta `max_conversations`.

    Expande cada sesión en ventana a sus episodios elegibles:
      * episodio ACTIVO (último sin `closed_at_ms`), o
      * episodio CERRADO dentro de la ventana (`closed_at_ms >= cutoff`) — así un
        cierre reciente se evalúa completo aunque ya no esté activo.
    Sesiones legacy sin `episodes[]` → un unit por la sesión entera (id sin
    `::`, mismo shape que la selección histórica — backward compatible).

    `min_turns` se aplica POR EPISODIO (eventos del slice), no por sesión: un
    `wa_*` con 80 mensajes históricos y un episodio nuevo de 2 no se evalúa.
    """
    if vault_dir is None:
        from src.platform.config import WORKSPACE_VAULT_DIR

        vault_dir = WORKSPACE_VAULT_DIR
    now = now if now is not None else time.time()
    cutoff_ms = int((now - window.lookback_hours * 3600) * 1000)

    units: list[tuple[str, str, float, int, bool]] = []  # (unit, sid, mtime, n, handoff)
    for sd in _session_dirs(vault_dir):
        jsonl = _jsonl_path(sd)
        if not jsonl.exists():
            continue
        try:
            mtime = jsonl.stat().st_mtime
        except OSError:
            continue
        if mtime * 1000 < cutoff_ms:
            continue
        handoff = _is_handoff(sd)
        sid = sd.name
        episodes = reconstruct.read_session_metadata(vault_dir, sid).get("episodes") or []
        episodes = [e for e in episodes if isinstance(e, dict) and e.get("episode_id")]
        if not episodes:
            nlines = _count_lines(jsonl)
            if nlines >= window.min_turns:
                units.append((sid, sid, mtime, nlines, handoff))
            continue
        events = reconstruct.read_session_events(vault_dir, sid)
        for ep in episodes:
            closed = ep.get("closed_at_ms")
            is_active = closed is None
            closed_in_window = isinstance(closed, int) and closed >= cutoff_ms
            if not (is_active or closed_in_window):
                continue
            n = len(reconstruct.slice_episode_events(events, ep))
            if n < window.min_turns:
                continue
            unit = reconstruct.make_eval_unit_id(sid, str(ep["episode_id"]))
            units.append((unit, sid, mtime, n, handoff))

    units.sort(key=lambda u: (u[4], u[3], u[2]), reverse=True)
    selected = [u[0] for u in units[: window.max_conversations]]

    # SigNoz rankea por session.id — reordenar los units por el rank de su sesión
    # (estable dentro de la misma sesión). Best-effort, degrada al orden del vault.
    session_order = list(dict.fromkeys(u[1] for u in units))
    ranked_sessions = _maybe_prioritize_by_signoz(session_order, window)
    if ranked_sessions:
        rank = {s: i for i, s in enumerate(ranked_sessions)}
        selected.sort(
            key=lambda unit: rank.get(reconstruct.parse_eval_unit_id(unit)[0], len(rank))
        )
    return selected


def _maybe_prioritize_by_signoz(
    session_ids: list[str], window: EvalWindowInput
) -> list[str]:
    """Best-effort: reordena `session_ids` por costo (más caro = más prioritario)
    consultando SigNoz. Degrada a [] (caller mantiene el orden por vault).

    Gated por `SIGNOZ_API_URL`. No levanta nunca — observabilidad opcional.
    """
    import os

    base = os.getenv("SIGNOZ_API_URL", "").strip()
    if not base or not session_ids:
        return []
    try:  # pragma: no cover — requiere SigNoz vivo
        import httpx

        api_key = os.getenv("SIGNOZ_API_KEY", "").strip()
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - window.lookback_hours * 3600 * 1000
        # Builder query: sum(gen_ai.usage.cost) agrupado por session.id en la ventana.
        payload = {
            "start": str(start_ms),
            "end": str(end_ms),
            "step": "3600",
            "compositeQuery": {
                "queryType": "builder",
                "panelType": "table",
                "builderQueries": {
                    "A": {
                        "queryName": "A",
                        "dataSource": "traces",
                        "aggregateOperator": "sum",
                        "aggregateAttribute": {
                            "key": "gen_ai.usage.cost",
                            "dataType": "float64",
                            "type": "tag",
                        },
                        "groupBy": [
                            {"key": "session.id", "dataType": "string", "type": "tag"}
                        ],
                        "expression": "A",
                        "orderBy": [{"columnName": "#SIGNOZ_VALUE", "order": "desc"}],
                    }
                },
            },
        }
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["SIGNOZ-API-KEY"] = api_key
        resp = httpx.post(
            f"{base.rstrip('/')}/api/v3/query_range",
            json=payload,
            headers=headers,
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        # Extrae el orden de session.id del resultado tabular (defensivo).
        ranked: list[str] = []
        series = (
            data.get("data", {}).get("result", [{}])[0].get("series", []) or []
        )
        for s in series:
            sid = (s.get("labels") or {}).get("session.id")
            if sid in session_ids:
                ranked.append(sid)
        # Completa con los que SigNoz no devolvió, en su orden original.
        ranked += [s for s in session_ids if s not in ranked]
        return ranked
    except Exception:  # noqa: BLE001 — best-effort, degrada silencioso
        return []
