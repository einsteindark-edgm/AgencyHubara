"""MedusaOrderRegistration — adapter live del `OrderRegistrationPort`.

Implementacion de referencia que registra el pedido contra Medusa v2 via
`POST /admin/draft-orders`. El draft order queda en Medusa con
`is_draft_order=True` y el operador (humano) lo convierte a orden real con
un click cuando confirma el pago — flujo standard del backoffice Medusa.

R-DIP: este modulo NO importa de ningun agente. Es un adapter de
`platform/orders/port.py` (Protocol) consumido por el composition root
del worker `chats.sales` (`src/plugins/chats/workers/sales.py`).

R-JSON: devuelve `OrderRegistrationResult` (frozen dataclass) JSON-safe.

Patron de resiliencia (la pregunta del usuario sobre Temporal):
  * `HttpMedusaClient._request` ya tiene tenacity con 3 attempts +
    exponential backoff sobre `httpx.TransportError` (transient network).
  * Este adapter NO agrega su propio retry loop sobre 5xx — lo deja burbujear
    a la `execute_tool` activity de Temporal, que tiene su propio
    `RetryPolicy` configurado en el worker. Asi Temporal es quien decide
    cuanto retrear (declarative, no escondido en codigo).
  * Si despues de todos los retries Medusa sigue rota, el adapter captura
    la excepcion final y devuelve `success=False` + `error_detail` → la
    tool le dice al LLM que escale via
    `escalate_to_human(reason_category='ORDER_REGISTRATION_FAILED')`.

Premortem fixes aplicados (ver docs/PREMORTEM_ORDERS.md):
  * **B1**: idempotency_key derivado de session_key+ts_bucket(10min) embebido
    en `metadata.idempotency_key` del draft order.
  * **C1**: `asyncio.wait_for` con timeout total de 45s para evitar que el
    activity execute_tool se cuelgue indefinidamente.
  * **E1**: `_resolve_items` paraleliza N round-trips a Medusa con
    `asyncio.gather`.
  * **F1**: log estructurado con `session_key` correlation en cada error.
  * **H1**: `_discover_shipping_option_id` filtra por name/provider para no
    elegir "Recogida en tienda" por accidente cuando hay multiples opciones.
  * **H2**: validacion explicita de `default_currency == "cop"` (zero-decimal)
    para que un cambio a USD no rompa el pricing silenciosamente.
  * **H3**: `variant_label_mismatch` flag en metadata cuando el LLM pidio una
    variante que no matcheo — visible al operador en el dashboard. La clase
    del match (`variant_match_kind`) y los tokens que sobraron viven en la
    misma metadata; el matching en si vive en `variant_matching`.

Flujo del adapter:
  1. Resolve cada `OrderItem.handle` → `variant_id` (paralelizado).
  2. Find-or-create customer por email sintetizado.
  3. Discover shipping_option_id (env override → smart filter → fallback).
  4. Build draft order payload con idempotency_key.
  5. POST /admin/draft-orders → unwrap → return `OrderRegistrationResult`.

Toda la secuencia esta envuelta en `asyncio.wait_for(45s)` para garantizar
que el activity NO se cuelgue.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any

from src.platform.medusa.client import HttpMedusaClient, MedusaAPIError
from src.platform.medusa.service import MedusaProductService
from src.platform.medusa.settings import MedusaSettings
from src.platform.orders import variant_matching
from src.platform.orders.port import (
    OrderItem,
    OrderRegistrationResult,
    OrderShipping,
)

log = logging.getLogger(__name__)


# Default mid-attempt cap. Si Medusa no devuelve shipping options (porque el
# operador no las creo), el adapter aborta con un error claro en vez de
# fallar 422 cuando POST /admin/draft-orders rechace `shipping_methods=[]`.
_MAX_SHIPPING_OPTIONS_LOOKUP = 50

# Premortem C1: timeout total para evitar que el activity execute_tool se
# cuelgue indefinidamente. Suma de worst-case: 4 round-trips × 12s (tenacity
# 3 attempts con exp backoff max 4s) ≈ 48s. Damos 45s con margen.
_REGISTER_ORDER_TIMEOUT_S = 45.0

# Premortem B1: bucket size para el idempotency key. Mismo bucket = misma
# orden (deduplicación a nivel adapter). 600s = 10min — si el LLM o el
# workflow retry llega 2x en esa ventana, mismo idempotency_key.
_IDEMPOTENCY_BUCKET_S = 600

# Resiliencia orden↔tag (fix integridad): cuántos draft orders recientes
# inspeccionar en el pre-check de idempotencia antes de crear uno nuevo.
# El duplicado (retry de Temporal) ocurre en segundos → queda al tope del
# orden `-created_at`. 50 cubre minutos de actividad en baja frecuencia
# (agencia WhatsApp). Si el volumen creciera, migrar a filtro server-side
# por `metadata` cuando Medusa lo soporte de forma estable.
_IDEMPOTENCY_LOOKBACK_DRAFTS = 50

# Premortem H1: keywords que indican "envio normal/estandar". Si una shipping
# option matchea, la preferimos sobre las que NO matchean (e.g. "Recogida en
# tienda" tipicamente NO incluye "envio" o "shipping").
_SHIPPING_OPTION_KEYWORDS_PREFERRED = (
    "envio", "envío", "shipping", "estandar", "estándar", "standard",
    "domicilio", "delivery",
)


class MedusaOrderConfigError(Exception):
    """Raised when MedusaSettings.region_id / sales_channel_id is missing.

    El composition (`get_order_registration_port`) chequea esto antes de
    instanciar el adapter y, si falta, falla rapido con un mensaje claro.
    Si por accidente alguien instancia el adapter con settings incompletos,
    `register_order` devuelve `success=False` y el LLM escala.
    """


class MedusaOrderRegistration:
    """Adapter live del OrderRegistrationPort contra Medusa v2 Draft Orders."""

    def __init__(
        self,
        client: HttpMedusaClient,
        product_service: MedusaProductService,
        settings: MedusaSettings,
    ) -> None:
        if not settings.region_id or not settings.sales_channel_id:
            raise MedusaOrderConfigError(
                "MedusaOrderRegistration requires MEDUSA_REGION_ID + "
                "MEDUSA_SALES_CHANNEL_ID set in env. Configure them in "
                "Medusa Admin → Settings → Regions / Sales Channels."
            )
        # Premortem H2: defensive currency check. Si alguien cambia el env
        # a "usd" sin saber que `unit_price` se mandaria mal, log loud.
        if (settings.default_currency or "").lower() != "cop":
            log.warning(
                "MedusaOrderRegistration: default_currency=%r is NOT 'cop'. "
                "unit_price values are sent as integers in MAJOR currency "
                "units (Medusa convention). For COP that's correct (no "
                "decimals). For USD/EUR (2 decimals), the prices will be "
                "interpreted WRONG by Medusa unless the upstream pipeline "
                "is adapted (e.g. send 17000 as 170.00 for USD).",
                settings.default_currency,
            )
        self._client = client
        self._products = product_service
        self._settings = settings
        # Cache descubrimiento de shipping option — Medusa rara vez cambia,
        # mantenerlo en memoria evita queries repetidas por cada checkout.
        self._cached_shipping_option_id: str | None = (
            settings.default_shipping_option_id or None
        )

    # ------------------------------------------------------------------
    # Public — OrderRegistrationPort
    # ------------------------------------------------------------------

    async def register_order(
        self,
        *,
        session_key: str,
        items: list[OrderItem],
        shipping: OrderShipping,
        payment_method: str,
        subtotal_cop: int,
        shipping_cop: int,
        total_cop: int,
        currency: str = "COP",
        attribution: dict[str, Any] | None = None,
    ) -> OrderRegistrationResult:
        # Premortem C1: wrap ALL the work in a single wait_for to bound the
        # worst-case latency at ~45s. The activity heartbeat (every 10s in
        # `execute_tool`) keeps Temporal alive during that window. If we
        # exceed 45s, abort and return success=False — LLM escalates.
        try:
            return await asyncio.wait_for(
                self._register_order_inner(
                    session_key=session_key,
                    items=items,
                    shipping=shipping,
                    payment_method=payment_method,
                    subtotal_cop=subtotal_cop,
                    shipping_cop=shipping_cop,
                    total_cop=total_cop,
                    currency=currency,
                    attribution=attribution,
                ),
                timeout=_REGISTER_ORDER_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            log.error(
                "MedusaOrderRegistration.register_order TIMEOUT after %.1fs",
                _REGISTER_ORDER_TIMEOUT_S,
                extra={"session_key": session_key, "items_count": len(items)},
            )
            return OrderRegistrationResult(
                success=False,
                order_id=None,
                provider="medusa",
                error_detail=(
                    f"timeout: Medusa did not respond within "
                    f"{_REGISTER_ORDER_TIMEOUT_S}s. The order is NOT registered."
                ),
            )

    async def _register_order_inner(
        self,
        *,
        session_key: str,
        items: list[OrderItem],
        shipping: OrderShipping,
        payment_method: str,
        subtotal_cop: int,
        shipping_cop: int,
        total_cop: int,
        currency: str = "COP",
        attribution: dict[str, Any] | None = None,
    ) -> OrderRegistrationResult:
        log.info(
            "MedusaOrderRegistration.register_order start",
            extra={
                "session_key": session_key,
                "items_count": len(items),
                "total_cop": total_cop,
                "currency": currency,
            },
        )

        # Idempotencia (resiliencia orden↔tag): fingerprint ESTABLE del
        # contenido de la orden. Dos `register_order` con el mismo contenido
        # (retry de Temporal tras un crash, o el LLM llamando dos veces) →
        # mismo fingerprint → reusamos el draft existente en vez de crear un
        # duplicado. Incluir el fingerprint (no solo session+bucket) evita el
        # falso-positivo de deduplicar dos compras LEGÍTIMAS y distintas del
        # mismo cliente en la misma ventana de 10min.
        fingerprint = _compute_order_fingerprint(items, total_cop, payment_method)
        idempotency_bucket = int(time.time()) // _IDEMPOTENCY_BUCKET_S
        idempotency_key = f"{session_key}:{idempotency_bucket}:{fingerprint}"

        try:
            # 0) Pre-check de idempotencia (best-effort): ¿ya existe un draft
            #    con este mismo contenido? Cubre la ventana donde el intento
            #    previo creó la orden en Medusa pero el worker murió ANTES de
            #    que la activity `execute_tool` reportara completion a Temporal
            #    (→ retry). Medusa es la fuente de verdad, así que el check
            #    contra Medusa cierra esa ventana que ni el replay ni la
            #    metadata local cubren.
            existing = await self._find_existing_draft_order(
                session_key=session_key, fingerprint=fingerprint
            )
            if existing is not None:
                log.warning(
                    "MedusaOrderRegistration: idempotency hit — reusing "
                    "existing draft order instead of creating a duplicate",
                    extra={
                        "session_key": session_key,
                        "order_id": existing.get("id"),
                        "fingerprint": fingerprint,
                    },
                )
                return OrderRegistrationResult(
                    success=True,
                    order_id=str(existing["id"]),
                    provider="medusa",
                    raw_payload=_safe_dict(existing),
                    customer_id=existing.get("customer_id"),
                )

            # 1) Resolve handle → variant_id (paralelizado — Premortem E1).
            resolved_items, variant_mismatches = await self._resolve_items(items)

            # 2) Find or create customer (idempotent por email sintetico).
            customer = await self._upsert_customer(
                session_key=session_key,
                shipping=shipping,
            )

            # 3) Discover shipping_option_id (lazy con cache + smart filter).
            shipping_option_id = await self._discover_shipping_option_id()

            # 4) Build payload (incluye Premortem B1 idempotency_key +
            #    H3 variant_mismatches en metadata).
            payload = self._build_payload(
                session_key=session_key,
                customer_id=customer["id"],
                customer_email=customer["email"],
                resolved_items=resolved_items,
                shipping=shipping,
                shipping_option_id=shipping_option_id,
                shipping_cop=shipping_cop,
                payment_method=payment_method,
                subtotal_cop=subtotal_cop,
                total_cop=total_cop,
                currency_code=self._settings.default_currency,
                variant_mismatches=variant_mismatches,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                attribution=attribution,
            )

            # 5) POST /admin/draft-orders.
            draft = await self._client.create_draft_order(payload)

            log.info(
                "MedusaOrderRegistration: draft_order created",
                extra={
                    "session_key": session_key,
                    "order_id": draft.get("id"),
                    "customer_id": customer["id"],
                    "variant_mismatches": variant_mismatches,
                },
            )
            return OrderRegistrationResult(
                success=True,
                order_id=str(draft["id"]),
                provider="medusa",
                raw_payload=_safe_dict(draft),
                customer_id=customer["id"],
                items_resolved=resolved_items,
            )

        except MedusaAPIError as exc:
            log.error(
                "MedusaOrderRegistration: Medusa API rejected order",
                extra={
                    "session_key": session_key,
                    "status_code": exc.status_code,
                    "path": exc.path,
                    "body_preview": exc.body[:500],
                },
            )
            # Premortem J4-like: enrich error_detail with auth hint on 401.
            if exc.status_code == 401:
                detail = (
                    "medusa_unauthorized: HTTP 401 — el admin_token expiró "
                    f"o es inválido ({exc.path})."
                )
            else:
                detail = f"medusa_api_error: HTTP {exc.status_code} {exc.path}: {exc.body[:300]}"
            return OrderRegistrationResult(
                success=False,
                order_id=None,
                provider="medusa",
                error_detail=detail,
            )
        except Exception as exc:  # network errors, parse errors, config errors
            log.exception(
                "MedusaOrderRegistration: unexpected failure",
                extra={"session_key": session_key},
            )
            return OrderRegistrationResult(
                success=False,
                order_id=None,
                provider="medusa",
                error_detail=f"unexpected_error: {type(exc).__name__}: {exc}",
            )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _resolve_items(
        self, items: list[OrderItem]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Resolve each `handle` → Medusa `variant_id` (+ title/sku).

        Premortem E1: paraleliza N round-trips con `asyncio.gather`.

        Premortem H3: cuando un `variant_label` NO matchea ninguna variante
        en Medusa, NO levantamos error (la venta debe cerrar), pero
        devolvemos un segundo array con info del mismatch — el caller lo
        embebe en `metadata.variant_mismatches[]` para que el operador
        vea cuáles items necesitan ajuste manual.

        Un item puede resolver a MAS de una linea: si el label nombra varias
        variantes y la cantidad lo permite ("Capricornio morado, Capricornio
        verde, Sagitario azul" x3 → 2 Capricornio + 1 Sagitario), cada
        variante sale como su propia linea con el mismo `unit_price`.

        Raises `MedusaAPIError` si el product handle no existe — la venta
        no puede cerrar sin el producto, escalamos.

        Returns:
            (resolved_items, variant_mismatches)
        """
        async def lookup_one(
            it: OrderItem,
        ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
            page = await self._products.list(handle=it.handle, limit=1)
            if not page.products:
                raise MedusaAPIError(
                    status_code=404,
                    path=f"/admin/products?handle={it.handle}",
                    body=f"Product handle '{it.handle}' not found in Medusa.",
                )
            product = page.products[0]
            resolution = self._resolve_variant(product, it.variant_label, it.quantity)
            annotations: dict[str, Any] = {}
            if resolution.is_mismatch:
                annotations["variant_label_mismatch"] = True
            if resolution.mismatch_kind:
                annotations["variant_match_kind"] = resolution.mismatch_kind
            if resolution.unresolved_tokens:
                annotations["variant_unresolved_tokens"] = list(
                    resolution.unresolved_tokens
                )
                annotations["variant_unresolved_tag_kinds"] = list(
                    resolution.unresolved_tag_kinds
                )
            if len(resolution.lines) > 1:
                annotations["variant_split_from_quantity"] = it.quantity
            resolved = [
                {
                    "title": product.title,
                    "sku": line.variant.sku or product.handle,
                    "variant_id": line.variant.id,
                    "quantity": line.quantity,
                    # Medusa v2: unit_price en unidades MAYORES. COP no tiene
                    # subunidades fraccionarias, asi que mandamos el int crudo.
                    # Premortem H2: si default_currency != cop, el adapter loguea
                    # warning al init — esto sigue el contrato de Medusa.
                    "unit_price": it.unit_price_cop,
                    "metadata": {
                        "handle": it.handle,
                        **({"variant_label": it.variant_label} if it.variant_label else {}),
                        **annotations,
                    },
                }
                for line in resolution.lines
            ]
            first = resolution.lines[0].variant
            mismatch = (
                {
                    "handle": it.handle,
                    "requested_label": it.variant_label,
                    "selected_variant_id": first.id,
                    "selected_variant_title": first.title,
                    "reason": resolution.mismatch_kind,
                }
                if resolution.is_mismatch
                else None
            )
            return resolved, mismatch

        results = await asyncio.gather(*[lookup_one(it) for it in items])
        resolved = [line for lines, _ in results for line in lines]
        mismatches = [m for _, m in results if m is not None]
        return resolved, mismatches

    @staticmethod
    def _pick_variant_with_status(
        product: Any, label: str | None
    ) -> tuple[Any, bool]:
        """Variante principal para `label` + si es un mismatch real."""
        resolution = MedusaOrderRegistration._resolve_variant(product, label, 1)
        return resolution.lines[0].variant, resolution.is_mismatch

    @staticmethod
    def _resolve_variant(
        product: Any, label: str | None, quantity: int
    ) -> variant_matching.VariantResolution:
        """Resuelve `label` → línea(s) con variante, o fallback a la primera.

        Mismatch real (`is_mismatch`) sólo cuando ningún token resuelve la
        dimensión de variante (o una de ellas) — el caller lo surface al
        operador en `metadata.variant_mismatches`. Los tokens sobrantes
        (aromas/colores, que en esta tienda son tags) quedan anotados sin
        levantar la alarma. Ver `variant_matching`.
        """
        variants = product.variants or []
        if not variants:
            raise MedusaAPIError(
                status_code=404,
                path=f"/admin/products?handle={product.handle}",
                body=f"Product '{product.handle}' has no variants in Medusa.",
            )
        single = variant_matching.VariantResolution(
            lines=(variant_matching.VariantLine(variants[0], quantity),)
        )
        # Single-variant products NEVER mismatch (2026-05-26): en esta tienda
        # los "aromas" y "colores" viven como **tags** del producto — label
        # "Limoncillo, Lila" describe atributos, no una variante alternativa.
        if not label or len(variants) == 1:
            return single
        if not variant_matching.split_label(label):
            return single

        resolution = variant_matching.resolve(
            variants, getattr(product, "tags", None) or [], label, quantity
        )
        if resolution.is_mismatch:
            log.warning(
                "MedusaOrderRegistration: variant_label=%r → %s en %s "
                "(variante %r; sin resolver %r) — surfaced in "
                "draft_order.metadata.variant_mismatches.",
                label, resolution.mismatch_kind, product.handle,
                resolution.lines[0].variant.title,
                list(resolution.unresolved_tokens),
            )
        return resolution

    async def _upsert_customer(
        self,
        *,
        session_key: str,
        shipping: OrderShipping,
    ) -> dict[str, Any]:
        """Find or create a Medusa customer for this WhatsApp session.

        Strategy:
          * Email sintetizado: `wa+{session_key}@hubara.local`. Estable
            entre llamadas (mismo session = mismo customer).
          * Query `/admin/customers?email=...` primero. Si existe, reusa.
          * Si no existe, `POST /admin/customers` con first_name="Cliente
            WhatsApp" + metadata para trazabilidad.
        """
        email = _synthesize_email(session_key)

        # Try lookup first (idempotency on retry).
        try:
            page = await self._client.list_customers(email=email, limit=1)
            existing = page.get("customers", [])
            if existing:
                log.info(
                    "MedusaOrderRegistration: reusing existing customer",
                    extra={
                        "session_key": session_key,
                        "customer_id": existing[0]["id"],
                        "email": email,
                    },
                )
                return existing[0]
        except MedusaAPIError as exc:
            # 404 puede significar "endpoint disabled" en algunas configs;
            # seguimos al create. Otros errores tambien — best-effort.
            log.warning(
                "MedusaOrderRegistration: customer lookup failed (%s) — "
                "proceeding to create new.",
                exc,
                extra={"session_key": session_key, "email": email},
            )

        # Create new — Medusa puede devolver 409 si ya existe (race).
        try:
            return await self._client.create_customer(
                email=email,
                first_name="Cliente",
                last_name="WhatsApp",
                phone=shipping.phone,
                metadata={
                    "session_key": session_key,
                    "source": "hubara_whatsapp_sales",
                },
            )
        except MedusaAPIError as exc:
            if exc.status_code == 409:
                # Race: otra request creo el customer entre nuestro lookup y
                # create. Re-lookup.
                page = await self._client.list_customers(email=email, limit=1)
                if page.get("customers"):
                    return page["customers"][0]
            raise

    async def _discover_shipping_option_id(self) -> str:
        """Return the shipping_option_id to use for the draft order.

        Premortem H1: smart selection cuando hay multiples shipping options
        configuradas. Sin filtro, Medusa devuelve por `created_at` ascendente,
        que puede ser "Recogida en tienda" (la primera que creó el operador)
        — y todas las ordenes Hubara cuyo shipping_cop > 0 quedarian con
        "Recogida en tienda" (mismatch peligroso).

        Preferencia:
          1. `MEDUSA_DEFAULT_SHIPPING_OPTION_ID` env var (cacheado en __init__).
          2. Match por nombre — buscar keywords como "envio", "shipping",
             "estandar", "domicilio". Si una option matchea, la usamos.
          3. Fallback a la primera devuelta + log warning loud.

        Raises `MedusaAPIError 404` si no hay shipping options configuradas.
        """
        if self._cached_shipping_option_id:
            return self._cached_shipping_option_id

        log.info(
            "MedusaOrderRegistration: discovering shipping option",
            extra={"region_id": self._settings.region_id},
        )
        page = await self._client.list_shipping_options(
            region_id=self._settings.region_id,
            limit=_MAX_SHIPPING_OPTIONS_LOOKUP,
        )
        options = page.get("shipping_options", [])
        if not options:
            raise MedusaAPIError(
                status_code=404,
                path=f"/admin/shipping-options?region_id={self._settings.region_id}",
                body=(
                    "No shipping options configured for region. Create at "
                    "least one in Medusa Admin → Settings → Locations & "
                    "Shipping → Shipping Profile."
                ),
            )

        # Premortem H1: prefer shipping options con nombres que sugieren
        # delivery (no pickup).
        preferred = _pick_preferred_shipping_option(options)
        if preferred is not None:
            self._cached_shipping_option_id = str(preferred["id"])
            log.info(
                "MedusaOrderRegistration: selected shipping option by name match",
                extra={
                    "shipping_option_id": self._cached_shipping_option_id,
                    # NOTE: cannot use key `name` in `extra` — clashes with
                    # LogRecord.name. Use `option_name` instead.
                    "option_name": preferred.get("name"),
                    "total_options_available": len(options),
                },
            )
            return self._cached_shipping_option_id

        # No preferred match — fallback al primero pero con log loud para
        # que el operador note que debe setear MEDUSA_DEFAULT_SHIPPING_OPTION_ID.
        first = options[0]
        self._cached_shipping_option_id = str(first["id"])
        log.warning(
            "MedusaOrderRegistration: no shipping option matched preferred "
            "keywords %s. Using first option as fallback. STRONGLY recommend "
            "setting MEDUSA_DEFAULT_SHIPPING_OPTION_ID env var to avoid "
            "ambiguity (e.g. shipping orders labeled 'Recogida en tienda').",
            list(_SHIPPING_OPTION_KEYWORDS_PREFERRED),
            extra={
                "shipping_option_id": self._cached_shipping_option_id,
                "option_name": first.get("name"),
                "available_options": [
                    {"id": o.get("id"), "name": o.get("name")} for o in options[:5]
                ],
            },
        )
        return self._cached_shipping_option_id

    async def _find_existing_draft_order(
        self,
        *,
        session_key: str,
        fingerprint: str,
    ) -> dict[str, Any] | None:
        """Pre-check de idempotencia: ¿ya existe un draft con este contenido?

        Busca en los `_IDEMPOTENCY_LOOKBACK_DRAFTS` más recientes uno cuyo
        `metadata.session_key` + `metadata.order_fingerprint` coincidan. El
        match ignora el bucket de tiempo embebido en `idempotency_key` para no
        fallar cuando el retry cruza el borde de un bucket de 10min.

        Best-effort: si el listado falla (Medusa transitoriamente caído) NO
        bloqueamos la venta — devolvemos None y el caller procede a crear. El
        peor caso de proceder es un duplicado (que el operador reconcilia); el
        peor caso de bloquear sería perder una venta ya confirmada.
        """
        try:
            page = await self._client.list_draft_orders(
                limit=_IDEMPOTENCY_LOOKBACK_DRAFTS,
                order="-created_at",
                fields="id,customer_id,metadata,created_at",
            )
        except Exception as exc:  # noqa: BLE001 — best-effort, no bloquear venta
            log.warning(
                "MedusaOrderRegistration: idempotency pre-check failed "
                "(proceeding to create) — %s",
                exc,
                extra={"session_key": session_key, "fingerprint": fingerprint},
            )
            return None

        for draft in page.get("draft_orders", []):
            md = draft.get("metadata") or {}
            if (
                md.get("session_key") == session_key
                and md.get("order_fingerprint") == fingerprint
            ):
                return draft
        return None

    def _build_payload(
        self,
        *,
        session_key: str,
        customer_id: str,
        customer_email: str,
        resolved_items: list[dict[str, Any]],
        shipping: OrderShipping,
        shipping_option_id: str,
        shipping_cop: int,
        payment_method: str,
        subtotal_cop: int,
        total_cop: int,
        currency_code: str,
        variant_mismatches: list[dict[str, Any]],
        idempotency_key: str,
        fingerprint: str,
        attribution: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build the POST /admin/draft-orders payload per OpenAPI spec."""
        # shipping_address: country_code en lowercase per spec.
        # province queda vacio (Bogota no es exactamente una "province" ISO
        # 3166-2 que Medusa exija — el field es opcional en la practica).
        # Nombre de quien recibe (requisito 2026-08-31) → first/last name de
        # la direccion (visible en panel + guia de la transportadora).
        # Records legacy sin receiver_name conservan el sintetico de siempre.
        first_name, last_name = _split_receiver_name(shipping.receiver_name)
        address_metadata: dict[str, Any] = {
            "neighborhood": shipping.neighborhood,
            "session_key": session_key,
        }
        if shipping.national_id:
            address_metadata["receiver_national_id"] = shipping.national_id
        address = {
            "first_name": first_name or "Cliente",
            "last_name": last_name if first_name else "WhatsApp",
            "phone": shipping.phone,
            "address_1": shipping.address,
            "address_2": shipping.neighborhood,  # barrio va aca para que se vea en el panel
            "city": shipping.city,
            "country_code": self._settings.default_country,
            "metadata": address_metadata,
        }

        # Premortem B1 + fix idempotencia: `idempotency_key` y `fingerprint`
        # llegan ya computados desde `_register_order_inner` (el mismo par que
        # alimentó el pre-check `_find_existing_draft_order`). Embebemos AMBOS
        # en metadata: `order_fingerprint` es la llave estable por contenido
        # que el pre-check matchea (independiente del bucket de tiempo, para
        # no fallar en el borde de bucket); `idempotency_key` queda para
        # auditoría / dedup downstream.
        metadata: dict[str, Any] = {
            "session_key": session_key,
            "payment_method": payment_method,
            "subtotal_cop": subtotal_cop,
            "shipping_cop": shipping_cop,
            "total_cop": total_cop,
            "source": "hubara_whatsapp_sales",
            "idempotency_key": idempotency_key,
            "order_fingerprint": fingerprint,
        }
        # Cedula de quien recibe (opcional, requisito 2026-08-31) — tambien
        # a nivel orden para que el operador la vea sin abrir la direccion.
        if shipping.national_id:
            metadata["receiver_national_id"] = shipping.national_id
        # Premortem H3: surface variant mismatches to the operator via
        # Medusa metadata. Each entry lists requested label + selected variant.
        if variant_mismatches:
            metadata["variant_mismatches"] = variant_mismatches
        # Atribución CTWA (join venta↔campaña, 2026-07-09): el ad id del
        # referral de la sesión. Solo keys con valor — ausencia = venta directa
        # (los joins/backfill distinguen por presencia, no por null).
        if attribution:
            metadata.update({k: v for k, v in attribution.items() if v})

        return {
            "sales_channel_id": self._settings.sales_channel_id,
            "region_id": self._settings.region_id,
            "currency_code": currency_code,
            "email": customer_email,
            "customer_id": customer_id,
            "shipping_address": address,
            "billing_address": address,  # same address — agencia colombiana basica
            "items": resolved_items,
            "shipping_methods": [
                {
                    "name": "Envío estándar",
                    # Medusa v2 admin schema exige `shipping_option_id`. El nombre
                    # `option_id` (que parece natural por simetría con `option_id`
                    # de variantes) devolvió HTTP 400 en producción
                    # (run bc54cb93-52d4-45d1-b69d-24b542a759ee, 2026-05-25):
                    # `Field 'shipping_methods, 0, shipping_option_id' is required`.
                    "shipping_option_id": shipping_option_id,
                    "amount": shipping_cop,
                }
            ],
            "metadata": metadata,
            "no_notification_order": True,  # WhatsApp es el canal — no email blast
        }


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _compute_order_fingerprint(
    items: list[OrderItem], total_cop: int, payment_method: str
) -> str:
    """Hash estable y corto del contenido de la orden.

    Dos `register_order` con el MISMO contenido (retry de Temporal / doble
    llamada del LLM) producen el mismo fingerprint; una compra distinta del
    mismo cliente produce uno distinto. Esto hace seguro el pre-check de
    idempotencia: solo deduplica lo que es realmente el mismo pedido.

    Determinístico: ordenamos los items para que el orden de llegada no
    cambie el hash.
    """
    parts = sorted(
        f"{it.handle}:{it.quantity}:{it.unit_price_cop}:{it.variant_label or ''}"
        for it in items
    )
    raw = "|".join(parts) + f"|total={total_cop}|pay={payment_method}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _pick_preferred_shipping_option(
    options: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Premortem H1: choose a shipping option whose name suggests delivery.

    Iterates in the order Medusa returned them (typically `created_at` asc)
    and picks the first match. Returns None if no name matches.
    """
    for opt in options:
        name = (opt.get("name") or "").lower()
        if any(kw in name for kw in _SHIPPING_OPTION_KEYWORDS_PREFERRED):
            return opt
    return None


def _split_receiver_name(receiver_name: str) -> tuple[str, str]:
    """Divide el nombre de quien recibe en (first_name, last_name) para la
    direccion Medusa: la ULTIMA palabra es el apellido ("Ana María Pérez" →
    ("Ana María", "Pérez")). Una sola palabra → apellido vacio. Vacio →
    ("", "") y el caller cae al sintetico legacy Cliente/WhatsApp."""
    tokens = (receiver_name or "").split()
    if not tokens:
        return "", ""
    if len(tokens) == 1:
        return tokens[0], ""
    return " ".join(tokens[:-1]), tokens[-1]


def _synthesize_email(session_key: str) -> str:
    """Synthesize a stable email from session_key for guest customer creation.

    Format: `wa+{session_key}@hubara.local`. The `+tag` form is standard
    plus-addressing; Medusa stores it as-is and we can find-or-create by
    exact email match.

    `session_key` is already a `wa_57311...` shape sanitized by the
    webhook, so it's safe to embed.
    """
    return f"wa+{session_key}@hubara.local"


def _safe_dict(value: Any) -> dict[str, Any]:
    """Best-effort: cast a Medusa response dict to a plain JSON-safe dict.

    Medusa returns plain dicts (`json.loads` of httpx response), so this is
    mostly a defensive cast — but Pydantic models or Decimal slots could
    leak in future iterations.
    """
    if isinstance(value, dict):
        return value
    return {"_raw": str(value)}


# Re-export: los tests y callers históricos importan el split desde acá.
_VARIANT_LABEL_SEPARATORS = variant_matching.LABEL_SEPARATORS
_split_variant_label = variant_matching.split_label
