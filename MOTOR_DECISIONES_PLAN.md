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
| Cola de desacuerdos y métricas por decisión (UNA para todo el sistema: ventas y el Order Sentinel) | `hubara_agency/src/platform/perception/{disagreements,metrics}.py` (vía `src.sdk.connectorkit`); datos en `<vault>/_decisions/` |
| Lector de Jev del Order Sentinel | `hubara_agency/src/plugins/order_sentinel/agent/cycle/use_cases/readings.py` + `GraphAgents/graphs/order_sentinel.py` (`_plan`) |
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
- [x] Banco de referencia (`sales_lab/reference_bank.py`, `95ec4726` + `d6028e28`): sorteo determinista de ~150 turnos difíciles con cuotas por categoría, el MISMO state y las mismas preguntas que recibe Jev (test carácter por carácter), CLI de etiquetas para Claude Code (`scripts/lab_bench_labels.py`) y métricas por pregunta (precisión, cobertura, calibración; puede actuar con precisión ≥ 0,95 y ≥ 30 positivos). Pendiente del operador: correr `preparar` + etiquetar sobre un banco real.
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
- [x] cupón · fuera de catálogo · cantidad (`capabilities/lecturas_pedido.py`): cupón y fuera de catálogo en el ingest (`read_coupon_talk`, `read_catalog_gap`); la cantidad en la activity del prompt. Fuera de catálogo: Jev solo puede QUITAR términos (el piso es la lista de la regla).
- [x] mapeos (`capabilities/mapeos.py`), choice sobre la lista cerrada más «ambiguo»/«ninguno», valor validado contra la lista: categoría (`search_products`), familia de color e ítem del pedido (`set_order_slot`), zona de envío (`present_order_confirmation`, `register_order`).
- [x] producto nombrado (remarketing: qué ficha ve el gancho; Jev SUMA el producto nombrado con otras palabras, los exactos se quedan) y fuera de catálogo en el contexto de remarketing (la misma capacidad del ingest: Jev solo quita), por el enchufe `decide_catalog_context` de `chats/shared/agent_decisions` que conecta el worker de remarketing.
- [x] acuse (2026-09-29): «¿Este mensaje es solo un acuse o una cortesía a la despedida, sin pedir ni contar nada nuevo?» en el ingest (enchufe de lecturas, `EngineReadings.read_ack`); absorbe solo con p ≥ 0,90, un «?» nunca se absorbe y con duda el bot despierta; `is_closing_ack` queda de respaldo. Llegó de main (#379) después del inventario: regla 4.
- [ ] verdad de producto (remarketing): hoy la decide el workflow de remarketing (`invented_product_claim`); por el diseño, lo que decide un workflow pasa al motor solo en un workflow nuevo (un V2 de remarketing).
- [ ] ANTES de encender `zona_de_envio`: la API (`shipping_rate_for_city`) cobra la nacional a toda ciudad que no sea Bogotá; la tool acepta las dos tarifas fuera de Bogotá. Con Jev, Chía pagaría la de Bogotá en el bot y la nacional en la API. Decisión del operador: cuál es la correcta.
- [ ] El laboratorio no ejercita cupón ni fuera de catálogo (`sandbox/turn.py::turn_context` arma la nota del cupón como en juego y nunca la de fuera de catálogo); los veredictos de F3 van a la cola y a los logs, no a la traza del turno.

### F4 · Workflow V2
- [x] `HubaraSalesSessionWorkflowV2` (`workflows/sales_session_v2.py`) sin reglas de texto (guarda AST: falla si importa un detector de texto o usa `patched`); el egreso lo decide la activity `decide_egress` ANTES de `record_turn` (hook `egress=` de `run_agent_turn`; con `None`, V1/remarketing/ETA byte a byte); registrado junto a V1 (V1 primero en el manifiesto). Brazos A1 (V1, reglas) · B0 (V2, reglas: tiene que dar lo mismo que A1) · B (V2 con Jev). Paridad: las suites de V1 corren contra V2 (3 exclusiones escritas, todas por la diferencia 3), equivalencia de 112 combinaciones del egreso, corpus V1 contra V2 y sandbox B0 contra A1.
- [x] Diferencias intencionales de V2: el panel muestra solo lo que salió; el texto se suprime por el selector solo si el selector salió de verdad; sin la ronda extra ② por palabras; el LLM recuerda el texto que salió.
- [ ] Congelar historias reales de V2 antes de encenderlo (desde ahí, cada cambio de V2 lleva `workflow.patched`).

### F5 · Texto del LLM con Jev
- [x] persona (`capabilities/texto.py`), en las tools de cierre (`tags.py`) y de escalación (punto de extensión `customer_farewell` de la tool de plataforma): «¿Deja ver que quien atiende es un bot o una IA?» oración por oración, sobre las MISMAS oraciones del filtro de plataforma (`customer_sentences`, `keep_customer_safe_sentences(drop=…)`). Piso = autoidentificación y relevo a «una persona»/«un humano», siempre subconjunto de la regla de hoy. Jev puede dejar una frase de marca («Cada vela lleva un toque humano»).
- [x] enumeración, en la activity del selector de variantes: «¿Qué le enumera el texto al cliente?» {aromas, colores, combinaciones de cupón, productos, nada}; las etiquetas las sigue sacando el código del catálogo; las combinaciones «Color · Aroma» nunca van al selector (piso).
- [x] monto, en el checkout: «¿La oración cotiza el precio de un producto?» solo en las oraciones donde las palabras de política aceptan un monto que el catálogo no explica (`policy_decided_sentences`); esas pierden el contexto de política. La cuenta sigue en código; Jev solo puede hacer el chequeo más estricto.
- [x] selector, en los quick replies: «¿Estos botones le piden al cliente elegir un producto o una variante?»; el namespace del id (`product.`, `color.`…) es piso.
- [x] Las tools entran al motor SOLO por `guards` (`safe_customer_text`, `product_quote_sentences`, `catalog_choice_buttons`); el vault les llega por la raíz de composición (`build_vault_dir`).
- [x] «Resumen interno en una plantilla»: el `motivo` (prosa del LLM al etiquetar) que el watchdog de remarketing manda como variable de plantilla pasa por `destinatario` (variante de plantilla: hoy no se revisa); si no es para el cliente, va el genérico. Enchufe `label_is_internal` de `chats/shared`.
- [x] destinatario · rescate · saludo · portavelas en el egreso de V2 (`decisions/egress.py`): cada una con la regla de V1 de respaldo, métricas para su vara y redacción de los datos del cliente.
- [x] destinatario también en las tools, con las MISMAS capacidades del egreso: `send_reply` (destinatario extendido + rescate por párrafo), el flush de los intents (destinatario básico; `_sanitize_intent_client_text` recibe la sesión) y el filtro de oraciones de cierre y escalación (destinatario por oración, en paralelo con persona). Caso: «Usa el código VELAS_10 al pagar» deja de rechazarse con Jev.
- [x] preámbulo (2026-09-29): la muletilla de presentación del modelo («Aquí tienes:»), pieza de la familia C que el inventario no había asignado. Hasta 3 oraciones iniciales, nunca la última; corta con p ≥ 0,85; la regla es el paso 2 del saneador. En V2 la decide el egreso (paso 0, `EgressInput.raw_text`); en las tools, `guards.clean_llm_text`.
- [x] envío (2026-09-29): el sitio «envío» del detector de fugas (diseño §07, `destinatario`: «reemplaza el detector de fugas en sus 8 sitios») no se había migrado: tiraba en silencio textos que Jev aprobó. V2 manda `decided_by_engine=True` (3.er argumento de `send_whatsapp_message_activity`); V1, remarketing y ETA siguen con el detector. La guarda de V2 se amplió (10 detectores más, imports relativos, `egress=` obligatorio, helpers del V1 recorridos).
- [ ] verdad de producto (remarketing): ver F3 (requiere un V2 de remarketing).

### F6 · Tools, datos y etapas
- [x] Perfil `jev-v3` = cuestionario `rafaga-v3` + política `turno-v3` (sobre v2). Las preguntas de etapa solo se hacen en su etapa (datos de envío, variantes, cierre, poscierre).
- [x] Contrato asunto → tool escrito en código; las tools requeridas viajan GRABADAS en `TurnDecisions.tools.required` con su nota. Excepciones: precio ya visto (sin tool), colores/aromas (selector solo en variantes; antes, la ficha), queja (humano solo si es de un pedido hecho), datos dados en el turno (`set_order_slot` con esos campos).
- [x] Guía de etapas: la etapa la calcula el código; la activity le pasa a la política lo que falta (borrador) y los turnos estancados (trazas del episodio; un dato nuevo corta la cuenta). Nota `[ETAPA]` con lo que dio, lo que falta y el siguiente paso; retroceso (`quitar=true`); refuerzo a los 3 turnos sin dato nuevo.
- [x] La traza guarda `tools` y `guide` del motor (el laboratorio mide el cumplimiento del contrato contra los pasos `tool`).
- [x] Segunda puerta (workflow V2): `TurnPolicy.final_round_note` en `run_agent_turn`; si el LLM va a cerrar con texto sin la tool que el contrato GRABADO pedía y el cliente todavía no vio nada, UNA ronda más con la nota (el borrador no se graba ni se recuerda). V2 la arma con `facade.contract_policy_of(decided)`; sin contrato grabado, el turno de hoy. V1, remarketing y ETA no la pasan: replay de historias reales ventas 59/59, remarketing 89/89, ETA 15/15.
- [x] Auditoría del turno: las tools que pedía el contrato contra las usadas quedan en la traza (`contract`: required, missing, ok) = métrica «cumplimiento del contrato». La pregunta de respaldo sobre afirmaciones sin consultar (capacidad `afirmacion`: stock, entrega, estado del pedido) la hace la activity de la traza después de enviar y arranca en sombra (nunca actúa). Los montos por turno ya los mide el check del scorecard con `find_unexplained_amounts`.
- [x] Revisión de cada dato de `set_order_slot` (capacidad `datos`, `capabilities/datos.py`): antes de guardar un dato de envío o de pago, «¿el cliente lo dio en la conversación?»; bloquea solo con p ≤ 0,15 (tope 2 s) y el LLM recibe «pídeselo en vez de suponerlo». Con `reglas` no lee ni el historial.

### F7 · V2 en producción (código de enrutamiento; el encendido es del operador)
- [x] Control por capacidad y por versión del workflow en el MISMO contrato `perception-rollout@v1` (solo crece): el GET trae `capabilities` (modo, techo, vara por modo) y `workflow_v2`; `PUT /perception/capabilities {capability, mode}` y `PUT /perception/workflow {mode}` (cast de Agents incluido). Subir exige la vara de la capacidad (`capability_rollout`); V2 va a canary antes de todos; todo dentro de los techos de Terraform; bajar siempre pasa.
- [x] Panel «Motor de decisiones» en Agents (ventas): cada capacidad Reglas → Sombra → Canary → Jev, con lo que falta para subir; «Workflow de ventas» V1 / V2 en canary / V2 para todos / Volver a V1; canary y encendido con confirmación en dos pasos.
- [ ] Encender (operador): `SALES_CAPABILITIES_CEILING` y `SALES_WORKFLOW_V2_CEILING` por Terraform; primero sombra, después números de prueba.

### F8 · Decisiones del agente y asuntos nuevos
- [x] contactar (remarketing): «¿Sobra un mensaje proactivo ahora?» lo pregunta la activity que lee el contexto del gancho, ANTES de redactar (`skip_touch` en el resultado grabado). Si sobra, el workflow toma el camino de la abstención (consume el peldaño, devuelve el routing, termina) sin turno del LLM ni «escribiendo». Remarketing no importa el motor (contrato `agents-independent`): el worker le conecta el decisor por el enchufe `chats/shared/agent_decisions`. Nace en `reglas` = decide el LLM como hoy. Replay de 89 historias reales de remarketing: 89/89.
- [x] cierre por abandono (workflow V2): el aviso de ghosting recibe la sesión; la capacidad `cierre` decide la etiqueta {confirmado sin datos, interesado, rechazo, compra exitosa} y el aviso le dice al LLM cuál usar (solo ejecuta las tools: la mecánica del cierre queda igual). Invariantes del código: pedido registrado = COMPRA_EXITOSA; CONFIRMADO_SIN_DATOS sin confirmación = INTERESADO. V1 llama la activity sin sesión: el aviso de hoy.
- [x] Order Sentinel · estado del pedido (capacidad `estado_pedido`). Jev percibe en hubara (la activity del snapshot; el agente de GraphAgents sigue sin pegar red) y el código decide en el nodo puro `_plan` de GraphAgents con las MISMAS guardas para las dos fuentes. Dos preguntas: choice «¿qué cambió que el sistema todavía no sabe?» {nada, preparación, listo, en camino, entregado, pago} y, solo si Jev está seguro (≥ 0,85) de que algo cambió, un sí/no por cada mensaje NUEVO desde el watermark que podría probarlo (equipo o cliente; el pago, solo el equipo; a lo sumo 16). El veredicto de Jev tiene la forma del veredicto del LLM, con la cita textual. El LLM se sigue llamando siempre (es la regla de hoy): `shadow` = actúa el LLM y se compara lo que cada uno haría despachar; `on` = actúa Jev cuando tiene veredicto, si duda el LLM. Los desacuerdos van a la cola única (la cola y las métricas pasaron a la plataforma, `src.sdk.connectorkit`) por una activity que el workflow agenda SOLO si el result grabado trae desacuerdos (replay de las 30 historias reales del ciclo: 30/30). Interruptor `ORDER_SENTINEL_READER` (off | shadow | on) en Terraform (`lab-config`), nace `off`. Dato real que fijó la evidencia en lo nuevo: el único despacho de 30 días tenía su prueba 12 candidatos atrás del final de la ventana.
- [x] Asuntos nuevos del cuestionario: `promocion` («promoción · cupón», el único asunto nuevo del diseño) ya está en `rafaga-v3` (perfil `jev-v3`); queja y estado del pedido existen desde v1. Los asuntos sin tool (tiempos, pagos, personalización, aplazamiento, saludo) se siguen revisando por texto con la verificación ③.
- [ ] Encender el lector del Order Sentinel (operador): `tenants.<t>.lab.order_sentinel_reader = "shadow"` en `tenants.auto.tfvars` + apply + re-render del `.env` en la caja; 7 ciclos en sombra y los desacuerdos calificados antes de `on`.
- [ ] «¿Hay un mensaje que responder tras un relevo?» (inventario §06 del diseño, `workflow_helpers.py`: hoy el LLM decide con el prompt y `NO_MESSAGE`): el diseño no le asignó capacidad en §07 ni en F8; queda para la próxima fase.

## 4. Vara para encender cada capacidad
- Laboratorio: el bot nuevo igual o mejor que A1 en el scorecard, sin checks que empeoren (los checks que usan detectores de producción los califica el juez).
- Banco de referencia: cada pregunta que actúa con precisión ≥ 0,95 y ≥ 30 positivos; Jev gana en los desacuerdos.
- Producción: 7 días en sombra, caídas < 1 %, p95 < 1,5 s, misma versión de Jev con la que se calibró.

## 5. Bitácora
- 2026-09-28: juez = Claude Code subido (`0b7edd25`), main al día, 4 tests con fecha fija arreglados.
- 2026-09-28: F0 (`b9c43cbf`), F1 (`6ae85402`), F2 + lecturas de F3 (`5f7b7ffb`), métricas por capacidad (`5c0afbaf`), F6 motor (contrato de tools + guía de etapas). Replay de 59 historias reales de producción verde en cada paso.
- 2026-09-28: F5 destinatario en las tools, F6 revisión de datos, F8 cierre por abandono, producto nombrado en remarketing, etiqueta del watchdog y F8 Order Sentinel. Replay de historias reales: ventas 59/59, remarketing 89/89, ETA 15/15, Order Sentinel 30/30.
- 2026-09-29: revisión tras simular el caso real de las fotos (ver §6).

## 6. Revisión del 2026-09-29

Pedido del operador: simular el caso real en código de producción y en el bot nuevo del laboratorio, y revisar que el bot nuevo no tenga reglas quemadas y esté inyectado en los workers. Hallazgos y arreglos (rama `lab/todo`):
- **El laboratorio no medía el bot nuevo.** El brazo B corría `jev-v1` (motor de F0). Ahora `jev-v3`, el mismo perfil por defecto de producción (Terraform incluido, con guarda). El sandbox además: deja la ráfaga en el historial antes del turno (sin eso `datos` e `item_del_pedido` le preguntaban a Jev sin el dato recién dado), decide cupón y fuera de catálogo con el motor, les da a las lecturas solo lo que escribió el cliente (texto de la foto, botón, carrito), publica con cada turno las decisiones del motor y la cola de desacuerdos, y cuenta las caídas de Jev a la regla y la segunda puerta de V2 (`contract_extra_round`). El modal del hilo muestra esas decisiones.
- **Reglas quemadas que quedaban en V2:** el filtro del envío, el acuse tras la despedida y el preámbulo del modelo. Resueltas arriba (F3 y F5).
- **Pendiente:**
  - «¿Hay que responder tras un relevo?» (sin capacidad, lo decide el LLM por prompt).
  - Verdad de producto en remarketing (requiere un V2 de remarketing).
  - Los textos de `cta_url` (`body_text`, `button_text`) salen sin ningún chequeo en V1 y V2; cubrirlos cambia lo que ve un cliente de V1: decide el operador.
  - `calibrated_model: null` en los tres perfiles: la guarda de calibración no actúa hasta calibrar con el banco de referencia.
  - Historias reales de V2 congeladas y casos de `jev-v3` en la sonda diaria.
  - PDF = comprobante y comprobante leído por la visión quedan como invariantes: Jev no ve archivos ni imágenes.
  - Partición léxica «rojo y azul»: el diseño la dejó en código, pero el caso «una lila y otra azul» registró 2× lila (ver la foto → producto y los pedidos multi-variante como propuestas aparte).
  - En el laboratorio local, 2 de 6 turnos de B cayeron a la regla por el tope de 3 s de Jev (latencia medida aparte: p50 0,55 s, máx. 1,06 s): vigilar la tasa de caídas en cada corrida.

### 6.1 Primeros arreglos tras la UI legible (2026-09-29, tarde)

Con la UI nueva el operador vio tres cosas en el turno 1 de la corrida `caso-fotos-0929-r2`. Causas y arreglos:
- **«El bot con Jev no muestra el catálogo de religiosas ni el de Halloween» (6543, 4567).** Sí los mostró; la burbuja decía solo «Lista de productos». La causa de fondo era otra: con `rafaga-v3`, «¿tienen religiosas?» y «la colección de Halloween» (con el anuncio adelante) le dieron a «catálogo» 0,11–0,32, y el plan quedó solo con «saludo». Nada obligaba a mostrar el catálogo (el contrato asunto → tool no se activaba), y en 4329 el bot mandó la lista de Halloween como texto. **`jev-v4`** = `jev-v3` con `rafaga-v4`: catálogo y disponibilidad con descripciones más amplias. Con Jev real, catálogo 0,97 y disponibilidad 0,94, sin temas falsos en los controles. Es el perfil del bot nuevo (laboratorio, producción por defecto y Terraform).
- **«En 4329 no saluda».** Pasó también en producción: el LLM saludó junto a `search_products` (descartado) y cerró con un texto sin saludo. La garantía de saludo solo miraba tools que le escriben al cliente. Ahora cubre los turnos de texto (V1 detrás de `first-contact-greeting-text-turns`; un traspaso de remarketing no saluda). El bot nuevo decide el saludo con lo que de verdad sale. Antes saludaba después, en el complemento, que el laboratorio no mostraba; ahora sí lo muestra.
- **La espera de Jev** (decisión del operador: «es indispensable que siempre funcione con Jev»). Antes: 1,5 s o 2 s por capacidad y 3 s en el turno; el 11,6 % de las preguntas caía a la regla por tiempo. Ahora hay una sola espera de 10 s (`jev-1.13.timeout_s`), un reintento ante fallas pasajeras (no ante un `timeout`) y las activities con lugar para esa espera (percepción y verificación 30 s, egreso 90 s). **Pendiente del operador:** la vara de encendido por capacidad (§ métricas: p95 < 1,5 s) quedó más corta que la espera; revisarla con la latencia medida en la caja de AWS.

### 6.2 Segunda vuelta del turno 1 (2026-09-29, noche)

Corrida `caso-fotos-0929-r4` (A1 y B, mismo banco) contra r3:
- **4567: «Buenas tardes 🤍» después del «Buenos días» de la lista (r3).** El turno 1 fue solo la lista de Halloween, con el saludo y la marca en su texto: lo único que el cliente lee con el menú. La verificación (③) solo leía los textos sueltos: vio «(sin texto)», dio el saludo por faltante y agendó un complemento. APE-01 tomó ese complemento como el primer texto y falló «sin marca». La misma ceguera explicaba 3 de los 4 complementos de r3, todos en turnos de solo lista (6543 t1 y 4329 t9: «Sí, todas las que te mostré están disponibles»). **Arreglo:** `sales/card_texts.py` dice qué texto lee el cliente en cada tarjeta. La verificación y el scorecard lo leen en el orden en que llega: texto, tarjetas, complemento. DES-10 (precios) usa la misma lista; nunca había leído el texto de la ficha (`caption_suffix`) ni el del botón con enlace. En r4 la verificación dijo «enviar» en el turno 1 de las tres conversaciones, y los complementos bajaron de 4 a 2 (los dos por variantes, en 4567).
- **La hora del complemento.** Dijo «tardes» a las 08:55: los turnos de sistema no llevan el bloque «Hora actual en Colombia», y el LLM solo ve la hora del contenedor, en UTC (13:55). Pasa con el complemento y, en el bot actual, con los traspasos y los fantasmas. Queda como tarea aparte.
- **Jev colgado.** Con 10 s igual caían 24 de 322 llamadas: no eran lentas, se colgaban. **Reenvío:** gana la primera respuesta válida, dentro de la misma espera. Con reenvíos a los 2 s y 5 s, r4 bajó a 1 de 330. Con 1,5, 3, 5 y 7,5 s, 0 de 200 en el experimento intercalado; el esquema anterior perdió 1 de 200.
- **Sigue en el turno 1:** EST-06 (menor) en las tres conversaciones y en los dos brazos. El LLM escribe el saludo junto a la búsqueda y ese texto se descarta; el cliente igual recibe el saludo por la garantía de saludo.

### 6.3 Turno de la foto en 6543 (2026-09-29, noche)

El turno 2 del 6543 en r4 (bot nuevo): el cliente mandó una captura de nuestro catálogo («Sacrificio de Amor», con nombre y precio a la vista) y preguntó «tienes esta?». El bot respondió «esa pieza como tal no la manejamos» sin buscar. Aun así el scorecard marcó PASA, porque las revisiones del juez seguían pendientes (en r2 ese turno fue ALERTA). Causas, en orden:
1. **Visión:** describió la forma («vela gris… rostro de Jesús») e ignoró el título. Ver «Fotos de nuestro catálogo» abajo.
2. **Nota «fuera de catálogo»:** la regla es literal («jesús» no está escrito en el catálogo) y la nota *ordenaba* decir «eso no lo manejamos». **Arreglo:** la nota dice «no aparece por nombre», pide buscar con `search_products` (por nombre y por lo que describe) antes de responder, y solo si no aparece nada parecido se niega. Mismo cambio en SOUL.md y en la spec de ventas.
3. **Contrato de herramientas:** el plan pedía consultar el catálogo, pero la segunda puerta solo actuaba si el modelo cerraba con texto suelto, no con `send_reply` (12 de 43 turnos en r4). **Arreglo:** la puerta también actúa en `send_reply`. Retiene la respuesta, le avisa al modelo que no salió y da una ronda más. No actúa si el cliente ya tiene algo delante (tarjeta o formulario) ni si el turno cierra por etiqueta. Lo retenido no queda en el historial ni cuenta como texto enviado. Aparte: «medidas» acepta `search_products`, que ya trae las medidas.
4. **Hilo del turno:** la puerta y las rondas extra se nombran en palabras, y las cajas de los pasos internos crecen con su texto.

**Fotos de nuestro catálogo (investigación, sin implementar):**
- El modelo de visión actual (`gemini-2.5-flash-lite`) sí lee texto. Pidiendo cada dato en su campo (JSON: nombre, precio, URL, SKU) leyó bien las 5 capturas con texto, 15 de 15, sin inventar.
- Hace falta **comparar con el catálogo en código:** SKU → URL `/products/<handle>` → nombre exacto → nombre parecido con umbral alto. Probado con nombres falsos («Luz Eterna» no pasa como Luz Serena).
- Sin texto: 5 candidatos por similitud de imagen (embeddings) y un verificador (`gemini-3.1-flash-lite`) que elige uno o «ninguno». Acertó 74 de 74 en dos corridas, con casos trampa, a ~US$0,0008 por foto y ~2,5 s. Hace falta un tiempo límite.
- **WhatsApp:** `context.referred_product` trae el SKU exacto cuando el cliente escribe desde el catálogo; hoy se guarda y no se usa.
- **Nunca negar un producto por una foto:** si la identificación es probable, se pregunta «¿es esta?» mostrando la ficha.
- El laboratorio no vuelve a llamar a la visión: para medir el cambio hay que llevar las fotos al banco.
- Artefactos: scratchpad de la sesión fbec0352, `photo_research/`.

**Resultado en el laboratorio (r5 y r6, 2026-09-29, noche):**
- **Foto del 6543:** los dos bots ya no niegan sin buscar; buscan y muestran el más parecido. El producto sigue siendo el equivocado (Sagrado Rostro en vez de Sacrificio de Amor): eso lo resuelve la identificación de fotos, no la nota.
- **Turnos con varias fotos:** muestran candidatos en vez de negar. Siguen negando la «figura femenina con vasija» (Luz Serena): la búsqueda por frases enteras no la encuentra.
- **La puerta en `send_reply`** bajó los turnos que salen sin cumplir el contrato de 14 a 6–7 de 43. Casos buenos: medidas y estado del pedido consultados en vez de dichos de memoria.
- **Una regresión, ya corregida:** «¿cuánto se demora el envío?» salía solo con la tarjeta de tarifas. `jev-v5` = `jev-v4` con `rafaga-v5`, que suma la pregunta `envio.costo`. La tarjeta se exige solo si preguntan el costo. En r6 el cliente recibe «A Medellín llega en 2 a 3 días hábiles…».
- **r6, bot nuevo:** 2 PASA y 1 ALERTA, con menos fallas que r4. Jev con 0 caídas por tiempo gracias a los reenvíos [1,5, 3, 5, 7,5].
