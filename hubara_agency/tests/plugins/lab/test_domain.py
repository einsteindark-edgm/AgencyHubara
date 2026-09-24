"""Dominio del plugin lab: las rutas del contrato `lab@v1` de chats.

El cast arma la ruta del provider con los segmentos que manda el navegador;
cada segmento se valida acá (puro) antes de reenviar: una corrida, una
conversación, un episodio o un brazo con otra forma nunca llega a chats.
"""
from __future__ import annotations

import pytest

from src.plugins.lab.domain.logic import (
    LabPathError,
    PROVIDER_PREFIX,
    clean_query,
    conversation_path,
    run_path,
)


def test_provider_prefix_is_the_chats_lab_contract() -> None:
    assert PROVIDER_PREFIX == "/api/chats/lab"


def test_run_path_joins_valid_segments() -> None:
    assert run_path("run-20260923-1041-ab12") == "/api/chats/lab/runs/run-20260923-1041-ab12"
    assert run_path("run-20260923-1041-ab12", "summary") == "/api/chats/lab/runs/run-20260923-1041-ab12/summary"


def test_conversation_path_validates_the_session_id() -> None:
    path = conversation_path("run-20260923-1041-ab12", "wa_573001234567", "turns", "trace")
    assert path == "/api/chats/lab/runs/run-20260923-1041-ab12/conversations/wa_573001234567/turns/trace"


@pytest.mark.parametrize("run", ["..", "run/../x", "RUN-1", "a", "run-%2e%2e", ""])
def test_run_path_rejects_other_shapes(run: str) -> None:
    with pytest.raises(LabPathError):
        run_path(run)


@pytest.mark.parametrize("sid", ["..", "wa_", "session-1", "wa_1/2", "wa_12345%2F"])
def test_conversation_path_rejects_other_session_shapes(sid: str) -> None:
    with pytest.raises(LabPathError):
        conversation_path("run-20260923-1041-ab12", sid)


def test_clean_query_drops_missing_values_and_validates_shapes() -> None:
    assert clean_query(arm="B", rep=2, episode=None) == {"arm": "B", "rep": 2}
    with pytest.raises(LabPathError):
        clean_query(arm="Z")
    with pytest.raises(LabPathError):
        clean_query(episode="ep_x")
    with pytest.raises(LabPathError):
        clean_query(rep=5)
    with pytest.raises(LabPathError):
        clean_query(bench="../bench")
    assert clean_query(arms="A1,B,C", reps=3, bench="new") == {"arms": "A1,B,C", "reps": 3, "bench": "new"}
