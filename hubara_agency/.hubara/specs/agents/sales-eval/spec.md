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
Cada etiqueta humana SHALL guardar `judge_verdict`, el veredicto del juez que
el operador vio al etiquetar; la calibración se computa desde las etiquetas,
sin releer meses de scorecards en cada cierre de episodio.

#### Scenario: Juez inconsistente

- WHEN las dos muestras del juez difieren (pasa y falla)
- THEN el resultado es `desconocido` y el check entra a la cola de etiquetado

### Requirement: Evaluación al cierre del episodio

Al cerrar un episodio, `EvaluateEpisodeWorkflow` SHALL esperar la traza del
turno de cierre, correr el scorecard (gate `scorecard-v1`), guardar el
registro en `<vault>/_evals/scorecards/<fecha>.jsonl`, emitir un punto por check
a SigNoz (`check.<id>`, suite `scorecard`) y luego correr la eval legada. Un
error del scorecard NO SHALL impedir la eval legada.

El evento de cierre se despacha ANTES de que el turno de cierre envíe y
persista su traza. Además de la gracia fija del workflow (90 s), la activity
SHALL sondear hasta 180 s por una traza del episodio con `recorded_at_ms ≥
closed_at_ms` cuando el cierre es reciente (≤ 15 min); si no llega, evalúa
igual y deja constancia en el log. La evidencia emitida a SigNoz SHALL pasar
por la redacción de PII (cita al cliente).

#### Scenario: Turno de cierre lento

- GIVEN el episodio cerró (`closed_at_ms`) y el envío del último mensaje reintenta
- WHEN el scorecard corre antes de que la traza de ese turno aterrice
- THEN espera (sondeo) y evalúa con el turno de cierre incluido
- AND no reprueba en falso checks del cierre (CIE-04, TAG-01) por un turno ausente

### Requirement: Barrido diario del scorecard

El barrido diario de la eval (`SalesEvalWorkflow`, schedule `sales-eval-schedule`,
23:00 Bogotá en prod) SHALL correr también el scorecard (gate
`daily-scorecard-v1`), uno a la vez, sobre los episodios con actividad en la
ventana que el disparo al cierre no cubre: los ABIERTOS (INTERESADO, ruta
humano, en curso: nunca emiten cierre) y los CERRADOS sin un scorecard
posterior al cierre. Sin mínimo de turnos. Episodios sin mensajes del cliente
quedan fuera. Un error del scorecard NO SHALL impedir la eval legada.

#### Scenario: Lead que quedó INTERESADO

- GIVEN un cliente conversó hoy y el episodio quedó abierto en INTERESADO
- WHEN corre el barrido de las 23:00
- THEN el episodio queda calificado con el scorecard y aparece en Calidad LLM

### Requirement: Fecha del episodio para listas y tendencias

Cada registro SHALL llevar `episode_date` (fecha UTC del cierre, o del inicio
si sigue abierto). La lista de scorecards, el embudo y la tendencia semanal
SHALL ventanearse y agruparse por esa fecha, no por la fecha de evaluación:
el backfill califica hoy conversaciones de hace meses.

#### Scenario: Backfill de historial

- GIVEN un episodio cerrado hace 3 meses calificado hoy por el backfill
- WHEN el dashboard pide los últimos 56 días
- THEN el episodio no aparece en la lista ni infla la semana actual de la tendencia

### Requirement: Trayectoria legada honesta

Los episodios sin trazas SHALL evaluarse con fidelidad `legacy` (y los que
empezaron antes de la traza con `partial`); los checks que dependen de datos
de la traza SHALL devolver `desconocido`, nunca `pasa`.

El JSONL del dashboard no dice qué agente escribió cada mensaje. En la
trayectoria legada, un mensaje del bot que llega más de 30 minutos después del
último mensaje del cliente NO SHALL atribuirse al asesor de ventas: es
remarketing, una notificación de envío o un recordatorio.

#### Scenario: Notificación de envío dentro de un episodio abierto (primer informe, 9-15 sep)

- GIVEN el cliente preguntó por las formas de pago y el asesor respondió en segundos
- AND al día siguiente el agente de envíos escribió "Tu pedido ya va en camino 🚚" en la misma sesión
- WHEN se califica el episodio sin trazas
- THEN la notificación no forma parte de la trayectoria y no reprueba EST-02 al asesor

### Requirement: Los datos públicos de la política no son hallazgos

DES-05 NO SHALL tratar como precio de catálogo los montos de la política de
pago o de envío (umbral de contra entrega, recargos, valor del envío). ENV-05
NO SHALL reprobar la llave Nequi del negocio (`PAYMENT_NEQUI_NUMBER`), único
dato de pago que el guion permite escribir; cualquier otra cuenta o número sí.

### Requirement: Juez resiliente al límite por minuto

Una llamada al juez que falla por límite de cuota (429, `RESOURCE_EXHAUSTED`)
SHALL reintentarse con espera creciente (5, 15 y 30 s); otros errores no se
reintentan. Si el juez no respondió ninguna llamada, el registro SHALL decir
`judge=false` y contar los errores en `judge_errors`: un scorecard con el juez
caído no se presenta como juzgado.

### Requirement: Alerta de fallo crítico con dedup

Un episodio en `FALLA` SHALL abrir un issue de GitHub con la huella de sus
checks críticos o comentar el issue abierto con la misma huella. El cuerpo NO
SHALL incluir teléfonos completos y la evidencia SHALL pasar por la redacción
de PII. Sin `SCORECARD_ALERTS_GITHUB_TOKEN` y `SCORECARD_ALERTS_REPO` es un no-op.
El issue abierto se busca LISTANDO los issues con la etiqueta (API consistente),
no con la búsqueda de GitHub (indexa con retraso y limita a 30/min). Recalcular
un episodio ya guardado en `FALLA` con la misma huella NO SHALL volver a
alertar.

#### Scenario: Recálculo de un FALLA conocido

- GIVEN el episodio ya tiene un scorecard guardado en `FALLA` con huella H
- WHEN el operador recalcula (o corre `ScoreEpisodeWorkflow`) y vuelve a dar `FALLA` con huella H
- THEN no se abre ni comenta ningún issue

### Requirement: Goldens con el mismo registro

El runner de goldens SHALL calificar cada corrida con el mismo registro de
checks que producción y reportar pass^k por escenario (ninguna de las k
corridas en `FALLA`).

### Requirement: API del scorecard segura ante ids y dobles clics

Los ids de sesión y episodio SHALL validarse con coincidencia completa
(`fullmatch`: sin saltos de línea finales). El recálculo con juez SHALL usar un
id de workflow estable por episodio (`scorecard-<sesión>-<episodio>`): un
segundo clic mientras corre reusa el run en vuelo en vez de pagar dos veces el
juez. La respuesta del recálculo SHALL decir si el juez quedó encolado
(`judge_queued`, `judge_error`) y el dashboard SHALL mostrarlo y sondear el
detalle hasta que llegue el registro del juez.

## Out of scope

- La eval legada por métricas DeepEval (convive durante la transición, plan §3.7).
- El scorecard del agente de remarketing (HU-SC-7).
