# HU refinement — Conectar sección Agents del dashboard a datos reales del backend

- HU id: HU-20260527-012300-conectar-seccion-agents-del-dashboard-a
- Source: $ARTIFACTS_DIR/hu-original.md
- Refiner: hubara-tech-refiner-archon
- Date: 2026-05-26
- Iteration: 1

---

## §0. Plugin classification

- **mode:** `single_plugin`
- **plugins_affected:**
  - id: `agents_admin`
    layers: [api, frontend]
    action: extend   # template A (frontend-only) → template B (frontend + API)
- **shared_files_touched:**
  - path: frontend_dashboard/src/entities/agent/api.ts
    reason: reemplazar mock AGENTS + PERSONALITIES hardcodeados por useQuery real contra GET /api/agents; deprecar usePersonalities()
  - path: frontend_dashboard/src/entities/agent/model.ts
    reason: actualizar interface Agent (agregar prompts/skills, quitar personality: PersonalityKey)
  - path: frontend_dashboard/src/entities/agent/contracts.ts
    reason: actualizar Zod schema agentSchema para reflejar el nuevo shape de Agent
  - path: frontend_dashboard/src/entities/agent/index.ts
    reason: re-exportar si se quitan exports deprecated (usePersonalities, Personality)
- **requires_merger:** false

> **Nota sobre `plugin.schema.yaml`**: La HU pide agregar `agentic: true` al schema YAML.
> Ese cambio requiere ADR + PR separado (spinal + nota explícita en manifest-schema.md §10:
> "Feature task NUNCA agrega campo al schema"). Asunción A1 en §15 propone la alternativa
> adoptada: detectar plugins agénticos por presencia de `agent.workers[]` no vacío — sin
> modificar el schema. Ver §12 para el riesgo.

---

## §1. Acceptance criteria

- **AC-1:** Given que el plugin `chats` tiene `agent.workers[]` con al menos un entry en su `plugin.yaml` (sales y remarketing), when el frontend carga la sección Agents, then muestra exactamente esos dos agentes (sales y remarketing) en la lista, con nombre y rol derivados del workspace.
- **AC-2:** Given un operador selecciona el agente `sales` en la lista, when se abre el panel de prompts, then el contenido de cada sección (Identity, Soul, Tools, Agents, Users) refleja el texto real de los archivos `IDENTITY.md`, `SOUL.md`, `TOOLS.md`, `AGENTS.md`, `USER.md` del workspace de sales; y el campo `skills` del agente contiene el contenido de `workspace/skills/*/skill.md`.
- **AC-3:** Given que existen plugins no agénticos (`catalog`, `orders`, `eta`, `agents_admin`) sin `agent.workers[]` en su manifest, when el endpoint `GET /api/agents` responde, then esos plugins NO aparecen en la lista de agentes.
- **AC-4:** Given que los archivos `.md` del workspace cambian en el servidor (nuevo deploy), when el operador recarga la sección Agents, then el contenido actualizado se muestra (staleTime finito en useQuery — no Infinity como el mock).
- **AC-5:** Given que el campo `agent:` está ausente o `agent.workers[]` está vacío en el `plugin.yaml` de un plugin, when se consulta el endpoint, then ese plugin tampoco aparece en la lista de agentes.

---

## §2. Out of scope (re-confirmado)

- Editar o guardar cambios en los archivos `.md` del workspace desde el dashboard.
- Hot-reload automático al modificar archivos sin recargar la sección.
- Métricas de uso (`calls`, `csat`) en tiempo real — esos campos se devuelven `null` desde el API.
- Soporte para plugins agénticos distintos de `chats` (el endpoint los incluiría automáticamente, pero solo `chats` existe hoy).
- Versionado o historial de cambios de los prompts.
- Agregar campo `agentic: true` al schema de `plugin.schema.yaml` (requiere ADR separado).
- Renderizar las `skills` en la UI — el API las incluye en la respuesta y el modelo frontend las tipea, pero el componente `AgentsPrompts.tsx` no las renderiza en esta HU.

---

## §3. Cambios por stack

### §3.1 Backend Python (`hubara_agency/src/...`)

| Archivo | Acción | Rol | LOC budget |
|---|---|---|---|
| `hubara_agency/src/plugins/agents_admin/__init__.py` | new | anchor Python package | ~3 |
| `hubara_agency/src/plugins/agents_admin/api/__init__.py` | new | module marker | ~3 |
| `hubara_agency/src/plugins/agents_admin/api/routes.py` | new | FastAPI router GET /agents | ~75 |
| `hubara_agency/src/platform/plugin_manifest.py` | modify | agregar `list_agentic_plugins()` helper | +20 |

**Sub-cambios por capa:**

- **§3.1.1 DTOs:** No hay DTOs cruzando boundary workflow↔activity (este endpoint es FastAPI puro, sin Temporal). El router devuelve dicts plain — FastAPI los serializa. No se toca `src/platform/contracts.py`.

- **§3.1.2 Activities:** N/A — no hay actividades Temporal nuevas.

- **§3.1.3 Workflows:** N/A.

- **§3.1.4 Tools LLM:** N/A.

- **§3.1.5 Workspace:** N/A — los archivos `.md` se leen; no se escriben.

- **§3.1.6 `platform/plugin_manifest.py` — nueva función `list_agentic_plugins()`:**

  ```python
  # canonical — shape en platform/plugin_manifest.py
  def list_agentic_plugins() -> list[tuple[str, list[str]]]:
      """Retorna [(plugin_id, [worker_names])] para plugins con agent.workers[] no vacío.
      
      Lee todos los manifests de _PLUGINS_MANIFEST_DIR. No usa el cache de
      load_manifest() porque necesita iterar sobre todos los plugins.
      """
  ```

  La implementación itera `_PLUGINS_MANIFEST_DIR`, lee cada `plugin.yaml` con
  `yaml.safe_load`, filtra los que tienen `manifest.get("agent", {}).get("workers") or []`
  no vacío, y devuelve `(plugin_id, [w["name"] for w in workers])`.

  **R-DIP:** `platform/` puede leer el filesystem del repo sin importar plugins.
  `list_agentic_plugins()` usa solo `yaml`, `pathlib.Path`, y `_PLUGINS_MANIFEST_DIR`
  que ya vive en `plugin_manifest.py`.

- **§3.1.7 `agents_admin/api/routes.py` — lógica del endpoint:**

  ```python
  # canonical — shape en plugins/agents_admin/api/routes.py
  from fastapi import APIRouter
  from pathlib import Path
  from src.platform.plugin_manifest import list_agentic_plugins

  router = APIRouter()

  _WORKSPACE_ROOT = Path(__file__).parents[4] / "src" / "plugins"
  # parents[4] desde routes.py → hubara_agency/

  @router.get("/agents")
  async def list_agents() -> list[dict]:
      ...
  ```

  Lógica del handler:
  1. `list_agentic_plugins()` → `[(plugin_id, [worker_names])]`
  2. Por cada `(plugin_id, worker_names)`, por cada `worker_name`:
     - `workspace_dir = _WORKSPACE_ROOT / plugin_id / "agent" / worker_name / "workspace"`
     - Leer `IDENTITY.md`, `SOUL.md`, `TOOLS.md`, `AGENTS.md`, `USER.md` (Path.read_text, encoding="utf-8"). Si falta un archivo → string vacío `""`.
     - Leer `workspace_dir / "skills" / */skill.md` → dict `{skill_dir_name: content}`.
     - Extraer `role`: primera línea no-heading y no-vacía de `IDENTITY.md` (strip `#`, strip whitespace).
     - Derivar `display_name`: `worker_name.capitalize()` (e.g. `"sales"` → `"Sales"`).
     - Derivar `icon` y `color` (ver §15 A4).
  3. Devolver lista de dicts con el schema de §4.

  **R-DIP check:** `routes.py` importa de `src.platform.plugin_manifest` ✅ (plugins pueden importar de platform). NO importa de `src.plugins.chats.*` ✅. No importa `temporalio.*` ✅.

- **§3.1.8 Tests:**
  - `tests/plugins/agents_admin/api/test_list_agents.py` — nuevo. Necesita fixtures de workspace `.md` en `tmp_path`. Ver §9.

### §3.2 API HTTP

| Endpoint | Method | Path completo | Auth | Source |
|---|---|---|---|---|
| Listar agentes con workspace content | GET | `/api/agents` | none | `agents_admin/api/routes.py` |

Mounting vía manifest `api.prefix: /api` + `@router.get("/agents")`.

**Response shape** (los campos exactos están en §4):

```json
[
  {
    "id": "chats/sales",
    "plugin_id": "chats",
    "worker_name": "sales",
    "display_name": "Sales",
    "role": "primera línea descriptiva del IDENTITY.md",
    "icon": "bolt",
    "color": "green",
    "prompts": {
      "identity": "...(contenido IDENTITY.md)...",
      "soul":     "...(contenido SOUL.md)...",
      "tools":    "...(contenido TOOLS.md)...",
      "agents":   "...(contenido AGENTS.md)...",
      "users":    "...(contenido USER.md)..."
    },
    "skills": {
      "hubara_catalog": "...(contenido SKILL.md del hubara_catalog)...",
      "sales_script":   "...(contenido SKILL.md del sales_script)..."
    },
    "calls": null,
    "csat": null
  },
  {
    "id": "chats/remarketing",
    ...
  }
]
```

### §3.3 Frontend TS (`frontend_dashboard/src/...`)

| Archivo | Acción | Layer FSD | LOC delta |
|---|---|---|---|
| `frontend_dashboard/src/entities/agent/model.ts` | modify | entity | +8 / -10 |
| `frontend_dashboard/src/entities/agent/contracts.ts` | modify | entity | +15 / -15 |
| `frontend_dashboard/src/entities/agent/api.ts` | modify | entity | +25 / -65 |
| `frontend_dashboard/src/entities/agent/index.ts` | modify | entity | ±5 |
| `frontend_dashboard/src/plugins/agents_admin/frontend/features/agents-prompts/ui/AgentsPrompts.tsx` | modify | plugin feature | +3 / -10 |

**§3.3.1 Entity `agent/model.ts` — actualizar `Agent` interface:**

```typescript
// canonical — nuevo shape de Agent (remove personality, add prompts/skills)
export interface Agent {
  id: string;           // "{plugin_id}/{worker_name}" e.g. "chats/sales"
  plugin_id: string;
  worker_name: string;
  display_name: string;
  role: string;
  icon: IconName;       // derivado en backend, default "bot"
  color: AgentColor;    // derivado en backend, default "blue"
  status: AgentStatus;  // "online" hardcoded por ahora
  prompts: PersonalityPrompt;  // workspace content (reusar la interface existente)
  skills: Record<string, string>;
  calls: number | null;
  csat: number | null;
}
// Quitar: category?: string, personality: PersonalityKey
// Mantener: PersonalityPrompt interface (reutilizada para prompts)
// Quitar: Personality interface, PersonalityKey type (ya no aplican con datos reales)
```

**§3.3.2 Entity `agent/contracts.ts` — actualizar Zod schema:**

```typescript
// canonical — agentSchema actualizado
export const personalityPromptSchema = z.object({
  identity: z.string(),
  soul:     z.string(),
  tools:    z.string(),
  agents:   z.string(),
  users:    z.string(),
});

export const agentSchema = z.object({
  id:           z.string(),
  plugin_id:    z.string(),
  worker_name:  z.string(),
  display_name: z.string(),
  role:         z.string(),
  icon:         z.string(),
  color:        z.string(),
  status:       z.enum(["online", "idle", "off"]),
  prompts:      personalityPromptSchema,
  skills:       z.record(z.string()),
  calls:        z.number().nullable(),
  csat:         z.number().nullable(),
});
export type AgentDto = z.infer<typeof agentSchema>;
```

**§3.3.3 Entity `agent/api.ts` — reemplazar mocks con useQuery real:**

```typescript
// canonical — useAgents real
export function useAgents() {
  return useQuery({
    queryKey: agentKeys.list(),
    queryFn: async () => {
      const raw = await apiClient.get<unknown>("/api/agents");
      return z.array(agentSchema).parse(raw);
    },
    staleTime: 5 * 60 * 1000,  // 5 min — no Infinity
  });
}
// Quitar: usePersonalities(), constante AGENTS, constante PERSONALITIES
```

**§3.3.4 `AgentsPrompts.tsx` — quitar usePersonalities():**

```typescript
// canonical — cambio mínimo
// ANTES: const { data: personalities = [] } = usePersonalities();
//        const personality = personalities.find(p => p.key === agent.personality);
// DESPUÉS: usar agent.prompts directamente

// En el map de PROMPT_SECTIONS:
// ANTES: personality.prompts[s.key]
// DESPUÉS: agent.prompts[s.key as keyof PersonalityPrompt]
```

El layout visual de `AgentsPrompts.tsx` no cambia. Solo el source de datos
para `PROMPT_SECTIONS.map(...)`.

**§3.3.5 `AgentsList.tsx` — verificar grouping:**

El componente agrupa por `agent.category`. Con el nuevo `Agent` type, `category`
se elimina. Si el componente ya usa `a.category` → needs update o bien el campo
se elimina del grouping. **Verify**: si `AgentsList` sin `category` funciona
mostrando todos los agentes en un grupo plano. Ajustar grouping a `plugin_id`
o simplificar a lista flat. Ver §12 R2.

**§3.3.6 Tailwind tokens:** N/A — no se agregan tokens nuevos.

**§3.3.7 Tests:**
- `frontend_dashboard/src/entities/agent/api.test.tsx` — actualizar o agregar tests que verifiquen el fetch real. Ver §9.

### §3.4 Manifest (`agents_admin/plugin.yaml`)

| Sección del manifest | Cambio |
|---|---|
| `api:` (nuevo bloque) | agregar con `python_module`, `prefix: /api`, `tags: [Agents]` |

```yaml
# Agregar a frontend_dashboard/src/plugins/agents_admin/plugin.yaml:
api:
  python_module: src.plugins.agents_admin.api.routes
  prefix: /api
  tags: [Agents]
```

El loader de `src.main` registra automáticamente el router al ver `api.python_module`
con `prefix: /api` → el endpoint final es `GET /api/agents`.

**Regla del bloque `frontend:`:** el bloque `frontend:` YA EXISTE en `agents_admin/plugin.yaml`.
No se modifica. El test de architecture `#19a/#19b` sigue verde.

**NO se agrega** bloque `agent:` (agents_admin no tiene workers Temporal).

### §3.5 K8s manifest

N/A — no se agrega ningún worker Temporal nuevo.

---

## §4. DTOs boundary (R-JSON)

No hay nuevos DTOs cruzando workflow↔activity boundary. El endpoint FastAPI retorna
dicts planos serializados automáticamente por FastAPI. R-JSON no aplica aquí.

**Response dict shape** (no es un dataclass Temporal — solo documentación del JSON):

```python
# shape — NO es @dataclass, es el dict que retorna routes.py
{
    "id": str,          # f"{plugin_id}/{worker_name}"
    "plugin_id": str,
    "worker_name": str,
    "display_name": str,
    "role": str,
    "icon": str,        # "bolt" | "calendar" | "bot" según worker_name
    "color": str,       # "green" | "blue" | "purple" según worker_name
    "status": str,      # "online" hardcoded
    "prompts": {
        "identity": str,
        "soul": str,
        "tools": str,
        "agents": str,
        "users": str,
    },
    "skills": dict[str, str],  # {skill_dir_name: content}
    "calls": None,
    "csat": None,
}
```

---

## §5. Activities + retry policies

N/A — ninguna activity Temporal nueva. El endpoint lee archivos sincrónicamente
en el hilo de FastAPI (archivos pequeños, lectura local, latencia <1ms).

---

## §6. Workspace deltas (`workspace/*.md`)

N/A — los archivos workspace existentes se leen, no se crean ni modifican.

---

## §7. State adapters

N/A — ninguna persistencia nueva.

---

## §8. Composition factories

N/A — ninguna factory DI requerida. El endpoint es una función stateless.

---

## §9. Tests por rol

| Rol | Archivo / test | Comando |
|---|---|---|
| Unit (API route — backend) | `tests/plugins/agents_admin/api/test_list_agents.py::test_returns_two_agents_for_chats` | `cd hubara_agency && uv run pytest tests/plugins/agents_admin/ -v` |
| Unit (API route — backend) | `tests/plugins/agents_admin/api/test_list_agents.py::test_non_agentic_plugins_excluded` | idem |
| Unit (API route — backend) | `tests/plugins/agents_admin/api/test_list_agents.py::test_workspace_md_files_read_correctly` | idem |
| Unit (API route — backend) | `tests/plugins/agents_admin/api/test_list_agents.py::test_skills_included_in_response` | idem |
| Unit (API route — backend) | `tests/plugins/agents_admin/api/test_list_agents.py::test_missing_md_returns_empty_string` | idem |
| Unit (platform) | `tests/test_plugin_manifest.py::test_list_agentic_plugins_returns_chats` | `cd hubara_agency && uv run pytest tests/test_plugin_manifest.py -v` |
| Functional (E2E backend) | `tests/functional/test_agents_endpoint.py::test_get_agents_returns_real_workspace_content` | `cd hubara_agency && uv run pytest tests/functional/ -m functional -v` |
| Frontend unit (vitest) | `entities/agent/api.test.tsx::test_useAgents_fetches_real_endpoint_and_validates_zod` | `cd frontend_dashboard && npm test -- entities/agent` |
| Frontend unit (vitest) | `entities/agent/api.test.tsx::test_useAgents_stale_time_is_finite` | idem |
| Frontend arch | (corre todo) | `cd frontend_dashboard && npm run test:arch` |

**Nota de fixtures (backend):** Los tests de `test_list_agents.py` necesitan:
1. Fixture `tmp_path` con una estructura de workspace fake (5 `.md` + skills).
2. Mockear `list_agentic_plugins()` para retornar `[("chats", ["sales"])]` con el tmp_path como base.
3. O inyectar `workspace_root` como argumento al router (make testeable).

**Recomendación**: extraer el `_WORKSPACE_ROOT` a un parámetro inyectable en `list_agents()`:
```python
async def list_agents(workspace_root: Path = _WORKSPACE_ROOT) -> list[dict]:
```
Esto permite tests sin patchear Path(__file__).

---

## §10. Verification commands

```bash
# ── Backend ────────────────────────────────────────────────────────────────
# Unit tests del nuevo endpoint
cd hubara_agency && uv run pytest tests/plugins/agents_admin/ -v

# Unit tests platform (list_agentic_plugins)
cd hubara_agency && uv run pytest tests/test_plugin_manifest.py -v

# Functional (requiere workers + vault locales)
cd hubara_agency && uv run pytest tests/functional/ -m functional -v

# Architecture gate (R-DIP, imports)
cd hubara_agency && uv run pytest -m architecture
cd hubara_agency && uv run lint-imports

# Smoke import del nuevo módulo
cd hubara_agency && uv run python -c "import src.plugins.agents_admin.api.routes; print('OK')"

# Boot API local + curl manual
cd hubara_agency && uv run python run_api.py &
curl http://localhost:8000/api/agents | python3 -m json.tool

# Drift check compose (no debe cambiar — agents_admin no tiene workers)
cd hubara_agency && uv run python scripts/render-compose.py && \
  git diff --exit-code docker-compose.local.yml

# ── Frontend ────────────────────────────────────────────────────────────────
# Type check
cd frontend_dashboard && npx tsc -b

# Tests vitest
cd frontend_dashboard && npm test

# Architecture gate (FSD + plugin isolation)
cd frontend_dashboard && npm run test:arch

# Sync plugins registry (debe seguir incluyendo agents_admin)
cd frontend_dashboard && npm run plugins:sync

# Build
cd frontend_dashboard && npm run build
```

---

## §11. Hard rules check (R-rules + FSD)

- **R-DET:** N/A — no se modifican workflows Temporal.
- **R-JSON:** N/A — no hay nuevos DTOs cruzando workflow↔activity boundary.
- **R-STATELESS:** N/A — no hay nuevas activities.
- **R-HEARTBEAT:** N/A — no hay nuevas activities.
- **R-DIP:** Applies.
  - `agents_admin.api.routes` → importa `src.platform.plugin_manifest.list_agentic_plugins` ✅ (plugins can import platform)
  - `agents_admin.api.routes` → NO debe importar `src.plugins.chats.*` ni ningún sibling ✅
  - `agents_admin.api.routes` → NO importa `temporalio.*` ✅
  - `platform.plugin_manifest.list_agentic_plugins` → solo usa `yaml`, `pathlib`, módulos stdlib ✅
- **R-DIP #10 cross-worker:** N/A — no hay transitions entre workers.
- **Orchestration contract changes:** N/A — no se toca ningún Input dataclass de workflow.
- **FSD layering:**
  - `entities/agent/api.ts` importa solo de `shared/api/client`, `@tanstack/react-query`, `./keys`, `./contracts` ✅
  - `AgentsPrompts.tsx` (plugin feature) importa de `entities/agent` ✅ (plugins pueden importar entities)
  - Cross-plugin imports: AgentsPrompts no importa de `@plugins/chats/*` ✅
- **Plugin manifest = SSoT:**
  - El nuevo bloque `api:` en `agents_admin/plugin.yaml` es el único lugar que declara el router ✅
  - `agents_admin` NO declara bloque `agent:` (no tiene workers) ✅
  - El `frontend:` block ya existe → `plugins-sync.ts` lo incluye ✅
  - Test `#19a/#19b` sigue verde porque `frontend.entry: ./frontend` existe en disco ✅

---

## §12. Risks / open questions

- **R1 — Workspace path en Docker/K8s**: `_WORKSPACE_ROOT = Path(__file__).parents[4] / "src" / "plugins"` asume que la imagen Docker preserva la estructura de directorios `hubara_agency/src/plugins/`. Esto es correcto en el `Dockerfile` actual (copia el repo entero), pero debe verificarse. Mitigación: agregar una smoke assertion en el boot que verifica que al menos un workspace dir existe; o exponerlo como env var `PLUGINS_CODE_DIR`. **Pendiente: verificar en docker local antes de ship.**

- **R2 — `AgentsList` agrupa por `category`**: El campo `category` se quita del modelo `Agent`. El componente `AgentsList.tsx` actualmente hace `agents.forEach(a => { groups.get(a.category) })`. Con el nuevo tipo sin `category`, la compilación TypeScript fallará si no se actualiza el componente. Opciones: (a) retornar `category` derivado desde el API (`"Conversacional"` para sales, `"Conversacional"` para remarketing), o (b) refactorizar grouping a `plugin_id`. Ver §15 A4 para la decisión adoptada (opción a).

- **R3 — `AgentsInspector.tsx` usa `calls`/`csat`**: No se leyó el contenido de `AgentsInspector.tsx`. Si renderiza métricas del `Agent.calls` o `Agent.csat` y estos llegan `null`, el componente podría mostrar `null` en texto o romper el render. **Pendiente: verificar `AgentsInspector.tsx` antes de ship.** Mitigación: manejar `null` con `calls ?? "—"`.

- **R4 — `role` extracción de IDENTITY.md**: Se asume que la primera línea no-heading y no-vacía de `IDENTITY.md` es el `role`. Si el archivo comienza directamente con un heading `# Título`, se toma la segunda línea no-vacía. Fragil si el formato cambia. Alternativa más robusta: retornar la primera oración (split por `.`). Decisión en §15 A3.

- **R5 — skills glob y symlinks**: `workspace/skills/*/skill.md` — si el glob encuentra symlinks o archivos no-UTF8, la lectura puede fallar. Mitigación: envolver en `try/except` por skill individual; si falla → omitir esa skill del dict sin bajar el endpoint.

- **O1 — ¿`display_name` debería venir del manifest?**: El manifest `chats` tiene `display_name: Chats` (el plugin). El worker se llama `sales`. ¿El `display_name` del agente es `"Sales"` (worker_name capitalizado) o `"Chats — Sales"` (plugin + worker)? Asumido: `worker_name.capitalize()`. Reversibilidad alta.

---

## §13. Out of scope (técnico)

- NO agregar campo `agentic: true` al `plugin.schema.yaml` (requiere ADR separado; se evita en esta HU).
- NO crear K8s manifest nuevo (agents_admin sigue sin workers).
- NO regenerar `docker-compose.local.yml` (agents_admin no tiene compose workers).
- NO agregar provider a `app/providers/index.tsx`.
- NO agregar tokens Tailwind nuevos.
- NO agregar icons nuevos a `shared/ui/Icon.tsx` (se usan `bolt`, `calendar`, `bot` existentes; ver §15 A4).
- NO refactorizar el componente `AgentsList` más allá de lo necesario para cambiar el grouping.
- NO eliminar la constante `PROMPT_SECTIONS` de `AgentsPrompts.tsx` — se reutiliza.
- NO tocar tests de architecture `hubara_agency/tests/architecture/` ni `frontend_dashboard/src/test/architecture/`.

---

## §14. Iteration changelog

N/A — iteración 1.

---

## §15. Assumptions made

- **A1 (schema change avoided):** Asumí que `agentic: true` en el schema no se agrega (requiere ADR). En su lugar, el endpoint filtra plugins con `manifest.get("agent", {}).get("workers") or []` no vacío. Default chosen: derivar de estructura existente. Reversibilidad: alta — si se quiere el campo explícito en el futuro, se agrega en un PR de architecture-change y la lógica del endpoint lo puede usar como override.

- **A2 (endpoint path `/api/agents`):** Asumí `prefix: /api` en el manifest + `@router.get("/agents")` → monta en `/api/agents`. Consistente con el patrón de `chats.api.dashboard` que usa `prefix: /api/dashboard`. Reversibilidad: alta.

- **A3 (role extraction):** Asumí que el `role` se extrae como la primera línea de contenido no-heading no-vacía de `IDENTITY.md`. Si IDENTITY.md empieza con `# Heading` y luego la descripción → la descripción es el `role`. Si no hay tal línea → `role = worker_name.capitalize()`. Reversibilidad: alta.

- **A4 (icon/color/category derivados en backend):** Asumí la siguiente tabla de derivación:

  | worker_name | icon | color | category |
  |---|---|---|---|
  | `sales` | `"bolt"` | `"green"` | `"Conversacional"` |
  | `remarketing` | `"calendar"` | `"blue"` | `"Conversacional"` |
  | cualquier otro | `"bot"` | `"purple"` | `"General"` |

  Reversibilidad: alta (cambio en routes.py solo).

- **A5 (usePersonalities deprecated):** Asumí que `usePersonalities()` y la constante `PERSONALITIES` se eliminan de `entities/agent/api.ts`. `AgentsPrompts.tsx` se actualiza para usar `agent.prompts` directamente en lugar de buscar en `PERSONALITIES`. El `Personality` interface y `PersonalityKey` type se eliminan de `model.ts`. Reversibilidad: baja (es un cambio de comportamiento — el mock de personalities desaparece). Si el operador quiere mantenerlos, la alternativa es dejar `usePersonalities()` retornando un array derivado de `useAgents()` pero eso es over-engineering.

- **A6 (skills en modelo pero no renderizados):** Asumí que `skills: Record<string, string>` aparece en el `Agent` type y en la respuesta del API, pero `AgentsPrompts.tsx` no renderiza skills en esta HU. La HU AC dice "también trae los skills.md" (la API los incluye), no "también muestra los skills" (el UI los renderiza). Reversibilidad: alta.

- **A7 (status siempre "online"):** El campo `status: "online"` viene hardcodeado desde el backend. No hay mecanismo de detección de estado en tiempo real. Reversibilidad: alta.

- **A8 (workspace_root inyectable para tests):** Asumí que el handler `list_agents` acepta `workspace_root: Path = _WORKSPACE_ROOT` como parámetro inyectable para facilitar tests sin patchear Path(__file__). FastAPI acepta dependencias Depends() para esto, pero dado que `workspace_root` es una Path constante (no un servicio), se pasa como default arg con override en tests. Reversibilidad: alta.

---

## §16. Spec deltas — capability behavior changes

| Capability | Path delta | Status | Resumen |
|---|---|---|---|
| `plugins/agents_admin` | `$ARTIFACTS_DIR/spec-deltas/plugins/agents_admin/spec.md` | seed_inline (no existía) | Bootstrap spec: agents_admin pasa de frontend-only a frontend+API; expone GET /api/agents con workspace content real |
| `entities/agent` (frontend) | (inline en delta de agents_admin) | seed_inline | useAgents() → real API; usePersonalities() deprecado; Agent type actualizado |
