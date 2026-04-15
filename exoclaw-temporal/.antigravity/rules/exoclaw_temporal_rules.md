# Arquitectura y Reglas Estrictas de exoclaw-temporal

Esta guía es el manual definitivo para desarrollar sobre `exoclaw-temporal`. Todas las interacciones con código deben seguir estrictamente estas reglas. NO SE BASA EN SUPOSICIONES, es extraído directamente de la fuente y de la implementación de `Temporal.io` + `exoclaw`.

---

## 1. Arquitectura Exacta del Framework

`exoclaw-temporal` toma el bucle de razonamiento de un agente (OpenClaw/exoclaw) y lo vuelve inquebrantable integrándolo con Temporal.

### El "Sexto Protocolo" (El Executor)
El diseño original de `exoclaw` consta de 5 protocolos de flujo (`InboundMessage → Bus → AgentLoop → LLM → Tools → Bus → OutboundMessage → Channel`), más un **sexto protocolo (`Executor`)** que dicta *cómo* el bucle realiza el I/O.
En vez de ejecutar esto en un hilo local, `exoclaw-temporal` sustituye las llamadas del `Executor` por **Temporal Activities**.

### Topología del Workflow
El bucle en sí mismo vive puramente como una máquina de estados determinista (Temporal Workflow) que coordina los pasos de I/O (Temporal Activities):
1. **Activity: `build_prompt`** (Lee disco/API, carga historial y genera el prompt final).
2.  Bucle (hasta `max_iterations`):
    - **Activity: `llm_chat`** (Llamada al LLM con retry granular).
    - Ejecución paralela o iterativa de herramientas: **Activity: `execute_tool`** (Con `activity.heartbeat()` para sobrevivir tareas lentas).
3. **Activity: `record_turn`** (Guarda los nuevos mensajes asíncronamente en el historial volátil).

### Dos Enfoques Disponibles
- **Turn-based (`exoclaw_temporal.turn_based`)**: Un workflow por *cada mensaje*. Sencillo, el workflow vive lo que dura un turno.
- **Session-based (`exoclaw_temporal.session_based`)**: Un *único* workflow de larga duración para toda la sesión del usuario. Los mensajes entran como *Temporal Signals* (`send_message`). Se expone el estado vía *Temporal Queries* (`get_last_response`).

### Estado y Persistencia
- **No hay estado abstracto en memoria**: Cualquier worker puede morir en cualquier nanosegundo.
- El *estado en-vuelo* (los mensajes acumulados temporalmente en el turno actual o sesión) **vive exclusivamente en el historial de transacciones de Temporal**.
- El *estado duradero inter-turnos* (el JSONL de las sesiones de Agentic Memory) **vive en un Shared Workspace Volume** al que todos los workers deben poder hacer `mount`.

---

## 2. Mejores Prácticas y Patrones Estrictos (Reglas de Tipado e I/O)

Al modificar este framework, estúdiate a fuego lo siguiente:

### A. Tipado y JSON Serializable Boundaries (La Barrera de Temporal)
- **Regla Inquebrantable**: Todo argumento que cruza un `workflow.execute_activity` **DEBE** ser 100% serializable nativamente a JSON. 
- **NO PASES OBJETOS VIVOS**: No puedes pasar la instancia de `ToolRegistry`, el cliente HTTP (`httpx`), el `Client` de Temporal o el `LiteLLMProvider` entre workflow y actividades.
- **Usa el `exoclaw_temporal.config`**: Todas las transferencias de estado usan `dataclasses` planas (`TurnInput`, `WorkspaceConfig`, `LLMConfig`, `ExecuteToolInput`, `TurnOutput`, etc.).
- **Stringified JSON para Schemas**: Cuidado con listas de diccionarios profundamente anidados; el framework convierte el output de metadatos de las herramientas localmente en JSON String (`tool_definitions_json: str = "[]"`) para evitar que el converter crashee.

### B. Determinismo Estricto en Workflows
Dentro de los archivos de `workflows/` (como `AgentTurnWorkflow` o `AgentSessionWorkflow`):
- **PROHIBIDO**: Hacer llamadas a APIs, interacciones al filesystem, invocar `time.sleep()`, usar `random.randint()`, invocar `uuid.uuid4()`.
- Si necesitas latencia/espera, debes forzosamente usar `asyncio.sleep()` de la forma en la que Temporal lo intercepta (a través de utilidades de Temporal como `workflow.wait_condition` o los timeouts de history). Si necesitas I/O real, tienes que abstraerlo como una nueva `@activity.defn`.

### C. Actividades Lentas (Heartbeats)
- La actividad `execute_tool` maneja un subproceso (o task) interno únicamente para enviar `activity.heartbeat()` cada 10 segundos. Si alguna vez haces una actividad nueva de larga duración, **DEBES** implementar este bucle de cancelación y heartbeat usando `asyncio.sleep` y `activity.heartbeat()`. De lo contrario, Temporal colgará timeouts equivocados.

### D. Session Limits y Continue-as-New
- En el enfoque **Session-Based**, el historial crecerá infinitamente con cada Signal.
- Si editas esta lógica, nunca borres el contador de turnos: El framework implementa una purga `_CONTINUE_AS_NEW_AFTER_TURNS = 50`. Esto invoca un `workflow.continue_as_new()` devolviendo un canvas limpio sin romper la lógica del cliente.

---

## 3. Protocolo: "Qué hacer cuando te sientas perdido"

Si estás depurando una falla, te estancas escribiendo código, o el LLM pide usar una librería extravagante, vas a detenerte y seguir estos pasos obligatorios antes de escribir nada:

1. **PROHIBIDO ALUCINAR FRAMEWORKS**: En todo `exoclaw-temporal`, el LLM provider está envuelto en `exoclaw_provider_litellm`. No invoques OpenAI nativamente, ni Langchain, ni LlamaIndex, ni agentes de terceros. El I/O es estricto.
2. **Revisa los Contratos (El Mapa Base)**: 
   - Dirígete de inmediato a `exoclaw_temporal/config.py`. Allí está la definición de toda la sangre que fluye por las venas de Temporal. Confirma si los datos que necesitas existen o si debes agregarlos acá primero.
3. **Falla en el Comportamiento de I/O o Red**:
   - Acude a `exoclaw_temporal/activities/`. Revisa la instanciación de `_build_registry` en `tools.py` o los constructores de llamadas en `llm.py` y `conversation.py`. El error probablemente esté porque olvidaste reconstruir el estado.
4. **Fallas de Concurrencia o Timers ("Non-deterministic workflow error")**:
   - Ve a los archivos bajo `workflows/`. Revisa el bloque `with workflow.unsafe.imports_passed_through():`. Si estás usando un módulo random o datetime externo ahí dentro, estás violando las reglas de Temporal.
5. **No hay "Estado Mágico"**:
   - Si perdiste info, recuerda el flujo: Los registros persistentes van al Shared Workspace PVC (el directorio físico del `path`). Si el archivo `.jsonl` no está allí, la culpa es de `record_turn_activity`. Trazalo hacia atrás.

---

## 4. Instrucciones: Inicialización del Worker Nativo

Para habilitar que `exoclaw-temporal` atienda órdenes y opere de manera nativa sin boilerplate extra (solo usando la librería central), ejecuta los comandos utilizando **`uv`**.

### Prerrequisitos Globales
Asegúrate de que Temporal Server esté ejecutándose de fondo, normalmente:
```bash
docker compose up -d
```
Asegúrate de configurar la(s) variable(s) del proveedor que tengas activo (por defecto espera leer el `.env` o la shell local).
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
# O la respectiva para Azure/OpenAI cargada en tus variables locales.
```

### Ejecutar el Turn-Based Worker
Levanta un Worker que escucha en el Queue `exoclaw-turn-based`. Aquí vive en loop esperando la llamada de cualquier Workflow de turnos.
```bash
uv run python -m exoclaw_temporal.turn_based --worker
```
*(Puedes especificar un host externo con `--temporal-url my-cluster.com:7233`)*

### Ejecutar el Session-Based Worker
Levanta un Worker que escucha en el Queue `exoclaw-session-based`. Mantiene vivas las subrutinas de comunicación continua.
```bash
uv run python -m exoclaw_temporal.session_based --worker
```

### Entrar en Modo Cliente Interactivo (CLI Test)
Si omites el flag `--worker`, el script actuará como cliente y enviará la solicitud al cluster de temporal para que los workers registrados arriba recojan y despachen.
```bash
# Para turn-based (requiere tener el worker turn_based corriendo en otra terminal):
uv run python -m exoclaw_temporal.turn_based
```
