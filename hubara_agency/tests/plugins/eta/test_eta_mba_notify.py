"""D1.9 — con Meta Business Agent al frente, la notificación de estado del
pedido NO la manda Hubara (tomaría el hilo): el ETA le cuenta la novedad a
MBA por `agent_event` (endpoint del plugin mba, identidad de servicio) y MBA
se la transmite al cliente. El `claim` reserva el stage como notificado y
devuelve `None` (el workflow no envía nada). Si MBA no puede recibirlo, el
ETA notifica como siempre (el cliente no se queda sin aviso).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastapi import HTTPException
from temporalio.testing import ActivityEnvironment

from src.plugins.eta.agent.eta.activities import tracking
from src.plugins.eta.agent.eta.activities.mba_notify import api_base_url, notify_via_mba
from src.plugins.eta.agent.eta.prompts import render_stage_notification
from tests.plugins.eta.test_eta_agent import ORDER, SID, _fake_detail, _read_meta, _write_meta


def test_the_api_base_is_the_compose_service_name_unless_overridden(monkeypatch) -> None:
    monkeypatch.delenv("HUBARA_API_BASE_URL", raising=False)
    assert api_base_url() == "http://hubara-api:8000"
    monkeypatch.setenv("HUBARA_API_BASE_URL", "http://localhost:8000/")
    assert api_base_url() == "http://localhost:8000"


@respx.mock
async def test_notify_via_mba_posts_the_event_to_the_mba_plugin_with_the_service_identity(monkeypatch) -> None:
    from src.platform import config

    monkeypatch.setenv("HUBARA_API_BASE_URL", "http://api.test")
    monkeypatch.setattr(config, "HUBARA_SERVICE_TOKEN", "svc-token")
    route = respx.post(f"http://api.test/api/mba/sessions/{SID}/agent-events").mock(
        return_value=httpx.Response(200, json={"session_key": SID, "emitted": True, "reason": "accepted",
                                               "agent_event_id": "AE_1", "at_ms": 1, "error": None, "recorded": True})
    )
    out = await notify_via_mba(SID, event_type="order_shipped", order_id=ORDER, message="va en camino",
                               payload={"stage": "shipping"})
    assert out["emitted"] is True and out["agent_event_id"] == "AE_1"
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer svc-token"
    assert json.loads(req.content) == {"type": "order_shipped", "order_id": ORDER, "message": "va en camino",
                                       "payload": {"stage": "shipping"}, "source": "eta"}


# ── el hook en claim_eta_notification_activity ───────────────────────────────


class _Port:
    def __init__(self, **summary: Any) -> None:
        self._summary = summary

    async def get(self, oid: str):
        return _fake_detail(**self._summary)


class _Notify:
    def __init__(self, response: dict[str, Any] | None = None, raises: Exception | None = None) -> None:
        self.response, self.raises, self.calls = response, raises, []

    async def __call__(self, session_id: str, **kw: Any) -> dict[str, Any]:
        self.calls.append((session_id, kw))
        if self.raises is not None:
            raise self.raises
        return dict(self.response or {})


def _arm(monkeypatch, vault: Path, *, controls: bool, notify: _Notify, pay_status: str = "paid",
         service_window: bool = False) -> None:
    meta: dict[str, Any] = {"active_route": "humano", "tag": "COMPRA_EXITOSA", "control_owner": "mba",
                            "eta_tracking": {"orders": {ORDER: {"order_id": ORDER, "notified_stages": [], "events": []}}}}
    if service_window:
        meta["service_window_expires_at_ms"] = 4_000_000_000_000
    _write_meta(vault, SID, meta)
    monkeypatch.setattr("src.platform.orders.composition.get_order_query_port",
                        lambda: _Port(customer="Ana Ruiz", display_id="#1247", total_cop=124500,
                                      pay_type="confirmed", pay_status=pay_status))
    monkeypatch.setattr(tracking, "mba_controls_thread", lambda metadata, session_id: controls)
    monkeypatch.setattr(tracking, "notify_via_mba", notify)


async def test_with_mba_in_front_the_stage_goes_to_mba_and_hubara_sends_nothing(_isolate_vault_dir: Path, monkeypatch) -> None:
    notify = _Notify({"emitted": True, "reason": "accepted", "agent_event_id": "AE_1"})
    _arm(monkeypatch, _isolate_vault_dir, controls=True, notify=notify, service_window=True)
    facts = await ActivityEnvironment().run(tracking.claim_eta_notification_activity, SID, ORDER, "shipping")
    assert facts is None  # el workflow no envía texto ni template
    (sid, kw), = notify.calls
    assert sid == SID and kw["event_type"] == "order_shipped" and kw["order_id"] == ORDER
    assert kw["message"] == render_stage_notification(
        stage="shipping", customer_name="Ana", order_display_id="#1247", total_label="$ 124.500",
        pay_type="confirmed", payment_confirmed=True, delivery_window=None, items_label="",
    )
    assert kw["payload"] == {"order_id": ORDER, "stage": "shipping", "order_display_id": "#1247",
                             "total_label": "$ 124.500", "pay_type": "confirmed", "payment_confirmed": True,
                             "items_label": "", "tracking_url": None}
    entry = _read_meta(_isolate_vault_dir, SID)["eta_tracking"]["orders"][ORDER]
    assert entry["notified_stages"] == ["shipping"] and entry["current_stage"] == "shipping"
    assert "agent_event order_shipped" in entry["events"][-1]["agent_msg"] and "AE_1" in entry["events"][-1]["agent_msg"]
    # segunda entrega del mismo evento → dedup por notified_stages, MBA no se vuelve a consultar
    assert await ActivityEnvironment().run(tracking.claim_eta_notification_activity, SID, ORDER, "shipping") is None
    assert len(notify.calls) == 1


async def test_preparing_is_payment_received_only_when_the_payment_is_really_confirmed(_isolate_vault_dir: Path, monkeypatch) -> None:
    notify = _Notify({"emitted": True, "reason": "accepted", "agent_event_id": "AE_1"})
    _arm(monkeypatch, _isolate_vault_dir, controls=True, notify=notify, pay_status="paid")
    await ActivityEnvironment().run(tracking.claim_eta_notification_activity, SID, ORDER, "preparing")
    assert notify.calls[-1][1]["event_type"] == "payment_received"
    _arm(monkeypatch, _isolate_vault_dir, controls=True, notify=notify, pay_status="pending")
    await ActivityEnvironment().run(tracking.claim_eta_notification_activity, SID, ORDER, "preparing")
    assert notify.calls[-1][1]["event_type"] == "order_preparing"


async def test_with_hubara_in_front_mba_is_not_consulted_and_the_claim_is_as_before(_isolate_vault_dir: Path, monkeypatch) -> None:
    notify = _Notify({"emitted": True})
    _arm(monkeypatch, _isolate_vault_dir, controls=False, notify=notify)
    facts = await ActivityEnvironment().run(tracking.claim_eta_notification_activity, SID, ORDER, "shipping")
    assert facts is not None and facts["order_display_id"] == "#1247" and notify.calls == []
    assert _read_meta(_isolate_vault_dir, SID)["eta_tracking"]["orders"][ORDER]["notified_stages"] == []


async def test_the_real_predicate_is_false_with_the_flag_off_so_nothing_changes_in_prod(_isolate_vault_dir: Path, monkeypatch) -> None:
    notify = _Notify({"emitted": True})
    _arm(monkeypatch, _isolate_vault_dir, controls=True, notify=notify)
    monkeypatch.delattr(tracking, "mba_controls_thread")  # vuelve al predicado real (flag OFF por default)
    from src.sdk.runtime import mba_controls_thread

    monkeypatch.setattr(tracking, "mba_controls_thread", mba_controls_thread, raising=False)
    facts = await ActivityEnvironment().run(tracking.claim_eta_notification_activity, SID, ORDER, "shipping")
    assert facts is not None and notify.calls == []


@pytest.mark.parametrize("response", [
    {"emitted": False, "reason": "rejected", "error": "400 Unknown entity"},
    {"emitted": False, "reason": "unavailable"},
    {"emitted": False, "reason": "not_configured"},
    {"emitted": False, "reason": "hubara_controls"},
    {"emitted": False, "reason": "entity_id_missing"},
])
async def test_if_mba_cannot_take_the_event_hubara_notifies_as_always(_isolate_vault_dir: Path, monkeypatch, response) -> None:
    notify = _Notify(response)
    _arm(monkeypatch, _isolate_vault_dir, controls=True, notify=notify)
    facts = await ActivityEnvironment().run(tracking.claim_eta_notification_activity, SID, ORDER, "shipping")
    assert facts is not None and facts["order_display_id"] == "#1247"
    assert _read_meta(_isolate_vault_dir, SID)["eta_tracking"]["orders"][ORDER]["notified_stages"] == []


@pytest.mark.parametrize("response", [
    {"emitted": False, "reason": "already_emitted", "agent_event_id": "AE_0"},
    {"emitted": False, "reason": "ambiguous", "error": "ReadTimeout"},
])
async def test_an_event_mba_may_already_have_is_not_repeated_by_hubara(_isolate_vault_dir: Path, monkeypatch, response) -> None:
    _arm(monkeypatch, _isolate_vault_dir, controls=True, notify=_Notify(response))
    assert await ActivityEnvironment().run(tracking.claim_eta_notification_activity, SID, ORDER, "shipping") is None
    entry = _read_meta(_isolate_vault_dir, SID)["eta_tracking"]["orders"][ORDER]
    assert entry["notified_stages"] == ["shipping"] and response["reason"] in entry["events"][-1]["agent_msg"]
    # ambiguo = Meta no respondió al plugin mba: el cliente PUEDE no haber recibido el aviso → visible en el timeline
    ambiguous = response["reason"] == "ambiguous"
    assert (entry["events"][-1]["flagged"], entry["events"][-1]["flag"]) == (ambiguous, "mba_ambiguous" if ambiguous else None)


async def test_the_shipping_event_carries_the_tracking_link_the_workflow_passes(_isolate_vault_dir: Path, monkeypatch) -> None:
    notify = _Notify({"emitted": True, "reason": "accepted", "agent_event_id": "AE_1"})
    _arm(monkeypatch, _isolate_vault_dir, controls=True, notify=notify)
    url = "https://guia.test/?g=123"
    assert await ActivityEnvironment().run(tracking.claim_eta_notification_activity, SID, ORDER, "shipping", url) is None
    kw = notify.calls[-1][1]
    assert kw["payload"]["tracking_url"] == url and url in kw["message"]
    assert kw["message"] == render_stage_notification(
        stage="shipping", customer_name="Ana", order_display_id="#1247", total_label="$ 124.500",
        pay_type="confirmed", payment_confirmed=True, delivery_window=None, items_label="", tracking_url=url,
    )


async def test_writes_made_by_others_during_the_hop_survive_the_claim(_isolate_vault_dir: Path, monkeypatch) -> None:
    """Revisión D1.9 (HIGH): el ETA reservaba el stage escribiendo el
    metadata.json ENTERO leído antes del hop → borraba `agent_events` que el
    plugin mba acababa de anotar bajo lock, y cualquier inbound/handover de
    esos segundos. Ahora la reserva es un update bajo el flock que toca solo
    `eta_tracking`."""
    from src.sdk.runtime import FilesystemMetadataStore

    store = FilesystemMetadataStore(_isolate_vault_dir)

    class _WritesLikeMba(_Notify):
        async def __call__(self, session_id: str, **kw: Any) -> dict[str, Any]:
            def _mba(data: dict[str, Any]) -> dict[str, Any]:
                data["agent_events"] = [{"type": "order_shipped", "order_id": ORDER, "status": "accepted"}]
                return data

            def _inbound(data: dict[str, Any]) -> dict[str, Any]:
                data["last_inbound_at_ms"] = 123
                data["control_owner"] = "mba"
                return data

            store.update(session_id, _mba)
            store.update(session_id, _inbound)
            return await super().__call__(session_id, **kw)

    _arm(monkeypatch, _isolate_vault_dir, controls=True,
         notify=_WritesLikeMba({"emitted": True, "reason": "accepted", "agent_event_id": "AE_1"}))
    assert await ActivityEnvironment().run(tracking.claim_eta_notification_activity, SID, ORDER, "shipping") is None
    meta = _read_meta(_isolate_vault_dir, SID)
    assert meta["agent_events"] == [{"type": "order_shipped", "order_id": ORDER, "status": "accepted"}]
    assert meta["last_inbound_at_ms"] == 123 and meta["control_owner"] == "mba"
    assert meta["eta_tracking"]["orders"][ORDER]["notified_stages"] == ["shipping"]


async def test_a_slow_order_query_falls_back_to_minimal_facts_within_the_activity_budget(_isolate_vault_dir: Path, monkeypatch) -> None:
    """Medusa tiene 30 s de timeout HTTP, igual que el start_to_close del
    claim: con el hop a mba (15 s) encima, la activity vencería con el POST en
    vuelo y Temporal la reintentaría en paralelo. El fetch va acotado."""
    import asyncio

    notify = _Notify({"emitted": True, "reason": "accepted", "agent_event_id": "AE_1"})
    _arm(monkeypatch, _isolate_vault_dir, controls=True, notify=notify)

    class _SlowPort:
        async def get(self, oid: str):
            await asyncio.sleep(0.5)
            return _fake_detail(customer="Ana", display_id="#1247", total_cop=1, pay_type="confirmed", pay_status="paid")

    monkeypatch.setattr("src.platform.orders.composition.get_order_query_port", lambda: _SlowPort())
    monkeypatch.setattr(tracking, "_ORDER_FETCH_TIMEOUT_S", 0.05)
    assert await ActivityEnvironment().run(tracking.claim_eta_notification_activity, SID, ORDER, "shipping") is None
    kw = notify.calls[-1][1]
    assert kw["payload"]["order_display_id"] == ORDER and kw["payload"]["payment_confirmed"] is False


@pytest.mark.parametrize("status", [502, 503, 403, 404])
async def test_if_the_api_refused_or_was_down_before_meta_hubara_notifies_as_always(_isolate_vault_dir: Path, monkeypatch, status) -> None:
    _arm(monkeypatch, _isolate_vault_dir, controls=True, notify=_Notify(raises=HTTPException(status_code=status, detail="x")))
    facts = await ActivityEnvironment().run(tracking.claim_eta_notification_activity, SID, ORDER, "shipping")
    assert facts is not None
    assert _read_meta(_isolate_vault_dir, SID)["eta_tracking"]["orders"][ORDER]["notified_stages"] == []


@pytest.mark.parametrize("status", [504, 500])
async def test_an_unknown_outcome_is_raised_so_temporal_retries_the_claim(_isolate_vault_dir: Path, monkeypatch, status) -> None:
    """504 (el API no respondió a tiempo) o 500: el evento PUDO llegar a Meta.
    Ni enviar (duplicaría y tomaría el hilo) ni callar: la activity falla y
    el retry vuelve a preguntar — el dedupe de mba (`already_emitted`) hace
    el reintento seguro."""
    _arm(monkeypatch, _isolate_vault_dir, controls=True, notify=_Notify(raises=HTTPException(status_code=status, detail="x")))
    with pytest.raises(HTTPException):
        await ActivityEnvironment().run(tracking.claim_eta_notification_activity, SID, ORDER, "shipping")
    assert _read_meta(_isolate_vault_dir, SID)["eta_tracking"]["orders"][ORDER]["notified_stages"] == []


def test_the_eta_vocabulary_is_a_subset_of_the_mba_catalogue() -> None:
    """Guard de deriva entre plugins (eta no puede importar mba en src)."""
    from src.plugins.eta.agent.eta.activities.mba_notify import event_type_for_stage
    from src.plugins.mba.domain.agent_events import AGENT_EVENT_TYPES, agent_event_type_for_stage

    for stage in ("preparing", "ready", "shipping", "delivered", "cancelled", "new"):
        for paid in (True, False):
            ours = event_type_for_stage(stage, payment_confirmed=paid)
            assert ours == agent_event_type_for_stage(stage, payment_confirmed=paid)
            assert ours is None or ours in AGENT_EVENT_TYPES
