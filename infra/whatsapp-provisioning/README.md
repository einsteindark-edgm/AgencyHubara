# WhatsApp + Catálogo — provisioning repetible (estilo Terraform)

Levanta el **WhatsApp Cloud API + el catálogo Meta** de un tenant (Hubara u
otro) de la forma más automatizada posible. La idea es la de Terraform: una
**config declarativa por tenant** describe el estado deseado, `plan` muestra el
diff contra el estado real (vía Graph API) y `apply` converge idempotente.

> **Qué NO se puede automatizar** (Meta lo exige humano, una sola vez):
> conseguir la línea telefónica, **recibir el código de verificación**, el
> **App Secret**, la **Business Verification** y la aprobación del **display
> name**. Todo lo demás lo hace el CLI.

```
infra/whatsapp-provisioning/
  whatsapp_provision.py     # el CLI (solo stdlib; corre con cualquier python3)
  tenants/<tenant>.env      # config declarativa por tenant (gitignoreá los reales)
  definitions/              # definiciones declarativas TENANT-AGNÓSTICAS (versionadas)
    flows.json              #   flows nativos a crear+publicar (shipping, etc.)
    templates.json          #   message templates a submitear a Meta (copy + samples)
  README.md                 # esta guía
```

> **Dos niveles de config a propósito:** `tenants/<tenant>.env` = lo que cambia
> por tenant (IDs, número, token). `definitions/*.json` = lo que se comparte
> entre tenants (los flows y el copy de los templates). Un tenant nuevo reusa las
> `definitions/` tal cual; solo escribe su `.env`.

---

## 0. Prerrequisitos (una vez por tenant, en consola Meta)

Estos son los pasos manuales irreducibles. Detalle paso-a-paso con clicks en
`hubara_agency/docs/META_CATALOG_SETUP.md` (Fases 1–12).

1. **Business Portfolio** del cliente + **Business Verification** APROBADA.
2. **App tipo Business** vinculada al business. Anotá `APP_ID` y `APP_SECRET`.
3. Permisos en App Review (Standard Access): `whatsapp_business_messaging`,
   `whatsapp_business_management`, `business_management`, `catalog_management`.
4. **WABA** creado bajo el business.
5. **Catálogo** (vertical E-commerce) creado en Commerce Manager.
6. **System User** (Admin) con el **app + WABA + catálogo** asignados; generar
   **token permanente** (expiration Never) con los 4 scopes. Ese token va por
   env como `META_SYSTEM_USER_TOKEN`.

Verificá el token: `curl ".../debug_token?input_token=$T&access_token=$T"` debe
listar los 4 scopes (o usá `whatsapp_provision.py discover`).

---

## 1. Configurar el tenant

```bash
cd infra/whatsapp-provisioning
cp tenants/hubara.env.example tenants/hubara.env
$EDITOR tenants/hubara.env        # BUSINESS_ID, APP_ID, WABA_ID, CATALOG_ID, CALLBACK_URL, NEW_NUMBER...
# Secretos por env (no en el archivo):
export META_SYSTEM_USER_TOKEN='EAA...'
export WHATSAPP_APP_SECRET='...'
```

`CALLBACK_URL` = `https://<CADDY_DOMAIN>/api/chats/webhook` (el dominio del box
prod; `grep CADDY_DOMAIN /opt/hubara/.env`).

---

## 2. Descubrir y planear

```bash
python3 whatsapp_provision.py discover --config tenants/hubara.env   # estado real
python3 whatsapp_provision.py plan     --config tenants/hubara.env   # diff deseado vs real
```

---

## 3. Aplicar (alta del número + wiring)

```bash
# 1ª pasada: da de alta el número y dispara el código de verificación
python3 whatsapp_provision.py apply --config tenants/hubara.env
#   -> "Esperando el código..." (Meta lo manda por SMS/voz al número)

# 2ª pasada: con el código que recibió la línea
python3 whatsapp_provision.py apply --config tenants/hubara.env --code 123456
```

`apply` es idempotente y hace, en orden seguro:
`add-number → request-code → [código humano] → verify-code → register →
commerce-settings → subscribe-app → webhook → flows → templates`.

> Si Meta rechaza `add-number` por API (apps que no son Tech Provider), agregá
> el número en **WhatsApp Manager → Add phone number** (UI, ~2 min), poné su
> `PHONE_NUMBER_ID=<id>` en la config y re-corré `apply` — el resto sigue
> automatizado.

---

## 4. Wirear el backend (env → SSM → deploy)

El CLI **no toca SSM** (eso es infra). Generá el bloque y subilo:

```bash
python3 whatsapp_provision.py ssm-block --config tenants/hubara.env
# Pegá las líneas WHATSAPP_* en infra/scripts/secrets.<tenant>.env:
#   WHATSAPP_PHONE_NUMBER_ID=<nuevo>   WHATSAPP_BUSINESS_ACCOUNT_ID=<waba>
#   WHATSAPP_ACCESS_TOKEN=<= META_SYSTEM_USER_TOKEN>   WHATSAPP_VERIFY_TOKEN=...
#   WHATSAPP_APP_SECRET=<app secret>

cd ../scripts && python3 aws_bootstrap.py secrets --tenant <tenant> --file secrets.<tenant>.env
# En la caja EC2:
ssh ec2-user@<host> 'cd /opt/hubara && ./render-env-from-ssm.sh && \
  docker compose up -d --force-recreate api worker-chats-sales worker-chats-remarketing'
```

> Gotchas: `WHATSAPP_ACCESS_TOKEN` debe ser **el mismo** token System User
> (`META_SYSTEM_USER_TOKEN`). `aws_bootstrap.py` NO limpia comentarios inline —
> valores en su propia línea. SSM reads se corrompen por el proxy; usá
> `rtk proxy aws ssm get-parameter ...` para ver el valor real.

---

## 5. Catálogo (Medusa → Meta)

Independiente del número. Con `META_CATALOG_ID` + `META_SYSTEM_USER_TOKEN` en el
`.env` del worker `catalog-sync`:

```bash
# en la caja:
docker compose exec -T worker-catalog-sync python scripts/trigger_catalog_sync.py --triggered-by setup
# esperás: pushed:True, creates:N, y un `handle` real (no None)
```

El pull sube solo productos `published`, **sin collection**, con **imagen** y
**precio COP**. El mapper prefiere COP cuando Medusa lista usd+cop.

---

## 5.5 Flows y templates (WABA-scoped — se re-crean al migrar de WABA)

Tanto los **WhatsApp Flows** (formularios nativos) como los **message templates**
(único modo legal de escribir fuera de la ventana de 24h) **viven dentro de un
WABA**. Si migrás de WABA (número nuevo / app nueva), el `flow_id` y los templates
del WABA viejo **no sirven** — hay que re-crearlos/re-someterlos en el nuevo. El
CLI lo hace idempotente desde `definitions/`:

```bash
python3 whatsapp_provision.py flows            --config tenants/hubara.env   # create + upload JSON + publish
python3 whatsapp_provision.py templates        --config tenants/hubara.env   # CREATE los que faltan (submit a review)
python3 whatsapp_provision.py templates-update --config tenants/hubara.env   # EDITA la copy que cambió (→ PENDING)
python3 whatsapp_provision.py capi             --config tenants/hubara.env   # dataset CAPI (atribución CTWA), create+link
```

- **`flows`** lee `definitions/flows.json` (nombre + categorías + path al JSON del
  flow en el repo). Si ya hay un flow `PUBLISHED` con ese nombre lo reusa; si no,
  lo crea, sube el `FLOW_JSON` y lo publica. El `flow_id` resuelto sale en
  `ssm-block` como `META_FLOW_ID_SHIPPING`.
- **`templates`** (create) lee `definitions/templates.json` (name, category, language,
  body, samples). Si el template ya existe (name+language) **no** re-submitea: reporta
  su status. Si no existe, lo crea → entra a review de Meta (**1–24h**, a veces 72h).
- **`templates-update`** (edit) compara el `body`/categoría de cada definición contra
  lo que vive en Meta y **edita SOLO los que cambiaron** (`POST /{template_id}`). Es
  idempotente (si el body ya matchea, no toca nada) y salta los `PENDING` (no
  editables). Al editar, el template **vuelve a PENDING** hasta re-aprobación —
  `name`/`language` son inmutables (para eso: borrar+recrear, cooldown 30 días).

> **Copy de templates = quality rating de por vida.** El `body` de cada template
> lo redacta un humano (no LLM). Los `UTILITY` **no** pueden tener promo/ofertas
> (Meta los recategoriza a marketing). El único `MARKETING` del set es
> `cart_recovery` — y **tampoco** menciona promoción ni envío gratis: es
> re-engagement puro con opt-out (política 2026-07 + guía de mensajes de marketing
> de Meta). Acento **tuteado**, no voseo. Fuente del copy:
> `hubara_agency/.hubara/runbooks/meta_template_approval.md`.

> **Sync con el código:** los `name` de `definitions/templates.json` deben matchear
> exactamente el `waba_template_name` de
> `hubara_agency/src/platform/whatsapp/templates/catalog.yaml`. Cuando Meta apruebe,
> seteás `WATCHDOG_ENABLED=true` en SSM y redeployás (ver runbook §7).

---

## 6. Verificación E2E

```bash
python3 whatsapp_provision.py discover --config tenants/hubara.env   # app suscrita, número CONNECTED, commerce on
```
- Mandá un WhatsApp al número → `docker compose logs -f api` muestra el inbound + el agente responde.
- Catálogo: verificá por el MCP oficial (`ads_catalog_*`) que `product_count` > 0 y precios en COP.
- Consistencia eventual de Meta: cards en minutos; "shoppable" en el perfil 14–24h.

---

## Mapa: automatizado vs manual

| Paso | ¿Automatizado? |
|---|---|
| Business Verification | ❌ Meta-side (humano) |
| Crear app / permisos / token | ❌ consola (una vez) |
| Conseguir la línea + recibir código | ❌ humano |
| App Secret | ❌ lo provee el operador |
| Alta del número (`add-number`) | ✅ API (fallback UI si no-Tech-Provider) |
| request/verify code, register/PIN | ✅ API (código relevado por humano) |
| subscribe-app, commerce-settings, webhook | ✅ API |
| **Flows nativos** (create + upload + publish) | ✅ `flows` (desde `definitions/flows.json`) |
| **Templates** (create a Meta) | ✅ `templates` (desde `definitions/templates.json`) |
| **Templates** (editar copy que cambió) | ✅ `templates-update` (idempotente, → PENDING) |
| **Dataset CAPI** (atribución CTWA, create+link al WABA) | ✅ `capi` (idempotente; `POST /{WABA_ID}/dataset` reemplaza Events Manager §13+§15) |
| **App domains** (dominios OAuth de la app, login Meta del dashboard) | ✅ `app-domains` (aditivo + verificación post-POST; requiere toggle "API access to app settings", ver §5.7) |
| SSM + render + recreate | ✅ scripts (`aws_bootstrap` + `render-env-from-ssm.sh`) |
| Catálogo Medusa→Meta | ✅ `trigger_catalog_sync.py` |
| Redacción del copy de templates | ❌ humano (define quality rating) |
| **Aprobación** de templates | ❌ Meta-side (1–24h review) |
| Display name approval | ❌ Meta-side |

## 5.6 CAPI — atribución CTWA (dataset WABA-scoped)

El comando `capi` crea (si falta) el **dataset de Conversions API** linkeado al
WABA — la caja donde aterrizan los eventos `LeadSubmitted`/`Purchase` de
atribución CTWA (`action_source: business_messaging`). Sin esto, los
Click-to-WhatsApp ads son ciegos: Meta ve el click pero no la venta.

- Es **WABA-scoped**: al migrar de WABA se crea dataset nuevo → re-pushear
  `META_CAPI_DATASET_ID` a SSM.
- El `META_SYSTEM_USER_TOKEN` sirve como `META_CAPI_ACCESS_TOKEN` (necesita
  `ads_management`, que ya pide este toolkit).
- Los nombres de evento válidos son **`LeadSubmitted`** y **`Purchase`** —
  `Lead` (nombre del CAPI web clásico) es RECHAZADO con error 2804066.
- El backend consume ambas vars vía `src/platform/config.py`
  (`send_capi_event_activity` — dispara al cierre de episodio en sales).
- Runbook humano con el detalle completo: `hubara_agency/.hubara/runbooks/meta_template_approval.md` §11–§22.

## 5.7 App domains — dominios OAuth de la app (login Meta del dashboard)

El comando `app-domains` converge `app_domains` + `website_url` de la **app**
(no del WABA) vía `POST /{APP_ID}` con el app token (`id|secret`). Sin el
dominio del `redirect_uri` en esa lista, el diálogo OAuth del dashboard (plugin
ads, "Conectar con Meta") rebota con **"El dominio de esta URL no está incluido
en los dominios de la app"** — caso 2026-07-09.

```bash
python3 whatsapp_provision.py app-domains --config tenants/hubara.env
```

Config: `APP_DOMAINS` (coma-separado) + `WEBSITE_URL` en el `.env` del tenant;
requiere `APP_SECRET`. Es **aditivo** (no borra dominios existentes) e
idempotente, y después del POST **re-lee** para verificar qué persistió de verdad.

Gotchas que motivaron automatizarlo:

- **El form del dashboard pierde ediciones en silencio**: el campo "Dominios de
  la app" exige Enter para commitear el chip ANTES de "Guardar cambios"; si no,
  guarda sin el dominio y no avisa. La API es la vía autoritativa.
- **Error `(#10)` "Changing app settings through API calls has been disabled"**:
  toggle en el dashboard de la app → Configuración → Avanzada → Seguridad →
  **"Permitir el acceso de la API a la configuración de la app"** → Sí (una vez
  por app). El comando lo detecta y te imprime este fix.
- **Dominios de DNS compartido** (`sslip.io`, `ngrok`): Meta puede aceptar el
  POST con `success:true` y **descartar el dominio en silencio** — por eso el
  re-GET de verificación. Si lo descarta, la salida es usar un dominio propio
  (p.ej. `hubara.com.co`) para el redirect OAuth.

## Multi-tenant

Un `tenants/<tenant>.env` por cliente + su `secrets.<tenant>.env` en SSM
(`/<prefix>/<tenant>/*`). El CLI no guarda estado: el estado real ES Meta, y
`plan`/`discover` lo leen siempre fresco. `tenants/*.env` (salvo `.example`)
debe ir en `.gitignore`.
