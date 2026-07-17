"""El snapshot builder usa el índice como SHORTLIST (Punto 2, escala).

Con índice presente: solo abre el metadata.json de los candidatos (ventana
abierta / gancho / entrada joven) — el frío+viejo masivo ni se lee. Sin
índice: full scan que además lo RECONSTRUYE. Toda entrada leída se refresca.
El índice nunca decide: la decisión real es del pre-filtro sobre metadata
real + el gate al ejecutar.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

from src.plugins.reengagement.agent.cycle.activities import (
    build_reengagement_snapshot_activity,
)
from src.sdk.messagingkit import (
    load_reengagement_index,
    update_reengagement_index_entry,
)

HOUR = 60 * 60 * 1000
DAY = 24 * HOUR


def _seed(vault: Path, session_id: str, data: dict) -> None:
    d = vault / session_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(json.dumps(data), encoding="utf-8")


def _open_metadata(now_ms: int) -> dict:
    # Lead dormido (5h de silencio) con CSW aún abierta — pasa el pre-filtro
    # de dormancia y el de la central.
    return {
        "tag": "INTERESADO",
        "service_window_expires_at_ms": now_ms + HOUR,
        "last_inbound_at_ms": now_ms - 5 * HOUR,
    }


@pytest.mark.asyncio
async def test_con_indice_solo_lee_los_candidatos(_isolate_vault_dir: Path):
    now_ms = int(time.time() * 1000)
    # Ambas sesiones tienen metadata REACTIVABLE (CSW abierta) — pero el
    # índice dice que wa_b está fría y vieja → shortlist la excluye SIN
    # leer su metadata (eso es exactamente el ahorro O(accionable)).
    _seed(_isolate_vault_dir, "wa_a", _open_metadata(now_ms))
    _seed(_isolate_vault_dir, "wa_b", _open_metadata(now_ms))
    update_reengagement_index_entry(
        _isolate_vault_dir, "wa_a", _open_metadata(now_ms), now_ms=now_ms
    )
    update_reengagement_index_entry(
        _isolate_vault_dir,
        "wa_b",
        {
            "tag": "NO_ETIQUETADO",
            "service_window_expires_at_ms": now_ms - 10 * DAY,
            "last_inbound_at_ms": now_ms - 10 * DAY,
        },
        now_ms=now_ms - 10 * DAY,
    )

    snapshot = await ActivityEnvironment().run(build_reengagement_snapshot_activity)

    ids = [c["session_id"] for c in snapshot["conversations"]]
    assert ids == ["wa_a"], ids
    assert snapshot["shortlisted"] == 1
    assert snapshot["index_size"] == 2


@pytest.mark.asyncio
async def test_sin_indice_full_scan_y_lo_reconstruye(_isolate_vault_dir: Path):
    now_ms = int(time.time() * 1000)
    _seed(_isolate_vault_dir, "wa_a", _open_metadata(now_ms))
    _seed(_isolate_vault_dir, "wa_b", _open_metadata(now_ms))

    snapshot = await ActivityEnvironment().run(build_reengagement_snapshot_activity)

    ids = sorted(c["session_id"] for c in snapshot["conversations"])
    assert ids == ["wa_a", "wa_b"], ids
    index = load_reengagement_index(_isolate_vault_dir)
    assert index is not None and set(index) == {"wa_a", "wa_b"}, (
        "el full scan debe RECONSTRUIR el índice"
    )


@pytest.mark.asyncio
async def test_refresca_las_entradas_que_lee(_isolate_vault_dir: Path):
    now_ms = int(time.time() * 1000)
    stale_ms = now_ms - HOUR
    _seed(_isolate_vault_dir, "wa_a", _open_metadata(now_ms))
    update_reengagement_index_entry(
        _isolate_vault_dir, "wa_a", _open_metadata(stale_ms), now_ms=stale_ms
    )

    await ActivityEnvironment().run(build_reengagement_snapshot_activity)

    entry = load_reengagement_index(_isolate_vault_dir)["wa_a"]
    assert entry["updated_at_ms"] >= now_ms, "toda entrada leída se refresca"
