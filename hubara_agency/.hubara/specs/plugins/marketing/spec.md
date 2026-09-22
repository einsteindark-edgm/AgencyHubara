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
un carrusel (media cards de Meta). Cada tarjeta MUST llevar la foto del
producto (media_id subido a Meta), "nombre · precio" y el botón "Me interesa"
cuyo payload `ref: <SKU>` el ingest de chats entiende como referencia de
producto. Meta fija la cantidad de tarjetas al aprobar la plantilla: MUST
existir una plantilla por cantidad (`campaign_carousel_marketing_v1_{n}`).

#### Scenario: Campaña con tres productos

- GIVEN `carousel_handles = [a, b, c]`
- WHEN se envía (o se hace el envío de prueba)
- THEN el plan usa `campaign_carousel_marketing_v1_3`, las fotos se suben UNA vez (cache `carousel_media`, renovado a los 25 días) y las mismas 3 tarjetas viajan a cada destinatario
- AND el historial de la sesión muestra los productos ofrecidos

#### Scenario: Cantidad inválida o producto sin foto

- WHEN se guarda 1 producto (o 11), o un producto elegido no tiene foto en el catálogo
- THEN el PUT / el envío responde 422 con la razón y nada sale

#### Scenario: El cliente toca "Me interesa"

- GIVEN el webhook trae `type: button` con `payload: "ref: HUB-CUBOLOVE"`
- WHEN chats lo ingiere
- THEN el texto del mensaje lleva título y payload ("Me interesa · ref: HUB-CUBOLOVE") y el producto queda hidratado como con el botón del PDP
