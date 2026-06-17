# 04 · TestKit (el TCK) + certificación C0–C2

> Fase F-SDK-2 · Fuente: `hubara_agency/src/sdk/testkit/` · Gates: P-27 + self-test del testkit

## Qué problema soluciona

Python no compila la arquitectura. Antes, la conformidad de un plugin se
verificaba con una suite central que "policiaba" — y nada obligaba a un
plugin NUEVO a someterse (un dir sin tests pasaba CI silbando). El TestKit
emula el compilador: **el artefacto de conformance es parte del artefacto de
código** — plugin sin TCK = CI rojo = no mergea (P-27).

## Cómo funciona

Tres frontends, UNA fuente de checks (`src/sdk/testkit/checks.py`):

```
checks.py (funciones puras: AST + disco, cero red, cero imports del plugin)
   ├── conformance_suite(id)  → tests pytest   (tests/conformance/test_<id>_conformance.py)
   ├── run_conformance(id)    → CertificationReport (JSON p/ CI + catálogo)
   └── CLI check/certify      → salida estilo rustc (F-SDK-3)
```

- **El plugin INSTANCIA, no copia** (lección L-3): su archivo TCK son 3
  líneas. Un check nuevo en el SDK upgradea a los 7 plugins de una.
- **`CheckContext` con `repo_root` inyectable**: los checks corren igual
  contra el repo real o contra un skeleton en tmpdir — así el self-test
  (`test_testkit_selftest.py`) fabrica plugins rotos y prueba que el TCK los
  caza ("el gate que nunca falla es un gate roto"), y el golden del
  scaffolder (F-SDK-3) corre hermético.
- Cada fallo se renderiza con el **diagnóstico completo** (código + fix +
  ref, ver [03-diagnosticos.md](03-diagnosticos.md)) — el mensaje de error ES
  la documentación.

### Niveles

| Nivel | Significa | Cómo se computa |
|---|---|---|
| `none` | el manifest ni siquiera es válido | falla `C0-SCHEMA` o `P-29A` |
| `C0` — Declarado | manifest válido, pero algo declarado NO existe | falla algún `C1-*` |
| `C1` — Cargable | todo lo declarado existe, pero una P-rule falla | falla algún `P-*` |
| `C2` — Certificado | TCK completo verde (warnings permitidos y listados) | cero `fail` |
| `C3` — Verificado | conducta (specs + evals + smoke) | reservado (F-SDK-7) |

### El reporte

`run_conformance(id)` + `write_report()` →
`hubara_agency/.hubara/certification/<id>.json` (**gitignored** — es
derivable; committearlo invitaría drift). Lleva `git_sha` + `generated_at`:
un consumidor degrada un reporte stale a "sin certificar", jamás inventa
verde. **La certificación gobierna merge y catálogo — nunca el runtime.**

## Cómo se usa

```bash
# pytest (lo corre CI y la verificación §8):
cd hubara_agency && uv run pytest tests/conformance -q

# reporte de un plugin (programático):
cd hubara_agency && uv run python -c "
from src.sdk.testkit import run_conformance, write_report
print(write_report(run_conformance('eta')))"

# vía CLI (F-SDK-3):
cd hubara_agency && uv run python -m src.sdk.cli certify eta
```

Para un **plugin nuevo**: `hubara create plugin` genera el archivo TCK
automáticamente. A mano son 3 líneas en
`tests/conformance/test_<id>_conformance.py`:

```python
from src.sdk.testkit import conformance_suite

globals().update(conformance_suite("<id>"))
```

## Cómo se extiende (regla de oro)

Check nuevo ⇒ en el MISMO PR: (1) la función en `checks.py` (+ registrarla en
`ALL_CHECKS`), (2) su entrada en el catálogo de diagnósticos, (3) su caso
NEGATIVO en `test_testkit_selftest.py` (probar que caza), (4) doc acá si
cambia la semántica de niveles. Los 7 archivos de `tests/conformance/` NO se
tocan — eso es el punto.
