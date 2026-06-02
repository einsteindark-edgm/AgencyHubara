# Task F01 — Agregar agentic: true al manifest de chats

- Slug: chats-manifest-agentic
- HU id: HU-20260527-194116-conectar-seccion-agents-del-dashboard-a
- Plugin id: chats
- Plugin template: D (se mantiene D; el work type es manifest-only)
- Refinement source: $ARTIFACTS_DIR/hu-refinada.md (§3.4, §0)
- Planner: hubara-feature-planner-archon
- Date: 2026-05-27
- Iteration: 1
- Estimated LOC: 3
- Risk: low

---

## §1. Context

Delivers acceptance criterion(s):

- **AC-1:** El plugin chats aparece en GET /api/agents_admin porque tiene `agentic: true` — el endpoint filtra sobre este campo. Sin el campo, ningún agente aparece.
- **AC-3:** Plugins sin `agentic: true` (catalog, orders, eta, agents_admin) quedan excluidos — el discriminador vive en el manifest de cada plugin, no en el código del endpoint.
- **AC-5:** El campo `agentic` es discriminador explícito — un plugin con sección `agent:` pero sin `agentic: true` (e.g. catalog) no aparece. Esto separa workers conversacionales (chats) de workers Temporal no conversacionales.

Refinement sections que informaron esta task: §0 (Plugin classification), §3.4 (Manifest changes), §11 (Hard rules), §12 risk #1 (schema ADR risk).

Code anchors del refinement relevantes:

- Pattern: `agentic: true` en top-level de `plugin.yaml` (junto a `id`, `version`) — from refinement §3.4.
- File to extend: `frontend_dashboard/src/plugins/chats/plugin.yaml` (línea 5 área top-level).
- File dependency: `frontend_dashboard/src/plugins/_schema/plugin.schema.yaml` debe tener `agentic` en `properties:` ANTES de que `plugins:sync` corra con éxito.

Assumptions del refinement §15 que afectan esta task:

- **A4:** prefix `/api/agents_admin` — no afecta directamente esta task (el manifest de chats no declara API propia aquí), pero es el endpoint que consumirá el flag.

**Estado observado en worktree:** al leer `chats/plugin.yaml` durante la planeación, el campo `agentic: true` ya está presente en línea 5. El implementer DEBE verificar el estado real vs rama base antes de decidir si editar o solo validar.

---

## §2. Dependencies

- depends_on: []
- blocks: []
- Inherits from upstream: N/A (task foundation, no predecessor)
- Cross-plugin dependency: `agents_admin` debe haber agregado `agentic` a `plugin.schema.yaml` antes de que `npm run plugins:sync` pueda validar sin error. En el worktree actual el schema ya tiene el campo (mismo batch paralelo).
- Backend dependency: ninguna (este cambio no depende de ningún endpoint previo)

---

## §3. Files affected

| Path | Acción | Rol | LOC budget |
|---|---|---|---|
| `frontend_dashboard/src/plugins/chats/plugin.yaml` | modify | manifest discriminador | +1~3 |

Sin archivos nuevos. Sin archivos Python. Sin archivos de frontend del plugin chats.

**Nota schema:** `frontend_dashboard/src/plugins/_schema/plugin.schema.yaml` es tocado por `agents_admin` (mismo batch) — NO por esta task de chats. El implementer NO debe editar el schema desde esta task.

---

## §4. Boundary DTOs (R-JSON)

N/A — no hay Temporal en esta task. Sin DTOs.

---

## §5. Snippets canónicos

```yaml
# canonical — frontend_dashboard/src/plugins/chats/plugin.yaml (diff)
# Agregar después de `description:` y antes de `depends_on:`

agentic: true  # workers conversacionales con workspace IDENTITY/SOUL/TOOLS/AGENTS/USER
```

Verificar posición: el campo debe quedar en el bloque top-level del manifest, ANTES del primer bloque de sección (`depends_on:`, `frontend:`, `api:`, `agent:`).

**Si el campo ya está presente (observado en worktree actual):** no editar. Proceder directamente a §10 verification commands.

---

## §6. Workspace deltas

N/A — `chats` tiene workspaces reales (`agent/sales/workspace/`, `agent/remarketing/workspace/`) pero esta task NO los modifica. El endpoint de `agents_admin` los lee vía filesystem — no requieren cambio para que AC-1/AC-2 funcionen.

---

## §7. Composition wiring

N/A — sin cambios en composition.py.

---

## §8. Worker registration

N/A — sin cambios en workers Temporal de chats.

---

## §9. Tests

| Test file | New/modify | Scenarios |
|---|---|---|
| `frontend_dashboard/src/plugins/_schema/plugin.schema.yaml` | (owned by agents_admin) | validación indirecta vía plugins:sync |

No hay tests nuevos para esta task. La validación es structural:

- `npm run plugins:sync` valida que `chats/plugin.yaml` pasa el JSON Schema (additionalProperties: false).
- `npx tsc -b` confirma que ningún tipo TS se rompió.
- `npm run build` confirma que el bundle compile sin error.

Los tests funcionales del comportamiento real (que el endpoint emita agents con workspace) viven en `agents_admin` plugin (tests/plugins/agents_admin/api/test_routes.py).

---

## §10. Verification commands

```bash
# Sync + schema validation (PRINCIPAL — falla si agentic no está en schema)
cd frontend_dashboard && npm run plugins:sync

# Type check
cd frontend_dashboard && npx tsc -b

# Build prod
cd frontend_dashboard && npm run build

# Architecture gate frontend
cd frontend_dashboard && npm run test:arch

# Render-compose drift check (no debería cambiar — plugin chats no tiene workers nuevos)
cd hubara_agency && uv run python scripts/render-compose.py && \
  git diff --exit-code docker-compose.local.yml

# Backend architecture gate (sin cambios Python pero gate de sanidad)
cd hubara_agency && uv run pytest -m architecture --tb=short
cd hubara_agency && uv run lint-imports
```

**Orden recomendado:** `plugins:sync` primero (falla rápido si schema no tiene `agentic`); luego `tsc -b`; luego `build`.

---

## §11. Definition of Done

- [ ] `frontend_dashboard/src/plugins/chats/plugin.yaml` tiene `agentic: true` en top-level.
- [ ] `npm run plugins:sync` exit 0 (schema valida sin errores).
- [ ] `npx tsc -b` exit 0.
- [ ] `npm run build` exit 0.
- [ ] `npm run test:arch` exit 0.
- [ ] `render-compose.py && git diff --exit-code docker-compose.local.yml` exit 0 (no drift).
- [ ] `uv run pytest -m architecture` exit 0.
- [ ] `uv run lint-imports` exit 0.
- [ ] No edits a `tests/architecture/`, `.importlinter`, `R_*_EXEMPTIONS`,
      `.dependency-cruiser.cjs`, `.archon/workflows/`, `.claude/skills/hubara-*`.

---

## §12. Hard rules check (R-rules + FSD + manifest)

- **R-DET:** N/A — no hay workflows Temporal en esta task.
- **R-JSON:** N/A — no hay boundary workflow↔activity.
- **R-STATELESS:** N/A — sin activities.
- **R-HEARTBEAT:** N/A — sin activities.
- **R-DIP:** N/A — sin cambios Python. El manifest YAML no crea dependencias de import.
- **R-DIP #10 cross-worker:** N/A — esta task no modifica workflows ni transitions.
- **Orchestration footguns:** N/A — no se modifica ningún Input dataclass ni transition.
- **FSD layering:** N/A — sin cambios TS.
- **Manifest = SSoT:** Applies — `agentic: true` en `chats/plugin.yaml` es la única fuente de verdad para que el plugin sea tratado como agéntico. El endpoint `GET /api/agents_admin` lo lee con `load_manifest(plugin_id).get("agentic", False)`. No hay hardcode en Python. ✓

---

## §13. Open questions / risks

- **Risk #1 (schema ADR):** `plugin.schema.yaml` es spinal con nota "Cambios al schema requieren ADR". El campo `agentic` es aditivo (booleano opcional, default false). La nota en plugin-manifest.yaml y hu-refinada.md §12 reconoce esto y lo trata como un cambio que puede ir junto al feature. El implementer debe flag al operador si el branch requiere un PR previo para el schema.

- **Risk #2 (estado previo del campo):** El campo `agentic: true` ya está presente en `chats/plugin.yaml` en el worktree actual. Posibles causas: (a) agents_admin lo agregó como side-effect de su implementación (cross-plugin edit no autorizado), (b) ya estaba en la rama base antes de la HU. El implementer debe verificar con `git log -1 --follow frontend_dashboard/src/plugins/chats/plugin.yaml` para determinar origen. Si lo agregó agents_admin, no hay problema funcional pero sí un gotcha de ownership. Si ya estaba, el trabajo de esta task es verificación solamente.

- **Risk #3 (plugins:sync dependency):** Si el implementer corre `plugins:sync` ANTES de que `agents_admin` haya agregado `agentic` al schema en el mismo branch, el sync fallará con `additionalProperties: false`. Orden correcto: primero agents_admin task F02 (schema), luego esta task F01.
