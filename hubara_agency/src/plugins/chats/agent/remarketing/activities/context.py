"""Activities de contexto del gancho (I/O acá; la transformación en use_cases).

`read_remarketing_context_activity` lee del vault el metadata de la sesión y
el transcript JSONL (`<vault>/<sid>/sessions/<sid>.jsonl` — convención del
`FilesystemMessageHistoryStore` de plataforma, el log que también lee el
dashboard). Tolerante: archivo ausente o líneas corruptas → contexto parcial,
nunca falla el gancho por esto.
"""
from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from temporalio import activity

from src.plugins.chats.agent.remarketing.contracts import (
    RemarketingContext,
    RemarketingTriggerInput,
)
from src.plugins.chats.agent.remarketing.prompts import build_remarketing_trigger
from src.plugins.chats.agent.remarketing.use_cases.context import (
    catalog_facts_for,
    contact_transcript,
    context_from_metadata,
    customer_text_for,
    episode_shown_titles,
    hook_number_sources,
    named_products,
)
from src.plugins.chats.shared import sales_campaigns
from src.plugins.chats.shared.agent_decisions import decide_catalog_context, decide_contact
from src.plugins.chats.shared.payment_terms import CASH_ON_DELIVERY_MIN_PRODUCTS_COP
from src.plugins.chats.shared.product_truth import unavailable_terms
from src.plugins.chats.shared.store_pack import store_domain, vocabulary
from src.sdk.catalogkit import CatalogError, get_catalog_client
from src.sdk.connectorkit import coupon_problem, get_promotions_port
from src.sdk.messagingkit import effective_max_touches

#: el snapshot publicado son ~30 productos; el tope solo evita un prompt
#: desbordado si el catálogo crece.
_CATALOG_LIMIT = 200
#: la descripción del producto que vio, para el toque que se lo EXPLICA
#: (análisis 2026-10-13); la ficha de siempre se corta a 280.
_EXPLAIN_DESCRIPTION_CHARS = 900
#: Cuántos productos de la tarjeta llevan su ficha: el catálogo completo eran
#: hasta 30 fichas de 900 caracteres en cada toque (revisión del PR #426).
_EXPLAIN_MAX_PRODUCTS = 4


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


@activity.defn(name="read_remarketing_context_activity")
async def read_remarketing_context_activity(session_id: str) -> RemarketingContext:
    # Import local: el valor se resuelve al CALL time para que el fixture
    # `_isolate_vault_dir` de tests pueda re-bindear `src.sdk.runtime`.
    from src.sdk.runtime import WORKSPACE_VAULT_DIR

    session_dir = Path(WORKSPACE_VAULT_DIR) / session_id
    metadata = _read_json(session_dir / "metadata.json")
    events = _read_jsonl(session_dir / "sessions" / f"{session_id}.jsonl")
    context = context_from_metadata(metadata, events, now_ms=int(time.time() * 1000))
    # Motor de decisiones (F8, capacidad `contactar`): ¿el gancho sobra? Se
    # decide ANTES de redactar; con el bot de hoy (o sin el motor conectado)
    # no hay decisión y el LLM decide como siempre.
    skip, trace = await decide_contact(
        session_id=session_id,
        # Con cómo terminó la conversación anterior a la vista (2026-09-30).
        transcript=contact_transcript(metadata or {}, events),
        touch_number=context.touch_number,
        silence_minutes=context.silence_minutes,
        vault_dir=Path(WORKSPACE_VAULT_DIR),
    )
    # Ya compró y su cortesía quedó sin respuesta: el cierre se le debe
    # (operador, 2026-10-01: un NO_MESSAGE ahí queda frío). Jev decide el
    # resto; esto solo le quita el corte a ese caso.
    context = replace(
        context,
        skip_touch=skip and context.post_purchase != "closing",
        contact=trace,
        # El tope real de Frecuencia: con 3 toques, el 3º es el último.
        total_touches=effective_max_touches(Path(WORKSPACE_VAULT_DIR)),
    )
    products = await _catalog_products()
    context = await _with_campaign_script(context, metadata or {}, session_dir, products)
    if not products:
        return _with_allowed_numbers(context)
    mentioned = f"{context.tag_motivo}\n{context.transcript}"
    customer_text = customer_text_for(metadata, events)
    # Motor de decisiones (F3): qué ficha ve el gancho («producto nombrado») y
    # qué pidió el cliente que no existe («fuera de catálogo»). Con el bot de
    # hoy (o sin el motor conectado), las reglas de siempre.
    named, terms = await decide_catalog_context(
        session_id=session_id,
        text=mentioned,
        customer_text=customer_text,
        products=products,
        named=[p.title for p in named_products(products, mentioned=mentioned)],
        terms=unavailable_terms(customer_text, products),
        vault_dir=Path(WORKSPACE_VAULT_DIR),
    )
    description_chars = None
    if context.silent_after_catalog:
        # Se calló tras el catálogo (análisis 2026-10-13): la ficha ENTERA de
        # lo que vio en la tarjeta, para explicárselo.
        titles = {p.title for p in products}
        seen = [t for t in episode_shown_titles(metadata or {}, events) if t in titles][:_EXPLAIN_MAX_PRODUCTS]
        named = list(dict.fromkeys([*named, *seen]))
        # La ficha entera es para el toque que explica (el 1º); los demás, la
        # de siempre.
        if context.touch_number in (None, 1):
            description_chars = _EXPLAIN_DESCRIPTION_CHARS
    facts_kwargs = {"description_chars": description_chars} if description_chars else {}
    described = context.silent_after_catalog and any(
        (p.description or "").strip() for p in products if p.title in set(named)
    )
    return _with_allowed_numbers(replace(
        context,
        catalog_facts=catalog_facts_for(products, mentioned=mentioned, named=named, **facts_kwargs),
        unavailable_terms=terms,
        catalog_described=described,
    ))


async def _with_campaign_script(
    context: RemarketingContext, metadata: dict[str, Any], session_dir: Path, products: list[Any]
) -> RemarketingContext:
    """El guion de la campaña de venta del episodio para ESTE toque (operador,
    2026-10-19), con los precios del catálogo; en su toque, la oferta del
    cupón con su fecha para quien quiso comprar (2026-10-20). Sin campaña, o
    si algo falla, el contexto tal cual: el gancho de siempre."""
    try:
        return await _campaign_script(context, metadata, session_dir, products)
    except Exception as exc:  # noqa: BLE001 — una campaña rota deja el gancho de siempre
        activity.logger.warning("remarketing: guion de campaña no disponible: %r", exc)
        return context


def _purchase_readings(path: Path) -> list[dict[str, Any]]:
    """Las lecturas de la capacidad `compra` de `evals/decisions.jsonl`, línea
    a línea: el archivo de un cliente de meses pesa megas y solo importan esas.
    Una línea cortada (a mitad de una «á») se salta y no tumba el toque
    (revisión del 2026-10-20)."""
    rows: list[dict[str, Any]] = []
    try:
        with path.open("rb") as handle:
            for line in handle:
                if b'"compra"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        return rows
    return rows


#: Los toques que le llegaron al cliente (`_record_touch` del workflow); uno
#: viejo sin `kind` cuenta. Abstenerse (NO_MESSAGE) o una plantilla que falló no.
_SENT_TOUCHES = frozenset({"free_form", "template", None})


def _episode_touches(metadata: dict[str, Any], episode: dict[str, Any]) -> int:
    """Los toques ya enviados en este episodio (la escalera vuelve a contar
    desde el último mensaje del cliente; la oferta va una vez por episodio)."""
    start = episode.get("started_at_ms")
    return sum(
        1
        for t in metadata.get("remarketing_touches") or []
        if isinstance(t, dict)
        and t.get("kind") in _SENT_TOUCHES
        and isinstance(t.get("at_ms"), int)
        and (not isinstance(start, int) or t["at_ms"] >= start)
    )


async def _campaign_script(
    context: RemarketingContext, metadata: dict[str, Any], session_dir: Path, products: list[Any]
) -> RemarketingContext:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    episodes = [e for e in metadata.get("episodes") or [] if isinstance(e, dict)]
    episode = episodes[-1] if episodes else None
    today = datetime.now(ZoneInfo("America/Bogota")).date()
    # Sin el texto del cliente: el ingest de Ventas ya marcó el episodio con lo
    # que dijo al abrirlo; aquí cuentan la marca, el anuncio y el pedido.
    campaign = sales_campaigns.campaign_for(episode, today=today)
    if campaign is None or episode is None:
        return context
    try:
        boxes = store_domain().get("sold_by_box")
    except Exception:  # noqa: BLE001 — sin el dominio, los precios van por unidad
        boxes = None
    # Las lecturas de Jev de la conversación (`evals/decisions.jsonl`): dicen si
    # el cliente quiso comprar.
    intent = sales_campaigns.purchase_intent(episode, _purchase_readings(session_dir / "evals" / "decisions.jsonl"))
    offer_touch = bool(campaign.toque_oferta) and _episode_touches(metadata, episode) == (campaign.toque_oferta or 0) - 1
    offer = None
    # Con un cupón ya aplicado en el episodio no se ofrece otro.
    if intent and offer_touch and not episode.get("applied_coupon"):
        bought_before = any(e.get("order_id") for e in episodes[:-1])
        ids = tuple(str(p.id) for p in products if getattr(p, "handle", None) in campaign.productos and getattr(p, "id", None))
        offer = await _coupon_offer(campaign.cupon, bought_before=bought_before, today=today, product_ids=ids)
        # Ventas ya se la dijo en este episodio (con esta fecha): no se repite.
        if offer is not None and episode.get(sales_campaigns.OFFER_KEY) == offer.key():
            offer = None
    block = sales_campaigns.remarketing_block(
        campaign, products, cod_min=CASH_ON_DELIVERY_MIN_PRODUCTS_COP, boxes=boxes,
        touch_number=context.touch_number, offer=offer, intent=intent, offer_touch=offer_touch,
    )
    return replace(
        context,
        sales_campaign_script=block,
        sales_campaign_examples=sales_campaigns.remarketing_examples(campaign),
    )


#: El tope de la consulta del cupón: la activity tiene 15 s y ya espera a Jev.
_COUPON_TIMEOUT_S = 3.0


async def _coupon_offer(code: str | None, *, bought_before: bool, today: Any, product_ids: tuple[str, ...] = ()) -> Any:
    """La oferta del cupón si rige hoy (`coupon_problem`, la misma vara de
    `list_promotions`: Medusa lo deja «active» al vencer) y, si es solo para la
    primera compra, el cliente no compró antes en esta conversación. None si
    no rige o no se sabe a tiempo: el toque no lo nombra."""
    import asyncio

    if not code:
        return None
    try:
        promotion = await asyncio.wait_for(get_promotions_port().get_by_code(code), timeout=_COUPON_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — sin promociones, el toque no nombra el cupón
        activity.logger.warning("remarketing: promociones no disponibles para el guion: %r", exc)
        return None
    if promotion is None or coupon_problem(promotion, now_ms=int(time.time() * 1000)) is not None:
        return None
    offer = sales_campaigns.coupon_offer(promotion, today=today, product_ids=product_ids)
    if offer is not None and offer.first_purchase_only and bought_before:
        return None
    return offer


def _with_allowed_numbers(context: RemarketingContext) -> RemarketingContext:
    """Los números que el gancho puede escribir (`hook_number_sources`)."""
    practical_facts = str(vocabulary().get("practical_facts") or "")
    return replace(context, allowed_numbers=hook_number_sources(context, practical_facts=practical_facts))


async def _catalog_products() -> list[Any]:
    """Productos del snapshot que lee Ventas; [] si no está (nunca tumba el gancho).

    Sin catálogo el trigger le prohíbe al LLM afirmar atributos de producto:
    degradar a "no sé" es seguro, inventar no (incidente 2026-09-25).
    """
    try:
        result = await get_catalog_client().search("", limit=_CATALOG_LIMIT)
    except (CatalogError, OSError, ValueError) as exc:
        activity.logger.warning("remarketing: catálogo no disponible para el gancho: %s", exc)
        return []
    return list(result.results)


@activity.defn(name="build_remarketing_trigger_v2_activity")
async def build_remarketing_trigger_v2_activity(input: RemarketingTriggerInput) -> str:
    """Trigger con contexto real (motivo del tag + draft + transcript)."""
    return build_remarketing_trigger(
        input.motivo,
        input.memory_context,
        has_order_draft=input.has_order_draft,
        transcript=input.transcript,
        touch_number=input.touch_number,
        silence_minutes=input.silence_minutes,
        campaign_context=input.campaign_context,
        catalog_facts=input.catalog_facts,
        unavailable_terms=input.unavailable_terms,
        post_purchase=input.post_purchase,
        silent_after_catalog=input.silent_after_catalog,
        catalog_described=input.catalog_described,
        sales_campaign_script=input.sales_campaign_script,
        sales_campaign_examples=list(input.sales_campaign_examples),
        # Contexto legacy sin el tope: el de siempre (5).
        **({"total_touches": input.total_touches} if input.total_touches else {}),
    )
