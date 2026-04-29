"""Pytest fixtures comunes a toda la suite del refactor DEHA.

`temporal_env` arranca un `WorkflowEnvironment.start_time_skipping` por test que lo
solicite. Es el unico modo seguro de testear workflows sin depender de un cluster
Temporal real (ADR-005).
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from temporalio.testing import WorkflowEnvironment


@pytest_asyncio.fixture
async def temporal_env() -> AsyncIterator[WorkflowEnvironment]:
    env = await WorkflowEnvironment.start_time_skipping()
    try:
        yield env
    finally:
        await env.shutdown()
