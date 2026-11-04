"""Informe para el Estratega del ciclo Colossus (2026-11-03): cifras agregadas
de producción, sin textos ni números de clientes, para proponer cada semana
qué le falta a Colossus (el bot nuevo, workflow v2 con Jev) para llevar más
conversaciones sin el equipo.

Lee lo que ya vive en el vault (metadata de cada conversación, la traza de
cada turno, el historial del chat y las decisiones de Jev) y los pedidos de
OrderFacts. Datos sintéticos: ningún número, nombre ni texto real.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from src.plugins.chats.agent.sales_eval import strategy
from src.plugins.chats.shared import turn_traces
from src.sdk.connectorkit import InMemoryOrderFacts, OrderFacts

NOW = datetime(2026, 10, 10, 15, 0, tzinfo=UTC)
#: Dentro de la ventana actual (7 días: 2026-10-04 a 2026-10-10).
RECENT_MS = int(datetime(2026, 10, 8, 14, 0, tzinfo=UTC).timestamp() * 1000)
#: Dentro de la ventana base (28 días antes: 2026-09-06 a 2026-10-03).
OLDER_MS = int(datetime(2026, 9, 20, 14, 0, tzinfo=UTC).timestamp() * 1000)
#: Antes de las dos ventanas.
ANCIENT_MS = int(datetime(2026, 8, 1, 14, 0, tzinfo=UTC).timestamp() * 1000)
MINUTE_MS = 60_000


def _conversation(
    vault: Path,
    sid: str,
    *,
    started_ms: int,
    workflow: str | None,
    stages: tuple[str, ...] = ("descubrimiento",),
    episode_id: str = "ep_001",
    order_id: str | None = None,
    status_history: list[dict] | None = None,
) -> None:
    """Una conversación con un episodio: un turno del bot por etapa (sin bot
    si `workflow` es None) y su metadata."""
    if workflow is not None:
        for n, stage in enumerate(stages, start=1):
            start = started_ms + n * MINUTE_MS
            turn_traces.append_trace(vault, sid, {
                "episode_id": episode_id, "turn": n, "workflow": workflow, "trigger": "customer",
                "turn_started_ms": start, "recorded_at_ms": start + 5_000, "stage_out": stage,
            })
    path = vault / sid / "metadata.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"episodes": []}
    data["episodes"].append({
        "episode_id": episode_id, "started_at_ms": started_ms, "closed_at_ms": None, "order_id": order_id,
    })
    if status_history:
        data.setdefault("status_history", []).extend(status_history)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


async def _report(vault: Path, **kwargs) -> dict:
    kwargs.setdefault("order_facts", InMemoryOrderFacts())
    return await strategy.build_report(vault, days=7, base_days=28, now=NOW, **kwargs)


async def test_the_sample_counts_conversations_and_turns_per_bot_and_window(tmp_path: Path) -> None:
    _conversation(tmp_path, "wa_100000000001", started_ms=RECENT_MS, workflow="v2",
                  stages=("descubrimiento", "variantes"))
    _conversation(tmp_path, "wa_100000000002", started_ms=RECENT_MS, workflow="v1")
    _conversation(tmp_path, "wa_100000000003", started_ms=OLDER_MS, workflow="v2")
    _conversation(tmp_path, "wa_100000000004", started_ms=RECENT_MS, workflow=None)
    _conversation(tmp_path, "wa_100000000005", started_ms=ANCIENT_MS, workflow="v2")

    report = await _report(tmp_path)

    actual, base = report["muestra"]["actual"], report["muestra"]["base"]
    assert actual["conversaciones"] == {"colossus": 1, "botsito": 1, "mixto": 0, "sin_bot": 1}
    assert actual["turnos"] == {"colossus": 2, "botsito": 1, "mixto": 0, "sin_bot": 0}
    assert base["conversaciones"] == {"colossus": 1, "botsito": 0, "mixto": 0, "sin_bot": 0}
    assert report["ventanas"]["actual"] == {"dias": 7, "desde": "2026-10-04", "hasta": "2026-10-10"}
    assert report["ventanas"]["base"] == {"dias": 28, "desde": "2026-09-06", "hasta": "2026-10-03"}


async def test_the_operator_test_numbers_are_left_out(tmp_path: Path) -> None:
    _conversation(tmp_path, "wa_100000000001", started_ms=RECENT_MS, workflow="v2")
    _conversation(tmp_path, "wa_100000000009", started_ms=RECENT_MS, workflow="v2")
    rollout = tmp_path / "_rollout" / "perception.json"
    rollout.parent.mkdir(parents=True)
    rollout.write_text(json.dumps({"mode": "canary", "test_numbers": ["wa_100000000009"]}), encoding="utf-8")

    report = await _report(tmp_path)

    assert report["muestra"]["actual"]["conversaciones"]["colossus"] == 1
    assert report["muestra"]["excluidas_numeros_de_prueba"] == 1


def _handoff(at_ms: int, reason: str | None = None, *, source: str | None = None, route: str = "humano") -> dict:
    entry = {"tag": "HUMANO" if route == "humano" else "", "active_route": route, "timestamp": at_ms / 1000,
             "motivo": "texto libre del motivo que nunca sale del vault"}
    if reason:
        entry["reason_category"] = reason
    if source:
        entry["source"] = source
    return entry


async def test_handoffs_count_each_entry_to_the_team_by_reason_stage_and_origin(tmp_path: Path) -> None:
    turn = RECENT_MS + MINUTE_MS  # el turno n empieza en RECENT_MS + n minutos
    _conversation(tmp_path, "wa_100000000001", started_ms=RECENT_MS, workflow="v2",
                  stages=("descubrimiento", "variantes"),
                  status_history=[_handoff(turn + 2 * MINUTE_MS, "CATALOG_GAP")])
    _conversation(tmp_path, "wa_100000000002", started_ms=RECENT_MS, workflow="v2", status_history=[
        _handoff(turn + 1_000, source="dashboard_intervene"),
        _handoff(turn + 2_000, "EXPLICIT_REQUEST"),  # ya estaba con el equipo: no es otro relevo
        _handoff(turn + 3_000, route="ventas"),  # «Devolver al bot»
        _handoff(turn + 4_000, "PAYMENT_VERIFICATION_PENDING", source="safety_net"),
    ])
    _conversation(tmp_path, "wa_100000000003", started_ms=RECENT_MS, workflow="v1",
                  status_history=[_handoff(turn + 1_000, "EXPLICIT_REQUEST")])

    relevos = (await _report(tmp_path))["relevos"]["actual"]

    colossus = relevos["colossus"]
    assert colossus["total"] == 3
    assert colossus["conversaciones"] == 2
    assert colossus["por_motivo"] == {"CATALOG_GAP": 1, "PAYMENT_VERIFICATION_PENDING": 1, "TOMADO_POR_EL_EQUIPO": 1}
    assert colossus["por_etapa"] == {"descubrimiento": 2, "variantes": 1}
    assert colossus["por_origen"] == {"bot": 1, "dashboard_intervene": 1, "safety_net": 1}
    assert colossus["por_motivo_y_etapa"]["CATALOG_GAP"] == {"variantes": 1}
    assert relevos["botsito"]["por_motivo"] == {"EXPLICIT_REQUEST": 1}


def _chat(vault: Path, sid: str, *events: dict) -> None:
    """Eventos del historial del chat (`sessions/<id>.jsonl`, el del dashboard)."""
    path = vault / sid / "sessions" / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def _at(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat()


def _team(at_ms: int, **fields) -> dict:
    return {"role": "assistant", "sender": "human", "content": "texto del colega que nunca sale", "timestamp": _at(at_ms),
            **fields}


async def test_what_the_team_sent_is_counted_by_type_and_stage_without_its_text(tmp_path: Path) -> None:
    sid = "wa_100000000001"
    _conversation(tmp_path, sid, started_ms=RECENT_MS, workflow="v2", stages=("descubrimiento", "variantes"))
    first, second = RECENT_MS + MINUTE_MS + 30_000, RECENT_MS + 3 * MINUTE_MS
    _chat(tmp_path, sid,
          {"role": "user", "content": "hola", "timestamp": _at(RECENT_MS)},
          {"role": "assistant", "content": "respuesta del bot", "timestamp": _at(first)},
          _team(first),
          _team(second, kind="template", template_name="seguimiento_humano"),
          _team(second, kind="ui_component", component_kind="shipping_flow", operator_tool="request_shipping_details"),
          _team(second, image_url="media/out/foto.jpg"))
    _conversation(tmp_path, "wa_100000000002", started_ms=RECENT_MS, workflow="v1")
    _chat(tmp_path, "wa_100000000002", _team(first))

    report = await _report(tmp_path)

    colossus = report["equipo"]["actual"]["colossus"]
    assert colossus["mensajes"] == 4
    assert colossus["conversaciones"] == 1
    assert colossus["por_tipo"] == {"foto": 1, "formulario_de_envio": 1, "plantilla": 1, "texto": 1}
    assert colossus["por_etapa"] == {"variantes": 3, "descubrimiento": 1}
    assert colossus["herramientas_de_la_app"] == {"request_shipping_details": 1}
    assert report["equipo"]["actual"]["botsito"]["mensajes"] == 1
    assert "texto del colega" not in json.dumps(report, ensure_ascii=False)


def _order(order_id: str, *, pay_status: str = "pending", stage: str = "new", is_test: bool = False) -> OrderFacts:
    return OrderFacts(order_id=order_id, display_id="1", total_cop=1, currency_code="cop", pay_status=pay_status,
                      stage=stage, customer="Cliente sintético", is_draft=False, is_test=is_test)


async def test_the_funnel_says_where_conversations_stop_and_orders_come_from_order_facts(tmp_path: Path) -> None:
    _conversation(tmp_path, "wa_100000000001", started_ms=RECENT_MS, workflow="v2", order_id="ord_paid",
                  stages=("descubrimiento", "variantes", "confirmacion", "datos_envio", "cierre"))
    _conversation(tmp_path, "wa_100000000002", started_ms=RECENT_MS, workflow="v2", stages=("descubrimiento", "variantes"))
    _conversation(tmp_path, "wa_100000000003", started_ms=RECENT_MS, workflow="v2", order_id="ord_cancelled")
    _conversation(tmp_path, "wa_100000000004", started_ms=RECENT_MS, workflow="v2", order_id="ord_test")
    facts = InMemoryOrderFacts([
        _order("ord_paid", pay_status="paid"),
        _order("ord_cancelled", stage="cancelled"),
        _order("ord_test", is_test=True),
    ])

    report = await _report(tmp_path, order_facts=facts)

    funnel = report["embudo"]["actual"]["colossus"]
    assert funnel["conversaciones"] == 4
    assert funnel["por_etapa_mas_lejana"] == {"postcierre": 3, "variantes": 1}
    assert funnel["llegaron_a"]["variantes"] == 4 and funnel["llegaron_a"]["confirmacion"] == 3
    assert funnel["sin_pedido_se_quedan_en"] == {"postcierre": 2, "variantes": 1}
    assert funnel["pedidos"] == {"con_pedido": 1, "pagados": 1, "cancelados": 1, "de_prueba": 1, "sin_confirmar": 0}
    assert funnel["conversion_a_pedido_pct"] == 25.0
    assert report["embudo"]["fuente_pedidos"] == "OrderFacts"


async def test_orders_that_medusa_could_not_confirm_are_not_counted_as_lost_sales(tmp_path: Path) -> None:
    _conversation(tmp_path, "wa_100000000001", started_ms=RECENT_MS, workflow="v2", order_id="ord_unknown")

    report = await _report(tmp_path, order_facts=InMemoryOrderFacts(available=False))

    funnel = report["embudo"]["actual"]["colossus"]
    assert funnel["pedidos"]["sin_confirmar"] == 1
    assert funnel["pedidos"]["con_pedido"] == 0
    assert funnel["sin_pedido_se_quedan_en"] == {}
    assert "aviso" in report["embudo"]
