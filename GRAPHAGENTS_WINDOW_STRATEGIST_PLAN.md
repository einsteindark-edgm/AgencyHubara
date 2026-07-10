# GraphAgents — "Window Strategist" (plan de implementación, REFINADO)

> **Documento de planeación. NO es código.** Refinado 2026-07-03 tras mapear el
> código vivo con los harnesses `graphagents-developer` y
> `hubara-plugin-developer` (paths y firmas verificados contra el worktree).
> El agente vive en `GraphAgents/` (rama/PR propia, TDD golden-replay, reglas
> G-*); la contraparte hubara vive en el monorepo (rama/PR propia, TDD DEHA,
> reglas P-*/L-*). Estrategia madre: `WHATSAPP_WINDOW_STRATEGY.md`.

## Propósito

Un agente **autónomo** que se activa por ciclo, clasifica cada lead por estado
de ventana (72h CTWA / 24h CSW / expirado) + warmth + gancho transaccional, y
**despacha reactivaciones a remarketing (hubara) cuando y como las ventanas lo
hacen rentable** — exprimiendo el carril gratis de 72h, usando utility barata
cuando hay motivo transaccional real, y **suprimiendo** los leads fríos fuera
de ventana.

**Sin HITL.** La seguridad del gasto no es un humano — es doble:
1. El agente solo despacha lo que su política (ventana × warmth × cadencia ×
   presupuesto) marca como rentable, con tope duro por ciclo.
2. hubara re-valida cada envío con `decide_reengagement` **en el propio
   RemarketingWorkflow** antes de mandar (gate nuevo — ver §"Estado real":
   ese wiring HOY NO EXISTE y es parte del alcance de este trabajo).

## Estado real del terreno (verificado 2026-07-03 — corrige al plan v1)

| Afirmación del plan v1 | Realidad |
|---|---|
| "hubara re-valida cada envío con `send_policy` al ejecutar remarketing" | **FALSO hoy.** `decide_reengagement` (`hubara_agency/src/platform/whatsapp/send_policy.py:266`) tiene **cero callers de producción**. `evaluate_send` solo ANOTA post-envío en el path template (`platform/whatsapp/activities.py:446`); el free-form del remarketing NO pasa por la central. **Cablear el gate es parte de esta HU** (workstream WS-B2). |
| "Reusa el protocolo de `sdk/callback.py`; hubara corre un consumidor de ese buzón" | **El push por callback nunca se construyó.** `to_event` (`GraphAgents/sdk/callback.py`) es un mapper huérfano de tests; no hay sink HTTP ni webhook receptor. El puente vivo y probado es **hubara-initiated, poll-based**: plugin `ads` → `runs/launcher.py` (port EC2+SSM) + `runs/orchestrator.py` (`launch_and_poll` → `apply_state`, emite `run.result` al ver `completed`) + `runs/record.py` (append idempotente por `event_id`). Consumir intents = leer `payload.output` del `run.result` del poll. |
| "idempotencia por `event_id` evita doble-toque" | `event_id` dedupea eventos **dentro de un run**; dos barridos = dos `run_id` → no protege cross-run. El guard real es el **fingerprint de envío**, que hoy existe SOLO para templates (`_template_fingerprint`, `platform/whatsapp/activities.py:305`); **free-form no tiene fingerprint** (trabajo nuevo, WS-B3). |
| "snapshot incluye cadencia previa" | No existe como campo consolidado; se deriva del log de outbounds por episodio (`_append_outbound_to_active_episode`) o se persiste nuevo. |
| Lo que el plan v1 SÍ tenía bien | `decide_reengagement`/`LeadState` existen con esa forma; snapshot-como-input-del-run calza con `launcher.dispatch(agent, input, run_id)`; `create agent` no existe en el CLI (manifest a mano); `archetype: analyzer` válido; `python3 -m`, nunca `uv run`. |

## Decisión de arquitectura #1 — el grafo es un analyzer PURO; G-DUR no tiene sujeto

Veredicto del harness GraphAgents: **el diseño pasa los gates tal cual**, sin
"reinterpretar" G-DUR. La regla (`references/01-graph-rules.md`) y su check
ejecutable T-DUR (`sdk/testkit/tool_checks.py:26-32`) disparan solo sobre tools
con `side_effect: outward`. Acá **no hay ninguna**:

- `parse-conversations` es `side_effect: pure` (recibe el snapshot en el input).
- El nodo `dispatch` **no ejecuta nada**: la lista de intents ES el output del
  `run()`. El runtime la entrega como `run.result.payload.output` — cero
  protocolo nuevo.
- La acción outward real (mandar WhatsApp) vive en hubara bajo el gate
  `decide_reengagement` — eso es DEHA-land, no G-rules.

**Molde correcto:** `graphs/ctwa_insights.py` + su golden (capability pura que
recibe payload y usa tool inyectada, sin HITL) para la mecánica, y
`graphs/ctwa_campaign_funnel.py` para la forma multi-nodo. `budget_approval.py`
solo sirve como referencia de qué NO copiar (`AwaitingHuman`, `@human_task`,
`interrupt`, kwarg `decision`).

## Decisión de arquitectura #2 — hubara pre-digiere el LeadState (mata el grueso de la paridad)

`decide_reengagement(now_ms, metadata, lead: LeadState, rate_card)` consume un
`LeadState` (frozen: `tag, has_order_draft, has_registered_order, is_ctwa_lead,
engaged, allow_paid_marketing` + property `transactional_hook`,
`send_policy.py:222`). **La derivación metadata→LeadState no existe todavía en
ningún lado.** Se escribe UNA vez, platform-side (`lead_state_from_metadata`,
helper puro junto a `send_policy.py`), y la usan:

1. la activity que arma el snapshot para el agente (WS-B4), y
2. el gate `check_reengagement_policy_activity` del remarketing (WS-B2).

Consecuencia: el snapshot lleva los campos del LeadState **ya digeridos** + los
expiries de ventana crudos. El "espejo" en `parse-conversations` queda reducido
a: comparar `now_ms` contra `service_window_expires_at_ms` /
`ctwa_window_expires_at_ms` + aplicar la matriz de precedencia — no re-derivar
warmth/ganchos desde metadata. El drift posible se encoge a la matriz, que se
guarda con un golden compartido (ver §Paridad).

## Decisión de arquitectura #3 — dónde vive cada pieza en hubara

- **Gate de re-validación → platform, dentro del `RemarketingSessionWorkflow`.**
  Nueva activity `check_reengagement_policy_activity(session_id, ...) → SendDecision`
  (junto a `check_remarketing_eligibility`, `platform/temporal/activities.py:100`):
  lee metadata, deriva LeadState, llama `decide_reengagement`, y el workflow
  aborta / elige canal-categoría según la decisión. Así el guardrail cubre a
  TODOS los dispatchers (agente, dashboard-handoff, transition INTERESADO) sin
  tocar P-28. ⚠️ **L-9**: hay runs de remarketing dormidos durante días →
  `workflow.patched()` obligatorio.
  Quiet hours: la autoridad queda hubara-side en este gate, reusando los
  helpers del watchdog (`_resolve_local_timezone` / `_is_quiet_hours_for_session`,
  `agent/remarketing/activities/watchdog_activities.py:73,100`) — el nodo `plan`
  del agente solo prioriza/ordena; no duplica lógica de timezone.
- **Ciclo strategist (snapshot + dispatch a GraphAgents + consumo de intents)
  → plugin NUEVO `reengagement`** (no engordar `chats`, no colgar de `ads`):
  `archetype:` en el manifest (P-29), TCK en `tests/conformance/` (P-27),
  `ensure_plugin_enabled` primero (P-21), task queue propia (P-16), compose vía
  `render-compose.py` (P-20). No aplica `owns_route` (no toma turnos
  conversacionales); si expone rutas, ownea `/api/reengagement/`.
- **El intent llega a remarketing por transition declarativa** en el manifest
  del plugin nuevo (precedente exacto: orders→eta): `target_worker: remarketing`,
  `input_mapping {session_id, motivo}` → `RemarketingWorkflow`
  (`RemarketingSessionInput(session_id, motivo)`, `contracts.py:12`). Cero
  imports cross-plugin. **Usar `via: start_workflow` (NO
  `start_workflow_with_replace`)**: replace TERMINA un remarketing en curso —
  un intent del agente no debe matar una conversación viva.
- **Prerequisito compartido:** el bridge (`Launcher` port + `record` +
  `conductor.interpret` + `launch_and_poll`) es hoy **privado del plugin `ads`**
  — el plugin nuevo no puede importarlo (P-3). Opciones:
  - (a) **Promoverlo al SDK** (kit, con la regla de oro: símbolo + consumidor +
    check TestKit) y que `ads` pase a importar `src.sdk`. Más caro, correcto.
  - (b) Duplicación mínima en el plugin nuevo, declarada como deuda con
    ticket de consolidación. Aceptable para el MVP si (a) no cabe en la HU.
  Decidir en el refinamiento técnico de la HU hubara (default recomendado: (a)).
- **Snapshot NO va por HTTP** (la API prod está sin auth — sería un leak). El
  seam es activity-side: `build_conversations_snapshot` escanea el vault
  (precedente: `ads` lee vault vía platform; `_sample_vault_state` en
  `chats/api/dashboard.py:264` como referencia de scan) y el bridge lo pasa
  como **input del run** (`launcher.dispatch(agent, input, run_id=...)`).

## El grafo (StateGraph determinista)

```
ingest → classify → plan → dispatch → END      (cadena LINEAL — sin conditional edges, L-15)
```

- **`ingest`** — valida/normaliza el snapshot (vía `tools["parse-conversations"]`,
  inyectada por el mapping `tools` — nunca import directo, L-24).
- **`classify`** — por conversación: fase de ventana (`ctwa_free` / `csw_free` /
  `expired`) comparando `now_ms` del payload contra los expiries + LeadState
  pre-digerido. Espejo MÍNIMO de la matriz de `decide_reengagement`
  (precedencia real verificada, `send_policy.py:288-322`): HUMANO/COMPRA_EXITOSA
  → suppress · CSW abierta → free_form · CTWA abierta → template gratis
  (utility si hook, sino marketing) · Fase B → utility si hook / marketing si
  opt-in / suppress.
- **`plan`** — guardrail autónomo (el reemplazo del approval humano):
  - cadencia por lead (máx X toques/ventana, sobre `recent_touches` del snapshot),
  - presupuesto por ciclo (tope duro de toques PAGOS; los `ctwa_free`/`csw_free`
    no lo consumen),
  - orden por valor esperado (gancho transaccional y 72h-gratis primero),
  - **lo truncado por presupuesto se reporta en el output** (`truncated`), no
    se pierde en silencio.
  Fase A (72h/24h) → dispatch gratis, agresivo. Fase B → solo utility con
  gancho o alto-valor con opt-in; el resto suprime con razón.
- **`dispatch`** — arma el output final (la lista de intents). **No ejecuta nada.**

Firma obligatoria (G-RUN-SIG, L-2): `run(input, *, ports=None, tools=None)` +
`build(*, checkpointer=None)`, lógica compartida una sola vez (L-11). State del
`build()` anotada solo con builtins (L-13); sin reducers custom (L-14). Si más
adelante entra un nodo LLM (redacción/priorización fina), es best-effort con
try/except — la lista determinista SIEMPRE se emite (L-26).

## Contratos

**Snapshot (input del run — hubara lo arma, `now` viaja en el payload, G-DET/L-1):**
```json
{
  "schema_version": 1,
  "now_ms": 0,
  "conversations": [{
    "session_id": "wa_...",
    "service_window_expires_at_ms": 0,
    "ctwa_window_expires_at_ms": 0,
    "last_inbound_at_ms": 0,
    "lead": {
      "tag": "INTERESADO",
      "has_order_draft": true,
      "has_registered_order": false,
      "is_ctwa_lead": true,
      "engaged": true,
      "allow_paid_marketing": false
    },
    "recent_touches": [{"at_ms": 0, "kind": "remarketing|watchdog|sales"}],
    "tz": "America/Bogota"
  }]
}
```

**Output del run (= `run.result.payload.output` que el poller de hubara lee):**
```json
{
  "schema_version": 1,
  "snapshot_now_ms": 0,
  "dispatch": [{
    "kind": "reengagement_dispatch",
    "session_id": "wa_...",
    "recommended_channel": "free_form|template",
    "recommended_category": "utility|marketing|service",
    "reason": "ctwa_72h_free | csw_free_form | transactional_hook | phase_b_high_value",
    "priority": 1
  }],
  "suppressed": [{"session_id": "wa_...", "reason": "fase_b_cold | cadence_cap | quiet_hours_defer"}],
  "truncated_by_budget": 0
}
```
El intent NO lleva el texto del mensaje: el contenido lo genera el remarketing
workflow como hoy; `recommended_*` son hints que el gate hubara-side puede
overridear (la autoridad es `decide_reengagement` al ejecutar).

En hubara, el evento intent = `@dataclass(frozen=True)` en
`plugins/reengagement/shared/contracts/events.py` (R-JSON), declarado en
`emits:`; **sin** `from __future__ import annotations` (imitar el gotcha
documentado en `send_policy.py:25-28` — cruza boundary Temporal).

## Trigger y cadencia

Barrido por ciclo con **Temporal Schedule** en el plugin `reengagement`
(workflow cíclico: `build_snapshot` → `dispatch_run` + poll → emitir intents).
⚠️ Costo oculto real: la caja GraphAgents es EC2 pay-per-use con autostop —
cada ciclo paga `start_box()` (cold start 1-3 min). **Dimensionar el ciclo en
decenas de minutos (30-60 min), no "cada N minutos"**; la granularidad fina la
dan las ventanas (72h/24h), no el poll. Event-driven (inbound nuevo, ventana
por expirar) queda como fase 2.

Idempotencia end-to-end (tres capas, de afuera hacia adentro):
1. `event_id` del record del bridge (dedupe intra-run; esquema real del buzón:
   `{run_id}:{idx}:{status}`, `orchestrator.py:48`).
2. Workflow id determinista `remarketing-{session_id}` + `start_workflow`
   idempotente (si ya corre, no pisa).
3. **Fingerprint de envío en hubara** (el guard cross-run verdadero): ya existe
   para templates; **agregar el equivalente free-form** (WS-B3).

## Paridad de clasificación (el riesgo #1, ahora acotado)

- La derivación warmth/ganchos NO se duplica (Decisión #2): vive una sola vez
  en `lead_state_from_metadata`.
- La matriz de precedencia sí vive en dos lados. Guard: **extraer la matriz a
  un JSON golden compartido** (`{metadata, lead, now_ms} → {allowed, channel,
  category, is_free, suppress_reason}`), consumido por:
  (a) test parametrizado en `hubara_agency/tests/platform/` contra
  `decide_reengagement` real (los casos ya existen inline en
  `test_send_policy_reengagement.py` — extraerlos), y
  (b) el golden de `tools/parse-conversations` en GraphAgents (copia del
  archivo + guard de checksum, porque la frontera del monorepo impide imports).
- La autoridad del costo es SIEMPRE hubara al ejecutar (gate del workflow); el
  espejo solo prioriza.

## Piezas y orden TDD

### Rama GraphAgents (WS-A) — harness `graphagents-developer`

1. **WS-A1** `python3 -m sdk.cli create tool parse-conversations --side-effect pure`
   — el scaffold nace con test ROJO. Golden alimentado por la matriz JSON
   compartida. `certify-tool parse-conversations` (T-IMPL + G-AGNOSTIC).
2. **WS-A2** `tests/graphs/test_window_strategist_golden.py` — el rojo: fixture
   de snapshot → EXACTAMENTE esta lista de dispatch/suppressed/truncated.
   Molde: `test_ctwa_insights_golden.py` (payload + tool inyectada, sin HITL).
3. **WS-A3** `graphs/window_strategist.py` — `run`/`build`, nodos
   `ingest/classify/plan/dispatch`, lógica compartida una vez.
4. **WS-A4** `manifests/window-strategist.agent.yaml` a mano — `archetype:
   analyzer`, `capability: graphs.window_strategist:build`,
   `tools: [{uses: parse-conversations@1, with: {payload: $state.payload}}]`
   (G-BIND, L-12), `contract:{inputs: payload, outputs: dispatch}`. Verificar
   el CABLE completo (L-25: manifest + run() invoca la tool + golden E2E).
5. **WS-A5** `python3 -m sdk.cli certify window-strategist` → C2.

### Rama hubara (WS-B) — harness `hubara-plugin-developer`, monorepo

0. **WS-B0 (decisión previa)** Bridge al SDK: promover `plugins/ads/runs/`
   (launcher port + record + conductor + poll loop) a kit del SDK (opción a,
   recomendada) o duplicación mínima declarada (opción b). Si (a): regla de
   oro — símbolo + consumidor + check TestKit; `ads` migra a importar `src.sdk`.
1. **WS-B1** `lead_state_from_metadata` — helper puro junto a `send_policy.py`,
   rojo en `tests/platform/`. + Extracción de la matriz golden JSON compartida.
2. **WS-B2** Gate: `check_reengagement_policy_activity` + wiring en
   `RemarketingSessionWorkflow` (tras `check_remarketing_eligibility`; aborta
   o fija canal/categoría según `SendDecision`; quiet hours reusando helpers
   del watchdog). **`workflow.patched()`** (L-9). Rojo con WorkflowEnvironment
   (⚠️ si cuelga local: `pkill -9 -f temporal-test-server`).
   Con esto la afirmación "hubara re-valida cada envío" pasa a ser VERDAD para
   todos los dispatchers — cierra el ⏳ del §9-bis de la estrategia.
3. **WS-B3** Fingerprint free-form (anti doble-toque cross-run), simétrico al
   `_template_fingerprint` existente, persistido en la misma escritura del
   outbound.
4. **WS-B4** Plugin `reengagement`: TCK conformance + activity
   `build_conversations_snapshot` (vault scan, platform-side) + workflow
   cíclico (Temporal Schedule) que arma snapshot → `dispatch("window-strategist",
   snapshot, run_id)` → poll → por cada intent del `run.result`, emite evento
   frozen via `src.sdk.eventkit` → transition declarativa en el manifest hacia
   `RemarketingWorkflow` (`via: start_workflow`, `input_mapping {session_id,
   motivo: reason}`). Molde de tests: `tests/plugins/ads/test_analysis_*` con
   `FakeLauncher`.
5. **WS-B5** Gates: `/hubara-gates backend` + `lint-imports` + `pytest -m
   architecture` (⚠️ env dummies Medusa si falla local) + `render-compose`.

**Dependencias:** WS-B1 alimenta a WS-A1 (la matriz golden) y a WS-B2. WS-A y
WS-B2/B3 pueden avanzar en paralelo; WS-B4 necesita WS-B0 + WS-A4 (nombre del
agente + contrato) para el E2E. Ninguna pieza toca paths PROTECTED; si el DTO
del intent tienta ir a `platform/contracts.py`, resistir — vive en el plugin.

## Reglas duras que aplican

**GraphAgents:** G-DET (esqueleto puro, `now` por payload) · G-PORT (data por
tool-que-recibe-payload; jamás red a hubara/WhatsApp desde el grafo) · G-DUR
(satisfecha por construcción: cero tools outward; el output es data) ·
G-RUN-SIG/G-BIND/G-CONTRACT · L-2, L-11, L-13, L-14, L-15, L-24, L-25, L-26 ·
`python3 -m`, nunca `uv run`.

**hubara:** P-3 (no imports cross-plugin — bridge via SDK o duplicado
declarado) · P-16/P-20/P-21/P-27/P-29 (plugin nuevo completo) · P-28 (plugins
no importan `src.platform` — el gate es activity platform, no código de
plugin; fachada SDK de send_policy SOLO si un plugin la llama directo) · R-JSON
(intent frozen, sin `from __future__ import annotations`) · L-9
(`workflow.patched()` en remarketing).

**Frontera:** GraphAgents NO importa `hubara_agency.*`; el puente es
poll-based hubara-initiated (launcher/SSM) + golden JSON copiado con checksum.

## Riesgos

- **Runaway dispatch** — sin HITL, el freno es: tope duro por ciclo en `plan`
  (testeado, con `truncated_by_budget` visible) + el gate `decide_reengagement`
  por envío + cadencia por lead. Tres capas independientes.
- **Doble-toque** — fingerprint free-form (WS-B3) es la capa que HOY falta;
  sin ella, `event_id` + workflow-id no cubren dos barridos distintos.
- **Drift de la matriz** — golden JSON compartido con checksum en ambos repos;
  el día que `decide_reengagement` cambie, el checksum roto obliga a
  re-sincronizar el espejo.
- **Interrumpir conversaciones vivas** — mitigado con `start_workflow` (no
  replace) + el gate aborta si `active_route == humano` o CSW conversacional
  activa con sales en turno.
- **Costo del ciclo** — cold start EC2 por barrido; cadencia 30-60 min y
  medir. Si el costo de caja supera el ahorro de ventanas, considerar correr
  la capability en el API host (es pura — no necesita GPU ni Conductor para
  el MVP; decisión para después de medir).
- **Runs dormidos de remarketing** (L-9) — el edit del workflow sin
  `workflow.patched()` rompe replays al deploy.
