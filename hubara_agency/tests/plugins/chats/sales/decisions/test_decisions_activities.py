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


async def test_with_jev_v2_the_activity_reads_what_the_customer_saw_from_the_vault(recording_port, _isolate_vault_dir) -> None:
    """F1: el contexto lo arma la activity leyendo el historial del vault (el
    mismo JSONL del dashboard) sin los mensajes de esta ráfaga, más los hechos
    del pedido. Sin datos personales: el nombre y el barrio se tapan igual."""
    import json

    history = _isolate_vault_dir / SID / "sessions" / f"{SID}.jsonl"
    history.parent.mkdir(parents=True, exist_ok=True)
    events = [
        {"role": "user", "content": "Quiero el Duo Zodiacal", "wamid": "w0"},
        {"role": "assistant", "content": "¡Listo Carolina! ¿Te lo enviamos a la misma dirección?"},
        {"role": "user", "content": "es para Carolina, que vive en Chapinero Alto", "wamid": "w1"},
        {"role": "user", "content": "¿cuánto sale el envío?", "wamid": "w2"},
    ]
    history.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n", encoding="utf-8")
    burst = [{**m, "wamid": w} for m, w in zip(PERSONAL, ("w1", "w2"))]

    out = await ActivityEnvironment().run(perceive_burst_activity, PerceiveInput(session_id=SID, profile="jev-v2", messages=burst))

    [sent] = recording_port.sent
    context, _, turn = sent.partition("ESTE TURNO")
    assert "¿Te lo enviamos a la misma dirección?" in context and "Quiero el Duo Zodiacal" in context
    assert "Chapinero" not in context and "cuánto sale el envío" in turn  # la ráfaga solo va en ESTE TURNO
    assert "HECHOS DEL PEDIDO" in context and "Etapa:" in context
    for secret in ("Carolina", "Chapinero"):
        assert secret not in sent
    assert out.versions["questions"] == "rafaga-v2"



async def test_with_jev_v3_the_activity_passes_the_stage_what_is_missing_and_the_stagnation(
    monkeypatch, _isolate_vault_dir
) -> None:
    """F6: la activity le da a la política la etapa que calcula el código, lo
    que falta en ella (borrador) y cuántos turnos lleva sin un dato nuevo (las
    trazas de la sesión); las preguntas de etapa solo se hacen en esa etapa."""
    import json

    from src.platform.perception.adapters.fake import FakePerceptionAdapter
    from src.sdk import connectorkit

    from src.sdk.connectorkit import TypedAnswer

    given = {"datos.direccion": 0.93, "datos.ciudad": 0.04, "datos.telefono": 0.03, "datos.nombre_recibe": 0.02,
             "datos.metodo_pago": 0.02}
    fake = FakePerceptionAdapter({qid: TypedAnswer(id=qid, kind="noul", p=p) for qid, p in given.items()})

    def _port(_oracle: str) -> FakePerceptionAdapter:
        return fake

    _port.cache_clear = lambda: None
    monkeypatch.setattr(connectorkit, "get_perception_port", _port)
    session = _isolate_vault_dir / SID
    (session / "evals").mkdir(parents=True)
    slots = {"producto": "Cubo", "aroma": "lavanda", "color": "rojo", "cantidad": 1, "ciudad": "Medellín"}
    episode = {"episode_id": "ep_1", "closed_at_ms": None, "order_draft": {"slots": slots}}
    (session / "metadata.json").write_text(json.dumps({"episodes": [episode]}), encoding="utf-8")
    trace = {"episode_id": "ep_1", "stage_out": "datos_envio", "draft": slots, "trigger": "customer"}
    (session / "evals" / "turn_traces.jsonl").write_text("\n".join(json.dumps(trace) for _ in range(3)) + "\n", encoding="utf-8")

    out = await ActivityEnvironment().run(
        perceive_burst_activity, PerceiveInput(session_id=SID, profile="jev-v3", messages=[{"text": "Cra 7 # 12-30", "ts_ms": 1}])
    )

    [(_, questions)] = fake.calls
    assert "datos.direccion" in questions and "cierre.confirma_resumen" not in questions
    assert out.guide["stage"] == "etapa_datos_envio" and out.guide["stagnant"] == 3
    # Faltaban dirección, teléfono, quien recibe y pago (la ciudad ya está en
    # el borrador); la dirección llega en este turno y hay que guardarla.
    assert out.guide["given_now"] == ["direccion"]
    assert out.guide["missing"] == ["telefono", "nombre_recibe", "metodo_pago"]
    [slot] = [r for r in out.tools["required"] if r["topic"] == "datos_envio"]
    assert slot["any_of"] == ["set_order_slot"] and slot["fields"] == ["direccion"]
    assert "3 turnos" in out.note
