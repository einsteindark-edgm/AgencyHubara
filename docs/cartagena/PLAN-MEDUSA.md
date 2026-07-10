# PLAN-MEDUSA — plugin de booking hotelero (repo Medusa)

> **Playbook ejecutable y self-contained**: se lleva al repo de Medusa del
> silo y lo ejecuta un agente programador paso a paso. No asume contexto de
> AgencyHubara. Su gemelo es `PLAN-HUBARA.md` (lado consumidor); la unión
> entre ambos es ÚNICAMENTE `CONTRATO-BOOKING.md`, que debe copiarse a este
> repo en `docs/CONTRATO-BOOKING.md` y mantenerse byte-idéntico al del otro
> lado.

---

## Parte 0 — Instrucciones para el agente ejecutor (leer PRIMERO)

### 0.1 Quién ejecuta y con qué

- **Agente**: una sesión de **Claude Code** (CLI `claude`) parada en la raíz
  del repo Medusa del silo. Modelo: el más capaz disponible; si el asignado
  es un modelo menor, seguí este documento AL PIE DE LA LETRA — todas las
  decisiones de arquitectura ya están tomadas acá, no tomes ninguna nueva.
- **Primer acto de CADA sesión**: leer este documento completo +
  `docs/CONTRATO-BOOKING.md`. Si existe `docs/booking-plugin-notes.md`
  (se crea en F0), leerlo también.
- **Subagentes**: usá el agente `Explore` (read-only) para mapear código que
  no conocés ANTES de editarlo. No edites un archivo que no leíste.

### 0.2 Reglas NO negociables

1. **TDD estricto**: ningún código de producción sin un test que falla
   primero. Orden por paso: escribir test → correrlo → verlo FALLAR por la
   razón correcta (no por import roto) → implementar lo mínimo → verde →
   refactor. Un `ImportError` o error de sintaxis NO cuenta como rojo válido.
2. **El contrato es ley**: `docs/CONTRATO-BOOKING.md` NO se edita desde este
   repo. Si un paso del plan contradice el contrato, o el contrato no cubre
   un caso que necesitás, **STOP** — reportá al operador y esperá. Jamás
   "interpretes" el contrato de forma creativa.
3. **Código vendored intocable por default**: el código copiado del plugin
   upstream (F0.2) no se modifica salvo que un paso lo pida explícitamente.
   TODA modificación a código vendored se registra en
   `plugins/hubara-booking/VENDOR_CHANGES.md` (formato: archivo, motivo,
   diff resumido) en el MISMO commit.
4. **Un paso = un commit** con mensaje `feat(booking): F<fase>.<paso> <qué>`
   (o `test(booking):` / `chore(booking):`). Antes de CADA commit: la suite
   completa de tests del plugin en verde (`npm run test` del plugin o el
   comando que quede definido en F0.5). Nunca commitees con tests rojos.
5. **Regla de 3 intentos**: si un paso falla 3 veces por la misma causa,
   STOP. Escribí en `docs/booking-plugin-notes.md` sección "BLOQUEOS" qué
   intentaste y por qué falló, y reportá. No improvises una arquitectura
   alternativa.
6. **Nunca**: `git push --force`, `git reset --hard` sobre trabajo ajeno,
   borrar migrations ya aplicadas, editar la DB a mano para "hacer pasar" un
   test, ni deshabilitar un test para avanzar.
7. **Dinero**: SIEMPRE enteros COP (`*_cop`). Si ves un `float` de dinero en
   tu propio diff, es un bug — corregilo antes de commitear.
8. **Fechas de estadía**: strings `YYYY-MM-DD`, timezone del hotel
   `America/Bogota`. `check_out` es EXCLUSIVO (noche del 17 al 18 = la noche
   del 17). No uses `Date` de JS con timezone implícita del server para
   aritmética de noches: iterá fechas como strings con una util `eachNight`
   (se escribe en F2.2 con tests propios).

### 0.3 Stack y técnicas prescriptas (no elegir otras)

| Tema | Prescripción |
|---|---|
| Framework | Medusa v2 (≥ 2.7.0), Node 20+, PostgreSQL |
| Empaquetado | Plugin **local vendorizado** en `plugins/hubara-booking/` (workspace del app repo), registrado en `medusa-config.ts`. NO dependencia npm remota |
| Data models nuevos | DML de Medusa (`model.define`) dentro del module del plugin; migrations generadas con `npx medusa db:generate <Module>` |
| Lógica multi-paso con side effects | **Workflows SDK de Medusa** (`createWorkflow` + `createStep` con función `compensate`). Toda escritura que pueda quedar a medias va en un workflow con compensación |
| API | API Routes de Medusa bajo `src/api/admin/hubara/**` (dentro del plugin). `/admin/*` ya exige autenticación admin por default — no inventar auth propia |
| Tests | `@medusajs/test-utils`: `medusaIntegrationTestRunner` para endpoints (DB real de test) y unit tests puros (jest) para funciones de cálculo. Los tests de contrato (CB-*) son integration |
| Concurrencia (INV-B1) | **Advisory lock transaccional de Postgres** por tipo de habitación: `SELECT pg_advisory_xact_lock(hashtext($1))` con `$1 = room_type_handle`, ejecutado como PRIMER statement del step de allocation, vía raw SQL del manager del módulo. Coarse-grained (serializa reservas del mismo tipo) — correcto para volumen hotelero. NO uses `SELECT FOR UPDATE` sobre filas que aún no existen ni checks read-then-write sin lock |
| Idempotencia | Unique constraint en DB + manejo del error de unique violation (código PG `23505`) releyendo y devolviendo el existente. El lookup previo es optimización, el constraint es la garantía |

### 0.4 Layout de archivos objetivo (referencia rápida)

```
plugins/hubara-booking/
├── VENDOR_BASE.md            # commit upstream del que se partió (F0.2)
├── VENDOR_CHANGES.md         # toda modificación a código vendored
├── src/
│   ├── modules/booking/      # vendored (modelos/servicios upstream)
│   ├── modules/hubara-facade/    # NUESTRO módulo: reservation + extras
│   │   ├── models/hubara-reservation.ts
│   │   ├── models/hubara-reservation-extra.ts
│   │   ├── service.ts
│   │   └── index.ts
│   ├── workflows/create-hotel-reservation.ts
│   ├── workflows/add-reservation-extra.ts
│   ├── workflows/cancel-hotel-reservation.ts
│   ├── api/admin/hubara/booking-contract/route.ts
│   ├── api/admin/hubara/availability/route.ts
│   ├── api/admin/hubara/extras/route.ts
│   ├── api/admin/hubara/reservations/route.ts
│   ├── api/admin/hubara/reservations/[id]/route.ts
│   ├── api/admin/hubara/reservations/[id]/extras/route.ts
│   ├── api/admin/hubara/reservations/[id]/cancel/route.ts
│   ├── lib/dates.ts          # eachNight, nights, validaciones de rango
│   ├── lib/pricing.ts        # resolveNightlyRate (función PURA)
│   └── scripts/seed-cartagena-demo.ts
└── integration-tests/
    ├── contract/cb-01-availability.spec.ts … cb-17-*.spec.ts
    └── helpers/fixtures.ts
docs/
├── CONTRATO-BOOKING.md       # copiado del repo consumidor, byte-idéntico
└── booking-plugin-notes.md   # mapa del plugin + decisiones + BLOQUEOS
```

---

## F0 — Bootstrap y aprendizaje de la maquinaria

### F0.1 Preparación
1. Verificar: `node --version` (≥ 20), Postgres accesible, `npx medusa --version`.
2. Crear branch `feat/hubara-booking`.
3. Copiar `CONTRATO-BOOKING.md` a `docs/` (viene del repo AgencyHubara,
   `docs/cartagena/CONTRATO-BOOKING.md`). Commit `chore(booking): F0.1 contrato v1.0.0`.

### F0.2 Vendorizar el plugin
1. `git clone https://github.com/RSC-Labs/medusa-booking-system /tmp/rsc-booking`
2. Anotar el commit HEAD clonado. Copiar el contenido (sin `.git`) a
   `plugins/hubara-booking/`. Crear `VENDOR_BASE.md` con: URL, commit hash,
   fecha, licencia (Apache 2.0 — conservar `LICENSE`).
3. Renombrar el package a `@hubara/medusa-booking` en su `package.json`.
4. Registrarlo en `medusa-config.ts` (array `plugins`) con resolución local
   (workspace o `file:`). Instalar deps. Correr `npx medusa db:migrate`.
5. Levantar el server (`npm run dev`) y verificar que arranca sin errores.
6. Commit `chore(booking): F0.2 vendor RSC-Labs booking plugin @<hash>`.

### F0.3 Mapear el plugin (NO editar nada todavía)
Con el agente Explore, producir `docs/booking-plugin-notes.md` con:
- Lista de data models del module booking (nombre, campos clave, relaciones).
- Servicios y sus métodos públicos (firma + qué hacen).
- Cómo se crea una allocation y qué la vincula al booking/order.
- Cómo funcionan availability rules (prioridad, efecto) y price configs
  (campos, si tienen vigencia por fechas o no).
- Si el resource tiene noción de CAPACIDAD/cantidad o es unidad única.
- Los endpoints existentes que usa el ciclo hold→cart→complete.
Este documento es la fuente para las decisiones ya tomadas en F1 —
completalo con honestidad; si algo contradice los supuestos de F1, STOP y
reportá antes de seguir.

### F0.4 Ejercitar el ciclo upstream
Escribir `scripts/smoke-booking-upstream.sh`: secuencia curl (auth admin →
crear resource → rule → price config → publicar → availability → hold →
booking-cart → add item → complete) imprimiendo cada respuesta. Correrlo
contra el server local hasta que el ciclo complete y la ORDER exista en
Medusa. Pegar el output final en `booking-plugin-notes.md`.
Commit `chore(booking): F0.4 smoke del ciclo upstream verde`.

### F0.5 Armar el runner de tests
Configurar `medusaIntegrationTestRunner` con un primer test trivial
(GET a un endpoint upstream del plugin responde 200). Definir
`npm run test:integration` en el plugin. Verlo correr verde en limpio dos
veces seguidas (detecta estado compartido). Commit.

**GATE F0**: server arranca con el plugin, ciclo upstream verde, notes
completo, runner funcionando. Si algo falta, no pasar a F1.

---

## F1 — Modelado hotelero

### F1.1 Unidades por tipo — DECISIÓN YA TOMADA
**Default prescripto: opción (b)** — un booking resource por UNIDAD FÍSICA
(habitación real), agrupadas por `room_type_handle` común (guardado en
metadata del resource o campo del module facade). Disponibilidad de un tipo =
agregación sobre sus resources. Razón: cero cambios al motor de allocations
vendored.
Única excepción: si F0.3 encontró que el resource YA tiene campo de capacidad
funcional con sus allocations — entonces opción (a) (un resource por tipo con
capacity) y anotarlo en notes. No hay tercera opción.

Pasos: test de integración que crea 2 resources con el mismo
`room_type_handle` y verifica que se pueden listar agrupados → helper
`listRoomTypeUnits(handle)` en el module facade.

### F1.2 Room type ↔ producto Medusa
Por cada tipo de habitación existe un product Medusa (1 variant) — el plugin
upstream ya integra resource↔variant (verificado en F0.3; si NO lo hace,
STOP+reportar). Test: crear producto "Habitación Doble Vista al Mar" +
resource linkeado; asertar que la relación es recuperable.

### F1.3 Tarifas por temporada
1. Si los price configs upstream tienen vigencia por fechas → usarlos.
2. Si NO (lo esperado): migration del module facade con tabla
   `hubara_season_rate` (`room_type_handle`, `valid_from`, `valid_to`,
   `nightly_rate_cop int`, `priority int`) — SIN tocar el modelo vendored.
3. Función PURA `resolveNightlyRate(rates, date) -> int` en `lib/pricing.ts`:
   elige la rate cuyo rango contiene `date`, mayor `priority` gana, sin rate
   aplicable → throw `NoRateError`. **Unit tests exhaustivos primero**
   (bordes de temporada, solapamiento, hueco sin tarifa).

### F1.4 Extras (adicionales)
Extras = product variants de Medusa en la collection `extras`, con
`metadata.extra_type` (`tour` | `room_service` | `other`),
`metadata.requires_schedule` (bool) y precio en COP. NO tocan allocations ni
capacidad. Test: seed de 2 extras y lectura filtrada por collection+type.

### F1.5 Seed demo
`src/scripts/seed-cartagena-demo.ts` (correr con `npx medusa exec`):
3 tipos de habitación (2/3/2 unidades), 2 temporadas con tarifas distintas
(cambio de tarifa el día 17 del mes próximo para CB-12), 2 tours
(`requires_schedule: true`) y 2 ítems room service. Idempotente (re-correrlo
no duplica). Test de integración que lo invoca y verifica conteos.

**GATE F1**: seed corre idempotente; una consulta manual de unidades+tarifas
devuelve la verdad conocida del seed.

---

## F2 — Fachada `/admin/hubara/*` (el contrato)

Regla de la fase: **cada endpoint se construye test-de-contrato-primero** —
el test CB-* correspondiente (F3 lista el mapeo) se escribe ANTES que la
ruta, usando el shape EXACTO del contrato (copiá los JSON del contrato al
test, no los escribas de memoria).

### F2.1 `GET /admin/hubara/booking-contract`
Ruta trivial que devuelve `{ contract_version: "1.0.0", plugin_version }`
(leída de una constante `CONTRACT_VERSION` en un solo archivo). Test CB-11.

### F2.2 Utils de fechas + `GET /admin/hubara/availability`
1. `lib/dates.ts`: `nights(check_in, check_out)`, `eachNight(...) ->
   string[]`, `validateStayRange(...)` (422 conditions del contrato §5.2:
   check_out ≤ check_in, pasado, > 30 noches). Unit tests primero.
2. Service `getAvailability({check_in, check_out, guests, room_type_handle?})`:
   por cada room_type: unidades (F1.1) − allocations solapadas por noche;
   `units_available` = mínimo sobre las noches; filtrar `max_guests >= guests`
   y `units_available >= 1`; `nightly_breakdown` con `resolveNightlyRate`;
   `total_cop` = suma. Integration tests CB-01, CB-02, CB-03, CB-12 (el seed
   de F1.5 ya trae el fixture de temporadas).
3. Ruta que delega al service. Los tests CB pegan al endpoint HTTP, no al
   service.

### F2.3 `GET /admin/hubara/extras`
Lee la collection `extras`, mapea al shape §5.3. Test: los 4 extras del seed
salen con `extra_handle`, `type`, `unit_price_cop`, `requires_schedule`.

### F2.4 Modelos del facade
DML models + migration:
- `hubara_reservation`: id (`bkg_` prefix), `client_reference` **UNIQUE**,
  `room_type_handle`, `check_in`, `check_out`, `guests`, `guest_name`,
  `guest_phone`, `guest_email?`, `guest_document_id?`, `payment_method`,
  `notes?`, `status` (`confirmed|cancelled|checked_in|checked_out`),
  `room_total_cop`, `currency`, `order_id`, `attribution jsonb?`,
  timestamps.
- `hubara_reservation_extra`: id, `reservation_id` FK, `extra_reference`
  **UNIQUE**, `extra_handle`, `name`, `type`, `quantity`, `unit_price_cop`,
  `total_cop`, `scheduled_for?`, `added_at`.
Test de módulo: crear/leer; violar el unique de `client_reference` lanza.

### F2.5 `createHotelReservationWorkflow`
Workflow con steps compensables, EN ESTE ORDEN:
1. `acquireRoomTypeLock` — advisory lock (§0.3). Sin compensación (el lock
   muere con la transacción).
2. `validateAvailabilityStep` — re-chequea unidades para el rango DENTRO del
   lock; si no hay → throw `NoAvailabilityError` (→ 409 `NO_AVAILABILITY`).
3. `resolvePricingStep` — calcula room_total + extras (valida
   `UNKNOWN_EXTRA` / `INVALID_SCHEDULE`); compara grand total con
   `expected_total_cop`; difiere → throw `PriceChangedError` con el total
   vigente (→ 409 `PRICE_CHANGED`).
4. `allocateUnitStep` — crea la allocation sobre una unidad libre.
   **Compensate**: eliminar la allocation.
5. `createOrderStep` — order de Medusa con line item habitación (variant del
   room type × nights) + un line item por extra. **Compensate**: cancelar la
   order.
6. `persistReservationStep` — inserta `hubara_reservation` + extras (con
   `extra_reference` derivadas `:x0, :x1…` de `client_reference`). Si el
   insert falla por unique 23505 (carrera de idempotencia) → releer y
   devolver el existente como replay. **Compensate**: n/a (último step).

Ruta POST: (1) lookup por `client_reference` → si existe, 200 replay;
(2) correr workflow; (3) mapear errores tipados a los códigos HTTP del
contrato. Tests: CB-04, CB-05 (contar ORDERS, no solo reservas), CB-13,
CB-15, y validaciones 422.

### F2.6 CB-06 — concurrencia (el test más valioso del plan)
Integration test: seed con UNA unidad libre; disparar DOS POST simultáneos
(`Promise.all`) con `client_reference` distintas; asertar exactamente un 201
y un 409 `NO_AVAILABILITY`, una sola allocation y una sola order en DB.
Si este test flakea, el lock de F2.5.1 está mal puesto — no lo marques
flaky: arreglalo.

### F2.7 `GET /admin/hubara/reservations/{id_or_reference}`
Distinguir por prefijo `bkg_`. Shape §5.4 completo (extras + 3 totales).
Tests CB-07, CB-08.

### F2.8 `POST /reservations/{…}/extras` — DECISIÓN YA TOMADA
Cómo materializar el cargo post-creación en la contabilidad:
- **Intento A (time-box: 1 sesión)**: Order Edit de Medusa v2 sobre la order
  original (agregar line item + confirmar edit programáticamente). Si en una
  sesión no queda limpio y testeado → abandonar A.
- **Fallback B (default seguro)**: order suplementaria vinculada
  (metadata `hubara_reservation_id`), un line item por cargo.
El contrato no cambia en ningún caso (el consumidor solo ve `extras[]` +
totales). Anotar la decisión en notes.
Workflow `addReservationExtraWorkflow`: validar reserva activa (409
`RESERVATION_NOT_ACTIVE`) → validar extra + schedule → guard de precio del
CARGO → materializar (A o B, compensable) → insertar
`hubara_reservation_extra` (unique 23505 → replay 200).
Tests CB-16 (replay no duplica: contar line items) y CB-17.

### F2.9 `POST /reservations/{…}/cancel`
Workflow: liberar allocation → cancelar order(s) → `status = cancelled`.
Idempotente (cancelada → 200 estado actual). 409 `ALREADY_CHECKED_IN` si
aplica. Tests CB-09 (availability vuelve a mostrar la unidad), CB-10.

**GATE F2**: los 7 endpoints responden según contrato contra el seed demo,
ejercitados con `scripts/smoke-hubara-facade.sh` (versión fachada del smoke
de F0.4).

---

## F3 — La suite compartida completa (CB-01…CB-17)

Ya se escribieron durante F2 (test-first). Este paso es de CIERRE:
1. Verificar 1:1 contra el contrato §8 que TODOS los CB existen como test,
   con un archivo por escenario en `integration-tests/contract/` nombrado
   `cb-NN-<slug>.spec.ts`. Tabla de mapeo en notes.
2. CB-14 como test transversal: recorrer las respuestas capturadas y asertar
   que ningún campo `*_cop` es float.
3. Integrarlos al CI del repo (los CB corren en cada PR).

**GATE F3**: `npm run test:integration` verde en CI, 17/17 CB presentes.

---

## F4 — Deploy y runbook

1. **Deploy a Railway**: migrations del plugin en el start command
   (`npx medusa db:migrate && npm run start` o predeploy hook de Railway).
   Verificar `GET /admin/hubara/booking-contract` en el entorno vivo.
2. **Seed REAL del hotel** con runbook reproducible
   (`docs/runbook-seed-hotel.md`): tipos, tarifas, temporadas, unidades,
   extras — vía script parametrizado, NUNCA a mano irrepetible. ⚠️ Sin seed,
   la feature "existe" pero responde vacío: el smoke DEBE consultar
   availability real y ver habitaciones.
3. **Smoke post-deploy**: `scripts/smoke-hubara-facade.sh` apuntado al
   entorno vivo con `client_reference` prefijadas `smoke:` + paso de
   limpieza (cancelar lo creado).

**GATE F4 / Definición de terminado**:
1. CB-01…CB-17 verdes en CI.
2. Smoke verde contra el entorno desplegado, con datos reales sembrados.
3. `docs/CONTRATO-BOOKING.md` idéntico al del repo consumidor, versión
   congelada `1.0.0` (verificable con `diff` / hash).

---

## Riesgos conocidos (ya mitigados por diseño — no re-decidir)

| Riesgo | Mitigación prescripta |
|---|---|
| Plugin joven, allocations bajo carrera | Advisory lock nuestro (F2.5.1) + CB-06 obligatorio |
| Resource sin capacidad múltiple | Opción (b) resource-por-unidad, default de F1.1 |
| Price configs sin vigencia por fechas | Tabla propia `hubara_season_rate` + `resolveNightlyRate` puro (F1.3) |
| Order Edits áspero | Time-box 1 sesión → fallback order suplementaria (F2.8) |
| Drift del contrato entre repos | Contrato copiado + handshake `contract_version` + gate de `diff` en F4 |
| Upstream avanza y divergimos | Aceptado: `VENDOR_BASE.md` ancla el commit; diffear solo con motivo |
