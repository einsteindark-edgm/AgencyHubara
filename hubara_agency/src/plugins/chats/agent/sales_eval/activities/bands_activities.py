"""Activity de las bandas por regla del scorecard (Ciclo Colossus F4, 2026-11-03).

El barrido diario (`SalesEvalWorkflow`, 23:00 Bogotá) la corre después de
calificar los episodios del día: calcula las bandas (`sales_eval/bands.py`),
abre o comenta la alerta `[ALR]` de cada regla en `abrir` (`band_alerts.py`,
no-op sin configuración) y guarda el informe en `<vault>/_evals/bands/<día>.json`
(lo que se anotó, y lo que lee el evaluador externo).

R-HEARTBEAT: lee hasta 35 días de scorecards (puede pasar de 10 s) → el cálculo
va en un hilo y `@with_heartbeat` late mientras tanto. R-JSON: entra
`BandsInput`, sale `BandsSummary` (escalares). R-STATELESS: nada a nivel de
módulo; el vault sale del composition root. Nunca lanza: un error queda en
`error` y el barrido sigue con la eval legada.
"""
from __future__ import annotations

import asyncio
from datetime import date

from temporalio import activity

from src.plugins.chats.agent.sales_eval.evals import composition
from src.plugins.chats.agent.sales_eval.evals.contracts import BandsInput, BandsSummary
from src.sdk.runtime import with_heartbeat


@activity.defn(name="compute_scorecard_bands")
@with_heartbeat(every=10)
async def compute_scorecard_bands_activity(inp: BandsInput) -> BandsSummary:
    from src.plugins.chats.agent.sales_eval import bands
    from src.plugins.chats.agent.sales_eval.scorecard import band_alerts

    vault = composition.get_vault_dir()
    try:
        end = date.fromisoformat(inp.end) if inp.end else None
        report = await asyncio.to_thread(bands.build_report, vault, end=end)
    except Exception as exc:  # noqa: BLE001 — las bandas nunca tumban el barrido
        activity.logger.warning("bandas: no se pudieron calcular: %r", exc)
        return BandsSummary(error=repr(exc)[:300])

    count = report["conteo"]
    error = ""
    outcome: list[dict] = []
    if inp.alert:
        try:
            outcome = await band_alerts.notify_bands(report)
            report["alertas"] = outcome
        except Exception as exc:  # noqa: BLE001 — la alerta es best-effort
            activity.logger.warning("bandas: alerta no enviada: %r", exc)
            error = f"alerta: {exc!r}"[:300]
    try:
        path = str(bands.save_report(vault, report))
    except OSError as exc:
        activity.logger.warning("bandas: no se pudo guardar el informe: %r", exc)
        path, error = "", error or f"guardar: {exc!r}"[:300]

    activity.logger.info(
        "bandas %s: abrir=%s diagnosticar=%s anotar=%s alertas=%s",
        report["ventanas"]["reciente"]["hasta"], count["abrir"], count["diagnosticar"], count["anotar"], outcome,
    )
    return BandsSummary(
        to_open=count["abrir"],
        to_diagnose=count["diagnosticar"],
        to_note=count["anotar"],
        opened=sum(1 for o in outcome if o["accion"] == "abierta"),
        commented=sum(1 for o in outcome if o["accion"] == "comentada"),
        report_path=path,
        error=error,
    )
