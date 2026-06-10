# El Protocolo de Plugin — conformidad estructural contra las "mentiras" (fable)

> **La pregunta del operador (2026-06-09):** *"hay una técnica en programación
> que es usar protocolos o interfaces obligatorias de implementar para que un
> sistema funcione — Apple lo hace muy bien. ¿Crees que con ese tipo de
> abstracciones podamos eliminar tantas mentiras a la hora de crear un plugin?"*
>
> **Respuesta corta: SÍ — y ya está implementado.** Este doc explica el
> diseño, qué mentiras mata, cuáles NO puede matar (y qué las mata a ellas), y
> la regla de oro para que el sistema no degenere.

---

## §1. El diagnóstico: por qué había "mentiras"

Una "mentira" del plugin system es **una declaración sin verificador**: el
manifest/schema/doc DICE algo y nada obliga a que el código lo cumpla. Todas
las que encontramos eran de esa familia:

| Mentira histórica | Declaración | Realidad (pre-refactor) |
|---|---|---|
| PM-3 `agentic` | "gatea GET /api/agents" | el código lo ignoraba |
| PM-6 `dashboard.workspace` | "acá vive el workspace" | path stale silencioso |
| PM-2 ruteo eta | template del workflow id | duplicado en 3 lugares, sin guard |
| N-1 `ENABLED_PLUGINS` | "gobierna qué corre" | los containers ni lo recibían |
| N-3 P-9 | "detecta consumo cross-API" | detectaba comentarios |
| F2/F8 entities | "mini-FSD por plugin" | todas centrales, lavando llamadas |

El patrón de Apple que citás (Swift protocols) funciona porque es
**conformidad estructural verificada por el compilador**: no podés DECIR que
conformás `Codable` — o tenés la forma, o no compila. La traducción a este
repo no es una interface de Swift: es **que cada declaración del manifest
tenga un verificador en la capa más temprana posible.**

## §2. El protocolo en tres capas (lo implementado)

Un plugin de AgencyHubara hoy conforma un protocolo verificado en 3 capas:

### Capa 1 — Compilación / análisis estático (la más barata)

- **TS — entry del plugin**: el registry generado fuerza
  `PluginModule = { default: ComponentType }` vía `assertPluginModule` en cada
  `lazy(import(...))` (`plugins-sync.ts`). Un plugin cuyo `index.ts` pierde su
  default-component **no compila** (`tsc -b`, que corre en CI). Antes: error
  críptico del bundler en runtime.
- **TS — contrato shell↔plugin**: `PluginHostState` (`shared/lib/plugin-host`)
  reemplaza el bag de props `any`. Un Page que pide algo que el host no provee
  no compila.
- **Python — puntos de integración**: `src/platform/plugin_protocol.py`
  (`ApiModule`, `WorkerModule`, `ConversationRouteOwner` — `typing.Protocol`
  *estructural*, sin herencia: el plugin no importa NADA para conformar, igual
  que en Swift).

### Capa 2 — Boot fail-fast (runtime, antes de servir tráfico)

| Guard | Qué mata |
|---|---|
| `main.py` exige `router: APIRouter` + import limpio + `id == dirname` | API declarada inexistente; typo de id silencioso (N-9) |
| `run_workers` exige `async def main()` | worker declarado sin entrypoint |
| `plugin_loader.validate_enabled` (P-6) | habilitar un plugin sin sus deps duras |
| `plugin_runtime.ensure_plugin_enabled` (P-21) | container huérfano/stale sirviendo un plugin apagado (PM-1) |
| `routing._build_registry` (F6) | rutas colisionadas / core / template roto |

### Capa 3 — CI (el conformance gate — bloquea merge a main)

`tests/architecture/test_plugin_conformance.py` + vecinos: **toda superficie
declarada existe de verdad.**

| Campo del manifest | Verificador |
|---|---|
| `api.python_module` / `legacy_routers` | P-1 (self-contained) + boot capa 2 |
| `agent.workers[].module` | P-1 + capa 2 + P-21 (self-gate por AST) |
| `agent.workers[].task_queue` | premortem (declarada + única) + P-16 (self-ref) |
| `agent.workers[].workflow_classes` | orchestration-consistency (AST `@workflow.defn`) |
| `agent.workers[].emits` / `transitions` | orchestration-consistency (4 checks) |
| `agent.workers[].dashboard.workspace` | P-15 (existe en disco) |
| `agentic` | P-17 (⟺ dashboard workers) + el código LO HONRA (F1) |
| `owns_route` + `route_workflow_id_template` | P-18 ×3 (registry resuelve · transitions atadas al prefijo del dueño · cero hardcode en el ruteo) |
| `depends_on` | P-6 (validate al boot, testeado) |
| `consumes` (cast) | P-14 (forma + provider∈depends_on + cast existe) |
| `frontend.contributes.*.icon` | P-12 (resuelve en base ∪ contribuciones) |
| `frontend.entry` | plugins-sync (existe) + registry test |
| `wiring_intents.env_vars_required` | **P-25** (presente en el compose renderizado) |
| dir backend sin manifest | **P-26** (huérfano = fallo) |
| compose/k8s vs manifests | P-20 + drift + paridad k8s |
| consumo cross-plugin real | P-9 (estricto) + P-22 (ownership de entities) + P-23 (literales /api en CÓDIGO) |

## §3. La regla de oro (lo que mantiene esto vivo)

> **Ningún campo nuevo del manifest sin su check de conformidad, en el mismo
> PR.** Un campo sin verificador es una mentira en potencia — exactamente como
> nació `agentic` (PM-3).

Operativamente: si agregás un campo a `_schema/plugin.schema.yaml`, el PR debe
agregar (a) el consumidor real del campo y (b) el test en
`test_plugin_conformance.py` (o el archivo de gates que corresponda) que ata
campo ↔ código. El meta-gate hace que tocar esos tests requiera el label
`architecture-change` — la conformidad no se relaja en silencio.

## §4. Qué NO pueden matar los protocolos (y qué lo mata)

Honestidad sobre los límites — los protocolos verifican **forma**, no
**comportamiento ni valores**:

1. **Strings/templates duplicados** (PM-2): ningún type-system ata
   `f"eta-{session_id}"` de un archivo al template de un yaml. Lo mata la
   **declaración única + registry** (F6: el template vive UNA vez en el
   manifest del dueño; P-18 prohíbe la segunda copia). Moraleja: cuando la
   mentira vive en un string, la cura no es un protocolo — es **eliminar la
   copia** y derivar todo de una declaración.
2. **Comportamiento** (gotcha #1, PM-13): wiring consistente ≠ dispatch
   funcionando. Lo matan los **smokes de comportamiento** (los DoD de F2/F5/F6
   incluyeron smokes reales; el functional E2E del dispatch sigue siendo el
   complemento recomendado — `tests/functional/`).
3. **Valores del entorno** (secrets reales, endpoints vivos): P-25 verifica
   que la KEY esté en el artefacto, no que el VALOR sea válido. Eso es
   monitoreo/health-checks, no CI.

## §5. Cómo se crea un plugin hoy (la experiencia post-protocolo)

1. `plugin.yaml` — la **declaración de conformidad** (id=dirname, api, workers
   con queue/workflows/workspace, `agentic` si tiene dashboard, `owns_route`
   si posee ruta, `depends_on`/`consumes` si consume).
2. Código bajo `plugins/<id>/` en ambos stacks — **y nada más** (INV-1):
   entities en `frontend/entities/`, glifos en `frontend/icons.tsx`,
   selección vía `useSelection("<id>")`, worker con
   `ensure_plugin_enabled("<id>")` primero.
3. `uv run pytest tests/architecture && npm run test:arch` — la conformidad
   completa en local; CI la repite y **bloquea el merge** si mentís.

Si los gates pasan, el plugin **no puede** estar mintiendo sobre ninguna
superficie declarada. Esa es la garantía que pediste.
