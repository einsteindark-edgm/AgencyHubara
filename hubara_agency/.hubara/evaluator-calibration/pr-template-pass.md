# PR-NNN — <slug del PR>

> **Template para PRs que el humano calificó como `pass` (score ≥7.5).**
> Copiá este archivo a `pr-<NNN>-<slug>.md` y completalo.

## Metadata

- PR URL: https://github.com/einsteindark-edgm/AgencyHubara/pull/<NNN>
- Merged at: <YYYY-MM-DD>
- HU id: <HU-...>
- Branch: hu/<HU_ID>
- LOC changed: <e.g., +180 -45>
- Files touched: <N>
- Type: <frontend-only | backend-only | full-stack-agentic | refactor | platform>

## Resumen del PR (1 párrafo)

<Qué hace el PR y por qué. Esto le da contexto al evaluator si en el futuro re-evaluamos.>

## Human score (juicio del operador)

```yaml
architectural_compliance:
  score: <0-10>          # ¿R-rules + FSD cumplidas?
  rationale: |
    <1-2 oraciones citando archivo:línea si hay borderline cases>

test_coverage_real:
  score: <0-10>          # ¿tests verifican comportamiento o solo schema?
  rationale: |
    <1-2 oraciones>

visual_verification:
  applies: <true|false>
  score: <0-10|null>
  rationale: |
    <1-2 oraciones>

code_quality:
  score: <0-10>
  rationale: |
    <1-2 oraciones>

scope_discipline:
  score: <0-10>
  rationale: |
    <1-2 oraciones>

weighted: <calculated>    # sum(score * weight) / sum(weight)
verdict: pass
```

## ¿Qué hace de este PR un "pass"?

- <e.g., "Functional test cubre el flujo E2E end-to-end — no solo el endpoint, sino que verifica que el agente USA el tool nuevo en una conversación real">
- <e.g., "Refactor mínimo en composition.py — solo agrega una factory, no toca las existentes">
- <e.g., "Workspace TOOLS.md actualizado en sync con el código">

## ¿Algo subtle que el evaluator podría perderse?

<Insights del humano que no son obvios del diff. E.g.: "El PR agrega un campo opcional al Input dataclass con default — backwards-compat sin necesitar input_mapping. Es la opción correcta pero el evaluator podría flaguearlo como 'cambio de signature sin actualizar callers'. La rúbrica debería agarrar esto en architectural_compliance anchor 10.">

## Files diff (compacto)

```
<output de git diff --stat para este PR>
```

## Inputs disponibles al evaluator si re-ejecutamos

- [ ] hu-refinada.md existe en histórico (¿commit que lo introdujo?)
- [ ] task-result.yaml existe
- [ ] exploration-map.md existe (no aplica a PRs pre-Fase-2)
- [ ] feature-plan-manifest.yaml existe

(Si los inputs históricos no existen, este PR sirve solo como "ground truth de juicio" — no como reproducible test del evaluator.)
