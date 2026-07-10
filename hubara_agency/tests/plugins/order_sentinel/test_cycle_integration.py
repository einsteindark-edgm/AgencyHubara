"""Integración del ciclo order-sentinel a nivel ACTIVITIES REALES (gotcha #1
del repo: verificar comportamiento, no solo schema): vault sembrado con una
conversación HUMANO realista → build_snapshot (con enriquecimiento vía API
mockeada) produce EXACTAMENTE el contrato que consume el agente → un result
canned del agente (mismo shape que el golden de GraphAgents) → execute aplica
el PATCH exacto y avanza el watermark. Cubre el seam snapshot→agente→executor
de punta a punta sin Temporal ni la caja.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx
from temporalio.testing import ActivityEnvironment

from src.plugins.order_sentinel.agent.cycle.activities import (
    build_order_sentinel_snapshot_activity,
    execute_order_intents_activity,
)

BASE = "http://hubara-api.invalid"

# 2026-07-09T10:00:00Z / 11:00:00Z en epoch ms (los timestamps del historial).
_T_CLIENTE = int(datetime(2026, 7, 9, 10, tzinfo=timezone.utc).timestamp() * 1000)
_T_OPERADOR = int(datetime(2026, 7, 9, 11, tzinfo=timezone.utc).timestamp() * 1000)


def _seed_conversacion_pago(vault: Path) -> None:
    d = vault / "wa_573001112233"
    (d / "sessions").mkdir(parents=True)
    (d / "metadata.json").write_text(
        json.dumps(
            {
                "tag": "HUMANO",
                "episodes": [{"episode_id": "ep_1", "order_id": "order_01INT"}],
            }
        ),
        encoding="utf-8",
    )
    events = [
        {
            "role": "user",
            "content": "ya consigné, te mando el comprobante",
            "timestamp": "2026-07-09T10:00:00+00:00",
            "image_url": "media/comprobante.jpg",
        },
        {
            "role": "assistant",
            "sender": "human",
            "content": "perfecto, ya quedó confirmado tu pago",
            "timestamp": "2026-07-09T11:00:00+00:00",
        },
    ]
    (d / "sessions" / "wa_573001112233.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events),
        encoding="utf-8",
    )


@pytest.mark.asyncio
@respx.mock
async def test_ciclo_snapshot_agente_executor_de_punta_a_punta(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("HUBARA_API_BASE_URL", BASE)
    _seed_conversacion_pago(_isolate_vault_dir)
    respx.get(f"{BASE}/api/orders/orders/order_01INT").mock(
        return_value=httpx.Response(
            200,
            json={"summary": {"id": "order_01INT", "status": "preparing", "pay_status": "pending"}},
        )
    )

    # 1. El snapshot que se despacharía a la caja — el contrato EXACTO del agente.
    snapshot = await ActivityEnvironment().run(build_order_sentinel_snapshot_activity)
    (convo,) = snapshot["conversations"]
    assert convo["order_id"] == "order_01INT"
    assert convo["current_stage"] == "preparing"
    assert convo["payment_confirmed"] is False
    assert convo["messages"] == [
        {
            "who": "customer",
            "text": "ya consigné, te mando el comprobante",
            "at_ms": _T_CLIENTE,
            "has_media": True,
        },
        {
            "who": "human_operator",
            "text": "perfecto, ya quedó confirmado tu pago",
            "at_ms": _T_OPERADOR,
            "has_media": False,
        },
    ]
    assert snapshot["watermarks"] == {"wa_573001112233": _T_OPERADOR}

    # 2. El result del agente para ESE snapshot (mismo shape que el golden de
    #    GraphAgents: fixtures/cases/order-sentinel-pago-confirmado).
    agent_result = {
        "schema_version": 1,
        "snapshot_now_ms": snapshot["now_ms"],
        "dispatch": [
            {
                "kind": "order_stage_intent",
                "session_id": "wa_573001112233",
                "order_id": "order_01INT",
                "action": "confirm_payment",
                "evidence": ["perfecto, ya quedó confirmado tu pago"],
                "confidence": "high",
            }
        ],
        "suppressed": [],
        "llm_errors": [],
    }

    # 3. El executor aplica el intent y cierra el watermark.
    confirm = respx.patch(f"{BASE}/api/orders/orders/order_01INT/confirm-payment").mock(
        return_value=httpx.Response(
            200,
            json={"success": True, "order_id": "order_01INT", "current_stage": None,
                  "error_detail": None, "audit_id": "aud_9"},
        )
    )
    summary = await ActivityEnvironment().run(
        execute_order_intents_activity,
        agent_result["dispatch"],
        snapshot["watermarks"],
    )

    assert summary == {"applied": 1, "skipped": 0, "failed": 0}
    assert json.loads(confirm.calls.last.request.content) == {"by": "order-sentinel"}
    watermark = json.loads(
        (_isolate_vault_dir / "wa_573001112233" / "order_sentinel.json").read_text()
    )
    assert watermark == {"last_analyzed_at_ms": _T_OPERADOR}
