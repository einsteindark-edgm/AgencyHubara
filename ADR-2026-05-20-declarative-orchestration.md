# ADR-2026-05-20 — Declarative Cross-Worker Orchestration

**Estado:** Aceptado e implementado
**Owner:** Operador
**Supersedes:** ADR-2026-05-19-string-based-workflow-dispatch (Level 2 → Level 3)
**Implementation date:** 2026-05-20
**Implementation status:** ✅ Complete — backend refactor, tests, lint, skills, review-pr, system_map

---

## §0. TL;DR

Convertimos el cross-worker flow del plugin `chats` de **import-coupled**
(workflow A importa workflow class B) a **declarative-orchestrated** (workflow
A emite un evento; el manifest declara qué hacer; un dispatcher genérico
arranca workflow B por NOMBRE leído del manifest).

**Eliminadas:**

- 6 entries de `ignore_imports` en `.importlinter` (R-DIP #9 + R-DIP #10).
- 1 import cross-agent en `sales/use_cases/load_or_start_sales_session.py`.
- 5 imports cross-agent locales en `src.platform.temporal.dispatcher`.
- Acoplamiento conceptual: el código del workflow A NO sabe quién es B.

**Agregados:**

- Schema extension: `workers[].workflow_classes`, `emits`, `transitions`.
- Platform package: `src.platform.orchestration` con events + transitions +
  dispatcher.
- Shared events boundary: `src.plugins.chats.shared.contracts.events`.
- Tests: 30 nuevos unitarios + 4 nuevos arquitecturales.
- Skills + review-pr-hubara con detector específico + auto-block en merge.

**Resultado verificable:**
- `uv run lint-imports` → 4 contracts kept, **0 broken**, **0 ignored**.
- `uv run pytest -q` → 416 passed (386 pre-ADR + 30 nuevos).
- `system_map` muestra los edges sales↔remarketing con labels descriptivos
  derivados del manifest.

---

## §1. Contexto y problema

### §1.1 Estado pre-ADR

```python
# src/plugins/chats/agent/sales/use_cases/load_or_start_sales_session.py
from src.plugins.chats.agent.remarketing.workflows.remarketing import (
    RemarketingSessionWorkflow,    # ← R-DIP #10 violation
)

workflow_class: type = RemarketingSessionWorkflow
handle = await client.start_workflow(workflow_class, ...)
```

Y en `src.platform.temporal.dispatcher` había 5 imports locales dentro de
activity bodies (también violaciones documentadas como deuda en
`.importlinter`):

```python
@activity.defn(name="start_or_signal_sales_workflow")
async def start_or_signal_sales_workflow_activity(decision):
    # Local imports inside activity body to avoid module-level cycles
    from src.plugins.chats.agent.sales.config.env import get_workspace_path
    from src.plugins.chats.agent.sales.contracts import SalesSessionInput
    from src.plugins.chats.agent.sales.workflows.sales_session import HubaraSalesSessionWorkflow
    ...
```

### §1.2 Por qué el ADR-2026-05-19 (Nivel 2) no era suficiente

ADR-2026-05-19 proponía:

> **Adoptamos dispatch por string** para invocaciones cross-agent

Eso resolvía el R-DIP #10 al sustituir el import por
`get_workflow_name("chats", "remarketing")`. Pero **el código source seguía
sabiendo el nombre del target** (hardcoded en el use_case). El acoplamiento
quedaba en "qué llamar a continuación" — solo cambiaba el formato.

Para el objetivo declarado del operador — *"ejecutar con archon la mayor
cantidad de automatizaciones aisladas"* — agentes IA múltiples deben poder
trabajar en sales sin coordinarse con quien trabaja en remarketing. Eso
requería **dispatch declarativo**: el código source emite "terminé con
estado X", y el manifest decide qué pasa.

### §1.3 La pregunta del operador (2026-05-20)

> *"sería muy exagerado que en el código para llamar al otro workflow sea
> solamente desde el plugin.yaml? que el plugin.yaml se convierta en un
> estilo orquestador el cual los workers y el workflow vea para accionar
> los siguientes nodos a activar"*

Respuesta: **no es exagerado, es una arquitectura conocida (Declarative
Orchestration / Event-Driven con manifest como SSoT del flujo)**. Esta ADR
documenta su adopción, escope y trade-offs.

---

## §2. Decisión

**Adoptamos Level 3 — Declarative Orchestration** para todo flujo
cross-worker. El manifest del plugin se convierte en la fuente de verdad
del flujo entre workers; el código de los workflows emite eventos y NO
referencia al target.

### §2.1 Los 4 niveles posibles

| Nivel | Decisor de "qué workflow ejecutar next" | Acoplamiento |
|---|---|---|
| **0** (pre-ADR-19) | Código importa clase de workflow target | 🔴 Alto — viola R-DIP #10 |
| **1** (commit 9ccfacd) | YAML declara `invokes:` (doc only) | 🟡 Medio — código sigue importando |
| **2** (ADR-19) | YAML `workflow_classes:`, código dispatcha por string | 🟢 Bajo — código suelto pero hardcoded |
| **3** (este ADR) | YAML `transitions:`, código emite eventos | 🟢 Mínimo — código no sabe target |
| 4 (futuro?) | BPMN/n8n: YAML define grafo completo de estados | 🔵 Cero — reinventa Temporal |

**Implementamos Nivel 3, NO Nivel 4** (mantenemos Temporal como engine
durable; el YAML es dispatch table, no state machine).

### §2.2 Regla resultante

> **Si tu código (workflow / use_case / activity) necesita arrancar,
> signal-ar, o transferir control a un workflow registrado por OTRO worker
> (mismo plugin o cross-plugin), DEBE hacerlo via declarative
> orchestration: emit un `@dataclass(frozen=True)` event vía
> `dispatch_event_activity`, y declarar la transition en `plugin.yaml`.
> NUNCA importar la clase del workflow target ni sus DTOs/contracts/tools/
> use_cases/activities.**

Excepciones: dentro del **mismo agent** (e.g. `sales/use_cases` →
`sales/workflows`), importar el workflow class está OK — es código del
mismo dominio, no cruza la frontera sibling.

---

## §3. Schema changes — `plugin.schema.yaml`

Extender `frontend_dashboard/src/plugins/_schema/plugin.schema.yaml`:

```yaml
agent:
  workers:
    items:
      properties:
        workflow_classes:
          type: array
          description: |
            Nombres canónicos de los workflows que este worker registra. Cada
            entry es el `__name__` o el `name=` del decorator `@workflow.defn`.
            Permite dispatch cross-worker POR STRING.
          items:
            type: string
            pattern: "^[A-Z][A-Za-z0-9]*$"

        emits:
          type: array
          description: |
            Eventos de completion / transición que el workflow puede emitir.
            Cada entry es el nombre del dataclass del evento. El dispatcher
            matchea estos contra `transitions[]`.
          items:
            type: string
            pattern: "^[A-Z][A-Za-z0-9]*Event$"

        transitions:
          type: array
          description: |
            Transiciones declarativas: `on_event + when + action`.
            El dispatcher las ejecuta cuando este worker emite un evento.
          items:
            properties:
              id:               { type: string, pattern: "^[a-z][a-z0-9_]*$" }
              on_event:         { type: string, pattern: "^[A-Z][A-Za-z0-9]*Event$" }
              when:             { type: object }
              action:
                properties:
                  via:                     # start_workflow | start_workflow_with_replace | ensure_running | signal
                  target_plugin:           # optional, default = source plugin
                  target_worker:           # required
                  target_workflow:         # required, must be in target's workflow_classes
                  signal_name:             # for via=signal
                  workflow_id_template:    # tokens: {event.<field>}
                  input_mapping:           # { field: "$" | "$.field" }
                  start_delay_field:       # name of event field with int seconds
```

### §3.1 Manifest actualizado — `chats/plugin.yaml`

```yaml
agent:
  workers:
    - name: sales
      module: src.plugins.chats.workers.sales
      task_queue: queue-sales-agent
      workflow_classes:
        - HubaraSalesSessionWorkflow
      emits:
        - SalesSessionCompletionEvent
      transitions:
        - id: sales_to_remarketing_on_interested
          on_event: SalesSessionCompletionEvent
          when:
            tag: INTERESADO
          action:
            via: start_workflow_with_replace
            target_plugin: chats
            target_worker: remarketing
            target_workflow: RemarketingWorkflow
            workflow_id_template: "remarketing-{event.session_id}"
            input_mapping:
              session_id: "$.session_id"
              motivo: "$.motivo"
            start_delay_field: delay_seconds

    - name: remarketing
      module: src.plugins.chats.workers.remarketing
      task_queue: queue-remarketing-agent
      workflow_classes:
        - RemarketingWorkflow
      emits:
        - CustomerRepliedDuringRemarketingEvent
      transitions:
        - id: remarketing_to_sales_on_customer_reply
          on_event: CustomerRepliedDuringRemarketingEvent
          action:
            via: ensure_running
            target_plugin: chats
            target_worker: sales
            target_workflow: HubaraSalesSessionWorkflow
            workflow_id_template: "session-{event.session_id}"
            input_mapping:
              session_id: "$.session_id"
```

---

## §4. Backend implementation

### §4.1 Platform: `src.platform.orchestration`

Nuevo package con 4 módulos:

```
src/platform/orchestration/
├── __init__.py         # public surface (barrel)
├── events.py           # EventEnvelope, envelope_for, event_to_dict, event_type_name
├── transitions.py      # Transition, TransitionAction, from_dict, matches
└── dispatcher.py       # dispatch_event_activity (the runtime piece)
```

**Diseño clave** — `EventEnvelope`:

```python
@dataclass(frozen=True)
class EventEnvelope:
    event_type: str               # ← Python class name of the emitted event
    payload: Mapping[str, Any]    # ← asdict(event)
    source_plugin: str
    source_worker: str
```

El envelope cruza el boundary workflow → activity como **plain dict
serializable**. El dispatcher es genérico (no sabe ningún event type
concreto); matchea por nombre.

**Dispatcher activity** (`dispatch_event_activity`):

```python
@activity.defn(name="orchestration.dispatch_event")
async def dispatch_event_activity(envelope: EventEnvelope) -> DispatchResult:
    transitions = get_transitions(envelope.source_plugin, envelope.source_worker)
    matched = [t for t in transitions if t.matches(envelope)]
    if not matched:
        return DispatchResult(..., no_matches=True)

    client = await get_temporal_client()
    outcomes = []
    for t in matched:
        # _resolve_workflow_id, _build_input, _resolve_start_delay
        # → execute via, signal, ensure_running, start_workflow_with_replace
        outcomes.append(await _execute_action(...))
    return DispatchResult(..., matches=outcomes)
```

### §4.2 Platform helpers — `src.platform.plugin_manifest`

Agregados:

- `get_workflow_name(plugin_id, worker_name, index=0) -> str` — del Nivel 2.
- `get_emitted_events(plugin_id, worker_name) -> list[str]`.
- `get_transitions(plugin_id, worker_name) -> list[Transition]` — parseadas a typed.
- `find_matching_transitions(plugin_id, worker_name, envelope)` — sugar para tests.
- `WorkflowClassNotDeclaredError` (nueva exception).

### §4.3 Shared events boundary — `chats/shared/contracts/events.py`

```python
@dataclass(frozen=True)
class SalesSessionCompletionEvent:
    session_id: str
    tag: str                  # "INTERESADO" | "HUMANO" | etc.
    motivo: str = ""
    delay_seconds: int = 60

@dataclass(frozen=True)
class CustomerRepliedDuringRemarketingEvent:
    session_id: str
    summary: str = ""
```

Vive en `shared/contracts/` (no en `agent/<sub>/contracts/`) → ambos
siblings pueden importarlo sin violar R-DIP #10.

### §4.4 Workflow refactor — Sales

```python
# Antes (línea 198 sales_session.py):
if result.schedule_remarketing is not None:
    await workflow.execute_activity(
        schedule_remarketing_workflow_activity,
        result.schedule_remarketing, ...
    )

# Después:
if result.schedule_remarketing is not None:
    if workflow.patched("declarative-orchestration-v1"):
        await workflow.execute_activity(
            dispatch_event_activity,
            envelope_for(
                SalesSessionCompletionEvent(
                    session_id=result.schedule_remarketing.session_id,
                    tag="INTERESADO",
                    motivo=result.schedule_remarketing.motivo,
                    delay_seconds=result.schedule_remarketing.delay_seconds,
                ),
                source_plugin="chats",
                source_worker="sales",
            ),
            ...
        )
    else:
        # Legacy path (pre-deploy histories) — replay-safe
        from src.platform.temporal.dispatcher import schedule_remarketing_workflow_activity
        await workflow.execute_activity(schedule_remarketing_workflow_activity, ...)
```

### §4.5 Workflow refactor — Remarketing

Idéntico patrón. Nuevo método privado `self._handoff_to_sales(...)` que:

1. Llama `write_pending_handoff_activity(session_id, summary)` (efecto side).
2. Llama `dispatch_event_activity(envelope_for(CustomerRepliedDuringRemarketingEvent(...)))`.

Ambos pasos están gated por `workflow.patched("declarative-orchestration-v1")`
para preservar determinismo durante migración.

### §4.6 Refactor del use_case `load_or_start_sales_session`

- Eliminado `from src.plugins.chats.agent.remarketing.workflows.remarketing import RemarketingSessionWorkflow`.
- Eliminado `workflow_class` variable.
- Signal final usa string: `await handle.signal("send_message", args=...)` en
  lugar de `handle.signal(workflow_class.send_message, args=...)`.
- El path Remarketing en el use_case SOLO reusa handle existente (no
  arranca nuevo). Si Remarketing está muerto, fallback a Sales (mismo
  comportamiento previo). El arranque nuevo de Remarketing ahora pasa por
  el dispatcher declarativo cuando Sales emite el evento.

### §4.7 Refactor del `platform/temporal/dispatcher.py`

Las 5 entries de `ignore_imports` se eliminaron porque:

- `start_or_signal_sales_workflow_activity`: usa `get_workflow_name("chats", "sales")` + dict input (Temporal deserializa al type hint). NO importa class ni input dataclass.
- `start_remarketing_for_session` (helper compartido con dashboard HTTP):
  usa string name + dict input.
- Las activities legacy se mantienen para preservar replay-safety; se
  pueden eliminar tras drain (idle_timeout=24h en remarketing).

Nueva activity genérica:

```python
@activity.defn(name="write_pending_handoff")
async def write_pending_handoff_activity(session_id: str, summary: str) -> None:
    """Genérica — no toca ningún agent module."""
    metadata_store = FilesystemMetadataStore(WORKSPACE_VAULT_DIR)
    data = metadata_store.read(session_id)
    data["pending_handoff_summary"] = summary
    metadata_store.write(session_id, data)
```

### §4.8 Bootstrap activities robustos a `runtime_workspace_path=None`

El dispatcher declarativo no sabe el `runtime_workspace_path` del worker
target (sería leak cross-agent). Los bootstrap activities de Sales y
Remarketing ahora hacen fallback a `get_workspace_path()` local cuando el
input no lo trae:

```python
runtime_path = input.runtime_workspace_path
if not runtime_path:
    # Local fallback — config local del agente, no leak cross-agent
    from src.plugins.chats.agent.<self>.config.env import get_workspace_path
    runtime_path = str(get_workspace_path())
```

---

## §5. Tests

### §5.1 Unitarios (30 nuevos)

- `tests/platform/orchestration/test_events.py` (10 tests):
  - `event_type_name`, `event_to_dict`, `event_get`, `envelope_for`
- `tests/platform/orchestration/test_transitions.py` (15 tests):
  - `Transition.from_dict` parsing + defaults + missing required fields
  - `Transition.matches` con varios `when:` (empty, single, multi, falsy)
- `tests/platform/orchestration/test_dispatcher.py` (15 tests):
  - `dispatch_event_activity` con mocks de Temporal client
  - Cada verb (`start_workflow`, `start_workflow_with_replace`,
    `ensure_running`, `signal`) con casos exitoso + race + error
  - `_resolve_workflow_id` con template tokens + default
  - `_build_input` con `$`, `$.field`, sin mapping
  - `_resolve_start_delay` con field, sin field

### §5.2 Arquitecturales (2 nuevos)

- `tests/architecture/test_r_dip_workflow_class_imports.py`:
  - `test_no_agent_imports_sibling_agent_module` — AST scan: detecta
    imports de `src.plugins.<X>.agent.<A>.{workflows,contracts,use_cases,tools,activities}`
    desde un sibling agent (A != self_agent).
  - `test_no_platform_imports_agent_workflows` — AST scan: detecta
    `src.platform.*` importando workflow classes de agents (incluso en
    local imports).
- `tests/architecture/test_manifest_orchestration_consistency.py`:
  - `test_workflow_classes_exist_in_code` — para cada
    `workflow_classes[i]`, verifica via AST que existe un
    `@workflow.defn(name=<i>)` en el código.
  - `test_transitions_reference_emitted_events` — cada
    `transition.on_event` está en el mismo worker's `emits[]`.
  - `test_transition_targets_exist` — `target_workflow` está en el target
    worker's `workflow_classes[]`.
  - `test_emitted_events_are_importable` — eventos en `emits[]` son
    importables de `shared/contracts/events.py` o `platform/contracts`.

### §5.3 Resultados

```
$ uv run pytest -q
386 passed, 1 skipped in 15.81s    # pre-ADR

$ uv run pytest -q   # post-ADR
416 passed, 1 skipped in 16.42s    # +30 tests

$ uv run lint-imports
4 contracts kept, 0 broken.        # 0 ignore_imports, R-DIP #9 + #10 sin excepciones
```

---

## §6. `.importlinter` cleanup

**Antes:**

```ini
[importlinter:contract:platform-no-agents]
...
ignore_imports =
    src.platform.temporal.dispatcher -> src.plugins.chats.agent.sales.config.env
    src.platform.temporal.dispatcher -> src.plugins.chats.agent.sales.contracts
    src.platform.temporal.dispatcher -> src.plugins.chats.agent.sales.workflows.sales_session
    src.platform.temporal.dispatcher -> src.plugins.chats.agent.remarketing.config.env
    src.platform.temporal.dispatcher -> src.plugins.chats.agent.remarketing.contracts

[importlinter:contract:agents-independent]
...
ignore_imports =
    [5 lines above repeated]
    src.plugins.chats.agent.sales.use_cases.load_or_start_sales_session ->
        src.plugins.chats.agent.remarketing.workflows.remarketing
```

**Después:**

```ini
[importlinter:contract:platform-no-agents]
...
; ADR-2026-05-19 + ADR-2026-05-20 resolved the documented exceptions.
; Cross-agent dispatch now goes through:
;   - get_workflow_name(plugin, worker) + string-based start_workflow, OR
;   - dispatch_event_activity + manifest transitions (declarative).
;
; Adding `ignore_imports` here requires opening a new ADR.

[importlinter:contract:agents-independent]
...
; ADR-2026-05-20 resolved all documented exceptions.
; Tests/architecture/test_r_dip_workflow_class_imports.py enforces.
```

---

## §7. system_map updates

El plugin `system_map` (visualizador) ahora lee `transitions[]` como SSoT
del flujo cross-worker. Si un worker declara `transitions[]`, ignora sus
`invokes[]` legacy (evita edges duplicados).

Cada transition genera un `Edge { kind: "invokes_worker" }` con label
descriptivo:

```
worker:chats:sales → worker:chats:remarketing
  label="SalesSessionCompletionEvent tag=INTERESADO → start_workflow_with_replace"

worker:chats:remarketing → worker:chats:sales
  label="CustomerRepliedDuringRemarketingEvent → ensure_running"
```

Esto permite al humano leer el flujo de un vistazo desde la UI del
system_explorer (`http://localhost:5175`).

---

## §8. Skills + review-pr-hubara updates

### §8.1 `hubara-architecture-guide/references/deha-rules.md`

Nueva §5.6 "Cross-worker flow: declarative orchestration" con:

- Snippets canónicos del patrón Level 3.
- Verbos disponibles (`start_workflow` / `start_workflow_with_replace` /
  `ensure_running` / `signal`).
- Side-effects pre-dispatch (activity separada).
- `workflow.patched()` durante migración.
- Pre-requisitos para declarar transitions.
- Anti-patterns + enforcement.

### §8.2 `hubara-implementer-archon/SKILL.md`

Nueva §5.1.1 "R-DIP #10 — Cross-worker dispatch" con checklist
obligatorio antes de emitir `status: passed`:

- `uv run lint-imports` debe pasar SIN `ignore_imports`.
- `uv run pytest tests/architecture/test_r_dip_workflow_class_imports.py`.
- `uv run pytest tests/architecture/test_manifest_orchestration_consistency.py`.
- `system_map` muestra el edge.

### §8.3 `hubara-feature-planner-archon/SKILL.md`

En §12 (Hard rules check) se agregó:

- **R-DIP #10 cross-worker (ADR-2026-05-20)** — checklist específico para
  cuando una HU hace que un workflow arranque/signale otro.

### §8.4 `hubara-tech-refiner-archon/SKILL.md`

En §11 (Hard rules check) se agregó:

- Bloquear refinements que fraseen "importar workflow class del sibling"
  con `mode: blocked, blocked_reason: violates_R-DIP_10`.

### §8.5 `review-pr-hubara.yaml` — `agent-deha-compliance`

- Nuevo prompt: detector regex específico para el anti-pattern.
- Auto-fix recipe: sugiere el patrón canónico cuando matchea.
- Synthesize phase: bumpea severity a critical Y setea
  `merge_blocking: true` para findings cross-agent.
- Nueva sección "Declarative orchestration drift": verifica que
  `transitions.on_event` aparece en `emits[]`.

### §8.6 `synthesize` phase output

Nuevo artifact `$ARTIFACTS_DIR/merge-decision.yaml`:

```yaml
blocked: true | false
blocking_findings:
  - file: <path>
    rule: <rule>
    reason: <one-liner>
```

La fase final del workflow puede leer este artifact y bloquear el merge
automáticamente.

---

## §9. Rollout

### §9.1 Estrategia adoptada — big-bang con `workflow.patched()`

A diferencia de ADR-2026-05-19 (que proponía 8 PRs incrementales), este ADR
se implementó en un solo commit porque:

1. El cambio es **atómico** — refactor backend + tests + skills + ADR son
   interdependientes (no tiene sentido tener manifest sin dispatcher, o
   dispatcher sin tests).
2. **`workflow.patched()`** protege la transición: workflows in-flight
   pre-deploy ejecutan el código legacy; nuevos toman el path Level 3.
3. **Tests robustos** (30 unit + 4 architecture) detectan regresiones
   antes del merge.
4. La cobertura de `system_map` permite **verificación visual** post-deploy.

### §9.2 Drain de patches legacy

Los `workflow.patched("declarative-orchestration-v1")` gates se mantienen
hasta que:

- Pase el `idle_timeout` de Remarketing (24h) Y
- No queden workflows in-flight con events pre-patch en su history.

Tras eso, `workflow.deprecate_patch("declarative-orchestration-v1")` +
remover el branch legacy + remover los activities legacy
(`schedule_remarketing_workflow_activity`,
`start_or_signal_sales_workflow_activity`) de los workers que ya no los
necesitan (chats/workers/sales.py + chats/workers/remarketing.py).

### §9.3 Comunicación

- Commit message contiene `ADR-2026-05-20` reference.
- CHANGELOG entry: "feat(orchestration): declarative cross-worker via
  manifest transitions + dispatch_event_activity (ADR-2026-05-20)".
- Skills updated → siguiente ejecución de los pipelines hubara-* ya
  incluye el patrón.

---

## §10. Trade-offs (los pagamos a sabiendas)

### §10.1 Pérdida de type safety entre workflows

**Antes:** si renombrás `RemarketingSessionInput`, mypy te grita en
`sales/use_cases/load_or_start_sales_session.py`.

**Ahora:** el dispatcher pasa un dict; Temporal deserializa al type hint
del workflow target. Si las keys del dict no matchean los fields del
dataclass, falla en runtime (no en mypy).

**Mitigación:**

- `test_manifest_orchestration_consistency` detecta drift entre
  `workflow_classes:` y código (via AST).
- Schema validation strict del manifest.
- Tests funcionales end-to-end ejercitan los flujos.

### §10.2 Debugging más indirecto

Stack trace de un dispatch no apunta directamente al target workflow —
hay un hop "consulta manifest + arranca por string".

**Mitigación:** structlog en `dispatch_event_activity` loguea:

```
orchestration.dispatch_event: received envelope event_type=... source_plugin=... source_worker=...
orchestration.dispatch_event: started workflow_id=... target_workflow=... task_queue=... start_delay_seconds=...
```

### §10.3 Pendiente resbaladiza de las `when:` clauses

Empieza con `when: { tag: INTERESADO }` (igualdad simple), termina en
`when: "tag == X and customer.score > 0.7 and not in_blackout_window"` →
mini-lenguaje custom.

**Mitigación dura:** el schema solo permite `additionalProperties:
oneOf: [string, integer, boolean, null]`. Si necesitás lógica compleja,
el workflow setea el campo apropiado en el evento (donde es plain
Python, type-safe).

### §10.4 Tooling de IDE se rompe

"Find usages" de `RemarketingWorkflow` no encuentra al sales workflow.
Refactoring de renames no funciona automáticamente.

**Mitigación:**

- `test_workflow_classes_exist_in_code` falla si un rename quita la clase
  pero deja el `workflow_classes:` apuntando al nombre viejo.
- Cuando renombrés un workflow → grep manual del manifest YAML.

### §10.5 Temporal ya es un orquestador

Temporal orquesta DENTRO de un workflow (activities, child workflows).
Vos orquestás ENTRE workflows en queues distintas → eso Temporal lo deja
al cliente, así que esta capa tiene sentido. **Pero la frontera importa:**

- DENTRO de un workflow → Temporal, código determinista, type-safe.
- ENTRE workflows cross-worker → YAML manifest, declarativo, late-bound.

Si se cruza esa frontera (e.g. child workflows del mismo worker via YAML),
reinventamos BPMN — **no lo hacemos**.

---

## §11. Riesgos

| Riesgo | Probabilidad | Mitigación |
|---|---|---|
| Typo en `workflow_classes:` → runtime error en producción | media | `test_workflow_classes_exist_in_code` falla CI |
| `transitions[].action.target_workflow` apunta a workflow inexistente | media | `test_transition_targets_exist` falla CI |
| Event field rename rompe `input_mapping:` silenciosamente | media | `KeyError` explícito en `_build_input` |
| Manifest YAML malformado al boot del worker | baja | Schema strict + import-linter |
| Worker sale del `workflow.patched()` antes de drain | baja | Idle timeout es 24h en remarketing; tests de replay |
| Auto-fix del review-pr aplica un fix inválido | baja | `revertible_by_test` corre tests post-fix; revierte si rompe |

---

## §12. Referencias

- **ADR-2026-05-19-string-based-workflow-dispatch** (supersedido por este)
- **`hubara_agency/.importlinter`** — contratos R-DIP #9 + #10
- **`hubara_agency/src/platform/orchestration/`** — implementation
- **`hubara_agency/src/plugins/chats/shared/contracts/events.py`** — events boundary
- **`hubara_agency/tests/platform/orchestration/`** — 30 unit tests
- **`hubara_agency/tests/architecture/test_r_dip_workflow_class_imports.py`** — AST scan
- **`hubara_agency/tests/architecture/test_manifest_orchestration_consistency.py`** — drift detection
- **`frontend_dashboard/src/plugins/chats/plugin.yaml`** — example manifest with transitions
- **`frontend_dashboard/src/plugins/_schema/plugin.schema.yaml`** — schema extension
- **`.claude/skills/hubara-architecture-guide/references/deha-rules.md §5.6`** — canonical pattern
- **`.archon/workflows/review-pr-hubara.yaml`** — `agent-deha-compliance` detector + auto-block

---

## §13. Decisión

**Accept and implemented.**

Cuando se completa (ya está):

- ✅ R-DIP #10 cumple sin excepciones (`lint-imports` clean).
- ✅ Pipeline hubara-* vacunado contra esta clase de bug en HUs futuras.
- ✅ Review automático detecta + bloquea cross-agent imports.
- ✅ System_map muestra el flujo cross-worker con labels declarativos.
- ✅ Múltiples agentes IA pueden trabajar en `sales/` y `remarketing/` en
  paralelo sin coordinación cross-PR (solo coordinación de manifest).

---

**Fin ADR-2026-05-20.**
