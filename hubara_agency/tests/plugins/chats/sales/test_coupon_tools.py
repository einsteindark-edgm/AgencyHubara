"""Cupones en el bot de ventas — `list_promotions` + `apply_coupon` + el
descuento server-side en la confirmación y el registro del pedido.

Contrato:
  * El bot NUNCA inventa descuentos: `apply_coupon` valida el código contra
    Medusa (port) y persiste el SNAPSHOT de la promoción en el episodio.
  * `present_order_confirmation` y `register_order` recomputan el descuento
    desde ese snapshot con los precios del catálogo (L-19); el LLM solo
    pasa el total que el envelope le dijo.
  * El código con forma de tag interno (`VELAS_10`) se rechaza (colisión
    con el guard de egreso: el bot enmudecería).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog import (
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
    ProductNotFoundError,
)
from src.platform.orders.port import (
    DiscountedUnits,
    OrderItem,
    OrderRegistrationResult,
    OrderShipping,
)
from src.platform.orders.reconciliation import reconcile_one
from src.platform.state import FilesystemMetadataStore
from src.platform.promotions.port import FakePromotionsPort, PromotionDTO
from src.plugins.chats.agent.sales.tools.coupons import (
    ApplyCouponTool,
    ListPromotionsTool,
)
from src.plugins.chats.agent.sales.tools.order_registration import RegisterOrderTool
from src.plugins.chats.agent.sales.tools.ui_intents import PresentOrderConfirmationTool
from src.plugins.chats.agent.sales.use_cases.coupons import (
    applied_coupon,
    build_coupon_note,
    coupon_discount_for_items,
)

KEY = "wa_test_coupons"
_NOW = 1_750_000_000_000


def _product(handle: str, title: str, price: str) -> CatalogProductDTO:
    return CatalogProductDTO(
        id=f"prod_{handle}",
        handle=handle,
        title=title,
        status="published",
        variants=[
            CatalogVariantDTO(
                id=f"variant_{handle}",
                title="Unico",
                sku=f"HUB-{handle.upper()}",
                prices=[CatalogPriceDTO(amount=price, currency_code="cop")],
            )
        ],
    )


class FakeCatalog:
    products = {
        "vela-buda": _product("vela-buda", "Vela Buda Zen", "40000"),
        "cubo-love": _product("cubo-love", "Cubo Love", "30000"),
    }

    async def get_by_handle(self, handle: str) -> CatalogProductDTO:
        try:
            return self.products[handle]
        except KeyError:
            raise ProductNotFoundError(handle) from None

    async def search(self, q: str, *, limit: int = 10, category: str | None = None):
        from src.platform.catalog.dtos import CatalogManifestDTO, SearchResult

        products = list(self.products.values())
        return SearchResult(
            query=q,
            count=len(products),
            truncated=False,
            stale=False,
            manifest=CatalogManifestDTO(version="t", fetched_at="2026-01-01T00:00:00Z", product_count=len(products)),
            results=products,
        )


def _promo(**over) -> PromotionDTO:
    base = dict(
        id="promo_1",
        code="MAMA15",
        discount_type="percentage",
        value=15,
        currency_code="cop",
        target_type="items",
        allocation="across",
        max_quantity=None,
        product_ids=(),
        variant_ids=(),
        collection_ids=(),
        min_subtotal_cop=None,
        is_automatic=False,
        status="active",
        starts_at_ms=None,
        ends_at_ms=None,
        budget_type=None,
        budget_limit=None,
        budget_used=None,
        description="Día de la madre",
    )
    base.update(over)
    return PromotionDTO(**base)


@dataclass
class FakePort:
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def register_order(
        self,
        *,
        session_key: str,
        items: list[OrderItem],
        shipping: OrderShipping,
        payment_method: str,
        subtotal_cop: int,
        shipping_cop: int,
        total_cop: int,
        currency: str = "COP",
        attribution: dict[str, Any] | None = None,
        coupon_code: str | None = None,
        discount_cop: int = 0,
    ) -> OrderRegistrationResult:
        self.calls.append(
            {
                "total_cop": total_cop,
                "coupon_code": coupon_code,
                "discount_cop": discount_cop,
            }
        )
        return OrderRegistrationResult(
            success=True,
            order_id="draft_c_001",
            provider="medusa",
            raw_payload={"id": "draft_c_001", "display_id": 41},
        )


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(session_key=KEY, channel="whatsapp", chat_id=KEY)


def _seed(vault: Path, extra: dict | None = None) -> Path:
    path = vault / KEY / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    md = {"episodes": [{"episode_id": "ep_001", "started_at_ms": 1, "closed_at_ms": None}]}
    md.update(extra or {})
    path.write_text(json.dumps(md, ensure_ascii=False), encoding="utf-8")
    return path


def _md(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# --- list_promotions --------------------------------------------------------


@pytest.mark.asyncio
async def test_list_promotions_cuenta_codigo_valor_y_productos(ctx, _isolate_vault_dir):
    port = FakePromotionsPort(
        [
            _promo(),
            _promo(
                id="p2",
                code="BUDA10",
                value=10,
                product_ids=("prod_vela-buda",),
                description=None,
            ),
            _promo(id="p3", code="VIEJO", status="inactive"),
        ]
    )
    tool = ListPromotionsTool(workspace=str(_isolate_vault_dir), promotions=port, catalog=FakeCatalog())
    env = json.loads(await tool.execute_with_context(ctx))
    assert [p["code"] for p in env["promotions"]] == ["MAMA15", "BUDA10"]
    assert env["promotions"][0]["discount"] == "15%"
    assert env["promotions"][0]["products"] == "todo el catálogo"
    assert env["promotions"][1]["products"] == ["Vela Buda Zen"]
    assert "MAMA15" in env["summary"]


@pytest.mark.asyncio
async def test_list_promotions_sin_promos_lo_dice(ctx, _isolate_vault_dir):
    tool = ListPromotionsTool(
        workspace=str(_isolate_vault_dir), promotions=FakePromotionsPort([]), catalog=FakeCatalog()
    )
    env = json.loads(await tool.execute_with_context(ctx))
    assert env["promotions"] == []
    assert "no hay" in env["summary"].lower()


# --- apply_coupon -----------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_coupon_valido_persiste_snapshot_en_el_episodio(ctx, _isolate_vault_dir):
    path = _seed(_isolate_vault_dir)
    tool = ApplyCouponTool(
        workspace=str(_isolate_vault_dir),
        metadata_store=FilesystemMetadataStore(_isolate_vault_dir),
        promotions=FakePromotionsPort([_promo()]),
        catalog=FakeCatalog(),
        now_ms=lambda: _NOW,
    )
    env = json.loads(
        await tool.execute_with_context(
            ctx,
            code="mama15",
            items=[{"handle": "vela-buda", "quantity": 1, "unit_price_cop": 40000}],
        )
    )
    assert env["applied"] is True
    assert env["code"] == "MAMA15"
    assert env["discount_cop"] == 6000
    assert env["applicable_handles"] == ["vela-buda"]
    assert "6.000" in env["summary"]
    coupon = _md(path)["episodes"][-1]["applied_coupon"]
    assert coupon["code"] == "MAMA15"
    assert coupon["promotion"]["value"] == 15
    assert coupon["applied_at_ms"] == _NOW


@pytest.mark.asyncio
async def test_apply_coupon_invalido_no_persiste_y_explica(ctx, _isolate_vault_dir):
    path = _seed(_isolate_vault_dir)
    tool = ApplyCouponTool(
        workspace=str(_isolate_vault_dir),
        metadata_store=FilesystemMetadataStore(_isolate_vault_dir),
        promotions=FakePromotionsPort([_promo()]),
        catalog=FakeCatalog(),
    )
    env = json.loads(await tool.execute_with_context(ctx, code="NOEXISTE"))
    assert env["applied"] is False
    assert env["reason"] == "not_found"
    assert "applied_coupon" not in _md(path)["episodes"][-1]
    env = json.loads(await tool.execute_with_context(ctx, code="VELAS_10"))
    assert env["reason"] == "invalid_format"


@pytest.mark.asyncio
async def test_apply_coupon_reemplaza_el_anterior_y_vacio_lo_quita(ctx, _isolate_vault_dir):
    path = _seed(_isolate_vault_dir)
    port = FakePromotionsPort([_promo(), _promo(id="p2", code="PAPA20", value=20)])
    tool = ApplyCouponTool(
        workspace=str(_isolate_vault_dir), metadata_store=FilesystemMetadataStore(_isolate_vault_dir), promotions=port, catalog=FakeCatalog()
    )
    await tool.execute_with_context(ctx, code="MAMA15")
    await tool.execute_with_context(ctx, code="PAPA20")
    assert _md(path)["episodes"][-1]["applied_coupon"]["code"] == "PAPA20"
    env = json.loads(await tool.execute_with_context(ctx, code=""))
    assert env["applied"] is False and env["reason"] == "removed"
    assert "applied_coupon" not in _md(path)["episodes"][-1]


@pytest.mark.asyncio
async def test_apply_coupon_medusa_caido_es_honesto(ctx, _isolate_vault_dir):
    from src.platform.promotions.port import PromotionsUnavailableError

    class Down:
        async def list_active(self):
            raise PromotionsUnavailableError("timeout")

        async def get_by_code(self, code):
            raise PromotionsUnavailableError("timeout")

    _seed(_isolate_vault_dir)
    tool = ApplyCouponTool(
        workspace=str(_isolate_vault_dir), metadata_store=FilesystemMetadataStore(_isolate_vault_dir), promotions=Down(), catalog=FakeCatalog()
    )
    env = json.loads(await tool.execute_with_context(ctx, code="MAMA15"))
    assert env["applied"] is False
    assert env["reason"] == "unavailable"


# --- use case: descuento desde el snapshot ---------------------------------


@pytest.mark.asyncio
async def test_coupon_discount_for_items_desde_el_snapshot(_isolate_vault_dir):
    _seed(
        _isolate_vault_dir,
        {
            "episodes": [
                {
                    "episode_id": "ep_001",
                    "started_at_ms": 1,
                    "closed_at_ms": None,
                    "applied_coupon": {
                        "code": "BUDA10",
                        "applied_at_ms": _NOW,
                        "promotion": {**_promo(code="BUDA10", value=10, product_ids=("prod_vela-buda",)).__dict__},
                    },
                }
            ]
        },
    )
    md = _md(_isolate_vault_dir / KEY / "metadata.json")
    assert applied_coupon(md) is not None
    res = await coupon_discount_for_items(
        md,
        FakeCatalog(),
        [
            {"handle": "vela-buda", "quantity": 2, "unit_price_cop": 40000},
            {"handle": "cubo-love", "quantity": 1, "unit_price_cop": 30000},
        ],
    )
    assert res is not None
    assert res.code == "BUDA10"
    assert res.discount_cop == 8000
    assert res.applicable_handles == ["vela-buda"]


@pytest.mark.asyncio
async def test_coupon_discount_sin_cupon_es_none(_isolate_vault_dir):
    _seed(_isolate_vault_dir)
    md = _md(_isolate_vault_dir / KEY / "metadata.json")
    assert await coupon_discount_for_items(md, FakeCatalog(), []) is None


# --- present_order_confirmation con cupón ----------------------------------


def _coupon_md() -> dict:
    return {
        "episodes": [
            {
                "episode_id": "ep_001",
                "started_at_ms": 1,
                "closed_at_ms": None,
                "applied_coupon": {
                    "code": "MAMA15",
                    "applied_at_ms": _NOW,
                    "promotion": {**_promo().__dict__},
                },
            }
        ]
    }


@pytest.mark.asyncio
async def test_present_confirmation_descuenta_y_lo_dice(ctx, _isolate_vault_dir):
    path = _seed(_isolate_vault_dir, _coupon_md())
    tool = PresentOrderConfirmationTool(workspace=str(_isolate_vault_dir), catalog=FakeCatalog())
    env = json.loads(
        await tool.execute_with_context(
            ctx,
            items=[{"handle": "vela-buda", "quantity": 1, "unit_price_cop": 40000}],
            shipping_cop=7900,
            shipping_address_summary="Calle 1, Bogotá",
            payment_method="transfer",
        )
    )
    assert env["queued"] is True
    assert env["discount_cop"] == 6000
    assert env["coupon_code"] == "MAMA15"
    # total = 40.000 − 6.000 + 7.900
    assert env["total_cop"] == 41900
    assert "MAMA15" in env["summary"] and "6.000" in env["summary"]
    (intent,) = _md(path)["pending_ui_intents"]
    assert intent["params"]["discount_cop"] == 6000
    assert intent["params"]["coupon_code"] == "MAMA15"
    assert intent["params"]["total_cop"] == 41900


# --- register_order con cupón ----------------------------------------------


_SHIPPING = {
    "city": "Bogotá",
    "neighborhood": "Chapinero",
    "address": "Calle 1 #2-3",
    "phone": "3001234567",
    "receiver_name": "Ana Pérez",
}


async def _register(vault, ctx, port, *, total: int) -> dict:
    tool = RegisterOrderTool(workspace=str(vault), vault_dir=vault, port=port, catalog=FakeCatalog())
    return json.loads(
        await tool.execute_with_context(
            ctx,
            items=[{"handle": "vela-buda", "quantity": 1, "unit_price_cop": 40000}],
            shipping=_SHIPPING,
            payment_method="transfer",
            subtotal_cop=40000,
            shipping_cop=7900,
            total_cop=total,
        )
    )


@pytest.mark.asyncio
async def test_register_order_exige_el_total_con_descuento(ctx, _isolate_vault_dir):
    _seed(_isolate_vault_dir, _coupon_md())
    port = FakePort()
    env = await _register(_isolate_vault_dir, ctx, port, total=47900)
    assert env["registered"] is False
    assert env["error_detail"] == "amount_mismatch"
    assert "41900" in env["summary"].replace(".", "") or "41.900" in env["summary"]
    assert port.calls == []


@pytest.mark.asyncio
async def test_register_order_pasa_el_cupon_al_port_y_al_intent_de_pago(ctx, _isolate_vault_dir):
    path = _seed(_isolate_vault_dir, _coupon_md())
    port = FakePort()
    env = await _register(_isolate_vault_dir, ctx, port, total=41900)
    assert env["registered"] is True, env
    assert env["discount_cop"] == 6000
    assert port.calls == [{"total_cop": 41900, "coupon_code": "MAMA15", "discount_cop": 6000}]
    md = _md(path)
    assert md["registered_order"]["discount_cop"] == 6000
    assert md["registered_order"]["coupon_code"] == "MAMA15"
    (intent,) = [i for i in md["pending_ui_intents"] if i["kind"] == "payment_instructions"]
    assert intent["params"]["discount_cop"] == 6000
    assert intent["params"]["coupon_code"] == "MAMA15"
    assert intent["params"]["total_cop"] == 41900


@dataclass
class CapturingPort:
    """Guarda TODO lo que recibe el port (ítems con su reparto incluido)."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    medusa_down: bool = False

    async def register_order(self, **kwargs: Any) -> OrderRegistrationResult:
        self.calls.append(kwargs)
        if self.medusa_down:
            return OrderRegistrationResult(
                success=False, order_id=None, provider="medusa", error_detail="medusa_api_error: HTTP 503"
            )
        return OrderRegistrationResult(
            success=True,
            order_id="draft_c_044",
            provider="medusa",
            raw_payload={"id": "draft_c_044", "display_id": 44},
        )


def _episode_with_coupon(promo: PromotionDTO) -> dict:
    return {
        "episodes": [
            {
                "episode_id": "ep_001",
                "started_at_ms": 1,
                "closed_at_ms": None,
                "applied_coupon": {"code": promo.code, "applied_at_ms": _NOW, "promotion": {**promo.__dict__}},
            }
        ]
    }


_AMOR26 = _promo(
    code="AMOR26", value=10, allocation="each", max_quantity=10, product_ids=("prod_cubo-love",)
)


@pytest.mark.asyncio
async def test_register_order_sends_per_unit_discount_to_the_port(ctx, _isolate_vault_dir):
    """Pedido #44: el cupón llega al port como unidades con descuento de cada
    ítem (el adapter las escribe como línea con el precio descontado, porque
    Medusa no aplica la promoción a un draft); el ítem que no aplica va sin
    descuento."""
    _seed(_isolate_vault_dir, _episode_with_coupon(_AMOR26))
    port = CapturingPort()
    tool = RegisterOrderTool(
        workspace=str(_isolate_vault_dir), vault_dir=_isolate_vault_dir, port=port, catalog=FakeCatalog()
    )
    env = json.loads(
        await tool.execute_with_context(
            ctx,
            items=[
                {"handle": "cubo-love", "quantity": 2, "unit_price_cop": 30000},
                {"handle": "vela-buda", "quantity": 1, "unit_price_cop": 40000},
            ],
            shipping=_SHIPPING,
            payment_method="transfer",
            subtotal_cop=100000,
            shipping_cop=7900,
            total_cop=101900,
        )
    )
    assert env["registered"] is True, env
    (call,) = port.calls
    assert [it.discounted_units for it in call["items"]] == [
        (DiscountedUnits(units=2, discount_unit_cop=3000),),
        (),
    ]
    assert (call["coupon_code"], call["discount_cop"]) == ("AMOR26", 6000)


@pytest.mark.asyncio
async def test_failed_coupon_registration_is_retried_with_the_same_discounted_lines(
    ctx, _isolate_vault_dir
):
    """Medusa caído al registrar un pedido con AMOR26: el record fallido guarda
    el reparto del cupón y el reintento de reconciliación manda exactamente los
    mismos ítems con descuento — Medusa termina con el total que se confirmó."""
    _seed(_isolate_vault_dir, _episode_with_coupon(_AMOR26))
    down = CapturingPort(medusa_down=True)
    tool = RegisterOrderTool(
        workspace=str(_isolate_vault_dir), vault_dir=_isolate_vault_dir, port=down, catalog=FakeCatalog()
    )
    env = json.loads(
        await tool.execute_with_context(
            ctx,
            items=[{"handle": "cubo-love", "quantity": 2, "unit_price_cop": 30000}],
            shipping=_SHIPPING,
            payment_method="transfer",
            subtotal_cop=60000,
            shipping_cop=7900,
            total_cop=61900,
        )
    )
    assert env["registered"] is False

    retry = CapturingPort()
    outcome = await reconcile_one(
        vault_dir=_isolate_vault_dir, session_key=KEY, audit_id=env["audit_id"], port=retry
    )

    assert outcome.is_resolved
    ((original,), (again,)) = down.calls, retry.calls
    assert again["items"] == original["items"]
    assert again["items"][0].discounted_units == (DiscountedUnits(units=2, discount_unit_cop=3000),)
    assert (again["coupon_code"], again["discount_cop"], again["total_cop"]) == ("AMOR26", 6000, 61900)


@pytest.mark.asyncio
async def test_register_order_shipping_coupon_sends_the_shipping_discount(ctx, _isolate_vault_dir):
    """Un cupón de envío llega al port como descuento del envío: ningún ítem
    lleva unidades con descuento."""
    envio = _promo(code="ENVIOGRATIS", value=100, target_type="shipping_methods")
    _seed(_isolate_vault_dir, _episode_with_coupon(envio))
    port = CapturingPort()
    tool = RegisterOrderTool(
        workspace=str(_isolate_vault_dir), vault_dir=_isolate_vault_dir, port=port, catalog=FakeCatalog()
    )
    env = json.loads(
        await tool.execute_with_context(
            ctx,
            items=[{"handle": "vela-buda", "quantity": 1, "unit_price_cop": 40000}],
            shipping=_SHIPPING,
            payment_method="transfer",
            subtotal_cop=40000,
            shipping_cop=7900,
            total_cop=40000,
        )
    )
    assert env["registered"] is True, env
    (call,) = port.calls
    assert call.get("shipping_discount_cop") == 7900
    assert [it.discounted_units for it in call["items"]] == [()]
    assert (call["coupon_code"], call["discount_cop"]) == ("ENVIOGRATIS", 7900)


@pytest.mark.asyncio
async def test_register_order_sin_cupon_no_manda_kwargs_nuevos(ctx, _isolate_vault_dir):
    """Los fakes/ports viejos (sin `coupon_code`) siguen funcionando."""
    _seed(_isolate_vault_dir)

    @dataclass
    class OldPort:
        calls: list = field(default_factory=list)

        async def register_order(self, *, session_key, items, shipping, payment_method,
                                 subtotal_cop, shipping_cop, total_cop, currency="COP",
                                 attribution=None):
            self.calls.append(total_cop)
            return OrderRegistrationResult(success=True, order_id="o1", provider="stub")

    port = OldPort()
    env = await _register(_isolate_vault_dir, ctx, port, total=47900)
    assert env["registered"] is True
    assert port.calls == [47900]



# --- Alcance: el cupón dice A QUÉ productos aplica (incidente AMOR26) ---------


@pytest.mark.asyncio
async def test_apply_coupon_de_productos_lista_los_elegibles_con_precio_y_descuento(
    ctx, _isolate_vault_dir
):
    path = _seed(_isolate_vault_dir)
    promo = _promo(code="AMOR26", value=10, product_ids=("prod_cubo-love",))
    tool = ApplyCouponTool(
        workspace=str(_isolate_vault_dir),
        metadata_store=FilesystemMetadataStore(_isolate_vault_dir),
        promotions=FakePromotionsPort([promo]),
        catalog=FakeCatalog(),
        now_ms=lambda: _NOW,
    )
    env = json.loads(await tool.execute_with_context(ctx, code="amor26"))
    assert env["applied"] is True
    assert env["whole_catalog"] is False
    assert env["eligible_products"] == [
        {"handle": "cubo-love", "title": "Cubo Love", "price_cop": 30000, "discounted_price_cop": 27000}
    ]
    assert "todo el catálogo" not in env["summary"]
    assert "SOLO" in env["summary"] and "Cubo Love" in env["summary"]
    assert "$27.000" in env["summary"]
    # Los elegibles quedan en el episodio: la nota de cada turno los recuerda.
    coupon = _md(path)["episodes"][-1]["applied_coupon"]
    assert [p["handle"] for p in coupon["eligible_products"]] == ["cubo-love"]


class TaggedCatalog(FakeCatalog):
    """Cubo Love en rosado; la Vela Buda no tiene esa etiqueta."""

    products = {
        "vela-buda": _product("vela-buda", "Vela Buda Zen", "40000"),
        "cubo-love": replace(
            _product("cubo-love", "Cubo Love", "30000"),
            tags=["Color: Rosado", "Aroma: Café"],
        ),
    }


@pytest.mark.asyncio
async def test_cupon_con_condicion_de_etiquetas_aplica_solo_a_los_productos_que_las_tienen(
    ctx, _isolate_vault_dir
):
    """Run 28a8e407: AMOR26 con `items.product.tags.id` — ahora se entiende
    (Y con la lista de productos, como en Medusa) en vez de rechazarse."""
    path = _seed(_isolate_vault_dir)
    promo = _promo(
        code="AMOR26",
        value=10,
        product_ids=("prod_cubo-love", "prod_vela-buda"),
        tag_values=("Color: Rosado",),
    )
    tool = ApplyCouponTool(
        workspace=str(_isolate_vault_dir),
        metadata_store=FilesystemMetadataStore(_isolate_vault_dir),
        promotions=FakePromotionsPort([promo]),
        catalog=TaggedCatalog(),
        now_ms=lambda: _NOW,
    )
    env = json.loads(await tool.execute_with_context(ctx, code="AMOR26"))
    assert env["applied"] is True
    assert [p["handle"] for p in env["eligible_products"]] == ["cubo-love"]
    discount = await coupon_discount_for_items(
        _md(path),
        TaggedCatalog(),
        [
            {"handle": "cubo-love", "quantity": 1, "unit_price_cop": 30000},
            {"handle": "vela-buda", "quantity": 1, "unit_price_cop": 40000},
        ],
    )
    assert discount is not None and discount.discount_cop == 3000


@pytest.mark.asyncio
async def test_apply_coupon_de_todo_el_catalogo_lo_dice(ctx, _isolate_vault_dir):
    _seed(_isolate_vault_dir)
    tool = ApplyCouponTool(
        workspace=str(_isolate_vault_dir),
        metadata_store=FilesystemMetadataStore(_isolate_vault_dir),
        promotions=FakePromotionsPort([_promo()]),
        catalog=FakeCatalog(),
        now_ms=lambda: _NOW,
    )
    env = json.loads(await tool.execute_with_context(ctx, code="MAMA15"))
    assert env["whole_catalog"] is True
    assert env["eligible_products"] == []
    assert "todo el catálogo" in env["summary"]


@pytest.mark.asyncio
async def test_apply_coupon_con_alcance_desconocido_no_se_aplica(ctx, _isolate_vault_dir):
    path = _seed(_isolate_vault_dir)
    promo = _promo(code="AMOR26", scope_unresolved=True)
    tool = ApplyCouponTool(
        workspace=str(_isolate_vault_dir),
        metadata_store=FilesystemMetadataStore(_isolate_vault_dir),
        promotions=FakePromotionsPort([promo]),
        catalog=FakeCatalog(),
        now_ms=lambda: _NOW,
    )
    env = json.loads(await tool.execute_with_context(ctx, code="AMOR26"))
    assert env["applied"] is False
    assert env["reason"] == "scope_unresolved"
    assert "todo el catálogo" not in env["summary"]
    assert "applied_coupon" not in _md(path)["episodes"][-1]


@pytest.mark.asyncio
async def test_list_promotions_omite_las_de_alcance_desconocido(ctx, _isolate_vault_dir):
    port = FakePromotionsPort([_promo(), _promo(id="p2", code="AMOR26", scope_unresolved=True)])
    tool = ListPromotionsTool(workspace=str(_isolate_vault_dir), promotions=port, catalog=FakeCatalog())
    env = json.loads(await tool.execute_with_context(ctx))
    assert [p["code"] for p in env["promotions"]] == ["MAMA15"]


def test_nota_del_cupon_ordena_ofrecer_los_elegibles_y_avisa_que_lo_demas_va_sin_descuento():
    from dataclasses import asdict

    promo = _promo(code="AMOR26", value=10, product_ids=("prod_cubo-love",))
    md = {
        "episodes": [
            {
                "episode_id": "ep_1",
                "started_at_ms": 1,
                "closed_at_ms": None,
                "applied_coupon": {
                    "code": "AMOR26",
                    "promotion": asdict(promo),
                    "applied_at_ms": _NOW,
                    "eligible_products": [
                        {"handle": "cubo-love", "title": "Cubo Love",
                         "price_cop": 30000, "discounted_price_cop": 27000}
                    ],
                },
            }
        ]
    }
    note = build_coupon_note(md)
    assert "AMOR26" in note
    assert "SOLO" in note
    assert "Cubo Love ($30.000 → $27.000)" in note
    assert "ofrece" in note.lower()
    assert "sin descuento" in note.lower()


def test_nota_de_cupon_viejo_sin_elegibles_no_inventa_alcance():
    from dataclasses import asdict

    promo = _promo(code="AMOR26", value=10, product_ids=("prod_cubo-love",))
    md = {"episodes": [{"episode_id": "ep_1", "started_at_ms": 1, "closed_at_ms": None,
                        "applied_coupon": {"code": "AMOR26", "promotion": asdict(promo),
                                           "applied_at_ms": _NOW}}]}
    note = build_coupon_note(md)
    assert "todo el catálogo" not in note
    assert "list_promotions" in note
