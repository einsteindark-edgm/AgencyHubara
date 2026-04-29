---
title: Refactor DEHA - Hubara Agency
last_updated: 2026-04-28
---

# Refactor DEHA - Hubara Agency

Esta carpeta es la **fuente de verdad** del proceso de refactorización del monorepo `hubara_agency` hacia DEHA (Durable Execution Hexagonal Architecture). Cada documento cumple un rol especifico:

| Documento | Proposito |
|-----------|-----------|
| `README.md` (este archivo) | Indice + dashboard de progreso por fase. |
| `AUDIT.md` | Auditoria completa rectificada (5 criticos, 8 medios, 7 menores) con `archivo:linea` verificado. |
| `DECISIONS.md` | ADRs cortos (decisiones del usuario que persisten entre sesiones). |
| `PLAN.md` | Plan de 5 fases con checkboxes y tareas con paths concretos. |
| `PHASE6.md` | Plan de Fase 6 - cierre de deuda residual (N-1, N-2, N-3) y consolidacion R-DET 100%. |
| `PROGRESS.md` | Log cronologico append-only de cada fix completado. |

## Estado actual de cada fase

| Fase | Titulo | Riesgo | Estado | Avance |
|------|--------|--------|--------|--------|
| 1 | Estabilizar | Bajo | Completada | 5/5 fixes |
| 2 | Extraer infraestructura compartida | Bajo-Medio | Completada | 6/6 + 3 bonus |
| 3 | Sacar business logic de workflows | Medio | Completada | 8/8 (5 originales + 3 diferidas F3.6/F3.7/F3.8) |
| 4 | Eliminar tools-como-sub-workflows | Alto | Completada | 6/6 (replay test diferido) |
| 5 | Deduplicar `_run_turn` | Medio | Completada | 5/5 (replay test diferido) |
| 6 | Cerrar deuda residual (N-1, N-2, N-3) + R-DET 100% | Medio | Pendiente | 0/7 |

> **Leyenda de estado**: Pendiente / En progreso / Completada / Bloqueada.
>
> Fase 6 detallada en `PHASE6.md`. Aborda los hallazgos diferidos durante la revision integral (R7) y consolida cumplimiento R-DET al 100%.

## Las 5 reglas duras DEHA (recordatorio)

- **R-DET** (Determinismo): los workflows no usan `time.time()`, `uuid.uuid4()`, `random.*`, `datetime.now()`, `open()`, `requests.*`, `httpx.*`. Solo `workflow.now()` / `workflow.uuid4()`.
- **R-JSON** (Boundary): todo lo que cruza `workflow.execute_activity` es un dataclass plano JSON-serializable. Pydantic se queda en `domain/` y `application/`.
- **R-STATELESS** (Hygiene de actividades): las activities reconstruyen sus dependencias en cada invocacion via factories. Cero estado mutable a nivel de modulo.
- **R-HEARTBEAT** (Liveness): toda activity > ~10s envuelve con `@with_heartbeat(every=10)`. Nada de `asyncio.create_task(_loop)` ad-hoc dentro del cuerpo.
- **R-DIP** (Direccion de dependencia): `domain/` y `application/` no importan `litellm`, `temporalio`, `httpx`, `exoclaw_conversation`. Solo `typing.Protocol` ports.

## Como navegar este refactor

1. **Para entender el por que**: leer `AUDIT.md`.
2. **Para ver las decisiones tomadas**: leer `DECISIONS.md`.
3. **Para ver el plan**: leer `PLAN.md`.
4. **Para ver lo hecho**: leer `PROGRESS.md` (orden cronologico inverso).
5. **Para retomar el trabajo**: empezar por la fase con menor numero que aun tenga checkboxes vacios en `PLAN.md`.

## IDs de fix

Cada fix tiene un identificador unico `Fx.y` (Fase x, fix y). El mismo ID se usa en `PLAN.md` (donde se planifica) y en `PROGRESS.md` (donde se registra el cierre). Asi se rastrea el ciclo de vida completo.
