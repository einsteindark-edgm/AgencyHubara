"""Egreso del turno de ventas (diseño v2 §07 familia C y §08; fases F4/F5).

Lo que el LLM escribió pasa por cuatro capacidades del motor antes de grabarse
en su historial y de salir al cliente:

* destinatario — «¿Qué es este texto?» (mensaje al cliente, razonamiento,
  reporte interno, acuse al sistema o deliberación). Regla de hoy:
  `looks_like_admin_leak` con el set extendido.
* rescate — los párrafos que sí son para el cliente. Regla de hoy:
  `salvage_customer_text`. Si no queda nada, una sola reacción: el silencio.
* portavelas — en un pedido sin portavelas, las oraciones que le afirman algo
  del portavelas. Regla de hoy: `strip_portavelas_notice` y, si no queda
  nada, la despedida aprobada.
* saludo — «¿alguno de estos mensajes ya saluda?». Regla de hoy:
  `should_send_first_contact_greeting` con las mismas entradas que el V1.

Con `reglas` (así nace el bot B0) el resultado tiene que ser EXACTAMENTE lo que
decide el workflow V1 hoy: la transcripción literal de sus pasos (el rescate
del turno compartido antes de grabar, el saludo, el portavelas y la guarda de
texto administrativo) es el oráculo de estos tests.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales.decisions import egress
from src.plugins.chats.agent.sales.decisions.contracts import EgressInput, EgressOutput
from src.plugins.chats.agent.sales.decisions.disagreements import DisagreementLog
from src.plugins.chats.agent.sales.first_contact_greeting import should_send_first_contact_greeting
from src.sdk.agentkit import (
    is_no_message_abstention,
    looks_like_admin_leak,
    salvage_customer_text,
    strip_portavelas_notice,
)
from src.sdk.connectorkit import TypedAnswer

SID = "wa_573001234567"

# ── El oráculo: lo que el V1 decide hoy (sales_session.py + run_agent_turn) ──


def v1_egress(
    text: str,
    *,
    first_contact: bool = False,
    tools_used: tuple[str, ...] = (),
    outbound_tool_texts: tuple[str, ...] = (),
    order_registered: bool = False,
    portavelas_included: bool | None = None,
    admin_turn: bool = False,
) -> dict:
    """Transcripción literal de los pasos del V1 sobre el texto final de un
    turno nuevo (todos los `workflow.patched` en True)."""
    from src.plugins.chats.agent.sales.workflows.sales_session import _ORDER_REGISTERED_FALLBACK_FAREWELL

    # run_agent_turn(salvage_leaked_text=True): rescate ANTES de grabar.
    final = text
    salvaged_leak = False
    if (
        not admin_turn
        and final
        and not is_no_message_abstention(final)
        and looks_like_admin_leak(final, extended=True)
    ):
        salvaged = salvage_customer_text(final, extended=True)
        if salvaged:
            final, salvaged_leak = salvaged, True
    llm_text = final
    # Workflow: el saludo de primer contacto (la parte que lee texto).
    greeting = should_send_first_contact_greeting(
        first_contact=first_contact,
        tools_used=list(tools_used),
        client_texts=[*outbound_tool_texts, final or ""],
    )
    guards: list[dict] = []
    # Guarda del portavelas.
    if order_registered and not portavelas_included and final and "portavela" in final.lower():
        stripped = strip_portavelas_notice(final)
        guards.append({"name": "portavelas_notice_guard", "before": final, "after": stripped or _ORDER_REGISTERED_FALLBACK_FAREWELL})
        final = stripped or _ORDER_REGISTERED_FALLBACK_FAREWELL
    # Guarda de texto administrativo + rescate.
    leak_blocked = bool(final) and looks_like_admin_leak(final, extended=True)
    if leak_blocked:
        guards.append({"name": "admin_text_guard", "before": final, "after": ""})
        salvaged = salvage_customer_text(final, extended=True)
        if salvaged:
            guards.append({"name": "admin_text_salvaged", "before": final, "after": salvaged})
            final = salvaged
            leak_blocked = False
    return {
        "text": "" if (admin_turn or leak_blocked or not final) else final,
        "llm_text": llm_text,
        "final_text": final,
        "rescued_before_record": salvaged_leak,
        "blocked": leak_blocked,
        "greeting_needed": greeting,
        "guards": guards,
    }


def _inp(text: str, **kw) -> EgressInput:
    return EgressInput(
        session_id=SID,
        final_text=text,
        first_contact=kw.get("first_contact", False),
        tools_used=list(kw.get("tools_used", ())),
        outbound_tool_texts=list(kw.get("outbound_tool_texts", ())),
        order_registered=kw.get("order_registered", False),
        portavelas_included=kw.get("portavelas_included"),
        admin_turn=kw.get("admin_turn", False),
    )


def _rules(_capability: str) -> str:
    return "reglas"


async def _egress_with(provider: str, inp: EgressInput, **kw) -> EgressOutput:
    return await egress.decide_egress(inp, provider_of=lambda _c: provider, profile_id="jev-v1", **kw)


# Textos reales o con la forma de los incidentes que originaron cada regla.
PLAIN = "¿Qué aroma te gustaría? 🤍"
DELIBERATION = "El cliente dice que le gusta. Le respondo con el precio.\n\n¡Qué bueno! La Cubo Love cuesta $45.000 🤍"
TAGGED = "La conversación quedó etiquetada como `INTERESADO` en el sistema."
HANDOFF_ACK = "Listo, la conversación quedó en manos del equipo humano."
TAG_ACK = "Etiqueta registrada."
COUPON_FALSE_POSITIVE = "Usa el código VELAS_10 al pagar y te queda en $40.500 🤍"
PORTAVELAS_FAREWELL = (
    "¡Listo! Tu pedido quedó registrado 🤍 Al finalizar el pago del pedido se escogen los colores "
    "del portavelas, según disponibilidad. Gracias por elegir a Hubara."
)
PORTAVELAS_ONLY = "Los colores del portavelas se escogen al finalizar el pago."
PORTAVELAS_AND_LEAK = "Tu pedido quedó registrado. El cliente eligió portavelas dorado."
GREETING = "¡Buenas tardes! Bienvenido a *Hubara* 🤍 Te dejo el catálogo."
NO_GREETING = "Te dejo el catálogo 👇"

CORPUS = [
    "",
    PLAIN,
    DELIBERATION,
    TAGGED,
    HANDOFF_ACK,
    TAG_ACK,
    COUPON_FALSE_POSITIVE,
    "NO_MESSAGE",
    PORTAVELAS_FAREWELL,
    PORTAVELAS_ONLY,
    PORTAVELAS_AND_LEAK,
    GREETING,
    NO_GREETING,
    f"{DELIBERATION}\n\n{PORTAVELAS_ONLY}",
]

CONTEXTS = [
    {},
    {"first_contact": True, "tools_used": ("present_products",)},
    {"first_contact": True, "tools_used": ("present_products",), "outbound_tool_texts": ("¡Hola! Mira nuestras velas",)},
    {"first_contact": True, "tools_used": ("search_products",)},
    {"order_registered": True, "portavelas_included": False, "tools_used": ("register_order",)},
    {"order_registered": True, "portavelas_included": True, "tools_used": ("register_order",)},
    {"admin_turn": True},
    {"admin_turn": True, "order_registered": True, "portavelas_included": False},
]


@pytest.fixture(autouse=True)
def _no_real_oracle(monkeypatch):
    """Ningún test de acá sale a la red: el oráculo es el falso."""
    from src.sdk import connectorkit

    holder = {"fake": FakePerceptionAdapter({})}

    def _get(_oracle: str):
        return holder["fake"]

    _get.cache_clear = lambda: None
    monkeypatch.setattr(connectorkit, "get_perception_port", _get)
    return holder


@pytest.mark.parametrize("context", CONTEXTS, ids=lambda c: ",".join(f"{k}" for k in c) or "normal")
@pytest.mark.parametrize("text", CORPUS, ids=lambda t: (t[:24] or "vacío").replace(" ", "_"))
async def test_with_rules_the_egress_is_exactly_what_v1_decides(text: str, context: dict, _no_real_oracle) -> None:
    out = await egress.decide_egress(_inp(text, **context), provider_of=_rules, profile_id="jev-v1")
    expected = v1_egress(text, **context)

    got = {k: getattr(out, k) for k in expected}
    assert got == expected
    assert out.portavelas is any(g["name"] == "portavelas_notice_guard" for g in expected["guards"])
    assert out.salvaged is (expected["rescued_before_record"] or any(g["name"] == "admin_text_salvaged" for g in expected["guards"]))
    assert all(v["by"] == "reglas" for v in out.verdicts)
    assert _no_real_oracle["fake"].calls == [], "con reglas Jev no se consulta"


def test_the_approved_farewell_is_the_one_v1_sends() -> None:
    from src.plugins.chats.agent.sales.workflows.sales_session import _ORDER_REGISTERED_FALLBACK_FAREWELL

    assert egress.ORDER_REGISTERED_FALLBACK_FAREWELL == _ORDER_REGISTERED_FALLBACK_FAREWELL


def test_the_egress_capabilities_are_the_four_of_the_design() -> None:
    assert set(egress.EGRESS_CAPABILITIES) == {"destinatario", "rescate", "portavelas", "saludo"}


async def test_the_output_travels_as_json() -> None:
    out = await egress.decide_egress(_inp(DELIBERATION), provider_of=_rules, profile_id="jev-v1")

    payload = dataclasses.asdict(out)
    assert json.loads(json.dumps(payload)) == payload
    assert {v["capability"] for v in out.verdicts} == {"destinatario", "rescate", "saludo"}


# ── Jev (sombra / jev): cada capacidad con su pregunta y la regla de respaldo ──


def _choice(qid: str, pick: str, p: float) -> TypedAnswer:
    return TypedAnswer(id=qid, kind="choice", choice=pick, probs=((pick, p),), confidence=p)


def _noul(qid: str, p: float) -> TypedAnswer:
    return TypedAnswer(id=qid, kind="noul", p=p)


class _Reader:
    """Jev falso que LEE: al «¿qué es?» del texto (o del párrafo [i]) contesta
    según lo que dice ese texto (`labels`: fragmento → (opción, p)); lo que no
    tenga ningún fragmento es un mensaje al cliente."""

    def __init__(self, labels: dict[str, tuple[str, float]]) -> None:
        self.labels = labels
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def _judge(self, text: str) -> tuple[str, float]:
        return next((v for k, v in self.labels.items() if k in text), ("mensaje_al_cliente", 0.97))

    async def ask(self, state, questions, *, timeout_s, redact=()):
        from src.sdk.connectorkit import PerceptionResult

        self.calls.append((state, tuple(q.id for q in questions)))
        answers = []
        for q in questions:
            if q.id == "egreso.destinatario":
                target = state.split("\n", 1)[1]
            else:
                index = q.id.rsplit(".", 1)[1]
                target = next(line for line in state.splitlines() if line.startswith(f"[{index}] "))
            answers.append(_choice(q.id, *self._judge(target)))
        return PerceptionResult(ok=True, answers=tuple(answers), provider="fake", model="typesafe/jev-1.13-x")


async def test_jev_lets_a_coupon_code_reach_the_customer(_no_real_oracle) -> None:
    """Falso positivo de hoy (diseño §06): «Usa el código VELAS_10 al pagar»
    parecía un token interno y el cliente se quedaba sin respuesta."""
    _no_real_oracle["fake"] = FakePerceptionAdapter({"egreso.destinatario": _choice("egreso.destinatario", "mensaje_al_cliente", 0.97)})

    out = await _egress_with("jev", _inp(COUPON_FALSE_POSITIVE))

    assert (out.text, out.blocked) == (COUPON_FALSE_POSITIVE, False)
    [verdict] = [v for v in out.verdicts if v["capability"] == "destinatario"]
    assert (verdict["by"], verdict["rule"], verdict["jev"]) == ("jev", True, False)


async def test_jev_blocks_a_report_the_rule_does_not_see(_no_real_oracle) -> None:
    report = "Ya quedó todo registrado en el pedido, solo falta que confirme el pago."
    _no_real_oracle["fake"] = FakePerceptionAdapter({
        "egreso.destinatario": _choice("egreso.destinatario", "reporte_interno", 0.93),
        "egreso.rescate.1": _choice("egreso.rescate.1", "reporte_interno", 0.93),
    })

    out = await _egress_with("jev", _inp(report))

    assert (out.text, out.blocked) == ("", True)
    assert [g["name"] for g in out.guards] == ["admin_text_guard"]


async def test_jev_rescues_paragraph_by_paragraph(_no_real_oracle) -> None:
    text = "Voy a revisar si hay stock y luego le contesto.\n\n¡Sí la tenemos! ¿Te la separo? 🤍"
    reader = _Reader({"Voy a revisar": ("razonamiento", 0.91)})
    _no_real_oracle["fake"] = reader

    out = await _egress_with("jev", _inp(text))

    assert out.text == "¡Sí la tenemos! ¿Te la separo? 🤍"
    assert (out.rescued_before_record, out.salvaged, out.llm_text) == (True, True, out.text)
    # Lo que quedó ya lo juzgó Jev párrafo por párrafo: no se le vuelve a preguntar.
    assert [ids for _s, ids in reader.calls] == [("egreso.destinatario",), ("egreso.rescate.1", "egreso.rescate.2")]


async def test_when_jev_doubts_a_paragraph_the_rule_rescues(_no_real_oracle) -> None:
    _no_real_oracle["fake"] = _Reader({"El cliente dice": ("razonamiento", 0.55)})

    out = await _egress_with("jev", _inp(DELIBERATION))

    assert out.text == salvage_customer_text(DELIBERATION, extended=True)
    [rescate] = [v for v in out.verdicts if v["capability"] == "rescate"]
    assert (rescate["by"], rescate["reason"]) == ("respaldo", "duda")


async def test_jev_keeps_a_true_notice_and_strips_the_false_promise(_no_real_oracle) -> None:
    """«Tu pedido no incluye portavelas.» es cierto y hay que decirlo (§06): la
    regla de hoy lo borraba porque dice «portavela»."""
    text = "Tu pedido quedó registrado 🤍 Tu pedido no incluye portavelas. Los colores del portavelas se escogen al pagar."
    _no_real_oracle["fake"] = FakePerceptionAdapter({
        "egreso.portavelas.1": _noul("egreso.portavelas.1", 0.02),
        "egreso.portavelas.2": _noul("egreso.portavelas.2", 0.05),
        "egreso.portavelas.3": _noul("egreso.portavelas.3", 0.97),
    })

    out = await _egress_with("jev", _inp(text, order_registered=True, portavelas_included=False))

    assert out.text == "Tu pedido quedó registrado 🤍 Tu pedido no incluye portavelas."
    assert out.portavelas is True
    assert [g["name"] for g in out.guards] == ["portavelas_notice_guard"]
    assert strip_portavelas_notice(text) == "Tu pedido quedó registrado 🤍"  # la regla de hoy borraba el aviso cierto


async def test_jev_strips_a_portavelas_promise_without_the_word(_no_real_oracle) -> None:
    text = "Tu pedido quedó registrado 🤍 El soporte dorado de la vela te lo escoges al pagar."
    _no_real_oracle["fake"] = FakePerceptionAdapter({
        "egreso.portavelas.1": _noul("egreso.portavelas.1", 0.03),
        "egreso.portavelas.2": _noul("egreso.portavelas.2", 0.95),
    })

    out = await _egress_with("jev", _inp(text, order_registered=True, portavelas_included=False))

    assert out.text == "Tu pedido quedó registrado 🤍"


async def test_nothing_left_after_the_portavelas_is_the_approved_farewell(_no_real_oracle) -> None:
    _no_real_oracle["fake"] = FakePerceptionAdapter({"egreso.portavelas.1": _noul("egreso.portavelas.1", 0.97)})

    out = await _egress_with("jev", _inp(PORTAVELAS_ONLY, order_registered=True, portavelas_included=False))

    assert out.text == egress.ORDER_REGISTERED_FALLBACK_FAREWELL


async def test_an_order_with_portavelas_never_asks_about_it(_no_real_oracle) -> None:
    out = await _egress_with("jev", _inp(PORTAVELAS_FAREWELL, order_registered=True, portavelas_included=True))

    assert out.text == PORTAVELAS_FAREWELL and out.portavelas is False
    assert not any(q.startswith("egreso.portavelas") for _s, ids in _no_real_oracle["fake"].calls for q in ids)


async def test_jev_sees_a_greeting_the_regex_misses(_no_real_oracle) -> None:
    _no_real_oracle["fake"] = FakePerceptionAdapter({"egreso.saludo": _noul("egreso.saludo", 0.96)})

    out = await _egress_with(
        "jev",
        _inp("", first_contact=True, tools_used=("present_products",), outbound_tool_texts=("¡Qué gusto tenerte por acá! Mira:",)),
    )

    assert out.greeting_needed is False
    assert should_send_first_contact_greeting(
        first_contact=True, tools_used=["present_products"], client_texts=["¡Qué gusto tenerte por acá! Mira:", ""]
    ) is True  # la regla de hoy mandaba otra bienvenida


async def test_no_outbound_tool_means_no_question_about_the_greeting(_no_real_oracle) -> None:
    out = await _egress_with("jev", _inp(NO_GREETING, first_contact=True, tools_used=("search_products",)))

    assert out.greeting_needed is False
    assert not any("egreso.saludo" in ids for _s, ids in _no_real_oracle["fake"].calls)


async def test_the_sentinel_and_admin_turns_never_go_to_jev(_no_real_oracle) -> None:
    """`NO_MESSAGE` es protocolo y el texto de un turno administrativo nunca
    sale: no hay nada que preguntarle a Jev; decide la regla (misma traza)."""
    sentinel = await _egress_with("jev", _inp("NO_MESSAGE"))
    admin = await _egress_with("jev", _inp(TAGGED, admin_turn=True))

    assert _no_real_oracle["fake"].calls == []
    assert sentinel.text == "" and admin.text == ""
    assert [g["name"] for g in admin.guards] == ["admin_text_guard"]


async def test_sombra_keeps_the_rule_and_queues_the_disagreement(_no_real_oracle, tmp_path: Path) -> None:
    _no_real_oracle["fake"] = FakePerceptionAdapter({"egreso.destinatario": _choice("egreso.destinatario", "mensaje_al_cliente", 0.97)})
    log = DisagreementLog(tmp_path)

    out = await _egress_with("sombra", _inp(COUPON_FALSE_POSITIVE), disagreements=log)

    assert (out.text, out.blocked) == ("", True)  # decide la regla de hoy
    items = {i["capability"]: i for i in log.pending()}
    assert set(items) == {"destinatario", "rescate"}  # un desacuerdo por pregunta, no por paso
    assert (items["destinatario"]["rule"], items["destinatario"]["jev"]) == (True, False)
    assert (items["rescate"]["rule"], items["rescate"]["jev"]) == ("", COUPON_FALSE_POSITIVE)
    assert items["destinatario"]["session_id"] == SID


async def test_jev_failing_falls_back_to_the_rule(_no_real_oracle) -> None:
    _no_real_oracle["fake"] = FakePerceptionAdapter({}, error="http_503")

    out = await _egress_with("jev", _inp(DELIBERATION))
    expected = v1_egress(DELIBERATION)

    assert out.text == expected["text"]
    assert {v["by"] for v in out.verdicts} == {"respaldo"}


async def test_the_same_text_is_judged_once(_no_real_oracle) -> None:
    """El V1 mira el texto dos veces (antes de grabar y antes de enviar). Si no
    cambió en el medio, Jev lo juzga una sola vez."""
    _no_real_oracle["fake"] = FakePerceptionAdapter({
        "egreso.destinatario": _choice("egreso.destinatario", "reporte_interno", 0.93),
        "egreso.rescate.1": _choice("egreso.rescate.1", "reporte_interno", 0.93),
    })

    out = await _egress_with("jev", _inp(TAGGED))

    asked = [ids for _s, ids in _no_real_oracle["fake"].calls]
    assert asked.count(("egreso.destinatario",)) == 1
    assert [v["capability"] for v in out.verdicts].count("destinatario") == 1
    assert out.blocked is True


# ── La activity `decide_egress`: el bot de la conversación decide el proveedor ──


@pytest.fixture
def vault(tmp_path: Path, monkeypatch) -> Path:
    from src.plugins.chats.agent.sales.decisions import egress_activities

    monkeypatch.setattr(egress_activities, "_vault_dir", lambda: tmp_path)
    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    return tmp_path


async def test_the_activity_uses_todays_rules_for_todays_bot(vault: Path, _no_real_oracle) -> None:
    from temporalio.testing import ActivityEnvironment

    from src.plugins.chats.agent.sales.decisions.egress_activities import decide_egress_activity

    out = await ActivityEnvironment().run(decide_egress_activity, _inp(DELIBERATION))

    assert out.text == v1_egress(DELIBERATION)["text"]
    assert out.verdicts and all(v["by"] == "reglas" for v in out.verdicts)
    assert _no_real_oracle["fake"].calls == [] and out.error is None


async def test_the_activity_asks_jev_for_a_bot_with_jev(vault: Path, _no_real_oracle, monkeypatch) -> None:
    from temporalio.testing import ActivityEnvironment

    from src.plugins.chats.agent.sales.decisions.egress_activities import decide_egress_activity

    monkeypatch.setenv("DECISIONS_BOT", "B")
    _no_real_oracle["fake"] = FakePerceptionAdapter({"egreso.destinatario": _choice("egreso.destinatario", "mensaje_al_cliente", 0.97)})

    out = await ActivityEnvironment().run(decide_egress_activity, _inp(COUPON_FALSE_POSITIVE))

    assert out.text == COUPON_FALSE_POSITIVE
    assert any(v["capability"] == "destinatario" and v["by"] == "jev" for v in out.verdicts)


async def test_the_activity_never_fails_it_falls_back_to_the_rules(vault: Path, _no_real_oracle, monkeypatch) -> None:
    from temporalio.testing import ActivityEnvironment

    from src.plugins.chats.agent.sales.decisions import egress_activities
    from src.plugins.chats.agent.sales.decisions.egress_activities import decide_egress_activity

    def broken(*_a, **_k):
        raise RuntimeError("registro de bots roto")

    monkeypatch.setattr(egress_activities, "bot_for_session", broken)

    out = await ActivityEnvironment().run(decide_egress_activity, _inp(DELIBERATION))

    assert out.text == v1_egress(DELIBERATION)["text"]
    assert out.error and "registro de bots roto" in out.error
