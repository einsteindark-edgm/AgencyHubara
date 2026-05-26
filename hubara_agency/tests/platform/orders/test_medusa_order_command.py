"""Tests del `MedusaOrderCommand` adapter — respx mock contra el HTTP layer.

Cobertura:
  * `schedule_delivery` → transition new→preparing + scheduled fields.
  * `transition_stage` → drag-and-drop (preparing→ready).
  * `confirm_payment` → idempotente + sets flag.
  * `cancel_order` → desde cualquier stage no terminal.
  * Detection draft vs order (probar draft_ prefix vs order_ prefix).
  * Error paths: 404, 5xx, invalid transition.
"""
from __future__ import annotations

import pytest
import respx
from httpx import Response

from src.platform.medusa.client import HttpMedusaClient
from src.platform.orders.command_port import (
    CancelOrderCommand,
    ConfirmPaymentCommand,
    ScheduleDeliveryCommand,
    TransitionStageCommand,
)
from src.platform.orders.medusa_order_command import (
    MedusaOrderCommand,
    NoopOrderCommand,
)


_BASE_URL = "http://medusa.test"


@pytest.fixture
async def adapter():
    client = HttpMedusaClient(
        base_url=_BASE_URL, admin_token="sk_test", timeout=5.0
    )
    yield MedusaOrderCommand(client)
    await client.aclose()


# ----------------------------------------------------------------------
# schedule_delivery
# ----------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock(base_url=_BASE_URL)
async def test_schedule_delivery_converts_draft_and_patches_metadata(
    respx_mock, adapter
):
    """Full schedule flow:
      1. GET draft  → metadata actual.
      2. POST /admin/draft-orders/{id}/convert-to-order  → draft pasa a Order real.
      3. POST /admin/orders/{id}  → patch metadata con scheduled + transition.
    """
    draft_id = "draft_01HXX"
    # Step 1: GET /admin/orders/{id} devuelve el draft con status="draft"
    # (Medusa v2 retorna drafts via este endpoint también).
    respx_mock.get(
        f"/admin/orders/{draft_id}",
        params__contains={"fields": "id,metadata,total,payment_status,status"},
    ).mock(
        return_value=Response(
            200,
            json={
                "order": {
                    "id": draft_id,
                    "status": "draft",
                    "metadata": {
                        "session_key": "wa_57",
                        "hubara_stage": "new",
                        "hubara_stage_history": [
                            {"from": None, "to": "new", "at_ms": 100, "by": "agent"}
                        ],
                    },
                }
            },
        )
    )
    # Convert-to-order route (NUEVO): la draft pasa a Order real.
    convert_route = respx_mock.post(
        f"/admin/draft-orders/{draft_id}/convert-to-order"
    ).mock(
        return_value=Response(
            200,
            json={
                "order": {
                    "id": draft_id,
                    "status": "pending",  # ya no es draft
                    "payment_status": "not_paid",
                    "metadata": {
                        "session_key": "wa_57",
                        "hubara_stage": "new",
                    },
                }
            },
        )
    )
    # GET a /admin/orders/{id} (lo hace `patch_order_metadata` internamente
    # para hacer merge — necesita la metadata actual de la Order ya convertida).
    respx_mock.get(f"/admin/orders/{draft_id}").mock(
        return_value=Response(
            200,
            json={
                "order": {
                    "id": draft_id,
                    "metadata": {
                        "session_key": "wa_57",
                        "hubara_stage": "new",
                    },
                }
            },
        )
    )
    # POST a /admin/orders/{id} para el patch metadata (post-conversion).
    post_route = respx_mock.post(f"/admin/orders/{draft_id}").mock(
        return_value=Response(
            200,
            json={
                "order": {
                    "id": draft_id,
                    "metadata": {
                        "session_key": "wa_57",
                        "hubara_stage": "preparing",
                        "hubara_scheduled_delivery_iso": "2026-05-26",
                        "hubara_human_note": "antes 9am",
                    },
                }
            },
        )
    )

    cmd = ScheduleDeliveryCommand(
        order_id=draft_id,
        delivery_iso="2026-05-26",
        delivery_time="09:00",
        note="antes 9am",
    )
    result = await adapter.schedule_delivery(cmd)

    assert result.success is True
    assert result.current_stage == "preparing"
    # Se llamó convert-to-order (draft → order real).
    assert convert_route.call_count == 1
    # Se llamó el patch metadata sobre /admin/orders/ (NO /admin/draft-orders/).
    assert post_route.call_count == 1
    import json
    body = json.loads(post_route.calls[0].request.content)
    assert body["metadata"]["hubara_scheduled_delivery_iso"] == "2026-05-26"
    assert body["metadata"]["hubara_scheduled_delivery_time"] == "09:00"
    assert body["metadata"]["hubara_human_note"] == "antes 9am"
    assert body["metadata"]["hubara_stage"] == "preparing"
    # Preservó session_key (merge no destructivo)
    assert body["metadata"]["session_key"] == "wa_57"


# ----------------------------------------------------------------------
# transition_stage
# ----------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock(base_url=_BASE_URL)
async def test_transition_stage_drag_and_drop(respx_mock, adapter):
    """Drag and drop preparing → ready."""
    draft_id = "draft_01HXX"
    # Medusa v2 live: /admin/orders/{id} también devuelve drafts. Para
    # preservar la semántica de fallback antiguo en este test, devolvemos
    # 404 desde el endpoint orders → el adapter cae al endpoint draft-orders.
    respx_mock.get(f"/admin/orders/{draft_id}").mock(
        return_value=Response(404, json={"message": "not found"})
    )
    respx_mock.get(f"/admin/draft-orders/{draft_id}").mock(
        return_value=Response(
            200,
            json={
                "draft_order": {
                    "id": draft_id,
                    "metadata": {
                        "hubara_stage": "preparing",
                        "hubara_stage_history": [
                            {"from": None, "to": "new", "at_ms": 100, "by": "agent"},
                            {"from": "new", "to": "preparing", "at_ms": 200, "by": "human"},
                        ],
                    },
                }
            },
        )
    )
    post_route = respx_mock.post(f"/admin/draft-orders/{draft_id}").mock(
        return_value=Response(
            200,
            json={
                "draft_order": {
                    "id": draft_id,
                    "metadata": {"hubara_stage": "ready"},
                }
            },
        )
    )

    result = await adapter.transition_stage(
        TransitionStageCommand(order_id=draft_id, to_stage="ready")
    )

    assert result.success is True
    assert result.current_stage == "ready"
    import json
    body = json.loads(post_route.calls[0].request.content)
    assert body["metadata"]["hubara_stage"] == "ready"
    # History tiene 3 entries ahora
    assert len(body["metadata"]["hubara_stage_history"]) == 3


@pytest.mark.asyncio
@respx.mock(base_url=_BASE_URL)
async def test_transition_stage_rejects_invalid_skip(respx_mock, adapter):
    """new → delivered es inválido (no se puede saltar)."""
    draft_id = "draft_01HXX"
    # Medusa v2 live: /admin/orders/{id} también devuelve drafts. Para
    # preservar la semántica de fallback antiguo en este test, devolvemos
    # 404 desde el endpoint orders → el adapter cae al endpoint draft-orders.
    respx_mock.get(f"/admin/orders/{draft_id}").mock(
        return_value=Response(404, json={"message": "not found"})
    )
    respx_mock.get(f"/admin/draft-orders/{draft_id}").mock(
        return_value=Response(
            200, json={"draft_order": {"id": draft_id, "metadata": {"hubara_stage": "new"}}}
        )
    )

    result = await adapter.transition_stage(
        TransitionStageCommand(order_id=draft_id, to_stage="delivered")
    )

    assert result.success is False
    assert result.error_detail is not None
    assert result.error_detail.startswith("invalid_transition:")
    assert result.current_stage == "new"


# ----------------------------------------------------------------------
# confirm_payment
# ----------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock(base_url=_BASE_URL)
async def test_confirm_payment_registers_in_medusa_and_sets_flag(
    respx_mock, adapter
):
    """Full payment flow consistency (post Opción B, 2026-05-25):
      1. GET order (debe ser Order real, no draft).
      2. POST /admin/payment-collections con amount.
      3. POST /admin/payment-collections/{id}/mark-as-paid.
      4. GET order (interno del patch_order_metadata para merge).
      5. POST /admin/orders/{id} con metadata.hubara_payment_confirmed=true.
    """
    order_id = "order_01HXX"  # Order real (no draft_*)
    # Tenemos prefix `order_` → el adapter prueba /admin/orders primero.
    respx_mock.get(f"/admin/orders/{order_id}").mock(
        return_value=Response(
            200,
            json={
                "order": {
                    "id": order_id,
                    "metadata": {"hubara_stage": "preparing"},
                    "total": 17000,
                    "status": "pending",
                    "payment_status": "not_paid",
                }
            },
        )
    )
    pc_route = respx_mock.post("/admin/payment-collections").mock(
        return_value=Response(
            200,
            json={
                "payment_collection": {
                    "id": "pay_col_01HXX",
                    "status": "not_paid",
                    "amount": 17000,
                }
            },
        )
    )
    mark_paid_route = respx_mock.post(
        "/admin/payment-collections/pay_col_01HXX/mark-as-paid"
    ).mock(
        return_value=Response(
            200,
            json={
                "payment_collection": {
                    "id": "pay_col_01HXX",
                    "status": "completed",
                    "captured_amount": 17000,
                }
            },
        )
    )
    patch_route = respx_mock.post(f"/admin/orders/{order_id}").mock(
        return_value=Response(
            200,
            json={
                "order": {
                    "id": order_id,
                    "metadata": {
                        "hubara_stage": "preparing",
                        "hubara_payment_confirmed": True,
                    },
                }
            },
        )
    )

    result = await adapter.confirm_payment(
        ConfirmPaymentCommand(order_id=order_id)
    )

    assert result.success is True
    # Verificar que las 3 llamadas se hicieron en orden.
    assert pc_route.call_count == 1
    assert mark_paid_route.call_count == 1
    assert patch_route.call_count == 1

    import json
    pc_body = json.loads(pc_route.calls[0].request.content)
    assert pc_body == {"order_id": order_id, "amount": 17000}

    mp_body = json.loads(mark_paid_route.calls[0].request.content)
    assert mp_body == {"order_id": order_id}

    patch_body = json.loads(patch_route.calls[0].request.content)
    assert patch_body["metadata"]["hubara_payment_confirmed"] is True


@pytest.mark.asyncio
async def test_confirm_payment_rejects_draft_with_helpful_message(adapter):
    """Drafts no soportan payment registration en Medusa — el humano debe
    agendar primero (que convierte a Order real). Error claro, no crash."""
    draft_id = "draft_01HXX"
    with respx.mock(base_url=_BASE_URL, assert_all_called=False) as respx_mock:
        # /admin/orders/{id} devuelve el draft con status="draft"
        # (comportamiento Medusa v2 real).
        respx_mock.get(f"/admin/orders/{draft_id}").mock(
            return_value=Response(
                200,
                json={
                    "order": {
                        "id": draft_id,
                        "metadata": {"hubara_stage": "new"},
                        "total": 17000,
                        "status": "draft",
                    }
                },
            )
        )

        result = await adapter.confirm_payment(
            ConfirmPaymentCommand(order_id=draft_id)
        )

        assert result.success is False
        assert result.error_detail is not None
        assert result.error_detail.startswith("invalid_state:")
        assert "draft" in result.error_detail.lower()


@pytest.mark.asyncio
async def test_confirm_payment_idempotent_when_flag_already_set(adapter):
    """Si ya estaba confirmado, devuelve success sin tocar Medusa."""
    order_id = "order_01HXX"
    with respx.mock(base_url=_BASE_URL, assert_all_called=False) as respx_mock:
        respx_mock.get(f"/admin/orders/{order_id}").mock(
            return_value=Response(
                200,
                json={
                    "order": {
                        "id": order_id,
                        "metadata": {
                            "hubara_stage": "preparing",
                            "hubara_payment_confirmed": True,
                        },
                        "total": 17000,
                        "status": "pending",
                        "payment_status": "captured",
                    }
                },
            )
        )
        pc_route = respx_mock.post("/admin/payment-collections")
        patch_route = respx_mock.post(f"/admin/orders/{order_id}")

        result = await adapter.confirm_payment(
            ConfirmPaymentCommand(order_id=order_id)
        )

        assert result.success is True
        # NO se llamó a Medusa (ni payment-collections, ni patch metadata).
        assert pc_route.call_count == 0
        assert patch_route.call_count == 0


@pytest.mark.asyncio
@respx.mock(base_url=_BASE_URL)
async def test_confirm_payment_skips_medusa_if_already_captured(
    respx_mock, adapter
):
    """Edge: humano fue a Medusa Admin manualmente y registró pago, pero
    no apretó nuestro botón. Cuando aprete ahora, Medusa ya está captured —
    saltamos los POSTs de payment-collections y solo escribimos el flag."""
    order_id = "order_01HXX"
    respx_mock.get(f"/admin/orders/{order_id}").mock(
        return_value=Response(
            200,
            json={
                "order": {
                    "id": order_id,
                    "metadata": {"hubara_stage": "preparing"},
                    "total": 17000,
                    "status": "pending",
                    "payment_status": "captured",  # ya está pagada en Medusa
                }
            },
        )
    )
    patch_route = respx_mock.post(f"/admin/orders/{order_id}").mock(
        return_value=Response(
            200,
            json={
                "order": {
                    "id": order_id,
                    "metadata": {
                        "hubara_stage": "preparing",
                        "hubara_payment_confirmed": True,
                    },
                }
            },
        )
    )

    result = await adapter.confirm_payment(
        ConfirmPaymentCommand(order_id=order_id)
    )

    assert result.success is True
    # Patch se hizo (escribir el flag), pero NO se creó payment-collection.
    assert patch_route.call_count == 1


# ----------------------------------------------------------------------
# cancel_order
# ----------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock(base_url=_BASE_URL)
async def test_cancel_order_from_shipping(respx_mock, adapter):
    draft_id = "draft_01HXX"
    # Medusa v2 live: /admin/orders/{id} también devuelve drafts. Para
    # preservar la semántica de fallback antiguo en este test, devolvemos
    # 404 desde el endpoint orders → el adapter cae al endpoint draft-orders.
    respx_mock.get(f"/admin/orders/{draft_id}").mock(
        return_value=Response(404, json={"message": "not found"})
    )
    respx_mock.get(f"/admin/draft-orders/{draft_id}").mock(
        return_value=Response(
            200,
            json={
                "draft_order": {
                    "id": draft_id,
                    "metadata": {"hubara_stage": "shipping"},
                }
            },
        )
    )
    post_route = respx_mock.post(f"/admin/draft-orders/{draft_id}").mock(
        return_value=Response(
            200,
            json={
                "draft_order": {
                    "id": draft_id,
                    "metadata": {
                        "hubara_stage": "cancelled",
                        "hubara_cancelled_reason": "Devuelto",
                    },
                }
            },
        )
    )

    result = await adapter.cancel_order(
        CancelOrderCommand(order_id=draft_id, reason="Devuelto")
    )
    assert result.success is True
    assert result.current_stage == "cancelled"
    import json
    body = json.loads(post_route.calls[0].request.content)
    assert body["metadata"]["hubara_stage"] == "cancelled"
    assert body["metadata"]["hubara_cancelled_reason"] == "Devuelto"


@pytest.mark.asyncio
@respx.mock(base_url=_BASE_URL)
async def test_cancel_order_real_order_calls_medusa_hard_cancel(
    respx_mock, adapter
):
    """Bug fix 2026-05-26: cuando cancelamos una Order real (no draft) via
    dashboard, además de patch metadata debe llamar Medusa hard-cancel
    (`POST /admin/orders/{id}/cancel`). Sin esto, Medusa sigue con la order
    activa (inventario reservado, payment_collections vivas)."""
    order_id = "order_01XYZ"
    # GET inicial (en _patch) → order real (status="pending")
    respx_mock.get(f"/admin/orders/{order_id}").mock(
        return_value=Response(
            200,
            json={
                "order": {
                    "id": order_id,
                    "status": "pending",
                    "metadata": {"hubara_stage": "preparing"},
                }
            },
        )
    )
    # POST patch metadata
    respx_mock.post(f"/admin/orders/{order_id}").mock(
        return_value=Response(
            200,
            json={
                "order": {
                    "id": order_id,
                    "metadata": {
                        "hubara_stage": "cancelled",
                        "hubara_cancelled_reason": "Cliente arrepentido",
                    },
                }
            },
        )
    )
    # POST cancel — endpoint Medusa hard-cancel
    cancel_route = respx_mock.post(f"/admin/orders/{order_id}/cancel").mock(
        return_value=Response(
            200,
            json={"order": {"id": order_id, "status": "canceled"}},
        )
    )

    result = await adapter.cancel_order(
        CancelOrderCommand(order_id=order_id, reason="Cliente arrepentido")
    )
    assert result.success is True
    assert result.current_stage == "cancelled"
    # Verificamos que Medusa hard-cancel fue llamado.
    assert cancel_route.call_count == 1


@pytest.mark.asyncio
@respx.mock(base_url=_BASE_URL)
async def test_cancel_order_draft_does_NOT_call_medusa_cancel(
    respx_mock, adapter
):
    """Drafts: solo soft-cancel (metadata). NO llamar a Medusa cancel — el
    cancel de drafts es DELETE (destructivo) y queremos preservar la card
    en columna 'Cancelada' para que el operador pueda revisar."""
    draft_id = "draft_01ABC"
    # GET /admin/orders/{id} → draft (status="draft") — para _fetch_with_kind
    respx_mock.get(f"/admin/orders/{draft_id}").mock(
        return_value=Response(
            200,
            json={
                "order": {
                    "id": draft_id,
                    "status": "draft",
                    "metadata": {"hubara_stage": "new"},
                }
            },
        )
    )
    # GET /admin/draft-orders/{id} — `patch_draft_order_metadata` lee este
    # antes de hacer el merge-patch.
    respx_mock.get(f"/admin/draft-orders/{draft_id}").mock(
        return_value=Response(
            200,
            json={
                "draft_order": {
                    "id": draft_id,
                    "metadata": {"hubara_stage": "new"},
                }
            },
        )
    )
    # POST patch metadata sobre draft endpoint (porque is_draft=True)
    respx_mock.post(f"/admin/draft-orders/{draft_id}").mock(
        return_value=Response(
            200,
            json={
                "draft_order": {
                    "id": draft_id,
                    "metadata": {"hubara_stage": "cancelled"},
                }
            },
        )
    )
    # NO declaramos `/admin/orders/{draft_id}/cancel` — si el adapter lo
    # llama (bug), respx levanta AllMockedAssertionError, lo cual hace que
    # el test falle con mensaje claro. Garantía implícita.

    result = await adapter.cancel_order(
        CancelOrderCommand(order_id=draft_id, reason="Test")
    )
    assert result.success is True


@pytest.mark.asyncio
@respx.mock(base_url=_BASE_URL)
async def test_cancel_order_medusa_already_canceled_is_idempotent(
    respx_mock, adapter
):
    """Si Medusa devuelve 422 ("already canceled") en el hard-cancel, NO
    fallar — la metadata ya fue patcheada y el estado final es el deseado."""
    order_id = "order_already_cancelled"
    respx_mock.get(f"/admin/orders/{order_id}").mock(
        return_value=Response(
            200,
            json={
                "order": {
                    "id": order_id,
                    "status": "pending",
                    "metadata": {"hubara_stage": "preparing"},
                }
            },
        )
    )
    respx_mock.post(f"/admin/orders/{order_id}").mock(
        return_value=Response(
            200,
            json={
                "order": {
                    "id": order_id,
                    "metadata": {"hubara_stage": "cancelled"},
                }
            },
        )
    )
    # Medusa rechaza con 422 — orden ya estaba canceled
    respx_mock.post(f"/admin/orders/{order_id}/cancel").mock(
        return_value=Response(422, json={"message": "already canceled"})
    )

    result = await adapter.cancel_order(
        CancelOrderCommand(order_id=order_id)
    )
    # Debe ser idempotente — devuelve success igual.
    assert result.success is True
    assert result.current_stage == "cancelled"


# ----------------------------------------------------------------------
# Detection draft vs order
# ----------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock(base_url=_BASE_URL)
async def test_falls_back_to_order_endpoint_when_draft_404(
    respx_mock, adapter
):
    """Si el id no matchea draft (404), prueba el endpoint de orders."""
    order_id = "order_01HXX"  # prefix order_ → prefiere order primero
    respx_mock.get(f"/admin/orders/{order_id}").mock(
        return_value=Response(
            200,
            json={
                "order": {
                    "id": order_id,
                    "metadata": {"hubara_stage": "preparing"},
                }
            },
        )
    )
    post_route = respx_mock.post(f"/admin/orders/{order_id}").mock(
        return_value=Response(
            200,
            json={"order": {"id": order_id, "metadata": {"hubara_stage": "ready"}}},
        )
    )

    result = await adapter.transition_stage(
        TransitionStageCommand(order_id=order_id, to_stage="ready")
    )
    assert result.success is True
    assert post_route.call_count == 1


# ----------------------------------------------------------------------
# Error paths
# ----------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock(base_url=_BASE_URL)
async def test_not_found_returns_failure(respx_mock, adapter):
    """Si ambos endpoints devuelven 404 → success=False con not_found:."""
    bogus_id = "draft_does_not_exist"
    respx_mock.get(f"/admin/draft-orders/{bogus_id}").mock(
        return_value=Response(404, json={"message": "not found"})
    )
    respx_mock.get(f"/admin/orders/{bogus_id}").mock(
        return_value=Response(404, json={"message": "not found"})
    )

    result = await adapter.confirm_payment(
        ConfirmPaymentCommand(order_id=bogus_id)
    )
    assert result.success is False
    assert result.error_detail is not None
    assert result.error_detail.startswith("not_found:")


@pytest.mark.asyncio
@respx.mock(base_url=_BASE_URL)
async def test_medusa_500_returns_failure(respx_mock, adapter):
    draft_id = "draft_01HXX"
    # Medusa v2 live: /admin/orders/{id} también devuelve drafts. Para
    # preservar la semántica de fallback antiguo en este test, devolvemos
    # 404 desde el endpoint orders → el adapter cae al endpoint draft-orders.
    respx_mock.get(f"/admin/orders/{draft_id}").mock(
        return_value=Response(404, json={"message": "not found"})
    )
    respx_mock.get(f"/admin/draft-orders/{draft_id}").mock(
        return_value=Response(503, json={"message": "service unavailable"})
    )

    result = await adapter.confirm_payment(
        ConfirmPaymentCommand(order_id=draft_id)
    )
    assert result.success is False
    assert result.error_detail is not None
    assert "medusa_unavailable" in result.error_detail


# ----------------------------------------------------------------------
# NoopOrderCommand stub
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_noop_returns_unavailable_for_all_commands():
    """Cuando Medusa no está configurado, el stub devuelve mensaje claro."""
    noop = NoopOrderCommand()
    for cmd in [
        ScheduleDeliveryCommand(
            order_id="x", delivery_iso="2026-01-01", delivery_time=None, note=None
        ),
        TransitionStageCommand(order_id="x", to_stage="ready"),
        ConfirmPaymentCommand(order_id="x"),
        CancelOrderCommand(order_id="x"),
    ]:
        # cada port method tiene la misma signature de result
        op_name = type(cmd).__name__
        if "Schedule" in op_name:
            r = await noop.schedule_delivery(cmd)
        elif "Transition" in op_name:
            r = await noop.transition_stage(cmd)
        elif "ConfirmPayment" in op_name:
            r = await noop.confirm_payment(cmd)
        else:
            r = await noop.cancel_order(cmd)
        assert r.success is False
        assert r.error_detail is not None
        assert "medusa_unavailable" in r.error_detail
