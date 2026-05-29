# Ads Analysis Agent — Documento de diseño (seed de desarrollo)

> Estado: **DRAFT / punto de partida**. Este doc fija la arquitectura, las fuentes
> de datos, las métricas y el comportamiento del agente de análisis de pauta.
> No es código ni una capability spec todavía — es el blueprint que alimenta el
> refinamiento técnico y el roadmap.
>
> Fecha: 2026-05-27 · Contexto de negocio: ventas WhatsApp (CTWA) en Colombia,
> tienda de velas/aromas, pricing per-message de Meta desde jul-2025.

---

## 1. Propósito y objetivo de negocio

Construir un agente que **analice el costo y el rendimiento de la publicidad**
(Meta Ads → WhatsApp CTWA) cruzando tres fuentes de datos, y que **sugiera
cambios accionables** para mejorar la rentabilidad de la pauta.

La pregunta que el negocio quiere responder, semana a semana:

> "¿En qué campaña/anuncio estoy ganando plata, en cuál la estoy perdiendo, y
> qué debería hacer al respecto?"

El objetivo no es un dashboard más: es **cerrar el loop** entre lo que Meta
cobra, lo que cuesta la conversación de WhatsApp, y lo que realmente se vende —
y devolverle al sistema (y a Meta) la señal para vender mejor.

**No-goals (de esta primera etapa):** ejecutar cambios automáticamente en Meta
(pausar/escalar campañas sin humano), generar creativos, ni gestión de
presupuesto autónoma. El agente **sugiere**; el humano (o una etapa posterior)
**decide**.

---

## 2. Resumen de la arquitectura

Decisión tomada (ver §8 para el detalle del razonamiento):

- **El agente vive en un servicio externo, en su propio repositorio**, basado en
  **LangGraph + AgentSpan** (durable execution sobre Netflix Conductor). Razón:
  aislamiento de despliegue — iteramos el razonamiento del agente sin tocar ni
  redesplegar el repo central que corre las conversaciones de venta en vivo.
- **El repo central (`hubara_agency`) usa Temporal** como **agendador y
  coordinador**: dispara el análisis en cadencia, espera de forma durable el
  resultado (puede tardar minutos u horas), y lo ingiere para mostrarlo en el
  frontend y para el loop de feedback.
- **La costura entre ambos es un contrato HTTP versionado** (`POST /v1/analyze`).
  El `execution_id` que devuelve AgentSpan es la idempotency key.

```
┌────────────────────────────── REPO CENTRAL (hubara_agency) ──────────────────────────────┐
│                                                                                            │
│  Temporal Schedule                Temporal Workflow                  Frontend (plugin ads) │
│  ┌───────────────┐   trigger   ┌────────────────────────┐  result  ┌──────────────────┐   │
│  │ daily watchdog │──────────▶ │ AdsAnalysisWorkflow      │ ───────▶ │ /api/chats/ads/* │   │
│  │ weekly deep    │            │  - start analysis (act.) │          │  + sugerencias   │   │
│  │ monthly strat. │            │  - async-wait result     │          └──────────────────┘   │
│  └───────────────┘            │  - persist + CAPI feedback│                                │
│                               └───────────┬──────────────┘                                │
│   Lee: vault + orders (BD interna)        │ POST /v1/analyze  (execution_id)              │
└───────────────────────────────────────────┼──────────────────────────────────────────────┘
                                             │  poll get_status() / callback signal
┌────────────────────────────── SERVICIO EXTERNO (ads-analysis, otro repo) ─────────────────┐
│                                             ▼                                              │
│   AgentSpan runtime (Conductor)  ──  task graph (DAG)  ──  durable, resumible              │
│                                                                                            │
│   fetch_meta_metrics ─┐                                                                    │
│                       ├─▶ match_attribution ─▶ analyze_campaign[i] ─▶ aggregate ─▶ recommend│
│   fetch_internal_data ┘        (LLM por campaña, fan-out paralelo)                         │
│                                                                                            │
│   Llama: Meta Marketing API (spend/impressions) + recibe vault+orders del central          │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

**Quién es dueño de qué durabilidad:**
- La durabilidad de la **corrida de análisis** (sobrevivir crashes a mitad de los
  6 nodos del DAG, reanudar sin re-pagar tokens LLM) → **AgentSpan/Conductor**.
- La durabilidad de "se agendó, se disparó, vamos a recibir y persistir un
  resultado" → **Temporal**.
- No se duplica: Temporal NO checkpointea los pasos internos del agente.

---

## 3. Stack tecnológico

| Capa | Tecnología | Rol | Por qué |
|---|---|---|---|
| Agendador / coordinador central | **Temporal** (ya en el repo) | Cron durable, espera async del resultado, ingesta, acciones downstream | Ya es el motor de orquestación del sistema; garantiza que el análisis corre y el resultado aterriza |
| Runtime del agente | **AgentSpan** (sobre Netflix Conductor) | Durable execution del DAG de análisis, resumible, observable | "Agents that don't die when your process does" — Conductor da durable execution real (no solo checkpoints) |
| Framework del agente | **LangGraph** (LangChain 1.x) | Autoría del grafo de razonamiento, integración con AgentSpan | Ergonomía para topologías de razonamiento; AgentSpan lo soporta nativo |
| Estructura del workflow | **Task graph (DAG)** | Pipeline determinista con fan-out paralelo por campaña | El análisis es pipeline-shaped (fetch→match→analizar→sugerir), no agente-abierto |
| LLM | Claude (Anthropic) vía el provider que ya usamos | Razonamiento analítico + generación de sugerencias | Mejor modelo de razonamiento; ya integrado vía LiteLLM/provider |
| Fuente externa | **Meta Marketing API** (Graph API, última versión) | spend, impressions, clicks, CPM, learning phase | Es la mitad del costo que el vault no tiene |
| BD interna | **El vault** (`hubara_vault/wa_*/metadata.json`) | Costo WhatsApp, atribución, outcome, link a orders | Ya existe; `check_costs.py` es el lector canónico |
| Revenue | **Orders plugin** (Medusa) | `total_cop` por order ganada | Cierra el ROAS real |
| Observabilidad del razonamiento | AgentSpan UI + OpenTelemetry / (LangSmith opcional) | Traza por nodo, tokens, latencia | Para "mejorar el agente constantemente" |
| Observabilidad del outcome | Repo central (vault + orders) | ¿La sugerencia mejoró el ROAS después? | El loop de feedback vive donde está el dato real |

---

## 4. Fuentes de datos

Hay **tres** fuentes que el agente cruza. La clave de join entre todas es el
**`source_id`** (id del anuncio que originó la conversación CTWA).

### 4.1 BD interna — el vault (`hubara_vault/wa_<phone>/metadata.json`)

Una carpeta por cliente WhatsApp. El schema (derivado de `scripts/check_costs.py`
y `use_cases/list_ads_campaigns.py`, que son los lectores canónicos):

```jsonc
{
  "active_route": "ventas",            // ruta de agente activa
  "tag": "...",                        // tag conversacional raíz
  "origin": {                          // ATRIBUCIÓN — de dónde vino el cliente
    "channel": "ad",                   // "ad" | "post" | "web_referral" | "direct"
    "source_id": "120203928401234",    // ⭐ id del anuncio Meta — CLAVE DE JOIN
    "source_type": "ad",
    "headline": "Regala aroma este 12 de mayo",  // título del anuncio
    "first_seen_ms": 1746...,
    "last_seen_ms": 1746...
  },
  "episodes": [                        // N conversaciones del mismo cliente en el tiempo
    {
      "episode_id": "ep_...",
      "started_at_ms": 1746...,        // para madurez de atribución + filtros temporales
      "closing_tag": "COMPRA_EXITOSA", // ⭐ OUTCOME: COMPRA_EXITOSA | RECHAZO | TIMEOUT | null(activo)
      "order_id": "order_01J...",      // ⭐ link a orders → revenue
      "cost_summary": {                // ⭐ COSTO WhatsApp del episodio
        "total_usd_micros": 12500,     // 10^-6 USD (12500 = $0.0125)
        "messages_count": 14,
        "messages_billable_count": 9,
        "messages_free_count": 5,
        "messages_pending_count": 0,   // >10% sostenido = webhook roto
        "by_category": {               // marketing | utility | service | authentication
          "marketing": { "count": 2, "usd_micros": 25000 },
          "utility":   { "count": 1, "usd_micros":   800 }
        },
        "by_pricing_type": { "...": { "count": 0, "usd_micros": 0 } }
      },
      "outbound_messages": [           // detalle por mensaje saliente
        {
          "wa_message_id": "wamid...",
          "sent_at_ms": 1746...,
          "cost_usd_micros": 12500,    // null = pricing pending (webhook no llegó)
          "template_name": "promo_dia_madre_v3",
          "kind": "template",
          "pricing": { "category": "marketing", "pricing_type": "..." }
        }
      ]
    }
  ]
}
```

**Qué nos da el vault (disponible HOY):**
- **Costo de la conversación WhatsApp** por episodio/cliente (`total_usd_micros`),
  desglosado por categoría (marketing/utility/service/authentication) y pricing_type.
- **Atribución**: `origin.source_id` + `channel` → vincula la conversación al anuncio.
- **Outcome**: `closing_tag` (`COMPRA_EXITOSA` = ganado, `RECHAZO`/`TIMEOUT` = perdido).
- **Link a revenue**: `episodes[].order_id`.
- **Salud de datos**: `messages_pending_count` / ratio pending (webhook capture health).

> ⚠️ **Importante — qué NO es el vault:** la categoría `marketing` en el vault son
> **plantillas de marketing de WhatsApp** (mensajes de remarketing salientes), **NO**
> el gasto de Meta Ads (CPM/CPC del anuncio). Son dos costos distintos que sumamos,
> no el mismo. No confundirlos.

**Cómo se lee hoy (CLI de referencia — `check_costs.py`):**
```bash
cd hubara_agency
uv run python scripts/check_costs.py summary --since 7d      # spend total + breakdown
uv run python scripts/check_costs.py by-channel --since 7d   # spend + win_rate por canal de origen
uv run python scripts/check_costs.py episodes --closing-tag COMPRA_EXITOSA  # ganados + Avg CAC (solo WA)
uv run python scripts/check_costs.py marketing --since 7d    # gasto en templates marketing
uv run python scripts/check_costs.py pending --detail        # health del webhook capture
```
El agente NO va a shellear este script — va a leer el vault con la misma lógica
(probablemente reusando/expandiendo `list_ads_campaigns`), pero el script define
**el contrato del schema** y las agregaciones que ya validamos.

### 4.2 Meta Marketing API (a integrar — NO existe hoy)

Es la mitad faltante del costo. Jerarquía Meta: **Ad Account → Campaign → Ad Set → Ad**.
El `source_id` del vault es típicamente el **id del Ad** (CTWA referral) — se sube
por la jerarquía (Ad → Ad Set → Campaign) vía la API.

Métricas a traer del endpoint **Insights** (por ad / ad set / campaign, con
`time_range` y `time_increment=1` para serie diaria):

| Campo Meta | Uso |
|---|---|
| `spend` | ⭐ gasto en pauta — la mitad del CAC que falta |
| `impressions`, `reach`, `frequency` | alcance, saturación de audiencia (fatiga) |
| `clicks`, `ctr`, `cpc`, `cpm` | eficiencia del anuncio en traer el click |
| `actions` (messaging_conversation_started) | conversaciones CTWA según Meta (cruzar con vault) |
| `cost_per_action_type` | costo por conversación iniciada según Meta |
| `objective`, `optimization_goal` | qué optimiza el ad set (típico: "Mensajes") |
| `delivery_info` / `learning_stage_info` | ⭐ estado de learning phase (no sugerir cambios si está aprendiendo) |
| `targeting` (audience) / `adset` budget | contexto para sugerencias de presupuesto |

> A confirmar en integración: nombres exactos de los `action_type` de CTWA
> (`onsite_conversion.messaging_conversation_started_7d` u similar), versión del
> Graph API, y el campo de learning phase. Auth: System User token + permisos
> `ads_read`.

### 4.3 Orders plugin (revenue)

`episode.order_id` → orders plugin → **`total_cop`** (entero, COP) + `created_at`.
Hoy el orders API ya hace `_fetch_order_total_and_date(order_id)` contra Medusa.
Revenue atribuido a una campaña = suma de `total_cop` de las orders de sus
episodios ganados.

### 4.4 La clave de join (atribución end-to-end)

```
Meta Ad (spend, impressions)
      │  ad_id == origin.source_id
      ▼
Vault conversation (WhatsApp cost, closing_tag, order_id)
      │  order_id
      ▼
Order (total_cop = revenue)
```

Una campaña, vista entera, es: **lo que Meta cobró por traer el click** +
**lo que costó la conversación de WhatsApp** vs **lo que se vendió**.

---

## 5. Métricas — catálogo (traídas + derivadas)

### 5.1 Traídas directo

| Métrica | Fuente | Unidad |
|---|---|---|
| `meta_spend` | Meta API | COP o USD (según ad account) |
| `impressions`, `reach`, `clicks`, `frequency` | Meta API | conteo |
| `cpm`, `cpc`, `ctr` | Meta API | derivadas por Meta |
| `learning_stage` | Meta API | enum (LEARNING / SUCCESS / LIMITED) |
| `whatsapp_cost` | Vault `total_usd_micros` | USD micros |
| `conversations_started` | Vault (episodios con source_id) | conteo |
| `won` / `lost` | Vault `closing_tag` | conteo |
| `revenue` | Orders `total_cop` (de episodios ganados) | COP |
| `funnel counts` | Vault classifier (nuevo→activo→…→ganado/perdido) | conteo por estado |

### 5.2 Derivadas (lo que el agente calcula)

| Métrica derivada | Fórmula | Para qué |
|---|---|---|
| **Costo total por campaña** | `meta_spend + whatsapp_cost` | costo real, no solo Meta |
| **CAC real** | `(meta_spend + whatsapp_cost) / won` | cuánto cuesta ganar un cliente de verdad |
| **ROAS** | `revenue / (meta_spend + whatsapp_cost)` | retorno sobre el gasto total |
| **Costo por conversación iniciada** | `meta_spend / conversations_started` | eficiencia CTWA |
| **Tasa de conversión del embudo** | `won / conversations_started` y por etapa | dónde se cae el embudo |
| **WhatsApp cost por ganado** | `whatsapp_cost / won` | peso del costo conversacional |
| **Margen estimado** | `revenue - costo_total` (o con margen de producto) | rentabilidad neta |

### 5.3 Gotcha de unidades y moneda (NO ignorar)

- WhatsApp cost = **`usd_micros`** (10^-6 USD). Hay lección previa en el repo:
  pricing sub-cent NO se modela con cents; usar micros (ver memoria
  `cost_unit_lesson`).
- Revenue = **COP** (entero).
- Meta spend = **COP o USD** según el ad account (Colombia → probablemente COP).
- **El agente DEBE normalizar todo a una moneda** (recomendado: COP, porque el
  negocio y las orders son COP) con un FX explícito y fechado. Mezclar monedas en
  el CAC/ROAS es el bug #1 esperable. Definir la fuente de FX en la integración.

---

## 6. Cómo el agente analiza, hace match y sugiere

El núcleo es un **task graph (DAG)** durable. Cada nodo es una tarea con su retry;
el fan-out por campaña corre en paralelo.

### 6.1 Nodos del DAG

```
1. fetch_meta_metrics      (Meta API)         ─┐
                                                ├─▶ 3. match_attribution
2. fetch_internal_data     (vault + orders)   ─┘         │
                                                          ▼
                                          4. analyze_campaign[i]   ← LLM, fan-out paralelo (1 por campaña)
                                                          │
                                                          ▼
                                          5. aggregate (portfolio view)
                                                          │
                                                          ▼
                                          6. recommend (LLM, sugerencias priorizadas)
                                                          │
                                                          ▼
                                          7. emit (resultado JSON + CAPI feedback opcional)
```

- **1–2 (fetch):** tareas idempotentes, retry agresivo. Meta API tiene rate
  limits → backoff. El vault/orders los provee el central (ver §8) o el servicio
  los lee de una read-API.
- **3 (match):** join por `source_id`. Construye, por campaña, el objeto unificado
  {meta + whatsapp + revenue + funnel}. **Aplica madurez de atribución**: para
  ROAS/CAC solo cuenta episodios con `started_at_ms ≥ 7 días` (los datos recientes
  mienten — ver §7). Marca lo inmaduro como "preliminar".
- **4 (analyze_campaign):** un nodo LLM por campaña. Recibe las métricas derivadas
  + contexto (learning phase, fatiga, embudo) y produce un **diagnóstico**: ¿gana o
  pierde plata? ¿dónde se cae el embudo? ¿la audiencia está saturada? ¿el costo por
  conversación subió?
- **5 (aggregate):** vista de portfolio — ranking por ROAS, dónde está concentrado
  el gasto improductivo, oportunidades de reasignación.
- **6 (recommend):** genera **sugerencias accionables y priorizadas** (ver 6.3),
  cada una con: acción, justificación cuantitativa, impacto estimado, y un flag de
  "¿esto resetea el learning phase?".
- **7 (emit):** estructura el resultado y, opcionalmente, dispara el feedback CAPI.

### 6.2 El "match" (atribución) en detalle

Por cada campaña (`source_id`):
1. Del **vault**: agrupar episodios con ese `source_id` → conversaciones iniciadas,
   counts por estado del embudo, ganados/perdidos, costo WhatsApp total, order_ids.
2. De **orders**: por cada `order_id` de episodios ganados → `total_cop` → revenue.
3. De **Meta**: por el `ad_id == source_id` → spend, impressions, clicks, learning.
4. Unificar en un `CampaignAnalysis` con todas las métricas derivadas (§5.2),
   normalizadas a COP.

### 6.3 Qué sugerencias produce (taxonomía)

| Tipo de sugerencia | Trigger típico | Ejemplo |
|---|---|---|
| **Reasignar presupuesto** | Campaña A ROAS alto vs B ROAS bajo | "Mover 20% del presupuesto de C a A" |
| **Pausar / revisar** | CAC > margen, ROAS < 1, 0 ganados con gasto alto | "Pausar 'Aromaterapia': CAC $X > ticket $Y" |
| **Escalar (con cuidado)** | ROAS alto + fuera de learning phase | "Escalar A +15-20%/día, no doblar (resetea learning)" |
| **Embudo** | Caída fuerte en una etapa | "70% se cae en 'cotizado→ganado': revisar precio/objeciones" |
| **Creativo / fatiga** | Frequency alta + CTR cayendo | "Frequency 4.2 y CTR -30%: rotar creativo" |
| **Calidad de señal** | Optimiza por 'mensajes' pero las ventas no escalan | "Enviar 'ganado' a Meta vía CAPI para optimizar por venta real" |
| **Salud de datos** | pending ratio > 10% | "Webhook de pricing roto: CAC no confiable hasta arreglar" |

### 6.4 Guardrails del agente (críticos para no hacer daño)

Estas reglas vienen de cómo funciona Meta de verdad (§7) y deben ir en el prompt
+ validarse en código:

1. **Nunca sugerir cambios que reseteen learning phase** sin marcarlo explícito y
   sin agrupar ediciones. Cada reset cuesta ~5-15% del ROAS de la semana siguiente.
2. **No sugerir cambios sobre datos inmaduros** (< 7 días de atribución). Etiquetar
   como "esperar maduración".
3. **No sugerir sobre campañas en learning phase** salvo problema catastrófico
   (gasto sin ninguna conversación tras 48h).
4. **Cambios de presupuesto ≤ 20%** por iteración (sobre 20% resetea learning).
5. **El agente sugiere, no ejecuta.** Toda acción sobre Meta pasa por humano (en
   esta etapa).

---

## 7. Cadencia — cada cuánto analizar

Hallazgo contraintuitivo (respaldado por cómo opera Meta): **analizar-para-actuar
más seguido empeora la pauta**, por dos mecánicas:

- **Learning phase:** ~50 eventos de optimización por ad set / 7 días para salir de
  aprendizaje; ediciones significativas resetean el contador (costo de ROAS real).
- **Madurez de atribución:** ventana default 7-day click / 1-day view; los últimos
  1-3 días están incompletos; el gasto de un día acumula conversiones hasta 7 días
  después. El ROAS de "ayer" miente.

→ **Cadencia escalonada en tres capas** (mapea a tres Temporal Schedules):

| Capa | Frecuencia | Qué hace | ¿LangGraph pesado? |
|---|---|---|---|
| **Watchdog** | Diario | Spend pacing, anomalías, learning status, pending ratio. **Solo alerta.** | No — pull barato |
| **Análisis profundo** | **Semanal** | ROAS/CAC sobre cohorte maduro (≥7d), embudo, sugerencias. **Capa que ACTÚA.** | Sí |
| **Estratégico** | Mensual | Portfolio, fatiga, saturación, LTV por fuente | Sí, corrida rica |

Regla: **monitorear diario (read-only), analizar-para-actuar semanal.**

---

## 8. La costura — contrato Temporal ↔ AgentSpan

### 8.1 Disparo y espera (patrón async-completion)

1. **Temporal Schedule** dispara `AdsAnalysisWorkflow` (overlap policy `Skip` o
   `BufferOne` — si una corrida semanal se pasa, no apilar).
2. Una **activity** hace `POST /v1/analyze` → el servicio responde **rápido** con
   `{ execution_id }` (AgentSpan `runtime.start()`). La activity completa.
3. El workflow **espera de forma durable** el resultado sin bloquear un worker:
   - **Opción A (recomendada):** async activity completion — el servicio llama de
     vuelta a Temporal al terminar (callback con el `execution_id`).
   - **Opción B:** poll — activity que consulta `get_status(execution_id)` con
     timer + heartbeat hasta `done`.
4. Al recibir el resultado, el workflow lo **persiste** (vault/store), lo expone en
   `/api/chats/ads/*`, y opcionalmente dispara el **CAPI feedback**.

`execution_id` = **idempotency key**: un retry de Temporal NO relanza el análisis,
reusa la ejecución durable de AgentSpan.

### 8.2 Contrato (borrador)

```jsonc
// POST /v1/analyze   (central → servicio)
{
  "analysis_type": "deep" | "watchdog" | "strategic",
  "window": { "since_ms": 1746..., "until_ms": 1746... },
  "currency": "COP",
  "fx": { "usd_to_cop": 4100.0, "as_of_ms": 1746... },
  "internal_data_ref": "...",   // cómo el servicio obtiene vault+orders (ver 8.3)
  "idempotency_key": "weekly-2026-W21"
}
// → 202 { "execution_id": "exec_..." }

// Resultado (servicio → central, vía callback o poll)
{
  "execution_id": "exec_...",
  "generated_at_ms": 1746...,
  "currency": "COP",
  "campaigns": [{
    "source_id": "120203928401234",
    "name": "Velas vainilla · Día de la Madre",
    "metrics": { "meta_spend": ..., "whatsapp_cost": ..., "revenue": ...,
                 "cac": ..., "roas": ..., "conversations_started": ...,
                 "won": ..., "lost": ..., "funnel": {...},
                 "learning_stage": "SUCCESS", "data_maturity": "mature" },
    "diagnosis": "texto del LLM",
    "suggestions": [{
      "type": "reallocate_budget",
      "action": "Mover 20% de C a A",
      "rationale": "ROAS A=3.4 vs C=0.8 ...",
      "estimated_impact": "...",
      "resets_learning_phase": false,
      "confidence": 0.0-1.0,
      "complexity": "simple" | "needs_human"
    }]
  }],
  "portfolio_summary": "...",
  "alerts": [{ "severity": "warn|critical", "message": "..." }]
}
```

### 8.3 Cómo el servicio obtiene la BD interna (decisión abierta — §12)

Dos opciones:
- **(a) El central exporta** un snapshot del vault+orders y lo pasa por referencia
  (URL firmada / payload) en el request. Más simple, el servicio queda stateless
  respecto del vault.
- **(b) El central expone una read-API** (`GET /api/chats/ads/raw?since=...`) que el
  servicio consume. Más acoplado pero datos siempre frescos.

Recomendación inicial: **(a)** para mantener el servicio externo desacoplado del
almacenamiento interno.

---

## 9. Loop de feedback (lo que nos hace "mejores vendedores")

Dos mitades de observabilidad, ambas necesarias:

1. **Traza del razonamiento** (qué pensó el agente, por nodo) → AgentSpan UI /
   OTel. Sirve para mejorar **el agente**.
2. **Outcome real** (¿la sugerencia, aplicada, mejoró el ROAS la semana siguiente?)
   → vive en el **repo central** (vault + orders). Sirve para mejorar **la pauta**.

**Palanca de mayor impacto — CAPI:** hoy Meta optimiza por "conversación iniciada",
NO por venta (la venta ocurre dentro del chat, Meta no la ve). Devolverle el evento
**`COMPRA_EXITOSA` vía Conversions API** hace que el algoritmo optimice por ventas
reales. Esto es más palanca que cualquier dashboard. El nodo `emit` del DAG (o una
activity central post-resultado) dispara este evento.

Persistir: cada sugerencia emitida + si se aplicó + el delta de ROAS posterior, para
realimentar al agente y medir su valor.

---

## 10. Cómo se muestra en el frontend

Ya existe el plugin `ads` (frontend-only, FSD) consumiendo `/api/chats/ads/*`:
overview header, lista de campañas, funnel, distribución por estado, tendencia
diaria, tabla atribuida, inspector. Hoy `spend/revenue/impressions` salen `null`.

Cambios:
- **Poblar los `null`** con los datos reales (Meta spend, revenue de orders) una vez
  integrados → los componentes dejan de mostrar "— / dataPending".
- **Nueva feature `ads-suggestions`**: panel que muestra `suggestions[]` del último
  análisis, con severidad, justificación cuantitativa y el flag de learning phase.
- **Badge de frescura/madurez**: marcar visualmente datos "preliminares" (< 7 días)
  vs "maduros".

---

## 11. Roadmap de desarrollo (fases)

| Fase | Entregable | Depende de |
|---|---|---|
| **F0 — Datos base** | Confirmar/extender lectura del vault (atribución + costo + outcome + order link) ya disponible; normalización de moneda; FX source | — |
| **F1 — Integración Meta** | Cliente Meta Marketing API (spend/impressions/clicks/learning) + mapping `source_id` (ad) → ad set → campaign | F0 |
| **F2 — Revenue link** | `order_id` → `total_cop` consolidado por campaña (ya existe parcialmente en orders API) | F0 |
| **F3 — Servicio externo (esqueleto)** | Repo nuevo + AgentSpan + DAG con nodos fetch/match (sin LLM aún) + `POST /v1/analyze` → execution_id | F1, F2 |
| **F4 — Razonamiento del agente** | Nodos `analyze_campaign` + `recommend` (LLM) + guardrails + contrato de sugerencias | F3 |
| **F5 — Costura Temporal** | `AdsAnalysisWorkflow` + Schedules (watchdog/semanal/mensual) + async-completion + ingesta | F3 |
| **F6 — Frontend** | Poblar nulls + feature `ads-suggestions` + badges de madurez | F4, F5 |
| **F7 — Feedback loop** | CAPI feedback (`COMPRA_EXITOSA` → Meta) + tracking de outcome de sugerencias | F5 |

---

## 12. Riesgos y decisiones abiertas

**Riesgos:**
1. **Madurez de AgentSpan** (~172★, proyecto joven). Mitigante: el contrato de la
   costura desacopla — si AgentSpan no madura, se reemplazan las tripas del servicio
   (Conductor directo / LangGraph Platform) sin tocar el central. Confirmar
   self-host de Conductor antes de comprometerse.
2. **Dos motores durable execution** (Temporal + Conductor). Justificado por
   aislamiento, pero más superficie operativa. Disciplina: **un solo scheduler**
   (Temporal), dueño único de cada durabilidad.
3. **Mezcla de monedas** (USD micros / COP / spend Meta) → bug esperable en CAC/ROAS.
   Normalizar explícito con FX fechado.
4. **Atribución imperfecta**: `source_id` puede faltar (origen `direct`), o un cliente
   puede venir de un anuncio y comprar semanas después fuera de ventana. Tratar
   "direct" como campaña sintética (ya lo hace `list_ads_campaigns`).
5. **Webhook pricing pending**: si el ratio sube, el costo WhatsApp no es confiable.
   El watchdog debe degradar el análisis y avisar, no calcular CAC sobre datos rotos.

**Decisiones abiertas:**
- [ ] ¿El servicio externo lee la BD interna por snapshot (a) o read-API (b)? (§8.3)
- [ ] ¿`analysis_type=watchdog` necesita LangGraph o es un check barato en el central?
- [ ] Fuente de FX USD→COP (y con qué frecuencia se refresca).
- [ ] ¿CAPI feedback en F7 o adelantarlo? (es alta palanca)
- [ ] Granularidad del análisis Meta: ¿ad, ad set o campaign? (el join natural es ad)
- [ ] Margen de producto para el "margen neto" — ¿lo tenemos en catalog/orders?

---

## 13. Glosario de métricas

- **CAC (Customer Acquisition Cost):** costo total de adquirir un cliente ganado =
  `(meta_spend + whatsapp_cost) / won`.
- **ROAS (Return On Ad Spend):** `revenue / costo_total`. > 1 = rentable (antes de
  margen).
- **CTWA (Click-To-WhatsApp Ads):** anuncios de Meta cuyo CTA abre una conversación
  de WhatsApp. El `referral.source_id` es el id del anuncio.
- **Learning phase:** período en que el algoritmo de Meta explora; necesita ~50
  conversiones/7d para estabilizar. Ediciones lo resetean.
- **Ventana de atribución:** 7-day click / 1-day view (default 2026). Conversiones se
  atribuyen retroactivamente → datos recientes inmaduros.
- **CAPI (Conversions API):** API server-side para devolverle conversiones a Meta
  (ej. la venta real cerrada en WhatsApp).
- **usd_micros:** unidad de costo = 10^-6 USD (evita el bug de redondeo sub-cent).

---

## Apéndice — referencias en el repo

- Lector canónico del schema de costos: `hubara_agency/scripts/check_costs.py`
- Atribución existente (agrupa por `source_id`): `hubara_agency/src/plugins/chats/agent/sales/use_cases/list_ads_campaigns.py`
- Endpoints ads actuales: `hubara_agency/src/plugins/chats/api/ads.py`
- Frontend plugin: `frontend_dashboard/src/plugins/ads/` + `frontend_dashboard/src/entities/ads-campaign/`
- Revenue (orders): `hubara_agency/src/plugins/orders/vault_scanner.py` (`total_cop`) + `api/__init__.py` (`_fetch_order_total_and_date`)
- Patrón agente DEHA/Temporal (si algo se hiciera in-repo): `hubara_agency/src/platform/workflow_helpers.py` (`run_agent_turn`)
</content>
</invoke>
