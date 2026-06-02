# Task F02 — Frontend entity: rewrite entities/agent/* con Zod + hook real

- Slug: frontend-entity-rewrite
- HU id: HU-20260527-194116-conectar-seccion-agents-del-dashboard-a
- Plugin id: agents_admin
- Plugin template: B (migración A → B)
- Refinement source: artifacts/runs/b9caeb73e09972e8688c06b65b13c134/hu-refinada.md
- Planner: hubara-feature-planner-archon
- Date: 2026-05-27
- Iteration: 1
- Estimated LOC: 165 (sin tests: ~115)
- Risk: medium (rewrite + eliminación de tipos — verificar consumers antes)

---

## §1. Context

Delivers acceptance criterion(s) (verbatim from refinement §1):
- **AC-4:** Given que los archivos .md del workspace cambian en el servidor (nuevo deploy), when el operador recarga la sección Agents, then el contenido actualizado se muestra (sin `staleTime: Infinity` en el frontend).

Refinement sections informaron esta task: §3.3, §3.3.1, §3.3.2, §3.3.3, §3.3.6, §3.3.7, §9, §15.

Code anchors del refinement:
- Pattern: `agentDtoSchema`, `agentListDtoSchema`, `useAgents()` at §3.3.2, §3.3.3
- File to modify: `frontend_dashboard/src/entities/agent/model.ts` (modify — delete Personality types, update Agent)
- File to modify: `frontend_dashboard/src/entities/agent/api.ts` (rewrite — mock → real fetch)
- File to modify: `frontend_dashboard/src/entities/agent/keys.ts` (delete personalities key)
- File to modify: `frontend_dashboard/src/entities/agent/index.ts` (update barrel exports)
- File to create: `frontend_dashboard/src/entities/agent/contracts.ts` (Zod schemas)
- File to create: `frontend_dashboard/src/entities/agent/api.test.tsx` (vitest unit test)

Assumptions del refinement §15 que afectan esta task:
- A1: Icon/color map inline `{sales→bolt/blue, remarketing→refresh/orange}` | reversibility: alta
- A3: `staleTime` omitido (default 0 → refetch on mount) — satisface AC-4 | reversibility: alta
- A7/A8: `Personality`/`PersonalityKey`/`usePersonalities()` eliminados directo — exploración confirmó ÚNICO caller es `AgentsPrompts.tsx` | reversibility: git revert

**PRE-CONDICIÓN:** Antes de eliminar `Personality`/`PersonalityKey`/`usePersonalities()`, grep para confirmar que NO hay otros consumers fuera de `AgentsPrompts.tsx`. La exploración confirmó que es el único — el implementer debe verificar con:
```bash
cd frontend_dashboard && grep -r "usePersonalities\|PersonalityKey\|Personality" src/ --include="*.ts" --include="*.tsx"
```
Si aparece algún consumer distinto de `agents_admin/**`, STOP — no eliminar, reportar a planner.

---

## §2. Dependencies

- depends_on: []
- blocks: ["F03"]
- Inherits from upstream: nada (es foundation task)
- Cross-plugin dependency: none (entities/agent es owned por agents_admin en esta HU)
- Backend dependency: `GET /api/agents_admin` debe existir para E2E tests de F02, pero los unit tests de vitest mockean el apiClient — F02 puede correr sin F01 completo.

---

## §3. Files affected

| Path | Acción | Rol | LOC budget |
|---|---|---|---|
| `frontend_dashboard/src/entities/agent/contracts.ts` | new | Zod schemas: skillContentSchema, workspaceContentSchema, agentDtoSchema, agentListDtoSchema | ~45 |
| `frontend_dashboard/src/entities/agent/model.ts` | modify | agregar SkillContent + WorkspaceContent; update Agent; eliminar Personality/PersonalityKey/PersonalityPrompt | net ~-20 (reescritura parcial) |
| `frontend_dashboard/src/entities/agent/api.ts` | modify (rewrite) | eliminar AGENTS mock + usePersonalities; agregar useAgents() real con useQuery + Zod | net ~-45 → ~50 lines final |
| `frontend_dashboard/src/entities/agent/keys.ts` | modify | eliminar `personalities()` key | -3 |
| `frontend_dashboard/src/entities/agent/index.ts` | modify | actualizar barrel: agregar contratos, eliminar Personality/usePersonalities | ±8 |
| `frontend_dashboard/src/entities/agent/api.test.tsx` | new | vitest unit test del hook useAgents | ~50 |

---

## §4. Boundary DTOs (R-JSON)

N/A — Template B, sin Temporal. Los DTOs del frontend son Zod schemas (runtime validation) no dataclasses Python.

```typescript
// canonical shape — contracts.ts
export const skillContentSchema = z.object({
  name: z.string(),
  content: z.string(),
});

export const workspaceContentSchema = z.object({
  identity: z.string(),
  soul: z.string(),
  tools: z.string(),
  agents: z.string(),
  users: z.string(),              // ← key "users" (backend mapea USER.md → "users")
  skills: z.array(skillContentSchema).default([]),
});

export const agentDtoSchema = z.object({
  id: z.string(),
  plugin_id: z.string(),
  worker_name: z.string(),
  name: z.string(),
  role: z.string().default(""),
  workspace: workspaceContentSchema,
});

export const agentListDtoSchema = z.array(agentDtoSchema);
export type AgentDto = z.infer<typeof agentDtoSchema>;
```

---

## §5. Snippets canónicos

```typescript
// canonical — entities/agent/model.ts (NUEVOS tipos — agregar al inicio)
export interface SkillContent {
  name: string;
  content: string;
}

export interface WorkspaceContent {
  identity: string;
  soul: string;
  tools: string;
  agents: string;
  users: string;              // ← key "users" (no "user") — matchea backend
  skills: SkillContent[];
}

// Agent ACTUALIZADO (workspace reemplaza personality; calls/csat → nullable)
export interface Agent {
  id: string;               // "chats:sales"
  plugin_id: string;
  worker_name: string;
  name: string;
  role: string;
  workspace: WorkspaceContent;
  model: string;            // default: "deepseek-chat"
  icon: IconName;
  color: AgentColor;
  status: AgentStatus;
  calls: number | null;     // null hasta HU observabilidad
  csat: number | null;
  category: string;
  capabilities: Capability[];
}
// ELIMINAR: Personality, PersonalityKey, PersonalityPrompt (solo después de grep confirm)
```

```typescript
// canonical — entities/agent/api.ts (rewrite completo)
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/shared/api/client";
import { agentKeys } from "./keys";
import { agentListDtoSchema } from "./contracts";
import type { Agent } from "./model";

const _DEFAULTS: Record<string, { icon: string; color: string }> = {
  sales:       { icon: "bolt",    color: "blue"   },
  remarketing: { icon: "refresh", color: "orange" },
};

export function useAgents() {
  return useQuery({
    queryKey: agentKeys.list(),
    queryFn: async (): Promise<Agent[]> => {
      const raw = await apiClient.get<unknown>("/api/agents_admin");
      const dtos = agentListDtoSchema.parse(raw);
      return dtos.map(dto => ({
        ...dto,
        model: "deepseek-chat",
        icon: (_DEFAULTS[dto.worker_name]?.icon ?? "bot") as Agent["icon"],
        color: (_DEFAULTS[dto.worker_name]?.color ?? "blue") as Agent["color"],
        status: "online" as const,
        calls: null,
        csat: null,
        category: dto.worker_name.charAt(0).toUpperCase() + dto.worker_name.slice(1),
        capabilities: [],
      }));
    },
    // staleTime omitido → default 0 (refetch on mount — AC-4)
  });
}
// ELIMINAR: usePersonalities(), const AGENTS = [...], const PERSONALITIES = [...]
```

```typescript
// canonical — entities/agent/keys.ts (modificación)
export const agentKeys = {
  all: ["agents"] as const,
  list: () => [...agentKeys.all, "list"] as const,
  detail: (id: string) => [...agentKeys.all, "detail", id] as const,
  // ELIMINAR: personalities: () => [...agentKeys.all, "personalities"] as const,
};
```

```typescript
// canonical — entities/agent/index.ts (barrel actualizado)
// AGREGAR:
export type { SkillContent, WorkspaceContent, AgentDto } from "./contracts";
export { agentDtoSchema, agentListDtoSchema } from "./contracts";
// CONSERVAR:
export { PROMPT_SECTIONS } from "./model";
export { useAgents } from "./api";
export type { Agent } from "./model";
// ELIMINAR:
// export { usePersonalities } from "./api";
// export type { Personality, PersonalityKey, PersonalityPrompt } from "./model";
```

---

## §6. Workspace deltas

N/A — esta task modifica entity frontend, no workspace de agente.

---

## §7. Composition wiring

N/A — entity hooks no requieren composition factory. TanStack Query maneja el lifecycle.

---

## §8. Worker registration

N/A — Template B.

---

## §9. Tests

| Test file | New/modify | Scenarios |
|---|---|---|
| `frontend_dashboard/src/entities/agent/api.test.tsx` | new | fetch real + Zod parse, icon/color defaults, staleTime=0 |

Test name list:
- `src/entities/agent/api.test.tsx::test useAgents fetches and maps with Zod`
- `src/entities/agent/api.test.tsx::test useAgents provides default icon/color for known workers`
- `src/entities/agent/api.test.tsx::test useAgents falls back to bot/blue for unknown worker_name`
- `src/entities/agent/api.test.tsx::test useAgents maps calls and csat to null`

**Patrón de test (vitest + TanStack):**
```typescript
// canonical shape — api.test.tsx
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { useAgents } from "./api";

// MSW server para mockear apiClient
const server = setupServer(
  http.get("/api/agents_admin", () =>
    HttpResponse.json([{
      id: "chats:sales",
      plugin_id: "chats",
      worker_name: "sales",
      name: "Sales Agent",
      role: "Ventas",
      workspace: {
        identity: "# Sales\nAyudo a vender.",
        soul: "", tools: "", agents: "", users: "", skills: [],
      },
    }])
  )
);

// test: fetchea y mapea defaults
```

---

## §10. Verification commands

```bash
# Unit tests del hook (vitest)
cd frontend_dashboard && npm test -- agent/api

# Architecture gate (FSD layering — contracts.ts solo importa de zod; api.ts solo de shared/api)
cd frontend_dashboard && npm run test:arch

# Type check
cd frontend_dashboard && npx tsc -b

# Build
cd frontend_dashboard && npm run build

# Backend architecture gate (no toca Python — verificación de completitud)
cd hubara_agency && uv run pytest -m architecture --tb=short
```

---

## §11. Definition of Done

- [ ] `contracts.ts` creado con los 4 schemas Zod (`skillContentSchema`, `workspaceContentSchema`, `agentDtoSchema`, `agentListDtoSchema`) y type `AgentDto`.
- [ ] `model.ts` actualizado: `SkillContent` + `WorkspaceContent` añadidos; `Agent.workspace: WorkspaceContent`; `Agent.calls/csat: number | null`; `Personality`/`PersonalityKey`/`PersonalityPrompt` eliminados (tras grep confirm).
- [ ] `api.ts` reescrito: solo `useAgents()` (sin `usePersonalities()`, sin `AGENTS`, sin `PERSONALITIES`). `staleTime` omitido.
- [ ] `keys.ts` actualizado: `personalities()` eliminado.
- [ ] `index.ts` actualizado: exports nuevos de `contracts.ts`; `usePersonalities` y `Personality*` removidos.
- [ ] `api.test.tsx` con 4 tests, todos `exit 0`.
- [ ] `npm run test:arch` exit 0 (FSD: contracts.ts solo importa zod; api.ts importa de shared/api y ./keys y ./contracts — no cross-plugin).
- [ ] `npx tsc -b` exit 0 (no type errors en entity ni en F02 scope).
- [ ] `npm run build` exit 0.
- [ ] Grep confirmó que `usePersonalities`/`Personality`/`PersonalityKey` no tienen consumers fuera de `agents_admin/**`.

---

## §12. Hard rules check

- **R-DET:** N/A — no hay Temporal.
- **R-JSON:** N/A — no hay boundary activity/workflow. Zod schemas validan en frontend.
- **R-STATELESS:** N/A.
- **R-HEARTBEAT:** N/A.
- **R-DIP:** N/A en backend. Frontend: `entities/agent/api.ts` importa de `@/shared/api/client` (shared → entity: permitido). NO importa de `@plugins/agents_admin/*` ni de otro entity. ✓
- **FSD layering:** Applies. `entities/agent/*` importa solo de `shared/` (zod, apiClient, types propios de la entity). Ninguna feature de ningún plugin importa desde el interior de `entities/agent/` sin pasar por `entities/agent/index.ts`. `npm run test:arch` debe pasar.
- **Manifest = SSoT:** N/A (esta task es puramente entity frontend).

---

## §13. Open questions / risks

- **Risk: key mismatch "users" vs "user".** El backend usa key `"users"` (mapea `USER.md → "users"`). `PROMPT_SECTIONS` en model.ts debe tener `key: "users"` (no `"user"`) en la entrada correspondiente. Si el key no matchea, `agent.workspace[s.key as keyof ...]` devuelve `undefined` y el panel muestra la sección vacía. Verificar antes de que F03 use `PROMPT_SECTIONS.map(s => agent.workspace[s.key])`.
- **Risk: AgentsList.tsx y AgentsInspector.tsx rompen con `calls: null`.** Están fuera del scope de F02 (son archivos del plugin, no del entity) pero TypeScript fallará si el type cambia y no se actualiza. F02 puede ignorar esto — F03 lo corrige. El `tsc -b` en F02 puede fallar si estos archivos usan `agent.calls.toLocaleString()` sin null-check. El implementer debe correr `npx tsc -b` y esperar errores en AgentsList.tsx — documentar el error como "a resolver en F03" (no bloquear F02).
- **Risk: `api.test.tsx` setup de MSW.** Verificar que el proyecto tiene `msw` en devDependencies (`package.json`). Si no está, el implementer agrega solo con `npm install -D msw` (documentar en PR). No tocar `package.json` sin confirmar con operador si requiere ADR.

---

## §14. Wiring intents (spinal files)

```yaml
wiring_intents:
  # entities/agent/* son spinal pero agents_admin es el ÚNICO contributor en esta HU.
  # Los intents se declaran como documentación para futuros mergers.
  
  frontend_dashboard/src/entities/agent/index.ts:
    - kind: ts_barrel
      export_statement: "export type { SkillContent, WorkspaceContent, AgentDto } from './contracts';"
      order_hint: append
    - kind: ts_barrel
      export_statement: "export { agentDtoSchema, agentListDtoSchema } from './contracts';"
      order_hint: append
    # Nota: también se ELIMINAN exports (usePersonalities, Personality*) — operación directa, no intent de merger
    merge_complexity: trivial  # single contributor

  # contracts.ts es nuevo — no hay conflict (cada plugin crea su propio path)
  # model.ts, api.ts, keys.ts: operaciones de modify/rewrite — edición directa (single contributor)
```
