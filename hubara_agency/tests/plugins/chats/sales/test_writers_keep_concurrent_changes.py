"""Las activities de ventas que esperan algo entre leer y escribir
`metadata.json` no pisan lo que otro escritor puso mientras tanto
(incidente 2026-10-06: una copia vieja devolvió una foto ya entregada a la cola).

  * `transcribe_audio_activity` espera la transcripción (segundos);
  * `ensure_promised_handoff_activity` espera al motor de decisiones (Jev).

Las dos escribían su copia ENTERA; ahora solo lo suyo, sobre lo fresco.
"""
from __future__ import annotations

import json
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
