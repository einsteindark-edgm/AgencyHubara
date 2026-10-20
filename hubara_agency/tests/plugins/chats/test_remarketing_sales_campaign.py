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
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

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
    assert "el cupón que trae el GUION DE LA CAMPAÑA, tal como viene" in text
    assert "ni su fecha" in text  # la fecha límite es la de Medusa, no se cambia
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


_BOGOTA = ZoneInfo("America/Bogota")
#: El último día del cupón, en 10 días (el dashboard guarda el fin al día siguiente a las 00:00).
LAST_DAY = datetime.now(_BOGOTA).date() + timedelta(days=10)


def _bienvenida(**extra):
    from src.sdk.connectorkit import PromotionDTO

    ends = datetime.combine(LAST_DAY + timedelta(days=1), datetime.min.time(), tzinfo=_BOGOTA)
    fields = dict(
        id="promo_1", code="BIENVENIDA", discount_type="percentage", value=5, currency_code=None,
        target_type="items", allocation="across", max_quantity=None, product_ids=(), variant_ids=(),
        collection_ids=(), min_subtotal_cop=None, is_automatic=False, status="active", starts_at_ms=None,
        ends_at_ms=int(ends.timestamp() * 1000), budget_type=None, budget_limit=None, budget_used=None,
        description=None, first_purchase_only=True,
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


def _seed(
    vault, sid: str, *, stamped: bool, bought_before: bool = False, touch: int = 1, intent: bool = False,
    earlier_touches: int = 0, episode_extra: dict | None = None, decisions_bytes: bytes | None = None,
    earlier_kinds: tuple[str, ...] = (),
) -> None:
    """Una conversación de la campaña en su toque `touch`; con `intent`, Jev
    leyó en un mensaje que el cliente eligió un producto."""
    last_inbound = int(time.time() * 1000) - 3 * 3_600_000
    episode = {"episode_id": "ep_2" if bought_before else "ep_1", "msgs_count_at_start": 0,
               "started_at_ms": last_inbound - 60_000}
    if stamped:
        episode[sc.EPISODE_KEY] = "velitas-7-dic"
    episode.update(episode_extra or {})
    earlier = [{"episode_id": "ep_1", "closed_at_ms": 1, "closing_tag": "COMPRA_EXITOSA", "order_id": "order_1"}]
    # Toques de un ciclo anterior del MISMO episodio (el cliente respondió y se
    # calló otra vez): la escalera vuelve a contar desde su último mensaje.
    before = [{"at_ms": last_inbound - 30_000 + i} for i in range(earlier_touches)]
    before += [{"at_ms": last_inbound - 20_000 + i, "kind": kind} for i, kind in enumerate(earlier_kinds)]
    d = vault / sid
    (d / "sessions").mkdir(parents=True)
    (d / "metadata.json").write_text(
        json.dumps({"tag": "INTERESADO", "motivo": "vio las velitas", "last_inbound_at_ms": last_inbound,
                    "remarketing_touches": [*before, *({"at_ms": last_inbound + 60_000 * (i + 1)} for i in range(touch - 1))],
                    "episodes": [*(earlier if bought_before else []), episode]}), encoding="utf-8"
    )
    if decisions_bytes is not None:
        (d / "evals").mkdir(parents=True)
        (d / "evals" / "decisions.jsonl").write_bytes(decisions_bytes)
    elif intent:
        (d / "evals").mkdir(parents=True)
        reading = {"at_ms": last_inbound, "stage": "ingest", "message_id": "wamid.X", "capability": "compra",
                   "by": "jev", "provider": "jev", "value": [None, "text"],
                   "answers": [{"q": "compra.que_hace", "choice": "elige", "confidence": 0.92}]}
        (d / "evals" / "decisions.jsonl").write_text(json.dumps(reading) + "\n", encoding="utf-8")
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
    assert "BIENVENIDA" not in ctx.sales_campaign_script  # el 1er toque no lleva la oferta
    assert ctx.sales_campaign_examples == list(campaign.ejemplos_toques)
    assert {"22500", "45000"} <= set(ctx.allowed_numbers)


async def test_a_coupon_not_live_in_medusa_is_not_named(_isolate_vault_dir, monkeypatch, _env) -> None:
    from temporalio.testing import ActivityEnvironment

    context_mod, _ = _env
    monkeypatch.setattr(context_mod, "get_promotions_port", lambda: _Promotions(found=False))
    sid = "wa_10000000002"
    _seed(_isolate_vault_dir, sid, stamped=True, touch=2, intent=True)

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
    _seed(_isolate_vault_dir, sid, stamped=True, touch=2, intent=True)

    ctx = await ActivityEnvironment().run(context_mod.read_remarketing_context_activity, sid)

    assert ctx.sales_campaign_script and "BIENVENIDA" not in ctx.sales_campaign_script


async def test_a_customer_who_already_bought_is_not_offered_the_welcome_coupon(_isolate_vault_dir, monkeypatch, _env) -> None:
    from temporalio.testing import ActivityEnvironment

    context_mod, _ = _env
    monkeypatch.setattr(context_mod, "get_promotions_port", lambda: _Promotions(found=True))
    sid = "wa_10000000005"
    _seed(_isolate_vault_dir, sid, stamped=True, touch=2, intent=True, bought_before=True)

    ctx = await ActivityEnvironment().run(context_mod.read_remarketing_context_activity, sid)

    assert ctx.sales_campaign_script and "BIENVENIDA" not in ctx.sales_campaign_script


async def test_a_slow_medusa_does_not_hold_the_touch(_isolate_vault_dir, monkeypatch, _env) -> None:
    """La activity tiene 15 s y ya espera a Jev: el cupón tiene su tope."""
    import time

    from temporalio.testing import ActivityEnvironment

    context_mod, _ = _env
    monkeypatch.setattr(context_mod, "get_promotions_port", lambda: _Promotions(found=True, slow=True))
    sid = "wa_10000000006"
    _seed(_isolate_vault_dir, sid, stamped=True, touch=2, intent=True)

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


# ── la oferta con fecha para quien quiso comprar (operador, 2026-10-20) ─────


async def test_who_wanted_to_buy_gets_the_offer_with_its_deadline_on_the_offer_touch(_isolate_vault_dir, monkeypatch, _env) -> None:
    """«También en uno de los mensajes de remarketing para los que tuvieron
    intención y no compraron»: Jev leyó que eligió un producto."""
    from temporalio.testing import ActivityEnvironment

    context_mod, campaign = _env
    monkeypatch.setattr(context_mod, "get_promotions_port", lambda: _Promotions(found=True))
    sid = "wa_10000000008"
    _seed(_isolate_vault_dir, sid, stamped=True, touch=campaign.toque_oferta, intent=True)

    ctx = await ActivityEnvironment().run(context_mod.read_remarketing_context_activity, sid)

    assert "BIENVENIDA" in ctx.sales_campaign_script
    assert f"hasta el {LAST_DAY.day} de " in ctx.sales_campaign_script
    assert str(LAST_DAY.day) in ctx.allowed_numbers  # la guarda de números deja pasar la fecha


async def test_who_only_looked_gets_no_offer(_isolate_vault_dir, monkeypatch, _env) -> None:
    from temporalio.testing import ActivityEnvironment

    context_mod, campaign = _env
    monkeypatch.setattr(context_mod, "get_promotions_port", lambda: _Promotions(found=True))
    sid = "wa_10000000009"
    _seed(_isolate_vault_dir, sid, stamped=True, touch=campaign.toque_oferta, intent=False)

    ctx = await ActivityEnvironment().run(context_mod.read_remarketing_context_activity, sid)

    assert ctx.sales_campaign_script and "BIENVENIDA" not in ctx.sales_campaign_script



# ── revisión del 2026-10-20 ──────────────────────────────────────────────────


async def _context(_isolate_vault_dir, monkeypatch, _env, sid: str, **seed):
    from temporalio.testing import ActivityEnvironment

    context_mod, campaign = _env
    monkeypatch.setattr(context_mod, "get_promotions_port", lambda: _Promotions(found=True))
    _seed(_isolate_vault_dir, sid, stamped=True, touch=campaign.toque_oferta, **seed)
    return await ActivityEnvironment().run(context_mod.read_remarketing_context_activity, sid)


async def test_the_offer_touch_happens_once_per_episode(_isolate_vault_dir, monkeypatch, _env) -> None:
    """La escalera vuelve a contar cuando el cliente responde: el toque 2 del
    segundo ciclo no repite la oferta («uno de los mensajes»)."""
    ctx = await _context(_isolate_vault_dir, monkeypatch, _env, "wa_10000000010", intent=True, earlier_touches=1)

    assert ctx.sales_campaign_script and "BIENVENIDA" not in ctx.sales_campaign_script


async def test_an_offer_ventas_already_told_is_not_repeated(_isolate_vault_dir, monkeypatch, _env) -> None:
    told = {sc.OFFER_KEY: f"BIENVENIDA@{LAST_DAY.isoformat()}"}
    ctx = await _context(_isolate_vault_dir, monkeypatch, _env, "wa_10000000011", intent=True, episode_extra=told)

    assert ctx.sales_campaign_script and "BIENVENIDA" not in ctx.sales_campaign_script


async def test_with_a_coupon_already_applied_no_other_is_offered(_isolate_vault_dir, monkeypatch, _env) -> None:
    applied = {"applied_coupon": {"code": "AMOR2026", "promotion": {"value": 10}, "applied_at_ms": 1}}
    ctx = await _context(_isolate_vault_dir, monkeypatch, _env, "wa_10000000012", intent=True, episode_extra=applied)

    assert ctx.sales_campaign_script and "BIENVENIDA" not in ctx.sales_campaign_script


async def test_a_cut_line_in_the_decisions_log_does_not_break_the_touch(_isolate_vault_dir, monkeypatch, _env) -> None:
    """Revisión del 2026-10-20: una línea cortada dentro de una «á» lanzaba
    UnicodeDecodeError, la activity fallaba sus dos intentos y el workflow de
    remarketing de la sesión moría."""
    good = json.dumps({"at_ms": int(time.time() * 1000) - 3 * 3_600_000, "stage": "ingest", "capability": "compra",
                       "by": "jev", "value": [None, "text"],
                       "answers": [{"q": "compra.que_hace", "choice": "elige", "confidence": 0.9}]}).encode()
    cut = '{"capability": "compra", "text": "m'.encode() + "á".encode()[:1]

    ctx = await _context(_isolate_vault_dir, monkeypatch, _env, "wa_10000000013", decisions_bytes=good + b"\n" + cut)

    assert "BIENVENIDA" in ctx.sales_campaign_script  # la línea buena se lee; la cortada se salta


@pytest.mark.parametrize(("kind", "offered"), [("abstained", True), ("failed", True), ("template", False), ("free_form", False)])
async def test_only_the_touches_that_reached_the_customer_count(_isolate_vault_dir, monkeypatch, _env, kind, offered) -> None:
    """Revisión del 2026-10-20: un toque en el que el bot se abstuvo
    (NO_MESSAGE) o cuya plantilla falló no le llegó al cliente; contarlo
    corría la oferta de lugar o la saltaba."""
    sid = f"wa_1000000002{['abstained', 'failed', 'template', 'free_form'].index(kind)}"
    ctx = await _context(_isolate_vault_dir, monkeypatch, _env, sid, intent=True, earlier_kinds=(kind,))

    assert ("BIENVENIDA" in ctx.sales_campaign_script) is offered


async def test_a_coupon_for_everyone_reaches_who_bought_before(_isolate_vault_dir, monkeypatch, _env) -> None:
    """VELITAS5 (cupón propio de la campaña, 2026-10-20) puede no ser de primera
    compra: el toque con la oferta también le llega a quien ya compró."""
    from temporalio.testing import ActivityEnvironment

    context_mod, campaign = _env
    monkeypatch.setattr(context_mod, "get_promotions_port", lambda: _Promotions(found=True, first_purchase_only=False))
    sid = "wa_10000000030"
    _seed(_isolate_vault_dir, sid, stamped=True, touch=campaign.toque_oferta, intent=True, bought_before=True)

    ctx = await ActivityEnvironment().run(context_mod.read_remarketing_context_activity, sid)

    offer = next(x for x in ctx.sales_campaign_script.splitlines() if "BIENVENIDA" in x)
    assert "primera compra" not in offer

