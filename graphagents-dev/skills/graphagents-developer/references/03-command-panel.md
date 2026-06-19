# 03 · El panel de comandos (definition-of-done)

Cada gate es un comando exacto con exit code. "¿Está bien?" no se razona: se
ejecuta el verbo y se lee el `0`/`1`. Corré `/graphagents-gates [scope]`, o los
comandos a mano desde `GraphAgents/` con `uv run`.

## El comando único (DoD de cualquier cambio)

```
/graphagents-gates all
```

## Por gate

| Scope | Comando (`cd GraphAgents &&`) | Caza | Exit |
|---|---|---|---|
| `manifests` | `uv run python -m sdk.cli check` | schema C0 · `archetype`/`strategy` en enum · refs `capability:` (C1) | 0 · 1 |
| `arch` | `uv run pytest tests/architecture -q` | reglas G-* del TestKit + validez de todos los manifests | 0 · 1 |
| `cert` | `uv run pytest tests/conformance -q` | TCK por agente (cada agente instancia su check, niveles C0–C3) | 0 · 1 |
| `tools` | `uv run python -m sdk.cli certify-tool` + `uv run pytest tests/tools -q` | per-tool TCK: T-CONTRACT · T-DUR · G-AGNOSTIC + golden de la impl | 0 · 1 |
| `graphs` | `uv run pytest tests/graphs -q` | golden-replay (G-DET: fixture → output exacto) | 0 · 1 |
| `integration` | `uv run pytest tests/integration -q` | el manifest compila a runnable y CORRE sobre el runtime port (+ recovery por execution-id) | 0 · 1 |

## Certificar un agente

```bash
cd GraphAgents && uv run python -m sdk.cli certify <id>   # exit 1 si < C2
```

## Recordatorios

- **Tests verdes ≠ feature viva.** Un cambio de comportamiento se verifica
  corriendo el grafo real sobre AgentSpan: `agentspan server start`, `runtime.run`,
  y probá recovery (matás el proceso, recuperás por `execution-id`) + las HUMAN
  tasks de las acciones con `approval_required`.
- **Hook Stop:** si tocaste `manifests/`, `sdk/`, `graphs/` o `tools/`, los gates
  de cert+arquitectura corren SOLOS al cerrar. Para que un rojo **bloquee** el
  cierre: `export GRAPHAGENTS_STOP_GATE_BLOCK=1`.
- **No fixes a ciegas:** rojo → verde → refactor con tu contexto completo. Si un
  gate de cert falla, el rojo correcto suele ser el caso negativo del check.
