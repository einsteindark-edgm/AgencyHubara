# Acktos Stores: una plataforma, las tiendas como configuración

> **Estado:** ACEPTADO (ADR `docs/adr/2026-10-22-acktos-stores.md`). En ejecución desde el 2026-10-22: el avance está en el §0.
> Propuesto el 2026-10-19; actualizado el 2026-10-21 con lo que entró a `main` (el refactor de Temporal: #443, #444, #448, #450; campañas: #442, #447, #449, #451, #452).
> Reemplaza, para las tiendas pequeñas, la decisión de silo total por cliente (`VINCENZO_SPLIT_PLAN.md`, 2026-07-09).
> **Orden:** Vincenzo es la primera tienda; cuando esté estable, Hubara se muda a la misma plataforma.

## 0. Avance

**Lo que hace el operador** (no lo puede hacer un agente: cuentas, pagos y Meta):

- [ ] **Temporal Cloud, antes del 31-oct:** pasar la cuenta de Essentials al pago por uso.
- [ ] **AWS:** crear la organización desde la cuenta de Hubara (cuenta de gestión) y la cuenta miembro `acktos`, con el rol `OrganizationAccountAccessRole`. Después, el bootstrap de la cuenta (F2) lo corre un agente, con el plan revisado antes.
- [ ] **Dominio** (decisión 5): comprarlo, de preferencia en Route 53 de la cuenta `acktos`.
- [ ] **Meta, Vincenzo:** su app (tipo Business, producto WhatsApp), su WABA, su número y la verificación de su negocio.
- [ ] **Meta, Acktos:** verificación del negocio y el trámite de proveedor tecnológico (en paralelo, no bloquea a Vincenzo).
- [ ] **Datos de Vincenzo** para su paquete (F4): Nequi, recargos, ciudad y zona local, tarifas, mínimo de contra entrega, prefijo de SKU, colecciones, precios, fotos y medidas del morral (M y XL), Instagram, web y tiempos de entrega.
- [ ] **PR #410:** mergearlo, y después el `terraform apply` de compute y platform de Hubara (plan revisado antes).

**Desarrollo:**

| Paso | Estado |
|---|---|
| F0: ADR | Hecho (2026-10-22) |
| F1.1: contexto de tienda (`src.sdk.tenantkit`) | Hecho (2026-10-22), en el mismo PR que el ADR |
| F1.2: la tienda viaja por Temporal (interceptor) | Siguiente |

## 1. Qué es y por qué ahora

Acktos Stores es **una** plataforma, con un motor y una infraestructura, donde cada tienda es configuración: su nombre, su política comercial, la voz de sus agentes, su WhatsApp, su Medusa y sus datos.

Una tienda nueva cuesta lo que pesan su Medusa y su tráfico, no una caja entera.

Por qué ahora:

1. **Costo.** Un clon de forge con sus valores por defecto cuesta unos **US$184/mes** por tienda, y unos US$68 si se ajusta. En Acktos la primera tienda cuesta unos **US$60**, y las siguientes casi nada hasta llenar la caja (§4).
2. **Operación.** Con forge, cada mejora del motor hay que fusionarla en un repo por cliente. Dos fusiones de `main` a la rama de forge en octubre necesitaron arreglar ~30 pruebas. En Acktos un solo despliegue actualiza todas las tiendas.
3. **Momento.** Vincenzo todavía no está en producción. Su paquete de forge es del 2026-08-12, nunca tuvo caja, y su tenant en el platform de Hubara es un fantasma con valores de relleno. Es el momento de cambiar el modelo sin migrar a nadie.

## 2. Principios

1. **Un solo motor, un solo repo y una sola `main`.** No hay fork ni rama larga de Acktos. El motor aprende a saber de qué tienda es cada cosa (tenant-aware). Hubara, en su caja de siempre, corre **el mismo código** como única tienda: la tienda por defecto. Cada paso se prueba primero en Hubara en producción, y así no hay divergencia.
2. **La tienda es dato, no código.** Todo lo que forge reescribe hoy en el código del motor (40 de sus 108 reglas) pasa a la configuración de la tienda. El manifest de forge, que distingue «motor» de «cliente», es el mapa de ese trabajo.
3. **Lo vivo de Hubara no se toca por accidente.** Acktos tiene su propio estado de Terraform y, recomendado, su propia cuenta de AWS (§9). Es la lección del apply que borró el vault.
4. **Datos separados por tienda.** Cada tienda tiene su propia base de Medusa, carpeta del vault, secretos, entrada de WhatsApp e IDs de Temporal. Unas pruebas con dos tiendas sintéticas lo exigen.
5. **Barato desde la primera tienda.** Al principio todo va en una caja (motor, Medusa, LiteLLM y Redis) más un Postgres pequeño. Se crece por escalones.
6. **Como siempre:** TDD, los gates, y un ADR para los archivos protegidos (`src/platform/tool_extensions.py` y `constants.py` cambian).
7. **Un payload de Temporal lleva la tienda, nunca sus secretos.** Es la regla de #450 (la llave del LLM ya no viaja en la historia): el payload lleva el id de la tienda y, si hace falta, el nombre de la variable; la activity resuelve el valor con `TenantConfig`. En un namespace compartido por varias tiendas, cualquiera que lea la historia vería los secretos de todas.

## 3. Arquitectura

```
                         Internet
                            │
                Caddy (TLS, varios dominios)
      ┌─────────────────────┼─────────────────────────────┐
  api.<acktos>      /webhook/<tienda>        admin.<tienda>.<acktos>
      │                     │                             │
┌─────┴────────── Caja de la celda: EC2 t4g.large (Graviton, 8 GB) ──────────┐
│ acktos-api ............ FastAPI: todas las tiendas                          │
│ worker-conversa ×2 .... ventas · remarketing · ETA · posventa · entrada     │
│ worker-fondo .......... catálogo · pedidos · centinela · ciclos · campañas  │
│ litellm ............... uno para todas las tiendas                          │
│ medusa-<tienda> ....... uno por tienda       ·  redis (compartido, prefijo) │
│ vault ................. /mnt/acktos-vault/<tienda>/…  (EBS dedicado)       │
└─────────────────────────────────────────────────────────────────────────────┘
  RDS Postgres: una base por tienda      S3: fotos, respaldos, paquetes de tienda
  SSM /acktos/<tienda>/*: secretos       Temporal Cloud: namespace `acktos` (pago por uso)
  Cognito: un pool, un grupo por tienda  CloudFront: un solo panel para todas
  GraphAgents: una caja por uso para toda la celda (F6)
```

Piezas nuevas del motor:

- **Contexto de tienda** (`TenantContext`)
  - Es una `ContextVar` que se fija en cada entrada: webhook, API, Temporal, ciclos. Todo lo demás la lee con `current_tenant()`.
  - En la caja de Hubara, la tienda por defecto sale del entorno.
- **Propagación por Temporal**
  - Un interceptor de cliente y de worker lleva la tienda en un encabezado: workflow → activity → child workflow → señal.
  - Desde la entrada durable (#443, #448), cada mensaje de WhatsApp entra a Temporal antes de que el webhook responda 200: `InboundMessageWorkflow` (`inbound-<wamid>`) y, por cliente, `InboundSessionWorkflow` (`inbound-wa_<número>`), que ordena los mensajes y corre el ingest un paso por activity. En la celda, `/webhook/<tienda>` arranca ese workflow ya con la tienda en el encabezado: es la primera puerta y desde ahí todo la hereda.
  - El worker de conversación es el único camino de entrada de los mensajes. Como #448 quitó el invariante de una réplica, en la celda corre con 2 réplicas y la alarma `sales-worker-down` (que trajo #443) vigila que no se caigan las dos.
  - En la celda, los IDs de workflow llevan el prefijo `<tienda>:`, y el atributo de búsqueda `TiendaId` permite filtrar en la UI de Temporal.
  - Hoy ya existe `TracingInterceptor` para el baggage de OTel, pero no sirve para esto: es observabilidad y puede estar apagada.
- **Configuración de tienda** (`TenantConfig`)
  - **Identidad:** nombre, marca, país, moneda, zona horaria, dominio web e Instagram.
  - **Política comercial:** los campos `commerce` de forge: Nequi, recargos, zona y tarifas, contra entrega, SKU y colecciones.
  - **Decisiones:** el paquete (versión) y su `domain.yaml`.
  - **Agentes y plugins:** el workspace de cada agente y los plugins habilitados.
  - **Secretos:** en SSM `/acktos/<tienda>/`.
  - **Carga:** por tienda, con caché y TTL. En la caja de Hubara la fuente sigue siendo el `.env` de siempre.
- **Paquetes de tienda**
  - Viven en un repo privado `acktos-tenants`: `tenants/<tienda>/{tenant.yaml, store.yaml, domain.yaml, workspace/…}`.
  - El CI los valida con el mismo validador de forge y los publica en S3. La celda los recarga sin redesplegar el motor.
  - Reemplazan `forge/clients/`, que hoy es local e ignorado por git.

## 4. Costos (precios de AWS us-east-1 observados el 2026-10-19)

| Concepto | Vincenzo sola en Acktos | Acktos con Vincenzo y Hubara |
|---|---|---|
| Caja t4g.large (8 GB), Savings Plan de 1 año | 35,41 | 35,41 (70,74 si hace falta una t4g.xlarge de 16 GB) |
| Discos (raíz 20 GB + vault 20–30 GB) y snapshots | 4,20 | 5,50 |
| IP fija | 3,65 | 3,65 |
| RDS t4g.micro + 20 GB (bases de Medusa) | 13,98 | 13,98 (25,66 con t4g.small) |
| S3 (fotos, respaldos, paquetes) | ~1 | ~2 |
| CloudWatch mínimo (una cuenta nueva trae 10 métricas gratis) | ~0,50 | ~1 |
| Temporal, namespace `acktos` en pago por uso (con la entrada durable) | ~1–2 | ~8–11 |
| GraphAgents por uso, compartida (F6) | — | ~11 |
| **Total al mes** | **≈ 60** | **≈ 82** (≈ 117 con la caja de 16 GB) |

Para comparar:

| Alternativa | Costo al mes |
|---|---|
| Vincenzo como clon de forge con valores por defecto | ≈ US$184 |
| Vincenzo como clon de forge ajustado | ≈ US$68 |
| Hubara hoy (solo AWS) | ≈ US$63 |
| Medusa de Hubara en Railway | US$10–20 |
| Temporal de Hubara | US$100 en Essentials; ≈ US$7–10 en pago por uso desde la entrada durable (era ≈ US$5). Recomendado hacer el cambio antes del 31-oct |

- **Durante la transición** (Vincenzo en Acktos y Hubara en su caja): unos US$130–140 al mes en total.
- **Después de mudar Hubara:** unos US$82–117 por las dos tiendas, más lo de Railway hasta que su Medusa se mude (F8). En esa celda caben más tiendas.
- **Tienda nueva:** unos US$3–5 al mes (su Medusa ~0,5 GB, su Temporal, su S3) hasta llenar la caja.

**Temporal con la entrada durable.** Cada mensaje que entra ahora es un workflow más un paso por activity (leer el estado, preparar el turno, grabar la historia, entregar). Las acciones por conversación suben; según cuántos mensajes manda cada cliente, entre 1,5 y 2 veces las de antes. Del 1 al 18 de octubre Hubara usó 48.990 acciones (antes de la entrada durable), unas 85.000 al mes; con la entrada durable serían 130.000–170.000, unos US$7–10 en pago por uso. Sigue muy lejos de los US$100 de Essentials. Hay que medirlo en la página de uso a fin de mes.

Las tres primeras filas de costo fijo se pueden comprar ya: un *Compute* Savings Plan sirve para cualquier caja de cualquier familia, así que se puede comprar ahora para la caja de Hubara y luego cubre la de Acktos.

## 5. Fases

Hay dos hitos:
- **Hito V:** Vincenzo vendiendo en Acktos, con reactivación desde el primer día (decisión del §9). Unas 8–12 semanas.
- **Hito H:** Hubara mudada a Acktos. Unas 6–8 semanas más: 4 de estabilización y 2–3 de migración.

Los tiempos son estimados.

### F0. Decisiones y ADR (1 semana)

- **ADR «Acktos Stores»** (`docs/adr/2026-10-22-acktos-stores.md`, hecho):
  - reemplaza el silo para las tiendas pequeñas;
  - aprueba los cambios en archivos protegidos;
  - deja el silo de forge como opción para un cliente grande que exija aislamiento total.
- **Decisiones del §9:** ya están la cuenta de AWS, el Postgres, la app de Meta y la reactivación al lanzar. El criterio para mudar Hubara y el nombre van con lo recomendado. Falta el dominio.
- **Esquema del paquete de tienda** (`tenant.yaml` / `store.yaml`): sale de lo que forge ya clasifica como «del cliente» (`COMMERCE_FIELDS`, `workspace_overlay`, `domain.yaml`, marca).
- **Temporal:** Hubara pasa a Developer (pago por uso) **antes del 31 de octubre** y se crea el namespace `acktos` en la misma cuenta. Al 19 de octubre la cuenta seguía en Essentials.
- **Organización de AWS:** la cuenta de Hubara pasa a ser la cuenta de gestión de una organización, y Acktos nace como cuenta miembro. Una sola factura; el Savings Plan que se compre se comparte entre las dos.

### F1. Motor que sabe de qué tienda es cada cosa, sin cambiar su comportamiento (3–4 semanas)

Se despliega a Hubara a medida que avanza: en su caja todo resuelve a la tienda por defecto, con las mismas rutas y los mismos IDs.

| Paso | Qué | Dónde | Esfuerzo |
|---|---|---|---|
| F1.1 | **Hecho.** Contexto de tienda: `current_tenant()` y `tenant_scope(t)`. En la caja de Hubara, fuera de un alcance, es `hubara`; una caja de una tienda la nombra con `ACKTOS_TENANT`; la celda (`ACKTOS_CELL=1`) no tiene tienda por defecto. | `src/platform/tenant/`, exportado en `src.sdk.tenantkit` | S |
| F1.2 | Interceptor de Temporal que propaga la tienda. Prueba con dos tiendas en el servidor de pruebas: workflow → activity → child → señal. | `src/platform/temporal/client.py`, los workers en `src/plugins/*/workers/` | M |
| F1.3 | Configuración por tienda: las 29 lecturas de entorno al importar de `src/platform/config.py`, y las demás (238 en total), pasan a `tenant_config()`. Un gate prohíbe lecturas nuevas al importar. | `src/platform/config.py` y sus usuarios | L |
| F1.4 | Vault por tienda: `vault_dir()` en lugar de `WORKSPACE_VAULT_DIR` (81 módulos, 88 `FilesystemMetadataStore(...)`, 55 uniones directas de `metadata.json`). En la caja de Hubara apunta a la raíz de siempre. Va detrás de puertos (`MetadataStore`) para que el paso a Postgres (F8) sea solo cambiar la implementación. | `src/platform/state.py` y sus usuarios | L |
| F1.4a | Lo nuevo del vault que trajo `main` también va por tienda: el buzón de avisos de etapa de pedidos (`_orders/stage_notices/`, #444) y el registro de entrada de la entrada durable. | `orders/`, `chats/agent/sales/` | S |
| F1.4b | Los recorridos del vault que se multiplican por tienda: un índice mensaje→sesión para `_locate`, que hoy recorre todo el vault en cada aviso de estado de WhatsApp, y una reconciliación de pedidos incremental en vez de cada 5 minutos. | `ingest_delivery_status.py`, `orders/vault_scanner.py` | M |
| F1.5 | Fábricas y cachés por tienda: unos 63 `lru_cache` con estado de tienda y unas 15 variables globales (ingest, dashboard, Ads, analytics, y ahora `_INBOUND_ENTRY` de la entrada durable) pasan a tener la tienda como llave. Se quitan los 7 enlaces al importar del worker de ventas. | `chats/agent/sales/composition.py`, `chats/workers/sales.py`, `medusa/`, `orders/`, `catalog/`, `promotions/` | M |
| F1.6 | La tienda deja de ser código (detalle debajo). | `chats/agent/**` | L |
| F1.7 | Tools por agente: `_EXTENSIONS` pasa a tener el agente como llave (archivo protegido, va en el ADR). Así se pueden juntar los workers en 2 procesos sin que ventas reciba las tools de remarketing (L-12). | `src/platform/tool_extensions.py` | S |
| F1.8 | Temporal multi-tienda (detalle debajo). | `platform/temporal/dispatcher.py`, `plugin.yaml`, workers | M |
| F1.9 | WhatsApp por tienda (detalle debajo). | `chats/api/sales.py`, `platform/whatsapp/` | M |
| F1.10 | API, panel y app por tienda (detalle debajo). | `platform/auth.py`, `chats/api/{dashboard,mobile}.py`, `frontend_dashboard/`, `android_operator/` | M |
| F1.11 | Comercio y Meta por tienda: clientes de Medusa, catálogo de Meta, CAPI y Ads vía `TenantConfig`. El control del bot por comando (`infra/scripts/bot_control.sh` y `decisions/control.py`) recibe la tienda. | `medusa/`, `catalog/`, `ads/`, `decisions/control.py` | M |
| F1.12 | Gates (detalle debajo). | `tests/` | M |

**F1.6, la tienda deja de ser código:**
- `POLICY` y `CODES` (`chats/agent/sales/config/*.py`) pasan a `tenant_policy()`, junto con el mínimo de contra entrega que ahora comparten ventas y remarketing (`chats/shared/payment_terms.py`). En la rama del PR #410 ese mínimo ya sale de la configuración de la tienda (merge del 2026-10-21).
- Los guiones de campaña (`chats/shared/campaigns/<id>.yaml`, #449) y el envío gratis por cajas (#451) son de la tienda: van en su paquete (`acktos-tenants/tenants/<tienda>/campaigns/`), y las cajas (`sold_by_box`) en su `domain.yaml`.
- `store_domain()` se resuelve por tienda.
- Las descripciones de las tools se arman al crear las tools de la sesión, no al importar. Hoy se fijan con `_V = vocabulary()` en `tools/catalog.py`, `ui_intents.py`, `order_draft.py` y `order_registration.py`.
- Los ~50 literales «Hubara» del agente pasan a `tenant.brand`.
- El workspace va por tienda. Hoy el dispatcher pierde la ruta del workspace al iniciar otro worker (`platform/temporal/dispatcher.py:116,175`).
- Al terminar, sobran las 40 reglas de forge que tocan código. Un ratchet lo vigila: el escáner de forge en modo crítico sobre el código que corre.

**F1.8, Temporal multi-tienda:**
- En la celda, los IDs llevan el prefijo `<tienda>:`. En la caja de Hubara los IDs no cambian, porque hay workflows vivos.
- **Ahora es obligatorio, no prolijidad.** Desde #444 los dispatchers arrancan con `id_conflict_policy`: `TERMINATE_EXISTING` (la sesión de ventas) o `USE_EXISTING`. Sin prefijo, si el mismo cliente le escribe a dos tiendas, el workflow de una termina o reusa el de la otra, sin error visible. La suite de dos tiendas (F1.12) lo prueba con el mismo número en las dos.
- Los IDs que trajo `main` también llevan el prefijo: `inbound-<wamid>`, `inbound-wa_<número>` (entrada durable), `campaign-send-<id>` y sus lotes `…-batch-NNNNNNN` (#442).
- Los ~9 IDs y schedules fijos se vuelven por tienda: `order-reconciliation`, `sales-eval`, `decisions-probe`, `post-sale-return`, `reengagement-cycle`, `order-sentinel-cycle`, entre otros.
- Ya ayuda lo de #444: un cliente de Temporal por proceso (con un solo namespace `acktos` alcanza), continue-as-new por run y apagado ordenado.
- Se agrega el atributo de búsqueda `TiendaId`.
- Si hace falta, equidad entre tiendas con la clave *fairness* de Temporal.

**F1.9, WhatsApp por tienda:**
- `/webhook/<tienda>` lleva el token de verificación y el app secret de esa tienda.
- `/webhook` sigue siendo la tienda por defecto.
- Se rechaza un `phone_number_id` que no sea de la tienda.

**F1.10, API, panel y app por tienda:**
- El JWT lleva un claim de tienda (un grupo de Cognito), y `require_auth` devuelve la tienda.
- Todos los endpoints filtran por tienda.
- El muestreo y el SSE del dashboard, y la app móvil, solo ven su tienda. Hoy el SSE le manda todas las sesiones a todos.
- Los plugins se habilitan por tienda.
- El panel es un solo build: la marca y los plugins salen de `GET /api/tenant`.
- La app (`com.acktos.operator`) es una sola para todas las tiendas.

**F1.12, gates:**
- Una suite con dos tiendas sintéticas (`acme`, `beta`) corre una venta completa en cada una y verifica que no se cruza nada: vault, Temporal, API, SSE, app.
- Un gate de arquitectura exige: ninguna configuración de tienda por entorno al importar, y todo acceso al vault por `vault_dir()`.

**Para el Hito V hacen falta** F1.1–F1.9, F1.11 y la parte de F1.12 que cubre una tienda.
**Para el Hito H hace falta** todo F1, en especial F1.10 y F1.12 completos.

**Salida de F1:** Hubara en producción con el código nuevo y el mismo comportamiento (suite completa verde y canary), y la suite de dos tiendas verde.

### F2. Infraestructura de Acktos (1–2 semanas, en paralelo con F1)

- **Cuenta y bootstrap.** La cuenta según el §9. El bootstrap crea el bucket de estado, el lock, el OIDC de GitHub y los roles de CI, adaptando `infra/scripts/aws_bootstrap.py`.
- **Terraform nuevo `infra/terraform/acktos/`**, con estado propio:
  - `cell`:
    - la EC2 t4g.large;
    - un disco raíz de 20 GB;
    - el vault en un EBS de 20 GB con `prevent_destroy`;
    - una IP fija;
    - IAM mínimo;
    - CloudWatch: memoria y disco, más memoria por proceso;
    - una alarma;
    - snapshots DLM.
  - `rds`: t4g.micro, una base por tienda, respaldos de 7 días.
  - `storage`: S3 para fotos, respaldos y paquetes, con reglas de ciclo de vida.
  - `panel`: un S3 con CloudFront.
  - `auth`: un pool de Cognito con un grupo por tienda.
  - `tenant`: por tienda; crea el SSM `/acktos/<tienda>/*`, el grupo, la base y el prefijo en S3. Se configura con `tenants = { vincenzo = {...} }`.
- **Compose de la celda generado, no escrito a mano** (a diferencia de `infra/compose/docker-compose.prod.yml`):
  - api, 2 workers, litellm, caddy con varios dominios, redis y un medusa por tienda;
  - `mem_limit` por servicio;
  - los contenedores no pueden usar el rol de la caja (IMDS con hop limit 1), salvo el que lo necesite.
- **Imágenes ARM** (arm64) del motor y de Medusa, con build multi-arquitectura en el CI.
- **CI de despliegue:** una sola imagen para todas las tiendas, primero en una tienda de prueba. Los respaldos se prueban con un ensayo: restaurar RDS y el vault.
  - Desde #444, volver atrás es solo hacia adelante: con workflows vivos no se despliega código viejo (`infra/DEPLOY_RUNBOOK.md`). Vale igual para la celda: un despliegue malo se arregla con otro hacia adelante, y por eso va primero a la tienda de prueba.
  - Cada worker se apaga ordenado (SIGTERM, `run_worker` y 45 s de gracia), así que se puede actualizar una réplica de `worker-conversa` mientras la otra atiende.
- **Observabilidad mínima:** CloudWatch, sin caja de SigNoz, y OTel apagado (`OTEL_SDK_DISABLED=true`).
- **LLM:** un juego de llaves para la plataforma. El costo se atribuye por tienda con los costos por conversación que ya se registran, más la dimensión tienda.

### F3. Comercio en la celda (1–2 semanas)

- **Imagen `acktos-medusa`:**
  - parte de `HubaraAdministrador/hubara_backend` (Medusa 2.12.5), sin lo propio de Hubara;
  - los módulos de Redis (event bus, workflow engine, caché y locking) van contra el Redis compartido, con prefijo por tienda;
  - los archivos van en S3 o R2 con prefijo por tienda.
- **Medusa de Vincenzo:**
  - base `medusa_vincenzo` en RDS;
  - contenedor `medusa-vincenzo`;
  - admin en `admin.vincenzo.<acktos>`;
  - carga inicial: Morral Nómada (tallas M y XL), región Colombia, COP, canal de venta y publishable key.
- **Red interna:** el motor habla con Medusa dentro de la caja, sin salir a internet.

### F4. Alta de Vincenzo: forge deja de clonar repos y pasa a dar de alta tiendas (1 semana)

- **Comando nuevo `forge tenant create <tienda>`.** Hace lo siguiente:
  - valida el paquete, con el mismo esquema de hoy: `commerce`, `domain.yaml` y workspace;
  - lo publica en `acktos-tenants` y en S3;
  - agrega la tienda al Terraform;
  - carga los secretos en SSM;
  - crea el grupo y los usuarios de Cognito;
  - crea los schedules de Temporal de la tienda.
- **Poner al día el paquete de Vincenzo.** Hoy `forge/clients/vincenzo/` trae solo `client.yaml` y el workspace de ventas y remarketing (22 archivos). Falta:
  - `commerce`: Nequi, recargos, zona local, tarifas, mínimo de contra entrega, SKU y colecciones;
  - `domain.yaml`;
  - el workspace de ETA;
  - repasar los skills contra `ventas-7`;
  - las fotos del morral y las medidas de la talla XL.
- **WhatsApp de Vincenzo:**
  - número y WABA propios;
  - **su propia app de Meta**, como hoy Hubara (decisión del §9): su app secret va en SSM `/acktos/vincenzo/`;
  - **en paralelo, Acktos tramita ser proveedor tecnológico de Meta** (verificación del negocio, revisión de la app y alta embebida). No bloquea a Vincenzo; las tiendas siguientes se dan de alta con la app de Acktos;
  - el webhook en `https://api.<acktos>/webhook/vincenzo`;
  - plantillas aprobadas en su WABA (seguimiento, ETA, campañas);
  - el Flow del formulario de envío creado en **su** WABA, porque los Flows son por WABA.
- **Panel y app:** los usuarios de Vincenzo van en el pool de Acktos, en el grupo `vincenzo`. La app móvil es la misma, con la configuración de la tienda.

### F5. Prueba y salida a producción de Vincenzo (1–2 semanas)

- **Laboratorio** con la configuración de Vincenzo (conversaciones simuladas y juez) antes de abrir el número.
- **QA de la app** en el emulador.
- **Apertura gradual:** primero los números internos.
- **Criterio propuesto para declarar a Vincenzo estable** (y abrir el Hito H):
  - 4 semanas sin incidentes graves;
  - pedidos de punta a punta: bot → Medusa → ETA;
  - el costo real por conversación medido;
  - la memoria de la caja estable;
  - un ensayo de restauración de respaldos hecho.

### F6. Reactivación compartida con GraphAgents (1–2 semanas, **antes** de salir a producción: decisión del §9)

- **Una sola caja de GraphAgents** por uso para toda la celda; hoy hay una por proyecto.
- **El ciclo de reengagement** manda la tienda en el snapshot, y los resultados vuelven a la tienda correcta.
- **Las credenciales de Meta Ads** van por tienda.
- **Sin F6, Vincenzo no tiene reactivación automática:** hoy el Window Strategist es el único camino.

### F7. Migración de Hubara (2–3 semanas con ensayo; detalle en el §7)

### F8. Después (opcional)

- **Medusa de Hubara:** mudarla de Railway a la celda (pg_dump y restore a RDS, y su contenedor). Cambian la URL de su web (Astro) y los webhooks de Medusa.
- **Renombrar lo interno** a Acktos (`hubara_agency`, `HubaraSalesSessionWorkflow`…). En el namespace `acktos` no hay workflows viejos que romper. El repo de GitHub se puede renombrar, porque GitHub redirige.
- **El modo clon de forge** queda solo para un plan dedicado, o se retira.
- **Cuando la celda se llene** (~10–15 tiendas): el estado pasa a Postgres y S3 (la implementación de los puertos de F1.4), y se agrega una segunda caja.

## 6. Mapa del código: qué tan lejos está hoy

Viene de la auditoría del 2026-10-19. Hoy el motor es «un proceso = una tienda» a propósito.

| Área | Esfuerzo | Bloqueo principal |
|---|---|---|
| Identidad de la tienda y su configuración | L | No hay contexto de tienda. 238 lecturas de entorno (37 al importar). `render-env-from-ssm.sh` aplana un solo árbol de SSM. |
| Vault | L | Ruta importada por valor en 81 módulos y bloqueo (`flock`) de disco local. |
| Recorridos del vault | M | 19 módulos. `_locate` es O(N) por cada aviso de estado. La reconciliación corre cada 5 minutos. |
| WhatsApp | M | Un solo verify token y app secret. El token es una constante. El `phone_number_id` ya se lee y se guarda. |
| Medusa | M | Unas 16 fábricas `lru_cache(1)` y 7 enlaces al importar en el worker de ventas. |
| Meta (catálogo, CAPI, Ads) | S | Casi todo se lee del entorno en cada llamada. |
| LLM y LiteLLM | S | Las llaves se pueden compartir. Falta atribuir el costo por tienda. |
| Workspace de agentes | M | El dispatcher pierde la ruta. Las descripciones de las tools y la marca se fijan al importar. |
| Paquete de decisiones | S | Ya va por id de paquete. Solo falta el vocabulario que se fija al importar. |
| Política comercial | M | `POLICY` y `CODES` son constantes de módulo, usadas por unos 16 módulos. |
| Temporal | L | IDs armados solo con el teléfono, sin tienda en las entradas, unos 9 IDs y schedules fijos. |
| Cognito, API y SSE | M | Un pool por proceso, sin claim de tienda, y el SSE le manda todas las sesiones a todos. |
| Cachés y globales | M | 63 fábricas en caché y unas 15 globales. |
| Juntar workers en un proceso | S | `_EXTENSIONS` global (L-12). |

## 7. Migración de Hubara (resumen del runbook)

**Prerrequisitos**
- F1 completo en Hubara desde hace al menos 2 semanas.
- Vincenzo estable según el criterio de F5.
- La celda con margen de memoria medido; si no, pasar a t4g.xlarge con stop/start (unos minutos).
- Un ensayo completo con una copia de los datos.

**Pasos**
1. **Ensayo (día −7):**
   - copiar el vault de Hubara a una tienda `hubara-ensayo` en la celda;
   - correr las lecturas (dashboard, pedidos, Ads) contra esa copia;
   - medir la memoria;
   - inventariar los workflows abiertos en el namespace viejo por tipo: sesiones, remarketing de 24 horas, ETA de 7 días, watchdogs.
2. **Alta:**
   - `forge tenant create hubara`, con la configuración de hoy: SSM `/hubara/hubara/*`, su política, `ventas-7` y su workspace.
   - Su Medusa sigue en Railway; la tienda apunta ahí.
   - Las plantillas y el Flow ya existen en su WABA.
3. **Día D, dentro del horario quieto (21:30–08:00):**
   1. Última copia incremental del vault (rsync) a `/mnt/acktos-vault/hubara/`.
   2. Cambiar el webhook en Meta a `https://api.<acktos>/webhook/hubara`. Antes del cambio, que la caja vieja no tenga `inbound-*` sin terminar en su namespace (duran segundos); después, los mensajes entran directo al namespace `acktos`.
   3. Recrear lo largo a partir de los datos: ETA desde los pedidos abiertos y remarketing desde el índice de reengagement. Terminar esos workflows en el namespace viejo, para que la caja vieja no siga escribiendo en su vault.
   4. Las sesiones de venta abiertas se retoman con el historial copiado en el siguiente mensaje del cliente.
   5. Pasar los usuarios del panel al pool de Acktos, en el grupo `hubara`. La app toma la configuración nueva.
4. **Vigilancia de 72 horas.**
   - Rollback: el webhook vuelve a la caja vieja y se hace rsync inverso de lo nuevo.
5. **Apagar:**
   - la caja de la app de Hubara;
   - la de observabilidad (liberar su IP y su disco);
   - la de GraphAgents vieja;
   - borrar el tenant fantasma `vincenzo` del platform de Hubara;
   - archivar el estado de Terraform y tomar un snapshot final del vault.

## 8. Riesgos

| Riesgo | Impacto | Cómo se contiene |
|---|---|---|
| El cambio a multi-tienda rompe Hubara | Alto | Todo va detrás de la tienda por defecto, sin cambiar IDs ni rutas en su caja. Pasos chicos con canary y suite completa por PR. |
| Choque de IDs de Temporal entre tiendas | Alto | Desde #444 un ID repetido termina o reusa el workflow de la otra tienda (`id_conflict_policy`). Prefijo `<tienda>:` en todos los IDs de la celda, y la suite de dos tiendas con el mismo número de cliente en las dos. |
| El worker de conversación es el único camino de entrada | Medio | Desde la entrada durable, si se cae no entra ningún mensaje de ninguna tienda (esperan en Temporal). 2 réplicas en la celda, alarma `sales-worker-down` y el ADR pendiente de #443 (PM-002). |
| Se cruzan datos entre tiendas | Alto | Suite de dos tiendas, gates, SSE y app por tienda, claim de tienda en el login. Revisión de seguridad antes del Hito H. |
| Una caída tumba todas las tiendas de la celda | Medio | Respaldos probados (RDS con restauración a un punto en el tiempo, DLM del vault), runbook para reemplazar la caja en menos de 30 minutos, alarmas, celdas chicas. |
| Una tienda acapara recursos | Medio | `mem_limit` por servicio, presupuesto y RPM por tienda en LiteLLM (con su base en RDS), y *fairness* en Temporal. |
| Meta trabaja por WABA | Medio | Checklist de F4: plantillas y Flow recreados en el WABA de Vincenzo, y app secret por tienda. |
| Hubara no cabe en 8 GB | Medio | Medir en el ensayo. Pasar a t4g.xlarge con stop/start, o abrir una segunda celda. |
| El costo real sale distinto | Bajo | Medición semanal: CloudWatch, el uso en Temporal y el costo por conversación con la dimensión tienda. |
| El plan se alarga | Medio | Hitos separados. Vincenzo solo necesita la parte de F1 que cubre una tienda. El resto se termina antes de que entre Hubara. |

## 9. Decisiones del operador

**Tomadas (2026-10-21):**

1. **Cuenta de AWS:** una cuenta nueva dentro de una organización. Separa permisos, cuotas y gastos; un `terraform apply` de Acktos no puede tocar a Hubara, y apagar lo viejo es más fácil.
2. **Postgres:** RDS t4g.micro, unos US$14 más al mes, con respaldos automáticos y restauración a un punto en el tiempo.
3. **App de Meta:** Vincenzo usa su propia app ahora. En paralelo, Acktos tramita ser proveedor tecnológico para las tiendas siguientes.
4. **Reactivación:** Vincenzo sale con ella desde el primer día. F6 va antes del lanzamiento y el Hito V se corre 1–2 semanas.

**Con lo recomendado (2026-10-22), salvo que el operador diga otra cosa:**

6. **Criterio para mudar Hubara:** el de F5.
7. **Nombre:** «Acktos Stores» para la plataforma. El repo y el código se renombran al final (F8).

**Pendiente:**

5. **Dominio de Acktos**, para la API, el panel y los admins de Medusa. Lo ideal es uno propio desde el inicio: cambiarlo después obliga a tocar el webhook de Meta, el login y la app. Además, la verificación de Acktos ante Meta pide un sitio en el dominio. El 2026-10-22, `acktos.com` estaba registrado (desde 2012) y `acktos.co` y `acktos.store` estaban libres; se recomienda `acktos.co`. Mientras tanto se arranca con sslip.io, como Hubara.

## 10. Lo que no hacemos por ahora

- **EKS / Kubernetes:** sigue descartado.
- **Lambda:** no ahorra (`lambda_web_functions_eval`).
- **Hetzner:** sus máquinas baratas están solo en Europa y sin disponibilidad, y en EE. UU. salen más caras que AWS.
- **Mover el vault a Postgres** antes de necesitar una segunda caja.
- **Renombrar el código** antes de mudar Hubara.
- **Medusa multi-tienda o Mercur.**

## 11. Relación con lo que ya existe

- **`VINCENZO_SPLIT_PLAN.md`** (silo, 2026-07-09): queda reemplazado para Vincenzo. Su análisis de choques de nombres a nivel de cuenta y su checklist de Meta siguen sirviendo.
- **`MULTI_TENANT_COMMERCE_ARCHITECTURE.md` y `ADR-2026-06-05`:** se mantiene su eje A (un Medusa y una base por tienda). El «multi-tenant compartido en Hubara (1 proceso, N tiendas)» que esos documentos dejaron para después es justo F1.
- **forge:** su manifest (qué es motor y qué es tienda) es el mapa de F1.6. El `commerce`, el `domain.yaml` y el workspace de cada cliente forman el paquete de tienda, y `forge tenant create` reemplaza a `forge apply` para las tiendas pequeñas.
- **PR #410:** conviene mergearlo igual. Cierra el IAM entre proyectos de la cuenta y pasa la política comercial a configuración, lo que F1.6 aprovecha.
- **Temporal:** Hubara pasa ya a Developer (pago por uso), y Acktos usa la misma cuenta con el namespace `acktos`. El refactor de Temporal que entró el 2026-10-21 (#443, #444, #448, #450) juega a favor de la celda: un cliente por proceso, apagado ordenado, entrada durable y secretos fuera de los payloads. Lo que suma trabajo está en F1.8 (IDs nuevos y `id_conflict_policy`).
