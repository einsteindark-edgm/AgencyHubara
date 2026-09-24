"""Cupo por unidad en la confirmación y el registro del pedido (Fase 5,
parte 2 de CUPONES_PLAN.md).

Con cupo, el descuento va SOLO a las unidades de la combinación producto +
color + aroma que quedan; el resto a precio normal (D2 parcial). El reparto
confirmado se guarda y `register_order` lo relee BAJO EL CANDADO del código:
si otro cliente se llevó la última unidad, no crea el draft y devuelve
`quota_changed` con el total nuevo.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog import CatalogPriceDTO, CatalogProductDTO, CatalogVariantDTO, ProductNotFoundError
from src.platform.catalog.dtos import CatalogManifestDTO, SearchResult
from src.platform.orders.port import DiscountedUnits, OrderRegistrationResult
from src.platform.promotions.port import PromotionDTO, PromotionsUnavailableError
from src.platform.promotions.quota_lock import VaultQuotaLock
from src.platform.promotions.quota_store import FakePromoQuotaStore
from src.platform.promotions.quotas import PromoUnitQuota
from src.plugins.chats.agent.sales.tools.order_registration import RegisterOrderTool
from src.plugins.chats.agent.sales.tools.ui_intents import PresentOrderConfirmationTool

KEY = "wa_test_quota_orders"
_TAGS = ["Color: Rosado", "Color: Azul", "Aroma: Café", "Aroma: Lavanda"]


def _product(handle: str, title: str, price: str, pid: str, tags: list[str]) -> CatalogProductDTO:
    return CatalogProductDTO(
        id=pid, handle=handle, title=title, status="published", tags=tags,
        variants=[CatalogVariantDTO(id=f"variant_{handle}", title="Unico", sku=handle.upper(),
                                    prices=[CatalogPriceDTO(amount=price, currency_code="cop")])],
    )


class _Catalog:
    products = {
        "cubo-love": _product("cubo-love", "Cubo Love", "21000", "prod_cubo", _TAGS),
        "vela-buda": _product("vela-buda", "Vela Buda", "40000", "prod_buda", []),
    }

    async def get_by_handle(self, handle: str) -> CatalogProductDTO:
        try:
            return self.products[handle]
        except KeyError:
            raise ProductNotFoundError(handle) from None

    async def search(self, q: str, *, limit: int = 10, category: str | None = None) -> SearchResult:
        products = list(self.products.values())
        return SearchResult(
            query=q, count=len(products), truncated=False, stale=False,
            manifest=CatalogManifestDTO(version="t", fetched_at="2026-01-01T00:00:00Z", product_count=len(products)),
            results=products,
        )


_AMOR26 = PromotionDTO(
    id="promo_amor26", code="AMOR26", discount_type="percentage", value=10, currency_code=None,
    target_type="items", allocation="across", max_quantity=None,
    product_ids=("prod_cubo",), variant_ids=(), collection_ids=(), min_subtotal_cop=None,
    is_automatic=False, status="active", starts_at_ms=None, ends_at_ms=None,
    budget_type=None, budget_limit=None, budget_used=None, description="AMOR Y AMISTAD 2026",
)


def _snapshot(promo: PromotionDTO) -> dict[str, Any]:
    return {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(promo).items()}


def _seed(vault: Path, key: str = KEY, *, draft_items: list[dict[str, Any]] | None = None) -> Path:
    episode: dict[str, Any] = {
        "episode_id": "ep_001", "started_at_ms": 1, "closed_at_ms": None,
        "applied_coupon": {"code": "AMOR26", "promotion": _snapshot(_AMOR26), "applied_at_ms": 1},
    }
    if draft_items is not None:
        episode["order_draft"] = {"items": draft_items}
    path = vault / key / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"episodes": [episode]}, ensure_ascii=False), encoding="utf-8")
    return path


_Q = "q_rosado_cafe"


def _quotas(units: int = 5) -> FakePromoQuotaStore:
    store = FakePromoQuotaStore()
    store.replace(
        "promo_amor26", "AMOR26",
        [PromoUnitQuota(_Q, "promo_amor26", "AMOR26", "prod_cubo", "cubo-love", "Cubo Love",
                        "Rosado", "Café", units, "2026-09-23T17:00:00Z", "ana")],
        show_units_left=True, actor="ana", now_iso="2026-09-23T17:00:00Z",
    )
    return store


@dataclass
class _Sales:
    sold: dict[str, int] = field(default_factory=dict)
    down: bool = False

    async def sold_units(self, *, since: datetime) -> dict[str, int]:
        if self.down:
            raise PromotionsUnavailableError("timeout")
        await asyncio.sleep(0)
        return dict(self.sold)


def _ctx(key: str = KEY) -> ToolContext:
    return ToolContext(session_key=key, channel="whatsapp", chat_id=key)


def _confirm_tool(vault: Path, sales: _Sales) -> PresentOrderConfirmationTool:
    return PresentOrderConfirmationTool(workspace=str(vault), catalog=_Catalog(), quotas=_quotas(), sales=sales)


async def _confirm(tool: PresentOrderConfirmationTool, ctx: ToolContext, items: list[dict[str, Any]]) -> dict[str, Any]:
    return json.loads(
        await tool.execute_with_context(
            ctx, items=items, shipping_cop=7900,
            shipping_address_summary="Calle 1, Bogotá", payment_method="transfer",
        )
    )


def _cubo(qty: int = 1, **attrs: Any) -> dict[str, Any]:
    return {"handle": "cubo-love", "quantity": qty, "unit_price_cop": 21000, **attrs}


# --- Confirmación ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_present_order_confirmation_rejects_aroma_not_in_product(_isolate_vault_dir) -> None:
    path = _seed(_isolate_vault_dir)

    env = await _confirm(_confirm_tool(_isolate_vault_dir, _Sales()), _ctx(),
                         [_cubo(color="Rosado", aroma="Vainilla")])

    assert env["queued"] is False
    assert env["error"] == "invalid_variant_attribute"
    assert "Vainilla" in env["message"] and "Café" in env["message"]
    assert "total_cop" not in env
    assert "pending_ui_intents" not in json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_confirmation_discounts_only_quota_units_in_mixed_order(_isolate_vault_dir) -> None:
    _seed(_isolate_vault_dir)

    env = await _confirm(_confirm_tool(_isolate_vault_dir, _Sales({_Q: 2})), _ctx(), [
        _cubo(color="rosado", aroma="cafe"),
        _cubo(color="Azul", aroma="Lavanda"),
    ])

    assert env["queued"] is True, env
    assert env["discount_cop"] == 2100
    assert env["total_cop"] == 42000 + 7900 - 2100
    assert "1 × Cubo Love Rosado · Café con AMOR26 (−$2.100)" in env["summary"]


@pytest.mark.asyncio
async def test_confirmation_is_partial_when_asking_more_than_left(_isolate_vault_dir) -> None:
    _seed(_isolate_vault_dir)

    env = await _confirm(_confirm_tool(_isolate_vault_dir, _Sales({_Q: 4})), _ctx(),
                         [_cubo(2, color="Rosado", aroma="Café")])

    assert env["discount_cop"] == 2100
    assert "1 a precio normal" in env["summary"]


@pytest.mark.asyncio
async def test_confirmation_takes_color_and_aroma_from_the_order_draft(_isolate_vault_dir) -> None:
    _seed(_isolate_vault_dir,
          draft_items=[{"producto": "cubo love", "color": "Rosado", "aroma": "Café", "cantidad": 1}])

    env = await _confirm(_confirm_tool(_isolate_vault_dir, _Sales()), _ctx(), [_cubo()])

    assert env["discount_cop"] == 2100


@pytest.mark.asyncio
async def test_confirmation_without_attributes_asks_for_them_before_any_card(_isolate_vault_dir) -> None:
    """Sin color/aroma en una línea con cupo NO sale la tarjeta a precio lleno:
    la tarjeta termina el turno y el cliente confirmaría sin el descuento que
    le prometieron. Se piden primero (con las opciones del producto)."""
    path = _seed(_isolate_vault_dir)

    env = await _confirm(_confirm_tool(_isolate_vault_dir, _Sales()), _ctx(), [_cubo()])

    assert (env["queued"], env["error"]) == (False, "missing_variant_attributes")
    assert "Cubo Love" in env["message"] and "Rosado" in env["message"] and "Café" in env["message"]
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "pending_ui_intents" not in saved
    assert "coupon_confirmed_split" not in saved["episodes"][-1]


@pytest.mark.asyncio
async def test_confirmation_with_sales_unreadable_does_not_discount(_isolate_vault_dir) -> None:
    _seed(_isolate_vault_dir)

    env = await _confirm(_confirm_tool(_isolate_vault_dir, _Sales(down=True)), _ctx(),
                         [_cubo(color="Rosado", aroma="Café")])

    assert "discount_cop" not in env
    assert "no pude confirmar" in env["summary"].lower()


# --- Registro ---------------------------------------------------------------------

_SHIPPING = {"city": "Bogotá", "neighborhood": "Chapinero", "address": "Calle 1 #2-3",
             "phone": "3001234567", "receiver_name": "Ana Pérez"}


@dataclass
class _Port:
    """Port que 'vende' las unidades con cupo que registra (como Medusa)."""

    sales: _Sales
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def register_order(self, **kwargs: Any) -> OrderRegistrationResult:
        await asyncio.sleep(0.02)  # Medusa tarda: sin candado la carrera queda abierta
        self.calls.append(kwargs)
        for item in kwargs["items"]:
            for group in item.discounted_units:
                if group.quota_id:
                    self.sales.sold[group.quota_id] = self.sales.sold.get(group.quota_id, 0) + group.units
        n = len(self.calls)
        return OrderRegistrationResult(success=True, order_id=f"draft_{n}", provider="medusa",
                                       raw_payload={"id": f"draft_{n}", "display_id": 40 + n})


def _register_tool(vault: Path, port: _Port) -> RegisterOrderTool:
    return RegisterOrderTool(
        workspace=str(vault), vault_dir=vault, port=port, catalog=_Catalog(),
        quotas=_quotas(), sales=port.sales, quota_lock=VaultQuotaLock(vault),
    )


async def _register(tool: RegisterOrderTool, ctx: ToolContext, *, total: int) -> dict[str, Any]:
    return json.loads(
        await tool.execute_with_context(
            ctx, items=[_cubo(color="Rosado", aroma="Café")], shipping=_SHIPPING,
            payment_method="transfer", subtotal_cop=21000, shipping_cop=7900, total_cop=total,
        )
    )


_WITH_DISCOUNT = 21000 + 7900 - 2100


@pytest.mark.asyncio
async def test_register_order_sends_the_quota_of_each_discounted_unit(_isolate_vault_dir) -> None:
    _seed(_isolate_vault_dir)
    sales = _Sales()
    await _confirm(_confirm_tool(_isolate_vault_dir, sales), _ctx(), [_cubo(color="Rosado", aroma="Café")])
    port = _Port(sales)

    env = await _register(_register_tool(_isolate_vault_dir, port), _ctx(), total=_WITH_DISCOUNT)

    assert env["registered"] is True, env
    (call,) = port.calls
    assert call["items"][0].discounted_units == (DiscountedUnits(1, 2100, quota_id=_Q),)


@pytest.mark.asyncio
async def test_register_order_quota_changed_returns_new_total_without_draft(_isolate_vault_dir) -> None:
    _seed(_isolate_vault_dir)
    sales = _Sales({_Q: 4})  # queda 1 al confirmar
    await _confirm(_confirm_tool(_isolate_vault_dir, sales), _ctx(), [_cubo(color="Rosado", aroma="Café")])
    sales.sold[_Q] = 5  # otro cliente se llevó la última
    port = _Port(sales)

    env = await _register(_register_tool(_isolate_vault_dir, port), _ctx(), total=_WITH_DISCOUNT)

    assert env["registered"] is False
    assert env["error_detail"] == "quota_changed"
    assert env["new_total_cop"] == 21000 + 7900
    assert "present_order_confirmation" in env["summary"]
    assert port.calls == []


@pytest.mark.asyncio
async def test_register_with_quota_never_sells_the_last_unit_twice(_isolate_vault_dir) -> None:
    """Dos clientes confirmaron la última unidad: uno registra, el otro recibe
    `quota_changed` (sin draft)."""
    sales = _Sales({_Q: 4})
    port = _Port(sales)
    keys = ["wa_test_quota_a", "wa_test_quota_b"]
    for key in keys:
        _seed(_isolate_vault_dir, key)
        await _confirm(_confirm_tool(_isolate_vault_dir, sales), _ctx(key), [_cubo(color="Rosado", aroma="Café")])

    results = await asyncio.gather(*[
        _register(_register_tool(_isolate_vault_dir, port), _ctx(key), total=_WITH_DISCOUNT) for key in keys
    ])

    assert sorted(r["registered"] for r in results) == [False, True]
    assert [r.get("error_detail") for r in results if not r["registered"]] == ["quota_changed"]
    assert len(port.calls) == 1 and sales.sold[_Q] == 5


# --- Segunda revisión: idempotencia en el borde del cupo y fallas cerradas ------------


def _fingerprint(call: dict[str, Any]) -> str:
    from src.platform.orders.medusa_order import _compute_order_fingerprint

    return _compute_order_fingerprint(call["items"], call["total_cop"], call["payment_method"])


@pytest.mark.asyncio
async def test_register_retry_of_the_same_order_at_the_last_unit_is_idempotent(_isolate_vault_dir) -> None:
    """El reintento del MISMO pedido no cuenta su propio draft como vendido:
    pide el mismo pedido (mismo fingerprint → el adapter reusa el draft) en
    vez de responder `quota_changed` y terminar duplicándolo a precio lleno."""
    _seed(_isolate_vault_dir)
    sales = _Sales({_Q: 4})  # queda 1
    await _confirm(_confirm_tool(_isolate_vault_dir, sales), _ctx(), [_cubo(color="Rosado", aroma="Café")])
    port = _Port(sales)
    tool = _register_tool(_isolate_vault_dir, port)

    first = await _register(tool, _ctx(), total=_WITH_DISCOUNT)
    again = await _register(tool, _ctx(), total=_WITH_DISCOUNT)

    assert first["registered"] is True and again["registered"] is True, again
    assert _fingerprint(port.calls[0]) == _fingerprint(port.calls[1])


@pytest.mark.asyncio
async def test_register_without_a_confirmed_split_asks_to_confirm_it(_isolate_vault_dir) -> None:
    """Con cupo, el cliente tiene que haber VISTO el reparto (confirmación)."""
    _seed(_isolate_vault_dir)
    port = _Port(_Sales())

    env = await _register(_register_tool(_isolate_vault_dir, port), _ctx(), total=_WITH_DISCOUNT)

    assert (env["registered"], env["error_detail"]) == (False, "quota_changed")
    assert port.calls == []


@pytest.mark.asyncio
async def test_register_rejects_a_color_the_product_does_not_have(_isolate_vault_dir) -> None:
    _seed(_isolate_vault_dir)
    port = _Port(_Sales())
    tool = _register_tool(_isolate_vault_dir, port)

    env = json.loads(await tool.execute_with_context(
        _ctx(), items=[_cubo(color="Verde", aroma="Café")], shipping=_SHIPPING,
        payment_method="transfer", subtotal_cop=21000, shipping_cop=7900, total_cop=28900,
    ))

    assert (env["registered"], env["error_detail"]) == (False, "invalid_variant_attribute")
    assert "Verde" in env["summary"]
    assert port.calls == []


@pytest.mark.asyncio
async def test_register_with_quota_but_no_lock_fails_closed(_isolate_vault_dir) -> None:
    _seed(_isolate_vault_dir)
    port = _Port(_Sales())
    tool = RegisterOrderTool(workspace=str(_isolate_vault_dir), vault_dir=_isolate_vault_dir, port=port,
                             catalog=_Catalog(), quotas=_quotas(), sales=port.sales, quota_lock=None)

    env = await _register(tool, _ctx(), total=_WITH_DISCOUNT)

    assert (env["registered"], env["error_detail"]) == (False, "quota_unavailable")
    assert port.calls == []


@pytest.mark.asyncio
async def test_register_order_returns_quota_busy_when_the_lock_times_out(_isolate_vault_dir, monkeypatch) -> None:
    import src.plugins.chats.agent.sales.tools.order_registration as reg

    _seed(_isolate_vault_dir)
    monkeypatch.setattr(reg, "_QUOTA_LOCK_TIMEOUT_S", 0.1)
    port = _Port(_Sales())
    lock = VaultQuotaLock(_isolate_vault_dir)

    async with lock.hold("AMOR26", timeout_s=1):
        env = await _register(_register_tool(_isolate_vault_dir, port), _ctx(), total=_WITH_DISCOUNT)

    assert (env["registered"], env["error_detail"]) == (False, "quota_busy")
    assert port.calls == []


@pytest.mark.asyncio
async def test_confirmation_with_quota_but_no_sales_reader_does_not_discount(_isolate_vault_dir) -> None:
    _seed(_isolate_vault_dir)
    tool = PresentOrderConfirmationTool(workspace=str(_isolate_vault_dir), catalog=_Catalog(),
                                        quotas=_quotas(), sales=None)

    env = await _confirm(tool, _ctx(), [_cubo(color="Rosado", aroma="Café")])

    assert env["queued"] is True and "discount_cop" not in env
    assert "no pude confirmar" in env["summary"].lower()


# --- Premortem tras #353: rechazos con `error` y cupo ilegible al registrar ---------------


@pytest.mark.asyncio
async def test_register_quota_rejections_carry_error_so_the_bot_fixes_and_retries(
    _isolate_vault_dir, monkeypatch
) -> None:
    """#353: `registered=false` CON `error` → el bot corrige y reintenta (y el
    workflow no deja salir un send_reply del mismo paso); SIN `error` escala
    como si Medusa hubiera rechazado el pedido. Los rechazos del cupo son de
    la primera clase."""
    import src.plugins.chats.agent.sales.tools.order_registration as reg

    _seed(_isolate_vault_dir)
    tool = _register_tool(_isolate_vault_dir, _Port(_Sales()))

    changed = await _register(tool, _ctx(), total=_WITH_DISCOUNT)  # sin reparto confirmado
    bad_color = json.loads(await tool.execute_with_context(
        _ctx(), items=[_cubo(color="Verde", aroma="Café")], shipping=_SHIPPING,
        payment_method="transfer", subtotal_cop=21000, shipping_cop=7900, total_cop=28900,
    ))
    monkeypatch.setattr(reg, "_QUOTA_LOCK_TIMEOUT_S", 0.1)
    async with VaultQuotaLock(_isolate_vault_dir).hold("AMOR26", timeout_s=1):
        busy = await _register(tool, _ctx(), total=_WITH_DISCOUNT)

    assert [e.get("error") for e in (changed, bad_color, busy)] == [
        "quota_changed", "invalid_variant_attribute", "quota_busy",
    ]


class _BrokenQuotas:
    """Hoja de cupos ilegible (disco del vault con problemas)."""

    def get(self, promotion_id: str) -> Any:
        from src.platform.promotions.quota_store import QuotaStoreError

        raise QuotaStoreError(f"no pude leer {promotion_id}")


@pytest.mark.asyncio
@pytest.mark.parametrize("unreadable", ["sales", "quota_sheet"])
async def test_register_that_cannot_reread_the_quota_keeps_the_confirmed_order_for_retry(
    _isolate_vault_dir, unreadable: str
) -> None:
    """Sin poder releer lo vendido NO es "cambiaron las unidades" (el cliente
    leería un total falso): no se crea nada, el pedido queda guardado con el
    reparto que el cliente CONFIRMÓ para que la reconciliación lo reintente
    bajo el candado, y el bot escala como con Medusa caído (sin `error`)."""
    path = _seed(_isolate_vault_dir)
    sales = _Sales({_Q: 4})
    await _confirm(_confirm_tool(_isolate_vault_dir, sales), _ctx(), [_cubo(color="Rosado", aroma="Café")])
    port = _Port(sales)
    tool = _register_tool(_isolate_vault_dir, port)
    if unreadable == "sales":
        sales.down = True
    else:
        tool = RegisterOrderTool(workspace=str(_isolate_vault_dir), vault_dir=_isolate_vault_dir, port=port,
                                 catalog=_Catalog(), quotas=_BrokenQuotas(), sales=sales,
                                 quota_lock=VaultQuotaLock(_isolate_vault_dir))

    env = await _register(tool, _ctx(), total=_WITH_DISCOUNT)

    assert env["registered"] is False and "error" not in env, env
    assert str(env["error_detail"]).startswith("quota_unavailable")
    assert env["audit_id"] and "ORDER_REGISTRATION_FAILED" in env["summary"]
    assert port.calls == []
    (record,) = json.loads(path.read_text(encoding="utf-8"))["failed_order_registrations"]
    assert (record["status"], record["total_cop"], record["discount_cop"]) == ("pending", _WITH_DISCOUNT, 2100)
    assert record["coupon_line_discounts"] == [
        {"index": 0, "units": 1, "discount_unit_cop": 2100, "quota_id": _Q},
    ]


_BUDA = {"handle": "vela-buda", "quantity": 1, "unit_price_cop": 40000}
_MIXED_TOTAL = 21000 + 40000 + 7900 - 2100


async def _register_items(tool: RegisterOrderTool, items: list[dict[str, Any]], *, total: int) -> dict[str, Any]:
    return json.loads(await tool.execute_with_context(
        _ctx(), items=items, shipping=_SHIPPING, payment_method="transfer",
        subtotal_cop=sum(i["unit_price_cop"] * i["quantity"] for i in items), shipping_cop=7900, total_cop=total,
    ))


@pytest.mark.asyncio
async def test_register_with_the_items_in_another_order_is_the_same_confirmed_order(_isolate_vault_dir) -> None:
    """El LLM puede listar los ítems en otro orden al registrar: es el MISMO
    pedido (no `quota_changed` con el mismo total) y el descuento va a la
    línea del cubo, no a la que quedó en su posición."""
    _seed(_isolate_vault_dir)
    sales = _Sales({_Q: 4})
    await _confirm(_confirm_tool(_isolate_vault_dir, sales), _ctx(), [_cubo(color="Rosado", aroma="Café"), _BUDA])
    port = _Port(sales)

    env = await _register_items(_register_tool(_isolate_vault_dir, port),
                                [_BUDA, _cubo(color="Rosado", aroma="Café")], total=_MIXED_TOTAL)

    assert env["registered"] is True, env
    (call,) = port.calls
    assert [it.discounted_units for it in call["items"]] == [(), (DiscountedUnits(1, 2100, quota_id=_Q),)]


@pytest.mark.asyncio
async def test_retry_of_the_same_order_with_reordered_items_reuses_the_draft(_isolate_vault_dir) -> None:
    """Reintento del pedido YA registrado con los ítems en otro orden: el
    reparto registrado se aplica a la línea correcta (mismo fingerprint → el
    adapter reusa el draft) en vez de descontarle a otra y duplicar."""
    _seed(_isolate_vault_dir)
    sales = _Sales({_Q: 4})  # queda 1: el reintento ve su propio draft como vendido
    await _confirm(_confirm_tool(_isolate_vault_dir, sales), _ctx(), [_cubo(color="Rosado", aroma="Café"), _BUDA])
    port = _Port(sales)
    tool = _register_tool(_isolate_vault_dir, port)

    first = await _register_items(tool, [_cubo(color="Rosado", aroma="Café"), _BUDA], total=_MIXED_TOTAL)
    again = await _register_items(tool, [_BUDA, _cubo(color="Rosado", aroma="Café")], total=_MIXED_TOTAL)

    assert first["registered"] is True and again["registered"] is True, again
    assert _fingerprint(port.calls[0]) == _fingerprint(port.calls[1])
    assert port.calls[1]["items"][0].discounted_units == ()


def test_prompts_treat_quota_rejections_as_fix_and_retry() -> None:
    """Los rechazos del cupo llevan `error` (#353): la descripción de la tool y
    el guion de cierre los ponen con los que se corrigen y reintentan, no con
    el rechazo de Medusa que escala."""
    from src.plugins.chats.agent.sales.tools import order_registration as reg

    retry = reg.RegisterOrderTool.description.split("sin `error`")[0]
    stage = (Path(reg.__file__).parents[1] / "workspace" / "skills" / "etapa_cierre" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    step4 = stage[stage.index("4. Lee el envelope"): stage.index("\n5. ")]
    with_error = next(line for line in step4.splitlines() if "con `error`" in line)
    for reason in ("invalid_variant_attribute", "quota_changed", "quota_busy"):
        assert reason in retry, reason
        assert reason in with_error, reason



# --- B1: el color/aroma se valida SOLO donde el cupo lo necesita ----------------------------

_Catalog.products["cubo-sol"] = _product("cubo-sol", "Cubo Sol", "21000", "prod_sol", _TAGS)


def _seed_without_coupon(vault: Path) -> Path:
    path = vault / KEY / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"episodes": [{"episode_id": "ep_001", "started_at_ms": 1}]}), encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_order_without_coupon_accepts_attributes_outside_the_tag_lists(_isolate_vault_dir) -> None:
    """Sin cupón nada cambia: `set_order_slot` guarda valores que no están en
    las etiquetas (dos aromas, colores de `metadata.colores`, familias) y el
    LLM los copia; la confirmación y el registro los aceptan como antes."""
    _seed_without_coupon(_isolate_vault_dir)
    item = _cubo(color="naranja", aroma="Drakar, Café")

    confirmed = await _confirm(_confirm_tool(_isolate_vault_dir, _Sales()), _ctx(), [item])
    registered = json.loads(await _register_tool(_isolate_vault_dir, _Port(_Sales())).execute_with_context(
        _ctx(), items=[item], shipping=_SHIPPING, payment_method="transfer",
        subtotal_cop=21000, shipping_cop=7900, total_cop=28900,
    ))

    assert confirmed["queued"] is True, confirmed
    assert registered["registered"] is True, registered


@pytest.mark.asyncio
async def test_coupon_with_quota_only_checks_the_products_it_covers(_isolate_vault_dir) -> None:
    """AMOR26 tiene cupo solo en Cubo Love: una línea de otro producto con un
    color fuera de su lista no frena la confirmación."""
    _seed(_isolate_vault_dir)

    env = await _confirm(_confirm_tool(_isolate_vault_dir, _Sales()), _ctx(), [
        _cubo(color="Rosado", aroma="Café"),
        {"handle": "cubo-sol", "quantity": 1, "unit_price_cop": 21000, "color": "naranja"},
    ])

    assert env["queued"] is True, env
    assert env["discount_cop"] == 2100


@pytest.mark.asyncio
async def test_two_aromas_in_one_quota_line_asks_for_one_line_per_combination(_isolate_vault_dir) -> None:
    _seed(_isolate_vault_dir)

    env = await _confirm(_confirm_tool(_isolate_vault_dir, _Sales()), _ctx(),
                         [_cubo(2, color="Rosado", aroma="Café, Lavanda")])

    assert (env["queued"], env["error"]) == (False, "invalid_variant_attribute")
    assert "una línea por combinación" in env["message"]


# --- C1: el color y el aroma llegan al pedido de Medusa ----------------------------------


@pytest.mark.asyncio
async def test_register_sends_the_chosen_color_and_aroma_of_each_line(_isolate_vault_dir) -> None:
    """Con cupo, los canónicos de la lista del producto; sin cupo en esa línea,
    lo que dijo el cliente tal cual (el equipo tiene que saber qué despachar)."""
    _seed(_isolate_vault_dir)
    sales = _Sales()
    items = [_cubo(color="rosado", aroma="cafe"),
             {"handle": "cubo-sol", "quantity": 1, "unit_price_cop": 21000, "color": "naranja", "aroma": "Drakar, Café"}]
    await _confirm(_confirm_tool(_isolate_vault_dir, sales), _ctx(), items)
    port = _Port(sales)

    env = await _register_items(_register_tool(_isolate_vault_dir, port), items, total=21000 + 21000 + 7900 - 2100)

    assert env["registered"] is True, env
    assert [(it.color, it.aroma) for it in port.calls[0]["items"]] == [("Rosado", "Café"), ("naranja", "Drakar, Café")]


@pytest.mark.asyncio
async def test_draft_color_and_aroma_only_fill_a_product_that_appears_once(_isolate_vault_dir) -> None:
    """C9: el borrador guarda UN color/aroma por producto. Con dos líneas del
    mismo producto no se sabe cuál es cuál: se piden, en vez de descontar las
    dos como Rosado · Café (y gastar dos unidades de esa fila)."""
    _seed(_isolate_vault_dir,
          draft_items=[{"producto": "cubo love", "color": "Rosado", "aroma": "Café", "cantidad": 1}])

    env = await _confirm(_confirm_tool(_isolate_vault_dir, _Sales()), _ctx(), [_cubo(), _cubo()])

    assert (env["queued"], env["error"]) == (False, "missing_variant_attributes"), env


def test_remembering_the_split_never_writes_over_an_unreadable_session() -> None:
    """C11: `store.update` escribe lo que devuelve el mutator. Si la lectura
    llegó vacía (otro writer a medio escribir), guardar el reparto NO puede
    pisar la sesión con `{}`: sin episodio activo, no se escribe."""
    from src.plugins.chats.agent.sales.use_cases.coupon_quota import remember_confirmed_split

    assert remember_confirmed_split({}, "AMOR26", []) is None
    saved = remember_confirmed_split({"episodes": [{"episode_id": "ep_1", "started_at_ms": 1}]}, "AMOR26", [])
    assert saved is not None and saved["episodes"][-1]["coupon_confirmed_split"] == {"code": "AMOR26", "split": []}


# --- Premortem del cupo en el bot (B-series) ----------------------------------------------

_Q_AZUL = "q_azul_lavanda"


def _quotas_two(*, rosado: int = 5, azul: int = 5) -> FakePromoQuotaStore:
    store = FakePromoQuotaStore()
    store.replace(
        "promo_amor26", "AMOR26",
        [PromoUnitQuota(_Q, "promo_amor26", "AMOR26", "prod_cubo", "cubo-love", "Cubo Love",
                        "Rosado", "Café", rosado, "2026-09-23T17:00:00Z", "ana"),
         PromoUnitQuota(_Q_AZUL, "promo_amor26", "AMOR26", "prod_cubo", "cubo-love", "Cubo Love",
                        "Azul", "Lavanda", azul, "2026-09-23T17:00:00Z", "ana")],
        show_units_left=True, actor="ana", now_iso="2026-09-23T17:00:00Z",
    )
    return store


@pytest.mark.asyncio
async def test_sold_out_combination_is_told_as_sold_out_not_as_not_applicable(_isolate_vault_dir) -> None:
    """B3: se agotó Rosado · Café pero quedan de Azul · Lavanda. Decirle al
    cliente "el cupón no aplica a estos productos" es falso."""
    _seed(_isolate_vault_dir)
    tool = PresentOrderConfirmationTool(workspace=str(_isolate_vault_dir), catalog=_Catalog(),
                                        quotas=_quotas_two(), sales=_Sales({_Q: 5}))

    env = await _confirm(tool, _ctx(), [_cubo(color="Rosado", aroma="Café")])

    assert env["queued"] is True, env
    assert "se agotaron" in env["summary"] and "no aplica" not in env["summary"]


@pytest.mark.asyncio
async def test_card_itself_says_which_units_carry_the_coupon(_isolate_vault_dir) -> None:
    """B4: la tarjeta TERMINA el turno — lo que el bot "diría" después (1 a
    precio normal, no pude confirmar el cupo) no le llega al cliente. Va en
    la tarjeta."""
    path = _seed(_isolate_vault_dir)

    await _confirm(_confirm_tool(_isolate_vault_dir, _Sales({_Q: 4})), _ctx(), [_cubo(2, color="Rosado", aroma="Café")])

    (intent,) = json.loads(path.read_text(encoding="utf-8"))["pending_ui_intents"]
    assert "1 a precio normal" in intent["params"]["coupon_note"]


@pytest.mark.asyncio
async def test_card_says_the_units_could_not_be_checked(_isolate_vault_dir) -> None:
    path = _seed(_isolate_vault_dir)

    await _confirm(_confirm_tool(_isolate_vault_dir, _Sales(down=True)), _ctx(), [_cubo(color="Rosado", aroma="Café")])

    (intent,) = json.loads(path.read_text(encoding="utf-8"))["pending_ui_intents"]
    assert "no pude confirmar" in intent["params"]["coupon_note"].lower()


@pytest.mark.asyncio
async def test_order_card_prints_the_coupon_note() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from src.platform.whatsapp import dtos as wa_dtos
    from src.plugins.chats.agent.sales.activities.flush_ui_intents import _dispatch_intent

    wa = SimpleNamespace(send_text=AsyncMock(return_value=SimpleNamespace(ok=True)),
                         send_interactive_buttons=AsyncMock(return_value=SimpleNamespace(ok=True)))
    await _dispatch_intent(
        wa_client=wa, wa_dtos=wa_dtos, kind="order_confirmation", fallback={},
        params={"reference_id": "HUB-1", "items": [{"title": "Cubo Love", "quantity": 2, "unit_price_cop": 21000}],
                "subtotal_cop": 42000, "shipping_cop": 7900, "total_cop": 47800, "currency": "COP",
                "shipping_address_summary": "Calle 1", "payment_method": "transfer",
                "discount_cop": 2100, "coupon_code": "AMOR26",
                "coupon_note": "1 × Cubo Love Rosado · Café con AMOR26 (−$2.100); 1 a precio normal"},
        phone_number_id="phone-1", to_number="573000000000", last_inbound_message_id=None,
    )

    body = wa.send_interactive_buttons.await_args.args[2].body
    assert "1 a precio normal" in body


@pytest.mark.asyncio
async def test_offer_skips_rows_whose_color_no_longer_exists_on_the_product(_isolate_vault_dir) -> None:
    """B6: una fila vieja (color renombrado en Medusa) no se ofrece: la
    confirmación la rechazaría con `invalid_variant_attribute`."""
    from src.plugins.chats.agent.sales.use_cases.coupon_quota import quota_offer

    store = FakePromoQuotaStore()
    store.replace("promo_amor26", "AMOR26", [
        PromoUnitQuota(_Q, "promo_amor26", "AMOR26", "prod_cubo", "cubo-love", "Cubo Love",
                       "Rosado", "Café", 5, "2026-09-23T17:00:00Z", "ana"),
        PromoUnitQuota("q_verde", "promo_amor26", "AMOR26", "prod_cubo", "cubo-love", "Cubo Love",
                       "Verde", "Café", 5, "2026-09-23T17:00:00Z", "ana"),
    ], show_units_left=True, actor="ana", now_iso="2026-09-23T17:00:00Z")

    offer = await quota_offer(_AMOR26, quotas=store, sales=_Sales(), catalog=_Catalog())

    assert [u["color"] for u in offer.units] == ["Rosado"]


@pytest.mark.asyncio
async def test_quota_respects_the_coupon_max_quantity_per_line(_isolate_vault_dir) -> None:
    """B10: un cupón de Medusa con `each` + máximo 1 por línea sigue valiendo
    1 unidad por línea aunque el cupo tenga más."""
    from dataclasses import replace as dc_replace

    promo = dc_replace(_AMOR26, allocation="each", max_quantity=1)
    path = _isolate_vault_dir / KEY / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"episodes": [{
        "episode_id": "ep_001", "started_at_ms": 1,
        "applied_coupon": {"code": "AMOR26", "promotion": _snapshot(promo), "applied_at_ms": 1},
    }]}), encoding="utf-8")

    env = await _confirm(_confirm_tool(_isolate_vault_dir, _Sales()), _ctx(), [_cubo(3, color="Rosado", aroma="Café")])

    assert env["discount_cop"] == 2100


@pytest.mark.asyncio
async def test_label_that_contradicts_the_chosen_color_is_rejected_on_a_quota_line(_isolate_vault_dir) -> None:
    """B7: el descuento y el cupo van por `color`/`aroma`; si el
    `variant_label` dice otra cosa, la línea de Medusa diría una y cobraría
    otra. Se corrige antes."""
    _seed(_isolate_vault_dir)

    env = await _confirm(_confirm_tool(_isolate_vault_dir, _Sales()), _ctx(),
                         [_cubo(color="Rosado", aroma="Café", variant_label="Lavanda, Azul")])

    assert (env["queued"], env["error"]) == (False, "invalid_variant_attribute")
    assert "Lavanda, Azul" in env["message"]


@pytest.mark.asyncio
async def test_confirmation_rejected_for_price_does_not_scan_medusa(_isolate_vault_dir) -> None:
    """B8: un rechazo barato (precio, envío) no espera el barrido de Medusa."""
    _seed(_isolate_vault_dir)

    @dataclass
    class _CountingSales(_Sales):
        reads: int = 0

        async def sold_units(self, *, since: datetime) -> dict[str, int]:
            self.reads += 1
            return await super().sold_units(since=since)

    sales = _CountingSales()
    env = await _confirm(_confirm_tool(_isolate_vault_dir, sales), _ctx(),
                         [{**_cubo(color="Rosado", aroma="Café"), "unit_price_cop": 19000}])

    assert env["error"] == "price_mismatch"
    assert sales.reads == 0


@pytest.mark.asyncio
async def test_a_hung_sales_read_fails_closed_within_the_deadline(_isolate_vault_dir, monkeypatch) -> None:
    """C10/B8: Medusa lento no deja la confirmación (ni el candado del
    registro) colgada: pasado el plazo, `quota_unavailable`."""
    import src.plugins.chats.agent.sales.use_cases.coupon_quota as cq

    monkeypatch.setattr(cq, "QUOTA_READ_TIMEOUT_S", 0.05)
    _seed(_isolate_vault_dir)

    @dataclass
    class _HungSales(_Sales):
        async def sold_units(self, *, since: datetime) -> dict[str, int]:
            await asyncio.sleep(5)
            return {}

    env = await asyncio.wait_for(
        _confirm(_confirm_tool(_isolate_vault_dir, _HungSales()), _ctx(), [_cubo(color="Rosado", aroma="Café")]),
        timeout=2,
    )

    assert "discount_cop" not in env and "no pude confirmar" in env["summary"].lower()


@pytest.mark.asyncio
async def test_offer_is_exhausted_when_only_rows_outside_the_coupon_scope_have_units() -> None:
    """A15: una fila de un producto que el cupón ya no cubre no cuenta para
    "quedan": con las del alcance agotadas el cupón está agotado (no "no pude
    confirmar")."""
    from src.plugins.chats.agent.sales.use_cases.coupon_quota import quota_offer

    store = FakePromoQuotaStore()
    store.replace("promo_amor26", "AMOR26", [
        PromoUnitQuota(_Q, "promo_amor26", "AMOR26", "prod_cubo", "cubo-love", "Cubo Love",
                       "Rosado", "Café", 5, "2026-09-23T17:00:00Z", "ana"),
        PromoUnitQuota("q_buda", "promo_amor26", "AMOR26", "prod_buda", "vela-buda", "Vela Buda",
                       None, None, 5, "2026-09-23T17:00:00Z", "ana"),
    ], show_units_left=True, actor="ana", now_iso="2026-09-23T17:00:00Z")

    offer = await quota_offer(_AMOR26, quotas=store, sales=_Sales({_Q: 5}), catalog=_Catalog())

    assert offer.reason == "quota_exhausted"
