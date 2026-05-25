# Orders — implementación y setup de producción

Guía breve de los dos desarrollos del módulo `orders` y los pasos manuales
que tenés que ejecutar para activarlo en producción.

---

## Los dos desarrollos

### 1. **Crear órdenes** — agente Sales → Medusa Draft Orders

Cuando el agente de WhatsApp cierra una venta, llama la tool
`register_order(items, shipping, payment_method, ...)`. La tool delega al
`OrderRegistrationPort` (DI hexagonal), cuyo adapter live
`MedusaOrderRegistration` hace:

1. Resolve `handle` → `variant_id` (paralelo, N items).
2. Find-or-create customer por email sintetizado (`wa+{session}@hubara.local`).
3. Descubre el `shipping_option_id` (smart filter por nombre).
4. `POST /admin/draft-orders` con `metadata.idempotency_key` y
   `metadata.session_key`.
5. Devuelve `OrderRegistrationResult(success, order_id, ...)`.

Si Medusa falla → la tool persiste el intento en
`metadata.failed_order_registrations[]` y el LLM escala a humano con
`ORDER_REGISTRATION_FAILED`. Si Medusa no está configurado → cae al
`StubOrderRegistration` y loguea warning (dev mode).

Toda la secuencia está envuelta en `asyncio.wait_for(45s)` para que el
activity de Temporal nunca se cuelgue indefinidamente.

**Archivos clave:**
- `src/plugins/chats/agent/sales/tools/order_registration.py` — la tool del LLM.
- `src/platform/orders/port.py` — `OrderRegistrationPort` (Protocol + DTOs).
- `src/platform/orders/medusa_order.py` — adapter live.
- `src/platform/orders/composition.py` — DI factory.

### 2. **Obtener órdenes** — dashboard frontend ← Medusa

El plugin `orders` expone 3 endpoints FastAPI bajo `/api/orders/*`:

| Endpoint | Función |
|---|---|
| `GET /orders` | Lista (kanban). Fusiona `/admin/orders` + `/admin/draft-orders`. |
| `GET /orders/{id}` | Detalle (inspector). Acepta `order_01HXX...` o `#1247`. |
| `GET /vault-orders` | Pedidos NO en Medusa (failed + stub). Para reconciliación manual. |
| `GET /orders-health` | Sanity check del port + Medusa reachable. |

El backend hace mapping `payment_status` + `fulfillment_status` → status
UI (new/preparing/ready/shipping/delivered/cancelled). Slots que Medusa
no tiene (timeline detallado, agente, notas, customer history) salen
con `data_completeness_missing[]` y el frontend pinta marker "Pendiente
integración".

Si Medusa cae / token expira / red down, los endpoints devuelven shape
válido pero con `catalog_available: false` y `error_detail` específico
(distingue 401, 403, 5xx) — el dashboard muestra banner explícito en
lugar de error 500.

**Archivos clave:**
- `src/platform/orders/query_port.py` — `OrderQueryPort` + DTOs.
- `src/platform/orders/medusa_order_query.py` — adapter live.
- `src/plugins/orders/api/__init__.py` — router FastAPI.
- `src/plugins/orders/vault_scanner.py` — escanea vault local.
- `frontend_dashboard/src/entities/order/` — hooks + Zod contracts.
- `frontend_dashboard/src/plugins/orders/frontend/` — kanban + inspector + banner.

---

## Setup en producción — paso a paso

### Pre-requisitos

- Medusa v2 desplegado y accesible desde el backend (`MEDUSA_BASE_URL`).
- Al menos un producto publicado en Medusa con los mismos `handle` que
  usa el agente Sales (sincronizado vía `catalog_sync`).
- Admin user con permisos `manage_orders` + `manage_customers` +
  `manage_shipping_options`.

### Variables de entorno requeridas

Las siguientes envs van en `.env` del backend (`hubara_agency/.env`):

```bash
# === Existentes (ya estaban) ===
MEDUSA_BASE_URL=https://medusa.tu-dominio.com
MEDUSA_ADMIN_TOKEN=sk_...

# === Nuevas para Orders ===
MEDUSA_REGION_ID=reg_01HXX...           # OBLIGATORIO para crear órdenes
MEDUSA_SALES_CHANNEL_ID=sc_01HXX...     # OBLIGATORIO para crear órdenes
MEDUSA_DEFAULT_SHIPPING_OPTION_ID=so_01HXX...  # OPCIONAL (recomendado)
MEDUSA_DEFAULT_CURRENCY=cop             # default ya es "cop"
MEDUSA_DEFAULT_COUNTRY=co               # default ya es "co"
```

> ⚠️ **Si NO seteás `MEDUSA_REGION_ID` + `MEDUSA_SALES_CHANNEL_ID`:** el
> agente Sales cae al `StubOrderRegistration` — registra los pedidos en
> `metadata.json` local pero **no llegan a Medusa**. El dashboard los
> muestra en "Pendientes de reconciliar" con tag "Local (stub)" para que
> los migres manualmente. Para producción real, los dos vars son obligatorios.

### Cómo conseguir cada ID (Medusa Admin paso a paso)

Asumo Medusa Admin corriendo en `https://medusa.tu-dominio.com/app`. Si tu
URL es distinta, reemplazá.

#### A) `MEDUSA_REGION_ID`

1. Abrir `https://medusa.tu-dominio.com/app`.
2. Sidebar izquierdo → ⚙️ **Settings** → **Regions**.
3. Si ya tenés una región para Colombia (con currency `COP`), entrá a ella.
4. Si NO, click **Create Region**:
   - **Name**: `Colombia`
   - **Currency**: `COP - Colombian Peso`
   - **Tax rate**: `0` (o el que aplique a tu negocio)
   - **Payment providers**: marcá los que aplican
   - **Countries**: agregá `Colombia`
   - Save.
5. Una vez creada (o entrando a la existente), el **ID está en la URL**:
   `https://medusa.tu-dominio.com/app/settings/regions/reg_01HXXXXXXXXXX`
   → copia `reg_01HXXXXXXXXXX`.

#### B) `MEDUSA_SALES_CHANNEL_ID`

1. Sidebar → ⚙️ **Settings** → **Sales Channels**.
2. Por default hay uno llamado **"Default Sales Channel"** — podés usar ese
   o crear uno dedicado a WhatsApp:
   - Click **Create Sales Channel**.
   - **Name**: `WhatsApp Hubara`
   - **Description**: `Ventas cerradas por el agente conversacional`
   - Save.
3. Entrá al sales channel → el **ID está en la URL**:
   `https://medusa.tu-dominio.com/app/settings/sales-channels/sc_01HXXXXXXXXXX`
   → copia `sc_01HXXXXXXXXXX`.
4. **Importante**: tenés que asociar tus productos a este sales channel
   (sino el draft order falla con "product not available in this sales channel"):
   - **Products** → bulk select → "Add to Sales Channel" → elegí el que creaste.

#### C) `MEDUSA_DEFAULT_SHIPPING_OPTION_ID` (opcional pero recomendado)

> Sin este var, el adapter intenta descubrir la shipping option
> automáticamente filtrando por nombre (`envio` / `shipping` /
> `domicilio` / `estandar` / `standard`). Funciona pero hace un
> round-trip extra por sesión y NO es determinístico si tenés varias
> opciones con esos nombres. Recomendado: setealo explícitamente.

1. Sidebar → ⚙️ **Settings** → **Locations & Shipping**.
2. Entrá al **shipping profile** que corresponda a tu region (Colombia).
3. Si ya tenés una opción "Envío estándar", entrá a ella; sino crea una
   nueva:
   - **Name**: `Envío estándar`
   - **Profile**: default
   - **Price type**: `Flat Rate`
   - **Amount**: tu costo (puede ser `0` si lo decidís en checkout, o el
     fijo que cobras).
   - **Region**: Colombia (la que creaste arriba).
   - Save.
4. El ID está en la URL:
   `https://medusa.tu-dominio.com/app/settings/locations/.../shipping-options/so_01HXXXXXXXXXX`
   → copia `so_01HXXXXXXXXXX`.

#### D) `MEDUSA_ADMIN_TOKEN` (si no lo tenés todavía)

> Si ya configuraste el catálogo (`HU-01`) este token ya está en tu `.env`.
> Saltá este paso.

1. Sidebar → ⚙️ **Settings** → **Developer** → **Secret API Keys**.
2. Click **Create API Key**.
   - **Title**: `Hubara Backend`
   - Save.
3. **Copia el token AHORA** — empieza con `sk_...` y Medusa NO te lo
   muestra de nuevo después de cerrar el modal.

---

## Aplicar la config

Una vez que tengas los 3 IDs:

1. Editá `hubara_agency/.env`:
   ```bash
   MEDUSA_REGION_ID=reg_01HXX...
   MEDUSA_SALES_CHANNEL_ID=sc_01HXX...
   MEDUSA_DEFAULT_SHIPPING_OPTION_ID=so_01HXX...
   ```

2. Re-renderizá el docker-compose (lee del `.env` y los pasa a los
   containers):
   ```bash
   cd hubara_agency
   uv run python scripts/render-compose.py
   ```

3. Recreá los containers afectados:
   ```bash
   docker compose -f docker-compose.local.yml up -d --force-recreate \
     hubara-api hubara-worker-chats-sales
   ```

4. Verificá que el adapter se eligió correctamente:
   ```bash
   docker compose -f docker-compose.local.yml logs hubara-api hubara-worker-chats-sales \
     | grep "OrderRegistrationPort\|OrderQueryPort"
   ```
   Deberías ver:
   ```
   OrderRegistrationPort = MedusaOrderRegistration (region_id=reg_..., ...)
   OrderQueryPort = MedusaOrderQuery (base_url=...)
   ```
   Si ves `StubOrderRegistration` / `EmptyOrderQuery` → faltó setear algún
   env, revisá el `.env`.

---

## Smoke test end-to-end

### 1. Verificar que los endpoints respondan

```bash
# Sanity check del port + reachability de Medusa
curl http://localhost:8000/api/orders/orders-health
# Esperado: {"port":"MedusaOrderQuery","catalog_available":true,...}

# Lista (puede venir vacía si no hay órdenes todavía)
curl http://localhost:8000/api/orders/orders
# Esperado: {"orders":[],"count":0,"catalog_available":true,"error_detail":null,...}

# Vault orders (failed + stub) — debería estar vacío en una instalación limpia
curl http://localhost:8000/api/orders/vault-orders
# Esperado: {"records":[],"count":0,"failed_count":0,"stub_count":0}
```

### 2. Cerrar una venta de prueba vía WhatsApp

Desde tu número de WhatsApp Sandbox:
1. Saluda al agente.
2. Pide un producto del catálogo.
3. Confirma con `present_order_confirmation` → "✅ Confirmar".
4. Completa el Flow de datos de envío (o respondé conversacionalmente).
5. El agente llama `register_order` → debería crear un Draft Order.

Verifica en logs del worker:
```bash
docker compose -f docker-compose.local.yml logs hubara-worker-chats-sales \
  | grep "MedusaOrderRegistration"
```
Esperado: `draft_order created` con un `order_id="draft_01HXX..."`.

### 3. Verificar en el dashboard

Abrir el frontend (`http://localhost:5173`) → sección **Orders**.

Debería aparecer el draft order recién creado en la columna "Nueva" con
badge "**DRAFT**" violeta a la izquierda. Click en la card → inspector
muestra items + dirección + total. Los campos sin datos (timeline,
notas, agente, historial cliente) llevan marker "Pendiente integración".

### 4. Verificar fallbacks

Para probar que el sistema NO se rompe si Medusa cae:

```bash
# Apagá Medusa temporalmente (si lo tenés en docker)
docker stop medusa-server  # o el container que uses

# El endpoint NO debe devolver 500 — devuelve catalog_available=false
curl http://localhost:8000/api/orders/orders
# Esperado: {"orders":[],"catalog_available":false,"error_detail":"medusa_unavailable: HTTP ...",...}

# El dashboard muestra banner "Medusa no está disponible" con el motivo.
```

Para probar token expirado:
```bash
# Modificá MEDUSA_ADMIN_TOKEN a un valor inválido en .env, recreá hubara-api
curl http://localhost:8000/api/orders/orders
# Esperado: {"catalog_available":false,"error_detail":"medusa_unauthorized: HTTP 401 — el admin_token expiró..."}
# El dashboard muestra banner "Medusa: token expirado" (mensaje distinto al de Medusa down).
```

---

## Datos que el dashboard NO muestra todavía

El backend devuelve `data_completeness_missing[]` con estos slots que
Medusa no expone y que pintamos como "Pendiente integración" en la UI.
Cuando estos vengan de algún lado, hay que actualizar el mapper:

| Slot | Falta integrar |
|---|---|
| `due_date` / `due_time` | Tracking de fecha comprometida — necesita campo en metadata o integración con shipping providers. |
| `tracking_number` | Provider de envíos (Coordinadora, Envia, etc.). |
| `shipping_provider` | Idem. |
| `payment_method_detail` | Últimos dígitos, comisión gateway, etc. — necesita integración Wompi. |
| `agent` | Persistir agente asignado al order (Medusa no lo trackea; podría ir en `metadata.agent`). |
| `priority` | Hoy derivado heurístico por total. Si querés priorización real, agregar campo. |
| `notes` | Notas internas — necesita endpoint propio + UI de edición. |
| `customer_history` | LTV, recurrencia, tag VIP — necesita query agregada sobre orders + clientes. |

Cada slot tiene un placeholder visual en el inspector — no engaña al
operador con datos falsos.

---

## Troubleshooting

| Síntoma | Causa probable | Fix |
|---|---|---|
| Worker arranca con `OrderRegistrationPort = StubOrderRegistration` | Falta `MEDUSA_REGION_ID` o `MEDUSA_SALES_CHANNEL_ID` | Setealos en `.env`, re-render compose, recrear worker. |
| `register_order` falla con `HTTP 422` | Sales channel no asociado al producto | En Medusa Admin → producto → agregar a tu sales channel. |
| `register_order` falla con `HTTP 422: shipping_methods` | No hay shipping options creadas en la región | Crear al menos una. Ver §C arriba. |
| Endpoint devuelve `medusa_unauthorized` | Admin token expirado | Crear nuevo Secret API Key, actualizar `.env`, recrear `hubara-api`. |
| Endpoint devuelve `medusa_unavailable` | Medusa caído / red rota | Verificar que Medusa está arriba y el backend lo alcanza (`curl $MEDUSA_BASE_URL/health` desde el container). |
| El dashboard muestra "Pendientes de reconciliar (N stub)" | Hay órdenes que se crearon antes de configurar Medusa | Cada record tiene `session_key` + items + shipping — registrarlas manualmente en Medusa Admin o ignorar (son históricas). |
| Variante incorrecta en el draft order | El LLM mandó `variant_label` que no matchea | Ver `metadata.variant_mismatches[]` del draft en Medusa — corregir manualmente. |

---

## Documentos relacionados

- `docs/PREMORTEM_ORDERS.md` — análisis de modos de fallo + fixes aplicados.
- `docs/META_CATALOG_SETUP.md` — setup de la sincronización Medusa → Meta Catalog.
- `.env.example` — referencia completa de variables.
- `frontend_dashboard/src/plugins/orders/plugin.yaml` — manifest del plugin.
