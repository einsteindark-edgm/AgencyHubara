# Plan: Duplicación total Hubara ↔ Vincenzo (silo por cliente, misma cuenta AWS)

> **Fecha**: 2026-07-09 · **Estado**: PROPUESTO
> **Decisión estratégica**: se abandona el enfoque "1 repo / 1 state / N tenants"
> (ADR-2026-06-05, `MULTI_TENANT_COMMERCE_ARCHITECTURE.md`) a favor de **silo total
> por cliente**: nuevo repo GitHub, nueva infra Terraform, nuevo Medusa. Lo ÚNICO
> compartido es la cuenta AWS (525237381234, us-east-1).
> Este doc reemplaza el scaffolding vincenzo parcial que ya existe en el repo.

---

## 0. Principios

1. **No tocar nada vivo de Hubara.** Lección del incidente 2026-07-08 (apply reemplazó
   las cajas y destruyó el vault). Todo cambio del lado Hubara es *subtractivo y de
   texto* (quitar vincenzo de tfvars/matrices); ningún recurso de Hubara se renombra,
   reemplaza ni re-crea.
2. **En el clon Vincenzo se cambia SOLO lo que colisiona a nivel cuenta + la identidad
   de marca del agente.** Todo lo demás (código, arquitectura DEHA/FSD, pipeline,
   compose interno de la caja) queda idéntico, para que portar fixes entre repos sea
   un cherry-pick y no un merge conceptual.
3. **Momento cero es el único momento barato para renombrar estado.** Los nombres que
   viven *dentro* de la caja de Vincenzo (volumen del vault, compose project, /opt) se
   renombran el día 0, cuando aún no hay datos. Después del primer cliente real, no.

---

## 1. Estado actual (inventario verificado 2026-07-09)

### 1.1 Lo que ya existe de "vincenzo" en la infra de Hubara (a desmontar)

El enfoque anterior dejó scaffolding a medias. **Verificado en vivo: todos los SSM de
vincenzo son `PLACEHOLDER_set_out_of_band` → destruirlos no pierde nada.**

| Recurso vivo | Identificador | Dónde se declara |
|---|---|---|
| S3 frontend | `agencyhubara-vincenzo-frontend` | `infra/terraform/platform/tenants.auto.tfvars` (bloque vincenzo) |
| CloudFront | `E1RYQQ6BRH0UGJ` → `d2n1dbc9k2oro1.cloudfront.net` | ídem |
| Cognito pool | `us-east-1_0Jkh63kBE` | ídem |
| SSM ×17 + scheduler ×9 | `/hubara/vincenzo/*` | ídem (placeholders puros) |
| Matriz CI frontend | `tenant: [hubara, vincenzo]` | `.github/workflows/frontend-deploy.yml:26` |
| Namespaces Temporal | `["hubara", "vincenzo"]` | `infra/terraform/temporal-cloud/main.tf:44` (root opcional; verificar si se aplicó) |
| Comentario pendiente | "sumá vincenzo cuando tenga su caja" | `.github/workflows/backend-deploy.yml:66` |

`infra/terraform/compute/tenants.auto.tfvars` **nunca** tuvo bloque vincenzo (no hay
caja EC2 de vincenzo). No existe nada de vincenzo con datos reales.

### 1.2 Topología viva de Hubara (lo que se va a duplicar)

- **Caja app** `agencyhubara-hubara-app` (`i-07ebe6296b9e50865`, t3.medium, EIP
  98.88.237.207 → `https://98-88-237-207.sslip.io`): Caddy + FastAPI + LiteLLM +
  **8 workers** Temporal (catalog-sync, sales, remarketing, sales_eval, eta,
  orders-reconcile + los nuevos **reengagement-cycle** y **order-sentinel-cycle**,
  post-merge 2026-07-10). `enabled_plugins` incluye `order_sentinel,reengagement`;
  post-#184 existe el plugin **marketing** (campañas WhatsApp) — su worker de
  campañas AÚN no está en el compose de prod (pendiente wiring en el motor);
  los clones nacen con `marketing` habilitado y heredan el worker por
  cherry-pick cuando el motor lo sume.
  Vault (sesiones WA + snapshot catálogo + evals + índice de reengagement +
  **historial LLM** `agent_state/` — PR #183: la memoria conversacional ya NO
  vive en la imagen, sobrevive deploys) en volumen
  docker `hubara-prod_hubara-vault` sobre el root EBS, con DLM diario (7 días).
- **Medusa**: EXTERNO al repo — corre en **Railway**
  (`https://hubarabackend-production.up.railway.app`, leído de SSM). Solo se consume
  vía `MEDUSA_BASE_URL`/`MEDUSA_ADMIN_TOKEN`.
- **Temporal Cloud**: 1 cuenta, namespace `hubara.ri1ti`, auth por API key
  (key a nivel cuenta, no por namespace).
- **Frontend**: S3 + CloudFront + Cognito (`us-east-1_tj9egBufy`), build por tenant
  con `VITE_*` desde el output `build_config` de Terraform.
- **Compartidos entre tenants por diseño actual**: caja SigNoz (observabilidad),
  caja GraphAgents (pay-per-use, tag `Role=graphagents`), SSM `/graphagents/*`,
  OIDC provider de GitHub, roles `agencyhubara-gha-{terraform,deploy}`, state bucket
  `agencyhubara-tfstate-525237381234` + lock `agencyhubara-tflock`.
- **Meta/WhatsApp de Hubara**: BUSINESS_ID `1873134773439557` (Lily Pulido), APP
  `36625019197144622`, WABA `3018148735027036` (nuevo: `1763803271643573` post-migración),
  catálogo `868785339159351`, CAPI dataset `1704018554189395`, flow shipping
  `1369134491793466`. Todo WABA-scoped → **nada de esto se comparte con Vincenzo**.

### 1.3 Colisiones a nivel cuenta (lo que OBLIGA a renombrar en el clon)

Dos states independientes en la misma cuenta chocan en todo nombre global fijo:

| Categoría | Recurso que colisiona | Resolución en Vincenzo |
|---|---|---|
| IAM/OIDC | OIDC provider `token.actions.githubusercontent.com` (**1 por cuenta**) | `data` source, NO crear (gotcha `EntityAlreadyExists`) |
| IAM | Roles `agencyhubara-gha-terraform` / `-gha-deploy` | Roles nuevos `vincenzo-gha-*` con trust al repo nuevo |
| S3/DynamoDB | `agencyhubara-tfstate-*` / `agencyhubara-tflock` | Bucket/tabla propios `vincenzo-tfstate-525237381234` / `vincenzo-tflock` |
| SSM | Prefijo `/hubara/<tenant>/` | Prefijo `/vincenzo/<tenant>/` |
| EC2 | Key pair `agencyhubara-ops`, SGs, roles, profiles `agencyhubara-*` | Prefijo `vincenzo-*` |
| EC2 tags | `Role=graphagents` (los workflows hacen `head -1` sobre ese filtro) | forge renombra a `Role=graphagents-<slug>` en tf+workflows+ctl+backend (GraphAgents viaja en todo clon, D-4 revisada) |
| DLM | Política snapshotea **todo volumen de la cuenta** con tag `Backup=daily` | Vincenzo usa tag `Backup=daily-vincenzo` + política DLM propia (evita doble-snapshot cruzado) |
| Cognito domain | `agencyhubara-<tenant>` (global en la región) | `vincenzo-<tenant>` |
| GHCR | `ghcr.io/einsteindark-edgm/agencyhubara` | `ghcr.io/einsteindark-edgm/agencyvincenzo` (sale solo del nombre del repo nuevo) |

---

## 2. Arquitectura destino

```
Cuenta AWS 525237381234 (us-east-1)
│
├── PROYECTO HUBARA (repo einsteindark-edgm/AgencyHubara)
│   ├── tfstate: agencyhubara-tfstate-…  (keys platform/ y compute/)
│   ├── IAM: agencyhubara-gha-terraform / -gha-deploy  (trust → AgencyHubara)
│   ├── SSM: /hubara/hubara/*            ├── EIP 98.88.237.207
│   ├── EC2 app hubara + SigNoz + GraphAgents (se quedan acá)
│   ├── S3/CF/Cognito hubara             └── Medusa: Railway proyecto Hubara
│   └── Temporal Cloud ns hubara.ri1ti
│
├── PROYECTO VINCENZO (repo nuevo einsteindark-edgm/AgencyVincenzo)
│   ├── tfstate: vincenzo-tfstate-…      (keys platform/ y compute/)
│   ├── IAM: vincenzo-gha-terraform / -gha-deploy  (trust → AgencyVincenzo)
│   ├── SSM: /vincenzo/vincenzo/*        ├── EIP nueva → <ip>.sslip.io
│   ├── EC2 app vincenzo (t3.large)      └── Medusa: Railway proyecto NUEVO
│   ├── S3/CF/Cognito vincenzo
│   └── Temporal Cloud ns vincenzo.ri1ti (misma cuenta Temporal, ver D-2)
│
└── COMPARTIDO INEVITABLE: la cuenta misma (billing, límites, OIDC provider GitHub)
```

### Decisiones de frontera (con recomendación; marcadas D-#)

- **D-1 Historia git del repo nuevo**: **fresh start (1 commit inicial), SIN historia
  de Hubara** — la historia contiene IDs reales del cliente Hubara (WABA, catálogo,
  seeds del vault). Se agrega `git remote add hubara …` para cherry-picks de fixes de
  motor. *Alternativa descartada*: mirror con historia (filtra datos de un cliente al
  repo del otro).
- **D-2 Temporal Cloud**: **compartir la cuenta Temporal** (evita otro piso de
  $100/mes), con **namespace `vincenzo` propio + service account propio + API key
  propia scoped a ese namespace**. Aislamiento operacional real; el único
  acoplamiento es el billing de Temporal. *Alternativa*: cuenta Temporal nueva si el
  cliente exige separación contractual total.
- **D-3 Observabilidad (SigNoz)**: la caja actual queda siendo de Hubara. Vincenzo
  arranca **sin OTel** (desde PR #127 el exporter de prod es opt-in explícito) y se
  decide después: caja SigNoz propia (~$65/mes) o apuntar a la de Hubara (acoplamiento
  consciente). Recomendado: sin OTel en F1, caja propia solo si se necesita.
- **D-4 GraphAgents** *(revisada 2026-07-10)*: **VIAJA en todos los clones** — es
  esencial por cliente. forge renombra automáticamente los tres identificadores
  que colisionan a nivel cuenta: tag EC2 `Role=graphagents-<slug>` (terraform +
  workflows + `graphagents_ctl` + default del backend), SSM
  `/<slug>-graphagents/*`, e imagen GHCR `<slug>-graphagents`. Los nombres de
  módulo tf y el path `/opt/graphagents` de la caja quedan intactos
  (preserve_tokens). Secretos del cliente: `aws_bootstrap.py secrets --tenant
  <slug>-graphagents --prefix ""` con `META_AD_ACCOUNT_ID` propio. SigNoz sí
  queda solo en hubara (D-3).
- **D-5 API keys de LLM (DeepSeek/Gemini)**: **crear keys nuevas para Vincenzo**
  (billing separable por cliente, revocación independiente). Es solo cargar valores
  distintos en `/vincenzo/vincenzo/{DEEPSEEK,GEMINI}_API_KEY`; cero código.
- **D-6 Business Manager de Meta**: recomendado **BM propio del cliente Vincenzo**
  (verificación de negocio a su nombre, ownership de su WABA/catálogo/dataset). Si
  se usa el BM de la agencia, documentar el plan de traspaso.
- **D-7 Nombres internos del código** (`hubara_agency/`, imports `src.*`, workflow
  `HubaraSalesSessionWorkflow`, task queues): **se mantienen** en el clon. Son
  identificadores del *motor*, no del cliente; no colisionan (viven en cajas y
  namespaces separados) y renombrarlos rompería el cherry-pick entre repos. El nombre
  del motor es "hubara engine"; el cliente es config.
- **D-8 GHCR_PULL_TOKEN**: PAT nuevo fine-grained con acceso SOLO al package
  `agencyvincenzo` (no reusar el de Hubara).

---

## 3. Tabla de renombre (repo Vincenzo)

Mecánica: introducir `variable "project" { default = "vincenzo" }` en ambos roots y
reemplazar los literales. Hoy `agencyhubara`/`hubara` está hardcodeado en ~20 puntos.

| Qué | Hubara (hoy) | Vincenzo (clon) | Dónde tocar |
|---|---|---|---|
| Prefijo recursos AWS | `agencyhubara-` | `vincenzo-` | `platform/modules/*` y `compute/modules/*` (todos los `name`/`Name`) |
| Prefijo SSM | `/hubara/<t>/` | `/vincenzo/<t>/` | `platform/modules/{secrets,scheduler-config}/main.tf`, `github-oidc/main.tf:146`, `compute/modules/app-instance/main.tf:91-107`, `infra/compose/render-env-from-ssm.sh:38` |
| State backend | `agencyhubara-tfstate…` / `-tflock` | `vincenzo-tfstate-525237381234` / `vincenzo-tflock` | `*/backend.tf`, `envs/*.tfbackend`, `infra/scripts/aws_bootstrap.py` |
| Repo GitHub | `einsteindark-edgm/AgencyHubara` | `einsteindark-edgm/AgencyVincenzo` | `platform/variables.tf:16`, `aws_bootstrap.py:28` |
| Imagen backend | `ghcr.io/…/agencyhubara` | `ghcr.io/…/agencyvincenzo` | automático (`github.repository_owner` + nombre); default en `compute/variables.tf:33` |
| Matrices CI | `[hubara]` / `[hubara, vincenzo]` | `[vincenzo]` | `backend-deploy.yml:66`, `frontend-deploy.yml:26` |
| Paths en la caja | `/opt/hubara`, `/tmp/hubara-deploy` | `/opt/vincenzo`, `/tmp/vincenzo-deploy` | cloud-init `.tftpl`, `backend-deploy.yml:105-142`, render script |
| Compose project + vault | `hubara-prod` / `hubara-vault` | `vincenzo-prod` / `vincenzo-vault` | `infra/compose/docker-compose.prod.yml:13,59-99` (día 0, sin datos aún; el DLM restore doc referencia `vincenzo-prod_vincenzo-vault`) |
| Tag backup DLM | `Backup=daily` | `Backup=daily-vincenzo` | `compute/modules/app-instance` (volume_tags) + `compute/backup.tf` |
| Key pair | `agencyhubara-ops` | `vincenzo-ops` (keygen nuevo) | `compute/main.tf:22`, `tenants.auto.tfvars:1`, GH secret `EC2_SSH_KEY` |
| Verify hardcodeado | `/hubara/hubara/…`, `agencyhubara-hubara` | `/vincenzo/vincenzo/…` | `aws_bootstrap.py:198-200`, `infra/robotocore/test-local.sh:60-72` |
| Tenant id runtime | `HUBARA_TENANT_ID=hubara` (default), `META_ADS_TENANT=hubara` | setear `=vincenzo` vía SSM/env (defaults del código se quedan) | `.env` renderizado; `chats/agent/sales/composition.py:92,121`, `ads/meta/settings.py:44` |
| Reference id órdenes | `HUB-hubara-…` | `VIN-vincenzo-…` | `chats/agent/sales/tools/ui_intents.py:705` |

**Identidad de marca del agente (reescritura, no renombre)** — todo en
`hubara_agency/src/plugins/chats/agent/{sales,remarketing}/`:

- `workspace/IDENTITY.md`, `SOUL.md`, `TOOLS.md`, `memory/MEMORY.md` — marca, tono,
  producto ("velas artesanales de cera de palma" → lo de Vincenzo), apertura que
  nombra la marca.
- `workspace/skills/etapa_*/SKILL.md`, `sales_script/SKILL.md` — guiones por etapa.
- Carpeta `workspace/skills/hubara_catalog/` → `vincenzo_catalog` (en ambos agentes).
- `tools/ui_intents.py:916-950` — allowlist de dominios (`hubara.com.co`, Instagram),
  `:776,794` emojis de marca, `:848` tarjeta de contacto del asesor humano.
- `prompts.py:12`, `translate.py:8`.
- Guard anti-voseo `test_no_voseo_in_agent_strings.py`: sigue aplicando (Colombia);
  si Vincenzo es otro país/registro, ajustar la regla #1 y el guard juntos.
- `infra/whatsapp-provisioning/definitions/flows.json` — name "Hubara — Datos de
  envío v1" y el flow JSON en `hubara_agency/docs/whatsapp_flows/shipping_v1.json`
  (revisar campos de envío según logística de Vincenzo).
- `hubara_agency/scripts/inject_snapshot_products.py` — lista `PRODUCTS_TO_INJECT`
  con IDs Meta de Hubara → vaciar/reemplazar.
- Datos bancarios deterministas (PR #132): cargar los de Vincenzo (variables
  `PAYMENT_TRANSFER_*` van a SSM del tenant nuevo; en Hubara aún están pendientes de
  push a SSM — mismo mecanismo).

**Scrub de datos de Hubara en el clon** (privacidad entre clientes):
`infra/whatsapp-provisioning/tenants/hubara.env.example` (contiene BUSINESS/WABA/APP/
CATALOG reales) → reemplazar por `vincenzo.env.example` limpio; seeds committeados en
`hubara_agency/hubara_vault/`; docs históricos con datos de Hubara (podar
`SERVICIOS_AWS_Y_SCHEDULERS.md`, `MULTI_TENANT_COMMERCE_ARCHITECTURE.md`, ADRs viejos,
`*_PLAN.md` que no aplican).

---

## 4. Cambios en el repo HUBARA (este repo) — "que quede solo Hubara"

PR único, se aplica en **F9** (al final, cuando Vincenzo ya vive en su repo):

1. `infra/terraform/platform/tenants.auto.tfvars` — eliminar bloque `vincenzo` →
   el apply **destruye** S3/CloudFront/Cognito/SSM de vincenzo (seguro: placeholders
   verificados; CloudFront tarda ~15 min en deshabilitar+borrar).
2. `.github/workflows/frontend-deploy.yml:26` — matriz `[hubara]`.
3. `.github/workflows/backend-deploy.yml:66` — quitar comentario "sumá vincenzo".
4. `infra/terraform/temporal-cloud/main.tf:44` — default `["hubara"]`. **Antes**:
   verificar si el namespace `vincenzo` existe en Temporal Cloud. Si existe y F6 lo va
   a reusar → `terraform state rm` (sacarlo del state SIN destruir) y que el repo de
   Vincenzo lo importe; si no existe, editar y ya.
5. Docs: actualizar `infra/INFRASTRUCTURE.md`, `SERVICIOS_AWS_Y_SCHEDULERS.md`,
   `DEPLOY_RUNBOOK.md` (quitar vincenzo de tablas/costos); marcar
   `MULTI_TENANT_COMMERCE_ARCHITECTURE.md` y `ADR-2026-06-05-multi-tenant-commerce-architecture.md`
   como **SUPERSEDED** apuntando a este plan.
6. Nuevo `ADR-2026-07-09-silo-por-cliente.md`: registra la decisión (silo total,
   qué se comparte a nivel cuenta, reglas de naming `vincenzo-*` vs `agencyhubara-*`,
   protocolo de cherry-pick de fixes entre repos).
7. La maquinaria multi-tenant (`for_each = var.tenants`) **se conserva** con un solo
   tenant — no cuesta nada y permite un tenant de staging propio de Hubara a futuro.

Nada de esto toca la caja viva, la EIP, el vault ni los SSM de `/hubara/hubara/*`.

---

## 5. `forge` — CLI de clonación por cliente (el nuevo F1)

**Objetivo**: `forge apply vincenzo --dest ~/Documents/Projects/AgencyVincenzo` produce
la carpeta del repo nuevo, renombrada, limpia de datos de Hubara, con git inicializado
y gates verdes — repetible para cualquier cliente futuro con solo un YAML nuevo.

**Dónde vive**: `forge/` en ESTE repo (el repo madre es el template; los clones
no llevan forge). Python 3 stdlib + PyYAML, se corre `python3 forge/forge.py …`
(sin `uv run`, mismo patrón que GraphAgents para no pelear con el hook pre-bash).
Estilo de UX calcado de `infra/whatsapp-provisioning/`: declarativo, idempotente,
`plan` antes de `apply`.

### Comandos

```bash
python3 forge/forge.py init vincenzo        # genera clients/vincenzo.yaml (cuestionario)
python3 forge/forge.py plan vincenzo        # dry-run: TODO lo que va a copiar/renombrar/editar/borrar + preview del scanner
python3 forge/forge.py apply vincenzo --dest ~/Documents/Projects/AgencyVincenzo
python3 forge/forge.py verify ~/Documents/Projects/AgencyVincenzo   # scanner de residuales (re-ejecutable)
python3 forge/forge.py publish ~/Documents/Projects/AgencyVincenzo  # opcional: gh repo create privado + push (pide confirmación)
```

### Input: bundle por cliente `forge/clients/<slug>/` (identidad, NUNCA secretos)

```
forge/clients/vincenzo/
├── client.yaml                    # identidad estructurada (abajo)
└── workspace/                     # ★ los .md de la PERSONALIDAD del agente,
    │                              #   espejo 1:1 del workspace destino — forge los
    │                              #   copia ENCIMA reemplazando los de Hubara
    ├── sales/
    │   ├── IDENTITY.md            # "Eres el Asesor Exclusivo de Ventas de Vincenzo…"
    │   ├── SOUL.md                # marca, tono, apertura que nombra la marca
    │   ├── TOOLS.md
    │   ├── memory/MEMORY.md
    │   └── skills/
    │       ├── etapa_descubrimiento/SKILL.md   # "Bienvenido a Vincenzo, <producto>…"
    │       ├── etapa_*/SKILL.md
    │       ├── sales_script/SKILL.md
    │       └── catalog/SKILL.md   # forge lo instala como skills/vincenzo_catalog/
    └── remarketing/               # gemelo (misma estructura)
```

```yaml
# client.yaml
slug: vincenzo                    # → prefijos, SSM, tenant id
company: Vincenzo
repo: einsteindark-edgm/AgencyVincenzo
aws:
  region: us-east-1
  resource_prefix: vincenzo       # → vincenzo-gha-terraform, vincenzo-tfstate-…
  ssm_prefix: /vincenzo
business:
  country: CO                     # → moneda, idioma templates, regla anti-voseo
  currency: COP
  product_description: "TODO"     # → contexto para redactar el workspace
  domains: []                     # → allowlist ui_intents (esto es código, va por manifest)
  instagram: ""
```

**Contrato del overlay de workspace**:
- `forge init <slug>` genera el bundle pre-poblado: copia el workspace de Hubara como
  punto de partida con la marca sustituida mecánicamente y marcas `TODO-BRAND` en cada
  pasaje que describe el negocio (producto, materiales, origen, guiones). Redactar la
  personalidad = editar esos .md en `clients/<slug>/workspace/` ANTES del apply — y
  queda versionado en el repo madre como fuente de verdad de la identidad del cliente.
- `forge plan/apply` valida el overlay contra una **lista de archivos requeridos**
  (IDENTITY, SOUL, TOOLS, memory, cada etapa_*, sales_script, catalog — sales y
  remarketing): falta un archivo → FAIL con la lista; sobra un archivo que no existe
  en el workspace destino → FAIL (typo de path).
- El scanner de residuales corre también SOBRE el overlay: "Hubara", IDs de Hubara o
  `TODO-BRAND` sin resolver en el bundle → el apply bloquea (con `--allow-todos` para
  forjar un clon de prueba antes de tener la redacción final).
- Lo que es código y no prosa (allowlist de dominios en `ui_intents.py:916-950`,
  emojis de marca, tarjeta de contacto, prefijo `reference_id`) NO va en el overlay:
  se inyecta vía manifest desde `client.yaml`.

Los secretos y IDs de Meta/Medusa/Temporal jamás pasan por este bundle — van a SSM en
las fases F3-F6, como hoy.

### Motor: manifest declarativo + scanner (por qué NO es un sed global)

"hubara" en este repo es dos cosas: **nombre del motor** (se conserva: `hubara_agency/`,
imports `src.*`, `HubaraSalesSessionWorkflow`, D-7) y **nombre del cliente** (se
reemplaza). Un find-replace ciego rompe el motor. Por eso el corazón es
`forge/manifest.yaml`, versionado, donde **cada ocurrencia está clasificada**:

```yaml
version: 1
copy_exclude: [.git, .claude/worktrees, hubara_agency/hubara_vault/wa_*, node_modules, infra/forge]
replacements:                    # ediciones con scope de archivos explícito (tabla §3)
  - id: tf-resource-prefix
    files: ["infra/terraform/**/*.tf", "infra/terraform/**/*.tfvars"]
    from: "agencyhubara-"
    to: "{{aws.resource_prefix}}-"
  - id: ssm-prefix
    files: ["infra/terraform/**", "infra/compose/render-env-from-ssm.sh", "infra/scripts/aws_bootstrap.py",
            "infra/whatsapp-provisioning/**"]   # ads-token imprime /hubara/<t>/meta/oauth
    from: "/hubara/"
    to: "{{aws.ssm_prefix}}/"
  - id: ci-tenant-matrix
    files: [".github/workflows/*.yml"]
    from: "tenant: [hubara"
    to: "tenant: [{{slug}}"
  # … una entrada por fila de la tabla §3 (paths /opt, compose name, vault, DLM tag,
  #   key pair, reference_id HUB-, repo github, oidc→data, etc.)
renames:
  - {from: ".../workspace/skills/hubara_catalog", to: ".../workspace/skills/{{slug}}_catalog"}
deletes:                         # datos del cliente Hubara — scrub
  - "infra/whatsapp-provisioning/tenants/hubara.env.example"
  - "hubara_agency/hubara_vault/**"
  - "hubara_agency/scripts/inject_snapshot_products.py::PRODUCTS_TO_INJECT"  # vaciar lista
  - "docs/cartagena/**"          # vertical hotelero de OTRO cliente — no viaja a terceros
  - docs históricos (MULTI_TENANT_*, ADRs de hubara, *_PLAN.md no aplicables)
workspace_overlay:               # los .md del bundle clients/<slug>/workspace/ se copian
  - {from: "clients/{{slug}}/workspace/sales/", to: ".../chats/agent/sales/workspace/"}
  - {from: "clients/{{slug}}/workspace/remarketing/", to: ".../chats/agent/remarketing/workspace/"}
  required: [IDENTITY.md, SOUL.md, TOOLS.md, memory/MEMORY.md, skills/etapa_*/SKILL.md,
             skills/sales_script/SKILL.md, skills/catalog/SKILL.md]   # falta uno → FAIL
templates:                       # archivos regenerados con {{vars}} + marcas TODO
  - "infra/whatsapp-provisioning/tenants/{{slug}}.env.example"
  - "infra/whatsapp-provisioning/definitions/flows.json"   # name "{{company}} — Datos de envío v1"
engine_allowlist:                # "hubara" legítimo del MOTOR — el scanner lo ignora
  - "hubara_agency/"             # nombre de carpeta/paquete
  - "HubaraSalesSessionWorkflow"
  - "HUBARA_TENANT_ID"           # nombre de la env var (el VALOR sí cambia vía SSM)
forbidden_residuals:             # el scanner FALLA si aparecen en el clon
  - "3018148735027036"           # WABA hubara (y el resto de IDs de §1.2)
  - "868785339159351"            # catálogo Meta
  - "98.88.237.207"              # EIP
  - "hubara.com.co"
  - "1873134773439557"           # business id
  - pattern: "wa_57\\d{10}"      # teléfonos reales en fixtures/seeds
```

**El scanner es el ratchet** (regla de oro "ningún campo sin su check"): tras
transformar, recorre el clon y clasifica CADA ocurrencia restante de
`hubara|agencyhubara` como (a) allowlist del motor → OK, (b) cualquier otra → **FAIL
con la lista**. Cuando el repo madre gane un literal nuevo del cliente, el próximo
`forge plan` lo detecta y obliga a clasificarlo en el manifest. El manifest no se
desactualiza en silencio.

### Stages de `apply` (checkpoint en `.forge-state.json`, resumible)

| Stage | Qué hace |
|---|---|
| A `export` | `git archive HEAD` del repo madre → carpeta destino (sin historia — D-1) |
| B `transform` | replacements + renames + deletes + templates del manifest |
| C `scrub-verify` | scanner de residuales (bloquea si falla) |
| D `git-init` | `git init` + commit inicial `chore: génesis {{company}} desde hubara engine <sha-madre>` (el SHA madre queda registrado → base del SYNC_LOG de cherry-picks) + `git remote add hubara …` |
| E `next-steps` | escribe `NEXT_STEPS.md` en el clon: F2-F8 de este plan parametrizados con el slug + `forge-report.json` |
| F `gates` (opcional `--with-gates`) | corre pytest + arch + tsc + build en el clon y reporta |

`publish` (separado, confirmación explícita): `gh repo create --private` + push +
imprime los comandos `aws_bootstrap.py github` para las vars/secrets del repo nuevo.

### TDD de la propia tool

Golden test en `forge/tests/`: forjar un cliente fake `acme` a un tmpdir y
asertar (1) scanner limpio, (2) cero `forbidden_residuals`, (3) los YAML de workflows
y compose parsean, (4) `terraform fmt -check` pasa en el clon, (5) los templates de
workspace contienen las marcas `TODO-BRAND` esperadas. Ese golden corre en CI del repo
madre → cualquier PR que agregue un literal de cliente sin clasificar rompe el build,
no al próximo cliente.

### Evolución v2 (ratchet hacia "cliente = data, no código")

Cada entrada del manifest es deuda: un literal de cliente incrustado en el motor. La
métrica a bajar es **`entries en manifest`**. Con cada cliente, mover literales a
config (allowlist de dominios de `ui_intents.py` → config del tenant, prefijo de
`reference_id` → env, workspace ya es archivos) y cherry-pickear esa mejora al repo
madre. Cuando el manifest tienda a solo-templates, el motor está listo para el
template real de `PLATFORM_BLUEPRINT_fable.md`.

**Esfuerzo estimado**: v1 de forge = 1-2 días (la tabla §3 ya ES el manifest; es
mecanizarla + scanner + golden test).

---

## 6. Fases de creación de Vincenzo

### F0 — Insumos (bloqueante humano; arrancar YA porque Meta tarda días/semanas)

- [ ] **Identidad de negocio**: qué vende Vincenzo, marca/tono, país/moneda (¿COP?),
      dominios web e Instagram (para el allowlist), datos bancarios, política de
      envíos/pagos. Alimenta toda la sección de reescritura de §3.
- [ ] **Meta**: Business Manager (D-6), Business Verification, línea telefónica nueva
      (no puede estar registrada en otra WABA), display name a aprobar, App Meta nueva
      (App Secret). Estos son los pasos human-in-the-loop irreducibles del toolkit
      (`infra/whatsapp-provisioning/README`).
- [ ] **Cuentas**: repo GitHub `AgencyVincenzo` (privado), proyecto Railway nuevo,
      keys DeepSeek/Gemini nuevas (D-5), decisión D-2 confirmada (service account
      Temporal).

### F1 — Repo nuevo (código) — **vía `forge` (§5)**

**F1a — Construir forge** (en ESTE repo, PR propio): manifest desde la tabla §3 +
scanner + templates de workspace + golden test `acme`. 1-2 días.

**F1b — Correrlo para Vincenzo**:

1. `forge init vincenzo` → genera el bundle `clients/vincenzo/` (client.yaml +
   overlay de workspace pre-poblado con marcas `TODO-BRAND`).
2. **Redactar la personalidad** en `clients/vincenzo/workspace/{sales,remarketing}/`
   con los insumos de F0 (producto, tono, guiones por etapa). Es el paso creativo del
   proceso — todo lo demás es mecánico. Queda versionado en el repo madre.
3. `forge plan vincenzo` → revisar el dry-run completo (incluye validación del
   overlay: archivos requeridos + cero "Hubara"/`TODO-BRAND` residual).
4. `forge apply vincenzo --dest ~/Documents/Projects/AgencyVincenzo --with-gates` →
   carpeta nueva, renombrada, scrubbed, con la personalidad de Vincenzo instalada,
   git inicializado, gates verdes. (Los tests de arquitectura necesitan dummies
   `MEDUSA_BASE_URL`/`MEDUSA_ADMIN_TOKEN` — gotcha conocido; forge los inyecta.)
5. `forge verify` final + `forge publish` → repo GitHub privado creado y pusheado.

### F2 — Bootstrap AWS (una sola vez, local con creds admin)

1. `python infra/scripts/aws_bootstrap.py state` (ya renombrado) → bucket
   `vincenzo-tfstate-525237381234` + tabla `vincenzo-tflock`.
2. `ssh-keygen` → key pair `vincenzo-ops`; pública a `compute/tenants.auto.tfvars`,
   privada a GH secret `EC2_SSH_KEY` del repo nuevo.
3. **Modificar `platform/modules/github-oidc`**: el `aws_iam_openid_connect_provider`
   pasa a `data` (ya existe en la cuenta — crearlo da `EntityAlreadyExists`). Los
   roles `vincenzo-gha-terraform`/`-gha-deploy` sí se crean, con trust al repo nuevo.
   *Mejora de aislamiento recomendada*: scope de las policies del rol terraform de
   Vincenzo a `arn:…:*/vincenzo-*` y `parameter/vincenzo/*` (el rol de Hubara hoy usa
   `*`; que el nuevo no pueda tocar recursos de Hubara por diseño).
4. Primer `terraform apply` de `platform` **local** (huevo-gallina de OIDC).
5. `aws_bootstrap.py github` → vars del repo nuevo (`AWS_REGION`,
   `AWS_TERRAFORM_ROLE_ARN`, `AWS_DEPLOY_ROLE_ARN`, `TF_STATE_BUCKET`,
   `TF_STATE_LOCK_TABLE`) + secret `EC2_SSH_KEY`. Environment `production` con
   required reviewers.

### F3 — Platform (plano de control)

- Apply crea: S3 `vincenzo-vincenzo-frontend`, CloudFront, Cognito pool/client/domain,
  SSM placeholders `/vincenzo/vincenzo/*` (17 secretos + 9 scheduler knobs).
- Usuario operador en Cognito (`admin-create-user`).
- Cargar los secretos que ya existen: `DEEPSEEK_API_KEY`, `GEMINI_API_KEY` (nuevas,
  D-5), `GHCR_PULL_TOKEN` (D-8), `WHATSAPP_VERIFY_TOKEN` (generar uno real, no
  `my_verify_token`).

### F4 — Medusa nuevo (Railway)

Réplica del patrón de Hubara (Railway + Postgres). **No hay provisioning automatizado
— es runbook manual** (`MULTI_TENANT_COMMERCE_ARCHITECTURE.md:162-164` lo deja
explícito):

1. Proyecto Railway nuevo: servicio Medusa v2 + Postgres propio.
2. `medusa db:migrate` + crear admin + publishable/admin token.
3. Seed runtime vía Admin API (no hay archivo declarativo aún): región (COP/CO si
   aplica), sales channel, shipping options, currency. Anotar `MEDUSA_REGION_ID`,
   `MEDUSA_SALES_CHANNEL_ID`, `MEDUSA_DEFAULT_SHIPPING_OPTION_ID`.
4. Cargar catálogo de productos de Vincenzo (con imágenes cuyos filenames siguen la
   convención de designs si usan esa feature — PR #132).
5. SSM: `MEDUSA_BASE_URL`, `MEDUSA_ADMIN_TOKEN` (+ IDs anteriores como env extra).
6. *Mejora opcional (recomendada, 1 día)*: escribir el `medusa-seed.py` idempotente
   que el ADR viejo prometía — se escribe una vez en el repo Vincenzo y se
   cherry-pickea a Hubara; paga en el tercer cliente.

### F5 — Meta / WhatsApp (puede correr en paralelo con F2-F4; long pole de Meta)

Con el toolkit existente (`infra/whatsapp-provisioning/whatsapp_provision.py`,
idempotente, tenant-agnóstico):

1. `tenants/vincenzo.env`: BUSINESS_ID/APP_ID/APP_SECRET/WABA/SYSTEM_USER_TOKEN de
   Vincenzo, `CALLBACK_URL=https://<eip-vincenzo>.sslip.io/api/chats/webhook`
   (la EIP sale de F7 — el registro del webhook se hace después del primer deploy),
   `DISPLAY_NAME` de Vincenzo.
2. `discover` → `plan` → `apply`: número, catálogo Meta nuevo, templates (es_CO son
   tenant-agnósticos; revisar idioma/registro), **flow shipping re-publicado en el
   WABA nuevo** (WABA-scoped → `META_FLOW_ID_SHIPPING` nuevo), dataset CAPI nuevo
   vinculado al WABA (`POST /{WABA}/dataset`).
   ⚠️ Gotchas ya pagados: evento CAPI = `LeadSubmitted` (no `Lead`); smoke real, no
   solo unit tests.
3. SSM: `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_APP_SECRET`,
   `WHATSAPP_BUSINESS_ACCOUNT_ID`, `META_CATALOG_ID`, `META_SYSTEM_USER_TOKEN`,
   `META_CAPI_DATASET_ID`, `META_FLOW_ID_SHIPPING`, `META_APP_*`.
4. **Conexión Ads del dashboard** (single-tenant SIN OAuth, decisión 2026-07-09):
   `python3 whatsapp_provision.py ads-token --config tenants/vincenzo.env` verifica
   el system-user token (scopes `ads_read`+`ads_management` + cuenta publicitaria
   asignada en el BM) e imprime el `aws ssm put-parameter` del token store
   `/<prefix>/<tenant>/meta/oauth`. Sin este seed, el chip del dashboard dice
   "Meta no conectado" — no hay botón de login. ⚠️ Para forge: el CLI imprime el
   path con `/hubara/` hardcodeado → `infra/whatsapp-provisioning/**` entra al
   scope del replacement `ssm-prefix` del manifest.

### F6 — Temporal Cloud

1. Namespace `vincenzo` (región aws-us-east-1, retention 30d, api_key_auth). Vía el
   root `temporal-cloud` del repo nuevo, o import si ya existe (ver §4.4).
2. Service account propio + API key scoped (D-2).
3. SSM: `TEMPORAL_ADDRESS` (`us-east-1.aws.api.temporal.io:7233`),
   `TEMPORAL_NAMESPACE` (`vincenzo.<account_id>`), `TEMPORAL_API_KEY`.
4. ⚠️ Verificar el estado de `infra/PENDING_IMPLEMENTATION.md` §1 (naming
   `TEMPORAL_URL` legacy vs `TEMPORAL_ADDRESS`): Hubara prod YA corre contra Temporal
   Cloud, así que replicar **exactamente** las claves/nombres que la caja de Hubara
   tiene hoy en `/hubara/hubara/*` (fuente de verdad: `aws ssm get-parameters-by-path`),
   no lo que digan los docs.

### F7 — Compute + primer deploy

1. `compute/tenants.auto.tfvars` del repo nuevo: bloque `vincenzo` (t3.large según
   sizing doc, AMI **pinneado** — mismo `ami-07ab13a91f7d7a8af` o el AL2023 vigente,
   con `ignore_changes=[ami]`), `domain` provisional vacío.
2. Apply compute → caja + EIP nueva + SG + IAM + DLM propio (tag `daily-vincenzo`).
3. Con la EIP: `domain = "<ip-con-guiones>.sslip.io"` → segundo apply (Caddy saca TLS).
   Actualizar en platform tfvars `api_url` + callbacks Cognito con la URL real, y el
   `CALLBACK_URL` de F5.
4. Push a `main` del repo nuevo → `backend-deploy` (imagen `agencyvincenzo` a GHCR →
   SSH → render `/vincenzo/vincenzo/*` → compose up) y `frontend-deploy` (build con
   `VITE_*` del `build_config` → S3 → invalidation).
5. Registrar webhook en la App Meta (`CALLBACK_URL` + verify token) y suscribir el WABA.
6. **Seed del snapshot de catálogo** post-deploy (gotcha conocido: sin seed, el primer
   "Ver catálogo" escala a humano con `catalog_unavailable`) — correr el sync de
   catálogo o esperar el schedule.
7. **Sembrar los schedules que NO se auto-crean** contra el namespace del tenant
   nuevo: `scripts/create_reengagement_schedule.py` (`reengagement-cycle-schedule`)
   y `scripts/create_order_sentinel_schedule.py` (`order-sentinel-cycle-schedule`).
   Ambos son idempotentes (no-op si existe). Los otros tres
   (`order-reconciliation-schedule`, `sales-eval-schedule`, `golden-eval-schedule`)
   los crean los propios workers al arrancar.

### F8 — Verificación E2E (checklist de cierre)

- [ ] `GET /` por HTTPS responde en `https://<eip>.sslip.io` (TLS de Caddy OK).
- [ ] Webhook Meta verificado (challenge) y mensaje real entra al agente sales.
- [ ] Conversación completa: saludo con marca Vincenzo (no "Hubara" — grep runtime,
      no solo tests: lección voseo/strings hardcodeados), catálogo visible, draft
      order creado en el Medusa NUEVO, datos bancarios de Vincenzo.
- [ ] Dashboard: login Cognito, chats visibles, bandeja humana (tag HUMANO) funciona.
- [ ] CAPI: evento `LeadSubmitted` de prueba aceptado por el dataset nuevo.
- [ ] Temporal UI cloud: workflows corriendo en `vincenzo.<acct>`, los **5 schedules**
      creados en el ns nuevo (`order-reconciliation-schedule`, `sales-eval-schedule`,
      `golden-eval-schedule`, `reengagement-cycle-schedule`,
      `order-sentinel-cycle-schedule`).
- [ ] Los 8 workers del compose arriba y polleando (incluye reengagement-cycle y
      order-sentinel-cycle; este último con `HUBARA_API_BASE_URL=http://api:8000` —
      sin eso el ciclo es `skipped_empty` eterno, gotcha PM-003 ya fijado en el
      compose de prod).
- [ ] Chip "Meta conectado" en la sección Ads del dashboard (seed `ads-token` de F5.4).
- [ ] App móvil (Tauri): build con la URL real del clon — `tauri.conf.json` y el
      runbook Android salen del forge con `TODO-EIP.sslip.io` a reemplazar.
- [ ] DLM: primer snapshot del volumen de Vincenzo presente (cubre TAMBIÉN el
      historial LLM post-#183); **restore test** (crear
      volumen del snapshot y montarlo — no repetir el incidente de Hubara).
- [ ] Aislamiento negativo: el rol `vincenzo-gha-terraform` NO puede leer
      `/hubara/*` ni tocar recursos `agencyhubara-*` (probar un `ssm get` que falle).
- [ ] Smoke de que Hubara sigue intacto (conversación de prueba en la línea Hubara).

### F9 — Limpieza en Hubara (§4) + cierre

- PR de limpieza en este repo, `terraform apply` (platform) para destruir el
  scaffolding vincenzo, docs + ADR nuevo.
- Escribir en ambos repos el **protocolo de fixes compartidos**: fix de motor se hace
  en Hubara (repo madre) → `git cherry-pick` al repo Vincenzo (remote `hubara`) →
  gates verdes en ambos. Mantener un `SYNC_LOG.md` con los SHAs portados.

---

## 7. Costos delta estimados (mensual, us-east-1)

| Ítem | Estimado |
|---|---|
| EC2 t3.large app Vincenzo (always-on) + EBS 30-60GB + EIP | ~$70-80 |
| Railway (Medusa + Postgres) | ~$10-20 |
| S3 + CloudFront + Cognito + SSM + DLM snapshots | < $5 |
| Temporal Cloud (namespace extra, misma cuenta) | $0 extra sobre el piso (hasta superar acciones incluidas) |
| SigNoz propio (si D-3 = duplicar) | +~$65 (diferido) |
| **Total fase inicial** | **~$85-105/mes** |

## 8. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Un apply de Vincenzo toca recursos de Hubara | States separados + naming disjunto + policies del rol terraform de Vincenzo scoped a `vincenzo-*` (F2.3); nunca correr terraform de un proyecto con el rol del otro |
| `EntityAlreadyExists` del OIDC provider | Módulo github-oidc con `data` source (F2.3) |
| DLM cruzado (doble snapshot / dependencia oculta) | Tag `Backup=daily-vincenzo` + política propia |
| Filtro EC2 `Role=graphagents` ambiguo (`head -1`) | Vincenzo no monta GraphAgents en v1 (D-4); si lo monta, tag distinto |
| Réplica de config Temporal contra docs stale | Fuente de verdad = SSM vivo de Hubara, no los docs (F6.4) |
| Meta tarda (verification, display name, línea) | F5 arranca en F0; es el long pole — todo lo AWS se hace en paralelo |
| "Hubara" residual en la experiencia del cliente Vincenzo | Checklist F8 con verificación de COMPORTAMIENTO (mensaje real), no solo grep/tests — lecciones "voseo hardcodeado" y "backend behavior verification" |
| Drift de motor entre repos (costo estructural del silo) | Protocolo cherry-pick + SYNC_LOG (F9); a mediano plazo, si llega el cliente #3, extraer el motor a template/paquete (`PLATFORM_BLUEPRINT_fable.md` ya apunta ahí) |
| Datos de Hubara filtrados al repo Vincenzo | D-1 fresh start + scrub §3; revisar también `hubara_vault/` seeds y fixtures de tests con teléfonos reales |
| Apply de compute reemplaza la caja (incidente 2026-07-08) | AMI pinneado + `ignore_changes` viene heredado del fix #128; DLM desde el día 0 en Vincenzo |

## 9. Orden y esfuerzo

```
F0  (insumos + Meta)  ───────────────────────────┐  humano, días-semanas (Meta)
F1a (construir forge, en repo Hubara) ── 1-2 días│  arranca YA, no depende de F0
F1b (forge apply vincenzo + identidad) ─ ½-1 día │  necesita F0 para la marca
F2  (bootstrap AWS)  ─────────── ½ día           │  F2-F4 paralelizables entre sí
F3  (platform+secretos) ──────── ½ día           │  y con F5
F4  (Medusa Railway) ─────────── ½-1 día         │
F5  (Meta/WhatsApp provisioning) ─ depende de F0 ┘
F6  (Temporal) ───────────────── 2 h
F7  (compute + deploys) ──────── ½ día
F8  (E2E) ────────────────────── ½ día
F9  (limpieza Hubara + ADR) ──── ½ día
```

Camino crítico: **F0/F5 (Meta)**. Todo lo demás suma ~5-7 días de trabajo efectivo,
de los cuales 1-2 son inversión reutilizable (forge) que el cliente #3 ya no paga.

---

## 10. Evaluación: ECS Fargate como plataforma por cliente (track separado)

**Pregunta**: ¿reemplazar la caja EC2 + docker-compose + SSH por ECS Fargate haría el
stamp por cliente más estructural? **Respuesta corta: sí estructuralmente, pero es un
replatform con 4 prerequisitos, y NO debe acoplarse al lanzamiento de Vincenzo.**

### Qué aporta de verdad (mapeado a dolores ya pagados)

| Dolor actual | Con Fargate |
|---|---|
| Apply de compute reemplazó las cajas y **destruyó el vault** (2026-07-08); AMI pinneada + `ignore_changes` como parche | No hay AMI, no hay caja, no hay user_data. El estado vive en EFS, independiente del ciclo de vida del compute. AWS Backup para EFS reemplaza al DLM |
| Deploy = SSH/SCP con `EC2_SSH_KEY` + `render-env-from-ssm.sh` + gotchas de deploy stale | Deploy = nueva task definition + rolling update del service vía OIDC puro (sin llaves SSH, sin `appleboy/*`). Los secretos SSM se inyectan **nativos** en la task def (`secrets:`) — muere el render script |
| Onboarding de cliente = caja + EIP + cloud-init + box.env | Cliente = **instanciación de un módulo Terraform** (cluster/servicios/listener rule). El manifest de forge encoge: desaparecen `/opt/<proyecto>`, box.env, key pair, AMI |
| Baile start/stop de la caja GraphAgents (tag ambiguo, `DescribeInstanceInformation`, autostop cron) | `ecs run-task` on-demand: la tarea nace, corre y muere. Pay-per-use real sin cron ni EIP dinámica |
| Workers y API escalan juntos (una caja) | Sizing por servicio. Bonus: los 8 workers son pollers de Temporal (tolerantes a interrupción por diseño) → **Fargate Spot** (~70% off) es fit perfecto |

### Los 4 acoples que obliga a romper (el costo real del replatform)

1. **Vault → EFS.** Hoy es un volumen docker compartido entre API + 8 workers en un
   host. En Fargate las tareas no comparten disco local → EFS montado en todas.
   (Fargate soporta EBS por-tarea desde 2024, pero NO compartido → no sirve para el
   vault.) Archivos JSON chicos: latencia NFS aceptable, pero hay que probar el
   locking de sesiones concurrentes.
2. **Caddy + sslip.io → ALB + ACM + dominio propio.** ACM no puede validar
   `*.sslip.io` (no controlamos ese DNS) → **prerequisito duro: dominio propio +
   Route53** (el pendiente §3 de `PENDING_IMPLEMENTATION.md` deja de ser opcional).
   Caddy y el hack sslip.io mueren; el webhook de Meta apunta al ALB.
3. **GHCR → ECR** (o `repositoryCredentials` con Secrets Manager para seguir en GHCR;
   ECR es lo natural con OIDC y muere `GHCR_PULL_TOKEN`).
4. **Compose DNS → Service Connect/Cloud Map** (`http://litellm:4000` y
   `HUBARA_API_BASE_URL=http://api:8000` pasan a service discovery de ECS).

### Costo (us-east-1, aprox)

| | Hoy (EC2) | Fargate naive | Fargate bien hecho |
|---|---|---|---|
| Compute | t3.medium ~$30 (11 contenedores) | api+litellm+8 workers on-demand ≈ **$95-100** | API on-demand + workers/litellm en **Spot** ≈ **$40-45** |
| Extras | EIP, EBS 30GB | + ALB ~$18 + EFS ~$1 + Route53 | ídem |
| **Total/cliente** | **~$35** | ~$120 | **~$60-70** |

Comparable al t3.large que igual se contemplaba para Vincenzo. No es el costo el
argumento en contra — es el riesgo de cambiar dos variables a la vez.

### Lo que Fargate NO mejora

Medusa (Railway), Temporal (Cloud), todo el provisioning Meta/WhatsApp (F0/F5), la
identidad del agente, Cognito/CloudFront. O sea: **el camino crítico por cliente
(Meta) y ~60% del trabajo de onboarding son idénticos** con o sin Fargate.

### Decisión propuesta (D-9)

- **Vincenzo sale en el silo EC2 probado** (forge v1). Un lanzamiento de cliente ya
  estrena forge; no le apilamos encima un replatform de runtime (una variable a la
  vez — la misma lógica de no tocar lo vivo de Hubara).
- **forge se diseña con la capa infra swappable**: `infra_flavor: ec2 | fargate` en
  `client.yaml`. El manifest ya separa "identidad/branding" de "infra" — la vía
  Fargate solo reemplaza el segundo bloque. Costo hoy: casi cero (es estructura del
  manifest, no código extra).
- **Piloto Fargate en la pieza perfecta, sin cliente en riesgo**: la caja GraphAgents
  como `run-task` on-demand (elimina el baile start/stop y es aislada del funnel), o
  un tenant staging de Hubara.
- **Gate de adopción**: cuando haya dominio propio + ACM (prerequisito 2) y el piloto
  esté verde → cliente #3 nace `infra_flavor: fargate`, y migrar Hubara/Vincenzo se
  decide después con datos (la migración por cliente es: EFS sync del vault + cutover
  de DNS).

---

## 11. Forge Console — UI de flota sobre los CLIs (decisión D-10)

**Decisión 2026-07-10**: silo + forge se mantiene como eje multi-cliente (se evaluó y
descartó Argo CD/EKS: el patrón GitOps que se buscaba se cubre sin volver a
Kubernetes). Encima se construye una **UI liviana de operación de flota**, con una
regla de oro:

> **La UI es piel; los CLIs son músculo.** Cada botón ejecuta un CLI headless que
> funciona igual sin UI (testeable TDD, invocable por el pipeline); cada pantalla lee
> archivos declarativos (`client.yaml`, workspace `.md`s, terraform plan). Cero
> lógica de negocio en la UI.

**Dónde vive**: vista nueva de **Acktos Studio** (`vscode-hubara`) — ya ejecuta
procesos, streamea output, tiene webviews y self-update, y es **local por diseño**:
los secretos van del formulario directo a SSM con las credenciales AWS del operador,
sin servidor hosteado y sin persistirse en la UI. Alternativa (solo si se necesita
operar fuera de VS Code): web app local FastAPI+React con el mismo contrato de CLIs.

### Músculos: CLIs existentes y faltantes

| Capacidad | CLI | Estado |
|---|---|---|
| Teléfonos / Meta / catálogo / flows / CAPI / ads-token | `infra/whatsapp-provisioning/whatsapp_provision.py` | ✅ |
| Secretos env → SSM · bootstrap state · GH vars | `infra/scripts/aws_bootstrap.py` | ✅ |
| Deploys y runs CI | `gh workflow run` / `gh run list` | ✅ |
| Estado de cajas via SSM | patrón de `infra/scripts/graphagents_ctl.py` | ✅ |
| Seed catálogo + schedules | `trigger_catalog_sync.py`, `create_*_schedule.py` | ✅ |
| Clonar/renombrar/scrub/personalidades | **forge** (§5) | 🔨 F1a |
| **Desplegar Medusa desde URL de repo** (Railway: proyecto + Postgres + deploy + `db:migrate` + seed región/canal/envíos → imprime `MEDUSA_BASE_URL`+token para SSM) | **`medusa_provision.py`** (nuevo, mismo estilo plan/apply) | 🔨 — convierte el runbook manual F4 en CLI |

### Pantallas (5)

1. **Flota** — card por cliente desde `forge/clients/*`: repo, IP/dominio,
   workers arriba/abajo (SSM), último deploy, schedules vivos.
2. **Cliente nuevo (wizard)** — el formulario ES el `client.yaml` (incluye la URL del
   repo Medusa de donde jalar + plan Railway). Botones `forge plan` (dry-run completo
   = "ver qué se va a subir") y `forge apply` con log en vivo.
3. **Personalidades** — editor markdown sobre `clients/<slug>/workspace/`, checklist
   de archivos requeridos + `TODO-BRAND` resaltados; semáforo = `forge verify`.
4. **Meta/WhatsApp** — formulario `tenants/<slug>.env` + un botón por comando de
   `whatsapp_provision.py` con output streameado; los pasos humanos irreducibles
   (business verification, código SMS, display name) como checklist con estado.
5. **Despliegues** — por cliente: disparar workflows, runs recientes, `terraform
   plan` renderizado, diff de lo que sube antes de aprobar.

### Steps de migración (`forge/migrate.py`) — construido 2026-07-10

La migración completa corre como **steps con estado** (`status`/`run`/`done`,
estado por cliente gitignored en el bundle). La distinción de tipos ES la
garantía de aislamiento de hubara:

| Step | Tipo | Qué hace |
|---|---|---|
| S1 clone | auto | `forge apply` → repo del cliente |
| S2 supabase | auto | proyecto Supabase NUEVO (no hay namespaces: proyecto por cliente, Management API) → DATABASE_URL |
| S3 medusa | auto | Railway GraphQL: proyecto + servicio desde la URL del repo Medusa + env + dominio |
| S4 medusa-seed | auto | Admin API idempotente: región/canal/secret key → imprime los `ssm put` |
| S5 temporal | guiado | namespace + service account + API key (tcld o comandos impresos) |
| S6 aws-bootstrap | guiado | imprime `cd <clon> && aws_bootstrap.py state/github` + keygen |
| S7 platform | guiado | imprime terraform apply + carga de secretos del clon |
| S8 compute | guiado | imprime compute apply + push + schedules (checklist en NEXT_STEPS.md) |

**Regla de la casa (crítica)**: los steps AUTO solo hablan con APIs de
terceros (Supabase/Railway/Medusa/Temporal) donde hubara ni existe; TODO lo
que toca AWS es GUIADO — el runner **jamás ejecuta un comando AWS**, los
imprime apuntando al clon. Guards duros además: slug/prefijo/ssm de hubara
rechazados en `render_vars`, y el clon no puede vivir dentro del repo madre.
Contrato testeado en `forge/tests/test_guards.py` + `test_steps_y_migrate.py`.

### Orden de construcción (cada fase usable sola)

```
1. forge CLI (F1a)              1-2 días   ← sin esto la UI no tiene qué mostrar
2. Flota + wizard + logs        2-3 días   ← con 1+2 ya está el 70% del valor
3. medusa_provision.py          1 día
4. Panel Meta/WhatsApp          1 día
5. Editor de personalidades     1 día
                                ≈ 1.5-2 semanas efectivas
```

**GitOps sin K8s (complementos baratos, mismo espíritu)**: (a) el CI itera una matriz
sobre `clients/*.yaml` — el "ApplicationSet casero" (la matriz
`tenant: [hubara, vincenzo]` de frontend-deploy ya era esto a medio hacer); (b) un
workflow cron de **drift detection** que corre `terraform plan` por cliente y avisa
si hay diff. Si algún día la flota crece mucho: Atlantis (infra) + Komodo (app compose)
son el par "Argo sin K8s" — evaluar recién entonces.
