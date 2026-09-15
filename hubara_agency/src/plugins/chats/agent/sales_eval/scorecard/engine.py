"""Motor del scorecard (HU-SC-1): corre los checks sobre una trayectoria."""
from __future__ import annotations

from src.plugins.chats.agent.sales_eval.scorecard.checks import CODE_CHECKS
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext, CheckResult
from src.plugins.chats.agent.sales_eval.scorecard.registry import CHECKS
from src.plugins.chats.agent.sales_eval.scorecard.trajectory import Trajectory


def run_code_checks(traj: Trajectory, ctx: CheckContext) -> list[CheckResult]:
    """Todos los checks de código del registro, en orden de registro.

    Episodio sin turnos → `no_aplica` en todo. Un check que lanza excepción
    devuelve `desconocido` con el error como evidencia (nunca se omite en
    silencio, lección del promedio legado).
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
    return results
