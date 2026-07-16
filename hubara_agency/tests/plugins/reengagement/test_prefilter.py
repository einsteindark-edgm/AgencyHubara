"""Pre-filtro con la CENTRAL en el snapshot builder (Punto 1, escala).

Clasificar millones de leads fríos en la caja para suprimirlos es pagar
cómputo por un "no" que hubara ya sabe. El builder corre decide_reengagement
(via sdk.messagingkit — la MISMA autoridad del gate, no un espejo) y excluye
del seed lo que la central suprime, con contador VISIBLE por razón (no
silent caps). El agente sigue suprimiendo en classify (defensa en
profundidad) y el gate re-valida al ejecutar.
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

ONE_HOUR_MS = 60 * 60 * 1000


def _seed(vault: Path, session_id: str, data: dict) -> None:
    d = vault / session_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.asyncio
async def test_prefiltra_lo_que_la_central_suprime(_isolate_vault_dir: Path):
    now_ms = int(time.time() * 1000)
    on, off = now_ms + ONE_HOUR_MS, now_ms - ONE_HOUR_MS
    dormant = now_ms - 5 * ONE_HOUR_MS  # silencio > umbral de dormancia
    # 1. CSW abierta + lead dormido → ENTRA (la central permite free_form)
    _seed(_isolate_vault_dir, "wa_open", {
        "tag": "INTERESADO",
        "service_window_expires_at_ms": on,
        "ctwa_window_expires_at_ms": off,
        "last_inbound_at_ms": dormant,
    })
    # 2. HUMANO → AFUERA (terminal — hoy viajaba a la caja para nada)
    _seed(_isolate_vault_dir, "wa_human", {
        "tag": "HUMANO",
        "service_window_expires_at_ms": on,
        "ctwa_window_expires_at_ms": on,
        "last_inbound_at_ms": dormant,
    })
    # 3. Fase B fría (todo expirado, sin gancho, sin opt-in) → AFUERA
    _seed(_isolate_vault_dir, "wa_cold", {
        "tag": "NO_ETIQUETADO",
        "service_window_expires_at_ms": off,
        "ctwa_window_expires_at_ms": off,
        "last_inbound_at_ms": now_ms - 30 * ONE_HOUR_MS,
    })
    # 4. Fase B con gancho (expirado PERO pago pendiente) → ENTRA (utility)
    _seed(_isolate_vault_dir, "wa_hook", {
        "tag": "CONFIRMADO_PAGO_PENDIENTE",
        "service_window_expires_at_ms": off,
        "ctwa_window_expires_at_ms": off,
        "last_inbound_at_ms": now_ms - 30 * ONE_HOUR_MS,
        "episodes": [{"episode_id": "ep_001", "closed_at_ms": off, "order_id": "o1"}],
    })

    snapshot = await ActivityEnvironment().run(build_reengagement_snapshot_activity)

    ids = sorted(c["session_id"] for c in snapshot["conversations"])
    assert ids == ["wa_hook", "wa_open"], ids
    assert snapshot["prefiltered"] == {
        "human_owned": 1,
        "fase_b_cold_suppressed": 1,
    }, snapshot.get("prefiltered")


@pytest.mark.asyncio
async def test_prefiltered_ausente_cuando_no_se_excluye_nada(
    _isolate_vault_dir: Path,
):
    now_ms = int(time.time() * 1000)
    _seed(_isolate_vault_dir, "wa_solo", {
        "tag": "INTERESADO",
        "service_window_expires_at_ms": now_ms + ONE_HOUR_MS,
        "last_inbound_at_ms": now_ms - 5 * ONE_HOUR_MS,
    })
    snapshot = await ActivityEnvironment().run(build_reengagement_snapshot_activity)
    assert len(snapshot["conversations"]) == 1
    assert snapshot["prefiltered"] == {}


@pytest.mark.asyncio
async def test_prefiltra_conversacion_activa(_isolate_vault_dir: Path):
    """Dormancia (post-mortem run 019f6d0d, 2026-07-16): una conversación con
    inbound hace minutos NO es candidata a reengagement — ventas está en plena
    charla. Sin este filtro, el único freno era el cadence_cap por accidente.
    La exclusión es VISIBLE (`prefiltered.conversation_active`, no silent cap).
    """
    now_ms = int(time.time() * 1000)
    on = now_ms + 20 * ONE_HOUR_MS
    # Activa: el cliente escribió hace 4 minutos → AFUERA del seed.
    _seed(_isolate_vault_dir, "wa_activa", {
        "tag": "INTERESADO",
        "service_window_expires_at_ms": on,
        "last_inbound_at_ms": now_ms - 4 * 60 * 1000,
    })
    # Dormida: 5h de silencio, CSW aún abierta → ENTRA (el caso que el
    # Window Strategist existe para rescatar).
    _seed(_isolate_vault_dir, "wa_dormida", {
        "tag": "INTERESADO",
        "service_window_expires_at_ms": on,
        "last_inbound_at_ms": now_ms - 5 * ONE_HOUR_MS,
    })
    # Sin inbound registrado (nunca escribió): la dormancia no aplica — decide
    # la central (acá fase B con gancho para que entre).
    _seed(_isolate_vault_dir, "wa_sin_inbound", {
        "tag": "CONFIRMADO_PAGO_PENDIENTE",
        "service_window_expires_at_ms": now_ms - ONE_HOUR_MS,
        "episodes": [
            {"episode_id": "ep_001", "closed_at_ms": None, "order_id": "o1"}
        ],
    })

    snapshot = await ActivityEnvironment().run(build_reengagement_snapshot_activity)

    ids = sorted(c["session_id"] for c in snapshot["conversations"])
    assert ids == ["wa_dormida", "wa_sin_inbound"], ids
    assert snapshot["prefiltered"] == {"conversation_active": 1}, (
        snapshot.get("prefiltered")
    )
