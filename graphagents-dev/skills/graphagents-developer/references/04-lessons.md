# 04 · Lecciones de validación (sección VIVA — append-only)

Formato de cada entrada (copiar tal cual):

```
### L-<n> · <título corto> (<fecha>, <contexto>)
- **Síntoma:** qué se vio (error literal, comportamiento).
- **Causa raíz:** qué regla/mecanismo se malentendió o qué hueco había.
- **Fix aplicado:** commit/PR + qué cambió.
- **Regla para el skill:** la instrucción imperativa de 1-2 líneas a internalizar.
- **Guard:** el gate que ahora lo caza (o "PENDIENTE: <propuesta>").
```

Cuando un run real (sobre AgentSpan) revela un bug, el **primer** artefacto es un
guard rojo que lo reproduce — el "Guard:" se escribe ANTES que el "Fix:".

### L-0 · La arquitectura de AgentSpan no se lee desde la landing (2026-06-18, bootstrap)
- **Síntoma:** se asumió (dos veces) que AgentSpan no tenía ni YAML declarativo
  ni concepto de "task graph", mirando solo el README/landing.
- **Causa raíz:** la landing muestra el SDK Python (`Agent` + `>>` + strategies).
  El YAML declarativo vive en `cli/examples/{simple,multi}-agent.yaml`; el "task
  graph" es el DAG de Conductor (`docs/design/specs/2026-03-20-server-dag-injection-design.md`);
  la compilación de LangGraph → Conductor está en `docs/langgraph-integration.md`.
- **Fix aplicado:** se leyó el TREE del repo (`gh api .../git/trees/main?recursive=1`)
  y los archivos reales antes de diseñar el subsistema.
- **Regla para el skill:** antes de afirmar qué hace/no hace AgentSpan (o cualquier
  framework), leé el árbol del repo y el archivo fuente, no el resumen de la landing.
- **Guard:** procedimiento (sin gate automático).

### L-1 · El execution-id del runtime debe ser determinista (2026-06-18, montaje de G1)
- **Síntoma:** el test de recovery (`resume(eid)` + asertar `ex.id == eid` y output)
  necesita un id estable; con `uuid4`/`time` el replay/golden de una ejecución no
  es reproducible (y el sandbox de Claude prohíbe `Date.now`/`random`).
- **Causa raíz:** se tiende a generar el id con time/random (lo "natural").
- **Fix aplicado:** `LocalRuntime._new_id` es un contador (`local-000001`, ...).
- **Regla para el skill:** el vendor de runtime genera ids DETERMINISTAS (contador,
  no time/random). Es el R-DET del runtime: lo no-determinista no entra en la
  identidad de una ejecución.
- **Guard:** `test_execution_id_es_determinista` (dos runtimes frescos → mismo primer id).

### L-2 · La capability `run` tiene firma uniforme — el loader inyecta ports Y tools (2026-06-18, montaje de G1)
- **Síntoma:** una capability que declara `run(input, *, ports)` (sin `tools`) truena
  con `TypeError: unexpected keyword argument 'tools'` al correrla por el runtime,
  porque `build_runnable` inyecta SIEMPRE `ports=` y `tools=`.
- **Causa raíz:** cada capability usa solo una de las dos inyecciones (meta-insights
  usa ports; roas-cac usa tools), y tienta declarar solo la que usás.
- **Fix aplicado:** firma uniforme `run(input, *, ports=None, tools=None)` en TODA
  capability; el check `G-RUN-SIG` la exige.
- **Regla para el skill:** toda capability expone `run(input, *, ports=None, tools=None)`
  aunque use una sola — el loader inyecta ambas. La lógica pura va ahí; `build()`
  (el StateGraph) es el adapter al runtime real.
- **Guard:** `check_capability_run_signature` (G-RUN-SIG) + `test_run_sig_guard_*`.

<!-- AÑADIR NUEVAS LECCIONES ARRIBA DE ESTA LÍNEA, NUMERADAS L-1, L-2, ... -->
