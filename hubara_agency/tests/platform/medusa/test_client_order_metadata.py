"""`patch_order_metadata`: merge-patch de metadata en órdenes NO-draft.

Caso real (backfill 2026-07-09): las 13 órdenes históricas ya fueron
CONVERTIDAS (draft → order) y `/admin/draft-orders/{id}` da 404 — el patch
debe ir por `/admin/orders/{id}` (Medusa v2 update order acepta `metadata`).
Mismo contrato shallow-merge que `patch_draft_order_metadata`: las keys del
patch reemplazan, las existentes se preservan.
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from src.platform.medusa.client import HttpMedusaClient


@pytest.mark.asyncio
async def test_patch_order_metadata_merges_over_existing():
    c = HttpMedusaClient(base_url="https://m.test", admin_token="sk_xyz")
    with respx.mock(base_url="https://m.test") as r:
        r.get("/admin/orders/order_1").mock(
            return_value=httpx.Response(
                200,
                json={"order": {"id": "order_1",
                                "metadata": {"session_key": "wa_1", "total_cop": 17000}}},
            )
        )
        post_route = r.post("/admin/orders/order_1").mock(
            return_value=httpx.Response(200, json={"order": {"id": "order_1"}})
        )
        await c.patch_order_metadata(
            "order_1",
            {"meta_campaign_id": "c-1", "attribution_backfilled": "seeded"},
        )
        body = json.loads(post_route.calls[0].request.content)
        # shallow merge: lo viejo se preserva, el patch se suma
        assert body["metadata"] == {
            "session_key": "wa_1",
            "total_cop": 17000,
            "meta_campaign_id": "c-1",
            "attribution_backfilled": "seeded",
        }
    await c.aclose()
