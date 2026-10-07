# Acceso a la app para la revisión de Google

Guía oficial: https://support.google.com/googleplay/android-developer/answer/15748846

La app pide login, así que **para una prueba cerrada o para producción** Google exige credenciales de prueba:
siempre disponibles, reutilizables y válidas, con instrucciones en inglés. La prueba interna puede no pasar por la
revisión normal.

1. Crea en Cognito un usuario solo para la revisión (por ejemplo `play-review@<dominio de la empresa>`), con una
   contraseña definitiva (no temporal: la revisión no puede cambiarla). No uses la cuenta de una persona.
2. En Play Console → Contenido de la app → Acceso a la app → «Todas o algunas funciones están restringidas» →
   agrega las instrucciones de abajo con ese usuario y su contraseña. La contraseña se escribe solo ahí, nunca en el
   repo.
3. Cuando la revisión termine, puedes deshabilitar el usuario en Cognito y volver a habilitarlo para la siguiente.

## Instrucciones (en inglés)

```
This is an internal app for the customer-service staff of a store. Accounts are created by the administrator;
there is no sign-up.

1. Open the app and sign in with the email and password below.
2. The "Chats" tab lists WhatsApp conversations. Tap one to open it.
3. Tap "Tomar la conversación" (take over the conversation) to reply as a human operator.
4. The "Incendios" tab lists urgent cases; the "Órdenes" tab lists orders.
No two-factor authentication or one-time codes are required.
```
