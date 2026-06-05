# Multi-Tenant Commerce Architecture — AgencyHubara

> **Propósito.** Contrato de diseño para hacer multi-tenant el **sistema de
> órdenes + catálogo** (hoy acoplado a una sola config Medusa) y para que cada
> "combinación" de Medusa (cómo se guardan productos, cómo se resuelven
> variantes, cómo cambian de estado las órdenes) sea **declarativa por config**,
> no hardcodeada en los adaptadores.
>
> **Audiencia.** El operador + cualquier agente IA del pipeline Archon que
> implemente las fases de §11. Si dudás de una decisión, está justificada en §4
> (Medusa) y §13 (rechazadas).
>
> **Relación con docs existentes.** Extiende [PLUGIN_ARCHITECTURE.md](PLUGIN_ARCHITECTURE.md)
> (sistema de plugins) hacia el eje *comercio*. Honra la decisión firme **D1**
> de ese doc ("monorepo + N stacks Terraform por tenant"). Se formaliza como
> decisión en [ADR-2026-06-05-multi-tenant-commerce-architecture.md](ADR-2026-06-05-multi-tenant-commerce-architecture.md).
>
> **Estado.** PROPUESTO. Ningún código cambia con la sola adopción de este doc.

---

## §1. Resumen ejecutivo

AgencyHubara va a operar como **multi-tenant**: cada empresa cliente puede tener
su propia configuración de Medusa y, posiblemente, su propio backend Medusa.
Hoy el sistema es **single-tenant por proceso**: la config Medusa vive en env
vars (`MEDUSA_*`) leídas una vez, y los `@lru_cache(maxsize=1)` del composition
root atan un proceso a un tenant.

El requerimiento se descompone en **dos ejes ortogonales** que conviene NO
mezclar porque tienen soluciones distintas:

- **Eje A — Tenancy / Routing**: *a qué Medusa apunto y con qué credenciales.*
  Solución: **modelo silo** (una Medusa + una DB por tenant) + **bundle de
  config por tenant**. Confirmado por investigación (§4): Medusa v2 **no tiene
  multi-tenancy nativo** y comparte la tabla `customers` a nivel instancia, así
  que pooled-single-instance NO aísla empresas. El silo es además la decisión D1
  ya tomada. **Cero cambio al código que corre hoy** — es packaging/deployment.

- **Eje B — Combinación / Comportamiento**: *cómo se mapean productos,
  variantes, estados y órdenes.* Hoy está hardcodeado en los adaptadores Medusa.
  Solución: **commerce profile** (la combinación como YAML declarativo) +
  **registry de 6 estrategias** (espeja el patrón ya existente de
  `tool_extensions.py`). El comportamiento actual se **extrae byte-idéntico** a
  las primeras estrategias registradas, gateado por los tests existentes.

La invariante que gobierna todo: **agregar un tenant, un profile o una
estrategia NUNCA edita archivos que ya están en producción** (§3, §10).

---

## §2. Decisiones firmes

Cerradas con la adopción de este doc. No reabrir sin instrucción explícita.

| # | Decisión | Razón |
|---|---|---|
| **C1** | **Eje A = modelo silo**: 1 Medusa + 1 DB por tenant. | Único modelo con aislamiento real entre empresas (Medusa comparte customers; §4). = D1. |
| **C2** | **Hubara-app sigue single-tenant por proceso.** Un deployment de Hubara por tenant, apuntando a su Medusa. | El silo lo permite; preserva `@lru_cache(1)`, R-STATELESS y los bindings module-level del sales worker sin tocarlos. |
| **C3** | **Routing por config, no por código**: `infra/tenants/<id>/` declara plugins habilitados + ref a Medusa + profile. | El env var `COMMERCE_PROFILE` + el secret bundle son el único "switch" por deployment. |
| **C4** | **Eje B = commerce profile (config) + strategy registry (algoritmo).** | Lo que difiere como *valor* es config; lo que difiere como *algoritmo* es una clase nueva en archivo nuevo. |
| **C5** | **El comportamiento actual se captura como `hubara-co-default`** y se extrae byte-idéntico a estrategias nombradas. | El profile default reproduce hoy exactamente → backward-compatible por construcción. |
| **C6** | **Los puertos (`OrderRegistrationPort`, `OrderQueryPort`, `OrderCommandPort`, `CatalogPort`) NO cambian su firma.** | Los call-sites del inventario (§12) siguen igual; el cambio es interno a los adaptadores + composition. |
| **C7** | **El onboarding de una Medusa nueva = config (`medusa-config.ts`/env) + `medusa-seed.yaml` declarativo reproducido vía Admin API.** | En Medusa, regiones/sales-channels/shipping/currency/catálogo son runtime-only, no declarables en config (§4.5). |
| **C8** | **El registry de estrategias es append-only / auto-discovered**, igual que el auto-discovery de plugins (R1) y `register_tool_extension` (idempotente por key). | Garantiza "estrategia nueva = archivo nuevo, cero ediciones". |

---

## §3. Las tres reglas no negociables (aislamiento)

Si alguien siente la tentación de violarlas, **el diseño está mal — no la regla.**

### R-ISO-1 — Tenant nuevo = solo archivos nuevos

Agregar un tenant crea `infra/tenants/<new>/` y (si hace falta) un
`commerce_profiles/<new>.yaml`. **NUNCA edita el bundle de otro tenant, ni un
profile existente, ni código.** Los bundles son disjuntos por construcción.

### R-ISO-2 — Estrategia nueva = archivo nuevo + auto-registro

Una combinación que necesita un *algoritmo* nuevo agrega un archivo en
`platform/commerce/strategies/<kind>/` que se auto-registra al import (§7.3).
**NUNCA edita una estrategia existente.** Mismo espíritu que R3 de
PLUGIN_ARCHITECTURE.md (carpeta autocontenida) y que `register_tool_extension`.

### R-ISO-3 — El default reproduce producción

Un deployment sin `COMMERCE_PROFILE` cae a `hubara-co-default`, que es
**byte-idéntico** al comportamiento de hoy. Cualquier extracción a estrategia se
valida con la suite completa (`tests/plugins/`, functional E2E, architecture
gates) ANTES de mergear. Si un test cambia de verde a rojo, la extracción está
mal — no el test.

---

## §4. Medusa multi-tenancy — hallazgos y decisión (Eje A)

Investigación 2026-06 sobre Medusa v2 (versión actual). Distinción v1/v2
explícita; se marca dónde los docs están en silencio.

### §4.1 Medusa v2 NO es multi-tenant nativo

Cita textual de los docs oficiales del Store Module: *"While Medusa doesn't
natively support multi-tenancy, the Store Module allows you to create and manage
multiple stores within a single instance... You can then build customizations to
link products, customers, orders..."* El "multi-store" es **un data model sin
aislamiento automático** — un hook, no un feature. Las feature-requests
[#11671](https://github.com/medusajs/medusa/discussions/11671) y
[#12304](https://github.com/medusajs/medusa/discussions/12304) están abiertas
**sin compromiso del core team**.

### §4.2 Qué está aislado vs compartido (el crux)

- **`customers` son COMPARTIDOS** a nivel instancia / entre sales channels. La
  guía canónica multivendor define links `product-store`/`order-store` pero
  **no** `customer-store`. → Para empresas que NO deben verse, una sola
  instancia es insegura.
- **`products` y `orders` no se auto-aíslan** — solo quedan por-store si vos
  agregás los module links y filtrás cada query.
- **Sales channels** aíslan *disponibilidad de producto* + carts + routing de
  inventario, pero los docs **no documentan** aislamiento de orders/customers
  por channel (gap explícito).
- **Admin API key NO es tenant-scoped** — ve toda la instancia.

### §4.3 Patrones de deployment — ventajas y desventajas

| Patrón | Aislamiento | Blast radius | Costo (5–50) | Upgrades | ¿A favor del grain? |
|---|---|---|---|---|---|
| **(a) Instancia + DB por tenant (silo)** | **Físico, no se puede filtrar mal** | Por-tenant | Alto, bin-packable | Limpio, por-tenant, rollback aislado | **Sí** (modelo "Multi-Tenant Platform" oficial) |
| (b) Schema-per-tenant | Fuerte-ish (app compartida) | App compartida | Medio | Doloroso, no soportado | No |
| (c) Shared schema + RLS (`tenant_id` en 44+ tablas) | DB-enforced *si es perfecto* | Máximo | Bajo | Riesgoso: patch del framework eterno; raw SQL/singletons/jobs bypassean RLS | No (el que más pelea) |
| (d) Sales-channel por tenant | Débil (customers compartidos) | Máximo | Bajo | Trivial | Mecánicamente sí, **semánticamente NO** |

### §4.4 Decisión: silo (a)

Para una agencia con ~5–50 merchants chicos, **(a) gana**: es el único con
aislamiento que no se puede filtrar mal (customers compartidos = deal-breaker en
single-instance); trabaja a favor del grain (cada tenant es una Medusa stock, sin
patch → upgrades en el happy path); y a 5–50 tenants el costo es manejable
**bin-packeando containers + compartiendo un *server* Postgres (1 DB/tenant) + 1
Redis (`redisPrefix`)**, lo que baja la factura sin perder separación lógica.

Trade-off aceptado: hay que **construir un control-plane** (registro de tenants +
provisioner + router). Medusa explícitamente dice que eso es tu trabajo. Para
5–50 tenants es automatización finita, no un fork del framework.

**Reconsiderar (c) RLS solo si** escalás a cientos/miles de tenants chicos donde
N silos dejan de bin-packear económicamente — ahí se asume el patch del framework
a sabiendas.

### §4.5 Superficie de config por deployment (config-time vs runtime)

Split duro, decisivo para onboarding declarativo:

- **Config-time** (`medusa-config.ts` + env): `databaseUrl`, `redisUrl`/`redisPrefix`,
  CORS/JWT/cookie secrets, `workerMode`, y **providers** (payment/fulfillment/
  notification/file/cache/event-bus/workflow) como module providers.
- **Runtime-only** (Admin API, NO declarables en config): **regions, sales
  channels, shipping options + pricing, `supported_currencies`, stock
  locations, productos, customers, API keys.**

→ "Medusa nueva por config" = `medusa-config.ts`/env **+ un `medusa-seed.yaml`
declarativo** que el provisioner reproduce vía Admin API **después** de
`medusa db:migrate`. El flujo automatizado de provisioning NO está documentado
por Medusa (gap) — lo construimos nosotros (§5.3).

### §4.6 Footprint por instancia

Una Medusa v2 productiva = server + worker (`MEDUSA_WORKER_MODE`; colapsables a
`shared` en setups chicos) + Postgres + Redis (Cache + Event Bus + Workflow
Engine). Único número oficial: **≥ 2 GB RAM** para app + Admin. Sizing de
DB/Redis no publicado (gap).

> Fuentes: [store](https://docs.medusajs.com/resources/commerce-modules/store) ·
> [sales-channel](https://docs.medusajs.com/resources/commerce-modules/sales-channel) ·
> [multi-region recipe](https://docs.medusajs.com/resources/recipes/multi-region-store) ·
> [medusa-config](https://docs.medusajs.com/learn/configurations/medusa-config) ·
> [deployment](https://docs.medusajs.com/learn/deployment) ·
> [v1→v2](https://docs.medusajs.com/learn/introduction/from-v1-to-v2) ·
> [blog multi-tenant](https://medusajs.com/blog/multi-tenant-rigby/) ·
> [Rigby RLS guide](https://www.rigbyjs.com/blog/multi-tenancy-in-medusa) ·
> [#11671](https://github.com/medusajs/medusa/discussions/11671)

---

## §5. Eje A — bundle por tenant + control-plane

### §5.1 Estructura del bundle

Alineado con la nota de PLUGIN_ARCHITECTURE.md §10 ("la estructura
`infra/tenants/<x>/` ya está pensada"):

```
infra/tenants/<tenant_id>/
├── hubara.yaml          # config de la app Hubara para este tenant
├── medusa-seed.yaml     # provisioning de SU Medusa (runtime-only resources)
└── terraform/           # (diferido) stack de infra del tenant
```

`hubara.yaml`:

```yaml
tenant_id: acme-co
enabled_plugins: [chats, orders, catalog, eta, agents_admin]   # gating existente
commerce_profile: hubara-co-default        # ← referencia al Eje B (§6)
medusa:
  secret_ref: hubara-medusa-acme-co        # credenciales POR REFERENCIA — nunca inline
  region_id: reg_01...                     # no-secretos, instance-specific
  sales_channel_id: sc_01...
  currency: cop
  country: co
  shipping_option_id: so_01...             # opcional (si vacío → ShippingSelectionStrategy)
```

**Regla de secretos:** el yaml NUNCA contiene `admin_token`/passwords. Solo
`secret_ref` apuntando al secret-manager (k8s Secret / VPS env). Esto preserva el
contrato de [MedusaSettings](hubara_agency/src/platform/medusa/settings.py) (sigue
leyendo `MEDUSA_*` del env; el bundle solo decide *qué* secret se monta).

`medusa-seed.yaml` (Eje A onboarding):

```yaml
store:
  name: ACME
  supported_currencies: [{ code: cop, is_default: true }]
regions:
  - { name: Colombia, currency: cop, countries: [co], payment_providers: [pp_system_default] }
sales_channels:
  - { name: WhatsApp, is_default: true }
shipping_options:
  - { name: Envío estándar, price: 0, provider: manual }
catalog_import: ./catalog.csv     # opcional
```

### §5.2 Cómo se selecciona el tenant (silo)

Un solo env var por deployment: `COMMERCE_PROFILE=hubara-co-default` + el secret
bundle de Medusa montado. `load_active_commerce_profile()` (§8) lee el env +
resuelve el yaml. **No hay lógica nueva de routing en el código de la app.**

### §5.3 Control-plane (provisioner) — responsabilidades

Pieza nueva, SEPARADA de la app (no es un plugin). Para cada tenant:

1. Crear DB (o schema en el Postgres compartido) + Redis prefix.
2. Render `medusa-config.ts` desde template + secrets → `medusa db:migrate`.
3. Reproducir `medusa-seed.yaml` vía Admin API (regions → channels → shipping →
   currency → catálogo). **Idempotente** (re-run no duplica).
4. Registrar el tenant en el registro + wirear routing (subdomain/host → deployment).
5. Desplegar la Hubara-app del tenant con `ENABLED_PLUGINS` + `COMMERCE_PROFILE`
   + secret bundle.

Puede empezar como **runbook manual** (los pasos 2–3 a mano) y automatizarse
después — es lo que recomienda Medusa y desacopla el riesgo.

---

## §6. Eje B — el commerce profile (la combinación como config)

`commerce_profiles/<id>.yaml`. La combinación actual, capturada una vez:

```yaml
# commerce_profiles/hubara-co-default.yaml
id: hubara-co-default
display_name: Hubara Colombia (default)

variant_resolution: tags_as_descriptors    # aromas/colores = tags; 1 variante "Único"
order_flow:         draft_then_convert      # draft → convert-to-order → payment-collection
state_machine:      hubara_six_stage        # new→preparing→ready→shipping→delivered→cancelled
shipping_selection: keyword_match           # filtra "envío/domicilio/estándar"
customer_model:     synthesized_email       # wa+{session}@hubara.local
payment_model:      cod_orthogonal          # pago ortogonal al stage (COD)

currency: { code: cop, decimals: 0 }        # primitivo consumido directo
country:  co
```

Cada clave es **o un primitivo** (currency, country) **o el NOMBRE de una
estrategia registrada** (las 6 de §7). El profile es reusable: una segunda empresa
idéntica referencia `hubara-co-default` y no escribe nada nuevo.

**Ubicación (decisión abierta, §14):** se propone top-level `commerce_profiles/`
(config reusable, versionada con el repo, visible como `plugins/`). Los bundles de
tenant (§5) la referencian por id.

**Schema:** se valida con un `commerce_profile.schema.yaml` análogo a
`frontend_dashboard/src/plugins/_schema/plugin.schema.yaml`. Cada campo de
estrategia tiene `enum` con los nombres registrados → un profile que referencia
una estrategia inexistente falla fast al boot (igual que `TaskQueueMissingError`).

---

## §7. Eje B — las 6 estrategias (los contratos)

El comportamiento que hoy está hardcodeado se factoriza en **6 `Protocol`s** con
responsabilidades disjuntas (no se solapan → componen limpio). Cada uno vive en
`platform/commerce/strategies/<kind>/`, define el Protocol + la 1ª implementación
(= comportamiento de hoy) + futuras.

> **Mapeo de responsabilidades** (para que no se pisen):
> *variant* = handle→variante · *customer* = identidad+upsert · *shipping* = qué
> opción de envío · *order_flow* = qué recursos Medusa + secuencia HTTP
> create/convert/cancel · *payment* = semántica de pago + captura · *state_machine*
> = stages/DAG/reglas de `hubara_stage` + patch builders.

### §7.1 `VariantResolutionStrategy` — "obtener variantes / enlazar variantes"

Reemplaza [`_pick_variant_with_status`](hubara_agency/src/platform/orders/medusa_order.py#L417)
+ el split de labels compuestos.

```python
@dataclass(frozen=True)
class VariantPick:
    variant: Any            # MedusaVariant
    had_mismatch: bool

class VariantResolutionStrategy(Protocol):
    def pick_variant(self, product: Any, label: str | None) -> VariantPick: ...
```

| Estrategia | Comportamiento |
|---|---|
| **`tags_as_descriptors`** (hoy) | 1 variante "Único"; labels = tags descriptivos; single-variant NUNCA mismatch; scoring de tokens para 2+ opciones. |
| `variant_per_option` (futuro) | Cada combinación aroma×color es una variante real; match exacto por option values. |
| `sku_exact` (futuro) | El label ES el SKU; lookup directo. |

### §7.2 `CustomerModelStrategy` — "cómo se identifica el cliente"

Reemplaza `_upsert_customer` + `_synthesize_email`.

```python
class CustomerModelStrategy(Protocol):
    def identity(self, session_key: str) -> str: ...               # email/phone estable
    async def upsert(self, client, *, session_key: str, shipping) -> dict: ...
```

| Estrategia | Comportamiento |
|---|---|
| **`synthesized_email`** (hoy) | `wa+{session}@hubara.local`; find-or-create por email; maneja 409 race. |
| `phone_lookup` (futuro) | Customer por teléfono real. |
| `real_email` (futuro) | El cliente da email real (e-commerce con login). |

### §7.3 `ShippingSelectionStrategy` — "qué opción de envío"

Reemplaza `_discover_shipping_option_id` + `_pick_preferred_shipping_option`.

```python
class ShippingSelectionStrategy(Protocol):
    async def select(self, client, *, region_id: str, pinned_id: str | None) -> str: ...
```

| Estrategia | Comportamiento |
|---|---|
| **`keyword_match`** (hoy) | env pin → keywords ("envío/domicilio/estándar") → primera + warning. |
| `env_pinned` (futuro) | Solo el id pinneado; error si falta. |
| `single` / `cheapest` (futuro) | La única / la más barata. |

### §7.4 `OrderFlowStrategy` — "cómo se guarda y muta la orden en Medusa"

La más grande. Encapsula **qué recursos Medusa** representan la orden y la
**secuencia HTTP** de creación/conversión/cancelación. Reemplaza el cuerpo de
[`MedusaOrderRegistration`](hubara_agency/src/platform/orders/medusa_order.py)
(create) + las secuencias de [`MedusaOrderCommand`](hubara_agency/src/platform/orders/medusa_order_command.py)
(schedule→convert, cancel soft/hard).

```python
class OrderFlowStrategy(Protocol):
    async def create(self, client, *, payload: dict) -> OrderRegistrationResult: ...
    async def on_schedule(self, client, *, backend_id: str, is_draft: bool) -> None: ...
    async def cancel(self, client, *, backend_id: str, is_draft: bool) -> None: ...
    def fetch_kind_endpoints(self) -> tuple[str, str]: ...   # orders-first vs drafts-first
```

| Estrategia | Comportamiento |
|---|---|
| **`draft_then_convert`** (hoy) | `POST /admin/draft-orders` → al agendar `convert-to-order` → cancel soft (metadata) + hard (`/orders/{id}/cancel`) para orders reales; drafts solo soft. Idempotencia por fingerprint. |
| `direct_order` (futuro) | Crea `Order` directo, sin paso draft. |
| `cart_checkout` (futuro) | Flujo cart → complete (storefront-style). |

### §7.5 `PaymentModelStrategy` — "semántica de pago"

Reemplaza la secuencia `create_payment_collection` + `mark-as-paid` + la regla
"pago ortogonal al stage".

```python
class PaymentModelStrategy(Protocol):
    def gates_stage(self) -> bool: ...      # ¿el pago bloquea avanzar de stage?
    async def confirm(self, client, *, backend_id: str, total: int, by: str) -> None: ...
```

| Estrategia | Comportamiento |
|---|---|
| **`cod_orthogonal`** (hoy) | Pago independiente del stage; `payment-collections` + `mark-as-paid`; idempotente si ya `captured`. |
| `prepaid_gates_stage` (futuro) | No se avanza a `preparing` sin pago confirmado. |

### §7.6 `StateMachineSpec` — "cómo cambian de estado las órdenes"

Reemplaza los stages/DAG/reglas de [`state.py`](hubara_agency/src/platform/orders/state.py).
El estado sigue viviendo en `metadata.hubara_stage` (Hubara dueño; Medusa opaco).

```python
class StateMachineSpec(Protocol):
    stages: tuple[str, ...]
    def initial_stage(self) -> str: ...
    def allowed(self, from_stage: str) -> frozenset[str]: ...
    def validate(self, from_stage: str, to_stage: str, *, force: bool,
                 metadata: dict) -> None: ...     # incl. reglas de negocio
    def meta_prefix(self) -> str: ...             # "hubara_" (evita choques)
```

| Estrategia | Comportamiento |
|---|---|
| **`hubara_six_stage`** (hoy) | `new→preparing→ready→shipping→delivered→cancelled`; `new→preparing` exige fecha agendada; `delivered`/`cancelled` terminales; `cancelled` alcanzable desde cualquier no-terminal. |
| `restaurant_flow` (ejemplo futuro) | `received→cooking→out_for_delivery→delivered`. |

Los `build_*_patch` (schedule/confirm/cancel/initial) de `state.py` pasan a ser
funciones parametrizadas por el spec (mismas firmas, el spec inyecta stages+reglas).

---

## §8. Composition profile-aware

Las factories del composition root leen el profile activo e inyectan estrategias +
config. **Las firmas de los puertos no cambian.**

```python
# platform/commerce/profile.py
@dataclass(frozen=True)
class CommerceProfile:
    id: str
    variant_resolution: str
    order_flow: str
    state_machine: str
    shipping_selection: str
    customer_model: str
    payment_model: str
    currency_code: str
    currency_decimals: int
    country: str

@lru_cache(maxsize=1)
def load_active_commerce_profile() -> CommerceProfile:
    profile_id = os.getenv("COMMERCE_PROFILE", "hubara-co-default")
    return _parse_profile(_PROFILES_DIR / f"{profile_id}.yaml")
```

```python
# platform/orders/composition.py  (DESPUÉS — mismo shape, ahora profile-aware)
@lru_cache(maxsize=1)
def get_order_command_port() -> OrderCommandPort:
    p = load_active_commerce_profile()
    settings = get_medusa_settings()
    if not _has_medusa(settings):
        return NoopOrderCommand()                          # fallback intacto
    return MedusaOrderCommand(
        client=get_medusa_client(),
        flow=get_strategy("order_flow", p.order_flow),
        state_machine=get_strategy("state_machine", p.state_machine),
        payment=get_strategy("payment_model", p.payment_model),
    )
```

El adaptador `MedusaOrderCommand` pasa a ser un **orquestador delgado**: el
cuerpo concreto de hoy se muda a `DraftThenConvertOrderFlow` + `CodOrthogonalPayment`
+ `HubaraSixStage`. Los stubs (`NoopOrderCommand`, `EmptyOrderQuery`,
`StubOrderRegistration`) quedan **igual**.

**Keying del cache:** en silo hay 1 profile/proceso → `maxsize=1` sigue siendo
correcto. La firma `get_strategy(kind, name)` deja la puerta abierta a keyear por
tenant si algún día se hace pooled (no se construye ahora — C2).

---

## §9. Cómo cumple cada requisito del operador

1. **"Config-driven nueva Medusa por empresa"** → tenant = `infra/tenants/<id>/`
   nuevo. Misma combinación → reusa `hubara-co-default` (config pura). Combinación
   nueva expresable como config → `commerce_profiles/<id>.yaml` nuevo. Combinación
   con algoritmo nuevo → estrategia nueva (R-ISO-2). **Cero ediciones a archivos
   existentes.**

2. **"Prendo/apago el plugin de órdenes por tenant → funciona todo"** → vía
   `ENABLED_PLUGINS` (auto-discovery existente). **Sutileza clave:** el kanban +
   el worker `reconcile` son el plugin `orders`, pero el tool `register_order`
   (el agente *creando* la orden) vive en el sales worker de `chats` y se activa
   cuando el puerto Medusa está configurado. **El commerce profile + las
   credenciales Medusa son el switch único que enciende AMBOS lados** — esa
   centralización es justo lo que hoy falta.

3. **"Completamente isolated"** → 4 niveles: **config** (bundles/profiles
   disjuntos), **estrategias** (registry append-only, R-ISO-2), **plugins**
   (R1/R2/R3 de PLUGIN_ARCHITECTURE.md), **deployment** (silo = DB/Medusa/
   Temporal-namespace físicamente separados; blast radius cero).

4. **"No rompe producción por ninguna razón"** → Eje A: cero cambio de código.
   Eje B: el único cambio a archivos vivos es la extracción byte-idéntica (§11),
   gateada por toda la suite + architecture gates + functional E2E. El default
   reproduce hoy exactamente (R-ISO-3). Backward-compatible por construcción.

---

## §10. Inventario de seams (call-sites que tocan composition)

Los puntos que consumen las factories. **Ninguno cambia su llamada** — el cambio
es interno. Dos patrones:

| Patrón | Dónde | Implicación |
|---|---|---|
| **Module-level binding** | [`sales.py:115/142/161`](hubara_agency/src/plugins/chats/workers/sales.py#L115) — `_catalog`, `_medusa`, `_order_registration_port` se atan al import | Ata 1 tenant/proceso. En silo está OK (C2). Sería el seam a refactorizar si algún día se hace pooled. |
| **Per-request call** | [`orders/api`](hubara_agency/src/plugins/orders/api/__init__.py) (8 call-sites), `chats/api/eta.py`, `eta/activities/tracking.py`, `orders/agent/activities/reconcile.py` | Llaman la factory por request/activity → ya tenant-routable si se necesita. |

Factories afectadas: `get_order_registration_port`, `get_order_query_port`,
`get_order_command_port`, `get_catalog_client`, `get_medusa_client`,
`get_medusa_product_service`, `get_medusa_settings`.

---

## §11. Plan de migración (registry completo — cada PR shippea verde)

Decisión del operador: **construir el registry completo** (las 6 estrategias),
no solo el seam. Cada PR es una extracción byte-idéntica, reversible, gateada por
los tests existentes. El tenant productivo corre sobre `hubara-co-default` todo el
tiempo.

- **PR0 — Scaffolding (aditivo, sin consumo).** `platform/commerce/` package:
  `profile.py` (`CommerceProfile` + `load_active_commerce_profile`), `registry.py`
  (`register_commerce_strategy`/`get_strategy`, idempotente por `(kind, name)`,
  auto-discovery de `strategies/`), `commerce_profiles/hubara-co-default.yaml` +
  su schema. Nada lo consume aún. Tests verdes triviales.
- **PR1 — `VariantResolutionStrategy`** (menor riesgo). Extrae `_pick_variant_with_status`
  → `tags_as_descriptors`. `MedusaOrderRegistration` consume la estrategia
  inyectada. Gate: `tests/plugins/chats/sales/test_register_order_tool.py` + functional.
- **PR2 — `ShippingSelectionStrategy` + `CustomerModelStrategy`** (helpers aislados
  del registration adapter).
- **PR3 — `StateMachineSpec`** (`hubara_six_stage` = `state.py`). Parametriza los
  `build_*_patch`. Gate: tests de transición + functional.
- **PR4 — `OrderFlowStrategy` + `PaymentModelStrategy`** (`draft_then_convert` +
  `cod_orthogonal`). La extracción más grande; el command/registration adapter
  quedan como orquestadores delgados. Gate: functional E2E de schedule/pay/cancel.
- **PR5 — Composition profile-aware.** Las factories leen el profile e inyectan.
  Gate: arranque con `COMMERCE_PROFILE` unset = idéntico; smoke test E2E.
- **PR6 — Eje A: bundle + control-plane.** `infra/tenants/<id>/` + provisioner
  (runbook manual primero → automatización). Desbloquea onboarding real.

Cada PR: `uv run pytest -q` + `uv run lint-imports` + `uv run pytest -m architecture`
verdes antes de mergear. R-DIP: `platform/commerce` → `platform/*` OK; los plugins
consumen vía composition (sin nuevos cruces).

---

## §12. Verificación cruzada — comandos canónicos

```bash
# Suite completa (debe seguir verde tras cada extracción)
cd hubara_agency && uv run pytest -q

# Architecture gates (R-rules) — el registry no debe introducir cruces R-DIP
cd hubara_agency && uv run pytest -m architecture && uv run lint-imports

# El default reproduce hoy: arrancar sin COMMERCE_PROFILE = comportamiento idéntico
cd hubara_agency && uv run python -c "from src.platform.commerce.profile import load_active_commerce_profile as f; print(f().id)"
# → hubara-co-default

# Smoke E2E (bearings)
cd hubara_agency && bash .hubara/smoke-test.sh
```

---

## §13. Lo que NO hacemos (diferido / rechazado)

| Item | Estado | Razón |
|---|---|---|
| Pooled multi-tenant en Hubara (1 proceso, N tenants) | ⏸ Diferido | El silo lo hace innecesario. Si llega: keyear factories por tenant + delazyficar bindings module-level del sales worker (§10). |
| Medusa single-instance multi-store (sales-channel/tenant) | ❌ Rechazado | Customers compartidos; aislamiento débil para empresas distintas (§4.2/4.3-d). |
| Medusa RLS shared-schema (`tenant_id` en 44+ tablas) | ❌ Rechazado (por ahora) | Patch del framework eterno; raw SQL/singletons/jobs bypassean. Reconsiderar solo a escala de cientos/miles. |
| Mini-lenguaje en el profile (condiciones, expresiones) | ❌ Rechazado | Mismo riesgo que las `when:` clauses (ADR-2026-05-20 §10.3). El profile solo selecciona estrategias + primitivos. |
| Hot-reload de profiles sin restart | ❌ Rechazado | `@lru_cache` + restart, igual que D10 de PLUGIN_ARCHITECTURE.md. |
| Automatizar el provisioner antes del 2º tenant | ⏸ Diferido | Runbook manual primero; automatizar cuando el flujo esté validado. |
| Capability spec de comportamiento por profile | ⏸ Diferido | Cuando exista 2º profile real, documentar en `.hubara/specs/`. |

---

## §14. Decisiones abiertas

1. **Ubicación de `commerce_profiles/`**: top-level (propuesto, reusable/visible)
   vs `hubara_agency/`. Decidir antes de PR0.
2. **Postgres por tenant**: server dedicado vs server compartido con 1 DB/tenant
   (bin-pack). Recomendado el segundo a 5–50; decidir en PR6.
3. **Control-plane**: runbook manual primero vs automatizar el provisioner ya.
4. **Frontend per-tenant**: hoy 1 bundle gated por `ENABLED_PLUGINS`; migrar a
   bundle-per-tenant cuando tenant count > 5 (= ítem diferido de PLUGIN_ARCHITECTURE.md §10).
5. **Registry: auto-discovery vs entry-points explícitos** para las estrategias.
   Recomendado auto-discovery de `strategies/` (espeja plugins).

---

## §15. Glosario

- **Eje A (Tenancy/Routing)** — a qué Medusa apunta un tenant + credenciales.
  Resuelto por silo + bundle de config.
- **Eje B (Combinación)** — cómo se mapean productos/variantes/estados/órdenes.
  Resuelto por commerce profile + strategy registry.
- **Silo** — 1 Medusa + 1 DB por tenant; aislamiento físico (decisión C1/D1).
- **Commerce profile** — YAML que captura una combinación seleccionando las 6
  estrategias + primitivos. `hubara-co-default` = el comportamiento de hoy.
- **Strategy** — implementación nombrada de uno de los 6 `Protocol`s de §7,
  auto-registrada. Una nueva = archivo nuevo (R-ISO-2).
- **Tenant bundle** — `infra/tenants/<id>/` con `hubara.yaml` + `medusa-seed.yaml`.
- **Control-plane / provisioner** — automatización que crea la Medusa del tenant
  (migrate + seed vía Admin API) y despliega la Hubara-app.
- **Extracción byte-idéntica** — mover lógica hardcodeada a una estrategia sin
  cambiar comportamiento, gateada por la suite (R-ISO-3).

---

**Fin del documento.** Este es el contrato. Cualquier desviación durante la
implementación se discute con el operador antes de aplicarla.
