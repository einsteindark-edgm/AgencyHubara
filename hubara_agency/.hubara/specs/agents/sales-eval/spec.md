# Capability: agents/sales-eval — Scorecard por etapa del Asesor de Ventas

> Bootstrap 2026-09-15 (HU-SC-0..SC-6). Código: `hubara_agency/src/plugins/chats/agent/sales_eval/scorecard/`,
> worker `chats/workers/sales_eval.py`, API `chats/api/scorecards.py`, dashboard
> `frontend_dashboard/src/plugins/agents_admin/frontend/` (pestaña Calidad LLM).
> Diseño y contrato de API: `SALES_SCORECARD_PLAN.md` en la raíz.

## Purpose

Evaluar cada episodio del asesor de ventas con un checklist binario por etapa
del funnel, con checks críticos que reprueban solos, sobre la trayectoria
completa del episodio (texto, tools, componentes, estado, guardas), para ver
errores como los del PR #281 de forma fácil y fiable a escala. Reemplaza al
promedio holístico de métricas como titular.

## Requirements

### Requirement: Veredicto nunca es un promedio

El scorecard SHALL emitir `FALLA` si cae al menos un check crítico, `ALERTA`
si cae al menos uno mayor y ninguno crítico, `PASA` en otro caso, y
`SIN_DATOS` si el episodio no tiene turnos. `desconocido` y `no_aplica` NO
SHALL contar como `pasa`. El cumplimiento (pasados sobre decididos) es
secundario.

#### Scenario: Episodio del PR #281

- GIVEN la trayectoria de los runs 01a0a0eb / 01a0a0f1 antes del fix (formulario sin confirmación, 11 aromas en texto, CONFIRMADO_SIN_DATOS sin sí)
- WHEN se califica solo con checks de código
- THEN el veredicto es `FALLA` con 5 críticos (VAR-01, CON-01, CON-02, TAG-01, TAG-02) anclados a sus turnos
- AND el mismo episodio con las guardas del PR #281 da `ALERTA` (VAR-01b, CON-05, TAG-01b)

### Requirement: Checks de juez aislados y calibrados

Cada check de juez SHALL evaluarse con una llamada aislada al juez, con la
trayectoria renderizada (tools con resultado, componentes, texto suprimido,
etapa, señal), dos muestras que deben coincidir (si no, `desconocido`) y un
prefiltro de aplicabilidad en código. El prompt SHALL indicar que el estado
del sistema no es prueba. Un check de juez crítico NO SHALL reprobar un
episodio solo hasta estar calibrado (kappa ≥ 0.6 con n ≥ 50 etiquetas humanas).

#### Scenario: Juez inconsistente

- WHEN las dos muestras del juez difieren (pasa y falla)
- THEN el resultado es `desconocido` y el check entra a la cola de etiquetado

### Requirement: Evaluación al cierre del episodio

Al cerrar un episodio, `EvaluateEpisodeWorkflow` SHALL esperar la traza del
turno de cierre, correr el scorecard (gate `scorecard-v1`), guardar el
registro en `<vault>/_evals/scorecards/<fecha>.jsonl`, emitir un punto por check
a SigNoz (`check.<id>`, suite `scorecard`) y luego correr la eval legada. Un
error del scorecard NO SHALL impedir la eval legada.

### Requirement: Trayectoria legada honesta

Los episodios sin trazas SHALL evaluarse con fidelidad `legacy` (y los que
empezaron antes de la traza con `partial`); los checks que dependen de datos
de la traza SHALL devolver `desconocido`, nunca `pasa`.

### Requirement: Alerta de fallo crítico con dedup

Un episodio en `FALLA` SHALL abrir un issue de GitHub con la huella de sus
checks críticos o comentar el issue abierto con la misma huella. El cuerpo NO
SHALL incluir teléfonos completos y la evidencia SHALL pasar por la redacción
de PII. Sin `SCORECARD_ALERTS_GITHUB_TOKEN` y `SCORECARD_ALERTS_REPO` es un no-op.

### Requirement: Goldens con el mismo registro

El runner de goldens SHALL calificar cada corrida con el mismo registro de
checks que producción y reportar pass^k por escenario (ninguna de las k
corridas en `FALLA`).

## Out of scope

- La eval legada por métricas DeepEval (convive durante la transición, plan §3.7).
- El scorecard del agente de remarketing (HU-SC-7).
