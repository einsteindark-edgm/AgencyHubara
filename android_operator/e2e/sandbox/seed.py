#!/usr/bin/env python3
"""Build a fresh, 100 % synthetic sandbox data dir (stdlib only).

    python3 seed.py                 # (re)create ./data
    python3 seed.py --image-base http://10.0.2.2:8010/__sandbox/static/catalog

What it creates (all names/numbers are obviously fake; session ids are
``wa_0000000001NN`` — zeros, never a real phone):

  * vault sessions (metadata.json + JSONL history) for
      Laura    — bot attending, etapa_variantes, Dúo Zodiacal ×2 without aroma, 1 unanswered msg
      Sofía    — asked for a human (EXPLICIT_REQUEST), 12 min unanswered → GRAVE chat fire
      Camilo   — bot is closing the sale (etapa_cierre, full shipping data) → /mobile/hot
      Andrés   — paid order #41 scheduled 4 days ago, still "preparing" → GRAVE order fire
      Valentina— human route, 24 h window CLOSED → the app must offer templates
      Daniela  — order #42 (draft) awaiting payment check + receipt PHOTO → "hoy" fire
  * a synthetic catalog snapshot (4 published products + 1 draft). Photo URLs default to
    https://cdn.sandbox.invalid/catalog/*.png (the real WhatsApp builder only accepts
    https links; .invalid never resolves). The PNGs are also written to
    data/static/catalog and served at /__sandbox/static/catalog (see --image-base).
  * the backing store of the fake Medusa (orders #39–#42 + promo code PRUEBA10)
  * the "running workflows" list of the fake Temporal client

Timestamps are relative to NOW: re-run (or ``inject.py reset``) before a test
session so the "12 min waiting" / "hot in the last 30 min" facts are fresh.
"""
from __future__ import annotations

import argparse
import shutil
import uuid
from typing import Any

from sandbox_common import (
    CATALOG_DIR,
    DATA_DIR,
    DAY_MS,
    DEFAULT_IMAGE_BASE,
    EMULATOR_IMAGE_BASE,
    HOUR_MS,
    MEDUSA_STORE,
    MIN_MS,
    PHONE_NUMBER_ID,
    SEED_INFO,
    STATIC_DIR,
    TEMPORAL_STORE,
    VAULT_DIR,
    append_jsonl,
    atomic_write_json,
    bogota_day,
    iso_utc,
    iso_z,
    now_ms,
    product_png,
    receipt_png,
    session_paths,
)

SESSIONS = {
    "laura": "wa_000000000101",
    "sofia": "wa_000000000102",
    "camilo": "wa_000000000103",
    "andres": "wa_000000000104",
    "valentina": "wa_000000000105",
    "daniela": "wa_000000000106",
}
NAMES = {
    "laura": "Laura Prueba",
    "sofia": "Sofía Prueba",
    "camilo": "Camilo Prueba",
    "andres": "Andrés Prueba",
    "valentina": "Valentina Prueba",
    "daniela": "Daniela Prueba",
}
#: A zeros-only "phone" for shipping forms / Medusa addresses (never a real number).
FAKE_PHONE = "3000000000"

ORDER_IDS = {
    "maria": "order_01SBXMARIA0000000000000039",
    "juan": "order_01SBXJUAN00000000000000040",
    "andres": "order_01SBXANDRES000000000000041",
    "daniela": "order_01SBXDANIELA00000000000042",
}

PROMO_CODE = "PRUEBA10"


def _wamid() -> str:
    return f"wamid.SBX{uuid.uuid4().hex[:20].upper()}"


# ── catalog ──────────────────────────────────────────────────────────────────


def _catalog(image_base: str) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    base = image_base.rstrip("/")
    photos: dict[str, bytes] = {
        "duo-leo.png": product_png((236, 226, 244), (123, 97, 180)),
        "duo-aries.png": product_png((250, 232, 226), (206, 92, 84)),
        "duo-piscis.png": product_png((226, 240, 248), (72, 132, 190)),
        "luz-serena.png": product_png((244, 240, 230), (232, 222, 196)),
        "luz-serena-mesa.png": product_png((236, 232, 222), (214, 204, 176)),
        "plegaria-blanca.png": product_png((240, 238, 236), (252, 252, 250)),
        "plegaria-dorada.png": product_png((245, 238, 220), (212, 175, 55)),
        "cubo-aromatico.png": product_png((232, 246, 238), (150, 206, 170)),
    }

    def img(name: str, rank: int) -> dict[str, Any]:
        return {"url": f"{base}/{name}", "rank": rank}

    def variant(sku: str, amount: int) -> list[dict[str, Any]]:
        return [{"id": f"variant_sbx_{sku.lower()}", "title": "Unico", "sku": sku,
                 "prices": [{"amount": str(amount), "currency_code": "cop"}]}]

    products = [
        {
            "id": "prod_sbx_duo_zodiacal", "handle": "duo-zodiacal", "title": "Dúo Zodiacal",
            "status": "published",
            "description": "Dos velas de cera de palma con el diseño de tu signo. Producto de PRUEBA del sandbox.",
            "thumbnail": f"{base}/duo-leo.png",
            "images": [img("duo-leo.png", 0), img("duo-aries.png", 1), img("duo-piscis.png", 2)],
            "variants": variant("SBX-DUO-01", 89_900),
            "tags": ["Aroma: Lavanda", "Aroma: Vainilla", "Aroma: Canela", "Aroma: Coco",
                     "Color: Azul", "Color: Rosa", "Color: Blanco"],
            "categories": ["velas-zodiacales"],
            "category_labels": {"velas-zodiacales": "Velas Zodiacales"},
        },
        {
            "id": "prod_sbx_luz_serena", "handle": "luz-serena", "title": "Luz Serena",
            "status": "published",
            "description": "Vela de meditación en vaso de vidrio. Producto de PRUEBA del sandbox.",
            "thumbnail": f"{base}/luz-serena.png",
            "images": [img("luz-serena.png", 0), img("luz-serena-mesa.png", 1)],
            "variants": variant("SBX-LUZ-01", 29_000),
            "tags": ["Aroma: Lavanda", "Aroma: Canela", "Aroma: Eucalipto"],
            "categories": ["velas-aromaticas"],
            "category_labels": {"velas-aromaticas": "Velas Aromáticas"},
        },
        {
            "id": "prod_sbx_plegaria", "handle": "plegaria-de-luz", "title": "Plegaria de Luz",
            "status": "published",
            "description": "Velón decorado para intenciones. Producto de PRUEBA del sandbox.",
            "thumbnail": f"{base}/plegaria-blanca.png",
            "images": [img("plegaria-blanca.png", 0), img("plegaria-dorada.png", 1)],
            "variants": variant("SBX-PLG-01", 45_000),
            "tags": ["Aroma: Sándalo", "Aroma: Rosas", "Color: Blanco", "Color: Dorado"],
            "categories": ["velas-religiosas"],
            "category_labels": {"velas-religiosas": "Velas Religiosas"},
        },
        {
            "id": "prod_sbx_cubo", "handle": "cubo-aromatico", "title": "Cubo Aromático",
            "status": "published",
            "description": "Cubo de cera para derretir. Producto de PRUEBA del sandbox.",
            "thumbnail": f"{base}/cubo-aromatico.png",
            "images": [img("cubo-aromatico.png", 0)],
            "variants": variant("SBX-CUB-01", 35_000),
            "tags": ["Aroma: Coco", "Aroma: Vainilla", "Color: Rosa", "Color: Verde menta"],
            "categories": ["velas-aromaticas"],
            "category_labels": {"velas-aromaticas": "Velas Aromáticas"},
        },
        {
            # Borrador: NO debe salir en /api/chats/catalog (filtro status=published).
            "id": "prod_sbx_borrador", "handle": "vela-borrador", "title": "Vela en borrador",
            "status": "draft", "variants": variant("SBX-BRR-01", 10_000), "tags": [],
        },
    ]
    return products, photos


# ── vault helpers ────────────────────────────────────────────────────────────


class Chat:
    """Builds one vault session: JSONL events + metadata."""

    def __init__(self, key: str) -> None:
        self.key = key
        self.session = SESSIONS[key]
        self.name = NAMES[key]
        self.events: list[dict[str, Any]] = []
        self.last_inbound_ms: int | None = None
        self.last_inbound_wamid: str | None = None

    def user(self, at: int, text: str, **extra: Any) -> str:
        wamid = _wamid()
        self.events.append({"role": "user", "content": text, "timestamp": iso_utc(at), "wamid": wamid, **extra})
        self.last_inbound_ms, self.last_inbound_wamid = at, wamid
        return wamid

    def bot(self, at: int, text: str, tools: list[str] | None = None) -> None:
        ev: dict[str, Any] = {"role": "assistant", "content": text, "timestamp": iso_utc(at)}
        if tools:
            ev["tools_used"] = tools
        self.events.append(ev)

    def card(self, at: int, component_kind: str, text: str) -> None:
        self.events.append({"role": "assistant", "kind": "ui_component", "component_kind": component_kind,
                            "content": text, "timestamp": iso_utc(at), "wamid": _wamid()})

    def human(self, at: int, text: str) -> None:
        self.events.append({"role": "assistant", "sender": "human", "content": text, "timestamp": iso_utc(at)})

    def write(self, metadata: dict[str, Any]) -> None:
        meta_path, jsonl_path, _media = session_paths(self.session)
        for ev in self.events:
            append_jsonl(jsonl_path, ev)
        base: dict[str, Any] = {"phone_number_id": PHONE_NUMBER_ID, "profile": {"name": self.name}}
        if self.last_inbound_ms is not None:
            base.update({
                "last_inbound_at_ms": self.last_inbound_ms,
                "last_inbound_message_id": self.last_inbound_wamid,
                # IngestInboundMessage: window = last inbound + 24 h.
                "service_window_expires_at_ms": self.last_inbound_ms + DAY_MS,
            })
        base.update(metadata)
        atomic_write_json(meta_path, base)


def _status(tag: str, motivo: str, route: str, at: int, **extra: Any) -> dict[str, Any]:
    return {"tag": tag, "motivo": motivo, "active_route": route, "timestamp": at / 1000.0, **extra}


def _episode(ep_id: str, started: int, **extra: Any) -> dict[str, Any]:
    return {"episode_id": ep_id, "started_at_ms": started, "closed_at_ms": None, **extra}


def _payment_text(total: int, subtotal: int, shipping: int, reference: str) -> str:
    def cop(v: int) -> str:
        return f"${v:,}".replace(",", ".") + " COP"

    return (
        "Aquí tienes los datos para tu pago anticipado 🤍\n\n*Nequi o llave*: @llave-sandbox\n\n"
        f"*Productos*: {cop(subtotal)}\n*Envío*: {cop(shipping)}\n*Total*: {cop(total)}\n"
        f"Pedido: {reference}\n\nCuando hagas el pago, envíanos el comprobante por este chat."
    )


# ── the six customers ────────────────────────────────────────────────────────


def _laura(t: int) -> None:
    c = Chat("laura")
    c.user(t - 20 * MIN_MS, "Hola 👋 vi sus velas en Instagram, ¿tienen el Dúo Zodiacal?")
    c.bot(t - 19 * MIN_MS, "¡Hola Laura! 🤍 Sí, claro. El Dúo Zodiacal son dos velas de cera de palma con el "
                           "diseño de tu signo, a $89.900. ¿Para quién sería?", tools=["search_products"])
    c.card(t - 19 * MIN_MS + 5_000, "product_detail", "📷 El bot envió una foto del producto: «Dúo Zodiacal · $89.900»")
    c.user(t - 6 * MIN_MS, "Me encanta 😍 es para mi mamá y para mi tía")
    c.bot(t - 5 * MIN_MS, "¡Qué lindo regalo! 🎁 ¿Cuántos te gustaría llevar?")
    c.user(t - 3 * MIN_MS, "¿y qué aromas tienen? quiero 2")
    c.write({
        "active_route": "ventas",
        "tag": "INTERESADO",
        "motivo": "Pregunta por el Dúo Zodiacal (2 unidades) — falta elegir aroma y color",
        "status_history": [_status("INTERESADO", "Pregunta por el Dúo Zodiacal", "ventas", t - 19 * MIN_MS)],
        "episodes": [_episode("ep_001", t - 20 * MIN_MS, msgs_count_at_start=0, order_draft={
            # Slots planos (borrador de UN producto) — draft_items() lo lee como 1 ítem.
            "slots": {"producto": "Dúo Zodiacal", "cantidad": "2"},
            "updated_at_ms": t - 3 * MIN_MS,
        })],
    })


def _sofia(t: int) -> None:
    c = Chat("sofia")
    c.user(t - 40 * MIN_MS, "Buenas tardes, quiero cotizar 30 velas para un evento de mi empresa")
    c.bot(t - 39 * MIN_MS, "¡Hola Sofía! Qué emoción 🤍 ¿Para qué fecha las necesitas y qué estilo de vela te gustaría?")
    c.user(t - 15 * MIN_MS, "Para el 15 de octubre. ¿Me puedes comunicar con una persona? Quiero hablar de precios por volumen")
    c.bot(t - 15 * MIN_MS + 20_000, "Claro que sí, Sofía. Te comunico con una asesora del equipo, en un momento te escribe 🙌",
          tools=["escalate_to_human"])
    c.user(t - 12 * MIN_MS, "Hola?")
    c.user(t - 4 * MIN_MS, "Sigo pendiente 🙏 necesito la cotización hoy")
    motivo = "La clienta pidió hablar con una persona: cotización de 30 velas para un evento empresarial"
    c.write({
        "active_route": "humano",
        "tag": "HUMANO",
        "motivo": motivo,
        "escalation_reason": "EXPLICIT_REQUEST",
        "status_history": [
            _status("INTERESADO", "Cotización corporativa", "ventas", t - 39 * MIN_MS),
            _status("HUMANO", motivo, "humano", t - 15 * MIN_MS, reason_category="EXPLICIT_REQUEST"),
        ],
        "episodes": [_episode("ep_001", t - 40 * MIN_MS, msgs_count_at_start=0)],
    })


def _camilo(t: int) -> None:
    c = Chat("camilo")
    c.user(t - 35 * MIN_MS, "Hola, ¿tienen el velón Plegaria de Luz?")
    c.bot(t - 34 * MIN_MS, "¡Hola Camilo! 🤍 Sí, el Plegaria de Luz está a $45.000. Lo tenemos en blanco o dorado, "
                           "con aroma a sándalo o rosas.", tools=["search_products"])
    c.user(t - 20 * MIN_MS, "Quiero 2 blancos con sándalo")
    c.bot(t - 19 * MIN_MS, "¡Perfecto! 2 Plegaria de Luz blancos con sándalo 🕯️ ¿Te los enviamos?",
          tools=["set_order_slot"])
    c.user(t - 12 * MIN_MS, "Sí, de una")
    c.card(t - 11 * MIN_MS, "shipping_flow", "📋 El bot pidió los datos de envío (formulario)")
    c.user(t - 2 * MIN_MS, "[datos de envío recibidos] receiver_name=Camilo Prueba; phone=3000000000; city=Medellín; "
                           "neighborhood=Laureles; address=Calle Falsa # 12-34; payment_method=Nequi")
    c.write({
        "active_route": "ventas",
        "tag": "INTERESADO",
        "motivo": "Dio los datos de envío — falta confirmar el resumen del pedido",
        "status_history": [_status("INTERESADO", "Quiere 2 Plegaria de Luz", "ventas", t - 19 * MIN_MS)],
        "episodes": [_episode("ep_001", t - 35 * MIN_MS, msgs_count_at_start=0, order_draft={
            "slots": {
                "producto": "Plegaria de Luz", "aroma": "Sándalo", "color": "Blanco", "cantidad": "2",
                "ciudad": "Medellín", "barrio": "Laureles", "direccion": "Calle Falsa # 12-34",
                "telefono": FAKE_PHONE, "nombre_recibe": "Camilo Prueba", "metodo_pago": "Nequi",
            },
            "confirmed_at_ms": t - 12 * MIN_MS,
            "updated_at_ms": t - 2 * MIN_MS,
        })],
    })


def _andres(t: int) -> None:
    c = Chat("andres")
    ordered = t - 6 * DAY_MS
    order_id = ORDER_IDS["andres"]
    due = bogota_day(t, -4)
    c.user(ordered, "Hola, quiero el Dúo Zodiacal en lavanda, color azul, 2 por favor")
    c.bot(ordered + 60_000, "¡Hola Andrés! 🤍 Anotado: 2 Dúo Zodiacal lavanda, color azul.", tools=["set_order_slot"])
    c.card(ordered + 5 * MIN_MS, "order_confirmation", "🧾 El bot envió el resumen del pedido con botones para confirmar")
    c.user(ordered + 7 * MIN_MS, "[el cliente tocó el botón: Confirmar pedido]")
    c.bot(ordered + 8 * MIN_MS, _payment_text(187_700, 179_800, 7_900, "#41 (Dúo Zodiacal)"), tools=["register_order"])
    c.user(ordered + 40 * MIN_MS, "Listo, ya pagué ✅")
    c.human(ordered + 2 * HOUR_MS, f"¡Pago confirmado, Andrés! Tu pedido #41 queda agendado para el {due} 🙌")
    c.user(t - 25 * MIN_MS, "Hola, buenas. Mi pedido #41 no ha llegado y ya pasaron varios días 😕")
    c.bot(t - 24 * MIN_MS, "¡Hola Andrés! Qué pena la demora 🙏 Ya le pregunto al equipo por el estado de tu "
                           "pedido #41 y te cuento.")
    c.write({
        "active_route": "ventas",
        # Episodio nuevo tras COMPRA_EXITOSA → ensure_active_episode resetea el tag.
        "tag": "NO_ETIQUETADO",
        "status_history": [
            _status("CONFIRMADO_PAGO_PENDIENTE", "Pedido #41 registrado", "humano", ordered + 8 * MIN_MS),
            _status("COMPRA_EXITOSA", "Pago verificado por operador@sandbox desde dashboard de orders — "
                    "la conversación vuelve al bot de ventas", "ventas", ordered + 2 * HOUR_MS,
                    source="orders_confirm_payment"),
        ],
        "registered_order": {
            "order_id": order_id, "session_key": c.session, "provider": "medusa", "success": True,
            "error_detail": None, "customer_id": "cus_sbx_andres",
            "items": [{"handle": "duo-zodiacal", "title": "Dúo Zodiacal", "quantity": 2,
                       "unit_price_cop": 89_900, "variant_label": "Lavanda, Azul"}],
            "item_variants": [{"color": "Azul", "aroma": "Lavanda"}],
            "shipping": {"city": "Bogotá", "neighborhood": "Chapinero", "address": "Calle Falsa # 12-34",
                         "receiver_name": "Andrés Prueba", "phone": FAKE_PHONE},
            "payment_method": "transfer", "subtotal_cop": 179_800, "shipping_cop": 7_900, "discount_cop": 0,
            "coupon_code": None, "total_cop": 187_700, "currency": "COP", "registered_at_ms": ordered + 8 * MIN_MS,
        },
        "registered_orders_history": [
            {"order_id": order_id, "provider": "medusa", "success": True, "ts_ms": ordered + 8 * MIN_MS},
        ],
        "episodes": [
            {"episode_id": "ep_001", "started_at_ms": ordered, "closed_at_ms": ordered + 9 * MIN_MS,
             "closing_tag": "COMPRA_EXITOSA", "closing_motivo": "Pedido #41 registrado",
             "order_id": order_id, "order_total_cop": 187_700, "order_currency": "COP",
             "payment_confirmed_at_ms": ordered + 2 * HOUR_MS, "payment_confirmed_by": "operador@sandbox"},
            _episode("ep_002", t - 25 * MIN_MS),
        ],
    })


def _valentina(t: int) -> None:
    c = Chat("valentina")
    c.user(t - 27 * HOUR_MS, "Hola, ¿hacen centros de mesa con velas para una boda?")
    c.bot(t - 27 * HOUR_MS + 30_000, "¡Hola Valentina! 💍 Te comunico con una asesora para ayudarte con los centros "
                                     "de mesa.", tools=["escalate_to_human"])
    c.user(t - 26 * HOUR_MS, "Dale, gracias. Son 12 mesas")
    c.human(t - 25 * HOUR_MS - 30 * MIN_MS, "¡Hola Valentina! Soy la asesora 😊 Te preparo una propuesta con fotos y "
                                           "te escribo.")
    motivo = "Centros de mesa para boda (12 mesas) — pidió asesoría personalizada"
    c.write({
        "active_route": "humano",
        "tag": "HUMANO",
        "motivo": motivo,
        "escalation_reason": "EXPLICIT_REQUEST",
        "status_history": [_status("HUMANO", motivo, "humano", t - 27 * HOUR_MS, reason_category="EXPLICIT_REQUEST")],
        "episodes": [_episode("ep_001", t - 27 * HOUR_MS, msgs_count_at_start=0)],
    })  # last inbound 26 h ago → service_window_expires_at_ms = 2 h ago (CLOSED)


def _daniela(t: int) -> None:
    c = Chat("daniela")
    order_id = ORDER_IDS["daniela"]
    receipt = "sbx-comprobante-42.png"
    _meta, _jsonl, media_dir = session_paths(c.session)
    media_dir.mkdir(parents=True, exist_ok=True)
    (media_dir / receipt).write_bytes(receipt_png())
    c.user(t - 60 * MIN_MS, "¡Hola! Quiero 2 velas Luz Serena en lavanda para Cali")
    c.bot(t - 59 * MIN_MS, "¡Hola Daniela! 🤍 Claro: 2 Luz Serena con aroma a lavanda, $29.000 cada una.",
          tools=["set_order_slot"])
    c.card(t - 57 * MIN_MS, "shipping_flow", "📋 El bot pidió los datos de envío (formulario)")
    c.user(t - 54 * MIN_MS, "[datos de envío recibidos] receiver_name=Daniela Prueba; phone=3000000000; city=Cali; "
                            "neighborhood=San Fernando; address=Carrera Falsa # 45-67; payment_method=Nequi")
    c.bot(t - 50 * MIN_MS, _payment_text(74_940, 58_000, 16_940, "#42 (Luz Serena)"), tools=["register_order"])
    # Foto del comprobante: mismo marker + image_url que deja el reentry de visión del ingest.
    c.user(t - 20 * MIN_MS,
           "[el cliente envió un comprobante de pago: Comprobante de transferencia Nequi por $74.940, "
           'referencia 42] con el texto: "Listo, ya pagué 🙌"',
           image_url=f"/api/dashboard/media/{c.session}/{receipt}")
    c.human(t - 18 * MIN_MS, "¡Gracias, Daniela! Ya lo estamos verificando y te confirmo en un ratico 🙌")
    motivo = "Pedido #42 registrado por transferencia — verificar el comprobante de pago"
    c.write({
        "active_route": "humano",
        "tag": "HUMANO",
        "motivo": motivo,
        "escalation_reason": "PAYMENT_VERIFICATION_PENDING",
        "status_history": [
            _status("CONFIRMADO_PAGO_PENDIENTE", "Pedido #42 registrado", "ventas", t - 50 * MIN_MS),
            _status("HUMANO", motivo, "humano", t - 50 * MIN_MS, reason_category="PAYMENT_VERIFICATION_PENDING"),
        ],
        "registered_order": {
            "order_id": order_id, "session_key": c.session, "provider": "medusa", "success": True,
            "error_detail": None, "customer_id": "cus_sbx_daniela",
            "items": [{"handle": "luz-serena", "title": "Luz Serena", "quantity": 2,
                       "unit_price_cop": 29_000, "variant_label": "Lavanda"}],
            "item_variants": [{"color": None, "aroma": "Lavanda"}],
            "shipping": {"city": "Cali", "neighborhood": "San Fernando", "address": "Carrera Falsa # 45-67",
                         "receiver_name": "Daniela Prueba", "phone": FAKE_PHONE},
            "payment_method": "transfer", "subtotal_cop": 58_000, "shipping_cop": 16_940, "discount_cop": 0,
            "coupon_code": None, "total_cop": 74_940, "currency": "COP", "registered_at_ms": t - 50 * MIN_MS,
        },
        "registered_orders_history": [
            {"order_id": order_id, "provider": "medusa", "success": True, "ts_ms": t - 50 * MIN_MS},
        ],
        "episodes": [{
            "episode_id": "ep_001", "started_at_ms": t - 60 * MIN_MS, "closed_at_ms": t - 50 * MIN_MS,
            "closing_tag": "CONFIRMADO_PAGO_PENDIENTE", "closing_motivo": "Pedido #42 registrado, pago pendiente",
            "order_id": order_id, "order_total_cop": 74_940, "order_currency": "COP",
        }],
    })


# ── fake Medusa store ────────────────────────────────────────────────────────


def _address(first: str, city: str, neighborhood: str, address: str) -> dict[str, Any]:
    return {"first_name": first, "last_name": "Prueba", "phone": FAKE_PHONE, "address_1": address,
            "address_2": neighborhood, "city": city, "country_code": "co", "postal_code": None}


def _line(n: int, title: str, handle: str, sku: str, qty: int, price: int, thumb: str,
          aroma: str | None, color: str | None) -> dict[str, Any]:
    label = ", ".join(p for p in (aroma, color) if p) or None
    meta: dict[str, Any] = {"handle": handle}
    if label:
        meta["variant_label"] = label
    if aroma:
        meta["aroma"] = aroma
    if color:
        meta["color"] = color
    return {"id": f"ordli_sbx_{n}", "title": title, "subtitle": None, "variant_title": "Unico",
            "variant_sku": sku, "quantity": qty, "unit_price": price, "total": price * qty,
            "thumbnail": thumb, "metadata": meta}


def _medusa_order(*, order_id: str, display_id: int, first: str, created: int, items: list[dict[str, Any]],
                  shipping: int, address: dict[str, Any], status: str, payment_status: str,
                  fulfillment_status: str, metadata: dict[str, Any]) -> dict[str, Any]:
    subtotal = sum(i["total"] for i in items)
    return {
        "id": order_id, "display_id": display_id, "status": status, "payment_status": payment_status,
        "fulfillment_status": fulfillment_status, "email": f"sandbox+{first.lower()}@example.invalid",
        "currency_code": "cop", "created_at": iso_z(created), "updated_at": iso_z(created), "canceled_at": None,
        "total": subtotal + shipping, "subtotal": subtotal + shipping, "item_subtotal": subtotal,
        "shipping_total": shipping, "tax_total": 0, "discount_total": 0,
        "region_id": "reg_sbx_co", "customer_id": f"cus_sbx_{first.lower()}", "sales_channel_id": "sc_sbx_whatsapp",
        "customer": {"id": f"cus_sbx_{first.lower()}", "first_name": first, "last_name": "Prueba",
                     "email": f"sandbox+{first.lower()}@example.invalid"},
        "shipping_address": address, "billing_address": address,
        "sales_channel": {"id": "sc_sbx_whatsapp", "name": "WhatsApp (sandbox)"},
        "items": items, "metadata": metadata,
        "fulfillments": [], "shipping_methods": [], "transactions": [], "payment_collections": [],
    }


def _paid_collection(n: int, amount: int, at: int) -> list[dict[str, Any]]:
    return [{"id": f"paycol_sbx_{n}", "amount": amount, "status": "paid", "currency_code": "cop",
             "payments": [{"id": f"pay_sbx_{n}", "amount": amount, "provider_id": "pp_system_default",
                           "captured_at": iso_z(at), "captures": [{"id": f"capt_sbx_{n}", "amount": amount}],
                           "refunds": []}]}]


def _history(*entries: tuple[str | None, str, int, str]) -> list[dict[str, Any]]:
    return [{"from": f, "to": to, "at_ms": at, "by": by} for f, to, at, by in entries]


def _medusa_store(t: int, image_base: str) -> dict[str, Any]:
    thumbs = image_base.rstrip("/")
    orders: list[dict[str, Any]] = []

    created = t - 9 * DAY_MS
    maria = _medusa_order(
        order_id=ORDER_IDS["maria"], display_id=39, first="María", created=created,
        items=[_line(391, "Cubo Aromático", "cubo-aromatico", "SBX-CUB-01", 1, 35_000,
                     f"{thumbs}/cubo-aromatico.png", "Coco", "Rosa")],
        shipping=7_900, address=_address("María", "Bogotá", "Usaquén", "Avenida Falsa # 1-23"),
        status="completed", payment_status="captured", fulfillment_status="delivered",
        metadata={"source": "hubara_whatsapp_sales", "payment_method": "transfer",
                  "hubara_stage": "delivered", "hubara_payment_confirmed": True,
                  "hubara_payment_confirmed_at_ms": created + HOUR_MS,
                  "hubara_scheduled_delivery_iso": bogota_day(t, -6),
                  "hubara_stage_history": _history((None, "new", created, "agent"),
                                                   ("new", "preparing", created + 2 * HOUR_MS, "human"),
                                                   ("preparing", "ready", created + DAY_MS, "human"),
                                                   ("ready", "shipping", created + 2 * DAY_MS, "human"),
                                                   ("shipping", "delivered", created + 3 * DAY_MS, "human"))},
    )
    maria["payment_collections"] = _paid_collection(39, 42_900, created + HOUR_MS)
    orders.append(maria)

    created = t - 3 * DAY_MS
    juan = _medusa_order(
        order_id=ORDER_IDS["juan"], display_id=40, first="Juan", created=created,
        items=[_line(401, "Luz Serena", "luz-serena", "SBX-LUZ-01", 3, 29_000,
                     f"{thumbs}/luz-serena.png", "Eucalipto", None)],
        shipping=16_940, address=_address("Juan", "Medellín", "El Poblado", "Transversal Falsa # 9-87"),
        status="pending", payment_status="captured", fulfillment_status="not_fulfilled",
        metadata={"source": "hubara_whatsapp_sales", "payment_method": "transfer",
                  "hubara_stage": "shipping", "hubara_payment_confirmed": True,
                  "hubara_payment_confirmed_at_ms": created + HOUR_MS,
                  "hubara_scheduled_delivery_iso": bogota_day(t, 0),
                  "hubara_scheduled_delivery_time": "16:00",
                  "hubara_stage_history": _history((None, "new", created, "agent"),
                                                   ("new", "preparing", created + 2 * HOUR_MS, "human"),
                                                   ("preparing", "ready", created + DAY_MS, "human"),
                                                   ("ready", "shipping", t - 5 * HOUR_MS, "human"))},
    )
    juan["payment_collections"] = _paid_collection(40, 103_940, created + HOUR_MS)
    orders.append(juan)

    created = t - 6 * DAY_MS + 8 * MIN_MS
    andres = _medusa_order(
        order_id=ORDER_IDS["andres"], display_id=41, first="Andrés", created=created,
        items=[_line(411, "Dúo Zodiacal", "duo-zodiacal", "SBX-DUO-01", 2, 89_900,
                     f"{thumbs}/duo-leo.png", "Lavanda", "Azul")],
        shipping=7_900, address=_address("Andrés", "Bogotá", "Chapinero", "Calle Falsa # 12-34"),
        status="pending", payment_status="captured", fulfillment_status="not_fulfilled",
        metadata={"source": "hubara_whatsapp_sales", "session_key": SESSIONS["andres"],
                  "payment_method": "transfer", "idempotency_key": "sbx-andres-41",
                  "hubara_stage": "preparing", "hubara_payment_confirmed": True,
                  "hubara_payment_confirmed_at_ms": created + 2 * HOUR_MS,
                  "hubara_payment_confirmed_by": "operador@sandbox",
                  "hubara_scheduled_delivery_iso": bogota_day(t, -4),
                  "hubara_scheduled_delivery_time": "15:00",
                  "hubara_human_note": "Entregar en portería (dato de prueba)",
                  "hubara_stage_history": _history((None, "new", created, "agent"),
                                                   ("new", "preparing", created + 3 * HOUR_MS, "human"))},
    )
    andres["payment_collections"] = _paid_collection(41, 187_700, created + 2 * HOUR_MS)
    orders.append(andres)

    created = t - 50 * MIN_MS
    daniela = _medusa_order(
        order_id=ORDER_IDS["daniela"], display_id=42, first="Daniela", created=created,
        items=[_line(421, "Luz Serena", "luz-serena", "SBX-LUZ-01", 2, 29_000,
                     f"{thumbs}/luz-serena.png", "Lavanda", None)],
        shipping=16_940, address=_address("Daniela", "Cali", "San Fernando", "Carrera Falsa # 45-67"),
        status="draft", payment_status="not_paid", fulfillment_status="not_fulfilled",
        metadata={"source": "hubara_whatsapp_sales", "session_key": SESSIONS["daniela"],
                  "payment_method": "transfer", "idempotency_key": "sbx-daniela-42",
                  "hubara_stage": "new", "hubara_stage_history": _history((None, "new", created, "agent"))},
    )
    orders.append(daniela)

    promotion = {
        "id": "promo_sbx_prueba10", "code": PROMO_CODE, "type": "standard", "is_automatic": False,
        "status": "active",
        "application_method": {"type": "percentage", "value": 10, "currency_code": "cop",
                               "target_type": "items", "allocation": "each", "max_quantity": None,
                               "target_rules": []},
        "rules": [], "campaign": None,
    }
    return {"orders": orders, "promotions": [promotion], "seq": {"display_id": 42, "n": 100}}


def _campaigns(t: int) -> None:
    """Una campaña de WhatsApp ya enviada (plugin de marketing, `<vault>/_campaigns/`): la lee la pantalla del servidor
    «Campañas» de la App Operador. Datos sintéticos, sin teléfonos."""
    sent_at = t - 2 * DAY_MS
    atomic_write_json(VAULT_DIR / "_campaigns" / "mkt-sbx0000001.json", {
        "id": "mkt-sbx0000001", "name": "Velas de octubre (prueba)", "status": "sent", "goal": "reactivar clientes",
        "percent": 15, "coupon_code": "OCTUBRE15", "valid_until": "2026-10-31", "segments": ["compradores"],
        "excluded_session_ids": [], "extra_session_ids": [], "imported_contacts": [], "carousel_handles": [],
        "message": {"header": "Llegaron las velas de octubre", "body": "Hola, tenemos aromas nuevos con 15 % de descuento."},
        "template_name": "campaign_promo_marketing_v1", "schedule_at_ms": None,
        "created_at_ms": sent_at - DAY_MS, "updated_at_ms": sent_at, "sent_at_ms": sent_at,
        "send_result": {"planned": 120, "sent": 118, "failed": [{"reason": "131026"}, {"reason": "131026"}], "skipped": [],
                        "unit_cost_usd_micros": 12_000, "spent_usd_micros": 1_416_000},
        "failure_reason": None, "test_sends": [],
    })


def _temporal_state() -> dict[str, Any]:
    # Workflows "vivos" que el fake de Temporal reporta RUNNING: los chats que
    # atiende el bot (intervenir los termina, como en producción).
    running = [f"session-{SESSIONS[k]}" for k in ("laura", "camilo", "andres")]
    return {"running": {wid: {"type": "HubaraSalesSessionWorkflow", "task_queue": "queue-sales-agent"}
                        for wid in running}}


def build(*, image_base: str = DEFAULT_IMAGE_BASE) -> dict[str, Any]:
    """Wipe and rebuild the whole data dir. Returns the seed info."""
    t = now_ms()
    for sub in (VAULT_DIR, CATALOG_DIR, STATIC_DIR, MEDUSA_STORE.parent, TEMPORAL_STORE.parent):
        if sub.exists():
            shutil.rmtree(sub)
    VAULT_DIR.mkdir(parents=True, exist_ok=True)

    products, photos = _catalog(image_base)
    photo_dir = STATIC_DIR / "catalog"
    photo_dir.mkdir(parents=True, exist_ok=True)
    for name, blob in photos.items():
        (photo_dir / name).write_bytes(blob)
    atomic_write_json(CATALOG_DIR / "snapshot.json", products)
    atomic_write_json(CATALOG_DIR / "manifest.json", {
        "version": f"sandbox-{t}", "fetched_at": iso_utc(t), "product_count": len(products),
        "source_etag": "sandbox",
    })

    for build_chat in (_laura, _sofia, _camilo, _andres, _valentina, _daniela):
        build_chat(t)

    atomic_write_json(MEDUSA_STORE, _medusa_store(t, image_base))
    atomic_write_json(TEMPORAL_STORE, _temporal_state())
    _campaigns(t)

    info = {
        "seeded_at_ms": t,
        "seeded_at": iso_utc(t),
        "image_base": image_base,
        "data_dir": str(DATA_DIR),
        "sessions": {k: {"session_id": SESSIONS[k], "name": NAMES[k]} for k in SESSIONS},
        "orders": {"#39": ORDER_IDS["maria"], "#40": ORDER_IDS["juan"], "#41": ORDER_IDS["andres"],
                   "#42": ORDER_IDS["daniela"]},
        "promo_code": PROMO_CODE,
    }
    atomic_write_json(SEED_INFO, info)
    return info


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image-base", default=DEFAULT_IMAGE_BASE,
                        help=("base URL of catalog photos (default %(default)s: https, so the real WhatsApp "
                              f"builder accepts photo tools). Use {EMULATOR_IMAGE_BASE} to see thumbnails "
                              "in the emulator (photo tools are then rejected: http)."))
    args = parser.parse_args()
    info = build(image_base=args.image_base)
    print(f"sandbox seeded at {info['seeded_at']} → {info['data_dir']}")
    for key, s in info["sessions"].items():
        print(f"  {s['session_id']}  {s['name']:<17} ({key})")
    for ref, oid in info["orders"].items():
        print(f"  order {ref}  {oid}")


if __name__ == "__main__":
    main()
