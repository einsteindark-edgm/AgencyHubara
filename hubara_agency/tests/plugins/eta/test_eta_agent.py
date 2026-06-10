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

from src.plugins.eta.agent.eta.activities import (
    all_trackings_terminal_activity,
    bootstrap_eta_session_activity,
    claim_eta_notification_activity,
    record_eta_notification_activity,
    start_eta_tracking_activity,
)
from src.plugins.eta.agent.eta.contracts import EtaSessionInput
from src.plugins.eta.agent.eta.prompts import build_stage_notification_turn


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
def _entry(meta: dict, order_id: str) -> dict:
    """Entry del pedido en el mapa multi-pedido (shape v2)."""
    return meta["eta_tracking"]["orders"][order_id]


async def test_start_tracking_does_not_touch_route_or_tag(_isolate_vault_dir: Path):
    """Convivencia ETA/Sales: el ETA es notificador puro — start NO toma el
    turno conversacional (antes seteaba active_route=eta + tag=ETA acá)."""
    _write_meta(_isolate_vault_dir, SID, {"active_route": "ventas", "tag": "EN_CURSO"})
    await ActivityEnvironment().run(start_eta_tracking_activity, SID, ORDER)
    meta = _read_meta(_isolate_vault_dir, SID)
    assert meta["active_route"] == "ventas"  # intacto
    assert meta["tag"] == "EN_CURSO"         # intacto
    tr = _entry(meta, ORDER)
    assert tr["order_id"] == ORDER
    assert tr["notified_stages"] == []
    assert tr["events"] == []


async def test_start_tracking_preserves_route_humano(_isolate_vault_dir: Path):
    """Bug fix: el start anterior pisaba incluso `humano` (el claim lo
    respetaba pero el start no — inconsistencia interna)."""
    _write_meta(_isolate_vault_dir, SID, {"active_route": "humano", "tag": "HUMANO"})
    await ActivityEnvironment().run(start_eta_tracking_activity, SID, ORDER)
    meta = _read_meta(_isolate_vault_dir, SID)
    assert meta["active_route"] == "humano"
    assert meta["tag"] == "HUMANO"


async def test_start_tracking_adds_second_order_without_reset(_isolate_vault_dir: Path):
    """Multi-pedido: un pedido nuevo NO resetea el tracking del anterior
    (antes lo descartaba y sus notificaciones quedaban 'stale')."""
    _write_meta(
        _isolate_vault_dir, SID,
        {"eta_tracking": {"order_id": "order_OLD", "notified_stages": ["preparing"], "events": [{"stage": "preparing"}]}},
    )
    await ActivityEnvironment().run(start_eta_tracking_activity, SID, ORDER)
    meta = _read_meta(_isolate_vault_dir, SID)
    orders = meta["eta_tracking"]["orders"]
    assert set(orders) == {"order_OLD", ORDER}
    assert orders["order_OLD"]["notified_stages"] == ["preparing"]  # intacto (migrado v1→v2)
    assert orders[ORDER]["notified_stages"] == []


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


async def test_claim_notifies_even_when_route_humano(_isolate_vault_dir: Path):
    """L-6: la notificación es push informativo — NO depende del turno. Toda
    venta exitosa termina en route=humano (verificación de pago, terminal);
    el guard viejo bloqueaba las notificaciones de TODOS los pedidos vendidos
    (run 19ee6679: mover preparing→ready no notificó nada)."""
    _write_meta(_isolate_vault_dir, SID, {"active_route": "humano", "eta_tracking": {"order_id": ORDER, "notified_stages": []}})
    facts = await ActivityEnvironment().run(claim_eta_notification_activity, SID, ORDER, "ready")
    assert facts is not None


async def test_claim_dedups_already_notified(_isolate_vault_dir: Path):
    _write_meta(_isolate_vault_dir, SID, {"active_route": "eta", "eta_tracking": {"order_id": ORDER, "notified_stages": ["ready"]}})
    facts = await ActivityEnvironment().run(claim_eta_notification_activity, SID, ORDER, "ready")
    assert facts is None


async def test_claim_creates_entry_for_unknown_order(_isolate_vault_dir: Path):
    """Multi-pedido: un order_id sin tracking previo (p.ej. entró directo en
    `ready`) se da de alta en el claim — antes se descartaba como 'stale'."""
    _write_meta(_isolate_vault_dir, SID, {"active_route": "eta", "eta_tracking": {"order_id": ORDER, "notified_stages": []}})
    facts = await ActivityEnvironment().run(claim_eta_notification_activity, SID, "order_OTHER", "ready")
    assert facts is not None  # notifica (Medusa no configurado → datos mínimos)
    orders = _read_meta(_isolate_vault_dir, SID)["eta_tracking"]["orders"]
    assert set(orders) == {ORDER, "order_OTHER"}  # el viejo migró, el nuevo se creó


async def test_claim_dedup_is_per_order(_isolate_vault_dir: Path):
    """El dedup de stages es POR pedido: `ready` notificado en un pedido no
    bloquea el `ready` de otro."""
    _write_meta(
        _isolate_vault_dir, SID,
        {"eta_tracking": {"orders": {
            ORDER: {"order_id": ORDER, "notified_stages": ["ready"], "events": []},
            "order_B": {"order_id": "order_B", "notified_stages": [], "events": []},
        }}},
    )
    assert await ActivityEnvironment().run(claim_eta_notification_activity, SID, ORDER, "ready") is None
    facts_b = await ActivityEnvironment().run(claim_eta_notification_activity, SID, "order_B", "ready")
    assert facts_b is not None


async def test_record_notification_appends_event_and_dedups(_isolate_vault_dir: Path):
    _write_meta(_isolate_vault_dir, SID, {"eta_tracking": {"order_id": ORDER, "notified_stages": [], "events": []}})
    await ActivityEnvironment().run(record_eta_notification_activity, SID, ORDER, "preparing", "¡Hola! Tu pedido entró en preparación.")
    meta = _read_meta(_isolate_vault_dir, SID)
    tr = _entry(meta, ORDER)
    assert "preparing" in tr["notified_stages"]
    assert tr["current_stage"] == "preparing"
    assert len(tr["events"]) == 1
    assert tr["events"][0]["agent_msg"].startswith("¡Hola!")
    assert tr["events"][0]["stage"] == "preparing"


async def test_record_notification_isolated_per_order(_isolate_vault_dir: Path):
    """Multi-pedido: el record de un pedido no contamina el timeline del otro."""
    _write_meta(
        _isolate_vault_dir, SID,
        {"eta_tracking": {"orders": {
            ORDER: {"order_id": ORDER, "notified_stages": ["preparing"], "events": [{"stage": "preparing"}]},
        }}},
    )
    await ActivityEnvironment().run(record_eta_notification_activity, SID, "order_B", "ready", "Tu pedido order_B está listo.")
    meta = _read_meta(_isolate_vault_dir, SID)
    assert _entry(meta, ORDER)["notified_stages"] == ["preparing"]
    entry_b = _entry(meta, "order_B")
    assert entry_b["notified_stages"] == ["ready"]
    assert entry_b["current_stage"] == "ready"


async def test_bootstrap_falls_back_to_local_workspace(_isolate_vault_dir: Path):
    from src.plugins.eta.agent.eta.config.env import get_workspace_path

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
    from src.plugins.eta.agent.eta.config.env import get_workspace_path

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
# Dashboard API — listado desde el order port + overlay del timeline
# ════════════════════════════════════════════════════════════════════════
def _summary(**over):
    """``OrderSummaryDTO`` con defaults razonables (solo override lo relevante)."""
    from src.platform.orders.query_port import OrderSummaryDTO

    base = dict(
        id="order_X", display_id="#9", customer="Cliente WhatsApp", short="CW",
        color="a", phone=None, city="Bogotá", channel="WhatsApp",
        status="preparing", pay_status="paid", pay_type="confirmed",
        items=1, pieces=1, total_cop=100000, currency_code="COP", is_draft=False,
        due_iso=None, due_time=None, overdue=False, priority="normal", agent="—",
        created_at_ms=1_780_000_000_000, updated_at_ms=1_780_000_000_000,
    )
    base.update(over)
    return OrderSummaryDTO(**base)


class _ListPort:
    """Fake del order query port: solo necesita ``list`` (el endpoint dejó de
    hacer N ``get`` por sesión)."""

    def __init__(self, summaries):
        self._summaries = summaries

    async def list(self, *, limit=50, offset=0, include_drafts=True):
        from src.platform.orders.query_port import OrderListDTO

        return OrderListDTO(
            orders=list(self._summaries), count=len(self._summaries),
            offset=offset, limit=limit, catalog_available=True,
        )


def test_tracked_from_summary_overlays_timeline():
    from datetime import datetime

    from src.plugins.eta.api import _BOGOTA, _tracked_from_summary

    tracking = {
        "order_id": ORDER,
        "current_stage": "shipping",
        "events": [
            {"stage": "preparing", "agent_msg": "Entró en preparación", "at_ms": 1_780_000_000_000, "reply": "ok", "flagged": False, "flag": None},
            {"stage": "shipping", "agent_msg": "Va en camino", "at_ms": 1_780_100_000_000, "reply": "¿cambio dirección?", "flagged": True, "flag": "address"},
        ],
    }
    s = _summary(id=ORDER, display_id="#1247", customer="María Camila", status="shipping", total_cop=124500)
    out = _tracked_from_summary(s, tracking, current="shipping", now=datetime.now(_BOGOTA))
    assert out["id"] == "#1247"
    assert out["current"] == "shipping"
    assert out["payType"] == "confirmed"
    assert out["total"] == 124500
    assert out["needs"] is True  # hay un evento flagged
    assert out["events"][0]["agentMsg"] == "Entró en preparación"
    assert out["events"][1]["flagged"] is True


def test_tracked_from_summary_without_tracking_has_empty_timeline():
    from datetime import datetime

    from src.plugins.eta.api import _BOGOTA, _tracked_from_summary

    s = _summary(display_id="#5", status="preparing")
    out = _tracked_from_summary(s, None, current="preparing", now=datetime.now(_BOGOTA))
    assert out["id"] == "#5"
    assert out["current"] == "preparing"
    assert out["events"] == []   # el agente todavía no notificó
    assert out["needs"] is False


async def test_list_surfaces_fulfillment_orders_without_tracking(
    _isolate_vault_dir: Path, monkeypatch
):
    """Regresión del bug reportado ("la sección ETA no carga ninguna orden"):
    los pedidos en fulfillment se muestran AUNQUE ninguna sesión tenga
    ``eta_tracking`` todavía — el caso de los pedidos que ya existían antes de
    que el Agente ETA existiera. ``new`` y ``cancelled`` quedan fuera."""
    from src.plugins.eta import api as eta_api

    # Vault SIN ninguna sesión con eta_tracking (réplica del estado real).
    summaries = [
        _summary(id="order_5", display_id="#5", status="preparing"),
        _summary(id="order_4", display_id="#4", status="shipping"),
        _summary(id="order_2", display_id="#2", status="delivered"),
        _summary(id="order_6", display_id="#6", status="new"),        # excluido
        _summary(id="order_1", display_id="#1", status="cancelled"),  # excluido
    ]
    monkeypatch.setattr(eta_api, "get_order_query_port", lambda: _ListPort(summaries))

    resp = await eta_api.list_tracked_orders()
    assert resp["count"] == 3
    assert {o["id"] for o in resp["orders"]} == {"#5", "#4", "#2"}
    assert all(o["events"] == [] for o in resp["orders"])  # sin timeline aún


async def test_list_endpoint_overlays_sent_notification(
    _isolate_vault_dir: Path, monkeypatch
):
    """Behavior (gotcha #1): el mensaje que el agente ENVIÓ queda visible en el
    timeline, superpuesto sobre el pedido vivo del order port (match por
    ``eta_tracking.order_id`` == el id Medusa del summary)."""
    from src.plugins.eta import api as eta_api

    _write_meta(
        _isolate_vault_dir, SID,
        {"active_route": "eta", "eta_tracking": {
            "order_id": ORDER, "current_stage": "preparing", "notified_stages": ["preparing"],
            "events": [{"stage": "preparing", "agent_msg": "¡Hola María! Tu pedido #1247 entró en preparación.", "at_ms": 1_780_000_000_000, "reply": None, "flagged": False, "flag": None}],
        }},
    )
    summaries = [_summary(id=ORDER, display_id="#1247", customer="María Camila", status="preparing", total_cop=124500)]
    monkeypatch.setattr(eta_api, "get_order_query_port", lambda: _ListPort(summaries))

    resp = await eta_api.list_tracked_orders()
    assert resp["count"] == 1
    order = resp["orders"][0]
    assert order["id"] == "#1247"
    assert order["current"] == "preparing"
    assert order["events"][0]["agentMsg"].startswith("¡Hola María!")


async def test_list_timeline_only_when_port_unavailable(
    _isolate_vault_dir: Path, monkeypatch
):
    """Sin Medusa (dev sin .env / order list falla): el listado cae a
    timeline-only — solo los pedidos que el agente ya trackeó."""
    from src.plugins.eta import api as eta_api

    _write_meta(
        _isolate_vault_dir, SID,
        {"eta_tracking": {
            "order_id": ORDER, "current_stage": "shipping", "notified_stages": ["shipping"],
            "events": [{"stage": "shipping", "agent_msg": "Va en camino", "at_ms": 1_780_000_000_000, "reply": None, "flagged": False, "flag": None}],
        }},
    )

    def _raise():
        raise ValueError("MEDUSA_BASE_URL missing")

    monkeypatch.setattr(eta_api, "get_order_query_port", _raise)
    resp = await eta_api.list_tracked_orders()
    assert resp["count"] == 1
    assert resp["orders"][0]["current"] == "shipping"
    assert resp["orders"][0]["events"][0]["agentMsg"] == "Va en camino"


async def test_claim_facts_include_items_label(_isolate_vault_dir: Path, monkeypatch):
    """El cliente no reconoce "#6": los facts llevan los productos del pedido."""
    _write_meta(_isolate_vault_dir, SID, {"eta_tracking": {"order_id": ORDER, "notified_stages": []}})

    class _Port:
        async def get(self, oid):
            return SimpleNamespace(
                summary=SimpleNamespace(customer="Ana María", display_id="#6", total_cop=51000, pay_type="cod"),
                items_detail=[
                    SimpleNamespace(title="Vela Cruz de Vida", quantity=3),
                    SimpleNamespace(title="Vela Sándalo", quantity=1),
                ],
            )

    monkeypatch.setattr("src.platform.orders.composition.get_order_query_port", lambda: _Port())
    facts = await ActivityEnvironment().run(claim_eta_notification_activity, SID, ORDER, "shipping")
    assert facts["items_label"] == "3× Vela Cruz de Vida, Vela Sándalo"


async def test_all_trackings_terminal(_isolate_vault_dir: Path):
    """Cierre proactivo: True solo cuando TODOS los pedidos están terminales."""
    _write_meta(
        _isolate_vault_dir, SID,
        {"eta_tracking": {"orders": {
            "order_A": {"order_id": "order_A", "current_stage": "delivered", "notified_stages": [], "events": []},
            "order_B": {"order_id": "order_B", "current_stage": "shipping", "notified_stages": [], "events": []},
        }}},
    )
    assert await ActivityEnvironment().run(all_trackings_terminal_activity, SID) is False

    _write_meta(
        _isolate_vault_dir, SID,
        {"eta_tracking": {"orders": {
            "order_A": {"order_id": "order_A", "current_stage": "delivered", "notified_stages": [], "events": []},
            "order_B": {"order_id": "order_B", "current_stage": "cancelled", "notified_stages": [], "events": []},
        }}},
    )
    assert await ActivityEnvironment().run(all_trackings_terminal_activity, SID) is True

    _write_meta(_isolate_vault_dir, SID, {})
    assert await ActivityEnvironment().run(all_trackings_terminal_activity, SID) is False
