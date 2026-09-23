# ADR 2026-09-23 — Los gates R-* no pueden pasar en vacío (scope por rol + meta-gate)

- **Status**: Accepted
- **Date**: 2026-09-23
- **Context surfacing**: auditoría de los gates DEHA sobre `main` 82055bf6 — R-DET escaneaba 0 archivos.
- **Protected files touched**: `hubara_agency/tests/architecture/conftest.py`,
  `test_r_det.py`, `test_r_heartbeat.py`, `test_r_stateless.py`,
  `test_anti_patterns.py`, `test_gate_scope.py` (nuevo).

## Contexto

`conftest.py` definía el scope de cada rol como tuplas de globs
(`AGENT_WORKFLOWS_GLOBS`, `AGENT_ACTIVITIES_GLOBS`, `AGENT_TOOLS_GLOBS`) y, "por
compatibilidad", alias singulares `AGENT_*_GLOB = AGENT_*_GLOBS[0]`. R-DET,
R-HEARTBEAT, R-STATELESS y la regla de naming de tools (#15) usaban el alias:
`src/*/workflows/*.py` / `src/*/activities/*.py`, que no matchean nada desde que
los agentes viven en `src/plugins/` (PR5). Los gates pasaban en verde sin
auditar nada:

| Gate | Escaneaba | Existían |
|---|---|---|
| R-DET | 0 módulos | 15 módulos de workflow |
| R-HEARTBEAT | 2 archivos de platform listados a mano | 35 módulos de activities |
| R-STATELESS | 2 archivos de platform listados a mano | 35 módulos de activities |
| #15 naming de tools | 2 (`src/platform/tools/`) | 12 en `tools/` de agentes |
| R-JSON | 4 `contracts.py` | 7 `contracts.py` de agentes |

Aun las tuplas plurales dejaban afuera:

1. el layout de agente único `src/plugins/<id>/agent/{workflows,activities}/`
   (catalog, orders) — los patrones exigían un nivel `agent/<agente>/`;
2. las activities definidas en `__init__.py` (marketing, order_sentinel,
   reengagement) — los gates salteaban todo `__init__.py`;
3. 9 módulos con `@activity.defn` fuera de un directorio `activities/` (7 en
   platform + `chats/agent/post_sale_return/activities.py`), de los que
   R-HEARTBEAT y R-STATELESS listaban a mano 2 cada uno;
4. `contracts.py` de agentes con layout único o anidados (`catalog/agent/`,
   `orders/agent/`, `chats/agent/sales_eval/evals/`).

El vacío escondía violaciones reales:

- **R-DET** — `chats/agent/sales/workflows/sales_session.py` importaba
  `schedule_remarketing_workflow_activity` dentro del cuerpo del workflow,
  fuera de `imports_passed_through()`. Inofensivo en runtime hoy (el mismo
  módulo ya estaba importado en passthrough arriba), pero rompe la regla.
- **R-STATELESS** — `__all__ = [...]` (lista mutable a nivel módulo) en 5
  módulos de activities.

## Decisión

1. **Scope de un gate por rol = TODOS los patrones de su tupla ∪ todo módulo
   bajo `src/` que define el entrypoint del rol** (AST: `@workflow.defn`,
   `@activity.defn`, subclase de `ToolBase`). Helpers en `conftest.py`:
   `workflow_modules()`, `activity_modules()`, `tool_modules()`. Los
   `__init__.py` que traen los globs se descartan (shims de re-export); si
   definen el entrypoint entran por el término semántico.
2. Los globs de plugins usan `**`: `src/plugins/*/agent/**/<rol>/*.py` cubre
   `agent/<rol>/` y `agent/<agente>/<rol>/`. `CONTRACTS_GLOBS` suma
   `src/plugins/*/agent/**/contracts.py`.
3. **Se borran los alias singulares** `AGENT_*_GLOB` (la trampa que causó el
   bug) y las listas a mano de archivos de platform en R-HEARTBEAT /
   R-STATELESS (las reemplaza el término semántico).
4. **Meta-gate nuevo** `tests/architecture/test_gate_scope.py`: para R-DET,
   R-HEARTBEAT, R-STATELESS, #15 y R-JSON asserta que (a) el gate escanea un
   set NO vacío y (b) cubre todo módulo de su rol según un ground truth sacado
   del código — directorios del rol encontrados con `rglob` + módulos que
   definen el entrypoint —, sin reusar los globs ni los helpers de
   `conftest.py`. Un layout nuevo que los globs no conozcan pone rojo el
   meta-gate en vez de quedar sin auditar.
5. Las violaciones se arreglan en el código, sin relajar reglas: el import de
   `sales_session.py` entra al bloque `imports_passed_through()` existente; los
   `__all__` pasan a tuplas (inmutables; `import *` se comporta igual).

## Consecuencias

- R-DET, R-HEARTBEAT, R-STATELESS, #15 y R-JSON auditan todo el código de su
  rol; un plugin nuevo entra solo, con el layout que tenga.
- Un layout de agente que los globs no cubran rompe `test_gate_scope` con la
  lista de módulos sin auditar: agregar el patrón o mover el código pasa a ser
  una decisión explícita (y, por vivir en `tests/architecture/`, con label
  `architecture-change`).
- El término semántico parsea todo `src/` una vez por sesión de pytest
  (`lru_cache`).
- Fuera de alcance: `test_r_json_nested_dataclass.py` saltea en silencio una
  entrada de `_TARGET_FILES` que no exista (hoy la única existe).
