# CONTRATO-BOOKING — v1.0.0-draft

> **Fuente de verdad compartida** entre el repo Medusa (que lo EXPONE) y
> AgencyHubara (que lo CONSUME vía `BookingPort` → `MedusaBookingVendor`).
> Este archivo se copia LITERAL a ambos repos. Cambios: semver — major =
> breaking (el vendor de Hubara se niega a operar ante mismatch de major).
> Estado: **draft** hasta revisión humana; ahí pasa a `1.0.0` y se congela.

## 1. Alcance

Cubre **reservas de alojamiento** (habitaciones por rango de fechas) y sus
**adicionales (extras)**: planes turísticos y servicio a la habitación que se
cuelgan de una reserva — se cotizan junto a ella o se cargan durante la
estadía, y aparecen en la misma cuenta. Un plan turístico vendido SIN reserva
de alojamiento sigue siendo un producto Medusa normal por los ports existentes
(`CatalogPort`, `OrderRegistrationPort`); el camino de extras exige una
reserva activa.

El lado Medusa expone estos endpoints bajo el namespace **`/admin/hubara/`**
(fachada de alto nivel sobre el plugin de booking). Los endpoints granulares
del plugin (`/admin/booking-resources`, etc.) son detalle interno del repo
Medusa — Hubara **jamás** los llama.

Racional de la fachada: el consumidor es un agente LLM orquestado por Temporal.
La lección aprendida (caso orden↔tag, mayo 2026) es que una "transacción"
fragmentada en varias llamadas coordinadas por el prompt NO es atómica. Por eso
la reserva es **UN endpoint** que internamente resuelve hold + allocation +
order dentro de un workflow de Medusa con compensación.

## 2. Autenticación

Igual que el resto del vendor Medusa: `Authorization: Bearer <MEDUSA_ADMIN_TOKEN>`
(API key secreta de admin). Sin auth → 401.

## 3. Convenciones

| Tema | Regla |
|---|---|
| Dinero | Enteros en COP: campos `*_cop: int` (COP no maneja subcentavos). `currency` siempre presente, `"COP"` en v1. Si algún día entra USD u otra moneda sub-cent: `usd_micros` (lección HU-WA24H-001), version bump. |
| Fechas de estadía | `check_in` / `check_out` como fecha ISO `YYYY-MM-DD` (SIN hora). Timezone implícita del hotel: `America/Bogota`. `nights = check_out - check_in` (check_out exclusivo). Mínimo 1 noche. |
| Timestamps | ISO 8601 con offset (`2026-07-09T14:30:00-05:00`). |
| Ids | `reservation_id` opaco generado por Medusa (`bkg_...`). `room_type_handle` estable tipo slug (`doble-vista-mar`), análogo al `handle` de producto. |
| Idempotencia | `client_reference: str` único, generado por el CLIENTE (Hubara): `wa:{session_key}:{fingerprint-corto}`. Unique constraint en el servidor. |
| JSON | Todo snake_case. Campos desconocidos se ignoran (forward-compat dentro del mismo major). |

## 4. Estados de una reserva

```
confirmed ──→ cancelled          (cancelación, por API o por el hotel)
confirmed ──→ checked_in ──→ checked_out    (operación del hotel, v1 opcional)
```

En v1 la reserva nace `confirmed` (el flujo conversacional confirma pago por
los mismos mecanismos que hoy usa sales: instrucciones de pago deterministas +
verificación humana). No hay estado `pending_payment` en v1 — si el negocio lo
pide, es v1.1 aditivo (estado nuevo + campo `payment_status`).

**Modificación de fechas en v1 = cancelar + crear de nuevo** (nueva
`client_reference`). `modify` real es v2.

## 5. Endpoints

### 5.1 `GET /admin/hubara/booking-contract`

Handshake. Sin parámetros.

```json
{ "contract_version": "1.0.0", "plugin_version": "<semver del fork>" }
```

### 5.2 `GET /admin/hubara/availability`

Búsqueda **por fechas, cross-recurso** (qué hay libre y cuánto cuesta — la
disponibilidad Y la cotización son la misma respuesta; el agente cotiza en el
mismo turno).

Query params: `check_in` (req), `check_out` (req), `guests` (req, int ≥ 1),
`room_type_handle` (opcional — filtra a un solo tipo).

```json
{
  "check_in": "2026-08-15",
  "check_out": "2026-08-18",
  "nights": 3,
  "currency": "COP",
  "room_types": [
    {
      "room_type_handle": "doble-vista-mar",
      "name": "Habitación Doble Vista al Mar",
      "description": "…",
      "max_guests": 3,
      "units_available": 2,
      "nightly_breakdown": [
        { "date": "2026-08-15", "rate_cop": 350000 },
        { "date": "2026-08-16", "rate_cop": 350000 },
        { "date": "2026-08-17", "rate_cop": 420000 }
      ],
      "total_cop": 1120000
    }
  ]
}
```

- Sin disponibilidad → `room_types: []` con HTTP 200 (no es error).
- Solo tipos con `max_guests >= guests` y `units_available >= 1` para TODAS
  las noches del rango.
- `nightly_breakdown` refleja tarifas por temporada (una noche puede costar
  distinto que otra dentro del mismo rango).
- 422 si `check_out <= check_in`, fechas pasadas, o rango > 30 noches.

### 5.3 `GET /admin/hubara/extras`

Catálogo de adicionales contratables sobre una reserva. Query param opcional:
`type` (`tour` | `room_service` | `other`).

```json
{
  "currency": "COP",
  "extras": [
    {
      "extra_handle": "tour-islas-rosario",
      "name": "Tour Islas del Rosario (día completo)",
      "type": "tour",
      "unit_price_cop": 180000,
      "requires_schedule": true,
      "description": "…"
    },
    {
      "extra_handle": "desayuno-habitacion",
      "name": "Desayuno a la habitación",
      "type": "room_service",
      "unit_price_cop": 35000,
      "requires_schedule": false
    }
  ]
}
```

- Cada extra es, server-side, un product variant de Medusa (así entra a la
  order con line item legible) — detalle interno, el consumidor solo ve este
  shape.
- `requires_schedule: true` → al contratarlo, `scheduled_for` (fecha ISO) es
  obligatorio y debe caer dentro de `[check_in, check_out]` (422 si no).

### 5.4 `POST /admin/hubara/reservations`

Crea la reserva de forma **atómica e idempotente**, opcionalmente con extras
incluidos desde el arranque.

```json
{
  "client_reference": "wa:57300…:a3f9",
  "room_type_handle": "doble-vista-mar",
  "check_in": "2026-08-15",
  "check_out": "2026-08-18",
  "guests": 2,
  "guest": { "name": "María Pérez", "phone": "+57300…", "email": null, "document_id": null },
  "payment_method": "transferencia",
  "notes": "llegan ~22h",
  "extras": [
    { "extra_handle": "tour-islas-rosario", "quantity": 2, "scheduled_for": "2026-08-16" }
  ],
  "expected_total_cop": 1480000,
  "attribution": { }
}
```

Respuesta 201 (creada) o 200 (replay idempotente):

```json
{
  "reservation_id": "bkg_01H…",
  "client_reference": "wa:57300…:a3f9",
  "status": "confirmed",
  "idempotent_replay": false,
  "room_type_handle": "doble-vista-mar",
  "check_in": "2026-08-15",
  "check_out": "2026-08-18",
  "nights": 3,
  "guests": 2,
  "guest": { "name": "María Pérez", "phone": "+57300…" },
  "room_total_cop": 1120000,
  "extras": [
    {
      "extra_reference": "wa:57300…:a3f9:x0",
      "extra_handle": "tour-islas-rosario",
      "name": "Tour Islas del Rosario (día completo)",
      "type": "tour",
      "quantity": 2,
      "unit_price_cop": 180000,
      "total_cop": 360000,
      "scheduled_for": "2026-08-16",
      "added_at": "2026-07-09T14:30:00-05:00"
    }
  ],
  "extras_total_cop": 360000,
  "grand_total_cop": 1480000,
  "currency": "COP",
  "order_id": "order_01H…",
  "created_at": "2026-07-09T14:30:00-05:00"
}
```

Semántica:

- **Idempotencia**: segundo POST con la misma `client_reference` → 200 con la
  reserva EXISTENTE, `idempotent_replay: true`, sin efectos nuevos (ni segunda
  order, ni segunda allocation). El body del replay puede diferir del original:
  gana el original, no se re-valida.
- **Atomicidad**: allocation de la habitación + order de Medusa se crean en un
  workflow con compensación — o queda todo, o no queda nada.
- **`expected_total_cop`**: guard anti-drift sobre el **grand total**
  (habitación + extras incluidos en este POST). Si el total server-side
  difiere → 409 `PRICE_CHANGED` con el total vigente en el body (el agente
  re-cotiza al cliente). Nunca se reserva a un precio que el cliente no vio.
- **`order_id`**: toda reserva confirmada materializa una order de Medusa
  (line items = variant del room type × nights + un line item por extra) —
  así el dashboard de Pedidos, métricas y unit economics la ven sin trabajo
  extra.
- Los extras del POST de creación reciben `extra_reference` derivada
  determinísticamente de `client_reference` (`:x0`, `:x1`, …) — el replay
  idempotente tampoco los duplica.
- 409 `NO_AVAILABILITY` si la capacidad se agotó entre la consulta y el POST
  (double-booking race). Sin efectos.
- 422 validación (fechas, guests > max_guests, handle inexistente → 422
  `UNKNOWN_ROOM_TYPE`, extra inexistente → 422 `UNKNOWN_EXTRA`,
  `scheduled_for` faltante o fuera de la estadía → 422 `INVALID_SCHEDULE`).

### 5.5 `GET /admin/hubara/reservations/{id_or_reference}`

Acepta `reservation_id` o `client_reference` (el servidor distingue por
prefijo `bkg_`). Respuesta: mismo shape que 5.4 (incluye `extras[]` y los
tres totales). 404 si no existe.

**Este endpoint es la pieza del protocolo de recuperación** (ver §6): tras un
read-timeout en el POST, el cliente consulta por `client_reference` ANTES de
reintentar.

### 5.6 `POST /admin/hubara/reservations/{id_or_reference}/extras`

Agrega adicionales a una reserva EXISTENTE — el caso "servicio a la
habitación durante la estadía" y el upsell de tours post-reserva.

```json
{
  "extra_reference": "wa:57300…:rs-0817-am",
  "extra_handle": "desayuno-habitacion",
  "quantity": 2,
  "scheduled_for": null,
  "expected_total_cop": 70000
}
```

Respuesta 201 (o 200 si replay): la reserva completa actualizada (shape §5.4).

- **Idempotencia por `extra_reference`** (generada por el cliente, única):
  replay → 200, sin cargo duplicado (mismo protocolo que la reserva).
- `expected_total_cop` es el total de ESTE cargo (no el grand total): guard
  `PRICE_CHANGED` idéntico a la creación.
- El cargo se materializa en la cuenta de la reserva server-side (line item
  en la order de la reserva o en una order suplementaria vinculada — detalle
  interno del lado Medusa; el consumidor solo ve `extras[]` y los totales).
- Solo sobre reservas `confirmed` o `checked_in`; 409
  `RESERVATION_NOT_ACTIVE` si está `cancelled` / `checked_out`.
- 422 `UNKNOWN_EXTRA` / `INVALID_SCHEDULE` igual que en la creación.
- Read-timeout → mismo protocolo de recuperación: `GET` de la reserva y
  buscar la `extra_reference` en `extras[]` antes de reintentar.

### 5.7 `POST /admin/hubara/reservations/{id_or_reference}/cancel`

Body opcional: `{ "reason": "cliente cambió de plan" }`.

- 200 con la reserva en `status: "cancelled"`. Libera la allocation (las
  fechas vuelven a estar disponibles inmediatamente).
- **Idempotente**: cancelar una ya cancelada → 200, estado actual, sin error.
- La order asociada se cancela también (misma transacción).
- 404 si no existe. 409 `ALREADY_CHECKED_IN` si el hotel ya hizo check-in.

## 6. Semántica de errores (regla L-1 del ConnectorKit — HTTP honesto)

| Situación | Código | Significado para el cliente |
|---|---|---|
| Validación de input | 422 + `{"code": "...", "message": "..."}` | NO se aplicó. No reintentar igual. |
| Conflicto de negocio | 409 + code (`NO_AVAILABILITY`, `PRICE_CHANGED`, `ALREADY_CHECKED_IN`, `RESERVATION_NOT_ACTIVE`) | NO se aplicó. Acción correctiva (re-cotizar, ofrecer otro tipo). |
| No existe | 404 | N/A |
| Auth | 401 | Config rota → escalar a humano. |
| Error interno del plugin | 500 | NO se aplicó (el workflow compensó). Seguro reintentar con la MISMA `client_reference`. |
| Connect error (cliente no llegó) | — | NO se aplicó. Reintento con backoff. |
| Read timeout (llegó, no respondió) | — | **DESCONOCIDO**. El cliente DEBE hacer `GET …/{client_reference}` para saber si existe antes de reintentar. Jamás reintentar a ciegas con referencia nueva. |

Todo error 4xx/5xx lleva body `{"code": "SCREAMING_SNAKE", "message": "humano"}`.

## 7. Invariantes

- **INV-B1 (no double-booking)**: para todo tipo de habitación y toda noche,
  `allocations_confirmadas ≤ units` del tipo. Se garantiza server-side con
  lock/constraint, NO con "el cliente consultó antes".
- **INV-B2 (idempotencia)**: una `client_reference` produce a lo sumo UNA
  reserva y UNA order, para siempre.
- **INV-B3 (reserva ⇔ order)**: toda reserva `confirmed` tiene exactamente una
  order activa; cancelar una cancela la otra.
- **INV-B4 (precio visto = precio cobrado)**: ninguna reserva ni cargo de
  extra se crea con total distinto a su `expected_total_cop`.
- **INV-B5 (contrato chequeado)**: el consumidor verifica
  `contract_version` (major) antes de operar; mismatch = no operar + escalar.
- **INV-B6 (extras idempotentes y contabilizados)**: una `extra_reference`
  produce a lo sumo UN cargo, para siempre; todo extra de `extras[]` tiene su
  line item en la cuenta de la reserva y
  `grand_total_cop = room_total_cop + extras_total_cop` siempre.

## 8. Escenarios canónicos (CB-*) — la suite compartida

Formato capability-spec (RFC 2119 + Gherkin). **Ambos repos implementan estos
mismos escenarios como tests**: en Medusa como integration tests del plugin;
en Hubara como contract suite del `BookingPort` (parametrizada fake | live).
Un escenario nuevo = PR al contrato primero, versión minor bump.

### Requirement: Disponibilidad por fechas
El sistema SHALL responder qué tipos de habitación tienen unidades libres para
TODO el rango pedido, con desglose de tarifa por noche y total.

- **CB-01** — GIVEN 2 unidades libres de `doble-vista-mar` del 15 al 18 WHEN
  consulto availability 15→18 para 2 huéspedes THEN el tipo aparece con
  `units_available: 2`, 3 noches en `nightly_breakdown` y `total_cop` = suma.
- **CB-02** — GIVEN cero unidades libres alguna de las noches WHEN consulto
  THEN `room_types` NO incluye ese tipo (y `[]` + HTTP 200 si ninguno queda).
- **CB-03** — WHEN consulto con `check_out <= check_in` THEN 422 y ninguna
  consulta parcial.
- **CB-12** — GIVEN temporada alta desde el 17 con tarifa mayor WHEN consulto
  15→18 THEN `nightly_breakdown` muestra la tarifa correcta POR noche y el
  total cruza temporadas correctamente.

### Requirement: Reserva atómica e idempotente
El sistema SHALL crear reserva + allocation + order como una unidad, a lo sumo
una vez por `client_reference`.

- **CB-04** — GIVEN disponibilidad WHEN POST reservations THEN 201 `confirmed`,
  con `order_id`, y la availability posterior refleja una unidad menos.
- **CB-05** — GIVEN reserva creada con ref R WHEN repito el POST con ref R
  THEN 200, `idempotent_replay: true`, mismo `reservation_id`, y NO existe
  segunda order.
- **CB-06** — GIVEN 1 unidad libre WHEN dos POST concurrentes con refs
  distintas THEN exactamente uno recibe 201 y el otro 409 `NO_AVAILABILITY`
  sin efectos (INV-B1 bajo carrera).
- **CB-13** — GIVEN total server-side ≠ `expected_total_cop` WHEN POST THEN
  409 `PRICE_CHANGED` con el total vigente, sin reserva (INV-B4).

### Requirement: Consulta y recuperación
El sistema SHALL permitir recuperar una reserva por id o por
`client_reference`, como base del protocolo post-timeout.

- **CB-07** — GIVEN reserva con ref R WHEN GET por R THEN 200 mismo shape que
  la creación.
- **CB-08** — WHEN GET por ref inexistente THEN 404 (el cliente concluye "el
  POST no se aplicó, puedo reintentar con la MISMA ref").

### Requirement: Cancelación
El sistema SHALL cancelar liberando la disponibilidad, de forma idempotente.

- **CB-09** — GIVEN reserva confirmada WHEN cancel THEN `cancelled`, la order
  asociada queda cancelada, y availability vuelve a mostrar la unidad.
- **CB-10** — GIVEN reserva cancelada WHEN cancel de nuevo THEN 200 estado
  actual, sin error ni efecto.

### Requirement: Adicionales (tours y servicio a la habitación)
El sistema SHALL permitir contratar extras junto con la reserva o durante la
estadía, idempotentes por `extra_reference`, reflejados en la cuenta.

- **CB-15** — GIVEN el extra `tour-islas-rosario` en el catálogo WHEN creo la
  reserva con `extras: [{…, quantity: 2, scheduled_for}]` y el
  `expected_total_cop` correcto THEN 201 con `extras[]` poblado,
  `grand_total_cop = room_total_cop + extras_total_cop`, y la order incluye
  un line item por el tour además de la habitación.
- **CB-16** — GIVEN reserva confirmada WHEN POST extras
  (`desayuno-habitacion`, ref E) THEN 201 con el cargo en `extras[]`; WHEN
  repito el POST con ref E THEN 200 sin cargo duplicado (INV-B6).
- **CB-17** — WHEN POST extras con `extra_handle` inexistente THEN 422
  `UNKNOWN_EXTRA`; WHEN un extra `requires_schedule` va sin `scheduled_for` o
  con fecha fuera de `[check_in, check_out]` THEN 422 `INVALID_SCHEDULE`; WHEN
  POST extras sobre una reserva cancelada THEN 409 `RESERVATION_NOT_ACTIVE`.
  En todos los casos, sin efectos.

### Requirement: Handshake de contrato
- **CB-11** — WHEN GET booking-contract THEN `contract_version` semver; el
  consumidor SHALL negarse a operar si el major difiere del propio (INV-B5).

### Requirement: Moneda y montos
- **CB-14** — todos los montos del contrato son `int` COP; ningún endpoint
  emite floats de dinero.

## 9. Changelog

- `1.0.0-draft` (2026-07-09) — versión inicial. Mismo día: agregados los
  **adicionales** (extras: planes turísticos y servicio a la habitación) —
  catálogo §5.3, extras en la creación §5.4, cargo post-creación §5.6,
  INV-B6, CB-15..17. Pendiente revisión humana para congelar como `1.0.0`.
