"""Motor del scorecard (HU-SC-1): corre los checks sobre una trayectoria."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace

from src.plugins.chats.agent.sales_eval.scorecard.checks import CODE_CHECKS
from src.plugins.chats.agent.sales_eval.scorecard.checks._helpers import SIN_SENAL, clip
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext, CheckResult, CheckSpec
from src.plugins.chats.agent.sales_eval.scorecard.registry import CHECKS, SPECS_BY_ID
from src.plugins.chats.agent.sales_eval.scorecard.trajectory import Trajectory

OUT_OF_FOCUS = "falla fuera del turno foco"


def run_code_checks(traj: Trajectory, ctx: CheckContext) -> list[CheckResult]:
    """Todos los checks de código del registro, en orden de registro.

    Episodio sin turnos → `no_aplica` en todo. Un check que lanza excepción
    devuelve `desconocido` con el error como evidencia (nunca se omite en
    silencio, lección del promedio legado). En modo turno (`focus_turn`) los
    resultados quedan anclados al turno foco (`pin_to_focus`).
    """
    results: list[CheckResult] = []
    for spec in CHECKS:
        if spec.kind != "code":
            continue
        if not traj.turns:
            results.append(CheckResult(spec.id, "no_aplica", evidence="episodio sin turnos"))
            continue
        fn = CODE_CHECKS.get(spec.id)
        if fn is None:
            results.append(CheckResult(spec.id, "desconocido", evidence="check sin implementación"))
            continue
        try:
            results.append(fn(traj, ctx))
        except Exception as exc:  # noqa: BLE001 — un check roto no tumba el scorecard
            results.append(
                CheckResult(spec.id, "desconocido", evidence=f"error del check: {exc!r}"[:280])
            )
    if traj.focus_turn is not None:
        return pin_to_focus(results, traj.focus_turn)
    return results


def pin_to_focus(
    results: Iterable[CheckResult],
    focus_turn: int,
    specs: Mapping[str, CheckSpec] = SPECS_BY_ID,
) -> list[CheckResult]:
    """Ancla cada resultado al turno foco (modo turno del laboratorio).

    Alarma de seguridad: una `falla` en otro turno (un check que no sabe del
    turno foco) se vuelve `desconocido` — una falla del prefijo real nunca se
    cuenta contra la respuesta candidata. Un `pasa` de un check `future` sin su
    evidencia en el turno foco queda `sin_senal` (nunca un pasa falso).
    """
    pinned: list[CheckResult] = []
    for r in results:
        spec = specs.get(r.check_id)
        if r.verdict == "falla" and r.turn != focus_turn:
            where = f"turno {r.turn}" if r.turn is not None else "sin turno"
            r = replace(r, verdict="desconocido", evidence=clip(f"{OUT_OF_FOCUS} ({where}): {r.evidence}"))
        elif r.verdict == "pasa" and spec is not None and spec.focus == "future" and r.turn != focus_turn:
            r = replace(r, verdict=SIN_SENAL, evidence=clip(f"sin evidencia en el turno foco: {r.evidence}"))
        pinned.append(replace(r, turn=focus_turn))
    return pinned
