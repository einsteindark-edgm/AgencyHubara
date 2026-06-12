"""Semántica del bus de eventos del dashboard (F1, auditoría frontend).

Cubre el contrato que el SSE multiplexado asume:
  - fan-out a N suscriptores
  - publish nunca bloquea: cola llena → descarta el MÁS VIEJO
  - unsubscribe saca la cola del fan-out
  - el envelope SSE serializa domain/type/id/ts_ms (+payload solo si existe)
"""

import json

from src.platform.events import DashboardEvent, DashboardEventBus
from src.platform.events.bus import _QUEUE_MAXSIZE


def test_fanout_to_multiple_subscribers() -> None:
    bus = DashboardEventBus()
    q1, q2 = bus.subscribe(), bus.subscribe()

    bus.publish("orders", "changed", id="#1247")

    e1, e2 = q1.get_nowait(), q2.get_nowait()
    assert e1.domain == e2.domain == "orders"
    assert e1.type == "changed"
    assert e1.id == "#1247"
    assert e1.ts_ms > 0


def test_publish_never_blocks_drops_oldest_on_full_queue() -> None:
    bus = DashboardEventBus()
    queue = bus.subscribe()

    for i in range(_QUEUE_MAXSIZE + 5):
        bus.publish("chats", "session_updated", id=f"wa_{i}")

    # La cola quedó llena pero el publisher nunca explotó; lo más viejo se fue.
    assert queue.qsize() == _QUEUE_MAXSIZE
    first = queue.get_nowait()
    assert first.id == "wa_5"  # los 5 primeros se descartaron


def test_unsubscribe_stops_delivery() -> None:
    bus = DashboardEventBus()
    queue = bus.subscribe()
    bus.unsubscribe(queue)

    bus.publish("eta", "changed")

    assert queue.qsize() == 0
    assert bus.subscriber_count == 0


def test_sse_envelope_shape() -> None:
    event = DashboardEvent(
        domain="chats", type="sessions_snapshot", payload={"sessions": []}, ts_ms=123
    )
    raw = event.to_sse()
    assert raw.startswith("data: ")
    assert raw.endswith("\n\n")
    body = json.loads(raw[len("data: ") :])
    assert body == {
        "domain": "chats",
        "type": "sessions_snapshot",
        "id": None,
        "ts_ms": 123,
        "payload": {"sessions": []},
    }


def test_sse_envelope_omits_missing_payload() -> None:
    body = json.loads(
        DashboardEvent(domain="orders", type="changed", ts_ms=1).to_sse()[6:]
    )
    assert "payload" not in body
