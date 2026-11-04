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
from src.sdk.connectorkit import InMemoryOrderFacts

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
