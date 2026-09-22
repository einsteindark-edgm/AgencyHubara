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

### Requirement: Envío de prueba a un número del operador

`POST /campaigns/{id}/test` SHALL normalizar el número con la misma regla que
el CSV de audiencia (`3001234567`, `300 123 4567` y `+57 300 123 4567` son la
sesión `wa_573001234567`, como la escribe el webhook) y MUST NOT exigir
conversación previa: sin sesión, el envío usa el número del negocio
(`WHATSAPP_PHONE_NUMBER_ID`) igual que un contacto importado. Un rechazo del
envío (plantilla no aprobada, número inválido para Meta, configuración
faltante) SHALL responder 502 con el motivo, nunca un 500 sin detalle.

#### Scenario: El operador teclea su celular sin indicativo

- GIVEN la sesión `wa_573001234567` existe (o no)
- WHEN el operador envía la prueba con `300 123 4567`
- THEN la plantilla sale a `wa_573001234567` (con el nombre del vault si lo hay) y el historial de pruebas guarda `573001234567`

#### Scenario: Número que no es un celular

- WHEN el operador envía `6012345678` (fijo) o texto
- THEN responde 422 explicando el formato y nada sale

#### Scenario: Meta rechaza el envío

- WHEN el envío lanza (por ejemplo `code=132001`, plantilla inexistente)
- THEN responde 502 con ese motivo y la prueba NO queda en el historial

### Requirement: Registro de bajas de marketing (quién, cuándo, por qué vía y qué campaña)

Una baja de marketing la registra SIEMPRE Hubara en el metadata del contacto
(`marketing_opt_out=true`), por dos vías: el cliente responde pidiéndola
("no más", "baja", "stop"… con campaña reciente; `marketing_opt_out_source =
texto`) o la pide desde WhatsApp y Meta rechaza el envío con el código
`131050` (`source = meta`, que además MUST ser no reintentable). En ambos
casos SHALL quedar `marketing_opt_out_at_ms` y `marketing_opt_out_campaign_id`
(la campaña del touch reciente, o la que estaba enviando cuando Meta rechazó).
La baja es sticky: una segunda baja MUST NOT pisar la primera. Solo el
operador la revierte editando el metadata.

#### Scenario: Meta rechaza el envío de campaña con 131050

- GIVEN el workflow de envío intenta un destinatario y Meta responde 131050
- THEN el contacto queda de baja con `source = meta` y `campaign_id` = esta campaña, NO cuenta como fallido (`send_result.opted_out`) y no se le vuelve a intentar en campañas futuras

#### Scenario: El cliente responde "no más" tras una campaña

- GIVEN un touch de campaña `mkt-1` hace menos de 7 días
- WHEN el ingest de chats recibe "no más"
- THEN queda de baja con `source = texto`, la fecha del inbound y `campaign_id = mkt-1`

#### Scenario: Audiencia y métrica de bajas

- WHEN el operador abre la audiencia de cualquier campaña
- THEN los contactos de baja aparecen en una sección "Bajas" aparte de "No reciben", con razón `dado_de_baja`, la vía, la fecha y el nombre de la campaña que la provocó (`opted_out_campaign_name`, null si esa campaña se borró), más `opted_out_count`
- AND `GET /campaigns/{id}/stats` devuelve `opted_out` = bajas provocadas por ESA campaña (fila "Bajas" en el resultado del envío)
