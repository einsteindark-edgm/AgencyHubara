# Traer TODA la data de una campaña CTWA — el caso "Día del padre"

> Playbook de adquisición de datos para campañas Click-to-WhatsApp. Sale de la
> investigación firsthand sobre la cuenta real de Hubara (`1010393601284112`,
> 2026-06-19/20). El bug que motivó todo: la campaña que más importa devuelve `0`
> por el camino "fácil". Detalle de la investigación: `dev-error-log.md`.

## TL;DR

1. **El MCP `ads_get_ad_entities` NO alcanza** para campañas purchase-conversion:
   "Día del padre" (activa, ~$255K gastados) devuelve `results: "0 (Meta purchases)"`
   — pero SÍ generó conversaciones de WhatsApp.
2. **La conversación vive en el `actions` de Graph `/insights`**, como
   `onsite_conversion.messaging_conversation_started_7d`.
3. **Traé las dos fuentes** y cruzá `spend`/`clicks` entre ellas: `ads_get_ad_entities`
   (vista objetivo-aware) **+** `/insights` con `actions` (la conversación real).

---

## 1. La trampa: `ads_get_ad_entities` undercuenta las purchase-conversion

`ads_get_ad_entities` (el tool del MCP oficial) devuelve `results` **dependiente del
objetivo**. Para "Día del padre" (`OUTCOME_SALES`, optimizada a mensajería→compra):

```json
{ "id": "120243118818600317", "name": "Día del padre 2026 - mundia",
  "objective": "OUTCOME_SALES",
  "results": { "value": "0 (Meta purchases)" },        // ← 0, ENGAÑOSO
  "cost_per_result": { "value": "$ 0 COP (Meta purchases)" },
  "amount_spent": "$ 255.105 COP", "actions:link_click": "467" }
```

Ese `0` es **real pero inútil**: es la **brecha de atribución CTWA** — la compra
ocurre en WhatsApp y Meta no la trackea. Las **conversaciones de mensajería** que la
campaña SÍ generó **no aparecen** en `results`.

## 2. La causa raíz: `result_type` sigue al `optimization_goal` del ADSET (no al `objective`)

Bajando a los adsets de "Día del padre" (los 3 iguales):

| `optimization_goal` del adset | `results` que reporta el MCP | ¿Trae la conversación? |
|---|---|---|
| `CONVERSATIONS` | "Messaging conversations started" | **Sí** (Duo zodiacal, semana santa…) |
| `MESSAGING_PURCHASE_CONVERSION` | "Meta purchases" = 0 | **No** ← Día del padre, Dia de la madre |

Las campañas de temporada de Hubara (las que más gastan) son
`MESSAGING_PURCHASE_CONVERSION` → por el MCP entities quedan en 0.

## 3. Las dos fuentes (y por qué necesitás ambas)

| Fuente | Tool / endpoint | Da | NO da |
|---|---|---|---|
| **MCP entities** | `ads_get_ad_entities` | spend, link_clicks, objetivo, el `results` objetivo-aware, cost_per_result | la conversación de mensajería en purchase-conversion |
| **Graph /insights** | `GET /{id}/insights` con `actions` | spend, inline_link_clicks, **la conversación** (`actions[...]`), por día | (es la fuente rica — de acá sale todo lo de unit-economics) |

El parser de cada shape ya existe en el motor: `meta_mcp.parse_ad_entities` (entities)
y `meta_insights.parse_meta_insights` (insights+actions). En GraphAgents, la tool
`meta-ads-insights` consume la forma **/insights con actions**.

## 4. La receta de fetch (paso a paso)

1. **Confirmar la cuenta** — `ads_get_ad_accounts` → `is_ads_mcp_enabled: true`,
   `is_queryable: true`, `currency: "COP"`. (Hubara = `1010393601284112`.)
2. **Resolver el id de la campaña** (Día del padre = `120243118818600317`).
3. **Fetch Graph `/insights` con `actions`** — la llamada que trae la conversación:

   ```
   GET /v21.0/120243118818600317/insights
     ?level=campaign                 # o adset/ad para granularidad
     &fields=spend,inline_link_clicks,actions,campaign_id,campaign_name,date_start,date_stop
     &action_breakdowns=action_type
     &time_increment=1               # diario (para joinear con ventas por día)
     &time_range={"since":"2026-06-01","until":"2026-06-19"}
     &access_token=<token>
   ```

   La respuesta trae `{"data":[{ "spend":"...", "inline_link_clicks":"...",
   "actions":[{"action_type":"...","value":"..."}], "date_start":"..." }]}`.

4. **La conversación CTWA** = el valor del action cuyo `action_type` es
   `onsite_conversion.messaging_conversation_started_7d` (ver §5).
5. **Cross-check** — `spend` e `inline_link_clicks` de `/insights` deben coincidir con
   `amount_spent` y `actions:link_click` de `ads_get_ad_entities`. Si coinciden, estás
   mirando la misma campaña y la conversación de `/insights` es la pieza que faltaba.
6. **(Contexto, opcional)** para el "¿qué hago?": `ads_get_opportunity_score`,
   `ads_insights_anomaly_signal`, `ads_insights_performance_trend` (Meta ya los
   calcula — citar, no recalcular). Ver `ads-analytics-engine/docs/MCP-TOOLBOX.md`.

## 5. Los `action_types` de mensajería (el corazón)

**Confirmado** (está en el código del motor, `meta_insights.py` `CTWA_ACTION_TYPE`):

- `onsite_conversion.messaging_conversation_started_7d` → **conversaciones de mensajería
  iniciadas (7d)**. Es LA señal del embudo CTWA.

**Candidatos a las columnas del UI "Contactos de mensajes totales / nuevos"** — pedirlos
en `fields=actions` y **verificar el nombre exacto** contra la primera respuesta real de
`/insights` (no inventar; la lista de actions varía por cuenta/objetivo):

- `onsite_conversion.total_messaging_connection` → "Contactos de mensajes totales" (probable).
- `onsite_conversion.messaging_first_reply` → primer respuesta (proxy de contacto nuevo).
- (otros `onsite_conversion.messaging_*` que aparezcan en el `actions` de esa campaña).

> **Regla:** la primera vez que el central fetchee `/insights` de una campaña
> purchase-conversion, **listá todos los `action_type` del `actions`** y fijá el mapeo
> real → registralo acá. No asumir nombres.

## 6. Snapshot real de "Día del padre" (2026-06-19/20, vía `ads_get_ad_entities`)

Campaña `120243118818600317` · `OUTCOME_SALES` · 3 adsets, todos `MESSAGING_PURCHASE_CONVERSION`:

| Adset | spend | link_clicks | results (MCP) |
|---|--:|--:|---|
| dia del padre segmentacion detallada | $176.294 | 344 | 0 (Meta purchases) |
| dia del padre segmentación abierta - Copia | $39.212 | 64 | 0 (Meta purchases) |
| dia del padre segmentacion similar | $39.672 | 59 | 0 (Meta purchases) |
| **campaña (total)** | **~$255.105** | **467** | **0 (Meta purchases)** |

→ El MCP entities da spend + clicks correctos, pero la **conversación = 0**. Esa
conversación hay que traerla de `/insights` `actions` (§4). Lo demás (órdenes/ingreso)
sale de las **ventas manuales de WhatsApp** (el blend de cuenta del motor).

## 7. Auth no-baneable (cómo lo usamos NOSOTROS)

`/insights` con `actions` se lee con un **token OAuth consentido + scope `ads_read`
(read-only)** — es el uso **sancionado** del Marketing API (no el patrón raw-token /
broker third-party / scraping que sí arriesga ban). En la arquitectura:

- El **proyecto central** (frontend) hace el OAuth oficial una vez y deposita el JSON
  crudo de `/insights` en un seam (SSM/S3/HTTP).
- El **agente de GraphAgents NUNCA toca Meta** — recibe ese JSON y lo parsea
  (`meta-ads-insights`). Cero superficie de ban. Ver memoria
  `graphagents_ads_port_constraints` (constraint #2/#3).

## 8. Verificación — ¿trajiste TODO?

- [ ] `spend` e `inline_link_clicks` coinciden entre `ads_get_ad_entities` y `/insights`.
- [ ] El `actions` de `/insights` trae `messaging_conversation_started_7d` con valor > 0
      (si la campaña corrió mensajería). Si da 0 **y** es purchase-conversion, revisá el
      `action_breakdowns`/los `action_type` disponibles antes de concluir "no hubo".
- [ ] El periodo (`time_increment=1`) cubre las mismas fechas que las ventas manuales,
      con la **misma zona horaria** (Bogotá) — un desfase de 1 día rompe el join.
- [ ] `currency == "COP"` en ambas (el motor se rehúsa a mezclar monedas).

## Lo que NO sirve (probado)

- **`ads_get_ad_entities` solo** → 0 en purchase-conversion.
- **Las columnas "Contactos de mensajes" del Ads Manager (UI)** → NO están en el catálogo
  de campos del MCP (bajé el catálogo entero). Solo se obtienen vía `/insights actions`.
- **`breakdowns: ["action_type"]`** en `ads_get_ad_entities` → lo **rechaza** la insights
  API (no es un breakdown válido ahí). El desglose de actions va por `action_breakdowns`
  en `/insights`, no por `breakdowns` en el tool de entities.
