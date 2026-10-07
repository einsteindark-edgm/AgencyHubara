"""Informe de huecos (2026-10-07): cuántas veces pasa cada cosa que el análisis
propone corregir, y si vale la pena corregirla.

Junta dos registros que ya viven en el vault: el scorecard (cada regla que
falló, turno por turno: `_evals/scorecards/`) y el testigo de huecos
(`_huecos/`, lo que el scorecard no ve; lo escribe `sales/huecos.py`). Solo
cuenta: sin textos y con los números de los clientes tapados (`···1234`).
El análisis y el plan: `docs/calidad-llm/cobertura-motor.html`.

En producción, dentro del worker `sales_eval`:

    python -m src.plugins.chats.agent.sales_eval.huecos --dias 3 [--json]
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from src.plugins.chats.agent.sales_eval.scorecard import store
from src.plugins.chats.agent.sales_eval.scorecard.bot import episode_bot
from src.plugins.chats.agent.sales_eval.scorecard.registry import SPECS_BY_ID

#: Los registros del scorecard anteriores a esta versión traen falsas alarmas
#: que la v8 corrigió (abstenciones, traspasos, cierre en modo turno).
MIN_REGISTRY_VERSION = 8
#: Menos turnos que esto en la ventana: el veredicto de una regla mayor o menor
#: no pasa de «quizás» (muestra chica).
MIN_TURNS = 50
#: Casos por cada 100 turnos desde los que vale la pena arreglar una regla
#: mayor o menor (una crítica vale la pena con un solo caso).
WORTH_PER_100 = {"mayor": 2.0, "menor": 5.0}
#: Con tantas conversaciones distintas, una regla mayor vale la pena aunque la
#: tasa sea baja: no es un caso aislado.
WORTH_CONVERSATIONS = 3

BOTS = ("nuevo", "actual")
_LEVEL_RANK = {"critico": 0, "mayor": 1, "menor": 2}


@dataclass(frozen=True)
class Proposal:
    id: str
    name: str
    step: str
    checks: tuple[str, ...] = ()
    witness: tuple[str, ...] = ()
    level: str | None = None  # sin reglas del scorecard: el nivel lo da el análisis


#: Lo que el análisis propone y todavía no está hecho (pasos 3 a 6).
PROPOSALS: tuple[Proposal, ...] = (
    Proposal("puerta_afirmaciones", "Puerta de afirmaciones antes de enviar", "4 · motor",
             ("CON-03", "POS-01", "ENV-05", "DES-10", "DES-05", "EST-05"), ("afirmacion_sin_consultar",)),
    Proposal("retener_texto_suelto", "Retener y recomponer el texto suelto", "3 · motor (genérico)",
             witness=("texto_suelto_lista", "texto_suelto_niega_foto", "texto_suelto_promete_volver"), level="mayor"),
    Proposal("saludo_v2", "Saludo v2 (hora y marca)", "4 · paquete", ("APE-01", "APE-02", "APE-04")),
    Proposal("piso_cierre", "Piso de cierre sin COMPRA_EXITOSA", "4 · paquete", ("CIE-06",)),
    Proposal("repregunta", "Repregunta (dato que el pedido ya tiene)", "4 · paquete", ("VAR-06", "ENV-04")),
    Proposal("despedida", "Despedida sin frases prohibidas", "4 · paquete", ("CIE-05",)),
    Proposal("filas_turno", "Filas de turn.yaml (apertura, producto elegido, descubrimiento)", "4 · paquete",
             ("APE-03", "DES-08", "DES-01", "DES-09")),
    Proposal("formulario_una_vez", "Formulario de envío una sola vez", "6 · código fijo", ("ENV-01",)),
    Proposal("selector_duplicado", "Sin selectores duplicados", "6 · código fijo", ("VAR-03",)),
    Proposal("escribir_al_llegar", "Selección y formulario escritos al llegar", "6 · código fijo", ("VAR-04", "ENV-03")),
    Proposal("motivo_verificacion_pago", "Verificación de pago con orden o comprobante", "6 · código fijo", ("TAG-02",)),
    Proposal("redaccion_prompt", "Redacción (pregunta por burbuja, tuteo, emojis)", "prompt",
             ("DES-02", "EST-01", "EST-02")),
)
#: Lo que arregló el paso 2: debería quedar en cero después del deploy.
CONTROL: tuple[str, ...] = ("CIE-02", "CON-02", "CIE-01", "VAR-01")

_BOT_OF_WORKFLOW = {"v2": "nuevo", "v1": "actual"}

Key = tuple[str, str, int]


def _window(days: int, now: datetime, since: date | None) -> list[str]:
    today = now.astimezone(UTC).date()
    out = [(today - timedelta(days=i)) for i in range(max(1, days))]
    return [d.isoformat() for d in out if since is None or d >= since]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines:
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


def _bot_of(vault_dir: Path, session_id: str, episode_id: str, cache: dict[str, list[dict[str, Any]]]) -> str:
    from src.plugins.chats.shared import turn_traces

    if session_id not in cache:
        cache[session_id] = turn_traces.read_traces(vault_dir, session_id)
    bot = episode_bot(t for t in cache[session_id] if t.get("episode_id") == episode_id)
    return bot if bot in BOTS else "actual"  # un episodio mixto se cuenta con el bot de hoy


def _masked(key: Key) -> str:
    sid, ep, turn = key
    return f"···{sid[-4:]} {ep} t{turn}"


def _level(p: Proposal) -> str:
    if p.level:
        return p.level
    levels = [SPECS_BY_ID[c].level for c in p.checks if c in SPECS_BY_ID]
    return min(levels, key=lambda lv: _LEVEL_RANK.get(lv, 9)) if levels else "menor"


def _verdict(level: str, cases: int, conversations: int, turns: int) -> tuple[str, str]:
    if cases == 0:
        return "sin casos todavía", "no apareció en la ventana"
    per_100 = 100 * cases / turns if turns else 0.0
    rate = f"{cases} caso(s) en {turns} turnos ({per_100:.1f} por cada 100), {conversations} conversación(es)"
    if level == "critico":
        return "sí", f"regla crítica: {rate}"
    small = turns < MIN_TURNS
    frequent = per_100 >= WORTH_PER_100.get(level, 5.0) or (level == "mayor" and conversations >= WORTH_CONVERSATIONS)
    if frequent and not small:
        return "sí", rate
    if level == "mayor":
        return "quizás", rate + (" · muestra chica" if small else "")
    return "no por ahora", rate + (" · muestra chica" if small else "")


def build_report(
    vault_dir: Path, *, days: int = 3, now: datetime | None = None, since: date | None = None
) -> dict[str, Any]:
    """El informe de la ventana: cada propuesta con sus casos por bot y su veredicto."""
    now = now or datetime.now(UTC)
    dates = _window(days, now, since)
    records = store.latest_by_unit(store.read_scorecards(store.scorecards_dir(vault_dir), dates=dates))
    turn_records = [r for r in records if r.get("mode") == "turn"]
    kept = [r for r in turn_records if int(r.get("registry_version") or 0) >= MIN_REGISTRY_VERSION]

    traces: dict[str, list[dict[str, Any]]] = {}
    turns = dict.fromkeys(BOTS, 0)
    failing: dict[str, dict[str, set[Key]]] = {}  # check → bot → turnos
    for rec in kept:
        sid, ep = str(rec.get("session_id") or ""), str(rec.get("episode_id") or "")
        bot = _bot_of(vault_dir, sid, ep, traces)
        turns[bot] += len(rec.get("by_turn") or [])
        for row in rec.get("results") or []:
            if row.get("verdict") == "falla" and isinstance(row.get("turn"), int):
                failing.setdefault(str(row.get("check_id")), {}).setdefault(bot, set()).add((sid, ep, row["turn"]))

    witness_turns = dict.fromkeys(BOTS, 0)
    witnessed: dict[str, dict[str, set[Key]]] = {}  # hueco → bot → turnos
    for day in dates:
        for line in _read_jsonl(Path(vault_dir) / "_huecos" / f"{day}.jsonl"):
            bot = _BOT_OF_WORKFLOW.get(str(line.get("workflow")), "actual")
            witness_turns[bot] += 1
            key = (str(line.get("session") or ""), str(line.get("episode") or ""), int(line.get("turn") or 0))
            for hole in line.get("huecos") or []:
                witnessed.setdefault(str(hole), {}).setdefault(bot, set()).add(key)

    proposals: list[dict[str, Any]] = []
    for p in PROPOSALS:
        denominator = turns if p.checks else witness_turns
        por_bot: dict[str, dict[str, int]] = {}
        all_keys: set[Key] = set()
        for bot in BOTS:
            keys = set().union(
                *(failing.get(c, {}).get(bot, set()) for c in p.checks),
                *(witnessed.get(w, {}).get(bot, set()) for w in p.witness),
            )
            all_keys |= keys
            por_bot[bot] = {"casos": len(keys), "conversaciones": len({k[0] for k in keys}), "turnos": denominator[bot]}
        level = _level(p)
        verdict, why = _verdict(
            level, len(all_keys), len({k[0] for k in all_keys}), sum(denominator.values())
        )
        proposals.append({
            "id": p.id,
            "nombre": p.name,
            "paso": p.step,
            "nivel": level,
            "reglas": {c: sum(len(v) for v in failing.get(c, {}).values()) for c in p.checks},
            "testigo": {w: sum(len(v) for v in witnessed.get(w, {}).values()) for w in p.witness},
            "por_bot": por_bot,
            "veredicto": verdict,
            "motivo": why,
            "ejemplos": [_masked(k) for k in sorted(all_keys)[:3]],
        })

    control = [
        {
            "regla": c,
            "nombre": SPECS_BY_ID[c].name if c in SPECS_BY_ID else c,
            "casos": sum(len(v) for v in failing.get(c, {}).values()),
            "por_bot": {bot: len(failing.get(c, {}).get(bot, set())) for bot in BOTS},
            "ejemplos": [_masked(k) for k in sorted(set().union(*failing.get(c, {}).values()))[:3]],
        }
        for c in CONTROL
    ]
    return {
        "generado": now.isoformat(timespec="seconds"),
        "ventana": {"dias": len(dates), "desde": min(dates) if dates else None, "hasta": max(dates) if dates else None},
        "muestra": {
            "episodios": len(kept),
            "excluidos_version_vieja": len(turn_records) - len(kept),
            "turnos": turns,
            "turnos_testigo": witness_turns,
        },
        "propuestas": proposals,
        "control": control,
    }


def render(report: dict[str, Any]) -> str:
    """El informe en Markdown, en español llano."""
    w, m = report["ventana"], report["muestra"]
    lines = [
        f"# Huecos del bot · {w['desde']} a {w['hasta']}",
        "",
        f"Episodios calificados: {m['episodios']} (excluidos por calificación vieja: {m['excluidos_version_vieja']}). "
        f"Turnos: bot nuevo {m['turnos']['nuevo']}, bot actual {m['turnos']['actual']}. "
        f"Turnos en el testigo: nuevo {m['turnos_testigo']['nuevo']}, actual {m['turnos_testigo']['actual']}.",
        "",
        "| ¿Vale la pena? | Propuesta | Paso | Nivel | Bot nuevo | Bot actual | Por qué | Ejemplos |",
        "|---|---|---|---|---|---|---|---|",
    ]
    order = {"sí": 0, "quizás": 1, "no por ahora": 2, "sin casos todavía": 3}
    for p in sorted(report["propuestas"], key=lambda x: (order.get(x["veredicto"], 9), _LEVEL_RANK.get(x["nivel"], 9))):
        nuevo, actual = p["por_bot"]["nuevo"], p["por_bot"]["actual"]
        lines.append(
            f"| **{p['veredicto']}** | {p['nombre']} | {p['paso']} | {p['nivel']} "
            f"| {nuevo['casos']} en {nuevo['conversaciones']} conv. | {actual['casos']} en {actual['conversaciones']} conv. "
            f"| {p['motivo']} | {', '.join(p['ejemplos']) or '—'} |"
        )
    lines += ["", "## Lo que arregló el paso 2 (debería quedar en cero)", ""]
    for c in report["control"]:
        lines.append(f"- {c['regla']} {c['nombre']}: {c['casos']} caso(s) {', '.join(c['ejemplos'])}".rstrip())
    return "\n".join(lines) + "\n"


def main(argv: Iterable[str] | None = None) -> int:
    from src.plugins.chats.agent.sales_eval.evals.composition import get_vault_dir

    parser = argparse.ArgumentParser(description="Informe de huecos del bot (cuántas veces pasa cada cosa).")
    parser.add_argument("--dias", type=int, default=3, help="días hacia atrás (incluye hoy, UTC)")
    parser.add_argument("--desde", type=date.fromisoformat, default=None, help="no contar antes de esta fecha")
    parser.add_argument("--json", action="store_true", help="el informe como JSON")
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = build_report(Path(get_vault_dir()), days=args.dias, since=args.desde)
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
