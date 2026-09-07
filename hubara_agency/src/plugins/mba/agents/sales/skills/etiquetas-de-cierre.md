---
title: etiquetas-de-cierre
description: Aplicar cuando una conversación termina sin que el pedido quede registrado: el cliente se despide, dice que lo piensa, deja de responder tras mostrar interés, o rechaza la compra. Define las dos únicas etiquetas que propones con manage_conversation_tag y cuándo va cada una.
---

# Etiquetas al cerrar una conversación

Cuando la conversación termina y no hay un pedido registrado, propones una etiqueta con la herramienta manage_conversation_tag. Tiene dos parámetros: tag (solo puede ser INTERESADO o RECHAZO) y motivo (una frase corta con el porqué).

| Caso | tag | Ejemplo de motivo |
|---|---|---|
| Mostró interés (preguntó precios, eligió producto, pidió fotos, dijo "lo pienso", "después te confirmo", "tengo que preguntarle a mi pareja") pero no compró | INTERESADO | "Preguntó por la vela de lavanda y el envío a Medellín; dijo que lo consulta con su esposo" |
| Descartó la compra de forma clara ("no me interesa", "muy caro, gracias", "era solo por curiosidad") o el mensaje era spam o sin intención de compra | RECHAZO | "Buscaba velas al por mayor con descuento y no le sirve el precio unitario" |

Reglas:
- Solo esas dos etiquetas. El estado de un pedido confirmado o pagado lo lleva el equipo de Hubara a partir de register_order y de la verificación del pago; tú no lo marcas.
- Etiquetas una sola vez, cuando la conversación efectivamente terminó (despedida, rechazo claro o el cliente dijo que lo piensa). No etiquetes en mitad de una venta que sigue viva.
- Un cliente INTERESADO recibirá más adelante un mensaje de seguimiento del equipo; un RECHAZO no. Por eso el motivo debe ser concreto: es lo que el colega lee antes de escribirle.
- Si el cliente pasó a un colega con escalate_to_human, no etiquetes: el colega cierra.
- El mensaje de despedida al cliente va aparte, en tu voz, breve y cálido. La etiqueta es una acción interna; nunca la menciones en el chat.
