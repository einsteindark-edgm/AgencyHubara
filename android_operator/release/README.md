# Release de la App Operador en Google Play

La receta completa, paso a paso y con los enlaces oficiales, está en
[`docs/mobile-native/publicar-en-google-play.html`](../../docs/mobile-native/publicar-en-google-play.html).

Aquí queda lo que se sube a Play Console:

| Archivo | Para qué |
|---|---|
| `play-store/icono-512.png` | Ícono de la ficha (512×512, PNG de 32 bits). |
| `play-store/grafico-funciones-1024x500.png` | Gráfico de funciones (obligatorio para publicar la ficha). |
| `play-store/capturas/*.png` | Capturas de teléfono (1080×2160), con datos de prueba. |
| `ficha-play-store.md` | Nombre, descripciones, categoría y contacto. |
| `seguridad-de-datos.md` | Respuestas del formulario «Seguridad de los datos». |
| `politica-de-privacidad.html` | Borrador de la política: completar lo marcado `[COMPLETAR]`, publicarla en una URL pública y ponerla en Terraform (`tenants.hubara.mobile.privacy_url`). |
| `acceso-para-revision.md` | Cuenta de prueba e instrucciones (en inglés) para la revisión de Google. |

Los gráficos y las capturas se regeneran con `play-store/generar_graficos.py` a partir de las capturas del arnés E2E
(instrucciones en el propio script); los datos que se ven son sintéticos («Laura Prueba», números de ceros).

La app solo trae fija la dirección de la configuración del servidor (`hubara.configUrl`); la API, Cognito, la política
de privacidad y la versión mínima los baja de ahí (lo publica `frontend-deploy.yml` desde Terraform).

**Nunca** van al repo (es público): la clave de subida (`.jks`) ni sus contraseñas.
