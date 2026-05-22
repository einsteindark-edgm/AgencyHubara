# Stress test del harness — Protocolo trimestral

> Eleva la **Técnica 10** del HARNESS_ENGINEERING.md.
>
> *"Cada componente del harness encodifica una suposición sobre lo que el modelo no puede hacer solo. Esas suposiciones envejecen."*
>
> Cada 3 meses + tras cada release de modelo (Opus 4.7 → 5.0, etc.) hacemos esto.

---

## ¿Por qué este ritual?

El harness de AgencyHubara tiene ~30 componentes (skills, hooks, workflows, prompts, gates). Cada uno fue diseñado contra una limitación específica del modelo. Pero los modelos mejoran rápido. Sin re-evaluación, acumulamos overhead que ya no compensa nada — solo agrega latencia, tokens, y cargas cognitivas innecesarias.

El stress test ES la auditoría que decide qué se queda, qué se simplifica, qué se elimina.

---

## Componentes del harness y la hipótesis que codifica cada uno

| Componente | Hipótesis (qué limitación compensa) | Cómo "removerlo" temporalmente |
|---|---|---|
| `CLAUDE.md` raíz | El agente no sabe qué es el repo sin esto | Renombrar a `CLAUDE.md.bak`; verificar si los skills/sesiones cold se desorientan |
| `CLAUDE.md` por subdir | Scoping per-area mejora rendimiento | Renombrar `hubara_agency/CLAUDE.md.bak` y `frontend_dashboard/CLAUDE.md.bak` |
| `.claudeignore` | Sin esto, el agente lee archivos generados / lock files | Renombrar a `.claudeignore.bak` |
| `CODEMAP.md` | Sin esto, el agente busca antes de saber dónde buscar | Renombrar a `CODEMAP.md.bak` |
| `project-context.md` | Convenciones canónicas; sin esto skills inventan | Renombrar |
| `spinal-files.yaml` | Sin esto, edits silenciosos a shared files | Renombrar |
| `hubara-architecture-guide` | Detalle DEHA + FSD; sin esto reglas violadas | Renombrar carpeta |
| `hubara-tech-refiner-archon` | Refinement explícito; sin esto, scope drift | Saltar este skill en el workflow (modificar workflow para invocar planner directo desde hu-original) |
| `hubara-plugin-planner-archon` | Decompose multi-plugin; sin esto, todo single task | Saltar (deshabilitar fan-out, forzar single inline) |
| `hubara-feature-planner-archon` | Decompose feature-level; sin esto, una task gigante | Saltar (implementer recibe hu directo) |
| `hubara-implementer-archon §1.5 explorer` | Sin esto, implementer quema contexto en grep | Comment-out la sección §1.5 |
| `hubara-implementer-archon §3.5 impact` | Sin esto, signature changes rompen callers | Comment-out la sección §3.5 |
| `hubara-implementer-archon §0.5 bearings` | Sin esto, construir sobre sistema roto | Comment-out §0.5 |
| `hubara-implementer-archon §7.5 visual` | Sin esto, frontend bugs invisibles | Comment-out §7.5 |
| `hubara-evaluator-archon` | Sin esto, PRs pasan con issues qualitativos | Skip el nodo `evaluate-pre-pr` en workflow |
| `hubara-merger-archon` | Sin esto, multi-plugin merge conflicts | (No removible — el merge ES funcional, no enseñanza) |
| `hubara-explorer-archon` | Template para subagent | Si los skills no lo invocan, está unused |
| Hook `pre-bash-cd-check` | Sin esto, modelo olvida `cd hubara_agency` | Comentar el matcher en settings.json |
| Hook `post-edit-lint` | Sin esto, ruff/eslint olvidados | Idem |
| Hooks `post-edit-affected-tests-*` | Tests verifica regresión local | Idem (también está opt-in) |
| Hook `post-tool-log` | Observabilidad por session | Idem |
| Hook `stop-session-log` | Log de footprint para post-mortem | Idem |
| Hook `stop-arch-gate` | Catch arch regressions al cierre | Idem |
| `smoke-test.sh` | Detecta sistema roto antes de empezar | Renombrar |
| `evaluator-rubric.yaml` | Sin esto, el evaluator inventa criterios | Renombrar |
| Workflow gates (architecture-protected) | Sin esto, edits silenciosos a protected | Comentar las `when:` de los gates |
| Codegraph MCP | Sin esto, agente solo tiene grep | Stop el MCP server |
| Settings.local.json allowlist | Sin esto, prompts permission excesivos | Reducir a vacío |
| Settings.json hooks wiring | Sin esto los hooks no se disparan | Idem |

---

## Las 3 HUs de calibración

Elegir tres HUs representativas del repo con outcomes conocidos. Re-implementarlas con/sin cada componente y comparar metrics.

### HU-CAL-1 — Single-plugin, frontend-only

**Spec:** Agregar un dropdown "filter by tag" al sidebar de chats inbox.

**Por qué:** simple, single-plugin, solo frontend. Ejercita FSD anti-patterns, Tailwind tokens, vitest, Playwright.

**Tokens baseline esperados:** ~25K tokens, ~3 iteraciones, completable en ~6 min.

### HU-CAL-2 — Single-plugin, full-stack agéntica

**Spec:** Agregar un tool LLM al agent de sales que permite "marcar lead como hot/warm/cold" + persistirlo en el vault del session + UI badge en el inbox.

**Por qué:** ejercita TODA la stack DEHA (tool, activity, workspace TOOLS.md, composition, worker registration) + FSD frontend (entity, feature, page mount) + spinal Icon.

**Tokens baseline esperados:** ~65K tokens, ~6 iteraciones (refiner + planner + implementer + evaluator + merger + review), completable en ~30 min.

### HU-CAL-3 — Multi-plugin con shared file

**Spec:** Agregar un endpoint `GET /api/orders/<id>/related-chats` que retorna las sesiones de chat asociadas a un order. Toca plugins `orders` (backend) + `chats` (backend read-only) + `frontend orders` (UI nueva). Modifica `src/platform/contracts.py` para agregar un DTO compartido.

**Por qué:** ejercita el fan-out multi-plugin, el merger, el DAG plugin-level, los spinal files (contracts.py).

**Tokens baseline esperados:** ~120K tokens, ~10 iteraciones, completable en ~75 min.

---

## Metodología

### Fase 0 — Baseline (sin removals)

```bash
# Asegurate de tener el harness en su estado completo, todos los componentes ON.
git checkout main
git pull

# Correr las 3 HUs de calibración
for HU in HU-CAL-1 HU-CAL-2 HU-CAL-3; do
  archon workflow run hu-hubara-pipeline "$HU"
done

# Recolectar metrics post-run
bash hubara_agency/.hubara/metrics-aggregator.sh > baseline-$(date +%Y%m%d).jsonl
```

Métricas a registrar:
- Tokens consumidos (total + por skill)
- Duración del pipeline
- # iteraciones por skill
- # PRs merged sin issues post-merge
- Evaluator scores promedio
- # de tests rotos en CI post-merge

### Fase 1 — Test componente por componente

Por **cada componente** del listado de arriba:

```bash
# 1. Desactivar el componente
# (renombrar archivo, comentar bloque, etc. — ver "Cómo removerlo" de la tabla)

# 2. Re-correr las 3 HUs
for HU in HU-CAL-1 HU-CAL-2 HU-CAL-3; do
  archon workflow run hu-hubara-pipeline "$HU"
done

# 3. Recolectar metrics
bash hubara_agency/.hubara/metrics-aggregator.sh > without-<componente>-$(date +%Y%m%d).jsonl

# 4. Restaurar el componente
git restore <archivos modificados>
```

### Fase 2 — Análisis

Por componente, calcular:

```
delta_tokens   = mean(tokens_without)   - mean(tokens_baseline)
delta_duration = mean(duration_without) - mean(duration_baseline)
delta_quality  = mean(eval_scores_without) - mean(eval_scores_baseline)
delta_bugs     = sum(post_merge_bugs_without) - sum(post_merge_bugs_baseline)
```

### Fase 3 — Decisión

Para cada componente:

| Patrón | Diagnóstico | Acción |
|---|---|---|
| `delta_tokens ≤ 5%` + `delta_quality ≤ 0.3 pts` + `delta_bugs = 0` | NO load-bearing en este modelo | **Podar** (remover el componente) |
| `delta_tokens > 5%` o `delta_quality > 0.3 pts` | Load-bearing | **Mantener** |
| `delta_bugs > 0` | Critical | **Mantener obligatorio** |
| Tokens ahorrados pero `delta_quality > 0.5 pts` | Trade-off compute vs quality | **Decisión humana** (¿el ahorro de tokens vale la baja de quality?) |

### Fase 4 — Reporte

Generar `stress-test-report-YYYYMMDD.md` con:
- Resumen ejecutivo (qué se podó, qué se mantuvo).
- Por componente: hipótesis original + delta medido + decisión + justificación.
- Recomendaciones para el próximo ciclo (componentes que están cerca de la línea de poda).

Commit del reporte al repo.

---

## Cadencia y triggers

| Trigger | Acción |
|---|---|
| **Cada 3 meses** (1° de Q1/Q2/Q3/Q4) | Full stress test |
| **Release de modelo principal** (Opus N → N+1) | Full stress test prioritario |
| **3+ HUs consecutivas con quality drop** | Diagnóstico spot del componente sospechado |
| **Pipeline duration aumentó >30% sin cambio en workflow** | Diagnóstico spot |

---

## Plantilla del reporte

```markdown
# Stress test report — YYYY-MM-DD

## Modelo testeado
- Modelo principal: <e.g., claude-opus-4-7[1m]>
- Versión harness: <git SHA del commit en main al momento del test>

## Resumen ejecutivo
- Componentes podados: <N> (lista)
- Componentes load-bearing confirmados: <N>
- Componentes borderline (decisión humana): <N>
- Token savings estimados anuales: <N>K tokens

## Por componente

### `<componente>`
- Hipótesis: <de la tabla>
- Delta tokens: <X%>
- Delta quality: <Y pts>
- Delta bugs: <N>
- Decisión: <podar | mantener | borderline>
- Justificación: <1-2 oraciones>

(...)

## Acciones tomadas
- <e.g., "Removí §1.5 explorer porque delta_tokens = -8% y delta_quality = -0.1 pts">
- <e.g., "Mantuve evaluator porque sin él, post-merge bugs subieron de 1 a 5">

## Próximos pasos
- <Componentes a monitorear>
- <Fecha del próximo stress test>
```

---

## Apéndice: por qué este ritual >>> intuición

> *"Empíricamente, en el harness moderno de Anthropic: lo que típicamente se mantiene útil: planner, evaluator, artefactos durables, ritual de bearings. Lo que puede volverse opcional con modelos mejores: sprint construct, context resets, decomposition extrema."*
>
> — §11.4 del HARNESS_ENGINEERING.md

Cada equipo tiene la tentación de "no podar nada porque siempre nos sirvió". El stress test convierte esa intuición en evidencia.
