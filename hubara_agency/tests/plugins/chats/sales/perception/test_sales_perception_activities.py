"""Activities de las capas ① y ③ (plan del laboratorio §3.2): llaman al
clasificador por el puerto del SDK y NUNCA fallan. Un error, un timeout o un
perfil desconocido devuelven un plan vacío (①) o `send` (③): el turno sale
como hoy (fail-open)."""
from __future__ import annotations

import pytest
from temporalio.testing import ActivityEnvironment

from src.plugins.chats.agent.sales.perception.activities import perceive_burst_activity, verify_coverage_activity
from src.plugins.chats.agent.sales.perception.contracts import PerceiveInput, VerifyInput

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
