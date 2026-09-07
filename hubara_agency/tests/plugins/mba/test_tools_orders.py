"""Tools de pedidos del connector: estado (vault + port de orders) y verificación de checkout."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.platform.catalog.checkout_port import CheckoutItem, CheckoutVerification, VerifiedItem
from src.plugins.mba.tools.orders import check_order_status, verify_order_for_checkout
from src.sdk.runtime import FilesystemMetadataStore

_SESSION = "wa_573001234567"


def _vault(tmp_path: Path, session: str, data: dict) -> FilesystemMetadataStore:
    (tmp_path / session).mkdir(parents=True, exist_ok=True)
    (tmp_path / session / "metadata.json").write_text(json.dumps(data), encoding="utf-8")
    return FilesystemMetadataStore(tmp_path)


class _Query:
    def __init__(self, summaries: dict, *, boom: bool = False) -> None:
        self._s = summaries
        self.boom = boom

    async def get(self, order_id: str):
        if self.boom:
            raise RuntimeError("medusa down")
        s = self._s.get(order_id)
        return SimpleNamespace(summary=s) if s else None


@pytest.mark.asyncio
async def test_status_lists_registered_orders_with_live_stage_and_pay_status(tmp_path: Path) -> None:
    store = _vault(tmp_path, _SESSION, {
        "episodes": [{"order_id": "order_1"}],
        "registered_order": {"order_id": "order_2", "success": True},
        "eta_tracking": {"orders": {"order_1": {"order_id": "order_1", "current_stage": "shipping", "events": [{"at_ms": 1757200000000}]}}},
    })
    query = _Query({
        "order_1": SimpleNamespace(status="shipping", pay_status="paid", display_id="#1247", total_cop=52000),
        "order_2": SimpleNamespace(status="new", pay_status="pending", display_id="#1248", total_cop=35000),
    })
    out = await check_order_status(store, query, session_key=_SESSION)
    assert [o["order_id"] for o in out["orders"]] == ["order_1", "order_2"]
    first, second = out["orders"]
    assert first["status"] == "en camino" and first["payment_confirmed"] is True and first["display_id"] == "#1247"
    assert second["status"] == "recibido" and second["payment_confirmed"] is False and second["pay_status"] == "pending"
    assert "note" not in out


@pytest.mark.asyncio
async def test_status_is_scoped_to_the_customer_and_degrades_when_live_lookup_fails(tmp_path: Path) -> None:
    store = _vault(tmp_path, _SESSION, {"episodes": [{"order_id": "order_1"}]})
    other = await check_order_status(store, _Query({}), session_key="wa_573009876543")
    assert other["orders"] == [] and "note" in other
    degraded = await check_order_status(store, _Query({}, boom=True), session_key=_SESSION)
    assert degraded["orders"][0]["status"] == "registrado"
    assert "payment_confirmed" not in degraded["orders"][0]
    assert "en vivo" in degraded["note"]
    local_only = await check_order_status(store, None, session_key=_SESSION)
    assert local_only["orders"][0]["order_id"] == "order_1" and "note" not in local_only


class _Verifier:
    def __init__(self, result: CheckoutVerification) -> None:
        self.result = result
        self.items: list[CheckoutItem] = []

    async def verify_items(self, items: list[CheckoutItem]) -> CheckoutVerification:
        self.items = items
        return self.result


@pytest.mark.asyncio
async def test_checkout_verification_passes_items_and_reports_discrepancies() -> None:
    v = _Verifier(CheckoutVerification(verified=False, catalog_available=True, items=[
        VerifiedItem(handle="vela-lavanda", title="Vela Lavanda", snapshot_price="35000", live_price="38000", currency="cop", in_stock=True, discrepancy=True),
    ]))
    out = await verify_order_for_checkout(v, items=[{"handle": "vela-lavanda", "quantity": 2, "variant_label": "Lavanda, Blanco"}])
    assert v.items == [CheckoutItem(handle="vela-lavanda", quantity=2)]
    assert out["verified"] is False and out["discrepancy"] is True
    assert out["items"][0]["live_price"] == "38000" and "precio" in out["message"]
    ok = await verify_order_for_checkout(
        _Verifier(CheckoutVerification(verified=True, catalog_available=True, items=[])), items=[{"handle": "x", "quantity": 1}]
    )
    assert ok["verified"] is True and ok["discrepancy"] is False


@pytest.mark.asyncio
async def test_checkout_without_live_source_tells_the_agent_to_escalate() -> None:
    down = await verify_order_for_checkout(
        _Verifier(CheckoutVerification(verified=False, catalog_available=False, error_detail="timeout")),
        items=[{"handle": "x", "quantity": 1}],
    )
    assert down["error"] == "catalog_unavailable" and "CHECKOUT_VERIFY_FAILED" in down["message"]
    assert (await verify_order_for_checkout(None, items=[{"handle": "x", "quantity": 1}]))["error"] == "catalog_unavailable"
