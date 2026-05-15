HU id: HU-20260515-001219-simplificar-panel-derecho-de-chats-elimi

# Simplificar panel derecho de chats eliminando campos y acciones no usados

# Simplificar panel derecho de chats eliminando campos y acciones no usados

Como **operador del dashboard**,
quiero que el panel derecho de la sección de chats no muestre el campo sentimiento, el campo mensajes, la acción cambiar tag ni el botón cerrar,
para reducir el ruido visual y enfocar la atención en la información esencial de cada conversación.

## Acceptance criteria

- **Given** que el operador navega a la sección de chats y selecciona un chat, **when** visualiza el panel derecho, **then** el campo "sentimiento" no aparece en ninguna parte del panel.
- **Given** que el operador selecciona un chat, **when** visualiza el panel derecho, **then** el campo o indicador "mensajes" no aparece en el panel.
- **Given** que el operador selecciona un chat, **when** inspecciona las opciones disponibles en el panel derecho, **then** la acción "cambiar tag" no está visible ni es accionable.
- **Given** que el operador selecciona un chat, **when** inspecciona las acciones del panel derecho, **then** el botón o acción "cerrar" no aparece en ningún lugar del panel.
- **Given** que los cuatro elementos fueron eliminados, **when** el operador visualiza el panel derecho con cualquier chat seleccionado, **then** el layout restante ocupa el espacio sin gaps vacíos ni elementos desalineados.

## Out of scope

- Eliminar o modificar los campos sentimiento, mensajes, tag y estado de cierre en el backend o en la API
- Modificar el panel izquierdo (lista de chats) o la zona central (área de mensajes)
- Agregar nuevos campos, acciones o secciones al panel derecho
- Cambiar la lógica de negocio asociada a sentimiento, tags o cierre de chats
- Ocultar los elementos condicionalmente (p. ej. por rol o feature flag) — la eliminación es permanente e incondicional

## Notas técnicas

- Cambios puramente de presentación (JSX/TSX); no modificar lógica de negocio ni llamadas a la API
- Verificar que no queden importaciones de componentes huérfanos tras eliminar los cuatro elementos
- El layout del panel derecho debe reajustarse visualmente sin spacers artificiales ni clases de altura forzada
