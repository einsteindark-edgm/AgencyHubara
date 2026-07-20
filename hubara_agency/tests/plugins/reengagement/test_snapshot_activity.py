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
                        # Proactivo: enviado bien después del último inbound
                        # (100) + gap — las réplicas conversacionales no viajan.
                        {"sent_at_ms": 700_000, "kind": "text"},
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
    # El cierre del último episodio viaja pre-digerido (supresión
    # already_purchased — el espejo de la caja no re-deriva). Episodio
    # abierto → None.
    assert lead["last_closing_tag"] is None
    assert convo["recent_touches"] == [{"at_ms": 700_000, "kind": "text"}]


@pytest.mark.asyncio
async def test_recent_touches_solo_cuenta_toques_proactivos(
    _isolate_vault_dir: Path,
):
    """Post-mortem run 019f6d0d (2026-07-16): las réplicas conversacionales del
    bot contaban como "toques" → cadence_cap suprimía a TODO lead con una
    conversación real en 24h, justo mientras la ventana gratis estaba abierta
    (csw_free_form era código muerto en la práctica). Un toque es PROACTIVO
    solo si fue enviado al silencio: después del último inbound + gap. Los
    outbounds anteriores al último inbound fueron, por definición, respondidos.
    """
    import time

    now_ms = int(time.time() * 1000)
    hour = 60 * 60 * 1000
    last_inbound = now_ms - 6 * hour
    _seed(
        _isolate_vault_dir,
        "wa_573003",
        {
            "tag": "INTERESADO",
            "service_window_expires_at_ms": last_inbound + 24 * hour,
            "last_inbound_at_ms": last_inbound,
            "episodes": [
                {
                    "episode_id": "ep_001",
                    "closed_at_ms": None,
                    "outbound_messages": [
                        # Réplica ANTES del último inbound → conversacional.
                        {"sent_at_ms": last_inbound - 2 * 60 * 1000, "kind": "text"},
                        # Réplica 13s después del inbound (dentro del gap) →
                        # conversacional.
                        {"sent_at_ms": last_inbound + 13_000, "kind": "text"},
                        # Nudge 2h después del inbound (silencio) → PROACTIVO.
                        {"sent_at_ms": last_inbound + 2 * hour, "kind": "text"},
                    ],
                }
            ],
        },
    )
    snapshot = await ActivityEnvironment().run(
        build_reengagement_snapshot_activity
    )
    (convo,) = snapshot["conversations"]
    assert convo["recent_touches"] == [
        {"at_ms": last_inbound + 2 * hour, "kind": "text"}
    ], convo["recent_touches"]


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


@pytest.mark.asyncio
async def test_dispatch_envuelve_el_snapshot_en_payload(monkeypatch):
    # Run prod ce80f73f (2026-07-10): el nodo ingest falló con "falta 'payload'"
    # — la activity mandaba el snapshot CRUDO, pero el contrato del manifest
    # (inputs: payload) y el case window-strategist-ciclo esperan
    # {"payload": snapshot}. El seam debe envolver.
    from src.plugins.reengagement.agent.cycle import activities as acts

    seen: dict = {}

    class FakeLauncher:
        def start_box(self):
            seen["box"] = True

        def dispatch(self, agent, input, *, run_id):
            seen["agent"] = agent
            seen["input"] = input
            return "eid-1"

    monkeypatch.setattr(acts, "get_launcher", lambda: FakeLauncher())
    snapshot = {"schema_version": 1, "now_ms": 5, "conversations": []}
    eid = await ActivityEnvironment().run(
        acts.dispatch_window_strategist_activity, snapshot, "run-x"
    )
    assert eid == "eid-1"
    assert seen["agent"] == "window-strategist"
    assert seen["input"] == {"payload": snapshot}, seen["input"]


@pytest.mark.asyncio
async def test_poll_fallido_despierta_la_caja_y_reintenta(monkeypatch):
    """Eslabón 9 de la cadena dispatch (PR #153, memoria graphagents-dispatch-
    prod-chain): el autostop puede apagar la caja con el resultado sin
    cosechar — un fetch_status fallido debe DESPERTARLA (start_box) antes de
    dejar que Temporal reintente, o el run queda inalcanzable para siempre."""
    from src.plugins.reengagement.agent.cycle import activities as acts

    calls: dict = {"box": 0}

    class DyingLauncher:
        def start_box(self):
            calls["box"] += 1

        def fetch_status(self, execution_id):
            raise RuntimeError("InvalidInstanceId: la caja está stopped")

    monkeypatch.setattr(acts, "get_launcher", lambda: DyingLauncher())
    with pytest.raises(RuntimeError):
        await ActivityEnvironment().run(acts.poll_window_strategist_activity, "eid-9")
    assert calls["box"] == 1, "el poll fallido debe despertar la caja (eslabón 9)"
