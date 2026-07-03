# GraphAgents — "Window Strategist" (plan de implementación)

> **Documento de planeación. NO es código.** El desarrollo del agente vive en el
> subsistema aparte `GraphAgents/` y se implementa en su propia rama/PR con el
> harness `graphagents-developer` (TDD golden-replay, reglas G-*). Este doc es
> la especificación para arrancarlo. Estrategia madre: `WHATSAPP_WINDOW_STRATEGY.md`.

## Propósito

Un agente **autónomo** que se activa por conversación, clasifica cada lead por
estado de ventana (72h CTWA / 24h CSW / expirado) + warmth + gancho
transaccional, y **despacha reactivaciones a remarketing (hubara) cuando y como
las ventanas lo hacen rentable** — exprimiendo el carril gratis de 72h,
usando utility barata cuando hay motivo transaccional real, y **suprimiendo**
los leads fríos fuera de ventana (no pagar por lo que no se calentó).

**Sin HITL.** No hay gate humano. El agente decide y manda a ejecutar solo. La
seguridad del gasto NO es un humano — es doble:
1. El agente solo despacha lo que su política (ventana × warmth × cadencia ×
   presupuesto) marca como rentable.
2. **hubara re-valida cada envío con la central `send_policy`** al ejecutar el
   remarketing (defensa en profundidad — INV: ningún gasto no autorizado por la
   central, aunque el agente se equivoque).

Esto reconcilia **G-DUR** (acción outward = controlada): el agente nunca pega a
WhatsApp ni gasta directo; **emite intents de dispatch** que hubara ejecuta bajo
su propio guardrail. El "approval" deja de ser humano y pasa a ser
**programático** (la central + el guardrail de presupuesto/cadencia del nodo
`plan`).

## Trigger — "se activa con cada conversación"

Barrido periódico (p.ej. cada N minutos) que, por ciclo, evalúa el conjunto de
conversaciones activas. Por cada conversación:
- hubara expone un **snapshot** (ventanas, tag, order_draft, registered_order,
  ctwa_clid, last_inbound_at_ms, cadencia previa) que el agente consume por una
  **tool pura** (`parse-conversations`) — el agente NO pega red a hubara desde
  el esqueleto (G-PORT); hubara deposita el JSON en el seam.
- `now` entra en el payload (parte del seed), **nunca** `datetime.now()` en el
  grafo (G-DET — si no, el golden flakea, L-1).

Alternativa event-driven (fase 2): hubara emite eventos "conversación cambió"
(nuevo inbound, borde de episodio, ventana por expirar) → el agente corre por
conversación. El barrido es el MVP; el event-driven es la optimización.

## El grafo (StateGraph determinista, molde `graphs/budget_approval.py` SIN el HITL)

```
ingest → classify → plan → dispatch → END
```

- **`ingest`** — recibe el snapshot de conversaciones (vía `tools["parse-conversations"]`).
- **`classify`** — por conversación: estado de ventana (dentro 72h / dentro 24h /
  expirado), warmth (respondió?, tag), gancho transaccional (order_draft /
  registered_order / CONFIRMADO_PAGO_PENDIENTE). **Espeja la lógica de
  `send_policy.decide_reengagement`** (la frontera del monorepo impide importarla;
  se re-implementa en la tool pura `parse-conversations` y se mantiene en paridad
  con guardas). La AUTORIDAD del costo sigue siendo hubara al ejecutar.
- **`plan`** — construye la lista de dispatch respetando el **guardrail
  autónomo** (el reemplazo del approval humano):
  - cadencia por lead (no más de X toques / ventana),
  - quiet hours (hora local del cliente),
  - presupuesto por ciclo (tope de toques pagos),
  - orden por valor esperado (los de gancho transaccional y los de 72h-gratis
    primero).
  Fase A (72h/24h) → dispatch gratis, agresivo. Fase B → solo gancho
  transaccional (utility) o alto-valor con opt-in; el resto **suprime**.
- **`dispatch`** — emite un intent por conversación de vuelta a hubara. **No
  ejecuta el envío.**

## El puente GraphAgents → hubara (sin approval, fire-and-execute)

- Reusa el protocolo de eventos de `sdk/callback.py` (idempotencia por
  `event_id = <run_id>:<seq>`), pero el evento terminal es un **`run.result`**
  que lleva la **lista de dispatch** (no un `run.awaiting_approval`).
- **hubara** corre un consumidor de ese buzón que, por cada intent, dispara el
  **remarketing workflow** de la conversación. Ese workflow **re-consulta
  `send_policy`/`decide_reengagement`** antes de mandar → el guardrail final.
- Idempotencia end-to-end: el `event_id` del intent + el fingerprint del envío
  en hubara evitan doble-toque ante reintentos.

Contrato del intent (borrador):
```json
{
  "kind": "reengagement_dispatch",
  "session_id": "wa_...",
  "recommended_channel": "template|free_form",
  "recommended_category": "utility|marketing|service",
  "reason": "ctwa_72h_free | transactional_hook | phase_b_high_value",
  "now_ms": 0
}
```

## Piezas a crear en `GraphAgents/` (orden TDD)

1. **`tools/parse-conversations/`** (`sdk.cli create tool`) — pura, recibe el
   snapshot, clasifica ventana/warmth/gancho. Golden en `tests/tools/`.
2. **`tests/graphs/test_window_strategist_golden.py`** — el rojo: dado un
   fixture de conversaciones, el grafo produce EXACTAMENTE esta lista de
   dispatch. (Molde: `tests/graphs/test_budget_approval_golden.py` **sin** el
   `interrupt`/`await_approval`.)
3. **`graphs/window_strategist.py`** — `run(input, *, ports, tools)` +
   `build(*, checkpointer=None)`; nodos `ingest/classify/plan/dispatch`; lógica
   compartida run≡build una sola vez (G-DET, L-11).
4. **`manifests/window-strategist.agent.yaml`** — `archetype: analyzer`,
   `capability: graphs.window_strategist:build`, `tools: [{uses: parse-conversations@1}]`,
   `contract:{inputs,outputs}`. (⚠️ `create agent` no está en el CLI — manifest a
   mano.)
5. **hubara (rama separada, monorepo):** consumidor del buzón de dispatch +
   trigger del remarketing workflow por intent. Re-usa `decide_reengagement`
   (YA existe, `platform/whatsapp/send_policy.py`) como guardrail.
6. **Certificar C2** (`sdk.cli certify window-strategist`). Correr con
   `python3 -m pytest` / `python3 -m sdk.cli`, **NO `uv run`**.

## Reglas duras que aplican

- **G-DET** — esqueleto puro; `now` por payload. Sin `datetime.now()`.
- **G-PORT** — la data de conversaciones entra por tool-que-recibe-payload;
  nunca red cruda a hubara/WhatsApp desde el grafo.
- **G-DUR (reinterpretado)** — el gasto real lo ejecuta hubara bajo `send_policy`;
  el agente solo emite intents dentro del guardrail de presupuesto/cadencia del
  nodo `plan`. Sin HITL, pero con tope duro por ciclo (no runaway-dispatch).
- **Frontera del monorepo** — el agente NO importa `hubara_agency.*`; el puente
  es el protocolo de eventos, no un import.

## Riesgos

- **Paridad de clasificación** — la lógica de ventana vive en DOS lados
  (`send_policy` en hubara = autoridad; `parse-conversations` en el agente =
  espejo para planear). Guard: la autoridad del costo es siempre hubara al
  ejecutar; el espejo solo prioriza. Documentar el drift como riesgo y testear
  ambos contra los mismos fixtures.
- **Runaway dispatch** — sin HITL, el tope de presupuesto/cadencia del nodo
  `plan` es el único freno. Debe ser un límite duro, testeado, con `log` de lo
  truncado (no silencioso).
- **Doble-toque** — idempotencia por `event_id` + fingerprint en hubara.
