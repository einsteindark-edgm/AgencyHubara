"""Smoke A2 (HU-003) — verifica que TracingInterceptor produce spans anidados.

Corre un workflow trivial bajo WorkflowEnvironment con ConsoleSpanExporter.
Esperado en stdout: un span RunWorkflow y un span StartActivity:echo_activity
que comparten trace_id (activity anidado bajo el workflow). Sin red, sin SigNoz.

GOTCHA R-DET (descubierto por este smoke): el módulo que contiene un
`@workflow.defn` es re-importado por el sandbox de Temporal para validarlo. Si
importa `opentelemetry` a nivel módulo, el sandbox dispara
RestrictedWorkflowAccessError (random.getrandbits, usado para generar span IDs).
Por eso los imports de OTel van DENTRO de main() — igual que en los workers
reales, donde el workflow vive en su propio archivo sin imports de OTel. Además
el Worker marca `opentelemetry` como passthrough para el span-creation en runtime.

Uso:
    cd hubara_agency && PYTHONPATH=. uv run python scripts/otel_smoke.py
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import activity, workflow


@activity.defn
async def echo_activity(msg: str) -> str:
    return f"echo: {msg}"


@workflow.defn
class SmokeWorkflow:
    @workflow.run
    async def run(self, msg: str) -> str:
        return await workflow.execute_activity(
            echo_activity,
            msg,
            start_to_close_timeout=timedelta(seconds=10),
        )


async def main() -> None:
    # Imports de OTel acá adentro (no a nivel módulo) — ver docstring §GOTCHA.
    from opentelemetry import trace
    from temporalio.contrib.opentelemetry import TracingInterceptor
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from src.platform.observability import init_otel, otel_workflow_runner

    print("STEP-1: init_otel", flush=True)
    init_otel("otel-smoke")  # sin OTEL_EXPORTER_OTLP_ENDPOINT → ConsoleSpanExporter
    tracer = trace.get_tracer("otel.smoke")

    print("STEP-2: starting WorkflowEnvironment (puede bajar test-server)...", flush=True)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        print("STEP-3: WorkflowEnvironment listo", flush=True)
        async with Worker(
            env.client,
            task_queue="otel-smoke-q",
            workflows=[SmokeWorkflow],
            activities=[echo_activity],
            interceptors=[TracingInterceptor()],
            workflow_runner=otel_workflow_runner(),
        ):
            with tracer.start_as_current_span("smoke-parent"):
                result = await env.client.execute_workflow(
                    SmokeWorkflow.run,
                    "hola-otel",
                    id="otel-smoke-wf",
                    task_queue="otel-smoke-q",
                )
            print("WORKFLOW_RESULT:", result)

    provider = trace.get_tracer_provider()
    if hasattr(provider, "force_flush"):
        provider.force_flush()
    print("SMOKE_DONE")


if __name__ == "__main__":
    asyncio.run(main())
