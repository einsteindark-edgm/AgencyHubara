# Motor de decisiones con Jev — plan de ejecución

> Diseño aprobado por el operador el 2026-09-28 («sí a todo»): `MOTOR_DECISIONES_DISENO.html`
> (copia del documento del diseño v2, secciones 00–12). **Revisarlo antes de implementar cada fase.**
> Este archivo es el plan técnico: qué entra en cada fase, dónde vive, cómo se prueba y en qué quedó.

## 0. Decisiones del operador (2026-09-28)

| # | Pregunta | Decisión |
|---|---|---|
| 1 | ¿F0 va dentro de #372? | Sí. Todo el motor va en `lab/todo` (#372). |
| 2 | ¿Workflow V2 (tipo nuevo, V1 congelado)? | Sí. |
| 3 | ¿Jev puede frenar (dato no dicho, «sí» que no era de compra)? | Sí, solo con certeza alta y después de la sombra. |
| 4 | ¿Claude Code califica el banco de referencia y los desacuerdos? | Sí. |
| 5 | Baja de marketing | Esperar a que la capacidad «baja» pase la sombra; después, piso solo con frases inequívocas y Jev decide los casos dudosos. La frase explícita nunca se puede quitar. |

Quitar OpenAI (brazo C) ya estaba decidido: 100 % Jev.

## 1. Reglas que no se negocian

1. **Jev percibe, el código decide, el LLM redacta.** Jev contesta preguntas cerradas; nunca escoge una tool ni una etapa.
2. **Si Jev falla o tarda, decide la regla de hoy**, que queda como respaldo dentro del motor (`reglas`).
3. **Nada actúa sin laboratorio y sin sombra en producción.** Cada capacidad tiene su interruptor: `reglas` → `sombra` → `jev`. Todo nace en `reglas`.
4. **Un arreglo nuevo de alucinación entra como capacidad del motor**, nunca como otro regex en un worker.
5. **Replay:** las reglas viven en activities y viajan grabadas en su resultado; el workflow solo aplica. El contrato solo crece con campos opcionales (Temporal ignora campos desconocidos y rellena los que faltan con su valor por defecto: verificado en `temporalio/converter/_payload_converter.py`).

## 2. Dónde vive cada cosa

| Pieza | Ruta |
|---|---|
| Oráculo (adaptador de Jev, falso, nulo, anonimización) | `hubara_agency/src/platform/perception/` |
| Motor: contratos, fachada, cuestionarios, políticas, perfiles, capacidades, activities | `hubara_agency/src/plugins/chats/agent/sales/decisions/` |
| Registro de bots (V1/V2, proveedor por capacidad, perfil) | `sales/decisions/bots.py` |
| Workflow V2 | `sales/workflows/sales_session_v2.py` |
| Cola de desacuerdos y etiquetas (producción) | `<vault>/_decisions/` |
| Banco de referencia (etiquetas de Claude Code) | S3 del laboratorio, `bench/labels/` |
| CLI de Claude Code | `hubara_agency/scripts/decisions_queue.py`, `scripts/lab_bench_labels.py` |

## 3. Fases

Cada fase: TDD (rojo por comportamiento, nunca por ImportError), batería completa, replay de historias reales, forge, commit.

### F0 · Solo Jev + base del motor — sin cambio de comportamiento ✅
- [x] Quitar OpenAI: adaptador `litellm`, rama en `composition.py`, perfil `openai-lp-v1`, brazo C (backend, API, costos, dashboard), alias `openrouter-perception` y su precio, la llave de OpenRouter del proxy de la caja, docs (07-connectorkit), spec de ventas y plan del laboratorio (§12).
- [x] `sales/perception/` → `sales/decisions/` (mismos nombres de activity: `perceive_burst`, `verify_coverage`).
- [x] Contrato `TurnDecisions` (superconjunto de `PerceiveOutput`, campos nuevos con valor por defecto: `contract`, `versions`, `note`, `coverage`; `VerifyOutput.complement_note`) + `facade.py` (lo único que importa el workflow).
- [x] La nota, las reglas de la capa ② y el texto del complemento viajan grabados en el resultado de las activities; el workflow no conoce preguntas ni umbrales.
- [x] Corregidos mis bugs de #372: ② juzgaba la narración descartada (ahora juzga lo que el cliente ve: `workflow_helpers._text_shown`); «aplaza» quedaba siempre cubierto (ahora pide un texto); ③ contaba `send_reply` y tools rechazadas como tarjetas (`facade.delivered_components`).
- [x] Perfiles del motor (`decisions/profiles.yaml`: oráculo, cuestionario, política, umbrales, sombra, snapshot calibrado) separados del oráculo (`platform/perception/profiles.yaml`: `jev-1.13`).
- [x] Cuestionario `rafaga-v1` como datos (`questionnaires/rafaga-v1.yaml`), idéntico al código anterior (lista congelada en `tests/fixtures/decisions/rafaga_v1_frozen.json`).
- [x] Fronteras por test (`test_decisions_boundaries.py`): el workflow importa solo `contracts`+`facade`; las tools solo `guards`; el motor no importa workflows y su núcleo no importa Temporal.
- Verificación: replay de 59 historias reales de producción (bajadas el 28-sep) sin divergir; fixture congelada `perception-v1` re-juega; batería completa.

### F1 · Contexto
- [x] Ventana de lo que vio el cliente desde el historial del vault (`decisions/context.py`: últimos 8 eventos o ~1.800 caracteres; ráfaga actual fuera por `wamid`; mensaje largo del bot cortado por el principio; tarjetas, botones y mensajes del equipo incluidos; cita del cliente).
- [x] Hechos del pedido (etapa, ítems, ciudad; dirección/teléfono/quien recibe/pago solo «dado»/«falta»; compra confirmada sí/no), anonimizados por el oráculo.
- [x] `wamid` por mensaje en la entrada de `perceive_burst` (solo payload, L-22) y en la señal del laboratorio (el sandbox corta igual que producción).
- [x] Cuestionario `rafaga-v2`: CONTEXTO / HECHOS DEL PEDIDO / ESTE TURNO separados; asuntos limitados a «este turno»; `thread.bot_asked` (7 opciones), `thread.answers_bot`, `thread.answer`; `bot_asked` no se pregunta si el código ya lo sabe (tarjeta de confirmación o formulario); sin la pregunta de etapa.
- [x] Política `turno-v2`: nota `[LECTURA DEL TURNO]` con la lectura del hilo + la lista de asuntos; evidencia de compra `reading.purchase` = `si` (pregunta de compra a la vista y sí ≥ 0,85) / `no` (≤ 0,20 de que se preguntó por la compra) / `duda`. Perfil `jev-v2` con umbrales por costo del error.
- [x] Sombra doble dentro de la misma activity (`shadow` en el perfil; en paralelo, tope propio de 1,5 s; solo traza).
- [x] Calibración atada al snapshot de Jev (`calibrated_model`): si Jev sirve con otra versión, sin nota ni reglas (`acting.allowed=false`), la verificación no pide complemento, y el control «Bot nuevo» no deja subir a canary/encendido (`same_model`).
- [x] La traza del turno guarda `versions`, `reading`, `shadow` y `acting` del motor.
- [ ] Banco de referencia: selección de ~150 turnos difíciles, CLI de etiquetas para Claude Code, métricas por pregunta (precisión, cobertura, calibración).
- [x] Sonda diaria: 20 ráfagas sintéticas con respuesta conocida (`decisions/probe.py`, las mismas preguntas del turno vía `engine.burst_request`; Schedule `decisions-probe-schedule` 07:00 Bogotá en `sales_eval`; reporte en `<vault>/_decisions/probe/`). El control «Bot nuevo» exige la última sonda `ok` y de 48 h o menos para subir a canary o encendido. Pendiente: casos de `jev-v3` cuando ese perfil vaya a actuar.

### F2 · Enchufes — sin cambio de comportamiento ✅
- [x] Registro de bots (`decisions/bots.py`): versión del workflow + proveedor por capacidad (`reglas`/`sombra`/`jev`) + perfil de Jev + modo de las capas ①②③. `bot_for_arm()` (laboratorio; el sandbox lo fija con `DECISIONS_BOT`) y `bot_for_session()` (producción: `_rollout/decisions.json` por capacidad y por versión del workflow, con los números de prueba y el porcentaje del control «Bot nuevo», dentro de los techos de Terraform `SALES_CAPABILITIES_CEILING` y `SALES_WORKFLOW_V2_CEILING`, `off` por defecto). Control ilegible = el bot de hoy.
- [x] Arranque unificado: `LoadOrStartSalesSession` arranca por NOMBRE la versión que dice el registro; la plataforma consulta un enrutador que el plugin registra (`platform/workflow_routing.py`, expuesto en `sdk.foundation`) en el dispatcher de orquestación y en `start_or_signal_sales_workflow_activity`; lo registran los workers de ventas y remarketing.
- [x] Tabla de sustituciones declaradas en el worker (`ACTIVITY_SUBSTITUTIONS`); la guarda L-3 valida cada entrada (mismo nombre, reemplazo uno a uno, sin duplicados).
- [x] Proveedor de lecturas en `IngestInboundMessage` (`readings=`; por defecto `EngineReadings`), que escribe los mismos campos con UNA función (`apply_readings`) que usan el ingest y el sandbox. Las tres reglas quedaron partidas en leer/escribir sin cambiar lo que hacen (`classify_inbound_purchase_signal`/`apply_inbound_purchase_signal`, `parse_reengagement_deferral`+`is_courtesy_text`/`apply_reengagement_deferral`, `has_recent_marketing_context`+`is_opt_out_text`).
- [x] El sandbox corre las lecturas del ingest con el bot del brazo, fijado para todo el caso; los brazos salen del registro de bots.
- [x] Marco de capacidades (`capabilities/__init__.py`: `decide()` con reglas / sombra / jev, respaldo, pisos, calibración) + cola de desacuerdos (`disagreements.py`) + CLI para Claude Code en producción (`scripts/decisions_queue.py`) + guardia para las tools (`guards.py`, sin Temporal).
- [x] Métricas por capacidad (`capability_rollout.py`): cada decisión con Jev suma caídas, latencia, versión servida y desacuerdos; la vara de encendido de cada capacidad (7 días en sombra, caídas < 1 %, p95 < 1,5 s, misma versión de Jev, desacuerdos calificados con Jev ganando) se calcula de ahí.

### F3 · Lecturas del cliente con Jev
- [x] compra (`capabilities/lecturas.py`): «¿Qué hace el cliente con la compra?» con lo que preguntó el asesor a la vista; confirma solo con la pregunta de compra visible (≥ 0,85) y retira un «sí» que respondía otra cosa (≤ 0,20). Carrito y botón los lee el código.
- [x] retoma: Jev decide SI hay aplazamiento (veta lo que la regla leyó o agrega un «yo les escribo» sin fecha); la fecha la calcula el código. Lo que escribe la visión ya no pasa por las lecturas (bug del comprobante que pausaba una semana): el reentry de visión le pasa a las lecturas solo el texto que el cliente puso en la foto.
- [x] baja: solo con promoción reciente (condición de hoy); la frase explícita es PISO; Jev solo agrega.
- [ ] cupón · fuera de catálogo · cantidad.
- [ ] mapeos (categoría, familia de color, ítem, zona de envío, producto nombrado).

### F4 · Workflow V2
- [ ] `HubaraSalesSessionWorkflowV2` sin reglas de texto; aplica veredictos grabados; guarda que falla si importa un detector de texto; brazos A1/B0/B; B0 = A1.

### F5 · Texto del LLM con Jev
- [x] persona (`capabilities/texto.py`), en las tools de cierre (`tags.py`) y de escalación (punto de extensión `customer_farewell` de la tool de plataforma): «¿Deja ver que quien atiende es un bot o una IA?» oración por oración, sobre las MISMAS oraciones del filtro de plataforma (`customer_sentences`, `keep_customer_safe_sentences(drop=…)`). Piso = autoidentificación y relevo a «una persona»/«un humano», siempre subconjunto de la regla de hoy. Jev puede dejar una frase de marca («Cada vela lleva un toque humano»).
- [x] enumeración, en la activity del selector de variantes: «¿Qué le enumera el texto al cliente?» {aromas, colores, combinaciones de cupón, productos, nada}; las etiquetas las sigue sacando el código del catálogo; las combinaciones «Color · Aroma» nunca van al selector (piso).
- [x] monto, en el checkout: «¿La oración cotiza el precio de un producto?» solo en las oraciones donde las palabras de política aceptan un monto que el catálogo no explica (`policy_decided_sentences`); esas pierden el contexto de política. La cuenta sigue en código; Jev solo puede hacer el chequeo más estricto.
- [x] selector, en los quick replies: «¿Estos botones le piden al cliente elegir un producto o una variante?»; el namespace del id (`product.`, `color.`…) es piso.
- [x] Las tools entran al motor SOLO por `guards` (`safe_customer_text`, `product_quote_sentences`, `catalog_choice_buttons`); el vault les llega por la raíz de composición (`build_vault_dir`).
- [ ] destinatario · rescate · saludo · portavelas: egress de V2; destinatario también en `send_reply`, el flush y el filtro de oraciones de las tools.
- [ ] verdad de producto (remarketing).

### F6 · Tools, datos y etapas
- [x] Perfil `jev-v3` = cuestionario `rafaga-v3` + política `turno-v3` (sobre v2). Las preguntas de etapa solo se hacen en su etapa (datos de envío, variantes, cierre, poscierre).
- [x] Contrato asunto → tool escrito en código; las tools requeridas viajan GRABADAS en `TurnDecisions.tools.required` con su nota. Excepciones: precio ya visto (sin tool), colores/aromas (selector solo en variantes; antes, la ficha), queja (humano solo si es de un pedido hecho), datos dados en el turno (`set_order_slot` con esos campos).
- [x] Guía de etapas: la etapa la calcula el código; la activity le pasa a la política lo que falta (borrador) y los turnos estancados (trazas del episodio; un dato nuevo corta la cuenta). Nota `[ETAPA]` con lo que dio, lo que falta y el siguiente paso; retroceso (`quitar=true`); refuerzo a los 3 turnos sin dato nuevo.
- [x] La traza guarda `tools` y `guide` del motor (el laboratorio mide el cumplimiento del contrato contra los pasos `tool`).
- [ ] Segunda puerta en `run_agent_turn` (ronda extra que nombra la tool que falta), solo con reglas grabadas.
- [ ] Auditoría antes de enviar (requeridas vs usadas; montos en cada turno) y pregunta de respaldo de Jev en sombra.
- [ ] Revisión de cada dato de `set_order_slot` (capacidad «datos»: bloquea solo con p ≤ 0,15, tope 2 s).

### F7 · V2 en producción (código de enrutamiento; el encendido es del operador)
- [x] Control por capacidad y por versión del workflow en el MISMO contrato `perception-rollout@v1` (solo crece): el GET trae `capabilities` (modo, techo, vara por modo) y `workflow_v2`; `PUT /perception/capabilities {capability, mode}` y `PUT /perception/workflow {mode}` (cast de Agents incluido). Subir exige la vara de la capacidad (`capability_rollout`); V2 va a canary antes de todos; todo dentro de los techos de Terraform; bajar siempre pasa.
- [x] Panel «Motor de decisiones» en Agents (ventas): cada capacidad Reglas → Sombra → Canary → Jev, con lo que falta para subir; «Workflow de ventas» V1 / V2 en canary / V2 para todos / Volver a V1; canary y encendido con confirmación en dos pasos.
- [ ] Encender (operador): `SALES_CAPABILITIES_CEILING` y `SALES_WORKFLOW_V2_CEILING` por Terraform; primero sombra, después números de prueba.

### F8 · Decisiones del agente y asuntos nuevos
- [ ] Remarketing decide antes de redactar · cierre por abandono · Order Sentinel · asuntos nuevos del cuestionario.

## 4. Vara para encender cada capacidad
- Laboratorio: el bot nuevo igual o mejor que A1 en el scorecard, sin checks que empeoren (los checks que usan detectores de producción los califica el juez).
- Banco de referencia: cada pregunta que actúa con precisión ≥ 0,95 y ≥ 30 positivos; Jev gana en los desacuerdos.
- Producción: 7 días en sombra, caídas < 1 %, p95 < 1,5 s, misma versión de Jev con la que se calibró.

## 5. Bitácora
- 2026-09-28: juez = Claude Code subido (`0b7edd25`), main al día, 4 tests con fecha fija arreglados.
- 2026-09-28: F0 (`b9c43cbf`), F1 (`6ae85402`), F2 + lecturas de F3 (`5f7b7ffb`), métricas por capacidad (`5c0afbaf`), F6 motor (contrato de tools + guía de etapas). Replay de 59 historias reales de producción verde en cada paso.
