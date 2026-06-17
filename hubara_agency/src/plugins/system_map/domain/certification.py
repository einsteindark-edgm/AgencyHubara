"""Certificación de plugins para el catálogo (F-SDK-5).

El system_map deja de ser un mapa pasivo: cada plugin del grafo lleva su
veredicto del TCK (`src.sdk.testkit`, consumido vía SDK — plugins → sdk).

Decisión de diseño: se computa EN VIVO por request en lugar de leer los
reportes JSON de `.hubara/certification/` — los checks son filesystem-only y
baratos (ms por plugin), y así el catálogo local NUNCA muestra un veredicto
stale (el reporte JSON sigue siendo el artefacto para CI/PRs). Si el deploy
artifact no incluye `tests/` (containers), el check P-27 se reporta `skip`
honesto (se verifica en CI) en vez de un fail falso.
"""
from __future__ import annotations

from src.plugins.system_map.domain.contracts import PluginCertification
from src.sdk.testkit import run_conformance


def collect_certifications(plugin_ids: list[str]) -> list[PluginCertification]:
    """Corre el TCK de cada plugin y devuelve los veredictos para el grafo."""
    out: list[PluginCertification] = []
    for plugin_id in plugin_ids:
        report = run_conformance(plugin_id)
        out.append(
            PluginCertification(
                plugin_id=plugin_id,
                archetype=report.archetype,
                level=report.level,
                fails=len(report.failed),
                warns=len(report.warnings),
                failed_checks=[
                    {"code": c.code, "detail": c.detail} for c in report.failed
                ],
                warning_checks=[
                    {"code": c.code, "detail": c.detail} for c in report.warnings
                ],
                sdk=report.sdk,
                git_sha=report.git_sha,
                generated_at=report.generated_at,
            )
        )
    return out
