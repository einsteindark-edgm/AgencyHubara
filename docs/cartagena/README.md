# Cartagena — vertical hotelero sobre Medusa (booking plugin propio)

> Carpeta de arranque del vertical **reservas de hotel + venta de planes
> turísticos** para tenants tipo Cartagena. Fecha de investigación: 2026-07-09.

## Decisión

**Nos quedamos en Medusa.** No se reemplaza el backend comercial: se le agrega
capacidad de reservas mediante un **plugin de Medusa v2 propio**, partiendo del
plugin open source [`@rsc-labs/medusa-booking-system`](https://github.com/RSC-Labs/medusa-booking-system)
(Apache 2.0, Medusa ≥ 2.7.0) como base vendorizada y adaptada.

Por qué este camino y no un PMS externo (QloApps / Odoo+OCA-pms):

1. **Conservamos el 100% del stack existente** — vendor Medusa, tools del
   agente, draft orders, sync de catálogo Meta, unit economics, dashboard de
   pedidos. Una reserva confirmada TERMINA siendo una order de Medusa, así que
   toda la maquinaria downstream (Pedidos, ETA, métricas) la ve gratis.
2. **Planes turísticos y room service = productos de Medusa.** Sueltos (sin
   reserva) ya funcionan hoy vía `CatalogPort` + `OrderRegistrationPort`;
   como ADICIONALES de una reserva viajan por el mismo contrato de booking
   (cargos a la cuenta, misma order).
3. **El plugin base ya trae lo difícil**: recursos reservables, holds con TTL,
   reglas de disponibilidad con prioridad, políticas, allocations, flujo
   booking-cart → order, pricing por moneda con integración a variants.
4. Un solo sistema que operar por silo (decisión 2026-07-09: silo total por
   cliente — repo + infra + Medusa propios).

Lo que el plugin base NO trae y agregamos en el fork: búsqueda de
disponibilidad **por fechas cross-recurso** (el agente pregunta "¿qué hay libre
del 15 al 18?", el plugin es recurso-céntrico), **reserva atómica de un solo
endpoint** con idempotencia por referencia, **adicionales sobre la reserva**
(planes turísticos y servicio a la habitación como cargos a la cuenta — al
crear la reserva o durante la estadía), y el **contrato versionado** que
Hubara consume.

## Los tres documentos

| Doc | Vive en | Quién lo ejecuta |
|---|---|---|
| [`CONTRATO-BOOKING.md`](CONTRATO-BOOKING.md) | **AMBOS repos, copiado literal** (fuente de verdad compartida, versionada) | Nadie lo "ejecuta": los dos planes lo implementan, cada uno de su lado |
| [`PLAN-MEDUSA.md`](PLAN-MEDUSA.md) | Se lleva al repo de Medusa (self-contained, no asume contexto de AgencyHubara) | Agente Claude Code en el repo Medusa, siguiendo el playbook al pie de la letra |
| [`PLAN-HUBARA.md`](PLAN-HUBARA.md) | Este repo | Agente Claude Code con el harness hubara-dev (skill `hubara-plugin-developer` + subagentes explorer/tdd-author/gate-reviewer) |

**Los dos planes son PLAYBOOKS ejecutables por un agente programador**, no
resúmenes: cada uno abre con una "Parte 0" de instrucciones para el ejecutor
(qué agente/harness usar, reglas no negociables, regla de STOP a los 3
fallos), todas las decisiones de arquitectura vienen YA TOMADAS (el ejecutor
no decide arquitectura), y cada fase tiene pasos atómicos con test rojo
primero, comandos exactos y gate de salida. Si un paso contradice el contrato
o el código real, el ejecutor PARA y reporta — no improvisa.

## Cómo se fusionan "por arte de magia"

La fusión no es magia: es **contract-first con verificación ejecutable en los
dos lados**. Tres mecanismos:

1. **El contrato se congela primero** (v1.0.0) y ambos planes solo hablan a
   través de él. El lado Medusa expone `/admin/hubara/*`; el lado Hubara
   implementa `BookingPort` → `MedusaBookingVendor` que consume esos endpoints.
   Ninguno conoce internals del otro.
2. **Los mismos escenarios (CB-01…CB-17) se implementan como tests en ambos
   repos.** En Medusa: integration tests del plugin. En Hubara: la contract
   suite del `BookingPort`, parametrizada para correr contra el
   `FakeBookingVendor` (in-memory, siempre) y contra el Medusa vivo con el
   plugin (smoke, gated por env). Regla 3 del ConnectorKit: *la misma suite
   corre contra fake y vendor*.
3. **Handshake de versión en runtime**: `GET /admin/hubara/booking-contract`
   devuelve `{"contract_version": "1.0.0"}`. El vendor de Hubara lo chequea y
   se niega a operar ante mismatch de major (regla de oro del plugin system:
   ningún campo del contrato sin su check).

Con eso, el día de la fusión es literalmente: apuntar `MEDUSA_BASE_URL` del
silo al Medusa con el plugin, correr la contract suite en modo live, verde =
fusionado.

## Orden de ejecución

```
1. Congelar CONTRATO-BOOKING.md v1.0.0   (revisión humana, este repo)
        │
        ├──────────────┬─────────────────────────┐
        ▼              ▼                          (en PARALELO)
2a. PLAN-MEDUSA    2b. PLAN-HUBARA
    F0→F4 en el        H0→H4 en este repo
    repo Medusa        (contra FakeBookingVendor,
                        no necesita el plugin vivo)
        │              │
        └──────┬───────┘
               ▼
3. Fusión: contract suite en modo live contra staging (H5)
4. Workspace hotelero del agente + verificación conversacional E2E
```

El paralelismo es real: Hubara desarrolla TODO contra el fake (que implementa
los mismos invariantes del contrato) y no espera al plugin. Es el mismo patrón
fake-first que ya usa el ConnectorKit para Medusa commerce.

## Investigación que respalda la decisión (2026-07-09)

- Medusa v2 soporta plugins desde v2.3.0 — paquetes con modules, workflows,
  API routes, links, subscribers y admin extensions
  ([docs](https://docs.medusajs.com/learn/fundamentals/plugins)).
- Recipe oficial de booking (ticket booking) con módulo custom + validación de
  disponibilidad vía workflow hooks
  ([recipe](https://docs.medusajs.com/resources/recipes/ticket-booking),
  [ejemplo](https://github.com/medusajs/examples/tree/main/ticket-booking)).
- Plugin base: [`RSC-Labs/medusa-booking-system`](https://github.com/RSC-Labs/medusa-booking-system)
  — Apache 2.0, Medusa ≥ 2.7.0, Node 20+, creado 2026-01, último push 2026-03.
  Joven (pocas stars) → se **vendoriza** (copia al repo, no dependencia npm):
  control total, sin riesgo de abandono upstream.
- Alternativas evaluadas y descartadas para este camino: QloApps (PMS completo
  pero stack PHP/PrestaShop aparte = segundo sistema que operar), Odoo+OCA/pms
  (anclado a Odoo 14, ERP pesado), PHPTravels (no es open source real).
