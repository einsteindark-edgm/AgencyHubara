"""La vista `reply_gap` del motor de decisiones (incidente del 2026-10-06).

Un cliente volvió 11 días después y escribió «Buenas». Jev dio `cortesia.solo`
0,94 y el turno contestó solo «Buenos días 🤍», sin invitar a seguir: un saludo
con el que el cliente abre la conversación no es cortesía. La capacidad no
veía si el mensaje abría el episodio ni cuánto hacía que la tienda había
escrito (su estado solo trae la ventana, sin horas). La vista lo deriva de la
entrada, genérica para cualquier paquete:

* `opens_episode`: el episodio activo arrancó con ESTE mensaje (el ingest lo
  abrió con él: `started_inbound_message_id`), también cuando llega por la
  reentrada de un audio o una foto (`_transcribed`, `_vision`).
* `hours_since_store`: horas enteras desde el último mensaje de la tienda (un
  evento `assistant` del historial, por su `timestamp`: el bot, el equipo o
  una plantilla); None si nunca escribió o ninguno se puede fechar. No sale de
  `last_inbound_at_ms`: el ingest ya lo pisó con este mensaje.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import yaml

from src.plugins.chats.agent.sales.decisions.bundled import CATALOG_PATH, builtin
from src.plugins.chats.agent.sales.decisions.readings import Inbound

SID = "wa_573001234567"
NOW = 1_790_000_000_000
HOUR = 3_600_000
WAMID = "wamid.HBgMNTczMDAxMjM0NTY3FQIAEhgUM0E"


def _iso(ms: int, *, z: bool = False, naive: bool = False) -> str:
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    if naive:
        return dt.replace(tzinfo=None).isoformat()
    text = dt.isoformat()
    return text.replace("+00:00", "Z") if z else text


def _metadata(started_by: str | None, *, closed_before: bool = True) -> dict[str, Any]:
    """Lo que deja el ingest ANTES de las lecturas: el episodio activo (y el
    anterior, cerrado) y `last_inbound_at_ms` ya pisado con este mensaje."""
    episodes: list[dict[str, Any]] = []
    if closed_before:
        episodes.append({
            "episode_id": "ep_001", "started_at_ms": NOW - 300 * HOUR, "started_inbound_message_id": "wamid.viejo",
            "closed_at_ms": NOW - 260 * HOUR, "closing_tag": "TIMEOUT",
        })
    episodes.append({
        "episode_id": f"ep_{len(episodes) + 1:03d}", "started_at_ms": NOW, "started_inbound_message_id": started_by,
        "closed_at_ms": None,
    })
    return {"episodes": episodes, "last_inbound_at_ms": NOW, "last_inbound_message_id": WAMID}


def _view(
    *, message_id: str | None = WAMID, metadata: dict[str, Any] | None = None, events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    inp = Inbound(
        session_id=SID, text="Buenas", now_ms=NOW, message_id=message_id,
        metadata=_metadata(WAMID) if metadata is None else metadata, events=events or [],
    )
    return builtin("view", "reply_gap")(inp)


def _store(ms: int, content: str = "Hola, ¿te ayudo con algo más?", **extra: Any) -> dict[str, Any]:
    return {"role": "assistant", "content": content, "timestamp": _iso(ms), **extra}


def _customer(ms: int, content: str = "gracias") -> dict[str, Any]:
    return {"role": "user", "content": content, "timestamp": _iso(ms), "wamid": "wamid.otro"}


def test_the_customer_who_comes_back_eleven_days_later_opens_the_episode() -> None:
    """El incidente: el episodio nuevo arrancó con «Buenas» y la tienda había
    escrito por última vez 264 horas antes."""
    events = [_store(NOW - 270 * HOUR), _customer(NOW - 265 * HOUR), _store(NOW - 264 * HOUR)]

    assert _view(events=events) == {"opens_episode": True, "hours_since_store": 264}


def test_the_first_message_to_a_store_that_never_wrote() -> None:
    assert _view(metadata=_metadata(WAMID, closed_before=False)) == {"opens_episode": True, "hours_since_store": None}


def test_a_message_inside_the_episode_does_not_open_it() -> None:
    events = [_store(NOW - 2 * HOUR)]

    assert _view(metadata=_metadata("wamid.el_primero"), events=events) == {"opens_episode": False, "hours_since_store": 2}


def test_the_reentry_of_an_audio_or_a_photo_opens_the_episode_its_message_started() -> None:
    """El ingest vuelve a entrar con `<wamid>_transcribed` / `<wamid>_vision`:
    es el MISMO mensaje del cliente."""
    assert _view(message_id=f"{WAMID}_transcribed")["opens_episode"] is True
    assert _view(message_id=f"{WAMID}_vision", metadata=_metadata(f"{WAMID}_vision"))["opens_episode"] is True


def test_without_an_active_episode_or_a_message_id_nothing_opens() -> None:
    closed = _metadata(WAMID)
    closed["episodes"][-1]["closed_at_ms"] = NOW

    assert _view(metadata=closed)["opens_episode"] is False
    assert _view(message_id=None)["opens_episode"] is False
    assert _view(metadata=_metadata(None))["opens_episode"] is False
    assert _view(metadata={}) == {"opens_episode": False, "hours_since_store": None}


def test_the_hours_count_from_the_latest_message_of_the_store_by_its_timestamp() -> None:
    """El bot, el equipo (`sender: human`) o una plantilla: todos son la
    tienda. Los mensajes del cliente no cuentan; la hora se trunca."""
    events = [
        _store(NOW - 50 * HOUR),
        _store(NOW - 30 * HOUR, "Tu pedido va en camino", kind="template"),
        _store(NOW - 26 * HOUR - 59 * 60_000, "Te escribe Ana del equipo", sender="human"),
        _customer(NOW - HOUR),
    ]

    assert _view(events=events)["hours_since_store"] == 26


def test_a_store_message_that_cannot_be_dated_is_skipped() -> None:
    events = [
        _store(NOW - 40 * HOUR),
        {"role": "assistant", "content": "sin hora"},
        {"role": "assistant", "content": "hora rota", "timestamp": "ayer"},
        {"role": "assistant", "content": "hora en número", "timestamp": NOW},
    ]

    assert _view(events=events)["hours_since_store"] == 40
    assert _view(events=events[1:])["hours_since_store"] is None


def test_the_timestamp_formats_of_the_history_are_read() -> None:
    """`Z`, `+00:00` y sin zona (se lee como UTC, igual que el laboratorio)."""
    for stamp in (_iso(NOW - 25 * HOUR, z=True), _iso(NOW - 25 * HOUR), _iso(NOW - 25 * HOUR, naive=True)):
        events = [{"role": "assistant", "content": "hola", "timestamp": stamp}]
        assert _view(events=events)["hours_since_store"] == 25, stamp


def test_a_store_message_from_the_future_counts_as_just_now() -> None:
    assert _view(events=[_store(NOW + 3 * HOUR)])["hours_since_store"] == 0


def test_the_customer_s_last_inbound_is_not_the_store() -> None:
    """`last_inbound_at_ms` vale `now` (el ingest lo pisó): si se leyera,
    cualquier mensaje parecería recién contestado."""
    meta = _metadata(WAMID)
    meta["last_inbound_at_ms"] = NOW

    assert _view(metadata=meta, events=[_customer(NOW - 500 * HOUR)])["hours_since_store"] is None


def test_the_catalog_declares_the_fields_the_view_gives() -> None:
    catalog = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
    spec = catalog["builtins"]["reply_gap"]

    assert (spec["kind"], spec["input"]) == ("view", "Inbound")
    assert set(spec["fields"]) == set(_view())
    assert spec["fields"] == {"opens_episode": "bool", "hours_since_store": "int?"}
