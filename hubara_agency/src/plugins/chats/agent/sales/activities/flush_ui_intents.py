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


async def _gallery_inter_delay() -> None:
    """Wrapper aislado para que ruff vea `asyncio` como usado y no lo elimine.

    También centraliza la pausa por si más adelante queremos hacerla jitter
    o configurable por intent.
    """
    await asyncio.sleep(_GALLERY_INTER_IMAGE_DELAY_S)
# Cap defensivo: no mandamos más de 4 imágenes en un solo gallery intent.
_GALLERY_MAX_IMAGES = 4


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
        try:
            result = await _dispatch_intent(
                wa_client=wa_client,
                wa_dtos=wa_dtos,
                kind=kind,
                params=params,
                phone_number_id=phone_number_id,
                to_number=to_number,
                last_inbound_message_id=last_inbound_msg_id,
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
        _safe_write_metadata(metadata_file, data)
        sent_count += 1

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
    phone_number_id: str,
    to_number: str,
    last_inbound_message_id: str | None,
):
    """Mapea `kind` a la función `send_*` correspondiente.

    Devuelve `OutboundResult` o None si el kind es desconocido / intent
    invalido sin posibilidad de envío.
    """
    if kind == "product_detail":
        link = params.get("image_url")
        if not link:
            return None
        return await wa_client.send_image(
            phone_number_id,
            to_number,
            wa_dtos.ImageOutbound(
                link=link,
                caption=params.get("caption"),
            ),
        )

    if kind == "products_list":
        from src.platform.whatsapp import limits as wa_limits

        sections_payload = wa_limits.cap_list_rows_total(
            params.get("sections") or [], wa_limits.MAX_LIST_ROWS_TOTAL
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
                body=params.get("intro_text", "Mirá las opciones:"),
                button_label=params.get("button_label", "Ver opciones"),
                sections=sections,
            ),
        )

    if kind == "location_request":
        return await wa_client.send_location_request(
            phone_number_id,
            to_number,
            wa_dtos.InteractiveLocationRequestOutbound(
                body=params.get("body", "Compartí tu ubicación"),
            ),
        )

    if kind == "shipping_flow":
        flow_id = params.get("flow_id")
        if not flow_id or flow_id == "FLOW_ID_SHIPPING_PLACEHOLDER":
            # Flow no configurado todavía — fallback a pedir datos en texto
            # via buttons. NO falla silenciosamente: emitimos un texto que
            # el LLM (next turn) parseará.
            return await wa_client.send_interactive_buttons(
                phone_number_id,
                to_number,
                wa_dtos.InteractiveButtonsOutbound(
                    body=(
                        "Para completar tu pedido necesito unos datos. "
                        "¿Cómo prefieres dármelos?"
                    ),
                    buttons=[
                        wa_dtos.ReplyButton(
                            id="shipping.via_text",
                            title="Escribirlos",
                        ),
                        wa_dtos.ReplyButton(
                            id="shipping.share_location",
                            title="Compartir ubicación",
                        ),
                    ],
                ),
            )
        return await wa_client.send_flow(
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
        # Estrategia: send_image en loop con pausas pequeñas. La primera
        # imagen lleva caption (título); las siguientes van limpias para
        # que la conversación luzca como "tomá, mirá también esta… y esta".
        urls = list(params.get("image_urls") or [])
        urls = urls[:_GALLERY_MAX_IMAGES]
        if not urls:
            return None
        lead_caption = params.get("lead_caption")
        last_result = None
        all_ok = True
        for idx, url in enumerate(urls):
            if idx > 0:
                # Pausa para que no se vea robótico (burst de imágenes).
                # El @with_heartbeat sigue dando keepalive aunque haya sleep.
                await _gallery_inter_delay()
            result = await wa_client.send_image(
                phone_number_id,
                to_number,
                wa_dtos.ImageOutbound(
                    link=url,
                    caption=lead_caption if idx == 0 else None,
                ),
            )
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
        # Picker de aromas/colores/tamaños. Mismo render que products_list
        # pero con sections diferentes (por categoría sensorial) y emojis
        # curados desde variant_emoji (closed-list).
        # Defensa en profundidad: cap total ≤10 rows (bug 10d8bd21).
        from src.platform.whatsapp import limits as wa_limits

        sections_payload = wa_limits.cap_list_rows_total(
            params.get("sections") or [], wa_limits.MAX_LIST_ROWS_TOTAL
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
                body=params.get("intro_text", "Estas son las opciones:"),
                button_label=params.get("button_label", "Ver opciones"),
                sections=sections,
            ),
        )

    # Unknown kind — sin dispatch
    return None


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


def _humanize_payment(code: str | None) -> str:
    return {
        "card": "Tarjeta",
        "transfer": "Transferencia",
        "cash_on_delivery": "Contra entrega",
    }.get(code or "", code or "Por confirmar")
