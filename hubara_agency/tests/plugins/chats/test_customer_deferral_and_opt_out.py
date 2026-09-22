"""Chats honra el aplazamiento del cliente y la baja de marketing.

Incidente (session wa_5730…, runs 337efe8c / ee3cec91, 2026-09-21):
«Sí, pero les escribo la otra semana» no se reconocía ni como aplazamiento
(`te escribo` sí, `les escribo` no) y la escalera siguió tocando hasta el
«No más». Además el watchdog (plantilla utility al cerrar la ventana 24h) no
miraba ni la baja ni el aplazamiento: un empujón de venta a quien pidió baja.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from src.platform.whatsapp.reengagement_deferral import (
    DEFERRAL_KEY,
    DEFERRAL_KIND_DATED,
)
from src.plugins.chats.agent.remarketing.activities.watchdog_activities import (
    check_watchdog_eligibility_activity,
)
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import (
    IngestInboundMessage,
)
from src.plugins.chats.shared.purchase_signals import detect_deferral

SESSION = "wa_573001234567"
H = 60 * 60 * 1000


@pytest.mark.parametrize(
    "text",
    [
        "Si, pero les escribo la otra semana",
        "Yo les escribo cuando vaya a comprar",
        "le aviso el jueves",
    ],
)
def test_les_escribo_tambien_es_aplazamiento(text: str):
    assert detect_deferral(text) is True


# ---------------------------------------------------------------------------
# Ingest: el inbound estampa la fecha de retoma
# ---------------------------------------------------------------------------


class _FakeHistoryStore:
    def append_user_event(self, session_id: str, content: str, **kw: Any) -> None:
        pass


class _FakeLoadOrStart:
    async def execute(self, *a: Any, **kw: Any) -> None:
        pass


class _FakeMetadataStore:
    def __init__(self) -> None:
        self.store: dict[str, dict] = {}

    def read(self, session_id: str) -> dict:
        return dict(self.store.get(session_id, {}))

    def write(self, session_id: str, data: dict) -> None:
        self.store[session_id] = dict(data)


def _msg(text: str, message_id: str) -> WhatsAppMessage:
    return WhatsAppMessage(
        message_id=message_id,
        from_number="573001234567",
        phone_number_id="PID",
        text=text,
        media=None,
        timestamp="1714312345",
    )


@pytest.mark.asyncio
async def test_el_ingest_estampa_la_pausa_del_cliente(_isolate_vault_dir):
    store = _FakeMetadataStore()
    use_case = IngestInboundMessage(
        history_store=_FakeHistoryStore(),  # type: ignore[arg-type]
        load_session=_FakeLoadOrStart(),  # type: ignore[arg-type]
        metadata_store=store,  # type: ignore[arg-type]
    )
    before = int(time.time() * 1000)
    await use_case.execute(_msg("Si, pero les escribo la otra semana", "wamid.A"))

    deferral = store.store[SESSION][DEFERRAL_KEY]
    assert deferral["kind"] == DEFERRAL_KIND_DATED
    # La semana que viene: entre 1 y 8 días desde hoy.
    assert before + 24 * H <= deferral["until_ms"] <= before + 8 * 24 * H


# ---------------------------------------------------------------------------
# Watchdog: el empujón de venta respeta la baja y la pausa
# ---------------------------------------------------------------------------


def _pin_clock_at_10am_bogota(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import datetime, timezone

    from src.plugins.chats.agent.remarketing.activities import watchdog_activities

    fixed = datetime(2026, 7, 7, 15, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(watchdog_activities, "_utc_now", lambda: fixed)


def _write(vault: Path, data: dict) -> None:
    target = vault / SESSION / "metadata.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data), encoding="utf-8")


def _watchdog_meta(now_ms: int) -> dict:
    return {
        "active_route": "ventas",
        "service_window_expires_at_ms": now_ms + 25 * 60 * 1000,
        "episodes": [
            {
                "episode_id": "ep_001",
                "started_at_ms": now_ms - 23 * H,
                "closed_at_ms": None,
                "closing_tag": None,
            }
        ],
        "tag": "INTERESADO",
        "motivo": "el cliente dudó del precio",
    }


@pytest.fixture
def _watchdog_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WATCHDOG_ENABLED", "1")
    _pin_clock_at_10am_bogota(monkeypatch)


@pytest.mark.asyncio
async def test_watchdog_no_empuja_la_venta_a_quien_pidio_la_baja(
    _watchdog_on, _isolate_vault_dir: Path
):
    now_ms = int(time.time() * 1000)
    md = _watchdog_meta(now_ms)
    md["marketing_opt_out"] = True
    _write(_isolate_vault_dir, md)

    result = await check_watchdog_eligibility_activity(SESSION, "ep_001")

    assert result.eligible is False
    assert result.reason == "marketing_opt_out"


@pytest.mark.asyncio
async def test_watchdog_no_empuja_la_venta_a_quien_aplazo(
    _watchdog_on, _isolate_vault_dir: Path
):
    now_ms = int(time.time() * 1000)
    md = _watchdog_meta(now_ms)
    md[DEFERRAL_KEY] = {
        "at_ms": now_ms - 22 * H,
        "until_ms": now_ms + 5 * 24 * H,
        "kind": DEFERRAL_KIND_DATED,
        "text": "les escribo la otra semana",
    }
    _write(_isolate_vault_dir, md)

    result = await check_watchdog_eligibility_activity(SESSION, "ep_001")

    assert result.eligible is False
    assert result.reason == "customer_deferred"


@pytest.mark.asyncio
async def test_watchdog_sigue_avisando_del_pedido_aunque_haya_baja(
    _watchdog_on, _isolate_vault_dir: Path
):
    # La baja es de PROMOCIONES: el aviso de un pedido que el cliente hizo
    # (pago pendiente) es servicio y sigue saliendo.
    now_ms = int(time.time() * 1000)
    md = _watchdog_meta(now_ms)
    md["marketing_opt_out"] = True
    md["registered_order"] = {"order_id": "ORD-1042", "success": True}
    _write(_isolate_vault_dir, md)

    result = await check_watchdog_eligibility_activity(SESSION, "ep_001")

    assert result.eligible is True
    assert result.resolved_template_name == "payment_pending_utility_v2"
