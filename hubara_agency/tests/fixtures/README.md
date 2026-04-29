# tests/fixtures - History fixtures para replay tests

Esta carpeta vive **trackeada en git** (`.gitignore` no la excluye). Cada
`history_<workflow>_v<N>.json` es un dump JSON de la history Temporal de un
workflow corrido contra `WorkflowEnvironment.start_time_skipping` con las
activities mockeadas. Sirve como barrera contra regresiones de shape de
history (R-DET / ADR-005).

## Archivos

| Fixture | Workflow | Origen |
|---|---|---|
| `history_sales_session_v1.json` | `HubaraSalesSessionWorkflow` | `generate_fixtures.py` |
| `history_remarketing_session_v1.json` | `RemarketingSessionWorkflow` | `generate_fixtures.py` |

**Post-F7**: la fixture de Sales refleja el patron bootstrap-activity. Su
primer `activityTaskScheduledEvent` es `bootstrap_sales_session_activity`
(simetrico a `bootstrap_remarketing_session_activity` en la fixture de
Remarketing). El input del workflow es `SalesSessionInput(session_id, turn_count=0)`,
no el `SessionInput` completo.

## Convencion de naming

`history_<workflow>_v<N>.json`. **Bumpear `N` cada vez que se cambia
legitimamente la shape de history**: orden / cantidad de activity executions,
signature de signals/queries, tipos de args serializables.

Cuando se bumpea, el fixture viejo se elimina (no se mantiene historico:
para eso esta git).

## Cuando regenerar

Regenerar **siempre** que se haga alguno de estos cambios al codigo del
workflow productivo:

- Agregar / quitar / reordenar `await workflow.execute_activity(...)`.
- Agregar / quitar / renombrar signals (`@workflow.signal`) o queries (`@workflow.query`).
- Cambiar la signature de un signal/query handler (params, tipos).
- Cambiar la signature del `@workflow.run` (DTO de entrada).
- Cambiar `continue_as_new` payload o trigger.

Cambios que **no** requieren regenerar:

- Cambiar logging interno (`workflow.logger.info(...)`).
- Cambiar implementacion interna de una activity (no su nombre ni signature).
- Cambiar prompts / strings de negocio dentro de `domain/policies/`.

Si el `Replayer` se queja con `NonDeterminismError`, el codigo de tu workflow
ya no es replayable contra la fixture: o regeneras (cambio legitimo) o
arreglas el bug (cambio accidental).

## Como regenerar

Desde la raiz de `hubara_agency/`:

```bash
uv run python tests/fixtures/generate_fixtures.py
```

El script:

1. Levanta `WorkflowEnvironment.start_time_skipping`.
2. Registra `HubaraSalesSessionWorkflow` y `RemarketingSessionWorkflow` con
   activities **mockeadas** (mismo `@activity.defn(name=...)` que las
   productivas, payloads minimos deterministicos).
3. Para Sales: arranca el workflow, manda 1 signal `send_message`, deja que
   el `_IDLE_TIMEOUT` (1 min) dispare via time-skipping -> ghost trigger ->
   shutdown.
4. Para Remarketing: arranca el workflow (auto-bootstrap), procesa el
   trigger interno y deja que el `_IDLE_TIMEOUT` (24h) dispare via
   time-skipping -> claim a Ventas -> return.
5. Llama `await handle.fetch_history()` y persiste `to_json()` a disco.

Tras regenerar, correr la suite para confirmar que los replay tests siguen
pasando:

```bash
uv run pytest tests/test_replay_sales.py tests/test_replay_remarketing.py -v
```

## Consumidores

Los tests `tests/test_replay_sales.py` y `tests/test_replay_remarketing.py`
hacen `WorkflowHistory.from_json(...)` y `Replayer.replay_workflow(history)`
contra el codigo actual del workflow.

Cuando F6.7 se aplique (CI gate), estos tests seran obligatorios para mergear
PRs en `main`.

## Referencias

- ADR-005: pruebas del workflow basadas en `WorkflowEnvironment` + replay.
- ADR-009: drain operativo cuando un cambio de shape de history despliega a
  produccion (sin esto los workflows en vuelo lanzan `NonDeterminismError`).
- `docs/refactor/PHASE6.md` seccion F6.2.
