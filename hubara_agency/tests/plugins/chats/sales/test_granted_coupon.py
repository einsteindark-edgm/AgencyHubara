"""El cupón de agradecimiento por calificar (personal) en el bot de ventas.

Reseñas tras la entrega (2026-10-13): quien califica su pedido recibe GRACIAS5
(o GRACIAS10 con foto) para la próxima compra. Es un código único con la
condición «solo quien lo recibió»: otra persona que lo escriba no lo obtiene,
y el bot no lo anuncia a quien no lo tiene.

Contrato:
  * `apply_coupon` lo aplica a quien tiene la concesión vigente y lo rechaza,
    con la razón, a quien no. Si las concesiones no se pueden leer, no lo
    aplica (falla cerrada, L-32).
  * `list_promotions` solo lo muestra a quien lo tiene.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.promotions.grants import CouponGrant, FakeCouponGrantsStore
from src.platform.promotions.port import FakePromotionsPort
from src.platform.state import FilesystemMetadataStore
from src.plugins.chats.agent.sales.tools.coupons import ApplyCouponTool, ListPromotionsTool
from src.plugins.chats.agent.sales.use_cases.coupon_application import resolve_coupon_application
from tests.plugins.chats.sales.test_coupon_tools import KEY, FakeCatalog, _md, _promo, _seed

NOW = 1_760_000_000_000
_THANKS = _promo(id="promo_gracias5", code="GRACIAS5", value=5, description="Gracias por calificar",
                 granted_only=True)
_AMOR = _promo(id="promo_amor", code="AMOR2026", value=10, product_ids=("prod_cubo-love",),
               description="Amor y amistad 2026")


def _grants(session: str = KEY) -> FakeCouponGrantsStore:
    store = FakeCouponGrantsStore()
    store.grant(CouponGrant(code="GRACIAS5", session_id=session, percent=5, granted_at_ms=NOW - 1000,
                            expires_at_ms=NOW + 86_400_000, source="review", reference="order_01A"))
    return store


class _BrokenGrants(FakeCouponGrantsStore):
    def active(self, session_id, code, now_ms):
        raise OSError("disco")


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(session_key=KEY, channel="whatsapp", chat_id=KEY)


def _apply(vault: Path, grants) -> ApplyCouponTool:
    return ApplyCouponTool(
        workspace=str(vault), metadata_store=FilesystemMetadataStore(vault),
        promotions=FakePromotionsPort([_THANKS, _AMOR]), catalog=FakeCatalog(),
        coupon_grants=grants, now_ms=lambda: NOW,
    )


def _list(vault: Path, grants) -> ListPromotionsTool:
    return ListPromotionsTool(
        workspace=str(vault), promotions=FakePromotionsPort([_THANKS, _AMOR]), catalog=FakeCatalog(),
        metadata_store=FilesystemMetadataStore(vault), coupon_grants=grants, now_ms=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_the_person_who_rated_gets_the_thanks_coupon(ctx, _isolate_vault_dir):
    path = _seed(_isolate_vault_dir)

    env = json.loads(await _apply(_isolate_vault_dir, _grants()).execute_with_context(ctx, code="gracias5"))

    assert env["applied"] is True
    assert _md(path)["episodes"][-1]["applied_coupon"]["code"] == "GRACIAS5"


@pytest.mark.asyncio
async def test_someone_else_typing_the_code_does_not_get_it(ctx, _isolate_vault_dir):
    path = _seed(_isolate_vault_dir)

    env = json.loads(
        await _apply(_isolate_vault_dir, _grants("wa_573009876543")).execute_with_context(ctx, code="GRACIAS5")
    )

    assert env["applied"] is False
    assert env["reason"] == "not_granted"
    assert "applied_coupon" not in _md(path)["episodes"][-1]


@pytest.mark.asyncio
async def test_unreadable_grants_fail_closed(ctx, _isolate_vault_dir):
    path = _seed(_isolate_vault_dir)

    env = json.loads(await _apply(_isolate_vault_dir, _BrokenGrants()).execute_with_context(ctx, code="GRACIAS5"))

    assert env["applied"] is False
    assert env["reason"] == "grant_unknown"
    assert "applied_coupon" not in _md(path)["episodes"][-1]


@pytest.mark.asyncio
async def test_a_gate_without_grants_wiring_never_applies_a_personal_coupon():
    application = await resolve_coupon_application(
        "GRACIAS5", promotions=FakePromotionsPort([_THANKS]), quotas=None, sales=None,
        catalog=FakeCatalog(), now_ms=NOW,
    )
    assert application.reason == "grant_unknown"


@pytest.mark.asyncio
async def test_list_promotions_shows_the_personal_coupon_only_to_its_owner(ctx, _isolate_vault_dir):
    _seed(_isolate_vault_dir)

    mine = json.loads(await _list(_isolate_vault_dir, _grants()).execute_with_context(ctx))
    others = json.loads(await _list(_isolate_vault_dir, _grants("wa_573009876543")).execute_with_context(ctx))

    mine_codes = {p["code"]: p for p in mine["promotions"]}
    assert mine_codes["GRACIAS5"]["applies_to_customer"] is True
    assert mine_codes["GRACIAS5"]["personal"] is True
    assert "GRACIAS5" not in {p["code"] for p in others["promotions"]}
    assert "AMOR2026" in {p["code"] for p in others["promotions"]}


@pytest.mark.asyncio
async def test_list_promotions_never_promises_a_personal_coupon_past_its_promotion(ctx, _isolate_vault_dir):
    """Premortem C8: si la promoción de Medusa vence antes que la concesión,
    el bot dice la fecha de la promoción (después ya no se puede aplicar)."""
    _seed(_isolate_vault_dir)
    ends = NOW + 3_600_000
    short = _promo(id="promo_gracias5", code="GRACIAS5", value=5, granted_only=True, ends_at_ms=ends)
    tool = ListPromotionsTool(
        workspace=str(_isolate_vault_dir), promotions=FakePromotionsPort([short]), catalog=FakeCatalog(),
        metadata_store=FilesystemMetadataStore(_isolate_vault_dir), coupon_grants=_grants(), now_ms=lambda: NOW,
    )

    listed = {p["code"]: p for p in json.loads(await tool.execute_with_context(ctx))["promotions"]}

    assert listed["GRACIAS5"]["ends_at_ms"] == ends


# ---------- el uso: registrar el pedido gasta la concesión ----------


@pytest.mark.asyncio
async def test_registering_the_order_spends_the_personal_coupon(ctx, _isolate_vault_dir):
    from src.plugins.chats.agent.sales.tools.order_registration import RegisterOrderTool
    from tests.plugins.chats.sales.test_coupon_tools import _SHIPPING, FakePort

    thanks = _promo(id="promo_gracias5", code="GRACIAS5", value=5, granted_only=True)
    _seed(_isolate_vault_dir, {
        "episodes": [{
            "episode_id": "ep_001", "started_at_ms": 1, "closed_at_ms": None,
            "applied_coupon": {"code": "GRACIAS5", "applied_at_ms": NOW, "promotion": {**thanks.__dict__}},
        }]
    })
    # El registro usa el reloj real: la concesión vence en el futuro lejano.
    grants = FakeCouponGrantsStore()
    grants.grant(CouponGrant(code="GRACIAS5", session_id=KEY, percent=5, granted_at_ms=NOW,
                             expires_at_ms=4_000_000_000_000, source="review", reference="order_01A"))
    tool = RegisterOrderTool(
        workspace=str(_isolate_vault_dir), vault_dir=_isolate_vault_dir, port=FakePort(),
        catalog=FakeCatalog(), coupon_grants=grants,
    )
    env = json.loads(await tool.execute_with_context(
        ctx,
        items=[{"handle": "vela-buda", "quantity": 1, "unit_price_cop": 40000}],
        shipping=_SHIPPING, payment_method="transfer",
        subtotal_cop=40000, shipping_cop=7900, total_cop=45900,
    ))

    assert env["registered"] is True, env
    [grant] = grants.list_for(KEY)
    assert grant.used_order_id == env["order_id"]


# ---------- premortem C2: un solo uso también al reintentar ----------


_FUTURE = 4_000_000_000_000


def _seed_thanks(vault: Path) -> None:
    thanks = _promo(id="promo_gracias5", code="GRACIAS5", value=5, granted_only=True)
    _seed(vault, {
        "episodes": [{
            "episode_id": "ep_001", "started_at_ms": 1, "closed_at_ms": None,
            "applied_coupon": {"code": "GRACIAS5", "applied_at_ms": NOW, "promotion": {**thanks.__dict__}},
        }]
    })


def _live_grants() -> FakeCouponGrantsStore:
    grants = FakeCouponGrantsStore()
    grants.grant(CouponGrant(code="GRACIAS5", session_id=KEY, percent=5, granted_at_ms=NOW,
                             expires_at_ms=_FUTURE, source="review", reference="order_01A"))
    return grants


async def _register_thanks(ctx, vault: Path, port, grants) -> dict:
    from src.plugins.chats.agent.sales.tools.order_registration import RegisterOrderTool
    from tests.plugins.chats.sales.test_coupon_tools import _SHIPPING

    tool = RegisterOrderTool(workspace=str(vault), vault_dir=vault, port=port, catalog=FakeCatalog(),
                             coupon_grants=grants)
    return json.loads(await tool.execute_with_context(
        ctx,
        items=[{"handle": "vela-buda", "quantity": 1, "unit_price_cop": 40000}],
        shipping=_SHIPPING, payment_method="transfer",
        subtotal_cop=40000, shipping_cop=7900, total_cop=45900,
    ))


@pytest.mark.asyncio
async def test_a_registration_retried_later_spends_the_personal_coupon(ctx, _isolate_vault_dir):
    """Medusa caído al registrar: el reintento de reconciliación lleva el
    cupón y, si sale, gasta la concesión (antes quedaba reutilizable)."""
    from src.platform.orders.reconciliation import reconcile_one
    from tests.plugins.chats.sales.test_coupon_tools import CapturingPort

    _seed_thanks(_isolate_vault_dir)
    grants = _live_grants()
    env = await _register_thanks(ctx, _isolate_vault_dir, CapturingPort(medusa_down=True), grants)
    assert env["registered"] is False
    assert grants.list_for(KEY)[0].used_order_id is None

    outcome = await reconcile_one(
        vault_dir=_isolate_vault_dir, session_key=KEY, audit_id=env["audit_id"], port=CapturingPort(),
        coupon_grants=grants,
    )

    assert outcome.is_resolved
    [grant] = grants.list_for(KEY)
    assert grant.used_order_id == "draft_c_044"


@pytest.mark.asyncio
async def test_a_retry_whose_personal_coupon_was_used_meanwhile_is_not_registered_with_it(
    ctx, _isolate_vault_dir
):
    from src.platform.orders.reconciliation import OUTCOME_ABANDONED, reconcile_one
    from tests.plugins.chats.sales.test_coupon_tools import CapturingPort

    _seed_thanks(_isolate_vault_dir)
    grants = _live_grants()
    env = await _register_thanks(ctx, _isolate_vault_dir, CapturingPort(medusa_down=True), grants)
    grants.mark_used(KEY, "GRACIAS5", "draft_otro_pedido", NOW + 1)
    retry = CapturingPort()

    outcome = await reconcile_one(
        vault_dir=_isolate_vault_dir, session_key=KEY, audit_id=env["audit_id"], port=retry,
        coupon_grants=grants,
    )

    assert outcome.outcome == OUTCOME_ABANDONED
    assert "coupon_not_granted" in (outcome.error_detail or "")
    assert retry.calls == []


@pytest.mark.asyncio
async def test_register_order_does_not_apply_a_personal_coupon_already_used(ctx, _isolate_vault_dir):
    """La concesión se usó (u otra conversación la gastó) después de aplicar el
    cupón: no se registra con un descuento que ya no tiene, se le dice al bot
    por qué y el cupón sale del pedido."""
    from src.plugins.chats.agent.sales.use_cases.coupon_application import COUPON_REASON_TEXT
    from tests.plugins.chats.sales.test_coupon_tools import CapturingPort

    _seed_thanks(_isolate_vault_dir)
    grants = _live_grants()
    grants.mark_used(KEY, "GRACIAS5", "draft_otro_pedido", NOW + 1)
    port = CapturingPort()

    env = await _register_thanks(ctx, _isolate_vault_dir, port, grants)

    assert env["registered"] is False and env["error"] == "coupon_not_granted"
    assert COUPON_REASON_TEXT["not_granted"] in env["summary"]
    assert port.calls == []
    episode = _md(_isolate_vault_dir / KEY / "metadata.json")["episodes"][-1]
    assert "applied_coupon" not in episode and "GRACIAS5" in episode["declined_coupons"]


@pytest.mark.asyncio
async def test_a_spend_that_finds_no_grant_is_a_warning(ctx, _isolate_vault_dir, caplog):
    from src.platform.orders.reconciliation import reconcile_one
    from tests.plugins.chats.sales.test_coupon_tools import CapturingPort

    class _Vanishing(FakeCouponGrantsStore):
        def mark_used(self, *a, **k):
            return None

    _seed_thanks(_isolate_vault_dir)
    live = _live_grants()
    env = await _register_thanks(ctx, _isolate_vault_dir, CapturingPort(medusa_down=True), live)
    vanishing = _Vanishing()
    vanishing.grant(live.list_for(KEY)[0])

    with caplog.at_level("WARNING"):
        await reconcile_one(vault_dir=_isolate_vault_dir, session_key=KEY, audit_id=env["audit_id"],
                            port=CapturingPort(), coupon_grants=vanishing)

    assert any("GRACIAS5" in r.getMessage() and "personal" in r.getMessage() for r in caplog.records)
