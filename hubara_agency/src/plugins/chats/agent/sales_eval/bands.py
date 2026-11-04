"""Bandas por regla del scorecard (Ciclo Colossus F4, 2026-11-03).

Para cada regla del scorecard: ¿falla más esta semana que de costumbre? Sin
LLM: solo cuenta los registros que el scorecard ya dejó en el vault
(`_evals/scorecards/`, modo turno).

**La tasa.** Fallas por cada 100 turnos calificados. El turno cuenta para una
regla solo si el registro trae una fila de esa regla en ese turno: las reglas
de juez no tienen filas cuando el juez no corrió, y contarlas sobre todos los
turnos las diluiría. Un turno con la regla fallada cuenta una vez.

**Las ventanas.** Reciente = los 7 días que terminan hoy (UTC); base = los 28
días anteriores. Cada episodio cuenta el día en que ocurrió (`episode_date`:
cierre, o inicio si sigue abierto), no el día en que se calificó: un recálculo
de hoy de una conversación vieja va a su día. Del episodio vale su última
calificación. Registros anteriores a la v8 del registro quedan fuera (traen
falsas alarmas que la v8 corrigió, igual que en `huecos.py`).

**El σ.** Es el σ de la tasa de 7 días que se esperaría con la base, en fallas
por cada 100 turnos. Método: carta p′ de Laney (2002). Parte del error
binomial de la ventana reciente con la tasa de la base, `√(p̄(1−p̄)/n)`, y lo
multiplica por la dispersión entre días que mostró la base: las tasas diarias
de la base se estandarizan (`z = (p_día − p̄)/√(p̄(1−p̄)/n_día)`) y su rango
móvil medio dividido por 1,128 dice cuánto más varían de un día a otro de lo
que explica el azar. Ese factor nunca baja de 1 (no se asume menos variación
que la binomial). Así una regla que de por sí sube y baja entre días necesita
una subida mayor para alarmar. Si la regla no falló en toda la base, el error
binomial usa media falla (`0,5/n_base`) para no dividir por cero.

**Los niveles** (desvío = cuántos σ sube la tasa reciente sobre la base):

* `normal`: menos de 1σ (o ninguna falla en 7 días).
* `anotar`: ≥1σ. Queda en el informe guardado del día; nada más.
* `diagnosticar`: ≥2σ. Lo lee el evaluador externo en su próxima corrida.
  También ≥3σ con menos de 3 fallas en la semana (`pocos_casos`): uno o dos
  casos de una regla que casi nunca falla no abren una alerta.
* `abrir`: ≥3σ con 3 fallas o más, o una regla crítica sin ninguna falla en la
  base que ahora falla (`critica_nueva`). Abre o comenta la alerta
  (`scorecard/band_alerts.py`).

**Muestra chica.** Con menos de 200 turnos en la base, o menos de 7 días con
turnos, la base no alcanza para decir qué es normal: nada pasa de `anotar`.

El informe nunca trae el número completo de una sesión (solo sus últimos 4
dígitos) y la evidencia pasa por `redact_pii`.

En producción, dentro del worker `sales_eval` (solo lee; `--alert` abre o
comenta las alertas como el barrido diario):

    python -m src.plugins.chats.agent.sales_eval.bands [--json] [--alert] [--hasta AAAA-MM-DD]

El barrido diario (`SalesEvalWorkflow`, 23:00 Bogotá) lo corre con las alertas
y guarda el informe en `<vault>/_evals/bands/<fecha>.json`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from src.plugins.chats.agent.sales_eval.evals.redaction import redact_pii
from src.plugins.chats.agent.sales_eval.huecos import MIN_REGISTRY_VERSION
from src.plugins.chats.agent.sales_eval.scorecard import store
from src.plugins.chats.agent.sales_eval.scorecard.registry import CHECKS, SPECS_BY_ID

RECENT_DAYS = 7
BASELINE_DAYS = 28
#: Con menos turnos (o días con turnos) en la base nada pasa de `anotar`.
MIN_BASELINE_TURNS = 200
MIN_BASELINE_DAYS = 7
#: Fallas en la semana para abrir por desvío (la crítica nueva abre con una).
MIN_FAILURES_TO_OPEN = 3
MAX_EXAMPLES = 3
#: Constante d2 de un rango móvil de dos puntos (Shewhart): σ ≈ MR̄ / 1,128.
_D2 = 1.128

BANDS = ("abrir", "diagnosticar", "anotar", "normal")
_BAND_RANK = {band: i for i, band in enumerate(BANDS)}


@dataclass
class _Tally:
    """Turnos y fallas de una regla por día, y las fallas recientes."""

    turns: Counter[str] = field(default_factory=Counter)
    fails: Counter[str] = field(default_factory=Counter)
    examples: list[tuple[str, str, str, int, str]] = field(default_factory=list)


def _windows(end: date) -> tuple[list[str], list[str]]:
    recent = [(end - timedelta(days=i)).isoformat() for i in range(RECENT_DAYS)]
    base = [(end - timedelta(days=RECENT_DAYS + i)).isoformat() for i in range(BASELINE_DAYS)]
    return sorted(recent), sorted(base)


def _session_tail(session_id: str) -> str:
    return f"…{session_id[-4:]}" if len(session_id) >= 4 else f"…{session_id}"


def _evidence(text: str) -> str:
    clean = " ".join(redact_pii(text or "").split())
    return clean if len(clean) <= 200 else clean[:199] + "…"


def _laney_factor(daily: Iterable[tuple[int, int]], p: float) -> float:
    """Dispersión entre días de la base (Laney): rango móvil medio de las
    tasas diarias estandarizadas / 1,128, nunca menor que 1."""
    if not 0 < p < 1:
        return 1.0
    zs = [(f / n - p) / math.sqrt(p * (1 - p) / n) for f, n in daily if n > 0]
    if len(zs) < 2:
        return 1.0
    moving_range = sum(abs(b - a) for a, b in zip(zs, zs[1:])) / (len(zs) - 1)
    return max(1.0, moving_range / _D2)


def _sigma(fails_base: int, turns_base: int, daily: list[tuple[int, int]], turns_recent: int) -> tuple[float, float]:
    """(σ de la tasa reciente por cada 100 turnos, factor de dispersión)."""
    if turns_base == 0 or turns_recent == 0:
        return 0.0, 1.0
    p = fails_base / turns_base
    factor = _laney_factor(daily, p)
    p_sigma = min(max(p, 0.5 / turns_base), 1 - 0.5 / turns_base)
    return 100 * math.sqrt(p_sigma * (1 - p_sigma) / turns_recent) * factor, factor


def _classify(level: str, fails_recent: int, fails_base: int, small: bool, deviation: float) -> tuple[str, str]:
    if fails_recent == 0:
        return "normal", ""
    if small:
        return ("anotar", "muestra_chica") if deviation >= 1 or fails_base == 0 else ("normal", "")
    if level == "critico" and fails_base == 0:
        return "abrir", "critica_nueva"
    if deviation >= 3:
        return ("abrir", "banda_3σ") if fails_recent >= MIN_FAILURES_TO_OPEN else ("diagnosticar", "pocos_casos")
    if deviation >= 2:
        return "diagnosticar", "banda_2σ"
    if deviation >= 1:
        return "anotar", "banda_1σ"
    return "normal", ""


def _rule_row(rule_id: str, tally: _Tally, recent: list[str], base: list[str]) -> dict[str, Any]:
    spec = SPECS_BY_ID[rule_id]
    fails_recent = sum(tally.fails[d] for d in recent)
    turns_recent = sum(tally.turns[d] for d in recent)
    fails_base = sum(tally.fails[d] for d in base)
    turns_base = sum(tally.turns[d] for d in base)
    daily = [(tally.fails[d], tally.turns[d]) for d in base if tally.turns[d] > 0]
    rate_recent = 100 * fails_recent / turns_recent if turns_recent else 0.0
    rate_base = 100 * fails_base / turns_base if turns_base else 0.0
    sigma, factor = _sigma(fails_base, turns_base, daily, turns_recent)
    deviation = (rate_recent - rate_base) / sigma if sigma > 0 else 0.0
    small = turns_base < MIN_BASELINE_TURNS or len(daily) < MIN_BASELINE_DAYS
    band, reason = _classify(spec.level, fails_recent, fails_base, small, deviation)
    examples = sorted(tally.examples, key=lambda e: (e[0], e[1], e[2], e[3]), reverse=True)[:MAX_EXAMPLES]
    return {
        "regla": rule_id,
        "nombre": spec.name,
        "nivel": spec.level,
        "familia": spec.family,
        "banda": band,
        "motivo": reason,
        "reciente": {"fallas": fails_recent, "turnos": turns_recent, "tasa": round(rate_recent, 2)},
        "base": {"fallas": fails_base, "turnos": turns_base, "tasa": round(rate_base, 2), "dias": len(daily)},
        "sigma": round(sigma, 3),
        "dispersion": round(factor, 2),
        "desvio": round(deviation, 2),
        "ejemplos": [
            {"sesion": _session_tail(sid), "episodio": ep, "turno": turn, "fecha": day, "evidencia": _evidence(ev)}
            for day, sid, ep, turn, ev in examples
        ],
    }


def build_report(
    vault_dir: Path, *, now: datetime | None = None, end: date | None = None
) -> dict[str, Any]:
    """El informe de bandas: una fila por regla del registro, lo más grave primero."""
    now = now or datetime.now(UTC)
    end = end or now.astimezone(UTC).date()
    recent, base = _windows(end)
    recent_set, window = set(recent), set(recent) | set(base)
    records = store.latest_by_unit(store.read_scorecards(store.scorecards_dir(vault_dir), dates=window))

    tallies: dict[str, _Tally] = {rule_id: _Tally() for rule_id in SPECS_BY_ID}
    kept = old = turns_recent = turns_base = 0
    for rec in records:
        day = str(rec.get("episode_date") or rec.get("date") or "")[:10]
        if rec.get("mode") != "turn" or day not in window:
            continue
        if int(rec.get("registry_version") or 0) < MIN_REGISTRY_VERSION:
            old += 1
            continue
        kept += 1
        n_turns = len(rec.get("by_turn") or [])
        turns_recent += n_turns if day in recent_set else 0
        turns_base += 0 if day in recent_set else n_turns
        seen: dict[str, set[int]] = {}
        failed: dict[str, dict[int, str]] = {}
        for row in rec.get("results") or []:
            rule_id, turn = str(row.get("check_id")), row.get("turn")
            if rule_id not in tallies or not isinstance(turn, int):
                continue
            seen.setdefault(rule_id, set()).add(turn)
            if row.get("verdict") == "falla":
                failed.setdefault(rule_id, {}).setdefault(turn, str(row.get("evidence") or ""))
        sid, ep = str(rec.get("session_id") or ""), str(rec.get("episode_id") or "")
        for rule_id, turns in seen.items():
            tallies[rule_id].turns[day] += len(turns)
        for rule_id, by_turn in failed.items():
            tallies[rule_id].fails[day] += len(by_turn)
            if day in recent_set:
                tallies[rule_id].examples.extend((day, sid, ep, t, ev) for t, ev in by_turn.items())

    rows = [_rule_row(spec.id, tallies[spec.id], recent, base) for spec in CHECKS]
    rows.sort(key=lambda r: (_BAND_RANK[r["banda"]], -r["desvio"], r["regla"]))
    return {
        "generado": now.isoformat(timespec="seconds"),
        "ventanas": {
            "reciente": {"desde": recent[0], "hasta": recent[-1], "dias": RECENT_DAYS},
            "base": {"desde": base[0], "hasta": base[-1], "dias": BASELINE_DAYS},
        },
        "muestra": {
            "episodios": kept,
            "excluidos_version_vieja": old,
            "turnos_recientes": turns_recent,
            "turnos_base": turns_base,
        },
        "parametros": {
            "min_turnos_base": MIN_BASELINE_TURNS,
            "min_dias_base": MIN_BASELINE_DAYS,
            "min_fallas_para_abrir": MIN_FAILURES_TO_OPEN,
        },
        "conteo": {band: sum(1 for r in rows if r["banda"] == band) for band in BANDS},
        "reglas": rows,
    }


_BAND_LABEL = {
    "abrir": "**abrir**",
    "diagnosticar": "diagnosticar",
    "anotar": "anotar",
}


def render(report: dict[str, Any]) -> str:
    """El informe en Markdown, en español llano: solo las reglas que se movieron."""
    rec, base, sample = report["ventanas"]["reciente"], report["ventanas"]["base"], report["muestra"]
    count = report["conteo"]
    lines = [
        f"# Bandas por regla · {rec['desde']} a {rec['hasta']}",
        "",
        f"Base: {base['desde']} a {base['hasta']}. Episodios calificados: {sample['episodios']} "
        f"(excluidos por calificación vieja: {sample['excluidos_version_vieja']}). "
        f"Turnos: {sample['turnos_recientes']} en los últimos 7 días, {sample['turnos_base']} en la base.",
        "",
        f"Abrir: {count['abrir']} · diagnosticar: {count['diagnosticar']} · anotar: {count['anotar']} · "
        f"normal: {count['normal']}.",
        "",
    ]
    moved = [r for r in report["reglas"] if r["banda"] != "normal"]
    if not moved:
        return "\n".join([*lines, "Ninguna regla se salió de su banda."]) + "\n"
    lines += [
        "| Nivel | Regla | Gravedad | 7 días (por 100 turnos) | Base | σ | Desvío | Motivo | Ejemplos |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in moved:
        rr, rb = r["reciente"], r["base"]
        examples = ", ".join(f"{e['sesion']} {e['episodio']} t{e['turno']}" for e in r["ejemplos"]) or "—"
        lines.append(
            f"| {_BAND_LABEL[r['banda']]} | {r['regla']} {r['nombre']} | {r['nivel']} "
            f"| {rr['tasa']} ({rr['fallas']} en {rr['turnos']}) | {rb['tasa']} ({rb['fallas']} en {rb['turnos']}) "
            f"| {r['sigma']} | {r['desvio']}σ | {r['motivo'] or '—'} | {examples} |"
        )
    return "\n".join(lines) + "\n"


def bands_dir(vault_dir: Path) -> Path:
    return Path(vault_dir) / "_evals" / "bands"


def save_report(vault_dir: Path, report: dict[str, Any]) -> Path:
    """Guarda el informe del día (lo que se anotó) en `_evals/bands/<hasta>.json`."""
    path = bands_dir(vault_dir) / f"{report['ventanas']['reciente']['hasta']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def main(argv: Iterable[str] | None = None) -> int:
    from src.plugins.chats.agent.sales_eval.evals.composition import get_vault_dir
    from src.plugins.chats.agent.sales_eval.scorecard import band_alerts

    parser = argparse.ArgumentParser(description="Bandas por regla del scorecard (¿qué falla más que de costumbre?).")
    parser.add_argument("--hasta", type=date.fromisoformat, default=None, help="último día de la ventana (UTC; default hoy)")
    parser.add_argument("--json", action="store_true", help="el informe como JSON")
    parser.add_argument("--alert", action="store_true", help="abre o comenta las alertas de las reglas en `abrir`")
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = build_report(Path(get_vault_dir()), end=args.hasta)
    if args.alert:
        report["alertas"] = asyncio.run(band_alerts.notify_bands(report))
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
