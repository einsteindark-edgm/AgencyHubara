"""La sección de objeciones del informe para el Estratega (2026-11-10).

La decisión `objecion` (paquete ventas-9) anota, por cada mensaje del cliente,
si puso una objeción y de qué tipo. El informe la cuenta por tipo y dice en
cuántas conversaciones el cliente siguió hacia la compra después de ponerla
(la conversación avanzó de etapa) y cuántas terminaron en pedido. Solo conteos:
ni textos ni números de clientes. Datos sintéticos.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from src.plugins.chats.agent.sales_eval import strategy
from src.plugins.chats.shared import turn_traces
from src.sdk.connectorkit import InMemoryOrderFacts, OrderFacts

NOW = datetime(2026, 10, 10, 15, 0, tzinfo=UTC)
#: Dentro de la ventana actual (7 días) y de la ventana base (28 días antes).
RECENT_MS = int(datetime(2026, 10, 8, 14, 0, tzinfo=UTC).timestamp() * 1000)
OLDER_MS = int(datetime(2026, 9, 20, 14, 0, tzinfo=UTC).timestamp() * 1000)
MINUTE_MS = 60_000


def _conversation(
    vault: Path, sid: str, *, started_ms: int, workflow: str, stages: tuple[str, ...] = ("descubrimiento",),
    order_id: str | None = None,
) -> None:
    """Una conversación con un episodio: el turno n del bot empieza al minuto n
    y deja la conversación en la etapa n."""
    for n, stage in enumerate(stages, start=1):
        start = started_ms + n * MINUTE_MS
        turn_traces.append_trace(vault, sid, {
            "episode_id": "ep_001", "turn": n, "workflow": workflow, "trigger": "customer",
            "turn_started_ms": start, "recorded_at_ms": start + 5_000, "stage_out": stage,
        })
    path = vault / sid / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"episodes": [
        {"episode_id": "ep_001", "started_at_ms": started_ms, "closed_at_ms": None, "order_id": order_id},
    ]}), encoding="utf-8")


def _noted(vault: Path, sid: str, *rows: dict) -> None:
    """Lo que `objecion` dejó en las decisiones de la conversación."""
    path = vault / sid / "evals" / "decisions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps({"stage": "ingest", "capability": "objecion", **row}, ensure_ascii=False) + "\n")


def _objection(at_ms: int, kind: str) -> dict:
    """En sombra: decide la regla (nada) y lo que leyó Jev queda en `jev`."""
    return {"at_ms": at_ms, "by": "reglas", "provider": "sombra", "value": "", "jev": kind, "agree": False}


def _none(at_ms: int) -> dict:
    """Jev leyó el mensaje y no hay objeción."""
    return {"at_ms": at_ms, "by": "reglas", "provider": "sombra", "value": "", "agree": True}


def _unsure(at_ms: int) -> dict:
    """Jev no supo: ni tipo ni acuerdo."""
    return {"at_ms": at_ms, "by": "reglas", "provider": "sombra", "value": ""}


def _after_turn(started_ms: int, n: int) -> int:
    """Un mensaje del cliente que llega después del turno n del bot."""
    return started_ms + n * MINUTE_MS + 30_000


async def _report(vault: Path, **kwargs) -> dict:
    kwargs.setdefault("order_facts", InMemoryOrderFacts())
    return await strategy.build_report(vault, days=7, base_days=28, now=NOW, **kwargs)


def _paid(order_id: str) -> OrderFacts:
    return OrderFacts(order_id=order_id, display_id="1", total_cop=1, currency_code="cop", pay_status="paid",
                      stage="new", customer="Cliente sintético", is_draft=False, is_test=False)


def _three_colossus_conversations(vault: Path) -> None:
    # A: objeta el precio al principio, sigue hasta confirmar y paga.
    _conversation(vault, "wa_100000000001", started_ms=RECENT_MS, workflow="v2",
                  stages=("descubrimiento", "variantes", "confirmacion"), order_id="ord_paid")
    _noted(vault, "wa_100000000001",
           _objection(_after_turn(RECENT_MS, 1), "precio"),
           _none(_after_turn(RECENT_MS, 2)),
           _unsure(_after_turn(RECENT_MS, 3)))
    # B: objeta el precio y el envío y la conversación no pasa de ahí.
    _conversation(vault, "wa_100000000002", started_ms=RECENT_MS, workflow="v2")
    _noted(vault, "wa_100000000002",
           _objection(_after_turn(RECENT_MS, 1), "precio"),
           _objection(_after_turn(RECENT_MS, 1) + 10_000, "envio"),
           _objection(_after_turn(RECENT_MS, 1) + 20_000, "Ana Pérez dice que no le alcanza"))
    # C: objeta el producto en su primer mensaje (antes de cualquier turno del
    # bot) y después elige variantes. Aquí Jev decide (el laboratorio o un número
    # de prueba): el tipo está en `value`.
    _conversation(vault, "wa_100000000003", started_ms=RECENT_MS, workflow="v2", stages=("descubrimiento", "variantes"))
    _noted(vault, "wa_100000000003",
           {"at_ms": RECENT_MS + 1_000, "by": "jev", "provider": "jev", "value": "producto", "jev": "producto",
            "agree": False})


async def test_without_notes_the_section_says_what_is_missing(tmp_path: Path) -> None:
    _conversation(tmp_path, "wa_100000000001", started_ms=RECENT_MS, workflow="v2")

    objections = (await _report(tmp_path))["objeciones"]

    assert objections["disponible"] is False
    assert "sombra" in objections["faltante"]
    assert objections["motivo"]


async def test_objections_are_counted_by_kind_and_by_what_happened_next(tmp_path: Path) -> None:
    _three_colossus_conversations(tmp_path)

    report = await _report(tmp_path, order_facts=InMemoryOrderFacts([_paid("ord_paid")]))

    objections = report["objeciones"]
    assert objections["disponible"] is True
    assert objections["tipos"] == ["precio", "envio", "tiempo_entrega", "confianza", "producto"]
    colossus = objections["actual"]["colossus"]
    assert colossus["lecturas"] == 5  # con tipo o «ninguna»
    assert colossus["sin_lectura"] == 2  # Jev no supo, o dejó algo que no es un tipo
    assert colossus["con_objecion"] == 4
    assert colossus["conversaciones_con_objecion"] == 3
    assert colossus["por_tipo"] == {
        "precio": {"mensajes": 2, "conversaciones": 2, "siguieron_hacia_la_compra": 1, "terminaron_en_pedido": 1},
        "envio": {"mensajes": 1, "conversaciones": 1, "siguieron_hacia_la_compra": 0, "terminaron_en_pedido": 0},
        "producto": {"mensajes": 1, "conversaciones": 1, "siguieron_hacia_la_compra": 1, "terminaron_en_pedido": 0},
    }
    assert list(colossus["por_tipo"]) == ["precio", "envio", "producto"]  # primero las más frecuentes
    assert objections["nota"]


async def test_objections_fall_in_their_window_and_their_bot(tmp_path: Path) -> None:
    _three_colossus_conversations(tmp_path)
    _conversation(tmp_path, "wa_100000000004", started_ms=OLDER_MS, workflow="v2")
    _noted(tmp_path, "wa_100000000004", _objection(_after_turn(OLDER_MS, 1), "precio"))
    _conversation(tmp_path, "wa_100000000005", started_ms=RECENT_MS, workflow="v1")
    _noted(tmp_path, "wa_100000000005", _objection(_after_turn(RECENT_MS, 1), "envio"))

    objections = (await _report(tmp_path))["objeciones"]

    assert objections["base"]["colossus"]["por_tipo"] == {
        "precio": {"mensajes": 1, "conversaciones": 1, "siguieron_hacia_la_compra": 0, "terminaron_en_pedido": 0},
    }
    assert objections["actual"]["botsito"]["con_objecion"] == 1
    assert objections["actual"]["sin_bot"] == {
        "lecturas": 0, "sin_lectura": 0, "con_objecion": 0, "conversaciones_con_objecion": 0, "por_tipo": {},
    }


async def test_the_section_carries_only_codes(tmp_path: Path) -> None:
    _three_colossus_conversations(tmp_path)

    report = await _report(tmp_path, order_facts=InMemoryOrderFacts([_paid("ord_paid")]))

    dumped = json.dumps(report["objeciones"], ensure_ascii=False)
    for private in ("Ana", "alcanza", "wa_", "100000000002", "ord_paid"):
        assert private not in dumped


async def test_the_plain_report_reads_the_objections_in_spanish(tmp_path: Path) -> None:
    _three_colossus_conversations(tmp_path)

    text = strategy.render(await _report(tmp_path, order_facts=InMemoryOrderFacts([_paid("ord_paid")])))

    assert "Objeciones: no disponible" not in text
    assert "Objeciones (Colossus, ahora): 4 en 5 mensajes leídos" in text
    assert "precio 2" in text and "envio 1" in text


async def test_the_plain_report_says_when_there_are_no_objection_notes(tmp_path: Path) -> None:
    _conversation(tmp_path, "wa_100000000001", started_ms=RECENT_MS, workflow="v2")

    text = strategy.render(await _report(tmp_path))

    assert "Objeciones: no disponible" in text
