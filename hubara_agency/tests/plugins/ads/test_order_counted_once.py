"""Una venta de Ads es UN pedido real: cuenta una vez y solo si existe.

Auditoría 2026-11-10 del panel de Ads:

  * El mismo `order_id` puede quedar en dos episodios de una conversación (el
    cliente vuelve y el bot re-registra el mismo borrador — el adapter devuelve
    el existente por huella —, o «Crear pedido» sobre un episodio que ya tenía
    pedido). Ads lo sumaba dos veces: $100.000 → $200.000 y 2 «ganados».
    El pedido es del PRIMER episodio que lo trajo; el siguiente no es otra venta.
  * «Ganado» contaba cualquier `order_id` del chat: también pedidos marcados
    «prueba» en Órdenes (excluidos de todo total) y pedidos que ya no existen en
    Medusa. Ninguno es una venta: el chat llegó al pedido → «cotizado», con el
    motivo en `state_reason`. Si Orders no responde, el chat manda (sin evidencia
    no se degrada una venta).
"""
from __future__ import annotations

import json
from pathlib import Path

from src.plugins.ads.aggregation import (
    list_ads_campaigns,
    list_attributed_conversations,
    list_daily_series,
)
from src.sdk.connectorkit import OrderFacts, OrderFactsSnapshot

_DAY_MS = 24 * 60 * 60 * 1000
_NOW_MS = 1_789_700_000_000
_T0 = _NOW_MS - 2 * _DAY_MS
ORDER = "order_31"


def _fact(order_id: str = ORDER, *, pay: str = "paid", total: int = 100_000) -> OrderFacts:
    return OrderFacts(
        order_id=order_id, display_id="#31", total_cop=total, currency_code="cop",
        pay_status=pay, stage="new", customer="Ana", is_draft=False,
    )


def _episode(episode_id: str, started_ms: int, *, order_id: str | None, tag: str) -> dict:
    return {
        "episode_id": episode_id,
        "started_at_ms": started_ms,
        "closed_at_ms": started_ms + 60_000,
        "closing_tag": tag,
        "order_id": order_id,
        "order_total_cop": 100_000 if order_id else None,
        "referral_snapshot": {"channel": "ad", "source_id": "AD_X", "headline": "Velas"},
    }


def _seed(vault: Path, phone: str, episodes: list[dict]) -> None:
    d = vault / f"wa_{phone}"
    d.mkdir(parents=True)
    (d / "metadata.json").write_text(
        json.dumps(
            {
                "origin": {"channel": "ad", "source_id": "AD_X", "first_seen_ms": _T0},
                "tag": "NO_ETIQUETADO",
                "episodes": episodes,
            }
        )
    )


def _same_order_in_two_episodes(vault: Path) -> OrderFactsSnapshot:
    _seed(
        vault,
        "573001112233",
        [
            _episode("ep_001", _T0, order_id=ORDER, tag="CONFIRMADO_PAGO_PENDIENTE"),
            _episode("ep_002", _T0 + _DAY_MS, order_id=ORDER, tag="COMPRA_EXITOSA"),
        ],
    )
    return OrderFactsSnapshot(facts={ORDER: _fact()})


def test_order_in_two_episodes_counts_once_in_campaign(tmp_path: Path) -> None:
    facts = _same_order_in_two_episodes(tmp_path)

    [campaign] = list_ads_campaigns(tmp_path, order_facts=facts)

    assert campaign.revenue == 100_000
    assert campaign.revenue_count == 1
    assert campaign.conversations["ganado"] == 1


def test_later_episode_with_the_same_order_is_not_another_sale(tmp_path: Path) -> None:
    """La fila del episodio que repite el pedido dice por qué no es venta y no
    lleva el pedido: el análisis con IA cuenta ventas por `order_status`."""
    facts = _same_order_in_two_episodes(tmp_path)

    rows = {
        c.episode_id: c
        for c in list_attributed_conversations(tmp_path, "AD_X", order_facts=facts)
    }

    first, later = rows["ep_001"], rows["ep_002"]
    assert (first.state, first.value, first.order_status) == ("ganado", 100_000, "paid")
    assert later.state == "cotizado"
    assert later.state_reason == "order_repeated"
    assert (later.value, later.order_status, later.order_value_cop) == (None, None, None)


def test_purchase_reported_to_meta_belongs_to_the_episode_with_the_sale(
    tmp_path: Path,
) -> None:
    """El `Purchase` de CAPI se identifica por pedido (`purchase_<order_id>`):
    con el pedido en dos episodios, el badge va al que tiene la venta."""
    facts = _same_order_in_two_episodes(tmp_path)
    meta_path = tmp_path / "wa_573001112233" / "metadata.json"
    metadata = json.loads(meta_path.read_text())
    metadata["capi_events_sent"] = [
        {"event_id": f"purchase_{ORDER}", "event_name": "Purchase", "status": "sent"}
    ]
    meta_path.write_text(json.dumps(metadata))

    rows = {
        c.episode_id: c
        for c in list_attributed_conversations(tmp_path, "AD_X", order_facts=facts)
    }

    assert rows["ep_001"].capi_event == "Purchase"
    assert rows["ep_002"].capi_event is None


def _one_sale(vault: Path) -> None:
    _seed(
        vault,
        "573001112233",
        [_episode("ep_001", _T0, order_id=ORDER, tag="CONFIRMADO_PAGO_PENDIENTE")],
    )


def _only_row(vault: Path, facts: OrderFactsSnapshot):
    [row] = list_attributed_conversations(vault, "AD_X", order_facts=facts)
    [campaign] = list_ads_campaigns(vault, order_facts=facts)
    return row, campaign


def test_test_order_is_not_a_sale(tmp_path: Path) -> None:
    _one_sale(tmp_path)
    test_order = OrderFacts(
        order_id=ORDER, display_id="#31", total_cop=100_000, currency_code="cop",
        pay_status="paid", stage="new", customer="Ana", is_draft=False, is_test=True,
    )

    row, campaign = _only_row(tmp_path, OrderFactsSnapshot(facts={ORDER: test_order}))

    assert (row.state, row.state_reason) == ("cotizado", "order_test")
    assert campaign.conversations["ganado"] == 0
    assert campaign.revenue is None


def test_order_that_no_longer_exists_is_not_a_sale(tmp_path: Path) -> None:
    """Snapshot sin el id y sin marcarlo `unresolved` = Medusa confirmó que no existe."""
    _one_sale(tmp_path)

    row, campaign = _only_row(tmp_path, OrderFactsSnapshot())

    assert (row.state, row.state_reason) == ("cotizado", "order_missing")
    assert campaign.conversations["ganado"] == 0


def test_order_orders_could_not_answer_for_keeps_the_chat_state(tmp_path: Path) -> None:
    _one_sale(tmp_path)

    row, campaign = _only_row(
        tmp_path, OrderFactsSnapshot(unresolved=frozenset({ORDER}), stale=True)
    )

    assert (row.state, row.state_reason) == ("ganado", None)
    assert campaign.conversations["ganado"] == 1


def test_daily_series_counts_the_order_once(tmp_path: Path) -> None:
    facts = _same_order_in_two_episodes(tmp_path)

    series = list_daily_series(tmp_path, "AD_X", days=7, now_ms=_NOW_MS, order_facts=facts)

    assert sum(p.ganado for p in series) == 1
    assert sum(p.cotizado for p in series) == 1
