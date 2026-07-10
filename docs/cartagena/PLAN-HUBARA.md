# PLAN-HUBARA — BookingPort + agente hotelero (este repo)

> **Playbook ejecutable**: lo ejecuta un agente programador paso a paso en el
> monorepo AgencyHubara. Su gemelo es `PLAN-MEDUSA.md` (repo Medusa); la
> unión es `CONTRATO-BOOKING.md`. **Todo este plan se ejecuta SIN el plugin
> de Medusa vivo** (contra `FakeBookingVendor`); la fusión (H5) es apuntar el
> vendor al Medusa real y correr la misma suite.

---

## Parte 0 — Instrucciones para el agente ejecutor (leer PRIMERO)

### 0.1 Quién ejecuta y con qué

- **Agente**: sesión de **Claude Code** en la raíz del monorepo, con el
  harness **hubara-dev** activo (se auto-carga por hook de sesión). Antes de
  escribir código: invocar el skill **`hubara-plugin-developer`** y leer sus
  `references/00-tdd-law.md` y `references/01-hard-rules.md`.
- **Subagentes — usarlos, no improvisar**:
  - `hubara-dev:hubara-explorer` — ANTES de tocar una zona que no conocés
    (workspace del agente sales, mecánica de `order_draft`, carga de SOUL.md,
    registro de tags). Pedile el mapa; no edites a ciegas.
  - `hubara-dev:hubara-tdd-author` — para escribir el test ROJO de cada
    incremento cuando el test no sea calco directo de uno existente.
  - `hubara-dev:hubara-gate-reviewer` — al CERRAR cada fase, antes del PR.
- **Primer acto de cada sesión**: leer este doc + `CONTRATO-BOOKING.md` +
  los archivos espejo de §0.4.

### 0.2 Reglas NO negociables (además de las del harness)

1. **TDD 3 leyes**: test que falla primero, por la razón correcta (un rojo
   por `ImportError`/colección NO vale). Un comportamiento por vuelta.
2. **El contrato es ley**: si algo no cierra contra `CONTRATO-BOOKING.md`,
   STOP y reportar. No editarlo desde una fase de implementación.
3. **Todo `uv run …` va prefijado `cd hubara_agency &&`** (hook pre-bash lo
   bloquea si no).
4. **Re-exports del SDK con alias idiom** (`from x import y as y`) — el hook
   post-edit corre `ruff --fix` y PODA imports sin alias (lección L-0).
5. **PROTECTED paths** (`spinal-files.yaml`): si un paso requiere tocar
   `src/platform/constants.py`, `tests/architecture/**` o `.importlinter`,
   se hace con ADR + `ARCH_CHANGE_APPROVED=1` + label `architecture-change`
   en el PR. NUNCA silenciosamente.
6. **Después de tocar un worker** (`chats/workers/*.py`):
   `cd hubara_agency && uv run ruff check --select F821 src/` (caza el
   NameError-en-runtime de símbolos usados en lambdas sin import).
7. **Strings de agente SIN voseo** — el guard
   `test_no_voseo_in_agent_strings.py` debe cubrir los archivos nuevos.
8. **Una fase = un PR** (H0 va dentro del PR de H1). Título
   `feat(booking): H<fase> <qué>`. Antes de cada PR, panel completo de §0.5.
9. **Regla de 3 intentos**: tres fallos por la misma causa → STOP, documentar
   en el PR draft qué se intentó, reportar al operador.
10. **Dinero `*_cop: int`, fechas `YYYY-MM-DD` str, DTOs frozen JSON-safe**
    (R-JSON: nada de `Decimal`/`datetime`/`Path` cruzando boundaries).

### 0.3 Decisiones de arquitectura YA TOMADAS (no re-decidir)

| Tema | Decisión |
|---|---|
| Ubicación del port | `src/platform/booking/` (port.py, dtos.py, fake.py, medusa_booking.py, composition.py) — espejo exacto del layout de `src/platform/orders/` |
| Superficie pública | vía `src/sdk/connectorkit/` (entrada en `_LAZY_EXPORTS` + espejo eager en `ports.py`) — los plugins JAMÁS importan `src.platform.booking` directo (P-28) |
| Binding del vendor | Estático por env en `composition.py` (patrón `get_order_registration_port`): live si `MEDUSA_BASE_URL` + `BOOKING_ENABLED=1`, si no `FakeBookingVendor`. El registry config-driven es F-SDK-4b, fuera de alcance |
| Errores de negocio | Resultados con `success=False` + `error_code`, NUNCA excepciones hacia la tool (patrón `OrderRegistrationResult`) — el LLM decide la acción correctiva |
| Activación del vertical | Env `TENANT_VERTICAL=hotel` → el composition root del sales worker registra las tools de booking y selecciona el workspace hotelero. Default (sin la var): comportamiento retail actual INTACTO — mainline no cambia de conducta |
| Workspace hotelero | Directorio hermano `workspace_hotel/` junto a `workspace/` del agente sales, seleccionado por env en el composition root. El mecanismo exacto de carga lo mapea `hubara-explorer` en H4.1 |
| Idempotencia | Fingerprints deterministas calculados por la tool desde el draft (sha256 truncado de un string canónico) — sin `random`, sin `now` (R-DET) |
| Recovery post-timeout | Vive EN EL ADAPTER (H2.3), no en el LLM ni en el prompt |

### 0.4 Archivos espejo — leer ANTES de escribir su equivalente

| Vas a escribir | Leé primero (calco del patrón) |
|---|---|
| `booking/port.py` + `dtos` | `src/platform/orders/port.py` (Protocol + DTOs frozen + docstring de idempotencia) |
| `booking/fake.py` | `src/platform/orders/stub.py` + fakes de tests existentes |
| `booking/medusa_booking.py` | `src/platform/orders/medusa_order.py` (httpx + tenacity + settings) |
| `booking/composition.py` | `src/platform/orders/composition.py` (`@lru_cache(maxsize=1)`) |
| tools de booking | `src/plugins/chats/agent/sales/tools/order_registration.py` (tool inerte, port por constructor, envelope textual, patrón LLM documentado en docstring) |
| wiring en el worker | `src/plugins/chats/workers/sales.py` (bloque donde se construyen `MedusaCheckoutVerification` / `get_medusa_product_service` y los `register_tool_extension`) |
| tests de tools | `tests/plugins/chats/sales/test_register_order_tool.py` |
| export SDK | `src/sdk/connectorkit/__init__.py` + `ports.py` (mantener AMBOS en sync — el guard `test_sdk_lazy_surface` compara) |

### 0.5 Panel de verificación (correr al cerrar CADA fase)

```bash
cd hubara_agency && uv run pytest tests/platform/booking tests/plugins/chats -q
cd hubara_agency && uv run lint-imports
cd hubara_agency && MEDUSA_BASE_URL=http://medusa.invalid MEDUSA_ADMIN_TOKEN=ci-dummy \
  OTEL_SDK_DISABLED=true uv run pytest tests/architecture -q
cd hubara_agency && uv run python -m src.sdk.cli check
cd hubara_agency && uv run ruff check --select F821 src/
```
(o el panel completo: skill `/hubara-gates backend`). ⚠️ Los dummies
`MEDUSA_*` son SOLO para `tests/architecture` — NO exportarlos para
`tests/platform/` (cuelga en retries HTTP).

---

## H0 — Spec de capability (entra en el PR de H1)

1. Crear `hubara_agency/.hubara/specs/booking/spec.md` siguiendo el formato
   de `hubara_agency/.hubara/specs/plugins/orders/spec.md` (leerlo primero):
   `## Purpose` + un `### Requirement:` por cada Requirement del contrato §8
   + los escenarios CB-01..CB-17 como `#### Scenario:` GIVEN/WHEN/THEN,
   copiados (no parafraseados) del contrato.
2. Registrar la capability en `.hubara/specs/README.md` si hay índice.

---

## H1 — `BookingPort` + Fake + contract suite (las 3 patas, un PR)

### H1.1 DTOs (`src/platform/booking/dtos.py`) — spec completa, copiar tal cual

Todos `@dataclass(frozen=True)`; usar `field(default_factory=list)` donde
aplique:

```python
@dataclass(frozen=True)
class NightRate:
    date: str            # "YYYY-MM-DD"
    rate_cop: int

@dataclass(frozen=True)
class RoomTypeOption:
    room_type_handle: str
    name: str
    description: str
    max_guests: int
    units_available: int
    nightly_breakdown: list[NightRate]
    total_cop: int

@dataclass(frozen=True)
class AvailabilityResult:
    success: bool
    check_in: str
    check_out: str
    nights: int
    currency: str                     # "COP"
    room_types: list[RoomTypeOption]  # vacío = sin disponibilidad (NO error)
    error_code: str | None = None     # solo si success=False
    error_detail: str | None = None

@dataclass(frozen=True)
class ExtraCatalogItem:
    extra_handle: str
    name: str
    extra_type: str        # "tour" | "room_service" | "other"
    unit_price_cop: int
    requires_schedule: bool
    description: str = ""

@dataclass(frozen=True)
class ExtrasCatalogResult:
    success: bool
    currency: str
    extras: list[ExtraCatalogItem]
    error_code: str | None = None
    error_detail: str | None = None

@dataclass(frozen=True)
class ExtraRequest:
    extra_handle: str
    quantity: int
    scheduled_for: str | None = None

@dataclass(frozen=True)
class ExtraLine:
    extra_reference: str
    extra_handle: str
    name: str
    extra_type: str
    quantity: int
    unit_price_cop: int
    total_cop: int
    scheduled_for: str | None
    added_at: str          # ISO timestamp que reporta el vendor

@dataclass(frozen=True)
class GuestInfo:
    name: str
    phone: str
    email: str | None = None
    document_id: str | None = None

@dataclass(frozen=True)
class ReservationResult:
    success: bool
    provider: str                      # "medusa" | "fake"
    reservation_id: str | None = None
    client_reference: str | None = None
    status: str | None = None          # confirmed|cancelled|checked_in|checked_out
    idempotent_replay: bool = False
    room_type_handle: str | None = None
    check_in: str | None = None
    check_out: str | None = None
    nights: int | None = None
    guests: int | None = None
    guest: GuestInfo | None = None
    room_total_cop: int | None = None
    extras: list[ExtraLine] = field(default_factory=list)
    extras_total_cop: int | None = None
    grand_total_cop: int | None = None
    currency: str = "COP"
    order_id: str | None = None
    created_at: str | None = None
    # fallas (success=False):
    error_code: str | None = None      # NO_AVAILABILITY | PRICE_CHANGED |
                                       # UNKNOWN_ROOM_TYPE | UNKNOWN_EXTRA |
                                       # INVALID_SCHEDULE | RESERVATION_NOT_ACTIVE |
                                       # ALREADY_CHECKED_IN | NOT_FOUND |
                                       # VALIDATION | UPSTREAM_UNREACHABLE |
                                       # UPSTREAM_UNKNOWN | CONTRACT_MISMATCH
    error_detail: str | None = None
    current_total_cop: int | None = None  # poblado en PRICE_CHANGED
```

### H1.2 Port (`src/platform/booking/port.py`)

`BookingPort` como `typing.Protocol` `runtime_checkable` con los 6 métodos
(firma exacta ya definida abajo), docstring estilo `OrderRegistrationPort`
que documente idempotencia (INV-B2/B6) y semántica de errores:

```python
class BookingPort(Protocol):
    async def check_availability(self, *, check_in: str, check_out: str,
        guests: int, room_type_handle: str | None = None) -> AvailabilityResult: ...
    async def list_extras(self, *, extra_type: str | None = None) -> ExtrasCatalogResult: ...
    async def create_reservation(self, *, client_reference: str,
        room_type_handle: str, check_in: str, check_out: str, guests: int,
        guest: GuestInfo, payment_method: str, expected_total_cop: int,
        extras: list[ExtraRequest] | None = None, notes: str | None = None,
        attribution: dict[str, Any] | None = None) -> ReservationResult: ...
    async def add_reservation_extra(self, *, id_or_reference: str,
        extra_reference: str, extra_handle: str, quantity: int,
        expected_total_cop: int, scheduled_for: str | None = None) -> ReservationResult: ...
    async def get_reservation(self, *, id_or_reference: str) -> ReservationResult: ...
    async def cancel_reservation(self, *, id_or_reference: str,
        reason: str | None = None) -> ReservationResult: ...
```

### H1.3 `FakeBookingVendor` (`src/platform/booking/fake.py`)

In-memory, configurable por constructor con un fixture:

```python
FakeHotelFixture(
    room_types=[FakeRoomType(handle, name, max_guests, units,
                             rates=[(valid_from, valid_to, nightly_rate_cop)])],
    extras=[ExtraCatalogItem(...)],
)
```

Comportamiento OBLIGATORIO (es lo que la contract suite verifica):
- Disponibilidad por noche = `units` − reservas `confirmed`/`checked_in`
  que solapan esa noche; `units_available` = mínimo del rango; filtra
  `max_guests`; breakdown por `rates` (mayor prioridad = último matching;
  noche sin rate → excluir el tipo).
- `create_reservation`: valida (422-equivalentes → `error_code`); recalcula
  grand total y compara con `expected_total_cop` → `PRICE_CHANGED` +
  `current_total_cop`; sin unidades → `NO_AVAILABILITY`; `client_reference`
  repetida → devuelve la EXISTENTE con `idempotent_replay=True` (aunque el
  body difiera — gana el original); genera `order_id` `fake_order_…`
  determinista (`f"fake_order_{client_reference}"`), `reservation_id`
  `f"bkg_fake_{client_reference}"`, extras con `extra_reference`
  `f"{client_reference}:x{i}"`.
- `add_reservation_extra`: reserva inexistente → `NOT_FOUND`; no activa →
  `RESERVATION_NOT_ACTIVE`; `extra_reference` repetida → replay sin
  duplicar; guard de precio del cargo; totales SIEMPRE consistentes
  (`grand = room + extras`, INV-B6).
- `cancel_reservation`: idempotente; libera las noches (afecta availability
  posterior); `checked_in` → `ALREADY_CHECKED_IN`.
- `created_at`/`added_at`: contador determinista del fake
  (`"2026-01-01T00:00:0{n}-05:00"`), NO `datetime.now()`.

### H1.4 Contract suite (`tests/platform/booking/test_booking_contract.py`)

- Un test por escenario, nombrado `test_cb_NN_<slug>`, con los valores del
  contrato §8 (copiar los ejemplos del contrato, no inventar).
- Fixture `booking_port` parametrizada: param `"fake"` (siempre) y param
  `"live"` con `pytest.mark.live_contract` + skip salvo
  `BOOKING_CONTRACT_LIVE=1` (H5 lo enciende). Registrar el marker en la
  config de pytest para que la corrida normal NO toque red.
- CB-06 (carrera) contra el fake: `asyncio.gather` de dos
  `create_reservation` sobre la última unidad → exactamente una `success` y
  una `NO_AVAILABILITY` (el fake serializa con un `asyncio.Lock` interno —
  implementarlo así).

### H1.5 Las 3 patas del SDK (mismo PR)

1. `src/sdk/connectorkit/__init__.py`: agregar a `_LAZY_EXPORTS`
   `BookingPort` → `src.platform.booking.port` y `get_booking_port` →
   `src.platform.booking.composition`. Espejo eager en
   `src/sdk/connectorkit/ports.py` con alias idiom.
2. Verificar el guard: `test_sdk_lazy_surface` (compara ambos archivos) y
   que importar `src.sdk.connectorkit` NO cargue httpx/medusa (lazy).
3. Doc: sección "BookingPort" en `docs/_sdk/07-connectorkit.md` (qué
   soluciona, contrato de errores, fake, cómo se compone).

**Cierre H1**: panel §0.5 + `hubara-gate-reviewer` + PR
`feat(booking): H1 BookingPort + fake + contract suite (CB-01..17)`.

---

## H2 — `MedusaBookingVendor` (adapter live)

### H2.1 Settings y client
`BookingSettings` (env: `MEDUSA_BASE_URL`, `MEDUSA_ADMIN_TOKEN`,
`BOOKING_ENABLED`) siguiendo el patrón de settings de `medusa_order.py`.
HTTP client httpx async con tenacity-backoff sobre `httpx.TransportError`,
timeouts explícitos (connect 5s, read 15s).

### H2.2 Mapeo endpoint↔método (mecánico, del contrato §5)
`GET availability` / `GET extras` / `POST reservations` /
`POST …/extras` / `GET …/{id}` / `POST …/cancel` → los 6 métodos del port.
Mapeo de status HTTP a `error_code`: 409/422 → el `code` del body; 404 →
`NOT_FOUND`; 401/5xx → `UPSTREAM_UNKNOWN` con detail. Tests con
`respx` (o el mock httpx que ya use el repo — buscar cómo testea
`medusa_order.py` y calcar).

### H2.3 Recovery post-timeout (EN el adapter — regla L-1)
En `create_reservation` y `add_reservation_extra`:
- Connect error agotado → `success=False, error_code="UPSTREAM_UNREACHABLE"`
  (NO se aplicó; la tool puede reintentar el turno siguiente).
- **Read-timeout** → NO adivinar: `GET …/{client_reference}` (o buscar la
  `extra_reference` en `extras[]`); si existe → devolver ESE resultado
  (`idempotent_replay=True`); si `NOT_FOUND` → `UPSTREAM_UNREACHABLE`
  (seguro reintentar con la MISMA referencia). Test: simular read-timeout en
  el POST + 200 en el GET → el resultado es la reserva, sin duplicado.

### H2.4 Handshake INV-B5
En el primer uso (cacheado): `GET booking-contract`; comparar major contra
`SUPPORTED_CONTRACT_MAJOR = 1` (constante local). Mismatch → el vendor queda
inoperante y TODO método devuelve `error_code="CONTRACT_MISMATCH"`.

### H2.5 Composition
`get_booking_port()` con `@lru_cache(maxsize=1)`: live si
`BOOKING_ENABLED=1` y hay `MEDUSA_BASE_URL`/token; si no,
`FakeBookingVendor` con el fixture demo. Export ya hecho en H1.5.

**Cierre H2**: panel + gate-reviewer + PR.

---

## H3 — Tools LLM del agente

### H3.0 Explorar primero
`hubara-explorer`: mapa de (a) mecánica de `order_draft` (dónde vive, cómo
lo leen las tools), (b) la activity de red de seguridad idempotente
orden↔tag existente (caso `CONFIRMADO_PAGO_PENDIENTE`) — se va a calcar,
(c) dónde se validan los tags permitidos (⚠️ si es
`src/platform/constants.py`, es PROTECTED → regla §0.2.5).

### H3.1 Las tools (`src/plugins/chats/agent/sales/tools/booking.py`)
Inertes (sin httpx/temporal), `BookingPort` por constructor, envelope
textual al LLM con datos listos para narrar. TDD contra `FakeBookingVendor`
(incluye asertar el TEXTO del envelope):

| Tool | Port call | Envelope (resumen) |
|---|---|---|
| `check_room_availability(check_in, check_out, guests)` | `check_availability` | opciones con desglose por noche y total; vacío → texto "sin disponibilidad" + sugerir fechas alternativas |
| `list_hotel_extras(extra_type?)` | `list_extras` | catálogo con precios, para ofrecer/upsell |
| `register_reservation(...)` | `create_reservation` | `registered=true` + resumen de cuenta; `PRICE_CHANGED` → instruir re-cotizar; falla dura → instruir `escalate_to_human(reason_category="RESERVATION_FAILED")` |
| `add_reservation_extra(...)` | `add_reservation_extra` | cargo agregado + grand total actualizado; `RESERVATION_NOT_ACTIVE` → explicar/escalar |
| `get_reservation_status(...)` | `get_reservation` | estado + extras + totales |
| `cancel_reservation(...)` | `cancel_reservation` | requiere confirmación explícita previa del cliente (documentado en el docstring-patrón LLM) |

### H3.2 Fingerprints deterministas (en las tools, no en el LLM)
```python
client_reference = f"wa:{session_key}:{sha256(f'{room_type}|{check_in}|{check_out}|{guests}'.encode()).hexdigest()[:8]}"
extra_reference  = f"wa:{session_key}:{sha256(f'{extra_handle}|{quantity}|{scheduled_for}|{n_cargo}'.encode()).hexdigest()[:8]}"
```
`n_cargo` = índice incremental del draft (un SEGUNDO desayuno legítimo es
otro ítem del draft, no un replay). Sin `random`/`now` (R-DET). Test: dos
llamadas idénticas de la tool → misma referencia → una sola reserva en el
fake.

### H3.3 Red de seguridad reserva↔tag
Calcar la activity idempotente existente (H3.0.b): si hay reserva
`confirmed` sin tag `RESERVA_EXITOSA`, ponerlo — nunca al revés. La tool
emite la decisión; el workflow converge (lección Temporal-atomicity).

### H3.4 Registro en el worker
En `src/plugins/chats/workers/sales.py`, dentro del bloque de
`register_tool_extension`, gateado por `TENANT_VERTICAL == "hotel"` (§0.3):
las clases importadas AL TOP del archivo (gotcha del lambda). Después:
`ruff check --select F821 src/`. Test: con la env seteada las tools
aparecen; sin ella, el set de tools actual queda idéntico.

**Cierre H3**: panel + gate-reviewer + PR.

---

## H4 — Workspace hotelero

### H4.1 Explorar primero
`hubara-explorer`: cómo se carga `workspace/` (SOUL.md, skills de etapa,
TOOLS.md), cómo `resolve_funnel_stage` decide etapa sobre `order_draft`, y
cómo el prompt-diet inyecta el guion por etapa. Con ese mapa, implementar la
selección `workspace_hotel/` por env en el composition root (decisión §0.3).

### H4.2 `reservation_draft`
Estado del draft en metadata (calco de `order_draft`): fechas, huéspedes,
tipo elegido, extras elegidos (con `n_cargo`), total cotizado. Es la fuente
de los fingerprints y del `expected_total_cop`.

### H4.3 Etapas del funnel hotel (skills de etapa deterministas)
`indagacion` (fechas/huéspedes) → `disponibilidad` → `cotizacion` (incluye
ofrecer tours como adicional — upsell UNA sola vez) → `datos_huesped` →
`confirmacion` → `estadia` (reserva activa: room service / tour →
`add_reservation_extra`) → `postcierre`. Función `resolve_funnel_stage`
hotelera sobre `reservation_draft` + estado de reserva, con unit tests por
transición (calco del patrón retail).

### H4.4 SOUL, tags y guards
- `workspace_hotel/SOUL.md` + `TOOLS.md` (reglas de cierre: la secuencia
  legítima availability → cotización → confirmación del cliente →
  `register_reservation` → tag `RESERVA_EXITOSA`, calco del patrón de
  `register_order`).
- Tag `RESERVA_EXITOSA` donde viva la lista de tags (PROTECTED-aware, §0.2.5).
- Guard de voseo extendido a `workspace_hotel/` + `booking.py`.
- Ratchet de prompt budget para el prompt hotelero (calco de
  `test_prompt_budget`).
- Los planes turísticos SUELTOS siguen viviendo en el mismo agente
  (tools de catálogo/checkout intactas); con reserva activa el guion de
  etapa prefiere el camino de adicionales (una sola cuenta).

**Cierre H4**: panel + gate-reviewer + PR.

---

## H5 — Fusión y verificación E2E

1. **Contract suite live**: `BOOKING_CONTRACT_LIVE=1 MEDUSA_BASE_URL=<staging>
   MEDUSA_ADMIN_TOKEN=<token>` → los CB-01..17 param `"live"` corren contra
   el Medusa de staging con el plugin (F3/F4 del otro plan hechos). Verde =
   contrato cumplido por ambos lados. ⚠️ Correr SOLO la suite live con esas
   env (no exportarlas globalmente — cuelga `tests/platform/`).
2. **Smoke conversacional** (sandbox WhatsApp): consultar disponibilidad,
   cotizar con un tour adicional, reservar, pedir room service sobre la
   reserva activa, consultar estado. **Verificar comportamiento, no schema**:
   la order aparece en el dashboard de Pedidos con line items legibles
   (habitación + extras), el tag `RESERVA_EXITOSA` quedó, la reserva existe
   en Medusa. Evidencia: capturas del dashboard (stack Docker local — puertos
   REALES en `docker ps`, frontend :5174, API :8000; NUNCA :5173).
3. Panel completo `/hubara-gates all` + TCK (`uv run python -m src.sdk.cli
   certify`).

## Encaje con el silo Cartagena

Se implementa en el mainline **detrás del port** (sin `TENANT_VERTICAL` el
comportamiento retail es idéntico; el fake mantiene CI verde sin Medusa
hotelero). El silo Cartagena nace por clonación (`forge`) con el vertical
incluido; activar = env del deployment del silo (`TENANT_VERTICAL=hotel`,
`BOOKING_ENABLED=1`, URL/token del Medusa con plugin).

## Definición de "terminado" (este repo)

1. CB-01…CB-17 verdes contra fake (CI) y contra staging live (H5.1).
2. Smoke conversacional E2E verde con evidencia visual del dashboard.
3. Panel §0.5 + `/hubara-gates all` + TCK verdes; sin `TENANT_VERTICAL`, cero
   cambio de conducta retail (suite existente intacta).
4. Spec `specs/booking/spec.md` mergeada; contrato v1.0.0 idéntico en ambos
   repos (verificable con `diff`).
