# 03 · El panel de comandos (definition-of-done)

Cada gate es un comando exacto con exit code. "¿Está bien?" no se razona: se
ejecuta el verbo y se lee el `0`/`1`. Corré `/graphagents-gates [scope]`, o los
comandos a mano desde `GraphAgents/`.

> **Runner (L-3):** a mano usá **`python3 -m pytest` / `python3 -m sdk.cli`** —
> NO `uv run` (el pre-bash hook del monorepo lo bloquea fuera de `hubara_agency`,
> y local no hay `.venv`). El python del sistema tiene pydantic/pyyaml/pytest y las
> capabilities importan langgraph lazy, así que el camino puro corre igual. El panel
> `/graphagents-gates` autodetecta el intérprete (python3 local · `uv run` en Docker).

## El comando único (DoD de cualquier cambio)

```
/graphagents-gates all
```

## Por gate

| Scope | Comando (desde `GraphAgents/`) | Caza | Exit |
|---|---|---|---|
| `manifests` | `python3 -m sdk.cli check` | schema C0 · `archetype`/`strategy` en enum · refs `capability:` (C1) | 0 · 1 |
| `arch` | `python3 -m pytest tests/architecture -q` | reglas G-* del TestKit + validez de todos los manifests + el serializador `sdk.graph` | 0 · 1 |
| `cert` | `python3 -m pytest tests/conformance -q` | TCK por agente (cada agente instancia su check, niveles C0–C3) | 0 · 1 |
| `tools` | `python3 -m sdk.cli certify-tool` + `python3 -m pytest tests/tools -q` | per-tool TCK: T-CONTRACT · T-DUR · G-AGNOSTIC + golden de la impl | 0 · 1 |
| `graphs` | `python3 -m pytest tests/graphs -q` | golden-replay (G-DET: fixture → output exacto) | 0 · 1 |
| `cases` | `python3 -m sdk.cli cases --check` | el catálogo de CASOS de ejecución (`fixtures/cases/*.case.yaml`): golden desactualizado · `$ref` roto · target inexistente · port sin fixture vendor. **Todo tool/agente/flujo nuevo cierra con su caso** (00-tdd-law §caso de ejecución) | 0 · 1 |
| `integration` | `python3 -m pytest tests/integration -q` | el manifest compila a runnable y CORRE sobre el runtime port (+ recovery + backend del explorer) | 0 · 1 |
| `viewer` | `python3 -m pytest tests/architecture/test_system_graph.py tests/integration/test_viewer_api.py -q` | el grafo del sistema (`sdk.graph`) + la API del explorer (`api_route` sin socket) | 0 · 1 |

## El explorer visual (catálogo + grafo + marketplace)

```bash
cd GraphAgents
python3 -m sdk.cli graph                 # el grafo del sistema en mermaid (se ve en GitHub)
python3 -m sdk.cli graph --format json   # el mismo grafo como JSON (lo come cualquier UI)
python3 -m viewer.server                 # el explorer vivo → http://localhost:8900
```

Read-only: PROYECTA los manifests (no los muta). Corre agentes tool-only por el
LocalRuntime desde la UI. Verificá el HTTP local con `urllib`/el browser, no `curl` (L-4).

## Certificar un agente

```bash
cd GraphAgents && python3 -m sdk.cli certify <id>   # exit 1 si < C2
```

## Recordatorios

- **Tests verdes ≠ feature viva.** Un cambio de comportamiento se verifica
  corriendo el grafo real sobre AgentSpan: `docker compose up -d agentspan` (server +
  UI en :6767), `AgentSpanRuntime().run(build_agent(node), input)`, y mirá la ejecución
  en **:6767** (receta §2.5d) + probá recovery + las HUMAN tasks (`approval_required`).
- **Tests de langgraph/agentspan → en el container.** Importan deps pesadas (no están en
  el python del sistema): arrancan con `pytest.importorskip` (local SKIP) y corren con
  `docker compose run --rm --no-deps graphagents /opt/venv/bin/python -m pytest …` (L-7).
- **Editaste un `.py` del viewer/sdk en Docker?** `docker compose up -d --force-recreate
  graphagents` (NO `restart` — el bind-mount no re-sincroniza + Python no hot-reloadea, L-9).
- **Hook Stop:** si tocaste `manifests/`, `sdk/`, `graphs/` o `tools/`, los gates
  de cert+arquitectura corren SOLOS al cerrar. Para que un rojo **bloquee** el
  cierre: `export GRAPHAGENTS_STOP_GATE_BLOCK=1`.
- **No fixes a ciegas:** rojo → verde → refactor con tu contexto completo. Si un
  gate de cert falla, el rojo correcto suele ser el caso negativo del check.
