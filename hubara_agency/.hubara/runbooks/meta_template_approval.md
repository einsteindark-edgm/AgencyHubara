# Runbook — Aprobación de Templates en Meta Business Manager

> **Audience:** Vos (operador AgencyHubara) — alguien que NUNCA aprobó un template Meta antes.
> **Triggered when:** estás listo para activar el HU-WA24H-001 (watchdog + cadencia remarketing) y necesitás los 4 templates iniciales aprobados.
> **Tiempo estimado:** **45-60 min activos** (redactar + submit los 4). **24-72h pasivos** (esperando approval de Meta por template).
> **Cuándo arrancar:** YA. No hay razón para esperar. Cuanto antes empezás, antes tenés activable el watchdog en producción.

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
  Hola {{1}}, tu cotización para {{2}} ya está lista. ¿Querés que te la pase ahora?
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
Hola {{1}}, tu pedido {{2}} está {{3}}. Si querés más info contame por acá.
```

Samples:
- `{{1}}`: `Carlos`
- `{{2}}`: `#1042`
- `{{3}}`: `en camino con entrega estimada mañana`

Footer y Buttons vacíos.

**Step 4 — Submit**.

---

## §5 Template #4 — `cart_recovery_marketing` (MARKETING)

> **Lo que hace:** recuperar carrito abandonado fuera de ventana, con incentivo.
> **Cuándo se manda:** decisión consciente del LLM o cadencia (NUNCA por watchdog).
> **Costo:** $0.0125 USD siempre (es marketing).
> ⚠️ Este es el ÚNICO marketing del set inicial. Ser conservador con el copy — Meta es estricto con promos.

### §5.1 Repetir el wizard

Volver a **"Message templates" → "Create template"**.

**Step 1 — Category**: ahora **"Marketing"** ← DISTINTO de los anteriores.
- Sub-options: elegir **"Custom"** si te lo pide.

**Step 2**:
- **Name**: `cart_recovery_marketing`
- **Language**: `Spanish (COL)`

**Step 3 — Body**:
```
Hola {{1}}, dejaste {{2}} en tu carrito. Si te animás a volver, tenés {{3}} disponible. ¿Lo retomamos?
```

Samples:
- `{{1}}`: `Andrea`
- `{{2}}`: `vela aromática Patchouli`
- `{{3}}`: `envío gratis`

Footer y Buttons vacíos.

> ⚠️ **No prometer descuentos específicos en el copy si no es siempre cierto.** El sample dice "envío gratis" pero el agente puede después rellenar con "10% off" o lo que aplique. Meta evalúa el copy aprobado, no el sample.

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

## §10 Referencias

- Meta — Create message templates: https://developers.facebook.com/docs/whatsapp/business-management-api/message-templates/
- Meta — Template guidelines: https://developers.facebook.com/docs/whatsapp/business-management-api/message-templates/template-guidelines
- HU-WA24H-001 refinement §4.5 (template registry) y §7.0 (Fase 0 operacional).
