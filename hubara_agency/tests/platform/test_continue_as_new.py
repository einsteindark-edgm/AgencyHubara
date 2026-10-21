"""La regla de continue-as-new de las sesiones (auditoría de Temporal, 2026-10-15).

El tope cuenta los turnos DEL RUN (el run que nace de un continue-as-new trae el
acumulado) y el servidor puede sugerir cortar antes. El gate se consulta solo
donde la regla nueva y la vieja difieren: así los runs vivos al desplegar (que
nunca cortaron con la vieja) pasan a la nueva sin quedar memoizados en False, y
la historia de un run que cortó con la vieja se reproduce igual.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from temporalio import workflow

from src.platform.temporal.continue_as_new import CONTINUE_AS_NEW_PATCH, continue_as_new_due

LIMIT = 50


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch):
    """El servidor (`suggested`) y el gate (`patched`) falsos; anota cada
    consulta al gate."""
    state = SimpleNamespace(suggested=False, patched=True, consulted=[])

    def patched(patch_id: str) -> bool:
        state.consulted.append(patch_id)
        return state.patched

    info = SimpleNamespace(is_continue_as_new_suggested=lambda: state.suggested)
    monkeypatch.setattr(workflow, "info", lambda: info)
    monkeypatch.setattr(workflow, "patched", patched)
    return state


def test_a_run_born_from_continue_as_new_does_not_cut_after_one_turn(server) -> None:
    assert continue_as_new_due(run_turns=1, total_turns=LIMIT + 1, limit=LIMIT) is False
    assert server.consulted == [CONTINUE_AS_NEW_PATCH]


def test_a_history_that_cut_with_the_old_rule_still_cuts_on_replay(server) -> None:
    server.patched = False  # la history no trae el marcador
    assert continue_as_new_due(run_turns=1, total_turns=LIMIT + 1, limit=LIMIT) is True


def test_the_server_suggestion_cuts_before_the_cap(server) -> None:
    server.suggested = True
    assert continue_as_new_due(run_turns=3, total_turns=3, limit=LIMIT) is True
    assert server.consulted == [CONTINUE_AS_NEW_PATCH]


def test_a_history_without_the_gate_ignores_the_suggestion(server) -> None:
    server.suggested, server.patched = True, False
    assert continue_as_new_due(run_turns=3, total_turns=3, limit=LIMIT) is False


@pytest.mark.parametrize(
    ("run_turns", "total_turns", "expected"),
    [(3, 3, False), (LIMIT, LIMIT, True), (LIMIT + 2, LIMIT + 2, True)],
)
def test_where_both_rules_agree_the_gate_is_not_consulted(server, run_turns: int, total_turns: int, expected: bool) -> None:
    assert continue_as_new_due(run_turns=run_turns, total_turns=total_turns, limit=LIMIT) is expected
    assert server.consulted == []
