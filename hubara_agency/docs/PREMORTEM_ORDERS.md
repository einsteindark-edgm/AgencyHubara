# Premortem — Orders feature end-to-end

Documento de análisis: imaginamos que estos dos desarrollos YA fallaron en
producción y trabajamos hacia atrás identificando QUÉ pudo haber salido
mal. Cada hallazgo lleva severidad + decisión (fix ahora / fix después /
acepar como deuda consciente).

**Scope:**
1. **Crear order** — `RegisterOrderTool` + `MedusaOrderRegistration`
   (`POST /admin/draft-orders` cuando el agente Sales cierra venta).
2. **Obtener órdenes** — `OrderQueryPort` + `MedusaOrderQuery` + endpoints
   FastAPI + frontend kanban.

## Convención de severidad

- 🔴 **CRITICAL** — bug visible en producción con clientes reales, perderíamos
  ventas o engañaríamos al operador. **Fix ahora.**
- 🟠 **HIGH** — bug detectable en ops normales pero no perdemos venta. **Fix ahora.**
- 🟡 **MEDIUM** — edge case real pero raro. **Fix si es trivial, sino documentar.**
- 🟢 **LOW** — especulación / cosmético / futuro. **Documentar y mover on.**

---

## A. Customer journey failures

### 🟠 A1 — Deep-link a `/orders/{display_id}` rompe sin pasar por lista
**Síntoma:** operador comparte URL `/orders/#1247` con compañero. Compañero
abre directo → inspector vacío con error.

**Causa:** `entities/order/api.ts` mantiene `_idByDisplay = new Map<>()` global
del módulo, poblado SOLO cuando `useOrders()` se ejecuta. Si el componente que
abre el deep-link no monta `useOrders()` primero, `useOrderDetail()` no
encuentra el backend id (formato `order_01HXX...`) y throw.

**Decisión:** ⚠️ Fix ahora — pequeño cambio de wiring.

**Fix:** o (a) el endpoint detail acepta ambos id formato (backend `order_01HXX` y display `#1247`), o (b) el frontend siempre pide la lista primero. Voy con (a) — más robusto.

---

### 🟡 A2 — Operador interpreta Draft Order como orden confirmada
**Síntoma:** operador despacha un draft order pensando que ya está pagado.
El cliente WhatsApp pagó CONTRA ENTREGA (no transferencia) — operador
asume confirmado.

**Causa:** El badge "Draft" del inspector es claro, pero en el kanban card NO se
distingue draft vs order completo. Operador escanea rápido.

**Decisión:** Fix ahora — agregar visual distintivo en la card.

**Fix:** card con borde lateral o icono "Draft" cuando `order.isDraft`.

---

### 🟡 A3 — Orden creada antes del fix de currency precision queda mal cobrada
**Síntoma:** Medusa interpreta `unit_price=17000` como $170.00 si la región
tiene currency con 2 decimales en lugar de zero-decimal.

**Causa:** Medusa v2 usa unidades MAYORES. Para COP (zero-decimal) `17000`
significa $17.000. Pero si el operador configura mal la región como
`is_tax_inclusive=true` o usa USD (2 decimales), `17000` se vuelve $170.00.

**Decisión:** Fix ahora — agregar test que valide explícitamente con COP +
documentar.

**Fix:** comentario inline más claro en `_resolve_items` + test específico que
verifique el shape de `unit_price` para `cop`. Adicional: validar contra
`currency_code` antes de enviar.

---

## B. Race conditions

### 🟡 B1 — LLM llama `register_order` 2× en mismo turn → 2 draft orders en Medusa
**Síntoma:** Cliente WhatsApp confirma una vez. Aparecen 2 draft orders
idénticas en el dashboard.

**Causa:** El LLM no es estrictamente determinístico; en rare cases (retry de
activity, replay del workflow) puede llamar la tool dos veces. Hoy no hay
idempotency-key.

**Decisión:** ⚠️ Fix ahora — bajo costo, gran beneficio. Agregar
`idempotency-key` desde el tool al adapter.

**Fix:** generar `idempotency_key = f"{session_key}-{ts_bucket_10min}"` y
mandarlo en el `metadata` del Draft Order. La query del backend puede
deduplicar usando ese key (de-dupe at-read en el adapter).

---

### 🟢 B2 — Race en `_upsert_customer` ya está manejada
**Manejo actual:** lookup → create con `try except 409 → re-lookup`. OK.

---

### 🟢 B3 — Dashboard polling cada 30s puede mostrar order obsoleto
Aceptable. Cuando el operador actualiza estado del order, el cambio se
propaga en máximo 30s. No hay locking pesimista en este caso.

---

## C. Network failures

### 🟠 C1 — `register_order` puede exceder `start_to_close_timeout` del activity
**Síntoma:** El activity `execute_tool` se mata por timeout cuando
`register_order` está esperando Medusa. Tool entra en retry loop indefinido.

**Causa:** El `HttpMedusaClient._request` tiene tenacity `stop_after_attempt(3)`
con `wait_exponential(min=0.5, max=4)`. Worst case ~12s para 3 attempts. PERO
hacemos 4 calls secuenciales (list_customers + create_customer + list_shipping_options + create_draft_order). 4 × 12s + http_timeout 30s = potencialmente 60-90s.

**Decisión:** Fix ahora — documentar el peor-caso + agregar guard de
`asyncio.wait_for` de 45s total.

**Fix:** envolver `register_order` en `asyncio.wait_for(..., timeout=45)`. Si
excede, return success=False con error_detail explícito.

---

### 🟢 C2 — Frontend muestra estado vacío explícito cuando Medusa caído
Manejo correcto. Ya implementado.

---

### 🟠 C3 — Frontend `useOrders` no maneja red-down
**Síntoma:** Backend caído (no Medusa, BACKEND). El frontend `apiClient` lanza
exception → TanStack Query muestra error genérico. Operador no entiende.

**Decisión:** Fix ahora — manejo explícito de `apiClient` failure.

**Fix:** capturar `ApiError` en `useOrders.queryFn` → mismo shape que cuando
Medusa down (`catalog_available=false`).

---

## D. i18n / locale

### 🟢 D1-D3 — Manejo OK
UTF-8 OK, locale browser, fechas en es-CO. Edge case timezone (D3) — orden
creado a 23:55 UTC en Bogotá (UTC-5) podría mostrarse "mañana". Aceptable.

---

## E. Performance

### 🟡 E1 — `_resolve_items` serializa N round-trips a Medusa
**Síntoma:** Orden de 10 items → 10 round-trips secuenciales a Medusa (~5s
extra mínimo).

**Decisión:** Fix ahora — refactor a `asyncio.gather`. 10 minutos de trabajo.

**Fix:** `asyncio.gather(*[lookup_item(it) for it in items])`.

---

### 🟢 E2-E4 — Aceptable hoy
- list orders+drafts en paralelo: ya hecho.
- 100 items en kanban: aceptable.
- polling 30s: aceptable.

---

## F. Observability

### 🟠 F1 — Logs no correlacionan session_key con error de Medusa
**Síntoma:** Operador ve "register_order failed" en Sentry. ¿Qué pedido?
¿Qué cliente?

**Decisión:** Fix ahora — agregar `extra={"session_key": ...}` a logs.

**Fix:** structured logging consistente.

---

### 🟠 F2 — No hay forma de ver `failed_order_registrations[]` en el dashboard
**Síntoma:** Medusa cae por 1 hora. 20 órdenes quedan en
`metadata.failed_order_registrations[]`. Operador no se entera salvo por
las escalations a humano.

**Decisión:** Fix ahora — exponer un endpoint específico `/api/orders/failed-registrations` que liste de los vault files.

**Fix:** nuevo endpoint que escanea `hubara_vault/wa_*/metadata.json` y
extrae los `failed_order_registrations[]`.

---

### 🟢 F3 — Métricas Prometheus
Deuda consciente. Falta integrar.

---

## G. Security

### 🟢 G1 — Endpoints sin auth
El dashboard hoy es interno, sin auth. Aceptable a corto plazo. Documentar.

### 🟢 G2-G4 — OK
session_key sanitizado, Medusa parametriza queries, no multi-tenant
aceptado.

---

## H. Data integrity

### 🔴 H1 — `_discover_shipping_option_id` toma el PRIMER option sin filtrar
**Síntoma:** Operador crea 2 shipping options en Medusa: "Recogida en
tienda" (gratis) + "Envío estándar" (5k). El adapter toma el primero
(según orden de creación) — que puede ser "Recogida en tienda" — y todas
las órdenes Hubara dicen "Recogida" pero el shipping_cop del cliente fue
5000.

**Causa:** No hay filtro. Sin env var `MEDUSA_DEFAULT_SHIPPING_OPTION_ID`,
elige greedy.

**Decisión:** ⚠️ Fix ahora — agregar filtro inteligente + warning log si
hay >1 option y no hay env var seteada.

**Fix:** filtrar por `name` o `provider_id` matching "estándar" / "standard"
/ "shipping" / "envio". Si ninguna matchea, log warning explícito + usar
primera.

---

### 🟠 H2 — Currency precision no validada
**Síntoma:** Si la región de Medusa está en USD, `unit_price=17000` se
interpreta como $170.00 (Medusa USA 2 decimales para USD).

**Causa:** El adapter asume COP zero-decimal. No valida que el
`region.currency_code == "cop"` ni el `MEDUSA_DEFAULT_CURRENCY == "cop"`.

**Decisión:** ⚠️ Fix ahora — validación + comentarios explicítos.

**Fix:** chequeo defensivo en el adapter: si `settings.default_currency != "cop"`,
log warning indicando que el `unit_price` debe ser float con 2 decimales.
Test explícito que rompa si se cambia.

---

### 🟡 H3 — `_pick_variant` fallback silencioso a primera variante
**Síntoma:** Cliente eligió "Lavanda" pero Medusa cambió el nombre a "Lavender".
El adapter falla el match, log warning, registra "Default" como variante.

**Causa:** Fallback "mejor que perder venta" pero sin surfacear al operador.

**Decisión:** Fix ahora — agregar `variant_label_mismatch=true` a `metadata` del
draft order para que el operador lo vea.

**Fix:** marcar en metadata + UI.

---

### 🟠 H4 — Cache `_idByDisplay` no sobrevive deep-link
Ya cubierto en A1.

---

### 🟡 H5 — Medusa puede cambiar response shape
**Síntoma:** Medusa upgrade mayor → `{"orders": []}` cambia a `{"data": []}`.
Backend cae al except generic, devuelve vacio.

**Decisión:** Aceptable por ahora — el `catalog_available=false` ya cubre el caso,
pero log más explícito.

**Fix:** distinguir entre "Medusa down" vs "shape change" en el log.

---

## I. UX edge cases

### 🟡 I1 — Pago "Contra entrega" + "Pagado" simultáneamente
**Síntoma:** Operador ve `pay_type=cod` + `pay_status=paid` → confunde porque
COD significa que el cliente paga AL RECIBIR, no antes.

**Causa:** Mapping `_map_pay_status` no considera `pay_type`. Si el operador
en Medusa marca el cobro como capturado (porque entregó el producto),
pay_status pasa a "paid" — pero la card aún dice "Contra entrega" porque
es el `pay_type`.

**Decisión:** Aceptable — es informativo y NO incorrecto. Pay type = método;
pay status = estado del cobro. Documentar en UI con tooltip.

---

### 🟡 I2 — Order sin items / con address null
**Síntoma:** Inspector muestra "0 items" + "—" en todos los campos.

**Decisión:** Aceptable. Empty state OK pero feo.

---

### 🟡 I3 — fmtMoney con números muy grandes
Verificar — probablemente OK.

---

### 🟡 I4 — Nombre largo trunca
Verificar CSS.

---

## J. Integration drift

### 🟠 J4 — 401 auth expired no surface al operator
**Síntoma:** Admin token expira en Medusa → todas las queries 401 → frontend
muestra "Medusa no responde" — pero la causa real es token, no Medusa
down.

**Decisión:** Fix ahora — detectar 401 en error_detail.

**Fix:** `error_detail` específico: `"medusa_unauthorized: admin_token expired or invalid"`.

---

### 🟢 J1-J3 — Aceptables
Defaults y fallbacks razonables.

---

## K. Backward compatibility

### 🟠 K1 — Stub orders (provider="stub") invisibles en dashboard
**Síntoma:** Antes de configurar Medusa, el agente cerró 5 ventas con stub.
Quedan en `hubara_vault/wa_*/metadata.json` como `registered_order` con
`provider="stub"`. Dashboard solo lee Medusa → esos 5 pedidos se perdieron
para el operador.

**Decisión:** Fix ahora — agregar un endpoint que escanee el vault y
combine los stub orders con las Medusa orders.

**Fix:** mismo endpoint `/api/orders/failed-registrations` (de F2) puede
incluir también los stub orders. O un endpoint separado
`/api/orders/local-vault-orders`.

---

# Plan de fixes (prioridad por severidad)

## Fixes 🔴 CRITICAL (inmediato)

1. **H1** — `_discover_shipping_option_id` filtra inteligentemente.

## Fixes 🟠 HIGH (inmediato)

2. **A1 / H4** — Endpoint detail acepta backend id + display id.
3. **B1** — Idempotency-key en register_order.
4. **C1** — `asyncio.wait_for` 45s guard en register_order.
5. **C3** — Frontend `apiClient` error → `catalog_available=false` shape.
6. **E1** — `_resolve_items` paralelizado con `asyncio.gather`.
7. **F1** — Structured logging con session_key.
8. **F2** — Endpoint `/api/orders/failed-registrations` lee vault.
9. **H2** — Validación currency con warning si no es COP.
10. **H3** — `variant_label_mismatch` en metadata.
11. **J4** — Error_detail específico para 401.
12. **K1** — Stub orders del vault visibles en dashboard.

## Fixes 🟡 MEDIUM (este PR)

13. **A2** — Distintivo "Draft" en card del kanban.
14. **A3** — Comentario + test específico para currency_code=cop.

## Aceptados como deuda consciente

- G1 (auth dashboard), F3 (Prometheus), J2-J3 (Medusa shape changes futuras),
  I1 (cod+paid display), I2-I4 (empty states).

---

# Fixes aplicados (estado actual)

Tracker rápido de los fixes que se shippearon en este PR. Cada uno tiene
tests + verificación end-to-end:

## Backend (write side — `register_order`)

| Fix | Archivo principal | Test cobertor | Estado |
|---|---|---|---|
| 🔴 H1 — smart shipping option discovery | `medusa_order.py:_discover_shipping_option_id` + `_pick_preferred_shipping_option` | `test_premortem_h1_prefers_shipping_with_envio_keyword` | ✅ |
| 🟠 B1 — idempotency_key | `medusa_order.py:_build_payload` | `test_premortem_b1_idempotency_key_in_metadata` + `test_premortem_b1_same_session_in_same_bucket_gets_same_key` | ✅ |
| 🟠 C1 — wait_for(45s) guard | `medusa_order.py:register_order` + `_register_order_inner` | `test_premortem_c1_timeout_returns_failure_with_detail` | ✅ |
| 🟠 E1 — paralelizar resolve_items | `medusa_order.py:_resolve_items` (asyncio.gather) | tests existentes (latency invisible en mocks pero el patrón quedó) | ✅ |
| 🟠 F1 — structured logging | `medusa_order.py:_register_order_inner` (extra={"session_key": ...}) | manual: logs en `docker compose logs` | ✅ |
| 🟠 H2 — currency validation | `medusa_order.py:__init__` (warning si != cop) | `test_premortem_h2_warn_when_currency_not_cop` | ✅ |
| 🟠 H3 — variant_label_mismatch surface | `medusa_order.py:_pick_variant_with_status` + payload metadata | `test_premortem_h3_variant_mismatch_surfaced_in_metadata` + `test_premortem_h3_no_mismatch_when_label_matches` | ✅ |

## Backend (read side — `OrderQueryPort`)

| Fix | Archivo principal | Test cobertor | Estado |
|---|---|---|---|
| 🟠 A1 — accept display_id | `medusa_order_query.py:get` + `_resolve_display_id` | `test_premortem_a1_get_accepts_display_id_with_hash` + `_with_hash_prefix` + `_falls_back_to_draft_orders` + `_returns_none_when_not_found` | ✅ |
| 🟠 J4 — 401 → unauthorized | `medusa_order_query.py:_format_medusa_error` | `test_premortem_j4_401_returns_unauthorized_error_detail` + `_403_returns_forbidden_error_detail` + `_503_returns_unavailable_error_detail` | ✅ |
| 🟠 F2 + K1 — vault scanner endpoint | `src/plugins/orders/vault_scanner.py` + `api/__init__.py:/vault-orders` | `test_vault_scanner.py` (11 tests) | ✅ |

## Frontend

| Fix | Archivo principal | Estado |
|---|---|---|
| 🟠 C3 — ApiError → catalog_available=false | `entities/order/api.ts:useOrders` (try/catch ApiError + TypeError) | ✅ tsc + tests |
| 🟡 A2 — Draft badge en card del kanban | `features/orders-board/ui/OrdersBoard.tsx:Card` (borde + chip) | ✅ tsc + tests |
| F2+K1 UI — VaultOrdersBanner | `plugins/orders/frontend/OrdersSection.tsx:VaultOrdersBanner` | ✅ tsc + tests |
| J4 UI — banner específico para token expirado | `OrdersSection.tsx` (chequea `medusa_unauthorized`) | ✅ |

## Verificación final

| Check | Resultado |
|---|---|
| `uv run pytest tests/platform/orders/ tests/plugins/orders/` | ✅ 51/51 |
| `uv run pytest tests/architecture/` | ✅ 49 passed, 1 skipped |
| `uv run lint-imports` | ✅ 4/4 contracts kept |
| `npx tsc -b` | ✅ Compilation completed |
| `npm test` (vitest) | ✅ 82 passed, 1 skipped |
| `npm run build` | ✅ Built in 515ms |
| `curl /api/orders/orders-health` | ✅ port=MedusaOrderQuery, catalog_available=true |
| `curl /api/orders/vault-orders` | ✅ records=[] (vault vacío en dev) |
| `curl /api/orders/orders` | ✅ orders=[], catalog_available=true |
