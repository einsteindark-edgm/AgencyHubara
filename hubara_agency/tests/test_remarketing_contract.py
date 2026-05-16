"""Verifica que `RemarketingSessionInput` es serializable como DTO de boundary
(R-JSON) y que la signature del workflow `RemarketingSessionWorkflow.run` lo acepta.
No ejecuta el workflow completo: la primera activity (`claim_conversation_routing`)
falla determinísticamente porque no hay registry; lo que valida este test es la
serializacion/deserializacion del DTO al cruzar la frontera del workflow.
"""
from __future__ import annotations

from dataclasses import asdict
import inspect

from src.plugins.chats.agent.remarketing.contracts import RemarketingSessionInput
from src.plugins.chats.agent.remarketing.workflows.remarketing import (
    RemarketingSessionWorkflow,
)


def test_remarketing_input_is_json_friendly_dataclass() -> None:
    dto = RemarketingSessionInput(session_id="wa_5491111111111", motivo="cliente dudó del precio")
    payload = asdict(dto)
    # PR-A: `runtime_workspace_path` se agrega al DTO con default `None` para
    # cumplir R-JSON sin romper fixtures viejas (ver ADR-2026-05-06-08).
    assert payload == {
        "session_id": "wa_5491111111111",
        "motivo": "cliente dudó del precio",
        "runtime_workspace_path": None,
    }


def test_remarketing_input_accepts_runtime_workspace_path() -> None:
    dto = RemarketingSessionInput(
        session_id="wa_5491111111111",
        motivo="cliente dudó del precio",
        runtime_workspace_path="/tmp/ws-remarketing",
    )
    assert dto.runtime_workspace_path == "/tmp/ws-remarketing"
    payload = asdict(dto)
    assert payload["runtime_workspace_path"] == "/tmp/ws-remarketing"


def test_remarketing_workflow_run_accepts_dto() -> None:
    # La signature debe declarar exactamente un argumento posicional `input` de tipo DTO.
    sig = inspect.signature(RemarketingSessionWorkflow.run)
    params = [p for p in sig.parameters.values() if p.name != "self"]
    assert len(params) == 1
    assert params[0].name == "input"
    annotation = params[0].annotation
    assert annotation is RemarketingSessionInput or annotation == "RemarketingSessionInput"
