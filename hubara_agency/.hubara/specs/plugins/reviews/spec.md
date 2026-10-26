# Capability: plugins/reviews — reseñas tras la entrega

## Purpose

Pedir la calificación de cada pedido entregado por WhatsApp, premiar a quien
califica con un cupón personal, ofrecer la reseña en Google sin premio y
publicar en la tienda las reseñas que el equipo aprueba. Decisiones del
operador del 2026-10-13 (plan: https://claude.ai/artifact/KNofZ1JASr9bouNT4vgdxs).

### Requirement: Una invitación por pedido entregado, aprovechando la ventana

El sistema SHALL invitar a calificar UNA vez por pedido que pasa a `delivered`
con aviso al cliente, nunca a pedidos de prueba ni en horario de silencio, y
SHALL preferir el formulario como mensaje normal dentro de la ventana de 24 h.

#### Scenario: ventana abierta
- GIVEN un pedido entregado hace ≥ 2 h y la ventana de servicio abierta
- WHEN despierta su `ReviewRequestWorkflow`
- THEN se envía el formulario `review_v1` con la foto del pedido (canal `flow`)

#### Scenario: ventana cerrada
- GIVEN la ventana cerrada y el cliente sin escribir
- WHEN pasan 20 h desde la entrega (horario permitido, sin baja de marketing)
- THEN se envía la plantilla `order_review_marketing_v1` con el descuento

#### Scenario: repetición
- GIVEN una invitación ya reclamada para el pedido
- WHEN el evento de entrega llega otra vez
- THEN no se envía nada (`already_requested`)

### Requirement: Calificar da un cupón personal con cualquier calificación

El sistema MUST conceder GRACIAS5 (o GRACIAS10 con foto y permiso para
publicarla) a quien responde el formulario, sin importar las estrellas, una
sola vez por pedido, y MUST ofrecer el botón «Opinar en Google» a todos sin
premio (Google prohíbe premiar su reseña o pedirla solo a los contentos).

#### Scenario: calificación baja
- GIVEN una respuesta con 2 estrellas
- WHEN se procesa
- THEN se guarda, se concede GRACIAS5, se agradece con el botón a Google y la
  conversación pasa al equipo (`LOW_RATING`)

#### Scenario: respuesta repetida
- GIVEN una reseña ya guardada con su cupón
- WHEN Meta reentrega la respuesta
- THEN no se concede otro cupón ni se agradece de nuevo

### Requirement: Solo quien lo recibió usa el cupón, una vez

Un cupón `granted_only` MUST rechazarse a quien no tiene concesión vigente y
MUST gastarse al registrar el pedido que lo usa; se aplica solo cuando el dueño
vuelve a escribir.

### Requirement: Publicación moderada

El sistema SHALL publicar en Medusa solo lo que el equipo aprueba; sin permiso
del cliente la reseña cuenta solo con sus estrellas, anónima y sin foto.

#### Scenario: hide de una reseña ya publicada
- GIVEN una reseña publicada (aunque haya quedado a medias)
- WHEN el equipo la oculta
- THEN todas sus filas quedan `hidden` en Medusa y la tienda se reconstruye de
  una (no espera la ventana de 10 min)

### Requirement: Lo público no abre datos del pedido

Nada que llegue a la tienda (clave de la reseña en Medusa, nombre de la foto)
MUST contener el id del pedido de Medusa (con él se leen sus datos personales).
El nombre público MUST ser el de quien calificó (perfil de WhatsApp), solo
letras; sin un nombre usable la reseña sale como «Cliente de Hubara». La ciudad
de envío solo sale si quien recibió es quien calificó (regalos).

### Requirement: La invitación sobrevive fallas y sigue su entrega (premortem 2026-10-14)

El flujo SHALL leer fresca la etapa del pedido; una falla al decidir MUST
esperar y reintentar (no terminar); un envío que se cae a mitad MUST NOT
reintentarse (queda «sin confirmar»). Tras enviar, el flujo SHALL esperar el
aviso de entrega de Meta: un formulario no entregado por la ventana cerrada
(131047) reabre la invitación y sale la plantilla; otro fallo queda `failed`.
Una plantilla aún sin aprobar (132001/132015/132016) libera la invitación.

#### Scenario: dos pedidos entregados juntos
- GIVEN la invitación de otro pedido del mismo cliente enviada hace < 24 h y
  sin contestar
- WHEN despierta el flujo del segundo pedido
- THEN espera (`another_invitation_pending`)

#### Scenario: calificación baja en la posventa
- GIVEN la conversación en ruta humano con tag COMPRA_EXITOSA
- WHEN llega una calificación de 2★
- THEN `/escalate` la pone en HUMANO con `LOW_RATING`; si chats no lo confirma,
  la actividad se reintenta

### Requirement: Por qué no salió (observabilidad)

Cada decisión del flujo SHALL quedar registrada con su motivo
(`_reviews/plans/`) y el resumen del panel SHALL contar las invitaciones sin
enviar y en espera por motivo, los envíos sin confirmar, si falta el formulario
o los cupones (que duren lo prometido) y el estado de la reconstrucción de la
tienda.

#### Scenario: entregado sin avisar al cliente
- GIVEN un pedido que el equipo marca «Entregado» con «Avisar al cliente»
  apagado (saltar etapas en el tablero, o el centinela)
- WHEN se registra la entrega
- THEN no sale el WhatsApp de «entregado» del ETA, pero sí la invitación a
  calificar: `orders` emite `OrderDeliveredEvent` con CADA entrega y la
  transición `order_delivered_requests_review` cuelga de él (operador,
  2026-10-23)
