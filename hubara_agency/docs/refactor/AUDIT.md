---
title: Auditoria DEHA rectificada - Hubara Agency
last_updated: 2026-04-28
---

# Auditoria DEHA rectificada

Auditoria del monorepo `hubara_agency` (rama `main`) contra las 5 reglas duras DEHA y los anti-patrones conocidos. Cada hallazgo cita `archivo:linea` verificado contra el codigo en `main`.

## Resumen ejecutivo

- **5 hallazgos criticos** (C-1..C-5): violan reglas duras o rompen el arbol de durabilidad. **5/5 RESUELTOS**.
- **8 hallazgos medios** (M-1..M-8): degradan testabilidad / mantenibilidad pero no rompen produccion. **8/8 RESUELTOS**.
- **7 hallazgos menores** (m-1..m-7): higiene, deuda tecnica menor. **7/7 RESUELTOS**.

## Estado final por hallazgo

| ID | Descripcion | Estado | Resuelto en |
|----|-------------|--------|-------------|
| C-1 | Tool abre Temporal Client desde activity | RESUELTO | F4.2 + F4.3 |
| C-2 | Estado mutable a nivel de modulo | RESUELTO | F2.5 (verificado: stateless) |
| C-3 | Heartbeat ad-hoc en activity body | RESUELTO | F2.1 (`@with_heartbeat`) |
| C-4 | Tool importa `temporal_client` (DIP) | RESUELTO | F4.2 (import eliminado) |
| C-5 | Workflow construye `Path` directo | RESUELTO | F1.3 (linea muerta borrada) |
| M-1 | `workflow.timedelta(...)` | RESUELTO | F1.1 |
| M-2 | `getattr(self, '_force_shutdown', False)` | RESUELTO | F1.2 |
| M-3 | `except Exception:` generico | RESUELTO | F1.4 + F2.bonus.2 |
| M-4 | `phone_number_id` default `"TESTING"` | RESUELTO | F1.5 |
| M-5 | Prompts hardcoded en workflow | RESUELTO | F3.1 + F3.2 |
| M-6 | `_run_turn` duplicado en dos workflows | RESUELTO | F5.1-F5.4 |
| M-7 | Imports diferidos sin justificacion | RESUELTO | F2.6 |
| M-8 | `service.py` mezcla 5 responsabilidades | RESUELTO | F3.5 (parser fuera) + F4 (start_workflow fuera de tool) |
| m-1 | Comentarios en spanglish | RESUELTO | F3.4 |
| m-2 | Magic strings repetidos | RESUELTO | F2.4 |
| m-3 | `_CONTINUE_AS_NEW_AFTER_TURNS` muerto en remarketing | RESUELTO | F2.bonus.1 |
| m-4 | Comentarios obsoletos en remarketing | RESUELTO | F3.4 |
| m-5 | `import json` / `import time` no usados | RESUELTO | F2.bonus.3 |
| m-6 | `_load_remarketing_brain` y `_load_shared_brain` casi identicos | RESUELTO | F2.3 (delegan a `load_brain`) |
| m-7 | `ws.path` tratado como `Path` y `str` | RESUELTO | F3.6 (DTO `WorkspaceConfig.path: str` consolidado) |

## Archivos auditados

- `src/core/activities.py`
- `src/core/registries.py`
- `src/core/temporal_client.py`
- `src/core/config.py`
- `src/domains/sales_whatsapp/workflows/sales_session.py`
- `src/domains/sales_whatsapp/service.py`
- `src/domains/sales_whatsapp/tools/routing.py`
- `src/domains/sales_whatsapp/integrations.py`
- `src/domains/remarketing_whatsapp/workflows/remarketing.py`
- `src/domains/remarketing_whatsapp/service.py`
- `worker.py`

---

## Hallazgos criticos

### C-1. Tool abre Temporal Client desde dentro de una activity

- **Regla**: R-DIP + anti-patron "tools-como-sub-workflows".
- **Ubicacion**: `src/domains/sales_whatsapp/tools/routing.py:53,72-93`.
- **Sintoma**: la tool `TransferToSalesAgentTool.execute` invoca `get_temporal_client()`, llama `start_workflow` y `signal` desde dentro de la activity. Esto rompe el arbol de durabilidad: si la activity falla a mitad de `start_workflow + signal`, Temporal **no recupera estado consistente**, ya que el lado del nuevo workflow (Sales) puede haber arrancado pero la signal puede no haber llegado.
- **Consecuencia**: race condition entre el lifecycle de Remarketing y Sales. La tool ademas se vuelve incompatible con replay / fakes / unit tests.
- **Fix planificado**: ADR-001. La tool devuelve un payload de decision (`{"action": "transfer_to_sales", "resumen": ...}`); el workflow leee la decision y ejecuta una activity `start_or_signal_sales_workflow_activity` que es testeable y reintentable. Ver Fase 4 en `PLAN.md`.

### C-2. Estado mutable a nivel de modulo en activity

- **Regla**: R-STATELESS.
- **Ubicacion**: `src/core/registries.py` (cualquier `_REGISTRY = {}` o equivalente cacheado a nivel de modulo en `get_base_tools_registry`).
- **Sintoma**: si `get_base_tools_registry` retiene estado de algun runtime previo, dos activities en el mismo proceso pueden ver registros stale.
- **Consecuencia**: comportamiento no determinista entre invocaciones consecutivas del worker.
- **Fix planificado**: factories puras que reconstruyen el registry en cada llamada. Fase 2.

### C-3. Heartbeat ad-hoc dentro del cuerpo de la activity

- **Regla**: R-HEARTBEAT.
- **Ubicacion**: `src/core/activities.py:40-54` (loop manual `asyncio.create_task(_heartbeat_loop)`).
- **Sintoma**: cada activity larga tiene su propio `while True: activity.heartbeat(); await asyncio.sleep(10)`. Si una excepcion se lanza antes del `try`, el heartbeat task queda colgado.
- **Consecuencia**: codigo duplicado entre activities, riesgo de leak de tasks, dificulta el testing.
- **Fix planificado**: extraer un decorador `@with_heartbeat(every=10)` a `infrastructure/temporal/heartbeat.py` y aplicarlo a las activities relevantes. Fase 2.

### C-4. Tool importa `temporal_client` (rompe DIP)

- **Regla**: R-DIP.
- **Ubicacion**: `src/domains/sales_whatsapp/tools/routing.py:5` (`from src.core.temporal_client import get_temporal_client`).
- **Sintoma**: una tool (que conceptualmente es un adapter de dominio) importa infraestructura de orquestacion. Hace que la tool no se pueda probar sin un servidor Temporal corriendo.
- **Consecuencia**: ciclo conceptual: dominio -> infraestructura -> dominio. Imposibilita el testing aislado.
- **Fix planificado**: Igual que C-1; la decision de "transferir" se mueve al workflow. Fase 4.

### C-5. Workflow lee filesystem directamente (de forma indirecta)

- **Regla**: R-DET.
- **Ubicacion**: `src/domains/remarketing_whatsapp/workflows/remarketing.py:103,106` (uso de `Path(ws.path)` y comentario `metadata_file = Path(ws.path) / "metadata.json"`).
- **Sintoma**: aunque el filesystem se lee a traves de activities (`read_workspace_memory_activity`), **dentro del workflow** se construyen `Path` y se referencia el filesystem como si fuera mutable durante la ejecucion. Hoy es solo deuda conceptual; manana rompe replay si alguien agrega `if path.exists()` directo en el workflow.
- **Consecuencia**: replay frageil si se agrega cualquier check al filesystem al lado del Path.
- **Fix planificado**: dejar todo I/O dentro de activities; el workflow solo recibe strings y dataclasses. Fase 1 borra la linea muerta (F1.3); Fase 3 elimina la idea de `Path` del workflow.

---

## Hallazgos medios

### M-1. `workflow.timedelta(...)` en lugar de `timedelta(...)`

- **Regla**: convencion idiomatica del SDK.
- **Ubicaciones**:
  - `src/domains/sales_whatsapp/workflows/sales_session.py:111`
  - `src/domains/remarketing_whatsapp/workflows/remarketing.py:112,120,141,167,196,204`
- **Sintoma**: 7 ocurrencias de `workflow.timedelta(...)`. El re-export existe en el SDK pero crea ambiguedad sobre que helpers son determinísticos.
- **Fix**: F1.1 (Fase 1).

### M-2. Atributo de instancia leido con `getattr` antes de inicializar

- **Regla**: higiene de Python + R-DET indirecta.
- **Ubicaciones**:
  - `src/domains/sales_whatsapp/workflows/sales_session.py:94,105,115`
  - `src/domains/remarketing_whatsapp/workflows/remarketing.py:182,183,198,200,209`
- **Sintoma**: `self._force_shutdown` se accede con `getattr(self, '_force_shutdown', False)` porque nunca se declara en `__init__`. Dificulta replay (un atributo "fantasma" en la signature del workflow).
- **Fix**: F1.2 (Fase 1).

### M-3. `except Exception:` generico

- **Regla**: higiene general.
- **Ubicaciones**:
  - `src/domains/sales_whatsapp/tools/routing.py:66`
  - `src/domains/sales_whatsapp/service.py:95,112`
  - `src/core/activities.py:95,114`
  - `src/domains/remarketing_whatsapp/workflows/remarketing.py:123`
- **Sintoma**: captura cualquier cosa, incluyendo `KeyboardInterrupt`, `MemoryError`, etc.
- **Fix**: F1.4 reemplaza por excepciones especificas.

### M-4. `phone_number_id` con default `"TESTING"`

- **Regla**: fail-fast.
- **Ubicacion**: `src/core/activities.py:88`.
- **Sintoma**: en produccion sin la env var, se mandan mensajes a un numero falso.
- **Fix**: F1.5 (Fase 1).

### M-5. Logica de negocio dentro del workflow (gigant prompt como string)

- **Regla**: business-logic-en-policy.
- **Ubicaciones**:
  - `src/domains/sales_whatsapp/workflows/sales_session.py:89` (prompt de ghosting hardcoded).
  - `src/domains/remarketing_whatsapp/workflows/remarketing.py:145` (prompt de reactivacion hardcoded).
- **Sintoma**: prompts de 200+ caracteres viven dentro del workflow, mezclados con la maquina de estados.
- **Fix**: Fase 3 los mueve a `domain/policies/prompts.py` como funciones puras `build_ghost_trigger_prompt(...)`.

### M-6. Codigo duplicado: `_run_turn` identico en dos workflows

- **Regla**: SRP / DRY.
- **Ubicaciones**:
  - `src/domains/sales_whatsapp/workflows/sales_session.py:137-215`
  - `src/domains/remarketing_whatsapp/workflows/remarketing.py:215-293`
- **Sintoma**: 80 lineas duplicadas. Cualquier cambio se debe replicar en dos archivos.
- **Fix**: Fase 5 extrae `_run_turn` a un mixin o helper compartido.

### M-7. Mezcla de `import` a nivel de funcion sin justificacion clara

- **Regla**: higiene general.
- **Ubicaciones**:
  - `src/core/activities.py:69` (`import time` dentro de `claim_conversation_routing`)
  - `src/core/activities.py:81-83` (varios imports dentro de `send_whatsapp_message_activity`)
  - `src/domains/sales_whatsapp/tools/routing.py:41,57-59` (varios imports diferidos)
  - `src/domains/sales_whatsapp/service.py:92,109` (imports dentro de funciones)
- **Sintoma**: imports diferidos sin razon (no hay riesgo circular real).
- **Fix**: limpieza de Fase 2 al extraer infraestructura.

### M-8. `service.py` mezcla parsing de webhook + ruteo + start workflow

- **Regla**: SRP.
- **Ubicacion**: `src/domains/sales_whatsapp/service.py:28-138`.
- **Sintoma**: `process_incoming_message` parsea el JSON de Meta, escribe metadata, escribe history file, decide ruta, arranca workflow, manda signal. Cinco responsabilidades en 100 lineas.
- **Fix**: Fase 3 extrae a un use case `RouteIncomingMessage` con 3 sub-pasos claros.

---

## Hallazgos menores

### m-1. Comentarios en spanglish

- Mezcla de espanol/ingles en comentarios y docstrings (`Loop stateful del Remarketing`, `Salvavidas DETERMINISTA`, `# Reusing activity to read a file or we can just read history`).
- **Fix**: limpieza en cada fase.

### m-2. Magic strings repetidos

- `"ventas"`, `"remarketing"`, `"wa_"`, `"queue-sales-agent"` repetidos en 4+ archivos.
- **Fix**: constants en `src/core/constants.py` (Fase 2).

### m-3. `_CONTINUE_AS_NEW_AFTER_TURNS = 50` en remarketing

- **Ubicacion**: `src/domains/remarketing_whatsapp/workflows/remarketing.py:32`.
- **Sintoma**: el workflow de remarketing nunca llega a hacer `continue_as_new` (segun ADR-004 es efimero). La constante es codigo muerto.
- **Fix**: ADR-004; eliminar en Fase 3 si tras revisar el codigo se confirma que `turn_count` no se usa.

### m-4. Comentario obsoleto en `remarketing.py`

- **Ubicacion**: `src/domains/remarketing_whatsapp/workflows/remarketing.py:118-122`.
- **Sintoma**: `# Reusing activity to read a file or we can just read history` y `# Actually, to check history properly without new activity, we can just fetch the sessions file` son notas mentales del autor original.
- **Fix**: limpieza en Fase 3.

### m-5. `import json` y `import time` arriba en `remarketing.py:19,20`

- Imports a nivel de modulo que no se usan en el cuerpo principal del archivo (solo en helpers). Detectar con flake8.
- **Fix**: Fase 2.

### m-6. `_load_remarketing_brain` y `_load_shared_brain` casi identicos

- **Ubicaciones**:
  - `src/domains/remarketing_whatsapp/workflows/remarketing.py:56-65`
  - `src/domains/sales_whatsapp/service.py:17-26`
- **Sintoma**: misma funcion duplicada con paths distintos.
- **Fix**: Fase 2 extrae a `src/core/brains.py` con parametro de directorio.

### m-7. `ws.path` se trata como `Path` y como `str` indistintamente

- **Sintoma**: `Path(ws.path)` aparece 5 veces, `str(ws.path)` 1 vez. La fuente de verdad de `WorkspaceConfig.path` no esta clara.
- **Fix**: Fase 2 al definir el contrato del DTO.

---

## Lo bien hecho

- **`SessionInput`, `BuildPromptInput`, `LLMChatInput`, `RecordTurnInput`, `ExecuteToolInput`** son dataclasses planos JSON-serializables. Cumplen R-JSON correctamente.
- **El uso de `temporalio.common.RetryPolicy`** esta bien parametrizado por tipo de activity (`_LLM_OPTIONS`, `_TOOL_OPTIONS`, `_CONV_OPTIONS`).
- **El `@workflow.signal` y `@workflow.query`** estan declarados correctamente y son consistentes en ambos workflows.
- **`record_turn` y `build_prompt`** son activities centralizadas en `exoclaw_temporal`, no duplicadas.
- **El override `execute_tool`** sigue el patron documentado en exoclaw-temporal de "registry hibrido".
- **`process_incoming_message`** captura `(KeyError, IndexError)` especificamente para parsing del payload de Meta. Buen ejemplo de excepcion focalizada.

---

## Conclusion

El proyecto tiene una base solida (DTOs correctos, activities desacopladas) pero arrastra deuda en tres ejes:

1. **Determinismo blando**: `getattr` fantasma, `workflow.timedelta`, comentarios de Path en workflow.
2. **Tools-como-sub-workflows** (C-1, C-4): el anti-patron mas grave, requiere Fase 4 dedicada.
3. **Duplicacion**: `_run_turn`, brain loaders, magic strings.

El plan en `PLAN.md` aborda estos en 5 fases incrementales, cada una con fixes pequenos y testeables.

---

## Hallazgos post-refactor (descubiertos durante R7 - revision integral 2026-04-28)

Estos hallazgos no estaban en el AUDIT original. Se documentan aqui para fix en una iteracion posterior.

### N-1. `build_workspace_config` y `get_base_tools_registry` ejecutan I/O dentro del workflow Remarketing (R-DET blando)

- **Regla**: R-DET (workflow no debe hacer I/O).
- **Ubicacion**: `src/domains/remarketing_whatsapp/workflows/remarketing.py:73-75` (dentro de `@workflow.run`).
- **Sintoma**: `build_workspace_config(session_id)` llama `Path.mkdir(parents=True, exist_ok=True)` y `get_base_tools_registry(Path(...))` instancia tools que registran rutas de filesystem. Aunque las tools en si no hacen I/O al construirse, `mkdir` si lo hace. Esto se ejecuta dentro del workflow.
- **Consecuencia**: replay no es seguro si el filesystem cambia entre invocaciones. Hoy es deuda conceptual; manana rompe replay.
- **Fix propuesto**: mover el setup de workspace y registry a una activity `bootstrap_session_activity(session_id) -> SessionInput`. El workflow recibe el `SessionInput` ya armado en `input` (de hecho Sales ya lo recibe asi: el workflow Sales no construye su propio `SessionInput`, lo recibe). Remarketing deberia hacer lo mismo: el caller (`schedule_remarketing_workflow_activity`) construye el `SessionInput` y lo pasa via DTO.
- **Severidad**: Medio. R-DET blando, no rompe hoy pero es la trampa clasica.
- **Estado**: DIFERIDO a iteracion futura (no rompe nada en produccion ahora; el codigo ya estaba asi antes del refactor; arreglar requiere cambiar la signature del workflow Remarketing -> shape de history -> drain previo). Se podria abordar como F6.1.

### N-2. Replay tests faltantes (deuda obligatoria por ADR-005)

- **Regla**: ADR-005 (tests obligatorios desde Fase 3, incluyendo replay).
- **Ubicacion**: `tests/`.
- **Sintoma**: tenemos tests de smoke, parsers, prompts, imports, contracts, transfer-tool, dispatcher, run-agent-turn (8 archivos). **No hay** ni un solo replay test contra una history fixture. Las Fases 3, 4 y 5 cambiaron shape de history (nuevas activities, signature de `RemarketingSessionWorkflow.run`, helper `run_agent_turn`).
- **Consecuencia**: workflows en vuelo al deploy lanzaran `NonDeterminismError`. Mitigacion documentada en ADR-009 (drain previo al deploy). Pero no hay test que **bloquee** una regresion futura del shape.
- **Fix propuesto**: capturar history JSON de un workflow real corriendo en preprod (`temporal workflow show --workflow-id X --output json`), guardarla en `tests/fixtures/history_*.json`, escribir un test que use `Replayer.replay_workflow(...)` apuntado a ese JSON. Bloquear merge si el test falla.
- **Severidad**: Alto. Es la unica pieza de la cadena CI/CD que valida determinismo.
- **Estado**: DIFERIDO. Tarea para la siguiente iteracion (F6.2). El refactor se hizo con drain operativo (ADR-009) como mitigacion.

### N-3. `_load_remarketing_brain()` se sigue llamando dentro del workflow.run (R-DET blando)

- **Regla**: R-DET.
- **Ubicacion**: `src/domains/remarketing_whatsapp/workflows/remarketing.py:116, 144`.
- **Sintoma**: `_load_remarketing_brain()` es un wrapper sobre `load_brain(REMARKETING_BRAIN_DIR)` que lee filesystem. Se invoca dentro del workflow `run()`. ADR-006 lo dejo conscientemente como deuda (porque cambiar el call site rompe replay), pero la conclusion logica es: la lectura del brain debe ser una activity.
- **Consecuencia**: idem N-1. Hoy funciona porque los archivos del brain no cambian en runtime; replay falla si el contenido cambia entre primera ejecucion y replay.
- **Fix propuesto**: activity `load_remarketing_brain_activity() -> list[str]` y reemplazar las dos llamadas. Cambia history shape -> drain previo al deploy.
- **Severidad**: Medio.
- **Estado**: DIFERIDO. Documentado en ADR-006 como deuda consciente. Tarea F6.3.

### N-4. `WorkflowExecutionStatus` import en dispatcher (informativo)

- **Ubicacion**: `src/core/infrastructure/temporal/dispatcher_activities.py:11`.
- **Sintoma**: el dispatcher importa `WorkflowExecutionStatus` desde `temporalio.client` para verificar `desc.status != WorkflowExecutionStatus.RUNNING`. Esto es correcto (corre dentro de activity, no workflow), pero la cadena de retry de la activity puede iterar varias veces si el status flapea.
- **Severidad**: Menor. Solo nota, no requiere fix inmediato.
- **Estado**: VERIFICADO OK.

### N-5. Tests no han sido ejecutados en este entorno (carencia de validacion runtime)

- **Sintoma**: ningun comando `pytest tests/` se ejecuto en esta sesion ni en las anteriores. Los tests son sintacticamente validos por inspeccion, pero el runtime no fue validado.
- **Recomendacion**: ejecutar manualmente `cd hubara_agency && pytest tests/ -v` antes del merge.
- **Estado**: PENDIENTE de validacion manual antes de deploy.
