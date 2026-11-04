"""`ventas-7`, el turno: el de `ventas-6` con la política `turno-v6`, la
pregunta `descubrimiento.senala_producto` (cuestionario `rafaga-v9`) y la fila
del saludo en ③. Lo demás del paquete (las decisiones de hechos) lo prueba
`test_decisions_ventas_7.py`.

Caso de producción del 2026-10-13 (anuncio de Halloween):

* Turno 2: un «hola» con la pregunta de las medidas a media conversación. El
  bot respondió las medidas y ③ pidió una burbuja solo por el saludo (salió
  «buenas noches» a las 4 p. m.). En el primer contacto el saludo lo pone el
  egreso; después, la nota de la hora pide no volver a saludar: el saludo
  nunca pide otra burbuja.
* Turno 5: el cliente citó la línea del set y escribió «Estás» (= «estas»).
  Con la cita y la nota arregladas (PR #423) el modelo lo entendía 11 de 15
  veces; las otras 4 volvía a preguntar «¿una sola o el set?», porque la guía
  de descubrimiento decía siempre «ayúdale a escoger un producto». Con la
  pregunta nueva, si el cliente señala un producto concreto el paso es
  hablarle de ese y preguntarle si se lo lleva, sin volver a preguntar cuál.
  Que le guste no es que lo quiera (criterio del operador, 2026-10-07): el
  paso pregunta, no anota.

En un clon de forge `ventas-7` no viaja (experimento de esta tienda): se salta.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales.decisions import engine, registry
from src.plugins.chats.agent.sales.decisions.context import TurnContext, Window
from src.plugins.chats.agent.sales.decisions.contracts import PerceiveInput, VerifyInput
from src.plugins.chats.shared.store_pack import BUNDLES_DIR, CATALOG_PATH
from src.sdk import connectorkit
from src.sdk.connectorkit import TypedAnswer

V6, V7 = BUNDLES_DIR / "ventas-6", BUNDLES_DIR / "ventas-7"
IN_FORGE_CLONE = not (Path(__file__).resolve().parents[6] / "forge").is_dir()
POINTED = "descubrimiento.senala_producto"
GENERIC_STEP = "ayúdale a escoger un producto"
GREETING_ROW = {"when": "item.topic == 'saludo'", "then": "covered"}
SET_LINE = "El set trae las tres velas juntas, con esas mismas medidas."


def _need_v7() -> None:
    if IN_FORGE_CLONE:
        pytest.skip("clon de forge: los paquetes de prueba de esta tienda no viajan")
    assert (V7 / "bundle.yaml").is_file(), "falta el paquete ventas-7"


def _need_v6() -> None:
    """Lo de antes (`ventas-6`): en un clon de forge solo viaja el paquete que corre la tienda."""
    if not (V6 / "bundle.yaml").is_file():
        pytest.skip("clon de forge: ventas-6 (experimento de esta tienda) no viaja")


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _fresh_registry():
    registry.reset()
    yield
    registry.reset()


def test_ventas_7_is_certified() -> None:
    from src.sdk.decisionkit import check_bundle, load_bundle

    _need_v7()

    assert check_bundle(V7, CATALOG_PATH) == []
    assert load_bundle(V7, CATALOG_PATH).ref == "ventas-7@7"


def test_the_turn_of_ventas_7_is_the_one_of_ventas_6_with_the_pointed_product_and_the_greeting_row() -> None:
    _need_v7()
    # Sin lo que ventas-7 trae en el turno por otros casos (③ del catálogo y el
    # selector al querer cerrar sin la variante: test_decisions_ventas_7.py).
    from tests.plugins.chats.sales.decisions.test_decisions_ventas_7 import _without_catalog_verification

    old, new = _load(V6 / "turn.yaml"), _without_catalog_verification(_load(V7 / "turn.yaml"))

    assert (old["policy"], new["policy"]) == ("turno-v5", "turno-v6")
    assert (old["questionnaire"]["id"], new["questionnaire"]["id"]) == ("rafaga-v8", "rafaga-v9")
    old_q = {q["id"]: q for q in old["questionnaire"]["questions"] if "id" in q}
    new_q = {q["id"]: q for q in new["questionnaire"]["questions"] if "id" in q}
    assert set(new_q) - set(old_q) == {POINTED}
    assert new_q[POINTED]["when"] == {"stage": "etapa_descubrimiento"}
    assert all(old_q[k] == new_q[k] for k in old_q)
    assert {k: v for k, v in new["questionnaire"].items() if k not in {"id", "questions"}} == {
        k: v for k, v in old["questionnaire"].items() if k not in {"id", "questions"}
    }
    assert new["verify_decide"] == [GREETING_ROW, *old["verify_decide"]]
    assert new["examples"]["contract"] == old["examples"]["contract"]
    assert new["examples"]["verify"][: len(old["examples"]["verify"])] == old["examples"]["verify"]
    changed = {"policy", "thresholds", "questionnaire", "verify_decide", "examples"}
    assert {k: v for k, v in new.items() if k not in changed} == {k: v for k, v in old.items() if k not in changed}


# ── turno 2: el saludo a media conversación no pide otra burbuja ──────────────


async def _verify(monkeypatch: pytest.MonkeyPatch, bundle: str) -> tuple[str, list[str]]:
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", bundle)
    registry.reset()
    answers = (
        TypedAnswer(id="cover.medidas", kind="noul", p=0.98),
        TypedAnswer(id="cover.saludo", kind="noul", p=0.22),
    )
    fake = FakePerceptionAdapter({a.id: a for a in answers})
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: fake)
    out = await engine.verify(VerifyInput(
        session_id="wa_573001234567", profile="jev-v5",
        messages=[{"text": "Hola, ¿de qué tamaño son?", "ts_ms": 1_791_925_276_000}],
        topics=[{"topic": "medidas", "msg": 1, "p": 0.98}, {"topic": "saludo", "msg": None, "p": 0.9}],
        reply_text="Son piezas pequeñas: la calabaza mide 9 cm de alto. ¿Cuál te llama más la atención?",
    ))
    assert out.ok, out.error
    return out.decision, list(out.missing)


async def test_with_ventas_7_a_greeting_mid_conversation_asks_for_no_extra_bubble(monkeypatch: pytest.MonkeyPatch) -> None:
    _need_v7()

    assert await _verify(monkeypatch, "ventas-7") == ("send", [])


async def test_with_ventas_6_the_greeting_asked_for_an_extra_bubble(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lo que pasó el 2026-10-13: por eso el arreglo va con el paquete."""
    _need_v6()
    assert await _verify(monkeypatch, "ventas-6") == ("complement", ["saludo"])


# ── turno 5: «Estas» citando la línea del set ─────────────────────────────────


async def _discovery_note(monkeypatch: pytest.MonkeyPatch, bundle: str, *, pointed: float) -> tuple[str, dict]:
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", bundle)
    registry.reset()
    answers = (
        TypedAnswer(id="topic.gusto", kind="noul", p=0.19),
        TypedAnswer(id=POINTED, kind="noul", p=pointed),
        TypedAnswer(id="thread.bot_asked", kind="choice", choice="pregunta_abierta",
                    probs=(("pregunta_abierta", 0.9),), confidence=0.9),
        TypedAnswer(id="thread.answer", kind="choice", choice="otra", probs=(("otra", 0.95),), confidence=0.95),
    )
    fake = FakePerceptionAdapter({a.id: a for a in answers})
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: fake)
    out = await engine.perceive(
        PerceiveInput(session_id="wa_573001234567", profile="jev-v5",
                      messages=[{"text": "Estas", "ts_ms": 1_791_925_320_000}]),
        context=TurnContext(
            window=Window(lines=(f"[asesor] {SET_LINE}", "[cliente] Todas son muy bonitas"), quoted=SET_LINE),
            facts=("Etapa: descubrimiento",), stage="etapa_descubrimiento", stagnant=3,
        ),
    )
    assert out.ok, out.error
    return out.note or "", dict(out.guide or {})


async def test_with_ventas_7_a_pointed_product_is_not_asked_again(monkeypatch: pytest.MonkeyPatch) -> None:
    _need_v7()

    note, guide = await _discovery_note(monkeypatch, "ventas-7", pointed=0.92)

    assert GENERIC_STEP not in note
    assert "no le vuelvas a preguntar cuál" in note
    assert "si citó un mensaje, es el que nombra ese mensaje" in note
    # Que le guste no es que lo quiera: el paso pregunta, no anota.
    assert "pregúntale si se lo lleva" in note and "cuando te diga que sí" in note
    assert guide["pointed_product"] is True


async def test_with_ventas_7_without_a_pointed_product_the_step_is_the_usual_one(monkeypatch: pytest.MonkeyPatch) -> None:
    _need_v7()

    note, guide = await _discovery_note(monkeypatch, "ventas-7", pointed=0.10)

    assert GENERIC_STEP in note
    assert "pointed_product" not in guide


def test_pointing_has_its_own_threshold_below_given() -> None:
    """Sonda con Jev real (frases sintéticas, 2026-10-13): «Estás» citando la
    línea del set da 0,74–0,76 aunque la pregunta y el criterio digan que es
    «estas» con tilde; «Estas» 0,94, «Quiero el zorro» 0,98. Lo más alto que no
    señala uno: «¿La del zorro de qué tamaño es?» 0,38–0,40; «Todas están
    lindas», «¿Estás?», «Estás ahí?» ≤ 0,04. Con `given` (0,85) el caso del
    incidente no contaba; 0,60 deja margen a los dos lados."""
    _need_v7()

    thresholds = _load(V7 / "turn.yaml")["thresholds"]

    assert thresholds["pointed"] == 0.60
    assert {k: v for k, v in thresholds.items() if k != "pointed"} == _load(V6 / "turn.yaml")["thresholds"]


@pytest.mark.parametrize(("pointed", "expected"), [(0.60, True), (0.59, False)])
async def test_the_pointed_step_starts_at_its_threshold(
    monkeypatch: pytest.MonkeyPatch, pointed: float, expected: bool
) -> None:
    _need_v7()

    note, guide = await _discovery_note(monkeypatch, "ventas-7", pointed=pointed)

    assert (GENERIC_STEP not in note) is expected
    assert guide.get("pointed_product", False) is expected


_STAGES = ["etapa_descubrimiento", "etapa_variantes", "etapa_datos_envio", "etapa_cierre", "etapa_postcierre"]


async def _turn(monkeypatch: pytest.MonkeyPatch, bundle: str, *, stage: str, pointed: float, viewed: Any = None) -> Any:
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", bundle)
    registry.reset()
    answers = (
        TypedAnswer(id="topic.precio", kind="noul", p=0.91),
        TypedAnswer(id="topic.variante", kind="noul", p=0.74),
        TypedAnswer(id="variantes.elige", kind="noul", p=0.9),
        TypedAnswer(id="datos.ciudad", kind="noul", p=0.93),
        TypedAnswer(id="cierre.confirma_resumen", kind="noul", p=0.2),
        TypedAnswer(id="postcierre.comprobante", kind="noul", p=0.9),
        TypedAnswer(id="precio.en_contexto", kind="noul", p=0.1),
        TypedAnswer(id=POINTED, kind="noul", p=pointed),
        TypedAnswer(id="thread.bot_asked", kind="choice", choice="elegir_variante",
                    probs=(("elegir_variante", 0.9),), confidence=0.9),
        TypedAnswer(id="thread.answers_bot", kind="noul", p=0.8),
        TypedAnswer(id="thread.answer", kind="choice", choice="otra", probs=(("otra", 0.95),), confidence=0.95),
    )
    fake = FakePerceptionAdapter({a.id: a for a in answers})
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: fake)
    out = await engine.perceive(
        PerceiveInput(session_id="wa_573001234567", profile="jev-v5",
                      messages=[{"text": "La roja, ¿cuánto vale?", "ts_ms": 1_791_925_320_000}]),
        context=TurnContext(window=Window(lines=("[asesor] ¿En qué color la quieres?",)), facts=("Etapa: x",),
                            stage=stage, missing=("ciudad",), stagnant=3, viewed_product=viewed),
    )
    assert out.ok, out.error
    return out


@pytest.mark.parametrize("stage", _STAGES)
async def test_without_a_pointed_product_ventas_7_builds_the_turn_of_ventas_6(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    """La paridad que promete `turno-v6`: sin el producto señalado (y fuera de
    descubrimiento, aunque Jev diga que señala), el turno es el de `turno-v5`."""
    _need_v7()

    for pointed in (0.10, 0.95) if stage != "etapa_descubrimiento" else (0.10,):
        new = await _turn(monkeypatch, "ventas-7", stage=stage, pointed=pointed)
        old = await _turn(monkeypatch, "ventas-6", stage=stage, pointed=pointed)
        assert (new.note, new.tools, new.guide, new.coverage, new.reading) == (
            old.note, old.tools, old.guide, old.coverage, old.reading
        ), (stage, pointed)


async def test_a_pointed_product_weighs_more_than_the_card_it_wrote_from(monkeypatch: pytest.MonkeyPatch) -> None:
    """Escribe desde la ficha de la Calabaza pero señala otro producto en este
    turno: lo que señala ahora es más reciente que la ficha (`turno-v4`)."""
    from src.plugins.chats.agent.sales.decisions.context import ViewedProduct

    _need_v7()
    card = ViewedProduct(title="Calabaza", handle="calabaza", from_catalog=True)

    pointed = await _turn(monkeypatch, "ventas-7", stage="etapa_descubrimiento", pointed=0.9, viewed=card)
    not_pointed = await _turn(monkeypatch, "ventas-7", stage="etapa_descubrimiento", pointed=0.1, viewed=card)

    assert "no le vuelvas a preguntar cuál" in (pointed.note or "") and "Calabaza" not in (pointed.note or "")
    assert "ficha de «Calabaza»" in (not_pointed.note or "")


async def test_with_ventas_6_the_step_asked_to_choose_a_product_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lo que pasó el 2026-10-13: la guía pedía escoger aunque ya hubiera escogido."""
    _need_v6()
    note, _guide = await _discovery_note(monkeypatch, "ventas-6", pointed=0.92)

    assert GENERIC_STEP in note
