# Evaluator calibration corpus

> Ritual operacional para tunear el `hubara-evaluator-archon` con feedback humano.
> Eleva la **Técnica 7** del HARNESS_ENGINEERING.md (evaluador calibrado).

## ¿Para qué sirve esta carpeta?

Los LLMs out-of-the-box son **patológicamente optimistas** al evaluar código. Sin calibración, el `hubara-evaluator-archon` aprobaría PRs que un senior humano marcaría como cuestionables.

La forma de combatirlo es el **calibration loop** (§8.4 del HARNESS_ENGINEERING.md):

1. Tomamos PRs históricos del repo.
2. El humano (vos) los puntúa contra la rúbrica del `evaluator-rubric.yaml`.
3. Corremos el evaluator sobre cada PR y comparamos.
4. Cuando diverge >1.5 puntos en algún criterio, identificamos el patrón → ajustamos el SKILL.md del evaluator con few-shot examples.
5. Repetimos hasta convergencia (>80% de criterios con divergencia ≤1.5).

## Estructura

Cada PR histórico vive como un archivo en esta carpeta:

```
pr-<PR_NUMBER>-<slug>.md
```

Ejemplo:

```
pr-115-add-customer-tag.md       # caso de PASE limpio (score humano ~8.5)
pr-098-broken-r-dip.md           # caso de FAIL (score humano ~4, R-DIP violado)
pr-127-borderline-tests.md       # caso BORDERLINE (score humano ~6.5)
```

## Template de un entry

Ver `pr-template-pass.md` y `pr-template-fail.md` en esta carpeta. Copiá uno de ellos y completá.

## Ritual de calibración

Frecuencia recomendada: **cada 3 meses** + **cada vez que actualiza el modelo principal** (e.g., Opus 4.7 → Opus 5).

Pasos:

1. **Curar PRs.** Eligí 5-10 PRs históricos del repo. Mezclá:
   - 3 PRs que pasaron limpio (score humano ≥7.5).
   - 3 PRs con issues legítimos detectados post-merge (score humano <6).
   - 2-3 PRs borderline (score humano 6-7.5).
   
   Idealmente PRs que representan distintos tipos de HU:
   - Frontend-only
   - Backend-only (agent + worker)
   - Full-stack agéntica
   - Refactor (e.g., extraer entity, mover composition factory)

2. **Puntuar cada PR como humano.** Por cada PR:
   - Copiá `pr-template-pass.md` o `pr-template-fail.md` a `pr-<NN>-<slug>.md`.
   - Completá `human_score` por criterio basándote en tu juicio.
   - Documentá tu razonamiento en `human_notes`.

3. **Sincronizar con la rúbrica.** Agregá cada PR como entry en `evaluator-rubric.yaml.calibration_examples`:

   ```yaml
   calibration_examples:
     - pr_url: "https://github.com/einsteindark-edgm/AgencyHubara/pull/115"
       pr_title: "feat(chats): agregar tag conversation"
       human_score: {architectural_compliance: 9, test_coverage_real: 8, ...}
       weighted: 8.4
       verdict: pass
       notes_file: "evaluator-calibration/pr-115-add-customer-tag.md"
   ```

4. **Correr el evaluator en cada PR.** Por cada calibration example:
   ```bash
   # Setup: checkout del SHA pre-PR para que git diff main...HEAD sea reproducible
   git checkout <commit_at_pr_merge>
   
   # Stagear los inputs que el evaluator espera en $ARTIFACTS_DIR/
   # ... (copiar refinement, manifest, task-result que estaban al momento del PR)
   
   # Invocar el skill manualmente (no via workflow)
   archon chat .claude/skills/hubara-evaluator-archon
   ```
   
   Capturá el `evaluation.yaml` que produce y guardalo como `pr-<NN>-evaluator-output.yaml` en esta carpeta.

5. **Comparar y ajustar.** Por cada PR:
   - Diff: `human_score[c] - evaluator_score[c]` por criterio.
   - Si `|diff| > 1.5` en cualquier criterio:
     - **Identificá el patrón.** ¿Qué evidencia el humano valoró que el evaluator no?
     - **Actualizá `.claude/skills/hubara-evaluator-archon/SKILL.md`** agregando un few-shot example en la sección apropiada (§4.3 o un §X nuevo) que muestre el caso al evaluator.

6. **Re-correr.** Volvé a step 4 y itera hasta que >80% de criterios converjan a `|diff| ≤ 1.5`.

## Convergencia esperada

Out-of-the-box: ~50% convergencia (evaluator overly generous).
Tras 2-3 rondas: ~80% convergencia.
Tras 5+ rondas con calibration corpus de 10+ PRs: ~90% convergencia.

Si NO convergís tras 5 rondas, el problema no es prompt — es la rúbrica. Considerá:
- ¿Los anchors están bien definidos?
- ¿El criterio tiene demasiada subjetividad?
- ¿El `weight` está bien?

## Mantenimiento

- Cuando un PR mergeado revela un bug post-merge que el evaluator NO detectó, agregá ese PR al corpus como negative example.
- Cuando el operador escala un `block_merge` con "es legítimo, override", agregá ese PR como ejemplo de borderline para tunear el threshold.
- Documentá decisiones de calibración en commit messages: "calibrate(evaluator): bump scope_discipline anchor 7 para tests inflados".

---

**Estado actual del corpus:** vacío. Agregar el primer PR del repo aquí cuando esté listo el primer ritual de calibración.
