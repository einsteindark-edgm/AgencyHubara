"""Smoke test: el `WorkflowEnvironment.start_time_skipping` arranca, ejecuta un
workflow trivial registrado al vuelo y retorna su resultado. Sirve solo para verificar
que la infra de testing (pytest + pytest-asyncio + temporalio.testing) esta operativa.
"""
from __future__ import annotations

import uuid

from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker


@workflow.defn(name="SmokeEchoWorkflow")
class SmokeEchoWorkflow:
    @workflow.run
    async def run(self, value: str) -> str:
        return f"echo:{value}"


async def test_smoke_workflow_env_runs(temporal_env: WorkflowEnvironment) -> None:
    task_queue = f"smoke-{uuid.uuid4()}"
    async with Worker(
        temporal_env.client,
        task_queue=task_queue,
        workflows=[SmokeEchoWorkflow],
    ):
        result = await temporal_env.client.execute_workflow(
            SmokeEchoWorkflow.run,
            "hello",
            id=f"smoke-wf-{uuid.uuid4()}",
            task_queue=task_queue,
        )
    assert result == "echo:hello"
