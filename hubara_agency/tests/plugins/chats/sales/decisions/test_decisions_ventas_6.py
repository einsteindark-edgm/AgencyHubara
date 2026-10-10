"""`ventas-6`: `ventas-5` con la política `turno-v5` y nada más (esta prueba lo
exige, como `test_decisions_ventas_4.py`).

Caso de producción del 2026-10-09 (pedido #64): el cliente compró, «Confirmar
pago» le devolvió la conversación al bot y 6 minutos después respondió citando
su comprobante. Con el episodio en post-venta, Jev lee que el cliente menciona
un comprobante y la guía de `turno-v4` le decía al LLM «agradece y confirma que
el equipo revisa el pago»: falso, el pago ya estaba confirmado. Con `turno-v5`
el paso es mirar el pago con `check_order_status` y decir lo que diga. Las
preguntas a Jev y los umbrales no cambian.

Y la tabla de `cierre` (decisión del operador, 2026-10-09: ningún silencio pasa
solo al equipo): un pedido a medio cerrar que Jev lee como «confirmó la compra
pero no terminó de dar los datos de envío» cierra INTERESADO —el episodio sigue
abierto con el pedido y remarketing lo retoma—, no CONFIRMADO_SIN_DATOS + relevo
al equipo. La pregunta a Jev es la misma; cambia solo lo que decide su respuesta.

Y la fila `catalogo` del contrato (turno 1 de …7392, 2026-10-08): el modelo
contestó con un texto que listaba los productos y el catálogo con botones no
salió. La fila aceptaba `list_categories`, que solo le devuelve la lista al
modelo (no le muestra nada al cliente), y su nota no decía dónde va el texto.
Ahora pide `present_products` (o la galería) y que lo que iba a escribir vaya
en `intro_text`.

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
    "turn.yaml": {"policy", "contract", "examples"},
    "capabilities/cierre.yaml": {"decide", "examples"},
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


def test_ventas_6_is_ventas_5_with_turno_v5_and_the_close_table_and_nothing_else() -> None:
    _need_v6()

    v5_files = sorted(str(p.relative_to(V5)) for p in V5.rglob("*.yaml"))
    assert sorted(str(p.relative_to(V6)) for p in V6.rglob("*.yaml")) == v5_files
    for rel in v5_files:
        old, new = _load(V5 / rel), _load(V6 / rel)
        changed = {k for k in old.keys() | new.keys() if old.get(k) != new.get(k)}
        assert changed == CHANGED.get(rel, set()), rel
    assert {**_load(V6 / "bundle.yaml"), "id": "ventas-5", "version": 5} == _load(V5 / "bundle.yaml")
    assert (_load(V5 / "turn.yaml")["policy"], _load(V6 / "turn.yaml")["policy"]) == ("turno-v4", "turno-v5")
    # Del contrato cambia solo la fila del catálogo (y su ejemplo).
    old_turn, new_turn = _load(V5 / "turn.yaml"), _load(V6 / "turn.yaml")
    assert [r for r in old_turn["contract"] if r["topic"] != "catalogo"] == [
        r for r in new_turn["contract"] if r["topic"] != "catalogo"
    ]
    assert {k: v for k, v in old_turn["examples"].items() if k != "contract"} == {
        k: v for k, v in new_turn["examples"].items() if k != "contract"
    }


def test_with_ventas_6_the_catalog_asks_for_the_list_with_buttons() -> None:
    """…7392: la fila del catálogo pide lo que el cliente VE (la lista o la
    galería), no `list_categories`, y dice dónde va el texto."""
    _need_v6()

    [row] = [r for r in _load(V6 / "turn.yaml")["contract"] if r["topic"] == "catalogo"]

    assert row["any_of"] == ["present_products", "present_product_gallery"]
    assert "intro_text" in row["nudge"] and "list_categories" not in row["nudge"]


def test_with_ventas_6_the_held_back_catalog_text_asks_to_show_it_not_to_wait_for_data() -> None:
    """La segunda puerta con la fila de cada versión (…7392): con ventas-5 la
    fila acepta una lectura y la nota pedía «el dato» (el modelo ya lo tenía);
    con ventas-6 dice que la herramienta es la que se lo muestra."""
    from src.plugins.chats.agent.sales.decisions.contracts import TurnDecisions
    from src.plugins.chats.agent.sales.decisions.facade import contract_policy_of

    _need_v6()

    def note(bundle: Path) -> str:
        [row] = [r for r in _load(bundle / "turn.yaml")["contract"] if r["topic"] == "catalogo"]
        policy = contract_policy_of(TurnDecisions(ok=True, profile="jev-v5", tools={"required": [row]}))
        assert policy is not None and policy.final_round_note is not None
        return policy.final_round_note(["search_products"], "Tenemos 4 piezas de Halloween") or ""

    assert note(V5).endswith("hasta tener el dato de la herramienta.")
    assert note(V6).endswith("Esa herramienta es la que se lo muestra al cliente: llámala en esta misma respuesta, tu texto solo no basta.")


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


# ── el cierre por silencio de un pedido a medio cerrar ─────────────────────────


SID = "wa_573001234567"


def _seed_unfinished_order(vault: Path) -> None:
    """Confirmó la compra (borrador con `confirmed_at_ms`), le faltan los datos
    de envío y se quedó callado: el caso que Jev lee `confirmado_sin_datos`."""
    import json

    session = vault / SID
    (session / "sessions").mkdir(parents=True, exist_ok=True)
    episode = {
        "episode_id": "ep_1", "started_at_ms": 1, "closed_at_ms": None,
        "order_draft": {"slots": {"producto": "Velón"}, "confirmed_at_ms": 2, "confirmed_by": "button"},
    }
    (session / "metadata.json").write_text(json.dumps({"episodes": [episode]}), encoding="utf-8")
    lines = [{"role": "assistant", "content": "¿Confirmas el pedido?"}, {"role": "user", "content": "Sí, confirmo"}]
    (session / "sessions" / f"{SID}.jsonl").write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")


async def _ghost_notice(monkeypatch: pytest.MonkeyPatch, vault: Path, bundle: str) -> str:
    from temporalio.testing import ActivityEnvironment

    from src.plugins.chats.agent.sales.activities.bootstrap_session import decide_ghosting_action

    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", bundle)
    monkeypatch.setenv("DECISIONS_BOT", "B")
    registry.reset()
    answer = TypedAnswer(
        id="cierre.etiqueta", kind="choice", choice="confirmado_sin_datos",
        probs=(("confirmado_sin_datos", 0.92),), confidence=0.92,
    )
    fake = FakePerceptionAdapter({"cierre.etiqueta": answer})
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: fake)
    _seed_unfinished_order(vault)
    return await ActivityEnvironment().run(decide_ghosting_action, SID)


async def test_with_ventas_6_an_unfinished_order_goes_back_to_remarketing(
    monkeypatch: pytest.MonkeyPatch, _isolate_vault_dir: Path
) -> None:
    from src.plugins.chats.agent.sales.prompts import build_decided_ghosting_prompt

    _need_v6()

    notice = await _ghost_notice(monkeypatch, _isolate_vault_dir, "ventas-6")

    assert notice == build_decided_ghosting_prompt("INTERESADO")
    assert "escalate_to_human" not in notice


async def test_with_ventas_5_an_unfinished_order_goes_to_the_team(
    monkeypatch: pytest.MonkeyPatch, _isolate_vault_dir: Path
) -> None:
    """Lo que pasaría hoy: el relevo al equipo; por eso va en el paquete."""
    from src.plugins.chats.agent.sales.prompts import build_decided_ghosting_prompt

    notice = await _ghost_notice(monkeypatch, _isolate_vault_dir, "ventas-5")

    assert notice == build_decided_ghosting_prompt("CONFIRMADO_SIN_DATOS")
