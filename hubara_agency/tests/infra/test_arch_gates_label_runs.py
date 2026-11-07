"""Adding a label to a PR never leaves its required checks cancelled.

The architecture gates also run on `labeled`/`unlabeled`, so that removing
`architecture-change` re-checks the diff without the bypass. A PR opened with a
label (`gh pr create --label hecho-por:agente`, as the cycle's Taller does)
fires `opened` and `labeled` at the same time. While both runs shared one
concurrency group, one cancelled the other: GitHub counts that cancelled run
on the head commit, so the PR stayed blocked although the other run passed
(PRs #482, #484 and #485).

A run is cancelled only by a newer push to the same PR, which is a different
commit; a label run never cancels and is never cancelled.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_GATES = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "architecture-gates.yml"


def _lookup(context: dict[str, Any], path: str) -> Any:
    value: Any = context
    for key in path.split(".")[1:]:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def _group(context: dict[str, Any]) -> str:
    """The concurrency group GitHub computes for a run with this `github` context."""
    template = str(yaml.safe_load(_GATES.read_text(encoding="utf-8"))["concurrency"]["group"])

    def evaluate(expression: str) -> str:
        python = re.sub(r"\bgithub(?:\.\w+)+", lambda m: f"_get({m.group()!r})", expression)
        python = python.replace("&&", " and ").replace("||", " or ")
        return str(eval(python, {"__builtins__": {}}, {"_get": lambda path: _lookup(context, path)}))  # noqa: S307

    return re.sub(r"\$\{\{(.*?)\}\}", lambda m: evaluate(m.group(1)), template)


def _pr_event(action: str, run_id: int, number: int = 485) -> dict[str, Any]:
    return {
        "workflow": "Architecture Gates",
        "run_id": run_id,
        "ref": f"refs/pull/{number}/merge",
        "event": {"action": action, "pull_request": {"number": number}},
    }


def test_a_pr_opened_with_a_label_runs_both_gates_to_the_end() -> None:
    assert _group(_pr_event("opened", 1)) != _group(_pr_event("labeled", 2))


def test_two_labels_at_once_do_not_cancel_each_other() -> None:
    assert _group(_pr_event("labeled", 2)) != _group(_pr_event("labeled", 3))
    assert _group(_pr_event("unlabeled", 4)) != _group(_pr_event("labeled", 5))


def test_a_new_push_still_cancels_the_run_of_the_previous_commit() -> None:
    concurrency = yaml.safe_load(_GATES.read_text(encoding="utf-8"))["concurrency"]

    assert concurrency["cancel-in-progress"] is True
    assert _group(_pr_event("opened", 1)) == _group(_pr_event("synchronize", 6)) == _group(_pr_event("reopened", 7))
    assert _group(_pr_event("synchronize", 6)) != _group(_pr_event("synchronize", 6, number=486))


def test_pushes_to_main_group_by_branch() -> None:
    push = {"workflow": "Architecture Gates", "run_id": 8, "ref": "refs/heads/main", "event": {}}

    assert _group(push) == _group({**push, "run_id": 9})
    assert "refs/heads/main" in _group(push)
