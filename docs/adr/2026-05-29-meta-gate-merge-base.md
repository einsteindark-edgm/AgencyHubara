# ADR 2026-05-29 — Meta-gate compara contra merge-base, no contra `origin/main` directo

- **Status**: Accepted
- **Date**: 2026-05-29
- **Context surfacing run**: `78a6e0c4` (hu-hubara-pipeline, HU-20260527-194116)
- **Protected file touched**: `hubara_agency/tests/architecture/test_meta.py`

## Contexto

El meta-gate `test_protected_files_unchanged_vs_main` existe para bloquear
reescrituras silenciosas de la suite de arquitectura (`tests/architecture/`,
`.importlinter`, spinal files, `.archon/workflows/`, `.claude/skills/hubara-*`).
Su mecánica original:

```python
git diff --name-only origin/main --   # two-dot
```

Un **two-dot diff** compara el working tree contra `origin/main` **directamente**.
Eso marca como "modificado por la branch" CUALQUIER archivo donde
`branch != origin/main`, incluyendo archivos que **`main` avanzó** después de
que la branch forkeó o hizo su último merge.

### Falla observada

Durante el pipeline de HU-20260527-194116 ("conectar Agents dashboard"), el
harness recibió múltiples fixes al propio `.archon/workflows/hu-hubara-pipeline.yaml`
(watchdog de pytest, bootstrap de `.env`, detached HEAD, etc.). Cada fix se
mergeó a `main`, moviendo el archivo. La HU branch — que **nunca tocó el
workflow** — quedaba sistemáticamente "atrás" de main en ese archivo, y el
two-dot diff la marcaba como offender:

```
Meta-gate violation — architecture-protected files modified vs origin/main:
    .archon/workflows/hu-hubara-pipeline.yaml
```

Resultado: `final-validation` cancelaba el pipeline con un falso positivo. Mergear
`main → branch` lo arreglaba temporalmente, pero el siguiente fix al workflow
rompía de nuevo — un **moving target / loop infinito**.

## Decisión

Cambiar el meta-gate para diffear contra el **merge-base** entre `origin/main`
y `HEAD`, en vez de contra `origin/main` directamente:

```python
diff_base = git merge-base origin/main HEAD
git diff --name-only $diff_base --
```

Esto es exactamente la semántica de **PR-diff** que usa GitHub Actions
(`pull_request` events) y `git diff main...HEAD`: reporta SOLO lo que introdujo
la branch desde su punto de fork/último-merge, ignorando lo que `main` avanzó
en paralelo.

### Por qué la protección se mantiene intacta

- Si la branch **genuinamente** modifica un archivo protegido en uno de sus
  propios commits (posterior al merge-base), el diff merge-base→HEAD lo
  **sigue detectando**. El caso de uso real del gate (un implementer AI que
  toca `tests/architecture/` para "hacer pasar" un test) queda cubierto.
- Solo se elimina el **falso positivo** por divergencia de `main`.
- Fallback defensivo: si `git merge-base` falla (historias no relacionadas),
  el gate cae al comportamiento two-dot original (`base`) — nunca se
  auto-desactiva silenciosamente.
- El bypass `ARCH_CHANGE_APPROVED=1` sigue siendo el único override explícito.

## Consecuencias

- **Positivas**: el harness puede iterar sobre `.archon/workflows/` en `main`
  sin romper las HU branches en vuelo. El gate deja de dar falsos positivos
  por skew con `main`.
- **Neutrales**: en CI (PR contra main reciente) el merge-base suele ser el
  fork point, así que el comportamiento efectivo es el mismo que se esperaba
  originalmente — el two-dot solo coincidía con merge-base cuando la branch
  estaba perfectamente sincronizada con main.
- **A vigilar**: si en el futuro se quiere enforcement aún más estricto
  (checksum pinneado por archivo, Capa 5 del docstring de `test_meta.py`),
  este cambio no lo bloquea — son capas complementarias.

## Alternativas descartadas

1. **Setear `ARCH_CHANGE_APPROVED=1` permanente en el pipeline**: desactivaría
   la protección para TODAS las HUs. Inaceptable — el gate dejaría de proteger.
2. **Merge `main → branch` antes de cada run**: no escala, es el loop infinito
   que motivó este ADR.
3. **Excluir `.archon/workflows/` de los prefijos protegidos**: perdería la
   protección sobre los workflows del orquestador, que SÍ son arquitectura.
