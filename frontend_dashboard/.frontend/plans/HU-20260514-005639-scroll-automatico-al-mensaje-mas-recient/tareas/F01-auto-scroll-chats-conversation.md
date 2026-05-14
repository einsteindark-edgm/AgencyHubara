# Task F01 — Smart auto-scroll hook for ChatsConversation

- Slug: auto-scroll-chats-conversation
- HU id: HU-20260514-005639-scroll-automatico-al-mensaje-mas-recient
- Target frontend: frontend_dashboard
- Refinement source: $ARTIFACTS_DIR/hu-refinada.md (sections cited inline)
- Planner: frontend-task-planner-archon
- Date: 2026-05-13
- Iteration: 1
- Estimated LOC: 160
- Risk: low

## 1. Context

Delivers acceptance criterion(s) (verbatim from refinement §1):

- AC-1: Given the panel is at the bottom, when a new message arrives (from client or agent), then the panel scrolls automatically to the new message without operator interaction.
- AC-2: Given the operator has scrolled up (position ≠ bottom, threshold > 50 px from bottom), when a new message arrives, then the panel does NOT auto-scroll and the operator's reading position is preserved.
- AC-3: Given the operator has scrolled up and new messages have accumulated, when the operator manually scrolls to the bottom of the panel, then auto-scroll mode re-activates and subsequent new messages will scroll the panel automatically.
- AC-4: Given the operator opens a conversation with existing messages, when the panel finishes rendering, then the scroll position is set to the most recent message (instant, no visible animation).
- AC-5: Given TanStack Query polls and returns the same message list (same length), when the poll completes, then no scroll occurs and no perceptible re-render happens.

Refinement sections that informed this task: §4 (features affected), §5 (shared primitives), §8 (Tailwind), §11 (hard rules), §12 (risks), §13 (tests), §14 (implementation order).

## 2. Dependencies

- depends_on: []
- blocks: []
- Inherits from upstream tasks: none (foundation task)
- Backend dependency: none (`useChatMessages(chatId)` already exists and returns `ChatMessageItem[]` — no new endpoint needed)

## 3. Files affected

| Path | Action | Role | LOC budget |
|------|--------|------|-----------|
| `frontend_dashboard/src/features/chats-conversation/model/useAutoScroll.ts` | new | smart auto-scroll hook (3 effects + refs + scroll listener) | ~50 |
| `frontend_dashboard/src/features/chats-conversation/model/useAutoScroll.test.ts` | new | hook tests — 5 scenarios via renderHook + JSDOM mock | ~95 |
| `frontend_dashboard/src/features/chats-conversation/ui/ChatsConversation.tsx` | modify | import `useAutoScroll`, call it, attach `containerRef` to `div.msgs` | +8 net |

> `ChatsConversation.tsx` is a modify-not-spinal file (not declared in spinal-files.yaml). This task is the sole modifier; no merger consolidation needed — the implementer edits it directly.

> The feature barrel (`frontend_dashboard/src/features/chats-conversation/index.ts`) is NOT modified — `useAutoScroll` is feature-internal and must not be barrel-exported (refinement §4, §11 rule 2).

## 4. Entity layer snippets (R-Zod boundary)

No entity changes in this task. `useChatMessages(chatId)` at
`frontend_dashboard/src/entities/chat/api.ts:161` is consumed unchanged.
`ChatMessageItem[]` type from the same entity is the only entity type referenced
inside `ChatsConversation.tsx` (pre-existing).

## 5. Feature layer snippets

### `model/useAutoScroll.ts`

```ts
// canonical — frontend_dashboard/src/features/chats-conversation/model/useAutoScroll.ts
import { useEffect, useRef } from "react";

export function useAutoScroll(
  itemCount: number,      // messages.length — sole scroll trigger (from refinement §4)
  resetKey: string | null // chatId — forces jump to bottom on conversation switch
): React.RefObject<HTMLDivElement> {
  const containerRef = useRef<HTMLDivElement>(null);
  const isAtBottomRef = useRef<boolean>(true); // ref, not state — avoids re-render (§11 rule 4)

  // Effect A — resetKey changes: reset sticky flag + instant jump (AC-4, covers chatId switch)
  useEffect(() => { /* ... */ }, [resetKey]);

  // Effect B — itemCount changes: scroll if sticky (AC-1, AC-3, AC-5 no-op on same count)
  useEffect(() => { /* ... */ }, [itemCount]);

  // Effect C — mount only: attach passive scroll listener (AC-2, AC-3 detection)
  useEffect(() => { /* ... */ return () => { /* cleanup */ }; }, []);

  return containerRef;
}
```

> Effect declaration order must be A → B → C so that when `resetKey` changes,
> Effect A sets `isAtBottomRef.current = true` before Effect B reads it
> (React runs effects in declaration order — refinement §12, risk 1).

### `ui/ChatsConversation.tsx` edit

Current code at lines 74–83 (from refinement §4):

```tsx
// BEFORE — frontend_dashboard/src/features/chats-conversation/ui/ChatsConversation.tsx
{subTab === "Chat" && (
  <>
    <div className="msgs">
      {messages.map((m, i) => (
        <ChatsBubble key={i} message={m} />
      ))}
    </div>
    <ChatsComposer />
  </>
)}
```

```tsx
// canonical — AFTER (delta to apply)
// Add near top of ChatsConversation body (after messages is available):
const containerRef = useAutoScroll(messages.length, chatId);

// Existing JSX — only change: add ref prop to div.msgs
{subTab === "Chat" && (
  <>
    <div className="msgs" ref={containerRef}>
      {messages.map((m, i) => (
        <ChatsBubble key={i} message={m} />
      ))}
    </div>
    <ChatsComposer />
  </>
)}
```

> `chatId` is already in scope (pre-existing prop). `messages` comes from
> `useChatMessages(chatId)` (pre-existing, unchanged). No prop changes needed
> (refinement §4 — "Props shape: no change").

### Feature barrel — NO CHANGE

```ts
// canonical — frontend_dashboard/src/features/chats-conversation/index.ts
// useAutoScroll is internal — do NOT add it here.
// Only ChatsConversation is exported (pre-existing, unchanged).
export { ChatsConversation } from "./ui/ChatsConversation";
```

## 6. Page mount (composition wiring)

No page change required. `ChatsConversation` is already mounted in
`frontend_dashboard/src/pages/Dashboard.tsx:168` (refinement §10):

```tsx
// src/pages/Dashboard.tsx:168 — NO EDIT NEEDED
<ChatsConversation chatId={selectedChatId} />
```

Props shape is unchanged. `Dashboard.tsx` is not in `affects_spinal_files` for this task.

## 7. Tailwind tokens (if any)

None. `div.msgs` already has `overflow-y: auto` at
`frontend_dashboard/src/index.css:468` (confirmed in refinement §8).
No new `@theme` tokens required.

## 8. Entity / feature barrel updates

No existing barrel edits — `useAutoScroll` is feature-internal and must not
be exported. The feature barrel (`index.ts`) stays unchanged.

## 9. Tests

| Test file | New / modified | Scenarios |
|-----------|---------------|-----------|
| `frontend_dashboard/src/features/chats-conversation/model/useAutoScroll.test.ts` | new | 5 scenarios via `renderHook` + JSDOM scroll mock |

> JSDOM mock pattern (from refinement §12): define `scrollTop` as a settable
> property and mock `scrollHeight` / `clientHeight` on `containerRef.current`
> using `Object.defineProperty` before each assertion.

Test name list:

- `useAutoScroll — initial render sets scrollTop to scrollHeight (AC-4)`
- `useAutoScroll — itemCount increase while at bottom scrolls to bottom (AC-1)`
- `useAutoScroll — itemCount increase after scroll-up does NOT change scrollTop (AC-2)`
- `useAutoScroll — scroll-to-bottom re-activates sticky; next itemCount increase scrolls (AC-3)`
- `useAutoScroll — resetKey change resets isAtBottom and scrolls even when previously scrolled up (AC-4 + switch)`

## 10. Verification commands

All commands run from repo root; `cd frontend_dashboard &&` prefix is mandatory
(see project-context.md §Command conventions).

```bash
# 1. Type-check after creating hook
cd frontend_dashboard && npx tsc -b

# 2. Run hook tests
cd frontend_dashboard && npm test -- features/chats-conversation/model

# 3. Full test suite — no regressions
cd frontend_dashboard && npm test

# 4. Production build
cd frontend_dashboard && npm run build
```

FSD compliance greps (must return empty / "no rogue fetch"):

```bash
cd frontend_dashboard

# No rogue fetch in features or pages
grep -rEn "fetch\(" src/features src/pages src/app | grep -v "// allowed:" || echo "no rogue fetch"

# No deep imports of feature internals from outside
grep -rEn "from ['\"]@/features/[^'\"]+/(ui|model)/" src/features || echo "no deep imports"

# No cross-feature imports
grep -rEn "from ['\"]@/features/" src/features \
  | grep -vE "^src/features/([a-z-]+)/[^:]+:.*from ['\"]@/features/\1" || echo "no cross-feature"

# No useState + useEffect + fetch combo
grep -rEn "useState.*useEffect.*fetch" src/features src/pages || echo "no manual fetching"
```

## 11. Definition of Done

- [ ] `useAutoScroll.ts` created at `frontend_dashboard/src/features/chats-conversation/model/useAutoScroll.ts` with effects in order A → B → C.
- [ ] `ChatsConversation.tsx` edited: `useAutoScroll` imported from `../model/useAutoScroll`; called with `(messages.length, chatId)`; `containerRef` attached to `div.msgs`.
- [ ] Feature barrel (`index.ts`) unchanged — `useAutoScroll` NOT exported.
- [ ] `useAutoScroll.test.ts` created with all 5 test scenarios passing.
- [ ] `cd frontend_dashboard && npx tsc -b` exits 0.
- [ ] `cd frontend_dashboard && npm test -- features/chats-conversation/model` exits 0.
- [ ] `cd frontend_dashboard && npm test` exits 0 (no regressions).
- [ ] `cd frontend_dashboard && npm run build` exits 0.
- [ ] All 4 FSD compliance greps return empty (or "no rogue fetch" echo).
- [ ] Page mount in Dashboard.tsx NOT modified (no edit needed per §6).
- [ ] No new Tailwind tokens (no edit to `index.css` per §7).
- [ ] FSD rules check in §12 confirmed.

## 12. FSD rules check

- **Import rules (layering):** applies — `model/useAutoScroll.ts` imports only React (external). `ui/ChatsConversation.tsx` imports from `../model/useAutoScroll` (same feature, allowed). No upward imports. ✓
- **Barrel-only public API:** applies — `useAutoScroll` is NOT added to the feature barrel. External consumers (none currently) must not import it directly. ✓
- **Zod at HTTP boundary:** not applicable — no new HTTP call introduced.
- **TanStack Query for server data:** applies — messages continue to arrive via `useChatMessages` (TanStack Query). `useAutoScroll` reads only `messages.length` as a render-cycle integer; no `useState` for server data. ✓
- **No cross-feature imports:** applies — `useAutoScroll` lives in the same feature. No other feature is imported. ✓
- **No deep imports:** applies — `useAutoScroll` is imported only from within the same feature folder. If promoted to shared in the future, it must go through `shared/hooks/` barrel. ✓
- **No fetch() in components/pages:** applies — no `fetch()` introduced anywhere in this task. ✓
- **Tailwind token naming:** not applicable — no new tokens added.
- **JSX files use .tsx:** applies — `useAutoScroll.ts` is a pure hook (no JSX), correctly `.ts`. `ChatsConversation.tsx` already `.tsx`. ✓
- **No useState for server data (anti-pattern #2):** applies — `isAtBottomRef` uses `useRef`, not `useState`, to avoid re-rendering the message list on every scroll event. ✓
- **Promote to shared only when 2+ consumers (anti-pattern #11):** applies — `useAutoScroll` stays in `model/`; no second consumer exists. ✓

## 13. Open questions / risks

- **Effect ordering (A before B) — CRITICAL:** when `chatId` changes, Effects A and B may both fire. Effect A must set `isAtBottomRef.current = true` before Effect B reads it. React runs effects in declaration order — implementer must declare Effect A as the first `useEffect` call. Do not re-order. (From refinement §12.)

- **JSDOM scroll mocking:** JSDOM does not implement `scrollTop`/`scrollHeight`/`clientHeight`. Tests must mock `containerRef.current` with settable properties. Recommended pattern: `Object.defineProperty(containerRef.current, 'scrollHeight', { value: 500 })` plus direct assignment for `scrollTop`. Verify behavior in CI — mark as "verify" if Vitest JSDOM version differs.

- **Pre-existing `key={i}` (index key) in message list (`ChatsConversation.tsx:77`):** out of scope for this HU. Do not fix here; raise a separate HU.

- **Resize / font-load reflow:** if bubble content causes layout reflow after initial paint (image load, font swap), `scrollHeight` read in Effect B may be stale. Edge case not covered; defer to follow-up if reported in production. (From refinement §12.)

- **Backend dependency:** none.
</content>
</invoke>