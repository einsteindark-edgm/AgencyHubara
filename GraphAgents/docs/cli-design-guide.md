# GraphAgents CLI — guía de diseño (el compilador determinista del desarrollo)

> Propósito: que **crear** un desarrollo (tool/agente/capability/connector) sea tan
> determinista como **verificarlo**. Hoy verificamos con el CLI; creamos a mano
> (copiando `tools/hello/`) — y lo manual es la fuente de inconsistencia. Esta guía
> especifica el generador que cierra ese hueco.

## Estado actual: SÍ hay CLI (verificación) — FALTA el generador

`sdk/cli.py` ya expone los verbos de **verificación** (delegando en el TestKit —
una sola fuente de reglas):

| Comando | Qué hace | Estado |
|---|---|---|
| `check [<id>...]` | manifests: schema + archetype + refs + binding | ✅ |
| `certify [<id>...]` | manifests: nivel C0–C3 (exit 1 si < C2) | ✅ |
| `certify-tool [<id>...]` | tools: nivel C0–C3 (exit 1 si < C2) | ✅ |
| `list-tools` / `list-agents` / `search <t>` | el palette / catálogo | ✅ |
| `graph [--format mermaid\|json]` | serializa el sistema (alimenta el explorer) | ✅ |
| `run <id> [--input JSON]` | corre un agente **tool-only** por el LocalRuntime | ✅ |
| `create tool <id>` | scaffold determinista de una tool (nace C2 + golden rojo) | ✅ **implementado** (TDD) |
| `create capability\|agent\|connector <id>` | scaffold de las otras unidades | ⏳ pendiente (spec abajo) |

**El gap (en cierre):** `create tool` **ya está** (implementado vía TDD —
`sdk/scaffold.py`, wiring en `sdk/cli.py`): genera el patrón completo (contrato + impl
pura + 2 adapters + test golden) y la tool nace **C2 a nivel contrato** con su golden
**ROJO** por construcción. Falta el resto (`capability`/`agent`/`connector`,
`new-fixture`, `run --fixture`) — spec abajo; hasta tenerlas, esas se copian a mano.

## Por qué un generador = determinismo

Un scaffold determinista hace que **toda unidad nazca idéntica en forma**: mismo
layout, mismo contrato base, mismos adapters, y — clave — **su test rojo ya
escrito**. Eso convierte la ley TDD (rojo→verde→refactor) y la regla de oro
("ningún campo sin su check") en algo **forzado por construcción**, no por
disciplina. El mismo spec produce los mismos bytes: reproducible, revisable,
sin deriva.

## Los comandos que faltan (spec)

### `create tool <id>` — scaffold de una tool agnóstica
Desde flags/spec (`--side-effect pure|read|outward`, `--inputs`, `--outputs`,
`--tags`), genera:
- `tools/<id>/tool.yaml` — contrato (id, version `1.0.0`, side_effect, idempotent,
  `approval_required` auto-`true` si outward [T-DUR], credentials, inputs/outputs,
  `impl: tools.<id>.impl:run`).
- `tools/<id>/impl.py` — `def run(*, ...) -> dict` PURO (sin import de runtime, G-AGNOSTIC).
- `tools/<id>/adapters/{langgraph,agentspan}.py` — los dos adapters (import del runtime
  DENTRO de la función, como `recommend_budget`).
- `tests/tools/test_<id>.py` — el **golden ROJO** (input→output exacto + idempotencia),
  con un `assert` real que falla hasta implementar.
Nace `certify-tool` C1; sube a C2 al implementar `run` y poner verde el golden.

### `create capability <x>` — scaffold de un StateGraph determinista
- `graphs/<x>.py` — `run(input, *, ports=None, tools=None)` puro (firma uniforme, L-2) + `build()` stub.
- `fixtures/<x>.json` — placeholder del dataset golden.
- `tests/graphs/test_<x>_golden.py` — golden-replay rojo.

### `create agent <id> --archetype extractor|analyzer|reporter|supervisor`
- `manifests/<id>.agent.yaml` — con `archetype`, `capability`/`consumes`/`tools`, `certification`.
- Si el spec agrega un campo NUEVO al manifest: emite un recordatorio de la **regla de
  oro** (tocar `manifest_model` + `loader` + `testkit/checks.py` en el mismo cambio) +
  el caso negativo en `tests/architecture/`.

### `create connector <port>` — scaffold de un port + vendors (ConnectorKit)
- Entrada en `sdk/connectorkit/ports.py` (Protocol + `Fixture<Port>` vendor).
- Test con los **4 paths** (éxito · error del vendor · timeout · no-disponible).

### `new-fixture <x> --from-mcp <archivo.json>` — congela un snapshot real como golden
Toma una respuesta cruda del MCP de Meta y la guarda como fixture determinista.
**Crítico para el caso de columnas `results` vacías**: capturar un snapshot real con
"Contactos de mensajes totales"/"nuevos contactos" y `results` vacío como golden del
resolver de señal.

### `run <id> --fixture <port>=<archivo>` — correr agentes que consumen ports
Hoy `run` rechaza agentes con `consumes:` (necesitan vendors inyectados). Falta el
flag para inyectar `FixtureXxx` desde el CLI y poder correr el pipeline completo en seco.

### `gates [arch|cert|graphs|manifests|all]` — el panel en un verbo
Conveniencia que envuelve `check`+`certify`+`certify-tool`+`pytest` con un solo exit
code (hoy eso vive en el comando del plugin `/graphagents-gates`; el CLI podría exponerlo
nativo para no depender del shell del panel).

## Reglas de diseño del CLI

- **No implementa reglas** — delega en `sdk/testkit/` (única fuente). El scaffolding
  reusa las plantillas del catálogo vivo (`hello`/`recommend_budget`), no strings sueltos.
- **Determinista** — sin `time`/`random` en ids ni outputs (L-1). Mismo spec → mismos bytes.
- **Nace rojo** — todo `create` emite primero el test que FALLA (TDD por construcción).
- **Regla de oro recordada** — `create agent`/campo nuevo deja el TODO de los 3 toques
  (modelo + loader + check).

## Cómo lo consume graphagents-dev

- El skill y los hooks llaman `python3 -m sdk.cli create ...` (local) o `uv run` (Docker);
  el panel autodetecta el runner (L-3, el pre-bash hook bloquea `uv run` fuera de Docker).
- Cada receta de `references/02-recipes.md` arranca con un `create` en vez de "copiá
  `tools/hello/`". El generador es el que hace cumplir la receta, no la memoria del que programa.

## Plan de implementación (TDD, bajo el skill)

1. Rojo: `tests/architecture/test_cli_create.py` — `create tool foo` genera los 5 archivos
   y `certify-tool foo` da C1 (y el golden generado FALLA con assert real).
2. Verde: implementar `cmd_create` (reusando las plantillas del catálogo).
3. Iterar por subcomando (`tool` → `capability` → `agent` → `connector` → `new-fixture`).
