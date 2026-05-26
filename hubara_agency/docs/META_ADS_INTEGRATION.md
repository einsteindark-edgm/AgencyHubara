# Meta Ads Integration — Setup humano + plan de ejecución (v1, single-tenant Hubara)

> **Audiencia:** dos secciones, dos audiencias.
> - **Parte A (Fases 1–6):** vos (operador admin del negocio). Lo configurás una vez.
> - **Parte B (Fases 7–12):** el dev. Plan de código DEHA sobre el plugin `meta_ads` nuevo.
> **Objetivo:** llevar la sección `Ads` del dashboard de mocks a datos reales — campañas Meta + insights (spend / impressions / reach / clicks / CTR / CPM / CPC) + atribución CTWA → WhatsApp + ROAS/CAC reales cuando se integre con orders.
> **Pre-requisito:** ya tenés ejecutado `META_CATALOG_SETUP.md` Fases 1–12 (Business Portfolio + App + WABA + System User token con `whatsapp_business_management` + `catalog_management`). Lo que falta acá es **agregar `ads_read` al mismo System User y vincular el Ad Account al Business**.
> **Tiempo total humano:** 1–2 horas si ya tenés el Business Portfolio + Ads Manager con campañas en running. 1 día si arrancás de cero la cuenta publicitaria.
> **Versión Graph API:** v23.0 (matchea con `META_CATALOG_SETUP.md` y `src/platform/meta_catalog/client.py:32`).

---

## Tabla de contenidos

**PARTE A — Setup humano (1–2 horas)**

- [Fase 1 — Pre-flight: qué necesitás antes de tocar Meta](#fase-1)
- [Fase 2 — Conectar la Ad Account al Business Portfolio](#fase-2)
- [Fase 3 — Vincular WABA ↔ Ad Account (habilita CTWA en Marketing API)](#fase-3)
- [Fase 4 — Agregar `ads_read` al System User token](#fase-4)
- [Fase 5 — Verificar scopes con `debug_token`](#fase-5)
- [Fase 6 — Capturar IDs requeridos por el backend](#fase-6)

**PARTE B — Plan de ejecución dev (DEHA, ~3–4 días)**

- [Fase 7 — Inventario de métricas: lo que la UI ya pide](#fase-7)
- [Fase 8 — Mapping Meta API → modelo Hubara](#fase-8)
- [Fase 9 — Nuevo plugin `meta_ads` (skeleton DEHA)](#fase-9)
- [Fase 10 — Workflow + activities + snapshot durable](#fase-10)
- [Fase 11 — Merge en endpoint existente `/api/chats/ads/campaigns`](#fase-11)
- [Fase 12 — Daily series + Conversions API CTWA](#fase-12)
- [Fase 13 — Sugerencias del agente IA (DEFERRED — mencionado, no profundizar)](#fase-13)
- [Anexos — rate limits, troubleshooting, ADRs futuros](#anexos)

---

## PARTE A — Setup humano

## Fase 1 — Pre-flight: qué necesitás antes de tocar Meta
<a id="fase-1"></a>

> **Saltá esta fase si en `https://business.facebook.com/settings/ad-accounts` ya ves tu Ad Account listado y tiene campañas Click-to-WhatsApp corriendo.**

### 1.1 — Checklist de prerequisites

Debe estar TODO esto resuelto antes de seguir. Si falta alguno, retrocedé a `META_CATALOG_SETUP.md`:

- [ ] Business Portfolio creado y verificado (`META_CATALOG_SETUP.md` §Fase 1).
- [ ] App de Meta tipo Business creada (`META_CATALOG_SETUP.md` §Fase 2).
- [ ] WhatsApp Business Account (WABA) configurada y vinculada al Business Portfolio (`META_CATALOG_SETUP.md` §Fase 3-4).
- [ ] Business Verification aprobada (`META_CATALOG_SETUP.md` §Fase 5).
- [ ] System User existente con token permanente (`META_CATALOG_SETUP.md` §Fase 10-11).
- [ ] Las env vars `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN`, `META_SYSTEM_USER_TOKEN` ya en producción y working.

### 1.2 — Tu Ad Account existe?

Andá a `https://business.facebook.com/settings/ad-accounts`. Tres casos posibles:

**Caso A — Ya tenés Ad Account propio:** lo ves listado con un ID `act_XXXXXXXXXX`. Saltá a §Fase 2.

**Caso B — La Ad Account está en otra cuenta personal (no en el Business):** la creaste como persona, no como business. Tenés que reclamarla:
- En `Business Settings → Ad accounts`, click **"Add"** → **"Request access to an ad account"**.
- Pegá el `act_id` (lo ves en Ads Manager → URL bar).
- Esperá que el dueño actual (vos personalmente) acepte. Self-approve si es tu cuenta.

**Caso C — No tenés Ad Account creada:** la creás dentro del Business:
- `Business Settings → Ad accounts → Add → Create a new ad account`.
- **Name:** `Hubara Ads` (visible solo en admin, no en chat).
- **Time zone:** `America/Bogota` (Colombia). **CRÍTICO:** una vez creada, NO se puede cambiar. Si te equivocás, hay que crear otra y migrar.
- **Currency:** `COP` (peso colombiano). Mismo gotcha que time zone.
- **Payment method:** vincular tarjeta corporativa o cuenta bancaria (Meta exige al menos uno antes de aprobar primer ad).

### 1.3 — Tenés Click-to-WhatsApp ads corriendo?

El dashboard solo va a poblar datos para campañas que ya están en Meta. Si todavía no creaste ningún ad CTWA:

- Andá a `Ads Manager` → **Create**.
- **Objective:** `Engagement → Messages` (recomendado para CTWA — optimiza por conversaciones iniciadas).
- **Conversion location:** `Messaging apps → WhatsApp`.
- **WhatsApp Business Account:** seleccionar el WABA que ya tenés vinculado.
- **Audience / placement / creative:** lo que aplique al negocio. Para Hubara (velas aromáticas): `Mujeres 28-55 · Bogotá, Medellín, Cali` funcionó OK como baseline (ver mocks de `entities/ads-campaign/model.ts:170`).
- Publicar al menos UNA campaña con al menos UNA conversación recibida para tener data real para verificar.

> **Sin campañas activas, el dashboard sigue mostrando `<MissingField />`** — el backend escribiría un snapshot vacío y la UI lo refleja honestamente.

---

## Fase 2 — Conectar la Ad Account al Business Portfolio
<a id="fase-2"></a>

> **Saltá esta fase si en `Business Settings → Ad accounts` ya ves la cuenta listada Y tiene a `Hubara Ads System User` asignado como `Manage ad account`.**

### 2.1 — Verificar que el Business "owns" la Ad Account

- `https://business.facebook.com/settings/ad-accounts`
- Click sobre tu Ad Account → tab **"Owners"**.
- Tiene que decir tu Business Portfolio (Hubara), NO una cuenta personal.

Si dice cuenta personal: hay que pedir migration support a Meta (request via Business Help Center). Es la única forma — no se puede hacer desde la UI.

### 2.2 — Asignar el System User existente

- Misma pantalla → tab **"People"** o **"System users"**.
- Click **"Add people"** o **"Assign System User"**.
- Seleccionar `Hubara Ads System User` (el que ya creaste en `META_CATALOG_SETUP.md` §Fase 10).
- **Role:** seleccionar **`Manage ad account`** (suficiente para read insights + read campaigns). Si en el futuro querés que el agente IA pueda PAUSAR/CREAR ads, ahí escala a `Manage campaigns`.
- Click **"Assign"**.

### 2.3 — Confirmar acceso

- En el detalle del System User (`Business Settings → System Users → Hubara Ads System User`), tab **"Assigned Assets"**.
- Debe aparecer la Ad Account listada con role `Manage ad account` o superior.

---

## Fase 3 — Vincular WABA ↔ Ad Account (habilita CTWA en Marketing API)
<a id="fase-3"></a>

Este paso es el que **habilita que los `actions[onsite_conversion.messaging_*]`** aparezcan en el endpoint `/insights`. Sin esto, vas a ver `impressions` y `clicks` pero `actions=[]` siempre.

### 3.1 — En Ads Manager o Business Settings

- `Business Settings → Accounts → WhatsApp Accounts`.
- Seleccionar tu WABA.
- Tab **"Connected assets"** o **"Linked accounts"**.
- Confirmar que la Ad Account está listada. Si no:
  - Click **"Add asset" → "Ad account"**.
  - Seleccionar tu Ad Account.
  - Confirmar.

### 3.2 — Smoke test del link

- Crear (o pausar y reactivar) un ad CTWA en Ads Manager.
- El selector **"WhatsApp number"** dentro del flow de creación debe mostrarte el número de tu WABA. Si no aparece, el link NO está hecho.

---

## Fase 4 — Agregar `ads_read` al System User token
<a id="fase-4"></a>

El token de System User que generaste en `META_CATALOG_SETUP.md` §Fase 11 tiene 4 scopes (`whatsapp_business_management`, `whatsapp_business_messaging`, `catalog_management`, `business_management`). Para Marketing API necesitás añadir **`ads_read`** (lectura suficiente, no necesitamos `ads_management` write-access en v1).

### 4.1 — Generar token nuevo con scope ampliado

- `Business Settings → System Users → Hubara Ads System User`.
- Click **"Generate new token"**.
- **App:** seleccionar la misma App que usaste para el catálogo.
- **Token expiration:** `Never` (System User tokens son permanentes — si ponés `60 days` te toca rotar).
- **Permissions (scopes):** marcar TODOS estos. Los primeros 4 ya los tenías, agregás 2 nuevos:
  - `whatsapp_business_management`
  - `whatsapp_business_messaging`
  - `catalog_management`
  - `business_management`
  - **`ads_read`** ← NUEVO (lectura de campañas, ad sets, ads, insights)
  - `pages_read_engagement` (opcional — útil si más adelante queremos leer engagement de los ads en Facebook Page)

> **No marcar `ads_management`** todavía. Es write access. La regla DEHA: dale a Hubara el mínimo permiso necesario (R-STATELESS no aplica acá pero el principio de "least privilege" sí). Si en el futuro el agente IA quiere pausar/duplicar campañas, escalá ahí.

- Click **"Generate"**.
- **CRÍTICO:** Meta muestra el token UNA SOLA VEZ. Copialo a un password manager o anotalo. Si lo perdés, no podés recuperarlo — tenés que generar otro nuevo (y los servicios viejos quedan con token inválido).

### 4.2 — Decisión: reemplazar `META_SYSTEM_USER_TOKEN` o crear `META_ADS_TOKEN` separado?

Dos opciones, recomendamos la primera:

**Opción A (recomendada) — UN solo token con todos los scopes:**
- Reemplazar `META_SYSTEM_USER_TOKEN` en el .env (y en `hubara-meta-secret` de k8s) por el nuevo.
- El backend lo lee desde `os.environ["META_SYSTEM_USER_TOKEN"]` para ambos clientes (catalog + ads).
- Menos secretos que rotar.

**Opción B — Tokens separados (`META_SYSTEM_USER_TOKEN` para catalog, `META_ADS_TOKEN` para ads):**
- Útil si querés blast-radius más pequeño en caso de leak (un token compremetido afecta solo un dominio).
- Cuesta más operacional.
- **Aplicar solo si tu compliance/seguridad lo exige.**

Para Hubara v1 → Opción A.

---

## Fase 5 — Verificar scopes con `debug_token`
<a id="fase-5"></a>

Sanity check antes de pasarle el token al dev.

### 5.1 — Con `curl`

```bash
export META_TOKEN="<paste_token_here>"
export META_APP_ID="<your_app_id>"            # Business Settings → Apps → tu App → App ID
export META_APP_SECRET="<your_app_secret>"    # Business Settings → Apps → tu App → App Secret

curl -s "https://graph.facebook.com/v23.0/debug_token?input_token=${META_TOKEN}&access_token=${META_APP_ID}|${META_APP_SECRET}" | jq
```

### 5.2 — Output esperado

```json
{
  "data": {
    "app_id": "1234567890",
    "type": "SYSTEM_USER",
    "application": "Hubara WhatsApp + Catalog + Ads",
    "data_access_expires_at": 0,
    "expires_at": 0,                                          // 0 = never
    "is_valid": true,
    "scopes": [
      "whatsapp_business_management",
      "whatsapp_business_messaging",
      "catalog_management",
      "business_management",
      "ads_read"                                              // ← debe estar
    ],
    "user_id": "987654321"
  }
}
```

**Si `ads_read` NO aparece:** generaste el token sin marcar el scope. Volver a §Fase 4.1.

**Si `is_valid: false`:** token mal pegado o expirado. Regenerar.

**Si `expires_at` no es 0:** generaste un short-lived token. Regenerar con expiration `Never`.

### 5.3 — Probar que ve la Ad Account

```bash
curl -s "https://graph.facebook.com/v23.0/me/adaccounts?access_token=${META_TOKEN}" | jq
```

Esperado:
```json
{
  "data": [
    {
      "account_id": "1234567890123456",
      "id": "act_1234567890123456"
    }
  ],
  "paging": { ... }
}
```

**Si `data: []`:** el System User NO está asignado a ninguna Ad Account. Volver a §Fase 2.2.

---

## Fase 6 — Capturar IDs requeridos por el backend
<a id="fase-6"></a>

El dev necesita estos IDs para el .env. Capturalos ahora y mandalos por canal seguro (1Password / sealed envelope).

### 6.1 — IDs a capturar

| Variable | Cómo capturarlo | Ejemplo |
|---|---|---|
| `META_AD_ACCOUNT_ID` | `Business Settings → Ad accounts → click la cuenta → "Account ID"`. NO incluir el prefijo `act_`. | `1234567890123456` |
| `META_BUSINESS_ID` | `Business Settings → Business info → "Business Manager ID"` | `100012345678901` |
| `META_PIXEL_ID` | (Opcional, para Fase 12 CTWA Conversions API.) `Events Manager → Data Sources → Pixel`. Si nunca creaste un Pixel para tu Business, saltá esto — la integración base no lo requiere. | `1098765432109876` |
| `META_SYSTEM_USER_TOKEN` | El que generaste en §Fase 4.1 (ahora con `ads_read` incluido). | `EAAxxxxxxxxxxxx...` |

### 6.2 — Confirmar timezone y currency

El backend asume `America/Bogota` + `COP`. Si tu Ad Account está configurada distinto (por algún Caso B/C raro de Fase 1.2), avisalo al dev — el mapper de insights y los formatters del dashboard (frontend `lib/format.ts`) asumen estos valores.

### 6.3 — Entregables al dev

Mensaje al dev (por canal seguro):

```
Meta Ads listo. IDs:
- META_AD_ACCOUNT_ID=1234567890123456
- META_BUSINESS_ID=100012345678901
- META_PIXEL_ID=1098765432109876   (deferred a Fase 12)
- Token: actualizado (mismo META_SYSTEM_USER_TOKEN), ahora con ads_read.

Verificado con debug_token: scopes correctos.
Verificado con /me/adaccounts: la Ad Account aparece.

Timezone: America/Bogota. Currency: COP.
Hay 6 campañas CTWA active running.
```

---

# PARTE B — Plan de ejecución dev (DEHA)

## Fase 7 — Inventario de métricas: lo que la UI ya pide
<a id="fase-7"></a>

El frontend ya tiene un contrato exhaustivo (Zod schema + TypeScript interface) en `frontend_dashboard/src/entities/ads-campaign/`. Todos los campos faltantes están `nullable()` con un `<MissingField />` marker. **No hay cambios en el frontend para que la integración funcione** — solo el backend deja de mandar `null`.

### 7.1 — Inventario completo de fields esperados

**`AdsCampaign` (mock canónico: [`entities/ads-campaign/model.ts:67-100`](../../frontend_dashboard/src/entities/ads-campaign/model.ts)):**

| Field | Tipo | Hoy hace | Fuente requerida |
|---|---|---|---|
| `id` | `string` | OK | vault (source_id de WhatsApp referral) |
| `name` | `string \| null` | OK (headline más reciente) | Meta `/campaign/{id}.name` (override del headline) |
| `started` | `number` | OK (count de episodios) | vault o Meta `actions[onsite_conversion.messaging_conversation_started_7d]` |
| `dates` | `string` (formatted) | OK | derivado de Meta `start_time` + `stop_time` |
| `status` | `"active" \| "paused" \| null` | NULL | Meta `effective_status` (ACTIVE / PAUSED / DELETED / ARCHIVED) |
| `objective` | `string \| null` | NULL | Meta `objective` (OUTCOME_ENGAGEMENT / OUTCOME_SALES / etc.) |
| `placement` | `string \| null` | NULL | Meta `/insights` con `breakdowns=publisher_platform,platform_position`, summarized |
| `audience` | `string \| null` | NULL | Meta `/adset/{id}.targeting` JSON, summarized |
| `daysRun` | `number \| null` | NULL | derivado: `(today - start_time).days` |
| `metaCampaignId` | `string \| null` | NULL | Meta `/campaign/{id}.id` (override del source_id que hoy ya matchea) |
| `adSet` | `string \| null` | NULL | Meta `/adset/{id}.name` (primer ad set activo de la campaña) |
| `creativeTitle` | `string \| null` | NULL | Meta `/ad/{id}/adcreatives.object_story_spec.link_data.message` o `title` |
| `template` | `string \| null` | NULL | vault (no Meta — es la plantilla WA usada en el primer outbound del bot) |
| `spend` | `number \| null` | NULL | Meta `/insights.spend` |
| `impressions` | `number \| null` | NULL | Meta `/insights.impressions` |
| `reach` | `number \| null` | NULL | Meta `/insights.reach` |
| `clicks` | `number \| null` | NULL | Meta `/insights.clicks` |
| `conversations` | `AdsConversationCounts \| null` | OK (counts por estado de WA) | vault (clasificador ya implementado) |
| `revenue` | `number \| null` | NULL | orders plugin (futuro, Fase posterior) |
| `avgTicket` | `number \| null` | NULL | orders plugin (futuro) |
| `firstResp` | `string \| null` | NULL | vault (derivar del JSONL: timestamp primer outbound - primer inbound) |
| `tendency` | `"up" \| "flat" \| "down" \| null` | NULL | Meta `/insights` time-series: comparar `last_7d` vs `7d_before_last_7d` |

**`AttributedConversation` (mock: [`entities/ads-campaign/model.ts:112-145`](../../frontend_dashboard/src/entities/ads-campaign/model.ts)):**

Ya está todo OK desde el backend WA. Solo faltan `name` (CRM), `city` (CRM), `value` (orders) — fuera de scope de esta integración Meta.

**`AdsDailyPoint` (mock: [`entities/ads-campaign/model.ts:149-159`](../../frontend_dashboard/src/entities/ads-campaign/model.ts)):**

Sigue siendo mock. Implementación real en §Fase 12.1.

### 7.2 — KPIs derivados en frontend (no pedir al backend)

Los siguientes se calculan en `AdsOverviewHeader.tsx` + `AdsInspector.tsx` + `AdsFunnel.tsx` y NO viajan por el wire. Solo necesitan que sus inputs no sean null:

| KPI | Fórmula | Inputs |
|---|---|---|
| ROAS | `revenue / spend` | `revenue`, `spend` |
| CAC | `spend / won` | `spend`, `conversations.ganado` |
| CTR | `clicks / impressions` | `clicks`, `impressions` |
| CPM | `(spend / impressions) * 1000` | `spend`, `impressions` |
| CPC | `spend / clicks` | `spend`, `clicks` |
| Costo por chat | `spend / started` | `spend`, `started` |
| Win rate | `won / total_convos` | `conversations.ganado`, `conversations.*` |
| Reply rate | `1 - no_reply/total` | `conversations.*` |
| Qualified rate | `(cal + cot + won)/total` | `conversations.*` |
| Click → chat | `started / clicks` | `started`, `clicks` |
| Spend/día | `spend / daysRun` | `spend`, `daysRun` |

### 7.3 — Fórmulas explicadas en profundidad

Cada KPI responde a una pregunta de negocio concreta. Esta sub-sección explica **por qué la fórmula es la que es**, **de dónde sale cada input**, **cómo interpretarla** (bandas malo/OK/bueno calibradas para CTWA + ecommerce LATAM) y **qué edge cases** maneja el código existente. La referencia canónica del código está en los componentes citados — esta sección NO inventa cálculos, los DOCUMENTA.

Los KPIs están agrupados en 4 familias: **(A) Costos**, **(B) Retornos**, **(C) Tasas del embudo**, **(D) Valor del pipeline**.

#### A. Costos — ¿cuánto pago por cada cosa?

Todas las métricas de costo en este dashboard se derivan de **`spend`** (lo que Meta cobró) dividido por la **unidad económica relevante** (mil impresiones, un click, un chat, una venta…). La regla universal de la división: si el denominador es 0 o falta, el componente muestra `<MissingField />` en vez de pintar `∞` o `0`.

**A.1 — CPM (Costo por mil impresiones)** · `AdsInspector.tsx:56-59`

```
CPM = (spend / impressions) × 1000
```

- **Pregunta:** ¿cuánto me cuesta poner mi ad en frente de 1.000 ojos?
- **Inputs:** ambos vienen de Meta `/insights` (`spend`, `impressions`). No depende del vault ni de orders.
- **Por qué multiplicar por 1000:** convención de la industria publicitaria (los CPMs se cotan en miles porque los costos individuales son centavos). Meta también devuelve `cpm` directo en `/insights.cpm` — el cálculo local es para tener control de banding y formato de moneda.
- **Interpretación CTWA Colombia (referencia, mayo 2026):**
  - Bueno: < $10.000 COP. Tu audience es eficiente para Meta.
  - OK: $10.000–25.000 COP.
  - Malo: > $25.000 COP. Audience muy nicho o muy competitiva — Meta tiene que pujar caro para mostrarte.
- **Edge case (código):** si `impressions == 0` (campaña recién publicada, learning phase), `CPM = null` → `<MissingField />`.

**A.2 — CPC (Costo por clic)** · `AdsInspector.tsx:60-63`

```
CPC = spend / clicks
```

- **Pregunta:** ¿cuánto pago cada vez que alguien hace tap en el ad?
- **Inputs:** ambos de Meta `/insights`.
- **Diferencia con `cost_per_unique_click`:** el `clicks` incluye repeticiones (la misma persona haciendo 3 taps cuenta 3). Si querés deduplicar, mapear `unique_clicks` y calcular `spend / unique_clicks`. La industria CTWA usa el primero como default.
- **Interpretación CTWA Colombia:**
  - Bueno: < $400 COP. Creative fuerte.
  - OK: $400–800 COP.
  - Malo: > $800 COP. Headline o image no enganchan, o audience indiferente.
- **Por qué importa:** CPC alto + CTR bajo = creative malo. CPC alto + CTR bueno = audience cara. La combinación con A.3 (costo por chat) te dice CUÁL de los dos.

**A.3 — Costo por chat iniciado** · `AdsInspector.tsx:64-65` + `AdsOverviewHeader.tsx:48-51`

```
costPerChat = spend / started
```

- **Pregunta:** ¿cuánto pago por cada conversación REAL que arranca (cliente que realmente escribió el primer mensaje)?
- **Inputs:**
  - `spend` → Meta `/insights`.
  - `started` → **vault** (count de episodios atribuidos a la campaña — ground truth, ver §A.2 del Anexo). NO usar `actions[onsite_conversion.messaging_conversation_started_7d]` porque Meta cuenta el "click que abre el flow" como conversation, aunque el cliente cierre sin escribir.
- **Por qué es más honesta que CPC para CTWA:** el CPC mide intent superficial (tap). El costo por chat mide intent real (cliente que se tomó el trabajo de escribir). La industria reporta que **~90% de los clicks NO terminan en mensaje** (fuente: ver Anexo A.2). Por eso `costPerChat` típicamente es 5–10× el CPC.
- **Interpretación CTWA Colombia (ticket $100K-200K COP):**
  - Bueno: < $3.000 COP por chat iniciado.
  - OK: $3.000–8.000 COP.
  - Malo: > $8.000 COP. Plantilla de bienvenida muy débil, o cliente abandona en el primer mensaje (revisar `firstResp`).
- **Edge case:** si `started == 0` pero `spend > 0` (campaña con plata gastada y cero chats), `costPerChat = null` para no dividir entre cero. El frontend muestra `<MissingField />`, pero ese estado es **una señal de alarma fuerte** — la campaña está quemando plata sin generar conversaciones.

**A.4 — CAC / CPA (Costo de adquisición por cliente)** · `AdsOverviewHeader.tsx:44-47` + `AdsInspector.tsx:66-67`

```
CAC = spend / won           # won = conversations.ganado
```

- **Pregunta:** ¿cuánto me cuesta CADA cliente que efectivamente compró?
- **Inputs:**
  - `spend` → Meta.
  - `won` → vault (count de episodios con state="ganado" — los marcó el classifier conversacional cuando se cerró tag=GANADO).
- **Por qué tanto el OverviewHeader (`CAC`) como el Inspector (`CPA`) usan la misma fórmula:** son sinónimos. Marketing tradicional dice "CAC" (Customer Acquisition Cost), Meta Ads UI dice "CPA" (Cost Per Acquisition). Decidimos rotular CAC en el header (visión negocio) y CPA en el panel de métricas Meta (matchea el vocabulario de Ads Manager para que el operador no se confunda).
- **Interpretación (regla universal):** `CAC < avgTicket × margenBruto`. Si tu margen bruto es 50% y tu ticket promedio $150K, tu CAC tiene que ser < $75K. Sino vendés con pérdida.
- **Combinación crítica:** CAC + ROAS son los dos KPIs que deciden si una campaña es viable. ROAS te da el ratio de retorno (cualquier campaña con ROAS > 1 "gana plata bruta"), CAC te da el costo absoluto (importante si tu LTV — lifetime value — es alto: podés tolerar CAC alto si el cliente vuelve a comprar).
- **Edge case:** si `won == 0` (campaña sin ventas todavía), `CAC = null`. **NO confundir con `null` por estado pendiente** — el componente distingue: si `spend` también es `null` muestra "datos pendientes Meta", si `spend > 0 && won == 0` muestra "—" con tooltip "Aún sin ventas atribuidas".

**A.5 — Spend/día** · `AdsOverviewHeader.tsx:53-56`

```
spendPerDay = spend / daysRun
```

- **Pregunta:** ¿a qué ritmo está quemando presupuesto la campaña?
- **Inputs:**
  - `spend` → Meta (acumulado del período).
  - `daysRun` → derivado de Meta `start_time` (días desde que arrancó hasta hoy).
- **Por qué es útil:** Meta te muestra `daily_budget` (cuánto pediste gastar por día), pero la entrega real puede ser menor (audience saturada) o mayor (Meta entrega con un buffer ±25% sobre el daily_budget cuando hay demanda). `spendPerDay` te da el gasto REAL promedio.
- **Detección de underdelivery:** si `spendPerDay << daily_budget` → la campaña no está saliendo a todo lo que pagaste. Causas: bid demasiado bajo, audience demasiado nicho, creative en learning phase.
- **Edge case:** `daysRun = 0` (campaña recién lanzada hoy) → `spendPerDay = null`. Esperar 24h antes de evaluar.

#### B. Retornos — ¿qué me devuelve cada peso invertido?

**B.1 — ROAS (Return on Ad Spend)** · `AdsCampaignsList.tsx:126-129` + `AdsOverviewHeader.tsx:40-43`

```
ROAS = revenue / spend
```

- **Pregunta:** ¿por cada $1 que le di a Meta, cuántos $ volvieron como ingresos atribuidos?
- **Inputs:**
  - `spend` → Meta `/insights.spend`.
  - `revenue` → **plugin `orders`** (futuro). Suma de `total_amount` de todas las orders cuya sesión WhatsApp tenga `origin.source_id == campaign_id`. Hoy el backend lo devuelve `null` hasta que se integre el join orders↔chats.
- **Por qué se reporta como `Nx` y no como porcentaje:** convención de la industria. Un ROAS de `3.5×` significa "por cada peso invertido recuperás 3.5 pesos en ingresos brutos". El `300%` sería equivalente pero menos legible.
- **Atención — bruto vs neto:** este ROAS es **bruto** (ingresos / spend), no neto (margen / spend). Si tu margen bruto es 40%, un ROAS de 2× significa que apenas estás empatando (40% × 2× = 80% recuperación). Para Hubara (velas) el target real es **ROAS > 2.5×** para que sea rentable después de margen + impuestos + logística.
- **Banding visual del frontend** (`AdsCampaignsList.tsx:183-191`):
  - **Verde (`pos`):** ROAS ≥ 2.0×. Campaña rentable.
  - **Amarillo (`neu`):** ROAS 1.0–1.99×. Empata o subsidia — depende del LTV del cliente para decidir si pausarla.
  - **Rojo (`neg`):** ROAS < 1.0×. Cada peso da menos de un peso. Pausar o iterar creative urgente.
- **Edge case crítico:** si `spend == 0` (campaña paused o nunca delivered), `ROAS = null`. **NO retornar `∞`** (división por cero) ni `0` (eso parece "pésima"). El código explícitamente checkea `spend > 0` antes de dividir.
- **Edge case revenue null:** si el backend no tiene aún el join orders↔chats, `revenue = null` → `ROAS = null`. Por eso es **prerequisito de Fase 12.2** (Conversions API CTWA) tener orders integrado, sino el dashboard sigue mostrando `<MissingField />` en el slot estrella.

**B.2 — Ticket promedio (avgTicket)** · viene directo del backend (no se calcula en frontend)

```
avgTicket = sum(order.total) / count(orders_atribuidos)    # backend
```

- Fuente: plugin `orders` (futuro). Backend lo devuelve precomputado para evitar tener que mandar todas las orders por el wire.
- Se usa como input para el cálculo de **Pipeline Value** (familia D abajo).

#### C. Tasas del embudo — ¿qué porcentaje avanza en cada paso?

El embudo CTWA→WhatsApp tiene 6 etapas: **Impresión → Click → Chat iniciado → En conversación → Cotizado → Ganado**. Cada tasa te dice qué % sobrevive de una etapa a la siguiente.

**C.1 — CTR (Click-through rate)** · `AdsInspector.tsx:43-46`

```
CTR = clicks / impressions
```

- **Pregunta:** ¿qué % de la gente que vio mi ad le hizo tap?
- **Inputs:** ambos de Meta. Meta también devuelve `ctr` directo — el cálculo local es para tener el ratio puro como fracción (0–1) en vez del porcentaje formateado.
- **Por qué es la métrica de "creative health":** CTR refleja **únicamente** qué tan buena es la combinación creative + headline + audience. No depende del bot, no depende del producto. Si el CTR está mal, el problema está en lo que ve el cliente ANTES de tocar el ad.
- **Interpretación CTWA Colombia:**
  - Bueno: > 2.0%. Headline + image alineados con la audience.
  - OK: 1.0–2.0%.
  - Malo: < 1.0%. Audience equivocada, image floja, o headline genérico.
- **Edge case:** `impressions == 0` → CTR null. La UI muestra `<MissingField />`.

**C.2 — Click → Chat iniciado (startedRate)** · `AdsInspector.tsx:47-49`

```
startedRate = started / clicks
```

- **Pregunta:** de los que hicieron tap en el ad, ¿qué % efectivamente escribió el primer mensaje en WhatsApp?
- **Inputs:**
  - `clicks` → Meta.
  - `started` → vault (count de sessions WA con `origin.source_id == campaign_id`).
- **Por qué es la métrica más subestimada del CTWA:** la industria reporta que **solo ~10% de los clicks terminan en mensaje** (el resto: usuario abre WhatsApp y cierra, usuario no completa el flow de abrir el chat, usuario hace tap por accidente). Si tu `startedRate` < 10%, hay fricción en el handoff. Si está > 20%, tu pre-fill message o tu plantilla de apertura son muy buenos.
- **Edge case:** `clicks == 0` (anuncio nunca recibió taps — algo muy mal con el creative) → null.

**C.3 — Reply rate (Respuesta a plantilla)** · `AdsInspector.tsx:50-51`

```
replyRate = 1 - (no_reply / total)
```

donde `total = sum(conversations.*)` (suma de los 7 estados).

- **Pregunta:** de los clientes que iniciaron chat, ¿qué % al menos respondieron a algo (no quedaron en silencio después del primer mensaje del bot)?
- **Inputs:** todos del vault (classifier conversacional).
- **Por qué `1 - no_reply/total` y no `(total - no_reply)/total`:** matemáticamente idénticos. Se escribió así para hacer obvio que el resultado es "el complemento del bounce rate".
- **Interpretación:** un `no_reply` alto indica que la **plantilla de bienvenida del bot** asusta o no engancha. El cliente arrancó el chat con intención (escribió "Hola"), recibió la respuesta del bot, y se fue. Es una de las palancas más altas de mejora.
- **Edge case:** `total == 0` (campaña sin chats) → null.

**C.4 — Qualified rate** · `AdsInspector.tsx:52-54`

```
qualifiedRate = (calificado + cotizado + ganado) / total
```

- **Pregunta:** ¿qué % de las conversaciones pasaron el filtro de "cliente serio" (al menos llegaron a calificar interés)?
- **Por qué excluye `nuevo` y `activo`:** esas dos categorías significan "el cliente todavía está conversando, pero no demostró intención de compra concreta". `calificado` es el primer estado donde el classifier dice "este cliente sí va en serio".
- **Inputs:** todos del vault (`conversations.*`).
- **Interpretación:** es un indicador de "calidad de la audience". Audience mal targetada → mucho `nuevo`/`activo` que nunca avanza. Audience bien targetada → `qualifiedRate` > 30%.

**C.5 — Win rate (% Ganados)** · `AdsOverviewHeader.tsx:52` + `AdsInspector.tsx:49`

```
winRate = won / total
```

- **Pregunta:** ¿qué % de TODOS los chats iniciados terminó en venta?
- **Inputs:** vault.
- **Diferencia con `qualifiedRate`:** este es el rate del fondo del embudo (la venta efectiva). `qualifiedRate` es el rate del medio (interés validado).
- **Interpretación CTWA Hubara (ecommerce ticket bajo, ciclo < 24h):**
  - Bueno: > 7%. El producto se vende casi solo, el bot está bien.
  - OK: 3–7%.
  - Malo: < 3%. O el producto no resuena (audience mala), o el bot pierde clientes en el cierre.

**C.6 — Conversión global del embudo** · `AdsFunnel.tsx:91`

```
overallConv = won / impressions
```

- **Pregunta:** de TODAS las personas que vieron el ad, ¿qué fracción terminó comprando?
- **Por qué se reporta con 3 decimales en lugar de 2** (`fmtPct(overallConv, 3)`): el número es naturalmente diminuto. Una campaña sana de CTWA tiene ~0.03% de conversión global. 2 decimales mostraría `0.00%` para campañas reales que SÍ están vendiendo.
- **Útil para comparación entre campañas con misma audience:** si A tiene `overallConv = 0.05%` y B tiene `0.02%`, A es 2.5× más eficiente extremo a extremo aunque ambas tengan ROAS parecidos (porque B podría estar vendiendo a clientes con ticket más alto).

**C.7 — Conversión paso-a-paso (stepConv)** · `AdsFunnel.tsx:110`

```
stepConv[i] = stages[i].value / stages[i-1].value
```

- **Pregunta:** entre la etapa N-1 y la N, ¿qué % avanzó?
- **Inputs:** los counters de cada etapa (impressions → clicks → started → in_conversation → cotizado → ganado).
- **Para qué sirve:** identificar **el cuello de botella**. Si todos los step rates están alrededor de 30% excepto uno que está en 5%, ese paso es el problema. Por ejemplo:
  - Click → Chat = 5% → problema en el handoff (WhatsApp prefill, plantilla apertura).
  - Cotizado → Ganado = 10% → problema en el cierre (precio alto, fricción de pago).
- **Edge case:** stage `i-1` con valor 0 → `stepConv = 1` (default neutro para no romper el render).

#### D. Valor del pipeline — ¿cuánto vale lo que tengo en curso?

El componente `AdsStateDistribution.tsx` proyecta el valor económico esperado del pipeline ponderando cada chat por su probabilidad de cierre.

**D.1 — Probabilidad de cierre por estado (PIPELINE_WEIGHT)** · `AdsStateDistribution.tsx:28-36`

```typescript
const PIPELINE_WEIGHT: Record<AdsState, number> = {
  no_reply:   0.00,    // perdido implícito — cliente nunca contestó
  nuevo:      0.15,    // arrancó conversación pero apenas
  activo:     0.30,    // está conversando — 1 de cada 3 cierra
  calificado: 0.55,    // mostró interés concreto — coin flip favorable
  cotizado:   0.75,    // pidió precio — muy probable cierre
  ganado:     1.00,    // ya cerró
  perdido:    0.00,    // explícitamente descartado
};
```

- **De dónde salen estos números:** **CALIBRACIÓN HEURÍSTICA**, no datos reales aún. Son targets de funnel típicos para ecommerce LATAM ticket medio. Cuando se acumule data real (3+ meses de campañas con orders linkeadas) **estos pesos deben recalibrarse** con las tasas observadas reales — idealmente per-tenant.
- **Por qué `no_reply` y `perdido` valen 0:** ambos son sinks del embudo. `no_reply` raramente revive sin un push remarketing (que ya es otra campaña). `perdido` es el classifier marcando "este chat no va a cerrar".
- **Por qué `ganado = 1.00`:** ya cerró. El valor real es el ticket que pagó (no estimado, sino REAL — viene del orders link).
- **TODO técnico (deferred):** versionar `PIPELINE_WEIGHT` con un commit + dejar logueado en `progress-log/` cuando se recalibre — sino el dashboard arroja valores históricos no comparables después de un cambio.

**D.2 — Valor estimado por estado** · `AdsStateDistribution.tsx:113`

```
estimatedValueByState[s] = count[s] × avgTicket × PIPELINE_WEIGHT[s]
```

- **Pregunta:** si los chats en el estado `s` terminaran cerrando con su probabilidad histórica, ¿cuánto plata representan?
- **Inputs:**
  - `count[s]` → vault (classifier).
  - `avgTicket` → orders plugin (futuro).
  - `PIPELINE_WEIGHT[s]` → constante calibrada (§D.1).
- **Ejemplo concreto:** 32 chats `calificado` + `avgTicket = $156.000` + `weight = 0.55` → valor esperado = **$2.745.600 COP**.
- **Interpretación:** sirve para responder "¿deberíamos seguir invirtiendo en esta campaña?". Si el pipeline expected value está creciendo > spend, sí. Si está plano, no.
- **Edge case:** `avgTicket == null` (orders no integrado) → la celda muestra `<MissingField />`. Los `count` y `%` SÍ se pintan aunque el valor estimado falte (información parcial pero útil).

**D.3 — Tendency (up/flat/down)** · backend-derived, no hay fórmula en frontend

El backend marca `tendency` comparando 2 ventanas:

```
tendency = sign(metric(last_7d) - metric(7d_before_last_7d))
```

- **`metric`:** la métrica primaria de la campaña — recomendamos `started` (chats iniciados) porque es el indicador más temprano de salud. Alternativas válidas: `revenue`, `won`. Decidir en Fase 10 de la implementación.
- **`sign`:** > +10% relativo → `up`. < -10% → `down`. Entre ±10% → `flat`. El threshold ±10% es para evitar noise estadístico en campañas pequeñas.
- **Por qué viene del backend y no del frontend:** requiere acceso a las dos ventanas de tiempo, que solo el snapshot Meta + vault rollup tienen. Se calcula en el use case `merge_with_chats.py` (Fase 11).

#### Tabla resumen — fórmulas vs fuentes de datos

| KPI | Fórmula | Inputs Meta | Inputs vault | Inputs orders | Banding |
|---|---|---|---|---|---|
| CPM | `spend/impressions × 1000` | ✓ ✓ | — | — | ✓ |
| CPC | `spend/clicks` | ✓ ✓ | — | — | ✓ |
| Costo/chat | `spend/started` | ✓ | ✓ (started) | — | ✓ |
| CAC | `spend/won` | ✓ | ✓ (ganado) | — | depende de avgTicket |
| Spend/día | `spend/daysRun` | ✓ ✓ | — | — | — |
| ROAS | `revenue/spend` | ✓ | — | ✓ | ✓ (2.0×/1.0×) |
| CTR | `clicks/impressions` | ✓ ✓ | — | — | ✓ |
| Started rate | `started/clicks` | ✓ | ✓ | — | ~10% baseline |
| Reply rate | `1 - no_reply/total` | — | ✓ | — | — |
| Qualified rate | `(cal+cot+won)/total` | — | ✓ | — | — |
| Win rate | `won/total` | — | ✓ | — | ✓ |
| OverallConv | `won/impressions` | ✓ | ✓ | — | — |
| StepConv | `stage[i]/stage[i-1]` | mixto | mixto | — | — |
| Pipeline value | `n × avgTicket × weight` | — | ✓ | ✓ | — |
| Tendency | sign(Δ últimas 2 ventanas) | ✓ | ✓ | — | — |

**Lectura de la tabla:** una vez que llegue Meta API (Fases 9–11), las primeras 5 filas se desbloquean. ROAS y Pipeline value requieren ADEMÁS el join con orders (Fase 12.2 o posterior). Todo lo del vault ya funciona hoy.

---

## Fase 8 — Mapping Meta API → modelo Hubara
<a id="fase-8"></a>

### 8.1 — Endpoints Meta a consumir

| Endpoint | Para qué | Fields recomendados |
|---|---|---|
| `GET /v23.0/act_{aid}/campaigns` | Lista campañas | `id,name,status,effective_status,objective,start_time,stop_time,daily_budget,lifetime_budget,bid_strategy,buying_type,special_ad_categories,created_time,updated_time` |
| `GET /v23.0/act_{aid}/adsets?filtering=[{"field":"campaign.id","operator":"EQUAL","value":"<cid>"}]` | Ad sets de una campaña (para `audience` + `adSet` name) | `id,name,targeting,optimization_goal,billing_event,daily_budget` |
| `GET /v23.0/act_{aid}/ads?filtering=[{"field":"campaign.id","operator":"EQUAL","value":"<cid>"}]&limit=1` | Primer ad de la campaña (para `creativeTitle`, `template`) | `id,name,creative{id,object_story_spec,title,body,image_url,thumbnail_url}` |
| `GET /v23.0/{cid}/insights` | Métricas core | `spend,impressions,reach,clicks,ctr,cpm,cpc,frequency,actions,action_values,unique_clicks,cost_per_unique_click` |
| `GET /v23.0/{cid}/insights` con `breakdowns=publisher_platform,platform_position` | Métricas con breakdown por placement | mismo `fields` |
| `GET /v23.0/{cid}/insights` con `time_increment=1&date_preset=last_14d` | Serie diaria (§Fase 12.1) | `spend,impressions,clicks,actions` |
| `POST /v23.0/{pixel_id}/events` | CAPI CTWA (Fase 12.2) | body con `ctwa_clid`, `action_source=business_messaging`, `messaging_channel=whatsapp` |

### 8.2 — Action types relevantes en `actions[]`

El response de `/insights` con `fields=actions` devuelve:

```json
{
  "actions": [
    { "action_type": "link_click", "value": "4128" },
    { "action_type": "onsite_conversion.messaging_conversation_started_7d", "value": "612" },
    { "action_type": "onsite_conversion.messaging_first_reply", "value": "428" },
    { "action_type": "onsite_conversion.messaging_block", "value": "8" }
  ]
}
```

| Action type | Mapeo Hubara |
|---|---|
| `onsite_conversion.messaging_conversation_started_7d` | Validación de `started` (cross-check con el counter del vault) |
| `onsite_conversion.messaging_first_reply` | Numerador para "% respuesta a plantilla" (Inspector) |
| `link_click` | Componente del `clicks` total (informativo, no se mapea) |
| `onsite_conversion.messaging_block` | Métrica de bounce — futuro panel "salud de la campaña" |

### 8.3 — Mapping `effective_status` → `status` del modelo

```python
def map_status(meta_effective_status: str) -> str | None:
    if meta_effective_status == "ACTIVE":
        return "active"
    if meta_effective_status in ("PAUSED", "CAMPAIGN_PAUSED", "ADSET_PAUSED"):
        return "paused"
    if meta_effective_status in ("DELETED", "ARCHIVED", "DISAPPROVED", "PENDING_REVIEW"):
        return None     # frontend muestra "Estado pendiente" con dataPending marker
    return None
```

### 8.4 — Mapping `targeting` JSON → `audience` (string corto)

Meta devuelve `targeting` como JSON anidado complejo (genders, age_min, age_max, geo_locations.cities, interests, etc.). El frontend solo tiene un slot de string corto. Resumir así:

```python
def summarize_audience(targeting: dict) -> str:
    gender = {1: "Hombres", 2: "Mujeres"}.get(targeting.get("genders", [0])[0], "Todos")
    age_min = targeting.get("age_min", "—")
    age_max = targeting.get("age_max", "—")
    cities = [c["name"] for c in targeting.get("geo_locations", {}).get("cities", [])]
    location = ", ".join(cities[:3]) or "Internacional"
    return f"{gender} {age_min}-{age_max} · {location}"
```

Output ejemplo: `"Mujeres 28-55 · Bogotá, Medellín, Cali"` (matchea con el mock).

### 8.5 — Mapping `placement` summarized

```python
def summarize_placements(insights_by_placement: list[dict]) -> str:
    # insights_by_placement viene de GET con breakdowns=publisher_platform,platform_position
    # Devuelve "Reels · Feed · Stories" en orden de spend descendente.
    pos_label = {
        "feed": "Feed",
        "story": "Stories",
        "reels": "Reels",
        "marketplace": "Marketplace",
        "explore": "Explore",
        "instagram_stories": "IG Stories",
    }
    by_pos: dict[str, float] = {}
    for row in insights_by_placement:
        pos = row.get("platform_position", "")
        label = pos_label.get(pos, pos)
        by_pos[label] = by_pos.get(label, 0) + float(row.get("spend", 0))
    sorted_pos = sorted(by_pos.items(), key=lambda x: -x[1])
    return " · ".join(p for p, _ in sorted_pos[:3]) or "—"
```

---

## Fase 9 — Nuevo plugin `meta_ads` (skeleton DEHA)
<a id="fase-9"></a>

### 9.1 — Layout DEHA (hexagonal — sección 02 del architecture guide)

```
hubara_agency/src/plugins/meta_ads/
├── __init__.py
├── agent/
│   ├── __init__.py
│   ├── composition.py            # lru_cache singletons (client, repository)
│   ├── contracts.py              # @dataclass(frozen=True) — R-JSON
│   ├── activities/
│   │   ├── __init__.py
│   │   ├── fetch_campaigns.py    # GET /act_{aid}/campaigns
│   │   ├── fetch_insights.py     # GET /{cid}/insights (batched)
│   │   ├── fetch_creative.py     # GET /ad/{aid}?fields=creative{...}
│   │   └── write_snapshot.py     # write atomic a vault/meta_ads/<aid>.json
│   ├── workflows/
│   │   ├── __init__.py
│   │   └── sync.py               # MetaAdsSyncWorkflow
│   └── use_cases/
│       ├── __init__.py
│       └── merge_with_chats.py   # pure: merge MetaCampaignDTO + AdsCampaignSummary
├── workers/
│   ├── __init__.py
│   └── sync.py                   # boot del worker
└── plugin.yaml                   # registro de worker + task_queue
```

Y en `src/platform/meta_ads/` (el adapter — sigue el patrón de `src/platform/meta_catalog/`):

```
src/platform/meta_ads/
├── __init__.py
├── client.py                     # MetaAdsClient (httpx async, rate-limit logging)
├── dtos.py                       # MetaCampaignDTO, MetaInsightsDTO (raw API shapes)
├── mapper.py                     # raw JSON → DTOs frozen
├── repository.py                 # MetaAdsRepository — read/write vault/meta_ads/<aid>.json
└── port.py                       # MetaAdsClientPort Protocol (R-DIP)
```

### 9.2 — Contracts (R-JSON)

```python
# src/plugins/meta_ads/agent/contracts.py
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class MetaAdsSyncInput:
    ad_account_id: str       # sin "act_" prefix
    snapshot_dir: str        # caller-provided o "" → activity fallback
    insights_date_preset: str = "last_30d"


@dataclass(frozen=True)
class FetchCampaignsInput:
    ad_account_id: str


@dataclass(frozen=True)
class FetchCampaignsResult:
    campaigns_json: str      # JSON-string trick (gotcha #6 del DEHA arch)
    count: int
    fetched_at: str          # ISO 8601 UTC


@dataclass(frozen=True)
class FetchInsightsInput:
    campaign_ids: tuple[str, ...]    # tuple → R-JSON friendly, immutable
    date_preset: str
    with_placement_breakdown: bool = True
    with_daily_breakdown: bool = False     # True para §Fase 12.1


@dataclass(frozen=True)
class FetchInsightsResult:
    insights_json: str       # dict de cid → insights raw
    count: int               # número de campañas con data
    fetched_at: str


@dataclass(frozen=True)
class WriteMetaAdsSnapshotInput:
    ad_account_id: str
    campaigns_json: str
    insights_json: str
    creatives_json: str
    fetched_at: str
    snapshot_dir: str


@dataclass(frozen=True)
class WriteMetaAdsSnapshotResult:
    version: str
    bytes_written: int


@dataclass(frozen=True)
class MetaAdsSyncResult:
    write: WriteMetaAdsSnapshotResult
    campaigns_fetched: int
    insights_fetched: int
```

### 9.3 — Client (en `src/platform/meta_ads/client.py`)

Mismo patrón que `src/platform/meta_catalog/client.py`. Características obligatorias:

- `httpx.AsyncClient` con `timeout=60.0` (insights pueden tardar en cuentas con muchas campañas).
- Loguear `X-Business-Use-Case-Usage` header (devuelve % de quota consumido para `ads_management`).
- Tolerar 429 con backoff (Temporal retry policy lo cubre, pero el client debería devolver structured error).
- Soportar `/v23.0/?batch=[]` para colapsar N requests de insights en 1 HTTP call (1 req = 1 punto rate limit, vs N puntos).
- Lectura del token desde env en composition.py, NUNCA en input del workflow (R-DET + Temporal event history persistence).

Ejemplo de método:

```python
async def fetch_campaigns(
    self,
    ad_account_id: str,
    access_token: str,
    fields: list[str] | None = None,
) -> list[MetaCampaignRaw]:
    """GET /v23.0/act_{aid}/campaigns?fields=...&limit=100"""
    fields = fields or [
        "id", "name", "status", "effective_status", "objective",
        "start_time", "stop_time", "daily_budget", "lifetime_budget",
        "bid_strategy", "buying_type", "special_ad_categories",
        "created_time", "updated_time",
    ]
    url = f"{self._base_url}/{self._api_version}/act_{ad_account_id}/campaigns"
    params = {
        "access_token": access_token,
        "fields": ",".join(fields),
        "limit": 100,
    }
    results = []
    while url:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(url, params=params)
        # Log rate limit header
        rate_header = resp.headers.get("X-Business-Use-Case-Usage", "")
        logger.info("meta_ads.rate_limit", header=rate_header)
        resp.raise_for_status()
        data = resp.json()
        results.extend(MetaCampaignRaw(**c) for c in data.get("data", []))
        # Pagination
        paging = data.get("paging", {})
        url = paging.get("next")
        params = None    # next URL ya incluye todo
    return results
```

### 9.4 — Port (R-DIP)

```python
# src/platform/meta_ads/port.py
from typing import Protocol

class MetaAdsClientPort(Protocol):
    async def fetch_campaigns(
        self, ad_account_id: str, access_token: str
    ) -> list[MetaCampaignRaw]: ...

    async def fetch_insights_batch(
        self,
        campaign_ids: list[str],
        access_token: str,
        date_preset: str = "last_30d",
        breakdowns: list[str] | None = None,
        time_increment: int | None = None,
    ) -> dict[str, MetaInsightsRaw]: ...
```

El use case `merge_with_chats.py` depende de **Port**, no del cliente concreto. Test substitution = `StubMetaAdsClient`.

### 9.5 — `plugin.yaml` del nuevo plugin

```yaml
id: meta_ads
version: 0.1.0
display_name: Meta Ads Sync
description: |
  Sync periódico (cada 15 min) desde Meta Marketing API → snapshot filesystem.
  El plugin `chats` lee el snapshot y lo mergea con sus campañas WA-derivadas
  en el endpoint `/api/chats/ads/campaigns` (R-DIP: chats NO importa meta_ads).

depends_on: []

frontend:
  # Sin frontend propio — el plugin `ads` ya tiene la UI y consume
  # `/api/chats/ads/campaigns` que ahora viene enriquecido.

# Sin api: por ahora. Si en el futuro queremos exponer `/api/meta_ads/sync`
# para trigger manual, se agrega acá.

agent:
  python_module: src.plugins.meta_ads.agent
  workers:
    - name: sync
      module: src.plugins.meta_ads.workers.sync
      task_queue: queue-meta-ads-sync
      workflow_classes:
        - MetaAdsSyncWorkflow
      deployment:
        replicas: 1                    # idempotent reads, pero 1 writer al snapshot
        strategy: Recreate
        cpu_request: 100m
        memory_request: 256Mi
        env_secrets:
          - { var: META_SYSTEM_USER_TOKEN, secret: hubara-meta-secret, key: META_SYSTEM_USER_TOKEN }
          - { var: META_AD_ACCOUNT_ID,     secret: hubara-meta-secret, key: META_AD_ACCOUNT_ID }
      compose:
        env:
          TEMPORAL_URL: temporal:7233
          WORKSPACE_VAULT_DIR: /app/hubara_vault
          META_ADS_SNAPSHOT_DIR: /app/hubara_vault/meta_ads
          META_AD_ACCOUNT_ID: ${META_AD_ACCOUNT_ID}
          META_SYSTEM_USER_TOKEN: ${META_SYSTEM_USER_TOKEN}
        volumes:
          - hubara-vault-local:/app/hubara_vault
        depends_on:
          - temporal

wiring_intents:
  filesystem_volumes:
    - hubara-vault                   # subPath meta_ads/
  env_vars_required:
    - TEMPORAL_URL
    - META_ADS_SNAPSHOT_DIR
    - META_AD_ACCOUNT_ID
    - META_SYSTEM_USER_TOKEN
```

---

## Fase 10 — Workflow + activities + snapshot durable
<a id="fase-10"></a>

### 10.1 — Workflow `MetaAdsSyncWorkflow`

```python
# src/plugins/meta_ads/agent/workflows/sync.py
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from src.plugins.meta_ads.agent.activities import (
        fetch_campaigns_activity,
        fetch_insights_activity,
        fetch_creatives_activity,
        write_meta_ads_snapshot_activity,
    )
    from src.plugins.meta_ads.agent.contracts import (
        MetaAdsSyncInput,
        MetaAdsSyncResult,
        FetchCampaignsInput,
        FetchInsightsInput,
        WriteMetaAdsSnapshotInput,
    )
    from src.platform.temporal.retry_policies import _TOOL_OPTIONS, _CONV_OPTIONS


@workflow.defn(name="MetaAdsSyncWorkflow")
class MetaAdsSyncWorkflow:
    @workflow.run
    async def run(self, input: MetaAdsSyncInput) -> MetaAdsSyncResult:
        # 1) Fetch campañas activas + paused (deletadas se ignoran).
        campaigns = await workflow.execute_activity(
            fetch_campaigns_activity,
            FetchCampaignsInput(ad_account_id=input.ad_account_id),
            **_TOOL_OPTIONS,
        )

        # 2) Parse JSON-string trick para extraer campaign_ids
        # (deserialización determinística OK en workflow — sin I/O).
        import json
        campaign_dicts = json.loads(campaigns.campaigns_json)
        campaign_ids = tuple(c["id"] for c in campaign_dicts)

        # 3) Fetch insights batch (1 HTTP request para todas las campaigns).
        insights = await workflow.execute_activity(
            fetch_insights_activity,
            FetchInsightsInput(
                campaign_ids=campaign_ids,
                date_preset=input.insights_date_preset,
                with_placement_breakdown=True,
            ),
            **_TOOL_OPTIONS,
        )

        # 4) Fetch primer ad creative por campaña (para creativeTitle, template).
        # Activity opcional — si falla, snapshot sin creativeTitle.
        creatives = await workflow.execute_activity(
            fetch_creatives_activity,
            FetchInsightsInput(campaign_ids=campaign_ids, date_preset=""),
            **_TOOL_OPTIONS,
        )

        # 5) Write atómico del snapshot a vault.
        write_result = await workflow.execute_activity(
            write_meta_ads_snapshot_activity,
            WriteMetaAdsSnapshotInput(
                ad_account_id=input.ad_account_id,
                campaigns_json=campaigns.campaigns_json,
                insights_json=insights.insights_json,
                creatives_json=creatives.insights_json,
                fetched_at=campaigns.fetched_at,
                snapshot_dir=input.snapshot_dir,
            ),
            **_CONV_OPTIONS,
        )

        return MetaAdsSyncResult(
            write=write_result,
            campaigns_fetched=campaigns.count,
            insights_fetched=insights.count,
        )
```

**R-DET cumplido:** workflow no toca `time.time()`, `os.environ`, no instancia clientes. Todo I/O en activities.

**R-JSON cumplido:** todos los inputs/outputs son `@dataclass(frozen=True)`. `campaigns_json: str` aplica el JSON-string trick para evitar tipos anidados complejos.

### 10.2 — Schedule periódico

Igual que `META_CATALOG_SETUP.md` y a diferencia de `CatalogSyncWorkflow` (que es on-demand), acá SÍ queremos Schedule periódico — Meta no nos avisa cuando cambian impressions/clicks, hay que poll.

Script: `scripts/setup_meta_ads_schedule.py` (siguiendo el patrón de `scripts/trigger_catalog_sync.py`):

```python
import asyncio
from datetime import timedelta
from src.platform.temporal.client import get_temporal_client
from src.platform.plugin_manifest import get_task_queue
from src.plugins.meta_ads.agent.contracts import MetaAdsSyncInput
import os

async def setup_schedule():
    client = await get_temporal_client()
    schedule_id = "meta-ads-sync-periodic"
    await client.create_schedule(
        schedule_id,
        Schedule(
            action=ScheduleActionStartWorkflow(
                "MetaAdsSyncWorkflow",
                MetaAdsSyncInput(
                    ad_account_id=os.environ["META_AD_ACCOUNT_ID"],
                    snapshot_dir=os.environ["META_ADS_SNAPSHOT_DIR"],
                    insights_date_preset="last_30d",
                ),
                id=f"meta-ads-sync-{schedule_id}",
                task_queue=get_task_queue("meta_ads", "sync"),
            ),
            spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=timedelta(minutes=15))]),
        ),
    )

asyncio.run(setup_schedule())
```

**Cadencia:** cada 15 minutos. Justificación:
- Meta insights freshness: impressions/clicks ~real-time (segundos), spend ~minutos, actions/conversiones ~horas.
- Frontend tiene `staleTime: 30_000` en TanStack Query (`api.ts:198`). 15 min de backend + 30s de cache TQ = staleness máxima ~15.5 min.
- Rate budget: ~96 syncs/día, ~4 HTTP calls cada uno (campaigns + insights batch + creatives + write). ~400 calls/día. Bajo el límite de 200/h development tier (al startup hay un burst pero después se distribuye).

### 10.3 — Snapshot schema en disco

`vault/meta_ads/<ad_account_id>.json`:

```json
{
  "ad_account_id": "1234567890123456",
  "fetched_at": "2026-05-25T14:30:00Z",
  "version": "v1",
  "campaigns": [
    {
      "id": "120203928401234",
      "name": "Velas vainilla · Día de la Madre",
      "effective_status": "ACTIVE",
      "objective": "OUTCOME_ENGAGEMENT",
      "start_time": "2026-05-01T00:00:00-0500",
      "stop_time": null,
      "daily_budget": "150000",
      "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
      "audience_summary": "Mujeres 28-55 · Bogotá, Medellín, Cali",
      "ad_set_name": "Pack 6 — Adquisición",
      "creative_title": "Regala aroma este 12 de mayo",
      "template_hint": "promo_dia_madre_v3",
      "placement_summary": "Reels · Feed · Stories"
    }
  ],
  "insights": {
    "120203928401234": {
      "spend": 1840000,
      "impressions": 184320,
      "reach": 96420,
      "clicks": 4128,
      "ctr": 2.24,
      "cpm": 9.98,
      "cpc": 445,
      "frequency": 1.91,
      "actions": {
        "link_click": 4128,
        "onsite_conversion.messaging_conversation_started_7d": 612,
        "onsite_conversion.messaging_first_reply": 428
      }
    }
  }
}
```

### 10.4 — Tests obligatorios

```
tests/plugins/meta_ads/
├── test_workflow_determinism.py       # workflow replay → same result
├── test_contracts_json_serializable.py # R-JSON
├── test_fetch_campaigns_activity.py    # stub MetaAdsClient + golden response
├── test_fetch_insights_batch.py        # batch URL construction + pagination
├── test_mapper_action_types.py         # action types → KPIs
└── test_mapper_targeting.py            # targeting JSON → audience summary
```

Y los `tests/architecture/`:
- Verificar que `src/plugins/meta_ads/` NO importa de otros `src/plugins/*` (R-DIP).
- Verificar que las activities NO tienen module-level state (R-STATELESS).

### 10.5 — Heartbeat (R-HEARTBEAT)

`fetch_insights_activity` con `time_increment=1` en cuentas grandes puede tardar > 10s (Meta agrupa todas las series diarias). Aplicar `@with_heartbeat`:

```python
from src.platform.temporal.heartbeat import with_heartbeat

@activity.defn(name="fetch_insights")
@with_heartbeat
async def fetch_insights_activity(input: FetchInsightsInput) -> FetchInsightsResult:
    ...
```

---

## Fase 11 — Merge en endpoint existente `/api/chats/ads/campaigns`
<a id="fase-11"></a>

### 11.1 — Estrategia

El endpoint actual ([`hubara_agency/src/plugins/chats/api/ads.py:35`](../src/plugins/chats/api/ads.py)) llama a `list_ads_campaigns(WORKSPACE_VAULT_DIR)`. Esa función vive en `chats/agent/sales/use_cases/list_ads_campaigns.py` y solo lee `wa_*/metadata.json`.

El cambio: la función queda igual y devuelve `AdsCampaignSummary` con todos los campos Meta como `None`. Después agregamos un **paso de merge** que lee el snapshot Meta y enriquece.

**R-DIP:** `chats` NO importa `meta_ads`. La integración va a través del **filesystem** (snapshot JSON) — patrón ya establecido para `catalog/snapshot/`.

### 11.2 — Cambios en código

**Nuevo archivo:** `hubara_agency/src/platform/meta_ads/repository.py`

```python
"""Repository de read-only para el snapshot Meta Ads. Lo consume el plugin
`chats` desde su endpoint /api/chats/ads/campaigns. Sin acoplamiento de
import — chats NO importa meta_ads, sí importa platform/meta_ads (que es
infraestructura compartida)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MetaCampaignEnrichment:
    """Subset del snapshot Meta Ads que el frontend consume."""
    meta_campaign_id: str
    name: str | None
    status: str | None              # mapped de effective_status
    objective: str | None
    placement: str | None           # placement_summary
    audience: str | None            # audience_summary
    ad_set: str | None
    creative_title: str | None
    template: str | None
    days_run: int | None
    spend: float | None
    impressions: int | None
    reach: int | None
    clicks: int | None


class MetaAdsRepository:
    def __init__(self, snapshot_dir: str | None = None) -> None:
        self._dir = Path(
            snapshot_dir or os.environ.get("META_ADS_SNAPSHOT_DIR", "")
        )

    def load_enrichments_by_id(self) -> dict[str, MetaCampaignEnrichment]:
        """Lee TODOS los snapshots disponibles (single-tenant: 1 ad account)
        y devuelve dict {campaign_id: enrichment}. Tolera snapshot ausente
        — devuelve dict vacío y el endpoint sirve todo con `null`."""
        if not self._dir.exists():
            return {}
        out: dict[str, MetaCampaignEnrichment] = {}
        for snap_path in self._dir.glob("*.json"):
            try:
                raw = json.loads(snap_path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            for c in raw.get("campaigns", []):
                cid = c.get("id")
                if not cid:
                    continue
                insights = raw.get("insights", {}).get(cid, {})
                out[cid] = _to_enrichment(c, insights, raw.get("fetched_at"))
        return out


def _to_enrichment(
    campaign: dict, insights: dict, fetched_at: str | None
) -> MetaCampaignEnrichment:
    ...  # mapping de §Fase 8.3, 8.4, 8.5
```

**Cambio en `list_ads_campaigns`:** después del loop existente, agregar un step de merge:

```python
# hubara_agency/src/plugins/chats/agent/sales/use_cases/list_ads_campaigns.py
from src.platform.meta_ads.repository import MetaAdsRepository

def list_ads_campaigns(vault_dir: Path) -> list[AdsCampaignSummary]:
    # ... código existente ...

    # NEW: enrichment desde Meta Ads snapshot. Match por `source_id == meta_campaign_id`.
    # Si el snapshot no existe (Meta apagado / sync pending), `enrichments` = {} y
    # los campos siguen siendo None.
    enrichments = MetaAdsRepository().load_enrichments_by_id()

    enriched = []
    for s in summaries:
        e = enrichments.get(s.id)
        if e is None:
            enriched.append(s)   # campaña detectada en WA pero NO en Meta (origen=post u otro)
            continue
        enriched.append(dataclasses.replace(
            s,
            name=e.name or s.name,
            status=e.status,
            objective=e.objective,
            placement=e.placement,
            audience=e.audience,
            ad_set=e.ad_set,
            creative_title=e.creative_title,
            template=e.template,
            days_run=e.days_run,
            spend=e.spend,
            impressions=e.impressions,
            reach=e.reach,
            clicks=e.clicks,
            meta_campaign_id=e.meta_campaign_id,
        ))
    return enriched
```

**Importante:** `meta_ads.repository` vive en `src/platform/`, no en `src/plugins/`. Eso es R-DIP-OK: `platform/` es infra compartida, los plugins SÍ pueden importar de platform/.

### 11.3 — Tests de integración

```python
# tests/plugins/chats/test_ads_endpoint_with_meta_enrichment.py
def test_endpoint_serves_meta_enriched_fields(tmp_path, monkeypatch):
    # Setup vault con 1 sesión WA
    session_dir = tmp_path / "wa_5491155551234"
    session_dir.mkdir(parents=True)
    (session_dir / "metadata.json").write_text(json.dumps({
        "origin": {
            "channel": "ad",
            "source_id": "120203928401234",
            "headline": "Velas vainilla",
            "first_seen_ms": 1714312400000,
        },
        "tag": "GANADO",
        "episodes": [...],
    }))

    # Setup snapshot Meta Ads en tmp
    meta_dir = tmp_path / "meta_ads"
    meta_dir.mkdir()
    (meta_dir / "1234567890123456.json").write_text(json.dumps({
        "ad_account_id": "1234567890123456",
        "fetched_at": "2026-05-25T14:30:00Z",
        "campaigns": [{
            "id": "120203928401234",
            "name": "Velas vainilla · Día de la Madre",
            "effective_status": "ACTIVE",
            "objective": "OUTCOME_ENGAGEMENT",
            "audience_summary": "Mujeres 28-55 · Bogotá",
            "placement_summary": "Reels · Feed",
            "start_time": "2026-05-01T00:00:00-0500",
        }],
        "insights": {
            "120203928401234": {
                "spend": 1840000,
                "impressions": 184320,
                "reach": 96420,
                "clicks": 4128,
            }
        }
    }))

    monkeypatch.setenv("META_ADS_SNAPSHOT_DIR", str(meta_dir))

    result = list_ads_campaigns(tmp_path)
    assert len(result) == 1
    c = result[0]
    assert c.id == "120203928401234"
    assert c.name == "Velas vainilla · Día de la Madre"
    assert c.status == "active"
    assert c.spend == 1840000
    assert c.impressions == 184320
    assert c.audience == "Mujeres 28-55 · Bogotá"
```

---

## Fase 12 — Daily series + Conversions API CTWA
<a id="fase-12"></a>

> **Estas dos sub-fases son OPCIONALES en el v1.** Si querés shipear sin ellas, los gráficos siguen mock pero todo lo demás funciona. Recomendamos hacerlas en este orden: 12.1 antes que 12.2.

### 12.1 — Daily series (`AdsDailyTrend` real)

**Cambio:**
1. En `fetch_insights_activity`, hacer un segundo call con `time_increment=1&date_preset=last_14d` y persistirlo en el snapshot como `insights_daily: {cid: [{date, spend, impressions, clicks, actions}, ...]}`.
2. Nuevo endpoint `GET /api/chats/ads/campaigns/{cid}/daily?days=14` que lee el snapshot y devuelve `AdsDailyPoint[]`.
3. Frontend: cambiar [`entities/ads-campaign/api.ts:228 useDailySeries`](../../frontend_dashboard/src/entities/ads-campaign/api.ts) de mock a fetch real.

El daily breakdown desde Meta NO te da counts por estado conversacional (eso es nuestro clasificador). Los counts diarios por estado los derivás del vault iterando los `episodes[].closed_at_ms` agrupados por día.

**Híbrido recomendado:**
- Meta: aporta `spend`, `impressions`, `clicks` por día.
- Vault: aporta `ganado`, `cotizado`, `calificado`, `activo`, `nuevo`, `no_reply`, `perdido` por día.
- El `AdsDailyPoint` final del frontend se construye con un merge en el use case `daily_rollup.py` (nuevo).

### 12.2 — Conversions API CTWA (cierra el loop)

**Por qué:** Meta optimiza las campañas según las conversiones que recibe. Si solo le mandamos `messaging_conversation_started`, Meta optimiza por "iniciar chats" — no por "vender". Si le mandamos `Purchase` con `ctwa_clid`, Meta optimiza por clientes que efectivamente compraron.

**Flujo:**
1. El webhook WA ya guarda `ctwa_clid` en `metadata.json` cuando llega un primer mensaje desde un ad. Confirmar mirando `origin.ctwa_clid` (puede que hoy no se guarde — chequear `_handle_referral` en el ingest).
2. Cuando el plugin `orders` registra una venta exitosa (Medusa draft order → completed), dispara una activity nueva `send_ctwa_purchase_event_activity`.
3. La activity hace `POST /v23.0/{pixel_id}/events` con:

```json
{
  "data": [{
    "event_name": "Purchase",
    "event_time": 1715050800,
    "action_source": "business_messaging",
    "messaging_channel": "whatsapp",
    "user_data": {
      "ph": ["<sha256 del phone>"],
      "ctwa_clid": "ARAxYz9KvL2..."
    },
    "custom_data": {
      "value": 198000,
      "currency": "COP",
      "content_ids": ["sku-velas-vainilla-6"]
    }
  }],
  "access_token": "<META_SYSTEM_USER_TOKEN>"
}
```

**Caveats:**
- `ctwa_clid` tiene un TTL — chequear docs Meta. Si la conversión llega 6 meses después, Meta lo ignora pero la API no falla.
- `phone` debe ir SHA256-hashed (Meta lo exige por GDPR).
- Si no tenés Pixel ID, este paso no se hace — Conversions API también acepta `dataset_id` (Events Manager → Datasets) como reemplazo.

**Tests:**
- Smoke: enviar 1 evento dummy y verificar en `Events Manager → Test Events` que aparece con `match_quality: "good"`.

---

## Fase 13 — Sugerencias del agente IA (DEFERRED)
<a id="fase-13"></a>

> **Mencionado pero NO en scope de esta entrega.** Documentado acá para no perderlo.

El panel `Sugerencias del agente IA` en [`AdsInspector.tsx:164`](../../frontend_dashboard/src/plugins/ads/frontend/features/ads-inspector/ui/AdsInspector.tsx) hoy renderea 2 tips hardcodeados ("ROAS sobre 3×", "30% sin respuesta tras clic").

Cuando se priorice, el patrón sería:
- Workflow `MetaAdsSuggestionsWorkflow` que corre diariamente (o cuando se sincronice Meta).
- Activity con LLM call: input = campaña con todas las métricas; output = lista de `Suggestion(severity, title, body, suggested_action)`.
- Persistir en `vault/meta_ads/<aid>_suggestions.json`.
- Nuevo endpoint `GET /api/chats/ads/campaigns/{cid}/suggestions`.
- Frontend consume y reemplaza los tips hardcodeados.

Requiere un PRD aparte porque hay decisiones (prompt engineering, severidad scoring, action wiring — si una sugerencia es "aumenta 20% el budget", ¿hay un botón que lo aplica? eso ya implica `ads_management` write scope) que no caben en esta integración base.

---

## Anexos
<a id="anexos"></a>

### A.1 — Rate limits prácticos (development tier)

| Métrica | Valor |
|---|---|
| Score máximo | 60 puntos |
| Read call | 1 punto |
| Write call | 3 puntos |
| Header de tracking | `X-Business-Use-Case-Usage` (JSON con `ads_management.call_count`, `ads_insights.call_count`) |
| Recovery | El bucket se rellena con el tiempo — `estimated_time_to_regain_access` del header indica segundos |
| Upgrade path | Pasar a Advanced Access en App Review (10× quota aproximadamente) |

**Strategy:**
- Workflow corre cada 15 min → 96 syncs/día.
- Por sync: 1 GET campaigns + 1 batch GET insights + ~6 GET ads (1 por campaña en pago activo) = ~8 calls = ~8 puntos.
- Total/h = 32 puntos. Estamos al 53% del limit.
- Si querés bajar a 5 min, vas a 96 puntos/h → bursts pueden chocar contra el limit. Quedarse en 15 min hasta upgrade a Standard Access.

### A.2 — Atribución y discrepancias

El `started` que reporta Meta (`actions[onsite_conversion.messaging_conversation_started_7d]`) y el que cuenta el vault (count de sessions WA con `origin.source_id == campaign_id`) **NO van a coincidir exactamente**. Por qué:
- Meta cuenta clicks que **abrieron** el flow CTWA, no que **completaron** el primer mensaje. Algunos usuarios cierran sin escribir nada — Meta los cuenta, el vault NO.
- Meta cuenta con su attribution window (7d_click). El vault no tiene ventana — todo lo que llega lo guarda.
- Time zone shift: Meta agrega por día PST por default; el vault está en UTC.

**Decisión:** mostrar el del vault como "ground truth" (es la verdad sobre tu negocio). El de Meta queda en `actions[]` del snapshot para cross-check en debugging.

### A.3 — Versiones Graph API

Meta libera nueva versión cada 3 meses. v23.0 es la actual (matchea con catálogo). Cuando migremos a v24.0+, cambiar:
- `GRAPH_API_VERSION = "v23.0"` en `src/platform/meta_ads/client.py`
- `GRAPH_API_VERSION = "v23.0"` en `src/platform/meta_catalog/client.py` (ya existe)

NO cambia el código de mapping ni los tests. Los breaking changes en Meta SDK son raros — anuncian deprecations con 90 días.

### A.4 — Troubleshooting más común

| Síntoma | Causa probable | Fix |
|---|---|---|
| `(#190) Invalid OAuth access token` | Token expirado o sin scope | Regenerar token (§Fase 4.1) con `ads_read` |
| `actions: []` siempre en insights | WABA no linkeada al Ad Account | Volver a §Fase 3.1 |
| `(#100) Ad account is not allowed to access this object` | System User no asignado al Ad Account | Volver a §Fase 2.2 |
| Snapshot escrito pero campos `null` en frontend | Match fail entre `source_id` (vault) y `id` (Meta). Mismo número, distinto formato | Logguear en `merge_with_chats.py` y normalizar |
| Rate limit 429 | Demasiados syncs concurrent | Backoff (Temporal retry policy lo cubre), reducir frequency |
| Insights vacíos para campaña pausada hace > 30 días | `date_preset=last_30d` no cubre | Default fine; si querés histórico, cambiar a `time_range={"since": ..., "until": ...}` |

### A.5 — ADR pendientes (para discusión cuando se priorice)

1. **Multi-tenant Ad Accounts.** Hoy un solo `META_AD_ACCOUNT_ID`. Cuando onboardeen un segundo cliente, los IDs y tokens migran al plugin `agents_admin` (per-tenant config en filesystem). El snapshot Meta queda en `vault/meta_ads/<tenant_id>/<aid>.json`.
2. **Write scope `ads_management`.** Para que el agente IA pueda pausar/duplicar campañas desde una sugerencia, hay que escalar el token. Requiere App Review más profundo de Meta — no es trivial.
3. **Audit log de cambios.** Cuando Hubara pause/active un ad, registrar quién (system_user, agent_id) y por qué. Útil para compliance + debugging de regression.

---

## Checklist completo

**Humano (Parte A):**

- [ ] Fase 1.1 — Pre-requisites OK (catalog setup completo)
- [ ] Fase 1.2 — Ad Account en el Business Portfolio
- [ ] Fase 1.3 — Al menos 1 ad CTWA running con conversaciones reales
- [ ] Fase 2 — System User asignado al Ad Account
- [ ] Fase 3 — WABA ↔ Ad Account linked
- [ ] Fase 4 — Token regenerado con `ads_read`
- [ ] Fase 5 — `debug_token` verifica scope correcto + `me/adaccounts` lista cuenta
- [ ] Fase 6 — IDs (AD_ACCOUNT_ID + BUSINESS_ID) entregados al dev

**Dev (Parte B):**

- [ ] Fase 9 — Skeleton plugin `meta_ads` creado + tests architecture pasan
- [ ] Fase 10 — Workflow + 4 activities + tests determinism + Schedule corriendo
- [ ] Fase 11 — `MetaAdsRepository` + merge en `list_ads_campaigns` + golden test
- [ ] Fase 12.1 — (opcional) daily series real, mock retirado
- [ ] Fase 12.2 — (opcional) Conversions API CTWA en `orders.completed`
- [ ] Frontend manual smoke: abrir `/ads`, ver KPIs con valores reales sin `<MissingField />` en spend/impressions/clicks/status
- [ ] Premortem run sobre el plugin (10 categorías)
- [ ] Code review multi-agent (DEHA + plugin-system + security)
