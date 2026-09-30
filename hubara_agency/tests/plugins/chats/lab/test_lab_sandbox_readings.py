"""El sandbox corre las lecturas del ingest (motor de decisiones F2).

Antes el sandbox arrancaba el workflow directo, sin el paso de lecturas del
ingest: el aplazamiento nunca se registraba, el check CON-02 no se juzgaba en
los brazos simulados y la confirmación de compra llegaba con estado
inconsistente. Ahora cada mensaje de la ráfaga pasa por el MISMO proveedor
de lecturas y la MISMA escritura que en producción, con el bot del brazo.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.plugins.chats.lab.test_lab_sandbox_leaks import _run_probe
from tests.plugins.chats.lab.test_lab_sandbox_materialize import T0, _case


def test_a_deferral_in_the_burst_reaches_the_turn_like_in_production(tmp_path: Path) -> None:
    case = _case(burst=[{"text": "les escribo la otra semana", "ts_ms": T0 + 60_000, "wamid": "wamid.D"}],
                 real={"inbound_text": "les escribo la otra semana", "sent_texts": ["Listo"]})

    report = _run_probe(tmp_path, case)

    result = report["result"]
    assert result["error"] is None, result
    assert any("EL CLIENTE APLAZÓ" in note for note in result["plugin_context"]), result["plugin_context"]
    assert [v["capability"] for v in result["readings"][0]] == ["compra", "retoma", "baja"]


async def test_the_readings_read_what_the_customer_wrote_not_what_the_ingest_added(tmp_path: Path, monkeypatch) -> None:
    """En producción las lecturas del ingest leen el texto crudo del cliente
    (`effective.text`), no el turno con la campaña citada. En el sandbox el
    mensaje trae los dos (`text` y `raw_text`): las lecturas leen el crudo."""
    from src.plugins.chats.agent.sales_lab.sandbox.readings import apply_burst_readings

    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    metadata: dict = {"episodes": [{"episode_id": "ep_1", "started_at_ms": T0, "closed_at_ms": None}]}
    wrapped = {"text": "[campaña citada: «les escribo la otra semana con novedades»]\nhola", "raw_text": "hola",
               "ts_ms": T0 + 60_000, "wamid": "wamid.R"}

    await apply_burst_readings(metadata, [wrapped], session_id="wa_573001234567", vault_dir=tmp_path, at_ms=T0 + 60_000)
    wrapped_only = {k: v for k, v in wrapped.items() if k != "raw_text"}
    control: dict = {"episodes": [{"episode_id": "ep_1", "started_at_ms": T0, "closed_at_ms": None}]}
    await apply_burst_readings(control, [wrapped_only], session_id="wa_573001234567", vault_dir=tmp_path, at_ms=T0 + 60_000)

    assert control != metadata, "el texto envuelto sí registra un aplazamiento (control del test)"
    assert not any("deferral" in key for key in metadata), metadata


# ── Lo que leen las lecturas (auditoría del brazo B, punto 3): en producción
# `Inbound.text` es lo que ESCRIBIÓ el cliente (`parsed.text`; en una foto,
# solo el texto que puso en ella) y el botón o el carrito viajan aparte
# (`interactive` / `order`) para que los lea el código. El LLM sigue viendo el
# texto efectivo (la descripción de la foto): eso no cambia.

SID_R = "wa_573001234567"


def _metadata_with_product() -> dict:
    return {"episodes": [{"episode_id": "ep_1", "started_at_ms": T0, "closed_at_ms": None,
                          "order_draft": {"slots": {"producto": "Cubo Love"}}}]}


async def _read(tmp_path: Path, message: dict) -> tuple[dict, list[dict]]:
    from src.plugins.chats.agent.sales_lab.sandbox.readings import apply_burst_readings

    metadata = _metadata_with_product()
    [verdicts] = await apply_burst_readings(metadata, [message], session_id=SID_R, vault_dir=tmp_path, at_ms=T0 + 60_000)
    return metadata, verdicts


@pytest.fixture
def current_bot(monkeypatch):
    monkeypatch.delenv("DECISIONS_BOT", raising=False)


@pytest.fixture
def new_bot(monkeypatch):
    from src.sdk.connectorkit import get_perception_port

    monkeypatch.setenv("DECISIONS_BOT", "B")
    monkeypatch.setenv("PERCEPTION_PROVIDER", "fake")
    get_perception_port.cache_clear()
    yield
    get_perception_port.cache_clear()


async def test_a_photo_is_read_only_by_what_the_customer_typed_in_it(tmp_path: Path, current_bot) -> None:
    """La descripción la escribió la visión: un «mañana» en la foto no es un
    aplazamiento del cliente (bug: un comprobante pausaba la reactivación)."""
    photo = {"text": "[el cliente envió una foto: una vela con la frase «nos vemos mañana»]", "kind": "text",
             "caption": None, "ts_ms": T0 + 60_000, "wamid": "wamid.P_vision"}

    metadata, _ = await _read(tmp_path, photo)

    assert "reengagement_deferral" not in metadata, metadata


async def test_the_caption_of_a_photo_is_what_the_readings_read(tmp_path: Path, current_bot) -> None:
    photo = {"text": '[el cliente envió una foto: una vela roja] con el texto: "luego te escribo"', "kind": "text",
             "caption": "luego te escribo", "ts_ms": T0 + 60_000, "wamid": "wamid.P_vision"}

    metadata, _ = await _read(tmp_path, photo)

    assert metadata["last_inbound_signal"]["kind"] == "deferral", metadata


async def test_the_confirm_button_is_read_by_the_code_not_asked_to_jev(tmp_path: Path, new_bot) -> None:
    button = {"text": "[el cliente tocó el botón: ✅ Confirmar]", "kind": "interactive",
              "interactive": {"type": "button_reply", "id": "", "title": "✅ Confirmar"},
              "ts_ms": T0 + 60_000, "wamid": "wamid.K"}

    metadata, verdicts = await _read(tmp_path, button)

    draft = metadata["episodes"][0]["order_draft"]
    assert draft.get("confirmed_by") == "button", draft
    compra = next(v for v in verdicts if v["capability"] == "compra")
    assert compra["reason"] == "no_question" and compra["jev"] is None, compra


async def test_a_cart_confirms_the_purchase_like_production(tmp_path: Path, current_bot) -> None:
    cart = {"text": "[el cliente armó un carrito con: 2× HUB-CUBO-01]", "kind": "order",
            "order": {"product_items": [{"product_retailer_id": "HUB-CUBO-01", "quantity": 2}]},
            "ts_ms": T0 + 60_000, "wamid": "wamid.O"}

    metadata, _ = await _read(tmp_path, cart)

    assert metadata["episodes"][0]["order_draft"].get("confirmed_by") == "cart", metadata


async def test_a_dated_deferral_is_confirmed_with_its_date_like_in_production(tmp_path: Path, current_bot) -> None:
    """El ingest estampa la hora de cada mensaje (`last_inbound_at_ms`) antes
    de las lecturas: así la nota del aplazamiento le pide al bot confirmar el
    día que dio el cliente («te escribimos el lunes…»), como en producción."""
    from src.plugins.chats.agent.sales_lab.sandbox.readings import ingest_burst

    metadata = _metadata_with_product()
    message = {"text": "les escribo el lunes", "kind": "text", "ts_ms": T0 + 60_000, "wamid": "wamid.L"}

    [ingested] = await ingest_burst(metadata, [message], session_id=SID_R, vault_dir=tmp_path, at_ms=T0 + 61_000)

    note = next(n for n in ingested.context if "EL CLIENTE APLAZÓ" in n)
    assert "Confírmale que le escribimos el lunes" in note, note
    assert metadata["last_inbound_at_ms"] == T0 + 60_000


async def test_a_quoted_bot_photo_gets_the_citation_note_like_in_production(tmp_path: Path, current_bot) -> None:
    """«Esta me gusta» citando una foto que mandó el bot: el ingest le dice al
    LLM cuál foto es (`outbound_media_index`). La cita viene en el evento del
    dashboard que escribió el ingest (`reply_to`)."""
    from src.plugins.chats.agent.sales_lab.sandbox.readings import ingest_burst

    metadata = _metadata_with_product() | {
        "outbound_media_index": {"wamid.BOT1": {"title": "Vela Buda", "handle": "vela-buda", "label": "Buda dorado"}}
    }
    message = {"text": "esta me gusta", "kind": "text", "ts_ms": T0 + 60_000, "wamid": "wamid.Q"}
    record = {"role": "user", "content": "esta me gusta", "timestamp": "2026-09-15T14:21:00+00:00", "wamid": "wamid.Q",
              "reply_to": {"id": "wamid.BOT1", "author": "agent", "text": "Vela Buda"}}

    [ingested] = await ingest_burst(
        metadata, [message], session_id=SID_R, vault_dir=tmp_path, at_ms=T0 + 61_000, records=[record]
    )

    assert any("citando) a una foto" in n and "«Vela Buda», diseño «Buda dorado»" in n for n in ingested.context), \
        ingested.context


async def test_the_ad_banner_is_not_what_the_customer_wrote(tmp_path: Path, current_bot) -> None:
    """El ingest le antepone al primer mensaje que llega de un anuncio un
    banner con el título del anuncio; las lecturas leen solo el mensaje."""
    first = {"text": "[el cliente vino desde un anuncio de Facebook/Instagram, titulado 'Llévala mañana']\nhola",
             "kind": "text", "ts_ms": T0 + 60_000, "wamid": "wamid.A"}

    metadata, _ = await _read(tmp_path, first)

    assert "reengagement_deferral" not in metadata and "last_inbound_signal" not in metadata, metadata


async def test_what_happened_between_the_burst_and_the_turn_goes_in_its_place(
    tmp_path: Path, current_bot, monkeypatch
) -> None:
    """Ráfaga partida: el cliente escribió «y el envío a Bogotá» mientras el bot
    contestaba el turno anterior (su respuesta salió DESPUÉS del mensaje y
    ANTES de este turno), y una persona del equipo había escrito antes. Cada
    mensaje se lee con lo que había cuando llegó y el turno arranca con el
    historial en el orden de producción (`dashboard_between` del caso)."""
    import json as _json

    from src.plugins.chats.agent.sales.decisions import readings as engine_readings
    from src.plugins.chats.agent.sales_lab.sandbox.readings import append_history_event, ingest_burst

    def _event(role: str, content: str, at: int, **extra) -> dict:
        from datetime import datetime, timezone

        return {"role": role, "content": content, "timestamp": datetime.fromtimestamp(at / 1000, tz=timezone.utc).isoformat(), **extra}

    for event in (_event("user", "hola", T0 + 1_000), _event("user", "me mandas el catálogo", T0 + 60_000)):
        append_history_event(tmp_path, SID_R, event)
    team = _event("assistant", "Hola, te escribe Ana del equipo", T0 + 65_000, sender="human")
    reply = _event("assistant", "Claro, te comparto el catálogo", T0 + 75_000)
    seen: list[list[str]] = []
    original = engine_readings.EngineReadings.read

    async def spy(self, inp):
        seen.append([str(e.get("content")) for e in inp.events])
        return await original(self, inp)

    monkeypatch.setattr(engine_readings.EngineReadings, "read", spy)
    message = {"text": "y el envío a Bogotá", "kind": "text", "ts_ms": T0 + 70_000, "wamid": "wamid.E"}

    await ingest_burst(
        _metadata_with_product(), [message], session_id=SID_R, vault_dir=tmp_path, at_ms=T0 + 80_000,
        between=[team, reply],
    )

    assert seen == [["hola", "me mandas el catálogo", "Hola, te escribe Ana del equipo"]]
    history = tmp_path / SID_R / "sessions" / f"{SID_R}.jsonl"
    contents = [_json.loads(line)["content"] for line in history.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert contents == [
        "hola", "me mandas el catálogo", "Hola, te escribe Ana del equipo", "y el envío a Bogotá",
        "Claro, te comparto el catálogo",
    ]


async def test_a_photo_of_our_catalog_reenters_as_production_would_today(tmp_path: Path, current_bot) -> None:
    """Identificación de fotos (2026-09-30): el sandbox vuelve a leer la foto
    del banco con la visión de hoy. El mensaje que recibe el turno, el evento
    del dashboard y el contexto de su señal salen como en producción: la foto
    nombra el producto y el turno lleva la nota «FOTO DEL CLIENTE»."""
    from src.plugins.chats.agent.sales_lab.sandbox.readings import _history, ingest_burst
    from tests.plugins.chats.lab.test_lab_sandbox_photos import OLD, SCREENSHOT, _step, _Vision

    step = _step(tmp_path / "banco", _Vision(SCREENSHOT))
    text = f'{OLD} con el texto: "tienes esta?"'
    message = {"text": text, "kind": "text", "caption": "tienes esta?", "image": "2348323652689569.jpg",
               "ts_ms": T0 + 60_000, "wamid": "wamid.P"}
    record = {"role": "user", "content": text, "timestamp": "2026-09-15T14:21:00+00:00", "wamid": "wamid.P",
              "image_url": "/api/dashboard/media/wa_573009876543/2348323652689569.jpg"}

    [ingested] = await ingest_burst(
        _metadata_with_product(), [message], session_id=SID_R, vault_dir=tmp_path / "vault", at_ms=T0 + 61_000,
        records=[record], photos=step,
    )

    assert message["text"] == (
        "[el cliente envió una foto: vela gris en forma de cruz con rostro y corona dorada (es nuestro producto "
        '«Sacrificio de Amor»: se lee su nombre en la imagen)] con el texto: "tienes esta?"'
    )
    assert any(n.startswith("[FOTO DEL CLIENTE") and "«Sacrificio de Amor»" in n for n in ingested.context)
    # La foto ya identificada no alimenta la nota de fuera de catálogo («jesús»).
    assert not any("no aparece por nombre en el catálogo" in n for n in ingested.context)
    assert _history(tmp_path / "vault", SID_R)[-1]["content"] == message["text"]
    assert ingested.photo is not None and ingested.photo["product"] == {"handle": "sacrificio-de-amor", "how": "nombre"}
