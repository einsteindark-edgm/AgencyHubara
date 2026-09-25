# tests/fixtures - History fixtures para replay tests

Esta carpeta vive **trackeada en git** (`.gitignore` no la excluye). Cada
`history_<workflow>_v<N>.json` es un dump JSON de la history Temporal de un
workflow corrido contra `WorkflowEnvironment.start_time_skipping` con las
activities mockeadas. Sirve como barrera contra regresiones de shape de
history (R-DET / ADR-005).

## Archivos

| Fixture | Workflow | Origen |
|---|---|---|
| `history_sales_session_v2.json` | `HubaraSalesSessionWorkflow` | `generate_fixtures.py` |
| `history_remarketing_session_v3.json` | `RemarketingSessionWorkflow` | `generate_fixtures.py` |
| `history_sales_escalation_prepatch_v1.json` | `HubaraSalesSessionWorkflow` | history REAL de prod (run 5ed9af2d, 2026-09-18), saneada |
| `history_sales_tag_closure_prepatch_v1.json` | `HubaraSalesSessionWorkflow` | sintética, generada con el código del commit 4052c29 (PRE `tag-ends-turn-v1`) |
| `history_sales_perception_v1.json` | `HubaraSalesSessionWorkflow` | sintética, clasificador PRENDIDO (`perception-v1`), generada con el código de `lab/integracion` (9afa43b4) |

**`history_sales_tag_closure_prepatch_v1.json` también está CONGELADA y NO se
regenera** (el código que la produjo ya no existe: regenerarla hoy daría la
forma POST-patch y dejaría de proteger nada). Tiene la forma PRE
`tag-ends-turn-v1`: tras `manage_conversation_tag` hay un `llm_chat` extra (el
acuse "Etiqueta registrada." del run b06636a6), en los DOS sitios del corte —
turno de cliente con `customer_message` y turno admin de cierre por ghosting.
Es sintética **a propósito**, a diferencia de la de la escalación: el corte
nuevo se decide por una clave NUEVA del envelope (`tag_closure`), así que una
history real de prod (tool results de forma vieja) jamás lo activa y no puede
proteger el gate. El único caso en que una history sin el marker trae un
envelope que el código nuevo cortaría es la **ventana de versiones mezcladas
de un deploy** (activity con la tool nueva + workflow task con el loop viejo),
y eso es lo que congela. Su control negativo está automatizado
(`test_prepatch_tag_closure_history_breaks_without_the_gate`: sin el gate →
`NondeterminismError 'llm_chat' vs 'record_turn'`). Sesión sintética
`wa_tagclosure`, identidad del worker → `fixture-worker`. Procedencia
reproducible en `generate_tag_closure_prepatch_fixture.py` (requiere el código
del commit 4052c29 y se NIEGA a correr si `workflow_helpers.py` ya trae el
gate). Fixture y generador se borran junto con
`workflow.deprecate_patch("tag-ends-turn-v1")`.

**`history_sales_perception_v1.json` también está CONGELADA y NO se
regenera.** Es una sesión con el clasificador de las capas ①②③ PRENDIDO
(`workflow.patched("perception-v1")`, laboratorio PR 14): turno en `shadow`
(percepción en paralelo, verificación después de enviar) y turno en `on`
(plan, verificación antes de enviar que pide `complement`, turno de sistema del
complemento) y el cierre por ghosting. Una vez que el clasificador corra en
producción, las sesiones en vuelo traen esta forma: cambiar commands en esos
caminos sin su propio gate las rompe al redeployar (L-9). Se generó con el
código ANTERIOR a que la verificación leyera todo lo que el cliente recibe en
el turno (A-PM06): replayea con el código nuevo porque ese cambio solo toca el
payload de `verify_coverage` (L-22). Control negativo automatizado
(`test_the_classifier_history_breaks_without_its_gate`: con `perception-v1` en
False → `NondeterminismError`). Sesión sintética `wa_perception`, identidad del
worker → `fixture-worker`; procedencia en `generate_perception_v1_fixture.py`.
Cuando el clasificador corra unos días en sombra en producción se suman 5–10
historias REALES con el marker, saneadas (A-PM07). Se borra junto con
`workflow.deprecate_patch("perception-v1")`.

**`history_sales_escalation_prepatch_v1.json` es distinta a las demás: está
CONGELADA y NO se regenera.** Es la history real del incidente "Listo, la
conversación quedó en manos del equipo humano." con la forma PRE
`escalation-ends-turn-v1` (un `llm_chat` extra después de `escalate_to_human`).
Prueba que el código nuevo sigue replayeando runs en vuelo del deploy anterior
(L-9), y protege DOS gates: forzar a True `escalation-ends-turn-v1` o
`admin-leak-patterns-v2` la rompe con NondeterminismError (L-20 / L-21).
Saneada antes de commitear: teléfono → sintético clasificado en
`forge/manifest.yaml`, API keys fuera, system prompt y tool definitions
reemplazados por stubs, hostnames del worker → `fixture-worker`. Se borra
junto con el `workflow.deprecate_patch(...)` de ambos gates.

**Post-F7**: la fixture de Sales refleja el patron bootstrap-activity. Su
primer `activityTaskScheduledEvent` es `bootstrap_sales_session_activity`
(simetrico a `bootstrap_remarketing_session_activity` en la fixture de
Remarketing). El input del workflow es `SalesSessionInput(session_id, turn_count=0,
runtime_workspace_path=None)`, no el `SessionInput` completo.

**v2 (PR-A workspace refactor)**: la signature de `bootstrap_sales_session_activity`
cambia de `(session_id: str)` a `(input: SalesSessionInput)` para hacerla extensible
sin romper el shape de la activity en el futuro. La fixture v1 quedo obsoleta y se
elimina al regenerar.

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
