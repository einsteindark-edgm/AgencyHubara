# src/sdk — contexto para agentes (la superficie pública de la plataforma)

> Se carga ADEMÁS del CLAUDE.md raíz y el de hubara_agency/ cuando trabajás acá.
> Docs por funcionalidad: `docs/_sdk/` (raíz del repo). ADR: ADR-2026-06-12.

## Qué es esta capa

La **fachada pública** que los plugins importan (`plugins → sdk → platform`).
`src/platform/*` es implementación PRIVADA. Si un plugin necesita algo que no
está acá, el fix es **agregarlo al SDK** (con las 3 patas de abajo), no
importar platform directo — el gate P-28 lo frena.

## Reglas duras de esta capa

1. **Re-export idiom**: `from src.platform.x import y as y` — SIEMPRE con el
   alias redundante. El hook post-edit corre `ruff --fix` y un import sin uso
   ni alias se PODA silenciosamente (lección L-0). Verificá después de editar.
2. **El SDK no importa plugins** (import-linter `sdk-no-plugins`) y
   **platform no importa el SDK** (`platform-no-sdk`). Romper la dirección =
   `uv run lint-imports` rojo.
3. **Sin lógica de negocio en las fachadas** (`__init__`, `foundation`,
   `runtime`, `eventkit`, `agentkit`): solo re-exports + docstrings. La lógica
   nueva del SDK vive en submódulos propios (`testkit/`, `cli/`,
   `connectorkit/`...) con sus tests.
4. **Regla de oro — símbolo/protocolo/port nuevo ⇒ 3 patas en el MISMO PR:**
   (a) check en `src/sdk/testkit/` (o gate en `tests/architecture/`),
   (b) template/uso en el CLI si aplica,
   (c) doc en `docs/_sdk/` (qué soluciona, cómo funciona, cómo se usa).
5. **Kits, no God-module**: `src.sdk` (Foundation) es lo universal; lo demás
   se importa por kit (`src.sdk.agentkit`, `src.sdk.eventkit`, ...). Un
   símbolo va al kit de su ROL, no a `__init__` "para que sea cómodo".
6. **No re-exportar privados** de platform (`_LLM_OPTIONS`, etc.). Si algo
   privado se volvió necesario para plugins, primero se promueve a público en
   platform (con docstring + test) y recién entonces se fachadea.

## Cómo verificar cambios en esta capa

```bash
cd hubara_agency && uv run python -c "import src.sdk, src.sdk.agentkit, src.sdk.eventkit, src.sdk.runtime"
cd hubara_agency && uv run lint-imports
cd hubara_agency && MEDUSA_BASE_URL=http://medusa.invalid MEDUSA_ADMIN_TOKEN=ci-dummy \
  OTEL_SDK_DISABLED=true uv run pytest tests/architecture -q
# en esta rama (PROTECTED tocados): prefijo ARCH_CHANGE_APPROVED=1
```

## Mapa de la capa

| Módulo | Rol | Doc |
|---|---|---|
| `__init__.py` | Foundation: manifest + toggle + protocolos + routing | docs/_sdk/01 |
| `foundation.py` | la implementación del re-export Foundation | docs/_sdk/01 |
| `runtime.py` | vault, metadata store, Temporal client, heartbeat, logging | docs/_sdk/01 |
| `eventkit.py` | canal 2: eventos + dispatcher + transitions (cross-worker, durable) | docs/_sdk/01 |
| `dashboardkit.py` | canal 1: push al dashboard (bus in-process / SSE, efímero) | docs/_sdk/09 |
| `agentkit.py` | workers conversacionales: turn loop + tools + registries | docs/_sdk/01 |
| `manifest_model.py` | `PluginManifest` pydantic (validación C0) | docs/_sdk/02 |
| `diagnostics.py` | catálogo código→mensaje→fix (`explain`) | docs/_sdk/03 |
| `testkit/` | TCK: checks + perfiles de arquetipo + reportes C0–C3 | docs/_sdk/04-05 |
| `cli/` | `uv run python -m src.sdk.cli` (check/certify/explain/create/graph) | docs/_sdk/06 |
| `connectorkit/` | ports de capability + fakes (vendors externos) | docs/_sdk/07 |
| `castkit.py` | canal 3: cast HTTP cross-plugin (porta `Authorization` + semántica honesta) | docs/_sdk/10 |

## Gotchas vividos en esta capa

- `ruff --fix` post-edit poda imports "unused" → usar SIEMPRE el alias idiom
  (regla 1). Si un import desapareció misteriosamente, fue eso.
- El hook pre-bash exige el prefijo literal `cd hubara_agency &&` en todo
  `uv run ...` (aunque ya estés parado ahí: `cd /abs/path && cd hubara_agency
  && uv run ...` desde el repo root).
- `tests/architecture/**` y `.importlinter` son PROTECTED: corridas locales
  con `ARCH_CHANGE_APPROVED=1`, PR con label `architecture-change`.
