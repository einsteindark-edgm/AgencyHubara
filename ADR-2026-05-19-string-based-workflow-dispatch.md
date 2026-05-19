# ADR-2026-05-19 — String-Based Workflow Dispatch + Enforcement Pipeline-Wide

**Estado:** Propuesto
**Owner:** Operador (asignar al implementer)
**Estimación:** 1.5–2 días backend + 1 día pipeline/skills/tests
**Pipeline target:** `archon workflow run hu-hubara-pipeline "ADR-string-dispatch"`

---

## §0. TL;DR

El use case `sales/use_cases/load_or_start_sales_session.py` importa
`RemarketingSessionWorkflow` directamente desde `chats/agent/remarketing/`.
Esto **viola R-DIP #10** (agentes hermanos deben ser independientes), está
**reconocido como deuda** en `hubara_agency/.importlinter` con
`ignore_imports` + comment "Known debt — do NOT add more without ADR".

**Plan:**

1. Extender el schema `plugin.yaml` con `workers[].workflow_classes:`.
2. Crear helper `src.platform.plugin_manifest.get_workflow_name(plugin, worker)`.
3. Refactorear el use_case a dispatch por string + eliminar el import.
4. Remover las entries `ignore_imports` del `.importlinter`.
5. **Pipeline enforcement** — para que esto no vuelva a pasar:
   - Test arquitectural nuevo que falla CI si vuelve a aparecer el patrón.
   - Sección nueva en el skill `hubara-architecture-guide` con la regla
     + ejemplo correcto/incorrecto.
   - Checklist en `hubara-implementer-archon` que el AI debe verificar
     antes de emitir `task-result.yaml status=passed`.
   - Bullet específico en `agent-deha-compliance` de `review-pr-hubara` +
     auto-fix recipe.

Cuando esté terminado, **R-DIP #10 cumple sin excepciones** y los pipelines
agéntico + de revisión están vacunados contra esta clase de bug.

---

## §1. Contexto

### §1.1 El antipatrón

```python
# hubara_agency/src/plugins/chats/agent/sales/use_cases/load_or_start_sales_session.py:54
from src.plugins.chats.agent.remarketing.workflows.remarketing import (
    RemarketingSessionWorkflow,    # ← R-DIP #10 VIOLATION (sibling agent import)
)

# Línea 144:
workflow_class: type = RemarketingSessionWorkflow

# Línea 226:
handle = await client.start_workflow(
    workflow_class,                                    # ← class import
    workflow_id=workflow_id,
    task_queue=get_task_queue("chats", "remarketing"), # ← task_queue OK (manifest-driven)
)
```

### §1.2 Por qué está mal arquitectónicamente

R-DIP #10 (`hubara_agency/.importlinter` contracto `agents-independent`):

> Agent siblings stay isolated. Cross-agent flow goes through
> `src.platform.*`. If you need shared code, lift it to `src.platform/` —
> never import a sibling agent.

| Problema | Consecuencia |
|---|---|
| **Acoplamiento de código** | Renombrar `RemarketingSessionWorkflow` rompe `sales` |
| **Disable selectivo imposible** | `ENABLED_PLUGINS=chats:sales` (sin remarketing) → import error en boot |
| **Ciclos latentes** | Si remarketing alguna vez necesita algo de sales → cycle |
| **Bloquea extracción** | "Quiero mover remarketing a un plugin propio" → refactor masivo |

### §1.3 La deuda ya está documentada

`hubara_agency/.importlinter` líneas ~95-105:

```ini
; Known debt — documented exceptions (do NOT add more without ADR):
;
;   2. sales.use_cases.load_or_start_sales_session imports
;      RemarketingSessionWorkflow as a TYPE for `workflow_class: type` so it
;      can call `start_workflow(<class>, ...)`. Long-term fix: same as #1 —
;      when workflow registry lives in platform/, the use-case branches on a
;      string key and the cross-agent import disappears.
```

Este ADR es ese fix.

### §1.4 Por qué el manifest `invokes:` no resuelve esto solo

El commit `9ccfacd` agregó `workers[].invokes:` declarativo. Eso documenta
la relación, pero el **código sigue importando la clase**. El manifest es
**doc only** hasta que un mecanismo runtime use esa info para resolver
workflows por string.

Este ADR cierra el loop: agregar el mecanismo runtime + refactorear los
callers + bloquear regresiones.

---

## §2. Decisión

**Adoptamos dispatch por string** para invocaciones cross-agent, con
**fuente de verdad en el manifest** del plugin.

```python
# ANTES (deuda)
from src.plugins.chats.agent.remarketing.workflows.remarketing import (
    RemarketingSessionWorkflow,
)
handle = await client.start_workflow(
    RemarketingSessionWorkflow,
    ...
)

# DESPUÉS (objetivo)
from src.platform.plugin_manifest import get_workflow_name, get_task_queue
handle = await client.start_workflow(
    get_workflow_name("chats", "remarketing"),    # string del manifest
    args=[input_dto],                              # DTO serializable (R-JSON frozen)
    workflow_id=workflow_id,
    task_queue=get_task_queue("chats", "remarketing"),
)
```

**Regla resultante:**

> **Si el código necesita invocar un workflow registrado por un worker
> de OTRO agent (sibling), DEBE usar `get_workflow_name(plugin, worker)`
> del manifest. NUNCA importar la clase del workflow del sibling.**

Excepción: **dentro del mismo agent**, importar el workflow class está OK
(es código del mismo dominio).

---

## §3. Schema changes — `plugin.schema.yaml`

Extender `frontend_dashboard/src/plugins/_schema/plugin.schema.yaml`
agregando `workflow_classes:` al worker:

```yaml
agent:
  workers:
    items:
      type: object
      required: [name, module, task_queue]
      properties:
        # ... existentes (name, module, task_queue, invokes, deployment, compose) ...

        workflow_classes:
          type: array
          description: |
            Nombres canónicos de los workflows que este worker registra. Cada
            entry es el `__name__` de la clase Workflow Python (e.g.
            "RemarketingSessionWorkflow"). Permite que dispatchers cross-agent
            arranquen workflows POR STRING (Temporal acepta tanto class como
            string) sin importar la clase del sibling — esto desbloquea R-DIP
            sin excepciones.

            Si el worker NO declara workflow_classes, los callers DEBEN seguir
            importando la clase (compatibilidad). Pero las nuevas invocaciones
            cross-agent DEBEN usar workflow_classes + get_workflow_name().
          items:
            type: string
            pattern: "^[A-Z][A-Za-z0-9]*$"   # PascalCase (Python class names)
```

### §3.1 Actualizar manifest de `chats`

```yaml
agent:
  workers:
    - name: sales
      module: src.plugins.chats.workers.sales
      task_queue: queue-sales-agent
      workflow_classes:                             # ← NUEVO
        - HubaraSalesSessionWorkflow
      invokes:
        - worker: remarketing
          via: start_workflow
          when: "tag=INTERESADO al cerrar venta"

    - name: remarketing
      module: src.plugins.chats.workers.remarketing
      task_queue: queue-remarketing-agent
      workflow_classes:                             # ← NUEVO
        - RemarketingSessionWorkflow
      invokes:
        - worker: sales
          via: get_workflow_handle
          when: "cliente responde durante remarketing → transferir a sales"
```

### §3.2 Validación automática

Test invariante nuevo: para cada `worker.workflow_classes[]`, verificar que
el nombre EXISTE en el módulo `worker.module.workflows`. Detecta drift
manifest↔código.

---

## §4. Backend refactor

### §4.1 Nuevo helper en `src.platform.plugin_manifest`

```python
# hubara_agency/src/platform/plugin_manifest.py

def get_workflow_name(plugin_id: str, worker_name: str, index: int = 0) -> str:
    """Devuelve el nombre del workflow registrado por un worker.

    Lee del manifest `workers[name=worker_name].workflow_classes[index]`.
    Permite invocar workflows cross-agent sin importar la clase Python
    (Temporal acepta strings: `client.start_workflow("WorkflowName", ...)`).

    Args:
        plugin_id: id del plugin owner del worker target.
        worker_name: name del worker target.
        index: si el worker registra >1 workflow, cuál usar (default 0,
               el primer entry).

    Raises:
        WorkflowClassNotDeclaredError: si el worker NO tiene workflow_classes
            o el index está fuera de rango. Sugiere agregarlo al manifest.

    Example:
        >>> get_workflow_name("chats", "remarketing")
        'RemarketingSessionWorkflow'
    """
    spec = get_worker_spec(plugin_id, worker_name)   # helper ya existe
    classes = spec.get("workflow_classes") or []
    if not classes:
        raise WorkflowClassNotDeclaredError(
            f"Worker '{plugin_id}.{worker_name}' has no workflow_classes "
            f"declared in manifest. Add it under `agent.workers[].workflow_classes`."
        )
    if index >= len(classes):
        raise WorkflowClassNotDeclaredError(
            f"Worker '{plugin_id}.{worker_name}' declares only "
            f"{len(classes)} workflow_classes, requested index={index}."
        )
    return classes[index]
```

### §4.2 Refactor del use_case

**Archivo:** `hubara_agency/src/plugins/chats/agent/sales/use_cases/load_or_start_sales_session.py`

```python
# QUITAR:
from src.plugins.chats.agent.remarketing.workflows.remarketing import (
    RemarketingSessionWorkflow,
)

# AGREGAR:
from src.platform.plugin_manifest import get_workflow_name, get_task_queue

# Cambiar línea 144:
# workflow_class: type = RemarketingSessionWorkflow
workflow_name: str = get_workflow_name("chats", "remarketing")

# Cambiar línea 226 (y similares):
handle = await client.start_workflow(
    workflow_name,                                   # string en vez de clase
    args=[remarketing_input],                        # DTO frozen R-JSON
    workflow_id=workflow_id,
    task_queue=get_task_queue("chats", "remarketing"),
)
```

### §4.3 Manejo del DTO `RemarketingSessionInput`

El input DTO también vive en `remarketing/contracts.py`. Si se importa
desde `sales/`, sigue violando R-DIP #10.

**Opciones:**

| Opción | Pros | Cons |
|---|---|---|
| A. Mover `RemarketingSessionInput` a `src.platform.contracts.chats/` | DTOs compartidos centralizados | Centraliza contratos por plugin |
| B. Construir el dict args manualmente desde `sales/` | Sin imports | Pierde type safety |
| C. Definir el DTO en el manifest (JSON schema) | 100% manifest-driven | Mucho boilerplate |

**Recomendado: A** — mover los DTOs cross-agent a `src.platform.contracts/`
o a `src.plugins.chats.shared/` (subdir compartido dentro del plugin, fuera
de `agent/`). Estilo análogo al pattern `entities/` del frontend.

Voy con la propuesta: nuevo subdir `src.plugins.chats.shared.contracts/` que
contiene los DTOs cross-sub-agent. Esto:

- Cumple R-DIP #10 (los agents no se importan entre sí; importan del shared)
- Mantiene los DTOs cerca del plugin (no contamina `src.platform`)
- Setea pattern para futuros plugins multi-agent

### §4.4 Eliminar `ignore_imports` del `.importlinter`

Una vez completo §4.2 y §4.3:

```ini
; QUITAR de [importlinter:contract:agents-independent]:
ignore_imports =
    src.platform.temporal.dispatcher -> src.plugins.chats.agent.sales.config.env
    src.platform.temporal.dispatcher -> src.plugins.chats.agent.sales.contracts
    src.platform.temporal.dispatcher -> src.plugins.chats.agent.sales.workflows.sales_session
    src.platform.temporal.dispatcher -> src.plugins.chats.agent.remarketing.config.env
    src.platform.temporal.dispatcher -> src.plugins.chats.agent.remarketing.contracts
    # ↑ deuda #1 — mismo refactor aplica al dispatcher

; AGREGAR comment:
; Tras ADR-2026-05-19 string-based workflow dispatch, R-DIP #10 cumple
; sin excepciones. Los workflows se invocan por nombre (get_workflow_name).
```

(El dispatcher de `src.platform.temporal.dispatcher` tiene la misma deuda
documentada — se resuelve con el mismo mecanismo.)

---

## §5. Tests de arquitectura

### §5.1 Test nuevo: `tests/architecture/test_r_dip_workflow_class_imports.py`

```python
"""R-DIP #10 — Anti-pattern: importar workflow class de sibling agent.

Detecta el bug que motivó ADR-2026-05-19. Falla si un módulo agent importa
un Workflow class de otro agent hermano (chats.sales → chats.remarketing,
chats.remarketing → chats.sales, etc.).

Regla canónica: cross-agent workflow dispatch DEBE usar
`get_workflow_name(plugin, worker)` + string passed a `client.start_workflow`.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_PLUGINS_DIR = Path(__file__).resolve().parents[2] / "src" / "plugins"


def _iter_agent_modules() -> list[Path]:
    """[(plugin_id, agent_name, py_file)] de cada .py bajo plugins/<id>/agent/<name>/."""
    out: list[Path] = []
    for plugin_dir in _PLUGINS_DIR.iterdir():
        if not plugin_dir.is_dir():
            continue
        agent_root = plugin_dir / "agent"
        if not agent_root.exists():
            continue
        for agent_dir in agent_root.iterdir():
            if not agent_dir.is_dir():
                continue
            for py in agent_dir.rglob("*.py"):
                if "__pycache__" in py.parts:
                    continue
                out.append(py)
    return out


def _imports_in_file(py: Path) -> list[str]:
    """Devuelve los module paths importados (from src.X.Y import Z → src.X.Y)."""
    try:
        tree = ast.parse(py.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    paths: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            paths.append(node.module)
    return paths


def test_no_agent_imports_sibling_agent_workflow_class() -> None:
    """Falla si un agent importa un módulo `workflows.*` de otro agent hermano."""
    violations: list[str] = []

    for py in _iter_agent_modules():
        # Detectar el agent del archivo: src/plugins/<plugin>/agent/<agent>/...
        parts = py.relative_to(_PLUGINS_DIR).parts
        if len(parts) < 3 or parts[1] != "agent":
            continue
        own_plugin, own_agent = parts[0], parts[2]

        for imp in _imports_in_file(py):
            # Match: src.plugins.<X>.agent.<Y>.workflows.<Z>
            if not imp.startswith("src.plugins."):
                continue
            imp_parts = imp.split(".")
            if len(imp_parts) < 6:
                continue
            if imp_parts[3] != "agent":
                continue
            # imp_parts[2] = target plugin, imp_parts[4] = target agent
            target_plugin, target_agent = imp_parts[2], imp_parts[4]

            # Sibling agent (mismo plugin, distinto agent) Y módulo .workflows.*
            same_plugin = target_plugin == own_plugin
            different_agent = target_agent != own_agent
            is_workflow = len(imp_parts) >= 6 and imp_parts[5] == "workflows"

            if same_plugin and different_agent and is_workflow:
                violations.append(
                    f"{py.relative_to(_PLUGINS_DIR)} imports "
                    f"`{imp}` — sibling agent workflow class. "
                    f"Use `get_workflow_name({target_plugin!r}, {target_agent!r})` "
                    f"+ string dispatch (ver ADR-2026-05-19)."
                )

    assert violations == [], (
        "R-DIP #10 violations detected — cross-agent workflow class imports:\n  "
        + "\n  ".join(violations)
    )
```

### §5.2 Extender `test_r_dip.py` (existente)

Agregar un test que verifica que `.importlinter` NO tiene `ignore_imports`
agregadas para el contracto `agents-independent`:

```python
def test_importlinter_has_no_agents_independent_exceptions() -> None:
    """Post ADR-2026-05-19, R-DIP #10 NO debe tener `ignore_imports`.

    Si alguien agrega una excepción nueva, este test falla y obliga a
    abrir un ADR o refactorear según string-dispatch.
    """
    importlinter_path = Path(__file__).resolve().parents[2] / ".importlinter"
    text = importlinter_path.read_text(encoding="utf-8")

    # Buscar el bloque [importlinter:contract:agents-independent]
    blocks = text.split("[importlinter:contract:")
    for block in blocks:
        if not block.startswith("agents-independent"):
            continue
        # Dentro del bloque, buscar líneas activas `ignore_imports = ...`
        # (excluyendo lines comentadas)
        for line in block.splitlines():
            stripped = line.strip()
            if stripped.startswith(";"):  # comment
                continue
            if stripped.startswith("ignore_imports"):
                raise AssertionError(
                    "Contracto `agents-independent` tiene `ignore_imports` "
                    "activos. Post-ADR-2026-05-19 no debería haber excepciones. "
                    "Refactorear el call site a string dispatch o abrir ADR."
                )
        return
    raise AssertionError("No encontré contracto agents-independent en .importlinter")
```

### §5.3 Test nuevo: `tests/plugins/test_workflow_classes_declared.py`

```python
"""Cada worker debe declarar workflow_classes en manifest, y los nombres
deben existir como clases Python en su módulo workflows.

Detecta drift manifest ↔ código.
"""

def test_every_worker_workflow_class_exists_in_code() -> None:
    """Cada string en workers[].workflow_classes debe matchear una clase
    real importable desde `<worker.module_root>.workflows.*`.
    """
    # Por cada manifest, por cada worker, por cada workflow_class declarado:
    # - importlib.import_module(worker.module rstrip .workers.X + .workflows)
    # - getattr para obtener la clase
    # - assert es subclass de temporalio.workflow.Workflow (o tiene @workflow.defn)
    ...
```

---

## §6. Skills del pipeline `hubara-*`

### §6.1 `hubara-architecture-guide/references/deha-rules.md`

Agregar §5.4 nueva sección:

```markdown
### §5.4 Anti-pattern: cross-agent workflow class import

**Síntoma:** un módulo bajo `plugins/<X>/agent/<A>/` importa
`from src.plugins.<X>.agent.<B>.workflows.<Z> import <Z>WorkflowClass`
(o sus DTOs / contracts) — el agent A está importando del agent B (sibling).

**Por qué viola R-DIP #10:**
- Acopla los 2 agents — refactor de B rompe A
- Impide deshabilitar agents selectivamente (`ENABLED_PLUGINS=chats:sales`
  sin remarketing → ImportError en boot)
- Ciclos latentes
- Bloquea extracción del agent a plugin propio

**Solución (canónica desde ADR-2026-05-19):**

```python
# ❌ INVÁLIDO — viola R-DIP #10
from src.plugins.chats.agent.remarketing.workflows.remarketing import (
    RemarketingSessionWorkflow,
)
await client.start_workflow(RemarketingSessionWorkflow, ...)

# ✅ CORRECTO — dispatch por string desde manifest
from src.platform.plugin_manifest import get_workflow_name, get_task_queue
await client.start_workflow(
    get_workflow_name("chats", "remarketing"),
    args=[input_dto],                # DTO frozen (R-JSON), desde shared/contracts
    workflow_id=workflow_id,
    task_queue=get_task_queue("chats", "remarketing"),
)
```

**Pre-requisitos:**
1. El worker target declara `workflow_classes` en el manifest.
2. El DTO del input vive en `plugins/<X>/shared/contracts/` (no en
   `agent/<B>/contracts.py`) — fuera del scope sibling, accesible para
   importar sin violar R-DIP.

**Excepciones:** DENTRO del mismo agent, importar workflow classes está
OK (no cruza sibling boundary).

**Test enforcer:** `tests/architecture/test_r_dip_workflow_class_imports.py`
falla CI si detecta el patrón.
```

### §6.2 `hubara-implementer-archon/SKILL.md`

Agregar al checklist de "antes de emitir task-result.yaml passed":

```markdown
## §N. Cross-agent dispatch check (R-DIP #10)

Si tu task introduce código bajo `src/plugins/<X>/agent/<A>/` que necesita
arrancar un workflow registrado por un worker hermano (otro agent del
mismo plugin O de otro plugin):

- ❌ NO importes la clase del workflow del sibling
- ❌ NO importes los DTOs de `agent/<B>/contracts.py` directamente
- ✅ USA `get_workflow_name(plugin, worker)` del `src.platform.plugin_manifest`
- ✅ Si el worker target NO tiene `workflow_classes:` en su manifest,
  agregalo (es parte de tu task — actualizar AMBOS, manifest + uso)
- ✅ Mueve los DTOs compartidos a `src/plugins/<X>/shared/contracts/`
- ✅ Si el cambio es no-trivial, levanta `status: blocked,
  blocked_reason: requires_planner_update` y dejá nota describiendo el
  refactor necesario

**Antes de emitir `status: passed`:**
- Corré `uv run lint-imports` localmente — debe pasar sin warnings
- Corré `uv run pytest tests/architecture/test_r_dip_workflow_class_imports.py`
- Si agregaste un `workflow_classes:` a un worker, agregalo también al
  `wiring_intents` de tu `task-result.yaml`

Ver ADR-2026-05-19 + `sections/04-backend-agents.md §5.4`.
```

### §6.3 `hubara-feature-planner-archon/SKILL.md`

Agregar regla al planner:

```markdown
## §N. Cross-agent invocation risk

Si la HU pide que un agent invoque un workflow de otro agent (sibling o
cross-plugin), marcá la task con:

- `risk: medium` o `high` según complejidad
- `cross_agent_dispatch: true` en metadata de la task
- Incluí check en `delivers_acceptance`: "Cross-agent dispatch implementado
  con get_workflow_name() (string-based), no class import"

Esto asegura que el implementer no caiga en el antipatrón por descuido —
el flag es señal explícita en la task.md.

Si la HU es ambigua sobre "cómo" invocar (sólo dice "cuando termina X,
arranca Y"), el planner debe asumir string-based dispatch y agregar la
declaración de `workflow_classes:` al `wiring_intents` esperado.
```

### §6.4 `hubara-tech-refiner-archon/SKILL.md`

Agregar al refinement checklist:

```markdown
## §N. Cross-agent flow flagging

Cuando la HU mencione interacciones entre agents (sales↔remarketing,
catalog→chats, etc.), incluí en la §X "Dependencias técnicas":

- Mecanismo: string-based dispatch (NO importar workflow class del sibling)
- Pre-requisito: `workflow_classes:` declarado en manifest del target
- DTO de input: vive en `shared/contracts/` (no en el agent target)
- Reference: ADR-2026-05-19

Si la HU pide explícitamente "importar tal workflow class", marcá el
refinement con `mode: blocked, blocked_reason: violates_R-DIP_10` y
sugerí el fraseo correcto.
```

---

## §7. `review-pr-hubara/agent-deha-compliance`

### §7.1 Extender el prompt del agent

En `.archon/workflows/review-pr-hubara.yaml`, sección `agent-deha-compliance`
(línea ~164), expandir el bullet R-DIP:

```yaml
prompt: |
  ...
  Buscá violaciones:
    - R-DET: ...
    - R-JSON: ...
    - R-STATELESS: ...
    - R-HEARTBEAT: ...
    - R-DIP:
        * platform/ importando plugins/
        * cross-plugin imports
        * **NUEVO post-ADR-2026-05-19:** cross-agent workflow class import
          (sales → remarketing.workflows, o similar). Detectar con grep
          `^from src\\.plugins\\.[a-z_]+\\.agent\\.[a-z_]+\\.workflows`. Si
          el archivo source está en `<plugin>/agent/<A>/` y la importación
          es de `<plugin>/agent/<B>/workflows/` (B != A), MARCAR como
          severity: critical, rule: R-DIP, fix_suggestion: "Usar
          get_workflow_name() en lugar de import — ver ADR-2026-05-19".
  ...
```

### §7.2 Auto-fix recipe nuevo

En `agent-deha-compliance`, agregar a la sección auto-fix-suggestions:

```python
# Anti-pattern detector + fix template:
import re

CROSS_AGENT_IMPORT_RE = re.compile(
    r"^from\s+src\.plugins\.([a-z_]+)\.agent\.([a-z_]+)\.workflows\.\S+\s+import\s+(\S+)",
    re.MULTILINE,
)

# Para cada match, el auto-fix:
# 1. Comentar la línea del import (no eliminar — el dev decide)
# 2. Insertar:
#      from src.platform.plugin_manifest import get_workflow_name
# 3. Detectar usos de la clase importada y reemplazar por
#      get_workflow_name("<plugin>", "<target_agent>")
# 4. Verificar que client.start_workflow recibe `args=[...]` (no positional)
# 5. Corre tests/architecture/test_r_dip_workflow_class_imports.py
# 6. Si falla → revertir el auto-fix (es un caso complejo, dejar al humano)
```

El auto-fix solo se aplica si:
- Es 1:1 mapping (1 import, 1 sitio de uso)
- El target worker ya tiene `workflow_classes:` en manifest (sino, no se
  puede string-dispatch sin agregar manifest también)

Si no se cumple, el finding queda como CRITICAL con `fix_suggestion`
descriptivo pero `auto_fix: false`.

### §7.3 Synthesize-step: severity bump

En la fase `synthesize` del workflow review-pr-hubara, agregar:

```yaml
# Si findings-deha.yaml tiene un finding con rule=R-DIP +
# message contiene "cross-agent" o "sibling agent" → BLOCK el merge
# (severity bump a critical + setear `merge_blocking: true`).
```

Esto asegura que ningún PR nuevo introduce esta clase de violación.

---

## §8. Validación end-to-end

### §8.1 Smoke tests post-implementación

```bash
# 1. Tests de arquitectura
cd hubara_agency
uv run pytest tests/architecture/test_r_dip.py -v
uv run pytest tests/architecture/test_r_dip_workflow_class_imports.py -v
uv run pytest tests/plugins/test_workflow_classes_declared.py -v

# 2. Import linter (debe pasar SIN warnings)
uv run lint-imports

# 3. Pytest full suite
uv run pytest -q

# 4. System map endpoint: verificar que los edges siguen apareciendo
curl -s http://localhost:8000/api/system-map/graph | jq '.edges[] | select(.kind=="invokes_worker")'
# Esperado: sigue habiendo 3 edges (sales↔remarketing del manifest + api→sales del code scan)

# 5. End-to-end functional
cd hubara_agency
uv run pytest tests/functional/ -m functional -v
# Verificar que el flujo sales → remarketing sigue funcionando
```

### §8.2 Smoke test con plugin synthetic

Test que crea un mini-plugin con 2 agents siblings y un dispatch entre ellos.
Verifica que con `workflow_classes:` declarado en ambos, `get_workflow_name()`
retorna el string correcto y `start_workflow` lo resuelve a la clase
registrada en runtime.

### §8.3 Pipeline hubara — test del fix end-to-end

Lanzar el pipeline contra una mini-HU sintética que requiera cross-agent
dispatch (e.g. "agregar handoff de catalog.sync → chats.sales cuando hay
producto nuevo"). El implementer debería:

1. Detectar el flag del feature-planner
2. NO importar la clase
3. Usar `get_workflow_name()`
4. Agregar `workflow_classes:` al manifest target
5. Pasar lint-imports + tests de arquitectura

Si el AI mete el antipatrón, el `until_bash` re-corre los gates y el AI
recibe `test-failures.md` con el output del test arquitectural → re-implementa.

---

## §9. Rollout plan

### §9.1 Orden de PRs

| PR | Scope | Pre-requisitos | Validación |
|---|---|---|---|
| **PR1** | Schema `workflow_classes:` + helper `get_workflow_name()` en platform | Ninguno | Tests unit del helper |
| **PR2** | Update manifest `chats` con `workflow_classes` + DTOs cross-agent a `shared/contracts/` | PR1 | Manifest válido contra schema + import-linter |
| **PR3** | Refactor `sales/use_cases/load_or_start_sales_session.py` a string dispatch | PR1+PR2 | Tests funcionales (sales↔remarketing) siguen pasando |
| **PR4** | Refactor `src.platform.temporal.dispatcher` (misma deuda #1) | PR1+PR2 | Idem |
| **PR5** | Remover `ignore_imports` del `.importlinter` + agregar test que enforza ausencia | PR3+PR4 | `lint-imports` clean + test arquitectural pasa |
| **PR6** | Test nuevo `test_r_dip_workflow_class_imports.py` + test_workflow_classes_declared.py | PR5 | Test verde con repo limpio |
| **PR7** | Update skills `hubara-*` (architecture-guide, implementer, feature-planner, tech-refiner) | PR6 | Smoke tests del pipeline con mini-HU |
| **PR8** | Update `review-pr-hubara` (agent-deha-compliance + auto-fix + synthesize bump) | PR7 | Mini-HU sintética: PR con violación → review lo detecta y blockea |

### §9.2 Validación entre PRs

Después de PR5, correr el pipeline completo contra una HU "real" actual del
backlog para confirmar que NADA del flujo agéntico rompió.

### §9.3 Comunicación

- ADR commiteado a `docs/adr/` (o raíz, según convención del repo)
- Mención en CHANGELOG.md
- Sticky message en canal de devs: "Nuevo patrón canónico para cross-agent
  workflow dispatch — leer ADR-2026-05-19"

---

## §10. Riesgos

| Riesgo | Mitigación |
|---|---|
| Workflow name typo en `get_workflow_name("chats", "remmarketing")` → runtime error en producción | Test `test_workflow_classes_declared.py` detecta drift manifest↔código en CI |
| Worker no registra el workflow (typo en `workflows=[...]` del `Worker()`) | Mismo test verifica que el nombre existe como clase importable |
| `get_workflow_name` lookup falla por manifest no cargado | Helper hace lazy import + cache del manifest (mismo pattern que `get_task_queue`) |
| Refactor toca muchos archivos a la vez → merge conflicts | Hacer PRs incrementales (orden de §9.1) — cada PR es atómico |
| Auto-fix del review-pr-hubara hace cambio inválido | Auto-fix solo si 1:1 mapping + target tiene `workflow_classes` — sino, finding sin auto-fix |
| Reviewers no notan el nuevo patrón en PRs | `agent-deha-compliance` + `synthesize` con severity bump → BLOCK merge automático |

---

## §11. Referencias

- **`hubara_agency/.importlinter`** — contrato `agents-independent` + `Known debt` notes
- **`hubara_agency/src/plugins/chats/agent/sales/use_cases/load_or_start_sales_session.py:54-150`** — el call site con el antipatrón
- **`.claude/skills/hubara-architecture-guide/references/deha-rules.md §5`** — R-DIP overview
- **`hubara_agency/tests/architecture/test_r_dip.py`** — tests existentes
- **`.archon/workflows/review-pr-hubara.yaml`** — definición del agent-deha-compliance
- **`HUBARA_PIPELINE_GUIDE.md §11`** — overview de las R-rules enforced por el pipeline
- **commit `9ccfacd`** — manifest `invokes:` (precursor de este ADR)
- **Temporal docs — start_workflow with string vs class:**
  https://docs.temporal.io/develop/python/temporal-clients#start-workflow-execution

---

## §12. Decisión

**Accept** — implementar en el orden de §9.1. Pipeline enforcement (§6, §7)
es parte del scope: sin él, la regla queda como "documentación que nadie
mira" y el bug puede volver.

**Owner:** asignar al implementer / abrir issue.

**Fecha de implementación target:** dentro de las próximas 2 semanas.

**Cuando se complete:**
- ✅ R-DIP #10 cumple sin excepciones
- ✅ `.importlinter` sin `ignore_imports`
- ✅ Pipeline hubara vacunado contra esta clase de bug en HUs futuras
- ✅ Review automático detecta + bloquea
- ✅ System_map sigue mostrando los edges `invokes_worker` correctamente
  (ahora doble-validados: manifest + código alineados)

---

**Fin ADR-2026-05-19.**
