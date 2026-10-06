# Plugin: order_sentinel

> Behavior contract — bootstrap 2026-07-09 (feature Order Sentinel).
> Fuente: `hubara_agency/src/plugins/order_sentinel/` +
> `GraphAgents/graphs/order_sentinel.py` + `manifests/order-sentinel.agent.yaml`.

## Purpose

Ciclo autónomo diario que lee las conversaciones WhatsApp escaladas a humano
(tag `HUMANO`) con orden vinculada, interpreta la conversación completa con un
agente LLM en la caja GraphAgents (`order-sentinel`), y ejecuta los cambios de
estado del pedido que el humano ya comunicó por chat: transiciones del kanban
(`preparing → ready → shipping → delivered`) y confirmación de pago. La
autoridad es SIEMPRE la API de orders (validación DAG real); el agente solo
propone intents bajo guardrails deterministas.

## Requirements

### Requirement: Elegibilidad de conversaciones

El ciclo SHALL analizar ÚNICAMENTE sesiones con `tag == "HUMANO"` que tengan
una orden real de Medusa vinculada (`order_`/`draft_`; los stubs HUB-/AUDIT-
no cuentan) y mensajes nuevos posteriores al watermark propio de la sesión
(`<session>/order_sentinel.json`, ajeno a metadata.json).

#### Scenario: Sesión del bot no se analiza

- GIVEN una sesión con tag `INTERESADO` y orden vinculada
- WHEN corre el ciclo
- THEN la sesión queda fuera del snapshot (el bot sigue a cargo; ETA notifica)

#### Scenario: Sin mensajes nuevos no se paga LLM

- GIVEN una sesión HUMANO cuyo último mensaje es anterior al watermark
- WHEN corre el ciclo
- THEN la sesión queda fuera y NO se consulta la API de orders para ella
- AND con snapshot vacío el ciclo termina `skipped_empty` sin prender la caja

### Requirement: El LLM propone, el código dispone

El agente SHALL descartar de forma determinista (visible en `suppressed`)
todo verdict del LLM que: proponga un stage fuera de
{preparing, ready, shipping, delivered} (`cancelled` NUNCA por inferencia);
no sea el paso ADYACENTE del DAG desde el stage actual; tenga
`confidence != high`; duplique la misma orden; confirme un pago ya confirmado
o de una orden cancelada; o cite evidencia que no aparezca textual en la
conversación (`evidence_not_found`, anti-alucinación).

#### Scenario: Señal ambigua no mueve el pedido

- GIVEN el operador escribe "de pronto te lo mando hoy, te aviso"
- WHEN el agente clasifica `confidence: medium`
- THEN el intent queda `suppressed: low_confidence` y el pedido no se toca

### Requirement: Ejecución con supresión de ETA

El ciclo SHALL ejecutar cada intent vía la API HTTP de orders con
`by: "order-sentinel"` y `notify_customer: false` (el humano ya avisó por
chat: no sale WhatsApp; el evento CAPI de la etapa sí). `invalid_transition`/`invalid_state` SHALL contarse como `skipped`
(carrera benigna con el humano / draft sin agendar), no como fallo.

#### Scenario: Carrera con el operador es benigna

- GIVEN el operador ya arrastró la tarjeta a `shipping` durante el día
- WHEN el ciclo intenta la misma transición
- THEN la API responde `invalid_transition` y el intent cuenta como `skipped`

### Requirement: El watermark solo cierra lo analizado

Las sesiones cuyo análisis falló (entrada en `llm_errors`, orden ilegible,
o truncadas por el cap del ciclo) SHALL conservar su watermark para que el
próximo ciclo las re-analice. Un run que no expone `dispatch` extraíble
SHALL terminar `result_missing_dispatch` sin ejecutar ni cerrar watermarks.

#### Scenario: Proxy LLM caído no pierde señal

- GIVEN LiteLLM inalcanzable para una conversación
- WHEN el run completa con esa sesión en `llm_errors`
- THEN su watermark NO avanza y el ciclo siguiente la re-analiza

### Requirement: Lector de Jev del estado del pedido (motor de decisiones F8)

El lector SHALL fijarlo Terraform (`ORDER_SENTINEL_READER`: `off` | `shadow` |
`on`; nace `off`). Con `off` el snapshot, el run y el resumen del ciclo SHALL
ser los de hoy y Jev NO se llama. Con `shadow` u `on`, la activity del
snapshot SHALL preguntarle a Jev por cada conversación «¿qué cambió?» (nada,
en preparación, listo, en camino, entregado, pago confirmado) y, solo si Jev
está seguro (p ≥ 0,85) de que algo cambió, un sí/no por cada mensaje NUEVO
desde el watermark (del equipo o del cliente; el pago, solo del equipo; a lo
sumo 16) que podría probarlo. El veredicto de Jev SHALL tener la forma del
veredicto del LLM, con la evidencia textual de los mensajes que Jev marcó
(p ≥ 0,85). Si Jev duda, cae o tarda, la lectura MUST NOT traer veredicto:
decide el LLM. El LLM se sigue llamando siempre (es la regla de hoy y la vara
se mide contra él). En `shadow` actúa el LLM; en `on` actúa el veredicto de
Jev cuando lo hay. Las guardas del agente MUST ser las mismas para las dos
fuentes. Lo que Jev ve MUST ir anonimizado con las casillas personales del
borrador del pedido vinculado. Cada lectura SHALL dejar una métrica
(`DecisionMetrics`, capacidad `estado_pedido`) y los DESACUERDOS entre lo que
el LLM y Jev harían despachar SHALL ir a la cola única que califica Claude
Code (`DisagreementLog`); registrar es observabilidad: si falla, el ciclo
igual termina.

#### Scenario: En sombra el desacuerdo se registra y actúa el LLM

- GIVEN el lector en `shadow`, el operador escribió "ya salió con el mensajero" y el pedido está `ready`
- AND Jev lee `en_camino` con esa cita y el LLM no ve señal
- WHEN corre el ciclo
- THEN no se despacha nada (actúa el LLM)
- AND la cola de desacuerdos recibe `estado_pedido` con `rule: {action: none}` y `jev: {action: transition, to_stage: shipping}`

#### Scenario: Con el lector encendido actúa Jev bajo las mismas guardas

- GIVEN el lector en `on` y Jev lee `entregado` para un pedido `ready`
- WHEN el agente planifica
- THEN el veredicto de Jev queda `suppressed: invalid_transition` (`by: jev`), igual que si lo hubiera propuesto el LLM

#### Scenario: Jev caído no cambia nada

- GIVEN el lector en `on` y Jev responde timeout
- WHEN corre el ciclo
- THEN la lectura trae `verdict: null` y `error: timeout`, decide el LLM como hoy y la métrica cuenta la caída
