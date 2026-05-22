# PR-NNN — <slug del PR> (FAIL case)

> **Template para PRs que el humano calificó como `block_merge` o `warn` con issues legítimos.**
> Estos son los casos MÁS valiosos para calibración — captan los patrones que el evaluator
> debe aprender a detectar.

## Metadata

- PR URL: https://github.com/einsteindark-edgm/AgencyHubara/pull/<NNN>
- Merged at: <YYYY-MM-DD | NEVER (rejected)>
- HU id: <HU-...>
- Branch: hu/<HU_ID>
- LOC changed: <e.g., +280 -32>
- Files touched: <N>
- Type: <frontend-only | backend-only | full-stack-agentic | refactor | platform>
- **Post-merge incident:** <yes / no — si yes, link al incident>

## Resumen del PR (1 párrafo)

<Qué hace el PR. Incluí qué SALIÓ MAL si llegó a producción.>

## Human score (juicio del operador)

```yaml
architectural_compliance:
  score: <0-6>
  hard_threshold_failed: <true|false>
  rationale: |
    <Identificá la regla violada. Citar archivo:línea.
     Ejemplo: "Line 23 de tools/foo.py importa directly from
     src.plugins.sales — viola R-DIP cross-plugin. Debería usar
     composition root via registries.">

test_coverage_real:
  score: <0-6>
  rationale: |
    <Por qué los tests no capturaron el bug. Ejemplo: "Tests
     mockean el LLM call y verifican que `chat()` retorna sin
     error, pero el bug es que cuando el agent decide transfer
     a sales, el worker target no recibe el signal. Test no
     verifica el comportamiento observable end-to-end.">

visual_verification:
  applies: <true|false>
  score: <0-6|null>
  rationale: |
    <Si HU es de UI y el PR no incluye Playwright spec, anchor 0.>

code_quality:
  score: <0-6>
  rationale: |
    <e.g., "Función handle_message de 90 líneas, 5 niveles de
     anidamiento. 3 magic strings sin nombrar. Imports innecesarios.">

scope_discipline:
  score: <0-6>
  rationale: |
    <e.g., "PR pretende agregar 1 tool nuevo, pero también
     refactoreó 3 helpers no relacionados y cambió formatting
     en 12 archivos. §13 OOS del refinement no flageaba esto.">

weighted: <calculated>
verdict: <block_merge | warn>
```

## El bug / issue específico (la lección)

> **Esto es lo CRÍTICO para calibración.** Si el evaluator out-of-the-box no detecta este patrón,
> hay que enseñarle.

<Descripción detallada del bug en 1-3 párrafos. Incluí:
 - Qué hace mal el código (citar líneas).
 - Por qué tests no lo agarraron.
 - Cuándo se descubrió (post-merge / pre-merge code review / etc.).
 - Quién pagó el costo (rollback, hotfix, customer report).>

## ¿Qué señales del diff señalaban el bug?

<Características del diff que un evaluator BIEN ENTRENADO debería notar.
 Ejemplo:
 - "Imports de `sibling_plugin` sin pasar por composition root → red flag R-DIP."
 - "Tests con `mock_llm.return_value = {...}` sin verificar follow-up actions."
 - "Función nueva con un parámetro `**kwargs` sin docstring — opacidad de contrato.">

## ¿Cómo entrenar al evaluator a detectarlo?

<Sugerencia concreta para el SKILL.md del evaluator. Ejemplo:
 - "Agregar un few-shot en §4.3 con este caso, mostrando: input → score esperado por criterio → razonamiento."
 - "Endurecer el anchor 4 de `code_quality` para mencionar 'imports innecesarios' como red flag."
 - "Agregar a `auto_check_commands` un grep por imports cross-plugin: grep -E 'from src.plugins.(?!<current_plugin>)' src/plugins/<current_plugin>/">

## Files diff (compacto)

```
<output de git diff --stat>
```

## Lecciones para la rúbrica (si aplica)

<Si este PR revela que la rúbrica es deficiente — e.g., un criterio NO existente que debería:
 - "La rúbrica no tiene criterio explícito para 'manejo de errores'. Cuando una task agrega un nuevo path code que puede fallar (HTTP call, DB query), debería evaluar manejo de excepciones, retries, etc.">

Si querés agregar criterios nuevos a la rúbrica, hacelo en un PR aparte (cambio de rúbrica) — no
en el calibration corpus.
