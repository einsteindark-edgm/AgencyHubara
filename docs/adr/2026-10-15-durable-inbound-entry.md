# ADR 2026-10-15 · Entrada durable de los mensajes de WhatsApp (fases 1 y 2)

**Estado:** aceptado (fase 1 y fase 2 implementadas; la fase 2 está en la sección «Fase 2»).
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
  frena la dedupe. Los statuses y los `BackgroundTasks` de ese POST no corren con
  el 503 (FastAPI los descarta); se procesan con el reintento. Tras una falla,
  durante 5 s el webhook responde 503 sin volver a probar: con Temporal colgado,
  cada webhook esperaba sus 8 s en fila detrás del mismo candado.
- **Los statuses (✓/✓✓) y los campos `standby` y `messaging_handovers`** siguen
  como hoy: `BackgroundTasks` de la API.
- **El cliente de Temporal** se conecta una vez por proceso y se reusa
  (`InboundEntry`). Hoy cada ingest abre una conexión nueva, y ahora esa conexión
  queda antes del 200.
- **`_in_their_conversation`** (el BSUID y los alias de teléfono) sigue en el
  webhook, porque hace falta para saber quién es el cliente al encadenar (punto 2).
- **Qué queda en Temporal Cloud:** el input del workflow y de la activity se
  guarda en claro (no hay codec) durante la retención del namespace (30 días):
  - el texto, el teléfono o BSUID, el nombre de perfil, la ubicación;
  - las tarjetas de contacto que comparta el cliente;
  - el referral del anuncio.

  El texto y el teléfono ya estaban ahí, en los signals y en el id
  `session-wa_<tel>`. El `raw` de Meta no viaja: el ingest no lo lee. Cifrar los
  payloads con un codec queda para cuando haga falta (toca a todos los workers).

### 2 · Orden por cliente: el mismo de hoy, ahora durable

Se mantiene la garantía real de hoy. Los mensajes de un mismo cliente que llegan
**en el mismo POST** se ingieren en el orden de Meta. El webhook le pasa a cada
uno el `wamid` del anterior del mismo cliente (`after`). El workflow, antes de
ingerir, espera a que cierre el workflow de ese mensaje: la activity
`wait_inbound_predecessor_activity` hace un long-poll de `handle.result()` con
heartbeat y un tope de 2 min. Si el anterior no existe, falló o se pasa del tope,
sigue igual. Ningún mensaje se pierde por esperar el orden. Un mensaje sin `wamid`
no tiene workflow al que esperar: el siguiente se encadena al último que sí lo
tiene.

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

> **Fase 2:** el invariante cae. Ya no hay estado en memoria del proceso: la foto,
> la nota de voz y el orden viven en Temporal.

En local, el compose del worker `sales` suma `MEDUSA_PUBLISHABLE_API_KEY`,
porque el ingest lee el carrito web. En prod el `.env` ya es compartido.

### 4 · `PhotoReads` en memoria

> **Fase 2:** reemplazado. «Foto en lectura» es el workflow abierto de la foto, y el
> texto que la sigue la espera en `InboundSessionWorkflow`
> (`docs/adr/2026-10-16-inbound-session-workflow.md`). Cae el invariante de una
> réplica (punto 3). Lo que sigue describe la fase 1.

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
  `heartbeat_timeout` 30 s y `RetryPolicy(maximum_attempts=5)`. Los intentos se
  reparten en ~3 min (5 s, 15 s, 45 s y 2 min de espera). Es acotado, pero un vault
  o un Temporal que tarda un par de minutos en volver no deja el mensaje en
  *Failed*: Meta ya tiene su 200 y no lo re-entrega. Un worker que muere a mitad
  (deploy, OOM) se nota por el heartbeat, y el ingest se repite en el worker nuevo.
- **Un error de programación** (`TypeError`, `KeyError`, `AttributeError`…) no se
  reintenta. Reintentar no lo arregla y cada intento repetiría Jev, el cupón en
  Medusa, el reloj del watchdog y analytics.
- **Si se agotan los intentos,** el workflow queda *Failed* en Temporal, a la vista
  y reseteable desde la UI. En el ledger queda `ingest_failed`, como hoy.
- **Lo que un reintento no puede repetir** pasa por un diario por `wamid`
  (`InboundEffects`, archivos marcadores en `vault/_ledger/inbound_effects/<día>/`,
  creados con `O_EXCL`). Se conserva y se consulta tanto como la retención de
  Temporal (30 días), así un reset desde la UI días después tampoco repite nada:
  - `history`: la línea del cliente en el JSONL de la sesión. Se marca después de
    escribirla; un reintento no la duplica en el panel. Escribir y marcar no tiene
    ningún `await` en medio, así que una cancelación no cae entre los dos.
  - `turn`: el mensaje a la sesión de ventas (`LoadOrStartSalesSession.execute`),
    una vez por `wamid`. Hay un candado por `wamid`: dos intentos que se superponen
    (un heartbeat vencido con el primero vivo) pasan de a uno, y el segundo ve la
    marca. El aviso corre protegido de la cancelación: si el worker se apaga
    mientras avisa, el aviso termina y deja su marca. La sesión V2 no deduplica
    por `wamid`, así que sin esto el bot contestaba dos veces.
  - `send:<sha256(texto)[:16]>`: cada envío directo al cliente («Recibí tu
    documento», el audio que no se entendió, el comprobante recibido). Se
    **reclama antes** de enviar: un envío de cortesía sale como mucho una vez.

  El resto del ingest (lecturas, metadata con merge de tres vías, cupón ya marcado
  en la sesión) se puede repetir sin daño. Solo una caída del **proceso** entre un
  efecto y su marca lo repite: milisegundos en el historial, la duración del RPC
  en el aviso a la sesión. En el peor caso se repite una línea, nunca se pierde el
  mensaje. Un disco que falla al escribir la marca deja pasar el efecto
  (*fail-open*, como el ledger).
- **La foto cancelada antes de leerse** no deja `PhotoReads` trabado. Si la
  activity se cancela entre «empieza a leerse» y lanzar la lectura, se libera; si
  no, cada texto de ese cliente esperaría 10 s hasta reiniciar el worker.

  **Límite conocido (fase 1; lo cierra la fase 2):** un reintento no es un replay. Lo que el intento anterior ya
  dejó en la metadata (el episodio abierto, el referral visto, el cupón aplicado) lo
  ve el nuevo intento como ya hecho. Si el worker murió justo entre abrir el
  episodio y avisar a la sesión, el turno puede salir sin la nota de primer
  contacto. Los eventos del watchdog y de analytics se pueden emitir dos veces. Es
  la ventana de una caída a mitad de un ingest de segundos; lo cierra la fase 2
  (cada paso, su activity).

### 7 · El ledger de entrada sigue como verificación cruzada

El webhook sigue escribiendo `request` y `seen` del body crudo, y agrega `queued`,
`duplicate` o `enqueue_failed`. La activity escribe `ingested` o `ingest_failed`
(antes lo hacía `_ingest_ledgered`). En el resumen, `queued` sin desenlace cuenta
como `pending` (está en Temporal), no como `lost`. Pasada media hora a la hora
del reporte es `stuck`: ningún ingest tarda eso, el workflow está varado (ver el
runbook).

## Runbook

- **Deploy:** la imagen nueva de la API ya arranca `InboundMessageWorkflow`. Hasta
  que el worker `sales` nuevo lo registre, el worker viejo hace fallar esos workflow
  tasks y Temporal los reintenta. No se pierde nada: se demoran hasta que el
  worker nuevo los toma. Justo después del deploy se aplica el `terraform apply`
  del módulo `app-instance` (patrón de CloudWatch de la API y la alarma
  `sales-worker-down`). Entre el deploy y el apply, la serie de RSS de la API se
  corta.
- **Rollback:** **no** volver el worker `sales` a una imagen anterior a este cambio
  sin drenar.
  - Esa imagen no registra el tipo: los `inbound-*` que estén corriendo quedan
    varados, porque sus workflow tasks fallan para siempre. Las activities ya
    agendadas queman sus 5 intentos y quedan *Failed*.
  - Meta no los re-entrega: ya tuvo su 200.
  - Si hay que volver atrás, ir a otra imagen que ya registre
    `InboundMessageWorkflow`.
  - Si no queda otra, primero esperar a que no haya ninguno corriendo:
    `WorkflowType="InboundMessageWorkflow" AND ExecutionStatus="Running"` en la UI de
    Temporal.
- **Mensajes varados** (`stuck` en el reporte del ledger, o la alarma
  `sales-worker-down`):
  - Buscar `inbound-<wamid>` en la UI de Temporal.
  - Si está *Running* esperando worker: levantar el worker `sales`.
  - Si está *Failed*: corregir la causa y hacer *Reset* al primer workflow task. El
    diario de efectos evita repetir lo que el intento anterior ya hizo.
- **Worker `sales` caído:** la alarma `sales-worker-down` (SNS) suena tras 5 min
  sin métrica. Mientras está caído, los mensajes esperan en Temporal: ni el bot ni
  el panel ni la App los ven (ver Consecuencias).

## Fase 2 (implementada el 2026-10-16)

`InboundMessageWorkflow` corre el camino nuevo detrás de
`workflow.patched("inbound-steps-v1")`. Los runs de la fase 1 que estén vivos al
desplegar lo reproducen sin la marca y terminan por el camino viejo: el ingest
entero en `ingest_inbound_message`. Por eso esa activity y
`wait_inbound_predecessor` siguen registradas. Las cubre la fixture
`history_inbound_message_chained_v1.json`.

### 1 · El ingest en pasos

`IngestInboundMessage` expone los pasos. Cada uno toma y devuelve un DTO chico,
frozen y JSON (`contracts.py`). El documento de la sesión se queda en el vault y
cada paso lo relee. Así no viajan a Temporal Cloud la metadata (PII y tamaño) ni
una copia que otro escritor haya cambiado mientras tanto.

| Paso (activity) | Hace | Devuelve |
|---|---|---|
| `read_inbound_state` | lee la sesión; origen y referral; acuse de despedida y lecturas con Jev; rota el episodio; mide la ventana; escribe todo **en una escritura** | `IngestState`: las notas que salen de esa decisión |
| `prepare_inbound_turn` | carrito web, producto de la web o de la ficha, cupón de la campaña, aviso al watchdog, texto efectivo, PDF, citas, lectura del cupón, «fuera de catálogo» | `IngestTurn`: el turno armado, o por qué no hay (foto, nota de voz, nada que escribir, solo al chat) |
| `write_inbound_history` | la línea del cliente en el JSONL y `last_inbound_message_id` | — |
| `deliver_inbound_turn` | las notas que salen del documento ya releído y el mensaje a la sesión | — |

`execute` sigue existiendo: corre los mismos pasos en el proceso (`run`) para los
tests y quien llame al caso de uso directo.

**Desviación del plan:** «leer el estado» y «las lecturas con Jev» son **un solo
paso**. El episodio que rota el mensaje y lo que escriben las lecturas van en una
sola escritura, bajo la guarda de la toma humana (#393).

- Si se separan, la metadata tendría que viajar en el historial de Temporal (PII
  y tamaño).
- O la rotación quedaría escrita antes de esperar a Jev, y una persona que toma el
  chat durante esa espera ya no podría deshacerla.

Lo que el estado decide (la nota de frontera, la campaña, el resumen del episodio
anterior; nunca el episodio entero, que lleva los datos de envío del borrador)
queda en el resultado del paso. Un reintento posterior ya no lo pierde: era el
«límite conocido» de la fase 1.

**El diario de efectos** deja de ser lo que evita repetir pasos ya hechos; eso lo
hace el historial del workflow. Sigue dentro de cada activity para lo que su
propio reintento no puede repetir:

- `send:` reclama antes de enviar. Una cortesía sale como mucho una vez.
- `turn` lleva el candado por `wamid` y el aviso protegido de la cancelación
  (PM-003).
- `history` cubre los milisegundos entre escribir la línea y que la activity
  termine.

El paso del estado se reintenta si su escritura falla (`write_failed`). Los demás
escriben de mejor esfuerzo, como en la fase 1.

### 2 · La foto y la nota de voz, cada una su paso

`describe_inbound_image` y `transcribe_inbound_audio` laten cada 10 s, con 3
intentos (cada uno cuesta) y sin reintentar errores de programación.

- Devuelven la **reentrada como texto** (`<wamid>_vision` / `<wamid>_transcribed`).
  El workflow la corre por los mismos pasos, con la llegada de la foto como reloj.
- Al terminar la foto (también si su lectura falló), `notify_inbound_photo_done`
  le avisa a la sesión de ventas. Así su ráfaga deja de esperarla.
- Se retiraron:
  - `_spawn_safe`: los eventos de analytics y el aviso al watchdog se esperan con
    tope de 5 s (`_best_effort`), sin tareas sueltas (L-7);
  - `PhotoReads` y `use_cases/photo_reads.py`;
  - `transcribe_audio_activity`, que estaba muerta (del worker, del
    laboratorio y del módulo).
- La transcripción viva saca **solo su pendiente**, con un `update()` atómico. Si
  mientras tanto llegó otra nota de voz, la suya se queda.

### 3 · `PhotoReads` → `InboundSessionWorkflow` y 4 · orden entre POSTs

Ver `docs/adr/2026-10-16-inbound-session-workflow.md`.

- Cada mensaje se anuncia al workflow de su cliente (`inbound-wa_<número>`), que le
  responde con su anterior.
- Las lecturas corren en seguida.
- Antes de `write_inbound_history`, el mensaje espera (tope de 30 s) a que termine
  el workflow de su anterior: el del mismo POST (`after`) y el que dijo el workflow
  del cliente.

La ráfaga llega junta y en orden de llegada al webhook, también entre POSTs. El
texto escrito detrás de una foto entra después de la foto. Cae el invariante de
una réplica del worker `sales`.

### 5 · El reloj del ingest

`received_at_ms` (la llegada al webhook) mide «llegó tarde» (`sent_at_ms`) y
«fuera de ventana» (`arrived_after_window`). Un ingest que se reintenta minutos
después no vuelve tarde un mensaje que llegó a tiempo. Las demás horas del
ingest (episodio, cupón) siguen siendo las del paso del estado, una sola para
todos los pasos.

### Runbook de la fase 2

- **Deploy:** la API no cambia. El worker `sales` nuevo toma los runs nuevos por
  pasos; los de la fase 1 que estén vivos terminan por su camino.
- **Rollback:** **no** volver el worker a una imagen de la fase 1 con
  `InboundMessageWorkflow` corriendo.
  - Sus histories ya tienen la marca `inbound-steps-v1` y las activities nuevas, y
    el código viejo no las reproduce.
  - Antes, drenar: `WorkflowType="InboundMessageWorkflow" AND
    ExecutionStatus="Running"`.
  - Los `InboundSessionWorkflow` que queden se pueden terminar sin perder nada: no
    guardan mensajes.
- **Mensajes varados:**
  - Un paso *Failed* se ve con su nombre en la UI de Temporal.
  - *Reset* al workflow task anterior a ese paso: los pasos anteriores no se
    repiten.
- **Retirar la fase 1:** cuando ya no quede ningún `inbound-*` sin la marca
  (minutos después del deploy), un PR aparte puede:
  - quitar `_whole_ingest`, `ingest_inbound_message` y `wait_inbound_predecessor`;
  - pasar el patch a `workflow.deprecate_patch`;
  - retirar la fixture v1.

### Lo que la fase 2 no hace

- **La I/O síncrona del ingest en el event loop** (lo anotó el premortem para esta
  fase): lecturas y escrituras del vault, `count_session_jsonl_lines`. Siguen
  bloqueando el loop del worker mientras duran. Queda para un cambio aparte.
- **El punto único de entrada** (el worker `sales` caído deja los mensajes sin ver)
  sigue diferido a su propio ADR.
- **El orden usa la llegada al webhook, no el `timestamp` de Meta** (que viene en
  segundos). Un rezagado de días ya lo marca «llegó tarde».

## Consecuencias

- **Un deploy o un OOM ya no pierden mensajes en vuelo.** Si la API no está, Meta
  reintenta; si el worker no está, el mensaje espera en Temporal.
- **El 200 a Meta espera un RPC a Temporal Cloud por mensaje** (~50–150 ms; un
  batch se arranca en serie para que el `after` exista).
- **Una caída de Temporal Cloud corta la entrada**, con 503 hasta que vuelva.
  Meta reintenta por días, así que los mensajes se demoran pero no se pierden.
  Volver a `BackgroundTasks` es revertir el commit.
- **El worker `sales` pasa a ser el único camino de entrada.** Antes, con el worker
  caído, la API igual escribía la línea del cliente en el historial y el equipo la
  veía en el panel y en la App (podía contestar a mano). Ahora, con el worker caído
  o en crash loop, los mensajes esperan en Temporal sin que nadie los vea, ni
  siquiera en los hilos con el bot apagado.
  - **Mitigación de esta fase:** la alarma `sales-worker-down` y el `stuck` del
    ledger.
  - **Arreglo de fondo (requiere ADR propio):** que la entrada no dependa del
    worker del bot. Por ejemplo, un worker de ingest separado o la línea del
    historial escrita por la API bajo el diario `history`.
- **Costo en Temporal Cloud:** por mensaje entran
  - el workflow;
  - la activity del ingest (2 si viene encadenado);
  - al menos un heartbeat facturable (`with_heartbeat` late al empezar);
  - el input guardado dos veces en la historia.

  Son ~6–8 acciones por mensaje: centavos al mes con el volumen de hoy.

  **Fase 2:** son el anuncio, los 4 pasos (6 con una foto o una nota de voz), sus
  heartbeats, las dos señales con el workflow del cliente y la espera al anterior
  cuando lo hay. Quedan ~15–20 acciones por mensaje, 2–3 veces las de la fase 1.
- **El primer deploy** puede levantar la API antes que el worker. Los workflows
  esperan (o fallan sus workflow tasks y se reintentan) hasta que el worker nuevo
  los toma (ver el runbook).
- **`run_api.py`** deja de usar `reload=True` por defecto: en prod era un proceso
  supervisor y un hijo `multiprocessing.spawn` sin ninguna ganancia (el código
  vive en la imagen). Para recargar en el host se pide con `UVICORN_RELOAD=1`. El
  patrón de CloudWatch de la API (`monitoring.tf`) pasa a `run_api.py`, que cubre
  los dos modos; requiere `terraform apply` justo después del deploy.
- **Premortem del 2026-10-16:** 16 modos de falla; los arreglos de este ADR salen
  de ahí.
  - **Diferido:** el arreglo de fondo del punto único de entrada (arriba).
  - **Lo cubre la Ola 0 (#444):** las conexiones a Temporal por mensaje dentro del
    worker, con su cliente único por proceso.
  - **Quedaba para la fase 2:** la I/O síncrona del ingest en el event loop.
    La fase 2 no la tocó (ver «Lo que la fase 2 no hace»).
