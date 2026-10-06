"""Las decisiones del motor en un caso del laboratorio (auditoría del brazo B,
punto 4).

Cada capacidad que decide durante el caso (lecturas del ingest, tools,
activities del turno) deja su veredicto en `<vault del sandbox>/_decisions/`,
que se borra con el sandbox. El caso las junta ANTES y las publica con su
turno: qué capacidad decidió qué, si lo decidió Jev, la regla, el piso o el
respaldo (y por qué), compactas y con el texto del cliente anonimizado como
en la cola de desacuerdos.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.plugins.chats.agent.sales.decisions.capabilities import Verdict
from src.plugins.chats.agent.sales_lab.sandbox.activities import SandboxCapture
from src.plugins.chats.agent.sales_lab.sandbox.decisions import CaseDecisions, case_disagreements

DATA = "Listo Carolina Pérez, te lo mandamos a Calle 45 # 12-30"


def _verdict(capability: str = "rescate", **over) -> Verdict:
    base = dict(
        capability=capability, value=DATA, by="respaldo", provider="jev", rule=DATA, jev=None, agree=None,
        reason="timeout", model="", latency_ms=1500, answers=(),
    )
    return Verdict(**{**base, **over})


def test_each_decision_is_published_by_stage_compact_and_anonymized() -> None:
    capture = SandboxCapture()
    log = CaseDecisions(capture)

    log.ingest_message(1)
    log(_verdict("compra", value=["affirmation", "text"], rule=[None, "text"], jev=["affirmation", "text"], by="jev",
                 agree=False, reason=None, model="typesafe/jev-1.13", latency_ms=412,
                 answers=({"q": "compra.que_hace", "type": "choice", "p": None, "choice": "confirma", "confidence": 0.93456},)))
    log.turn()
    log(_verdict("destinatario", value=False, rule=False, by="reglas", provider="reglas", reason=None, latency_ms=0))
    log(_verdict())
    capture.trace_payloads.append({"turn": 1})  # el turno quedó grabado: lo que sigue es el complemento
    log(_verdict("saludo", value=True, rule=True, by="reglas", provider="reglas", reason=None, latency_ms=0))

    rows = log.published(redact=["Carolina Pérez"])

    assert rows == [
        {"stage": "ingest", "message": 1, "capability": "compra", "by": "jev", "provider": "jev",
         "value": ["affirmation", "text"], "rule": [None, "text"], "jev": ["affirmation", "text"], "agree": False,
         "model": "typesafe/jev-1.13", "latency_ms": 412,
         "answers": [{"q": "compra.que_hace", "choice": "confirma", "confidence": 0.935}]},
        {"stage": "turno", "capability": "destinatario", "by": "reglas", "provider": "reglas", "value": False},
        {"stage": "turno", "capability": "rescate", "by": "respaldo", "provider": "jev",
         "value": "Listo [nombre], te lo mandamos a [dirección]", "reason": "timeout", "latency_ms": 1500},
        {"stage": "complemento", "capability": "saludo", "by": "reglas", "provider": "reglas", "value": True},
    ]
    assert json.loads(json.dumps(rows)) == rows


def test_the_disagreements_of_the_case_are_collected_before_the_sandbox_goes(tmp_path: Path) -> None:
    """La cola de desacuerdos del sandbox (lo que Jev vio, ya anonimizado por
    la cola) viaja con el caso: la corrida la puede calificar."""
    from src.sdk.connectorkit import DisagreementLog

    DisagreementLog(tmp_path).record(
        capability="cupon", state=f"ESTE MENSAJE DEL CLIENTE\n[1] {DATA}", rule=True, jev=False, model="m",
        answers=[{"q": "cupon.habla", "p": 0.1}], session_id="wa_573001234567", redact=("Carolina Pérez",),
    )

    [row] = case_disagreements(tmp_path, redact=["Carolina Pérez"])

    assert row == {"capability": "cupon", "rule": True, "jev": False,
                   "state": "ESTE MENSAJE DEL CLIENTE\n[1] Listo [nombre], te lo mandamos a [dirección]"}
    assert case_disagreements(tmp_path / "vacío", redact=[]) == []
