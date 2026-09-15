"""Goldens unificados con el scorecard (HU-SC-5).

El runner de goldens (`scripts/golden_eval.py`) maneja al agente REAL por
escenarios controlados. Acá cada corrida se convierte en trazas con la MISMA
tubería de producción (`build_turn_payload` + `enrich_turn_trace`) y se evalúa
con el MISMO registro de checks: un escenario golden y un episodio de
producción se juzgan igual.

Diferencias de fidelidad con producción, declaradas:
  * El runner no pasa por el ingest: la señal del cliente (aplazamiento o
    afirmación) se detecta del texto con `purchase_signals`, y
    `confirmed_at_ms` no existe (la confirmación sale de la señal).
  * El runner no corre las guardas del workflow: `guards` va vacío, así que
    los gemelos `*b` pasan siempre en goldens.
  * El texto que el LLM escribe junto a tool calls va a `discarded_narration`
    (en producción el default-deny lo descarta).

`pass^k` (τ-bench, Sierra): un escenario "pasa^k" si ninguna de sus k corridas
da FALLA; un check pasa^k si no falló en ninguna corrida donde aplicó.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from src.plugins.chats.agent.sales.turn_trace import build_turn_payload, enrich_turn_trace
from src.plugins.chats.agent.sales_eval.scorecard import service
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext
from src.plugins.chats.agent.sales_eval.scorecard.registry import CHECKS, SPECS_BY_ID
from src.plugins.chats.agent.sales_eval.scorecard.trajectory import build_trajectory, detect_signal

# Behaviors del ledger legado que no son un check (son aserciones genéricas de
# tool usada / no usada, específicas de cada escenario).
BEHAVIORS_WITHOUT_CHECK: set[str] = {"tool_called", "tool_not_called"}

_TURN_MS = 60_000


def check_ids_for_behavior(behavior_type: str) -> list[str]:
    return [c.id for c in CHECKS if behavior_type in c.golden_behaviors]


def _pairs(turns: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    out = []
    pending: dict[str, Any] | None = None
    for t in turns:
        if t.get("role") == "user":
            pending = t
        elif t.get("role") == "assistant" and pending is not None:
            out.append((pending, t))
            pending = None
    return out


def traces_from_golden_run(
    res: dict[str, Any], *, session_id: str, episode_id: str = "ep_golden"
) -> list[dict[str, Any]]:
    ledger = [e for e in res.get("ledger") or [] if isinstance(e, dict)]
    traces: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    for i, (user_t, asst_t) in enumerate(_pairs(res.get("turns") or [])):
        outputs = [o for o in asst_t.get("tool_outputs") or [] if isinstance(o, dict)]
        calls = [e for e in ledger if e.get("turn") == i]
        events = [
            {
                "name": call.get("name"),
                "args": call.get("args") if isinstance(call.get("args"), dict) else {},
                "result": outputs[j].get("output") if j < len(outputs) else "",
            }
            for j, call in enumerate(calls)
        ]
        final = str(asst_t.get("final") or "")
        started = (i + 1) * _TURN_MS
        payload = build_turn_payload(
            trigger="customer",
            inbound_text=str(user_t.get("content") or ""),
            turn_started_ms=started,
            first_contact=i == 0,
            tool_events=events,
            discarded_narration=[str(x) for x in asst_t.get("pre_tool") or []],
            llm_text=final,
            sent_texts=[final] if final else [],
            suppressed_reason=None,
            guards=[],
        )
        metadata = asst_t.get("metadata") if isinstance(asst_t.get("metadata"), dict) else {}
        trace = enrich_turn_trace(
            payload, metadata, previous=previous, session_id=session_id, recorded_at_ms=started + 1_000
        )
        # El encadenamiento (número de turno, etapa de entrada) usa el episodio
        # del metadata; el id fijo `episode_id` se aplica después.
        previous = dict(trace)
        trace["episode_id"] = episode_id
        kind = detect_signal(str(user_t.get("content") or ""))
        trace["signal"] = {"kind": kind, "text": user_t.get("content")} if kind else None
        traces.append(trace)
    return traces


def score_golden_run(
    res: dict[str, Any],
    *,
    session_id: str,
    ctx: CheckContext | None = None,
    episode_id: str = "ep_golden",
) -> dict[str, Any]:
    traces = traces_from_golden_run(res, session_id=session_id, episode_id=episode_id)
    traj = build_trajectory(traces, session_id=session_id, episode={"episode_id": episode_id})
    return service.score_trajectory(traj, ctx or CheckContext())


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def pass_hat_k(runs: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """`runs`: filas con `verdict` y `checks` (check → veredicto) por corrida."""
    runs = list(runs)
    k = len(runs)
    non_failing = sum(1 for r in runs if r.get("verdict") != "FALLA")
    checks: dict[str, dict[str, Any]] = {}
    ids = sorted({cid for r in runs for cid in (r.get("checks") or {})})
    for cid in ids:
        verdicts = [(r.get("checks") or {}).get(cid) for r in runs]
        decided = [v for v in verdicts if v in ("pasa", "falla")]
        passes = sum(1 for v in decided if v == "pasa")
        checks[cid] = {
            "k": k,
            "decided": len(decided),
            "passes": passes,
            "pass_at_1": _rate(passes, len(decided)),
            "pass_hat_k": (passes == len(decided)) if decided else None,
        }
    return {
        "scenario": {"k": k, "pass_at_1": _rate(non_failing, k), "pass_hat_k": non_failing == k and k > 0},
        "checks": checks,
    }


def format_scorecard_md(summary: list[dict[str, Any]]) -> str:
    """Sección markdown del reporte de CI: veredicto por corrida y pass^k."""
    lines = [
        "## Scorecard por etapa",
        "",
        "Mismo registro de checks que producción. pass^k: ninguna corrida del escenario dio FALLA.",
        "",
        "| Escenario | Veredictos | pass^k | Checks que fallaron (corridas) |",
        "|---|---|:--:|---|",
    ]
    for s in summary:
        cards = s.get("scorecards") or []
        if not cards:
            continue
        ph = pass_hat_k(cards)
        failing = [
            f"{cid} ({v['decided'] - v['passes']}/{v['k']})"
            for cid, v in ph["checks"].items()
            if v["pass_hat_k"] is False
        ]
        failing.sort(key=lambda x: SPECS_BY_ID[x.split(" ")[0]].level if x.split(" ")[0] in SPECS_BY_ID else "z")
        mark = "✅" if ph["scenario"]["pass_hat_k"] else "❌"
        lines.append(
            f"| `{s.get('id')}` | {' · '.join(str(c.get('verdict')) for c in cards)} | {mark} | "
            f"{', '.join(failing) or '—'} |"
        )
    return "\n".join(lines)
