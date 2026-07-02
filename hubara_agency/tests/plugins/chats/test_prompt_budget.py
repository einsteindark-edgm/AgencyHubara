"""Ratchet de presupuesto del system prompt del agente Sales.

Antes de la dieta (2026-07-02) los archivos siempre-cargados sumaban ~93 KB
(~25-30k tokens) y viajaban ENTEROS en cada llamada al LLM — 28 llamadas de
una sola conversación real (run eda8d460) sumaron 1.06M prompt tokens, y la
obediencia por-regla se degradaba (cada falla observada YA tenía una regla en
el prompt que la prohibía). La dieta: TOOLS.md 51.7→~10 KB (el detalle por
tool vive en su `description`), guion 19.6→~7.6 KB core + guion POR ETAPA
inyectado determinísticamente (`resolve_funnel_stage`).

Este ratchet impide la regresión silenciosa: cada regla nueva que se agregue
al prompt debe pagarse quitando algo (o convirtiéndose en mecánica
determinista, que es la preferencia del repo — L-11, sanitizador, gates).
"""
from __future__ import annotations

from pathlib import Path

_WS = (
    Path(__file__).resolve().parents[3]
    / "src/plugins/chats/agent/sales/workspace"
)

# Presupuestos en bytes (con holgura sobre el estado post-dieta; subirlos
# requiere justificación en el PR — son un ratchet, no una formalidad).
_ALWAYS_LOADED_BUDGETS: dict[str, int] = {
    "TOOLS.md": 16_000,
    "SOUL.md": 16_000,
    "IDENTITY.md": 6_000,
    "AGENTS.md": 6_000,
    "USER.md": 4_000,
    "skills/sales_script/SKILL.md": 10_000,
}
_TOTAL_ALWAYS_BUDGET = 55_000  # pre-dieta: ~93_000

_STAGE_SKILLS = (
    "etapa_descubrimiento",
    "etapa_variantes",
    "etapa_datos_envio",
    "etapa_cierre",
    "etapa_postcierre",
)
_STAGE_BUDGET = 6_000  # cada etapa es un guion enfocado, no un manual


def test_always_loaded_files_within_budget() -> None:
    total = 0
    over: list[str] = []
    for rel, budget in _ALWAYS_LOADED_BUDGETS.items():
        size = (_WS / rel).stat().st_size
        total += size
        if size > budget:
            over.append(f"{rel}: {size} > {budget}")
    assert not over, (
        "Archivos siempre-en-prompt sobre presupuesto (dieta 2026-07-02): "
        + "; ".join(over)
        + ". Si la regla nueva es DURA, conviértela en mecánica determinista "
        "(gate/guard) en vez de prompt; si es de tono, paga el espacio "
        "quitando otra cosa."
    )
    assert total <= _TOTAL_ALWAYS_BUDGET, (
        f"El system prompt estático pesa {total} bytes (> {_TOTAL_ALWAYS_BUDGET}). "
        f"Pre-dieta eran ~93000 y la obediencia por-regla se degradaba."
    )


def test_stage_skills_within_budget_and_not_always() -> None:
    for stage in _STAGE_SKILLS:
        p = _WS / "skills" / stage / "SKILL.md"
        assert p.is_file(), f"falta {p}"
        size = p.stat().st_size
        assert size <= _STAGE_BUDGET, (
            f"{stage}: {size} > {_STAGE_BUDGET} — el guion de etapa debe ser "
            f"enfocado; lo transversal va al core (sales_script)."
        )
        content = p.read_text(encoding="utf-8")
        assert '"always": true' not in content, (
            f"{stage} marcado always:true — anularía la carga por etapa "
            f"(volveríamos a inyectar todo el guion en cada llamada)."
        )


def test_sales_script_core_is_the_only_always_skill() -> None:
    """El único skill always:true del workspace Sales es el core del guion.

    Cualquier otro always reabre la inflación del prompt por la puerta de
    atrás (el caso hubara_catalog: es load_skill on-demand a propósito)."""
    always: list[str] = []
    for skill_md in sorted((_WS / "skills").glob("*/SKILL.md")):
        if '"always": true' in skill_md.read_text(encoding="utf-8"):
            always.append(skill_md.parent.name)
    assert always == ["sales_script"], (
        f"Skills always:true inesperados: {always} — solo sales_script "
        f"(core) debe inyectarse siempre."
    )
