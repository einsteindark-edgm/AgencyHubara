"""El costo REAL (webhook `message_status`) usa la tarjeta vigente POR FECHA.

`IngestDeliveryStatus` recibía UNA `RateCard` al construirse y el composition
root la resolvía una sola vez por proceso: un API que arrancó en septiembre
seguiría valuando los service messages de octubre a $0 hasta el próximo
deploy. Ahora recibe un proveedor y resuelve la tarjeta por la fecha de ENVÍO
del mensaje — determinista: re-procesar un status viejo da el mismo costo.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.platform.state import FilesystemMetadataStore
from src.platform.whatsapp.composition import get_current_rate_card
from src.plugins.chats.agent.sales.use_cases.ingest_delivery_status import (
    IngestDeliveryStatus,
)
from tests.test_ingest_delivery_status import _seed_episode_with_pending_entry

SID = "wa_573001234567"
# Shape oficial post-1-oct-2026 (doc "non-template messages" de Meta).
SERVICE_REGULAR = {
    "billable": True,
    "pricing_model": "PMP",
    "type": "regular",
    "category": "service",
}


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp() * 1000)


async def _deliver(vault_dir: Path, *, sent_at_ms: int) -> dict:
    store = FilesystemMetadataStore(vault_dir)
    _seed_episode_with_pending_entry(
        store,
        session_id=SID,
        wa_message_id="wamid.OUT1",
        kind="text",
        template_name=None,
        sent_at_ms=sent_at_ms,
    )
    use_case = IngestDeliveryStatus(
        metadata_store=store,
        rate_card_for=get_current_rate_card,
        event_bus=None,
        vault_dir=vault_dir,
        retry_delays=(0.001,),
    )
    await use_case.execute(
        wa_message_id="wamid.OUT1",
        status="delivered",
        pricing=SERVICE_REGULAR,
    )
    return store.read(SID)["episodes"][0]["outbound_messages"][0]


@pytest.mark.asyncio
async def test_free_form_enviado_en_octubre_se_valua_con_q4(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("WHATSAPP_RATE_CARD_VERSION", raising=False)
    entry = await _deliver(tmp_path, sent_at_ms=_ms("2026-10-02T15:00:00"))
    assert entry["cost_usd_micros"] == 800
    assert entry["rate_card_version"] == "co_2026q4_v1"


@pytest.mark.asyncio
async def test_free_form_enviado_en_septiembre_sigue_con_q2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("WHATSAPP_RATE_CARD_VERSION", raising=False)
    entry = await _deliver(tmp_path, sent_at_ms=_ms("2026-09-20T15:00:00"))
    assert entry["rate_card_version"] == "co_2026q2_v1"


@pytest.mark.asyncio
async def test_el_use_case_compuesto_en_septiembre_valua_octubre_con_q4(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """El bug de raíz: el composition root resolvía la tarjeta UNA vez al
    construir el singleton. Un proceso levantado en septiembre (sin deploy el
    1-oct) debe valuar igual los mensajes de octubre con la tarjeta nueva."""
    from src.platform.whatsapp import composition as wa_composition
    from src.plugins.chats.agent.sales import composition as sales_composition

    monkeypatch.delenv("WHATSAPP_RATE_CARD_VERSION", raising=False)
    monkeypatch.setattr(sales_composition, "_DELIVERY_STATUS_USE_CASE", None)
    monkeypatch.setattr(sales_composition, "WORKSPACE_VAULT_DIR", tmp_path)
    monkeypatch.setattr(sales_composition, "setup_analytics", lambda: None)
    # El proceso "arranca" el 20 de septiembre.
    monkeypatch.setattr(
        wa_composition.time, "time", lambda: _ms("2026-09-20T12:00:00") / 1000
    )
    use_case = sales_composition.build_ingest_delivery_status_use_case()

    store = FilesystemMetadataStore(tmp_path)
    _seed_episode_with_pending_entry(
        store,
        session_id=SID,
        wa_message_id="wamid.OUT2",
        kind="text",
        template_name=None,
        sent_at_ms=_ms("2026-10-03T10:00:00"),
    )
    await use_case.execute(
        wa_message_id="wamid.OUT2", status="delivered", pricing=SERVICE_REGULAR
    )
    entry = store.read(SID)["episodes"][0]["outbound_messages"][0]
    assert entry["rate_card_version"] == "co_2026q4_v1"
    assert entry["cost_usd_micros"] == 800


@pytest.mark.asyncio
async def test_free_customer_service_con_tarjeta_que_ya_cobra_deja_rastro(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """Riesgo del short-circuit: `compute_message_cost_micros` devuelve 0 para
    `free_customer_service` ANTES de leer la tarjeta. Meta documenta que desde
    el 1-oct el service llega como `regular`; si en la práctica siguiera
    llegando `free_customer_service` mientras cobra, subcontaríamos en silencio.
    El costo sigue la verdad del webhook (0) y el tripwire deja el caso en el
    log para conciliarlo contra la factura de Meta."""
    import logging

    monkeypatch.delenv("WHATSAPP_RATE_CARD_VERSION", raising=False)
    store = FilesystemMetadataStore(tmp_path)
    _seed_episode_with_pending_entry(
        store,
        session_id=SID,
        wa_message_id="wamid.OUT3",
        kind="text",
        template_name=None,
        sent_at_ms=_ms("2026-10-05T10:00:00"),
    )
    use_case = IngestDeliveryStatus(
        metadata_store=store,
        rate_card_for=get_current_rate_card,
        event_bus=None,
        vault_dir=tmp_path,
        retry_delays=(0.001,),
    )
    with caplog.at_level(logging.WARNING):
        await use_case.execute(
            wa_message_id="wamid.OUT3",
            status="delivered",
            pricing={
                "billable": False,
                "type": "free_customer_service",
                "category": "service",
            },
        )
    entry = store.read(SID)["episodes"][0]["outbound_messages"][0]
    assert entry["cost_usd_micros"] == 0
    assert any(
        "free_pricing_on_billable_category" in r.getMessage() for r in caplog.records
    )


@pytest.mark.asyncio
async def test_utility_gratis_en_ventana_antes_del_1_de_octubre_no_es_ruido(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    # Hasta el 30-sep una utility dentro de la ventana ES gratis de verdad
    # (tarjeta q2: utility=800 pero service=0 → todavía no pasó el acantilado).
    import logging

    monkeypatch.delenv("WHATSAPP_RATE_CARD_VERSION", raising=False)
    store = FilesystemMetadataStore(tmp_path)
    _seed_episode_with_pending_entry(
        store,
        session_id=SID,
        wa_message_id="wamid.OUT5",
        sent_at_ms=_ms("2026-09-20T10:00:00"),
    )
    use_case = IngestDeliveryStatus(
        metadata_store=store,
        rate_card_for=get_current_rate_card,
        event_bus=None,
        vault_dir=tmp_path,
        retry_delays=(0.001,),
    )
    with caplog.at_level(logging.WARNING):
        await use_case.execute(
            wa_message_id="wamid.OUT5",
            status="delivered",
            pricing={
                "billable": False,
                "type": "free_customer_service",
                "category": "utility",
            },
        )
    assert not any(
        "free_pricing_on_billable_category" in r.getMessage() for r in caplog.records
    )


@pytest.mark.asyncio
async def test_ventana_gratis_del_anuncio_no_dispara_el_tripwire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    # `free_entry_point` (72h del anuncio) SIGUE siendo gratis post-oct: no es ruido.
    import logging

    monkeypatch.delenv("WHATSAPP_RATE_CARD_VERSION", raising=False)
    store = FilesystemMetadataStore(tmp_path)
    _seed_episode_with_pending_entry(
        store,
        session_id=SID,
        wa_message_id="wamid.OUT4",
        kind="text",
        template_name=None,
        sent_at_ms=_ms("2026-10-05T10:00:00"),
    )
    use_case = IngestDeliveryStatus(
        metadata_store=store,
        rate_card_for=get_current_rate_card,
        event_bus=None,
        vault_dir=tmp_path,
        retry_delays=(0.001,),
    )
    with caplog.at_level(logging.WARNING):
        await use_case.execute(
            wa_message_id="wamid.OUT4",
            status="delivered",
            pricing={"billable": False, "type": "free_entry_point", "category": "service"},
        )
    assert not any(
        "free_pricing_on_billable_category" in r.getMessage() for r in caplog.records
    )
