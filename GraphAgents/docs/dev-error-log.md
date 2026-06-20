# GraphAgents — bitácora de errores de desarrollo (input para graphagents-dev)

> **Journal append-only.** Cada error que cometa o descubra **al probar** se anota acá
> crudo, en el momento. Cuando un error se entiende del todo, se **promueve** a una
> lección `L-#` en
> `graphagents-dev/skills/graphagents-developer/references/04-lessons.md` (el canon,
> formato Síntoma → Causa raíz → Fix → Regla para el skill → Guard). Esta bitácora es
> el **staging**; el plugin es el **destino**. Nada se pierde: lo que acá es una nota
> cruda, allá se vuelve un gate.

## Cómo anotar (plantilla)

```
### YYYY-MM-DD · <título corto> (<contexto: qué estaba probando>)
- Síntoma: el error LITERAL (traceback, output, comportamiento).
- Hipótesis: qué creo que pasó.
- Qué probé: comandos / cambios.
- Resultado: qué confirmó/descartó.
- Promovido a: L-# (o "pendiente — falta entender la causa raíz").
```

Regla: cuando un run real revela un bug, el **primer** artefacto es un guard rojo que
lo reproduce — el "Guard" se escribe ANTES que el "Fix" (ley TDD, `00-tdd-law.md`).

---

## Entradas

### 2026-06-19 · El MCP oficial de Meta es OAuth-only — no hay headless sin-OAuth (research, pre-build)
- Síntoma/hallazgo: el MCP conectado (`https://mcp.facebook.com/ads`, first-party) autentica
  **solo por OAuth 2.0 interactivo**. No expone modo token / system-user. El único "headless"
  posible es heredar un token OAuth **cacheado a nivel host** (bootstrap interactivo + refresh = frágil).
- Hipótesis: un agente corriendo en un server no puede depender de OAuth interactivo.
- Qué probé: WebSearch + WebFetch de la doc oficial y de implementaciones open-source.
- Resultado: para headless sin-OAuth → **self-host** un Meta Ads MCP open-source con un
  **token de System User de Meta Business Manager** (env var del server, scope `ads_read`
  read-only), hablando al Marketing API oficial. NO usar un broker third-party **hosted**
  (ej. endpoint remoto de pipeboard con `PIPEBOARD_API_TOKEN` → tiene TU token = el patrón
  "third-party broker" que arriesga ban). Self-host + System User token + read-only = patrón
  server-to-server sancionado.
- Promovido a: **memoria persistente** `graphagents_ads_port_constraints.md` (constraint #2).
  Pendiente al construir la tool `meta-ads-insights`: elegir/self-hostear el server y fijar
  el nombre exacto del env var del token.

### 2026-06-19 · El CLI no tiene generador (`create` es stub) (revisión de sdk/cli.py)
- Síntoma: `sdk/cli.py::cmd_create` imprime `"TODO (G2): scaffold..."` — no genera nada.
- Hipótesis: crear una tool/agente a mano (copiando `tools/hello/`) introduce deriva de forma
  → errores no-deterministas que esta bitácora va a registrar de más si no lo cerramos.
- Resultado: spec del generador en `docs/cli-design-guide.md`. El `create` determinista hace
  cumplir TDD + regla de oro por construcción.
- **RESUELTO parcial (2026-06-19):** `create tool` implementado vía TDD (rojo
  `tests/architecture/test_cli_create.py` → verde `sdk/scaffold.py` + wiring en `sdk/cli.py`);
  verificado end-to-end (create → C2 → golden rojo por construcción → cleanup; suite 54 verde).
  Pendiente: `create capability|agent|connector`, `new-fixture`, `run --fixture`.
- Promovido a: doc de diseño + esta entrada (gap de tooling, no bug).

<!-- AÑADIR ENTRADAS NUEVAS ABAJO; PROMOVER A L-# CUANDO LA CAUSA RAÍZ ESTÉ CLARA -->

### 2026-06-19 · El MCP oficial NO expone "Contactos de mensajes"; `results` es por-objetivo (uso firsthand del MCP)
- Contexto: probé el MCP en vivo (cuenta Hubara `1010393601284112`) para basar el diseño en datos reales, no supuestos.
- Hallazgos:
  - `results` viene SIEMPRE poblado por-campaña y lleva su TIPO en el label ("Messaging conversations
    started" / "Meta purchases" / "Link clicks" / "Profile and Page visits" / "Facebook likes" /
    "Post engagements"). Ninguna de 18 campañas tuvo `results` vacío en el MCP → el "Resultados vacío"
    del Ads Manager es un ARTEFACTO de la columna del UI (una columna, un tipo de result, objetivos
    mixtos → blanco donde no matchea), NO un hueco del MCP.
  - El catálogo COMPLETO de campos del MCP NO tiene "Contactos de mensajes totales/nuevos" (ni
    messaging_contacts / total_messaging_connection / new_messaging_contacts). Solo results,
    result_values, cost_per_result, conversions, lead, clicks, cpc/ctr/cpm/cpp, reach, impressions,
    purchase_roas, y un set curado de actions:* (link_click, like, omni_purchase, comment,
    page_engagement, post_reaction, post_save).
  - `action_type` NO es breakdown válido en este MCP (lo rechaza la insights API).
- Implicación de diseño: la señal de conversaciones de mensajería = `results` cuando result_type ==
  "Messaging conversations started" (lo que ya hace `meta_mcp.is_messaging`). Campañas con headline
  result NO-mensajería (ej. "Dia de la madre": ENGAGEMENT, $983K, 1663 clicks, results "0 Meta
  purchases") → el MCP NO da sus conversaciones → funnel insufficient_data; se reportan por objetivo.
  Las columnas "Contactos de mensajes" del UI NO se obtienen por este MCP oficial.
- LECCIÓN para el plugin: **verificar el contrato de datos REAL del MCP/fuente antes de diseñar el
  parser/resolver — un supuesto sobre columnas (tomado del UI) puede no existir en la API.** Probar
  firsthand ahorró construir un resolver para campos inexistentes.
- Investigación profunda (a pedido del operador): la causa raíz es el `optimization_goal` del adset.
  `CONVERSATIONS` → `results` = "Messaging conversations started" (TENEMOS la señal).
  `MESSAGING_PURCHASE_CONVERSION` → `results` = "Meta purchases" = 0 (la BRECHA de atribución CTWA:
  la compra real ocurre en WhatsApp, Meta no la trackea) → la conversación NO se expone en el MCP a
  ningún nivel (campaign/adset; `conversions` da "Not available"). Confirmado: "Dia de la madre" = 4
  adsets, todos `MESSAGING_PURCHASE_CONVERSION`.
- Conclusión de diseño: para campañas purchase-conversion perdemos solo el FUNNEL por-campaña (sin
  conv → sin drop-off); spend + clicks SÍ los tenemos. El BLEND a nivel cuenta (MER/CPA con ventas
  manuales = el valor real) queda intacto para TODAS. Las columnas "Contactos de mensajes" del UI son
  una mejora de granularidad por-campaña, NO crítica para el core. Recomendado: `results`-when-messaging;
  sumar 2ª fuente (export CSV) solo si se necesita el funnel fino de esas campañas.
- LECCIÓN para el plugin: el `result_type` de una campaña CTWA sigue al `optimization_goal` del adset,
  no al `objective` del campaign. Para clasificar la señal, mirar el result_type (string) — y recordar
  que purchase-conversion enmascara la conversación con "Meta purchases"=0 (brecha de atribución).
- Promovido a: pendiente OK del operador → luego constraint #3 de la memoria + L-# en `graphagents-dev`.

### 2026-06-19 · "Día del padre" (campaña ACTIVA) confirma el patrón + DÓNDE está la señal real
- "Día del padre 2026" (id `120243118818600317`, activa, ~$255K): 3 adsets, todos
  `MESSAGING_PURCHASE_CONVERSION`, todos `results: "0 (Meta purchases)"`, con gasto + clicks reales.
  Es la campaña que importa AHORA → el signal-loss NO es un edge raro, es el caso PRINCIPAL del
  operador. (Corrijo mi llamada anterior de "nice-to-have": SÍ es crítico.)
- DÓNDE vive la conversación: en el array `actions` del endpoint Graph `/insights`, como
  `onsite_conversion.messaging_conversation_started_7d` — que el motor YA parsea en `meta_insights.py`
  (`CTWA_ACTION_TYPE`). El MCP `ads_get_ad_entities` NO expone `actions` (solo `results` por-objetivo
  + actions:* curados) → por eso no da la conversación en purchase-conversion.
- Dos fuentes / dos parsers del motor: `meta_mcp.parse_ad_entities` (MCP entities, sin actions de
  mensajería) vs `meta_insights.parse_meta_insights` (Graph /insights con actions = la señal para
  TODAS las campañas). El parser de GraphAgents debe consumir la forma Graph+actions (la rica).
- Ban: leer `/insights` con actions con token OAuth consentido + `ads_read` (read-only) es el uso
  SANCIONADO del Marketing API, NO el patrón de riesgo (raw token / broker third-party / scraping).
  El MCP curado es el wrapper más nuevo, pero es un subconjunto.
- LECCIÓN para el plugin: cuando una fuente "oficial" (MCP curado) no expone un dato, verificar si el
  dato vive en la API base (Graph /insights actions) antes de declararlo inaccesible — y distinguir
  "uso sancionado con consentimiento" de "patrón de riesgo de ban" (no son lo mismo).
- Promovido a: decisión del operador sobre la fuente → constraint #3 + L-# plugin.

### 2026-06-20 · Premortem del feature ads-analytics (graph-cert-reviewer) — 5 fixes test-first + 1 deferral
- El panel salió VERDE pero el cert-reviewer (no-self-review) encontró issues REALES que el verde no captura. Resueltos test-first:
  - **MF-9** (RESUELTO): `actions` como JSON-string → `_conversations_from_actions` iteraba caracteres → conv=0 MUDO (¡la tesis del feature!). Fix: des-serializar string + raise si forma inesperada. Guard: `test_actions_como_json_string` + `test_actions_forma_inesperada_explota`.
  - **MF-2/MF-3** (RESUELTO): `numbers-qa` no reconciliaba el PERIODO (el verdict de cabecera) ni chequeaba bounds de cost/cpa. Fix: helper `_audit` sobre days + period, bounds de las 5. Guard: `test_falla_cuando_el_periodo_fue_editado`.
  - **MF-6** (RESUELTO): `mer==0` (revenue 0) → `rotate_creative`, indistinguible de "venta no cargada/atribuida" (purchase-conversion). Fix: `diagnose` devuelve `no_revenue_recorded`. Guard: `test_revenue_cero_no_es_rotate`.
  - **MF-5** (RESUELTO): `currency != COP` se dropeaba mudo. Fix: `blended-economics` propaga currency + raise si ≠ COP. Guard: `test_rechaza_currency_no_cop`.
  - **MF-7** (RESUELTO): seam central→agente reventaba con KeyError. Fix: extractors validan `payload` → ValueError de dominio. Guard: `test_falta_payload_da_error_de_dominio` (×2).
- **MF-1/MF-4 (RESUELTO 2026-06-20 — la orquestación es CORE, no G1+):** se implementó el threading del task graph — binding `inputs:` en los agentes-ref (`manifest_model`) + el `loader` threadea un estado acumulador (`build_runnable` composing branch) + el check **G-WIRE** (un supervisor que compone sin wiring NO certifica) + el CLI `run --input-file`. El supervisor corre por su manifest: `tests/integration/test_ads_analytics_supervisor.py` (reporte terminal + seed incompleto falla loud). El QA gobierna la confianza: `ctwa-report` marca `[ALERTA]` si `qa_passed=false`. → **L-10 RESUELTO** + regla G-WIRE en el plugin.
- **MF-8/10/11/12/14 (HARDENING, follow-up):** double-encode defensivo; timezone off-by-one Bogotá↔UTC (lo normaliza el central); fecha duplicada granular; `with: $state.*` letra-muerta (convención pre-existente, se consume con StateGraph G1+); fixture huérfano `meta_insights_sample.json`. No bloquean.
- Panel re-corrido tras los fixes: **VERDE**.
