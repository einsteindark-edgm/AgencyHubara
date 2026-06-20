# Runbook de despliegue — AgencyHubara (paso a paso para humano)

Guía directa para llevar la infra a AWS real. Cada paso dice **qué corrés**, **qué
hace** y **cómo verificás**. El detalle de diseño está en
[`INFRASTRUCTURE.md`](INFRASTRUCTURE.md); acá está el "qué tecleo, en qué orden".

> **Modelo mental.** Hay 3 planos:
> 1. **Tu app** (FastAPI + 6 workers + LiteLLM) → corre en **docker-compose** sobre una caja **EC2** por tenant. Igual que tu compose local de hoy, pero en AWS.
> 2. **El plano de control de AWS** (dónde viven buckets, secretos, pools, roles) → lo gestiona **Terraform**.
> 3. **Temporal** → managed en **Temporal Cloud** (no lo hosteás).
>
> **robotocore** es una réplica LOCAL del plano #2 (la API de AWS) para probar el Terraform y los scripts sin gastar ni romper nada. NO es compute (ver FASE 0).

---

## Prerrequisitos (una vez)

| Necesitás | Para qué | Verificás |
|---|---|---|
| Cuenta **AWS** + `aws configure` con creds admin (bootstrap) | crear state, primer apply | `aws sts get-caller-identity` |
| **`gh`** logueado (`gh auth login`) | setear variables/secrets del repo | `gh auth status` |
| **`terraform`** ≥ 1.6 | plan/apply | `terraform version` |
| **`docker`** | robotocore + build de imagen | `docker ps` |
| Cuenta **Temporal Cloud** (+ trial credits) | namespaces + API key | ver §Pendientes |
| Cuenta **Meta/WhatsApp**, **Medusa**, **DeepSeek/Gemini** | los secretos del negocio | ya las tenés (están en tu `.env` local) |

Todos los comandos asumen que estás en `infra/` del repo.

---

## FASE 0 — Probar TODO local con robotocore (gratis, antes de AWS)

```bash
docker compose -f robotocore/docker-compose.robotocore.yml up -d   # AWS local en :4566
./robotocore/test-local.sh                                         # aplica el Terraform + asserts
```

**Qué hace:** levanta robotocore (emulador de AWS) y corre el MISMO Terraform que
irá a la nube, apuntado al `:4566`. Valida el grafo completo + crea los recursos en
el emulador + asserts. Si esto da 🎉 verde, el Terraform es sano.

También podés probar los scripts de bootstrap contra robotocore (no tocan AWS real):

```bash
cd scripts
python3 aws_bootstrap.py state   --endpoint-url http://localhost:4566
python3 aws_bootstrap.py secrets --tenant hubara --file secrets.example.env --endpoint-url http://localhost:4566
```

### ¿Puedo correr el PROYECTO desde robotocore, como levanto docker hoy?

**No exactamente — y es importante entender por qué.** robotocore emula la **API de
AWS** (S3, SSM, Cognito, EC2, IAM…), **no es una plataforma de compute**. Las
instancias EC2 que Terraform "crea" en robotocore son objetos de mentira: NO hay una
VM corriendo tu imagen Docker adentro. Tu app **no corre dentro de robotocore**.

Lo que robotocore te da en local:
- ✅ **Validar/aplicar todo el Terraform** sin AWS real (ya lo probaste arriba).
- ✅ **Probar los scripts** de bootstrap (`state`, `secrets`) contra el `:4566`.
- ✅ **Ensayar el camino de config**: seedear el SSM de robotocore y que tu app local
  lea de ahí (en vez de un `.env` a mano), igual que en prod.

Tu app sigue levantándose como hoy: `docker compose -f hubara_agency/docker-compose.local.yml up`
(eso trae FastAPI + workers + LiteLLM + Temporal local + SigNoz). robotocore
reemplaza el **plano de control de AWS**, no esos contenedores.

**Receta "app local leyendo config desde robotocore"** (opcional, para ensayar el flujo SSM→app):
```bash
# 1. Seed del SSM de robotocore con tus knobs/secretos:
cd infra/scripts && python3 aws_bootstrap.py secrets --tenant hubara \
  --file secrets.hubara.env --endpoint-url http://localhost:4566
# 2. Bajar ese SSM a un .env (apuntando el render a robotocore):
AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test \
AWS_ENDPOINT_URL=http://localhost:4566 \
  TENANT=hubara AWS_REGION=us-east-1 ... ./render-env-from-ssm.sh   # ver nota abajo
# 3. Levantás tu compose local de siempre con ese .env.
```
> El `render-env-from-ssm.sh` está pensado para correr EN la caja EC2 (lee
> `/opt/hubara/box.env` + usa el instance profile). Para correrlo local contra
> robotocore tenés que exportar a mano `AWS_ENDPOINT_URL`, `AWS_REGION`, `TENANT`,
> `HUBARA_IMAGE`. Es un ensayo del mecanismo, no el modo de trabajo diario — para
> dev del día a día seguí usando tu `.env`/compose local normal.

Apagar robotocore: `docker compose -f robotocore/docker-compose.robotocore.yml down`.

---

## FASE 1 — Bootstrap-once (lo hacés UNA vez en tu compu)

### 1.1 — State store (bucket S3 + lock DynamoDB)

```bash
cd scripts
python3 aws_bootstrap.py state --bucket agencyhubara-tfstate --table agencyhubara-tflock
```
**Qué hace:** crea el bucket S3 (versionado + cifrado + sin acceso público) donde
Terraform guarda su *state*, y la tabla DynamoDB que hace el *lock* (evita que dos
applies corran a la vez). Es huevo-gallina: el state de Terraform no puede vivir en
Terraform, por eso se crea a mano (idempotente: si ya existen, los saltea).
**Verificás:** `aws s3 ls | grep tfstate` y `aws dynamodb list-tables`.

### 1.2 — Key pair de EC2 (para que el deploy entre por SSH)

```bash
ssh-keygen -t ed25519 -f ~/.ssh/hubara_ops -N "" -C "hubara-ops"
```
**Qué hace:** genera un par de claves. La **pública** la pone Terraform en las cajas
EC2; la **privada** la usa GitHub Actions para SSHear al deployar. Poné la pública en
`terraform/compute/tenants.auto.tfvars`:
```hcl
ssh_public_key = "ssh-ed25519 AAAA... hubara-ops"   # contenido de ~/.ssh/hubara_ops.pub
```
**Verificás:** `cat ~/.ssh/hubara_ops.pub`.

### 1.3 — Primer apply de `platform` (local, con tus creds)

```bash
cd ../terraform/platform
cp envs/real.s3.tfbackend.example envs/real.s3.tfbackend     # editá si cambiaste nombres
terraform init -backend-config=envs/real.s3.tfbackend
terraform apply        # revisá el plan y confirmá
```
**Qué hace:** crea S3+CloudFront, Cognito, los parámetros SSM (con placeholders), y
**los roles OIDC** que después usarán los workflows. **Por qué local y no por
GitHub:** los workflows necesitan un rol OIDC para autenticarse… que todavía no
existe. Este primer apply lo crea con TUS credenciales. De acá en más, GitHub se
autentica solo (sin llaves estáticas).
> Si tu cuenta YA tiene el OIDC provider de GitHub: `terraform import 'module.github_oidc.aws_iam_openid_connect_provider.github' <arn>` antes del apply (sino falla con `EntityAlreadyExists`).

**Verificás:** `terraform output github_terraform_role_arn` devuelve un ARN.

### 1.4 — GH variables + secret SSH (para que los workflows funcionen)

```bash
cd ../../scripts
python3 aws_bootstrap.py github --repo einsteindark-edgm/AgencyHubara \
  --platform-dir ../terraform/platform --ssh-key-file ~/.ssh/hubara_ops
```
**Qué hace:** lee los outputs de Terraform (los ARNs de los roles) y setea las
**GitHub variables** (`AWS_REGION`, `AWS_TERRAFORM_ROLE_ARN`, `AWS_DEPLOY_ROLE_ARN`,
`TF_STATE_BUCKET`, `TF_STATE_LOCK_TABLE`) + el **secret** `EC2_SSH_KEY` (la privada).
Sin esto los workflows no saben qué rol asumir ni cómo SSHear.
**Verificás:** `gh variable list --repo einsteindark-edgm/AgencyHubara`.

### 1.5 — Environment `production` en GitHub (gate del apply)

En GitHub → **Settings → Environments → New environment → `production`** → activá
**Required reviewers** (vos mismo). **Qué hace:** el workflow `terraform-apply` queda
**pausado pidiendo aprobación** antes de tocar AWS. Una red de seguridad contra un
apply accidental. **Verificás:** el environment aparece en Settings.

---

## FASE 2 — Cargar los secretos reales en SSM

Terraform creó las CLAVES con placeholders; ahora poné los VALORES reales (NO van en
git ni en el state):

```bash
cd scripts
cp secrets.example.env secrets.hubara.env       # editá con los valores REALES
python3 aws_bootstrap.py secrets --tenant hubara --file secrets.hubara.env
# repetí por tenant (secrets.vincenzo.env, --tenant vincenzo)
```
**Qué hace:** sube cada `KEY=VALUE` a SSM como `SecureString` en
`/hubara/<tenant>/<KEY>`. La caja EC2 los lee con su instance profile al deployar.
`TEMPORAL_*` y `GHCR_PULL_TOKEN` salen de §Pendientes. **Verificás:**
`aws ssm get-parameters-by-path --path /hubara/hubara --with-decryption --query 'Parameters[].Name'`.

> Los **knobs de scheduler** (crons/intervalos) NO los cargás acá: Terraform ya los
> creó con defaults en `/hubara/<tenant>/scheduler/`. Cambiarlos = `put-parameter
> --overwrite` (ver §Operar).

---

## FASE 3 — Aplicar la infra desde GitHub

Push a `main` (o **Actions → Terraform plan → Run**) corre el **plan**. Para aplicar:
**Actions → Terraform apply → Run workflow → `both`** → aprobás el environment.

**Qué hace cada paso del workflow `terraform-apply`** (esto es lo que "corre el Terraform en GitHub"):
1. **checkout** — baja el repo al runner.
2. **configure-aws-credentials (OIDC)** — el runner presenta un token de identidad de
   GitHub; AWS lo valida contra el OIDC provider y le da **credenciales temporales**
   del `terraform_role`. Cero llaves estáticas guardadas.
3. **setup-terraform** — instala Terraform en el runner.
4. **`terraform init -backend-config=...`** — conecta al state en S3 (bucket+tabla de
   la FASE 1) y baja el provider AWS.
5. **`terraform apply`** — compara el state con lo declarado y crea/actualiza S3,
   CloudFront, Cognito, SSM, roles (root `platform`) y EC2, SGs, EIPs, instance
   profiles (root `compute`). Aplica `platform` primero, después `compute`.

**Verificás:** el run en verde + `terraform output` muestra los hosts/buckets.

---

## FASE 4 — Deploys de código (automáticos en push a `main`)

| Workflow | Cuándo | Qué hace (paso a paso) |
|---|---|---|
| **frontend-deploy** | push a `frontend_dashboard/**` | (1) asume rol TF, lee `build_config` del state (bucket, dist id, api_url). (2) `npm ci && npm run build` con `VITE_API_URL` del tenant. (3) asume rol deploy, `aws s3 sync dist → bucket`, `cloudfront create-invalidation`. |
| **backend-deploy** | push a `hubara_agency/**` etc. | (1) build de la imagen (Dockerfile raíz) y **push a GHCR**. (2) por tenant: lee la IP del host del state, **SSHea**, copia el `docker-compose.prod.yml`, **rinde el `.env` desde SSM**, `docker login ghcr.io`, `docker compose pull && up -d`. |
| **observability-deploy** | push a `deploy/signoz/**` | SSHea a la caja SigNoz, copia el stack vendorizado, `docker compose up -d`. |

Todos usan OIDC (rol angosto de deploy) + el secret `EC2_SSH_KEY`. **Verificás:** el
run en verde; `curl https://api.<tenant>...` responde; los traces aparecen en SigNoz.

---

## FASE 5 — GraphAgents (subsistema separado, on-demand / pay-per-use)

Agentes de análisis de **Meta Ads** (LangGraph + AgentSpan durable). Corre en **su
propia caja EC2 compartida**, **apagada por default** porque se usa muy de vez en
cuando. Ver `INFRASTRUCTURE.md §3.11`.

**5.1 — Secretos en SSM `/graphagents/`** (las claves las creó Terraform con placeholder):
```bash
cd scripts
cp secrets.example.env graphagents.secrets.env   # poné: AGENTSPAN_MASTER_KEY (openssl rand -base64 32),
                                                  # POSTGRES_PASSWORD, OPENAI/ANTHROPIC, META_*, GHCR_PULL_TOKEN
python3 aws_bootstrap.py secrets --tenant graphagents --prefix "" --file graphagents.secrets.env
#   ↑ --prefix "" hace que caigan en /graphagents/<KEY> (no /hubara/...)
```

**5.2 — Deploy (una vez, y cada cambio de `GraphAgents/`):** push a `main` que toque
`GraphAgents/**`, o **Actions → GraphAgents deploy → Run**. El workflow buildea la
imagen de la app → GHCR, **prende la caja** (estaba apagada), espera, y levanta
`postgres + agentspan + app`. Como los contenedores tienen `restart: unless-stopped`,
de ahí en más **prender la caja = el stack vuelve solo** (sin re-deploy).

**5.3 — Correr un análisis (el día a día):**
```bash
cd infra/scripts
python3 graphagents_ctl.py start     # prende la caja, espera, imprime las URLs
#   → Explorer http://<ip>:8900  ·  AgentSpan http://<ip>:6767  (solo desde tu IP)
#   ... corrés tus agentes de ads ...
python3 graphagents_ctl.py stop      # apagás (o no hacés nada: se auto-apaga ~20 min idle)
python3 graphagents_ctl.py status    # estado + IP actual
```
**Costo:** apagada = solo **~$3/mo** de disco EBS (no se pierde el estado durable).
Prendida = ~$0.08/h (centavos por análisis). **No podés olvidarla prendida** — la
caja se auto-apaga sola al quedar idle (cron + permiso de apagarse a sí misma).

> Si en algún momento la querés **always-on** (ej. cuando llegue el puente fase-B con
> el monorepo), seteá `autostop_idle_minutes = 0` y `use_eip = true` en el módulo.

---

## Operar (día a día, sin redeploy)

**Cambiar un timing de scheduler** (PR #69):
```bash
aws ssm put-parameter --overwrite --type String \
  --name /hubara/hubara/scheduler/ORDER_RECONCILE_INTERVAL_MINUTES --value "10"
ssh -i ~/.ssh/hubara_ops ec2-user@<ip> \
  'cd /opt/hubara && ./render-env-from-ssm.sh && docker compose up -d --no-deps --force-recreate worker-orders-reconcile'
```
**Rotar un secreto:** `aws_bootstrap.py secrets ...` (o un `put-parameter`) + re-render + recreate del worker/api afectado.

---

## Los pendientes — setup OPERATIVO (el código va en [`PENDING_IMPLEMENTATION.md`](PENDING_IMPLEMENTATION.md))

### Temporal Cloud (API key + namespaces)
1. Creá la cuenta en cloud.temporal.io y reclamá los **$1.000 de trial** (vía AWS Marketplace, sin tarjeta).
2. Creá los namespaces `hubara` y `vincenzo` (consola, o `infra/terraform/temporal-cloud/`). Anotá el `namespace.account_id` de cada uno.
3. Generá una **API key**: consola → Settings → API Keys → Create. Copiala (se muestra una vez).
4. Cargá en SSM por tenant: `TEMPORAL_ADDRESS` (ej. `us-east-1.aws.api.temporal.io:7233`), `TEMPORAL_NAMESPACE` (`hubara.<account_id>`), `TEMPORAL_API_KEY`.
5. **Código:** falta que `get_temporal_client()` use API key (hoy usa mTLS) → ver `PENDING_IMPLEMENTATION.md §1`.

### JWT / login del dashboard (Cognito)
1. Creá un usuario operador: `aws cognito-idp admin-create-user --user-pool-id <pool> --username vos@empresa.com` (el pool ya existe; el id está en `terraform output auth`).
2. **Código:** falta wirear el login en el front + validar el JWT en FastAPI → `PENDING_IMPLEMENTATION.md §2`. Hoy las rutas NO exigen auth.

### Dominios propios + HTTPS
1. **API (Caddy):** apuntá un registro DNS `A` de `api.<tenant>...` a la **EIP** de la caja (`terraform output app_hosts`). Caddy saca el cert solo al primer hit.
2. **Dashboard (CloudFront):** validá un cert **ACM en us-east-1** para `dashboard.<tenant>...`, poné su ARN + el alias en `terraform/platform/tenants.auto.tfvars`, `terraform apply`, y apuntá un `CNAME` al dominio de CloudFront (`terraform output frontend`).
3. **Código/config:** detalle en `PENDING_IMPLEMENTATION.md §3`.

---

## Troubleshooting

- **`terraform init` pide backend:** te faltó `-backend-config=envs/real.s3.tfbackend` (o el bucket no existe → FASE 1.1).
- **Workflow falla en configure-aws-credentials:** la GH variable del role ARN está mal o el environment no aprobó. Revisá FASE 1.4/1.5.
- **`EntityAlreadyExists` en el OIDC provider:** ya existía → importalo (FASE 1.3).
- **La caja no baja la imagen de GHCR:** `GHCR_PULL_TOKEN` en SSM vencido/sin `read:packages`.
- **CloudFront sirve viejo:** la invalidación tarda ~1-2 min; o forzá `create-invalidation --paths "/*"`.
