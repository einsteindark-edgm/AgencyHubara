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

- **`src.sdk.connectorkit`** re-exporta los 9 ports + sus factories de
  composición: `OrderQueryPort`/`OrderCommandPort`/`OrderRegistrationPort`
  (commerce), `CatalogPort`/`CheckoutVerificationPort`, `MetaCatalogPort`,
  `AudioTranscriptionPort`, `ImageVisionPort`, `CustomerScoringPort` — y el
  nuevo **`AttributionReadPort`**.
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

## Reglas al agregar un port (regla de oro del kit)

Port nuevo ⇒ en el MISMO PR: el `Protocol` + su factory + su **fake** + su
**contract suite** (parametrizada fake/real) + re-export en
`connectorkit/__init__.py` + fila en este doc. Ningún port sin fake; ningún
adapter sin suite.
