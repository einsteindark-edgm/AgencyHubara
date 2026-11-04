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


def _decisions(vault: Path, sid: str, *rows: dict) -> None:
    """Decisiones de Jev de la conversación (`evals/decisions.jsonl`, `decision_log`)."""
    path = vault / sid / "evals" / "decisions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps({"stage": "turno", **row}, ensure_ascii=False) + "\n")


async def test_jev_doubts_are_counted_per_capability(tmp_path: Path) -> None:
    sid = "wa_100000000001"
    _conversation(tmp_path, sid, started_ms=RECENT_MS, workflow="v2")
    at = RECENT_MS + 2 * MINUTE_MS
    _decisions(tmp_path, sid,
               {"at_ms": at, "capability": "compra", "by": "respaldo", "provider": "jev", "value": False, "reason": "duda"},
               {"at_ms": at, "capability": "compra", "by": "jev", "provider": "jev", "value": True, "jev": True},
               {"at_ms": at, "capability": "compra", "by": "respaldo", "provider": "jev", "value": False,
                "reason": "timeout"},
               {"at_ms": at, "capability": "compra", "by": "respaldo", "provider": "jev", "value": False,
                "reason": "error con texto del cliente adentro"},
               {"at_ms": at, "capability": "cajas", "by": "jev", "provider": "jev", "value": "duda", "jev": "duda"},
               {"at_ms": at, "capability": "baja", "by": "reglas", "provider": "sombra", "value": False},
               {"at_ms": ANCIENT_MS, "capability": "compra", "by": "respaldo", "provider": "jev", "reason": "duda"})

    jev = (await _report(tmp_path))["jev_dudas"]

    colossus = jev["actual"]["colossus"]
    assert colossus["decisiones"] == 6
    assert colossus["dudas"] == 2
    assert colossus["por_capacidad"]["compra"] == {
        "decisiones": 4, "dudas": 1, "jev_dudo": 1, "valor_duda": 0, "respaldo_por_falla": 2, "en_sombra": 0,
    }
    assert colossus["por_capacidad"]["cajas"]["valor_duda"] == 1
    assert colossus["por_capacidad"]["baja"]["en_sombra"] == 1
    assert list(colossus["por_capacidad"])[:2] == ["cajas", "compra"]  # primero las que más dudan
    assert colossus["respaldo_por_motivo"] == {"otro": 1, "timeout": 1}
    assert "sombra" in jev["nota"]


async def test_interventions_per_100_conversations_compare_both_windows(tmp_path: Path) -> None:
    during = RECENT_MS + MINUTE_MS + 1_000
    _conversation(tmp_path, "wa_100000000001", started_ms=RECENT_MS, workflow="v2",
                  status_history=[_handoff(during, "CATALOG_GAP")])
    _conversation(tmp_path, "wa_100000000002", started_ms=RECENT_MS, workflow="v2")
    _chat(tmp_path, "wa_100000000002", _team(during), _team(during))
    _conversation(tmp_path, "wa_100000000003", started_ms=RECENT_MS, workflow="v2",
                  status_history=[_handoff(during, "PAYMENT_VERIFICATION_PENDING")])
    _conversation(tmp_path, "wa_100000000004", started_ms=RECENT_MS, workflow="v2")
    older = OLDER_MS + MINUTE_MS + 1_000
    _conversation(tmp_path, "wa_100000000005", started_ms=OLDER_MS, workflow="v2",
                  status_history=[_handoff(older, "EXPLICIT_REQUEST")])
    _conversation(tmp_path, "wa_100000000006", started_ms=OLDER_MS, workflow="v2")
    _chat(tmp_path, "wa_100000000006", _team(older))

    north_star = (await _report(tmp_path))["intervenciones_por_100"]

    assert north_star["actual"]["colossus"] == {
        "conversaciones": 4,
        "con_intervencion": 3,
        "por_100": 75.0,
        "relevos_por_100": 50.0,
        "relevos_sin_verificar_pago_por_100": 25.0,
        "mensajes_del_equipo_por_100": 50.0,
    }
    assert north_star["base"]["colossus"]["por_100"] == 100.0
    assert north_star["cambio_por_100"]["colossus"] == -25.0
    assert north_star["actual"]["botsito"]["por_100"] is None  # sin conversaciones no hay tasa
    assert north_star["definicion"]


async def test_objections_are_reported_as_missing_instead_of_invented(tmp_path: Path) -> None:
    _conversation(tmp_path, "wa_100000000001", started_ms=RECENT_MS, workflow="v2")

    objections = (await _report(tmp_path))["objeciones"]

    assert objections["disponible"] is False
    assert objections["motivo"] and objections["faltante"]


def _busy_conversation(vault: Path, sid: str) -> None:
    """Una conversación con todo lo que el informe lee, llena de texto personal."""
    during = RECENT_MS + MINUTE_MS + 1_000
    _conversation(vault, sid, started_ms=RECENT_MS, workflow="v2", order_id="ord_paid",
                  status_history=[_handoff(during, "Quiero hablar con alguien, soy Ana Pérez")])
    _chat(vault, sid, {"role": "user", "content": "Calle falsa 123, Ana Pérez", "timestamp": _at(RECENT_MS)},
          _team(during), _team(during, operator_tool="enviar a Ana Pérez"))
    _decisions(vault, sid, {"at_ms": during, "capability": "compra", "by": "respaldo", "provider": "jev",
                            "value": "Ana Pérez", "reason": "duda"})


async def test_the_report_carries_no_conversation_ids_phone_numbers_or_texts(tmp_path: Path) -> None:
    _busy_conversation(tmp_path, "wa_109876544567")

    report = await _report(tmp_path, order_facts=InMemoryOrderFacts([_order("ord_paid", pay_status="paid")]))

    dumped = json.dumps(report, ensure_ascii=False)
    assert report["relevos"]["actual"]["colossus"]["total"] == 1  # sí leyó la conversación
    for private in ("109876544567", "4567", "wa_", "ord_paid", "Ana", "Calle", "texto del colega", "texto libre"):
        assert private not in dumped


async def test_a_damaged_conversation_is_counted_without_logging_its_number(
    tmp_path: Path, caplog, capsys
) -> None:
    _conversation(tmp_path, "wa_100000000001", started_ms=RECENT_MS, workflow="v2")
    damaged = tmp_path / "wa_109876544567" / "metadata.json"
    damaged.parent.mkdir(parents=True)
    damaged.write_text('{"episodes": [', encoding="utf-8")

    with caplog.at_level("DEBUG"):
        report = await _report(tmp_path)

    assert report["muestra"]["sesiones_ilegibles"] == 1
    assert report["muestra"]["actual"]["conversaciones"]["colossus"] == 1
    captured = capsys.readouterr()
    assert "109876544567" not in caplog.text + captured.err + captured.out


def test_the_cli_prints_the_report_as_json(tmp_path: Path, monkeypatch, capsys) -> None:
    from src.plugins.chats.agent.sales_eval.evals import composition

    _conversation(tmp_path, "wa_100000000001", started_ms=int(datetime.now(UTC).timestamp() * 1000), workflow="v2")
    monkeypatch.setattr(composition, "get_vault_dir", lambda: tmp_path)

    assert strategy.main(["--dias", "3", "--base", "14", "--json"]) == 0

    printed = json.loads(capsys.readouterr().out)
    assert printed["ventanas"]["actual"]["dias"] == 3 and printed["ventanas"]["base"]["dias"] == 14
    assert printed["muestra"]["actual"]["conversaciones"]["colossus"] == 1


async def test_the_plain_report_reads_in_spanish_without_ids(tmp_path: Path) -> None:
    _busy_conversation(tmp_path, "wa_109876544567")

    text = strategy.render(await _report(tmp_path))

    assert "Intervenciones por cada 100 conversaciones" in text
    assert "109876544567" not in text and "Ana" not in text
