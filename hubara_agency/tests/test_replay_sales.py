"""Replay test del workflow de Sales (F6.2 / ADR-005).

Carga la history sintetica capturada por `tests/fixtures/generate_fixtures.py`
y la re-ejecuta contra el codigo actual de `HubaraSalesSessionWorkflow`. Si la
shape de history cambia (orden de activities, signal/query signature, args
serializables) y la fixture queda desactualizada, el `Replayer` lanzara
`NonDeterminismError` y este test fallara.

Cuando se cambie legitimamente la shape, regenerar la fixture con:

    cd hubara_agency
    uv run python tests/fixtures/generate_fixtures.py

Y bumpear el `_v<N>` del filename (ver `tests/fixtures/README.md`).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from src.plugins.chats.agent.sales.workflows.sales_session import HubaraSalesSessionWorkflow

FIXTURE = Path(__file__).parent / "fixtures" / "history_sales_session_v2.json"


async def test_sales_session_replay_does_not_diverge() -> None:
    history = WorkflowHistory.from_json("test-sales", FIXTURE.read_text(encoding="utf-8"))
    replayer = Replayer(workflows=[HubaraSalesSessionWorkflow])
    await replayer.replay_workflow(history)


# History REAL de prod (run 5ed9af2d, 2026-09-18) saneada: sin teléfono, sin API
# keys, sin system prompt ni tool definitions. Tiene la forma PRE
# `escalation-ends-turn-v1`: tras `escalate_to_human` hay un `llm_chat` extra
# (el acuse "Listo, la conversación quedó en manos del equipo humano." que le
# llegó al cliente). El código nuevo corta el turno en la escalación, así que
# este replay es la garantía de que el deploy no rompe runs en vuelo (L-9):
# sin el gate `workflow.patched(...)` falla con NondeterminismError
# ('llm_chat' scheduled vs 'record_turn' command). CONGELADA — no se regenera;
# se borra junto con `workflow.deprecate_patch("escalation-ends-turn-v1")`.
PREPATCH_ESCALATION_FIXTURE = (
    Path(__file__).parent / "fixtures" / "history_sales_escalation_prepatch_v1.json"
)


async def test_prepatch_escalation_history_still_replays() -> None:
    history = WorkflowHistory.from_json(
        "test-sales-escalation-prepatch",
        PREPATCH_ESCALATION_FIXTURE.read_text(encoding="utf-8"),
    )
    replayer = Replayer(workflows=[HubaraSalesSessionWorkflow])
    await replayer.replay_workflow(history)


# History PRE `tag-ends-turn-v1` (run b06636a6, misma clase que L-20): tras
# `manage_conversation_tag` el loop todavía pide un `llm_chat` extra (el acuse
# "Etiqueta registrada."). Sintética A PROPÓSITO, y por una razón de fondo: el
# corte nuevo se decide por una clave NUEVA del envelope (`tag_closure`), así
# que una history real de prod (tool results de forma vieja) jamás lo activa y
# NO puede proteger el gate. El único caso en que una history sin el marker
# trae un envelope que el código nuevo cortaría es la ventana de versiones
# mezcladas de un deploy (activity con la tool nueva + workflow task con el
# loop viejo): eso es lo que esta fixture congela. Generada con el código del
# commit 4052c29 (anterior al gate); cubre los dos sitios del corte: turno de
# cliente con `customer_message` y turno admin de cierre por ghosting.
# CONGELADA — no se regenera; se borra junto con
# `workflow.deprecate_patch("tag-ends-turn-v1")`.
PREPATCH_TAG_CLOSURE_FIXTURE = (
    Path(__file__).parent / "fixtures" / "history_sales_tag_closure_prepatch_v1.json"
)


def _prepatch_tag_closure_history() -> WorkflowHistory:
    return WorkflowHistory.from_json(
        "test-sales-tag-closure-prepatch",
        PREPATCH_TAG_CLOSURE_FIXTURE.read_text(encoding="utf-8"),
    )


async def test_prepatch_tag_closure_history_still_replays() -> None:
    replayer = Replayer(workflows=[HubaraSalesSessionWorkflow])
    await replayer.replay_workflow(_prepatch_tag_closure_history())


@pytest.mark.parametrize(
    ("site", "ungated_from_consultation"),
    [
        # El gate se consulta exactamente DOS veces en la fixture, una por
        # sitio del corte. Se des-gatea cada sitio por separado para probar
        # que la fixture protege a los dos (si no, el primero enmascara al
        # segundo: el replay rompe en el turno del cliente y nunca llega al
        # cierre por ghosting).
        ("turno de cliente con customer_message", 1),
        ("turno admin de cierre por ghosting", 2),
    ],
)
async def test_prepatch_tag_closure_history_breaks_without_the_gate(
    monkeypatch, site: str, ungated_from_consultation: int
) -> None:
    """Control negativo: la fixture PROTEGE el gate (un replay que no puede
    fallar no prueba nada). Se simula el corte SIN su `workflow.patched`: el
    turno termina en el tag y el replay choca con el `llm_chat` que la history
    sí agendó."""
    from temporalio import workflow

    real_patched = workflow.patched
    consultations = {"n": 0}

    def ungated(patch_id: str) -> bool:
        if patch_id != "tag-ends-turn-v1":
            return real_patched(patch_id)
        consultations["n"] += 1
        if consultations["n"] >= ungated_from_consultation:
            return True  # como si la rama nueva no estuviera detrás del gate
        return False  # lo que devuelve el gate real al replayear esta history

    monkeypatch.setattr(workflow, "patched", ungated)

    replayer = Replayer(workflows=[HubaraSalesSessionWorkflow])
    # El choque exacto: la history agendó el llm_chat del acuse y el código sin
    # gate, que ya cortó el turno, agenda `record_turn`.
    with pytest.raises(workflow.NondeterminismError, match="'llm_chat'.*'record_turn'"):
        await replayer.replay_workflow(_prepatch_tag_closure_history())
    assert consultations["n"] == ungated_from_consultation, (
        f"{site}: el gate se consultó {consultations['n']} veces"
    )
