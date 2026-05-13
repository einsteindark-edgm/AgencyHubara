"""Fixtures + setup for `tests/functional/` — the agent-level evidence layer.

What lives here (vs `tests/architecture/` and root `tests/`):

- `tests/test_*.py` → unit-ish tests of individual classes / pure functions.
- `tests/architecture/` → structural invariants (R-rules, layout). Gate that
  decides if a task can ship to main.
- `tests/functional/` (this dir) → END-TO-END behavior of a feature. Calls a
  tool / endpoint / workflow / agent the way it would be invoked in
  production (with mock externals), asserts the observable outcome, and is
  the EVIDENCE attached to the PR comment.

The pipeline (hu-exoclaw-pipeline.yaml) runs `pytest tests/functional/
-m functional --tb=long -v` as part of the gate, captures the output, and
embeds it in the PR body so a reviewer sees concrete proof that the feature
works without having to reproduce locally.

LLM strategy:
  Default → mocked via the `mock_llm` fixture below. Deterministic, free, fast.
  Opt-in real LLM → set `LIVE_LLM=1` in the environment. Costs money, may flake
  on non-deterministic responses; use only when the feature is specifically
  about LLM behavior (e.g. tool-call routing, prompt quality).

The 4 patterns a functional test typically uses (pick the smallest that
proves the feature works):

  1. Tool (no Temporal):
       tool = MyTool(workspace=tmp_path, ...)
       result = await tool.execute_with_context(ctx, **params)
       assert json.loads(result)["status"] == "ok"

  2. FastAPI endpoint:
       async with httpx.AsyncClient(app=app, base_url="http://test") as client:
           response = await client.post("/sessions/foo", json={...})
       assert response.status_code == 200

  3. Workflow (with mocked activities):
       async with WorkflowEnvironment.start_time_skipping() as env:
           async with Worker(env.client, ..., activities=[mock_llm_chat, ...]):
               handle = await env.client.start_workflow(MyWorkflow.run, ...)
               result = await handle.result()
       assert result.foo == expected

  4. Agent E2E ("usuario → LLM → tool → respuesta"):
       Same as #3, but with `mock_llm` set to return a tool-call envelope
       that triggers the real tool path, then assert on the agent's final
       reply.
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------
# Auto-mark every test in this directory with `@pytest.mark.functional`, the
# same trick conftest.py for `architecture/` uses. Lets the pipeline filter
# with `pytest -m functional` without each file declaring `pytestmark`.

def pytest_collection_modifyitems(
    config: pytest.Config,  # noqa: ARG001 — pytest hook signature
    items: list[pytest.Item],
) -> None:
    for item in items:
        # Only mark items whose nodeid is inside tests/functional/
        if "tests/functional/" in str(item.fspath):
            item.add_marker(pytest.mark.functional)


# ---------------------------------------------------------------------------
# LLM mock — default ON; bypass with LIVE_LLM=1 env var.
# ---------------------------------------------------------------------------
# We re-export `mock_llm_chat` from the existing fixtures module (single
# source of truth — don't duplicate the mock logic).

LIVE_LLM_ENV = "LIVE_LLM"


def _llm_is_live() -> bool:
    return os.environ.get(LIVE_LLM_ENV) == "1"


@pytest.fixture
def mock_llm():
    """Yields the canned-response mock activity used by Worker-based tests.

    Example:
        async with Worker(client, task_queue="q", workflows=[MyWf],
                          activities=[mock_llm, ...]):
            ...
    """
    if _llm_is_live():
        pytest.skip(
            f"{LIVE_LLM_ENV}=1 — skipping mock-LLM test. The live-LLM equivalent "
            f"should be written separately under tests/functional/live/."
        )
    from tests.fixtures.generate_fixtures import mock_llm_chat
    return mock_llm_chat


@pytest.fixture
def llm_mode() -> str:
    """`mock` or `live` — handy for tests that want to know which mode."""
    return "live" if _llm_is_live() else "mock"


# ---------------------------------------------------------------------------
# Temporal — fixture for tests that need a workflow environment.
# ---------------------------------------------------------------------------
# Costs ~1-2s per fixture call (time-skipping env startup). Scope="function"
# so each test gets an isolated env — slower but avoids flakiness from shared
# state when tests run in parallel.

@pytest.fixture
async def workflow_env() -> AsyncIterator[Any]:
    """Time-skipping Temporal env for workflow-level functional tests.

    Yields a `WorkflowEnvironment` instance. Worker registration + activity
    mocking is the test's responsibility (varies per workflow).
    """
    from temporalio.testing import WorkflowEnvironment
    async with await WorkflowEnvironment.start_time_skipping() as env:
        yield env


# ---------------------------------------------------------------------------
# HTTP — fixture for tests that hit the FastAPI app in-process.
# ---------------------------------------------------------------------------
# Uses httpx.AsyncClient against the FastAPI ASGI app directly — no uvicorn
# needed, no real port. ~10ms per request.

@pytest.fixture
async def api_client() -> AsyncIterator[Any]:
    """`httpx.AsyncClient` pre-wired to the dashboard FastAPI app."""
    import httpx
    from src.main import app  # type: ignore[attr-defined]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
