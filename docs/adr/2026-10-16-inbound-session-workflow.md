# ADR 2026-10-16 · «Foto en lectura» y el orden de entrega viven en un workflow por cliente

**Estado:** aceptado.
**Contexto:** fase 2 de la entrada durable (`docs/adr/2026-10-15-durable-inbound-entry.md`, paso 3).

## Problema

`PhotoReads` (`use_cases/photo_reads.py`) guarda en memoria del proceso qué fotos de cada cliente
se están leyendo. El texto que el cliente escribe detrás de una foto («¿tienes esta?») espera ahí
a que la foto entre al bot (caso 2026-09-30). Con la visión como activity (fase 2, paso 2), la foto
se lee en cualquier worker, así que ese estado en memoria ya no sirve. Además obliga a correr el
worker `sales` con una sola réplica.

El ADR de la fase 1 dejó dos caminos:

1. un `InboundSessionWorkflow` (`inbound-{sesión}`);
2. delegarlo en la sesión de ventas, que ya recibe el signal `photo_reading`.

## Decisión

Un **`InboundSessionWorkflow` por cliente**, con id `inbound-wa_<número>`, en la cola
`queue-sales-agent`. No guarda mensajes ni lleva cola. Lleva solo **quién llegó antes**:

- cada `InboundMessageWorkflow` se anuncia al arrancar con `arrived(workflow_id, received_at_ms)`,
  vía `signal_with_start` desde una activity;
- el workflow del cliente le responde con su **anterior**: el último anunciado con una hora de
  llegada menor.

El mensaje corre sus lecturas en paralelo con los demás. Antes de escribir el historial espera, con
tope, a que termine el workflow de su anterior. Lo mismo hacía la fase 1 con `after` dentro de un
POST; ahora vale también entre POSTs.

«Foto en lectura» es entonces un workflow abierto. El de una foto no termina hasta que la foto
entró al bot: visión, reentrada, historial y entrega. Lo que el cliente escribió después la espera,
como antes con `PhotoReads`, pero la espera es durable y no depende del proceso. Por eso **cae el
invariante de una réplica**.

### Por qué no la sesión de ventas

- **Solo existe en la ruta de ventas.** Los mensajes que van a remarketing o a un humano no pasan
  por ella. El aviso `photo_reading` hoy se salta si la sesión no está viva, y esta se apaga tras un
  minuto sin mensajes.
- **La arranca la primera entrega.** No puede retener una ráfaga que empezó antes de existir, que
  es justo el primer contacto «foto + ¿tienes esta?».
- **No ordena el historial del panel.** El texto quedaría escrito antes que la foto, que entra al
  JSONL recién cuando la visión termina.
- **Solo vería juntos los mensajes que caen en el mismo debounce.** Un texto cuya lectura con Jev
  tarda 8 s llegaría en otro turno que el que escribió 1 s después.

El aviso `photo_reading` a la sesión de ventas **se queda**: cubre el texto escrito *antes* de la
foto, que ya está en la ráfaga.

### Por qué no serializar todo el ingest por cliente

Lo descartó la fase 1 por latencia, y sigue descartado. Aquí solo se ordena el tramo final, el
historial y la entrega, que tarda decenas de milisegundos. Las lecturas, Jev y la visión de mensajes
de POSTs distintos corren en paralelo. Una ráfaga «hola» + «quiero velas» + «¿envían a Cali?» (un
POST por mensaje, lo típico) llega junta a la sesión, en el orden en que entró al webhook.

Dos excepciones:

- **Dentro de un mismo POST, el paso del estado espera al anterior** (`after`, como en la fase 1).
  Los dos verían el mismo episodio cerrado o la misma campaña sin responder.
- **Las lecturas que describen al último mensaje** (señal de compra, pausa, cortesía) se escriben
  en el tramo ordenado, con `last_inbound_message_id`. Escritas al leer, la foto que reentra tarde
  borraría las del texto que el cliente mandó después.

## Consecuencias

- **Por mensaje:** una activity (el anuncio con `signal_with_start`) y dos señales (`arrived` y la
  respuesta). Cuando hay anterior, se suma la espera ya existente (`wait_inbound_workflow`, un
  long-poll con heartbeat).
- **El workflow del cliente termina solo** tras 10 min sin anuncios. El siguiente mensaje lo vuelve
  a arrancar sin anterior. Lo de hace más de 10 min ya terminó, o está trabado y no se lo espera.
- **Topes:**
  - la respuesta del anterior se espera 30 s;
  - el anterior se espera 30 s.

  Si algo se traba, el mensaje sale igual. Se pierde el orden, nunca el mensaje.
- **No hay ciclos.** El anterior siempre tiene una hora de llegada estrictamente menor. Dentro de un
  POST, `after` sigue mandando aunque los anuncios lleguen desordenados.
- **El orden es el de llegada al webhook** (`received_at_ms`), no el `timestamp` de Meta (en
  segundos). Meta entrega en orden. Un rezagado de días ya lo marca «llegó tarde» y no se reordena.
