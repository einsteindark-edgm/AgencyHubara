"""CI corre los replay de workflows y los tests de Temporal de la Ola 0
(auditoría de Temporal, 2026-10-15; premortem 2026-10-16).

Los `test_replay_*.py` son la única guarda contra un deploy que rompe runs
vivos (L-9: nondeterminism que el sticky cache esconde hasta el reinicio del
worker). El job de arquitectura nombra sus paths uno por uno y no los incluía:
en CI no corrían. El premortem encontró dos huecos más: el paso solo veía el
primer nivel de `tests/` (un replay bajo `tests/plugins/` quedaba afuera) y los
tests de la Ola 0 tampoco corrían en CI.
"""
from __future__ import annotations

from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[3]
_TESTS = _REPO / "hubara_agency" / "tests"
_GATES = _REPO / ".github" / "workflows" / "architecture-gates.yml"


def _backend_steps() -> list[dict]:
    return yaml.safe_load(_GATES.read_text(encoding="utf-8"))["jobs"]["backend-arch"]["steps"]


def _run_of(name_prefix: str) -> str:
    [step] = [s for s in _backend_steps() if str(s.get("name", "")).startswith(name_prefix)]
    return str(step.get("run") or "")


def test_the_backend_gates_run_every_replay_test_at_any_depth() -> None:
    run = _run_of("Replay de workflows")
    assert "shopt -s globstar" in run and "tests/**/test_replay_*.py" in run, run
    # Con `globstar`, `tests/**/` incluye el primer nivel y cualquier carpeta:
    # lo que ve este glob es lo que corre CI.
    assert len(list(_TESTS.glob("**/test_replay_*.py"))) >= 5


def test_the_wave0_temporal_tests_run_in_ci_and_none_is_stale() -> None:
    listed = [token for token in _run_of("Temporal — continue-as-new").split() if token.startswith("tests/")]
    assert len(listed) >= 20, listed
    missing = [p for p in listed if not (_TESTS.parent / p).exists()]
    assert missing == [], f"paths que ya no existen en el paso de CI: {missing}"
