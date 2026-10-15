"""`backfill_delivery_checks`: el doble check de WhatsApp para lo que salió
ANTES del deploy del PR #437.

Esos mensajes muestran «sin confirmación»: sus estados de Meta quedaron en
`_orphan_delivery_statuses.jsonl` (y en el log analítico) y sus textos no
guardan `wamids`. El script, que corre el operador en la caja, une los dos con
datos de Meta: los wamids de cada burbuja (trazas de turno v2,
`outbound_text_index`) y los estados que Meta mandó por webhook. Nunca inventa
una hora (`at_ms` en null) ni un check.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plugins.chats.api import dashboard

#: Sintéticos (forge: nunca un teléfono real en el repo).
_SID = "wa_573001234567"
_OTHER_SID = "wa_573007654321"


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _seed(
    vault: Path,
    *,
    history: list[dict],
    traces: list[dict] | None = None,
    metadata: dict | None = None,
    sid: str = _SID,
) -> None:
    session = vault / sid
    session.mkdir(parents=True, exist_ok=True)
    (session / "metadata.json").write_text(json.dumps(metadata or {"tag": "INTERESADO"}), encoding="utf-8")
    _write_jsonl(session / "sessions" / f"{sid}.jsonl", history)
    if traces:
        _write_jsonl(session / "evals" / "turn_traces.jsonl", traces)


def _orphans(vault: Path, statuses: list[tuple[str, str]]) -> None:
    """Como los deja `IngestDeliveryStatus._dead_letter`: sin hora ni destinatario."""
    _write_jsonl(
        vault / "_orphan_delivery_statuses.jsonl",
        [
            {"wa_message_id": w, "status": s, "pricing": None, "reason": "not_found",
             "session_id": None, "episode_id": None}
            for w, s in statuses
        ],
    )


def _bot_turn(at_iso: str, bubbles: list[tuple[str, str]], sid: str = _SID) -> dict:
    """Traza v2 de un turno con su paso `outbound` (lo que Meta aceptó)."""
    at = _ms(at_iso)
    return {
        "turn_key": f"{sid}/ep_001/t1",
        "episode_id": "ep_001",
        "turn_started_ms": at - 4_000,
        "recorded_at_ms": at + 1_000,
        "steps": [
            {"kind": "outbound", "at_ms": at, "bubbles": [
                {"kind": "text", "text": text, "wamid": wamid, "delivered": True} for wamid, text in bubbles
            ]},
        ],
    }


@pytest.fixture
def panel(tmp_path: Path):
    """El detalle de la conversación que leen el panel y la App Operador."""
    vault = tmp_path / "vault"
    vault.mkdir()
    app = FastAPI()
    app.include_router(dashboard.router, prefix="/api/dashboard")
    with patch("src.plugins.chats.api.dashboard.WORKSPACE_VAULT_DIR", vault):
        client = TestClient(app)

        def messages(sid: str = _SID) -> list[dict]:
            return client.get(f"/api/dashboard/sessions/{sid}").json()["messages"]

        yield vault, messages


def _seed_outage_reply(vault: Path) -> None:
    """La noche del 2026-10-15 (caída de DeepSeek): el bot contestó en dos
    burbujas, el episodio no tenía `outbound_messages` y los estados de Meta
    quedaron huérfanos."""
    _seed(
        vault,
        history=[
            {"role": "user", "content": "hola", "timestamp": "2026-10-15T02:12:58+00:00", "wamid": "wamid.IN1"},
            {"role": "assistant", "content": "¡Hola! Soy el asistente.\n\n¿Qué buscas hoy?",
             "timestamp": "2026-10-15T02:13:06+00:00"},
        ],
        traces=[_bot_turn("2026-10-15T02:13:05+00:00", [
            ("wamid.B1", "¡Hola! Soy el asistente."), ("wamid.B2", "¿Qué buscas hoy?"),
        ])],
    )
    _orphans(vault, [("wamid.B1", "sent"), ("wamid.B2", "sent"), ("wamid.B1", "read"), ("wamid.B2", "read")])


def _files(vault: Path) -> dict[str, bytes]:
    return {str(p.relative_to(vault)): p.read_bytes() for p in sorted(vault.rglob("*")) if p.is_file()}


def test_an_old_bot_text_shows_what_whatsapp_reported_after_the_backfill(panel) -> None:
    from scripts.backfill_delivery_checks import main

    vault, messages = panel
    _seed_outage_reply(vault)
    assert messages()[1]["delivery"]["status"] == "unconfirmed"

    main(["--vault", str(vault), "--apply"])

    assert messages()[1]["delivery"] == {
        "status": "read", "at_ms": None, "error_code": None, "error_title": None,
    }


def test_without_apply_it_only_says_what_it_would_do(panel) -> None:
    from scripts.backfill_delivery_checks import main

    vault, messages = panel
    _seed_outage_reply(vault)
    before = _files(vault)

    report = main(["--vault", str(vault)])

    assert _files(vault) == before, "sin --apply no se escribe nada"
    assert messages()[1]["delivery"]["status"] == "unconfirmed"
    assert report.get("mode") == "dry_run"
    assert report.get("text_events_linked") == 1
    assert report.get("status_rows") == 4


def test_a_second_run_changes_nothing(panel) -> None:
    """Idempotente: lo que ya está (de esta corrida o del webhook en vivo) no se repite."""
    from scripts.backfill_delivery_checks import main

    vault, _ = panel
    _seed_outage_reply(vault)
    main(["--vault", str(vault), "--apply"])
    after_first = _files(vault)

    report = main(["--vault", str(vault), "--apply"])

    assert _files(vault) == after_first
    assert (report.get("text_events_linked"), report.get("status_rows")) == (0, 0)


def test_the_history_keeps_a_backup_and_only_the_linked_text_changes(panel) -> None:
    """Reescribir el historial es lo único delicado: queda una copia del
    original al lado y las demás líneas no se tocan, byte a byte."""
    from scripts.backfill_delivery_checks import main

    vault, _ = panel
    _seed_outage_reply(vault)
    history = vault / _SID / "sessions" / f"{_SID}.jsonl"
    # Una línea de otro serializador (escapes ASCII, sin espacios) sigue igual.
    foreign = b'{"role":"user","content":"\\u00bfa qu\\u00e9 hora?","timestamp":"2026-10-15T02:14:00+00:00"}\n'
    history.write_bytes(history.read_bytes() + foreign)
    original = history.read_bytes()

    main(["--vault", str(vault), "--apply"])

    assert (history.parent / f"{history.name}.bak-delivery-backfill").read_bytes() == original
    before, after = original.splitlines(keepends=True), history.read_bytes().splitlines(keepends=True)
    assert [after[0], after[2]] == [before[0], before[2]]
    assert json.loads(after[1]) == {**json.loads(before[1]), "wamids": ["wamid.B1", "wamid.B2"]}


def test_an_event_the_bot_writes_during_the_rewrite_is_not_lost(panel, monkeypatch) -> None:
    """El historial es append-only y lo escriben las activities en vivo: lo
    que entra entre la lectura y el reemplazo sigue en el historial."""
    import os

    from scripts.backfill_delivery_checks import main
    from src.platform.session_history.store import FilesystemMessageHistoryStore

    vault, _ = panel
    _seed_outage_reply(vault)
    store = FilesystemMessageHistoryStore(vault)
    real_replace = os.replace

    def replace_while_the_customer_writes(src, dst):
        if str(dst).endswith(f"{_SID}.jsonl"):
            store.append_user_event(_SID, "¿siguen ahí?", wamid="wamid.IN2")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace_while_the_customer_writes)

    main(["--vault", str(vault), "--apply"])

    events = store.read_events(_SID)
    assert [e.get("wamid") for e in events] == ["wamid.IN1", None, "wamid.IN2"]
    assert events[1]["wamids"] == ["wamid.B1", "wamid.B2"]


def _text_index(*bubbles: tuple[str, str, str]) -> dict:
    """`metadata.outbound_text_index`: wamid → {text, author}, en orden de envío."""
    return {"outbound_text_index": {w: {"text": t, "author": a} for w, t, a in bubbles}}


def test_an_old_operator_text_is_linked_through_the_outbound_text_index(panel) -> None:
    """Lo que el operador manda desde el panel o la app no tiene traza de
    turno: sus burbujas están en el índice de citas de la metadata."""
    from scripts.backfill_delivery_checks import main

    vault, messages = panel
    _seed(
        vault,
        history=[
            {"role": "user", "content": "¿hay alguien?", "timestamp": "2026-10-15T01:40:00+00:00"},
            {"role": "assistant", "sender": "human", "content": "Hola, soy del equipo.\n\nYa te ayudo.",
             "timestamp": "2026-10-15T01:42:00+00:00"},
        ],
        metadata=_text_index(("wamid.H1", "Hola, soy del equipo.", "human"), ("wamid.H2", "Ya te ayudo.", "human")),
    )
    _orphans(vault, [("wamid.H1", "delivered"), ("wamid.H2", "delivered")])

    main(["--vault", str(vault), "--apply"])

    assert messages()[1]["delivery"]["status"] == "delivered"


def test_a_text_that_repeats_in_the_history_is_not_guessed_from_the_index(panel) -> None:
    """El índice no tiene hora: con el mismo texto dos veces en el chat no se
    sabe de cuál es la burbuja. Queda «sin confirmación»; nunca un check ajeno."""
    from scripts.backfill_delivery_checks import main

    vault, messages = panel
    thanks = {"role": "assistant", "content": "¡Gracias por escribirnos!"}
    _seed(
        vault,
        history=[
            {**thanks, "timestamp": "2026-10-10T15:00:00+00:00"},
            {"role": "user", "content": "otra cosa", "timestamp": "2026-10-15T01:00:00+00:00"},
            {**thanks, "timestamp": "2026-10-15T01:00:30+00:00"},
        ],
        metadata=_text_index(("wamid.T1", "¡Gracias por escribirnos!", "agent")),
    )
    _orphans(vault, [("wamid.T1", "read")])

    report = main(["--vault", str(vault), "--apply"])

    assert [m["delivery"]["status"] for m in messages() if m["role"] == "assistant"] == ["unconfirmed"] * 2
    assert report.get("text_events_linked") == 0


def test_an_evicted_text_does_not_take_a_bubble_of_a_later_message(panel) -> None:
    """El índice guarda solo las últimas burbujas. «Te espero» viejo ya no
    está; la que está es la 2.ª burbuja de un mensaje posterior: es de ese."""
    from scripts.backfill_delivery_checks import main

    vault, messages = panel
    _seed(
        vault,
        history=[
            {"role": "assistant", "content": "Te espero", "timestamp": "2026-10-01T15:00:00+00:00"},
            {"role": "user", "content": "listo", "timestamp": "2026-10-15T01:00:00+00:00"},
            {"role": "assistant", "content": "¡Perfecto!\n\nTe espero", "timestamp": "2026-10-15T01:00:30+00:00"},
        ],
        metadata=_text_index(("wamid.P1", "¡Perfecto!", "agent"), ("wamid.P2", "Te espero", "agent")),
    )
    _orphans(vault, [("wamid.P1", "read"), ("wamid.P2", "read")])

    main(["--vault", str(vault), "--apply"])

    old, _, new = messages()
    assert (old["delivery"]["status"], new["delivery"]["status"]) == ("unconfirmed", "read")


def _analytics(vault: Path, day: str, statuses: list[tuple[str, str, str | None]]) -> None:
    """El log analítico del día: un `delivery_status` por webhook de Meta,
    también los que entraron por el camino del costo (nunca huérfanos)."""
    from dataclasses import asdict

    from src.platform.analytics import make_delivery_status

    path = vault / "_analytics" / f"{day}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for wamid, status, sid in statuses:
            event = make_delivery_status(session_id=sid, tenant_id=None, wa_message_id=wamid, status=status)
            fh.write(json.dumps(asdict(event), ensure_ascii=False, separators=(",", ":")) + "\n")


def test_statuses_that_went_through_the_cost_path_come_from_the_analytics_log(panel) -> None:
    """Con el episodio abierto, el estado materializaba el costo y no quedaba
    huérfano: solo está en el log analítico. Una tarjeta (con su `wamid` desde
    siempre) y un texto unido por su traza muestran lo que dijo Meta."""
    from scripts.backfill_delivery_checks import main

    vault, messages = panel
    _seed(
        vault,
        history=[
            {"role": "assistant", "kind": "ui_component", "component_kind": "products_list",
             "content": "El bot envió el catálogo", "timestamp": "2026-10-12T15:00:00+00:00", "wamid": "wamid.C1"},
            {"role": "assistant", "content": "¿Cuál te gustó?", "timestamp": "2026-10-12T15:00:03+00:00"},
        ],
        traces=[_bot_turn("2026-10-12T15:00:02+00:00", [("wamid.B1", "¿Cuál te gustó?")])],
    )
    _analytics(vault, "2026-10-12", [
        ("wamid.C1", "sent", _SID), ("wamid.C1", "delivered", _SID), ("wamid.B1", "delivered", _SID),
        ("wamid.C1", "read", _SID),
    ])

    main(["--vault", str(vault), "--apply"])

    card, text = messages()
    assert (card["delivery"]["status"], text["delivery"]["status"]) == ("read", "delivered")


def test_session_limits_the_backfill_to_the_conversations_named(panel) -> None:
    """Primero las tres conversaciones del 2026-10-15: `--session` no toca las demás."""
    from scripts.backfill_delivery_checks import main

    vault, messages = panel
    _seed_outage_reply(vault)
    _seed(
        vault,
        sid=_OTHER_SID,
        history=[{"role": "assistant", "content": "Hola", "timestamp": "2026-10-15T02:20:01+00:00"}],
        traces=[_bot_turn("2026-10-15T02:20:00+00:00", [("wamid.O1", "Hola")], sid=_OTHER_SID)],
    )
    _write_jsonl(vault / "_orphan_delivery_statuses.jsonl", [
        *_read(vault / "_orphan_delivery_statuses.jsonl"),
        {"wa_message_id": "wamid.O1", "status": "read", "reason": "not_found"},
    ])
    other_before = {k: v for k, v in _files(vault).items() if k.startswith(_OTHER_SID)}

    main(["--vault", str(vault), "--apply", "--session", _SID])

    assert messages()[1]["delivery"]["status"] == "read"
    assert {k: v for k, v in _files(vault).items() if k.startswith(_OTHER_SID)} == other_before


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_the_report_has_only_counts_and_masked_conversations(panel, capsys) -> None:
    """Lo lee el operador en la caja (y puede llegar a la conversación con
    Claude): conteos, la conversación enmascarada, ni teléfonos ni textos.
    Dice cuántos mensajes salientes quedan «sin confirmación» después."""
    from scripts.backfill_delivery_checks import main

    vault, _ = panel
    _seed_outage_reply(vault)
    history = vault / _SID / "sessions" / f"{_SID}.jsonl"
    history.write_text(
        history.read_text(encoding="utf-8")
        + json.dumps({"role": "assistant", "content": "Sin rastro", "timestamp": "2026-10-15T02:30:00+00:00"}) + "\n",
        encoding="utf-8",
    )
    _write_jsonl(vault / "_orphan_delivery_statuses.jsonl", [
        *_read(vault / "_orphan_delivery_statuses.jsonl"),
        {"wa_message_id": "wamid.DE_OTRA", "status": "read", "reason": "not_found"},
    ])

    report = main(["--vault", str(vault), "--session", _SID, "--session", "wa_573000000099"])

    printed = capsys.readouterr().out
    assert json.loads(printed) == report
    assert _SID.removeprefix("wa_") not in printed and "Hola" not in printed
    assert report.get("sessions_not_found") == 1
    assert report.get("statuses_without_message") == 1
    assert report.get("sessions") == {
        "···4567": {"text_events_linked": 1, "status_rows": 4, "outbound": 2, "unconfirmed_after": 1},
    }


def test_a_line_separator_inside_a_message_does_not_shift_the_events(panel) -> None:
    """`json.dumps(ensure_ascii=False)` deja crudo un U+2028 que pegó el
    cliente. El historial se lee línea por línea (`\\n`), como el panel: si el
    relleno contara distinto, los `wamids` caerían en otro mensaje."""
    from scripts.backfill_delivery_checks import main

    vault, messages = panel
    _seed(
        vault,
        history=[
            {"role": "user", "content": "quiero esta y esta", "timestamp": "2026-10-15T02:12:00+00:00"},
            {"role": "assistant", "content": "¡Claro!", "timestamp": "2026-10-15T02:13:06+00:00"},
        ],
        traces=[_bot_turn("2026-10-15T02:13:05+00:00", [("wamid.B1", "¡Claro!")])],
    )
    _orphans(vault, [("wamid.B1", "read")])

    main(["--vault", str(vault), "--apply"])

    customer, bot = messages()
    assert "wamids" not in customer
    assert bot["delivery"]["status"] == "read"


def test_a_repeated_bot_text_takes_the_bubbles_of_its_own_turn(panel) -> None:
    """El bot repite frases. La del 1-oct (sin traza v2) no se queda con las
    burbujas del turno del 15-oct: la traza se une por texto Y por hora."""
    from scripts.backfill_delivery_checks import main

    vault, messages = panel
    more = {"role": "assistant", "content": "¿Te ayudo con algo más?"}
    _seed(
        vault,
        history=[
            {**more, "timestamp": "2026-10-01T15:00:00+00:00"},
            {"role": "user", "content": "gracias", "timestamp": "2026-10-15T02:13:00+00:00"},
            {**more, "timestamp": "2026-10-15T02:13:06+00:00"},
        ],
        traces=[_bot_turn("2026-10-15T02:13:05+00:00", [("wamid.M1", "¿Te ayudo con algo más?")])],
    )
    _orphans(vault, [("wamid.M1", "read")])

    main(["--vault", str(vault), "--apply"])

    old, _, new = messages()
    assert (old["delivery"]["status"], new["delivery"]["status"]) == ("unconfirmed", "read")


def test_only_customer_conversations_are_touched(panel) -> None:
    """El chat solo muestra el check de conversaciones de clientes
    (`DeliveryStatusLog`): un directorio de pruebas del vault no se toca."""
    from scripts.backfill_delivery_checks import main

    vault, _ = panel
    _seed_outage_reply(vault)
    golden = "wa_golden_saludo"
    _seed(
        vault,
        sid=golden,
        history=[{"role": "assistant", "content": "Hola", "timestamp": "2026-10-15T02:20:01+00:00"}],
        traces=[_bot_turn("2026-10-15T02:20:00+00:00", [("wamid.G1", "Hola")], sid=golden)],
    )
    golden_before = {k: v for k, v in _files(vault).items() if k.startswith(golden)}

    report = main(["--vault", str(vault), "--apply"])

    assert {k: v for k, v in _files(vault).items() if k.startswith(golden)} == golden_before
    assert report.get("sessions_scanned") == 1


def test_a_bubble_that_already_belongs_to_a_message_is_not_taken_again(panel) -> None:
    """Después del deploy los textos guardan sus `wamids`. «¡Con gusto!» de
    antes (ya fuera del índice y sin traza con wamid) no se queda con la
    burbuja del «¡Con gusto!» de después, que ya es suya."""
    from scripts.backfill_delivery_checks import main

    vault, messages = panel
    _seed(
        vault,
        history=[
            {"role": "assistant", "content": "¡Con gusto!", "timestamp": "2026-10-15T02:13:00+00:00"},
            {"role": "user", "content": "gracias", "timestamp": "2026-10-15T02:13:30+00:00"},
            {"role": "assistant", "content": "¡Con gusto!", "timestamp": "2026-10-15T02:14:00+00:00",
             "wamids": ["wamid.N1"]},
        ],
        traces=[_bot_turn("2026-10-15T02:13:59+00:00", [("wamid.N1", "¡Con gusto!")])],
        metadata=_text_index(("wamid.N1", "¡Con gusto!", "agent")),
    )
    _orphans(vault, [("wamid.N1", "read")])

    main(["--vault", str(vault), "--apply"])

    old, _, new = messages()
    assert (old["delivery"]["status"], new["delivery"]["status"]) == ("unconfirmed", "read")


def test_since_only_fills_the_messages_from_that_day_on(panel) -> None:
    """`--since 2026-09-16` (medianoche de Bogotá): lo anterior queda como estaba."""
    from scripts.backfill_delivery_checks import main

    vault, messages = panel
    _seed(
        vault,
        history=[
            {"role": "assistant", "content": "Antes", "timestamp": "2026-09-16T04:30:01+00:00"},
            {"role": "assistant", "content": "Después", "timestamp": "2026-09-16T05:30:01+00:00"},
        ],
        traces=[
            _bot_turn("2026-09-16T04:30:00+00:00", [("wamid.A1", "Antes")]),
            _bot_turn("2026-09-16T05:30:00+00:00", [("wamid.D1", "Después")]),
        ],
    )
    _orphans(vault, [("wamid.A1", "read"), ("wamid.D1", "read")])

    report = main(["--vault", str(vault), "--apply", "--since", "2026-09-16"])

    before, after = messages()
    assert "wamids" not in before and before["delivery"]["status"] == "unconfirmed"
    assert after["delivery"]["status"] == "read"
    assert report.get("sessions") == {
        "···4567": {"text_events_linked": 1, "status_rows": 1, "outbound": 1, "unconfirmed_after": 0},
    }


def test_since_skips_the_days_of_the_analytics_log_before_it(panel) -> None:
    """Un estado siempre llega después del envío: los días del log analítico
    anteriores a `--since` no se leen (en la caja la memoria es poca)."""
    from scripts.backfill_delivery_checks import main

    vault, _ = panel
    _seed(
        vault,
        history=[{"role": "assistant", "content": "Hola", "timestamp": "2026-09-20T15:00:01+00:00"}],
        traces=[_bot_turn("2026-09-20T15:00:00+00:00", [("wamid.H1", "Hola")])],
    )
    _analytics(vault, "2026-09-10", [("wamid.H1", "sent", _SID)])
    _analytics(vault, "2026-09-20", [("wamid.H1", "read", _SID)])

    report = main(["--vault", str(vault), "--since", "2026-09-16"])

    assert report.get("status_rows") == 1
