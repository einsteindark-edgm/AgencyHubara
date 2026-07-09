# WhatsApp Window Strategy — "Free-First Funnel"

> Doc VIVO. Semilla de los sprints de adaptación al cambio de pricing de Meta
> (non-template messages, verificado 2026-07-03). Fuente de verdad de la
> **estrategia de aprovechamiento de ventanas** y del **motor central de
> decisión de envío**. Cuando el código contradiga este doc, gana el código —
> y se actualiza este doc.

## TL;DR

1. Desde **1-oct-2026** Meta empieza a cobrar los mensajes free-form del bot
   (service messages) y los utility templates dentro de la ventana de 24h.
   Hoy son gratis. → cada burbuja del bot pasa a costar ~$0.0008 (CO).
2. La ventana **CTWA de 72h (Free Entry Point)** cubre **TODO gratis, incluso
   marketing templates**, y **queda como el único carril 100% gratis** después
   del 1-oct. Hay que exprimirla.
3. **"Utility con código de promoción" NO funciona** — Meta recategoriza a
   marketing automáticamente (desde 9-abr-2025). Utility solo es barata si es
   **genuinamente transaccional** (pedido/carrito/pago pendiente).
4. Toda decisión de envío pasa por **una central pura** (`send_policy`): sales,
   remarketing y watchdog la consultan SÍ o SÍ antes de mandar. Choke point
   único, enforced por gate de arquitectura.
5. Un **agente GraphAgents** inspecciona las conversaciones, clasifica ventana +
   warmth + gancho transaccional, y decide qué reactivar/cuándo mandar un
   utility legítimo fuera de ventana — proponiendo, no ejecutando (G-DUR).

## 1. Timeline de pricing (fechas duras)

| Fecha | Qué se cobra | Nota |
|---|---|---|
| **1 ago 2026** | Mensajes vía Meta Business Agent (per-token, ~$2/1M tok ≈ 4-5¢/msg) | Solo si adoptamos MBA |
| **1 sep 2026** | Meta publica el rate de *service* ("igual que utility/auth") | En CO ≈ $0.0008/msg |
| **1 oct 2026** | **Service messages** (free-form del bot en ventana 24h) → per-message | 🔴 toca sales+remarketing |
| **1 oct 2026** | **Utility templates dentro de la CSW** dejan de ser gratis | 🔴 toca el watchdog |
| — | **72h CTWA Free Entry Point** — "unchanged for message delivery" | ✅ sigue gratis |

## 2. Mecánica de las dos ventanas (NO fusionar — controlan cosas distintas)

- **24h Customer Service Window (CSW)** → controla **SI podés** mandar free-form
  (non-template). Se **resetea con CADA inbound del cliente**.
- **72h Free Entry Point (FEP, CTWA)** → controla **si es GRATIS**. Es **fija
  desde tu primera respuesta al lead CTWA, NO se renueva** con inbounds
  subsecuentes.

Consecuencia: estar **dentro de las 72h pero fuera de las 24h** = podés mandar
**solo templates** (free-form bloqueado), y esos templates son **gratis**.

**Cada inbound del cliente resetea las 24h.** Diseñar la conversación para
provocar respuestas tiene valor económico directo (mantiene el carril barato
abierto).

## 3. La matriz de costo por envío (post 1-oct-2026)

| Estado de ventana | Free-form (service) | Utility template | Marketing template |
|---|---|---|---|
| **Dentro de 72h CTWA** | GRATIS *(si además en 24h)* | **GRATIS** | **GRATIS** |
| En 24h, fuera de 72h | $0.0008 | $0.0008 | $0.0125 |
| Fuera de 24h y de 72h | 🚫 bloqueado | $0.0008 | $0.0125 |

*(Hasta el 1-oct la fila del medio es toda gratis; después, así.)*

## 4. Estrategia: Free-First Funnel

**Principio rector:** gastar agresivo mientras es gratis, cortar quirúrgico
cuando cuesta.

### Fase A — Lead CTWA, dentro de las 72h (carril gratis)
- **Sales empuja fuerte.** Front-load el intento de conversión en las primeras 72h.
- **Remarketing reactiva GRATIS si se congela.** Límite = UX/cadencia, NO
  presupuesto. Cliente respondió <24h → free-form gratis. Respondió >24h pero
  <72h → **template de reactivación, gratis**.
- **Watchdog = aliado.** Nudea antes de que cierren las 24h para provocar una
  respuesta → resetea las 24h → mantiene el free-form vivo dentro de las 72h.

### Fase B — Cerraron las 72h y el lead NO se calentó
Acá cualquier reactivación es paga. **Se corta.**
- **Lead frío** (ignoró los toques gratis) → **SUPRIMIR**. No tirar marketing
  pago = plata perdida.
- **Lead tibio con gancho transaccional** (pedido a medias, carrito reservado,
  pago pendiente) → **UTILITY genuina** ($0.0008): *"Tu pedido #123 sigue
  reservado, ¿lo confirmamos?"*. NUNCA promo disfrazado.
- **Lead tibio sin gancho, alto valor esperado** → **UN marketing** ($0.0125) y
  parar.

Decisión Fase B = `costo del toque pago vs valor esperado del lead` (data de
atribución CTWA + CAPI que ya montamos).

## 5. Rectificaciones de política (verificadas)

- ✅ **72h CTWA cubre marketing templates gratis** (tabla oficial Meta).
- 🔴 **Promo en utility = recategorización auto a marketing** (desde 9-abr-2025,
  `allow_category_change` es default). Promo/oferta/descuento/CTA "comprá ahora"
  (incl. en botones) → cobrado como marketing o rechazado + baja quality rating.
- 🔴 **El "24h gratis orgánico" vence el 1-oct-2026.** Después, cada service
  message orgánico se cobra. Post-oct, CTWA 72h es el único carril gratis.
- ⚠️ **General-purpose AI prohibido desde 15-ene-2026.** Nuestros agentes son
  purpose-scoped (sales de catálogo, remarketing) → OK. No abrir a charla libre.

## 6. Arquitectura: el motor central de decisión de envío (`send_policy`)

**Mandato:** ningún subsistema decide por su cuenta si/cómo/con qué costo mandar.
**Toda la info sale de la central.**

```
send_policy(now_ms, metadata, lead_state, intended) -> SendDecision
```

- **Puro** (R-DET): sin Temporal, sin `datetime.now()`. El caller pasa `now_ms`
  desde una activity. Vive en `platform/whatsapp/` (messaging cross-plugin).
- **Input:**
  - `now_ms` — reloj inyectado.
  - `metadata` — ventanas (`service_window_expires_at_ms`,
    `ctwa_window_expires_at_ms`).
  - `lead_state` — warmth + ganchos transaccionales (order_draft, ctwa_clid,
    tags, replied?).
  - `intended` — qué quiere mandar el caller (free-form / template + category).
- **Output `SendDecision` (frozen, R-JSON):**
  - `allowed: bool` + `suppress_reason: str | None`
  - `channel: "free_form" | "template"`
  - `recommended_category: "service" | "utility" | "marketing" | "authentication"`
  - `is_free: bool` + `expected_cost_micros: int`
  - `rationale: str` (observabilidad — por qué esta decisión)
- **Absorbe** el acantilado del 1-oct (rate card `co_2026q4_v1.yaml`) y el fix
  del short-circuit `free_customer_service` de `cost.py:164`.

**Enforcement:** gate de arquitectura — ninguna activity de send se invoca sin
una `SendDecision` previa (choke point único, sin bypass).

## 7. GraphAgents: el "Window Strategist" agent (AUTÓNOMO, sin HITL)

Subsistema aparte (LangGraph + AgentSpan + manifests). Se desarrolla en su
propia rama; acá solo el plan → **`GRAPHAGENTS_WINDOW_STRATEGIST_PLAN.md`**.

Un agente **autónomo** que se activa por conversación, clasifica cada lead
(ventana 72h/24h/expirado + warmth + gancho transaccional) y **despacha
reactivaciones a remarketing (hubara) cuando las ventanas lo hacen rentable** —
exprime el carril gratis de 72h, usa utility barata cuando hay motivo
transaccional real (confirmación/reminder de pedido casi listo, pago pendiente),
y **suprime** los fríos fuera de ventana.

**Sin HITL** (decisión del operador). No hay gate humano. La seguridad del gasto
es doble: (1) el agente solo despacha lo que su política ventana×warmth×cadencia×
presupuesto marca rentable; (2) **hubara re-valida cada envío con la central
`send_policy` al ejecutar** → ningún gasto no autorizado por la central, aunque
el agente se equivoque. **G-DUR** se cumple porque el agente nunca gasta directo:
emite *intents de dispatch*; el "approval" es programático (central + guardrail
de presupuesto/cadencia del nodo `plan`), no humano.

**G-DET:** golden-replay — `now` entra por payload, no `datetime.now()`. El
grafo `ingest → classify → plan → dispatch → END` es puro.

## 8. Plan de implementación (workstreams)

- **WS0 — Doc + memoria.** (este doc + memorias). ✅
- **WS1 — Motor central `send_policy` (pura, TDD).** La pieza de la que cuelga
  todo. Incluye rate card `co_2026q4` + fix short-circuit. Rojo primero.
- **WS2 — Rewire de los 3 consumidores** (sales / remarketing / watchdog) para
  consultar la central antes de cada envío + gate de arquitectura (no send sin
  `SendDecision`). Watchdog window-aware + idempotencia dura (duplicado ahora
  pago).
- **WS3 — Agente GraphAgents "Window Strategist"** — autónomo, SIN HITL (ver
  §7): emite intents de dispatch; hubara re-valida cada envío con la central
  (gate `decide_reengagement` en el remarketing workflow). Plan refinado:
  `GRAPHAGENTS_WINDOW_STRATEGIST_PLAN.md`.
- **WS4 — Bucle de medición** — costo-de-servir por lane vs conversión; calibra
  el umbral de la Fase B.

**Secuencia:** WS1 → WS2 → WS3, con WS4 transversal. WS1 desbloquea todo.

## 9. Bucle de medición

`EpisodeCostSummary` ya trackea `by_category`/`by_pricing_type`. Falta cruzarlo
con conversión: **costo-de-servir por lead vs valor generado**, segmentado por
lane (72h-gratis / paga / suprimido). Ese número calibra, semana a semana, el
umbral de la Fase B. Conecta con el motor de unit-economics CTWA.

## 9-bis. Estado real + runbook (honestidad post-premortem 2026-07-03)

**Qué está cableado hoy vs qué es capa lista:**
- ✅ `evaluate_send` consultado en el path de gasto (`send_template_to_session`)
  → estampa `last_outbound_policy` en cada template send.
- ⏳ `decide_reengagement`/`LeadState` = **capa funnel lista, SIN caller de
  producción**. Wiring en vivo = fase siguiente (agente GraphAgents / remarketing).
  NO afirmar "choke point único enforced" hasta ese wiring.
- ⏳ `last_outbound_policy` se ESCRIBE pero **nadie lo lee todavía** — la data de
  lane está capturada; el consumidor (dashboard/aggregation) es follow-up. NO
  reclamar "medición implementada".

**🔴 RUNBOOK #1 — el flip del rate card el 1-oct (premortem M1, el fallo más caro):**
El default de `get_current_rate_card()` sigue siendo `co_2026q2_v1` (service=0).
`co_2026q4_v1` (service=800) **solo entra si se setea
`WHATSAPP_RATE_CARD_VERSION=co_2026q4_v1`** en el deploy del 1-oct. **No hay
guard automático** que lo fuerce ni que compare `now_ms` contra el
`effective_from_ms` del card. Si nadie flippea el env el 1-oct → **subconteo
silencioso de TODO el gasto de service**. Fix propio (follow-up): selección de
rate card date-aware por `effective_from_ms`. Mientras tanto, es item de deploy
obligatorio + (idealmente) una alerta si el card activo tiene service=0 después
del 1-oct.

## 10. Incógnitas a verificar (contra webhook real, no doc)

- 🔴 **Trigger de las 72h** — nuestro `window.py` computa desde
  `ctwa_first_touch_at_ms` (primer inbound), pero Meta arranca las 72h desde
  **nuestra primera respuesta** (hasta 24h después) → subestimamos ventana
  gratis. Autoritativo = `pricing.type=free_entry_point` del webhook.
- 🔴 **Shape del `pricing` post-1-oct** — ¿Meta manda `pricing_type=regular,
  category=service` o sigue mandando `free_customer_service` mientras cobra? Si
  es lo segundo, `cost.py:164` pone $0 en silencio. Verificar contra webhook
  real después del 1-oct.

## 11. Fuentes

- Meta — Non-template message pricing: https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing/non-template-messages
- Meta — Pricing overview: https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing
- Meta — Template categorization: https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/template-categorization
- SleekFlow — CTWA 72h free window: https://help.sleekflow.io/en_US/whatsapp/understanding-click-to-whatsapp-ads-ctwa-and-the-72-hour-free-window
- hello-charles — WhatsApp pricing 2025: https://www.hello-charles.com/blog/whatsapp-business-pricing-2025-explained-the-ultimative-guide
