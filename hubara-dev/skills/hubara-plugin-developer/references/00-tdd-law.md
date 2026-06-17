# 00 · La ley TDD (rojo → verde → refactor, sin excepción)

> Distilación operativa de `ARCHITECTURE_FINAL_fable.md §3.5`. El *porqué*
> completo está allá; acá está el *cómo*, para ejecutar.

## Las tres leyes (se cumplen las tres, siempre)

1. **No escribís código de producción** hasta tener un test que **falla** y lo
   exige.
2. **No escribís más test** del mínimo suficiente para fallar (un
   `ImportError`/error de colección NO es un rojo válido).
3. **No escribís más código de producción** del mínimo suficiente para pasar
   ese único test rojo.

Pasos de **minutos**, un comportamiento atómico por vuelta. Si escribís 5 tests
antes de una línea de código, o 50 líneas para "un" test, rompiste el paso.

## El bucle

- **ROJO** — escribí el test del siguiente incremento y **velo fallar con un
  assert con sentido** (`AssertionError: esperaba X, vino Y`). Un rojo por la
  razón equivocada (import roto, fixture faltante) es un *falso rojo*: el test
  todavía no prueba nada. El nombre del test ES la spec:
  `test_<sujeto>_<condición>_<resultado>`.
- **VERDE** — el mínimo código para pasar. Hardcodear el primer caso es
  legítimo; el segundo test (triangulación) te obliga a generalizar.
- **REFACTOR** — con el test de red, limpiá test Y producción; mantené **todo
  el panel §8 verde**. Si el test es feo de escribir, el diseño está mal —
  arreglá el diseño, no el test (el test es el primer consumidor de tu API).

## Qué harness usa cada capa (dónde NACE el test, primero)

| Capa | Harness | Vive en |
|---|---|---|
| Dominio / use-case puro | pytest directo, sin mocks salvo ports inyectados | `hubara_agency/tests/test_<x>.py` |
| Activity | `ActivityEnvironment().run(activity, ...)` + `monkeypatch`/`tmp_path` | `tests/test_<x>_activity.py` |
| Workflow | `WorkflowEnvironment.start_time_skipping()` + activities fake con tracker (R-DET lo hace 100% determinista) | `tests/test_<x>_workflow*.py` (patrón: `test_sales_workflow_debounce.py`) |
| Tool del agente | `execute_with_context(ctx, **params)` con fakes; assert sobre el decision payload | `tests/test_<tool>_tool.py` |
| Frontend entity | Zod parsea un fixture del shape REAL del backend | `entities/<e>/contracts.test.ts` |
| Frontend feature | vitest sobre comportamiento (no implementación) | `features/<f>/...test.tsx` |
| Gate / check nuevo | el caso **NEGATIVO primero**: fabricá el estado roto y probá que el gate lo CAZA | `tests/architecture/test_testkit_selftest.py` |

## Bug encontrado en producción ⇒ guard ROJO antes del fix

Cuando un run real revela un bug, el **primer** artefacto es un test que
**reproduce el incidente y falla**; recién entonces el fix lo pone verde. El
"Guard" de una lección se escribe ANTES que el "Fix". El guard rojo es la
definición de "entendí el bug". (R-DET + time-skipping hacen que hasta las
carreras de workflow se reproduzcan en un test — ver L-13.)

## No es TDD (rechazá esto)

- Código primero y "después los tests" (invierte la presión de diseño).
- Asertar sobre **implementación** (orden de pasos internos) en vez de
  **comportamiento observable** (output, decision payload, estado).
- Over-mock: si mockeás medio mundo, la unidad hace demasiado (señal de diseño).
- Un test que **no puede fallar** (sin assert real, pasa con el código borrado).

## El atajo mental

Antes de tocar producción: *"¿cuál es el test que falla y exige esto?"* Si no
podés nombrarlo, todavía no entendés el incremento.

---
Fuente canónica: `ARCHITECTURE_FINAL_fable.md §3.5`. Si difiere del código
vivo, gana el código vivo (y es lección nueva para §9).
