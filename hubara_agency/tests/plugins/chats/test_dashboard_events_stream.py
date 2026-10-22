"""El SSE del dashboard visto desde un cliente: qué recibe y cuándo (app móvil, 2026-10-21).

Caso del operador: en la app los mensajes llegaban tarde y salía «Sin conexión» mientras la web iba al día. Con 581
chats cada bandeja completa pesa ~380 KB y sale en cada tick con cambios (hasta cada 2,5 s): un teléfono en 4G no da
abasto, la cola de su conexión se llena de bandejas viejas y lo nuevo le llega minutos después. Estos tests fijan:

- un cliente atrasado recibe solo la bandeja MÁS NUEVA (las viejas en cola ya no sirven);
- la app (`?inbox=delta`) recibe la bandeja completa al conectar y después solo las filas que cambiaron;
- la web sigue igual (bandeja completa);
- cada conexión deja en el log su modo, cuánto mandó y cuánto se atrasó, para saber si un teléfono está al día.
"""

import asyncio
import json

import pytest
from loguru import logger

from src.platform.events import DashboardEventBus
from src.plugins.chats.api import dashboard


def _row(session_id: str, preview: str = "Hola", **extra) -> dict:
    return {
        "session_id": session_id,
        "phone_number": session_id.removeprefix("wa_"),
        "tag": "INTERESADO",
        "active_agent_route": "ventas",
        "last_message_preview": preview,
        "last_updated_timestamp": 1.0,
        **extra,
    }


@pytest.fixture
def bus(monkeypatch) -> DashboardEventBus:
    """Un bus propio, sin muestreador de fondo y con latido corto: cuando la cola queda vacía llega `: ping`."""
    bus = DashboardEventBus()
    monkeypatch.setattr(dashboard, "get_dashboard_event_bus", lambda: bus)
    monkeypatch.setattr(dashboard, "ensure_sampler", lambda: None)
    monkeypatch.setattr(dashboard, "_HEARTBEAT_S", 0.05)

    async def inbox() -> dict:
        return {"sessions": [_row("wa_test_laura"), _row("wa_test_carlos")]}

    monkeypatch.setattr(dashboard, "list_dashboard_sessions", inbox)
    return bus


def _decode(chunk: bytes | str) -> dict | None:
    text = chunk.decode() if isinstance(chunk, bytes) else chunk
    if text.startswith(":"):
        return None  # latido
    return json.loads(text[len("data: ") :])


async def _next(stream) -> dict:
    while True:
        event = _decode(await asyncio.wait_for(stream.__anext__(), timeout=2))
        if event is not None:
            return event


async def _until_idle(stream) -> list[dict]:
    """Todo lo que manda hasta el primer latido (la cola quedó vacía)."""
    events = []
    for _ in range(50):
        event = _decode(await asyncio.wait_for(stream.__anext__(), timeout=2))
        if event is None:
            return events
        events.append(event)
    raise AssertionError("el stream nunca quedó quieto")


async def test_un_cliente_atrasado_recibe_solo_la_bandeja_mas_nueva(bus: DashboardEventBus) -> None:
    stream = dashboard.dashboard_events_generator()
    assert (await _next(stream))["type"] == "sessions_snapshot"

    # Mientras el teléfono bajaba la primera, el muestreador publicó tres bandejas con sus avisos.
    for version in ("v1", "v2", "v3"):
        bus.publish("chats", "sessions_snapshot", payload={"sessions": [_row("wa_test_laura", preview=version)]})
        bus.publish("chats", "session_updated", id="wa_test_laura")

    events = await _until_idle(stream)
    snapshots = [e for e in events if e["type"] == "sessions_snapshot"]
    assert [s["payload"]["sessions"][0]["last_message_preview"] for s in snapshots] == ["v3"]
    assert [e["id"] for e in events if e["type"] == "session_updated"] == ["wa_test_laura"]
    await stream.aclose()


async def test_la_app_recibe_al_conectar_la_bandeja_y_despues_solo_lo_que_cambio(bus: DashboardEventBus) -> None:
    stream = dashboard.dashboard_events_generator(inbox="delta")
    first = await _next(stream)
    assert first["type"] == "sessions_snapshot"
    assert [s["session_id"] for s in first["payload"]["sessions"]] == ["wa_test_laura", "wa_test_carlos"]

    bus.publish("chats", "sessions_snapshot", payload={"sessions": [
        _row("wa_test_laura", preview="¿Me confirmas el precio?"),  # cambió
        _row("wa_test_sofia"),                                       # llegó
    ]})                                                              # carlos ya no está

    events = await _until_idle(stream)
    assert [e["type"] for e in events] == ["sessions_delta"]
    delta = events[0]["payload"]
    assert {r["session_id"]: r["last_message_preview"] for r in delta["sessions"]} == {
        "wa_test_laura": "¿Me confirmas el precio?",
        "wa_test_sofia": "Hola",
    }
    assert delta["removed"] == ["wa_test_carlos"]
    await stream.aclose()


async def test_a_la_app_no_le_llega_nada_si_su_bandeja_no_cambio(bus: DashboardEventBus) -> None:
    stream = dashboard.dashboard_events_generator(inbox="delta")
    await _next(stream)

    # Cambió el detalle de un chat (otra cosa que la fila de la bandeja): solo el aviso de esa sesión.
    bus.publish("chats", "sessions_snapshot", payload={"sessions": [_row("wa_test_laura"), _row("wa_test_carlos")]})
    bus.publish("chats", "session_updated", id="wa_test_laura")

    assert [e["type"] for e in await _until_idle(stream)] == ["session_updated"]
    await stream.aclose()


async def test_la_web_sigue_recibiendo_la_bandeja_completa(bus: DashboardEventBus) -> None:
    stream = dashboard.dashboard_events_generator()
    await _next(stream)

    bus.publish("chats", "sessions_snapshot", payload={"sessions": [_row("wa_test_laura", preview="nuevo"), _row("wa_test_carlos")]})

    events = await _until_idle(stream)
    assert [e["type"] for e in events] == ["sessions_snapshot"]
    assert len(events[0]["payload"]["sessions"]) == 2
    await stream.aclose()


async def test_los_eventos_de_otros_dominios_pasan_tal_cual(bus: DashboardEventBus) -> None:
    stream = dashboard.dashboard_events_generator(inbox="delta")
    await _next(stream)

    bus.publish("marketing", "campaign_updated", id="camp_1")
    bus.publish("marketing", "campaign_updated", id="camp_1")
    bus.publish("orders", "changed")

    assert [(e["domain"], e["type"]) for e in await _until_idle(stream)] == [
        ("marketing", "campaign_updated"), ("marketing", "campaign_updated"), ("orders", "changed"),
    ]
    await stream.aclose()


async def test_cada_conexion_deja_en_el_log_su_modo_lo_que_mando_y_lo_que_omitio(bus: DashboardEventBus) -> None:
    lines: list[str] = []
    sink = logger.add(lines.append, format="{message}", level="INFO")
    try:
        stream = dashboard.dashboard_events_generator(inbox="delta")
        await _next(stream)
        bus.publish("chats", "sessions_snapshot", payload={"sessions": [_row("wa_test_laura", preview="v1")]})
        bus.publish("chats", "sessions_snapshot", payload={"sessions": [_row("wa_test_laura", preview="v2")]})
        await _until_idle(stream)
        await stream.aclose()
    finally:
        logger.remove(sink)

    opened = [line for line in lines if line.startswith("sse: conexión abierta")]
    closed = [line for line in lines if line.startswith("sse: conexión cerrada")]
    assert len(opened) == 1 and "cambios" in opened[0]
    assert len(closed) == 1
    # La bandeja al conectar + un solo cambio (la bandeja vieja de la cola se omitió).
    assert "2 eventos" in closed[0]
    assert "1 bandejas viejas omitidas" in closed[0]
    assert "KB" in closed[0] and "retraso máx" in closed[0]


async def test_una_conexion_larga_deja_un_resumen_periodico(bus: DashboardEventBus, monkeypatch) -> None:
    monkeypatch.setattr(dashboard, "_STATS_EVERY_S", 0.0)
    lines: list[str] = []
    sink = logger.add(lines.append, format="{message}", level="INFO")
    try:
        stream = dashboard.dashboard_events_generator(inbox="delta")
        await _next(stream)
        bus.publish("chats", "session_updated", id="wa_test_laura")
        await _until_idle(stream)
        await stream.aclose()
    finally:
        logger.remove(sink)

    assert any(line.startswith("sse: conexión viva (cambios") for line in lines)


async def test_el_endpoint_usa_el_modo_que_pide_la_app(bus: DashboardEventBus) -> None:
    response = await dashboard.stream_dashboard_events(inbox="delta")
    stream = response.body_iterator
    await _next(stream)

    bus.publish("chats", "sessions_snapshot", payload={"sessions": [_row("wa_test_laura", preview="otro")]})

    assert [e["type"] for e in await _until_idle(stream)] == ["sessions_delta"]
    await stream.aclose()


async def test_un_modo_desconocido_es_el_de_la_web(bus: DashboardEventBus) -> None:
    response = await dashboard.stream_dashboard_events(inbox="cualquiera")
    stream = response.body_iterator
    await _next(stream)

    bus.publish("chats", "sessions_snapshot", payload={"sessions": [_row("wa_test_laura", preview="otro")]})

    assert [e["type"] for e in await _until_idle(stream)] == ["sessions_snapshot"]
    await stream.aclose()
