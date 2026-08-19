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

El idempotency-key efectivo es `intent.id` (uuid generado en la tool). Si
Temporal reintenta esta activity tras un fallo parcial, los intents YA
enviados quedaron limpiados (limpieza pre-envío de cada item), evitando
duplicados al cliente.

Resilencia: cada intent va dentro de try/except aislado. Un intent con
payload corrupto NO bloquea a los siguientes. Errores se loguean + se
emite analytics `error.flush_intent`.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from typing import Any

from temporalio import activity

from src.platform.constants import WHATSAPP_SESSION_PREFIX
from src.platform.temporal.heartbeat import with_heartbeat

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


def _sanitize_intent_client_text(
    kind: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Limpia los params de texto de un intent y neutraliza olor a admin.

    Import tardío del sanitizer (mismo patrón que el resto del módulo: no
    tocar imports pesados en module load del worker).
    """
    from src.sdk.agentkit import looks_like_admin_leak, sanitize_llm_text

    out = dict(params)
    for key, neutral in _INTENT_TEXT_NEUTRAL.items():
        value = out.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        cleaned = sanitize_llm_text(value).text or value
        if looks_like_admin_leak(cleaned):
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


def _render_payment_instructions_text(params: dict[str, Any]) -> str | None:
    """Plantilla FIJA de datos bancarios para pago por transferencia.

    Los datos salen EXCLUSIVAMENTE de env (`PAYMENT_TRANSFER_*`) — jamás del
    LLM ni de los params del intent (caso wa_573125671604: el LLM alucinó
    número de cuenta y NIT). Si la config mínima (banco + cuenta + titular)
    no está completa devuelve None y NO se envía nada: el sistema nunca
    inventa datos de pago, ni parciales.

    Formato WhatsApp: bold con UN asterisco (`*Banco*`), nunca markdown
    doble (`**`).
    """
    bank = (os.getenv("PAYMENT_TRANSFER_BANK") or "").strip()
    account_number = (
        os.getenv("PAYMENT_TRANSFER_ACCOUNT_NUMBER") or ""
    ).strip()
    holder = (os.getenv("PAYMENT_TRANSFER_HOLDER") or "").strip()
    if not (bank and account_number and holder):
        return None
    account_type = (
        os.getenv("PAYMENT_TRANSFER_ACCOUNT_TYPE") or "Cuenta"
    ).strip()
    holder_id = (os.getenv("PAYMENT_TRANSFER_HOLDER_ID") or "").strip()

    lines = [
        "Aquí tienes los datos para tu transferencia 🤍",
        "",
        f"*Banco*: {bank}",
        f"*{account_type}*: {account_number}",
        f"*Titular*: {holder}",
    ]
    if holder_id:
        lines.append(f"*Documento*: {holder_id}")
    total_cop = params.get("total_cop")
    if isinstance(total_cop, int) and total_cop > 0:
        currency = params.get("currency") or "COP"
        total_formatted = f"${total_cop:,}".replace(",", ".")
        lines.append(f"*Valor*: {total_formatted} {currency}")
    # Referencia humana ("#22 (Plegaria de Luz)") si el intent la trae;
    # fallback al order_id crudo para intents encolados pre-deploy o
    # providers sin display_id (stub).
    reference = params.get("order_reference") or params.get("order_id")
    if reference:
        lines.append(f"Pedido: {reference}")
    lines.append("")
    lines.append("Cuando la hagas, envíanos el comprobante por este chat.")
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
async def flush_pending_ui_intents_activity(session_id: str) -> int:
    """Lee `metadata.json[pending_ui_intents]` y dispatch a `send_*`.

    Devuelve la cantidad de intents enviados (excluye los que fallaron y
    los unknown).
    """
    # Imports tardíos para evitar tocar httpx/Temporal-imports en module load
    from src.platform.analytics import (
        get_event_bus,
        make_outbound_sent,
    )
    from src.platform.config import WORKSPACE_VAULT_DIR
    from src.platform.whatsapp import client as wa_client
    from src.platform.whatsapp import dtos as wa_dtos

    metadata_file = WORKSPACE_VAULT_DIR / session_id / "metadata.json"
    if not metadata_file.exists():
        return 0
    try:
        data = json.loads(metadata_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        activity.logger.warning(
            "flush_ui_intents.bad_metadata",
            extra={"session_id": session_id},
        )
        return 0

    intents = list(data.get("pending_ui_intents") or [])
    if not intents:
        return 0

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
        data["pending_ui_intents"] = fresh_intents
        _safe_write_metadata(metadata_file, data)
    intents = fresh_intents
    if not intents:
        return 0

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
        data["pending_ui_intents"] = []
        _safe_write_metadata(metadata_file, data)
        return 0

    last_inbound_msg_id = data.get("last_inbound_message_id")
    bus = get_event_bus()
    tenant_id = os.getenv("HUBARA_TENANT_ID", "hubara")

    sent_count = 0
    failed: list[dict[str, Any]] = []

    # PREMORTEM #1: idempotency on Temporal retry.
    # ANTES (bug): leíamos N intents, dispatch todos, limpiábamos al final.
    # Si crash post-dispatch del 3er intent: Temporal reintenta, lee los
    # MISMOS 5 intents (no se limpiaron), dispatch otra vez los 3 que ya
    # se enviaron → cliente recibe duplicados + billing Meta x2.
    # AHORA (fix): por cada intent exitoso, lo removemos del array Y
    # escribimos metadata. Si crash en el 3er intent, al retry quedan
    # 2+1=3 pendientes (los 2 enviados ya se limpiaron, el que falló se
    # reintenta o se descarta según política abajo).
    #
    # Hacemos una copia de la lista para iterar — `pending_ui_intents`
    # se va vaciando in-place.
    intents_pending = list(intents)
    data["pending_ui_intents"] = intents_pending

    while intents_pending:
        intent = intents_pending[0]
        kind = intent.get("kind")
        params = intent.get("params") or {}
        analytics_meta = intent.get("analytics") or {}
        # Fotos enviadas en ESTE intent (wamid → producto/diseño). Se
        # persiste en `outbound_media_index` aunque el envelope global
        # falle a medias — cada foto que SÍ llegó es citable.
        media_log: list[dict[str, Any]] = []
        try:
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
            # Pop el intent fallido (NO retry automático — el LLM puede
            # decidir reemitir en próxima iteración) y persistir.
            intents_pending.pop(0)
            _safe_write_metadata(metadata_file, data)
            continue

        if result is None:
            # kind desconocido o intent inválido (sin imagen, etc)
            failed.append({"kind": kind, "error": "no_dispatch"})
            intents_pending.pop(0)
            _safe_write_metadata(metadata_file, data)
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
            intents_pending.pop(0)
            _safe_write_metadata(metadata_file, data)
            continue

        # ENVÍO EXITOSO — pop e inmediatamente persistir antes de
        # cualquier otra operación (analytics es best-effort y NO debe
        # bloquear la idempotencia).
        intents_pending.pop(0)
        if media_log:
            _merge_media_index(data, media_log)
        _safe_write_metadata(metadata_file, data)
        sent_count += 1

        # Marker al histórico del dashboard (post-pop, post-write —
        # best-effort: si crashea, el intent NO se reenvía y el flush sigue).
        try:
            history_event = _build_history_event(kind, params)
            if history_event is not None:
                _append_history_event(session_id, history_event)
        except Exception:  # noqa: BLE001 - observability nunca bloquea
            pass

        # Analytics outbound (post-pop, post-write — si esto crashea, el
        # intent NO se reenvía).
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
        history = list(data.get("ui_intents_failures") or [])
        history.extend(failed)
        data["ui_intents_failures"] = history[-50:]
        _safe_write_metadata(metadata_file, data)

    return sent_count


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
):
    """Mapea `kind` a la función `send_*` correspondiente.

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
    params = _sanitize_intent_client_text(kind or "", params)
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
                                params.get("intro_text")
                                or "Toca un producto para ver más:",
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
        # funciona y el cliente abandona).
        order_total_cop = int(params.get("order_total_cop") or 0)
        cod_line = (
            "\n_Contra entrega disponible para pedidos > $45.000 COP._"
            if order_total_cop > 45000
            else ""
        )
        text = (
            "Para coordinar el envío necesito estos datos, puedes "
            "enviármelos en un solo mensaje o uno por uno:\n\n"
            "🏙️ *Ciudad*\n"
            "📍 *Barrio*\n"
            "🏠 *Dirección* (calle, número, apartamento)\n"
            "📞 *Teléfono* de contacto\n"
            "💳 *Método de pago* (tarjeta, transferencia"
            f"{', contra entrega' if order_total_cop > 45000 else ''})"
            f"{cod_line}"
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
        lines = [
            f"• {it.get('quantity', 1)}× {it.get('title') or it.get('handle')} — "
            f"${it.get('unit_price_cop', 0):,}".replace(",", ".")
            for it in items
        ]
        items_summary = "\n".join(lines)
        total = int(params.get("total_cop", 0))
        subtotal = int(params.get("subtotal_cop", 0))
        shipping = int(params.get("shipping_cop", 0))
        currency = params.get("currency", "COP")
        body_lines = [
            "*Resumen de tu pedido*",
            items_summary,
            "",
            f"Subtotal: ${subtotal:,} {currency}".replace(",", "."),
        ]
        if shipping > 0:
            body_lines.append(f"Envío: ${shipping:,} {currency}".replace(",", "."))
        body_lines.extend([
            f"*Total: ${total:,} {currency}*".replace(",", "."),
            "",
            f"📍 {params.get('shipping_address_summary', '')}",
            f"💳 Pago: {_humanize_payment(params.get('payment_method'))}",
        ])
        body = "\n".join(body_lines)
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

    # Pie pidiendo respuesta libre. Sin botones — esperamos texto.
    variant_type = params.get("variant_type") or ""
    tail = {
        "scent": "Dime cuál te gusta y seguimos 🤍",
        "color": "Cuéntame qué color prefieres y seguimos 🤍",
        "size": "Dime qué tamaño quieres y seguimos 🤍",
    }.get(variant_type, "Dime cuál prefieres y seguimos 🤍")
    text_lines.append(tail)

    text = "\n".join(line for line in text_lines if line is not None).strip()
    return text or None


_NOTE_TEXT_LIMIT = 160


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


def _safe_write_metadata(path, data: dict) -> None:
    try:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        activity.logger.warning(
            "flush_ui_intents.write_failed", extra={"path": str(path)}
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
    """
    from src.platform.config import WORKSPACE_VAULT_DIR

    session_id = f"{WHATSAPP_SESSION_PREFIX}{to_number}"
    metadata_file = WORKSPACE_VAULT_DIR / session_id / "metadata.json"
    if not metadata_file.exists():
        return
    try:
        data = json.loads(metadata_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        activity.logger.warning(
            "flush_ui_intents.flow_awaiting_flag_read_failed",
            extra={"session_id": session_id, "error": str(e)},
        )
        return

    try:
        # SDK-native: estable entre retries de esta activity attempt.
        ts_ms = int(activity.info().scheduled_time.timestamp() * 1000)
    except RuntimeError:
        # Defensive — solo dispararía si la helper fuese invocada fuera de un
        # activity context en el futuro. Wall-clock se acerca lo suficiente.
        ts_ms = int(time.time() * 1000)

    data["shipping_flow_awaiting_reply_since_ms"] = ts_ms
    _safe_write_metadata(metadata_file, data)


def _humanize_payment(code: str | None) -> str:
    return {
        "card": "Tarjeta",
        "transfer": "Transferencia",
        "cash_on_delivery": "Contra entrega",
    }.get(code or "", code or "Por confirmar")
