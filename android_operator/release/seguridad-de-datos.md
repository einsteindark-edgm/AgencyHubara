# Formulario «Seguridad de los datos» (Data safety)

Guía oficial: https://support.google.com/googleplay/android-developer/answer/10787469

**¿Cuándo hace falta?** En pruebas cerradas, abiertas y en producción. Una app que solo está en **prueba interna**
está exenta. Lo dejamos listo para cuando pase a cerrada o producción.

Las respuestas salen del código de la app (1-oct-2026). Si la app cambia, se revisan de nuevo:

- **Recolecta:** el email y la contraseña (van a AWS Cognito para iniciar sesión), los mensajes que escribe el
  operador y sus acciones (tomar o devolver una conversación, cambiar la etapa de un pedido, la guía y el costo del
  envío).
- **No recolecta:** ubicación, contactos del teléfono, fotos ni archivos, identificadores del dispositivo,
  analítica ni informes de fallos.
- **Datos de los clientes** (nombres, teléfonos, mensajes, direcciones): la app los **recibe** de nuestro servidor
  para mostrarlos y los guarda en el teléfono hasta que el operador cierra sesión. No los saca del teléfono, salvo lo
  que el operador escribe en un mensaje.

## Respuestas

| Pregunta | Respuesta | Por qué |
|---|---|---|
| ¿La app recolecta o comparte alguno de los tipos de datos requeridos? | **Sí** | Email de la cuenta, mensajes y acciones. |
| ¿Todos los datos se cifran en tránsito? | **Sí** | Todo va por HTTPS; el build de release bloquea HTTP en claro (`network_security_config.xml`). |
| ¿Los usuarios pueden pedir que se borren sus datos? | **Sí** | Escribiendo al email de contacto; además «Cerrar sesión» borra todo lo guardado en el teléfono. |

### Tipos de datos

| Categoría → tipo | ¿Recolectado? | ¿Compartido? | ¿Obligatorio? | Para qué |
|---|---|---|---|---|
| Información personal → **Dirección de email** | Sí | No | Obligatorio | Gestión de la cuenta (iniciar sesión). |
| Mensajes → **Otros mensajes en la app** | Sí | No ¹ | Obligatorio | Funcionalidad de la app (responder a clientes). |
| Actividad en la app → **Otras acciones** | Sí | No | Obligatorio | Funcionalidad de la app (tomar conversaciones, avanzar pedidos). |

¹ El mensaje llega al cliente por WhatsApp porque el operador lo envía a propósito, y los proveedores (AWS, la API de
WhatsApp de Meta) solo lo procesan por cuenta de la empresa. Según la guía de Google, ninguna de las dos cosas cuenta
como «compartir». Confírmalo al llenar el formulario: las definiciones están en la misma guía.

**No marcar:** ubicación, información financiera, salud, fotos y videos, audio, archivos, calendario, contactos,
identificadores del dispositivo, información y rendimiento de la app (no hay informes de fallos).
