"""CI corre los replay de workflows (auditoría de Temporal, 2026-10-15).

Los `tests/test_replay_*.py` son la única guarda contra un deploy que rompe
runs vivos (L-9: nondeterminism que el sticky cache esconde hasta el reinicio
del worker). El job de arquitectura nombra sus paths uno por uno y no los
incluía: en CI no corrían.
"""
from __future__ import annotations

import fnmatch
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[3]
_GATES = _REPO / ".github" / "workflows" / "architecture-gates.yml"


def _backend_runs() -> list[str]:
    jobs = yaml.safe_load(_GATES.read_text(encoding="utf-8"))["jobs"]
    return [str(step.get("run") or "") for step in jobs["backend-arch"]["steps"]]


def test_the_backend_gates_run_every_replay_test() -> None:
    replay_tests = sorted(p.name for p in (_REPO / "hubara_agency" / "tests").glob("test_replay_*.py"))
    assert len(replay_tests) >= 5, replay_tests
    patterns = [token for run in _backend_runs() for token in run.split() if token.startswith("tests/test_replay")]
    covered = [name for name in replay_tests if any(fnmatch.fnmatch(f"tests/{name}", p) for p in patterns)]
    assert covered == replay_tests, f"replay tests fuera de CI: {sorted(set(replay_tests) - set(covered))}"
