# 02 · Manifest tipado (`PluginManifest`) + campo `archetype`

> Fase F-SDK-1 · Gate: `tests/architecture/test_manifest_model.py` (contrato realidad↔modelo↔schema)

## Qué problema soluciona

El `plugin.yaml` era la SSoT… **sin validador**. Tres lectores independientes
(plugins-sync.ts, main.py, run_workers.py) parseaban el YAML crudo y cada uno
chequeaba SU pedacito. Consecuencias reales encontradas al tipar:

- `signal_with_start` se usaba en 4 transitions reales pero **no existía en el
  enum del schema** (drift desde L-8 — nadie validaba contra el schema).
- El bloque `dashboard:` de los workers (3 usos, y los gates P-15/P-17
  dependen de él) **no estaba declarado** en el schema.

Un manifest malformado se descubría en runtime (o nunca). Ahora se descubre
en C0: `parse_manifest()` rechaza con paths accionables.

## Cómo funciona

- `src/sdk/manifest_model.py` define los modelos pydantic espejando el schema
  **con su misma strictness**: `extra="forbid"` exactamente donde el schema
  dice `additionalProperties: false` (top-level, frontend, api, agent,
  transitions, action, deployment, compose, dashboard, permissions);
  `extra="allow"` donde el schema es abierto (worker items, wiring_intents).
- Los **enums viven UNA sola vez** en el modelo (`Archetype`, `Via`) y el
  contract test exige que el schema YAML los espeje EXACTO — el drift
  modelo↔schema (clase L-10) truena en CI.
- `archetype:` es la identidad arquitectónica del plugin (ver
  [05-arquetipos.md](05-arquetipos.md)). Clasificación vigente:

| Plugin | archetype | Por qué |
|---|---|---|
| `ads` | `full_stack` | frontend completo + api de agregación local |
| `agents_admin` | `full_stack` | frontend completo + api introspectora |
| `catalog` | `sync` | pipeline Medusa → snapshot → Meta Catalog |
| `chats` | `agentic` | workers conversacionales con workspace |
| `eta` | `notifier` | push puro post-L-4 (no posee conversación) |
| `orders` | `full_stack` | frontend + api + workers de background |
| `system_map` | `api_only` | backend puro (UI en system_explorer aparte) |

## Cómo se usa

```python
from src.sdk import load_typed_manifest, parse_manifest, PluginManifest

typed = load_typed_manifest("eta")        # ManifestValidationError si C0 falla
typed.archetype                            # "notifier"
typed.agent.workers[0].task_queue          # "queue-eta-agent" (pattern validado)
typed.agent.workers[0].transitions[0].action.via   # Literal tipado
typed.agent.workers[0].schedule            # WorkerSchedule {id, cadence} | None

# Para validar un dict arbitrario (tests, CLI, scaffolder):
parse_manifest({"id": "x", "version": "0.1.0"}, source="mi-test")
```

## Reglas al evolucionar el manifest

1. **Campo nuevo = 3 patas en el MISMO PR**: schema YAML + campo tipado en
   `manifest_model.py` + el check que lo consume (regla de oro — así nació
   `agentic` decorativo y así se evita repetirlo).
2. Si el campo es un **enum compartido con el frontend**, el valor canónico
   vive en `manifest_model.py` y el schema lo espeja (el contract test te
   frena si divergen).
3. Si un manifest real usa algo que el modelo rechaza, NO aflojes el modelo
   por reflejo: primero decidí si el manifest está mal (fix al manifest) o el
   contrato quedó viejo (fix a las 3 patas).

## Campo `schedule:` de un worker (2026-07-10)

Un worker cuyo ciclo lo dispara un **Temporal Schedule** (workflows one-shot
tipo `ReengagementCycleWorkflow`) lo declara en su entry:

```yaml
workers:
  - name: cycle
    schedule:
      id: reengagement-cycle-schedule   # el SCHEDULE_ID del script que lo crea
      cadence: cada 45 min              # texto humano — Acktos Studio lo muestra
```

Las 3 patas: tipado en `WorkerSchedule` (manifest_model) · schema en
`plugin.schema.yaml` · check en `tests/platform/test_worker_schedule_field.py`
(drift guard bidireccional contra `scripts/create_*schedule*.py` — ni
schedules fantasma declarados, ni schedules reales invisibles). Consumidor:
el system map lo proyecta al nodo (`data.schedule` / `has_schedule`) y el
canvas de Acktos Studio dibuja el reloj ⏱ en la cajita.
