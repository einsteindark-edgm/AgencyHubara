"""La sección de comprobantes del informe para el Estratega (tarjeta 33 del ciclo).

Colossus anota en sombra qué diría de cada comprobante de pago. El informe lo
cruza con lo que el equipo hizo con ese pedido (OrderFacts: pagado o cancelado)
y separa el caso grave: dijo «coincide» y el equipo no aceptó el pago. Solo
conteos. Datos sintéticos.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from src.plugins.chats.agent.sales_eval import strategy
from src.plugins.chats.shared import turn_traces
from src.plugins.chats.shared.receipt_checks import append_check
from src.sdk.connectorkit import InMemoryOrderFacts, OrderFacts

NOW = datetime(2026, 10, 10, 15, 0, tzinfo=UTC)
RECENT_MS = int(datetime(2026, 10, 8, 14, 0, tzinfo=UTC).timestamp() * 1000)
OLDER_MS = int(datetime(2026, 9, 20, 14, 0, tzinfo=UTC).timestamp() * 1000)
MINUTE_MS = 60_000


def _conversation(vault: Path, n: int, *, order_id: str | None, started_ms: int = RECENT_MS, workflow: str = "v2") -> str:
    """Una conversación de un episodio con su pedido; devuelve la sesión."""
    sid = f"wa_10000000{n:04d}"
    start = started_ms + MINUTE_MS
    turn_traces.append_trace(vault, sid, {
        "episode_id": "ep_001", "turn": 1, "workflow": workflow, "trigger": "customer",
        "turn_started_ms": start, "recorded_at_ms": start + 5_000, "stage_out": "cierre",
    })
    path = vault / sid / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"episodes": [
        {"episode_id": "ep_001", "started_at_ms": started_ms, "closed_at_ms": start + MINUTE_MS, "order_id": order_id},
    ]}), encoding="utf-8")
    return sid


def _noted(vault: Path, sid: str, verdict: str, reason: str | None = None, *, source: str = "image",
           order_id: str | None = "x", minutes: int = 10) -> None:
    append_check(vault, sid, {
        "at_ms": RECENT_MS + minutes * MINUTE_MS, "episode_id": "ep_001", "order_id": order_id,
        "verdict": verdict, "reason": reason, "source": source,
    })


def _order(order_id: str, *, pay_status: str = "pending", stage: str = "new", is_test: bool = False) -> OrderFacts:
    return OrderFacts(order_id=order_id, display_id="1", total_cop=1, currency_code="cop", pay_status=pay_status,
                      stage=stage, customer="Cliente sintético", is_draft=False, is_test=is_test)


async def _report(vault: Path, *orders: OrderFacts) -> dict:
    return await strategy.build_report(vault, days=7, base_days=28, now=NOW, order_facts=InMemoryOrderFacts(list(orders)))


async def test_without_any_noted_receipt_the_section_says_what_is_missing(tmp_path: Path) -> None:
    _conversation(tmp_path, 1, order_id=None)

    section = (await _report(tmp_path))["comprobantes"]

    assert section["disponible"] is False
    assert section["motivo"] and section["faltante"]


async def test_each_verdict_is_crossed_with_what_the_team_did_with_the_order(tmp_path: Path) -> None:
    _noted(tmp_path, _conversation(tmp_path, 1, order_id="ord_a"), "coincide")
    _noted(tmp_path, _conversation(tmp_path, 2, order_id="ord_b"), "coincide")
    _noted(tmp_path, _conversation(tmp_path, 3, order_id="ord_c"), "no_coincide", "monto")
    _noted(tmp_path, _conversation(tmp_path, 4, order_id="ord_d"), "no_coincide", "fecha_vieja")
    _noted(tmp_path, _conversation(tmp_path, 5, order_id="ord_e"), "coincide")
    _noted(tmp_path, _conversation(tmp_path, 6, order_id="ord_f"), "ilegible", source="pdf")
    _noted(tmp_path, _conversation(tmp_path, 7, order_id=None), "sin_pedido", order_id=None)

    report = await _report(
        tmp_path,
        _order("ord_a", pay_status="paid"), _order("ord_b", stage="cancelled"),
        _order("ord_c", stage="cancelled"), _order("ord_d", pay_status="paid"),
        _order("ord_e"), _order("ord_f", pay_status="paid"),
    )

    section = report["comprobantes"]
    assert section["disponible"] is True
    now = section["actual"]["colossus"]
    assert now["comprobantes"] == 7
    assert now["por_veredicto"] == {"coincide": 3, "no_coincide": 2, "ilegible": 1, "sin_pedido": 1}
    assert now["por_motivo"] == {"fecha_vieja": 1, "monto": 1}
    assert now["por_origen"] == {"image": 6, "pdf": 1}
    cross = now["contra_el_equipo"]
    assert cross == {
        "pedidos_comparados": 4,
        "coincidencias": 2,
        "dijo_coincide_y_el_equipo_no_acepto": 1,
        "dijo_no_coincide_y_el_equipo_acepto": 1,
        "sin_resolver": 1,
    }


async def test_the_last_verdict_of_an_order_is_the_one_compared(tmp_path: Path) -> None:
    """El cliente mandó primero un comprobante que no cuadraba y luego el bueno."""
    sid = _conversation(tmp_path, 1, order_id="ord_a")
    _noted(tmp_path, sid, "no_coincide", "monto", minutes=10)
    _noted(tmp_path, sid, "coincide", minutes=20)

    now = (await _report(tmp_path, _order("ord_a", pay_status="paid")))["comprobantes"]["actual"]["colossus"]

    assert now["comprobantes"] == 2
    cross = now["contra_el_equipo"]
    assert (cross["pedidos_comparados"], cross["coincidencias"], cross["dijo_no_coincide_y_el_equipo_acepto"]) == (1, 1, 0)


async def test_the_agreement_rate_waits_for_forty_receipts(tmp_path: Path) -> None:
    for n in range(1, 4):
        _noted(tmp_path, _conversation(tmp_path, n, order_id=f"ord_{n}"), "coincide")
    few = (await _report(tmp_path, *(_order(f"ord_{n}", pay_status="paid") for n in range(1, 4))))["comprobantes"]

    assert few["minimo_para_medir"] == 40
    assert few["actual"]["colossus"]["suficientes"] is False
    assert few["actual"]["colossus"]["coincidencia_pct"] is None

    for n in range(4, 41):
        _noted(tmp_path, _conversation(tmp_path, n, order_id=f"ord_{n}"), "coincide")
    enough = (await _report(tmp_path, *(_order(f"ord_{n}", pay_status="paid") for n in range(1, 40)),
                            _order("ord_40", stage="cancelled")))["comprobantes"]["actual"]["colossus"]

    assert enough["suficientes"] is True
    assert enough["coincidencia_pct"] == 97.5
    assert enough["contra_el_equipo"]["dijo_coincide_y_el_equipo_no_acepto"] == 1


async def test_test_orders_and_other_windows_do_not_mix(tmp_path: Path) -> None:
    _noted(tmp_path, _conversation(tmp_path, 1, order_id="ord_test"), "coincide")
    _noted(tmp_path, _conversation(tmp_path, 2, order_id="ord_old", started_ms=OLDER_MS), "coincide")
    _noted(tmp_path, _conversation(tmp_path, 3, order_id="ord_v1", workflow="v1"), "coincide")

    section = (await _report(tmp_path, _order("ord_test", is_test=True), _order("ord_old", pay_status="paid"),
                             _order("ord_v1", pay_status="paid")))["comprobantes"]

    assert section["actual"]["colossus"]["contra_el_equipo"]["pedidos_comparados"] == 0
    assert section["base"]["colossus"]["contra_el_equipo"]["coincidencias"] == 1
    assert section["actual"]["botsito"]["comprobantes"] == 1


async def test_a_verdict_that_is_not_a_known_code_is_never_copied(tmp_path: Path) -> None:
    sid = _conversation(tmp_path, 1, order_id="ord_a")
    _noted(tmp_path, sid, "texto libre con el monto 84.000", "Calle 1 # 2-3", source="wa_109876544567")

    dumped = json.dumps(await _report(tmp_path, _order("ord_a", pay_status="paid")), ensure_ascii=False)

    for private in ("texto libre", "84.000", "Calle", "wa_", "ord_a"):
        assert private not in dumped


async def test_the_text_report_tells_the_receipts_in_plain_words(tmp_path: Path) -> None:
    _noted(tmp_path, _conversation(tmp_path, 1, order_id="ord_a"), "coincide")

    text = strategy.render(await _report(tmp_path, _order("ord_a", pay_status="paid")))

    assert "Comprobantes" in text and "faltan" in text
