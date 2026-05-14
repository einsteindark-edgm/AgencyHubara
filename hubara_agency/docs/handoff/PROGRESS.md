# Progreso — Human Handoff

Actualizado a medida que avanza la implementación.

## Backend

- [x] B1. Extraer `send_message_to_session(session_id, text)` reusable en `platform/whatsapp/activities.py`.
- [x] B2. Agregar `append_human_event(session_id, content)` en `platform/session_history/store.py`.
- [x] B3. Extraer `start_remarketing_for_session(client, session_id, motivo, delay_seconds)` + `terminate_session_workflows(client, session_id)` en `platform/temporal/dispatcher.py`.
- [x] B4. Crear `src/dashboard/composition.py` (DI factory).
- [x] B5. Crear `src/dashboard/handoff.py` con 3 endpoints.
- [x] B6. Extender clasificador en `src/dashboard/api.py` para `human_message`.
- [x] B7. Registrar router en `src/main.py`.
- [x] B8. Tests: `tests/test_handoff_endpoints.py` (10 tests).
- [x] B9. Tests: extensión a `tests/test_filesystem_history_store.py` (3 tests nuevos).
- [x] B10. Tests: `tests/test_dashboard_handoff_classifier.py` (3 tests).
- [x] B11. Suite completa verde: **232 passed, 1 skipped**.

## Frontend

- [x] F1. Extender enum `messageUiTypeSchema` con `human_message`.
- [x] F2. Extender `ChatMessage` model + filters (`isVisibleChatMessage`, `getMessageSender`).
- [x] F3. Adapter `entities/chat/api.ts`: pasar bandera `author: "human"|"bot"` al `ChatMessageItem`.
- [x] F4. Extender `ChatMessageItem` model con campo `author`.
- [x] F5. Crear `entities/handoff/api.ts` con 3 mutaciones + `contracts.ts` + `index.ts`.
- [x] F6. Wire `ChatsComposer` al `active_agent_route` real + mutaciones (via `useSession`).
- [x] F7. Modal de selección Sales/Remarketing al devolver al bot (con motivo obligatorio para Remarketing).
- [x] F8. `ChatsBubble` con render distinto para humano (badge "Humano" + tinte naranja).
- [x] F9. Tests: `entities/handoff/api.test.tsx` (7 tests).
- [x] F10. Tests: `features/chats-conversation/ui/ChatsComposer.test.tsx` (7 tests).
- [x] F11. `pnpm test` verde: **58 passed, 1 skipped**.

## Verificación end-to-end

Pendiente smoke manual en docker — flujo a probar:

- [ ] V1. Escalación + take-over manual desde dashboard. *(El bot escala → operador entra al dashboard → composer muestra "Intervenir" + después de click muestra textarea.)*
- [ ] V2. Cliente sigue escribiendo, bot no responde. *(El JSONL crece pero `LoadOrStartSalesSession` cortó por `active_route=humano`.)*
- [ ] V3. Devolver al bot (Sales) → siguiente mensaje del cliente arranca Sales. *(metadata.json[active_route] = "ventas"; siguiente webhook arranca workflow.)*
- [ ] V4. Devolver al bot (Remarketing) → workflow arranca y manda gancho. *(`start_remarketing_for_session` se llama inmediatamente con `delay_seconds=0`.)*
- [ ] V5. Historial visible en dashboard con mensajes humanos diferenciados. *(Badge naranja "Humano" en burbujas escritas desde el composer.)*

Reiniciar `local-hubara-api` para tomar los endpoints nuevos:
```
docker compose -f docker-compose.local.yml restart hubara-api hubara-frontend
```

## Pre-mortem (auditoría antes de prueba real)

Issues encontradas y resueltas antes de smoke en producción:

- [x] **PM1.** `terminate_session_workflows` capturaba solo `RPCError`. Si `describe()` o `terminate()` lanzaban OTRO error (race entre describe y terminate, timeout), el endpoint 500-aba. Fix: catch genérico por workflow individual + log + sigue con el siguiente. Tests `test_terminate_continues_after_individual_failure` y `test_terminate_handles_describe_failure_gracefully`.
- [x] **PM2.** `intervene` 500-aba si Temporal estaba inaccesible. La metadata YA estaba persistida (paso 1) pero el operador no veía OK. Fix: termination es best-effort dentro de try/except; el endpoint reporta `terminated_workflows=[]` y 200. Test `test_intervene_still_succeeds_if_temporal_unreachable`.
- [x] **PM3.** `start_remarketing_for_session` podía chocar con `WorkflowAlreadyStartedError` por race entre el terminate del zombie y el start_workflow del nuevo. Fix: swallow `WorkflowAlreadyStartedError` (igual que `start_or_signal_sales_workflow_activity`). Test `test_start_remarketing_swallows_already_started_race`.

Issues evaluadas y dejadas para iteración futura:

- **PM4.** `get_temporal_client()` no cachea: cada request HTTP abre una conexión TLS nueva (~200ms). Pre-existente, no introducido por este PR.
- **PM5.** Memoria del LLM al retomar Sales no diferencia turnos humanos. Los mensajes humanos viajan como `role=assistant` y el LLM los ve como propios. **Por diseño** — mantiene el flow natural sin que el LLM tenga que "disculparse por lo que el humano dijo".

## Notas / decisiones tomadas en runtime

- **Timeout dep-cruiser**: se subió de 5s a 30s. El subprocess `npx depcruise`
  tarda ~8s a 152 módulos; el test fallaba por timing, no por violaciones.
- **JSONL shape**: mensajes humanos se persisten como `{role: "assistant",
  sender: "human", content, timestamp}`. La API de Anthropic ignora `sender`;
  el clasificador del dashboard lo proyecta como `ui_type: "human_message"`.
- **Workflow termination al intervenir**: si Sales/Remarketing están RUNNING
  cuando el humano clickea Intervenir, se terminan vía
  `terminate_session_workflows` para evitar respuesta paralela del bot.
- **Composer state derivado**: `ChatsComposer` ya no usa `useState(intervened)`
  local — todo viene de `useSession(chatId).active_agent_route`. Si el bot
  escala automáticamente (`escalate_to_human`), el composer se cambia solo a
  intervenido sin que el operador toque nada.
