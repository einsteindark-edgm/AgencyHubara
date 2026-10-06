"""Activity: renderiza los `pending_ui_intents` encolados por las decision tools.

Patrón (HU-002): las decision tools (`ui_intents.py`) escriben intents a
`metadata.json[pending_ui_intents]`. Esta activity los consume DESPUÉS de
que el LLM emite su texto (`send_whatsapp_message_activity`), mapea cada
intent al `send_*` correspondiente del `platform/whatsapp/client.py`,
ejecuta el envío, emite analytics, y limpia el array.

DEHA:
  * R-STATELESS: sin module-level cache, todo desde args.
  * R-JSON: in/out JSON-safe (session_id: str, returns: int).
  * R-HEARTBEAT: heartbeat cada 5s — un batch con N intents pueden tardar
    si el cliente WA tira lento. 5s margin agresivo para que el timeout
    activity (90s) no nos pille.
  * R-DIP: no importa workflow/client de Temporal — solo `@activity.defn`.

Una tarjeta entregada no vuelve a salir (incidente 2026-10-06): el turno 4
mandó la foto de un producto y el turno 5 la volvió a mandar sin que el bot la
pidiera — una escritura vieja de `metadata.json` la devolvió a la cola. Desde
entonces:

  * cada intent tiene identidad (`ui_intent_id`: el `id` que pone quien lo
    encola, o uno estable derivado de qué es y cuándo se encoló en los
    encolados antes de que hubiera id);
  * cada intent despachado queda anotado en un registro de solo-agregar FUERA
    de `metadata.json` (`ui_intents_delivered.jsonl` de la sesión), y antes de
    mandar se descarta sin enviar todo intent ya entregado con ese id (y el
    mismo intent fallido; ver `_DELIVERED_LOG`);
  * sacar el intent de la cola y anotar sus fotos en `outbound_media_index` es
    un `update()` sobre la lectura fresca, por id (con el candado del store):
    lo que otro escritor puso mientras se enviaba no se pisa.

Si Temporal reintenta tras un fallo parcial, los intents ya despachados están
en el registro y fuera de la cola: no se repiten. Un intent ya ENTREGADO no
sale otra vez aunque una escritura vieja lo devuelva a la cola.

La garantía es «al menos una vez», no «exactamente una vez»: un intent puede
repetirse si el worker cae ENTRE el envío a Meta y la anotación en el registro
(milisegundos; el reintento lo manda de nuevo), o si dos flushes de la MISMA
sesión corren a la vez (el del turno y el del endpoint `/order`): los dos lo
leen pendiente antes de que cualquiera lo anote. Se revisa el registro justo
antes de cada envío para achicar esa ventana, no para cerrarla.

Resilencia: cada intent va dentro de try/except aislado. Un intent con
payload corrupto NO bloquea a los siguientes. Errores se loguean + se
emite analytics `error.flush_intent`.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from temporalio import activity

from src.platform.constants import WHATSAPP_SESSION_PREFIX
from src.platform.temporal.heartbeat import with_heartbeat
from src.plugins.chats.agent.sales.config.payments import (
    PAYMENT_LINK_SURCHARGE_NEQUI_BANCOLOMBIA,
    PAYMENT_LINK_SURCHARGE_OTHER_BANKS,
    get_nequi_number,
)
from src.plugins.chats.agent.sales.config.shipping import (
    ORDER_SUMMARY_SHIPPING_LINE,
    ORDER_SUMMARY_SHIPPING_NOTE,
    SHIPPING_RATES_MESSAGE,
    cash_on_delivery_available,
)

# Delay entre fotos del gallery — sin pausa Meta los entrega como burst, lo
# que se ve robótico (3 thumbnails todos al mismo segundo). Una pausa
# pequeña (~600ms) simula "te estoy mandando otra…" de una persona real.
_GALLERY_INTER_IMAGE_DELAY_S = float(
    os.getenv("WHATSAPP_GALLERY_INTER_IMAGE_DELAY_S", "0.6")
)
# Cap defensivo: no mandamos más de 4 imágenes en un solo gallery intent.
_GALLERY_MAX_IMAGES = 4


# Cap del índice wamid→foto en metadata.json — suficiente para resolver
# replies sobre las últimas galerías sin crecer sin límite.
_MEDIA_INDEX_MAX = 50

# TTL de un intent encolado (premortem C4, run 5f43bcd0): un turno suprimido
# (ghosting / _force_shutdown / turno admin) puede dejar intents en
# `pending_ui_intents` sin flushear. Sin TTL, la PRÓXIMA sesión del cliente
# (horas o días después) los flushea en su primer turno → catálogo/picker
# fantasma fuera de contexto. Todo intent más viejo que esto se descarta con
# warning. Los intents SIN `queued_at_ms` también se descartan: todos los
# enqueue paths vivos lo stampean, así que un intent sin stamp solo puede ser
# un remanente pre-deploy — imposible de envejecer, imposible de confiar.
_UI_INTENT_TTL_MS = int(
    float(os.getenv("WHATSAPP_UI_INTENT_TTL_S", "600")) * 1000
)


# Params de texto client-facing que escribe el LLM en las tools de UI intent
# (run 1c9ef231): viajan verbatim al cliente sin pasar por
# `send_whatsapp_message_activity`, así que este es SU choke point. Si el
# texto huele a reporte administrativo se reemplaza por el neutro — el intent
# (menú, botones, picker) sigue saliendo; solo muere el texto envenenado. Un
# falso positivo degrada a un intro genérico, no deja al cliente mudo.
_INTENT_TEXT_NEUTRAL: dict[str, str | None] = {
    "intro_text": "Aquí tienes las opciones:",
    "body": "¿Cuál de estas opciones prefieres?",
    "caption": None,
    "header_text": None,
    "footer": None,
}


async def _sanitize_intent_client_text(
    kind: str, params: dict[str, Any], *, session_id: str | None = None
) -> dict[str, Any]:
    """Limpia los params de texto de un intent y neutraliza olor a admin.

    Import tardío del sanitizer (mismo patrón que el resto del módulo: no
    tocar imports pesados en module load del worker).

    Motor de decisiones (F5): con `session_id`, la muletilla del modelo al
    principio la decide la capacidad `preambulo` y si un texto NO es para el
    cliente, `destinatario`, con el proveedor del bot de la conversación (la
    regla de hoy por defecto: idéntico a antes). Sin `session_id`, la regla
    de hoy.
    """
    from src.sdk.agentkit import looks_like_admin_leak, sanitize_llm_text

    out = dict(params)
    for key, neutral in _INTENT_TEXT_NEUTRAL.items():
        value = out.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        if session_id:
            from src.platform.config import WORKSPACE_VAULT_DIR
            from src.plugins.chats.agent.sales.decisions.guards import clean_llm_text, is_internal_text

            vault = Path(WORKSPACE_VAULT_DIR)
            cleaned = await clean_llm_text(value, session_id=session_id, vault_dir=vault) or value
            internal = await is_internal_text(cleaned, session_id=session_id, vault_dir=vault)
        else:
            cleaned = sanitize_llm_text(value).text or value
            internal = looks_like_admin_leak(cleaned)
        if internal:
            activity.logger.warning(
                "flush_ui_intents.admin_text_neutralized",
                extra={
                    "kind": kind,
                    "param": key,
                    "preview": cleaned[:120],
                },
            )
            out[key] = neutral
        else:
            out[key] = cleaned
    return out


#: Máximo del cuerpo de un mensaje con botones en WhatsApp.
_MAX_ORDER_BODY = 1024


def _format_cop(amount: int, currency: str) -> str:
    return f"${amount:,}".replace(",", ".") + f" {currency}"


def _append_total_and_reference(
    lines: list[str],
    params: dict[str, Any],
    *,
    total_label: str = "Valor",
    breakdown_total_label: str = "Total",
) -> None:
    """Bloque común montos + referencia humana del pedido.

    Montos (requisito 2026-09-07, run 943e6bff — el cliente veía un solo
    "Valor" y no sabía qué estaba pagando): si el intent trae el desglose
    (`subtotal_cop` + `shipping_cop`, validados por SEC-07 en
    `register_order`) se muestran *Productos*, *Envío* y el total con
    `breakdown_total_label`. Sin desglose (intents encolados pre-deploy)
    sale la línea única `total_label` de siempre — nunca se inventa un
    reparto.

    Referencia: "#22 (Plegaria de Luz)" si el intent la trae; fallback al
    order_id crudo para intents encolados pre-deploy o providers sin
    display_id (stub).
    """
    total_cop = params.get("total_cop")
    subtotal_cop = params.get("subtotal_cop")
    shipping_cop = params.get("shipping_cop")
    if isinstance(total_cop, int) and total_cop > 0:
        if lines and lines[-1]:
            lines.append("")
        currency = params.get("currency") or "COP"
        has_breakdown = (
            isinstance(subtotal_cop, int)
            and isinstance(shipping_cop, int)
            and subtotal_cop >= 0
            and shipping_cop >= 0
        )
        if has_breakdown:
            shipping_text = (
                _format_cop(shipping_cop, currency)
                if shipping_cop > 0
                else "sin costo"
            )
            lines.append(f"*Productos*: {_format_cop(subtotal_cop, currency)}")
            discount_cop = params.get("discount_cop")
            if isinstance(discount_cop, int) and discount_cop > 0:
                coupon = params.get("coupon_code")
                label = f"*Descuento{f' ({coupon})' if coupon else ''}*"
                lines.append(f"{label}: −{_format_cop(discount_cop, currency)}")
            lines.extend([
                f"*Envío*: {shipping_text}",
                f"*{breakdown_total_label}*: {_format_cop(total_cop, currency)}",
            ])
        else:
            lines.append(f"*{total_label}*: {_format_cop(total_cop, currency)}")
    reference = params.get("order_reference") or params.get("order_id")
    if reference:
        lines.append(f"Pedido: {reference}")


def _render_payment_link_notice_text(params: dict[str, Any]) -> str:
    """Aviso determinista del link de pago + su recargo (requisito
    2026-08-31). El link real lo genera y envía el humano (escalación
    PAYMENT_VERIFICATION_PENDING) — acá solo fijamos expectativas: recargo
    de 1,5% con Nequi/Bancolombia o 2,69% con otros bancos. Sin datos de
    cuenta (esos no aplican a este método)."""
    lines = [
        "Tu pedido quedó registrado 🤍 Te enviaremos el *link de pago* "
        "por este chat en breve.",
        "",
        "Ten presente: el link tiene un recargo adicional de "
        f"*{PAYMENT_LINK_SURCHARGE_NEQUI_BANCOLOMBIA}* pagando con Nequi o "
        f"Bancolombia, o *{PAYMENT_LINK_SURCHARGE_OTHER_BANKS}* con otros "
        "bancos.",
    ]
    _append_total_and_reference(
        lines,
        params,
        total_label="Valor sin recargo",
        breakdown_total_label="Total sin recargo",
    )
    return "\n".join(lines)


def _render_payment_instructions_text(params: dict[str, Any]) -> str | None:
    """Plantilla FIJA con los datos de pago según el método del pedido.

    * ``method="payment_link"`` → aviso del link + recargo (sin datos de
      cuenta): `_render_payment_link_notice_text`.
    * ``method="transfer"`` (default, incluye intents pre-deploy sin
      `method`) → pago anticipado: llave/Nequi (default de
      `config/payments.py`, overrideable por `PAYMENT_NEQUI_NUMBER`) +
      bloque bancario opcional desde env `PAYMENT_TRANSFER_*`.

    Los datos bancarios salen EXCLUSIVAMENTE de env — jamás del LLM ni de
    los params del intent (caso wa_573125671604: el LLM alucinó número de
    cuenta y NIT). El bloque bancario solo sale COMPLETO (banco + cuenta +
    titular); si además la llave Nequi está desactivada, devuelve None y NO
    se envía nada: el sistema nunca inventa datos de pago, ni parciales.

    Formato WhatsApp: bold con UN asterisco (`*Banco*`), nunca markdown
    doble (`**`).
    """
    if (params.get("method") or "transfer") == "payment_link":
        return _render_payment_link_notice_text(params)

    nequi = get_nequi_number()
    bank = (os.getenv("PAYMENT_TRANSFER_BANK") or "").strip()
    account_number = (
        os.getenv("PAYMENT_TRANSFER_ACCOUNT_NUMBER") or ""
    ).strip()
    holder = (os.getenv("PAYMENT_TRANSFER_HOLDER") or "").strip()
    bank_complete = bool(bank and account_number and holder)
    if not nequi and not bank_complete:
        return None

    lines = ["Aquí tienes los datos para tu pago anticipado 🤍", ""]
    if nequi:
        lines.append(f"*Nequi o llave*: {nequi}")
    if bank_complete:
        account_type = (
            os.getenv("PAYMENT_TRANSFER_ACCOUNT_TYPE") or "Cuenta"
        ).strip()
        holder_id = (os.getenv("PAYMENT_TRANSFER_HOLDER_ID") or "").strip()
        if nequi:
            lines.append("")
            lines.append("Si prefieres transferencia bancaria:")
        lines.extend([
            f"*Banco*: {bank}",
            f"*{account_type}*: {account_number}",
            f"*Titular*: {holder}",
        ])
        if holder_id:
            lines.append(f"*Documento*: {holder_id}")
    _append_total_and_reference(lines, params)
    lines.append("")
    lines.append("Cuando hagas el pago, envíanos el comprobante por este chat.")
    return "\n".join(lines)


def _merge_media_index(
    data: dict[str, Any], media_log: list[dict[str, Any]]
) -> None:
    """Mergea las fotos enviadas al `outbound_media_index` (wamid → foto),
    evictando las entradas más viejas por encima de `_MEDIA_INDEX_MAX`.
    El dict preserva insertion order (JSON round-trip incluido)."""
    index = dict(data.get("outbound_media_index") or {})
    for entry in media_log:
        wamid = entry.get("wa_message_id")
        if not wamid:
            continue
        index[wamid] = {k: v for k, v in entry.items() if k != "wa_message_id"}
    if len(index) > _MEDIA_INDEX_MAX:
        index = dict(list(index.items())[-_MEDIA_INDEX_MAX:])
    data["outbound_media_index"] = index


async def _gallery_inter_delay() -> None:
    """Wrapper aislado para que ruff vea `asyncio` como usado y no lo elimine.

    También centraliza la pausa por si más adelante queremos hacerla jitter
    o configurable por intent.
    """
    await asyncio.sleep(_GALLERY_INTER_IMAGE_DELAY_S)


@activity.defn(name="flush_pending_ui_intents_activity")
@with_heartbeat(every=5)
async def flush_pending_ui_intents_activity(session_id: str) -> list[dict[str, Any]] | int:
    """Activity del workflow: delega en `flush_pending_ui_intents_report` (la
    lógica es una función plana para que también la pueda invocar un handler
    HTTP). Devuelve qué entregó, intent por intent (`[{kind, wamid, ok}]`,
    traza v2). La anotación admite `int` A PROPÓSITO: Temporal decodifica el
    resultado grabado con este tipo, y las histories anteriores a la traza v2
    grabaron la cantidad (replay, L-22)."""
    return await flush_pending_ui_intents_report(session_id)


async def flush_pending_ui_intents(session_id: str) -> int:
    """Cantidad de intents enviados (excluye los que fallaron y los unknown)."""
    return sum(1 for r in await flush_pending_ui_intents_report(session_id) if r["ok"])


async def flush_pending_ui_intents_report(session_id: str) -> list[dict[str, Any]]:
    """Lee `metadata.json[pending_ui_intents]` y dispatch a `send_*`.

    Devuelve un registro por intent intentado, en orden: `{kind, wamid, ok}`
    (`wamid` de Meta si salió; `None` si falló o no se despachó). Los intents
    vencidos o ya entregados que se descartan sin intentar no aparecen.

    Función PLANA (sin `activity.info()` ni heartbeat): la invoca la activity
    de arriba desde el workflow Sales y, desde D1.2b, el endpoint
    `session-actions@v1 /order` de chats tras registrar un pedido que llegó
    por Meta Business Agent (no hay turno del bot que flushee las
    instrucciones de pago). `activity.logger` es seguro fuera de un activity:
    sin contexto solo omite el sufijo con los datos del activity.

    `metadata.json` se lee UNA vez (qué mandar, a qué número) y NUNCA se
    escribe con esa copia: cada cambio (sacar de la cola, el índice de fotos,
    CAPI, los fallos) es un `update()` con SOLO sus llaves sobre la lectura
    fresca (incidente 2026-10-06, ver el docstring del módulo).
    """
    # Imports tardíos para evitar tocar httpx/Temporal-imports en module load
    from src.platform.analytics import (
        get_event_bus,
        make_outbound_sent,
    )
    from src.platform.config import WORKSPACE_VAULT_DIR
    from src.platform.whatsapp import client as wa_client
    from src.platform.whatsapp import dtos as wa_dtos
    from src.sdk.runtime import FilesystemMetadataStore

    session_dir = WORKSPACE_VAULT_DIR / session_id
    store = FilesystemMetadataStore(WORKSPACE_VAULT_DIR)
    # Por el store: un metadata dañado se lee de la última copia buena
    # (decisión del operador, 2026-10-06). Esa copia va una escritura atrás:
    # si trae una tarjeta ya entregada, el registro de entregas la frena.
    data = store.read(session_id)

    intents = [it for it in (data.get("pending_ui_intents") or []) if isinstance(it, dict)]
    if not intents:
        return []

    # Una tarjeta entregada no vuelve a salir (incidente 2026-10-06): un
    # intent que ya figura en el registro de entregas volvió a la cola por una
    # escritura vieja — se descarta sin mandarlo (la foto del turno 4 salió
    # otra vez en el turno 5).
    delivery_log = _delivery_log(session_dir)
    repeated = [it for it in intents if delivery_log.blocks(it)]
    if repeated:
        activity.logger.warning(
            "flush_ui_intents.already_delivered_discarded",
            extra={
                "session_id": session_id,
                "discarded": [
                    {"id": ui_intent_id(it), "kind": it.get("kind")} for it in repeated
                ],
            },
        )
        _settle_intents(store, session_id, {ui_intent_id(it) for it in repeated})
        intents = [it for it in intents if not delivery_log.blocks(it)]
    if not intents:
        return []

    # PREMORTEM C4: descartar intents vencidos ANTES de resolver teléfono o
    # dispatch — un turno suprimido no puede convertirse en un mensaje
    # fantasma en la sesión siguiente. Persistimos el descarte de inmediato
    # para que un retry de Temporal tampoco los resucite.
    now_ms = int(time.time() * 1000)
    fresh_intents: list[dict[str, Any]] = []
    stale_intents: list[dict[str, Any]] = []
    for intent in intents:
        queued_at_ms = intent.get("queued_at_ms")
        if (
            isinstance(queued_at_ms, (int, float))
            and not isinstance(queued_at_ms, bool)
            and now_ms - queued_at_ms <= _UI_INTENT_TTL_MS
        ):
            fresh_intents.append(intent)
        else:
            stale_intents.append(intent)
    if stale_intents:
        activity.logger.warning(
            "flush_ui_intents.stale_intents_discarded",
            extra={
                "session_id": session_id,
                "ttl_ms": _UI_INTENT_TTL_MS,
                "discarded": [
                    {
                        "id": it.get("id"),
                        "kind": it.get("kind"),
                        "queued_at_ms": it.get("queued_at_ms"),
                    }
                    for it in stale_intents
                ],
            },
        )
        _settle_intents(store, session_id, {ui_intent_id(it) for it in stale_intents})
    intents = fresh_intents
    if not intents:
        return []

    # Resolve target phone
    phone_number_id = data.get("phone_number_id") or os.getenv(
        "WHATSAPP_PHONE_NUMBER_ID"
    )
    to_number = session_id.replace(WHATSAPP_SESSION_PREFIX, "")
    if not phone_number_id:
        activity.logger.warning(
            "flush_ui_intents.no_phone_number_id",
            extra={"session_id": session_id, "intents_count": len(intents)},
        )
        # Limpiar igual — no podemos enviar, mejor no acumular forever
        _settle_intents(store, session_id, {ui_intent_id(it) for it in intents})
        return []

    last_inbound_msg_id = data.get("last_inbound_message_id")
    bus = get_event_bus()
    tenant_id = os.getenv("HUBARA_TENANT_ID", "hubara")

    report: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    # PREMORTEM #1 (idempotencia ante el retry de Temporal): cada intent
    # despachado se anota en el registro de entregas y sale de la cola ANTES
    # de cualquier otra operación (analytics es best-effort y NO bloquea la
    # idempotencia). Si crashea en el 3er intent, al retry los 2 enviados ya
    # no están en la cola y figuran en el registro. Salir de la cola es un
    # `update()` por id sobre la lectura fresca: lo que otro escritor puso
    # mientras se enviaba (otro intent, el aviso de entrega) no se pisa.
    for intent in intents:
        intent_id = ui_intent_id(intent)
        kind = intent.get("kind")
        if _delivery_log(session_dir).blocks(intent):
            # Otro flush lo despachó mientras este enviaba los anteriores.
            activity.logger.warning(
                "flush_ui_intents.already_delivered_discarded",
                extra={
                    "session_id": session_id,
                    "discarded": [{"id": intent_id, "kind": kind}],
                },
            )
            _settle_intents(store, session_id, {intent_id})
            continue
        params = intent.get("params") or {}
        analytics_meta = intent.get("analytics") or {}
        # Fotos enviadas en ESTE intent (wamid → producto/diseño). Se
        # persiste en `outbound_media_index` aunque el envelope global
        # falle a medias — cada foto que SÍ llegó es citable.
        media_log: list[dict[str, Any]] = []
        try:
            # Motor de decisiones (`destinatario`, F5): los textos del LLM se
            # deciden UNA vez, acá. Lo que sale y lo que muestra el panel
            # (el marker de abajo) son los mismos params: si el motor cambió
            # un texto por el neutro, el operador ve el neutro que leyó el
            # cliente, no el texto rechazado.
            params = await _sanitize_intent_client_text(kind or "", params, session_id=session_id)
            result = await _dispatch_intent(
                wa_client=wa_client,
                wa_dtos=wa_dtos,
                kind=kind,
                params=params,
                fallback=intent.get("fallback") or {},
                phone_number_id=phone_number_id,
                to_number=to_number,
                last_inbound_message_id=last_inbound_msg_id,
                media_log=media_log,
                session_id=session_id,
                client_text_decided=True,
            )
        except Exception as e:  # noqa: BLE001
            activity.logger.warning(
                "flush_ui_intents.dispatch_failed",
                extra={
                    "session_id": session_id,
                    "kind": kind,
                    "error": str(e),
                },
            )
            failed.append({"kind": kind, "error": str(e)})
            report.append({"kind": kind, "wamid": None, "ok": False})
            # Fuera de la cola (NO retry automático — el LLM puede decidir
            # reemitir en próxima iteración).
            _record_delivery(session_dir, intent, ok=False, wamid=None)
            _settle_intents(store, session_id, {intent_id})
            continue

        if result is None:
            # kind desconocido o intent inválido (sin imagen, etc)
            failed.append({"kind": kind, "error": "no_dispatch"})
            report.append({"kind": kind, "wamid": None, "ok": False})
            _record_delivery(session_dir, intent, ok=False, wamid=None)
            _settle_intents(store, session_id, {intent_id})
            continue

        if not result.ok:
            activity.logger.warning(
                "flush_ui_intents.send_failed",
                extra={
                    "session_id": session_id,
                    "kind": kind,
                    "error": result.error,
                },
            )
            failed.append({"kind": kind, "error": result.error})
            report.append({"kind": kind, "wamid": None, "ok": False})
            _record_delivery(session_dir, intent, ok=False, wamid=None)
            _settle_intents(store, session_id, {intent_id})
            continue

        # ENVÍO EXITOSO — anotar la entrega y sacarlo de la cola (con sus
        # fotos en el índice) antes de cualquier otra operación.
        _record_delivery(session_dir, intent, ok=True, wamid=result.wa_message_id)
        _settle_intents(store, session_id, {intent_id}, media_log=media_log)
        report.append({"kind": kind, "wamid": result.wa_message_id, "ok": True})

        # Auditoría CAPI 2026-09-08: lo que el cliente acaba de VER es la
        # señal de embudo para Meta (ViewContent / AddToCart /
        # InitiateCheckout). Se encola acá y lo manda el flusher del turno.
        _enqueue_capi_after_send(store, session_id, kind=kind, params=params, now_ms=now_ms)

        # Marker al histórico del dashboard (post-envío — best-effort: si
        # crashea, el intent NO se reenvía y el flush sigue).
        try:
            history_event = _build_history_event(kind, params)
            if history_event is not None:
                # `wamid`: destino de las citas del cliente ("este" citando
                # los botones / el catálogo / la foto). Sin él el dashboard
                # muestra "Mensaje no disponible" (caso 2026-09-17).
                if result.wa_message_id:
                    history_event["wamid"] = result.wa_message_id
                _append_history_event(session_id, history_event)
        except Exception:  # noqa: BLE001 - observability nunca bloquea
            pass

        # Analytics outbound (post-envío — si esto crashea, el intent NO se
        # reenvía).
        try:
            ev = make_outbound_sent(
                session_id=session_id,
                tenant_id=tenant_id,
                component_kind=analytics_meta.get("component_kind", kind),
                wa_message_id=result.wa_message_id,
                component_id=analytics_meta.get("component_id"),
                payload_extra={
                    "intent_kind": kind,
                    **{k: v for k, v in analytics_meta.items()
                       if k not in {"component_kind", "component_id"}},
                },
            )
            await bus.record(ev)
        except Exception:  # noqa: BLE001 - analytics never bloquea
            pass

    # Persistir failures finales (histórico, último N).
    if failed:
        _append_failures(store, session_id, failed)

    return report


def ui_intent_id(intent: dict[str, Any]) -> str:
    """La identidad de un intent encolado.

    El `id` que le puso quien lo encoló (`_append_intent`, `register_order`).
    Los encolados antes de que hubiera id (o por un camino que no lo pone)
    reciben uno estable derivado de qué son y cuándo se encolaron: el mismo en
    cada lectura, así el flush los reconoce aunque una escritura vieja los
    devuelva a la cola.
    """
    raw = intent.get("id")
    if isinstance(raw, str) and raw:
        return raw
    if isinstance(raw, int) and not isinstance(raw, bool):
        return str(raw)
    material = json.dumps(
        {
            "kind": intent.get("kind"),
            "params": intent.get("params"),
            "queued_at_ms": intent.get("queued_at_ms"),
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return "sin-id-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


#: Registro de entregas de la sesión (solo-agregar, FUERA de `metadata.json`):
#: una línea JSON por intent despachado — `{id, kind, ok, wamid, queued_at_ms,
#: at_ms}` —, también los fallidos. Antes de mandar, el flush descarta todo
#: intent ENTREGADO (`ok`) con el mismo id, y el MISMO intent fallido (mismo id
#: y mismo `queued_at_ms`: un fallido no se reintenta solo, el LLM decide
#: reemitir). Un reencolado legítimo con el id de un fallido sí sale: las
#: instrucciones de pago llevan un id fijo por pedido (`payinstr-<order_id>`).
_DELIVERED_LOG = "ui_intents_delivered.jsonl"

#: Cuánto del final del registro se lee: un intent vence a los
#: `_UI_INTENT_TTL_MS` (10 min), así que solo importan las entregas recientes;
#: 64 KiB son cientos de líneas.
_DELIVERED_TAIL_BYTES = 64 * 1024


@dataclass(frozen=True)
class _DeliveryLog:
    """Lo que dice el final del registro de entregas."""

    #: ids entregados: bloquean cualquier intent con ese id.
    delivered: frozenset[str] = frozenset()
    #: `(id, queued_at_ms)` fallidos: bloquean solo ESE intent.
    failed: frozenset[tuple[str, str]] = frozenset()

    def blocks(self, intent: dict[str, Any]) -> bool:
        intent_id = ui_intent_id(intent)
        return intent_id in self.delivered or (intent_id, _queued_key(intent)) in self.failed


def _queued_key(intent: dict[str, Any]) -> str:
    return json.dumps(intent.get("queued_at_ms"))


def _delivery_log(session_dir: Path) -> _DeliveryLog:
    """El final del registro de entregas (vacío si no hay registro)."""
    path = session_dir / _DELIVERED_LOG
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            start = max(0, size - _DELIVERED_TAIL_BYTES)
            fh.seek(start)
            chunk = fh.read()
    except FileNotFoundError:
        return _DeliveryLog()
    except OSError:
        activity.logger.warning(
            "flush_ui_intents.delivered_log_unreadable", extra={"path": str(path)}
        )
        return _DeliveryLog()
    lines = chunk.splitlines()
    if start > 0 and lines:
        lines = lines[1:]  # la primera puede venir cortada
    delivered: set[str] = set()
    failed: set[tuple[str, str]] = set()
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            continue
        if row.get("ok") is True:
            delivered.add(row["id"])
        else:
            failed.add((row["id"], _queued_key(row)))
    return _DeliveryLog(frozenset(delivered), frozenset(failed))


def _record_delivery(
    session_dir: Path,
    intent: dict[str, Any],
    *,
    ok: bool,
    wamid: str | None,
) -> None:
    """Agrega el intent despachado al registro de entregas. Best-effort: si
    no se puede escribir, el intent igual sale de la cola."""
    intent_id = ui_intent_id(intent)
    row = {
        "id": intent_id,
        "kind": intent.get("kind"),
        "ok": ok,
        "wamid": wamid,
        "queued_at_ms": intent.get("queued_at_ms"),
        "at_ms": int(time.time() * 1000),
    }
    try:
        session_dir.mkdir(parents=True, exist_ok=True)
        with (session_dir / _DELIVERED_LOG).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        activity.logger.warning(
            "flush_ui_intents.delivered_log_write_failed",
            extra={"intent_id": intent_id},
        )


def _settle_intents(
    store: Any,
    session_id: str,
    intent_ids: set[str],
    *,
    media_log: list[dict[str, Any]] | None = None,
) -> None:
    """Saca de la cola, por id y sobre la lectura FRESCA, los intents ya
    resueltos (enviados, fallidos, vencidos o repetidos) y anota en
    `outbound_media_index` las fotos que salieron. Lo demás de la cola (lo que
    una tool encoló mientras tanto) queda como está.

    Caso borde conocido (segunda revisión del PR #393, M1): saca por id, no
    por `(id, queued_at_ms)`. Si mientras se enviaba se reencolara un intent
    con el MISMO id —un reencolado legítimo de las instrucciones de pago,
    `payinstr-<order_id>`—, también saldría de la cola sin enviarse. Hoy no se
    alcanza: ese id solo lo encola `register_order` al crear el pedido, en el
    turno y antes del flush, y `/order` no reencola un pedido ya registrado.
    Si algún día se reencola en caliente, sacar por `(id, queued_at_ms)` como
    hace el registro de fallidos (`_DeliveryLog.blocks`)."""

    def _settle(fresh: dict[str, Any]) -> dict[str, Any] | None:
        pending = fresh.get("pending_ui_intents")
        queue = pending if isinstance(pending, list) else []
        kept = [
            it
            for it in queue
            if not (isinstance(it, dict) and ui_intent_id(it) in intent_ids)
        ]
        if len(kept) == len(queue) and not media_log:
            return None  # ya no estaban: nada que escribir
        fresh["pending_ui_intents"] = kept
        if media_log:
            _merge_media_index(fresh, media_log)
        return fresh

    _safe_update(store, session_id, _settle)


def _enqueue_capi_after_send(
    store: Any,
    session_id: str,
    *,
    kind: str | None,
    params: dict[str, Any],
    now_ms: int,
) -> None:
    """Encola (sobre la lectura fresca) el evento CAPI del intent recién
    enviado. Best-effort: la atribución nunca bloquea el envío."""

    def _enqueue(fresh: dict[str, Any]) -> dict[str, Any] | None:
        enqueued = _enqueue_capi_for_sent_intent(
            fresh, session_id=session_id, kind=kind, params=params, now_ms=now_ms
        )
        return fresh if enqueued else None

    try:
        store.update(session_id, _enqueue)
    except Exception:  # noqa: BLE001 - atribución nunca bloquea el envío
        pass


def _append_failures(store: Any, session_id: str, failed: list[dict[str, Any]]) -> None:
    """Agrega los fallos del flush al histórico (`ui_intents_failures`, últimos 50)."""

    def _append(fresh: dict[str, Any]) -> dict[str, Any]:
        history = list(fresh.get("ui_intents_failures") or [])
        history.extend(failed)
        fresh["ui_intents_failures"] = history[-50:]
        return fresh

    _safe_update(store, session_id, _append)


def _safe_update(store: Any, session_id: str, mutator: Any) -> None:
    """`store.update` que no tumba el flush por un error de disco (el envío ya
    ocurrió): se loguea y sigue, como antes."""
    try:
        store.update(session_id, mutator)
    except OSError:
        activity.logger.warning(
            "flush_ui_intents.write_failed", extra={"session_id": session_id}
        )


async def _dispatch_intent(
    *,
    wa_client,
    wa_dtos,
    kind: str | None,
    params: dict[str, Any],
    fallback: dict[str, Any],
    phone_number_id: str,
    to_number: str,
    last_inbound_message_id: str | None,
    media_log: list[dict[str, Any]] | None = None,
    session_id: str | None = None,
    client_text_decided: bool = False,
):
    """Mapea `kind` a la función `send_*` correspondiente.

    `client_text_decided`: el caller ya pasó los textos del LLM por
    `_sanitize_intent_client_text` (el flush lo hace para que el marker del
    panel muestre lo mismo que salió); no se le vuelve a preguntar al motor.

    `fallback`: hints opcionales del tool al dispatcher (ej.
    `prefer_native_product_list: bool` para `products_list`). Vacío si la
    tool no lo encoló.

    `media_log`: lista mutable donde los kinds que mandan FOTOS
    (`product_detail`, `product_gallery`) appendean una entrada por cada
    send exitoso — `{wa_message_id, handle, title, image_url, label}` — para
    que el caller persista `outbound_media_index` (resolver replies del
    cliente que citan una foto; caso wa_573125671604).

    Devuelve `OutboundResult` o None si el kind es desconocido / intent
    invalido sin posibilidad de envío.
    """
    # Choke point de texto LLM en intents (run 1c9ef231): limpiar/neutralizar
    # ANTES de cualquier rama — todos los kinds leen de `params`.
    if not client_text_decided:
        params = await _sanitize_intent_client_text(kind or "", params, session_id=session_id)
    if kind == "product_detail":
        link = params.get("image_url")
        if not link:
            return None
        result = await wa_client.send_image(
            phone_number_id,
            to_number,
            wa_dtos.ImageOutbound(
                link=link,
                caption=params.get("caption"),
            ),
        )
        if media_log is not None and result and result.ok and result.wa_message_id:
            media_log.append({
                "wa_message_id": result.wa_message_id,
                "handle": params.get("handle"),
                "title": params.get("title"),
                "image_url": link,
                "label": params.get("design"),
            })
        return result

    if kind == "payment_instructions":
        # Datos bancarios para transferencia — plantilla fija desde env,
        # encolada por register_order (NO por el LLM). Sin config completa
        # no se manda nada: jamás inventamos datos de pago.
        text = _render_payment_instructions_text(params)
        if not text:
            activity.logger.warning(
                "flush_ui_intents.payment_instructions_unconfigured",
                extra={"params": params},
            )
            return None
        return await wa_client.send_text(phone_number_id, to_number, text)

    if kind == "products_list":
        from src.platform.whatsapp import limits as wa_limits

        sections_payload = params.get("sections") or []
        # `prefer_native_product_list`: flag que encola `present_products` cuando
        # cree que los productos están en Meta Catalog (Parte B). Si está, y
        # tenemos META_CATALOG_ID en env, y todas las rows traen
        # `product_retailer_id`, mandamos `interactive.product_list` (MPM con
        # cards rendereadas por Meta — A.11). Si no, fallback a
        # `interactive.list` (lista custom de texto — A.3).
        prefer_native = bool(fallback.get("prefer_native_product_list"))
        catalog_id = (os.environ.get("META_CATALOG_ID") or "").strip()
        all_have_retailer_id = bool(sections_payload) and all(
            r.get("product_retailer_id")
            for s in sections_payload
            for r in (s.get("rows") or [])
        )

        if prefer_native and catalog_id and all_have_retailer_id:
            # MPM path — cap Meta es 30 items totales en hasta 10 sections.
            mpm_sections_raw = sections_payload[: wa_limits.MAX_PRODUCT_LIST_SECTIONS]
            mpm_total = 0
            product_sections: list[wa_dtos.ProductSection] = []
            for s in mpm_sections_raw:
                rows = s.get("rows") or []
                room = wa_limits.MAX_PRODUCT_LIST_ITEMS_TOTAL - mpm_total
                if room <= 0:
                    break
                items = [
                    wa_dtos.ProductItem(
                        product_retailer_id=r["product_retailer_id"]
                    )
                    for r in rows[:room]
                    if r.get("product_retailer_id")
                ]
                if not items:
                    continue
                product_sections.append(
                    wa_dtos.ProductSection(
                        title=wa_limits.truncate(
                            s.get("title") or "Productos",
                            wa_limits.MAX_LIST_SECTION_TITLE,
                        ),
                        product_items=items,
                    )
                )
                mpm_total += len(items)
            if product_sections:
                try:
                    native_result = await wa_client.send_product_list(
                        phone_number_id,
                        to_number,
                        wa_dtos.InteractiveProductListOutbound(
                            catalog_id=catalog_id,
                            header_text=wa_limits.truncate(
                                params.get("header_text") or "Nuestro catálogo",
                                wa_limits.MAX_PRODUCT_LIST_HEADER,
                            ),
                            body=wa_limits.truncate(
                                _with_card_guide(
                                    params.get("intro_text") or "Toca un producto para ver más:",
                                    first_page=params.get("page") in (None, 1),
                                ),
                                wa_limits.MAX_PRODUCT_LIST_BODY,
                            ),
                            sections=product_sections,
                            footer="Hubara",
                        ),
                    )
                except Exception as e:  # noqa: BLE001 — recuperar a la lista
                    native_result = None
                    activity.logger.warning(
                        "products_list.native_exception_fallback_list",
                        extra={"error": str(e)},
                    )
                if native_result is not None and native_result.ok:
                    return native_result
                # Native rechazado (ej. (#131009) "product not found": items aún
                # no shoppables tras conectar el catálogo al WABA, o gap de
                # catálogo). NO retornamos el fallo — caemos a interactive.list
                # (browse del catálogo LOCAL, sin Meta Catalog). Mismo patrón que
                # shipping_flow. Prod 2026-07-01 run 019f1b52.
                activity.logger.warning(
                    "products_list.native_send_failed_fallback_list",
                    extra={
                        "error": (
                            native_result.error
                            if native_result is not None
                            else "exception"
                        ),
                    },
                )

        # Fallback: interactive.list (sin Meta Catalog — cap 10 total).
        sections_payload = wa_limits.cap_list_rows_total(
            sections_payload, wa_limits.MAX_LIST_ROWS_TOTAL
        )
        sections = [
            wa_dtos.ListSection(
                title=s.get("title", "Opciones"),
                rows=[
                    wa_dtos.ListRow(
                        id=r["id"],
                        title=r["title"],
                        description=r.get("description"),
                    )
                    for r in (s.get("rows") or [])
                ],
            )
            for s in sections_payload
            if s.get("rows")
        ]
        if not sections:
            return None
        return await wa_client.send_interactive_list(
            phone_number_id,
            to_number,
            wa_dtos.InteractiveListOutbound(
                body=params.get("intro_text", "Mira las opciones:"),
                button_label=params.get("button_label", "Ver opciones"),
                sections=sections,
            ),
        )

    if kind == "shipping_flow":
        # Resolver el flow_id en este orden de prioridad:
        #   1. `META_FLOW_ID_SHIPPING` desde env — productivo.
        #   2. `params.flow_id` que viene del tool — actualmente siempre
        #      el placeholder, pero queda como override por sesión.
        #   3. Placeholder → caemos al fallback de texto plano.
        # El env-first hace que cambiar el Flow en Meta (re-publicar →
        # nuevo flow_id) sea un re-deploy del worker SIN tocar código.
        env_flow_id = (os.environ.get("META_FLOW_ID_SHIPPING") or "").strip()
        flow_id = (
            env_flow_id
            if env_flow_id and env_flow_id != "FLOW_ID_SHIPPING_PLACEHOLDER"
            else params.get("flow_id")
        )
        use_native_flow = bool(flow_id and flow_id != "FLOW_ID_SHIPPING_PLACEHOLDER")

        if use_native_flow:
            # Defensa en profundidad (post-mortem run bc54cb93, 2026-05-25):
            # ANTES el send_flow estaba sin try/except. Si Meta rechazaba o
            # `_mark_flow_awaiting_reply` lanzaba NameError, el cliente se
            # quedaba SIN nada y tenía que escalar a humano. AHORA si el path
            # nativo falla, caemos al texto plano — peor que el Flow visual
            # pero infinitamente mejor que cero respuesta.
            try:
                # Marcar en metadata que estamos esperando un `nfm_reply` para
                # que el workflow Sales extienda su ghosting timeout (sesión
                # c4e3416f). El cliente tarda 1-3 min en llenar el form — sin
                # esto, ghosting a los 60s dispara remarketing y el nfm_reply
                # se rutea ahí. El flag se setea ANTES del send_flow para que
                # aunque la API call crashee, el timeout extendido ya esté
                # escrito y proteja el próximo retry. El nfm_reply (cuando
                # llegue) limpia el flag.
                _mark_flow_awaiting_reply(to_number)
                flow_result = await wa_client.send_flow(
                    phone_number_id,
                    to_number,
                    wa_dtos.InteractiveFlowOutbound(
                        flow_id=flow_id,
                        flow_token=params.get("flow_token", ""),
                        flow_cta=params.get("flow_cta", "Completar"),
                        flow_action=params.get("flow_action", "navigate"),
                        flow_action_screen=params.get("flow_action_screen"),
                        flow_action_data=params.get("flow_action_data"),
                        body=params.get("body"),
                        header_text=params.get("header_text"),
                        footer=params.get("footer"),
                        mode=params.get("mode", "published"),
                    ),
                )
                if flow_result and flow_result.ok:
                    return flow_result
                # Meta rechazó (4xx/5xx) — loguear + fallback a texto.
                activity.logger.warning(
                    "shipping_flow.native_send_failed_fallback_text",
                    extra={
                        "flow_id": flow_id,
                        "error": (
                            flow_result.error
                            if flow_result is not None
                            else "no_result"
                        ),
                    },
                )
            except Exception as e:  # noqa: BLE001 — fallback genérico
                # Cualquier excepción del native path (NameError de imports,
                # ValueError en build_flow, transport error, etc.).
                activity.logger.warning(
                    "shipping_flow.native_exception_fallback_text",
                    extra={"flow_id": flow_id, "error": str(e)},
                )

        # Fallback (Flow disabled vía env vacío O native_failed):
        # recolectamos los datos conversacionalmente con un mensaje de texto
        # plano que enumera los campos requeridos. NO usamos botones
        # (anti-patrón sesión adc6400c — la opción "Compartir ubicación" no
        # funciona y el cliente abandona). Las formas de pago se informan
        # con sus condiciones (requisito 2026-08-31).
        order_total_cop = int(params.get("order_total_cop") or 0)
        nequi = get_nequi_number()
        payment_lines = []
        if cash_on_delivery_available(order_total_cop):
            payment_lines.append(
                "  • Contra entrega — el valor se calcula con la "
                "transportadora"
            )
        payment_lines.append(
            f"  • Pago anticipado — Nequi o llave {nequi}"
            if nequi
            else "  • Pago anticipado (Nequi)"
        )
        payment_lines.append(
            "  • Link de pago — recargo adicional de "
            f"{PAYMENT_LINK_SURCHARGE_NEQUI_BANCOLOMBIA} con Nequi o "
            f"Bancolombia, {PAYMENT_LINK_SURCHARGE_OTHER_BANKS} con otros "
            "bancos"
        )
        payment_block = "\n".join(payment_lines)
        text = (
            "Para coordinar el envío necesito estos datos, puedes "
            "enviármelos en un solo mensaje o uno por uno:\n\n"
            "🏙️ *Ciudad*\n"
            "📍 *Barrio*\n"
            "🏠 *Dirección* (calle, número, apartamento)\n"
            "📞 *Teléfono* de contacto\n"
            "🙋 *Nombre de quien recibe* el pedido\n"
            "🪪 *Cédula* de quien recibe (opcional)\n"
            "💳 *Método de pago*, elige entre:\n"
            f"{payment_block}"
        )
        return await wa_client.send_text(
            phone_number_id,
            to_number,
            text,
        )

    if kind == "order_confirmation":
        # Por ahora: fallback transparente a interactive.buttons con resumen
        # textual completo. A.12 nativo (interactive.order_details) requiere
        # Meta Catalog + gateway approved — se activará por feature flag.
        items = params.get("items") or []
        # La variante va cuando el producto se repite en varias líneas
        # («1× Velón Gorrión (Lila · Lavanda)»); si no, la línea de siempre.
        lines = [
            (
                f"• {it.get('quantity', 1)}× {it.get('title') or it.get('handle')}"
                + (f" ({it['variant']})" if it.get("variant") else "")
                + f" — ${it.get('unit_price_cop', 0):,}"
            ).replace(",", ".")
            for it in items
        ]
        items_summary = "\n".join(lines)
        subtotal = int(params.get("subtotal_cop", 0))
        shipping = int(params.get("shipping_cop", 0))
        total = int(params.get("total_cop", 0))
        currency = params.get("currency", "COP")
        payment_method = params.get("payment_method")
        body_lines = [
            "*Resumen de tu pedido*",
            items_summary,
            "",
            f"Subtotal productos: {_format_cop(subtotal, currency)}",
        ]
        discount_cop = params.get("discount_cop")
        if isinstance(discount_cop, int) and discount_cop > 0:
            coupon = params.get("coupon_code")
            body_lines.append(
                f"Descuento{f' ({coupon})' if coupon else ''}: −{_format_cop(discount_cop, currency)}"
            )
        if payment_method == "cash_on_delivery":
            # Regla del operador (2026-09-07, `config/shipping.py`): con
            # contra entrega el envío se paga al recibir y la transportadora
            # lo recalcula antes de despachar → NO se muestra valor de envío
            # ni total (sería subtotal + un envío que no es definitivo); va
            # "Por confirmar" + la nota. `shipping_cop`/`total_cop` siguen en
            # el intent (analytics), no se renderizan.
            body_lines.extend(["", ORDER_SUMMARY_SHIPPING_LINE])
        else:
            # Pago anticipado / link: el cliente paga el envío AHORA con la
            # tarifa mínima (misma que valida SEC-07 en register_order y que
            # muestra payment_instructions, #236) → se aclara que es mínima y
            # se da el total. Envío 0 = "sin costo", nunca se inventa reparto.
            shipping_line = (
                f"Envío (tarifa mínima): {_format_cop(shipping, currency)}"
                if shipping > 0
                else "Envío: sin costo"
            )
            body_lines.extend([shipping_line, f"Total: {_format_cop(total, currency)}"])
        body_lines.extend([
            "",
            f"📍 Dirección: {params.get('shipping_address_summary', '')}",
            "",
            f"💳 Medio de pago: {_humanize_payment(payment_method)}",
        ])
        if payment_method == "cash_on_delivery":
            body_lines.extend(["", ORDER_SUMMARY_SHIPPING_NOTE])
        body = "\n".join(body_lines)
        # Cupo por unidad (premortem B4): qué unidades llevan el descuento, o
        # por qué no — la tarjeta termina el turno del bot, así que el cliente
        # lo lee acá. Va al final y cabe en el cuerpo (máx. de WhatsApp).
        note = params.get("coupon_note")
        if isinstance(note, str) and note.strip():
            room = _MAX_ORDER_BODY - len(body) - len("\n\n🎟️ ")
            if room >= 40:
                text = note.strip()
                body += "\n\n🎟️ " + (text if len(text) <= room else text[: room - 1].rstrip() + "…")
        ref = params.get("reference_id", "HUB")
        return await wa_client.send_interactive_buttons(
            phone_number_id,
            to_number,
            wa_dtos.InteractiveButtonsOutbound(
                body=body,
                buttons=[
                    wa_dtos.ReplyButton(
                        id=f"order.confirm.{ref}", title="✅ Confirmar"
                    ),
                    wa_dtos.ReplyButton(
                        id=f"order.modify.{ref}", title="✏️ Modificar"
                    ),
                    wa_dtos.ReplyButton(
                        id=f"order.cancel.{ref}", title="❌ Cancelar"
                    ),
                ],
            ),
        )

    if kind == "shipping_rates":
        # Mensaje estándar de tarifas de envío (regla del operador
        # 2026-09-07). Texto FIJO desde `config/shipping.py`: los params del
        # intent se ignoran a propósito — el LLM no redacta tarifas.
        return await wa_client.send_text(
            phone_number_id, to_number, SHIPPING_RATES_MESSAGE
        )

    if kind == "reaction":
        if not last_inbound_message_id:
            return None
        return await wa_client.send_reaction(
            phone_number_id,
            to_number,
            wa_dtos.ReactionOutbound(
                message_id=last_inbound_message_id,
                emoji=params.get("emoji", "🤍"),
            ),
        )

    if kind == "contact_card":
        # Resolver desde env vars (agents_admin integration es follow-up).
        # Defaults razonables para que la tool no falle en dev.
        advisor_name = os.getenv(
            "HUBARA_ADVISOR_NAME", "Hubara Atención"
        )
        advisor_phone = os.getenv(
            "HUBARA_ADVISOR_PHONE", ""
        )
        if not advisor_phone:
            return None
        wa_id = advisor_phone.lstrip("+").replace(" ", "")
        contact = wa_dtos.ContactCard(
            name=wa_dtos.ContactName(formatted_name=advisor_name),
            phones=[
                wa_dtos.ContactPhone(
                    phone=advisor_phone,
                    type="CELL",
                    wa_id=wa_id,
                ),
            ],
            org_name="Hubara",
            org_title="Asesor",
        )
        return await wa_client.send_contact(
            phone_number_id, to_number, [contact]
        )

    if kind == "cta_url":
        url = params.get("url")
        button_text = params.get("button_text")
        body_text = params.get("body_text")
        if not url or not button_text or not body_text:
            return None
        return await wa_client.send_cta_url(
            phone_number_id,
            to_number,
            wa_dtos.InteractiveCTAUrlOutbound(
                body=body_text,
                button_text=button_text,
                url=url,
            ),
        )

    if kind == "product_gallery":
        # Múltiples fotos del MISMO producto como secuencia.
        # Estrategia: send_image en loop con pausas pequeñas. Cada imagen
        # lleva su label de diseño como caption (la primera con el título
        # delante) — el cliente ve QUÉ es cada foto y puede nombrarla.
        # Compat: intents encolados pre-deploy traen solo `image_urls`
        # (sin `images` etiquetadas) — se mandan sin label.
        labeled_images = params.get("images")
        if labeled_images:
            gallery = [
                (img.get("url"), img.get("label"))
                for img in labeled_images
                if img.get("url")
            ]
        else:
            gallery = [(url, None) for url in (params.get("image_urls") or [])]
        gallery = gallery[:_GALLERY_MAX_IMAGES]
        if not gallery:
            return None
        lead_caption = params.get("lead_caption")
        last_result = None
        all_ok = True
        for idx, (url, label) in enumerate(gallery):
            if idx > 0:
                # Pausa para que no se vea robótico (burst de imágenes).
                # El @with_heartbeat sigue dando keepalive aunque haya sleep.
                await _gallery_inter_delay()
            if idx == 0:
                caption = (
                    f"{lead_caption} · {label}"
                    if lead_caption and label
                    else (lead_caption or label)
                )
            else:
                caption = label
            result = await wa_client.send_image(
                phone_number_id,
                to_number,
                wa_dtos.ImageOutbound(
                    link=url,
                    caption=caption,
                ),
            )
            if (
                media_log is not None
                and result
                and result.ok
                and result.wa_message_id
            ):
                media_log.append({
                    "wa_message_id": result.wa_message_id,
                    "handle": params.get("handle"),
                    "title": params.get("title"),
                    "image_url": url,
                    "label": label,
                })
            last_result = result
            if not result or not result.ok:
                all_ok = False
                # NO abortamos: seguimos con las restantes — un 4xx en una
                # imagen no debe perder las demás. Pero si ya se rompió
                # la secuencia, no agregamos más delays.
                if idx == 0:
                    # Si la primera ya falló, rara vez tiene sentido seguir.
                    break
        if last_result is None:
            return None
        # Reportamos all_ok como bandera global; el wa_message_id queda
        # como el de la ÚLTIMA enviada con éxito.
        if not all_ok and last_result.ok:
            # Edge: las primeras fallaron pero después funcionó. Mantenemos
            # ok=True (al menos algo llegó al cliente) pero registramos
            # en analytics implícitamente via warn arriba.
            pass
        return last_result

    if kind == "quick_replies":
        # Botones genéricos — saludo, decisiones simples.
        body = params.get("body")
        raw_buttons = params.get("buttons") or []
        buttons = [
            wa_dtos.ReplyButton(id=str(b["id"]), title=str(b["title"]))
            for b in raw_buttons
            if b.get("id") and b.get("title")
        ]
        if not body or not buttons:
            return None
        return await wa_client.send_interactive_buttons(
            phone_number_id,
            to_number,
            wa_dtos.InteractiveButtonsOutbound(
                body=body,
                buttons=buttons,
            ),
        )

    if kind == "variant_picker":
        text = _render_variant_picker_text(params)
        if not text:
            return None
        return await wa_client.send_text(
            phone_number_id,
            to_number,
            text,
        )

    # Unknown kind — sin dispatch
    return None


#: Cómo se usan los dos botones de la ficha de un producto del catálogo de
#: WhatsApp (no se pueden cambiar: son de WhatsApp). «Enviar mensaje a la
#: empresa» manda el producto exacto (`referred_product`, 2026-09-30).
CATALOG_CARD_GUIDE = (
    "Toca una vela para ver sus fotos y el precio. Para pedirla, «Añadir a la solicitud de pedido» "
    "y envía la solicitud; si tienes una duda sobre ella, «Enviar mensaje a la empresa»."
)


def _with_card_guide(intro: str, *, first_page: bool) -> str:
    """El texto del catálogo con la guía de los botones (solo la primera página)."""
    return f"{intro}\n\n{CATALOG_CARD_GUIDE}" if first_page else intro


def render_variant_picker_text(params: dict[str, Any]) -> str | None:
    """El texto que recibe el cliente con un selector de variantes."""
    return _render_variant_picker_text(params)


def _render_variant_picker_text(params: dict[str, Any]) -> str | None:
    """Aromas/colores/tamaños como **texto plano con emojis curados**.

    Antes (sesión adc6400c) usábamos `interactive.list` — visualmente
    se sentía robótico y obligaba al cliente a tap-tap para ver más
    de 10 opciones. Ahora render de texto: el cliente escribe la
    opción ("lavanda") y seguimos la conversación naturalmente.
    El emoji por opción YA viene curado dentro de row.title desde
    `present_variant_picker` (closed-list, no inventado por LLM).

    Compartido entre el dispatch (lo que se envía al cliente) y el marker
    del session_history (lo que ve el operador) — mismo texto por diseño.
    """
    sections_payload = params.get("sections") or []
    if not sections_payload:
        return None

    intro = (params.get("intro_text") or "Estas son las opciones:").strip()
    text_lines: list[str] = [intro, ""]
    # Cupón con cupo en este producto: sus combinaciones (o el aviso de que
    # lo ya elegido va a precio normal) van antes de la lista completa.
    coupon = params.get("coupon")
    if isinstance(coupon, dict):
        notice = str(coupon.get("notice") or "").strip()
        if notice:
            text_lines += [notice, ""]
        coupon_rows = [str(r).strip() for r in coupon.get("rows") or [] if str(r).strip()]
        if coupon_rows:
            text_lines.append(f"*{str(coupon.get('title') or '').strip()}*")
            text_lines += coupon_rows
            text_lines.append("")
            others = str(coupon.get("others") or "").strip()
            if others:
                text_lines.append(others)
    for sec in sections_payload:
        sec_title = (sec.get("title") or "").strip()
        rows = sec.get("rows") or []
        if not rows:
            continue
        if sec_title and sec_title != "Opciones":
            text_lines.append(f"*{sec_title}*")
        for r in rows:
            row_title = (r.get("title") or "").strip()
            if row_title:
                text_lines.append(row_title)
        text_lines.append("")  # separador entre sections

    # Pie pidiendo respuesta libre. Sin botones — esperamos texto. Si el
    # selector lo armó la guarda de enumeración con la lista del bot, cierra
    # con lo que el bot escribió después de la lista (su pregunta).
    variant_type = params.get("variant_type") or ""
    tail = str(params.get("closing_text") or "").strip() or {
        "scent": "Dime cuál te gusta y seguimos 🤍",
        "color": "Cuéntame qué color prefieres y seguimos 🤍",
        "size": "Dime qué tamaño quieres y seguimos 🤍",
    }.get(variant_type, "Dime cuál prefieres y seguimos 🤍")
    text_lines.append(tail)

    text = "\n".join(line for line in text_lines if line is not None).strip()
    return text or None


_NOTE_TEXT_LIMIT = 160


#: Intent enviado → evento CAPI (auditoría 2026-09-08). Un evento por
#: episodio (event_id estable): ver 3 fotos = un ViewContent.
_CAPI_EVENT_BY_INTENT_KIND: dict[str, str] = {
    "product_detail": "ViewContent",
    "products_list": "ViewContent",
    "product_gallery": "ViewContent",
    "variant_picker": "ViewContent",
    "order_confirmation": "AddToCart",
    "shipping_flow": "InitiateCheckout",
}


def _enqueue_capi_for_sent_intent(
    data: dict[str, Any],
    *,
    session_id: str,
    kind: str | None,
    params: dict[str, Any],
    now_ms: int,
) -> bool:
    """Encola el evento CAPI que corresponde al intent recién enviado.
    Devuelve True si escribió algo en ``data`` (el caller persiste)."""
    event_name = _CAPI_EVENT_BY_INTENT_KIND.get(kind or "")
    if event_name is None:
        return False
    from src.plugins.chats.shared.funnel import active_episode
    from src.sdk.connectorkit import enqueue_capi_event

    episode = active_episode(data)
    episode_id = str((episode or {}).get("episode_id") or "")
    if not episode_id:
        return False
    value: int | None = None
    currency: str | None = None
    if event_name == "AddToCart":
        total = params.get("total_cop")
        if isinstance(total, int) and not isinstance(total, bool) and total > 0:
            value = total
            currency = str(params.get("currency") or "COP")
    try:
        event_id = enqueue_capi_event(
            data,
            event_name=event_name,
            session_id=session_id,
            episode_id=episode_id,
            value=value,
            currency=currency,
            contents=_capi_contents_for_intent(kind, params),
            source=f"flush_ui_intents:{kind}",
            now_ms=now_ms,
        )
    except ValueError:
        return False
    return event_id is not None


def _capi_contents_for_intent(kind: str | None, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Identidad de producto (``retailer_id`` = SKU en Meta) del intent que el
    cliente acaba de VER, en la forma ``contents`` de CAPI: le dice a Meta qué
    producto vio. No cuenta para la "coincidencia de catálogo" (solo eventos
    web/app, verificado 2026-09-21).

      * product_detail / product_gallery / variant_picker → el producto
        (``params.retailer_id``, precio unitario si viene).
      * products_list → cada row del MPM (``product_retailer_id``).
      * order_confirmation → los ítems resueltos (retailer_id + qty + precio).
      * shipping_flow → sin identidad (solo total); Meta no la exige ahí.
    Sin ids → lista vacía y el evento sale como antes (nunca bloquea).
    """
    if kind in ("product_detail", "product_gallery", "variant_picker"):
        retailer_id = params.get("retailer_id")
        if not retailer_id:
            return []
        return [{"id": retailer_id, "quantity": 1, "price": params.get("price")}]
    if kind == "products_list":
        rows = [
            r
            for section in (params.get("sections") or [])
            if isinstance(section, dict)
            for r in (section.get("rows") or [])
            if isinstance(r, dict)
        ]
        return [{"product_retailer_id": r.get("product_retailer_id")} for r in rows]
    if kind == "order_confirmation":
        return [it for it in (params.get("items") or []) if isinstance(it, dict)]
    return []


def _trunc(text: str, limit: int = _NOTE_TEXT_LIMIT) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _build_history_event(
    kind: str | None, params: dict[str, Any]
) -> dict[str, Any] | None:
    """Marker human-readable del intent enviado, para el JSONL del dashboard.

    El dashboard del operador lee el JSONL del session_history; sin este
    marker los envíos no-textuales (catálogo, flows, botones, galerías…)
    quedan invisibles y la conversación se vuelve imposible de seguir
    (mismo problema que resolvió `_append_template_to_session_history`
    para los templates — HU-WA24H-001 pre-mortem F2.2).

    Dos shapes:
      * `variant_picker` envía TEXTO real al cliente (y el workflow suprime
        el final_content del LLM) → se persiste el texto renderizado como
        assistant message normal, exactamente lo que vio el cliente.
      * El resto → `{"role": "assistant", "kind": "ui_component",
        "component_kind": <kind>, "content": <nota>}`, que el clasificador
        del dashboard proyecta como `ui_type: ui_component_sent` y el
        frontend pinta como nota de sistema.
    """
    if kind == "variant_picker":
        text = _render_variant_picker_text(params)
        if not text:
            return None
        return {"role": "assistant", "content": text}

    if kind == "payment_instructions":
        # Igual que variant_picker: el cliente recibió TEXTO real — el
        # operador ve exactamente los datos bancarios que se enviaron.
        text = _render_payment_instructions_text(params)
        if not text:
            return None
        return {"role": "assistant", "content": text}

    if kind == "shipping_rates":
        # Texto fijo real que recibió el cliente — el operador lo ve tal cual.
        return {"role": "assistant", "content": SHIPPING_RATES_MESSAGE}

    if kind == "product_detail":
        caption = (params.get("caption") or "").strip()
        content = "📷 El bot envió una foto del producto"
        if caption:
            content += f": «{_trunc(caption)}»"
    elif kind == "products_list":
        total = sum(
            len(s.get("rows") or []) for s in (params.get("sections") or [])
        )
        content = f"🛍️ El bot envió el catálogo con {total} productos"
    elif kind == "shipping_flow":
        content = "📋 El bot pidió los datos de envío (formulario)"
    elif kind == "order_confirmation":
        content = "🧾 El bot envió el resumen del pedido con botones para confirmar"
    elif kind == "reaction":
        content = f"El bot reaccionó con {params.get('emoji', '🤍')} a un mensaje del cliente"
    elif kind == "contact_card":
        content = "👤 El bot envió la tarjeta de contacto del asesor"
    elif kind == "cta_url":
        button = _trunc(str(params.get("button_text") or ""), 60)
        content = f"🔗 El bot envió un botón «{button}» → {params.get('url', '')}"
    elif kind == "product_gallery":
        n = min(len(params.get("image_urls") or []), _GALLERY_MAX_IMAGES)
        lead = (params.get("lead_caption") or "").strip()
        content = f"🖼️ El bot envió {n} fotos del producto"
        if lead:
            content += f" — «{_trunc(lead)}»"
    elif kind == "quick_replies":
        titles = " · ".join(
            str(b.get("title"))
            for b in (params.get("buttons") or [])
            if b.get("title")
        )
        content = f"🔘 El bot envió botones: {titles}"
        body = _trunc(str(params.get("body") or ""))
        if body:
            content += f" — con el mensaje: «{body}»"
    else:
        # Kind futuro sin descripción específica: nota genérica — si se
        # envió con éxito, el operador merece saber que ALGO salió.
        content = f"📤 El bot envió un mensaje interactivo ({kind})"

    return {
        "role": "assistant",
        "kind": "ui_component",
        "component_kind": kind,
        "content": content,
    }


def _append_history_event(session_id: str, event: dict[str, Any]) -> None:
    """Appendea el marker al JSONL que lee el dashboard. Best-effort: el
    envío al cliente YA ocurrió; un fallo de I/O acá se loguea y no
    bloquea el flush (precedente: `_append_template_to_session_history`)."""
    from src.platform.config import WORKSPACE_VAULT_DIR

    history_path = (
        WORKSPACE_VAULT_DIR / session_id / "sessions" / f"{session_id}.jsonl"
    )
    try:
        history_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            **event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        activity.logger.warning(
            "flush_ui_intents.history_append_failed",
            extra={"session_id": session_id},
        )


def _mark_flow_awaiting_reply(to_number: str) -> None:
    """Escribe `shipping_flow_awaiting_reply_since_ms` en metadata.json para
    que el workflow Sales extienda su timeout de ghosting (sesión c4e3416f).

    Best-effort: si falla, loguea pero NO bloquea el envío del Flow.
    El nfm_reply (cuando llegue) limpia el flag desde `ingest_inbound_message`.

    Resolución del session_id desde `to_number`: respetamos el prefijo
    canónico `WHATSAPP_SESSION_PREFIX` (mismo del wrapper arriba).

    Timestamp idempotente entre retries (consult Temporal Python SDK docs,
    `references/python/determinism.md`): preferimos `activity.info().scheduled_time`
    sobre `time.time()` porque el SDK lo deja IDÉNTICO entre re-ejecuciones de la
    misma activity attempt-1 → attempt-N. Si la activity hace retry (Meta tira
    flaky, worker crash mid-execution), el ghosting window NO se renueva por
    cada intento — preserva el momento "real" en que el workflow programó el
    envío del Flow. Fallback a `time.time()` solo defensivo por si la helper se
    llamara desde fuera de un activity context en el futuro (no es el caso hoy
    — todos los call-sites están dentro de `flush_pending_ui_intents_activity`).

    Escribe SOLO el flag, con `update()` sobre la lectura fresca: el flush lo
    respeta al sacar el Flow de la cola (run 01a0a0f1, 2026-09-14: la copia
    vieja del flush lo pisaba y el ghosting cerraba a los 5 min).
    """
    from src.platform.config import WORKSPACE_VAULT_DIR
    from src.sdk.runtime import FilesystemMetadataStore

    session_id = f"{WHATSAPP_SESSION_PREFIX}{to_number}"
    metadata_file = WORKSPACE_VAULT_DIR / session_id / "metadata.json"
    if not metadata_file.exists():
        return
    # Un metadata dañado ya no aborta: `update()` lo recupera con la última
    # copia buena (decisión del operador, 2026-10-06).

    try:
        # SDK-native: estable entre retries de esta activity attempt.
        ts_ms = int(activity.info().scheduled_time.timestamp() * 1000)
    except RuntimeError:
        # Defensive — solo dispararía si la helper fuese invocada fuera de un
        # activity context en el futuro. Wall-clock se acerca lo suficiente.
        ts_ms = int(time.time() * 1000)

    def _mark(fresh: dict[str, Any]) -> dict[str, Any]:
        fresh["shipping_flow_awaiting_reply_since_ms"] = ts_ms
        return fresh

    _safe_update(FilesystemMetadataStore(WORKSPACE_VAULT_DIR), session_id, _mark)


def _humanize_payment(code: str | None) -> str:
    # `card` es legacy (órdenes registradas antes del requisito 2026-08-31);
    # las 3 formas vigentes son transfer / payment_link / cash_on_delivery.
    return {
        "card": "Tarjeta",
        "transfer": "Pago anticipado (Nequi)",
        "payment_link": "Link de pago",
        "cash_on_delivery": "Contra entrega",
    }.get(code or "", code or "Por confirmar")
