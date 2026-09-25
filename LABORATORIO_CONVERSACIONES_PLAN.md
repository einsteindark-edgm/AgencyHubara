# Laboratorio de conversaciones y bot con clasificador — plan de ejecución

> **Estado:** aprobado por el operador el 2026-09-23 (decisiones en §1). Clasificadores: **Jev y OpenAI, los dos servidos por OpenRouter** (§1.3); falta confirmar el modelo exacto de OpenAI.
> **Plan visual:** https://claude.ai/artifact/4UNdDwmnQiFFEnPL9r9APe (diagramas y maquetas clicables).
> **Antes de implementar cualquier pantalla, revisar el diseño del plan visual** (§11): el modal del hilo con su diagrama de secuencia, la sección Laboratorio y el botón **Nueva corrida**. Copia en el repo: `LABORATORIO_CONVERSACIONES_DISENO.html`.
> **Verificado contra:** `main` 82055bf6 (la ejecución arrancó sobre `main` ce723d3f) y lectura de solo lectura de la caja de producción el 2026-09-23.
> **Regla de trabajo:** TDD en cada PR (test rojo primero), panel de gates verde, sin PRs apilados. Todo cambio de workflow lleva replay contra historias reales de producción.

---

## 0. Resumen

**Problema.** Cuando el cliente escribe en ráfaga (varios mensajes seguidos, o un mensaje con varias preguntas), el bot de ventas no responde todos los asuntos ni lleva el hilo. En el código hay cinco causas:

1. Turnos de una sola acción: seis tools cortan el turno.
2. Guardas que se llevan el texto entero.
3. La ráfaga llega como un bloque.
4. Nadie lleva los pendientes de un turno al siguiente.
5. El evaluador EST-08 está ciego ante las ráfagas.

**Solución.** El mismo bot, con tres capas nuevas dentro del turno que ya existe, alrededor de la llamada a exoclaw:
- **① antes:** un clasificador (Jev, de TypeSafe, o un modelo pequeño de OpenAI, los dos servidos por OpenRouter) responde preguntas cerradas sobre la ráfaga.
- **② durante:** si falta cubrir un asunto, el LLM tiene una ronda más.
- **③ al final:** se verifica la cobertura antes de enviar.

Las capas solo corren si el modo de la sesión lo permite. Con el modo apagado, el turno es exactamente el de hoy.

**Cómo decidimos.** Un laboratorio toma todas las conversaciones reales desde el 2026-09-10 y hace que cada bot responda cada turno sobre el prefijo real, sin inventar al cliente. A las respuestas se les pasa el mismo scorecard de producción. Una sección nueva del dashboard, **Laboratorio**, muestra las mismas gráficas para cada bot, el hilo de cada turno (modal tipo Make) y las evaluaciones por chat. El bot actual re-simulado es el control. Cada corrida se lanza con el botón **Nueva corrida** de esa sección; no hay nada programado.

**Cómo se publica.** No hay un segundo chatbot. El modo por sesión avanza así:
1. Apagado.
2. Sombra: las capas corren sobre clientes reales sin cambiar la respuesta.
3. Canary: primero tus números de prueba, después el 10 % y el 50 % de las sesiones. Se mide con el scorecard de producción.
4. Encendido.

Hay un interruptor de apagado que actúa en el siguiente mensaje, sin redeploy.

**Qué no cambia.** Hasta la Fase 6 ningún cliente ve nada distinto. Chats no se toca hasta la Fase 5.

---

## 1. Decisiones aprobadas (2026-09-23)

| # | Decisión | Detalle |
|---|---|---|
| 1 | **Lógica en `chats`, caja propia, plugin `lab` como vitrina** | La simulación y la evaluación viven con el agente: worker `sales_lab` y scorecard de `sales_eval`. Así se respeta la decisión cerrada de `PLUGIN_CONTRACT.md` §5.2: "evals NO es un plugin". El plugin nuevo `lab` es la sección Laboratorio del dashboard. Es de solo lectura (su única acción es lanzar o cancelar una corrida), lee el contrato `lab@v1` por cast y se apaga con `ENABLED_PLUGINS`. |
| 2 | **Jev por OpenRouter, con datos anonimizados** | Jev (`typesafe/jev-1.13`) está en OpenRouter desde el 2026-09-18: ya no hace falta el acceso anticipado ni una cuenta en TypeSafe. Las ráfagas van anonimizadas. El operador revisa la privacidad de OpenRouter y la política de TypeSafe, que procesa las preguntas. |
| 3 | **Clasificadores: Jev y OpenAI, los dos por OpenRouter** | Decidido por el operador el 2026-09-23. B = Jev y C = OpenAI, con una sola llave (`OPENROUTER_API_KEY`). DeepSeek y Gemini quedan fuera. Falta confirmar el modelo de OpenAI: por OpenRouter, `gpt-5.4-nano` no da logprobs y se recomienda `gpt-4o-mini-2024-07-18` (§1.3). |
| 4 | **Vara de promoción** | Propuesta en §8.3. Se ajusta con las primeras gráficas. |
| 5 | **Temporal en el laboratorio** | La caja del laboratorio corre su propio Temporal en modo desarrollo: un solo proceso con base SQLite, no el clúster completo. Sin bypass: ver §3.3. |
| 6 | **Llaves de la caja** | Solo las de los LLM: DeepSeek para el bot, Gemini para el juez y OpenRouter para Jev y OpenAI. Ni WhatsApp, ni Medusa, ni Meta, ni Temporal Cloud: ver §3.5. |
| 7 | **Activación por botón** | Sin cron ni horario. Cada corrida se lanza con el botón **Nueva corrida** de la sección Laboratorio y exporta el banco en ese momento. Pedido del operador el 2026-09-23. Ver §3.7. |
| 8 | **Diseño visual de referencia** | El plan visual es la referencia de diseño de la UI: el modal del hilo con su diagrama de secuencia, la sección Laboratorio y el botón. Se revisa antes de implementar (§11). Pedido del operador el 2026-09-23. |

### 1.3 Clasificadores: Jev y OpenAI, los dos por OpenRouter (decidido el 2026-09-23)

**Decisión del operador:** el brazo B es **Jev** y el brazo C es **OpenAI**, los dos servidos por **OpenRouter**: una sola cuenta, una sola factura y una sola llave (`OPENROUTER_API_KEY`). DeepSeek queda fuera porque ya es el modelo del agente, y Gemini porque no da probabilidades reales.

**Verificado en OpenRouter el 2026-09-23** (API pública de modelos y página de cada modelo):

| | Brazo B: Jev | Brazo C: OpenAI |
|---|---|---|
| Id | `typesafe/jev-1.13`, fijo. No usar `Jev Latest`, que cambia de versión solo. | Recomendado: `openai/gpt-4o-mini-2024-07-18`, snapshot con fecha (ver el ajuste abajo). |
| API | **Decisions API** de OpenRouter, en alpha: `POST https://openrouter.ai/api/alpha/decisions`. No es la API de chat, así que Jev **no pasa por LiteLLM**. | Chat completions, compatible con OpenAI: pasa por LiteLLM como el resto de los modelos. |
| Probabilidades | Nativas y calibradas: `noul` (probabilidad de sí), `choice` (distribución sobre las opciones) y `score` (posición en una rúbrica ordenada). | Logprobs (`logprobs` y `top_logprobs`), servidos solo por el proveedor OpenAI. |
| Precio | US$0,042 por millón de tokens de entrada; la salida es gratis. | US$0,15 por millón de entrada y US$0,60 por millón de salida. |
| Costo por turno* | ≈ US$0,0002 | ≈ US$0,0006 |
| Latencia | p50 0,25 s, p95 ≈ 0,47 s y p99 ≈ 1 s (OpenRouter, última semana). | Se mide en el PR 5. |
| Disponibilidad | 99,80 % en 3 días, con un solo proveedor: TypeSafe. | Proveedor OpenAI, sin fallback a otro. |

\*Dos llamadas por turno, con unos 2.000 tokens de entrada cada una.

**El ajuste que falta confirmar:** `openai/gpt-5.4-nano`, el que recomendé pensando en OpenAI directo, **no devuelve logprobs por OpenRouter**. Ninguno de sus cuatro endpoints (OpenAI, OpenAI flex, Azure y Azure US) los ofrece. Sin logprobs, OpenAI solo daría una confianza escrita por el propio modelo, que suele estar mal calibrada, y la comparación con Jev dejaría de ser justa. Por OpenRouter, el modelo barato de OpenAI que sí los devuelve es `gpt-4o-mini`, con el snapshot fijo `2024-07-18`. Es más viejo que nano; para eso está la arena: medir si clasifica bien. Alternativas:
- `openai/gpt-5.4-nano` por OpenRouter, sin logprobs (confianza declarada).
- `openai/gpt-oss-120b` por OpenRouter, fijando proveedores que sí dan logprobs (Cerebras, Novita, Parasail o DigitalOcean, entre otros). El razonamiento no se apaga del todo, así que hay que calibrar.
- `gpt-5.4-nano` directo con OpenAI, que sí da logprobs, pero pide la cuenta de OpenAI que se quiere evitar.

**Cómo se usa cada uno** (perfiles en §4.2):
- **Jev:** adaptador nuevo `openrouter_decisions`, con httpx. Las preguntas de `rafaga-v1` ya tienen la forma nativa de Jev: `noul`, `choice` o `score`, con instrucciones y criterios. El `state` es la ráfaga anonimizada, mensaje por mensaje con su hora, más los asuntos pendientes. Tope de 32K de contexto.
- **OpenAI:** adaptador `litellm`, con el alias `openrouter-perception`. Cada pregunta se responde con un código de un solo token; la probabilidad sale de los logprobs, renormalizada sobre las opciones permitidas, y se calibra por pregunta con los turnos que etiquetó el juez (temperature scaling o regresión isotónica). Así las respuestas de los dos brazos tienen **la misma forma** y se comparan con Brier y error de calibración.
- **Preferencias de proveedor**, fijadas en el alias de LiteLLM: `provider: {order: ["openai"], allow_fallbacks: false, require_parameters: true, data_collection: "deny"}`. Con `require_parameters`, si OpenAI dejara de dar logprobs, OpenRouter rechaza la llamada en vez de ignorar el parámetro sin avisar, y el adaptador falla abierto. `zdr: true` (retención cero) se prueba en el PR 5; si no hay endpoint con retención cero, se decide contigo.

**Riesgos que trae OpenRouter:**
- La API de decisiones está en **alpha** y puede cambiar. Mitigación: test de contrato con respuestas grabadas, validación de la forma de cada respuesta y fail-open. El plan B de Jev es la API directa de TypeSafe, con otro adaptador que cumpla el mismo contrato del puerto.
- OpenRouter es un intermediario más en la ruta de los datos. Las ráfagas salen anonimizadas, y hay que revisar su configuración de privacidad y la política de TypeSafe, que es quien procesa las preguntas de Jev.
- Si OpenRouter se cae, se caen Jev y OpenAI a la vez. El turno sale como hoy (fail-open), y la sombra mide las caídas antes de encender nada.

**Contexto de la investigación anterior:** ningún Gemini 3.x de texto devuelve logprobs ([Google, 2026-08-05](https://discuss.ai.google.dev/t/missing-logprobs-support-in-the-newest-gemini-models-3-1-pro-3-6-flash-on-vertex-ai-and-ai-studio/176557)); Gemini 2.5 Flash-Lite se retira en octubre de 2026; Groq, Mistral y Amazon Nova no devuelven logprobs; Cohere Classify está descontinuado.

---

## 2. Hechos de producción que fundamentan el plan

Leídos el 2026-09-23 en `/mnt/hubara-vault`, con scripts de solo lectura que devolvieron conteos sin teléfonos.

| Dato | Valor |
|---|---|
| Conversaciones con mensajes desde el 10-sep | 82 (91 episodios: 10 compras, 10 rechazos, 63 abiertos) |
| Mensajes de clientes | 615 |
| Turnos con traza completa (`turn_traces.jsonl`, desde el 15-sep) | 404: 309 del cliente y 95 del sistema ("fantasma") |
| Grupos de mensajes seguidos del cliente que son ráfagas (2 o más) | 20 % (74 de 2, 13 de 3, 6 de 4, 2 de 5 o más) |
| Tiempo entre mensajes de una ráfaga | mediana 11 s, p75 30 s; el 56 % llega dentro de 12 s |
| Turnos del cliente con 2 o más asuntos (heurística por palabras clave) | 62 de 309 (20 %) |
| Turnos con texto del LLM descartado junto a una tool | 100 de 404 (25 %) |
| Conversaciones con mensajes escritos por una persona del equipo | 30 (311 mensajes) |
| Scorecard, último por episodio | PASA 27 · ALERTA 54 · FALLA 9 · SIN_DATOS 1 |
| Checks que más fallan (episodios) | EST-06: 50 · APE-03: 32 · EST-03b: 9 · APE-01: 8 · CON-05: 8 · ENV-02: 6 · EST-07: 6 · EST-08: 4 (ciego) |
| Costo real de LLM de esos 91 episodios | US$7,09 (47 M tokens de entrada) |
| Caja de producción | t3.medium, 2 vCPU, 3,8 GB, **sin swap**, ~450 MB libres; LiteLLM ocupa ~980 MB |
| Historial completo del LLM (con tools) | `vault/agent_state/<slug>/sessions/*.jsonl` (82 archivos de ventas) |
| Scorecards | `_evals/scorecards/YYYY-MM-DD.jsonl`. Una falla solo apunta al **primer** turno que falla |

**Caso típico** (anonimizado): el cliente escribe "¿me mandas el catálogo?" y 7 s después "¿y el envío a Bogotá cuánto sale?". El bot llama `send_shipping_rates`, cuya descripción dice "el mensaje fijo ES tu respuesta (tu turno termina)". El catálogo queda sin respuesta y EST-08 no lo marca porque no hay "?".

---

## 3. Arquitectura

### 3.1 Componentes

| Componente | Dónde vive | Qué hace |
|---|---|---|
| Bot de ventas | `chats`, caja de producción | El mismo `HubaraSalesSessionWorkflow`, con las capas ①②③ detrás del modo. |
| Puerto de percepción | `src/platform/perception/`, expuesto por `src.sdk.connectorkit` | Hace las preguntas tipadas. Adaptadores: `fake`, `null`, `litellm` (OpenAI por OpenRouter) y `openrouter_decisions` (Jev por OpenRouter). Perfiles en YAML. |
| Worker `sales_lab` | `chats`, **solo en la caja del laboratorio** | Importa el banco, arma los casos por turno, simula los bots en un sandbox, evalúa con el scorecard y sube los resultados a S3. |
| Lanzador de corridas | `chats` (API y worker `sales_eval`), caja de producción | Al pulsar **Nueva corrida**: exporta el banco a S3, prende la caja del laboratorio, le da la orden por SSM y sigue el avance (§3.7). |
| Caja del laboratorio | EC2 aparte en la misma cuenta de AWS que producción (no tu Mac), bajo demanda (t3.large) y con autostop | Temporal en modo desarrollo (un contenedor), LiteLLM propio y disco propio. No tiene llaves de producción. |
| S3 privado | bucket `hubara-lab-<cuenta>` | `bench/` (entrada) y `runs/` (resultados). |
| Plugin `lab` | dashboard, sección "Laboratorio" | Vitrina de solo lectura: conversaciones, resumen, banco y corridas, y el modal del hilo. Su única acción es el botón **Nueva corrida** (y cancelarla). |
| Calidad LLM (`agents_admin`) | dashboard | Sigue solo con producción. En el canary suma el filtro "bot actual / bot nuevo". |
| Chats | dashboard | Sin cambios hasta la Fase 5. Ahí recibe el modal del hilo con trazas reales. |

La caja del laboratorio no va en la caja de producción: con ~450 MB libres y sin swap, un simulador podría hacer que el sistema operativo mate un proceso del bot real. El patrón de caja bajo demanda ya existe: `src/platform/graphagents/launcher.py`, `boto3_launcher.py` e `infra/terraform/compute/modules/graphagents-instance/`.

### 3.2 El turno con las tres capas

Así es el turno de hoy, en orden:

1. `sales_session.py` coalesce la ráfaga (`_coalesce_batch` → `coalesce_inbox`).
2. Llama a `run_agent_turn(session, msg, has_new_input=…, admin_turn=…, align_history_with_episode=True)` (≈ línea 479).
3. Dentro de `run_agent_turn` (`src/platform/workflow_helpers.py`) corre exoclaw: `build_prompt`, el bucle `llm_chat` ⇄ `execute_tool` (≈ 688) con las reglas de corte (≈ 963–1031) y `record_turn` (≈ 1221).
4. Vuelta en `sales_session.py`, corren las guardas de ventas, `send_whatsapp_message_activity` (≈ 1072), `persist_assistant_message_activity`, `flush_pending_ui_intents_activity` (≈ 1126) y `persist_turn_trace_activity` (≈ 1196).

Dónde entra cada capa:

| Capa | Dónde entra | Qué reutiliza | Qué agrega |
|---|---|---|---|
| **① Antes** | `sales_session.py`, entre `_coalesce_batch` y `run_agent_turn` | La ráfaga coalescida y el `PendingMessage`, que exoclaw arma como siempre | La activity `perceive_burst`, el plan en código (módulo puro) y la lista de asuntos dentro del mensaje del turno. La activity `prefetch_turn_facts` precarga tarifas y medios de pago. |
| **② Durante** | `run_agent_turn`, en el punto de corte por tool | El mismo bucle de exoclaw | Parámetro opcional `turn_policy: TurnPolicy \| None = None`. Si falta un asunto, habilita **una** ronda extra. No agenda llamadas nuevas. Con `None` es el código de hoy, así que remarketing y ETA no se enteran. |
| **③ Al final** | `sales_session.py`, después de las guardas y antes de `send_whatsapp_message_activity` | Las guardas de hoy. El complemento es un **turno de sistema** como los "fantasma" (`[SISTEMA]: …`, `is_ghost_trigger`), así que pasa por exoclaw y `record_turn` queda en el historial y en el episodio. | La activity `verify_coverage`. Si falta un asunto: una burbuja de complemento, como máximo. Si hay duda: envía y deja el asunto pendiente. |
| **Modo** | La lectura de configuración que ya se hace en cada vuelta (`read_idle_timeout_seconds_activity`, ≈ 262) | La misma activity | El modo de la sesión: `off`, `shadow`, `canary` u `on`. |

Los **pendientes** no se guardan en memoria interna. El clasificador pregunta en la conversación qué quedó sin responder, así que funciona igual en el laboratorio, después de un reinicio y después de un `continue_as_new`.

### 3.3 Temporal en el laboratorio: un solo proceso, sin bypass

El bot no corre sin Temporal y no hay atajo. Lo que cambia es **a qué servidor de Temporal se conecta**, y eso ya lo decide `src/platform/temporal/client.py`:

- **Con `TEMPORAL_API_KEY`** se conecta a Temporal Cloud en `TEMPORAL_ADDRESS`. Así corre producción, con el worker de ventas en `queue-sales-agent`.
- **Sin llave** se conecta a `TEMPORAL_URL` (por defecto `localhost:7233`), sin TLS. Así corre el laboratorio, contra un Temporal que vive **dentro de la caja del laboratorio**, en AWS. Tu Mac no participa en las corridas.

**No se instala Temporal completo.** Hay tres formas de tener un servidor propio, y el laboratorio usa la más liviana:

| | Temporal autoalojado completo | Tu stack local de Docker | Caja del laboratorio |
|---|---|---|---|
| Qué es | El servidor como se opera en producción cuando no hay Temporal Cloud | `temporalio/auto-setup:1.24.2` | `temporal server start-dev`: el modo desarrollo del CLI oficial |
| Servicios internos (frontend, history, matching y worker) | Separados | Juntos, en un contenedor | Juntos, en un proceso |
| Base de datos | PostgreSQL, MySQL o Cassandra, y Elasticsearch opcional | Postgres 14 en otro contenedor (`local-temporal-db`) | SQLite: un archivo en el disco de la caja |
| UI | Aparte | Aparte (`temporalio/ui:latest`) | Dentro del mismo proceso |
| Contenedores | Varios, más la base de datos | 3 | 1 |

**Medido en tu Mac el 2026-09-23**, solo para ver cuánto pesa (en la caja corre el mismo programa), con el CLI 1.4.1 que ya tienes instalado con Homebrew y el SDK `temporalio` 1.25.0 del repo:
- arranca en 0,5 s;
- ocupa 72 MB de RAM en reposo y 100 MB después de correr un workflow;
- un workflow con un timer de 1 s y una activity termina en 1,07 s;
- la UI responde desde el mismo proceso.

La caja del laboratorio tiene 8 GB de RAM.

**Cómo queda en la caja:**
- **Un servicio más en el compose del laboratorio:** la imagen oficial `temporalio/temporal:1.9.1` (47 MB comprimida, publicada el 2026-09-14), **fijada por digest**. Si el digest no coincide, Docker no la corre.
- **Comando:** `temporal server start-dev --ip 0.0.0.0 --namespace hubara-lab --db-filename /data/lab.sqlite`. El puerto 7233 solo existe en la red interna del compose. La UI se publica en `127.0.0.1:8233` de la caja y se ve por redirección de puertos de SSM, sin puertos públicos (§7).
- **Base en archivo, no en memoria.** Una corrida de decisión lanza unos 3.600 turnos y en memoria la RAM crecería con cada uno. El arranque de la caja borra `/lab/temporal/`, así que cada arranque empieza con un Temporal vacío. Los resultados no dependen de esa base: van a S3.
- **El worker del laboratorio** es la misma imagen de GHCR que producción, con `TEMPORAL_URL=temporal:7233` y `TEMPORAL_NAMESPACE=hubara-lab`, y sin `TEMPORAL_API_KEY`, `TEMPORAL_ADDRESS` ni certificados. El namespace propio es un candado más: aunque alguien apuntara el worker a Temporal Cloud, allá `hubara-lab` no existe.
- **Registra** `SimSalesTurnWorkflow` y las activities de siempre:
  - las de exoclaw: `build_prompt`, `llm_chat`, `execute_tool` y `record_turn`;
  - las de ventas, con adaptadores de sandbox donde hay efectos (§3.6).
- `SimSalesTurnWorkflow` llama al **mismo** `run_agent_turn`, con la misma coalescencia, las mismas guardas y el mismo manejo de episodios. El código del turno es el de producción; solo cambia el servidor.
- **Las mismas funciones de Temporal que en Cloud:** el turno de ventas usa señales, consultas, timers, activities, `workflow.patched` y `continue_as_new`. No usa search attributes, workflows hijos ni updates, así que el modo desarrollo no necesita configuración extra.

**Prueba de que funciona:**
- Los tests del workflow ya corren el `HubaraSalesSessionWorkflow` real sobre el servidor de pruebas del SDK (`tests/test_sales_workflow_debounce.py`, con `WorkflowEnvironment.start_time_skipping`). El laboratorio usa el servidor real en modo desarrollo, que es más fiel que el de pruebas.
- Antes de cada corrida, un turno de humo del banco tiene que pasar. Si no pasa, la corrida no arranca.

**Por qué no Temporal Cloud desde el laboratorio:**
- Un error en el nombre de una cola haría que el laboratorio tomara turnos de clientes reales en `queue-sales-agent`.
- Con la llave de Cloud se podrían mandar señales a conversaciones vivas o terminarlas.
- Miles de workflows simulados ensuciarían la historia y la visibilidad de producción, y Cloud cobra por acción.
- Un namespace de laboratorio en Cloud, con una cuenta de servicio limitada a él, evitaría los dos primeros riesgos, pero pide crear esa cuenta y sigue cobrando por acción. El servidor de la caja es gratis y no necesita credenciales.

### 3.4 Dónde quedan los datos

**Cómo llegan las conversaciones de producción a la caja**, sin copias a mano y sin abrir acceso a producción:
1. Pulsas **Nueva corrida** en la sección Laboratorio (§3.7). El worker `sales_eval` de producción, el mismo que ya lee el vault para el barrido diario del scorecard, arma el banco en ese momento: conversaciones desde el 2026-09-10, historial del LLM, trazas, metadata, scorecards, catálogo, promociones y order facts. Solo lee el vault; no escribe en él.
2. Sube el banco al S3 privado (`bench/<banco>/`), con su manifiesto.
3. Prende la caja del laboratorio y le da la orden de correr por SSM, como ya se hace con la caja de GraphAgents (`src/platform/graphagents/boto3_launcher.py`). No hay conexión de red entre las dos cajas.
4. La caja baja el banco, corre los bots y sube los resultados a `runs/<corrida>/`.
5. La API de producción lee `runs/` y la sección Laboratorio del dashboard los muestra. La caja se apaga sola a los 10 minutos sin trabajo.

S3 lleva los datos y SSM lleva la orden; la caja del laboratorio nunca ve el disco del vault. Cada banco trae todo desde el 2026-09-10 hasta el momento del clic. No hay nada programado: si nadie pulsa el botón, no se exporta nada, la caja no se prende y no hay gasto.

La misma imagen no es el mismo despliegue. El código escribe donde le indican sus variables (`WORKSPACE_VAULT_DIR`, `EXOCLAW_STATE_DIR`, `CATALOG_SNAPSHOT_DIR`, `EVAL_*`). En la caja del laboratorio esas variables apuntan a un **sandbox por turno** que se crea desde el banco, truncado a ese turno, y se borra al terminar.

```
# Caja del laboratorio: disco propio; el vault de producción no está montado
/lab/bench/<banco>/                          copia del banco, solo lectura
    vault/wa_…/                               historial, metadata, trazas y scorecards reales
    agent_state/…                             historial real del LLM
    catalog/  promotions.json  order_facts/   snapshots del día
/lab/runs/<corrida>/<brazo>/<rep>/<turno>/    sandbox de UN turno: se crea, se usa y se borra
    vault/wa_…/metadata.json, sessions/, memory/MEMORY.md, evals/turn_traces.jsonl
    agent_state/…/sessions/wa_….jsonl

# S3 privado: lo único que sale de la caja
bench/<banco>/                               lo sube producción al pulsar el botón
runs/<corrida>/manifest.json                 banco, brazos, perfiles, imagen y versión del evaluador y del juez
runs/<corrida>/turns/<brazo>/<rep>/*.jsonl   traza v2 de cada turno simulado
runs/<corrida>/scores/<brazo>/<rep>/*.jsonl  checks por turno, con la forma del scorecard
runs/<corrida>/summary.json                  lo que dibujan las gráficas
runs/<corrida>/ids.json                      mapeo del id ficticio a la sesión real (solo en el bucket)
```

- Dentro del sandbox, cada número se reemplaza por uno ficticio con el mismo formato (`wa_570…`, que no existe en Colombia).
- **Nunca** se escribe en el vault de producción, ni siquiera en una subcarpeta. Más de diez partes del código de producción lo recorren buscando `wa_*`: reengagement, Order Sentinel, post-venta, pedidos, el barrido del scorecard, atribución y otras. Si apareciera una carpeta del laboratorio, el ciclo de reengagement podría escribirle a ese cliente.
- **En sombra y en canary no hay nada simulado.** Son conversaciones reales que escriben en el vault como hoy; solo se agregan campos a la misma traza.

### 3.5 Llaves y permisos

| Llave | Caja de producción | Caja del laboratorio |
|---|---|---|
| WhatsApp (`WHATSAPP_*`) | sí | **no** |
| Medusa (`MEDUSA_*`) | sí | **no** |
| Meta (`META_*`, CAPI) | sí | **no** |
| Temporal Cloud (`TEMPORAL_API_KEY`, `TEMPORAL_ADDRESS`) | sí | **no**: usa el Temporal de la caja (`TEMPORAL_URL=temporal:7233`) |
| DeepSeek y Gemini (LLM del bot y del juez) | sí | sí, con tope de gasto por corrida y por mes |
| OpenRouter (`OPENROUTER_API_KEY`: Jev y OpenAI) | sí, desde la sombra | sí; una llave aparte, con su propio límite de crédito |
| S3 del laboratorio | escribe `bench/` y lee `runs/` | lee `bench/` y escribe `runs/` |

Candados:
1. **Físico:** el disco del vault solo está conectado a la caja de producción.
2. **Permisos (IAM):** la caja del laboratorio no puede leer los parámetros de producción.
3. **Código:** el worker `sales_lab` no arranca si alguna carpeta apunta fuera de `/lab/`, si encuentra una llave de producción en su entorno o si `TEMPORAL_URL` no apunta al servicio `temporal` de la caja. Las variables se fijan **antes** de importar la app, porque la composición guarda rutas y clientes en caché con `lru_cache`.
4. **Test de fugas en CI:** falla si se escribió algo fuera del sandbox o si se abrió una conexión que no sea al LLM o al clasificador.

Precedente: la suite golden heredó el entorno de producción y escribió `wa_golden_*` en el vault real (arreglo en #338).

### 3.6 Sandbox: qué responde en lugar de cada efecto

Verificado en `src/plugins/chats/workers/sales.py`, líneas ≈ 136–260.

| Lo que hace el bot | En producción | En el sandbox |
|---|---|---|
| Correr el turno | Temporal Cloud | Temporal en modo desarrollo, dentro de la caja (§3.3) |
| Enviar texto o tarjetas | API de WhatsApp | Se captura; no sale |
| Buscar productos (`_catalog`) | Snapshot del catálogo (no llama a Medusa) | El mismo snapshot para A1, B y C, del día de la corrida |
| Promociones y cupones (`_promotions`) | Medusa en vivo, lectura | `promotions.json` exportado con el banco |
| Verificar precio y stock al cobrar (`_checkout_verifier`) | Medusa en vivo | Medusa falso que responde desde el snapshot |
| Registrar el pedido (`_order_registration_port`) | `POST /admin/draft-orders`: **pedido real** | Falso: registra "se habría creado" y devuelve un id ficticio. Ya existe `StubOrderRegistration` en `src/platform/orders/stub.py` |
| Estado de pedido (`_order_query_port`) | Medusa y OrderFacts en vivo | Lo que respondió de verdad en ese turno, si se consultó; si no, `order_facts/` del día de la corrida |
| Etiquetas, etapa y borrador | Vault | Vault del sandbox |
| Escalar a una persona | Notifica y cambia la ruta | Se registra; no notifica |
| CAPI y transiciones a otros agentes | Meta y Temporal de producción | Apagados o solo registrados |
| Hora | Reloj real | Hora original del turno, con reloj inyectado (saludos por hora, ventana de 24 h) |
| Telemetría | SigNoz | `OTEL_SDK_DISABLED=true`; el costo se lleva en el resumen de la corrida |

### 3.7 El botón Nueva corrida

No hay cron ni horario. Todo el proceso arranca con el botón **Nueva corrida** de la sección Laboratorio.

**El formulario:**
- **Bots:** A1 (el control, siempre marcado), B (Jev) y C (OpenAI), los dos por OpenRouter. Un bot sin llave aparece deshabilitado, con el motivo.
- **Repeticiones:** 1 (corrida rápida) o 3 (corrida de decisión).
- **Banco:** exportar uno nuevo en ese momento, o reusar el de una corrida anterior para comparar bots sobre los mismos casos.
- **Costo:** el estimado de la corrida, el tope por corrida y lo que queda del tope del mes. **Lanzar** queda deshabilitado si la estimación no cabe.

**Lo que pasa al lanzar** (workflow `LabLaunchWorkflow`, en Temporal Cloud, con el worker `sales_eval` de producción):

| Fase | Dónde corre | Qué hace |
|---|---|---|
| 1. Exportar | Caja de producción | Arma el banco y lo sube a S3 archivo por archivo. |
| 2. Prender la caja | AWS (EC2 y SSM) | Prende la caja del laboratorio y espera a que su agente de SSM esté en línea. |
| 3. Correr | Caja del laboratorio | Recibe la orden por SSM, baja el banco, corre los bots y escribe su avance en `runs/<corrida>/progress.json`. |
| 4. Evaluar | Caja del laboratorio | El mismo scorecard, turno por turno, y el juez. |
| 5. Listo | Dashboard | La corrida aparece en la sección. La caja se apaga sola a los 10 minutos sin trabajo. |

Mientras corre, la sección muestra la fase, los turnos hechos sobre el total y el gasto hasta el momento, y consulta el estado cada 10 s. **Cancelar** detiene la corrida: la caja guarda lo que alcanzó a correr y se apaga sola.

**Candados:**
- **Una corrida a la vez.** El workflow usa un id fijo (`lab-launch`) con la política de conflicto `FAIL`. Un doble clic o dos personas al tiempo no lanzan dos corridas: la API responde 409 y la sección muestra la que está en curso.
- **Una orden repetida no duplica la corrida.** Si la activity que da la orden se reintenta, la caja reconoce el id de la corrida y no la arranca dos veces.
- **Topes de gasto** por corrida y por mes (`LAB_MAX_USD_PER_RUN` y `LAB_MAX_USD_PER_MONTH` en Terraform; propuestos: US$120 y US$300). La API rechaza con 422 lo que no cabe, y la caja corta si el gasto real llega al tope.
- **La exportación no le quita memoria al bot.** Medido el 2026-09-23 a las 18:31 (solo lectura): la caja de producción tiene ≈ 420 MB libres y no tiene swap. El banco pesa unos 15–20 MB: 82 conversaciones (11,5 MB), historial del LLM de ventas (1,6 MB) y scorecards (2,0 MB), más catálogo, promociones y order facts. Se sube archivo por archivo, sin cargar el banco en memoria.
- **Durable.** Si producción se redespliega en medio de una corrida, el workflow sigue donde iba.
- **Sin reintentos que gasten.** Si la caja no prende, la orden no llega o la corrida se cae, la corrida queda como fallida con el motivo y la sección lo muestra. No se relanza sola.
- **Quién puede pulsarlo:** cualquiera con sesión en el dashboard (`require_auth`), igual que el resto de las acciones; hoy el dashboard no tiene roles (§9).

---

## 4. Contratos

### 4.1 Traza v2 (`<sesión>/evals/turn_traces.jsonl`, una línea por turno)

Extiende la v1 sin quitar campos, para que el scorecard no cambie. `TRACE_VERSION = 2`.

```jsonc
{
  "v": 2,                                   // lo pone enrich_turn_trace; ningún campo del payload puede llamarse v, session_id, episode_id, turn ni recorded_at_ms
  "turn_key": "run:<run_id>/t:<turn_count>", // determinista: no depende del contador de la activity (que puede correrse)
  "source": "prod",                          // o "lab:<corrida>:<brazo>:<rep>"
  "mode": "off|shadow|canary|on",
  "inbound": [{"seq": 41, "wamid": "wamid.…", "ts_ms": 0, "kind": "text", "text": "…"}],
  "steps": [
    {"i": 1, "at_ms": 0,    "kind": "perception", "model": "typesafe/jev-1.13", "profile": "jev-v1", "dur_ms": 420,
     "confidence": 0.91, "fallback": null, "answers": [{"q": "topic.catalogo", "type": "noul", "p": 0.96, "picked": true, "msg": 1}]},
    {"i": 2, "at_ms": 430,  "kind": "plan", "checklist": [{"topic": "catalogo", "msg": 1, "via": "card"}, {"topic": "envio", "msg": 2, "via": "text"}]},
    {"i": 3, "at_ms": 440,  "kind": "llm", "round": 1, "dur_ms": 1900, "tokens_in": 38412, "tokens_out": 96,
     "finish": "tool_calls", "tool_calls": ["search_products"], "text_fate": "none|sent|discarded_default_deny"},
    {"i": 4, "at_ms": 2350, "kind": "tool", "name": "search_products", "call_id": "…", "args": {}, "ok": true, "excerpt": "…", "dur_ms": 300},
    {"i": 5, "at_ms": 2700, "kind": "guard", "name": "variant_enumeration_guard", "before": "…", "after": "…"},
    {"i": 6, "at_ms": 2800, "kind": "restart", "reason": "checkpoint_a"},
    {"i": 7, "at_ms": 4300, "kind": "verify", "answers": [{"topic": "catalogo", "p": 0.95}], "decision": "send|complement|pending"},
    {"i": 8, "at_ms": 4800, "kind": "outbound", "bubbles": [{"seq": 44, "wamid": "wamid.…", "kind": "text", "delivered": true}]}
  ],
  "topics": [{"id": "catalogo", "msg": 1, "status": "covered|missed|pending"}],
  "context_notes": ["burst_note", "draft"]   // solo nombres de las notas inyectadas
  // + campos v1: trigger, inbound_text, tools, guards, discarded_narration, llm_text, sent_texts, suppressed_reason, stage_in/out, draft, state…
}
```

Límites: 600 caracteres por texto y 60 pasos por turno. Los lectores toleran v1: del 15 al 23 de septiembre hay trazas sin tiempos y con las guardas sin orden. Antes del 15-sep la fidelidad es `legacy`.

### 4.2 Puerto de percepción

```python
# src/platform/perception/ports.py  (DTOs frozen, JSON-serializables: R-JSON)
@dataclass(frozen=True)
class TypedQuestion:
    id: str                      # "topic.catalogo", "stage", "msg.2.topic"…
    kind: Literal["noul", "choice", "score"]
    text: str                    # instrucciones (el idioma lo fija el juego de preguntas)
    criteria: Mapping[str, str] | tuple[str, ...] = ()
                                 # noul: {"true": …, "false": …} · choice: {opción: descripción}, ≤ 255 · score: niveles en orden

@dataclass(frozen=True)
class TypedAnswer:
    id: str
    p: float | None = None       # noul: P(sí)
    choice: str | None = None
    probs: tuple[tuple[str, float], ...] = ()
    confidence: float | None = None

class PerceptionPort(Protocol):
    async def ask(self, state: str, questions: Sequence[TypedQuestion], *, timeout_s: float) -> PerceptionResult: ...
```

- `get_perception_port(profile_id)` vive en `src/platform/perception/composition.py`, con `lru_cache`.
- Se exporta de forma perezosa en `src/sdk/connectorkit/__init__.py`, igual que `get_image_vision_port`. El test `test_sdk_lazy_surface.py` sigue verde.
- Adaptadores:
  - `fake`: tests.
  - `null`: apagado.
  - `litellm`: OpenAI por OpenRouter, a través de LiteLLM; probabilidades desde los logprobs, renormalizadas y calibradas (§1.3). Un modelo sin logprobs solo daría confianza declarada.
  - `openrouter_decisions`: httpx contra `POST https://openrouter.ai/api/alpha/decisions` (la API de decisiones de OpenRouter, en alpha), con modelo fijo `typesafe/jev-1.13` (L-23). Manda `state` y `questions` en la forma nativa de Jev y recibe `noul`, `choice` o `score` con sus probabilidades. Valida la forma de cada respuesta; si no cuadra, falla abierto. Si hiciera falta la API directa de TypeSafe (plan B), es otro adaptador con el mismo contrato.
- Anonimización previa (`anonymize.py`): teléfonos, correos, nombres de perfil y direcciones, cuando el perfil lo pide.

**Juego de preguntas `rafaga-v1`**, unas 25 en una sola llamada:
- **Asuntos** (probabilidad de sí): catálogo, precio, envío, tiempos, pagos, medidas, color o variante, aroma, personalización, disponibilidad, estado de pedido, datos de envío, confirma compra, aplaza, queja, pregunta sobre una foto y saludo.
- **Hilo:** ¿responde a la última pregunta del bot? ¿repite algo que quedó sin respuesta?
- **Etapa** (elección entre 7) y **asunto principal de cada mensaje** (elección).

Verificación: "¿la respuesta (texto y tarjetas) atiende el asunto X del mensaje k?", una pregunta por asunto.

**Perfiles** (`src/platform/perception/profiles.yaml`, versionados):

```yaml
jev-v1:                                       # brazo B
  provider: openrouter_decisions              # POST https://openrouter.ai/api/alpha/decisions
  model: typesafe/jev-1.13                    # fijo; nunca "Jev Latest" (L-23)
  questions: rafaga-v1                        # forma nativa: noul, choice y score
  timeout_s: 3
  thresholds: {detect: 0.70, confidence: 0.60, covered: 0.70}
  anonymize: true
openai-lp-v1:                                 # brazo C
  provider: litellm
  model: litellm_proxy/openrouter-perception  # → openrouter/openai/gpt-4o-mini-2024-07-18
  params: {logprobs: true, top_logprobs: 20, temperature: 0, max_tokens: 64}
  questions: rafaga-v1-codes                  # un token por respuesta
  timeout_s: 3
  thresholds: {detect: 0.70, confidence: 0.60, covered: 0.70}
  anonymize: true
```

### 4.3 Contrato `lab@v1` (provisto por `chats`: lectura desde S3 y el lanzador de corridas)

| Endpoint | Devuelve |
|---|---|
| `GET /api/chats/lab/runs` | corridas: banco, brazos, repeticiones, estado, costo y versiones |
| `GET /api/chats/lab/estimate?arms=&reps=&bench=` | costo estimado de la corrida y lo que queda del tope del mes |
| `POST /api/chats/lab/runs` | lanza una corrida (bots, repeticiones y banco); 409 si ya hay una en curso y 422 si no cabe en el tope |
| `GET /api/chats/lab/runs/active` | fase, turnos hechos sobre el total, gasto y error de la corrida en curso |
| `POST /api/chats/lab/runs/active/cancel` | cancela la corrida en curso |
| `GET /api/chats/lab/runs/{run}/bench` | conteos del banco y exclusiones con motivo |
| `GET /api/chats/lab/runs/{run}/conversations` | lista de conversaciones con el veredicto de cada brazo |
| `GET /api/chats/lab/runs/{run}/conversations/{sid}?episode=` | el hilo: mensajes reales y, por turno, la salida de cada brazo, con sus `turn_key` |
| `GET /api/chats/lab/runs/{run}/conversations/{sid}/turns/{turn_key}/trace?arm=&rep=` | pasos listos para dibujar (modal) |
| `GET /api/chats/lab/runs/{run}/conversations/{sid}/evaluations?arm=` | checks por turno: veredicto, nivel, evidencia y crítica |
| `GET /api/chats/lab/runs/{run}/summary?arm=` | **misma forma** que `/api/chats/evals/checks/stats`: veredictos, Pareto, embudo y tendencia |
| `GET /api/chats/lab/runs/{run}/diff?base=A1&cand=B` | diferencia por check, con intervalo y "aún no concluyente" |

El plugin `lab` los consume por cast (`castkit.forward`, con timeout de toda la cadena, L-1) desde `/api/lab/...`, igual que `agents_admin/api/evals.py`. Se prueban 4 caminos: éxito, error del proveedor, timeout y no disponible. En `lab/plugin.yaml`: `consumes: [{provider: chats, contract: lab@v1, into: lab-views, cast: api/lab}]` y `depends_on: [chats]` (P-14).

### 4.4 Parámetros (Terraform es la fuente de verdad)

| Parámetro | Tipo | Dónde | Notas |
|---|---|---|---|
| `OPENROUTER_API_KEY` | SecureString | lista `secrets` en `infra/terraform/platform/variables.tf` | Placeholder con `ignore_changes`. Una llave para Jev y OpenAI; la caja del laboratorio tiene la suya en `/hubara/lab/*`. **El valor lo carga el operador**, con límite de crédito en cada llave. |
| `SALES_PERCEPTION_MODE_CEILING` | String | módulo de config autoritativo (patrón `mba-config`) | `off` por defecto. Es el techo: el dashboard no lo puede superar. |
| `SALES_PERCEPTION_PROFILE` | String | ídem | Perfil activo en producción. |
| `LAB_*` (bucket, instancia, `LAB_MAX_USD_PER_RUN` y `LAB_MAX_USD_PER_MONTH`) | String | ídem | Para la caja del laboratorio y el lanzador. Topes propuestos: US$120 por corrida y US$300 al mes. |
| Alias `openrouter-perception` en LiteLLM | YAML | `exoclaw-temporal/litellm_config.yaml`; en la caja, `/opt/hubara/litellm_config.yaml` | → `openrouter/openai/gpt-4o-mini-2024-07-18`, con las preferencias de proveedor del §1.3. Guard de id estable (`test_litellm_model_ids_stable.py`). Jev no pasa por LiteLLM: su id fijo vive en el perfil `jev-v1`. |

El estado del día a día (sombra, canary y porcentaje) lo escribe el control del dashboard en el vault, bajo `_rollout/perception.json`. Es un directorio `_*` que los recorredores de sesiones ignoran. Nunca supera el techo de Terraform, y apagar nunca se bloquea.

---

## 5. Evaluación

1. **EST-08 v2, lo primero.**
   - Sin el prefiltro de "?" (`judge_checks.py` ≈ 191); `render_transcript` sin el tope de 400 caracteres y respetando los saltos de línea (≈ 205).
   - El juez ve la ráfaga mensaje por mensaje y devuelve cobertura por asunto.
   - El juez saca los asuntos **por su cuenta**; nunca usa la percepción del brazo.
   - `REGISTRY_VERSION` pasa de 2 a 3.
2. **Modo por turno ("turno foco").**
   - Hoy `failed(check, turn)` reporta solo el primer turno que falla y los resultados que pasan tienen `turn=None`.
   - El motor recibe `focus_turn`: el prefijo real es contexto y solo se juzga ese turno.
   - El juez recibe una llamada por episodio y brazo, con las respuestas candidatas marcadas.
3. **Validación obligatoria:** el modo por turno tiene que reproducir las fallas del scorecard de producción en los 91 episodios. Queda como test permanente.
4. **Veredicto del episodio simulado:** misma regla (`verdict.py`) sobre los checks por turno. A0 se re-mide igual, así que las cuatro columnas quedan comparables.
5. **Checks sin señal en simulación:** los que dependen de turnos futuros (cierre, pedido registrado). No se modifican; se muestran como "sin señal".
6. **Estadística:** comparación pareada contra A1, con bootstrap por conversación, 3 repeticiones y pass^3. Cuando el intervalo cruza el cero, el tablero dice "aún no concluyente".
7. **Fidelidad del simulador:** A1 coincide con A0 en al menos el 90 % de los checks de código.
8. **Arena** (extra, sin etiquetado humano): acuerdo con los asuntos del juez, calibración, latencia p95, costo por turno y tasa de caída a "turno como hoy".
9. **Versiones:** cada corrida guarda la versión del registro y del juez. El permiso de `gemini-pro-judge` vence el 2026-12-18. No se mezclan corridas de versiones distintas.

---

## 6. Plan por PR

Formato de cada PR: objetivo, archivos, tests rojos primero, "lista cuando" y notas de producción.

### Fase 0 — Medir bien y registrar el hilo (los clientes no ven nada)

**PR 1 · EST-08 v2**
- Archivos:
  - `hubara_agency/src/plugins/chats/agent/sales_eval/scorecard/judge_checks.py`: prompt (≈ 132), prefiltro (≈ 191) y `render_transcript` (≈ 205).
  - `registry.py`: EST-08 (≈ 315) y `REGISTRY_VERSION`.
  - `trajectory.py`: conservar `\n` y usar `inbound[]` cuando exista.
- Tests rojos en `tests/evals/scorecard/`:
  - una ráfaga sin "?" con 2 asuntos y una respuesta que cubre 1: EST-08 aplica y falla, con juez falso;
  - los saltos de línea se preservan;
  - se parsea la cobertura por asunto.
- Lista cuando: marca el caso catálogo + envío; la calibración con 20 casos coincide al menos en un 90 % (revisión opcional del operador en la pestaña Calibración); backfill desde el 15-sep con `backfill.py`.
- Producción: cambia veredictos del scorecard, no el bot. Las gráficas muestran la versión 3.

**PR 2 · Traza v2 en el bot actual**
- Archivos:
  - `src/platform/workflow_helpers.py`: `TurnResult.steps` con default, que también devuelve el `TurnResult` interrumpido. Se registra cada `llm_chat` (ronda, `workflow.now()`, `finish_reason`, tools pedidas y uso), cada `execute_tool` (`call_id`, argumentos, resumen, inicio y fin) y cada decisión de corte: tag, escalación, empujón, sanitizador, fallback y Checkpoints A y B.
  - `sales_session.py`: log de guardas **en orden** con antes y después (se mantiene el campo v1 `guards`), intentos y reinicios, `turn_key` y burbujas enviadas.
  - `turn_trace.py`: `TRACE_VERSION=2` y límites.
  - `golden.py` (usa `build_turn_payload`).
  - `src/platform/whatsapp/activities.py`: el envío devuelve `[{wamid, text}]` entregados.
  - `flush_ui_intents.py`: devuelve `[{kind, wamid, ok}]`.
- Tests rojos:
  - `tests/plugins/chats/sales/test_turn_trace.py`: forma v2.
  - `tests/test_sales_workflow_debounce.py`: orden llm → tool → llm, guardas en orden e intento reiniciado, con activities falsas.
  - `tests/test_run_agent_turn.py`: defaults; remarketing intacto.
- Replay: `tests/test_replay_sales.py` con historias reales. Es un cambio de payload y de la forma del resultado, sin patch (L-22). Hay que **aceptar las dos formas** del resultado (`None`/`int` en historias viejas).
- Lista cuando: una conversación real re-jugada produce los pasos en orden, y el scorecard y los golden no cambian.

**PR 3 · Ids de la ráfaga en la señal**
- Archivos:
  - `sales_session.py`: `send_message(message, media, plugin_context, inbound_meta=None)`.
  - `load_or_start_sales_session.py` (≈ 343–362): pasa `{wamid, ts_ms, kind}`.
  - `PendingMessage` e `InboxMsg` se llenan con esos datos, incluida la recomposición por reinicio.
  - La traza trae `inbound[]`.
- Tests: señal con 3 y con 4 argumentos; replay.
- **Despliegue: primero el worker, después la API.** Un worker viejo que recibe 4 argumentos falla la tarea del workflow.
- Lista cuando: la traza lista los mensajes de cada ráfaga.

### Fase 1 — Puerto de percepción (los clientes no ven nada)

**PR 4 · Puerto y adaptadores**
- Archivos:
  - `src/platform/perception/`: `ports.py`, `questions.py` (rafaga-v1), `profiles.yaml`, `composition.py`, `anonymize.py`, `adapters/{fake,null,litellm,openrouter_decisions}.py`;
  - `src/sdk/connectorkit/__init__.py` y `ports.py` (export perezoso);
  - `docs/_sdk/07-connectorkit.md`.
- Tests: un **test de contrato común** que corre contra los cuatro adaptadores, con httpx y LiteLLM simulados con respuestas grabadas (incluidas las de la API de decisiones de OpenRouter, que está en alpha); timeout y fail-open; anonimización; `test_sdk_lazy_surface.py`; P-31.
- Lista cuando: el contrato pasa con los cuatro adaptadores.

**PR 5 · Terraform: parámetros, secreto y alias**
- Archivos: `infra/terraform/platform/variables.tf` (secreto `OPENROUTER_API_KEY` y config autoritativa), `render-env-from-ssm.sh`, `exoclaw-temporal/litellm_config.yaml` (alias `openrouter-perception`) y el guard de ids estables (`openai/gpt-4o-mini-2024-07-18` y `typesafe/jev-1.13`).
- Prueba real, una sola vez y grabada: una llamada a cada clasificador por OpenRouter que confirme los logprobs con `require_parameters`, si `zdr` encuentra endpoint y la forma de la respuesta de la API de decisiones. La grabación alimenta el test de contrato del PR 4.
- Lista cuando: `terraform plan` solo agrega; el parámetro existe con `off`.

### Fase 2 — Caja, banco y sección Laboratorio (los clientes no ven nada)

**PR 6 · Terraform: caja del laboratorio, S3 e IAM**
- Archivos:
  - `infra/terraform/compute/modules/lab-instance/`, a partir de `graphagents-instance`: t3.large, autostop a los 10 min, agente SSM, rol con S3 limitado a su prefijo, SSM solo `/hubara/lab/*` más las llaves de LLM (incluida la llave de OpenRouter del laboratorio);
  - user-data que borra `/lab/temporal/` y levanta el compose del laboratorio (§3.3);
  - bucket privado con bloqueo de acceso público, cifrado y ciclo de vida (`bench` 30 días, `runs` 180 días);
  - rol de la app de producción: `s3:PutObject` en `bench/*`, `s3:GetObject` en `runs/*`, y `ec2:StartInstances` y `ssm:SendCommand` solo sobre la caja con tag `Role=lab` (el `SendCommand`, además, solo con el documento `AWS-RunShellScript`). `ssm:GetCommandInvocation`, `ssm:DescribeInstanceInformation` y `ec2:DescribeInstances` no se pueden limitar por recurso y van sobre `*`. Ojo: la política de GraphAgents (`wake_graphagents` en `app-instance/main.tf`) da hoy `ssm:SendCommand` sobre `*`; no se copia tal cual.
- Lista cuando: el plan **no muestra cambios ni reemplazos en la caja de producción** (checklist de la memoria "terraform apply destroyed vault") y el módulo se crea con `count`.

**PR 7 · Lanzador de corridas: exportador, launcher y API del botón**
- Archivos:
  - `LabLaunchWorkflow` en el worker `sales_eval`, **sin horario**: id fijo `lab-launch` con política de conflicto `FAIL` y una consulta de estado;
  - activities `export_bench_snapshot` (archivo por archivo), `start_lab_box`, `dispatch_lab_run` (idempotente por id de corrida), `poll_lab_run` (con heartbeat; lee `runs/<corrida>/progress.json`) y `cancel_lab_run`;
  - `src/plugins/chats/api/lab.py`: `GET /estimate`, `POST /runs`, `GET /runs/active` y `POST /runs/active/cancel` (§4.3);
  - `LabStorePort` en platform, con adaptadores S3 y filesystem; launcher con el patrón de `graphagents/launcher.py`.
- El banco incluye: sesiones desde el 2026-09-10, `agent_state`, trazas, metadata, scorecards, catálogo, promociones y order facts. Las exclusiones van con motivo: turnos humanos, turnos del sistema, `wa_golden_*`, números internos, pedidos de prueba (`hubara_test_order`) y cuarentena.
- Tests:
  - funciones puras del exportador sobre un vault de fixture (`tmp_path`, número `wa_573001234567`), con un S3 falso que comprueba que sube archivo por archivo;
  - workflow con `start_time_skipping` y launcher falso: un segundo lanzamiento responde 409; cancelar llama a `cancel_lab_run`; una caja que no prende deja la corrida como fallida con su motivo; una orden repetida no duplica la corrida;
  - la API rechaza con 422 una corrida que no cabe en el tope.
- Lista cuando: `POST /api/chats/lab/runs` deja el banco en S3 con su manifiesto y prende la caja. La orden a la caja se completa con el PR 8 y el botón llega con el PR 9.

**PR 8 · Worker `sales_lab` y contrato `lab@v1`**
- Archivos:
  - `src/plugins/chats/workers/sales_lab.py`: guard de arranque (§3.5). **No se renderiza en el compose de producción**: tiene su propio compose en la caja, con tres servicios: `temporal` (imagen oficial fijada por digest, §3.3), `litellm` y `sales_lab`.
  - `src/plugins/chats/agent/sales_lab/`: cargador del banco y armado de casos. Cada caso lleva el prefijo del historial del LLM truncado antes del mensaje de usuario del turno, el historial del dashboard truncado, la metadata del momento (a partir de `stage_in`, `draft` y `state` de la traza, más los episodios hasta ese instante) y la ráfaga.
  - `LabRunWorkflow`, sobre el Temporal de la caja (§3.3).
  - `src/plugins/chats/api/lab.py`: los endpoints de lectura del §4.3 (los del lanzador llegan en el PR 7).
- Tests: golden del armado de casos, contrato de la API (`TestClient`) y guard de arranque, que rechaza `TEMPORAL_API_KEY`, `TEMPORAL_ADDRESS`, certificados o un `TEMPORAL_URL` que no sea el de la caja.
- Lista cuando: la API sirve el banco y el hilo real (A0) de cada conversación, y una orden del lanzador arma los casos en la caja y los publica en `runs/`.

**PR 9 · Plugin `lab` (sección Laboratorio)**
- Creación: `cd hubara_agency && uv run python -m src.sdk.cli create plugin lab --archetype full_stack`.
- Backend: forward por cast.
- Frontend:
  - `sections: [{key: lab, label: Laboratorio}]` y `sidebar: [/lab]`;
  - pestañas Conversaciones (lista, hilo de solo lectura con las clases CSS de Chats, panel de resumen y subpestaña Evaluaciones) y Banco y corridas;
  - botón **Nueva corrida** con el formulario (bots, repeticiones, banco, costo estimado y topes), el avance en vivo y **Cancelar** (§3.7);
  - en `@/shared/ui`: `Modal`, `SequenceTrace` y `TraceStepDetail`; en `@/shared/lib`: `sequence-layout.ts`, con tests.
- DoD de plugin nuevo: TCK (P-27), archetype (P-29), aristas en `build_system_graph()` y memoria "New plugin DoD".
- Lista cuando: lanzas una corrida con el botón y ves su avance; ves el banco y el hilo de cada turno real del bot actual con el modal; `npm run test:arch` y vitest verdes (CI no corre vitest, así que se reporta la corrida local); sin `lab` en `ENABLED_PLUGINS` la sección desaparece sin errores.

**PR 10 · Las gráficas de Calidad LLM pasan a `@/shared/ui`**
- `FailurePareto`, `StageFunnel`, `CheckTrend`, `VerdictTiles` (se extrae de `SummaryView`), `TrajectoryStrip` con su layout y la parte de presentación de `ComplianceMatrix`. Los tipos de vista genéricos van a `@/shared/lib`.
- Lista cuando: Calidad LLM se ve igual (tests existentes y verificación visual en :5174).

### Fase 3 — Simulador y evaluación por turno (los clientes no ven nada)

**PR 11 · Sandbox y test de fugas**
- Archivos: `src/plugins/chats/agent/sales_lab/sandbox/`:
  - entrypoint en subproceso con el entorno fijado antes de importar;
  - registro de adaptadores del §3.6 y reloj inyectado;
  - `SimSalesTurnWorkflow`;
  - turno de humo del banco antes de cada corrida (§3.3).
- Test de fugas: sockets con lista permitida, sistema de archivos sin cambios fuera del sandbox y entorno sin llaves.
- Lista cuando: un caso real del banco corre de punta a punta y el test de fugas pasa en CI.

**PR 12 · Scorecard en modo turno**
- Archivos: `scorecard/engine.py`, `checks/_helpers.py`, `trajectory.py` y `judge_checks.py` (juez por lote, por episodio y brazo).
- Lista cuando: el test de validación contra los 91 episodios pasa (§5.3).

**PR 13 · Brazos A0 y A1, y pestaña Resumen**
- A0 se re-mide y A1 se simula, con 3 repeticiones. Reporte de fidelidad. Pestaña Resumen con las gráficas compartidas por bot, diferencias con intervalo y lista de turnos que cambiaron.
- Lista cuando: A1 coincide con A0 en al menos el 90 % de los checks de código.

### Fase 4 — Bot nuevo en el laboratorio (los clientes no ven nada)

**PR 14 · Las capas ①②③ detrás del modo**
- Archivos:
  - `sales_session.py`: lectura del modo (se extiende la lectura por vuelta), ① `perceive_burst` más el plan (módulo puro `src/plugins/chats/agent/sales/perception_plan.py`) más `prefetch_turn_facts`, ③ `verify_coverage`, y el complemento como turno de sistema (bandera `is_complement_trigger`, como `is_ghost_trigger`).
  - `workflow_helpers.py`: `turn_policy`.
  - Todo va detrás de `workflow.patched("perception-v1")`. Cada activity nueva se registra en `workers/sales.py`, con su fake en el arnés de debounce y su fixture de replay (L-3, L-9, L-21).
- Tests: plan puro; workflow con percepción falsa que cubre, que no cubre, que hace timeout y que da error; con modo `off`, el replay es **idéntico**.
- Lista cuando: con el modo apagado no cambia ningún comando, y en el laboratorio las capas corren sobre el banco.

**PR 15 · Brazos B y C, y arena**
- Perfiles `jev-v1` (B) y `openai-lp-v1` (C), los dos por OpenRouter. Calibración por pregunta y métricas de la arena en el resumen de la corrida.
- Lista cuando: los dos bots nuevos corren sobre el banco completo. **Aquí decides con las gráficas.**

### Fase 5 — Sombra en producción (no cambia respuestas)

**PR 16 · Modo por turno y control en el dashboard**
- Política de preparación pura, con el patrón de `src/plugins/mba/domain/rollout_policy.py` (`RolloutFacts`, `readiness` y `can_enable`); en esa política apagar nunca se bloquea.
- Control en la sección Agents (`agents_admin`, donde vive la configuración del agente), contra un endpoint de `chats` por cast.
- En sombra, la percepción corre en paralelo al LLM y la verificación después de enviar, así que no suma espera.

**PR 17 · Chats: botón por turno y modal**
- Archivos:
  - backend de `chats`: `GET /api/chats/sessions/{sid}/turns` y `/turns/{turn_key}/trace`, y `seq` en los mensajes de `/api/dashboard/sessions/{sid}`;
  - frontend: Zod en el mismo cambio (L-10) y un botón visible siempre en `ChatsMessageList`, que funcione también en la app Android.
- Lista cuando: 7 días o más de sombra dentro de los umbrales (§8.3).

### Fase 6 — Canary y encendido (sí, gradual y con apagado)

**PR 18 · Calidad LLM: filtro "bot actual / bot nuevo"** dentro de Producción, a partir del `mode` de la traza. Operación: lista de números de prueba, 10 %, 50 % y 100 %.

**PR 19 · Limpieza**
- A las 2 semanas al 100 %: `workflow.deprecate_patch(...)` de los patches viejos, después del drenaje.
- **El modo apagado se queda** como interruptor de emergencia: es el mismo código de hoy y no cuesta mantenerlo.

---

## 7. Runbooks

- **Cambios de workflow:** replay con historias reales en CI; desplegar primero el worker y después la API cuando cambia una señal; desplegar en horas de poco tráfico. El idle de 1 minuto en ventas drena lo que estaba en vuelo.
- **Terraform:** `plan` revisado a mano; cero cambios en `app-instance`; nunca `apply` con reemplazos; el módulo nuevo va con `count`.
- **Lanzar una corrida:** botón **Nueva corrida** en Laboratorio → elegir bots, repeticiones y banco → confirmar el costo estimado → seguir el avance en la misma sección. **Cancelar** la detiene y la caja se apaga sola a los 10 minutos sin trabajo. Solo hay una corrida a la vez. Antes del PR 9, que trae el botón, la misma corrida se lanza con `POST /api/chats/lab/runs` y la sesión del dashboard.
- **Ver una corrida en la UI de Temporal:** con la caja prendida, `aws ssm start-session --target <id-de-la-caja> --document-name AWS-StartPortForwardingSession --parameters portNumber=8233,localPortNumber=18233` y abrir http://localhost:18233. El puerto 18233 evita chocar con la UI de tu stack local. La base se borra en el siguiente arranque de la caja.
- **Apagar el bot nuevo:** botón en Agents; actúa en el siguiente mensaje de cada conversación. Alternativa: bajar el techo en Terraform y redesplegar.
- **Qué mirar en sombra:** caídas a "turno como hoy", p95 de la percepción, errores y cobertura del bot actual.

---

## 8. Riesgos, costos y vara de promoción

### 8.1 Riesgos

| Riesgo | Mitigación |
|---|---|
| El simulador tumba al bot real (poca RAM) | Caja aparte |
| El laboratorio escribe a un cliente o crea un pedido | Sin llaves, puertos falsos, Temporal propio de la caja, test de fugas |
| El Temporal de la caja se comporta distinto a Temporal Cloud | Versión fija; el turno solo usa funciones básicas (§3.3); la prueba real es el canary en producción |
| Workflows vivos dejan de re-jugarse | Solo payload sin patch; lo nuevo con `patched`; replay; orden de despliegue |
| `terraform apply` destructivo | `count`, plan revisado, nada de reemplazos |
| Jev lento, caído o que cambie de versión | Fail-open a 3 s, id fijo `typesafe/jev-1.13` (nunca `Jev Latest`), sombra primero |
| La API de decisiones de OpenRouter está en alpha | Test de contrato con respuestas grabadas, validación de cada respuesta y fail-open; plan B: la API directa de TypeSafe |
| OpenRouter caído: se caen Jev y OpenAI a la vez | Fail-open (el turno sale como hoy); la sombra mide las caídas |
| Jev rinde menos en español | Preguntas en inglés y en español en la arena; el rival como alternativa |
| Datos personales a proveedores nuevos (OpenRouter y TypeSafe) | Anonimización antes de enviar; `data_collection: deny` y retención cero si hay endpoint; políticas revisadas por el operador |
| El clasificador se califica a sí mismo | El juez saca los asuntos por su cuenta |
| Banco chico | Tres repeticiones, intervalos y "aún no concluyente"; cada corrida exporta el banco al día |
| Cambio de evaluador o de juez | Versiones en cada corrida; no se mezclan |
| Confundir simulación con producción | Sección aparte y sin compositor; su única acción es lanzar corridas del laboratorio |
| Costos | Topes por corrida y por mes; el botón muestra el costo estimado y no deja lanzar lo que no cabe |
| Doble clic o dos personas lanzan dos corridas | Id fijo del workflow con política `FAIL`: una corrida a la vez (§3.7) |
| Exportar en horas de clientes le quita memoria al bot | El banco pesa unos 15–20 MB y se sube archivo por archivo (§3.7) |

### 8.2 Costos

| Concepto | Costo |
|---|---|
| Caja del laboratorio (t3.large, ~2 h por corrida completa) | ≈ US$0,17 por corrida, más el disco (≈ US$2,4/mes) |
| Agente simulado, un brazo sobre el banco actual | ≈ US$7 (medido: producción gastó US$7,09 en esos 91 episodios) |
| Corrida de decisión (A1, B y C × 3, más el juez) | ≈ US$95 (≈ US$63 de agente + ≈ US$30 de juez, estimado) |
| Corrida rápida (A1 y un bot nuevo, 1 repetición, más el juez) | ≈ US$20 |
| Jev en producción | ≈ US$0,0002 por turno |
| OpenAI en producción, si gana | ≈ US$0,0006 por turno con `gpt-4o-mini` por OpenRouter |

### 8.3 Vara de promoción (propuesta)

- **Laboratorio:** cobertura de asuntos (EST-08 v2) mejor que A1, con un intervalo que no cruza cero; ningún check crítico o mayor peor; fidelidad del simulador de al menos el 90 %.
- **Sombra, 7 días o más:** menos del 1 % de caídas, p95 de percepción por debajo de 1,5 s y cero turnos afectados. En sombra la percepción corre en paralelo, así que no suma espera; el p95 importa para el canary.
- **Canary:** en episodios reales, el bot nuevo iguala o supera al actual con el scorecard de producción; no aumentan los FALLA; el apagado ya se probó en vivo.

---

## 9. Lo que necesita el operador

1. Crear la cuenta de OpenRouter y dos llaves con límite de crédito: una para producción y otra para la caja del laboratorio. Cargarlas en `OPENROUTER_API_KEY` (PR 5). Yo no creo cuentas.
2. Revisar la configuración de privacidad de OpenRouter (retención cero si está disponible) y la política de privacidad de TypeSafe, que procesa las preguntas de Jev. Ya no hace falta el acceso anticipado: Jev está en OpenRouter desde el 2026-09-18.
3. Confirmar el modelo de OpenAI (§1.3): `gpt-4o-mini-2024-07-18` con logprobs (recomendado) o `gpt-5.4-nano` sin logprobs.
4. Opcional: revisar 20 casos para calibrar EST-08 v2 (unos 20 minutos).
5. Confirmar o ajustar los topes del laboratorio (propuestos: US$120 por corrida y US$300 al mes).
6. Decidir si el botón **Nueva corrida** debe ser solo tuyo. Hoy cualquiera con sesión en el dashboard puede pulsarlo; los roles serían un PR aparte.

## 10. Hallazgos laterales (tareas aparte)

- El dashboard registra como enviado un texto que la guarda del selector de variantes no envió (`sales_session.py` ≈ 1062–1107).
- `is_handoff` se pierde en `coalesce_inbox`. Por eso la guarda del saludo (≈ 844) no lo ve y la traza nunca dice `handoff` (≈ 1182).
- Hay coincidencias con teléfonos de clientes en comentarios del código (34 archivos).
- El frontend no conoce la fidelidad `partial` (se arregla en PR 10 o PR 17).
- El registro de envíos del episodio se salta la despedida cuando el episodio ya cerró (se arregla en PR 2).

## 11. Diseño visual de referencia (revisar antes de implementar)

> **Nota para quien implemente, incluido yo en otra sesión:** el plan visual es parte del diseño, no una ilustración. El operador lo aprobó el 2026-09-23 ("espectacular el diagrama de secuencia en el modal"). Antes de escribir cualquier pantalla (el modal del hilo en los PR 9 y 17, la sección Laboratorio y su botón en el PR 9, el control del modo en el PR 16) y antes de fijar la forma de la traza v2 (PR 2), ábrelo y sigue sus decisiones. Si algo no se puede hacer igual, se anota aquí con el motivo.

**Dónde está:**
- Plan visual: https://claude.ai/artifact/4UNdDwmnQiFFEnPL9r9APe (versión 10 o posterior; privado, del operador).
- Copia en el repo: `LABORATORIO_CONVERSACIONES_DISENO.html`, junto a este archivo. Se abre directo en el navegador y es la fuente si el artifact cambia o no se puede abrir.
- Qué mirar: la sección 08 (el chat de ejemplo y el modal, clicables), la 09 (la sección Laboratorio con el botón **Nueva corrida**) y las figuras de las secciones 03 y 04. El código del modal está al final del archivo: los datos de ejemplo (`ARMS`) y las funciones que dibujan (`seqSvg`, `listHtml`, `detailHtml` y `renderModal`).

### 11.1 El modal del hilo

- **Cómo se abre desde el chat:**
  - clic en el grupo de la ráfaga (una barra violeta a la izquierda y la etiqueta "Ráfaga · 2 mensajes · 7 s"): abre el modal en el paso 1;
  - clic en el chip "Ver hilo del turno", debajo de la respuesta (un punto del color del resultado y "13 pasos · 2 de 2 asuntos"): abre el modal en el último paso.
- **Cabecera:**
  - título "Hilo del turno N" y una línea con fecha, hora, tamaño de la ráfaga, duración, costo y origen (producción, o laboratorio con su brazo);
  - selector de bot (bot actual o bot nuevo), para ver el mismo turno con los dos;
  - dos filas de chips: **Asuntos** (asunto · mensaje N · estado) y **Checks del turno** (EST-08 v2, EST-06, ENV-02…).
- **Cuerpo en dos columnas** (1,25 fr y 1 fr): el diagrama de secuencia a la izquierda y el detalle del paso elegido a la derecha.
- **El diagrama de secuencia:**
  - cinco carriles fijos: Cliente, Workflow, el clasificador del brazo (Jev u OpenAI), LLM y Tools, cada uno con su etiqueta en forma de píldora y una línea de vida punteada;
  - si el turno no usó clasificador, su carril se atenúa y dice "sin uso";
  - cada paso es una flecha horizontal entre dos carriles, con su número en un círculo, una etiqueta corta sobre un fondo que tapa la línea y, a la derecha, el tiempo desde el inicio del turno ("+2,5 s");
  - las respuestas que vuelven hacia la izquierda van punteadas, salvo la que llega al Cliente;
  - lo que pasa dentro de un mismo carril (una guarda, el plan en código) es una caja sobre la línea de vida;
  - color por estado: info en azul, clasificador en violeta, tools en cian, ok en verde, advertencia en ámbar, falla en rojo y neutro en gris;
  - el paso elegido lleva borde del color de acento, trazo más grueso y etiqueta en negrita;
  - geometría de la maqueta: carriles en x = 58, 173, 288, 403 y 518 sobre un ancho de 610; el primer paso en y = 70 y 44 px entre pasos; ancho mínimo de 560 px, con scroll horizontal si no cabe.
- **Detalle del paso:** el tipo (en el color del paso), "N. título", "Carril → Carril · +t · duró d", y secciones de cuatro clases: caja de texto o de código, texto, pares clave-valor y **barras de probabilidad**, con la marca del umbral, el valor y ✓ en lo elegido (lo elegido va en violeta).
- **Pie:** el resultado del turno en una línea, con su chip (✓ o ⚠).
- **Teclado y accesibilidad:** `role="dialog"` con `aria-modal`; cada paso es un botón con foco; Enter o espacio lo eligen; las flechas arriba y abajo recorren los pasos; Escape cierra, y el foco vuelve a lo que abrió el modal.
- **En el celular** (menos de 760 px): el modal ocupa toda la pantalla; el diagrama se cambia por una lista de pasos (número con su color, título, "Carril → Carril" y tiempo) y el detalle queda debajo. También tiene que funcionar en la app Android (PR 17).

### 11.2 De la traza v2 al dibujo

Cada paso de la maqueta es lo que la traza v2 (§4.1) tiene que poder dar. Por eso el PR 2 fija la forma de la traza pensando en este dibujo:

| En la maqueta | Sale de la traza v2 |
|---|---|
| Carriles de origen y destino | El `kind` del paso: la ráfaga va de Cliente a Workflow; `perception` y `verify`, entre Workflow y el clasificador; `plan`, `guard` y `restart`, dentro de Workflow; `llm`, entre Workflow y LLM; `tool`, entre Workflow y Tools; `outbound`, de Workflow a Cliente. |
| Color | El tipo y el resultado del paso (ok, advertencia o falla). |
| Título y etiqueta corta | Se arman al dibujar; no se guardan en la traza. |
| Tiempo y duración | `at_ms` y `dur_ms`, relativos al inicio del turno (`workflow.now()`). |
| Secciones del detalle | Los datos del paso: mensajes de la ráfaga con su hora; modelo, tokens y costo; argumentos y extracto de la tool; probabilidades con su umbral; texto antes y después de una guarda; wamid entregados. |

- Las tools las ejecuta el workflow (`execute_tool` de exoclaw) después de que el LLM las pide. Por eso el dibujo va LLM → Workflow ("pide la tool"), Workflow → Tools y Tools → Workflow. Así quedó también la maqueta desde la versión 10.
- El dibujo sale de una función pura, `sequence-layout.ts`, que recibe `steps[]` y devuelve posiciones. Lleva tests.

### 11.3 La sección Laboratorio y el botón

- Tres pestañas: Conversaciones, Resumen, y Banco y corridas.
- **Conversaciones:** la lista del banco a la izquierda (200 px), con el veredicto de cada bot; el hilo en el centro, con selector de bot y las mismas burbujas de Chats; a la derecha (248 px), el resumen de evaluaciones del chat y la subpestaña Evaluaciones. Debajo de 900 px, la lista pasa arriba y se desliza de lado.
- **Botón Nueva corrida**, a la derecha de la barra superior: abre un panel encima de las pestañas con el formulario (bots, repeticiones, banco y costo) y, al lanzar, el avance (fases en píldoras, barra, turnos, gasto y Cancelar).
- Todo con los tokens del tema oscuro del dashboard (`--hb-*`, de `frontend_dashboard/src/index.css`). Nada de colores inventados.

### 11.4 Lo que no quedó igual al diseño (y por qué)

- **Tokens.** La maqueta usa `--hb-*`, pero en el dashboard los tokens se llaman `--color-*` (bloque `@theme` de `src/index.css`) y el gate R-TAILWIND prohíbe archivos `.css` propios. Se usaron los equivalentes: `--hb-win` → `--color-win-bg`; `--hb-panel` → `--color-inspector`; el resto con el mismo nombre.
- **El SVG del diagrama** es un `group` con filas-botón y no un `img`: un `img` esconde sus hijos a los lectores de pantalla.
- **Nueva corrida** arranca en 1 repetición (la maqueta mostraba 3) para que el primer clic sea el barato.
- **El avance** se consulta cada 60 s, no cada 10 s: la política de datos en tiempo real del dashboard solo permite un intervalo fijo de 60 s o más sin ADR. Lanzar y cancelar refrescan al instante.
- **Pestaña Resumen:** llega con los bots simulados (PR 13); en el PR 9 van Conversaciones y Banco y corridas.

## 12. Desvíos durante la implementación

Anotados al implementar (2026-09-23/24). El código vivo manda; esta lista explica por qué difiere de lo escrito arriba.

- **PR 3:** el 4.º argumento de la señal (`inbound_meta`) va detrás de la bandera `SALES_SIGNAL_INBOUND_META` (Terraform, `off`). Así el orden de despliegue (worker antes que la API) no depende de la memoria de nadie: se prende cuando el worker nuevo ya está.
- **PR 4:** el set de preguntas `rafaga-v1` vive en `chats` (PR 14), no en `src/platform/perception/`: las preguntas son del dominio de ventas.
- **PR 5 y 6:** los secretos del laboratorio van en `/hubara-lab/*` (no `/hubara/lab/*`), para que la política IAM de la caja no pueda tocar nada de `/hubara/`. El bucket tiene un prefijo más, `orders/`, donde el lanzador deja la orden de cada corrida.
- **PR 7:** `LabLaunchInput` lleva `spend_limit_usd` (el tope real de gasto de la corrida: el menor entre el tope por corrida y lo que queda del mes), separado del estimado.
- **PR 8:** el `turn_key` viaja como parámetro de consulta (lleva `/`, que no viaja en un segmento de ruta). El worker `sales_lab` no está en el manifiesto: solo lo arranca `dispatch.sh` en la caja.
- **PR 9:** ver §11.4. Además, el build del dashboard trae todas las secciones, pero el API de producción solo sirve los plugins de su `ENABLED_PLUGINS` (Terraform): sin `lab` ahí, la sección explica que el laboratorio no está habilitado en vez de mostrar un 404.
- **PR 10:** al volver a pedir la matriz tras una respuesta vacía, el tope de filas se reinicia.
- **PR 11:** los puertos en modo sandbox viven en `src/platform/lab/sandbox_ports.py` y se exponen por `labkit` (P-28/P-31 prohíben `src.platform` desde el plugin). El workflow se arranca por nombre (R-DIP #10). `cases.py` traía el borrador y el estado de DESPUÉS del turno: se agregaron `draft_before` y `state_before`.
- **PR 12:** registro 3 → 4 (campo `focus`). Seis checks dependen de turnos futuros (CIE-03, CIE-03b, CIE-04, CIE-08, GHO-02, TAG-07) y en modo turno dan `sin_senal`. La réplica exacta se probó con 207 trayectorias capturadas, no con los 91 episodios reales: esa validación la hace la primera corrida real (ver PR 13).
- **PR 13:** en dos partes. #356 corre A1; la segunda (evaluación y Resumen) califica A0, A1, B y C en modo turno dentro de la caja, publica `summary.json` (gráficas, `diffs` con bootstrap por conversación, `fidelity`, `arena`, `validation`) y agrega `GET …/report`. La **validación contra los 91 episodios** (§5.3) no se corrió desde la máquina de desarrollo, porque bajar el vault con datos de clientes quedó bloqueado. Se hace sola en la primera corrida real: `validation` compara, dentro de la caja, el scorecard de producción (copia intacta en `production/scores/`) con A0 re-medido, y publica solo conteos por check. Si el gasto llega al tope, la corrida se califica sin juez y lo anota. El gasto del juez no se mide en vivo: lo cubre el estimado del lanzador.
- **PR 14:** el modo no amplía `read_idle_timeout_seconds` (cambiar el tipo de retorno rompe el replay): viaja en el 4.º argumento de la señal. `prefetch_turn_facts` no se hizo, por la regla del operador de que las tarifas solo van por `send_shipping_rates`.
- **PR 15:** la verificación deja en la traza `cost_usd` y `complement_scheduled` (solo payload, sin patch), para que el laboratorio espere el complemento sin adivinar con tiempos. El acuerdo con el juez lleva sus asuntos (texto libre) a los 17 códigos por palabras clave, así que es aproximado y sirve para comparar B contra C. No hay preguntas en inglés en la arena.
- **PR 16:** el control vive en el inspector de ventas de Agents (panel "Bot nuevo"), compuesto desde la página, porque una feature no importa otra. La vara está en el servidor (422) y el panel solo la muestra.
- **PR 19:** no se implementa todavía: toca a las 2 semanas al 100 % y después del drenaje (`deprecate_patch`).

## 13. Referencias

- Plan visual: https://claude.ai/artifact/4UNdDwmnQiFFEnPL9r9APe. Copia en el repo: `LABORATORIO_CONVERSACIONES_DISENO.html` (§11).
- OpenRouter: modelo `typesafe/jev-1.13` (https://openrouter.ai/typesafe/jev-1.13), API de decisiones y preferencias de proveedor (https://openrouter.ai/docs/guides/routing/provider-selection).
- Contrato de plugins: `PLUGIN_CONTRACT.md` §5.2 (evals). Arquitectura viva: `ARCHITECTURE_FINAL_fable.md` (lecciones L-1, L-3, L-9, L-10, L-21, L-22 y L-23).
- Refinamiento previo de ráfagas: `hubara_agency/.hubara/refinements/HU-burst-inbox-watermark/refinement.md`.
- Patrones: `src/platform/vision/composition.py` (puerto con proveedor por entorno), `src/platform/graphagents/launcher.py` (caja bajo demanda), `src/plugins/mba/domain/rollout_policy.py` (preparación y apagado), `src/plugins/agents_admin/api/evals.py` (cast), `src/platform/temporal/client.py` (Temporal Cloud con llave o `TEMPORAL_URL` sin llave).
