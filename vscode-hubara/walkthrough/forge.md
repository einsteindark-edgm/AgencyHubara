# Forge — migrar el motor a un cliente nuevo

Forge crea, para cada cliente, **una copia independiente del motor**: su propio
repo, su propia infraestructura (AWS, base de datos, tienda) y su propia voz.
Nada de lo que hagas aquí toca a Hubara: los pasos que tocan AWS solo
**muestran** comandos que apuntan al clon, y forge rechaza nombres o carpetas
que choquen con el proyecto madre.

Forge vive en **su propio ícono 🔥 «Forge»** en la barra de actividad (la columna
de íconos de la izquierda). Si no lo ves, búscalo en el menú **«…»** al final de
esa barra. Abre la consola con `Cmd/Ctrl+Shift+P` → **«Forge: Abrir consola»**.

> En un clon ya forjado no verás Forge: la carpeta `forge/` no viaja al clon.

## 1. Sembrar el cliente

En la tarjeta **«Nuevo cliente»** escribe un nombre corto (minúsculas, sin
espacios, p. ej. `mi_tienda`) y pulsa **Sembrar**. Se crea
`forge/clients/<nombre>/` con:

- **`client.yaml`** — los datos del cliente: `company` (nombre comercial),
  `repo` (dueño/nombre en GitHub), `android_app_id` (el id de su App Operador
  en Google Play, por defecto `com.acktos.<nombre>`), `aws` (región y
  prefijos), `business` (país, moneda, descripción, dominios) y
  **`commerce`**, la política comercial que el bot le dice al cliente: tarifas
  de envío y su zona local, mínimo de contra entrega, llave Nequi (vacía = sin
  pago anticipado), recargos del link de pago, prefijo de los SKU y
  colecciones del catálogo. Forjar **se niega** mientras `commerce` tenga
  `TODO` (salvo un clon de prueba): sin ella el clon cobraría con la política
  de la tienda madre. Forge la escribe en el Terraform del clon
  (`tenants.auto.tfvars → store`). La consola avisa de los demás valores en
  plantilla (`TODO-owner`, `api_url` vacío).
- **`domain.yaml`** — **el concepto de la tienda** para el motor de
  decisiones: nombre, despedida al registrar un pedido y ejemplos de cómo
  habla y de sus productos y variantes.
- **`workspace/`** — las voces del cliente, una carpeta por agente: `sales`
  (ventas), `remarketing` (reactivación), `mba_sales` (el agente de Meta
  Business)… La lista exacta y los archivos que cada uno necesita los define
  `forge/manifest.yaml`; la consola la lee de ahí.

Todo nace con la marca **TODO-BRAND** donde hay que redactar. Reescribe el
contenido para el cliente y **borra cada TODO-BRAND**: mientras quede uno,
«Forjar» está bloqueado (salvo un «clon de prueba»). La tarjeta muestra
cuántos quedan y qué archivos requeridos faltan; haz clic para abrirlos.

## 2. Revisar, forjar, verificar y publicar

1. **Plan** — simulación: qué cambiaría el manifest, sin escribir nada.
2. **⚒ Forjar** — crea la carpeta del clon. Antes te muestra de qué rama y
   commit sale, y te avisa si la rama no es `main` o si hay cambios sin commit
   (esos **no viajan**: el clon sale del último commit).
3. **Verificar** — busca restos de Hubara en el clon (se activa cuando el
   clon existe).
4. **Publicar…** — muestra los comandos para crear el repo en GitHub y
   subirlo. Los corres tú.

## 3. Migración paso a paso (S2–S9)

Cada tarjeta tiene la sección **«Migración paso a paso»** con los 9 pasos, en
orden, y su estado (**hecho**, **pendiente** o **guiado**):

| Paso | Qué hace | Tipo |
|---|---|---|
| S1 | Forjar el repo (lo mismo que «Forjar») | automático |
| S2 | Base de datos nueva en Supabase | automático · `SUPABASE_ACCESS_TOKEN` |
| S3 | Tienda Medusa en Railway | automático · `RAILWAY_API_TOKEN` |
| S4 | Preparar la tienda (región, canal, llave) | automático · `MEDUSA_ADMIN_EMAIL` + `MEDUSA_ADMIN_PASSWORD` |
| S5 | WhatsApp/Meta: número y aprobaciones | guiado (Meta tarda días: empieza pronto) |
| S6 | Temporal Cloud (namespace + llave) | guiado · `TEMPORAL_CLOUD_API_KEY` o `tcld login` |
| S7 | Preparar la cuenta AWS del cliente | guiado |
| S8 | Plataforma AWS + secretos | guiado |
| S9 | Servidor, primer despliegue y tareas programadas | guiado |

- **Ejecutar** corre el paso y su salida aparece en vivo a la derecha.
- Las llaves de los pasos automáticos se leen **del entorno con que abriste
  VS Code**; la consola solo muestra ✓ o ✗. **Nunca las escribas en la
  consola.** Si falta una, cierra VS Code y ábrelo desde una terminal donde la
  hayas exportado (`code .`).
- Los pasos **guiados** solo muestran comandos: córrelos tú en una terminal,
  desde el clon, y después pulsa **Marcar como hecho**.

## 4. Lo que queda: `NEXT_STEPS.md` del clon

Cuando el clon existe, la tarjeta muestra **📋 NEXT_STEPS.md**: la lista de lo
que falta fuera de forge — aprobaciones de Meta/WhatsApp, AWS, Firebase y la
App Operador, tareas programadas (schedules) y la primera sincronización del
catálogo.

## Qué viaja al clon

- **El mismo paquete de decisiones** que usa la tienda madre, leyendo el
  `domain.yaml` del cliente: misma inteligencia, el concepto de su tienda.
- **GraphAgents** (análisis de anuncios), con su propia identidad y secretos.
- **La App Operador** (`android_operator`), con su propio `applicationId`
  tomado de `android_app_id`.
- **El plugin `mba`** (Meta Business Agent) habilitado pero cerrado: no
  responde hasta que el cliente configura `HUBARA_MBA_API_KEY`.

## Requisito

Python con PyYAML (`python3 -m pip install pyyaml`). Si usas otro intérprete,
cámbialo en el ajuste **`acktos.forge.python`**. La consola avisa si falta.
