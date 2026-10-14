"""`set_order_slot` anota cajas, no velas, en un producto que se cobra por
caja (laboratorio de ···0686, 2026-10-14).

Las velas de deseos cuadradas traen 12 velas por caja a $25.000 la caja. «Dos
cajas de 12, ¿cómo sería el envío y el pago?» se anotó `cantidad: 24` dos de
cuatro veces y el resumen salió con 24 cajas ($600.000). Desde una caja
entera, la capacidad `cajas` (ventas-7) decide si la cantidad son cajas o
velas: «velas» anota las cajas que son; «cajas» la deja; sin Jev o en duda no
se anota y el texto vuelve al modelo con las dos cuentas. Menos de una caja,
un producto que no va por caja y `ventas-6` siguen como antes. El sobre dice
siempre que la cantidad es de cajas.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog.dtos import (
    CatalogManifestDTO,
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
    SearchResult,
)
from src.plugins.chats.agent.sales.decisions import registry
from src.plugins.chats.agent.sales.tools.order_draft import SetOrderSlotTool
from src.plugins.chats.shared.draft_items import draft_items

SID = "wa_boxes"
ASKED = "Dos cajas de 12 como seria el envio y el pago"


def _variant(title: str) -> CatalogVariantDTO:
    return CatalogVariantDTO(id=f"v_{title}", title=title, prices=[CatalogPriceDTO(amount="25000", currency_code="cop")])


CUADRADAS = CatalogProductDTO(
    id="prod_deseos", handle="velas-de-deseos-cuadradas", title="Velas de deseos cuadradas", status="published",
    tags=["Aroma: Navidad"], options={"Color": ["Rojo y verde", "Multicolor"]},
    description="Pequeñas velas con grandes intenciones. El kit trae 12 velas en su caja. Cada caja trae 12 velas",
    variants=[_variant("Multicolor"), _variant("Rojo y verde")],
)
LUZ = CatalogProductDTO(
    id="prod_luz", handle="luz-serena", title="Luz Serena", status="published", tags=["Aroma: Lavanda"],
    description="Vela de 40 horas en caja de regalo.", variants=[_variant("Unico")],
)


class _Catalog:
    async def search(self, q: str, *, limit: int = 10) -> SearchResult:
        products = [CUADRADAS, LUZ]
        return SearchResult(
            query=q, count=len(products), truncated=False, stale=False,
            manifest=CatalogManifestDTO(version="v1", fetched_at="2026-10-14T00:00:00Z", product_count=2),
            results=products,
        )


@pytest.fixture(autouse=True)
def _bundle(monkeypatch):
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-7")
    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    registry.reset()
    yield
    registry.reset()


def _jev(monkeypatch, p: float):
    from src.platform.perception.adapters.fake import FakePerceptionAdapter
    from src.sdk import connectorkit
    from src.sdk.connectorkit import TypedAnswer

    fake = FakePerceptionAdapter({"cajas.pide_cajas": TypedAnswer(id="cajas.pide_cajas", kind="noul", p=p)})

    def _get(_oracle: str):
        return fake

    _get.cache_clear = lambda: None
    monkeypatch.setattr(connectorkit, "get_perception_port", _get)
    monkeypatch.setenv("DECISIONS_BOT", "B")
    return fake


def _tool(tmp_path: Path) -> SetOrderSlotTool:
    events = [{"role": "assistant", "content": "¿Te la dejo en rojo y verde o en multicolor? 🤍"},
              {"role": "user", "content": ASKED}]
    return SetOrderSlotTool(workspace=tmp_path, vault_dir=tmp_path / "vault", catalog=_Catalog(),
                            history_reader=lambda _key: events)


async def _set(tool: SetOrderSlotTool, **kwargs) -> dict:
    ctx = ToolContext(session_key=SID, channel="whatsapp", chat_id="c")
    return json.loads(await tool.execute_with_context(ctx, **kwargs))


def _quantity(tmp_path: Path) -> str | None:
    meta = json.loads((tmp_path / "vault" / SID / "metadata.json").read_text(encoding="utf-8"))
    items = draft_items(meta["episodes"][-1]["order_draft"])
    return items[0].get("cantidad") if items else None


@pytest.mark.asyncio
async def test_with_the_rule_a_whole_box_or_more_goes_back_with_both_readings(tmp_path: Path) -> None:
    result = await _set(_tool(tmp_path), producto="Velas de deseos cuadradas", cantidad="24")

    assert _quantity(tmp_path) is None
    assert [r["reason"] for r in result["rejected"]] == ["pack_units"]
    assert "24 cajas = 288 velas, $600.000" in result["summary"]
    assert "24 velas = 2 cajas, $50.000" in result["summary"]


@pytest.mark.asyncio
async def test_jev_reads_candles_and_the_boxes_are_noted(tmp_path: Path, monkeypatch) -> None:
    fake = _jev(monkeypatch, 0.04)

    result = await _set(_tool(tmp_path), producto="Velas de deseos cuadradas", cantidad="24")

    assert _quantity(tmp_path) == "2"
    assert "rejected" not in result
    assert "24 velas = 2 cajas" in result["summary"]
    [state] = [s for s, qs in fake.calls if "cajas.pide_cajas" in qs]
    assert ASKED in state


@pytest.mark.asyncio
async def test_jev_reads_boxes_and_the_quantity_stays(tmp_path: Path, monkeypatch) -> None:
    _jev(monkeypatch, 0.95)

    await _set(_tool(tmp_path), producto="Velas de deseos cuadradas", cantidad="24")

    assert _quantity(tmp_path) == "24"


@pytest.mark.asyncio
async def test_less_than_a_box_is_not_asked_and_says_it_is_boxes(tmp_path: Path, monkeypatch) -> None:
    fake = _jev(monkeypatch, 0.04)

    result = await _set(_tool(tmp_path), producto="Velas de deseos cuadradas", cantidad="2")

    assert _quantity(tmp_path) == "2"
    assert not any("cajas.pide_cajas" in qs for _s, qs in fake.calls)
    assert "cada caja trae 12 velas" in result["summary"]


@pytest.mark.asyncio
async def test_a_product_sold_by_the_unit_is_not_asked(tmp_path: Path, monkeypatch) -> None:
    fake = _jev(monkeypatch, 0.04)

    await _set(_tool(tmp_path), producto="Luz Serena", cantidad="24")

    assert _quantity(tmp_path) == "24"
    assert not any("cajas.pide_cajas" in qs for _s, qs in fake.calls)


@pytest.mark.asyncio
async def test_with_ventas_6_nothing_changes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-6")
    registry.reset()

    await _set(_tool(tmp_path), producto="Velas de deseos cuadradas", cantidad="24")

    assert _quantity(tmp_path) == "24"


@pytest.mark.asyncio
async def test_lines_of_candles_are_noted_as_boxes(tmp_path: Path, monkeypatch) -> None:
    """«Una caja de cada color» repartida en líneas de 12 (las velas que trae)."""
    _jev(monkeypatch, 0.04)
    lines = [{"color": "Multicolor", "cantidad": "12"}, {"color": "Rojo y verde", "cantidad": "12"}]

    await _set(_tool(tmp_path), producto="Velas de deseos cuadradas", lineas=lines)

    meta = json.loads((tmp_path / "vault" / SID / "metadata.json").read_text(encoding="utf-8"))
    assert [i["cantidad"] for i in draft_items(meta["episodes"][-1]["order_draft"])] == ["1", "1"]


@pytest.mark.asyncio
async def test_with_the_rule_lines_of_a_whole_box_are_not_written(tmp_path: Path) -> None:
    lines = [{"color": "Multicolor", "cantidad": "12"}, {"color": "Rojo y verde", "cantidad": "12"}]

    result = await _set(_tool(tmp_path), producto="Velas de deseos cuadradas", lineas=lines)

    assert {r["reason"] for r in result["rejected"]} == {"pack_units"}
    assert result["summary"].count("se cobra por caja") == 1  # el mismo aviso, una vez


@pytest.mark.asyncio
async def test_the_doubt_goes_back_once_per_customer_message(tmp_path: Path) -> None:
    """Sin Jev (así nace la decisión en producción) la regla siempre duda: el
    segundo intento con la misma cantidad, en el mismo mensaje del cliente, es
    el modelo que ya lo revisó y se anota (nunca un bucle)."""
    tool = _tool(tmp_path)
    vault = tmp_path / "vault" / SID
    vault.mkdir(parents=True)
    (vault / "metadata.json").write_text(json.dumps({"last_inbound_message_id": "wamid.A", "episodes": [
        {"episode_id": "ep_001", "started_at_ms": 1, "closed_at_ms": None}]}), encoding="utf-8")

    first = await _set(tool, cantidad="24", producto="Velas de deseos cuadradas")
    second = await _set(tool, cantidad="24", producto="Velas de deseos cuadradas")

    assert [r["reason"] for r in first["rejected"]] == ["pack_units"]
    assert "rejected" not in second and _quantity(tmp_path) == "24"


def test_a_bulk_order_counts_boxes_not_the_candles_they_bring() -> None:
    """Laboratorio (2026-10-14): «me vendes 36 velitas de deseos cuadradas?» (3
    cajas de 12) se escaló como pedido al por mayor (>20 unidades)."""
    from src.platform.tools.escalation import EscalateToHumanTool

    tools_md = Path("src/plugins/chats/agent/sales/workspace/TOOLS.md").read_text(encoding="utf-8")
    reason = EscalateToHumanTool.parameters["properties"]["reason_category"]["description"]

    assert "| Pide >20 unidades de venta | `BULK_ORDER` |" in tools_md
    assert "una caja o kit cuenta como 1" in reason
