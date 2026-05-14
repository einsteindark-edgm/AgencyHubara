Tech refinement (frontend) — Scroll automático al mensaje más reciente en vista de conversación

HU id: HU-20260514-005639-scroll-automatico-al-mensaje-mas-recient
Source: $ARTIFACTS_DIR/hu-original.md
Target frontend: frontend_dashboard (cwd: /Users/edgm/Documents/Projects/AgencyHubara/frontend_dashboard)
Layout status: FSD in place
Refiner: frontend-tech-refiner-archon
Date: 2026-05-13
Iteration: 1
requires_backend_change: false

---

## 1. Scope

**Summary:** Replace the always-scroll pattern in `ChatsConversation` with a smart auto-scroll that preserves the operator's reading position when scrolled up.

**Acceptance criteria:**

- Given the panel is at the bottom, when a new message arrives (from client or agent), then the panel scrolls automatically to the new message without operator interaction.
- Given the operator has scrolled up (position ≠ bottom, threshold > 50 px from bottom), when a new message arrives, then the panel does NOT auto-scroll and the operator's reading position is preserved.
- Given the operator has scrolled up and new messages have accumulated, when the operator manually scrolls to the bottom of the panel, then auto-scroll mode re-activates and subsequent new messages will scroll the panel automatically.
- Given the operator opens a conversation with existing messages, when the panel finishes rendering, then the scroll position is set to the most recent message (instant, no visible animation).
- Given TanStack Query polls and returns the same message list (same length), when the poll completes, then no scroll occurs and no perceptible re-render happens.

**Out of scope:**

- Visual notifications (badge, toast) or audio alerts for new messages.
- Marking messages as read / "seen" indicators based on scroll position.
- Paginated history loading (messages before the initial range).
- Scroll-to-specific-message (by search or reference).
- Multi-panel behavior (simultaneous conversations).

---

## 2. Page(s) affected

**Decision:** no page change.

**Justification:** The behavior is fully internal to `features/chats-conversation`. `Dashboard.tsx` already mounts `<ChatsConversation chatId={selectedChatId} />` at line 168 — no prop change required.

**Cross-feature state added/lifted:** none.

---

## 3. Entities affected/created

No entity change. `useChatMessages(chatId)` at `frontend_dashboard/src/entities/chat/api.ts:161` already returns `ChatMessageItem[]`. No new HTTP call is needed. The scroll trigger reads `messages.length` only.

---

## 4. Features affected/created

### `features/chats-conversation/` — extended

| File | Status | Change |
|---|---|---|
| `model/useAutoScroll.ts` | **NEW** | Local-state hook implementing smart auto-scroll logic |
| `ui/ChatsConversation.tsx` | **EDIT** | Import and call `useAutoScroll`; attach `containerRef` to `div.msgs` |

---

### `model/useAutoScroll.ts` — hook contract

```typescript
// pseudo
export function useAutoScroll(
  itemCount: number,      // messages.length — the only scroll trigger
  resetKey: string | null // chatId — forces jump to bottom on conversation switch
): React.RefObject<HTMLDivElement>
```

**Internal state:**

- `containerRef` — `useRef<HTMLDivElement>(null)` attached to `div.msgs`.
- `isAtBottomRef` — `useRef<boolean>(true)`. Uses a ref (NOT `useState`) to avoid re-rendering the message list on every scroll event (anti-pattern #2).

**Effect A** — dep `[resetKey]`: resets `isAtBottomRef.current = true`, then sets `containerRef.current.scrollTop = containerRef.current.scrollHeight` (instant jump — handles AC#4). Fires when the operator switches to a different conversation.

**Effect B** — dep `[itemCount]`: if `isAtBottomRef.current === true`, sets `containerRef.current.scrollTop = containerRef.current.scrollHeight` (handles AC#1 and AC#3 re-activation). Fires when a new message arrives. Same-length polls produce no effect (AC#5).

**Effect C** — dep `[]` (mounts once): attaches a `{ passive: true }` `"scroll"` listener on `containerRef.current`. Computes `atBottom = scrollHeight − scrollTop − clientHeight < 50` and writes it to `isAtBottomRef.current`. Removes the listener on cleanup (handles AC#2 and AC#3 detection).

**Declaration order inside the hook must be A → B → C** so that when `resetKey` changes, Effect A sets `isAtBottomRef.current = true` before Effect B reads it (React runs effects in declaration order).

---

### `ui/ChatsConversation.tsx` — changes

Current `div.msgs` block (lines 74–83):

```tsx
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

After edit:

```tsx
// pseudo — add at top of ChatsConversation body:
const containerRef = useAutoScroll(messages.length, chatId);

// ...
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

**Props shape:** no change — `chatId` is already available in the component scope.

**Entity hooks consumed:** `useChatMessages(chatId)` (unchanged).

**Local state hooks created:** `useAutoScroll` (new, lives in `model/`).

---

## 5. Shared primitives

No new shared primitives. The hook is feature-internal (single consumer). Per anti-pattern #11: promote to `shared/hooks/` only when a second feature needs it.

No new generic UI components needed.

---

## 6. Backend contract dependencies

| Endpoint | Status | Cited backend file | Frontend schema |
|---|---|---|---|
| `GET /api/dashboard/sessions/:id` | exists, no change | `hubara_agency/src/dashboard/api.py` (existing) | `sessionDetailsSchema` (existing in `entities/session/contracts.ts`) |

**Blocked work items:** none.

**Behavior verification (Step 1.5):** Not triggered — the HU is a UI scrolling behavior improvement, not a new data visualization. The messages are already rendered in `div.msgs` via `useChatMessages(chatId)`. No new backend data required.

---

## 7. Cross-feature state

No cross-feature state. `isAtBottomRef` and `containerRef` are local to `ChatsConversation` via `useAutoScroll`.

---

## 8. Tailwind token deltas

No new tokens. `div.msgs` already has `overflow-y: auto` confirmed at `frontend_dashboard/src/index.css:468`. No scrollbar styling change needed.

---

## 9. App-layer wiring

No app-layer change. No new provider. `main.tsx` unchanged.

---

## 10. Composition wiring

`ChatsConversation` is already mounted in `frontend_dashboard/src/pages/Dashboard.tsx:168`:

```tsx
<ChatsConversation chatId={selectedChatId} />
```

No props change. No page edits required.

| Feature | Mount file | JSX location | Props passed |
|---|---|---|---|
| `ChatsConversation` | `pages/Dashboard.tsx` | line 168, inside `ChatsSection` | `chatId` (unchanged) |

---

## 11. Hard rules check

1. **Import rules (layering):** applies — `model/useAutoScroll.ts` imports only React (external lib). `ui/ChatsConversation.tsx` imports from its own `model/` (same feature). No upward cross-layer imports. ✓
2. **Barrel-only public API:** applies — `features/chats-conversation/index.ts` exports only `ChatsConversation`. `useAutoScroll` is feature-internal, not barrel-exported. ✓
3. **Zod at HTTP boundary:** not applicable — no new HTTP call.
4. **TanStack Query for server data:** applies — `messages` still come from `useChatMessages` (TanStack Query cache). `useAutoScroll` only reads `.length` as a render-cycle trigger; no `useState` for server data. ✓
5. **No cross-feature imports:** applies — `useAutoScroll` lives in the same feature. No other feature is imported. ✓
6. **No deep imports:** applies — `useAutoScroll` is not imported by external consumers; if it were promoted to shared, it would go through a barrel. ✓
7. **No fetch() in components/pages:** applies — no `fetch()` call introduced anywhere. ✓
8. **Tailwind token naming:** not applicable — no new tokens.
9. **JSX files use .tsx:** applies — `useAutoScroll.ts` is a pure hook with no JSX, correctly `.ts`. `ChatsConversation.tsx` already `.tsx`. ✓

---

## 12. Risks / open questions

- **Effect ordering (A before B):** when `chatId` changes, Effects A and B may both fire (if the new conversation has a different message count). Effect A must set `isAtBottomRef.current = true` before Effect B reads it. React runs effects in declaration order within a component/hook — declare Effect A before Effect B. Implementation must respect this order. Recommended default: always keep Effect A as the first `useEffect` in `useAutoScroll`.

- **JSDOM scroll mocking in tests:** JSDOM does not implement `scrollTop`/`scrollHeight`/`clientHeight` natively. Tests must mock `containerRef.current` with an object that has settable `scrollTop`, readable `scrollHeight` and `clientHeight`. Use `Object.defineProperty` or assign a plain object with the needed properties. Mark as "verify" if test environment behavior differs.

- **`key={i}` (index key) in message list:** pre-existing in `ChatsConversation.tsx:77`. If messages are ever reordered or spliced, React will reconcile incorrectly. This is out of scope for this HU — raise a separate HU. Do not bundle here.

- **Resize / font-load reflow:** if bubble content causes layout reflow after the initial paint (e.g. image load, font swap), `scrollHeight` read in Effect B may be stale. Edge case not covered by the HU; defer to a follow-up if reported in production.

- **Backend dependency:** none.
- **Defer to follow-up design doc:** none.
- **Pre-existing FSD violation in touched code:** `key={i}` (index key). Flag only — not bundled into this HU.

---

## 13. Tests

| Test file | Type | Asserts |
|---|---|---|
| `frontend_dashboard/src/features/chats-conversation/model/useAutoScroll.test.ts` | hook — `renderHook` + JSDOM mock | (1) Initial render → `containerRef.current.scrollTop` set to `scrollHeight` value; (2) `itemCount` increases while `isAtBottom` is true → scrolls to bottom; (3) `itemCount` increases after simulated scroll-up → does NOT change `scrollTop`; (4) Scroll-up, then simulate scroll-to-bottom event → `isAtBottomRef` flips; next `itemCount` increase → scrolls; (5) `resetKey` changes (new `chatId`) → `isAtBottomRef` reset to `true` and instant scroll even if was previously scrolled up |

---

## 14. Implementation order (suggested)

1. **Create** `frontend_dashboard/src/features/chats-conversation/model/useAutoScroll.ts` with effects in order A → B → C. Verify: `cd frontend_dashboard && npx tsc -b`.
2. **Edit** `frontend_dashboard/src/features/chats-conversation/ui/ChatsConversation.tsx`: import `useAutoScroll`, call with `(messages.length, chatId)`, attach `containerRef` to `div.msgs`. Verify: `cd frontend_dashboard && npx tsc -b`.
3. **Write** `frontend_dashboard/src/features/chats-conversation/model/useAutoScroll.test.ts` covering the 5 asserts above. Run: `cd frontend_dashboard && npm test -- features/chats-conversation/model`.
4. **Run FSD compliance greps** (from `project-context.md`) — expect empty output for all four checks.
5. **Run** `cd frontend_dashboard && npm run build` — confirm no errors.
