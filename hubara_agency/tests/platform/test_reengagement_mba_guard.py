"""D1.7 — con Meta Business Agent al frente, Hubara NO manda toques dentro de
la ventana de servicio (un envío propio le quitaría el hilo a MBA y sería un
doble toque: el followup de MBA queda apagado por decisión). Fuera de ventana,
templates como hoy (toman el hilo → política de release, D1.6).

La regla vive en la CENTRAL `decide_reengagement` (parámetro `mba_controls`,
default False = comportamiento de hoy) y la aplican el gate del
RemarketingWorkflow y el pre-filtro del snapshot.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.platform.whatsapp.cost import RateCard, RateCardEntry
from src.platform.whatsapp.send_policy import CHANNEL_FREE_FORM, decide_reengagement, lead_state_from_metadata

ONE_HOUR_MS = 60 * 60 * 1000
NOW = 1_757_500_000_000
SESSION = "wa_573001234567"


def _rate_card() -> RateCard:
    return RateCard(version="co_test", effective_from_ms=0, country="CO", currency="USD",
                    rates={"utility": RateCardEntry(usd_micros_per_message=800),
                           "marketing": RateCardEntry(usd_micros_per_message=12500)})


def _md(*, csw_open: bool) -> dict:
    return {
        "tag": "INTERESADO",
        "service_window_expires_at_ms": NOW + (ONE_HOUR_MS if csw_open else -ONE_HOUR_MS),
        "ctwa_window_expires_at_ms": NOW - ONE_HOUR_MS,
        "last_inbound_at_ms": NOW - 5 * ONE_HOUR_MS,
        "control_owner": "mba",
    }


def test_in_window_with_mba_controlling_nothing_is_sent() -> None:
    md = _md(csw_open=True)
    decision = decide_reengagement(NOW, md, lead_state_from_metadata(md), _rate_card(), mba_controls=True)
    assert decision.allowed is False and decision.suppress_reason == "control_owner_mba"
    baseline = decide_reengagement(NOW, md, lead_state_from_metadata(md), _rate_card())
    assert baseline.allowed and baseline.channel == CHANNEL_FREE_FORM  # default = hoy


def test_out_of_window_templates_go_as_today_even_with_mba_controlling() -> None:
    md = _md(csw_open=False)
    with_mba = decide_reengagement(NOW, md, lead_state_from_metadata(md), _rate_card(), mba_controls=True)
    without = decide_reengagement(NOW, md, lead_state_from_metadata(md), _rate_card())
    assert with_mba == without and with_mba.suppress_reason != "control_owner_mba"


def test_an_explicit_remarketing_tag_from_the_operator_still_touches_even_with_mba_controlling() -> None:
    """Misma excepción que `already_purchased` y `customer_active`: REMARKETING
    es una decisión humana de re-contactar ya (aunque le quite el hilo a MBA)."""
    md = _md(csw_open=True) | {"tag": "REMARKETING"}
    decision = decide_reengagement(NOW, md, lead_state_from_metadata(md), _rate_card(), mba_controls=True)
    assert decision.suppress_reason != "control_owner_mba"


def test_terminal_states_still_win_over_the_mba_rule() -> None:
    md = _md(csw_open=True) | {"tag": "HUMANO"}
    decision = decide_reengagement(NOW, md, lead_state_from_metadata(md), _rate_card(), mba_controls=True)
    assert decision.suppress_reason == "human_owned"


@pytest.mark.asyncio
async def test_the_remarketing_gate_reads_the_predicate_from_the_vault(monkeypatch, tmp_path: Path) -> None:
    from src.platform import config
    from src.platform.whatsapp import activities as act

    monkeypatch.setattr(act, "WORKSPACE_VAULT_DIR", tmp_path)
    monkeypatch.setattr(act, "_now_ms", lambda: NOW)
    monkeypatch.setattr(act, "is_quiet_hours_for_session", lambda *a, **k: False)
    monkeypatch.setattr(config, "MBA_CUSTOMER_ALLOWLIST", frozenset({"573001234567"}))
    (tmp_path / SESSION).mkdir(parents=True)
    (tmp_path / SESSION / "metadata.json").write_text(json.dumps(_md(csw_open=True)), encoding="utf-8")

    monkeypatch.setattr(config, "MBA_STANDBY_ENABLED", True)
    decision = await act.check_reengagement_policy_activity(SESSION)
    assert decision.allowed is False and decision.suppress_reason == "control_owner_mba"

    monkeypatch.setattr(config, "MBA_STANDBY_ENABLED", False)  # flag OFF → como hoy
    decision = await act.check_reengagement_policy_activity(SESSION)
    assert decision.suppress_reason != "control_owner_mba"
