Tech refinement (frontend) — Mostrar mensajes del agente en el historial del chat

HU id: HU-20260512-224306-mostrar-mensajes-del-agente-en-el-histor
Source: $ARTIFACTS_DIR/hu-original.md
Target frontend: frontend_dashboard (cwd: /Users/edgm/Documents/Projects/AgencyHubara/frontend_dashboard)
Layout status: FSD in place
Refiner: frontend-tech-refiner-archon
Date: 2026-05-12
Iteration: 1

---

## 1. Scope

**Summary:** Solidificar la renderización de mensajes del agente en el panel de chat: abstraer la lógica de `sender` al entity layer, exponer una prop explícita en `ChatBubble`, y reemplazar el loading de texto plano por un skeleton de burbujas.

**Acceptance criteria:**

- Given el panel de chat está abierto con una conversación existente, when el operador carga la vista, then se renderizan todos los mensajes —tanto `user_message` como `agent_message`— en orden cronológico (el backend los devuelve ya ordenados por posición en el JSONL).
- Given un mensaje tiene `ui_type === "agent_message"`, when se muestra en el chat, then la burbuja está alineada a la izquierda (`self-start`) con fondo `--color-agent-bubble`; los mensajes del usuario están a la derecha (`self-end`) con fondo `--color-user-bubble`.
- Given una conversación con múltiples turnos, when el operador hace scroll, then la secuencia completa usuario→agente→usuario→agente se mantiene sin saltos (el filtro `isVisibleChatMessage` preserva el orden del array).
- Given la consulta de mensajes está en curso sin datos cacheados previos, when el panel de chat monta, then se muestra `<ChatMessageListSkeleton />` con burbujas placeholder alternadas izquierda/derecha en lugar del texto "Loading session...".
- Given una conversación sin respuesta del agente aún, when el operador la visualiza, then solo se renderizan los mensajes del usuario (el array no contiene ningún `agent_message`, `isVisibleChatMessage` no genera placeholders vacíos).

**Out of scope:**

- Streaming en tiempo real de respuestas del agente (SSE per-sesión / WebSocket).
- Enviar, editar o eliminar mensajes desde el dashboard.
- Filtrar la vista por tipo de remitente.
- Mostrar metadata técnica (tool calls, tokens, system prompts).
- Migrar el styling de `Dashboard.legacy.css` a Tailwind utilities (trabajo separado).

---

## 2. Page(s) affected

**Decision:** no page change — la modificación es interna al feature `session-chat` y al entity `message`.

**Justification:** `Dashboard.tsx` monta `<SessionChat sessionId={...} />` sin cambios. El estado cross-feature (`selectedSessionId`) ya está en la página y no se toca.

**Cross-feature state added/lifted:** none

---

## 3. Entities affected/created

### `entities/message/` — extended

| File | Status | Change |
|------|--------|--------|
| `model.ts` | edit | Añadir `export type MessageSender = "user" \| "agent"` (nuevo tipo derivado; no viene del backend) |
| `contracts.ts` | no change | `chatMessageSchema` ya valida `ui_type`; `sender` es derivado, no del payload |
| `keys.ts` | no change needed | No existe ni se crea: los mensajes se obtienen embebidos en `sessionDetailsSchema` vía `entities/session`; la query key es `sessionKeys.detail(id)` |
| `api.ts` | no change needed | No existe ni se crea: el fetch lo hace `useSession(id)` en `entities/session/api.ts:48` |
| `filters.ts` | edit | Añadir `getMessageSender(msg: ChatMessage): MessageSender` |
| `index.ts` | edit | Exportar `MessageSender` y `getMessageSender` |

**Función nueva en `filters.ts`:**

```ts
// pseudo
export function getMessageSender(msg: ChatMessage): MessageSender {
  return msg.ui_type === "user_message" ? "user" : "agent";
}
```

Lógica: los únicos mensajes que llegan a `ChatBubble` ya pasaron `isVisibleChatMessage` (filtra `system_event`, `agent_tool_call`, `tool_execution_result`, ghost triggers, agent echoes). El único `ui_type` distinto de `agent_message` que queda es `user_message`. El mapeo es exhaustivo para la práctica.

**New query hooks:** ninguno — los mensajes viajan en `SessionDetails.messages` (ver `entities/session/model.ts:38`), validados por `z.array(chatMessageSchema)` en `entities/session/contracts.ts:36`.

---

## 4. Features affected/created

### `features/session-chat/` — extended

| File | Status | Change |
|------|--------|--------|
| `ui/ChatBubble.tsx` | edit | Agregar `sender: MessageSender` a `Props`; reemplazar `const isUser = message.ui_type === "user_message"` por `const isUser = sender === "user"` |
| `ui/ChatMessageList.tsx` | edit | Importar `getMessageSender` desde `@/entities/message`; pasar `sender={getMessageSender(msg)}` a `<ChatBubble>` |
| `ui/ChatMessageListSkeleton.tsx` | new | Skeleton con burbujas placeholder alternadas izquierda/derecha |
| `ui/SessionChat.tsx` | edit | En el branch `isLoading && !details`, reemplazar el `<div>Loading session...</div>` inline por `<ChatMessageListSkeleton />` |
| `ui/ChatHeader.tsx` | no change | — |
| `ui/ChatInput.tsx` | no change | — |
| `index.ts` | no change | Sigue exportando solo `SessionChat` |

**Props shape de ChatBubble (solo se añade `sender`):**

```tsx
// pseudo
interface Props {
  message: ChatMessage;
  sender: MessageSender;  // nuevo
}
```

**Comportamiento de ChatMessageListSkeleton:**

```tsx
// pseudo
export function ChatMessageListSkeleton() {
  // usa las clases legacy .chat-messages, .bubble, .bubble-user, .bubble-agent
  // más animate-pulse de Tailwind para el shimmer
  return (
    <div className="chat-messages">
      {/* 4 placeholders alternados, sin texto */}
      <div className="bubble bubble-user opacity-40 animate-pulse w-[45%]" />
      <div className="bubble bubble-agent opacity-40 animate-pulse w-[60%]" />
      <div className="bubble bubble-user opacity-40 animate-pulse w-[35%]" />
      <div className="bubble bubble-agent opacity-40 animate-pulse w-[55%]" />
    </div>
  );
}
```

Las anchuras arbitrarias (`w-[45%]` etc.) simulan longitudes distintas de mensaje. No se añaden tokens nuevos.

**Entity hooks consumed:** `useSession` (ya existente, sin cambio) — consumido en `SessionChat.tsx:19`

**Local state hooks:** ninguno nuevo

**Cross-feature state:** ninguno — la prop `sender` es local a cada burbuja, derivada del mensaje

---

## 5. Shared primitives

No new shared primitives. `ChatMessageListSkeleton` es feature-internal: solo `session-chat` la usa.

---

## 6. Backend contract dependencies

| Endpoint | Status | Cited backend file | Frontend Zod schema |
|----------|--------|--------------------|---------------------|
| `GET /api/dashboard/sessions/{session_id}` | exists, no change | `hubara_agency/src/dashboard/api.py:76-137` | `sessionDetailsSchema` (con `messages: z.array(chatMessageSchema)`) en `entities/session/contracts.ts:27-37` |

**Blocked work items:** none — el backend ya inyecta `ui_type` server-side y devuelve los mensajes del agente en el array unificado `messages`. No se necesita campo `sender` en el payload (se deriva en el frontend).

---

## 7. Cross-feature state

No cross-feature state added. La prop `sender` es local a cada `<ChatBubble>` invocation.

---

## 8. Tailwind token deltas

No new tokens. El skeleton reutiliza las clases `.bubble-user` y `.bubble-agent` de `Dashboard.legacy.css` (que ya aplican colores vía `var(--user-bubble)` / `var(--agent-bubble)`, mapeadas desde `--color-user-bubble` / `--color-agent-bubble` en `index.css:27,25`).

`animate-pulse` es una utilidad built-in de Tailwind v4; no requiere token.

---

## 9. App-layer wiring

Provider added: none
main.tsx change: no
`src/app/providers/index.tsx`: no change

---

## 10. Composition wiring

No new features. `SessionChat` ya está montado en `Dashboard.tsx` y no cambia su punto de mount.

El único cambio de composición es **dentro** de `SessionChat.tsx`:

| Feature | Mount file | Cambio de wiring |
|---------|-----------|-----------------|
| `ChatMessageListSkeleton` | `features/session-chat/ui/SessionChat.tsx:37-43` (branch `isLoading && !details`) | Reemplaza `<div style={{...}}>Loading session...</div>` |

---

## 11. Hard rules check

1. **Import rules (layering):** applies — `features/session-chat` importa de `@/entities/message` (feature → entity ✓). Ningún import hacia pages ni app.
2. **Barrel-only public API:** applies — `getMessageSender` y `MessageSender` se exponen solo desde `entities/message/index.ts`. `ChatMessageListSkeleton` es interno al feature (no se exporta desde `features/session-chat/index.ts`).
3. **Zod at HTTP boundary:** applies — `sessionDetailsSchema.parse(raw)` en `entities/session/api.ts:32` valida el array de mensajes con `z.array(chatMessageSchema)`. No se añade ningún fetch sin Zod.
4. **TanStack Query for server data:** applies — los mensajes viven en el cache de `useSession(id)`. No se introduce ningún `useState` para datos del servidor.
5. **No cross-feature imports:** applies — ningún feature importa de otro feature.
6. **No deep imports:** applies — `ChatMessageList` importa `{ getMessageSender, isVisibleChatMessage }` desde `@/entities/message` (barrel), no desde un subpath.
7. **No fetch() in components/pages:** applies — el único fetch está en `entities/session/api.ts:31`.
8. **Tailwind token naming:** applies — no se añaden tokens nuevos; los existentes siguen la convención `--color-agent-*`, `--color-user-*`.
9. **JSX files use .tsx:** applies — `ChatMessageListSkeleton.tsx` usará extensión `.tsx`.

---

## 12. Risks / open questions

1. **`getMessageSender` asume exhaustividad en `isVisibleChatMessage`:** Si en el futuro se añade un nuevo `ui_type` que pase el filtro y no sea `user_message`, será clasificado como `"agent"` (el else branch). Recommended default: aceptable por ahora; documentar con un comentario en `getMessageSender` y añadir el nuevo tipo a `MessageUiType` + al filtro correspondiente si aplica.

2. **`agent_message` con `content: null`:** El backend puede producir entradas `role: "assistant"` sin `tool_calls` y con `content: null` (si el LLM generó una respuesta vacía). `ChatBubble` actualmente renderiza `JSON.stringify(null ?? "")` → `""` en el HTML, produciendo una burbuja vacía visible. La HU no pide resolver esto, pero es un edge case a atender en follow-up. Recommended default: agregar un filtro en `isVisibleChatMessage` para descartar mensajes con `content: null` o una guarda en `ChatBubble` para no renderizar si `html === ""`.

3. **Skeleton usa clases de `Dashboard.legacy.css`:** El skeleton apoya en `.bubble`, `.bubble-user`, `.bubble-agent` definidas en el archivo legacy. Si esas clases se migran/eliminan, el skeleton se rompe junto con el resto del chat. No es un riesgo de esta HU; se resuelve cuando se migre el CSS.

4. **Backend dependency:** none — el endpoint existe y no requiere cambios.

5. **Defer to follow-up design doc:** none en esta HU.

6. **Pre-existing FSD violation in touched code:** none detectada en los archivos que toca esta HU.

---

## 13. Tests

| Test file | Type | Asserts |
|-----------|------|---------|
| `frontend_dashboard/src/entities/message/filters.test.ts` | unit (extend) | `getMessageSender` retorna `"user"` para `user_message`; `"agent"` para `agent_message`; `"agent"` para cualquier otro `ui_type` que pasara el filtro |
| `frontend_dashboard/src/features/session-chat/ui/ChatBubble.test.tsx` | RTL (new) | Con `sender="user"` el wrapper tiene clase `bubble-user`; con `sender="agent"` tiene clase `bubble-agent`; el contenido se renderiza con micro-markdown (`**x**` → `<strong>`) |
| `frontend_dashboard/src/features/session-chat/ui/ChatMessageListSkeleton.test.tsx` | RTL (new, optional) | Renderiza 4 nodos con clase `bubble`; no hay texto visible (burbujas vacías) |

---

## 14. Implementation order (suggested)

1. **`entities/message/model.ts`** — añadir `export type MessageSender = "user" | "agent"`. Verificar: `cd frontend_dashboard && npx tsc -b`.
2. **`entities/message/filters.ts`** — añadir `getMessageSender`. Extender `filters.test.ts` con los 3 asserts. Verificar: `cd frontend_dashboard && npm test -- entities/message`.
3. **`entities/message/index.ts`** — añadir `MessageSender` y `getMessageSender` a los exports. Verificar: `cd frontend_dashboard && npx tsc -b`.
4. **`features/session-chat/ui/ChatBubble.tsx`** — agregar `sender: MessageSender` a `Props`; cambiar la línea `const isUser = message.ui_type === "user_message"` por `const isUser = sender === "user"`. Escribir `ChatBubble.test.tsx`. Verificar: `cd frontend_dashboard && npm test -- features/session-chat/ui/ChatBubble`.
5. **`features/session-chat/ui/ChatMessageList.tsx`** — importar `getMessageSender` desde `@/entities/message`; pasar `sender={getMessageSender(msg)}` a `<ChatBubble>`. Verificar: `cd frontend_dashboard && npx tsc -b`.
6. **`features/session-chat/ui/ChatMessageListSkeleton.tsx`** — crear el componente skeleton. Verificar: `cd frontend_dashboard && npm test -- features/session-chat/ui/ChatMessageListSkeleton` (si se escribe test).
7. **`features/session-chat/ui/SessionChat.tsx`** — importar `ChatMessageListSkeleton`; reemplazar el branch `isLoading && !details` con `return <ChatMessageListSkeleton />`. Verificar: `cd frontend_dashboard && npx tsc -b`.
8. **FSD compliance greps** (obligatorio antes de declarar done):
   ```bash
   cd frontend_dashboard
   grep -rEn "fetch\(" src/features src/pages src/app | grep -v "// allowed:"
   grep -rEn "from ['\"]@/features/[^'\"]+/(ui|model)/" src/features
   grep -rEn "from ['\"]@/features/" src/features | grep -vE "^src/features/([a-z-]+)/[^:]+:.*from ['\"]@/features/\1"
   ```
9. **Full suite + lint:** `cd frontend_dashboard && npm test && npm run lint && npm run build`.
