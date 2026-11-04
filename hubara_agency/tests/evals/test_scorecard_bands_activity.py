"""Activity `compute_scorecard_bands` (Ciclo Colossus F4): el barrido diario
calcula las bandas por regla, abre o comenta las alertas de las reglas en
`abrir` y guarda el informe del día en el vault. Nunca lanza: un error queda en
el resumen y el barrido sigue."""
from __future__ import annotations

import json
from pathlib import Path

from temporalio.testing import ActivityEnvironment

from src.plugins.chats.agent.sales_eval import bands
from src.plugins.chats.agent.sales_eval.activities import bands_activities as ba
from src.plugins.chats.agent.sales_eval.evals import composition
from src.plugins.chats.agent.sales_eval.evals.contracts import BandsInput
from src.plugins.chats.agent.sales_eval.scorecard import alerts
from tests.evals.test_scorecard_bands import BOTSITO, END, MAYOR, _day, _history, _put


class Tracker:
    def __init__(self, *, broken: bool = False) -> None:
        self.broken = broken
        self.created: list[str] = []

    async def find_open(self, fingerprint: str) -> int | None:
        if self.broken:
            raise RuntimeError("GitHub no responde")
        return None

    async def create(self, title: str, body: str, labels: list[str]) -> int:
        self.created.append(title)
        return 77

    async def comment(self, number: int, body: str) -> None:  # pragma: no cover
        raise AssertionError("no había alerta abierta")


def _setup(monkeypatch, vault: Path, tracker: Tracker) -> None:
    _history(vault, base_fails={MAYOR: 1}, recent_fails={MAYOR: 6})
    monkeypatch.setattr(composition, "get_vault_dir", lambda: vault)
    monkeypatch.setattr(alerts, "tracker_from_env", lambda: tracker)


def _saved(vault: Path) -> dict:
    return json.loads((vault / "_evals" / "bands" / f"{END.isoformat()}.json").read_text(encoding="utf-8"))


async def test_the_daily_run_opens_the_alert_and_keeps_the_report(tmp_path: Path, monkeypatch) -> None:
    tracker = Tracker()
    _setup(monkeypatch, tmp_path, tracker)

    summary = await ActivityEnvironment().run(
        ba.compute_scorecard_bands_activity, BandsInput(alert=True, end=END.isoformat())
    )

    assert (summary.to_open, summary.opened, summary.commented, summary.error) == (1, 1, 0, "")
    assert len(tracker.created) == 1
    saved = _saved(tmp_path)
    assert saved["alertas"] == [{"regla": MAYOR, "accion": "abierta", "issue": 77}]
    assert next(r for r in saved["reglas"] if r["regla"] == MAYOR)["banda"] == "abrir"
    assert summary.report_path.endswith(f"{END.isoformat()}.json")


async def test_without_alerting_it_only_keeps_the_report(tmp_path: Path, monkeypatch) -> None:
    tracker = Tracker()
    _setup(monkeypatch, tmp_path, tracker)

    summary = await ActivityEnvironment().run(
        ba.compute_scorecard_bands_activity, BandsInput(alert=False, end=END.isoformat())
    )

    assert (summary.to_open, summary.opened) == (1, 0)
    assert tracker.created == []
    assert "alertas" not in _saved(tmp_path)


async def test_a_failing_alert_still_keeps_the_report(tmp_path: Path, monkeypatch) -> None:
    _setup(monkeypatch, tmp_path, Tracker(broken=True))

    summary = await ActivityEnvironment().run(
        ba.compute_scorecard_bands_activity, BandsInput(alert=True, end=END.isoformat())
    )

    assert summary.error.startswith("alerta:")
    assert summary.opened == 0
    assert next(r for r in _saved(tmp_path)["reglas"] if r["regla"] == MAYOR)["banda"] == "abrir"


async def test_an_unreadable_vault_never_breaks_the_sweep(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(composition, "get_vault_dir", lambda: tmp_path)

    def broken(*args, **kwargs):
        raise OSError("vault ilegible")

    monkeypatch.setattr(bands, "build_report", broken)

    summary = await ActivityEnvironment().run(ba.compute_scorecard_bands_activity, BandsInput())

    assert "vault ilegible" in summary.error
    assert summary.to_open == 0


async def test_the_daily_run_counts_only_the_production_bot(tmp_path: Path, monkeypatch) -> None:
    """El barrido no recibe el bot: usa el de producción (Colossus). Una subida
    que solo tuvo Botsito no abre alerta."""
    tracker = Tracker()
    _history(tmp_path, base_fails={MAYOR: 1}, recent_fails={MAYOR: 1})
    for offset in range(0, 7):
        _put(tmp_path, day=_day(offset), turns=100, fails={MAYOR: 9}, session=BOTSITO, workflow="v1")
    monkeypatch.setattr(composition, "get_vault_dir", lambda: tmp_path)
    monkeypatch.setattr(alerts, "tracker_from_env", lambda: tracker)

    summary = await ActivityEnvironment().run(
        ba.compute_scorecard_bands_activity, BandsInput(alert=True, end=END.isoformat())
    )

    assert (summary.to_open, summary.opened, summary.error) == (0, 0, "")
    assert tracker.created == []
    saved = _saved(tmp_path)
    assert saved["bot"] == "nuevo"
    assert next(r for r in saved["reglas"] if r["regla"] == MAYOR)["banda"] == "normal"
