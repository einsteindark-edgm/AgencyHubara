"""Preámbulo del modelo (capacidad `preambulo`; diseño v2 §07, familia C).

El saneador de la plataforma quita al principio del texto del LLM las
muletillas de presentación que conoce («Here's my attempt:», «Aquí tienes:»;
bug 579d34e7). Es el único paso del saneador que LEE el texto: ahora lo
decide el motor, oración por oración al principio del texto, con el bot de la
conversación. Los demás pasos (comillas, duplicados, escapes, rayas) siguen
siendo mecánicos y corren igual, en su orden.

* reglas — el meta-prefijo de hoy (`strip_model_preamble`): idéntico a
  `sanitize_llm_text`.
* Jev — «¿Esta oración es una muletilla de presentación del modelo, sin
  contenido para el cliente?», solo para las primeras oraciones (nunca la
  última: el texto nunca queda vacío). Corta solo con p ≥ 0,85 y deja con
  p ≤ 0,15; si duda, decide la regla. Nunca corta a mitad de frase. Piso:
  los prefijos que casi nunca abren una oración legítima («Here's my
  attempt:», «Final answer:») siempre se van.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales.decisions import bots
from src.plugins.chats.agent.sales.decisions.guards import clean_llm_text
from src.sdk.connectorkit import TypedAnswer
from src.sdk.textkit import sanitize_llm_text

SID = "wa_573001234567"
UNKNOWN_PREAMBLE = "Claro, aquí va el mensaje para el cliente:\n\n¡Hola! La Cubo Love cuesta $45.000 🤍"
ANSWER = "¡Hola! La Cubo Love cuesta $45.000 🤍"
PLAN_FOR_THE_CUSTOMER = "Voy a:\n1. Separarte la Cubo Love\n2. Enviártela mañana 🤍"
CORPUS = [
    "",
    "¿Qué aroma te gustaría? 🤍",
    "Aquí tienes:\n¡Hola! ¿Qué aroma te gusta?",
    "Aquí tienes todas nuestras velas. Míralas 🤍",
    'Here\'s my attempt:\n\n"¡Hola de nuevo! 🌿 Quedó pendiente la *Plegaria de Luz* — ¿la dejamos lista? 🤍"',
    UNKNOWN_PREAMBLE,
    PLAN_FOR_THE_CUSTOMER,
    "  Sure!\\n¡Hola de nuevo! 🌿  ",
]


def _noul(qid: str, p: float) -> TypedAnswer:
    return TypedAnswer(id=qid, kind="noul", p=p)


def _answers(**ps: float) -> FakePerceptionAdapter:
    """Jev falso: `p` de «¿es una muletilla?» por oración (`n1=0.9`…)."""
    return FakePerceptionAdapter({f"preambulo.{k[1:]}": _noul(f"preambulo.{k[1:]}", p) for k, p in ps.items()})


@pytest.fixture
def oracle(monkeypatch):
    from src.sdk import connectorkit

    holder = {"fake": FakePerceptionAdapter({})}

    def _get(_oracle: str):
        return holder["fake"]

    _get.cache_clear = lambda: None
    monkeypatch.setattr(connectorkit, "get_perception_port", _get)
    return holder


@pytest.fixture
def jev(monkeypatch):
    monkeypatch.setenv("DECISIONS_BOT", "B")


def _asked(fake: FakePerceptionAdapter) -> list[str]:
    return [qid for _state, ids in fake.calls for qid in ids]


@pytest.mark.parametrize("raw", CORPUS, ids=lambda t: (t[:20] or "vacío").replace(" ", "_"))
async def test_with_rules_the_text_is_what_the_sanitizer_gives_today(tmp_path: Path, oracle, raw: str) -> None:
    assert await clean_llm_text(raw, session_id=SID, vault_dir=tmp_path) == sanitize_llm_text(raw).text
    assert oracle["fake"].calls == []


async def test_jev_cuts_a_preamble_the_rule_does_not_know(tmp_path: Path, oracle, jev) -> None:
    oracle["fake"] = _answers(n1=0.95, n2=0.03)

    assert await clean_llm_text(UNKNOWN_PREAMBLE, session_id=SID, vault_dir=tmp_path) == ANSWER
    assert sanitize_llm_text(UNKNOWN_PREAMBLE).text == UNKNOWN_PREAMBLE  # la regla de hoy no la conoce
    [(state, ids)] = oracle["fake"].calls
    assert ids == ("preambulo.1", "preambulo.2")
    assert "[1] Claro, aquí va el mensaje para el cliente:" in state


async def test_jev_keeps_a_line_for_the_customer_the_rule_cuts(tmp_path: Path, oracle, jev) -> None:
    oracle["fake"] = _answers(n1=0.05)

    assert await clean_llm_text(PLAN_FOR_THE_CUSTOMER, session_id=SID, vault_dir=tmp_path) == PLAN_FOR_THE_CUSTOMER
    assert sanitize_llm_text(PLAN_FOR_THE_CUSTOMER).text.startswith("1. Separarte")  # la regla le quitaba el «Voy a:»


async def test_the_strong_prefixes_always_go(tmp_path: Path, oracle, jev) -> None:
    oracle["fake"] = _answers(n1=0.02)

    out = await clean_llm_text("Here's my attempt:\n\n¡Hola! ¿Qué aroma te gusta?", session_id=SID, vault_dir=tmp_path)

    assert out == "¡Hola! ¿Qué aroma te gusta?"


async def test_when_jev_doubts_the_rule_decides(tmp_path: Path, oracle, jev) -> None:
    oracle["fake"] = _answers(n1=0.5, n2=0.05)

    assert await clean_llm_text(UNKNOWN_PREAMBLE, session_id=SID, vault_dir=tmp_path) == UNKNOWN_PREAMBLE


async def test_jev_never_cuts_mid_sentence(tmp_path: Path, oracle, jev) -> None:
    text = "Te cuento: la Cubo Love cuesta $45.000 🤍"
    oracle["fake"] = _answers(n1=0.95)

    assert await clean_llm_text(text, session_id=SID, vault_dir=tmp_path) == text


async def test_a_single_sentence_is_never_asked(tmp_path: Path, oracle, jev) -> None:
    assert await clean_llm_text("Te dejo el catálogo 👇", session_id=SID, vault_dir=tmp_path) == "Te dejo el catálogo 👇"
    assert oracle["fake"].calls == []


async def test_only_the_leading_sentences_are_asked(tmp_path: Path, oracle, jev) -> None:
    text = "Claro:\nAquí va:\nMensaje:\n¡Hola! Tenemos lavanda. También coco. ¿Cuál te gusta?"
    oracle["fake"] = _answers(n1=0.97, n2=0.97, n3=0.97)

    assert await clean_llm_text(text, session_id=SID, vault_dir=tmp_path) == "¡Hola! Tenemos lavanda. También coco. ¿Cuál te gusta?"
    assert _asked(oracle["fake"]) == ["preambulo.1", "preambulo.2", "preambulo.3"]


async def test_shadow_keeps_the_rule_and_queues_the_disagreement(tmp_path: Path, oracle, monkeypatch) -> None:
    from src.plugins.chats.agent.sales.decisions.disagreements import DisagreementLog

    monkeypatch.setenv("SALES_CAPABILITIES_CEILING", "on")
    bots.write_capability_modes(tmp_path, {"preambulo": "shadow"})
    oracle["fake"] = _answers(n1=0.95, n2=0.03)

    assert await clean_llm_text(UNKNOWN_PREAMBLE, session_id=SID, vault_dir=tmp_path) == UNKNOWN_PREAMBLE
    # En la cola va la muletilla que cada uno quita ("" = ninguna), no el mensaje.
    [item] = DisagreementLog(tmp_path).pending()
    assert (item["capability"], item["rule"], item["jev"]) == ("preambulo", "", "Claro, aquí va el mensaje para el cliente:")


async def test_jev_failing_falls_back_to_the_rule(tmp_path: Path, oracle, jev) -> None:
    oracle["fake"] = FakePerceptionAdapter({}, error="http_503")

    out = await clean_llm_text("Aquí tienes:\n¡Hola! ¿Qué aroma te gusta?", session_id=SID, vault_dir=tmp_path)

    assert out == "¡Hola! ¿Qué aroma te gusta?"


# ── En las tools y el flush: los sitios del saneador fuera del workflow ──────


class _Jev(FakePerceptionAdapter):
    """Jev falso: «¿es una muletilla?» según `preamble`; lo demás es un
    mensaje para el cliente que no delata nada (persona, destinatario)."""

    def __init__(self, preamble: dict[str, float]) -> None:
        super().__init__({})
        self.preamble = preamble

    async def ask(self, state, questions, *, timeout_s, redact=()):
        from src.sdk.connectorkit import PerceptionResult

        self.calls.append((state, tuple(q.id for q in questions)))
        answers = []
        for q in questions:
            if q.id in self.preamble:
                answers.append(_noul(q.id, self.preamble[q.id]))
            elif q.kind == "choice":
                pick = "mensaje_al_cliente"
                answers.append(TypedAnswer(id=q.id, kind="choice", choice=pick, probs=((pick, 0.97),), confidence=0.97))
            else:
                answers.append(_noul(q.id, 0.02))
        return PerceptionResult(ok=True, answers=tuple(answers), provider="fake", model="fake")


@pytest.fixture
def cuts_the_first(monkeypatch):
    """Bot con Jev: la primera oración es muletilla; la segunda, no."""
    from src.sdk import connectorkit

    fake = _Jev({"preambulo.1": 0.95, "preambulo.2": 0.03})
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: fake)
    monkeypatch.setenv("DECISIONS_BOT", "B")
    return fake


@pytest.fixture
def today(monkeypatch):
    monkeypatch.delenv("DECISIONS_BOT", raising=False)


def _ctx():
    from exoclaw.agent.tools import ToolContext

    return ToolContext(session_key=SID, channel="whatsapp", chat_id=SID)


def _open_episode(vault: Path) -> None:
    import json

    session = vault / SID
    session.mkdir(parents=True, exist_ok=True)
    episode = {"episode_id": "ep_001", "started_at_ms": 1_790_000_000_000, "closed_at_ms": None}
    (session / "metadata.json").write_text(json.dumps({"active_route": "ventas", "episodes": [episode]}), encoding="utf-8")


async def _reply(tmp_path: Path, text: str) -> dict:
    import json

    from src.plugins.chats.agent.sales.tools.reply import SendReplyTool

    return json.loads(await SendReplyTool(workspace=str(tmp_path), vault_dir=tmp_path).execute_with_context(_ctx(), text=text))


async def test_send_reply_today_keeps_a_preamble_the_rule_does_not_know(tmp_path: Path, today) -> None:
    assert (await _reply(tmp_path, UNKNOWN_PREAMBLE))["reply"]["text"] == UNKNOWN_PREAMBLE


async def test_with_jev_send_reply_cuts_the_preamble(tmp_path: Path, cuts_the_first) -> None:
    assert (await _reply(tmp_path, UNKNOWN_PREAMBLE))["reply"]["text"] == ANSWER
    assert any("preambulo.1" in ids for _state, ids in cuts_the_first.calls)


async def test_with_jev_the_closing_tag_farewell_loses_the_preamble(tmp_path: Path, cuts_the_first) -> None:
    import json

    from src.plugins.chats.agent.sales.tools.tags import ManageConversationTagTool

    _open_episode(tmp_path)
    farewell = "Claro, aquí va la despedida para el cliente:\n\n¡Gracias por escribirnos! Aquí estamos cuando quieras 🤍"
    tool = ManageConversationTagTool(workspace=str(tmp_path), vault_dir=tmp_path)

    out = json.loads(await tool.execute_with_context(_ctx(), tag="RECHAZO", motivo="ya consiguió", customer_message=farewell))

    assert out["tag_closure"]["customer_message"] == "¡Gracias por escribirnos! Aquí estamos cuando quieras 🤍"


async def test_with_jev_the_handoff_farewell_loses_the_preamble(tmp_path: Path, cuts_the_first) -> None:
    import json

    from src.platform.tools.escalation import EscalateToHumanTool
    from src.plugins.chats.agent.sales.tools.escalation import guarded_escalation_tool

    _open_episode(tmp_path)
    farewell = "Eso te lo coordino con un colega del equipo, te responde en este mismo chat 🤍"
    tool = guarded_escalation_tool(EscalateToHumanTool)(workspace=str(tmp_path), vault_dir=tmp_path)

    out = json.loads(
        await tool.execute_with_context(
            _ctx(), reason_category="BULK_ORDER", summary="pide 100 unidades",
            customer_message=f"Claro, aquí va mi respuesta:\n\n{farewell}",
        )
    )

    assert out["customer_message"] == farewell


async def test_with_jev_the_intent_text_loses_the_preamble(cuts_the_first) -> None:
    from src.plugins.chats.agent.sales.activities.flush_ui_intents import _sanitize_intent_client_text

    out = await _sanitize_intent_client_text(
        "products", {"intro_text": "Aquí va el mensaje:\n¡Mira nuestras velas de temporada! 🤍"}, session_id=SID
    )

    assert out["intro_text"] == "¡Mira nuestras velas de temporada! 🤍"


async def test_the_intent_text_today_keeps_it(today) -> None:
    from src.plugins.chats.agent.sales.activities.flush_ui_intents import _sanitize_intent_client_text

    intro = "Aquí va el mensaje:\n¡Mira nuestras velas de temporada! 🤍"

    assert (await _sanitize_intent_client_text("products", {"intro_text": intro}, session_id=SID))["intro_text"] == intro
