# 03 · El panel determinístico (comandos exactos de verificación)

> Distilación de `ARCHITECTURE_FINAL_fable.md §8`. Cada gate es un comando con
> exit code: no se razona el "¿está bien?", se ejecuta. El detalle por gate
> (qué caza cada uno, exit codes, niveles C0–C2) está en §8.

## La regla de los dummies (determinismo real)

El trío `MEDUSA_BASE_URL=http://medusa.invalid MEDUSA_ADMIN_TOKEN=ci-dummy
OTEL_SDK_DISABLED=true` se antepone a CADA `pytest`/CLI de backend. **El shell
no persiste env entre llamadas** — inline el trío en cada comando, un `export`
no alcanza. Vale para `tests/architecture`, `tests/plugins`, `tests/conformance`
y el CLI; NUNCA para `tests/platform/` (cuelga en retries HTTP).

El prefijo `cd hubara_agency &&` / `cd frontend_dashboard &&` es obligatorio
(hook pre-bash), aunque ya estés ahí.

## El comando único (definition-of-done de cualquier cambio)

```bash
# Backend
cd hubara_agency && uv run lint-imports && \
  MEDUSA_BASE_URL=http://medusa.invalid MEDUSA_ADMIN_TOKEN=ci-dummy OTEL_SDK_DISABLED=true \
  uv run pytest tests/architecture tests/plugins -q
# Frontend
cd frontend_dashboard && npm run plugins:sync && npx tsc -b && \
  npm run test:arch && npm test
```

Con el SDK presente, la DoD suma la certificación: `… uv run pytest tests/conformance -q`.

El comando `/hubara-gates` corre todo esto por vos y reporta el resultado.

## Gates sueltos (cuándo correr cada uno)

- `cd hubara_agency && uv run lint-imports` — R-DIP (aislamiento de imports).
- `… uv run pytest tests/architecture -q` — R-rules + plugin-contract +
  orquestación + meta-gate.
- `… uv run pytest tests/conformance -q` — TCK por plugin (P-27).
- `… uv run python -m src.sdk.cli check` — el compilador rápido (sin red).
- `cd frontend_dashboard && npm run test:arch` — FSD + íconos + meta-gate front.

PROTECTED tocado ⇒ prefijo `ARCH_CHANGE_APPROVED=1` local + label
`architecture-change` en el PR (cómo lo ve CI: L-14). Repro local verde + CI
rojo en un gate de allowlist ⇒ staleness, mergeá main + regenerá (L-15).

---
Fuente canónica: `ARCHITECTURE_FINAL_fable.md §8`. Si difiere del código vivo,
gana el código vivo.
