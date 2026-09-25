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

### Requirement: Las sesiones de prueba nunca son audiencia

Una sesión sembrada para probar Ads (`seeded_test: true`, teléfono falso,
historial "[seed] mensaje N") MUST NOT ser destinataria de ninguna campaña:
ni por segmento, ni agregada a mano, ni importada. La audiencia la salta con
razón `sesion_de_prueba` y la API no la lista ni en "No reciben" (es ruido de
desarrollo, no un contacto). La limpieza es
`python -m scripts.seed_test_ctwa_sessions --clean --apply` (container api):
mueve esas sesiones a `<vault>/_quarantine/seeded-<ts>/` (reversible con `mv`),
reconociendo también seeds viejos sin marker por su historial.

#### Scenario: Campaña a "fríos" con seeds en el vault

- GIVEN una sesión `wa_5730000009XX` con `seeded_test: true` y tag INTERESADO
- WHEN se resuelve la audiencia de una campaña a interesados
- THEN no está en `recipients` ni en `skipped` de la API y el envío nunca la toca
- AND `GET /segments` tampoco la cuenta (ni en el segmento ni en `excluded_count`): la card del segmento y la audiencia usan la misma regla

### Requirement: El mensaje de campaña solo ofrece lo que viaja en la plantilla

Las plantillas aprobadas (`campaign_promo_marketing` y las de carrusel) tienen
un único cuerpo con saludo + mensaje + oferta y el texto de baja fijo: NO
tienen pie ni botón (Meta los fija al aprobar y no admiten variables; el
carrusel no admite pie). El builder MUST NOT ofrecer campos de pie o botón, la
campaña guarda solo `message: {header, body}` (un `footer`/`cta` entrante se
ignora) y la vista previa SHALL mostrar el mensaje tal cual viaja: encabezado +
cuerpo en un solo párrafo ("Encabezado. Cuerpo"), seguido de la oferta y la baja.

#### Scenario: Campaña vieja con pie y botón guardados

- GIVEN una campaña con `message.footer` y `message.cta` de antes de este cambio
- WHEN el operador la abre o la edita
- THEN la vista previa no los muestra y el siguiente guardado los elimina

### Requirement: El cupón que anuncia la campaña existe en Medusa

Si la campaña tiene `coupon_code`, el envío y el envío de prueba MUST validarlo
contra Medusa con la misma regla que usa el bot (`resolve_coupon`): código
inexistente, inactivo, sin empezar, vencido, agotado, con reglas ilegibles o
de envío → 422 con el motivo y nada sale; Medusa caído → 503. El builder avisa
cuando el código escrito no está entre los vigentes, y no ofrece cupones de
envío (el envío lo cobra la transportadora a su tarifa, sin descuentos —
decisión del operador, 2026-09-23).

#### Scenario: La campaña anuncia un código que no existe

- GIVEN la campaña dice "Usa el código AMOR" y en Medusa solo existe AMOR26
- WHEN el operador envía (o prueba) la campaña
- THEN responde 422 "El cupón AMOR no existe en Medusa…" y el builder ya lo advertía bajo el campo

#### Scenario: Cupón programado y envío programado

- GIVEN AMOR27 empieza mañana
- WHEN el operador programa el envío para después de que empiece, o manda una prueba
- THEN se acepta (el cupón se valida para el instante del envío); un cupón que vence ANTES del envío programado responde 422 aunque hoy rija

#### Scenario: El cupón cambió antes de que saliera el envío programado

- GIVEN una campaña programada con AMOR27
- WHEN al dispararse AMOR27 está pausado, vencido o ya no existe
- THEN no sale ningún mensaje, la campaña queda `failed` con `failure_reason` a la vista del operador
- AND si sigue valiendo, la campaña copia el % y el "válido hasta" de ese momento; si Medusa no responde se reintenta y, en el último intento, se frena con el motivo

#### Scenario: La campaña anuncia un cupón de envío

- GIVEN en Medusa existe `ENVIOGRATIS` sobre el envío
- WHEN el operador abre el builder o envía una campaña con ese código
- THEN el builder no lo lista entre los cupones vigentes y el envío responde 422 "El cupón ENVIOGRATIS es de envío, y el envío lo cobra la transportadora…"

### Requirement: Central de cupones

Marketing → Cupones MUST ser la central de control de los cupones: crea,
edita, pausa y borra promociones de **porcentaje** sobre productos (D8)
escribiendo en Medusa por el `PromotionsAdminPort` (promoción + campaña en UNA
llamada al crear). El "hasta" MUST ser un día incluido en hora de Bogotá
(la campaña cierra a las 00:00 del día siguiente). Cualquier usuario del
dashboard puede operar la central (D6), y cada cambio MUST quedar en el
registro con el actor que verificó `require_auth` (`current_actor`), nunca uno
que venga en el cuerpo del request.

#### Scenario: Crear un cupón desde la central

- GIVEN el operador llena código AMOR27, 10 %, un producto, del 22 al 27-sep
- WHEN guarda con "Crear y activar"
- THEN Medusa tiene la promoción AMOR27 activa con su campaña `2026-09-22T05:00Z → 2026-09-28T05:00Z`
- AND el registro de cambios dice quién la creó

#### Scenario: Código ocupado

- GIVEN ya existe AMOR26 en Medusa (con cualquier combinación de mayúsculas)
- WHEN el operador intenta crear AMOR26, o renombrar un borrador a "amor26"
- THEN la central responde 409 "Ese código ya existe." y no escribe nada

#### Scenario: Cupón editado fuera de Hubara (D7)

- GIVEN AMOR26 tiene una condición por etiquetas creada en Medusa Admin
- THEN la central lo muestra en solo lectura con el motivo
- AND editarlo o pausarlo responde 409 y ninguna escritura toca Medusa

#### Scenario: Editar solo lo que cambió

- WHEN el operador cambia solo el porcentaje de un cupón gestionable
- THEN la central manda solo `POST /admin/promotions/{id}` con el nuevo valor
- AND si un paso de una edición falla, responde 502 con el paso y el estado releído de Medusa (reintentar es seguro); si falla el PRIMER paso responde el error original (nada cambió)

#### Scenario: Medusa no confirma una escritura que sí salió

- WHEN crear, editar, pausar o borrar se corta después de enviarse (timeout, 5xx)
- THEN la central relee Medusa: si quedó aplicado, responde como éxito y lo registra; si no se puede saber, responde 503 diciendo que el resultado es DESCONOCIDO (verificar antes de reintentar) y lo registra como `*_unconfirmed` — nunca "no se hizo ningún cambio"

#### Scenario: Campaña de Medusa compartida

- GIVEN dos promociones cuelgan de la misma campaña de Medusa (armado en Medusa Admin)
- THEN la central las muestra en solo lectura y borrar un borrador nunca borra una campaña que otra promoción usa

#### Scenario: Borrar

- GIVEN un cupón con ventas, o que no es borrador
- WHEN el operador intenta borrarlo
- THEN responde 409 y le sugiere pausarlo; un borrador sin ventas se borra con su campaña y su cupo

### Requirement: Cupo por unidad administrado en la central

Las filas del cupo (producto + color + aroma + unidades) MUST validarse contra
las listas cerradas del producto (sus etiquetas `Color:`/`Aroma:`) y contra los
productos del cupón; el guardado es todo o nada, con errores por fila. El
cupo vive en Hubara, así que se puede poner también a un cupón de porcentaje
de solo lectura. Las vendidas y los resultados MUST derivarse de los pedidos.

#### Scenario: Color que el producto no tiene

- WHEN el operador guarda una fila Cubo Love · Verde y el producto no tiene "Color: Verde"
- THEN responde 422 con el error en esa fila y no guarda ninguna

#### Scenario: Bajar las unidades por debajo de lo vendido

- GIVEN una fila con 3 vendidas
- WHEN el operador la deja en 1
- THEN la central muestra 0 disponibles y el aviso de sobreventa

#### Scenario: Dos personas editan las unidades

- GIVEN el operador A cargó las unidades y el operador B guardó después
- WHEN A guarda con la versión que cargó (`expected_updated_at`)
- THEN responde 409 `units_changed` sin escribir, y el editor ofrece "Recargar" sin perder el borrador

#### Scenario: Productos que salen del cupón

- WHEN una edición quita productos del cupón
- THEN sus filas del cupo se quitan (bajo el candado) y el registro de cambios lo dice (`units_pruned`)

### Requirement: La campaña anuncia los términos del cupón

Cuando la campaña lleva cupón, el envío y el envío de prueba MUST copiar el
porcentaje y el último día incluido del cupón a la campaña ("válido hasta 27
de septiembre"), en vez de usar lo que tipeó el operador.

### Requirement: La campaña existe en Ads desde el envío, con las estadísticas de su envío

Cada envío REAL de una campaña MUST estampar en el touch del destinatario el id
del mensaje que devolvió Meta (`wa_message_id`), y el webhook de estados MUST
anotar en ese touch si se entregó, si se leyó o si falló (con su código) y el
precio de Meta (`delivery`). Ads MUST mostrar la fila de la campaña desde el
envío (aunque nadie haya respondido) con su envío: enviados, entregados,
leídos, fallidos, respuestas, bajas y gasto real (`whatsapp_send`), además del
embudo de sus conversaciones. Los envíos de prueba MUST NOT contar. El costo
de la plantilla MUST NOT sumarse dos veces: si cayó en una conversación que
el contacto tenía abierta, esa fila no lo repite (pedido del operador,
2026-09-25).

#### Scenario: Campaña enviada que todavía nadie respondió

- GIVEN una campaña real enviada a 100 contactos
- WHEN Meta avisa que 90 se entregaron y 60 se leyeron
- THEN Ads muestra la fila de la campaña (insignia WhatsApp) con enviados 100, entregados 90, leídos 60 y el gasto real, con 0 conversaciones

#### Scenario: El embudo y el costo de una campaña con ventas

- GIVEN 12 contactos respondieron y 2 compraron por 94.000 COP
- THEN el panel "WhatsApp · envío de la campaña" muestra enviados → entregados → leídos → respondieron → ventas, el costo por respuesta y por venta y el ROAS aproximado (US$1 ≈ $4.000)
- AND los mensajes sin precio todavía y los enviados antes del registro de entregas se avisan aparte, nunca como $0

#### Scenario: Varias campañas al mismo cliente

- GIVEN el cliente recibió Amor (lunes) y Halloween (jueves)
- THEN envío, entrega, lectura, fallos y costo van a cada campaña por el id de su mensaje
- AND su respuesta y su venta cuentan para UNA sola campaña, en Ads y en el inspector de Marketing: la que citó al responder o, sin cita, la última que no había respondido (nunca una prueba)

#### Scenario: Envíos de prueba

- GIVEN el operador hizo envíos de prueba y respondió
- THEN la campaña no aparece en Ads por esas pruebas (el touch de prueba guarda el id del mensaje, pero la atribución lo ignora)

