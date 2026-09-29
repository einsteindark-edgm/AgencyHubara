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
OTHER = "wa_573009876543"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("DECISIONS_BOT", "SALES_CAPABILITIES_CEILING", "SALES_WORKFLOW_V2_CEILING", "SALES_PERCEPTION_PROFILE"):
        monkeypatch.delenv(name, raising=False)


def test_by_default_every_conversation_gets_todays_bot(tmp_path: Path) -> None:
    bot = bots.bot_for_session(TEST, vault_dir=tmp_path)

    assert bot.workflow == bots.WORKFLOW_V1
    assert bot.provider("baja") == "reglas" and bot.provider("compra") == "reglas"
    assert bot.profile == "jev-v3"


def test_the_lab_arm_picks_the_bot() -> None:
    assert bots.bot_for_arm("A1").workflow == bots.WORKFLOW_V1
    assert bots.bot_for_arm("A1").provider("compra") == "reglas"
    assert bots.bot_for_arm("B").provider("compra") == "jev"
    with pytest.raises(ValueError, match="Z"):
        bots.bot_for_arm("Z")


def test_the_lab_arms_separate_the_new_workflow_from_jev() -> None:
    """Diseño §08: A1 = V1 con reglas (el bot de hoy); B0 = V2 con reglas
    (tiene que dar lo mismo que A1: prueba que el esqueleto nuevo es fiel);
    B = V2 con Jev. Así se separa el efecto del workflow del efecto de Jev."""
    a1, b0, b = (bots.bot_for_arm(arm) for arm in ("A1", "B0", "B"))

    assert tuple(bots.LAB_BOTS) == ("A1", "B0", "B")
    assert (a1.workflow, b0.workflow, b.workflow) == (bots.WORKFLOW_V1, bots.WORKFLOW_V2, bots.WORKFLOW_V2)
    assert all(b0.provider(c) == "reglas" for c in ("compra", "retoma", "baja", "destinatario", "saludo"))
    assert (b0.layers, b0.profile) == ("off", bots.DEFAULT_PROFILE)
    assert (b.provider("destinatario"), b.provider("compra"), b.layers, b.profile) == ("jev", "jev", "on", "jev-v3")


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


def test_every_capability_of_the_engine_has_a_switch_in_the_control() -> None:
    """Una capacidad que el control no conoce nunca se podría subir a sombra
    ni a Jev desde el dashboard (ni bajar). Guarda: las del motor = las del
    control."""
    import ast
    from pathlib import Path as _Path

    root = _Path(bots.__file__).parent
    names: set[str] = set()
    for path in [*root.joinpath("capabilities").glob("*.py"), root / "egress.py"]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constants = {
            t.id: n.value.value
            for n in ast.walk(tree) if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
            for t in n.targets if isinstance(t, ast.Name)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "name" for t in item.targets):
                        value = item.value
                        if isinstance(value, ast.Constant) and isinstance(value.value, str):
                            names.add(value.value)
                        elif isinstance(value, ast.Name) and value.id in constants:
                            names.add(constants[value.id])

    assert names and names <= set(bots.CAPABILITIES), sorted(names - set(bots.CAPABILITIES))
