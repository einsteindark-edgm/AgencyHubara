# Task F01 — Entity sender abstraction + ChatBubble sender prop + skeleton loading

- Slug: agent-sender-and-skeleton
- HU id: HU-20260512-224306-mostrar-mensajes-del-agente-en-el-histor
- Target frontend: frontend_dashboard
- Refinement source: $ARTIFACTS_DIR/hu-refinada.md (sections cited inline)
- Planner: frontend-task-planner-archon
- Date: 2026-05-12
- Iteration: 1
- Estimated LOC: 123
- Risk: low

---

## 1. Context

Delivers acceptance criterion(s) (verbatim from refinement §1):

- **AC-1:** Given el panel de chat está abierto con una conversación existente, when el operador carga la vista, then se renderizan todos los mensajes —tanto `user_message` como `agent_message`— en orden cronológico (el backend los devuelve ya ordenados por posición en el JSONL).
- **AC-2:** Given un mensaje tiene `ui_type === "agent_message"`, when se muestra en el chat, then la burbuja está alineada a la izquierda (`self-start`) con fondo `--color-agent-bubble`; los mensajes del usuario están a la derecha (`self-end`) con fondo `--color-user-bubble`.
- **AC-3:** Given una conversación con múltiples turnos, when el operador hace scroll, then la secuencia completa usuario→agente→usuario→agente se mantiene sin saltos (el filtro `isVisibleChatMessage` preserva el orden del array).
- **AC-4:** Given la consulta de mensajes está en curso sin datos cacheados previos, when el panel de chat monta, then se muestra `<ChatMessageListSkeleton />` con burbujas placeholder alternadas izquierda/derecha en lugar del texto "Loading session...".
- **AC-5:** Given una conversación sin respuesta del agente aún, when el operador la visualiza, then solo se renderizan los mensajes del usuario (el array no contiene ningún `agent_message`, `isVisibleChatMessage` no genera placeholders vacíos).

Refinement sections that informed this task: §3 (entities/message edits), §4 (features/session-chat edits), §5 (no shared primitives), §8 (no token changes), §11 (hard rules), §12 (risks), §13 (tests), §14 (implementation order).

---

## 2. Dependencies

- **depends_on:** []
- **blocks:** []
- **Inherits from upstream tasks:** none (foundation task)
- **Backend dependency:** none — `GET /api/dashboard/sessions/{session_id}` already exists and returns `ui_type` server-side; no backend change required (refinement §6).

---

## 3. Files affected

| Path | Action | Role | LOC budget | Spinal? |
|------|--------|------|-----------|---------|
| `frontend_dashboard/src/entities/message/model.ts` | modify | Add `MessageSender` type | +3 | YES — `entities/*/model.ts` |
| `frontend_dashboard/src/entities/message/filters.ts` | modify | Add `getMessageSender` function | +8 | no (not in spinal-files.yaml) |
| `frontend_dashboard/src/entities/message/index.ts` | modify | Re-export `MessageSender` + `getMessageSender` | +2 | YES — `entities/*/index.ts` |
| `frontend_dashboard/src/entities/message/filters.test.ts` | modify | Extend with 3 new assertions | +25 | no (test file) |
| `frontend_dashboard/src/features/session-chat/ui/ChatBubble.tsx` | modify | Add `sender: MessageSender` prop; replace isUser derivation | +3 | no |
| `frontend_dashboard/src/features/session-chat/ui/ChatBubble.test.tsx` | new | RTL tests: sender-driven CSS class + micro-markdown | ~35 | no |
| `frontend_dashboard/src/features/session-chat/ui/ChatMessageList.tsx` | modify | Import `getMessageSender`; pass `sender` to ChatBubble | +3 | no |
| `frontend_dashboard/src/features/session-chat/ui/ChatMessageListSkeleton.tsx` | new | 4 alternating placeholder bubbles | ~20 | no |
| `frontend_dashboard/src/features/session-chat/ui/ChatMessageListSkeleton.test.tsx` | new | Optional: 4 bubble nodes, no visible text | ~24 | no |
| `frontend_dashboard/src/features/session-chat/ui/SessionChat.tsx` | modify | Replace loading `<div>` branch with `<ChatMessageListSkeleton />` | +4 | no |

**Spinal note:** `entities/message/model.ts` and `entities/message/index.ts` are declared spinal via glob patterns. Since this is the only task in the plan, no merger consolidation is required — the implementer writes these files directly. `features/session-chat/index.ts` is NOT modified (ChatMessageListSkeleton is feature-internal).

---

## 4. Entity layer snippets (R-Zod boundary)

No new entity is created. Existing `entities/message/` is extended with a derived type and a pure function.

```ts
// canonical — src/entities/message/model.ts
// Append after MessageUiType (line 11 in current file):
export type MessageSender = "user" | "agent";
```

```ts
// canonical — src/entities/message/filters.ts
// Append after isVisibleChatMessage (line 51 in current file):
import type { MessageSender } from "./model";

export function getMessageSender(msg: ChatMessage): MessageSender {
  // Assumes isVisibleChatMessage was applied upstream; any non-user_message
  // that passes the filter is treated as agent (exhaustive for current ui_types).
  return msg.ui_type === "user_message" ? "user" : "agent";
}
```

```ts
// canonical — src/entities/message/index.ts
// Add these 2 lines (current file has 9 lines):
export type { MessageSender } from "./model";
export { getMessageSender } from "./filters";
```

**Zod boundary:** `MessageSender` is derived client-side from `ui_type`; it does not come from the HTTP payload. No Zod schema change needed (`chatMessageSchema` in `entities/session/contracts.ts:27-37` already validates the full message array — refinement §3).

**Reused from existing entities:** `ChatMessage` (model.ts), `isVisibleChatMessage` (filters.ts), `chatMessageSchema` (contracts.ts via session entity).

---

## 5. Feature layer snippets

### ChatBubble.tsx — add sender prop (from refinement §4)

```tsx
// canonical — src/features/session-chat/ui/ChatBubble.tsx
import type { ChatMessage, MessageSender } from "@/entities/message";

interface Props {
  message: ChatMessage;
  sender: MessageSender;  // explicit; caller derives via getMessageSender
}

export function ChatBubble({ message, sender }: Props) {
  const isUser = sender === "user";
  const html = renderAgentMarkup(
    typeof message.content === "string"
      ? message.content
      : JSON.stringify(message.content ?? ""),
  );
  return (
    <div className={`bubble ${isUser ? "bubble-user" : "bubble-agent"}`}>
      <div dangerouslySetInnerHTML={{ __html: html }} />
    </div>
  );
}
// renderAgentMarkup stays unchanged (lines 16-21 in current file)
```

### ChatMessageList.tsx — pass sender to ChatBubble (from refinement §4)

```tsx
// canonical — src/features/session-chat/ui/ChatMessageList.tsx (delta only)
// Line 2: extend import:
import { type ChatMessage, isVisibleChatMessage, getMessageSender } from "@/entities/message";
// Line 25: change ChatBubble call:
<ChatBubble key={idx} message={msg} sender={getMessageSender(msg)} />
```

### ChatMessageListSkeleton.tsx — new file (from refinement §4)

```tsx
// canonical — src/features/session-chat/ui/ChatMessageListSkeleton.tsx
export function ChatMessageListSkeleton() {
  // Uses legacy CSS classes .bubble/.bubble-user/.bubble-agent from
  // Dashboard.legacy.css. animate-pulse is built-in Tailwind v4.
  return (
    <div className="chat-messages">
      <div className="bubble bubble-user opacity-40 animate-pulse w-[45%]" />
      <div className="bubble bubble-agent opacity-40 animate-pulse w-[60%]" />
      <div className="bubble bubble-user opacity-40 animate-pulse w-[35%]" />
      <div className="bubble bubble-agent opacity-40 animate-pulse w-[55%]" />
    </div>
  );
}
```

### SessionChat.tsx — replace loading branch (from refinement §4, §10)

```tsx
// canonical — src/features/session-chat/ui/SessionChat.tsx (delta only)
// Add import after line 12:
import { ChatMessageListSkeleton } from "./ChatMessageListSkeleton";
// Replace lines 37-43 (isLoading branch):
  if (isLoading && !details) {
    return <ChatMessageListSkeleton />;
  }
```

---

## 6. Page mount (composition wiring)

**No page change.** `Dashboard.tsx` already mounts `<SessionChat sessionId={selectedSessionId} />` without modification. No JSX delta needed (refinement §2, §10).

---

## 7. Tailwind tokens (if any)

**No new tokens.** `animate-pulse` is built-in Tailwind v4. The skeleton reuses `.bubble`, `.bubble-user`, `.bubble-agent` from `Dashboard.legacy.css`, which in turn consume CSS variables `--color-agent-bubble` / `--color-user-bubble` already declared in `src/index.css:25,27`. No changes to `src/index.css` (refinement §8).

---

## 8. Entity / feature barrel updates

```diff
// frontend_dashboard/src/entities/message/index.ts
  export type { ChatMessage, MessageUiType } from "./model";
+ export type { MessageSender } from "./model";
  export { chatMessageSchema, messageUiTypeSchema } from "./contracts";
  export type { ChatMessageDto } from "./contracts";
  export {
    isTechnicalEvent,
    isGhostTrigger,
    isAgentEcho,
    isVisibleChatMessage,
+   getMessageSender,
  } from "./filters";
```

`features/session-chat/index.ts` — **no change.** `ChatMessageListSkeleton` is feature-internal; it is not exported from the feature barrel (refinement §5, §11 barrel-only rule).

---

## 9. Tests

| Test file | New / modified | Scenarios |
|-----------|---------------|-----------|
| `frontend_dashboard/src/entities/message/filters.test.ts` | modified (extend) | `getMessageSender` returns "user" for user_message; "agent" for agent_message; "agent" for unknown/future ui_type |
| `frontend_dashboard/src/features/session-chat/ui/ChatBubble.test.tsx` | new | `sender="user"` → wrapper has class `bubble-user`; `sender="agent"` → class `bubble-agent`; micro-markdown renders `**x**` as `<strong>x</strong>` |
| `frontend_dashboard/src/features/session-chat/ui/ChatMessageListSkeleton.test.tsx` | new (optional) | Renders 4 elements with class `bubble`; no visible text content in any placeholder |

**Test name list (implementer writes the bodies):**

```
filters.test.ts::getMessageSender returns "user" for user_message
filters.test.ts::getMessageSender returns "agent" for agent_message
filters.test.ts::getMessageSender returns "agent" for unknown ui_type passing filter

ChatBubble.test.tsx::renders with bubble-user class when sender is "user"
ChatBubble.test.tsx::renders with bubble-agent class when sender is "agent"
ChatBubble.test.tsx::renders micro-markdown bold (**x** → <strong>x</strong>)

ChatMessageListSkeleton.test.tsx::renders 4 bubble placeholder elements
ChatMessageListSkeleton.test.tsx::placeholder elements have no visible text content
```

---

## 10. Verification commands

All commands must exit 0. CWD = `frontend_dashboard/` (see project-context.md §Command conventions).

```bash
# 1. Entity layer — type check after model.ts + filters.ts + index.ts edits
cd frontend_dashboard && npx tsc -b

# 2. Entity tests
cd frontend_dashboard && npm test -- entities/message

# 3. Feature tests
cd frontend_dashboard && npm test -- features/session-chat

# 4. Full production build
cd frontend_dashboard && npm run build

# 5. Full suite + lint (final gate)
cd frontend_dashboard && npm test && npm run lint
```

**FSD compliance greps (must return empty):**

```bash
cd frontend_dashboard

# No rogue fetch outside entities/*/api.ts or shared/api/
grep -rEn "fetch\(" src/features src/pages src/app | grep -v "// allowed:"

# No deep feature imports (always via barrel)
grep -rEn "from ['\"]@/features/[^'\"]+/(ui|model)/" src/features

# No cross-feature imports
grep -rEn "from ['\"]@/features/" src/features \
  | grep -vE "^src/features/([a-z-]+)/[^:]+:.*from ['\"]@/features/\1"

# No manual useState+useEffect+fetch combo
grep -rEn "useState.*useEffect.*fetch" src/features src/pages
```

---

## 11. Definition of Done

- [ ] `entities/message/model.ts` exports `MessageSender = "user" | "agent"`.
- [ ] `entities/message/filters.ts` exports `getMessageSender(msg: ChatMessage): MessageSender`.
- [ ] `entities/message/index.ts` re-exports both `MessageSender` (type) and `getMessageSender`.
- [ ] `entities/message/filters.test.ts` has 3 new passing assertions for `getMessageSender`.
- [ ] `ChatBubble.tsx` Props interface includes `sender: MessageSender`; `isUser` derives from `sender === "user"`, not from `message.ui_type`.
- [ ] `ChatBubble.test.tsx` created with ≥3 passing RTL tests (sender class + micro-markdown).
- [ ] `ChatMessageList.tsx` imports `getMessageSender` from `@/entities/message` (barrel); passes `sender={getMessageSender(msg)}` to each `<ChatBubble>`.
- [ ] `ChatMessageListSkeleton.tsx` created; renders 4 alternating placeholder `<div>` elements with `.bubble .bubble-user/.bubble-agent .animate-pulse`.
- [ ] `SessionChat.tsx` `isLoading && !details` branch returns `<ChatMessageListSkeleton />` (imports from `./ChatMessageListSkeleton`).
- [ ] All verification commands in §10 exit 0.
- [ ] `npm test` (full suite) shows no regressions beyond the new test files.
- [ ] All FSD compliance greps return empty.
- [ ] `features/session-chat/index.ts` is **unchanged** (ChatMessageListSkeleton remains internal).
- [ ] `Dashboard.tsx` is **unchanged**.
- [ ] `src/index.css` is **unchanged**.

---

## 12. FSD rules check

- **Import rules (layering):** applies — `features/session-chat` imports `getMessageSender` and `MessageSender` from `@/entities/message` (feature→entity ✓). No upward import toward pages or app.
- **Barrel-only public API:** applies — `getMessageSender` is exposed only via `entities/message/index.ts` barrel (✓). `ChatMessageListSkeleton` is NOT exported from `features/session-chat/index.ts` — it stays feature-internal (✓).
- **Zod at HTTP boundary:** applies — no new HTTP boundary introduced. The existing `sessionDetailsSchema` in `entities/session/contracts.ts:27-37` validates the messages array with `z.array(chatMessageSchema)`. `MessageSender` is client-side derived (✓).
- **TanStack Query for server data:** applies — no new server state hook is introduced. `useSession(id)` remains the single data source, cached by TanStack Query (✓).
- **No cross-feature imports:** applies — `session-chat` does not import from any other feature (✓).
- **No deep imports:** applies — `ChatMessageList.tsx` imports via `@/entities/message` barrel, not `@/entities/message/filters` (✓).
- **No fetch() in components/pages:** applies — no new `fetch()` call is introduced in any component (✓).
- **Tailwind token naming:** applies — no new tokens added; existing `--color-agent-bubble` / `--color-user-bubble` follow the `--color-*` convention (✓).
- **JSX files use .tsx:** applies — `ChatMessageListSkeleton.tsx` and `ChatBubble.test.tsx` use `.tsx` extension (✓).

---

## 13. Open questions / risks

1. **Non-exhaustive `getMessageSender`:** If a future `ui_type` passes `isVisibleChatMessage` and is neither `user_message` nor `agent_message`, it silently renders as an agent bubble. Recommended default: add a JSDoc comment in `getMessageSender` documenting this assumption; update when a new type is added to `MessageUiType`.

2. **`agent_message` with `content: null`:** Backend may emit assistant entries with `content: null`; `ChatBubble` renders them as empty `<div>` (via `JSON.stringify(null ?? "")` → `""`). This HU does not fix it. Recommended default: follow-up HU to add a guard in `isVisibleChatMessage` (`&& msg.content !== null`) or in `ChatBubble`.

3. **Skeleton tied to `Dashboard.legacy.css`:** `ChatMessageListSkeleton` relies on `.bubble`, `.bubble-user`, `.bubble-agent` from the legacy stylesheet. If those classes migrate to Tailwind utilities, the skeleton must be updated. Risk is low for this HU; tracked against the CSS migration story.

4. **Backend dependency:** none — `GET /api/dashboard/sessions/{session_id}` exists and returns `ui_type` server-side. No backend changes required before this task can run (refinement §6).
