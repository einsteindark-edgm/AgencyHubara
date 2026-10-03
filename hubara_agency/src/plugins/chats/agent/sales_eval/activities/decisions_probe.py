"""Activity de la sonda diaria de Jev (motor de decisiones, diseño v2).

Corre las 20 ráfagas sintéticas de `sales/decisions/probe.py`, compara con la
sonda anterior (¿lo sirve otro snapshot de Jev?), guarda el reporte en
`<vault>/_decisions/probe/` y avisa en el log si quedó `degraded` o `down`.
La lógica vive en `probe.py` (sin Temporal); acá solo el I/O del vault.

R-HEARTBEAT: hasta 20 llamadas seguidas a Jev (3 s como máximo cada una) →
`@with_heartbeat`. R-JSON: sin input; out `DecisionsProbeSummary` (escalares).
R-STATELESS: el vault y la sonda anterior se leen en cada corrida.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from temporalio import activity

from src.plugins.chats.agent.sales.decisions import probe
from src.plugins.chats.agent.sales_eval.evals.contracts import DecisionsProbeSummary
from src.sdk.runtime import WORKSPACE_VAULT_DIR, with_heartbeat

#: Estados que avisan con WARNING en el log del worker.
ALERT_STATUSES = (probe.STATUS_DEGRADED, probe.STATUS_DOWN)
#: Cuántos casos con problema nombra el log (el detalle completo está en el vault).
LOGGED_ROWS = 5


def _rows(rows: Sequence[Mapping[str, Any]], *keys: str) -> str:
    shown = ["·".join(str(row.get(k)) for k in keys) for row in rows[:LOGGED_ROWS]]
    return ", ".join(shown) + (" …" if len(rows) > LOGGED_ROWS else "") or "-"


def _log(summary: DecisionsProbeSummary, report: Mapping[str, Any], previous: Mapping[str, Any] | None) -> None:
    if summary.status == probe.STATUS_NO_KEY:
        activity.logger.info("decisions.probe: sin_llave — OPENROUTER_API_KEY no está cargada; no se llamó a Jev")
        return
    before = ", ".join((previous or {}).get("models") or []) or "-"
    log = activity.logger.warning if summary.status in ALERT_STATUSES else activity.logger.info
    log(
        "decisions.probe: %s — respuestas válidas %s, aciertos %s, p95 %s ms; Jev %s (sonda anterior: %s); "
        "errores de forma: %s; respuestas distintas: %s",
        summary.status, summary.ok_rate, summary.pass_rate, summary.p95_ms, summary.models or "-", before,
        _rows(report.get("shape_errors") or [], "case", "error"),
        _rows(report.get("failures") or [], "case", "question"),
    )


@activity.defn(name="run_decisions_probe")
@with_heartbeat(every=10)
async def run_decisions_probe_activity() -> DecisionsProbeSummary:
    """Corre la sonda, la compara con la anterior, la guarda y avisa (ver el
    docstring del módulo). Si no puede guardar, falla: sin reporte nuevo, el
    control «Bot nuevo» no deja subir (la sonda envejece)."""
    vault = Path(WORKSPACE_VAULT_DIR)
    previous = probe.read_latest(vault)
    report = await probe.run_probe()
    report["status"] = probe.probe_status(report, previous)
    probe.write_report(vault, report)
    summary = DecisionsProbeSummary(
        status=report["status"],
        at_ms=report["at_ms"],
        cases=report["cases"],
        ok_rate=report["ok_rate"],
        pass_rate=report["pass_rate"],
        p95_ms=report["p95_ms"],
        models=", ".join(report["models"]),
        shape_errors=len(report["shape_errors"]),
        failures=len(report["failures"]),
    )
    _log(summary, report, previous)
    return summary
