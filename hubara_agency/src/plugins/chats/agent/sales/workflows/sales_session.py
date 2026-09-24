from __future__ import annotations

import asyncio
import dataclasses
import json
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from exoclaw_temporal.config import SessionInput
    from src.platform.orchestration import (
        dispatch_event_activity,
        envelope_for,
    )
    from src.platform.whatsapp.activities import send_whatsapp_message_activity
    from src.platform.temporal.dispatcher import (
        schedule_remarketing_workflow_activity,
        start_or_signal_sales_workflow_activity,
    )
    from src.platform.temporal.retry_policies import _LLM_OPTIONS
    from src.sdk.agentkit import (
    is_no_message_abstention,
    looks_like_admin_leak,
    salvage_customer_text,
    strip_portavelas_notice,
)
    from src.platform.workflow_helpers import (
        InboxMsg,
        PendingMessage,
        TurnPolicy,
        coalesce_inbox,
        coalesce_pending,
        run_agent_turn,
    )
    from src.plugins.chats.agent.sales.perception.activities import (
        perceive_burst_activity,
        verify_coverage_activity,
    )
    from src.plugins.chats.agent.sales.perception.contracts import (
        PerceiveInput,
        PerceiveOutput,
        VerifyInput,
        VerifyOutput,
    )
    from src.plugins.chats.agent.sales.perception.plan import (
        PlanTopic,
        TurnPlan,
        checklist_note,
        complement_message,
        pending_round_note,
        uncovered_topics,
    )
    from src.platform.session_history.activities import (
        persist_assistant_message_activity,
    )
    from src.platform.whatsapp.activities import send_typing_indicator_activity
    from src.plugins.chats.agent.sales.activities import (
        apply_variant_enumeration_guard_activity,
        bootstrap_sales_session_activity,
        build_first_contact_greeting_activity,
        decide_ghosting_action,
        ensure_closing_escalation_activity,
        ensure_payment_pending_closure_activity,
        flush_pending_ui_intents_activity,
        persist_turn_trace_activity,
        read_and_clear_pending_handoff_activity,
        read_idle_timeout_seconds_activity,
        read_order_draft_note_activity,
    )
    from src.platform.contracts import EpisodeClosedDecision
    from src.plugins.chats.agent.sales.contracts import SalesSessionInput
    from src.plugins.chats.agent.sales.first_contact_greeting import (
        should_send_first_contact_greeting,
    )
    from src.plugins.chats.agent.sales.turn_trace import (
        build_turn_payload,
        context_note_names,
    )
    from src.platform.whatsapp.capi_activity import (
        LEAD_CLOSING_TAGS,
        PURCHASE_CLOSING_TAGS,
        flush_capi_outbox_activity,
        send_capi_event_activity,
    )
    from src.plugins.chats.shared.contracts.events import (
        EpisodeClosedEvent,
        SalesSessionCompletionEvent,
    )


# HU-WA24H-001 Sprint CAPI: helper module-level que mapea el closing_tag al
# CAPI event_name correspondiente. Vive afuera del workflow body porque los
# frozenset son inmutables y los lookups son O(1) determinísticos (R-DET
# safe). Devuelve None para tags que no disparan CAPI (RECHAZO / GHOSTED
# / TIMEOUT).
def _map_closing_tag_to_capi_event(closing_tag: str) -> str | None:
    """Map a sales episode closing tag to its CAPI event name, or None."""
    if closing_tag in PURCHASE_CLOSING_TAGS:
        return "Purchase"
    if closing_tag in LEAD_CLOSING_TAGS:
        # "LeadSubmitted", NO "Lead" — Meta rechaza "Lead" para
        # business_messaging (error 2804066; smoke test 2026-07-01).
        return "LeadSubmitted"
    return None


# Module-level anchor que evita que ruff strippee el import de
# `send_capi_event_activity` antes de que el workflow body lo referencie en
# `workflow.execute_activity(...)`. El workflow body usa el import
# directamente (no `_CAPI_ACTIVITY`), así que esta línea es defensa
# pura contra el formatter — se puede borrar cuando el workflow body
# garantice el uso.
_CAPI_ACTIVITY: object = send_capi_event_activity


# Fix integridad orden↔tag (patrón A): closing tags que EXIGEN escalación a
# humano. Si el LLM marca el tag (cierra el episodio) pero no llama
# `escalate_to_human`, el workflow garantiza la escalación correspondiente vía
# `ensure_closing_escalation_activity`. CONFIRMADO_PAGO_PENDIENTE NO va acá: lo
# cubre la red disparada por `order_registered_decision` (que además puede
# forzar el cierre del episodio). RECHAZO / COMPRA_EXITOSA no requieren humano.
_CLOSING_TAGS_REQUIRING_ESCALATION: dict[str, str] = {
    "CONFIRMADO_SIN_DATOS": "ORDER_PENDING_SHIPPING_DETAILS",
}


_CONTINUE_AS_NEW_AFTER_TURNS = 50

_IDLE_TIMEOUT = timedelta(minutes=1)

# Trailing debounce: tras el primer signal, esperamos hasta `_DEBOUNCE_SILENCE`
# de silencio antes de procesar. Cada signal nuevo resetea el timer. Si el
# cliente nunca para de escribir, el cap `_DEBOUNCE_MAX_WAIT` fuerza el
# procesamiento. Replay-safe: `workflow.wait_condition` + timeouts deterministicos.
_DEBOUNCE_SILENCE = timedelta(seconds=1.5)
_DEBOUNCE_MAX_WAIT = timedelta(seconds=12)
# Fase 1 interrupción: máximo de restarts limpios de un turno cuando el
# cliente escribe mientras el LLM piensa (D3 del refinamiento burst-inbox).
# Tras el cap, el turno corre hasta el final y lo pendiente va al siguiente.
_MAX_TURN_RESTARTS = 2


# Despedida mínima del cierre si el guard del portavelas vació el texto del
# LLM (todo el mensaje hablaba del portavelas): el cliente que acaba de dar
# sus datos NUNCA recibe silencio. Mismo copy base del guion `etapa_cierre`.
_ORDER_REGISTERED_FALLBACK_FAREWELL = (
    "Listo, tu pedido quedó registrado 🤍. Gracias por elegir a Hubara."
)


# ── Traza v2 (plan del laboratorio §4.1): helpers puros del registro ────────
# Solo manipulan listas en memoria con el reloj del workflow: no agregan
# commands a la history (replay-safe sin patch, L-22).


def _now_ms() -> int:
    return int(workflow.now().timestamp() * 1000)


_RAW_TEXT_MAX = 2000


def _clean_inbound_meta(raw: object) -> dict[str, Any] | None:
    """`{wamid, ts_ms, kind, text}` de la señal, con cada campo validado; lo
    que no tenga la forma esperada queda en None (nunca falla: es solo traza).
    `text` es lo que escribió el cliente, sin lo que el ingest le agrega al
    turno para el LLM (campaña citada, resumen del episodio anterior)."""
    if not isinstance(raw, dict):
        return None
    wamid, ts_ms, kind, text = raw.get("wamid"), raw.get("ts_ms"), raw.get("kind"), raw.get("text")
    return {
        "wamid": wamid if isinstance(wamid, str) and wamid else None,
        "ts_ms": int(ts_ms) if isinstance(ts_ms, (int, float)) and not isinstance(ts_ms, bool) else None,
        "kind": kind if isinstance(kind, str) and kind else "text",
        "text": text[:_RAW_TEXT_MAX] if isinstance(text, str) else None,
    }


def _inbound_trace(batch: list[PendingMessage]) -> list[dict[str, Any]]:
    """`inbound[]` de la traza: los mensajes del cliente del turno, en orden de
    llegada (sin el trigger de ghosting ni los handoff)."""
    out: list[dict[str, Any]] = []
    for p in batch:
        if p.is_ghost_trigger or p.is_handoff:
            continue
        meta = p.inbound_meta or {}
        out.append(
            {
                "seq": len(out) + 1,
                "wamid": meta.get("wamid"),
                "ts_ms": meta.get("ts_ms"),
                "kind": meta.get("kind") or "text",
                "text": p.message,
            }
        )
    return out


def _note_guard(
    steps: list[dict],
    guards: list[str],
    name: str,
    *,
    before: str | None = None,
    after: str | None = None,
    v1: bool = True,
) -> None:
    """Una guarda que actuó: paso v2 en su lugar del turno y, si `v1`, su nombre
    en el campo v1 `guards` (el que lee el scorecard; no se le suman nombres
    nuevos para que los checks no cambien)."""
    steps.append({"kind": "guard", "at_ms": _now_ms(), "name": name, "before": before, "after": after})
    if v1:
        guards.append(name)


def _text_outbound(text: str, delivered: object) -> dict:
    """Paso `outbound` de un envío de texto. `delivered` es lo que devolvió
    `send_whatsapp_message_activity`: `[{wamid, text}]` desde la traza v2;
    `None` en histories anteriores (no se sabe si salió)."""
    if isinstance(delivered, list):
        bubbles = [
            {"kind": "text", "text": b.get("text", ""), "wamid": b.get("wamid") or None, "delivered": True}
            for b in delivered
            if isinstance(b, dict)
        ] or [{"kind": "text", "text": text, "delivered": False}]
    else:
        bubbles = [{"kind": "text", "text": text, "delivered": None}]
    return {"kind": "outbound", "at_ms": _now_ms(), "bubbles": bubbles}


def _flush_outbound(report: object) -> dict | None:
    """Paso `outbound` del flush de componentes (`[{kind, wamid, ok}]`). Las
    histories anteriores devuelven la cantidad (int): no hay detalle."""
    if not isinstance(report, list) or not report:
        return None
    bubbles = [
        {"kind": str(r.get("kind") or "ui"), "wamid": r.get("wamid"), "delivered": bool(r.get("ok"))}
        for r in report
        if isinstance(r, dict)
    ]
    return {"kind": "outbound", "at_ms": _now_ms(), "bubbles": bubbles} if bubbles else None


# ── Capas del turno con clasificador (plan del laboratorio §3.2, PR 14) ──────
# El modo llega en el 4.º argumento de la señal (`inbound_meta`); sin modo o
# con `off` NO se consulta `workflow.patched("perception-v1")` y el turno es
# idéntico al de hoy (ni un command nuevo en la history).
_PERCEPTION_MODES = ("off", "shadow", "canary", "on")
_LAYER_MODES = ("shadow", "canary", "on")
_ACTING_MODES = ("canary", "on")
_DEFAULT_PERCEPTION_PROFILE = "jev-v1"
_PERCEPTION_OPTIONS: dict[str, Any] = {
    "start_to_close_timeout": timedelta(seconds=10),
    "retry_policy": RetryPolicy(maximum_attempts=1),
}


def _perception_settings(raw: object) -> tuple[str | None, str | None]:
    """(modo, perfil) de la señal; lo que no tenga la forma esperada no cuenta."""
    if not isinstance(raw, dict):
        return None, None
    mode, profile = raw.get("perception_mode"), raw.get("perception_profile")
    mode = mode if isinstance(mode, str) and mode in _PERCEPTION_MODES else None
    valid_profile = isinstance(profile, str) and 0 < len(profile) <= 40 and profile.replace("-", "").isalnum()
    return mode, (profile if valid_profile else None)


def _burst_messages(batch: list[PendingMessage]) -> list[dict[str, Any]]:
    """Los mensajes del cliente del turno para el clasificador (sin triggers):
    el texto crudo del cliente si viajó, si no el mensaje del turno."""
    out = []
    for p in batch:
        if p.is_ghost_trigger or p.is_handoff or p.is_complement_trigger:
            continue
        meta = p.inbound_meta or {}
        text = meta.get("text") or p.message
        if (text or "").strip():
            out.append({"text": text, "ts_ms": meta.get("ts_ms")})
    return out


def _perception_step(out: PerceiveOutput, started_ms: int, *, mode: str) -> dict[str, Any]:
    # La duración es la del clasificador (la mide el adaptador): en sombra el
    # resultado se lee después de enviar, y `ahora - inicio` sería el turno
    # entero (la vara del canary, p95 < 1500 ms, nunca se cumpliría). Payload
    # de la traza: sin efecto en el replay (L-22).
    return {
        "kind": "perception",
        "at_ms": started_ms,
        "dur_ms": out.latency_ms or (_now_ms() - started_ms),
        "latency_ms": out.latency_ms,
        "model": out.model,
        "profile": out.profile,
        "mode": mode,
        "fallback": None if out.ok else (out.error or "error"),
        "answers": list(out.answers),
        "cost_usd": out.cost_usd,
    }


def _reply_as_sent(already_sent: list[str], outgoing: str | None) -> str:
    """Lo que el cliente recibe en el turno, para la verificación (②/③): las
    burbujas que ya salieron (saludo de primer contacto, textos previos a las
    tools) y el texto final si va a salir, ya pasado por las guardas. La misma
    vara en sombra (lo lee de lo enviado) y en activo (antes de enviarlo).
    Solo arma el payload de `verify_coverage`: no agrega commands (L-22)."""
    return "\n\n".join(t for t in [*already_sent, outgoing or ""] if t)


def _verify_step(out: VerifyOutput, started_ms: int, *, applied: bool) -> dict[str, Any]:
    return {
        "kind": "verify",
        "at_ms": started_ms,
        "dur_ms": _now_ms() - started_ms,
        "model": out.model,
        "decision": out.decision,
        "missing": list(out.missing),
        "answers": list(out.answers),
        "applied": applied,
        "fallback": None if out.ok else (out.error or "error"),
        "cost_usd": out.cost_usd,
        # True solo si este turno agendó el complemento (lo lee el
        # laboratorio para esperar ese segundo turno sin adivinar).
        "complement_scheduled": False,
    }


def _plan_of(out: PerceiveOutput) -> TurnPlan:
    return TurnPlan(
        ok=out.ok,
        topics=tuple(PlanTopic(str(t.get("topic")), t.get("msg"), t.get("p")) for t in out.topics if t.get("topic")),
        stage=out.stage,
    )


def _turn_policy(plan: TurnPlan) -> TurnPolicy | None:
    """Capa ②: una ronda más si el corte por tool deja un asunto del plan."""
    if not plan.topics:
        return None

    def extra_round_note(tools_used: list[str], text: str) -> str | None:
        missing = uncovered_topics(plan, tools_used=tools_used, text=text)
        return pending_round_note(missing) if missing else None

    return TurnPolicy(extra_round_note=extra_round_note)


@workflow.defn(name="HubaraSalesSessionWorkflow")
class HubaraSalesSessionWorkflow:
    """Long-running session workflow with ghosting injection mechanism."""

    def __init__(self) -> None:
        self._pending: list[PendingMessage] = []
        self._last_response: str | None = None
        self._processing = False
        self._force_shutdown: bool = False
        # Capas del turno con clasificador (PR 14): modo y perfil de la última
        # señal que los trajo, y los asuntos que quedaron pendientes (③).
        self._perception_mode: str = "off"
        self._perception_profile: str = _DEFAULT_PERCEPTION_PROFILE
        self._pending_topics: list[str] = []

    @workflow.signal
    async def send_message(
        self,
        message: str,
        media: list[str] | None = None,
        plugin_context: list[str] | None = None,
        inbound_meta: Any = None,
    ) -> None:
        """Mensaje del cliente. `inbound_meta` (4.º argumento, opcional) trae
        `{wamid, ts_ms, kind}` para la traza. Tipado `Any` a propósito: un
        valor que no decodifique como el tipo anotado hace que Temporal
        DESCARTE la señal entera (y con ella el mensaje del cliente); acá se
        limpia sin fallar."""
        mode, profile = _perception_settings(inbound_meta)
        if mode is not None:
            self._perception_mode = mode
        if profile is not None:
            self._perception_profile = profile
        self._pending.append(
            PendingMessage(
                message=message,
                media=media,
                plugin_context=plugin_context,
                inbound_meta=_clean_inbound_meta(inbound_meta),
            )
        )

    @workflow.query
    def get_last_response(self) -> str | None:
        return self._last_response

    @workflow.query
    def is_processing(self) -> bool:
        return self._processing

    async def _handoff_draft_context(self, session_id: str) -> list[str] | None:
        """plugin_context del turno de HANDOFF: la note del draft del pedido.

        Incidente 2026-07-17 (run 019f6db3): el bloque `[DATOS DEL PEDIDO YA
        CONFIRMADOS]` solo se inyectaba en el path del webhook — el turno de
        handoff Remarketing→Sales arrancaba ciego aunque el draft estuviera
        intacto en el vault, y el LLM re-preguntaba (y pisaba) lo ya elegido.

        Gated (R-DET): histories pre-deploy replayean sin la activity nueva.
        """
        if not workflow.patched("handoff-draft-note-v1"):
            return None
        draft_note = await workflow.execute_activity(
            read_order_draft_note_activity,
            session_id,
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
        return [draft_note] if draft_note else None

    async def _perceive(self, inp: PerceiveInput) -> PerceiveOutput:
        """Capa ①: nunca tumba el turno (fail-open)."""
        try:
            return await workflow.execute_activity(perceive_burst_activity, inp, **_PERCEPTION_OPTIONS)
        except Exception as exc:  # noqa: BLE001
            return PerceiveOutput(ok=False, profile=inp.profile, error=f"activity: {type(exc).__name__}")

    async def _verify(self, inp: VerifyInput) -> VerifyOutput:
        """Capa ③: sin verificación, el turno sale como hoy (`send`)."""
        try:
            return await workflow.execute_activity(verify_coverage_activity, inp, **_PERCEPTION_OPTIONS)
        except Exception as exc:  # noqa: BLE001
            return VerifyOutput(ok=False, decision="send", error=f"activity: {type(exc).__name__}")

    def _coalesce_batch(self, batch: list[PendingMessage]) -> PendingMessage:
        """Coalescea una ráfaga en un solo turno (nota de ráfaga incluida).

        Convertir a InboxMsg es puro (seq = orden de llegada — determinista en
        replay: el orden de `_pending` es el orden de los signals, garantizado
        por Temporal). Con >1 mensaje del cliente, el LLM recibe la lista
        explícita de lo que escribió (nota en plugin_context) y responde al
        hilo COMPLETO. Preserva is_handoff (L-12).

        Cadena de gates (más nuevo primero — patrón patched/deprecate de
        Temporal, R-DET/L-9): ejecuciones nuevas graban y toman v2 (nota solo
        con mensajes CON texto + dedupe de plugin_context); histories con el
        marker v1 (deploy 2026-07-01) replayean v1; histories pre-PR#100
        replayean coalesce_pending.

        Usada en dos puntos: el coalesce inicial del debounce y la
        RECOMPOSICIÓN del turno cuando el cliente escribe mientras el LLM
        piensa (Fase 1 interrupción).
        """
        inbox_batch = [
            InboxMsg(
                seq=i,
                wamid=(p.inbound_meta or {}).get("wamid"),
                text=p.message,
                ts_ms=(p.inbound_meta or {}).get("ts_ms") or 0,
                media=p.media,
                plugin_context=p.plugin_context,
                is_handoff=p.is_handoff,
            )
            for i, p in enumerate(batch, 1)
        ]
        if workflow.patched("burst-note-v2"):
            return coalesce_inbox(inbox_batch, version=2)
        if workflow.patched("burst-note-v1"):
            return coalesce_inbox(inbox_batch)
        return coalesce_pending(batch)

    @workflow.run
    async def run(self, input: SalesSessionInput) -> None:
        # Bootstrap: construye SessionInput fuera del workflow (R-DET).
        # Reemplaza el `build_workspace_config` + `get_base_tools_registry`
        # que antes ejecutaba el caller (service.py / dispatcher_activities)
        # antes de `start_workflow`. Patron simetrico al de Remarketing (F6.1).
        # PR-A: pasamos el SalesSessionInput completo. El campo
        # `runtime_workspace_path` viaja para PR-B sin romper la signature de
        # la activity en futuras iteraciones.
        session: SessionInput = await workflow.execute_activity(
            bootstrap_sales_session_activity,
            input,
            **_LLM_OPTIONS,
        )
        turn_count = input.turn_count

        # Handoff Remarketing→Sales (Fix 3, gated): el dispatcher escribio
        # `pending_handoff_summary` en metadata.json al transferir. Lo leemos +
        # clearamos atomicamente y seedeamos `_pending` con un marker
        # `is_handoff=True`. El coalesce lo va a mover a plugin_context cuando
        # construya el prompt (no contamina el rol "user" de la conversacion
        # como hacia el legacy `[SISTEMA INTERNO]` signal).
        if workflow.patched("handoff-via-metadata-v1"):
            initial_handoff = await workflow.execute_activity(
                read_and_clear_pending_handoff_activity,
                session.session_id,
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )
            if initial_handoff:
                self._pending.append(
                    PendingMessage(
                        message=initial_handoff,
                        is_handoff=True,
                        plugin_context=await self._handoff_draft_context(
                            session.session_id
                        ),
                    )
                )

        while True:
            # Dynamic ghosting timeout (sesión c4e3416f, gated): por default
            # son 60s, pero si acabamos de enviar un WhatsApp Flow nativo y
            # estamos esperando el `nfm_reply`, se extiende hasta 10 min para
            # que el cliente tenga tiempo de llenar el formulario sin que se
            # dispare ghosting prematuramente (que arrancaría remarketing y
            # ruteo el nfm_reply ahí cuando llegue).
            if workflow.patched("dynamic-idle-timeout-v1"):
                idle_timeout_seconds = await workflow.execute_activity(
                    read_idle_timeout_seconds_activity,
                    session.session_id,
                    start_to_close_timeout=timedelta(seconds=5),
                    retry_policy=RetryPolicy(maximum_attempts=2),
                )
                effective_idle_timeout = timedelta(seconds=idle_timeout_seconds)
            else:
                # Pre-patch workflows en replay caen al timeout legacy fijo
                # para preservar determinismo (R-DET).
                effective_idle_timeout = _IDLE_TIMEOUT

            try:
                await workflow.wait_condition(
                    lambda: len(self._pending) > 0,
                    timeout=effective_idle_timeout,
                )
            except asyncio.TimeoutError:
                # L-12 (run 3607aecc): ANTES de declarar ghosting, chequear si
                # hay un handoff pendiente en metadata. El handoff NO despierta
                # el wait_condition (viaja por metadata, no por signal): un
                # mensaje del cliente que remarketing convirtió en handoff
                # durante la ventana de transferencia ("Usuario respondió:
                # Dame 3") quedaba dormido hasta este timeout — y el flujo
                # viejo lo coalesceaba JUNTO con el trigger de ghosting, con
                # `_force_shutdown=True` suprimiendo la respuesta. Acá: si hay
                # handoff (o llegó un signal en la race del timeout), se
                # procesa como turno normal y NO hay ghosting este ciclo.
                handled_without_ghosting = False
                if workflow.patched("ghost-checks-handoff-first-v1"):
                    late_handoff = await workflow.execute_activity(
                        read_and_clear_pending_handoff_activity,
                        session.session_id,
                        start_to_close_timeout=timedelta(seconds=10),
                        retry_policy=RetryPolicy(maximum_attempts=2),
                    )
                    if late_handoff:
                        self._pending.append(
                            PendingMessage(
                                message=late_handoff,
                                is_handoff=True,
                                plugin_context=await self._handoff_draft_context(
                                    session.session_id
                                ),
                            )
                        )
                        handled_without_ghosting = True
                    elif self._pending:
                        handled_without_ghosting = True

                if not handled_without_ghosting:
                    workflow.logger.info(f"Ghosting detectado para sesión {session.session_id}. Inyectando trigger de auto-etiquetado.")
                    ghost_trigger = await workflow.execute_activity(
                        decide_ghosting_action,
                        start_to_close_timeout=timedelta(seconds=10),
                        retry_policy=RetryPolicy(maximum_attempts=2),
                    )

                    self._pending.append(
                        PendingMessage(
                            message=ghost_trigger, is_ghost_trigger=True
                        )
                    )
                    self._force_shutdown = True

            # Handoff refresh per-iteration (Fix 3, gated): si Sales ya estaba
            # corriendo cuando el dispatcher escribio handoff, el bootstrap NO
            # se reejecuta. Leemos metadata aca para captar handoffs que
            # llegaron mientras el workflow procesaba un turno previo.
            if workflow.patched("handoff-refresh-per-iteration-v1"):
                handoff_refresh = await workflow.execute_activity(
                    read_and_clear_pending_handoff_activity,
                    session.session_id,
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=RetryPolicy(maximum_attempts=2),
                )
                if handoff_refresh:
                    self._pending.append(
                        PendingMessage(message=handoff_refresh, is_handoff=True)
                    )

            # Trailing debounce con reset (Fix 1, gated): tras el primer
            # signal, esperamos hasta `_DEBOUNCE_SILENCE` de silencio. Cada
            # signal nuevo resetea el timer. Cap absoluto `_DEBOUNCE_MAX_WAIT`
            # protege contra clientes que tipean sin parar. Workflows
            # pre-deploy caen al path legacy (un mensaje por turno) por
            # `workflow.patched()` — replay-safe.
            if workflow.patched("trailing-debounce-coalesce-v1"):
                debounce_start = workflow.now()
                while True:
                    snapshot_len = len(self._pending)
                    elapsed = workflow.now() - debounce_start
                    cap_remaining = _DEBOUNCE_MAX_WAIT - elapsed
                    if cap_remaining <= timedelta(0):
                        break
                    timeout = min(_DEBOUNCE_SILENCE, cap_remaining)
                    try:
                        await workflow.wait_condition(
                            lambda: len(self._pending) > snapshot_len,
                            timeout=timeout,
                        )
                        # llego algo nuevo → siguiente iter con snapshot fresco
                        # = reset implicito del timer
                    except asyncio.TimeoutError:
                        break  # silencio achieved

                batch = list(self._pending)
                self._pending.clear()
                # Complemento de la capa ③ (PR 14): si el cliente escribió
                # mientras tanto, su mensaje manda y el complemento se descarta
                # (lo que quedó pendiente lo vuelve a ver la percepción).
                # Solo existe con el marker `perception-v1`: sin patch propio.
                if any(p.is_complement_trigger for p in batch):
                    customers = [p for p in batch if not p.is_complement_trigger]
                    batch = customers or [p for p in batch if p.is_complement_trigger][-1:]
                msgs_to_process: list[PendingMessage]
                # Bandeja/watermark (PR burst-inbox): la ráfaga se convierte a
                # InboxMsg y se coalescea en `_coalesce_batch` (nota de ráfaga
                # incluida). `raw_batch` se conserva para poder RECOMPONER el
                # turno si el cliente escribe mientras el LLM piensa (Fase 1
                # interrupción, ver el loop de restart más abajo).
                raw_batch = batch
                if len(batch) == 1 and batch[0].is_complement_trigger:
                    msgs_to_process = [batch[0]]
                else:
                    msgs_to_process = [self._coalesce_batch(batch)]
            else:
                # Legacy: un mensaje a la vez. Solo para workflows pre-deploy.
                raw_batch = None
                msgs_to_process = []
                while self._pending:
                    msgs_to_process.append(self._pending.pop(0))

            # Turno ADMIN estructural (run 5f43bcd0): si el batch contiene el
            # trigger de ghosting, el texto del turno es reporte interno y
            # JAMÁS se enruta al cliente (send/persist/typing/flush). Antes la
            # supresión dependía SOLO de `_force_shutdown`, y el corrientazo
            # (que legítimamente lo limpia — el cliente volvió) dejaba pasar
            # el resumen administrativo al canal. La propiedad es del batch,
            # no del lifecycle de la sesión. patched(): histories en vuelo
            # (incl. la del incidente, que SÍ envió) replayean sin la rama.
            turn_is_admin = any(
                p.is_ghost_trigger for p in (raw_batch or [])
            )

            # Invalidación de premisa PRE-turno (premortem C2, simétrico
            # inverso del run 5f43bcd0): si junto al trigger de ghosting hay
            # un mensaje REAL (el cliente escribió en la ventana entre el
            # timeout y el turno), el ghosting quedó invalidado ANTES de
            # arrancar — dropeamos el trigger, limpiamos el shutdown y el
            # turno responde normal. Sin esto, la respuesta se suprimía por
            # `_force_shutdown` y la sesión se apagaba tragándose el mensaje
            # (el cancel-shutdown del cierre solo mira `_pending`, ya vacío).
            if (
                turn_is_admin
                and any(not p.is_ghost_trigger for p in raw_batch)
                and workflow.patched("ghost-premise-invalidated-pre-turn-v1")
            ):
                workflow.logger.info(
                    "Mensaje del cliente en la ventana de ghosting: premisa "
                    "invalidada pre-turno — trigger dropeado, shutdown "
                    "cancelado, turno normal."
                )
                raw_batch = [
                    p for p in raw_batch if not p.is_ghost_trigger
                ]
                msgs_to_process = [self._coalesce_batch(raw_batch)]
                self._force_shutdown = False
                turn_is_admin = False

            admin_no_send = turn_is_admin and workflow.patched(
                "admin-turn-no-send-v1"
            )

            for msg in msgs_to_process:
                self._processing = True

                try:
                    # Typing indicator outbound (Fix 5, gated): mostrar
                    # "escribiendo..." al cliente. Best-effort, no bloquea
                    # el turno si la API de WhatsApp falla. Turno admin: no
                    # mostramos "escribiendo..." de un turno que jamás va a
                    # escribir (el cliente veía typing y después silencio).
                    if workflow.patched("typing-indicator-v1") and not admin_no_send:
                        try:
                            await workflow.execute_activity(
                                send_typing_indicator_activity,
                                session.session_id,
                                start_to_close_timeout=timedelta(seconds=5),
                                retry_policy=RetryPolicy(maximum_attempts=1),
                            )
                        except Exception:
                            pass

                    # Fase 1 interrupción ("corrientazo", run eda8d460): si el
                    # cliente escribe MIENTRAS el LLM piensa y el turno aún no
                    # tocó al cliente, `run_agent_turn` aborta (interrupted) y
                    # acá recomponemos: batch viejo + pendientes → un solo
                    # turno que considera TODO. Cap de restarts para no morir
                    # de inanición si el cliente tipea sin parar (tras el cap,
                    # has_new_input=None desactiva los checkpoints y el turno
                    # corre hasta el final; lo pendiente va al ciclo próximo).
                    # `workflow.patched`: histories pre-deploy → hni None →
                    # shape idéntico al viejo (R-DET/L-9).
                    # HU-SC-0: inicio del turno (reloj del workflow, determinista)
                    # y acumuladores de la traza. Listas en memoria: no agregan
                    # commands a la history.
                    turn_started_ms = int(workflow.now().timestamp() * 1000)
                    trace_sent_texts: list[str] = []
                    trace_guards: list[str] = []
                    trace_suppressed: str | None = None
                    # Traza v2: pasos de TODOS los intentos del turno, en orden.
                    trace_steps: list[dict] = []
                    restarts = 0
                    # Capas ①②③ (plan del laboratorio §3.2, PR 14). `patched`
                    # SOLO se consulta con un modo activo: con `off` (o sin
                    # modo) el turno no graba ni un command nuevo.
                    is_complement = msg.is_complement_trigger
                    turn_mode = (
                        self._perception_mode
                        if raw_batch is not None
                        and not admin_no_send
                        and not msg.is_handoff
                        and not is_complement
                        else "off"
                    )
                    layers = turn_mode in _LAYER_MODES and workflow.patched("perception-v1")
                    if not layers:
                        turn_mode = "off"
                    plan: TurnPlan | None = None
                    shadow_handle = None
                    shadow_started_ms = 0
                    if layers and turn_mode == "shadow":
                        shadow_burst = _burst_messages(raw_batch or [])
                        if shadow_burst:
                            # Sombra: en paralelo al LLM; no suma espera.
                            shadow_started_ms = _now_ms()
                            shadow_handle = workflow.start_activity(
                                perceive_burst_activity,
                                PerceiveInput(
                                    session_id=session.session_id,
                                    profile=self._perception_profile,
                                    messages=shadow_burst,
                                    pending=list(self._pending_topics),
                                ),
                                **_PERCEPTION_OPTIONS,
                            )
                    while True:
                        hni = None
                        policy: TurnPolicy | None = None
                        if layers and turn_mode in _ACTING_MODES:
                            burst = _burst_messages(raw_batch or [])
                            if burst:
                                perceived_ms = _now_ms()
                                perceived = await self._perceive(
                                    PerceiveInput(
                                        session_id=session.session_id,
                                        profile=self._perception_profile,
                                        messages=burst,
                                        pending=list(self._pending_topics),
                                    )
                                )
                                trace_steps.append(_perception_step(perceived, perceived_ms, mode=turn_mode))
                                plan = _plan_of(perceived)
                                note = checklist_note(plan)
                                if note:
                                    trace_steps.append(
                                        {
                                            "kind": "plan",
                                            "at_ms": _now_ms(),
                                            "checklist": [
                                                {"topic": t.topic, "msg": t.msg, "p": t.p} for t in plan.topics
                                            ],
                                        }
                                    )
                                    msg = dataclasses.replace(
                                        msg, plugin_context=[*(msg.plugin_context or []), note]
                                    )
                                    policy = _turn_policy(plan)
                        if (
                            raw_batch is not None
                            and restarts < _MAX_TURN_RESTARTS
                            and workflow.patched("turn-interrupt-v1")
                        ):
                            hni = lambda: bool(self._pending)  # noqa: E731
                        # `admin_turn` (run b06636a6): el helper termina el
                        # turno apenas el tag declara su cierre (sin el
                        # llm_chat del acuse) y no le hace recordar al LLM un
                        # texto que nunca salió. Se pasa en CADA vuelta: un
                        # corrientazo que dropea el trigger de ghosting baja
                        # `admin_no_send` y el re-run es un turno normal.
                        result = await run_agent_turn(
                            session,
                            msg,
                            has_new_input=hni,
                            admin_turn=admin_no_send,
                            # Episodio abierto por campaña: el historial del
                            # LLM arranca ahí (runs edbb0d8b / 8e73b7dc).
                            align_history_with_episode=True,
                            # Run 28a8e407: el párrafo de razonamiento se cae
                            # ANTES de grabar — el LLM recuerda lo que salió.
                            salvage_leaked_text=True,
                            # Capa ② (PR 14): None salvo con modo activo.
                            turn_policy=policy,
                        )
                        trace_steps.extend(result.steps or [])
                        if result.interrupted:
                            restarts += 1
                            drained = list(self._pending)
                            self._pending.clear()
                            trace_steps.append(
                                {
                                    "kind": "restart",
                                    "at_ms": _now_ms(),
                                    "reason": "checkpoint_a",
                                    "attempt": restarts,
                                    "drained": len(drained),
                                }
                            )
                            raw_batch = [
                                p for p in [*(raw_batch or []), *drained] if not p.is_complement_trigger
                            ]
                            msg = self._coalesce_batch(raw_batch)
                            is_complement = False
                            # Run 48ec6df5 (caso 573229041190): un corrientazo
                            # durante el turno de ghosting invalida su premisa
                            # — el cliente SÍ volvió. Sin esto, el recompose
                            # consume el mensaje de `_pending` y la guarda
                            # cancel-shutdown de abajo (que solo mira
                            # `_pending` al cierre del turno) no dispara:
                            # el flush de UI intents se saltea por
                            # `_force_shutdown` y la sesión se apaga con la
                            # respuesta encolada sin enviar. Dentro de este
                            # loop el flag solo puede venir de ghosting (la
                            # escalation lo setea DESPUÉS de run_agent_turn,
                            # y un turno con escalation_decision nunca se
                            # interrumpe), así que limpiarlo es seguro.
                            if self._force_shutdown and workflow.patched(
                                "interrupt-cancels-ghost-shutdown-v1"
                            ):
                                workflow.logger.info(
                                    "Corrientazo durante turno de ghosting: "
                                    "el cliente volvió — cancelando el "
                                    "shutdown programado."
                                )
                                self._force_shutdown = False
                                # Run 5f43bcd0: cancelar el shutdown no basta.
                                # El batch recompuesto conservaba el trigger
                                # [SISTEMA] de ghosting → el re-run era un
                                # híbrido admin+cliente: el LLM etiquetaba
                                # INTERESADO y su resumen administrativo (con
                                # el flag ya limpio) salía al cliente, mientras
                                # el mensaje real ("Caballero") quedaba sin
                                # respuesta. La premisa del ghosting quedó
                                # invalidada → DROPEAMOS el trigger y el re-run
                                # es un turno normal sobre lo que el cliente
                                # escribió. (Si el filtro dejara el batch
                                # vacío — no debería: el corrientazo implica
                                # al menos un mensaje real — conservamos el
                                # batch original por defensividad.)
                                if workflow.patched(
                                    "ghost-trigger-drop-on-recompose-v1"
                                ):
                                    non_admin = [
                                        p
                                        for p in raw_batch
                                        if not p.is_ghost_trigger
                                    ]
                                    if non_admin:
                                        raw_batch = non_admin
                                        msg = self._coalesce_batch(raw_batch)
                                        admin_no_send = False
                            continue
                        break
                    self._last_response = result.final_content
                    turn_count += 1
                    # Id determinista del turno (traza v2): no depende del
                    # contador de la activity de la traza, que puede correrse.
                    turn_key = f"run:{workflow.info().run_id}/t:{turn_count}"

                    # Abstención explícita (incidente wa_573125671604,
                    # 2026-07-17 23:15 UTC): en un turno de handoff sin
                    # mensaje del cliente y sin venta pendiente, el LLM
                    # declinaba en prosa ("No hay mensaje nuevo del
                    # cliente… No genero respuesta") y esa deliberación se
                    # enviaba al cliente. El framing de handoff ahora ofrece
                    # el sentinel NO_MESSAGE; acá lo honramos: no enviar,
                    # no persistir — Sales sigue dueño y el loop continúa
                    # (el ghost cierra después como siempre).
                    # workflow.patched(): histories en vuelo no tienen la rama.
                    abstained = False
                    if workflow.patched("no-message-abstention-v1"):
                        abstained = is_no_message_abstention(
                            result.final_content
                        )
                    if abstained:
                        _note_guard(
                            trace_steps, trace_guards, "no_message",
                            before=result.final_content, after="", v1=False,
                        )

                    # ADR-001 + ADR-2026-05-20: si la tool emitio una decision,
                    # convertirla a un completion event y dispatchar por manifest.
                    # NO importar workflow classes de sibling agents (R-DIP #10).
                    #
                    # SOLO-REPLAY: desde la limpieza post-PR#113 la tool de tags
                    # ya NO emite `schedule_remarketing` (la transition que
                    # consumia el evento se elimino del manifest y el dispatcher
                    # lo no-op-eaba). Este branch queda para replay de histories
                    # viejas cuyo output de tool grabado SI trae el envelope
                    # (el parser de workflow_helpers lo sigue entendiendo).
                    # Ejecuciones nuevas: siempre None. Removible junto con el
                    # deprecate_patch de abajo cuando drenen las histories.
                    #
                    # workflow.patched(): pre-deploy histories tienen
                    # `schedule_remarketing_workflow_activity` directo; el patched
                    # gate evita NondeterminismError al replay-arlos. Tras drain,
                    # `workflow.deprecate_patch("declarative-orchestration-v1")`.
                    if result.schedule_remarketing is not None:
                        if workflow.patched("declarative-orchestration-v1"):
                            # Level 3 declarative path: emit event, dispatcher routes via manifest.
                            await workflow.execute_activity(
                                dispatch_event_activity,
                                envelope_for(
                                    SalesSessionCompletionEvent(
                                        session_id=result.schedule_remarketing.session_id,
                                        tag="INTERESADO",
                                        motivo=result.schedule_remarketing.motivo,
                                        delay_seconds=result.schedule_remarketing.delay_seconds,
                                    ),
                                    source_plugin="chats",
                                    source_worker="sales",
                                ),
                                start_to_close_timeout=timedelta(seconds=30),
                                retry_policy=RetryPolicy(maximum_attempts=3),
                            )
                        else:
                            # Legacy path for pre-deploy workflows (replay-safe).
                            await workflow.execute_activity(
                                schedule_remarketing_workflow_activity,
                                result.schedule_remarketing,
                                start_to_close_timeout=timedelta(seconds=30),
                                retry_policy=RetryPolicy(maximum_attempts=3),
                            )
                    if result.transfer_decision is not None:
                        # L-12 (run 3607aecc): una transfer_decision DENTRO de
                        # sales es siempre una AUTOtransferencia (la tool
                        # transfiere HACIA ventas y este workflow YA es ventas).
                        # El legacy "self-loop" ejecutaba start_or_signal: pisaba
                        # `pending_handoff_summary` ajeno (perdió el "Dame 3"
                        # del cliente, escrito por remarketing 2s antes) y el
                        # LLM regurgitaba el `message` interno de la tool como
                        # respuesta final → "El control ha sido transferido al
                        # agente de ventas." llegó al cliente. La tool ya no
                        # está registrada en este worker (Fix A); esta rama es
                        # defensa en profundidad para el window de workflows
                        # vivos con tool_definitions viejos.
                        if workflow.patched("sales-self-transfer-noop-v1"):
                            workflow.logger.warning(
                                "transfer_decision emitida DENTRO de sales "
                                "(autotransferencia) — noop: no se escribe "
                                "handoff ni se envía el texto del turno."
                            )
                            _note_guard(
                                trace_steps, trace_guards, "self_transfer_noop",
                                before=result.final_content, after="",
                            )
                            result.final_content = ""
                        else:
                            # Rama legacy solo para replay de histories
                            # pre-deploy (R-DET).
                            await workflow.execute_activity(
                                start_or_signal_sales_workflow_activity,
                                result.transfer_decision,
                                start_to_close_timeout=timedelta(seconds=30),
                                retry_policy=RetryPolicy(maximum_attempts=3),
                            )

                    # RED DE SEGURIDAD orden↔tag (fix integridad): si el LLM
                    # registró una orden con éxito (`order_registered_decision`),
                    # garantizamos el cierre "pago pendiente" + la escalación
                    # AUNQUE el LLM no haya emitido `manage_conversation_tag` /
                    # `escalate_to_human`. La secuencia de cierre la decide el
                    # LLM en tool calls separadas (coordinadas solo por el
                    # prompt); Temporal reanuda el turno tras un crash pero NO
                    # fuerza al LLM a emitir las tools. Esta activity es
                    # idempotente: no pisa si el LLM ya cerró.
                    #
                    # workflow.patched(): workflows en vuelo pre-deploy NO
                    # tienen esta activity en su history — el gate evita
                    # NondeterminismError al replay-arlos. Tras el drain
                    # (idle 1min en Sales), `deprecate_patch`.
                    episode_closed_decision = result.episode_closed_decision
                    safety_net_escalated = False
                    # Premortem C3 (run 5f43bcd0): las redes de seguridad NO
                    # setean `_force_shutdown` directo — eso suprimía el
                    # final_content legítimo del turno ("tu pedido quedó
                    # registrado...") porque los bloques de send de abajo
                    # guardan con `not self._force_shutdown`. El shutdown se
                    # difiere y se aplica DESPUÉS del send/persist/flush,
                    # espejo del path de escalación del LLM.
                    shutdown_after_send = False
                    if (
                        result.order_registered_decision is not None
                        and workflow.patched("ensure-order-closure-v1")
                    ):
                        closure = await workflow.execute_activity(
                            ensure_payment_pending_closure_activity,
                            args=[
                                result.order_registered_decision.session_id,
                                result.order_registered_decision.order_id,
                                result.order_registered_decision.motivo,
                            ],
                            start_to_close_timeout=timedelta(seconds=15),
                            retry_policy=RetryPolicy(maximum_attempts=3),
                        )
                        # Si la red cerró el episodio (el LLM no lo hizo —
                        # mutuamente excluyente con el path del LLM, porque si
                        # el LLM cerró, `closure.acted=False`), adoptamos su
                        # decisión para emitir EpisodeClosedEvent + CAPI abajo
                        # sin duplicar.
                        if closure.acted:
                            _note_guard(trace_steps, trace_guards, "safety_net_order_closure")
                        if closure.acted and episode_closed_decision is None:
                            episode_closed_decision = EpisodeClosedDecision(
                                session_id=result.order_registered_decision.session_id,
                                episode_id=closure.closed_episode_id,
                                closing_tag=closure.closing_tag,
                            )
                        if closure.escalated:
                            # La red escaló a humano → cerrar el workflow como
                            # cualquier escalation (cliente queda en cola
                            # humana; `active_route=humano` bloquea dispatch
                            # de mensajes futuros). Shutdown DIFERIDO (C3):
                            # primero sale la despedida del turno, después se
                            # apaga — igual que la escalación del LLM.
                            #
                            # workflow.patched(): histories en vuelo pre-fix
                            # suprimieron los sends de este turno; el gate las
                            # replayea con el shape viejo (flag inmediato).
                            if workflow.patched(
                                "safety-net-shutdown-after-send-v1"
                            ):
                                shutdown_after_send = True
                            else:
                                self._force_shutdown = True
                            safety_net_escalated = True

                    # HU-WA24H-001 Sprint 2: el episodio cerró con un
                    # CLOSING_TAG (vía `ManageConversationTagTool` O vía la red
                    # de seguridad de arriba). Emitir `EpisodeClosedEvent` para
                    # que el dispatcher manifest signale `cancel_watchdog` al
                    # watchdog del episodio. Gated bajo el mismo patch que el
                    # path declarativo — workflows en vuelo pre-deploy NO ven
                    # este branch (replay-safe).
                    if (
                        episode_closed_decision is not None
                        and workflow.patched("watchdog-event-emit-v1")
                    ):
                        await workflow.execute_activity(
                            dispatch_event_activity,
                            envelope_for(
                                EpisodeClosedEvent(
                                    session_id=episode_closed_decision.session_id,
                                    episode_id=episode_closed_decision.episode_id,
                                    closing_tag=episode_closed_decision.closing_tag,
                                ),
                                source_plugin="chats",
                                source_worker="sales",
                            ),
                            start_to_close_timeout=timedelta(seconds=30),
                            retry_policy=RetryPolicy(maximum_attempts=3),
                        )

                    # HU-WA24H-001 Sprint CAPI: cuando el episodio cierra,
                    # disparar el CAPI event correspondiente (Lead /
                    # Purchase) si aplica. La activity tiene guards
                    # internos completos (ctwa_clid presente + dentro de
                    # los 7d de attribution window, terminal_event check,
                    # idempotencia via event_id estable), así que si no
                    # corresponde, hace un "skipped_*" silencioso sin
                    # raise.
                    #
                    # Falla del CAPI NUNCA bloquea el workflow — la
                    # atribución a ads es secundaria al flujo del
                    # cliente. Catcheamos cualquier excepción y seguimos.
                    # La activity ya loguea el outcome (sent / skipped /
                    # failed) — observabilidad vive en logs + metadata.
                    #
                    # workflow.patched(): gating para evitar
                    # NondeterminismError al replay de workflows en vuelo
                    # pre-deploy. Cuando todos los workflows pre-CAPI
                    # hayan drainado (idle timeout 1min en sales),
                    # `workflow.deprecate_patch("capi-event-emit-v1")`.
                    if (
                        episode_closed_decision is not None
                        and workflow.patched("capi-event-emit-v1")
                    ):
                        capi_event_name = _map_closing_tag_to_capi_event(
                            episode_closed_decision.closing_tag
                        )
                        if capi_event_name is not None:
                            try:
                                await workflow.execute_activity(
                                    send_capi_event_activity,
                                    args=[
                                        episode_closed_decision.session_id,
                                        episode_closed_decision.episode_id,
                                        capi_event_name,
                                    ],
                                    start_to_close_timeout=timedelta(seconds=30),
                                    retry_policy=RetryPolicy(maximum_attempts=3),
                                )
                            except Exception as exc:  # noqa: BLE001
                                workflow.logger.warning(
                                    "CAPI dispatch falló (non-blocking): "
                                    f"session={episode_closed_decision.session_id} "
                                    f"event={capi_event_name} err={exc!r}"
                                )

                    # RED DE SEGURIDAD escalación (patrón A): un closing tag que
                    # EXIGE escalación a humano (ej. CONFIRMADO_SIN_DATOS) cerró
                    # el episodio, pero el LLM NO llamó `escalate_to_human`. El
                    # workflow garantiza la escalación faltante. Skip si ya se
                    # escaló (LLM o la red de pago pendiente). Gated por patch
                    # (replay-safe). CONFIRMADO_PAGO_PENDIENTE no entra al mapa:
                    # lo cubre la red de `order_registered_decision`.
                    if (
                        episode_closed_decision is not None
                        and not safety_net_escalated
                        and result.escalation_decision is None
                        and workflow.patched("ensure-closing-escalation-v1")
                    ):
                        _esc_reason = _CLOSING_TAGS_REQUIRING_ESCALATION.get(
                            episode_closed_decision.closing_tag
                        )
                        if _esc_reason is not None:
                            _escalated = await workflow.execute_activity(
                                ensure_closing_escalation_activity,
                                args=[
                                    episode_closed_decision.session_id,
                                    _esc_reason,
                                    "Cierre con datos de envío pendientes — "
                                    "un humano debe pedir los datos faltantes.",
                                ],
                                start_to_close_timeout=timedelta(seconds=15),
                                retry_policy=RetryPolicy(maximum_attempts=3),
                            )
                            if _escalated:
                                _note_guard(trace_steps, trace_guards, "safety_net_closing_escalation")
                                # Shutdown DIFERIDO (C3) — ver la rama de la
                                # red orden↔tag de arriba: mismo patch, misma
                                # razón (la despedida sale antes de apagar).
                                if workflow.patched(
                                    "safety-net-shutdown-after-send-v1"
                                ):
                                    shutdown_after_send = True
                                else:
                                    self._force_shutdown = True
                                safety_net_escalated = True

                    # SALUDO DE PRIMER CONTACTO (runs dc32f7fe /
                    # 3ce50ef3, CTWA "amor y amistad", 2026-09-10/11):
                    # el LLM saludó como content JUNTO a `search_products`
                    # (descartado por el default-deny de abajo) y cerró el
                    # turno con `present_products` → el cliente recibió el
                    # menú SIN saludo. Cuando es el primer intercambio de la
                    # conversación y el turno tocó al cliente por una tool
                    # outbound sin que ningún texto client-facing salude, el
                    # workflow garantiza la Burbuja 1 del guion ANTES de
                    # cualquier texto y del flush del menú. Decisión pura
                    # (`should_send_first_contact_greeting`); la hora vive en
                    # la activity (R-DET). No aplica en handoff (un humano ya
                    # habló con el cliente) ni en turnos sin envío.
                    #
                    # workflow.patched(): histories en vuelo pre-deploy no
                    # tienen estos sends; tras el drain (idle 1min en Sales),
                    # eliminar el if + `deprecate_patch("first-contact-greeting-v1")`.
                    if (
                        result.first_contact
                        and not msg.is_handoff
                        and not self._force_shutdown
                        and not abstained
                        and not admin_no_send
                        and workflow.patched("first-contact-greeting-v1")
                        and should_send_first_contact_greeting(
                            first_contact=result.first_contact,
                            tools_used=list(result.tools_used or []),
                            client_texts=[
                                *result.pre_tool_messages,
                                *result.outbound_tool_texts,
                                result.final_content or "",
                            ],
                        )
                    ):
                        greeting = await workflow.execute_activity(
                            build_first_contact_greeting_activity,
                            start_to_close_timeout=timedelta(seconds=10),
                            retry_policy=RetryPolicy(maximum_attempts=3),
                        )
                        workflow.logger.info(
                            "first-contact-greeting: el turno salió por tool "
                            f"sin saludo (tools={result.tools_used}); enviando "
                            "la burbuja de apertura antes del menú."
                        )
                        _note_guard(
                            trace_steps, trace_guards, "first_contact_greeting",
                            before="", after=greeting,
                        )
                        greeting_delivered = await workflow.execute_activity(
                            send_whatsapp_message_activity,
                            args=[session.session_id, greeting],
                            start_to_close_timeout=timedelta(seconds=90),
                            retry_policy=RetryPolicy(maximum_attempts=2),
                        )
                        trace_steps.append(_text_outbound(greeting, greeting_delivered))
                        await workflow.execute_activity(
                            persist_assistant_message_activity,
                            args=[session.session_id, greeting],
                            start_to_close_timeout=timedelta(seconds=10),
                            retry_policy=RetryPolicy(maximum_attempts=2),
                        )
                        trace_sent_texts.append(greeting)

                    # SALUDO DESCARTADO (bug run ddd0d472 / session-wa_573125671604):
                    # cuando el LLM emite texto client-facing JUNTO con una tool
                    # call (ej. "Buenos días. Bienvenido a *Hubara*..." encolando
                    # send_quick_replies), ese texto antes NO llegaba al cliente —
                    # el loop solo enviaba `final_content` (el content del último
                    # mensaje sin tools). `run_agent_turn` ahora los expone en
                    # `result.pre_tool_messages`; los mandamos como burbujas en
                    # orden ANTES del final_content y los persistimos al store del
                    # dashboard igual que final_content. Skip en force_shutdown
                    # (ghosting/escalation no envían texto).
                    #
                    # workflow.patched(): histories en vuelo pre-deploy NO tienen
                    # estos sends en su shape; el gate evita NondeterminismError al
                    # replay-arlas. Tras el drain (idle 1min en Sales), eliminar el
                    # if + `workflow.deprecate_patch("send-pre-tool-messages-v1")`.
                    if (
                        result.pre_tool_messages
                        and not self._force_shutdown
                        and not abstained
                        and not admin_no_send
                        and workflow.patched("send-pre-tool-messages-v1")
                    ):
                        for pre_msg in result.pre_tool_messages:
                            # Misma última línea que el final_content: burbuja
                            # pre-tool con olor administrativo no sale.
                            if workflow.patched(
                                "admin-text-guard-v1"
                            ) and looks_like_admin_leak(
                                pre_msg,
                                extended=workflow.patched(
                                    "admin-leak-patterns-v2"
                                ),
                            ):
                                workflow.logger.warning(
                                    "admin-text-guard: pre_tool bloqueado: "
                                    f"{pre_msg[:120]!r}"
                                )
                                _note_guard(
                                    trace_steps, trace_guards, "admin_text_guard_pre_tool",
                                    before=pre_msg, after="", v1=False,
                                )
                                continue
                            pre_delivered = await workflow.execute_activity(
                                send_whatsapp_message_activity,
                                args=[session.session_id, pre_msg],
                                start_to_close_timeout=timedelta(seconds=90),
                                retry_policy=RetryPolicy(maximum_attempts=2),
                            )
                            trace_steps.append(_text_outbound(pre_msg, pre_delivered))
                            trace_sent_texts.append(pre_msg)
                            if workflow.patched("persist-assistant-message-v1"):
                                await workflow.execute_activity(
                                    persist_assistant_message_activity,
                                    args=[session.session_id, pre_msg],
                                    start_to_close_timeout=timedelta(seconds=10),
                                    retry_policy=RetryPolicy(maximum_attempts=2),
                                )

                    # UN SOLO MENSAJE en pickers de variante (bug run fe86d4e4):
                    # cuando el LLM llama `present_variant_picker`, el render de
                    # texto del intent (intro + opciones + invitación a elegir)
                    # YA es el mensaje completo del agente. Si además mandáramos
                    # `final_content`, el cliente vería DOS burbujas repitiendo
                    # la misma pregunta. Suprimimos el send de texto en ese caso
                    # — el flush del intent (más abajo) entrega el picker como
                    # burbuja única. Gated por patch para no romper el replay de
                    # workflows en vuelo (R-DET): histories pre-deploy no tienen
                    # el marker → patched()=False → mandan el texto como antes.
                    # Excepción (run 5ed9af2d): si el turno ESCALÓ, el texto es
                    # la despedida del relevo (`customer_message`) y sale
                    # siempre — un batch [picker, escalate] dejaba al cliente
                    # escalado y sin una palabra. Replay-safe sin patch
                    # propio: en histories pre `escalation-ends-turn-v1` el
                    # picker cortaba el turno con final_content="" (L-11), así
                    # que ningún command dependía de este flag en ese caso.
                    suppress_text_for_picker = (
                        workflow.patched("suppress-text-when-variant-picker-v1")
                        and "present_variant_picker" in result.tools_used
                        and result.escalation_decision is None
                    )
                    # Guarda de enumeración de variantes (run 9bd495be,
                    # 2026-09-14): el LLM listó los 11 aromas como texto plano
                    # en vez del picker. Si el texto final enumera 4+ aromas o
                    # colores del catálogo y el turno no emitió picker, la
                    # activity encola el picker (formato curado) y acá se
                    # suprime el texto plano — el flush entrega el picker.
                    # Gate para replay-safety; tras el drain,
                    # `deprecate_patch("variant-enumeration-guard-v1")`.
                    if (
                        result.final_content
                        and not suppress_text_for_picker
                        and "present_variant_picker" not in result.tools_used
                        and not self._force_shutdown
                        and not abstained
                        and not admin_no_send
                        and workflow.patched("variant-enumeration-guard-v1")
                    ):
                        replaced_by_picker = await workflow.execute_activity(
                            apply_variant_enumeration_guard_activity,
                            args=[session.session_id, result.final_content],
                            start_to_close_timeout=timedelta(seconds=15),
                            retry_policy=RetryPolicy(maximum_attempts=2),
                        )
                        if replaced_by_picker:
                            workflow.logger.warning(
                                "variant-enumeration-guard: el texto final "
                                "enumeraba variantes; reemplazado por el "
                                f"picker: {result.final_content[:120]!r}"
                            )
                            suppress_text_for_picker = True
                            _note_guard(
                                trace_steps, trace_guards, "variant_enumeration_guard",
                                before=result.final_content, after="",
                            )
                            trace_suppressed = "variant_enumeration_guard"
                    if admin_no_send and result.final_content:
                        # Observabilidad del turno admin: el LLM produjo texto
                        # pese a la instrucción de silencio — lo suprimimos y
                        # dejamos rastro. NO vive en el historial del LLM:
                        # `run_agent_turn(admin_turn=True)` lo recorta antes de
                        # `record_turn` (run b06636a6); queda en la traza.
                        workflow.logger.warning(
                            "turno admin: final_content suprimido (no va al "
                            f"cliente): {result.final_content[:120]!r}"
                        )
                        _note_guard(
                            trace_steps, trace_guards, "admin_turn",
                            before=result.final_content, after="", v1=False,
                        )
                    # Última línea determinista (run 5f43bcd0 + premortem D1):
                    # aunque el turno sea normal, texto que huele a reporte
                    # administrativo ("etiquetada como `INTERESADO`", envelope
                    # de tool regurgitado) NO sale al cliente. patched():
                    # histories en vuelo que SÍ enviaron replayean sin la rama.
                    # Incidente 943e6bff (2026-09-07): pedido SIN portavelas
                    # cerró con "se escogen los colores del portavelas". La
                    # tool decide contra el catálogo si el pedido lo incluye
                    # (`portavelas_included`); si NO, ninguna oración sobre el
                    # portavelas sale al cliente ni se persiste, aunque el LLM
                    # la escriba igual. Solo el turno que registró la orden
                    # (la despedida). patched(): histories en vuelo que SÍ la
                    # enviaron replayean con el texto original.
                    if (
                        result.order_registered_decision is not None
                        and not result.order_registered_decision.portavelas_included
                        and result.final_content
                        and "portavela" in result.final_content.lower()
                        and workflow.patched("portavelas-notice-guard-v1")
                    ):
                        stripped = strip_portavelas_notice(result.final_content)
                        workflow.logger.warning(
                            "portavelas-notice-guard: pedido sin portavelas, "
                            "oración removida de la despedida: "
                            f"{result.final_content[:120]!r}"
                        )
                        _note_guard(
                            trace_steps, trace_guards, "portavelas_notice_guard",
                            before=result.final_content,
                            after=stripped or _ORDER_REGISTERED_FALLBACK_FAREWELL,
                        )
                        result.final_content = (
                            stripped or _ORDER_REGISTERED_FALLBACK_FAREWELL
                        )
                    # Set de patrones VERSIONADO (run 5ed9af2d): el veredicto decide
                    # commands, así que los patrones posteriores al set original solo
                    # aplican bajo su propio patch — histories pre-deploy que SÍ
                    # enviaron un texto que hoy cazarían replayean con el set viejo.
                    if result.salvaged_leak:
                        trace_guards.append("admin_text_salvaged")
                    leak_blocked = (
                        bool(result.final_content)
                        and workflow.patched("admin-text-guard-v1")
                        and looks_like_admin_leak(
                            result.final_content,
                            extended=workflow.patched("admin-leak-patterns-v2"),
                        )
                    )
                    if leak_blocked:
                        _note_guard(
                            trace_steps, trace_guards, "admin_text_guard",
                            before=result.final_content, after="",
                        )
                        workflow.logger.warning(
                            "admin-text-guard: final_content bloqueado (texto "
                            f"administrativo): {result.final_content[:120]!r}"
                        )
                        # Rescate (run edbb0d8b): deliberación como primer
                        # párrafo + respuesta real después → bloquear TODO
                        # dejaba al cliente sin respuesta. Se cae solo el
                        # párrafo filtrado. Gated: cambia si se agenda el send.
                        if workflow.patched("admin-text-salvage-v1"):
                            salvaged = salvage_customer_text(
                                result.final_content,
                                extended=workflow.patched("admin-leak-patterns-v2"),
                            )
                            if salvaged:
                                _note_guard(
                                    trace_steps, trace_guards, "admin_text_salvaged",
                                    before=result.final_content, after=salvaged,
                                )
                                result.final_content = salvaged
                                leak_blocked = False
                    # Capa ③ (PR 14): con el texto ya pasado por las guardas y
                    # ANTES de enviarlo, el clasificador verifica que atienda
                    # cada asunto del plan. `complement` → una burbuja más como
                    # turno de sistema (se encola tras el envío); `pending` → se
                    # envía y el asunto queda pendiente para la percepción del
                    # turno siguiente.
                    verify_out: VerifyOutput | None = None
                    if (
                        layers
                        and turn_mode in _ACTING_MODES
                        and plan is not None
                        and plan.topics
                        and not self._force_shutdown
                        and not admin_no_send
                    ):
                        # Lo que el cliente recibe en el turno: lo ya enviado
                        # (saludo de primer contacto, textos previos) + el texto
                        # final si sale (una guarda que lo retiene lo saca).
                        verify_reply = _reply_as_sent(
                            trace_sent_texts,
                            None
                            if (leak_blocked or suppress_text_for_picker or abstained)
                            else result.final_content,
                        )
                        verified_ms = _now_ms()
                        verify_out = await self._verify(
                            VerifyInput(
                                session_id=session.session_id,
                                profile=self._perception_profile,
                                messages=_burst_messages(raw_batch or []),
                                topics=[{"topic": t.topic, "msg": t.msg, "p": t.p} for t in plan.topics],
                                reply_text=verify_reply,
                                components=[t for t in result.tools_used if t.startswith(("present_", "send_", "request_"))],
                            )
                        )
                        trace_steps.append(_verify_step(verify_out, verified_ms, applied=True))
                    if (
                        result.final_content
                        and not self._force_shutdown
                        and not abstained
                        and not admin_no_send
                        and not leak_blocked
                    ):
                        # Evitamos enviar respuestas vacías o alucinar respuestas internas durante auto-cierres
                        if not suppress_text_for_picker:
                            final_delivered = await workflow.execute_activity(
                                send_whatsapp_message_activity,
                                args=[session.session_id, result.final_content],
                                start_to_close_timeout=timedelta(seconds=90),
                                retry_policy=RetryPolicy(maximum_attempts=2)
                            )
                            trace_steps.append(
                                _text_outbound(result.final_content, final_delivered)
                            )
                            trace_sent_texts.append(result.final_content)
                        elif trace_suppressed is None:
                            # El LLM llamó al selector: su texto no sale (el
                            # selector ES el mensaje, run fe86d4e4).
                            _note_guard(
                                trace_steps, trace_guards, "variant_picker_text",
                                before=result.final_content, after="", v1=False,
                            )
                        # Persistir la respuesta al JSONL DESPUES del send: si el
                        # send falla y retry, no contaminamos el log con mensajes
                        # que el cliente nunca vio. El dashboard lee este JSONL
                        # para mostrar el lado del agente en el panel central.
                        #
                        # workflow.patched(): los workflows ya en vuelo (history
                        # generado antes de este deploy) NO tienen la activity
                        # en su history; el patched gate evita NondeterminismError
                        # al replay-arlos. Workflows nuevos siempre ven True.
                        # Cuando el idle timeout (1min en Sales) garantiza que no
                        # quedan in-flight pre-patch, eliminar el if + el patch_id
                        # con `workflow.deprecate_patch()` en el deploy siguiente.
                        if workflow.patched("persist-assistant-message-v1"):
                            # `tools_used` (3er arg, opcional): evidencia para el
                            # juez del eval — sin esto el juez no ve que el turno
                            # llamó search_products / escalate_to_human y puntúa
                            # falsos negativos (caso ep_010, run fa1eb974).
                            # patched(): runs en vuelo replayean con el shape de
                            # 2 args de su history (L-9).
                            _persist_args: list = [
                                session.session_id, result.final_content
                            ]
                            if workflow.patched("persist-tools-used-v1"):
                                _persist_args.append(list(result.tools_used or []))
                            await workflow.execute_activity(
                                persist_assistant_message_activity,
                                args=_persist_args,
                                start_to_close_timeout=timedelta(seconds=10),
                                retry_policy=RetryPolicy(maximum_attempts=2),
                            )

                    # HU-002: render UI intents que las decision tools encolaron.
                    # Las tools `present_*`, `request_*`, `react_to_message`,
                    # `send_contact_card`, `send_cta_url` escriben intents a
                    # `metadata.json[pending_ui_intents]`. Esta activity los
                    # dispatch a `send_*` del cliente WA después de que el
                    # texto del LLM ya se envió, para que el cliente vea el
                    # texto + componente visual juntos.
                    #
                    # workflow.patched(): histories pre-HU-002 no tienen este
                    # branch — el patched gate evita NondeterminismError al
                    # replay-ar workflows en vuelo del deploy anterior.
                    if (
                        not self._force_shutdown
                        and not admin_no_send
                        and workflow.patched("flush-ui-intents-v1")
                    ):
                        flush_report = await workflow.execute_activity(
                            flush_pending_ui_intents_activity,
                            args=[session.session_id],
                            start_to_close_timeout=timedelta(seconds=120),
                            retry_policy=RetryPolicy(maximum_attempts=2),
                        )
                        flush_step = _flush_outbound(flush_report)
                        if flush_step is not None:
                            trace_steps.append(flush_step)

                    # Auditoría CAPI 2026-09-08: flush del outbox de eventos
                    # de Meta que el turno encoló (tools + UI intents +
                    # cierres). Una activity por turno; su falla NUNCA
                    # bloquea al cliente. workflow.patched(): histories en
                    # vuelo pre-deploy no tienen este branch.
                    if workflow.patched("capi-outbox-flush-v1"):
                        try:
                            await workflow.execute_activity(
                                flush_capi_outbox_activity,
                                args=[session.session_id],
                                start_to_close_timeout=timedelta(seconds=60),
                                retry_policy=RetryPolicy(maximum_attempts=2),
                            )
                        except Exception as exc:  # noqa: BLE001
                            workflow.logger.warning(
                                "CAPI outbox flush falló (non-blocking): "
                                f"session={session.session_id} err={exc!r}"
                            )

                    # Capas (PR 14), después del envío y del flush. Sombra: la
                    # percepción que corrió en paralelo y la verificación de lo
                    # que salió; solo quedan en la traza (`applied: false`).
                    if shadow_handle is not None:
                        try:
                            shadow_out = await shadow_handle
                        except Exception as exc:  # noqa: BLE001 — fail-open
                            shadow_out = PerceiveOutput(
                                ok=False, profile=self._perception_profile, error=f"activity: {type(exc).__name__}"
                            )
                        trace_steps.append(_perception_step(shadow_out, shadow_started_ms, mode="shadow"))
                        shadow_plan = _plan_of(shadow_out)
                        if shadow_plan.topics:
                            trace_steps.append(
                                {
                                    "kind": "plan",
                                    "at_ms": _now_ms(),
                                    "applied": False,
                                    "checklist": [
                                        {"topic": t.topic, "msg": t.msg, "p": t.p} for t in shadow_plan.topics
                                    ],
                                }
                            )
                            shadow_verified_ms = _now_ms()
                            shadow_verify = await self._verify(
                                VerifyInput(
                                    session_id=session.session_id,
                                    profile=self._perception_profile,
                                    messages=_burst_messages(raw_batch or []),
                                    topics=[{"topic": t.topic, "msg": t.msg, "p": t.p} for t in shadow_plan.topics],
                                    reply_text=_reply_as_sent(trace_sent_texts, None),
                                    components=[
                                        t for t in result.tools_used if t.startswith(("present_", "send_", "request_"))
                                    ],
                                )
                            )
                            trace_steps.append(_verify_step(shadow_verify, shadow_verified_ms, applied=False))
                    if verify_out is not None:
                        if (
                            verify_out.decision == "complement"
                            and verify_out.missing
                            and plan is not None
                            and not self._pending
                            and not self._force_shutdown
                        ):
                            self._pending.append(
                                PendingMessage(
                                    message=complement_message(plan, verify_out.missing),
                                    is_complement_trigger=True,
                                )
                            )
                            for step in reversed(trace_steps):
                                if step.get("kind") == "verify":
                                    step["complement_scheduled"] = True
                                    break
                        self._pending_topics = list(verify_out.missing) if verify_out.decision == "pending" else []
                    elif layers and turn_mode in _ACTING_MODES:
                        self._pending_topics = []

                    # HU-SC-0 — TRAZA POR TURNO para el scorecard por etapa.
                    # El evaluador anterior solo veía texto enviado + nombres
                    # de tools: no veía rechazos de guardas, texto suprimido,
                    # narración descartada ni guardas que dispararon (PR #281,
                    # runs 01a0a0eb / 01a0a0f1: calificó 0.93 un episodio con
                    # formulario sin confirmación). Acá, después del send y
                    # del flush, se persiste lo que solo el workflow sabe; la
                    # activity lo enriquece con la etapa y el estado. Nunca
                    # bloquea al cliente. patched(): histories en vuelo no
                    # tienen la activity (R-DET).
                    if workflow.patched("turn-trace-v1"):
                        if trace_suppressed is None and result.final_content and (
                            result.final_content not in trace_sent_texts
                        ):
                            if abstained:
                                trace_suppressed = "no_message"
                            elif admin_no_send:
                                trace_suppressed = "admin_turn"
                            elif leak_blocked:
                                trace_suppressed = "admin_text_guard"
                            elif suppress_text_for_picker:
                                trace_suppressed = "variant_picker"
                            elif self._force_shutdown:
                                trace_suppressed = "shutdown"
                        is_ghost_turn = any(
                            p.is_ghost_trigger for p in (raw_batch or [msg])
                        )
                        trace_payload = build_turn_payload(
                            trigger=(
                                "ghost"
                                if is_ghost_turn
                                else "handoff"
                                if msg.is_handoff
                                else "complement"
                                if is_complement
                                else "customer"
                            ),
                            inbound_text=msg.message or "",
                            turn_started_ms=turn_started_ms,
                            first_contact=result.first_contact,
                            tool_events=list(result.tool_events),
                            discarded_narration=list(result.discarded_narration),
                            llm_text=result.final_content or "",
                            sent_texts=trace_sent_texts,
                            suppressed_reason=trace_suppressed,
                            guards=trace_guards,
                            steps=trace_steps,
                            turn_key=turn_key,
                            mode=turn_mode,
                            context_notes=context_note_names(msg.plugin_context),
                            inbound=_inbound_trace(list(raw_batch or [msg])),
                        )
                        try:
                            await workflow.execute_activity(
                                persist_turn_trace_activity,
                                args=[
                                    session.session_id,
                                    json.dumps(trace_payload, ensure_ascii=False),
                                ],
                                start_to_close_timeout=timedelta(seconds=15),
                                retry_policy=RetryPolicy(maximum_attempts=2),
                            )
                        except Exception as exc:  # noqa: BLE001
                            workflow.logger.warning(
                                "turn-trace: no se persistió la traza "
                                f"(non-blocking): session={session.session_id} "
                                f"err={exc!r}"
                            )

                    # Escalation a humano: la tool ya escribio metadata
                    # (active_route=humano, tag=HUMANO). Mandado ya el mensaje
                    # de despedida del LLM (en el bloque de send_whatsapp de
                    # arriba), cerramos el workflow. NO programamos remarketing —
                    # escalation y remarketing son mutuamente excluyentes: un
                    # humano va a tomar el caso. Subsecuentes mensajes del
                    # cliente NO arrancan un workflow nuevo porque
                    # `LoadOrStartSalesSession` chequea `active_route==humano`
                    # y omite el dispatch.
                    #
                    # workflow.patched(): histories pre-deploy no tienen este
                    # branch en su shape; el gate evita NondeterminismError al
                    # replay-arlos. Tras el primer drain (ver
                    # `docs/refactor/PHASE6.md::Drain operativo`), eliminar el
                    # if + `workflow.deprecate_patch("sales-escalation-v1")`.
                    if (
                        result.escalation_decision is not None
                        and workflow.patched("sales-escalation-v1")
                    ):
                        # stdlib logging usa `%s`, NO `{}` — el msg pasa por
                        # `record.getMessage()` que hace `msg % args`. F-string
                        # formatea antes y evita el conflicto (mismo patron que
                        # las otras lineas de este file en lineas 84 y 174).
                        workflow.logger.info(
                            f"Sesion {session.session_id} escalada a humano "
                            f"(reason={result.escalation_decision.reason_category}). "
                            "Cerrando workflow."
                        )
                        self._force_shutdown = True

                    # Aplicación del shutdown DIFERIDO de las redes de
                    # seguridad (C3): la despedida/persist/flush de arriba ya
                    # corrieron; recién ahora se apaga el workflow. Solo puede
                    # ser True bajo `safety-net-shutdown-after-send-v1`.
                    if shutdown_after_send:
                        self._force_shutdown = True

                    if self._force_shutdown:
                        # Cancel-shutdown si llegaron mensajes nuevos durante
                        # el procesamiento (Fix 3 / H3, gated). Antes se
                        # retornaba directo y signals entre `_force_shutdown=True`
                        # y `return` se perdian.
                        #
                        # IMPORTANTE (post-mortem run bc54cb93, 2026-05-25):
                        # NO cancelar shutdown si el motivo es escalation a
                        # humano. La escalation es definitiva — el cliente
                        # quedó en cola humana y `active_route=humano` ya
                        # bloquea el dispatch para mensajes futuros. Si
                        # llegan mensajes durante el turn-de-escalation,
                        # el LLM puede emitir respuestas "del pensamiento"
                        # (vio mensaje, pero no sabe que ya escaló), y eso
                        # ROMPE LA EXPERIENCIA: el cliente recibe texto
                        # extra post "te transfiero al humano".
                        #
                        # Cancel-shutdown sigue habilitado para el caso
                        # ghosting (cliente volvió a tiempo) — ahí SÍ
                        # queremos continuar la conversación.
                        #
                        # workflow.patched(): workflows en vuelo pre-fix
                        # podrían haber tomado la rama legacy (cancel-shutdown
                        # también para escalation). En replay caen acá con
                        # `is_escalation = False` para preservar su history.
                        # Workflows nuevos ven `True` y respetan escalation.
                        if workflow.patched("escalation-blocks-cancel-shutdown-v1"):
                            # La escalación de la RED DE SEGURIDAD orden↔tag es
                            # tan definitiva como la del LLM: el cliente quedó
                            # en cola humana para verificación de pago. NO
                            # cancelar el shutdown aunque lleguen mensajes.
                            is_escalation = (
                                result.escalation_decision is not None
                                or safety_net_escalated
                            )
                        else:
                            is_escalation = False  # legacy replay path
                        if (
                            self._pending
                            and not is_escalation
                            and workflow.patched("cancel-shutdown-on-new-pending-v1")
                        ):
                            workflow.logger.info(
                                f"Cancel-shutdown: llegaron {len(self._pending)} "
                                f"mensaje(s) nuevos durante el turno. Continuando."
                            )
                            self._force_shutdown = False
                        else:
                            if is_escalation and self._pending:
                                # Logueamos explícitamente para que quede
                                # rastro: mensajes que llegaron post-escalation
                                # NO se procesan (el humano los verá en el
                                # dashboard cuando tome la sesión).
                                workflow.logger.info(
                                    f"Escalation activa: descartando "
                                    f"{len(self._pending)} mensaje(s) que "
                                    "llegaron durante el turno de escalation. "
                                    "El humano los retoma desde el dashboard."
                                )
                            workflow.logger.info(f"Auto-diagnóstico concluido. Apagando sesión {session.session_id} por abandono de usuario o transferencia.")
                            return

                finally:
                    self._processing = False

            # continue_as_new to keep history bounded
            if turn_count >= _CONTINUE_AS_NEW_AFTER_TURNS and not self._pending:
                # F-string: stdlib logging usa `%s`, no `{}`; el formato previo
                # `"Reached {} turns", N` lanzaba TypeError al disparar (bug
                # latente que solo se activaba al pasar 50 turnos).
                workflow.logger.info(
                    f"Reached {_CONTINUE_AS_NEW_AFTER_TURNS} turns, continuing as new"
                )
                workflow.continue_as_new(
                    SalesSessionInput(
                        session_id=session.session_id,
                        turn_count=turn_count,
                    )
                )
