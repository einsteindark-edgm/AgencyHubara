"""
config/env.py — sales_whatsapp domain.

Resolves the runtime workspace path for the **Sales** agent. This module is the
ONLY place in the sales_whatsapp domain allowed to read ``os.environ`` for
path resolution; everywhere else takes the path via constructor injection
(R-DIP).

Why per-domain (`EXOCLAW_WORKSPACE_SALES`) instead of a global
`EXOCLAW_WORKSPACE`:
    The hubara_agency process bundles two agents (sales + remarketing) with
    different identity, soul, tools and skills. A single global env var would
    force them to share a workspace dir. Per-domain env vars keep them
    independently configurable in production (e.g. each gets its own PVC).
    See ADR-2026-05-06-03 in `agent_coordination/decisions.md`.

The path is wired into a ``WorkspaceConfig(path=str(workspace))`` boundary DTO
and passed to ``bootstrap_sales_session_activity``. The activity calls
``DefaultConversation.create`` with this path so ``ContextBuilder`` finds the
bootstrap files (IDENTITY.md, SOUL.md, USER.md, TOOLS.md, AGENTS.md, plus
memory/* and skills/*).
"""
from __future__ import annotations

import os
from pathlib import Path

# Default = `<repo>/hubara_agency/src/domains/sales_whatsapp/workspace/`.
# `Path(__file__).resolve()` -> .../sales_whatsapp/config/env.py
# .parents[0] -> .../sales_whatsapp/config/
# .parents[1] -> .../sales_whatsapp/
_DEFAULT_WORKSPACE = (Path(__file__).resolve().parents[1] / "workspace").resolve()


def get_workspace_path() -> Path:
    """Resolve the **Sales** agent's runtime workspace.

    Default: ``<repo>/hubara_agency/src/domains/sales_whatsapp/workspace/``
    (committed to the repo for dev convenience).

    Override with ``EXOCLAW_WORKSPACE_SALES`` in production to point at a
    persistent volume so ``memory/MEMORY.md`` and ``memory/HISTORY.md``
    survive container restarts.
    """
    raw = os.environ.get("EXOCLAW_WORKSPACE_SALES")
    if raw:
        return Path(raw).expanduser().resolve()
    return _DEFAULT_WORKSPACE
