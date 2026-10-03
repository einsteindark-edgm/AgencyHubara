"""Lecturas del cliente en el ingest (diseño v2 §07-§08, fases F2 y F3).

El ingest recibe un proveedor de lecturas: escribe los MISMOS campos de hoy
(`last_inbound_signal` y la confirmación de compra, `reengagement_deferral`,
`marketing_opt_out`), así los leen igual las tools de ventas, remarketing, el
watchdog y el ciclo de reactivación, sin tocarlos. Cada lectura es una
capacidad del motor con la regla de hoy de respaldo:

* compra — «¿Qué hace el cliente con la compra?» con lo que le preguntó el
  asesor a la vista. Confirma solo con la pregunta de compra visible
  (p ≥ 0,85); retira un «sí» que respondía otra cosa (p ≤ 0,20).
* retoma — «¿Dice que retomará más adelante?» y «¿es solo una cortesía?»;
  la fecha la sigue calculando el código.
* baja — «¿Pide dejar de recibir mensajes?»; la frase explícita de hoy es
  PISO: Jev solo agrega.

Lo que escribe la visión (la descripción de una foto) no es texto del
cliente: no pasa por las lecturas (bug: un comprobante pausaba una semana).
"""
from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales.decisions import bots
from src.plugins.chats.agent.sales.decisions.readings import EngineReadings, Inbound
from src.sdk.connectorkit import TypedAnswer

NOW = 1_790_000_000_000  # 2026-09-21 (lunes) ~13:13 Bogotá
TZ = ZoneInfo("America/Bogota")
SID = "wa_573001234567"
RECENT_CAMPAIGN = {"campaign_touches": [{"campaign_id": "c1", "sent_at_ms": NOW - 3_600_000}]}
DRAFT = {"episodes": [{"episode_id": "ep_1", "closed_at_ms": None, "order_draft": {"slots": {"producto": "Duo Zodiacal"}}}]}


def _noul(qid: str, p: float) -> TypedAnswer:
    return TypedAnswer(id=qid, kind="noul", p=p)


def _choice(qid: str, pick: str, p: float) -> TypedAnswer:
    return TypedAnswer(id=qid, kind="choice", choice=pick, probs=((pick, p),), confidence=p)


@pytest.fixture
def oracle(monkeypatch):
    from src.sdk import connectorkit

    holder = {"fake": FakePerceptionAdapter({})}

    def _get(_oracle: str):
        return holder["fake"]

    _get.cache_clear = lambda: None
    monkeypatch.setattr(connectorkit, "get_perception_port", _get)
    return holder


@pytest.fixture
def jev(monkeypatch):
    """El bot con Jev decidiendo las tres lecturas (sin tocar el vault)."""
    monkeypatch.setenv("DECISIONS_BOT", "B")


def _inbound(text: str | None, *, metadata: dict | None = None, events: list | None = None, **kw) -> Inbound:
    return Inbound(session_id=SID, text=text, now_ms=NOW, message_id="w1", metadata=metadata or {}, events=events or [],
                   tz=TZ, **kw)


async def test_with_rules_the_readings_are_todays(tmp_path: Path, oracle) -> None:
    readings = await EngineReadings(tmp_path).read(_inbound("Te confirmo, sí la quiero"))

    assert readings.purchase == ("deferral", "text")  # el bug de hoy, intacto con reglas
    assert readings.opt_out is False and readings.courtesy is False
    assert all(v["by"] == "reglas" for v in readings.verdicts)
    assert oracle["fake"].calls == []


async def test_jev_reads_the_yes_to_the_purchase_question_as_a_purchase(tmp_path: Path, oracle, jev) -> None:
    oracle["fake"] = FakePerceptionAdapter({
        "compra.que_hace": _choice("compra.que_hace", "confirma", 0.95),
        "compra.pregunta_compra": _noul("compra.pregunta_compra", 0.93),
    })
    events = [{"role": "assistant", "content": "¿Confirmas el pedido del Duo Zodiacal por $58.000?"}]

    readings = await EngineReadings(tmp_path).read(_inbound("Te confirmo, sí la quiero", metadata=DRAFT, events=events))

    assert readings.purchase == ("affirmation", "text")
    assert next(v for v in readings.verdicts if v["capability"] == "compra")["by"] == "jev"


async def test_a_yes_to_another_question_is_not_a_purchase(tmp_path: Path, oracle, jev) -> None:
    oracle["fake"] = FakePerceptionAdapter({
        "compra.que_hace": _choice("compra.que_hace", "confirma", 0.9),
        "compra.pregunta_compra": _noul("compra.pregunta_compra", 0.05),
    })
    events = [{"role": "assistant", "content": "¿Te lo enviamos a la misma dirección?"}]

    readings = await EngineReadings(tmp_path).read(_inbound("Si", metadata=DRAFT, events=events))

    assert readings.purchase == (None, "text")


async def test_choosing_an_aroma_is_not_a_purchase(tmp_path: Path, oracle, jev) -> None:
    oracle["fake"] = FakePerceptionAdapter({"compra.que_hace": _choice("compra.que_hace", "elige", 0.92)})

    readings = await EngineReadings(tmp_path).read(_inbound("Ok, lavanda", metadata=DRAFT))

    assert readings.purchase == (None, "text")


async def test_the_confirmation_card_already_says_what_was_asked(tmp_path: Path, oracle, jev) -> None:
    oracle["fake"] = FakePerceptionAdapter({"compra.que_hace": _choice("compra.que_hace", "confirma", 0.95)})
    events = [{"role": "assistant", "kind": "ui_component", "component_kind": "order_confirmation",
               "content": "🧾 El bot envió el resumen del pedido con botones para confirmar"}]

    readings = await EngineReadings(tmp_path).read(_inbound("Si", metadata=DRAFT, events=events))

    assert readings.purchase == ("affirmation", "text")
    [questions] = [q for _, q in oracle["fake"].calls if "compra.que_hace" in q]
    assert "compra.pregunta_compra" not in questions


async def test_buttons_and_the_cart_are_read_by_the_code(tmp_path: Path, oracle, jev) -> None:
    readings = await EngineReadings(tmp_path).read(
        _inbound(None, interactive={"type": "button_reply", "id": "order.confirm", "title": "Confirmar"})
    )

    assert readings.purchase == ("affirmation", "button")
    assert not any("compra.que_hace" in q for _, q in oracle["fake"].calls)


async def test_jev_vetoes_a_deferral_the_rule_read(tmp_path: Path, oracle, jev) -> None:
    """La regla lee «la otra semana» sola como aplazamiento con fecha; si Jev
    está casi seguro (p ≤ 0,15) de que el cliente no aplaza, no hay pausa."""
    oracle["fake"] = FakePerceptionAdapter({
        "retoma.aplaza": _noul("retoma.aplaza", 0.05), "retoma.cortesia": _noul("retoma.cortesia", 0.05),
        "compra.que_hace": _choice("compra.que_hace", "pregunta", 0.9),
    })

    readings = await EngineReadings(tmp_path).read(_inbound("la otra semana"))

    assert readings.deferral is None


async def test_when_jev_confirms_the_deferral_the_code_keeps_the_date(tmp_path: Path, oracle, jev) -> None:
    oracle["fake"] = FakePerceptionAdapter({
        "retoma.aplaza": _noul("retoma.aplaza", 0.95), "retoma.cortesia": _noul("retoma.cortesia", 0.02),
        "retoma.cuando": _choice("retoma.cuando", "otro_dia_con_fecha", 0.9),
        "compra.que_hace": _choice("compra.que_hace", "aplaza", 0.9),
    })

    readings = await EngineReadings(tmp_path).read(_inbound("les escribo la otra semana"))

    assert readings.deferral is not None and readings.deferral.kind == "fecha"


async def test_what_the_vision_wrote_is_not_read_as_the_customer(tmp_path: Path, oracle) -> None:
    receipt = "[el cliente envió un comprobante de pago: transferencia Nequi del viernes 25 de septiembre por $58.000]"

    readings = await EngineReadings(tmp_path).read(_inbound(receipt, synthetic=True, metadata=RECENT_CAMPAIGN))

    assert (readings.purchase, readings.deferral, readings.opt_out) == ((None, "text"), None, False)


async def test_the_explicit_opt_out_phrase_is_a_floor_jev_only_adds(tmp_path: Path, oracle, jev) -> None:
    oracle["fake"] = FakePerceptionAdapter({"baja.pide": _noul("baja.pide", 0.03)})
    floor = await EngineReadings(tmp_path).read(_inbound("no más", metadata=RECENT_CAMPAIGN))
    oracle["fake"] = FakePerceptionAdapter({"baja.pide": _noul("baja.pide", 0.96)})
    added = await EngineReadings(tmp_path).read(_inbound("por favor no me contacten", metadata=RECENT_CAMPAIGN))

    assert floor.opt_out is True and added.opt_out is True


async def test_without_a_recent_campaign_the_opt_out_is_not_asked(tmp_path: Path, oracle, jev) -> None:
    oracle["fake"] = FakePerceptionAdapter({"baja.pide": _noul("baja.pide", 0.99)})

    readings = await EngineReadings(tmp_path).read(_inbound("no me escriban"))

    assert readings.opt_out is False
    assert not any("baja.pide" in q for _, q in oracle["fake"].calls)


async def test_shadow_keeps_todays_readings_and_queues_disagreements(tmp_path: Path, oracle, monkeypatch) -> None:
    monkeypatch.setenv("SALES_CAPABILITIES_CEILING", "on")
    bots.write_capability_modes(tmp_path, {"compra": "shadow"})
    oracle["fake"] = FakePerceptionAdapter({
        "compra.que_hace": _choice("compra.que_hace", "confirma", 0.95),
        "compra.pregunta_compra": _noul("compra.pregunta_compra", 0.95),
    })

    events = [{"role": "assistant", "content": "¿Confirmas el pedido del Duo Zodiacal por $58.000?"}]

    readings = await EngineReadings(tmp_path).read(_inbound("Te confirmo, sí la quiero", metadata=DRAFT, events=events))

    assert readings.purchase == ("deferral", "text")
    from src.plugins.chats.agent.sales.decisions.disagreements import DisagreementLog

    [item] = DisagreementLog(tmp_path).pending()
    assert item["capability"] == "compra" and item["rule"] == ["deferral", "text"] and item["jev"] == ["affirmation", "text"]


def test_one_writer_for_the_ingest_and_the_lab_sandbox() -> None:
    """La escritura de las lecturas es una sola función: el ingest y el sandbox
    del laboratorio escriben exactamente lo mismo."""
    from src.plugins.chats.agent.sales.decisions.readings import Readings, apply_readings
    from src.sdk.messagingkit import ReengagementDeferral

    metadata = {"episodes": [{"episode_id": "ep_1", "closed_at_ms": None, "order_draft": {"slots": {"producto": "Cubo"}}}],
                **RECENT_CAMPAIGN}
    readings = Readings(
        purchase=("affirmation", "text"),
        deferral=ReengagementDeferral(until_ms=NOW + 86_400_000, kind="fecha"),
        courtesy=False,
        opt_out=True,
    )

    written = apply_readings(metadata, readings, text="sí, y les escribo mañana", now_ms=NOW, message_id="w9", tz=TZ,
                             opt_out_campaign_id="c1")

    assert written.signal == "affirmation" and written.opted_out is True
    assert metadata["last_inbound_signal"]["message_id"] == "w9"
    assert metadata["episodes"][0]["order_draft"]["confirmed_at_ms"] == NOW
    assert metadata["reengagement_deferral"]["kind"] == "fecha"
    assert metadata["marketing_opt_out"] is True and metadata["marketing_opt_out_campaign_id"] == "c1"
    again = apply_readings(metadata, readings, text="no más", now_ms=NOW + 1, message_id="w10", tz=TZ, opt_out_campaign_id="c1")
    assert again.opted_out is False  # la baja ya estaba: no se vuelve a marcar
