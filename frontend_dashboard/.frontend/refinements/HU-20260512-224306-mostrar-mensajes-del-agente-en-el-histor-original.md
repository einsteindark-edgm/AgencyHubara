HU id: HU-20260512-224306-mostrar-mensajes-del-agente-en-el-histor

# Mostrar mensajes del agente en el historial del chat

# Mostrar mensajes del agente en el historial del chat

Como **operador del dashboard**,
quiero **ver los mensajes del agente junto con los del usuario en la vista de chat**,
para **tener visibilidad completa de cada conversación sin salir del dashboard**.

## Acceptance criteria

- **Given** el panel de chat está abierto con una conversación existente, **when** el operador carga la vista, **then** se renderizan todos los mensajes —tanto del usuario como del agente— en orden cronológico.
- **Given** un mensaje pertenece al agente, **when** se muestra en el chat, **then** está visualmente diferenciado del mensaje del usuario (alineación opuesta y color de burbuja distinto).
- **Given** una conversación con múltiples turnos, **when** el operador hace scroll, **then** la secuencia completa usuario→agente→usuario→agente se mantiene sin saltos ni mensajes fuera de orden.
- **Given** la consulta de mensajes está en curso, **when** el panel de chat monta o refresca, **then** se muestra un skeleton/loading que cubre tanto burbujas de usuario como de agente.
- **Given** una conversación en la que el agente todavía no respondió, **when** el operador la visualiza, **then** solo aparecen los mensajes del usuario sin placeholders vacíos ni errores.

## Out of scope

- Streaming en tiempo real de las respuestas del agente (WebSocket / SSE).
- Enviar mensajes nuevos desde el dashboard.
- Editar o eliminar mensajes del agente.
- Filtrar la vista para mostrar solo mensajes del usuario o solo del agente.
- Mostrar metadata interna del agente (prompts del sistema, tool calls, tokens usados).

## Notas técnicas

- El modelo de mensaje debe incluir un campo `sender: "user" | "agent"` para distinguir el origen; validar con Zod al parsear la respuesta de la API.
- Reusar el componente de burbuja de mensaje existente añadiendo una prop `sender` que controle alineación (derecha para usuario, izquierda para agente) y variante de color Tailwind.
- Usar TanStack Query para el fetch del historial completo; la clave de query debe incluir el `conversationId` para invalidación correcta.
- Si la API devuelve los mensajes del agente en un campo separado, normalizar a un array unificado ordenado por `timestamp` antes de renderizar.
