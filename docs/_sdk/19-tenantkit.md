# 19 · TenantKit (de qué tienda es cada cosa)

> Fuente: `src/sdk/tenantkit.py` → `src/platform/tenant/context.py` · Checks:
> `tests/platform/test_tenant_context.py` (comportamiento) y
> `tests/platform/test_tenantkit.py` (identidad, pureza y lector único)
> · Plan: `ACKTOS_STORES_PLAN.md` (F1.1) · ADR: `docs/adr/2026-10-22-acktos-stores.md`

## Qué problema soluciona

Acktos Stores es una plataforma donde cada tienda es configuración: un motor
para todas. Hasta ahora el motor era «un proceso = una tienda» a propósito, y
cada cosa (la carpeta del vault, los IDs de Temporal, la política comercial,
los clientes de Medusa) suponía la única tienda de la caja.

Para que un mismo proceso atienda varias tiendas, cada pieza tiene que saber
de qué tienda es lo que está haciendo, sin pasar la tienda de mano en mano por
cientos de firmas. La entrada la fija una vez y todo lo demás la lee.

## Cómo funciona

| Caja | Fuera de un alcance | Dentro de `tenant_scope(t)` |
|---|---|---|
| Hubara (sin variables nuevas) | `"hubara"`: el mismo motor como única tienda | `t` |
| Una sola tienda (`ACKTOS_TENANT=vincenzo`) | `"vincenzo"` | `t` |
| La celda (`ACKTOS_CELL=1`) | lanza `TenantNotInScope`, **nunca** otra tienda | `t` |

- Es una `ContextVar`: cada tarea de asyncio ve la suya. `asyncio.gather` y
  `create_task` copian el contexto al crear la tarea, así que dos tiendas
  atendidas a la vez no se ven.
- Al salir del bloque vuelve la tienda de antes, aunque el bloque falle: un
  turno que revienta no deja la tienda pegada para lo siguiente.
- El id se valida al entrar (`InvalidTenantId`), porque nombra recursos: la
  carpeta del vault, el prefijo de los IDs de Temporal, el árbol de SSM, la
  base de Medusa y el grupo de Cognito. Reglas del slug de forge: 2 a 20
  minúsculas o dígitos, empieza con una letra, sin `_` ni `-`; no puede ser
  `graphagents` ni `lab`, ni empezar por `aws`, `ssm` o `amazon`. Un id que
  llega de afuera (`../acme`) no llega a una ruta.
- Solo usa la stdlib: lo van a importar el webhook, las activities y los
  interceptores de Temporal (F1.2).

## Superficie

| Símbolo | Rol |
|---|---|
| `current_tenant()` | La tienda del alcance actual; fuera de uno, la de la caja (en la celda, error) |
| `tenant_scope(t)` | Context manager: todo lo que corre dentro es de `t` |
| `validate_tenant_id(t)` | `t` si puede nombrar los recursos de una tienda; si no, `InvalidTenantId` |
| `TenantNotInScope` | En la celda, código que corre sin que la entrada haya fijado la tienda |
| `InvalidTenantId` | Un id que no sirve para nombrar recursos |

Uso:

```python
from src.sdk.tenantkit import current_tenant, tenant_scope

with tenant_scope("vincenzo"):
    current_tenant()   # "vincenzo"
```

## Reglas

- **Nadie fuera de `src/platform/tenant/` lee `ACKTOS_TENANT` ni
  `ACKTOS_CELL`** (guard en `test_tenantkit.py`). Leerlas directo se salta el
  alcance y, en la celda, respondería una tienda que no es.
- **Hilos:** `asyncio.to_thread` copia el contexto; `loop.run_in_executor`
  **no**. Para correr en un hilo algo que lee la tienda, usar `to_thread` o
  `contextvars.copy_context().run`.
- **Cachés y globales:** nada que dependa de la tienda se guarda en una
  variable de módulo ni en un `lru_cache` sin la tienda en la llave (F1.5).

## Lo que viene (plan, F1)

- **F1.2:** un interceptor de Temporal lleva la tienda en un encabezado:
  workflow → activity → child workflow → señal.
- **F1.3 y F1.4:** la configuración de la tienda (`tenant_config()`) y su
  carpeta del vault (`vault_dir()`) se resuelven con `current_tenant()`.
- **F1.9 y F1.10:** el webhook `/webhook/<tienda>` y el claim de tienda del
  login abren el alcance en la entrada.
