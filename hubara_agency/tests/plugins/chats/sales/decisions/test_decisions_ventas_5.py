"""`ventas-5`: `ventas-4` con la pregunta `compra.pregunta_compra` ampliada a la
propuesta de cierre y la de `relevo.promete` sin los avisos de un evento del
pedido, y nada más (esta prueba lo exige, como `test_decisions_ventas_4.py`).

Relevo (premortem del 2026-10-09): «Nuestro equipo te avisa cuando
despachemos tu pedido» es el aviso que manda el sistema al despachar; Jev le
daba 0,83–0,84 (al borde del 0,85 que escala y calla al bot). Sonda con la
pregunta nueva: 0,03–0,07, y los relevos de verdad siguen en 0,95–0,96.

Incidente del 2026-10-09 (bot V2 con Jev, ventas-4): el asesor ANUNCIÓ el
cierre («Te paso el formulario para los datos de envío y dejamos el pedido
listo») y el cliente dijo «Si dale». Jev leyó `compra.que_hace` = confirma con
certeza, pero la pregunta «¿el asesor le pregunta si confirma la compra?» dio
0,10 (anunció, no preguntó) y la fila de retiro de ventas-3 quitó el sí: la
compra no quedó confirmada. Sonda con Jev (10 conversaciones sintéticas): con
la pregunta nueva los anuncios de cierre dan 0,82–0,94 y los «¿te cuento los
aromas?», «¿te muestro el catálogo?» y «¿te muestro las fotos?» siguen en 0,04.

En un clon de forge `ventas-5` no viaja (experimento de esta tienda): se salta.
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
from src.plugins.chats.agent.sales.decisions.readings import Inbound
from src.plugins.chats.shared.store_pack import BUNDLES_DIR, CATALOG_PATH
from src.sdk import connectorkit
from src.sdk.connectorkit import TypedAnswer

V4, V5 = BUNDLES_DIR / "ventas-4", BUNDLES_DIR / "ventas-5"
#: forge no viaja a un clon (`copy_exclude`): sin él, esto es otra tienda.
IN_FORGE_CLONE = not (Path(__file__).resolve().parents[6] / "forge").is_dir()
#: Lo que cambia cada archivo (llaves de primer nivel); el resto es idéntico.
CHANGED = {
    "bundle.yaml": {"id", "version"},
    "capabilities/compra.yaml": {"questions", "examples"},
    "capabilities/relevo.yaml": {"questions"},
}
SID = "wa_573001234567"
TZ = ZoneInfo("America/Bogota")
NOW = 1_790_000_000_000
MIN = 60_000
ANNOUNCED = (
    "Exacto, con el festivo de por medio llega el martes.\n\n"
    "Te paso el formulario para los datos de envío y dejamos el pedido listo 🤍"
)


def _need_v5() -> None:
    if IN_FORGE_CLONE:
        pytest.skip("clon de forge: los paquetes de prueba de esta tienda no viajan")
    assert (V5 / "bundle.yaml").is_file(), "falta el paquete ventas-5"


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _fresh_registry():
    registry.reset()
    yield
    registry.reset()


def test_ventas_5_is_certified() -> None:
    from src.sdk.decisionkit import check_bundle, load_bundle

    _need_v5()

    assert check_bundle(V5, CATALOG_PATH) == []
    assert load_bundle(V5, CATALOG_PATH).ref == "ventas-5@5"


def test_relevo_only_rewords_its_question_to_leave_out_order_event_notices() -> None:
    _need_v5()

    old, new = _load(V4 / "capabilities/relevo.yaml"), _load(V5 / "capabilities/relevo.yaml")
    (q_old,), (q_new,) = old["questions"], new["questions"]
    assert {k: v for k, v in q_new.items() if k not in ("text", "criteria")} == {
        k: v for k, v in q_old.items() if k not in ("text", "criteria")
    }
    # Excluye SOLO el aviso puro: un relevo que nombra el despacho sigue
    # contando (la primera redacción dejaba esos relevos en duda).
    assert "un colega, un asesor, alguien de despachos" in q_new["text"]
    assert "Cuenta aunque diga que será cuando el pedido salga o esté listo" in q_new["text"]
    assert "No cuenta si solo le avisan" in q_new["text"]
    assert new["decide"] == old["decide"] and new["thresholds"] == old["thresholds"]


def test_ventas_5_is_ventas_4_with_the_wider_purchase_question_and_nothing_else() -> None:
    _need_v5()

    v4_files = sorted(str(p.relative_to(V4)) for p in V4.rglob("*.yaml"))
    assert sorted(str(p.relative_to(V5)) for p in V5.rglob("*.yaml")) == v4_files
    for rel in v4_files:
        old, new = _load(V4 / rel), _load(V5 / rel)
        changed = {k for k in old.keys() | new.keys() if old.get(k) != new.get(k)}
        assert changed == CHANGED.get(rel, set()), rel
    assert {**_load(V5 / "bundle.yaml"), "id": "ventas-4", "version": 4} == _load(V4 / "bundle.yaml")

    old, new = _load(V4 / "capabilities/compra.yaml"), _load(V5 / "capabilities/compra.yaml")
    # Solo cambia el texto (y los criterios) de `compra.pregunta_compra`.
    assert [q["id"] for q in new["questions"]] == [q["id"] for q in old["questions"]]
    assert new["questions"][0] == old["questions"][0]
    asked_old, asked_new = old["questions"][1], new["questions"][1]
    assert {k: v for k, v in asked_new.items() if k not in ("text", "criteria")} == {
        k: v for k, v in asked_old.items() if k not in ("text", "criteria")
    }
    assert "propone cerrarlo" in asked_new["text"] and "formulario de envío" in asked_new["text"]
    # Los ejemplos de antes siguen; se agregan los del incidente.
    assert new["examples"][: len(old["examples"])] == old["examples"]
    assert new["decide"] == old["decide"] and new["thresholds"] == old["thresholds"]


# ── el turno del incidente, con cada paquete ─────────────────────────────────


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _si_dale_after_the_announcement() -> Inbound:
    meta = {"episodes": [{
        "episode_id": "ep_001", "started_at_ms": NOW - 60 * MIN, "started_inbound_message_id": "wamid.uno",
        "closed_at_ms": None,
        "order_draft": {"slots": {"producto": "Calabaza", "cantidad": "1", "ciudad": "Bogotá",
                                  "metodo_pago": "contra entrega"}},
    }]}
    events = [
        {"role": "user", "content": "Osea q si hago el pedido hoy llega el martes?", "timestamp": _iso(NOW - 2 * MIN)},
        {"role": "assistant", "content": ANNOUNCED, "timestamp": _iso(NOW - MIN)},
    ]
    return Inbound(session_id=SID, text="Si dale", now_ms=NOW, message_id="wamid.si", metadata=meta,
                   events=events, tz=TZ)


def _jev_as_in_the_probe(monkeypatch: pytest.MonkeyPatch, asked: float) -> None:
    """Jev confirma con certeza; `asked` es lo que contestó a la pregunta de
    cada paquete (ventas-4: 0,10 en producción; ventas-5: 0,94 en la sonda)."""
    answers = (
        TypedAnswer(id="compra.que_hace", kind="choice", choice="confirma", probs=(("confirma", 0.89),),
                    confidence=0.89),
        TypedAnswer(id="compra.pregunta_compra", kind="noul", p=asked),
    )
    fake = FakePerceptionAdapter({a.id: a for a in answers})
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: fake)


async def _compra(monkeypatch: pytest.MonkeyPatch, bundle: str, asked: float) -> Any:
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", bundle)
    registry.reset()
    _jev_as_in_the_probe(monkeypatch, asked)
    return await decide(registry.capability("compra"), _si_dale_after_the_announcement(), provider="jev",
                        profile_id="jev-v5", session_id=SID)


async def test_with_ventas_5_the_yes_to_the_announced_close_is_a_purchase(monkeypatch: pytest.MonkeyPatch) -> None:
    _need_v5()

    verdict = await _compra(monkeypatch, "ventas-5", asked=0.94)

    assert verdict.rule == ["affirmation", "text"]
    assert (verdict.value, verdict.by) == (["affirmation", "text"], "jev")


async def test_with_ventas_4_the_yes_was_retracted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lo que pasó en producción: por eso el arreglo se promueve con el paquete."""
    verdict = await _compra(monkeypatch, "ventas-4", asked=0.10)

    assert (verdict.value, verdict.by) == ([None, "text"], "jev")
