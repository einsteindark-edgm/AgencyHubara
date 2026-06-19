---
name: graphagents-developer
description: |
  Harness de desarrollo para GraphAgents — agentes de análisis de datos de Meta
  Ads construidos con LangGraph (task graphs deterministas) sobre el runtime
  durable AgentSpan, orquestados por manifests YAML. Úsalo SIEMPRE que la tarea
  toque GraphAgents/: agregar o modificar una capability (StateGraph), una tool,
  un connector a Meta, un manifest taskgraph.yaml, el SDK (manifest_model,
  testkit, cli, loader), o correr los gates de certificación/arquitectura.
  Programa con TDD obligatorio (rojo→verde→refactor): el rojo de una capability
  es un golden-replay que falla. Dispara aunque no digas "skill": "agregá un
  agente de ads", "nueva capability de roas", "componé el supervisor", "por qué
  falla el gate", "certificá el agente". NO lo uses para el monorepo (ese es
  hubara-plugin-developer) — son arquitecturas separadas que se unen recién en
  la fase B por un puente HTTP/execution-id.
---

# graphagents-developer — el harness de GraphAgents

GraphAgents es un subsistema **aparte** del monorepo. Toma prestados los
*conceptos* de AgencyHubara (manifest-driven, SDK + kits, TCK + certificación,
archetypes, gates, TDD) pero **no importa su código**. Se programa igual de
disciplinado, contra su propio panel determinístico.

## El modelo en una página

- **Un runtime durable:** AgentSpan/Conductor. Retry por task, replay, estado
  durable, HUMAN tasks, `execution-id` cross-process. No lo construimos: lo usamos.
- **El "task graph"** = el DAG de Conductor en que TODO compila.
- **Dos superficies de autoría → el mismo task graph:**
  1. **YAML declarativo** (`manifests/*.yaml`): agentes/subagentes + `strategy`
     (`handoff|router|parallel|swarm|...`) + `tools`. Es el `plugin.yaml` de este
     mundo. AgentSpan lo trae nativo; nosotros lo extendemos (ver abajo).
  2. **LangGraph `StateGraph`** (`graphs/*.py`): la capability determinista de UN
     agente. AgentSpan la compila a tasks tipadas (preserva nodos/edges/reducers/
     retries/HUMAN tasks).
- **Determinismo:** lo ponés en cómo escribís los nodos del `StateGraph` (puros);
  el LLM/IO va en nodos marcados. El rojo de TDD es un **golden-replay**.
- **Manifest superset:** nuestro `taskgraph.yaml` = el YAML nativo de AgentSpan
  + nuestras llaves (`archetype`, `capability`, `consumes`, `certification`,
  `approval_required`). El `loader` valida, certifica y despacha cada nodo a la
  API correcta de AgentSpan; las llaves ext se quitan antes de `agentspan deploy`.

Arquitectura completa y layout: `GraphAgents/README.md`.

**Tools = unidad de catálogo de primera clase.** Una tool vive en `tools/<id>/`
(contrato `tool.yaml` + `impl.py` PURA + `adapters/`), se certifica sola
(`cli certify-tool`) y se enchufa a cualquier agente por `uses:` + `with:`
(binding, G-BIND). La impl NO importa runtime (G-AGNOSTIC); los adapters sí. Es
la base de la visión "n8n de agentes": tools agnósticas reusables en un palette.
Receta: `references/02-recipes.md` §2.0.

**Agentes = la otra unidad de catálogo.** Un agente vive en
`manifests/<id>.agent.yaml`, se referencia por `uses: agent://<id>@1` (reuso, no
inline; G-BIND-AGENT), se invoca como tool de otro (`exposes_as_tool`) y se
publica hacia afuera (`publish: {as: mcp|http}`) por `execution-id`. `cli
list-agents` muestra el catálogo. Receta: `references/02-recipes.md` §2.5b.

**El runtime es un port** (`sdk/runtime.py`): `LocalRuntime` (determinista,
dev/tests, recovery por `execution-id`) y `AgentSpanRuntime` (el real, G1+). El
loader compila el manifest a un callable con `build_runnable` (resuelve refs,
inyecta ports + tools del catálogo). Una capability es `run` puro (G-DET, golden)
+ `build` (StateGraph, G1+). Reglas en `01-graph-rules.md` §runtime; receta §2.5c.

**El explorer visual** (`viewer/`): una **proyección read-only** del catálogo —
NO un editor (los manifests siguen siendo la verdad). El serializador
`sdk/graph.py` (`build_graph`/`to_mermaid`) es la ÚNICA fuente que alimenta tres
frontends: el CLI (`graph --format mermaid|json`), el visor Cytoscape
(`viewer/index.html`, zero-build) y el backend vivo (`viewer/server.py`, stdlib
`http.server`: `/api/graph` + `/api/run`). `docker compose up viewer` →
http://localhost:8900. Toda vista nueva LEE de `sdk.graph`, no reparsea manifests.
Receta §2.8; lecciones L-3 (runner python3), L-4 (curl truncado), L-6 (stdlib).

## TDD obligatorio (rojo → verde → refactor) — sin excepción

No escribís una línea de producción sin un test que **falla primero** y lo exige.
- **El rojo de una capability** es un golden-replay: dado un fixture de datos
  (`fixtures/`), el grafo produce EXACTAMENTE este output. Un rojo por
  `ImportError`/colección NO cuenta.
- **El rojo de una tool** es su decision payload. **El rojo del sdk/manifest** es
  un check del TestKit (el caso NEGATIVO primero: fabricá el manifest roto y
  probá que el check lo caza).
- Pasos de minutos, un comportamiento por vuelta. Método por capa:
  `references/00-tdd-law.md`.

## Las reglas duras (qué gate te frena)

`G-DET` · `G-STATE` · `G-ISO` · `G-SPAN` · `G-PORT` · `G-DUR` · `G-CERT`.
Tabla completa con síntoma → fix: `references/01-graph-rules.md`. La de oro:
**ningún campo nuevo del manifest sin su check** (campo + `loader` + `testkit`
en el mismo cambio).

## Verificación determinística

Corré el panel: **`/graphagents-gates [arch|cert|graphs|manifests|viewer|all]`**.
Cada gate es un comando con exit code. Detalle: `references/03-command-panel.md`.
A mano usá `python3 -m pytest` / `python3 -m sdk.cli`, NO `uv run` (L-3).
Recordá: tests verdes ≠ feature viva — un cambio de comportamiento se verifica
corriendo el grafo real sobre AgentSpan (recovery por `execution-id`).

## Recetas (paso a paso, test-first)

`references/02-recipes.md`: agregar un manifest · una capability (StateGraph) ·
una tool (con approval) · un connector a Meta · componer el supervisor ·
certificar · el explorer visual (catálogo + grafo + marketplace, §2.8) · el
puente a la fase B (integración al plugin `ads` del monorepo).

## Subagents

- `graph-explorer` — mapea una zona de GraphAgents antes de editar (read-only).
- `graph-tdd-author` — escribe el golden rojo y confirma que falla por la razón
  correcta. NO escribe producción.
- `graph-cert-reviewer` — corre el TCK + audita contra las reglas G-* y las
  lecciones antes de cerrar. Read-only.

## Lo que NO repetir

`references/04-lessons.md` (append-only, formato §9: Síntoma → Causa → Fix →
Regla → Guard). Cuando un run real revela un bug, el primer artefacto es un
guard rojo que lo reproduce.
