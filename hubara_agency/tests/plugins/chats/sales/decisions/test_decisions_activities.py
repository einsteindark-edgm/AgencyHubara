"""Activities de las capas ① y ③ (plan del laboratorio §3.2): llaman al
clasificador por el puerto del SDK y NUNCA fallan. Un error, un timeout o un
perfil desconocido devuelven un plan vacío (①) o `send` (③): el turno sale
como hoy (fail-open)."""
from __future__ import annotations

import pytest
from temporalio.testing import ActivityEnvironment

from src.plugins.chats.agent.sales.decisions.activities import perceive_burst_activity, verify_coverage_activity
from src.plugins.chats.agent.sales.decisions.contracts import PerceiveInput, VerifyInput

SID = "wa_573001234567"
MESSAGES = [
    {"text": "¿me mandas el catálogo? mi número es 3001234567", "ts_ms": 1_000},
    {"text": "y el envío a Bogotá cuánto sale?", "ts_ms": 8_000},
]


@pytest.fixture(autouse=True)
def _fake_classifier(monkeypatch):
    from src.sdk import connectorkit

    monkeypatch.setenv("PERCEPTION_PROVIDER", "fake")
    connectorkit.get_perception_port.cache_clear()
    yield
    connectorkit.get_perception_port.cache_clear()


async def test_perceive_returns_the_plan_and_the_answers_for_the_trace() -> None:
    out = await ActivityEnvironment().run(
        perceive_burst_activity, PerceiveInput(session_id=SID, profile="jev-v1", messages=MESSAGES)
    )

    assert out.ok and out.profile == "jev-v1"
    assert [t["topic"] for t in out.topics] == ["catalogo", "envio"]
    assert any(a["q"] == "topic.catalogo" and a["picked"] for a in out.answers)
    assert not any(a["q"] == "topic.queja" and a["picked"] for a in out.answers)


async def test_perceive_records_what_the_workflow_applies() -> None:
    """Motor de decisiones (diseño v2 §03): la nota del turno y las reglas de
    la capa ② las arma la activity y viajan grabadas en su resultado; el
    workflow solo las aplica. Cambiar una regla afecta solo a los turnos
    nuevos: el replay de una conversación en vuelo lee lo grabado."""
    out = await ActivityEnvironment().run(
        perceive_burst_activity, PerceiveInput(session_id=SID, profile="jev-v1", messages=MESSAGES)
    )

    assert out.contract == 1
    assert out.versions == {"profile": "jev-v1", "questions": "rafaga-v1", "policy": "turno-v1", "model": "fake"}
    assert out.note is not None and out.note.startswith("[PLAN DEL TURNO]") and "catálogo (mensaje 1)" in out.note
    assert set(out.coverage) == {"catalogo", "envio"}
    assert "send_shipping_rates" in out.coverage["envio"]["tools"]
    assert [t["label"] for t in out.topics] == ["catálogo (mensaje 1)", "envío"]


async def test_an_unknown_profile_is_a_turn_as_today() -> None:
    """El perfil del rival OpenAI ya no existe: sin cuestionario ni política,
    el turno sale como hoy (sin nota ni reglas)."""
    out = await ActivityEnvironment().run(
        perceive_burst_activity, PerceiveInput(session_id=SID, profile="openai-lp-v1", messages=MESSAGES)
    )

    assert not out.ok and out.error == "unknown_profile"
    assert (out.note, out.coverage, out.topics) == (None, {}, [])


async def test_perceive_fails_open(monkeypatch) -> None:
    monkeypatch.setenv("PERCEPTION_PROVIDER", "off")
    from src.sdk import connectorkit

    connectorkit.get_perception_port.cache_clear()

    out = await ActivityEnvironment().run(
        perceive_burst_activity, PerceiveInput(session_id=SID, profile="jev-v1", messages=MESSAGES)
    )

    assert not out.ok and out.topics == [] and out.error


async def test_verify_decides_on_the_reply_that_is_about_to_go_out() -> None:
    topics = [{"topic": "catalogo", "msg": 1, "p": 0.9}, {"topic": "envio", "msg": 2, "p": 0.9}]

    covered = await ActivityEnvironment().run(
        verify_coverage_activity,
        VerifyInput(session_id=SID, profile="jev-v1", messages=MESSAGES, topics=topics,
                    reply_text="Te dejo el catálogo y el envío a Bogotá cuesta $X", components=[]),
    )

    assert covered.ok and covered.decision == "send" and covered.missing == []
    assert [a["q"] for a in covered.answers] == ["cover.catalogo", "cover.envio"]


async def test_verify_drafts_the_complement_note(monkeypatch) -> None:
    """③ El texto del turno de sistema del complemento lo redacta el motor
    (viaja grabado): el workflow solo lo encola."""
    from src.platform.perception.adapters.fake import FakePerceptionAdapter
    from src.sdk import connectorkit
    from src.sdk.connectorkit import TypedAnswer

    fake = FakePerceptionAdapter({"cover.catalogo": TypedAnswer(id="cover.catalogo", kind="noul", p=0.05)})

    def _port(_oracle: str) -> FakePerceptionAdapter:
        return fake

    _port.cache_clear = lambda: None
    monkeypatch.setattr(connectorkit, "get_perception_port", _port)
    topics = [{"topic": "catalogo", "msg": 1, "p": 0.9, "label": "catálogo (mensaje 1)"}]

    out = await ActivityEnvironment().run(
        verify_coverage_activity,
        VerifyInput(session_id=SID, profile="jev-v1", messages=MESSAGES, topics=topics, reply_text="Hola", components=[]),
    )

    assert out.decision == "complement" and out.missing == ["catalogo"]
    assert out.complement_note is not None and out.complement_note.startswith("[SISTEMA] Complemento del turno")
    assert "catálogo (mensaje 1)" in out.complement_note


async def test_verify_fails_open(monkeypatch) -> None:
    monkeypatch.setenv("PERCEPTION_PROVIDER", "off")
    from src.sdk import connectorkit

    connectorkit.get_perception_port.cache_clear()

    out = await ActivityEnvironment().run(
        verify_coverage_activity,
        VerifyInput(session_id=SID, profile="jev-v1", messages=MESSAGES,
                    topics=[{"topic": "catalogo", "msg": 1, "p": 0.9}], reply_text="hola", components=[]),
    )

    assert out.decision == "send" and not out.ok


class _RecordingPort:
    """Lo que de verdad sale hacia el proveedor: el `state` como lo anonimiza
    un adaptador (decisión 2 del plan). Delega la respuesta en el fake."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.sent: list[str] = []

    async def ask(self, state, questions, *, timeout_s, redact=()):
        from src.platform.perception.anonymize import anonymize_text

        self.sent.append(anonymize_text(state, redact=redact))
        return await self.inner.ask(state, questions, timeout_s=timeout_s, redact=redact)


@pytest.fixture
def recording_port(monkeypatch, _isolate_vault_dir):
    """El cliente dejó sus datos de envío en el borrador del episodio: el
    nombre de quien recibe y el barrio NO salen hacia el clasificador, aunque
    el cliente o el asesor los repitan en el turno (y aunque no se anuncien con
    "me llamo")."""
    import json

    from src.sdk import connectorkit

    session = _isolate_vault_dir / SID
    session.mkdir(parents=True)
    draft = {"slots": {"nombre_recibe": "Carolina Pérez", "direccion": "Cra 7 # 12-34 apto 501", "barrio": "Chapinero Alto"}}
    (session / "metadata.json").write_text(json.dumps({"episodes": [{"id": "ep_1", "order_draft": draft}]}))
    port = _RecordingPort(connectorkit.get_perception_port("jev-v1"))

    def _port(_profile: str) -> _RecordingPort:
        return port

    _port.cache_clear = lambda: None  # el fixture autouse lo limpia al final
    monkeypatch.setattr(connectorkit, "get_perception_port", _port)
    return port


PERSONAL = [
    {"text": "es para Carolina, que vive en Chapinero Alto", "ts_ms": 1_000},
    {"text": "¿cuánto sale el envío?", "ts_ms": 5_000},
]


async def test_perceive_never_sends_this_customers_names_or_address(recording_port) -> None:
    await ActivityEnvironment().run(perceive_burst_activity, PerceiveInput(session_id=SID, profile="jev-v1", messages=PERSONAL))

    [sent] = recording_port.sent
    for secret in ("Carolina", "Pérez", "Chapinero"):
        assert secret not in sent
    assert "envío" in sent


async def test_verify_never_sends_this_customers_names_even_in_the_reply(recording_port) -> None:
    await ActivityEnvironment().run(
        verify_coverage_activity,
        VerifyInput(session_id=SID, profile="jev-v1", messages=PERSONAL, topics=[{"topic": "envio", "msg": 2, "p": 0.9}],
                    reply_text="Listo Carolina, a Chapinero Alto el envío sale en $12.000", components=[]),
    )

    [sent] = recording_port.sent
    assert "Carolina" not in sent and "Chapinero" not in sent
