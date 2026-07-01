# Runbook — Setup operacional Meta Business Manager (Templates + CAPI)

> **Audience:** Vos (operador AgencyHubara) — alguien que NUNCA aprobó un template Meta ni configuró CAPI antes.
> **Scope:** Este doc cubre 2 setups operacionales que tenés que hacer manualmente en Meta Business Manager antes de activar HU-WA24H-001 en prod:
>   - **§1-§10** — Aprobación de los 4 templates iniciales (watchdog + remarketing).
>   - **§11-§22** — Configuración de **CAPI (Conversions API) for Business Messaging** — necesario para que los CTWA (Click-to-WhatsApp Ads) sean atribuibles y Meta pueda optimizar tus campañas hacia compras reales.
> **Triggered when:** estás listo para activar HU-WA24H-001 Y/O vas a empezar a comprar tráfico CTWA.
> **Tiempo estimado:**
>   - Templates: **45-60 min activos** (redactar + submit los 4). **24-72h pasivos** (esperando approval Meta por template).
>   - CAPI: **30-45 min activos** (Events Manager + token + smoke test). **0 tiempo pasivo** (no hay approval — funciona apenas mandes el primer event).
> **Cuándo arrancar:**
>   - Templates: YA. Cuanto antes empezás, antes tenés activable el watchdog.
>   - CAPI: ANTES de poner un solo CTWA ad live. Sin CAPI los ads son CIEGOS — Meta no sabe si convierten.

---

## §0 Antes de arrancar — checklist

Asegurate de tener acceso a:

- [ ] **Cuenta personal de Facebook** conectada a la Business Manager de AgencyHubara.
- [ ] **Permisos de Admin** o "Manage templates" en la WhatsApp Business Account (WABA). Si no tenés, pedirle al dueño de la Business Manager.
- [ ] **El phone_number_id** del número WhatsApp que va a usarse en producción (lo necesitás solo para verificar, no para crear el template). Está en `metadata.json` del vault o en la env var `WHATSAPP_PHONE_NUMBER_ID`.
- [ ] **Login activo** en `business.facebook.com` (la cuenta personal que es Admin).
- [ ] **45 min sin interrupciones** — el flujo es ir 4 veces por el mismo wizard, pero pedir aprobación con copy revisado lleva tiempo.

---

## §1 Llegar a la pantalla de Templates

1. Abrir el navegador en **https://business.facebook.com/**.
2. Si te pide login, entrar con la cuenta personal que es Admin de AgencyHubara.
3. Una vez dentro, **arriba a la izquierda** vas a ver el nombre de la Business Manager (si tenés varias, elegir la de **AgencyHubara**).
4. En el **menú izquierdo**, buscar el ícono de **"WhatsApp Manager"** (un cuadradito verde con el logo de WhatsApp). Si no aparece directamente, hacer click en **"All tools"** abajo y buscar "WhatsApp Manager" en la lista.
5. Click en **"WhatsApp Manager"** → te lleva al panel principal de la WABA.
6. En el panel de WABA, **menú izquierdo**, ver:
   - "Overview"
   - "Phone numbers"
   - **"Account tools"** → expandir → **"Message templates"** ← **acá**

7. Click en **"Message templates"**.

Vas a ver una tabla con todos los templates de la WABA (probablemente vacía o con muy pocos templates de prueba/legacy).

8. Arriba a la derecha de la tabla, botón azul **"Create template"** ← este es el que vamos a usar 4 veces.

---

## §2 Template #1 — `quote_ready_utility` (UTILITY)

> **Lo que hace:** notificar al cliente que su cotización está lista.
> **Cuándo se manda:** cuando el cliente pidió cotización pero pasaron horas sin que el agente respondiera. Watchdog dispara automático 30min antes de cierre de ventana 24h.
> **Costo:** $0 dentro de ventana, $0.0008 USD fuera.

### §2.1 Crear

Click en **"Create template"**. Aparece un wizard.

**Step 1 — Category**

- Seleccionar **"Utility"** ← IMPORTANTE: NO marketing.
  - Si el dropdown te muestra sub-options, elegir **"Account update"** o la más genérica de Utility.
- Click **"Continue"**.

**Step 2 — Name and language**

- **Name**: escribir `quote_ready_utility` (todo minúsculas, snake_case, sin acentos, sin espacios).
  > ⚠️ Este nombre debe matchear EXACTAMENTE el `waba_template_name` del catalog YAML del código (`hubara_agency/src/platform/whatsapp/templates/catalog.yaml`). Si cambiás el nombre acá, hay que cambiarlo allá también.
- **Language**: buscar **"Spanish (COL)"** o `es_CO`. NO usar "Spanish" genérico — la WABA está configurada para Colombia.
- Click **"Continue"**.

**Step 3 — Template content**

Vas a ver 4 secciones: Header (optional), **Body (required)**, Footer (optional), Buttons (optional).

- Header: **dejarlo vacío** (no agregar).
- **Body**: pegar este texto EXACTO (con los `{{1}}` y `{{2}}`):

  ```
  Hola {{1}}, tu cotización para {{2}} ya está lista. ¿Quieres que te la comparta ahora?
  ```

- Abajo del campo Body vas a ver **"Add sample content"**. Click ahí — Meta exige samples para aprobar.
  - `{{1}}`: escribir `Andrea` (sample del nombre)
  - `{{2}}`: escribir `kit aroma rosas (cotización #42)` (sample del producto/cotización)
- Footer: **dejarlo vacío**.
- Buttons: **dejarlo vacío**. NO agregar botones — los buttons quote-reply meterían el template en zona gris para Meta.

**Step 4 — Submit**

- Click **"Submit"**.
- Meta te muestra un modal con disclaimer ("Templates take 1-24 hours to approve..."). Click **"Confirm"**.

### §2.2 Qué esperar

- El template aparece en la tabla con status **"In review"** (amarillo).
- Aprobación típica: **1-24h** (a veces hasta 72h si Meta está saturada).
- Meta te manda un email cuando approve / reject.
- Si reject: ver §6 troubleshooting.

---

## §3 Template #2 — `payment_pending_utility` (UTILITY)

> **Lo que hace:** recordar pago pendiente de orden creada.
> **Cuándo se manda:** cliente arrancó checkout y se distrajo. Watchdog 23.5h después.
> **Costo:** $0 dentro de ventana, $0.0008 fuera.

### §3.1 Repetir el wizard

Volver a **"Message templates" → "Create template"**.

**Step 1 — Category**: **Utility** (mismo flow que §2.1).

**Step 2 — Name and language**:
- **Name**: `payment_pending_utility`
- **Language**: `Spanish (COL)`

**Step 3 — Template content**:
- Header: vacío.
- **Body**:
  ```
  Hola {{1}}, tu pago de la orden {{2}} por {{3}} está pendiente. ¿Te ayudo a completarlo?
  ```
- Samples:
  - `{{1}}`: `Juan`
  - `{{2}}`: `#1042`
  - `{{3}}`: `$120.000 COP`
- Footer: vacío.
- Buttons: vacío.

**Step 4 — Submit**.

---

## §4 Template #3 — `order_status_utility` (UTILITY)

> **Lo que hace:** notificar update de orden (despachada, en camino, demorada, entregada).
> **Cuándo se manda:** post-compra cuando hay update objetivo del estado.
> **Costo:** $0 dentro de ventana, $0.0008 fuera.

### §4.1 Repetir el wizard

Volver a **"Message templates" → "Create template"**.

**Step 1 — Category**: **Utility**.

**Step 2**:
- **Name**: `order_status_utility`
- **Language**: `Spanish (COL)`

**Step 3 — Body**:
```
Hola {{1}}, tu pedido {{2}} está {{3}}. Si quieres más información, cuéntame por aquí.
```

Samples:
- `{{1}}`: `Carlos`
- `{{2}}`: `#1042`
- `{{3}}`: `en camino con entrega estimada mañana`

Footer y Buttons vacíos.

**Step 4 — Submit**.

---

## §5 Template #4 — `cart_recovery_marketing` (MARKETING)

> **Lo que hace:** recuperar carrito abandonado fuera de ventana (re-engagement, SIN promoción ni envío gratis; el body incluye opt-out).
> **Cuándo se manda:** decisión consciente del LLM o cadencia (NUNCA por watchdog).
> **Costo:** $0.0125 USD siempre (es marketing).
> ⚠️ Este es el ÚNICO marketing del set inicial. Copy conservador: NO mencionar promociones ni envío gratis (política 2026-07 + guía de mensajes de marketing de Meta). Acento tuteado, no voseo.

### §5.1 Repetir el wizard

Volver a **"Message templates" → "Create template"**.

**Step 1 — Category**: ahora **"Marketing"** ← DISTINTO de los anteriores.
- Sub-options: elegir **"Custom"** si te lo pide.

**Step 2**:
- **Name**: `cart_recovery_marketing`
- **Language**: `Spanish (COL)`

**Step 3 — Body**:
```
Hola {{1}}, vi que quedó {{2}} en tu carrito. ¿Quieres que te ayude a completar tu pedido? Si no deseas recibir más mensajes, respóndeme y te doy de baja.
```

Samples:
- `{{1}}`: `Andrea`
- `{{2}}`: `una vela aromática Patchouli`

Footer y Buttons vacíos.

> ⚠️ **Sin promociones ni envío gratis.** El template es re-engagement puro (solo 2 variables: nombre + producto). No mencionar descuentos, promos ni "envío gratis" en el copy ni en los samples. Incluye opt-out en el body ("respóndeme y te doy de baja") por requisito de la guía de mensajes de marketing de Meta.

**Step 4 — Submit**.

---

## §6 Troubleshooting — qué hacer si Meta rechaza

### §6.1 Reject reason "Promotional content in Utility template"

**Causa:** Meta detectó copy promo en un template marcado utility.
**Solución:**
1. Click en el template rechazado → "Edit template".
2. Quitar cualquier mención de descuento, promo, oferta, "te ahorras".
3. Re-submit.

### §6.2 Reject reason "Template name does not match conventions"

**Causa:** nombre con mayúsculas, espacios, caracteres especiales.
**Solución:**
1. Verificar que el nombre sea exactamente `quote_ready_utility` (etc.) — todo minúsculas, snake_case, sin acentos.
2. Si necesitás cambiar el nombre, hay que **eliminar** el template rechazado (botón "Delete") y crear uno nuevo (no se puede editar el nombre).
3. **MUY IMPORTANTE**: si el nombre nuevo es distinto al que está en `catalog.yaml`, hay que actualizar el código:
   - File: `hubara_agency/src/platform/whatsapp/templates/catalog.yaml`
   - Cambiar el valor del campo `waba_template_name` del template correspondiente al nombre nuevo aprobado.
   - Si me pedís ayuda con eso (cambio de YAML + verificar tests), avisame.

### §6.3 Reject reason "Generic / not specific enough"

**Causa:** Meta dice que el copy es muy vago, podría aplicar a cualquier negocio.
**Solución:**
1. Hacer el body más específico al contexto de AgencyHubara (e.g., mencionar "tu pedido en Hubara" en vez de "tu pedido" solo).
2. Re-submit. Cuidado: si agregás demasiado contexto promocional, Meta lo recategorizá a marketing.

### §6.4 Reject silenciado por días

**Causa:** Meta a veces tarda más de 72h sin notificar.
**Solución:**
1. Esperar 5 días totales.
2. Si pasados 5 días sigue en "In review", hacer click en el template → si hay opción "Request review" usarla.
3. Si no hay opción, contactar soporte Meta vía Business Manager → Help.

### §6.5 Approved pero después "Paused" (132012)

**Causa:** Meta pausa templates automáticamente si la quality rating del template baja (muchos rejects por parte de usuarios). Se ve en la pantalla del template como "Paused".
**Solución:**
1. Esperar 7 días — Meta puede unpauseear si la métrica se recupera.
2. Mientras tanto, en código:
   - El send activity ya maneja code 132012 como non-retryable, así que el workflow aborta limpio.
   - El operador (vos) puede pausar el uso del template editando `catalog.yaml` y comentando el entry (o agregando un flag `disabled: true` que el registry respete — fix de pre-mortem si aplica).

---

## §7 Cuando los 4 estén aprobados — qué hacer

### §7.1 Verificación operacional

1. En "Message templates", los 4 templates deben aparecer con status **"Approved"** (verde).
2. Anotar los **nombres exactos** que quedaron aprobados (a veces Meta los normaliza, e.g., `quote_ready_utility` → `quote_ready_utility_v2` si el `v1` ya existía).

### §7.2 Sync con código

Verificar que `hubara_agency/src/platform/whatsapp/templates/catalog.yaml` tenga el `waba_template_name` matcheando EXACTO al aprobado en Meta. Si no matchea:

1. Editar el YAML — cambiar el campo `waba_template_name` por el aprobado.
2. NO cambiar el campo `name` (eso es interno).
3. Correr los tests:
   ```bash
   cd hubara_agency && uv run pytest tests/platform/test_whatsapp_template_registry.py
   ```
4. Commit con mensaje `chore(chats): sync waba_template_name with Meta-approved values`.

### §7.3 Test E2E manual

Antes de activar el watchdog en producción, hacer un send de prueba a tu propio número:

1. Asegurarte de tener `WATCHDOG_ENABLED=false` en env (default).
2. Mandar un template manualmente vía un script puntual:
   ```bash
   cd hubara_agency && uv run python -c "
   import asyncio
   from src.platform.whatsapp.activities import send_template_to_session
   asyncio.run(send_template_to_session(
       session_id='wa_+57<tu_numero>',
       template_name='quote_ready_utility_v1',
       variables={'customer_first_name': 'Test', 'product_or_quote_label': 'cotización de prueba'},
   ))
   "
   ```
3. Verificar que llega el WhatsApp en tu celular.
4. Verificar en Business Manager que el send aparece en "Activity" del template.
5. Si todo OK → tu sistema está listo para que el Sprint 2 (watchdog) se active.

### §7.4 Activar el watchdog en producción

(Solo después de §7.3 verde y de revisar el PR del Sprint 2.)

1. Setear env var `WATCHDOG_ENABLED=true` en el deploy de los workers.
2. Restart workers — `cd hubara_agency && uv run python -m src.run_workers`.
3. Monitorear logs durante 24h — buscar:
   - `template_send_success` events.
   - `wa_delivery_status` events con `pricing.type=free_customer_service`.
   - Quality rating en Business Manager — debe mantenerse GREEN.

---

## §8 Cheat sheet — los 4 templates en una sola tabla

| # | Nombre | Category | Variables | Body summary |
|---|---|---|---|---|
| 1 | `quote_ready_utility` | Utility | `{{1}}` nombre, `{{2}}` producto/cotización | Notifica cotización lista |
| 2 | `payment_pending_utility` | Utility | `{{1}}` nombre, `{{2}}` orden, `{{3}}` monto | Recuerda pago pendiente |
| 3 | `order_status_utility` | Utility | `{{1}}` nombre, `{{2}}` orden, `{{3}}` status | Update post-compra |
| 4 | `cart_recovery_marketing` | Marketing | `{{1}}` nombre, `{{2}}` producto, `{{3}}` incentivo | Recovery con oferta |

---

## §9 Si algo en el flow es distinto a lo que dice acá

Meta cambia la UI de Business Manager cada 3-6 meses. Si llegás a una pantalla que no matchea exactamente:

1. Buscar la opción equivalente por nombre (ej. si "Account tools" se renombró a "Manage tools", es lo mismo).
2. Si te trabás >10 min: tomá screenshot de dónde estás + qué buscás, y pedime ayuda — actualizamos este runbook con lo que veas.

## §10 Referencias (templates)

- Meta — Create message templates: https://developers.facebook.com/docs/whatsapp/business-management-api/message-templates/
- Meta — Template guidelines: https://developers.facebook.com/docs/whatsapp/business-management-api/message-templates/template-guidelines
- HU-WA24H-001 refinement §4.5 (template registry) y §7.0 (Fase 0 operacional).

---

# Parte 2 — CAPI (Conversions API) for Business Messaging

## §11 Qué es CAPI y por qué lo necesitás

### §11.1 El problema sin CAPI

Cuando vos comprás un **CTWA ad** (Click-to-WhatsApp Ad) en Facebook/Instagram, alguien lo clickea y arranca un chat con tu agente. **Sin CAPI**, Meta ve:

1. ✅ Usuario clickeó el ad (clic atribuido).
2. ✅ Usuario abrió WhatsApp y mandó "Hola".
3. ❌ **Después? Meta no tiene idea.** No sabe si el lead se cerró en venta, si pagó, si se fue.

Resultado: el algoritmo de optimización de Meta NO puede encontrar más gente como tus compradores reales — solo más gente como "gente que clickea ads". El **CPL** (costo por lead) baja al principio pero el **CAC** (costo de adquisición de cliente real) se va a la nube porque traés volumen sin calidad.

### §11.2 Lo que CAPI agrega

CAPI es un **endpoint server-to-server** donde TU sistema le dice a Meta:

> "Oye Meta: el cliente que mandaste vía el ad XYZ (identificado por `ctwa_clid`) **acaba de comprar** por $80.000 COP. Toma este `Purchase` event."

Meta matchea el `ctwa_clid` contra la impresión original del ad y registra una **conversión bottom-of-funnel**. Con suficientes Purchase events, su algoritmo aprende **qué tipo de impresión convierte** y empieza a buscar más usuarios parecidos. **CPL baja Y CAC baja al mismo tiempo** — el algoritmo finalmente puede optimizar para compras y no clics.

### §11.3 Por qué en 2026 es obligatorio (no opcional)

Meta apretó tornillos de atribución desde fines de 2025:
- **Ventana de atribución más corta** (7 días para CTWA CAPI events, antes 28).
- **Advantage+ requiere señales de conversión** para entrar en fase de aprendizaje productiva.
- iOS 14+ siguió erosionando el tracking client-side → CAPI es el único path confiable.

> **TL;DR:** si vas a poner CTWA en producción sin CAPI, estás quemando plata. El setup toma 30-45 min — paga 100x lo que cuesta.

### §11.4 Los 2 únicos eventos que CAPI Business Messaging soporta

> ⚠️ **El nombre del evento de lead es `LeadSubmitted`, NO `Lead`.** Meta rechaza `Lead` para `action_source: business_messaging` con error_subcode 2804066 (verificado contra el dataset vivo, 2026-07-01). `Lead` es el nombre del CAPI web clásico.

Meta limitó CAPI for Business Messaging (WhatsApp) a **2 eventos**:

| Evento | Cuándo mandarlo | Requiere |
|---|---|---|
| **LeadSubmitted** | Cliente mostró intención cualificada (pidió cotización, dijo "quiero comprar", entregó datos) | `ctwa_clid` |
| **Purchase** | Orden CONFIRMADA con pago | `ctwa_clid`, `value`, `currency` |

> ⚠️ **Otros eventos clásicos NO aplican aquí.** `AddToCart`, `InitiateCheckout`, `CompleteRegistration` etc. funcionan en el CAPI estándar (websites), pero **NO** en `action_source: business_messaging`. Si los mandás, Meta los ignora silenciosamente.

> ⚠️ **Solo 1 CAPI event por ad event.** Esto es CRÍTICO: si una conversación generó Lead y después Purchase, **solo el Purchase cuenta** — Meta dedupea y se queda con el último/más fuerte. Estrategia: priorizar Purchase si ocurre dentro de 7 días, fallback Lead.

---

## §12 Pre-requisitos antes del setup CAPI

Asegurate de tener:

- [ ] **Templates §1-§9 ya iniciados.** No bloquea CAPI, pero arrancá esos en paralelo (Meta los procesa async).
- [ ] **Permisos Admin** en la Business Manager (los mismos que necesitaste para templates).
- [ ] **Pixel ya creado** en la BM (si no tenés uno, los pasos §13 te guían a crearlo — un Pixel y un Dataset son lo mismo conceptualmente; Meta los unificó).
- [ ] **WABA verificada** y conectada a una Facebook Page (esto suele estar listo si ya operás WhatsApp Business; si no, lo hacés en Settings → Business Settings → WhatsApp Accounts).
- [ ] **Acceso a env vars / Vault** de producción donde vas a guardar el token. **No commitees el token al repo.**

---

## §13 Setup en Events Manager — paso a paso clicks

### §13.1 Llegar a Events Manager

1. Abrir **https://business.facebook.com/events_manager**.
2. Si te lleva a la home de BM en lugar de Events Manager, en el menú izquierdo buscá el ícono de gráfico de barras (📊) que dice **"Events Manager"** — o usá **"All tools"** y buscalo.
3. Una vez dentro vas a ver la lista de **Data Sources** (Pixels, Datasets, Conversions API gateways).

### §13.2 Crear o seleccionar el Dataset

**Caso A — ya tenés un Pixel/Dataset asociado a tu WABA:**
- Click sobre él para abrirlo.
- Saltá a §13.3.

**Caso B — no tenés ninguno (lo más probable si estás pre-launch):**
1. Botón **"Connect data sources"** o **"+ Add"** arriba a la izquierda.
2. Modal aparece — elegir **"Web"** (Pixel funciona para web Y messaging; Meta unificó).
   - Si te pregunta entre "Conversions API only" o "Pixel + CAPI", elegir **"Conversions API"** o el que incluya server events.
3. **Nombre del Dataset**: `AgencyHubara Dataset Prod` (o lo que quieras — humano-legible).
4. **Connect to**: tu Business Manager (debería estar pre-seleccionado).
5. Click **"Create"** o **"Confirm"**.
6. El Dataset aparece en la lista.

### §13.3 Encontrar el dataset_id

Una vez dentro del Dataset:

1. En la URL del browser vas a ver algo como `https://business.facebook.com/events_manager/data-sources/{NUMERO_LARGO}/...` — **ese `{NUMERO_LARGO}` es tu `dataset_id`**. Anotalo (ej. `1234567890123456`).
2. Alternativamente: en el panel del Dataset, arriba en el header dice "Dataset ID: 1234567890123456". Botón copy al lado.
3. **Guardalo seguro** — lo vas a meter en una env var (§18).

---

## §14 Generar el Access Token de CAPI

Sin token no podés mandar events. El access token es **server-to-server**, NO uses el token de usuario.

### §14.1 Ir a Settings del Dataset

1. Dentro del Dataset (de §13), en el menú lateral izquierdo del panel del Dataset buscar **"Settings"** (ícono de tuerca ⚙️).
2. Scroll hasta la sección **"Conversions API"**.
3. Subsección **"Set up manually"** o **"Generate access token"**.

### §14.2 Generar el token

1. Click en **"Generate access token"**.
2. Modal muestra el token (string largo, ~190 chars que empieza con `EAA...`).
3. **Copiar AHORA** — Meta lo muestra una sola vez. Si lo perdés, hay que generar uno nuevo.
4. Guardarlo en un password manager (1Password, Bitwarden) etiquetado como `Meta CAPI Token — AgencyHubara — Prod — 2026-05-27`.
5. Cerrar modal — **no** lo dejes en pantalla.

### §14.3 Anotar fecha de expiración

- El token de CAPI **no expira por default** (es System User token de larga duración), pero si lo rotás (best practice cada 90 días), poné un recordatorio en el calendario.
- Si en algún momento te aparece "Token expired" en logs → §19.5 troubleshooting.

---

## §15 Linkear WABA con el Dataset

Sin este link, el `ctwa_clid` que llega en los webhooks NO se conecta con los CAPI events que mandás.

### §15.1 Conectar

1. Dentro del Dataset → **Settings**.
2. Sección **"Connected Assets"** o **"Data Sources"** (a veces dice "Linked WhatsApp Business Accounts").
3. Botón **"+ Add WhatsApp Business Account"**.
4. Modal con dropdown — elegir tu WABA (la que ya usás para templates).
5. **Confirmar**.

### §15.2 Verificar conexión

- La WABA aparece en la lista de Connected Assets con status "Active".
- Si dice "Pending" o "Failed": esperar 2-5 min y refresh. Si persiste → §19.4.

---

## §16 Test Events — verificar que llegan

ANTES de poner CTWA ads en producción, mandar un event de prueba.

### §16.1 Conseguir el Test Event Code

1. Dentro del Dataset → tab **"Test events"** (en el menú top del Dataset).
2. Sección **"Server"**.
3. Vas a ver un campo **"Test event code"** con un valor del tipo `TEST12345`.
4. **Copiar** ese código.

### §16.2 Mandar un event de prueba (desde el código local)

> ⚠️ Esto requiere que el backend ya tenga el activity `send_capi_event_activity` implementado — ver §18.1 si todavía no existe.

Por ahora, smoke test manual con curl:

```bash
# Exportar las 3 vars (poner valores reales):
export META_CAPI_DATASET_ID="1234567890123456"     # del §13.3
export META_CAPI_ACCESS_TOKEN="EAA..."             # del §14.2
export META_CAPI_TEST_CODE="TEST12345"             # del §16.1
export WABA_ID="9876543210987654"                  # tu WABA id (lo tenés en metadata.json o env)

# Mandar Lead event con test_event_code:
curl -X POST "https://graph.facebook.com/v18.0/${META_CAPI_DATASET_ID}/events?access_token=${META_CAPI_ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "data": [{
      "event_name": "LeadSubmitted",
      "event_time": '$(date +%s)',
      "event_id": "smoke_test_'$(date +%s)'",
      "action_source": "business_messaging",
      "messaging_channel": "whatsapp",
      "user_data": {
        "whatsapp_business_account_id": "'${WABA_ID}'",
        "ctwa_clid": "FAKE_CLID_SMOKE_TEST"
      }
    }],
    "test_event_code": "'${META_CAPI_TEST_CODE}'"
  }'
```

### §16.3 Verificar en Events Manager

1. Refresh el tab **"Test events"** del Dataset.
2. Tu event debería aparecer en la lista con:
   - **Event name**: LeadSubmitted
   - **Action source**: business_messaging
   - **Status**: ✅ Received
3. Si NO aparece:
   - Verificar que el `dataset_id` en la URL del curl matchea el del Dataset.
   - Verificar que el access_token no esté trunco al pegarlo.
   - Verificar que el `test_event_code` esté en el body (no en URL).
   - Ver §19.2.

### §16.4 Match Quality — qué chequear

En el Dataset → tab **"Overview"** vas a ver el **Event Match Quality (EMQ)** score (0-10) por evento. Para CTWA con `ctwa_clid`:
- Score esperado: **8-10** (alto, porque ctwa_clid es match perfecto).
- Si está <6 → es porque te falta el `ctwa_clid` o lo estás mandando mal. NO mandes más events hasta arreglar.

---

## §17 Eventos que vamos a mandar y CUÁNDO — estrategia AgencyHubara

Acá vivís la decisión de diseño. Sólo 2 eventos, cuándo dispararlos.

### §17.1 Estrategia de disparo (high level)

```
┌────────────────────────────────────────────────────────────────────┐
│ Customer clickea CTWA ad → entra a WhatsApp                         │
│       │                                                              │
│       ▼ ctwa_clid llega en referral del primer webhook              │
│ ┌──────────────────────────────┐                                    │
│ │ persist ctwa_clid en         │ ← F2.2 metadata.json                │
│ │ episode metadata.json        │   con timestamp                     │
│ │ (TTL 7 días — attribution    │                                    │
│ │  window expiry)              │                                    │
│ └──────────────────────────────┘                                    │
│       │                                                              │
│       ▼                                                              │
│ Customer engages — agente conversational                             │
│       │                                                              │
│       ├──► [Trigger A] qualified_intent_detected                     │
│       │      (ej: pidió cotización, dijo "quiero pagar")            │
│       │      → emit IntentQualifiedEvent                            │
│       │      → workflow re-checks: ¿ya mandamos Purchase para        │
│       │        este ctwa_clid? Sí → SKIP. No → mandar Lead.          │
│       │                                                              │
│       └──► [Trigger B] OrderConfirmed (orders plugin)                │
│              → workflow chequea: ¿ctwa_clid presente en episode?     │
│              → Sí + < 7 días → mandar Purchase (PRIORIDAD).          │
│              → Marca metadata.capi_event_sent = "Purchase"           │
│              → Si después llega otro Lead trigger → SKIP             │
│                                                                      │
│  Regla de oro: Purchase reemplaza a Lead. Una vez mandado Purchase, │
│  no se manda nada más para ese ctwa_clid.                           │
└────────────────────────────────────────────────────────────────────┘
```

### §17.2 Lead — cuándo dispararlo

**Disparar Lead cuando el customer cruza el umbral de "intención cualificada"** — NO al primer "Hola".

Triggers que cuentan:
- ✅ Customer pidió cotización explícita ("¿cuánto vale el kit de rosas?", "mándame el precio del envío a Cali").
- ✅ Customer pidió enviar info de pago/transferencia.
- ✅ Customer entregó datos de envío (dirección, contacto).
- ✅ Customer respondió afirmativamente a un quote (e.g., "ok, lo quiero").
- ✅ Customer reactivó episode via watchdog y respondió >1 turn.

Triggers que NO cuentan (no mandar Lead):
- ❌ Primer "Hola" sin contexto.
- ❌ "¿Aún está disponible?" sin cualificación posterior.
- ❌ Spam / consulta off-topic.
- ❌ Customer abrió y no respondió (timeout).

**Tracking sugerido** (Lead Response Management Study aplicado):
- Si entre clic del ad y primera respuesta del agente pasan **<5 min**, el lead converte ~100x más que >20 min. Mandar Lead apenas el customer entrega señal cualificada ayuda a Meta a optimizar para CTWA donde respondemos rápido.

### §17.3 Purchase — cuándo dispararlo

**Disparar Purchase cuando hay orden CONFIRMADA con pago.**

Trigger único:
- ✅ `OrderConfirmedEvent` emitido por `orders` plugin con `payment_status="confirmed"`.

Payload obligatorio:
- `value`: monto de la orden (numérico, en la moneda real cobrada).
- `currency`: ISO 4217 → para Colombia siempre `"COP"`.

> ⚠️ **Cuidado con currency.** Si la orden está en COP y mandás `value: 80000` con `currency: "USD"` → Meta computa $80,000 USD de revenue. Esto rompe el cost-per-purchase de tus ads catastróficamente. Doble-checkear en el activity de send.

### §17.4 Idempotencia y dedup

Meta dedupea events por `event_id`. Estrategia:

- **Lead** `event_id`: `lead_{episode_id}_{first_qualified_intent_at_ms}`
- **Purchase** `event_id`: `purchase_{order_id}` (el order_id es naturalmente único)

Si el activity falla y reintenta, el `event_id` es estable → Meta dedupea solo. **No** generar UUID random — pierde idempotencia.

### §17.5 Persistencia del ctwa_clid en metadata.json

El `ctwa_clid` lo necesitás **hasta 7 días después del clic** porque ese es el attribution window. Esquema sugerido en `metadata.json` del episode:

```yaml
attribution:
  ctwa_clid: "ARxxxxxxxxxxxxxxxxxx"            # del referral object
  ctwa_clid_received_at_ms: 1716800000000
  ctwa_clid_expires_at_ms: 1717404800000        # +7 días desde recibido
  capi_events:
    - event_name: "Lead"
      event_id: "lead_wa_+573001234567_ep_42_1716810000000"
      sent_at_ms: 1716810000000
      meta_received: true
  capi_terminal_event: "Purchase"               # si ya se mandó Purchase, no mandar más
```

> Este esquema es **propuesta** — el sprint de implementación CAPI (futuro HU) lo va a aterrizar. Por ahora basta con que vos sepas que el backend va a necesitar esto.

### §17.6 Cuándo NO mandar ningún CAPI event

- Episode sin `ctwa_clid` (vino por entrada orgánica, no por CTWA ad) → **no mandar nada**. Solo CTWA events alimentan al algoritmo de ads.
- `ctwa_clid_expires_at_ms` ya pasó (>7 días) → mandar es desperdicio, Meta no lo atribuye.
- Ya mandaste Purchase para ese ctwa_clid → no mandar Lead aunque ocurra después (el "1 event per ad event" limit hace que la lectura sea: el último gana, pero por seguridad, no lo retoques).

---

## §18 Variables de entorno y backend wiring

Después de §13-§16 tenés que setear estas env vars en el deploy de los workers:

```bash
# .env / k8s secret
META_CAPI_DATASET_ID="1234567890123456"       # del §13.3
META_CAPI_ACCESS_TOKEN="EAA..."               # del §14.2 — SECRET, no commitear
META_CAPI_TEST_EVENT_CODE=""                  # vacío en prod, set en staging para usar §16
WABA_ID="9876543210987654"                    # de tu WhatsApp Business Account
```

### §18.1 Files que el backend va a tocar (futuro sprint CAPI)

> Estos NO existen aún — son los placeholders que un próximo HU va a implementar. Te los listo para que sepas que el setup de §11-§16 va a ser consumido por código real.

- `hubara_agency/src/platform/whatsapp/capi.py` — DTOs (`CapiEvent`, `CapiUserData`, `CapiCustomData`) + función pura `build_capi_event(...)`.
- `hubara_agency/src/platform/whatsapp/activities.py` — nuevo `send_capi_event_activity` con retry policy non-retryable para 4xx (auth, malformed).
- `hubara_agency/src/plugins/chats/agent/sales/use_cases/ingest_inbound_message.py` — extraer `referral.ctwa_clid` y persistirlo en `metadata.json` del episode con `ctwa_clid_expires_at_ms = received + 7d`.
- `hubara_agency/src/plugins/chats/agent/sales/workflows/...` — listener para `IntentQualifiedEvent` → trigger Lead CAPI activity.
- `hubara_agency/src/plugins/orders/...` — al confirmar order, si episode tiene `ctwa_clid` vigente, trigger Purchase CAPI activity.
- `hubara_agency/src/platform/config.py` — sumar `META_CAPI_DATASET_ID`, `META_CAPI_ACCESS_TOKEN`, `META_CAPI_TEST_EVENT_CODE`.

> Hito recomendado: ese sprint CAPI implementa solo Lead + Purchase para CTWA. NO mezclar con web/app CAPI (es otro `action_source`, otro Pixel/Dataset si querés separar revenue por canal).

---

## §19 Troubleshooting CAPI

### §19.1 "Invalid OAuth access token" / 401

**Causa:** token mal pegado, expirado, o usás token de user en vez de System User.
**Fix:**
1. Volver a §14, regenerar el token.
2. Verificar que el deploy esté usando la env var actualizada (kubectl exec / docker exec y echo $META_CAPI_ACCESS_TOKEN).
3. Si persiste, asegurate que el token tenga scope `ads_management` (debería traerlo por default).

### §19.2 Event no aparece en Test events

**Causa:** `test_event_code` no enviado, o enviado en lugar incorrecto del payload.
**Fix:**
1. `test_event_code` va en el **body** del POST, NO en URL.
2. Va a NIVEL RAÍZ del payload, NO dentro de `data[]`.
3. Si llamás sin `test_event_code` → el event llega a producción directo (saltea Test Events panel y aparece en Overview con delay 20 min). Para smoke verificar inmediatamente, **siempre** usar test_event_code en staging.

### §19.3 Event llega pero Match Quality bajo (<6)

**Causa más común:** `ctwa_clid` ausente o vacío.
**Fix:**
1. Verificar en metadata.json del episode que efectivamente capturaste `referral.ctwa_clid` del primer webhook.
2. Si el primer webhook no traía `referral` → ese episode NO vino de un CTWA ad → NO mandar CAPI event (no aplica).
3. Si todos tus episodes vienen sin `referral` pero esperás que vinieran de ads: chequear que tu CTWA ad esté usando el ad format "Click to WhatsApp" (no "Send to messenger" o web link).

### §19.4 WABA no aparece en Connected Assets

**Causa:** la WABA no está en la misma Business Manager que el Dataset.
**Fix:**
1. Settings → Business Settings → WhatsApp Accounts — verificar que tu WABA aparezca y esté "Active".
2. Si está en otra BM → moverla o duplicar el Dataset en la BM correcta.

### §19.5 "This token has expired"

**Causa:** rotaste el token o Meta lo revocó.
**Fix:**
1. Volver a §14.2 → generar nuevo token.
2. Update env var en prod + restart workers.
3. **No te olvides** de actualizar el password manager.

### §19.6 EMQ alto, events llegan, pero ad no muestra purchases atribuidas

**Causa probable:** atribución toma 24-48h en aparecer en Ads Manager.
**Fix:**
1. Esperar 48h.
2. Verificar en Ads Manager que el ad esté usando el Pixel/Dataset correcto en su event tracking (Edit ad set → Conversion event).
3. Si configuraste el ad con "Lead" como conversion goal pero estás mandando solo "Purchase" eventos → cambiar el ad goal a "Purchase" o mandar Lead también (respetando §17.4 dedup).

---

## §20 Cheat sheet CAPI — todo en una pantalla

| Setting | Valor |
|---|---|
| Endpoint | `POST https://graph.facebook.com/v18.0/{dataset_id}/events` |
| Auth | `?access_token=...` (query param) o header `Authorization: Bearer ...` |
| `action_source` | `business_messaging` (siempre) |
| `messaging_channel` | `whatsapp` (siempre) |
| `user_data.whatsapp_business_account_id` | tu WABA_ID |
| `user_data.ctwa_clid` | del `referral.ctwa_clid` del primer webhook |
| Events soportados | `Lead`, `Purchase` (NO otros) |
| Attribution window | 7 días desde clic del ad |
| Eventos por ad click | 1 (el más fuerte gana — priorizar Purchase) |
| Currency Colombia | `COP` |
| `event_id` Lead | `lead_{episode_id}_{first_qualified_intent_ms}` |
| `event_id` Purchase | `purchase_{order_id}` |
| Smoke test | `test_event_code` en root del payload (NO en data[]) |

---

## §21 Cuándo activar CAPI en producción — checklist

Antes de poner el primer CTWA ad live:

- [ ] §11-§15 completos: Dataset creado, token generado, WABA linkeada.
- [ ] §16 verificado: smoke test pasó con curl, event aparece en Test events del panel.
- [ ] Backend implementado (sprint CAPI futuro): activities + persistencia ctwa_clid + triggers Lead/Purchase listos y mergeados.
- [ ] Env vars en deploy de prod: `META_CAPI_DATASET_ID`, `META_CAPI_ACCESS_TOKEN`, `META_CAPI_TEST_EVENT_CODE=""` (vacío en prod).
- [ ] Workers restart con nuevas env vars cargadas.
- [ ] Test E2E manual: spawneás un episode con ctwa_clid mock, disparás IntentQualifiedEvent, verificás que Lead event aparece en Events Manager Overview (sin test_event_code, espera ~20 min).
- [ ] EMQ score >7 en Lead events.
- [ ] CTWA ad live con conversion goal = Purchase (no Lead — empezás midiendo bottom funnel desde día 1).

---

## §22 Referencias CAPI

- Meta — [Conversions API for Business Messaging](https://developers.facebook.com/docs/marketing-api/conversions-api/business-messaging/)
- Meta — [Onboarding guide](https://developers.facebook.com/documentation/ads-commerce/conversions-api/business-messaging)
- Meta — [Conversions API overview](https://developers.facebook.com/docs/marketing-api/conversions-api/)
- Meta — [Dataset Quality API](https://developers.facebook.com/docs/marketing-api/conversions-api/dataset-quality-api/) (para monitorear EMQ programáticamente)
- HU-WA24H-001 refinement §0 (contexto CTWA) y §3 (cost tracking baseline — el integral con CAPI viene en sprint futuro).
- AsisteClick — [CTWA Ads 2026 guide](https://asisteclick.com/en/blog/click-to-whatsapp-ads-ctwa-conversion-2026/) (tactical complement).
