"""Alertas de las bandas por regla (Ciclo Colossus F4, 2026-11-03).

Una regla en la banda `abrir` (`sales_eval/bands.py`: ≥3σ sobre su base, o
una crítica que nunca fallaba y ahora falla) abre un issue en el repo del ciclo
con el prefijo `[ALR]` y la etiqueta `origen:alerta`; la recepción del ciclo lo
vuelve tarjeta del tablero. Mismo puerto que las alertas por episodio
(`alerts.IssueTrackerPort`, sin configuración no-op).

  * **Huella por regla**: si ya hay un issue abierto de esa regla se comenta
    «volvió a pasar» con las cifras del día, en vez de abrir otro. La huella es
    un hash de largo fijo: la de `EST-03` no es prefijo de la de `EST-03b`.
  * **PII**: los ejemplos llegan con los últimos 4 dígitos de la sesión (nunca
    el número completo) y la evidencia se vuelve a pasar por `redact_pii`.
"""
from __future__ import annotations

import hashlib
from typing import Any

from src.plugins.chats.agent.sales_eval.evals.redaction import redact_pii
from src.plugins.chats.agent.sales_eval.scorecard import alerts

_TITLES = {
    "critica_nueva": "Crítica nueva",
    "banda_3σ": "Banda 3σ",
}


def fingerprint(rule_id: str) -> str:
    return hashlib.sha1(f"banda|{rule_id}".encode("utf-8")).hexdigest()[:12]


def _cell(text: Any) -> str:
    return redact_pii(" ".join(str(text or "").split())).replace("|", "/")


def _examples(row: dict[str, Any]) -> list[str]:
    return [
        f"| {_cell(e.get('fecha'))} | sesión {_cell(e.get('sesion'))} · {_cell(e.get('episodio'))} "
        f"· turno {_cell(e.get('turno'))} | {_cell(e.get('evidencia')) or '—'} |"
        for e in row.get("ejemplos") or []
    ]


def _figures(row: dict[str, Any]) -> str:
    rr, rb = row["reciente"], row["base"]
    return (
        f"{rr['tasa']} fallas por cada 100 turnos en 7 días ({rr['fallas']} en {rr['turnos']} turnos) "
        f"vs {rb['tasa']} de base ({rb['fallas']} en {rb['turnos']}); {row['desvio']}σ"
    )


def render_issue(row: dict[str, Any], report: dict[str, Any]) -> tuple[str, str]:
    kind = _TITLES.get(str(row.get("motivo")), "Banda 3σ")
    title = f"{alerts.TITLE_PREFIX} {kind}: {row['regla']} {row['nombre']}"
    recent, base = report["ventanas"]["reciente"], report["ventanas"]["base"]
    rr, rb = row["reciente"], row["base"]
    why = (
        "Es una regla crítica que no había fallado ni una vez en la base y ahora falla."
        if row.get("motivo") == "critica_nueva"
        else f"La tasa de los últimos 7 días está {row['desvio']}σ por encima de su base."
    )
    examples = _examples(row)
    body = "\n".join([
        "Una regla del scorecard del asesor de ventas se salió de su banda.",
        "",
        f"**Regla:** {row['regla']} · {row['nombre']} (nivel {row['nivel']}, familia {row['familia']})",
        f"**Motivo:** {why}",
        "",
        "| | Fallas | Turnos calificados | Por cada 100 turnos |",
        "|---|---|---|---|",
        f"| Últimos 7 días ({recent['desde']} a {recent['hasta']}) | {rr['fallas']} | {rr['turnos']} | {rr['tasa']} |",
        f"| Base ({base['desde']} a {base['hasta']}, {rb['dias']} días con turnos) "
        f"| {rb['fallas']} | {rb['turnos']} | {rb['tasa']} |",
        "",
        f"**σ de la tasa de 7 días:** {row['sigma']} por cada 100 turnos "
        f"(binomial con la tasa de la base × dispersión entre días {row['dispersion']}). "
        f"**Desvío:** {row['desvio']}σ.",
        "",
        "**Ejemplos recientes** (sesión con sus últimos 4 dígitos):",
        "",
        *(["| Fecha | Dónde | Evidencia |", "|---|---|---|", *examples] if examples else ["—"]),
        "",
        "Para verlos: Agents → Calidad LLM → Conversaciones, filtra por la fecha y busca la sesión por sus "
        f"últimos 4 dígitos; abre el turno y mira la regla {row['regla']}.",
        "Cada día que la regla siga fuera de su banda se agrega un comentario aquí.",
        "",
        f"<!-- {alerts.MARKER}:{fingerprint(row['regla'])} -->",
    ])
    return title, body


def render_comment(row: dict[str, Any], report: dict[str, Any]) -> str:
    day = report["ventanas"]["reciente"]["hasta"]
    where = ", ".join(
        f"sesión {_cell(e.get('sesion'))} · {_cell(e.get('episodio'))} · turno {_cell(e.get('turno'))}"
        for e in row.get("ejemplos") or []
    )
    return f"Volvió a pasar ({day}): {_figures(row)}." + (f" Ejemplos: {where}." if where else "")


async def notify_bands(
    report: dict[str, Any], *, tracker: alerts.IssueTrackerPort | None = None
) -> list[dict[str, Any]]:
    """Abre o comenta la alerta de cada regla en `abrir`. Devuelve qué hizo."""
    rows = [r for r in report.get("reglas") or [] if r.get("banda") == "abrir"]
    if not rows:
        return []
    tracker = tracker if tracker is not None else alerts.tracker_from_env()
    if tracker is None:
        return []
    outcome: list[dict[str, Any]] = []
    for row in rows:
        existing = await tracker.find_open(fingerprint(row["regla"]))
        if existing is not None:
            await tracker.comment(existing, render_comment(row, report))
            outcome.append({"regla": row["regla"], "accion": "comentada", "issue": existing})
            continue
        title, body = render_issue(row, report)
        number = await tracker.create(title, body, [alerts.LABEL])
        outcome.append({"regla": row["regla"], "accion": "abierta", "issue": number})
    return outcome
