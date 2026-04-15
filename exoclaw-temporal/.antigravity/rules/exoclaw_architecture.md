# Exoclaw-Temporal Agent Rules & Architecture Guide

## 1. Arquitectura del Framework (Core Architecture)

`exoclaw-temporal` unifica la arquitectura de agentes `exoclaw` con el modelo de ejecución durable y robusto de Temporal. 
La arquitectura central depende de **6 protocolos principales** para el ciclo de vida y la entrada/salida de datos del agente. Como se define en el modelo fundacional de exoclaw, cada concepto clave es un protocolo:

1. **InboundMessage**: Encapsula el mensaje entrante del usuario o del sistema.
2. **Bus**: Sistema de mensajería asíncrona / transporte entre capas.
3. **LLM**: El proveedor del modelo de lenguaje que ejecuta el análisis.
4. **Tools**: Definiciones de registro de herramientas.
5. **OutboundMessage**: El sistema para preparar la entrega estructurada de la respuesta generada.
6. **Executor**: El 6º protocolo (y más importante aquí) que dicta **cómo** el AgentLoop realiza las operaciones de Entrada y Salida (I/O). Su contrato formal define 6 funciones estratégicas que puedes aislar o reemplazar:
   - `build_prompt`: Lee el estado persistente y construye el bloque de mensajería (System prompt + historial JSONL + input del usuario).
   - `chat`: Orquesta la llamada de red asíncrona hacia el proveedor LLM y captura si el bot requiere usar tools.
   - `execute_tool`: Evalúa las protecciones y ejecuta el código subyacente de la herramienta contra el entorno (ej: shell, web, filesystem).
   - `record`: Captura el final del turno y guarda los mensajes de delta en el storage persistente (usualmente `[session].jsonl`).
   - `clear`: Purga físicamente la sesión y el historial en curso (borra archivos o vacía referencias). Permite el reseteo manual del bot a estado cero por el usuario.
   - `run_hook`: Es un mecanismo puente que permite inyectar y despachar *callbacks* o funciones asíncronas de middleware en el flujo del Agente (ej. para inyectar un log al frontend o un middleware de auditoría) sin alterar el código core puro y determinista.

**Adaptación a Temporal (Workflows y Activities)**:
El framework implementa el `Executor` mediante Actividades de Temporal, separando el "AgentLoop" en un **Workflow determinista** y sus operaciones en **Activities reintentables**:
- `build_prompt` (Actividad: Carga el estado local y mensajes JSONL).
- `llm_chat` (Actividad: Ejecuta la llamada no determinista a la LLM).
- `execute_tool` (Actividad: Aplica las reglas y ejecuta las funciones asíncronas de las herramientas).
- `record_turn` (Actividad: Persiste los registros en JSONL).

Si un proceso falla en medio de un comando (ej shell de 10 min), al relanzarse simplemente retoma desde el log de Workflow History.

## 2. Modelos de Ejecución: `turn_based` vs `session_based`

Existen dos enfoques principales en el framework para levantar workflows, y de ti depende escoger cuál modificar según la naturaleza del flujo de la aplicación:

### `turn_based/` (Un Workflow por Turno de Mensaje)
- **Comportamiento**: Cada mensaje o interacción del usuario desencadena el lanzamiento de un **nuevo** `AgentTurnWorkflow`. El historial de conversación se carga desde disco al inicio de cada turno y se guarda al final.
- **Ventajas**: Modelo mental simple. Completamente stateless. Cualquier worker puede ejecutar cualquier turno (si comparten el volumen).
- **Cuándo usarlo**: Es el modelo principal para la mayoría de los casos de uso en producción, donde las respuestas secuenciales estructuradas (sin interrupciones) son el estándar.

### `session_based/` (Un Workflow por Sesión de Conversación Larga)
- **Comportamiento**: Un solo `AgentSessionWorkflow` se mantiene vivo durante lo que dura toooodo el ciclo de vida de la sesión conversacional. 
  - **Interacción Externa (Signals)**: Los nuevos mensajes interactúan interactúan con la sesión inyectándose en vivo mediante `@workflow.signal` (`send_message`). Nunca se abren sockets o endpoints HTTP dentro del Workflow. Todo input del mundo exterior (Slack, Webhooks) entra a través de una llamada al Cliente de Temporal para enviar el signal al workflow en ejecución.
  - **Consultas de Estado (Queries)**: El mundo exterior puede verificar asíncronamente el estado del Agente u obtener respuestas pasadas llamando a métodos `@workflow.query` (`get_last_response`, `is_processing`). Esto está diseñado para ver en memoria viva la ejecución sin bloquearla ni romper el determinismo.
- **Ventajas**: Extraordinariamente dinámico. Permite recibir señales de interrupción a la mitad de los flujos o agregar comandos concurrentes por diferentes canales sin esperar que un turno finalice. Emplea inteligentemente `workflow.continue_as_new()` tras 50 turnos para reinicializar el historial del Workflow y prevenir problemas de memoria en el servidor Temporal.
- **Cuándo usarlo**: Imprescindible cuando una sesión específica requiere recibir inputs desde múltiples orígenes simultáneamente (ej., Un Bot de Slack + un Cron de Scheduled Heartbeat + CLI al mismo tiempo lanzando inputs al mismo Agente).

## 3. Patrones y Reglas Estrictas de Programación

Para preservar la robustez arquitectónica y cumplir las normas de Temporal, debes aplicar rigurosamente estas reglas:

1. **Serialización Mínima y Tipado (`config.py`)**:
   - Todo objeto o parámetro que deba atravesar la barrera entre Workflow y Activity **DEBE estar dictado por un `dataclass`**. 
   - **CERO OBJETOS VIVOS**: Nunca envíes conexiones abiertas, librerías instanciadas, registries o clientes HTTP a través de los constructores u opciones del Workflow. Sólo strings, primitivos y diccionarios JSON. Evita clases dinámicas complejas.
2. **I/O Handling y Side-Effects**:
   - Queda totalmente prohibido que los archivos Workflow (ej. `agent_turn.py`) realicen operaciones directamente de sistema.
   - Cualquier consulta, escritura SQL, impresión HTTP, guardado shell, archivo IO o mutación DEBE envolverse dentro de un archivo `@activity.defn` en el directorio de `activities/`.
3. **Restricciones de Determinismo en Workflows**:
   - En el interior de un método decorado con `@workflow.run` nunca invoques a `uuid.uuid4()`, `random`, u operaciones de tiempo real (`time.sleep()`). En su lugar, consume funcionalidades autorizadas por `workflow.unsafe.imports_passed_through()` o inyecta variables pre-computadas desde parámetros.
4. **Estado (State Management)**:
   - El estado de vida real reside en el historial del Workflow y en el almacenamiento de archivos JSONL para el caso de `session_based`. `exoclaw-temporal` no utiliza bases de datos ocultas de turno.

## 4. Protocolo: "Qué hacer cuando te sientas perdido"

Si intentas agregar una funcionalidad y no sabes cómo el framework opera con Temporal **NO ALUCINES DEPENDENCIAS.**

- **Flujo Principal y Ciclo de vida**: Entra en `exoclaw_temporal/turn_based/workflows/agent_turn.py` o `session_based/workflows/agent_session.py`.
- **Serialización Permitida**: Revisa y apóyate en `exoclaw_temporal/config.py`.
- **Llamadas Asíncronas e I/O**: Mira dentro de `exoclaw_temporal/activities/`.
- **Inicialización (Bootstrap)**: Mira `app.py`, `worker.py` o `__main__.py` de los respectivos subdirectorios (turn vs session) para ver el enrutador hacia el SDK Temporalio. 

## 5. Instrucciones Directas: Inicializar y Ejecutar el Worker Nativo

**Enfoque Turn-Based:**
```bash
uv run python -m exoclaw_temporal.turn_based --worker
```

**Enfoque Session-Based:**
```bash
uv run python -m exoclaw_temporal.session_based --worker
```

## 6. Manejo de Channels y Cron

`exoclaw-temporal` no depende de Daemons tradicionales ni servidores de websockets amarrados al agente para manejar interactividad asíncrona. Todo se encapsula rígidamente:

### Channels (Canales)
En frameworks clásicos, la conexión con un "Channel" (como Slack, Discord, CLI) se realiza escuchando eventos internamente. Aquí es **al revés**. 
- El Agente no administra activamente sockets hacia el exterior. 
- La identificación del origen de los mensajes llega explícitamente en variables del Input Dataclass: `channel` (ej. `"slack"`) y `chat_id`. 
- Cualquier adaptador externo (webhook handler en FastAPI, socket escuchando en JS) es el que manda `Input` al servidor de Temporal enviando esos identificadores, y lee mediante Queries la respuesta para despacharla a donde corresponda. Temporal unifica cualquier channel en un solo tipo de Worker stateless.

### Cron (Tareas Programadas)
Típicamente, requerirías un proceso extra como `celery beat` para ejecutar funciones periódicas en el agente. En `exoclaw-temporal`:
- **Cron es una Tool**: El framework registra `CronTool()` al armar el set de herramientas base.
- **Sin crontab externo**: No hace falta un servicio de sistema operativo para gatillar al bot. El LLM es capaz de razonar e inscribir trabajos de Cron manipulando un archivo de estado central (`cron.json` guardado en el Shared Workspace Volume).
- **Ejecución vía Temporal**: Cualquier trabajo registrado por el `CronTool` simplemente se despacha a lo largo del tiempo de vida del Agente utilizando la propia garantía del Temporal Cluster o el manejo de ciclos que se haya configurado por herramientas dependientes, sin romper el modelo *stateless* de los Workers.
