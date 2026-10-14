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
