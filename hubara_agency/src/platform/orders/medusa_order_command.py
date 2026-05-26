"""`MedusaOrderCommand` — adapter live del `OrderCommandPort` contra Medusa v2.

Espejo write del `MedusaOrderQuery` (read). Las 4 operaciones del port se
materializan como merge-patches al campo `metadata` del draft order / order
correspondiente (ver `state.py` para el schema de keys `hubara_*`).

Detección draft vs order:
  El `order_id` puede ser tanto `draft_01HXX...` como `order_01HXX...`. El
  adapter prueba primero el endpoint de drafts (porque la mayoría de las
  operaciones del dashboard son sobre drafts recién creados), y cae al
  endpoint de orders si Medusa devuelve 404. Esto evita tener que hacer 2
  round-trips siempre.

R-DIP: el adapter NO importa de ningún plugin. Está acoplado solo al
`HttpMedusaClient` (driven adapter).

R-JSON: devuelve `OrderCommandResult` (frozen dataclass).

Resiliencia: el `HttpMedusaClient._request` ya tiene tenacity retry. Este
adapter NO agrega su propio retry — captura `MedusaAPIError` final y la
traduce a `OrderCommandResult(success=False, error_detail=...)`.
"""
from __future__ import annotations

import logging
from typing import Any

from src.platform.medusa.client import HttpMedusaClient, MedusaAPIError
from src.platform.orders.command_port import (
    CancelOrderCommand,
    ConfirmPaymentCommand,
    OrderCommandResult,
    ScheduleDeliveryCommand,
    TransitionStageCommand,
)
from src.platform.orders.state import (
    InvalidStageTransitionError,
    build_cancel_patch,
    build_confirm_payment_patch,
    build_schedule_patch,
    read_stage,
    transition_stage,
)

log = logging.getLogger(__name__)


class MedusaOrderCommand:
    """Adapter live — escribe metadata a Medusa via merge-patch."""

    def __init__(self, client: HttpMedusaClient) -> None:
        self._client = client

    # ------------------------------------------------------------------
    # Public — implementa OrderCommandPort protocol
    # ------------------------------------------------------------------

    async def schedule_delivery(
        self, command: ScheduleDeliveryCommand
    ) -> OrderCommandResult:
        """Agendar + transicionar `new → preparing` + **convertir draft a Order real**.

        Flujo (post-rationale fix 2026-05-25):
          1. Fetch metadata actual (draft o order).
          2. Build patch con scheduled_delivery + transition new→preparing.
          3. **Si es draft**: convertir a Order via `convert-to-order` ANTES de
             escribir la metadata. La conversion preserva el `id` y la metadata
             existente (Medusa garantiza ambos). NO requiere payment provider —
             la Order resultante queda `payment_status="not_paid"` legítimamente.
          4. Aplicar el patch al endpoint correcto (ya es order ahora).

        Esto materializa el flujo que el operador pidió: "al darle confirmar
        cambiar el estado del pedido a una orden real".
        """
        try:
            is_draft, current_data = await self._fetch_with_kind(command.order_id)
        except MedusaAPIError as exc:
            return _exc_to_result(command.order_id, exc, "schedule_delivery")

        current_metadata: dict[str, Any] = current_data.get("metadata") or {}

        # Build el patch DESDE la metadata actual.
        try:
            patch = build_schedule_patch(
                current_metadata,
                delivery_iso=command.delivery_iso,
                delivery_time=command.delivery_time,
                note=command.note,
                by=command.by,
            )
        except InvalidStageTransitionError as exc:
            log.info(
                "schedule_delivery: invalid transition",
                extra={"order_id": command.order_id, "exc": str(exc)},
            )
            return OrderCommandResult(
                success=False,
                order_id=command.order_id,
                current_stage=read_stage(current_metadata),
                error_detail=f"invalid_transition: {exc}",
            )

        if not patch:
            # No-op (ya está en preparing y mismas keys) → success.
            return OrderCommandResult(
                success=True,
                order_id=command.order_id,
                current_stage=read_stage(current_metadata),
            )

        # Si es draft, convertirlo a Order REAL antes del patch.
        # La conversion preserva id + metadata existente.
        if is_draft:
            try:
                await self._client.convert_draft_to_order(command.order_id)
                log.info(
                    "schedule_delivery: draft converted to order",
                    extra={"order_id": command.order_id},
                )
            except MedusaAPIError as exc:
                log.error(
                    "schedule_delivery: convert-to-order failed",
                    extra={
                        "order_id": command.order_id,
                        "status_code": exc.status_code,
                        "body": str(exc)[:200],
                    },
                )
                return _exc_to_result(command.order_id, exc, "convert_to_order")

        # Patch metadata sobre el endpoint de orders (ya no es draft).
        try:
            updated = await self._client.patch_order_metadata(
                command.order_id, patch
            )
        except MedusaAPIError as exc:
            return _exc_to_result(command.order_id, exc, "patch_order_metadata")

        new_metadata: dict[str, Any] = updated.get("metadata") or {}
        new_stage = read_stage(new_metadata)
        log.info(
            "schedule_delivery: OK",
            extra={
                "order_id": command.order_id,
                "new_stage": new_stage,
                "was_draft": is_draft,
            },
        )
        return OrderCommandResult(
            success=True,
            order_id=command.order_id,
            current_stage=new_stage,
        )

    async def transition_stage(
        self, command: TransitionStageCommand
    ) -> OrderCommandResult:
        """Drag-and-drop o click directo. Valida transición permitida."""
        return await self._patch(
            command.order_id,
            patch_builder=lambda meta: transition_stage(
                meta,
                to_stage=command.to_stage,
                by=command.by,
                note=command.note,
                force=command.force,
            ),
            operation="transition_stage",
        )

    async def confirm_payment(
        self, command: ConfirmPaymentCommand
    ) -> OrderCommandResult:
        """Marcar pago confirmado — flujo consistente Medusa + metadata.

        Sequence (post-rationale 2026-05-25):
          1. Fetch order (debe ser Order real, no draft — pagos manuales
             solo aplican post-conversion). Si es draft, error claro.
          2. Si `metadata.hubara_payment_confirmed` ya es True → idempotente,
             devolver success sin tocar Medusa.
          3. **Registrar payment en Medusa** (atómico — si falla, NO escribir
             el flag local):
               3a. POST `/admin/payment-collections` con order_id + amount.
               3b. POST `/admin/payment-collections/{id}/mark-as-paid`
                   con order_id → Medusa marca `payment_status="captured"`.
          4. PATCH metadata.hubara_payment_confirmed=true + history append.

        Consistencia: si paso 3 falla, paso 4 NO se ejecuta. El estado
        Medusa (`payment_status="not_paid"`) sigue siendo source of truth.
        Si paso 3 succeed pero paso 4 falla (raro), Medusa queda en
        `captured` pero nuestro flag no — el frontend mostrará pagado por
        el `_map_pay_status` legacy (que ya lee Medusa `payment_status`).
        """
        order_id = command.order_id

        try:
            is_draft, current_data = await self._fetch_with_kind(order_id)
        except MedusaAPIError as exc:
            return _exc_to_result(order_id, exc, "confirm_payment.fetch")

        if is_draft:
            # Drafts no pueden tener payment registrado en Medusa (no son
            # Orders reales). El humano debe agendar primero (que convierte
            # a Order) antes de confirmar pago.
            return OrderCommandResult(
                success=False,
                order_id=order_id,
                current_stage=read_stage(current_data.get("metadata") or {}),
                error_detail=(
                    "invalid_state: el pedido aún es draft. Agendá la "
                    "entrega primero (eso lo convierte en una Order real), "
                    "y después podés confirmar el pago."
                ),
            )

        current_metadata: dict[str, Any] = current_data.get("metadata") or {}

        # Idempotente: si ya está confirmado, devolver success sin side effects.
        # build_confirm_payment_patch ya hace esto check, pero queremos evitar
        # llamadas a Medusa innecesarias también.
        patch = build_confirm_payment_patch(current_metadata, by=command.by)
        if not patch:
            return OrderCommandResult(
                success=True,
                order_id=order_id,
                current_stage=read_stage(current_metadata),
            )

        # Verificar si la order ya tiene un payment captured en Medusa (caso:
        # un humano fue al admin y registró pago manualmente, sin pasar por
        # nuestro endpoint). Si ya está captured, solo escribimos el flag.
        existing_payment_status = current_data.get("payment_status")
        already_captured_in_medusa = existing_payment_status == "captured"

        if not already_captured_in_medusa:
            # Registrar payment en Medusa via pp_system_default.
            amount = int(current_data.get("total") or 0)
            if amount <= 0:
                return OrderCommandResult(
                    success=False,
                    order_id=order_id,
                    current_stage=read_stage(current_metadata),
                    error_detail=(
                        "invalid_state: la order no tiene total > 0 en "
                        "Medusa; no puedo registrar el pago."
                    ),
                )
            try:
                pc = await self._client.create_payment_collection(
                    order_id, amount=amount
                )
                pc_id = str(pc["id"])
                await self._client.mark_payment_collection_paid(
                    pc_id, order_id=order_id
                )
                log.info(
                    "confirm_payment: Medusa payment registered",
                    extra={
                        "order_id": order_id,
                        "payment_collection_id": pc_id,
                        "amount": amount,
                    },
                )
            except MedusaAPIError as exc:
                return _exc_to_result(
                    order_id, exc, "confirm_payment.medusa_register"
                )

        # Patch metadata con el flag. Solo después de que Medusa ya está OK.
        try:
            updated = await self._client.patch_order_metadata(order_id, patch)
        except MedusaAPIError as exc:
            log.error(
                "confirm_payment: Medusa captured but metadata patch failed",
                extra={
                    "order_id": order_id,
                    "status_code": exc.status_code,
                    "body": str(exc)[:200],
                },
            )
            return _exc_to_result(
                order_id, exc, "confirm_payment.patch_metadata"
            )

        new_metadata: dict[str, Any] = updated.get("metadata") or {}
        return OrderCommandResult(
            success=True,
            order_id=order_id,
            current_stage=read_stage(new_metadata),
        )

    async def cancel_order(
        self, command: CancelOrderCommand
    ) -> OrderCommandResult:
        """Cancelar — soft (metadata) + hard (Medusa) cuando aplica.

        Sequence:
          1. Fetch para detectar draft vs order.
          2. Patch metadata (`hubara_stage="cancelled"` + reason + history).
             Esto SIEMPRE corre, así el kanban refleja el cambio aún si
             Medusa rechaza el hard-cancel.
          3. **Si es Order real** (no draft): POST `/admin/orders/{id}/cancel`
             para que Medusa también refleje el estado. Esto rollback de
             inventario reservado y deja la order en `status="canceled"`.
             Si falla, loggeamos pero NO revertimos el metadata patch — el
             humano necesita ver que está cancelada en el kanban; la
             reconciliación con Medusa puede hacerse manual.
          4. **Si es draft**: NO llamamos a Medusa. Soft-cancel only —
             Medusa's draft `DELETE` es destructivo (la id deja de resolverse)
             y queremos preservar la card en la columna "Cancelada" para
             que el operador pueda revisarla. Si quiere eliminarla del todo,
             lo hace via Medusa Admin.
        """
        result = await self._patch(
            command.order_id,
            patch_builder=lambda meta: build_cancel_patch(
                meta,
                reason=command.reason,
                by=command.by,
            ),
            operation="cancel_order",
        )
        # Si el patch falló (DAG, 404, etc.) → devolver tal cual sin tocar
        # Medusa. Cancel hard sin patch metadata sería peor que ninguno
        # (frontend no refleja el cambio).
        if not result.success:
            return result

        # Detectar si es draft o order — si es Order real, hard-cancel via
        # Medusa para mantener consistencia con su payment_collections /
        # inventory. Si es draft, soft-cancel suficiente.
        try:
            is_draft, _ = await self._fetch_with_kind(command.order_id)
        except MedusaAPIError as exc:
            log.warning(
                "cancel_order: post-patch fetch failed; soft-cancel only",
                extra={
                    "order_id": command.order_id,
                    "status_code": exc.status_code,
                },
            )
            return result

        if is_draft:
            return result

        # Order real — hard-cancel via Medusa.
        try:
            await self._client.cancel_order(command.order_id)
            log.info(
                "cancel_order: Medusa hard-cancel OK",
                extra={"order_id": command.order_id},
            )
        except MedusaAPIError as exc:
            # Idempotente: si la order ya estaba cancelada en Medusa,
            # Medusa devuelve 422 ("already canceled"). Tratamos como OK.
            if exc.status_code in (404, 422):
                log.info(
                    "cancel_order: Medusa already canceled (idempotent)",
                    extra={
                        "order_id": command.order_id,
                        "status_code": exc.status_code,
                    },
                )
                return result
            # Otro tipo de error — loggeamos pero devolvemos success igual
            # (metadata patch ya pasó, el operador ya ve el cambio).
            log.error(
                "cancel_order: Medusa hard-cancel failed; metadata-only state",
                extra={
                    "order_id": command.order_id,
                    "status_code": exc.status_code,
                    "body": str(exc)[:200],
                },
            )
        return result

    # ------------------------------------------------------------------
    # Internals — el flujo común: detect draft vs order → fetch → patch
    # ------------------------------------------------------------------

    async def _patch(
        self,
        order_id: str,
        *,
        patch_builder,
        operation: str,
    ) -> OrderCommandResult:
        """Flujo común de las 4 operaciones:

          1. Detectar si es draft o order (probar draft primero).
          2. Leer metadata actual.
          3. Llamar `patch_builder(current_metadata)` → patch dict.
          4. Si patch vacío (no-op idempotente) → return success sin Medusa call.
          5. Merge-patch contra Medusa.
          6. Devolver result con el stage resultante.

        Captura `MedusaAPIError` y `InvalidStageTransitionError` y los traduce
        a `OrderCommandResult(success=False, error_detail=...)`.
        """
        try:
            is_draft, current_data = await self._fetch_with_kind(order_id)
        except MedusaAPIError as exc:
            log.warning(
                "MedusaOrderCommand: fetch failed",
                extra={
                    "order_id": order_id,
                    "operation": operation,
                    "status_code": exc.status_code,
                    "body": str(exc)[:200],
                },
            )
            if exc.status_code == 404:
                return OrderCommandResult(
                    success=False,
                    order_id=order_id,
                    error_detail=(
                        f"not_found: order {order_id} no existe en Medusa"
                    ),
                )
            return OrderCommandResult(
                success=False,
                order_id=order_id,
                error_detail=_format_medusa_error(exc),
            )

        current_metadata: dict[str, Any] = current_data.get("metadata") or {}

        try:
            patch = patch_builder(current_metadata)
        except InvalidStageTransitionError as exc:
            log.info(
                "MedusaOrderCommand: invalid transition rejected",
                extra={
                    "order_id": order_id,
                    "operation": operation,
                    "from_stage": exc.from_stage,
                    "to_stage": exc.to_stage,
                },
            )
            return OrderCommandResult(
                success=False,
                order_id=order_id,
                current_stage=read_stage(current_metadata),
                error_detail=f"invalid_transition: {exc}",
            )

        if not patch:
            # No-op idempotente (e.g. ya estaba en el stage destino, o pago
            # ya confirmado). Devuelve success con el estado actual.
            return OrderCommandResult(
                success=True,
                order_id=order_id,
                current_stage=read_stage(current_metadata),
            )

        try:
            if is_draft:
                updated = await self._client.patch_draft_order_metadata(
                    order_id, patch
                )
            else:
                updated = await self._client.patch_order_metadata(
                    order_id, patch
                )
        except MedusaAPIError as exc:
            log.error(
                "MedusaOrderCommand: patch failed",
                extra={
                    "order_id": order_id,
                    "operation": operation,
                    "status_code": exc.status_code,
                    "body": str(exc)[:200],
                },
            )
            return OrderCommandResult(
                success=False,
                order_id=order_id,
                current_stage=read_stage(current_metadata),
                error_detail=_format_medusa_error(exc),
            )

        new_metadata: dict[str, Any] = updated.get("metadata") or {}
        new_stage = read_stage(new_metadata)
        log.info(
            "MedusaOrderCommand: %s OK",
            operation,
            extra={
                "order_id": order_id,
                "operation": operation,
                "new_stage": new_stage,
                "patch_keys": sorted(patch.keys()),
            },
        )
        return OrderCommandResult(
            success=True,
            order_id=order_id,
            current_stage=new_stage,
        )

    async def _fetch_with_kind(
        self, order_id: str
    ) -> tuple[bool, dict[str, Any]]:
        """Detect draft vs order y fetch en una sola pasada.

        Returns: (is_draft, raw_data).

        Bug fix 2026-05-26: Medusa v2 NO usa prefijo `draft_*` — todos los
        IDs son `order_*`. El distinguishing factor es el campo `status` del
        payload, no el prefijo. Además `/admin/orders/{id}` devuelve drafts
        también (con status="draft"). Estrategia revisada:
          1. Probar `/admin/orders/{id}` (devuelve ambos tipos).
          2. Si 404 → fallback a `/admin/draft-orders/{id}`.
          3. is_draft = (payload.status == "draft").
        """
        fields = "id,metadata,total,payment_status,status"
        try:
            data = await self._client.get_order(order_id, fields=fields)
            # /admin/orders/{id} devolvió 200 — infer is_draft del status.
            # Medusa v2 live retorna status="draft" para drafts también vía
            # este endpoint (verified 2026-05-26).
            is_draft = data.get("status") == "draft"
            return is_draft, data
        except MedusaAPIError as exc:
            if exc.status_code != 404:
                raise
        # Fallback al endpoint de drafts — algunas versiones de Medusa solo
        # exponen drafts via /admin/draft-orders/{id}. Si llegamos acá, sabemos
        # con certeza que es draft (porque /admin/orders/{id} respondió 404).
        data = await self._client.get_draft_order(order_id, fields=fields)
        return True, data


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _exc_to_result(
    order_id: str, exc: MedusaAPIError, operation: str
) -> OrderCommandResult:
    """Translate `MedusaAPIError` to `OrderCommandResult(success=False)`.

    Compartido entre `schedule_delivery` (que tiene flow extendido por la
    conversion + patch separadas) y `_patch` (que cubre los demás
    comandos). Centraliza el formato `kind: detail` esperado por el frontend.
    """
    log.warning(
        "MedusaOrderCommand: %s failed",
        operation,
        extra={
            "order_id": order_id,
            "operation": operation,
            "status_code": exc.status_code,
            "body": str(exc)[:200],
        },
    )
    if exc.status_code == 404:
        return OrderCommandResult(
            success=False,
            order_id=order_id,
            error_detail=f"not_found: order {order_id} no existe en Medusa",
        )
    return OrderCommandResult(
        success=False,
        order_id=order_id,
        error_detail=_format_medusa_error(exc),
    )


def _format_medusa_error(exc: MedusaAPIError) -> str:
    """Traduce MedusaAPIError a el formato `kind: detail` que el frontend
    espera. Mismo shape que `medusa_order_query._format_medusa_error`.
    """
    if exc.status_code == 401:
        return (
            "medusa_unauthorized: HTTP 401 — el admin_token expiró o es "
            "inválido. Regenerá un Secret API Key en Medusa Admin → "
            "Settings → Developer."
        )
    if exc.status_code == 403:
        return "medusa_forbidden: HTTP 403 — el admin_token no tiene permisos."
    if 500 <= exc.status_code < 600:
        return f"medusa_unavailable: HTTP {exc.status_code} {exc.path}"
    return f"medusa_api_error: HTTP {exc.status_code} {exc.path}"


# ----------------------------------------------------------------------
# Stub fallback — para entornos sin Medusa configurado
# ----------------------------------------------------------------------


class NoopOrderCommand:
    """Stub que devuelve success=False con detail informativo.

    Wired por composition cuando `MEDUSA_REGION_ID` / `_SALES_CHANNEL_ID`
    no están configurados. Permite que el dashboard cargue (`/api/orders/*`
    sigue respondiendo) sin romper con HTTP 500 — el operador ve el banner
    "Medusa no disponible" y los botones quedan deshabilitados.
    """

    @staticmethod
    def _unavailable(order_id: str) -> OrderCommandResult:
        return OrderCommandResult(
            success=False,
            order_id=order_id,
            error_detail=(
                "medusa_unavailable: Medusa no está configurado en este "
                "entorno. Definí MEDUSA_BASE_URL + MEDUSA_ADMIN_TOKEN + "
                "MEDUSA_REGION_ID + MEDUSA_SALES_CHANNEL_ID en .env."
            ),
        )

    async def schedule_delivery(
        self, command: ScheduleDeliveryCommand
    ) -> OrderCommandResult:
        return self._unavailable(command.order_id)

    async def transition_stage(
        self, command: TransitionStageCommand
    ) -> OrderCommandResult:
        return self._unavailable(command.order_id)

    async def confirm_payment(
        self, command: ConfirmPaymentCommand
    ) -> OrderCommandResult:
        return self._unavailable(command.order_id)

    async def cancel_order(
        self, command: CancelOrderCommand
    ) -> OrderCommandResult:
        return self._unavailable(command.order_id)
