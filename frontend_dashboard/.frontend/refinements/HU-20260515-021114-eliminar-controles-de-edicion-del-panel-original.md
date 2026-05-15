HU id: HU-20260515-021114-eliminar-controles-de-edicion-del-panel

# Eliminar controles de edición del panel de detalle de agente en Chats

# Eliminar controles de edición del panel de detalle de agente en Chats

Como **operador del dashboard**,
quiero **que el panel derecho de la sección Chats muestre solo información de lectura del agente activo**,
para **reducir la fricción visual y evitar acciones de configuración accidentales desde el contexto de una conversación**.

## Acceptance criteria

- **Given** que estoy en la sección Chats con un agente seleccionado, **when** el panel derecho muestra el detalle del agente, **then** no deben aparecer los botones "Prompt", "Flujo", "Probar", "Clonar", "Tokens" ni "Temperature".
- **Given** que el panel derecho de Chats está visible, **when** lo comparo con el panel de detalle de la sección Agentes, **then** el de Chats omite todos los controles de edición/configuración listados arriba.
- **Given** que el panel derecho de Chats no tiene botones de acción, **when** el usuario quiere editar el prompt o la temperatura del agente, **then** no hay ruta de navegación desde ese panel (la edición ocurre en la sección Agentes).
- **Given** que se eliminan esos controles, **when** el panel se renderiza, **then** la información restante (nombre, descripción, estado u otros datos de solo lectura) ocupa el espacio de forma coherente sin elementos vacíos ni huecos.

## Out of scope

- Modificar el panel de detalle de agente en la sección Agentes (esos botones permanecen intactos allí).
- Agregar un enlace o botón "Ir a configurar" que redirija a la sección Agentes.
- Cambiar el layout o diseño visual del panel más allá de remover los controles indicados.
- Alterar la lógica de negocio o los endpoints que alimentan el panel.

## Notas técnicas (opcional)

- El panel derecho en Chats probablemente reutiliza el mismo componente de detalle de agente que la sección Agentes; preferir pasar una prop `readOnly` o `variant="chat"` en lugar de duplicar el componente.
- Si el componente no admite esa prop hoy, agregar solo lo necesario para ocultar los controles en contexto Chats; no refactorizar el componente completo.
- Verificar que eliminar los botones no deje contenedores vacíos con padding/margin visible.
