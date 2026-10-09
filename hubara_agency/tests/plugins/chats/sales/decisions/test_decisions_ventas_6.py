"""`ventas-6`: `ventas-5` con la política `turno-v5` y nada más (esta prueba lo
exige, como `test_decisions_ventas_4.py`).

Caso de producción del 2026-10-09 (pedido #64): el cliente compró, «Confirmar
pago» le devolvió la conversación al bot y 6 minutos después respondió citando
su comprobante. Con el episodio en post-venta, Jev lee que el cliente menciona
un comprobante y la guía de `turno-v4` le decía al LLM «agradece y confirma que
el equipo revisa el pago»: falso, el pago ya estaba confirmado. Con `turno-v5`
el paso es mirar el pago con `check_order_status` y decir lo que diga. Las
preguntas a Jev, los umbrales y las tablas no cambian.

En un clon de forge `ventas-6` no viaja (experimento de esta tienda): se salta.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales.decisions import engine, registry
from src.plugins.chats.agent.sales.decisions.context import TurnContext, Window
from src.plugins.chats.agent.sales.decisions.contracts import PerceiveInput
from src.plugins.chats.shared.store_pack import BUNDLES_DIR, CATALOG_PATH
from src.sdk import connectorkit
from src.sdk.connectorkit import TypedAnswer

V5, V6 = BUNDLES_DIR / "ventas-5", BUNDLES_DIR / "ventas-6"
#: forge no viaja a un clon (`copy_exclude`): sin él, esto es otra tienda.
IN_FORGE_CLONE = not (Path(__file__).resolve().parents[6] / "forge").is_dir()
#: Lo que cambia cada archivo (llaves de primer nivel); el resto es idéntico.
CHANGED = {
    "bundle.yaml": {"id", "version"},
    "turn.yaml": {"policy"},
}
TEAM_CHECKS = "confirma que el equipo revisa el pago"


def _need_v6() -> None:
    if IN_FORGE_CLONE:
        pytest.skip("clon de forge: los paquetes de prueba de esta tienda no viajan")
    assert (V6 / "bundle.yaml").is_file(), "falta el paquete ventas-6"


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _fresh_registry():
    registry.reset()
    yield
    registry.reset()


def test_ventas_6_is_certified() -> None:
    from src.sdk.decisionkit import check_bundle, load_bundle

    _need_v6()

    assert check_bundle(V6, CATALOG_PATH) == []
    assert load_bundle(V6, CATALOG_PATH).ref == "ventas-6@6"


def test_ventas_6_is_ventas_5_with_turno_v5_and_nothing_else() -> None:
    _need_v6()

    v5_files = sorted(str(p.relative_to(V5)) for p in V5.rglob("*.yaml"))
    assert sorted(str(p.relative_to(V6)) for p in V6.rglob("*.yaml")) == v5_files
    for rel in v5_files:
        old, new = _load(V5 / rel), _load(V6 / rel)
        changed = {k for k in old.keys() | new.keys() if old.get(k) != new.get(k)}
        assert changed == CHANGED.get(rel, set()), rel
    assert {**_load(V6 / "bundle.yaml"), "id": "ventas-5", "version": 5} == _load(V5 / "bundle.yaml")
    assert (_load(V5 / "turn.yaml")["policy"], _load(V6 / "turn.yaml")["policy"]) == ("turno-v4", "turno-v5")


# ── el turno del caso: post-venta, el cliente cita su comprobante ─────────────


def _jev(monkeypatch: pytest.MonkeyPatch) -> None:
    """Jev: el cliente menciona el comprobante (lo que pesa para el paso)."""
    answers = (
        TypedAnswer(id="postcierre.comprobante", kind="noul", p=0.93),
        TypedAnswer(id="postcierre.pregunta_pedido", kind="noul", p=0.12),
        TypedAnswer(id="thread.bot_asked", kind="choice", choice="nada", probs=(("nada", 0.99),), confidence=0.99),
        TypedAnswer(id="thread.answer", kind="choice", choice="otra", probs=(("otra", 0.97),), confidence=0.97),
    )
    fake = FakePerceptionAdapter({a.id: a for a in answers})
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: fake)


async def _turn_note(monkeypatch: pytest.MonkeyPatch, bundle: str) -> str:
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", bundle)
    registry.reset()
    _jev(monkeypatch)
    context = TurnContext(
        window=Window(lines=(), quoted="[el cliente envió un comprobante de pago: Comprobante por $48.500]"),
        facts=("Etapa: post-cierre",),
        stage="etapa_postcierre",
    )
    out = await engine.perceive(
        PerceiveInput(session_id="wa_573001234567", profile="jev-v5",
                      messages=[{"text": "…", "ts_ms": 1_791_576_097_000}]),
        context=context,
    )
    assert out.ok, out.error
    return out.note or ""


async def test_with_ventas_6_a_receipt_after_buying_checks_the_payment(monkeypatch: pytest.MonkeyPatch) -> None:
    _need_v6()

    note = await _turn_note(monkeypatch, "ventas-6")

    assert "check_order_status" in note and "pago quedó confirmado" in note
    assert TEAM_CHECKS not in note


async def test_with_ventas_5_the_step_says_the_team_checks_the_payment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lo que pasaría hoy: por eso el arreglo se promueve con el paquete."""
    note = await _turn_note(monkeypatch, "ventas-5")

    assert TEAM_CHECKS in note
