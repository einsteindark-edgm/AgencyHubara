"""El turno de traspaso de remarketing a Ventas lleva el guion de la campaña
y, si el cliente aplazó, la oferta del cupón (caso del 2026-10-21, velitas).

El cliente contestó al toque 2 «No gracias voy a esperar» (Jev: aplaza) y, 6 s
después, «Feliz día y bendiciones». El mensaje entró con la ruta en
remarketing, que se la pasó a Ventas; el turno de traspaso solo llevaba la
hora, el borrador y el resumen: ni el guion ni la oferta. Ventas se despidió
sin el 5 % y el operador lo mandó a mano.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from src.plugins.chats.agent.sales.activities.bootstrap_session import read_order_draft_note_activity
from src.plugins.chats.shared import sales_campaigns as sc
from src.plugins.chats.shared.purchase_signals import apply_inbound_purchase_signal
from src.sdk.catalogkit import CatalogPriceDTO, CatalogProductDTO, CatalogVariantDTO

SESSION = "wa_10000000021"
FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "sales_campaigns"
_BOGOTA = ZoneInfo("America/Bogota")
LAST_DAY = datetime.now(_BOGOTA).date() + timedelta(days=10)


def _product(handle: str, title: str, price: int) -> CatalogProductDTO:
    return CatalogProductDTO(
        id=f"prod_{handle}", handle=handle, title=title, status="published",
        variants=[CatalogVariantDTO(id=f"var_{handle}", title="Multicolor",
                                    prices=[CatalogPriceDTO(amount=str(price), currency_code="cop")])],
    )


class _Catalog:
    async def search(self, q: str, *, limit: int = 10, category: str | None = None):
        return SimpleNamespace(results=[
            _product("velas-de-deseos-onduladas", "Caja de Velas de deseos onduladas", 22_500),
            _product("velas-de-deseos-cuadradas", "Caja de Velas de deseos cuadradas", 25_000),
        ])


def _live_coupon():
    from src.sdk.connectorkit import PromotionDTO

    ends = datetime.combine(LAST_DAY + timedelta(days=1), datetime.min.time(), tzinfo=_BOGOTA)
    return PromotionDTO(
        id="promo_1", code="BIENVENIDA", discount_type="percentage", value=5, currency_code=None,
        target_type="items", allocation="across", max_quantity=None, product_ids=(), variant_ids=(),
        collection_ids=(), min_subtotal_cop=None, is_automatic=False, status="active", starts_at_ms=None,
        ends_at_ms=int(ends.timestamp() * 1000), budget_type=None, budget_limit=None, budget_used=None,
        description=None, first_purchase_only=True,
    )


class _Promotions:
    def __init__(self, promotion=None, *, fail: bool = False) -> None:
        self._promotion, self._fail = promotion, fail

    async def get_by_code(self, code: str):
        if self._fail:
            raise OSError("medusa caída")
        return self._promotion if self._promotion is not None and code == self._promotion.code else None


@pytest.fixture(autouse=True)
def vault(tmp_path, monkeypatch):
    import src.sdk.connectorkit as connectorkit

    monkeypatch.setattr("src.plugins.chats.agent.sales.activities.bootstrap_session.WORKSPACE_VAULT_DIR", tmp_path)
    (campaign,) = sc.load_campaigns(FIXTURES)
    always = sc.Campaign(**{**vars(campaign), "desde": date(2000, 1, 1), "hasta": date(2999, 1, 1)})
    monkeypatch.setattr(sc, "active_campaigns", lambda: (always,))
    monkeypatch.setattr(connectorkit, "get_promotions_port", lambda: _Promotions(_live_coupon()))
    monkeypatch.setattr(connectorkit, "get_catalog_client", lambda: _Catalog())
    return tmp_path


def _write(
    vault: Path, *, deferred: bool = True, replied_after: bool = False, draft: dict | None = None,
    farewell: bool = True,
) -> None:
    """La conversación al llegar el traspaso: el toque de remarketing, el
    aplazamiento y la despedida (sin señal), con la ruta ya en Ventas."""
    now = int(time.time() * 1000)
    episode: dict = {"episode_id": "ep_001", "started_at_ms": now - 3_600_000, "sales_campaign": "velitas-7-dic"}
    if draft:
        episode["order_draft"] = {"slots": draft}
    metadata: dict = {"active_route": "ventas", "episodes": [episode],
                      "last_outbound": {"sent_at_ms": now - 400_000, "kind": "text"}}
    if deferred:
        apply_inbound_purchase_signal(metadata, "deferral", "text", now_ms=now - 13_000, message_id="wamid.A",
                                      text="No gracias voy a esperar")
    if farewell:
        apply_inbound_purchase_signal(metadata, None, "text", now_ms=now - 7_000, message_id="wamid.B",
                                      text="Feliz día y bendiciones")
    metadata["last_inbound_message_id"] = "wamid.B" if farewell else "wamid.A"
    if replied_after:
        metadata["last_outbound"]["sent_at_ms"] = now - 1_000
    (vault / SESSION).mkdir(parents=True, exist_ok=True)
    (vault / SESSION / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")


def _episode(vault: Path) -> dict:
    return json.loads((vault / SESSION / "metadata.json").read_text(encoding="utf-8"))["episodes"][-1]


@pytest.mark.asyncio
async def test_the_handoff_turn_brings_the_script_and_the_offer_once(vault: Path) -> None:
    _write(vault)

    first = await read_order_draft_note_activity(SESSION)
    again = await read_order_draft_note_activity(SESSION)

    assert first is not None and "[GUION DE LA CAMPAÑA" in first
    assert "AHORA: el cliente acaba de aplazar" in first and f"hasta el {LAST_DAY.day} de " in first
    assert _episode(vault).get(sc.OFFER_KEY)
    # Una sola vez por episodio: el reintento o el turno siguiente ya no.
    assert again is not None and "[GUION DE LA CAMPAÑA" in again and "acaba de aplazar" not in again


@pytest.mark.asyncio
async def test_a_lone_deferral_to_remarketing_also_brings_the_offer(vault: Path) -> None:
    """Mismo día, otra conversación de velitas: «Yo te aviso» contestando al
    toque 1, sin nada después. Remarketing pasó el resumen «[EL CLIENTE
    APLAZÓ]…» y Ventas se despidió sin la oferta."""
    _write(vault, farewell=False)

    note = await read_order_draft_note_activity(SESSION)

    assert note is not None and "AHORA: el cliente acaba de aplazar" in note
    assert _episode(vault).get(sc.OFFER_KEY)


@pytest.mark.asyncio
async def test_without_a_deferral_the_handoff_brings_the_script_without_the_offer(vault: Path) -> None:
    _write(vault, deferred=False)

    note = await read_order_draft_note_activity(SESSION)

    assert note is not None and "[GUION DE LA CAMPAÑA" in note and "acaba de aplazar" not in note
    assert not _episode(vault).get(sc.OFFER_KEY)


@pytest.mark.asyncio
async def test_after_our_reply_the_old_deferral_does_not_bring_the_offer(vault: Path) -> None:
    _write(vault, replied_after=True)

    note = await read_order_draft_note_activity(SESSION)

    assert note is not None and "acaba de aplazar" not in note


@pytest.mark.asyncio
async def test_the_order_draft_still_goes_first(vault: Path) -> None:
    _write(vault, draft={"producto": "Caja de Velas de deseos onduladas", "cantidad": "2"})

    note = await read_order_draft_note_activity(SESSION)

    assert note is not None and note.startswith("[DATOS DEL PEDIDO YA CONFIRMADOS")
    assert "[GUION DE LA CAMPAÑA" in note


@pytest.mark.asyncio
async def test_without_medusa_the_handoff_still_brings_the_script(vault: Path, monkeypatch) -> None:
    import src.sdk.connectorkit as connectorkit

    monkeypatch.setattr(connectorkit, "get_promotions_port", lambda: _Promotions(fail=True))
    _write(vault)

    note = await read_order_draft_note_activity(SESSION)

    assert note is not None and "[GUION DE LA CAMPAÑA" in note and "BIENVENIDA" not in note
    assert not _episode(vault).get(sc.OFFER_KEY)


@pytest.mark.asyncio
async def test_a_broken_campaign_never_breaks_the_handoff(vault: Path, monkeypatch) -> None:
    def _boom(*args, **kwargs):
        raise TypeError("campaña rota")

    monkeypatch.setattr(sc, "campaign_for", _boom)
    _write(vault, draft={"producto": "Caja de Velas de deseos onduladas", "cantidad": "2"})

    note = await read_order_draft_note_activity(SESSION)

    assert note is not None and note.startswith("[DATOS DEL PEDIDO YA CONFIRMADOS")
    assert "[GUION DE LA CAMPAÑA" not in note
