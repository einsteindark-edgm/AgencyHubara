"""`ventas-7`, ③: la verificación ve lo que mostró el bot y lo que se habló
antes, y el catálogo tiene su propia pregunta y su propio complemento.

Anuncio de velitas, 2026-10-13 (16:53 Bogotá). El cliente pidió «más
información sobre las velitas del 7 de diciembre»:

* Turno 1: el bot mandó botones («Ver catálogo · Es para una ocasión»). ③
  preguntó «¿atiende el asunto catálogo?» y Jev dudó (0,56): se envió igual.
* Turno 2: «Quiero ver el catálogo» y el bot mandó el catálogo completo de 30.
  ③ solo veía los mensajes de ese turno y el nombre de la tarjeta: 0,96, «sí».

Sonda con Jev real (pregunta precisa del catálogo): botones 0,10; catálogo
completo 0,36; la lista con las dos velitas 0,82 si ③ veía qué productos
llevaba, 0,58 si no. Por eso ventas-7 trae las tres cosas juntas: la pregunta
del catálogo (`topics[].verify`), lo que muestran las tarjetas
(`reply_state.contents`) y el contexto (`reply_state.with_context`). Y si
falta el catálogo, la burbuja de más lo MUESTRA (`topics[].complement`): un
texto con `send_reply` no le muestra productos a nadie.

ventas-6 sigue igual (la prueba de abajo lo fija).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from temporalio.testing import ActivityEnvironment

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales.decisions import engine, registry
from src.plugins.chats.agent.sales.decisions.activities import verify_coverage_activity
from src.plugins.chats.agent.sales.decisions.contracts import VerifyInput
from src.plugins.chats.shared.store_pack import BUNDLES_DIR
from src.sdk import connectorkit
from src.sdk.connectorkit import TypedAnswer

V6, V7 = BUNDLES_DIR / "ventas-6", BUNDLES_DIR / "ventas-7"
IN_FORGE_CLONE = not (Path(__file__).resolve().parents[6] / "forge").is_dir()
SID = "wa_573001234567"
AD = ("[el cliente vino desde un anuncio de Facebook/Instagram, titulado 'Velitas de intención']\n"
      "¡Hola! Quiero más información sobre las velitas del 7 de diciembre.")
BUTTONS = "Botones: «Ver catálogo» · «Es para una ocasión»"
WHOLE = "Lista con todo el catálogo: 30 productos"


def _need_v7() -> None:
    if IN_FORGE_CLONE:
        pytest.skip("clon de forge: los paquetes de prueba de esta tienda no viajan")
    assert (V7 / "bundle.yaml").is_file(), "falta el paquete ventas-7"


def _need_v6() -> None:
    """Lo de antes (`ventas-6`): en un clon de forge solo viaja el paquete que corre la tienda."""
    if not (V6 / "bundle.yaml").is_file():
        pytest.skip("clon de forge: ventas-6 (experimento de esta tienda) no viaja")


def _topic(bundle: Path, topic: str) -> dict[str, Any]:
    raw = yaml.safe_load((bundle / "turn.yaml").read_text(encoding="utf-8"))
    return next(t for t in raw["questionnaire"]["topics"] if t["id"] == topic)


@pytest.fixture(autouse=True)
def _fresh_registry():
    registry.reset()
    yield
    registry.reset()


def test_the_catalog_asks_whether_the_reply_shows_what_was_asked() -> None:
    _need_v7()

    own = _topic(V7, "catalogo")["verify"]
    assert "MUESTRA" in own["text"] and "{where}" in own["text"]
    assert "botón" in own["criteria"]["false"] and "todo el catálogo" in own["criteria"]["false"]
    assert "present_products" in _topic(V7, "catalogo")["complement"]
    assert "verify" not in _topic(V6, "catalogo")


def test_saying_clearly_that_we_do_not_have_it_attends_the_catalog() -> None:
    """Revisión de producción del PR #426 (2026-10-14): «¿tienen velas de
    Navidad?» → «por ahora no tenemos» se leía «falta» (Jev real 0,03) y el
    complemento le mostraba una lista de otra cosa justo después. Con la
    redacción de abajo, 0,80–0,82; el anuncio con botones, con el catálogo
    completo o con el menú sigue en «falta» (0,06 · 0,19–0,20 · 0,11)."""
    _need_v7()
    topic = _topic(V7, "catalogo")

    assert "no lo tenemos" in topic["verify"]["criteria"]["true"]
    assert "no lo tenemos" in topic["complement"] and "no le muestres otros" in topic["complement"]


def _jev(monkeypatch: pytest.MonkeyPatch, bundle: str, cover: float) -> FakePerceptionAdapter:
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", bundle)
    registry.reset()
    # El saludo sí se atendió (lo pone el egreso): la prueba es del catálogo,
    # con o sin la fila del saludo en ③.
    fake = FakePerceptionAdapter({
        "cover.catalogo": TypedAnswer(id="cover.catalogo", kind="noul", p=cover),
        "cover.saludo": TypedAnswer(id="cover.saludo", kind="noul", p=0.95),
    })
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: fake)
    return fake


def _seed_history(vault: Path) -> list[dict[str, Any]]:
    """El turno 2: antes, el anuncio y los botones; ahora, «quiero ver el catálogo»."""
    history = vault / SID / "sessions" / f"{SID}.jsonl"
    history.parent.mkdir(parents=True, exist_ok=True)
    events = [
        {"role": "user", "content": AD, "wamid": "w0"},
        {"role": "assistant", "content": "¿Buscas las velitas para una ocasión especial o quieres ver las opciones?"},
        {"role": "user", "content": "Quiero ver el catálogo", "wamid": "w1"},
    ]
    history.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n", encoding="utf-8")
    (vault / SID / "metadata.json").write_text(json.dumps({"episodes": [{"episode_id": "ep_1"}]}), encoding="utf-8")
    return [{"text": "Quiero ver el catálogo", "ts_ms": 1_791_928_457_000, "wamid": "w1"}]


async def _verify_turn_2(monkeypatch: pytest.MonkeyPatch, vault: Path, bundle: str) -> tuple[str, Any]:
    fake = _jev(monkeypatch, bundle, cover=0.36)
    out = await ActivityEnvironment().run(verify_coverage_activity, VerifyInput(
        session_id=SID, profile="jev-v5", messages=_seed_history(vault),
        topics=[{"topic": "catalogo", "msg": 1, "p": 0.97}],
        reply_text="Claro que sí. Este es nuestro catálogo completo. ¿Cuál te llama la atención?",
        components=["present_products"], card_contents=[WHOLE],
    ))
    [(state, _ids)] = fake.calls
    return state, out


async def test_with_ventas_7_the_verification_sees_what_was_asked_before_and_what_the_list_shows(
    monkeypatch: pytest.MonkeyPatch, _isolate_vault_dir: Path
) -> None:
    _need_v7()

    state, out = await _verify_turn_2(monkeypatch, _isolate_vault_dir, "ventas-7")

    before, _, turn = state.partition("ESTE TURNO")
    assert "las velitas del 7 de diciembre" in before and "Quiero ver el catálogo" in turn
    assert state.splitlines()[-1].endswith(WHOLE)
    assert out.decision == "pending" and out.missing == ["catalogo"]


async def test_with_ventas_6_the_verification_sees_only_this_turn_and_the_card_name(
    monkeypatch: pytest.MonkeyPatch, _isolate_vault_dir: Path
) -> None:
    """Lo que pasó el 2026-10-13: por eso el arreglo va con el paquete."""
    _need_v6()
    state, _ = await _verify_turn_2(monkeypatch, _isolate_vault_dir, "ventas-6")

    assert "velitas" not in state and WHOLE not in state


async def test_with_ventas_7_a_missing_catalog_is_shown_not_written(monkeypatch: pytest.MonkeyPatch) -> None:
    _need_v7()
    _jev(monkeypatch, "ventas-7", cover=0.10)

    out = await engine.verify(VerifyInput(
        session_id=SID, profile="jev-v5", messages=[{"text": AD, "ts_ms": 1_791_928_409_000}],
        topics=[{"topic": "catalogo", "msg": None, "p": 0.97}, {"topic": "saludo", "msg": None, "p": 0.98}],
        reply_text="¿Buscas las velitas para una ocasión especial o quieres ver las opciones que tenemos?",
        components=["send_quick_replies"], card_contents=[BUTTONS],
    ))

    assert out.decision == "complement" and out.missing == ["catalogo"]
    assert "present_products" in out.complement_note and "send_reply" not in out.complement_note
