"""El control del bot nuevo, POR COMANDO (decisión del operador, 2026-10-06):
«que los botones de la UI no sirvan y todo se haga por comandos, para evitar
que alguien jugando dañe producción».

`decisions/control.py` guarda las mismas garantías que tenían los PUT del
panel (ahora 403): el modo se mueve DENTRO del techo de Terraform; apagar y
bajar siempre pasan; subir sin la vara se rechaza con los chequeos que
fallan; cada cambio queda firmado. Suma el interruptor «los números de prueba
deciden con Jev»: solo cambia a esos números, dentro de los techos, y nadie
más ve diferencia (ni la espera de la sombra de las capacidades).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.plugins.chats.agent.sales.decisions import bots, control, probe
from src.plugins.chats.agent.sales.decisions.rollout import RolloutState
from src.plugins.chats.agent.sales.decisions.rollout_store import ShadowMetrics, write_state

NOW_MS = 1_790_200_000_000
HOUR_MS = 3_600_000
SERVED = "typesafe/jev-1.13-20260917"
TEST = "wa_573001234567"
OTHER = "wa_573009876543"


def _probe_report(status: str = "ok", *, hours_ago: int = 2) -> dict:
    return {
        "at_ms": NOW_MS - hours_ago * HOUR_MS, "status": status, "models": [SERVED], "cases": 20,
        "ok_rate": 1.0, "pass_rate": 0.95, "p95_ms": 900, "shape_errors": [], "failures": [],
    }


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(control, "_now_ms", lambda: NOW_MS)
    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    for key, value in {
        "SALES_PERCEPTION_MODE_CEILING": "on",
        "SALES_CAPABILITIES_CEILING": "on",
        "SALES_WORKFLOW_V2_CEILING": "on",
        "SALES_PERCEPTION_PROFILE": "jev-v1",
        "SALES_SIGNAL_INBOUND_META": "on",
        "OPENROUTER_API_KEY": "sk-or-test",
    }.items():
        monkeypatch.setenv(key, value)
    return tmp_path


def _saved(vault: Path) -> dict:
    return json.loads((vault / "_rollout" / "perception.json").read_text(encoding="utf-8"))


def _refused(call, *args, **kwargs) -> control.ControlError:
    with pytest.raises(control.ControlError) as caught:
        call(*args, **kwargs)
    return caught.value


# ── Percepción (las capas del turno): lo que hacía el PUT /perception/rollout ──


def test_the_mode_moves_within_the_ceiling_and_is_signed(vault: Path) -> None:
    out = control.set_rollout(vault, mode="shadow", actor="comando:ana")

    assert out["state"]["mode"] == "shadow"
    saved = _saved(vault)
    assert (saved["mode"], saved["updated_at_ms"], saved["updated_by"]) == ("shadow", NOW_MS, "comando:ana")


def test_raising_without_the_shadow_bar_is_refused_with_the_failing_checks(vault: Path) -> None:
    control.set_rollout(vault, mode="shadow", actor="x")

    error = _refused(control.set_rollout, vault, mode="on", actor="x")

    assert error.status == 422 and "shadow_days" in error.detail["failing"]


def test_turning_off_always_works(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    control.set_rollout(vault, mode="shadow", actor="x")
    monkeypatch.setenv("SALES_SIGNAL_INBOUND_META", "off")
    monkeypatch.delenv("OPENROUTER_API_KEY")

    assert control.set_rollout(vault, mode="off", actor="x")["state"]["mode"] == "off"


def test_canary_settings_are_validated(vault: Path) -> None:
    assert _refused(control.set_rollout, vault, mode="off", canary_percent=150, actor="x").status == 422
    assert _refused(control.set_rollout, vault, mode="off", test_numbers=["573001234567"], actor="x").status == 422

    out = control.set_rollout(vault, mode="off", canary_percent=10, test_numbers=[TEST], actor="x")

    assert out["state"]["test_numbers"] == [TEST] and out["state"]["canary_percent"] == 10


def test_test_numbers_must_match_whole(vault: Path) -> None:
    assert _refused(control.set_rollout, vault, mode="off", test_numbers=[TEST + "\n"], actor="x").status == 422


def test_turning_off_never_waits_for_the_shadow_metrics(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Apagar es el interruptor de emergencia: no recorre el vault antes de
    escribir, y si las métricas fallan la sombra cuenta como cero (nadie sube
    por error)."""
    control.set_rollout(vault, mode="shadow", actor="x")

    def broken(*_a, **_k):
        raise RuntimeError("recorrido del vault caído")

    monkeypatch.setattr(control, "shadow_metrics", broken)

    assert control.set_rollout(vault, mode="off", actor="x")["state"]["mode"] == "off"
    assert _saved(vault)["mode"] == "off"
    snap = control.snapshot(vault)
    assert snap["metrics"]["turns"] == 0 and "shadow_days" in snap["can"]["canary"]


def test_an_odd_stored_setting_never_blocks_turning_off(vault: Path) -> None:
    (vault / "_rollout").mkdir()
    (vault / "_rollout" / "perception.json").write_text(
        json.dumps({"mode": "canary", "canary_percent": 150, "test_numbers": ["no-es-un-numero"]}), encoding="utf-8"
    )

    assert control.set_rollout(vault, mode="off", actor="x")["state"]["mode"] == "off"


def test_a_slow_raise_never_overwrites_a_turn_off_that_arrived_meanwhile(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control.set_rollout(vault, mode="shadow", actor="x")
    probe.write_report(vault, _probe_report())

    def metrics_while_someone_turns_off(*_a, **_k):
        write_state(vault, RolloutState(mode="off", updated_by="otra persona"))
        return ShadowMetrics(days=8, turns=400, fallback_rate=0.0, p95_ms=500)

    monkeypatch.setattr(control, "shadow_metrics", metrics_while_someone_turns_off)

    assert _refused(control.set_rollout, vault, mode="canary", canary_percent=10, actor="x").status == 409
    assert _saved(vault)["mode"] == "off"


@pytest.mark.parametrize("report", [_probe_report("degraded"), _probe_report(hours_ago=49)], ids=["degraded", "vieja"])
def test_raising_with_a_bad_or_old_probe_is_refused(vault: Path, monkeypatch: pytest.MonkeyPatch, report) -> None:
    control.set_rollout(vault, mode="shadow", actor="x")
    monkeypatch.setattr(
        control, "shadow_metrics", lambda *_a, **_k: ShadowMetrics(days=8, turns=400, fallback_rate=0.0, p95_ms=500)
    )
    probe.write_report(vault, report)

    error = _refused(control.set_rollout, vault, mode="canary", canary_percent=10, actor="x")

    assert error.status == 422 and error.detail["failing"] == ["probe_ok"]


def test_test_numbers_are_added_and_removed_without_touching_the_mode(vault: Path) -> None:
    control.set_rollout(vault, mode="shadow", actor="x")

    control.add_test_number(vault, TEST, actor="x")
    control.add_test_number(vault, TEST, actor="x")

    assert (_saved(vault)["test_numbers"], _saved(vault)["mode"]) == ([TEST], "shadow")
    control.remove_test_number(vault, TEST, actor="x")
    assert _saved(vault)["test_numbers"] == []


# ── Capacidades y workflow: lo que hacían los PUT /capabilities y /workflow ──


def test_a_capability_moves_to_shadow_within_the_ceiling(vault: Path) -> None:
    out = control.set_capability(vault, "baja", "shadow", actor="x")

    assert out["capabilities"]["baja"]["mode"] == "shadow"
    assert bots.bot_for_session(OTHER, vault_dir=vault).provider("baja") == "sombra"


def test_raising_a_capability_without_its_bar_is_refused(vault: Path) -> None:
    error = _refused(control.set_capability, vault, "baja", "canary", actor="x")

    assert error.status == 422 and "shadow_days" in error.detail["failing"]


def test_a_capability_above_the_ceiling_is_refused(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SALES_CAPABILITIES_CEILING", "off")

    assert "within_ceiling" in _refused(control.set_capability, vault, "baja", "shadow", actor="x").detail["failing"]


def test_lowering_a_capability_always_passes(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bots.write_capability_modes(vault, {"compra": "on"})
    monkeypatch.setenv("SALES_CAPABILITIES_CEILING", "off")

    assert control.set_capability(vault, "compra", "off", actor="x")["capabilities"]["compra"]["mode"] == "off"


def test_an_unknown_capability_or_mode_is_refused(vault: Path) -> None:
    assert _refused(control.set_capability, vault, "juguete", "shadow", actor="x").status == 422
    assert _refused(control.set_capability, vault, "baja", "turbo", actor="x").status == 422


def test_workflow_v2_goes_to_canary_before_everyone(vault: Path) -> None:
    assert _refused(control.set_workflow, vault, "on", actor="x").status == 422

    out = control.set_workflow(vault, "canary", actor="x")

    assert out["workflow_v2"]["mode"] == "canary" and bots.read_decisions_state(vault)["workflow_v2"] == "canary"
    assert control.set_workflow(vault, "on", actor="x")["workflow_v2"]["mode"] == "on"


def test_workflow_v2_respects_the_ceiling_and_always_goes_back(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SALES_WORKFLOW_V2_CEILING", "off")
    assert _refused(control.set_workflow, vault, "canary", actor="x").status == 422

    bots.write_workflow_mode(vault, "on")

    assert control.set_workflow(vault, "off", actor="x")["workflow_v2"]["mode"] == "off"
    assert _refused(control.set_workflow, vault, "shadow", actor="x").status == 422


# ── Los números de prueba deciden con Jev (2026-10-06) ──────────────────────


def test_test_numbers_decide_with_jev_and_nobody_else_changes(vault: Path) -> None:
    control.set_rollout(vault, mode="shadow", test_numbers=[TEST], actor="x")

    control.set_test_numbers_jev(vault, True, actor="x")

    mine = bots.bot_for_session(TEST, vault_dir=vault)
    other = bots.bot_for_session(OTHER, vault_dir=vault)
    assert (mine.provider("baja"), mine.provider("compra"), mine.layers) == ("jev", "jev", "canary")
    assert (other.provider("baja"), other.provider("compra"), other.layers) == ("reglas", "reglas", "shadow")


def test_test_numbers_with_jev_never_pass_the_terraform_ceilings(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    control.set_rollout(vault, mode="shadow", test_numbers=[TEST], actor="x")
    control.set_test_numbers_jev(vault, True, actor="x")
    monkeypatch.setenv("SALES_CAPABILITIES_CEILING", "shadow")
    monkeypatch.setenv("SALES_PERCEPTION_MODE_CEILING", "shadow")

    mine = bots.bot_for_session(TEST, vault_dir=vault)

    assert (mine.provider("baja"), mine.layers) == ("sombra", "shadow")


def test_turning_on_jev_for_test_numbers_needs_numbers_a_key_and_room_under_the_ceilings(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert "test_numbers" in _refused(control.set_test_numbers_jev, vault, True, actor="x").detail["failing"]

    control.add_test_number(vault, TEST, actor="x")
    monkeypatch.setenv("SALES_CAPABILITIES_CEILING", "shadow")
    monkeypatch.setenv("OPENROUTER_API_KEY", "PLACEHOLDER_set_out_of_band")

    failing = _refused(control.set_test_numbers_jev, vault, True, actor="x").detail["failing"]

    assert {"within_ceiling", "api_key"} <= set(failing)


def test_turning_off_jev_for_test_numbers_always_works(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    control.add_test_number(vault, TEST, actor="x")
    control.set_test_numbers_jev(vault, True, actor="x")
    monkeypatch.delenv("OPENROUTER_API_KEY")

    out = control.set_test_numbers_jev(vault, False, actor="x")

    assert out["test_numbers_jev"] is False
    assert bots.bot_for_session(TEST, vault_dir=vault).provider("baja") == "reglas"


# ── El comando ──────────────────────────────────────────────────────────────


@pytest.fixture
def cli(vault: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(control, "_default_vault", lambda: vault)
    return vault


def test_every_change_needs_who_makes_it(cli: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert control.main(["numeros", "agregar", TEST]) == 2

    assert "--por" in capsys.readouterr().err
    assert not (cli / "_rollout" / "perception.json").exists()


def test_the_command_adds_a_test_number_signed_and_masked(cli: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert control.main(["--por", "ana", "numeros", "agregar", TEST]) == 0

    out = capsys.readouterr().out
    assert "573001234567" not in out and "···4567" in out
    assert (_saved(cli)["test_numbers"], _saved(cli)["updated_by"]) == ([TEST], "comando:ana")


def test_a_refused_change_exits_with_the_failing_checks(cli: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert control.main(["--por", "ana", "capacidad", "baja", "canary"]) == 2

    assert "shadow_days" in capsys.readouterr().err


def test_the_state_needs_no_author_and_shows_what_runs(cli: Path, capsys: pytest.CaptureFixture[str]) -> None:
    control.main(["--por", "ana", "numeros", "agregar", TEST])
    control.main(["--por", "ana", "prueba-jev", "si"])
    capsys.readouterr()

    assert control.main(["estado"]) == 0

    out = capsys.readouterr().out
    assert "Workflow V2" in out and "Números de prueba" in out and "···4567" in out
    assert "con Jev: sí" in out
