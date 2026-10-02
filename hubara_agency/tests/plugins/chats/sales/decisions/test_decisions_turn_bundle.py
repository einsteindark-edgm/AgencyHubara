"""El turno sale del paquete (PAQUETES_DE_DECISION.md F7).

`jev-v5` (el perfil del bot B y el default de producción) ya no nombra un
cuestionario ni umbrales: toma el turno del paquete activo (`turn.yaml`). De
ahí salen la ráfaga ① (el cuestionario `rafaga-v5`, movido sin cambiar un
carácter), los umbrales y las tablas de la política `turno-v3`: qué atiende
cada asunto (②), las notas de la lectura del hilo, el contrato asunto →
tools (con sus excepciones como filas: el precio ya visto, el envío que no
pregunta el costo, la queja de un pedido hecho, el selector según la etapa),
la banda de ③ y la guía de etapas.

La paridad es la compuerta: con las tablas del paquete, el turno decide
EXACTAMENTE lo mismo que con las constantes del código, sobre una grilla de
asuntos, etapas y respuestas en los bordes de cada umbral. Los perfiles
viejos (jev-v1…v4) siguen con sus cuestionarios y las constantes.
"""
from __future__ import annotations

import importlib
import importlib.util
import itertools
import json
import shutil
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.plugins.chats.agent.sales.decisions import registry
from src.plugins.chats.agent.sales.decisions.context import STAGE_LABELS, TurnContext, Window
from src.plugins.chats.agent.sales.decisions.contracts import PerceiveInput, VerifyInput
from src.plugins.chats.agent.sales.decisions.plan import PlanTopic, TurnPlan
from src.plugins.chats.agent.sales.decisions.policies import turno_v1, turno_v2, turno_v3
from src.plugins.chats.agent.sales.decisions.profiles import get_engine_profile
from src.plugins.chats.agent.sales.decisions.questionnaire import QUESTIONNAIRES_DIR, load_questionnaire

FROZEN = json.loads(
    (Path(__file__).resolve().parents[4] / "fixtures/decisions/rafaga_v5_frozen.json").read_text(encoding="utf-8")
)
#: Los umbrales de `jev-v5` antes de F7. `confidence` no la lee ninguna
#: política del turno (PAQUETES_DE_DECISION.md §1): el paquete no la trae.
OLD_V5_THRESHOLDS = {
    "detect": 0.70, "confidence": 0.60, "covered": 0.70, "answers": 0.70,
    "purchase_confirm": 0.85, "purchase_retract": 0.20, "given": 0.85,
}
SID = "wa_573001234567"


@pytest.fixture(autouse=True)
def _store_bundle(monkeypatch):
    """El paquete de la tienda (el registro lo compila una vez por carpeta)."""
    monkeypatch.delenv("SALES_DECISIONS_BUNDLE", raising=False)


def _turn_module() -> Any:
    assert importlib.util.find_spec("src.plugins.chats.agent.sales.decisions.turn") is not None, (
        "falta el resolutor del turno (decisions/turn.py)"
    )
    return importlib.import_module("src.plugins.chats.agent.sales.decisions.turn")


def _v5() -> Any:
    profile = get_engine_profile("jev-v5")
    assert profile is not None
    return _turn_module().turn_of(profile)


# ── el perfil y el cuestionario ─────────────────────────────────────────────


def test_jev_v5_takes_its_turn_from_the_active_bundle() -> None:
    profile = get_engine_profile("jev-v5")

    assert profile is not None
    assert (profile.questions, profile.policy, getattr(profile, "bundle", None)) == ("rafaga-v5", "turno-v3", "ventas@1")
    assert profile.thresholds == {k: v for k, v in OLD_V5_THRESHOLDS.items() if k != "confidence"}


def test_the_older_profiles_keep_their_questionnaires_and_the_code_tables() -> None:
    for profile_id in ("jev-v1", "jev-v2", "jev-v3", "jev-v4"):
        profile = get_engine_profile(profile_id)
        assert profile is not None and getattr(profile, "bundle", "falta") is None, profile_id
        turn = _turn_module().turn_of(profile)
        assert turn.questionnaire.id == profile.questions and turn.tables is None, profile_id


def test_the_burst_questionnaire_moved_without_changing_a_character() -> None:
    from src.plugins.chats.agent.sales.decisions.engine import burst_request

    turn = _v5()

    assert turn.questionnaire.id == "rafaga-v5"
    assert dict(turn.questionnaire.raw) == FROZEN["raw"]
    for name, context in _frozen_contexts().items():
        inp = PerceiveInput(session_id=SID, profile="jev-v5", messages=_FROZEN_MSGS, pending=("pagos",))
        state, questions = burst_request(turn.questionnaire, inp, context)
        assert {"state": state, "questions": [_plain(q) for q in questions]} == FROZEN["requests"][name], name
    plan = TurnPlan(ok=True, topics=(PlanTopic("envio", 2, 0.95), PlanTopic("tiempos", None, 0.9)))
    assert [_plain(q) for q in turn.questionnaire.verify_questions(plan)] == FROZEN["verify"]
    assert turn.questionnaire.reply_state(
        _FROZEN_MSGS, "El envío a Medellín cuesta $15.000", ["send_shipping_rates"]
    ) == FROZEN["reply_state"]


def test_the_questionnaire_file_moved_into_the_bundle() -> None:
    assert not (QUESTIONNAIRES_DIR / "rafaga-v5.yaml").exists()
    with pytest.raises(KeyError):
        load_questionnaire("rafaga-v5")


def _tool_names(package: str) -> set[str]:
    import inspect
    import pkgutil

    names: set[str] = set()
    root = importlib.import_module(package)
    for info in pkgutil.iter_modules(root.__path__):
        module = importlib.import_module(f"{package}.{info.name}")
        for _name, cls in inspect.getmembers(module, inspect.isclass):
            if isinstance(getattr(cls, "name", None), str) and isinstance(getattr(cls, "description", None), str):
                names.add(cls.name)
    return names


def test_the_catalog_turn_vocabulary_is_the_code_s() -> None:
    """Lo que el `turn.yaml` puede nombrar (el `turn:` del catálogo) existe en
    el código: las etapas y los datos que calcula, los hechos del turno y las
    tools del agente. Una tool que no existe dejaría una fila del contrato
    imposible de cumplir."""
    import yaml

    from src.plugins.chats.agent.sales.decisions.context import BOT_ASKED_BY_COMPONENT, SHIPPING_SLOTS
    from src.plugins.chats.shared.store_pack import CATALOG_PATH

    vocab = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))["turn"]

    assert vocab["stages"] == list(STAGE_LABELS)
    assert vocab["slots"] == list(SHIPPING_SLOTS)
    assert list(vocab["facts"]) == list(TurnContext().when_facts())
    assert vocab["facts"]["stage"] == [None, *STAGE_LABELS]
    assert vocab["facts"]["bot_asked_known"] == [None, *BOT_ASKED_BY_COMPONENT.values()]
    assert vocab["facts"]["has_context"] == [True, False]
    # Todas las tools propias del agente se pueden nombrar; las de plataforma
    # que el worker le registra (`escalate_to_human`) también; nada más.
    own, platform = _tool_names("src.plugins.chats.agent.sales.tools"), _tool_names("src.platform.tools")
    assert own <= set(vocab["tools"]) <= own | platform
    assert "escalate_to_human" in vocab["tools"]
    assert set(vocab["policies"]) == {"turno-v3"}


# ── las tablas ───────────────────────────────────────────────────────────────


def test_the_bundle_tables_are_the_ones_the_code_had() -> None:
    tables = _v5().tables

    assert tables is not None
    topics = _v5().questionnaire.topic_ids
    empty = (frozenset(), (), False)
    assert {t: tables.coverage.get(t, empty) for t in topics} == {t: turno_v1._COVERAGE.get(t, empty) for t in topics}
    assert set(tables.coverage) == set(topics)
    assert dict(tables.reading_notes) == turno_v2._READING_NOTES
    assert dict(tables.reading_any) == turno_v2._READING_ANY
    assert tables.choice_topics == turno_v3._CHOICE_TOPICS
    assert tables.no_sale_stages == turno_v3._NO_SALE_STAGES
    assert tables.stagnant_turns == turno_v3.STAGNANT_TURNS
    assert dict(tables.slot_labels) == turno_v3._SLOT_LABELS


def _answer(qid: str, p: Any = None, *, choice: str | None = None, conf: float | None = None) -> Any:
    kind = "choice" if choice is not None else "noul"
    probs = {choice: conf} if choice is not None and conf is not None else {}
    return SimpleNamespace(id=qid, kind=kind, p=p, choice=choice, confidence=conf, probs=probs)


def _result(*answers: Any, ok: bool = True) -> Any:
    return SimpleNamespace(ok=ok, answers=list(answers), error=None if ok else "timeout", model="fake")


_EDGES = (None, "none", 0.0, 0.14, 0.15, 0.19, 0.20, 0.69, 0.70, 0.84, 0.85, 1.0)
_TOPIC_IDS = [t["id"] for t in FROZEN["raw"]["topics"]]
_TOPIC_SETS = (
    *([t] for t in _TOPIC_IDS),
    ["precio", "envio"], ["variante", "aroma"], ["queja", "datos_envio", "promocion"], ["envio", "tiempos", "variante"],
)


def _evidence(qids: tuple[str, ...], value: Any) -> list[Any]:
    """Las respuestas de esas preguntas sí/no con ese valor: None = no hay
    respuesta; "none" = Jev contestó sin probabilidad."""
    if value is None:
        return []
    return [_answer(qid, None if value == "none" else value) for qid in qids]


_WINDOW = Window(lines=("[asesor] ¿Qué color quieres?",))
_CONTEXTS: dict[str, Any] = {
    "sin_contexto": None,
    **{
        f"{stage}{suffix}": TurnContext(window=window, stage=stage, missing=missing, stagnant=stagnant, courtesy=courtesy)
        for stage in STAGE_LABELS
        for suffix, window, missing, stagnant, courtesy in (
            ("", _WINDOW, (), 0, False),
            ("_falta", _WINDOW, ("ciudad", "direccion", "aroma (ítem 2)"), 3, False),
            ("_cortesia", Window(), (), 4, True),
        )
    },
}
_SPECIAL = (
    "precio.en_contexto", "envio.costo", "queja.pedido_hecho", "variantes.elige", "etapa.cambia_producto",
    "datos.ciudad", "datos.direccion", "cierre.confirma_resumen", "cierre.pide_cambio",
    "postcierre.pregunta_pedido", "postcierre.comprobante",
)
_READINGS = (
    (),
    (_answer("thread.bot_asked", choice="elegir_variante", conf=0.9), _answer("thread.answers_bot", 0.9),
     _answer("thread.answer", choice="otra", conf=0.8)),
    (_answer("thread.bot_asked", choice="confirmar_compra", conf=0.95), _answer("thread.answers_bot", 0.7),
     _answer("thread.answer", choice="si", conf=0.9)),
    (_answer("thread.bot_asked", choice="confirmar_dato_envio", conf=0.3), _answer("thread.answers_bot", 0.69),
     _answer("thread.answer", choice="no", conf=0.9)),
)


def _cases() -> list[tuple[str, list[str], Any, Any]]:
    out = []
    for (ctx_name, context), topics, edge, reading in itertools.product(
        _CONTEXTS.items(), _TOPIC_SETS, _EDGES[::3] + (0.70, 0.85), _READINGS
    ):
        answers = [_answer(f"topic.{t}", 0.9 if t not in ("variante", "aroma") else 0.73) for t in topics]
        answers += [_answer("msg.1.topic", choice=topics[0], conf=0.9)]
        answers += _evidence(_SPECIAL, edge)
        out.append((ctx_name, topics, context, _result(*answers, *reading)))
    return out


def test_the_turn_decides_exactly_what_the_code_decided() -> None:
    turn = _v5()
    assert turn.tables is not None
    checked = 0
    for ctx_name, topics, context, result in _cases():
        want = turno_v3.decide_turn(
            result, questionnaire=turn.questionnaire, context=context, n_messages=1, thresholds=OLD_V5_THRESHOLDS
        )
        got = turno_v3.decide_turn(
            result, questionnaire=turn.questionnaire, context=context, n_messages=1, thresholds=turn.thresholds,
            tables=turn.tables,
        )
        assert got == want, (ctx_name, topics, [(a.id, a.p, a.choice) for a in result.answers])
        checked += 1
    assert checked > 1000


@pytest.mark.parametrize("stage", [None, *STAGE_LABELS])
@pytest.mark.parametrize("edge", _EDGES)
def test_the_contract_rows_require_exactly_what_the_code_required(stage: str | None, edge: Any) -> None:
    turn = _v5()
    topics = _TOPIC_IDS
    for special in ("precio.en_contexto", "envio.costo", "queja.pedido_hecho"):
        result = _result(*_evidence((special,), edge))
        for given in ([], ["ciudad", "telefono"]):
            want = turno_v3._required(topics, result, stage, given, OLD_V5_THRESHOLDS)
            got = turno_v3._required(topics, result, stage, given, turn.thresholds, turn.tables)
            assert got == want, (special, edge, stage, given)


_COVER_EDGES = (None, "none", 0.0, 0.29, 0.30, 0.30000000000000004, 0.31, 0.5, 0.69, 0.70, 0.71, 1.0)


@pytest.mark.parametrize(("first", "second"), list(itertools.product(_COVER_EDGES, _COVER_EDGES[::2])))
def test_verification_decides_exactly_what_the_code_decided(first: Any, second: Any) -> None:
    turn = _v5()
    plan = TurnPlan(ok=True, topics=(PlanTopic("precio", 1, 0.9), PlanTopic("envio", None, 0.8)))
    result = _result(*_evidence(("cover.precio",), first), *_evidence(("cover.envio",), second))

    want = turno_v3.coverage_decision(plan, result, thresholds=OLD_V5_THRESHOLDS)
    got = turno_v3.coverage_decision(plan, result, thresholds=turn.thresholds, tables=turn.tables)

    assert got == want
    for empty in (TurnPlan(ok=True), plan):
        failed = _result(ok=False)
        assert turno_v3.coverage_decision(empty, failed, tables=turn.tables) == turno_v3.coverage_decision(empty, failed)


# ── el motor lee el paquete ─────────────────────────────────────────────────


class _Port:
    def __init__(self, answers: dict[str, Any]) -> None:
        from src.platform.perception.adapters.fake import FakePerceptionAdapter

        self.fake = FakePerceptionAdapter(answers)
        self.questions: list[tuple[str, ...]] = []

    async def ask(self, state, questions, *, timeout_s, redact=()):
        self.questions.append(tuple(q.id for q in questions))
        return await self.fake.ask(state, questions, timeout_s=timeout_s, redact=redact)


#: El paquete de prueba (no `ventas-2`: ese ya existe en el repo).
OTHER = "ventas-prueba"


@pytest.fixture
def other_bundle(tmp_path: Path, monkeypatch):
    """Otro paquete (`ventas-prueba`, una copia de `ventas` con su turno editado) como el de la tienda."""

    def make(edit) -> None:
        bundles = tmp_path / "bundles"
        shutil.copytree(registry.BUNDLES_DIR, bundles)
        shutil.move(bundles / "ventas", bundles / OTHER)
        head = bundles / OTHER / "bundle.yaml"
        head.write_text(head.read_text(encoding="utf-8").replace("id: ventas\n", f"id: {OTHER}\n"), encoding="utf-8")
        turn = bundles / OTHER / "turn.yaml"
        turn.write_text(edit(turn.read_text(encoding="utf-8")), encoding="utf-8")
        monkeypatch.setattr(registry, "BUNDLES_DIR", bundles)
        monkeypatch.setenv("SALES_DECISIONS_BUNDLE", OTHER)
        registry.reset()

    yield make
    registry.reset()


async def test_the_engine_runs_the_turn_of_the_active_bundle(other_bundle, monkeypatch) -> None:
    from src.plugins.chats.agent.sales.decisions import engine
    from src.sdk import connectorkit
    from src.sdk.connectorkit import TypedAnswer

    other_bundle(
        lambda text: text.replace("Para mostrarle el catálogo usa present_products", "Muéstrale el catálogo con present_products"),
    )
    port = _Port({"topic.catalogo": TypedAnswer(id="topic.catalogo", kind="noul", p=0.95)})
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: port)

    out = await engine.perceive(PerceiveInput(session_id=SID, profile="jev-v5", messages=[{"text": "catálogo?", "ts_ms": 1}]))

    assert out.versions == {
        "profile": "jev-v5", "questions": "rafaga-v5", "policy": "turno-v3", "model": out.model, "bundle": f"{OTHER}@1",
    }
    assert out.tools["required"][0]["nudge"].startswith("Muéstrale el catálogo con present_products")
    assert "envio.costo" in port.questions[0]


async def test_verification_runs_the_bands_of_the_active_bundle(other_bundle, monkeypatch) -> None:
    from src.plugins.chats.agent.sales.decisions import engine
    from src.sdk import connectorkit
    from src.sdk.connectorkit import TypedAnswer

    # Otro paquete: atendido desde 0,5 (no desde `covered`).
    other_bundle(lambda text: text.replace("item.p >= th['covered']", "item.p >= 0.5"))
    port = _Port({"cover.precio": TypedAnswer(id="cover.precio", kind="noul", p=0.6)})
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: port)
    inp = VerifyInput(
        session_id=SID, profile="jev-v5", messages=[{"text": "precio?", "ts_ms": 1}],
        topics=[{"topic": "precio", "msg": 1, "p": 0.9}], reply_text="Vale $20.000", components=[],
    )

    out = await engine.verify(inp)

    assert (out.decision, out.missing) == ("send", [])


# ── la foto del cuestionario (los mismos casos con que se congeló) ──────────

_FROZEN_MSGS = [
    {"text": "hola", "ts_ms": 1000}, {"text": "¿cuánto vale el envío a Medellín?", "ts_ms": 4000},
    {"text": "y cuánto tarda", "ts_ms": 9000},
]


def _frozen_contexts() -> dict[str, Any]:
    window = Window(lines=("[asesor] ¿Qué color quieres?",), quoted="¿Qué color quieres?")
    known = Window(lines=("[asesor] (tarjeta de confirmación)",), last_component="order_confirmation",
                   bot_asked_known="confirmar_compra")
    return {
        "sin_contexto": None,
        "descubrimiento": TurnContext(window=window, facts=("Etapa: descubrimiento",), stage="etapa_descubrimiento"),
        "variantes": TurnContext(window=window, facts=("Etapa: variantes", "Ítem 1: Vela, lavanda"),
                                 stage="etapa_variantes", missing=("color",)),
        "datos_envio": TurnContext(window=window, facts=("Etapa: datos de envío",), stage="etapa_datos_envio",
                                   missing=("direccion",)),
        "cierre_tarjeta": TurnContext(window=known, facts=("Etapa: cierre",), stage="etapa_cierre"),
        "postcierre": TurnContext(window=window, facts=("Etapa: postcierre",), stage="etapa_postcierre"),
    }


def _plain(q: Any) -> dict[str, Any]:
    crit = dict(q.criteria) if isinstance(q.criteria, Mapping) else list(q.criteria)
    return {"id": q.id, "kind": q.kind, "text": q.text, "criteria": crit}
