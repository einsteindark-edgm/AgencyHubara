# Pantallas del servidor (Server-Driven UI)

**Toda la App Operador se arma con los archivos de esta carpeta**: las pestañas (`app.json`), la bandeja (`chats.json`),
Incendios, Órdenes, la ficha del pedido, el chat, la paleta de acciones, las plantillas y las de «Más». La app **no trae
pantallas programadas**: las baja del servidor (`<cloudfront del dashboard>/mobile/screens/<id>.json`, junto a
`config.json`) y las arma con su catálogo de componentes. Una pantalla nueva, un componente cambiado, una pestaña o un
flujo distinto sale a producción **con el deploy del dashboard, sin publicar otra versión en Google Play**.

Lo único nativo es lo que no puede depender de un archivo: el login (antes de la sesión), la pantalla «Hay una versión
nueva», el radar de incendios (una capa encima de todo), el widget y las notificaciones. Lo crítico del teléfono entra a
las pantallas como **piezas nativas** del catálogo (el chat con su composer, el aviso de notificaciones).

| Archivo | Qué es |
|---|---|
| `app.json` | Las pestañas de la barra de abajo, en orden; la primera es la de inicio. |
| `chats.json` | La bandeja: filtros, no leídos, cerrar sesión y privacidad (menú «Más opciones»). |
| `incendios.json` | Incendios de chats y órdenes; cada uno abre su chat en vivo o la ficha del pedido. |
| `ordenes.json` · `pedido.json` | La lista de órdenes y la ficha (hoja) con el siguiente paso según la etapa. |
| `chat.json` · `acciones.json` · `plantillas.json` | El chat (encabezado + pieza nativa `chat`), «+ Más» y reactivar con plantilla. |
| `crear_pedido.json` | «Crear pedido»: el pedido prellenado de la conversación; lo abre la burbuja verde o «+ Más». |
| `mas.json` y las demás | La pestaña «Más»: resumen de ventas, por cobrar, campañas. |

- **Referencia completa** de componentes, propiedades, filtros, acciones e íconos: [CATALOGO.md](CATALOGO.md)
  (generada desde el código: siempre al día).
- **Autocompletado y errores mientras escribes**: abre el archivo en VS Code. La primera línea
  `"$schema": "./screen.schema.json"` le dice qué existe; un error de tipeo se marca en rojo al instante.

## Cómo funciona

```
android_operator/screens/ventas.json ──(merge a main)──▶ frontend-deploy ──▶ <cloudfront>/mobile/screens/ventas.json
                                                                                         │
App Operador ── abre «Resumen de ventas» ── muestra la versión guardada ── baja la del servidor ── pide los datos
                                                                                         │
                                                       GET /api/orders/orders (nuestro backend, con el token)
```

1. Al abrir una pantalla, la app muestra al instante la última versión que guardó (o la que viene dentro del APK) y en
   paralelo baja la del servidor. Si cambió, la reemplaza.
2. Pide los datos de cada fuente de `data` a **nuestro** backend, con el token del operador. Mientras llegan muestra lo
   último que trajo (sin red, la pantalla se ve igual).
3. Arma el `body` con los componentes y enlaza los datos con `{{ … }}`.
4. Vuelve a pedir los datos cuando cambia un filtro, cuando el servidor avisa (`refreshOn`), cada `every` segundos o al
   tirar hacia abajo.

## Crear una pantalla nueva, paso a paso

Ejemplo real: «Pedidos atrasados», con datos que el backend ya tiene.

**1. Elige el endpoint.** Tiene que ser una ruta de nuestro backend (`/api/...`). Para ver qué devuelve, ábrelo en el
dashboard con las herramientas del navegador (pestaña Red) o con `curl` contra el backend local. Algunos útiles:

| Endpoint | Qué trae |
|---|---|
| `/api/orders/orders?limit=500` | `{"orders": [...]}` — cada pedido con `id`, `display_id`, `customer`, `city`, `status`, `pay_status`, `total_cop`, `overdue`, `created_at_ms`, `is_test`… |
| `/api/orders/orders/{id}` | El detalle de un pedido (ítems, dirección, pago). |
| `/api/dashboard/sessions` | `{"sessions": [...]}` — los chats con `session_id`, `phone_number`, `customer_name`, `active_agent_route`, `last_message_preview`… |
| `/api/marketing/campaigns` | `{"campaigns": [...]}` — campañas de WhatsApp. |
| `/api/marketing/campaigns/{id}/stats` | Enviados, respuestas, ventas y bajas de una campaña. |
| `/api/chats/mobile/fires` · `/api/chats/mobile/hot` | Incendios y ventas calientes. |

Si los datos que necesitas no salen de ningún endpoint, hay que agregarlo en el backend (un deploy del backend, no
de la app).

**2. Crea el archivo** `atrasados.json` (el `id` es igual al nombre del archivo: minúsculas, números y `_`):

```json
{
  "$schema": "./screen.schema.json",
  "schema": 1,
  "id": "atrasados",
  "title": "Pedidos atrasados",
  "data": {
    "pedidos": { "get": "/api/orders/orders", "query": { "limit": "500" }, "refreshOn": ["orders"] }
  },
  "computed": {
    "atrasados": "{{pedidos.orders | where:'overdue' | where_not:'is_test' | sort:'created_at_ms'}}"
  },
  "body": [
    { "type": "text", "style": "subtitle", "emphasis": true, "text": "{{atrasados | count | plural:'pedido atrasado':'pedidos atrasados'}}" },
    {
      "type": "list", "items": "{{atrasados}}", "as": "p", "key": "{{p.id}}",
      "item": {
        "type": "list_item", "avatar": "{{p.customer}}",
        "title": "#{{p.display_id | replace:'#':''}} · {{p.customer}}",
        "subtitle": "{{p.city | default:'Sin ciudad'}}",
        "tag": "Atrasado", "tag_tone": "danger",
        "trailing": "{{p.total_cop | money}}",
        "action": { "type": "open_order", "order": "{{p.id}}" }
      },
      "empty": { "type": "empty", "icon": "check_circle", "title": "No hay pedidos atrasados" }
    }
  ]
}
```

**3. Ponle una entrada.** Lo más común: un renglón en [mas.json](mas.json) (la pestaña «Más»), o una pestaña propia en
[app.json](app.json):

```json
{ "type": "list_item", "icon": "warning", "icon_tone": "danger", "chevron": true,
  "title": "Pedidos atrasados", "subtitle": "Los que ya pasaron su fecha",
  "action": { "type": "navigate", "screen": "atrasados" } }
```

Otras entradas: un botón o renglón en otra pantalla (`navigate`), una pestaña en [app.json](app.json) (de 2 a 5; los
cambios de pestañas se aplican la próxima vez que se abre la app) o un enlace `hubara://screen/atrasados` en una
notificación.

**4. Abre el PR.** La CI corre `ScreensRepoTest`: si un componente, propiedad, ícono, filtro, fuente o pantalla no
existe, el PR no pasa y el error dice dónde (`atrasados.json: body[1].item.tag_tone: «rojo» no vale; usa uno de…`).
Si la pantalla trae un flujo nuevo, agrégale un escenario en `../e2e/scenarios.yaml` (ver S17 y S18): el check
**QA emulador** la recorre y deja una captura en el PR.

**5. Mergea.** `frontend-deploy` la publica en minutos. El operador la ve la próxima vez que la abre.

## Cambiar un componente por otro

Es editar el archivo. Por ejemplo, el pie de «Más» como aviso en vez de texto:

```diff
-    { "type": "text", "style": "caption", "tone": "muted", "text": "Estas pantallas las arma el servidor…" }
+    { "type": "notice", "tone": "info", "title": "Novedad", "text": "Ya puedes ver tus campañas desde aquí." }
```

O los pedidos como tarjetas en vez de renglones segmentados: `"style": "cards"` en la `list`.

## Datos

- **Fuentes del teléfono** (`"app": "<nombre>"`): lo que ya vive en la app y se mantiene al día solo — la bandeja en
  Room con lo no leído de este teléfono (`conversations`), los incendios (`fires`, `radar`), el encabezado de un chat
  (`chat`, con `"params": {"session": …}`), las plantillas (`templates`) y datos de la app (`app`). Entregan los campos
  ya listos para mostrar (títulos, «2 mensajes sin leer», «CHAT · GRAVE»…); la lista exacta está en
  [CATALOGO.md](CATALOGO.md#fuentes-de-datos-del-teléfono-app-). `{{status.<fuente>.failed}}` dice si la última
  renovación falló (sin red, se sigue viendo lo guardado).
- **Fuentes del backend** (`data`): `"get"` con la ruta, `"query"` con los parámetros (los vacíos no se mandan), `"optional": true`
  si la pantalla puede mostrarse sin ella, `"refreshOn": ["orders"]` para volver a pedirla cuando el servidor avisa
  (`orders`, `chats`, `fires`, `marketing`) y `"every": 60` para renovarla cada minuto mientras se ve.
- **Parámetros** (`params`): lo que recibe la pantalla al abrirse, p. ej. `"params": ["campaign_id"]` y
  `"get": "/api/marketing/campaigns/{{params.campaign_id}}"`. Quien la abre los manda en `navigate`.
- **Estado** (`state`): valores que la pantalla cambia sola, con su valor inicial. Un `chips` con `"bind": "state.rango"`
  los cambia y toda fuente que use `{{state.rango}}` se vuelve a pedir (ver [ventas.json](ventas.json)).
- **Formulario** (`form`): lo que escribe el operador en un `text_field` o `switch` con `"bind": "form.guia"`; se manda
  en el `body` de una llamada: `"body": {"tracking_url": "{{form.guia}}"}`.
- **Calculados** (`computed`): una cadena de filtros con nombre, para no repetirla (`{{atrasados | count}}`).

## El lenguaje `{{ … }}`

Una ruta (`pedidos.orders.0.customer`) o un literal (`'texto'`, `12`, `true`), seguido de filtros con `|`:

```
{{p.total_cop | money}}                                  → $45.000
{{p.created_at_ms | relative}}                           → hace 5 min
{{pedidos.orders | where:'status':'ready' | count}}      → 3
{{p.status | map:'new=Nuevo;ready=Listo;*=Otro'}}         → Nuevo
{{p.paid | if:'Pagado':'Pendiente'}}                      → Pendiente
{{pedidos.orders | since:'created_at_ms':state.rango}}    → los del rango elegido
```

Lo que no existe se pinta vacío (nunca se cae la pantalla). Todos los filtros: [CATALOGO.md](CATALOGO.md#filtros-de--valor--filtroarg-).

- `visible`: el componente se muestra solo si da verdadero (`"visible": "{{p.overdue}}"`; una lista = todas).
- Dentro de una `list`, cada elemento se lee con el nombre de `as` (por defecto `item`) y su posición con `index`.

## Acciones (las transiciones)

`navigate` (otra pantalla, con `params`; `"sheet": true` la abre como hoja), `open_chat` (`"live": true` lo abre en la
pila de Incendios) y `open_order` (la ficha del pedido), `call` (llama al backend: `method`, `path`, `body`, `confirm`,
`success` y `then`), `confirm` (pregunta y, si el operador acepta, hace `then`), `if` (una u otra según una condición),
`native` (algo que hace la app con lo suyo: mandar por el outbox con deshacer, tomar o devolver la conversación, ocultar
un incendio, cerrar sesión — ver [CATALOGO.md](CATALOGO.md)), `set_state`, `refresh`, `open_url`, `message` y `copy`.
Una lista `[ … ]` ejecuta varias en orden. Ejemplos: [incendios.json](incendios.json) (cada incendio va a su ficha o a su
chat con `if`), [acciones.json](acciones.json) (manda por el outbox y vuelve), [chats.json](chats.json) (cerrar sesión
con `confirm`).

Si el backend rechaza (un error o un `200` con `"success": false`), la app muestra su motivo y no ejecuta `then`.

## Cambiar una pantalla principal

Las pantallas de siempre son archivos como cualquier otro. Por ejemplo, en la bandeja ([chats.json](chats.json)) un
filtro nuevo es una opción más en `chips` (cada filtro es un campo verdadero/falso de la fila, como `unread` o `human`), y
quitar la vista previa del último mensaje es borrar `"subtitle"` del renglón. En el chat ([chat.json](chat.json)) el
encabezado, el botón «Pedido #41» y el menú son de este archivo; el historial, las burbujas y el composer son la pieza
nativa `chat`. Las claves de navegación de siempre (`hubara://chat/…`, el radar, «Volver con …») abren estos mismos
archivos.

## Lo que una pantalla NO puede hacer (a propósito)

- **Ejecutar código.** Solo rutas, literales y la lista cerrada de filtros y acciones.
- **Llamar a otros dominios.** Las rutas son relativas y empiezan con `/api/`: van a nuestro backend con el token del
  operador, y el token nunca sale hacia otro servidor. `open_url` solo abre `https://` o `tel:` escritos tal cual.
- **Usar algo que la app no trae.** Un componente, filtro, ícono o acción nuevo se agrega en la app (`:core:sdui`
  `Catalog.kt` + su render en `:feature:screens`), se sube `Catalog.VERSION` y la pantalla que lo use declara
  `"requires": <versión>`. Esa sí es una versión nueva en Google Play; las apps viejas muestran «Actualiza la app» en
  esa pantalla en vez de pintarla a medias. Después regenera el esquema y la referencia:
  `cd android_operator && ./gradlew :core:sdui:test -PupdateScreens=true`.

## Probar antes de mergear

- `cd android_operator && ./gradlew :core:sdui:test` valida todas las pantallas (lo mismo que la CI).
- En el emulador, el backend de prueba sirve esta carpeta en `/__sandbox/mobile/screens/` y
  `e2e/sandbox/inject.py screen <id>.json --from <archivo>` pone otra versión encima, como haría el servidor.
- El APK lleva una copia de esta carpeta (`assets/screens/`): sin red, la primera vez se ve lo del APK.
