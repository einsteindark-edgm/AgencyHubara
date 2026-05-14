# Plan — Human Handoff (cerrar el círculo de escalación)

## Contexto

El PR previo (snapshot=verdad + escalation tool + ruta humano) entregó:
- Cuando `EscalateToHumanTool` corre → `metadata.json[active_route=humano, tag=HUMANO]`.
- `LoadOrStartSalesSession` ya filtra mensajes nuevos: si ruta=humano, no dispara LLM.
- Frontend ya soporta tag `HUMANO`.

**Falta cerrar el círculo**: el humano operador del dashboard necesita
1. **Tomar el control** de una conversación (desde el dashboard, no fuera de banda).
2. **Responderle al cliente por WhatsApp** desde el dashboard.
3. **Persistir cada mensaje suyo** en la memoria del chat (JSONL) marcado como humano.
4. **Devolver el control al bot** eligiendo entre Sales o Remarketing.

## Decisiones tomadas (del operador)

- Etiqueta de sesión durante el takeover: `tag=HUMANO` (preserva la actual).
- Cada mensaje del humano queda en el JSONL con `sender: "human"` y `role: "assistant"`.
  - Roleo `assistant` para que cuando vuelva al bot, exoclaw lo vea como historial natural.
  - Campo extra `sender` para que el dashboard renderice distinto y para tener trazabilidad histórica.
- Devolución al bot: el humano **elige** target (`ventas` | `remarketing`):
  - `ventas` → solo marca metadata; el siguiente mensaje del cliente arranca Sales normal.
  - `remarketing` → arranca `RemarketingWorkflow` proactivamente con un `motivo` que el humano provee, para que mande un gancho.

## Diseño backend

### Endpoints nuevos (`src/dashboard/handoff.py`)

| Método | Path | Body | Guarda |
|---|---|---|---|
| `POST` | `/api/dashboard/sessions/{id}/intervene` | `{ motivo?: str }` | Setea `tag=HUMANO, active_route=humano` y terminate de workflows en vuelo |
| `POST` | `/api/dashboard/sessions/{id}/messages` | `{ text: str }` | Sólo si `active_route==humano`. Envía a WhatsApp + persiste con `sender=human` |
| `POST` | `/api/dashboard/sessions/{id}/return-to-bot` | `{ target_route: "ventas"\|"remarketing", motivo?: str }` | Sólo si `active_route==humano`. Marca metadata; si remarketing → arranca workflow |

### Cambios cross-cutting

- **`src/platform/whatsapp/activities.py`**: extraer `send_message_to_session(session_id, text)` (pura) que el activity y los handlers HTTP reusan. La activity se queda thin.
- **`src/platform/session_history/store.py`**: agregar `append_human_event(session_id, content)` que escribe `{role: "assistant", content, sender: "human", timestamp}`.
- **`src/platform/temporal/dispatcher.py`**: extraer `start_remarketing_for_session(client, session_id, motivo, delay_seconds=0)` reusable por activity + HTTP.
- **`src/dashboard/api.py`**: extender el clasificador de `get_session_history` para mapear `sender=="human"` a `ui_type: "human_message"`.
- **`src/main.py`**: registrar el nuevo router.

### Race: workflow en vuelo durante intervención

Si Sales está procesando un turno cuando el humano clickea "Intervenir", podría responderle al cliente mientras el humano ya tomó el control. Mitigación: en `POST /intervene`, intentamos terminar `session-{id}` y `remarketing-{id}` si están RUNNING. Mismo patrón que el dispatcher usa para zombies (`terminate(reason=...)`).

## Diseño frontend

### Wiring del composer ya existente

`features/chats-conversation/ui/ChatsComposer.tsx` ya tiene los dos modos (bot gestiona / intervenido) — sólo hay que reemplazar el `useState(false)` local por el `active_agent_route` real y agregar mutations.

- `entities/handoff/api.ts` (nuevo) — `useInterveneMutation`, `useSendHumanMessageMutation`, `useReturnToBotMutation` con `invalidateQueries(sessionKeys.detail(id))`.
- `ChatsComposer`:
  - Si `active_agent_route !== "humano"`: muestra banner + "Intervenir" → llama `useInterveneMutation`.
  - Si `active_agent_route === "humano"`: muestra textarea con send + dropdown "Devolver al bot" con opciones Sales/Remarketing.

### Render diferenciado de mensajes humanos

- `entities/message/contracts.ts`: agregar `"human_message"` al enum `messageUiTypeSchema`.
- `entities/message/model.ts`: extender el tipo.
- `entities/message/filters.ts`: incluir `human_message` en `isVisibleChatMessage`. `getMessageSender` lo trata como agent (kind: out).
- `entities/chat/api.ts`: en `adaptMessage`, marcar mensajes humanos con un campo distintivo para el bubble.
- `features/chats-conversation/ui/ChatsBubble.tsx`: si el mensaje es human, agregar un badge "👤 Humano" o cambiar color.

## Archivos involucrados

### Backend nuevos
- `src/dashboard/handoff.py`
- `src/dashboard/composition.py`

### Backend modificados
- `src/main.py`
- `src/dashboard/api.py`
- `src/platform/whatsapp/activities.py`
- `src/platform/session_history/store.py`
- `src/platform/temporal/dispatcher.py`

### Frontend nuevos
- `src/entities/handoff/api.ts`
- `src/entities/handoff/index.ts`

### Frontend modificados
- `src/entities/message/model.ts`
- `src/entities/message/contracts.ts`
- `src/entities/message/filters.ts`
- `src/entities/chat/api.ts`
- `src/entities/chat/model.ts`
- `src/features/chats-conversation/ui/ChatsComposer.tsx`
- `src/features/chats-conversation/ui/ChatsBubble.tsx`

### Tests backend
- `tests/test_handoff_endpoints.py`
- `tests/test_session_history_human.py`
- `tests/test_dashboard_handoff_classifier.py`

### Tests frontend
- `src/entities/handoff/api.test.ts`
- `src/features/chats-conversation/ui/ChatsComposer.test.tsx`

## Orden de ejecución (mantiene tests verdes en cada paso)

1. **Backend**: extraer helpers reusables (`send_message_to_session`, `start_remarketing_for_session`, `append_human_event`).
2. **Backend**: handlers HTTP en `dashboard/handoff.py`.
3. **Backend**: clasificador para `human_message` en `dashboard/api.py`.
4. **Backend**: wiring en `main.py`.
5. **Backend tests**: 3 archivos nuevos.
6. **Frontend**: contratos + clasificadores (entities).
7. **Frontend**: hooks de mutación (`entities/handoff`).
8. **Frontend**: wire del composer (mutations + estado real).
9. **Frontend**: render del bubble humano.
10. **Frontend tests**: handoff hooks + composer.

## Verificación end-to-end

1. Cliente escribe "necesito 50 velas para boda" → Sales escala → metadata `tag=HUMANO`.
2. Dashboard: la sesión aparece con badge HUMANO. Humano selecciona la conversación.
3. Composer muestra el modo "intervenido" (porque ruta=humano). Humano escribe respuesta → llega al cliente vía WhatsApp + queda en el JSONL con `sender=human`.
4. Cliente responde → mensaje se persiste pero el bot NO responde (route=humano).
5. Humano resuelve, clickea "Devolver al bot" → modal "¿Sales o Remarketing?" → confirma Sales → `tag=RETOMA_VENTA, active_route=ventas`.
6. Cliente escribe "¿entonces sí me confirmas?" → `LoadOrStartSalesSession` ve `ventas`, arranca Sales, el LLM ve todo el historial (incluyendo mensajes del humano marcados `sender=human` que la API de Anthropic ignora pero que están en el contexto como `assistant`).

## Notas para futuras iteraciones

- **`tag=HUMANO_EN_PROGRESO` vs `HUMANO`**: por ahora un solo tag durante el takeover; podríamos diferenciar "asignado" vs "respondió".
- **Notificaciones**: cuando llega un cliente nuevo a la cola humano, el dashboard podría hacer push (out-of-scope).
- **Auth**: el endpoint `intervene` no chequea quién es el humano. Cuando agreguemos auth, registramos `agent_id` en `status_history`.
- **Hand-off entre humanos**: dos humanos en la misma sesión no se manejan (último que escribe gana). Para v1 está bien.
