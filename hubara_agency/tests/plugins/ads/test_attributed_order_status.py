"""Cada conversación atribuida trae su anuncio (`source_id`) y el estado REAL de su pedido.

Caso Halloween (2026-09-25): el análisis con IA contaba como venta todo chat «ganado»,
incluido un pedido cancelado, y no sabía de qué anuncio venía cada chat. El drill-down
por anuncio necesita las dos cosas. El estado se lee de `OrderFacts` (gotcha 13); si
Orders no responde por ese pedido, de lo último que se le reportó a Meta (CAPI):

  paid        — pagado, no cancelado, no de prueba (cuenta en el retorno)
  pending     — pedido vivo sin pago confirmado (o solo LeadSubmitted a Meta)
  cancelled   — cancelado en Orders o `OrderCanceled` enviado a Meta
  test        — marcado de prueba en Orders
  unverified  — Orders no respondió y Meta no sabe nada del pedido
  None        — el chat no registró pedido
"""
from __future__ import annotations

import json
from pathlib import Path

from src.plugins.ads.aggregation import list_attributed_conversations
from src.sdk.connectorkit import OrderFacts, OrderFactsSnapshot

_STARTED_MS = 1_789_600_000_000


def _fact(oid: str, *, pay: str = "paid", stage: str = "delivered", is_test: bool = False) -> OrderFacts:
    return OrderFacts(order_id=oid, display_id=f"#{oid}", total_cop=52900, currency_code="cop",
                      pay_status=pay, stage=stage, customer="Ana", is_draft=False, is_test=is_test)


def _capi(event_name: str, event_id: str) -> dict:
    """Los ids reales: `purchase_<order>`, `ordercanceled_<order>`, `lead_<session>_<episode>`."""
    return {"event_id": event_id, "event_name": event_name, "status": "sent", "http_status": 200}


def _session(vault: Path, name: str, *, order_id: str | None, capi: list[dict] | None = None,
             source_id: str = "AD_1") -> None:
    d = vault / name
    d.mkdir(parents=True, exist_ok=True)
    ep = {"episode_id": "ep_001", "started_at_ms": _STARTED_MS, "closed_at_ms": _STARTED_MS + 1,
          "closing_tag": "COMPRA_EXITOSA" if order_id else "SIN_RESPUESTA",
          "referral_snapshot": {"channel": "ad", "source_id": source_id, "headline": "Velas"}}
    if order_id:
        ep["order_id"] = order_id
        ep["order_total_cop"] = 52900
    md = {"origin": {"channel": "ad", "first_seen_ms": _STARTED_MS, "headline": "Velas", "source_id": source_id},
          "capi_events_sent": capi or [], "episodes": [ep]}
    (d / "metadata.json").write_text(json.dumps(md), encoding="utf-8")


def _by_session(vault: Path, facts: OrderFactsSnapshot) -> dict[str, object]:
    convs = list_attributed_conversations(
        vault, "AD_1", source_ids=frozenset({"AD_1", "AD_2"}), order_facts=facts)
    return {c.id.split("__")[0]: c for c in convs}


def test_estado_del_pedido_desde_orders(tmp_path: Path) -> None:
    _session(tmp_path, "wa_1", order_id="o_paid")
    _session(tmp_path, "wa_2", order_id="o_pending")
    _session(tmp_path, "wa_3", order_id="o_cancel")
    _session(tmp_path, "wa_4", order_id="o_test")
    _session(tmp_path, "wa_5", order_id=None)
    facts = OrderFactsSnapshot(facts={
        "o_paid": _fact("o_paid"),
        "o_pending": _fact("o_pending", pay="pending", stage="new"),
        "o_cancel": _fact("o_cancel", stage="cancelled"),
        "o_test": _fact("o_test", is_test=True),
    })
    got = {k: c.order_status for k, c in _by_session(tmp_path, facts).items()}
    assert got == {"wa_1": "paid", "wa_2": "pending", "wa_3": "cancelled", "wa_4": "test", "wa_5": None}


def test_si_orders_no_responde_manda_lo_reportado_a_meta(tmp_path: Path) -> None:
    # Halloween: la caché de Orders fría → todos `unresolved`. Purchase = pago confirmado por
    # el operador; OrderCanceled = cancelado; LeadSubmitted = pedido sin pago; nada = sin verificar.
    _session(tmp_path, "wa_1", order_id="o1", capi=[_capi("Purchase", "purchase_o1")])
    _session(tmp_path, "wa_2", order_id="o2",
             capi=[_capi("Purchase", "purchase_o2"), _capi("OrderCanceled", "ordercanceled_o2")])
    _session(tmp_path, "wa_3", order_id="o3", capi=[_capi("LeadSubmitted", "lead_wa_3_ep_001")])
    _session(tmp_path, "wa_4", order_id="o4")
    facts = OrderFactsSnapshot(unresolved=frozenset({"o1", "o2", "o3", "o4"}), stale=True)
    got = {k: c.order_status for k, c in _by_session(tmp_path, facts).items()}
    assert got == {"wa_1": "paid", "wa_2": "cancelled", "wa_3": "pending", "wa_4": "unverified"}


def test_cada_conversacion_trae_su_anuncio(tmp_path: Path) -> None:
    _session(tmp_path, "wa_1", order_id=None, source_id="AD_1")
    _session(tmp_path, "wa_2", order_id=None, source_id="AD_2")
    got = {k: c.source_id for k, c in _by_session(tmp_path, OrderFactsSnapshot()).items()}
    assert got == {"wa_1": "AD_1", "wa_2": "AD_2"}


def test_cada_conversacion_trae_el_monto_de_su_pedido(tmp_path: Path) -> None:
    # `value` es solo ingreso CONFIRMADO; el análisis también necesita cuánto suman los
    # pendientes y los cancelados (para decir "1 pendiente de $45.000"). Orders manda; si no
    # responde, la copia del chat.
    _session(tmp_path, "wa_1", order_id="o_pending")
    _session(tmp_path, "wa_2", order_id="o_unresolved")
    _session(tmp_path, "wa_3", order_id=None)
    facts = OrderFactsSnapshot(
        facts={"o_pending": OrderFacts(order_id="o_pending", display_id="#1", total_cop=45000,
                                       currency_code="cop", pay_status="pending", stage="new",
                                       customer="Ana", is_draft=False)},
        unresolved=frozenset({"o_unresolved"}))
    got = {k: (c.value, c.order_value_cop) for k, c in _by_session(tmp_path, facts).items()}
    assert got == {"wa_1": (None, 45000), "wa_2": (52900, 52900), "wa_3": (None, None)}
