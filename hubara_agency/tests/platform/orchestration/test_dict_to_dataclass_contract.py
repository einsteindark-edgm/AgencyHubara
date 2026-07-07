"""Contract test (marked ``functional`` — slow, uses Temporal in-process env):
``dispatch_event_activity`` produces a dict that Temporal materializes back
into the target workflow's input dataclass at the worker boundary.

Marked ``functional`` because ``WorkflowEnvironment.start_time_skipping()``
downloads the Temporal test server binary on first run and takes >2min in
cold-cache. The fast-lane CI runs ``pytest -m 'not functional'`` and skips
this. Full CI runs everything.

This is the CRITICAL contract behind ADR-2026-05-20 declarative orchestration:
the dispatcher does not know the target's concrete input type — it passes a
plain dict. Temporal's default ``DataConverter`` is responsible for
deserialization on the worker side, using the type hint of ``@workflow.run``.

The premortem of commit eb27473 flagged this as the #1 risk. This test
verifies the contract end-to-end with Temporal's in-process test environment.

If this test ever starts failing, EITHER:
  - Temporal Python SDK changed its DataConverter behavior, OR
  - Someone added a non-default field to a dispatch target's input dataclass
    without updating the `input_mapping` of the transitions that point at it.

In both cases: NO MERGES sin revisar. La arquitectura Level 3 depende de
este contrato.

Note: Temporal forbids ``@workflow.defn`` on locally-scoped classes
(qualname containing ``<locals>``), so the test workflows must live at
module level — even though they're only used inside one test each.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker


@dataclass
class _SimulatedInput:
    """Mirror of ``RemarketingSessionInput`` shape (without importing it to
    keep this test self-contained — we're testing Temporal's behavior, not
    chats-specific code)."""

    session_id: str
    motivo: str = ""
    runtime_workspace_path: str | None = None


@workflow.defn(name="ContractTestWorkflow")
class _ContractTestWorkflow:
    @workflow.run
    async def run(self, input: _SimulatedInput) -> dict:
        # We want to observe what Temporal handed us — a dataclass instance
        # built from the dict the client passed.
        return {
            "type": type(input).__name__,
            "session_id": getattr(input, "session_id", None),
            "motivo": getattr(input, "motivo", None),
            "runtime_workspace_path": getattr(input, "runtime_workspace_path", "MISSING"),
            "is_dataclass": hasattr(input, "__dataclass_fields__"),
        }


@dataclass
class _StrictInput:
    """All-required fields — used to verify that missing fields fail loudly."""

    session_id: str
    required_field: str  # no default


@workflow.defn(name="StrictContractWorkflow")
class _StrictWorkflow:
    @workflow.run
    async def run(self, input: _StrictInput) -> str:
        return input.session_id


@pytest.mark.functional
@pytest.mark.asyncio
async def test_dict_input_materializes_into_target_dataclass():
    """The dispatcher passes a dict; Temporal reconstructs the dataclass.

    This is the cornerstone of Level 3 — without this, the dispatcher would
    have to import the target's input dataclass (R-DIP #10 violation).
    """
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="contract-q",
            workflows=[_ContractTestWorkflow],
        ):
            # Pass a dict (mimicking what dispatch_event_activity does internally).
            result = await env.client.execute_workflow(
                "ContractTestWorkflow",
                {"session_id": "abc", "motivo": "test"},  # Only 2 of 3 fields
                id="contract-1",
                task_queue="contract-q",
            )

    assert result["type"] == "_SimulatedInput", (
        f"Temporal did NOT materialize the dataclass — got {result['type']!r}. "
        f"The dispatcher → worker contract is broken; "
        f"check Temporal Python SDK version and DataConverter config."
    )
    assert result["is_dataclass"] is True
    assert result["session_id"] == "abc"
    assert result["motivo"] == "test"
    # Field not in dict → dataclass default kicks in.
    assert result["runtime_workspace_path"] is None


@pytest.mark.functional
@pytest.mark.asyncio
async def test_dict_missing_required_field_fails_loudly():
    """If the dispatcher's input_mapping omits a required field of the target
    dataclass (no default), Temporal must fail loudly — not silently.

    Catches the "renamed/added a required field without updating
    input_mapping" footgun. This is critical for the architecture: the
    dispatcher operates blindly on dicts; the only guard rail is Temporal
    raising at the boundary.
    """
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="strict-q",
            workflows=[_StrictWorkflow],
            # Sin esto el decode error es un workflow TASK failure y Temporal
            # lo REINTENTA para siempre (non_retryable=false) → execute_workflow
            # nunca retorna y el test cuelga la suite entera (caso 2026-07-06:
            # "regresiones locales corriendo horas"). Con RuntimeError listado,
            # el "Failed decoding arguments" falla el WORKFLOW y el cliente
            # recibe la excepción — que es exactamente el contrato "falla
            # ruidoso en el boundary" que este test asierta.
            workflow_failure_exception_types=[RuntimeError],
        ):
            # Omit required_field — Temporal must reject this.
            with pytest.raises(Exception) as exc_info:
                await env.client.execute_workflow(
                    "StrictContractWorkflow",
                    {"session_id": "abc"},  # missing required_field
                    id="strict-1",
                    task_queue="strict-q",
                )
            # Temporal wraps the TypeError as WorkflowFailureError →
            # ApplicationError("Failed decoding arguments") → ApplicationError
            # con el TypeError original. El detalle del campo faltante vive en
            # la CADENA de causas (str(top-level) es solo "Workflow execution
            # failed") — recorremos __cause__ para asertar que el fallo es
            # ruidoso y nombra el problema, no un modo de falla silencioso.
            chain: list[str] = []
            err: BaseException | None = exc_info.value
            while err is not None:
                chain.append(str(err))
                err = err.__cause__
            error_msg = " | ".join(chain)
            assert (
                "required_field" in error_msg
                or "missing" in error_msg.lower()
                or "argument" in error_msg.lower()
                or "positional" in error_msg.lower()
            ), (
                f"Expected loud failure mentioning missing field; got: {error_msg}"
            )
