---
name: frontend-feature-sliced
description: Architectural reference for frontend dashboards built on Vite + React 19 + TanStack Query + Tailwind v4 + Zod (canonical example: AgencyHubara/frontend_dashboard). Use whenever the user asks to add, refactor, or review code in a React/TS frontend in this repo, or anywhere they invoke "/frontend-feature-sliced". Enforces the 5-layer Feature-Sliced layout (`app / pages / features / entities / shared`) with strict import rules so multiple agents can ship features in parallel without collisions in `App.tsx` or shared CSS. Triggers — "agrega feature al dashboard", "nuevo componente frontend", "refactor del dashboard", "review frontend", "/frontend-feature-sliced".
metadata: {"frontend": {"always": false}}
---

# Feature-Sliced architecture for parallel-agent frontends

This skill is the source of truth for any React/TypeScript frontend in this repo. It exists because the original `frontend_dashboard` shipped with everything in `components/` + `App.tsx`, and adding a 5th feature would have meant 5 agents stomping on the same files. The layout below makes it possible for N agents to ship N features in parallel — each in its own folder, with zero overlap.

The canonical living implementation is at `frontend_dashboard/src/`. When in doubt, mirror what's already there.

---

## 0. The 5 layers

```
src/
├── app/              # Composition root: providers, router, layout, error boundary
├── pages/            # Thin shells that compose features into screens
├── features/         # Self-contained user-facing capabilities (1 folder = 1 feature)
├── entities/         # Domain models + their data hooks (sessions, messages, users…)
├── shared/           # Cross-feature primitives: api client, ui kit, lib helpers, env
└── test/             # Vitest setup
```

Every file lives in exactly one of these layers. There is no `components/`, no `utils/`, no `lib/` at the root. New folders at `src/` require explicit user approval.

---

## 1. The 4 import rules — non-negotiable

These rules are what enable parallel work. Violating them re-creates the coupling we left behind.

| Layer       | May import from                       | May NOT import from                     |
|-------------|---------------------------------------|-----------------------------------------|
| `app/`      | `pages, features, entities, shared`   | nothing forbidden (it's the root)       |
| `pages/`    | `features, entities, shared`          | `app`, other `pages`                    |
| `features/` | `entities, shared`                    | `app`, `pages`, **other `features/*`**  |
| `entities/` | `shared`, other `entities`            | `app`, `pages`, `features`              |
| `shared/`   | nothing inside `src/`                 | everything above it                     |

**Rule of thumb**: *imports flow downward only*. If `features/foo` needs something from `features/bar`, that something belongs in `entities/` or `shared/`.

Each `features/<x>/` and `entities/<x>/` folder exposes a public API via its `index.ts`. Importing from a deep subpath (`features/session-list/ui/SessionList`) is forbidden — always go through the barrel (`@/features/session-list`).

---

## 2. Folder skeleton per layer

### `entities/<name>/`

Owns a domain concept and its data-fetching. Generic enough to be consumed by multiple features.

```
entities/<name>/
├── model.ts          # Pure TS types — no Zod, no fetching
├── contracts.ts      # Zod schemas for boundary validation
├── keys.ts           # Query key factory (see §4)
├── api.ts            # TanStack Query hooks: useFoo(), useFoos(), useFooStream()
├── filters.ts        # Pure predicates / transforms (optional)
├── index.ts          # Barrel — exports the public surface
└── *.test.ts(x)      # Unit tests for predicates, integration tests for hooks
```

### `features/<name>/`

Owns a single user-visible capability. Renders UI, holds local UI state, consumes entities.

```
features/<name>/
├── ui/
│   ├── <Feature>.tsx       # Root component (only one exported via index.ts)
│   └── <SubComponent>.tsx  # Internal pieces
├── model/
│   ├── use<X>.ts           # Local-state hooks (filters, drafts, modal state…)
│   └── *.ts                # Helpers internal to the feature
├── index.ts                # Barrel — usually exports ONE root component
└── *.test.ts(x)            # Tests
```

### `shared/<area>/`

Generic infra. No knowledge of any domain concept.

```
shared/
├── api/        # apiClient, subscribeSse, ApiError
├── config/     # env vars
├── ui/         # Generic primitives: Button, Modal, Pill (when extracted)
├── hooks/      # Generic hooks: useDebounce, useMediaQuery
└── lib/        # Pure helpers: formatTime, classnames
```

### `pages/<name>.tsx`

Thin shells. Their only job is to compose features. Allowed responsibilities:

- Hold cross-feature coordination state (e.g. `selectedSessionId`).
- Mount global side effects (e.g. `useSessionsStream()`).
- Lay out the page.

Forbidden in pages:
- Direct `apiClient.get(...)` calls — go through entity hooks.
- Feature-specific UI logic — push it down into the feature.

### `app/`

The composition root. `providers/` (QueryProvider, ThemeProvider, ErrorBoundary), `layout/` (AppShell when shared across pages), `router.tsx` when multiple routes appear.

---

## 3. Adding a new feature — recipe

Want to add `features/foo`? Steps in order:

1. **Decide the entity it works on.** If it operates on `session`, `message`, etc., consume the existing entity hooks. If it introduces a new concept, create `entities/<concept>/` first.
2. **Create the feature folder** with the skeleton above.
3. **Write the public component** in `ui/Foo.tsx`. Keep it small — break out subcomponents.
4. **Local state in hooks** under `model/` — never inline in the component if there's filtering/derivation logic. Hooks are testable; component state isn't.
5. **Export only the root** from `index.ts`.
6. **Mount it in a page** (`pages/<X>.tsx`) — this is the only file outside the feature that should change.
7. **Add a test file** alongside the hook or component. Pure logic → unit test. Component → RTL test with a `QueryClient` wrapper.
8. **Run** `npm test && npm run build`. Both must be green.

If your "feature" doesn't fit in a folder by itself — if it can't be added without editing files in another feature — it's not a feature. It's an entity, a shared primitive, or a sign that the boundary is wrong.

---

## 4. Canonical patterns (copy-paste, don't invent)

### TanStack Query keys

```ts
// entities/<x>/keys.ts
export const xKeys = {
  all: ["x"] as const,
  list: () => [...xKeys.all, "list"] as const,
  detail: (id: string) => [...xKeys.all, "detail", id] as const,
} as const;
```

Never hardcode array literals as query keys outside this file. Invalidations target the factory:

```ts
qc.invalidateQueries({ queryKey: xKeys.detail(id) });
```

### Query hook + Zod boundary

```ts
// entities/<x>/api.ts
async function fetchX(id: string): Promise<X> {
  const raw = await apiClient.get<unknown>(`/api/x/${id}`);
  return xSchema.parse(raw); // explodes loud at the boundary
}

export function useX(id: string | null) {
  return useQuery({
    queryKey: xKeys.detail(id ?? ""),
    queryFn: () => fetchX(id!),
    enabled: !!id,
  });
}
```

### Feature component shape

```tsx
// features/<x>/ui/<X>.tsx
export function X({ /* cross-feature props only */ }: Props) {
  const { data, isLoading, isError } = useEntity(id);  // entity hook
  const local = useLocalUiState(data);                  // hook from ./model/

  if (isLoading) return <SkeletonX />;
  if (isError)   return <ErrorX />;
  return <XView data={data} local={local} />;
}
```

### Cross-feature shared modal / overlay

If a modal can be opened from more than one feature, it lives in its own `features/<modal-name>/` and exposes `<Modal open onClose ...props />`. The opening feature owns the open-state.

---

## 5. Tailwind v4 conventions

Tailwind is wired via `@tailwindcss/vite` (no `tailwind.config.js`). Tokens live in `index.css` under `@theme`.

**When to add a token vs. use an arbitrary value:**

- **Add a `--color-*` to `@theme`** when the value is reused 2+ times or represents a semantic role (`bg`, `accent`, `panel-border`). Then use the auto-generated utility (`bg-accent`).
- **Use arbitrary values** (`bg-[#1a2030]`, `w-[340px]`) only when truly one-off and not semantic. Three+ uses → promote to a token.

**Naming**: avoid utility collisions.
- ✅ `--color-fg`, `--color-fg-muted` (becomes `text-fg`, `text-fg-muted`)
- ❌ `--color-text-primary` (becomes `text-text-primary`, ugly)

**Migration from legacy CSS**: each feature owns its conversion. There is no big-bang Tailwind migration PR. When you touch a feature, prefer Tailwind classes for any new markup; convert nearby legacy classes opportunistically. Until a feature is fully converted, the legacy CSS file stays imported.

**Don't add a global stylesheet for a feature.** Feature styling lives in component files (Tailwind classes inline) or a colocated `*.module.css` if absolutely needed.

---

## 6. Testing conventions

- **Framework**: Vitest + Testing Library + jsdom.
- **Setup**: `src/test/setup.ts` enables `@testing-library/jest-dom/vitest` matchers and runs `cleanup()` after each test.
- **Env vars**: declared in `vitest.config.ts → test.env`. Don't read `.env.development` from tests.
- **What to test, in priority order**:
  1. Pure functions (filters, formatters, parsers) — fastest, most valuable.
  2. Custom hooks via `renderHook`.
  3. Components via RTL when behavior is non-trivial (forms, conditional render, interactions).
  4. Integration tests across feature boundaries — only when warranted.
- **Don't test**: trivial render, prop pass-through, third-party library behavior.
- **Mocking fetch** in hook/component tests:
  ```ts
  const fetchMock = vi.fn();
  beforeEach(() => vi.stubGlobal("fetch", fetchMock));
  afterEach(() => { vi.unstubAllGlobals(); fetchMock.mockReset(); });
  ```
- **Testing components that use TanStack Query**: wrap in a fresh `QueryClientProvider` with `retry: false` per test to avoid cross-test cache leakage.

---

## 7. Anti-patterns (do NOT do)

The skill is most useful as a list of things to refuse. If you find yourself doing any of these, stop and rethink.

1. **`fetch(...)` inside a component or page.** All HTTP goes through `entities/<x>/api.ts` via `apiClient`.
2. **`useState` for server data.** Server data goes in TanStack Query. `useState` is for UI state only.
3. **Two components polling the same endpoint.** They must share a hook from `entities/`. TanStack Query dedupes by `queryKey`.
4. **`import X from '../../../something'`.** Use `@/` aliases. If the relative path crosses a layer boundary, the import is illegal anyway.
5. **`import { Foo } from '@/features/bar/ui/Foo'`** (deep import). Always go through the barrel: `@/features/bar`.
6. **Cross-feature imports.** `features/a` cannot import from `features/b`. Lift the shared piece to `entities/` or `shared/`.
7. **Adding a CSS file at `src/`.** Tailwind tokens live in `index.css`. Per-feature styling stays inside the feature folder.
8. **Editing `App.tsx` / `pages/Dashboard.tsx` to change feature behavior.** Pages only orchestrate. Feature changes happen in `features/<x>/`.
9. **Hardcoded API URL.** Use `env.apiUrl` from `shared/config/env.ts`.
10. **Skipping Zod at the boundary.** Every `apiClient.get<unknown>(...)` is followed by `schema.parse(raw)`. The compile-time `<T>` is documentation; Zod is enforcement.
11. **Putting a "shared" helper in `shared/lib/` on speculation.** Start it inside the feature; promote to `shared/` only when the second consumer arrives.
12. **`*/` inside a JSDoc block comment** (e.g. `entities/*/api.ts`). It closes the comment and TypeScript hates it. Use `<x>` placeholders.
13. **Naming a Tailwind token `--color-text-primary`.** Causes `text-text-primary` utility. Use `--color-fg` instead.
14. **Adding a new top-level dep without a reason in the PR description.** Bundle size matters; the migration plan picked TanStack Query/Zod/Tailwind deliberately.

---

## 8. Parallel-agent PR checklist

Before merging a PR, the agent confirms:

- [ ] Changes are confined to **one feature folder** (or one entity, one shared primitive). If it spans more, split the PR.
- [ ] No edits to other `features/*` folders.
- [ ] No new file at `src/` root.
- [ ] No `fetch(`, `EventSource(`, or `XMLHttpRequest(` outside `entities/*/api.ts` or `shared/api/`.
- [ ] All entity DTOs validated with Zod at the HTTP boundary.
- [ ] At least one test added or updated for the touched logic.
- [ ] `npm run build` and `npm test` are both green.
- [ ] No new dep in `package.json` unless mentioned in the PR description.
- [ ] Public surface of the touched feature/entity exported via `index.ts`; nothing imported deeply from outside.

If these check out, the PR is reviewable in isolation and won't conflict with parallel feature work.

---

## 9. Decision tree: where does this code go?

```
Is it a TS type or a Zod schema?
  → entities/<x>/{model,contracts}.ts

Is it an HTTP call or query hook?
  → entities/<x>/api.ts

Is it a generic helper (formatDate, classnames)?
  → shared/lib/

Is it a generic UI primitive (Button, Modal, Pill)?
  → shared/ui/

Is it user-visible behavior owned by one feature?
  → features/<x>/ui/

Is it local state for that feature (filter, draft)?
  → features/<x>/model/

Is it state shared across multiple features in the same page?
  → pages/<page>.tsx (lift to the page) — and reconsider whether the boundary is right.

Is it a global concern (auth provider, theme provider, error boundary)?
  → app/providers/
```

---

## 10. Reference: the canonical implementation

Mirror the existing structure when in doubt:

- `frontend_dashboard/src/entities/session/` — full entity with model + contracts + keys + api + sse + tests.
- `frontend_dashboard/src/entities/message/` — entity that's mostly types + filters (no own endpoint).
- `frontend_dashboard/src/features/session-list/` — feature with `ui/` + `model/` + tests.
- `frontend_dashboard/src/features/session-metadata/` — feature that consumes a *shared* feature (`memory-modal`).
- `frontend_dashboard/src/pages/Dashboard.tsx` — the canonical thin page.
- `frontend_dashboard/src/shared/api/client.ts` — the only place that wraps `fetch`.

If a new contributor reads any of those files first, the pattern propagates by example. That's the goal.
