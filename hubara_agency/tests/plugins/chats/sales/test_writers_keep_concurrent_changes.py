"""Las activities de ventas que esperan algo entre leer y escribir
`metadata.json` no pisan lo que otro escritor puso mientras tanto
(incidente 2026-10-06: una copia vieja devolvió una foto ya entregada a la cola).

  * `transcribe_audio_activity` espera la transcripción (segundos);
  * `ensure_promised_handoff_activity` espera al motor de decisiones (Jev).

Las dos escribían su copia ENTERA; ahora solo lo suyo, sobre lo fresco.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from temporalio.testing import ActivityEnvironment

from src.platform.state import FilesystemMetadataStore

SID = "wa_573001234567"


def _seed(vault: Path, **metadata: Any) -> Path:
    path = vault / SID / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "phone_number_id": "pnid-1",
                "active_route": "ventas",
                "tag": "NO_ETIQUETADO",
                "pending_ui_intents": [{"id": "foto-t4", "kind": "product_detail"}],
                **metadata,
            }
        ),
        encoding="utf-8",
    )
    return path


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _flush_pops_the_photo(vault: Path) -> None:
    def _pop(fresh: dict[str, Any]) -> dict[str, Any]:
        fresh["pending_ui_intents"] = []
        fresh["outbound_media_index"] = {"wamid.foto": {"handle": "cubo-love"}}
        return fresh

    FilesystemMetadataStore(vault).update(SID, _pop)


@pytest.mark.asyncio
async def test_audio_transcription_keeps_what_the_flush_wrote_meanwhile(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import src.platform.analytics as analytics
    import src.platform.audio.composition as audio_composition
    from src.plugins.chats.agent.sales.activities.transcribe_audio import transcribe_audio_activity

    path = _seed(
        _isolate_vault_dir,
        pending_transcription={"media_id": "media-1", "inbound_message_id": "wamid.audio", "voice": True},
    )

    class _Port:
        async def transcribe(self, request: Any) -> SimpleNamespace:
            _flush_pops_the_photo(_isolate_vault_dir)
            return SimpleNamespace(
                ok=True, text="¿la tienes en lila?", error=None, provider="fake",
                duration_seconds=3.0, cost_usd_estimate=0.0, latency_ms=10,
            )

    class _Bus:
        async def record(self, event: Any) -> None:
            return None

    monkeypatch.setattr(audio_composition, "get_audio_transcription_port", lambda: _Port())
    monkeypatch.setattr(analytics, "get_event_bus", lambda: _Bus())

    text = await ActivityEnvironment().run(transcribe_audio_activity, SID)

    assert text == "¿la tienes en lila?"
    metadata = _read(path)
    assert metadata["pending_ui_intents"] == [], "la transcripción devolvió la foto a la cola"
    assert "wamid.foto" in metadata["outbound_media_index"]
    assert "pending_transcription" not in metadata
    assert metadata["recent_transcriptions"][-1]["text"] == "¿la tienes en lila?"


@pytest.mark.asyncio
async def test_promised_handoff_escalation_keeps_what_the_flush_wrote_meanwhile(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import src.plugins.chats.agent.sales.decisions.guards as guards
    from src.plugins.chats.agent.sales.activities.episode_closure import ensure_promised_handoff_activity

    path = _seed(_isolate_vault_dir)

    async def promised_handoff(text: str, **kwargs: Any) -> bool:
        _flush_pops_the_photo(_isolate_vault_dir)
        return True

    monkeypatch.setattr(guards, "promised_handoff", promised_handoff)

    assert await ensure_promised_handoff_activity(SID, "Un colega del equipo te escribe enseguida.") is True

    metadata = _read(path)
    assert (metadata["active_route"], metadata["tag"]) == ("humano", "HUMANO")
    assert metadata["pending_ui_intents"] == [], "la escalación devolvió la foto a la cola"
    assert "wamid.foto" in metadata["outbound_media_index"]


@pytest.mark.asyncio
async def test_promised_handoff_escalates_over_a_damaged_document_from_the_last_good_copy(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La red leía el archivo directo y no hacía nada ante un daño: el bot
    prometía un colega y nadie tenía el caso. Se recupera de `.prev`."""
    import src.plugins.chats.agent.sales.decisions.guards as guards
    from src.plugins.chats.agent.sales.activities.episode_closure import ensure_promised_handoff_activity

    path = _seed(_isolate_vault_dir)
    path.with_name("metadata.json.prev").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text('{"active_route": "ventas", "episodes": [', encoding="utf-8")

    async def promised_handoff(text: str, **kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(guards, "promised_handoff", promised_handoff)

    assert await ensure_promised_handoff_activity(SID, "Un colega del equipo te escribe enseguida.") is True
    assert _read(path)["active_route"] == "humano"


@pytest.mark.asyncio
async def test_a_transient_read_error_stops_the_promised_handoff_net_and_keeps_the_takeover(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sexta revisión del PR #393: con un EMFILE, `read()` devolvía la copia
    vieja (antes de la toma del operador) y la red escalaba encima, pisando el
    motivo y el `escalation_reason` del operador. Ahora `read()` lanza: la
    activity falla (Temporal la reintenta) y nada cambia."""
    import errno
    import os

    import src.plugins.chats.agent.sales.decisions.guards as guards
    from src.plugins.chats.agent.sales.activities.episode_closure import ensure_promised_handoff_activity

    path = _seed(_isolate_vault_dir)
    store = FilesystemMetadataStore(_isolate_vault_dir)
    store.update(
        SID,
        lambda d: {**d, "active_route": "humano", "tag": "HUMANO", "motivo": "Lo atiende Ana (operador)",
                   "escalation_reason": "OPERATOR_TAKEOVER"},
    )
    before = {p.name: p.read_bytes() for p in path.parent.iterdir() if not p.name.endswith(".lock")}

    async def promised_handoff(text: str, **kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(guards, "promised_handoff", promised_handoff)
    real_read_text = Path.read_text
    left = {"n": 1}

    def read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if self.name == "metadata.json" and left["n"]:
            left["n"] -= 1
            raise OSError(errno.EMFILE, os.strerror(errno.EMFILE))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)

    with pytest.raises(OSError):
        await ensure_promised_handoff_activity(SID, "Un colega del equipo te escribe enseguida.")

    monkeypatch.setattr(Path, "read_text", real_read_text)
    after = {p.name: p.read_bytes() for p in path.parent.iterdir() if not p.name.endswith(".lock")}
    assert after == before, "la red pisó la toma del operador"


# --- revisión del PR #393 (M7) -------------------------------------------------


@pytest.mark.asyncio
async def test_the_pending_transcription_of_another_audio_stays(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mientras se transcribe el audio 1 llega el audio 2: al terminar, se saca
    el pendiente del 1, no el del 2 (que todavía no se transcribió)."""
    import src.platform.analytics as analytics
    import src.platform.audio.composition as audio_composition
    from src.plugins.chats.agent.sales.activities.transcribe_audio import transcribe_audio_activity

    path = _seed(
        _isolate_vault_dir,
        pending_transcription={"media_id": "media-1", "inbound_message_id": "wamid.audio1", "voice": True},
    )
    second = {"media_id": "media-2", "inbound_message_id": "wamid.audio2", "voice": True}

    class _Port:
        async def transcribe(self, request: Any) -> SimpleNamespace:
            FilesystemMetadataStore(_isolate_vault_dir).update(SID, lambda d: {**d, "pending_transcription": second})
            return SimpleNamespace(
                ok=True, text="hola", error=None, provider="fake",
                duration_seconds=1.0, cost_usd_estimate=0.0, latency_ms=5,
            )

    class _Bus:
        async def record(self, event: Any) -> None:
            return None

    monkeypatch.setattr(audio_composition, "get_audio_transcription_port", lambda: _Port())
    monkeypatch.setattr(analytics, "get_event_bus", lambda: _Bus())

    await ActivityEnvironment().run(transcribe_audio_activity, SID)

    metadata = _read(path)
    assert metadata["pending_transcription"] == second
    assert metadata["recent_transcriptions"][-1]["media_id"] == "media-1"


def _hold_the_lock(vault: Path, mutator: Any) -> tuple[threading.Thread, threading.Event]:
    """Otro escritor (el aviso de entrega, un handoff) a mitad de un `update()`:
    leyó y sostiene el candado hasta que se le suelte."""
    inside = threading.Event()
    release = threading.Event()

    def slow(fresh: dict[str, Any]) -> dict[str, Any]:
        inside.set()
        release.wait(5)
        return mutator(fresh)

    writer = threading.Thread(target=FilesystemMetadataStore(vault).update, args=(SID, slow), daemon=True)
    writer.start()
    assert inside.wait(5)
    threading.Timer(0.3, release.set).start()
    return writer, release


@pytest.mark.asyncio
async def test_read_and_clear_waits_for_a_handoff_being_written(_isolate_vault_dir: Path) -> None:
    """El traspaso que se escribe mientras Ventas lo consume no se pierde ni
    se entrega dos veces: leer y limpiar es una sola operación con candado."""
    from src.plugins.chats.agent.sales.activities.bootstrap_session import read_and_clear_pending_handoff_activity

    path = _seed(_isolate_vault_dir, pending_handoff_summary="resumen 1")

    def append_handoff(fresh: dict[str, Any]) -> dict[str, Any]:
        fresh["pending_handoff_summary"] = f"{fresh['pending_handoff_summary']}\nresumen 2"
        return fresh

    writer, _ = _hold_the_lock(_isolate_vault_dir, append_handoff)
    summary = await ActivityEnvironment().run(read_and_clear_pending_handoff_activity, SID)
    writer.join(5)

    assert summary == "resumen 1\nresumen 2"
    assert "pending_handoff_summary" not in _read(path)


@pytest.mark.asyncio
async def test_operator_takeover_waits_for_a_write_in_progress(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El operador toma la conversación mientras otro escritor está a mitad de
    un `update()`: la toma no cae en medio (la ruta humana no se pierde) y lo
    del otro escritor tampoco."""
    from unittest.mock import AsyncMock

    import src.plugins.chats.api.handoff as handoff

    path = _seed(_isolate_vault_dir)
    monkeypatch.setattr(handoff, "get_temporal_client", AsyncMock(side_effect=RuntimeError("sin Temporal")))

    def delivery_status(fresh: dict[str, Any]) -> dict[str, Any]:
        fresh["last_delivery_status"] = "delivered"
        return fresh

    writer, _ = _hold_the_lock(_isolate_vault_dir, delivery_status)
    response = await handoff.intervene(
        SID, handoff.InterveneRequest(motivo="Lo atiendo yo"), FilesystemMetadataStore(_isolate_vault_dir)
    )
    writer.join(5)

    assert response.active_route == "humano"
    metadata = _read(path)
    assert (metadata["active_route"], metadata["tag"]) == ("humano", "HUMANO")
    assert metadata["last_delivery_status"] == "delivered"


# Sacar al bot de la conversación queda escrito aunque el documento esté
# dañado: el store se recupera solo (última copia buena o vacío) y escribe;
# si no, el operador vería «tomado» y el bot seguiría.

_BROKEN_JSON = '{"active_route": "ventas", "episodes": ['


@pytest.mark.asyncio
async def test_operator_takeover_over_a_damaged_document_still_stops_the_bot(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    import src.plugins.chats.api.handoff as handoff

    path = _isolate_vault_dir / SID / "metadata.json"
    path.parent.mkdir(parents=True)
    path.write_text(_BROKEN_JSON, encoding="utf-8")
    monkeypatch.setattr(handoff, "get_temporal_client", AsyncMock(side_effect=RuntimeError("sin Temporal")))

    await handoff.intervene(SID, handoff.InterveneRequest(motivo="Lo atiendo yo"), FilesystemMetadataStore(_isolate_vault_dir))

    assert _read(path)["active_route"] == "humano"


def test_a_payment_receipt_routes_to_human_over_a_damaged_document(_isolate_vault_dir: Path) -> None:
    from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import IngestInboundMessage

    path = _isolate_vault_dir / SID / "metadata.json"
    path.parent.mkdir(parents=True)
    path.write_text(_BROKEN_JSON, encoding="utf-8")
    ingest = IngestInboundMessage(
        history_store=None,  # type: ignore[arg-type]
        load_session=None,  # type: ignore[arg-type]
        metadata_store=FilesystemMetadataStore(_isolate_vault_dir),
    )

    ingest._route_to_human(
        session_id=SID, motivo="El cliente mandó un comprobante", reason_category="PAYMENT_VERIFICATION_PENDING"
    )

    assert _read(path)["active_route"] == "humano"
