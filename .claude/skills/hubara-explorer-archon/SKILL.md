---
name: hubara-explorer-archon
description: Read-only exploration template para subagents que mapean subsistemas del repo AgencyHubara antes de que el implementer edite. NO ejecutable como skill directo — el contenido de este archivo se pasa como PROMPT al `Agent(subagent_type='Explore', ...)` desde el §1.5 del hubara-implementer-archon o el §1.6 del hubara-feature-planner-archon. Eleva la Técnica 15 (separar exploración de edición) y la Técnica 16 (usar codegraph como fuente primaria). Triggers — invocación indirecta via Read + Agent desde otros skills del pipeline; NO usar como user-facing slash command, NO invocar via Skill tool.
---

# hubara-explorer-archon — Read-only exploration template

> **Este archivo NO es un skill que se ejecuta solo.** Su contenido se PASA como prompt
> a un subagent (Agent tool con `subagent_type='Explore'`) desde otros skills del pipeline.
> El propósito de tenerlo como archivo separado es: (a) un único lugar para versionar el
> protocolo de exploración; (b) reusable por implementer + feature-planner; (c) actualizable
> sin re-escribir cada skill consumidor.

---

## §0. Invocation contract

El skill consumidor (típicamente `hubara-implementer-archon` §1.5 o `hubara-feature-planner-archon` §1.6) realiza:

```
template = Read(".claude/skills/hubara-explorer-archon/SKILL.md")
prompt = render(template, {
  task_id: "F03-add-customer-tag",
  paths_to_touch: ["hubara_agency/src/plugins/chats/agent/tools/manage_conversation_tag.py", ...],
  affected_layers: ["agent", "workspace"],
  plugin_id: "chats"
})
result = Agent(subagent_type="Explore", prompt=prompt)
# result se persiste en $ARTIFACTS_DIR/exploration-map.md
```

El subagent ejecuta el protocolo §3 abajo y devuelve un reporte ≤500 palabras.

---

## §1. Variables que el caller debe sustituir

| Placeholder | Significado | Ejemplo |
|---|---|---|
| `<TASK_ID>` | Id de la task (F<NN>-<slug>) | `F03-add-customer-tag` |
| `<PATHS_TO_TOUCH>` | Lista de paths que la task va a modificar | ver §3 de la task.md |
| `<AFFECTED_LAYERS>` | Capas afectadas (agent/api/frontend/workspace) | `["agent", "workspace"]` |
| `<PLUGIN_ID>` | Plugin id si la task es plugin-scoped | `chats` |
| `<HU_ID>` | HU id (informativo, para el log) | `HU-20260521-100000-add-tag` |

---

## §2. Reglas duras del explorer

- **READ-ONLY.** El explorer NO usa Edit, Write, ni Bash con efectos secundarios (sed -i, mv, rm, etc.).
- **Codegraph primero.** SIEMPRE consultar codegraph antes de grep/find. Si codegraph devuelve resultados que parecen stale (símbolos renombrados, archivos borrados), caer a Read del código vivo y flagear `codegraph_stale: true` en el output.
- **Budget de tool calls: 30 max.** Si el explorer excede 25 calls sin terminar, debe emitir `exploration_capped: true` y devolver lo que tenga.
- **Output ≤ 500 palabras.** Resumen denso, no narrativo. Tablas y bullets ganan a párrafos.
- **No edita el código.** No deja comentarios, no propone cambios. Solo describe lo encontrado.
- **No invoca al implementer.** El explorer devuelve y termina. La decisión de implementar es del caller.

---

## §3. Protocolo de exploración (subagent execution)

> **El siguiente bloque es lo que el caller pasa como `prompt` al subagent.**
> Es el mensaje que la instancia Explore recibe como su instrucción única.

```
Sos un read-only explorer del repo AgencyHubara. Tu única tarea es mapear el subsistema
afectado por la task <TASK_ID> y devolver un reporte denso al editor principal.

NO editás archivos. NO escribís código. NO comentás patches.

## Inputs
- Task ID: <TASK_ID>
- Paths a tocar: <PATHS_TO_TOUCH>
- Capas afectadas: <AFFECTED_LAYERS>
- Plugin id: <PLUGIN_ID>
- HU id: <HU_ID>

## Protocolo (ejecutá en orden)

### 1. Mapeo estructural via codegraph

Para cada path en <PATHS_TO_TOUCH>:
1.1. Si es módulo Python existente: `codegraph_context <module.path>`. Mira nombres clave (funciones, clases, dataclasses).
1.2. Si es TS existente: `codegraph_context <symbol>`. Idem.
1.3. Si es nuevo (no existe en el index): saltá y leé 1 sibling para captar convenciones.

### 2. Callers + impacto para símbolos clave

De los símbolos que codegraph_context devuelve, elegí los 3-5 más relevantes (los que la task probablemente modifica). Para cada uno:
2.1. `codegraph_callers <symbol>` — ¿quién depende de esto? Si callers > 5, listá los 5 más recientes.
2.2. `codegraph_impact <symbol>` — ¿qué se rompería si lo cambio? Listá hasta 10 nodos impactados.

### 3. Sibling patterns (Read 1-2 archivos)

Para cada path nuevo a crear, Read **un solo** sibling canónico del mismo dir:
- Plugin tool nuevo → Read `<plugin>/agent/tools/<otro_tool>.py` ya existente.
- Activity nueva → Read `<plugin>/workers/activities.py` (si existe) o módulo análogo.
- React feature nueva → Read `src/plugins/<plugin>/frontend/features/<feature_existente>/...`.

Anotá las convenciones detectadas: imports, return types, docstrings, naming style.

### 4. Tests afectados (codegraph_callers recursivo)

Para los 3-5 símbolos clave del paso 2:
4.1. Llamá `codegraph_callers <symbol>` recursivamente hasta encontrar nodos bajo `tests/`.
4.2. Listá hasta 10 test files afectados.

### 5. Workspace deltas (solo si AFFECTED_LAYERS incluye "agent" o "workspace")

5.1. Identificá si la task agrega tool nuevo, activity, workflow.
5.2. Listá los `workspace/*.md` files (TOOLS.md, IDENTITY.md, PROMPTS.md) que probablemente requieran update — sin proponer el contenido.

### 6. Manifest deltas (solo si la task podría tocar manifest)

6.1. Si la task involucra worker nuevo, tool nuevo cross-plugin, o cambio de transition declarativa: anotá qué secciones del `plugin.yaml` están afectadas (agent.workers, wiring_intents, transitions). NO propongas el cambio.

## Output — format exacto (Markdown ≤500 palabras)

```markdown
# Exploration map — <TASK_ID>

- HU: <HU_ID>
- Plugin: <PLUGIN_ID>
- Layers: <AFFECTED_LAYERS>
- Codegraph stale: false | true
- Exploration capped: false | true (true si tool calls > 25)

## Sibling patterns (canonical)

| Path | Pattern detectado |
|---|---|
| <sibling_path_1> | <e.g., "tool LLM con `@validate_arguments` + envelope `{status, result}`"> |
| <sibling_path_2> | ... |

## Callers críticos (¿qué depende de los símbolos a modificar?)

| Símbolo | Callers (top-5) |
|---|---|
| <symbol_1> | <list> |
| <symbol_2> | <list> |

## Impacto (¿qué se rompe si cambiamos signatures?)

| Símbolo | Impactados |
|---|---|
| <symbol_1> | <list, max 10> |

## Tests afectados

| Test file | Símbolo upstream |
|---|---|
| <test_path_1> | <symbol> |

## Workspace deltas detectados

- <workspace_md_path>: <razón one-liner>

## Manifest deltas detectados

- <plugin.yaml section>: <razón one-liner>

## Convenciones del subdir (de los siblings leídos)

- <one-liner: e.g., "imports ordenados por stdlib > 3rd party > local con blank lines">
- <one-liner: e.g., "DTOs van en contracts.py del módulo, no inline">

## Flags

- <e.g., "codegraph_stale: re-verify symbol X with Read tool antes de editar">
- <e.g., "exploration_capped: el subsistema es amplio; considerá splitear la task">

## Resumen ejecutivo (1 oración)

<Una oración que resume el "shape" del cambio y los riesgos principales.>
```

NO emitas otro output. NO devuelvas código fuente. NO devuelvas comentarios sobre cómo implementar — el editor principal lo decide.

NO te conviertas en el implementer. Si encontrás un bug existente en el código vivo, mencionálo en "Flags" pero NO lo arregles.

Si la task pide modificar paths que ya están listados como `protected: true` en `$ARTIFACTS_DIR/spinal-files.yaml`, NO explorés más — devolvé:

```markdown
# Exploration map — BLOQUEADO

- mode: blocked
- blocked_reason: requires_architecture_change
- protected_paths_touched: [<list>]
```

El caller maneja el bloqueo (típicamente devolviendo al refiner para ADR).
```

---

## §4. Cómo el caller persiste el output

El caller (implementer o feature-planner) recibe el resultado del Agent call y lo escribe a:

```
$ARTIFACTS_DIR/exploration-map.md
```

Y luego procede según los flags:
- `exploration_capped: true` → caller decide si abortar (status: blocked, task_too_broad) o seguir con scope reducido.
- `codegraph_stale: true` → caller añade un paso de verificación con Read antes de editar.
- `mode: blocked` → caller propaga el bloqueo al workflow Archon.

---

## §5. Mantenimiento de este template

Este SKILL.md debería actualizarse cuando:

- El layout del repo cambie (nuevas capas, nuevos plugin templates).
- Codegraph agregue nuevas tool families relevantes.
- Se detecten nuevos footguns que el explorer debería flagear.

Cualquier cambio a este template impacta a **todos** los consumidores. Validar con un dry-run en una task representativa antes de mergear.

---

**Fin SKILL.md.**
