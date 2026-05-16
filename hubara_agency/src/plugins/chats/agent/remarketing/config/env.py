"""
config/env.py — remarketing_whatsapp domain.

Resolves the runtime workspace path for the **Remarketing** agent. This module
is the ONLY place in the remarketing_whatsapp domain allowed to read
``os.environ`` for path resolution; everywhere else takes the path via
constructor injection (R-DIP).

Why per-domain (`EXOCLAW_WORKSPACE_REMARKETING`) instead of a global
`EXOCLAW_WORKSPACE`:
    The hubara_agency process bundles two agents (sales + remarketing) with
    different identity, soul, tools and skills. A single global env var would
    force them to share a workspace dir. Per-domain env vars keep them
    independently configurable in production (e.g. each gets its own PVC).
    See ADR-2026-05-06-03 in `agent_coordination/decisions.md`.

The path is wired into a ``WorkspaceConfig(path=str(workspace))`` boundary DTO
and passed (via PR-A) through ``RemarketingSessionInput.runtime_workspace_path``
to ``bootstrap_remarketing_session_activity``. PR-B: the activity now consumes
it and instantiates ``WorkspaceConfig(path=runtime_workspace_path)`` directly
— ``build_workspace_config(session_id)`` (the per-session vault dir) is no
longer called for identity/catalog purposes. The per-session vault still owns
the JSONL message store and metadata files; the workspace dir owns the
bootstrap files (IDENTITY.md, SOUL.md, USER.md, TOOLS.md, AGENTS.md, plus
memory/* and skills/*).
"""
from __future__ import annotations

import os
from pathlib import Path

# Default = `<repo>/hubara_agency/src/domains/remarketing_whatsapp/workspace/`.
# `Path(__file__).resolve()` -> .../remarketing_whatsapp/config/env.py
# .parents[0] -> .../remarketing_whatsapp/config/
# .parents[1] -> .../remarketing_whatsapp/
_DEFAULT_WORKSPACE = (Path(__file__).resolve().parents[1] / "workspace").resolve()


def get_workspace_path() -> Path:
    """Resolve the **Remarketing** agent's runtime workspace.

    Default: ``<repo>/hubara_agency/src/domains/remarketing_whatsapp/workspace/``
    (committed to the repo for dev convenience).

    Override with ``EXOCLAW_WORKSPACE_REMARKETING`` in production to point at a
    persistent volume so ``memory/MEMORY.md`` and ``memory/HISTORY.md``
    survive container restarts.
    """
    raw = os.environ.get("EXOCLAW_WORKSPACE_REMARKETING")
    if raw:
        return Path(raw).expanduser().resolve()
    return _DEFAULT_WORKSPACE
