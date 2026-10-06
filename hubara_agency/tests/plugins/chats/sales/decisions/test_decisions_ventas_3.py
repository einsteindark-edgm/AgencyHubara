"""`ventas-3`: el paquete de la tienda después del incidente del 2026-10-06
(bot V2 con Jev decidiendo para un número de prueba, paquete `ventas-2@2`).

Un error de decisión se arregla en una versión NUEVA del paquete (L-34,
PAQUETES_DE_DECISION.md): `ventas-3` es `ventas-2` con estos cambios y nada
más (esta prueba lo exige, como `test_decisions_ventas_2.py`):

* **Cortesía**: un cliente volvió 11 días después y escribió «Buenas». Jev
  dio 0,94 a «solo cortesía» y el turno contestó «Buenos días 🤍» sin invitar
  a seguir; en el turno 2 el bot dio la bienvenida de marca. Un saludo solo
  con el que el cliente abre la conversación no es cortesía (la pregunta lo
  dice ahora) y un mensaje que abre el episodio cuando la tienda no escribe
  hace 24 horas o más (o nunca escribió) tampoco, diga lo que diga Jev: una
  fila antes de leerlo, con la vista genérica `reply_gap`. El aviso reciente
  (ETA «¿Nos confirmas…?» → «Hola… Muchas gracias») lo sigue decidiendo Jev.
* **Compra**: «¿Te cuento los aromas y colores?» → «Si, quiero uno para mi
  mamá, le gusta la naturaleza». Jev: `confirma` 0,51 y `pregunta_compra`
  0,05; con la duda decidía la regla («^si», «quiero uno») y la compra
  quedaba confirmada (pegajosa: habilita el formulario de envío y el cierre).
  Si se sabe que el asesor NO preguntó por la compra y la regla leyó un «sí»
  en el texto, se retira (salvo que Jev lea un aplazamiento).
* **Afirmación** (solo mide): la pregunta de sí/no quedaba en duda 6 de 9
  veces. Jev dice QUÉ afirma el texto (stock, entrega, estado del pedido o
  nada) y la tabla lo cruza con las herramientas del turno, que el código ya
  sabe.
* **Turno** (`rafaga-v6`): `confirma_compra` ya no se lee en «sí, quiero
  ver»; asunto nuevo `gusto` (dice qué le gusta o para quién es) que se
  atiende recomendando una o dos opciones, no listando 11 aromas.
* **Cantidad** (decisión del operador, 2026-10-06): si el cliente dice
  explícitamente cuántas unidades quiere («quiero uno», «dame dos»), se
  anota aunque el asesor no lo haya preguntado. Solo Jev (pregunta nueva
  `cantidad.dice`, umbral 0,9): la regla sigue exigiendo la pregunta del
  asesor y el camino «el asesor sí preguntó» no cambia.

En un clon de forge `ventas-3` no viaja (experimento de esta tienda): se salta.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import yaml

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales.decisions import registry
from src.plugins.chats.agent.sales.decisions.capabilities import decide
from src.plugins.chats.agent.sales.decisions.capabilities.texto import Afirmacion
from src.plugins.chats.agent.sales.decisions.plan import PlanTopic, TurnPlan, uncovered_topics
from src.plugins.chats.agent.sales.decisions.policies import turno_v1
from src.plugins.chats.agent.sales.decisions.readings import Inbound
from src.plugins.chats.shared.store_pack import BUNDLES_DIR, CATALOG_PATH
from src.sdk import connectorkit
from src.sdk.connectorkit import TypedAnswer

V2, V3 = BUNDLES_DIR / "ventas-2", BUNDLES_DIR / "ventas-3"
#: forge no viaja a un clon (`copy_exclude`): sin él, esto es otra tienda.
IN_FORGE_CLONE = not (Path(__file__).resolve().parents[6] / "forge").is_dir()
SID = "wa_573001234567"
TZ = ZoneInfo("America/Bogota")
NOW = 1_790_000_000_000
HOUR = 3_600_000
WAMID = "wamid.HBgMNTczMDAxMjM0NTY3FQIAEhgUM0E"
#: Lo que cambia cada archivo (llaves de primer nivel); el resto es idéntico.
CHANGED = {
    "bundle.yaml": {"id", "version"},
    "capabilities/cortesia.yaml": {"view", "questions", "decide", "examples"},
    "capabilities/compra.yaml": {"decide", "examples"},
    "capabilities/afirmacion.yaml": {"questions", "thresholds", "decide", "examples"},
    "capabilities/cantidad.yaml": {"questions", "thresholds", "decide", "examples"},
    "turn.yaml": {"questionnaire", "coverage", "reading", "examples"},
}


def _need_v3() -> None:
    if IN_FORGE_CLONE:
        pytest.skip("clon de forge: los paquetes de prueba de esta tienda no viajan")
    assert (V3 / "bundle.yaml").is_file(), "falta el paquete ventas-3"


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _use(monkeypatch: pytest.MonkeyPatch, bundle: str) -> None:
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", bundle)
    registry.reset()


@pytest.fixture(autouse=True)
def _fresh_registry():
    registry.reset()
    yield
    registry.reset()


def _jev(monkeypatch: pytest.MonkeyPatch, *answers: TypedAnswer) -> FakePerceptionAdapter:
    fake = FakePerceptionAdapter({a.id: a for a in answers})
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: fake)
    return fake


def _noul(qid: str, p: float) -> TypedAnswer:
    return TypedAnswer(id=qid, kind="noul", p=p)


def _choice(qid: str, pick: str, p: float) -> TypedAnswer:
    return TypedAnswer(id=qid, kind="choice", choice=pick, probs=((pick, p),), confidence=p)


async def _decide(name: str, inp: Any) -> Any:
    return await decide(registry.capability(name), inp, provider="jev", profile_id="jev-v3", session_id=SID)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


# ── es ventas-2 con estos cambios y nada más ────────────────────────────────


def test_ventas_3_is_certified() -> None:
    from src.sdk.decisionkit import check_bundle, load_bundle

    _need_v3()

    assert check_bundle(V3, CATALOG_PATH) == []
    assert load_bundle(V3, CATALOG_PATH).ref == "ventas-3@3"


def test_ventas_3_is_ventas_2_with_these_changes_and_nothing_else() -> None:
    _need_v3()

    v2_files = sorted(str(p.relative_to(V2)) for p in V2.rglob("*.yaml"))
    assert sorted(str(p.relative_to(V3)) for p in V3.rglob("*.yaml")) == v2_files
    for rel in v2_files:
        old, new = _load(V2 / rel), _load(V3 / rel)
        changed = {k for k in old.keys() | new.keys() if old.get(k) != new.get(k)}
        assert changed == CHANGED.get(rel, set()), rel
    assert {**_load(V3 / "bundle.yaml"), "id": "ventas-2", "version": 2} == _load(V2 / "bundle.yaml")


def test_cortesia_adds_the_view_a_row_before_jev_and_keeps_the_rest() -> None:
    _need_v3()
    old, new = _load(V2 / "capabilities/cortesia.yaml"), _load(V3 / "capabilities/cortesia.yaml")

    assert new["view"] == {"builtin": "reply_gap"}
    [question] = new["questions"]
    assert question["id"] == "cortesia.solo" and question["kind"] == "noul"
    assert "«buenas»" in question["text"] and "NO es cortesía" in question["text"]
    assert new["decide"][0] == {
        "when": "inp.opens_episode == true && (inp.hours_since_store == null || inp.hours_since_store >= 24)",
        "then": False,
    }
    assert new["decide"][1:] == old["decide"]
    assert new["examples"][: len(old["examples"])] == old["examples"]


def test_compra_adds_one_row_before_the_confidence_one_and_keeps_the_rest() -> None:
    _need_v3()
    old, new = _load(V2 / "capabilities/compra.yaml"), _load(V3 / "capabilities/compra.yaml")

    assert new["decide"][0]["then"] == [None, "text"]
    assert "rule == ['affirmation', 'text']" in new["decide"][0]["when"]
    assert new["decide"][1:] == old["decide"]
    assert new["examples"][: len(old["examples"])] == old["examples"]


def test_afirmacion_asks_what_the_text_claims_and_crosses_it_with_the_tools() -> None:
    _need_v3()
    new = _load(V3 / "capabilities/afirmacion.yaml")

    [question] = new["questions"]
    assert (question["id"], question["kind"]) == ("afirmacion.que_afirma", "choice")
    assert list(question["criteria"]) == ["stock", "entrega", "estado_pedido", "nada"]
    rows = " ".join(row.get("when", "") + str(row.get("then", "")) for row in new["decide"])
    assert "inp.tools_used.exists(" in rows and "check_order_status" in rows and "search_products" in rows


def test_cantidad_adds_the_unprompted_question_and_one_row_and_keeps_the_rest() -> None:
    """La pregunta del asesor, la regla y el camino «sí preguntó» no cambian:
    una pregunta más (`cantidad.dice`), su umbral y UNA fila, después de la
    que exige la respuesta de si preguntó."""
    _need_v3()
    old, new = _load(V2 / "capabilities/cantidad.yaml"), _load(V3 / "capabilities/cantidad.yaml")

    *kept, said = new["questions"]
    assert kept == old["questions"]
    assert (said["id"], said["kind"]) == ("cantidad.dice", "noul")
    assert "«un regalo para mi mamá»" in said["text"] and "«tengo 2 hijos»" in said["text"]
    assert new["thresholds"] == {**old["thresholds"], "said": 0.9}
    assert new["decide"][:1] == old["decide"][:1] and new["decide"][2:] == old["decide"][1:]
    assert "p['cantidad.pregunto'] < th['asked']" in new["decide"][1]["when"]
    assert new["examples"][: len(old["examples"])] == old["examples"]


def test_the_turn_changes_only_the_purchase_hint_the_new_topic_its_rule_and_one_note() -> None:
    _need_v3()
    old, new = _load(V2 / "turn.yaml"), _load(V3 / "turn.yaml")
    oq, nq = old["questionnaire"], new["questionnaire"]

    assert (oq["id"], nq["id"]) == ("rafaga-v5", "rafaga-v6")
    assert {k for k in oq.keys() | nq.keys() if oq.get(k) != nq.get(k)} == {"id", "topics"}
    old_topics = {t["id"]: t for t in oq["topics"]}
    *kept, gusto = nq["topics"]
    assert [t["id"] for t in kept] == list(old_topics)
    assert [t for t in kept if t != old_topics[t["id"]]] == [{
        "id": "confirma_compra", "label": "confirmación de compra",
        "hint": "confirma la compra o el pedido que el asesor le propuso (pedir algo, elegir o aceptar ver opciones no es confirmar)",
    }]
    assert gusto["id"] == "gusto" and "recomiéndale una o dos opciones" in gusto["label"]
    assert new["coverage"] == {**old["coverage"], "gusto": new["coverage"]["gusto"]}
    assert new["reading"]["any"] == old["reading"]["any"]
    answered = {k: v for k, v in new["reading"]["answered"].items() if k != "ver_opciones"}
    assert answered == {k: v for k, v in old["reading"]["answered"].items() if k != "ver_opciones"}
    assert new["reading"]["answered"]["ver_opciones"]["no"] == old["reading"]["answered"]["ver_opciones"]["no"]
    for key in ("contract", "verify"):
        assert new["examples"][key][: len(old["examples"][key])] == old["examples"][key]


# ── cortesía: el saludo con que el cliente vuelve ───────────────────────────


def _returning(text: str, *, store_hours_ago: int | None, opens: bool = True) -> Inbound:
    """El mensaje de un cliente cuyo episodio anterior ya cerró: el ingest
    abrió el nuevo con ESTE mensaje (o no, si `opens` es False)."""
    meta = {
        "episodes": [
            {"episode_id": "ep_001", "started_at_ms": NOW - 400 * HOUR, "started_inbound_message_id": "wamid.antes",
             "closed_at_ms": NOW - 300 * HOUR, "closing_tag": "TIMEOUT"},
            {"episode_id": "ep_002", "started_at_ms": NOW, "closed_at_ms": None,
             "started_inbound_message_id": WAMID if opens else "wamid.el_primero"},
        ],
        "last_inbound_at_ms": NOW,
    }
    events = [] if store_hours_ago is None else [
        {"role": "assistant", "content": "Hola, tu pedido #47 ya está listo. ¿Nos confirmas para coordinar la entrega?",
         "timestamp": _iso(NOW - store_hours_ago * HOUR)},
    ]
    return Inbound(session_id=SID, text=text, now_ms=NOW, message_id=WAMID, metadata=meta, events=events, tz=TZ)


async def test_the_greeting_of_a_customer_who_comes_back_days_later_is_not_courtesy(monkeypatch) -> None:
    """El incidente: «Buenas» 11 días (264 horas) después. Con `ventas-2` Jev
    (0,94) lo daba por cortesía; con `ventas-3` decide la fila nueva."""
    _need_v3()
    _jev(monkeypatch, _noul("cortesia.solo", 0.94))
    inp = _returning("Buenas", store_hours_ago=264)

    _use(monkeypatch, "ventas-2")
    before = await _decide("cortesia", inp)
    _use(monkeypatch, "ventas-3")
    after = await _decide("cortesia", inp)

    assert (before.value, before.by) == (True, "jev")
    assert (after.value, after.by, after.bundle) == (False, "jev", "ventas-3@3")


async def test_the_ingest_no_longer_marks_the_returning_greeting_as_courtesy(monkeypatch, tmp_path: Path) -> None:
    """De punta a punta por el proveedor de lecturas del ingest, con el bot B
    (V2 con Jev): con `ventas-3` el «Buenas» del cliente que vuelve deja de
    marcar `courtesy_only` (el que pedía la nota «no abras una venta»)."""
    from src.plugins.chats.agent.sales.decisions.readings import EngineReadings

    _need_v3()
    _jev(monkeypatch, _noul("cortesia.solo", 0.94))
    monkeypatch.setenv("DECISIONS_BOT", "B")
    inp = _returning("Buenas", store_hours_ago=264)

    _use(monkeypatch, "ventas-2")
    before = await EngineReadings(tmp_path).read(inp)
    _use(monkeypatch, "ventas-3")
    after = await EngineReadings(tmp_path).read(inp)

    assert (before.courtesy_only, after.courtesy_only) == (True, False)
    assert {v["capability"]: v["bundle"] for v in after.verdicts}["cortesia"] == "ventas-3@3"


async def test_the_first_message_to_a_store_that_never_wrote_is_not_courtesy(monkeypatch) -> None:
    _need_v3()
    _jev(monkeypatch, _noul("cortesia.solo", 0.94))
    _use(monkeypatch, "ventas-3")

    verdict = await _decide("cortesia", _returning("Hola, buenos días", store_hours_ago=None))

    assert verdict.value is False


async def test_the_thanks_to_a_recent_notice_is_still_decided_by_jev(monkeypatch) -> None:
    """Caso del 2026-09-29: el ETA avisó «¿Nos confirmas…?» hace una hora y el
    cliente contestó «Hola… Muchas gracias»: abre el episodio, pero la tienda
    acaba de escribir. Decide Jev, como en `ventas-2`."""
    _need_v3()
    _jev(monkeypatch, _noul("cortesia.solo", 0.9))
    _use(monkeypatch, "ventas-3")

    recent = await _decide("cortesia", _returning("Hola cómo están? Son geniales. Muchas gracias", store_hours_ago=1))
    inside = await _decide("cortesia", _returning("Muchas gracias", store_hours_ago=264, opens=False))

    assert (recent.value, inside.value) == (True, True)


# ── compra: el «sí» que respondía otra cosa ─────────────────────────────────


ASKED_TO_SHOW = "El Velón Koala viene en varios aromas y colores. ¿Te cuento los aromas y colores?"


def _yes_to_see_options() -> Inbound:
    meta = {"episodes": [{
        "episode_id": "ep_001", "started_at_ms": NOW - HOUR, "started_inbound_message_id": "wamid.uno", "closed_at_ms": None,
        "order_draft": {"slots": {"producto": "Velón Koala"}},
    }]}
    events = [
        {"role": "user", "content": "Hola, ¿qué velas tienen?", "timestamp": _iso(NOW - 10 * 60_000)},
        {"role": "assistant", "content": ASKED_TO_SHOW, "timestamp": _iso(NOW - 5 * 60_000)},
    ]
    return Inbound(
        session_id=SID, text="Si, quiero uno para mi mamá, le gusta la naturaleza", now_ms=NOW, message_id=WAMID,
        metadata=meta, events=events, tz=TZ,
    )


async def test_a_yes_to_see_the_options_is_not_a_purchase(monkeypatch) -> None:
    """Turno 5 del incidente: Jev duda de la compra (0,51) pero sabe que el
    asesor no la preguntó (0,05). Con `ventas-2` decidía la regla
    (`[affirmation, text]`, compra confirmada); con `ventas-3`, se retira."""
    _need_v3()
    _jev(monkeypatch, _choice("compra.que_hace", "confirma", 0.51), _noul("compra.pregunta_compra", 0.05))
    inp = _yes_to_see_options()

    _use(monkeypatch, "ventas-2")
    before = await _decide("compra", inp)
    _use(monkeypatch, "ventas-3")
    after = await _decide("compra", inp)

    assert before.rule == ["affirmation", "text"]
    assert (before.value, before.by, before.reason) == (["affirmation", "text"], "respaldo", "duda")
    assert (after.value, after.by) == ([None, "text"], "jev")


async def test_jev_reading_a_deferral_is_never_retracted_by_the_new_row(monkeypatch) -> None:
    _need_v3()
    _jev(monkeypatch, _choice("compra.que_hace", "aplaza", 0.9), _noul("compra.pregunta_compra", 0.05))
    _use(monkeypatch, "ventas-3")

    verdict = await _decide("compra", _yes_to_see_options())

    assert verdict.value == ["deferral", "text"]


# ── cantidad: la que el cliente dice sin que el asesor la pregunte ──────────


@pytest.fixture
def quantity_on(monkeypatch: pytest.MonkeyPatch, _isolate_vault_dir: Path) -> Path:
    """`cantidad` encendida (con Jev) en todas las conversaciones."""
    from src.plugins.chats.agent.sales.decisions import bots

    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    monkeypatch.setenv("SALES_CAPABILITIES_CEILING", "on")
    bots.write_capability_modes(_isolate_vault_dir, {"cantidad": "on"})
    return _isolate_vault_dir


async def _quantity_turn(workspace_root: Path, vault: Path, sid: str, message: str) -> dict[str, Any]:
    """El turno del incidente por `build_prompt` (donde decide `cantidad`): el
    producto ya está elegido y sin cantidad (hay hueco) y lo último que vio el
    cliente es «¿Te cuento los aromas y colores?», que salió por `send_reply`.
    Devuelve las casillas del borrador después del turno."""
    import json

    from src.plugins.chats.agent.sales.activities.build_prompt_stage import sales_build_prompt
    from tests.plugins.chats.sales.test_quantity_capture import _input, _make_workspace, _seed_history, _seed_metadata

    ws = _make_workspace(workspace_root)
    _seed_metadata(vault, sid, {"producto": "Velón Koala"})
    _seed_history(ws, sid, ASKED_TO_SHOW, via_send_reply=True)
    await sales_build_prompt(_input(ws, sid, message, None))
    meta = json.loads((vault / sid / "metadata.json").read_text("utf-8"))
    return meta["episodes"][-1]["order_draft"]["slots"]


#: Jev en el turno 5 del incidente: el asesor no preguntó la cantidad, pero
#: el cliente dice cuántas quiere («quiero uno»).
SAYS_ONE = (_noul("cantidad.pregunto", 0.05), _noul("cantidad.dice", 0.95), _choice("cantidad.dio", "1", 0.9))


async def test_a_quantity_the_customer_says_unasked_is_pinned_by_jev(monkeypatch, tmp_path: Path, quantity_on: Path) -> None:
    """Decisión del operador (2026-10-06): «Si, quiero uno para mi mamá» con el
    producto elegido anota 1 aunque el asesor no haya preguntado. El caso SÍ
    llega a Jev (hay hueco, y el texto del asesor y el del cliente)."""
    _need_v3()
    fake = _jev(monkeypatch, *SAYS_ONE)
    _use(monkeypatch, "ventas-3")

    slots = await _quantity_turn(tmp_path, quantity_on, SID, "Si, quiero uno para mi mamá, le gusta la naturaleza")

    assert slots.get("cantidad") == "1"
    [(state, asked)] = fake.calls
    assert "¿Te cuento los aromas y colores?" in state and "Si, quiero uno para mi mamá" in state
    assert {"cantidad.pregunto", "cantidad.dice", "cantidad.dio"} <= set(asked)


async def test_with_ventas_2_the_unasked_quantity_was_not_pinned(monkeypatch, tmp_path: Path, quantity_on: Path) -> None:
    _need_v3()
    _jev(monkeypatch, *SAYS_ONE)
    _use(monkeypatch, "ventas-2")

    slots = await _quantity_turn(tmp_path, quantity_on, SID, "Si, quiero uno para mi mamá, le gusta la naturaleza")

    assert "cantidad" not in slots


async def test_a_gift_for_mom_is_not_a_quantity(monkeypatch, tmp_path: Path, quantity_on: Path) -> None:
    _need_v3()
    fake = _jev(
        monkeypatch,
        _noul("cantidad.pregunto", 0.05), _noul("cantidad.dice", 0.08), _choice("cantidad.dio", "ninguna", 0.9),
    )
    _use(monkeypatch, "ventas-3")

    slots = await _quantity_turn(tmp_path, quantity_on, SID, "Un regalo para mi mamá")

    assert "cantidad" not in slots
    assert len(fake.calls) == 1


# ── afirmación: qué afirma el texto × qué consultó el turno ─────────────────


@pytest.mark.parametrize(
    ("claims", "tools", "expected"),
    [
        ("stock", ("send_reply",), True),
        ("stock", ("search_products", "send_reply"), False),
        ("estado_pedido", ("send_reply",), True),
        ("entrega", ("check_order_status", "send_reply"), False),
        ("nada", (), False),
    ],
)
async def test_an_unconsulted_claim_is_what_the_text_claims_without_the_tool(monkeypatch, claims, tools, expected) -> None:
    _need_v3()
    _jev(monkeypatch, _choice("afirmacion.que_afirma", claims, 0.9))
    _use(monkeypatch, "ventas-3")

    verdict = await _decide("afirmacion", Afirmacion(text="Sí, el Velón Koala está disponible en Sándalo 🤍", tools_used=tools))

    assert verdict.value is expected


# ── el turno: lo que le gusta, para quién es ────────────────────────────────


def _turn() -> Any:
    from src.sdk.decisionkit import load_bundle

    return load_bundle(V3, CATALOG_PATH).turn


def test_what_the_customer_likes_is_attended_by_recommending_not_by_listing() -> None:
    """El bot listó 11 aromas sin recomendar. ② da el asunto por atendido solo
    si el texto recomienda; ③ pide el complemento si Jev ve que no."""
    from src.plugins.chats.agent.sales.decisions.turn import tables_of

    _need_v3()
    turn = _turn()
    topics = [PlanTopic("gusto", 1, 0.9)]
    rules = turno_v1.coverage_rules(TurnPlan(ok=True, topics=tuple(topics)), tables_of(turn))
    listed = "Tenemos estos aromas: Lavanda, Vainilla, Canela, Sándalo, Coco, Limoncillo, Rosas, Café, Menta, Pino y Miel"

    assert uncovered_topics(topics, rules, tools_used=["send_reply"], shown=listed) == topics
    assert uncovered_topics(topics, rules, tools_used=[], shown="Para tu mamá te recomiendo el Sándalo o el Pino 🌿") == []
    assert uncovered_topics(topics, rules, tools_used=[], shown="Te sugiero el aroma Pino, muy natural") == []
    assert turn.required(["gusto"], p={}, inp={"stage": None}) == []
    missed = turn.verify([("gusto", 1)], p={"cover.gusto": 0.1})
    assert (missed.decision, tuple(missed.missing)) == ("complement", ("gusto",))


def test_the_burst_asks_the_new_topic_and_the_narrower_purchase_hint() -> None:
    from src.plugins.chats.agent.sales.decisions.questionnaire import questionnaire_of

    _need_v3()
    questionnaire = questionnaire_of(_turn().questionnaire)
    texts = {q.id: q.text for q in questionnaire.burst_questions([{"text": "Si, quiero uno para mi mamá"}])}

    assert questionnaire.id == "rafaga-v6"
    assert "dice qué le gusta o para quién es" in texts["topic.gusto"]
    assert "aceptar ver opciones no es confirmar" in texts["topic.confirma_compra"]


def test_a_yes_to_see_the_options_asks_to_recommend_if_it_said_who_it_is_for() -> None:
    from src.plugins.chats.agent.sales.decisions.turn import tables_of

    _need_v3()
    note = tables_of(_turn()).reading_notes[("ver_opciones", "si")]

    assert note.startswith("El cliente quiere ver las opciones que le ofreciste")
    assert "si te dijo para quién es o qué le gusta, recomiéndale una o dos" in note.lower()
