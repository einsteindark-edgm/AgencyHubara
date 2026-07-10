"""Tests de `execute_order_intents_activity` (fase roja TDD).

Ejecuta los intents del agente order-sentinel contra la API HTTP de orders
(httpx + respx, endpoints REALES de src/plugins/orders/api):

  * action="transition"      → PATCH /api/orders/{id}/stage con
    notify_customer=false (el cliente YA fue avisado por el humano en el
    chat — la cascada ETA duplicaría el mensaje).
  * action="confirm_payment" → PATCH /api/orders/{id}/confirm-payment.

Semántica de resultado (shape write-side real: {success, order_id,
current_stage, error_detail, audit_id}):
  * success=False + error_detail "invalid_transition:..." → `skipped`
    (carrera benigna con el humano que ya movió la tarjeta), NO `failed`.
  * otros errores / HTTP error → `failed`.

Al cierre escribe el watermark de CADA sesión del snapshot
(`<vault>/<session>/order_sentinel.json`, key `last_analyzed_at_ms`) —
también para sesiones con intents fallidos o sin intents: el watermark es
"hasta dónde analicé", no "hasta dónde apliqué".
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from temporalio.testing import ActivityEnvironment

from src.plugins.order_sentinel.agent.cycle.activities import (
    execute_order_intents_activity,
)

BASE = "http://hubara-api.invalid"


def _ok(current_stage: str | None) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "success": True,
            "order_id": "order_A",
            "current_stage": current_stage,
            "error_detail": None,
            "audit_id": "aud_1",
        },
    )


def _transition_intent(**overrides) -> dict:
    intent = {
        "kind": "order_stage_intent",
        "session_id": "wa_1",
        "order_id": "order_A",
        "action": "transition",
        "to_stage": "shipping",
        "evidence": ["operador: 'ya salió con el mensajero'", "cliente: 'ok'"],
        "confidence": 0.9,
    }
    intent.update(overrides)
    return intent


@pytest.mark.asyncio
@respx.mock
async def test_transition_patchea_stage_sin_notificar_al_cliente(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("HUBARA_API_BASE_URL", BASE)
    route = respx.patch(f"{BASE}/api/orders/order_A/stage").mock(
        return_value=_ok("shipping")
    )

    result = await ActivityEnvironment().run(
        execute_order_intents_activity, [_transition_intent()], {"wa_1": 5_000}
    )

    assert result == {"applied": 1, "skipped": 0, "failed": 0}
    body = json.loads(route.calls.last.request.content)
    assert body == {
        "stage": "shipping",
        # nota de auditoría: la PRIMERA evidencia del intent.
        "note": "order-sentinel: operador: 'ya salió con el mensajero'",
        "by": "order-sentinel",
        "notify_customer": False,
    }


@pytest.mark.asyncio
@respx.mock
async def test_confirm_payment_patchea_endpoint_dedicado(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("HUBARA_API_BASE_URL", BASE)
    route = respx.patch(f"{BASE}/api/orders/order_A/confirm-payment").mock(
        return_value=_ok(None)
    )

    intent = _transition_intent(action="confirm_payment", to_stage=None)
    result = await ActivityEnvironment().run(
        execute_order_intents_activity, [intent], {"wa_1": 5_000}
    )

    assert result == {"applied": 1, "skipped": 0, "failed": 0}
    assert json.loads(route.calls.last.request.content) == {"by": "order-sentinel"}


@pytest.mark.asyncio
@respx.mock
async def test_invalid_transition_cuenta_como_skipped_no_failed(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    # Carrera benigna: el humano ya movió la tarjeta más allá. El endpoint
    # responde HTTP 200 con success=False + error_detail "invalid_transition:".
    monkeypatch.setenv("HUBARA_API_BASE_URL", BASE)
    respx.patch(f"{BASE}/api/orders/order_A/stage").mock(
        return_value=httpx.Response(
            200,
            json={
                "success": False,
                "order_id": "order_A",
                "current_stage": "delivered",
                "error_detail": "invalid_transition: delivered → shipping",
                "audit_id": None,
            },
        )
    )

    result = await ActivityEnvironment().run(
        execute_order_intents_activity, [_transition_intent()], {"wa_1": 5_000}
    )
    assert result == {"applied": 0, "skipped": 1, "failed": 0}


@pytest.mark.asyncio
@respx.mock
async def test_invalid_state_de_draft_cuenta_como_skipped(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    """PM-011: confirm_payment sobre un DRAFT devuelve `invalid_state:` (no
    invalid_transition) — también es benigno (el humano agenda después), no
    debe contar como failed recurrente."""
    monkeypatch.setenv("HUBARA_API_BASE_URL", BASE)
    respx.patch(f"{BASE}/api/orders/draft_R1/confirm-payment").mock(
        return_value=httpx.Response(
            200,
            json={
                "success": False,
                "order_id": "draft_R1",
                "current_stage": None,
                "error_detail": "invalid_state: draft orders cannot confirm payment",
                "audit_id": None,
            },
        )
    )
    intent = _transition_intent(
        action="confirm_payment", to_stage=None, order_id="draft_R1"
    )
    result = await ActivityEnvironment().run(
        execute_order_intents_activity, [intent], {"wa_1": 5_000}
    )
    assert result == {"applied": 0, "skipped": 1, "failed": 0}


@pytest.mark.asyncio
@respx.mock
async def test_nota_de_auditoria_se_sanea_y_trunca(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    """PM-020: la evidencia es texto arbitrario del cliente/LLM — a la note de
    la orden va saneada: str(), whitespace colapsado y máx ~200 chars."""
    monkeypatch.setenv("HUBARA_API_BASE_URL", BASE)
    route = respx.patch(f"{BASE}/api/orders/order_A/stage").mock(
        return_value=_ok("shipping")
    )
    evil = "ya  salió\nel pedido\t" + "x" * 500
    intent = _transition_intent(evidence=[evil])
    await ActivityEnvironment().run(
        execute_order_intents_activity, [intent], {"wa_1": 5_000}
    )
    note = json.loads(route.calls.last.request.content)["note"]
    assert note.startswith("order-sentinel: ya salió el pedido x")
    assert "\n" not in note and "\t" not in note
    assert len(note) <= 220


@pytest.mark.asyncio
@respx.mock
async def test_order_id_raro_viaja_url_quoted(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    """PM-023: un order_id con caracteres de ruta no rompe la URL."""
    monkeypatch.setenv("HUBARA_API_BASE_URL", BASE)
    route = respx.patch(f"{BASE}/api/orders/order_a%2Fb/stage").mock(
        return_value=_ok("shipping")
    )
    intent = _transition_intent(order_id="order_a/b")
    result = await ActivityEnvironment().run(
        execute_order_intents_activity, [intent], {"wa_1": 5_000}
    )
    assert result == {"applied": 1, "skipped": 0, "failed": 0}
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_error_http_cuenta_como_failed(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("HUBARA_API_BASE_URL", BASE)
    respx.patch(f"{BASE}/api/orders/order_A/stage").mock(
        return_value=httpx.Response(500, json={"detail": "boom"})
    )

    result = await ActivityEnvironment().run(
        execute_order_intents_activity, [_transition_intent()], {"wa_1": 5_000}
    )
    assert result == {"applied": 0, "skipped": 0, "failed": 1}


@pytest.mark.asyncio
@respx.mock
async def test_escribe_watermarks_de_todas_las_sesiones_incluso_fallidas(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    # wa_a: su intent FALLA (500). wa_b: no tiene intents (el agente no vio
    # nada accionable). AMBAS quedan con watermark escrito — es "hasta dónde
    # analicé", no depende del resultado de los PATCH.
    monkeypatch.setenv("HUBARA_API_BASE_URL", BASE)
    (_isolate_vault_dir / "wa_a").mkdir(parents=True)
    (_isolate_vault_dir / "wa_b").mkdir(parents=True)
    respx.patch(f"{BASE}/api/orders/order_A/stage").mock(
        return_value=httpx.Response(500, json={"detail": "boom"})
    )

    result = await ActivityEnvironment().run(
        execute_order_intents_activity,
        [_transition_intent(session_id="wa_a")],
        {"wa_a": 1_111, "wa_b": 2_222},
    )

    assert result == {"applied": 0, "skipped": 0, "failed": 1}
    wm_a = json.loads(
        (_isolate_vault_dir / "wa_a" / "order_sentinel.json").read_text()
    )
    wm_b = json.loads(
        (_isolate_vault_dir / "wa_b" / "order_sentinel.json").read_text()
    )
    assert wm_a == {"last_analyzed_at_ms": 1_111}
    assert wm_b == {"last_analyzed_at_ms": 2_222}
