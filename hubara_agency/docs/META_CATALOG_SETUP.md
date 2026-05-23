# Meta Catalog — Setup manual del operador (v1, single-tenant Hubara)

> **Audiencia:** vos (operador admin del negocio Hubara). No es para el dev.
> **Objetivo:** punta a punta — desde "no tengo nada en Meta" hasta "el backend pushea productos a Meta Commerce Catalog y las cards `interactive.product` llegan al cliente con imagen".
> **Tiempo total:** 3–5 horas activas + 1–5 días hábiles esperando Business Verification.
> **Versión Graph API:** v23.0 (backend pinned). Si Meta publica v24+ con cambios, el dev lo migra; vos no tenés que tocar nada.
> **Multi-tenant:** este v1 es single-tenant (un solo Hubara). Cuando onboardeen un segundo cliente, los IDs/tokens migrarán al plugin `agents_admin` (per-tenant config). Por ahora viven como env vars del backend.

---

## Tabla de contenidos

- [Sección 0 — Decisión crítica antes de tocar nada](#sección-0)
- [Fase 1 — Cuenta Meta + Business Portfolio](#fase-1)
- [Fase 2 — Crear la App de Meta (tipo Business)](#fase-2)
- [Fase 3 — Configurar WhatsApp Cloud API en la app](#fase-3)
- [Fase 4 — Linkear la App al Business Portfolio](#fase-4)
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

> **Saltá esta fase si ya tenés `business.facebook.com/settings/info` mostrando "Hubara" como Business.**

### 1.1 Cuenta personal de Facebook

- Meta exige una cuenta personal de Facebook como dueño de cualquier Business Portfolio. Si todavía no tenés una con tu email corporativo (`@hubara.com.co`), crearla en `https://www.facebook.com/r.php` con email `tu-nombre@hubara.com.co`.
- Verificar el email (Meta manda un link de confirmación).
- Activar **2FA** desde `https://www.facebook.com/security/2fac/settings/` — requerido para ser admin de Business.

### 1.2 Crear el Business Portfolio (antes "Business Manager")

- URL: `https://business.facebook.com/overview`
- Si nunca creaste un Business: pantalla de bienvenida con botón azul **"Create Account"**.
- Modal "Create your business portfolio":
  - **Business and account name:** `Hubara` (visible en la UI admin, no en el chat).
  - **Your name:** tu nombre completo (igual al de Facebook).
  - **Business email:** `tu-nombre@hubara.com.co` (Meta lo verifica vía link).
- Click **"Submit"** → te llega email de verificación → click el link → cuenta activa.
- URL final del portfolio: `https://business.facebook.com/settings/info`.

### 1.3 Datos del negocio

- En `Business Settings → Business info` (sidebar izquierda):
  - **Legal Business Name:** exacto al certificado de Cámara de Comercio (con "S.A.S.", "Ltda." si aplica).
  - **Address:** dirección física, sin abreviaturas ("Calle" no "Cll").
  - **Phone:** fijo o celular (Meta puede llamar para verificar).
  - **Website:** `https://hubara.com.co`.
  - **Tax ID:** NIT con dígito verificación (`900.XXX.XXX-X`).
- Click **"Save Changes"**.

| Anotar |
|---|
| `BUSINESS_ID = _______________________` (visible arriba, ~15 dígitos) |

---

## Fase 2 — Crear la App de Meta (tipo Business)
<a id="fase-2"></a>

> **Saltá esta fase si ya tenés una app en `https://developers.facebook.com/apps/` que tiene WhatsApp Cloud API configurado.**

### 2.1 Entrar al developer dashboard

- URL: `https://developers.facebook.com/apps/`
- Logueate con tu cuenta personal de Facebook (la misma de Fase 1).
- Si nunca creaste una app, vas a ver un botón verde **"Create App"** centrado.

### 2.2 Crear App

- Click **"Create App"**.
- **Paso 1 — App name + email:**
  - **App Display Name:** `Hubara WhatsApp` (visible en App Review modals, no para usuarios).
  - **App Contact Email:** `tu-nombre@hubara.com.co`.
  - Click **"Next"**.
- **Paso 2 — Use case:** elegí **`Other`** (esto desbloquea todos los tipos de app en el siguiente paso; "WhatsApp" como use case prefijado limita opciones después).
  - Click **"Next"**.
- **Paso 3 — App Type:** elegí **`Business`** ⚠️ **CRÍTICO**.
  - Los otros tipos (`Consumer`, `Gaming`, `None`) NO permiten `catalog_management` ni `whatsapp_*` con scopes ampliados. Si elegís uno equivocado, no se puede cambiar después — hay que borrar la app y empezar de nuevo.
  - Click **"Next"**.
- **Paso 4 — Business Portfolio:** dropdown con tus business portfolios → seleccioná **"Hubara"**.
  - Si no aparece, refrescá la página. Si sigue sin aparecer, volvé a Fase 1.
  - Click **"Create App"** → te pide tu password de Facebook por seguridad.

### 2.3 Anotar el App ID

- URL del dashboard de la app: `https://developers.facebook.com/apps/<APP_ID>/dashboard/`
- El `<APP_ID>` numérico (~15 dígitos) es visible en la URL Y en la sección "App settings → Basic".

| Anotar |
|---|
| `META_APP_ID = _______________________` |
| `META_APP_SECRET = _______________________` (en "App settings → Basic" → "App secret" → "Show", pedirá tu password) |

> El `App Secret` no se usa en este v1 (solo si en el futuro se hace `appsecret_proof` HMAC validation), pero anotalo igual en password manager.

---

## Fase 3 — Configurar WhatsApp Cloud API en la app
<a id="fase-3"></a>

> **Saltá esta fase si ya tenés en el sidebar de la app un producto "WhatsApp" con un "Test phone number".**

### 3.1 Agregar el producto WhatsApp

- En el dashboard de la app, sidebar izquierdo → buscar la sección **"Products"** (puede aparecer como "+ Add Product" si no hay nada).
- Click **"+ Add Product"** → lista de productos disponibles.
- Encontrá **"WhatsApp"** → click **"Set up"**.
- Te lleva a la consola "WhatsApp Business Platform".

### 3.2 Configurar el primer phone number

Meta da por default un "Test phone number" (números `+1 555 0xxx xxxx`) para development:

- En `WhatsApp → API Setup` (sidebar):
  - **From (Sender):** seleccioná el test number (o agregá tu número real productivo más adelante).
  - **To (Recipient):** click **"Manage phone number list"** → agregá tu propio número de WhatsApp como destinatario de prueba (max 5 en development mode).
  - Te llega un código de verificación a tu WhatsApp → ingresalo.
- **Anotar el Phone Number ID** que aparece en la sección "Send and receive messages":

| Anotar |
|---|
| `WHATSAPP_PHONE_NUMBER_ID_TEST = _______________________` (numérico ~15 dígitos) |

### 3.3 Generar Temporary Access Token (solo para validar conexión)

- Misma página `API Setup` → sección **"Temporary access token"** → click **"Generate"**.
- Te da un token de 24h. **NO LO USES EN PRODUCCIÓN** — solo para probar que la conexión funciona ahora con un cURL de ejemplo.
- Después de validar, lo descartás. El token permanente lo generás en Fase 11.

### 3.4 Smoke test con el test number

Pegá el cURL de ejemplo que Meta te muestra en la página (botón **"Send Message"**), reemplazando el token temporal. Debe llegarte un "Hello World" template a tu WhatsApp.

- Si llega: WhatsApp Cloud API está configurado OK. Seguí adelante.
- Si no llega: revisar que tu número esté en "Manage phone number list" + que el template `hello_world` aparezca como `Approved`.

### 3.5 (Producción) Agregar tu número real

Para producción real Hubara necesita un número propio (no test):

- En `API Setup` → botón **"Add phone number"** → modal:
  - **Display name:** "Hubara" (lo que ven los clientes).
  - **Category:** `Shopping & Retail`.
  - **Phone number:** el número productivo Hubara (debe poder recibir SMS o llamada de verificación).
- Verificación: Meta llama o manda SMS. Confirmar el código.
- El número queda en estado `Pending → Connected`. Tarda 5–15 minutos.

| Anotar (cuando tengas el número real) |
|---|
| `WHATSAPP_PHONE_NUMBER_ID = _______________________` (el productivo, no el test) |

> **Nota:** podés seguir con TODAS las fases siguientes (4-18) usando el test number primero. El número real lo agregás cuando esté Hubara listo para clientes reales — el sistema funciona idéntico.

---

## Fase 4 — Linkear la App al Business Portfolio
<a id="fase-4"></a>

> Esto suele pasar automáticamente cuando creás la app en Fase 2 con un Business Portfolio seleccionado. Verificá igual.

### 4.1 Confirmar que la app aparece en el Business

- URL: `https://business.facebook.com/settings/apps`
- En la lista de "Apps" debe aparecer **"Hubara WhatsApp"** con su `App ID`.

### 4.2 Si NO aparece — claim manual

- Click **"Add"** (botón azul arriba derecha) → **"Connect an existing App ID"**.
- Pegá el `META_APP_ID` de Fase 2.3.
- Click **"Add app"**.
- Te pide confirmar que sos owner del app → click `Confirm`.

---

## Fase 5 — Business Verification (gate más largo: 1–5 días)
<a id="fase-5"></a>

> **CRÍTICO:** Sin Business Verification aprobada, ciertos scopes (incluyendo `catalog_management`) quedan en modo limitado. Si vas a producción real (no solo dev), esto es bloqueante. **Iniciá HOY.**

### 5.1 Confirmar estado actual

- URL: `https://business.facebook.com/settings/security` → sección "Business verification" en el panel izquierdo.
- Estados posibles:
  - **Verified (badge verde):** listo, saltar a Fase 6.
  - **Not verified / Not started:** ir a 5.2.
  - **Pending / In review:** esperar (1–5 días). Podés seguir con Fases 6–8 mientras tanto.

### 5.2 Iniciar verificación

- Mismo URL → botón **"Start verification"**.
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
- Click **"Submit"**.
- **Email de aprobación/rechazo:** 1–5 días hábiles. Si rechazan, te dicen qué doc no cuadró.

---

## Fase 6 — Verificar WABA + Phone Number ID
<a id="fase-6"></a>

### 6.1 Confirmar WABA bajo el Business Portfolio

- URL: `https://business.facebook.com/settings/whatsapp-business-accounts`
- Click el WABA de Hubara → panel derecho muestra `Phone numbers` con el número activo + `Phone Number ID`.

| Anotar |
|---|
| `WABA_ID = _______________________` (~15 dígitos) |
| `WHATSAPP_PHONE_NUMBER_ID = _______________________` (productivo, no el test) |

### 6.2 Si el WABA aparece en otro Business

- Hay que migrarlo. En el Business correcto: `Settings → WhatsApp Accounts → Add → Request Access`.
- Es un trámite Meta-mediated (1–3 días), pero es raro: el WABA usualmente queda donde lo creaste.

---

## Fase 7 — Crear catálogo en Commerce Manager
<a id="fase-7"></a>

### 7.1 Entrar a Commerce Manager

- URL: `https://business.facebook.com/commerce` (redirige a `commerce.facebook.com`).
- Si nunca hubo catálogo en este Business, vas a ver una pantalla de bienvenida con un botón azul **"Add catalog"** o **"Get started"**.

### 7.2 Crear el catálogo

- Click `Add catalog` → modal "Create a new catalog":
  - **Catalog name:** `Hubara Velas Aromáticas` (algo identificable; aparece en UI admin, no en el chat).
  - **Catalog type / Vertical:** **`E-commerce`** ⚠️ **CRÍTICO**.
    - NO Retail, NO otros. Solo E-commerce funciona 100% con WhatsApp catalog messages (`interactive.product`, `interactive.product_list`). Si elegís mal, no se puede cambiar — hay que crear catálogo nuevo.
  - **Catalog owner:** Business Portfolio de Hubara (pre-seleccionado).
- Click `Create`.
- URL final: `https://business.facebook.com/commerce/<CATALOG_ID>/home` → ese número en la URL es el ID.

| Anotar |
|---|
| `WHATSAPP_CATALOG_ID = _______________________` (~16 dígitos) |

### 7.3 Asignar admin del catálogo

- En el catálogo → engranaje (Settings) arriba derecha → tab `People` o `Permissions`.
- Verificá que vos figurás como `Admin`.
- El System User (lo creás en Fase 10) lo agregás después.

---

## Fase 8 — Linkear catálogo al WABA
<a id="fase-8"></a>

### 8.1 Abrir el WABA en WhatsApp Manager

- URL: `https://business.facebook.com/latest/whatsapp_manager/overview?waba_id=<WABA_ID>`
- O navegando: `business.facebook.com` → menú izquierdo → `WhatsApp Manager` → seleccionar el WABA.
- Nav izquierdo del WhatsApp Manager → `Account tools` → **`Catalog`**.

### 8.2 Conectar el catálogo

- Si nunca se linkeó nada, ves "No catalog connected" con CTA azul **"Choose a catalog"**.
- Dropdown → seleccioná "Hubara Velas Aromáticas" → click **"Connect catalog"**.
- **Restricción:** un WABA solo puede tener UN catálogo activo. Si más adelante necesitan separar inventarios (Hubara Belleza vs Hubara Hogar), necesitan otro WABA distinto.

### 8.3 Activar visibilidad pública (opcional pero recomendado)

- En la misma sección Catalog del WhatsApp Manager, toggle **"Show catalog on WhatsApp"** → **ON**.
- Esto hace que el botón "View catalog" aparezca en el perfil del negocio dentro del chat (los clientes lo ven al tocar el nombre del negocio en la conversación).
- Sin costo. Mejora discoverability.

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
> **El checkbox `catalog_management` NO aparece en el modal de generación de token a menos que el permiso esté declarado en la App.** Es por eso que aunque generes el token con todos los checkboxes ticked, sale incompleto.

### 9.1 Entrar al App Dashboard

- URL: `https://developers.facebook.com/apps/<APP_ID>/dashboard/`
- Logueate con tu cuenta personal (la admin de la app).

### 9.2 Ir a "App Review → Permissions and Features"

- Sidebar izquierdo del App Dashboard → categoría **"App Review"** → subsección **"Permissions and Features"**.
- URL directa: `https://developers.facebook.com/apps/<APP_ID>/app-review/permissions/`

### 9.3 Buscar `catalog_management`

- Esta página lista TODOS los permisos disponibles para apps tipo Business. Hay un buscador arriba.
- Tipeá: `catalog_management`
- Aparece una fila con:
  - **Permission name:** `catalog_management`
  - **Description:** "Provides the ability to perform CRUD operations on a Business' product catalog…"
  - **Status:** posiblemente **"Standard Access"** o **"Request advanced access"** (botón a la derecha).

### 9.4 Activar el permiso

**Caso A — la fila muestra "Standard Access (active)":** ya está, saltá a Fase 10.

**Caso B — la fila muestra botón "Request Advanced Access" (o "Get Advanced Access"):**

- Click **"Request Advanced Access"**.
- Modal **"How will you use this permission?"** → elegí: **`I want to use this permission for my own business / app`** (NO elijas "Build a product for other businesses" — esa opción es para BSPs tipo Twilio).
- En modo single-business, **NO requiere App Review con screencast** — Meta lo concede como "Standard Access" inmediatamente. El screencast solo aplica si vas a ser un proveedor para otras empresas.
- Click **"Get Standard Access"** (o "Confirm" según wording del modal).
- El status cambia a **"Standard Access"** ✓.

### 9.5 Verificar permisos finales de la app

La fila `catalog_management` debe estar verde "Standard Access". Mientras estés en la página, asegurate también que estos otros 3 estén en Standard Access (Hubara los necesita TODOS):

- [x] `whatsapp_business_messaging`
- [x] `whatsapp_business_management`
- [x] `business_management`
- [x] **`catalog_management`** ← el nuevo

> Si alguno falta, repetí 9.3-9.4 con ese.

### 9.6 Refrescar para propagar

- Cmd+Shift+R (refresh forzado) en la página.
- **Esperá 2 minutos** antes de pasar a Fase 11 — Meta tarda un poquito en propagar el permiso al modal del token. (Fase 10 podés hacerla ya, no afecta.)

---

## Fase 10 — Crear System User + asignar todos los assets
<a id="fase-10"></a>

> El System User es la "cuenta de servicio" que va a generar el token permanente. Sin asset assignment, el token sale sin permiso a operar sobre los assets concretos.

### 10.1 Crear el System User

- URL: `https://business.facebook.com/settings/system-users`
- Click **"Add"** → modal "Create new system user":
  - **Name:** `hubara-whatsapp-prod` (descriptivo; lo van a ver futuros admins).
  - **Role:** **`Admin`** ⚠️ — NO `Employee`. Solo Admins pueden generar tokens con scopes amplios.
- Click `Create system user` → te pide tu password.

### 10.2 Asignar assets (los 3 que necesitamos)

Una vez creado, click sobre el system user → botón **"Add Assets"** (azul, esquina superior derecha del panel).

El modal tiene tabs por tipo de asset. Hay que hacerlo **3 veces** (una por tab):

#### Tab 1 — Apps

- Click tab **"Apps"**.
- Lista de apps del Business → seleccioná **"Hubara WhatsApp"**.
- Toggle a la derecha → **`Develop`** o **`Manage`** (cualquiera funciona; "Develop" es más conservador).
- Click **"Save Changes"**.

#### Tab 2 — WhatsApp accounts

- Click **"Add Assets"** de nuevo → tab **"WhatsApp accounts"**.
- Seleccioná el WABA de Hubara.
- Toggle → **`Full control`** (necesario para generar tokens con permission `whatsapp_business_*`).
- Click **"Save Changes"**.

#### Tab 3 — Catalogs ⭐ (el que la gente olvida)

- Click **"Add Assets"** de nuevo → tab **"Catalogs"**.
- Lista de catálogos del Business → seleccioná **"Hubara Velas Aromáticas"**.
- Toggle a la derecha → **"Manage catalog"** → **ON**.
- Click **"Save Changes"**.

### 10.3 Verificar Assigned Assets

En el panel del system user, scrolleá hasta la sección **"Assigned Assets"** (o "Connected Assets"). Debe aparecer:

- ✅ Apps: Hubara WhatsApp — `Develop` o `Manage`
- ✅ WhatsApp accounts: WABA Hubara — `Full control`
- ✅ Catalogs: Hubara Velas Aromáticas — `Manage catalog`

Si falta alguno, repetir 10.2 con el tab correspondiente.

---

## Fase 11 — Generar token permanente con los 4 scopes
<a id="fase-11"></a>

> Ahora SÍ va a aparecer el checkbox `catalog_management` en el modal (porque la app lo tiene en Fase 9 y el system user tiene el catálogo asignado en Fase 10).

### 11.1 Click Generate New Token

- En el mismo panel del system user → click botón **"Generate New Token"** (azul, abajo del panel de assets).

### 11.2 Configurar el token

Modal "Generate New Token":

- **App:** dropdown → seleccionar **"Hubara WhatsApp"**.
- **Token expiration:** **`Never`** ⚠️ — esto evita la rotación de 60 días del user token. Es lo que hace permanente al token.

### 11.3 Tickear los checkboxes

Lista de **"Available Permissions"** — ahora debería tener al menos estos 5 (más algunos extras que NO necesitás):

- [x] `whatsapp_business_messaging`
- [x] `whatsapp_business_management`
- [x] `business_management`
- [x] **`catalog_management`** ← **EL NUEVO. Asegurate de que está tickeado.**
- [ ] `pages_show_list` (opcional, no lo necesitás)
- [ ] otros checkboxes — dejalos sin tickear (principio de least privilege)

### 11.4 Generate y copiar

- Click **"Generate Token"**.
- Meta muestra el token UNA SOLA VEZ. **Copialo INMEDIATAMENTE a un password manager** (1Password, Bitwarden).
- Empieza con `EAA...`, ~200 caracteres.

| Anotar (en password manager, NUNCA en archivos del repo) |
|---|
| `META_SYSTEM_USER_TOKEN = EAA...` |

> **¿Y si lo perdés?** Volvé a esta misma pantalla → `Generate New Token` → repetir 11.2-11.4. El token viejo queda invalidado automáticamente (no, no es así realmente — los tokens viejos siguen vivos hasta que los revoques manualmente; usá el botón "Revoke" si querés rotarlo).

### 11.5 Si `catalog_management` SIGUE sin aparecer en el checklist

Causas residuales (poco frecuentes):

1. **El catálogo no se asignó al System User** (Fase 10.2 Tab 3) — volver allá y verificar.
2. **El permiso no se propagó aún** — esperá 5 minutos, refrescá con Cmd+Shift+R, intentá de nuevo. Cerrá y abrí el modal.
3. **Tu rol en el Business Portfolio es Employee, no Admin** — pedile al super-admin del Business que te haga Admin (`Business Settings → People → tu nombre → Edit → Role: Admin`).
4. **La app no es tipo Business** — Fase 2.2 paso 3 era crítico. Volver al App Dashboard → "App settings → Basic" → campo "App type". Si dice `Consumer` o `Gaming`, esa app NO sirve. Crear app nueva tipo Business y migrar (lleva ~15 min).
5. **Business Verification "Pending"** — algunos scopes están limitados mientras esté en review. Si tu badge dice `Pending`, esperá. (Si dice `Verified` o `Not verified`, este no es el problema.)

---

## Fase 12 — Verificar scopes con `debug_token`
<a id="fase-12"></a>

> Antes de cargar el token al .env, **siempre validá** que tiene los 4 scopes. Si te ahorrás esto, podés pasar 1h debuggeando "Missing Permission" por nada.

### 12.1 Correr debug_token

Desde cualquier terminal (no hace falta entorno del proyecto):

```bash
curl -s "https://graph.facebook.com/v23.0/debug_token?input_token=<TU_TOKEN_NUEVO>&access_token=<TU_TOKEN_NUEVO>" | python3 -m json.tool
```

### 12.2 Validar que `catalog_management` esté en `scopes`

Output esperado (las 4 líneas verdes ✓):

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

Si **`catalog_management` NO está en el array `scopes`** → volvé a Fase 9.4 (el permiso no está en la app) o a Fase 10.2 Tab 3 (el catálogo no está asignado al system user). El problema NO está en el token — está en lo que vino antes.

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
    "owner_business": {
        "id": "<BUSINESS_ID>",
        "name": "Hubara"
    }
}
```

Si devuelve `(#100) Missing Permission` → el catálogo NO está asignado al System User (volver a Fase 10.2 Tab 3).

---

## Fase 13 — WhatsApp Flow de datos de envío (formulario nativo)
<a id="fase-13"></a>

> **Por qué esta fase es OPCIONAL pero recomendada**: sin el Flow, el agente recolecta los 5 datos (ciudad, barrio, dirección, teléfono, pago) **conversacionalmente por texto** (mensaje formateado + recolección turn-by-turn). Funciona, pero el cliente tipea 5 veces. Con el Flow publicado, ve un **formulario nativo en WhatsApp** y completa todo en 1 botón. Cero código del lado del dev — todo el wiring (`send_flow`, `nfm_reply` parser, `_format_flow_response` para el LLM) ya está en el repo.

> **Cuándo hacerlo**: cualquier momento. La Fase 14+ no la bloquea. Se puede activar más adelante seteando `META_FLOW_ID_SHIPPING` en el `.env` y recreando el worker `chats-sales` — el dispatch detecta el cambio automáticamente.

### 13.1 Abrir Flow Builder en Meta

- URL: `https://business.facebook.com/wa/manage/flows/?business_id=<BUSINESS_ID>`
- Si no aparece el menú "Flows" en la barra lateral de WhatsApp Manager, asegurate de que tu cuenta tiene rol **Admin** en el Business (Fase 1.4) y que la app está vinculada a este Business (Fase 4).

### 13.2 Crear un Flow nuevo

1. Click **`Create flow`**.
2. **Name**: `Hubara — Datos de envío v1`
3. **Categories**: marcá **`SIGN_UP`** (recolección de datos de cliente; encaja mejor que `SURVEY`).
4. **Template**: elegí **`Start from scratch`** (vamos a pegar el JSON entero).
5. **Endpoint URI / "Data endpoint"**:
   - **NO completar nada — dejar el campo vacío.** Si Meta te exige un valor o muestra un toggle **`Endpoint-less Flow`**, activá ese.
   - **¿Por qué no necesitamos endpoint?** Este Flow es **estático puro**: recibe los datos iniciales (`payment_options`, `order_total_cop`, `items_summary`) cuando el agente envía el mensaje, y cuando el cliente apreta "Confirmar" emite un payload `nfm_reply` con todos los campos. No hay llamadas a backend mientras el cliente llena el formulario. Por eso el JSON v1 **NO** incluye el campo `data_api_version` (en versiones del Flow Builder donde ese campo dispara la obligación de endpoint).
6. Click **`Create`**.

> Si la UI fuerza un endpoint y no acepta vacío ni tiene toggle endpoint-less, podés poner una URL placeholder válida (ej. `https://hubara.com.co/whatsapp-flow-noop`). Meta NO la va a llamar porque nuestro Flow termina con `complete` action — no con `data_exchange`. Pero es feo y conviene buscar el toggle primero.

### 13.3 Pegar el JSON canónico

1. En el editor, abrí la pestaña **`Edit JSON`** (esquina superior derecha).
2. Borrá el JSON template autogenerado.
3. Pegá el contenido íntegro de **`hubara_agency/docs/whatsapp_flows/shipping_v1.json`**. (El archivo está en el repo; copialo desde tu IDE o cualquier editor.)
4. Click **`Save`**.

> Si la UI te muestra warning "comments not allowed" sobre el campo `_comment_`, ignoralo — Meta lo conserva como propiedad anotada y no afecta runtime.
> Si la UI insiste con "missing `data_api_version`", **NO lo agregues**: eso convertiría el Flow en dynamic y forzaría endpoint. La ausencia es intencional (Flow estático endpoint-less, ver 13.2).

### 13.4 Validar y publicar

1. Click **`Validate`**. Debe quedar verde (`Flow is valid`). Si sale error, comprobá:
   - Que no editaste el JSON al pegar (alguna comilla curva, indentación rota).
   - Que `version` sea `"7.2"`.
   - Que **NO** haya un campo `data_api_version` (el JSON v1 lo omite a propósito).
2. Click **`Preview`** y hacé un test rápido: completá los 5 campos en el preview y mirá el output JSON que generaría el `nfm_reply`. Deberías ver `{city, neighborhood, address, phone, payment_method, order_total_cop, items_summary}`.
3. Click **`Publish`**. Meta tarda 1–3 minutos en publicarlo (status pasa `DRAFT` → `PUBLISHED`).
4. Copiá el **`Flow ID`** (string numérico ~16 dígitos que Meta muestra en el header de la pestaña, ej. `1234567890123456`).

### 13.5 Configurar el `flow_id` en el backend

Agregá al `.env` del backend (local + producción):

```bash
META_FLOW_ID_SHIPPING=1234567890123456
```

Y recreá el worker `chats-sales` para que tome la nueva env var:

```bash
cd hubara_agency
docker compose -f docker-compose.local.yml up -d --force-recreate hubara-worker-chats-sales
```

Smoke test desde el chat: cuando llegues a la etapa de envío, el agente debe abrir el formulario nativo (botón "Completar datos" → modal con 5 campos), NO un mensaje de texto enumerando los campos.

Si seguís viendo el texto plano: el worker tomó la variable como vacía. Verificá con:
```bash
docker exec local-hubara-worker-chats-sales printenv META_FLOW_ID_SHIPPING
```

### 13.6 Cuando el cliente complete el Flow

Lo que pasa atrás de escena (no necesitás hacer nada — el dev ya lo tiene wireado):

1. El cliente apreta **`Confirmar datos`** en el formulario.
2. Meta envía un webhook al backend con `interactive.nfm_reply.response_json` conteniendo los 5 campos.
3. El parser (`src/plugins/chats/agent/sales/translate.py`) lo convierte a texto plano para el LLM: `[datos de envío recibidos] city=Bogotá; neighborhood=Chapinero; address=Cl 100 #15-20; phone=3001234567; payment_method=transfer; ...`.
4. El LLM continúa el cierre con `verify_order_for_checkout` y luego `present_order_confirmation`.

### 13.7 Versionado / cambios futuros

Si querés cambiar el JSON (agregar un campo, cambiar copy, etc):

1. Editá `docs/whatsapp_flows/shipping_v1.json` localmente.
2. Subí el nuevo JSON al Flow Builder y republicalo (botón `Publish` otra vez).
3. **Importante**: cada Publish genera una **nueva versión** del Flow PERO el `flow_id` NO cambia. Por eso `META_FLOW_ID_SHIPPING` se setea una sola vez.
4. Si rompés algo grave y querés rollback rápido, podés crear un Flow nuevo (`shipping_v2`), publicarlo, y cambiar el env var apuntando al `flow_id` viejo o al nuevo.

---

## Fase 14 — Smoke test manual de 1 producto
<a id="fase-14"></a>

> Antes de habilitar la sincronización automática desde Medusa, validá que el linking funciona con un solo producto creado a mano. Esto separa "problema de configuración Meta" de "problema de sincronización Medusa→Meta".

### 14.1 Crear un producto manual

- URL: `https://business.facebook.com/commerce/<CATALOG_ID>/products`
- Botón **`Add items`** (azul, esquina superior derecha) → modal con 3 opciones → elegí **`Manual`** → `Next`.

### 14.2 Campos mínimos (requeridos por Meta)

| Campo | Valor de prueba | Notas |
|---|---|---|
| Image | JPG/PNG, ≥500×500 px, ≤8 MB | **NO usar .webp** — Meta no lo acepta para upload manual. JPG/PNG. |
| Title | "Vela aromática Lavanda — 250g" | Max 150 chars; visible en chat. |
| Description | "Fragancia relajante, notas florales suaves" | **Sin health claims** (Fase 0). |
| Price | `45000` + Currency `COP` | COP nativo soportado desde fines 2025. **No usar USD.** |
| Availability | `in stock` | |
| Condition | `new` | |
| Brand | `Hubara` | |
| Content ID / Retailer ID | `HUB-TEST-001` | **CRÍTICO** — es lo que el dev usa en `product_retailer_id`. Valor estable, no auto-generado. |
| Link | `https://hubara.com.co/productos/vela-lavanda-250g` | Puede ser placeholder. |

### 14.3 Confirmar que pasa a "Active"

- Click `Add item` → status inicial "Pending review".
- Refrescá la lista de productos cada 5 minutos. Pasa a "Active" en 5–30 minutos.
- Si pasa a "Rejected" → click sobre el producto → panel lateral con `reason code`. Causas frecuentes: imagen <500px, copy con health claims, link rota.

---

## Fase 15 — Smoke test end-to-end con el dev
<a id="fase-15"></a>

Una vez que tengas los 5 datos a entregar (ver Fase 17), pasáselos al dev. El dev corre:

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

- Tu WhatsApp recibe una card con imagen + nombre + precio + botón **"View"**.
- Tocar "View" → abre detail con "Add to cart".
- Errores comunes:
  - `Error 100 — invalid catalog_id` → catálogo no linkeado al WABA del Phone Number ID usado (Fase 8).
  - `Error 100 — Missing Permission` → token sin `catalog_management` (Fase 9-11).
  - Card sin imagen → producto subido con .webp; el backend del workflow normaliza automáticamente para los productos que vengan de Medusa, pero el manual de Fase 13 NO se normaliza — re-subí la imagen como JPG.

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

- Recibís un mensaje con header + carousel.
- Tocar "View items" → modal con thumbnails.
- Selección de productos → arma cart.
- Botón final **"Send"** → manda un order summary al chat.

### 15.3 Verificar order webhook (dev)

Cuando completás un cart desde el MPM y tocás "Send", el backend Hubara debe recibir un webhook tipo `order` con la lista de productos. El dev valida que llegó al backend (ej. revisando logs del API).

---

## Fase 16 — Catalog Quality Dashboard
<a id="fase-16"></a>

### 16.1 Ver productos rechazados / con issues

- URL: `https://business.facebook.com/commerce/<CATALOG_ID>/diagnostics`
- Dashboard con dos secciones:
  - **Errors (críticos):** items que no aparecen en ningún canal. Cada error muestra los productos afectados + un `Fix` action.
  - **Warnings (no críticos):** items que aparecen pero con quality reducida (imagen baja resolución, falta `google_product_category`).

### 16.2 Quality score global

- URL: `https://business.facebook.com/commerce/<CATALOG_ID>/quality`
- Factores que bajan el score:
  - Productos con campos opcionales vacíos (gtin, mpn, brand inconsistente).
  - Imágenes <500px o con marca de agua agresiva.
  - Descripciones <50 chars.
  - Mismatches entre `link` y la página final (404, redirect a homepage).
  - Stale availability (`in stock` pero la web dice sold out).
- **Objetivo razonable para v1:** >85%.

---

## Fase 17 — Entregables finales al dev
<a id="fase-17"></a>

Cuando completes Fases 1–14, mandame **por canal seguro** (1Password share, Bitwarden Send, Signal — **NO** Slack público, **NO** email plano):

```
BUSINESS_ID=<de 1.3>
META_APP_ID=<de 2.3>
WABA_ID=<de 6.1>
WHATSAPP_PHONE_NUMBER_ID=<de 6.1>
WHATSAPP_CATALOG_ID=<de 7.2>
META_SYSTEM_USER_TOKEN=<de 11.4 — el secreto>
META_FLOW_ID_SHIPPING=<de 13.5 — flow_id publicado>
```

El dev los carga en:
- Local dev: `hubara_agency/.env` (en `.gitignore`, NUNCA committeado).
- Producción: secret store de Docker / k8s.

---

## Fase 18 — Consideraciones LATAM Colombia
<a id="fase-18"></a>

### 18.1 Currency

- WhatsApp Commerce acepta **COP nativo** desde fines de 2025.
- Formato del campo `price`: número entero **sin separadores de miles** + espacio + `COP`. Ejemplo: `"45000 COP"`.
- **No usar USD** — clientes ven precio en su moneda nativa, sin conversión confusa.

### 18.2 Pagos in-app

- **WhatsApp Pay NO está disponible en Colombia al 2026-05** (solo Brasil, India, Singapur).
- El flujo `interactive.product_list` → cliente arma carrito → toca "Send" → el order summary llega al backend como webhook tipo `order`.
- **De ahí en adelante, Hubara responde con un link de pago externo** (Wompi / PayU / ePayco — a definir).
- Este link de pago **NO está cubierto en este v1** — viene como work item separado cuando se decida la pasarela.

### 18.3 DIAN / Facturación electrónica

- **Meta NO pide nada relacionado con DIAN.** El RUT entra como doc de Business Verification (Fase 5.2), pero después no hay integración fiscal.
- La facturación electrónica obligatoria (resoluciones DIAN) es responsabilidad de Hubara en su propio backend, **después** del order callback.

### 18.4 Idioma

- Los campos `title` y `description` pueden ir en español. El clasificador de policy violations de Meta entiende español OK.

### 18.5 Business hours

- El catálogo NO tiene business hours nativo. Si querés que el perfil del negocio en WhatsApp muestre horarios, configuralos en `business.facebook.com/wa/manage/profile/` → "Business hours" con timezone `America/Bogota`.

---

## Fase 19 — Categorías PROHIBIDAS por Meta
<a id="fase-19"></a>

> Aplica para Commerce Catalog en cualquier canal Meta (FB, IG, WhatsApp). Si subís uno de éstos, el catálogo entero puede quedar degradado o suspendido.

1. **Adult products** (juguetes sexuales, contenido para adultos).
2. **Alcohol** (cualquier bebida alcohólica — WhatsApp es más estricto que FB/IG; totalmente prohibido acá).
3. **Animals** y partes/fluidos del cuerpo humano.
4. **Counterfeit goods** (réplicas, "inspired by", knockoffs).
5. **Digital products and services intangibles** (NFTs, cursos digitales puros, gift cards de terceros).
6. **Discrimination / hate speech** (productos con simbología discriminatoria).
7. **Drugs, drug paraphernalia, kratom, CBD** (CBD prohibido aun en países donde es legal — **esto incluye velas con CBD**).
8. **Healthcare items** (medicamentos, agujas, equipos médicos).
9. **Ingestibles & supplements regulados** (vitaminas con claims, pérdida de peso).
10. **Misleading / deceptive practices** ("antes $100, ahora $20" falsos, before/after).
11. **Real money gambling.**
12. **Recalled products.**
13. **Tobacco, e-cigarettes, vaping products.**
14. **Unsafe supplements** (steroids, peptides).
15. **Weapons, ammunition, explosives.**
16. **Services** (peluquería, consultoría — Meta es "tangible goods only" estricto en WhatsApp).
17. **Vehicles** (autos, motos — usar vertical "Auto" en otro catalog).
18. **Subscriptions** (recurrentes, SaaS).

**Para Hubara (velas aromáticas) OK con tres caveats:**

- **Eliminar health claims** del copy (ver Sección 0).
- **No incluir CBD/cannabis.**
- **Productos terminados** (vela armada, difusor armado) sí; **materias primas sueltas** (aceite esencial puro) → zona gris, mejor no subir.

---

## Checklist imprimible
<a id="checklist-imprimible"></a>

### Sección 0 — Pre-flight
- [ ] Revisé títulos y descripciones en Medusa: sin health claims
- [ ] Sin productos con CBD / cannabis en el catálogo
- [ ] No hay aceites esenciales puros sueltos

### Fase 1 — Cuenta Meta + Business
- [ ] Cuenta personal de Facebook con email `@hubara.com.co` + 2FA activado
- [ ] Business Portfolio "Hubara" creado en `business.facebook.com`
- [ ] Business info completo (legal name, address, phone, website, NIT)
- [ ] Anotado: `BUSINESS_ID = ____________`

### Fase 2 — App de Meta
- [ ] App creada en `developers.facebook.com/apps/`
- [ ] **App type: `Business`** (no Consumer/Gaming) ⚠️
- [ ] Business Portfolio "Hubara" asociado al crear la app
- [ ] Anotado: `META_APP_ID = ____________` + `META_APP_SECRET = ____________`

### Fase 3 — WhatsApp Cloud API
- [ ] Producto "WhatsApp" agregado en sidebar del App Dashboard
- [ ] Test phone number configurado + mi número en "Manage phone number list"
- [ ] Smoke test "Hello World" template enviado y recibido OK
- [ ] (Producción) Número real Hubara agregado y conectado

### Fase 4 — Linking App ↔ Business
- [ ] App "Hubara WhatsApp" visible en `business.facebook.com/settings/apps`

### Fase 5 — Business Verification
- [ ] Status = **Verified** (badge verde)
- [ ] (Si Pending) docs subidos: Cámara de Comercio + RUT + comprobante de domicilio

### Fase 6 — WABA
- [ ] WABA visible en `business.facebook.com/settings/whatsapp-business-accounts`
- [ ] Anotado: `WABA_ID = ____________`
- [ ] Anotado: `WHATSAPP_PHONE_NUMBER_ID = ____________` (productivo)

### Fase 7 — Catálogo
- [ ] Catalog name: `Hubara Velas Aromáticas`
- [ ] Vertical: **`E-commerce`** ⚠️ (no Retail, no otros)
- [ ] Catalog owner: Business Portfolio Hubara
- [ ] Anotado: `WHATSAPP_CATALOG_ID = ____________`

### Fase 8 — Linking Catalog ↔ WABA
- [ ] Catálogo conectado al WABA en `WhatsApp Manager → Account tools → Catalog`
- [ ] Toggle "Show catalog on WhatsApp" en **ON**

### Fase 9 — `catalog_management` en la App ⭐
- [ ] App Review → Permissions: `catalog_management` con status **Standard Access** ✓
- [ ] Mismo status para: `whatsapp_business_messaging`, `whatsapp_business_management`, `business_management`
- [ ] Esperé 2 minutos después de activarlo

### Fase 10 — System User + Assets ⭐
- [ ] System User creado: `hubara-whatsapp-prod` con role **Admin**
- [ ] Asset asignado: Apps → Hubara WhatsApp → `Develop` o `Manage`
- [ ] Asset asignado: WhatsApp accounts → WABA → `Full control`
- [ ] Asset asignado: **Catalogs → Hubara Velas Aromáticas → `Manage catalog`** ⚠️

### Fase 11 — Token permanente
- [ ] Token generado con expiration **`Never`**
- [ ] Los 4 checkboxes tickeados: `whatsapp_business_messaging`, `whatsapp_business_management`, `business_management`, `catalog_management`
- [ ] Token guardado en password manager (`META_SYSTEM_USER_TOKEN`)

### Fase 12 — Verificación token
- [ ] `debug_token` muestra los 4 scopes incluyendo `catalog_management`
- [ ] `GET /<CATALOG_ID>?fields=...` devuelve el catálogo (no 401/403)

### Fase 13 — WhatsApp Flow de envío
- [ ] Flow creado en Meta Flow Builder (categoría `SIGN_UP`)
- [ ] JSON pegado desde `hubara_agency/docs/whatsapp_flows/shipping_v1.json`
- [ ] Validación verde (botón "Validate")
- [ ] Flow publicado (estado `PUBLISHED`)
- [ ] `flow_id` copiado y agregado a `.env` como `META_FLOW_ID_SHIPPING=...`
- [ ] Worker `chats-sales` recreado (`docker compose up -d --force-recreate hubara-worker-chats-sales`)

### Fase 14 — Producto manual
- [ ] Al menos 1 producto subido manualmente con `Retailer ID = HUB-TEST-001`
- [ ] Producto en status "Active" (no Pending / no Rejected)

### Fase 15 — Smoke test E2E
- [ ] Mi número agregado como test recipient en Developer Console
- [ ] `interactive.product` recibido en mi WhatsApp con card visible (imagen incluida)
- [ ] `interactive.product_list` recibido con carousel
- [ ] **Flow de datos de envío** abre como formulario nativo (no como texto plano fallback)
- [ ] Order webhook llegando al backend cuando se completa un cart

### Fase 16 — Quality
- [ ] Dashboard de issues revisado: 0 errors críticos
- [ ] Catalog quality score >85%

### Fase 17 — Entregables al dev
- [ ] Mandé al dev por canal seguro las 6 vars (incluyendo `META_FLOW_ID_SHIPPING`)
- [ ] Dev confirmó que cargó las vars en `.env` local y en prod

### Fase 18 — LATAM
- [ ] Confirmado: precios en COP (no USD)
- [ ] Pasarela de pago externa: **PENDIENTE** (work item separado)
- [ ] Business hours configuradas en `America/Bogota` (opcional)

---

## Anexos
<a id="anexos"></a>

### Anexo A — Env vars en el backend

Una vez que tengas los datos, el dev los carga en `hubara_agency/.env`:

```bash
# Meta Commerce Catalog (HU-002 Parte B)
META_CATALOG_ID=<de Fase 7.2>
META_SYSTEM_USER_TOKEN=<de Fase 11.4>

# Opcionales (referencia / future multi-tenant)
META_BUSINESS_ID=<de Fase 1.3>
META_WABA_ID=<de Fase 6.1>
# WHATSAPP_PHONE_NUMBER_ID ya existía
```

### Anexo B — Cómo dispara el dev el primer push

Con el backend corriendo (`uv run python -m src.run_workers` con el worker `catalog_sync` activo), desde el repo root del backend:

```bash
cd hubara_agency
uv run python scripts/trigger_catalog_sync.py
```

Output esperado:

```
Started workflow catalog-sync-on-demand-manual-1747832400
  snapshot_dir: /Users/edgm/.../catalog_workspace

✅ Sync completed
  Snapshot:
    version: a1b2c3d4
    bytes_written: 124589
    files_written: 47
  Meta push:
    pushed: True
    handle: AScdef123...
    creates: 23
    updates: 0
    deletes: 0
    skipped (sin imagen): 0
    skipped (sin precio): 0
    duration: 4.2s
```

Si ves `pushed: False` y `error: meta_not_configured` → falta cargar las env vars de Meta en `.env`.

### Anexo C — Eventual consistency

- **Items nuevos:** Meta documenta **14–24 horas** hasta que aparezcan en WhatsApp / Instagram como "shoppable" desde el perfil. Las messages `interactive.product` funcionan más rápido (minutos).
- **Updates** (price, availability) sobre items existentes: minutos a 1 hora.
- **Implicación:** **no programar campañas de lanzamiento "para el mismo día"** después de crear el catálogo. Esperar al menos 24h después del primer push para campañas en serio.

### Anexo D — Rate limits del catalog

- Initial: ~200 batch calls / hora por catálogo.
- Header `X-Business-Use-Case-Usage` muestra % usado.
- Si el catálogo Hubara crece, Meta escala automáticamente a 10k/h con uso normalizado.
- El backend usa **1 batch call por sync run** (todos los CREATE/UPDATE/DELETE en una sola request). No deberías ver rate limit hits salvo si disparás el sync >200 veces en 1h, lo cual no es normal.

### Anexo E — `item_type` requerido por Graph API v21+

Para `/items_batch`, Meta exige el parámetro `item_type` en el body. Para Hubara siempre es `PRODUCT_ITEM`. El backend lo manda automáticamente (`MetaBatchRequest.item_type` default = `"PRODUCT_ITEM"`). Si en el futuro Hubara quiere catálogos de hoteles/vuelos/vehículos, hay que cambiar el DTO.

### Anexo F — Cómo arreglar productos sin imagen

El backend skipea productos sin `thumbnail` ni `images[]`. Para que un producto skipped aparezca en Meta:

1. Entrá al admin de Medusa (`https://hubara.com.co/app` o donde tengan instalado el admin).
2. `Products` → buscar el producto skipped → click.
3. Sección "Media" → upload thumbnail + al menos 1 imagen adicional.
4. Save.
5. Disparar el sync de nuevo (`uv run python scripts/trigger_catalog_sync.py`). Esta vez se manda como CREATE.

---

## Troubleshooting
<a id="troubleshooting"></a>

| Síntoma | Causa probable | Cómo arreglar |
|---|---|---|
| `(#100) Missing Permission` en `/items_batch` | Token sin `catalog_management` scope | Fase 9–11. Verificar con Fase 12.1 que aparece en `scopes`. |
| `(#100) The parameter item_type is required` | Backend < 2026-05-22 sin fix `item_type` | El fix está committeado; rebuildear el worker (`docker compose up -d --build hubara-worker-catalog-sync`). |
| `(#100) Missing Permission` solo en `/items_batch`, pero `GET /<catalog_id>` funciona | El token tiene `catalog_management` pero el catálogo no está asignado al System User | Fase 10.2 Tab 3 (Catalogs). |
| `catalog_management` no aparece en el modal de Generate Token | El permiso no está habilitado en la App | Fase 9.3–9.4. |
| Productos quedan en "Pending review" >24h | Manual review humana | Esperar 48h. Si sigue, abrir ticket Commerce Support. |
| Producto rechazado: "policy violation" | Health claim en description / categoría prohibida | Reescribir copy en Medusa, esperar al próximo sync. |
| Imagen no aparece en la card de WhatsApp | URL .webp sin normalizar (productos viejos pre-fix) | Re-sincronizar; el backend ahora normaliza .webp → .jpeg via Cloudflare. |
| `interactive.product` devuelve `Error 100 — invalid catalog_id` | Catálogo no linkeado al WABA del número usado | Fase 8.2. Verificar con `GET /<WABA_ID>/product_catalogs`. |
| Push devuelve 80008 "rate limited" | Demasiados syncs en 1h | Esperar 1h. Reducir frecuencia del trigger. |
| Token expira con `Error 190` | Token revocado / system user eliminado | Regenerar token (Fase 11). Verificar que System User no fue borrado. |
| Catalog Quality score <70% | Productos rechazados nuevos o thumbnails baja calidad | Revisar Diagnostics dashboard (Fase 15.1). |
| `Application does not have permission` (`#10`) | Business Verification pendiente | Esperar aprobación (Fase 5). |

---

**Última actualización:** 2026-05-22
**Cambios desde versión anterior:**
- Agregadas Fases 1–4 (Business Portfolio + creación de App + WhatsApp Cloud API setup) — antes asumían que existían.
- Fase 9 reescrita como gate explícito: el permiso `catalog_management` en la App es PRE-REQUISITO para que aparezca en el modal de token (gotcha 2026-05-22).
- Fase 10.2 ahora documenta los 3 tabs por separado (Apps, WhatsApp accounts, Catalogs) — el tab Catalogs era el más olvidado.
- Fase 12 nueva: verificación con `debug_token` antes de cargar el token.
- Anexo E nuevo: `item_type` requirement de Graph API v21+.
- Anexo F nuevo: cómo arreglar productos sin imagen.
- Troubleshooting expandido con los 3 errores nuevos vistos en producción.

**Próxima revisión:** cuando Meta publique Graph API v24 o cambie la UI de Commerce Manager (lo que ocurra primero).
