# ADR 2026-09-04 — Allowlist explícita de routers públicos (`PUBLIC_ROUTER = True`)

- **Status**: Accepted
- **Date**: 2026-09-04
- **Context surfacing**: revisión del PR #231 (plugin `mba`, hallazgo 6 del gate-reviewer)
- **Protected file touched**: `hubara_agency/tests/architecture/test_public_routers.py` (nuevo)

## Contexto

`src/main.py` (`_register_router_from_module`) monta los routers de cada plugin
con `Depends(require_auth)` por defecto (fail-closed). La excepción es una
línea a nivel módulo:

```python
PUBLIC_ROUTER = True
```

Con ese flag el router se monta SIN la auth del shell. Existe porque hay
integraciones que Meta invoca directamente y que traen su propia auth:

| Módulo | Quién lo llama | Auth propia |
|---|---|---|
| `src.plugins.chats.api.sales` | Meta, webhook de WhatsApp | `verify_token` en GET + HMAC `X-Hub-Signature-256` en POST |
| `src.plugins.mba.api.connector` | Meta Business Agent, connector tools | header `X-API-Key` = `HUBARA_MBA_API_KEY`, fail-closed (503 sin la variable, 401 sin key) |

Hasta hoy **ningún gate enumeraba** qué módulos pueden llevar el flag. Cualquier
plugin podía abrir rutas al público con una línea y todos los gates seguían
verdes. Viola la regla de oro de `ARCHITECTURE_FINAL_fable.md §4.5`: un campo o
flag que cambia el comportamiento del sistema lleva su check.

## Decisión

Agregar `hubara_agency/tests/architecture/test_public_routers.py`:

- Escanea `src/plugins/**/*.py` buscando `^PUBLIC_ROUTER\s*=\s*True`.
- Asserta que el set encontrado es **exactamente** `PUBLIC_ROUTER_ALLOWLIST`,
  un dict `módulo → "quién lo llama + auth propia"`. Igualdad exacta: caza
  tanto un módulo público nuevo como una entrada stale.
- Self-test del detector: `chats.api.sales` debe aparecer en el scan (si el
  regex deja de matchear, el guard no queda vacío en silencio).

Agregar una entrada a la allowlist es una decisión de arquitectura: exige auth
propia en el módulo, review y (por vivir en `tests/architecture/`) el label
`architecture-change`.

## Consecuencias

- Un plugin que declare `PUBLIC_ROUTER = True` sin entrar en la allowlist rompe
  `pytest -m architecture` con el mensaje de qué encontró y qué esperaba.
- El guard no valida la auth propia del módulo (eso lo cubren los tests del
  plugin: `tests/plugins/chats` para el HMAC, `tests/plugins/mba/test_api.py`
  para la API key). Solo garantiza que la decisión de abrir un router sea
  explícita y revisada.
- Este PR va apilado sobre `feat/mba-plugin` (#231) porque la segunda entrada
  de la allowlist es el connector de `mba`; hasta que #231 mergee, en `main`
  el guard fallaría por allowlist stale.
