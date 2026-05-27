HU id: HU-20260527-012300-conectar-seccion-agents-del-dashboard-a

# Conectar sección Agents del dashboard a datos reales del backend

# Conectar sección Agents del dashboard a datos reales del backend

Como **operador del dashboard**,
quiero **ver en la sección Agents la personalidad real de cada agente IA (sales y remarketing), leyendo los archivos `.md` del workspace del servidor**,
para **auditar y monitorear el comportamiento de los agentes sin acceder directamente al filesystem del backend**.

## Acceptance criteria

- **Given** que el plugin `chats` tiene `agentic: true` en su `plugin.yaml` y los sub-agentes `sales` y `remarketing` tienen archivos en `agent/<name>/workspace/*.md`, **when** el frontend carga la sección Agents, **then** muestra exactamente esos dos agentes (sales y remarketing) en la lista, con nombre y rol derivados del workspace.
- **Given** un operador selecciona el agente `sales` en la lista, **when** se abre el panel de prompts, **then** el contenido de cada sección (Identity, Soul, Tools, Agents, Users) refleja el texto real de los archivos `IDENTITY.md`, `SOUL.md`, `TOOLS.md`, `AGENTS.md`, `USER.md`, del workspace de sales. tambien trae los skills.md que hay en workspace/skills/*/skill.md
- **Given** que existen plugins no agénticos (`catalog`, `orders`, `eta`, `agents_admin`), **when** el endpoint `GET /api/agents` responde, **then** esos plugins NO aparecen en la lista de agentes.
- **Given** que los archivos `.md` del workspace cambian en el servidor (nuevo deploy), **when** el operador recarga la sección Agents, **then** el contenido actualizado se muestra (sin caché infinito en el frontend).
- **Given** que el campo `agentic: true` está ausente o es `false` en el `plugin.yaml` de un plugin que sí tiene sección `agent:`, **when** se consulta el endpoint, **then** ese plugin tampoco aparece en la lista de agentes.

## Out of scope

- Editar o guardar cambios en los archivos `.md` del workspace desde el dashboard.
- Hot-reload automático al modificar archivos sin recargar la sección.
- Métricas de uso (`calls`, `csat`) en tiempo real — esos campos pueden quedar mock o como `null` hasta una HU de observabilidad.
- Soporte para plugins agénticos distintos de `chats` (no existen hoy).
- Versionado o historial de cambios de los prompts.

## Notas técnicas (opcional)

- **Plugin manifest (`plugin.yaml`)**: agregar campo `agentic: true` (boolean, default `false`) al schema en `frontend_dashboard/src/plugins/_schema/plugin.schema.yaml`. Solo `chats` lo recibe en esta HU.
- **Nuevo endpoint FastAPI**: el plugin `agents_admin` pasa de template A (frontend-only) a template B (frontend + API). Crear `hubara_agency/src/plugins/agents_admin/api/` con router `GET /api/agents` que: (1) escanea plugins con `agentic: true` via `plugin_manifest.py`, (2) detecta sub-agentes desde `agent.workers[]` del manifest, (3) lee los `.md` del workspace en `hubara_agency/src/plugins/<plugin>/agent/<worker>/workspace/` y los devuelve como JSON.
- **Workspace paths conocidos**: `src/plugins/chats/agent/sales/workspace/{IDENTITY,SOUL,TOOLS,AGENTS,USER}.md` y equivalentes en `remarketing/workspace/`. tambien para `src/plugins/chats/agent/sales/workspace/skills/*/skill.md`
- **Frontend**: reemplazar el mock `AGENTS` + `PERSONALITIES` hardcodeados en `frontend_dashboard/src/entities/agent/api.ts` por `useQuery` real contra el nuevo endpoint. El modelo `PersonalityPrompt` ya mapea `agents/identity/soul/tools/users` que coincide con los nombres de los `.md`.
- **`AgentsPrompts.tsx`** ya renderiza secciones por `PROMPT_SECTIONS` — solo necesita datos reales, no cambios estructurales.
