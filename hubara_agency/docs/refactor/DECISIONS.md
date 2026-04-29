---
title: Decisiones de arquitectura - Refactor DEHA
last_updated: 2026-04-28
---

# Registro de Decisiones de Arquitectura (ADRs)

ADRs cortos del refactor. Cada uno es la decision de un humano (no del modelo) y persiste entre sesiones para evitar redebatir.

Formato: Date, By, Context, Decision, Consequences.

---

## ADR-001 - Tools NO abren Temporal Client

- **Date**: 2026-04-28
- **By**: edgm
- **Context**: La tool `TransferToSalesAgentTool` (`src/domains/sales_whatsapp/tools/routing.py`) abre el Temporal Client, llama `start_workflow` y `signal` desde dentro de la activity `execute_tool`. Esto rompe el arbol de durabilidad: si el proceso muere a mitad de la secuencia (`start_workflow + signal`), Temporal no recupera estado consistente entre los dos workflows.
- **Decision**: Las tools devuelven un **payload de decision** (e.g. `{"action": "transfer_to_sales", "resumen": "..."}`). El **workflow** lee ese payload y ejecuta una activity dedicada `start_or_signal_sales_workflow_activity` que realiza la transicion. Las activities reciben reintentos automaticos y recovery por Temporal; las tools, no.
- **Consequences**:
  - Las tools se vuelven puras: input -> output JSON, sin side effects de control de orquestacion.
  - Los workflows toman decisiones explicitas (mejor testabilidad y replay).
  - Costo: refactorizar `TransferToSalesAgentTool` y agregar la activity nueva. Se hace en Fase 4.

---

## ADR-002 - Monorepo unico con dominios separados via DEHA

- **Date**: 2026-04-28
- **By**: edgm
- **Context**: El equipo discutio si separar `sales_whatsapp` y `remarketing_whatsapp` en repos independientes para acelerar deploys.
- **Decision**: Mantener un **monorepo unico** (`hubara_agency/`) con dominios separados via layout DEHA. Cada dominio tiene su propio `domain/`, `application/`, `infrastructure/`. Lo compartido vive en `core/` (cross-cutting tecnico) y `shared/` (codigo reusado entre dominios).
- **Consequences**:
  - Fronteras claras dentro del repo (los imports cruzados se detectan facil).
  - Un solo `pyproject.toml`, un solo Worker, varios task queues.
  - Si en el futuro se quiere extraer un dominio a un repo aparte, el corte es trivial porque las dependencias son explicitas.

---

## ADR-003 - Usar `from datetime import timedelta` en vez de `workflow.timedelta`

- **Date**: 2026-04-28
- **By**: edgm
- **Context**: El SDK de Temporal expone `workflow.timedelta` como re-export de `datetime.timedelta`, y el codigo actual mezcla ambos. La documentacion oficial usa `from datetime import timedelta` en todos sus ejemplos.
- **Decision**: Usar **siempre** `from datetime import timedelta` en workflows. Eliminar todas las ocurrencias de `workflow.timedelta(...)`.
- **Consequences**:
  - Un solo modo de declarar duraciones, alineado con la doc oficial.
  - Reduce la confusion sobre que helpers son determinísticos (`timedelta` lo es siempre; `workflow.now()` y `workflow.uuid4()` son los unicos helpers de tiempo/ids necesarios).
  - Diff trivial: F1.1 en Fase 1.

---

## ADR-004 - Remarketing es un agente efimero

- **Date**: 2026-04-28
- **By**: edgm
- **Context**: El workflow `RemarketingSessionWorkflow` tiene la constante `_CONTINUE_AS_NEW_AFTER_TURNS = 50` y el campo `turn_count` en su `SessionInput`, pero el flujo real es: saludar al cliente -> esperar respuesta -> transferir a Sales (o timeout 24h y dejar). Nunca alcanza turn 50.
- **Decision**: Remarketing es **efimero**: saludo + transferencia. NO necesita `continue_as_new` ni `turn_count`. Si el codigo actual los carga, se eliminan en Fase 3.
- **Consequences**:
  - Workflow de remarketing mas simple (menos estado, mas facil de razonar).
  - Si en el futuro queremos remarketing multi-turno, se cambiara explicitamente con un nuevo ADR.
  - Si Remarketing genera muchos eventos (eg. logging excesivo), aun queda margen de history antes de hit el limite de Temporal (~50K events).

---

## ADR-006 - Mantener wrappers `_load_shared_brain` / `_load_remarketing_brain` en Fase 2

- **Date**: 2026-04-28
- **By**: edgm (architect, refactor session)
- **Context**: F2.3 propuso eliminar las dos funciones duplicadas y reemplazarlas por llamadas directas a `load_brain(brain_dir)`. Pero `_load_remarketing_brain` se invoca desde dentro del workflow (`remarketing.py:134, 211`) y desde `service.py`. Cambiar el call site dentro del workflow modifica el grafo de tasks y rompe replay de workflows en vuelo.
- **Decision**: deduplicar el cuerpo (delegar a `load_brain`) pero mantener los wrappers como funciones con la misma firma. El nombre y la signature del callable que el workflow invoca no cambian -> replay safe.
- **Consequences**:
  - Cumple R-DRY a nivel de logica (un solo `load_brain`).
  - Preserva shape de history.
  - Costo: dos funciones de 3 lineas cada una que sobreviven hasta Fase 3 (cuando se mueva la lectura de filesystem fuera del workflow, las funciones desaparecen).

---

## ADR-007 - Tareas extra del usuario en Fase 2 se difieren a Fase 3

- **Date**: 2026-04-28
- **By**: edgm (architect, refactor session)
- **Context**: El usuario propuso 7 tareas para Fase 2 (incluyendo `RemarketingSessionInput` dataclass, mover `integrations.py` a `infrastructure/whatsapp/`, y setup de testing). El PLAN.md original solo tiene 6 tareas para Fase 2.
- **Decision**: ejecutar las 6 tareas del PLAN.md y diferir las 3 extras a Fase 3 con IDs F3.6, F3.7, F3.8. Justificacion: las tres extras alteran shape de history (cambio de signature de workflow.run) o requieren replay tests, lo que segun ADR-005 es obligatorio desde Fase 3.
- **Consequences**:
  - PLAN.md sigue siendo la fuente de verdad.
  - Las tareas no se pierden (estan documentadas en PLAN.md como F3.6-F3.8).
  - Fase 3 tiene mas trabajo pero es el lugar correcto para hacerlo (con tests).

---

## ADR-008 - `integrations.py` se mantiene como shim deprecated

- **Date**: 2026-04-28
- **By**: edgm (architect, fase 3)
- **Context**: F3.7 movio el cliente HTTP de WhatsApp y su activity a `src/core/infrastructure/whatsapp/`. El path original `src/domains/sales_whatsapp/integrations.py` podria ser eliminado completamente (grep confirmo cero call sites in-repo), pero scripts externos / CLIs / dashboards futuros podrian importarlo todavia.
- **Decision**: dejar `integrations.py` como un shim de una sola linea (`from src.core.infrastructure.whatsapp.client import send_message`), marcado como DEPRECATED en su docstring. Eliminacion fisica del archivo se hace en una iteracion posterior cuando se confirme cero adopcion.
- **Consequences**:
  - Cero blast radius en este PR.
  - Hay un test (`test_legacy_integrations_shim_redirects_to_infrastructure`) que verifica la equivalencia de simbolos entre el shim y el modulo nuevo. Si el shim diverge silenciosamente, el test rompe.
  - Costo: un archivo extra de 3 lineas hasta su retirada definitiva.

---

## ADR-009 - Cambios de shape de history son aceptables en Fase 3 si hay drain previo

- **Date**: 2026-04-28
- **By**: edgm (architect, fase 3)
- **Context**: F3.1, F3.2 y F3.6 introducen activities nuevas (`decide_ghosting_action`, `build_remarketing_trigger_activity`) y cambian la firma de `RemarketingSessionWorkflow.run` (de `(session_id, motivo)` a `(input: RemarketingSessionInput)`). Ambos cambios alteran la shape de history -> workflows en vuelo no pueden replayearse contra el codigo nuevo.
- **Decision**: aceptar el cambio. Mitigacion operativa, no arquitectonica:
  1. **Drain workflows en vuelo antes de deploy**: pausar la entrada de nuevos signals (ej. via feature flag en `process_incoming_message`), esperar a que los workflows existentes terminen (`_force_shutdown` o `_IDLE_TIMEOUT` los cierra en <= 24h), y solo entonces deploy.
  2. **Alternativa con `workflow.patched(...)`**: descartada por sobrecostes en complejidad (cada cambio de prompt seria un nuevo patch ID).
- **Consequences**:
  - Fase 3 mantiene momentum sin cargar deuda de versionado.
  - Operaciones tiene que coordinar el deploy con el drain (script en `Makefile`/`scripts/` queda pendiente).
  - Si el drain no se ejecuta, los workflows en vuelo lanzaran `NonDeterminismError` y caeran. La prioridad es: mejor que fallen rapido a que sigan corriendo con history corrompida.

---

## ADR-010 - El payload de decision viaja como JSON dentro del `tool_result`, no como dataclass

- **Date**: 2026-04-28
- **By**: edgm (architect, fase 4)
- **Context**: la tool emite una decision (`TransferDecision`, `ScheduleRemarketingDecision`). Hay tres opciones para llevarla del cuerpo de la activity `execute_tool` al workflow:
  1. Modificar la signature de `execute_tool` para que devuelva `tuple[str, Optional[Decision]]`.
  2. Embed la decision como JSON dentro del string que devuelve la tool al LLM.
  3. Crear un side-channel (queue, archivo, etc.).
- **Decision**: Opcion 2. La tool retorna JSON con campos conocidos (`transfer_decision`, `schedule_remarketing`, `message`). El helper `run_agent_turn` parsea ese JSON con `_try_parse_decision_payload`. Si el parse falla, el resultado se trata como texto plano (compatibilidad con tools que no emiten decisiones).
- **Consequences**:
  - Cero cambio de shape en `execute_tool` -> activities productivas existentes no rompen.
  - El LLM ve el JSON completo (no solo `message`); para mantenerlo enfocado, el `message` esta primero en la respuesta humana.
  - Costo: cada tool que quiera emitir una decision debe respetar el contrato JSON. Hay solo dos tools afectadas hoy (`routing.py`, `tags.py`); si crece, habria que extraer un helper para serializar decisiones.

---

## ADR-011 - El "salvavidas determinista" de Remarketing usa la dispatcher activity, no `execute_tool`

- **Date**: 2026-04-28
- **By**: edgm (architect, fase 4)
- **Context**: el workflow Remarketing tiene un fallback: si el cliente respondio pero el LLM no llamo `transfer_to_sales_agent`, el workflow lo "fuerza" llamando manualmente la tool. Antes lo hacia via `workflow.execute_activity(execute_tool, ExecuteToolInput(name="transfer_to_sales_agent", ...))`. Despues del refactor, la tool ya no hace `start_workflow` -> ese codigo dejaria de funcionar.
- **Decision**: el "salvavidas" construye un `TransferDecision` sintetico directamente en el workflow y lo pasa a `start_or_signal_sales_workflow_activity`. Cero LLM, cero tool roundtrip; la decision es del workflow, no del modelo.
- **Consequences**:
  - Mas determinista (no depende de la tool corriendo correctamente).
  - Bypass del LLM cuando ya sabemos que queremos transferir.
  - El "salvavidas" cambia su shape de history (no llama mas `execute_tool` en este path) -> requiere drain previo al deploy (cubierto por ADR-009).

---

## ADR-005 - Tests obligatorios desde Fase 3

- **Date**: 2026-04-28
- **By**: edgm
- **Context**: Cualquier cambio que toque la **shape de history del workflow** (anadir / quitar activities, cambiar args) puede romper replay de workflows ya en vuelo.
- **Decision**: A partir de **Fase 3**, cada PR del refactor debe incluir:
  - Test unitario en `domain/` (sin mocks).
  - Test de la activity con `ActivityEnvironment`.
  - Test del workflow con `WorkflowEnvironment.start_time_skipping`.
  - **Replay test** contra una history fixture guardada (locks-in determinismo).
- **Consequences**:
  - Fase 1 y 2 pueden ir sin tests (cambios mecanicos, no tocan history).
  - Fases 3-5 anaden ~30% al tiempo de cada PR pero blindan contra regresiones de replay.
  - El equipo de refactor escribe los tests; no se delega.
