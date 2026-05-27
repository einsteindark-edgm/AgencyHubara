# agents/remarketing-worker — capability spec

> Behavior contract del worker `remarketing` del plugin `chats`.
> Bootstrap incremental — esta versión cubre lo que existe + lo que
> agrega HU-WA24H-001. Los pendientes están listados como TBD.

## Purpose

El worker `remarketing` hospeda los workflows que **re-engagan a clientes que
quedaron inactivos** después de una conversación con sales. Tres workflows
principales hoy/post-HU-WA24H-001:

1. **`RemarketingWorkflow`** (existente) — sesión conversacional de
   remarketing cuando un cliente quedó como INTERESADO y se programó un
   re-engagement con delay. Reusa el motor LLM con un prompt distinto al
   de sales (tono más suave, foco en re-enganchar la conversación).
2. **`ServiceWindowWatchdogWorkflow`** (HU-WA24H-001 Sprint 2) — workflow
   per-episodio que duerme hasta 30min antes de cierre de ventana 24h y
   dispara un utility template legítimo si hay deal abierto.
3. **`RemarketingCadenceWorkflow`** (HU-WA24H-001 Sprint 4, futuro) —
   secuencia de 5 attempts en 21 días post-watchdog si el cliente no
   respondió, con escalation utility → marketing.

Los tres se inician declarativamente vía dispatcher manifest (ADR-2026-05-20)
— el worker NO importa workflow classes de sibling agents (R-DIP).

## Requirements

### Requirement: RemarketingWorkflow respeta route ownership

El sistema MUST verificar que `metadata.active_route` permita remarketing
ANTES de mandar cualquier mensaje al cliente. Si entre el momento del
programa (sales emitió `SalesSessionCompletionEvent(tag=INTERESADO,
delay_seconds=N)`) y el momento del fire (N segundos después) el route
cambió a `humano`, el workflow MUST abortar sin side-effects.

#### Scenario: Escalation a humano entre schedule y fire

- GIVEN sales emitió evento INTERESADO para session `wa_+57300...` con `delay=60`
- AND a t+30s el operador humano tomó el caso (`active_route=humano`)
- WHEN el remarketing workflow arranca a t+60s
- THEN `check_remarketing_eligibility` retorna `eligible=False, blocked_reason="active_route=humano"`
- AND el workflow returnea SIN llamar `claim_conversation_routing`, SIN enviar mensaje, SIN contaminar history
- AND el workflow completa cleanly liberando el workflow_id

### Requirement: Handoff a Sales cuando cliente responde

Cuando un cliente responde durante el remarketing, el sistema MUST transferir
el control de vuelta a Sales SIN duplicar mensajes ni perder contexto.

#### Scenario: Cliente responde durante remarketing

- GIVEN `RemarketingWorkflow` corriendo para session, motivo "duda de precio"
- WHEN el cliente envía un mensaje inbound
- THEN el dispatcher rutea el inbound al RemarketingWorkflow vía signal
- AND el LLM procesa con prompt de remarketing
- AND si la tool `transfer_to_sales` decide transferir → workflow emite `CustomerRepliedDuringRemarketingEvent`
- AND el dispatcher arranca/asegura `HubaraSalesSessionWorkflow` con `via=ensure_running`
- AND el `pending_handoff_summary` se persiste en metadata para que sales lo lea
- AND el remarketing workflow termina (force_shutdown=True)

#### Scenario: Cliente responde pero LLM no transfiere

Salvavidas determinista que evita que el remarketing siga conversando
indefinidamente cuando el LLM olvida usar la tool de transferir.

- GIVEN `RemarketingWorkflow` con `messages_processed > 1`
- AND el LLM responde pero NO invoca `transfer_to_sales`
- WHEN el workflow termina el turno
- THEN se fuerza una `TransferDecision` sintética con summary "Usuario respondió: ..."
- AND se ejecuta el mismo handoff path del scenario anterior

### Requirement: ServiceWindowWatchdog programa solo episodios activos

El sistema SHALL programar un `ServiceWindowWatchdogWorkflow` solo cuando:
- Hay un episodio activo (`closed_at_ms is None`) en metadata.
- `active_route ∈ {ventas, remarketing}` (NO `humano`).
- `WATCHDOG_ENABLED=true` en environment.

#### Scenario: Inbound de cliente con deal abierto programa watchdog

- GIVEN cliente con episodio activo, stage `awaiting_quote`, `active_route=ventas`
- WHEN llega un inbound
- THEN `IngestInboundMessage` persiste `service_window_expires_at_ms = now + 24h`
- AND emite `ServiceWindowOpenedEvent(session_id, episode_id, fire_at_ms = expiry - 30min)`
- AND el dispatcher arranca `ServiceWindowWatchdogWorkflow` con workflow_id `watchdog-<session>-<episode>` y `start_delay = (fire_at - now)`

#### Scenario: Cliente responde antes del fire — cancel

- GIVEN watchdog programado para t+23.5h
- WHEN cliente envía inbound a t+5h
- THEN `IngestInboundMessage` emite `CustomerRepliedEvent(session_id, episode_id)`
- AND dispatcher rutea via `signal` al `ServiceWindowWatchdogWorkflow.cancel_watchdog("customer_replied")`
- AND el watchdog NO dispara el template
- AND nuevo watchdog se programa con `service_window_expires_at_ms` actualizado

#### Scenario: Episodio cierra antes del fire — cancel

- GIVEN watchdog programado, episodio `awaiting_payment`
- WHEN cliente paga + agente cierra episodio con `COMPRA_EXITOSA`
- THEN `close_episode` emite `EpisodeClosedEvent(session_id, episode_id, closing_tag="COMPRA_EXITOSA")`
- AND dispatcher signala `cancel_watchdog("episode_closed")`
- AND watchdog NO dispara nudge sobre un episodio ya cerrado

### Requirement: Watchdog send respeta categoría utility

El sistema MUST garantizar que el watchdog SOLO use templates `category=utility`.
Marketing templates SHALL NEVER ser disparados automáticamente por el
watchdog — el costo financiero ($0.0125 USD vs $0.0008 utility) hace que
sea decisión consciente del LLM o de la cadencia.

#### Scenario: Stage del episodio no tiene utility template

- GIVEN episodio en stage `unknown_custom_stage`
- WHEN watchdog evalúa eligibility
- THEN `get_watchdog_template_for_stage` retorna None (no hay match utility)
- AND `check_watchdog_eligibility_activity` retorna `eligible=False, reason="no_utility_template_for_stage"`
- AND watchdog persiste outcome `skipped` SIN enviar nada

### Requirement: OutboundLogEntry persiste antes del activity return

El sistema MUST persistir el `OutboundLogEntry` (con `pricing=None,
cost_cents_usd=None`) en `metadata.episodes[active].outbound_messages[]`
DENTRO del activity de send, antes de retornar. Esto garantiza
atomicidad: cuando llegue el webhook delivery status, el log entry
existe para ser materializado.

#### Scenario: Webhook llega antes del activity return

(Edge case raro pero documentado — Meta puede emitir `sent` casi
instantáneamente.)

- GIVEN `send_whatsapp_template_activity` invoca `whatsapp_client.send_template`
- AND el webhook `message_status` con `pricing` llega ANTES de que el activity haya escrito metadata
- WHEN `IngestDeliveryStatus` busca el OutboundLogEntry → no existe
- THEN retry con backoff exponencial (max 3 attempts en ~5s)
- AND si después de retry sigue sin existir → dead-letter a `_orphan_delivery_statuses.jsonl`

### Requirement: Templates marketing requieren confirmación del LLM (TBD)

> **Sprint 4 (cadencia futura).** El tool `send_template_message` del LLM
> debe validar que category=marketing trae `confirm_marketing_send=True`
> en el input para prevenir sends accidentales caros.

## Out of scope

- **Cadence policy específica** (`default_b2c_v1`, `aggressive_low_ticket_v1`, `luxury_long_v1`) — vive en `src/platform/whatsapp/templates/cadences.yaml` (Sprint 4).
- **Frequency capping local** per-user — confiamos en cap global de Meta (#131049) hasta que sea problema.
- **Pause global por quality rating** — orchestración cross-workflow es Sprint 4.
- **Detalle de prompts del LLM remarketing** — vive en `src/plugins/chats/agent/remarketing/workspace/{IDENTITY,SOUL,USER}.md`.

## Dependencies

- **`platform/whatsapp/window`** — helpers de service window 24h y CTWA 72h.
- **`platform/whatsapp/templates`** — registry + catalog YAML.
- **`platform/whatsapp/cost`** — DTOs + helpers de cost computation.
- **`platform/whatsapp/composition`** — factories cached (`get_template_registry`, `get_current_rate_card`).
- **`platform/whatsapp/activities`** — `send_whatsapp_template_activity`.
- **`platform/orchestration/dispatcher`** — `dispatch_event_activity` + `envelope_for`.
- **`plugins/chats/shared/contracts/events`** — `ServiceWindowOpenedEvent`, `CustomerRepliedEvent`, `EpisodeClosedEvent`, `CustomerRepliedDuringRemarketingEvent`, `SalesSessionCompletionEvent`.
- **`plugins/chats/agent/remarketing/contracts`** — `RemarketingSessionInput`, `WatchdogInput`.
- **`plugins/chats/workers/remarketing.py`** — el worker que registra los workflows + activities.

## Specs de capabilities relacionadas

- **`messaging`** — contratos cross-plugin de WhatsApp (inbound/outbound, EventLog).
- **`plugins/chats`** — comportamiento del plugin chats end-to-end.
- **`agents/sales-worker`** — sibling worker, comparte primitives (`PendingMessage`, `coalesce_pending`, `run_agent_turn`).
