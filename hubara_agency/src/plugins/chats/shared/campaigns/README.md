# Guiones de venta por campaña

Cada archivo `<id>.yaml` de esta carpeta es el guion de venta de UNA campaña:
lo que el bot hace distinto con los productos de esa campaña. Lo lee
`chats/shared/sales_campaigns.py`.

## A quién le llega

Solo a las conversaciones de la campaña. Una conversación es de la campaña si:

- viene de uno de sus anuncios (las palabras de `anuncios` en el título o el texto
  del anuncio, o en el mensaje prellenado), o
- su pedido tiene uno de sus `productos`.

La primera vez, el ingest marca el episodio (`sales_campaign: <id>`): desde ahí,
Ventas y el remarketing siguen esa campaña en ese episodio. Fuera de las fechas
(`desde`/`hasta`) o con `activa: false`, el guion no le llega a nadie.

Las demás conversaciones no reciben nada nuevo. Lo prueban
`tests/plugins/chats/test_sales_campaigns.py`,
`tests/plugins/chats/sales/test_sales_campaign_note.py` y
`tests/plugins/chats/test_remarketing_sales_campaign.py`.

## Qué recibe el bot

- **Ventas**, en cada turno: una nota con el guion, los precios del catálogo de
  cada producto, el total de cada combo (con si paga contra entrega o no) y el
  cupón. El cupón lo ofrece solo si `list_promotions` dice que aplica a ese
  cliente.
- **Remarketing**, en cada toque: el texto de `toques` de ese número de toque
  (del último en adelante se repite el último), con los mismos precios y el cupón
  si está vigente en Medusa (el valor sale de Medusa). Sus `ejemplos.toques`
  reemplazan a los de la tienda.

## Reglas

- **Nunca escribas un precio en el archivo.** Los precios, el combo y el mínimo de
  contra entrega salen del catálogo y del código. Un precio escrito aquí se
  desfasa de Medusa, y la prueba lo frena. En los ejemplos usa «[su precio]».
- Los datos del producto (aromas, presentaciones, precio por mayor) viven en
  Medusa: el guion dice qué hacer con ellos, no los repite.
- Español de Colombia con tú (nunca vos).

## Campaña nueva o cambio de guion

1. Copia un archivo, renómbralo (`id` = nombre del archivo) y cambia `nombre`,
   fechas, `productos` (handles de Medusa), `anuncios`, `guion`, `toques` y
   `ejemplos`.
2. Prueba: `cd hubara_agency && uv run pytest -q tests/plugins/chats/test_sales_campaigns.py`.
3. PR, merge y deploy del backend. Cambiar una campaña no toca el paquete de
   decisiones ni a las otras campañas.

Para apagar una campaña en caliente no hace falta borrarla: `activa: false`
(también con PR y deploy).
