"""D1.4 — `IngestStandby`: el oído cuando MBA controla el hilo.

Verifica el vault REAL (gotcha 1): `metadata.json` (episodio, ventana, dedupe,
outbound pendiente de pricing) y el JSONL de la sesión (lo que el dashboard
lee). Nunca despacha a Temporal: el use case no recibe cliente ni factory.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.platform.constants import ROUTE_HUMANO
from src.platform.session_history import FilesystemMessageHistoryStore
from src.platform.state import FilesystemMetadataStore
from src.platform.whatsapp.cost import RateCard, RateCardEntry
from src.plugins.chats.agent.sales.parsers import parse_whatsapp_standby
from src.plugins.chats.agent.sales.use_cases.ingest_delivery_status import IngestDeliveryStatus
from src.plugins.chats.agent.sales.use_cases.ingest_standby import IngestStandby, StandbyIngestResult
from tests.plugins.chats import standby_payloads as P

SESSION = f"wa_{P.CUSTOMER}"
NOW_MS = 1_757_300_000_000


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    v.mkdir()
    return v


def _use_case(vault: Path, now_ms: int = NOW_MS, allowed=lambda customer: True) -> IngestStandby:
    return IngestStandby(
        metadata_store=FilesystemMetadataStore(vault),
        history_store=FilesystemMessageHistoryStore(vault),
        vault_dir=vault,
        now_ms=lambda: now_ms,
        is_customer_allowed=allowed,
    )


def _meta(vault: Path) -> dict[str, Any]:
    return json.loads((vault / SESSION / "metadata.json").read_text(encoding="utf-8"))


def _lines(vault: Path) -> list[dict[str, Any]]:
    p = vault / SESSION / "sessions" / f"{SESSION}.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


async def _run(vault: Path, body: dict[str, Any], now_ms: int = NOW_MS) -> StandbyIngestResult:
    return await _use_case(vault, now_ms).execute(parse_whatsapp_standby(body))


async def test_standby_inbound_opens_the_episode_and_writes_the_history(vault: Path) -> None:
    res = await _run(vault, P.inbound("hola, quiero una vela"))
    assert res.messages_persisted == 1 and res.duplicates == 0 and res.sessions == (SESSION,)
    m = _meta(vault)
    ep = m["episodes"][-1]
    assert ep["episode_id"] == "ep_001" and ep["closed_at_ms"] is None
    assert ep["started_inbound_message_id"] == "wamid.STANDBY.IN.1" and ep["msgs_count_at_start"] == 0
    assert m["last_inbound_at_ms"] == NOW_MS and m["service_window_expires_at_ms"] == NOW_MS + 24 * 3600 * 1000
    assert m["last_inbound_message_id"] == "wamid.STANDBY.IN.1"
    assert m["mba_standby"]["inbound_count"] == 1 and m["mba_standby"]["last_inbound_at_ms"] == NOW_MS
    assert "wamid.STANDBY.IN.1" in m["standby_seen_wamids"]
    assert "active_route" not in m or m["active_route"] != ROUTE_HUMANO
    lines = _lines(vault)
    assert len(lines) == 1
    assert lines[0]["role"] == "user" and lines[0]["content"] == "hola, quiero una vela"
    assert lines[0]["wamid"] == "wamid.STANDBY.IN.1" and lines[0]["timestamp"]


async def test_standby_echo_is_the_assistant_turn_sent_by_mba_and_a_pending_outbound(vault: Path) -> None:
    await _run(vault, P.inbound("hola"))
    res = await _run(vault, P.echo_text("Hola 🤍, ¿qué aroma buscas?"), now_ms=NOW_MS + 5_000)
    assert res.echoes_persisted == 1
    lines = _lines(vault)
    assert [line["role"] for line in lines] == ["user", "assistant"]
    echo = lines[1]
    assert echo["content"] == "Hola 🤍, ¿qué aroma buscas?" and echo["sender"] == "mba" and echo["wamid"] == "wamid.STANDBY.ECHO.1"
    m = _meta(vault)
    ep = m["episodes"][-1]
    entry = ep["outbound_messages"][-1]
    assert entry["wa_message_id"] == "wamid.STANDBY.ECHO.1" and entry["kind"] == "mba_text"
    assert entry["pricing"] is None and entry["cost_usd_micros"] is None
    assert entry["sent_at_ms"] == 1757300020 * 1000  # timestamp del echo, no el reloj local
    assert ep["cost_summary"]["messages_pending_count"] == 1 and ep["cost_summary"]["messages_count"] == 1
    assert m["last_outbound"]["wa_message_id"] == "wamid.STANDBY.ECHO.1"
    assert m["mba_standby"]["echo_count"] == 1


async def test_standby_status_materializes_the_cost_of_an_mba_message(vault: Path) -> None:
    """E2E echo → status con `pricing` (clave `type` como la doc de Meta) →
    `cost_summary` del episodio, por el mismo `IngestDeliveryStatus` de hoy."""
    await _run(vault, P.inbound())
    await _run(vault, P.echo_text())
    rate_card = RateCard(version="co_test", effective_from_ms=0, country="CO", currency="USD",
                         rates={"utility": RateCardEntry(usd_micros_per_message=800),
                                "marketing": RateCardEntry(usd_micros_per_message=12500)})

    async def no_sleep(_: float) -> None:
        return None

    status_uc = IngestDeliveryStatus(metadata_store=FilesystemMetadataStore(vault), rate_card=rate_card, event_bus=None,
                                     vault_dir=vault, sleeper=no_sleep, retry_delays=(0.001,), tenant_id="t")
    st = parse_whatsapp_standby(P.status()).statuses[0]
    await status_uc.execute(st.wa_message_id, st.status, st.pricing)
    ep = _meta(vault)["episodes"][-1]
    entry = ep["outbound_messages"][-1]
    assert entry["cost_usd_micros"] == 800 and entry["pricing"]["pricing_type"] == "regular"
    assert ep["cost_summary"]["total_usd_micros"] == 800 and ep["cost_summary"]["messages_pending_count"] == 0
    assert ep["cost_summary"]["by_category"]["utility"] == {"count": 1, "usd_micros": 800}


async def test_standby_events_are_deduplicated_by_wamid(vault: Path) -> None:
    await _run(vault, P.inbound())
    await _run(vault, P.echo_text())
    res = await _run(vault, P.merged(P.inbound(), P.echo_text()))
    assert res.duplicates == 2 and res.messages_persisted == 0 and res.echoes_persisted == 0
    assert len(_lines(vault)) == 2
    m = _meta(vault)
    assert len(m["episodes"][-1]["outbound_messages"]) == 1 and m["mba_standby"]["inbound_count"] == 1


async def test_standby_inbound_with_a_human_in_the_thread_is_recorded_without_touching_the_episode(vault: Path) -> None:
    await _run(vault, P.inbound())
    store = FilesystemMetadataStore(vault)
    store.update(SESSION, lambda d: {**d, "active_route": ROUTE_HUMANO, "tag": "HUMANO",
                                     "episodes": [{**d["episodes"][0], "closed_at_ms": NOW_MS, "closing_tag": "RECHAZO"}]})
    await _run(vault, P.inbound("sigo aquí", wamid="wamid.STANDBY.IN.2"), now_ms=NOW_MS + 60_000)
    m = _meta(vault)
    assert len(m["episodes"]) == 1 and m["tag"] == "HUMANO" and m["active_route"] == ROUTE_HUMANO
    assert m["last_inbound_at_ms"] == NOW_MS + 60_000
    assert [line["content"] for line in _lines(vault)] == ["Test standby message", "sigo aquí"]


async def test_standby_inbound_after_a_closed_episode_opens_a_new_one_with_the_referral(vault: Path) -> None:
    await _run(vault, P.inbound())
    FilesystemMetadataStore(vault).update(
        SESSION, lambda d: {**d, "tag": "RECHAZO",
                            "episodes": [{**d["episodes"][0], "closed_at_ms": NOW_MS, "closing_tag": "RECHAZO"}]})
    await _run(vault, P.inbound_with_referral(), now_ms=NOW_MS + 3_600_000)
    m = _meta(vault)
    assert len(m["episodes"]) == 2
    ep = m["episodes"][-1]
    assert ep["episode_id"] == "ep_002" and ep["started_inbound_message_id"] == "wamid.STANDBY.IN.AD"
    assert ep["referral_snapshot"]["source_id"] == "AD_1" and ep["msgs_count_at_start"] == 1
    assert m["tag"] == "NO_ETIQUETADO"
    assert m["ctwa_referrals"][-1]["ctwa_clid"] == "CLID_1" and m["ctwa_clids_seen"] == ["CLID_1"]


async def test_standby_echo_without_an_episode_is_still_recorded(vault: Path) -> None:
    """MBA puede hablar primero (p.ej. tras un release nuestro): el eco se
    guarda en el historial; sin episodio activo no hay dónde anotar el costo."""
    res = await _run(vault, P.echo_template())
    assert res.echoes_persisted == 1
    lines = _lines(vault)
    assert lines[0]["role"] == "assistant" and lines[0]["sender"] == "mba" and lines[0]["content"] == "[plantilla summer_sale_2026]"
    m = _meta(vault)
    assert m.get("episodes", []) == [] and m["last_outbound"]["kind"] == "mba_template"
    assert m["last_outbound"]["template_name"] == "summer_sale_2026"


async def test_standby_non_text_inbound_is_described_not_dropped(vault: Path) -> None:
    await _run(vault, P.inbound_image())
    assert _lines(vault)[0]["content"] == "[imagen] mi comprobante"


def test_ingest_standby_has_no_way_to_reach_temporal() -> None:
    """R-DIP + D1.4: el módulo no importa temporal ni el dispatcher; un
    standby jamás arranca ni señaliza un workflow."""
    import inspect

    from src.plugins.chats.agent.sales.use_cases import ingest_standby

    source = inspect.getsource(ingest_standby)
    for forbidden in ("temporalio", "get_temporal_client", "start_workflow", "signal_with_start", "eventkit", "dispatch"):
        assert forbidden not in source, forbidden


def test_the_human_route_literal_matches_the_platform_constant() -> None:
    from src.plugins.chats.agent.sales.use_cases import ingest_standby

    assert ingest_standby.ROUTE_HUMANO == ROUTE_HUMANO


async def test_referral_without_click_id_does_not_enter_ctwa_referrals(vault: Path) -> None:
    """Contrato HU-002: CAPI atribuye por `ctwa_referrals[-1]`; un touch
    web/direct sin `ctwa_clid` no pisa al último anuncio (sí queda en el
    snapshot del episodio)."""
    body = P.inbound("vengo de la web", wamid="wamid.STANDBY.IN.WEB")
    body["entry"][0]["changes"][0]["value"]["standby"]["messages"][0]["referral"] = {
        "source_url": "https://hubara.co", "source_type": "post", "headline": "web"}
    await _run(vault, body)
    m = _meta(vault)
    assert "ctwa_referrals" not in m and "ctwa_clids_seen" not in m
    assert m["episodes"][-1]["referral_snapshot"]["source_type"] == "post"


async def test_a_failed_history_append_does_not_mark_the_wamid_as_seen(vault: Path) -> None:
    """Si el JSONL no se pudo escribir, nada queda escrito y la reentrega de
    Meta vuelve a procesar el mensaje (no se pierde por el dedupe)."""

    class _Broken:
        def append_user_event(self, *a: Any, **k: Any) -> None:
            raise OSError("disk full")

        def append_assistant_event(self, *a: Any, **k: Any) -> None:
            raise OSError("disk full")

    uc = IngestStandby(metadata_store=FilesystemMetadataStore(vault), history_store=_Broken(), vault_dir=vault,
                       now_ms=lambda: NOW_MS, is_customer_allowed=lambda customer: True)
    with pytest.raises(OSError):
        await uc.execute(parse_whatsapp_standby(P.inbound()))
    assert not (vault / SESSION / "metadata.json").exists() or "standby_seen_wamids" not in _meta(vault)
    res = await _run(vault, P.inbound())
    assert res.messages_persisted == 1 and len(_lines(vault)) == 1


async def test_customers_outside_the_closed_list_are_rejected_and_nothing_is_written(vault: Path) -> None:
    """Lista cerrada del lado de Hubara: si Meta manda `standby` de un cliente
    que no habilitamos, no se persiste nada (y el log de ERROR es la alarma de
    que el rollout en Meta está más abierto de lo previsto)."""
    uc = _use_case(vault, allowed=lambda customer: customer == "573009876543")
    res = await uc.execute(parse_whatsapp_standby(P.merged(P.inbound(), P.echo_text())))
    assert res.rejected == 2 and res.messages_persisted == 0 and res.echoes_persisted == 0
    assert not (vault / SESSION).exists()
    uc = _use_case(vault, allowed=lambda customer: customer == P.CUSTOMER)
    res = await uc.execute(parse_whatsapp_standby(P.inbound()))
    assert res.rejected == 0 and res.messages_persisted == 1
