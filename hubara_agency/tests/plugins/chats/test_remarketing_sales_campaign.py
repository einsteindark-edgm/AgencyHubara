"""Los toques de remarketing siguen el guion de la campaña (operador, 2026-10-19).

Fin de semana 17-18 oct: 120 toques a 37 conversaciones de las velitas y
ninguna compra. Los toques nunca decían el precio ni proponían nada («¿casa o
regalar?», las tarifas de envío, la plantilla). Con la campaña, cada toque
hace lo que dice su archivo, con los precios del catálogo, el combo y el
cupón de bienvenida vigente en Medusa. Las conversaciones sin campaña tienen
el gancho de siempre.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.plugins.chats.agent.remarketing.prompts import build_remarketing_trigger
from src.plugins.chats.shared import sales_campaigns as sc

BLOCK = (
    "GUION DE LA CAMPAÑA «Velitas del 7 de diciembre» (uso interno): este cliente llegó por esta campaña.\n"
    "Precios del catálogo:\n• Caja de Velas de deseos onduladas: $22.500 la caja de 10 unidades\n"
    "Lo que hace ESTE toque: propónle el combo."
)
EXAMPLE = "La ondulada de 10 está en [su precio]; con 2 cajas pagas al recibir. ¿Te aparto el combo?"
_SILENT = dict(
    has_order_draft=False,
    transcript="Cliente: ¡Hola! Quiero más información sobre las velitas del 7 de diciembre.\nAsesor: 🛍️ El bot envió el catálogo con 2 productos",
    catalog_facts="Ficha de los productos de esta charla:\n- Caja de Velas de deseos onduladas: velas de deseos.",
    total_touches=3,
    silence_minutes=130,
    silent_after_catalog=True,
    catalog_described=True,
)


def test_the_hook_of_a_campaign_follows_its_script() -> None:
    text = build_remarketing_trigger(
        "vio las velitas", touch_number=1, sales_campaign_script=BLOCK, sales_campaign_examples=[EXAMPLE], **_SILENT
    )

    assert BLOCK in text
    # El cupón del guion es la excepción a la regla de descuentos.
    assert "cupón de bienvenida que trae el GUION DE LA CAMPAÑA" in text
    # Los precios del guion sí van; lo demás, solo si está en la ficha.
    assert "No repitas precios" not in text
    assert "los precios y el combo del GUION DE LA CAMPAÑA" in text
    assert EXAMPLE in text


# ── la activity que arma el contexto ─────────────────────────────────────────


def _box(handle: str, title: str, price: int):
    from src.sdk.catalogkit import CatalogPriceDTO, CatalogProductDTO, CatalogVariantDTO

    return CatalogProductDTO(
        id=f"prod_{handle}", handle=handle, title=title, status="published", description="Velas de deseos.",
        variants=[CatalogVariantDTO(id=f"var_{handle}", title="Multicolor",
                                    prices=[CatalogPriceDTO(amount=str(price), currency_code="cop")])],
        options={"Color": ["Rojo y verde", "Multicolor"]},
    )


class _Catalog:
    async def search(self, q: str = "", *, limit: int = 10, category: str | None = None):
        return SimpleNamespace(results=[
            _box("velas-de-deseos-onduladas", "Caja de Velas de deseos onduladas", 22_500),
            _box("velas-de-deseos-cuadradas", "Caja de Velas de deseos cuadradas", 25_000),
        ])


def _bienvenida(**extra):
    from src.sdk.connectorkit import PromotionDTO

    fields = dict(
        id="promo_1", code="BIENVENIDA", discount_type="percentage", value=5, currency_code=None,
        target_type="items", allocation="across", max_quantity=None, product_ids=(), variant_ids=(),
        collection_ids=(), min_subtotal_cop=None, is_automatic=False, status="active", starts_at_ms=None,
        ends_at_ms=None, budget_type=None, budget_limit=None, budget_used=None, description=None,
        first_purchase_only=True,
    )
    return PromotionDTO(**{**fields, **extra})


class _Promotions:
    def __init__(self, found: bool, *, slow: bool = False, **extra) -> None:
        self._found, self._slow, self._extra = found, slow, extra

    async def get_by_code(self, code: str):
        if self._slow:
            import asyncio

            await asyncio.sleep(30)
        if not self._found or code != "BIENVENIDA":
            return None
        return _bienvenida(**self._extra)


def _seed(vault, sid: str, *, stamped: bool, bought_before: bool = False) -> None:
    episode = {"episode_id": "ep_2" if bought_before else "ep_1", "msgs_count_at_start": 0}
    if stamped:
        episode[sc.EPISODE_KEY] = "velitas-7-dic"
    earlier = [{"episode_id": "ep_1", "closed_at_ms": 1, "closing_tag": "COMPRA_EXITOSA", "order_id": "order_1"}]
    d = vault / sid
    (d / "sessions").mkdir(parents=True)
    (d / "metadata.json").write_text(
        json.dumps({"tag": "INTERESADO", "motivo": "vio las velitas",
                    "episodes": [*(earlier if bought_before else []), episode]}), encoding="utf-8"
    )
    events = [
        {"role": "user", "content": "¡Hola! Quiero más información sobre las velitas del 7 de diciembre.",
         "timestamp": "2026-10-17T13:00:00+00:00"},
        {"role": "assistant", "content": "🛍️ El bot envió el catálogo con 2 productos", "kind": "ui_component",
         "component_kind": "products_list", "timestamp": "2026-10-17T13:00:30+00:00",
         "card": {"items": [{"title": "Caja de Velas de deseos onduladas"}, {"title": "Caja de Velas de deseos cuadradas"}]}},
    ]
    (d / "sessions" / f"{sid}.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events), encoding="utf-8")


@pytest.fixture
def _env(monkeypatch):
    from src.plugins.chats.agent.remarketing.activities import context as context_mod

    (campaign,) = sc.load_campaigns(Path(__file__).resolve().parents[2] / "fixtures" / "sales_campaigns")
    always = sc.Campaign(**{**vars(campaign), "desde": date(2000, 1, 1), "hasta": date(2999, 1, 1)})
    monkeypatch.setattr(sc, "active_campaigns", lambda: (always,))
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-7")
    monkeypatch.setattr(context_mod, "get_catalog_client", lambda: _Catalog())
    return context_mod, always


async def test_the_context_of_a_campaign_lead_brings_its_touch_with_prices(_isolate_vault_dir, monkeypatch, _env) -> None:
    from temporalio.testing import ActivityEnvironment

    context_mod, campaign = _env
    monkeypatch.setattr(context_mod, "get_promotions_port", lambda: _Promotions(found=True))
    sid = "wa_10000000001"
    _seed(_isolate_vault_dir, sid, stamped=True)

    ctx = await ActivityEnvironment().run(context_mod.read_remarketing_context_activity, sid)

    assert "$22.500 la caja de 10" in ctx.sales_campaign_script
    assert campaign.toques[0] in ctx.sales_campaign_script
    assert "BIENVENIDA da 5 %" in ctx.sales_campaign_script
    assert ctx.sales_campaign_examples == list(campaign.ejemplos_toques)
    assert {"22500", "45000"} <= set(ctx.allowed_numbers)


async def test_a_coupon_not_live_in_medusa_is_not_named(_isolate_vault_dir, monkeypatch, _env) -> None:
    from temporalio.testing import ActivityEnvironment

    context_mod, _ = _env
    monkeypatch.setattr(context_mod, "get_promotions_port", lambda: _Promotions(found=False))
    sid = "wa_10000000002"
    _seed(_isolate_vault_dir, sid, stamped=True)

    ctx = await ActivityEnvironment().run(context_mod.read_remarketing_context_activity, sid)

    assert ctx.sales_campaign_script and "BIENVENIDA" not in ctx.sales_campaign_script


async def test_a_lead_of_no_campaign_has_no_script(_isolate_vault_dir, monkeypatch, _env) -> None:
    from temporalio.testing import ActivityEnvironment

    context_mod, _ = _env
    monkeypatch.setattr(context_mod, "get_promotions_port", lambda: _Promotions(found=True))
    monkeypatch.setattr(sc, "active_campaigns", lambda: ())
    sid = "wa_10000000003"
    _seed(_isolate_vault_dir, sid, stamped=True)

    ctx = await ActivityEnvironment().run(context_mod.read_remarketing_context_activity, sid)

    assert ctx.sales_campaign_script == "" and ctx.sales_campaign_examples == []


async def test_the_trigger_activity_passes_the_script(monkeypatch) -> None:
    from temporalio.testing import ActivityEnvironment

    from src.plugins.chats.agent.remarketing.activities.context import build_remarketing_trigger_v2_activity
    from src.plugins.chats.agent.remarketing.contracts import RemarketingTriggerInput

    text = await ActivityEnvironment().run(
        build_remarketing_trigger_v2_activity,
        RemarketingTriggerInput(motivo="vio las velitas", touch_number=1, sales_campaign_script=BLOCK, sales_campaign_examples=[EXAMPLE]),
    )

    assert BLOCK in text and EXAMPLE in text


async def test_an_expired_coupon_is_not_named(_isolate_vault_dir, monkeypatch, _env) -> None:
    """Revisión del 2026-10-19: Medusa deja la promo «active» cuando su
    campaña termina (venta del 2026-10-09); la vara es `coupon_problem`."""
    from temporalio.testing import ActivityEnvironment

    context_mod, _ = _env
    monkeypatch.setattr(context_mod, "get_promotions_port", lambda: _Promotions(found=True, ends_at_ms=1))
    sid = "wa_10000000004"
    _seed(_isolate_vault_dir, sid, stamped=True)

    ctx = await ActivityEnvironment().run(context_mod.read_remarketing_context_activity, sid)

    assert ctx.sales_campaign_script and "BIENVENIDA" not in ctx.sales_campaign_script


async def test_a_customer_who_already_bought_is_not_offered_the_welcome_coupon(_isolate_vault_dir, monkeypatch, _env) -> None:
    from temporalio.testing import ActivityEnvironment

    context_mod, _ = _env
    monkeypatch.setattr(context_mod, "get_promotions_port", lambda: _Promotions(found=True))
    sid = "wa_10000000005"
    _seed(_isolate_vault_dir, sid, stamped=True, bought_before=True)

    ctx = await ActivityEnvironment().run(context_mod.read_remarketing_context_activity, sid)

    assert ctx.sales_campaign_script and "BIENVENIDA" not in ctx.sales_campaign_script


async def test_a_slow_medusa_does_not_hold_the_touch(_isolate_vault_dir, monkeypatch, _env) -> None:
    """La activity tiene 15 s y ya espera a Jev: el cupón tiene su tope."""
    import time

    from temporalio.testing import ActivityEnvironment

    context_mod, _ = _env
    monkeypatch.setattr(context_mod, "get_promotions_port", lambda: _Promotions(found=True, slow=True))
    sid = "wa_10000000006"
    _seed(_isolate_vault_dir, sid, stamped=True)

    started = time.monotonic()
    ctx = await ActivityEnvironment().run(context_mod.read_remarketing_context_activity, sid)

    assert time.monotonic() - started < 10
    assert ctx.sales_campaign_script and "BIENVENIDA" not in ctx.sales_campaign_script


async def test_a_broken_campaign_leaves_the_usual_hook(_isolate_vault_dir, monkeypatch, _env) -> None:
    from temporalio.testing import ActivityEnvironment

    context_mod, _ = _env
    monkeypatch.setattr(context_mod, "get_promotions_port", lambda: _Promotions(found=True))

    def _boom(*args, **kwargs):
        raise TypeError("can't compare datetime.datetime to datetime.date")

    monkeypatch.setattr(sc, "campaign_for", _boom)
    sid = "wa_10000000007"
    _seed(_isolate_vault_dir, sid, stamped=True)

    ctx = await ActivityEnvironment().run(context_mod.read_remarketing_context_activity, sid)

    assert ctx.sales_campaign_script == ""
