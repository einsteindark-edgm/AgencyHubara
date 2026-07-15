# {{repo_name}} — pasos post-forge ({{company}})

> Generado por forge desde el motor hubara (`{{engine_sha}}`). Este runbook
> reemplaza a los docs de infra del proyecto madre (describían la infra viva
> de Hubara y no aplican acá).
>
> **Orquestador**: estos pasos también existen como STEPS con estado — desde
> el repo madre: `python3 forge/migrate.py status {{slug}} --dest <esta carpeta>`.
> Los steps AUTO (Supabase / Railway-Medusa / seed / Temporal) se ejecutan
> solos contra APIs de terceros; los GUIADOS imprimen los comandos
> AWS/terraform exactos apuntando a ESTE clon — el orquestador jamás ejecuta
> comandos AWS, así que no puede tocar hubara.

## F2 — Bootstrap AWS (una vez, local con creds admin)

- [ ] `python3 infra/scripts/aws_bootstrap.py state` → bucket `{{prefix}}-tfstate` + tabla `{{prefix}}-tflock`
- [ ] `ssh-keygen -t ed25519 -C "{{slug}}-ops"` → pública a `infra/terraform/compute/tenants.auto.tfvars`, privada al secret `EC2_SSH_KEY`
- [ ] Primer `terraform apply` de `platform` LOCAL (el OIDC provider ya existe en la cuenta: `project.auto.tfvars` lo referencia con `create_github_oidc_provider = false`)
- [ ] `python3 infra/scripts/aws_bootstrap.py github --repo {{repo}}` → vars AWS_* + TF_STATE_* del repo
- [ ] Environment `production` en GitHub con required reviewers

## F3 — Platform + secretos

- [ ] Apply platform → S3/CloudFront/Cognito/SSM placeholders `{{ssm_prefix}}/{{slug}}/*`
- [ ] Usuario operador Cognito (`admin-create-user`)
- [ ] Cargar keys NUEVAS de este cliente: DEEPSEEK_API_KEY, GEMINI_API_KEY, GHCR_PULL_TOKEN (PAT fine-grained solo al package `{{image}}`), WHATSAPP_VERIFY_TOKEN real

## F4 — Medusa propio (Railway)

- [ ] Proyecto Railway nuevo (Medusa v2 + Postgres) · `medusa db:migrate` · admin + token
- [ ] Seed: región {{currency}}/{{country}}, sales channel, shipping options → anotar los IDs
- [ ] Catálogo de {{company}} · SSM: MEDUSA_BASE_URL + MEDUSA_ADMIN_TOKEN

## F5 — Meta / WhatsApp (long pole — arrancar YA)

- [ ] Completar `infra/whatsapp-provisioning/tenants/{{slug}}.env` (desde el .example)
- [ ] `python3 whatsapp_provision.py discover|plan|apply` → número, catálogo, templates, flow shipping (nuevo flow_id), dataset CAPI
- [ ] `python3 whatsapp_provision.py ads-token` → seed `{{ssm_prefix}}/{{slug}}/meta/oauth` (sin esto el dashboard dice "Meta no conectado")
- [ ] SSM: WHATSAPP_*, META_CATALOG_ID, META_SYSTEM_USER_TOKEN, META_CAPI_DATASET_ID, META_FLOW_ID_SHIPPING, META_APP_*

## F5b — GraphAgents del cliente (viaja en el clon, identidad propia)

- [ ] La caja nace con el apply de compute (F7) — tag `Role=graphagents-{{slug}}`,
      pay-per-use/autostop igual que el patrón del motor
- [ ] Secretos: `python3 infra/scripts/aws_bootstrap.py secrets --tenant {{slug}}-graphagents --prefix "" --file secrets.graphagents.env`
      → SSM `/{{slug}}-graphagents/*` (AGENTSPAN_MASTER_KEY y POSTGRES_PASSWORD nuevos,
      META_ACCESS_TOKEN + META_AD_ACCOUNT_ID del BM de {{company}}, GHCR_PULL_TOKEN
      del package `{{slug}}-graphagents`, GRAPHAGENTS_LLM_API_KEY)
- [ ] Primer deploy: workflow `graphagents-deploy` del repo (imagen `{{slug}}-graphagents`)
- [ ] Smoke: `python3 infra/scripts/graphagents_ctl.py status` (filtra por el tag propio)

## F6 — Temporal Cloud

- [ ] Namespace `{{slug}}` + service account + API key propia
- [ ] SSM: TEMPORAL_ADDRESS / TEMPORAL_NAMESPACE (`{{slug}}.<acct>`) / TEMPORAL_API_KEY
      (fuente de verdad de los nombres exactos: los SSM vivos del proyecto madre)

## F7 — Compute + primer deploy

- [ ] Apply compute → caja + EIP → poner `domain = "<ip-con-guiones>.sslip.io"` en tenants.auto.tfvars + api_url/callbacks en platform → re-apply
- [ ] Push a main → backend-deploy + frontend-deploy
- [ ] Registrar webhook en la App Meta (CALLBACK_URL + verify token) y suscribir el WABA
- [ ] Seed del snapshot de catálogo (sin esto, el primer "Ver catálogo" escala a humano)
- [ ] Schedules que NO se auto-crean (contra el namespace nuevo):
      `cd hubara_agency && uv run python scripts/create_reengagement_schedule.py`
      y `cd hubara_agency && uv run python scripts/create_order_sentinel_schedule.py`

## F8 — Verificación E2E

- [ ] HTTPS ok · webhook verificado · conversación real (saluda como {{company}}, NUNCA "Hubara")
- [ ] Draft order en el Medusa NUEVO · dashboard con Cognito · CAPI acepta LeadSubmitted
- [ ] 5 schedules vivos en Temporal UI · 8 workers polleando · primer snapshot DLM + restore test
- [ ] `python3 forge/forge.py verify .` (del repo madre) → sin residuales
