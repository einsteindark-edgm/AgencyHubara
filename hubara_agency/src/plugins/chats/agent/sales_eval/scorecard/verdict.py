"""Veredicto del episodio (HU-SC-1) — nunca un promedio.

  * **FALLA**  — cae al menos un check de nivel crítico.
  * **ALERTA** — ningún crítico, cae al menos uno mayor.
  * **PASA**   — solo fallos menores o ninguno.
  * **SIN_DATOS** — el episodio no tiene turnos para evaluar.

Un check de juez que todavía no está calibrado contra etiquetas humanas no
puede reprobar un episodio por sí solo: su nivel crítico se degrada a mayor
(regla del plan §3.8). `desconocido`, `no_aplica` y `sin_senal` (modo turno)
no cuentan para el cumplimiento, que es secundario y nunca titular.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from src.plugins.chats.agent.sales_eval.scorecard.model import CheckResult, CheckSpec
from src.plugins.chats.agent.sales_eval.scorecard.trajectory import Trajectory

LEVEL_RANK = {"critico": 0, "mayor": 1, "menor": 2}
VERDICT_RANK = {"FALLA": 0, "ALERTA": 1, "PASA": 2, "SIN_DATOS": 3}


@dataclass(frozen=True)
class Scorecard:
    session_id: str
    episode_id: str
    verdict: str
    fidelity: str
    counts: dict[str, int]
    compliance: float | None
    first_failure: dict[str, Any] | None
    first_critical: dict[str, Any] | None
    stage_final: str | None
    closing_tag: str | None
    turns: int
    results: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def effective_level(spec: CheckSpec, calibrated: Iterable[str]) -> str:
    if spec.kind == "judge" and spec.level == "critico" and spec.id not in set(calibrated):
        return "mayor"
    return spec.level


def _earliest(failures: list[tuple[dict[str, Any], str]]) -> dict[str, Any] | None:
    if not failures:
        return None
    big = 10**9
    row, _ = min(
        failures,
        key=lambda f: (
            f[0]["turn"] if f[0]["turn"] is not None else big,
            LEVEL_RANK.get(f[1], 9),
            f[0]["check_id"],
        ),
    )
    return {"turn": row["turn"], "check_id": row["check_id"]}


def compute_scorecard(
    trajectory: Trajectory,
    specs: Mapping[str, CheckSpec],
    results: Iterable[CheckResult],
    *,
    calibrated: Iterable[str] = frozenset(),
) -> Scorecard:
    calibrated_set = set(calibrated)
    counts = {"critico": 0, "mayor": 0, "menor": 0, "pasa": 0, "no_aplica": 0, "desconocido": 0}
    rows: list[dict[str, Any]] = []
    failures: list[tuple[dict[str, Any], str]] = []
    for r in results:
        spec = specs.get(r.check_id)
        if spec is None:
            continue
        level = effective_level(spec, calibrated_set)
        row = {
            "check_id": r.check_id,
            "verdict": r.verdict,
            "level": level,
            "turn": r.turn,
            "evidence": r.evidence,
            "critique": r.critique,
            "source": r.source,
        }
        if r.topics:
            row["topics"] = [dict(t) for t in r.topics]
        rows.append(row)
        if r.verdict == "falla":
            counts[level] += 1
            failures.append((row, level))
        elif r.verdict in counts:
            counts[r.verdict] += 1
        elif r.verdict == "sin_senal":
            # Solo existe en modo turno: la clave no aparece en los registros
            # de episodio (mismos bytes que antes del laboratorio).
            counts["sin_senal"] = counts.get("sin_senal", 0) + 1

    if not trajectory.turns:
        verdict = "SIN_DATOS"
    elif counts["critico"]:
        verdict = "FALLA"
    elif counts["mayor"]:
        verdict = "ALERTA"
    else:
        verdict = "PASA"

    decided = counts["pasa"] + counts["critico"] + counts["mayor"] + counts["menor"]
    compliance = round(counts["pasa"] / decided, 4) if decided else None
    return Scorecard(
        session_id=trajectory.session_id,
        episode_id=trajectory.episode_id,
        verdict=verdict,
        fidelity=trajectory.fidelity,
        counts=counts,
        compliance=compliance,
        first_failure=_earliest(failures),
        first_critical=_earliest([f for f in failures if f[1] == "critico"]),
        stage_final=trajectory.stage_final,
        closing_tag=trajectory.closing_tag,
        turns=len(trajectory.turns),
        results=rows,
    )
