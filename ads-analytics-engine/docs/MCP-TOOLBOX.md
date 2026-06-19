# Meta Ads MCP — toolbox + playbook de análisis (READ-ONLY)

Guía para los agentes: qué tools del MCP oficial usar y cómo combinarlos para el
**mejor análisis posible**. Verificado en vivo sobre la cuenta Hubara
(`1010393601284112`, COP, beta enabled).

## 🚫 Regla de oro: SOLO LECTURA
El analista **nunca** crea, modifica, activa, pausa, borra ni cambia presupuestos.
Esas son **decisiones humanas**. Prohibido llamar a cualquier tool de mutación:
`ads_create_*`, `ads_update_entity`, `ads_activate_entity`, `ads_boost_ig_post`,
`ads_*_create/update/delete` (audiencias, pixel events), cambios de budget.
Si el análisis sugiere una acción (escalar, rotar, combinar adsets), se **recomienda**
en el informe para que un humano la ejecute — el agente no la ejecuta.

## El dato base (para el motor)
| Tool | Para qué |
|---|---|
| `ads_get_ad_accounts` | descubrir cuenta + `is_ads_mcp_enabled` + `currency`. Si `is_ads_mcp_enabled:false` → NO usar esa cuenta. |
| `ads_get_ad_entities` | **el caballo de batalla.** `level` (account/campaign/adset/ad), `fields` (`amount_spent`, `actions:link_click`, `results`, `cost_per_result`, `impressions`, `reach`, `cpc`, `ctr`, `frequency`, `objective`…), `date_preset`/`time_range`, `time_increment=1` (diario), `breakdowns`, `filtering`, `sort`. → guardar JSON → `ads-engine mcp-report`. |
| `ads_get_field_context` | verificar nombres de campos antes de pedirlos (evita el error "Unsupported field"). |

> Recordá: el MCP devuelve **strings formateados** (`"$ 896.823 COP"`) y `results`
> depende del `objective`. El motor (`mcp-report`) ya los parsea bien — no calcules a mano.

## Insights ya calculados por Meta (úsalos, NO recalcules)
| Tool | Pregunta que responde |
|---|---|
| `ads_get_opportunity_score` | **"¿Qué hago?"** Score 0-100 + recos priorizadas por `opportunity_score_lift` (scale_good_campaign, fragmentation, mixed_formats, advantage+…). Empezar SIEMPRE por acá. |
| `ads_insights_anomaly_signal` | **"¿Qué anda raro?"** Spikes/drops, público narrow, bajo delivery. Observación, no causa. |
| `ads_insights_performance_trend` | **"¿Hacia dónde va?"** CPC/CPM/CPR/ROAS/CTR/CVR en el tiempo. |
| `ads_insights_auction_ranking_benchmarks` | **"¿Qué tan competitivo?"** quality/engagement/conversion rank + overlap de subasta (fragmentación). |
| `ads_insights_industry_benchmark` | **"¿Cómo voy vs la industria?"** vs peers por spend-tier + optimization goal. |

## Salud y operación
| Tool | Para qué |
|---|---|
| `ads_get_errors` | issues que **bloquean entrega** (no performance). "¿Por qué no entrega?" |
| `ads_account_get_activity_logs` | historial de cambios (quién tocó budget/status/targeting y cuándo). "¿Qué cambió?" |

## Señales / medición (¿confío en los datos?)
`ads_get_datasets` · `ads_get_dataset_quality` · `ads_get_dataset_stats` ·
`ads_get_customconversions` · `ads_pixel_*_read` → salud del pixel/CAPI. Si la calidad
de señal es baja, las "Meta purchases" subcuentan aún más → el blend con ventas
manuales (el motor) es más confiable que la atribución de Meta.

## Creativos + competencia
`ads_get_creatives` · `ads_get_ad_preview` · `ads_get_ad_images`/`videos` ·
`ads_get_ig_media` → qué creativo está corriendo (para fundamentar "rotar creativo").
**`ads_library_search`** → anuncios de la **competencia** (qué están corriendo otros).

## 🧩 Receta del informe (cómo se teje "el mejor análisis")
1. **Unit-economics duros** → el **MOTOR** (`ingest-sales` + `mcp-report`): funnel por
   campaña (drop-off, costo/conv), ventas por objetivo, y el blend de cuenta (MER/CPA
   con ventas manuales). **Números deterministas, no inventados.**
2. **"Qué hacer"** → `opportunity_score` (top recos por lift).
3. **"Qué anda raro"** → `anomaly_signal`.
4. **"Cómo voy"** → `performance_trend` + `industry_benchmark`.
5. **"Por qué" (diagnóstico fino)** → `ads_get_ad_entities` con `breakdowns`
   (placement, device, hora del día, edad/género, región) + `auction_ranking_benchmarks`.
6. **"¿Está sano?"** → `errors` + `activity_logs` + `dataset_quality`.
7. **Contexto competitivo** → `ads_library_search`.

→ El **Analyst** teje 1–7 en un informe accionable. Los **números duros** salen del
motor (paso 1, golden-tested); el resto es data que **Meta ya calculó**, presentada e
interpretada (no recalculada). Cada recomendación de acción es para que la ejecute un
**humano** (regla de oro).
