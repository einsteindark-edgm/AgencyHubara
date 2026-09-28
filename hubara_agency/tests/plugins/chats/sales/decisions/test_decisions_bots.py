"""Registro de bots (diseño v2 §08, fase F2): el ÚNICO control de qué corre en
cada conversación.

Un bot = una versión del workflow + un proveedor por capacidad (`reglas`,
`sombra` o `jev`) + un perfil de Jev. Lo leen el arranque del workflow, el
ingest, las tools, las activities y el laboratorio:

* en el laboratorio, el brazo escoge el bot (`bot_for_arm`), y el sandbox lo
  fija para el proceso del caso (`DECISIONS_BOT`);
* en producción (`bot_for_session`), el despliegue gradual que ya existe
  (números de prueba, porcentaje estable por conversación y techo de
  Terraform), ahora por capacidad y por versión del workflow.

Todo nace en `reglas` y en V1: sin tocar nada, el turno es el de hoy.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.plugins.chats.agent.sales.decisions import bots
from src.plugins.chats.agent.sales.decisions.rollout import RolloutState
from src.plugins.chats.agent.sales.decisions.rollout_store import write_state

TEST = "wa_573001234567"
OTHER = "wa_573009999999"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("DECISIONS_BOT", "SALES_CAPABILITIES_CEILING", "SALES_WORKFLOW_V2_CEILING", "SALES_PERCEPTION_PROFILE"):
        monkeypatch.delenv(name, raising=False)


def test_by_default_every_conversation_gets_todays_bot(tmp_path: Path) -> None:
    bot = bots.bot_for_session(TEST, vault_dir=tmp_path)

    assert bot.workflow == bots.WORKFLOW_V1
    assert bot.provider("baja") == "reglas" and bot.provider("compra") == "reglas"
    assert bot.profile == "jev-v1"


def test_the_lab_arm_picks_the_bot() -> None:
    assert bots.bot_for_arm("A1").workflow == bots.WORKFLOW_V1
    assert bots.bot_for_arm("A1").provider("compra") == "reglas"
    assert bots.bot_for_arm("B").provider("compra") == "jev"
    with pytest.raises(ValueError, match="Z"):
        bots.bot_for_arm("Z")


def test_the_sandbox_pins_the_bot_of_its_arm_for_the_whole_process(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DECISIONS_BOT", "B")

    assert bots.bot_for_session(OTHER, vault_dir=tmp_path) == bots.bot_for_arm("B")


def test_a_capability_moves_with_its_own_switch_within_the_ceiling(tmp_path: Path, monkeypatch) -> None:
    bots.write_capability_modes(tmp_path, {"baja": "on", "compra": "shadow"})

    capped = bots.bot_for_session(OTHER, vault_dir=tmp_path)
    monkeypatch.setenv("SALES_CAPABILITIES_CEILING", "on")
    free = bots.bot_for_session(OTHER, vault_dir=tmp_path)

    assert capped.provider("baja") == "reglas"  # techo de Terraform por defecto: off
    assert (free.provider("baja"), free.provider("compra"), free.provider("retoma")) == ("jev", "sombra", "reglas")


def test_canary_acts_only_for_test_numbers_and_the_stable_percent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SALES_CAPABILITIES_CEILING", "on")
    write_state(tmp_path, RolloutState(mode="shadow", canary_percent=0, test_numbers=(TEST,)))
    bots.write_capability_modes(tmp_path, {"baja": "canary"})

    assert bots.bot_for_session(TEST, vault_dir=tmp_path).provider("baja") == "jev"
    assert bots.bot_for_session(OTHER, vault_dir=tmp_path).provider("baja") == "sombra"


def test_workflow_v2_needs_its_own_switch_and_ceiling(tmp_path: Path, monkeypatch) -> None:
    bots.write_workflow_mode(tmp_path, "on")

    assert bots.bot_for_session(OTHER, vault_dir=tmp_path).workflow == bots.WORKFLOW_V1
    monkeypatch.setenv("SALES_WORKFLOW_V2_CEILING", "on")
    assert bots.bot_for_session(OTHER, vault_dir=tmp_path).workflow == bots.WORKFLOW_V2


def test_an_unreadable_control_is_todays_bot(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SALES_CAPABILITIES_CEILING", "on")
    (tmp_path / "_rollout").mkdir()
    (tmp_path / "_rollout" / "decisions.json").write_text("{roto", encoding="utf-8")

    bot = bots.bot_for_session(OTHER, vault_dir=tmp_path)

    assert bot.provider("baja") == "reglas" and bot.workflow == bots.WORKFLOW_V1


def test_an_unknown_capability_mode_is_ignored(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SALES_CAPABILITIES_CEILING", "on")
    bots.write_capability_modes(tmp_path, {"baja": "turbo"})

    assert bots.bot_for_session(OTHER, vault_dir=tmp_path).provider("baja") == "reglas"
