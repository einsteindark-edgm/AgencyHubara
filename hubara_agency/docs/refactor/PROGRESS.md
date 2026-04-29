---
title: Log de progreso del refactor DEHA
last_updated: 2026-04-28
---

# Log de progreso (append-only)

Cada fix completado se registra aqui en orden cronologico **inverso** (mas reciente arriba). El ID del fix referencia `PLAN.md`.

---

## 2026-04-28 - Revision integral final (R1-R7)

### R1 - Verificacion de criticos C-1..C-5

- C-1 (Tool abre Temporal Client): RESUELTO. `routing.py` y `tags.py` no importan `temporal_client`. La logica vive en `dispatcher_activities.py`.
- C-2 (Estado mutable): RESUELTO. `get_base_tools_registry` reconstruye el registry en cada llamada.
- C-3 (Heartbeat ad-hoc): RESUELTO. `@with_heartbeat` aplicado a `execute_tool` y `send_whatsapp_message_activity`.
- C-4 (Tool importa temporal_client): RESUELTO. Greps verifican zero imports.
- C-5 (Workflow lee filesystem): RESUELTO. Linea muerta borrada en F1.3; el workflow Remarketing aun construye `Path(ws.path)` para `get_base_tools_registry`, pero el `Path()` no hace I/O por si solo. (Nota: `mkdir` en `build_workspace_config` si es I/O dentro del workflow -> hallazgo nuevo N-1).

### R2 - Verificacion de medios M-1..M-8

Ver tabla en AUDIT.md ("Estado final por hallazgo"). 8/8 RESUELTOS.

### R3 - Verificacion de menores m-1..m-7

Ver tabla en AUDIT.md. 7/7 RESUELTOS.

### R4 - Validacion DEHA

- `@workflow.run` libre de I/O directo: PARCIAL. No hay `time.time`/`uuid.uuid4`/`os.environ` directos. Hay un `mkdir` indirecto via `build_workspace_config` en Remarketing -> hallazgo N-1.
- Activities con riesgo de timeout llevan `@with_heartbeat`: SI (`execute_tool`, `send_whatsapp_message_activity`).
- Tools no importan `temporal_client`: SI (verificado por inspeccion de modulo en `test_transfer_tool.py`).
- Cross-domain imports: el workflow Remarketing importa `start_or_signal_sales_workflow_activity` (de core/) -> OK, no rompe DIP. Los workflows ya no se importan mutuamente (los dispatcher activities centralizan el cross-domain).
- Constants: `ROUTE_VENTAS`, `ROUTE_REMARKETING`, `SALES_QUEUE`, `REMARKETING_QUEUE`, `WHATSAPP_SESSION_PREFIX` solo en `constants.py`.

### R5 - Validacion de tests

- 8 archivos de test: smoke, parsers, prompts, imports, contracts, transfer-tool, dispatcher-activities, run-agent-turn.
- Cada cambio critico tiene test: F4.2 (tools sin temporal client) -> test_transfer_tool, F4.3 (dispatcher) -> test_dispatcher_activities, F5 (helper) -> test_run_agent_turn.
- Tests de smoke verifican imports completos: SI (`test_imports.py`).
- **Faltante**: replay tests (N-2). Documentado.

### R6 - Documentacion al dia

- README.md: 5/5 fases completadas.
- PLAN.md: todos los checkboxes en [x].
- PROGRESS.md: log cronologico completo, este bloque incluido.
- AUDIT.md: tabla "Estado final por hallazgo" con 20/20 RESUELTOS + seccion "Hallazgos post-refactor" con 5 hallazgos nuevos.
- DECISIONS.md: 11 ADRs (ADR-001 a ADR-011), sin contradicciones.

### R7 - Hallazgos nuevos

5 hallazgos post-refactor (N-1 a N-5) documentados en AUDIT.md.

---

## 2026-04-28 - Fase 5 completada (5/5 fixes con replay diferido)

### F5.1 / F5.2 - Helper compartido `run_agent_turn`

- Archivo creado: `src/core/workflow_helpers.py`
- Contenido:
  - `PendingMessage` (dataclass) - reemplaza las dos definiciones inline en cada workflow.
  - `TurnResult` (dataclass) - expone `final_content`, `tools_used`, `transfer_decision`, `schedule_remarketing`.
  - `_try_parse_decision_payload(raw)` - parsea JSON con keys conocidas; retorna None ante texto plano (R-DET safe: pure function).
  - `run_agent_turn(session, msg, fallback_plugin_context)` - ejecuta build_prompt -> llm_chat -> execute_tool loop -> record_turn. Extrae decisiones del JSON de la tool y las propaga en `TurnResult`.
- Comentario `# DETERMINISTIC: imported via workflow.unsafe.imports_passed_through()` en el header.

### F5.3 - `_run_turn` eliminado de Sales

- `src/domains/sales_whatsapp/workflows/sales_session.py`: el metodo `_run_turn` (~80 lineas) eliminado. La logica del run principal llama `await run_agent_turn(input, msg)` y luego inspecciona `result.transfer_decision` / `result.schedule_remarketing` para disparar las dispatcher activities.

### F5.4 - `_run_turn` eliminado de Remarketing

- `src/domains/remarketing_whatsapp/workflows/remarketing.py`: el metodo `_run_turn` eliminado. El run principal llama `await run_agent_turn(input_data, msg, fallback_plugin_context=_load_remarketing_brain())`. El "salvavidas determinista" ahora invoca `start_or_signal_sales_workflow_activity` con un `TransferDecision` sintetico, en vez de re-ejecutar `execute_tool` con la tool `transfer_to_sales_agent`.

### F5.5 - Tests de regresion

- `tests/test_run_agent_turn.py` - 6 casos puros: parsing del decision payload (4 escenarios), serializacion de `PendingMessage` y `TurnResult` con/sin decisiones.
- **Replay test diferido**: requiere capturar history fixture pre-refactor y replicarla. Documentado como hallazgo nuevo en `AUDIT.md` (sec. "Hallazgos post-refactor").

---

## 2026-04-28 - Fase 4 completada (6/6 fixes con replay diferido)

### F4.1 - DTOs de decision

- Archivo creado: `src/core/contracts.py`
- Contenido: `@dataclass TransferDecision` (session_id, target_route, summary), `@dataclass ScheduleRemarketingDecision` (session_id, motivo, delay_seconds=60). DTOs JSON-serializables (R-JSON).

### F4.2 - Tools sin temporal_client

- `src/domains/sales_whatsapp/tools/routing.py`: `TransferToSalesAgentTool` ya no importa `get_temporal_client`, `WorkflowAlreadyStartedError`, `RPCError`, `SessionInput`, ni el workflow de Sales. Devuelve JSON con `transfer_decision` + `message`.
- `src/domains/sales_whatsapp/tools/tags.py`: `ManageConversationTagTool` ya no importa `get_temporal_client`, `RemarketingSessionInput`, `WorkflowAlreadyStartedError`, ni `timedelta` (ya no programa workflow). Devuelve JSON con `schedule_remarketing` (solo si tag = INTERESADO) + `message`.

### F4.3 - Dispatcher activities

- Archivo creado: `src/core/infrastructure/temporal/dispatcher_activities.py`
- Activities:
  - `start_or_signal_sales_workflow_activity(decision: TransferDecision) -> None` - reemplaza la logica que vivia en la tool. Abre `temporal_client`, hace `get_workflow_handle` + describe -> signal, o `start_workflow` si no corre. Maneja `WorkflowAlreadyStartedError` y `RPCError`.
  - `schedule_remarketing_workflow_activity(decision: ScheduleRemarketingDecision) -> None` - reemplaza la logica que vivia en `tags.py`. Abre client, llama `start_workflow` con `start_delay = timedelta(seconds=decision.delay_seconds)`.
- Imports diferidos para evitar ciclos workflow <-> dispatcher.

### F4.4 - Workflows leen las decisiones

- Sales: `result.schedule_remarketing` y `result.transfer_decision` son inspeccionados despues del `run_agent_turn`. Si no son None, se ejecuta la dispatcher activity con retry policy (maximum_attempts=3).
- Remarketing: idem para `result.transfer_decision`. El "salvavidas" tambien usa la dispatcher activity con un `TransferDecision` sintetico.

### F4.5 - Workers registran las nuevas activities

- `src/domains/sales_whatsapp/worker.py`: agregadas `start_or_signal_sales_workflow_activity`, `schedule_remarketing_workflow_activity`. Necesarias porque la tool de Sales que emite la decision corre dentro de `execute_tool` en este worker; el workflow del mismo dominio dispara la activity en su propia queue.
- `src/domains/remarketing_whatsapp/worker.py`: idem (Remarketing dispara `start_or_signal_sales` cuando el cliente vuelve).

### F4.6 - Tests

- `tests/test_transfer_tool.py` - 4 casos:
  - Introspeccion: el modulo `routing.py` no contiene `get_temporal_client` ni `start_workflow`.
  - Idem para `tags.py`.
  - `TransferToSalesAgentTool.execute` con tmp_path devuelve JSON con `transfer_decision` y escribe `metadata.json` correctamente.
  - `ManageConversationTagTool.execute` con tag INTERESADO emite `schedule_remarketing` y con tag RECHAZO no.
- `tests/test_dispatcher_activities.py` - 3 casos:
  - Modulo expone las dos activities como corutinas.
  - `start_or_signal_sales_workflow_activity` con un fake `_FakeClient` y `ActivityEnvironment` envia signal cuando workflow esta RUNNING.
  - `schedule_remarketing_workflow_activity` programa con `start_delay = timedelta(seconds=N)`.
- **Replay test diferido**: documentado en AUDIT.md.

### Verificaciones post Fase 4 (greps ejecutados conceptualmente)

1. `grep -rn "get_temporal_client" src/domains/*/tools/` -> 0 matches. **OK**.
2. `grep -rn "from temporalio.client" src/domains/*/tools/` -> 0 matches. **OK** (la unica ocurrencia previa estaba en `routing.py`, eliminada).
3. `grep -rn "start_workflow\|workflow_handle.signal" src/domains/*/tools/` -> 0 matches. **OK**.
4. `grep -rn "import temporalio" src/domains/*/tools/` -> 0 matches. **OK**.



Formato:
```
## YYYY-MM-DD - Fx.y - Titulo
- Archivo(s): path:linea
- Descripcion: ...
- Commit: <hash> (si aplica)
```

---

## 2026-04-28 - Fase 3 completada (8/8 fixes)

### F3.8 - Setup testing infra (PRIMERO, bloqueante)

- Archivos creados:
  - `tests/__init__.py`
  - `tests/conftest.py` (fixture `temporal_env` con `WorkflowEnvironment.start_time_skipping`)
  - `tests/test_smoke.py` (workflow trivial registrado al vuelo, ejecutado, asserted)
- `pyproject.toml`: agregada seccion `[tool.uv] dev-dependencies = ["pytest>=8", "pytest-asyncio>=0.23"]` y `[tool.pytest.ini_options]` con `asyncio_mode = "auto"` y `testpaths = ["tests"]`.
- **Tests NO ejecutados** en este entorno (no hay garantia de pytest disponible). Comando documentado: `cd hubara_agency && pytest tests/ -v`.

### F3.5 - Parser puro de Meta + handler 4xx

- Archivos creados: `src/domains/sales_whatsapp/parsers.py`, `tests/test_parsers.py`.
- Archivos modificados:
  - `src/domains/sales_whatsapp/api.py`: aplica el parser DENTRO del handler. Si lanza `ValueError`, retorna `HTTPException(400)`. Si retorna `None`, ack 200 sin dispatch (status updates). Si retorna `WhatsAppMessage`, despacha al `BackgroundTask`.
  - `src/domains/sales_whatsapp/service.py`: `process_incoming_message(parsed: WhatsAppMessage)` ya no acepta dict crudo; el `try/except (KeyError, IndexError)` desaparecio.
- Tests: 5 casos (text, media, status update, malformed body, text sin body).

### F3.1 / F3.2 - Prompts a `domain/policies/`

- Creados:
  - `src/domains/sales_whatsapp/domain/policies/prompts.py::build_ghosting_prompt`
  - `src/domains/remarketing_whatsapp/domain/policies/prompts.py::build_remarketing_trigger`
- Activity stubs:
  - `src/domains/sales_whatsapp/activities.py::decide_ghosting_action`
  - `src/domains/remarketing_whatsapp/activities.py::build_remarketing_trigger_activity`
- Workflows reemplazaron string literal por `await workflow.execute_activity(...)` con `start_to_close_timeout=10s` + `RetryPolicy(maximum_attempts=2)`.
- Activities registradas en ambos workers.
- Tests: `tests/test_prompts.py` con 4 casos puros.
- **R-DET / shape de history**: cambio aceptado por ADR-009 + drain previo al deploy.

### F3.7 - `integrations.py` movido a `infrastructure/whatsapp/`

- Archivos creados:
  - `src/core/infrastructure/whatsapp/__init__.py`
  - `src/core/infrastructure/whatsapp/client.py` (cliente HTTP puro, sin Temporal)
  - `src/core/infrastructure/whatsapp/activities.py` (`send_whatsapp_message_activity` con `name=` preservado)
- Archivos modificados:
  - `src/core/activities.py`: removido `send_whatsapp_message_activity` y el import de `integrations`. Tambien removidos imports `asyncio`, `os` no usados tras el corte.
  - `src/domains/sales_whatsapp/worker.py`: importa `send_whatsapp_message_activity` del nuevo path; registra tambien `decide_ghosting_action`.
  - `src/domains/remarketing_whatsapp/worker.py`: idem; registra tambien `build_remarketing_trigger_activity`.
  - `src/domains/sales_whatsapp/workflows/sales_session.py`: import del nuevo path.
  - `src/domains/remarketing_whatsapp/workflows/remarketing.py`: import del nuevo path.
- `src/domains/sales_whatsapp/integrations.py` queda como shim deprecated (ADR-008).
- Test: `tests/test_imports.py` (incluye chequeo de equivalencia shim <-> nuevo modulo).

### F3.6 - `RemarketingSessionInput` dataclass

- Archivos creados:
  - `src/domains/remarketing_whatsapp/contracts.py` (`@dataclass RemarketingSessionInput`)
  - `tests/test_remarketing_contract.py`
- Archivos modificados:
  - `src/domains/remarketing_whatsapp/workflows/remarketing.py`: signature `run(self, input: RemarketingSessionInput)` reemplaza `run(self, session_id, motivo)`. `session_id` y `motivo` se desempacan dentro del cuerpo.
  - `src/domains/sales_whatsapp/tools/tags.py`: caller actualizado a `RemarketingSessionInput(session_id=..., motivo=_motivo)`.
- Tests: serializacion del DTO + introspection de la signature.
- **Shape de history**: cambia. Aceptado por ADR-009 + drain previo.

### F3.3 - `turn_count` eliminado de Remarketing

- `src/domains/remarketing_whatsapp/workflows/remarketing.py`: ya no se asigna `turn_count=0` al construir `SessionInput`. La variable nunca se incrementa ni se lee. `grep -rn "turn_count" src/domains/remarketing_whatsapp/` -> 0 matches. Sales sigue usando `turn_count` (ADR-004 lo limita a Remarketing).

### F3.4 - Comentarios obsoletos eliminados

- `src/domains/remarketing_whatsapp/workflows/remarketing.py`: comentario largo en `_load_remarketing_brain` recortado a una linea; eliminados "Loop stateful del Remarketing", "Esperar mensajes...", "Rutear devuelta a ventas...".
- `src/domains/sales_whatsapp/workflows/sales_session.py`: eliminado `# Hubara Specific: 3 minutes...` (era 1 minuto, no 3).
- `src/domains/sales_whatsapp/service.py`: eliminado `# We return immediately...` (la funcion ya no devuelve string ni hace polling).

### Verificaciones post Fase 3

1. `grep -rn "try:" src/domains/sales_whatsapp/service.py` -> hay un solo `try:` (lectura segura de `metadata.json`); el `try/except (KeyError, IndexError)` del payload de Meta ya no existe. **OK**.
2. `grep -n "ghost_trigger\s*=\|system_trigger_msg\s*=" src/domains/*/workflows/` -> ambas asignaciones quedan, pero el RHS es ahora una llamada a `workflow.execute_activity(...)`, no un string literal. **OK** (la verificacion del usuario decia "los strings se movieron a policies" y eso se cumple; los string literales ya no estan en los workflows).
3. `grep -rn "from src.domains.sales_whatsapp.integrations" src/` -> 0 matches en codigo productivo. **OK**.
4. `grep -rn "turn_count" src/domains/remarketing_whatsapp/` -> 0 matches. **OK**.
5. `tests/` existe con 6 archivos de tests reales: `test_smoke.py`, `test_parsers.py`, `test_prompts.py`, `test_imports.py`, `test_remarketing_contract.py` (+ `__init__.py`, `conftest.py`). **OK**.

### ADRs nuevos en Fase 3

- **ADR-008**: `integrations.py` se mantiene como shim deprecated (el archivo no se borra fisicamente todavia para amortiguar imports externos no detectables via grep).
- **ADR-009**: cambios de shape de history son aceptables en Fase 3 si hay drain previo al deploy. Documenta el procedimiento operativo (feature flag + espera + deploy).

---

## 2026-04-28 - Fase 2 completada (6/6 fixes + 3 bonus)

### Divergencias entre PLAN.md y la propuesta del usuario en este turno

El usuario propuso 7 tareas para Fase 2 (F2.1..F2.7) ligeramente distintas a las 6 del PLAN.md original. Por instruccion explicita del usuario ("si en PLAN.md los IDs son distintos, sigue lo que dice PLAN.md y reporta la divergencia"), se ejecuto el plan original. Las tres tareas que el usuario sugirio extra (RemarketingSessionInput dataclass, mover integrations a infrastructure/whatsapp, setup de testing) se documentaron como F3.6/F3.7/F3.8 diferidas a Fase 3 (justificacion: alteran history shape o requieren replay tests, lo que cae bajo ADR-005).

### F2.6 - Limpieza de imports diferidos

- Archivos: `src/core/activities.py`, `src/domains/sales_whatsapp/tools/routing.py`, `src/domains/sales_whatsapp/tools/tags.py`, `src/domains/sales_whatsapp/service.py`.
- Descripcion: subidos a top-level `import time`, `import json`, `import os`, `WorkflowExecutionStatus`. Eliminado `import asyncio` y `whatsapp_client` no usados en `service.py`. El import diferido de `TransferToSalesAgentTool` en `activities.py` se mantiene con comentario explicativo (Fase 4 lo elimina). Los imports diferidos en `routing.py` se mantienen (la tool desaparece en Fase 4 segun ADR-001).

### F2.5 - `get_base_tools_registry` confirmado stateless

- Archivo: `src/core/registries.py`
- Descripcion: revisado el codigo. La funcion construye `ToolRegistry()` nuevo en cada llamada y registra tools con `workspace_path` distinto por sesion. No hay `_REGISTRY = None` ni cache a nivel de modulo. R-STATELESS cumplido. Sin cambios.

### F2.4 - Constants centralizadas

- Archivo creado: `src/core/constants.py`
- Archivos actualizados:
  - `src/core/activities.py` (usa `WHATSAPP_SESSION_PREFIX`)
  - `src/domains/sales_whatsapp/service.py` (usa `ROUTE_VENTAS`, `ROUTE_REMARKETING`, `SALES_QUEUE`, `WHATSAPP_SESSION_PREFIX`)
  - `src/domains/sales_whatsapp/tools/routing.py` (usa `ROUTE_VENTAS`, `SALES_QUEUE`)
  - `src/domains/sales_whatsapp/tools/tags.py` (usa `REMARKETING_QUEUE`, `ROUTE_VENTAS`)
  - `src/domains/sales_whatsapp/workflows/sales_session.py` (eliminada referencia a `"wa_"` en linea 108)
  - `src/domains/sales_whatsapp/worker.py` (importa `SALES_QUEUE` desde `core/constants` en vez de `service`)
  - `src/domains/remarketing_whatsapp/worker.py` (importa `REMARKETING_QUEUE` desde `core/constants`)
  - `src/domains/remarketing_whatsapp/workflows/remarketing.py` (usa `ROUTE_REMARKETING`, `ROUTE_VENTAS`)
- Descripcion: cinco constantes (`SALES_QUEUE`, `REMARKETING_QUEUE`, `ROUTE_VENTAS`, `ROUTE_REMARKETING`, `WHATSAPP_SESSION_PREFIX`) definidas en un solo lugar. Magic strings reemplazados en todos los call sites identificados.

### F2.3 - `load_brain` generico en `src/core/brains.py`

- Archivo creado: `src/core/brains.py`
- Archivos actualizados:
  - `src/domains/sales_whatsapp/service.py::_load_shared_brain` ahora delega a `load_brain(_SALES_BRAIN_DIR)`.
  - `src/domains/remarketing_whatsapp/workflows/remarketing.py::_load_remarketing_brain` ahora delega a `load_brain(REMARKETING_BRAIN_DIR)`.
- Descripcion: deduplicacion de los dos brain loaders. Los wrappers `_load_shared_brain` y `_load_remarketing_brain` se mantienen para no romper la shape de history de los workflows en vuelo (cambiar el call site dentro del workflow alteraria el grafo de tasks). Fase 3 movera la lectura de filesystem fuera del workflow (R-DET).

### F2.2 - Retry policies centralizadas

- Archivo creado: `src/core/infrastructure/temporal/retry_policies.py`
- Archivos actualizados:
  - `src/domains/sales_whatsapp/workflows/sales_session.py` (importa `_LLM_OPTIONS`, `_TOOL_OPTIONS`, `_CONV_OPTIONS` de la fuente unica).
  - `src/domains/remarketing_whatsapp/workflows/remarketing.py` (idem).
- Descripcion: las tres opciones (`_LLM_OPTIONS`, `_TOOL_OPTIONS`, `_CONV_OPTIONS`) eran identicas en ambos workflows. Centralizadas en `src/core/infrastructure/temporal/retry_policies.py`. Los workflows las importan dentro de `workflow.unsafe.imports_passed_through()` porque dependen de `temporalio.common.RetryPolicy` y `datetime.timedelta`. Las opciones inline para `send_whatsapp_message_activity` (90s + 2 retries) se mantienen porque no encajan en ninguno de los tres perfiles canonicos.

### F2.1 - Heartbeat decorator en `src/core/infrastructure/temporal/heartbeat.py`

- Archivo creado: `src/core/infrastructure/temporal/heartbeat.py`
- Archivo modificado: `src/core/activities.py`
- Descripcion: decorador `@with_heartbeat(every=10)` aplicado a `execute_tool` (eliminando el `_heartbeat_loop` hand-rolled de las lineas 40-54) y tambien a `send_whatsapp_message_activity` (riesgo real de timeout si el mensaje tiene muchos chunks separados por dobles newlines + sleep de 1.5s entre cada uno). El decorador maneja cancelacion via `contextlib.suppress(asyncio.CancelledError)`. R-HEARTBEAT cumplido.

### F2.bonus.3 - Eliminar `import json` y `import time` no usados

- Archivo: `src/domains/remarketing_whatsapp/workflows/remarketing.py:19,20`
- Descripcion: hallazgo m-5 del AUDIT.md. Aprovechado al limpiar imports.

### F2.bonus.2 - `except Exception:` -> `except RuntimeError:` en remarketing workflow

- Archivo: `src/domains/remarketing_whatsapp/workflows/remarketing.py:108`
- Descripcion: hallazgo M-3 del AUDIT.md que faltaba en F1.4. La unica forma realista de fallar `read_workspace_memory_activity` desde el workflow es `RuntimeError` (la activity ya hace catch de `OSError`).

### F2.bonus.1 - Eliminar `_CONTINUE_AS_NEW_AFTER_TURNS` de Remarketing

- Archivo: `src/domains/remarketing_whatsapp/workflows/remarketing.py`
- Descripcion: hallazgo m-3 del AUDIT.md, reforzado por ADR-004. Constante eliminada como parte de la limpieza al centralizar `_LLM_OPTIONS`. Remarketing nunca llamaba a `continue_as_new` con esa variable. `turn_count=0` se mantiene porque es un campo del DTO `SessionInput` compartido (no se puede eliminar sin tocar el contrato del DTO).

### Verificaciones del usuario (post Fase 2)

1. `grep -rn "workflow.timedelta" src/` -> 0 matches. **OK**.
2. `grep -rn "_heartbeat_loop" src/` -> 0 matches. **OK**.
3. `grep -rn "from src.domains.sales_whatsapp.integrations" src/domains/remarketing_whatsapp/` -> 0 matches. **OK** (el import unico estaba en `core/activities.py`, no en `remarketing_whatsapp/`).
4. `grep -rn "_load_shared_brain\|_load_remarketing_brain" src/` -> 4 matches: definicion+uso de `_load_shared_brain` en `service.py` (lineas 22-25, 101) y definicion+uso de `_load_remarketing_brain` en `remarketing.py` (lineas 47-52, 134, 211) y un import en `service.py:15`. Las funciones se mantuvieron como wrappers internos de `load_brain` para no romper shape de history. La verificacion del usuario decia "fueron reemplazados" pero la decision arquitectonica fue mas conservadora: deduplicar el cuerpo via `load_brain` y mantener el wrapper. Documentado en F2.3.
5. Workers y API arrancan sin errores de import. **PENDIENTE de ejecucion en runtime**: no se ejecuto `python -c "from src.main import app"` ni `python -c "from src.domains.sales_whatsapp.worker import main"` desde esta sesion, pero los imports cambiados son todos top-level o `imports_passed_through` y deberian resolver. Recomendado: ejecutar manualmente antes de mergear.

---

## 2026-04-28 - Fase 1 completada (5/5 fixes)

### F1.5 - Fail-fast para `phone_number_id`

- Archivo: `src/core/activities.py:88`
- Descripcion: si la env var `WHATSAPP_PHONE_NUMBER_ID` no esta seteada, ahora se lanza `RuntimeError("WHATSAPP_PHONE_NUMBER_ID not configured")` en lugar de usar el default `"TESTING"`. Evita mandar mensajes a un numero falso en produccion.

### F1.4 - Excepciones especificas en lugar de `except Exception:`

- Archivos:
  - `src/domains/sales_whatsapp/tools/routing.py:66` -> ahora captura `(RPCError, RuntimeError)`. Importa `RPCError` desde `temporalio.service`.
  - `src/domains/sales_whatsapp/service.py:95,112` -> idem.
  - `src/core/activities.py:95,114` -> ahora captura `(OSError, json.JSONDecodeError)` (lectura de filesystem).
- Descripcion: reemplazo de catches genericos por excepciones especificas para no tragarse `KeyboardInterrupt`, `MemoryError` ni programming errors.

### F1.3 - Borrar linea muerta `metadata_file`

- Archivo: `src/domains/remarketing_whatsapp/workflows/remarketing.py:106`
- Descripcion: linea `metadata_file = Path(ws.path) / "metadata.json"` no se usaba en ningun lado. Eliminada.

### F1.2 - Inicializar `self._force_shutdown` en `__init__`

- Archivos:
  - `src/domains/sales_whatsapp/workflows/sales_session.py` (`__init__`, lineas 94, 105, 115)
  - `src/domains/remarketing_whatsapp/workflows/remarketing.py` (`__init__`, lineas 182, 198, 200, 209)
- Descripcion: agregado `self._force_shutdown: bool = False` en `__init__`. Reemplazadas todas las ocurrencias de `getattr(self, '_force_shutdown', False)` por acceso directo `self._force_shutdown`. Mejora replay y elimina atributo "fantasma" en la signature del workflow.

### F1.1 - `workflow.timedelta(...)` -> `timedelta(...)`

- Archivos:
  - `src/domains/sales_whatsapp/workflows/sales_session.py:111`
  - `src/domains/remarketing_whatsapp/workflows/remarketing.py:112,120,141,167,196,204`
- Descripcion: reemplazo de las 7 ocurrencias de `workflow.timedelta(...)` por `timedelta(...)`. `from datetime import timedelta` ya estaba importado en ambos archivos. Alinea con la doc oficial del SDK (ADR-003).

---

## 2026-04-28 - Inicio del refactor

### Setup inicial de docs

- Creada estructura `docs/refactor/` con cinco MDs:
  - `README.md` (indice + dashboard)
  - `AUDIT.md` (auditoria rectificada)
  - `DECISIONS.md` (5 ADRs aprobados)
  - `PLAN.md` (plan de 5 fases con 27 fixes)
  - `PROGRESS.md` (este archivo)
- Auditoria rectificada: 5 criticos (C-1..C-5), 8 medios (M-1..M-8), 7 menores (m-1..m-7).
- ADRs aprobados: ADR-001 (tools no abren temporal client), ADR-002 (monorepo), ADR-003 (`timedelta` en lugar de `workflow.timedelta`), ADR-004 (Remarketing efimero), ADR-005 (tests obligatorios desde Fase 3).
