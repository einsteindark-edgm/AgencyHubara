"""Meta-test — guard the architecture suite itself against silent rewrites.

Why this exists
---------------
The architectural gate is only as strong as the trust in its tests. If a coding
agent (human or AI) could edit `tests/architecture/`, `.importlinter`, or the
`R_*_EXEMPTIONS` allow-lists in the same PR that adds a feature, the gate is
just a sticker. This meta-test enforces the rule that those files are CHANGED
ONLY by an explicitly-approved architecture PR:

  - Diffs the current worktree against `origin/main` (fallback: `main`).
  - Fails if any path under `ARCHITECTURE_PROTECTED_PREFIXES` is modified.
  - Allows bypass via the environment variable `ARCH_CHANGE_APPROVED=1`.

Bypass policy
-------------
The env var `ARCH_CHANGE_APPROVED=1` is the ONLY way to allow modifications:

  - In CI, it is set by the architecture-change CI job ONLY when the PR carries
    the `architecture-change` label AND a CODEOWNERS-approved review.
  - Locally, you set it manually when iterating on a deliberate architecture
    change (e.g. landing an ADR). Treat each manual set as a conscious act.

Skip conditions (informative skip, not a fail):
  - Not running inside a git repository.
  - Neither `origin/main` nor `main` ref is available.
  - HEAD is checked out on the `main` branch (nothing to diff against).

Honest caveat
-------------
This test runs the WORKING-TREE version of itself. A sufficiently capable agent
can disable it by editing this file in the same PR — though doing so would
itself show up in the diff and is visible to reviewers. For tamper-resistant
enforcement, pair this test with:

  - Branch protection + CODEOWNERS on `hubara_agency/tests/architecture/**`
    and `hubara_agency/.importlinter` (Capa 4).
  - A CI job that runs `pytest -m architecture` from a clean checkout of
    `origin/main` HEAD, not from the PR branch (out-of-band enforcement).
  - Optional: pinned-checksum of each protected file, verified by CI (Capa 5).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.architecture.conftest import ARCHITECTURE_PROTECTED_PREFIXES


_HUBARA_ROOT: Path = Path(__file__).resolve().parents[2]
_REPO_ROOT: Path = _HUBARA_ROOT.parent

_BYPASS_ENV_VAR = "ARCH_CHANGE_APPROVED"
_BASE_REF_CANDIDATES: tuple[str, ...] = ("origin/main", "main")


def _git(*args: str) -> tuple[int, str]:
    """Run `git <args>` from the repo root. Returns (exit_code, stdout)."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        return (127, "")
    return (result.returncode, result.stdout)


def _pick_base_ref() -> str | None:
    for ref in _BASE_REF_CANDIDATES:
        code, _ = _git("rev-parse", "--verify", "--quiet", ref)
        if code == 0:
            return ref
    return None


def _current_branch() -> str | None:
    code, out = _git("rev-parse", "--abbrev-ref", "HEAD")
    if code != 0:
        return None
    name = out.strip()
    return name or None


def _changed_files_vs(base: str) -> list[str]:
    """Files changed in current worktree vs `base`.

    Includes:
      - tracked diffs vs base (`git diff <base> --name-only`) — committed,
        staged, and unstaged tracked changes.
      - untracked files (`git ls-files --others --exclude-standard`) — new
        files the developer added but has not staged yet.

    The untracked layer matters for LOCAL enforcement; in CI everything is
    committed by the time the test runs, so `git diff <base>` alone suffices.
    Including both keeps the local and CI gates symmetric.
    """
    code, out = _git("diff", "--name-only", base, "--")
    tracked = [line.strip() for line in out.splitlines() if line.strip()] if code == 0 else []

    code2, out2 = _git("ls-files", "--others", "--exclude-standard")
    untracked = [line.strip() for line in out2.splitlines() if line.strip()] if code2 == 0 else []

    seen: set[str] = set()
    combined: list[str] = []
    for path in (*tracked, *untracked):
        if path not in seen:
            seen.add(path)
            combined.append(path)
    return combined


def _is_protected(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in ARCHITECTURE_PROTECTED_PREFIXES)


def test_protected_files_unchanged_vs_main() -> None:
    """Meta-gate — block silent rewrites of the architecture suite."""
    if os.environ.get(_BYPASS_ENV_VAR) == "1":
        pytest.skip(
            f"{_BYPASS_ENV_VAR}=1 — bypass acknowledged. "
            f"In CI this is set only when the PR carries the `architecture-change` "
            f"label and a CODEOWNERS-approved review. Locally, you set it "
            f"intentionally when working on a deliberate architecture change."
        )

    if not (_REPO_ROOT / ".git").exists():
        pytest.skip("Not inside a git repository — meta-gate does not apply.")

    base = _pick_base_ref()
    if base is None:
        pytest.skip(
            "No `origin/main` or `main` ref available locally. "
            "Run `git fetch origin main` if you want the meta-gate to engage."
        )

    branch = _current_branch()
    if branch == "main":
        pytest.skip("HEAD is on `main` — there is no PR to compare against.")

    changed = _changed_files_vs(base)
    offenders = sorted(p for p in changed if _is_protected(p))
    assert not offenders, (
        f"Meta-gate violation — architecture-protected files modified vs `{base}` "
        f"without {_BYPASS_ENV_VAR}=1:\n  "
        + "\n  ".join(offenders)
        + "\n\n"
        "These files encode the DEHA architectural contract. Modifying them "
        "requires:\n"
        "  1. An ADR under `docs/adr/` documenting the proposed change.\n"
        "  2. A separate PR carrying the `architecture-change` label.\n"
        "  3. A CODEOWNERS-approved review.\n\n"
        f"If THIS is the architecture-change PR itself: set {_BYPASS_ENV_VAR}=1 "
        f"(CI does it automatically when the label and review are present).\n"
        "If you are an AI coding assistant: STOP. Surface this in the task "
        "result as `status: blocked, reason: requires_planner_update`. Do not "
        "edit the protected files to make the gate pass."
    )
