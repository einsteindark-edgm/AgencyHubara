# 14 · CatalogKit (catálogo de productos para plugins)

> Fuente: `src/sdk/catalogkit.py` · Check: `tests/platform/test_catalogkit.py` (identidad)

## Qué problema soluciona

Los plugins que leen el catálogo (`chats` en sus tools de venta, `catalog`
en su sync) importaban `src.platform.catalog` directo — 13 líneas congeladas
en `p28_platform_import_allowlist.txt`. No había fachada: cualquier tool
nueva que necesitara el port quedaba bloqueada por el ratchet P-28. Este kit
es esa fachada y el camino de drenaje.

También aloja los **predicados de producto** que dependen de datos del
catálogo. El primero nació de un incidente (run 943e6bff, 2026-09-07): la
despedida del cierre decía "se escogen los colores del portavelas" a un
cliente cuyo pedido no traía portavela. La política solo aplica a los
productos que lo incluyen (hoy el Dúo Zodiacal) y eso lo decide el CATÁLOGO,
no el LLM.

## Superficie

| Símbolo | Implementación | Rol |
|---|---|---|
| `CatalogPort` | `platform/catalog/port` | protocolo de lectura (`search`, `get_by_handle`, `list_categories`) |
| `CatalogProductDTO` / `CatalogVariantDTO` / `SearchResult` | `platform/catalog/dtos` | DTOs frozen del snapshot |
| `CatalogError` / `CatalogUnavailableError` / `ProductNotFoundError` | `platform/catalog/errors` | errores del port |
| `get_catalog_client` | `platform/catalog/composition` | factory singleton (snapshot local) |
| `product_includes_portavelas` / `order_includes_portavelas` | `platform/catalog/portavelas` | ¿el producto / algún ítem del pedido trae portavela? |
| `PORTAVELAS_METADATA_KEY` | `platform/catalog/portavelas` | `"portavelas"` — flag explícito en `metadata` de Medusa |

## `product_includes_portavelas` — cómo decide

1. `metadata.portavelas` explícito gana: `"true"/"1"/"si"` → sí,
   `"false"/"0"/"no"` → no. El operador prende/apaga la política por producto
   desde Medusa sin tocar código.
2. Sin flag: la mención `portavela` (cubre "portavelas") en título o
   description decide. Es lo que hoy identifica al Dúo Zodiacal ("un plato
   portavela de concreto pulido").

Conservador por diseño: un producto que no resuelve en el catálogo NO cuenta
como portavela (antes que hablarle del portavelas a quien no lo pidió).

## Cómo se usa

```python
from src.sdk.catalogkit import CatalogPort, ProductNotFoundError, product_includes_portavelas

async def portavelas_handles(catalog: CatalogPort, handles: list[str]) -> list[str]:
    found = []
    for h in handles:
        try:
            if product_includes_portavelas(await catalog.get_by_handle(h)):
                found.append(h)
        except ProductNotFoundError:
            continue
    return found
```

`RegisterOrderTool` hace exactamente eso y publica el resultado en su
envelope (`portavelas.included` + `order_registered.portavelas_included`); el
workflow de ventas usa el flag como guard determinista de la despedida
(`portavelas-notice-guard-v1`, ver spec `agents/sales-worker`).
