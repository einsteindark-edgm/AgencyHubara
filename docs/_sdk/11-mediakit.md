# 11 · MediaKit (media saliente + etiquetas de imagen)

> Fuente: `src/sdk/mediakit.py` · Check: `tests/platform/test_mediakit.py` (identidad)

## Qué problema soluciona

Los plugins necesitan tres cosas de media sin importar `src.platform` directo
(P-28): persistir una foto saliente en el vault y exponerla por URL servible,
subir sus bytes a Meta para obtener un `media_id`, y derivar la **etiqueta de
diseño** del filename de una imagen del catálogo.

## Superficie

| Símbolo | Implementación | Rol |
|---|---|---|
| `persist_outbound_image` / `media_url_for` / `is_safe_segment` / `delete_outbound_image` | `platform/media/store` | storage vault + URL + guard anti-traversal |
| `upload_media` / `MediaUploadError` | `platform/whatsapp/client` | bytes → `media_id` de Meta |
| `derive_image_label` | `platform/catalog/image_labels` | `Leo-01KX...webp` → `"Leo"` |

## `derive_image_label` — por qué vive en platform

El operador nombra las fotos del producto por diseño (`Leo-*.webp`,
`Escorpion-*.webp`) y Medusa preserva ese filename en la URL del asset. La
etiqueta derivada es metadata de producto que consumen DOS lados:

- **chats** (tools `search_products`/`get_product_by_handle`/`present_*`):
  lista cerrada de `designs` + elegir la foto del diseño pedido.
- **catalog → Meta** (`platform/meta_catalog/mapper.py`, 2026-07-16): matchear
  la imagen de cada variante (`item_group_id` per-variante, caso Duo Zodiacal).

Por eso el módulo vive en `src/platform/catalog/image_labels.py` (platform no
puede importar plugins) y los plugins lo consumen vía este kit.

## Cómo se usa

```python
from src.sdk.mediakit import derive_image_label

derive_image_label("https://assets.hubara.com.co/Leo-01KXM9VD...webp")  # "Leo"
derive_image_label("https://assets.hubara.com.co/img1.webp")            # None
```

`None` significa "este filename no nombra un diseño" — el caller NO inventa
una etiqueta en su lugar.
