# Baseline — golden-eval del Asesor de Ventas (run 1)

> Primera ejecución del agente REAL (DeepSeek vía litellm) + tools reales
> (`StubOrderRegistration`, sin flush de envíos) + juez REAL (Gemini) sobre los
> 32 escenarios de `golden_scenarios.json`. Fecha: 2026-06-04.
> Reproducir: `cd hubara_agency && uv run --extra evals python scripts/golden_eval.py`

## Scorecard (run 1)

| Área | Veredicto | Evidencia |
|---|---|---|
| **Saludo (apertura)** | ✅ sólido | `greeting_compliance=1.0` en los 5 |
| **Escalación / handoff** | ✅ excelente | `correct_handoff=1.0` en los 8 triggers + no sobre-escala |
| **No-alucinación (bait directo)** | ✅ | 1.0 — no inventó producto/precio inexistente |
| **knowledge_retention** | 🔴 **falla #1** | 0.0 (no-repreguntar) · 0.11–0.36 (reales) — re-pregunta datos ya dados y se traba en loops |
| **Cierre (BANT)** | 🔴 débil | `script_adherence` 0.2–0.3 — se traba antes de `register_order` |
| **No-alucinación (producto nombrado directo)** | 🟡 | 0.0 en `descubrimiento_intencion_clara` — afirma sin `search_products` |
| **voseo / role** | 🟡 | fuga de voseo cuando lo baitean (style 0.75); role 0.5 al escalar |

## La falla dominante: knowledge_retention + loops

Caso real (`real_ep3`): el cliente pidió velas "rojas", el agente responde que no hay
rojo, y **se queda pidiendo color en bucle — ignora "✅ Confirmar" y nunca registra la
orden**. Es la fricción real ("Ya te dije los aromas", "Ya habíamos escogido color").

**Hipótesis de fix del agente** (workspace/skills/sales_script):
1. No re-preguntar un dato ya dado (proyectar el `order_draft` al prompt cada turno —
   ya existe el breadcrumb `set_order_slot`; reforzar la instrucción de NO re-preguntar).
2. Cuando un atributo pedido no está disponible (color "rojo"), ofrecer la alternativa
   más cercana **y avanzar**, no quedarse en loop; respetar señales de avance ("Confirmar").
3. Ante mención directa de producto, **buscar antes de afirmar** (cierra el gap de no-alucinación).

## Calibración del eval aplicada (post run 1)

El runner ahora corrige 2 sesgos detectados en run 1 (no son fallas del agente):
- **Full-funnel guard**: `script_adherence` / `conversion_progress` solo se evalúan en
  escenarios con ≥3 turnos de cliente (en aperturas de 1 turno penalizaban injusto).
- **Turnos vacíos**: se filtran los turnos solo-tool (content="") antes de calificar —
  rompían `KnowledgeRetentionMetric` ("2 validation errors for Knowledge", visto en real_ep4).

## Cabos sueltos (notas)
- `verify_order_for_checkout` (única tool que pega Medusa live) → en eval da error y el
  runner lo captura sin romperse. Run 1: **0 tool-errors** (el agente no llegó al checkout
  por trabarse antes). Stub dedicado = refinamiento futuro.
- Markers (`[BTN]`/`[FLOW]`/`[CART]`) se mapean a texto del cliente (no es el payload
  nativo 100%). Suficiente para calificar comportamiento; refinamiento futuro.

## Update — fidelity fix: draft harness (run 3)

Auditoría prod-vs-eval: el ciclo de turno de prod (`ingest_inbound_message` →
`run_agent_turn` → `build_prompt`) re-inyecta cada turno el **`order_draft_note`** al
`plugin_context` ("[DATOS YA CONFIRMADOS, no los vuelvas a preguntar] aroma=…, ciudad=…").
El agente graba el draft con `set_order_slot` (99 llamadas en la suite) pero el eval
**no se lo devolvía** → re-preguntaba → `knowledge_retention` falso-bajo.

**Fix** (en `golden_eval.py`): cada turno se lee el `order_draft` de metadata
(`get_projectable_draft`) y se reconstruye el system prompt con `build_order_draft_note`
(donde prod lo pone), preservando el history. Sin tocar el agente:

| `knowledge_retention` | run 2 (sin draft) | run 3 (con draft) |
|---|---|---|
| `no_repreguntar_datos` | 0.0 | **0.50** |
| `real_ep1` | 0.33 | **0.44** |
| `real_ep4` | 0.33 | **0.42** |
| `real_ep5` | None (error) | **0.36** |
| promedio "memoria" | ~0.25 | **~0.39 (+55%)** |

**El resto de `ingest` es bookkeeping** (atribución CTWA, origen, ventana 24h/72h, ciclo
de episodios) y NO afecta la conducta del vendedor → correctamente no se trae.

**Residual REAL del agente** (ya confiable, no artefacto del eval): `real_ep3` loop de
color (0.27 sin cambio — no ofrece alternativa cuando el atributo no existe) +
search-before-naming flaky (`no_hallucination` 0.0 intermitente).

**Fidelidad pendiente** (no es prompt, es input): markers `[FLOW]`/`[BTN]` nativos +
media `[IMG]` (visión). + `episode_boundary_note` si se testea re-engagement.
