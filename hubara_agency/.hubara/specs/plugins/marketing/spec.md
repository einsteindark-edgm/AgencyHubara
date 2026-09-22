# Capability: plugins/marketing

## Purpose

Campañas directas de WhatsApp desde el dashboard (sección Marketing): el
operador arma un mensaje dentro de una plantilla MARKETING aprobada por Meta,
elige la audiencia y la envía o programa. Bootstrap incremental (2026-09-21):
solo cubre la audiencia importada y el carrusel de productos; el resto del
plugin (segmentos, curaduría, atribución) se documenta cuando una HU lo toque.

### Requirement: Audiencia importada desde un archivo

El operador MUST poder subir un CSV/TXT de teléfonos como audiencia extra de
la campaña. Los números importados MUST recibir la campaña aunque nunca hayan
chateado con el bot; si SÍ tienen sesión, rigen las mismas exclusiones que un
agregado manual (humano / opt-out son absolutos; quiet hours protege siempre;
saltan el cooldown de 48 h).

#### Scenario: Importar un CSV con encabezado

- GIVEN un archivo `nombre,telefono` con celulares colombianos con o sin indicativo
- WHEN `POST /api/marketing/campaigns/{id}/contacts/import` recibe el archivo (multipart, ≤ 1 MB, texto)
- THEN cada número válido se normaliza a E.164 sin `+` (`3001234567` → `573001234567`) y se guarda en `imported_contacts` con su nombre
- AND la respuesta resume importados / repetidos / rechazados con número de línea y razón (`numero_invalido`, `sin_columna_telefono`)
- AND el mismo teléfono no se duplica entre importaciones

#### Scenario: Envío a un importado sin conversación previa

- GIVEN un contacto importado sin `metadata.json` en el vault
- WHEN la campaña se envía
- THEN la audiencia lo incluye con segmento `importados` y la plantilla sale con el número del negocio (`WHATSAPP_PHONE_NUMBER_ID`)
- AND el touch de campaña crea su metadata mínimo, así la respuesta del cliente se atribuye a la campaña

#### Scenario: Archivo no textual o demasiado grande

- WHEN se sube un `.xlsx` binario o un archivo > 1 MB
- THEN la API responde 415 / 413 con un detail legible y no cambia la campaña

### Requirement: Carrusel de productos en la plantilla de campaña

La campaña MAY llevar entre 2 y 10 productos del catálogo como tarjetas de
un carrusel de **product cards** de Meta: cada tarjeta referencia el ítem
del catálogo de Meta conectado al número del bot (`product_retailer_id` =
identidad vigente del producto + `META_CATALOG_ID`); foto y precio los pone
Meta desde el catálogo y el botón "Ver" abre el producto con carrito nativo.
Meta fija la cantidad de tarjetas al aprobar la plantilla: MUST existir una
plantilla por cantidad (`campaign_carousel_marketing_v1_{n}`, `carousel_kind:
product`). El sabor *media card* (foto por media_id + quick reply) queda
soportado en la plataforma para números sin catálogo.

#### Scenario: Campaña con tres productos

- GIVEN `carousel_handles = [a, b, c]` y `META_CATALOG_ID` configurado
- WHEN se envía (o se hace el envío de prueba)
- THEN el plan usa `campaign_carousel_marketing_v1_3`, las tarjetas se arman UNA vez (sin subir fotos) y las mismas 3 viajan a cada destinatario con `{type: product, product: {product_retailer_id, catalog_id}}`
- AND el historial de la sesión muestra los productos ofrecidos

#### Scenario: Cantidad inválida, producto fuera del catálogo o sin catálogo de Meta

- WHEN se guarda 1 producto (o 11), un handle ya no está en el catálogo, o falta `META_CATALOG_ID`
- THEN el PUT / el envío responde 422 con la razón y nada sale

#### Scenario: El cliente toca "Ver" y arma un carrito

- GIVEN el cliente abre el producto desde la tarjeta y envía el carrito nativo
- WHEN chats lo ingiere (`type: order`)
- THEN el bot lo recibe como "[el cliente armó un carrito con: …]" (flujo existente) y sigue la venta
