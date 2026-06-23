# 10 — CastKit: el cast HTTP cross-plugin con identidad (canal 3)

> Superficie SDK: `src.sdk.castkit` · Lógica propia (submódulo con tests, como
> `testkit`/`connectorkit`) · Fase: hardening de auth (2026-06-23) — formaliza
> el **canal 3** (consumo de datos entre plugins) como kit.

## Qué problema soluciona

El **canal 3** del plugin system es cómo un plugin consume datos de otro: en vez
de importar su módulo (P-3 lo prohíbe), declara `depends_on` + `consumes` en su
manifest y hace un **self-call HTTP al contrato publicado** del provider
(`/api/<provider>/...`) — un loopback a la MISMA app FastAPI
(`http://127.0.0.1:8000` por default). Los casts canónicos: `agents_admin→chats`
(`api/evals`) y `chats→orders` (`api/order_actions`).

Hasta 2026-06-23 cada cast tenía su propio `_forward` copy-pasteado con `httpx`.
Cuando se colgó `require_auth` (JWT/Cognito) en **todos** los routers
(`main.py`), el 2º hop del cast volvió a pasar por la auth — pero los `_forward`
**no reenviaban el header `Authorization`**. Resultado: el provider respondía
`401 {"detail":"Falta el bearer token"}` y el cast lo re-envolvía con
`resp.text` → `detail` **doble-anidado**
(`{"detail":"{\"detail\":\"Falta el bearer token\"}"}`). Síntoma para el
operador: las vistas de "Calidad LLM" (history, conversations) y el canvas de
pago del chat dejaban de cargar, aunque el token era válido en el edge.

Causa raíz: **el hop interno cruza el trust boundary de auth sin portar la
identidad ya validada en el edge.** La cura es centralizar el cast en un único
helper con una regla:

> **Todo cast loopback PORTA la identidad del request entrante.**

**No confundir con los otros canales:**

| | **castkit — canal 3** | **dashboardkit — canal 1** | **eventkit — canal 2** |
|---|---|---|---|
| Para qué | Leer datos de otro plugin | Refrescar la UI | Coordinar workers |
| Forma | Self-call HTTP (req/resp) | `asyncio.Queue` → SSE | Activity + dispatcher |
| Identidad | **Porta el `Authorization`** | in-process (sin auth) | durable (Temporal) |
| Alcance | mismo proceso API (loopback) | in-process | cross-worker |

## Cómo funciona por dentro

`src/sdk/castkit.py` — `async def forward(request, method, path, *, base_url,
timeout, cast_label, params=None, body=None) -> dict`:

- **Porta la identidad**: copia el header `Authorization` del `request` entrante
  al hop del provider. Sin sesión (dev local / tests con auth no-op) el request
  no trae el header → no se inventa nada (el provider tampoco lo exige). El
  `traceparent` de OTel lo propaga la instrumentación de `httpx` (OpenLIT)
  automáticamente; el kit solo añade `Authorization`.
- **Desanida el `detail`**: ante un `4xx/5xx` del provider devuelve el `detail`
  **interno** (`resp.json()["detail"]`), no el `resp.text` crudo — así no se
  produce el doble-anidado. Si el body no es JSON (p.ej. un 502 de un proxy
  intermedio), cae a `resp.text` sin romper.
- **Semántica honesta de fallos (L-1, generalizada a TODO cast)**: un
  connect-error garantiza no-aplicación → **502** ("la operación NO se aplicó");
  un timeout deja el resultado DESCONOCIDO → **504** ("PUEDE haberse aplicado"),
  nunca afirmando que la operación falló. El `timeout` lo fija cada cast (se
  dimensiona por el UPSTREAM del provider, no por el hop local — L-1).
- **`cast_label`** (`origen→provider`) etiqueta los mensajes de error para que
  el diagnóstico diga qué cast falló.

El swap del provider (multitenant) sigue siendo "tocar SOLO el `_forward` del
cast" — el `base_url` se resuelve por env en cada cast (`CHATS_API_BASE`,
`ORDERS_API_BASE`). La mecánica (auth, timeouts, errores) ya no se duplica.

## Cómo se usa

En el router del plugin que consume (el cast declarado en su manifest), cada
endpoint toma `request: Request` y lo pasa al helper:

```python
from fastapi import APIRouter, Query, Request
from src.sdk import castkit

router = APIRouter()
_TIMEOUT_S = 15.0


def _provider_base() -> str:
    return os.environ.get("CHATS_API_BASE", "http://127.0.0.1:8000").rstrip("/")


@router.get("/evals/history")
async def eval_history(
    request: Request,
    days: int = Query(default=30, ge=1, le=365),
) -> dict:
    return await castkit.forward(
        request, "GET", "/api/chats/evals/history",
        base_url=_provider_base(), timeout=_TIMEOUT_S,
        cast_label="agents_admin→chats", params={"days": days},
    )
```

Superficie pública: `castkit.forward`.

## Las 3 patas (regla de oro)

- **(a) Check**: dos gates. `tests/architecture/test_castkit_loopback.py` —
  prohíbe que un archivo de plugin abra un cliente `httpx` apuntando a loopback
  `:8000` por fuera del castkit (caza la clase de bug para todo cast futuro;
  connectors a vendors externos siguen libres). `tests/test_castkit.py` — el
  contrato del helper: propaga `Authorization`, desanida el `detail`, 502/504
  honestos. El guard del incidente end-to-end vive en
  `tests/test_casts_auth_integration.py` (auth ON, loopback reentra a la app y
  atraviesa `require_auth`).
- **(b) CLI / template**: **N/A**. El `cli create` scaffolda manifest +
  workers/activities del arquetipo; declarar un cast es una decisión del plugin
  que consume, no parte del esqueleto generado. Si un arquetipo futuro
  scaffoldea un router con un cast, esta pata se materializa ahí.
- **(c) Doc**: este archivo.

## Reglas que no se negocian

1. Un cast loopback va por `src.sdk.castkit.forward` — **nunca** `httpx` directo
   a `:8000` desde un plugin (el gate `test_castkit_loopback` lo frena).
2. El endpoint del cast SIEMPRE toma `request: Request` y lo pasa al helper —
   sin el request no hay identidad que portar y el 2º hop da 401.
3. Canal 3 (cast, req/resp, mismo proceso) ≠ canal 1 (UI) ≠ canal 2 (workers).
4. El `timeout` se dimensiona por el upstream del provider, no por el hop local
   (L-1); por eso es un parámetro del cast, no una constante del kit.
