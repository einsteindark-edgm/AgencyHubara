"""El gate `check_reengagement_policy_activity` re-valida la ESCALERA al ejecutar.

Un intent puede llegar viejo o duplicado (snapshot + cold start de la caja +
polls). La escalera es la autoridad de cadencia: si el toque anterior todavía
no "respiró" su hueco, o la escalera se agotó, el gate suprime. El tag
REMARKETING (decisión humana desde el dashboard) salta la escalera, igual que
salta las demás reglas de la central.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

from src.platform.whatsapp.activities import check_reengagement_policy_activity

H = 60 * 60 * 1000
SID = "wa_573001234567"


@pytest.fixture(autouse=True)
def _no_quiet_hours(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WATCHDOG_QUIET_HOURS_START", "0")
    monkeypatch.setenv("WATCHDOG_QUIET_HOURS_END", "24")


def _seed(vault: Path, *, silence_h: float, touches: list[tuple[float, str]],
          csw_open: bool = True, ctwa_open: bool = False, tag: str = "INTERESADO") -> None:
    now_ms = int(time.time() * 1000)
    last_inbound = now_ms - int(silence_h * H)
    d = vault / SID
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(json.dumps({
        "tag": tag,
        "last_inbound_at_ms": last_inbound,
        "service_window_expires_at_ms": now_ms + (H if csw_open else -H),
        "ctwa_window_expires_at_ms": now_ms + (H if ctwa_open else -H),
        "remarketing_touches": [
            {"at_ms": now_ms - int(h * H), "kind": kind} for h, kind in touches
        ],
    }), encoding="utf-8")


async def _decide():
    return await ActivityEnvironment().run(check_reengagement_policy_activity, SID)


@pytest.mark.asyncio
async def test_suprime_si_el_toque_anterior_no_respiro_su_hueco(_isolate_vault_dir: Path):
    _seed(_isolate_vault_dir, silence_h=3, touches=[(0.5, "free_form")])
    decision = await _decide()
    assert decision.allowed is False
    assert decision.suppress_reason == "ladder_not_due"


@pytest.mark.asyncio
async def test_permite_el_segundo_toque_cuando_vence(_isolate_vault_dir: Path):
    _seed(_isolate_vault_dir, silence_h=4.5, touches=[(2.1, "free_form")])
    decision = await _decide()
    assert decision.allowed is True
    assert decision.channel == "free_form"


@pytest.mark.asyncio
async def test_suprime_escalera_agotada(_isolate_vault_dir: Path):
    _seed(
        _isolate_vault_dir,
        silence_h=23,
        touches=[(21, "free_form"), (19, "free_form"), (15, "abstained"),
                 (9, "free_form"), (3, "free_form")],
    )
    decision = await _decide()
    assert decision.allowed is False
    assert decision.suppress_reason == "ladder_exhausted"


@pytest.mark.asyncio
async def test_tope_de_dos_plantillas_por_dia(_isolate_vault_dir: Path):
    # CSW cerrada + 72h CTWA abierta → la central pide plantilla; ya salieron 2.
    _seed(
        _isolate_vault_dir,
        silence_h=40,
        csw_open=False,
        ctwa_open=True,
        touches=[(20, "template"), (7, "template")],
    )
    decision = await _decide()
    assert decision.allowed is False
    assert decision.suppress_reason == "template_daily_cap"


@pytest.mark.asyncio
async def test_tag_remarketing_del_operador_salta_la_escalera(_isolate_vault_dir: Path):
    _seed(_isolate_vault_dir, silence_h=3, touches=[(0.5, "free_form")], tag="REMARKETING")
    decision = await _decide()
    assert decision.allowed is True
