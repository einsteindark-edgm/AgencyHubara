# Plan — Central de cupones con cupo por unidad

> **Estado 2026-09-23: IMPLEMENTADO en la rama `claude/coupon-central`, sin PR todavía** (ver §12).
> Fases 1–8 hechas con TDD; la Fase 0 es el PR #342 (otra sesión), que esta rama incluye por merge.
> Decisiones D1, D6, D7 y D8 tomadas por el operador; D2 y D3 van con el default recomendado hasta
> que se confirmen (§1). Falta: merge de #342, PR de esta rama, verificación en vivo (§12).
> Documento de decisión (v2, con maquetas y comparación de opciones):
> https://claude.ai/artifact/EbAEp1CdfoS7STjqVTvzyD — este archivo es el **plan de desarrollo**.
> Disparador: cupón AMOR26 (campaña "AMOR Y AMISTAD 2026"). El operador quiere (1) crear y
> manejar los cupones desde el dashboard, sin entrar a Medusa Admin, y (2) que un cupón aplique
> solo a unidades exactas (producto + color + aroma) llevando la cuenta de cuántas quedan.

---

## 0. TL;DR

- **Qué se construye:** la **central de cupones** en Marketing (selector `Campañas | Cupones`).
  - El operador crea, edita, pausa y borra cupones **de porcentaje** (D8).
  - Hubara los escribe en Medusa por la API admin: promoción y campaña en **una sola llamada**.
  - A cada cupón se le puede poner **cupo por unidad**: producto + color + aroma + cantidad.
- **Cómo cuenta:** las vendidas **se derivan de los pedidos de Medusa** que llevan la marca del
  cupo. No hay contador aparte: lo cancelado, borrado o de prueba deja de contar solo.
- **Qué hace el bot:** aplica el descuento solo a las unidades del cupo y dice cuántas quedan
  (D3). Cuando se agotan lo dice y ofrece el precio normal. Si el cliente pide más unidades de
  las que quedan, descuenta las que hay y el resto va a precio normal (D2).
- **Cómo queda el pedido en Medusa (Fase 0):** Hubara escribe el precio con descuento en su
  propia línea, con metadata de auditoría, **sin `promo_codes`**. Medusa 2.12.5 no aplica
  promociones con reglas de producto a draft orders (incidente #44).
- **Por qué no alcanza Medusa sola:** el código es único, el presupuesto de campaña cuenta
  pedidos o plata (nunca unidades) y no se mueve con draft orders, `max_quantity` es por línea,
  y las etiquetas son del producto, no de la unidad vendida (§2).
- **Tamaño:** ≈ 12–14 días de desarrollo además de la Fase 0, en 8 fases con TDD. El primer
  corte útil sale en ≈ 10 días: fases 1–5 más una central básica.

---

## 1. Decisiones

| ID | Decisión | Estado | Qué implica para el desarrollo |
|---|---|---|---|
| D1 | Opción C: central de cupones en Hubara, que escribe en Medusa por API; el cupo vive en Hubara | **Aprobada** 2026-09-23 | Este plan. |
| D2 | El cliente pide más unidades de las que quedan | Default: **(a) parcial**, pendiente de confirmar | El reparto descuenta las que quedan y el resto va a precio normal, dicho en la confirmación. |
| D3 | ¿El bot dice cuántas quedan? | Default: **(a) número exacto**, pendiente de confirmar | Preferencia por cupón `show_units_left`, por defecto `true`. |
| D4 | AMOR26 mientras no exista la central | Operativa (no es desarrollo) | Ver §9. |
| D5 | ¿AMOR26 debía valer el 27-sep? | Operativa | Ver §9. La central evita el error: el "hasta" es un día incluido, en hora de Bogotá. |
| D6 | ¿Quién crea y edita cupones? | **Aprobada: cualquier usuario del dashboard** | Sin roles. Cada acción queda en un registro de cambios con el usuario **verificado** de la sesión (Fase 7.0). |
| D7 | ¿Y si alguien edita un cupón en Medusa Admin? | **Aprobada: (a)** | La central siempre muestra lo que hay en Medusa y edita solo los cupones "gestionables" (§4.4). Los demás quedan en solo lectura y nunca se borra una regla ajena. |
| D8 | Tipos de cupón en v1 | **Aprobada: solo porcentaje** | La central crea y edita únicamente `percentage` sobre `items`. Los cupones de monto creados en Medusa se ven en solo lectura, y el bot los sigue aplicando como hoy. |

---

## 2. Hechos verificados que condicionan el diseño

Verificado el 2026-09-23 contra la Medusa de producción (solo GET), el código de Hubara en `main` y el código fuente
instalado de Medusa **2.12.5** (`~/Documents/Projects/HubaraAdministrador/hubara_backend/medusa-backend/node_modules/@medusajs`).

**Medusa 2.12.5**

- **`code` es único y distingue mayúsculas.** Hay un índice único parcial `IDX_unique_promotion_code … WHERE deleted_at IS NULL`
  (`promotion/dist/models/promotion.js:51-57`). No se pueden tener dos promociones con el mismo código.
  `campaign_identifier` también es único (`promotion/dist/models/campaign.js:29-32`).
- **Crear:** `POST /admin/promotions` acepta `campaign` en línea (sin `campaign_id`) y crea las dos cosas en una llamada.
  Los validadores son `.strict()` y **no aceptan `metadata`** (`medusa/dist/api/admin/promotions/validators.js:110-153`).
- **Editar y demás endpoints:**
  - Editar la promoción: `POST /admin/promotions/{id}`.
  - Cambiar reglas de producto: `POST /admin/promotions/{id}/target-rules/batch` (create/update/delete).
  - Editar la campaña: `POST /admin/campaigns/{id}`.
  - Borrar: `DELETE /admin/promotions/{id}` y `DELETE /admin/campaigns/{id}`.
  - Estados: `draft | active | inactive`.
- **Validaciones del método de aplicación** (`promotion/dist/utils/validations/application-method.js:20-90`):
  - el porcentaje va en (0, 100];
  - `allocation: each` exige `max_quantity`;
  - `across` lo prohíbe;
  - `target_type: items` exige `allocation`.
- **Qué muestra el panel de Medusa:** conoce `items.product.id`, `…categories.id`, `…collection_id`, `…type_id` y
  `…tags.id`, pero **no** `items.variant.id` (`medusa/dist/api/admin/promotions/utils/rule-attributes-map.js`).
  Una regla con otro atributo existe, pero el panel no la muestra.
- **Draft order con `promo_codes`:**
  - El código **se vincula siempre** (`core-flows/dist/draft-order/workflows/refresh-draft-order-adjustments.js:62-66`).
  - Al crear el draft, el contexto no trae `items.product` (`core-flows/dist/order/workflows/create-order.js:293-321`),
    así que las reglas por producto o etiqueta no coinciden y el descuento queda en 0.
  - Editar el draft carga solo `product: {id}` (`compute-draft-order-adjustments.js:98-107`).
- **Usos y presupuestos:** `registerUsage` solo corre en `completeCartWorkflow`
  (`core-flows/dist/cart/workflows/complete-cart.js`). Los presupuestos de campaña y el `Promotion.limit` de 2.12
  **nunca se mueven con draft orders**, y cancelar no revierte.
- **`max_quantity`:** es un tope por línea (`each`) o por pedido (`once`), nunca entre pedidos
  (`utils/dist/totals/promotion/index.js:110-113`).

**Producción (GET)**

- La única promoción es **AMOR26**: `percentage` 10, `items`, `each`, `max_quantity` 10.
  - Reglas: `items.product.id in [4 productos]` + `items.product.tags.id in [4 etiquetas]` (agregada 15:26 Bogotá).
  - Campaña `2026-09-22T05:00Z → 2026-09-27T05:00Z`; presupuesto `usage` sin límite, `used 0`.
- Los 4 productos tienen las **22 etiquetas** (11 `Color: …` y 11 `Aroma: …`) y una variante "Unico".
  Ninguna variante del catálogo tiene `manage_inventory`.
- Pedido **#44**: AMOR26 vinculado, `discount_total 0`, `total 49900`; el bot le confirmó al cliente 45.700 (`metadata.discount_cop 4200`).

**Hubara**

- **Lectura de promociones:** `PromotionsPort` con caché de 60 s por proceso (`src/platform/promotions/medusa.py`),
  expuesto por `src.sdk.connectorkit`. Entiende la regla por etiquetas desde #340.
- **Cálculo del descuento:** `compute_discount` (`src/platform/promotions/rules.py`). En porcentaje ignora `max_quantity`.
- **Estado del cupón en el chat:** `apply_coupon` guarda un snapshot en `episodes[-1].applied_coupon`
  (`src/plugins/chats/agent/sales/use_cases/coupons.py`). `present_order_confirmation`, `register_order` y
  "Crear pedido" **recalculan** el descuento (L-19).
- **Color y aroma:**
  - Solo viajan como texto libre en `variant_label` (`src/platform/orders/medusa_order.py:370-445`).
  - El borrador (`order_draft.items[]`) ya los guarda estructurados (`src/plugins/chats/shared/draft_items.py`).
  - Hay listas cerradas y matching: `parse_variant_tags` / `match_option` (`src/platform/catalog/variant_attrs.py`).
- **Almacenamiento:** no hay base de datos propia.
  - El vault está en un disco compartido por la API y todos los workers, en un solo host.
  - La lectura-modificación-escritura atómica usa `fcntl.flock` (`src/platform/state.py:124-160`).
- **Ciclo de vida de pedidos:** Medusa no manda webhooks.
  - La etapa vive en `metadata.hubara_stage` (`src/platform/orders/state.py`).
  - Cancelar un draft desde Hubara solo cambia metadata (`hubara_stage=cancelled`).
  - Los pedidos de prueba llevan `hubara_test_order`.
- **Autenticación del dashboard:** `require_auth` (`src/platform/auth.py`) valida Cognito o el token de servicio, pero no
  deja el usuario disponible y no hay roles. Hoy Órdenes toma el "by" del cuerpo del request, sin verificar.
- **Marketing hoy:**
  - Es una sola pantalla de campañas (`frontend_dashboard/src/plugins/marketing/frontend/MarketingSection.tsx`).
  - El cupón es un campo de texto en el paso 2 (`…/campaign-builder/ui/OfferStep.tsx`, máximo 14 caracteres).
  - La plantilla usa `percent` y `valid_until` como texto libre (`src/plugins/marketing/domain/campaigns.py:552-564`).
- **Tienda web:** el carrito termina en WhatsApp, sin checkout con cupones. Todas las ventas nacen en el bot o en
  "Crear pedido".

---

## 3. Alcance

### 3.1 Entra en v1

1. **Central de cupones** en Marketing: listar por estado, crear, editar, pausar/activar y borrar (solo borradores sin ventas).
   - Solo `percentage` sobre `items` (D8), a todo el catálogo o a productos elegidos.
   - Fechas con "hasta" incluido, en hora de Bogotá.
2. **Cupo por unidad** por cupón: filas producto + color + aroma + unidades, con vendidas y quedan en vivo.
3. **Resultados** por cupón: pedidos, descuento total y unidades del cupo, todo derivado de los pedidos.
4. **Registro de cambios:** quién hizo qué y cuándo, con la identidad verificada de la sesión.
5. **Bot de ventas:** aplica el cupo, informa cuántas quedan, reparte en parcial y maneja "agotado" y "se llevaron la última".
6. **"Crear pedido" del dashboard:** consume el cupo igual que el bot.
7. **Constructor de campañas:** el paso 2 elige el cupón de la lista de la central. `percent` y `valid_until` salen del cupón.
8. **Fase 0** (en curso): totales correctos en Medusa para cualquier cupón.

### 3.2 No entra en v1

- Cupones de monto fijo, de envío, 2x1 o con mínimo de compra: solo se ven en solo lectura si alguien los crea en Medusa.
- Límite de usos por cupón (se podría derivar después con el mismo lector de pedidos).
- Roles o permisos por usuario (D6).
- Cupo para pedidos creados a mano en Medusa Admin: no llevan la marca y no cuentan.
- Checkout web con cupones.
- "Apartar" unidades en la confirmación. Se revalida al registrar; si hay conflicto, se pide confirmación de nuevo.

---

## 4. Arquitectura

Respeta el aislamiento de plugins. Las capacidades viven en `src/platform/promotions/` y se exponen por
`src.sdk.connectorkit` (P-28). Las consumen `marketing` (la central, en el proceso API) y `chats` (el bot en el worker
de ventas, y "Crear pedido" en la API).

Igual que en `orders` (command_port / query_port), **lecturas y comandos van en puertos separados**: el worker de
ventas solo lee.

### 4.1 Mapa de piezas

| Pieza | Archivo | Proceso | Responsabilidad |
|---|---|---|---|
| Dominio del cupón | `src/platform/promotions/coupon.py` (nuevo) | puro | `CouponSpec`, validación, mapeo a payload Medusa, `CouponView` (estado, gestionable) desde Medusa, fechas Bogotá |
| Comandos Medusa | `src/platform/promotions/admin.py` (nuevo) | API | `PromotionsAdminPort` + `MedusaPromotionsAdmin` + `FakePromotionsAdmin` + errores de dominio |
| Cliente Medusa | `src/platform/medusa/client.py` | API | nuevos: `get_promotion`, `create_promotion`, `update_promotion`, `batch_promotion_target_rules`, `update_campaign`, `delete_promotion`, `delete_campaign` |
| Dominio del cupo | `src/platform/promotions/quotas.py` (nuevo) | puro | `PromoUnitQuota`, `units_left`, `allocate_units` (reparto), claves normalizadas |
| Almacén del cupo | `src/platform/promotions/quota_store.py` (nuevo) | API + worker | `PromoQuotaStore` + `VaultPromoQuotaStore` (flock) + `FakePromoQuotaStore` |
| Registro de cambios | `src/platform/promotions/audit.py` (nuevo) | API | `CouponAuditLog` append-only JSONL bajo flock |
| Ventas del cupón | `src/platform/promotions/coupon_sales.py` (nuevo) | API + worker | `CouponSalesReader`: pedidos + drafts de Medusa → unidades vendidas por cupo y resultados por cupón |
| Candado de registro | `src/platform/promotions/quota_lock.py` (nuevo) | API + worker | `flock` por código alrededor de "leer vendidas → repartir → crear draft" |
| Composición | `src/platform/promotions/composition.py` | — | `get_promotions_admin_port`, `get_promo_quota_store`, `get_coupon_sales_reader`, `get_coupon_audit_log` |
| SDK | `src/sdk/connectorkit/__init__.py` + `docs/_sdk/07-connectorkit.md` | — | exportar lo anterior (3 patas: símbolo, consumidor y check/doc) |
| Identidad verificada | `src/platform/auth.py` + export en `src.sdk` | API | `require_auth` deja `request.state.hubara_actor`; helper `current_actor(request)` |
| Pedido en Medusa (Fase 0) | `src/platform/orders/medusa_order.py`, `reconciliation.py` | worker + API | línea con descuento propia, metadata `hubara_coupon`, sin `promo_codes` |
| Tools del bot | `src/plugins/chats/agent/sales/tools/coupons.py`, `order_registration.py`, `ui_intents.py` | worker ventas | cupo en `apply_coupon`, `list_promotions`, `present_order_confirmation`, `register_order` |
| Casos de uso del bot | `src/plugins/chats/agent/sales/use_cases/coupons.py` | worker ventas | snapshot + reparto por unidades |
| Prompts del bot | `src/plugins/chats/agent/sales/workspace/TOOLS.md`, `skills/etapa_cierre/SKILL.md` | worker ventas | color y aroma por ítem con cupón; tono honesto al agotarse (TOOLS.md está al borde de su tope) |
| "Crear pedido" | `src/plugins/chats/api/session_actions.py`, `order_intake.py` | API | mismo reparto y candado |
| API de la central | `src/plugins/marketing/api/coupons.py` (nuevo, incluido en el router de marketing) | API | endpoints §4.6 |
| Plantilla de campaña | `src/plugins/marketing/domain/campaigns.py` | API + worker campañas | `percent` y `valid_until` derivados del cupón elegido |
| Front: entidad | `frontend_dashboard/src/plugins/marketing/frontend/entities/coupon/` (nueva; reemplaza a `promotion`) | — | `api`, `contracts` (Zod), `keys`, `model`, `index` |
| Front: vistas | `…/marketing/frontend/features/{coupons-list,coupon-form,coupon-detail,coupon-sales}/` (nuevas) + `MarketingSection.tsx` | — | selector Campañas / Cupones, lista, formulario, detalle con unidades y resultados, inspector de ventas |
| Front: constructor | `…/marketing/frontend/features/campaign-builder/ui/OfferStep.tsx` | — | elegir cupón de la lista; atajo "Crear cupón" |
| Front: "Crear pedido" | `frontend_dashboard/src/plugins/chats/frontend/features/chats-conversation/ui/CreateOrderAction.tsx` | — | color y aroma por ítem, con las listas del producto |
| Front: Órdenes | `frontend_dashboard/src/plugins/orders/frontend/features/orders-inspector/ui/ItemsPanel.tsx` | — | línea con cupón y precio de lista (Fase 0) |

### 4.2 Qué vive en Medusa y qué vive en Hubara

| Dato | Dónde | Por qué |
|---|---|---|
| Código, nombre de campaña, porcentaje, productos, fechas, estado | **Medusa** (promoción + campaña) | Registro de comercio; visible en Medusa Admin; útil para un checkout futuro |
| Cupo por unidad, `show_units_left` | **Hubara** vault `_promotions/quotas/<promotion_id>.json` | La API de Medusa no deja escribir metadata |
| Registro de cambios | **Hubara** vault `_promotions/audit.jsonl` | Quién y cuándo, con identidad verificada |
| Vendidas y resultados | **derivado** de pedidos Medusa | Sin copias (regla de la casa: datos del pedido = Medusa) |
| Candados | **Hubara** vault `_promotions/locks/<CODE>.lock` | Evitar vender dos veces la última unidad |

### 4.3 El cupón: contrato y mapeo a Medusa

`CouponSpec` (entrada de la central):

| Campo | Regla |
|---|---|
| `code` | `^[A-Z0-9]{3,14}$`, dentro de `COUPON_CODE_RE` del SDK y del tope de 14 caracteres de la plantilla de campaña. Se normaliza a mayúsculas. **Inmutable salvo en borrador.** |
| `campaign_name` | 1–80 caracteres; por defecto el código |
| `percentage` | entero 1–100 |
| `products` | `"all"` o una lista de 1+ `product_id` existentes en el catálogo |
| `starts_on` / `ends_on` | fechas (día) en `America/Bogota`; `ends_on ≥ starts_on`; `ends_on` es **inclusivo** |
| `status` | `draft` o `active` al crear |

Mapeo a `POST /admin/promotions`, por ejemplo AMOR26 creado desde la central:

```json
{
  "code": "AMOR26",
  "type": "standard",
  "is_automatic": false,
  "status": "active",
  "application_method": {
    "type": "percentage",
    "value": 10,
    "target_type": "items",
    "allocation": "across",
    "target_rules": [
      {"attribute": "items.product.id", "operator": "in", "values": ["prod_…", "prod_…", "prod_…", "prod_…"]}
    ]
  },
  "campaign": {
    "name": "AMOR Y AMISTAD 2026",
    "campaign_identifier": "AMOR26",
    "starts_at": "2026-09-22T05:00:00Z",
    "ends_at": "2026-09-28T05:00:00Z"
  }
}
```

- `allocation: across` sin `max_quantity`: Medusa lo prohíbe con `across`, y el tope por unidad lo pone el cupo.
- `products: "all"` → sin `target_rules`.
- Sin presupuesto (`budget`): no se mueve con draft orders.
- `starts_at` = día desde 00:00 −05:00. `ends_at` = **día siguiente** a `ends_on`, 00:00 −05:00. Colombia no tiene
  horario de verano, pero se usa `zoneinfo("America/Bogota")` igual.
- `campaign_identifier` = código (único por construcción).

### 4.4 `CouponView` (Medusa → central) y "gestionable"

- **Estados** (derivados con `now`):
  - `draft` → **Borrador**
  - `active` y `now < starts_at` → **Programado**
  - `active` en ventana → **Activo**
  - `inactive` → **Pausado**
  - `ends_at ≤ now` → **Vencido**, sin importar el estado
- **Gestionable** (D7) ⇔ se cumple todo esto:
  - `type == standard` y `is_automatic == false`
  - `application_method.type == percentage` y `target_type == items`
  - `target_rules` vacío, o exactamente una regla `items.product.id` con `in`
  - `rules` (de la promoción) vacío
  - tiene campaña
- Lo que **no** es gestionable se muestra en **solo lectura**, con el motivo ("tiene regla por etiquetas", "es de monto
  fijo", "sin campaña"). Ninguna escritura toca esas reglas.
- AMOR26 hoy **no es gestionable** porque tiene la regla por etiquetas. `allocation`/`max_quantity` existentes se
  respetan al leer y no se editan en v1.
- El bot sigue aplicando cualquier cupón que `PromotionsPort` sepa leer, sea gestionable o no. La lectura no cambia.

### 4.5 El cupo

- `PromoUnitQuota {id, promotion_id, code, product_id, handle, title, color|null, aroma|null, units, created_at, created_by}`.
- **Clave única:** `(promotion_id, product_id, normalize(color), normalize(aroma))`, normalizando con `normalize_label`.
- **Validación al guardar:**
  - el producto tiene que estar entre los del cupón (o el cupón aplica a todo el catálogo);
  - `color` y `aroma` se validan con `match_option(x, parse_variant_tags(product.tags).colors|aromas)`;
  - cada atributo es obligatorio si el producto tiene esa lista y `null` si no la tiene;
  - `units` es un entero ≥ 1.
- **Vendidas:** Σ `quantity` de las líneas con `metadata.hubara_coupon.quota_id == id`, en `/admin/orders` y
  `/admin/draft-orders` creados desde el inicio de la campaña. Se excluyen:
  - `status == canceled`
  - `metadata.hubara_stage == "cancelled"`
  - `metadata.hubara_test_order == true`
- `units_left = max(units − vendidas, 0)`. Si `vendidas > units` porque el operador bajó las unidades, la central lo avisa.
- **Reparto (puro)** `allocate_units(quotas_left, lines)`:
  - por cada línea, en orden, unidades con descuento = mín(cantidad, quedan de esa combinación);
  - consume lo que queda, así que dos líneas iguales comparten el cupo;
  - una línea sin color o sin aroma cuando el cupo los exige recibe 0 (falla cerrada, motivo `missing_attributes`).
- **Descuento por unidad:** `round(unit_price × pct / 100)` en pesos enteros. El total confirmado es la suma de los
  descuentos por unidad, igual que la Fase 0.
- Un cupón **con** cupo aplica **solo** a las unidades del cupo. Un cupón **sin** cupo aplica como hoy, a sus productos.

### 4.6 API de la central (router de marketing, `require_auth`)

| Método y ruta | Qué hace | Errores |
|---|---|---|
| `GET /api/marketing/coupons` | lista `CouponView` (estado, gestionable, unidades quedan/total, resultados resumidos) | 503 si Medusa no responde |
| `POST /api/marketing/coupons` | crea (spec §4.3) y audita | 422 con motivo; 409 código ocupado; 503 |
| `GET /api/marketing/coupons/{promotion_id}` | detalle + unidades + resultados | 404 |
| `PATCH /api/marketing/coupons/{promotion_id}` | edita campos de la spec; `code` solo en borrador; 409 si no es gestionable | 422, 409, 503 |
| `POST /api/marketing/coupons/{promotion_id}/status` | `{status: active\|inactive}` | 409 si no es gestionable |
| `DELETE /api/marketing/coupons/{promotion_id}` | solo borrador sin ventas: borra promoción + campaña + cupo | 409 si tiene ventas o no es borrador |
| `GET /api/marketing/coupons/{promotion_id}/units` | filas del cupo con vendidas/quedan | — |
| `PUT /api/marketing/coupons/{promotion_id}/units` | reemplaza las filas del cupo (validación §4.5) y `show_units_left` | 422 con motivo por fila |
| `GET /api/marketing/coupons/{promotion_id}/sales` | ventas que consumieron cupo y resultados (pedidos, descuento, unidades) | — |
| `GET /api/marketing/promotions` | se mantiene por compatibilidad hasta migrar el constructor; luego se retira | — |

- **Invalidación de caché:** tras cada escritura, la API limpia la caché del `PromotionsPort` de su propio proceso. El
  worker de ventas ve los cambios en ≤ 60 s. No se agrega evento cross-worker en v1.
- **Actualizaciones no atómicas:** editar toca hasta 3 endpoints (promoción, reglas y campaña). Cada paso fija valores
  absolutos, así que reintentar es idempotente. Si uno falla, la respuesta trae el estado real releído de Medusa y el
  error del paso fallido.

### 4.7 El pedido en Medusa (contrato con la Fase 0)

- Las unidades con descuento van en **su propia línea**:
  - `unit_price = precio − descuento por unidad`
  - `metadata.hubara_coupon = {code, promotion_id, list_unit_price_cop, discount_unit_cop, quota_id|null, color|null, aroma|null}`
- El pedido conserva `metadata.coupon_code` y `metadata.discount_cop`.
- **No se envía `promo_codes`.** Medusa lo vincularía sin descontar o, en un cupón sin reglas, descontaría dos veces.
- Si la Fase 0 ya fijó otros nombres de claves, **gana lo mergeado** y este plan se actualiza. `quota_id`, `color` y
  `aroma` se agregan en la Fase 5.
- **Como quedó implementado:** la Fase 0 (#342) usa claves PLANAS en `items[].metadata`: `coupon_code`,
  `list_unit_price_cop`, `discount_unit_cop`. La Fase 5 agrega `coupon_quota_id` (plana) y `DiscountedUnits.quota_id`.
  Color y aroma no se duplican en la línea: los tiene la fila del cupo (y `variant_label`).

### 4.8 Tools del bot (envelopes)

- **`apply_coupon`** (cupón con cupo):
  - Devuelve `units: [{title, color, aroma, units_left, price_cop, discounted_price_cop}]`, solo con `units_left > 0`.
  - Con `show_units_left = false` omite el número.
  - Motivos nuevos:
    - `quota_exhausted`: todas las filas en 0 → "Las unidades con descuento de X ya se agotaron."
    - `quota_unavailable`: Medusa no responde al leer vendidas; falla cerrada.
  - El rechazo **no corta el turno** (L-24).
- **`list_promotions`:** los cupones con cupo listan sus unidades con cuántas quedan. Los agotados aparecen como
  "agotado", para que el bot responda con honestidad si preguntan por ese código.
- **`present_order_confirmation` y `register_order`:**
  - Los ítems aceptan `color` y `aroma` opcionales, validados contra las listas del producto. Si el valor no existe,
    hay error y no se calcula monto.
  - El resumen separa "1 × Cubo Love Rosado · Café con AMOR26 (−$2.100)" de las unidades a precio normal.
- **`register_order`**, bajo candado:
  - Si el reparto difiere del confirmado, devuelve `quota_changed` con el total nuevo **sin crear draft**, y el bot
    pide confirmación de nuevo.
  - El fingerprint de idempotencia incluye el reparto.

---

## 5. Comportamiento esperado (base para los deltas de specs)

```gherkin
Scenario: crear un cupón de porcentaje desde la central
  Given el operador llena código AMOR27, 10%, 4 productos, del 22 al 27-sep
  When guarda con "Crear y activar"
  Then Medusa tiene la promoción AMOR27 activa con su campaña 2026-09-22T05:00Z → 2026-09-28T05:00Z
  And el registro de cambios dice quién la creó

Scenario: código ocupado
  Given ya existe AMOR26 en Medusa
  When el operador intenta crear AMOR26
  Then la central responde "Ese código ya existe" y no crea nada

Scenario: cupón editado fuera de Hubara
  Given AMOR26 tiene una regla por etiquetas creada en Medusa Admin
  Then la central lo muestra en solo lectura con el motivo
  And ninguna acción de la central modifica sus reglas

Scenario: el cupo aplica solo a la combinación
  Given AMOR26 tiene 5 unidades de Cubo Love · Rosado · Café y quedan 3
  When el cliente pide 1 Cubo Love Rosado Café y 1 Cubo Love Azul Lavanda
  Then solo la primera lleva 10% y la confirmación lo explica

Scenario: parcial (D2)
  Given quedan 1 unidad de Cubo Love · Rosado · Café
  When el cliente pide 2
  Then 1 lleva descuento y 1 va a precio normal

Scenario: agotado
  Given todas las filas de AMOR26 están en 0
  When el cliente da el código
  Then apply_coupon responde quota_exhausted y el bot ofrece el precio normal sin inventar otro descuento

Scenario: se llevaron la última
  Given dos clientes confirmaron la última unidad
  When ambos registran
  Then uno crea el pedido y el otro recibe quota_changed con el total nuevo, sin draft

Scenario: cancelar devuelve la unidad
  Given un pedido con 1 unidad del cupo
  When el pedido se cancela (Medusa o hubara_stage=cancelled) o se marca de prueba
  Then la unidad vuelve a quedar disponible en la siguiente lectura

Scenario: Medusa caído
  When el bot no puede leer las vendidas
  Then no aplica el cupón con cupo y lo dice (quota_unavailable)
```

---

## 6. Plan por fases (TDD)

**Reglas del harness `hubara-dev`** (`hubara-plugin-developer`, `references/00-tdd-law.md`):

- **Cada fila es una vuelta rojo → verde → refactor.** El rojo tiene que fallar con un assert con sentido; un
  ImportError no cuenta. El nombre del test es la especificación.
- **Rojos difíciles** (carrera por la última unidad, cupo cambiado, actualización a medias) → `hubara-tdd-author`.
  **Cierre de cada fase** → `/hubara-gates` + `hubara-gate-reviewer`.
- **Backend:** siempre `cd hubara_agency &&`.
  - Los dummies `MEDUSA_BASE_URL=http://medusa.invalid MEDUSA_ADMIN_TOKEN=ci-dummy OTEL_SDK_DISABLED=true` van en
    `tests/architecture`, `tests/plugins`, `tests/conformance` y el CLI.
  - **Nunca en `tests/platform/`**: con ellos se cuelga en reintentos HTTP.
  - Los tests **nunca tocan la Medusa real**: dobles oficiales + `respx`.
- **Frontend:** `cd frontend_dashboard &&`. vitest se corre local porque CI no lo corre.
- **Rutas PROTECTED:** no se tocan. Cada símbolo nuevo del SDK lleva sus 3 patas (símbolo, consumidor y check/doc).
- **Un PR por fase** o por par de fases, sin PRs apilados. Todo se puede desplegar solo: sin filas de cupo, el
  comportamiento es el de hoy.

### Fase 0 — Totales correctos en Medusa · **en curso en otra sesión** · 1,5–2 d

Al mergearla, verificar que deja:

- [ ] draft sin `promo_codes`, con la línea con descuento propia y la metadata de §4.7;
- [ ] `max_quantity` respetado en porcentaje;
- [ ] `_rebuild_order_args` conservando cupón y líneas;
- [ ] `ItemsPanel` mostrando precio de lista y cupón;
- [ ] la lección L-# "Medusa 2.12.5 no aplica promociones con reglas de producto a draft orders ni suma usos".

### Fase 1 — El cupón: dominio y mapeo · 1 d

`src/platform/promotions/coupon.py` — tests en `tests/platform/promotions/test_coupon_spec.py`

| Test que falla primero | Qué exige |
|---|---|
| `test_percentage_coupon_for_selected_products_maps_to_medusa_payload` | payload §4.3: `across`, sin `max_quantity`, `items.product.id in`, campaña en línea |
| `test_whole_catalog_coupon_sends_no_target_rules` | `products="all"` → sin reglas |
| `test_until_date_is_inclusive_in_bogota` | `ends_on` 2026-09-27 → `ends_at` 2026-09-28T05:00Z |
| `test_from_date_starts_at_midnight_bogota` | `starts_on` 2026-09-22 → 2026-09-22T05:00Z |
| `test_code_must_be_3_to_14_uppercase_letters_or_digits` | normaliza a mayúsculas; rechaza `_`, espacios y más de 14 |
| `test_percentage_must_be_integer_between_1_and_100` | 0 y 101 rechazados con motivo |
| `test_until_before_from_is_rejected` | motivo claro |
| `test_coupon_view_state_is_derived_from_status_and_dates` | borrador, programado, activo, pausado, vencido |
| `test_promotion_with_tag_rule_is_not_manageable` | AMOR26 hoy → solo lectura con motivo |
| `test_fixed_amount_promotion_is_not_manageable` | D8 |

### Fase 2 — Escribir cupones en Medusa · 1,5 d

`src/platform/promotions/admin.py` + métodos del cliente — tests en `tests/platform/promotions/test_promotions_admin_contract.py`
(la misma suite corre contra `FakePromotionsAdmin` y contra `MedusaPromotionsAdmin` con `respx`).

| Test que falla primero | Qué exige |
|---|---|
| `test_create_coupon_posts_one_request_with_inline_campaign` | una sola llamada `POST /admin/promotions`; nada a medias si falla |
| `test_create_coupon_with_taken_code_raises_coupon_code_taken` | el 400 "already exists" → `CouponCodeTakenError` |
| `test_create_coupon_invalid_data_raises_coupon_rejected_with_message` | otros 400 → `CouponRejectedError(mensaje)` |
| `test_update_sends_only_changed_fields` | porcentaje, nombre y fechas a sus endpoints |
| `test_update_products_batches_only_the_product_rule` | crea, actualiza o borra solo `items.product.id` |
| `test_update_refuses_unmanageable_promotion` | D7: ninguna llamada de escritura |
| `test_set_status_pauses_and_resumes` | `inactive` ⇄ `active` |
| `test_delete_refused_unless_draft_without_sales` | borra promoción + campaña solo en ese caso |
| `test_medusa_unreachable_raises_promotions_unavailable` | 5xx o timeout → error de dominio |
| `test_admin_write_clears_local_promotions_cache` | la central ve el cambio al instante |
| Exportar en `src.sdk.connectorkit` + check + `docs/_sdk/07-connectorkit.md` | 3 patas |

### Fase 3 — Dominio del cupo · 1 d

`src/platform/promotions/quotas.py` — tests en `tests/platform/promotions/test_promo_quotas.py`

| Test que falla primero | Qué exige |
|---|---|
| `test_units_left_is_units_minus_sold_never_negative` | 5 − 2 = 3; bajar debajo de lo vendido → 0 con aviso |
| `test_sold_ignores_cancelled_deleted_and_test_orders` | la liberación sale del cálculo |
| `test_quota_key_ignores_accents_and_case` | "cafe" = "Café" |
| `test_allocation_discounts_only_matching_product_color_aroma` | Azul · Lavanda no lleva descuento |
| `test_allocation_is_partial_when_asking_more_than_left` | D2 |
| `test_two_lines_same_combination_share_units_left` | no se gasta dos veces |
| `test_line_without_required_attributes_gets_no_quota_discount` | `missing_attributes` |
| `test_quota_attribute_is_optional_when_product_has_no_list` | producto sin lista de aromas → aroma `null` |
| `test_discount_is_rounded_per_unit_in_whole_pesos` | igual que la Fase 0 |
| `test_resolve_coupon_reports_quota_exhausted` | motivo nuevo en `rules.py` |

### Fase 4 — Almacén, ventas y candado · 1,5–2 d

| Test que falla primero | Qué exige | Dónde |
|---|---|---|
| `test_quota_store_contract` (fake y vault) | alta, edición y baja; clave única; mismo comportamiento | `tests/platform/promotions/test_promo_quota_store_contract.py` |
| `test_vault_quota_store_concurrent_writes_keep_all_rows` | `flock` entre procesos (patrón `tests/platform/test_metadata_store_update.py`) | ídem |
| `test_audit_log_appends_one_line_per_action_with_actor` | JSONL con ts, actor, acción, diff | `tests/platform/promotions/test_coupon_audit.py` |
| `test_sold_units_from_medusa_reads_line_metadata` | mapper puro con shapes reales **saneados** de pedidos y drafts | `tests/platform/promotions/test_coupon_sales.py` |
| `test_coupon_results_sum_orders_discount_and_units` | resultados por cupón | ídem |
| `test_register_with_quota_never_sells_the_last_unit_twice` | dos registros concurrentes → uno `quota_changed` | `tests/platform/orders/` |
| Exportar en `src.sdk.connectorkit` | 3 patas | — |

Los fixtures de Medusa se sanean: sin teléfonos, direcciones ni emails reales. El gate de forge tumba `wa_57\d{10}`.

### Fase 5 — Bot de ventas · 2 d

| Test que falla primero | Qué exige | Dónde |
|---|---|---|
| `test_present_order_confirmation_rejects_aroma_not_in_product` | lista cerrada; sin monto | `tests/plugins/chats/sales/` |
| `test_apply_coupon_lists_eligible_units_with_units_left` | unidades y cuántas quedan | `tests/plugins/chats/sales/test_coupon_tools.py` |
| `test_apply_coupon_hides_units_left_when_preference_off` | D3 como preferencia | ídem |
| `test_apply_coupon_quota_exhausted_is_honest_and_does_not_cut_turn` | L-24 | ídem |
| `test_confirmation_discounts_only_quota_units_in_mixed_order` | Rosado · Café sí, Azul · Lavanda no | `tests/plugins/chats/sales/` |
| `test_register_order_writes_quota_line_metadata` | `quota_id`, `color`, `aroma` en `hubara_coupon` | `tests/plugins/chats/sales/test_register_order_tool.py` |
| `test_register_order_quota_changed_returns_new_total_without_draft` | re-confirmación | ídem |
| `test_list_promotions_shows_units_left_and_marks_exhausted` | honestidad al preguntar por un código agotado | `test_coupon_tools.py` |
| Copys de TOOLS.md y `etapa_cierre` | guarda `test_no_voseo_in_agent_strings.py`; tope de tamaño de TOOLS.md | `chats/agent/sales/workspace/` |

### Fase 6 — "Crear pedido" del dashboard · 1 d

| Test que falla primero | Qué exige | Dónde |
|---|---|---|
| `test_dashboard_create_order_consumes_quota_like_the_bot` | mismo reparto y candado | `tests/plugins/chats/test_session_actions_api.py` |
| `test_order_intake_prefill_shows_quota_discount_per_line` | el formulario muestra qué línea lleva descuento | `tests/plugins/chats/test_order_intake_api.py` |
| `CreateOrderAction` pide color y aroma de las listas del producto | comportamiento del formulario (vitest) | `…/chats-conversation/ui/CreateOrderAction.test.tsx` |

### Fase 7 — La central de cupones en Marketing · 3–4 d

**7.0 Identidad verificada** (para el registro de cambios, D6)

| Test que falla primero | Qué exige | Dónde |
|---|---|---|
| `test_require_auth_sets_verified_actor_from_cognito_claims` | `request.state.hubara_actor` = `username` del access token | `tests/platform/test_auth_actor.py` |
| `test_service_token_actor_is_service` | "service" | ídem |
| `test_dev_without_cognito_actor_is_local` | "local" | ídem |
| Exportar `current_actor` en el SDK | 3 patas | `src/sdk/` + doc |

**7.1 API** — `src/plugins/marketing/api/coupons.py`, tests en `tests/plugins/marketing/test_coupons_api.py`
(`dependency_overrides` con los dobles oficiales, patrón de `test_marketing_api.py`)

| Test que falla primero | Qué exige |
|---|---|
| `test_post_coupon_creates_it_in_medusa_and_audits_actor` | crea con el puerto de comandos; registro con el actor verificado |
| `test_post_coupon_invalid_percentage_returns_422_with_reason` | mensaje que dice cómo corregirlo |
| `test_post_coupon_taken_code_returns_409` | "Ese código ya existe" |
| `test_patch_coupon_code_refused_unless_draft` | inmutable fuera de borrador |
| `test_patch_unmanageable_coupon_returns_409_with_reason` | D7 |
| `test_delete_coupon_with_sales_returns_409` | pausar en vez de borrar |
| `test_put_units_rejects_color_not_in_product_tags` | 422 por fila |
| `test_put_units_rejects_product_outside_coupon` | 422 por fila |
| `test_get_coupon_sales_returns_orders_discount_and_units` | resultados |
| `test_campaign_uses_coupon_percent_and_inclusive_end_date` | `percent` y `valid_until` ("27 de septiembre") salen del cupón |

**7.2 Frontend** — plugin `marketing` (FSD; solo `/api/marketing/…`; íconos del set o agregados con su gate)

| Test que falla primero | Qué exige |
|---|---|
| `entities/coupon/contracts.test.ts` | Zod parsea el shape real del backend (fixture copiado de la respuesta de la API) |
| `CouponForm`: valida código, porcentaje y fechas inline; muestra "el último día cuenta completo" | formulario |
| `CouponsList`: filtra por estado; muestra quedan/total | lista |
| `CouponDetail`: unidades con vendidas/quedan; agregar fila con selectores filtrados por el producto; resultados | detalle |
| `CouponDetail` en solo lectura con motivo cuando no es gestionable | D7 |
| `CouponSalesInspector`: ventas que consumieron cupo, con enlace al pedido | inspector |
| `MarketingSection`: selector Campañas / Cupones que conserva la selección de cada vista | shell de la sección |
| `OfferStep`: elige el cupón de la lista (activos y programados); ya no acepta texto libre; atajo "Crear cupón" | constructor |

### Fase 8 — Cierre · 1 d

- **Deltas de specs:**
  - `.hubara/specs/plugins/marketing/spec.md`: central de cupones, gestionable/solo lectura, fechas y registro de cambios.
  - `.hubara/specs/agents/sales-worker/spec.md`: cupo, parcial, agotado y última unidad.
  - `.hubara/specs/plugins/orders/spec.md`: cancelar devuelve la unidad; línea con cupón.
- **Documentación:** `docs/_sdk/07-connectorkit.md` (comandos de promociones, cupo, ventas del cupón) y `CODEMAP.md` (nuevas piezas).
- **Gates:** `/hubara-gates` completo, `hubara-gate-reviewer` y `npm test` local.
- **Verificación en vivo** en el stack Docker (`:5174`, HMR sobre el checkout `main`). Crear un cupón desde la central
  **escribe en la Medusa de producción** si el stack apunta a ella: pedir permiso explícito, o usar un Medusa de pruebas.
  Recorrido: crear cupón de prueba → poner 2 unidades → pedir por el bot → ver quedan 1 → cancelar → ver quedan 2 → borrar.
- **Lecciones:** registrar en `ARCHITECTURE_FINAL_fable.md §9` las lecciones nuevas que aparezcan en la
  implementación, cada una con su guard.

### Resumen de esfuerzo

| Fase | Días | Depende de |
|---|---|---|
| 0 Totales (en curso) | 1,5–2 | — |
| 1 Dominio del cupón | 1 | — |
| 2 Escritura en Medusa | 1,5 | 1 |
| 3 Dominio del cupo | 1 | 0 (formato de línea) |
| 4 Almacén, ventas y candado | 1,5–2 | 3 |
| 5 Bot | 2 | 0, 3, 4 |
| 6 "Crear pedido" | 1 | 5 |
| 7 Central (API + front) | 3–4 | 1, 2, 4 |
| 8 Cierre | 1 | todas |
| **Total sin Fase 0** | **≈ 12–14** | |
| **Primer corte útil** (1–5 + central básica: crear, editar, pausar, unidades) | **≈ 10** | |

---

## 7. Verificación (comandos)

```bash
# Backend: plataforma, SIN los dummies
cd hubara_agency && uv run lint-imports
cd hubara_agency && uv run pytest tests/platform/promotions tests/platform/orders -q

# Backend: plugins, arquitectura, TCK y CLI, CON los dummies
cd hubara_agency && MEDUSA_BASE_URL=http://medusa.invalid MEDUSA_ADMIN_TOKEN=ci-dummy OTEL_SDK_DISABLED=true uv run pytest tests/plugins/chats/sales tests/plugins/marketing -q
cd hubara_agency && MEDUSA_BASE_URL=http://medusa.invalid MEDUSA_ADMIN_TOKEN=ci-dummy OTEL_SDK_DISABLED=true uv run pytest tests/architecture tests/plugins tests/conformance -q
cd hubara_agency && MEDUSA_BASE_URL=http://medusa.invalid MEDUSA_ADMIN_TOKEN=ci-dummy OTEL_SDK_DISABLED=true uv run python -m src.sdk.cli check

# Frontend
cd frontend_dashboard && npm run plugins:sync && npx tsc -b && npm run test:arch && npm test
```

Los tests nuevos de `tests/platform/promotions` usan dobles y `respx`: no hacen llamadas reales a Medusa.

---

## 8. Despliegue

1. **Orden de merge:** Fase 0 → 1+2 → 3+4 → 5+6 → 7 → 8. Cada PR se puede desplegar solo.
   - Sin filas de cupo, el bot se comporta como hoy.
   - Sin la Fase 7, nadie puede crear filas.
2. **Servicios a reconstruir** por fase:
   - `api`: fases 2, 4, 6 y 7.
   - `worker-sales`: fases 0, 5 y 6, además de las tools.
   - `worker-orders`: la reconciliación relee el cupo, guarda solo su record y no se cuenta a sí misma (premortem).
   - `worker-marketing-campaigns`: si cambia la plantilla (7.1) y porque el envío programado revalida el cupón (A12).
   - Deploy del frontend: fases 6 y 7.
   - El fingerprint de un pedido ahora incluye color/aroma cuando existen. Un reintento de un pedido registrado ANTES
     del deploy (sin color/aroma) no reusaría ese draft: desplegar con la cola de registros fallidos vacía o revisarla.
3. **Rollback:** revertir el PR de la fase. Los cupones creados en Medusa siguen existiendo y funcionan con el camino de
   lectura de siempre. Las filas de cupo en el vault quedan inertes.
4. **Deploys en serie:** no lanzar deploys paralelos del backend. Ya tumbaron el proxy de LLM una vez.

---

## 9. Operación mientras tanto (AMOR26 — D4, D5)

Son acciones del operador en Medusa Admin; no son desarrollo.

- **Alcance de estos días.** Desde #340 (desplegado el 23-sep a las 17:22) el bot aplica 10% a los 4 productos en
  cualquier color y aroma. Si eso no es aceptable, dejar en el cupón solo los productos con unidades hechas. Pasar
  AMOR26 a `inactive` cuando se acaben las unidades: el bot lo nota en ≤ 60 s.
- **Totales.** Hasta que salga la Fase 0, corregir el total de cada pedido con AMOR26 en Medusa (el #44: línea a
  $18.900 c/u, total $45.700) o verificar las transferencias contra el total que confirmó el bot.
- **Fecha fin.** Si el 27-sep cuenta, mover el fin de la campaña al 28-sep 00:00.
- **Regla por etiquetas.** Se puede quitar: no restringe nada. Mientras exista, AMOR26 no será gestionable desde la central.

---

## 10. Riesgos

| Riesgo | Mitigación |
|---|---|
| Hubara escribe en la Medusa de producción | Puerto de comandos solo en la API; tests con dobles y `respx`; registro de cambios con identidad verificada; la primera prueba real requiere permiso |
| Edición en varios pasos queda a medias | Pasos idempotentes con valores absolutos; la respuesta trae el estado releído de Medusa; reintentar es seguro |
| Alguien edita en Medusa Admin | Detección de "gestionable" (§4.4); solo lectura con motivo; nunca se borra una regla ajena |
| Cualquier usuario puede crear o pausar cupones (D6) | Registro de cambios visible en cada cupón; roles quedan para después |
| Captura de color y aroma por el bot | Listas cerradas del producto; confirmación explícita; falla cerrada y el bot pregunta |
| Dos clientes y la última unidad | Candado por código + lectura fresca al registrar + `quota_changed` |
| Medusa caído | El cupón con cupo no se aplica (falla cerrada); la central muestra error y no crea nada |
| Pedidos a mano en Medusa Admin | No cuentan; el operador ajusta las unidades |
| Deriva de nombres con la Fase 0 | §4.7: gana lo mergeado; este plan se actualiza |
| Drafts sin pagar retienen unidades del cupo | Cuentan como vendidos hasta que se cancelan (Medusa o Hubara): el operador cancela los pedidos abandonados |
| Paginación por offset mientras entran pedidos | Un draft borrado entre dos páginas puede correr la lista y no contarse (subconteo de 1). El filtro `created_at[$gte]` achica la ventana |

---

## 11. Pendientes

- Confirmar **D2** (parcial) y **D3** (número exacto). El plan usa los defaults; cambiar D2 solo cambia
  `allocate_units` y su test, y D3 es una preferencia por cupón.
- Alinear las claves de metadata de línea con lo que mergee la Fase 0 (§4.7).
- Decidir si "Crear cupón" desde el constructor abre la vista Cupones o un modal. Se resuelve en la Fase 7.2 con el
  operador mirando la maqueta.

---

## 12. Estado de implementación (2026-09-24)

Rama `claude/coupon-central` (worktree `intelligent-rhodes-82a5f3`). `main` ya trae #342 (Fase 0, mismo árbol que
el que la rama tenía por merge) y #353 (envío = tarifa publicada; rechazos de `register_order` con `error`): ambos
mergeados a la rama el 24-sep. #346 (sin cupones de envío) también, el mismo día: el bot y el builder de campañas no
ofrecen cupones de envío y la central los muestra sin % (ni cupo por unidad ni campaña).

| Fase | Estado | Dónde |
|---|---|---|
| 0 Totales | PR #342 (otra sesión), incluido por merge | `platform/orders/medusa_order.py`, `rules.py` |
| 1 Dominio del cupón | ✅ | `platform/promotions/coupon.py` |
| 2 Escritura en Medusa | ✅ | `platform/promotions/admin.py`, `medusa/client.py` (sin reintento de escrituras no idempotentes tras timeout) |
| 3 Dominio del cupo | ✅ | `platform/promotions/quotas.py` |
| 4 Almacén, ventas, candado | ✅ | `quota_store.py` (`counting_since`, falla cerrada), `coupon_sales.py`, `audit.py`, `quota_lock.py` |
| 5 Bot | ✅ | `apply_coupon`/`list_promotions` (`use_cases/coupon_quota.py`), `present_order_confirmation` (color/aroma por ítem, reparto, reparto confirmado), `register_order` (relee bajo candado → `quota_changed`/`quota_busy`), `etapa_cierre` + `TOOLS.md` |
| 6 "Crear pedido" | ✅ | `chats/api/session_actions.py`, `order_intake.py`, `CreateOrderAction.tsx` |
| 7.0 Actor verificado | ✅ | `platform/auth.py` (`current_actor`) → `src.sdk.castkit` |
| 7.1 API de la central | ✅ | `plugins/marketing/api/coupons.py`, `domain/coupons.py`; la campaña toma % y "válido hasta" del cupón |
| 7.2 Front de la central | ✅ | `frontend_dashboard/src/plugins/marketing/frontend/{entities/coupon,features/coupons-list,coupon-form,coupon-detail,coupon-sales}` + `MarketingSection` + `OfferStep` |
| 8 Cierre | ✅ docs/specs/lección · ⏳ verificación en vivo | specs marketing/chats/orders, `docs/_sdk/07` y `10`, `CODEMAP.md`, L-27 |

**Cambios respecto del diseño:**
- El candado no usa un helper genérico: `register_order` corre su cuerpo bajo `VaultQuotaLock.hold(código)` y compara el
  reparto fresco con el reparto guardado en `present_order_confirmation` (`episodes[-1].coupon_confirmed_split`).
- Las filas del cupo se validan contra el alcance REAL del cupón (productos + etiquetas, vía `PromotionsPort`), y se
  permiten en cupones de porcentaje de solo lectura (AMOR26): el cupo vive en Hubara.
- Las vendidas se cuentan desde `QuotaSheet.counting_since` (primer guardado, nunca avanza, −1 h de margen de reloj).
- Color y aroma: el LLM los manda por ítem; si no, se toman del borrador estructurado (`order_draft.items`).
- Dos revisiones de gates (`hubara-gate-reviewer`), ambas con todos los gates verdes y hallazgos reales:
  - 1ª: cupo a medias, alcance por etiquetas, archivo roto = sin límite, reintentos de escrituras, etc. → commit
    "revisión de gates", lección **L-27**. Quedan anotadas: precio por variante (hoy todas son "Unico") y flock
    breve dentro de handlers async.
  - 2ª: el reintento del mismo pedido en la última unidad se duplicaba (HIGH), doble envío en "Crear pedido" (HIGH),
    reconciliación que podía sobrevender, formulario que confirmaba otro total → commit "segunda revisión",
    lección **L-28**.

### Premortem de toda la solución (2026-09-24)

Cuatro revisores en paralelo (central + escrituras en Medusa, cupo + tools del bot, registro + reconciliación,
front) sobre la rama ya con #342/#353: ~45 hallazgos verificados (CONFIRMED/PLAUSIBLE), todos arreglados con TDD
salvo lo anotado abajo. Lo que habría pasado en producción:

- **Tras #353**, los rechazos del cupo sin `error` → el bot escalaba un "se llevaron la unidad" como falla de Medusa
  (L-29, guarda estática sobre todos los `registered: False`).
- **Cupo ilegible** (Medusa/vault) informado como `quota_changed` con el precio lleno como "total nuevo", en el bot y
  en "Crear pedido" → ahora queda para reconciliación / `quota_unavailable` (L-30).
- **Pedidos SIN cupón rechazados**: la validación nueva de color/aroma contra las etiquetas corría en todos
  (`set_order_slot` guarda valores fuera de las etiquetas) → solo valida en productos con cupo (L-31).
- **Sin color/aroma la tarjeta salía a precio lleno** y terminaba el turno → `missing_variant_attributes`; la
  tarjeta dice qué unidades llevan el cupón (L-31).
- **Color y aroma nunca llegaban a Medusa** (productos "Unico"): ahora van en la línea, en el detalle del pedido y
  en el fingerprint.
- **Reintentos**: el reparto se compara por producto + color + aroma + precio (no por posición); la reconciliación no
  cuenta su propio draft, relee bajo el candado, guarda solo su record (antes revertía un traspaso a humano) y no
  corta el barrido; `create_draft_order` ya no se reintenta tras un timeout (duplicaba drafts).
- **Central**: escrituras a medias dichas como "sin cambios", renombre a un código ocupado en otras mayúsculas, dos
  editores de unidades pisándose (`expected_updated_at` → 409), campaña de Medusa compartida, actor = UUID de Cognito
  (ahora el email del ID token verificado), envío programado que no revalidaba el cupón.
- **Front**: "Crear pedido" en bucle tras `quota_changed` y registrando totales que nadie vio (ahora `dry_run` y
  cotización adoptada), el cupón que desaparecía a $0, ediciones perdidas en la central.

Verificación: backend `tests/platform` 1.670, `architecture + plugins + conformance` 2.830, resto 1.108 (+2 skip),
`lint-imports` 6/6, `sdk.cli check` OK; front `tsc` limpio, `test:arch` 23, `npm test` 1.270.

**Pendiente:**
1. PR (o dos: backend+central y front) y deploy EN SERIE: `api`, `worker-sales`, `worker-orders` (reconciliación),
   `worker-marketing-campaigns` (revalida el cupón al disparar) y frontend (§8).
2. **Filtro `created_at[$gte]` en Medusa 2.12.5 sin verificar en vivo** (la lectura de prod quedó bloqueada por el
   clasificador de permisos). Es seguro igual: si Medusa responde 400 se lee todo y se corta de este lado. Verificar
   con un GET de solo lectura (`/admin/draft-orders?limit=1&created_at[$gte]=…`) cuando haya permiso.
3. Verificación en vivo: crear un cupón de prueba **escribe en la Medusa de producción** → permiso explícito del
   operador (o un Medusa de pruebas). Recorrido: crear cupón → 2 unidades → pedido por el bot → quedan 1 → cancelar
   → quedan 2 → borrar. Tampoco hubo verificación visual: `:5174` sirve `main`.
4. Confirmar D2 y D3 con el operador.
5. Conocidos, sin arreglar: una campaña `failed` no se re-envía (hay que recrearla); los drafts sin pagar retienen
   unidades hasta cancelarse (§10); la paginación por offset puede subcontar 1 si se borra un draft entre páginas.
6. AMOR26: mientras tenga la regla por etiquetas se ve en solo lectura (se le puede poner cupo igual).
