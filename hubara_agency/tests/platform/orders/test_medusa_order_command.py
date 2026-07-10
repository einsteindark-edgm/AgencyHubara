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

import json

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
    apply_order_cancellation_to_chat_metadata,
    apply_payment_confirmation_to_chat_metadata,
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


@pytest.mark.asyncio
@respx.mock(base_url=_BASE_URL)
async def test_schedule_delivery_rejects_advanced_stage(respx_mock, adapter):
    """Premortem PM-005: agendar sobre ready/shipping/delivered/cancelled se
    rechaza con `invalid_state` — `build_schedule_patch` documenta que "el
    caller debe validar antes", pero sin este guard el patch aplicaba la
    fecha IGUAL (sin transición) sobre una orden ya entregada, pisando
    `hubara_scheduled_delivery_iso` y disparando la cascada ETA.

    Sin rutas convert/patch mockeadas: si el código intentara escribir,
    respx falla el test (garantiza cero side effects).
    """
    order_id = "order_01DELIVERED"
    respx_mock.get(f"/admin/orders/{order_id}").mock(
        return_value=Response(
            200,
            json={
                "order": {
                    "id": order_id,
                    "status": "pending",
                    "metadata": {
                        "hubara_stage": "delivered",
                        "hubara_scheduled_delivery_iso": "2026-07-01",
                    },
                }
            },
        )
    )

    result = await adapter.schedule_delivery(
        ScheduleDeliveryCommand(
            order_id=order_id,
            delivery_iso="2026-07-20",
            delivery_time=None,
            note=None,
        )
    )

    assert result.success is False
    assert "invalid_state" in (result.error_detail or "")
    assert result.current_stage == "delivered"


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


# ----------------------------------------------------------------------
# display_id resolution (premortem A1 extended to command path)
# ----------------------------------------------------------------------
#
# Bug 2026-05-26: el frontend manda `order.id = "#2"` (display_id) porque el
# entity layer mapea `id: s.display_id`. El query port ya resolvía display_id
# desde el get endpoint (premortem A1). El command port no — `httpx` trata el
# '#' como URL fragment marker, así que `/admin/orders/#2` se convertía
# silenciosamente en `/admin/orders/` (list endpoint) y `data["order"]` fallaba
# con KeyError → 500. El usuario veía un error de CORS (porque el 500 no
# llevaba headers CORS), pero la raíz era el 500.
#
# Estos tests verifican que las 4 operaciones del command port aceptan
# display_id ("#2" o "2") y resuelven a backend_id ANTES de cualquier llamada
# Medusa con el id crudo.


@pytest.mark.asyncio
@respx.mock(base_url=_BASE_URL)
async def test_schedule_delivery_accepts_display_id(respx_mock, adapter):
    """Frontend manda '#2' → adapter resuelve via /admin/orders list-scan,
    convierte el draft, patcha metadata. Sin la resolución, httpx hubiera
    interpretado '#' como fragment y la request hubiera ido a la list endpoint.
    """
    display_id = "#2"
    backend_id = "order_01HXXBACKEND"
    # Step 1: resolución display_id → backend_id via /admin/orders list-scan.
    respx_mock.get("/admin/orders", params__contains={"limit": 50}).mock(
        return_value=Response(
            200,
            json={
                "orders": [
                    {"id": "order_otro", "display_id": 1},
                    {"id": backend_id, "display_id": 2},
                ]
            },
        )
    )
    # Step 2: _fetch_with_kind → GET /admin/orders/{backend_id}.
    respx_mock.get(f"/admin/orders/{backend_id}").mock(
        return_value=Response(
            200,
            json={
                "order": {
                    "id": backend_id,
                    "status": "draft",
                    "metadata": {
                        "hubara_stage": "new",
                        "hubara_stage_history": [
                            {"from": None, "to": "new", "at_ms": 100, "by": "agent"}
                        ],
                    },
                }
            },
        )
    )
    # Step 3: convert-to-order DEBE usar backend_id, no display_id.
    convert_route = respx_mock.post(
        f"/admin/draft-orders/{backend_id}/convert-to-order"
    ).mock(
        return_value=Response(
            200,
            json={"order": {"id": backend_id, "status": "pending", "metadata": {}}},
        )
    )
    # Step 4: patch_order_metadata (POST /admin/orders/{backend_id}) — internamente
    # hace un GET previo para merge.
    post_route = respx_mock.post(f"/admin/orders/{backend_id}").mock(
        return_value=Response(
            200,
            json={
                "order": {
                    "id": backend_id,
                    "metadata": {
                        "hubara_stage": "preparing",
                        "hubara_scheduled_delivery_iso": "2026-05-30",
                    },
                }
            },
        )
    )

    result = await adapter.schedule_delivery(
        ScheduleDeliveryCommand(
            order_id=display_id,
            delivery_iso="2026-05-30",
            delivery_time="09:00",
            note=None,
        )
    )

    assert result.success is True
    assert result.current_stage == "preparing"
    # El response preserva el display_id que mandó el frontend (para que la
    # invalidation de TanStack matchee el query key).
    assert result.order_id == display_id
    # Las llamadas a Medusa fueron con backend_id, no display_id.
    assert convert_route.call_count == 1
    assert post_route.call_count == 1


@pytest.mark.asyncio
@respx.mock(base_url=_BASE_URL)
async def test_transition_stage_accepts_display_id(respx_mock, adapter):
    """Drag-and-drop manda '#3' → resuelve → patcha. Sin la resolución, httpx
    fragment habría tirado 500.
    """
    display_id = "#3"
    backend_id = "order_01HXXOTRO"
    respx_mock.get("/admin/orders", params__contains={"limit": 50}).mock(
        return_value=Response(
            200,
            json={
                "orders": [
                    {"id": backend_id, "display_id": 3},
                ]
            },
        )
    )
    respx_mock.get(f"/admin/orders/{backend_id}").mock(
        return_value=Response(
            200,
            json={
                "order": {
                    "id": backend_id,
                    "status": "pending",  # Order real (no draft)
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
    post_route = respx_mock.post(f"/admin/orders/{backend_id}").mock(
        return_value=Response(
            200,
            json={
                "order": {"id": backend_id, "metadata": {"hubara_stage": "ready"}}
            },
        )
    )

    result = await adapter.transition_stage(
        TransitionStageCommand(order_id=display_id, to_stage="ready")
    )

    assert result.success is True
    assert result.current_stage == "ready"
    assert post_route.call_count == 1


@pytest.mark.asyncio
@respx.mock(base_url=_BASE_URL)
async def test_cancel_order_accepts_display_id(respx_mock, adapter):
    """Cancel manda '#4' → resuelve → patcha metadata + hard-cancel Medusa."""
    display_id = "#4"
    backend_id = "order_01HXXCANCEL"
    # list-scan retorna el match.
    respx_mock.get("/admin/orders", params__contains={"limit": 50}).mock(
        return_value=Response(
            200,
            json={
                "orders": [
                    {"id": backend_id, "display_id": 4},
                ]
            },
        )
    )
    # GET para _fetch_with_kind (se llama 2 veces: dentro de _patch y después
    # de _patch para decidir hard-cancel).
    respx_mock.get(f"/admin/orders/{backend_id}").mock(
        return_value=Response(
            200,
            json={
                "order": {
                    "id": backend_id,
                    "status": "pending",  # Order real (no draft) → hard-cancel
                    "metadata": {"hubara_stage": "preparing"},
                }
            },
        )
    )
    # Patch metadata (soft-cancel).
    patch_route = respx_mock.post(f"/admin/orders/{backend_id}").mock(
        return_value=Response(
            200,
            json={
                "order": {
                    "id": backend_id,
                    "metadata": {"hubara_stage": "cancelled"},
                }
            },
        )
    )
    # Hard-cancel — DEBE recibir backend_id, no display_id.
    hard_cancel_route = respx_mock.post(
        f"/admin/orders/{backend_id}/cancel"
    ).mock(
        return_value=Response(
            200, json={"order": {"id": backend_id, "status": "canceled"}}
        )
    )

    result = await adapter.cancel_order(
        CancelOrderCommand(order_id=display_id, reason="cliente arrepentido")
    )

    assert result.success is True
    assert result.current_stage == "cancelled"
    assert patch_route.call_count == 1
    assert hard_cancel_route.call_count == 1


@pytest.mark.asyncio
@respx.mock(base_url=_BASE_URL)
async def test_confirm_payment_accepts_display_id(respx_mock, adapter):
    """Confirm-payment manda '#5' → resuelve → registra payment en Medusa +
    patcha flag local.
    """
    display_id = "#5"
    backend_id = "order_01HXXPAYME"
    respx_mock.get("/admin/orders", params__contains={"limit": 50}).mock(
        return_value=Response(
            200,
            json={
                "orders": [
                    {"id": backend_id, "display_id": 5},
                ]
            },
        )
    )
    respx_mock.get(f"/admin/orders/{backend_id}").mock(
        return_value=Response(
            200,
            json={
                "order": {
                    "id": backend_id,
                    "status": "pending",
                    "metadata": {"hubara_stage": "preparing"},
                    "total": 17000,
                    "payment_status": "not_paid",
                }
            },
        )
    )
    # Crear payment collection — DEBE recibir backend_id.
    pc_route = respx_mock.post("/admin/payment-collections").mock(
        return_value=Response(
            200, json={"payment_collection": {"id": "pc_01HXX"}}
        )
    )
    # mark-as-paid.
    mark_paid_route = respx_mock.post(
        "/admin/payment-collections/pc_01HXX/mark-as-paid"
    ).mock(
        return_value=Response(
            200, json={"payment_collection": {"id": "pc_01HXX", "status": "captured"}}
        )
    )
    # Patch del flag.
    patch_route = respx_mock.post(f"/admin/orders/{backend_id}").mock(
        return_value=Response(
            200,
            json={
                "order": {
                    "id": backend_id,
                    "metadata": {
                        "hubara_stage": "preparing",
                        "hubara_payment_confirmed": True,
                    },
                }
            },
        )
    )

    result = await adapter.confirm_payment(
        ConfirmPaymentCommand(order_id=display_id)
    )

    assert result.success is True
    assert pc_route.call_count == 1
    assert mark_paid_route.call_count == 1
    assert patch_route.call_count == 1
    # Body de create_payment_collection llevó backend_id (no '#5').
    import json
    pc_body = json.loads(pc_route.calls[0].request.content)
    assert pc_body["order_id"] == backend_id


@pytest.mark.asyncio
@respx.mock(base_url=_BASE_URL)
async def test_display_id_not_resolvable_returns_not_found(respx_mock, adapter):
    """Si display_id no aparece en /admin/orders ni /admin/draft-orders →
    not_found, NO 500. Caso edge: el operador clickea una card vieja que
    Medusa ya borró.
    """
    display_id = "#999"
    respx_mock.get("/admin/orders", params__contains={"limit": 50}).mock(
        return_value=Response(200, json={"orders": []})
    )
    respx_mock.get("/admin/draft-orders", params__contains={"limit": 50}).mock(
        return_value=Response(200, json={"draft_orders": []})
    )

    result = await adapter.schedule_delivery(
        ScheduleDeliveryCommand(
            order_id=display_id,
            delivery_iso="2026-05-30",
            delivery_time=None,
            note=None,
        )
    )

    assert result.success is False
    assert result.error_detail is not None
    assert result.error_detail.startswith("not_found:")


# ----------------------------------------------------------------------
# Cross-system sync: confirm_payment → chat metadata
# ----------------------------------------------------------------------
#
# HU "verificación humana de pago": después de que un humano confirma el
# pago desde el dashboard de orders, el `metadata.json` del chat debe
# reflejar el cierre con tag COMPRA_EXITOSA. Esto cierra el loop entre
# ambos sistemas (orders + chats inbox) para que la información concuerde
# en todos lados.


def test_apply_payment_confirmation_marks_tag_and_appends_history():
    """Helper puro: chat con CONFIRMADO_PAGO_PENDIENTE pasa a COMPRA_EXITOSA."""
    import json as _json

    chat = {
        "tag": "HUMANO",  # el escalate sobrescribió antes
        "motivo": "Pago por transferencia, verificar manualmente",
        "active_route": "humano",
        "escalation_reason": "PAYMENT_VERIFICATION_PENDING",
        "status_history": [
            {"tag": "CONFIRMADO_PAGO_PENDIENTE", "motivo": "Pedido X registrado", "active_route": "ventas", "timestamp": 1.0},
            {"tag": "HUMANO", "motivo": "verificar pago", "active_route": "humano", "timestamp": 2.0},
        ],
        "episodes": [
            {
                "episode_id": "ep_001",
                "started_at_ms": 1000,
                "closed_at_ms": 2000,
                "closing_tag": "CONFIRMADO_PAGO_PENDIENTE",
                "order_id": "draft_01ABC",
            }
        ],
    }
    changed = apply_payment_confirmation_to_chat_metadata(
        chat, now_ms=3000, by="vendedor.juan"
    )

    assert changed is True
    assert chat["tag"] == "COMPRA_EXITOSA"
    assert "Pago verificado por vendedor.juan" in chat["motivo"]
    # active_route NO cambia (humano cerró el caso)
    assert chat["active_route"] == "humano"
    # status_history append
    assert len(chat["status_history"]) == 3
    last_entry = chat["status_history"][-1]
    assert last_entry["tag"] == "COMPRA_EXITOSA"
    assert last_entry["active_route"] == "humano"
    # episodio actualizado
    ep = chat["episodes"][0]
    assert ep["closing_tag"] == "COMPRA_EXITOSA"
    assert ep["payment_confirmed_at_ms"] == 3000
    assert ep["payment_confirmed_by"] == "vendedor.juan"
    # order_id NO se borra
    assert ep["order_id"] == "draft_01ABC"
    # _json import suppress unused warning
    _ = _json


def test_apply_payment_confirmation_is_idempotent():
    """Si tag ya es COMPRA_EXITOSA, no hace cambios. Crítico para evitar
    duplicación de status_history en re-clicks del dashboard."""
    import json as _json

    chat = {
        "tag": "COMPRA_EXITOSA",
        "motivo": "Pago verificado previamente",
        "status_history": [
            {"tag": "COMPRA_EXITOSA", "motivo": "prev", "active_route": "humano", "timestamp": 1.0}
        ],
        "episodes": [],
    }
    snapshot = _json.loads(_json.dumps(chat))  # deep copy via roundtrip
    changed = apply_payment_confirmation_to_chat_metadata(
        chat, now_ms=999, by="humano2"
    )
    assert changed is False
    assert chat == snapshot


def test_apply_payment_confirmation_handles_chat_without_episodes():
    """Sesión legacy sin episodes[] sigue funcionando — solo se updatea
    tag + status_history."""
    chat = {
        "tag": "HUMANO",
        "motivo": "verificar pago",
        "active_route": "humano",
        "status_history": [],
    }
    changed = apply_payment_confirmation_to_chat_metadata(
        chat, now_ms=5000, by=None
    )
    assert changed is True
    assert chat["tag"] == "COMPRA_EXITOSA"
    assert "Pago verificado por humano" in chat["motivo"]
    assert len(chat["status_history"]) == 1


def test_apply_payment_confirmation_only_touches_latest_pending_episode():
    """Si hay varios episodios cerrados, solo el último con
    CONFIRMADO_PAGO_PENDIENTE se actualiza. Episodios anteriores con
    otros closing_tag quedan intactos (no son del pedido actual)."""
    chat = {
        "tag": "HUMANO",
        "motivo": "verificar",
        "active_route": "humano",
        "episodes": [
            {"episode_id": "ep_001", "closing_tag": "RECHAZO", "closed_at_ms": 100},
            {"episode_id": "ep_002", "closing_tag": "COMPRA_EXITOSA", "closed_at_ms": 200},
            {"episode_id": "ep_003", "closing_tag": "CONFIRMADO_PAGO_PENDIENTE", "closed_at_ms": 300, "order_id": "draft_03"},
        ],
    }
    changed = apply_payment_confirmation_to_chat_metadata(
        chat, now_ms=400, by="op1"
    )
    assert changed is True
    # ep_001 RECHAZO intacto
    assert chat["episodes"][0]["closing_tag"] == "RECHAZO"
    # ep_002 COMPRA_EXITOSA previo intacto (no se duplica payment_confirmed_*)
    assert chat["episodes"][1]["closing_tag"] == "COMPRA_EXITOSA"
    assert "payment_confirmed_at_ms" not in chat["episodes"][1]
    # ep_003 actualizado
    assert chat["episodes"][2]["closing_tag"] == "COMPRA_EXITOSA"
    assert chat["episodes"][2]["payment_confirmed_at_ms"] == 400


@pytest.mark.asyncio
@respx.mock(base_url=_BASE_URL)
async def test_confirm_payment_syncs_chat_metadata_when_session_key_present(
    respx_mock, adapter, _isolate_vault_dir
):
    """Integración: confirm_payment full path con un chat metadata real.
    El metadata Medusa tiene `session_key=wa_5730000` → el adapter debe
    abrir `<vault>/wa_5730000/metadata.json` y actualizarlo a
    COMPRA_EXITOSA."""
    import json as _json

    order_id = "order_01HXX_CHAT"
    session_key = "wa_57300012345"

    # Seed: chat metadata en el vault aislado (estado post-escalation con
    # CONFIRMADO_PAGO_PENDIENTE en el episodio + HUMANO en el tag actual).
    chat_dir = _isolate_vault_dir / session_key
    chat_dir.mkdir(parents=True, exist_ok=True)
    chat_meta_file = chat_dir / "metadata.json"
    chat_meta_file.write_text(
        _json.dumps(
            {
                "tag": "HUMANO",
                "motivo": "Pago por transferencia, verificar manualmente",
                "active_route": "humano",
                "escalation_reason": "PAYMENT_VERIFICATION_PENDING",
                "status_history": [
                    {"tag": "CONFIRMADO_PAGO_PENDIENTE", "motivo": "X", "active_route": "ventas", "timestamp": 1.0},
                    {"tag": "HUMANO", "motivo": "verificar", "active_route": "humano", "timestamp": 2.0},
                ],
                "episodes": [
                    {
                        "episode_id": "ep_001",
                        "closing_tag": "CONFIRMADO_PAGO_PENDIENTE",
                        "closed_at_ms": 2000,
                        "order_id": order_id,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    # Medusa: order existe con session_key en metadata
    respx_mock.get(f"/admin/orders/{order_id}").mock(
        return_value=Response(
            200,
            json={
                "order": {
                    "id": order_id,
                    "metadata": {
                        "hubara_stage": "preparing",
                        "session_key": session_key,
                    },
                    "total": 50000,
                    "status": "pending",
                    "payment_status": "not_paid",
                }
            },
        )
    )
    respx_mock.post("/admin/payment-collections").mock(
        return_value=Response(
            200,
            json={"payment_collection": {"id": "pc_01", "status": "not_paid", "amount": 50000}},
        )
    )
    respx_mock.post("/admin/payment-collections/pc_01/mark-as-paid").mock(
        return_value=Response(
            200,
            json={"payment_collection": {"id": "pc_01", "status": "completed", "captured_amount": 50000}},
        )
    )
    respx_mock.post(f"/admin/orders/{order_id}").mock(
        return_value=Response(
            200,
            json={
                "order": {
                    "id": order_id,
                    "metadata": {
                        "hubara_stage": "preparing",
                        "hubara_payment_confirmed": True,
                        "session_key": session_key,
                    },
                }
            },
        )
    )

    result = await adapter.confirm_payment(
        ConfirmPaymentCommand(order_id=order_id, by="vendedor.maria")
    )
    assert result.success is True

    # El chat metadata debe estar actualizado
    chat_after = _json.loads(chat_meta_file.read_text(encoding="utf-8"))
    assert chat_after["tag"] == "COMPRA_EXITOSA"
    assert "Pago verificado por vendedor.maria" in chat_after["motivo"]
    assert chat_after["active_route"] == "humano"  # no cambia
    # Episode actualizado
    assert chat_after["episodes"][0]["closing_tag"] == "COMPRA_EXITOSA"
    assert chat_after["episodes"][0]["payment_confirmed_by"] == "vendedor.maria"
    # Status history append (3 entries: 2 previas + la nueva)
    assert len(chat_after["status_history"]) == 3
    assert chat_after["status_history"][-1]["tag"] == "COMPRA_EXITOSA"


@pytest.mark.asyncio
@respx.mock(base_url=_BASE_URL)
async def test_confirm_payment_does_not_fail_when_chat_metadata_missing(
    respx_mock, adapter, _isolate_vault_dir
):
    """Defensivo: si la orden tiene session_key pero el chat metadata fue
    borrado / nunca existió, confirm_payment NO falla (Medusa side ya OK)."""
    order_id = "order_ghost_chat"
    session_key = "wa_borrado"
    # NO creamos el chat metadata.json — el directorio del vault no
    # tiene la entry del session_key.

    respx_mock.get(f"/admin/orders/{order_id}").mock(
        return_value=Response(
            200,
            json={
                "order": {
                    "id": order_id,
                    "metadata": {"session_key": session_key, "hubara_stage": "preparing"},
                    "total": 25000,
                    "status": "pending",
                    "payment_status": "not_paid",
                }
            },
        )
    )
    respx_mock.post("/admin/payment-collections").mock(
        return_value=Response(
            200, json={"payment_collection": {"id": "pc_03", "status": "not_paid", "amount": 25000}}
        )
    )
    respx_mock.post("/admin/payment-collections/pc_03/mark-as-paid").mock(
        return_value=Response(
            200,
            json={"payment_collection": {"id": "pc_03", "status": "completed", "captured_amount": 25000}},
        )
    )
    respx_mock.post(f"/admin/orders/{order_id}").mock(
        return_value=Response(
            200, json={"order": {"id": order_id, "metadata": {"hubara_payment_confirmed": True}}}
        )
    )

    result = await adapter.confirm_payment(ConfirmPaymentCommand(order_id=order_id))
    # El endpoint Medusa side está OK — eso es lo crítico.
    assert result.success is True


# ----------------------------------------------------------------------
# Premortem FIX #3: Cross-system sync cancel_order → chat metadata
# ----------------------------------------------------------------------


def test_apply_order_cancellation_marks_tag_rechazo_and_appends_history():
    """Helper puro: chat con CONFIRMADO_PAGO_PENDIENTE pasa a RECHAZO."""
    chat = {
        "tag": "HUMANO",
        "motivo": "verificar pago",
        "active_route": "humano",
        "status_history": [
            {"tag": "CONFIRMADO_PAGO_PENDIENTE", "motivo": "X", "active_route": "ventas", "timestamp": 1.0},
            {"tag": "HUMANO", "motivo": "verificar", "active_route": "humano", "timestamp": 2.0},
        ],
        "episodes": [
            {
                "episode_id": "ep_001",
                "closing_tag": "CONFIRMADO_PAGO_PENDIENTE",
                "closed_at_ms": 2000,
                "order_id": "draft_01XYZ",
            }
        ],
    }
    changed = apply_order_cancellation_to_chat_metadata(
        chat,
        now_ms=3000,
        reason="pago no recibido tras 48h",
        by="vendedor.pedro",
    )

    assert changed is True
    assert chat["tag"] == "RECHAZO"
    assert "Orden cancelada por vendedor.pedro" in chat["motivo"]
    assert "pago no recibido tras 48h" in chat["motivo"]
    assert chat["active_route"] == "humano"
    # status_history append
    assert len(chat["status_history"]) == 3
    assert chat["status_history"][-1]["tag"] == "RECHAZO"
    # episodio actualizado
    ep = chat["episodes"][0]
    assert ep["closing_tag"] == "RECHAZO"
    assert ep["cancelled_at_ms"] == 3000
    assert ep["cancelled_by"] == "vendedor.pedro"
    assert ep["cancellation_reason"] == "pago no recibido tras 48h"
    # order_id queda preservado para auditoría
    assert ep["order_id"] == "draft_01XYZ"


def test_apply_order_cancellation_idempotent_when_already_rechazo():
    """Si tag ya es RECHAZO, no hace cambios."""
    chat = {"tag": "RECHAZO", "motivo": "previo", "status_history": []}
    snapshot = json.loads(json.dumps(chat))
    changed = apply_order_cancellation_to_chat_metadata(
        chat, now_ms=5000, reason=None, by=None
    )
    assert changed is False
    assert chat == snapshot


def test_apply_order_cancellation_preserves_compra_exitosa_terminal():
    """Edge case: humano cancela DESPUÉS de haber confirmado pago. La tag
    COMPRA_EXITOSA es estado terminal positivo — no revertimos el chat
    aunque se cancele la orden en Medusa. El humano ve la inconsistencia
    en logs y la maneja manualmente (caso raro)."""
    chat = {
        "tag": "COMPRA_EXITOSA",
        "motivo": "pago verificado",
        "active_route": "humano",
        "status_history": [],
        "episodes": [{"closing_tag": "COMPRA_EXITOSA"}],
    }
    snapshot = json.loads(json.dumps(chat))
    changed = apply_order_cancellation_to_chat_metadata(
        chat, now_ms=9000, reason="cliente regretted", by="op"
    )
    assert changed is False
    assert chat == snapshot


def test_apply_order_cancellation_without_reason_just_marks_tag():
    """Sin reason explícito, motivo solo dice 'Orden cancelada por X'."""
    chat = {"tag": "HUMANO", "motivo": "verificar", "status_history": []}
    changed = apply_order_cancellation_to_chat_metadata(
        chat, now_ms=1000, reason=None, by="vendedor.ana"
    )
    assert changed is True
    assert chat["motivo"] == "Orden cancelada por vendedor.ana"


@pytest.mark.asyncio
@respx.mock(base_url=_BASE_URL)
async def test_cancel_order_syncs_chat_metadata_to_rechazo(
    respx_mock, adapter, _isolate_vault_dir
):
    """Integración: cancel_order sobre una Order real con session_key debe
    actualizar el chat metadata a tag=RECHAZO."""
    order_id = "order_01HCANCEL"
    session_key = "wa_57300077777"

    # Seed: chat metadata en estado escalado a humano
    chat_dir = _isolate_vault_dir / session_key
    chat_dir.mkdir(parents=True, exist_ok=True)
    chat_meta_file = chat_dir / "metadata.json"
    chat_meta_file.write_text(
        json.dumps(
            {
                "tag": "HUMANO",
                "motivo": "verificar pago por transferencia",
                "active_route": "humano",
                "status_history": [
                    {"tag": "HUMANO", "motivo": "verificar", "active_route": "humano", "timestamp": 1.0}
                ],
                "episodes": [
                    {
                        "episode_id": "ep_001",
                        "closing_tag": "CONFIRMADO_PAGO_PENDIENTE",
                        "closed_at_ms": 1000,
                        "order_id": order_id,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    # Medusa: 2 GETs (uno del _patch interno, uno del post-patch
    # _fetch_with_kind). Ambos devuelven la order con session_key.
    order_payload = {
        "order": {
            "id": order_id,
            "metadata": {"hubara_stage": "preparing", "session_key": session_key},
            "total": 50000,
            "status": "pending",
            "payment_status": "not_paid",
        }
    }
    respx_mock.get(f"/admin/orders/{order_id}").mock(
        return_value=Response(200, json=order_payload)
    )
    # patch metadata (cancel_patch)
    respx_mock.post(f"/admin/orders/{order_id}").mock(
        return_value=Response(
            200,
            json={
                "order": {
                    "id": order_id,
                    "metadata": {
                        "hubara_stage": "cancelled",
                        "session_key": session_key,
                    },
                }
            },
        )
    )
    # hard-cancel Medusa
    respx_mock.post(f"/admin/orders/{order_id}/cancel").mock(
        return_value=Response(200, json={"order": {"id": order_id, "status": "canceled"}})
    )

    result = await adapter.cancel_order(
        CancelOrderCommand(order_id=order_id, reason="pago no llegó", by="vendedor.luis")
    )
    assert result.success is True

    chat_after = json.loads(chat_meta_file.read_text(encoding="utf-8"))
    assert chat_after["tag"] == "RECHAZO"
    assert "vendedor.luis" in chat_after["motivo"]
    assert "pago no llegó" in chat_after["motivo"]
    assert chat_after["episodes"][0]["closing_tag"] == "RECHAZO"
    assert chat_after["episodes"][0]["cancelled_by"] == "vendedor.luis"


@pytest.mark.asyncio
@respx.mock(base_url=_BASE_URL)
async def test_cancel_order_skips_sync_when_no_session_key(
    respx_mock, adapter, _isolate_vault_dir
):
    """Cancel de una orden manual (sin session_key) NO toca chat metadata
    y no falla."""
    order_id = "order_manual_cancel"
    order_payload = {
        "order": {
            "id": order_id,
            "metadata": {"hubara_stage": "preparing"},  # SIN session_key
            "total": 30000,
            "status": "pending",
            "payment_status": "not_paid",
        }
    }
    respx_mock.get(f"/admin/orders/{order_id}").mock(
        return_value=Response(200, json=order_payload)
    )
    respx_mock.post(f"/admin/orders/{order_id}").mock(
        return_value=Response(
            200,
            json={"order": {"id": order_id, "metadata": {"hubara_stage": "cancelled"}}},
        )
    )
    respx_mock.post(f"/admin/orders/{order_id}/cancel").mock(
        return_value=Response(200, json={"order": {"id": order_id, "status": "canceled"}})
    )

    result = await adapter.cancel_order(
        CancelOrderCommand(order_id=order_id, reason="x")
    )
    assert result.success is True
    # No hay chat metadata creado, no rompió el flow


# ----------------------------------------------------------------------
# Premortem FIX #4: defensa tzdata en context.py (Bogotá)
# ----------------------------------------------------------------------


def test_resolve_bogota_tz_falls_back_when_zoneinfo_unavailable(monkeypatch):
    """Si `ZoneInfo("America/Bogota")` lanza `ZoneInfoNotFoundError`
    (container minimalista sin tzdata), `_resolve_bogota_tz` cae a un
    offset fijo UTC-5 con warning. Colombia no tiene DST así que el
    fallback es funcionalmente correcto.

    Premortem FIX #4: sin esta defensa, el import del módulo `context.py`
    haría crash al boot del agente sales en containers con base image
    minimalista (alpine sin tzdata)."""
    from datetime import timedelta, timezone

    import src.plugins.chats.agent.sales.context as ctx_mod

    def _fake_zoneinfo(_name: str):
        raise ctx_mod.ZoneInfoNotFoundError("simulated missing tzdata")

    monkeypatch.setattr(ctx_mod, "ZoneInfo", _fake_zoneinfo)

    tz = ctx_mod._resolve_bogota_tz()
    assert isinstance(tz, timezone)
    assert tz.utcoffset(None) == timedelta(hours=-5)


def test_build_bogota_context_string_works_with_fixed_offset_tz():
    """`build_bogota_context_string` debe producir el saludo correcto
    incluso si `_BOGOTA_TZ` es un `timezone(timedelta(hours=-5))` (caso
    fallback del FIX #4). El cómputo del saludo es por hora local, sin
    asumir nada del objeto TZ."""
    from datetime import datetime, timedelta, timezone

    fixed_utc_minus_5 = timezone(timedelta(hours=-5))
    morning = datetime(2026, 5, 26, 8, 30, tzinfo=fixed_utc_minus_5)
    afternoon = datetime(2026, 5, 26, 14, 0, tzinfo=fixed_utc_minus_5)
    night = datetime(2026, 5, 26, 21, 0, tzinfo=fixed_utc_minus_5)

    from src.plugins.chats.agent.sales.context import build_bogota_context_string

    assert "08:30" in build_bogota_context_string(now=morning)
    assert "Buenos días" in build_bogota_context_string(now=morning)
    assert "14:00" in build_bogota_context_string(now=afternoon)
    assert "Buenas tardes" in build_bogota_context_string(now=afternoon)
    assert "21:00" in build_bogota_context_string(now=night)
    assert "Buenas noches" in build_bogota_context_string(now=night)
