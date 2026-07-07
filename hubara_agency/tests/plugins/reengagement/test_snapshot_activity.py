"""Tests de `build_reengagement_snapshot_activity` (WS-B4, Window Strategist).

El snapshot es el seed que hubara deposita al agente: `now_ms` + por
conversación las ventanas CRUDAS + el LeadState PRE-DIGERIDO (via
sdk.messagingkit — la derivación vive UNA vez) + los toques recientes. El
seam es activity-side (NUNCA HTTP: la API prod está sin auth).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

from src.plugins.reengagement.agent.cycle.activities import (
    build_reengagement_snapshot_activity,
)


def _seed(vault: Path, session_id: str, data: dict) -> None:
    d = vault / session_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.asyncio
async def test_snapshot_incluye_ventanas_y_lead_predigerido(
    _isolate_vault_dir: Path,
):
    _seed(
        _isolate_vault_dir,
        "wa_573001",
        {
            "tag": "INTERESADO",
            "service_window_expires_at_ms": 111,
            "ctwa_window_expires_at_ms": 222,
            "last_inbound_at_ms": 100,
            "ctwa_clids_seen": ["clid_x"],
            "episodes": [
                {
                    "episode_id": "ep_001",
                    "closed_at_ms": None,
                    "order_draft": {"slots": {"producto": "camisa"}},
                    "outbound_messages": [
                        {"sent_at_ms": 90, "kind": "text"},
                    ],
                }
            ],
        },
    )
    snapshot = await ActivityEnvironment().run(
        build_reengagement_snapshot_activity
    )
    assert isinstance(snapshot["now_ms"], int) and snapshot["now_ms"] > 0
    (convo,) = snapshot["conversations"]
    assert convo["session_id"] == "wa_573001"
    assert convo["service_window_expires_at_ms"] == 111
    assert convo["ctwa_window_expires_at_ms"] == 222
    lead = convo["lead"]
    assert lead["tag"] == "INTERESADO"
    assert lead["has_order_draft"] is True
    assert lead["is_ctwa_lead"] is True
    assert convo["recent_touches"] == [{"at_ms": 90, "kind": "text"}]


@pytest.mark.asyncio
async def test_snapshot_ignora_sesiones_sin_actividad_whatsapp(
    _isolate_vault_dir: Path,
):
    # Sin last_inbound ni ventanas → no es una conversación reactivable; y
    # los directorios no-sesión (p.ej. _analytics) se saltan.
    _seed(_isolate_vault_dir, "wa_573002", {"phone_number_id": "X"})
    (_isolate_vault_dir / "_analytics").mkdir()
    snapshot = await ActivityEnvironment().run(
        build_reengagement_snapshot_activity
    )
    assert snapshot["conversations"] == []
