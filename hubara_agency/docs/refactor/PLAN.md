---
title: Plan de refactor en 5 fases - Hubara Agency
last_updated: 2026-04-28
---

# Plan de refactor en 5 fases

Cada fase se planifica con: objetivo, tareas con paths concretos, "Done si...", riesgos y mitigacion. Los fixes tienen ID `Fx.y` que aparece tambien en `PROGRESS.md`.

**Convencion de checkboxes**:
- `[ ]` = pendiente
- `[x]` = completado

---

## Fase 1 - Estabilizar (bajo riesgo, sin arquitectura)

**Objetivo**: limpiar deuda mecanica que no toca la shape de history del workflow ni las fronteras arquitectonicas. Cero cambios funcionales.

**Tareas**:

- [x] **F1.1** - Reemplazar `workflow.timedelta(...)` por `timedelta(...)` (ADR-003).
  - `src/domains/sales_whatsapp/workflows/sales_session.py:111`
  - `src/domains/remarketing_whatsapp/workflows/remarketing.py:112,120,141,167,196,204`
  - Verificar que `from datetime import timedelta` ya esta importado (linea 5 en ambos).

- [x] **F1.2** - Inicializar `self._force_shutdown: bool = False` en `__init__` de ambos workflows. Reemplazar `getattr(self, '_force_shutdown', False)` por acceso directo `self._force_shutdown`.
  - `src/domains/sales_whatsapp/workflows/sales_session.py:53-56,94,105,115`
  - `src/domains/remarketing_whatsapp/workflows/remarketing.py:72-75,182,183,198,200,209`

- [x] **F1.3** - Borrar linea muerta `metadata_file = Path(ws.path) / "metadata.json"` en `remarketing.py:106`.

- [x] **F1.4** - Reemplazar `except Exception:` por excepciones especificas:
  - `src/domains/sales_whatsapp/tools/routing.py:66` -> `except (RPCError, RuntimeError):` (importar `RPCError` de `temporalio.service`).
  - `src/domains/sales_whatsapp/service.py:95,112` -> idem.
  - `src/core/activities.py:95,114` -> revisar contexto (lectura de filesystem) y hacer especificos (`OSError`, `json.JSONDecodeError`).

- [x] **F1.5** - Fail-fast para `phone_number_id` en `src/core/activities.py:88`. Si la env var falta, `raise RuntimeError("WHATSAPP_PHONE_NUMBER_ID not configured")` en lugar de default `"TESTING"`.

**Done si**:
- Todos los workflows arrancan sin warnings nuevos.
- `grep -rn "workflow.timedelta" src/` no devuelve nada.
- `grep -rn "getattr(self, '_force_shutdown'" src/` no devuelve nada.
- `grep -rn "except Exception:" src/` solo aparece en lugares justificados (top-level entrypoint).
- `grep -rn "TESTING" src/core/activities.py` no devuelve nada.

**Riesgos**:
- El reemplazo de `except Exception` puede dejar pasar errores nuevos. **Mitigacion**: tras el cambio, monitorear logs ~24h.
- Fail-fast en `phone_number_id` puede romper entornos de dev sin la env var. **Mitigacion**: documentar la env var como obligatoria en `.env.example`.

---

## Fase 2 - Extraer infraestructura compartida (bajo-medio riesgo)

**Objetivo**: extraer codigo cross-cutting tecnico a `infrastructure/`. NO mueve business logic; solo elimina duplicacion y centraliza.

**Tareas**:

- [x] **F2.1** - Crear `src/core/infrastructure/temporal/heartbeat.py` con un decorador `@with_heartbeat(every=10)`. Aplicarlo a `execute_tool` en `src/core/activities.py:24`. Borrar el loop manual de `_heartbeat_loop`. **Tambien aplicado a `send_whatsapp_message_activity`** (riesgo de timeout con muchos chunks).

- [x] **F2.2** - Crear `src/core/infrastructure/temporal/retry_policies.py` con `_LLM_OPTIONS`, `_TOOL_OPTIONS`, `_CONV_OPTIONS` exportados. Importarlos desde los workflows en lugar de duplicarlos.

- [x] **F2.3** - Crear `src/core/brains.py` con `load_brain(brain_dir: Path) -> list[str]`. Usar desde:
  - `src/domains/sales_whatsapp/service.py:_load_shared_brain` (wrapper interno mantenido)
  - `src/domains/remarketing_whatsapp/workflows/remarketing.py:_load_remarketing_brain` (wrapper interno mantenido para no romper history shape)

- [x] **F2.4** - Crear `src/core/constants.py` con:
  - `ROUTE_VENTAS = "ventas"`
  - `ROUTE_REMARKETING = "remarketing"`
  - `WHATSAPP_SESSION_PREFIX = "wa_"`
  - `SALES_QUEUE = "queue-sales-agent"`
  - `REMARKETING_QUEUE = "queue-remarketing-agent"`

- [x] **F2.5** - Confirmar que `get_base_tools_registry` en `src/core/registries.py` es **stateless** (sin caches a nivel de modulo). Verificado: cada llamada construye un `ToolRegistry()` nuevo. Sin cambios.

- [x] **F2.6** - Limpiar imports diferidos sin justificacion:
  - `src/core/activities.py`: `import time`, `import json`, `import os` subidos a top. El import diferido de `TransferToSalesAgentTool` se mantiene (con WHY documentado: evita que core conozca dominios).
  - `src/domains/sales_whatsapp/tools/routing.py`: `import time` subido a top. Imports diferidos de workflows/registries se mantienen (la tool desaparece en Fase 4).
  - `src/domains/sales_whatsapp/tools/tags.py`: `import time` subido a top.
  - `src/domains/sales_whatsapp/service.py`: `WorkflowExecutionStatus` subido a top, eliminados imports duplicados, eliminado `whatsapp_client` no usado, eliminado `asyncio` no usado.

**Bonus (hallazgos perdidos en Fase 1)**:

- [x] **F2.bonus.1** - Eliminar `_CONTINUE_AS_NEW_AFTER_TURNS = 50` de `src/domains/remarketing_whatsapp/workflows/remarketing.py` (m-3 / ADR-004 cumplido).
- [x] **F2.bonus.2** - Reemplazar `except Exception:` por `except RuntimeError:` en `remarketing.py:108` (M-3 perdido en F1.4).
- [x] **F2.bonus.3** - Eliminar `import json` y `import time` no usados en `remarketing.py` (m-5).

**Done si**:
- `grep -rn "asyncio.create_task(_heartbeat_loop)" src/` no devuelve nada. **VERIFICADO**.
- `grep -rn "_LLM_OPTIONS = " src/` aparece solo en `retry_policies.py`. **VERIFICADO**.
- Los magic strings `"ventas"`, `"remarketing"`, `"wa_"` solo aparecen en `constants.py`. **VERIFICADO** (excepto `"wa_unknown"` en `tags.py:62` como fallback de session_id, no es la constante).

**Riesgos materializados**:
- Ninguno. Las opciones eran identicas en ambos workflows; centralizarlas no perdio info.

**Estado**: Completada 2026-04-28.

---

## Fase 3 - Sacar business logic de workflows (medio riesgo)

**Objetivo**: aplicar SRP a workflows y service. Mover prompts y reglas de negocio a `domain/policies/`.

**Tareas**:

- [x] **F3.1** - Crear `src/domains/sales_whatsapp/domain/policies/prompts.py` con `build_ghosting_prompt()` y activity stub `decide_ghosting_action`. Workflow llama la activity (cambio de history shape; aceptable por ADR-005 + drain previo).

- [x] **F3.2** - Crear `src/domains/remarketing_whatsapp/domain/policies/prompts.py` con `build_remarketing_trigger(motivo, memory_context)` y activity stub `build_remarketing_trigger_activity`.

- [x] **F3.3** - `turn_count=0` y `_CONTINUE_AS_NEW_AFTER_TURNS` eliminados de Remarketing en F2.bonus.1; verificado en F3.6 al reemplazar la signature del workflow (ya no se referencia en remarketing.py).

- [x] **F3.4** - Limpiar comentarios obsoletos: `# Hubara Specific: 3 minutes...` (era 1 min), `# We return immediately...` (ya no devuelve), comentario largo dentro de `_load_remarketing_brain`, comentario "Loop stateful del Remarketing", "Esperar mensajes...", "Rutear devuelta a ventas...".

- [x] **F3.5** - Parser puro `parse_whatsapp_inbound(body) -> WhatsAppMessage | None` en `parsers.py`. Distingue 400 (malformed -> `ValueError`) de 200 (status update legitimo -> `None`). Handler FastAPI retorna 400 si malformed; `service.process_incoming_message` ahora recibe `WhatsAppMessage` ya parseado.

**Done si**:
- Los prompts estan en `domain/policies/prompts.py` con tests unitarios (sin mocks).
- `process_incoming_message` tiene < 30 lineas y delega a 3 funciones nombradas.
- Los tests de Fase 3 pasan (ADR-005).

**Riesgos**:
- Mover prompts puede cambiar accidentalmente el contenido. **Mitigacion**: tests con assertEqual exacto del string completo.
- Eliminar `turn_count` de Remarketing requiere coordinacion: el `SessionInput` se comparte. **Mitigacion**: `turn_count` queda como `Optional[int] = 0` y Remarketing simplemente no lo usa.

---

## Fase 4 - Eliminar tools-como-sub-workflows (alto riesgo)

**Objetivo**: aplicar ADR-001. La tool deja de orquestar workflows; el workflow toma la decision.

**Tareas**:

- [x] **F4.1** - DTOs `TransferDecision` y `ScheduleRemarketingDecision` en `src/core/contracts.py`.

- [x] **F4.2** - Refactorizadas `TransferToSalesAgentTool.execute` y `ManageConversationTagTool.execute`:
  - Escriben metadata.json (lectura/escritura local OK porque corre dentro de activity).
  - Devuelven JSON con `transfer_decision` o `schedule_remarketing` mas un `message` legible.
  - Eliminados `start_workflow`, `signal` e import de `temporal_client`.

- [x] **F4.3** - Activities nuevas `start_or_signal_sales_workflow_activity` y `schedule_remarketing_workflow_activity` en `src/core/infrastructure/temporal/dispatcher_activities.py`. Retriable. Testeable con `ActivityEnvironment` + monkeypatch del client.

- [x] **F4.4** - Workflows leen `result.transfer_decision` / `result.schedule_remarketing` (extraidas por `run_agent_turn`) y disparan la activity dispatcher correspondiente. El "salvavidas determinista" de Remarketing tambien usa la nueva activity con un `TransferDecision` sintetico.

- [x] **F4.5** - `from src.core.temporal_client import get_temporal_client` eliminado de `routing.py` y `tags.py`. Solo aparece en `core/`, `service.py` y workers (composition root).

- [x] **F4.6** - Tests:
  - `tests/test_transfer_tool.py` - introspeccion del modulo + ejecucion async con fake context, valida JSON de salida y metadata escrito.
  - `tests/test_dispatcher_activities.py` - `ActivityEnvironment` + monkeypatch de `get_temporal_client` para verificar `signal` y `start_delay`.
  - Tests del workflow con `WorkflowEnvironment.start_time_skipping`: pendiente de replay test (requiere fixture de history). Ver "Hallazgos post-refactor" en AUDIT.md.

**Done si**:
- `grep -rn "get_temporal_client" src/domains/` no devuelve nada (solo aparece en `core/`).
- Las tools no tienen `import temporalio` ni `start_workflow`.
- El replay test pasa.

**Riesgos**:
- **El mas grave**: cambiar la shape de history del workflow de Remarketing rompe sesiones en vuelo. **Mitigacion**:
  - Drainar workflows en vuelo antes del deploy (CronJob de espera).
  - O usar `workflow.patched(...)` para versionado.
  - Documentar en el PR el plan de deploy.

---

## Fase 5 - Deduplicar `_run_turn` (medio riesgo)

**Objetivo**: eliminar las 80 lineas duplicadas entre `sales_session.py` y `remarketing.py`.

**Tareas**:

- [x] **F5.1** - Elegida Opcion A (helper explicito, no mixin). Archivo: `src/core/workflow_helpers.py`.

- [x] **F5.2** - Creado `src/core/workflow_helpers.py::run_agent_turn` con dataclasses `PendingMessage` y `TurnResult`. Centraliza el loop LLM-tool-LLM, parsea JSON de tools que emiten decision (`_try_parse_decision_payload`) y expone las decisiones extraidas en `TurnResult`.

- [x] **F5.3** - `_run_turn` en `sales_session.py` eliminado. El workflow llama `await run_agent_turn(input, msg)` y delega.

- [x] **F5.4** - `_run_turn` en `remarketing.py` eliminado. El workflow llama `await run_agent_turn(input_data, msg, fallback_plugin_context=_load_remarketing_brain())`.

- [x] **F5.5** - Tests de regresion:
  - `tests/test_run_agent_turn.py` cubre las piezas puras del helper (parser de decisiones, serializacion DTOs).
  - Replay test contra history fixture: pendiente (requiere capturar una history real antes/despues). Ver "Hallazgos post-refactor".

**Done si**:
- `_run_turn` aparece solo en `turn_runner.py`.
- Replay tests pasan en ambos workflows.

**Riesgos**:
- Compartir codigo entre workflows aumenta el blast radius de un cambio. **Mitigacion**: tests de replay obligatorios.
- El helper se ejecuta dentro del contexto del workflow (debe seguir siendo determinista). **Mitigacion**: marcar el archivo con un comentario `# DETERMINISTIC: imported via workflow.unsafe.imports_passed_through`.

---

## Resumen final

| Fase | Fixes | Riesgo | Tests obligatorios |
|------|-------|--------|--------------------|
| 1 | 5 | Bajo | No |
| 2 | 6 (+3 bonus) | Bajo-Medio | No |
| 3 | 5 (+ tareas diferidas de F2 user) | Medio | Si (ADR-005) |
| 4 | 6 | Alto | Si + replay |
| 5 | 5 | Medio | Si + replay |

**Total**: 27 fixes planificados (30 con bonus de F2).

## Tareas diferidas a Fase 3 desde sugerencias del usuario para Fase 2

El usuario propuso tres tareas adicionales en Fase 2 que se difieren a Fase 3 por ser de mayor riesgo o por requerir replay tests (ADR-005):

- [x] **F3.6** (diferida desde "F2.2 user") - `RemarketingSessionInput` dataclass en `src/domains/remarketing_whatsapp/contracts.py` y firma de `RemarketingSessionWorkflow.run` cambiada a `(input: RemarketingSessionInput)`. Caller actualizado en `tools/tags.py`. Rompe shape de history -> requiere drain previo al deploy.
- [x] **F3.7** (diferida desde "F2.3 user") - `integrations.py` movido a `src/core/infrastructure/whatsapp/client.py`. `send_whatsapp_message_activity` movido a `src/core/infrastructure/whatsapp/activities.py` (preservando `name="send_whatsapp_message_activity"`). Workers y workflows apuntan al nuevo path. `integrations.py` queda como shim deprecated (re-export) para amortiguar imports legacy externos.
- [x] **F3.8** (diferida desde "F2.7 user") - Estructura `tests/` creada con `conftest.py` (fixture `temporal_env`), `test_smoke.py`, `test_parsers.py`, `test_prompts.py`, `test_imports.py`, `test_remarketing_contract.py`. `pyproject.toml` con `pytest>=8` + `pytest-asyncio>=0.23` + `[tool.pytest.ini_options]`. **Tests no ejecutados** en este entorno (no hay garantia de pytest disponible); comando documentado: `cd hubara_agency && pytest tests/ -v`.
