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

### Requirement: Juez LLM apagado por defecto

Desde 2026-09-28 (decisión del operador por costo), ningún camino del eval
SHALL llamar al juez LLM salvo con `EVAL_LLM_JUDGE_ENABLED=true`. Sin la
variable, el scorecard SHALL correr solo sus checks de código (`judge=false`),
la eval legada SHALL devolver `skipped` con `error="llm_judge_disabled"` sin
leer el episodio, la suite de goldens SHALL correr con `--no-judge` y el
recálculo del dashboard con juez SHALL responder `judge_queued=false` con el
motivo en `judge_error`, sin encolar el workflow.

#### Scenario: Episodio cerrado con el juez apagado

- GIVEN `EVAL_LLM_JUDGE_ENABLED` sin definir
- WHEN cierra un episodio
- THEN se guarda un scorecard con checks de código y `judge=false`
- AND no se hace ninguna llamada al juez

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

### Requirement: Claude Code es el juez del scorecard

Desde el 2026-09-28 (decisión del operador) los checks de juez SHALL
calificarlos Claude Code con los mismos criterios, y Gemini SHALL NOT
llamarse desde el scorecard (vuelve solo con `EVAL_LLM_JUDGE_ENABLED=true` y
`SCORECARD_JUDGE=litellm` en producción, o `LAB_JUDGE=litellm` en la caja).
Los caminos automáticos (cierre, barrido diario, recálculo del dashboard)
SHALL encolar solo con `EVAL_LLM_JUDGE_ENABLED=true` (ver «Juez LLM apagado
por defecto»); cuando el operador le pide a Claude Code calificar, el
recálculo A PEDIDO (`judge_kind="claude"`: `rescore_scorecards.py
--claude-judge`, `claude_judge.py aplicar`) SHALL encolar aunque el
interruptor esté apagado (no gasta API). El scorecard SHALL dejar el
prompt EXACTO de cada check de juez sin respuesta en una cola
(`<vault>/_evals/judge_queue/` en producción, `runs/<corrida>/judge/` en el
laboratorio) y el check SHALL quedar `desconocido` con la crítica
«pendiente: lo califica Claude Code», sin contar como error del juez. Una
respuesta de Claude Code SHALL validarse con los parsers del juez antes de
entrar (en modo turno, debe calificar todas las candidatas) y SHALL aplicarse
solo al prompt con la misma huella: si la conversación o el catálogo cambian,
el check vuelve a la cola. Los checks de código no cambian. El juez de Claude
Code no gasta API: no entra al estimado ni a la reserva del tope del
laboratorio.

#### Scenario: Un episodio de producción espera a Claude Code

- GIVEN un episodio y un check de juez que aplica
- WHEN el operador pide calificarlo y corre el recálculo a pedido (`judge_kind="claude"`), o cierra con `EVAL_LLM_JUDGE_ENABLED=true`
- THEN el check queda `desconocido` con «pendiente: lo califica Claude Code» y su prompt queda en la cola, sin llamar a Gemini

#### Scenario: Claude Code califica y se aplica

- GIVEN Claude Code respondió todos los prompts de un episodio con veredictos legibles
- WHEN se recalcula ese episodio (`scripts/claude_judge.py aplicar`)
- THEN cada check de juez toma el veredicto, el turno, la evidencia y la crítica de Claude Code

#### Scenario: Una corrida del laboratorio se califica sin volver a simular

- GIVEN una corrida terminada con prompts del juez pendientes y Claude Code ya respondió en `runs/<corrida>/judge/answers.jsonl`
- WHEN se ordena a la caja solo evaluar (`dispatch.sh <corrida> <imagen> evaluate`)
- THEN la caja califica de nuevo sin simular, conserva el gasto que la corrida llevaba y el resumen dice cuántas calificaciones siguen pendientes

### Requirement: Producción se califica turno por turno, como el laboratorio

Desde el 2026-10-02 (decisión del operador: Calidad LLM con la vista del
laboratorio), el scorecard de cada episodio real SHALL calificarse en modo
turno, igual que el laboratorio califica su bot de producción (A0): cada
turno del cliente con el prefijo real como contexto y el episodio como
estaba al empezar ese turno (sin el cierre posterior; la orden, solo si ya
existía). El registro guardado SHALL traer `mode: turn` y `by_turn` (el
veredicto de cada turno) y el veredicto del episodio con la misma regla sobre
la unión de los checks por turno. La fila de la matriz SHALL quedarse, por
check, con el resultado más fuerte entre turnos (una falla no se esconde
detrás de un pasa). El cierre, el barrido diario, `ScoreEpisodeWorkflow`, el
backfill y el recálculo de la API SHALL guardar lo mismo; el recálculo
conserva el juicio previo en su turno. SigNoz recibe un punto por check y
episodio.

#### Scenario: Incidente del PR #281 turno por turno

- GIVEN la trayectoria del PR #281 antes del fix
- WHEN se califica en modo turno
- THEN fallan los mismos checks en los mismos turnos que calificando el episodio entero, el episodio da `FALLA` y los turnos 5 y 9 dan `FALLA`

### Requirement: El bot Jev es el workflow nuevo

Calidad LLM SHALL separar los episodios por el workflow que respondió los
turnos del cliente: `workflow: v2` en la traza del turno (lo escribe
`persist_turn_trace` desde la activity, sin cambiar el workflow) = bot Jev;
`v1` = bot actual. Las trazas de antes del campo SHALL reconocerse por la
salida de Jev (`egress`), que el workflow actual nunca deja. Las capas de
percepción sobre el workflow actual (`mode: on/canary`) NO SHALL contar como
bot Jev. Un episodio con turnos de los dos es `mixto` y no se le carga a
ninguno.

### Requirement: Cada decisión de Jev queda con su conversación

Cada decisión de una capacidad en la que Jev participa (proveedor `sombra` o
`jev`, con algo que preguntarle) SHALL quedar en
`<vault>/<sesión>/evals/decisions.jsonl` con su hora, su etapa (`ingest` con
el mensaje que la disparó, `turno`, `remarketing`, `cierre`) y el veredicto
compacto con lo personal tapado (lo mismo que publica el laboratorio). Con la
regla de hoy, o sin pregunta para Jev, NO SHALL escribirse nada. Escribirla
nunca SHALL frenar la decisión.

#### Scenario: Una decisión en la ventana del turno

- GIVEN una lectura del ingest del mensaje `wamid.B` y una decisión del egreso durante el turno 3
- WHEN el operador abre la ventana del turno 3 en Calidad LLM
- THEN «Decisiones de Jev» muestra la lectura «Al leer el mensaje 1» y la del egreso «Durante el turno», con quién decidió y por qué

### Requirement: Calidad LLM muestra producción con la vista del laboratorio

La pestaña Calidad LLM de Agents SHALL mostrar, para la ventana y el bot
elegidos (Todos / Bot actual / Bot Jev): en Resumen, el cumplimiento por
check semana a semana, dónde terminan los episodios, la matriz episodios ×
checks y, a pedido, cómo le fue a cada bot y el informe de Jev de los turnos
reales (tiempos, caídas a la regla con su motivo, costo por turno); en
Conversaciones, cada conversación real como un hilo con cada turno
calificado, «Resultado» y «Qué falló» por nombre, y la ventana del turno
(resultado, paso a paso, decisiones de Jev). Los datos SHALL leerse por el
contrato `evals@v1` (`/api/chats/evals/production/*`, cast
`/api/agents/evals/production/*`); un episodio sin calificar en modo turno
SHALL calificarse al vuelo con el código.

### Requirement: Calidad LLM muestra el motor de decisiones

La pestaña «Motor de decisiones» de Calidad LLM SHALL mostrar la versión del
motor que corre la tienda (paquete de decisión y versión, de dónde sale, el
oráculo de Jev, el perfil del turno y el contrato del motor) y cada decisión
que toma, agrupada por la parte del software donde actúa y en el orden de una
conversación, con qué resuelve en español llano y quién la decide hoy (la
regla, Jev en sombra, Jev en las conversaciones de prueba o Jev). Lo que se
muestra SHALL salir del catálogo del plugin (`places:` y `about:` de
`builtins.yaml`) por el contrato `perception-rollout@v1`
(`/api/chats/perception/engine`, cast `/api/agents/perception/engine`): una
capacidad que el código pide sin su `about` NO SHALL certificar (DB003).

#### Scenario: El paquete de la tienda no es el del código

- GIVEN la tienda configurada con `ventas-2` y la capacidad «Baja de mensajes» en sombra
- WHEN el operador abre «Motor de decisiones»
- THEN ve «ventas-2, versión 2», que lo eligió la configuración de la tienda y que el código trae «ventas», y «Baja de mensajes» bajo «Al leer cada mensaje del cliente» con «Jev en sombra: mide, decide la regla»

### Requirement: ENV-02 cuenta el mensaje del formulario solo cuando responde

El formulario de datos de envío sale con un mensaje que arma el código
(producto, variantes, cantidad y subtotal; sin Flow, la lista de campos) y la
traza lo trae en `card_text`. ENV-02 SHALL contar ese mensaje como respuesta
del bot solo si a CADA mensaje del cliente en el turno no le queda nada al
quitarle lo que el formulario cubre: un sí (también con letras repetidas,
«Okis» o un emoji de sí como 👍 ❤️ 🤍), una cantidad («2», «dos», «uno»,
«x2», «2 und», «2 de esas», «2 velas»), el producto y las variantes que nombra
el PROPIO mensaje del formulario (en cualquier género o número), y cortesías
o relleno («por favor», «gracias», artículos). Cualquier resto (una pregunta,
una condición, una variante que el formulario no nombra, otro emoji), un
aplazamiento o un turno de traspaso SHALL exigir un texto propio del bot. El
veredicto SHALL depender solo de la traza, nunca del catálogo.

Límite: es una regla de palabras que falla cerrada; ante la duda pide texto
(un sí poco común puede fallar de más). Los casos viven en el trinquete de
`tests/evals/scorecard/test_env02_ratchet.py`: se agregan, nunca se sacan.

#### Scenario: Pregunta después de un sí (revisión del PR #392)

- GIVEN el formulario salió con «2× Velón Koala (Blanco, Lavanda)» y sin texto del bot
- WHEN el cliente escribió «sí, cuánto se demora», o en una ráfaga «sí» y después «cuánto se demora»
- THEN ENV-02 falla en ese turno
- AND con «2», «Siii», «👍» o «Lavanda» ENV-02 pasa
- AND con «Azul» ENV-02 falla: el formulario no la nombra (el bot no recogió la elección)

### Requirement: El árbitro no inventa ni esconde fallas (registro v8)

Desde el 2026-10-07 (`REGISTRY_VERSION` 8):

- EST-03b NO SHALL fallar en un turno en que el bot se abstuvo (`NO_MESSAGE`,
  `suppressed_reason: no_message`): la guarda de texto administrativo lee el
  centinela como etiqueta interna, pero no hubo texto que frenar.
- Un turno que es SOLO un traspaso de remarketing (la traza trae el encuadre
  de la plataforma como `inbound_text` y `trigger: customer`) SHALL leerse
  como traspaso: `trigger: handoff`, el resumen del traspaso como lo que dijo
  el cliente y sin primer contacto (el bot no saluda a quien viene de
  remarketing).
- Con el episodio cerrado, CIE-03 y CIE-04 SHALL juzgarse con el episodio
  completo también en modo turno y su falla SHALL quedar en el turno que la
  causó. Con el episodio abierto siguen esperando («sin señal»).
- VAR-07 SHALL contar como precio visto el subtotal del mensaje del
  formulario (`card_text`), como el resumen del pedido.

#### Scenario: Orden registrada sin su etiqueta ni su escalación

- GIVEN un episodio cerrado con `register_order` en el turno 2, cerrado como `INTERESADO` y sin escalar `PAYMENT_VERIFICATION_PENDING`
- WHEN se califica en modo turno
- THEN CIE-03 y CIE-04 fallan en el turno 2, el turno 2 da `FALLA` y el episodio da `FALLA`

### Requirement: Que le guste no es elegirlo y las tarifas responden el envío (registro v9)

Prueba del operador del 2026-10-07 con el bot nuevo (`ep_012`): buena
conversación con dos ALERTA falsas. Desde `REGISTRY_VERSION` 9:

- DES-08 NO SHALL contar como elegir un producto que el cliente diga que le
  gusta o le encanta («me gusta», «me encanta»): el bot le pregunta si se lo
  lleva y lo anota cuando dice que sí. «Me gustaría», «lo quiero», «me lo
  llevo» siguen siendo elegirlo.
- Contestar solo con el nombre SHALL seguir siendo elegirlo, salvo que
  conteste una pregunta por lo que le gustó o le interesa («¿cuál te gustó?»,
  «¿alguna te llamó la atención?», «te cuento los detalles») sin pregunta de
  compra en la misma burbuja («¿te la llevas?», «¿cuál quieres?»,
  «¿prefieres…?»).
- ENV-02 SHALL contar el mensaje de tarifas (`send_shipping_rates`, lo arma el
  código) como respuesta a cuánto cuesta el envío cuando el cliente lo leyó en
  el turno. Un plazo, una ciudad que nombran o un medio de pago SHALL seguir
  exigiendo un texto del bot.

#### Scenario: Le gustó la calabaza y después la eligió

- GIVEN el bot mostró la línea de Halloween y preguntó «¿Cuál de las cuatro te gustó? Dime el nombre y te cuento los detalles»
- WHEN el cliente contestó «La de la calabaza» y el bot mostró el detalle sin anotarla
- THEN DES-08 no aplica en ese turno
- AND si el bot hubiera preguntado «¿Cuál te llevas?», la misma respuesta sin anotarla falla

#### Scenario: «Si» y «¿cuánto cuesta el envío?» con tarifas y formulario

- GIVEN el cliente escribió en una ráfaga «Si» y «Cuánto cuesta el envío ?»
- WHEN el bot mandó las tarifas y después el formulario, sin texto propio
- THEN ENV-02 pasa en ese turno
- AND con «Si» y «cuánto demora el envío?» ENV-02 falla: las tarifas no dicen el plazo

### Requirement: Un mensaje nuestro ya abrió la conversación (registro v11)

Incidente del 2026-10-09 (`ep_001` de ···9824): el cliente contestó el
seguimiento de una asesora («te escribe Liliana, asesora de Hubara… tenemos la
trilogía del terror: <enlace>») y Calidad LLM marcó ALERTA y FALLA una venta
bien atendida. Desde `REGISTRY_VERSION` 11, cuando el turno cita el mensaje
nuestro que el cliente contesta (`[El cliente responde a este mensaje que le
enviamos: «…»]`):

- APE-01 NO SHALL pedir la marca si ese mensaje ya la dijo; el saludo por la
  hora sí SHALL seguir exigiéndose.
- APE-03 NO SHALL aplicar: la apertura la hicimos nosotros y el cliente llega
  con un tema.
- DES-05 NO SHALL contar como nombrado de memoria un producto que nombró ese
  mensaje (sin importar tildes ni mayúsculas).

Además, DES-05 NO SHALL contar como precio de catálogo el mínimo del contra
entrega dicho como «compras/pedidos desde $X» o «a partir de $X».

#### Scenario: Contesta el seguimiento de la asesora y pregunta por contra entrega

- GIVEN el cliente contesta «Hola» al seguimiento de Liliana, asesora de Hubara, que nombró la trilogía del terror
- WHEN el bot saluda «Buenas tardes» sin la marca y, a «¿Tienen pago contra entrega?», responde «aplica para compras desde $45.000 en productos… ¿Te muestro la trilogía del terror?» sin haber buscado
- THEN APE-01 pasa, APE-03 no aplica y DES-05 no falla en esos turnos
- AND si el bot nombra la Calabaza, que el mensaje no nombró, sin buscar, DES-05 falla

### Requirement: Testigo e informe de huecos

Para decidir con datos qué arreglar (`docs/calidad-llm/cobertura-motor.html`),
`persist_turn_trace` SHALL dejar una línea por turno del cliente en
`<vault>/_huecos/<día UTC>.jsonl` con lo que el scorecard no ve (texto suelto
que se saltó las revisiones de `send_reply`, afirmación sin consultar que Jev
leyó en sombra), sin textos. Escribirla NUNCA SHALL afectar el turno. El
informe (`python -m src.plugins.chats.agent.sales_eval.huecos --dias N`) SHALL
juntar el testigo con los scorecards (v8 o posterior) de la ventana y decir, por propuesta,
cuántos casos hubo por bot y si vale la pena arreglarla: una regla crítica con
un caso; una mayor con 2 casos por cada 100 turnos o 3 conversaciones; una
menor con 5 por cada 100 (con menos de 50 turnos, a lo sumo «quizás»). El
informe NO SHALL llevar textos ni números completos de clientes.

## Out of scope

- La eval legada por métricas DeepEval (convive durante la transición, plan §3.7).
- El scorecard del agente de remarketing (HU-SC-7).
