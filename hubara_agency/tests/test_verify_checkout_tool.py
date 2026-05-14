"""Tests de `VerifyOrderForCheckoutTool` con verifier fake.

Cubre los 3 caminos del envelope que el LLM ve:
  1. Verified OK (sin discrepancia)  -> el LLM cierra normal.
  2. Verified con discrepancia       -> el LLM avisa al cliente.
  3. catalog_unavailable             -> el LLM debe escalar.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog.checkout_port import (
    CheckoutItem,
    CheckoutVerification,
    VerifiedItem,
)
from src.sales_whatsapp.tools.checkout import VerifyOrderForCheckoutTool


def _ctx(session: str) -> ToolContext:
    return ToolContext(session_key=session, channel="whatsapp", chat_id=session)


class FakeVerifier:
    def __init__(self, result: CheckoutVerification) -> None:
        self._result = result
        self.calls: list[list[CheckoutItem]] = []

    async def verify_items(
        self, items: list[CheckoutItem]
    ) -> CheckoutVerification:
        self.calls.append(items)
        return self._result


@pytest.mark.asyncio
async def test_verify_happy_path_returns_verified_true(tmp_path: Path) -> None:
    verifier = FakeVerifier(
        CheckoutVerification(
            verified=True,
            catalog_available=True,
            items=[
                VerifiedItem(
                    handle="plegaria-de-luz",
                    title="Plegaria de Luz",
                    snapshot_price="29000",
                    live_price="29000",
                    currency="cop",
                    in_stock=True,
                    discrepancy=False,
                )
            ],
        )
    )
    tool = VerifyOrderForCheckoutTool(workspace=str(tmp_path), verifier=verifier)

    raw = await tool.execute_with_context(
        _ctx("wa_A"),
        items=[{"handle": "plegaria-de-luz", "quantity": 2}],
    )
    payload = json.loads(raw)

    assert payload["verified"] is True
    assert payload["discrepancy"] is False
    assert len(payload["items"]) == 1
    assert payload["items"][0]["handle"] == "plegaria-de-luz"
    assert "Verificación OK" in payload["message"]

    # El verifier vio las cantidades correctas.
    assert verifier.calls == [
        [CheckoutItem(handle="plegaria-de-luz", quantity=2)]
    ]


@pytest.mark.asyncio
async def test_verify_with_discrepancy_emits_advisory_message(tmp_path: Path) -> None:
    verifier = FakeVerifier(
        CheckoutVerification(
            verified=False,
            catalog_available=True,
            items=[
                VerifiedItem(
                    handle="plegaria-de-luz",
                    title="Plegaria de Luz",
                    snapshot_price="29000",
                    live_price="30000",
                    currency="cop",
                    in_stock=True,
                    discrepancy=True,
                )
            ],
        )
    )
    tool = VerifyOrderForCheckoutTool(workspace=str(tmp_path), verifier=verifier)

    raw = await tool.execute_with_context(
        _ctx("wa_B"),
        items=[{"handle": "plegaria-de-luz", "quantity": 1}],
    )
    payload = json.loads(raw)

    assert payload["verified"] is False
    assert payload["discrepancy"] is True
    assert payload["items"][0]["snapshot_price"] == "29000"
    assert payload["items"][0]["live_price"] == "30000"
    # El message instruye al LLM a avisar honestamente al cliente.
    assert "honestidad" in payload["message"] or "honest" in payload["message"].lower()


@pytest.mark.asyncio
async def test_verify_catalog_unavailable_returns_error_envelope(tmp_path: Path) -> None:
    verifier = FakeVerifier(
        CheckoutVerification(
            verified=False,
            catalog_available=False,
            items=[],
            error_detail="medusa_unavailable: Connection timeout",
        )
    )
    tool = VerifyOrderForCheckoutTool(workspace=str(tmp_path), verifier=verifier)

    raw = await tool.execute_with_context(
        _ctx("wa_C"),
        items=[{"handle": "cualquier", "quantity": 1}],
    )
    payload = json.loads(raw)

    assert payload.get("error") == "catalog_unavailable"
    assert "CHECKOUT_VERIFY_FAILED" in payload["message"]


@pytest.mark.asyncio
async def test_verify_multiple_items_with_mixed_discrepancy(tmp_path: Path) -> None:
    """Un solo item con discrepancia hace que `discrepancy=True` globalmente."""
    verifier = FakeVerifier(
        CheckoutVerification(
            verified=False,
            catalog_available=True,
            items=[
                VerifiedItem(
                    handle="plegaria-de-luz",
                    title="Plegaria de Luz",
                    snapshot_price="29000",
                    live_price="29000",
                    currency="cop",
                    in_stock=True,
                    discrepancy=False,
                ),
                VerifiedItem(
                    handle="corona-de-redencion",
                    title="Corona de Redencion",
                    snapshot_price="35000",
                    live_price="38000",
                    currency="cop",
                    in_stock=True,
                    discrepancy=True,
                ),
            ],
        )
    )
    tool = VerifyOrderForCheckoutTool(workspace=str(tmp_path), verifier=verifier)

    raw = await tool.execute_with_context(
        _ctx("wa_D"),
        items=[
            {"handle": "plegaria-de-luz", "quantity": 1},
            {"handle": "corona-de-redencion", "quantity": 1},
        ],
    )
    payload = json.loads(raw)
    assert payload["verified"] is False
    assert payload["discrepancy"] is True
    assert len(payload["items"]) == 2
