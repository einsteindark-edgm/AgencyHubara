"""Tool: ManageConversationTagTool.

DEHA-compliant tool that satisfies the exoclaw `Tool` Protocol via the
`ToolBase` mixin. Implements `execute_with_context(ctx, **params)` so that the
`ToolRegistry.execute` dispatch (`exoclaw.agent.tools.registry:102-105`) injects
the `ToolContext` automatically.

The tag taxonomy (`INTERESADO`, `RECHAZO`, `COMPRA_EXITOSA`) is enforced by the
JSON schema `enum` constraint, not by post-hoc `if not isinstance(...)` defaults.
That means an invalid tag returns `Error: Invalid parameters ...` to the LLM
(via `ToolBase.validate_params`) — the legacy silent fallback to `INTERESADO`
is gone. Same for `motivo`: required, non-empty.

ADR-001: the tool is inert w.r.t. Temporal. It writes the tag to `metadata.json`
and returns a JSON envelope (`schedule_remarketing` + `message`); the workflow
parses the envelope and issues the dispatcher activity. No `temporal_client`,
no `start_workflow`, no workflow imports.

Episode lifecycle: cuando el tag es un cierre formal (`CLOSING_TAGS` —
COMPRA_EXITOSA, RECHAZO, CONFIRMADO_SIN_DATOS), se cierra el episodio
activo via `close_episode(metadata, ...)`. El siguiente inbound del mismo
cliente abrirá un episodio nuevo automáticamente.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from exoclaw.agent.tools import ToolBase, ToolContext

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.constants import ROUTE_VENTAS
from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import (
    CLOSING_TAGS,
    close_episode,
    count_session_jsonl_lines,
)


# Sesión c4e3416f: `CONFIRMADO_SIN_DATOS` es para el caso donde el cliente
# confirmó el pedido (apretó "Confirmar" en `present_order_confirmation`) pero
# NO completó los datos de envío y dejó la conversación. Esta tag NO arranca
# remarketing (a diferencia de INTERESADO) — el LLM la usa SIEMPRE en combo
# con `escalate_to_human(reason_category="ORDER_PENDING_SHIPPING_DETAILS")`
# para que un humano cierre la operación pidiendo los datos faltantes.
#
# HU "verificación humana de pago" (operativo hasta tener pasarela de pago):
# `CONFIRMADO_PAGO_PENDIENTE` se usa SIEMPRE después de `register_order`
# exitoso, ANTES de marcar `COMPRA_EXITOSA`. El LLM no puede saber si el
# cliente efectivamente pagó (sin pasarela integrada), entonces marca el
# pedido como "registrado, falta verificación de pago" y delega al humano
# vía `escalate_to_human(reason_category="PAYMENT_VERIFICATION_PENDING")`.
# El humano confirma el pago en el dashboard de orders y marca la venta
# como exitosa (o la rechaza si el pago no llega). Aplica a los 3 métodos
# (card, transfer, cash_on_delivery) hasta que haya pasarela activa.
_TAG_ENUM: list[str] = [
    "INTERESADO",
    "RECHAZO",
    "COMPRA_EXITOSA",
    "CONFIRMADO_SIN_DATOS",
    "CONFIRMADO_PAGO_PENDIENTE",
]


class ManageConversationTagTool(ToolBase):
    """Register the final commercial tag for a conversation, plus the reason.

    Used at end-of-sale or when the user loses interest. If the tag is
    `INTERESADO`, the response envelope carries a `schedule_remarketing` decision
    that the workflow turns into a dispatcher-activity invocation.
    """

    name = "manage_conversation_tag"
    description = (
        "Úsala al final de la venta, o si el usuario pierde el interés. "
        "Registra la etiqueta final ('INTERESADO', 'RECHAZO', "
        "'COMPRA_EXITOSA', 'CONFIRMADO_SIN_DATOS', "
        "'CONFIRMADO_PAGO_PENDIENTE') y un resumen breve."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "tag": {
                "type": "string",
                "enum": _TAG_ENUM,
                "description": (
                    "Etiqueta final. Una de: INTERESADO (cliente sigue "
                    "dudando o se enfrió — programa remarketing 1 hora "
                    "después), RECHAZO (no compra, cierre definitivo), "
                    "COMPRA_EXITOSA (cierre con venta concretada Y pago "
                    "verificado por un humano — esta tag la pone el "
                    "humano desde el dashboard, NO el LLM), "
                    "CONFIRMADO_SIN_DATOS (cliente confirmó la compra "
                    "pero NO completó los datos de envío — usá esta tag "
                    "SIEMPRE en combo con `escalate_to_human"
                    "(reason_category=ORDER_PENDING_SHIPPING_DETAILS)` "
                    "para que un humano cierre la operación pidiendo los "
                    "datos faltantes; NO programa remarketing), "
                    "CONFIRMADO_PAGO_PENDIENTE (cliente confirmó el "
                    "pedido + dio todos los datos de envío + `register_order` "
                    "devolvió `registered=true`; el LLM NO sabe si el pago "
                    "se efectuó porque no hay pasarela integrada — usá "
                    "esta tag SIEMPRE en combo con `escalate_to_human"
                    "(reason_category=PAYMENT_VERIFICATION_PENDING)` para "
                    "que un humano verifique el pago en el dashboard de "
                    "orders. NO programa remarketing. NO uses "
                    "COMPRA_EXITOSA para este caso — esa tag la pone el "
                    "humano cuando confirma el pago)."
                ),
            },
            "motivo": {
                "type": "string",
                "description": (
                    "Resumen breve, de máximo 2 líneas, de por qué se aplicó "
                    "esta etiqueta dado el contexto del chat."
                ),
                "minLength": 1,
            },
        },
        "required": ["tag", "motivo"],
    }

    def __init__(self, workspace: str | Path, vault_dir: str | Path | None = None):
        # POST-MORTEM workflow remarketing-wa_573125671604: el `workspace` que
        # llega aqui es el RUNTIME WORKSPACE CANONICO del agente, compartido
        # entre TODAS las sesiones — escribir `metadata.json` ahi pisa el
        # estado entre clientes distintos y NO es lo que `LoadOrStartSalesSession`
        # lee al rutear el siguiente webhook. El parametro queda por
        # compatibilidad pero NO se usa para metadata.
        # `vault_dir` (DI-friendly para tests): default = `WORKSPACE_VAULT_DIR`.
        self._workspace = Path(workspace)
        self._vault_dir = (
            Path(vault_dir) if vault_dir is not None else WORKSPACE_VAULT_DIR
        )

    async def execute_with_context(
        self, ctx: ToolContext, tag: str, motivo: str
    ) -> str:
        # Path per-sesion (NO el workspace canonico del agente).
        metadata_file = self._vault_dir / ctx.session_key / "metadata.json"
        metadata_file.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {}
        if metadata_file.exists():
            data = json.loads(metadata_file.read_text(encoding="utf-8"))

        # Premortem FIX #1: CONFIRMADO_PAGO_PENDIENTE requiere que el LLM
        # haya llamado `register_order` con éxito previamente. Sin esa
        # precondición, marcar este tag deja la metadata en estado
        # inconsistente (tag dice "pago pendiente" pero no hay orden en
        # Medusa para verificar pago). Rechazamos con error claro para que
        # el LLM corrija el orden de las tools (register_order primero).
        if tag == "CONFIRMADO_PAGO_PENDIENTE":
            registered = data.get("registered_order")
            ok = (
                isinstance(registered, dict)
                and registered.get("success") is True
            )
            if not ok:
                return json.dumps(
                    {
                        "error": (
                            "precondition_failed: no puedes marcar "
                            "CONFIRMADO_PAGO_PENDIENTE sin haber llamado "
                            "`register_order` con éxito (registered=true). "
                            "Llama `register_order` primero; cuando "
                            "devuelva `registered=true`, recién ahí marca "
                            "CONFIRMADO_PAGO_PENDIENTE y escala con "
                            "`escalate_to_human(reason_category="
                            "PAYMENT_VERIFICATION_PENDING)`. Si "
                            "`register_order` falla (registered=false), "
                            "no marques este tag, escala con "
                            "ORDER_REGISTRATION_FAILED."
                        ),
                    },
                    ensure_ascii=False,
                )

        data["tag"] = tag
        data["motivo"] = motivo

        now_ms = int(time.time() * 1000)
        history = data.setdefault("status_history", [])
        history.append(
            {
                "tag": tag,
                "motivo": motivo,
                "active_route": data.get("active_route", ROUTE_VENTAS),
                "timestamp": time.time(),
            }
        )

        # Episode lifecycle: cierre formal del episodio activo si el tag
        # es de cierre. Idempotente: si el episodio ya está cerrado, no
        # rompe (close_episode devuelve None y seguimos). Backfill lazy
        # cubre sesiones legacy sin `episodes[]`.
        #
        # `msgs_count_at_close` (FU3): snapshot del total de líneas del
        # JSONL al cerrar — el listing use case lo usa para calcular
        # `msgs_in_episode = at_close - at_start` exacto.
        closed_episode_id: str | None = None
        if tag in CLOSING_TAGS:
            msgs_at_close = count_session_jsonl_lines(
                self._vault_dir, ctx.session_key
            )
            closed_ep = close_episode(
                data,
                closing_tag=tag,
                closing_motivo=motivo,
                now_ms=now_ms,
                msgs_count_at_close=msgs_at_close,
            )
            if closed_ep is not None:
                # `close_episode` retorna None si era idempotente (ya estaba
                # cerrado) — en ese caso NO emitimos el evento (el episodio
                # ya cerró antes; el watchdog ya fue señalado en su momento).
                closed_episode_id = closed_ep.get("episode_id")

        metadata_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

        # ADR-001: la tool no abre temporal_client. Si el tag indica seguimiento,
        # devuelve una decision serializada para que el workflow programe el remarketing.
        response: dict[str, Any] = {
            "message": f"Éxito. Interacción etiquetada como '{tag}'.",
        }
        if tag == "INTERESADO":
            response["schedule_remarketing"] = {
                "session_id": ctx.session_key,
                "motivo": motivo,
                "delay_seconds": 60,
            }
            response[
                "message"
            ] += " Se programó un ciclo de remarketing automáticamente."

        # HU-WA24H-001 Sprint 2: si cerramos episodio activo (closed_ep no
        # None), emitir una decisión `episode_closed` para que el workflow
        # dispatchee `EpisodeClosedEvent`. El dispatcher manifest routea esa
        # event al `cancel_watchdog` signal del workflow del watchdog. Sin
        # esto, un watchdog programado para un episodio que cerró seguiría
        # corriendo y dispararía un nudge cuando ya no hay nada que vender.
        if closed_episode_id is not None:
            response["episode_closed"] = {
                "session_id": ctx.session_key,
                "episode_id": closed_episode_id,
                "closing_tag": tag,
            }

        return json.dumps(response, ensure_ascii=False)
