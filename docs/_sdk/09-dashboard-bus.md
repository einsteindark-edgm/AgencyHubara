# 09 — DashboardKit: el bus del dashboard (canal 1)

> Superficie SDK: `src.sdk.dashboardkit` · Implementación privada:
> `src.platform.events` · Fase: F-SDK-6 (drena la deuda P-28 que el SDK dejó
> grandfatherada en su PR fundacional).

## Qué problema soluciona

El dashboard del operador necesita reflejar cambios (pedidos nuevos, cambios de
etapa, snapshots de chat) **al instante**, sin pollear cada 30s. El **canal 1**
es ese push: los routers de cada plugin publican un evento y el dashboard —
suscrito a un único stream SSE multiplexado por dominio
(`/api/dashboard/events`, hosteado por el plugin chats) — invalida sus queries
por evento en vez de por reloj. Es el "Contrato F1" de la auditoría frontend
(2026-06-10): el dashboard deja de pollear y pasa a push.

**No confundir con `eventkit` (canal 2).** Son dos canales con roles opuestos:

| | **DashboardKit — canal 1** | **eventkit — canal 2** |
|---|---|---|
| Para qué | Refrescar la UI del dashboard | Coordinar workers entre sí |
| Durabilidad | **Efímero** (se pierde sin suscriptor) | **Durable** (Temporal, retry/replay) |
| Alcance | In-process (el uvicorn de la API) | Cross-worker (contenedores distintos) |
| Transporte | `asyncio.Queue` → SSE | Activity + dispatcher + manifest |
| Si nadie escucha | El evento se descarta | El target se arranca igual |

Usar canal 1 para coordinar workers (o canal 2 para refrescar la UI) es el
anti-patrón que esta separación previene.

## Cómo funciona por dentro

`src/platform/events/bus.py` — un fan-out mínimo, **in-process a propósito**:

- **Singleton del proceso API**: `get_dashboard_event_bus()` devuelve la única
  instancia. `run_api.py` corre **un** uvicorn (sin `workers=N`), así que un
  solo bus alcanza a todas las conexiones SSE.
- **`publish()` es sync y nunca bloquea**: escribe en la cola acotada
  (`maxsize=100`) de cada suscriptor. Con la cola llena descarta el evento
  **más viejo** — el publisher jamás se bloquea por un consumidor lento; el
  frontend reconcilia con un poll lento de fallback.
- **`DashboardEvent`** es un `@dataclass(frozen=True)` JSON-safe (`domain`,
  `type`, `id?`, `payload?`, `ts_ms`) con `to_sse()`. `domain` es un string
  **opaco** que el plugin elige (`"chats" | "orders" | "eta" | "catalog"`) —
  R-DIP: platform no conoce los plugins.
- **Los workers Temporal NO publican acá** (viven en otros contenedores). Sus
  mutaciones llegan al dashboard por el diff del vault compartido, que el
  sampler de `chats/api/dashboard.py` detecta y republica al bus.

### Límite consciente (no es un bug)

El bus es in-process **a propósito**. Si algún día la API escalara a
multi-proceso (`workers=N` o varias réplicas), un evento publicado en el
proceso A no llegaría a los suscriptores SSE del proceso B. Ahí este bus
necesitaría un backend compartido (Redis pub/sub) detrás de la **misma**
fachada `dashboardkit` — los plugins no cambiarían una línea. Es la frontera
natural de evolución, documentada para no "descubrirla" en producción.

## Cómo se usa

Publicar (en el router/api de cualquier plugin):

```python
from src.sdk.dashboardkit import get_dashboard_event_bus

def _publish_orders_changed(order_id: str | None = None) -> None:
    get_dashboard_event_bus().publish("orders", "changed", id=order_id)
```

Suscribir / servir el stream (lo hace el endpoint SSE en el plugin chats):

```python
from src.sdk.dashboardkit import DashboardEvent, get_dashboard_event_bus

queue = get_dashboard_event_bus().subscribe()
try:
    while True:
        event: DashboardEvent = await queue.get()
        yield event.to_sse()
finally:
    get_dashboard_event_bus().unsubscribe(queue)
```

Superficie pública: `get_dashboard_event_bus`, `DashboardEvent`,
`DashboardEventBus` (este último solo para type hints del lado suscriptor).

## Las 3 patas (regla de oro)

- **(a) Check**: `tests/architecture/test_p28_sdk_surface.py::test_p28_dashboardkit_reexports_platform_bus`
  exige **identidad** (`is`) entre los símbolos de `dashboardkit` y los de
  `platform.events` — la fachada re-exporta, no re-implementa (si lo hiciera,
  los plugins publicarían a un bus distinto del que suscriben los internos y el
  fan-out se partiría en silencio). El guard con dientes es el ratchet **P-28**:
  los 3 imports legacy de `src.platform.events` se **drenaron** de la allowlist,
  así que cualquier plugin que vuelva a importar el bus directo de platform
  rompe CI.
- **(b) CLI / template**: **N/A**. El `cli create` scaffolda manifest +
  workers/activities del arquetipo; publicar al dashboard es una decisión del
  router de cada plugin, no parte del esqueleto generado. Si en el futuro un
  arquetipo "api-first" scaffoldea un router con publicación al bus, esta pata
  se materializa ahí.
- **(c) Doc**: este archivo.

## Reglas que no se negocian

1. Plugins importan `src.sdk.dashboardkit` — **nunca** `src.platform.events`
   (drenado del ratchet P-28; reimportarlo directo rompe el gate).
2. Canal 1 (efímero, UI) ≠ canal 2 (durable, workers). No cruzar los roles.
3. La fachada solo re-exporta (regla 3 de `src/sdk/CLAUDE.md`): cero lógica en
   `dashboardkit.py`. La lógica del bus vive en `src/platform/events`.
