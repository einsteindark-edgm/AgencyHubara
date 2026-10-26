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

#: Las cajas son de la tienda madre (`domain.yaml: sold_by_box`, las velas de
#: deseos): en un clon de forge (sin `forge/`) su dominio no las trae.
pytestmark = pytest.mark.skipif(
    not (Path(__file__).resolve().parents[5] / "forge").is_dir(),
    reason="clon de forge: las cajas (las velas de deseos) son de la tienda madre",
)

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
ONDULADAS = CatalogProductDTO(
    id="prod_onduladas", handle="velas-de-deseos-onduladas", title="Velas de deseos onduladas", status="published",
    tags=["Aroma: Navidad"], options={"Color": ["Rojo y verde", "Multicolor"]},
    description="Cada caja trae 10 velas.", variants=[_variant("Multicolor"), _variant("Rojo y verde")],
)
LUZ = CatalogProductDTO(
    id="prod_luz", handle="luz-serena", title="Luz Serena", status="published", tags=["Aroma: Lavanda"],
    description="Vela de 40 horas en caja de regalo.", variants=[_variant("Unico")],
)


class _Catalog:
    async def search(self, q: str, *, limit: int = 10) -> SearchResult:
        products = [CUADRADAS, ONDULADAS, LUZ]
        return SearchResult(
            query=q, count=len(products), truncated=False, stale=False,
            manifest=CatalogManifestDTO(version="v1", fetched_at="2026-10-14T00:00:00Z", product_count=3),
            results=products,
        )


@pytest.fixture(autouse=True)
def _bundle(monkeypatch):
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-7")
    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    registry.reset()
    yield
    registry.reset()


def _jev(monkeypatch, p: float, question: str = "cajas.pide_cajas"):
    from src.platform.perception.adapters.fake import FakePerceptionAdapter
    from src.sdk import connectorkit
    from src.sdk.connectorkit import TypedAnswer

    fake = FakePerceptionAdapter({question: TypedAnswer(id=question, kind="noul", p=p)})

    def _get(_oracle: str):
        return fake

    _get.cache_clear = lambda: None
    monkeypatch.setattr(connectorkit, "get_perception_port", _get)
    monkeypatch.setenv("DECISIONS_BOT", "B")
    return fake


def _tool(tmp_path: Path, events: list[dict] | None = None) -> SetOrderSlotTool:
    events = events or [{"role": "assistant", "content": "¿Te la dejo en rojo y verde o en multicolor? 🤍"},
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


def test_only_the_wishing_candles_are_sold_by_the_box(tmp_path: Path, monkeypatch) -> None:
    """Decisión del operador (2026-10-14): solo las velas de deseos van por
    caja. Otro producto cuya descripción habla de una caja se anota tal cual."""
    import asyncio

    fake = _jev(monkeypatch, 0.04)
    other = CatalogProductDTO(
        id="prod_te", handle="velitas-de-te", title="Velitas de té", status="published", tags=["Aroma: Vainilla"],
        description="Cada caja trae 6 velas de té.", variants=[_variant("Unico")],
    )

    class _WithOther(_Catalog):
        async def search(self, q: str, *, limit: int = 10) -> SearchResult:
            found = await super().search(q, limit=limit)
            return SearchResult(query=q, count=3, truncated=False, stale=False, manifest=found.manifest,
                                results=[*found.results, other])

    tool = SetOrderSlotTool(workspace=tmp_path, vault_dir=tmp_path / "vault", catalog=_WithOther(),
                            history_reader=lambda _key: [{"role": "user", "content": "quiero 12 velitas de té"}])
    asyncio.run(_set(tool, producto="Velitas de té", cantidad="12"))

    assert _quantity(tmp_path) == "12"
    assert not any("cajas.pide_cajas" in qs for _s, qs in fake.calls)


@pytest.mark.asyncio
async def test_a_quantity_in_words_goes_back_as_the_number_of_boxes(tmp_path: Path) -> None:
    """Laboratorio (2026-10-14): «3 cajas (36 unidades)» quedó en el borrador
    y quien lo lee solo entiende números (cantidad desconocida en el registro).
    En un producto que va por caja vuelve al modelo: solo el número de cajas."""
    result = await _set(_tool(tmp_path), producto="Velas de deseos cuadradas", cantidad="3 cajas (36 unidades)")

    assert _quantity(tmp_path) is None
    assert [r["reason"] for r in result["rejected"]] == ["pack_units"]
    assert "solo el número de cajas" in result["summary"] and "'3'" in result["summary"]


@pytest.mark.asyncio
async def test_a_product_sold_by_the_unit_keeps_its_quantity_as_written(tmp_path: Path) -> None:
    await _set(_tool(tmp_path), producto="Luz Serena", cantidad="2 unidades")

    assert _quantity(tmp_path) == "2 unidades"


# --- La caja de otro producto (`caja_nombrada`, laboratorio de ···0686) ------
#
# «Dos cajas de 12, ¿cómo sería el envío y el pago?» y el bot anotó las
# onduladas, que traen 10 (la de 12 es la cuadrada); una vez le escribió a la
# clienta «cada caja trae 10 velas, no 12». Al anotar por primera vez un
# producto que va por caja, si el cliente nombró el tamaño de la caja de otro
# y no el de este, decide `caja_nombrada`: «otro» no anota el producto (los
# datos de envío sí); «duda» (sin Jev, así nace) lo devuelve UNA vez por
# mensaje del cliente; «este» lo anota.

NAMED = "caja_nombrada.pide_la_otra"
TWELVE = [
    {"role": "assistant", "content": "Las velitas de intención vienen en caja 🤍 ¿Te muestro los diseños?"},
    {"role": "user", "content": ASKED},
    {"role": "user", "content": "Multicolor para todas, pago contra entrega"},
]


def _seed_inbound(tmp_path: Path, inbound: str = "wamid.A") -> None:
    vault = tmp_path / "vault" / SID
    vault.mkdir(parents=True, exist_ok=True)
    meta_file = vault / "metadata.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {
        "episodes": [{"episode_id": "ep_001", "started_at_ms": 1, "closed_at_ms": None}]}
    meta["last_inbound_message_id"] = inbound
    meta_file.write_text(json.dumps(meta), encoding="utf-8")


def _draft(tmp_path: Path) -> dict:
    meta_file = tmp_path / "vault" / SID / "metadata.json"
    if not meta_file.exists():
        return {}  # nada se escribió
    episodes = json.loads(meta_file.read_text(encoding="utf-8")).get("episodes") or [{}]
    return episodes[-1].get("order_draft") or {}


@pytest.mark.asyncio
async def test_with_the_rule_the_box_of_the_other_product_goes_back(tmp_path: Path) -> None:
    result = await _set(_tool(tmp_path, TWELVE), producto="Velas de deseos onduladas", cantidad="2",
                        color="Multicolor")

    assert draft_items(_draft(tmp_path)) == []
    assert [r["reason"] for r in result["rejected"]] == ["other_box"]
    assert "la caja de 12 es Velas de deseos cuadradas" in result["summary"]


@pytest.mark.asyncio
async def test_jev_reads_the_other_box_and_the_product_is_not_noted(tmp_path: Path, monkeypatch) -> None:
    fake = _jev(monkeypatch, 0.9, NAMED)

    result = await _set(_tool(tmp_path, TWELVE), producto="Velas de deseos onduladas", cantidad="2")

    assert draft_items(_draft(tmp_path)) == [] and [r["reason"] for r in result["rejected"]] == ["other_box"]
    [state] = [s for s, qs in fake.calls if NAMED in qs]
    assert ASKED in state and "La caja de 12 velas es otro producto: Velas de deseos cuadradas." in state


@pytest.mark.asyncio
async def test_jev_reads_this_product_and_it_is_noted(tmp_path: Path, monkeypatch) -> None:
    _jev(monkeypatch, 0.05, NAMED)

    await _set(_tool(tmp_path, TWELVE), producto="Velas de deseos onduladas", cantidad="2")

    assert _quantity(tmp_path) == "2"


@pytest.mark.asyncio
async def test_the_shipping_data_of_the_same_call_is_kept(tmp_path: Path) -> None:
    await _set(_tool(tmp_path, TWELVE), producto="Velas de deseos onduladas", cantidad="2", ciudad="Bogotá")

    draft = _draft(tmp_path)
    assert draft_items(draft) == [] and draft["slots"]["ciudad"] == "Bogotá"


@pytest.mark.asyncio
async def test_naming_this_box_is_not_asked(tmp_path: Path, monkeypatch) -> None:
    fake = _jev(monkeypatch, 0.9, NAMED)
    ten = [{"role": "user", "content": "Quiero dos cajas de 10, multicolor"}]

    await _set(_tool(tmp_path, ten), producto="Velas de deseos onduladas", cantidad="2")

    assert _quantity(tmp_path) == "2" and not any(NAMED in qs for _s, qs in fake.calls)


@pytest.mark.asyncio
async def test_it_goes_back_once_per_customer_message_and_not_again_once_noted(tmp_path: Path) -> None:
    """El segundo intento en el mismo mensaje es el modelo que ya lo revisó;
    ya anotado, otro mensaje del cliente no lo vuelve a preguntar."""
    tool = _tool(tmp_path, TWELVE)
    _seed_inbound(tmp_path, "wamid.A")

    first = await _set(tool, producto="Velas de deseos onduladas", cantidad="2")
    second = await _set(tool, producto="Velas de deseos onduladas", cantidad="2")
    _seed_inbound(tmp_path, "wamid.B")
    third = await _set(tool, producto="Velas de deseos onduladas", cantidad="3")

    assert [r["reason"] for r in first["rejected"]] == ["other_box"]
    assert "rejected" not in second and "rejected" not in third and _quantity(tmp_path) == "3"


@pytest.mark.asyncio
async def test_lines_of_the_other_box_are_not_written(tmp_path: Path) -> None:
    lines = [{"color": "Multicolor", "cantidad": "1"}, {"color": "Rojo y verde", "cantidad": "1"}]

    result = await _set(_tool(tmp_path, TWELVE), producto="Velas de deseos onduladas", lineas=lines)

    assert draft_items(_draft(tmp_path)) == [] and {r["reason"] for r in result["rejected"]} == {"other_box"}
