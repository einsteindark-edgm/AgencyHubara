# Infra as Code — AgencyHubara

Terraform + GitHub Actions para desplegar la infra de [`../INFRASTRUCTURE.md`](../INFRASTRUCTURE.md)
en AWS real, con un harness que prueba el MISMO código contra **robotocore** (réplica
local de AWS) antes de tocar la nube.

## Mapa

```
infra/
├── terraform/
│   ├── platform/      # AWS managed: S3+CloudFront, Cognito, SSM, IAM/OIDC   (robotocore-testable)
│   ├── compute/       # EC2: caja de app por tenant + caja SigNoz            (robotocore-testable)
│   └── temporal-cloud/# OPCIONAL: namespaces de Temporal Cloud               (real-only)
├── compose/
│   ├── docker-compose.prod.yml   # lo que corre en cada caja EC2 (imagen GHCR, Temporal Cloud, sin SigNoz)
│   └── render-env-from-ssm.sh    # arma el .env desde SSM (corre en la caja)
└── robotocore/
    ├── docker-compose.robotocore.yml  # el emulador AWS en :4566
    ├── local.*.tfvars                 # apuntan el provider a robotocore
    └── test-local.sh                  # corre TODO el TF contra robotocore + asserts

.github/workflows/
├── local-aws-test.yml     # PR → valida TF contra robotocore (SIN creds AWS)
├── terraform-plan.yml     # push main → plan real (OIDC)
├── terraform-apply.yml    # manual + environment:production → apply real
├── frontend-deploy.yml    # build Vite → S3 + invalidación CloudFront
├── backend-deploy.yml     # build imagen → GHCR → EC2 (SSH + compose)
└── observability-deploy.yml # SigNoz → caja de obs
```

**Dos roots, dos states** (menor blast radius): `platform` cambia poco; `compute`
se recrea sin tocar buckets/pools. Ambos usan el MISMO provider AWS con un switch
(`aws_endpoint`) que los apunta a robotocore o a AWS real sin cambiar una línea.

## Probar local (robotocore) — hacelo SIEMPRE antes de la nube

```bash
cd infra/robotocore
docker compose -f docker-compose.robotocore.yml up -d   # AWS local en :4566
./test-local.sh                                         # plan+apply+asserts de ambos roots
# o todo de una:  ./test-local.sh --up
```

Qué valida: el **grafo completo** (plan de platform+compute — caza errores de
wiring/tipos/refs en TODOS los recursos) + **asserts** sobre los servicios de alta
fidelidad en el emulador (S3, Cognito, SSM, IAM, EC2). CloudFront/ACM son
*best-effort* en robotocore (Moto): el `plan` los valida, pero su comportamiento de
edge/validación-DNS solo se ve en real. El cloud-init de EC2 tampoco ejecuta en
local (no hay VM). Es exactamente el mismo gate que corre en cada PR
(`local-aws-test.yml`) — sin credenciales AWS, así un fork no puede tocar la nube.

## Bootstrap (una vez)

> **Atajo:** todo este bootstrap está automatizado en
> [`../scripts/aws_bootstrap.py`](../scripts/aws_bootstrap.py) y narrado paso a paso
> (con qué hace cada uno) en [`../DEPLOY_RUNBOOK.md`](../DEPLOY_RUNBOOK.md). Abajo
> quedan los comandos manuales equivalentes por si preferís hacerlo a mano.

1. **State store** (huevo-gallina: el backend de TF no puede vivir en TF) —
   equivale a `python3 ../scripts/aws_bootstrap.py state`:
   ```bash
   aws s3api create-bucket --bucket agencyhubara-tfstate --region us-east-1
   aws s3api put-bucket-versioning --bucket agencyhubara-tfstate \
     --versioning-configuration Status=Enabled
   aws dynamodb create-table --table-name agencyhubara-tflock \
     --attribute-definitions AttributeName=LockID,AttributeType=S \
     --key-schema AttributeName=LockID,KeyType=HASH --billing-mode PAY_PER_REQUEST
   ```
2. **Key pair EC2:** generá uno (`ssh-keygen -t ed25519 -f hubara_ops`), poné la
   pública en `compute/tenants.auto.tfvars` (`ssh_public_key`) y la privada en el
   GH secret `EC2_SSH_KEY`.
3. **Primer apply de platform** (crea el OIDC provider + roles). Como todavía no
   hay rol OIDC, corré este apply con tus credenciales locales:
   ```bash
   cd infra/terraform/platform
   terraform init -backend-config=envs/real.s3.tfbackend   # copiá el .example primero
   terraform apply
   terraform output github_terraform_role_arn   # ← copiá a la GH variable
   terraform output github_deploy_role_arn       # ← copiá a la GH variable
   ```
   > GOTCHA: si tu cuenta YA tiene el OIDC provider de GitHub, importalo
   > (`terraform import 'module.github_oidc.aws_iam_openid_connect_provider.github' <arn>`)
   > o el apply falla con `EntityAlreadyExists`.
4. **GH variables** (Settings → Secrets and variables → Actions → Variables):
   | Variable | Valor |
   |---|---|
   | `AWS_REGION` | `us-east-1` |
   | `AWS_TERRAFORM_ROLE_ARN` | output `github_terraform_role_arn` |
   | `AWS_DEPLOY_ROLE_ARN` | output `github_deploy_role_arn` |
   | `TF_STATE_BUCKET` | `agencyhubara-tfstate` |
   | `TF_STATE_LOCK_TABLE` | `agencyhubara-tflock` |
   GH secret: `EC2_SSH_KEY` (privada del paso 2).
5. **Environment `production`** (Settings → Environments): creá `production` con
   required reviewers — gatea `terraform-apply.yml`.

## Deploy real (orden)

1. `terraform-plan` corre solo en cada push a main; revisalo.
2. **Setear los valores de los secretos SSM** (TF crea las CLAVES con placeholder):
   ```bash
   aws ssm put-parameter --overwrite --type SecureString \
     --name /hubara/hubara/DEEPSEEK_API_KEY --value 'sk-...'
   # …repetí para cada clave de variables.tf:secret_keys × cada tenant
   ```
   Incluí `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, `TEMPORAL_API_KEY` (Temporal Cloud)
   y `GHCR_PULL_TOKEN` (PAT con `read:packages` para que la caja baje la imagen).
3. **Apply** vía `terraform-apply.yml` (Actions → Run workflow → `both`).
4. **Deploys de código** (automáticos en push a main, o Run workflow):
   `frontend-deploy` · `backend-deploy` · `observability-deploy`.

## Cambiar un timing de scheduler SIN redeploy (PR #69)

Los knobs de timing (orders-reconcile, sales-eval, golden-eval, watchdog) viven
como **SSM String** en `/hubara/<tenant>/scheduler/<VAR>` — los crea
`module.scheduler_config` con los defaults del código (`platform/variables.tf:scheduler_config`).
Cambiarlos NO requiere rebuild ni redeploy del proyecto:

```bash
# 1. Cambiar el valor en SSM (un solo parámetro):
aws ssm put-parameter --overwrite --type String \
  --name /hubara/hubara/scheduler/ORDER_RECONCILE_INTERVAL_MINUTES --value "10"

# 2. En la caja del tenant: re-render del .env (reusa la imagen actual) + recreate
#    SOLO del worker afectado:
ssh ec2-user@<ip-del-tenant>
cd /opt/hubara && ./render-env-from-ssm.sh            # baja el nuevo valor de SSM
docker compose up -d --no-deps --force-recreate worker-orders-reconcile
```

Al rebootear, el worker **converge** el Temporal Schedule al valor nuevo. Qué worker
recrear según la variable (tabla completa en `hubara_agency/docs/scheduler-config-aws.md`):
`ORDER_RECONCILE_*` → `worker-orders-reconcile` · `SALES_EVAL_*`/`GOLDEN_EVAL_*` →
`worker-chats-sales_eval` · `WATCHDOG_*` → `worker-chats-remarketing`.

> El `ignore_changes` del módulo hace que un `terraform apply` posterior NO revierta
> el override. Terraform define el **default**; SSM es la **fuente viva**.

## Pendientes que tocan este IaC (ver INFRASTRUCTURE.md §7)

- **Temporal API key (Task #1):** la prod compose ya pasa `TEMPORAL_ADDRESS/NAMESPACE/API_KEY`,
  pero el código todavía lee `TEMPORAL_URL` + cert paths. Hasta que aterrice la
  migración, esos 3 env de SSM no tienen efecto.
- **JWT en FastAPI (Task #2):** Cognito ya está creado (ids en el output `build_config`),
  pero el frontend aún no consume `VITE_COGNITO_*` ni la API valida el JWT.
- **Dominios/ACM:** por defecto CloudFront sirve por `*.cloudfront.net` y Caddy saca
  TLS del dominio del tenant. Para dominio propio en CloudFront: validá un cert ACM
  en **us-east-1** y poné su ARN + alias en `platform/tenants.auto.tfvars`.
