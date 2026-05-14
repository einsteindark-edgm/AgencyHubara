HU id: HU-20260514-005639-scroll-automatico-al-mensaje-mas-recient

# Scroll automático al mensaje más reciente en vista de conversación

# Scroll automático al mensaje más reciente en vista de conversación

Como **operador del dashboard**,
quiero **que la vista de conversación haga scroll automático al mensaje más reciente cada vez que llega un mensaje nuevo**,
para **seguir la conversación entre cliente y agente en tiempo real sin intervención manual**.

## Acceptance criteria

- **Given** la vista de conversación está abierta y el operador está posicionado al final del hilo, **when** llega un mensaje nuevo (del cliente o del agente), **then** el panel de mensajes hace scroll automáticamente hasta el mensaje recién llegado, manteniéndolo visible sin que el operador toque la pantalla.
- **Given** el operador ha hecho scroll hacia arriba para revisar mensajes anteriores (posición != fondo), **when** llega un mensaje nuevo, **then** el scroll NO se fuerza hacia abajo y el operador conserva su posición de lectura actual.
- **Given** el operador está leyendo mensajes anteriores y se acumulan mensajes nuevos sin auto-scroll, **when** el operador hace scroll manualmente hasta el fondo del panel, **then** el modo de auto-scroll se reactiva y los siguientes mensajes nuevos vuelven a desplazar el panel automáticamente.
- **Given** el operador abre por primera vez la vista de una conversación con mensajes existentes, **when** el panel termina de renderizar, **then** la posición inicial del scroll es el mensaje más reciente (fondo del hilo), sin animación de desplazamiento visible.
- **Given** la vista de conversación está activa, **when** el polling de TanStack Query devuelve la misma lista de mensajes sin cambios, **then** no ocurre ningún scroll ni re-render perceptible (sin degradación de rendimiento en actualizaciones vacías).

## Out of scope

- Notificaciones visuales (badge, toast) o sonoras al recibir mensajes nuevos.
- Marcar mensajes como leídos / indicadores de "visto" en base a la posición del scroll.
- Carga de historial paginado (mensajes anteriores al rango cargado inicialmente).
- Scroll a un mensaje específico por búsqueda o referencia.
- Comportamiento en vistas de múltiples conversaciones simultáneas (multi-panel).

## Notas técnicas (opcional)

- Implementar con un `ref` al contenedor de mensajes y un `useEffect` que observe cambios en la lista de mensajes; disparar `scrollIntoView` o `scrollTop = scrollHeight` solo cuando `isAtBottom === true`.
- Calcular `isAtBottom` con un threshold de ~50 px para tolerar imprecisiones de subpíxel: `scrollHeight - scrollTop - clientHeight < 50`.
- Escuchar el evento `scroll` del contenedor para actualizar `isAtBottom` y activar/desactivar el modo sticky.
- TanStack Query ya gestiona el polling; el hook de auto-scroll solo necesita reaccionar al cambio de longitud de `messages[]`.
- No introducir dependencia externa de librería de scroll; implementar con APIs nativas del DOM.
