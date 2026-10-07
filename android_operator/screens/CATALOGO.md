<!-- Generado desde Catalog.kt y Filters.kt: no lo edites a mano. ./gradlew :core:sdui:test -PupdateScreens=true -->
# Catálogo de pantallas del servidor (versión 1)

Lo que esta versión de la App Operador sabe pintar y hacer. La guía para crear una pantalla está en
[README.md](README.md). Todo texto acepta `{{ … }}`.

## Componentes

### `column`

Apila sus hijos de arriba abajo.

_Lleva `children`._

| Propiedad | Tipo | Qué es |
|---|---|---|
| `spacing` | número | Espacio entre hijos en dp (por defecto 8). |
| `padding` | número | Margen interno en dp. |
| `align` | `start` · `center` · `end` | Alineación horizontal. |

### `row`

Pone sus hijos uno al lado del otro. Un hijo con `"weight": 1` ocupa el espacio que sobra.

_Lleva `children`._

| Propiedad | Tipo | Qué es |
|---|---|---|
| `spacing` | número | Espacio entre hijos en dp (por defecto 8). |
| `align` | `top` · `center` · `bottom` | Alineación vertical. |
| `arrange` | `start` · `center` · `end` · `between` | Cómo reparte el espacio. |
| `scroll` | sí/no | Se desliza de lado si no cabe. |
| `wrap` | sí/no | Pasa a otra línea si no cabe. |

### `grid`

Cuadrícula de N columnas (para tarjetas de cifras).

_Lleva `children`._

| Propiedad | Tipo | Qué es |
|---|---|---|
| `columns` | número | Columnas (por defecto 2). |
| `spacing` | número | Espacio en dp (por defecto 8). |

### `card`

Tarjeta que agrupa a sus hijos. Se puede tocar.

_Lleva `children` · se puede tocar (`action`)._

| Propiedad | Tipo | Qué es |
|---|---|---|
| `style` | `filled` · `outlined` · `elevated` | Relleno tonal, con borde o elevada. |
| `tone` | `neutral` · `primary` · `secondary` · `bot` · `success` · `warning` · `danger` | Color del fondo (neutral por defecto). |
| `padding` | número | Margen interno en dp (por defecto 16). |

### `section`

Un bloque con título (y un enlace opcional a la derecha, con `action` + `action_label`).

_Lleva `children` · se puede tocar (`action`)._

| Propiedad | Tipo | Qué es |
|---|---|---|
| `title` **(obligatoria)** | texto | Título de la sección. |
| `subtitle` | texto | Línea de ayuda debajo. |
| `action_label` | texto | Texto del enlace de la derecha («Ver todo»). |

### `list`

Repite `item` por cada elemento de `items` (cada uno se lee con el nombre de `as`, por defecto `item`, y su posición con `index`), o muestra sus `children` como renglones fijos (un menú).

_Lleva `children` · `items` + `item` (+ `as`, `empty`) o `children` fijos._

| Propiedad | Tipo | Qué es |
|---|---|---|
| `items` | lista | La lista: `{{pedidos.orders}}` o una lista escrita tal cual `[{…}, {…}]`. |
| `key` | texto | Identificador estable de cada elemento (`{{item.id}}`). |
| `style` | `segmented` · `cards` · `plain` | Renglones segmentados (por defecto), tarjetas separadas o plana (sin fondo ni separadores, como la bandeja). |
| `limit` | número | Muestra como mucho N. |

### `spacer`

Espacio vacío.

| Propiedad | Tipo | Qué es |
|---|---|---|
| `size` | número | Alto en dp (por defecto 16). |

### `divider`

Línea divisoria.

### `text`

Un texto.

| Propiedad | Tipo | Qué es |
|---|---|---|
| `text` **(obligatoria)** | texto | El texto. |
| `style` | `display` · `headline` · `title` · `subtitle` · `body` · `label` · `caption` | Tamaño (por defecto body). |
| `emphasis` | sí/no | Versión enfatizada de Material 3 Expressive (más peso). |
| `tone` | `default` · `muted` · `primary` · `bot` · `success` · `warning` · `danger` | Color. |
| `align` | `start` · `center` · `end` | Alineación. |
| `max_lines` | número | Corta con «…» después de N líneas. |

### `icon`

Un ícono suelto.

| Propiedad | Tipo | Qué es |
|---|---|---|
| `name` **(obligatoria)** | ícono | Nombre del ícono. |
| `tone` | `default` · `muted` · `primary` · `bot` · `success` · `warning` · `danger` | Color. |
| `size` | número | Tamaño en dp (por defecto 24). |

### `image`

Una imagen por https.

| Propiedad | Tipo | Qué es |
|---|---|---|
| `url` **(obligatoria)** | texto | Dirección https de la imagen. |
| `ratio` | número | Ancho/alto (por defecto 1.5). |
| `shape` | `rounded` · `square` · `circle` | Forma. |
| `description` | texto | Qué muestra (para TalkBack). |

### `avatar`

Círculo con las iniciales de un nombre.

| Propiedad | Tipo | Qué es |
|---|---|---|
| `name` **(obligatoria)** | texto | Nombre (de ahí salen las iniciales). |
| `seed` | texto | Valor estable para el color (por defecto el nombre). |
| `size` | número | Tamaño en dp (por defecto 40). |

### `tag`

Píldora corta de estado.

| Propiedad | Tipo | Qué es |
|---|---|---|
| `text` **(obligatoria)** | texto | El texto. |
| `tone` | `neutral` · `primary` · `secondary` · `bot` · `success` · `warning` · `danger` | Color con significado. |
| `icon` | ícono | Ícono a la izquierda. |

### `list_item`

El renglón de una lista: ícono o avatar, título, subtítulo, una píldora y un valor a la derecha. Se puede tocar.

_Se puede tocar (`action`)._

| Propiedad | Tipo | Qué es |
|---|---|---|
| `title` **(obligatoria)** | texto | Primera línea. |
| `subtitle` | texto | Segunda línea. |
| `overline` | texto | Línea chica arriba del título. |
| `icon` | ícono | Ícono a la izquierda. |
| `icon_tone` | `neutral` · `primary` · `secondary` · `bot` · `success` · `warning` · `danger` | Color del ícono. |
| `avatar` | texto | Nombre para un avatar a la izquierda (en vez de ícono). |
| `avatar_seed` | texto | Valor estable para el color del avatar (por defecto el nombre). |
| `tag` | texto | Píldora debajo del título. |
| `tag_tone` | `neutral` · `primary` · `secondary` · `bot` · `success` · `warning` · `danger` | Color de la píldora. |
| `trailing` | texto | Valor a la derecha (un total, una cifra). |
| `trailing_caption` | texto | Línea chica debajo del valor (una hora). |
| `trailing_tone` | `default` · `muted` · `primary` · `bot` · `success` · `warning` · `danger` | Color del valor y su línea chica. |
| `chevron` | sí/no | Flecha a la derecha (lleva a otra pantalla). |
| `overline_tone` | `default` · `muted` · `primary` · `bot` · `success` · `warning` · `danger` | Color de la línea de arriba. |
| `emphasis` | sí/no | Título más marcado (algo sin leer). |
| `badge` | texto | Número en una píldora a la derecha (no leídos); vacío o 0 no se muestra. |
| `badge_label` | texto | Lo que dice TalkBack del número («2 mensajes sin leer»). |
| `caption` | texto | Tercera línea chica (quién atiende, en qué va). |
| `caption_icon` | ícono | Ícono chico antes de la tercera línea. |
| `caption_icon_tone` | `default` · `muted` · `primary` · `bot` · `success` · `warning` · `danger` | Color de ese ícono. |

### `stat`

Una cifra con su nombre (para tableros).

_Se puede tocar (`action`)._

| Propiedad | Tipo | Qué es |
|---|---|---|
| `label` **(obligatoria)** | texto | Qué es («Ventas de hoy»). |
| `value` **(obligatoria)** | texto | La cifra. |
| `caption` | texto | Línea chica debajo. |
| `tone` | `neutral` · `primary` · `secondary` · `bot` · `success` · `warning` · `danger` | Color con significado. |
| `icon` | ícono | Ícono arriba. |

### `key_value`

Un dato con su etiqueta, uno debajo del otro (como en la ficha del pedido).

| Propiedad | Tipo | Qué es |
|---|---|---|
| `label` **(obligatoria)** | texto | La etiqueta. |
| `value` **(obligatoria)** | texto | El dato. |

### `stepper`

Los pasos de un proceso con el actual marcado (las etapas de un pedido).

| Propiedad | Tipo | Qué es |
|---|---|---|
| `steps` **(obligatoria)** | opciones | Los pasos en orden: `[{ "value": "…", "label": "…" }]`. |
| `current` **(obligatoria)** | texto | El valor del paso actual. |
| `label` | texto | Lo que dice TalkBack («Etapa: Preparando»). |

### `progress`

Barra de avance. Sin `value` gira indefinida.

| Propiedad | Tipo | Qué es |
|---|---|---|
| `value` | texto | Avance de 0 a 1. |
| `label` | texto | Texto encima. |
| `tone` | `neutral` · `primary` · `secondary` · `bot` · `success` · `warning` · `danger` | Color con significado. |

### `notice`

Aviso en una franja de color. Se puede tocar.

_Se puede tocar (`action`)._

| Propiedad | Tipo | Qué es |
|---|---|---|
| `text` **(obligatoria)** | texto | El aviso. |
| `title` | texto | Título. |
| `tone` | `info` · `success` · `warning` · `danger` | Color (por defecto info). |
| `icon` | ícono | Ícono. |

### `empty`

Estado vacío: ícono grande, título y ayuda.

| Propiedad | Tipo | Qué es |
|---|---|---|
| `title` **(obligatoria)** | texto | Título. |
| `body` | texto | Línea de ayuda. |
| `icon` | ícono | Ícono. |

### `button`

Un botón. Necesita `action`.

_Necesita `action`._

| Propiedad | Tipo | Qué es |
|---|---|---|
| `text` **(obligatoria)** | texto | El texto. |
| `style` | `filled` · `tonal` · `outlined` · `text` | Relleno (por defecto), tonal, con borde o solo texto. |
| `icon` | ícono | Ícono a la izquierda. |
| `enabled` | sí/no | Si se puede tocar (`{{form.guia \| present}}`). |
| `full_width` | sí/no | Ocupa todo el ancho. |
| `tone` | `default` · `danger` | `danger` para acciones que borran o cancelan. |

### `chips`

Elige UNA opción. Guarda el valor en `bind` (`state.x` vuelve a pedir los datos que lo usan).

| Propiedad | Tipo | Qué es |
|---|---|---|
| `bind` **(obligatoria)** | `state.x` / `form.x` | Dónde se guarda: `state.<clave>`. |
| `options` **(obligatoria)** | opciones | `[{ "value": "…", "label": "…" }]`. |
| `style` | `chips` · `segmented` | Chips (por defecto) o botones segmentados. |

### `text_field`

Campo de texto. Lo escrito queda en `bind` (`form.x` para mandarlo en una llamada).

| Propiedad | Tipo | Qué es |
|---|---|---|
| `bind` **(obligatoria)** | `state.x` / `form.x` | Dónde se guarda: `form.<clave>` o `state.<clave>`. |
| `label` **(obligatoria)** | texto | Etiqueta. |
| `placeholder` | texto | Ejemplo dentro del campo. |
| `keyboard` | `text` · `number` · `phone` · `email` · `url` | Teclado. |
| `multiline` | sí/no | Varias líneas. |
| `value` | texto | Valor inicial. |
| `max_length` | texto | Como mucho N letras (un número o un dato: `{{v.max_length}}`). |

### `switch`

Interruptor sí/no. Guarda true/false en `bind`.

| Propiedad | Tipo | Qué es |
|---|---|---|
| `bind` **(obligatoria)** | `state.x` / `form.x` | Dónde se guarda. |
| `label` **(obligatoria)** | texto | Etiqueta. |
| `description` | texto | Línea de ayuda. |

### `chat`

El chat nativo de una conversación: lo que entendió el bot, el historial, deshacer, las burbujas y el composer. Llena el espacio (pantalla con `"layout": "fill"` y `"weight": 1`).

_Pieza nativa de la app._

| Propiedad | Tipo | Qué es |
|---|---|---|
| `session` **(obligatoria)** | texto | La conversación (`wa_…`). |
| `on_more` | acción | Lo que hace «+ Más» y mantener una burbuja (abrir la paleta de acciones). |
| `on_reactivate` | acción | Lo que hace «Reactivar con plantilla» con la ventana de 24 h cerrada. |

### `notifications_banner`

Aviso para activar las notificaciones (solo si están apagadas; pide el permiso).

_Pieza nativa de la app._

Todos aceptan además `visible` (se pinta solo si da verdadero) y, dentro de un `row`, `weight`.

## Acciones

| Acción | Qué hace |
|---|---|
| `navigate` | Abre otra pantalla del servidor: `screen`, `params` y `sheet: true` para abrirla como hoja. |
| `open_chat` | Abre el chat de una conversación: `session` (`live: true` lo abre en la pila de Incendios). |
| `open_order` | Abre la ficha nativa de un pedido: `order`. |
| `open_url` | Abre un enlace https (o `tel:`): `url`. |
| `back` | Vuelve a la pantalla anterior. |
| `refresh` | Vuelve a pedir los datos (todos, o los de `data: [ids]`). |
| `set_state` | Cambia valores de `state` (`values`); los datos que los usan se vuelven a pedir. |
| `call` | Llama al backend: `method` (POST, PUT, PATCH, DELETE), `path`, `query`, `body`, `confirm`, `success` y `then`. |
| `message` | Muestra un aviso corto abajo: `text`. |
| `copy` | Copia un texto: `text`. |
| `native` | Algo que hace la app con lo suyo: `name` (ver acciones nativas) y `args`. |
| `confirm` | Pide confirmación (`title`, `body`, `accept`, `dismiss`) y, si el operador acepta, hace `then`. |
| `if` | Si `condition` da verdadero hace `then`; si no, `else`. |

Una lista `[ {…}, {…} ]` en `action` ejecuta varias en orden.

### Acciones nativas (`{"type": "native", "name": …, "args": {…}}`)

| Nombre | Qué hace |
|---|---|
| `sign_out` | Cierra la sesión y borra del teléfono los chats, borradores y avisos. |
| `open_privacy` | Abre la política de privacidad. |
| `hide_fire` | Saca un incendio del radar (sigue en Incendios). args: fire_id. |
| `take_over` | El operador toma la conversación. args: session. |
| `return_to_bot` | Devuelve la conversación al bot. args: session. |
| `send_tool` | Manda una acción del bot por el outbox, con deshacer. args: session, tool, label. |
| `send_template` | Manda una plantilla aprobada por el outbox. args: session, template, values. |

## Fuentes de datos del teléfono (`"app": …`)

| Fuente | Qué trae |
|---|---|
| `conversations` | Los chats de la bandeja: session_id, title (nombre o número), phone, name, detail («Bot · Interesado · Pedido #41»), preview, unseen, unseen_label, unread, human, has_order, all, time_ms. |
| `fires` | Los incendios (graves primero): fire_id, kind (chat/order), severity (grave/hoy/espera), overline («CHAT · GRAVE»), title, subtitle, getting_worse, session_id, order_id, opens (chat/order), all, grave, chat, order. |
| `radar` | Los incendios graves que el operador no ocultó (lo que cuenta el chip del radar). Mismos campos que fires. |
| `chat` | Una conversación (params: session): session_id, title, subtitle («Tú atiendes»), name, phone, human, window_open, order_id, order_label («Pedido #41»), stage. |
| `templates` | Las plantillas aprobadas: name, title, label (con «· recomendada»), body, preview_body ({{variable}}), fallback, needs_image, is_default, variables [{name, label, max_length}], variable_names. |
| `app` | La app: privacy (si hay política de privacidad para abrir). |

El estado de cada fuente se lee con `{{status.<fuente>.loading}}` y `{{status.<fuente>.failed}}`.

## Filtros de `{{ valor | filtro:arg }}`

### Formato

| Filtro | Qué hace |
|---|---|
| `money` | Plata: `45000` → «$45.000». Con `'USD'` → «US$1,50». |
| `number` | Número con punto de miles: `1234` → «1.234». `number:1` deja un decimal. |
| `percent` | Proporción a porcentaje: `0.256` → «26 %». `percent:1` → «25,6 %». |
| `plural` | `3 \| plural:'pedido':'pedidos'` → «3 pedidos»; con 1 → «1 pedido». |
| `date` | Fecha corta en Bogotá: «28 sep» (con el año si no es este). Acepta milisegundos, segundos o ISO. |
| `time` | Hora en Bogotá: «3:45 p. m.». |
| `datetime` | «28 sep, 3:45 p. m.». |
| `relative` | «ahora», «hace 5 min», «hace 2 h», «ayer», «hace 3 días» y después la fecha. |
| `list_time` | La hora de una fila de la bandeja, como WhatsApp: hoy la hora, «Ayer», el día de la semana y luego la fecha. |

### Lógica

| Filtro | Qué hace |
|---|---|
| `eq` | ¿Es igual? `state.etapa \| eq:'new'`. |
| `ne` | ¿Es distinto? |
| `gt` | ¿Es mayor? |
| `gte` | ¿Es mayor o igual? |
| `lt` | ¿Es menor? |
| `lte` | ¿Es menor o igual? |
| `not` | Lo contrario: `item.paid \| not`. |
| `empty` | ¿Está vacío? (null, «», lista vacía) |
| `present` | ¿Tiene algo? (el 0 sí cuenta) |
| `in` | ¿Está en la lista? `item.status \| in:'new,ready'`. |
| `and` | Y: `item.paid \| and:item.shipped`. |
| `or` | O: `item.overdue \| or:item.flagged`. |
| `if` | Si es verdadero, el primero; si no, el segundo: `item.paid \| if:'Pagado':'Pendiente'`. |
| `map` | Traduce valores: `item.status \| map:'new=Nuevo;ready=Listo;*=Otro'` (`*` = cualquier otro). |
| `when` | Si la condición da verdadero, este valor; si no, sigue el que venía: `o.status \| map:'…' \| when:o.overdue:'danger'`. |
| `default` | Si no hay dato (null o «»), este: `cliente.ciudad \| default:'—'`. |
| `starts_with` | ¿Empieza con…? `form.guia \| starts_with:'http'`. |

### Listas

| Filtro | Qué hace |
|---|---|
| `count` | Cuántos hay (o el largo de un texto). |
| `sum` | Suma un campo: `pedidos.orders \| sum:'total_cop'`. |
| `where` | Solo los que cumplen: `where:'status':'new'`; sin valor, los que tienen el campo en verdadero. |
| `where_not` | Los que NO cumplen. |
| `where_in` | Los que tienen uno de esos valores: `where_in:'status':'ready,shipping'`. |
| `since` | Los de una ventana de fechas en Bogotá: `since:'created_at_ms':'today'` (`week`, `month`, `7d`, `30d`…). |
| `sort` | Ordena de menor a mayor por un campo. |
| `sort_desc` | Ordena de mayor a menor por un campo. |
| `take` | Los primeros N. |
| `first` | El primero. |
| `last` | El último. |
| `pluck` | Saca un campo de cada uno: `pluck:'customer'`. |
| `join` | Une en un texto (por defecto con «, »). |
| `get` | Un campo del valor: `pedidos.orders \| first \| get:'customer'`. |
| `missing` | De una lista de claves, las que están vacías en el objeto: `variable_names \| missing:form`. |

### Cuentas

| Filtro | Qué hace |
|---|---|
| `plus` | Suma. |
| `minus` | Resta. |
| `times` | Multiplica. |
| `divide` | Divide (entre 0 da 0): `spent_usd_micros \| divide:1000000`. |
| `round` | Redondea (con N decimales). |
| `abs` | Sin signo. |
| `to_number` | Texto a número, como lo escribe el operador: «12.000» → 12000, «12,5» → 12.5. |

### Texto

| Filtro | Qué hace |
|---|---|
| `to_text` | Número a texto, tal cual. |
| `upper` | MAYÚSCULAS. |
| `lower` | minúsculas. |
| `capitalize` | Primera letra en mayúscula. |
| `truncate` | Corta a N letras con «…». |
| `trim` | Sin espacios a los lados. |
| `fill` | Llena los huecos `{{clave}}` de un texto con un objeto (`fill:form`); los que faltan, con el segundo (`fill:form:t.fallback`). |
| `replace` | Reemplaza: `display_id \| replace:'#':''`. |

## Íconos

`add` · `apps` · `arrow_back` · `bar_chart` · `bolt` · `bot` · `calendar` · `call` · `campaign` · `chat` · `chat_filled` · `check` · `check_circle` · `chevron_right` · `close` · `edit` · `error` · `filter` · `fire` · `fire_filled` · `group` · `info` · `inventory` · `link` · `location` · `mail` · `more_vert` · `notifications` · `orders` · `orders_filled` · `payments` · `person` · `receipt` · `refresh` · `schedule` · `search` · `sell` · `send` · `settings` · `shipping` · `star` · `storefront` · `trending_down` · `trending_up` · `undo` · `warning`

## Eventos para `refreshOn`

`chats` · `fires` · `marketing` · `orders`
