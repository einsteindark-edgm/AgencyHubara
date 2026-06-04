# Plan — Loop cerrado de LLMOps para el Asesor de Ventas

> Estado: **diseño aprobado + golden set generado**. Este doc es la fuente de verdad
> de la implementación. Hermano del `LLM_EVAL_HARNESS_PLAN.md` (que cubre el offline
> eval + SigNoz ya en producción). Branch: `feat/golden-eval-loop`.

## 0. La visión — el loop cerrado

```
[1] DESCUBRIMIENTO offline (SigNoz)   ── ya existe ──
      worker sales_eval evalúa conversaciones REALES con el juez REAL cada 3h
      → emite gen_ai.eval.* a SigNoz
                    │
                    ▼  (score bajo)
[3] ALERTA + AUTO-ISSUE                ── pieza nueva ──
      candidata al frontend + GitHub issue con la descripción completa (dedup)
      el ISSUE es la alerta
                    │
                    ▼
[2] FRONTEND "Calidad LLM"             ── pieza nueva ──
      bandeja: conversaciones que NO pasaron el score (texto + métricas + fecha)
      histórico: timeline por tipo de conversación (falló el día X, mejoró el día Y)
                    │
                    ▼
[4] FIX del dev (humano)               ── no se automatiza ──
      baja la conversación al golden set, arregla el agente, sube versión
                    │
                    ▼
[5] VERIFICACIÓN REAL (GitHub Action)  ── pieza nueva ──
      corre el GOLDEN SET entero contra el SALES REAL + JUEZ REAL, on-demand sobre main
      → todos los scores; reemplaza al gate mockeado (que daba verde siempre)
                    │
                    └──────────────► vuelve a [1]
```

## 1. Decisiones locked (de la conversación con el operador)

- **El eval va contra el agente REAL** (cerebro DeepSeek + `workspace/*.md` + tools), NO mockeado.
- **Escritura neutralizada / lectura real** (best practice "cerebro real, efectos sandboxeados").
- **Catálogo = snapshot del vault** (lo que prod ya usa: copia cacheada de Medusa que se
  actualiza desde el menú Catalog del frontend). Real + determinista. Sin cassettes ni test-Medusa.
- **Gate de PR**: solo **on-demand sobre main** (`workflow_dispatch`). **Borramos el mockeado.**
- **Alerta** = el **GitHub issue** auto-creado (con dedup). Sin email/Slack por ahora.
- **Scope de esta tanda**: hasta el auto-issue. El fix + bump de versión es humano.

## 2. El mecanismo "real pero seguro" (núcleo técnico)

El loop de producción `run_agent_turn` (`src/platform/workflow_helpers.py`) corre:
`build_prompt → [llm_chat → execute_tool]* → final_content`. Es lo que hay que manejar.

Gracias a DEHA (R-DIP: efectos detrás de puertos; decisiones como dato; envíos
desacoplados como activities) **no mockeamos nada DENTRO del agente** — solo cambiamos
el borde:

| Tool de escritura | Cómo se neutraliza | Señal de grading (intento) |
|---|---|---|
| `register_order` | **Sin** `MEDUSA_REGION_ID`/`MEDUSA_SALES_CHANNEL_ID` → `StubOrderRegistration` (ya existe, `src/platform/orders/composition.py:43`) | decisión `order_registered` (items/cantidad correctos) |
| UI / WhatsApp (`send_quick_replies`, `present_*`, galerías…) | son **decision tools** → escriben `pending_ui_intents` a metadata; el envío real lo hace `flush_pending_ui_intents_activity` (workflow, `sales_session.py:567`). El runner **no corre el flush**. | leer `pending_ui_intents` = qué quiso mandar |
| Mensaje final de texto | lo manda el **workflow**, no `run_agent_turn`. El runner maneja el **helper**. | calificar `final_content` (juez) |
| `verify_order_for_checkout` | única que pega Medusa **live** → en eval, stub / skip (sin env). **Verificar degradación al implementar.** | n/a |
| `search_products` / catálogo | `get_catalog_client()` lee el **snapshot del vault** → real + determinista | n/a (lectura) |

**Doble lente de calificación sobre la MISMA corrida real:**
1. **Juez LLM** (el mismo `build_judge()` de SigNoz) sobre el texto → greeting, script_adherence, style, no_hallucination, handoff, conversion, role_adherence, knowledge_retention.
2. **Ledger conductual determinista** (`tools_used` + `pending_ui_intents` + decisiones) → los `expected_behaviors` del golden ("¿saludó con quick_replies? ¿buscó antes de recomendar? ¿intentó registrar la orden bien? ¿escaló correcto?"). Esto es "calificar que **intentaron** ir" sin tocar la realidad.

## 3. Golden set — `tests/evals/goldens/sales/golden_scenarios.json`  ✅ HECHO

32 escenarios (turnos del CLIENTE; el runner maneja al agente real). Cubre:
- **Apertura/estilo** (5): saludo canónico, voseo-bait, emoji-flood.
- **Descubrimiento** (3): regalo, intención clara, vago.
- **Recomendación/alucinación** (3): buscar antes de nombrar, producto inexistente, precio inventado.
- **Objeciones + escalación** (8): precio, descuento, mayoreo/B2B, evento, humano explícito, internacional, salud, **anti-sobre-escalación**.
- **Cierre BANT** (4): canónico, no-segundo-mensaje, jamás-COMPRA_EXITOSA, multi-producto.
- **Robustez** (4): no-repreguntar, ¿es IA?, fuera-de-scope, terso+typos.
- **REALES** (5): episodios redactados de `wa_573125671604` (browse multi-aroma, multi-cantidad con fricción "ya te dije los aromas", confusión "que hay que gacer?", modificar carrito, cierre limpio).

Cada escenario trae `expected_behaviors` (ledger determinista) + `judge_metrics` (lentes del juez) + `probes` (qué falla busca). PII real redactada (`[REDACTED:telefono]`, etc.). Vocabulario de behaviors documentado en el propio archivo. **Extensible**: cada candidata que SigNoz descubra se baja acá como un escenario nuevo.

## 4. Pieza 5 — GitHub Action `golden-eval` (verificación REAL, on-demand)

**Runner nuevo** `scripts/golden_eval.py` (generaliza `scripts/agent_eval.py`):
- Lee `golden_scenarios.json`.
- Para cada escenario: bootstrap de sesión aislada (vault temp + **snapshot del catálogo** copiado al vault) → maneja `run_agent_turn` por cada `customer_turn` → recoge respuestas + ledger.
- **Composition de eval**: registra las tools con los **puertos stub** (sin env Medusa) y NO corre `flush_pending_ui_intents_activity` (intents quedan en metadata).
- Mapea los markers (`[BTN: …]`, `[FLOW: …]`, `[CART: …]`) al input correcto.
- Califica: juez (`judge_metrics`) + asserts (`expected_behaviors` vs ledger).
- Reporte: tabla por escenario × métrica + pass/fail de behaviors. Markdown a `$GITHUB_STEP_SUMMARY` + artifact JSON. (Opcional: emitir a SigNoz con `source=golden` para el histórico.)

**Workflow** `.github/workflows/golden-eval.yml`:
- `on: workflow_dispatch` (input opcional: `category` / `scenario_id` para correr un subconjunto).
- Levanta litellm con `DEEPSEEK_API_KEY` + `GEMINI_API_KEY` (GitHub Secrets) → mismo juez que SigNoz.
- `uv run python scripts/golden_eval.py` → reporte visible en la corrida + artifact.
- **Borrar** `eval-unit-tests.yml` (el mockeado) o degradarlo a un lint estructural NO bloqueante.

**Cabo suelto a resolver al implementar**: `verify_order_for_checkout` sin env Medusa (degradación o stub) + el mapeo fiel de los markers de botón/flow al input real del agente.

## 5. Pieza 2 — Frontend "Calidad LLM": bandeja + histórico

Hoy la pestaña lista candidatas crudas (aprobar/descartar) y está vacía. Falta:
- **Bandeja "no pasaron el score"**: por candidata mostrar la **conversación**, las **métricas que fallaron** (con su score), y la **fecha**. Backend: extender `/evals/candidates` para incluir scores+métricas+timestamp (hoy la auto-curación ya escribe la candidata; agregar el detalle del veredicto).
- **Histórico / timeline**: score-over-time por **tipo de conversación** (golden/escenario). Requiere una **identidad estable** por escenario → taggear cada eval con `scenario.id` / `golden.id` al emitir a SigNoz, y graficar `gen_ai.eval.score` por ese id. El frontend consulta ese histórico (vía un endpoint backend que lee SigNoz, o un store propio) y muestra "falló el día X → mejoró el día Y".

Entidades/hooks nuevos en `frontend_dashboard/src/` (FSD): `eval-candidate` extendido + un `eval-history` (timeline). Respeta los 4 import rules + barrels.

## 6. Pieza 3 — Alerta + auto-issue (GitHub)

En el path que detecta score bajo (`evaluate_sales_conversation_activity` / auto-curación):
- Ya escribe la candidata (frontend). **Agregar**: crear un **GitHub issue** con la descripción completa — conversación (redactada), métricas que fallaron + scores, fecha, link a SigNoz, y el escenario sugerido para el golden set.
- **Dedup obligatorio** (el eval corre cada 3h): fingerprint del problema (p.ej. `hash(scenario_type + métricas_falladas)`) → si ya hay un issue OPEN con ese fingerprint (label `llm-eval` + el fingerprint en el body/título), **no crear otro**; opcional: comentar "reapareció el {fecha}".
- Mecanismo: GitHub API con un token (`GITHUB_EVAL_TOKEN` secret) desde el worker/activity, idempotente. R-DIP: un puerto `IssueTrackerPort` (real GitHub / stub no-op si falta token) — mismo patrón que `OrderRegistrationPort`.

## 7. Archivos (resumen)

| Pieza | Archivo | Acción |
|---|---|---|
| Golden set | `tests/evals/goldens/sales/golden_scenarios.json` | ✅ creado (32 escenarios) |
| Runner | `scripts/golden_eval.py` | nuevo (driver real + stub ports + dual grading) |
| Eval composition | `src/plugins/chats/agent/sales_eval/evals/agent_runner.py` (o en el script) | nuevo (compose stub ports, no-flush, marker mapping) |
| Action real | `.github/workflows/golden-eval.yml` | nuevo (`workflow_dispatch` + secrets) |
| Borrar mock | `.github/workflows/eval-unit-tests.yml` | borrar / degradar a lint no bloqueante |
| Auto-issue | `src/platform/.../issue_tracker.py` + puerto + wire en eval activity | nuevo (dedup) |
| Frontend bandeja+histórico | `frontend_dashboard/src/.../evals/*` + `eval-history` entity | nuevo |
| API detalle/histórico | `src/plugins/chats/api/evals.py` | extender |

## 8. Secuencia sugerida

1. **Runner + Action real** (pieza 5) — es lo que el operador quiere para "esa primera ejecución y encontrar fallas". Con el golden set ya hecho, se puede correr local (`uv run python scripts/golden_eval.py`) antes de cablear el Action.
2. **Auto-issue + dedup** (pieza 3) — cierra el lazo de alerta.
3. **Frontend bandeja + histórico** (pieza 2) — la cara visible.

## 9. Riesgos / notas

- **No-determinismo**: el agente real varía entre corridas → el golden-eval es **reporte on-demand**, no gate bloqueante. El histórico (tendencia) importa más que un número puntual.
- **Costo**: cada escenario corre el agente real (N turnos × tool-loop) + el juez. 32 escenarios ≈ cientos de llamadas LLM por corrida → on-demand, no en cada push.
- **Fidelidad de markers**: `[BTN]`/`[FLOW]`/`[CART]` deben mapearse al input real del agente; si se simplifican a texto, anotarlo (no es 100% el payload nativo).
- **Snapshot del catálogo**: hay que congelar una copia en el fixture para reproducibilidad; si el catálogo de prod cambia, refrescar el snapshot a propósito (no automático).
