"""La CITA: el día que el cliente dijo, le escribimos UNA vez.

Decisión del operador (2026-09-22, tras el incidente runs 337efe8c / ee3cec91):
«les escribo la otra semana» pausa la escalera hasta esa fecha (commit previo)
y ESE día sale un solo toque — `followup_interest_marketing_v1`, aunque sea una
plantilla de marketing PAGADA (a una semana las ventanas gratis ya cerraron).
Un toque, no la escalera: si no contesta, `SIN_RESPUESTA` y no se insiste.
Solo para aplazamientos CON FECHA; "yo les escribo cuando…" no tiene cita.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

from src.platform.whatsapp.activities import (
    check_reengagement_policy_activity,
    record_remarketing_touch_activity,
)
from src.platform.whatsapp.composition import get_current_rate_card
from src.platform.whatsapp.reengagement_deferral import (
    DEFERRAL_KEY,
    DEFERRAL_KIND_DATED,
    DEFERRAL_KIND_OPEN,
    appointment_pending,
)
from src.platform.whatsapp.reengagement_index import (
    index_entry_from_metadata,
    shortlist_session_ids,
)
from src.platform.whatsapp.send_policy import (
    decide_reengagement,
    lead_state_from_metadata,
)
from src.plugins.chats.agent.remarketing.activities.ladder_template import (
    TEMPLATE_FOLLOWUP,
    choose_ladder_template,
)
from src.plugins.reengagement.agent.cycle.use_cases import (
    build_snapshot_from_sessions,
)
from src.plugins.reengagement.agent.cycle.use_cases.unresponsive import (
    unresponsive_session_ids,
)

H = 60 * 60 * 1000
DAY = 24 * H
SESSION = "wa_573001234567"


def _cold_lead_with_appointment(now_ms: int, *, kind: str = DEFERRAL_KIND_DATED) -> dict:
    """Lead de hace una semana: aplazó con fecha, la fecha ya llegó, las
    ventanas gratis (24h y 72h del anuncio) cerraron hace días."""
    said_at = now_ms - 7 * DAY
    return {
        "tag": "INTERESADO",
        "last_inbound_at_ms": said_at,
        "service_window_expires_at_ms": said_at + DAY,
        "ctwa_window_expires_at_ms": said_at + 3 * DAY,
        "episodes": [{"episode_id": "ep_001", "closed_at_ms": None}],
        DEFERRAL_KEY: {
            "at_ms": said_at,
            "until_ms": now_ms - H,
            "kind": kind,
            "text": "les escribo la otra semana",
        },
    }


# ---------------------------------------------------------------------------
# La cita habilita UN marketing pago (la central y su espejo)
# ---------------------------------------------------------------------------


def test_un_aplazamiento_con_fecha_es_una_cita_pendiente():
    now_ms = 1_800_000_000_000
    assert appointment_pending(_cold_lead_with_appointment(now_ms)) is True
    assert (
        appointment_pending(_cold_lead_with_appointment(now_ms, kind=DEFERRAL_KIND_OPEN))
        is False
    )


def test_la_central_paga_una_plantilla_de_marketing_por_la_cita():
    now_ms = int(time.time() * 1000)
    meta = _cold_lead_with_appointment(now_ms)
    decision = decide_reengagement(
        now_ms, meta, lead_state_from_metadata(meta), get_current_rate_card(now_ms)
    )
    assert decision.allowed is True
    assert decision.channel == "template"
    assert decision.recommended_category == "marketing"


def test_sin_fecha_no_hay_cita_y_el_lead_frio_sigue_suprimido():
    now_ms = int(time.time() * 1000)
    meta = _cold_lead_with_appointment(now_ms, kind=DEFERRAL_KIND_OPEN)
    decision = decide_reengagement(
        now_ms, meta, lead_state_from_metadata(meta), get_current_rate_card(now_ms)
    )
    assert decision.suppress_reason == "fase_b_cold_suppressed"


def test_la_cita_usa_el_seguimiento_generico_aunque_sea_pagado():
    meta = _cold_lead_with_appointment(1_800_000_000_000)
    meta["episodes"][0]["order_draft"] = {"slots": {"producto": "Vela Calabaza"}}
    assert choose_ladder_template(meta, is_free=False) == (TEMPLATE_FOLLOWUP, {})


# ---------------------------------------------------------------------------
# El ciclo la encuentra aunque el lead sea viejo
# ---------------------------------------------------------------------------


def test_el_indice_shortlistea_la_cita_vencida_de_un_lead_viejo():
    now_ms = 1_800_000_000_000
    meta = _cold_lead_with_appointment(now_ms)
    entry = index_entry_from_metadata(meta, now_ms=now_ms - 7 * DAY)
    assert shortlist_session_ids({SESSION: entry}, now_ms=now_ms) == [SESSION]


def test_el_snapshot_deja_pasar_la_cita_con_la_central():
    now_ms = int(time.time() * 1000)
    meta = _cold_lead_with_appointment(now_ms)
    snapshot = build_snapshot_from_sessions(
        now_ms, [(SESSION, meta)], rate_card=get_current_rate_card(now_ms)
    )
    assert [c["session_id"] for c in snapshot["conversations"]] == [SESSION]
    # El espejo de GraphAgents (parse_conversations) decide el pago con este flag.
    assert snapshot["conversations"][0]["lead"]["allow_paid_marketing"] is True


# ---------------------------------------------------------------------------
# UN toque: el primero después de la fecha consume la cita
# ---------------------------------------------------------------------------


def _write(vault: Path, data: dict) -> None:
    d = vault / SESSION
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(json.dumps(data), encoding="utf-8")


def _read(vault: Path) -> dict:
    return json.loads((vault / SESSION / "metadata.json").read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_el_gate_deja_salir_la_cita_y_despues_no_insiste(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("WATCHDOG_QUIET_HOURS_START", "0")
    monkeypatch.setenv("WATCHDOG_QUIET_HOURS_END", "24")
    now_ms = int(time.time() * 1000)
    _write(_isolate_vault_dir, _cold_lead_with_appointment(now_ms))
    env = ActivityEnvironment()

    first = await env.run(check_reengagement_policy_activity, SESSION)
    assert first.allowed is True
    assert first.channel == "template"
    assert first.is_free is False

    await env.run(record_remarketing_touch_activity, SESSION, "template")
    assert appointment_pending(_read(_isolate_vault_dir)) is False

    second = await env.run(check_reengagement_policy_activity, SESSION)
    assert second.allowed is False


def test_sin_respuesta_a_la_cita_queda_sin_respuesta():
    now_ms = 1_800_000_000_000
    meta = _cold_lead_with_appointment(now_ms)
    meta["remarketing_touches"] = [{"at_ms": now_ms - 7 * H, "kind": "template"}]
    meta[DEFERRAL_KEY]["appointment_touched_at_ms"] = now_ms - 7 * H
    assert unresponsive_session_ids(now_ms, [(SESSION, meta)]) == [SESSION]
