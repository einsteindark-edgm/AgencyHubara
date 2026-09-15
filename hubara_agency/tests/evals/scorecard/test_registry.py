"""Integridad del registro de checks (HU-SC-1): cada check de código tiene su
función, cada check de juez su prompt, y nada existe sin su spec."""
from __future__ import annotations

from src.plugins.chats.agent.sales_eval.scorecard.checks import CODE_CHECKS
from src.plugins.chats.agent.sales_eval.scorecard.judge_checks import JUDGE_PROMPTS
from src.plugins.chats.agent.sales_eval.scorecard.registry import (
    CHECKS,
    FAMILIES,
    KINDS,
    LEVELS,
    SPECS_BY_ID,
)


def test_registry_ids_are_unique_and_fields_valid() -> None:
    assert len(SPECS_BY_ID) == len(CHECKS)
    for c in CHECKS:
        assert c.level in LEVELS, c.id
        assert c.kind in KINDS, c.id
        assert c.family in FAMILIES, c.id
        assert c.origin, c.id
        if c.twin_of:
            assert c.twin_of in SPECS_BY_ID, c.id


def test_every_code_check_has_exactly_one_implementation() -> None:
    code_ids = {c.id for c in CHECKS if c.kind == "code"}
    assert set(CODE_CHECKS) == code_ids


def test_every_judge_check_has_its_own_prompt() -> None:
    judge_ids = {c.id for c in CHECKS if c.kind == "judge"}
    assert set(JUDGE_PROMPTS) == judge_ids
