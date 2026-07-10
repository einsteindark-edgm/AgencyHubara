"""Tests de `build_order_sentinel_snapshot_activity` + dispatch (fase roja).

La activity escanea el vault (dirs `wa_*`): metadata.json + el historial
JSONL REAL de FilesystemMessageHistoryStore
(`<session>/sessions/<session>.jsonl`) + el watermark en
`<session>/order_sentinel.json` (key `last_analyzed_at_ms`); llama al use
case puro y ENRIQUECE cada conversación con `current_stage` +
`payment_confirmed` consultando `GET {HUBARA_API_BASE_URL}/api/orders/{id}`
(httpx, mockeado con respx — mismo patrón que tests/plugins/ads).

Shape REAL del GET (get_order_detail → asdict(OrderDetailDTO)):
`{"summary": {"status": <stage>, "pay_status": "paid"|..., ...}, ...}` —
`current_stage` = summary.status; `payment_confirmed` = pay_status == "paid"
(el adapter Medusa proyecta hubara_payment_confirmed → pay_status="paid").
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from temporalio.testing import ActivityEnvironment

from src.plugins.order_sentinel.agent.cycle.activities import (
    build_order_sentinel_snapshot_activity,
)

BASE = "http://hubara-api.invalid"


def _order_detail(status: str, pay_status: str) -> dict:
    """asdict(OrderDetailDTO) con el shape completo que emite el endpoint."""
    return {
        "summary": {
            "id": "order_X",
            "display_id": "#12",
            "customer": "Ana Pérez",
            "short": "AN",
            "color": "a",
            "phone": "+573001112233",
            "city": "Bogotá",
            "channel": "WhatsApp",
            "status": status,
            "pay_status": pay_status,
            "pay_type": "confirmed",
            "items": 1,
            "pieces": 1,
            "total_cop": 45000,
            "currency_code": "COP",
            "is_draft": False,
            "due_iso": None,
            "due_time": None,
            "overdue": False,
            "priority": "normal",
            "agent": "—",
            "created_at_ms": 1_752_000_000_000,
            "updated_at_ms": 1_752_000_000_000,
        },
        "items_detail": [],
        "shipping_address": None,
        "billing_address": None,
        "subtotal_cop": 45000,
        "shipping_cop": 0,
        "tax_total_cop": 0,
        "discount_total_cop": 0,
        "timeline": [],
        "payment_method_label": "Transferencia",
        "notes": [],
        "data_completeness_missing": [],
    }


def _seed_session(
    vault: Path,
    session_id: str,
    metadata: dict,
    events: list[dict],
    *,
    watermark_ms: int | None = None,
) -> None:
    d = vault / session_id
    (d / "sessions").mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    # Nombre REAL del historial: <session>/sessions/<session>.jsonl
    # (FilesystemMessageHistoryStore._path_for).
    history = d / "sessions" / f"{session_id}.jsonl"
    history.write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events),
        encoding="utf-8",
    )
    if watermark_ms is not None:
        (d / "order_sentinel.json").write_text(
            json.dumps({"last_analyzed_at_ms": watermark_ms}), encoding="utf-8"
        )


def _humano_con_orden(order_id: str) -> dict:
    return {"tag": "HUMANO", "episodes": [{"episode_id": "ep_1", "order_id": order_id}]}


def _user_msg(text: str, iso: str) -> dict:
    return {"role": "user", "content": text, "timestamp": iso}


@pytest.mark.asyncio
@respx.mock
async def test_snapshot_enriquece_current_stage_y_payment_desde_orders_api(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("HUBARA_API_BASE_URL", BASE)
    _seed_session(
        _isolate_vault_dir,
        "wa_pendiente",
        _humano_con_orden("order_PEND"),
        [_user_msg("ya hice la transferencia", "2026-07-09T10:00:00+00:00")],
    )
    _seed_session(
        _isolate_vault_dir,
        "wa_pagada",
        _humano_con_orden("order_PAID"),
        [_user_msg("cuando llega?", "2026-07-09T11:00:00+00:00")],
    )
    respx.get(f"{BASE}/api/orders/orders/order_PEND").mock(
        return_value=httpx.Response(200, json=_order_detail("preparing", "pending"))
    )
    respx.get(f"{BASE}/api/orders/orders/order_PAID").mock(
        return_value=httpx.Response(200, json=_order_detail("new", "paid"))
    )

    snapshot = await ActivityEnvironment().run(
        build_order_sentinel_snapshot_activity
    )

    by_session = {c["session_id"]: c for c in snapshot["conversations"]}
    assert set(by_session) == {"wa_pendiente", "wa_pagada"}
    assert by_session["wa_pendiente"]["current_stage"] == "preparing"
    assert by_session["wa_pendiente"]["payment_confirmed"] is False
    assert by_session["wa_pagada"]["current_stage"] == "new"
    assert by_session["wa_pagada"]["payment_confirmed"] is True
    # El historial se leyó del JSONL real y pasó por el use case puro.
    (msg,) = by_session["wa_pendiente"]["messages"]
    assert msg["who"] == "customer"
    assert msg["text"] == "ya hice la transferencia"


@pytest.mark.asyncio
@respx.mock
async def test_orden_ilocalizable_excluye_la_conversacion(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    # Si el GET falla no adivinamos stage: la conversación se EXCLUYE del
    # snapshot (mejor no analizar que analizar con estado inventado).
    monkeypatch.setenv("HUBARA_API_BASE_URL", BASE)
    _seed_session(
        _isolate_vault_dir,
        "wa_huerfana",
        _humano_con_orden("order_404"),
        [_user_msg("hola", "2026-07-09T10:00:00+00:00")],
    )
    respx.get(f"{BASE}/api/orders/orders/order_404").mock(
        return_value=httpx.Response(
            404, json={"detail": "Order 'order_404' not found in Medusa."}
        )
    )

    snapshot = await ActivityEnvironment().run(
        build_order_sentinel_snapshot_activity
    )
    assert snapshot["conversations"] == []


@pytest.mark.asyncio
@respx.mock
async def test_watermark_del_vault_filtra_sesiones_ya_analizadas(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    # order_sentinel.json dice "analicé hasta acá"; sin mensajes posteriores
    # la sesión queda fuera Y no se consulta la API (ni un GET de más).
    monkeypatch.setenv("HUBARA_API_BASE_URL", BASE)
    _seed_session(
        _isolate_vault_dir,
        "wa_ya_analizada",
        _humano_con_orden("order_OLD"),
        # 2026-07-09T10:00:00Z == 1_783_072_800_000... calculado abajo: el
        # watermark es POSTERIOR al único mensaje.
        [_user_msg("mensaje viejo", "2026-07-09T10:00:00+00:00")],
        watermark_ms=4_000_000_000_000,  # muy en el futuro → nada nuevo
    )
    route = respx.get(f"{BASE}/api/orders/orders/order_OLD").mock(
        return_value=httpx.Response(200, json=_order_detail("new", "pending"))
    )

    snapshot = await ActivityEnvironment().run(
        build_order_sentinel_snapshot_activity
    )
    assert snapshot["conversations"] == []
    assert route.called is False


@pytest.mark.asyncio
async def test_dispatch_envuelve_el_snapshot_en_payload(monkeypatch):
    # Guard del gotcha PR #148 (run prod ce80f73f en reengagement): el
    # contrato del agente en la caja espera {"payload": snapshot} — crudo
    # revienta el nodo ingest con "falta 'payload'".
    from src.plugins.order_sentinel.agent.cycle import activities as acts

    seen: dict = {}

    class FakeLauncher:
        def start_box(self):
            seen["box"] = True

        def dispatch(self, agent, input, *, run_id):
            seen["agent"] = agent
            seen["input"] = input
            seen["run_id"] = run_id
            return "eid-1"

    monkeypatch.setattr(acts, "get_launcher", lambda: FakeLauncher())
    snapshot = {
        "schema_version": 1,
        "now_ms": 5,
        "conversations": [{"session_id": "wa_a"}],
        "watermarks": {"wa_a": 5},
    }
    eid = await ActivityEnvironment().run(
        acts.dispatch_order_sentinel_activity, snapshot, "run-x"
    )
    assert eid == "eid-1"
    assert seen["box"] is True, "debe despertar la caja antes de despachar"
    assert seen["agent"] == "order-sentinel"
    assert seen["input"] == {"payload": snapshot}, seen["input"]
    assert seen["run_id"] == "run-x"
