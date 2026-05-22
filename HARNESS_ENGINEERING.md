# Harness Engineering — Guía de análisis y mejora

> **Propósito de este documento.** Esta es una guía operativa destinada a un agente LLM (Claude Code u otro). Su misión al leerla es: **(1) analizar el harness actual del proyecto, (2) detectar qué técnicas faltan o están mal implementadas, y (3) proponer e implementar mejoras concretas**. Es agnóstica de framework: las herramientas mencionadas (Playwright, Git, JSON, etc.) son ejemplos ilustrativos —el lector debe traducirlas al stack del usuario—.
>
> **Cómo usar este documento.**
> 1. Empieza por la sección "Protocolo de diagnóstico inicial".
> 2. Recorre cada técnica numerada y evalúa el harness actual contra ella.
> 3. Termina aplicando el "Playbook de mejora" priorizando los gaps de mayor impacto.

---

## 0. Conceptos fundamentales

### 0.1 Definición operativa de harness

Un **harness** es toda la capa de orquestación que envuelve a un modelo de lenguaje para convertirlo en un agente funcional. Formalmente:

```
Agente = Modelo + Harness
```

El harness incluye, sin limitarse a:
- **Prompts** (system, user, sub-agent).
- **Herramientas** (tools, MCP servers, hooks).
- **Políticas de contexto** (compactación, resets, retrieval).
- **Sandboxes y restricciones de seguridad** (allowlists, filesystem boundaries).
- **Loops de retroalimentación** (verificación, evaluación, retry).
- **Artefactos durables** (archivos en disco que persisten entre sesiones).
- **Orquestación multi-agente** (handoffs, contratos, roles).

Cualquier código, configuración o lógica de ejecución que **no sea el modelo mismo** forma parte del harness.

### 0.2 El problema que resuelve

Los modelos LLM tienen tres limitaciones estructurales:

1. **Memoria efímera**: cada ventana de contexto empieza en blanco.
2. **Auto-evaluación deficiente**: tienden a aprobar su propio trabajo aunque sea mediocre.
3. **Pérdida de coherencia**: la calidad degrada a medida que se llena el contexto, incluyendo el fenómeno de "context anxiety" (cerrar trabajo prematuramente al sentir que se acerca el límite).

El harness existe para compensar estas limitaciones mediante estructura externa.

### 0.3 Las dos capas del harness

El harness tiene dos capas distintas que se diagnostican y mejoran por separado. **Ambas importan; la capa A es lógicamente anterior a la capa B.**

**Capa A — El sustrato (estática/persistente).** Hace que el entorno sea *legible* para el agente y *extiende* sus capacidades. Existe independientemente de cualquier tarea concreta y se carga o está disponible en toda sesión. Incluye: archivos de contexto en capas (qué es el codebase, sus convenciones), hooks, skills/expertise on-demand, plugins, servidores MCP, integraciones de navegación a nivel de símbolo, y subagents. Es la respuesta a la pregunta *"¿qué necesita saber y poder hacer el agente para operar bien aquí, sin importar la tarea?"*

**Capa B — La orquestación (runtime/dinámica).** Gobierna *cómo se ejecuta una tarea larga*: el loop de sesiones, los roles (initializer/generator/evaluator), la decomposición, los handoffs, la verificación, la gestión de contexto. Es la respuesta a *"¿cómo lleva el agente una tarea de horas o días hasta su término sin descarrilarse?"*

> **Regla práctica**: un harness con orquestación sofisticada (Capa B) pero un sustrato pobre (Capa A) hará que el agente navegue a ciegas y consuma su contexto solo en orientarse. Diagnostica y arregla el sustrato primero.

Las técnicas de este documento están agrupadas en dos partes: **Parte I — Orquestación** (Técnicas 1-10), que define el loop autónomo, y **Parte II — Sustrato** (Técnicas 11-16), que define la base sobre la que ese loop opera dentro de un codebase. **Aunque el sustrato aparece después, diagnostícalo y arréglalo primero** —ver la regla práctica de arriba—.

### 0.4 Principio rector

> **Empieza con la solución más simple posible y solo añade complejidad cuando se justifique por evidencia.**

Cada componente del harness codifica una suposición sobre lo que el modelo **no puede hacer solo**. Esas suposiciones envejecen rápido cuando los modelos mejoran. Un componente que era *load-bearing* (sostenía la calidad) en un modelo puede convertirse en *overhead* inútil en el siguiente. Re-evaluar el harness es tan importante como construirlo.

---

## 1. Protocolo de diagnóstico inicial

**Antes de proponer cualquier mejora, el agente debe responder estas preguntas inspeccionando el codebase del usuario.** Si no encuentra evidencia para alguna, debe declararlo explícitamente.

### 1.1 Mapeo de superficie

- [ ] ¿Cuál es el archivo de entrada principal del harness? (típicamente un orquestador que arranca el loop)
- [ ] ¿Qué modelos LLM están involucrados? (proveedor, versión, parámetros)
- [ ] ¿Qué herramientas externas usa el agente? (lista completa: shell, browser, DB, APIs)
- [ ] ¿Cómo se invoca el agente? (CLI, web service, cron, webhook, evento)
- [ ] ¿Cuántos agentes/roles distintos existen? (uno generalista vs múltiples especializados)

### 1.2 Auditoría de prompts

- [ ] ¿Dónde viven los prompts? (strings en código, archivos externos, templates)
- [ ] ¿Hay un único prompt o varios prompts especializados por fase?
- [ ] ¿Los prompts tienen un "ritual de arranque" explícito? (ver Técnica 4)
- [ ] ¿Los prompts prohíben explícitamente comportamientos peligrosos? (borrar tests, declarar victoria prematura)
- [ ] ¿Hay prompts diferenciados para la "primera sesión" vs las "siguientes"?

### 1.3 Auditoría de estado durable

- [ ] ¿Qué artefactos sobreviven al fin de una sesión? (archivos, DB, git, logs)
- [ ] ¿Existe una "fuente única de verdad" sobre qué falta por hacer?
- [ ] ¿Existe un log narrativo de progreso entre sesiones?
- [ ] ¿Hay control de versiones (git u otro) usado activamente por el agente?
- [ ] ¿El estado está estructurado (JSON, schema) o no estructurado (Markdown libre)?

### 1.4 Auditoría de evaluación

- [ ] ¿Quién evalúa el trabajo del agente? (él mismo, otro agente, tests automáticos, humano)
- [ ] ¿Hay separación entre "el que hace" y "el que juzga"?
- [ ] ¿La evaluación se hace contra criterios explícitos y graduables?
- [ ] ¿Los criterios tienen umbrales duros (fail si bajan de X)?
- [ ] ¿La evaluación inspecciona el sistema real (UI viva, endpoints, DB) o solo el código?

### 1.5 Auditoría de gestión de contexto

- [ ] ¿Cómo se maneja el agotamiento del contexto? (compactación, reset, ambos, ninguno)
- [ ] ¿Hay un mecanismo de handoff entre sesiones?
- [ ] ¿El handoff incluye estado suficiente para "arrancar limpio"?
- [ ] ¿Hay loops infinitos detectables? (el agente repitiendo la misma acción sin progreso)

### 1.6 Auditoría de seguridad

- [ ] ¿Las herramientas tienen allowlist o el agente puede ejecutar cualquier comando?
- [ ] ¿El filesystem está restringido al directorio del proyecto?
- [ ] ¿Las credenciales y secretos están aislados del contexto del modelo?
- [ ] ¿Hay logs auditables de qué hizo el agente?

### 1.7 Auditoría del sustrato y legibilidad del codebase

- [ ] ¿Existen archivos de contexto persistentes que el agente lea automáticamente al iniciar? (ej: `CLAUDE.md`, `AGENTS.md`, o equivalentes)
- [ ] ¿Están organizados en capas (raíz para visión global, subdirectorios para convenciones locales) o todo en uno?
- [ ] ¿El archivo raíz es lean (punteros y gotchas críticos) o está inflado con detalle que aplica solo a partes del código?
- [ ] ¿El codebase es navegable? ¿Hay un mapa, índice o estructura de directorios que el agente pueda escanear antes de abrir archivos?
- [ ] ¿Los comandos de test/build/lint están scoped por subdirectorio o el agente corre la suite completa siempre?
- [ ] ¿Hay archivos de exclusión (`.ignore`, `permissions.deny`) para que el agente no gaste contexto en archivos generados, build artifacts o código de terceros?
- [ ] ¿El agente navega por búsqueda agéntica (grep/traversal en vivo) o depende de un índice/embedding que puede estar obsoleto?

### 1.8 Auditoría de puntos de extensión

- [ ] ¿Hay **hooks** que se disparen en momentos clave (inicio, fin, pre-acción)? ¿Se usan solo como guardas o también para auto-mejora?
- [ ] ¿La expertise especializada está empaquetada en **skills** cargables on-demand, o todo se mete siempre en el contexto?
- [ ] ¿Hay un mecanismo para **distribuir** setups que funcionan (plugins, paquetes) o cada quien reconstruye lo suyo?
- [ ] ¿Hay **servidores MCP** conectando al agente con herramientas/datos internos? ¿Se construyeron antes o después de tener lo básico funcionando?
- [ ] ¿Hay navegación **a nivel de símbolo** (LSP o equivalente) o el agente hace pattern-matching sobre texto?
- [ ] ¿Existe un **grafo de conocimiento pre-indexado** del código (call graph, impacto) que el agente pueda consultar, o re-explora desde cero cada vez? Si existe, ¿se consulta desde un subagent y hay fallback al código vivo cuando discrepa?
- [ ] ¿El agente traza el **radio de impacto** de un símbolo antes de modificarlo?
- [ ] ¿Se usan **subagents** para aislar exploración de edición, o todo ocurre en una sola sesión que mezcla ambas?

### 1.9 Auditoría organizacional (si aplica a un equipo)

- [ ] ¿Hay un dueño claro (DRI) de la configuración del harness, o el conocimiento es tribal?
- [ ] ¿Existe una convención estandarizada de archivos de contexto / skills / plugins?
- [ ] ¿El código generado por el agente pasa por el mismo proceso de revisión que el código humano?
- [ ] ¿Hay un ritual de revisión periódica de la configuración (cada 3-6 meses, o tras releases de modelo)?

**Output esperado de esta fase**: un informe estructurado por sección con respuestas concretas + identificación de los 3-5 gaps más críticos, separando claramente gaps de **sustrato** (Capa A) de gaps de **orquestación** (Capa B).

---

# PARTE I — LA ORQUESTACIÓN (capa runtime)

*Cómo se ejecuta una tarea larga: el loop, los roles, la decomposición, la verificación y la gestión de contexto.*

## 2. Técnica 1 — Artefactos durables como memoria externa

### 2.1 Principio

La memoria interna del modelo (vía compactación de contexto) es insuficiente para tareas largas. La memoria debe **externalizarse a archivos en disco con paths estables** que sobrevivan a cualquier reset, restart o handoff.

### 2.2 Los cuatro artefactos canónicos

| Artefacto | Propósito | Formato recomendado |
|-----------|-----------|---------------------|
| **Plan/spec maestro** | Fuente única de verdad de qué hay que construir | JSON estructurado |
| **Log de progreso narrativo** | Bitácora cronológica de qué se hizo, qué falta, qué problemas hubo | Texto plano o Markdown |
| **Historial de versiones** | Permite revertir errores y reconstruir estado funcional | Git (o equivalente) |
| **Script de bootstrap** | Permite levantar el entorno desde cero sin investigación | Shell ejecutable |

### 2.3 Por qué JSON > Markdown para el plan maestro

Cuando el plan debe ser **leído y modificado de forma controlada** por el agente, JSON gana porque:

- El modelo respeta mejor la estructura (es menos propenso a reescribir el archivo entero).
- Es fácil instruir "solo puedes cambiar el campo `X`".
- Permite queries simples (contar items pendientes, filtrar por categoría) desde shell.
- Es parseable por código si quieres añadir validación.

Estructura ejemplo (adaptable a cualquier dominio):

```json
[
  {
    "id": "task_001",
    "category": "functional",
    "description": "Descripción breve y verificable de la unidad de trabajo",
    "steps": [
      "Paso 1 de verificación",
      "Paso 2 de verificación",
      "Paso 3 de verificación"
    ],
    "passes": false
  }
]
```

### 2.4 Reglas anti-corrupción

El prompt del agente debe contener instrucciones explícitas y enfáticas:

- "Solo puedes modificar el campo `passes`."
- "Es CATASTRÓFICO eliminar o editar items en futuras sesiones."
- "Nunca consolides, reordenes ni combines tareas."

Lenguaje fuerte funciona. El objetivo es que el modelo trate este archivo como **read-mostly**.

### 2.5 Diagnóstico para el harness actual

Detecta si:
- ❌ El estado vive solo en la memoria del agente (= se pierde al reset).
- ❌ El plan está en Markdown libre que el agente reescribe completo.
- ❌ No hay log narrativo separado del plan estructurado (ambos cumplen funciones distintas).
- ❌ El agente no usa git para checkpoints.
- ❌ No existe script de bootstrap; cada sesión re-aprende cómo correr el sistema.

---

## 3. Técnica 2 — Separación de roles (initializer / generator / evaluator)

### 3.1 Principio

Un único agente generalista es subóptimo para tareas largas porque:
- Mezcla decisiones estratégicas con tácticas.
- Se auto-evalúa con sesgo positivo.
- Pierde foco al alternar entre planificar, ejecutar y verificar.

La solución es **descomponer en roles con prompts y herramientas distintas**. Mínimo dos roles; idealmente tres.

### 3.2 El patrón de dos agentes (mínimo viable)

**Initializer Agent** — corre **una sola vez** al inicio:
- Lee la spec de alto nivel.
- Expande la spec en un plan exhaustivo y estructurado.
- Prepara el entorno (scripts de bootstrap, repo de control de versiones, estructura de carpetas).
- Hace el commit inicial.
- Opcionalmente empieza a implementar las primeras unidades de trabajo.

**Coding/Worker Agent** — corre en **loop, una sesión por iteración**:
- Cada sesión empieza con contexto en blanco.
- Ejecuta el "ritual de arranque" (Técnica 4).
- Toma **una sola unidad de trabajo** del plan.
- La implementa, la verifica end-to-end, la marca como completada.
- Hace commit + actualiza log narrativo.
- Termina la sesión.

> **Importante**: aunque se llamen "agentes distintos", típicamente son la **misma configuración de LLM + tools** ejecutándose con **prompts iniciales diferentes**. La separación está en el prompt, no necesariamente en la infraestructura.

### 3.3 El patrón de tres agentes (calidad superior)

Añade un tercer rol al patrón anterior:

**Planner Agent** — corre antes que el initializer (o lo absorbe):
- Toma un prompt de 1-4 oraciones.
- Lo expande en una spec completa de producto.
- Define alcance, decisiones técnicas de alto nivel, criterios de éxito.
- **Deliberadamente NO especifica detalles granulares de implementación** —si se equivoca temprano, los errores cascadean—.

**Generator Agent** — equivalente al coding agent del patrón de dos:
- Implementa unidades de trabajo según el plan.

**Evaluator Agent** — corre después de cada unidad de trabajo del generator:
- Inspecciona el resultado con herramientas reales (no solo lee código).
- Puntúa contra criterios explícitos.
- Devuelve feedback accionable al generator.
- Tiene autoridad para rechazar trabajo (failure mode = el generator debe iterar).

### 3.4 Por qué separar generación de evaluación

Los LLM son **patológicamente optimistas con su propio trabajo**. Pedirles que se autocritiquen produce evaluaciones genéricas y permisivas ("looks good!"). La separación tiene tres efectos:

1. **Tractabilidad**: es más fácil tunear un evaluador escéptico que hacer crítico a un generador.
2. **Especialización de tools**: el evaluator puede tener acceso a herramientas que el generator no necesita (browser automation, debugging tools, scrapers de logs).
3. **Loop concreto**: una vez que existe feedback externo, el generator tiene algo concreto contra lo cual iterar.

### 3.5 Inspiración GAN

Esta arquitectura imita las **Generative Adversarial Networks**: un generador produce, un discriminador critica, la tensión entre ambos eleva la calidad. La diferencia es que aquí el "entrenamiento" ocurre en tiempo de inferencia mediante iteraciones del prompt, no por backprop.

### 3.6 Diagnóstico para el harness actual

Detecta si:
- ❌ Un único agente hace todo (planificar + ejecutar + verificar).
- ❌ La "verificación" es solo "pregúntate si lo hiciste bien" sin tools externas.
- ❌ No hay un step de planificación previo a ejecución.
- ❌ El prompt mezcla múltiples roles ("eres planner Y coder Y QA") en el mismo system message.

---

## 4. Técnica 3 — Decomposición incremental

### 4.1 Principio

Los modelos tienden a **"one-shottear"** la solución: intentan resolver todo de golpe, exceden contexto, y dejan el sistema en estado roto. La contraestrategia es forzar trabajo **en chunks tratables y atómicos**.

### 4.2 Tres niveles de granularidad

1. **Feature** (o unidad de trabajo): la unidad atómica. Debe ser completable y verificable end-to-end en una sesión.
2. **Sprint** (opcional): grupo coherente de 5-30 features que comparten contexto técnico.
3. **Spec completa**: el conjunto total del trabajo.

### 4.3 Reglas de descomposición

- Cada unidad debe ser **independientemente verificable**.
- Cada unidad debe tener **pasos de prueba concretos** escritos por adelantado.
- Debe haber un **orden de prioridad** (fundamentales primero).
- Mezclar **unidades estrechas** (2-5 pasos) y **unidades amplias** (10+ pasos) para cubrir tanto detalles como flujos integrales.

### 4.4 La regla "una unidad por sesión"

El prompt del worker debe incluir:

> "Trabaja en UNA SOLA unidad por sesión. Está bien si solo completas una. Habrá más sesiones para continuar."

Esta restricción contra-intuitiva mejora la calidad porque:
- Elimina la tentación de declarar victoria prematura.
- Permite verificación exhaustiva de cada unidad.
- Reduce el riesgo de dejar trabajo a medias al agotarse el contexto.

### 4.5 Diagnóstico para el harness actual

Detecta si:
- ❌ El prompt pide "implementa todo lo que puedas" sin tope.
- ❌ No hay archivo que enumere las unidades de trabajo restantes.
- ❌ Las unidades no tienen criterios de "done" pre-escritos.
- ❌ No hay priorización; el agente elige al azar qué hacer.

---

## 5. Técnica 4 — El ritual de "get your bearings"

### 5.1 Principio

Cada nueva sesión empieza **sin memoria**. El primer acto del agente debe ser **reconstruir su contexto operacional** ejecutando una secuencia ritual y predecible antes de tocar trabajo nuevo.

### 5.2 La secuencia canónica

```
1. pwd                              # ¿Dónde estoy?
2. ls -la                           # ¿Qué hay aquí?
3. cat <spec_file>                  # ¿Qué se supone que construyo?
4. cat <plan_file> | head -N        # ¿Cuál es el estado del plan?
5. cat <progress_log>               # ¿Qué pasó en sesiones anteriores?
6. git log --oneline -20            # ¿Cuál es la trayectoria reciente?
7. <count pending units>            # ¿Cuánto queda?
8. <run bootstrap script>           # ¿El entorno corre?
9. <run smoke test>                 # ¿Lo que ya estaba sigue funcionando?
```

Solo después de completar estos 9 pasos, el agente debe elegir su unidad de trabajo y empezar.

### 5.3 Por qué el smoke test es no-negociable

La sesión anterior pudo haber dejado bugs sin documentar. Si el agente empieza a construir sobre un sistema roto:
- Asume que los bugs son de su nuevo código.
- Pierde tokens debuggeando en el lugar equivocado.
- Empeora el estado.

El smoke test debe verificar **el flujo crítico end-to-end** (en una app de chat: login → enviar mensaje → recibir respuesta). Si falla, **arreglar el bug existente tiene prioridad absoluta sobre cualquier feature nueva**.

### 5.4 Diagnóstico para el harness actual

Detecta si:
- ❌ El agente empieza a trabajar sin leer estado previo.
- ❌ No existe un smoke test sistemático.
- ❌ El agente no verifica que el entorno arranque antes de codificar.
- ❌ El ritual no es explícito en el prompt (queda al criterio del modelo).

---

## 6. Técnica 5 — Gestión de contexto: compactación vs reset

### 6.1 Las dos estrategias

**Compactación**: resumir las partes antiguas del contexto en el mismo agente para liberar tokens. La conversación continúa.
- ✅ Preserva continuidad y matices recientes.
- ❌ Puede arrastrar "context anxiety" (el modelo siente que se acerca al límite y empieza a cerrar trabajo).
- ❌ El resumen puede perder detalles críticos.

**Context Reset**: tirar la sesión completa, arrancar agente nuevo, alimentarlo solo con artefactos durables.
- ✅ Pizarra limpia, sin ansiedad de contexto.
- ✅ Fuerza al harness a tener handoffs robustos.
- ❌ Añade complejidad de orquestación, overhead de tokens y latencia.
- ❌ Requiere que los artefactos contengan estado completo.

### 6.2 Cuándo usar cada una

| Situación | Estrategia |
|-----------|-----------|
| Modelo exhibe context anxiety fuerte | Reset |
| Tarea cabe holgadamente en un contexto | Ni una ni otra |
| Tarea larga pero modelo robusto al contexto largo | Compactación |
| Tarea muy larga + necesidad de checkpoints duros | Reset entre fases |
| Modelo recién actualizado: re-evaluar | Probar sin compactación primero |

### 6.3 El handoff artifact

Si usas resets, el handoff debe contener:
- **Estado del trabajo**: qué unidad está en curso, qué pasos están hechos.
- **Decisiones tomadas**: por qué se eligió X enfoque sobre Y.
- **Bloqueos pendientes**: bugs conocidos, dependencias faltantes.
- **Próximo paso concreto**: qué debe hacer literalmente el siguiente agente.

Sin estos cuatro elementos, el reset cuesta más de lo que ahorra.

### 6.4 Diagnóstico para el harness actual

Detecta si:
- ❌ La gestión de contexto es implícita (el modelo se las arregla).
- ❌ Usa compactación pero el modelo muestra context anxiety (cierra trabajo prematuramente).
- ❌ Usa resets pero el handoff no es estructurado.
- ❌ No se ha re-evaluado la estrategia después de actualizar el modelo.

---

## 7. Técnica 6 — Verificación con herramientas externas

### 7.1 Principio

Razonar sobre el código **no es lo mismo** que verificar que el sistema funciona. Los modelos detectan tipos de bugs muy distintos según la herramienta que usen:

| Herramienta | Detecta | No detecta |
|-------------|---------|------------|
| Leer código | Errores de tipos, lógica obvia, imports faltantes | Bugs de runtime, problemas de UI, integración rota |
| Unit tests | Lógica de funciones puras | Wiring entre capas, side effects |
| `curl` a endpoints | Respuestas HTTP correctas | UI rota, validación de cliente, UX |
| Automatización de navegador | Flujos end-to-end reales | Edge cases que no se cubren en el script |
| Inspección de DB | Estado persistido correctamente | Comportamiento concurrente |

Un harness robusto **combina varios** y verifica desde la perspectiva del usuario final.

### 7.2 Para frontend: automatización de navegador

El evaluator debe poder:
- Navegar la app en un navegador real.
- Hacer clicks, escribir en inputs, hacer scroll —como un humano—.
- Tomar capturas de pantalla en cada paso.
- Leer la consola del navegador para detectar errores JS.
- Verificar tanto **funcionalidad** como **apariencia visual**.

Anti-patrones a evitar:
- ❌ Usar evaluación de JavaScript (`page.evaluate`) para "atajar" pasos de UI.
- ❌ Verificar solo con `curl` y asumir que la UI también funciona.
- ❌ Saltarse la verificación visual.
- ❌ Marcar features como pasadas sin screenshots de evidencia.

### 7.3 Para backend: verificación de comportamiento real

Más allá de unit tests, el evaluator debe:
- Hacer llamadas HTTP a endpoints reales contra un servidor corriendo.
- Verificar estados de DB después de operaciones.
- Probar flujos completos (crear → leer → actualizar → borrar).
- Validar manejo de errores (qué pasa con input inválido, auth fallida, etc).
- Inspeccionar logs en tiempo real.

### 7.4 Para procesos asíncronos y jobs

Si el sistema tiene workers, queues, cron jobs:
- Verificar que los mensajes lleguen y se procesen.
- Verificar idempotencia.
- Verificar manejo de retries y dead-letter queues.

### 7.5 Diagnóstico para el harness actual

Detecta si:
- ❌ La verificación se hace solo leyendo código.
- ❌ No hay herramienta para inspeccionar el sistema corriendo.
- ❌ Los tests pasan en el código pero nadie verifica que la UI funcione.
- ❌ Solo hay verificación funcional, nunca visual.

---

## 8. Técnica 7 — Evaluador calibrado con criterios graduables

### 8.1 Principio

"¿Está bien hecho esto?" es una pregunta sin respuesta consistente. "¿Cumple estos cinco criterios específicos?" sí lo es. La labor de un buen evaluator es convertir juicios subjetivos en grades concretos contra criterios pre-acordados.

### 8.2 Estructura de criterios

Cada criterio debe tener:
- **Nombre corto** (1-3 palabras).
- **Definición operativa** (qué significa concretamente).
- **Ejemplos positivos y negativos** (calibración).
- **Umbral duro** (debajo de X = fail automático).
- **Peso relativo** (no todos los criterios cuentan igual).

### 8.3 Ejemplo de set de criterios (para frontend)

| Criterio | Definición | Umbral |
|----------|-----------|--------|
| Cohesion | ¿Colores, tipografía y layout forman un todo coherente? | Si está mal: fail |
| Originality | ¿Hay decisiones de diseño deliberadas o son plantillas? | Si es 100% template: fail |
| Craft | Jerarquía tipográfica, espaciado, contraste, ratios | Si rompe lo fundamental: fail |
| Functionality | ¿El usuario completa tareas sin adivinar? | Si tareas core fallan: fail |

Para domains más objetivos (backend, lógica), los criterios suelen ser más binarios (pasa/no pasa).

### 8.4 Calibración del evaluator

Tunear un evaluador **lleva trabajo iterativo**. El proceso es:

1. Hacer correr el evaluator sobre N salidas.
2. Comparar su veredicto con tu juicio humano.
3. Cuando divergen, identificar el patrón.
4. Actualizar el prompt del evaluator para incorporar few-shot examples con desgloses de score detallados.
5. Repetir hasta que el evaluator grade de manera razonable.

**Esperar varias rondas de este loop.** Out of the box, los LLM son malos QA: identifican issues legítimos y se autoconvencen de que no importan.

### 8.5 La instrucción de skepticismo

El prompt del evaluator debe ser explícito:

> "Tu trabajo NO es ser amable. Tu trabajo es encontrar todo lo que no cumple los criterios. Si dudas si algo es un bug, asume que lo es y reporta. La generosidad cuesta calidad."

### 8.6 Diagnóstico para el harness actual

Detecta si:
- ❌ La evaluación es prosa libre ("¿quedó bien?") en lugar de criterios discretos.
- ❌ No hay umbrales duros; todo se vuelve "buena onda".
- ❌ El evaluator nunca ha sido calibrado contra juicio humano.
- ❌ Los criterios no penalizan patrones genéricos comunes (en frontend: "AI slop" como gradientes morados sobre cards blancas).

---

## 9. Técnica 8 — Sprint Contracts (negociación pre-implementación)

### 9.1 Principio

Hay una brecha entre **user story** ("el usuario puede crear un proyecto") y **comportamiento testeable** ("clickear botón X muestra modal Y con campos A, B, C"). Si esa brecha se cubre durante la implementación, el generator termina construyendo contra criterios que él mismo inventó al vuelo.

La solución: **antes de escribir código, generator y evaluator negocian un contrato explícito** de qué significa "done" para esta unidad.

### 9.2 Mecánica

1. El **generator** lee la unidad de trabajo del plan.
2. Propone: "Voy a construir esto así, y se verificará con estos N criterios testables."
3. El **evaluator** revisa la propuesta. Puede:
   - Aceptarla.
   - Pedir más criterios.
   - Cuestionar el enfoque ("falta cubrir el caso X").
4. Iteran hasta acuerdo.
5. Solo entonces se escribe código.
6. Cuando el generator entrega, el evaluator verifica **contra ese contrato específico**, no contra criterios nuevos.

### 9.3 Implementación práctica

La comunicación se hace **vía archivos**. Un agente escribe `sprint_3_contract.md`, el otro lee y responde editando el mismo archivo o creando `sprint_3_contract_review.md`. Esto:
- Crea un audit trail.
- Sobrevive a context resets.
- Es inspeccionable por humanos.

### 9.4 Cuándo vale la pena

Sprint contracts añaden overhead. Justifícalo cuando:
- Las unidades de trabajo son complejas (>1 hora de implementación).
- Hay riesgo de malinterpretar la intención del spec.
- El costo de re-trabajar es alto.

Para unidades triviales, salta este paso.

### 9.5 Diagnóstico para el harness actual

Detecta si:
- ❌ El generator implementa primero y descubre los criterios de "done" después.
- ❌ No hay comunicación estructurada entre agentes (solo prompts encadenados).
- ❌ El evaluator inventa criterios sobre la marcha en lugar de validar contra acuerdos previos.

---

## 10. Técnica 9 — Sandboxing y seguridad por defecto

### 10.1 Principio

Un agente con poder ilimitado en tu máquina es un incidente esperando ocurrir. Defense-in-depth se aplica al harness igual que a producción.

### 10.2 Capas de defensa

1. **Allowlist de comandos shell**: solo se permiten ejecutar comandos pre-aprobados (ej: `ls`, `cat`, `git`, `npm`, `node`). Cualquier otro se bloquea.
2. **Restricción de filesystem**: operaciones de archivo restringidas al directorio del proyecto. El agente no puede leer ni escribir fuera.
3. **Aislamiento de credenciales**: secretos en variables de entorno o archivos fuera del contexto del modelo. Nunca en prompts.
4. **Hooks pre-acción**: interceptores que validan cada tool call antes de ejecutarlo. Pueden bloquear, modificar o requerir confirmación.
5. **Logs auditables**: cada acción del agente queda registrada con timestamp para revisión posterior.
6. **OS-level sandbox** (cuando aplicable): ejecutar en contenedor o VM aislada.

### 10.3 La filosofía del allowlist

Más fácil **enumerar lo permitido** que **enumerar lo prohibido**. Empieza con un set mínimo y expande cuando el agente lo necesite genuinamente. Si un agente intenta correr algo bloqueado, eso es el sistema funcionando correctamente —no un bug—.

### 10.4 Diagnóstico para el harness actual

Detecta si:
- ❌ El agente puede ejecutar arbitrary shell commands.
- ❌ No hay restricción de filesystem.
- ❌ Credenciales viajan en el contexto del modelo.
- ❌ No hay logs de qué hizo el agente.
- ❌ Un error del agente puede afectar el sistema host.

---

## 11. Técnica 10 — Stress test del harness y simplificación

### 11.1 Principio

Cada componente del harness encodifica una suposición sobre lo que el modelo no puede hacer solo. **Esas suposiciones envejecen.** Un componente que era load-bearing en una versión del modelo puede ser overhead inútil en la siguiente.

### 11.2 Cuándo re-evaluar

- **Llegó un nuevo modelo**: probar el harness con y sin componente X.
- **Subió el costo / latencia notablemente**: buscar componentes podables.
- **El agente falla de formas que el harness debería prevenir**: el componente no funciona como crees.
- **Cada 3-6 meses como ritual**: independiente de cambios externos.

### 11.3 Metodología metódica

No simplifiques todo a la vez. El proceso correcto:

1. Lista todos los componentes del harness.
2. Estima la hipótesis que codifica cada uno ("este existe porque el modelo no podía X").
3. Remueve **uno solo** y corre el harness en N tareas representativas.
4. Compara output (calidad, tiempo, costo) contra baseline.
5. Si la degradación es mínima → el componente no es load-bearing, déjalo fuera.
6. Si la degradación es significativa → restáuralo y prueba el siguiente.

### 11.4 Frecuencia de los componentes load-bearing

Empíricamente, en el harness moderno de Anthropic:
- Lo que **típicamente se mantiene útil**: planner, evaluator, artefactos durables, ritual de bearings.
- Lo que **puede volverse opcional** con modelos mejores: sprint construct, context resets, decomposition extrema.
- Lo que **escala con modelo**: sprint contracts, calibración del evaluator (más capaz el modelo, más detallados los criterios).

### 11.5 Diagnóstico para el harness actual

Detecta si:
- ❌ No se ha modificado el harness desde su creación a pesar de cambios de modelo.
- ❌ Hay componentes cuya razón de existir nadie recuerda.
- ❌ El harness sigue arquitecturas "best practice" sin evidencia de que apliquen al stack actual.

---

# PARTE II — EL SUSTRATO (capa estática)

*Cómo se hace que un codebase existente sea legible para el agente y cómo se extienden sus capacidades. Estas técnicas se diagnostican y arreglan **antes** que las de orquestación, aunque aparezcan después.*

> Nota sobre el origen: las Técnicas 1-10 derivan principalmente del problema de construir aplicaciones desde cero (greenfield) de forma autónoma. Las Técnicas 11-16 derivan del problema de operar dentro de codebases grandes y existentes (monorepos, sistemas legacy, arquitecturas multi-repo). Si tu caso es backend + frontend con frameworks propios y código ya existente, **esta Parte II es probablemente la de mayor impacto inmediato**.

## 12. Técnica 11 — Legibilidad del codebase

### 12.1 Principio

El agente navega un codebase como lo haría un ingeniero: recorre el filesystem, lee archivos, hace grep, sigue referencias. No depende de un índice pre-construido. Esto se llama **búsqueda agéntica** y tiene una ventaja decisiva sobre los enfoques basados en índices/embeddings (RAG): trabaja siempre contra el código **vivo**, nunca contra una versión obsoleta de hace semanas.

Pero tiene un trade-off: **funciona bien solo cuando el agente tiene suficiente contexto inicial para saber dónde mirar**. Si le pides encontrar un patrón vago en un codebase enorme, agotará la ventana de contexto antes de empezar el trabajo real. La calidad de navegación está acotada por cuán legible hayas hecho el codebase.

### 12.2 El presupuesto de contexto

Hay una tensión fundamental que gobierna todo el sustrato:

> **Demasiado contexto cargado en cada sesión degrada el rendimiento. Demasiado poco deja al agente navegando a ciegas.**

El objetivo es cargar **lo justo y relevante** para la tarea actual, ni más ni menos. Todas las técnicas de sustrato son, en el fondo, formas de gestionar este presupuesto.

### 12.3 Archivos de contexto en capas

El mecanismo central es un conjunto de archivos de contexto persistentes que el agente lee automáticamente al inicio (en Claude Code se llaman `CLAUDE.md`; el patrón es agnóstico —puedes implementar el equivalente en tu harness—). Reglas:

- **Organización aditiva en capas**: un archivo raíz para la visión global, archivos por subdirectorio para convenciones locales. El agente camina el árbol de directorios y carga cada archivo que encuentra en el camino, así que el contexto raíz nunca se pierde.
- **Raíz lean**: el archivo raíz debe contener solo **punteros y gotchas críticos**. Todo lo demás se vuelve ruido que se arrastra en cada sesión.
- **Inicializar en subdirectorios, no en la raíz**: el agente trabaja mejor cuando está scoped a la parte del código relevante a la tarea. Aunque la tooling de monorepos asuma acceso desde la raíz, scopear al subdirectorio reduce el ruido sin perder el contexto de capas superiores.

### 12.4 Otras palancas de legibilidad

- **Scopear comandos de test/build/lint por subdirectorio**: correr la suite completa cuando el agente tocó un solo servicio causa timeouts y desperdicia contexto en output irrelevante. Cada subdirectorio debe declarar los comandos que aplican a esa parte. *(En monorepos de lenguajes compilados con dependencias cruzadas profundas, esto es más difícil y puede requerir configuración de build específica.)*
- **Archivos de exclusión** (`.ignore`, reglas `permissions.deny` versionadas): excluir archivos generados, build artifacts y código de terceros para que el agente no gaste contexto en ruido. Versionarlas significa que todo el equipo hereda la misma reducción de ruido.
- **Mapas del codebase**: cuando la estructura de directorios no se explica sola, un archivo markdown ligero en la raíz que liste cada carpeta top-level con una línea de descripción le da al agente una tabla de contenidos que escanear antes de abrir archivos. Para cientos de carpetas, hacerlo en capas (raíz describe lo más alto, subdirectorios el siguiente nivel on-demand).

### 12.5 Diagnóstico para el harness actual

Detecta si:
- ❌ No hay archivos de contexto persistentes; cada sesión re-descubre las convenciones.
- ❌ Hay un único archivo de contexto monolítico e inflado que se carga siempre.
- ❌ El archivo raíz contiene detalle que solo aplica a partes específicas del código.
- ❌ El agente corre la suite de tests completa para cualquier cambio.
- ❌ No hay exclusiones; el agente abre archivos generados y de terceros.
- ❌ El codebase no tiene mapa ni estructura legible; el agente navega a ciegas.
- ❌ Se depende de un índice/embedding que puede estar desactualizado respecto al código vivo.

---

## 13. Técnica 12 — Los puntos de extensión del harness

### 13.1 Principio

Una idea central: **el harness importa tanto como el modelo**. El error más común es creer que la capacidad del agente está definida solo por el modelo. En la práctica, el ecosistema construido alrededor del modelo determina el rendimiento más que el modelo solo.

El sustrato se construye con un conjunto de **puntos de extensión**, y **el orden en que se construyen importa** porque cada capa se apoya en la anterior.

### 13.2 Los puntos de extensión, en orden de construcción

| Punto de extensión | Qué es | Cuándo carga | Mejor para | Error común |
|--------------------|--------|--------------|-----------|-------------|
| **Archivos de contexto** | Contexto que el agente lee automáticamente | Cada sesión | Convenciones del proyecto, conocimiento del codebase | Usarlo para expertise reutilizable que debería ser un skill |
| **Hooks** | Scripts que corren en momentos clave | Disparados por eventos | Comportamiento consistente automático, capturar aprendizajes | Usar prompts para cosas que deberían correr automáticamente |
| **Skills** | Instrucciones empaquetadas por tipo de tarea | On-demand, cuando son relevantes | Expertise reutilizable entre sesiones | Meter todo en el archivo de contexto |
| **Plugins** | Bundle de skills + hooks + configs | Disponibles una vez configurados | Distribuir un setup que funciona por toda la org | Dejar que los buenos setups queden tribales |
| **Navegación por símbolo (LSP)** | Inteligencia de código en tiempo real | Disponible una vez configurada | Navegación a nivel de símbolo en lenguajes tipados | Asumir que es automática |
| **Servidores MCP** | Conexiones a herramientas y datos externos | Disponibles una vez configurados | Dar acceso a herramientas internas inalcanzables | Construir MCP antes de tener lo básico funcionando |
| **Subagents** | Instancias aisladas de agente para tareas | Cuando se invocan | Separar exploración de edición, trabajo paralelo | Correr exploración y edición en la misma sesión |

### 13.3 La regla del orden

Construye de arriba hacia abajo en la tabla. **Los archivos de contexto van primero** (dan el conocimiento base). Luego hooks (automatizan consistencia). Luego skills (expertise on-demand). Plugins distribuyen. MCP y LSP extienden. Subagents delegan. El error recurrente es construir MCP y conexiones sofisticadas **antes** de tener archivos de contexto y hooks funcionando.

### 13.4 Diagnóstico para el harness actual

Detecta si:
- ❌ Se invirtió en MCP/integraciones complejas antes de tener contexto base sólido.
- ❌ Expertise reutilizable está embebida en el archivo de contexto en vez de en skills.
- ❌ Reglas que deberían ser deterministas (lint, format) se confían a instrucciones de prompt.
- ❌ No hay forma de distribuir el setup; cada quien reconstruye.

---

## 14. Técnica 13 — Harness auto-mejorable mediante hooks

### 14.1 Principio

Los hooks son scripts que se disparan en momentos clave del ciclo del agente. La mayoría los piensa solo como **guardas** (impedir que el agente haga algo malo), pero su uso más valioso es la **auto-mejora continua** y la **ejecución determinista**.

### 14.2 Tres usos de los hooks

1. **Captura de aprendizajes (stop hook)**: al terminar una sesión, un hook puede reflexionar sobre lo que ocurrió y proponer actualizaciones a los archivos de contexto **mientras el contexto está fresco**. Esto hace que el sustrato mejore solo con el uso.
2. **Carga dinámica de contexto (start hook)**: al iniciar, un hook puede cargar contexto específico del equipo o del módulo dinámicamente, de modo que cada sesión obtiene el setup correcto sin configuración manual.
3. **Enforcement determinista**: para chequeos como linting y formatting, un hook los aplica de forma determinista —produce resultados más consistentes que confiar en que el modelo recuerde una instrucción—.

### 14.3 La distinción clave: determinista vs probabilístico

> Si algo **debe** ocurrir siempre, hazlo con un hook (determinista). Si requiere juicio, déjalo en el prompt (probabilístico).

Confiar comportamientos críticos a instrucciones de prompt es frágil: el modelo puede olvidarlas, especialmente con el contexto lleno. Un hook no olvida.

### 14.4 Diagnóstico para el harness actual

Detecta si:
- ❌ No hay hooks; todo el comportamiento depende de que el modelo recuerde instrucciones.
- ❌ Los hooks existentes solo bloquean, nunca mejoran el sustrato.
- ❌ Chequeos deterministas (lint, format, tests) se piden por prompt en lugar de forzarse por hook.
- ❌ No hay mecanismo para que los aprendizajes de una sesión se persistan a los archivos de contexto.

---

## 15. Técnica 14 — Presupuesto de contexto y progressive disclosure

### 15.1 Principio

En un codebase con docenas de tipos de tarea, no toda la expertise necesita estar presente en cada sesión. Cargar todo siempre compite por espacio de contexto y degrada el rendimiento. La solución es **progressive disclosure**: empaquetar workflows especializados y conocimiento de dominio en **skills** que se cargan **solo cuando la tarea lo requiere**.

### 15.2 Cómo funciona

- Un skill de revisión de seguridad se carga cuando el agente evalúa código en busca de vulnerabilidades.
- Un skill de procesamiento de documentos se carga cuando hay que actualizar documentación.
- El resto del tiempo, esa expertise **no ocupa contexto**.

### 15.3 Skills scoped por path

Los skills pueden atarse a partes específicas del codebase para que solo se activen donde son relevantes. Un equipo que es dueño de un servicio de pagos puede atar su skill de deployment a ese directorio, de modo que nunca se auto-cargue cuando alguien trabaja en otra parte.

### 15.4 El criterio de qué va dónde

| Tipo de contenido | Dónde va |
|-------------------|----------|
| Convenciones que aplican a todo y siempre | Archivo de contexto (carga cada sesión) |
| Expertise reutilizable de un tipo de tarea | Skill (carga on-demand) |
| Comportamiento que debe ser determinista | Hook |
| Conocimiento de una parte específica del código | Archivo de contexto del subdirectorio o skill scoped a ese path |

### 15.5 Diagnóstico para el harness actual

Detecta si:
- ❌ Toda la expertise se carga siempre, independientemente de la tarea.
- ❌ El contexto base está inflado con conocimiento que aplica solo a tareas raras.
- ❌ No se aprovecha el scoping por path; expertise irrelevante se auto-carga.
- ❌ El rendimiento degrada con el tamaño del contexto base y nadie lo ha medido.

---

## 16. Técnica 15 — Subagents: separar exploración de edición

### 16.1 Principio

Un **subagent** es una instancia aislada del agente con su propia ventana de contexto, que toma una tarea, la ejecuta y devuelve **solo el resultado final** al agente padre. El uso de mayor valor es **separar la exploración de la edición**.

### 16.2 El patrón mapper + editor

1. Un subagent **read-only** mapea un subsistema (explora archivos, sigue referencias, entiende la arquitectura) y escribe sus hallazgos en un archivo.
2. El agente principal lee ese resumen y **edita con la imagen completa**, sin haber gastado su propia ventana de contexto en la exploración.

Esto resuelve un problema concreto: la exploración consume mucho contexto con información que, una vez sintetizada, ya no se necesita en crudo. Aislarla en un subagent mantiene limpio el contexto del editor.

### 16.3 Otros usos

- **Trabajo paralelo**: varios subagents atacando sub-tareas independientes a la vez.
- **Aislamiento de ruido**: cualquier tarea que genere mucho output intermedio (búsquedas masivas, análisis de logs) puede confinarse a un subagent que solo devuelve la conclusión.

### 16.4 Relación con la separación de roles (Técnica 2)

La Técnica 2 separaba roles por **función de juicio** (generar vs evaluar). Los subagents separan por **gestión de contexto** (explorar vs editar). Son complementarias: un evaluator puede a su vez delegar exploración a un subagent.

### 16.5 Diagnóstico para el harness actual

Detecta si:
- ❌ La exploración del codebase y la edición ocurren en la misma sesión, llenando el contexto.
- ❌ Tareas con mucho output intermedio contaminan el contexto principal.
- ❌ No se aprovecha el paralelismo para sub-tareas independientes.

---

## 17. Técnica 16 — Inteligencia estructural del código (LSP y knowledge graphs)

### 17.1 Principio

Sin inteligencia de código, el agente hace **pattern-matching sobre texto**: un grep de un nombre de función común devuelve miles de coincidencias y el agente quema contexto abriendo archivos para descubrir cuál importa. La inteligencia estructural le da precisión **a nivel de símbolo**: seguir una llamada hasta su definición, rastrear referencias entre archivos, y distinguir funciones homónimas. El filtrado ocurre **antes** de que el agente lea nada.

### 17.2 Dos formas de inteligencia estructural

| Forma | Qué es | Fortaleza | Debilidad |
|-------|--------|-----------|-----------|
| **En tiempo real (tipo LSP)** | Servidor de lenguaje que responde go-to-definition / find-references al vuelo | Siempre refleja el estado vivo del código | Limitado a consultas puntuales; no precomputa call graphs ni impacto |
| **Grafo pre-indexado (knowledge graph)** | Índice persistente de símbolos y aristas (llamadas, imports, herencia) consultable por queries ricas | Devuelve call graphs, radio de impacto y contexto en una sola llamada; muy eficiente en tokens | Es un índice: puede quedar **obsoleto**; indexa estructura, no semántica ni corrección |

Un *knowledge graph* (p. ej., una herramienta que parsea el código a ASTs con tree-sitter, extrae nodos/aristas y los guarda en una DB local con auto-sync) puede reducir drásticamente las tool calls de exploración —los benchmarks publicados reportan reducciones del orden del 90%—. Es una palanca poderosa, pero conlleva un trade-off que debes entender.

### 17.3 La tensión índice vs búsqueda agéntica (leer con cuidado)

La Técnica 11 advertía que los índices pueden devolver resultados obsoletos (una función renombrada, un módulo borrado) frente a la búsqueda agéntica en vivo. **Un knowledge graph reintroduce un índice**, exactamente lo que esa advertencia señala. Las buenas implementaciones lo mitigan con auto-sync por eventos del filesystem y debouncing, pero la preocupación persiste:

- Hay una **ventana de desfase** (el intervalo de debounce, más cualquier fallo de sync) donde el grafo puede no reflejar el código vivo.
- El grafo indexa el **esqueleto estructural** (qué llama a qué), no la semántica ni si el código es correcto.

**Regla de uso**: trátalo como un acelerador de *navegación e impacto*, no como sustituto de leer el código cuando lo que está en juego es la corrección. Cuando el grafo y el código vivo discrepen, **el código vivo gana**.

### 17.4 Usos operativos (en qué pasos del harness aporta)

La inteligencia estructural es una capacidad de sustrato que varios roles consumen:

1. **Exploración vía subagent (Técnica 15)** — el encaje primario. El subagent read-only consulta el grafo para mapear un subsistema en una o pocas llamadas y devuelve el resumen, sin que el editor gaste su contexto explorando. Las herramientas de grafo que devuelven mucho código fuente deben usarse **desde el subagent, no desde la sesión principal**.
2. **Análisis de impacto pre-edición (implementador/refinador)** — *concepto clave*. **Antes** de modificar un símbolo, el agente traza su radio de impacto (callers/callees) para no romper silenciosamente a sus dependientes. Es "verificación antes de actuar", complementaria a la verificación posterior de la Técnica 6.
3. **Bearings del worker (Técnica 4)** — orientarse consultando el grafo quema mucho menos contexto que grep/ls/cat repetidos.
4. **Planner en brownfield (Técnica 2)** — antes de escribir la spec, consultar la estructura existente para que el plan respete la arquitectura actual en vez de inventar una paralela.
5. **Selección de tests afectados, vía hook (Técnica 13)** — trazar qué tests dependen (transitivamente) de los archivos cambiados y correr **solo esos**, de forma determinista, en un hook pre-commit o de fin de sesión. Acelera el loop de verificación sin sacrificar cobertura relevante.

### 17.5 Caveat de setup

No es automático: LSP requiere instalar el servidor de lenguaje; un knowledge graph requiere indexar el proyecto y mantener el sync corriendo. El error común es asumir que la precisión de símbolo viene gratis, o confiar ciegamente en un índice sin política de fallback al código vivo.

### 17.6 Diagnóstico para el harness actual

Detecta si:
- ❌ El agente busca por string y abre muchos archivos para desambiguar.
- ❌ En codebases tipados o multi-lenguaje, el agente aterriza en el símbolo equivocado.
- ❌ Hay un LSP corriendo en el IDE del equipo pero no está expuesto al agente.
- ❌ El agente re-explora la misma estructura desde cero en cada tarea/sesión, sin un grafo pre-indexado que consultar.
- ❌ El agente modifica símbolos sin trazar antes su radio de impacto.
- ❌ Si hay un knowledge graph, se consulta desde la sesión principal (llenando contexto) en vez de desde un subagent; o no hay fallback al código vivo cuando el grafo discrepa.
- ❌ La suite de tests corre completa porque nadie traza qué tests dependen del cambio.

---

## 18. La capa organizacional (cuando el harness es de un equipo)

*Esta sección aplica si el harness lo mantiene un equipo, no un individuo. Si trabajas solo, léela como guía de higiene de mantenimiento.*

### 18.1 La configuración técnica no basta

Los rollouts más exitosos invirtieron también en la **capa organizacional**. El patrón: un equipo pequeño —a veces una sola persona— cablea la tooling **antes** del acceso amplio, de modo que la primera experiencia de cada desarrollador es productiva y no frustrante.

### 18.2 El dueño (DRI) o "agent manager"

La versión mínima viable es un **DRI** (directly responsible individual): una persona con autoridad sobre la configuración del harness, las políticas de permisos, el marketplace de plugins y las convenciones de archivos de contexto, y con la responsabilidad de mantenerlos al día. En orgs más grandes emerge un rol de **agent manager** (híbrido PM/ingeniero) dedicado a gestionar el ecosistema.

### 18.3 Evitar el conocimiento tribal

La adopción bottom-up genera entusiasmo pero se fragmenta sin alguien que centralice lo que funciona. Sin un dueño que ensamble y evangelice convenciones (jerarquía estándar de archivos de contexto, set curado de skills y plugins), el conocimiento queda tribal y la adopción se estanca. **Los plugins son el vehículo de distribución**: empaquetan un setup que funciona para que un ingeniero nuevo tenga el mismo contexto y capacidades desde el día uno.

### 18.4 Gobernanza

En entornos regulados, las preguntas de gobernanza surgen temprano: quién controla qué skills/plugins están disponibles, cómo se evita que miles de ingenieros reconstruyan lo mismo, cómo se asegura que el código generado pase la misma revisión que el código humano. Recomendación: empezar con un set acotado de skills aprobados, procesos de revisión requeridos y acceso inicial limitado, y expandir conforme crece la confianza.

### 18.5 Diagnóstico para el harness actual

Detecta si:
- ❌ El conocimiento del harness es tribal; no hay dueño claro.
- ❌ No hay convención estandarizada; cada quien improvisa su setup.
- ❌ El código generado por el agente no pasa por revisión equivalente al humano.
- ❌ No hay distribución; los buenos setups no se propagan.

---

## 19. Anti-patrones a buscar activamente

Lista de banderas rojas que el agente debe reportar si las encuentra en el harness actual:

| # | Anti-patrón | Por qué es problema |
|---|-------------|---------------------|
| 1 | **Mega-prompt monolítico** | Mezcla roles, dificulta debugging, no permite especialización |
| 2 | **Estado solo en memoria** | Cualquier crash o reset pierde todo el trabajo |
| 3 | **Auto-evaluación** | LLMs son patológicamente optimistas; sin separación, calidad colapsa |
| 4 | **"Implementa todo lo que puedas"** | Garantiza one-shotting y declaración prematura de victoria |
| 5 | **Verificación solo por código** | No detecta bugs de runtime, UI, integración |
| 6 | **Plan en Markdown editable libre** | El agente lo reescribe completo, se pierde el plan original |
| 7 | **Sin git activo** | No hay forma de revertir errores; cada bug es permanente |
| 8 | **Allowlist ausente** | Cualquier comando puede ejecutarse; riesgo de seguridad |
| 9 | **Sin smoke test al iniciar** | Bugs heredados se atribuyen al nuevo código |
| 10 | **Sin handoff estructurado** | Si hay resets, el nuevo agente arranca ciego |
| 11 | **Criterios subjetivos sin calibrar** | Evaluación inconsistente entre runs |
| 12 | **Cero retry/iteration loop** | Un fallo del evaluator termina la sesión sin oportunidad de arreglarlo |
| 13 | **Hardcoded para un modelo específico** | Acoplado a un proveedor; difícil de migrar o actualizar |
| 14 | **Sin observabilidad** | Imposible saber por qué falló una run |
| 15 | **Prompts en strings de código** | Difíciles de iterar; no versionables independientemente |
| 16 | **Sin archivos de contexto persistentes** | El agente re-descubre convenciones cada sesión; navega a ciegas |
| 17 | **Archivo de contexto monolítico e inflado** | Carga ruido en cada sesión; degrada rendimiento |
| 18 | **Toda la expertise cargada siempre** | Compite por contexto; no hay progressive disclosure |
| 19 | **Comportamiento determinista confiado a prompts** | Lint/format/checks que el modelo "debería recordar" fallan al llenarse el contexto |
| 20 | **Exploración y edición en la misma sesión** | La exploración llena el contexto que necesita la edición |
| 21 | **Búsqueda solo por string en codebase grande** | Miles de matches; el agente quema contexto desambiguando |
| 22 | **Conocimiento del harness tribal, sin dueño** | No se propaga lo que funciona; la adopción se estanca |
| 23 | **MCP/integraciones antes que lo básico** | Sofisticación sobre cimientos ausentes |
| 24 | **Re-explorar la estructura desde cero cada tarea** | Desperdicia tokens y tiempo; un grafo pre-indexado lo evita |
| 25 | **Editar un símbolo sin trazar su radio de impacto** | Rompe dependientes en silencio |
| 26 | **Confiar ciegamente en un índice sin fallback al código vivo** | El índice obsoleto induce errores que el código vivo habría evitado |

---

## 20. Patrones positivos a buscar (y replicar si funcionan)

| # | Patrón | Señal de salud |
|---|--------|----------------|
| 1 | Prompts en archivos separados, versionados | Iterables sin tocar código |
| 2 | Diferentes prompts para "primera vez" vs "continuación" | Reconoce que las dos fases son distintas |
| 3 | Plan estructurado con campo `passes` protegido | Estado durable y resistente a corrupción |
| 4 | Smoke test forzado al inicio de cada sesión | Detecta regresiones temprano |
| 5 | Commits frecuentes con mensajes descriptivos | Permite revertir granularmente |
| 6 | Evaluator con tools distintas al generator | Especialización real, no solo nominal |
| 7 | Sprint contract en archivo persistente | Audit trail + sobrevive resets |
| 8 | Logs de cada tool call con timestamp | Debuggeable post-mortem |
| 9 | Allowlist explícita de comandos | Seguridad por defecto |
| 10 | Documentación de qué hipótesis codifica cada componente | Permite stress test inteligente |
| 11 | Archivos de contexto en capas, raíz lean | El agente se orienta rápido sin inflar el contexto |
| 12 | Comandos de test/lint scoped por subdirectorio | Evita timeouts y output irrelevante |
| 13 | Hooks que capturan aprendizajes y los persisten al contexto | Sustrato auto-mejorable |
| 14 | Expertise en skills cargados on-demand y scoped por path | Progressive disclosure real |
| 15 | Subagent read-only que mapea antes de que el editor edite | Aísla exploración de edición |
| 16 | Navegación a nivel de símbolo (LSP) configurada | Precisión sin quemar contexto en grep |
| 17 | Dueño (DRI) y distribución vía plugins | El conocimiento no queda tribal |
| 18 | Grafo de conocimiento consultado por el subagent de exploración | ~90% menos tool calls de exploración, contexto limpio |
| 19 | Análisis de impacto antes de editar un símbolo | Previene roturas silenciosas en dependientes |
| 20 | Selección de tests afectados vía hook | Verificación rápida sin perder cobertura relevante |

---

## 21. Playbook de mejora — Orden de aplicación

Cuando hayas terminado el diagnóstico, aplica mejoras en este orden de impacto descendente. **Nota**: en codebases existentes, la Fase 0 (sustrato) suele dar el mayor retorno inmediato y debe ir antes que todo lo demás.

### Fase 0 — Sustrato del codebase (Capa A; arréglalo primero)

0a. **Crear archivos de contexto en capas**: uno raíz lean (punteros + gotchas críticos) y uno por subdirectorio con convenciones locales. Si ya existen pero están inflados, podarlos.
0b. **Hacer el codebase navegable**: añadir un mapa/índice si la estructura no se explica sola; añadir exclusiones para archivos generados y de terceros; scopear comandos de test/lint por subdirectorio.
0c. **Mover expertise reutilizable a skills** cargables on-demand y scoped por path, sacándola del contexto base.
0d. **Convertir comportamiento determinista en hooks** (lint, format, checks que hoy se piden por prompt); añadir un stop hook que capture aprendizajes al contexto.
0e. **Configurar inteligencia estructural del código**: navegación a nivel de símbolo (LSP) si el codebase es tipado o multi-lenguaje, y/o un grafo de conocimiento pre-indexado (call graph + impacto) consultable —idealmente desde el subagent de exploración, con fallback al código vivo cuando discrepe—. Añadir un paso de **análisis de impacto antes de editar** cualquier símbolo, y, si aplica, un hook que corra solo los tests afectados por el cambio.
0f. **Usar subagents para exploración**: que un subagent read-only mapee subsistemas y el agente principal edite con el resumen.

### Fase 1 — Fundamentos de orquestación (sin esto nada más importa)

1. **Externalizar estado a artefactos durables**. Si el plan vive en memoria, sácalo a un archivo JSON. Si no hay log narrativo, créalo.
2. **Habilitar control de versiones activo**. Si el agente no commitea, instruirlo en el prompt para que lo haga al final de cada unidad.
3. **Implementar el ritual de bearings** al inicio del prompt del worker. Sin esto, las sesiones siguientes operan ciegas.
4. **Crear script de bootstrap** que permita levantar el entorno con un solo comando.

### Fase 2 — Separación de roles

5. **Separar el initializer del worker**. Si hay un único prompt monolítico, divídelo en al menos dos prompts especializados.
6. **Introducir un evaluator distinto del generator**. Aunque inicialmente compartan modelo y tools, el prompt debe ser diferente y skeptical.
7. **Dotar al evaluator de herramientas de inspección real**: navegador automatizado para frontend, HTTP client para backend, DB client para data.

### Fase 3 — Calidad

8. **Definir criterios graduables explícitos** para el evaluator. Reemplazar "¿quedó bien?" por una rúbrica concreta.
9. **Calibrar el evaluator** con few-shot examples basados en juicios humanos previos.
10. **Forzar verificación end-to-end** antes de marcar trabajo como completado. Screenshots o equivalente como evidencia.

### Fase 4 — Robustez

11. **Implementar manejo de contexto** explícito: compactación, reset, o híbrido según el modelo.
12. **Diseñar el handoff artifact** si usas resets. Asegurar que contiene estado, decisiones, bloqueos y próximo paso.
13. **Añadir sandboxing y allowlists**. Restringir comandos shell, restringir filesystem, aislar credenciales.
14. **Instrumentar observabilidad**. Logs estructurados de cada tool call.

### Fase 5 — Sofisticación

15. **Introducir sprint contracts** si las unidades son complejas y costosas de re-trabajar.
16. **Añadir un planner agent** si el flujo actual requiere specs detalladas escritas a mano.
17. **Stress test del harness**: remover componentes uno a uno para identificar qué es load-bearing con el modelo actual. Hazlo cada 3-6 meses y siempre que el rendimiento se estanque tras un release de modelo nuevo.
18. **Establecer ownership y distribución** (si es un equipo): asignar un DRI de la configuración y empaquetar el setup en un plugin/bundle distribuible para que el conocimiento no quede tribal.

---

## 22. Plantilla de informe final

Cuando termines el análisis y las mejoras, produce un informe con esta estructura:

```markdown
# Informe de Harness Engineering — [Nombre del Proyecto]

## Estado inicial (snapshot)
- Patrón de orquestación actual: [single-agent / two-agent / three-agent / other]
- Estado del sustrato: [archivos de contexto / hooks / skills / navegabilidad / LSP / subagents]
- Componentes detectados: [...]
- Modelo en uso: [...]

## Gaps críticos identificados
**Sustrato (Capa A):**
1. [Gap 1] — Impacto: [Alto/Medio/Bajo] — Técnica relacionada: §X
**Orquestación (Capa B):**
1. [Gap 1] — Impacto: [Alto/Medio/Bajo] — Técnica relacionada: §X

## Anti-patrones detectados
- [Anti-patrón X] en [archivo:línea]
- ...

## Mejoras aplicadas
1. [Mejora 1]
   - Antes: [estado previo]
   - Después: [estado nuevo]
   - Archivos modificados: [...]
   - Hipótesis: [qué esperas que mejore]

## Mejoras propuestas (no aplicadas)
- [Mejora X] — Razón de no aplicar: [explicación]

## Métricas a observar
- [Métrica 1]: cómo medirla, baseline esperado
- ...

## Próximos pasos sugeridos
- [...]
```

---

## 23. Recordatorio final para el agente lector

- **Sustrato antes que orquestación**. Diagnostica y arregla la Capa A (archivos de contexto, navegabilidad, hooks, skills) antes de tocar la Capa B (loop, roles, contratos). Un loop sofisticado sobre un sustrato pobre desperdicia contexto en orientarse.
- **No asumas frameworks**. Las herramientas mencionadas (Playwright, Git, JSON, shell, LSP, MCP) son ejemplos. Tradúcelas al stack del usuario.
- **Diagnóstico antes que prescripción**. No propongas mejoras sin primero entender el estado actual.
- **Prioriza por impacto**. Aplica primero las mejoras de Fase 1; las de Fase 5 son lujos sin los fundamentos.
- **Justifica cada cambio**. Cada modificación al harness debe poder explicarse en términos de qué limitación del modelo está compensando.
- **Verifica que tus cambios no rompen lo que funcionaba**. Aplicar técnicas indiscriminadamente puede regresionar el sistema.
- **Pregunta cuando dudes**. Si hay decisiones arquitectónicas con trade-offs reales (ej: reset vs compactación), pide input antes de elegir.

**Tu objetivo no es aplicar todas las técnicas. Es elevar el harness a un nivel donde cada componente justifique su existencia.**
