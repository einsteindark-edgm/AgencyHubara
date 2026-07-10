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
chat). `invalid_transition`/`invalid_state` SHALL contarse como `skipped`
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
