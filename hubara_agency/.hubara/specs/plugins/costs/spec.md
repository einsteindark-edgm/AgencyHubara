# Plugin: costs

> Behavior contract — bootstrap 2026-11-04.
> Fuente: `hubara_agency/src/plugins/costs/` + `frontend_dashboard/src/plugins/costs/`.

## Purpose

La pestaña **Costos** dice cuánto cuesta el sistema productivo en un rango de
fechas. Solo lee: no escribe en el vault ni en otros plugins.

| Tarjeta | Fuente | Credencial |
|---|---|---|
| DeepSeek (el bot) | `episodes[].llm_usage.cost_usd` de cada conversación del vault | ninguna |
| Jev (OpenRouter) | `episodes[].jev_usage` (lo que OpenRouter cobró por pregunta) | ninguna |
| Gemini (fotos y audios) | `episodes[].vision_usage` + `audio_usage` | ninguna |
| WhatsApp | `pricing_analytics` DAILY del WABA (incluye campañas y envíos fuera de una conversación) | `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_BUSINESS_ACCOUNT_ID` |
| Temporal Cloud | `GET /cloud/usage` × tarifa de `costs.yaml` + tarifa del plan | `TEMPORAL_API_KEY`, `TEMPORAL_NAMESPACE` |

## Requirements

### Requirement: Rango de fechas acotado, por defecto dos semanas

El sistema SHALL consultar días calendario de Bogotá entre una fecha de inicio
y una final (ambas incluidas), por defecto los últimos 14 días terminando hoy.
SHALL rechazar con 422 y mensaje en español un rango con inicio posterior al
final, una fecha final futura o más de 92 días.

#### Scenario: Sin fechas

- GIVEN hoy es 2026-11-04 en Bogotá
- WHEN se pide `GET /api/costs/providers/temporal` sin `start`/`end`
- THEN se consulta del 2026-10-22 al 2026-11-04

### Requirement: Ninguna consulta cuelga la pestaña

Cada proveedor SHALL tener su propio endpoint (`GET /api/costs/providers/{id}`),
un tiempo máximo por llamada (`timeout_seconds`) y caché por (proveedor,
rango). Consultas simultáneas iguales SHALL compartir una sola llamada al
proveedor. Un proveedor lento o caído SHALL volverse una tarjeta en error, sin
afectar a las demás ni devolver 500.

#### Scenario: Proveedor que no responde

- GIVEN un proveedor que tarda más que `timeout_seconds`
- WHEN se pide su informe
- THEN la respuesta es 200 con `status: "error"` y `error` explicando que no respondió
- AND esa respuesta no se guarda en caché

### Requirement: Sin dato no es cero

Un proveedor sin credencial (o con el placeholder de SSM) SHALL responder
`status: "not_connected"` con `how_to_connect`, sin llamar a nadie; las
tarjetas de conversaciones, lo mismo si el entorno no tiene vault. En ese caso
y en `error`, `production_usd_micros` SHALL ser `null`, y el total de la
pestaña SHALL decir «Parcial» y nombrar lo que falta.

### Requirement: Los costos de LLM salen de las conversaciones

DeepSeek, Jev y Gemini SHALL sumarse de los costos que cada conversación de
producción guarda en `wa_*/metadata.json`, por día de Bogotá en que empezó la
conversación, sin llamar a la API de esos proveedores. La lectura SHALL ser una
sesión a la vez (solo las sumas en memoria), SHALL saltar las sesiones cuyo
`metadata.json` no se escribió desde el inicio del rango y SHALL compartirse
entre las tres tarjetas. Un archivo ilegible SHALL saltarse sin tumbar la tarjeta.

#### Scenario: Conversación que empezó antes del rango

- GIVEN una conversación que empezó el 2026-10-30 con costo de LLM
- WHEN se piden los costos del 2026-11-02 al 2026-11-03
- THEN esa conversación no suma, aunque su archivo se haya escrito después

### Requirement: Solo lo productivo suma

Cada línea SHALL declarar si es producción. Las conversaciones del vault de
producción lo son por construcción (los laboratorios y simulaciones locales no
escriben ahí); en Temporal, solo el namespace `TEMPORAL_NAMESPACE`. Lo demás
SHALL mostrarse aparte y no sumar al total productivo.

### Requirement: Dinero exacto y en dos monedas

Los montos SHALL viajar en micros enteros. Los pesos SHALL convertirse con la
TRM oficial de cada día (datos.gov.co; respaldo configurado si no responde,
dicho en la nota) y las otras monedas con la tasa de `costs.yaml`. Una moneda
sin tasa SHALL marcarse y no sumarse.

#### Scenario: Temporal cuadra con la factura

- GIVEN un mes con 100.000 acciones, 5 GB-hora activos y 500 GB-hora retenidos
- WHEN se valora con US$50/M acciones, US$0,042 y US$0,00105 por GB-hora y 10 % de tarifa del plan
- THEN el total es US$6,3085 (la misma fórmula cuadró al centavo con una factura real de Temporal)

### Requirement: Rutas autenticadas

Las rutas SHALL montarse con la autenticación por defecto de los plugins
(`require_auth`); el router SHALL NOT ser público.
