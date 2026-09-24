# 07 · ConnectorKit — ports, fakes, atribución y el ratchet P-31

> Fase F-SDK-4 · Fuente: `hubara_agency/src/sdk/connectorkit/` + `src/platform/attribution.py` · Gates: P-31 + contract tests del port

## Qué problema soluciona

Los sistemas externos (Medusa = commerce, Meta = catálogo/mensajería/ads)
entraban al código de dos maneras desparejas: ports `typing.Protocol` bien
hechos (¡los 9 ya existían!) PERO con sus adapters Medusa mezclados en el
mismo paquete, y plugins tocando módulos de vendor directo (12 imports
medidos). Cambiar de vendor era cirugía. El kit formaliza el lado *driven*
del hexágono: **plugins consumen contratos; el vendor es un detalle de
deployment.**

## Cómo funciona

- **`src.sdk.connectorkit`** re-exporta los 10 ports + sus factories de
  composición: `OrderQueryPort`/`OrderCommandPort`/`OrderRegistrationPort`
  (commerce), `CatalogPort`/`CheckoutVerificationPort`, `MetaCatalogPort`,
  `AudioTranscriptionPort`, `ImageVisionPort`, `CustomerScoringPort`,
  `AttributionReadPort` — y el nuevo **`WebCartReaderPort`**.
- **`WebCartReaderPort`** (HU web-cart hot lead, `src/platform/carts/`):
  lee un carrito de la **Store API** de Medusa v2 (`GET /store/carts/{id}`
  con `x-publishable-api-key` — env `MEDUSA_PUBLISHABLE_API_KEY`; sin ella
  la factory `get_web_cart_reader()` cae a `NullWebCartReader` y todo cart
  ref degrada al flujo conversacional). Semántica post-premortem: `None`
  = cart **VERIFICADO** como inexistente (404 real); 401/403 lanza
  `WebCartAuthError` (key rotada ≠ cart fantasma); el Null reader lanza
  `WebCartUnavailableError` (no verificó nada); errores de transporte
  PROPAGAN (regla 4) — el caller (ingest de sales) degrada con timeout
  corto. DTOs `WebCartSnapshot`/`WebCartItem`, errores y fake oficial
  `FakeWebCartReader` viajan con el port; contract suite en
  `tests/platform/carts/test_web_cart_reader.py`. Los errores del contrato
  de `CatalogPort` (`CatalogUnavailableError`/`ProductNotFoundError`)
  también se exportan acá — los consumers distinguen "no existe" de
  "catálogo caído".
- **Atribución como read model de plataforma** (`src/platform/attribution.py`):
  el ingest de WhatsApp es el ÚNICO writer (origin/last_touch/
  referral_snapshot en el metadata); los readers (ads hoy, **CAPI mañana** —
  mismo `ctwa_clid`) consumen `scan_sessions()` del port. El descubrimiento
  (glob `wa_*` + pre-filtro mtime superset + parse tolerante) migró DESDE el
  plugin ads — que ya no conoce el layout del vault para descubrir sesiones.
  Sus 71 tests de agregación pasan INTACTOS (firmas públicas preservadas).
- **Fake oficial**: `InMemoryAttributionStore` — misma semántica superset que
  el adapter real, verificado por la **contract suite**
  (`tests/platform/test_attribution_store.py`) que corre parametrizada
  contra AMBOS (si el fake y el real divergen, lo caza CI).
- **P-31 (ratchet)**: los imports de vendor en plugins quedaron CONGELADOS
  (12 entradas: `src.platform.medusa*`, `src.platform.meta_catalog*`,
  `src.platform.*.medusa_*`) en `p31_vendor_import_allowlist.txt` — igualdad
  exacta bidireccional, solo achica.

- **Checkout live + helpers de catálogo (D1.2 del plugin `mba`)**:
  `get_checkout_verification_port()` compone el `CheckoutVerificationPort`
  real (`MedusaCheckoutVerification` sobre el snapshot + Medusa live; antes
  solo el worker de sales lo armaba a mano desde módulos vendor congelados
  por P-31). Requiere `MEDUSA_BASE_URL`; sin config lanza en composición —
  el consumidor decide degradar (mba responde `catalog_unavailable`).
  Viajan con el port `CheckoutItem` (entrada de `verify_items`) y los
  helpers puros del catálogo `parse_variant_tags` (listas cerradas de
  aromas/colores desde los tags), `parse_variant_colors` (mapa opción→color
  desde `metadata.colores`) y `deslugify` (slug→label), para que un plugin
  arme envelopes de producto sin importar `src.platform.catalog` (P-28).

## Cómo se usa

```python
# Un plugin que necesita pedidos/catálogo/atribución:
from src.sdk.connectorkit import (
    OrderQueryPort, get_order_query_port,        # commerce (Medusa detrás)
    FilesystemAttributionStore,                  # atribución CTWA
)

port = get_order_query_port()                    # el deployment decide el vendor
sessions = FilesystemAttributionStore(vault).scan_sessions(since_ms=window_start)

# En tests — SIN red, SIN credenciales, SIN env dummies:
from src.sdk.connectorkit import InMemoryAttributionStore
store = InMemoryAttributionStore([AttributionSession("wa_57300...", tmp, meta)])
```

## Qué queda anotado para F-SDK-4b (no "descubrirlo")

1. **Mudanza física**: los adapters Medusa (`platform/orders/medusa_order*.py`
   ~110K, `platform/medusa/client.py`, `platform/catalog/medusa_checkout.py`)
   → `platform/connectors/medusa/` con `acl.py`; Meta (`meta_catalog`,
   whatsapp, CAPI) → `connectors/meta/`. P-31 impide que el acople crezca
   mientras tanto.
2. **Binding config-driven** (`CONNECTOR_ORDERS=<vendor>`) cuando exista el
   segundo vendor de un port — alinear con los commerce profiles del plan
   multi-tenant.
3. **`AttributionSession` expone `session_dir`** (los consumidores derivan
   señales del history JSONL — conteo lazy). Se estrecha a métodos
   (`message_count()`) cuando aterrice el segundo consumidor (CAPI).
4. **`HttpConnectorBase`** (timeouts honestos L-1, idempotencia
   fingerprint+pre-check, caches L-2) al mover el primer adapter HTTP.

## Meta: Graph API central + Conversions API (outbox)

Auditoría CAPI 2026-09-08. Dos símbolos nuevos en el kit, ambos stdlib-puros
al importar (lazy como el resto):

| Símbolo | Para qué |
|---|---|
| `graph_url(*segments, version=None)` · `META_GRAPH_API_VERSION` · `META_GRAPH_BASE_URL` | **Única** fuente de host + versión de la Graph API (`src/platform/meta/graph.py`). Ningún plugin ni módulo de platform escribe `graph.facebook.com` ni una versión propia — la guarda `tests/platform/test_meta_graph_central.py` lo impide. Override por entorno `META_GRAPH_API_VERSION` (rollback sin rebuild). |
| `enqueue_capi_event(metadata, event_name=…, session_id=…, source=…, now_ms=…, episode_id=…, order_id=…, value=…, currency=…)` | Encola un evento de Meta Conversions API en `metadata["capi_outbox"]` (puro, idempotente por `event_id` estable, no-op en sesiones sin `ctwa_clid`). Vocabulario: `CAPI_EVENT_NAMES` (los 14 de business messaging). |
| `flush_capi_outbox(session_id)` · `schedule_capi_flush(session_id)` | El ÚNICO emisor hacia Meta (`src/platform/whatsapp/capi_outbox.py`): guardas, POST, persistencia de resultado (incluidos skips), política L-1 (5xx/connect → pendiente; read-timeout → `unknown`, nunca se reenvía: Meta NO deduplica). `schedule_*` es la versión fire-and-forget para handlers HTTP. |
| `has_ctwa_attribution(metadata)` | ¿La sesión vino de un anuncio CTWA con clid? |

Productores hoy: tags (`QualifiedLead`/`Purchase`), `register_order`
(`OrderCreated`), flush de UI intents (`ViewContent`/`AddToCart`/
`InitiateCheckout`), TIMEOUT y watchdog (`CartAbandoned`), confirmación /
cancelación humana (`Purchase`/`OrderCanceled`), etapas del pedido
(`OrderShipped`/`OrderDelivered`/`OrderCanceled`). Flush durable: activity
`flush_capi_outbox_activity` tras cada turno de Sales + dentro de las
activities del watchdog y de `emit_order_stage`.

## OrderFacts: los datos de un pedido, una sola lectura para todo el dashboard

> Fuente: `src/platform/orders/facts.py` · Contract suite: `tests/platform/orders/test_order_facts.py` · Guarda: `tests/plugins/test_order_facts_readers_guard.py`

**Qué soluciona.** Pedido #31 (2026-09-17): se editó el producto (y el total)
de una orden en Medusa. Orders mostró el total nuevo y Ads el viejo, porque Ads
sumaba `episode.order_total_cop`, una copia congelada en el chat. Dos lecturas
del mismo dato dan dos números. Regla: **total, estado de pago, etapa, cliente
y moneda de un pedido se leen de `OrderFacts`**. El vault guarda el *vínculo*
(qué conversación o anuncio trajo qué `order_id`), nunca el valor.

**Cómo funciona.**

- *Un writer:* `get_order_query_port()` (el port que sirve la vista Orders)
  viene envuelto en `RecordingOrderQuery`. Cada orden que lista o lee queda
  grabada en el `OrderFactsStore` de `get_order_facts_port()`, así que Orders
  y los demás lectores ven el mismo valor.
- *N readers:* `await get_order_facts_port().get_facts(ids)` devuelve un
  `OrderFactsSnapshot`.
  - Lo que falta en el store se busca en Medusa, paginando hasta encontrarlo
    (tope de 20 páginas de 100).
  - Lo vencido (TTL `ORDER_FACTS_TTL_S`, default 60 s) se sirve al instante y
    se refresca en segundo plano.
  - Lecturas concurrentes comparten un solo fetch.
- *Invalidación:* el store escucha el bus del dashboard
  (`DashboardEventBus.add_listener`). Todo evento `orders` (confirmar o
  reversar pago, cambiar etapa, cancelar…) marca los valores como sucios, y el
  próximo read vuelve a Medusa. Lo editado directo en Medusa Admin aparece al
  vencer el TTL.
- *Degradación honesta:* si Medusa no responde, se sirve el último valor con
  `stale=True`. Un id nunca visto queda en `unresolved` y el lector usa su
  copia congelada. `snapshot.revenue_cop(order_id, frozen_total=...)`
  encapsula la regla de "venta cerrada": pagado, no cancelado y no de prueba,
  con el total vivo.
- *Pedido de prueba (2026-09-21):* el operador lo marca en el inspector de
  Órdenes (`PATCH /api/orders/orders/{id}/test-order`). La marca vive **solo**
  en la metadata de Medusa (`hubara_test_order`) y `OrderFacts.is_test` la
  espeja: `counts_as_revenue` da False, `ads/sales_join` lo excluye, los
  eventos CAPI de etapa y el `Purchase` de confirmar pago no salen, y al marcar
  se descartan del outbox del chat los eventos de ese pedido aún no enviados
  (los ya enviados Meta no los retracta). Limitación conocida: con Medusa
  caído, un id `unresolved` usa la copia congelada del vault, que no conoce
  la marca.
- *Fake oficial:* `InMemoryOrderFacts` (`available=False` simula Medusa
  caído). La contract suite corre contra ambos.

**Cómo se usa** (endpoint sync de un plugin):

```python
from anyio import from_thread
from src.sdk.connectorkit import OrderFactsSnapshot, get_order_facts_port

facts = from_thread.run(get_order_facts_port().get_facts, order_ids)
revenue = facts.revenue_cop(order_id, frozen_total=episode.get("order_total_cop"))
return {..., "orders_stale": facts.stale}  # la UI avisa "valores sin actualizar"
```

**Lectores migrados:** `ads` (campañas, segmentos, anuncios, conversaciones),
`marketing` (`campaign_stats`), `customer_scoring` (LTV / frecuencia / última
compra, y el endpoint dejó de fetchear Medusa por su cuenta), el **inbox de
chats** (botón "Confirmar pago"), el **watchdog de remarketing** (etapa del
template + monto real) `shared/funnel.is_open_cart` y los **eventos CAPI de etapa** (`orders/.../emit_stage.py`: OrderShipped/Delivered/Canceled con el total vivo; `registered_order` solo si Medusa no responde). La guarda AST cubre ads
y marketing; en el resto el reemplazo es de TAG por estado del pedido, con el
camino viejo como respaldo cuando Medusa no responde.

**Pendientes (siguen leyendo copias del vault):**

| Lector | Dato que duplica | Nota |
|---|---|---|
| `platform/whatsapp/capi_activity.py` + `shared/funnel.enqueue_capi_for_tag` | total y moneda del `Purchase` a Meta | el valor sale de `registered_order`; el lugar honesto para corregirlo es el flush del outbox (un solo punto, justo antes de enviar) |
| `sales/use_cases/episode_lifecycle.py` (cierre por inactividad → `CartAbandoned`) | "pagado" por etiqueta | corre en el ingest de CADA mensaje: consultar Medusa ahí agrega latencia al bot. El watchdog (ya migrado) suele emitir primero y el outbox dedupea |
| `plugins/ads/classification.py`, `marketing/domain/campaigns.py::segment_for_metadata` | estado "ganado" / segmento por etiqueta | es el estado de la CONVERSACIÓN (a quién le escribo), no el del pedido — migrar solo si el operador quiere audiencias por pago real |
| `chats/shared/purchase_signals.py::has_purchase_confirmation` | "el cliente dijo que sí" | señal conversacional, no estado del pedido |
| `plugins/reengagement/.../build_snapshot.py` | `has_registered_order` | |

Los evals de `sales_eval` leen el tag a propósito: evalúan lo que hizo el bot,
no el estado del pedido.

## Cupones: `PromotionsPort` (Medusa Admin → Promotions)

Los descuentos del bot de ventas NO se inventan ni se negocian: viven en
Medusa como **promociones con código** (Admin → Promotions: código, tipo
`percentage`/`fixed`, productos/variantes/colección objetivo, mínimo de
compra, campaña con vigencia y presupuesto). El kit expone:

| Símbolo | Qué es |
|---|---|
| `PromotionsPort` (`list_active()`, `get_by_code()`) | contrato; adapter real `platform/promotions/medusa.py` (`GET /admin/promotions`, cache 60 s), `NullPromotionsPort` sin Medusa |
| `PromotionDTO` | SNAPSHOT JSON-safe de la promoción (se persiste en `episodes[-1].applied_coupon` cuando el cliente aplica un cupón) |
| `resolve_coupon(code, promotions, now_ms)` | valida forma (`COUPON_CODE_RE = [A-Z0-9]{3,20}`, sin `_`: colisión con el guard anti-leak), existencia, estado, vigencia y presupuesto |
| `compute_discount(promo, items: DiscountLineItem[], shipping_cop)` | el MONTO (COP entero) — percentage / fixed across / fixed each con `max_quantity` / envío; `buyget` = unsupported |
| `FakePromotionsPort` | doble oficial; contract suite en `tests/platform/promotions/test_promotions_port_contract.py` |
| `get_promotions_port()` | factory (Medusa si `MEDUSA_BASE_URL`, si no Null) |

Quién lo usa: tools `list_promotions` / `apply_coupon` del sales worker
(validan y persisten el snapshot), `present_order_confirmation` /
`register_order` / el botón "Crear pedido" del dashboard (recomputan el
descuento desde el snapshot con los precios del catálogo — L-19: el LLM
solo repite el total que devuelve el envelope), y `register_order` manda
`promo_codes: [code]` al draft de Medusa (así `discount_total`/`total` reales
llegan a OrderFacts y al panel de Órdenes) más `metadata.coupon_code` /
`discount_cop` como auditoría. Marketing ofrece esos mismos códigos en el
builder (`GET /api/marketing/promotions`).

## Central de cupones: comandos, cupo por unidad y ventas derivadas

La central de Marketing (Marketing → Cupones, `CUPONES_PLAN.md`) ESCRIBE en
Medusa y le pone a cada cupón un **cupo por unidad** (producto + color +
aroma + unidades). Lecturas y comandos van en ports separados: el bot sigue
leyendo con `PromotionsPort`; los comandos solo los usa la API.

| Símbolo | Qué es |
|---|---|
| `CouponSpec`, `parse_coupon_spec`, `CouponSpecError(field, message)` | el formulario validado: código `[A-Z0-9]{3,14}`, porcentaje entero 1–100, productos o `"all"`, fechas en días de Bogotá con el "hasta" **inclusivo** |
| `CouponView`, `coupon_view_from_medusa(raw, now)` | lo que hay en Medusa: estado (`draft/scheduled/active/paused/expired`) y si es **gestionable** (porcentaje sobre productos, a lo sumo una regla `items.product.id in`, sin condiciones de compra, con campaña). Lo demás: solo lectura con `unmanageable_reason` |
| `PromotionsAdminPort` (`list_coupons`, `get_coupon`, `create_coupon`, `update_coupon`, `set_status`, `delete_coupon`) | adapter real `platform/promotions/admin.py`: crear = UNA llamada con la campaña en línea; editar = solo lo que cambió (promoción, regla de productos, campaña), pasos idempotentes; toda escritura limpia el cache del `PromotionsPort` del proceso |
| errores `CouponCodeTakenError` / `CouponRejectedError` / `CouponNotFoundError` / `CouponNotManageableError` / `CouponDeleteRefusedError` / `CouponPartialUpdateError(step, view)` | el vendor no cruza el port; `PromotionsUnavailableError` si Medusa no responde |
| `FakePromotionsAdmin` | doble oficial = el adapter real sobre Medusa en memoria (`.medusa.writes` para asertar que NO se escribió); contract suite fake/real (respx sobre el mismo simulador) en `tests/platform/promotions/test_promotions_admin_contract.py` |
| `PromoUnitQuota`, `validate_quota_rows`, `quota_product`, `quota_id_for` | las filas del cupo, validadas contra las listas cerradas del producto (tags `Color:`/`Aroma:`); el id es el de la combinación (re-guardar no pierde vendidas) |
| `quota_statuses`, `allocate_units`, `unit_discount_cop`, `quota_exhausted` | dominio puro: cuántas quedan, el reparto de un pedido (parcial si no alcanza; falla cerrada sin color/aroma), el descuento por unidad redondeado igual que el pedido |
| `PromoQuotaStore` / `QuotaSheet` / `FakePromoQuotaStore` / `get_promo_quota_store()` | el cupo vive en el vault (`_promotions/quotas/<promotion_id>.json`, flock): la API de Medusa no deja escribir metadata en promociones |
| `sold_units_by_quota`, `coupon_results`, `quota_board`, `get_coupon_sales_reader()` | las VENDIDAS se derivan de los pedidos y drafts de Medusa (`items[].metadata.coupon_quota_id`, más las claves `coupon_code`/`discount_unit_cop` de la línea con descuento); cancelados, de prueba y duplicados no cuentan. Sin Medusa: `PromotionsUnavailableError` (falla cerrada) |
| `CouponAuditPort` / `FakeCouponAuditLog` / `get_coupon_audit_log()` | registro de cambios append-only (`_promotions/audit.jsonl`) con el actor de `castkit.current_actor` |
| `register_under_quota`, `QuotaChanged`, `QuotaLockTimeout`, `get_quota_lock()` | candado por código (flock async) que relee lo vendido y recalcula el reparto antes de registrar: nunca se vende dos veces la última unidad |

Quién lo usa: `src/plugins/marketing/api/coupons.py` (la central). El bot de
ventas y "Crear pedido" consumen el cupo en la Fase 5/6 del plan.

## Reglas al agregar un port (regla de oro del kit)

Port nuevo ⇒ en el MISMO PR: el `Protocol` + su factory + su **fake** + su
**contract suite** (parametrizada fake/real) + re-export en
`connectorkit/__init__.py` + fila en este doc. Ningún port sin fake; ningún
adapter sin suite.
