"""Tests del sub-agente ETA (notificaciones de estado de pedido).

Cubre las tres capas deterministas (la generación del mensaje por el LLM es
integración / E2E manual):

  * **Prompts puros**: el trigger sintético elige el hint correcto por tipo de
    pago e incluye los slots.
  * **Activities de tracking** (vía ``ActivityEnvironment``): el state machine de
    ``metadata.eta_tracking`` — route+tag, dedup, route-guard, timeline.
  * **Dashboard API**: el shaping ``eta_tracking`` → ``TrackedOrder`` que consume
    el frontend (incl. el behavior test del gotcha #1: la notificación enviada
    queda visible en el timeline que sirve el endpoint).
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from temporalio.testing import ActivityEnvironment

from src.plugins.chats.agent.eta.activities import (
    bootstrap_eta_session_activity,
    claim_eta_notification_activity,
    record_eta_notification_activity,
    record_eta_reply_activity,
    start_eta_tracking_activity,
)
from src.plugins.chats.agent.eta.contracts import EtaSessionInput
from src.plugins.chats.agent.eta.prompts import build_stage_notification_turn


SID = "wa_573001112233"
ORDER = "order_01TESTETA"


def _write_meta(vault: Path, sid: str, data: dict) -> None:
    d = vault / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(json.dumps(data), encoding="utf-8")


def _read_meta(vault: Path, sid: str) -> dict:
    return json.loads((vault / sid / "metadata.json").read_text(encoding="utf-8"))


def _fake_detail(**summary):
    return SimpleNamespace(summary=SimpleNamespace(**summary))


# ════════════════════════════════════════════════════════════════════════
# Prompts puros
# ════════════════════════════════════════════════════════════════════════
def test_prompt_cod_recuerda_monto():
    msg = build_stage_notification_turn(
        stage="shipping",
        customer_name="Daniela",
        order_display_id="#1243",
        total_label="$ 215.000",
        pay_type="cod",
        delivery_window=None,
    )
    assert "En camino" in msg
    assert "Daniela" in msg and "#1243" in msg and "$ 215.000" in msg
    assert "contra entrega" in msg  # hint COD
    assert "aún no definida" in msg  # ventana None


def test_prompt_confirmed_recuerda_pagado():
    msg = build_stage_notification_turn(
        stage="preparing",
        customer_name="Carlos",
        order_display_id="#1246",
        total_label="$ 78.000",
        pay_type="confirmed",
        delivery_window="hoy 11:00-14:00",
    )
    assert "En preparación" in msg
    assert "pago" in msg.lower() and "confirmado" in msg.lower()
    assert "hoy 11:00-14:00" in msg


# ════════════════════════════════════════════════════════════════════════
# Activities — tracking state machine
# ════════════════════════════════════════════════════════════════════════
async def test_start_tracking_sets_route_tag_and_inits(_isolate_vault_dir: Path):
    await ActivityEnvironment().run(start_eta_tracking_activity, SID, ORDER)
    meta = _read_meta(_isolate_vault_dir, SID)
    assert meta["active_route"] == "eta"
    assert meta["tag"] == "ETA"  # route↔tag invariante: sale de la bandeja humana
    tr = meta["eta_tracking"]
    assert tr["order_id"] == ORDER
    assert tr["notified_stages"] == []
    assert tr["events"] == []


async def test_start_tracking_resets_on_different_order(_isolate_vault_dir: Path):
    _write_meta(
        _isolate_vault_dir, SID,
        {"eta_tracking": {"order_id": "order_OLD", "notified_stages": ["preparing"], "events": [{"stage": "preparing"}]}},
    )
    await ActivityEnvironment().run(start_eta_tracking_activity, SID, ORDER)
    tr = _read_meta(_isolate_vault_dir, SID)["eta_tracking"]
    assert tr["order_id"] == ORDER
    assert tr["notified_stages"] == []  # pedido nuevo → tracking fresco
    assert tr["events"] == []


async def test_claim_returns_facts_happy_path(_isolate_vault_dir: Path, monkeypatch):
    _write_meta(_isolate_vault_dir, SID, {"active_route": "eta", "eta_tracking": {"order_id": ORDER, "notified_stages": []}})

    class _Port:
        async def get(self, oid):
            assert oid == ORDER
            return _fake_detail(customer="María Camila Restrepo", display_id="#1247", total_cop=124500, pay_type="confirmed")

    monkeypatch.setattr("src.platform.orders.composition.get_order_query_port", lambda: _Port())
    facts = await ActivityEnvironment().run(claim_eta_notification_activity, SID, ORDER, "ready")
    assert facts is not None
    assert facts["customer_name"] == "María"  # primer nombre
    assert facts["order_display_id"] == "#1247"
    assert facts["total_label"] == "$ 124.500"
    assert facts["pay_type"] == "confirmed"
    # sin `service_window_expires_at_ms` → ventana cerrada → el workflow usará template
    assert facts["in_service_window"] is False


async def test_claim_reports_service_window_state(_isolate_vault_dir: Path):
    """El flag `in_service_window` decide texto-libre (LLM) vs template. Sin
    monkeypatch del port: Medusa no configurado → claim cae al fallback (detail
    None) pero IGUAL reporta la ventana — el camino fuera-de-ventana no depende
    de Medusa."""
    import time as _t

    # Ventana ABIERTA: el cliente escribió hace poco (expira en el futuro).
    _write_meta(
        _isolate_vault_dir, SID,
        {"active_route": "eta", "service_window_expires_at_ms": int(_t.time() * 1000) + 3_600_000,
         "eta_tracking": {"order_id": ORDER, "notified_stages": []}},
    )
    facts = await ActivityEnvironment().run(claim_eta_notification_activity, SID, ORDER, "ready")
    assert facts is not None and facts["in_service_window"] is True

    # Ventana CERRADA: sin el campo (caso común — el pedido se mueve días después).
    _write_meta(
        _isolate_vault_dir, SID,
        {"active_route": "eta", "eta_tracking": {"order_id": ORDER, "notified_stages": []}},
    )
    facts2 = await ActivityEnvironment().run(claim_eta_notification_activity, SID, ORDER, "ready")
    assert facts2 is not None and facts2["in_service_window"] is False


async def test_claim_skips_when_route_humano(_isolate_vault_dir: Path):
    _write_meta(_isolate_vault_dir, SID, {"active_route": "humano", "eta_tracking": {"order_id": ORDER, "notified_stages": []}})
    facts = await ActivityEnvironment().run(claim_eta_notification_activity, SID, ORDER, "ready")
    assert facts is None  # humano tomó la conversación → no notificar


async def test_claim_dedups_already_notified(_isolate_vault_dir: Path):
    _write_meta(_isolate_vault_dir, SID, {"active_route": "eta", "eta_tracking": {"order_id": ORDER, "notified_stages": ["ready"]}})
    facts = await ActivityEnvironment().run(claim_eta_notification_activity, SID, ORDER, "ready")
    assert facts is None


async def test_claim_skips_on_order_mismatch(_isolate_vault_dir: Path):
    _write_meta(_isolate_vault_dir, SID, {"active_route": "eta", "eta_tracking": {"order_id": ORDER, "notified_stages": []}})
    facts = await ActivityEnvironment().run(claim_eta_notification_activity, SID, "order_OTHER", "ready")
    assert facts is None  # el tracking sigue otro pedido


async def test_record_notification_appends_event_and_dedups(_isolate_vault_dir: Path):
    _write_meta(_isolate_vault_dir, SID, {"eta_tracking": {"order_id": ORDER, "notified_stages": [], "events": []}})
    await ActivityEnvironment().run(record_eta_notification_activity, SID, "preparing", "¡Hola! Tu pedido entró en preparación.")
    tr = _read_meta(_isolate_vault_dir, SID)["eta_tracking"]
    assert "preparing" in tr["notified_stages"]
    assert tr["current_stage"] == "preparing"
    assert len(tr["events"]) == 1
    assert tr["events"][0]["agent_msg"].startswith("¡Hola!")
    assert tr["events"][0]["stage"] == "preparing"


async def test_record_reply_attaches_to_last_event_with_flag(_isolate_vault_dir: Path):
    _write_meta(
        _isolate_vault_dir, SID,
        {"eta_tracking": {"order_id": ORDER, "notified_stages": ["shipping"],
                          "events": [{"stage": "shipping", "agent_msg": "Va en camino", "reply": None, "flagged": False}]}},
    )
    await ActivityEnvironment().run(
        record_eta_reply_activity,
        SID,
        "¿puedo cambiar la dirección?",
        True,
        "Cliente pidió cambio de dirección",
    )
    ev = _read_meta(_isolate_vault_dir, SID)["eta_tracking"]["events"][-1]
    assert ev["reply"] == "¿puedo cambiar la dirección?"
    assert ev["flagged"] is True
    assert ev["flag"] == "Cliente pidió cambio de dirección"


async def test_bootstrap_falls_back_to_local_workspace(_isolate_vault_dir: Path):
    from src.plugins.chats.agent.eta.config.env import get_workspace_path

    result = await ActivityEnvironment().run(
        bootstrap_eta_session_activity, EtaSessionInput(session_id=SID, order_id=ORDER, to_stage="preparing")
    )
    assert result.session_id == SID
    assert result.channel == "whatsapp"
    assert Path(result.workspace.path).resolve() == Path(get_workspace_path()).resolve()


async def test_build_prompt_loads_eta_workspace_and_templates(_isolate_vault_dir: Path):
    """Behavior (gotcha #1 + cache): ``ContextBuilder`` arma el system prompt
    desde el workspace ETA SIN requerir ``skills/`` (ETA no lo tiene), y las
    **plantillas canónicas** de TOOLS.md quedan EN el system prompt (prefijo
    estable → cache-hit). Es la verificación de que el prompt del agente se
    construye de verdad, no solo que los archivos existen.
    """
    from exoclaw_temporal.activities.conversation import build_prompt
    from exoclaw_temporal.config import BuildPromptInput, WorkspaceConfig

    from src.platform.registries import build_default_llm_config
    from src.plugins.chats.agent.eta.config.env import get_workspace_path

    ws = WorkspaceConfig(path=str(get_workspace_path()))
    messages = await ActivityEnvironment().run(
        build_prompt,
        BuildPromptInput(
            session_id="wa_573000000000",
            message="hola",
            channel="whatsapp",
            chat_id="wa_573000000000",
            llm=build_default_llm_config(),
            workspace=ws,
            media=None,
            plugin_context=None,
        ),
    )
    system = "\n".join(
        str(m.get("content", "")) for m in messages if m.get("role") == "system"
    )
    assert system, "system prompt vacío — ContextBuilder no leyó el workspace ETA"
    assert "Asistente de Seguimiento" in system  # IDENTITY.md cargado
    assert "preparación" in system  # plantillas canónicas (TOOLS.md) presentes


# ════════════════════════════════════════════════════════════════════════
# Dashboard API — shaping eta_tracking → TrackedOrder
# ════════════════════════════════════════════════════════════════════════
def test_build_tracked_order_from_detail_maps_fields():
    from datetime import datetime

    from src.plugins.chats.api.eta import _build_tracked_order, _BOGOTA

    tracking = {
        "order_id": ORDER,
        "current_stage": "shipping",
        "events": [
            {"stage": "preparing", "agent_msg": "Entró en preparación", "at_ms": 1_780_000_000_000, "reply": "ok", "flagged": False, "flag": None},
            {"stage": "shipping", "agent_msg": "Va en camino", "at_ms": 1_780_100_000_000, "reply": "¿cambio dirección?", "flagged": True, "flag": "address"},
        ],
    }
    detail = _fake_detail(
        status="shipping", display_id="#1247", customer="María Camila", short="MR",
        color="a", city="Bogotá", channel="WhatsApp", pay_type="confirmed", total_cop=124500,
    )
    out = _build_tracked_order(SID, tracking, detail, now=datetime.now(_BOGOTA))
    assert out is not None
    assert out["id"] == "#1247"
    assert out["current"] == "shipping"
    assert out["payType"] == "confirmed"
    assert out["total"] == 124500
    assert out["needs"] is True  # hay un evento flagged
    assert out["events"][0]["agentMsg"] == "Entró en preparación"
    assert out["events"][1]["flagged"] is True


def test_build_tracked_order_cancelled_excluded():
    from datetime import datetime

    from src.plugins.chats.api.eta import _build_tracked_order, _BOGOTA

    tracking = {"order_id": ORDER, "current_stage": "cancelled", "events": []}
    detail = _fake_detail(
        status="cancelled", display_id="#9", customer="X", short="X", color="b",
        city="", channel="Web", pay_type="cod", total_cop=1000,
    )
    assert _build_tracked_order(SID, tracking, detail, now=datetime.now(_BOGOTA)) is None


async def test_list_endpoint_surfaces_sent_notification(_isolate_vault_dir: Path, monkeypatch):
    """Behavior (gotcha #1): el mensaje que el agente ENVIÓ queda visible en el
    timeline que la sección ETA del dashboard consume."""
    from src.plugins.chats.api import eta as eta_api

    _write_meta(
        _isolate_vault_dir, SID,
        {"active_route": "eta", "eta_tracking": {
            "order_id": ORDER, "current_stage": "preparing", "notified_stages": ["preparing"],
            "events": [{"stage": "preparing", "agent_msg": "¡Hola María! Tu pedido #1247 entró en preparación.", "at_ms": 1_780_000_000_000, "reply": None, "flagged": False, "flag": None}],
        }},
    )

    class _Port:
        async def get(self, oid):
            return _fake_detail(
                status="preparing", display_id="#1247", customer="María Camila", short="MR",
                color="a", city="Bogotá", channel="WhatsApp", pay_type="confirmed", total_cop=124500,
            )

    monkeypatch.setattr(eta_api, "get_order_query_port", lambda: _Port())
    resp = await eta_api.list_tracked_orders()
    assert resp["count"] == 1
    order = resp["orders"][0]
    assert order["id"] == "#1247"
    assert order["current"] == "preparing"
    assert order["events"][0]["agentMsg"].startswith("¡Hola María!")
