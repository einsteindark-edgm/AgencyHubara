"""Tests del fingerprint free-form (WS-B3, plan Window Strategist).

El guard anti doble-toque CROSS-RUN que faltaba: el `event_id` del bridge solo
dedupea eventos dentro de un run, y el workflow-id determinista no cubre dos
barridos distintos del agente. El fingerprint del contenido (simétrico al
`_template_fingerprint` existente) es la última línea de defensa — a partir
del 1-oct-2026 un free-form duplicado es PLATA, no solo spam.

También cierra un hueco de observabilidad: el path free-form no persistía
`last_outbound` ni el OutboundLogEntry del episodio (solo el template path lo
hacía) — sin eso, ni `engaged` (lead_state) ni el cost tracking ven los
free-form del bot.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from src.platform.whatsapp import activities as wa_activities
from src.platform.whatsapp.activities import send_message_to_session

SESSION = "wa_573001110000"


@pytest.fixture(autouse=True)
def _fast_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "PHONE_TEST")

    async def _no_sleep(_secs):
        return None

    monkeypatch.setattr(wa_activities.asyncio, "sleep", _no_sleep)


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def fake_send(phone_number_id: str, to: str, text: str) -> None:
        calls.append(text)

    monkeypatch.setattr(wa_activities.whatsapp_client, "send_message", fake_send)
    return calls


def _seed_metadata(vault: Path) -> None:
    d = vault / SESSION
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(
        json.dumps(
            {
                "phone_number_id": "PHONE_TEST",
                "episodes": [
                    {"episode_id": "ep_001", "closed_at_ms": None},
                ],
            }
        ),
        encoding="utf-8",
    )


def _read_metadata(vault: Path) -> dict:
    return json.loads(
        (vault / SESSION / "metadata.json").read_text(encoding="utf-8")
    )


@pytest.mark.asyncio
async def test_duplicate_freeform_within_window_is_skipped(
    _isolate_vault_dir: Path, sent: list[str]
):
    _seed_metadata(_isolate_vault_dir)
    await send_message_to_session(SESSION, "¿Seguimos con tu pedido?")
    await send_message_to_session(SESSION, "¿Seguimos con tu pedido?")
    assert sent == ["¿Seguimos con tu pedido?"], (
        "el retry idéntico dentro de la ventana debe dedupearse (post 1-oct es plata)"
    )


@pytest.mark.asyncio
async def test_different_message_is_not_deduped(
    _isolate_vault_dir: Path, sent: list[str]
):
    _seed_metadata(_isolate_vault_dir)
    await send_message_to_session(SESSION, "Hola")
    await send_message_to_session(SESSION, "¿Sigues ahí?")
    assert sent == ["Hola", "¿Sigues ahí?"]


@pytest.mark.asyncio
async def test_freeform_send_persists_outbound_log(
    _isolate_vault_dir: Path, sent: list[str]
):
    _seed_metadata(_isolate_vault_dir)
    before_ms = int(time.time() * 1000)
    await send_message_to_session(SESSION, "Hola")
    meta = _read_metadata(_isolate_vault_dir)
    last = meta.get("last_outbound")
    assert last is not None, "el free-form debe persistir last_outbound"
    assert last["kind"] == "text"
    assert last["sent_at_ms"] >= before_ms
    episode = meta["episodes"][-1]
    assert len(episode.get("outbound_messages") or []) == 1


@pytest.mark.asyncio
async def test_stale_fingerprint_outside_window_resends(
    _isolate_vault_dir: Path, sent: list[str], monkeypatch: pytest.MonkeyPatch
):
    _seed_metadata(_isolate_vault_dir)
    await send_message_to_session(SESSION, "Recordatorio")
    # Simular que pasó más que la ventana de dedup: envejecer la marca.
    meta = _read_metadata(_isolate_vault_dir)
    for entry in meta["recent_freeform_sends"]:
        entry["sent_at_ms"] -= 10 * 60 * 1000
    (_isolate_vault_dir / SESSION / "metadata.json").write_text(
        json.dumps(meta), encoding="utf-8"
    )
    await send_message_to_session(SESSION, "Recordatorio")
    assert sent == ["Recordatorio", "Recordatorio"], (
        "un reenvío legítimo posterior (fuera de la ventana) NO debe bloquearse"
    )
