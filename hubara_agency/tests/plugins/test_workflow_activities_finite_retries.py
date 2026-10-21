"""Ninguna activity de un workflow reintenta para siempre (auditoría de Temporal, 2026-10-15).

Sin `retry_policy`, Temporal reintenta sin tope (cada 100 s como máximo): una
lectura del vault que falla siempre (un bug, un archivo roto) deja el workflow
colgado en silencio, con su id tomado, en vez de fallar a la vista. Remarketing
tenía 9 llamadas así. Cada `workflow.execute_activity`/`start_activity` (y las
locales) pasa su `retry_policy=` o un preset que la trae; el preset se sigue
hasta su dict (premortem 2026-10-16: antes cualquier `**PRESET` pasaba sin
mirarlo) y uno que no se puede resolver también frena.
"""
from __future__ import annotations

from pathlib import Path

from tests.plugins.activity_options_ast import ScheduledActivity, all_scheduled_activities, scheduled_activities


def _unbounded(calls: list[ScheduledActivity]) -> list[str]:
    return [c.where for c in calls if c.options is None or "retry_policy" not in c.options]


def test_the_gate_catches_presets_without_a_policy_and_local_activities(tmp_path: Path) -> None:
    """Caso negativo primero: el gate caza lo que tiene que cazar."""
    module = tmp_path / "wf.py"
    module.write_text(
        "from temporalio import workflow\n"
        "_SIN = {'start_to_close_timeout': 1}\n"
        "_CON = {**_SIN, 'retry_policy': 1}\n"
        "async def run():\n"
        "    await workflow.execute_activity(a, **_SIN)\n"  # 5: preset sin política
        "    await workflow.execute_activity(a, **_CON)\n"  # 6: ok
        "    await workflow.execute_activity(a, **de_otro_lado)\n"  # 7: no se resuelve
        "    await workflow.execute_local_activity(a, start_to_close_timeout=1)\n"  # 8: local sin política
        "    await workflow.start_activity(a, retry_policy=1)\n",  # 9: ok
        encoding="utf-8",
    )
    assert [w.rsplit(":", 1)[1] for w in _unbounded(scheduled_activities(module))] == ["5", "7", "8"]


def test_every_workflow_activity_has_a_finite_retry_policy() -> None:
    calls = all_scheduled_activities()
    assert len(calls) > 100, "el gate no está viendo las activities de src/"
    assert _unbounded(calls) == []


def test_every_retry_preset_brings_its_policy() -> None:
    from src.platform.temporal import retry_policies as presets

    for name in ("_LLM_OPTIONS", "_LLM_CHAT_OPTIONS", "_TOOL_OPTIONS", "_CONV_OPTIONS", "LLM_ACTIVITY_OPTIONS"):
        policy = getattr(presets, name)["retry_policy"]
        assert policy.maximum_attempts > 0, name
