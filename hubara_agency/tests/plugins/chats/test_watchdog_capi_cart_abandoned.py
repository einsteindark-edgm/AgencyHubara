"""Watchdog "fired" con embudo abierto → ``CartAbandoned`` (auditoría CAPI, punto 6).

El watchdog dispara cuando el cliente se quedó callado dentro de la ventana
de servicio. Si en ese momento hay un pedido registrado sin pago o un
borrador con producto, ESE es el carrito abandonado (no el TIMEOUT de 14
días, que cae fuera de la ventana de atribución de 7 días de Meta).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.plugins.chats.agent.remarketing.activities.watchdog_activities import (
    persist_watchdog_outcome_activity,
)

SESSION_ID = "wa_573009998877"
NOW_MS = 1_757_350_000_000


def _write(vault: Path, data: dict) -> Path:
    target = vault / SESSION_ID / "metadata.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data), encoding="utf-8")
    return target


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _attributed(**extra: object) -> dict:
    md: dict = {
        "phone_number_id": "PID",
        "ctwa_referrals": [{"ctwa_clid": "CLID", "captured_at_ms": NOW_MS}],
        "episodes": [{"episode_id": "ep_003", "started_at_ms": NOW_MS, "closed_at_ms": None}],
    }
    md.update(extra)
    return md


@pytest.fixture(autouse=True)
def _no_capi_config(monkeypatch: pytest.MonkeyPatch) -> None:
    # Sin dataset → el flush persiste `skipped_no_config` sin tocar la red.
    from src.platform import config

    monkeypatch.setattr(config, "META_CAPI_DATASET_ID", "")
    monkeypatch.setattr(config, "META_CAPI_ACCESS_TOKEN", "")


@pytest.mark.asyncio
async def test_fired_with_unpaid_registered_order_enqueues_and_flushes_cart_abandoned(
    _isolate_vault_dir: Path,
) -> None:
    md = _attributed(registered_order={"success": True, "order_id": "draft_1", "total_cop": 42000, "currency": "COP"})
    md["episodes"][0]["order_id"] = "draft_1"
    path = _write(_isolate_vault_dir, md)
    await persist_watchdog_outcome_activity(SESSION_ID, "fired", "wamid.tpl")
    saved = _read(path)
    assert saved["watchdog"]["fired_at_ms"]
    # Encolado y flusheado en la misma activity (durable por Temporal):
    assert saved["capi_outbox"] == []
    sent = saved["capi_events_sent"]
    assert [(e["event_name"], e["status"], e["event_id"]) for e in sent] == [
        ("CartAbandoned", "skipped_no_config", f"cartabandoned_{SESSION_ID}_ep_003")
    ]


@pytest.mark.asyncio
async def test_fired_with_draft_product_enqueues_cart_abandoned(_isolate_vault_dir: Path) -> None:
    md = _attributed()
    md["episodes"][0]["order_draft"] = {"slots": {"producto": "Ángel"}, "updated_at_ms": NOW_MS}
    path = _write(_isolate_vault_dir, md)
    await persist_watchdog_outcome_activity(SESSION_ID, "fired", "wamid.tpl")
    assert [e["event_name"] for e in _read(path)["capi_events_sent"]] == ["CartAbandoned"]


@pytest.mark.asyncio
async def test_fired_without_cart_or_after_purchase_enqueues_nothing(_isolate_vault_dir: Path) -> None:
    path = _write(_isolate_vault_dir, _attributed())
    await persist_watchdog_outcome_activity(SESSION_ID, "fired", "wamid.tpl")
    assert "capi_outbox" not in _read(path) and "capi_events_sent" not in _read(path)

    path2 = _write(
        _isolate_vault_dir,
        _attributed(
            capi_terminal_event="Purchase",
            registered_order={"success": True, "order_id": "draft_1", "total_cop": 42000},
        ),
    )
    await persist_watchdog_outcome_activity(SESSION_ID, "fired", "wamid.tpl")
    assert "capi_outbox" not in _read(path2)


@pytest.mark.asyncio
async def test_cancelled_outcome_does_not_touch_capi(_isolate_vault_dir: Path) -> None:
    md = _attributed(registered_order={"success": True, "order_id": "draft_1", "total_cop": 42000})
    md["episodes"][0]["order_id"] = "draft_1"
    path = _write(_isolate_vault_dir, md)
    await persist_watchdog_outcome_activity(SESSION_ID, "cancelled", "customer_replied")
    assert "capi_outbox" not in _read(path)
