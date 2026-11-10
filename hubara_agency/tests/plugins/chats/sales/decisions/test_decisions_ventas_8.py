"""`ventas-8`: es `ventas-7@7` con el saludo v2, tres decisiones nuevas y la
puerta de afirmaciones, y nada más.

Informe de huecos de producción del 2026-11-03 (bot V2, todo el tráfico):

* saludo v2 (APE-01, APE-04): la pregunta de `saludo` aceptaba un «hola» como
  saludo y la burbuja con la hora y la marca no salía; y al cliente que vuelve
  el modelo le escribía la bienvenida de quien llega por primera vez. El primer
  contacto de verdad es «la tienda nunca escribió» (`hours_since_store`, la
  cuenta de la vista `reply_gap`); fuera de él, `saludo_repetido` quita la
  bienvenida del texto.
* `despedida` (CIE-05): las frases que la tienda no dice («ahí te dejé el
  formulario», «gracias por tu compra»…) se quitan en el egreso; si no queda
  nada y el pedido quedó registrado, la despedida aprobada del dominio.
* `repregunta` (VAR-06, ENV-04): el dato que el pedido ya tiene, en `send_reply`
  (la prueba de la tool: `tests/plugins/chats/sales/test_send_reply_known_data.py`).
* `afirmacion_previa` (CON-03, POS-01, DES-10, ENV-05, EST-05): la puerta de
  afirmaciones (#467) entra en ventas-8, editado en el lugar ya vivo (decisión
  del operador, 2026-11-04: debió salir con él); su frase de tiempos aprobada
  va en `domain.yaml` (`delivery_time_phrases`). Sus pruebas:
  `test_decisions_assertion_gate.py`.

Textos sintéticos con la forma de los casos (nunca el de un cliente). En un clon
de forge `ventas-8` no viaja (experimento de esta tienda): se salta.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales.decisions import egress, registry
from src.plugins.chats.agent.sales.decisions.capabilities.texto import KnownOrderData
from src.plugins.chats.agent.sales.decisions.contracts import EgressInput
from src.plugins.chats.agent.sales.decisions.egress import GreetingCheck, OutgoingText
from src.plugins.chats.agent.sales.decisions.guards import decide_for_session, known_order_data
from src.plugins.chats.shared.store_pack import BUNDLES_DIR, CATALOG_PATH
from src.sdk.connectorkit import TypedAnswer

V7 = BUNDLES_DIR / "ventas-7"
V8 = BUNDLES_DIR / "ventas-8"
IN_FORGE_CLONE = not (Path(__file__).resolve().parents[6] / "forge").is_dir()
SID = "wa_573001111111"
NEW = ("saludo_repetido", "despedida", "repregunta")
#: La puerta de afirmaciones (#467), en ventas-8 desde el 2026-11-04.
GATE = "afirmacion_previa"

#: Con la forma de los casos del informe (sintéticos).
REGREETING = (
    "Buenos días. Bienvenido a *Hubara*, velas artesanales hechas a base de cera de palma, a mano en Colombia. "
    "¿Buscas algo para regalar o para tu casa?"
)
FORM_FILLER = "Con gusto, ahí te dejé el formulario. Cuando lo llenes, seguimos con tu pedido 🤍"
REASKS_QUANTITY = "La caja trae 12 velitas. ¿Te sirve la caja completa o prefieres ajustar la cantidad?"


def _need_v8() -> None:
    if IN_FORGE_CLONE:
        pytest.skip("clon de forge: los paquetes de prueba de esta tienda no viajan")
    assert (V8 / "bundle.yaml").is_file(), "falta el paquete ventas-8"


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _ventas_8(monkeypatch):
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-8")
    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    registry.reset()
    yield
    registry.reset()


@pytest.fixture
def oracle(monkeypatch):
    """Ninguna prueba sale a la red: Jev es el falso."""
    from src.sdk import connectorkit

    holder = {"fake": FakePerceptionAdapter({})}

    def _get(_oracle: str):
        return holder["fake"]

    _get.cache_clear = lambda: None
    monkeypatch.setattr(connectorkit, "get_perception_port", _get)
    return holder


@pytest.fixture
def jev(monkeypatch):
    """El bot nuevo: Jev decide cada capacidad."""
    monkeypatch.setenv("DECISIONS_BOT", "B")


def _answers(**ps: float) -> FakePerceptionAdapter:
    return FakePerceptionAdapter({q: TypedAnswer(id=q, kind="noul", p=p) for q, p in ps.items()})


def _asked(oracle) -> list[str]:
    return [q for _state, ids in oracle["fake"].calls for q in ids]


async def _decide(name: str, inp: object, vault: Path):
    cap = registry.capability_if_present(name)
    assert cap is not None
    return await decide_for_session(cap, inp, session_id=SID, vault_dir=vault)


# ── El paquete: ventas-7 + esto, y nada más ─────────────────────────────────


def test_ventas_8_is_ventas_7_with_the_greeting_v2_three_new_decisions_and_the_gate() -> None:
    _need_v8()
    files7 = {p.relative_to(V7) for p in V7.rglob("*.yaml")}
    files8 = {p.relative_to(V8) for p in V8.rglob("*.yaml")}

    assert files8 - files7 == {Path(f"capabilities/{name}.yaml") for name in (*NEW, GATE)}
    assert files7 <= files8
    changed = {p for p in files7 if (V7 / p).read_bytes() != (V8 / p).read_bytes()}
    assert changed == {Path("bundle.yaml"), Path("capabilities/saludo.yaml"), Path("domain.yaml")}
    d7, d8 = _load(V7 / "domain.yaml"), _load(V8 / "domain.yaml")
    assert {k: v for k, v in d8.items() if k != "delivery_time_phrases"} == d7
    assert d8["delivery_time_phrases"] == "1 a 2 dias habiles, 2 a 3 dias habiles"

    b7, b8 = _load(V7 / "bundle.yaml"), _load(V8 / "bundle.yaml")
    assert (b8["id"], b8["version"]) == ("ventas-8", 8)
    assert {k: v for k, v in b8.items() if k not in ("id", "version", "capabilities")} == {
        k: v for k, v in b7.items() if k not in ("id", "version", "capabilities")
    }
    assert set(b8["capabilities"]) == set(b7["capabilities"]) | set(NEW) | {GATE}


def test_the_greeting_v2_changes_only_its_question_and_the_store_wrote_row() -> None:
    _need_v8()
    s7, s8 = _load(V7 / "capabilities/saludo.yaml"), _load(V8 / "capabilities/saludo.yaml")

    assert {k: v for k, v in s8.items() if k not in ("questions", "decide", "examples")} == {
        k: v for k, v in s7.items() if k not in ("questions", "decide", "examples")
    }
    [question] = s8["questions"]
    assert question["id"] == "egreso.saludo_hora_marca"
    assert "hora del día" in question["text"] and "Hubara" in question["text"]
    assert s8["decide"][0] == {"when": "inp.hours_since_store != null", "then": False}
    renamed = yaml.safe_dump(s7["decide"]).replace("egreso.saludo", "egreso.saludo_hora_marca")
    assert s8["decide"][1:] == yaml.safe_load(renamed)


def test_the_catalog_asks_for_the_new_decisions_from_version_8_and_ventas_7_runs_without_them(monkeypatch) -> None:
    from src.sdk.decisionkit import check_bundle

    _need_v8()
    catalog = _load(CATALOG_PATH)

    assert check_bundle(V8, CATALOG_PATH) == []
    for name in (*NEW, GATE):
        assert catalog["capabilities_since"][name] == 8
        assert registry.capability_if_present(name).bundle == "ventas-8@8"
    assert catalog["about"]["saludo_repetido"]["where"] == ["antes_de_enviar"]
    assert catalog["about"]["despedida"]["where"] == ["antes_de_enviar"]
    assert catalog["about"]["repregunta"]["where"] == ["herramientas"]
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-7")
    registry.reset()
    assert all(registry.capability_if_present(name) is None for name in (*NEW, GATE))


def test_each_new_decision_has_its_switch_and_is_born_off(tmp_path: Path) -> None:
    from src.plugins.chats.agent.sales.decisions import bots

    assert set(NEW) <= set(bots.CAPABILITIES)
    assert set(NEW) <= set(egress.EGRESS_CAPABILITIES) | {"repregunta"}
    assert all(bots.bot_for_session(SID, vault_dir=tmp_path).provider(name) == "reglas" for name in NEW)


# ── Las frases del paquete solo proponen ────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "hits"),
    [
        (FORM_FILLER, [True, False]),
        ("¡Gracias por tu compra! Te escribimos cuando lo despachemos.", [True, False]),
        ("Ahí te dejo el link de pago.", [True]),
        ("Listo, el caso quedó cerrado.", [False]),  # «caso cerrado» son palabras seguidas
        ("Tu pedido quedó registrado 🤍", [False]),
    ],
)
def test_the_closing_phrases_propose_the_sentences_that_say_them(text: str, hits: list[bool]) -> None:
    _need_v8()

    items = registry.capability_if_present("despedida").items(OutgoingText(text))

    assert [item["hit"] for item in items] == hits


def test_the_welcome_phrases_propose_the_sentences_that_welcome() -> None:
    _need_v8()

    items = registry.capability_if_present("saludo_repetido").items(OutgoingText(REGREETING))

    assert [item["hit"] for item in items] == [False, True, False]


def test_known_order_data_are_only_what_the_draft_has_and_hide_the_personal_values() -> None:
    draft = {
        "items": [{"producto": "Velas de deseos cuadradas", "cantidad": "1"}],
        "slots": {"ciudad": "Bogotá", "direccion": "Calle 1 # 2-3", "telefono": "", "metodo_pago": "contra entrega"},
    }

    assert known_order_data(draft) == (
        ("el dato «cantidad»", "el pedido ya tiene la cantidad: 1 × Velas de deseos cuadradas"),
        ("el dato «ciudad de envío»", "el pedido ya tiene ciudad de envío: Bogotá"),
        ("el dato «dirección de envío»", "el pedido ya tiene dirección de envío"),
        ("el dato «método de pago»", "el pedido ya tiene método de pago: contra entrega"),
    )
    # Un producto sin cantidad: la cantidad todavía no es un dato del pedido.
    assert known_order_data({"items": [{"producto": "A", "cantidad": "2"}, {"producto": "B"}]}) == ()
    assert known_order_data(None) == ()


# ── Sin Jev no revisa ───────────────────────────────────────────────────────


async def test_without_jev_the_new_decisions_change_nothing_and_never_ask(tmp_path: Path, oracle) -> None:
    _need_v8()
    known = KnownOrderData(text=REASKS_QUANTITY, terms=("el dato «cantidad»",), facts=("x",))

    closing = await _decide("despedida", OutgoingText(FORM_FILLER), tmp_path)
    welcome = await _decide("saludo_repetido", OutgoingText(REGREETING), tmp_path)
    repeated = await _decide("repregunta", known, tmp_path)

    assert (closing.value, closing.by) == ("", "reglas")
    assert (welcome.value, welcome.by) == ("", "reglas")
    assert (list(repeated.value), repeated.by) == ([], "reglas")
    assert oracle["fake"].calls == []


# ── Con Jev ─────────────────────────────────────────────────────────────────


async def test_jev_drops_the_closing_phrase_and_only_the_proposed_sentence_is_asked(tmp_path: Path, oracle, jev) -> None:
    _need_v8()
    oracle["fake"] = _answers(**{"despedida.1": 0.95})

    verdict = await _decide("despedida", OutgoingText(FORM_FILLER), tmp_path)

    assert (verdict.value, verdict.by) == ("Cuando lo llenes, seguimos con tu pedido 🤍", "jev")
    assert _asked(oracle) == ["despedida.1"]


async def test_a_registered_order_whose_text_was_all_closing_phrases_gets_the_approved_farewell(
    tmp_path: Path, oracle, jev
) -> None:
    from src.plugins.chats.shared.store_pack import store_domain

    _need_v8()
    oracle["fake"] = _answers(**{"despedida.1": 0.95, "despedida.2": 0.95})

    verdict = await _decide(
        "despedida", OutgoingText("¡Gracias por tu compra! Caso cerrado.", order_registered=True), tmp_path
    )

    assert verdict.value == store_domain()["farewell_order_registered"]


async def test_a_text_without_closing_phrases_never_asks(tmp_path: Path, oracle, jev) -> None:
    _need_v8()

    verdict = await _decide("despedida", OutgoingText("¿Qué aroma te gustaría? 🤍"), tmp_path)

    assert verdict.value == "" and oracle["fake"].calls == []


async def test_the_welcome_is_never_judged_on_a_true_first_contact(tmp_path: Path, oracle, jev) -> None:
    """La tienda nunca escribió: la bienvenida es la de verdad."""
    _need_v8()

    verdict = await _decide("saludo_repetido", OutgoingText(REGREETING, first_contact=True), tmp_path)

    assert verdict.value == "" and oracle["fake"].calls == []


@pytest.mark.parametrize(
    "inp",
    [
        OutgoingText(REGREETING, first_contact=False, hours_since_store=240),  # vuelve en otro episodio
        OutgoingText(REGREETING, first_contact=True, hours_since_store=20),  # contesta una campaña
    ],
    ids=["returning", "store-wrote-first"],
)
async def test_jev_drops_the_welcome_for_whoever_already_knows_the_store(
    tmp_path: Path, oracle, jev, inp: OutgoingText
) -> None:
    _need_v8()
    oracle["fake"] = _answers(**{"saludo_repetido.2": 0.95})

    verdict = await _decide("saludo_repetido", inp, tmp_path)

    assert (verdict.value, verdict.by) == ("Buenos días. ¿Buscas algo para regalar o para tu casa?", "jev")
    assert _asked(oracle) == ["saludo_repetido.2"]


async def test_jev_says_which_known_data_the_text_asks_again(tmp_path: Path, oracle, jev) -> None:
    _need_v8()
    oracle["fake"] = _answers(**{"repregunta.dato_1": 0.93, "repregunta.dato_2": 0.04})
    inp = KnownOrderData(
        text=REASKS_QUANTITY,
        terms=("el dato «cantidad»", "el dato «método de pago»"),
        facts=("el pedido ya tiene la cantidad: 1 × Velas de deseos cuadradas", "el pedido ya tiene método de pago: x"),
    )

    verdict = await _decide("repregunta", inp, tmp_path)

    assert (list(verdict.value), verdict.by) == (["el dato «cantidad»"], "jev")
    [(state, questions)] = oracle["fake"].calls
    assert questions == ("repregunta.dato_1", "repregunta.dato_2")
    assert REASKS_QUANTITY in state and "1 × Velas de deseos cuadradas" in state


async def test_a_text_that_asks_nothing_is_never_judged_for_known_data(tmp_path: Path, oracle, jev) -> None:
    _need_v8()
    inp = KnownOrderData(text="Perfecto, ya lo anoté 🤍", terms=("el dato «cantidad»",), facts=("x",))

    verdict = await _decide("repregunta", inp, tmp_path)

    assert list(verdict.value) == [] and oracle["fake"].calls == []


async def test_a_hola_is_not_the_greeting_with_the_hour_and_the_brand(tmp_path: Path, oracle, jev) -> None:
    """APE-01: «¡Hola! ¿En qué te ayudo?» ya no deja sin la burbuja."""
    _need_v8()
    oracle["fake"] = _answers(**{"egreso.saludo_hora_marca": 0.05})
    inp = GreetingCheck(first_contact=True, tools_used=(), client_texts=("¡Hola! ¿En qué te ayudo?",))

    verdict = await _decide("saludo", inp, tmp_path)

    assert (verdict.value, verdict.by) == (True, "jev")
    [(state, questions)] = oracle["fake"].calls
    assert questions == ("egreso.saludo_hora_marca",)


async def test_no_welcome_bubble_when_the_store_already_wrote(tmp_path: Path, oracle, jev) -> None:
    _need_v8()
    oracle["fake"] = _answers(**{"egreso.saludo_hora_marca": 0.05})
    inp = GreetingCheck(
        first_contact=True, tools_used=(), client_texts=("¡Hola! ¿En qué te ayudo?",), hours_since_store=20
    )

    verdict = await _decide("saludo", inp, tmp_path)

    assert (verdict.value, verdict.by) == (False, "jev")


# ── En el egreso de V2 ──────────────────────────────────────────────────────


def _egress_inp(text: str, **kw: Any) -> EgressInput:
    return EgressInput(session_id=SID, final_text=text, **kw)


async def _egress(inp: EgressInput, provider: str = "jev", **kw: Any):
    return await egress.decide_egress(inp, provider_of=lambda _c: provider, profile_id="jev-v5", **kw)


async def test_the_egress_drops_the_welcome_to_a_returning_customer(oracle) -> None:
    _need_v8()
    oracle["fake"] = _answers(**{"saludo_repetido.2": 0.95})

    out = await _egress(_egress_inp(REGREETING, first_contact=False), hours_since_store=240)

    assert out.text == "Buenos días. ¿Buscas algo para regalar o para tu casa?"
    assert [g["name"] for g in out.guards] == ["regreeting_guard"]
    assert out.greeting_needed is False


async def test_the_egress_drops_the_closing_phrase(oracle) -> None:
    _need_v8()
    oracle["fake"] = _answers(**{"despedida.1": 0.95})

    out = await _egress(_egress_inp(FORM_FILLER, first_contact=False), hours_since_store=1)

    assert out.text == "Cuando lo llenes, seguimos con tu pedido 🤍"
    assert [g["name"] for g in out.guards] == ["closing_phrase_guard"]


async def test_the_egress_with_rules_sends_the_text_as_it_is(oracle) -> None:
    _need_v8()

    out = await _egress(_egress_inp(f"{REGREETING} {FORM_FILLER}", first_contact=False), provider="reglas")

    assert out.text == f"{REGREETING} {FORM_FILLER}" and out.guards == [] and oracle["fake"].calls == []


async def test_the_egress_with_ventas_7_never_runs_the_new_decisions(oracle, monkeypatch) -> None:
    _need_v8()
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-7")
    registry.reset()
    oracle["fake"] = _answers(**{"saludo_repetido.2": 0.95, "despedida.1": 0.95})

    out = await _egress(_egress_inp(f"{REGREETING} {FORM_FILLER}", first_contact=False), hours_since_store=240)

    assert out.text == f"{REGREETING} {FORM_FILLER}"
    assert not {v["capability"] for v in out.verdicts} & {"despedida", "saludo_repetido"}


async def test_the_egress_sends_no_welcome_bubble_when_the_store_already_wrote(oracle) -> None:
    _need_v8()
    oracle["fake"] = _answers(**{"egreso.saludo_hora_marca": 0.05})
    inp = _egress_inp("¡Hola! ¿En qué te ayudo?", first_contact=True, tools_used=["search_products"])

    first_time = await _egress(inp)
    store_wrote = await _egress(inp, hours_since_store=20)

    assert first_time.greeting_needed is True
    assert store_wrote.greeting_needed is False


def test_the_activity_reads_the_hours_since_the_store_wrote_from_the_history(monkeypatch) -> None:
    from datetime import datetime, timedelta, timezone

    from src.plugins.chats.agent.sales import composition
    from src.plugins.chats.agent.sales.decisions import egress_activities

    wrote = (datetime.now(timezone.utc) - timedelta(hours=5, minutes=30)).isoformat()
    events = [{"role": "assistant", "content": "x", "timestamp": wrote}, {"role": "user", "content": "y"}]
    monkeypatch.setattr(composition, "build_session_history_reader", lambda: lambda _sid: events)
    assert egress_activities._hours_since_store(SID) == 5

    monkeypatch.setattr(composition, "build_session_history_reader", lambda: lambda _sid: [{"role": "user"}])
    assert egress_activities._hours_since_store(SID) is None

    def _broken():
        raise OSError("sin disco")

    monkeypatch.setattr(composition, "build_session_history_reader", lambda: lambda _sid: _broken())
    assert egress_activities._hours_since_store(SID) is None

    # Con un paquete anterior nadie lo lee: el historial ni se abre.
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-7")
    registry.reset()
    monkeypatch.setattr(composition, "build_session_history_reader", lambda: lambda _sid: _broken())
    assert egress_activities._hours_since_store(SID) is None
    monkeypatch.setattr(composition, "build_session_history_reader", lambda: lambda _sid: events)
    assert egress_activities._hours_since_store(SID) is None


async def test_the_activity_hands_the_hours_to_the_greeting(monkeypatch, oracle, jev) -> None:
    from src.plugins.chats.agent.sales.decisions import egress_activities

    _need_v8()
    oracle["fake"] = _answers(**{"egreso.saludo_hora_marca": 0.05})
    inp = _egress_inp("¡Hola! ¿En qué te ayudo?", first_contact=True, tools_used=["search_products"])

    monkeypatch.setattr(egress_activities, "_hours_since_store", lambda _sid: None)
    never_wrote = await egress_activities.decide_egress_activity(inp)
    monkeypatch.setattr(egress_activities, "_hours_since_store", lambda _sid: 20)
    wrote = await egress_activities.decide_egress_activity(inp)

    assert (never_wrote.greeting_needed, wrote.greeting_needed) == (True, False)


def test_hours_since_store_counts_from_the_last_dated_store_message() -> None:
    from datetime import datetime, timezone

    from src.plugins.chats.agent.sales.decisions.bundled_ingest import hours_since_store

    events = [
        {"role": "assistant", "timestamp": "2026-11-01T10:00:00+00:00"},
        {"role": "assistant"},  # sin hora: se salta
        {"role": "user", "timestamp": "2026-11-03T10:00:00+00:00"},
    ]
    last_ms = int(datetime(2026, 11, 1, 10, tzinfo=timezone.utc).timestamp() * 1000)
    now_ms = last_ms + 30 * 3_600_000 + 59 * 60_000
    assert hours_since_store(events, now_ms) == 30
    assert hours_since_store([{"role": "user"}], now_ms) is None
    assert hours_since_store([], now_ms) is None
