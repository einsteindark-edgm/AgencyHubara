# ADR 2026-10-15 · Entrada durable de los mensajes de WhatsApp (fase 1)

**Estado:** aceptado (fase 1 implementada; fase 2 pendiente).
**Origen:** auditoría de Temporal del 2026-10-15: el hueco más grande de durable execution está en la puerta de entrada.

## Contexto

El webhook (`src/plugins/chats/api/sales.py`) le responde 200 a Meta y recién
después procesa cada mensaje en `BackgroundTasks` de la API:
`IngestInboundMessage.execute` (lecturas con Jev, cupón en Medusa, carrito, visión
y audio en `_spawn_safe`, envíos directos) y al final el `signal_with_start` a la
sesión de ventas. Si la API muere en ese rato (deploy, OOM de la caja de 4 GB),
el mensaje se pierde: Meta ya recibió su 200 y no reintenta. El ledger de entrada
lo ve como un `seen` sin `ingested`, pero el mensaje no vuelve.

Además, en el camino de ventas no hay dedupe por `wamid` (standby sí la tiene,
`use_cases/ingest_standby.py:24`). Una re-entrega de Meta se ingería dos veces.

Lo que garantiza hoy el orden: Starlette corre en serie los background tasks
**de un mismo POST**. Dos POST distintos, en cambio, se ingieren en paralelo.
Es el caso típico: el cliente escribe rápido y Meta manda un POST por mensaje.

## Decisión

### 1 · La entrada: un workflow por mensaje, antes del 200

Por cada mensaje del cliente, en el orden de Meta, el webhook hace:

```python
await client.start_workflow(
    "InboundMessageWorkflow", InboundMessageInput(...),
    id=f"inbound-{wamid}", task_queue=queue-sales-agent,
    id_reuse_policy=REJECT_DUPLICATE,
)
```

Recién con todos aceptados responde 200.

- **Mensaje repetido** (`WorkflowAlreadyStartedError`): Meta re-entregó algo que ya
  está en Temporal. Cuenta como aceptado y deja `duplicate` en el ledger. Esta es
  la dedupe por `wamid` que faltaba.
- **Temporal falla** (sin conexión, timeout de 8 s): `enqueue_failed` en el ledger
  y respuesta **503**. Meta reintenta el POST entero; lo que ya había entrado lo
  frena la dedupe.
- **Los statuses (✓/✓✓) y los campos `standby` y `messaging_handovers`** siguen
  como hoy: `BackgroundTasks` de la API.
- **El cliente de Temporal** se conecta una vez por proceso y se reusa
  (`InboundEntry`). Hoy cada ingest abre una conexión nueva, y ahora esa conexión
  queda antes del 200.
- **`_in_their_conversation`** (el BSUID y los alias de teléfono) sigue en el
  webhook, porque hace falta para saber quién es el cliente al encadenar (punto 2).

### 2 · Orden por cliente: el mismo de hoy, ahora durable

Se mantiene la garantía real de hoy. Los mensajes de un mismo cliente que llegan
**en el mismo POST** se ingieren en el orden de Meta. El webhook le pasa a cada
uno el `wamid` del anterior del mismo cliente (`after`). El workflow, antes de
ingerir, espera a que cierre el workflow de ese mensaje: la activity
`wait_inbound_predecessor_activity` hace un long-poll de `handle.result()` con
heartbeat y un tope de 2 min. Si el anterior no existe, falló o se pasa del tope,
sigue igual. Ningún mensaje se pierde por esperar el orden.

Esto ya no depende del proceso. Si el worker muere a mitad, la espera y el ingest
se reintentan en el worker nuevo, en orden. Con `BackgroundTasks` eso no pasaba:
el orden era una lista en memoria.

POSTs distintos siguen ingiriéndose en paralelo, como hoy. **Descartado:
serializar todo por cliente** (un workflow `inbound-{sesión}` con cola, al estilo
de `eta_session`). El ingest tarda de 1 a 10 s (Jev espera hasta 10 s). En serie,
la ráfaga «hola», «quiero velas», «¿envían a Cali?» llegaría a la sesión espaciada
por esa demora. El debounce de 1,5 s de la sesión partiría la ráfaga en turnos
que hoy son uno. Además, la dedupe dentro de un workflow por sesión solo vale
mientras ese run vive; `REJECT_DUPLICATE` por `wamid` vale toda la retención del
namespace. Ordenar lo que **ve el bot** sin pagar esa latencia queda para la fase 2.

### 3 · Task queue y worker: `queue-sales-agent`, worker `sales`

`InboundMessageWorkflow` y sus dos activities se registran en el worker `sales` y
corren en su cola. El manifest lo declara al final de `workflow_classes`. No se
suma un contenedor:

- la caja de 4 GB vive con ~300 MB libres de mínimo diario;
- el worker `sales` ya tiene el mismo `.env` (prod lo comparte entre todos), el
  vault montado y el paquete de decisión compilado al arrancar;
- es el proceso que ya le habla a la sesión de ventas.

El panel no se entera de nada distinto. El ingest no publica en el bus en proceso:
el panel ve los cambios por el muestreo del vault (`chats/api/dashboard.py`), que
sirve igual desde cualquier contenedor. Los statuses, que sí publican en el bus,
se quedan en la API. La API además deja de cargar el ingest, que es el proceso
cuya memoria crece.

**Invariante de la fase 1:** el worker `sales` corre como **una sola réplica**.
Hoy ya es así en la caja de prod (compose). Lo exigen `PhotoReads` y las tareas
de visión y audio (punto 4). El manifest declara `deployment.replicas: 3` para
k8s, que no es prod. Si ese camino se usara antes de la fase 2, el texto que
llega detrás de una foto dejaría de esperarla: el comportamiento de antes del
2026-09-30, sin perder mensajes.

En local, el compose del worker `sales` suma `MEDUSA_PUBLISHABLE_API_KEY`,
porque el ingest lee el carrito web. En prod el `.env` ya es compartido.

### 4 · `PhotoReads` en memoria

Se queda como está, ahora en el proceso del worker `sales`. El caso de uso es un
singleton por proceso (`build_ingest_use_case()`), así que la foto que se lee y
el texto que espera esa foto comparten el mismo `PhotoReads`, igual que hoy en la
API. La lectura de la foto y la transcripción del audio siguen en `_spawn_safe`
dentro del mismo proceso. Vuelven al ingest con un id sintético
(`<wamid>_vision`, `<wamid>_transcribed`).

Lo que sigue igual de frágil que hoy: si el worker muere **después** de que la
activity de la foto terminó (el ingest de una foto retorna apenas lanza la
lectura), esa lectura se pierde. La fase 2 la vuelve una activity.

### 5 · La ventana de 5 min de los media

No ata a esta fase. Los 5 min son la vida de la **URL firmada** que devuelve
`GET /{media_id}`, no del `media_id`. Cada descarga resuelve el `media_id` justo
antes (`platform/audio/meta_media_fetcher.py`; ver la nota en
`ingest_inbound_message.py:2688`). Un ingest que se reintenta minutos después
baja la foto igual. El código no dice cuánto conserva Meta el `media_id`. Los
reintentos de esta fase se miden en minutos.

### 6 · Reintentos acotados y envíos con su fingerprint

- **La activity del ingest:** `start_to_close` 5 min, heartbeat cada 10 s,
  `heartbeat_timeout` 30 s y `RetryPolicy(maximum_attempts=3)` con backoff de 2 s
  a 30 s. Un worker que muere a mitad (deploy, OOM) se nota por el heartbeat, y el
  ingest se repite en el worker nuevo.
- **Si se agotan los intentos,** el workflow queda *Failed* en Temporal, a la vista
  y reseteable desde la UI. En el ledger queda `ingest_failed`, como hoy.
- **Lo que un reintento no puede repetir** pasa por un diario por `wamid`
  (`InboundEffects`, archivos marcadores en `vault/_ledger/inbound_effects/<día>/`,
  creados con `O_EXCL`):
  - `history`: la línea del cliente en el JSONL de la sesión. Se marca después de
    escribirla; un reintento no la duplica en el panel.
  - `turn`: el mensaje a la sesión de ventas (`LoadOrStartSalesSession.execute`).
    Se marca después del signal; un reintento no hace que el bot conteste dos veces.
  - `send:<sha256(texto)[:16]>`: cada envío directo al cliente («Recibí tu
    documento», el audio que no se entendió, el comprobante recibido). Se
    **reclama antes** de enviar: un envío de cortesía sale como mucho una vez.

  El resto del ingest (lecturas, metadata con merge de tres vías, cupón ya marcado
  en la sesión) se puede repetir sin daño. Entre un efecto y su marca queda una
  ventana de milisegundos: en el peor caso se repite una línea, nunca se pierde
  el mensaje.

### 7 · El ledger de entrada sigue como verificación cruzada

El webhook sigue escribiendo `request` y `seen` del body crudo, y agrega `queued`,
`duplicate` o `enqueue_failed`. La activity escribe `ingested` o `ingest_failed`
(antes lo hacía `_ingest_ledgered`). En el resumen, `queued` sin desenlace cuenta
como `pending` (está en Temporal), no como `lost`.

## Fase 2 (pendiente, por pasos)

1. **Partir el ingest en activities** dentro de `InboundMessageWorkflow`:
   - leer el estado de la sesión;
   - las lecturas (Jev);
   - cupón y carrito;
   - escribir el historial;
   - entregar a la sesión.

   Cada efecto queda con su propia activity idempotente, y el diario de efectos
   pasa a ser el historial del workflow.
2. **La visión y el audio como activities** (`describe_image`, `transcribe_audio`),
   con su heartbeat. Con eso se retiran `_spawn_safe` y el `transcribe_audio_activity`
   muerto del worker `sales`.
3. **Reemplazar `PhotoReads` por estado de un workflow por cliente.** Hay dos
   formas posibles:
   - un `InboundSessionWorkflow` (`inbound-{sesión}`) que recibe los mensajes por
     signal y lleva «foto en lectura» como estado;
   - delegarlo en la sesión de ventas, que ya recibe `photo_reading`.

   Con eso cae el invariante de una réplica.
4. **Orden entre POSTs sin serializar las lecturas.** Las lecturas corren en
   paralelo y la entrega a la sesión va en orden de llegada (o por el `timestamp`
   de Meta dentro de la ráfaga). Así la ráfaga entera llega junta y en orden.
5. **Pasar `received_at_ms` al ingest** para que «llegó tarde» y «fuera de
   ventana» usen la hora de llegada al webhook y no la del intento. Hoy un
   reintento de minutos casi no cambia nada (el umbral de «llegó tarde» es de 5 min).

## Consecuencias

- **Un deploy o un OOM ya no pierden mensajes en vuelo.** Si la API no está, Meta
  reintenta; si el worker no está, el mensaje espera en Temporal.
- **El 200 a Meta espera un RPC a Temporal Cloud por mensaje** (~50–150 ms; un
  batch se arranca en serie para que el `after` exista).
- **Una caída de Temporal Cloud corta la entrada**, con 503 hasta que vuelva.
  Meta reintenta por días, así que los mensajes se demoran pero no se pierden.
  Volver a `BackgroundTasks` es revertir el commit.
- **Por mensaje entran** 1 workflow y 1 activity (2 si viene encadenado) en la
  facturación de Temporal Cloud.
- **El primer deploy** puede levantar la API antes que el worker. Los workflows
  esperan en la cola, sin perder nada.
- **`run_api.py`** deja de usar `reload=True` por defecto: en prod era un proceso
  supervisor y un hijo `multiprocessing.spawn` sin ninguna ganancia (el código
  vive en la imagen). Para recargar en el host se pide con `UVICORN_RELOAD=1`. El
  patrón de CloudWatch de la API (`monitoring.tf`) pasa a `run_api.py`, que cubre
  los dos modos; requiere `terraform apply`.
