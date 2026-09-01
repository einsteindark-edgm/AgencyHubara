# Meta Catalog — Setup manual del operador (v2, single-tenant Hubara)

> **Audiencia:** vos (operador admin del negocio Hubara). No es para el dev.
> **Objetivo:** punta a punta — desde "no tengo nada en Meta" hasta "el backend pushea productos a Meta Commerce Catalog y las cards `interactive.product` llegan al cliente con imagen".
> **Tiempo total:** 3–5 horas activas + 1–5 días hábiles esperando Business Verification.
> **Versión Graph API:** a **julio 2026 la última es `v25.0`** (Meta la publicó 2026-02-18). El backend está pinneado a `v23.0` (todavía soportada). Si Meta deprecia una versión, **el dev migra**; vos no tocás nada. Los `curl` de este doc usan `v23.0` para matchear el backend — si el dev te pide otra, cambiá el número de versión y listo.
> **Multi-tenant:** este v2 es single-tenant (un solo Hubara). Cuando onboardeen un segundo cliente, los IDs/tokens migrarán al plugin `agents_admin` (per-tenant config). Por ahora viven como env vars del backend.

---

> ## ⚠️ Qué cambió en la UI de Meta (2026) — leé esto primero si ya hiciste este trámite antes
>
> Meta rehízo varias pantallas entre 2025 y 2026. Los cambios grandes que afectan este runbook:
>
> 1. **Ya NO se elige "App Type" (Business/Consumer/Gaming) al crear la app.** El wizard es ahora **por _use cases_** (`developers.facebook.com/apps/creation/`). El equivalente a "app tipo Business" hoy es: seleccionar el use case de WhatsApp/mensajería **+ conectar un Business Portfolio verificado**. Ver Fase 2 reescrita. (Ya no existe el riesgo "elegí mal el tipo → borrar la app".)
> 2. **La puerta de entrada es Meta Business Suite.** "Business Manager" ya no es una app aparte; `Business Settings` vive dentro de **Business Suite → menú "Todas las herramientas" (All tools) → Business Settings**. El link directo `business.facebook.com/settings` sigue funcionando y es lo más confiable.
> 3. **System Users se movió** a `Business Settings → **Usuarios (Users) → Usuarios del sistema (System users)**`.
> 4. **Registrar un número real de producción es API-only.** Agregás + verificás la propiedad del número en **WhatsApp Manager**, pero el _registro_ para Cloud API se hace con una llamada `POST /<PHONE_NUMBER_ID>/register` (con PIN de 6 dígitos). Eso lo corre el dev. Ver Fase 3 reescrita.
> 5. **Graph API va en `v25.0`.** El test number para dev sigue igual, en `App Dashboard → WhatsApp → API Setup`.

---

## Tabla de contenidos

- [Sección 0 — Decisión crítica antes de tocar nada](#sección-0)
- [Fase 1 — Cuenta Meta + Business Portfolio](#fase-1)
- [Fase 2 — Crear la App de Meta (flujo por use cases, 2026)](#fase-2)
- [Fase 3 — Configurar WhatsApp Cloud API + número](#fase-3)
- [Fase 4 — Confirmar App ↔ Business Portfolio](#fase-4)
- [Fase 5 — Business Verification (gate más largo: 1–5 días)](#fase-5)
- [Fase 6 — Verificar WABA + Phone Number ID](#fase-6)
- [Fase 7 — Crear catálogo en Commerce Manager](#fase-7)
- [Fase 8 — Linkear catálogo al WABA](#fase-8)
- [Fase 9 — Habilitar `catalog_management` en la App (CRÍTICO)](#fase-9)
- [Fase 10 — Crear System User + asignar todos los assets](#fase-10)
- [Fase 11 — Generar token permanente con los 4 scopes](#fase-11)
- [Fase 12 — Verificar scopes con `debug_token`](#fase-12)
- [Fase 13 — WhatsApp Flow de datos de envío (formulario nativo)](#fase-13)
- [Fase 14 — Smoke test manual de 1 producto](#fase-14)
- [Fase 15 — Smoke test end-to-end con el dev](#fase-15)
- [Fase 16 — Catalog Quality Dashboard](#fase-16)
- [Fase 17 — Entregables finales al dev](#fase-17)
- [Fase 18 — Consideraciones LATAM Colombia](#fase-18)
- [Fase 19 — Categorías PROHIBIDAS por Meta](#fase-19)
- [Checklist imprimible](#checklist-imprimible)
- [Anexos](#anexos)
- [Troubleshooting](#troubleshooting)

---

## Sección 0 — Decisión crítica antes de tocar nada
<a id="sección-0"></a>

**Hubara vende velas aromáticas.** Esto está OK para Commerce Policy de Meta **siempre que NO usemos copy con health claims**. Antes de subir ningún producto:

1. Revisar **todos los títulos y descripciones** en Medusa.
2. **Eliminar / reescribir** copy que diga: "alivia ansiedad", "ayuda a dormir", "cura el insomnio", "antidepresivo natural", "terapéutico", "calma migrañas".
3. **Reemplazar por** lenguaje descriptivo: "fragancia relajante", "ambiente de calma", "notas que evocan tranquilidad", "ideal para momentos de descanso".
4. **NO incluir productos con CBD / cannabis** (aun legales en Colombia, Meta los prohíbe globalmente — un solo producto con CBD rechaza el catálogo entero).
5. **Aceites esenciales puros sueltos** (no en una vela terminada) están en zona gris (ingestibles/healthcare). Sacarlos del catálogo Meta o reformular como "aroma para difusor".

> Si saltás este paso, los productos quedan en `rejected` después del primer push y el catalog quality score baja. **Es mucho más barato limpiar copy en Medusa una vez que arreglar producto por producto en Meta.**

---

## Fase 1 — Cuenta Meta + Business Portfolio
<a id="fase-1"></a>

> **Saltá esta fase si ya tenés `business.facebook.com/settings/info` mostrando "Hubara" como Business Portfolio.**

### 1.1 Cuenta personal de Facebook

- Meta exige una cuenta personal de Facebook como dueño de cualquier Business Portfolio. Si todavía no tenés una con tu email corporativo (`@hubara.com.co`), crearla en `https://www.facebook.com/r.php` con email `tu-nombre@hubara.com.co`.
- Verificar el email (Meta manda un link de confirmación).
- Activar **2FA** desde `https://accountscenter.facebook.com/password_and_security` (el viejo `/security/2fac/settings/` redirige acá) — requerido para ser admin de Business.

### 1.2 Crear el Business Portfolio

> **Nota de nombres 2026:** lo que Meta antes llamaba "Business Manager" ahora se llama **Business Portfolio** y se administra desde **Meta Business Suite**. Es la misma cosa; cambió el nombre y el envoltorio.

- URL: `https://business.facebook.com/overview` (Meta Business Suite).
- Si nunca creaste un portfolio: pantalla de bienvenida con botón azul **"Crear cuenta" / "Create account"**.
- Modal "Crea tu portafolio comercial" / "Create your business portfolio":
  - **Nombre del portafolio / Business and account name:** `Hubara` (visible en la UI admin, no en el chat).
  - **Tu nombre / Your name:** tu nombre completo (igual al de Facebook).
  - **Email comercial / Business email:** `tu-nombre@hubara.com.co` (Meta lo verifica vía link).
- Click **"Enviar" / "Submit"** → te llega email de verificación → click el link → cuenta activa.
- URL final del portfolio: `https://business.facebook.com/settings/info`.

### 1.3 Datos del negocio

- Entrá a **Business Settings**. Ruta 2026:
  - **Link directo (lo más confiable):** `https://business.facebook.com/settings`.
  - **O por menú:** Business Suite → ícono hamburguesa **"Todas las herramientas" (All tools)** → **"Configuración del negocio" (Business Settings)**.
- En `Business Settings → Información del negocio (Business info)` (sidebar izquierda):
  - **Legal Business Name:** exacto al certificado de Cámara de Comercio (con "S.A.S.", "Ltda." si aplica).
  - **Address:** dirección física, sin abreviaturas ("Calle" no "Cll").
  - **Phone:** fijo o celular (Meta puede llamar para verificar).
  - **Website:** `https://hubara.com.co`.
  - **Tax ID:** NIT con dígito verificación (`900.XXX.XXX-X`).
- Click **"Guardar cambios" / "Save Changes"**.

### 1.4 Confirmá que sos Admin del portfolio

- `Business Settings → Usuarios (Users) → Personas (People)` → tu nombre → rol debe decir **Admin** (no "Empleado"/"Employee"). Sin Admin, varias fases (System User, tokens, Flows) están bloqueadas.

| Anotar |
|---|
| `BUSINESS_ID = _______________________` (visible arriba en Business Settings → Business info, ~15 dígitos) |

---

## Fase 2 — Crear la App de Meta (flujo por use cases, 2026)
<a id="fase-2"></a>

> **Saltá esta fase si ya tenés una app en `https://developers.facebook.com/apps/` que tiene WhatsApp Cloud API configurado.**
>
> 🆕 **Cambio 2026:** el wizard ya **no pide "App Type" (Business/Consumer/Gaming)**. Ahora elegís **use cases** y conectás un **Business Portfolio**. El equivalente funcional a "app tipo Business" de la versión vieja de este doc es: **seleccionar el use case de WhatsApp/mensajería + conectar el portfolio "Hubara" verificado**. Ya no hay riesgo de "elegí el tipo equivocado y hay que borrar la app".

### 2.1 Entrar al developer dashboard

- URL: `https://developers.facebook.com/apps/`
- Logueate con tu cuenta personal de Facebook (la misma de Fase 1).
- Botón verde **"Crear app" / "Create App"** (arriba a la derecha, o centrado si no tenés apps). Te lleva a `developers.facebook.com/apps/creation/`.

### 2.2 Wizard "Create App" (5 pasos, 2026)

El wizard tiene esta secuencia (los nombres exactos de botón pueden variar levemente por el idioma/rollout, pero el orden es estable):

1. **Detalles de la app (App details):**
   - **Nombre / App name:** `Hubara WhatsApp` (visible en modals de App Review, no para clientes).
   - **Email de contacto / App contact email:** `tu-nombre@hubara.com.co`.
   - Click **"Siguiente" / "Next"**.

2. **Casos de uso (Use cases):**
   - Seleccioná el use case relacionado con **WhatsApp / mensajería de negocios** (aparece como algo tipo _"Manage messaging and conversations for WhatsApp"_ / _"Otro" (Other)_ según rollout). **Los use cases incompatibles aparecen en gris** y podés agregar más después.
   - ✅ **Si dudás, elegí `Otro` (Other)**: te deja el máximo de flexibilidad para habilitar `whatsapp_*` **y** `catalog_management` en Fase 9 (el catálogo no siempre viene incluido en el use case prefijado de WhatsApp). Con "Other", la app entra en el flujo genérico de permisos por App Review, que es lo que necesitamos.
   - Click **"Siguiente" / "Next"**.

3. **Negocio (Business):**
   - Opciones: un **portfolio verificado**, un **portfolio no verificado**, "No quiero conectar un portfolio todavía", o "Crear un portfolio".
   - Seleccioná el portfolio **"Hubara"** (Fase 1). ⚠️ **Conectá el portfolio ahora** — es lo que reemplaza al viejo "App Type: Business" y habilita los scopes de negocio. Si no aparece "Hubara", refrescá; si sigue sin aparecer, volvé a Fase 1.
   - Click **"Siguiente" / "Next"**.

4. **Requisitos (Requirements):**
   - Meta muestra los requisitos (ej. qué necesita App Review para ciertos permisos). Solo revisá y seguí.

5. **Resumen (Overview):**
   - Revisá nombre, use cases, portfolio conectado y requisitos → click **"Ir al panel" / "Go to dashboard"**.
   - Puede pedirte tu password de Facebook por seguridad.

### 2.3 Anotar el App ID + App Secret

- URL del dashboard de la app: `https://developers.facebook.com/apps/<APP_ID>/dashboard/`
- El `<APP_ID>` numérico (~15 dígitos) es visible en la URL Y en **"Configuración de la app → Básica" (App settings → Basic)**.

| Anotar |
|---|
| `META_APP_ID = _______________________` |
| `META_APP_SECRET = _______________________` (en "App settings → Basic" → "App secret" → "Show", pedirá tu password) |

> El `App Secret` no se usa en este v2 (solo si en el futuro se hace `appsecret_proof` HMAC validation), pero anotalo igual en password manager.

---

## Fase 3 — Configurar WhatsApp Cloud API + número
<a id="fase-3"></a>

> **Saltá esta fase si ya tenés en el sidebar de la app un producto "WhatsApp" con un "Test phone number" y (para prod) un número real en estado `Connected`.**
>
> 🆕 **Cambio 2026 — el número real es API-only para registrar.** Agregar y **verificar la propiedad** de un número productivo se hace en **WhatsApp Manager**; pero **registrarlo para Cloud API** (dejarlo `connected`) se hace con una llamada API (`POST /<PHONE_NUMBER_ID>/register`). No se puede registrar desde WhatsApp Manager ni desde el App Dashboard. El **test number** para dev sigue igual y no necesita nada de esto.

### 3.1 Agregar el producto WhatsApp a la app

- En el dashboard de la app, sidebar izquierdo → buscá la sección **"Productos" (Products)** (o **"+ Agregar producto" / "+ Add Product"** si no hay nada).
- Encontrá **"WhatsApp"** → click **"Configurar" / "Set up"**.
- Te lleva a la consola **"WhatsApp → API Setup"** (a veces "Introducción/Getting Started").

### 3.2 Test phone number (para dev, gratis, inmediato)

Meta da por default un **"Test phone number"** (`+1 555 0xxx xxxx`) para development:

- En `WhatsApp → API Setup` (sidebar):
  - **From (remitente):** dejá seleccionado el test number.
  - **To (destinatario):** click **"Administrar lista de números" / "Manage phone number list"** → agregá tu propio WhatsApp como destinatario de prueba (máx 5 en development mode).
  - Te llega un código a tu WhatsApp → ingresalo.
- **Anotá el Phone Number ID** del test number (sección "Send and receive messages"):

| Anotar |
|---|
| `WHATSAPP_PHONE_NUMBER_ID_TEST = _______________________` (numérico ~15 dígitos) |

### 3.3 Temporary Access Token (solo para validar la conexión ahora)

- Misma página `API Setup` → sección **"Token de acceso temporal" / "Temporary access token"** → **"Generar" / "Generate"**.
- Token de 24h. **NO en producción** — solo para probar el `curl` de ejemplo de esta pantalla. Después lo descartás; el permanente lo generás en Fase 11.

### 3.4 Smoke test con el test number

Pegá el `curl` que Meta muestra en la página (botón **"Enviar mensaje" / "Send Message"**), con el token temporal. Debe llegarte un template **"Hello World"** a tu WhatsApp.

- Si llega: Cloud API OK. Seguí.
- Si no: tu número tiene que estar en "Manage phone number list" y el template `hello_world` en estado `Approved`.

### 3.5 (Producción) Agregar tu número real — flujo 2026

Para clientes reales, Hubara necesita un número propio (no el test). El flujo cambió: **agregar/verificar en WhatsApp Manager**, luego **registrar por API** (lo hace el dev).

**Paso A — Agregar + verificar propiedad (lo hacés vos, en WhatsApp Manager):**

- Entrá a **WhatsApp Manager**: `https://business.facebook.com/wa/manage/phone-numbers/?business_id=<BUSINESS_ID>` (o Business Suite → All tools → **WhatsApp Manager** → **Account tools → Phone numbers**).
- Botón **"Agregar número de teléfono" / "Add phone number"** → modal:
  - **Nombre visible / Display name:** "Hubara" (lo que ven los clientes; Meta lo revisa contra sus reglas de nombre).
  - **Categoría / Category:** `Shopping & Retail`.
  - **Número:** el productivo Hubara (debe poder recibir SMS o llamada).
- Verificación: Meta llama o manda SMS → ingresá el código. Esto **verifica la propiedad**, pero todavía NO deja el número usable por Cloud API.

**Paso B — Registrar para Cloud API (lo corre el dev, API-only):**

- El dev hace un `POST` al endpoint `register` con un **PIN de 6 dígitos** (two-step verification):

```bash
curl -X POST \
  "https://graph.facebook.com/v23.0/<PHONE_NUMBER_ID>/register" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{ "messaging_product": "whatsapp", "pin": "<PIN_6_DIGITOS>" }'
```

- Si el número ya tenía two-step verification activo, el `pin` es ese PIN existente. Si no, el que mandes queda como PIN del número (anotalo en el password manager).
- El número pasa a estado **`Connected`** (5–15 min). Solo con `connected` manda/recibe por la API.

| Anotar (cuando tengas el número real) |
|---|
| `WHATSAPP_PHONE_NUMBER_ID = _______________________` (el productivo, no el test) |
| `WHATSAPP_2FA_PIN = ______` (el PIN de 6 dígitos — password manager, NO en el repo) |

> **Embedded Signup:** existe un flujo "Embedded Signup" que empaqueta agregar+verificar+registrar en un popup. Está pensado para **BSPs/terceros** que onboardean clientes ajenos. Para single-tenant (nosotros administramos nuestro propio número) el flujo WhatsApp Manager + `register` de arriba es más directo. Cuando pasemos a multi-tenant (`agents_admin`), evaluamos Embedded Signup.
>
> **Nota:** podés seguir con TODAS las fases 4–18 usando el **test number** primero. El número real lo conectás cuando Hubara esté listo para clientes reales — el sistema funciona idéntico.

---

## Fase 4 — Confirmar App ↔ Business Portfolio
<a id="fase-4"></a>

> Si en Fase 2.2 paso 3 conectaste el portfolio "Hubara", esto ya está. Verificá igual.

### 4.1 Confirmar que la app aparece en el Business

- Ruta: `Business Settings → Cuentas (Accounts) → Apps`. Link directo: `https://business.facebook.com/settings/apps`.
- En la lista debe aparecer **"Hubara WhatsApp"** con su `App ID`.

### 4.2 Si NO aparece — claim manual

- Click **"Agregar" / "Add"** (arriba derecha) → **"Conectar un App ID existente" / "Connect an existing App ID"**.
- Pegá el `META_APP_ID` de Fase 2.3 → **"Agregar app" / "Add app"** → `Confirmar`.

---

## Fase 5 — Business Verification (gate más largo: 1–5 días)
<a id="fase-5"></a>

> **CRÍTICO:** Sin Business Verification aprobada, ciertos scopes (incluyendo `catalog_management`) quedan limitados. Para producción real es bloqueante. **Iniciá HOY.**

### 5.1 Confirmar estado actual

- Ruta 2026: `Business Settings → **Centro de seguridad (Security Center)**`. Link directo: `https://business.facebook.com/settings/security`.
- Estados:
  - **Verified (badge verde):** listo, saltar a Fase 6.
  - **Not verified / Not started:** ir a 5.2.
  - **Pending / In review:** esperar (1–5 días). Podés seguir con Fases 6–8 mientras tanto.

### 5.2 Iniciar verificación

- Mismo lugar → botón **"Iniciar verificación" / "Start verification"**.
- **Documentos para Colombia** (todos con menos de 90 días desde emisión):
  1. **Certificado de Cámara de Comercio** (existencia + representación legal). Doc primario.
  2. **RUT vigente** emitido por la DIAN (muestra NIT con dígito verificación).
  3. **Comprobante de domicilio comercial** (servicios públicos o estado de cuenta bancario empresarial, ≤12 meses).
- **Datos a ingresar** (EXACTO al RUT/Cámara, sin abreviaturas):
  - Legal business name (con "S.A.S." / "Ltda." si aplica).
  - Dirección (sin "Cll", usar "Calle").
  - Teléfono fijo o celular (Meta puede llamar).
  - Sitio web: `hubara.com.co`. Con email corporativo accesible (`@hubara.com.co`).
  - Tax ID: `900.XXX.XXX-X` (NIT con dígito verificación).
- **Elegir "Business verification" (no solo "Domain verification").** Para Commerce hace falta full.
- Subir los 3 docs en PDF (≤25 MB cada uno).
- Click **"Enviar" / "Submit"**.
- **Email de aprobación/rechazo:** 1–5 días hábiles. Si rechazan, te dicen qué doc no cuadró.

---

## Fase 6 — Verificar WABA + Phone Number ID
<a id="fase-6"></a>

### 6.1 Confirmar WABA bajo el Business Portfolio

- Ruta: `Business Settings → Cuentas (Accounts) → **Cuentas de WhatsApp (WhatsApp accounts)**`. Link directo: `https://business.facebook.com/settings/whatsapp-business-accounts`.
- Click el WABA de Hubara → panel derecho muestra `Phone numbers` con el número activo + `Phone Number ID`.

| Anotar |
|---|
| `WABA_ID = _______________________` (~15 dígitos) |
| `WHATSAPP_PHONE_NUMBER_ID = _______________________` (productivo, no el test) |

### 6.2 Si el WABA aparece en otro Business

- Hay que migrarlo. En el Business correcto: `Business Settings → WhatsApp accounts → Agregar (Add) → Solicitar acceso (Request Access)`.
- Trámite Meta-mediated (1–3 días), pero es raro: el WABA usualmente queda donde lo creaste.

---

## Fase 7 — Crear catálogo en Commerce Manager
<a id="fase-7"></a>

### 7.1 Entrar a Commerce Manager

- URL: `https://business.facebook.com/commerce` (redirige a `commerce.facebook.com`).
- Si nunca hubo catálogo en este Business, vas a ver bienvenida con botón azul **"Agregar catálogo" / "Add catalog"** (o "Get started").

### 7.2 Crear el catálogo

- Click **"Agregar catálogo" / "Add catalog"** → modal "Crear catálogo":
  - **Nombre / Catalog name:** `Hubara Velas Aromáticas` (identificable; UI admin, no chat).
  - **Tipo de catálogo / Vertical:** **`E-commerce`** ⚠️ **CRÍTICO**.
    - NO Retail, NO otros. Solo E-commerce funciona 100% con WhatsApp catalog messages (`interactive.product`, `interactive.product_list`). Si elegís mal, no se cambia — hay que crear catálogo nuevo.
  - **Dueño / Catalog owner:** Business Portfolio de Hubara (pre-seleccionado). **Debe ser el mismo portfolio que tiene el WABA.**
- Click **"Crear" / "Create"**.
- URL final: `https://business.facebook.com/commerce/<CATALOG_ID>/home` → ese número es el ID.

| Anotar |
|---|
| `WHATSAPP_CATALOG_ID = _______________________` (~16 dígitos) |

### 7.3 Asignar admin del catálogo

- En el catálogo → engranaje (Settings) arriba derecha → tab `Personas (People)` / `Permisos (Permissions)`.
- Verificá que figurás como `Admin`.
- El System User (Fase 10) lo agregás después.

---

## Fase 8 — Linkear catálogo al WABA
<a id="fase-8"></a>

### 8.1 Abrir el WABA en WhatsApp Manager

- URL: `https://business.facebook.com/latest/whatsapp_manager/overview?waba_id=<WABA_ID>`
- O: Business Suite → **All tools → WhatsApp Manager** → seleccionar el WABA.
- Nav izquierdo del WhatsApp Manager → **`Account tools` → `Catalog`**.

### 8.2 Conectar el catálogo

- Si nunca se linkeó nada: "No catalog connected" con CTA azul **"Elegir un catálogo" / "Choose a catalog"**.
- Dropdown → seleccioná "Hubara Velas Aromáticas" → **"Conectar catálogo" / "Connect catalog"**.
- **Restricción:** un WABA solo puede tener **UN** catálogo activo. Para separar inventarios (Hubara Belleza vs Hubara Hogar) hace falta otro WABA.

### 8.3 Activar visibilidad pública (opcional pero recomendado)

- Misma sección Catalog → toggle **"Mostrar catálogo en WhatsApp" / "Show catalog on WhatsApp"** → **ON**.
- Hace que el botón "Ver catálogo" aparezca en el perfil del negocio dentro del chat. Sin costo. Mejora discoverability.

### 8.4 Validar el linking (opcional, lo corre el dev)

```bash
curl -s "https://graph.facebook.com/v23.0/<WABA_ID>/product_catalogs?access_token=<TOKEN>"
```

Tiene que devolver un array con `{ "id": "<WHATSAPP_CATALOG_ID>", "name": "Hubara Velas Aromáticas" }`. Si devuelve array vacío, repetir 8.2.

---

## Fase 9 — Habilitar `catalog_management` en la App (CRÍTICO)
<a id="fase-9"></a>

> 🔑 **Este es el paso que más se saltea** y bloquea el push productivo con error `(#100) Missing Permission` aunque el token tenga todo lo demás OK.
>
> **El checkbox `catalog_management` NO aparece en el modal de generación de token a menos que el permiso esté declarado en la App.** Por eso aunque generes el token con todos los checkboxes ticked, sale incompleto.

### 9.1 Entrar al App Dashboard

- URL: `https://developers.facebook.com/apps/<APP_ID>/dashboard/`
- Logueate con tu cuenta personal (admin de la app).

### 9.2 Ir a "App Review → Permissions and Features"

- Sidebar izquierdo del App Dashboard → **"Revisión de la app" (App Review)** → **"Permisos y funciones" (Permissions and Features)**.
- URL directa: `https://developers.facebook.com/apps/<APP_ID>/app-review/permissions/`

### 9.3 Buscar `catalog_management`

- Esta página lista TODOS los permisos disponibles. Buscador arriba.
- Tipeá: `catalog_management`
- Aparece una fila con:
  - **Permission name:** `catalog_management`
  - **Description:** "Provides the ability to perform CRUD operations on a Business' product catalog…"
  - **Access level:** `Standard Access` (por default) con botón **"Solicitar acceso avanzado" / "Request Advanced Access"** a la derecha.

### 9.4 Activar el permiso — dos niveles (2026)

Meta tiene **dos niveles de acceso** por permiso: **Standard Access** (funcionalidad básica, sobre assets que vos poseés/administrás) y **Advanced Access** (a escala, sobre assets de terceros → requiere App Review con screencast).

**Para Hubara single-tenant, Standard Access alcanza** — el System User opera sobre NUESTRO propio catálogo/WABA, no sobre los de terceros. **NO necesitás el screencast de App Review.**

- **Caso A — la fila ya muestra "Standard Access" activo:** listo, saltá a Fase 10.
- **Caso B — querés confirmarlo explícitamente:** si hay un botón tipo "Get Advanced Access", **NO lo necesitás** para uso propio; con Standard Access el token del System User (Fase 11) puede operar sobre tus assets asignados. El screencast de App Review solo aplica si en el futuro Hubara se vuelve proveedor para otras empresas (multi-tenant real / BSP).

> ⚠️ Ojo con el wording: Meta a veces empuja "Request Advanced Access" como si fuera obligatorio. Para operar tu propio catálogo con un System User Admin **no lo es**. Lo que importa es que el permiso figure disponible (Standard) y que el catálogo esté asignado al System User (Fase 10).

### 9.5 Verificar permisos finales de la app

Mientras estás en la página, confirmá que estos 4 figuran (Standard Access alcanza para todos):

- [x] `whatsapp_business_messaging`
- [x] `whatsapp_business_management`
- [x] `business_management`
- [x] **`catalog_management`** ← el nuevo

> Si alguno no aparece disponible, revisá que la app tenga el use case correcto (Fase 2.2 — con "Other" quedan todos habilitables) y que el portfolio esté conectado (Fase 4).

### 9.6 Refrescar para propagar

- Cmd+Shift+R (refresh forzado).
- **Esperá 2 minutos** antes de Fase 11 — Meta tarda en propagar el permiso al modal del token. (Fase 10 podés hacerla ya.)

---

## Fase 10 — Crear System User + asignar todos los assets
<a id="fase-10"></a>

> El System User es la "cuenta de servicio" que genera el token permanente. Sin asset assignment, el token sale sin permiso a operar sobre los assets concretos.
>
> 🆕 **Ubicación 2026:** `Business Settings → **Usuarios (Users) → Usuarios del sistema (System users)**`.

### 10.1 Crear el System User

- Ruta: `Business Settings → Users → System users`. Link directo: `https://business.facebook.com/settings/system-users`.
- Click **"Agregar" / "Add"** → modal "Crear usuario del sistema":
  - **Nombre / Name:** `hubara-whatsapp-prod` (descriptivo).
  - **Rol / Role:** **`Admin`** ⚠️ — NO `Employee`. Solo Admins generan tokens con scopes amplios.
- Click `Crear usuario del sistema` → te pide tu password.

### 10.2 Asignar assets (los 3 que necesitamos)

Con el system user creado, click sobre él → botón **"Asignar activos" / "Add Assets"** (azul, arriba derecha del panel).

Hay que hacerlo **3 veces** (una por tipo de asset):

#### Tab 1 — Apps

- Tab **"Apps"** → seleccioná **"Hubara WhatsApp"**.
- Toggle a la derecha → **`Develop`** o **`Manage`** ("Develop" es más conservador).
- **"Guardar cambios" / "Save Changes"**.

#### Tab 2 — WhatsApp accounts

- **"Add Assets"** de nuevo → tab **"WhatsApp accounts"** → seleccioná el WABA de Hubara.
- Toggle → **`Full control`** (necesario para tokens con `whatsapp_business_*`).
- **"Save Changes"**.

#### Tab 3 — Catalogs ⭐ (el que la gente olvida)

- **"Add Assets"** de nuevo → tab **"Catalogs"** → seleccioná **"Hubara Velas Aromáticas"**.
- Toggle → **"Manage catalog"** → **ON**.
- **"Save Changes"**.

### 10.3 Verificar Assigned Assets

En el panel del system user, sección **"Activos asignados" / "Assigned Assets"**. Debe aparecer:

- ✅ Apps: Hubara WhatsApp — `Develop` o `Manage`
- ✅ WhatsApp accounts: WABA Hubara — `Full control`
- ✅ Catalogs: Hubara Velas Aromáticas — `Manage catalog`

Si falta alguno, repetir 10.2 con el tab correspondiente.

---

## Fase 11 — Generar token permanente con los 4 scopes
<a id="fase-11"></a>

> Ahora SÍ aparece el checkbox `catalog_management` en el modal (porque la app lo tiene en Fase 9 y el system user tiene el catálogo asignado en Fase 10).

### 11.1 Generate New Token

- En el panel del system user → **"Generar nuevo token" / "Generate New Token"** (abajo del panel de assets).

### 11.2 Configurar el token

Modal "Generate New Token":

- **App:** dropdown → **"Hubara WhatsApp"**.
- **Expiración / Token expiration:** **`Never`** ⚠️ — evita la rotación de 60 días. Es lo que lo hace permanente.

### 11.3 Tickear los checkboxes

Lista de **"Available Permissions"** — al menos estos 4:

- [x] `whatsapp_business_messaging`
- [x] `whatsapp_business_management`
- [x] `business_management`
- [x] **`catalog_management`** ← **EL NUEVO. Confirmá que está tickeado.**
- [ ] otros checkboxes — dejalos sin tickear (least privilege).

### 11.4 Generate y copiar

- Click **"Generar token" / "Generate Token"**.
- Meta lo muestra **UNA SOLA VEZ**. **Copialo YA al password manager** (1Password, Bitwarden).
- Empieza con `EAA...`, ~200 caracteres.

| Anotar (password manager, NUNCA en archivos del repo) |
|---|
| `META_SYSTEM_USER_TOKEN = EAA...` |

> **¿Y si lo perdés?** Volvé a esta pantalla → `Generate New Token` → repetir 11.2–11.4. Los tokens viejos siguen vivos hasta que los revoques con "Revocar / Revoke" — usalo si querés rotar de verdad.

### 11.5 Si `catalog_management` SIGUE sin aparecer en el checklist

Causas residuales:

1. **El catálogo no se asignó al System User** (Fase 10.2 Tab 3) — verificar.
2. **El permiso no se propagó** — esperá 5 min, Cmd+Shift+R, reabrí el modal.
3. **Tu rol en el Business Portfolio es Employee, no Admin** — pedile al super-admin que te haga Admin (`Business Settings → Users → People → tu nombre → Editar → Rol: Admin`).
4. **La app no tiene el use case/portfolio correcto** — Fase 2.2 (usar "Other" + conectar portfolio "Hubara"). Ya **no** existe un "App Type" que revisar; lo que importa es use case + portfolio + permiso declarado en Fase 9.
5. **Business Verification "Pending"** — algunos scopes están limitados en review. Si el badge dice `Pending`, esperá.

---

## Fase 12 — Verificar scopes con `debug_token`
<a id="fase-12"></a>

> Antes de cargar el token al .env, **siempre validá** que tiene los 4 scopes. Te ahorra 1h de "Missing Permission".

### 12.1 Correr debug_token

Desde cualquier terminal:

```bash
curl -s "https://graph.facebook.com/v23.0/debug_token?input_token=<TU_TOKEN_NUEVO>&access_token=<TU_TOKEN_NUEVO>" | python3 -m json.tool
```

### 12.2 Validar que `catalog_management` esté en `scopes`

Output esperado (las 4 líneas ✓):

```json
{
    "data": {
        "app_id": "<APP_ID>",
        "type": "SYSTEM_USER",
        "application": "Hubara WhatsApp",
        "expires_at": 0,                               ✓ 0 = never
        "is_valid": true,                              ✓
        "scopes": [
            "business_management",                     ✓
            "catalog_management",                      ✓ ← el crítico
            "whatsapp_business_management",            ✓
            "whatsapp_business_messaging",             ✓
            "public_profile"
        ],
        "granular_scopes": [...],
        "user_id": "<SYSTEM_USER_ID>"
    }
}
```

Si **`catalog_management` NO está en `scopes`** → volvé a Fase 9 (permiso no está en la app) o a Fase 10.2 Tab 3 (catálogo no asignado al system user). El problema NO está en el token.

### 12.3 (Opcional) Validar acceso al catálogo concreto

```bash
curl -s "https://graph.facebook.com/v23.0/<CATALOG_ID>?fields=id,name,product_count,owner_business&access_token=<TOKEN>" | python3 -m json.tool
```

Output esperado:

```json
{
    "id": "<CATALOG_ID>",
    "name": "Hubara Velas Aromáticas",
    "product_count": 0,
    "owner_business": { "id": "<BUSINESS_ID>", "name": "Hubara" }
}
```

Si devuelve `(#100) Missing Permission` → el catálogo NO está asignado al System User (Fase 10.2 Tab 3).

---

## Fase 13 — WhatsApp Flow de datos de envío (formulario nativo)
<a id="fase-13"></a>

> **Por qué es OPCIONAL pero recomendada**: sin el Flow, el agente recolecta los 5 datos (ciudad, barrio, dirección, teléfono, pago) **conversacionalmente por texto**. Funciona, pero el cliente tipea 5 veces. Con el Flow publicado ve un **formulario nativo** y completa en 1 botón. Cero código del dev — el wiring (`send_flow`, `nfm_reply` parser, `_format_flow_response`) ya está en el repo.

> **Cuándo hacerlo**: cualquier momento. No bloquea Fase 14+. Se activa seteando `META_FLOW_ID_SHIPPING` en `.env` y recreando el worker `chats-sales`.

### 13.1 Abrir Flow Builder en Meta

- URL: `https://business.facebook.com/wa/manage/flows/?business_id=<BUSINESS_ID>`
- Si no aparece "Flows" en el sidebar de WhatsApp Manager: confirmá rol **Admin** (Fase 1.4) y app vinculada al Business (Fase 4).

### 13.2 Crear un Flow nuevo

1. Click **`Create flow`**.
2. **Name**: `Hubara — Datos de envío v2`
3. **Categories**: marcá **`SIGN_UP`** (encaja mejor que `SURVEY`).
4. **Template**: **`Start from scratch`**.
5. **Endpoint URI / "Data endpoint"**:
   - **NO completar nada — dejar vacío.** Si Meta exige un valor o muestra un toggle **`Endpoint-less Flow`**, activá ese.
   - **¿Por qué sin endpoint?** Este Flow es **estático puro**: recibe datos iniciales (`payment_options`, `order_total_cop`, `items_summary`) al enviarse, y al apretar "Confirmar" emite un `nfm_reply` con todos los campos. No hay llamadas a backend mientras el cliente llena el form. Por eso el JSON v1 **NO** incluye `data_api_version`.
6. Click **`Create`**.

> Si la UI fuerza un endpoint y no acepta vacío ni tiene toggle, poné una URL placeholder válida (ej. `https://hubara.com.co/whatsapp-flow-noop`). Meta NO la llama porque el Flow termina con `complete`, no con `data_exchange`. Pero es feo — buscá el toggle primero.

### 13.3 Pegar el JSON canónico

1. Pestaña **`Edit JSON`** (arriba derecha).
2. Borrá el JSON template autogenerado.
3. Pegá el contenido de **`hubara_agency/docs/whatsapp_flows/shipping_v2.json`**.
4. Click **`Save`**.

> Warning "comments not allowed" sobre `_comment_` → ignoralo. Warning "missing `data_api_version`" → **NO lo agregues** (convertiría el Flow en dynamic y forzaría endpoint; la ausencia es intencional).

### 13.4 Validar y publicar

1. **`Validate`** → verde (`Flow is valid`). Si error: comillas curvas al pegar, `version` = `"7.2"`, y **NO** haya `data_api_version`.
2. **`Preview`** → completá los 7 campos (cédula es opcional), mirá el `nfm_reply` esperado: `{city, neighborhood, address, phone, receiver_name, national_id, payment_method, order_total_cop, items_summary}`.
3. **`Publish`** → 1–3 min (`DRAFT` → `PUBLISHED`).
4. Copiá el **`Flow ID`** (~16 dígitos, en el header de la pestaña).

### 13.5 Configurar el `flow_id` en el backend

`.env` del backend (local + prod):

```bash
META_FLOW_ID_SHIPPING=1234567890123456
```

Recreá el worker `chats-sales`:

```bash
cd hubara_agency
docker compose -f docker-compose.local.yml up -d --force-recreate hubara-worker-chats-sales
```

Smoke test desde el chat: en la etapa de envío el agente abre el formulario nativo (botón "Completar datos" → modal 7 campos, cédula opcional), NO texto plano.

Si seguís viendo texto plano, verificá que el worker tomó la variable:
```bash
docker exec local-hubara-worker-chats-sales printenv META_FLOW_ID_SHIPPING
```

### 13.6 Cuando el cliente complete el Flow

Atrás de escena (el dev ya lo tiene wireado):

1. Cliente apreta **`Confirmar datos`**.
2. Meta manda webhook con `interactive.nfm_reply.response_json` (los 7 campos).
3. El parser (`src/plugins/chats/agent/sales/translate.py`) lo pasa a texto para el LLM.
4. El LLM cierra con `verify_order_for_checkout` → `present_order_confirmation`.

### 13.7 Versionado / cambios futuros

1. Editá `docs/whatsapp_flows/shipping_v2.json` local (o creá `shipping_v3.json`).
2. Subilo al Flow Builder y republicá — o mejor: renombrá la entry en `infra/whatsapp-provisioning/definitions/flows.json` (el CLI crea el flow nuevo, lo publica y resuelve el `flow_id` al SSM key).
3. En el MISMO flow, cada Publish genera **nueva versión** y el `flow_id` **NO cambia**. Un flow NUEVO (rename) sí cambia el id → actualizá `META_FLOW_ID_SHIPPING` en SSM y recreá el worker.
4. Rollback rápido: apuntá el env var al flow_id anterior (los flows viejos siguen publicados en el WABA).

---

## Fase 14 — Smoke test manual de 1 producto
<a id="fase-14"></a>

> Antes de la sincronización automática desde Medusa, validá el linking con un producto a mano. Separa "problema de config Meta" de "problema de sync Medusa→Meta".

### 14.1 Crear un producto manual

- URL: `https://business.facebook.com/commerce/<CATALOG_ID>/products`
- Botón **`Agregar artículos` / `Add items`** (arriba derecha) → **`Manual`** → `Next`.

### 14.2 Campos mínimos (requeridos por Meta)

| Campo | Valor de prueba | Notas |
|---|---|---|
| Image | JPG/PNG, ≥500×500 px, ≤8 MB | **NO .webp** para upload manual. JPG/PNG. |
| Title | "Vela aromática Lavanda — 250g" | Max 150 chars; visible en chat. |
| Description | "Fragancia relajante, notas florales suaves" | **Sin health claims** (Fase 0). |
| Price | `45000` + Currency `COP` | COP nativo soportado. **No USD.** |
| Availability | `in stock` | |
| Condition | `new` | |
| Brand | `Hubara` | |
| Content ID / Retailer ID | `HUB-TEST-001` | **CRÍTICO** — es el `product_retailer_id` del dev. Valor estable, no auto-generado. |
| Link | `https://hubara.com.co/productos/vela-lavanda-250g` | Puede ser placeholder. |

### 14.3 Confirmar que pasa a "Active"

- Click `Add item` → "Pending review".
- Refrescá cada 5 min → "Active" en 5–30 min.
- Si "Rejected" → click el producto → panel con `reason code`. Frecuentes: imagen <500px, health claims, link roto.

---

## Fase 15 — Smoke test end-to-end con el dev
<a id="fase-15"></a>

Cuando tengas los datos a entregar (Fase 17), pasáselos al dev. Corre:

### 15.1 Test `interactive.product` (1 producto)

```bash
curl -X POST \
  https://graph.facebook.com/v23.0/<PHONE_NUMBER_ID>/messages \
  -H "Authorization: Bearer <META_SYSTEM_USER_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "messaging_product": "whatsapp",
    "to": "<TU_NUMERO_WHATSAPP_E164>",
    "type": "interactive",
    "interactive": {
      "type": "product",
      "body": { "text": "Mirá esta vela" },
      "footer": { "text": "Hubara" },
      "action": {
        "catalog_id": "<WHATSAPP_CATALOG_ID>",
        "product_retailer_id": "HUB-TEST-001"
      }
    }
  }'
```

**Lo que tiene que pasar:**

- Tu WhatsApp recibe card con imagen + nombre + precio + botón **"View"**.
- "View" → detail con "Add to cart".
- Errores comunes:
  - `Error 100 — invalid catalog_id` → catálogo no linkeado al WABA del Phone Number ID usado (Fase 8).
  - `Error 100 — Missing Permission` → token sin `catalog_management` (Fase 9–11).
  - Card sin imagen → producto con .webp; el backend normaliza los de Medusa, pero el manual de Fase 14 NO — re-subí como JPG.

### 15.2 Test `interactive.product_list` (Multi-Product Message)

Si hay 2+ productos en Active:

```bash
curl -X POST https://graph.facebook.com/v23.0/<PHONE_NUMBER_ID>/messages \
  -H "Authorization: Bearer <META_SYSTEM_USER_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "messaging_product": "whatsapp",
    "to": "<TU_NUMERO_WHATSAPP_E164>",
    "type": "interactive",
    "interactive": {
      "type": "product_list",
      "header": { "type": "text", "text": "Velas más vendidas" },
      "body": { "text": "Tocá una vela para ver detalle" },
      "footer": { "text": "Hubara" },
      "action": {
        "catalog_id": "<WHATSAPP_CATALOG_ID>",
        "sections": [
          {
            "title": "Aromáticas",
            "product_items": [
              { "product_retailer_id": "HUB-TEST-001" },
              { "product_retailer_id": "HUB-TEST-002" }
            ]
          }
        ]
      }
    }
  }'
```

- Recibís header + carousel.
- "View items" → modal con thumbnails → selección → cart.
- Botón **"Send"** → order summary al chat.

### 15.3 Verificar order webhook (dev)

Al completar un cart desde el MPM y tocar "Send", el backend recibe un webhook tipo `order`. El dev valida que llegó (logs del API).

---

## Fase 16 — Catalog Quality Dashboard
<a id="fase-16"></a>

### 16.1 Ver productos rechazados / con issues

- URL: `https://business.facebook.com/commerce/<CATALOG_ID>/diagnostics`
- **Errors (críticos):** items que no aparecen en ningún canal. Cada uno con un `Fix`.
- **Warnings (no críticos):** aparecen con quality reducida (imagen baja, falta `google_product_category`).

### 16.2 Quality score global

- URL: `https://business.facebook.com/commerce/<CATALOG_ID>/quality`
- Bajan el score: campos opcionales vacíos (gtin, mpn, brand inconsistente); imágenes <500px o con marca de agua; descripciones <50 chars; mismatch `link` vs página (404/redirect); stale availability.
- **Objetivo v1:** >85%.

---

## Fase 17 — Entregables finales al dev
<a id="fase-17"></a>

Cuando completes Fases 1–14, mandame **por canal seguro** (1Password share, Bitwarden Send, Signal — **NO** Slack público, **NO** email plano):

```
BUSINESS_ID=<de 1.4>
META_APP_ID=<de 2.3>
WABA_ID=<de 6.1>
WHATSAPP_PHONE_NUMBER_ID=<de 6.1>
WHATSAPP_CATALOG_ID=<de 7.2>
META_SYSTEM_USER_TOKEN=<de 11.4 — el secreto>
META_FLOW_ID_SHIPPING=<de 13.5 — flow_id publicado>
```

El dev los carga en:
- Local dev: `hubara_agency/.env` (en `.gitignore`, NUNCA committeado).
- Producción: secret store (SSM Parameter Store, ver runbooks de infra).

---

## Fase 18 — Consideraciones LATAM Colombia
<a id="fase-18"></a>

### 18.1 Currency

- WhatsApp Commerce acepta **COP nativo**.
- Formato `price`: entero **sin separadores de miles** + espacio + `COP`. Ej: `"45000 COP"`.
- **No USD** — clientes ven precio en su moneda, sin conversión confusa.

### 18.2 Pagos in-app

- **WhatsApp Pay NO está disponible en Colombia al 2026** (solo Brasil, India, Singapur).
- Flujo: `interactive.product_list` → cliente arma carrito → "Send" → order summary al backend como webhook `order`.
- De ahí Hubara responde con **link de pago externo** (Wompi / PayU / ePayco — a definir).
- Ese link **NO está cubierto en este v2** — work item separado cuando se decida la pasarela.

### 18.3 DIAN / Facturación electrónica

- **Meta NO pide nada de DIAN.** El RUT entra como doc de Business Verification (Fase 5.2), después no hay integración fiscal.
- La facturación electrónica es responsabilidad de Hubara en su backend, **después** del order callback.

### 18.4 Idioma

- `title` y `description` pueden ir en español. El clasificador de policy de Meta entiende español OK.

### 18.5 Business hours

- El catálogo no tiene business hours nativo. Para mostrar horarios en el perfil: `business.facebook.com/wa/manage/profile/` → "Business hours" con timezone `America/Bogota`.

---

## Fase 19 — Categorías PROHIBIDAS por Meta
<a id="fase-19"></a>

> Aplica para Commerce Catalog en cualquier canal Meta (FB, IG, WhatsApp). Si subís uno de éstos, el catálogo entero puede quedar degradado o suspendido.

1. **Adult products** (juguetes sexuales, contenido para adultos).
2. **Alcohol** (WhatsApp más estricto que FB/IG; totalmente prohibido acá).
3. **Animals** y partes/fluidos del cuerpo humano.
4. **Counterfeit goods** (réplicas, "inspired by", knockoffs).
5. **Digital products / intangibles** (NFTs, cursos digitales puros, gift cards de terceros).
6. **Discrimination / hate speech**.
7. **Drugs, drug paraphernalia, kratom, CBD** (CBD prohibido aun donde es legal — **incluye velas con CBD**).
8. **Healthcare items** (medicamentos, agujas, equipos médicos).
9. **Ingestibles & supplements regulados** (vitaminas con claims, pérdida de peso).
10. **Misleading / deceptive practices** ("antes $100, ahora $20" falsos, before/after).
11. **Real money gambling.**
12. **Recalled products.**
13. **Tobacco, e-cigarettes, vaping.**
14. **Unsafe supplements** (steroids, peptides).
15. **Weapons, ammunition, explosives.**
16. **Services** (Meta es "tangible goods only" estricto en WhatsApp).
17. **Vehicles** (usar vertical "Auto" en otro catalog).
18. **Subscriptions** (recurrentes, SaaS).

**Para Hubara (velas aromáticas) OK con tres caveats:**

- **Eliminar health claims** del copy (Sección 0).
- **No CBD/cannabis.**
- **Productos terminados** (vela armada, difusor armado) sí; **materias primas sueltas** (aceite esencial puro) → zona gris, mejor no subir.

---

## Checklist imprimible
<a id="checklist-imprimible"></a>

### Sección 0 — Pre-flight
- [ ] Revisé títulos y descripciones en Medusa: sin health claims
- [ ] Sin productos con CBD / cannabis
- [ ] No hay aceites esenciales puros sueltos

### Fase 1 — Cuenta Meta + Business Portfolio
- [ ] Cuenta personal de Facebook con email `@hubara.com.co` + 2FA activado
- [ ] Business Portfolio "Hubara" creado en Meta Business Suite
- [ ] Business info completo (legal name, address, phone, website, NIT)
- [ ] Confirmado: mi rol en el portfolio es **Admin**
- [ ] Anotado: `BUSINESS_ID = ____________`

### Fase 2 — App de Meta (use-case flow)
- [ ] App creada en `developers.facebook.com/apps/creation/`
- [ ] Use case de WhatsApp/mensajería (o **"Other"**) seleccionado
- [ ] **Business Portfolio "Hubara" conectado** en el wizard ⚠️
- [ ] Anotado: `META_APP_ID = ____________` + `META_APP_SECRET = ____________`

### Fase 3 — WhatsApp Cloud API + número
- [ ] Producto "WhatsApp" agregado en el App Dashboard
- [ ] Test number configurado + mi número en "Manage phone number list"
- [ ] Smoke test "Hello World" recibido OK
- [ ] (Producción) Número real agregado + verificado en **WhatsApp Manager**
- [ ] (Producción) Número **registrado por API** (`/register` con PIN) → estado `Connected`

### Fase 4 — Linking App ↔ Business
- [ ] App "Hubara WhatsApp" visible en `Business Settings → Accounts → Apps`

### Fase 5 — Business Verification
- [ ] Status = **Verified** (badge verde) en Security Center
- [ ] (Si Pending) docs subidos: Cámara de Comercio + RUT + comprobante de domicilio

### Fase 6 — WABA
- [ ] WABA visible en `Business Settings → WhatsApp accounts`
- [ ] Anotado: `WABA_ID = ____________`
- [ ] Anotado: `WHATSAPP_PHONE_NUMBER_ID = ____________` (productivo)

### Fase 7 — Catálogo
- [ ] Catalog name: `Hubara Velas Aromáticas`
- [ ] Vertical: **`E-commerce`** ⚠️
- [ ] Catalog owner: Business Portfolio Hubara (mismo que el WABA)
- [ ] Anotado: `WHATSAPP_CATALOG_ID = ____________`

### Fase 8 — Linking Catalog ↔ WABA
- [ ] Catálogo conectado en `WhatsApp Manager → Account tools → Catalog`
- [ ] Toggle "Show catalog on WhatsApp" en **ON**

### Fase 9 — `catalog_management` en la App ⭐
- [ ] App Review → Permissions: `catalog_management` disponible (Standard Access alcanza)
- [ ] También: `whatsapp_business_messaging`, `whatsapp_business_management`, `business_management`
- [ ] Esperé 2 minutos después de activarlo

### Fase 10 — System User + Assets ⭐
- [ ] System User creado: `hubara-whatsapp-prod` con role **Admin** (en Users → System users)
- [ ] Asset: Apps → Hubara WhatsApp → `Develop`/`Manage`
- [ ] Asset: WhatsApp accounts → WABA → `Full control`
- [ ] Asset: **Catalogs → Hubara Velas Aromáticas → `Manage catalog`** ⚠️

### Fase 11 — Token permanente
- [ ] Token con expiration **`Never`**
- [ ] 4 checkboxes: `whatsapp_business_messaging`, `whatsapp_business_management`, `business_management`, `catalog_management`
- [ ] Token guardado en password manager (`META_SYSTEM_USER_TOKEN`)

### Fase 12 — Verificación token
- [ ] `debug_token` muestra los 4 scopes incluyendo `catalog_management`
- [ ] `GET /<CATALOG_ID>?fields=...` devuelve el catálogo (no 401/403)

### Fase 13 — WhatsApp Flow de envío
- [ ] Flow creado (categoría `SIGN_UP`)
- [ ] JSON pegado desde `hubara_agency/docs/whatsapp_flows/shipping_v1.json`
- [ ] Validación verde
- [ ] Flow `PUBLISHED`
- [ ] `flow_id` en `.env` como `META_FLOW_ID_SHIPPING=...`
- [ ] Worker `chats-sales` recreado

### Fase 14 — Producto manual
- [ ] 1 producto con `Retailer ID = HUB-TEST-001`
- [ ] Producto en "Active"

### Fase 15 — Smoke test E2E
- [ ] Mi número como test recipient
- [ ] `interactive.product` recibido con imagen
- [ ] `interactive.product_list` recibido con carousel
- [ ] **Flow de envío** abre como formulario nativo (no texto plano)
- [ ] Order webhook llegando al backend

### Fase 16 — Quality
- [ ] 0 errors críticos
- [ ] Quality score >85%

### Fase 17 — Entregables al dev
- [ ] Mandé las vars por canal seguro (incluyendo `META_FLOW_ID_SHIPPING`)
- [ ] Dev confirmó carga en `.env` local y prod

### Fase 18 — LATAM
- [ ] Precios en COP (no USD)
- [ ] Pasarela de pago externa: **PENDIENTE**
- [ ] Business hours en `America/Bogota` (opcional)

---

## Anexos
<a id="anexos"></a>

### Anexo A — Env vars en el backend

```bash
# Meta Commerce Catalog (HU-002 Parte B)
META_CATALOG_ID=<de Fase 7.2>
META_SYSTEM_USER_TOKEN=<de Fase 11.4>

# Opcionales (referencia / future multi-tenant)
META_BUSINESS_ID=<de Fase 1.4>
META_WABA_ID=<de Fase 6.1>
# WHATSAPP_PHONE_NUMBER_ID ya existía
```

### Anexo B — Cómo dispara el dev el primer push

Con el worker `catalog_sync` activo, desde el repo root del backend:

```bash
cd hubara_agency
uv run python scripts/trigger_catalog_sync.py
```

Output esperado:

```
Started workflow catalog-sync-on-demand-manual-1747832400
  snapshot_dir: /Users/edgm/.../catalog_workspace

✅ Sync completed
  Snapshot: version: a1b2c3d4 · bytes_written: 124589 · files_written: 47
  Meta push: pushed: True · handle: AScdef123... · creates: 23 · updates: 0 · deletes: 0 · duration: 4.2s
```

Si `pushed: False` y `error: meta_not_configured` → faltan env vars de Meta en `.env`.

### Anexo C — Eventual consistency

- **Items nuevos:** Meta documenta **14–24 horas** hasta que aparezcan como "shoppable" desde el perfil. Los mensajes `interactive.product` funcionan en minutos.
- **Updates** (price, availability): minutos a 1 hora.
- **Implicación:** no programar campañas de lanzamiento "para el mismo día" tras crear el catálogo. Esperar 24h después del primer push.

### Anexo D — Rate limits del catalog

- Initial: ~200 batch calls / hora por catálogo.
- Header `X-Business-Use-Case-Usage` muestra % usado.
- El backend usa **1 batch call por sync run**. No deberías ver rate limits salvo >200 syncs/h.

### Anexo E — `item_type` requerido por Graph API v21+

Para `/items_batch`, Meta exige `item_type`. Para Hubara siempre `PRODUCT_ITEM` (default en `MetaBatchRequest.item_type`). Para catálogos de hoteles/vuelos/vehículos habría que cambiar el DTO.

### Anexo F — Cómo arreglar productos sin imagen

El backend skipea productos sin `thumbnail`/`images[]`:

1. Admin de Medusa (`https://hubara.com.co/app`).
2. `Products` → producto skipped → "Media" → upload thumbnail + 1 imagen.
3. Save → re-disparar el sync (`uv run python scripts/trigger_catalog_sync.py`).

### Anexo G — Versión de Graph API (2026)

- **Última versión: `v25.0`** (Meta la publicó 2026-02-18). Versiones soportadas ~`v21.0`–`v25.0`.
- Deprecaciones 2026: `v18.0` expiró 26-ene-2026, `v19.0` el 21-may-2026, `v20.0` deprecada 24-sep-2026.
- **El backend está pinneado (mayormente `v23.0`).** Los `curl` de este doc usan `v23.0`. Si el dev migra el pin, cambiá el número de versión en las URLs. La migración de versión la maneja el dev — el operador no toca nada.

---

## Troubleshooting
<a id="troubleshooting"></a>

| Síntoma | Causa probable | Cómo arreglar |
|---|---|---|
| `(#100) Missing Permission` en `/items_batch` | Token sin `catalog_management` scope | Fase 9–11. Verificar con Fase 12.1 que aparece en `scopes`. |
| `(#100) The parameter item_type is required` | Backend viejo sin fix `item_type` | Rebuildear el worker (`docker compose up -d --build hubara-worker-catalog-sync`). |
| `(#100) Missing Permission` solo en `/items_batch`, pero `GET /<catalog_id>` funciona | Token tiene `catalog_management` pero el catálogo no está asignado al System User | Fase 10.2 Tab 3 (Catalogs). |
| `catalog_management` no aparece en el modal de Generate Token | El permiso no está disponible en la App / catálogo no asignado | Fase 9.3–9.5 + Fase 10.2 Tab 3. |
| No encuentro "Business Settings" / "System Users" | UI 2026 movió todo a Meta Business Suite | Usar links directos: `business.facebook.com/settings` y `.../settings/system-users`. O Business Suite → All tools → Business Settings. |
| El wizard de app no me deja elegir "App Type: Business" | Flujo 2026 es por use cases, ya no hay app types | Fase 2: elegí use case (o "Other") + conectá el portfolio "Hubara". Eso reemplaza al viejo tipo Business. |
| Número real agregado pero no manda mensajes | Verificaste propiedad pero no registraste para Cloud API | Fase 3.5 Paso B — `POST /<PHONE_NUMBER_ID>/register` con PIN. Estado debe ser `Connected`. |
| Productos "Pending review" >24h | Manual review humana | Esperar 48h. Si sigue, ticket a Commerce Support. |
| Producto rechazado: "policy violation" | Health claim / categoría prohibida | Reescribir copy en Medusa, esperar próximo sync. |
| Imagen no aparece en la card | URL .webp sin normalizar (productos viejos) | Re-sincronizar; el backend normaliza .webp → .jpeg. |
| `interactive.product` → `Error 100 — invalid catalog_id` | Catálogo no linkeado al WABA del número usado | Fase 8.2. `GET /<WABA_ID>/product_catalogs`. |
| Push devuelve 80008 "rate limited" | Demasiados syncs en 1h | Esperar 1h. Reducir frecuencia. |
| Token expira con `Error 190` | Token revocado / system user eliminado | Regenerar (Fase 11). Verificar que el System User existe. |
| `Application does not have permission` (`#10`) | Business Verification pendiente | Esperar aprobación (Fase 5). |
| `Error 190 subcode 463 / token expired` en llamadas viejas | Se usó una versión de Graph API deprecada | Que el dev migre el pin a una versión soportada (Anexo G). |

---

**Última actualización:** 2026-07-09
**Versión del doc:** v2 (reescritura por UI 2026 de Meta)

**Cambios desde v1 (2026-05-22):**
- **Fase 2 reescrita:** creación de app ahora por **use cases** (no "App Type: Business/Consumer/Gaming"). El equivalente es use case de WhatsApp/mensajería (o "Other") + conectar Business Portfolio. Eliminado el warning obsoleto "elegí mal el tipo → borrá la app".
- **Fase 3 reescrita:** número de producción ahora es **agregar/verificar en WhatsApp Manager + registrar por API** (`POST /<PHONE_NUMBER_ID>/register` con PIN de 6 dígitos). Documentado Embedded Signup como opción para multi-tenant futuro.
- **Navegación 2026 actualizada en todo el doc:** puerta de entrada es **Meta Business Suite → All tools → Business Settings**; System Users bajo **Users → System users**; Business Verification en **Security Center**. Links directos preservados como fallback confiable.
- **Fase 9 aclarada:** Standard vs Advanced Access — para single-tenant **Standard Access alcanza**, NO hace falta el screencast de App Review (que solo aplica a proveedores/BSP).
- **Anexo G nuevo:** versión de Graph API — última es `v25.0` (2026-02-18); backend pinneado a `v23.0`; calendario de deprecaciones 2026.
- **Troubleshooting expandido** con las 4 confusiones nuevas de la UI 2026 (Business Settings/System Users movidos, no hay App Type, número sin registrar, versión deprecada).

**Próxima revisión:** cuando Meta publique Graph API v26 o vuelva a mover Commerce Manager / Business Suite (lo que ocurra primero).
