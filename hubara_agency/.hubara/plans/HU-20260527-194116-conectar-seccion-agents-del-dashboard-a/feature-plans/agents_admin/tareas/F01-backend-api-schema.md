# Task F01 — Backend API: router GET /api/agents_admin + schema agentic

- Slug: backend-api-schema
- HU id: HU-20260527-194116-conectar-seccion-agents-del-dashboard-a
- Plugin id: agents_admin
- Plugin template: B (migración A → B)
- Refinement source: artifacts/runs/b9caeb73e09972e8688c06b65b13c134/hu-refinada.md
- Planner: hubara-feature-planner-archon
- Date: 2026-05-27
- Iteration: 1
- Estimated LOC: 175 (sin tests: ~105)
- Risk: low

---

## §1. Context

Delivers acceptance criterion(s) (verbatim from refinement §1):
- **AC-3:** Given que existen plugins no agénticos (catalog, orders, eta, agents_admin), when el endpoint GET /api/agents responde, then esos plugins NO aparecen en la lista de agentes.
- **AC-5:** Given que el campo `agentic: true` está ausente o es false en el plugin.yaml de un plugin que sí tiene sección `agent:`, when se consulta el endpoint, then ese plugin tampoco aparece en la lista de agentes (discriminador explícito — filtra catalog que tiene agent: pero no es conversacional).

Refinement sections informaron esta task: §3.1, §3.1.4, §3.2, §3.4, §9, §11, §12.

Code anchors del refinement (relevantes a esta task):
- Pattern: `router = APIRouter()` + `list_agents()` at `§3.1.4` — forma canónica del endpoint
- File to create: `hubara_agency/src/plugins/agents_admin/api/routes.py`
- File to create: `hubara_agency/src/plugins/agents_admin/api/__init__.py`
- Sibling canónico: `hubara_agency/src/plugins/chats/api/dashboard.py` — convenciones de router

Assumptions del refinement §15 que afectan esta task:
- A4: prefix `/api/agents_admin` (consistente con convention prefix: /api/<plugin_id>) | reversibility: alta
- A2: Extracción name/role de IDENTITY.md — primer `#` heading → name, primer párrafo → role | reversibility: alta
- A9: Workspace file missing → empty string | default: empty string | reversibility: alta
- A10: enumerate_manifest_workers() ignora ENABLED_PLUGINS (muestra todos los plugins agénticos) | reversibility: alta

**FLAG OPERADOR (schema):** Esta task modifica `plugin.schema.yaml` (spinal, nota de ADR requerida).
El campo `agentic` es aditivo (optional boolean, default false). El guard `schema_and_feature_same_pr`
del plugin-manifest exige que schema + chats/plugin.yaml vayan en el mismo PR (sin el schema,
`plugins-sync.ts` falla la validación con additionalProperties: false). El implementer debe
confirmar con el operador antes de mergear: Opción A = mismo PR (recomendada), Opción B = PR de
arquitectura previo. NO bloquear la task por esto — implementar y flag al final.

---

## §2. Dependencies

- depends_on: []
- blocks: ["F03"]
- Inherits from upstream: nada (es foundation task)
- Cross-plugin dependency: plugin `chats` debe tener `agentic: true` en su `plugin.yaml` (trabajo del plugin chats en batch B1 paralelo). El test funcional puede mockear el manifest si chats no está disponible aún.
- Backend dependency: none

---

## §3. Files affected

| Path | Acción | Rol | LOC budget |
|---|---|---|---|
| `hubara_agency/src/plugins/agents_admin/api/__init__.py` | new | anchor de módulo | ~5 |
| `hubara_agency/src/plugins/agents_admin/api/routes.py` | new | router FastAPI GET /api/agents_admin | ~80 |
| `hubara_agency/tests/plugins/agents_admin/__init__.py` | new | test package anchor | ~0 |
| `hubara_agency/tests/plugins/agents_admin/api/__init__.py` | new | test package anchor | ~0 |
| `hubara_agency/tests/plugins/agents_admin/api/test_routes.py` | new | tests funcionales del endpoint | ~70 |
| `frontend_dashboard/src/plugins/agents_admin/plugin.yaml` | modify | agregar bloque `api:` | +10 |
| `frontend_dashboard/src/plugins/_schema/plugin.schema.yaml` | modify (spinal — operator flag) | agregar campo `agentic: boolean` | +10 |

---

## §4. Boundary DTOs (R-JSON)

N/A — Template B. Sin Temporal. La serialización es FastAPI → JSON automático (dicts/lists de Python).
No hay DTOs cross-workflow/activity boundary. No se toca `platform/contracts.py`.

---

## §5. Snippets canónicos

```python
# canonical — hubara_agency/src/plugins/agents_admin/api/__init__.py
"""Router FastAPI para agents_admin — Template B (API HTTP, sin workers Temporal)."""
```

```python
# canonical — hubara_agency/src/plugins/agents_admin/api/routes.py
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
    "users":    "USER.md",   # file USER.md maps to key "users"
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
        for worker in manifest.get("agent", {}).get("workers", []):
            w_name = worker.get("name", "")
            workspace = _read_workspace(plugin_id, w_name)
            result.append({
                "id": f"{plugin_id}:{w_name}",
                "plugin_id": plugin_id,
                "worker_name": w_name,
                "name": _extract_name(workspace["identity"], w_name),
                "role": _extract_role(workspace["identity"], w_name),
                "workspace": workspace,
            })
    return result
```

```python
# canonical — _read_workspace (dentro de routes.py)
def _read_workspace(plugin_id: str, worker_name: str) -> dict:
    base = _PLUGINS_PYTHON_DIR / plugin_id / "agent" / worker_name / "workspace"
    content: dict = {}
    for key, filename in _WORKSPACE_FILES.items():
        filepath = base / filename
        content[key] = filepath.read_text(encoding="utf-8") if filepath.exists() else ""
    skills = []
    skills_dir = base / "skills"
    if skills_dir.is_dir():
        for skill_dir in sorted(skills_dir.iterdir()):
            if skill_dir.is_dir():
                sf = skill_dir / "skill.md"
                if sf.exists():
                    skills.append({"name": skill_dir.name,
                                   "content": sf.read_text(encoding="utf-8")})
    content["skills"] = skills
    return content

def _extract_name(identity_text: str, fallback: str) -> str:
    for line in identity_text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback.capitalize()

def _extract_role(identity_text: str, fallback: str) -> str:
    in_body = False
    for line in identity_text.splitlines():
        if line.startswith("# "):
            in_body = True
            continue
        if in_body and line.strip():
            return line.strip()[:120]
    return fallback.capitalize()
```

```yaml
# canonical — plugin.yaml: agregar bloque api: (junto a frontend: existente)
api:
  python_module: src.plugins.agents_admin.api.routes
  prefix: /api/agents_admin
  tags: [AgentsAdmin]
```

```yaml
# canonical — plugin.schema.yaml: agregar en properties: (operator-flagged)
  agentic:
    type: boolean
    description: |
      Marca que el plugin alberga agentes conversacionales con workspace
      de prompts (IDENTITY.md, SOUL.md, TOOLS.md, AGENTS.md, USER.md).
      Solo los plugins con agentic=true aparecen en GET /api/agents_admin.
      Aplica independientemente de si el plugin tiene sección `agent:` —
      un plugin puede tener workers Temporal no conversacionales (catalog).
    default: false
```

```python
# canonical — tests/plugins/agents_admin/api/test_routes.py (shape)
import pytest
from httpx import AsyncClient

@pytest.mark.functional
async def test_list_agents_returns_agentic_only(api_client: AsyncClient, tmp_path, monkeypatch):
    # Setup: mock enumerate_manifest_workers y load_manifest
    # Assert: solo agents del plugin chats aparecen
    response = await api_client.get("/api/agents_admin")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    plugin_ids = {a["plugin_id"] for a in data}
    assert "chats" in plugin_ids
    assert "catalog" not in plugin_ids

@pytest.mark.functional
async def test_list_agents_includes_workspace_content(api_client: AsyncClient, tmp_path, monkeypatch):
    # Setup: workspace real con IDENTITY.md no vacío
    # Assert: data[0]["workspace"]["identity"] no es cadena vacía (gotcha #1 CLAUDE.md)
    ...

@pytest.mark.functional
async def test_list_agents_excludes_plugins_without_agentic_flag(api_client: AsyncClient, monkeypatch):
    # catalog tiene agent: pero agentic: false → no aparece (AC-5)
    ...

@pytest.mark.functional
async def test_list_agents_includes_skills_from_subdirectory(api_client: AsyncClient, tmp_path, monkeypatch):
    # skills_dir populated → data[0]["workspace"]["skills"] lista no vacía
    ...

@pytest.mark.functional
async def test_list_agents_returns_empty_string_for_missing_workspace_file(api_client: AsyncClient, tmp_path, monkeypatch):
    # workspace dir existe pero falta SOUL.md → workspace.soul == ""
    ...
```

---

## §6. Workspace deltas

N/A — `agents_admin` no tiene workspace propio. El plugin LEE workspaces ajenos vía Path; no tiene IDENTITY.md, SOUL.md, etc. propios.

---

## §7. Composition wiring

N/A — el router no necesita factory (no inyecta LLM ni Temporal). Lee filesystem directamente con Path.

---

## §8. Worker registration

N/A — Template B. Sin workers Temporal, sin `register_tool_extension`.

---

## §9. Tests

| Test file | New/modify | Scenarios |
|---|---|---|
| `hubara_agency/tests/plugins/agents_admin/api/test_routes.py` | new | agentic filter, workspace content real, skills subdir, missing file fallback |

Test name list:
- `tests/plugins/agents_admin/api/test_routes.py::test_list_agents_returns_agentic_only`
- `tests/plugins/agents_admin/api/test_routes.py::test_list_agents_includes_workspace_content`
- `tests/plugins/agents_admin/api/test_routes.py::test_list_agents_excludes_plugins_without_agentic_flag`
- `tests/plugins/agents_admin/api/test_routes.py::test_list_agents_includes_skills_from_subdirectory`
- `tests/plugins/agents_admin/api/test_routes.py::test_list_agents_returns_empty_string_for_missing_workspace_file`

**Nota de comportamiento real (CLAUDE.md gotcha #1):** el test `test_list_agents_includes_workspace_content` debe verificar que `data[0]["workspace"]["identity"]` no es cadena vacía cuando el workspace existe — no solo que el schema lo permite.

---

## §10. Verification commands

```bash
# Tests del endpoint
cd hubara_agency && uv run pytest tests/plugins/agents_admin/ -xvs

# Premortem invariants (Template B no tiene worker — no debe romper invariants)
cd hubara_agency && uv run pytest tests/plugins/ -v

# Architecture gate
cd hubara_agency && uv run pytest -m architecture --tb=short

# R-DIP: routes.py no importa de src.plugins.chats.* — solo Path filesystem
cd hubara_agency && uv run lint-imports

# Smoke: endpoint responde con datos reales
cd hubara_agency && ENABLED_PLUGINS=agents_admin,chats uv run python run_api.py &
sleep 3 && curl http://localhost:8000/api/agents_admin | python3 -m json.tool | head -40
# Esperado: lista JSON con 2 agentes (sales + remarketing) con workspace real

# AC-3/AC-5: catalog NO aparece
curl http://localhost:8000/api/agents_admin | python3 -c "
import json,sys; data=json.load(sys.stdin)
pids = {a['plugin_id'] for a in data}; print('plugin_ids:', pids)
assert 'catalog' not in pids, 'FALLO: catalog no debe aparecer'
print('OK: catalog excluido')
"

# Frontend: plugins:sync sigue funcionando con nuevo campo en schema
cd frontend_dashboard && npm run plugins:sync

# Render-compose drift (Template B no modifica compose, pero verificar)
cd hubara_agency && uv run python scripts/render-compose.py && git diff --exit-code docker-compose.local.yml
```

---

## §11. Definition of Done

- [ ] `api/__init__.py` y `api/routes.py` creados.
- [ ] `plugin.yaml` de agents_admin tiene bloque `api:` con `python_module`, `prefix: /api/agents_admin`, `tags: [AgentsAdmin]`.
- [ ] `plugin.schema.yaml` tiene campo `agentic: boolean` (ver FLAG OPERADOR en §1 — confirmar con operador).
- [ ] 5 tests en `test_routes.py` corren con `exit 0`.
- [ ] `uv run pytest tests/plugins/ -v` exit 0 (premortem invariants verdes).
- [ ] `uv run pytest -m architecture` exit 0.
- [ ] `uv run lint-imports` exit 0 (R-DIP: routes.py usa solo Path filesystem, no imports de chats.* ni sibling plugins).
- [ ] `npm run plugins:sync` exit 0 (schema actualizado no rompe el validator).
- [ ] `render-compose.py` exit 0 y `docker-compose.local.yml` sin drift.
- [ ] Smoke test endpoint retorna 2 agentes con workspace real no vacío.
- [ ] FLAG OPERADOR: implementer documenta en PR description la decisión schema (Opción A o B).

---

## §12. Hard rules check

- **R-DET:** N/A — no hay workflows Temporal.
- **R-JSON:** N/A — no hay boundary workflow↔activity. FastAPI serializa dicts/lists nativamente.
- **R-STATELESS:** N/A — no hay activities.
- **R-HEARTBEAT:** N/A — GET /api/agents_admin es O(archivos × KB), ~10ms. Sin riesgo de timeout.
- **R-DIP:** Applies. `routes.py` importa solo de `src.platform.plugin_manifest` (platform → plugin: correcto). NO importa de `src.plugins.chats.*` — lee filesystem vía Path (I/O, no import Python). `lint-imports` debe pasar sin cambios al `.importlinter`.
- **R-DIP #10 cross-worker:** N/A — Template B sin workers.
- **Orchestration footguns:** N/A — Template B.
- **FSD layering:** N/A (es backend puro). Plugin.yaml `api:` block es la declaración SSoT.
- **Manifest = SSoT:** Applies. El endpoint se declara en `agents_admin/plugin.yaml` bajo `api:`. El campo `agentic` se declara en `plugin.schema.yaml` y en `chats/plugin.yaml`. El loader FastAPI descubre automáticamente el router.

---

## §13. Open questions / risks

- **Risk: plugin.schema.yaml es spinal con nota ADR.** El campo `agentic` es aditivo — el guard `schema_and_feature_same_pr` exige que vayan juntos. Operador decide Opción A vs B antes de mergear. Default recomendado: Opción A (mismo PR).
- **Risk: enumerate_manifest_workers() semántica.** Verificar que la función itera todos los plugins (no solo los habilitados por ENABLED_PLUGINS) antes de filtrar por `agentic`. Si filtra por ENABLED_PLUGINS, el endpoint puede devolver resultados distintos según el entorno. (A10 asume que no filtra.)
- **Risk: workspace path en K8s/Docker.** El path `src/plugins/chats/agent/sales/workspace/` debe existir en el container. Verificar que el Dockerfile incluye los workspace .md o que el PVC los monta correctamente. _(Flag para deploy review, fuera de scope de esta task.)_
- **Risk: PROMPT_SECTIONS key mismatch.** El backend usa la key `"users"` (mapeada desde `USER.md`). Verificar que `PROMPT_SECTIONS` en `model.ts` usa `key: "users"` (no `"user"`). Esto afecta a F02/F03 pero el implementer debe coordinar con el explorador del frontend.

---

## §14. Wiring intents (spinal files)

```yaml
wiring_intents:
  frontend_dashboard/src/plugins/_schema/plugin.schema.yaml:
    - kind: yaml_dict_keys_append
      name: agentic
      definition: |
        agentic:
          type: boolean
          description: |
            Marca que el plugin alberga agentes conversacionales con workspace
            de prompts (IDENTITY.md, SOUL.md, TOOLS.md, AGENTS.md, USER.md).
            Solo los plugins con agentic=true aparecen en GET /api/agents_admin.
            Aplica independientemente de si el plugin tiene sección `agent:` — un
            plugin puede tener workers Temporal no conversacionales (e.g. catalog).
          default: false
      order_hint: alphabetical_by_name
      operator_flag: "SCHEMA_CHANGE — confirmar Opción A vs B con operador antes de PR"
```
