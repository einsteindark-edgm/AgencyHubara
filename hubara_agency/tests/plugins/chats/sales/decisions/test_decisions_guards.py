"""Guardia del motor para las tools (diseño v2 §03, regla 5).

Las tools le piden decisiones al motor por `guards.decide_for_session`: el
proveedor sale del registro de bots de la conversación y los desacuerdos van
a la cola. Importar la guardia NO trae Temporal: las tools no pueden
depender del SDK de Temporal (contrato `tools-no-temporal`)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from src.plugins.chats.agent.sales.decisions import bots
from src.plugins.chats.agent.sales.decisions.disagreements import DisagreementLog
from src.plugins.chats.agent.sales.decisions.guards import decide_for_session
from tests.plugins.chats.sales.decisions.test_decisions_capabilities import Ask, Unsubscribe, _Port

_HUBARA = Path(__file__).resolve().parents[5]


def test_importing_the_guard_does_not_load_temporal() -> None:
    code = (
        "import sys\n"
        "import src.plugins.chats.agent.sales.decisions.guards\n"
        "print(sorted(m for m in sys.modules if m == 'temporalio' or m.startswith('temporalio.')))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=_HUBARA, capture_output=True, text=True, check=True)

    assert out.stdout.strip() == "[]"


async def test_a_tool_gets_the_rule_by_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DECISIONS_BOT", raising=False)

    verdict = await decide_for_session(Unsubscribe(), Ask("no me escriban"), session_id="wa_1", vault_dir=tmp_path)

    assert (verdict.value, verdict.by) == (False, "reglas")


async def test_a_tool_in_shadow_queues_the_disagreement(tmp_path: Path, monkeypatch) -> None:
    from src.sdk import connectorkit

    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    monkeypatch.setenv("SALES_CAPABILITIES_CEILING", "on")
    bots.write_capability_modes(tmp_path, {"juguete": "shadow"})
    port = _Port(0.95)
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: port)

    verdict = await decide_for_session(Unsubscribe(), Ask("no me escriban"), session_id="wa_1", vault_dir=tmp_path)

    assert (verdict.value, verdict.by, verdict.jev) == (False, "reglas", True)
    assert [i["capability"] for i in DisagreementLog(tmp_path).pending()] == ["juguete"]


# ── F5: el texto seguro para el cliente (tools de cierre y de escalación) ──

BRAND_AND_RELAY = "Cada vela lleva un toque humano. Un humano te confirma el pago. Gracias por elegirnos 🤍"
SAMPLES = [
    BRAND_AND_RELAY,
    "Listo, tu pedido quedó registrado 🤍.\nUn colega del equipo te escribe por aquí.",
    "Hola 🤍\nTe cuento",
    "",
    "   ",
    "Soy un asistente virtual. Te ayudo con gusto 🤍",
    "Resumen para el equipo: el cliente pagó. Gracias por tu compra 🤍",
]


async def test_by_default_the_safe_text_is_todays(tmp_path: Path, monkeypatch) -> None:
    from src.plugins.chats.agent.sales.decisions.guards import safe_customer_text
    from src.sdk.textkit import keep_customer_safe_sentences

    monkeypatch.delenv("DECISIONS_BOT", raising=False)

    for text in SAMPLES:
        assert await safe_customer_text(text, session_id="wa_1", vault_dir=tmp_path) == keep_customer_safe_sentences(text)


async def test_with_jev_a_brand_sentence_stays_and_the_relay_falls(tmp_path: Path, monkeypatch) -> None:
    from src.platform.perception.adapters.fake import FakePerceptionAdapter
    from src.plugins.chats.agent.sales.decisions.guards import safe_customer_text
    from src.sdk import connectorkit
    from src.sdk.connectorkit import TypedAnswer

    monkeypatch.setenv("DECISIONS_BOT", "B")
    answers = {"persona.1": 0.03, "persona.2": 0.97, "persona.3": 0.01}
    fake = FakePerceptionAdapter({q: TypedAnswer(id=q, kind="noul", p=p) for q, p in answers.items()})
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: fake)

    out = await safe_customer_text(BRAND_AND_RELAY, session_id="wa_1", vault_dir=tmp_path)

    assert out == "Cada vela lleva un toque humano. Gracias por elegirnos 🤍"


async def test_what_goes_to_jev_hides_this_customers_data(tmp_path: Path, monkeypatch) -> None:
    import json

    from src.platform.perception.adapters.fake import FakePerceptionAdapter
    from src.plugins.chats.agent.sales.decisions.guards import safe_customer_text
    from src.sdk import connectorkit

    monkeypatch.setenv("DECISIONS_BOT", "B")
    seen: list[tuple[str, tuple[str, ...]]] = []

    class _Spy(FakePerceptionAdapter):
        async def ask(self, state, questions, *, timeout_s, redact=()):
            seen.append((state, tuple(redact)))
            return await super().ask(state, questions, timeout_s=timeout_s, redact=redact)

    spy = _Spy({})
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: spy)
    session = tmp_path / "wa_1"
    session.mkdir()
    episode = {"episode_id": "ep_1", "closed_at_ms": None,
               "order_draft": {"slots": {"nombre_recibe": "Laura Gómez", "direccion": "Cra 7 # 12-30"}}}
    (session / "metadata.json").write_text(json.dumps({"episodes": [episode]}), encoding="utf-8")

    await safe_customer_text("Gracias Laura, te llega a Cra 7 # 12-30 🤍", session_id="wa_1", vault_dir=tmp_path)

    [(_, redact)] = seen
    assert {"Laura Gómez", "Laura", "Gómez", "Cra 7 # 12-30"} <= set(redact)


async def test_every_decision_that_goes_to_jev_hides_the_customers_data_by_default(tmp_path: Path, monkeypatch) -> None:
    """Toda capacidad que sale hacia Jev tapa los datos personales del
    borrador de ESA conversación aunque la tool no los pase (las del F3
    mandan líneas de la conversación)."""
    import json

    from src.sdk import connectorkit

    monkeypatch.setenv("DECISIONS_BOT", "B")
    seen: list[tuple[str, ...]] = []

    class _Spy(_Port):
        async def ask(self, state, questions, *, timeout_s, redact=()):
            seen.append(tuple(redact))
            return await super().ask(state, questions, timeout_s=timeout_s, redact=redact)

    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: _Spy(0.95))
    session = tmp_path / "wa_1"
    session.mkdir()
    episode = {"episode_id": "ep_1", "closed_at_ms": None, "order_draft": {"slots": {"nombre_recibe": "Laura Gómez"}}}
    (session / "metadata.json").write_text(json.dumps({"episodes": [episode]}), encoding="utf-8")

    await decide_for_session(Unsubscribe(), Ask("Laura dice que no le escriban"), session_id="wa_1", vault_dir=tmp_path)

    assert seen and {"Laura Gómez", "Laura", "Gómez"} <= set(seen[0])
