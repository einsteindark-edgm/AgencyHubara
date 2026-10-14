"""La baja que prometen las plantillas de la escalera SE CUMPLE.

Hallazgo H-2 de la revisión: `followup_interest_marketing_v1` y
`cart_recovery_marketing_v2` dicen «respóndeme "NO MÁS" y te doy de baja», pero
el detector solo reconocía la baja tras una CAMPAÑA (`campaign_touches`) y la
escalera ni siquiera leía `marketing_opt_out`: el "NO MÁS" re-anclaba la
escalera y a las 2h salía otro toque. Determinista, sin LLM.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

from src.platform.whatsapp.activities import check_reengagement_policy_activity
from src.platform.whatsapp.marketing_opt_out import detect_marketing_opt_out
from src.plugins.reengagement.agent.cycle.use_cases import (
    build_snapshot_from_sessions,
)

H = 60 * 60 * 1000
NOW = 1_800_000_000_000


def test_no_mas_tras_una_plantilla_de_la_escalera_es_baja():
    meta = {"remarketing_touches": [{"at_ms": NOW - 3 * H, "kind": "template"}]}
    assert detect_marketing_opt_out("NO MÁS", meta, NOW) is True


def test_no_mas_tras_un_gancho_free_form_sigue_siendo_charla_normal():
    # El free-form no promete baja: "no más velas por ahora" es conversación.
    meta = {"remarketing_touches": [{"at_ms": NOW - 3 * H, "kind": "free_form"}]}
    assert detect_marketing_opt_out("no más por ahora gracias", meta, NOW) is False


def test_plantilla_vieja_ya_no_da_contexto_de_baja():
    meta = {"remarketing_touches": [{"at_ms": NOW - 8 * 24 * H, "kind": "template"}]}
    assert detect_marketing_opt_out("NO MÁS", meta, NOW) is False


def test_el_prefiltro_del_ciclo_excluye_a_quien_pidio_la_baja():
    meta = {
        "tag": "INTERESADO",
        "marketing_opt_out": True,
        "last_inbound_at_ms": NOW - 3 * H,
        "service_window_expires_at_ms": NOW + 21 * H,
    }
    snapshot = build_snapshot_from_sessions(NOW, [("wa_baja", meta)])
    assert snapshot["conversations"] == []
    assert snapshot["prefiltered"] == {"marketing_opt_out": 1}


@pytest.mark.asyncio
async def test_el_gate_suprime_a_quien_pidio_la_baja(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("WATCHDOG_QUIET_HOURS_START", "0")
    monkeypatch.setenv("WATCHDOG_QUIET_HOURS_END", "24")
    now_ms = int(time.time() * 1000)
    d = _isolate_vault_dir / "wa_573001234567"
    d.mkdir(parents=True)
    (d / "metadata.json").write_text(json.dumps({
        "tag": "INTERESADO",
        "marketing_opt_out": True,
        "last_inbound_at_ms": now_ms - 3 * H,
        "service_window_expires_at_ms": now_ms + 21 * H,
    }), encoding="utf-8")
    decision = await ActivityEnvironment().run(
        check_reengagement_policy_activity, "wa_573001234567"
    )
    assert decision.allowed is False
    assert decision.suppress_reason == "marketing_opt_out"


def test_no_mas_tras_la_plantilla_de_calificacion_es_baja():
    """Reseñas (2026-10-13): la invitación a calificar es MARKETING (dice el
    descuento) y promete la baja; un «NO MÁS» después la registra."""
    meta = {"review_request_touches": [{"at_ms": NOW - 3 * H, "kind": "template"}]}
    assert detect_marketing_opt_out("NO MÁS", meta, NOW) is True


def test_no_mas_tras_la_invitacion_dentro_de_la_ventana_no_es_baja():
    meta = {"review_request_touches": [{"at_ms": NOW - 3 * H, "kind": "flow"}]}
    assert detect_marketing_opt_out("NO MÁS", meta, NOW) is False


# --- Premortem C4: tras la invitación a calificar, solo la baja explícita -----


@pytest.mark.parametrize("text", ["NO MÁS", "No más 🙏", "no mas, gracias", "Baja", "STOP", "No me escribas más"])
def test_tras_la_calificacion_la_frase_sola_sigue_siendo_baja(text: str) -> None:
    meta = {"review_request_touches": [{"at_ms": NOW - 3 * H, "kind": "template"}]}
    assert detect_marketing_opt_out(text, meta, NOW) is True


@pytest.mark.parametrize(
    "text",
    [
        "quiero otra igual, no más que en azul",
        "Me encantó, no más llegó la caja la abrí",
        "No más de 50 mil, ¿qué me recomiendas?",
    ],
)
def test_tras_la_calificacion_una_frase_dentro_de_la_charla_no_es_baja(text: str) -> None:
    """«no más» dentro de un mensaje de compra no es una baja pegajosa."""
    meta = {"review_request_touches": [{"at_ms": NOW - 3 * H, "kind": "template"}]}
    assert detect_marketing_opt_out(text, meta, NOW) is False


def test_la_invitacion_a_calificar_da_contexto_de_baja_solo_48_horas() -> None:
    meta = {"review_request_touches": [{"at_ms": NOW - 49 * H, "kind": "template"}]}
    assert detect_marketing_opt_out("NO MÁS", meta, NOW) is False


def test_las_campanas_siguen_igual_con_no_mas_dentro_del_mensaje() -> None:
    """Solo cambia el contexto de la calificación: tras una plantilla de la
    escalera, la frase dentro del mensaje sigue contando (como hoy)."""
    meta = {"remarketing_touches": [{"at_ms": NOW - 3 * 24 * H, "kind": "template"}]}
    assert detect_marketing_opt_out("quiero otra igual, no más que en azul", meta, NOW) is True


def test_el_contexto_sin_texto_cuenta_la_calificacion_como_hasta_hoy() -> None:
    from src.platform.whatsapp.marketing_opt_out import has_recent_marketing_context

    meta = {"review_request_touches": [{"at_ms": NOW - 3 * H, "kind": "template"}]}
    assert has_recent_marketing_context(meta, NOW) is True
    assert has_recent_marketing_context(meta, NOW, text="quiero otra, no más que en azul") is False
    assert has_recent_marketing_context(meta, NOW, text="NO MÁS") is True
