# AgencyHubara — Infraestructura Cloud

> **Estado:** decisión tomada · pendiente de implementar (Terraform + mods de código).
> **Objetivo rector:** costo mínimo para un equipo **bootstrapped** (sin capital levantado), sin sacrificar confiabilidad en el camino crítico (conversaciones de WhatsApp que generan ingresos).
> **Fecha de decisión:** mayo 2026.

Este documento es la **fuente de verdad de dónde se aloja cada componente**. Lo retomamos para desarrollar el Terraform (`infra/terraform/`) y las modificaciones de código necesarias.

---

## 1. Resumen ejecutivo (la decisión)

| Componente | Dónde se aloja | Modelo | Costo/mo |
|---|---|---|---|
| **Frontend dashboard** (SPA Vite/React) | **AWS S3 + CloudFront** | Estático, CDN global | **$0** (free tier perpetuo 1TB/mes) |
| **Autenticación de usuarios** | **AWS Cognito** (1 user pool por compañía) | Managed, client-side PKCE | **~$0** (usuarios internos) |
| **FastAPI** (HTTP + webhooks) | **VPS** (docker-compose) | Always-on | incluido en VPS |
| **LiteLLM proxy** | **VPS** (misma caja, 1 réplica) | Always-on | incluido en VPS |
| **Temporal workers** (sales / remarketing / catalog) | **VPS** (contenedores) | Always-on, pollers | incluido en VPS |
| **Session store** (`hubara_vault`) | **Disco local de la VPS** | Filesystem | **$0** |
| **Secretos** | **AWS SSM Parameter Store** (tier estándar) | Managed | **$0** |
| **Temporal Server** | **Temporal Cloud** (1 cuenta, N namespaces, **auth por API key**) | Managed SaaS | **$0** con créditos / **$100** después |
| **TLS del endpoint público** | **Caddy auto-TLS** en la VPS (Let's Encrypt) | — | **$0** |
| **Observabilidad — SigNoz** (traces/metrics/logs + dashboards) | **VPS de observabilidad dedicada** (Hetzner, **compartida entre tenants**, off-critical-path) | Self-host Community, always-on | **~$8–16/mo** (1 caja; licencia **$0**) |
| **OpenTelemetry SDK** (instrumentación) | Dentro de los workers + API + browser (compute ya pago) | Librería | **$0** |
| **OpenLIT** (GenAI: tokens/costo/latencia del LLM) | Dentro de los workers (compute ya pago) | Librería (auto-instrumenta LiteLLM) | **$0** |
| **RAGAS** (eval de calidad: faithfulness…) | Worker `sales_eval` existente (Scheduled Workflow, offline) | Librería | **$0** infra (+ tokens del judge) |
| **system_explorer** (visualización) | No se despliega en prod (solo local) | On-demand | **$0** |

**Costo total de infra (ambas compañías): ~$21–39/mo** mientras duren los créditos de Temporal Cloud; **~$121–139/mo** después (de los cuales $100 es el piso de Temporal y ~$8–16 la VPS de observabilidad). **El mayor costo variable real son los tokens de LLM**, no la infra.

> **La observabilidad agrega UNA sola caja always-on** (la VPS de SigNoz). OpenTelemetry, OpenLIT y RAGAS son **librerías que viajan sobre compute que ya pagás** (los workers + la API) → $0 incremental. El único costo nuevo de infra es el host de SigNoz; ver §3.10.

### Lo que NO usamos (y por qué)
- **❌ Cloudflare Pages para el frontend** — evaluado y descartado. Su ventaja (bandwidth ilimitado) es irrelevante para un dashboard **interno** de bajo tráfico, y **CloudFront también es $0** dentro del free tier perpetuo (1TB/mes + 10M requests). Ganó la **consolidación en AWS**: un solo proveedor, una factura, un Terraform, y tracking de costo por tenant trivial con tags. (Alternativa válida si se prefiere git-push deploy dentro de AWS: **Amplify Hosting**.)
- **❌ EKS / Kubernetes** — los manifiestos en `hubara_agency/k8s/aws-produccion/` quedan como *ruta de alto volumen futura*, NO se usan ahora. EKS cuesta ~$315–400/mo en infra (control plane $73 + nodos + ALB + NAT + EFS) — desproporcionado a nuestra escala.
- **❌ HashiCorp Vault** — el "vault" del repo es un store de sesiones en filesystem, no un servicio de secretos. Para secretos usamos SSM.
- **❌ AWS Lambda para workers** — Temporal Serverless Workers (Lambda) ya existe (2026) pero está en Pre-release y, para nuestro workload conversacional always-on, un contenedor en VPS es más barato y simple. Reevaluar a futuro si aparecen patrones spiky con valles muertos.
- **❌ mTLS / certificados para Temporal** — usamos **API key auth** (recomendación oficial de Temporal para equipos sin PKI). Evita generar y rotar certs.
- **❌ Self-host de Temporal** — descartado por el riesgo operativo (backups del Postgres, uptime, upgrades) desproporcionado al ahorro de $100/mo para un equipo chico con tráfico de ingresos. Cloud con API key es trivial de conectar y los créditos lo hacen gratis al inicio.
- **❌ SigNoz Cloud** — evaluado y descartado: piso de **$49/mo** confirmado, no viable para bootstrapped. Vamos con **SigNoz Community self-hosted** (licencia **$0**) en una VPS de observabilidad *off-critical-path*. Nota la **asimetría deliberada con Temporal**: ahí pagamos managed porque self-host caía sobre el camino crítico de ingresos; acá self-hosteamos porque la observabilidad está **fuera** del camino crítico — si se cae, se pierde visibilidad, no negocio. El código emite OTLP genérico, así que migrar a Cloud/Grafana/Datadog después = **una env var, cero código** (cero lock-in). Ver §3.10.
- **❌ Co-locar SigNoz en la VPS de la app** — ClickHouse + Zookeeper piden ~4–8GB para sí solos; co-locarlos forzaría subir de tier ambas cajas de la app **y** acoplaría el crecimiento de disco de ClickHouse / un OOM al camino de ingresos. Va en caja separada (§3.10).

---

## 2. Topología

```
                          ┌─────────────────────────────┐
   Usuarios (operadores)  │   AWS S3 + CloudFront (SPA)  │   ← frontend estático, $0 (free tier 1TB)
        navegador  ─────► │   frontend_dashboard/dist    │
                          └──────────────┬──────────────┘
                                         │ (login client-side PKCE)
                                         ▼
                          ┌─────────────────────────────┐
                          │   AWS Cognito (user pool)    │   ← auth managed, ~$0
                          └─────────────────────────────┘
                                         │ JWT
                                         ▼
   WhatsApp / Meta ─┐                                          ┌──── DeepSeek V4 Pro
   Medusa webhooks ─┼──► Caddy (auto-TLS) ──────────► VPS      │
                    │                                    │     └──── Gemini (fallback)
                    │   ┌────────────────────────────────┼────────────────┐
                    │   │  VPS (docker-compose, always-on)│                │
                    └──►│   • FastAPI        :8000  ──────┘ (LLM via LiteLLM)
                        │   • LiteLLM proxy  :4000                          │
                        │   • worker: chats/sales                           │
                        │   • worker: chats/remarketing                     │
                        │   • worker: catalog/sync                          │
                        │   • hubara_vault/  (disco local = session store)  │
                        └───────────────────────┬───────────────────────────┘
                                                 │ gRPC + API key (TLS 1.3)
                                                 ▼
                                  ┌──────────────────────────────┐
                                  │   Temporal Cloud              │
                                  │   namespace: hubara           │
                                  │   namespace: vincenzo         │
                                  └──────────────────────────────┘
```

**Camino crítico de un mensaje:** WhatsApp → Caddy/VPS → FastAPI (`POST /api/chats/inbound`, signal barato) → Temporal Cloud encola (durable) → worker consume → activity LLM vía LiteLLM → respuesta. La task queue de Temporal absorbe los picos (backpressure); un spike sube latencia, no tira el sistema.

**Observabilidad (FUERA del camino crítico):** los workers + la API de ambos tenants exportan OTLP a una **VPS de observabilidad dedicada y compartida** (SigNoz self-host). Si esa caja se cae, el mensaje sigue fluyendo — se pierde visibilidad, no negocio. Por eso vive aparte de las cajas de la app y se self-hostea (§3.10).

```
   VPS Hubara ────┐
                  ├─ OTLP gRPC :4317 / HTTP :4318 ──┐  (red privada Hetzner / WireGuard)
   VPS Vincenzo ──┘                                 │       ┌─────────────────────────────────┐
                                                     ├──────►│  VPS de Observabilidad          │
   dashboard React (browser) ── OTLP HTTP :4318 ─────┘       │  (Hetzner ~8GB, compartida)     │
        (CORS, traceparent W3C)                              │   • SigNoz UI/query      :8080   │
                                                             │   • otel-collector  :4317/:4318  │
                                                             │   • ClickHouse + Zookeeper       │
                                                             │     (NUNCA públicos)             │
                                                             └─────────────────────────────────┘
```

---

## 3. Componente por componente

### 3.1 Frontend — AWS S3 + CloudFront
- **Qué es:** SPA pura (Vite + React 19 + TanStack Query + Tailwind v4), sin SSR. Build → `dist/`.
- **Por qué S3 + CloudFront (no Cloudflare):** para un dashboard **interno** de bajo tráfico, CloudFront es **$0** dentro del free tier perpetuo (1TB egress + 10M requests/mes). La consolidación en AWS (una factura, un Terraform, tracking por tenant con tags) pesa más que el git-push de Cloudflare Pages. El origin S3→CloudFront es gratis.
- **Topología:** bucket S3 privado (archivos del build) + distribución CloudFront con **OAC** (Origin Access Control) + cert en **ACM** + `index.html` como default root object + custom error response 403/404 → `/index.html` (para client-side routing de la SPA). Invalidación de cache en cada deploy.
- **Hosting independiente de la auth:** Cognito es client-side; servir desde S3+CloudFront, Cloudflare o localhost le da igual. La ubicación del frontend **NO** cambia la integración con Cognito.
- **Config clave:** `VITE_API_URL` apunta al dominio público de la FastAPI de cada compañía. Build per-tenant con sus IDs de Cognito (pool + app client).
- **Auth:** integración client-side con Cognito (Authorization Code + PKCE) vía `react-oidc-context` / `oidc-client-ts` (o Amplify).
- **Alternativa AWS con git-push:** **Amplify Hosting** (DX tipo Cloudflare Pages, ~$0 el 1er año). Descartada por ahora a favor del control + always-free de S3+CloudFront.

### 3.2 Autenticación — AWS Cognito
- **Modelo:** 1 user pool por compañía (aislamiento por cliente). Pocos usuarios internos (operadores de agencia) → cae en free tier, **~$0**.
- **Flujo:** la SPA hace login contra Cognito, obtiene JWT, lo manda a FastAPI. FastAPI valida el JWT contra el JWKS del pool (`https://cognito-idp.<region>.amazonaws.com/<pool_id>/.well-known/jwks.json`).
- **Pendiente de código:** middleware de validación de JWT en FastAPI (hoy las rutas no exigen auth). Ver §7.

### 3.3 FastAPI — VPS
- **Entrypoint:** `hubara_agency/run_api.py` (uvicorn programático, `0.0.0.0:8000`). Auto-discovery de routers de plugins.
- **Por qué always-on:** recibe webhooks de **WhatsApp** (`POST /api/chats/inbound`) y **Medusa** + sirve queries del dashboard. Necesita endpoint HTTPS público estable.
- **TLS:** **Caddy** en la caja con auto-TLS (Let's Encrypt) como reverse proxy → mantiene el stack consolidado sin depender de Cloudflare. (Si en el futuro se quiere DDoS gestionado, se puede poner Cloudflare proxy delante; opcional.)

### 3.4 LiteLLM proxy — VPS (misma caja)
- **Config:** `exoclaw-temporal/litellm_config.yaml`, puerto **4000**. Rutea `deepseek-v4-pro` (primario) → `gemini-...` (fallback) con `simple-shuffle`.
- **Réplicas:** **1** en prod (los manifiestos k8s ponen 2 para HA; innecesario a esta escala).
- **Stateless** — no necesita storage.

### 3.5 Workers — VPS (contenedores)
- **Workers:** `chats/sales`, `chats/remarketing`, `catalog/sync`. Pollers async I/O-bound (esperan al LLM, no queman CPU).
- **Réplicas:** 1 cada uno en prod inicial (k8s ponía 3 para sales). Escala horizontal = agregar contenedores/cajas; Temporal balancea la cola solo.
- **Concurrencia:** tunear `max_concurrent_activities` antes de agregar hardware — suele estar sub-utilizado.

### 3.6 Session store (`hubara_vault`) — disco local
- **Qué es:** store de metadata de sesión en filesystem (`WORKSPACE_VAULT_DIR`, JSON por conversación `wa_<phone>/metadata.json`). **NO es HashiCorp Vault.**
- **En VPS single-node:** disco local, $0. No se necesita EFS (que costaría $30+/mo y solo hace falta en multi-nodo).
- **Migración futura:** si se va a multi-nodo, mover a S3 o a un volumen de red. Por ahora, single-node + backup del directorio.

### 3.7 Secretos — AWS SSM Parameter Store
- **Qué guarda:** `DEEPSEEK_API_KEY`, `GEMINI_API_KEY`, `WHATSAPP_*`, `MEDUSA_*`, `META_*`, `TEMPORAL_API_KEY`.
- **Por qué SSM y no Vault:** tier estándar = **$0**, sin servidor que mantener. Vault server costaría infra extra.
- **Alternativa simple inicial:** `.env` cifrado en la caja. SSM es el upgrade limpio (lo provisiona el Terraform).

### 3.8 Temporal — Temporal Cloud con API key
Ver §5 (multi-tenant) y §6 (conexión). Decisión: **managed Cloud, auth por API key**, gratis durante créditos.

### 3.9 system_explorer — no en prod
- Herramienta de visualización (React Flow + nginx proxy). Observabilidad opcional, no camino crítico. Se usa **solo en local**. Si se quiere en prod, va estático a Cloudflare Pages apuntando `VITE_API_TARGET` a la FastAPI pública.

### 3.10 Observabilidad — OpenTelemetry + SigNoz + OpenLIT + RAGAS (HU-003)
- **Qué es:** el stack de observabilidad de los agentes, en **3 capas ortogonales**. Vendorizado en `hubara_agency/deploy/signoz/`; en local se levanta junto al stack (`docker-compose.local.yml` lo trae con `include:`). En prod va en su **propia VPS** (ver abajo).

| Capa | Qué es | Dónde corre | Costo |
|---|---|---|---|
| **OpenTelemetry** | El estándar/transporte (SDK + OTLP + semantic conventions): traces, metrics, logs | Librería dentro de workers + API + browser | $0 |
| **SigNoz Community** | El backend OTel-native (ClickHouse): almacena, dashboards, alertas | **VPS de observabilidad** (abajo) | ~$8–16/mo |
| **OpenLIT** | Auto-instrumenta LiteLLM → spans `gen_ai.*` + métricas tokens/costo/latencia | Librería dentro de los workers | $0 |
| **RAGAS** *(Parte B)* | Scoring de calidad (`faithfulness`…); emite `hubara.eval.*` de vuelta como métricas | Worker `sales_eval` existente (Scheduled Workflow, offline) | $0 infra + tokens del judge |

- **Las 4 tiers de señal** (definidas en `deploy/signoz/docker/otel-collector-config.yaml`):
  - **Tier 1 — backend:** workers (`sales-agent`…) + `api-gateway` → OTLP gRPC `:4317`. Un trace end-to-end por turno: webhook → workflow → activity → LLM → tool.
  - **Tier 2 — frontend:** el dashboard React (`dashboard-frontend`) → OTLP HTTP `:4318` (CORS). El `traceparent` W3C encadena browser → api-gateway.
  - **Tier 3 — infraestructura:** receiver `hostmetrics` scrapea CPU/mem/disk/net del host (pestaña Infrastructure de SigNoz).
  - **Tier 4 — logs:** puente stdlib `logging` → OTel-logs, correlacionados con su trace por `trace_id`.
- **Componentes del self-host SigNoz** (5 contenedores + 1 one-shot): **ClickHouse** (telemetry store — el que pesa), **Zookeeper** (coordinación de ClickHouse), **`signoz`** (query service + UI `:8080`), **`signoz-otel-collector`** (recibe OTLP `:4317/:4318` → ClickHouse), **`signoz-telemetrystore-migrator`** (migraciones de schema), + `init-clickhouse`.

#### Dónde alojarlo — la decisión (cheapest, off-critical-path)

> **Recomendado: una VPS de observabilidad DEDICADA y COMPARTIDA entre tenants** (Hetzner, ~8GB), separada de las cajas de la app. Self-host Community = **licencia $0**; el costo es la caja (~$8–16/mo para ambas compañías).

- **Por qué self-host y no SigNoz Cloud:** el piso de **SigNoz Cloud es $49/mo** (confirmado) — no viable bootstrapped, mismo criterio que el resto del doc. El código ya está vendorizado self-host en `deploy/signoz/`.
- **Por qué self-host es aceptable acá aunque NO lo fue para Temporal:** Temporal se descartó self-host porque su riesgo operativo (backups, uptime, upgrades) cae **sobre el camino crítico de ingresos**. La observabilidad está **fuera** del camino crítico — si SigNoz se cae, se pierde visibilidad, no negocio. El mismo razonamiento de riesgo que dijo "pagá Temporal managed" dice "self-hosteá SigNoz". La asimetría es deliberada.
- **Por qué caja separada y no co-locada en la VPS de la app:** SigNoz (ClickHouse + Zookeeper) pide ~4–8GB para sí solo. Co-locarlo forzaría subir Hubara 4→8GB y Vincenzo 8→16GB (+costo por tenant) **y** acoplaría el crecimiento de disco de ClickHouse (captura full prompt/completion) y un eventual OOM al camino de ingresos. **Una sola caja de obs para AMBOS tenants** amortiza el costo — mismo truco que "1 cuenta Temporal, 2 namespaces". SigNoz separa los tenants por `service.namespace` / `deployment.environment`.
- **Sizing (MEDIR):** arrancar en un **Hetzner CX32** (4 vCPU / 8GB, ~$8) y saltar a **CX42** (16GB, ~$16) si ClickHouse pide holgura. El **disco de ClickHouse es el driver de costo real** (full prompt/completion). Controlar con: **retention TTL 7–15 días**, **sampling** (head al inicio, tail-on-error al escalar) y **scrubbing/hash de PII** en el Collector en prod (números de WhatsApp, nombres). Agregar un volumen Hetzner (~$0.04/GB/mo) cuando ClickHouse crezca.
- **Networking / seguridad:** exponer **SOLO** los puertos del collector (`:4317/:4318`). **NUNCA** publicar ClickHouse (`:9000/:8123`) ni Zookeeper. Los workers de ambos tenants exportan al collector por la **red privada de Hetzner** (gratis, misma región) o un mesh **WireGuard/Tailscale** (gratis) si quedan cross-región. La UI de SigNoz (`:8080`) detrás de Caddy con auth o solo-Tailscale. Auth de ingesta en el collector (ingestion key).
- **Cero lock-in:** el código emite OTLP genérico (`OTEL_EXPORTER_OTLP_ENDPOINT`). Cambiar SigNoz self-host → SigNoz Cloud / Grafana / Datadog = **una env var, cero código**. Kill-switch global: `OTEL_SDK_DISABLED=true` apaga todo sin redeploy.
- **Fallback $0:** si ~$8/mo es demasiado al arranque, dejar los workers en `ConsoleSpanExporter` (default sin endpoint) o `OTEL_SDK_DISABLED=true`, y levantar SigNoz **on-demand / solo local** (como `system_explorer`) cuando haya que investigar. Perdés observabilidad always-on + alertas, pero el costo es $0.

---

## 4. Estrategia por compañía (multi-tenant)

Dos clientes: **Hubara** (~100 conversaciones/día ≈ 3.000/mes) y **Vincenzo** (~10.000 conversaciones/mes). Aislamiento por compañía en todas las capas:

| Capa | Hubara | Vincenzo | Aislamiento |
|---|---|---|---|
| Frontend | S3+CloudFront distro A | S3+CloudFront distro B | dominio separado |
| Cognito | user pool A | user pool B | usuarios separados |
| VPS | caja 4GB (~$5-8) | caja 8GB (~$8-15) | proceso + datos separados |
| LiteLLM | virtual key A | virtual key B | spend tracking separado |
| Temporal | namespace `hubara` | namespace `vincenzo` | **datos aislados, 1 sola cuenta** |
| Session store | disco VPS A | disco VPS B | filesystem separado |
| Observabilidad | `service.namespace=hubara`, env `hubara` | `service.namespace=vincenzo`, env `vincenzo` | **aislamiento lógico, 1 sola VPS de obs compartida** |

> **El frontend "fuera/dentro de AWS" NO afecta el multitenant.** El aislamiento vive en 4 capas: auth (pool de Cognito), authz (validación de JWT en FastAPI), compute+datos (VPS por tenant) y workflow state (namespace de Temporal). El frontend es el mismo build desplegado por tenant con env distinto — nunca es la frontera de seguridad.

### Tracking de consumo por tenant
El aislamiento físico **ya hace la atribución** — no hace falta un sistema de cost-allocation complejo:

| Costo por tenant | Cómo se mide |
|---|---|
| **Tokens LLM** (costo variable #1) | **LiteLLM spend tracking** — una *virtual key* por tenant; LiteLLM loguea gasto por key |
| **Temporal actions** | **Temporal Cloud UI muestra uso por namespace** (Hubara vs Vincenzo nativamente separados) |
| **VPS** | una caja por tenant → costo ya atribuido |
| **Frontend / Cognito** | ~$0 → irrelevante; si se quiere factura unificada, **cost allocation tags** (`tenant=hubara`) en Cost Explorer |
| **Observabilidad** | ~flat: **1 VPS compartida**, no escala por tenant; OpenLIT ya emite `gen_ai.usage.cost` por `service.namespace` → el costo LLM por tenant también se cruza en SigNoz |

**Clave de costo — Temporal:** el piso de $100/mo de Essentials es **por cuenta, no por namespace**. Corriendo ambos namespaces bajo **una sola cuenta**, las actions combinadas (~520k/mes) caben en el bundle de 1M → **un solo $100/mo cubre las dos compañías**. (Si se requiriera aislamiento de billing/legal por cliente, se separan cuentas — decisión de negocio, no técnica.)

**Dimensionamiento de VPS** (workload I/O-bound; el cuello real es el rate limit de DeepSeek, no la caja):
- **Hubara:** ~10 conv/hora, 5-15 concurrentes en pico. VPS 4GB sobra.
- **Vincenzo:** ~33 conv/hora, 20-60 concurrentes en pico. VPS 8GB sobra.
- **Provider sugerido:** Hetzner (US-East, ~$5/CX22, ~$8/CX32) por costo; alternativa todo-AWS: Lightsail ($10-20). La VPS solo habla con Temporal Cloud / Meta / DeepSeek / Medusa → no requiere proximidad a AWS.

---

## 5. Temporal Cloud — plan y costos

### Pricing (mayo 2026)
- **No hay free tier perpetuo.** Piso = Essentials = el mayor de **$100/mo o 5% del consumo**, e **incluye 1M actions/mes** + 1GB active + 40GB retained storage.
- **Overage:** $50 por millón (primeros 5M).
- **Créditos para arrancar (bootstrapped):** **$1.000 de trial** vía AWS Marketplace (sin tarjeta, **sin requisito de funding**) — es el offer estándar de cuenta nueva. Tiene **fecha de vencimiento** (se muestra en el signup).
- ❌ Programa startup ($6k): requiere funding → **no aplica** (somos bootstrapped).

### Actions por conversación (estimación — MEDIR en la UI)
Cada conversación genera: 1 start + 1 signal/mensaje + activities (LLM + tools + sends) + timers + heartbeats + retries.

| Diseño | Actions/conv |
|---|---|
| Lean | ~20 |
| Realista | ~40 |
| Heavy | ~80-100 |

### Proyección
| Compañía | Conv/mes | Actions/mes (realista) | ¿Excede 1M? | Costo Temporal |
|---|---|---|---|---|
| Hubara | 3.000 | ~120.000 | No | $0 (en bundle) |
| Vincenzo | 10.000 | ~400.000 | No | $0 (en bundle) |
| **Combinado (1 cuenta)** | 13.000 | **~520.000** | **No (<1M)** | **$100/mo** (o $0 con créditos) |

Vincenzo solo paga overage si su diseño supera ~100 actions/conv (>1M total). Mitigación: activities gordas (no fragmentar de más) + batchear sends.

---

## 6. Conexión a Temporal Cloud — API key (cambio decidido)

**Hoy** el repo conecta por mTLS:
```
TEMPORAL_URL=...
TEMPORAL_NAMESPACE=...
TEMPORAL_TLS_CERT_PATH=/etc/temporal-certs/temporal.pem
TEMPORAL_TLS_KEY_PATH=/etc/temporal-certs/temporal.key
```

**Decisión:** migrar a **API key auth** (más simple, sin certs que generar/rotar; misma encriptación TLS 1.3).
```
TEMPORAL_ADDRESS=<region>.<provider>.api.temporal.io:7233   # endpoint gRPC regional
TEMPORAL_NAMESPACE=hubara.<account_id>                      # namespace.account_id
TEMPORAL_API_KEY=<bearer-token>                             # desde SSM
```

**Mod de código pendiente** (Task #2): en la config de conexión (`hubara_agency/src/platform/config.py` + donde se hace `Client.connect`), pasar `api_key=...` y `tls=True` en vez de leer los cert paths. El SDK Python lo soporta nativo. Quitar/condicionar las env vars `TEMPORAL_TLS_*`.

---

## 7. Trabajo pendiente (próximas fases)

| # | Tarea | Detalle |
|---|---|---|
| 1 | **Migrar Temporal a API key** | §6 — mod en config de conexión + workers. Quitar dependencia de cert paths. |
| 2 | **Validación de JWT en FastAPI** | Middleware que valide el JWT de Cognito contra el JWKS del pool. Hoy las rutas no exigen auth. |
| 3 | **docker-compose de producción** | 1 réplica de FastAPI + LiteLLM + 3 workers, env desde SSM, apuntando a Temporal Cloud. Caddy/Cloudflare para TLS. |
| 4 | **Terraform** (`infra/terraform/`) | Cognito user pools (×2), SSM parameters (secretos), Cloudflare Pages projects (×2), opcional la VPS (Hetzner/Lightsail provider). |
| 5 | **Backup del session store** | Cron de backup del directorio `hubara_vault/` (a S3 o snapshot de la VPS). |
| 6 | **Deploy SigNoz prod (VPS de obs)** | Provisionar la VPS de observabilidad (Hetzner ~8GB); levantar `deploy/signoz/`; workers de ambos tenants exportan OTLP por red privada/WireGuard; UI `:8080` tras Caddy-auth o Tailscale; ClickHouse/Zookeeper nunca públicos. §3.10 |
| 7 | **Retention + sampling + PII scrubbing** | TTL de ClickHouse 7–15 días; head-sampling (→ tail-on-error al escalar); Collector hashea números/nombres (decisión D3 de HU-003) antes de ClickHouse. Controla el disco = el driver de costo. |
| 8 | **Alertas en SigNoz** | Alerta de latencia LLM, tasa de `tool_errors`, y caída de worker/host vía `hostmetrics` — reemplaza la "observabilidad mínima" manual (healthcheck `GET /` + caída de VPS). |
| 9 | **Terraform — VPS de obs** | Sumar la caja de observabilidad + su volumen ClickHouse + la red privada/WireGuard al Terraform (`infra/terraform/`). |

---

## 8. Camino de escalado (cuándo dejar de ser "una VPS")

El compute rara vez es el cuello — lo es el rate limit del LLM y, en último caso, las actions de Temporal. Orden de escalado:

1. **Vertical:** resize de la VPS (Hetzner/Lightsail en minutos). 4→8→16GB cubre muchísimo.
2. **Tunear concurrencia:** subir `max_concurrent_activities` antes de comprar hardware.
3. **Horizontal en workers:** N contenedores worker en 2-3 cajas baratas; Temporal balancea la cola solo. Acá también se mata el single-point-of-failure (2 cajas + Cloudflare delante de FastAPI).
4. **Recién entonces:** evaluar ECS Fargate con autoscaling (o los manifiestos EKS en `hubara_agency/k8s/aws-produccion/`) y/o Temporal Serverless Workers en Lambda — cuando el volumen y los patrones de tráfico lo justifiquen.

---

## 9. Decisiones abiertas

- **✅ RESUELTO — Frontend:** S3 + CloudFront (consolidación AWS, $0 free tier). Cloudflare Pages descartado para dashboard interno.
- **✅ RESUELTO — TLS endpoint público:** Caddy auto-TLS en la VPS (sin dependencia de Cloudflare).
- **✅ RESUELTO — Backend de observabilidad:** **SigNoz Community self-hosted** (SigNoz Cloud descartado, piso $49/mo). 3 capas ortogonales: OpenTelemetry (transporte) + SigNoz (backend) + OpenLIT (GenAI) + RAGAS (scoring de calidad). Código backend-agnóstico (OTLP genérico) → cero lock-in. §3.10.
- **✅ RESUELTO — Dónde alojar SigNoz:** **VPS de obs dedicada y compartida entre tenants, off-critical-path** (~$8–16/mo, licencia $0). Descartadas: co-locar en la VPS de la app (sube de tier + acopla al camino crítico de ingresos) · on-demand local a $0 (perdés always-on + alertas). Sizing exacto y retention: **MEDIR** (§3.10).
- **¿Una cuenta Temporal o una por compañía?** Default: **una cuenta, 2 namespaces** (ahorra el 2do $100). Cambiar solo si se necesita aislamiento de billing/legal por cliente.
- **¿Cognito por SPA o Cloudflare Access?** Default: **Cognito client-side en la SPA**.
- **Provider de VPS:** Hetzner (~$5-8, más barato) vs Lightsail (todo-AWS, máxima consolidación). Pendiente confirmar región/latencia con Meta y el endpoint de Temporal Cloud elegido. Nota: si la consolidación AWS es prioridad (como en la decisión del frontend), **Lightsail** es coherente.

---

*Documento vivo. Cuando una HU contradiga esto, gana el código vivo + `hubara-architecture-guide`. Actualizar acá las decisiones de infra que cambien.*
