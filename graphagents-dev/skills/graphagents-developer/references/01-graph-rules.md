# 01 · Reglas duras G-* (qué te frena cada gate)

Las reglas que gobiernan GraphAgents. Análogas a las R-rules/P-rules del
monorepo, pero propias de este subsistema. El gate las hace determinísticas.

| Si hacés esto… | Te frena | Fix |
|---|---|---|
| LLM/IO en el esqueleto del grafo (un nodo no marcado hace `llm.invoke` / red) | **G-DET** (el golden-replay se vuelve no-determinista / review) | aislá el LLM en un nodo marcado (`temperature=0` + structured output); el resto del grafo es puro |
| Estado suelto (`dict` sin tipo) cruzando nodos | **G-STATE** | `State` Pydantic + reducers declarados (`Annotated[list, operator.add]`) |
| Una capability lee el estado de otra / usa globals compartidos | **G-ISO** | la única superficie compartida es el **state contract** del manifest; nada de imports laterales entre `graphs/` |
| Un nodo sin nombre estable / sin span | **G-SPAN** | nombrá el nodo (AgentSpan lo registra como task; el span es su unidad de observabilidad); el TCK chequea que cada nodo emite su task |
| Tomar datos de Meta por fuera del ConnectorKit (un `requests.get` suelto a la Graph API) | **G-PORT** | un **port** en `sdk/connectorkit` + vendor swappable (`live` / `fixture` / `warehouse`); el grafo `consumes:` el port |
| Una tool con efecto outward (gasto, cambio en Meta) sin aprobación | **G-DUR** | `approval_required: true` (→ `@human_task` / HUMAN task de Conductor) + tool **idempotente** (fingerprint + pre-check) |
| Meter al registry / componer un agente `< C2` | **G-CERT** | certificá antes (`uv run python -m sdk.cli certify <id>`); la certificación gobierna el catálogo, nunca el runtime |
| Agregar un campo al manifest sin su check | **regla de oro** | campo en `manifest_model` + el código que lo CONSUME (`loader`) + el check en `testkit/checks.py`, en el MISMO cambio |
| Referenciar `capability: graphs.x:build` que no existe / no importa | **C1** (cli check / conformance) | creá el factory o corregí la ref |
| `strategy` o `archetype` fuera del enum | **C0** (schema) | usá un valor válido (`strategy`: handoff/router/parallel/sequential/swarm/round_robin/random/manual · `archetype`: extractor/analyzer/reporter/supervisor) |

## Reglas de las tools del catálogo (la unidad agnóstica)

Una **tool de catálogo** es una unidad reusable de primera clase: vive en
`tools/<id>/` con su `tool.yaml` (contrato), su `impl.py` PURA y sus `adapters/`.
Se enchufa a cualquier agente por `uses:` (no se reimplementa).

| Si hacés esto… | Te frena | Fix |
|---|---|---|
| La `impl` de una tool importa un runtime (`langgraph`/`agentspan`/`langchain`) | **G-AGNOSTIC** (AST) | movelo a `tools/<id>/adapters/`; la impl queda pura |
| Una tool sin `tool.yaml` (in/out, side-effect, version, impl) | **T-CONTRACT** | escribí el contrato (regla de oro: contrato + impl + adapter + per-tool test) |
| Una tool `side_effect: outward` sin `approval_required` | **T-DUR** | `approval_required: true` (→ HUMAN task durable) + idempotencia |
| `version` no semver / `id` no kebab | **C0** (schema) | corregí |
| Un agente `uses: <id>` que no está en el catálogo | **G-BIND** | creá la tool o corregí la ref; el binding (`with:`) mapea state→input |
| Componer/meter al palette una tool `< C2` | **G-CERT** (tool) | `cli certify-tool <id>` antes |

**Catálogo vs inline:** una tool de catálogo paga el costo (contrato + adapters +
TCK) porque el reuso es real. Un **nodo inline** privado de UNA capability puede
quedar simple (una función dentro del `StateGraph`). No conviertas todo en tool
de catálogo: agnosticidad donde hay reuso.

## Reglas de los agentes de catálogo (la otra unidad reusable)

Un **agente de catálogo** vive en `manifests/<id>.agent.yaml`. Es referenciable
por `uses: agent://<id>@<major>` desde cualquier task graph (no se redefine
inline), puede invocarse como tool de otro agente (`exposes_as_tool: true`) y
publicarse hacia afuera (`publish: {as: mcp|http}`) para que OTRO sistema lo
consuma por `execution-id`.

| Si hacés esto… | Te frena | Fix |
|---|---|---|
| Un task graph `uses: agent://<id>` que no está en el catálogo | **G-BIND-AGENT** | creá `manifests/<id>.agent.yaml` o corregí la ref |
| Redefinir un agente inline en vez de referenciarlo | (olor, no gate) | extraelo a `<id>.agent.yaml` y referencialo — un agente, una definición |
| Publicar / componer un agente `< C2` | **G-CERT** | certificá primero (`cli certify <id>`) |
| `publish` sin un `as` válido (mcp/http) | **C0** (schema) | corregí |

La referencia por id es el reuso real: el mismo `meta-insights` se usa desde N
task graphs y se publica una vez. `cli list-agents` muestra el catálogo.

## Reglas del runtime (ejecución durable)

El runtime es un **port** (`sdk/runtime.py`) con dos vendors: `LocalRuntime`
(in-process, determinista, dev/tests) y `AgentSpanRuntime` (el real, G1+). El
loader compila un manifest a un callable con `build_runnable` (resuelve refs
`agent://`, inyecta ports + tools del catálogo). La capability se ejecuta PURA;
`build()` es el adapter al StateGraph real.

| Si hacés esto… | Te frena | Fix |
|---|---|---|
| Capability `run(input, *, ports)` sin `tools` (o viceversa) | **G-RUN-SIG** | firma uniforme `run(input, *, ports=None, tools=None)` — el loader inyecta ambos (L-2) |
| Capability sin `run` (solo `build`) | **G-RUN-SIG** | exponé el entrypoint puro `run` (el LocalRuntime lo corre sin langgraph) |
| Generar el `execution-id` con time/random | (olor, L-1) | contador determinista — el R-DET del runtime |
| IO/red en el esqueleto de la capability | **G-DET** | dato externo por un port inyectado (`ports[...]`), nunca red cruda |

El determinismo se prueba con golden-replay (la capability pura sobre un fixture);
la durabilidad, con `resume(execution_id)` tras un crash simulado (`tests/integration`).

## Niveles de certificación (igual filosofía que el monorepo)

| Nivel | Significa |
|---|---|
| `none` | el manifest ni valida (falla `C0`) |
| `C0` Declarado | manifest válido, algo declarado no existe |
| `C1` Cargable | todo lo declarado existe (capability importa, ports resuelven), una G-rule falla |
| `C2` Certificado | TCK completo verde (warnings permitidos) |
| `C3` Verificado | conducta: golden-evals sobre datasets reales (reservado) |

## Una fuente, tres frontends

Los checks G-* viven en `sdk/testkit/checks.py` (única fuente). Tres consumidores:
`pytest` (`tests/conformance`, `tests/architecture`), el reporte JSON, y el CLI
(`sdk/cli.py`). El CLI no implementa reglas — delega en el TestKit. Si una regla
vive en dos lados, una va a driftear: centralizá en `checks.py`.

## El borde con el monorepo (no cruzar)

GraphAgents NO importa `hubara_agency`/`frontend_dashboard` ni al revés. La
integración es la **fase B**: un plugin `ads` en el monorepo llama al runtime de
AgentSpan por `execution-id` sobre HTTP y castea resultados bajo `/api/ads/*`.
Único punto de contacto. Si te encontrás importando el monorepo desde
GraphAgents (o al revés), pará: estás fusionando dos arquitecturas que deben
quedar separadas.
