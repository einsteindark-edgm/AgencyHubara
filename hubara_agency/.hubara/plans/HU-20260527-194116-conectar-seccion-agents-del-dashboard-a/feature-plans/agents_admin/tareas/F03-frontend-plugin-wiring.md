# Task F03 — Frontend plugin: AgentsSection + AgentsPrompts + null-safe consumers

- Slug: frontend-plugin-wiring
- HU id: HU-20260527-194116-conectar-seccion-agents-del-dashboard-a
- Plugin id: agents_admin
- Plugin template: B (migración A → B)
- Refinement source: artifacts/runs/b9caeb73e09972e8688c06b65b13c134/hu-refinada.md
- Planner: hubara-feature-planner-archon
- Date: 2026-05-27
- Iteration: 1
- Estimated LOC: 55 (sin tests: ~35)
- Risk: low

---

## §1. Context

Delivers acceptance criterion(s) (verbatim from refinement §1):
- **AC-1:** Given que el plugin chats tiene `agentic: true` en su plugin.yaml y los sub-agentes sales y remarketing tienen archivos en agent/<name>/workspace/*.md, when el frontend carga la sección Agents, then muestra exactamente esos dos agentes (sales y remarketing) en la lista, con nombre y rol derivados del workspace.
- **AC-2:** Given un operador selecciona el agente sales en la lista, when se abre el panel de prompts, then el contenido de cada sección (Identity, Soul, Tools, Agents, Users) refleja el texto real de los archivos IDENTITY.md, SOUL.md, TOOLS.md, AGENTS.md, USER.md del workspace de sales. También trae los skills workspace/skills/*/skill.md.

Refinement sections informaron esta task: §3.3.4, §3.3.5, §3.3.8, §9, §12, §15.

Code anchors del refinement:
- Pattern: `AgentsSection` internaliza state at §3.3.5 — bug fix pre-existente
- Pattern: `AgentsPrompts` usa `agent.workspace[s.key]` en lugar de `personality.prompts[s.key]` at §3.3.4
- File to modify: `frontend_dashboard/src/plugins/agents_admin/frontend/AgentsSection.tsx`
- File to modify: `frontend_dashboard/src/plugins/agents_admin/frontend/features/agents-prompts/ui/AgentsPrompts.tsx`
- File to modify (null-safe — discovery en exploration): `frontend_dashboard/src/plugins/agents_admin/frontend/features/agents-list/ui/AgentsList.tsx`
- File to modify (null-safe): `frontend_dashboard/src/plugins/agents_admin/frontend/features/agents-inspector/ui/AgentsInspector.tsx`
- File to create: `frontend_dashboard/e2e/agents/agents-list.spec.ts`

Assumptions del refinement §15 que afectan esta task:
- A5: `capabilities: []` en AgentsInspector — panel de Capacidades queda vacío, no crashea | reversibility: alta
- A6: `calls: null` → `AgentsList.tsx` necesita null-safe en render: `agent.calls?.toLocaleString() ?? "—"` | reversibility: alta

**BUG PRE-EXISTENTE corregido en esta task:**
`AgentsSection.tsx` declara `selectedAgentId: string` y `setSelectedAgentId` como required props,
pero el shell los pasa como `undefined` vía `ComponentType<any>` → click en cualquier agente causa crash.
Esta task corrige internalizando el estado (ver snippet §5).

---

## §2. Dependencies

- depends_on: ["F01", "F02"]
- blocks: []
- Inherits from upstream:
  - De F02: `useAgents()` hook disponible, `Agent.workspace: WorkspaceContent` tipado, `WorkspaceContent` keys definidos, `Personality*` eliminados.
  - De F01: endpoint `GET /api/agents_admin` disponible para E2E tests.
- Cross-plugin dependency: none (todos los files son internos al plugin agents_admin)
- Backend dependency: `GET /api/agents_admin` (F01) para Playwright E2E.

---

## §3. Files affected

| Path | Acción | Rol | LOC budget |
|---|---|---|---|
| `frontend_dashboard/src/plugins/agents_admin/frontend/AgentsSection.tsx` | modify | internalizar state + conectar useAgents() | +10 |
| `frontend_dashboard/src/plugins/agents_admin/frontend/features/agents-prompts/ui/AgentsPrompts.tsx` | modify | cambiar fuente de datos: usePersonalities → agent.workspace[s.key] + render skills | +5 |
| `frontend_dashboard/src/plugins/agents_admin/frontend/features/agents-list/ui/AgentsList.tsx` | modify | null-safe: `agent.calls?.toLocaleString() ?? "—"` (idem csat) | +3 |
| `frontend_dashboard/src/plugins/agents_admin/frontend/features/agents-inspector/ui/AgentsInspector.tsx` | modify | confirmar que `capabilities: []` no crashea `.map()` (puede ser no-op) | +2 |
| `frontend_dashboard/e2e/agents/agents-list.spec.ts` | new | Playwright E2E: operator ve agentes reales en sección Agents | ~35 |

---

## §4. Boundary DTOs (R-JSON)

N/A — esta task solo modifica componentes React. Todos los tipos vienen de `entities/agent` (F02).

---

## §5. Snippets canónicos

```typescript
// canonical — AgentsSection.tsx (state internalizado + useAgents)
import { useState } from "react";
import { useAgents } from "@/entities/agent";
import { AgentsList } from "./features/agents-list";
import { AgentsPrompts } from "./features/agents-prompts";
import { AgentsInspector } from "./features/agents-inspector";

export interface AgentsSectionProps {
  showSidebar: boolean;
  showInspector: boolean;
}

export function AgentsSection({ showSidebar, showInspector }: AgentsSectionProps) {
  const { data: agents = [] } = useAgents();
  const [selectedId, setSelectedId] = useState<string>("");
  const activeId = selectedId || (agents[0]?.id ?? "");
  const activeAgent = agents.find(a => a.id === activeId) ?? agents[0];

  return (
    <>
      {showSidebar && (
        <aside className="sidebar">
          <AgentsList
            agents={agents}
            selectedId={activeId}
            onSelect={setSelectedId}
          />
        </aside>
      )}
      <main className="main">
        {activeAgent && <AgentsPrompts agent={activeAgent} />}
      </main>
      {showInspector && activeAgent && (
        <aside className="inspector">
          <AgentsInspector agent={activeAgent} />
        </aside>
      )}
    </>
  );
}

export default AgentsSection;
// ELIMINAR: selectedAgentId: string, setSelectedAgentId como props
```

```typescript
// canonical — AgentsPrompts.tsx diff canónico
// ANTES:
//   const { data: personalities = [] } = usePersonalities();
//   const personality = personalities.find(p => p.key === agent.personality);
//   text = personality?.prompts[s.key] ?? ""
//
// DESPUÉS (reemplazar esas 3 líneas):
//   text = agent.workspace[s.key as keyof typeof agent.workspace] as string ?? ""
//   // s.key es uno de: "identity" | "soul" | "tools" | "agents" | "users"
//
// AGREGAR después del bloque de PROMPT_SECTIONS.map(...):
//   {agent.workspace.skills.map(skill => (
//     <div key={skill.name} className="prompt-section">
//       <div className="ps-head">{skill.name}</div>
//       <div className="prompt-view">{skill.content}</div>
//     </div>
//   ))}
//
// ELIMINAR: import { usePersonalities } from "@/entities/agent";
```

```typescript
// canonical — AgentsList.tsx null-safe fix
// Buscar render de agent.calls y agent.csat y aplicar:
//   agent.calls.toLocaleString()  →  agent.calls?.toLocaleString() ?? "—"
//   agent.csat.toFixed(1)         →  agent.csat?.toFixed(1) ?? "—"
//   (o variante equivalente según el JSX existente)
```

```typescript
// canonical — e2e/agents/agents-list.spec.ts
import { expect, test } from "@playwright/test";

test.describe("agents-section", () => {
  test("operator sees agents list from real backend", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("tab", { name: /Agents/i }).click();
    // AC-1: al menos 2 agentes visibles
    await expect(page.getByRole("list")).toBeVisible();
    await expect(page.getByText(/sales|Sales/i).first()).toBeVisible();
    await expect(page.getByText(/remarketing|Remarketing/i).first()).toBeVisible();
  });

  test("operator selects agent and sees workspace prompts", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("tab", { name: /Agents/i }).click();
    await page.getByText(/sales|Sales/i).first().click();
    // AC-2: el panel de prompts muestra contenido real (no vacío)
    await expect(page.locator(".prompt-section").first()).toBeVisible();
    await expect(page.locator(".prompt-view").first()).not.toBeEmpty();
  });

  test("catalog agent not shown in agents list", async ({ page }) => {
    // AC-3: solo plugins con agentic=true aparecen
    await page.goto("/");
    await page.getByRole("tab", { name: /Agents/i }).click();
    await expect(page.getByText(/catalog/i)).not.toBeVisible();
  });
});
```

---

## §6. Workspace deltas

N/A — `agents_admin` no tiene workspace propio.

---

## §7. Composition wiring

N/A — esta task solo edita componentes React internos del plugin.

---

## §8. Worker registration

N/A — Template B.

---

## §9. Tests

| Test file | New/modify | Scenarios |
|---|---|---|
| `frontend_dashboard/e2e/agents/agents-list.spec.ts` | new | operator ve lista, selecciona agente, catalog excluido |

Test name list:
- `e2e/agents/agents-list.spec.ts::agents-section > operator sees agents list from real backend`
- `e2e/agents/agents-list.spec.ts::agents-section > operator selects agent and sees workspace prompts`
- `e2e/agents/agents-list.spec.ts::agents-section > catalog agent not shown in agents list`

**Backend strategy E2E:** FastAPI en background con `ENABLED_PLUGINS=agents_admin,chats`. El Playwright runner debe levantar el backend antes del test o el pipeline lo hace. Ver §10.

---

## §10. Verification commands

```bash
# Type check (confirma null-safe y que usePersonalities fue eliminado)
cd frontend_dashboard && npx tsc -b

# Architecture gate
cd frontend_dashboard && npm run test:arch

# Build
cd frontend_dashboard && npm run build

# Unit tests del entity (F02 scope — correr para regresión)
cd frontend_dashboard && npm test -- agent/api

# Playwright E2E (necesita backend en background)
# Terminal 1:
cd hubara_agency && ENABLED_PLUGINS=agents_admin,chats UVICORN_PORT=8765 uv run python run_api.py &
# Terminal 2:
cd frontend_dashboard && VITE_API_URL="http://127.0.0.1:8765" npx playwright test e2e/agents/

# Suite completa frontend (no regresión)
cd frontend_dashboard && npm test
```

---

## §11. Definition of Done

- [ ] `AgentsSection.tsx` internaliza `selectedId` con `useState` — no recibe `selectedAgentId`/`setSelectedAgentId` como props.
- [ ] `AgentsSection.tsx` usa `useAgents()` y renderiza con `agents[0]` como default cuando no hay selección.
- [ ] `AgentsPrompts.tsx` usa `agent.workspace[s.key]` — no llama a `usePersonalities()`.
- [ ] `AgentsPrompts.tsx` renderiza skills: `agent.workspace.skills.map(skill => ...)`.
- [ ] `AgentsList.tsx` null-safe en `calls` y `csat`: `?.toLocaleString() ?? "—"`.
- [ ] `AgentsInspector.tsx` tolera `capabilities: []` sin crash.
- [ ] `npx tsc -b` exit 0 — ningún type error en los 4 componentes editados.
- [ ] `npm run test:arch` exit 0.
- [ ] `npm run build` exit 0.
- [ ] `npx playwright test e2e/agents/` exit 0 — 3 tests verdes (AC-1, AC-2, AC-3).
- [ ] `npm test` exit 0 — no regresión en suite existente.
- [ ] Ningún import de `usePersonalities`, `Personality`, `PersonalityKey` permanece en `agents_admin/**`.
- [ ] Seleccionar un agente en la UI NO causa crash (bug pre-existente corregido).

---

## §12. Hard rules check

- **R-DET:** N/A — no hay Temporal.
- **R-JSON:** N/A.
- **R-STATELESS:** N/A.
- **R-HEARTBEAT:** N/A.
- **R-DIP:** N/A en backend. Frontend: `AgentsSection.tsx` importa de `@/entities/agent` (entity → plugin: FSD slice descendente, PROHIBIDO). Corrección: el import debe ser desde la perspectiva FSD. `entities` está en una capa inferior a `plugins`. En FSD: `plugins` puede importar de `entities` (correcto). `AgentsSection.tsx` está en `plugins/agents_admin/frontend/` → importar `useAgents` de `@/entities/agent` está permitido. ✓
- **FSD layering:** Applies. `plugins/agents_admin/frontend/AgentsSection.tsx` importa de `@/entities/agent` (lower layer — OK). Componentes internos del plugin se importan entre features del mismo plugin (cross-feature OK dentro de plugin). NO hay import de `@plugins/chats/*`. `npm run test:arch` debe pasar.
- **Manifest = SSoT:** N/A para esta task (no toca manifests).

---

## §13. Open questions / risks

- **Risk: PROMPT_SECTIONS key order / mismatch.** Si `PROMPT_SECTIONS[i].key === "user"` (en lugar de `"users"`), el acceso `agent.workspace["user"]` devuelve `undefined` y la sección queda vacía. Verificar antes de terminar F03 que `PROMPT_SECTIONS` tiene exactamente las keys: `identity`, `soul`, `tools`, `agents`, `users`. Si hay divergencia, corregir en `model.ts` (parte de F02 pero puede fixarse en F03 si F02 ya terminó).
- **Risk: Playwright no puede levantar el backend.** Si el pipeline no provee un mecanismo automático de boot del backend para E2E tests, el implementer debe documentar el skip con `test.skip()` y explicar cómo correr manualmente. No bloquear el merge por esto si los demás gates pasan.
- **Risk: AgentsList.tsx y AgentsInspector.tsx estructura desconocida.** La exploración identificó que `agent.calls` está en el render pero no leyó el código exacto. El implementer lee los archivos antes de modificar para entender el JSX actual y aplicar el mínimo cambio necesario (null-safe, nada más).
- **Confirmed (exploración):** `usePersonalities()` tiene exactamente 1 caller (`AgentsPrompts.tsx`). Safe to remove en F02/F03.

---

## §14. Wiring intents

N/A — esta task no toca archivos spinal. Todos los archivos modificados son propiedad exclusiva del plugin `agents_admin` (en `frontend_dashboard/src/plugins/agents_admin/frontend/`).
