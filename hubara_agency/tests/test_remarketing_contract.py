"""Verifica que `RemarketingSessionInput` es serializable como DTO de boundary
(R-JSON) y que la signature del workflow `RemarketingSessionWorkflow.run` lo acepta.
No ejecuta el workflow completo: la primera activity (`claim_conversation_routing`)
falla determinísticamente porque no hay registry; lo que valida este test es la
serializacion/deserializacion del DTO al cruzar la frontera del workflow.
"""
from __future__ import annotations

from dataclasses import asdict
import inspect

from src.domains.remarketing_whatsapp.contracts import RemarketingSessionInput
from src.domains.remarketing_whatsapp.workflows.remarketing import (
    RemarketingSessionWorkflow,
)


def test_remarketing_input_is_json_friendly_dataclass() -> None:
    dto = RemarketingSessionInput(session_id="wa_5491111111111", motivo="cliente dudó del precio")
    payload = asdict(dto)
    assert payload == {"session_id": "wa_5491111111111", "motivo": "cliente dudó del precio"}


def test_remarketing_workflow_run_accepts_dto() -> None:
    # La signature debe declarar exactamente un argumento posicional `input` de tipo DTO.
    sig = inspect.signature(RemarketingSessionWorkflow.run)
    params = [p for p in sig.parameters.values() if p.name != "self"]
    assert len(params) == 1
    assert params[0].name == "input"
    annotation = params[0].annotation
    assert annotation is RemarketingSessionInput or annotation == "RemarketingSessionInput"
