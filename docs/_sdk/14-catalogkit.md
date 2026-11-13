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
| `CatalogCategoryDTO` / `CategoryResolution` / `resolve_category` | `platform/catalog/categories` | lista cerrada de categorías (con `promotional`: marcada como promoción en Medusa) y su resolver (regla de hoy de la capacidad `categoria` del motor de decisiones) |
| `ColorFamilies` / `resolve_color_family` / `family_of_color` | `platform/catalog/color_families` | tabla de familias de color del tenant y su resolver (regla de hoy de la capacidad `familia_de_color`) |
| `normalize_label` | `platform/catalog/variant_attrs` | normalización para comparar etiquetas (sin tildes, casefold) |
| `stem_word` | `platform/catalog/variant_attrs` | una palabra ya normalizada sin género ni número («rojas» → «roj», «azules» → «azul»): la única regla para comparar palabras del catálogo (familias de color, colores por variante, scorecard de envío, verdad del producto) |

## `product_includes_portavelas` — cómo decide

1. `metadata.portavelas` explícito gana: `"true"/"1"/"si"` → sí,
   `"false"/"0"/"no"` → no. El operador prende/apaga la política por producto
   desde Medusa sin tocar código.
2. Sin flag: la mención `portavela` (cubre "portavelas") en título o
   description decide. Es lo que hoy identifica al Dúo Zodiacal ("un plato
   portavela de concreto pulido").

Conservador por diseño: un producto que no resuelve en el catálogo NO cuenta
como portavela (antes que hablarle del portavelas a quien no lo pidió).

## Categorías de promoción — la marca vive en Medusa

Una categoría es de promoción cuando el operador le pone en Medusa (Admin →
Categoría → Metadatos) la llave `promocion` con el valor `true` (también `si`,
`1`, `yes`, `on`; la llave sin importar tildes ni mayúsculas). El sync la lleva
al snapshot (`CatalogProductDTO.promotional_categories`) y la lista cerrada la
expone en `CatalogCategoryDTO.promotional`. `list_promotions` del bot de ventas
la cuenta junto a los cupones: «¿qué promociones tienen?» muestra esa categoría.
Una promoción nueva = una categoría marcada + Sync, sin desplegar (incidente
2026-11-12, «Promociones Navideñas»).

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
