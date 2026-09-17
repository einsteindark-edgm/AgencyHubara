"""Sync del total vivo de Medusa → episodio del chat (lo que suma Ads).

Caso real (pedido #31, 2026-09-17): el operador cambió un producto de la
orden en Medusa. Orders mostró el total nuevo (lo lee en vivo) pero Ads siguió
con el viejo: suma `episode.order_total_cop`, congelado cuando el bot cerró la
venta. El barrido periódico de orders ahora copia el total vivo al episodio.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from src.platform.orders.query_port import OrderListDTO
from src.plugins.ads.aggregation import _episode_revenue_cop
from src.plugins.orders.order_totals_sync import apply_live_totals, sync_order_totals

NOW = 1_758_100_000_000


def _chat(order_id: str = "order_31", total: int | None = 90000) -> dict[str, Any]:
    return {
        "tag": "COMPRA_EXITOSA",
        "registered_order": {"success": True, "order_id": order_id, "total_cop": total},
        "episodes": [
            {"episode_id": "ep_0", "closing_tag": "RECHAZO"},
            {"episode_id": "ep_1", "order_id": order_id, "order_total_cop": total, "order_currency": "COP"},
        ],
    }


class TestApplyLiveTotals:
    def test_updates_episode_and_registered_order_keeping_original(self) -> None:
        md = _chat()
        assert apply_live_totals(md, {"order_31": 120000}, now_ms=NOW) is True
        ep = md["episodes"][1]
        assert ep["order_total_cop"] == 120000
        assert ep["order_total_cop_at_close"] == 90000
        assert ep["order_total_synced_at_ms"] == NOW
        assert md["registered_order"]["total_cop"] == 120000
        assert _episode_revenue_cop(ep, {}) == 120000

    def test_second_change_keeps_the_first_original(self) -> None:
        md = _chat()
        apply_live_totals(md, {"order_31": 120000}, now_ms=NOW)
        apply_live_totals(md, {"order_31": 80000}, now_ms=NOW + 1)
        assert md["episodes"][1]["order_total_cop"] == 80000
        assert md["episodes"][1]["order_total_cop_at_close"] == 90000

    def test_noop_when_total_matches_or_order_unknown(self) -> None:
        md = _chat()
        before = json.dumps(md, sort_keys=True)
        assert apply_live_totals(md, {"order_31": 90000}, now_ms=NOW) is False
        assert apply_live_totals(md, {"order_99": 5}, now_ms=NOW) is False
        assert json.dumps(md, sort_keys=True) == before

    def test_ignores_non_positive_live_totals(self) -> None:
        md = _chat()
        assert apply_live_totals(md, {"order_31": 0}, now_ms=NOW) is False


@dataclass
class _Summary:
    id: str
    total_cop: int


@dataclass
class FakeQuery:
    pages: list[list[_Summary]]
    available: bool = True
    calls: list[int] = field(default_factory=list)

    async def list(self, *, limit: int = 50, offset: int = 0, include_drafts: bool = True) -> OrderListDTO:
        self.calls.append(offset)
        idx = offset // limit
        orders = self.pages[idx] if self.available and idx < len(self.pages) else []
        return OrderListDTO(
            orders=orders,  # type: ignore[arg-type]
            count=sum(len(p) for p in self.pages),
            offset=offset,
            limit=limit,
            catalog_available=self.available,
            error_detail=None if self.available else "medusa_unavailable: x",
        )


def _write(vault: Path, session: str, md: dict[str, Any]) -> Path:
    path = vault / session / "metadata.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(md), encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_sync_rewrites_edited_order_total_in_vault(tmp_path: Path) -> None:
    edited = _write(tmp_path, "wa_31", _chat("order_31", 90000))
    untouched = _write(tmp_path, "wa_40", _chat("order_40", 50000))
    mtime = untouched.stat().st_mtime_ns
    port = FakeQuery(pages=[[_Summary("order_40", 50000), _Summary("order_31", 120000)]])

    result = await sync_order_totals(vault_dir=tmp_path, port=port, page_size=2, now_ms=NOW)

    assert result.updated_sessions == 1
    assert json.loads(edited.read_text())["episodes"][1]["order_total_cop"] == 120000
    assert untouched.stat().st_mtime_ns == mtime


@pytest.mark.asyncio
async def test_sync_stops_paging_once_every_order_is_found(tmp_path: Path) -> None:
    _write(tmp_path, "wa_31", _chat("order_31", 90000))
    port = FakeQuery(
        pages=[[_Summary("order_31", 120000), _Summary("order_x", 1)], [_Summary("order_y", 1)]]
    )
    await sync_order_totals(vault_dir=tmp_path, port=port, page_size=2, now_ms=NOW)
    assert port.calls == [0]


@pytest.mark.asyncio
async def test_sync_does_nothing_when_medusa_is_down(tmp_path: Path) -> None:
    path = _write(tmp_path, "wa_31", _chat("order_31", 90000))
    port = FakeQuery(pages=[[_Summary("order_31", 120000)]], available=False)
    result = await sync_order_totals(vault_dir=tmp_path, port=port, page_size=2, now_ms=NOW)
    assert result.updated_sessions == 0
    assert json.loads(path.read_text())["episodes"][1]["order_total_cop"] == 90000


@pytest.mark.asyncio
async def test_sync_skips_vault_without_orders(tmp_path: Path) -> None:
    _write(tmp_path, "wa_1", {"tag": "FRIO", "episodes": [{"episode_id": "e"}]})
    port = FakeQuery(pages=[[_Summary("order_31", 1)]])
    result = await sync_order_totals(vault_dir=tmp_path, port=port, page_size=2, now_ms=NOW)
    assert port.calls == []
    assert result.updated_sessions == 0
