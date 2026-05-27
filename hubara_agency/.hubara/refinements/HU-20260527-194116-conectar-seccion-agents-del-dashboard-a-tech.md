# HU refinement — Conectar sección Agents del dashboard a datos reales del backend

- HU id: HU-20260527-194116-conectar-seccion-agents-del-dashboard-a
- Source: $ARTIFACTS_DIR/hu-original.md
- Refiner: hubara-tech-refiner-archon
- Date: 2026-05-27
- Iteration: 1

---

## §0. Plugin classification

- **mode:** `multi_plugin`
- **plugins_affected:**
  - id: `agents_admin`
    layers: [api, frontend]
    action: extend  _(template A → B; se suma capa `api:` y se actualiza frontend)_
  - id: `chats`
    layers: []
    action: extend  _(manifest config only: se agrega `agentic: true` al `plugin.yaml`)_
- **shared_files_touched:**
  - path: `frontend_dashboard/src/plugins/_schema/plugin.schema.yaml`
    reason: nuevo campo `agentic: boolean` (opcional, default false)
  - path: `frontend_dashboard/src/entities/agent/model.ts`
    reason: nuevos tipos `SkillContent`, `WorkspaceContent`; `Agent` pierde `personality`; gana `workspace`; `Personality`/`PersonalityKey` se eliminan
  - path: `frontend_dashboard/src/entities/agent/api.ts`
    reason: reescritura completa — reemplaza mocks AGENTS/PERSONALITIES por `useQuery` real
  - path: `frontend_dashboard/src/entities/agent/keys.ts`
    reason: eliminación de `personalities()` key (hook removido)
  - path: `frontend_dashboard/src/entities/agent/index.ts`
    reason: actualización de exports (nuevos tipos, eliminación de Personality/usePersonalities)
  - path: `frontend_dashboard/src/entities/agent/contracts.ts`
    reason: archivo NUEVO con Zod schemas del response `GET /api/agents`
- **requires_merger:** true  _(multi_plugin + shared_files_touched no vacío)_

Esta sección la consume el downstream `hubara-plugin-planner-archon`
para construir el DAG plugin-level.

---

## §1. Acceptance criteria

- **AC-1:** Given que el plugin `chats` tiene `agentic: true` en su `plugin.yaml` y los sub-agentes `sales` y `remarketing` tienen archivos en `agent/<name>/workspace/*.md`, when el frontend carga la sección Agents, then muestra exactamente esos dos agentes (sales y remarketing) en la lista, con nombre y rol derivados del workspace.
- **AC-2:** Given un operador selecciona el agente `sales` en la lista, when se abre el panel de prompts, then el contenido de cada sección (Identity, Soul, Tools, Agents, Users) refleja el texto real de los archivos `IDENTITY.md`, `SOUL.md`, `TOOLS.md`, `AGENTS.md`, `USER.md` del workspace de sales. También trae los skills `workspace/skills/*/skill.md`.
- **AC-3:** Given que existen plugins no agénticos (`catalog`, `orders`, `eta`, `agents_admin`), when el endpoint `GET /api/agents` responde, then esos plugins NO aparecen en la lista de agentes.
- **AC-4:** Given que los archivos `.md` del workspace cambian en el servidor (nuevo deploy), when el operador recarga la sección Agents, then el contenido actualizado se muestra (sin `staleTime: Infinity` en el frontend).
- **AC-5:** Given que el campo `agentic: true` está ausente o es `false` en el `plugin.yaml` de un plugin que sí tiene sección `agent:`, when se consulta el endpoint, then ese plugin tampoco aparece en la lista de agentes (discriminador explícito — filtra `catalog` que tiene `agent:` pero no es conversacional).

---

## §2. Out of scope (re-confirmado)

- Editar o guardar cambios en los archivos `.md` del workspace desde el dashboard.
- Hot-reload automático al modificar archivos sin recargar la sección.
- Métricas de uso (`calls`, `csat`) en tiempo real — quedan `0` / `null` hasta HU de observabilidad.
- Soporte para plugins agénticos distintos de `chats` (no existen hoy).
- Versionado o historial de cambios de los prompts.
- Status real de los workers (online/offline) — queda "online" como placeholder.

---

## §3. Cambios por stack

### §3.1 Backend Python (`hubara_agency/src/...`)

| Archivo | Acción | Rol | LOC budget |
|---|---|---|---|
| `hubara_agency/src/plugins/agents_admin/api/__init__.py` | new | anchor de módulo | ~5 |
| `hubara_agency/src/plugins/agents_admin/api/routes.py` | new | router FastAPI `GET /api/agents` | ~80 |
| `hubara_agency/tests/plugins/agents_admin/__init__.py` | new | test package anchor | ~0 |
| `hubara_agency/tests/plugins/agents_admin/api/__init__.py` | new | test package anchor | ~0 |
| `hubara_agency/tests/plugins/agents_admin/api/test_routes.py` | new | tests funcionales del endpoint | ~70 |

**§3.1.1 DTOs:** Ningún DTO cruza boundary Temporal en esta HU — no hay Temporal. La serialización es FastAPI → JSON → frontend. No se toca `platform/contracts.py`.

**§3.1.2 Activities:** N/A — este plugin es Template B (solo API HTTP, sin workers Temporal).

**§3.1.3 Workflows:** N/A.

**§3.1.4 Routes (`routes.py`) — forma canónica:**

```python
# canonical (shape) — hubara_agency/src/plugins/agents_admin/api/routes.py
from pathlib import Path
from fastapi import APIRouter
from src.platform.plugin_manifest import load_manifest, enumerate_manifest_workers

router = APIRouter()

_PLUGINS_PYTHON_DIR = Path(__file__).resolve().parents[2]
# → hubara_agency/src/plugins/

_WORKSPACE_FILES = {
    "identity": "IDENTITY.md",
    "soul":     "SOUL.md",
    "tools":    "TOOLS.md",
    "agents":   "AGENTS.md",
    "users":    "USER.md",       # note: file USER.md maps to key "users"
}

@router.get("")
async def list_agents() -> list[dict]:
    result = []
    seen: set[str] = set()
    for plugin_id, worker_name, _ in enumerate_manifest_workers():
        if plugin_id in seen:
            continue
        manifest = load_manifest(plugin_id)
        if not manifest.get("agentic", False):
            continue
        seen.add(plugin_id)
        # iterate workers of this agentic plugin
        for worker in manifest.get("agent", {}).get("workers", []):
            w_name = worker.get("name", "")
            workspace = _read_workspace(plugin_id, w_name)
            agent_id = f"{plugin_id}:{w_name}"
            result.append({
                "id": agent_id,
                "plugin_id": plugin_id,
                "worker_name": w_name,
                "name": _extract_name(workspace["identity"], w_name),
                "role": _extract_role(workspace["identity"], w_name),
                "workspace": workspace,
            })
    return result


def _read_workspace(plugin_id: str, worker_name: str) -> dict:
    base = _PLUGINS_PYTHON_DIR / plugin_id / "agent" / worker_name / "workspace"
    content: dict = {}
    for key, filename in _WORKSPACE_FILES.items():
        filepath = base / filename
        content[key] = filepath.read_text(encoding="utf-8") if filepath.exists() else ""
    # skills: workspace/skills/*/skill.md
    skills = []
    skills_dir = base / "skills"
    if skills_dir.is_dir():
        for skill_dir in sorted(skills_dir.iterdir()):
            if skill_dir.is_dir():
                skill_file = skill_dir / "skill.md"
                if skill_file.exists():
                    skills.append({
                        "name": skill_dir.name,
                        "content": skill_file.read_text(encoding="utf-8"),
                    })
    content["skills"] = skills
    return content

# _extract_name, _extract_role: first # heading / first paragraph of IDENTITY.md
```

**§3.1.5 Workspace:** N/A — `agents_admin` no tiene workspace propio (no es agente conversacional).

**§3.1.6 Composition:** N/A — el router no necesita factory (no inyecta servicios externos).

**§3.1.7 Worker registration:** N/A — plugin Template B, sin workers Temporal.

**§3.1.8 Tests:** ver §9.

---

### §3.2 API HTTP (`hubara_agency/src/plugins/agents_admin/api/...`)

| Endpoint | Method | Path | Auth |
|---|---|---|---|
| Listar agentes con workspace | GET | `/api/agents_admin` | none (interno dashboard) |

**Nota sobre el prefix:** el manifest declarará `prefix: /api/agents_admin` y `python_module: src.plugins.agents_admin.api.routes`. El loader monta el router con ese prefix, por lo que el endpoint completo es `GET /api/agents_admin`. El frontend llama a esta URL desde `useAgents()`.

**Alternativa a revisar (A1):** el prefix podría ser simplemente `/api/agents` para alinearse con la nomenclatura del dominio. Verificar con el operador si hay conflicto con el router `chats/api/dashboard` que podría tener un endpoint similar.

---

### §3.3 Frontend TS (`frontend_dashboard/src/...`)

| Archivo | Acción | Layer FSD | LOC budget |
|---|---|---|---|
| `frontend_dashboard/src/entities/agent/contracts.ts` | new | entity | ~45 |
| `frontend_dashboard/src/entities/agent/model.ts` | modify | entity | ~-20 (net, elimina types) |
| `frontend_dashboard/src/entities/agent/api.ts` | modify (rewrite) | entity | ~50 (de ~95 lines → ~50) |
| `frontend_dashboard/src/entities/agent/keys.ts` | modify | entity | -3 (elimina personalities key) |
| `frontend_dashboard/src/entities/agent/index.ts` | modify | entity | ±5 (actualizar exports) |
| `frontend_dashboard/src/plugins/agents_admin/frontend/features/agents-prompts/ui/AgentsPrompts.tsx` | modify | feature | ~-10 (simplifica) |
| `frontend_dashboard/src/plugins/agents_admin/frontend/AgentsSection.tsx` | modify | plugin-page | +10 (agrega selección interna) |
| `frontend_dashboard/src/entities/agent/api.test.tsx` | new | entity-test | ~50 |

**§3.3.1 Entity `agent` — nuevos tipos en `model.ts`:**

```typescript
// NUEVOS (canonical shape)
export interface SkillContent {
  name: string;
  content: string;
}

export interface WorkspaceContent {
  identity: string;
  soul: string;
  tools: string;
  agents: string;
  users: string;
  skills: SkillContent[];
}

// Agent ACTUALIZADO: workspace reemplaza personality + Personality/PersonalityKey SE ELIMINAN
export interface Agent {
  id: string;           // "chats:sales"
  plugin_id: string;
  worker_name: string;
  name: string;         // de backend (extraído de IDENTITY.md)
  role: string;         // de backend
  workspace: WorkspaceContent;
  // Campos presentacionales con defaults del hook (fuera de scope de este backend)
  model: string;        // default: "deepseek-chat"
  icon: IconName;       // default por worker_name map
  color: AgentColor;    // default por worker_name map
  status: AgentStatus;  // default: "online"
  calls: number | null; // null (observabilidad futura)
  csat: number | null;  // null
  category: string;     // default: capitalize(worker_name)
  capabilities: Capability[];  // [] (empty, futura HU de observabilidad)
}
```

`Personality`, `PersonalityKey`, `PersonalityPrompt` se **eliminan** de `model.ts`. `PROMPT_SECTIONS` se **conserva** (sigue siendo la fuente de las 5 secciones del panel; las skills se renderizan por separado).

**§3.3.2 Entity `agent` — Zod schemas (`contracts.ts` nuevo):**

```typescript
// canonical shape — contracts.ts
import { z } from "zod";

export const skillContentSchema = z.object({
  name: z.string(),
  content: z.string(),
});

export const workspaceContentSchema = z.object({
  identity: z.string(),
  soul: z.string(),
  tools: z.string(),
  agents: z.string(),
  users: z.string(),
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

**§3.3.3 Hook rewrite (`api.ts`):**

```typescript
// canonical shape — entities/agent/api.ts
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
        csat:  null,
        category: dto.worker_name.charAt(0).toUpperCase() + dto.worker_name.slice(1),
        capabilities: [],
      }));
    },
    // staleTime omitido → default 0 (refetch on mount — satisface AC-4)
  });
}

// usePersonalities ELIMINADO — su rol lo cubre agent.workspace.*
```

**§3.3.4 `AgentsPrompts.tsx` — cambio de fuente de datos:**

```typescript
// diff canónico
// ANTES:
//   const { data: personalities = [] } = usePersonalities();
//   const personality = personalities.find(p => p.key === agent.personality);
//   text = personality.prompts[s.key]
//
// DESPUÉS:
//   (sin usePersonalities)
//   text = agent.workspace[s.key as keyof typeof agent.workspace]
//
// AGREGAR después de los 5 PROMPT_SECTIONS:
//   {agent.workspace.skills.map(skill => (
//     <div key={skill.name} className="prompt-section">
//       <div className="ps-head">...</div>
//       <div className="prompt-view">...</div>
//     </div>
//   ))}
```

El componente NO necesita cambio estructural — mismo CSS, misma lógica de layout. Solo cambia de dónde viene `text`.

**§3.3.5 `AgentsSection.tsx` — fix pre-existente + self-contained state:**

Este es un **bug pre-existente**: `AgentsSection` declara `selectedAgentId` y `setSelectedAgentId` como required props pero el shell llama `<ActivePage showSidebar showInspector />` vía `ComponentType<any>` → llegan como `undefined` → el `onSelect` de `AgentsList` crashea al primer click.

Esta HU lo corrige internalizando el estado:

```typescript
// canonical shape — AgentsSection.tsx
export function AgentsSection({ showSidebar, showInspector }: { showSidebar: boolean; showInspector: boolean }) {
  const { data: agents = [] } = useAgents();
  const [selectedId, setSelectedId] = useState<string>("");
  const activeId = selectedId || (agents[0]?.id ?? "");
  // ...
}
```

`AgentsSectionProps` se simplifica eliminando `selectedAgentId`/`setSelectedAgentId`.

**§3.3.6 `keys.ts` — eliminar key obsoleto:**

```typescript
// ELIMINAR:
//   personalities: () => [...agentKeys.all, "personalities"] as const,
```

**§3.3.7 `index.ts` — actualizar barrel:**

- Agregar: `export type { SkillContent, WorkspaceContent, AgentDto } from "./contracts";` (o re-export via model)
- Agregar: `export { agentDtoSchema, agentListDtoSchema } from "./contracts";`
- Eliminar: `export { usePersonalities } from "./api";`
- Eliminar: `export type { Personality, PersonalityKey, PersonalityPrompt } from "./model";`
- Conservar: `export { PROMPT_SECTIONS } from "./model";`

**§3.3.8 Tailwind tokens:** Ninguno nuevo.

**§3.3.9 Tests:** ver §9.

---

### §3.4 Manifest (`plugin.yaml`)

| Plugin | Sección del manifest | Cambio |
|---|---|---|
| `agents_admin` | `api:` | Agregar bloque completo |
| `chats` | top-level | Agregar `agentic: true` |
| `_schema/plugin.schema.yaml` | `properties:` | Agregar campo `agentic` |

**`agents_admin/plugin.yaml` — agregar:**

```yaml
api:
  python_module: src.plugins.agents_admin.api.routes
  prefix: /api/agents_admin
  tags: [AgentsAdmin]
```

**`chats/plugin.yaml` — agregar al top-level (junto a `id`, `version`):**

```yaml
agentic: true
```

**`plugin.schema.yaml` — agregar en `properties:` (junto a `id`, `version`, etc.):**

```yaml
  agentic:
    type: boolean
    description: |
      Marca que el plugin alberga agentes conversacionales con workspace
      de prompts (IDENTITY.md, SOUL.md, TOOLS.md, AGENTS.md, USER.md).
      Solo los plugins con agentic=true aparecen en GET /api/agents_admin.
      Aplica independientemente de si el plugin tiene sección `agent:` — un
      plugin puede tener workers Temporal no conversacionales (e.g. catalog).
    default: false
```

**Nota:** `agents_admin/plugin.yaml` tiene actualmente la descripción "Plugin frontend-only — los datos vienen de `entities/agent` (shared cross-plugin)". Esta descripción también debe actualizarse para reflejar la nueva capa API.

**Regla del bloque `frontend:` (§3.4 guide):** `agents_admin` tiene UI en el dashboard → el bloque `frontend:` existente se conserva sin cambio. La adición es solo el bloque `api:` nuevo.

---

### §3.5 K8s manifest

N/A — Template B no tiene worker Temporal. No se crea `worker-agents-admin-*.yaml`.

---

## §4. DTOs boundary (R-JSON)

N/A — esta HU no involucra Temporal. Los datos cruzan solo HTTP (FastAPI → JSON → frontend). La serialización es nativa de FastAPI (dicts/lists de Python → JSON automático).

---

## §5. Activities + retry policies

N/A — Template B. Sin activities ni workflows.

---

## §6. Workspace deltas

N/A — `agents_admin` no tiene workspace propio. El plugin LEER workspaces ajenos (chats/sales, chats/remarketing) vía filesystem, pero no escribe ni tiene workspace propio.

---

## §7. State adapters

N/A — la HU es read-only de workspaces del filesystem. No hay nueva persistencia.

---

## §8. Composition factories

N/A — el router de `agents_admin` no inyecta servicios externos (no LLM, no Temporal). Lee filesystem directamente.

---

## §9. Tests por rol

| Rol | Tests | Comando |
|---|---|---|
| Functional (backend) | `tests/plugins/agents_admin/api/test_routes.py::test_list_agents_returns_agentic_only` | `cd hubara_agency && uv run pytest tests/plugins/agents_admin/ -v` |
| Functional (backend) | `test_list_agents_includes_workspace_content` | idem |
| Functional (backend) | `test_list_agents_excludes_plugins_without_agentic_flag` | idem |
| Functional (backend) | `test_list_agents_includes_skills_from_subdirectory` | idem |
| Functional (backend) | `test_list_agents_returns_empty_string_for_missing_workspace_file` | idem |
| Frontend unit (vitest) | `entities/agent/api.test.tsx::test useAgents fetches and maps with Zod` | `cd frontend_dashboard && npm test -- agent/api` |
| Frontend unit (vitest) | `entities/agent/api.test.tsx::test useAgents provides default icon/color for known workers` | idem |
| Frontend arch | (sin nombre — corre todo) | `cd frontend_dashboard && npm run test:arch` |
| Backend arch | (sin nombre) | `cd hubara_agency && uv run pytest -m architecture` |
| Backend R-DIP | (sin nombre) | `cd hubara_agency && uv run lint-imports` |

**Test shape canónico backend:**

```python
# canonical — tests/plugins/agents_admin/api/test_routes.py
import pytest
from httpx import AsyncClient

@pytest.mark.functional
async def test_list_agents_returns_agentic_only(api_client: AsyncClient, tmp_path, monkeypatch):
    # Setup: mock manifest con agentic=True para chats; False para catalog
    # Assert: solo agents del plugin chats aparecen
    response = await api_client.get("/api/agents_admin")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    plugin_ids = {a["plugin_id"] for a in data}
    assert "chats" in plugin_ids
    assert "catalog" not in plugin_ids
```

**Nota de comportamiento real (CLAUDE.md gotcha #1):** los tests funcionales deben verificar que el endpoint EMITE datos reales de workspace, no solo que el schema los permite. El test `test_list_agents_includes_workspace_content` debe verificar que `data[0]["workspace"]["identity"]` no es una cadena vacía cuando el workspace existe.

---

## §10. Verification commands

```bash
# ── Backend ────────────────────────────────────────────────────────────
cd hubara_agency && uv run pytest tests/plugins/agents_admin/ -v
cd hubara_agency && uv run pytest tests/plugins/ -v      # incluye invariantes
cd hubara_agency && uv run pytest -m architecture
cd hubara_agency && uv run lint-imports

# Smoke: verificar que el endpoint arranca y responde
cd hubara_agency && ENABLED_PLUGINS=agents_admin,chats uv run python run_api.py &
sleep 3 && curl http://localhost:8000/api/agents_admin | python3 -m json.tool | head -40
# Esperado: lista JSON con 2 agentes (sales + remarketing) con workspace real

# Verificar que catalog NO aparece (AC-3, AC-5)
curl http://localhost:8000/api/agents_admin | python3 -c "
import json,sys; data=json.load(sys.stdin)
pids = {a['plugin_id'] for a in data}; print('plugin_ids:', pids)
assert 'catalog' not in pids, 'FALLO: catalog no debe aparecer'
print('OK: catalog excluido')
"

# ── Frontend ───────────────────────────────────────────────────────────
cd frontend_dashboard && npm test -- agent/api
cd frontend_dashboard && npm run test:arch
cd frontend_dashboard && npx tsc -b
cd frontend_dashboard && npm run build

# Verificar que plugins:sync sigue funcionando con el nuevo campo en schema
cd frontend_dashboard && npm run plugins:sync

# ── Render-compose drift (no cambia nada en compose, pero verificar) ──
cd hubara_agency && uv run python scripts/render-compose.py && \
  git diff --exit-code docker-compose.local.yml
```

---

## §11. Hard rules check

- **R-DET:** N/A — no hay workflows Temporal en esta HU.
- **R-JSON:** N/A — no hay boundary workflow↔activity. Los datos cruzan HTTP nativo.
- **R-STATELESS:** N/A — no hay activities.
- **R-HEARTBEAT:** N/A — el endpoint `GET /api/agents_admin` es O(archivos × KB). El tiempo de respuesta es de ~10ms para los workspaces actuales. Sin riesgo de timeout.
- **R-DIP:** Applies parcialmente.
  - `agents_admin/api/routes.py` importa de `src.platform.plugin_manifest` (platform → plugin: permitido en dirección correcta).
  - `routes.py` NO importa desde `src.plugins.chats.*` ni ningún sibling. Lee el filesystem con `Path` — esto es I/O, no un import Python. ✓ R-DIP satisfecho.
  - `import-linter` debe pasar sin cambio en el `.importlinter`.
- **FSD layering:** Applies.
  - `entities/agent/*` importa solo de `shared/` (Zod, apiClient). ✓
  - Features de `agents_admin` importan de `entities/agent` y `shared/ui`. ✓
  - `AgentsSection.tsx` importa `useAgents` de `entities/agent`. ✓
  - No hay cross-plugin frontend imports. ✓
  - `npm run test:arch` debe pasar sin cambios al `.dependency-cruiser.cjs`.
- **Plugin manifest = SSoT:** Applies.
  - El campo `agentic: true` se declara en `chats/plugin.yaml` y se valida vía `plugin.schema.yaml`. ✓
  - El endpoint `GET /api/agents_admin` se declara en `agents_admin/plugin.yaml` bajo `api:`. ✓
  - `test_premortem_invariants.py` verifica parity — no debe romperse porque `agents_admin` Template B no tiene workers (los tests de workers solo aplican a `agent.workers[]`).

---

## §12. Risks / open questions

1. **RIESGO: `plugin.schema.yaml` modifica schema shared.** La nota en `spinal-files.yaml` dice "Cambios al schema requieren ADR — feature task NO debería modificar esto". El campo `agentic` es aditivo (booleano opcional, default false) y sigue la regla "manifest = SSoT". Sin embargo, si el planner considera que esto requiere un PR de arquitectura separado, se puede proceder en 2 fases: (a) agregar `agentic` al schema en un PR, (b) implementar la feature en otro PR subsecuente.
   - **Mitigación:** el campo es `additionalProperties: false` → `chats/plugin.yaml` actualmente fallaría la validación schema si le agregamos `agentic: true` sin actualizar el schema. El fix y el feature deben ir juntos.

2. **RIESGO: prefix ambiguo `/api/agents_admin`** vs convención del dominio. El HU menciona "GET /api/agents" pero el prefix del plugin suele ser `/api/<plugin_id>`. El frontend lo llama como `"/api/agents_admin"` en `useAgents()`. Confirmar con el operador si el path debería ser `/api/agents_admin` (consistente con plugin_id) o `/api/agents` (más legible pero podría confundir con el nombre del plugin frontend).

3. **RIESGO: workspace path en K8s/Docker.** En producción, el workspace de `chats/agent/sales/workspace/` vive en el PVC de `hubara_agency`. El endpoint lee desde `src/plugins/chats/agent/sales/workspace/` — que es el directorio dentro del container. Verificar que el Dockerfile incluye los workspace .md en la imagen, o que el PVC los monta al path correcto. _(Verify en deployment.)_

4. **PRE-EXISTING BUG corregido:** `AgentsSection.tsx` declaraba `selectedAgentId: string` y `setSelectedAgentId` como required props, pero el shell los pasaba como `undefined` (vía `ComponentType<any>`). Click en cualquier agente causaba crash. Esta HU lo corrige internalizando el estado. Se flagea aquí para visibilidad.

5. **OPEN: extracción de `name`/`role` de IDENTITY.md.** La heurística (primer `#` heading → name, primer párrafo tras el heading → role) es best-effort. Los archivos IDENTITY.md de sales y remarketing deben verificarse para confirmar que el formato sea compatible. Si no tienen `#` heading, el fallback es `capitalize(worker_name)`.

6. **OPEN: TOOLS.md size.** El TOOLS.md de sales tiene 43KB. El response JSON completo podría ser grande (~50KB por agente). Para el caso de 2 agentes actuales esto es manejable. Para N > 5 agentes, considerar paginación o respuesta lazy (fuera de scope de esta HU).

---

## §13. Out-of-scope (técnicos)

- NO modificar `src/platform/plugin_manifest.py` para agregar una nueva función de enumeración. El router usa `enumerate_manifest_workers()` + `load_manifest()` inline.
- NO agregar Temporal ni activities a `agents_admin`. El endpoint es un router FastAPI puro.
- NO tocar `docker-compose.local.yml` ni K8s (Template B no tiene workers).
- NO refactorizar `AgentsInspector.tsx` más allá de que `capabilities` quede `[]`. El panel de Capacidades quedará vacío hasta HU de observabilidad.
- NO eliminar el campo `personality: PersonalityKey` del type vía deprecation cycle; se elimina directamente en esta HU (es mock, no hay consumers externos).
- NO agregar error boundary ni loading skeleton custom — los defaults de TanStack Query (`isLoading: true`, `data: undefined`) y los fallbacks `?? agents[0]` existentes son suficientes.
- NO modificar `tests/plugins/test_premortem_invariants.py` — Template B no agrega workers, los invariantes siguen pasando.

---

## §14. Iteration changelog

_(Solo si iteration > 1 — N/A en iteración 1.)_

---

## §15. Assumptions made

- **A1 — Icon/color por worker_name.** Se asume map inline `{sales→bolt/blue, remarketing→refresh/orange}`. Workers sin entrada en el map reciben `bot/blue`. Reversibilidad: alta (cambiar el map en api.ts).
- **A2 — Extracción name/role de IDENTITY.md.** Primera línea `# Heading` → name; primer párrafo no vacío después del heading → role (max 120 chars). Fallback: `capitalize(worker_name)`. Reversibilidad: alta (cambiar regex en routes.py).
- **A3 — staleTime omitido (TanStack default = 0).** Cada mount del componente `AgentsSection` dispara un refetch. Para un dashboard desktop donde el operador navega raramente, esto es aceptable. Si el refetch resulta costoso (TOOLS.md de 43KB × 2 agentes en cada switch de tab), se puede agregar `staleTime: 5 * 60 * 1000`. Reversibilidad: alta.
- **A4 — prefix `/api/agents_admin`.** Consistente con convention `prefix: /api/<plugin_id>`. Ver Risk #2 — revisar con operador si se prefiere `/api/agents`.
- **A5 — `capabilities: []` en `AgentsInspector`.** El panel de Capacidades quedará vacío. El componente no crashea (`.map()` sobre array vacío = nada). El operador verá "Capacidades" sin contenido. Reversibilidad: alta (llenar en HU de observabilidad).
- **A6 — `calls: null` en `AgentsList`.** El componente actual renderiza `agent.calls.toLocaleString()`. Con `calls: null` se crashea. El hook mapea `calls: null` pero el implementer debe actualizar `AgentsList.tsx` para ser null-safe: `{agent.calls?.toLocaleString() ?? "—"}`. Flageo esto como pre-requisito del implementer — es una consecuencia directa del cambio de tipo.
- **A7 — `Personality`, `PersonalityKey`, `PersonalityPrompt` eliminados directo.** No hay consumers externos a `agents_admin` que importen estos tipos (confirmado: solo `AgentsPrompts.tsx` los usaba). Reversibilidad: git revert si hay consumer oculto.
- **A8 — `usePersonalities()` eliminado.** Mismo razonamiento que A7.
- **A9 — Workspace file missing → empty string.** Si SOUL.md no existe, `workspace.soul` = `""`. El panel muestra la sección vacía. No se lanza 404. Reversibilidad: alta.
- **A10 — `enumerate_manifest_workers()` ignora `ENABLED_PLUGINS`.** El endpoint `GET /api/agents_admin` siempre retorna todos los plugins agénticos del repo, independiente de `ENABLED_PLUGINS`. Para el dashboard esto es correcto (el operador quiere ver todos los agentes del sistema). Si se quisiera filtrar, se puede agregar lógica posterior.

---

## §16. Spec deltas — capability behavior changes

| Capability | Path delta | Status | Resumen |
|---|---|---|---|
| `plugins/agents_admin` | `$ARTIFACTS_DIR/spec-deltas/plugins/agents_admin/spec.md` | seed_inline (no existía) | Bootstrap completo: API de lectura de workspaces + UI de prompts reales |

Los workers `agents/sales-worker` y `agents/remarketing-worker` NO cambian su comportamiento observable — el endpoint solo LEE sus workspaces. No se emiten deltas para esas capabilities.
