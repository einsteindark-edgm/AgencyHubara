"""`ventas-4`: `ventas-3` con la política `turno-v4` y nada más (esta prueba
lo exige, como `test_decisions_ventas_3.py`).

Conversación de prueba del 2026-10-07 (···1604, run 95f3f563, turno 3): el
cliente escribió «Me gusta esta» desde la ficha de la Calabaza en el catálogo
de WhatsApp. La nota del ingest llegó, pero el turno de `ventas-3` (política
`turno-v3`) calculó la etapa con el borrador vacío y le pidió al LLM «ayúdale
a escoger un producto (catálogo o ficha)»: el bot preguntó «¿Cuál de las
cuatro te gustó?». Con `turno-v4` el paso de descubrimiento usa el producto
desde el que escribe (`TurnContext.viewed_product`). Las preguntas a Jev, los
umbrales y las tablas no cambian.

En un clon de forge `ventas-4` no viaja (experimento de esta tienda): se salta.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales.decisions import engine, registry
from src.plugins.chats.agent.sales.decisions.context import TurnContext, ViewedProduct, Window
from src.plugins.chats.agent.sales.decisions.contracts import PerceiveInput
from src.plugins.chats.shared.store_pack import BUNDLES_DIR, CATALOG_PATH
from src.sdk import connectorkit
from src.sdk.connectorkit import TypedAnswer

V3, V4 = BUNDLES_DIR / "ventas-3", BUNDLES_DIR / "ventas-4"
#: forge no viaja a un clon (`copy_exclude`): sin él, esto es otra tienda.
IN_FORGE_CLONE = not (Path(__file__).resolve().parents[6] / "forge").is_dir()
#: Lo que cambia cada archivo (llaves de primer nivel); el resto es idéntico.
CHANGED = {
    "bundle.yaml": {"id", "version"},
    "turn.yaml": {"policy"},
}
CHOOSE_ONE = "ayúdale a escoger un producto"


def _need_v4() -> None:
    if IN_FORGE_CLONE:
        pytest.skip("clon de forge: los paquetes de prueba de esta tienda no viajan")
    assert (V4 / "bundle.yaml").is_file(), "falta el paquete ventas-4"


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _fresh_registry():
    registry.reset()
    yield
    registry.reset()


def test_ventas_4_is_certified() -> None:
    from src.sdk.decisionkit import check_bundle, load_bundle

    _need_v4()

    assert check_bundle(V4, CATALOG_PATH) == []
    assert load_bundle(V4, CATALOG_PATH).ref == "ventas-4@4"


def test_ventas_4_is_ventas_3_with_turno_v4_and_nothing_else() -> None:
    _need_v4()

    v3_files = sorted(str(p.relative_to(V3)) for p in V3.rglob("*.yaml"))
    assert sorted(str(p.relative_to(V4)) for p in V4.rglob("*.yaml")) == v3_files
    for rel in v3_files:
        old, new = _load(V3 / rel), _load(V4 / rel)
        changed = {k for k in old.keys() | new.keys() if old.get(k) != new.get(k)}
        assert changed == CHANGED.get(rel, set()), rel
    assert {**_load(V4 / "bundle.yaml"), "id": "ventas-3", "version": 3} == _load(V3 / "bundle.yaml")
    assert (_load(V3 / "turn.yaml")["policy"], _load(V4 / "turn.yaml")["policy"]) == ("turno-v3", "turno-v4")


# ── el turno 3 de la conversación de prueba, con cada paquete ─────────────────


def _jev(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lo que contestó Jev en ese turno (las que pesan para el paso)."""
    answers = (
        TypedAnswer(id="topic.gusto", kind="noul", p=0.65),
        TypedAnswer(id="thread.bot_asked", kind="choice", choice="pregunta_abierta",
                    probs=(("pregunta_abierta", 0.96),), confidence=0.95),
        TypedAnswer(id="thread.answers_bot", kind="noul", p=0.27),
        TypedAnswer(id="thread.answer", kind="choice", choice="otra", probs=(("otra", 0.93),), confidence=0.89),
        TypedAnswer(id="msg.1.topic", kind="choice", choice="gusto", probs=(("gusto", 0.96),), confidence=0.96),
    )
    fake = FakePerceptionAdapter({a.id: a for a in answers})
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: fake)


async def _turn_note(monkeypatch: pytest.MonkeyPatch, bundle: str) -> str:
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", bundle)
    registry.reset()
    _jev(monkeypatch)
    context = TurnContext(
        window=Window(lines=("[asesor] 🛍️ El bot envió el catálogo con 4 productos",)),
        facts=("Etapa: descubrimiento",),
        stage="etapa_descubrimiento",
        stagnant=2,
        viewed_product=ViewedProduct(title="Calabaza", handle="calabaza", from_catalog=True),
    )
    out = await engine.perceive(
        PerceiveInput(session_id="wa_573001234567", profile="jev-v5",
                      messages=[{"text": "Me gusta esta", "ts_ms": 1_791_389_181_000}]),
        context=context,
    )
    assert out.ok, out.error
    return out.note or ""


async def test_with_ventas_4_the_turn_answers_about_the_card_product(monkeypatch: pytest.MonkeyPatch) -> None:
    _need_v4()

    note = await _turn_note(monkeypatch, "ventas-4")

    assert "Calabaza" in note and CHOOSE_ONE not in note


async def test_with_ventas_3_the_turn_still_asks_to_choose(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lo que pasó en producción: por eso el arreglo se promueve con el paquete."""
    if not (V3 / "bundle.yaml").is_file():
        pytest.skip("clon de forge: ventas-3 (experimento de esta tienda) no viaja")
    note = await _turn_note(monkeypatch, "ventas-3")

    assert CHOOSE_ONE in note and "Calabaza" not in note
