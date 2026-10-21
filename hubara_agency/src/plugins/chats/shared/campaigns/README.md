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
  cada producto y el total de cada combo (con si paga contra entrega o no).
- **Remarketing**, en cada toque: el texto de `toques` de ese número de toque
  (del último en adelante se repite el último), con los mismos precios. Sus
  `ejemplos.toques` reemplazan a los de la tienda.

## La oferta del cupón, con su fecha límite

El `cupon` de la campaña es el incentivo para no dejar la compra para después.
Su valor y su fecha límite salen de Medusa (dashboard → Marketing → Cupones):
si el cupón vence el 31 de octubre, el bot dice «hasta el 31 de octubre»; si le
cambias el «hasta» al 15 de noviembre, dice eso, sin deploy. Vencido,
desactivado o de primera compra para quien ya compró, no se ofrece. Con una
fecha a más de 45 días, se ofrece sin fecha. El bot nunca dice una fecha que el
cupón no tenga.

- **Ventas:** cuando el cliente aplaza la compra («te aviso», «hay tiempo», «más
  adelante»; lo lee Jev con la capacidad `compra`), el bot se la dice una vez
  por episodio y por fecha, en su respuesta breve. Si el cliente pregunta por
  descuentos, también la puede dar.
- **Remarketing:** solo el toque `toque_oferta` del episodio (cuentan los toques
  que le llegaron al cliente), y solo a quien quiso comprar y no compró. Quiso
  comprar = un pedido a medias, o Jev leyó en sus mensajes que eligió un
  producto, dio datos, aplazó o confirmó. Preguntar el precio no cuenta.
- Límite conocido: el toque con la oferta no deja marca en el episodio. Si
  después el cliente aplaza, Ventas la dice una vez más: a lo sumo dos veces
  por episodio, y la segunda solo como respuesta a un aplazamiento.
- No se ofrecen cupones con cupo por unidad, con un monto fijo por ítem ni con
  condiciones que la frase no dice (mínimo de compra, colecciones, etiquetas,
  variantes u otros productos). Tampoco en la posventa ni con un cupón ya
  aplicado.

## Envío gratis desde N cajas

`envio_gratis_desde_cajas: 6` (operador, 2026-10-20): un pedido SOLO de los
productos de la campaña (las cajas onduladas y cuadradas, se pueden mezclar)
con 6 cajas o más va sin costo de envío, a cualquier ciudad y con cualquier
medio de pago, mientras la campaña esté vigente. Con menos cajas, o si el
pedido trae otros productos, se cobra la tarifa de siempre (operador: «es solo
para las velas del 7 de diciembre»). Vacío = sin envío gratis.

- **Lo saben Ventas y remarketing:** el guion trae la línea del envío gratis;
  la tarjeta de tarifas («¿cuánto vale el envío?») la suma al final en las
  conversaciones de la campaña; el remarketing lo puede decir tal cual.
- **La cuenta la hace el sistema, no el bot:** el resumen, el registro y
  «Crear pedido» (dashboard y app) ponen el envío en $0 cuando el pedido lleva
  las cajas, y rechazan otro valor. El pedido queda en Medusa con el envío en
  $0, la línea «Envío gratis (6 cajas o más)» y la marca `hubara_free_shipping`.
- **Al despachar:** el costo real que escribe el operador lo paga la tienda.
  Queda anotado (aparte, «Envío que paga la tienda»), pero no se le suma al
  cliente: ni al total, ni al cobro de contra entrega, ni a Ads, ni al aviso de
  «va en camino».
- Solo pedidos del bot y de «Crear pedido»: la tienda web (Medusa) no cambia.
- Se suma al cupón de la campaña (VELITAS5: 5 % solo en estas dos cajas, solo
  primera compra).

## El perfil «interesado sin compra» (público de las campañas de WhatsApp)

A quien quiso comprar (lo mismo que arriba: eligió, aplazó, dio datos,
confirmó, o tiene un pedido a medias), Ventas le marca en vivo el perfil
`profile_tags.interesado_sin_compra`, con el episodio, de dónde salió y la
campaña. No depende de la etiqueta de la conversación, que cambia sola.

En Marketing → Campañas → Audiencia aparece como el segmento **Interesados sin
compra**. Se suma a los otros sin repetir a nadie; quien compró después por el
chat ya no cuenta (un pedido hecho solo en la tienda web no queda en la
conversación y no lo saca), y humano, baja y pruebas quedan fuera como siempre. Sirve para la tanda
de «5 % hasta tal fecha»: pon la fecha del cupón también en el mensaje de la
campaña.

Para marcar las conversaciones de antes de este cambio, en la caja, dentro del
contenedor de la API (solo devuelve números):

```bash
python scripts/backfill_interest_profile.py           # cuenta, no escribe
python scripts/backfill_interest_profile.py --apply   # marca
```

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
