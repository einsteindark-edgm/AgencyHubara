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
and returns a JSON envelope (`message` + decisiones); the workflow parses the
envelope and issues dispatcher activities. No `temporal_client`, no
`start_workflow`, no workflow imports.

Reactivación post-INTERESADO: NO la dispara esta tool. Desde PR #113 la
transition `sales_to_remarketing_on_interested` no existe (guard:
tests/plugins/chats/test_manifest_no_immediate_remarketing.py) — el envelope
`schedule_remarketing` que emitía acá era un no-op río abajo y el `message`
le mentía al LLM ("Se programó un ciclo de remarketing automáticamente").
La reactivación la decide el ciclo del Window Strategist (plugin
`reengagement`) según silencio × calor del lead.

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
    get_active_episode,
)
from src.plugins.chats.shared.funnel import enqueue_capi_for_tag
from src.plugins.chats.shared.purchase_signals import has_purchase_confirmation
from src.sdk.textkit import keep_customer_safe_sentences, sanitize_llm_text

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

# Tags que NO cierran solos: van SIEMPRE en combo con `escalate_to_human`
# (valor = la razón que el guion exige). Tras ellos el `llm_chat` siguiente no
# es un canal ambiguo: el modelo tiene trabajo real (escalar con su resumen
# para el colega). Todo otro tag es AUTOSUFICIENTE: después de él no queda
# nada por hacer, y pedirle "un mensaje más" al modelo es abrirle el canal del
# acuse (run b06636a6: "Etiqueta registrada."). La tool lo DECLARA en el
# envelope (`tag_closure.ends_turn`) y `run_agent_turn` decide el corte.
_ESCALATION_REASON_BY_TAG: dict[str, str] = {
    "CONFIRMADO_SIN_DATOS": "ORDER_PENDING_SHIPPING_DETAILS",
    "CONFIRMADO_PAGO_PENDIENTE": "PAYMENT_VERIFICATION_PENDING",
}


class ManageConversationTagTool(ToolBase):
    """Register the final commercial tag for a conversation, plus the reason.

    Used at end-of-sale or when the user loses interest. La reactivación de
    un INTERESADO NO la dispara esta tool (ver nota en el docstring del
    módulo): la decide el ciclo del Window Strategist leyendo el tag y el
    estado que esta tool persiste en metadata.
    """

    name = "manage_conversation_tag"
    description = (
        "Úsala al final de la venta, o si el usuario pierde el interés. "
        "Registra la etiqueta final ('INTERESADO', 'RECHAZO', "
        "'COMPRA_EXITOSA', 'CONFIRMADO_SIN_DATOS', "
        "'CONFIRMADO_PAGO_PENDIENTE') y un resumen breve. Con INTERESADO o "
        "RECHAZO TERMINA tu turno: si el cliente está en la conversación, tu "
        "despedida viaja en `customer_message`."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "tag": {
                "type": "string",
                "enum": _TAG_ENUM,
                "description": (
                    "Etiqueta final. Una de: INTERESADO (cliente sigue "
                    "dudando o se enfrió — el sistema decidirá aparte si "
                    "y cuándo hacer seguimiento), RECHAZO (no compra, "
                    "cierre definitivo), "
                    "COMPRA_EXITOSA (cierre con venta concretada Y pago "
                    "verificado por un humano — esta tag la pone el "
                    "humano desde el dashboard, NO el LLM), "
                    "CONFIRMADO_SIN_DATOS (cliente confirmó la compra "
                    "pero NO completó los datos de envío — usa esta tag "
                    "SIEMPRE en combo con `escalate_to_human"
                    "(reason_category=ORDER_PENDING_SHIPPING_DETAILS)` "
                    "para que un humano cierre la operación pidiendo los "
                    "datos faltantes), "
                    "CONFIRMADO_PAGO_PENDIENTE (cliente confirmó el "
                    "pedido + dio todos los datos de envío + `register_order` "
                    "devolvió `registered=true`; el LLM NO sabe si el pago "
                    "se efectuó porque no hay pasarela integrada — usa "
                    "esta tag SIEMPRE en combo con `escalate_to_human"
                    "(reason_category=PAYMENT_VERIFICATION_PENDING)` para "
                    "que un humano verifique el pago en el dashboard de "
                    "orders. NO uses COMPRA_EXITOSA para este caso — esa "
                    "tag la pone el humano cuando confirma el pago)."
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
            "customer_message": {
                "type": "string",
                "description": (
                    "Tu línea de cierre para el cliente cuando él está en la "
                    "conversación (dijo que no, se despidió, lo va a pensar). "
                    "Es lo ÚNICO que lee en este turno: tu content se descarta "
                    "y con INTERESADO o RECHAZO esta tool termina tu turno. "
                    "Cálida, de tú y en primera persona, respondiendo a lo que "
                    "acaba de decir; sin etiquetas ni procesos internos. "
                    "Ejemplo: 'Con gusto, aquí estaré por si más adelante te "
                    "animas 🤍'. En el cierre por inactividad (el cliente ya "
                    "no está) NO lo mandes."
                ),
            },
        },
        # `customer_message` NO va en required a propósito: una sesión en vuelo
        # (tool_definitions del bootstrap pre-deploy) llama sin el param y no
        # debe rebotar en validate_params; y en el cierre por ghosting no hay
        # cliente a quien hablarle.
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
        self, ctx: ToolContext, tag: str, motivo: str, customer_message: str = ""
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

        # 2026-09-14 (run 01a0a0f1): CONFIRMADO_SIN_DATOS exige que el
        # cliente haya dicho que sí. Sin confirmación registrada en el
        # episodio, la etiqueta se degrada a INTERESADO (remarketing) y NO se
        # escala: el bot sigue a cargo cuando el cliente vuelva a escribir.
        degraded_from: str | None = None
        if tag == "CONFIRMADO_SIN_DATOS" and not has_purchase_confirmation(data):
            degraded_from = tag
            tag = "INTERESADO"
            motivo = (
                "[degradado de CONFIRMADO_SIN_DATOS: sin confirmación de compra "
                f"registrada en el episodio] {motivo}"
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

        # Auditoría CAPI 2026-09-08: señal de embudo para Meta (INTERESADO →
        # QualifiedLead, CONFIRMADO_* → LeadSubmitted, COMPRA_EXITOSA →
        # Purchase). Mismo helper que el endpoint humano/MBA → un solo
        # event_id por señal, un solo envío. Lo manda el flusher del turno.
        _active_ep = get_active_episode(data)
        enqueue_capi_for_tag(
            data,
            tag=tag,
            session_id=ctx.session_key,
            now_ms=now_ms,
            source="manage_conversation_tag",
            episode_id=str((_active_ep or {}).get("episode_id") or "") or None,
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

        # ADR-001: la tool no abre temporal_client. NO emite decisión de
        # remarketing: el envelope `schedule_remarketing` era un no-op desde
        # PR #113 (transition eliminada) y su `message` le mentía al LLM. El
        # parser del workflow (`workflow_helpers`) sigue entendiendo el
        # envelope SOLO para replay de histories viejas — acá no se produce.
        # El `message` DESCRIBE un hecho; no da órdenes ni tiene forma de
        # reporte (L-20). El viejo "Éxito. Interacción etiquetada como 'X'."
        # invitaba al acuse y quedaba en el historial como few-shot de "tras
        # una tool administrativa se responde con un parte de estado". Va en
        # CONDICIONAL porque la tool no sabe si el turno es de cliente o admin
        # y el texto queda grabado: tiene que ser cierto en los dos.
        escalation_reason = _ESCALATION_REASON_BY_TAG.get(tag)
        response: dict[str, Any] = {
            "message": (
                "Hecho. Si el cliente sigue en la conversación, tu próximo "
                "mensaje lo lee él."
                if escalation_reason is None
                else (
                    "Hecho. El relevo todavía no está pedido: corresponde "
                    f"`escalate_to_human` con reason_category='{escalation_reason}' "
                    "(la despedida viaja en su `customer_message`)."
                )
            ),
            # Cierre declarado: `run_agent_turn` corta el turno cuando
            # `ends_turn` (gate `tag-ends-turn-v1`). Va con el tag EFECTIVO: un
            # CONFIRMADO_SIN_DATOS degradado cierra como INTERESADO.
            "tag_closure": {"tag": tag, "ends_turn": escalation_reason is None},
        }
        # Texto para el cliente (solo en tags autosuficientes; en los combo la
        # despedida viaja en `escalate_to_human`). Tres estados que el loop
        # distingue: clave AUSENTE = el modelo no mandó texto (cierre por
        # ghosting, o va a responder en su siguiente mensaje); clave con texto
        # = lo único que lee el cliente; clave VACÍA = el modelo habló y nada
        # era seguro → el turno termina en silencio en vez de reabrirle el
        # canal. Se valida ACÁ (activity) y el workflow solo lee el resultado
        # grabado: un regex que decide commands es lógica de replay (L-21).
        # Etiqueta DEGRADADA → nunca se declara texto: el modelo lo redactó
        # bajo una premisa que esta tool rechazó (creía cerrar un
        # CONFIRMADO_SIN_DATOS: "un colega te escribe por los datos de envío")
        # y nadie va a escalar. Sin texto, en turno de cliente el loop no corta
        # y el modelo responde con el aviso de degradación a la vista (el
        # mecanismo del fix del run 01a0a0f1); en turno admin corta igual.
        if (
            escalation_reason is None
            and degraded_from is None
            and customer_message.strip()
        ):
            response["tag_closure"]["customer_message"] = (
                keep_customer_safe_sentences(
                    sanitize_llm_text(customer_message).text
                )
            )
        if degraded_from is not None:
            response["degraded_from"] = degraded_from
            response["message"] = (
                "Sin confirmación de compra registrada en este episodio (el "
                "cliente nunca dijo que sí), la etiqueta quedó como INTERESADO "
                "(remarketing automático). NO llames escalate_to_human: el bot "
                "sigue a cargo."
            )

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
