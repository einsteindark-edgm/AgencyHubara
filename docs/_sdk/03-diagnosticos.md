# 03 · Catálogo de diagnósticos (código → fix)

> Fase F-SDK-1 · Fuente: `hubara_agency/src/sdk/diagnostics.py`

## Qué problema soluciona

Los gates frenaban con asserts crípticos dispersos por 14 archivos de test.
El conocimiento "qué te frenó y cómo se arregla" vivía en una tabla de un doc
(ARCHITECTURE_FINAL §3) que ningún programa podía leer. El catálogo lo vuelve
**machine-readable y único**: el mismo registro alimenta los mensajes del
TestKit, el `explain` del CLI y los tooltips de la cuarentena del catálogo.

## Cómo funciona

`DIAGNOSTICS` es un dict `código → Diagnostic(code, title, severity, fix,
ref)`. Render estilo rustc:

```
error[P-16]: worker usa la task queue de OTRO plugin
  --> src/plugins/eta/workers/eta.py
  fix: get_task_queue("<tu-plugin>", "<tu-worker>") — la queue se declara en TU manifest
  ref: ARCHITECTURE_FINAL_fable.md §3
```

Familias de códigos:

| Familia | Qué cubre |
|---|---|
| `C0-*` | forma del manifest (schema/modelo) — nivel "Declarado" |
| `C1-*` | cargabilidad: módulos/entries/deps declarados existen — nivel "Cargable" |
| `P-*` | los gates del plugin system (pre-existentes P-6..P-26 y nuevos P-27/P-28/P-29/P-31) |

## Cómo se usa

```python
from src.sdk import get_diagnostic, format_diagnostic

get_diagnostic("P-28").fix          # el fix de una línea
print(format_diagnostic("P-29", "falta dir agent/<worker>/use_cases", location="src/plugins/x"))
```

```bash
# Desde el CLI (F-SDK-3):
cd hubara_agency && uv run python -m src.sdk.cli explain P-27
```

## Cómo se extiende (regla de oro)

Gate/check nuevo ⇒ su entrada en `DIAGNOSTICS` en el MISMO PR (el TestKit
referencia códigos del catálogo; un código sin entrada lanza
`UnknownDiagnosticError` en los tests del propio catálogo). El `fix` se
escribe imperativo y accionable — es lo que un dev (o un agente del pipeline)
va a ejecutar a ciegas.
