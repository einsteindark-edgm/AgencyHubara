HU id: HU-20260514-014934-auto-scroll-al-mensaje-mas-reciente-en-l

# Auto-scroll al mensaje más reciente en la vista de conversación

# Auto-scroll al mensaje más reciente en la vista de conversación

Como **operador del dashboard**,
quiero **que la vista de conversación haga scroll automático al mensaje más reciente cada vez que llega un mensaje nuevo**,
para **seguir el intercambio en tiempo real entre cliente y agente sin intervención manual**.

## Acceptance criteria

- **Given** que el operador está viendo una conversación con el scroll posicionado al fondo (≤50 px del bottom), **when** llega un mensaje nuevo (del cliente o del agente), **then** el contenedor de mensajes hace scroll suave hasta el último mensaje sin que el operador toque nada.
- **Given** que el operador ha hecho scroll hacia arriba para revisar historial (>50 px del bottom), **when** llega un mensaje nuevo, **then** NO se fuerza scroll automático y aparece un badge de "↓ Nuevo mensaje" sobre el input o al pie de la lista.
- **Given** que el badge "↓ Nuevo mensaje" es visible, **when** el operador hace clic en él, **then** el scroll salta al último mensaje y el badge desaparece.
- **Given** que la vista de conversación se monta por primera vez (o se cambia de conversación activa), **when** el componente termina de renderizar la lista de mensajes, **then** el scroll se posiciona directamente en el último mensaje sin animación.
- **Given** que el operador llega manualmente al fondo de la lista (scroll hacia abajo hasta ≤50 px del bottom), **when** ese evento de scroll ocurre, **then** el badge "↓ Nuevo mensaje" desaparece si estaba visible.

## Out of scope

- Notificaciones push, sonido o alertas del sistema operativo ante mensajes nuevos.
- Lógica de "marcar como leído" o conteo de mensajes no leídos en otras partes del dashboard.
- Indicador de "el agente está escribiendo" (typing indicator).
- Auto-scroll en vistas de lista de conversaciones (solo aplica a la vista de detalle de una conversación individual).
- Gestión de múltiples conversaciones abiertas en paralelo (paneles side-by-side).

## Notas técnicas

- Usar un `ref` al elemento sentinel vacío al final de la lista; invocar `ref.current.scrollIntoView({ behavior: 'smooth' })` cuando `isAtBottom === true` y llegue un mensaje nuevo desde TanStack Query.
- Calcular `isAtBottom` en el `onScroll` del contenedor: `scrollHeight - scrollTop - clientHeight ≤ 50`.
- El estado `isAtBottom` y la visibilidad del badge deben vivir en el mismo componente de conversación (estado local, no en el store global).
- El scroll al montar debe ser instantáneo (`behavior: 'auto'`) para evitar la animación de arranque.
- Reusar el mecanismo de polling/websocket ya existente en TanStack Query para detectar mensajes nuevos (sin añadir nueva capa de transporte).
