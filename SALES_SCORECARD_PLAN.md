# Plan — Scorecard por etapa para el Asesor de Ventas

> Análisis + diseño. NO es código aún. Hermano de `LLM_EVAL_HARNESS_PLAN.md` (3 superficies
> sobre SigNoz + DeepEval, 2026-06-03) y `GOLDEN_EVAL_LOOP_PLAN.md` (loop cerrado con goldens).
> Este doc **reemplaza el puntaje holístico** por un checklist binario por etapa con auto-fail.
> Fecha: 2026-09-14. Disparador: PR #281 (runs 01a0a0eb / 01a0a0f1) calificado 0.93 por el
> evaluador actual. Autor: agente (análisis + investigación) + operador (decisiones).

---

## 0. TL;DR

- **El 0.93 no es un bug del juez, es el diseño**: promedio simple de 9 métricas (2 regex que
  casi siempre dan 1.0 + 7 G-Eval que puntúan 8-9/10 salvo desastre), sin pesos, sin criterio
  crítico, y con instrucciones explícitas al juez de **dar por buenos los "hechos del sistema"**
  (etiqueta + escalación) que en este caso eran justamente los errores. El juez además no ve
  `ui_intents`, ni outputs de tools, ni turnos solo-tool, ni la etapa del funnel. Con esos
  insumos y esas reglas, 0.93 era la respuesta correcta a la pregunta equivocada.
- **Propuesta**: un **Scorecard por etapa** — ~50 checks binarios (`pasa / falla / no aplica /
  desconocido`) organizados por etapa del funnel, cada uno con **nivel** (`crítico` = auto-fail,
  `mayor`, `menor`), **tipo** (`código` sobre la trayectoria, o `juez` aislado por check con
  crítica + evidencia), **condición de aplicabilidad** (solo se evalúa si la conversación llegó a
  esa etapa / ocurrió el disparador) y **origen** (incidente, Requirement del spec o regla del
  guion). Veredicto = `FALLA` si cae un crítico; `ALERTA` si cae un mayor; `PASA` si no.
  Nunca un promedio.
- **Habilitador técnico #1**: persistir una **traza por turno** (`turn_trace`) desde el workflow —
  etapa de entrada/salida, tools con ok/rechazo, intents encolados, guardas disparadas, señales
  del cliente, texto enviado/suprimido. Hoy nada de eso llega al evaluador (gotcha #1 del
  CLAUDE.md: verificar que el backend EMITE, no que el schema lo permite).
- **Visual**: (1) *tira de trayectoria* por conversación (secuencia con bandas de color por
  etapa y los checks anclados al evento que juzgan, con el "primer fallo" marcado), (2) *matriz
  de cumplimiento* conversaciones × checks, (3) *Pareto de fallos*, (4) *tasa de cumplimiento
  por check en el tiempo* (reemplaza la tendencia del promedio), (5) *embudo de etapa terminal*,
  (6) *matriz de confusión de etiquetas*, (7) *calibración juez vs humano*.
- **Estado del arte 2025-2026** (ver §7): checklists binarios con crítica en vez de Likert
  (CheckEval, TICK, Rubrics-as-Rewards, Anthropic "Demystifying evals"), verificación de estado
  y trayectoria (τ-bench, agentevals superset/subset), auto-fail de contact-center QA, y
  calibración del juez contra etiquetas humanas (TPR/TNR, kappa; EvalGen "criteria drift").
  El sistema propuesto es exactamente ese stack, montado sobre lo que ya tenemos.
- Con el scorecard, la conversación del PR #281 da **FALLA** con 5 críticos, 4 mayores y el
  primer fallo en el turno 4 (§5). Cada incidente pasado de la memoria queda como un check con
  id, y cada check nuevo nace de un incidente (loop de análisis de errores, §6).

---

## 1. Diagnóstico: por qué 0.93

### 1.1 Lo que hace hoy el evaluador (`sales_eval/`)

| Pieza | Hoy |
|---|---|
| Disparo | `EpisodeClosedEvent` → `EvaluateEpisodeWorkflow` (event-driven, path vivo). Cron 08/14/20 apagado. |
| Insumo | JSONL del dashboard (`<vault>/wa_x/sessions/wa_x.jsonl`): turnos `user`/`assistant` con texto + **nombres** de tools. Corta en el primer mensaje del humano. Scenario = "HECHOS VERIFICADOS DEL SISTEMA" (closing_tag, order_id, escalación) + catálogo snapshot actual. |
| Métricas | 2 deterministas (`greeting_compliance`, `style_compliance`) + 7 juez (`script_adherence`, `proactive_offering`, `no_hallucination`, `conversion_progress`, `correct_handoff`, `role_adherence`, `knowledge_retention`), cada una un entero 0-10 de Gemini ÷ 10. |
| Agregación | `avg = Σ score / n`. Sin pesos. Métrica que lanza excepción → **se omite** y `n` baja. |
| Alerta | `avg < 0.7` o falla `no_hallucination`. `passed` (todas sobre umbral) existe pero NO flaggea. |
| UI | Lista por episodio con `last_avg` (verde ≥ 0.7), chips `métrica: score`, razones solo de las falladas, tendencia = promedio diario por métrica. |

### 1.2 Las seis causas del 0.93

1. **Promedio sin criterio crítico.** 7 de 9 en 1.0 y dos en 0.7 → 0.93. Un fallo catastrófico
   único no puede bajar el promedio de 0.7. En QA de contact-center esto se resuelve con
   *auto-fail*: un ítem crítico reprueba la interacción entera.
2. **Ground truth circular.** `CORRECT_HANDOFF_STEPS[2]`: *"Si el Scenario trae HECHOS VERIFICADOS
   DEL SISTEMA que registran la escalación, la etiqueta o la red de seguridad, DA LA ESCALACIÓN
   POR OCURRIDA"*. `SCRIPT_ADHERENCE_STEPS[5]`: *"si los HECHOS VERIFICADOS registran la
   orden/etiqueta/escalación, considera esa parte CUMPLIDA"*. Los "hechos" eran
   `CONFIRMADO_SIN_DATOS` + `ORDER_PENDING_SHIPPING_DETAILS`, ambos puestos por el mismo LLM que se
   está evaluando, y ambos falsos. **El estado del sistema no es verdad; es la salida del agente.**
   La verdad está en las señales del cliente (dijo sí / tocó Confirmar / aplazó).
3. **El juez está ciego a la trayectoria.** No ve `ui_intents` (el Flow de envío viajó como
   `ui_component` y el juez solo ve el nombre `request_shipping_details` en `tools_used`), no ve
   outputs de tools (no puede saber que `search_products("café")` devolvió 23/24 productos), no ve
   turnos solo-tool (cierre, ghosting), no ve texto descartado por default-deny ni suprimido por
   guardas, no ve `order_draft` ni `purchase_signals`, no ve la **etapa** en que estaba cada turno.
4. **Criterios holísticos y sin etapa.** Cada G-Eval empaqueta 3-6 "Verifica si…" y devuelve UN
   número. La literatura (CheckEval, TICK, "Coin Flip Judge") muestra que eso produce scores
   ruidosos y sesgados al 8-9. Nada es condicional a "en qué momento quedó" la conversación.
5. **El evaluador no aprendió de los incidentes.** Cada incidente de la memoria
   (`quantity_compound_reply_capture`, `quick_replies_catalog_selector_guard`,
   `first_contact_greeting_dropped`, `remarketing_deliberation_leak`, `ghost_close_leak`,
   `premature_shipping_form_false_confirmado`…) produjo una **guarda de runtime** pero ningún
   **check de evaluación**. El eval mide el guion de junio; el harness de septiembre tiene 40
   mecánicas que el eval no conoce.
6. **Omisión silenciosa.** Si el alias `gemini-pro-judge` falla o una métrica explota, el promedio
   se calcula sobre las que quedaron (hasta ≈1.0 con solo las 2 regex). `metrics_evaluated` lo
   delata pero la UI no lo muestra.

### 1.3 Qué SÍ sirve y se conserva

- El **disparo por episodio** (`EvaluateEpisodeWorkflow`), el worker propio, la persistencia en
  `<vault>/_evals/history/<date>.jsonl`, la emisión a SigNoz, el cast `agents_admin → chats`, la
  pestaña "Calidad LLM", el golden runner con `expected_behaviors` (ledger determinista), la
  curación de candidatos y el juez vía litellm. Todo eso queda; cambia **qué** se calcula y
  **qué** se muestra.
- `script_rubric.py` (regex de saludo, voseo, em dash, emoji, cierres prohibidos) pasa a ser la
  implementación de los checks `APE-*`/`EST-*`.
- El vocabulario `expected_behavior_vocab` del golden set se **unifica** con los ids de checks (§4.4).

---

## 2. El harness de venta, visto como lo que hay que auditar

Mapa condensado (detalle en `hubara_agency/.hubara/specs/agents/sales-worker/spec.md`, en
`workspace/skills/etapa_*/SKILL.md` y en el código vivo).

### 2.1 Etapas (deterministas, proyectadas del `order_draft`)

| Etapa | Condición (`resolve_funnel_stage`) | Guion inyectado |
|---|---|---|
| `descubrimiento` | sin producto en el draft | saludo + botón catálogo; máx 2 preguntas antes de mostrar; diseño antes que aroma |
| `variantes` | producto sí; falta aroma/color/cantidad | UNA variante por mensaje; 4+ opciones → `present_variant_picker`; cada elección → `set_order_slot` |
| `confirmación` *(implícita)* | variantes completas, sin `confirmed_at_ms` | pedir el sí (precio dicho + "¿lo confirmamos?"); `request_shipping_details` se rechaza sin confirmación |
| `datos_envio` | confirmado; faltan ciudad/dirección/teléfono/nombre/método | Flow UNA vez; pedir SOLO lo que falta; nunca datos bancarios ni envío definitivo |
| `cierre` | slots completos | `verify_order_for_checkout` → `present_order_confirmation` → botón → `register_order` → tag + escalación + UN mensaje |
| `postcierre` | `episode.order_id` | pago solo con `pay_status=paid`; cambios → escalar |
| *terminales laterales* | — | `INTERESADO` (dormido), `RECHAZO`, `HUMANO(reason)`, `TIMEOUT` |

La etapa "confirmación" no existe como skill: está repartida entre `etapa_variantes` (final) y
la guarda de `request_shipping_details`. Para el scorecard la tratamos como etapa propia porque
es el punto donde se rompió el PR #281.

### 2.2 Los tres canales de salida del bot (y por qué importa)

1. **Texto** (`final_content` → `send_whatsapp_message`) — sujeto a sanitizer, admin-text-guard,
   variant-enumeration-guard, portavelas-guard, suppress-when-picker, NO_MESSAGE.
2. **UI intents** (`pending_ui_intents` → `flush`) — quick replies, products_list, product_detail,
   gallery, variant_picker, shipping_flow, order_confirmation, shipping_rates,
   payment_instructions, cta_url, contact_card, reaction.
3. **Estado** (`metadata.json`) — tags, `status_history`, `escalation_reason`, `active_route`,
   `episodes[]`, `order_draft`, `registered_order`, `last_inbound_signal`.

El evaluador actual ve el canal 1 y el nombre de las tools del 2. **El scorecard necesita los tres.**

### 2.3 Las ~40 mecánicas deterministas ya existentes

Agrupadas por dónde actúan (cada una es hoy una guarda; cada una es mañana un check):

- **Pre-turno**: nota de hora Bogotá, nota de frontera de episodio, `purchase_signals`
  (afirmación/aplazamiento/botón → `confirmed_at_ms`, `last_inbound_signal`), nota del draft,
  captura de cantidad compuesta, web cart / product ref, PDF → humano, ruta humano = bot mudo,
  flag del Flow → idle 10 min.
- **Tool-loop**: tools que cortan el turno, default-deny de narración pre-tool, corrientazo,
  empty-content recovery, `sanitize_llm_text`, decisiones parseadas de envelopes.
- **Post-turno**: abstención NO_MESSAGE, red orden↔tag (`ensure_payment_pending_closure`), red de
  escalación por closing tag, `EpisodeClosedEvent` + CAPI, first-contact greeting, suppress text
  con picker, variant-enumeration guard, portavelas guard, admin-text guard, turno admin no-send,
  ghosting, escalation shutdown, persist `tools_used`, flush de intents.
- **Dentro de tools**: precondición de `request_shipping_details` (`purchase_not_confirmed`,
  `customer_deferred`), escalación guardada (`ORDER_PENDING_SHIPPING_DETAILS`), precondiciones de
  tags (`CONFIRMADO_*`), anti-selector en quick replies, closed-list en picker, validación de
  `set_order_slot` (familias de color, signos), validaciones de `register_order`
  (`missing_receiver_name`, `amount_mismatch`), `send_shipping_rates` fijo, `payment_instructions`
  determinista.

**Observación clave**: cada guarda deja una traza distinta (log, `status_history.source`,
`rejected_buttons` en el envelope, `queued:false`…), pero **ninguna llega al evaluador**. Una
guarda que dispara es información doble: (a) el cliente se salvó, (b) el LLM intentó hacerlo mal.
El scorecard registra ambas cosas: "guarda X disparó" = el check pasa para el cliente pero se
anota como *intento fallido del LLM* (nivel `mayor`), porque es señal de que el prompt o la etapa
están débiles y de que el día que la guarda no cubra el caso, el error llega.

### 2.4 Las tres fuentes de trazas que existen hoy

| Fuente | Qué tiene | Qué NO tiene |
|---|---|---|
| **A. JSONL dashboard** `<vault>/wa_x/sessions/wa_x.jsonl` | texto visto por el cliente, `tools_used` (desde gate `persist-tools-used-v1`), `ui_component` (flush exitoso), mensajes humanos | turnos solo-tool, args/results de tools, texto descartado/suprimido, notas de contexto, etapa |
| **B. Historial LLM** `$EXOCLAW_STATE_DIR/<slug>/sessions/wa_x.jsonl` | TODO: user, assistant con `tool_calls` + `reasoning_content`, `role:tool` results, turnos de ghosting | lo que el cliente realmente vio (texto suprimido por guardas sigue ahí), no expuesto por API |
| **C. `metadata.json`** | `status_history[]`, `episodes[]` (con `order_draft`, `msgs_count_at_*`), `registered_order`, `last_inbound_signal`, `ui_intents_failures[]`, `shipping_flow_awaiting_reply_since_ms` | la etapa por turno (el draft es mutable; solo se congela al cerrar) |
| D. Temporal history | cada activity con input/output | costoso de leer; no para el path online |
| E. Logs del worker | líneas canónicas de cada guarda | no correlacionables por episodio sin OTel |

Conclusión: **hay que emitir una traza por turno desde el workflow** (§3.1). Reconstruir desde
B + C es posible como backfill, pero frágil y no cubre "lo que el cliente vio".

---

## 3. Diseño

### 3.1 Habilitador: `turn_trace` (traza por turno)

Un evento por turno del bot, escrito por `sales_session.py` después de `run_agent_turn` y de las
guardas post-turno (bajo `workflow.patched("turn-trace-v1")`), al JSONL del dashboard con
`kind: "turn_trace"` (el store ya soporta `kind`; el dashboard ya deriva `ui_type` por kind y
puede ignorarlo).

```jsonc
{
  "kind": "turn_trace", "role": "system", "timestamp": "...",
  "episode_id": "ep_007", "turn": 6,
  "trigger": "customer" | "ghost" | "handoff" | "burst",
  "stage_in": "descubrimiento", "stage_out": "variantes",
  "draft_after": {"producto": "cubo-love", "color": "azul", "cantidad": ""},
  "inbound_signal": {"kind": "deferral" | "affirmation" | "button_confirm" | null, "text": "..."},
  "tools": [
    {"name": "search_products", "ok": true, "summary": "q=café → 23 resultados"},
    {"name": "request_shipping_details", "ok": false, "rejected": "purchase_not_confirmed"}
  ],
  "intents": [{"kind": "shipping_flow", "has_text": false}],
  "text_sent": true, "text_len": 142,
  "text_suppressed": null | "default_deny" | "variant_guard" | "admin_guard" | "picker" | "no_message" | "admin_turn",
  "guards": ["first_contact_greeting", "variant_enumeration_guard"],
  "state_after": {"tag": "INTERESADO", "route": "ventas", "escalation": null, "tag_source": "llm" | "safety_net" | null},
  "llm": {"model": "deepseek-flash", "iterations": 3, "usage_tokens": 4120, "latency_ms": 3800}
}
```

Reglas: R-JSON (solo escalares/listas planas), R-DET (todo bajo gate), best-effort (nunca tumba
el turno), tamaño acotado (`summary` ≤ 200 chars, sin args completos: eso sigue en B). Backfill
para episodios viejos: `scripts/backfill_turn_traces.py` reconstruye desde B + C con
`source: "backfill"` (menos fiel; el scorecard marca los checks que dependen de campos ausentes
como `desconocido`, no como `pasa`).

### 3.2 Trayectoria canónica (lo que consume el scorecard y la UI)

`sales_eval/scorecard/trajectory.py` fusiona A + C + `turn_trace` en una lista ordenada de eventos:

```
Evento = {t, turn, stage, lane, kind, payload, refs}
  lane ∈ {cliente, bot, tools, intents, estado, guardas}
  kind ∈ {msg_cliente, boton, lista, flow_reply, media, señal,
          msg_bot, msg_bot_suprimido,
          tool_ok, tool_rechazada,
          intent_enviado, intent_fallido,
          tag, escalacion, cierre_episodio, handoff_in, handoff_out,
          guarda, ghost, humano}
```

Además: `stage_final`, `outcome` (tag terminal / escalación / abierta), `first_contact`,
`episode_id`, `origin` (CTWA/web/orgánico), ventana temporal. Es **puro** (sin I/O) y testeable
con fixtures. La UI dibuja la tira de trayectoria directamente desde esta estructura; el
scorecard evalúa sobre ella. Una sola fuente → lo que se ve es lo que se evaluó.

### 3.3 Registro de checks (`checks.yaml`)

Cada check declara:

```yaml
- id: CON-01
  nombre: Formulario de envío solo tras confirmación de compra
  etapa: confirmacion
  nivel: critico            # critico | mayor | menor
  tipo: codigo              # codigo | juez
  aplica_cuando: "intent shipping_flow enviado en el episodio"
  regla: "existe señal de confirmación (confirmed_at_ms | botón Confirmar | afirmación) en un turno ANTERIOR al intent"
  evidencia: "turno del intent + turno de la señal (o su ausencia)"
  origen: [PR-281, spec:R14, memoria:premature_shipping_form_false_confirmado]
  golden_behavior: shipping_after_confirmation   # vocabulario unificado con goldens
```

**Tipos**:
- `codigo`: función pura `check(trayectoria) -> Resultado{veredicto, evidencia[], nota}`. Sin
  LLM. Corre para el 100 % de los episodios, cuesta cero, no es flaky. Es la capa de auto-fail.
- `juez`: UNA llamada aislada al juez por check (nunca 6 criterios en un prompt), con: extracto del
  guion de la etapa (política), ventana de trayectoria relevante (±2 turnos, o el episodio entero
  para checks de conversación), 3-5 ejemplos etiquetados, y salida JSON estricta
  `{veredicto: pasa|falla|no_aplica|desconocido, evidencia: "cita textual", critica: "1-3 frases"}`.
  Temperatura 0, **dos muestras**; si no coinciden → `desconocido` → cola humana. `desconocido`
  nunca cuenta como `pasa`.

**Veredicto del episodio** (`verdict.py`):
```
FALLA   si ≥1 check crítico en falla
ALERTA  si 0 críticos y ≥1 mayor en falla
PASA    en otro caso (solo menores o nada)
+ primer_fallo = {turn, check_id}   (Hamel: "note the first failure"; los errores aguas arriba causan los de aguas abajo)
+ cumplimiento = pasados / aplicables (no críticos)   ← secundario, nunca titular
+ desconocidos = N                                     ← visible, nunca escondido
```

### 3.4 El registro inicial (≈50 checks)

Leyenda: **C** crítico · **M** mayor · **m** menor · 🧮 código · ⚖️ juez. "Origen" cita
incidente/PR/Requirement del spec (R#, según §8 del mapa) o regla del guion (G).

**APE — Apertura** *(aplica si `first_contact`)*

| id | check | nivel | tipo | origen |
|---|---|---|---|---|
| APE-01 | Primer texto visible = saludo por hora + "Hubara" + propuesta de valor | M | 🧮 | R10, `first_contact_greeting_dropped` |
| APE-02 | Sin apertura prohibida ("¡Hola!", "Hey", "Buen día") | m | 🧮 | G |
| APE-03 | Ofreció catálogo en apertura (quick_replies con `catalog.browse`) | M | 🧮 | G, golden `offer_catalog_opening` |
| APE-04 | No re-saludó en conversación con historial | m | 🧮 | R10 |

**DES — Descubrimiento**

| id | check | nivel | tipo | origen |
|---|---|---|---|---|
| DES-01 | ≤ 2 preguntas del bot antes de mostrar algo (`products_list`/`product_detail`/`gallery`) | M | 🧮 | PR-281 (4 preguntas antes de mostrar) |
| DES-02 | Una pregunta por burbuja | m | 🧮 | G |
| DES-03 | **Catálogo pedido → catálogo enviado** en el mismo turno (botón/“ver catálogo” → intent de lista/galería) | C | 🧮 | G |
| DES-04 | **Clasificó bien la intención**: la primera recomendación es coherente con lo que el cliente dijo buscar (regalo/para sí, aroma, momento, diseño) | M | ⚖️ | operador |
| DES-05 | `search_products`/`get_product_by_handle` ANTES de nombrar producto/precio | C | 🧮 | G, golden `search_before_naming` |
| DES-06 | Sin alucinación de catálogo: producto/precio/aroma/color/conteo afirmado ∈ outputs de tools del episodio | C | 🧮 + ⚖️ (paráfrasis) | G, `duo_zodiacal_description_gap` |
| DES-07 | Cliente sin fit (pide algo fuera del rubro) → redirige o escala `CATALOG_GAP`, no inventa ni fuerza | M | ⚖️ | `remarketing_no_fit_lead` |
| DES-08 | Producto elegido → `set_order_slot(producto)` en ese turno | M | 🧮 | G |
| DES-09 | Diseño antes que aroma/color (no pregunta aroma sin producto) | m | 🧮 | PR-281 §6 |

**VAR — Variantes** *(aplica si hubo producto en el draft)*

| id | check | nivel | tipo | origen |
|---|---|---|---|---|
| VAR-01 | **4+ variantes nunca en texto**: texto enviado que enumera ≥4 labels del catálogo sin `variant_picker` | C | 🧮 | PR-281 §6, R16 |
| VAR-01b | La guarda de enumeración tuvo que disparar (el LLM lo intentó) | M | 🧮 | idem (señal de prompt débil) |
| VAR-02 | **Aromas/colores enviados correctos**: opciones del picker ⊆ variantes reales del producto Y responden a lo pedido | M | 🧮 + ⚖️ | operador |
| VAR-03 | Una variante por mensaje (no dos pickers/preguntas en un turno) | m | 🧮 | G |
| VAR-04 | Elección del cliente (lista/texto) → `set_order_slot` con ese valor en el turno | M | 🧮 | G |
| VAR-05 | No eligió por el cliente (no asumió variante que el cliente no dijo) | M | ⚖️ | PR-281 ("¿Te lo dejo en azul?") |
| VAR-06 | Cantidad dicha por el cliente no se re-pregunta | M | 🧮 | `quantity_compound_reply_capture` |
| VAR-07 | Precio dicho (texto o `product_detail`) antes de pedir confirmación/datos | M | 🧮 | PR-281 |
| VAR-08 | Tono de color → familia (no rechazo de "azul clarito") | m | 🧮 | `color_families_tolerance` |
| VAR-09 | Quick replies sin selectores de producto/aroma/color (`rejected_buttons` = 0) | m | 🧮 | `quick_replies_catalog_selector_guard` |

**CON — Confirmación de compra** *(aplica si variantes completas o si hubo intento de avance)*

| id | check | nivel | tipo | origen |
|---|---|---|---|---|
| CON-01 | **Formulario de envío solo tras confirmación** (señal ANTES del intent `shipping_flow`) | C | 🧮 | PR-281 §2, R14 |
| CON-02 | **Aplazamiento respetado**: último inbound = aplazamiento ⇒ sin tools de avance, texto breve, tag `INTERESADO` | C | 🧮 | PR-281 §1/§4, R15 |
| CON-03 | El bot no afirma "confirmado/registrado" sin señal de confirmación | C | 🧮 + ⚖️ | PR-281 |
| CON-04 | Con variantes completas, pidió el sí explícito (pregunta o quick_replies confirmar) | M | ⚖️ | G |
| CON-05 | La guarda `purchase_not_confirmed`/`customer_deferred` tuvo que disparar | M | 🧮 | PR-281 (intento del LLM) |

**ENV — Datos de envío** *(aplica si intent `shipping_flow` o slots de envío)*

| id | check | nivel | tipo | origen |
|---|---|---|---|---|
| ENV-01 | `request_shipping_details` UNA vez por episodio | M | 🧮 | G |
| ENV-02 | Formulario acompañado de texto (intro no vacío o texto enviado en el turno) — no "formulario pelado" | M | 🧮 | PR-281 (default-deny) |
| ENV-03 | `flow_reply` → `set_order_slot` de ciudad/dirección/teléfono/nombre en ese turno | M | 🧮 | G |
| ENV-04 | No re-preguntó un dato ya dado (slot lleno y el bot lo vuelve a pedir) | M | 🧮 | golden `no_reask` |
| ENV-05 | **Nunca datos bancarios ni valor de envío definitivo en texto** (regex Nequi/llave/"$ envío" sin `shipping_rates`) | C | 🧮 | R7, R8, `shipping_value_rules` |
| ENV-06 | Formas de pago informadas correctas (3 métodos, umbral contra entrega) | M | ⚖️ | R7 |
| ENV-07 | Nombre de quien recibe capturado antes de registrar | M | 🧮 | R6 |

**CIE — Cierre** *(aplica si `verify_order_for_checkout` o `register_order` o tag `CONFIRMADO_PAGO_PENDIENTE`)*

| id | check | nivel | tipo | origen |
|---|---|---|---|---|
| CIE-01 | Secuencia canónica en orden: `verify` → `order_confirmation` → botón → `register_order` | C | 🧮 | G (superset ordenado) |
| CIE-02 | `register_order` solo tras botón Confirmar / sí explícito | C | 🧮 | R14 |
| CIE-03 | Tag `CONFIRMADO_PAGO_PENDIENTE` ⇔ `registered_order.success` | C | 🧮 | G |
| CIE-03b | El tag lo puso el LLM, no la red de seguridad (`status_history.source ≠ safety_net`) | M | 🧮 | `sales_safety_net_deferred_shutdown` |
| CIE-04 | Escalación `PAYMENT_VERIFICATION_PENDING` tras registro | C | 🧮 | G |
| CIE-05 | UN solo mensaje de cierre, sin frases prohibidas | M | 🧮 | golden `single_closing_message`, `no_forbidden_closing` |
| CIE-06 | Nunca `COMPRA_EXITOSA` puesto por el bot | C | 🧮 | G, golden `tag_not_set` |
| CIE-07 | Monto coherente: `order_confirmation` = `verify` = `register_order` (`amount_mismatch` = 0) | C | 🧮 | SEC-07 |
| CIE-08 | Aviso de portavelas solo si `portavelas_included` | m | 🧮 | `portavelas_color_policy` |

**POS — Post-cierre** *(aplica si `episode.order_id`)*

| id | check | nivel | tipo | origen |
|---|---|---|---|---|
| POS-01 | Pago afirmado solo con `check_order_status.pay_status = paid` | C | 🧮 | G |
| POS-02 | Cambio al pedido → escalación (`EXPLICIT_REQUEST`/`POST_SALE_ISSUE`) | M | 🧮 + ⚖️ | G |
| POS-03 | Ack del comprobante con el copy vigente ("pasa a elaboración… informamos despacho") | m | 🧮 | `payment_receipt_ack_copy` |

**TAG — Etiquetado y estado** *(siempre aplica)*

| id | check | nivel | tipo | origen |
|---|---|---|---|---|
| TAG-01 | **Etiqueta terminal sostenida por evidencia**: `CONFIRMADO_*` ⇒ señal de confirmación; `INTERESADO` ⇒ sin confirmación+datos; `RECHAZO` ⇒ el cliente rechazó | C | 🧮 (`CONFIRMADO_*`) + ⚖️ (`RECHAZO`/`INTERESADO`) | PR-281 §3 |
| TAG-02 | Motivo de escalación sostenido por el disparador observable (`DISCOUNT_REQUEST` ⇒ el cliente habló de descuento, etc.) | M | 🧮 + ⚖️ | G |
| TAG-03 | **Escaló cuando debía** (descuento, mayoreo, evento, salud, humano explícito, internacional) | C (salud/humano explícito) · M (resto) | ⚖️ | G, golden `escalate` |
| TAG-04 | No sobre-escaló consultas normales | M | ⚖️ | golden `no_escalation` |
| TAG-05 | Ruta humano ⇒ el bot no volvió a hablar | C | 🧮 | `human_handoff_tag_invariant` |
| TAG-06 | Ninguna red de seguridad tuvo que actuar (`source ≠ safety_net`, `ensure_*` sin efecto) | M | 🧮 | varias |

**EST — Estilo y seguridad** *(siempre aplica)*

| id | check | nivel | tipo | origen |
|---|---|---|---|---|
| EST-01 | Tuteo colombiano, cero voseo | m | 🧮 | golden `no_voseo` |
| EST-02 | Cero em dash; ≤ 1 emoji por burbuja y de la allowlist | m | 🧮 | golden `no_em_dash`, `emoji_allowlist` |
| EST-03 | **Sin fuga de texto admin/deliberación** al cliente (`looks_like_admin_leak` sobre texto ENVIADO) | C | 🧮 | `remarketing_deliberation_leak`, `ghost_close_leak` |
| EST-03b | La admin-text-guard tuvo que disparar | M | 🧮 | idem |
| EST-04 | No reveló ser IA | M | ⚖️ | golden `no_ai_reveal` |
| EST-05 | Sin promesas de tiempo de entrega | M | 🧮 + ⚖️ | G |
| EST-06 | Sin narración descartada por default-deny (el LLM puso texto junto a tools) | m | 🧮 | `pre_tool_narration_default_deny` |
| EST-07 | Retención de contexto (no perdió lo dicho en el episodio) | M | ⚖️ | métrica actual `knowledge_retention` |
| EST-08 | Respondió lo que el cliente preguntó (relevancia por turno) | M | ⚖️ | operador |

**GHO — Ghosting** *(aplica si hubo turno con `trigger = ghost`)*

| id | check | nivel | tipo | origen |
|---|---|---|---|---|
| GHO-01 | El turno de ghosting NO envió texto al cliente | C | 🧮 | `ghost_close_leak` |
| GHO-02 | Etiqueta de ghosting coherente con el estado (ver TAG-01) | — | — | — |
| GHO-03 | Con Flow pendiente, el idle esperó ≥ 10 min antes del ghosting | m | 🧮 | R17, PR-281 §5 |

**REM — Remarketing (extensión, agente aparte)** — `REM-01` el gancho no inventa un "siguiente
paso" que la conversación no sostiene; `REM-02` el handoff a ventas prefija `[EL CLIENTE APLAZÓ]`
cuando aplica; `REM-03` no remarketing a lead sin fit. Se diseñan en su propio scorecard cuando
el de ventas esté vivo.

### 3.5 Aplicabilidad por etapa terminal

El checklist "depende de en qué momento quedó" así: cada check tiene `aplica_cuando`; la UI
muestra los no aplicables en gris (no cuentan). Ejemplos:

| Terminó en | Checks que aplican | No aplican |
|---|---|---|
| `descubrimiento` + `INTERESADO` | APE, DES, TAG-01 (INTERESADO sostenido), EST, GHO | VAR (salvo si hubo producto), CON, ENV, CIE, POS |
| `variantes` + `INTERESADO` | + VAR, CON-04 (¿pidió el sí?) | ENV, CIE, POS |
| `confirmación` + `CONFIRMADO_SIN_DATOS` | + CON-01..05, TAG-01 (crítico: ¿hubo señal?) | ENV (salvo si mandó Flow), CIE |
| `cierre` + `CONFIRMADO_PAGO_PENDIENTE` | todo hasta CIE | POS |
| `postcierre` + `COMPRA_EXITOSA` | todo | — |
| cualquiera + `HUMANO(reason)` | + TAG-02..05 | lo posterior a la escalación |

### 3.6 Arquitectura (dónde vive cada cosa)

```
hubara_agency/src/plugins/chats/agent/sales_eval/scorecard/
  checks.yaml            registro (id, etapa, nivel, tipo, aplica_cuando, regla, origen, golden_behavior)
  trajectory.py          A + C + turn_trace → Trayectoria (puro)
  checks/                un módulo por familia: ape.py des.py var.py con.py env.py cie.py pos.py tag.py est.py gho.py
  judge_checks.py        prompts por check ⚖️ (política = extracto del skill de etapa + few-shot)
  verdict.py             FALLA/ALERTA/PASA + primer_fallo + cumplimiento
  contracts.py           DTOs frozen escalares (R-JSON): ScorecardResult{verdict, n_critical_failed, ...}
activities/eval_activities.py
  score_episode_activity  ← nueva, corre junto a evaluate_sales_conversation_activity bajo gate en EvaluateEpisodeWorkflow
evals/history.py         registro extendido: {..., scorecard: {verdict, first_failure, checks: [{id, verdict, evidence_turns, critique}]}}
platform/observability/eval_metrics.py   gen_ai.eval.check (attrs check_id, stage, level, verdict) + gen_ai.eval.verdict
api/evals.py             GET /evals/scorecard?session_id&episode_id   (trayectoria + checks + veredicto)
                         GET /evals/scorecards?days&verdict&check_id   (matriz)
                         GET /evals/checks/stats?days&by=week          (tasa por check + pareto + embudo)
                         POST /evals/labels  ·  GET /evals/calibration  (§3.8)
frontend_dashboard/src/plugins/agents_admin/frontend/
  entities/scorecard, entities/check-stats, entities/eval-label   (Zod + hooks)
  features/trajectory-strip, scorecard-panel, compliance-matrix, failure-pareto, check-trend, stage-funnel, judge-calibration
  features/agents-quality  → pestañas: Resumen · Conversaciones · Calibración · Goldens
```

Reglas duras que aplican: P-28 (nuevo código importa `src.sdk`, no `src.platform`; `sales_eval`
ya tiene deuda, no sumar), R-DET/R-JSON en el workflow y la activity, `workflow.patched` para
cualquier cambio de secuencia en `sales_session.py` y `evaluate_episode.py`, cast
`agents_admin → chats` para todo endpoint nuevo (Canal 3), FSD estricto (entities por plugin,
sin imports cross-plugin en el frontend). El manifest `chats/plugin.yaml` es spinal.

### 3.7 Transición

1. **Convivencia**: `score_episode_activity` corre junto a la eval actual; el registro de history
   lleva ambos. La UI muestra el veredicto del scorecard como titular y el `avg` viejo en gris
   ("puntaje legado") durante 4 semanas para comparar.
2. **Retiro**: cuando ≥ 200 episodios tengan ambos, se mide cuántos `FALLA` tenían `avg ≥ 0.7`
   (la tasa de "0.93 con errores"). Se retira el `avg` como titular. Las 7 métricas juez se
   mapean: `greeting/style` → APE/EST; `script_adherence` → se disuelve en los checks por etapa;
   `no_hallucination` → DES-06; `correct_handoff` → TAG-02..04; `knowledge_retention` → EST-07;
   `proactive_offering`/`conversion_progress` → DES-03, CON-04; `role_adherence` → EST-04.
3. **Flag de alerta** pasa a ser `verdict ∈ {FALLA, ALERTA}` (no `avg < 0.7`).

### 3.8 Calibración del juez (Capa 3)

- **Etiquetado humano** en el dashboard: muestra de episodios (aleatorios + `FALLA` + con
  `desconocido` + extremos de tools/latencia); por cada check ⚖️ aplicable, el operador marca
  `pasa/falla` + nota. Se guarda en `<vault>/_evals/labels/<date>.jsonl`.
- **Acuerdo** por check ⚖️: TPR, TNR y kappa de Cohen contra las etiquetas humanas (no "accuracy"
  cruda: los fallos son raros). Umbral: kappa ≥ 0.6 para confiar; por debajo, la UI muestra el
  check con trama "juez no calibrado" y el veredicto del episodio no lo usa como crítico.
- **Set fijo de calibración** (~100 episodios etiquetados) que se re-puntúa en cada cambio de
  juez/modelo/prompt → detecta drift del juez, no del agente.
- **Criteria drift es esperado** (EvalGen): el registro `checks.yaml` es versionado; cada
  semana se hace análisis de errores sobre una muestra (§6) y se agregan/reformulan checks.
- Los desacuerdos se convierten en few-shot del prompt de ese check.

---

## 4. Visualizaciones

Todas se dibujan con SVG/HTML propio (el dashboard no usa librería de charts; `EvalTrendChart`
ya es SVG a mano). Colores: etapas con la paleta categórica validada del proyecto
(descubrimiento azul, variantes naranja, confirmación aqua, envío amarillo, cierre magenta,
postcierre verde, humano gris); estados con la paleta de status (pasa verde, menor ámbar,
mayor coral, crítico rojo) **siempre con ícono + texto**, nunca solo color.

### 4.1 Tira de trayectoria (por conversación) — la vista central

Diagrama de secuencia horizontal por episodio. Eje X = turnos (no tiempo lineal: los huecos de
horas se comprimen con un marcador "⏸ 2 h 18 min"). Bandas de fondo = etapa proyectada en cada
turno. Carriles:

```
etapa      ▒▒ descubrimiento ▒▒▒▒▒▒▒▒▒▒▒▒│▒ variantes ▒▒▒│▒ confirmación ▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒│ humano
cliente    ●hola  ●"para regalo"  ●"café"  ●"el primero azulito"  ⏸  ●"voy en camino a casa"
bot        ▢saludo ▢¿para ti? ▢¿aroma? ▢¿espacio? ▢¿color?  ▢"¿te lo dejo en azul?"   ▢(texto descartado)
tools      · · search(café→23) · · · set_slot(producto) · · · set_slot(color) request_shipping ✗guard
intents    ▤quick_replies · · · ▤products_list · · · · · · · · · · · · · · ▤shipping_flow
estado     · · · · · · · · · · · · · · · · · · · · · INTERESADO · · · handoff_in · · · CONFIRMADO_SIN_DATOS ⚠ · HUMANO
guardas    ☑greeting · · · · · · · · · · · · · · · · · · · · · · · · ☑default_deny · · · · · · · ☑ensure_escalation
checks     ✓APE-01 · · ✗DES-01(M) · · ✗VAR-01(C) · ✗VAR-07(M) ✗VAR-05(M) · ✓TAG-01 · · ✗CON-02(C) ✗CON-01(C) ✗ENV-02(M) · ✗TAG-01(C) ✗TAG-02(C)
                              ▲ primer fallo                     ▲ primer crítico
```

Interacción: click en un check → panel lateral con regla, evidencia (turnos resaltados), crítica
del juez si aplica, botón "etiquetar" (calibración) y "promover a golden". Hover en un evento →
detalle (args/summary de la tool, texto suprimido con su motivo, señal detectada).

### 4.2 Panel de scorecard (junto a la tira)

Lista agrupada por etapa: fila por check con ícono de estado (✓ pasa · ✗ falla · – no aplica ·
? desconocido · ▨ juez no calibrado), chip de nivel, y "ir al turno N". Cabecera: veredicto
grande (`FALLA`), `primer fallo: turno 4 · DES-01`, `críticos 5 · mayores 4 · menores 1 ·
desconocidos 0`, cumplimiento 61 % en gris.

### 4.3 Matriz de cumplimiento (conversaciones × checks)

Filas = episodios (ordenados por veredicto, luego fecha); columnas = checks agrupados por etapa
con cabecera pegajosa; celda = ✓/✗/–/? con color de status. Filtros: rango, etapa terminal,
veredicto, check, origen (CTWA/web/orgánico), versión del agente (SHA de deploy). Click en celda
→ abre la tira en ese turno. Esta es la vista que escala a cientos de conversaciones: un
`✗` rojo en la columna CON-01 salta a la vista aunque haya 300 filas.

### 4.4 Pareto de fallos (qué arreglar primero)

Barras = checks fallados en la ventana, ordenadas por frecuencia, color por nivel; línea
acumulada. Es literalmente el paso 4 del análisis de errores de Hamel ("count how often each
failure mode occurs"). Con tendencia semana a semana por check (▲▼).

### 4.5 Tasa de cumplimiento por check en el tiempo (reemplaza la tendencia del promedio)

Small multiples: un sparkline por check (semanal), con marcadores verticales de deploy (SHA) para
ver "falló desde el 10/09 → mejoró desde el 14/09 (PR #281)". Esto responde "¿mejoró?" por
comportamiento, no por promedio.

### 4.6 Embudo de etapa terminal

Barras apiladas por etapa terminal × resultado (INTERESADO/CONFIRMADO/HUMANO/RECHAZO), cada
barra coloreada por veredicto. Muestra dónde se caen las conversaciones **y** si se cayeron con
fallas del bot o por decisión del cliente.

### 4.7 Matriz de confusión de etiquetas

Filas = etiqueta puesta (`INTERESADO`, `CONFIRMADO_SIN_DATOS`, `CONFIRMADO_PAGO_PENDIENTE`,
`RECHAZO`, `HUMANO`); columnas = etiqueta sostenida por la evidencia (TAG-01/02). La diagonal es
verde; `CONFIRMADO_SIN_DATOS` puesto sin confirmación es una celda fuera de diagonal que hoy no
se ve en ningún lado.

### 4.8 Calibración del juez

Tabla por check ⚖️: n etiquetas, TPR, TNR, kappa, estado (confiable / revisar / no usar), y un
sparkline de kappa en el tiempo. Cola de "desconocidos" y desacuerdos para etiquetar.

---

## 5. Ejemplo trabajado: el episodio del PR #281 bajo el scorecard

Run `01a0a0f1` (sales) + handoff desde `01a0a0eb` (remarketing), 2026-09-14. Evaluador actual:
**0.93**. Scorecard:

| Turno | Evento | Check | Nivel | Resultado |
|---|---|---|---|---|
| 1 | saludo + quick_replies | APE-01, APE-03 | M | ✓ |
| 1-4 | 4 preguntas antes de mostrar nada | DES-01 | M | ✗ **primer fallo** |
| 3 | `search_products("café")` → 23/24 (el aroma es variante, no discrimina) | DES-04 (recomendación coherente) | M | ✗ |
| 5 | "Tenemos 11 aromas disponibles: Caballero…" en texto | VAR-01 | **C** | ✗ **primer crítico** (post-PR: guarda dispara → VAR-01 ✓, VAR-01b ✗ M) |
| 6 | "El primero azulito" → Cubo Love → "¿Te lo dejo en azul?" sin precio ni sí | VAR-07, VAR-05 | M, M | ✗ ✗ |
| 7 | cliente se va a cita → ghost → `INTERESADO` | TAG-01, GHO-01 | C | ✓ ✓ |
| 8 | gancho remarketing → "Voy apenas en camino a casa" (aplazamiento) → `set_order_slot` + `request_shipping_details` | CON-02, CON-01 | **C, C** | ✗ ✗ |
| 8 | texto "Perfecto, ya casi llegas…" descartado por default-deny → formulario pelado | ENV-02, EST-06 | M, m | ✗ ✗ |
| 9 | ghost 5 min → `CONFIRMADO_SIN_DATOS` (sin señal de confirmación) | TAG-01 | **C** | ✗ |
| 9 | `escalate_to_human(ORDER_PENDING_SHIPPING_DETAILS)` sin base | TAG-02 | **C** | ✗ |
| 9 | flag del Flow pisado → idle 5 min en vez de 10 | GHO-03 | m | ✗ |
| — | ruta humano, bot mudo cuando el cliente vuelva | TAG-05 | C | ✓ (consecuencia, no causa) |

**Veredicto: FALLA** · críticos 5 · mayores 4 · menores 2 · primer fallo turno 4 (DES-01) ·
primer crítico turno 5 (VAR-01) · cumplimiento 58 %. Cada ✗ tiene turno + evidencia; el Pareto
de esa semana habría mostrado CON-01/TAG-01 arriba antes de que el operador leyera el chat.

**Y después del PR #281**: el mismo episodio re-jugado daría VAR-01 ✓ (guarda) + VAR-01b ✗,
CON-01 ✓ (tool rechazada) + CON-05 ✗, TAG-01 ✓ (degradado a INTERESADO), TAG-02 ✓ (escalación
rechazada). Veredicto **ALERTA** (0 críticos, 5 mayores): el cliente se salvó, el LLM sigue
intentando lo mismo. Ese es exactamente el matiz que queremos ver: las guardas compran tiempo,
los checks `*b` dicen si el prompt mejoró de verdad.

---

## 6. El loop de análisis de errores (cómo crece el registro)

```
incidente en prod (o muestra semanal)
   → open coding: leer la tira, anotar el PRIMER fallo en lenguaje natural
   → axial coding: ¿es un check existente? ¿nuevo? ¿reformular uno?
   → checks.yaml versión N+1 (id nuevo, nivel, tipo, origen = run id)
   → si es 🧮: test rojo con la trayectoria del incidente como fixture (tests/evals/scorecard/)
   → si es ⚖️: 5+ ejemplos etiquetados, medir kappa antes de activarlo
   → guarda de runtime si corresponde (mecánica > prompt, L-11) — pero el check existe aunque la guarda no
   → golden scenario con `expected_behaviors: [<id>]` (§4.4 unificación)
   → la semana siguiente: el Pareto dice si bajó
```

Regla de oro: **ningún incidente cierra sin su check**. La memoria del operador ya tiene ~15
incidentes con guarda y sin check; el registro inicial de §3.4 los recoge todos.

**Unificación con goldens**: `expected_behavior_vocab` de `golden_scenarios.json` se mapea 1:1 a
ids de checks (`greeting_first_turn` → APE-01, `search_before_naming` → DES-05,
`tag_not_set:COMPRA_EXITOSA` → CIE-06, `escalate:<trigger>` → TAG-03, `no_reask` → ENV-04/VAR-06,
…). El golden runner emite el mismo `ScorecardResult`; el reporte de CI es la matriz de
cumplimiento escenario × check, con **pass^k** (k = 5 repeticiones) por escenario para los
críticos: "CON-01 pasa 5/5 en los 8 escenarios de confirmación" es la frase que autoriza un
deploy, no "avg 0.91".

---

## 7. Estado del arte (2025-2026) y cómo se mapea

| Tendencia | Fuente | En este diseño |
|---|---|---|
| Binario + crítica en vez de Likert; un juez aislado por criterio | Hamel Husain *LLM-as-judge* / *Evals FAQ*; Anthropic *Demystifying evals for AI agents* (2026); CheckEval (EMNLP 2025); TICK; RocketEval (ICLR 2025) | checks ⚖️ con `pasa/falla/no_aplica/desconocido` + evidencia + crítica; una llamada por check |
| Rubrics as Rewards: ítems Essential/Important/Optional/Pitfall, 0/1 cada uno; Likert pierde por 28 % | Scale AI (ICLR 2026) | niveles crítico/mayor/menor; auto-fail |
| Auto-fail en scorecards de contact-center | MaestroQA, Zendesk QA | veredicto FALLA por un crítico |
| Verificación de estado, no del camino; superset/subset de tools; precondiciones en tools con efecto | τ-bench / τ²-bench (Sierra), agentevals (LangSmith), Langfuse agent evaluation | checks 🧮 sobre trayectoria: required tools por etapa, forbidden tools, CON-01/CIE-02 como precondiciones |
| pass^k para agentes de cara al cliente; error bars con clustering | Sierra; Anthropic *Adding Error Bars to Evals* | golden runner con `repeat=5`, pass^k por escenario |
| Validar los validadores: TPR/TNR, kappa, criteria drift | EvalGen (UIST 2024); "Reliability without Validity" (2026); "Coin Flip Judge" (2026) | §3.8 calibración; `checks.yaml` versionado; dos muestras por check |
| Análisis de errores: open → axial coding → taxonomía → Pareto | Hamel/Shankar | §6; el registro ES la taxonomía |
| Slots de TOD: slot accuracy, joint goal accuracy, inform/success | MultiWOZ, "Mismatch" (2022) | `order_draft` por turno = dialogue state; VAR-04/ENV-03 = slot accuracy; CIE-07 = success/match |
| Guardrails ≠ evals, pero el mismo predicado sirve para ambos | práctica general | cada guarda → check `*b` |
| Vistas: timeline por sesión, clusters de fallos, custom viewer > vendor UI | Langfuse/LangSmith/Phoenix; Hamel "build a custom annotation tool" | §4, todo propio sobre nuestros datos (PII de WhatsApp no sale) |
| Control-decision por turno (Act/Ask/Confirm/Recover/Stop) | AgentAtlas (2026) | color secundario de los eventos del bot en la tira |

Lecturas base (orden sugerido para volverse experto): Anthropic *Demystifying evals for AI agents*
→ Hamel *Evals FAQ* + *LLM-as-judge* → EvalGen → τ-bench → Rubrics as Rewards → CheckEval →
*Adding Error Bars to Evals* → CALM (12 sesgos del juez) → Langfuse *AI agent evaluation* →
MaestroQA *auto-fail*.

---

## 8. Roadmap (HUs para el pipeline)

| HU | Qué | Capa | Depende de | Tamaño |
|---|---|---|---|---|
| **HU-SC-0** Traza por turno | `turn_trace` desde `sales_session.py` (gate), `trajectory.py` puro + fixtures, backfill script, tests de replay | backend | — | M |
| **HU-SC-1** Registro + checks de código | `checks.yaml`, familias 🧮 (≈35 checks), `verdict.py`, `score_episode_activity` bajo gate en `EvaluateEpisodeWorkflow`, history extendido, SigNoz `gen_ai.eval.check`, tests rojos con las trayectorias de los incidentes (PR-281, 943e6bff, 5f43bcd0…), backfill 30 días | backend | SC-0 | L |
| **HU-SC-2** Tira + scorecard en el dashboard | entities `scorecard`, features `trajectory-strip` + `scorecard-panel`, lista ordenada por veredicto, `avg` legado en gris | frontend + API | SC-1 | L |
| **HU-SC-3** Checks juez + vistas agregadas | `judge_checks.py` (≈15 checks ⚖️, doble muestra, few-shot), `compliance-matrix`, `failure-pareto`, `check-trend`, `stage-funnel`, `tag-confusion`, `GET /checks/stats` | backend + frontend | SC-1, SC-2 | L |
| **HU-SC-4** Calibración | etiquetado en dashboard, `labels` store, TPR/TNR/kappa por check, set fijo, badge "no calibrado" | full | SC-3 | M |
| **HU-SC-5** Goldens unificados | ids en `expected_behaviors`, golden runner emite scorecards, pass^k, reporte CI = matriz | backend + CI | SC-1 | M |
| **HU-SC-6** Alertas | `FALLA` con crítico → GitHub issue con dedup (pieza 3 del golden loop plan) | backend | SC-1 | S |
| HU-SC-7 Remarketing | scorecard REM-* | backend | SC-1 | M |

Secuencia mínima para ver valor: **SC-0 → SC-1 → SC-2** (3 HUs): con eso el operador abre una
conversación y ve la tira con los ✗ en rojo y el veredicto, sin juez. SC-3 en adelante añade el
juicio matizado y la escala.

---

## 9. Decisiones abiertas (para el operador)

1. **¿`ALERTA` bloquea algo?** Propuesta: no; solo `FALLA` genera issue. `ALERTA` alimenta el
   Pareto.
2. **¿Los checks `*b` (la guarda tuvo que disparar) son `mayor` o `menor`?** Propuesta: `mayor`
   durante el drain de cada patch (mide si el prompt mejoró), `menor` después.
3. **¿Juez Gemini vía litellm se mantiene?** Sí para empezar; la calibración (§3.8) decide si
   cambiar. Con checks binarios y ventanas cortas, un modelo más chico puede alcanzar
   (RocketEval: checklists cierran la brecha ×50 en costo).
4. **¿Sampling online del juez?** Con el volumen actual, 100 %. Los 🧮 siempre 100 %.
5. **¿Persistir `turn_trace` en el JSONL del dashboard o en archivo aparte?** Propuesta: mismo
   JSONL con `kind` (una sola lectura, orden garantizado, el dashboard ya filtra por kind).
6. **Retiro del `avg`**: ¿4 semanas de convivencia o hasta 200 episodios, lo que ocurra después?

---

## 10. Riesgos

- **Trazas incompletas para episodios viejos** → checks `desconocido`, nunca `pasa`. El backfill
  desde el historial LLM cubre tools/intents pero no "lo que el cliente vio" con certeza.
- **Sobre-ajuste al incidente**: un check demasiado específico (regex de "voy en camino") no
  generaliza. Los checks se escriben sobre **señales** (`inbound_signal = deferral`), no sobre
  frases; las frases viven en `purchase_signals.py` y tienen sus propios tests.
- **Juez no calibrado usado como crítico** → se prohíbe por diseño: solo 🧮 puede ser crítico
  hasta que un ⚖️ tenga kappa ≥ 0.6 con n ≥ 50.
- **Costo**: ≈15 checks ⚖️ × 2 muestras por episodio = 30 llamadas cortas (ventanas de ±2
  turnos), contra 7 llamadas largas hoy. Similar en tokens; si crece el volumen, muestreo para
  los ⚖️ no críticos.
- **Deuda P-28 en `sales_eval`**: el módulo nuevo `scorecard/` nace limpio (`src.sdk` solamente).
- **Replay**: `score_episode_activity` entra a `EvaluateEpisodeWorkflow` bajo
  `workflow.patched("scorecard-v1")`; `turn_trace` bajo `turn-trace-v1`. Bumpear fixture de
  replay `history_sales_session_v2.json` si cambia la secuencia.

---

## Apéndice A — Contrato de la API del scorecard (evals@v1, implementado)

Provider `chats` bajo `/api/chats/evals/*`; el dashboard lo consume por el cast
de `agents_admin` en `/api/agents/evals/*` (mismo shape, passthrough).

| Método y ruta | Respuesta |
|---|---|
| `GET /evals/checks` | `{registry_version, stages[], families[{id,label,stage}], checks[{id,name,family,family_label,stage,level,kind,applies,rule,origin[],golden_behaviors[],twin_of}]}` |
| `GET /evals/scorecards?days=30` | `{days, count, registry_version, scorecards[{session_id, episode_id, date, ts, verdict, fidelity, counts{critico,mayor,menor,pasa,no_aplica,desconocido}, compliance, first_failure{turn,check_id}\|null, first_critical, stage_final, closing_tag, turns, judge, checks{<id>: verdict}}]}` — el último por episodio, ordenado FALLA → ALERTA → PASA → SIN_DATOS y luego por fecha descendente |
| `GET /evals/scorecard?session_id&episode_id` | `{stored, scorecard{...fila de arriba + results[{check_id, verdict, level, turn, evidence, critique, source}]}, trajectory{session_id, episode_id, fidelity, closing_tag, order_id, turns[{turn, at_ms, trigger, inbound_text, signal, sent_texts[], llm_text, suppressed_reason, discarded_narration[], tools[{name, ok, error, notes[], args{}}], intents[], guards[], stage_in, stage_out, draft, confirmed, state, first_contact}]}, legacy{avg, date, metrics{}}\|null}` |
| `POST /evals/scorecard/rescore` body `{session_id, episode_id, judge}` | igual que el GET, recalculado y guardado |
| `GET /evals/checks/stats?days=56` | `{days, episodes, verdicts{FALLA,ALERTA,PASA,SIN_DATOS}, pareto[{check_id, name, level, failures}], trend[{check_id, name, level, weeks[{week, applicable, passed, rate}]}], funnel[{stage, FALLA, ALERTA, PASA, SIN_DATOS}]}` — `week` = lunes ISO |
| `POST /evals/labels` body `{session_id, episode_id, check_id, verdict: pasa\|falla, note}` | `{ok, label{..., labeled_at}}` |
| `GET /evals/labels?session_id&episode_id` | `{labels[]}` |
| `GET /evals/labels/queue?days=30&limit=20` | `{items[{session_id, episode_id, check_id, check_name, judge_verdict, reason: desconocido\|falla\|muestra, evidence, critique}]}` |
| `GET /evals/calibration` | `{min_labels, kappa_threshold, checks[{check_id, name, level, n, tp, fp, tn, fn, tpr, tnr, kappa, status: confiable\|revisar\|sin_datos}]}` |
