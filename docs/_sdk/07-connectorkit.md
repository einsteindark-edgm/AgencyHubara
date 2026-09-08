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

## Reglas al agregar un port (regla de oro del kit)

Port nuevo ⇒ en el MISMO PR: el `Protocol` + su factory + su **fake** + su
**contract suite** (parametrizada fake/real) + re-export en
`connectorkit/__init__.py` + fila en este doc. Ningún port sin fake; ningún
adapter sin suite.
