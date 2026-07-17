"""Tests para `read_idle_timeout_seconds_activity` — sesión c4e3416f.

El timeout normal de ghosting Sales son 300s (5 min — subido de 60s tras el
run 48ec6df5: la ventana de 1 min declaraba ghosting a clientes que tardaban
poco más de un minuto en tocar un botón, creando carreras ghost↔respuesta).
Cuando recién enviamos un WhatsApp Flow nativo, el cliente necesita 1-3
minutos para llenar el form. Si ghosting se dispara mientras tanto, arranca
remarketing → claim_routing pisa active_route → el nfm_reply se rutea a
remarketing y el cliente queda atascado.

Esta activity lee el flag `shipping_flow_awaiting_reply_since_ms` que
`flush_pending_ui_intents_activity` escribe al dispatchear el Flow, y
devuelve un timeout extendido (hasta 10 min) mientras el flag esté reciente.
"""
from __future__ import annotations

import json
import time

import pytest

from src.plugins.chats.agent.sales.activities.bootstrap_session import (
    read_idle_timeout_seconds_activity,
)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """Override WORKSPACE_VAULT_DIR para tests aislados."""
    monkeypatch.setattr(
        "src.plugins.chats.agent.sales.activities.bootstrap_session.WORKSPACE_VAULT_DIR",
        tmp_path,
    )
    return tmp_path


def _write_metadata(vault, session_id: str, data: dict) -> None:
    session_dir = vault / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "metadata.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_default_300s_when_no_metadata_file(vault):
    """Sesión nueva sin metadata.json → 300s normal."""
    result = await read_idle_timeout_seconds_activity("wa_nonexistent")
    assert result == 300


@pytest.mark.asyncio
async def test_default_300s_when_no_flag(vault):
    """Metadata existe pero sin el flag → 300s normal."""
    _write_metadata(vault, "wa_session_a", {"some": "field"})
    result = await read_idle_timeout_seconds_activity("wa_session_a")
    assert result == 300


@pytest.mark.asyncio
async def test_default_300s_when_flag_is_not_int(vault):
    """Flag con tipo inválido (string, None) → 300s normal — defensivo."""
    _write_metadata(
        vault, "wa_session_b", {"shipping_flow_awaiting_reply_since_ms": "abc"}
    )
    result = await read_idle_timeout_seconds_activity("wa_session_b")
    assert result == 300


@pytest.mark.asyncio
async def test_default_300s_when_flag_expired(vault):
    """Flag con > 10 min de antigüedad → 300s (cliente ya no completó)."""
    stale_ms = int(time.time() * 1000) - (11 * 60 * 1000)  # 11 min atrás
    _write_metadata(
        vault, "wa_stale", {"shipping_flow_awaiting_reply_since_ms": stale_ms}
    )
    result = await read_idle_timeout_seconds_activity("wa_stale")
    assert result == 300


@pytest.mark.asyncio
async def test_default_300s_when_flag_in_future(vault):
    """Flag con timestamp futuro (clock skew, bug) → 300s normal — defensivo."""
    future_ms = int(time.time() * 1000) + 60_000  # 1 min en el futuro
    _write_metadata(
        vault,
        "wa_future",
        {"shipping_flow_awaiting_reply_since_ms": future_ms},
    )
    result = await read_idle_timeout_seconds_activity("wa_future")
    assert result == 300


@pytest.mark.asyncio
async def test_extended_timeout_when_flag_recent(vault):
    """Flag reciente (1 min atrás) → ~9 min restantes hasta el cap de 10 min."""
    one_min_ago = int(time.time() * 1000) - 60_000
    _write_metadata(
        vault,
        "wa_pending",
        {"shipping_flow_awaiting_reply_since_ms": one_min_ago},
    )
    result = await read_idle_timeout_seconds_activity("wa_pending")
    # Restante esperado: 600 - 60 = 540s (± tolerancia de scheduling)
    assert 530 <= result <= 600


@pytest.mark.asyncio
async def test_extended_timeout_floor_is_300s_even_near_cap(vault):
    """Si el flag está a 9.99 min de antigüedad (filo del cap), igual
    devolver mínimo 300s — el workflow no debe quedarse con timeouts de 1s
    que crean spam de wait_condition."""
    near_cap_ms = int(time.time() * 1000) - (9 * 60 * 1000 + 59_000)  # 9m59s
    _write_metadata(
        vault,
        "wa_edge",
        {"shipping_flow_awaiting_reply_since_ms": near_cap_ms},
    )
    result = await read_idle_timeout_seconds_activity("wa_edge")
    assert result == 300  # piso mínimo aplicado


@pytest.mark.asyncio
async def test_returns_int_not_float(vault):
    """El workflow espera `int` para construir `timedelta(seconds=...)`."""
    half_min_ago = int(time.time() * 1000) - 30_000
    _write_metadata(
        vault,
        "wa_int_check",
        {"shipping_flow_awaiting_reply_since_ms": half_min_ago},
    )
    result = await read_idle_timeout_seconds_activity("wa_int_check")
    assert isinstance(result, int)


@pytest.mark.asyncio
async def test_bad_metadata_json_returns_default(vault):
    """metadata.json corrupto → 300s normal, no crash."""
    session_dir = vault / "wa_corrupt"
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "metadata.json").write_text(
        "{ corrupt json no comma }", encoding="utf-8"
    )
    result = await read_idle_timeout_seconds_activity("wa_corrupt")
    assert result == 300
